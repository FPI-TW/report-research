import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.overview import detect_overview  # noqa: E402
from app.services.overview import OverviewFilters, resolve_filters  # noqa: E402


class DetectOverviewTests(unittest.TestCase):
    def test_enumeration_cue_is_overview(self):
        self.assertTrue(detect_overview("給我所有元大的報告種類"))
        self.assertTrue(detect_overview("台股最近一週有哪些新研報"))
        self.assertTrue(detect_overview("元大總共有多少篇研報"))

    def test_specific_question_is_not_overview(self):
        self.assertFalse(detect_overview("台積電的投資評級如何"))
        self.assertFalse(detect_overview("元大怎麼看半導體景氣"))


class ResolveFiltersTests(unittest.TestCase):
    TODAY = date(2026, 6, 24)

    def _r(self, q):
        return resolve_filters(q, self.TODAY)

    def test_broker_chinese_name(self):
        self.assertEqual(self._r("給我所有元大的研報").source, "yuanta")

    def test_broker_display_name_foreign(self):
        self.assertEqual(self._r("摩根士丹利有哪些研報").source, "morgan_stanley")

    def test_market_synonyms(self):
        self.assertEqual(self._r("台股有哪些新研報").market, "TW")
        self.assertEqual(self._r("美國市場研報清單").market, "US")

    def test_instrument_type(self):
        self.assertEqual(self._r("有哪些期貨研報").instrument_type, "futures")
        self.assertEqual(self._r("ETF 報告列表").instrument_type, "etf")

    def test_relative_dates(self):
        f = self._r("最近一週有哪些研報")
        self.assertEqual((f.date_from, f.date_to), (date(2026, 6, 17), self.TODAY))
        g = self._r("今年有哪些元大研報")
        self.assertEqual((g.date_from, g.date_to), (date(2026, 1, 1), self.TODAY))

    def test_year_literal(self):
        f = self._r("2025年有哪些台股研報")
        self.assertEqual((f.date_from, f.date_to), (date(2025, 1, 1), date(2025, 12, 31)))

    def test_year_literal_not_stock_code(self):
        f = self._r("2025年有哪些台股研報")
        self.assertIsNone(f.stock_code)
        self.assertEqual((f.date_from, f.date_to), (date(2025, 1, 1), date(2025, 12, 31)))

    def test_stock_code(self):
        self.assertEqual(self._r("2330 有哪些研報").stock_code, "2330")

    def test_stock_name_residual(self):
        f = self._r("給我所有台積電的研報")
        self.assertEqual(f.stock_name, "台積電")

    def test_no_filter(self):
        self.assertFalse(self._r("列出所有天氣種類").any())

    def test_applied_labels(self):
        f = self._r("元大台股最近一週有哪些研報")
        labels = f.applied_labels()
        self.assertIn("券商=元大", labels)
        self.assertIn("市場=台股", labels)


from app.services.overview import (  # noqa: E402
    CorpusOverview,
    aggregate_facets,
    format_facts,
    render_overview_text,
)


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return list(self._rows)


class _QueuedSession:
    """依序回傳預先排好的 _FakeResult，模擬 aggregate_facets 的多次 execute。"""

    def __init__(self, results):
        self._results = list(results)
        self.executed = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, *a, **k):
        res = self._results[self.executed]
        self.executed += 1
        return res


class AggregateFacetsTests(unittest.IsolatedAsyncioTestCase):
    async def test_packs_rows_into_overview(self):
        # f.source 已設 → by_source 查詢被短路跳過，僅 6 次 execute
        results = [
            _FakeResult([(734, date(2021, 3, 1), date(2026, 6, 20))]),  # totals
            _FakeResult([("TW", 700), ("US", 20), ("MACRO", 14)]),      # by_market
            _FakeResult([("equity", 690), ("index", 300)]),             # by_instrument
            _FakeResult([("(未標註)", 732), ("速報", 1), ("策略", 1)]),  # by_report_type
            _FakeResult([("2330", 120), ("2317", 80)]),                # top_stocks
            _FakeResult([("rid1", "元大-台積電.pdf", "TW", date(2026, 6, 20))]),  # samples
        ]
        f = OverviewFilters(source="yuanta")
        ov = await aggregate_facets(_QueuedSession(results), f)
        self.assertEqual(ov.total, 734)
        self.assertEqual(ov.date_max, date(2026, 6, 20))
        self.assertEqual(ov.by_market[0], ("TW", 700))
        self.assertEqual(ov.by_report_type[0], ("(未標註)", 732))
        self.assertEqual(ov.samples[0][1], "元大-台積電.pdf")
        self.assertEqual(ov.by_source, [])
        self.assertIs(ov.filters, f)

    async def test_by_source_populated_when_no_source_filter(self):
        results = [
            _FakeResult([(900, date(2021, 1, 1), date(2026, 6, 20))]),       # totals
            _FakeResult([("TW", 800), ("US", 100)]),                          # by_market
            _FakeResult([("equity", 850)]),                                   # by_instrument
            _FakeResult([("yuanta", 500), ("kgi", 400)]),                     # by_source (runs: no source filter)
            _FakeResult([("(未標註)", 880)]),                                  # by_report_type
            _FakeResult([("2330", 200)]),                                     # top_stocks
            _FakeResult([("rid9", "報告.pdf", "TW", date(2026, 6, 20))]),     # samples
        ]
        ov = await aggregate_facets(_QueuedSession(results), OverviewFilters())
        self.assertEqual(ov.by_source[0], ("yuanta", 500))
        self.assertEqual(ov.by_report_type[0], ("(未標註)", 880))


