import asyncio
import contextlib
import io
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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
                              allow_web=False, retries=2, meta=None, max_tokens=None, task=None):
            for c in ["台積電", "展望", "正向 [1]"]:
                yield c

        async def fake_judge(system, user):
            if "拆解" in system:
                return {"statements": ["台積電展望正向"]}
            if "佐證" in system:
                return {"verdicts": [{"idx": 0, "supported": True}]}
            if "精準度" in system:
                return {"verdicts": [{"idx": 1, "relevant": True}]}  # CP 候選 1 起編號（schema v2）
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
    """與 smoke 測試同款的三指標假 judge（回單一 statement/verdict/question）。

    CP 依 payload 裡實際的候選數回判定：schema v2 要求 idx 恰好是 1..n，多一個少一個都是錯。
    """
    if "拆解" in system:
        return {"statements": ["陳述"]}
    if "佐證" in system:
        return {"verdicts": [{"idx": 0, "supported": True}]}
    if "精準度" in system:
        labels = re.findall(r"^\[(\d+)\] ", user, re.MULTILINE)
        return {"verdicts": [{"idx": int(n), "relevant": True} for n in labels]}
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
                              allow_web=False, retries=2, meta=None, max_tokens=None, task=None):
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
                              allow_web=False, retries=2, meta=None, max_tokens=None, task=None):
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
                              allow_web=False, retries=2, meta=None, max_tokens=None, task=None):
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

        async def fake_eval_question(q, *, judge, embed, retrieval_params, agentic=False,
                                     gen_model=rr.DEFAULT_MODEL):
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

        # 預設模型是 deepseek-flash，預檢要金鑰：只給假值（run 已換成假的，不會送出任何請求）
        env = mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": "fixed-test-secret-deepseek0"})
        env.start()
        self.addCleanup(env.stop)
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


# ── 量尺可追溯（DeepSeek 遷移 PR-06）──────────────────────────────────────


async def _one_ctx_retrieve(question, *, filters=None, **params):
    return [_FakeSource()], "[1] 報告：a.pdf（市場 TW，日期 2026-01-01）\n某段落內容"


async def _cited_stream(prompt, *, system=None, model=None, timeout=120.0,
                        allow_web=False, retries=2, meta=None, max_tokens=None, task=None):
    yield "答案 [1]"


