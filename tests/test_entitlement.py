# tests/test_entitlement.py
"""API 用戶端 entitlement：`from_mapping` 驗證、`sql()` 片段與參數、`allows()` 與 `sql()` 等價。

等價性不連 DB：`_eval_sql` 依 PostgreSQL 的三值邏輯解讀 `sql()` 實際產出的片段
（片段寫法一變、解讀不了就直接失敗，不會默默放行），再與 `allows()` 逐案比對。
NULL `source`／`report_type`／`instrument_types` 的研報在該維度有設定時必須兩邊都不符。
"""
import itertools
import re
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services.entitlement import Entitlement  # noqa: E402


class FromMappingTests(unittest.TestCase):
    def test_market_only_leaves_other_dimensions_unrestricted(self):
        ent = Entitlement.from_mapping({"market": ["TW", "US"]})
        self.assertEqual(ent, Entitlement(markets=("TW", "US")))
        self.assertIsNone(ent.sources)
        self.assertIsNone(ent.report_types)
        self.assertIsNone(ent.instrument_types)

    def test_all_dimensions(self):
        ent = Entitlement.from_mapping({
            "market": ["TW"],
            "source": ["元大", "凱基"],
            "report_type": ["個股"],
            "instrument_type": ["stock", "etf"],
        })
        self.assertEqual(ent.markets, ("TW",))
        self.assertEqual(ent.sources, ("元大", "凱基"))
        self.assertEqual(ent.report_types, ("個股",))
        self.assertEqual(ent.instrument_types, ("stock", "etf"))

    def test_empty_lists_mean_unrestricted(self):
        ent = Entitlement.from_mapping({"market": ["TW"], "source": [], "report_type": [], "instrument_type": []})
        self.assertEqual(ent, Entitlement(markets=("TW",)))

    def test_missing_or_empty_market_raises(self):
        for m in ({}, {"market": []}, {"source": ["元大"]}):
            with self.subTest(m=m), self.assertRaises(ValueError):
                Entitlement.from_mapping(m)

    def test_unknown_key_raises_instead_of_widening(self):
        # 打錯的維度名若被忽略，該維度就變成不限——allowlist 打錯字不能等於放寬權限。
        for key in ("markets", "broker", "instrument_types"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                Entitlement.from_mapping({"market": ["TW"], key: ["x"]})

    def test_bare_string_is_rejected_not_split_into_characters(self):
        with self.assertRaises(ValueError):
            Entitlement.from_mapping({"market": "TW"})
        with self.assertRaises(ValueError):
            Entitlement.from_mapping({"market": ["TW"], "source": "元大"})

    def test_empty_or_non_string_values_are_rejected(self):
        for bad in ([""], [None], [1]):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                Entitlement.from_mapping({"market": bad})

    def test_duplicates_collapse_preserving_order(self):
        ent = Entitlement.from_mapping({"market": ["US", "TW", "US"]})
        self.assertEqual(ent.markets, ("US", "TW"))

    def test_direct_construction_guards(self):
        with self.assertRaises(ValueError):
            Entitlement(markets=())
        with self.assertRaises(ValueError):
            Entitlement(markets=("TW",), sources=())

    def test_is_frozen(self):
        ent = Entitlement(markets=("TW",))
        with self.assertRaises(AttributeError):
            ent.markets = ("US",)  # type: ignore[misc]


class SqlTests(unittest.TestCase):
    def test_market_only(self):
        clause, params = Entitlement(markets=("TW", "US")).sql()
        self.assertEqual(clause, "r.market = ANY(CAST(:ent_markets AS text[]))")
        self.assertEqual(params, {"ent_markets": ["TW", "US"]})

    def test_each_dimension_fragment_and_param(self):
        ent = Entitlement(
            markets=("TW",), sources=("元大",), report_types=("個股",), instrument_types=("stock",)
        )
        clause, params = ent.sql("rr")
        self.assertEqual(
            clause.split(" AND "),
            [
                "rr.market = ANY(CAST(:ent_markets AS text[]))",
                "rr.source = ANY(CAST(:ent_sources AS text[]))",
                "rr.report_type = ANY(CAST(:ent_report_types AS text[]))",
                "rr.instrument_types && CAST(:ent_instrument_types AS text[])",
            ],
        )
        self.assertEqual(
            params,
            {
                "ent_markets": ["TW"],
                "ent_sources": ["元大"],
                "ent_report_types": ["個股"],
                "ent_instrument_types": ["stock"],
            },
        )

    def test_only_configured_dimensions_appear(self):
        clause, params = Entitlement(markets=("TW",), report_types=("產業",)).sql()
        self.assertNotIn("source", clause)
        self.assertNotIn("instrument_types", clause)
        self.assertEqual(set(params), {"ent_markets", "ent_report_types"})

    def test_params_are_lists_for_asyncpg_array_binding(self):
        _, params = Entitlement(markets=("TW",), instrument_types=("stock",)).sql()
        for value in params.values():
            self.assertIsInstance(value, list)

    def test_bind_casts_are_not_postgres_shorthand(self):
        # `:x::text[]` 會讓 text() 綁錯參數（AGENTS.md 資料層陷阱），一律寫 CAST。
        ent = Entitlement(markets=("TW",), sources=("a",), report_types=("b",), instrument_types=("c",))
        clause, _ = ent.sql()
        self.assertNotIn("::", clause)

    def test_array_dimension_uses_gin_friendly_overlap(self):
        # `= ANY(<陣列欄位>)` 吃不到 GIN（tests/test_sql_index_hygiene.py），陣列欄位要用 &&。
        clause, _ = Entitlement(markets=("TW",), instrument_types=("stock",)).sql()
        self.assertNotRegex(clause, r"ANY\(\s*\w+\.instrument_types")
        self.assertIn("r.instrument_types && ", clause)

    def test_alias_must_be_identifier(self):
        for alias in ("r; DROP TABLE x", "1r", "r.x", ""):
            with self.subTest(alias=alias), self.assertRaises(ValueError):
                Entitlement(markets=("TW",)).sql(alias)


_SCALAR = re.compile(r"^r\.(market|source|report_type) = ANY\(CAST\(:(ent_\w+) AS text\[\]\)\)$")
_ARRAY = re.compile(r"^r\.instrument_types && CAST\(:(ent_\w+) AS text\[\]\)$")


def _eval_sql(ent: Entitlement, row: dict) -> bool:
    """依 PostgreSQL 三值邏輯求 `ent.sql()` 對一列的 WHERE 結果（NULL 當作不放行）。"""
    clause, params = ent.sql()
    results = []
    for cond in clause.split(" AND "):
        if m := _SCALAR.match(cond):
            value = row[m.group(1)]
            # NULL = ANY(非空、無 NULL 元素的陣列) → NULL
            results.append(None if value is None else value in params[m.group(2)])
        elif m := _ARRAY.match(cond):
            value = row["instrument_types"]
            # NULL && x → NULL；陣列重疊忽略 NULL 元素，空陣列 → false
            overlap = {v for v in value if v is not None} & set(params[m.group(1)]) if value is not None else None
            results.append(None if overlap is None else bool(overlap))
        else:
            raise AssertionError(f"測試解讀不了這個片段，等價性無從比對：{cond!r}")
    if any(r is False for r in results):
        return False
    return all(r is True for r in results)


_ROWS = [
    dict(market="TW", source="元大", report_type="個股", instrument_types=["stock"]),
    dict(market="TW", source="凱基", report_type="產業", instrument_types=["stock", "etf"]),
    dict(market="US", source="元大", report_type="個股", instrument_types=["stock"]),
    dict(market="TW", source=None, report_type="個股", instrument_types=["stock"]),
    dict(market="TW", source="元大", report_type=None, instrument_types=["stock"]),
    dict(market="TW", source="元大", report_type="個股", instrument_types=None),
    dict(market="TW", source="元大", report_type="個股", instrument_types=[]),
    dict(market="TW", source="元大", report_type="個股", instrument_types=[None, "etf"]),
    dict(market=None, source="元大", report_type="個股", instrument_types=["stock"]),
    dict(market="HK", source=None, report_type=None, instrument_types=None),
]

_ENTITLEMENTS = [
    Entitlement(markets=("TW",)),
    Entitlement(markets=("TW", "US")),
    Entitlement(markets=("TW",), sources=("元大",)),
    Entitlement(markets=("TW",), report_types=("個股",)),
    Entitlement(markets=("TW",), instrument_types=("stock",)),
    Entitlement(markets=("TW",), instrument_types=("etf", "futures")),
    Entitlement(markets=("TW", "US"), sources=("元大", "凱基"), report_types=("個股",), instrument_types=("stock",)),
    Entitlement(markets=("HK",)),
]


class AllowsMatchesSqlTests(unittest.TestCase):
    def test_table_cases_agree(self):
        for ent, row in itertools.product(_ENTITLEMENTS, _ROWS):
            with self.subTest(ent=ent, row=row):
                self.assertEqual(ent.allows(**row), _eval_sql(ent, row))

    def test_null_dimensions_are_rejected_when_configured(self):
        ent = Entitlement(markets=("TW",), sources=("元大",), report_types=("個股",), instrument_types=("stock",))
        base = dict(market="TW", source="元大", report_type="個股", instrument_types=["stock"])
        self.assertTrue(ent.allows(**base))
        self.assertTrue(_eval_sql(ent, base))
        for key, null in (("source", None), ("report_type", None), ("instrument_types", None),
                          ("instrument_types", []), ("market", None)):
            row = {**base, key: null}
            with self.subTest(key=key, value=null):
                self.assertFalse(ent.allows(**row))
                self.assertFalse(_eval_sql(ent, row))

    def test_null_dimensions_pass_when_unrestricted(self):
        ent = Entitlement(markets=("TW",))
        row = dict(market="TW", source=None, report_type=None, instrument_types=None)
        self.assertTrue(ent.allows(**row))
        self.assertTrue(_eval_sql(ent, row))

    def test_table_covers_both_outcomes(self):
        outcomes = {ent.allows(**row) for ent, row in itertools.product(_ENTITLEMENTS, _ROWS)}
        self.assertEqual(outcomes, {True, False})


if __name__ == "__main__":
    unittest.main()
