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
        # search handler 與 LEX_CAP_SEARCH 已拆到 web.routers.search；
        # 檢索綁定（hybrid_search 等）仍走 web.deps。
        from web import deps
        from web.routers import search as search_mod

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
            deps.hybrid_search,
            deps.embed_query_cached,
            deps.rank_reports,
            deps.SessionFactory,
        )
        deps.hybrid_search = fake_hybrid_search
        deps.embed_query_cached = fake_embed
        deps.rank_reports = fake_rank_reports
        deps.SessionFactory = lambda: _FakeSession()
        try:
            response = await search_mod.search(
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
                deps.hybrid_search,
                deps.embed_query_cached,
                deps.rank_reports,
                deps.SessionFactory,
            ) = orig

        self.assertTrue(seen["lex_unlimited"])
        self.assertEqual(seen["lex_cap"], search_mod.LEX_CAP_SEARCH)
        self.assertEqual(seen["sort"], "relevance")
        self.assertEqual(response.total, 0)


if __name__ == "__main__":
    unittest.main()
