# tests/test_extract_takeaways_sql.py
"""純字串/結構斷言 extract_takeaways 的 SQL builder、checkpoint、解析與錨定整合。

不連 DB、不呼叫 LLM（對齊 test_extract_signals_sql.py）。
錨定測試用真的 locate_quote，只是餵手工造的正典文字。
"""
import asyncio
import importlib.util
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services.textnorm import clean_extracted  # noqa: E402

# 以檔案路徑載入 scripts/extract_takeaways.py（scripts 非套件）。
# 必須在 exec_module 前先註冊進 sys.modules：腳本內有 @dataclass，而 dataclasses
# 在裝飾時會做 sys.modules.get(cls.__module__).__dict__ 來解析 KW_ONLY 等哨兵型別，
# 未註冊會拿到 None 而在 import 期就 AttributeError。
# （test_extract_signals_sql.py 沒踩到只是因為該腳本用普通 class 而非 dataclass。）
_spec = importlib.util.spec_from_file_location(
    "extract_takeaways", REPO_ROOT / "scripts" / "extract_takeaways.py"
)
et = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = et
_spec.loader.exec_module(et)

# CLI 呼叫的實作住在共用模組（scripts/_claude_cli.py），所以失敗模式測試要 patch
# 那裡的 subprocess，不是 et 的——et 已經不再直接 import subprocess。
from scripts import _claude_cli as cc  # noqa: E402

# ── 測試語料 ──
# 刻意保留 CJK 字元間空白：research_report.full_text 就長這樣（未清理的原始抽取文字）。
RAW_FULL_TEXT = (
    "台 積 電 第 三 季 法 說 會 紀 要\n\n"
    "本 季 毛 利 率 達 五 成 九，優 於 市 場 預 期，主 要 來 自 先 進 製 程 的 產 品 組 合 改 善。\n\n"
    "公 司 上 修 全 年 資 本 支 出 至 四 百 億 美 元，反 映 AI 伺服器 需 求 強 勁。\n\n"
    "免 責 聲 明：本 報 告 僅 供 參 考。"
)
CANONICAL = clean_extracted(RAW_FULL_TEXT)
SHA = et.sha256_of(CANONICAL)

# 逐字複製自 CANONICAL（LLM 乖乖照抄的情況）
QUOTE_VERBATIM = "本季毛利率達五成九，優於市場預期，主要來自先進製程的產品組合改善。"
# CANONICAL 裡「反映 AI 伺服器」帶空白；LLM「順手」把空白拿掉 → 只能靠正規化層錨到
QUOTE_ORIG_WITH_SPACE = "公司上修全年資本支出至四百億美元，反映 AI 伺服器需求強勁。"
QUOTE_WHITESPACE_DRIFT = "公司上修全年資本支出至四百億美元，反映AI伺服器需求強勁。"
# 純屬編造，CANONICAL 裡完全沒有（連前綴都沒有）
QUOTE_FABRICATED = "本公司預計明年將推出全新的量子運算晶片產品線，並已取得多家客戶認證。"


def _parsed(*items) -> "et.ParsedTakeaways":
    return et.ParsedTakeaways(ok=True, takeaways=list(items))


class CanonicalTextTests(unittest.TestCase):
    """不變量：正典文字＝clean_extracted(full_text)，絕不是 full_text 本身。"""

    def test_canonical_strips_cjk_gaps(self):
        self.assertIn("台 積 電", RAW_FULL_TEXT)  # 原始文字保留 CJK 間空白
        self.assertNotIn("台 積 電", CANONICAL)
        self.assertIn("台積電第三季法說會紀要", CANONICAL)

    def test_canonical_keeps_paragraph_boundaries(self):
        self.assertIn("\n\n", CANONICAL)

    def test_sha_is_of_canonical_not_raw(self):
        # 兩者指紋必須不同，否則「拿 full_text 當基準」的錯誤會無聲通過
        self.assertNotEqual(SHA, et.sha256_of(RAW_FULL_TEXT))
        self.assertEqual(SHA, et.sha256_of(et.canonical_text(RAW_FULL_TEXT)))

    def test_sha_changes_when_text_changes(self):
        """全文一變 sha 就變 → checkpoint 自動失效重跑。"""
        self.assertNotEqual(SHA, et.sha256_of(CANONICAL + "補充：追加評論。"))

    def test_canonical_text_handles_none(self):
        self.assertEqual(et.canonical_text(None), "")


