"""`/healthz/security`：安全事件的狀態型告警，只回答本機直連、只回一個鍵。

判斷本身（`security_ops.evaluate_alerts` 的 SQL）在 tests/test_security_ops_db.py；這裡守 HTTP 契約：
本機直連以外一律 404、回應只有 `security` 一鍵且不含任何計數或識別、告警狀態 503、判不出來 200 unknown、
逾時有界、結論快取、每次重算前順手落庫到期的限流彙總。不連 DB：evaluate_alerts 以假值替換。
"""
from __future__ import annotations

import asyncio
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from app.services import security_ops
from app.services.security_ops import AlertEvaluation, AuditChainSnapshot
from web.routers import health
from web.server import app


def _local() -> TestClient:
    return TestClient(app, follow_redirects=False, base_url="http://127.0.0.1", client=("127.0.0.1", 51000))


def _ev(**kw) -> AlertEvaluation:
    base = dict(window_minutes=15, login_failure_threshold=20, account_failure_threshold=5,
                elevate_failure_threshold=3, audit_chain=AuditChainSnapshot(state="ok"))
    base.update(kw)
    return AlertEvaluation(**base)


class HealthzSecurityTests(unittest.TestCase):
    def _get(self, ev: AlertEvaluation | None = None, client: TestClient | None = None, **patches):
        fake = mock.AsyncMock(return_value=ev or _ev())
        with mock.patch.object(security_ops, "evaluate_alerts", fake):
            return (client or _local()).get("/healthz/security"), fake

    def test_only_direct_loopback(self):
        for client, headers in (
            (TestClient(app, base_url="http://127.0.0.1", client=("203.0.113.9", 51000)), {}),
            (TestClient(app, base_url="http://research.example.com", client=("127.0.0.1", 51000)), {}),
            (_local(), {"X-Forwarded-For": "203.0.113.9"}),
            (_local(), {"X-Real-IP": "203.0.113.9"}),
        ):
            fake = mock.AsyncMock(return_value=_ev(login_failures=999))
            with mock.patch.object(security_ops, "evaluate_alerts", fake):
                r = client.get("/healthz/security", headers=headers)
            with self.subTest(headers=headers, base=str(client.base_url)):
                self.assertEqual(r.status_code, 404)
                self.assertNotIn("security", r.text)
                fake.assert_not_called()

    def test_ok_is_200_single_key(self):
        r, _ = self._get(_ev(login_failures=3))
        self.assertEqual((r.status_code, r.json()), (200, {"security": "ok"}))

    def test_each_alert_is_503_and_names_only_the_rule(self):
        cases = {
            "login_failures": _ev(login_failures=20),
            "account_failures": _ev(accounts_over=("00000000-0000-4000-8000-000000000001",)),
            "elevate_failures": _ev(elevate_failures=3),
            "audit_chain_broken": _ev(audit_chain=AuditChainSnapshot(state="broken", broken_count=2)),
        }
        for state, ev in cases.items():
            health._SECURITY_CACHE.clear()
            with self.subTest(state=state), self.assertLogs("web.routers.health", "WARNING") as logs:
                r, _ = self._get(ev)
                self.assertEqual((r.status_code, r.json()), (503, {"security": state}))
                self.assertNotIn("00000000-0000-4000-8000-000000000001", r.text)
                self.assertNotIn("00000000-0000-4000-8000-000000000001", "\n".join(logs.output),
                                 "日誌只記計數，不記帳號")

    def test_cannot_tell_is_200_unknown(self):
        r, _ = self._get(_ev(events_error="OSError"))
        self.assertEqual((r.status_code, r.json()), (200, {"security": "unknown"}))

    def test_exception_and_timeout_are_unknown(self):
        with mock.patch.object(security_ops, "evaluate_alerts", mock.AsyncMock(side_effect=RuntimeError("x"))), \
                self.assertLogs("web.routers.health", "WARNING"):
            r = _local().get("/healthz/security")
        self.assertEqual((r.status_code, r.json()), (200, {"security": "unknown"}))
        health._SECURITY_CACHE.clear()

        async def slow(*a, **kw):
            await asyncio.sleep(30)

        with mock.patch.object(security_ops, "evaluate_alerts", slow), \
                mock.patch.object(health, "_SECURITY_WAIT", 0.1), self.assertLogs("web.routers.health", "WARNING"):
            r = _local().get("/healthz/security")
        self.assertEqual(r.json(), {"security": "unknown"})

    def test_result_is_cached(self):
        fake = mock.AsyncMock(return_value=_ev())
        with mock.patch.object(security_ops, "evaluate_alerts", fake):
            for _ in range(5):
                self.assertEqual(_local().get("/healthz/security").status_code, 200)
        self.assertEqual(fake.await_count, 1)

    def test_due_tallies_are_flushed_before_evaluation(self):
        flush = mock.AsyncMock(return_value=0)
        with mock.patch.object(security_ops, "flush_tallies", flush):
            self._get()
        flush.assert_awaited_once()

    def test_response_vocabulary(self):
        self.assertEqual(set(security_ops.HEALTH_STATES),
                         {"ok", "unknown", "audit_chain_broken", "elevate_failures", "account_failures",
                          "login_failures"})


if __name__ == "__main__":
    unittest.main()
