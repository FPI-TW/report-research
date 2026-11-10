"""`LLM_PROVIDER=opencode_go`：同一顆模型改經 OpenCode Go 送出（送達路徑不同，模型與量尺相同）。

全程不連網：`llm_http._transport` 換成 `httpx.MockTransport`；金鑰只用 gitleaks allowlist 內的假值，
而且以 `mock.patch.dict` 限定在單一測試內（conftest 的兩道防線不放寬）。
"""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("REPORT_MARK_SESSION_SECRET", "fixed-test-secret-0123456789")

from fastapi.testclient import TestClient  # noqa: E402

from app.services import llm, llm_health, llm_http  # noqa: E402
from app.services import llm_models as lm  # noqa: E402
from scripts import _claude_cli, _llm_env  # noqa: E402
from web.server import app  # noqa: E402

GO_KEY = "fixed-test-secret-opencodego0"
GO_BASE = "https://go.example.test/v1"
GO = {"LLM_PROVIDER": "opencode_go", "OPENCODE_GO_API_KEY": GO_KEY, "OPENCODE_GO_BASE_URL": GO_BASE}


def _sse(*events) -> bytes:
    lines = []
    for ev in events:
        lines += ["data: " + json.dumps(ev, ensure_ascii=False), ""]
    lines += ["data: [DONE]", ""]
    return ("\n".join(lines) + "\n").encode("utf-8")


def _chunk(content=None, finish=None, usage=None):
    delta = {} if content is None else {"content": content}
    return {"model": "gpt-6-luna", "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
            "usage": usage}


class _Transport(unittest.TestCase):
    def setUp(self):
        self.requests: list[httpx.Request] = []
        self.handler = lambda req: httpx.Response(200, content=_sse(_chunk("好"), _chunk("", "stop", {})))

        def recording(request):
            self.requests.append(request)
            return self.handler(request)

        llm_http._transport = httpx.MockTransport(recording)
        llm_http._reset_clients()
        self._quota, self._ok = llm_http._quota_seen_at, llm_http._ok_seen_at
        env = mock.patch.dict(os.environ, GO)
        env.start()
        self.addCleanup(env.stop)

    def _quota_before_test(self):
        return self._quota

    def tearDown(self):
        llm_http._quota_seen_at, llm_http._ok_seen_at = self._quota, self._ok
        llm_http._transport = None
        llm_http._reset_clients()

    def chat(self, **kw):
        return llm_http.complete_chat(
            "gpt-6-luna", "提示", max_tokens=16, timeout=10, task="summary", user_id="batch-summary",
            sleep=lambda s: None, **kw,
        )


class ModelsTests(unittest.TestCase):
    def test_provider_accepts_opencode_go(self):
        self.assertEqual(lm.provider({"LLM_PROVIDER": " OpenCode_Go "}), "opencode_go")
        self.assertEqual(lm.key_env({"LLM_PROVIDER": "opencode_go"}), "OPENCODE_GO_API_KEY")
        self.assertEqual(lm.key_env({}), "OPENAI_API_KEY")

    def test_internal_names_unchanged(self):
        # 換的是路徑、不是模型：預設表、judge 系譜兩條路徑相同
        env = {**GO, **{k: "" for k in lm.TASK_ENV.values()}}
        self.assertEqual(lm.resolve_model(lm.TASK_TAG, env=env), "gpt-6-luna")
        self.assertEqual(lm.judge_lineage("gpt-6-luna"), lm.JUDGE_LINEAGE_LUNA)

    def test_diagnose_names_the_go_key(self):
        out = lm.diagnose({"ask_answer": "gpt-6-luna"}, has_key=False, prov="opencode_go")
        self.assertIn("OPENCODE_GO_API_KEY 為空", out[0][1])


