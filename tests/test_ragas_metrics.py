import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from eval.ragas_metrics import (  # noqa: E402
    _average_precision,
    _cosine,
    answer_relevancy,
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


if __name__ == "__main__":
    unittest.main()
