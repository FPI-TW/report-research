"""維運代理的寫入類操作（restart、run）：硬性限制、execution group 互斥、鎖檔試探、先回應後執行。

**不得真的呼叫 systemctl**：一律 `FakeRunner`（記下 argv、回罐頭的 `systemctl show` 輸出）。鎖檔在
`tempfile` 的臨時目錄，由測試自己 flock 住或寫 PID，不碰 repo 的 `data/`。
"""

from __future__ import annotations

import asyncio
import fcntl
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from test_ops_agent import FakeRunner, _raw, _req, _uid

from ops_agent import actions, protocol
from ops_agent.catalog import AgentConfig, Catalog, CatalogError, Service, load_catalog, parse_catalog
from ops_agent.runner import RunResult
from ops_agent.server import Agent, serve

REPO_ROOT = Path(__file__).resolve().parents[1]
PROD_TOML = REPO_ROOT / "deploy" / "ops" / "services.prod.toml"
DEV_TOML = REPO_ROOT / "deploy" / "ops" / "services.dev.toml"
SYSTEMCTL = "/usr/bin/systemctl"


def _svc(name, unit=None, *, kind="systemd", actions_=("status", "logs"), group=None, container=None, **kw):
    return Service(name=name, kind=kind, tier="important", actions=tuple(actions_), unit=unit,
                   container=container, group=group, **kw)


def _catalog(lock_dir: str, *, restart_delay=0.0, request_timeout=5.0, environment="production", extra=()):
    """直接組 Catalog 物件（繞過載入期驗證）：用來證明 catalog 被誤設時代理仍然硬擋。"""
    services = (
        _svc("web", "report-mark-web.service", actions_=("status", "logs", "restart"), group="web"),
        _svc("sync", "report-mark-sync.service", actions_=("status", "logs", "run"), group="llm-batch",
             flock_files=(os.path.join(lock_dir, ".claude_cli.lock"),),
             pid_files=(os.path.join(lock_dir, ".sync_new_reports.lock"),)),
        # 同 group 的唯讀成員：它在跑時 sync 也不能 run
        _svc("backfill", "report-mark-backfill.service", group="llm-batch"),
        _svc("backup", "report-mark-backup.service", actions_=("status", "run"), group="backup"),
        _svc("audit", "report-mark-audit.service", actions_=("status",)),
        # 以下是「catalog 被誤設」：載入期會拒絕，這裡直接塞進物件
        _svc("postgres", kind="container", container="report-mark-postgres",
             actions_=("status", "restart", "run"), group="db"),
        _svc("nginx", kind="container", container="deploy-nginx-1", actions_=("status", "restart"), group="edge"),
        _svc("cloudflared", kind="container", container="deploy-cloudflared-1", actions_=("status", "restart"),
             group="edge"),
        _svc("pg-unit", "report-mark-postgres.service", actions_=("status", "restart", "run"), group="db"),
        _svc("pg16", "pg16.service", actions_=("status", "run"), group="db"),
        _svc("sync-restart", "report-mark-freshness.service", actions_=("status", "restart"), group="fresh"),
        _svc("web-run", "report-mark-dev-web.service", actions_=("status", "run"), group="web"),
        _svc("nogroup", "report-mark-r2-reconcile.service", actions_=("status", "run")),
        *extra,
    )
    return Catalog(environment=environment, socket_path=protocol.CANONICAL_SOCKETS[environment],
                   agent=AgentConfig(allowed_uids=frozenset({os.getuid()}), request_timeout=request_timeout,
                                     command_timeout=2.0, restart_delay=restart_delay),
                   services=services)


def _block(unit, active="inactive", *, load="loaded", job="", invocation="0123abcd"):
    return (f"Id={unit}\nLoadState={load}\nActiveState={active}\nSubState=dead\nJob={job}\n"
            f"InvocationID={invocation}\nActiveEnterTimestamp=@1791191596\nExecMainStartTimestamp=@1791191596\n")


