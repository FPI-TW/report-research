"""進階 DB 與事件趨勢 API（`/api/admin/db/*`、`/api/admin/incidents/trends`，HTTP 層）。

SQL 對真 DB 的驗證在 tests/test_db_insights_db.py；這裡驗：未登入 401、一般使用者 403、沒有 `ops.read` 的管理員
403 `missing_scope`（服務層一次都沒被呼叫）；回應形狀（各段 `error` 可為 null、慢查詢不可用時 200＋
`available=false`）；趨勢的預設 7 天、範圍顛倒或超過 400 天 400、`table_bytes` 缺 `table` 400、粒度依起點選；
事件趨勢的預設 90 天、批次失敗率的 90 天窗期、`trends` 不被 `/api/admin/incidents/{incident_id}` 吃掉；
router 的 Literal 與服務層常數逐字一致（前端 zod 由這些 Literal 產生）。
"""

from __future__ import annotations

import dataclasses
import typing
import unittest
from datetime import datetime, timedelta, timezone

from fake_accounts import FakeAccounts, install
from fastapi.testclient import TestClient

from app.services import db_insights as real_di
from web import auth, deps
from web.routers import admin_db
from web.server import app

ADMIN_PW = "root-password-1"
USER_PW = "alice-password-1"
T0 = datetime(2026, 10, 6, 3, 0, tzinfo=timezone.utc)
DB_PATHS = ("/api/admin/db/overview", "/api/admin/db/slow-queries", "/api/admin/db/trends?metric=db_size_bytes",
            "/api/admin/incidents/trends")


def _client():
    return TestClient(app, follow_redirects=False, base_url="http://127.0.0.1")


class _NoOpsRead(FakeAccounts):
    def _user(self, row, elevated_until=None):
        user = super()._user(row, elevated_until)
        if user.username == "limited":
            return dataclasses.replace(user, scopes=user.scopes - {"ops.read"})
        return user


class _Session:
    def __init__(self):
        self.rolled_back = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def rollback(self):
        self.rolled_back += 1


def _err(code):
    return {"code": code, "message": "x"}


class _FakeInsights:
    TREND_METRICS = real_di.TREND_METRICS

    def __init__(self):
        self.calls: list = []
        self.slow = {"available": False, "reason": "not_preloaded", "message": "沒有預載", "stats_reset": None,
                     "items": []}

    async def collect_overview(self, session, **kw):
        self.calls.append(("overview", kw))
        return {
            "generated_at": T0.isoformat(), "statement_timeout_ms": 5000,
            "database": {"error": None, "name": "research", "size_bytes": 123, "server_version": "16.4"},
            "tables": {"error": None, "table_count": 1, "live_tuples": 9, "dead_tuples": 1, "dead_ratio": 0.1,
                       "limit": 50, "items": [{
                           "schema_name": "research", "table": "report_chunk", "total_bytes": 100,
                           "row_estimate": None, "live_tuples": 9, "dead_tuples": 1, "dead_ratio": 0.1,
                           "last_autovacuum": T0.isoformat(), "last_vacuum": None, "last_autoanalyze": None,
                           "last_analyze": None}]},
            "unused_indexes": {"error": _err("permission_denied")},
            "connections": {"error": None, "max_connections": 100, "reserved_connections": 3,
                            "usable_connections": 97, "total": 4, "this_database": 3, "hidden": 1,
                            "by_state": {"active": 1, "idle": 2}, "usage_ratio": 4 / 97},
            "activity": {"error": _err("timeout")},
        }

    async def slow_queries(self, session, **kw):
        self.calls.append(("slow", kw))
        return self.slow

    def pick_granularity(self, since, now):
        self.calls.append(("pick", since, now))
        return real_di.pick_granularity(since, now, hourly_days=30)

    async def trend_points(self, session, **kw):
        self.calls.append(("trend", kw))
        return [{"t": T0.isoformat(), "value": 1.5, "min": None, "max": None}]

    async def latest_tables(self, session):
        return ["research.report_chunk"]

    async def incident_trends(self, session, **kw):
        self.calls.append(("incidents", kw))
        return {
            "weeks": [{"week_start": "2026-09-28", "component": "web", "severity": "CRITICAL", "total": 2,
                       "resolved": 1, "lost": 1, "firing": 0}],
            "summary": {"total": 2, "resolved": 1, "lost": 1, "firing": 0, "critical": 2, "warning": 0,
                        "mttr_seconds": 600.0, "p50_seconds": 600.0, "p90_seconds": 600.0},
            "by_component": [{"component": "web", "total": 2, "resolved": 1, "lost": 1, "firing": 0, "critical": 2,
                              "warning": 0, "mttr_seconds": 600.0, "p50_seconds": 600.0, "p90_seconds": 600.0}],
            "top_reasons": [{"reason": "probe_exit_1", "total": 2, "components": ["web"]}],
        }

    async def job_failure_rates(self, session, **kw):
        self.calls.append(("jobs", kw))
        return [{"unit": "report-mark-sync.service", "runs": 8, "finished": 8, "failed": 1, "lost": 0, "running": 0,
                 "failure_rate": 0.125, "last_failure_at": T0.isoformat(), "last_started_at": T0.isoformat()}]


