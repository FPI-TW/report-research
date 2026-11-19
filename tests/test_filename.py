# tests/test_filename.py
import os
import sys
import tempfile
import textwrap
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app import profile as P  # noqa: E402
from app.services.filename import (  # noqa: E402
    mtime_report_date,
    parse_filename,
)


class FilenameDateTests(unittest.TestCase):
    def _d(self, name):
        return parse_filename(name).report_date

    # --- 既有格式：YYYYMMDD（8 位連續數字），不可回歸 ---
    def test_yyyymmdd_compact(self):
        self.assertEqual(
            self._d("2025年半導體產業展望-凱基20241223.pdf"), date(2024, 12, 23)
        )

    # --- 底線/連字號分隔的 YYYY_MM_DD（語料最大宗，48%）---
    def test_yyyy_underscore_mm_dd(self):
        self.assertEqual(
            self._d("daily story_Semiconductor sector_2025_05_04_C_TW.pdf"),
            date(2025, 5, 4),
        )

    def test_yyyy_hyphen_mm_dd(self):
        self.assertEqual(self._d("Radar_2026-06-15_C_TW.pdf"), date(2026, 6, 15))

    def test_year_anchored_ignores_stock_code_prefix(self):
        # 1503 是股票代碼，不可被當成日期的一部分；應抽到 2024_08_23
        self.assertEqual(self._d("1503_2024_08_23_C_TW.pdf"), date(2024, 8, 23))

    # --- MMDDYYYY（如 晨訊03252025）---
    def test_mmddyyyy(self):
        self.assertEqual(self._d("晨訊03252025.pdf"), date(2025, 3, 25))

    # --- 6 位數：唯一可解者採用 ---
    def test_six_digit_yymmdd_unique(self):
        # 240510：YYMMDD=2024-05-10 有效；MMDDYY 月份=24 無效 → 唯一解
        self.assertEqual(self._d("策略新聞240510.pdf"), date(2024, 5, 10))

    def test_six_digit_mmddyy_unique(self):
        # 093024：MMDDYY=2024-09-30 有效；YYMMDD 月份=30 無效 → 唯一解
        self.assertEqual(
            self._d("10月產業投資策略_Strategy_c_093024.pdf"), date(2024, 9, 30)
        )

    def test_six_digit_both_valid_is_ambiguous(self):
        # 040506：YYMMDD=2004-05-06 與 MMDDYY=04/05/2006 皆有效 → 歧義，純檔名不猜
        self.assertIsNone(self._d("note040506.pdf"))

    # --- 只有 MMDD（無年份）→ 純檔名無法決定，回 None（交由內文補）---
    def test_mmdd_only_returns_none(self):
        self.assertIsNone(self._d("0330 債券雙週報.pdf"))

    # --- 無可解日期 ---
    def test_no_date_returns_none(self):
        self.assertIsNone(self._d("ued05064.pdf"))

    def test_invalid_month_day_rejected(self):
        # 20259999 不是合法日期
        self.assertIsNone(self._d("foo20259999.pdf"))


class MtimeReportDateTests(unittest.TestCase):
    def test_uses_mtime_when_far_from_ingest(self):
        # 檔案 mtime 與匯入時間相距夠遠 → 視為真實出版日，採用
        self.assertEqual(
            mtime_report_date(date(2024, 8, 19), date(2026, 6, 10)),
            date(2024, 8, 19),
        )

    def test_rejects_mtime_close_to_ingest(self):
        # mtime 與 created_at 相差 ≤2 天 → 視為批次複製時間，不可信 → None
        self.assertIsNone(mtime_report_date(date(2026, 6, 11), date(2026, 6, 10)))

    def test_none_mtime_returns_none(self):
        self.assertIsNone(mtime_report_date(None, date(2026, 6, 10)))

    def test_no_created_at_trusts_mtime(self):
        # 無 created_at 參照 → 直接採用 mtime（如即時匯入路徑）
        self.assertEqual(
            mtime_report_date(date(2025, 1, 2), None), date(2025, 1, 2)
        )


