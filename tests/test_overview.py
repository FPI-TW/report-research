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


from app.services.overview import CorpusOverview, aggregate_facets  # noqa: E402


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
        results = [
            _FakeResult([(734, date(2021, 3, 1), date(2026, 6, 20))]),  # totals
            _FakeResult([("TW", 700), ("US", 20), ("MACRO", 14)]),      # by_market
            _FakeResult([("equity", 690), ("index", 300)]),             # by_instrument
            _FakeResult([("yuanta", 734)]),                             # by_source
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
        self.assertIs(ov.filters, f)