class SqlBuilderTests(unittest.TestCase):
    def _all_sql(self) -> list[str]:
        return [
            et.build_reports_sql(),
            et.build_existing_takeaways_sql(),
            str(et.TAKEAWAY_DELETE_SQL),
            str(et.TAKEAWAY_INSERT_SQL),
        ]

    def test_reports_sql_structure(self):
        sql = et.build_reports_sql()
        self.assertIn("FROM research.research_report", sql)
        self.assertIn("r.full_text IS NOT NULL", sql)
        self.assertIn("r.full_text <> ''", sql)
        # is_research 含 NULL（未判定的也算研報），不可寫成 = true
        self.assertIn("r.is_research IS NOT FALSE", sql)
        self.assertNotIn("is_research = true", sql)
        self.assertIn("current_date - CAST(:since_days AS int)", sql)
        # 無字串拼接
        self.assertNotIn("%s", sql)

    def test_existing_takeaways_sql(self):
        sql = et.build_existing_takeaways_sql()
        self.assertIn("FROM research.report_takeaway", sql)
        self.assertIn(":report_ids", sql)
        self.assertIn("extraction_status", sql)
        self.assertIn("extraction_version", sql)
        self.assertIn("text_sha256", sql)

    def test_no_param_name_followed_by_double_colon(self):
        """`:x::type` 的 bind 會回溯成短名 → 參數沒綁上、冒號原樣進 PG（生產 500）。

        轉型一律 CAST(:x AS ...)。此斷言釘住整檔，避免有人「順手」改回 ::。
        """
        for sql in self._all_sql():
            self.assertIsNone(
                re.search(r":\w+::", sql), f"參數名緊接 :: → {sql}"
            )

    def test_delete_sql_casts_report_id(self):
        sql = str(et.TAKEAWAY_DELETE_SQL)
        self.assertIn("DELETE FROM research.report_takeaway", sql)
        self.assertIn("report_id = CAST(:report_id AS uuid)", sql)

    def test_insert_sql_structure(self):
        sql = str(et.TAKEAWAY_INSERT_SQL)
        self.assertIn("INSERT INTO research.report_takeaway", sql)
        self.assertIn("CAST(:id AS uuid)", sql)
        self.assertIn("CAST(:report_id AS uuid)", sql)
        self.assertIn("CAST(:raw_payload AS jsonb)", sql)
        for col in ("ordinal", "claim", "quote", "quote_start", "quote_end",
                    "anchor_method", "text_sha256", "extraction_version",
                    "extraction_status", "error_detail"):
            self.assertIn(col, sql)

    def test_insert_is_not_an_upsert(self):
        """摘錄是變長列表：upsert 會留下陳舊尾列（5 條變 3 條時 ordinal 4-5 沒被清掉）。

        寫入語義必須是 delete + insert。這條測試就是為了擋「順手改成 upsert」。
        """
        self.assertNotIn("ON CONFLICT", str(et.TAKEAWAY_INSERT_SQL))


class CheckpointTests(unittest.TestCase):
    """跳過條件＝有列 + 版本相符 + sha 相符 + 狀態 ∈ (valid, partial)。"""

    def test_done_when_valid_current_version_and_sha(self):
        existing = [("valid", et.EXTRACTION_VERSION, SHA)]
        self.assertTrue(et._is_done(existing, SHA, reextract=False))

    def test_done_when_partial(self):
        """partial＝有條目錨不到，但已擷取過 → 不必重跑。"""
        existing = [("partial", et.EXTRACTION_VERSION, SHA)]
        self.assertTrue(et._is_done(existing, SHA, reextract=False))

    def test_done_when_all_rows_agree(self):
        existing = [("valid", et.EXTRACTION_VERSION, SHA)] * 4
        self.assertTrue(et._is_done(existing, SHA, reextract=False))

    def test_not_done_when_no_rows(self):
        self.assertFalse(et._is_done([], SHA, reextract=False))

    def test_not_done_when_version_mismatch(self):
        existing = [("valid", "takeaway-2020-01-01.v0", SHA)]
        self.assertFalse(et._is_done(existing, SHA, reextract=False))

    def test_not_done_when_sha_mismatch(self):
        """全文重新抽取過 → 舊 offset 已失效，必須重跑。"""
        existing = [("valid", et.EXTRACTION_VERSION, et.sha256_of("別的全文"))]
        self.assertFalse(et._is_done(existing, SHA, reextract=False))

    def test_not_done_when_any_row_stale(self):
        existing = [
            ("valid", et.EXTRACTION_VERSION, SHA),
            ("valid", "takeaway-2020-01-01.v0", SHA),  # 混到舊版本列
        ]
        self.assertFalse(et._is_done(existing, SHA, reextract=False))

    def test_not_done_when_rejected(self):
        existing = [("rejected", et.EXTRACTION_VERSION, SHA)]
        self.assertFalse(et._is_done(existing, SHA, reextract=False))

    def test_not_done_when_pending(self):
        existing = [("pending", et.EXTRACTION_VERSION, SHA)]
        self.assertFalse(et._is_done(existing, SHA, reextract=False))

    def test_reextract_forces_redo(self):
        existing = [("valid", et.EXTRACTION_VERSION, SHA)]
        self.assertFalse(et._is_done(existing, SHA, reextract=True))


