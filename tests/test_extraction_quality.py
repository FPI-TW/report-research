# tests/test_extraction_quality.py
"""品質指標的算術與邊界。合成 Block，不碰真 PDF、不 render。

`quality.py` 是 §5 指標的**唯一計算來源**，所以它算錯的話，profiling 分佈、
比對報告、以及未來 `E1` 寫進 `research_report.quality_flags` 的值會一起錯，
而且錯得一致——一致的錯誤沒有任何交叉比對抓得到。
"""
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services.extraction.model import Block, Document, Page  # noqa: E402
from app.services.extraction.quality import garbled_ratio, measure  # noqa: E402


def _page(page_no: int, text: str, *, failed: bool = False, column: int = 0) -> Page:
    blocks = (
        ()
        if failed
        else (Block("paragraph", page_no, 0, (0.0, 0.0, 100.0, 10.0), text, column=column),)
    )
    return Page(page_no, 595.0, 842.0, blocks, failed=failed)


def _doc(*pages: Page) -> Document:
    return Document("test", "v0", tuple(pages))


class GarbledRatioTests(unittest.TestCase):
    def test_clean_text_is_zero(self):
        self.assertEqual(garbled_ratio("台積電第三季營收優於預期"), 0.0)

    def test_empty_is_zero_not_division_error(self):
        self.assertEqual(garbled_ratio(""), 0.0)

    def test_private_use_area_counts(self):
        self.assertAlmostEqual(garbled_ratio("ab"), 0.5)

    def test_replacement_char_counts(self):
        self.assertAlmostEqual(garbled_ratio("ab��"), 0.5)

    def test_rare_cjk_does_not_count(self):
        """v1 的定義把「非常用 CJK」算成亂碼，實測會把券商名與人名的罕用字
        誤判。真正的亂碼幾乎都落在私用區，所以刻意不含這一類。"""
        self.assertEqual(garbled_ratio("龘齾爩鱻"), 0.0)


class MeasureTests(unittest.TestCase):
    def test_page_count_and_chars(self):
        q = measure(_doc(_page(1, "a" * 100), _page(2, "b" * 300)))
        self.assertEqual(q.page_count, 2)
        # 兩個 Block 之間有 "\n\n"
        self.assertEqual(q.char_count, 402)
        self.assertEqual(q.chars_per_page, 201.0)

    def test_failed_pages_counted_and_ratio(self):
        q = measure(_doc(_page(1, "ok"), _page(2, "", failed=True), _page(3, "ok")))
        self.assertEqual(q.pages_failed, (2,))
        self.assertAlmostEqual(q.pages_failed_ratio, 1 / 3, places=3)

    def test_empty_document_does_not_divide_by_zero(self):
        q = measure(_doc())
        self.assertEqual(q.page_count, 0)
        self.assertEqual(q.chars_per_page, 0.0)
        self.assertEqual(q.pages_failed_ratio, 1.0)

    def test_columns_reported(self):
        doc = _doc(
            Page(
                1,
                595.0,
                842.0,
                (
                    Block("paragraph", 1, 0, (0.0, 0.0, 200.0, 10.0), "L", column=0),
                    Block("paragraph", 1, 1, (300.0, 0.0, 500.0, 10.0), "R", column=1),
                ),
            ),
            _page(2, "單欄"),
        )
        q = measure(doc)
        self.assertEqual(q.max_columns, 2)
        self.assertEqual(q.multi_column_pages, 1)

    def test_layout_coverage_is_none_without_renderer(self):
        """None ＝ 沒開 render。它**不可以**與「開了但壞了」混同——後者現在
        會 log warning 而不是靜默變 None（開發時實際踩過）。"""
        self.assertIsNone(measure(_doc(_page(1, "x" * 500))).layout_coverage)

    def test_flags_are_json_safe(self):
        import json

        flags = measure(_doc(_page(1, "x" * 500), _page(2, "", failed=True))).as_flags()
        self.assertIsInstance(flags["pages_failed"], list)
        json.dumps(flags)  # 會寫進 jsonb，不可以有 tuple


class QualityScoreTests(unittest.TestCase):
    def test_score_within_unit_interval(self):
        for doc in (
            _doc(),
            _doc(_page(1, "")),
            _doc(_page(1, "x" * 10000)),
            _doc(*[_page(i, "", failed=True) for i in range(1, 6)]),
        ):
            self.assertGreaterEqual(measure(doc).quality_score, 0.0)
            self.assertLessEqual(measure(doc).quality_score, 1.0)

    def test_garbled_lowers_score(self):
        clean = measure(_doc(_page(1, "正常內容" * 100)))
        dirty = measure(_doc(_page(1, "" * 400)))
        self.assertLess(dirty.quality_score, clean.quality_score)

    def test_failed_pages_lower_score(self):
        good = measure(_doc(_page(1, "x" * 500), _page(2, "x" * 500)))
        bad = measure(_doc(_page(1, "x" * 500), _page(2, "", failed=True)))
        self.assertLess(bad.quality_score, good.quality_score)

    def test_sparse_pages_lower_score(self):
        """「30 頁只抽到 3 頁」要看得出來——那正是現況看不出來的東西。"""
        dense = measure(_doc(*[_page(i, "x" * 1000) for i in range(1, 11)]))
        sparse = measure(_doc(*[_page(i, "x" * 5) for i in range(1, 11)]))
        self.assertLess(sparse.quality_score, dense.quality_score)


if __name__ == "__main__":
    unittest.main()