class JudgeErrorIsPerMetricTests(unittest.IsolatedAsyncioTestCase):
    """M8：judge 出錯只讓該指標為 None，不讓整題記 error。"""

    async def _eval(self, judge):
        saved = _install_fakes(["retrieve_context", "stream_completion"])
        try:
            rr.retrieve_context = _one_ctx_retrieve
            rr.stream_completion = _cited_stream
            return await rr.eval_question(
                {"id": "q001", "question": "台積電展望"},
                judge=judge, embed=_flat_embed, retrieval_params={},
            )
        finally:
            _restore(saved)

    async def test_context_precision_judge_error_keeps_other_metrics(self):
        async def judge(system, user):
            if "精準度" in system:
                raise rr.JudgeError("unbalanced JSON")
            return await _metrics_judge(system, user)

        out = await self._eval(judge)
        self.assertNotIn("error", out)
        self.assertIsNone(out["context_precision"])
        self.assertAlmostEqual(out["faithfulness"], 1.0)
        self.assertAlmostEqual(out["answer_relevancy"], 1.0)
        self.assertEqual(list(out["judge_errors"]), ["context_precision"])
        self.assertIn("JudgeError", out["judge_errors"]["context_precision"])

    async def test_llm_unavailable_in_decompose_only_nulls_faithfulness(self):
        async def judge(system, user):
            if "拆解" in system:
                raise rr.LLMUnavailableError("529")
            return await _metrics_judge(system, user)

        out = await self._eval(judge)
        self.assertNotIn("error", out)
        self.assertIsNone(out["faithfulness"])
        self.assertIn("faithfulness", out["judge_errors"])
        agg = aggregate([out])
        # judge 出錯不算「無脈絡」，也不改變 n_effective 的組成
        self.assertEqual(agg["n_no_context"], 0)
        self.assertEqual(agg["n_judge_errors"], 1)
        self.assertEqual(agg["n_errors"], 0)

    async def test_schema_error_only_nulls_that_metric(self):
        """schema v2：CP 照 0 起編號（v1 雙重編號的典型錯位）→ 重試後仍錯，只有 CP 為 None。"""
        calls = {"cp": 0}

        async def judge(system, user):
            if "精準度" in system:
                calls["cp"] += 1
                return {"verdicts": [{"idx": 0, "relevant": True}]}
            return await _metrics_judge(system, user)

        out = await self._eval(judge)
        self.assertNotIn("error", out)
        self.assertIsNone(out["context_precision"])
        self.assertIn("JudgeSchemaError", out["judge_errors"]["context_precision"])
        self.assertAlmostEqual(out["faithfulness"], 1.0)
        self.assertEqual(calls["cp"], 2)  # schema 錯重試 1 次

    async def test_answer_relevancy_without_questions_is_a_judge_error_not_zero(self):
        async def judge(system, user):
            if "反推" in system:
                return {"questions": []}
            return await _metrics_judge(system, user)

        out = await self._eval(judge)
        self.assertIsNone(out["answer_relevancy"])
        self.assertIn("answer_relevancy", out["judge_errors"])

    async def test_non_judge_exception_still_fails_the_whole_case(self):
        """程式錯誤不是量尺問題，吞成 None 會把 bug 藏成缺值。"""
        async def judge(system, user):
            raise KeyError("bug")

        out = await self._eval(judge)
        self.assertIn("error", out)

    async def test_success_records_new_case_fields_and_io(self):
        out = await self._eval(_metrics_judge)
        self.assertIs(out["cited"], True)
        self.assertIs(out["simplified"], False)
        self.assertIs(out["gen_truncated"], False)
        self.assertNotIn("judge_errors", out)
        io_ = out["_io"]
        self.assertEqual(io_["answer"], "答案 [1]")
        self.assertEqual(len(io_["contexts"]), 1)
        tasks = [c["task"] for c in io_["judge_calls"]]
        self.assertEqual(tasks, ["decompose", "ground", "context_precision", "answer_relevancy"])

    async def test_generator_model_reaches_stream_completion(self):
        seen = {}

        async def stream(prompt, *, system=None, model=None, timeout=120.0,
                         allow_web=False, retries=2, meta=None, max_tokens=None, task=None):
            seen["model"] = model
            seen["timeout"] = timeout
            yield "答案 [1]"

        saved = _install_fakes(["retrieve_context", "stream_completion"])
        try:
            rr.retrieve_context = _one_ctx_retrieve
            rr.stream_completion = stream
            await rr.eval_question(
                {"id": "q1", "question": "x"}, judge=_metrics_judge, embed=_flat_embed,
                retrieval_params={}, gen_model="deepseek-flash",
            )
        finally:
            _restore(saved)
        self.assertEqual(seen, {"model": "deepseek-flash", "timeout": rr.GEN_TIMEOUT})


class GenerateTruncationTests(unittest.IsolatedAsyncioTestCase):
    """n_truncated 取 stream_completion 回報的截斷訊號，不再用牆鐘推定（含 529 重試會誤判）。"""

    async def test_truncation_signal_from_stream_completion(self):
        async def cut(prompt, *, system=None, model=None, timeout=120.0,
                      allow_web=False, retries=2, meta=None, max_tokens=None, task=None):
            yield "前半"
            meta["truncated"] = True  # CLI 逾時後對已吐字 fail-open，只留這個記號

        saved = _install_fakes(["stream_completion"])
        try:
            rr.stream_completion = cut
            text, truncated = await rr._generate_answer("q", "ctx", timeout=5.0)
        finally:
            _restore(saved)
        self.assertEqual(text, "前半")
        self.assertTrue(truncated)

    async def test_slow_but_complete_is_not_truncated(self):
        """含 529 重試的牆鐘可以超過單次逾時，但只要成功那次沒撞到逾時就不是截斷。"""
        async def slow(prompt, *, system=None, model=None, timeout=120.0,
                       allow_web=False, retries=2, meta=None, max_tokens=None, task=None):
            await asyncio.sleep(timeout + 0.02)
            yield "完整答案"
            meta["truncated"] = False

        saved = _install_fakes(["stream_completion"])
        try:
            rr.stream_completion = slow
            _text, truncated = await rr._generate_answer("q", "ctx", timeout=0.05)
        finally:
            _restore(saved)
        self.assertFalse(truncated)

    async def test_normal_completion_is_not_truncated(self):
        saved = _install_fakes(["stream_completion"])
        try:
            rr.stream_completion = _cited_stream
            _text, truncated = await rr._generate_answer("q", "ctx", timeout=5.0)
        finally:
            _restore(saved)
        self.assertFalse(truncated)


