import os
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.config import _renderer, get_settings  # noqa: E402


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
        self.assertEqual(s.report_model, "claude-sonnet-5")
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

    def test_rerank_defaults(self):
        s = get_settings()
        self.assertEqual(s.ask_rerank_enabled, True)
        self.assertEqual(s.ask_rerank_candidates, 50)
        self.assertEqual(s.report_rerank_enabled, True)
        self.assertEqual(s.report_rerank_candidates, 120)
        self.assertEqual(s.rerank_model, "BAAI/bge-reranker-v2-m3")
        # per-path 逾時：prod 實測 50 對 ~34s、120 對 ~93s（20 核 CPU），
        # 舊共用 30s 使兩路徑全數逾時（M1b 基準線 notes）。預設須蓋過實測值 + 餘裕。
        self.assertEqual(s.ask_rerank_timeout, 60.0)
        self.assertEqual(s.report_rerank_timeout, 180.0)

    def test_query_planner_defaults_m5(self):
        # agentic_qa / query_planner（M5 區段；M5 里程碑只在本方法內加斷言）
        s = get_settings()
        self.assertEqual(s.qa_planner_model, "claude-haiku-4-5")
        self.assertEqual(s.qa_planner_timeout, 20.0)
        self.assertEqual(s.qa_planner_max_subqueries, 3)
        self.assertEqual(s.qa_max_rounds, 2)
        self.assertEqual(s.qa_agentic_enabled, True)
        self.assertEqual(s.qa_agentic_timeout, 90.0)
        self.assertEqual(s.qa_subquery_max_reports, 5)

    def test_query_planner_defaults_m6(self):
        # report 檢索增強 / query_planner（M6 區段；M6 里程碑只在本方法內加斷言）
        s = get_settings()
        self.assertEqual(s.report_planner_model, "claude-haiku-4-5")
        self.assertEqual(s.report_planner_timeout, 30.0)
        self.assertEqual(s.report_planner_max_subqueries, 8)
        self.assertEqual(s.report_fanout_concurrency, 3)
        self.assertEqual(s.report_subquery_dense_scan, 200)
        self.assertEqual(s.report_total_candidates, 600)
        self.assertEqual(s.report_mmr_enabled, True)
        self.assertEqual(s.report_mmr_lambda, 0.7)
        self.assertEqual(s.report_mmr_max_per_source, 6)
        self.assertEqual(s.report_mmr_max_per_month, 0)

    def test_renderer_defaults_m9a(self):
        # 渲染器雙軌（M9a 區段；M9a 里程碑只在本方法內加斷言）
        s = get_settings()
        self.assertEqual(s.report_renderer, "typst")

    def test_singleton(self):
        self.assertIs(get_settings(), get_settings())


class RendererFlagTests(unittest.TestCase):
    """_renderer 的 fail-safe：typo 不得靜默把生產切到另一條渲染路徑。"""

    def _renderer_with(self, value: str | None) -> str:
        env = {} if value is None else {"REPORT_RENDERER": value}
        with mock.patch.dict(os.environ, env, clear=False):
            if value is None:
                os.environ.pop("REPORT_RENDERER", None)
            return _renderer("REPORT_RENDERER", "typst")

    def test_known_values_pass_through(self):
        self.assertEqual(self._renderer_with("weasyprint"), "weasyprint")
        self.assertEqual(self._renderer_with("typst"), "typst")

    def test_case_and_space_tolerated(self):
        self.assertEqual(self._renderer_with("  WeasyPrint "), "weasyprint")

    def test_unknown_falls_back_to_default(self):
        with self.assertLogs("app.config", level="WARNING"):
            self.assertEqual(self._renderer_with("typoo"), "typst")

    def test_empty_falls_back_to_default(self):
        with self.assertLogs("app.config", level="WARNING"):
            self.assertEqual(self._renderer_with(""), "typst")

    def test_unset_uses_default(self):
        self.assertEqual(self._renderer_with(None), "typst")


if __name__ == "__main__":
    unittest.main()
