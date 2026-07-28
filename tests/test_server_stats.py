import os
import sys
import unittest
from datetime import date
from pathlib import Path

os.environ.setdefault("REPORT_MARK_ACCESS_USERNAME", "tester")
os.environ.setdefault("REPORT_MARK_ACCESS_PASSWORD", "testpass")
os.environ.setdefault("REPORT_MARK_SESSION_SECRET", "fixed-test-secret-0123456789")

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

# stats/progress 及其快取、進度解析 helper 已拆到 web.routers.monitor；服務綁定
# （SessionFactory）仍在 web.deps。故 handler/快取/常數的覆寫指向 monitor 模組。
from web import deps  # noqa: E402
from web.routers import monitor  # noqa: E402


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one(self):
        return self.value


class _RowsResult:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class _FirstResult:
    def __init__(self, row):
        self.row = row

    def first(self):
        return self.row


class OrchestratorParseTests(unittest.TestCase):
    def test_parse_resume_done_entry(self):
        parsed = monitor._parse_orchestrator_entry(
            "[2026-06-18 23:28:15] === resume done ==="
        )

        self.assertEqual(
            parsed,
            {
                "raw": "[2026-06-18 23:28:15] === resume done ===",
                "timestamp": "2026-06-18 23:28:15",
                "status": "done",
                "label": "編排器已完成",
            },
        )

    def test_parse_unknown_entry_falls_back_to_raw(self):
        parsed = monitor._parse_orchestrator_entry("some unexpected orchestrator text")

        self.assertEqual(
            parsed,
            {
                "raw": "some unexpected orchestrator text",
                "timestamp": None,
                "status": "unknown",
                "label": "編排器狀態",
            },
        )


class StatsCacheTests(unittest.IsolatedAsyncioTestCase):
    def test_cache_ttl_is_within_requested_range(self):
        self.assertGreaterEqual(monitor.DB_STATS_CACHE_TTL_SECONDS, 3.0)
        self.assertLessEqual(monitor.DB_STATS_CACHE_TTL_SECONDS, 5.0)

    def setUp(self):
        if hasattr(monitor, "_DB_STATS_CACHE"):
            monitor._DB_STATS_CACHE["data"] = None
            monitor._DB_STATS_CACHE["expires_at"] = 0.0

    async def test_stats_and_progress_share_one_db_snapshot_within_ttl(self):
        calls = []

        class FakeSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def execute(self, stmt, params=None):
                sql = str(stmt)
                calls.append(sql)
                if "FILTER (WHERE summary IS NOT NULL)" in sql:
                    return _FirstResult((3, 7))
                # 這兩個必須排在下方 catch-all「FROM research.research_report」之前:
                # 它們同樣掃 research_report,被 catch-all 攔到會回純量、解包時炸。
                if "report_takeaway" in sql:
                    return _FirstResult((4, 10, date(2026, 7, 20)))
                if "report_signal" in sql:
                    return _FirstResult((1, 10, date(2026, 7, 16)))
                if "unnest(instrument_types)" in sql:
                    return _RowsResult([("equity", 5)])
                if "GROUP BY report_type" in sql:
                    return _RowsResult([("daily", 4)])
                if "GROUP BY market" in sql:
                    return _RowsResult([("TW", 6)])
                if "FROM research.report_chunk" in sql:
                    return _ScalarResult(12)
                if "FROM research.research_report" in sql:
                    return _ScalarResult(6)
                raise AssertionError(sql)

        orig_session_factory = deps.SessionFactory
        orig_gather_runtime = monitor._gather_runtime
        deps.SessionFactory = lambda: FakeSession()
        monitor._gather_runtime = lambda: {
            "tagging": None,
            "ingest": None,
            "pipelines": {"web": True},
            "orchestrator": None,
        }
        try:
            stats = await monitor.stats()
            progress = await monitor.progress()
        finally:
            deps.SessionFactory = orig_session_factory
            monitor._gather_runtime = orig_gather_runtime

        # 8 = 原本 6 + takeaway/signal 覆蓋率各一。這個數字守的是「stats 與 progress
        # 共用 _DB_STATS_CACHE、TTL 內只打一次 DB」（見 monitor.py 模組 docstring）。
        self.assertEqual(len(calls), 8)
        self.assertEqual(stats["total_reports"], 6)
        self.assertEqual(progress["db"]["reports"], 6)
        self.assertEqual(progress["summary"]["total"], 7)
        # 派生資產新鮮度（在 progress 而非 stats——與既有的 summary 覆蓋率同處）:
        # 先前只量 summary,而 summary 恰好是唯一有排程的,真正在腐化的兩張表零量測。
        self.assertEqual(progress["takeaway"]["done"], 4)
        self.assertEqual(progress["takeaway"]["total"], 10)
        self.assertEqual(progress["takeaway"]["remaining"], 6)
        self.assertEqual(progress["takeaway"]["pct"], 40.0)
        self.assertEqual(progress["takeaway"]["latest"], "2026-07-20")
        self.assertEqual(progress["signal"]["done"], 1)
        self.assertEqual(progress["signal"]["latest"], "2026-07-16")


if __name__ == "__main__":
    unittest.main()