class _WithProfile(unittest.TestCase):
    """以指定的 profile 目錄解析（`PROFILE_DIR`＋清快取）；`PROFILE` 是 None 時寫一份只帶 `INSTRUMENTS`（其他
    section 也可以）的暫存 profile。"""

    PROFILE: Path | None = None
    INSTRUMENTS = ""

    def setUp(self):
        self._tmp = None
        root = self.PROFILE
        if root is None:
            self._tmp = tempfile.TemporaryDirectory()
            root = Path(self._tmp.name)
            (root / "profile.toml").write_text(
                '[institution]\nname = "測試"\nproduct_name = "測試平台"\n' + textwrap.dedent(self.INSTRUMENTS),
                encoding="utf-8")
        self._env = mock.patch.dict(os.environ, {"PROFILE_DIR": str(root)})
        self._env.start()
        P.reset_profile_cache()

    def tearDown(self):
        self._env.stop()
        P.reset_profile_cache()
        if self._tmp:
            self._tmp.cleanup()

    def code(self, name):
        meta = parse_filename(name)
        return meta.stock_code, meta.company_name, [p for p in meta.matched_patterns if p.startswith(("NAME", "CODE"))]


class TingfongStockCodeTests(_WithProfile):
    """廷豐（＝section 缺席的框架預設）：正好 4 碼數字，行為與框架化之前的寫死正則逐字相同。"""

    PROFILE = REPO_ROOT / "profiles" / "tingfong"

    def test_four_digit_forms(self):
        self.assertEqual(self.code("鴻海(2317)_報告.pdf"), ("2317", "鴻海", ["NAME_CODE"]))
        self.assertEqual(self.code("台積電 (2330) 更新.pdf"), ("2330", "台積電", ["NAME_CODE"]))
        self.assertEqual(self.code("1102亞泥_評析.pdf"), ("1102", "亞泥", ["CODE_NAME"]))
        self.assertEqual(self.code("1216 TT 統一.pdf"), ("1216", None, ["CODE_TT"]))
        self.assertEqual(self.code("1216 tt x.pdf"), ("1216", None, ["CODE_TT"]))

    def test_other_lengths_and_tickers_ignored(self):
        for name in ("00878國泰.pdf", "台積電(00878).pdf", "12345台積電.pdf", "12345 TT.pdf", "006208 TT 富邦.pdf",
                     "20250504台積電.pdf", "2330.pdf", "abc 1234 TT.pdf", "NVDA US Equity.pdf", "AAPL US.pdf"):
            with self.subTest(name=name):
                self.assertEqual(self.code(name), (None, None, []))

    def test_section_absent_is_the_same_rule(self):
        self.assertEqual(P.load_profile(self.PROFILE).instruments, P.Instruments())


class DemoStockCodeTests(_WithProfile):
    """示範券商：台股 4～6 碼數字（TT 寫法同樣適用）＋美股 1～5 碼字母含股別後綴（認 `<代號> US` 開頭）。"""

    PROFILE = REPO_ROOT / "profiles" / "demo"

    def test_tw_digit_range(self):
        self.assertEqual(self.code("00878國泰.pdf"), ("00878", "國泰", ["CODE_NAME"]))
        self.assertEqual(self.code("國泰永續高股息(00878).pdf"), ("00878", "國泰永續高股息", ["NAME_CODE"]))
        self.assertEqual(self.code("006208 TT 富邦.pdf"), ("006208", None, ["CODE_TT"]))
        self.assertEqual(self.code("1102亞泥_評析.pdf"), ("1102", "亞泥", ["CODE_NAME"]))
        self.assertEqual(self.code("20250504台積電.pdf"), (None, None, []))  # 8 位數是日期，不是代號

    def test_us_tickers_at_the_start(self):
        self.assertEqual(self.code("NVDA US Equity.pdf"), ("NVDA", None, ["CODE_US"]))
        self.assertEqual(self.code("F US autos.pdf"), ("F", None, ["CODE_US"]))
        self.assertEqual(self.code("BRK/B US.pdf"), ("BRK.B", None, ["CODE_US"]))
        self.assertEqual(self.code("BRK.B US 2026Q3.pdf"), ("BRK.B", None, ["CODE_US"]))

    def test_us_forms_that_are_not_tickers(self):
        for name in ("the us economy.pdf", "Nvda US.pdf", "NVIDIA US outlook.pdf", "Weekly NVDA US.pdf",
                     "NVDA USD flows.pdf", "NVDA_US.pdf", "20251104_cobalt_nvda_17.pdf"):
            with self.subTest(name=name):
                self.assertEqual(self.code(name), (None, None, []))


