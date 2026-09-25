import os
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.config import _extractor, _faithfulness_min, _positive_float, get_settings  # noqa: E402


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

    def test_ask_web_defaults_m11(self):
        """預設是「允許使用者開」而非「一律開」——真正的開關在每個請求的 web 欄位。"""
        s = get_settings()
        self.assertTrue(s.ask_enable_web)
        # 沿用 llm.py 的 120s 會在搜到一半被砍，而逾時對已串流文字是 fail-open：
        # 症狀是答案無聲截斷，沒有任何錯誤。
        self.assertEqual(s.ask_web_timeout, 240.0)

    def test_rerank_defaults(self):
        s = get_settings()
        self.assertEqual(s.ask_rerank_enabled, True)
        self.assertEqual(s.ask_rerank_candidates, 50)
        self.assertEqual(s.rerank_model, "BAAI/bge-reranker-v2-m3")
        # per-path 逾時：prod 實測 50 對 ~34s（20 核 CPU），舊共用 30s 使全數逾時
        # （M1b 基準線 notes）。預設須蓋過實測值 + 餘裕。
        self.assertEqual(s.ask_rerank_timeout, 60.0)

    def test_query_planner_defaults_m5(self):
        # agentic_qa / query_planner（M5 區段；M5 里程碑只在本方法內加斷言）
        s = get_settings()
        self.assertEqual(s.qa_planner_model, "claude-haiku-4-5")
        self.assertEqual(s.qa_planner_timeout, 45.0)
        self.assertEqual(s.qa_planner_max_subqueries, 3)
        self.assertEqual(s.qa_max_rounds, 2)
        self.assertEqual(s.qa_agentic_enabled, True)
        self.assertEqual(s.qa_agentic_timeout, 90.0)
        self.assertEqual(s.qa_subquery_max_reports, 5)

    def test_extractor_default_e1a(self):
        """E1a：預設維持 pypdf，E1d 才切；typo 退回預設而不是靜默切換。"""
        s = get_settings()
        self.assertEqual(s.extractor, "pypdf")
        with mock.patch.dict(os.environ, {"EXTRACTOR": " PDFPlumber "}):
            self.assertEqual(_extractor("EXTRACTOR", "pypdf"), "pdfplumber")
        with mock.patch.dict(os.environ, {"EXTRACTOR": "pdfplumbr"}):
            self.assertEqual(_extractor("EXTRACTOR", "pypdf"), "pypdf")

    def test_llm_http_total_timeout(self):
        """DeepSeek 串流的牆鐘總時限：預設 600 秒；0、負數、非數字、nan／inf 退回預設（0 會讓每次都立刻逾時）。"""
        self.assertEqual(get_settings().llm_http_total_timeout, 600.0)
        for raw, want in (("300", 300.0), ("", 600.0), ("0", 600.0), ("-5", 600.0), ("abc", 600.0),
                          ("nan", 600.0), ("inf", 600.0), ("-inf", 600.0), ("NaN", 600.0)):
            with self.subTest(raw=raw), mock.patch.dict(os.environ, {"LLM_HTTP_TOTAL_TIMEOUT": raw}):
                self.assertEqual(_positive_float("LLM_HTTP_TOTAL_TIMEOUT", 600.0), want)

    def test_llm_model_defaults_match_prior_literals(self):
        """conftest 強制 LLM_PROVIDER=claude_cli（測試值，生產預設是 deepseek）：各任務的模型與遷移前
        寫死的字串相同（完整一覽在 test_llm_models）。本檔其他斷言裡的 claude-haiku-4-5 也來自這個測試值。"""
        s = get_settings()
        self.assertEqual(s.llm_provider, "claude_cli")
        self.assertEqual(s.ask_answer_model, "claude-sonnet-5")
        self.assertEqual(s.ask_web_model, "claude-sonnet-5")
        self.assertEqual(s.faithfulness_model, "claude-haiku-4-5")

    def test_llm_provider_unset_defaults_to_deepseek(self):
        """PR-28：LLM_PROVIDER 沒設時 Settings 解析為 deepseek；網搜刻意仍是 Claude，生產忠實度 judge
        自 PR-26/27 起是 deepseek-flash。"""
        from app import config

        with mock.patch.dict(os.environ, {}):
            os.environ.pop("LLM_PROVIDER", None)
            s = config._load()
        self.assertEqual(s.llm_provider, "deepseek")
        self.assertEqual(s.ask_answer_model, "deepseek-flash")
        self.assertEqual(s.ask_intent_model, "deepseek-flash")
        self.assertEqual(s.ask_condense_model, "deepseek-flash")
        self.assertEqual(s.qa_planner_model, "deepseek-flash")
        self.assertEqual(s.ask_web_model, "claude-sonnet-5")
        self.assertEqual(s.faithfulness_model, "deepseek-flash")
        # dataclass 欄位預設與 DEFAULT_PROVIDER 的表一致（直接建構時不自相矛盾）
        fields = config.Settings.__dataclass_fields__
        self.assertEqual(fields["llm_provider"].default, "deepseek")
        self.assertEqual(fields["ask_answer_model"].default, "deepseek-flash")
        self.assertEqual(fields["ask_web_model"].default, "claude-sonnet-5")

    def test_singleton(self):
        self.assertIs(get_settings(), get_settings())


class FaithfulnessMinTests(unittest.TestCase):
    """FAITHFULNESS_MIN 新名優先、舊名 REPORT_FAITHFULNESS_MIN 退回（生產環境檔可能還設著舊名）。"""

    def _with(self, env: dict[str, str]) -> float:
        with mock.patch.dict(os.environ, env, clear=False):
            for k in ("FAITHFULNESS_MIN", "REPORT_FAITHFULNESS_MIN"):
                if k not in env:
                    os.environ.pop(k, None)
            return _faithfulness_min()

    def test_default(self):
        self.assertEqual(self._with({}), 0.9)
        self.assertEqual(get_settings().faithfulness_min, 0.9)

    def test_new_name_wins(self):
        self.assertEqual(self._with({"FAITHFULNESS_MIN": "0.8", "REPORT_FAITHFULNESS_MIN": "0.7"}), 0.8)

    def test_legacy_name_fallback(self):
        self.assertEqual(self._with({"REPORT_FAITHFULNESS_MIN": "0.7"}), 0.7)

    def test_blank_new_name_falls_back_to_legacy(self):
        self.assertEqual(self._with({"FAITHFULNESS_MIN": " ", "REPORT_FAITHFULNESS_MIN": "0.75"}), 0.75)


if __name__ == "__main__":
    unittest.main()