class CitationTests(unittest.TestCase):
    def test_only_existing_source_numbers_count(self):
        self.assertTrue(rr.has_valid_citation("見 [2]。", 2))
        self.assertFalse(rr.has_valid_citation("見 [3]。", 2))   # 越界的編號不算引用
        self.assertFalse(rr.has_valid_citation("見 [0]。", 2))
        self.assertFalse(rr.has_valid_citation("沒有引用", 2))
        self.assertFalse(rr.has_valid_citation("", 0))


class AggregateNewMetricsTests(unittest.TestCase):
    def test_rates_and_counts(self):
        per_q = [
            {"id": "q1", "faithfulness": 1.0, "cited": True, "simplified": False, "gen_truncated": False},
            {"id": "q2", "faithfulness": 0.5, "cited": False, "simplified": True, "gen_truncated": True},
            {"id": "q3", "faithfulness": None, "cited": True, "simplified": False, "gen_truncated": False,
             "judge_errors": {"faithfulness": "JudgeError: x", "context_precision": "JudgeError: y"}},
            {"id": "q4", "faithfulness": None, "cited": False, "simplified": False, "gen_truncated": False},
            {"id": "q5", "error": "boom", "cited": True, "gen_truncated": True},  # error 不入任何比例
        ]
        agg = aggregate(per_q)
        self.assertAlmostEqual(agg["citation_rate"], 0.5)
        self.assertAlmostEqual(agg["simplified_residual_rate"], 0.25)
        self.assertEqual(agg["n_truncated"], 1)
        self.assertEqual(agg["n_judge_errors"], 2)
        self.assertEqual(agg["n_no_context"], 1)  # 只有 q4；q3 的 None 是 judge 出錯
        self.assertEqual(agg["n_errors"], 1)

    def test_judged_question_set_is_recorded_per_metric(self):
        """judge 出錯的題不入該指標均值：入均值的題數與題目集合都要記下來，eval_compare 才
        分得出「題數相同、題目不同」（審查 M-1）。"""
        from scripts import eval_compare as ec

        per_q = [
            {"id": "q1", "faithfulness": 1.0, "context_precision": 0.5, "answer_relevancy": 0.9},
            {"id": "q2", "faithfulness": None, "context_precision": 0.7, "answer_relevancy": 0.8,
             "judge_errors": {"faithfulness": "JudgeError: x"}},
            {"id": "q3", "error": "boom"},
        ]
        agg = aggregate(per_q)
        self.assertEqual(agg["n_effective_faithfulness"], 1)
        self.assertEqual(agg["n_effective_context_precision"], 2)
        self.assertEqual(agg["n_effective_answer_relevancy"], 2)
        self.assertEqual(agg["judged_ids_sha"], ec.judged_ids_sha(per_q))
        swapped = [dict(per_q[0], faithfulness=None), dict(per_q[1], faithfulness=0.5)]
        self.assertEqual(aggregate(swapped)["n_effective_faithfulness"], 1)
        self.assertNotEqual(aggregate(swapped)["judged_ids_sha"], agg["judged_ids_sha"])

    def test_every_summary_key_is_classified_by_eval_compare(self):
        """run_ragas 新增的 summary 鍵沒在 METRIC_SPECS 補方向，eval_compare 會回 3。"""
        from scripts import eval_compare as ec

        agg = aggregate([{"id": "q1", "faithfulness": 1.0, "context_precision": 0.5, "answer_relevancy": 0.9}])
        self.assertEqual(sorted(set(agg) - set(ec.METRIC_SPECS)), [])

    def test_old_cases_without_new_fields_yield_none_rates(self):
        agg = aggregate([{"id": "q1", "faithfulness": 1.0}])
        self.assertIsNone(agg["citation_rate"])
        self.assertIsNone(agg["simplified_residual_rate"])
        self.assertEqual(agg["n_truncated"], 0)
        self.assertEqual(agg["n_judge_errors"], 0)


def _run(**kw):
    base = {
        "id": "q1", "question": "題", "faithfulness": 1.0, "context_precision": 0.5,
        "answer_relevancy": 0.6, "n_contexts": 3, "latency_ms": 100,
        "cited": True, "simplified": False, "gen_truncated": False,
    }
    base.update(kw)
    return base