class ShowRunner(FakeRunner):
    """`systemctl show` 依 `states` 回每個 unit 的狀態；其他 systemctl 指令照 `start_result` 回。"""

    def __init__(self, states=None, *, start_result=None, show_result=None):
        self.states = states or {}
        self.start_result = start_result or RunResult(0, b"", b"")
        self.show_result = show_result
        super().__init__(self._handle)

    def _handle(self, argv):
        if argv[1] == "show":
            if self.show_result is not None:
                return self.show_result
            units = argv[argv.index("--") + 1:]
            blocks = []
            for unit in units:
                spec = self.states.get(unit, {})
                blocks.append(_block(unit, **spec))
            return RunResult(0, "\n".join(blocks).encode(), b"")
        return self.start_result

    def commands(self):
        return [c["argv"] for c in self.calls if c["argv"][1] != "show"]


class ActionsTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="rmops-locks-")
        self.lock_dir = self._tmp.name
        self.flock_path = os.path.join(self.lock_dir, ".claude_cli.lock")
        self.pid_path = os.path.join(self.lock_dir, ".sync_new_reports.lock")

    async def asyncTearDown(self):
        self._tmp.cleanup()

    def agent(self, runner, **kw):
        return Agent(_catalog(self.lock_dir, **kw), runner)


# ── 硬性限制：catalog 被誤設也放不開 ─────────────────────────────────────


class HardLimitTests(ActionsTestCase):
    async def test_postgres_nginx_cloudflared_restart_always_refused(self):
        runner = ShowRunner()
        agent = self.agent(runner)
        for name in ("postgres", "nginx", "cloudflared", "pg-unit"):
            with self.subTest(name=name):
                resp = await agent.handle_line(_req("restart", name))
                self.assertEqual(resp["error"]["code"], "action_not_allowed", resp)
        self.assertEqual(runner.calls, [], "被硬擋的請求連 systemctl show 都不該跑")

    async def test_forbidden_targets_cannot_run_either(self):
        runner = ShowRunner()
        agent = self.agent(runner)
        for name in ("postgres", "pg-unit", "pg16"):
            with self.subTest(name=name):
                self.assertEqual((await agent.handle_line(_req("run", name)))["error"]["code"], "action_not_allowed")
        self.assertEqual(runner.calls, [])

    async def test_restart_only_for_web_and_run_never_for_web(self):
        runner = ShowRunner()
        agent = self.agent(runner)
        self.assertEqual((await agent.handle_line(_req("restart", "sync-restart")))["error"]["code"],
                         "action_not_allowed")
        self.assertEqual((await agent.handle_line(_req("run", "web-run")))["error"]["code"], "action_not_allowed")
        self.assertEqual((await agent.handle_line(_req("run", "nogroup")))["error"]["code"], "action_not_allowed")
        self.assertEqual(runner.calls, [])

    async def test_actions_not_in_catalog_are_refused(self):
        runner = ShowRunner()
        agent = self.agent(runner)
        cases = [("run", "audit"), ("restart", "audit"), ("run", "web"), ("restart", "sync"), ("restart", "backfill"),
                 ("run", "backfill")]
        for op, name in cases:
            with self.subTest(op=op, name=name):
                self.assertEqual((await agent.handle_line(_req(op, name)))["error"]["code"], "action_not_allowed")
        self.assertEqual((await agent.handle_line(_req("run", "nope")))["error"]["code"], "unknown_service")
        self.assertEqual((await agent.handle_line(_req("run", "report-mark-sync.service")))["error"]["code"],
                         "unknown_service")
        self.assertEqual(runner.calls, [])

    async def test_parameters_are_refused(self):
        runner = ShowRunner()
        agent = self.agent(runner)
        for op, name in (("run", "sync"), ("restart", "web")):
            for params in ({"unit": "sshd.service"}, {"args": ["--all"]}, {"since": "1h"}, {"lines": 5}):
                with self.subTest(op=op, params=params):
                    resp = await agent.handle_line(_req(op, name, **params))
                    self.assertEqual(resp["error"]["code"], "invalid_params")
        self.assertEqual(runner.calls, [])

    async def test_environment_mismatch_for_write_ops(self):
        runner = ShowRunner()
        agent = self.agent(runner)
        for op, name in (("run", "sync"), ("restart", "web")):
            resp = await agent.handle_line(_req(op, name, env="development"))
            self.assertEqual(resp["error"]["code"], "environment_mismatch")
        self.assertEqual(runner.calls, [])

    async def test_actor_is_validated(self):
        agent = self.agent(ShowRunner())
        line = json.dumps({"v": 1, "env": "production", "op": "run", "service": "sync", "actor": "a b; rm"}) + "\n"
        self.assertEqual((await agent.handle_line(line.encode()))["error"]["code"], "bad_request")


