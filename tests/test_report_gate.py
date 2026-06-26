import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services.report_gate import should_offer_report, suggested_title  # noqa: E402


class SuggestedTitleTests(unittest.TestCase):
    def test_appends_suffix_and_strips_punctuation(self):
        self.assertEqual(suggested_title("台積電未來展望？"), "台積電未來展望 深度研報")

    def test_empty_falls_back(self):
        self.assertEqual(suggested_title("  "), "研報")


class ShouldOfferReportTests(unittest.TestCase):
    def test_analysis_question_with_enough_citations_offers(self):
        offer, title = should_offer_report(
            "請分析台積電產業趨勢", ["a", "b", "c"], "結論[1][2][3]"
        )
        self.assertTrue(offer)
        self.assertTrue(title.endswith("深度研報"))

    def test_too_few_citations_declines(self):
        offer, _ = should_offer_report("請分析台積電趨勢", ["a"], "x[1]")
        self.assertFalse(offer)

    def test_trivial_price_question_declines(self):
        offer, _ = should_offer_report("台積電股價多少", ["a", "b", "c"], "約 1000 元[1]")
        self.assertFalse(offer)

    def test_long_answer_without_keyword_still_offers(self):
        offer, _ = should_offer_report("台積電怎麼樣", ["a", "b", "c"], "詳" * 500)
        self.assertTrue(offer)

    def test_short_answer_no_keyword_declines(self):
        offer, _ = should_offer_report("台積電怎麼樣", ["a", "b", "c"], "還行[1]")
        self.assertFalse(offer)
