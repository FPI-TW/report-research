# tests/test_faithfulness_qa.py
"""M8c 問答迷你忠實度抽查（answer.py）。

閘門（啟用/含數字/抽樣）、_faithfulness_spot_check 落庫、_update_evaluation、fail-open。
全用假物件（零 LLM/DB）。抽查掛在 done 之後，故只測 helper 與閘門邏輯，不驅動整條
answer_question（那由既有 test_answer 的 SSE 契約覆蓋）。
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services import answer as A  # noqa: E402
from app.services.faithfulness import ClaimVerdict, FaithfulnessResult  # noqa: E402


class SpotCheckGateTests(unittest.TestCase):
    """複刻 answer_question 的閘門條件，確保只在該查時查。"""

    def _should_check(self, *, enabled, qa_id, body, roll, rate):
        return bool(
            enabled and qa_id
            and A.is_numeric_claim(body)
            and roll < rate
        )

    def test_skips_when_no_numbers(self):
        self.assertFalse(self._should_check(
            enabled=True, qa_id="q", body="台積電看好 AI 前景", roll=0.0, rate=1.0))

    def test_runs_when_numeric_and_sampled(self):
        self.assertTrue(self._should_check(
            enabled=True, qa_id="q", body="營收年增 30%", roll=0.0, rate=1.0))

    def test_sampling_excludes(self):
        self.assertFalse(self._should_check(
            enabled=True, qa_id="q", body="營收年增 30%", roll=0.9, rate=0.5))

    def test_disabled_skips(self):
        self.assertFalse(self._should_check(
            enabled=False, qa_id="q", body="營收年增 30%", roll=0.0, rate=1.0))

    def test_no_qa_id_skips(self):
        self.assertFalse(self._should_check(
            enabled=True, qa_id=None, body="營收年增 30%", roll=0.0, rate=1.0))


class UpdateEvaluationTests(unittest.IsolatedAsyncioTestCase):
    async def test_update_evaluation_writes_jsonb(self):
        captured = {}

        class _S:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def execute(self, stmt, params):
                captured["sql"] = str(stmt)
                captured["params"] = params
            async def commit(self): captured["committed"] = True

        with patch.object(A, "SessionFactory", lambda: _S()):
            await A._update_evaluation("qa-1", {"faithfulness_score": 0.5})
        self.assertTrue(captured["committed"])
        self.assertIn("evaluation", captured["sql"])
        import json
        self.assertEqual(json.loads(captured["params"]["e"])["faithfulness_score"], 0.5)

    async def test_update_evaluation_swallows_errors(self):
        class _Boom:
            async def __aenter__(self): raise RuntimeError("DB down")
            async def __aexit__(self, *a): return False

        with patch.object(A, "SessionFactory", lambda: _Boom()):
            # 不得拋出（best-effort）
            await A._update_evaluation("qa-1", {"x": 1})


class SpotCheckTests(unittest.IsolatedAsyncioTestCase):
    async def test_spot_check_grounds_and_persists(self):
        captured = {}

        async def fake_resolve(ledger, session, **kw):
            return ["營收年增 30% 的來源片段"]

        async def fake_check(answer, context_texts, **kw):
            return FaithfulnessResult(
                1.0, 1.0, [ClaimVerdict("營收年增 30%", True, "supported")],
                degraded=False,
            )

        async def fake_update(qa_id, evaluation):
            captured["qa_id"] = qa_id
            captured["eval"] = evaluation

        class _S:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False

        with patch.object(A, "resolve_evidence_texts", fake_resolve), \
             patch.object(A, "check_faithfulness", fake_check), \
             patch.object(A, "_update_evaluation", fake_update), \
             patch.object(A, "SessionFactory", lambda: _S()):
            await A._faithfulness_spot_check("qa-1", "營收年增 30%", {"schema_version": 1})

        self.assertEqual(captured["qa_id"], "qa-1")
        self.assertEqual(captured["eval"]["numeric_support_rate"], 1.0)
        self.assertFalse(captured["eval"]["degraded"])

    async def test_spot_check_failopen_on_grounding_error(self):
        async def boom(*a, **k):
            raise RuntimeError("grounding 炸了")

        updated = []

        async def fake_update(qa_id, evaluation):
            updated.append(qa_id)

        with patch.object(A, "resolve_evidence_texts", boom), \
             patch.object(A, "_update_evaluation", fake_update):
            # 不得拋出；grounding 炸 → 不落庫（fail-open）
            await A._faithfulness_spot_check("qa-1", "營收年增 30%", None)
        self.assertEqual(updated, [])


if __name__ == "__main__":
    unittest.main()