# ── run ──────────────────────────────────────────────────────────────────


class RunTests(ActionsTestCase):
    async def test_run_starts_the_catalog_unit_with_fixed_argv(self):
        runner = ShowRunner({"report-mark-sync.service": {"invocation": "aaaa"}})
        resp = await self.agent(runner).handle_line(_req("run", "sync"))
        self.assertTrue(resp["ok"], resp)
        show = runner.calls[0]["argv"]
        self.assertEqual(show[:5], [SYSTEMCTL, "show", "--no-pager", "--timestamp=unix", "-p"])
        self.assertEqual(show[6:], ["--", "report-mark-sync.service", "report-mark-backfill.service"])
        self.assertEqual(runner.commands(),
                         [[SYSTEMCTL, "--no-ask-password", "start", "--no-block", "--", "report-mark-sync.service"]])
        result = resp["result"]
        self.assertEqual((result["action"], result["state"], result["name"], result["group"]),
                         ("run", "queued", "sync", "llm-batch"))
        self.assertEqual(result["previous_invocation_id"], "aaaa")
        self.assertEqual(result["previous_exec_main_start_at"], "2026-10-05T09:13:16Z")
        self.assertNotIn("flock_files", json.dumps(result))
        self.assertNotIn(self.lock_dir, json.dumps(result), "鎖檔路徑不回給 web")

    async def test_same_unit_running_is_409(self):
        for state in ("active", "activating", "reloading", "deactivating"):
            with self.subTest(state=state):
                runner = ShowRunner({"report-mark-sync.service": {"active": state}})
                resp = await self.agent(runner).handle_line(_req("run", "sync"))
                self.assertEqual(resp["error"]["code"], "already_running")
                self.assertEqual(runner.commands(), [])

    async def test_other_group_member_running_is_409(self):
        runner = ShowRunner({"report-mark-backfill.service": {"active": "activating"}})
        resp = await self.agent(runner).handle_line(_req("run", "sync"))
        self.assertEqual(resp["error"]["code"], "already_running")
        self.assertIn("report-mark-backfill.service", resp["error"]["message"])
        self.assertEqual(runner.commands(), [])

    async def test_pending_job_is_409(self):
        runner = ShowRunner({"report-mark-sync.service": {"job": "4711"}})
        resp = await self.agent(runner).handle_line(_req("run", "sync"))
        self.assertEqual(resp["error"]["code"], "already_running")
        self.assertIn("job", resp["error"]["message"])

    async def test_failed_unit_may_be_run_again(self):
        runner = ShowRunner({"report-mark-sync.service": {"active": "failed"}})
        self.assertTrue((await self.agent(runner).handle_line(_req("run", "sync")))["ok"])

    async def test_other_groups_do_not_conflict(self):
        runner = ShowRunner({"report-mark-sync.service": {"active": "activating"}})
        resp = await self.agent(runner).handle_line(_req("run", "backup"))
        self.assertTrue(resp["ok"], resp)
        self.assertEqual(runner.calls[0]["argv"][6:], ["--", "report-mark-backup.service"])

    async def test_show_failure_or_unknown_unit_refuses(self):
        for result, code in (
            (RunResult(1, b"", b"Failed to connect to bus"), "command_failed"),
            (RunResult(None, b"", b"", timed_out=True), "command_timeout"),
            (RunResult(0, b"Id=x\n", b""), "command_failed"),  # 段數對不上
        ):
            with self.subTest(code=code):
                runner = ShowRunner(show_result=result)
                self.assertEqual((await self.agent(runner).handle_line(_req("run", "sync")))["error"]["code"], code)
                self.assertEqual(runner.commands(), [])
        runner = ShowRunner({"report-mark-sync.service": {"load": "not-found"}})
        resp = await self.agent(runner).handle_line(_req("run", "sync"))
        self.assertEqual(resp["error"]["code"], "command_failed")
        self.assertEqual(runner.commands(), [])

    async def test_start_failure_is_reported(self):
        denied = b"Failed to start report-mark-sync.service: Access denied\n"
        runner = ShowRunner(start_result=RunResult(4, b"", denied))
        resp = await self.agent(runner).handle_line(_req("run", "sync"))
        self.assertEqual(resp["error"]["code"], "command_failed")
        self.assertIn("Access denied", resp["error"]["message"])

    async def test_group_is_released_after_each_request(self):
        runner = ShowRunner()
        agent = self.agent(runner)
        self.assertTrue((await agent.handle_line(_req("run", "sync")))["ok"])
        self.assertTrue((await agent.handle_line(_req("run", "sync")))["ok"])  # 主機狀態由 systemd 決定
        runner.start_result = RunResult(1, b"", b"boom")
        self.assertFalse((await agent.handle_line(_req("run", "sync")))["ok"])
        runner.start_result = RunResult(0, b"", b"")
        self.assertTrue((await agent.handle_line(_req("run", "sync")))["ok"])
        self.assertEqual(agent._busy_groups, set())

    async def test_concurrent_requests_in_same_group_conflict_in_agent(self):
        gate = asyncio.Event()
        runner = ShowRunner()
        plain = runner.handler

        async def slow(argv):
            if argv[1] == "show":
                await gate.wait()
            return plain(argv)

        runner.handler = slow
        agent = self.agent(runner)
        first = asyncio.create_task(agent.handle_line(_req("run", "sync")))
        await asyncio.sleep(0.01)
        second = await agent.handle_line(_req("run", "sync"))
        self.assertEqual(second["error"]["code"], "already_running")
        gate.set()
        self.assertTrue((await first)["ok"])
        self.assertEqual(len(runner.commands()), 1)

    async def test_request_timeout_releases_the_group(self):
        async def hang(argv):
            await asyncio.sleep(5)
            return RunResult(0, b"", b"")

        runner = ShowRunner()
        agent = self.agent(runner, request_timeout=0.05)
        plain = runner.handler
        runner.handler = hang
        self.assertEqual((await agent.handle_line(_req("run", "sync")))["error"]["code"], "timeout")
        runner.handler = plain
        self.assertTrue((await agent.handle_line(_req("run", "sync")))["ok"])