class MergeRepeatsTests(unittest.TestCase):
    """M8：repeat 的彙總規則——每題每指標跨次取平均，計數恆為整數。"""

    def test_metrics_are_averaged_per_question_skipping_none(self):
        merged = rr.merge_repeats([
            _run(faithfulness=1.0, latency_ms=100, cited=True),
            _run(faithfulness=None, latency_ms=201, cited=False),
            _run(faithfulness=0.5, latency_ms=300, cited=True),
        ])
        self.assertAlmostEqual(merged["faithfulness"], 0.75)
        self.assertEqual(merged["latency_ms"], 200)  # (100+201+300)/3=200.33 → 四捨五入成整數
        self.assertIsInstance(merged["latency_ms"], int)
        self.assertAlmostEqual(merged["cited"], 2 / 3)
        self.assertEqual(merged["n_runs"], 3)
        self.assertEqual(merged["n_runs_ok"], 3)
        self.assertEqual(len(merged["runs"]), 3)

    def test_error_runs_are_skipped_but_all_errors_fail_the_question(self):
        merged = rr.merge_repeats([_run(error="boom"), _run(faithfulness=0.4)])
        self.assertNotIn("error", merged)
        self.assertAlmostEqual(merged["faithfulness"], 0.4)
        self.assertEqual(merged["n_runs_ok"], 1)
        dead = rr.merge_repeats([_run(error="a"), _run(error="b")])
        self.assertEqual(dead["error"], "b")

    def test_judge_error_counts_only_when_no_run_has_a_value(self):
        partial = rr.merge_repeats([
            _run(context_precision=None, judge_errors={"context_precision": "JudgeError: x"}),
            _run(context_precision=0.8),
        ])
        self.assertAlmostEqual(partial["context_precision"], 0.8)
        self.assertNotIn("judge_errors", partial)
        total = rr.merge_repeats([
            _run(context_precision=None, judge_errors={"context_precision": "JudgeError: x"}),
            _run(context_precision=None, judge_errors={"context_precision": "JudgeError: y"}),
        ])
        self.assertIsNone(total["context_precision"])
        self.assertEqual(total["judge_errors"], {"context_precision": "JudgeError: y"})

    def test_private_io_never_reaches_the_report(self):
        merged = rr.merge_repeats([_run(_io={"answer": "x"}), _run()])
        self.assertTrue(all("_io" not in r for r in merged["runs"]))

    def test_aggregate_over_merged_cases_keeps_integer_sample_counts(self):
        cases = [
            rr.merge_repeats([_run(id="q1"), _run(id="q1", gen_truncated=True)]),
            rr.merge_repeats([_run(id="q2", error="x"), _run(id="q2", error="y")]),
        ]
        agg = aggregate(cases)
        self.assertEqual(agg["n"], 2)            # 題數，不乘 repeat
        self.assertEqual(agg["n_errors"], 1)
        self.assertEqual(agg["n_truncated"], 1)
        for key in ("n", "n_errors", "n_no_context", "n_judge_errors", "n_truncated"):
            self.assertIsInstance(agg[key], int, key)


