import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from eval.ragas_metrics import (  # noqa: E402
    _average_precision,
    _cosine,
    answer_relevancy,
    answer_relevancy_detailed,
    context_precision,
    faithfulness,
)


def make_judge(by_marker):
    """回一個 async judge：依 system prompt 內含的標記字選回應。by_marker: {substr: payload}。"""

    async def _judge(system, user):
        for marker, payload in by_marker.items():
            if marker in system:
                return payload
        raise AssertionError(f"unexpected judge system: {system[:40]!r}")

    return _judge


def make_embed(vectors):
    """回一個 embed：依文字查表回向量；查無則回零向量。"""

    def _embed(text):
        return vectors.get(text, [0.0, 0.0, 0.0])

    return _embed


class PureHelperTests(unittest.TestCase):
    def test_cosine_identical_is_one(self):
        self.assertAlmostEqual(_cosine([1.0, 0.0], [1.0, 0.0]), 1.0)

    def test_cosine_orthogonal_is_zero(self):
        self.assertAlmostEqual(_cosine([1.0, 0.0], [0.0, 1.0]), 0.0)

    def test_cosine_zero_vector_is_zero(self):
        self.assertEqual(_cosine([0.0, 0.0], [1.0, 1.0]), 0.0)

    def test_average_precision_all_relevant(self):
        # rel=[1,1,1] → (1/1 + 2/2 + 3/3)/3 = 1.0
        self.assertAlmostEqual(_average_precision([1, 1, 1]), 1.0)

    def test_average_precision_none_relevant(self):
        self.assertEqual(_average_precision([0, 0, 0]), 0.0)

    def test_average_precision_rank_weighted(self):
        # rel=[0,1,1] → hits at k=2,3: (1/2 + 2/3)/2 = 0.58333...
        self.assertAlmostEqual(_average_precision([0, 1, 1]), (0.5 + 2 / 3) / 2)


class FaithfulnessTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_supported_is_one(self):
        judge = make_judge({
            "拆解": {"statements": ["s1", "s2"]},
            "佐證": {"verdicts": [{"idx": 0, "supported": True}, {"idx": 1, "supported": True}]},
        })
        score = await faithfulness("ans", ["ctx"], judge=judge)
        self.assertAlmostEqual(score, 1.0)

    async def test_half_supported_is_half(self):
        judge = make_judge({
            "拆解": {"statements": ["s1", "s2"]},
            "佐證": {"verdicts": [{"idx": 0, "supported": True}, {"idx": 1, "supported": False}]},
        })
        score = await faithfulness("ans", ["ctx"], judge=judge)
        self.assertAlmostEqual(score, 0.5)

    async def test_zero_statements_is_none(self):
        judge = make_judge({"拆解": {"statements": []}})
        score = await faithfulness("找不到相關資料", ["ctx"], judge=judge)
        self.assertIsNone(score)

    async def test_missing_verdict_counts_unsupported(self):
        judge = make_judge({
            "拆解": {"statements": ["s1", "s2"]},
            "佐證": {"verdicts": [{"idx": 0, "supported": True}]},
        })
        score = await faithfulness("ans", ["ctx"], judge=judge)
        self.assertAlmostEqual(score, 0.5)

    async def test_duplicate_and_outofrange_verdicts(self):
        """Guard against malformed judge: duplicate idx and out-of-range idx should not inflate score > 1.0."""
        judge = make_judge({
            "拆解": {"statements": ["s1", "s2"]},
            "佐證": {"verdicts": [
                {"idx": 0, "supported": True},
                {"idx": 0, "supported": True},  # duplicate
                {"idx": 5, "supported": True},  # out-of-range
            ]},
        })
        score = await faithfulness("ans", ["ctx"], judge=judge)
        # Only idx 0 counts among 2 statements → score = 1/2 = 0.5
        self.assertAlmostEqual(score, 0.5)
        self.assertLessEqual(score, 1.0)


class ContextPrecisionTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_relevant_is_one(self):
        judge = make_judge({
            "精準度": {"verdicts": [{"idx": 0, "relevant": True}, {"idx": 1, "relevant": True}]},
        })
        score = await context_precision("q", "a", ["c0", "c1"], judge=judge)
        self.assertAlmostEqual(score, 1.0)

    async def test_none_relevant_is_zero(self):
        judge = make_judge({
            "精準度": {"verdicts": [{"idx": 0, "relevant": False}, {"idx": 1, "relevant": False}]},
        })
        score = await context_precision("q", "a", ["c0", "c1"], judge=judge)
        self.assertEqual(score, 0.0)

    async def test_rank_weighted_first_irrelevant(self):
        # rel=[0,1,1] → (1/2 + 2/3)/2
        judge = make_judge({
            "精準度": {"verdicts": [
                {"idx": 0, "relevant": False},
                {"idx": 1, "relevant": True},
                {"idx": 2, "relevant": True},
            ]},
        })
        score = await context_precision("q", "a", ["c0", "c1", "c2"], judge=judge)
        self.assertAlmostEqual(score, (0.5 + 2 / 3) / 2)

    async def test_no_contexts_is_zero(self):
        judge = make_judge({"精準度": {"verdicts": []}})
        score = await context_precision("q", "a", [], judge=judge)
        self.assertEqual(score, 0.0)


