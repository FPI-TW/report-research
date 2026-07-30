# tests/test_monitor_http.py
"""監控端點的 **HTTP 層**測試（不是直接呼叫函式物件）。

2026-07-28 事故：在 `@router.get("/api/progress")` 與 handler 之間插了一支輔助函式，
裝飾器因此套到輔助函式上，FastAPI 把它的 `done`/`total`/`latest` 參數當成 query
參數 → `/api/progress` 對正常請求回 **422**，監控頁全壞。

**既有測試全綠**：`test_server_stats` 直接呼叫 `monitor.progress()` 函式物件，
繞過路由層，看不到裝飾器套錯。這是本專案記過的同一種縫（M3：per-task 審查看不到
HTTP 端點縫）。本檔專門守這條縫——凡是端點契約（路由存在、參數形狀、回應結構），
一律經 TestClient 走真實 HTTP。
"""
import os
import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("REPORT_MARK_ACCESS_USERNAME", "tester")
os.environ.setdefault("REPORT_MARK_ACCESS_PASSWORD", "testpass")
os.environ.setdefault("REPORT_MARK_SESSION_SECRET", "fixed-test-secret-0123456789")

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from fastapi.testclient import TestClient  # noqa: E402
from web import auth, deps  # noqa: E402
from web.routers import monitor  # noqa: E402
from web.server import app  # noqa: E402

_SNAPSHOT = {
    "total_reports": 6,
    "total_chunks": 12,
    "markets": [{"market": "TW", "count": 6}],
    "instrument_types": [{"type": "equity", "count": 5}],
    "report_types": [{"type": "daily", "count": 4}],
    "summary_done": 3,
    "summary_total": 7,
    "takeaway_done_30d": 4,
    "takeaway_total_30d": 10,
    "takeaway_latest": date(2026, 7, 20),
    "signal_done_30d": 1,
    "signal_total_30d": 10,
    "signal_latest": date(2026, 7, 16),
    # M8 查核統計。handler 直接透傳這一塊，形狀由 _fetch_db_stats_snapshot 決定。
    "evaluation": {
        "qa": {"total": 40, "checked": 3, "degraded": 1, "below_min": 1,
               "avg_score": 0.5634, "latest": "2026-07-28"},
        "report": None,
        "min_score": 0.9,
    },
}


def _authed() -> TestClient:
    c = TestClient(app, follow_redirects=False, base_url="http://127.0.0.1")
    r = c.post("/login", data={"username": "tester", "password": "testpass"})
    assert r.status_code == 303, r.status_code
    return c


class ProgressHttpTests(unittest.TestCase):
    """`/api/progress` 必須以**無 query 參數**的 GET 回 200。"""

    def _get(self):
        async def fake_snapshot():
            # _db_stats_snapshot 已把 date 轉字串,此處比照 handler 實際拿到的形狀
            d = dict(_SNAPSHOT)
            d["takeaway_latest"] = "2026-07-20"
            d["signal_latest"] = "2026-07-16"
            return d

        with patch.object(monitor, "_db_stats_snapshot", fake_snapshot), \
             patch.object(monitor, "_gather_runtime", lambda: {
                 "tagging": None, "ingest": None,
                 "pipelines": {"web": True}, "orchestrator": None,
             }):
            return _authed().get("/api/progress")

    def test_no_query_params_required(self):
        """回歸守門：裝飾器若套到輔助函式上，這裡會是 422（實際事故的症狀）。"""
        r = self._get()
        self.assertEqual(r.status_code, 200, r.text[:300])

    def test_response_shape(self):
        body = self._get().json()
        for key in ("ts", "db", "summary", "takeaway", "signal"):
            self.assertIn(key, body)
        self.assertEqual(body["takeaway"]["done"], 4)
        self.assertEqual(body["takeaway"]["total"], 10)
        self.assertEqual(body["takeaway"]["pct"], 40.0)
        self.assertEqual(body["takeaway"]["latest"], "2026-07-20")
        self.assertEqual(body["signal"]["latest"], "2026-07-16")

    def test_coverage_block_is_not_a_route(self):
        """契約：輔助函式不得成為路由（否則就是裝飾器又套錯了）。"""
        paths = {getattr(r, "path", None) for r in app.routes}
        self.assertIn("/api/progress", paths)
        self.assertIn("/api/stats", paths)
        # 沒有任何路由的 endpoint 是輔助函式
        endpoints = {getattr(r, "endpoint", None) for r in app.routes}
        self.assertNotIn(monitor._coverage_block, endpoints)

    def test_no_private_helper_is_a_route(self):
        """比上一條更廣：模組裡任何 `_` 開頭的函式都不得成為端點。

        只釘 `_coverage_block` 的話，下一支插錯位置的輔助函式又會是同一個 422。
        """
        endpoints = {getattr(r, "endpoint", None) for r in app.routes}
        offenders = [
            name
            for name, obj in vars(monitor).items()
            if name.startswith("_") and callable(obj) and obj in endpoints
        ]
        self.assertEqual([], offenders, f"輔助函式被裝飾成端點：{offenders}")

    def test_requires_auth(self):
        c = TestClient(app, follow_redirects=False, base_url="http://127.0.0.1")
        r = c.get("/api/progress")
        self.assertIn(r.status_code, (302, 401))