class ParseTests(unittest.TestCase):
    def test_parse_normal(self):
        raw = json.dumps(
            {"takeaways": [
                {"claim": "毛利率優於預期", "quote": QUOTE_VERBATIM},
                {"claim": "資本支出上修", "quote": QUOTE_ORIG_WITH_SPACE},
            ]},
            ensure_ascii=False,
        )
        parsed = et.parse_takeaways(raw)
        self.assertTrue(parsed.ok)
        self.assertEqual(len(parsed.takeaways), 2)
        self.assertEqual(parsed.takeaways[0]["claim"], "毛利率優於預期")
        self.assertEqual(parsed.takeaways[0]["quote"], QUOTE_VERBATIM)

    def test_parse_tolerates_code_fence_and_prose(self):
        raw = '好的，以下是摘錄：\n```json\n{"takeaways": [{"claim": "毛利率優於預期", "quote": "x"}]}\n```'
        parsed = et.parse_takeaways(raw)
        self.assertTrue(parsed.ok)
        self.assertEqual(len(parsed.takeaways), 1)

    def test_parse_bad_json(self):
        """有大括號但語法壞掉 → 走 json.loads 失敗路徑（非「找不到 JSON 物件」）。"""
        parsed = et.parse_takeaways('{"takeaways": [{"claim": "壞掉的", }')
        self.assertFalse(parsed.ok)
        self.assertIn("JSON 解析失敗", parsed.error)
        self.assertEqual(parsed.takeaways, [])

    def test_parse_truncated_json_keeps_raw_text_for_log(self):
        parsed = et.parse_takeaways('{"takeaways": [{"claim": "被截斷的')
        self.assertFalse(parsed.ok)
        self.assertIn("被截斷的", parsed.raw_text)  # 原始回應留著供 log 稽核

    def test_parse_no_json_object(self):
        parsed = et.parse_takeaways("我沒有辦法讀取這份研報。")
        self.assertFalse(parsed.ok)
        self.assertEqual(parsed.error, "找不到 JSON 物件")

    def test_parse_empty_response(self):
        self.assertFalse(et.parse_takeaways("").ok)
        self.assertFalse(et.parse_takeaways("   ").ok)

    def test_parse_missing_takeaways_key(self):
        parsed = et.parse_takeaways('{"items": []}')
        self.assertFalse(parsed.ok)
        self.assertEqual(parsed.error, "缺 takeaways 陣列")

    def test_parse_empty_array_is_parseable_but_yields_no_rows(self):
        """空陣列＝JSON 合法但沒摘到東西 → ok=True、0 條 → build_rows 回 [] → rejected。"""
        parsed = et.parse_takeaways('{"takeaways": []}')
        self.assertTrue(parsed.ok)
        self.assertEqual(parsed.takeaways, [])
        self.assertEqual(et.build_rows("r1", CANONICAL, SHA, parsed), [])

    def test_parse_drops_items_without_claim(self):
        raw = json.dumps(
            {"takeaways": [
                {"quote": QUOTE_VERBATIM},           # 缺 claim → 丟棄
                {"claim": "", "quote": "x"},         # claim 空白 → 丟棄
                {"claim": "   ", "quote": "x"},      # claim 全空白 → 丟棄
                {"claim": 123, "quote": "x"},        # claim 非字串 → 丟棄
                {"claim": "留下來的論點", "quote": "x"},
            ]},
            ensure_ascii=False,
        )
        parsed = et.parse_takeaways(raw)
        self.assertTrue(parsed.ok)
        self.assertEqual([t["claim"] for t in parsed.takeaways], ["留下來的論點"])

    def test_parse_keeps_item_without_quote(self):
        """缺 quote 不丟條目：論點照樣顯示，只是不能跳（→ partial）。"""
        raw = json.dumps({"takeaways": [{"claim": "沒有引文的論點"}]}, ensure_ascii=False)
        parsed = et.parse_takeaways(raw)
        self.assertTrue(parsed.ok)
        self.assertEqual(len(parsed.takeaways), 1)
        self.assertIsNone(parsed.takeaways[0]["quote"])

    def test_parse_skips_non_dict_items(self):
        raw = json.dumps({"takeaways": ["字串", None, {"claim": "有效", "quote": "x"}]},
                         ensure_ascii=False)
        parsed = et.parse_takeaways(raw)
        self.assertEqual(len(parsed.takeaways), 1)

    def test_parse_truncates_to_five(self):
        raw = json.dumps(
            {"takeaways": [{"claim": f"論點 {i}", "quote": "x"} for i in range(9)]},
            ensure_ascii=False,
        )
        parsed = et.parse_takeaways(raw)
        self.assertEqual(len(parsed.takeaways), et.MAX_TAKEAWAYS)
        self.assertEqual(len(parsed.takeaways), 5)
        self.assertEqual(parsed.takeaways[0]["claim"], "論點 0")  # 保留前 5 條（依重要性排序）

    def test_parse_caps_runaway_claim(self):
        parsed = et.parse_takeaways(
            json.dumps({"takeaways": [{"claim": "長" * 500, "quote": "x"}]}, ensure_ascii=False)
        )
        self.assertEqual(len(parsed.takeaways[0]["claim"]), et.CLAIM_MAX)

    def test_parse_collapses_claim_whitespace_but_not_quote(self):
        """quote 的內部空白必須原樣保留，否則 exact 錨定層會失手。"""
        raw = json.dumps(
            {"takeaways": [{"claim": "毛利率\n  優於預期", "quote": "  反映 AI 伺服器需求強勁  "}]},
            ensure_ascii=False,
        )
        parsed = et.parse_takeaways(raw)
        self.assertEqual(parsed.takeaways[0]["claim"], "毛利率 優於預期")
        self.assertEqual(parsed.takeaways[0]["quote"], "反映 AI 伺服器需求強勁")

    def test_parse_top_level_not_object(self):
        parsed = et.parse_takeaways('[{"claim": "x"}]')
        self.assertFalse(parsed.ok)