# ── 鎖檔試探 ─────────────────────────────────────────────────────────────


class LockTests(ActionsTestCase):
    def _hold_flock(self, payload: dict | None = None) -> int:
        fd = os.open(self.flock_path, os.O_RDWR | os.O_CREAT, 0o644)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if payload is not None:
            os.write(fd, json.dumps(payload).encode())
        self.addCleanup(os.close, fd)
        return fd

    async def test_llm_lock_held_is_409(self):
        self._hold_flock({"pid": os.getpid(), "script": "extract_takeaways", "started_at": "x"})
        runner = ShowRunner()
        resp = await self.agent(runner).handle_line(_req("run", "sync"))
        self.assertEqual(resp["error"]["code"], "already_running")
        self.assertIn("extract_takeaways", resp["error"]["message"])
        self.assertEqual(runner.commands(), [])

    async def test_released_lock_is_free_and_probe_does_not_keep_it(self):
        fd = self._hold_flock()
        fcntl.flock(fd, fcntl.LOCK_UN)
        runner = ShowRunner()
        self.assertTrue((await self.agent(runner).handle_line(_req("run", "sync")))["ok"])
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)  # 試探完立刻放掉：批次照樣拿得到

    async def test_lock_file_never_created_is_free(self):
        self.assertFalse(os.path.exists(self.flock_path))
        self.assertTrue((await self.agent(ShowRunner()).handle_line(_req("run", "sync")))["ok"])

    async def test_missing_lock_directory_is_lock_unavailable(self):
        """目錄不存在＝路徑設錯或 sandbox 沒綁進來：無法確認就不執行（不是當成「沒被持有」）。"""
        runner = ShowRunner()
        agent = Agent(_catalog(os.path.join(self.lock_dir, "missing")), runner)
        resp = await agent.handle_line(_req("run", "sync"))
        self.assertEqual(resp["error"]["code"], "lock_unavailable")
        self.assertEqual(runner.commands(), [])
        self.assertEqual(agent._busy_groups, set())

    @unittest.skipIf(os.geteuid() == 0, "root 讀得到 0000 的檔案")
    async def test_unreadable_lock_file_is_lock_unavailable(self):
        Path(self.flock_path).write_text("")
        os.chmod(self.flock_path, 0)
        resp = await self.agent(ShowRunner()).handle_line(_req("run", "sync"))
        self.assertEqual(resp["error"]["code"], "lock_unavailable")

    async def test_live_pid_file_is_409(self):
        Path(self.pid_path).write_text(f"{os.getpid()}\n")
        runner = ShowRunner()
        resp = await self.agent(runner).handle_line(_req("run", "sync"))
        self.assertEqual(resp["error"]["code"], "already_running")
        self.assertIn(str(os.getpid()), resp["error"]["message"])
        self.assertEqual(runner.commands(), [])

    async def test_stale_or_garbage_pid_file_is_free(self):
        proc = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"], capture_output=True, text=True,
                              check=True)
        for content in (proc.stdout, "", "not-a-pid", "0", "-5"):
            with self.subTest(content=content):
                Path(self.pid_path).write_text(content)
                self.assertTrue((await self.agent(ShowRunner()).handle_line(_req("run", "sync")))["ok"])

    async def test_locks_only_apply_to_their_group(self):
        self._hold_flock()
        self.assertTrue((await self.agent(ShowRunner()).handle_line(_req("run", "backup")))["ok"])
        self.assertTrue((await self.agent(ShowRunner()).handle_line(_req("restart", "web")))["ok"])

    def test_group_locks_are_shared_by_members(self):
        catalog = _catalog(self.lock_dir, extra=(
            _svc("tagger", "report-mark-tag.service", actions_=("status", "run"), group="llm-batch",
                 flock_files=("/x/other.lock",)),))
        flocks, pids = actions.group_locks(catalog, catalog.get("tagger"))
        self.assertEqual(flocks, ["/x/other.lock", self.flock_path])
        self.assertEqual(pids, [self.pid_path])


