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
        # 替身沒填 stats → 旗標必須落在保守側（未截斷），不可 KeyError 也不可 None
        self.assertIs(response.lexical_truncated, False)


class LexicalTruncationFlagTests(unittest.IsolatedAsyncioTestCase):
    """檢索頁必須把「字面候選被 cap 截斷」帶出去。

    截斷本身是靜默的：`LIMIT :cap` 沒有 ORDER BY，取到哪 cap 列由 heap 物理順序決定
    （`synchronize_seqscans` 預設 on ⇒ 併發 seq scan 從任意 block 起掃），也就是同一
    查詢在不同時刻可能回不同結果，而回應裡沒有任何線索。
    """

    async def _search_with_stats(self, filler):
        from web import deps
        from web.routers import search as search_mod

        async def fake_hybrid_search(session, query, qvec, *, stats=None, **kwargs):
            filler(stats)
            return []

        orig = (
            deps.hybrid_search, deps.embed_query_cached,
            deps.rank_reports, deps.SessionFactory,
        )
        deps.hybrid_search = fake_hybrid_search
        deps.embed_query_cached = lambda q: [0.0]
        deps.rank_reports = lambda scored, *, sort="relevance": []
        deps.SessionFactory = lambda: _FakeSession()
        try:
            return await search_mod.search(
                q="台積電", market=None, instrument_type=None, relates_stock=None,
                relates_futures=None, report_type=None, sort="relevance",
                limit=50, offset=0, passages=3,
            )
        finally:
            (
                deps.hybrid_search, deps.embed_query_cached,
                deps.rank_reports, deps.SessionFactory,
            ) = orig

    async def test_truncated_stats_surface_as_flag(self):
        resp = await self._search_with_stats(
            lambda s: s.update({"lex_hits": 8000, "lex_cap": 8000, "lex_truncated": True})
        )
        self.assertIs(resp.lexical_truncated, True)

    async def test_untruncated_stats_leave_flag_false(self):
        resp = await self._search_with_stats(
            lambda s: s.update({"lex_hits": 37, "lex_cap": 8000, "lex_truncated": False})
        )
        self.assertIs(resp.lexical_truncated, False)

    async def test_handler_actually_passes_a_stats_dict(self):
        """不是 None：hybrid_search 只在 stats is not None 時填，傳 None 等於整條遙測落空。"""
        got = {}
        await self._search_with_stats(lambda s: got.update({"is_dict": isinstance(s, dict)}))
        self.assertTrue(got["is_dict"])


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
