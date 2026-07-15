import asyncio
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services.agentic_qa import AgenticOutcome  # noqa: E402
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


class AggregateLatencyTests(unittest.TestCase):
    def test_latency_stats_over_non_error_cases_only(self):
        per_q = [
            {"id": "q1", "faithfulness": 1.0, "latency_ms": 100},
            {"id": "q2", "faithfulness": 1.0, "latency_ms": 200},
            {"id": "q3", "faithfulness": 1.0, "latency_ms": 300},
            {"id": "q4", "error": "boom", "latency_ms": 99999},  # error 不入延遲統計
            {"id": "q5", "faithfulness": 1.0},  # 舊報表 case 無 latency_ms（相容）
        ]
        agg = aggregate(per_q)
        self.assertAlmostEqual(agg["latency_ms_mean"], 200.0)
        self.assertEqual(agg["latency_ms_p50"], 200)   # 最近秩：ceil(0.5*3)=2 → 第 2 小
        self.assertEqual(agg["latency_ms_p95"], 300)   # ceil(0.95*3)=3 → 第 3 小

    def test_no_latency_values_means_none(self):
        agg = aggregate([{"id": "q1", "error": "x"}])
        self.assertIsNone(agg["latency_ms_mean"])
        self.assertIsNone(agg["latency_ms_p50"])
        self.assertIsNone(agg["latency_ms_p95"])

    def test_single_case_percentiles(self):
        agg = aggregate([{"id": "q1", "faithfulness": 1.0, "latency_ms": 150}])
        self.assertAlmostEqual(agg["latency_ms_mean"], 150.0)
        self.assertEqual(agg["latency_ms_p50"], 150)
        self.assertEqual(agg["latency_ms_p95"], 150)


async def _metrics_judge(system, user):
    """與 smoke 測試同款的三指標假 judge（回單一 statement/verdict/question）。"""
    if "拆解" in system:
        return {"statements": ["陳述"]}
    if "佐證" in system:
        return {"verdicts": [{"idx": 0, "supported": True}]}
    if "精準度" in system:
        return {"verdicts": [{"idx": 0, "relevant": True}, {"idx": 1, "relevant": True}]}
    if "反推" in system:
        return {"questions": ["反推問題"]}
    raise AssertionError(system[:30])


def _flat_embed(text):
    return [1.0, 0.0, 0.0]


class EvalQuestionLatencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_latency_ms_recorded_and_excludes_judge(self):
        async def fake_retrieve(question, *, filters=None, **params):
            ctx = "[1] 報告：a.pdf（市場 TW，日期 2026-01-01）\n某段落內容"
            return [_FakeSource()], ctx

        async def fake_stream(prompt, *, system=None, model=None, timeout=120.0,
                              allow_web=False, retries=2):
            yield "答案 [1]"

        async def slow_judge(system, user):
            await asyncio.sleep(0.3)  # judge 牆鐘不得計入 latency_ms
            return await _metrics_judge(system, user)

        saved = _install_fakes(["retrieve_context", "stream_completion"])
        try:
            rr.retrieve_context = fake_retrieve
            rr.stream_completion = fake_stream
            out = await rr.eval_question(
                {"id": "q001", "question": "台積電展望"},
                judge=slow_judge, embed=_flat_embed, retrieval_params={},
            )
        finally:
            _restore(saved)

        self.assertNotIn("error", out)
        self.assertIsInstance(out["latency_ms"], int)
        self.assertGreaterEqual(out["latency_ms"], 0)
        self.assertLess(out["latency_ms"], 250)  # 三次 judge 共 sleep 0.9s，未被計入