class LettersOnlyStockCodeTests(_WithProfile):
    """只宣告美股格式：中文名＋數字與 TT 寫法都不認（沒有純數字格式）。"""

    INSTRUMENTS = """
        [instruments.code_formats.US]
        kind = "letters"
        min_length = 1
        max_length = 5
    """

    def test_digit_forms_disabled(self):
        for name in ("鴻海(2317)_報告.pdf", "1102亞泥.pdf", "1216 TT 統一.pdf", "BRK.B US.pdf"):
            with self.subTest(name=name):
                self.assertEqual(self.code(name), (None, None, []))
        self.assertEqual(self.code("AAPL US.pdf"), ("AAPL", None, ["CODE_US"]))


class MixedDigitFormatsTests(_WithProfile):
    """台股 4 碼＋陸股 6 碼：長度範圍是聯集（4～6），但 5 碼哪一個格式都不收，最後的格式驗證擋掉；
    TT 只照 TW 的格式。"""

    INSTRUMENTS = """
        [instruments.code_formats.TW]
        kind = "digits"
        min_length = 4
        max_length = 4
        [instruments.code_formats.CN]
        kind = "digits"
        min_length = 6
        max_length = 6
    """

    def test_union_then_validated(self):
        self.assertEqual(self.code("貴州茅台(600519).pdf"), ("600519", "貴州茅台", ["NAME_CODE"]))
        self.assertEqual(self.code("某公司(12345) 與 鴻海(2317).pdf"), ("2317", "鴻海", ["NAME_CODE"]))
        self.assertEqual(self.code("12345台積電.pdf"), (None, None, []))
        self.assertEqual(self.code("600519 TT.pdf"), (None, None, []))



class TingfongAdminKeywordTests(_WithProfile):
    """行政文件的檔名關鍵字來自 profile 的 `[filenames]`；廷豐宣告的是框架預設（框架化之前寫死的清單）。"""

    PROFILE = REPO_ROOT / "profiles" / "tingfong"

    def test_keywords(self):
        for name in ("人事異動公告.pdf", "Webcast flyer.pdf", "期交所部位限制.pdf", "大樓施工通知.pdf"):
            with self.subTest(name=name):
                self.assertTrue(parse_filename(name).is_admin)
        self.assertFalse(parse_filename("Org chart 2026.pdf").is_admin)
        self.assertFalse(parse_filename("鴻海(2317)_報告.pdf").is_admin)


class DemoAdminKeywordTests(_WithProfile):
    PROFILE = REPO_ROOT / "profiles" / "demo"

    def test_keywords(self):
        for name in ("Org Chart 2026.pdf", "HOLIDAY SCHEDULE.pdf", "人事異動公告.pdf"):
            with self.subTest(name=name):
                self.assertTrue(parse_filename(name).is_admin)
        for name in ("期交所部位限制.pdf", "TSMC earnings announcement review.pdf", "20251104_cobalt_nvda_17.pdf"):
            with self.subTest(name=name):
                self.assertFalse(parse_filename(name).is_admin)


class EmptyAdminKeywordTests(_WithProfile):
    INSTRUMENTS = "[filenames]\nadmin_keywords = []\n"

    def test_empty_list_disables_filename_detection(self):
        self.assertFalse(parse_filename("人事異動公告.pdf").is_admin)


if __name__ == "__main__":
    unittest.main()
