"""維運代理（ops_agent/）：catalog 驗證、dev／prod 交叉拒絕、白名單、參數邊界、逾時、回應上限、SO_PEERCRED。

**不得真的呼叫 systemctl／journalctl／docker**：status／logs 一律用 `FakeRunner`（記下 argv、回罐頭輸出）。
`SubprocessRunner` 本身只拿 `sys.executable` 驗逾時與輸出上限。socket 測試在 /tmp 的臨時目錄起真的
Unix socket（AF_UNIX 路徑上限約 107 字元，不能放在很深的暫存目錄）。
"""

from __future__ import annotations

import asyncio
import json
import os
import stat
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ops_agent import backends, protocol
from ops_agent.catalog import AgentConfig, Catalog, CatalogError, Service, load_catalog, parse_catalog, validate_binding
from ops_agent.runner import RunResult, SubprocessRunner
from ops_agent.server import Agent, serve

REPO_ROOT = Path(__file__).resolve().parents[1]
PROD_TOML = REPO_ROOT / "deploy" / "ops" / "services.prod.toml"
DEV_TOML = REPO_ROOT / "deploy" / "ops" / "services.dev.toml"
NOW = datetime(2026, 10, 6, 12, 0, 0, tzinfo=timezone.utc)


def _uid(_name: str) -> int:
    return 4242  # CI 沒有 kashionz 這個使用者：catalog 測試不依賴本機帳號


def _raw(environment="production", services=None, **agent) -> dict:
    return {
        "schema": 1,
        "environment": environment,
        "socket_path": protocol.CANONICAL_SOCKETS[environment],
        "agent": {"allowed_uids": [4242], **agent},
        "services": services if services is not None else [
            {"name": "web", "kind": "systemd", "unit": "report-mark-web.service", "tier": "critical",
             "actions": ["status", "logs"]},
        ],
    }


def _catalog(*, allowed=(4242,), request_timeout=5.0, max_response_bytes=256 * 1024, environment="production",
             services=None) -> Catalog:
    if services is None:
        services = (
            Service(name="web", kind="systemd", tier="critical", actions=("status", "logs"),
                    unit="report-mark-web.service"),
            Service(name="sync", kind="systemd", tier="important", actions=("status", "logs"),
                    unit="report-mark-sync.service", timer="report-mark-sync.timer"),
            Service(name="postgres", kind="container", tier="critical", actions=("status", "logs"),
                    container="report-mark-postgres"),
            Service(name="nginx", kind="container", tier="critical", actions=("status", "logs"),
                    container="deploy-nginx-1"),
            Service(name="audit", kind="systemd", tier="supporting", actions=("status",),
                    unit="report-mark-audit.service"),
        )
    return Catalog(environment=environment, socket_path=protocol.CANONICAL_SOCKETS[environment],
                   agent=AgentConfig(allowed_uids=frozenset(allowed), request_timeout=request_timeout,
                                     command_timeout=2.0, max_response_bytes=max_response_bytes),
                   services=tuple(services))


class FakeRunner:
    """記下每次的 argv；handler(argv) 回 RunResult（或 coroutine）。"""

    def __init__(self, handler=None):
        self.calls: list[dict] = []
        self.handler = handler or (lambda argv: RunResult(0, b"", b""))

    async def run(self, argv, *, timeout, max_bytes, merge_stderr=False, keep_tail=False, env=None):
        self.calls.append({"argv": list(argv), "timeout": timeout, "max_bytes": max_bytes,
                           "merge_stderr": merge_stderr, "keep_tail": keep_tail, "env": env})
        result = self.handler(list(argv))
        if asyncio.iscoroutine(result):
            result = await result
        return result


def _req(op, service=None, env="production", **params) -> bytes:
    obj = {"v": 1, "id": "t1", "env": env, "op": op}
    if service is not None:
        obj["service"] = service
    if params:
        obj["params"] = params
    return (json.dumps(obj) + "\n").encode()


# ── catalog ──────────────────────────────────────────────────────────────


class CatalogFileTests(unittest.TestCase):
    def test_repo_catalogs_load(self):
        prod = load_catalog(PROD_TOML, resolve_user=_uid)
        dev = load_catalog(DEV_TOML, resolve_user=_uid)
        self.assertEqual((prod.environment, prod.socket_path),
                         ("production", "/run/report-mark-ops/agent.sock"))
        self.assertEqual((dev.environment, dev.socket_path),
                         ("development", "/run/report-mark-ops-dev/agent.sock"))
        self.assertEqual(prod.agent.allowed_uids, frozenset({4242}))

    def test_prod_catalog_covers_the_required_services(self):
        prod = load_catalog(PROD_TOML, resolve_user=_uid)
        targets = {s.target for s in prod.services}
        for unit in ("report-mark-web.service", "report-mark-sync.service", "report-mark-backup.service",
                     "report-mark-freshness.service", "report-mark-audit.service",
                     "report-mark-r2-reconcile.service", "report-mark-health.service",
                     "report-mark-incident.service", "report-mark-edge-health.service"):
            self.assertIn(unit, targets)
        for container in ("report-mark-postgres", "deploy-nginx-1", "deploy-cloudflared-1"):
            self.assertIn(container, targets)

    def test_prod_catalog_units_exist_in_deploy_dir(self):
        """catalog 指到的 unit 必須是 repo 裡真的有的（改名、刪檔時這裡會紅）。"""
        prod = load_catalog(PROD_TOML, resolve_user=_uid)
        systemd_dir = REPO_ROOT / "deploy" / "systemd"
        missing = [t for s in prod.services for t in (s.unit, s.timer) if t and not (systemd_dir / t).exists()]
        self.assertEqual(missing, [])

    def test_dev_catalog_only_has_dev_things(self):
        dev = load_catalog(DEV_TOML, resolve_user=_uid)
        for svc in dev.services:
            self.assertTrue(svc.target.startswith("report-mark-dev"), svc.target)
        self.assertIn("report-mark-devdb", {s.target for s in dev.services})