class AnchorIntegrationTests(unittest.TestCase):
    """錨定整合：build_rows 用真的 locate_quote 把引文錨回正典文字。"""

    def test_verbatim_quote_anchors_exactly(self):
        rows = et.build_rows("r1", CANONICAL, SHA, _parsed(
            {"claim": "毛利率優於預期", "quote": QUOTE_VERBATIM}
        ))
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row.quote_start, CANONICAL.index(QUOTE_VERBATIM))
        self.assertEqual(row.quote_end, row.quote_start + len(QUOTE_VERBATIM))
        self.assertEqual(row.anchor_method, "exact")
        # offset 必須真的指向正典文字裡的那段字
        self.assertEqual(CANONICAL[row.quote_start:row.quote_end], QUOTE_VERBATIM)
        self.assertEqual(row.extraction_status, "valid")
        self.assertIsNone(row.error_detail)

    def test_rewritten_quote_falls_back_to_normalized(self):
        """LLM 把「反映 AI 伺服器」的空白拿掉 → exact 失手、正規化層接住。"""
        rows = et.build_rows("r1", CANONICAL, SHA, _parsed(
            {"claim": "資本支出上修", "quote": QUOTE_WHITESPACE_DRIFT}
        ))
        row = rows[0]
        self.assertEqual(row.anchor_method, "normalized")
        self.assertIsNotNone(row.quote_start)
        # 錨到的區間是「正典文字裡的原樣」（帶空白），不是 LLM 改寫後的字串
        self.assertEqual(CANONICAL[row.quote_start:row.quote_end], QUOTE_ORIG_WITH_SPACE)
        self.assertEqual(row.extraction_status, "valid")

    def test_fabricated_quote_is_partial_not_rejected(self):
        """編造的引文錨不到 → 條目仍寫入（可顯示、不可跳），整份落 partial。"""
        rows = et.build_rows("r1", CANONICAL, SHA, _parsed(
            {"claim": "編造的論點", "quote": QUOTE_FABRICATED}
        ))
        self.assertEqual(len(rows), 1)  # 不是失敗：條目照樣寫入
        row = rows[0]
        self.assertIsNone(row.quote_start)
        self.assertIsNone(row.quote_end)
        self.assertIsNone(row.anchor_method)
        self.assertEqual(row.quote, QUOTE_FABRICATED)  # 原文保留供稽核
        self.assertEqual(row.extraction_status, "partial")
        self.assertIn("錨定失敗", row.error_detail)

    def test_missing_quote_is_partial(self):
        rows = et.build_rows("r1", CANONICAL, SHA, _parsed(
            {"claim": "沒有引文的論點", "quote": None}
        ))
        row = rows[0]
        self.assertIsNone(row.quote_start)
        self.assertIsNone(row.anchor_method)
        self.assertEqual(row.extraction_status, "partial")
        self.assertEqual(row.error_detail, "LLM 未提供引文")

    def test_one_unanchorable_row_makes_whole_report_partial(self):
        rows = et.build_rows("r1", CANONICAL, SHA, _parsed(
            {"claim": "錨得到", "quote": QUOTE_VERBATIM},
            {"claim": "錨不到", "quote": QUOTE_FABRICATED},
        ))
        # 狀態是整份報告的性質：所有列共用同一個 status
        self.assertEqual([r.extraction_status for r in rows], ["partial", "partial"])
        self.assertIsNotNone(rows[0].quote_start)
        self.assertIsNone(rows[1].quote_start)

    def test_all_anchored_makes_report_valid(self):
        rows = et.build_rows("r1", CANONICAL, SHA, _parsed(
            {"claim": "一", "quote": QUOTE_VERBATIM},
            {"claim": "二", "quote": QUOTE_WHITESPACE_DRIFT},
        ))
        self.assertEqual([r.extraction_status for r in rows], ["valid", "valid"])

    def test_anchoring_against_raw_full_text_is_silently_wrong(self):
        """釘住不變量：拿未清理的 full_text 當基準**不會失敗，只會全錯**。

        直覺會以為「基準拿錯 → 錨不到 → 看得出來」。實際相反：locate_quote 的正規化
        層會把 CJK 間空白整個吃掉，所以逐字引文照樣「錨得到」（此例 method=normalized、
        start=23），只是那個 offset 屬於 full_text 的座標系。API 送給前端的是
        clean_extracted(full_text)（此例正確 start=13），拿 23 去切就切到別的字。

        沒有例外、沒有紅燈，只有跳轉位置全錯 —— 這就是本檔一取到 full_text
        就立刻轉 canonical、且全程不再碰 full_text 的唯一理由。
        """
        rows = et.build_rows("r1", RAW_FULL_TEXT, SHA, _parsed(
            {"claim": "毛利率優於預期", "quote": QUOTE_VERBATIM}
        ))
        row = rows[0]
        self.assertIsNotNone(row.quote_start)  # 錨得到 → 這種錯不會自己現形
        self.assertNotEqual(row.quote_start, CANONICAL.index(QUOTE_VERBATIM))
        # 把 full_text 座標系的 offset 套到 API 真正回傳的正典文字上 → 不是那句話
        self.assertNotEqual(CANONICAL[row.quote_start:row.quote_end], QUOTE_VERBATIM)
        # 對照組：基準正確時，切出來的就是那句話
        good = et.build_rows("r1", CANONICAL, SHA, _parsed(
            {"claim": "毛利率優於預期", "quote": QUOTE_VERBATIM}
        ))[0]
        self.assertEqual(CANONICAL[good.quote_start:good.quote_end], QUOTE_VERBATIM)

    def test_ordinals_are_contiguous_from_one(self):
        rows = et.build_rows("r1", CANONICAL, SHA, _parsed(
            {"claim": "一", "quote": QUOTE_VERBATIM},
            {"claim": "二", "quote": QUOTE_FABRICATED},
            {"claim": "三", "quote": None},
        ))
        self.assertEqual([r.ordinal for r in rows], [1, 2, 3])

    def test_rows_carry_version_sha_and_report_id(self):
        rows = et.build_rows("r1", CANONICAL, SHA, _parsed(
            {"claim": "一", "quote": QUOTE_VERBATIM}
        ))
        self.assertEqual(rows[0].report_id, "r1")
        self.assertEqual(rows[0].text_sha256, SHA)
        self.assertEqual(rows[0].extraction_version, et.EXTRACTION_VERSION)

    def test_build_rows_returns_empty_when_parse_failed(self):
        parsed = et.ParsedTakeaways(ok=False, error="CLI 無回應或逾時")
        self.assertEqual(et.build_rows("r1", CANONICAL, SHA, parsed), [])