class EvalQuestionAgenticTests(unittest.IsolatedAsyncioTestCase):
    """--agentic 單題流程：plan 並行、run_agentic 合併結果、歸因欄位落 case。"""

    _MERGED_CTX = (
        "[1] 報告：a.pdf（市場 TW，日期 2026-01-01）\n第一輪段落\n\n"
        "[2] 報告：b.pdf（市場 US，日期 2026-02-02）\n補查段落"
    )

    def _build_fakes(self, received):
        async def fake_retrieve(question, *, filters=None, **params):
            ctx = "[1] 報告：a.pdf（市場 TW，日期 2026-01-01）\n第一輪段落"
            return [_FakeSource()], ctx

        async def fake_plan(question, *, profile):
            received["plan_profile"] = profile
            return "PLAN"

        merged_ctx = self._MERGED_CTX

        async def fake_run_agentic(question, **kwargs):
            received["agentic_kwargs"] = kwargs
            yield ("stage", "evaluating")  # 接線須忽略 stage 事件
            yield (
                "outcome",
                AgenticOutcome(
                    sources=[_FakeSource(), _FakeSource()],
                    context=merged_ctx,
                    rounds=2,
                    subqueries_run=["原問題", "補查A"],
                    skipped=1,
                    fresh_requested=False,
                    degraded=False,
                ),
            )

        async def fake_stream(prompt, *, system=None, model=None, timeout=120.0,
                              allow_web=False, retries=2):
            received["gen_prompt"] = prompt
            yield "答案 [1][2]"

        return fake_retrieve, fake_plan, fake_run_agentic, fake_stream

    async def test_agentic_uses_merged_outcome_and_records_attribution(self):
        received = {}
        fake_retrieve, fake_plan, fake_run_agentic, fake_stream = self._build_fakes(received)
        saved = _install_fakes(
            ["retrieve_context", "plan_queries", "run_agentic", "stream_completion"]
        )
        try:
            rr.retrieve_context = fake_retrieve
            rr.plan_queries = fake_plan
            rr.run_agentic = fake_run_agentic
            rr.stream_completion = fake_stream
            out = await rr.eval_question(
                {"id": "q001", "question": "多面向題"},
                judge=_metrics_judge, embed=_flat_embed,
                retrieval_params=dict(rr.RETRIEVAL_PARAMS, rerank_top_m=0),
                agentic=True,
            )
        finally:
            _restore(saved)

        self.assertNotIn("error", out)
        # 歸因欄位（AgenticOutcome → case）
        self.assertEqual(out["rounds"], 2)
        self.assertEqual(out["subqueries_run"], 2)
        self.assertEqual(out["skipped"], 1)
        self.assertIsInstance(out["latency_ms"], int)
        # 生成與指標吃合併後 context
        self.assertEqual(out["n_contexts"], 2)
        self.assertIn("補查段落", received["gen_prompt"])
        # run_agentic 收到 plan、固定 corpus_qa 決策、第一輪結果與 rerank_timeout
        self.assertEqual(received["plan_profile"], "qa")
        kw = received["agentic_kwargs"]
        self.assertEqual(kw["plan"], "PLAN")
        self.assertEqual(kw["decision"].scope, "corpus_qa")
        self.assertEqual(len(kw["first"][0]), 1)
        self.assertIn("rerank_timeout", kw["retrieval_params"])
        self.assertNotIn("max_reports", kw["retrieval_params"])

    async def test_default_path_does_not_plan_or_run_agentic(self):
        called = {"plan": 0, "agentic": 0}

        async def fake_retrieve(question, *, filters=None, **params):
            ctx = "[1] 報告：a.pdf（市場 TW，日期 2026-01-01）\n某段落內容"
            return [_FakeSource()], ctx

        async def fake_plan(question, *, profile):
            called["plan"] += 1
            return "PLAN"

        def fake_run_agentic(question, **kwargs):
            called["agentic"] += 1
            raise AssertionError("非 agentic 模式不得呼叫 run_agentic")

        async def fake_stream(prompt, *, system=None, model=None, timeout=120.0,
                              allow_web=False, retries=2):
            yield "答案 [1]"

        saved = _install_fakes(
            ["retrieve_context", "plan_queries", "run_agentic", "stream_completion"]
        )
        try:
            rr.retrieve_context = fake_retrieve
            rr.plan_queries = fake_plan
            rr.run_agentic = fake_run_agentic
            rr.stream_completion = fake_stream
            out = await rr.eval_question(
                {"id": "q001", "question": "單面向題"},
                judge=_metrics_judge, embed=_flat_embed, retrieval_params={},
            )
        finally:
            _restore(saved)

        self.assertNotIn("error", out)
        self.assertEqual(called, {"plan": 0, "agentic": 0})
        self.assertNotIn("rounds", out)
        self.assertNotIn("skipped", out)


class RunAgenticSerialTests(unittest.IsolatedAsyncioTestCase):
    async def test_agentic_forces_concurrency_one_and_passes_flag(self):
        questions = [{"id": f"q{i}", "question": f"題{i}"} for i in range(3)]
        seen = {"active": 0, "max_active": 0, "agentic": []}

        async def fake_eval_question(q, *, judge, embed, retrieval_params, agentic=False):
            seen["active"] += 1
            seen["max_active"] = max(seen["max_active"], seen["active"])
            seen["agentic"].append(agentic)
            await asyncio.sleep(0.02)
            seen["active"] -= 1
            return {
                "id": q["id"], "question": q["question"],
                "faithfulness": 1.0, "context_precision": 1.0,
                "answer_relevancy": 1.0, "n_contexts": 1,
                "latency_ms": 100, "rounds": 1, "subqueries_run": 1, "skipped": 0,
            }

        with tempfile.TemporaryDirectory() as td:
            ds = Path(td) / "ds.json"
            ds.write_text(
                json.dumps({"questions": questions}, ensure_ascii=False),
                encoding="utf-8",
            )
            saved = _install_fakes(["eval_question"])
            try:
                rr.eval_question = fake_eval_question
                report = await rr.run(ds, out_path=None, concurrency=3, agentic=True)
            finally:
                _restore(saved)

        self.assertEqual(seen["max_active"], 1)  # --agentic 強制序列跑
        self.assertEqual(seen["agentic"], [True, True, True])
        self.assertEqual(report["cases"][0]["rounds"], 1)
        self.assertEqual(report["cases"][0]["skipped"], 0)
        self.assertAlmostEqual(report["summary"]["latency_ms_mean"], 100.0)
        self.assertEqual(report["summary"]["latency_ms_p95"], 100)


class MainAgenticFlagTests(unittest.TestCase):
    def test_main_passes_agentic_flag_to_run(self):
        captured = {}

        async def fake_run(dataset, **kwargs):
            captured.update(kwargs)
            return {"summary": {}, "cases": []}

        saved = _install_fakes(["run"])
        argv = sys.argv
        try:
            rr.run = fake_run
            sys.argv = ["run_ragas.py", "--agentic", "--json"]
            with contextlib.redirect_stdout(io.StringIO()):
                rr._main()
        finally:
            _restore(saved)
            sys.argv = argv

        self.assertTrue(captured["agentic"])


if __name__ == "__main__":
    unittest.main()
