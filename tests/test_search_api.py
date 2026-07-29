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


class BrowseListMappingTests(unittest.IsolatedAsyncioTestCase):
    """/api/reports 以**位移**解包 store.list_reports 的列 —— 欄序即契約。

    錯位不會拋錯（型別都是 str|None），只會讓標題欄印出券商名、來源欄印出標題之類
    的靜默錯值。這裡把欄序釘死：一旦 SELECT 與解包不同步，這條就紅。
    """

    async def test_row_columns_map_to_the_right_fields(self):
        from web import deps
        from web.routers import search as search_mod

        # 順序須與 store.list_reports 的 SELECT 一致
        row = (
            "rid-1", "h" * 64, "6247269925_260728_gs_umt.pdf", "低軌衛星業務擴展",
            "TW", "goldman_sachs", None, "個股報告", ["equity"], True, False,
            ["3491"], [], "摘要",
        )

        async def fake_list_reports(session, **kwargs):
            return 1, [row]

        orig_list, orig_factory = search_mod.list_reports, deps.SessionFactory
        search_mod.list_reports = fake_list_reports
        deps.SessionFactory = lambda: _FakeSession()
        try:
            resp = await search_mod.reports(
                market=None, instrument_type=None, relates_stock=None,
                relates_futures=None, report_type=None, sort="date_desc",
                limit=50, offset=0,
            )
        finally:
            search_mod.list_reports = orig_list
            deps.SessionFactory = orig_factory

        item = resp.items[0]
        self.assertEqual(item.file_name, "6247269925_260728_gs_umt.pdf")
        self.assertEqual(item.title, "低軌衛星業務擴展")
        self.assertEqual(item.market, "TW")
        self.assertEqual(item.report_type, "個股報告")
        self.assertEqual(item.summary, "摘要")
        self.assertEqual(item.stock_targets, ["3491"])


if __name__ == "__main__":
    unittest.main()