class CatalogValidationTests(unittest.TestCase):
    def test_dev_catalog_cannot_list_production_units(self):
        raw = _raw("development")  # 預設服務是 report-mark-web.service
        with self.assertRaisesRegex(CatalogError, "development catalog 只能列"):
            parse_catalog(raw)

    def test_dev_catalog_cannot_list_production_container(self):
        raw = _raw("development", services=[{"name": "pg", "kind": "container", "container": "report-mark-postgres",
                                             "tier": "critical", "actions": ["status"]}])
        with self.assertRaises(CatalogError):
            parse_catalog(raw)

    def test_prod_catalog_cannot_list_dev_things(self):
        raw = _raw("production", services=[{"name": "devdb", "kind": "container", "container": "report-mark-devdb",
                                            "tier": "critical", "actions": ["status"]}])
        with self.assertRaisesRegex(CatalogError, "不得列開發用"):
            parse_catalog(raw)

    def test_socket_path_must_match_environment(self):
        raw = _raw("production")
        raw["socket_path"] = protocol.CANONICAL_SOCKETS["development"]
        with self.assertRaisesRegex(CatalogError, "socket_path"):
            parse_catalog(raw)

    def test_unknown_action_rejected(self):
        raw = _raw(services=[{"name": "web", "kind": "systemd", "unit": "report-mark-web.service",
                              "tier": "critical", "actions": ["status", "stop"]}])
        with self.assertRaisesRegex(CatalogError, "不支援的 action"):
            parse_catalog(raw)

    def test_unknown_keys_rejected(self):
        for mutate in (
            lambda r: r.update(extra=1),
            lambda r: r["agent"].update(shell="/bin/sh"),
            lambda r: r["services"][0].update(actoins=["status"]),
        ):
            raw = _raw()
            mutate(raw)
            with self.subTest(raw=raw), self.assertRaisesRegex(CatalogError, "不認得"):
                parse_catalog(raw)

    def test_bad_names_and_targets_rejected(self):
        bad = [
            {"name": "Web", "kind": "systemd", "unit": "report-mark-web.service"},
            {"name": "web", "kind": "systemd", "unit": "--all.service"},
            {"name": "web", "kind": "systemd", "unit": "report-mark-web.socket"},
            {"name": "web", "kind": "systemd", "unit": "report-mark-web.service", "timer": "x.service"},
            {"name": "web", "kind": "container", "container": "-x"},
            {"name": "web", "kind": "container", "container": "a b"},
            {"name": "web", "kind": "systemd", "unit": "report-mark-web.service", "container": "x"},
            {"name": "web", "kind": "pod", "unit": "report-mark-web.service"},
        ]
        for svc in bad:
            svc = {"tier": "critical", "actions": ["status"], **svc}
            with self.subTest(svc=svc), self.assertRaises(CatalogError):
                parse_catalog(_raw(services=[svc]))

    def test_duplicates_rejected(self):
        svc = {"name": "web", "kind": "systemd", "unit": "report-mark-web.service", "tier": "critical",
               "actions": ["status"]}
        with self.assertRaisesRegex(CatalogError, "重複"):
            parse_catalog(_raw(services=[svc, dict(svc)]))
        with self.assertRaises(CatalogError):
            parse_catalog(_raw(services=[svc, {**svc, "name": "web2"}]))

    def test_allowed_callers_required_and_never_root(self):
        raw = _raw()
        raw["agent"]["allowed_uids"] = []
        with self.assertRaisesRegex(CatalogError, "至少要一個"):
            parse_catalog(raw)
        raw["agent"]["allowed_uids"] = [0]
        with self.assertRaisesRegex(CatalogError, "root"):
            parse_catalog(raw)

    def test_unknown_user_rejected(self):
        raw = _raw()
        raw["agent"] = {"allowed_users": ["no-such-user-report-mark-xyz"]}
        with self.assertRaisesRegex(CatalogError, "不存在"):
            parse_catalog(raw)

    def test_binaries_must_be_absolute(self):
        with self.assertRaisesRegex(CatalogError, "絕對路徑"):
            parse_catalog(_raw(docker="docker"))


class BindingTests(unittest.TestCase):
    def test_dev_socket_with_prod_catalog_refused(self):
        prod = parse_catalog(_raw("production"))
        with self.assertRaisesRegex(CatalogError, "拒絕以 development 的 socket"):
            validate_binding(prod, protocol.CANONICAL_SOCKETS["development"])

    def test_prod_socket_with_dev_catalog_refused(self):
        dev = load_catalog(DEV_TOML, resolve_user=_uid)
        with self.assertRaisesRegex(CatalogError, "拒絕以 production 的 socket"):
            validate_binding(dev, protocol.CANONICAL_SOCKETS["production"])

    def test_prod_socket_cannot_be_overridden(self):
        prod = parse_catalog(_raw("production"))
        with self.assertRaisesRegex(CatalogError, "只能綁"):
            validate_binding(prod, "/tmp/somewhere.sock")
        validate_binding(prod, protocol.CANONICAL_SOCKETS["production"])

    def test_dev_may_use_temporary_socket(self):
        dev = load_catalog(DEV_TOML, resolve_user=_uid)
        validate_binding(dev, "/tmp/rmops-test/agent.sock")
        with self.assertRaises(CatalogError):
            validate_binding(dev, "relative.sock")

    def test_cli_refuses_to_start_on_cross_binding(self):
        from ops_agent import server

        with tempfile.TemporaryDirectory() as tmp:
            toml = Path(tmp) / "c.toml"
            toml.write_text(PROD_TOML.read_text(encoding="utf-8").replace('["kashionz"]', "[]")
                            .replace("max_concurrent = 4", "max_concurrent = 4\nallowed_uids = [4242]"),
                            encoding="utf-8")
            rc = server.main(["--catalog", str(toml), "--socket", protocol.CANONICAL_SOCKETS["development"],
                              "--check"])
            self.assertEqual(rc, 2)
            self.assertEqual(server.main(["--catalog", str(toml), "--check"]), 0)


