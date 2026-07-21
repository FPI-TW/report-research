import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("REPORT_MARK_ACCESS_USERNAME", "tester")
os.environ.setdefault("REPORT_MARK_ACCESS_PASSWORD", "testpass")
os.environ.setdefault("REPORT_MARK_SESSION_SECRET", "fixed-test-secret-0123456789")

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import web.server as server  # noqa: E402
from web import deps  # noqa: E402


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
        parsed = server._parse_orchestrator_entry(
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
        parsed = server._parse_orchestrator_entry("some unexpected orchestrator text")

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
        self.assertGreaterEqual(server.DB_STATS_CACHE_TTL_SECONDS, 3.0)
        self.assertLessEqual(server.DB_STATS_CACHE_TTL_SECONDS, 5.0)

    def setUp(self):
        if hasattr(server, "_DB_STATS_CACHE"):
            server._DB_STATS_CACHE["data"] = None
            server._DB_STATS_CACHE["expires_at"] = 0.0

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
        orig_gather_runtime = server._gather_runtime
        deps.SessionFactory = lambda: FakeSession()
        server._gather_runtime = lambda: {
            "tagging": None,
            "ingest": None,
            "pipelines": {"web": True},
            "orchestrator": None,
        }
        try:
            stats = await server.stats()
            progress = await server.progress()
        finally:
            deps.SessionFactory = orig_session_factory
            server._gather_runtime = orig_gather_runtime

        self.assertEqual(len(calls), 6)
        self.assertEqual(stats["total_reports"], 6)
        self.assertEqual(progress["db"]["reports"], 6)
        self.assertEqual(progress["summary"]["total"], 7)


if __name__ == "__main__":
    unittest.main()