# ── restart：先回應、再執行 ───────────────────────────────────────────────


class RestartTests(ActionsTestCase):
    async def test_restart_is_accepted_before_it_runs(self):
        runner = ShowRunner({"report-mark-web.service": {"active": "active", "invocation": "old-id"}})
        agent = self.agent(runner, restart_delay=0.0)
        reply = await agent.handle(_req("restart", "web"))
        self.assertTrue(reply.response["ok"], reply.response)
        result = reply.response["result"]
        self.assertEqual((result["action"], result["state"], result["previous_invocation_id"]),
                         ("restart", "scheduled", "old-id"))
        self.assertEqual(runner.commands(), [], "回應產生時還沒執行 restart")
        self.assertIsNotNone(reply.after)
        await reply.after()
        self.assertEqual(runner.commands(),
                         [[SYSTEMCTL, "--no-ask-password", "restart", "--no-block", "--", "report-mark-web.service"]])

    async def test_restart_while_transitioning_is_409(self):
        for state in ("activating", "deactivating", "reloading"):
            with self.subTest(state=state):
                runner = ShowRunner({"report-mark-web.service": {"active": state}})
                resp = await self.agent(runner).handle_line(_req("restart", "web"))
                self.assertEqual(resp["error"]["code"], "already_running")
        runner = ShowRunner({"report-mark-web.service": {"active": "active", "job": "99"}})
        self.assertEqual((await self.agent(runner).handle_line(_req("restart", "web")))["error"]["code"],
                         "already_running")

    async def test_restart_of_a_stopped_or_failed_web_is_allowed(self):
        for state in ("inactive", "failed"):
            runner = ShowRunner({"report-mark-web.service": {"active": state}})
            self.assertTrue((await self.agent(runner).handle_line(_req("restart", "web")))["ok"])

    async def test_second_restart_before_execution_is_409(self):
        runner = ShowRunner({"report-mark-web.service": {"active": "active"}})
        agent = self.agent(runner, restart_delay=0.0)
        first = await agent.handle(_req("restart", "web"))
        second = await agent.handle_line(_req("restart", "web"))
        self.assertEqual(second["error"]["code"], "already_running")
        await first.after()
        self.assertEqual(len(runner.commands()), 1)
        third = await agent.handle(_req("restart", "web"))
        self.assertTrue(third.response["ok"])

    async def test_restart_failure_after_acceptance_releases_group(self):
        runner = ShowRunner({"report-mark-web.service": {"active": "active"}},
                            start_result=RunResult(1, b"", b"Access denied"))
        agent = self.agent(runner, restart_delay=0.0)
        with self.assertLogs("ops_agent", "ERROR"):
            await (await agent.handle(_req("restart", "web"))).after()
        self.assertEqual(agent._busy_groups, set())

    async def test_handle_line_schedules_restart_in_background(self):
        runner = ShowRunner({"report-mark-web.service": {"active": "active"}})
        agent = self.agent(runner, restart_delay=0.0)
        self.assertTrue((await agent.handle_line(_req("restart", "web")))["ok"])
        await agent.drain_background(2)
        self.assertEqual([c[2] for c in runner.commands()], ["restart"])