def _svc(name, deps=(), **extra) -> dict:
    return {"name": name, "kind": "systemd", "unit": f"report-mark-{name}.service", "tier": "important",
            "actions": ["status"], "depends_on": list(deps), **extra}


class DependencyGraphTests(unittest.TestCase):
    """depends_on 與 [[externals]]：引用存在、不自我依賴、無環；probe 只能指向有 status 的 systemd 服務。"""

    def _parse(self, services, externals=None):
        raw = _raw(services=services)
        if externals is not None:
            raw["externals"] = externals
        return parse_catalog(raw)

    def test_repo_catalogs_declare_dependencies(self):
        prod = load_catalog(PROD_TOML, resolve_user=_uid)
        self.assertEqual(set(prod.get("web").depends_on), {"postgres", "r2", "deepseek"})
        self.assertEqual({e.name for e in prod.externals},
                         {"r2", "deepseek", "nas", "slack", "public-edge", "linebot"})
        self.assertIn("nas", prod.get("sync").depends_on)
        dev = load_catalog(DEV_TOML, resolve_user=_uid)
        self.assertEqual(dev.get("dev-web").depends_on, ("devdb",))

    def test_valid_graph_with_externals(self):
        cat = self._parse([_svc("web", ["r2"]), _svc("health")],
                          [{"name": "r2", "tier": "critical", "probe": "health", "down_exit_codes": [6]},
                           {"name": "edge", "tier": "critical", "depends_on": ["web"]}])
        self.assertEqual(cat.externals[0].ok_exit_codes, (0,))
        self.assertEqual(cat.externals[0].public()["kind"], "external")
        self.assertEqual(cat.get("web").public()["depends_on"], ["r2"])
        self.assertIsNone(cat.get("r2"), "外部依賴不是服務：代理不會對它做 status／logs")

    def test_unknown_reference_rejected(self):
        with self.assertRaisesRegex(CatalogError, "不存在的節點 'postgres'"):
            self._parse([_svc("web", ["postgres"])])
        with self.assertRaisesRegex(CatalogError, "不存在的節點"):
            self._parse([_svc("web")], [{"name": "edge", "tier": "critical", "depends_on": ["nginx"]}])

    def test_self_dependency_rejected(self):
        with self.assertRaisesRegex(CatalogError, "不得依賴自己"):
            self._parse([_svc("web", ["web"])])
        with self.assertRaisesRegex(CatalogError, "不得依賴自己"):
            self._parse([_svc("web")], [{"name": "r2", "tier": "critical", "depends_on": ["r2"]}])

    def test_cycles_rejected_with_the_path(self):
        with self.assertRaisesRegex(CatalogError, "依賴圖有環：a → b → c → a"):
            self._parse([_svc("a", ["b"]), _svc("b", ["c"]), _svc("c", ["a"])])
        with self.assertRaisesRegex(CatalogError, "有環"):  # 經過外部依賴的環也算
            self._parse([_svc("web", ["edge"])], [{"name": "edge", "tier": "critical", "depends_on": ["web"]}])

    def test_find_cycle_handles_long_chains_without_recursion(self):
        from ops_agent.catalog import find_cycle

        chain = {f"n{i}": (f"n{i + 1}",) for i in range(5000)}
        chain["n5000"] = ()
        self.assertIsNone(find_cycle(chain))
        chain["n5000"] = ("n0",)
        self.assertEqual(len(find_cycle(chain)), 5002)
        self.assertIsNone(find_cycle({"a": ("b", "c"), "b": ("c",), "c": ()}))  # 菱形不是環

    def test_depends_on_shape(self):
        for bad in ("postgres", ["Postgres"], ["a", "a"], [1], [f"n{i}" for i in range(17)]):
            with self.subTest(bad=bad), self.assertRaises(CatalogError):
                self._parse([{**_svc("web"), "depends_on": bad}])

    def test_names_are_shared_between_services_and_externals(self):
        with self.assertRaisesRegex(CatalogError, "命名空間"):
            self._parse([_svc("web")], [{"name": "web", "tier": "critical"}])

    def test_external_validation(self):
        base = [_svc("health"), _svc("web")]
        bad = [
            ({"name": "r2", "tier": "critical", "unit": "x.service"}, "不認得"),
            ({"name": "r2", "tier": "urgent"}, "tier"),
            ({"name": "R2", "tier": "critical"}, "name"),
            ({"name": "r2", "tier": "critical", "down_exit_codes": [6]}, "沒有 probe"),
            ({"name": "r2", "tier": "critical", "probe": "health"}, "至少要寫"),
            ({"name": "r2", "tier": "critical", "probe": "health", "down_exit_codes": [0]}, "同時屬於"),
            ({"name": "r2", "tier": "critical", "probe": "health", "down_exit_codes": [256]}, "0–255"),
            ({"name": "r2", "tier": "critical", "probe": "health", "down_exit_codes": [True]}, "0–255"),
            ({"name": "r2", "tier": "critical", "probe": "nope", "down_exit_codes": [6]}, "不存在的服務"),
        ]
        for ext, msg in bad:
            with self.subTest(ext=ext), self.assertRaisesRegex(CatalogError, msg):
                self._parse(base, [ext])
        with self.assertRaisesRegex(CatalogError, "systemd 服務"):
            self._parse([*base, {"name": "pg", "kind": "container", "container": "report-mark-postgres",
                                 "tier": "critical", "actions": ["status"]}],
                        [{"name": "r2", "tier": "critical", "probe": "pg", "down_exit_codes": [1]}])
        with self.assertRaisesRegex(CatalogError, "systemd 服務"):
            self._parse([_svc("health", actions=["logs"]), _svc("web")],
                        [{"name": "r2", "tier": "critical", "probe": "health", "down_exit_codes": [6]}])
        with self.assertRaisesRegex(CatalogError, "externals"):
            raw = _raw()
            raw["externals"] = {"name": "r2"}
            parse_catalog(raw)

    def test_check_cli_reports_graph_size_and_rejects_cycles(self):
        import contextlib
        import io

        from ops_agent import server

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(server.main(["--catalog", str(PROD_TOML), "--check"]), 0)
        self.assertIn("environment=production", out.getvalue())
        self.assertIn("externals=6", out.getvalue())
        with tempfile.TemporaryDirectory() as tmp:
            toml = Path(tmp) / "c.toml"
            web_deps = 'depends_on = ["postgres", "r2", "deepseek"]\ndescription = "Web'
            text = PROD_TOML.read_text(encoding="utf-8").replace('["kashionz"]', "[]").replace(
                "max_concurrent = 4", "max_concurrent = 4\nallowed_uids = [4242]")
            self.assertIn(web_deps, text)
            toml.write_text(text.replace(web_deps, 'depends_on = ["nginx"]\ndescription = "Web'), encoding="utf-8")
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertEqual(server.main(["--catalog", str(toml), "--check"]), 2)
            self.assertIn("依賴圖有環", err.getvalue())