class ScheduleVisibilityHttpTests(unittest.TestCase):
    """`sync` 與 `unit_failures` 兩塊必須真的出現在 HTTP 回應裡。

    這兩塊補的是可觀測性斷層：runtime 區塊原本只認 `tag_run_*`／`ingest_run_*`
    兩種 log，而那兩支全量腳本只在初次建庫時跑——生產實際的入庫路徑
    （`sync_new_reports.sh`，每 3 小時）在監控頁上零可見度；而
    `data/unit_failures.log` 從 P3 上線起零程式消費端，2026-07-28 寫了 10 筆告警
    整整一天沒人知道。

    走 HTTP 而不是直接呼叫 handler，理由同本檔 docstring。
    """

    RUNTIME = {
        "tagging": None,
        "ingest": None,
        "pipelines": {"web": True},
        "orchestrator": None,
        "sync": {
            "raw": "[2026-07-30 09:00:12] === sync done ===",
            "timestamp": "2026-07-30 09:00:12",
            "status": "done",
            "label": "同步已完成",
        },
        "unit_failures": {
            "latest": "2026-07-28T15:00:03+08:00",
            "count_24h": 2,
            "count_7d": 10,
            "recent": [
                {"ts": "2026-07-28T15:00:03+08:00",
                 "unit": "report-mark-sync.service",
                 "stage": "sync_new_reports(import)", "rc": 2},
            ],
        },
    }

    def _body(self):
        async def fake_snapshot():
            d = dict(_SNAPSHOT)
            d["takeaway_latest"] = "2026-07-20"
            d["signal_latest"] = "2026-07-16"
            return d

        with patch.object(monitor, "_db_stats_snapshot", fake_snapshot), \
             patch.object(monitor, "_gather_runtime", lambda: dict(self.RUNTIME)):
            r = _authed().get("/api/progress")
        self.assertEqual(r.status_code, 200, r.text[:300])
        return r.json()

    def test_sync_block_is_exposed(self):
        body = self._body()
        self.assertIn("sync", body)
        self.assertEqual(body["sync"]["status"], "done")
        self.assertEqual(body["sync"]["timestamp"], "2026-07-30 09:00:12")

    def test_unit_failures_block_is_exposed(self):
        body = self._body()
        self.assertIn("unit_failures", body)
        self.assertEqual(body["unit_failures"]["count_24h"], 2)
        self.assertEqual(body["unit_failures"]["count_7d"], 10)
        self.assertEqual(
            body["unit_failures"]["recent"][0]["unit"], "report-mark-sync.service"
        )

    def test_runtime_cache_does_not_leak_across_requests_with_new_data(self):
        """快取存在，但同一個 patch 下兩次請求必須一致（不是回半舊半新）。"""
        first = self._body()
        second = self._body()
        self.assertEqual(first["unit_failures"], second["unit_failures"])


class StatsHttpTests(unittest.TestCase):
    def test_stats_returns_200_without_params(self):
        async def fake_snapshot():
            return dict(_SNAPSHOT)

        with patch.object(monitor, "_db_stats_snapshot", fake_snapshot):
            r = _authed().get("/api/stats")
        self.assertEqual(r.status_code, 200, r.text[:300])
        body = r.json()
        self.assertEqual(body["total_reports"], 6)
        self.assertEqual(body["username"], auth.ACCESS_USERNAME)


if __name__ == "__main__":
    unittest.main()
    _ = deps  # 保持 import（與其他測試一致的模組物件存取慣例）
