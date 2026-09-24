"""judge 量尺（`app/services/judge_schema.py`）與生產 judge 預設（`app/config.py`）。

DeepSeek 遷移 D4 的前提是「一次只換一個變因」。這裡釘住兩件事：
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

    def test_default_is_haiku_and_equals_the_legacy_judge(self):
        """改動前生產實際用的就是 haiku（沿用 intent 預設），預設值不得因解耦而改變。"""
        self.assertEqual(self._load({}).faithfulness_model, "claude-haiku-4-5")
        self.assertEqual(self._load({}).faithfulness_model, js.LEGACY_JUDGE_MODEL)

    def test_no_longer_follows_intent_model(self):
        s = self._load({"ASK_INTENT_MODEL": "deepseek-flash"})
        self.assertEqual(s.ask_intent_model, "deepseek-flash")
        self.assertEqual(s.faithfulness_model, "claude-haiku-4-5")

    def test_empty_string_counts_as_unset(self):
        self.assertEqual(self._load({"FAITHFULNESS_MODEL": ""}).faithfulness_model, "claude-haiku-4-5")

    def test_explicit_override_wins(self):
        self.assertEqual(
            self._load({"FAITHFULNESS_MODEL": "claude-sonnet-5"}).faithfulness_model, "claude-sonnet-5"
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
        self.assertEqual(js.judge_model_of({"judge_model": "deepseek-flash"}), "deepseek-flash")

    def test_unchecked_row_has_no_judge(self):
        self.assertIsNone(js.judge_model_of(None))
        self.assertIsNone(js.judge_model_of("garbage"))

    def test_non_string_mirrors_postgres_text_extraction(self):
        # PostgreSQL 的 `->>` 對純量回其 JSON 文字；Python 版要得到同一個字串。
        self.assertEqual(js.judge_model_of({"judge_model": 1}), "1")
        self.assertEqual(js.judge_model_of({"judge_model": True}), "true")

    def test_is_current_judge(self):
        self.assertTrue(js.is_current_judge({}, "claude-haiku-4-5"))
        self.assertFalse(js.is_current_judge({}, "deepseek-flash"))
        self.assertTrue(js.is_current_judge({"judge_model": "deepseek-flash"}, "deepseek-flash"))
        self.assertFalse(js.is_current_judge(None, "claude-haiku-4-5"))

    def test_sql_fragment_uses_the_same_legacy_default_and_a_bound_param(self):
        self.assertIn(f"'{js.LEGACY_JUDGE_MODEL}'", js.JUDGE_MODEL_SQL)
        self.assertIn("NULLIF(evaluation->>'judge_model', '')", js.JUDGE_MODEL_SQL)
        self.assertTrue(js.CURRENT_JUDGE_SQL.endswith("= :judge_model"))

    def test_all_three_readers_use_the_shared_filter(self):
        """M11：監控卡、待複核佇列、離線彙總共用同一段過濾，不各寫一份。"""
        for rel, needle in (
            ("web/routers/monitor.py", "CURRENT_JUDGE_SQL"),
            ("web/routers/review.py", "CURRENT_JUDGE_SQL"),
            ("scripts/eval_faithfulness.py", "is_current_judge"),
        ):
            src = (REPO_ROOT / rel).read_text(encoding="utf-8")
            self.assertIn(needle, src, rel)
            self.assertNotIn("evaluation->>'judge_model'", src, f"{rel} 自己寫了一份 judge 過濾")


if __name__ == "__main__":
    unittest.main()