# ── 參數邊界 ─────────────────────────────────────────────────────────────


class ParamTests(unittest.TestCase):
    AGE = timedelta(days=7)

    def test_lines_bounds(self):
        self.assertEqual(protocol.parse_lines(None, 1000), 200)
        self.assertEqual(protocol.parse_lines(None, 50), 50)
        self.assertEqual(protocol.parse_lines(1, 1000), 1)
        self.assertEqual(protocol.parse_lines(1000, 1000), 1000)
        for bad in (0, -1, 1001, True, "10", 1.5):
            with self.subTest(bad=bad), self.assertRaises(protocol.ProtocolError):
                protocol.parse_lines(bad, 1000)

    def test_since_relative(self):
        self.assertEqual(protocol.parse_since("15m", self.AGE, NOW), NOW - timedelta(minutes=15))
        self.assertEqual(protocol.parse_since("7d", self.AGE, NOW), NOW - timedelta(days=7))
        self.assertEqual(protocol.parse_since(None, self.AGE, NOW), NOW - timedelta(hours=1))

    def test_since_iso(self):
        got = protocol.parse_since("2026-10-06T18:30:00+08:00", self.AGE, NOW)
        self.assertEqual(got, datetime(2026, 10, 6, 10, 30, tzinfo=timezone.utc))
        self.assertEqual(protocol.parse_since("2026-10-06T10:30:00Z", self.AGE, NOW), got)

    def test_since_rejects_everything_else(self):
        for bad in ("8d", "169h", "0m", "yesterday", "-1week", "today", "2026-10-06T10:00:00",
                    "2026-09-01T00:00:00Z", "2026-10-07T00:00:00Z", "15 m", "1w", "", 15, "x" * 41,
                    "--since=all"):
            with self.subTest(bad=bad), self.assertRaises(protocol.ProtocolError) as ctx:
                protocol.parse_since(bad, self.AGE, NOW)
            self.assertEqual(ctx.exception.code, "invalid_params")


# ── 請求處理（假 runner）─────────────────────────────────────────────────


SHOW_OUTPUT = (
    "Id=report-mark-web.service\nLoadState=loaded\nActiveState=active\nSubState=running\nResult=success\n"
    "Type=simple\nExecMainCode=0\nExecMainStatus=0\nMainPID=123\nNRestarts=2\n"
    "ExecMainStartTimestamp=@1791191596\nExecMainExitTimestamp=\n\n"
    "Id=report-mark-sync.service\nLoadState=loaded\nActiveState=failed\nSubState=failed\nResult=exit-code\n"
    "Type=oneshot\nExecMainCode=1\nExecMainStatus=2\nExecMainStartTimestamp=@1791248400\n"
    "ExecMainExitTimestamp=@1791248500\n\n"
    "Id=report-mark-sync.timer\nLoadState=loaded\nActiveState=active\n"
    "NextElapseUSecRealtime=Tue 2026-10-06 04:00:00 UTC\nLastTriggerUSec=Tue 2026-10-06 01:00:00 UTC\n\n"
    "Id=report-mark-audit.service\nLoadState=not-found\nActiveState=inactive\n"
)

