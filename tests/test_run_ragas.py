import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from eval.run_ragas import aggregate, split_contexts  # noqa: E402


class SplitContextsTests(unittest.TestCase):
    def test_splits_on_numbered_headers(self):
        context = (
            "[1] 報告：a.pdf（市場 TW，日期 2026-01-01）\n段落一\n段落二\n\n"
            "[2] 報告：b.pdf（市場 US，日期 2026-02-02）\n段落三"
        )
        parts = split_contexts(context)
        self.assertEqual(len(parts), 2)
        self.assertTrue(parts[0].startswith("[1] 報告：a.pdf"))
        self.assertTrue(parts[1].startswith("[2] 報告：b.pdf"))

    def test_empty_context_is_empty_list(self):
        self.assertEqual(split_contexts(""), [])
        self.assertEqual(split_contexts("   "), [])


class AggregateTests(unittest.TestCase):
    def test_means_skip_none_and_error(self):
        per_q = [
            {"id": "q001", "faithfulness": 1.0, "context_precision": 0.8, "answer_relevancy": 0.9},
            {"id": "q002", "faithfulness": None, "context_precision": 0.6, "answer_relevancy": 0.7},
            {"id": "q003", "error": "boom"},
        ]
        agg = aggregate(per_q)
        self.assertAlmostEqual(agg["faithfulness"], 1.0)          # 只算 q001（q002 None、q003 error）
        self.assertAlmostEqual(agg["context_precision"], 0.7)     # (0.8+0.6)/2
        self.assertAlmostEqual(agg["answer_relevancy"], 0.8)      # (0.9+0.7)/2
        self.assertEqual(agg["n"], 3)
        self.assertEqual(agg["n_errors"], 1)
        self.assertEqual(agg["n_no_context"], 1)

    def test_thresholds_pass_flag(self):
        per_q = [{"id": "q1", "faithfulness": 0.95, "context_precision": 0.85, "answer_relevancy": 0.9}]
        self.assertTrue(aggregate(per_q)["thresholds_pass"])
        per_q = [{"id": "q1", "faithfulness": 0.80, "context_precision": 0.85, "answer_relevancy": 0.9}]
        self.assertFalse(aggregate(per_q)["thresholds_pass"])

    def test_all_errors_means_none(self):
        agg = aggregate([{"id": "q1", "error": "x"}])
        self.assertIsNone(agg["faithfulness"])
        self.assertFalse(agg["thresholds_pass"])


import eval.run_ragas as rr  # noqa: E402


class _FakeSource:
    pass


def _install_fakes(monkeypatch_targets):
    """把 retrieve_context / stream_completion 換成假物；回原值供還原。"""
    saved = {name: getattr(rr, name) for name in monkeypatch_targets}
    return saved


def _restore(saved):
    for name, val in saved.items():
        setattr(rr, name, val)


class EvalQuestionTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_mocked_smoke(self):
        async def fake_retrieve(question, *, filters=None, **params):
            ctx = "[1] 報告：a.pdf（市場 TW，日期 2026-01-01）\n某段落內容"
            return [_FakeSource()], ctx

        async def fake_stream(prompt, *, system=None, model=None, timeout=120.0,
                              allow_web=False, retries=2):
            for c in ["台積電", "展望", "正向 [1]"]:
                yield c

        async def fake_judge(system, user):
            if "拆解" in system:
                return {"statements": ["台積電展望正向"]}
            if "佐證" in system:
                return {"verdicts": [{"idx": 0, "supported": True}]}
            if "精準度" in system:
                return {"verdicts": [{"idx": 0, "relevant": True}]}
            if "反推" in system:
                return {"questions": ["台積電展望如何"]}
            raise AssertionError(system[:30])

        def fake_embed(text):
            return [1.0, 0.0, 0.0]

        saved = _install_fakes(["retrieve_context", "stream_completion"])
        try:
            rr.retrieve_context = fake_retrieve
            rr.stream_completion = fake_stream
            out = await rr.eval_question(
                {"id": "q001", "question": "台積電展望"},
                judge=fake_judge, embed=fake_embed, retrieval_params={},
            )
        finally:
            _restore(saved)

        self.assertNotIn("error", out)
        self.assertEqual(out["id"], "q001")
        self.assertAlmostEqual(out["faithfulness"], 1.0)
        self.assertAlmostEqual(out["context_precision"], 1.0)
        self.assertAlmostEqual(out["answer_relevancy"], 1.0)
        self.assertEqual(out["n_contexts"], 1)

    async def test_retrieve_failure_is_fail_open(self):
        async def boom_retrieve(question, *, filters=None, **params):
            raise RuntimeError("db down")

        saved = _install_fakes(["retrieve_context"])
        try:
            rr.retrieve_context = boom_retrieve
            out = await rr.eval_question(
                {"id": "q001", "question": "x"},
                judge=None, embed=None, retrieval_params={},
            )
        finally:
            _restore(saved)

        self.assertIn("error", out)
        self.assertIn("db down", out["error"])
        self.assertEqual(out["id"], "q001")


if __name__ == "__main__":
    unittest.main()
