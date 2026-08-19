"""基準量測腳本的純函式測試（不需要 DB）。

只測 build_coverage：SQL 本身由 DB 驗證，而百分比計算有一個真實的邊界——
空語料（新機器／重建中）會讓 total=0，除零會讓「量測腳本自己炸掉」，
那正是這類唯讀工具最不該有的故障型態。
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.measure_baseline import build_coverage  # noqa: E402


class BuildCoverageTests(unittest.TestCase):
    def test_empty_corpus_does_not_divide_by_zero(self):
        out = build_coverage(0, 0, 0, 0)
        self.assertEqual(out["total"], 0)
        self.assertEqual(out["pct_targets"], 0.0)
        self.assertEqual(out["pct_stock_code"], 0.0)
        self.assertEqual(out["pct_any"], 0.0)

    def test_percentages_rounded_to_two_places(self):
        out = build_coverage(1000, 250, 400, 500)
        self.assertEqual(out["pct_targets"], 25.0)
        self.assertEqual(out["pct_stock_code"], 40.0)
        self.assertEqual(out["pct_any"], 50.0)

    def test_repeating_decimal_is_rounded_not_truncated(self):
        # 1/3 = 33.333…％；round(…, 2) 應給 33.33 而非 33.0 或 33.34
        out = build_coverage(3, 1, 0, 1)
        self.assertEqual(out["pct_targets"], 33.33)

    def test_counts_are_passed_through_unchanged(self):
        out = build_coverage(10, 3, 4, 5)
        self.assertEqual(out["with_targets"], 3)
        self.assertEqual(out["with_stock_code"], 4)
        self.assertEqual(out["with_any"], 5)


if __name__ == "__main__":
    unittest.main()