class RequestTests(_Transport):
    def test_batch_request_goes_to_go_with_the_same_model_name(self):
        res = self.chat()
        self.assertEqual(res.text, "好")
        (req,) = self.requests
        self.assertEqual(str(req.url), GO_BASE + "/chat/completions")
        self.assertEqual(req.headers["authorization"], f"Bearer {GO_KEY}")
        self.assertEqual(req.headers["user-agent"], llm_http.go_user_agent())
        self.assertEqual(req.headers["x-opencode-session"], "batch-summary")
        body = json.loads(req.content)
        self.assertEqual(body["model"], "gpt-6-luna")  # 名稱不翻譯：Go 與官方同一個模型 id
        self.assertNotIn("user_id", body)
        self.assertNotIn("thinking", body)  # Go 對這顆模型的 thinking 回 400
        self.assertEqual(body["reasoning_effort"], "none")

    def test_user_agent_carries_the_identity_url_only_when_set(self):
        """沒設定網址（profile 沒給 `[institution].website`）時只送產品代號；預設 profile 的完整值由
        llm_wire_golden.json 釘住，設定網址的兩個入口由 tests/test_llm_identity.py 釘住。"""
        lm.set_identity_url("")
        self.chat()
        lm.set_identity_url("https://research.example.com")
        self.chat()
        self.assertEqual([r.headers["user-agent"] for r in self.requests],
                         ["report-mark/1", "report-mark/1 (+https://research.example.com)"])

    def test_openai_mode_has_no_go_headers(self):
        with mock.patch.dict(os.environ, {"LLM_PROVIDER": "openai", "OPENAI_API_KEY": "fixed-test-secret-openai0",
                                          "OPENAI_BASE_URL": "https://oa.example.test/v1"}):
            self.chat()
        req = self.requests[0]
        self.assertEqual(str(req.url), "https://oa.example.test/v1/chat/completions")
        self.assertEqual(req.headers["authorization"], "Bearer fixed-test-secret-openai0")
        self.assertNotIn("x-opencode-session", req.headers)
        self.assertNotEqual(req.headers.get("user-agent"), llm_http.go_user_agent())
        body = json.loads(req.content)
        self.assertEqual(body["model"], "gpt-6-luna")
        self.assertNotIn("user_id", body)

    def test_body_is_identical_on_both_paths(self):
        """兩條路徑差異只在 header 與端點（`llm_http` docstring）；body 一字不差才能互相代表。"""
        self.chat()
        with mock.patch.dict(os.environ, {"LLM_PROVIDER": "openai", "OPENAI_API_KEY": "fixed-test-secret-openai0",
                                          "OPENAI_BASE_URL": "https://oa.example.test/v1"}):
            self.chat()
        go, oa = self.requests
        self.assertEqual(json.loads(go.content), json.loads(oa.content))

    def test_session_header_is_sanitized(self):
        llm_http.complete_chat("gpt-6-luna", "提示", max_tokens=16, timeout=10, user_id="web ask/主答",
                               sleep=lambda s: None)
        self.assertRegex(self.requests[0].headers["x-opencode-session"], r"^[A-Za-z0-9_-]+$")

    def test_batch_task_label_becomes_the_session(self):
        """`run_claude` 的 task 標籤在 Go 路徑是 session；OpenAI 路徑不送。"""
        res = _claude_cli.run_claude(
            "提示", timeout=10, model="gpt-6-luna", max_tokens=16,
            meta={"task": "signal", "file_hash": "h1", "report_id": "r1"},
        )
        self.assertIsNone(res.error)
        self.assertEqual(self.requests[0].headers["x-opencode-session"], "batch-signal")

    def test_missing_go_key_names_the_go_variable(self):
        with mock.patch.dict(os.environ, {"OPENCODE_GO_API_KEY": ""}):
            res = self.chat()
        self.assertEqual(res.kind, llm_http.AUTH)
        self.assertIn("OPENCODE_GO_API_KEY", res.error)
        self.assertEqual(self.requests, [])

    def test_go_quota_429_is_quota_and_other_429_is_overloaded(self):
        quota = httpx.Response(429, json={"error": {"message": "Monthly usage limit reached for this model"}})
        self.handler = lambda req: quota
        self.assertEqual(self.chat().kind, llm_http.QUOTA)
        self.assertEqual(len(self.requests), 1)  # 帳號型：不重試
        self.assertGreater(llm_http.last_quota_at(), 0)
        self.handler = lambda req: httpx.Response(429, json={"error": {"message": "Too many requests"}})
        self.assertEqual(self.chat(retries=0).kind, llm_http.OVERLOADED)

    def test_upstream_rate_limit_is_not_quota(self):
        """Go 轉送上游的限流（「Rate limit reached」）重試就好，不得被 `limit reached` 比成額度用完。"""
        self.handler = lambda req: httpx.Response(429, json={"error": {"message": "Rate limit reached for gpt-6-luna"}})
        res = self.chat(retries=0)
        self.assertEqual(res.kind, llm_http.OVERLOADED)
        self.assertEqual(llm_http.last_quota_at(), self._quota_before_test())

    def test_insufficient_quota_code_is_quota_on_go(self):
        self.handler = lambda req: httpx.Response(429, json={"error": {"message": "x", "code": "insufficient_quota"}})
        res = self.chat()
        self.assertEqual(res.kind, llm_http.QUOTA)
        self.assertEqual(len(self.requests), 1)

    def test_openai_429_message_alone_never_becomes_quota(self):
        kind, _ = llm_http.classify_status(429, b'{"error": {"message": "usage limit reached"}}', "openai")
        self.assertEqual(kind, llm_http.OVERLOADED)

    def test_go_key_is_redacted(self):
        fake = "oc_" + "x" * 24  # 執行期組字串（gitleaks 規則見 AGENTS.md）
        self.assertNotIn(fake, llm_http.error_string(llm_http.AUTH, f"HTTP 401 bad key {fake}"))

    def test_go_unavailable_model_is_config(self):
        raw = b'{"error":{"type":"server_error","message":"Upstream request failed: Model is unavailable."}}'
        self.assertEqual(llm_http.classify_status(400, raw)[0], llm_http.CONFIG)

    def test_go_usage_is_normalized_to_cache_keys(self):
        # 10/28 探測的實際格式：OpenAI 式 prompt_tokens＋cached_tokens，用量紀錄沿用 hit／miss 兩個鍵
        usage = {"prompt_tokens": 10, "completion_tokens": 1, "prompt_tokens_details": {"cached_tokens": 4},
                 "completion_tokens_details": {"reasoning_tokens": 0}}
        self.handler = lambda req: httpx.Response(200, content=_sse(_chunk("好"), _chunk("", "stop", usage)))
        seen = []
        llm_http.add_observer(seen.append)
        try:
            self.chat()
        finally:
            llm_http.remove_observer(seen.append)
        self.assertEqual(seen[0]["tokens"], {"hit": 4, "miss": 6, "completion": 1, "reasoning": 0})
        already = {"prompt_cache_hit_tokens": 1, "prompt_cache_miss_tokens": 2, "prompt_tokens": 3}
        self.assertIs(llm_http._normalize_usage(already), already)

    def test_metadata_and_usage_rows_carry_provider(self):
        seen = []
        llm_http.add_observer(seen.append)
        try:
            self.chat()
        finally:
            llm_http.remove_observer(seen.append)
        self.assertEqual(seen[0]["provider"], "opencode_go")
        self.assertEqual(seen[0]["model"], "gpt-6-luna")

    def test_account_hints_follow_provider(self):
        self.assertIn("額度", _claude_cli._ACCOUNT_HINTS["opencode_go"][llm_http.QUOTA])
        self.assertIn("OpenAI 後台", _claude_cli._ACCOUNT_HINTS["openai"][llm_http.QUOTA])
        for prov in lm.PROVIDERS:
            self.assertEqual(set(_claude_cli._ACCOUNT_HINTS[prov]), set(llm_http.ACCOUNT_KINDS))


class OnlineSessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_online_task_label_becomes_the_session(self):
        requests = []

        def recording(req):
            requests.append(req)
            return httpx.Response(200, content=_sse(_chunk("好"), _chunk("", "stop", {})))

        llm_http._transport = httpx.MockTransport(recording)
        llm_http._reset_clients()
        self.addCleanup(llm_http._reset_clients)
        self.addCleanup(setattr, llm_http, "_transport", None)
        with mock.patch.dict(os.environ, GO):
            chunks = [c async for c in llm.stream_completion("提示", model="gpt-6-luna", task="ask_overview")]
            await llm_http.aclose()
        self.assertEqual(chunks, ["好"])
        self.assertEqual(requests[0].headers["x-opencode-session"], "web-ask_overview")

    async def test_judge_label_becomes_the_session(self):
        requests = []

        def recording(req):
            requests.append(req)
            return httpx.Response(200, json={"model": "gpt-6-luna", "choices": [
                {"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "{}"}}]})

        llm_http._transport = httpx.MockTransport(recording)
        llm_http._reset_clients()
        self.addCleanup(llm_http._reset_clients)
        self.addCleanup(setattr, llm_http, "_transport", None)
        with mock.patch.dict(os.environ, GO):
            res = await llm_http.complete_json(
                "gpt-6-luna", "只輸出 JSON", max_tokens=16, timeout=5, user_id="eval-judge",
            )
            await llm_http.aclose()
        self.assertIsNone(res.kind)
        self.assertEqual(requests[0].headers["x-opencode-session"], "eval-judge")


class HealthTests(_Transport):
    def setUp(self):
        super().setUp()
        llm_health.reset()
        llm_http._quota_seen_at = llm_http._ok_seen_at = 0.0
        self.addCleanup(llm_health.reset)

    def get(self):
        return TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 51000)).get("/healthz/llm")

    def test_models_200_is_ok_without_balance(self):
        self.handler = lambda req: httpx.Response(200, json={"object": "list", "data": []})
        r = self.get()
        self.assertEqual((r.status_code, r.json()), (200, {"llm": "ok"}))
        self.assertEqual(self.requests[0].url.path, "/v1/models")

    def test_models_401_is_auth_failed(self):
        self.handler = lambda req: httpx.Response(401, json={"error": {"message": "invalid key"}})
        self.assertEqual(self.get().json(), {"llm": "auth_failed"})

    def test_quota_latch_clears_only_after_a_successful_request(self):
        self.handler = lambda req: httpx.Response(200, json={"object": "list", "data": []})
        llm_http._quota_seen_at = time.monotonic()
        self.assertEqual(self.get().json(), {"llm": "exhausted"})
        llm_health._snap = llm_health._snap._replace(expires_at=0.0)
        self.assertEqual(self.get().json(), {"llm": "exhausted"})  # /models 200 證明不了額度回來了
        llm_http._ok_seen_at = time.monotonic()
        self.assertEqual(self.get().json(), {"llm": "ok"})


class BatchPreflightTests(unittest.TestCase):
    def test_require_llm_key_checks_the_go_key(self):
        with mock.patch.dict(os.environ, {**GO, "OPENCODE_GO_API_KEY": ""}), \
                mock.patch.object(_llm_env, "_fresh_breaker", return_value=None):
            _llm_env.load_llm_env()
            with self.assertRaises(SystemExit) as cm, redirect_stdout(io.StringIO()):
                _llm_env.require_llm_key({"tag": "gpt-6-luna"})
        self.assertEqual(cm.exception.code, _llm_env.RC_CONFIG)

    def test_deepseek_provider_is_rejected_rc2(self):
        """退役值：批次拒跑不花錢（web 才退回預設）；金鑰就算填了也一樣。"""
        for value in ("deepseek", " DeepSeek "):
            with self.subTest(value=value), mock.patch.dict(
                os.environ, {"LLM_PROVIDER": value, "OPENAI_API_KEY": "fixed-test-secret-openai0"},
            ), mock.patch.object(_llm_env, "_fresh_breaker", return_value=None):
                _llm_env.load_llm_env()
                err = io.StringIO()
                with self.assertRaises(SystemExit) as cm, redirect_stderr(err):
                    _llm_env.require_llm_key({"tag": "gpt-6-luna"})
                self.assertEqual(cm.exception.code, 2)
                self.assertIn("已退役", err.getvalue())

    def test_main_compares_only_the_current_providers_key(self):
        """正式環境沒有 Go 金鑰、測試環境沒有 OpenAI 金鑰都是正常的：另一把沒人填就不比。"""
        with tempfile.TemporaryDirectory() as d:
            a, b = Path(d, "a.env"), Path(d, "b.env")
            # 只有 OpenAI 金鑰、provider 未設（預設 openai）
            for path in (a, b):
                path.write_text("OPENAI_API_KEY=fixed-test-secret-openai0\n", encoding="utf-8")
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(_llm_env.main([str(a), str(b)]), 0)
            self.assertNotIn("OPENCODE_GO_API_KEY", out.getvalue())
            # 只有 Go 金鑰、provider=opencode_go
            for path in (a, b):
                path.write_text("LLM_PROVIDER=opencode_go\nOPENCODE_GO_API_KEY=fixed-test-secret-go0\n",
                                encoding="utf-8")
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(_llm_env.main([str(a), str(b)]), 0)
            self.assertNotIn("OPENAI_API_KEY", out.getvalue())
            # 目前 provider 的金鑰一份有、一份沒有：不一致
            a.write_text("OPENAI_API_KEY=fixed-test-secret-openai0\n", encoding="utf-8")
            b.write_text("OPENCODE_GO_API_KEY=fixed-test-secret-go0\n", encoding="utf-8")
            with redirect_stdout(io.StringIO()):
                self.assertEqual(_llm_env.main([str(a), str(b)]), 1)
            # 另一把有人填就要比：兩份不同算不一致
            a.write_text("OPENAI_API_KEY=fixed-test-secret-openai0\nOPENCODE_GO_API_KEY=fixed-test-secret-go0\n",
                         encoding="utf-8")
            b.write_text("OPENAI_API_KEY=fixed-test-secret-openai0\nOPENCODE_GO_API_KEY=fixed-test-secret-go1\n",
                         encoding="utf-8")
            with redirect_stdout(io.StringIO()):
                self.assertEqual(_llm_env.main([str(a), str(b)]), 1)

    def test_main_compares_go_key_and_provider(self):
        with tempfile.TemporaryDirectory() as d:
            a, b = Path(d, "a.env"), Path(d, "b.env")
            common = "OPENAI_API_KEY=fixed-test-secret-openai0\nOPENCODE_GO_API_KEY=fixed-test-secret-go0\n"
            a.write_text(common + "LLM_PROVIDER=opencode_go\n", encoding="utf-8")
            b.write_text(common + "LLM_PROVIDER=opencode_go\n", encoding="utf-8")
            with redirect_stdout(io.StringIO()):
                self.assertEqual(_llm_env.main([str(a), str(b)]), 0)
            b.write_text(common, encoding="utf-8")  # 批次那份沒切：web 走 Go、批次走 OpenAI
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(_llm_env.main([str(a), str(b)]), 1)
            self.assertIn("LLM_PROVIDER=openai（未設）", out.getvalue())
            b.write_text("OPENAI_API_KEY=fixed-test-secret-openai0\nLLM_PROVIDER=opencode_go\n", encoding="utf-8")
            with redirect_stdout(io.StringIO()):
                self.assertEqual(_llm_env.main([str(a), str(b)]), 1)  # Go 金鑰只有一份


if __name__ == "__main__":
    unittest.main()