class SocketRestartTests(ActionsTestCase):
    async def test_response_reaches_client_before_restart_runs(self):
        tmp = tempfile.mkdtemp(prefix="rmops", dir="/tmp")
        path = os.path.join(tmp, "a.sock")
        runner = ShowRunner({"report-mark-web.service": {"active": "active"}})
        agent = Agent(_catalog(self.lock_dir, restart_delay=0.3), runner)
        stop = asyncio.Event()
        task = asyncio.create_task(serve(agent, path, stop))
        try:
            for _ in range(100):
                if os.path.exists(path):
                    break
                await asyncio.sleep(0.01)
            reader, writer = await asyncio.open_unix_connection(path)
            writer.write(_req("restart", "web"))
            await writer.drain()
            resp = json.loads(await reader.readline())
            writer.close()
            await writer.wait_closed()
            self.assertTrue(resp["ok"], resp)
            self.assertEqual(resp["result"]["execute_after_ms"], 300)
            self.assertEqual(runner.commands(), [], "client 拿到回應時 restart 還沒執行")
            for _ in range(100):
                if runner.commands():
                    break
                await asyncio.sleep(0.02)
            self.assertEqual([c[2] for c in runner.commands()], ["restart"])
        finally:
            stop.set()
            await task
            os.rmdir(tmp)


# ── catalog 載入期規則與 repo 的兩份 catalog ──────────────────────────────


def _service(**kw):
    return {"name": "x", "kind": "systemd", "unit": "report-mark-sync.service", "tier": "important",
            "actions": ["status", "run"], "group": "g", **kw}