INSPECT_OUTPUT = json.dumps({
    "name": "/report-mark-postgres",
    "state": {"Status": "running", "Running": True, "Paused": False, "Restarting": False, "OOMKilled": False,
              "Dead": False, "Pid": 99, "ExitCode": 0, "Error": "", "StartedAt": "2026-10-05T07:57:52Z",
              "FinishedAt": "0001-01-01T00:00:00Z",
              "Health": {"Status": "healthy", "FailingStreak": 0, "Log": [{"Output": "DB_PASSWORD=leak"}]}},
    "restart_count": 1, "image": "pgvector/pgvector:pg16", "Env": ["POSTGRES_PASSWORD=leak"],
}) + "\n"


def _status_handler(argv):
    if argv[1] == "show":
        return RunResult(0, SHOW_OUTPUT.encode(), b"")
    if argv[1] == "inspect":
        return RunResult(1, INSPECT_OUTPUT.encode(), b"Error response from daemon: No such container: deploy-nginx-1\n")
    raise AssertionError(argv)


class AgentRequestTests(unittest.IsolatedAsyncioTestCase):
    async def _call(self, agent, line):
        return await agent.handle_line(line)

    async def test_list_batches_one_show_and_one_inspect(self):
        runner = FakeRunner(_status_handler)
        resp = await self._call(Agent(_catalog(), runner), _req("list"))
        self.assertTrue(resp["ok"], resp)
        self.assertEqual(resp["env"], "production")
        self.assertEqual(len(runner.calls), 2)
        show, inspect = runner.calls[0]["argv"], runner.calls[1]["argv"]
        self.assertEqual(show[:5], ["/usr/bin/systemctl", "show", "--no-pager", "--timestamp=unix", "-p"])
        self.assertEqual(show[6:], ["--", "report-mark-web.service", "report-mark-sync.service",
                                    "report-mark-sync.timer", "report-mark-audit.service"])
        self.assertNotIn("Environment", show[5])
        self.assertEqual(runner.calls[0]["env"], {"TZ": "UTC"})
        self.assertEqual(inspect[:5], ["/usr/bin/docker", "inspect", "--type", "container", "--format"])
        self.assertEqual(inspect[6:], ["--", "report-mark-postgres", "deploy-nginx-1"])
        self.assertNotIn(".Config.Env", inspect[5])
        items = {i["name"]: i for i in resp["result"]["items"]}
        self.assertEqual(items["web"]["summary"], "running")
        self.assertEqual(items["web"]["systemd"]["exec_main_start_at"], "2026-10-05T09:13:16Z")
        self.assertEqual(items["web"]["systemd"]["n_restarts"], 2)
        self.assertEqual(items["sync"]["summary"], "failed")
        self.assertEqual(items["sync"]["systemd"]["exec_main_code"], "exited")
        self.assertEqual(items["sync"]["systemd"]["exec_main_status"], 2)
        self.assertEqual(items["sync"]["timer_state"]["next_elapse_at"], "2026-10-06T04:00:00Z")
        self.assertEqual(items["sync"]["timer_state"]["last_trigger_at"], "2026-10-06T01:00:00Z")
        self.assertEqual(items["audit"]["summary"], "not_found")
        self.assertEqual(items["postgres"]["summary"], "running")
        self.assertEqual(items["postgres"]["container"]["health"], "healthy")
        self.assertIsNone(items["postgres"]["container"]["finished_at"])
        self.assertEqual(items["nginx"]["summary"], "not_found")

    async def test_list_carries_dependencies_and_externals_without_running_anything_for_them(self):
        import dataclasses

        from ops_agent.catalog import External

        base = _catalog()
        services = tuple(dataclasses.replace(s, depends_on=("postgres", "r2")) if s.name == "web" else s
                         for s in base.services)
        cat = dataclasses.replace(base, services=services, externals=(
            External(name="r2", tier="critical", probe="audit", down_exit_codes=(6,), description="R2"),))
        runner = FakeRunner(_status_handler)
        resp = await self._call(Agent(cat, runner), _req("list"))
        self.assertTrue(resp["ok"], resp)
        items = {i["name"]: i for i in resp["result"]["items"]}
        self.assertEqual(items["web"]["depends_on"], ["postgres", "r2"])
        self.assertEqual(items["sync"]["depends_on"], [])
        self.assertEqual(resp["result"]["externals"], [{
            "name": "r2", "kind": "external", "tier": "critical", "depends_on": [], "probe": "audit",
            "ok_exit_codes": [0], "degraded_exit_codes": [], "down_exit_codes": [6], "description": "R2"}])
        self.assertEqual(len(runner.calls), 2)  # 外部依賴不多跑任何指令
        self.assertNotIn("r2", json.dumps([c["argv"] for c in runner.calls]))
        r = await self._call(Agent(cat, runner), _req("status", "r2"))
        self.assertEqual(r["error"]["code"], "unknown_service")

    async def test_no_environment_or_health_log_leaks(self):
        resp = await self._call(Agent(_catalog(), FakeRunner(_status_handler)), _req("list"))
        text = json.dumps(resp)
        self.assertNotIn("leak", text)
        self.assertNotIn("Env", text)

    async def test_status_single_service(self):
        runner = FakeRunner(lambda argv: RunResult(0, SHOW_OUTPUT.split("\n\n")[0].encode(), b""))
        resp = await self._call(Agent(_catalog(), runner), _req("status", "web"))
        self.assertTrue(resp["ok"], resp)
        self.assertEqual(resp["result"]["name"], "web")
        self.assertEqual(runner.calls[0]["argv"][-1], "report-mark-web.service")
        self.assertIn("checked_at", resp["result"])

    async def test_status_command_failure_is_reported_per_row(self):
        runner = FakeRunner(lambda argv: RunResult(None, b"", b"", timed_out=True))
        resp = await self._call(Agent(_catalog(), runner), _req("status", "web"))
        self.assertTrue(resp["ok"])
        self.assertEqual(resp["result"]["summary"], "unknown")
        self.assertIn("逾時", resp["result"]["error"])

    async def test_show_block_mismatch_is_not_misattributed(self):
        runner = FakeRunner(lambda argv: RunResult(0, b"Id=a\nActiveState=active\n", b""))
        resp = await self._call(Agent(_catalog(), runner), _req("status", "sync"))  # sync 有 timer：要 2 段
        self.assertEqual(resp["result"]["summary"], "unknown")
        self.assertIn("無法對應", resp["result"]["error"])

    async def test_unknown_service_rejected_without_running_anything(self):
        runner = FakeRunner()
        for line in (_req("status", "nope"), _req("logs", "report-mark-web.service"), _req("status", "../web")):
            resp = await self._call(Agent(_catalog(), runner), line)
            self.assertEqual(resp["error"]["code"], "unknown_service")
        self.assertEqual(runner.calls, [])

    async def test_action_not_in_catalog_rejected(self):
        runner = FakeRunner()
        resp = await self._call(Agent(_catalog(), runner), _req("logs", "audit"))  # audit 只允許 status
        self.assertEqual(resp["error"]["code"], "action_not_allowed")
        self.assertEqual(runner.calls, [])

    async def test_unknown_ops_rejected(self):
        runner = FakeRunner()
        for op in ("stop", "run-now", "start", "enable", "disable", "exec", "shell", "kill"):
            resp = await self._call(Agent(_catalog(), runner), _req(op, "web"))
            self.assertEqual(resp["error"]["code"], "unknown_op", op)
        self.assertEqual(runner.calls, [])

    async def test_write_ops_need_the_catalog_action(self):
        """這份 catalog 沒有任何寫入類 action：restart／run 一律 action_not_allowed，什麼都不執行。"""
        runner = FakeRunner()
        for op in ("restart", "run"):
            resp = await self._call(Agent(_catalog(), runner), _req(op, "web"))
            self.assertEqual(resp["error"]["code"], "action_not_allowed", op)
        self.assertEqual(runner.calls, [])

    async def test_environment_mismatch_rejected(self):
        runner = FakeRunner()
        resp = await self._call(Agent(_catalog(), runner), _req("list", env="development"))
        self.assertEqual(resp["error"]["code"], "environment_mismatch")
        self.assertEqual(resp["env"], "production")
        self.assertEqual(runner.calls, [])

    async def test_malformed_requests(self):
        agent = Agent(_catalog(), FakeRunner())
        cases = {
            b"not json\n": "bad_request",
            b"[1,2]\n": "bad_request",
            b'{"v":2,"env":"production","op":"list"}\n': "bad_request",
            b'{"v":true,"env":"production","op":"list"}\n': "bad_request",
            b'{"v":1,"env":"production","op":"list","cmd":"rm -rf /"}\n': "bad_request",
            b'{"v":1,"env":"staging","op":"list"}\n': "bad_request",
            b'{"v":1,"env":"production","op":"list","service":"web"}\n': "bad_request",
            b'{"v":1,"env":"production","op":"status"}\n': "bad_request",
            b'{"v":1,"env":"production","op":"status","service":"web","params":{"lines":5}}\n': "invalid_params",
            b'{"v":1,"id":"x y","env":"production","op":"list"}\n': "bad_request",
        }
        for line, code in cases.items():
            resp = await agent.handle_line(line)
            self.assertEqual(resp["error"]["code"], code, line)

    async def test_logs_argv_for_systemd(self):
        runner = FakeRunner(lambda argv: RunResult(0, b"2026-10-06T10:00:00+08:00 host uv[1]: hello\n", b""))
        resp = await self._call(Agent(_catalog(), runner), _req("logs", "web", since="2h", lines=50))
        self.assertTrue(resp["ok"], resp)
        argv = runner.calls[0]["argv"]
        self.assertEqual(argv[:7], ["/usr/bin/journalctl", "--no-pager", "--quiet", "--output=short-iso",
                                    "--lines", "50", "--since"])
        self.assertRegex(argv[7], r"^@\d+$")
        self.assertEqual(argv[8:], ["--unit", "report-mark-web.service"])
        self.assertTrue(runner.calls[0]["keep_tail"])
        self.assertEqual(resp["result"]["entries"], ["2026-10-06T10:00:00+08:00 host uv[1]: hello"])
        self.assertEqual(resp["result"]["lines"], 50)
        self.assertFalse(resp["result"]["truncated"])

    async def test_logs_argv_for_container(self):
        runner = FakeRunner(lambda argv: RunResult(0, b"2026-10-06T02:00:00Z LOG: ok\n", b""))
        resp = await self._call(Agent(_catalog(), runner), _req("logs", "postgres", since="2026-10-06T00:00:00Z"))
        self.assertTrue(resp["ok"], resp)
        argv = runner.calls[0]["argv"]
        epoch = int(datetime(2026, 10, 6, tzinfo=timezone.utc).timestamp())
        self.assertEqual(argv, ["/usr/bin/docker", "logs", "--timestamps", "--since", str(epoch), "--tail", "200",
                                "--", "report-mark-postgres"])
        self.assertTrue(runner.calls[0]["merge_stderr"])

    async def test_logs_param_validation_reaches_no_command(self):
        runner = FakeRunner()
        agent = Agent(_catalog(), runner)
        for params in ({"lines": 0}, {"lines": 1001}, {"lines": "5"}, {"since": "yesterday"}, {"since": "30d"},
                       {"since": "1h", "unit": "sshd.service"}, {"output": "json"}):
            resp = await agent.handle_line(_req("logs", "web", **params))
            self.assertEqual(resp["error"]["code"], "invalid_params", params)
        self.assertEqual(runner.calls, [])

    async def test_logs_command_timeout_and_failure(self):
        agent = Agent(_catalog(), FakeRunner(lambda argv: RunResult(None, b"", b"", timed_out=True)))
        self.assertEqual((await agent.handle_line(_req("logs", "web")))["error"]["code"], "command_timeout")
        agent = Agent(_catalog(), FakeRunner(lambda argv: RunResult(1, b"Error: No such container: x\n", b"")))
        resp = await agent.handle_line(_req("logs", "postgres"))
        self.assertEqual(resp["error"]["code"], "command_failed")
        self.assertIn("No such container", resp["error"]["message"])

    async def test_request_timeout(self):
        async def slow(_argv):
            await asyncio.sleep(5)
            return RunResult(0, b"", b"")

        agent = Agent(_catalog(request_timeout=0.05), FakeRunner(slow))
        resp = await agent.handle_line(_req("logs", "web"))
        self.assertEqual(resp["error"]["code"], "timeout")

    async def test_busy_when_concurrency_exhausted(self):
        gate = asyncio.Event()

        async def blocked(_argv):
            await gate.wait()
            return RunResult(0, b"", b"")

        catalog = _catalog()
        catalog = Catalog(catalog.environment, catalog.socket_path,
                          AgentConfig(allowed_uids=frozenset({1}), max_concurrent=1), catalog.services)
        agent = Agent(catalog, FakeRunner(blocked))
        first = asyncio.create_task(agent.handle_line(_req("logs", "web")))
        await asyncio.sleep(0.01)
        self.assertEqual((await agent.handle_line(_req("logs", "web")))["error"]["code"], "busy")
        gate.set()
        self.assertTrue((await first)["ok"])

    async def test_logs_response_is_truncated_from_the_old_end(self):
        lines = [f"line-{i:05d} " + "x" * 200 for i in range(1000)]
        runner = FakeRunner(lambda argv: RunResult(0, ("\n".join(lines) + "\n").encode(), b""))
        agent = Agent(_catalog(max_response_bytes=32 * 1024), runner)
        resp = await agent.handle_line(_req("logs", "web", lines=1000))
        self.assertTrue(resp["ok"], resp.get("error"))
        result = resp["result"]
        self.assertTrue(result["truncated"])
        self.assertEqual(result["entries"][-1], lines[-1])  # 最新的那行一定在
        self.assertLess(len(result["entries"]), 1000)
        self.assertLessEqual(len(protocol.encode(resp)), 32 * 1024)

    async def test_runner_truncation_drops_partial_first_line(self):
        runner = FakeRunner(lambda argv: RunResult(0, b"tial-line\nfull-1\nfull-2\n", b"", truncated=True))
        resp = await Agent(_catalog(), runner).handle_line(_req("logs", "web"))
        self.assertEqual(resp["result"]["entries"], ["full-1", "full-2"])
        self.assertTrue(resp["result"]["truncated"])

    async def test_secrets_in_log_lines_are_redacted(self):
        raw = ("DEEPSEEK_API_KEY=sk-abcdefghijklmnopqrstuvwx\n"
               "connect postgresql+asyncpg://postgres:hunter2@localhost:5436/research\n"
               "header Authorization: Bearer abcdefghijklmnop\n"
               "X-Edge-Secret: s3cr3tvalue\n"
               "\x1b[31mred\x1b[0m plain\n")
        runner = FakeRunner(lambda argv: RunResult(0, raw.encode(), b""))
        resp = await Agent(_catalog(), runner).handle_line(_req("logs", "web"))
        text = "\n".join(resp["result"]["entries"])
        for secret in ("sk-abcdefghijklmnopqrstuvwx", "hunter2", "abcdefghijklmnop", "s3cr3tvalue", "\x1b"):
            self.assertNotIn(secret, text)
        self.assertIn("red plain", text)
        self.assertIn("postgres:<redacted>@localhost", text)


