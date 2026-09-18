# tests/test_extraction_quality_v4.py
"""品質指標 v4：coverage 缺席時權重重正規化（不再白送 0.15），版面統計進 quality_flags，
`store.needs_review` 逐項旗標門檻。

v3 生產路徑從未傳 renderer，coverage 每篇都是 None、每篇都拿滿分那 0.15：9,263 篇全部
≥ 0.9、5,804 篇恰好 1.0、`needs_review` 零篇。這裡釘住的是「沒量到的項目不能加分」。
"""
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services import store  # noqa: E402
from app.services.extraction.model import Block, Document, Page  # noqa: E402
from app.services.extraction.quality import measure  # noqa: E402


def _page(page_no: int, text: str) -> Page:
    return Page(page_no, 595.0, 842.0, (Block("paragraph", page_no, 0, (0.0, 0.0, 100.0, 10.0), text),))


class ScoreNormalisationTests(unittest.TestCase):
    def test_perfect_document_without_coverage_scores_one(self):
        q = measure(Document("t", "v", (_page(1, "x" * 1000),)))
        self.assertIsNone(q.layout_coverage)
        self.assertAlmostEqual(q.quality_score, 1.0, places=4)

    def test_missing_coverage_does_not_mask_other_defects(self):
        """一半的頁失敗：舊公式 0.35+0.25+0.125+0.15=0.875，新公式 (0.35+0.25+0.125)/0.85≈0.853。"""
        doc = Document("t", "v", (_page(1, "x" * 1000), Page(2, 595.0, 842.0, (), failed=True)))
        q = measure(doc)
        self.assertAlmostEqual(q.quality_score, (0.35 + 0.25 + 0.125) / 0.85, places=3)


class LayoutStatsTests(unittest.TestCase):
    def test_layout_meta_is_surfaced_in_flags(self):
        doc = Document(
            "t", "v", (_page(1, "x" * 500),),
            meta={"layout": {"words_raw": 200, "words_merged": 50, "chart_words_dropped": 7,
                             "tables_retried": 2, "tables_rejected": 1}},
        )
        flags = measure(doc).as_flags()
        self.assertEqual(flags["words_merged_ratio"], 0.25)
        self.assertEqual(flags["chart_words_dropped"], 7)
        self.assertEqual(flags["tables_retried"], 2)
        self.assertEqual(flags["tables_rejected"], 1)

    def test_missing_meta_gives_zeros(self):
        flags = measure(Document("t", "v", (_page(1, "x" * 500),))).as_flags()
        self.assertEqual(flags["words_merged_ratio"], 0.0)
        self.assertEqual(flags["tables_rejected"], 0)


class NeedsReviewFlagTests(unittest.TestCase):
    def test_low_coverage_flags(self):
        self.assertTrue(store.needs_review(0.95, (), 0.6, {"layout_coverage": 0.2}))
        self.assertFalse(store.needs_review(0.95, (), 0.6, {"layout_coverage": 0.6}))

    def test_missing_coverage_is_not_low(self):
        self.assertFalse(store.needs_review(0.95, (), 0.6, {"layout_coverage": None}))
        self.assertFalse(store.needs_review(0.95, (), 0.6, {}))

    def test_garbled_flags(self):
        self.assertTrue(store.needs_review(0.95, (), 0.6, {"garbled_ratio": 0.05}))
        self.assertFalse(store.needs_review(0.95, (), 0.6, {"garbled_ratio": 0.001}))

    def test_thresholds_are_overridable(self):
        self.assertTrue(store.needs_review(0.95, (), 0.6, {"layout_coverage": 0.5}, min_coverage=0.55))
        self.assertFalse(store.needs_review(0.95, (), 0.6, {"garbled_ratio": 0.05}, max_garbled=0.1))

    def test_old_positional_calls_still_work(self):
        self.assertTrue(store.needs_review(0.3, (), 0.6))
        self.assertTrue(store.needs_review(0.95, (3,), 0.6))
        self.assertFalse(store.needs_review(None, None, 0.6))


if __name__ == "__main__":
    unittest.main()
