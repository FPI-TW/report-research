"""一般使用者的粗粒度系統狀態 `GET /api/status`（HTTP 層）。

重點：任何登入使用者可讀、未登入 401；回應只有 `status`／`message` 兩鍵，不洩漏服務清單、主機或錯誤細節；
只看 critical 層；維運代理不可用時 fail-open 回 200 `unknown`；DB 探測失敗回 `degraded`；代理那段有快取。
"""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

from fake_accounts import FakeAccounts, install, session_cookies
from fake_ops_agent import POSTGRES, STATUS, FakeOpsAgent, default_handler, err, ok
from fastapi.testclient import TestClient

from web import ops_client
from web.routers import health
from web.server import app

_LEAKS = ("fake-host", "production", "web", "postgres", "report-mark", "維運代理")


class _FakeSession:
    def __init__(self, boom: bool = False):
        self.boom = boom

    async def __aenter__(self):
        if self.boom:
            raise RuntimeError("db down")
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, *a, **k):
        return None


def _list_handler(items):
    def handler(req):
        if req.get("op") == "list":
            return ok(req, {"environment": req["env"], "host": "fake-host", "checked_at": "2026-10-06T02:00:00Z",
                            "items": items})
        return default_handler(req)
    return handler


def _svc(base: dict, **over) -> dict:
    return {**base, **over}


class ApiStatusTests(unittest.TestCase):
    def setUp(self):
        health._cache = (0.0, False)
        self.store = FakeAccounts()
        self.store.add_user("alice", "alice-password-1", "user")
        self._ctx = install(self.store)
        self._ctx.__enter__()
        self.client = TestClient(app, follow_redirects=False, base_url="http://127.0.0.1",
                                 cookies=session_cookies("alice"))
        self._db = mock.patch.object(health.deps, "SessionFactory", lambda: _FakeSession())
        self._db.start()

    def tearDown(self):
        self._db.stop()
        self._ctx.__exit__(None, None, None)
        health._cache = (0.0, False)

    def _get(self, handler=default_handler):
        with FakeOpsAgent(handler) as agent, agent.installed():
            r = self.client.get("/api/status")
        return r, agent

    def _assert_shape(self, r, status: str):
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(set(body), {"status", "message"})
        self.assertEqual(body["status"], status)
        self.assertTrue(body["message"])
        for leak in _LEAKS:
            self.assertNotIn(leak, r.text)
        self.assertEqual(r.headers.get("cache-control"), "no-store")

    def test_requires_login(self):
        r = TestClient(app, follow_redirects=False, base_url="http://127.0.0.1").get("/api/status")
        self.assertEqual(r.status_code, 401)

    def test_regular_user_sees_ok_without_details(self):
        r, agent = self._get()
        self._assert_shape(r, "ok")
        self.assertEqual(r.json()["message"], "系統運作正常")
        self.assertEqual(agent.requests[0]["op"], "list")

    def test_critical_failed_is_degraded(self):
        r, _ = self._get(_list_handler([STATUS, _svc(POSTGRES, summary="failed")]))
        self._assert_shape(r, "degraded")

    def test_critical_idle_or_missing_is_degraded(self):
        for summary in ("idle", "not_found"):
            with self.subTest(summary=summary):
                health._STATUS_CACHE.clear()
                r, _ = self._get(_list_handler([_svc(STATUS, summary=summary), POSTGRES]))
                self._assert_shape(r, "degraded")

    def test_non_critical_failure_does_not_affect_status(self):
        sync = _svc(STATUS, name="sync", tier="important", summary="failed")
        backup = _svc(STATUS, name="backup", tier="supporting", summary="not_found")
        r, _ = self._get(_list_handler([STATUS, POSTGRES, sync, backup]))
        self._assert_shape(r, "ok")

    def test_transitioning_counts_as_ok_but_unknown_is_unknown(self):
        r, _ = self._get(_list_handler([_svc(STATUS, summary="transitioning"), POSTGRES]))
        self._assert_shape(r, "ok")
        health._STATUS_CACHE.clear()
        r, _ = self._get(_list_handler([_svc(STATUS, summary="unknown"), POSTGRES]))
        self._assert_shape(r, "unknown")

    def test_agent_unavailable_fails_open_to_unknown(self):
        missing = os.path.join(tempfile.gettempdir(), "rm-no-such-agent.sock")
        with mock.patch.object(ops_client, "default_client", lambda: ops_client.OpsClient(missing, "production")):
            r = self.client.get("/api/status")
        self._assert_shape(r, "unknown")
        self.assertEqual(r.json()["message"], "暫時無法取得完整的系統狀態")

    def test_agent_error_or_garbage_is_unknown(self):
        for handler in (lambda req: err(req, "busy", "fake-host 忙碌"), lambda req: "garbage"):
            with self.subTest(handler=handler):
                health._STATUS_CACHE.clear()
                r, _ = self._get(handler)
                self._assert_shape(r, "unknown")

    def test_no_critical_services_is_unknown(self):
        r, _ = self._get(_list_handler([]))
        self._assert_shape(r, "unknown")

    def test_db_down_is_degraded_and_skips_agent(self):
        with mock.patch.object(health.deps, "SessionFactory", lambda: _FakeSession(boom=True)):
            r, agent = self._get()
        self._assert_shape(r, "degraded")
        self.assertIn("資料庫", r.json()["message"])
        self.assertEqual(agent.requests, [])

    def test_agent_result_is_cached(self):
        with FakeOpsAgent() as agent, agent.installed():
            first = self.client.get("/api/status")
            second = self.client.get("/api/status")
        self._assert_shape(first, "ok")
        self._assert_shape(second, "ok")
        self.assertEqual(len(agent.requests), 1)

    def test_agent_timeout_is_capped(self):
        seen = []
        real = ops_client.OpsClient

        def factory():
            c = real("/nonexistent/agent.sock", "production", timeout=20.0)
            seen.append(c)
            return c

        with mock.patch.object(ops_client, "default_client", factory):
            r = self.client.get("/api/status")
        self._assert_shape(r, "unknown")
        self.assertEqual(seen[0].timeout, health._STATUS_AGENT_TIMEOUT)


if __name__ == "__main__":
    unittest.main()