class AnswerRelevancyTests(unittest.IsolatedAsyncioTestCase):
    async def test_mean_cosine_of_generated(self):
        judge = make_judge({"反推": {"questions": ["g1", "g2"]}})
        embed = make_embed({
            "orig": [1.0, 0.0, 0.0],
            "g1": [1.0, 0.0, 0.0],   # cosine 1.0
            "g2": [0.0, 1.0, 0.0],   # cosine 0.0
        })
        score = await answer_relevancy("orig", "a", judge=judge, embed=embed)
        self.assertAlmostEqual(score, 0.5)

    async def test_no_generated_questions_is_zero(self):
        judge = make_judge({"反推": {"questions": []}})
        embed = make_embed({})
        score = await answer_relevancy("orig", "a", judge=judge, embed=embed)
        self.assertEqual(score, 0.0)


class RelevancyDetailTests(unittest.IsolatedAsyncioTestCase):
    """反推問題與逐題餘弦必須跟著分數一起回。

    先前 baseline 只存最終分數，於是「AR 為什麼是 0.64」只能重跑整份評測才答得出來
    （2026-07-29 診斷門檻從未通過時就卡在這裡）。中間產物落庫後，下次診斷是讀檔而非重跑。
    """

    async def test_returns_questions_and_per_question_sims(self):
        judge = make_judge({"反推": {"questions": ["g1", "g2"]}})
        embed = make_embed({
            "orig": [1.0, 0.0, 0.0],
            "g1": [1.0, 0.0, 0.0],
            "g2": [0.0, 1.0, 0.0],
        })
        d = await answer_relevancy_detailed("orig", "a", judge=judge, embed=embed)
        self.assertAlmostEqual(d.score, 0.5)
        self.assertEqual(d.questions, ("g1", "g2"))
        self.assertEqual(len(d.sims), 2)
        self.assertAlmostEqual(d.sims[0], 1.0)
        self.assertAlmostEqual(d.sims[1], 0.0)

    async def test_score_is_mean_of_reported_sims(self):
        """分數必須等於它自己回報的逐題餘弦平均——否則存下來的細節解釋不了分數。"""
        judge = make_judge({"反推": {"questions": ["g1", "g2"]}})
        embed = make_embed({
            "orig": [1.0, 0.0, 0.0],
            "g1": [1.0, 0.0, 0.0],
            "g2": [0.0, 1.0, 0.0],
        })
        d = await answer_relevancy_detailed("orig", "a", judge=judge, embed=embed)
        self.assertAlmostEqual(d.score, sum(d.sims) / len(d.sims))

    async def test_empty_detail_when_no_questions(self):
        d = await answer_relevancy_detailed(
            "orig", "a", judge=make_judge({"反推": {"questions": []}}), embed=make_embed({})
        )
        self.assertEqual((d.score, d.questions, d.sims), (0.0, (), ()))

    async def test_old_score_only_api_still_matches(self):
        """舊介面沿用者不得受影響。"""
        judge = make_judge({"反推": {"questions": ["g1"]}})
        embed = make_embed({"orig": [1.0, 0.0], "g1": [1.0, 0.0]})
        score = await answer_relevancy("orig", "a", judge=judge, embed=embed)
        d = await answer_relevancy_detailed("orig", "a", judge=judge, embed=embed)
        self.assertEqual(score, d.score)


class JudgePromptShaTests(unittest.TestCase):
    """judge_prompt_sha 是量尺的一部分：系統提示或 payload 版型一動，雜湊就要跟著動。"""

    def test_sha_is_stable_hex(self):
        from eval import ragas_metrics as rm

        self.assertEqual(rm.judge_prompt_sha(), rm.judge_prompt_sha())
        self.assertRegex(rm.judge_prompt_sha(), r"^[0-9a-f]{64}$")

    def test_every_judge_prompt_is_covered(self):
        from app.services import faithfulness as fm
        from eval import ragas_metrics as rm

        covered = {text for _name, text in rm.JUDGE_PROMPT_TEMPLATES}
        for tmpl in (fm.DECOMPOSE_SYS, fm.GROUND_SYS, fm.GROUND_ITEM_FMT, fm.GROUND_PAYLOAD_FMT,
                     rm.CTX_RELEVANCE_SYS, rm.CP_ITEM_FMT, rm.CP_PAYLOAD_FMT, rm.GENQ_SYS):
            self.assertIn(tmpl, covered)

    def test_changing_a_payload_template_changes_the_sha(self):
        from unittest import mock

        from eval import ragas_metrics as rm

        before = rm.judge_prompt_sha()
        changed = tuple(
            (n, t + "x") if n == "context_precision_item" else (n, t) for n, t in rm.JUDGE_PROMPT_TEMPLATES
        )
        with mock.patch.object(rm, "JUDGE_PROMPT_TEMPLATES", changed):
            self.assertNotEqual(rm.judge_prompt_sha(), before)


if __name__ == "__main__":
    unittest.main()