class SummaryTests(unittest.TestCase):
    def _c(self, **kw):
        base = {"status": "running", "health": None, "oom_killed": False, "exit_code": 0}
        return backends.container_summary({**base, **kw})

    def test_container_summary(self):
        self.assertEqual(self._c(), "running")
        self.assertEqual(self._c(health="unhealthy"), "failed")
        self.assertEqual(self._c(health="starting"), "transitioning")
        self.assertEqual(self._c(status="restarting"), "transitioning")
        self.assertEqual(self._c(status="exited", exit_code=0), "idle")
        self.assertEqual(self._c(status="exited", exit_code=137), "failed")
        self.assertEqual(self._c(status="exited", oom_killed=True), "failed")

    def test_parse_ts(self):
        self.assertEqual(backends.parse_ts("@0"), "1970-01-01T00:00:00Z")
        self.assertIsNone(backends.parse_ts(""))
        self.assertIsNone(backends.parse_ts("n/a"))
        self.assertIsNone(backends.parse_ts("Tue 2026-10-06 12:00:00 CST"))  # 不是 UTC：寧可空也不猜


# ── socket：SO_PEERCRED、請求大小、權限、收尾 ─────────────────────────────


class SocketTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.mkdtemp(prefix="rmops", dir="/tmp")
        self.path = os.path.join(self.tmp, "a.sock")

    async def asyncTearDown(self):
        for name in os.listdir(self.tmp):
            os.unlink(os.path.join(self.tmp, name))
        os.rmdir(self.tmp)

    async def _serve(self, catalog):
        stop = asyncio.Event()
        task = asyncio.create_task(serve(Agent(catalog, FakeRunner(_status_handler)), self.path, stop))
        for _ in range(100):
            if os.path.exists(self.path):
                break
            await asyncio.sleep(0.01)
        return stop, task

    async def _roundtrip(self, payload: bytes) -> list[dict]:
        reader, writer = await asyncio.open_unix_connection(self.path)
        writer.write(payload)
        await writer.drain()
        out = []
        while line := await reader.readline():
            out.append(json.loads(line))
            if len(out) >= payload.count(b"\n"):
                break
        writer.close()
        await writer.wait_closed()
        return out

    async def test_allowed_peer_gets_answers_and_socket_is_0660(self):
        stop, task = await self._serve(_catalog(allowed=(os.getuid(),)))
        try:
            self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o660)
            out = await self._roundtrip(_req("list") + _req("status", "nope"))
            self.assertTrue(out[0]["ok"])
            self.assertEqual(out[1]["error"]["code"], "unknown_service")
        finally:
            stop.set()
            await task
        self.assertFalse(os.path.exists(self.path), "停止後要移除 socket")

    async def test_peer_uid_not_in_catalog_is_rejected(self):
        stop, task = await self._serve(_catalog(allowed=(os.getuid() + 1,)))
        try:
            out = await self._roundtrip(_req("list"))
            self.assertEqual(out[0]["error"]["code"], "forbidden_peer")
        finally:
            stop.set()
            await task

    async def test_oversized_request_is_rejected(self):
        stop, task = await self._serve(_catalog(allowed=(os.getuid(),)))
        try:
            out = await self._roundtrip(b'{"pad":"' + b"x" * (protocol.MAX_REQUEST_BYTES * 2) + b'"}\n')
            self.assertEqual(out[0]["error"]["code"], "request_too_large")
        finally:
            stop.set()
            await task

    async def test_refuses_to_replace_a_non_socket_file(self):
        Path(self.path).write_text("not a socket")
        with self.assertRaises(CatalogError):
            await serve(Agent(_catalog(), FakeRunner()), self.path, asyncio.Event())