class RunTraceabilityTests(unittest.IsolatedAsyncioTestCase):
    """run() 的結果檔：summary 的三個 META 鍵、config 快照、repeat 與 --dump-io。"""

    async def _run_with(self, td, *, repeat=1, dump=False, agentic=False, judge_model="deepseek-v4-pro"):
        questions = [{"id": "q/1", "question": "題1"}, {"id": "q2", "question": "題2"}]
        ds = Path(td) / "ds.json"
        ds.write_text(json.dumps({"questions": questions}, ensure_ascii=False), encoding="utf-8")
        seen = {"gen_model": [], "calls": 0}

        async def fake_eval_question(q, *, judge, embed, retrieval_params, agentic=False,
                                     gen_model=rr.DEFAULT_MODEL):
            seen["gen_model"].append(gen_model)
            seen["calls"] += 1
            return {
                **_run(id=q["id"], question=q["question"], faithfulness=0.5 + 0.1 * (seen["calls"] % 2)),
                "_io": {"question": q["question"], "filters": {}, "context": "[1] 報告：a",
                        "contexts": ["[1] 報告：a"], "answer": "答 [1]", "judge_calls": []},
            }

        saved = _install_fakes(["eval_question"])
        try:
            rr.eval_question = fake_eval_question
            report = await rr.run(
                ds, out_path=Path(td) / "out.json", concurrency=1, repeat=repeat,
                generator_model="deepseek-flash", judge_model=judge_model,
                dump_dir=(Path(td) / "frozen") if dump else None, agentic=agentic,
            )
        finally:
            _restore(saved)
        return report, seen, ds

    async def test_summary_carries_the_three_meta_keys(self):
        with tempfile.TemporaryDirectory() as td:
            report, _seen, _ds = await self._run_with(td)
        s = report["summary"]
        self.assertEqual(s["judge_model"], "deepseek-v4-pro")
        self.assertEqual(s["judge_prompt_sha"], rr.judge_prompt_sha())
        self.assertEqual(len(s["judge_prompt_sha"]), 64)
        self.assertEqual(s["judge_schema_version"], rr.JUDGE_SCHEMA_VERSION)

    async def test_config_records_generator_tasks_commit_and_queryset_sha(self):
        import hashlib

        with tempfile.TemporaryDirectory() as td:
            report, seen, ds = await self._run_with(td, agentic=True)
            expected_sha = hashlib.sha256(ds.read_bytes()).hexdigest()
            written = json.loads((Path(td) / "out.json").read_text(encoding="utf-8"))
        cfg = report["config"]
        self.assertEqual(written["config"], cfg)
        self.assertEqual(seen["gen_model"], ["deepseek-flash", "deepseek-flash"])
        self.assertEqual(cfg["gen_model"], "deepseek-flash")
        self.assertEqual(cfg["models"]["generate"], "deepseek-flash")
        for task in ("judge_decompose", "judge_ground", "judge_context_precision", "judge_answer_relevancy"):
            self.assertEqual(cfg["models"][task], "deepseek-v4-pro")
        self.assertIn("agentic_plan", cfg["models"])     # agentic 才會觸發規劃與評估步
        self.assertIn("agentic_evaluate", cfg["models"])
        self.assertEqual(cfg["judge"]["model"], "deepseek-v4-pro")
        self.assertEqual(cfg["judge"]["provider"], "deepseek_http")
        self.assertEqual(cfg["queryset_sha256"], expected_sha)
        self.assertIn("commit", cfg)
        self.assertTrue(cfg["agentic"])
        self.assertEqual(cfg["concurrency"], 1)
        self.assertEqual(cfg["rerank_top_m"], 0)

    async def test_non_agentic_config_lists_no_planner_task(self):
        with tempfile.TemporaryDirectory() as td:
            report, _seen, _ds = await self._run_with(td)
        self.assertNotIn("agentic_plan", report["config"]["models"])

    async def test_repeat_runs_every_question_n_times_and_merges(self):
        with tempfile.TemporaryDirectory() as td:
            report, seen, _ds = await self._run_with(td, repeat=3)
        self.assertEqual(seen["calls"], 6)
        self.assertEqual(report["summary"]["n"], 2)
        self.assertEqual(report["config"]["repeat"], 3)
        self.assertEqual(report["cases"][0]["n_runs"], 3)
        self.assertTrue(all("_io" not in c for c in report["cases"]))

    async def test_dump_io_writes_one_file_per_run_without_leaking_into_report(self):
        with tempfile.TemporaryDirectory() as td:
            report, _seen, _ds = await self._run_with(td, repeat=2, dump=True)
            files = sorted(p.name for p in (Path(td) / "frozen").iterdir())
            doc = json.loads((Path(td) / "frozen" / "q_1-r1.json").read_text(encoding="utf-8"))
        self.assertEqual(files, ["q2-r1.json", "q2-r2.json", "q_1-r1.json", "q_1-r2.json"])
        self.assertEqual(doc["answer"], "答 [1]")
        self.assertEqual(doc["gen_model"], "deepseek-flash")
        self.assertEqual(doc["judge_model"], "deepseek-v4-pro")
        self.assertEqual(len(doc["sha256"]), 64)
        self.assertIn("judge_calls", doc)
        self.assertTrue(all("_io" not in r for c in report["cases"] for r in c["runs"]))

    async def test_repeat_must_be_positive(self):
        with self.assertRaises(ValueError):
            await rr.run("unused.json", out_path=None, repeat=0)

    async def test_judge_lineage_is_labelled_in_config_and_notes_without_warning(self):
        """PR-26/27：DeepSeek judge 是正式的新量尺系譜，不再印「未校準」WARNING；系譜記在
        config.judge.lineage 與頂層 notes（eval_compare 會印），不進 summary（否則要在 METRIC_SPECS 分類）。"""
        for judge, provider, lineage in (("deepseek-flash", "deepseek_http", "deepseek-2026-09"),
                                         ("claude-haiku-4-5", "unsupported", "claude-haiku")):
            with self.subTest(judge=judge), tempfile.TemporaryDirectory() as td:
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    report, seen, _ds = await self._run_with(td, judge_model=judge)
                    written = json.loads((Path(td) / "out.json").read_text(encoding="utf-8"))
                self.assertEqual(seen["calls"], 2)
                self.assertEqual(report["config"]["judge"]["provider"], provider)
                self.assertEqual(report["config"]["judge"]["lineage"], lineage)
                self.assertEqual(report["summary"]["judge_model"], judge)
                self.assertNotIn("lineage", report["summary"])
                self.assertNotIn("judge_lineage", report["summary"])
                self.assertEqual(written["notes"], report["notes"])
                self.assertIn(lineage, report["notes"][0])
                self.assertNotIn("WARNING", err.getvalue())

    def test_deepseek_notes_say_old_baseline_is_not_comparable(self):
        note = rr.lineage_notes("deepseek-flash")[0]
        self.assertIn("baseline-2026-09-02.json", note)
        self.assertIn("回 2", note)


