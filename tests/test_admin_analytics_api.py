"""使用分析的管理端點（`/api/admin/analytics/*`，HTTP 層）。

計算與 SQL 在 tests/test_analytics.py、tests/test_analytics_db.py；這裡把服務層換成假物件，驗：
- 未登入 401、一般使用者 403、沒有 `analytics.read` 的管理員 403 `missing_scope`（服務層一次都沒被呼叫）。
- 預設範圍與參數轉給服務層；範圍錯誤 400 `invalid_params`、limit 越界與日期格式錯誤 422。
- DB 失敗 503 `db_unavailable`；回應的 `suppressed` 標示原樣保留；60 秒快取。
"""

from __future__ import annotations

import dataclasses
import unittest
from datetime import date
from unittest import mock

from fake_accounts import FakeAccounts, install
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.services import analytics
from web import auth, deps
from web.routers import admin_analytics
from web.server import app

ADMIN_PW = "root-password-1"
USER_PW = "alice-password-1"
TODAY = date(2026, 10, 7)


def _client():
    return TestClient(app, follow_redirects=False, base_url="http://127.0.0.1")


class _NoAnalyticsRead(FakeAccounts):
    """`limited` 是管理員但沒有 analytics.read（其他 scope 照常）。"""

    def _user(self, row, elevated_until=None):
        user = super()._user(row, elevated_until)
        if user.username == "limited":
            return dataclasses.replace(user, scopes=user.scopes - {"analytics.read"})
        return user


class _Session:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _range(plan, params):
    return analytics.range_info(plan, params)


PATHS = ("/api/admin/analytics/overview", "/api/admin/analytics/top", "/api/admin/analytics/routes",
         "/api/admin/analytics/quality", "/api/admin/analytics/operations")


