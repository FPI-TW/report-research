# tests/test_radar_queries.py
"""radar/queries.py：parse_signal_row 解析 + SQL 結構 + 假 session 打包（零 DB）。"""
import json
import sys
import unittest
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy.dialects.postgresql import asyncpg as pg_asyncpg  # noqa: E402

from app.services.radar import queries  # noqa: E402
from app.services.radar.types import parse_signal_row  # noqa: E402


def _row(eps_json="[]", thesis_json="{}", target=Decimal("2444.0000"), rating="buy",
         status="valid", created_at=datetime(2026, 7, 11, 9, tzinfo=timezone.utc)):
    # 順序須與 types.SIGNAL_SELECT_COLUMNS 一致
    return (
        "sig-1", "rep-1", "TW", "8046", "daiwa", date(2026, 7, 11), "Buy (1)", rating,
        target, "TWD", "12M", "TP 證據", eps_json, thesis_json, status, "daiwa-8046.pdf",
        created_at,
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

    def test_created_at_parsed(self):
        created_at = datetime(2026, 7, 11, 9, tzinfo=timezone.utc)
        s = parse_signal_row(_row(created_at=created_at))
        self.assertEqual(getattr(s, "created_at", None), created_at)


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
        self.assertIn(
            "COALESCE(NULLIF(BTRIM(r.source), ''), NULLIF(BTRIM(s.broker), '')) = :broker",
            sql,
        )

    def test_signal_queries_use_effective_broker_and_stable_order(self):
        effective = (
            "COALESCE(NULLIF(BTRIM(r.source), ''), "
            "NULLIF(BTRIM(s.broker), ''))"
        )
        instrument_sql = queries._instrument_signals_sql(broker=False)
        batch_sql = str(queries._BATCH_SIGNALS_SQL)
        coverage_sql = str(queries._COVERAGE_SQL)
        self.assertIn(f"{effective} AS broker", instrument_sql)
        self.assertIn(f"{effective} AS broker", batch_sql)
        self.assertIn(effective, coverage_sql)
        self.assertIn("s.created_at DESC, s.id DESC", instrument_sql)
        self.assertIn("s.created_at DESC, s.id DESC", batch_sql)

    def test_coverage_denominators_use_effective_broker_identity(self):
        effective = (
            "COALESCE(NULLIF(BTRIM(r.source), ''), "
            "NULLIF(BTRIM(s.broker), ''))"
        )
        coverage_total = str(queries._COVERAGE_SQL).split("AS brokers_total", 1)[0]
        catalog_rep = queries._catalog_cte().split("), rep AS (", 1)[1]

        self.assertIn(effective, coverage_total)
        self.assertIn("LEFT JOIN research.report_signal s", coverage_total)
        self.assertIn(f"count(DISTINCT {effective}) AS broker_count", catalog_rep)

    def test_catalog_cte_structure(self):
        cte = queries._catalog_cte()
        self.assertIn("unnest(r.stock_targets)", cte)
        self.assertIn("count(DISTINCT broker)", cte)
        self.assertIn("bool_or(extraction_status = ANY(:statuses))", cte)

    def test_instrument_name_is_bound_to_matching_stock_code(self):
        coverage_sql = str(queries._COVERAGE_SQL)
        catalog_cte = queries._catalog_cte()
        self.assertIn("r.stock_code = :code", coverage_sql)
        self.assertIn("r.stock_code = st", catalog_cte)

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

    def scalar_one(self):
        return self._rows[0]

    def first(self):
        return self._rows[0] if self._rows else None


class _QueuedSession:
    def __init__(self, results):
        self._results = list(results)
        self.executed = 0
        self.calls = []

    async def execute(self, *a, **k):
        self.calls.append((a, k))
        res = self._results[self.executed]
        self.executed += 1
        return res


def _brow(code, broker="a", market="TW",
          created_at=datetime(2026, 7, 11, 9, tzinfo=timezone.utc)):
    # SIGNAL_SELECT_COLUMNS 順序，供批次分組測試（不同 code/broker）
    return (
        f"s-{code}-{broker}", f"r-{code}-{broker}", market, code, broker,
        date(2026, 7, 11), "Buy", "buy", Decimal("100.0"), "TWD", "12M", "e",
        "[]", "{}", "valid", f"{broker}.pdf", created_at,
    )


class FetchTests(unittest.IsolatedAsyncioTestCase):
    async def test_fetch_instrument_signals_packs_rows(self):
        session = _QueuedSession([_FakeResult([_row(), _row(rating="neutral")])])
        signals = await queries.fetch_instrument_signals(session, "TW", "8046")
        self.assertEqual(len(signals), 2)
        self.assertEqual(signals[0].instrument_code, "8046")
        self.assertEqual(signals[1].rating_normalized, "neutral")

    async def test_broker_coverage_counts_distinguish_instrument_and_broker(self):
        fetch = getattr(queries, "fetch_broker_coverage_counts", None)
        self.assertIsNotNone(fetch)
        session = _QueuedSession([_FakeResult([(7, 2)])])

        coverage = await fetch(session, "TW", "USD/TWD", "A/B")

        self.assertEqual(coverage.instrument_reports_available, 7)
        self.assertEqual(coverage.broker_reports_available, 2)
        params = session.calls[0][0][1]
        self.assertEqual(
            params,
            {"market": "TW", "code": "USD/TWD", "broker": "A/B"},
        )


class BrokerCoverageSqlTests(unittest.TestCase):
    def test_uses_canonical_effective_broker_and_report_universe(self):
        sql_obj = getattr(queries, "_BROKER_COVERAGE_SQL", None)
        self.assertIsNotNone(sql_obj)
        sql = str(sql_obj)
        self.assertIn(
            "COALESCE(NULLIF(BTRIM(r.source), ''), NULLIF(BTRIM(s.broker), ''))",
            sql,
        )
        self.assertIn(":code = ANY(r.stock_targets)", sql)
        self.assertIn("r.is_research IS NOT FALSE", sql)
        self.assertIn("= :broker", sql)


class BatchSignalsTests(unittest.IsolatedAsyncioTestCase):
    async def test_groups_by_instrument(self):
        session = _QueuedSession(
            [_FakeResult([_brow("8046"), _brow("8046", "b"), _brow("9914")])]
        )
        out = await queries.fetch_signals_for_instruments(
            session, [("TW", "8046"), ("TW", "9914")]
        )
        self.assertEqual(set(out.keys()), {("TW", "8046"), ("TW", "9914")})
        self.assertEqual(len(out[("TW", "8046")]), 2)
        self.assertEqual(len(out[("TW", "9914")]), 1)

    async def test_empty_keys_skips_query(self):
        session = _QueuedSession([])  # 若真的 execute 會 IndexError
        out = await queries.fetch_signals_for_instruments(session, [])
        self.assertEqual(out, {})
        self.assertEqual(session.executed, 0)


class CatalogQueryTests(unittest.IsolatedAsyncioTestCase):
    async def test_catalog_order_is_stable_across_markets(self):
        session = _QueuedSession([_FakeResult([0]), _FakeResult([])])
        await queries.list_radar_instruments(session)
        page_sql = str(session.calls[1][0][0])
        self.assertIn(
            "ORDER BY cat.latest DESC NULLS LAST, cat.instrument_code, cat.market",
            page_sql,
        )


class BatchSqlStructureTests(unittest.TestCase):
    """須在「編譯後」驗證：str(text()) 只是把原字串吐回來，參數沒綁上也看不出來。

    :markets::text[] 曾讓 bind 回溯成短名 market＋殘字 s，冒號原樣送進 PG 炸 syntax
    error；當時的字串比對測試卻是綠的（它比對的正是壞掉的原字串）。
    """

    def _compiled(self):
        return queries._BATCH_SIGNALS_SQL.compile(dialect=pg_asyncpg.dialect())

    def test_batch_sql_binds_are_complete_names(self):
        # 名字被吃掉一個字元（markets → market）就會在這裡現形
        self.assertEqual(set(self._compiled().params), {"markets", "codes", "statuses"})

    def test_batch_sql_leaves_no_unbound_colon_param(self):
        # 編譯後只該剩 $n 佔位與 ::text 轉型；殘留 :name 代表該參數根本沒綁上
        self.assertNotRegex(str(self._compiled()), r"(?<!:):\w+")

    def test_batch_sql_keeps_row_wise_key_filter(self):
        compiled = str(self._compiled())
        self.assertIn("(s.market, s.instrument_code) IN", compiled)
        self.assertIn("unnest(CAST($1 AS text[]), CAST($2 AS text[]))", compiled)


if __name__ == "__main__":
    unittest.main()