class RowToParamsTests(unittest.TestCase):
    def _rows(self):
        return et.build_rows("11111111-1111-1111-1111-111111111111", CANONICAL, SHA,
                             _parsed({"claim": "毛利率優於預期", "quote": QUOTE_VERBATIM}))

    def test_params_cover_insert_binds(self):
        p = et.row_to_params(self._rows()[0])
        self.assertTrue(p["id"])  # 產生 uuid
        self.assertEqual(p["report_id"], "11111111-1111-1111-1111-111111111111")
        self.assertEqual(p["ordinal"], 1)
        self.assertEqual(p["claim"], "毛利率優於預期")
        self.assertEqual(p["anchor_method"], "exact")
        self.assertEqual(p["text_sha256"], SHA)
        self.assertEqual(p["extraction_status"], "valid")
        # INSERT 的每個 bind 名都要有值
        binds = set(re.findall(r":(\w+)", str(et.TAKEAWAY_INSERT_SQL)))
        self.assertEqual(binds - set(p), set())

    def test_raw_payload_serialized_to_json_str(self):
        p = et.row_to_params(self._rows()[0])
        self.assertIsInstance(p["raw_payload"], str)
        # ensure_ascii=False → 中文不轉義，且可反序列化回原結構
        self.assertEqual(json.loads(p["raw_payload"])["quote"], QUOTE_VERBATIM)
        self.assertIn("毛利率優於預期", p["raw_payload"])

    def test_ids_are_unique_per_row(self):
        row = self._rows()[0]
        self.assertNotEqual(et.row_to_params(row)["id"], et.row_to_params(row)["id"])


