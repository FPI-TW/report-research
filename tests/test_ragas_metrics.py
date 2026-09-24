import re
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services.judge_schema import JudgeSchemaError  # noqa: E402
from eval.ragas_metrics import (  # noqa: E402
    CTX_RELEVANCE_SYS,
    _average_precision,
    _cosine,
    answer_relevancy,
    answer_relevancy_detailed,
    context_precision,
    context_precision_payload,
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

    async def test_missing_verdict_is_a_schema_error_offline(self):
        """schema v2：離線端缺 idx 不再靜默記 unsupported（那會把量尺故障讀成品質變差）。
        生產端相反，仍計為 unsupported（審查 L19），見 tests/test_faithfulness.py。"""
        judge = make_judge({
            "拆解": {"statements": ["s1", "s2"]},
            "佐證": {"verdicts": [{"idx": 0, "supported": True}]},
        })
        with self.assertRaises(JudgeSchemaError):
            await faithfulness("ans", ["ctx"], judge=judge)

    async def test_duplicate_and_outofrange_verdicts(self):
        """重複與越界的 idx 不能灌高分數——v2 直接判 schema 錯，而不是挑一筆算。"""
        for verdicts in (
            [{"idx": 0, "supported": True}, {"idx": 0, "supported": True}, {"idx": 1, "supported": True}],
            [{"idx": 0, "supported": True}, {"idx": 1, "supported": True}, {"idx": 5, "supported": True}],
        ):
            judge = make_judge({"拆解": {"statements": ["s1", "s2"]}, "佐證": {"verdicts": verdicts}})
            with self.subTest(verdicts=verdicts), self.assertRaises(JudgeSchemaError):
                await faithfulness("ans", ["ctx"], judge=judge)

    async def test_string_statements_is_a_schema_error_not_one_claim_per_char(self):
        judge = make_judge({"拆解": {"statements": "台積電展望正向"}})
        with self.assertRaises(JudgeSchemaError):
            await faithfulness("ans", ["ctx"], judge=judge)


class ContextPrecisionTests(unittest.IsolatedAsyncioTestCase):
    # schema v2：CP 候選片段 1 起編號（與脈絡本身的 [n]、回答的引用一致，審查 M12）。
    async def test_all_relevant_is_one(self):
        judge = make_judge({
            "精準度": {"verdicts": [{"idx": 1, "relevant": True}, {"idx": 2, "relevant": True}]},
        })
        score = await context_precision("q", "a", ["c0", "c1"], judge=judge)
        self.assertAlmostEqual(score, 1.0)

    async def test_none_relevant_is_zero(self):
        judge = make_judge({
            "精準度": {"verdicts": [{"idx": 1, "relevant": False}, {"idx": 2, "relevant": False}]},
        })
        score = await context_precision("q", "a", ["c0", "c1"], judge=judge)
        self.assertEqual(score, 0.0)

    async def test_rank_weighted_first_irrelevant(self):
        # rel=[0,1,1] → (1/2 + 2/3)/2
        judge = make_judge({
            "精準度": {"verdicts": [
                {"idx": 1, "relevant": False},
                {"idx": 2, "relevant": True},
                {"idx": 3, "relevant": True},
            ]},
        })
        score = await context_precision("q", "a", ["c0", "c1", "c2"], judge=judge)
        self.assertAlmostEqual(score, (0.5 + 2 / 3) / 2)

    async def test_no_contexts_is_zero(self):
        judge = make_judge({"精準度": {"verdicts": []}})
        score = await context_precision("q", "a", [], judge=judge)
        self.assertEqual(score, 0.0)

    async def test_zero_based_idx_is_rejected_not_silently_shifted(self):
        """v1 的雙重編號：judge 照 0 起填 idx 時整體錯位一格、CP 靜默算錯。v2 直接拒收。"""
        judge = make_judge({
            "精準度": {"verdicts": [{"idx": 0, "relevant": True}, {"idx": 1, "relevant": True}]},
        })
        with self.assertRaises(JudgeSchemaError):
            await context_precision("q", "a", ["c0", "c1"], judge=judge)

    async def test_string_or_int_relevant_is_rejected(self):
        for bad in ("true", 1):
            judge = make_judge({"精準度": {"verdicts": [{"idx": 1, "relevant": bad}]}})
            with self.subTest(bad=bad), self.assertRaises(JudgeSchemaError):
                await context_precision("q", "a", ["c0"], judge=judge)


class ContextPrecisionNumberingTests(unittest.TestCase):
    """M12：候選片段的標籤、片段自帶的 [n]、回答的引用必須是同一個數字，而且每個片段只出現一次號碼。"""

    _CTXS = [
        "[1] 報告：a.pdf（市場 TW，日期 2026-01-01）\n段落一",
        "[2] 報告：b.pdf（市場 US，日期 2026-02-02）\n段落二",
    ]

    def test_labels_match_the_contexts_own_numbers(self):
        payload = context_precision_payload("問", "答 [2]", self._CTXS)
        self.assertEqual(re.findall(r"^\[(\d+)\] ", payload, re.MULTILINE), ["1", "2"])
        self.assertIn("[1] 報告：a.pdf", payload)
        self.assertIn("[2] 報告：b.pdf", payload)

    def test_no_double_numbering(self):
        payload = context_precision_payload("問", "答", self._CTXS)
        self.assertNotIn("[0]", payload)
        self.assertNotIn("[1]\n[1]", payload)
        self.assertEqual(payload.count("[1]"), 1)
        self.assertEqual(payload.count("[2]"), 1)

    def test_contexts_without_a_leading_number_are_numbered_by_position(self):
        payload = context_precision_payload("問", "答", ["片段甲", "片段乙"])
        self.assertIn("[1] 片段甲", payload)
        self.assertIn("[2] 片段乙", payload)

    def test_prompt_says_one_based(self):
        self.assertIn("從 1 起算", CTX_RELEVANCE_SYS)
        self.assertNotIn("0-based", CTX_RELEVANCE_SYS)


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

    async def test_no_generated_questions_is_a_schema_error(self):
        """v1 記 0.0，等於把「judge 沒答」當成「答非所問」。v2 判 schema 錯，由 run_ragas 記 None。"""
        for out in ({"questions": []}, {"questions": ["  "]}, {"questions": "一個問題"}, {}):
            judge = make_judge({"反推": out})
            with self.subTest(out=out), self.assertRaises(JudgeSchemaError):
                await answer_relevancy("orig", "a", judge=judge, embed=make_embed({}))


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

    async def test_no_questions_raises_instead_of_empty_detail(self):
        with self.assertRaises(JudgeSchemaError):
            await answer_relevancy_detailed(
                "orig", "a", judge=make_judge({"反推": {"questions": []}}), embed=make_embed({})
            )

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
