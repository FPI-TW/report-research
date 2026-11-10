"""judge 量尺（`app/services/judge_schema.py`）與生產 judge 預設（`app/config.py`）。

換 judge 的前提是「一次只換一個變因」。這裡釘住兩件事：
1. 生產 judge 不再跟著 `ASK_INTENT_MODEL` 走——換路由模型不得靜默換掉量尺。
2. 讀分數的三處共用同一條「只計現行 judge」規則，SQL 與 Python 兩個版本逐條等價。
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app import config  # noqa: E402
from app.services import judge_schema as js  # noqa: E402


class FaithfulnessModelDefaultTests(unittest.TestCase):
    def _load(self, env: dict):
        clean = {k: v for k, v in os.environ.items() if k not in ("FAITHFULNESS_MODEL", "ASK_INTENT_MODEL")}
        with mock.patch.dict(os.environ, {**clean, **env}, clear=True):
            return config._load()

    def test_default_is_flash_and_legacy_rows_become_other(self):
        """PR-26/27：judge 是 gpt-6-luna；缺 judge_model 的舊列（haiku）從此不是現行 judge——監控卡、
        待複核、eval_faithfulness 都把它們歸「其他 judge」。PR-M 移除 Claude 預設表後，沒有任何 provider
        設定能讓預設 judge 回到 haiku。"""
        for env in ({}, {"LLM_PROVIDER": "claude_cli"}, {"LLM_PROVIDER": "claude_only"}):
            with self.subTest(env=env):
                s = self._load(env)
                self.assertEqual(s.faithfulness_model, "gpt-6-luna")
                self.assertFalse(js.is_current_judge({"faithfulness_score": 0.5}, s.faithfulness_model))
                self.assertTrue(js.is_current_judge({"judge_model": "gpt-6-luna"}, s.faithfulness_model))
        self.assertEqual(js.LEGACY_JUDGE_MODEL, "claude-haiku-4-5", "舊列仍是 haiku 量的，常數不跟著改")

    def test_no_longer_follows_intent_model(self):
        s = self._load({"ASK_INTENT_MODEL": "gpt-other"})
        self.assertEqual(s.ask_intent_model, "gpt-other")
        self.assertEqual(s.faithfulness_model, "gpt-6-luna")

    def test_empty_string_counts_as_unset(self):
        self.assertEqual(self._load({"FAITHFULNESS_MODEL": ""}).faithfulness_model, "gpt-6-luna")

    def test_explicit_override_wins(self):
        self.assertEqual(
            self._load({"FAITHFULNESS_MODEL": "gpt-other"}).faithfulness_model, "gpt-other"
        )


class SchemaVersionTests(unittest.TestCase):
    def test_strict_validation_is_version_2(self):
        """v2 改變了 Claude 路徑的判分行為，必須排在 B0 之前並記進量尺。"""
        self.assertEqual(js.JUDGE_SCHEMA_VERSION, 2)

    def test_schema_error_is_not_a_value_error(self):
        """解析層的 `except ValueError`（JSON 壞掉）不得把「JSON 合法但不合格」吞成另一種錯。"""
        self.assertFalse(issubclass(js.JudgeSchemaError, ValueError))

    def test_parse_verdicts_reports_positions_zero_based(self):
        out, missing = js.parse_verdicts(
            {"verdicts": [{"idx": 2, "relevant": False}, {"idx": 1, "relevant": True}]}, 2, "relevant", first=1
        )
        self.assertEqual((out, missing), ({0: True, 1: False}, []))

    def test_all_missing_is_an_error_even_when_missing_is_allowed(self):
        """allow_missing 只放寬「漏判幾條」；一條都沒判是 judge 沒回答（審查 L3）。"""
        with self.assertRaises(js.JudgeSchemaError):
            js.parse_verdicts({"verdicts": []}, 2, "supported", allow_missing=True)
        out, missing = js.parse_verdicts({"verdicts": [{"idx": 1, "supported": True}]}, 2, "supported",
                                         allow_missing=True)
        self.assertEqual((out, missing), ({1: True}, [0]))
        self.assertEqual(js.parse_verdicts({"verdicts": []}, 0, "supported"), ({}, []))

    def test_non_object_response_is_schema_error(self):
        for bad in ([1, 2], "x", None, 3):
            with self.subTest(bad=bad), self.assertRaises(js.JudgeSchemaError):
                js.parse_statements(bad)


class JudgeIdentityTests(unittest.TestCase):
    def test_legacy_rows_are_haiku(self):
        """缺 judge_model 的舊列一律歸給 claude-haiku-4-5（M11）。這個常數不跟著生產預設改。"""
        self.assertEqual(js.LEGACY_JUDGE_MODEL, "claude-haiku-4-5")
        self.assertEqual(js.judge_model_of({"faithfulness_score": 0.5}), "claude-haiku-4-5")
        self.assertEqual(js.judge_model_of({"judge_model": None}), "claude-haiku-4-5")
        self.assertEqual(js.judge_model_of({"judge_model": ""}), "claude-haiku-4-5")

    def test_recorded_judge_wins(self):
        self.assertEqual(js.judge_model_of({"judge_model": "gpt-6-luna"}), "gpt-6-luna")

    def test_unchecked_row_has_no_judge(self):
        self.assertIsNone(js.judge_model_of(None))
        self.assertIsNone(js.judge_model_of("garbage"))

    def test_non_string_mirrors_postgres_text_extraction(self):
        # PostgreSQL 的 `->>` 對純量回其 JSON 文字；Python 版要得到同一個字串。
        self.assertEqual(js.judge_model_of({"judge_model": 1}), "1")
        self.assertEqual(js.judge_model_of({"judge_model": True}), "true")

    def test_is_current_judge(self):
        self.assertTrue(js.is_current_judge({}, "claude-haiku-4-5"))
        self.assertFalse(js.is_current_judge({}, "gpt-6-luna"))
        self.assertTrue(js.is_current_judge({"judge_model": "gpt-6-luna"}, "gpt-6-luna"))
        self.assertFalse(js.is_current_judge(None, "claude-haiku-4-5"))

    def test_sql_fragment_uses_the_same_legacy_default_and_a_bound_param(self):
        self.assertIn(f"'{js.LEGACY_JUDGE_MODEL}'", js.JUDGE_MODEL_SQL)
        self.assertIn("NULLIF(evaluation->>'judge_model', '')", js.JUDGE_MODEL_SQL)
        self.assertTrue(js.CURRENT_JUDGE_SQL.endswith("= :judge_model"))

    def test_all_three_readers_use_the_shared_filter(self):
        """M11：監控卡、待複核佇列、離線彙總共用同一段過濾，不各寫一份。"""
        for rel, needle in (
            ("web/stats_snapshot.py", "CURRENT_JUDGE_SQL"),
            ("web/routers/review.py", "CURRENT_JUDGE_SQL"),
            ("scripts/eval_faithfulness.py", "is_current_judge"),
        ):
            src = (REPO_ROOT / rel).read_text(encoding="utf-8")
            self.assertIn(needle, src, rel)
            self.assertNotIn("evaluation->>'judge_model'", src, f"{rel} 自己寫了一份 judge 過濾")



class DeclaredLineageIdentityTests(unittest.TestCase):
    """宣告模型（`declared:<模型>`）的 judge 識別：記在 evaluation 的 judge_lineage，比的是識別不是名稱。"""

    ANTHROPIC = {"LLM_PROVIDER": "anthropic", "ANTHROPIC_MODELS": "claude-haiku-4-5"}

    def test_prefix_mirrors_llm_models(self):
        from app.services.llm_models import JUDGE_LINEAGE_DECLARED_PREFIX

        self.assertEqual(js.DECLARED_LINEAGE_PREFIX, JUDGE_LINEAGE_DECLARED_PREFIX)

    def test_builtin_and_legacy_identity_is_the_model_name(self):
        """廷豐：沒有 judge_lineage 鍵的列，識別＝judge_model_of，判定與加入識別之前逐列相同。"""
        for ev in ({}, {"judge_model": "gpt-6-luna"}, {"judge_model": ""}, {"judge_model": 1},
                   {"judge_model": "gpt-6-luna", "judge_lineage": "gpt-6-luna-2026-11"}):
            with self.subTest(ev=ev):
                self.assertEqual(js.judge_identity_of(ev), js.judge_model_of(ev))
        self.assertIsNone(js.judge_identity_of(None))
        self.assertEqual(js.judge_identity("gpt-6-luna", "gpt-6-luna-2026-11"), "gpt-6-luna")
        self.assertEqual(js.judge_identity("claude-haiku-4-5", "claude-haiku"), "claude-haiku-4-5")

    def test_declared_name_colliding_with_the_legacy_name_is_a_different_judge(self):
        """以 Anthropic 宣告 claude-haiku-4-5：與缺 judge_model 的 CLI 時代舊列同名，但不是同一把尺。"""
        current = js.judge_identity("claude-haiku-4-5", "declared:claude-haiku-4-5")
        self.assertEqual(current, "declared:claude-haiku-4-5")
        new_row = {"judge_model": "claude-haiku-4-5", "judge_lineage": "declared:claude-haiku-4-5"}
        self.assertTrue(js.is_current_judge(new_row, current))
        self.assertFalse(js.is_current_judge({}, current), "CLI 時代的舊列不能算成宣告模型量的")
        self.assertFalse(js.is_current_judge({"judge_model": "claude-haiku-4-5"}, current))
        self.assertFalse(js.is_current_judge(new_row, "claude-haiku-4-5"))

    def test_sql_fragment_mirrors_python(self):
        self.assertIn(js.JUDGE_MODEL_SQL, js.JUDGE_IDENTITY_SQL)
        self.assertIn("starts_with(evaluation->>'judge_lineage', 'declared:')", js.JUDGE_IDENTITY_SQL)
        self.assertEqual(js.CURRENT_JUDGE_SQL, f"({js.JUDGE_IDENTITY_SQL}) = :judge_model")

    def test_faithfulness_records_lineage_only_for_declared_judges(self):
        from app.services.faithfulness import FaithfulnessResult

        builtin = FaithfulnessResult(1.0, None, judge_model="gpt-6-luna").to_evaluation()
        self.assertNotIn("judge_lineage", builtin, "內建 judge 的 evaluation 形狀不變")
        with mock.patch.dict(os.environ, self.ANTHROPIC):
            declared = FaithfulnessResult(1.0, None, judge_model="claude-haiku-4-5").to_evaluation()
        self.assertEqual(declared["judge_lineage"], "declared:claude-haiku-4-5")
        self.assertEqual(declared["judge_model"], "claude-haiku-4-5")
        self.assertNotIn("judge_lineage", FaithfulnessResult(None, None).to_evaluation())

    def test_readers_bind_the_identity(self):
        """讀分數的各處綁給 :judge_model 的是識別（內建模型＝模型名，廷豐不變）。"""
        from app.services import analytics
        from web import stats_snapshot
        from web.routers import review

        for module in (stats_snapshot, review):
            with self.subTest(module=module.__name__):
                self.assertEqual(module._JUDGE_IDENTITY, module._JUDGE_MODEL)
        bound = analytics._bind(analytics.date(2026, 11, 1), analytics.date(2026, 11, 2),
                                analytics.Params(judge_model="gpt-6-luna"))
        self.assertEqual(bound["judge_model"], "gpt-6-luna")
        with mock.patch.dict(os.environ, self.ANTHROPIC):
            bound = analytics._bind(analytics.date(2026, 11, 1), analytics.date(2026, 11, 2),
                                    analytics.Params(judge_model="claude-haiku-4-5"))
        self.assertEqual(bound["judge_model"], "declared:claude-haiku-4-5")


if __name__ == "__main__":
    unittest.main()