class AnalyticsApiTests(unittest.TestCase):
    def setUp(self):
        auth._FAILS.clear()
        admin_analytics.reset_caches()
        self.store = _NoAnalyticsRead()
        self.store.add_user("root", ADMIN_PW, "admin")
        self.store.add_user("alice", USER_PW, "user")
        self.store.add_user("limited", ADMIN_PW, "admin")
        self._ctx = install(self.store)
        self._ctx.__enter__()
        self.calls: list = []
        self._orig_sf = deps.SessionFactory
        deps.SessionFactory = lambda: _Session()
        self._patches = [
            mock.patch.object(analytics, "today", lambda: TODAY),
            mock.patch.object(analytics, "overview", self._fake("overview", self._overview)),
            mock.patch.object(analytics, "top", self._fake("top", self._top)),
            mock.patch.object(analytics, "routes", self._fake("routes", self._routes)),
            mock.patch.object(analytics, "quality", self._fake("quality", self._quality)),
            mock.patch.object(analytics, "operations", self._fake("operations", self._operations)),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        deps.SessionFactory = self._orig_sf
        self._ctx.__exit__(None, None, None)
        auth._FAILS.clear()
        admin_analytics.reset_caches()

    def _fake(self, name, build):
        async def fn(session, plan, params, *extra):
            self.calls.append((name, plan, params, extra))
            return build(plan, params, *extra)
        return fn

    @staticmethod
    def _overview(plan, params):
        return {"range": _range(plan, params),
                "totals": {"questions": 3, "stopped": 0, "reading": 1, "report_file": 0, "search": 2,
                           "active_users_peak": 2, "distinct_users_live": 2, "missing_days": 0},
                "latency": {"p50_ms": 10.0, "p95_ms": 20.0, "thinking_p50_ms": None, "thinking_p95_ms": None, "n": 3},
                "daily": [{"day": plan.until.isoformat(), "source": "live", "has_data": True, "questions": 3,
                           "askers": 2, "active_users": 2, "reading": 1, "report_file": 0, "search": 2}]}

    @staticmethod
    def _top(plan, params, limit):
        hidden = analytics.make_cell("US", 9, 2, k=params.min_users, suppressible=True)
        shown = analytics.make_cell("TW", 9, 3, k=params.min_users, suppressible=True, label="台股")
        empty = {"cells": [], "suppressed_count": 0, "complementary_count": 0, "truncated": False}
        return {"range": _range(plan, params), "limit": limit, "targets": {**empty, "suppressed_count": 4},
                "reports": empty, "markets": {"cells": [shown, hidden], "suppressed_count": 1, "complementary_count": 0,
                            "truncated": False},
                "reading": empty, "report_file": empty, "search_markets": empty}

    @staticmethod
    def _routes(plan, params):
        return {"range": _range(plan, params), "questions": 0, "stopped": 0, "llm_truncated": 0,
                "invalid_citation_rows": 0, "invalid_citations": 0,
                "distributions": [{"name": n, "suppressible": n == "path", "cells": [], "suppressed_count": 0,
                                   "complementary_count": 0} for n in analytics.ROUTE_KEYS]}

    @staticmethod
    def _quality(plan, params):
        return {"range": _range(plan, params), "judge_model": params.judge_model,
                "faithfulness_min": params.faithfulness_min, "other_judge_checked": 0, "weeks": []}

    @staticmethod
    def _operations(plan, params):
        return {"range": _range(plan, params), "weeks": [], "audit_actions": [{"action": "review.update", "count": 1}]}

    def _login(self, username, password):
        client = _client()
        r = client.post("/login", data={"username": username, "password": password})
        self.assertEqual(r.status_code, 303)
        return client

    def test_unauthenticated_401_user_403_admin_without_scope_403(self):
        user = self._login("alice", USER_PW)
        limited = self._login("limited", ADMIN_PW)
        for path in PATHS:
            with self.subTest(path=path):
                self.assertEqual(_client().get(path).status_code, 401)
                self.assertEqual(user.get(path).status_code, 403)
                r = limited.get(path)
                self.assertEqual((r.status_code, r.json()["code"]), (403, "missing_scope"))
        self.assertEqual(self.calls, [])

    def test_admin_gets_every_endpoint_with_default_range(self):
        admin = self._login("root", ADMIN_PW)
        for path in PATHS:
            with self.subTest(path=path):
                r = admin.get(path)
                self.assertEqual(r.status_code, 200, r.text)
                body = r.json()
                self.assertEqual((body["range"]["since"], body["range"]["until"]), ("2026-09-08", "2026-10-07"))
                self.assertEqual(body["range"]["spans"], [{"since": "2026-09-08", "until": "2026-10-07",
                                                           "source": "live"}])
        self.assertEqual([c[0] for c in self.calls], ["overview", "top", "routes", "quality", "operations"])
        params = self.calls[0][2]
        self.assertEqual(params.min_users, 3)

    def test_top_passes_limit_and_marks_suppressed(self):
        r = self._login("root", ADMIN_PW).get("/api/admin/analytics/top", params={
            "since": "2026-01-01", "until": "2026-10-07", "limit": 5})
        self.assertEqual(r.status_code, 200, r.text)
        _name, plan, _params, extra = self.calls[0]
        self.assertEqual((plan.since, plan.until, extra), (date(2026, 1, 1), TODAY, (5,)))
        body = r.json()
        self.assertEqual([s["source"] for s in body["range"]["spans"]], ["rollup", "live"])
        self.assertEqual(body["markets"]["cells"][1], {"key": "US", "label": None, "value": None, "users": None,
                                                       "suppressed": True, "suppression_reason": "min_users"})
        self.assertEqual(body["targets"]["suppressed_count"], 4)

    def test_bad_params(self):
        admin = self._login("root", ADMIN_PW)
        cases = [
            ("/api/admin/analytics/overview", {"since": "2026-10-07", "until": "2026-10-01"}, 400),
            ("/api/admin/analytics/overview", {"since": "2024-01-01", "until": "2026-10-07"}, 400),
            ("/api/admin/analytics/overview", {"since": "yesterday"}, 422),
            ("/api/admin/analytics/top", {"limit": 0}, 422),
            ("/api/admin/analytics/top", {"limit": 101}, 422),
        ]
        for path, params, status in cases:
            with self.subTest(params=params):
                r = admin.get(path, params=params)
                self.assertEqual(r.status_code, status, r.text)
                if status == 400:
                    self.assertEqual(r.json()["code"], "invalid_params")
        self.assertEqual(self.calls, [])

    def test_db_failure_is_503_and_responses_are_cached(self):
        admin = self._login("root", ADMIN_PW)
        self.assertEqual(admin.get("/api/admin/analytics/routes").status_code, 200)
        self.assertEqual(admin.get("/api/admin/analytics/routes").status_code, 200)
        self.assertEqual(len(self.calls), 1)   # 第二次命中快取

        async def boom(session, plan, params):
            raise OperationalError("SELECT 1", {}, ConnectionRefusedError("refused"))

        with mock.patch.object(analytics, "quality", boom):
            r = admin.get("/api/admin/analytics/quality")
        self.assertEqual((r.status_code, r.json()["code"]), (503, "db_unavailable"))



if __name__ == "__main__":
    unittest.main()
