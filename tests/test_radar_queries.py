# tests/test_radar_queries.py
"""radar/queries.py：parse_signal_row 解析 + SQL 結構 + 假 session 打包（零 DB）。"""
import json
import sys
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.radar import queries  # noqa: E402
from app.services.radar.types import parse_signal_row  # noqa: E402


def _row(eps_json="[]", thesis_json="{}", target=Decimal("2444.0000"), rating="buy",
         status="valid"):
    # 順序須與 types.SIGNAL_SELECT_COLUMNS 一致
    return (
        "sig-1", "rep-1", "TW", "8046", "daiwa", date(2026, 7, 11), "Buy (1)", rating,
        target, "TWD", "12M", "TP 證據", eps_json, thesis_json, status, "daiwa-8046.pdf",
    )


class ParseSignalRowTests(unittest.TestCase):
    def test_basic_fields(self):
        s = parse_signal_row(_row())
        self.assertEqual(s.instrument_code, "8046")
        self.assertEqual(s.broker, "daiwa")
        self.assertEqual(s.rating_normalized, "buy")
        self.assertEqual(s.target_price, 2444.0)  # Decimal → float
        self.assertEqual(s.target_currency, "TWD")
        self.assertEqual(s.file_name, "daiwa-8046.pdf")
        self.assertEqual(s.report_date, date(2026, 7, 11))

    def test_eps_and_thesis_parsed_from_text(self):
        eps = json.dumps([{"fiscal_year": 2026, "period": "FY", "currency": "TWD",
                           "unit": "per_share", "value": 66.4, "evidence": "e"}])
        thesis = json.dumps({"outlook": {"stance": "positive", "summary": "s", "evidence": "e"}})
        s = parse_signal_row(_row(eps_json=eps, thesis_json=thesis))
        self.assertEqual(len(s.eps), 1)
        self.assertEqual(s.eps[0].fiscal_year, 2026)
        self.assertEqual(s.eps[0].value, 66.4)
        self.assertEqual(s.thesis["outlook"].stance, "positive")

    def test_bad_jsonb_tolerated(self):
        s = parse_signal_row(_row(eps_json="{bad", thesis_json="also bad"))
        self.assertEqual(s.eps, ())      # 壞 jsonb → 空
        self.assertEqual(s.thesis, {})

    def test_null_rating_defaults_unknown(self):
        s = parse_signal_row(_row(rating=None))
        self.assertEqual(s.rating_normalized, "unknown")


class SqlStructureTests(unittest.TestCase):
    def test_instrument_signals_sql_named_params(self):
        sql = queries._instrument_signals_sql(broker=False)
        self.assertIn("FROM research.report_signal s", sql)
        self.assertIn("JOIN research.research_report r ON r.id = s.report_id", sql)
        self.assertIn(":market", sql)
        self.assertIn(":code", sql)
        self.assertIn("s.extraction_status = ANY(:statuses)", sql)
        self.assertNotIn(":broker", sql)  # broker=False 不含 broker 條件

    def test_broker_variant_adds_broker_filter(self):
        sql = queries._instrument_signals_sql(broker=True)
        self.assertIn("s.broker = :broker", sql)

    def test_catalog_cte_structure(self):
        cte = queries._catalog_cte()
        self.assertIn("unnest(r.stock_targets)", cte)
        self.assertIn("count(DISTINCT broker)", cte)
        self.assertIn("bool_or(extraction_status = ANY(:statuses))", cte)

    def test_catalog_filters_named_params(self):
        where, params = queries._catalog_filters("TW", "台積")
        self.assertIn("cat.market = :market", where)
        self.assertIn("ILIKE :q", where)
        self.assertEqual(params["market"], "TW")
        self.assertEqual(params["q"], "%台積%")
        # 無 q/market → 只有 statuses
        where2, params2 = queries._catalog_filters(None, None)
        self.assertEqual(where2, "")
        self.assertEqual(list(params2.keys()), ["statuses"])


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)


class _QueuedSession:
    def __init__(self, results):
        self._results = list(results)
        self.executed = 0

    async def execute(self, *a, **k):
        res = self._results[self.executed]
        self.executed += 1
        return res


class FetchTests(unittest.IsolatedAsyncioTestCase):
    async def test_fetch_instrument_signals_packs_rows(self):
        session = _QueuedSession([_FakeResult([_row(), _row(rating="neutral")])])
        signals = await queries.fetch_instrument_signals(session, "TW", "8046")
        self.assertEqual(len(signals), 2)
        self.assertEqual(signals[0].instrument_code, "8046")
        self.assertEqual(signals[1].rating_normalized, "neutral")


if __name__ == "__main__":
    unittest.main()