# 摘錄模型守門（原「不可退成更小的模型」，遷移 PR-28 依 D-A 改寫意圖）：逐字引文重準確度，改寫
# 一個字就錨不到。模型不再以「大小」判斷，而以「任一方式錨定成功率」（exact／normalized／prefix
# 任一方式錨上都算成功，分母是有 quote 的條目數）決定——9/24 探測同批研報：deepseek-flash
# 190/200 條＝95.0%、Claude 既有摘錄 180/198 條＝90.9%（分母是各自有 quote 的條目數，不是篇數；
# exact 只當觀測值：86.0% vs 39.9%）。
# 這張表只收**量過錨定率**的模型。要換成其他模型（包括 haiku 這類更小的模型、或 v4-pro），先依
# D-A 在探測集上量錨定成功率、差值 ≥ −5pp（D-N），再把結果寫進這張表——不要只為了讓測試綠而加。
ANCHOR_APPROVED_TAKEAWAY_MODELS = {
    "deepseek-flash": "9/24 探測：任一方式錨定 95.0%（190/200）",
    "claude-sonnet-5": "9/24 探測：同批研報既有摘錄任一方式錨定 90.9%（180/198）；遷移前的基準",
}


class CliArgsTests(unittest.TestCase):
    # argv 組裝（旗標、NUL 剝除）已移到 scripts/_claude_cli.py，
    # 對應斷言在 tests/test_claude_cli.py；這裡只留屬於本腳本的選擇。
    def test_default_model_is_anchoring_approved(self):
        """模組常數與預設表的摘錄模型都必須是量過錨定率的模型，不可為空、不可意外變成未量過的模型
        （理由見 ANCHOR_APPROVED_TAKEAWAY_MODELS 的註解）。"""
        from app.services import llm_models as lm

        self.assertIn(et.TAKEAWAY_MODEL_DEFAULT, ANCHOR_APPROVED_TAKEAWAY_MODELS)
        self.assertIn(lm.default_model(lm.TASK_TAKEAWAY), ANCHOR_APPROVED_TAKEAWAY_MODELS)

    def test_production_default_takeaway_model_is_flash(self):
        """生產預設（LLM_PROVIDER 未設＝deepseek）下的摘錄模型：D6 依探測與 D-A 定為 flash。"""
        from app.services import llm_models as lm

        env = {k: "" for k in lm.TASK_ENV.values()}
        self.assertEqual(lm.resolve_model(lm.TASK_TAKEAWAY, env=env), "deepseek-flash")


