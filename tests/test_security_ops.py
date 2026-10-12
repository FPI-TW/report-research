"""`app/services/security_ops.py` 的純邏輯（不連 DB）：fail-open 記錄、限流彙總、告警狀態的優先序、
稽核鏈快取、錨定狀態檔、高風險分類。SQL 對真的 PostgreSQL 成立與否在 `tests/test_security_ops_db.py`。
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from app.services import accounts, security_ops
from app.services.security_ops import AlertEvaluation, AuditChainSnapshot, ThrottledTally


class _Api:
    def __init__(self, fail: Exception | None = None, delay: float = 0.0):
        self.calls: list[tuple[str, dict]] = []
        self.fail = fail
        self.delay = delay

    async def record_auth_event(self, event, **kw):
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail is not None:
            raise self.fail
        self.calls.append((event, kw))


class RecordEventTests(unittest.IsolatedAsyncioTestCase):
    async def test_success_returns_true(self):
        api = _Api()
        self.assertTrue(await security_ops.record_event(api, "logout", session_id="s"))
        self.assertEqual(api.calls, [("logout", {"session_id": "s"})])

    async def test_db_error_is_swallowed(self):
        api = _Api(fail=RuntimeError("db down"))
        with self.assertLogs("app.services.security_ops", "WARNING"):
            self.assertFalse(await security_ops.record_event(api, "logout"))

    async def test_hang_is_bounded(self):
        api = _Api(delay=5)
        with mock.patch.object(security_ops, "RECORD_TIMEOUT_SECONDS", 0.05), \
                self.assertLogs("app.services.security_ops", "WARNING"):
            self.assertFalse(await security_ops.record_event(api, "logout"))


class ThrottledTallyTests(unittest.IsolatedAsyncioTestCase):
    def test_counts_per_ip_and_only_due_after_interval(self):
        now = [100.0]
        t = ThrottledTally("login.locked", flush_seconds=60, clock=lambda: now[0])
        for _ in range(3):
            t.note("1.1.1.1")
        t.note("2.2.2.2")
        self.assertFalse(t.due())
        now[0] = 159.9
        self.assertFalse(t.due())
        now[0] = 160.0
        self.assertTrue(t.due())
        self.assertEqual(t.drain(), [("1.1.1.1", 3), ("2.2.2.2", 1)])
        self.assertFalse(t.due())
        self.assertEqual(t.pending, 0)

    def test_key_cap_folds_into_null_ip_without_losing_counts(self):
        t = ThrottledTally("login.locked", max_keys=2)
        for ip in ("a", "b", "c", "d", "a"):
            t.note(ip)
        self.assertEqual(t.pending, 5)
        self.assertEqual(dict(t.drain()), {"a": 2, "b": 1, None: 2})

    async def test_flood_writes_one_aggregated_row_per_ip(self):
        """被攻擊時的寫入放大守門：1000 次被限流的請求在 60 秒內只寫 0 列，到期後每 IP 一列。"""
        now = [0.0]
        api = _Api()
        tally = ThrottledTally("login.locked", flush_seconds=60, clock=lambda: now[0])
        with mock.patch.object(security_ops, "_TALLIES", (tally,)):
            for i in range(1000):
                now[0] = i * 0.05  # 50 秒內
                await security_ops.note_throttled(api, tally, "9.9.9.9")
            self.assertEqual(api.calls, [])
            now[0] = 61.0
            await security_ops.note_throttled(api, tally, "9.9.9.9")
        self.assertEqual(api.calls, [("login.locked", {"ip": "9.9.9.9", "count": 1001})])

    async def test_force_flush_and_failure_does_not_raise(self):
        api = _Api(fail=RuntimeError("db down"))
        tally = ThrottledTally("login.insecure")
        tally.note("3.3.3.3")
        with mock.patch.object(security_ops, "_TALLIES", (tally,)), \
                self.assertLogs("app.services.security_ops", "WARNING"):
            self.assertEqual(await security_ops.flush_tallies(api, force=True), 0)
        self.assertEqual(tally.pending, 0)


def _eval(**kw) -> AlertEvaluation:
    base = dict(window_minutes=15, login_failure_threshold=20, account_failure_threshold=5,
                elevate_failure_threshold=3, audit_chain=AuditChainSnapshot(state="ok"))
    base.update(kw)
    return AlertEvaluation(**base)


class AlertStateTests(unittest.TestCase):
    def test_quiet_is_ok(self):
        self.assertEqual(_eval(login_failures=19, elevate_failures=2).state, "ok")

    def test_each_threshold(self):
        self.assertEqual(_eval(login_failures=20).state, "login_failures")
        self.assertEqual(_eval(accounts_over=("u",)).state, "account_failures")
        self.assertEqual(_eval(elevate_failures=3).state, "elevate_failures")
        self.assertEqual(_eval(audit_chain=AuditChainSnapshot(state="broken")).state, "audit_chain_broken")

    def test_priority_when_several_hold(self):
        ev = _eval(login_failures=99, accounts_over=("u",), elevate_failures=9,
                   audit_chain=AuditChainSnapshot(state="broken"))
        self.assertEqual(ev.triggered, security_ops.ALERT_STATES)
        self.assertEqual(ev.state, "audit_chain_broken")
        self.assertEqual(_eval(login_failures=99, elevate_failures=9).state, "elevate_failures")

    def test_cannot_tell_is_unknown_not_ok(self):
        self.assertEqual(_eval(events_error="OSError").state, "unknown")
        self.assertEqual(_eval(audit_chain=AuditChainSnapshot(state="error")).state, "unknown")
        # 事件查不到時，稽核鏈斷了仍要說出來
        self.assertEqual(_eval(events_error="OSError", audit_chain=AuditChainSnapshot(state="broken")).state,
                         "audit_chain_broken")

    def test_health_states_vocabulary(self):
        self.assertEqual(set(security_ops.HEALTH_STATES), {"ok", "unknown", *security_ops.ALERT_STATES})


class EvaluateAlertsFailOpenTests(unittest.IsolatedAsyncioTestCase):
    async def test_db_failure_is_unknown(self):
        def factory():
            raise OSError("connection refused")

        async def verify():
            return accounts.AuditChainStatus(ok=True, total=1, head_id=1, head_hash="h")

        with self.assertLogs("app.services.security_ops", "WARNING"):
            ev = await security_ops.evaluate_alerts(verify, session_factory=factory)
        self.assertEqual((ev.state, ev.events_error), ("unknown", "OSError"))


class AuditChainCacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_result_is_cached_for_max_age(self):
        calls = []
        now = [0.0]

        async def verify():
            calls.append(1)
            return accounts.AuditChainStatus(ok=False, total=10, head_id=10, head_hash="h", broken_ids=(3, 4))

        snap = await security_ops.audit_chain_snapshot(verify, max_age=3600, clock=lambda: now[0])
        self.assertEqual((snap.state, snap.total, snap.broken_count), ("broken", 10, 2))
        now[0] = 3599
        await security_ops.audit_chain_snapshot(verify, max_age=3600, clock=lambda: now[0])
        self.assertEqual(len(calls), 1)
        now[0] = 3600
        await security_ops.audit_chain_snapshot(verify, max_age=3600, clock=lambda: now[0])
        self.assertEqual(len(calls), 2)

    async def test_error_is_cached_only_briefly(self):
        calls = []
        now = [0.0]

        async def verify():
            calls.append(1)
            raise OSError("db down")

        with self.assertLogs("app.services.security_ops", "WARNING"):
            snap = await security_ops.audit_chain_snapshot(verify, max_age=3600, clock=lambda: now[0])
        self.assertEqual((snap.state, snap.error), ("error", "OSError"))
        now[0] = 61
        with self.assertLogs("app.services.security_ops", "WARNING"):
            await security_ops.audit_chain_snapshot(verify, max_age=3600, clock=lambda: now[0])
        self.assertEqual(len(calls), 2)

    async def test_reset_clears_cache(self):
        calls = []

        async def verify():
            calls.append(1)
            return accounts.AuditChainStatus(ok=True, total=0, head_id=None, head_hash=None)

        await security_ops.audit_chain_snapshot(verify)
        security_ops.reset()
        await security_ops.audit_chain_snapshot(verify)
        self.assertEqual(len(calls), 2)


class AnchorStatusFileTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"DATA_HEALTH_DIR": str(Path(self._tmp.name) / "health")})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_missing(self):
        self.assertEqual(security_ops.read_anchor_status().state, "missing")

    def test_roundtrip_and_staleness(self):
        t0 = datetime(2026, 10, 7, 4, 15, tzinfo=timezone.utc)
        self.assertTrue(security_ops.write_anchor_status(
            {"result": "ok", "at": t0.isoformat(), "head_id": 42, "total": 42, "anchors_checked": 3}, now=t0))
        path = Path(self._tmp.name) / "health" / "audit_anchor.json"
        self.assertTrue(path.is_file())
        st = security_ops.read_anchor_status(now=t0 + timedelta(hours=1))
        self.assertEqual((st.state, st.head_id, st.total, st.anchors_checked, st.age_hours), ("ok", 42, 42, 3, 1.0))
        self.assertEqual(security_ops.read_anchor_status(now=t0 + timedelta(hours=49)).state, "stale")

    def test_tamper_is_not_masked_by_staleness(self):
        t0 = datetime(2026, 10, 1, tzinfo=timezone.utc)
        security_ops.write_anchor_status({"result": "tamper", "message": "x"}, now=t0)
        self.assertEqual(security_ops.read_anchor_status(now=t0 + timedelta(days=9)).state, "tamper")

    def test_corrupt_shapes(self):
        path = Path(self._tmp.name) / "health" / "audit_anchor.json"
        path.parent.mkdir(parents=True)
        path.write_text("{not json", encoding="utf-8")
        self.assertEqual(security_ops.read_anchor_status().state, "corrupt")
        path.write_text(json.dumps({"name": "audit_anchor", "result": "maybe"}), encoding="utf-8")
        self.assertEqual(security_ops.read_anchor_status().state, "corrupt")

    def test_write_failure_does_not_raise(self):
        with mock.patch.dict(os.environ, {"DATA_HEALTH_DIR": "/proc/forbidden/report-mark"}):
            self.assertFalse(security_ops.write_anchor_status({"result": "ok"}))


class HighRiskClassificationTests(unittest.TestCase):
    def test_design_list_is_covered(self):
        for action in ("user.set_privileges", "user.set_role", "session.elevate", "session.elevate_failed",
                       "user.reset_password", "user.totp_reset", "user.delete_requested", "user.delete_executed",
                       "ops.restart", "ops.run", "qa_content.read", "data.export", "flag.update", "quota.update",
                       "session.admin_revoke"):
            with self.subTest(action=action):
                self.assertIsNotNone(security_ops.classify_action(action))

    def test_routine_actions_are_not_high_risk(self):
        for action in ("review.update", "report.hide", "upload.create", "user.create", "user.enable", "opsx"):
            with self.subTest(action=action):
                self.assertIsNone(security_ops.classify_action(action))

    def test_sql_filter_matches_python_classifier(self):
        exact, likes = security_ops._action_filter(security_ops.HIGH_RISK_CATEGORIES)
        self.assertIn("ops.%", likes)
        self.assertEqual(len(exact), len(set(exact)))

    def test_categories_are_disjoint(self):
        seen: dict[str, str] = {}
        for cat, patterns in security_ops.HIGH_RISK_ACTIONS.items():
            for p in patterns:
                self.assertNotIn(p, seen, f"{p} 同時在 {seen.get(p)} 與 {cat}")
                seen[p] = cat


class PurgeFloorTests(unittest.IsolatedAsyncioTestCase):
    async def test_auth_event_floor_is_365_even_if_settings_say_less(self):
        seen = []

        class _Res:
            rowcount = 0

        class _S:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def execute(self, sql, params):
                seen.append((str(sql), dict(params)))
                return _Res()

            async def commit(self):
                pass

        res = await security_ops.purge_expired(
            settings=SimpleNamespace(auth_event_retention_days=30, session_expired_retention_days=90),
            session_factory=_S,
        )
        self.assertEqual((res.auth_event_days, res.session_days), (365, 90))
        auth_sql = [p for s, p in seen if "auth_event" in s]
        self.assertEqual(auth_sql[0]["days"], 365)


if __name__ == "__main__":
    unittest.main()