def _sample_overview():
    return CorpusOverview(
        total=734,
        date_min=date(2021, 3, 1),
        date_max=date(2026, 6, 20),
        by_market=[("TW", 700), ("US", 20)],
        by_instrument=[("equity", 690), ("index", 300)],
        by_source=[],
        by_report_type=[("(未標註)", 732), ("速報", 1)],
        top_stocks=[("2330", 120)],
        samples=[("rid1", "元大-台積電.pdf", "TW", date(2026, 6, 20))],
        filters=OverviewFilters(source="yuanta"),
    )


class FormatFactsTests(unittest.TestCase):
    def test_facts_contain_numbers_and_labels(self):
        txt = format_facts(_sample_overview())
        self.assertIn("734", txt)
        self.assertIn("台股", txt)        # 市場代碼轉中文
        self.assertIn("(未標註)", txt)     # 稀疏 report_type 桶
        self.assertIn("元大", txt)         # 已套用條件顯示

    def test_render_text_has_total_and_sample_citation(self):
        txt = render_overview_text(_sample_overview())
        self.assertIn("734", txt)
        self.assertIn("[1]", txt)          # 樣本帶編號供點閱

    def test_zero_total_handled(self):
        ov = CorpusOverview(total=0, date_min=None, date_max=None,
                            filters=OverviewFilters(source="yuanta"))
        self.assertIn("找不到", render_overview_text(ov))


import asyncio  # noqa: E402

from app.services import answer as ans  # noqa: E402


class AnswerQuestionOverviewBranchTests(unittest.TestCase):
    def _drive(self, question):
        async def run():
            events = []
            async for ev in ans.answer_question(question):
                events.append(ev)
            return events

        return asyncio.run(run())

    def _patch_common(self):
        called = {"hybrid": 0}

        async def fake_hybrid(*a, **k):
            called["hybrid"] += 1
            return []

        async def fake_agg(session, f, **k):
            return CorpusOverview(
                total=734, date_min=date(2021, 3, 1), date_max=date(2026, 6, 20),
                by_market=[("TW", 700)], by_instrument=[("equity", 690)],
                by_source=[], by_report_type=[("(未標註)", 732)],
                top_stocks=[("2330", 120)],
                samples=[("rid1", "元大-台積電.pdf", "TW", date(2026, 6, 20))],
                filters=OverviewFilters(source="yuanta"),
            )

        async def fake_stream(*a, **k):
            yield "元大"
            yield "共有 734 篇研報。[1]"

        async def fake_log(*a, **k):
            return "qa-id"

        ans.hybrid_search = fake_hybrid
        ans.aggregate_facets = fake_agg
        ans.stream_completion = fake_stream
        ans._log_qa = fake_log
        ans.SessionFactory = lambda: _QueuedSession([])
        return called

    def test_overview_question_takes_overview_path(self):
        orig = (ans.hybrid_search, ans.aggregate_facets, ans.stream_completion,
                ans._log_qa, ans.SessionFactory)
        try:
            called = self._patch_common()
            events = self._drive("給我所有元大的報告種類")
            kinds = [e[0] for e in events]
            self.assertIn("sources", kinds)
            self.assertIn("token", kinds)
            self.assertEqual(kinds[-1], "done")
            self.assertEqual(called["hybrid"], 0)  # 沒走 RAG 檢索
            text_joined = "".join(p for k, p in events if k == "token" and isinstance(p, str))
            self.assertIn("734", text_joined)
        finally:
            (ans.hybrid_search, ans.aggregate_facets, ans.stream_completion,
             ans._log_qa, ans.SessionFactory) = orig