class PromptTests(unittest.TestCase):
    def test_prompt_states_hard_rules(self):
        prompt = et.build_takeaway_prompt("台積電_法說會.pdf", "2026-07-01", "券商甲", CANONICAL)
        self.assertIn("台積電_法說會.pdf", prompt)
        self.assertIn("2026-07-01", prompt)
        self.assertIn("券商甲", prompt)
        self.assertIn(CANONICAL, prompt)
        self.assertIn("3-5", prompt)
        self.assertIn("逐字複製", prompt)
        self.assertIn("takeaways", prompt)

    def test_prompt_omits_absent_metadata(self):
        prompt = et.build_takeaway_prompt("x.pdf", None, None, "內文")
        self.assertNotIn("報告日：", prompt)
        self.assertNotIn("券商：", prompt)


class CliFailureTests(unittest.TestCase):
    """CLI 失敗原因必須可區分。

    曾經是 `except Exception: return None` —— 於是「claude 不在 PATH」（systemd 下
    實際發生過）、逾時、OOM 全被寫成同一句「CLI 無回應或逾時」，整批 549 篇全滅
    卻還是正常結束、exit 0，只留下一行 ok=0 rejected=549，看不出該修 PATH 還是該
    調 timeout。
    """

    def _raises(self, exc):
        return mock.patch.object(cc.subprocess, "run", side_effect=exc)

    def test_success_returns_stdout_and_no_error(self):
        done = subprocess.CompletedProcess(args=[], returncode=0, stdout="OUT", stderr="")
        with mock.patch.object(cc.subprocess, "run", return_value=done):
            res = et.call_cli("prompt", "model")
        self.assertEqual(res.text, "OUT")
        self.assertIsNone(res.error)

    def test_missing_cli_raises_instead_of_returning_none(self):
        # 環境層級失敗：每篇都會踩到，必須往上拋以中止整批，
        # 而不是靜靜地把每一篇都記成 rejected
        with self._raises(FileNotFoundError(2, "No such file or directory", "claude")):
            with self.assertRaises(et.CliNotFoundError) as ctx:
                et.call_cli("prompt", "model")
        msg = str(ctx.exception)
        self.assertIn("claude", msg)
        self.assertIn("PATH", msg)  # 訊息要直接指出真因

    def test_timeout_is_reported_as_timeout(self):
        with self._raises(subprocess.TimeoutExpired(cmd="claude", timeout=180)):
            res = et.call_cli("prompt", "model", timeout=180)
        self.assertIsNone(res.text)
        self.assertIn("逾時", res.error)
        self.assertIn("180", res.error)

    def test_nonzero_exit_reports_code_and_stderr(self):
        fail = subprocess.CompletedProcess(
            args=[], returncode=3, stdout="", stderr="usage: unknown flag\n"
        )
        with mock.patch.object(cc.subprocess, "run", return_value=fail):
            res = et.call_cli("prompt", "model")
        self.assertIsNone(res.text)
        self.assertIn("3", res.error)
        self.assertIn("unknown flag", res.error)

    def test_other_exception_is_reported_with_its_type(self):
        with self._raises(OSError("Cannot allocate memory")):
            res = et.call_cli("prompt", "model")
        self.assertIsNone(res.text)
        self.assertIn("OSError", res.error)
        self.assertNotIn("逾時", res.error)  # 不得與逾時混為一談

    def test_failure_reasons_are_mutually_distinguishable(self):
        """三種失敗不可再塌縮成同一句話（這正是原缺陷的本體）。"""
        with self._raises(subprocess.TimeoutExpired(cmd="claude", timeout=180)):
            timeout_err = et.call_cli("p", "m").error
        with self._raises(OSError("boom")):
            other_err = et.call_cli("p", "m").error
        fail = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="e")
        with mock.patch.object(cc.subprocess, "run", return_value=fail):
            exit_err = et.call_cli("p", "m").error
        self.assertEqual(len({timeout_err, other_err, exit_err}), 3)


