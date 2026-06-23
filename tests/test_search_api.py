import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("REPORT_MARK_ACCESS_USERNAME", "tester")
os.environ.setdefault("REPORT_MARK_ACCESS_PASSWORD", "testpass")
os.environ.setdefault("REPORT_MARK_SESSION_SECRET", "fixed-test-secret-0123456789")

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class SearchApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_requests_unlimited_lexical_reports(self):
        from web import server

        seen = {}

        async def fake_hybrid_search(session, query, qvec, **kwargs):
            seen.update(kwargs)
            return []

        def fake_embed(query):
            return [0.0]

        def fake_rank_reports(scored, *, sort="relevance"):
            seen["sort"] = sort
            return []

        orig = (
            server.hybrid_search,
            server.embed_query_cached,
            server.rank_reports,
            server.SessionFactory,
        )
        server.hybrid_search = fake_hybrid_search
        server.embed_query_cached = fake_embed
        server.rank_reports = fake_rank_reports
        server.SessionFactory = lambda: _FakeSession()
        try:
            response = await server.search(
                q="台積電",
                market=None,
                instrument_type=None,
                relates_stock=None,
                relates_futures=None,
                report_type=None,
                sort="relevance",
                limit=50,
                offset=0,
                passages=3,
            )
        finally:
            (
                server.hybrid_search,
                server.embed_query_cached,
                server.rank_reports,
                server.SessionFactory,
            ) = orig

        self.assertTrue(seen["lex_unlimited"])
        self.assertEqual(seen["lex_cap"], server.LEX_CAP_SEARCH)
        self.assertEqual(seen["sort"], "relevance")
        self.assertEqual(response.total, 0)


if __name__ == "__main__":
    unittest.main()
