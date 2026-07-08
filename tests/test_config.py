import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.config import get_settings  # noqa: E402


class SettingsDefaultsTests(unittest.TestCase):
    def test_defaults_match_prior_literals(self):
        s = get_settings()
        # ASK_*
        self.assertEqual(s.ask_max_reports, 15)
        self.assertEqual(s.ask_max_passages, 4)
        self.assertEqual(s.ask_max_context_chars, 20000)
        self.assertEqual(s.ask_retrieval_k, 15)
        self.assertEqual(s.ask_dense_scan, 400)
        self.assertEqual(s.ask_recency_weight, 0.06)
        self.assertEqual(s.ask_recency_half_life_days, 90)
        self.assertEqual(s.ask_relevance_band, 0.10)
        self.assertEqual(s.ask_band_eps, 0.03)
        self.assertEqual(s.ask_fresh_factor, 0.5)
        self.assertEqual(s.ask_stale_factor, 0.1)
        self.assertEqual(s.ask_min_fresh_before_cutoff, 2)
        self.assertEqual(s.ask_relevance_floor, 0.62)
        self.assertEqual(s.ask_min_reports, 3)
        self.assertEqual(s.ask_stale_age_days, 180)
        self.assertEqual(s.ask_max_stale_reports, 4)
        self.assertTrue(s.ask_enable_web)
        # intent
        self.assertEqual(s.ask_intent_model, "claude-haiku-4-5")
        self.assertEqual(s.ask_intent_timeout, 20.0)
        self.assertEqual(s.ask_condense_model, "claude-haiku-4-5")
        self.assertEqual(s.ask_condense_timeout, 20.0)
        # REPORT_*
        self.assertEqual(s.report_model, "claude-sonnet-4-6")
        self.assertEqual(s.report_deep_k, 30)
        self.assertEqual(s.report_max_reports, 25)
        self.assertEqual(s.report_max_passages, 6)
        self.assertEqual(s.report_max_context_chars, 40000)
        self.assertEqual(s.report_timeout, 600.0)
        self.assertEqual(s.reports_dir, "data/reports")
        self.assertTrue(s.report_enable_web)
        self.assertEqual(s.report_thin_coverage, 8)
        # report_gate
        self.assertEqual(s.report_min_cited, 3)
        self.assertEqual(s.report_long_answer_chars, 400)

    def test_singleton(self):
        self.assertIs(get_settings(), get_settings())


if __name__ == "__main__":
    unittest.main()