class JudgeProviderTests(unittest.TestCase):
    def test_provider_and_lineage_follow_the_http_whitelist(self):
        from app.services.llm_models import HTTP_MODELS

        for model in sorted(HTTP_MODELS):
            with self.subTest(model=model):
                self.assertEqual(rr.judge_provider(model), "deepseek_http")
                self.assertEqual(rr.judge_lineage(model), rr.JUDGE_LINEAGE_DEEPSEEK)
        # PR-M：白名單外沒有 backend（判 unsupported）；系譜函式照舊把它們歸 Claude 時代（讀舊結果檔用）
        for model in ("claude-haiku-4-5", "claude-sonnet-5"):
            with self.subTest(model=model):
                self.assertEqual(rr.judge_provider(model), "unsupported")
                self.assertEqual(rr.judge_lineage(model), rr.JUDGE_LINEAGE_CLAUDE)

    def test_uncalibrated_warning_is_gone(self):
        """TODO(PR-26) 的警告在 judge 正式切換後刪除；印結果時改印系譜。"""
        self.assertFalse(hasattr(rr, "uncalibrated_judge_warning"))
        summary = {
            "faithfulness": 0.9, "context_precision": 0.8, "answer_relevancy": 0.6,
            "n": 1, "n_errors": 0, "n_no_context": 0, "thresholds_pass": True,
            "judge_model": "deepseek-flash", "judge_schema_version": 2, "judge_prompt_sha": "x" * 64,
        }
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rr._print_summary({"summary": summary})
        self.assertEqual(err.getvalue(), "")
        self.assertIn("系譜 deepseek-2026-09", out.getvalue())


class MainNewFlagsTests(unittest.TestCase):
    def _main(self, argv):
        captured = {}

        async def fake_run(dataset, **kwargs):
            captured.update(kwargs)
            return {"summary": {}, "cases": []}

        saved = _install_fakes(["run"])
        old = sys.argv
        try:
            rr.run = fake_run
            sys.argv = ["run_ragas.py", *argv, "--json"]
            # 金鑰預檢另有 tests/test_llm_env_loading.py；這裡只記下它被問了哪些模型。
            with contextlib.redirect_stdout(io.StringIO()), mock.patch.object(rr, "require_llm_key") as req:
                rr._main()
            captured["_required_models"] = req.call_args.args[0]
        finally:
            _restore(saved)
            sys.argv = old
        return captured

    def test_defaults_do_not_overwrite_a_baseline(self):
        kw = self._main([])
        self.assertEqual(kw["out_path"], "eval/candidate-ragas.json")
        self.assertEqual(kw["generator_model"], rr.DEFAULT_MODEL)
        self.assertEqual(kw["repeat"], 1)
        self.assertIsNone(kw["dump_dir"])

    def test_flags_are_passed_through(self):
        kw = self._main(["--generator-model", "deepseek-flash", "--repeat", "3", "--dump-io", "data/eval_frozen/x"])
        self.assertEqual(kw["generator_model"], "deepseek-flash")
        self.assertEqual(kw["repeat"], 3)
        self.assertEqual(kw["dump_dir"], "data/eval_frozen/x")

    def test_key_precheck_covers_generator_and_judge(self):
        kw = self._main(["--generator-model", "deepseek-flash"])
        self.assertEqual(kw["_required_models"], ["deepseek-flash", rr.DEFAULT_JUDGE_MODEL])


if __name__ == "__main__":
    unittest.main()
