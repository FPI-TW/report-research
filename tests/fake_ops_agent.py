"""假的維運代理：在背景執行緒起一個真的 Unix socket，照 handler 回一行 JSON，記下收到的請求。

給 web 端（`web/ops_client.py`、`web/routers/admin_ops.py`）的 HTTP 層測試用：走真的 socket 與協定，
但不執行任何 systemctl／journalctl／docker。代理本體的行為在 `tests/test_ops_agent.py` 另外測。

用法：
    with FakeOpsAgent() as agent, agent.installed():
        r = client.get("/api/admin/ops/services")
    agent.requests  # 收到的請求（dict）

handler(req) 回：完整回應 dict、`"hang"`（不回應，讓 client 逾時）、`"close"`（直接斷線）、`"garbage"`。
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import threading
from contextlib import contextmanager
from unittest import mock

SERVICE = {
    "name": "web", "kind": "systemd", "tier": "critical", "target": "report-mark-web.service", "timer": None,
    "actions": ["status", "logs"], "description": "Web",
}
STATUS = {
    **SERVICE, "summary": "running", "error": None, "container": None, "timer_state": None,
    "systemd": {"load_state": "loaded", "active_state": "active", "sub_state": "running", "result": "success",
                "type": "simple", "unit_file_state": "enabled", "exec_main_code": None, "exec_main_status": 0,
                "main_pid": 123, "n_restarts": 0, "exec_main_start_at": "2026-10-05T09:13:16Z",
                "exec_main_exit_at": None, "active_enter_at": "2026-10-05T09:13:16Z",
                "state_change_at": "2026-10-05T09:13:16Z"},
}
POSTGRES = {
    "name": "postgres", "kind": "container", "tier": "critical", "target": "report-mark-postgres", "timer": None,
    "actions": ["status", "logs"], "description": "PostgreSQL", "summary": "running", "error": None,
    "systemd": None, "timer_state": None,
    "container": {"status": "running", "running": True, "paused": False, "restarting": False, "oom_killed": False,
                  "dead": False, "exit_code": 0, "error": None, "started_at": "2026-10-05T07:57:52Z",
                  "finished_at": None, "health": None, "failing_streak": None, "restart_count": 0,
                  "image": "pgvector/pgvector:pg16"},
}
CHECKED_AT = "2026-10-06T02:00:00Z"


def ok(req: dict, result: dict) -> dict:
    return {"v": 1, "id": req.get("id"), "env": req.get("env"), "ok": True, "result": result}


def err(req: dict, code: str, message: str = "拒絕", env: str | None = None) -> dict:
    return {"v": 1, "id": req.get("id"), "env": env or req.get("env"), "ok": False,
            "error": {"code": code, "message": message}}


def action_result(req: dict, base: dict = STATUS) -> dict:
    """代理對 restart／run 的成功回應（restart＝scheduled，run＝queued）。"""
    public = {k: base[k] for k in ("name", "kind", "tier", "target", "timer", "actions", "description")}
    restart = req.get("op") == "restart"
    return {**public, "group": req.get("service"), "action": req.get("op"),
            "state": "scheduled" if restart else "queued", "previous_invocation_id": "0f1e2d3c",
            "previous_active_enter_at": "2026-10-05T09:13:16Z", "previous_exec_main_start_at": "2026-10-05T09:13:16Z",
            "execute_after_ms": 1500 if restart else 0, "accepted_at": CHECKED_AT, "checked_at": CHECKED_AT}


def default_handler(req: dict):
    op, name = req.get("op"), req.get("service")
    if op == "list":
        return ok(req, {"environment": req["env"], "host": "fake-host", "checked_at": CHECKED_AT,
                        "items": [STATUS, POSTGRES]})
    if name not in ("web", "postgres"):
        return err(req, "unknown_service", f"catalog 沒有服務 {name!r}")
    if op == "status":
        return ok(req, {**(STATUS if name == "web" else POSTGRES), "checked_at": CHECKED_AT})
    if op in ("restart", "run"):
        base = STATUS if name == "web" else POSTGRES
        if op == "restart" and name != "web":
            return err(req, "action_not_allowed", f"服務 {name} 永遠不允許 restart")
        if op == "run":
            return err(req, "action_not_allowed", f"服務 {name} 不允許 run")
        return ok(req, action_result(req, base))
    if op == "logs":
        base = STATUS if name == "web" else POSTGRES
        public = {k: base[k] for k in ("name", "kind", "tier", "target")}
        lines = req.get("params", {}).get("lines", 200)
        return ok(req, {**public, "timer": None, "actions": base["actions"], "description": base["description"],
                        "since": "2026-10-06T01:00:00Z", "lines": lines, "truncated": False,
                        "entries": ["2026-10-06T10:00:00+08:00 host uv[1]: hello"], "checked_at": CHECKED_AT})
    return err(req, "unknown_op")


class FakeOpsAgent:
    def __init__(self, handler=default_handler, *, environment: str = "production"):
        self.handler = handler
        self.environment = environment
        self.requests: list[dict] = []
        self._tmp = tempfile.mkdtemp(prefix="rmops", dir="/tmp")  # AF_UNIX 路徑上限約 107 字元
        self.socket_path = os.path.join(self._tmp, "agent.sock")
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._stop: asyncio.Event | None = None

    async def _handle(self, reader, writer):
        try:
            line = await reader.readline()
            if not line:
                return
            req = json.loads(line)
            self.requests.append(req)
            resp = self.handler(req)
            if resp == "hang":
                await asyncio.sleep(30)
                return
            if resp == "close":
                return
            if resp == "garbage":
                writer.write(b"this is not json\n")
            else:
                writer.write((json.dumps(resp, ensure_ascii=False) + "\n").encode())
            await writer.drain()
        except (ConnectionError, asyncio.CancelledError):
            pass
        finally:
            writer.close()

    async def _main(self):
        self._stop = asyncio.Event()
        server = await asyncio.start_unix_server(self._handle, path=self.socket_path)
        self._ready.set()
        async with server:
            await self._stop.wait()

    def __enter__(self):
        def _run():
            self._loop = asyncio.new_event_loop()
            self._loop.run_until_complete(self._main())
            self._loop.close()

        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()
        if not self._ready.wait(5):
            raise RuntimeError("假維運代理沒有起來")
        return self

    def __exit__(self, *exc):
        if self._loop and self._stop:
            self._loop.call_soon_threadsafe(self._stop.set)
        if self._thread:
            self._thread.join(5)
        for name in os.listdir(self._tmp):
            os.unlink(os.path.join(self._tmp, name))
        os.rmdir(self._tmp)
        return False

    def client(self, timeout: float = 2.0, environment: str | None = None):
        from web.ops_client import OpsClient

        return OpsClient(self.socket_path, environment or self.environment, timeout)

    @contextmanager
    def installed(self, timeout: float = 2.0, environment: str | None = None):
        """範圍內讓 `web.ops_client.default_client()` 指向這個假代理。"""
        from web import ops_client

        with mock.patch.object(ops_client, "default_client", lambda: self.client(timeout, environment)):
            yield self