class CliAbortTests(unittest.IsolatedAsyncioTestCase):
    """`claude` 不在 PATH → 提早中止整批，不跑完 N 次註定失敗的呼叫。"""

    @staticmethod
    def _item():
        return et.WorkItem("rep-1", "台積電_法說會.pdf", None, "券商甲", CANONICAL, SHA)

    async def test_missing_cli_propagates_out_of_extract_one(self):
        # 不可被單筆的 try/except 吞掉：吞了就會繼續跑完整批、每篇都 rejected
        before = et._rejected
        with mock.patch.object(et, "call_cli", side_effect=et.CliNotFoundError("不在 PATH")):
            with self.assertRaises(et.CliNotFoundError):
                await et.extract_one(asyncio.Semaphore(1), self._item(), 24000, "m", 1)
        self.assertEqual(et._rejected, before)  # 不該被記成「這一篇擷取失敗」

    async def test_timeout_reason_reaches_failure_log(self):
        # 逾時仍屬單篇失敗：不中斷長跑，但 log 要說得出真因（而非「CLI 無回應或逾時」）
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "takeaway_failures.log"
            with mock.patch.object(et, "FAIL_LOG", log), \
                 mock.patch.object(et, "call_cli",
                                   return_value=et.CliResult(None, "CLI 逾時（180s 內未回應）")):
                await et.extract_one(asyncio.Semaphore(1), self._item(), 24000, "m", 1)
            written = log.read_text(encoding="utf-8")
        self.assertIn("rep-1", written)
        self.assertIn("逾時", written)



class RawPayloadModelTests(unittest.IsolatedAsyncioTestCase):
    """raw_payload.model 記**實際產出**的模型（遷移 PR-15）：HTTP 取回應的 model 欄，CLI 退回請求的。"""

    def test_build_rows_records_model_and_keeps_quote(self):
        rows = et.build_rows("r1", CANONICAL, SHA, _parsed({"claim": "毛利率優於預期", "quote": QUOTE_VERBATIM}),
                             model="deepseek-flash")
        self.assertEqual(rows[0].raw_payload, {"claim": "毛利率優於預期", "quote": QUOTE_VERBATIM,
                                               "model": "deepseek-flash"})

    def test_build_rows_without_model_has_no_key(self):
        rows = et.build_rows("r1", CANONICAL, SHA, _parsed({"claim": "毛利率優於預期", "quote": QUOTE_VERBATIM}))
        self.assertNotIn("model", rows[0].raw_payload)

    async def _run(self, results):
        captured = {}

        def fake_build_rows(*a, **k):
            captured.update(k)
            return []

        it = iter(results)
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(et, "FAIL_LOG", Path(tmp) / "f.log"), \
             mock.patch.object(et, "call_cli", side_effect=lambda *a, **k: next(it)), \
             mock.patch.object(et, "build_rows", side_effect=fake_build_rows):
            item = et.WorkItem("rep-1", "f.pdf", None, "券商甲", CANONICAL, SHA)
            await et.extract_one(asyncio.Semaphore(1), item, 24000, "deepseek-flash", 1)
        return captured.get("model")

    async def test_response_model_wins(self):
        ok = et.CliResult('{"takeaways": []}', None, "deepseek-flash-0925")
        self.assertEqual(await self._run([ok]), "deepseek-flash-0925")

    async def test_falls_back_to_requested_model(self):
        ok = et.CliResult('{"takeaways": []}', None)  # CLI 路徑沒有回應的 model 欄
        self.assertEqual(await self._run([ok]), "deepseek-flash")

    async def test_no_response_no_model(self):
        err = et.CliResult(None, "CLI 逾時（180s 內未回應）")
        self.assertIsNone(await self._run([err, err, err]))


if __name__ == "__main__":
    unittest.main()