class CatalogWriteRuleTests(unittest.TestCase):
    def _bad(self, svc, environment="production", pattern=None):
        with self.assertRaisesRegex(CatalogError, pattern or ""):
            parse_catalog(_raw(environment, services=[svc]))

    def test_good_entries_load(self):
        parse_catalog(_raw(services=[_service()]))
        parse_catalog(_raw(services=[_service(unit="report-mark-web.service", actions=["status", "restart"])]))
        parse_catalog(_raw(services=[_service(flock_files=["/a/b/.claude_cli.lock"], pid_files=["/a/b/p.lock"])]))

    def test_pg_nginx_cloudflared_can_never_be_written(self):
        for svc in (
            {"name": "postgres", "kind": "container", "container": "report-mark-postgres", "tier": "critical",
             "actions": ["status", "restart"], "group": "db"},
            {"name": "nginx", "kind": "container", "container": "deploy-nginx-1", "tier": "critical",
             "actions": ["status", "restart"], "group": "edge"},
            {"name": "cloudflared", "kind": "container", "container": "deploy-cloudflared-1", "tier": "critical",
             "actions": ["status", "run"], "group": "edge"},
            _service(unit="report-mark-postgres.service", actions=["status", "restart"]),
            _service(unit="postgresql@16-main.service"),
            _service(unit="nginx.service"),
            _service(unit="cloudflared.service"),
            _service(unit="docker.service"),
            _service(unit="report-mark-ops-agent.service"),
        ):
            with self.subTest(svc=svc):
                self._bad(svc)

    def test_restart_only_web_and_run_not_web(self):
        self._bad(_service(actions=["status", "restart"]), pattern="restart 只開放")
        self._bad(_service(unit="report-mark-web.service"), pattern="不用 run")

    def test_structure_rules(self):
        self._bad(_service(group=None), pattern="group")
        self._bad(_service(actions=["run"]), pattern="status")
        self._bad(_service(unit="report-mark-web.service", actions=["status", "restart", "run"]), pattern="同時")
        self._bad(_service(group="Bad Group"), pattern="group")
        self._bad({**_service(actions=["status"], flock_files=["/a/b"]), "group": None}, pattern="只能寫在有 group")
        for bad in (["relative/path"], ["/a/../etc/shadow"], ["/a//b"], ["/a/./b"], [], ["/a b"], ["/a"] * 2,
                    ["/x"] * 9, "/a/b", [1]):
            with self.subTest(bad=bad):
                self._bad(_service(flock_files=bad))
                self._bad(_service(pid_files=bad))

    def test_dev_catalog_cannot_target_production_units_with_write_ops(self):
        self._bad(_service(unit="report-mark-web.service", actions=["status", "restart"]), "development",
                  pattern="development catalog 只能列")
        self._bad(_service(), "development", pattern="development catalog 只能列")


