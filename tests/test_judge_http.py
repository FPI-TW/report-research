"""DeepSeek judge adapter 的接線（DeepSeek 遷移 PR-18）：生產忠實度與離線評測兩個入口。

全程不連網：`httpx.MockTransport` 注入 `llm_http._transport`，金鑰只用 gitleaks allowlist 內的假值。

釘住的契約：
1. **重試只有一層、最壞次數寫得出來**：一個階段（一次 call_validated）在 HTTP 路徑最多
   `judge_schema.HTTP_STAGE_MAX_REQUESTS`（3）個請求——adapter 的截斷／空回應／暫時性重試、schema 重試、
   `judge_json` 的重試三者不相乘（`judge_json` 自 PR-M 起不再有自己的重試）。
2. 各階段 `max_tokens`：拆解 8192、grounding 2048、CP 2048、反推問題 1024（第二版計畫 §6.5）。
3. 失敗分類：生產記 `degraded_reason`（審查→content_risk、401／402→account），離線帳號錯誤拋
   `JudgeAccountError` 讓 run_ragas 整批中止（rc=2、不寫結果檔）。
4. PR-M：judge 只剩 DeepSeek。model 不在白名單（含 claude-*）時不送出——生產記 degraded(account)、離線拋
   `JudgeAccountError`（整批中止）。
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import eval.run_ragas as rr  # noqa: E402
from app.services import faithfulness as F  # noqa: E402
from app.services import judge_schema as js  # noqa: E402
from app.services import llm_http as lh  # noqa: E402
from eval import judge as EJ  # noqa: E402
from eval import ragas_metrics as RM  # noqa: E402

FAKE_KEY = "fixed-test-secret-deepseek0"
ENV = {"DEEPSEEK_API_KEY": FAKE_KEY, "DEEPSEEK_BASE_URL": "https://api.example.test"}
MODEL = "deepseek-flash"


def completion(content, finish="stop", fp="fp_judge"):
    return httpx.Response(200, json={
        "model": "deepseek-v4.1-flash", "system_fingerprint": fp,
        "choices": [{"index": 0, "message": {"content": content if isinstance(content, str)
                                             else json.dumps(content, ensure_ascii=False)},
                     "finish_reason": finish}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    })


class _Http(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._env = mock.patch.dict(os.environ, ENV)
        self._env.start()

    def tearDown(self):
        self._env.stop()
        lh._transport = None
        lh._reset_clients()

    def install(self, route):
        """route(system, n_th_request_for_that_system, body) → Response。"""
        self.requests: list[dict] = []
        counts: dict[str, int] = {}

        def handler(request: httpx.Request):
            body = json.loads(request.content)
            self.requests.append(body)
            system = body["messages"][0]["content"]
            counts[system] = counts.get(system, 0) + 1
            return route(system, counts[system], body)

        lh._transport = httpx.MockTransport(handler)
        lh._reset_clients()

    def per_system(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for b in self.requests:
            s = b["messages"][0]["content"]
            out[s] = out.get(s, 0) + 1
        return out


# ── 1. 單層重試預算 ───────────────────────────────────────────────────────────
class StageBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def test_outside_call_validated_is_per_call(self):
        self.assertEqual(js.http_attempts_allowed(2), 2)
        js.record_http_requests(5)  # 不在階段內：不記帳、不拋
        self.assertEqual(js.http_attempts_allowed(2), 2)

    async def test_schema_retry_only_gets_the_remaining_budget(self):
        seen = []

        async def judge(system, user):
            allowed = js.http_attempts_allowed(2)
            seen.append(allowed)
            js.record_http_requests(allowed)  # 每次都用滿
            return {"statements": "不是陣列"}

        with self.assertRaises(js.JudgeSchemaError):
            await js.call_validated(judge, "s", "u", js.parse_statements)
        self.assertEqual(seen, [2, 1])
        self.assertEqual(sum(seen), js.HTTP_STAGE_MAX_REQUESTS)

    async def test_no_schema_retry_when_budget_is_spent(self):
        calls = []

        async def judge(system, user):
            calls.append(1)
            js.record_http_requests(js.HTTP_STAGE_MAX_REQUESTS)
            return {"statements": 1}

        with self.assertRaises(js.JudgeSchemaError):
            await js.call_validated(judge, "s", "u", js.parse_statements)
        self.assertEqual(len(calls), 1)

    async def test_unaccounted_judge_keeps_its_schema_retry(self):
        """不記帳的 judge（注入的假 judge；PR-M 前還有 CLI judge）照舊重試 1 次。"""
        calls = []

        async def judge(system, user):
            calls.append(1)
            return {"statements": "x"}

        with self.assertRaises(js.JudgeSchemaError):
            await js.call_validated(judge, "s", "u", js.parse_statements)
        self.assertEqual(len(calls), 2)

    async def test_budget_is_scoped_to_one_stage(self):
        async def judge(system, user):
            js.record_http_requests(2)
            return {"statements": ["a"]}

        await js.call_validated(judge, "s", "u", js.parse_statements)
        await js.call_validated(judge, "s", "u", js.parse_statements)
        self.assertEqual(js.http_attempts_allowed(2), 2, "離開 call_validated 後預算要還原")


# ── 2. 生產忠實度（check_faithfulness → _http_judge）──────────────────────────
class ProductionJudgeTests(_Http):
    async def check(self, answer="營收年增 30%"):
        return await F.check_faithfulness(answer, ["參考：營收年增 30%"], model=MODEL, timeout=5.0)

    async def test_happy_path_records_scale_identity(self):
        def route(system, n, body):
            if system == F.DECOMPOSE_SYS:
                return completion({"statements": ["營收年增 30%"]})
            return completion({"verdicts": [{"idx": 0, "supported": True}]})

        self.install(route)
        r = await self.check()
        self.assertEqual(r.faithfulness_score, 1.0)
        ev = r.to_evaluation()
        self.assertEqual(ev["judge_model"], MODEL)
        self.assertEqual(ev["judge_model_resp"], "deepseek-v4.1-flash")
        self.assertEqual(ev["judge_fingerprint"], "fp_judge")
        self.assertEqual(ev["judge_requests"], 2)
        self.assertEqual(ev["usage"], {"prompt_tokens": 20, "completion_tokens": 10})
        by_sys = {b["messages"][0]["content"]: b for b in self.requests}
        self.assertEqual(by_sys[F.DECOMPOSE_SYS]["max_tokens"], 8192)
        self.assertEqual(by_sys[F.GROUND_SYS]["max_tokens"], 2048)
        for b in self.requests:
            self.assertEqual(b["user_id"], "web-faithfulness")
            self.assertEqual(b["response_format"], {"type": "json_object"})
            self.assertEqual(b["thinking"], {"type": "disabled"})
            self.assertEqual(b["temperature"], 0)
            self.assertIs(b["stream"], False)

    async def test_worst_case_is_three_requests_per_stage(self):
        """最壞路徑：截斷→重試後回不合 schema→schema 重試只剩 1 個請求、仍不合→degraded(schema)。"""
        def route(system, n, body):
            if n == 1:
                return completion('{"statements": ["截', "length")
            return completion({"statements": "一整段字串"})

        self.install(route)
        r = await self.check()
        self.assertTrue(r.degraded)
        self.assertEqual(r.degraded_reason, F.DEGRADED_SCHEMA)
        self.assertEqual(self.per_system(), {F.DECOMPOSE_SYS: js.HTTP_STAGE_MAX_REQUESTS})
        self.assertEqual(r.judge_requests, 3)

    async def test_grounding_stage_also_capped(self):
        def route(system, n, body):
            if system == F.DECOMPOSE_SYS:
                return completion({"statements": ["營收年增 30%"]})
            if n == 1:
                return completion({"verdicts": [{"idx": 7, "supported": True}]})  # 越界＝schema 錯
            return completion("", "stop")  # 空回應：adapter 想重試，但預算只剩 2

        self.install(route)
        r = await self.check()
        self.assertTrue(r.degraded)
        self.assertEqual(self.per_system()[F.GROUND_SYS], 3)
        self.assertEqual(r.degraded_reason, F.DEGRADED_EMPTY)

    async def test_truncated_twice_is_truncated_without_schema_retry(self):
        self.install(lambda system, n, body: completion("{", "length"))
        r = await self.check()
        self.assertEqual(r.degraded_reason, F.DEGRADED_TRUNCATED)
        self.assertEqual(len(self.requests), 2)
        self.assertEqual([b["max_tokens"] for b in self.requests], [8192, 16384])

    async def test_content_risk_is_not_retried(self):
        self.install(lambda s, n, b: httpx.Response(400, json={"error": {"message": "Content Exists Risk"}}))
        r = await self.check()
        self.assertEqual((r.degraded_reason, len(self.requests)), (F.DEGRADED_CONTENT_RISK, 1))

    async def test_finish_content_filter_is_content_risk(self):
        self.install(lambda s, n, b: completion("", "content_filter"))
        r = await self.check()
        self.assertEqual(r.degraded_reason, F.DEGRADED_CONTENT_RISK)

    async def test_account_errors_are_degraded_account(self):
        for status in (401, 402, 404):
            with self.subTest(status=status):
                self.install(lambda s, n, b, st=status: httpx.Response(st, json={"error": {"message": "x"}}))
                r = await self.check()
                self.assertEqual((r.degraded_reason, len(self.requests)), (F.DEGRADED_ACCOUNT, 1))

    async def test_invalid_json_is_parse(self):
        self.install(lambda s, n, b: completion("我無法判斷"))
        r = await self.check()
        self.assertEqual((r.degraded_reason, len(self.requests)), (F.DEGRADED_PARSE, 1))

    async def test_missing_key_is_account_and_sends_nothing(self):
        self.install(lambda s, n, b: completion({"statements": []}))
        with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": ""}):
            r = await self.check()
        self.assertEqual(r.degraded_reason, F.DEGRADED_ACCOUNT)
        self.assertEqual(self.requests, [])

    async def test_unexpected_exception_is_degraded_error_not_raised(self):
        """審查低1：HTTP 分支原本在 try 之外。complete_json 漏出任何例外時要落 degraded(error)——
        拋到 `_faithfulness_spot_check` 只會記日誌、不寫 evaluation，那一題就從監控卡靜默消失。"""
        async def boom(*_a, **_kw):
            raise RuntimeError("意料之外")

        with mock.patch.object(lh, "complete_json", boom), self.assertLogs("app.services.faithfulness", "ERROR"):
            r = await self.check()
        self.assertTrue(r.degraded)
        self.assertEqual(r.degraded_reason, F.DEGRADED_ERROR)
        self.assertIsNone(r.faithfulness_score)

    async def test_decoding_error_end_to_end_is_degraded(self):
        """真實例外走一遍：Content-Encoding 解不開 → adapter 歸 other → degraded，不拋。"""
        self.install(lambda s, n, b: httpx.Response(
            200, headers={"Content-Encoding": "gzip"}, stream=httpx.ByteStream(b"not gzip")))
        r = await self.check()
        self.assertTrue(r.degraded)
        self.assertEqual((r.degraded_reason, len(self.requests)), (F.DEGRADED_UNAVAILABLE, 1))

    async def test_every_http_kind_maps_into_the_closed_vocabulary(self):
        kinds = [v for k, v in vars(lh).items() if k.isupper() and isinstance(v, str) and k in (
            "AUTH", "QUOTA", "CONFIG", "CONTENT_FILTER", "BAD_REQUEST", "OVERLOADED", "NETWORK", "TIMEOUT",
            "TRUNCATED", "TIMEOUT_STREAMED", "EMPTY", "INVALID_JSON", "OTHER")]
        for kind in kinds:
            self.assertIn(F._HTTP_DEGRADED.get(kind, F.DEGRADED_UNAVAILABLE), F.DEGRADED_REASONS, kind)

    async def test_non_whitelisted_model_is_account_and_sends_nothing(self):
        """PR-M：judge model 不在白名單（含 claude-*）→ 不送出、degraded(account)；不再走 CLI 串流。"""
        self.install(lambda s, n, b: completion({"statements": []}))
        for model in ("claude-haiku-4-5", "haiku", ""):
            with self.subTest(model=model):
                with self.assertLogs("app.services.faithfulness", "ERROR"):
                    r = await F.check_faithfulness("營收 100 億", ["ctx"], model=model, timeout=1.0)
                self.assertTrue(r.degraded)
                self.assertEqual(r.degraded_reason, F.DEGRADED_ACCOUNT)
                self.assertEqual(self.requests, [])
        self.assertFalse(hasattr(F, "stream_completion"), "生產 judge 不得再 import 串流呼叫層")


# ── 3. 離線評測（judge_json / run_ragas）──────────────────────────────────────
class EvalJudgeTests(_Http):
    async def test_judge_json_does_not_retry_on_top_of_the_adapter(self):
        self.install(lambda s, n, b: httpx.Response(503))
        from app.services.llm import LLMUnavailableError

        with self.assertRaises(LLMUnavailableError) as cm:
            await EJ.judge_json("x", system="s", model=MODEL, timeout=5)
        self.assertEqual(cm.exception.kind, lh.OVERLOADED)
        self.assertEqual(len(self.requests), lh.JSON_MAX_ATTEMPTS, "judge_json 的重試不得疊在 adapter 上")
        import inspect

        self.assertNotIn("retries", inspect.signature(EJ.judge_json).parameters)

    async def test_non_whitelisted_model_raises_account_error_and_sends_nothing(self):
        """PR-M：離線 judge 設成 claude-* 之類 → `JudgeAccountError`（run_ragas 整批 rc=2），不送出。"""
        self.install(lambda s, n, b: completion({"a": 1}))
        for model in ("claude-haiku-4-5", "sonnet", ""):
            with self.subTest(model=model):
                with self.assertRaises(EJ.JudgeAccountError) as cm:
                    await EJ.judge_json("x", system="s", model=model, timeout=5)
                self.assertIn("白名單", str(cm.exception))
        self.assertEqual(self.requests, [])

    async def test_account_error_raises_judge_account_error(self):
        self.install(lambda s, n, b: httpx.Response(402, json={"error": {"message": "Insufficient Balance"}}))
        with self.assertRaises(EJ.JudgeAccountError):
            await EJ.judge_json("x", system="s", model=MODEL, timeout=5)
        self.assertEqual(len(self.requests), 1)

    async def test_invalid_json_is_judge_error(self):
        self.install(lambda s, n, b: completion("not json"))
        with self.assertRaises(EJ.JudgeError):
            await EJ.judge_json("x", system="s", model=MODEL, timeout=5)

    async def test_meta_and_body(self):
        self.install(lambda s, n, b: completion({"questions": ["q"]}))
        meta: dict = {}
        out = await EJ.judge_json("答", system=RM.GENQ_SYS, model=MODEL, timeout=5, max_tokens=1024, meta=meta)
        self.assertEqual(out, {"questions": ["q"]})
        self.assertEqual(meta["system_fingerprint"], "fp_judge")
        self.assertEqual(meta["model_resp"], "deepseek-v4.1-flash")
        self.assertEqual(meta["requests"], 1)
        self.assertEqual(self.requests[0]["max_tokens"], 1024)
        self.assertEqual(self.requests[0]["user_id"], "eval-judge")

    def test_stage_max_tokens_table(self):
        self.assertEqual(RM.JUDGE_MAX_TOKENS_BY_SYSTEM, {
            RM.DECOMPOSE_SYS: 8192, RM.GROUND_SYS: 2048, RM.CTX_RELEVANCE_SYS: 2048, RM.GENQ_SYS: 1024,
        })
        self.assertEqual(set(RM.JUDGE_MAX_TOKENS_BY_SYSTEM), set(rr._JUDGE_TASKS))

    def test_every_judge_prompt_asks_for_json(self):
        """JSON 模式要求 prompt 含 json 字樣（9/24 探測：大寫 JSON 也算）；四支都有，所以不改提示、
        judge_prompt_sha 不變。"""
        for system in RM.JUDGE_MAX_TOKENS_BY_SYSTEM:
            self.assertIn("json", system.lower())

    async def test_context_precision_worst_case_three_requests(self):
        """離線同樣：一個指標階段最多 3 個請求（CP：截斷→不合 schema→schema 重試 1 個）。"""
        def route(system, n, body):
            if n == 1:
                return completion("{", "length")
            return completion({"verdicts": [{"idx": 0, "relevant": True}]})  # CP 要 1 起編號：0 越界

        self.install(route)

        async def judge(system, user):
            return await EJ.judge_json(user, system=system, model=MODEL, timeout=5,
                                       max_tokens=RM.JUDGE_MAX_TOKENS_BY_SYSTEM[system])

        with self.assertRaises(js.JudgeSchemaError):
            await RM.context_precision("q", "a", ["[1] 報告：x"], judge=judge)
        self.assertEqual(len(self.requests), js.HTTP_STAGE_MAX_REQUESTS)
        self.assertEqual([b["max_tokens"] for b in self.requests], [2048, 4096, 2048])


class RunRagasAccountAbortTests(unittest.IsolatedAsyncioTestCase):
    async def test_eval_question_reraises_account_errors(self):
        async def fake_retrieve(question, *, filters=None, **params):
            return [], "[1] 報告：a\n內容"

        async def fake_generate(question, context, *, model):
            return "答 [1]", False

        async def judge(system, user):
            raise EJ.JudgeAccountError("API[quota] 帳戶餘額不足")

        saved = {n: getattr(rr, n) for n in ("retrieve_context", "_generate_answer")}
        try:
            rr.retrieve_context = fake_retrieve
            rr._generate_answer = fake_generate
            with self.assertRaises(EJ.JudgeAccountError):
                await rr.eval_question({"id": "q", "question": "Q"}, judge=judge, embed=lambda t: [1.0],
                                       retrieval_params={})
        finally:
            for n, v in saved.items():
                setattr(rr, n, v)

    async def _eval_with_generation_error(self, kind: str):
        from app.services.llm import LLMUnavailableError

        async def fake_retrieve(question, *, filters=None, **params):
            return [], "[1] 報告：a\n內容"

        async def fake_generate(question, context, *, model):
            raise LLMUnavailableError(f"API[{kind}] x", kind=kind)

        async def judge(system, user):
            raise AssertionError("生成失敗後不該再 judge")

        saved = {n: getattr(rr, n) for n in ("retrieve_context", "_generate_answer")}
        try:
            rr.retrieve_context = fake_retrieve
            rr._generate_answer = fake_generate
            return await rr.eval_question({"id": "q", "question": "Q"}, judge=judge, embed=lambda t: [1.0],
                                          retrieval_params={})
        finally:
            for n, v in saved.items():
                setattr(rr, n, v)

    async def test_generator_account_errors_abort_the_batch(self):
        """審查低2：402 多半先在生成端出現。記成單題 error 的話結果檔照寫、比較顯示劣化。"""
        for kind in sorted(lh.ACCOUNT_KINDS):
            with self.subTest(kind=kind), self.assertRaises(rr.GeneratorAccountError) as cm:
                await self._eval_with_generation_error(kind)
            self.assertIn(f"API[{kind}]", str(cm.exception))

    async def test_other_generator_errors_stay_single_case_errors(self):
        for kind in ("overloaded", "network", "timeout", "content_filter", "bad_request", "other"):
            with self.subTest(kind=kind):
                out = await self._eval_with_generation_error(kind)
                self.assertIn("LLMUnavailableError", out["error"])

    async def test_run_writes_no_results_on_generator_quota(self):
        from app.services.llm import LLMUnavailableError

        async def fake_retrieve(question, *, filters=None, **params):
            return [], "[1] 報告：a\n內容"

        async def fake_generate(question, context, *, model):
            raise LLMUnavailableError("API[quota] 帳戶餘額不足", kind=lh.QUOTA)

        with tempfile.TemporaryDirectory() as td:
            ds = Path(td) / "ds.json"
            ds.write_text(json.dumps({"questions": [{"id": "q1", "question": "Q"}, {"id": "q2", "question": "R"}]}),
                          encoding="utf-8")
            out = Path(td) / "out.json"
            saved = {n: getattr(rr, n) for n in ("retrieve_context", "_generate_answer")}
            try:
                rr.retrieve_context = fake_retrieve
                rr._generate_answer = fake_generate
                with self.assertRaises(rr.GeneratorAccountError):
                    await rr.run(ds, out_path=out, concurrency=1, judge_model=MODEL)
            finally:
                for n, v in saved.items():
                    setattr(rr, n, v)
            self.assertFalse(out.exists())

    def test_main_exits_2_on_generator_account_error(self):
        async def fake_run(dataset, **kwargs):
            raise rr.GeneratorAccountError("生成端（deepseek-flash）API[quota] 帳戶餘額不足")

        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "out.json"
            saved, old = rr.run, sys.argv
            try:
                rr.run = fake_run
                sys.argv = ["run_ragas.py", "--out", str(out)]
                err = io.StringIO()
                with contextlib.redirect_stderr(err), mock.patch.object(rr, "require_llm_key"), \
                        self.assertRaises(SystemExit) as cm:
                    rr._main()
            finally:
                rr.run, sys.argv = saved, old
            self.assertEqual(cm.exception.code, 2)
            self.assertFalse(out.exists())
            self.assertIn("生成端帳號層級錯誤", err.getvalue())

    def test_main_exits_2_without_writing_results(self):
        async def fake_run(dataset, **kwargs):
            raise EJ.JudgeAccountError("API[auth] 金鑰無效或缺漏")

        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "out.json"
            saved, old = rr.run, sys.argv
            try:
                rr.run = fake_run
                sys.argv = ["run_ragas.py", "--out", str(out)]
                err = io.StringIO()
                with contextlib.redirect_stderr(err), mock.patch.object(rr, "require_llm_key"), \
                        self.assertRaises(SystemExit) as cm:
                    rr._main()
            finally:
                rr.run, sys.argv = saved, old
            self.assertEqual(cm.exception.code, 2)
            self.assertFalse(out.exists())
            self.assertIn("整批中止", err.getvalue())

    async def test_run_passes_stage_limits_and_records_observed_scale(self):
        """run() 組出的 judge 依系統提示傳 max_tokens，並把 API 回報的 model／fingerprint 收進 config。"""
        seen: list[tuple[str, int]] = []
        with tempfile.TemporaryDirectory() as td:
            ds = Path(td) / "ds.json"
            ds.write_text(json.dumps({"questions": [{"id": "q1", "question": "Q"}]}), encoding="utf-8")

            async def fake_eval_question(q, *, judge, embed, retrieval_params, agentic=False, gen_model=None):
                for system in (RM.DECOMPOSE_SYS, RM.GROUND_SYS, RM.CTX_RELEVANCE_SYS, RM.GENQ_SYS):
                    await judge(system, "u")
                return {"id": q["id"], "question": q["question"], "faithfulness": 1.0, "context_precision": 1.0,
                        "answer_relevancy": 1.0, "n_contexts": 1, "latency_ms": 1, "cited": True,
                        "simplified": False, "gen_truncated": False}

            async def fake_judge_json(user, *, system, model, max_tokens, meta):
                seen.append((system, max_tokens))
                meta.update({"model_resp": "deepseek-v4.1-flash", "system_fingerprint": "fp1", "requests": 1,
                             "usage": {"prompt_tokens": 7}})
                return {}

            saved = {n: getattr(rr, n) for n in ("eval_question", "judge_json")}
            try:
                rr.eval_question = fake_eval_question
                rr.judge_json = fake_judge_json
                report = await rr.run(ds, out_path=None, concurrency=1, judge_model=MODEL)
            finally:
                for n, v in saved.items():
                    setattr(rr, n, v)
        self.assertEqual([t for _s, t in seen], [8192, 2048, 2048, 1024])
        judge_cfg = report["config"]["judge"]
        self.assertEqual(judge_cfg["max_tokens"], {
            "decompose": 8192, "ground": 2048, "context_precision": 2048, "answer_relevancy": 1024,
        })
        self.assertEqual(judge_cfg["observed"], {
            "model_resp": ["deepseek-v4.1-flash"], "system_fingerprint": ["fp1"], "requests": 4,
            "usage": {"prompt_tokens": 28},
        })
        self.assertNotIn("observed", report["summary"], "config 只供印差異，不能進 summary（會回 3）")

if __name__ == "__main__":
    unittest.main()