# ── SubprocessRunner：用 sys.executable，不碰 systemctl／docker ───────────


class SubprocessRunnerTests(unittest.IsolatedAsyncioTestCase):
    async def test_timeout_kills_the_child(self):
        res = await SubprocessRunner().run([sys.executable, "-c", "import time; time.sleep(10)"],
                                           timeout=0.3, max_bytes=1024)
        self.assertTrue(res.timed_out)
        self.assertIsNone(res.returncode)

    async def test_output_cap_keeps_head_or_tail(self):
        code = "import sys\nfor i in range(20000): sys.stdout.write(f'{i:06d}\\n')"
        head = await SubprocessRunner().run([sys.executable, "-c", code], timeout=10, max_bytes=70)
        self.assertTrue(head.truncated)
        self.assertTrue(head.stdout.startswith(b"000000\n"))
        self.assertEqual(len(head.stdout), 70)
        tail = await SubprocessRunner().run([sys.executable, "-c", code], timeout=10, max_bytes=70, keep_tail=True)
        self.assertTrue(tail.truncated)
        self.assertTrue(tail.stdout.endswith(b"019999\n"))
        self.assertEqual(tail.returncode, 0)

    async def test_child_gets_minimal_environment(self):
        os.environ["OPS_AGENT_TEST_SECRET"] = "should-not-pass"
        try:
            res = await SubprocessRunner().run(
                [sys.executable, "-c", "import os, json; print(json.dumps(sorted(os.environ)))"],
                timeout=10, max_bytes=65536)
        finally:
            del os.environ["OPS_AGENT_TEST_SECRET"]
        keys = set(json.loads(res.stdout))
        self.assertNotIn("OPS_AGENT_TEST_SECRET", keys)
        self.assertLessEqual(keys - {"TZ", "LC_CTYPE"}, {"PATH", "LANG", "LC_ALL", "SYSTEMD_COLORS", "SYSTEMD_PAGER",
                                                         "SYSTEMD_LESS"})


