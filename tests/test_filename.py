# tests/test_filename.py
import sys
import unittest
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

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


if __name__ == "__main__":
    unittest.main()