class _FakeMonitoring:
    def __init__(self):
        self.calls: list = []

    async def get_incident(self, session, incident_id):
        self.calls.append(incident_id)
        return None, [], False


class AdminDbApiTests(unittest.TestCase):
    def setUp(self):
        auth._FAILS.clear()
        self.store = _NoOpsRead()
        self.store.add_user("root", ADMIN_PW, "admin")
        self.store.add_user("alice", USER_PW, "user")
        self.store.add_user("limited", ADMIN_PW, "admin")
        self._ctx = install(self.store)
        self._ctx.__enter__()
        self.fake = _FakeInsights()
        self.mon = _FakeMonitoring()
        self.session = _Session()
        self._orig = (deps.db_insights, deps.ops_monitoring, deps.SessionFactory)
        deps.db_insights = self.fake
        deps.ops_monitoring = self.mon
        deps.SessionFactory = lambda: self.session

    def tearDown(self):
        deps.db_insights, deps.ops_monitoring, deps.SessionFactory = self._orig
        self._ctx.__exit__(None, None, None)
        auth._FAILS.clear()

    def _login(self, username, password):
        client = _client()
        r = client.post("/login", data={"username": username, "password": password})
        self.assertEqual(r.status_code, 303)
        return client

    # ── 權限 ──
    def test_unauthenticated_401_and_user_403(self):
        user = self._login("alice", USER_PW)
        for path in DB_PATHS:
            with self.subTest(path=path):
                self.assertEqual(_client().get(path).status_code, 401)
                self.assertEqual(user.get(path).status_code, 403)
        self.assertEqual(self.fake.calls, [])

    def test_admin_without_ops_read_gets_missing_scope(self):
        admin = self._login("limited", ADMIN_PW)
        for path in DB_PATHS:
            with self.subTest(path=path):
                r = admin.get(path)
                self.assertEqual(r.status_code, 403)
                self.assertEqual(r.json()["code"], "missing_scope")
        self.assertEqual(self.fake.calls, [])

    # ── 即時快照 ──
    def test_overview_shape_with_degraded_sections(self):
        r = self._login("root", ADMIN_PW).get("/api/admin/db/overview")
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertIsNone(body["database"]["error"])
        self.assertEqual(body["unused_indexes"]["error"]["code"], "permission_denied")
        self.assertEqual(body["unused_indexes"]["items"], [])
        self.assertEqual(body["activity"]["error"]["code"], "timeout")
        self.assertIsNone(body["activity"]["cache_hit_ratio"])
        self.assertEqual(body["connections"]["by_state"], {"active": 1, "idle": 2})
        self.assertEqual(body["tables"]["items"][0]["table"], "report_chunk")
        self.assertGreaterEqual(self.session.rolled_back, 1, "唯讀：結束時 rollback，SET LOCAL 隨之還原")

    # ── 慢查詢 ──
    def test_slow_queries_unavailable_is_200(self):
        r = self._login("root", ADMIN_PW).get("/api/admin/db/slow-queries", params={"limit": 5, "sort": "calls"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((r.json()["available"], r.json()["reason"]), (False, "not_preloaded"))
        self.assertEqual(self.fake.calls[-1], ("slow", {"limit": 5, "sort": "calls"}))

    def test_slow_queries_available_shape(self):
        self.fake.slow = {"available": True, "reason": None, "message": None, "stats_reset": None,
                          "extension_version": "1.10", "sort": "total", "limit": 20, "hidden_count": 1,
                          "items": [{"queryid": "1", "query": None, "query_truncated": False, "query_hidden": True,
                                     "calls": 1, "total_ms": 2.0, "mean_ms": 2.0, "rows": 0,
                                     "cache_hit_ratio": None, "temp_blks_written": 0}]}
        r = self._login("root", ADMIN_PW).get("/api/admin/db/slow-queries")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["items"][0]["query_hidden"], True)
        self.assertEqual(self.fake.calls[-1], ("slow", {"limit": real_di.SLOW_QUERY_DEFAULT_LIMIT, "sort": "total"}))

    def test_slow_queries_reject_bad_params(self):
        admin = self._login("root", ADMIN_PW)
        for params in ({"limit": 0}, {"limit": real_di.SLOW_QUERY_MAX_LIMIT + 1}, {"sort": "query"}):
            with self.subTest(params=params):
                self.assertEqual(admin.get("/api/admin/db/slow-queries", params=params).status_code, 422)
        self.assertEqual(self.fake.calls, [])

    # ── 趨勢 ──
    def test_trends_default_window_and_hourly(self):
        before = datetime.now(timezone.utc)
        r = self._login("root", ADMIN_PW).get("/api/admin/db/trends", params={"metric": "temp_bytes"})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual((body["metric"], body["kind"], body["granularity"], body["table"]),
                         ("temp_bytes", "rate", "hour", None))
        self.assertEqual(body["points"][0]["value"], 1.5)
        self.assertEqual(body["tables"], ["research.report_chunk"])
        kw = next(c[1] for c in self.fake.calls if c[0] == "trend")
        self.assertEqual(kw["until"] - kw["since"], timedelta(days=7))
        self.assertGreaterEqual(kw["until"], before)
        self.assertIsNone(kw["table"])

    def test_trends_old_range_uses_daily_and_table_passthrough(self):
        r = self._login("root", ADMIN_PW).get("/api/admin/db/trends", params={
            "metric": "table_bytes", "table": "research.report_chunk",
            "since": (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((r.json()["granularity"], r.json()["table"]), ("day", "research.report_chunk"))
        kw = next(c[1] for c in self.fake.calls if c[0] == "trend")
        self.assertEqual((kw["granularity"], kw["table"]), ("day", "research.report_chunk"))

    def test_trends_reject_bad_params(self):
        admin = self._login("root", ADMIN_PW)
        cases = [
            ({}, 422),
            ({"metric": "rows"}, 422),
            ({"metric": "table_bytes"}, 400),
            ({"metric": "table_bytes", "table": "no_schema"}, 422),
            ({"metric": "table_bytes", "table": "a.b; drop"}, 422),
            ({"metric": "deadlocks", "since": "2026-10-06T10:00:00+00:00", "until": "2026-10-06T09:00:00+00:00"},
             400),
            ({"metric": "deadlocks", "since": "2025-01-01T00:00:00+00:00", "until": "2026-10-06T00:00:00+00:00"},
             400),
        ]
        for params, status in cases:
            with self.subTest(params=params):
                r = admin.get("/api/admin/db/trends", params=params)
                self.assertEqual(r.status_code, status, r.text)
                if status == 400:
                    self.assertEqual(r.json()["code"], "invalid_params")
        self.assertFalse([c for c in self.fake.calls if c[0] == "trend"])

    # ── 事件趨勢 ──
    def test_incident_trends_shape_and_windows(self):
        r = self._login("root", ADMIN_PW).get("/api/admin/incidents/trends")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.mon.calls, [], "trends 不得被當成事件 id")
        body = r.json()
        self.assertEqual(body["summary"]["mttr_seconds"], 600.0)
        self.assertEqual(body["weeks"][0]["severity"], "CRITICAL")
        self.assertEqual(body["jobs"][0]["failure_rate"], 0.125)
        inc = next(c[1] for c in self.fake.calls if c[0] == "incidents")
        jobs = next(c[1] for c in self.fake.calls if c[0] == "jobs")
        self.assertEqual(inc["until"] - inc["since"], timedelta(days=90))
        self.assertEqual(jobs["until"], inc["until"])
        self.assertEqual(jobs["until"] - jobs["since"], timedelta(days=90))
        self.assertEqual(body["jobs_since"], jobs["since"].isoformat())

    def test_incident_trends_reject_bad_window_and_detail_still_routes(self):
        admin = self._login("root", ADMIN_PW)
        r = admin.get("/api/admin/incidents/trends", params={"since": "2024-01-01T00:00:00+00:00",
                                                             "until": "2026-10-06T00:00:00+00:00"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["code"], "invalid_params")
        self.assertEqual(admin.get("/api/admin/incidents/office-host:web:404").status_code, 404)
        self.assertEqual(self.mon.calls, ["office-host:web:404"])


class LiteralContractTests(unittest.TestCase):
    def test_router_literals_match_service_constants(self):
        self.assertEqual(set(typing.get_args(admin_db.SectionErrorCode)), set(real_di.ERROR_CODES))
        self.assertEqual(set(typing.get_args(admin_db.SlowQueryReason)), set(real_di.SLOW_QUERY_REASONS))
        self.assertEqual(set(typing.get_args(admin_db.SlowQuerySort)), set(real_di.SLOW_QUERY_SORTS))
        self.assertEqual(set(typing.get_args(admin_db.TrendMetric)), set(real_di.TREND_METRICS))
        self.assertEqual(set(typing.get_args(admin_db.TrendKind)), set(real_di.TREND_METRICS.values()))


if __name__ == "__main__":
    unittest.main()