class StdlibOnlyTests(unittest.TestCase):
    """代理不得 import app.*、web.* 或第三方套件（理由見 ops_agent/__init__.py）。"""

    def test_imports_are_stdlib_or_ops_agent(self):
        import ast

        allowed_roots = set(sys.stdlib_module_names) | {"ops_agent", "__future__"}
        offenders = []
        for path in sorted((REPO_ROOT / "ops_agent").glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    names = [node.module]
                offenders += [f"{path.name}: {n}" for n in names if n.split(".")[0] not in allowed_roots]
        self.assertEqual(offenders, [])

    def test_runs_with_system_python_without_venv(self):
        """系統的 python3（有的話）以隔離模式 `-I` 照樣能 --check 一份 catalog——不靠 .venv 的任何套件。"""
        import subprocess

        python = "/usr/bin/python3" if os.path.exists("/usr/bin/python3") else sys.executable

        with tempfile.TemporaryDirectory() as tmp:
            toml = Path(tmp) / "c.toml"
            toml.write_text(DEV_TOML.read_text(encoding="utf-8").replace('allowed_users = ["kashionz"]',
                                                                           "allowed_uids = [4242]"),
                            encoding="utf-8")
            proc = subprocess.run(
                [python, "-I", "-c",
                 f"import sys; sys.path.insert(0, {str(REPO_ROOT)!r}); from ops_agent.server import main; "
                 f"sys.exit(main(['--catalog', {str(toml)!r}, '--check']))"],
                capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("environment=development", proc.stdout)


if __name__ == "__main__":
    unittest.main()
