# tests/test_tagging.py
"""`app/services/tagging.py`：市場代碼映射與 tag JSON 解析。

**為什麼這支測試存在**：`tagging.py` 先前只有 `MARKETS` 這個常數被別的測試 import
（`test_radar_queries.py` 拿它當市場清單），映射邏輯本身**零 assert**。而它是
ingest 的閘門——`ingest_all.py` / `sync_new_reports.py` / `run_ingest.py` 三處都是
`if not tag.is_research or not tag.market: continue`，所以：

    映射漏一個值 → market 回 None → 整批研報靜默不入庫

沒有例外、沒有 log、沒有任何症狀，只有「那批報告搜不到」。CLAUDE.md 用一整段強調
市場代碼要對齊 findb，卻沒有一條測試釘住它。

**測試對象是「對齊 findb」這個契約本身**，不是實作細節：代碼清單、中文舊標籤的
對照、以及兩個刻意的降級（債券→MACRO、原物料→GLOBAL，findb 沒有那兩個市場）。
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services.tagging import (  # noqa: E402
    LEGACY_TO_FINDB,
    MARKET_DISPLAY,
    MARKETS,
    STOCK_TARGETS_MAX,
    load_tag,
    normalize_futures_targets,
    normalize_instruments,
    normalize_market,
    normalize_stock_targets,
    parse_tags,
)


class MarketVocabularyTests(unittest.TestCase):
    """代碼清單是**與 findb 的跨 repo 契約**，不是本 repo 想改就能改的東西。"""

    def test_exact_findb_market_codes(self):
        # 逐字寫死：CLAUDE.md、README、docs/WORKFLOW.md 都複述這一組，
        # 而「多一個」或「少一個」的後果是 ingest 靜默丟棄或寫進 findb 不認的代碼。
        self.assertEqual(
            MARKETS,
            ["TW", "US", "HK", "CN", "FX", "WTX", "MACRO", "GLOBAL", "CRYPTO"],
        )

    def test_every_code_has_a_display_name(self):
        """缺一個顯示名稱不會壞，只會讓前端印出裸代碼——所以要測。"""
        self.assertEqual(sorted(MARKET_DISPLAY), sorted(MARKETS))

    def test_every_legacy_label_maps_into_the_vocabulary(self):
        """舊標籤映到詞表外＝`make align` 之後那些列的 market 變 None。"""
        for label, code in LEGACY_TO_FINDB.items():
            with self.subTest(label=label):
                self.assertIn(code, MARKETS)

    def test_deliberate_downgrades_are_pinned(self):
        """findb 沒有債券與原物料市場，故歸最接近者。

        這兩條是**決定**而不是巧合（CLAUDE.md 與 tagging.py 檔頭都記載），
        改掉它們等於改變既有 1.4 萬列的分類語意。
        """
        self.assertEqual(LEGACY_TO_FINDB["債券"], "MACRO")
        self.assertEqual(LEGACY_TO_FINDB["原物料"], "GLOBAL")


class NormalizeMarketTests(unittest.TestCase):
    def test_code_passthrough_and_case_insensitive(self):
        for raw, want in (("TW", "TW"), ("tw", "TW"), ("  us  ", "US")):
            with self.subTest(raw=raw):
                self.assertEqual(normalize_market(raw), want)

    def test_legacy_chinese_labels(self):
        self.assertEqual(normalize_market("台股"), "TW")
        self.assertEqual(normalize_market("期貨"), "WTX")
        self.assertEqual(normalize_market("總體經濟"), "MACRO")

    def test_unknown_returns_none_not_a_guess(self):
        """認不出來必須回 None（＝ingest 跳過），不可猜一個代碼。

        猜錯的後果比丟掉更糟：報告會被歸到錯的市場，而檢索頁的市場篩選看起來
        完全正常。
        """
        for raw in ("日股", "JP", "", None, "   ", "GLOBALX"):
            with self.subTest(raw=raw):
                self.assertIsNone(normalize_market(raw))


class NormalizeInstrumentsTests(unittest.TestCase):
    def test_lowercases_dedups_and_preserves_order(self):
        self.assertEqual(
            normalize_instruments(["Equity", "INDEX", "equity"]), ["equity", "index"]
        )

    def test_drops_out_of_vocabulary_and_non_strings(self):
        self.assertEqual(normalize_instruments(["equity", "股票", 42, None]), ["equity"])

    def test_non_list_returns_empty(self):
        for raw in ("equity", None, {"a": 1}, 7):
            with self.subTest(raw=raw):
                self.assertEqual(normalize_instruments(raw), [])


class NormalizeStockTargetsTests(unittest.TestCase):
    """「正好 4 碼數字」是防呆，擋日期與頁碼被誤當股票代碼。"""

    def test_keeps_four_digit_codes_only(self):
        self.assertEqual(
            normalize_stock_targets(["2330", "0050", "233", "23300", "2330A"]),
            ["2330", "0050"],
        )

    def test_dedups_preserving_order(self):
        self.assertEqual(normalize_stock_targets(["2330", "2303", "2330"]), ["2330", "2303"])

    def test_accepts_non_string_items_by_coercion(self):
        """LLM 有時回數字而非字串——`2330` 與 `"2330"` 必須等價。"""
        self.assertEqual(normalize_stock_targets([2330]), ["2330"])

    def test_truncates_to_max(self):
        many = [f"{i:04d}" for i in range(1, STOCK_TARGETS_MAX + 10)]
        self.assertEqual(len(normalize_stock_targets(many)), STOCK_TARGETS_MAX)

    def test_non_list_returns_empty(self):
        self.assertEqual(normalize_stock_targets("2330"), [])


class NormalizeFuturesTargetsTests(unittest.TestCase):
    def test_vocabulary_only(self):
        from app.services.tagging import FUTURES_TARGETS

        inside = FUTURES_TARGETS[0]
        self.assertEqual(normalize_futures_targets([inside, "不存在的期貨"]), [inside])

    def test_non_list_returns_empty(self):
        self.assertEqual(normalize_futures_targets(None), [])


class ParseTagsTests(unittest.TestCase):
    """容錯解析 LLM 回應。每一條都對應一種實際見過的輸出形狀。"""

    @staticmethod
    def _obj(**kw):
        base = {"market": "TW", "is_research": True, "confidence": 0.9}
        base.update(kw)
        return json.dumps(base, ensure_ascii=False)

    def test_plain_json(self):
        tag = parse_tags(self._obj())
        self.assertEqual((tag.market, tag.is_research, tag.confidence), ("TW", True, 0.9))

    def test_fenced_json(self):
        tag = parse_tags("```json\n" + self._obj() + "\n```")
        self.assertEqual(tag.market, "TW")

    def test_prose_around_json(self):
        """CLI 有時在 JSON 前後加說明文字。"""
        tag = parse_tags("我讀完了，結果如下：\n" + self._obj() + "\n以上。")
        self.assertEqual(tag.market, "TW")

    def test_malformed_returns_none(self):
        for raw in ("", "沒有 JSON", "{不是合法 json}", "{", "}"):
            with self.subTest(raw=raw):
                self.assertIsNone(parse_tags(raw))

    def test_market_is_normalized_not_passed_through(self):
        """中文標籤要在這裡就轉成代碼，否則會原樣寫進 DB。"""
        self.assertEqual(parse_tags(self._obj(market="台股")).market, "TW")

    def test_is_research_defaults_to_whether_market_resolved(self):
        """沒帶 `is_research` 時以「市場認得出來嗎」推定——這是既有的預設語意。

        釘住它是因為改成固定 `True` 會讓行政檔全部入庫，改成固定 `False` 會讓
        沒帶該欄位的整批研報全部被丟掉。兩個方向都是靜默的。
        """
        with_market = json.dumps({"market": "TW", "confidence": 0.5})
        self.assertTrue(parse_tags(with_market).is_research)
        no_market = json.dumps({"market": "日股", "confidence": 0.5})
        self.assertFalse(parse_tags(no_market).is_research)

    def test_bad_confidence_falls_back_to_zero(self):
        for bad in ("很高", None, [], {}):
            with self.subTest(bad=bad):
                self.assertEqual(parse_tags(self._obj(confidence=bad)).confidence, 0.0)

    def test_relates_flags_distinguish_absent_from_false(self):
        """缺欄位是 `None`（未判定），明確的 false 是 `False`——DB 欄位可 NULL。

        兩者混用會讓「標註器沒說」與「標註器說沒有」在資料層無法區分。
        """
        absent = parse_tags(json.dumps({"market": "TW"}))
        self.assertIsNone(absent.relates_stock)
        self.assertIsNone(absent.relates_futures)
        explicit = parse_tags(self._obj(relates_stock=False, relates_futures=True))
        self.assertIs(explicit.relates_stock, False)
        self.assertIs(explicit.relates_futures, True)

    def test_targets_are_normalized_through_parse(self):
        tag = parse_tags(self._obj(stock_targets=["2330", "頁 6"], instrument_types=["Equity"]))
        self.assertEqual(tag.stock_targets, ["2330"])
        self.assertEqual(tag.instrument_types, ["equity"])


class LoadTagTests(unittest.TestCase):
    def test_missing_file_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(load_tag(Path(d), "a" * 64))

    def test_reads_and_parses(self):
        with tempfile.TemporaryDirectory() as d:
            h = "b" * 64
            (Path(d) / f"{h}.json").write_text(
                json.dumps({"market": "美股", "is_research": True}), encoding="utf-8"
            )
            self.assertEqual(load_tag(Path(d), h).market, "US")

    def test_corrupt_file_returns_none_not_raises(self):
        """壞掉的 tag 檔不該讓整批 ingest 中斷——那批有 1.4 萬個檔。"""
        with tempfile.TemporaryDirectory() as d:
            h = "c" * 64
            (Path(d) / f"{h}.json").write_text("{壞", encoding="utf-8")
            self.assertIsNone(load_tag(Path(d), h))


class IngestGateContractTests(unittest.TestCase):
    """三處 ingest 入口共用同一個閘門條件，這裡把那個條件的語意釘住。

    `if not tag.is_research or not tag.market: continue`——**兩個條件都是 OR**，
    所以「是研報但市場認不出來」也會被丟掉。那是刻意的（沒有市場的列在檢索頁的
    市場篩選裡是幽靈），但它也意味著映射表漏一個值就是靜默丟整批。
    """

    def test_research_without_market_is_gated_out(self):
        tag = parse_tags(json.dumps({"market": "日股", "is_research": True}))
        self.assertTrue(tag.is_research)
        self.assertIsNone(tag.market)
        self.assertTrue(not tag.is_research or not tag.market, "應被閘門擋下")

    def test_market_without_research_is_gated_out(self):
        tag = parse_tags(json.dumps({"market": "TW", "is_research": False}))
        self.assertTrue(not tag.is_research or not tag.market, "應被閘門擋下")

    def test_both_present_passes(self):
        tag = parse_tags(json.dumps({"market": "TW", "is_research": True}))
        self.assertFalse(not tag.is_research or not tag.market)


if __name__ == "__main__":
    unittest.main()