class RepoCatalogWriteTests(unittest.TestCase):
    def test_prod_write_actions_are_exactly_the_decided_whitelist(self):
        prod = load_catalog(PROD_TOML, resolve_user=_uid)
        writes = {s.name: [a for a in s.actions if a in protocol.WRITE_ACTIONS] for s in prod.services}
        writes = {k: v for k, v in writes.items() if v}
        self.assertEqual(writes, {"web": ["restart"], "sync": ["run"], "backup": ["run"], "freshness": ["run"],
                                  "audit": ["run"], "r2-reconcile": ["run"], "upload": ["run"],
                                  "db-snapshot": ["run"], "analytics-rollup": ["run"]})
        for name in ("postgres", "nginx", "cloudflared"):
            self.assertLessEqual(set(prod.get(name).actions), {"status", "logs"}, name)

    def test_admin_v2_units_run_decisions(self):
        """Admin v2：只讀或冪等、零 LLM 的 db-snapshot、analytics-rollup 給 run，各自一個 group、不帶鎖檔；
        會刪資料的 security-retention 與探針／P5 狀態機的 security-health、security-incident 唯讀。"""
        prod = load_catalog(PROD_TOML, resolve_user=_uid)
        for name in ("db-snapshot", "analytics-rollup"):
            with self.subTest(name=name):
                svc = prod.get(name)
                self.assertEqual(svc.group, name)
                self.assertEqual(prod.group_members(name), (svc,))
                self.assertEqual((svc.flock_files, svc.pid_files), ((), ()))
                self.assertEqual(svc.unit, f"report-mark-{name}.service")
                self.assertTrue((REPO_ROOT / "deploy" / "systemd" / svc.unit).is_file())
                self.assertTrue((REPO_ROOT / "deploy" / "systemd" / svc.timer).is_file())
        for name in ("security-health", "security-incident", "security-retention"):
            with self.subTest(name=name):
                svc = prod.get(name)
                self.assertEqual(svc.actions, ("status", "logs"))
                self.assertTrue((REPO_ROOT / "deploy" / "systemd" / svc.unit).is_file())
                self.assertTrue((REPO_ROOT / "deploy" / "systemd" / svc.timer).is_file())
        self.assertEqual(prod.get("security-health").depends_on, ())
        self.assertEqual(prod.get("security-incident").depends_on, ("slack",))

    def test_dev_write_targets_are_dev_only(self):
        dev = load_catalog(DEV_TOML, resolve_user=_uid)
        writes = {s.name: s.unit for s in dev.services if set(s.actions) & set(protocol.WRITE_ACTIONS)}
        self.assertEqual(set(writes), {"dev-web", "dev-smoke"})
        for unit in writes.values():
            self.assertTrue(unit.startswith("report-mark-dev-"), unit)

    def test_sync_locks_are_the_batches_own_lock_files(self):
        """sync 的鎖檔必須正是批次自己取的那兩個（路徑改了這裡會紅，不是改 catalog 去配合）。"""
        from scripts import _claude_lock

        prod = load_catalog(PROD_TOML, resolve_user=_uid)
        sync = prod.get("sync")
        claude_rel = _claude_lock.LOCK_PATH.relative_to(_claude_lock.ROOT).as_posix()
        sync_rel = re.search(r'^LOCK="([^"]+)"', (REPO_ROOT / "scripts" / "sync_new_reports.sh")
                             .read_text(encoding="utf-8"), re.M).group(1)
        self.assertEqual(len(sync.flock_files), 1)
        self.assertEqual(len(sync.pid_files), 1)
        root = sync.flock_files[0].removesuffix("/" + claude_rel)
        self.assertNotEqual(root, sync.flock_files[0], f"flock_files 要指向 <部署目錄>/{claude_rel}")
        self.assertEqual(sync.pid_files[0], f"{root}/{sync_rel}")

    def test_upload_lock_file_matches_worker(self):
        """upload 的 flock_files 就是 worker 整輪鎖（殼的 LOCK 預設值），自成 execution group、不併進 llm-batch。"""
        prod = load_catalog(PROD_TOML, resolve_user=_uid)
        upload, sync = prod.get("upload"), prod.get("sync")
        shell = (REPO_ROOT / "scripts" / "process_uploads.sh").read_text(encoding="utf-8")
        rel = re.search(r'^LOCK="\$\{UPLOAD_WORKER_LOCK_FILE:-([^}]+)\}"', shell, re.M).group(1)
        root = sync.flock_files[0].rsplit("/data/", 1)[0]
        self.assertEqual(upload.flock_files, (f"{root}/{rel}",))
        self.assertEqual(upload.group, "upload")
        self.assertEqual(set(upload.depends_on), {"postgres", "clamav", "deepseek", "r2"})

    def test_restart_delay_present(self):
        for path in (PROD_TOML, DEV_TOML):
            self.assertGreater(load_catalog(path, resolve_user=_uid).agent.restart_delay, 0)


class CheckFunctionTests(unittest.TestCase):
    def test_check_write_allowed_matches_catalog_rules(self):
        ok = _svc("web", "report-mark-web.service", actions_=("status", "restart"), group="web")
        actions.check_write_allowed(ok, "restart")
        with self.assertRaises(protocol.ProtocolError):
            actions.check_write_allowed(replace(ok, group=None), "restart")
        with self.assertRaises(protocol.ProtocolError):
            actions.check_write_allowed(replace(ok, unit="report-mark-postgres.service"), "restart")


if __name__ == "__main__":
    unittest.main()
