"""`LLM_PROVIDER=opencode_go`：DeepSeek 402 時的手動備援（同一顆模型改經 OpenCode Go 送出）。

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
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("REPORT_MARK_SESSION_SECRET", "fixed-test-secret-0123456789")

from fastapi.testclient import TestClient  # noqa: E402

from app.services import llm_health, llm_http  # noqa: E402
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
    return {"model": "deepseek-v4.1-flash", "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
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

    def tearDown(self):
        llm_http._quota_seen_at, llm_http._ok_seen_at = self._quota, self._ok
        llm_http._transport = None
        llm_http._reset_clients()

    def chat(self, **kw):
        return llm_http.complete_chat(
            "deepseek-flash", "提示", max_tokens=16, timeout=10, task="summary", user_id="batch-summary",
            sleep=lambda s: None, **kw,
        )


class ModelsTests(unittest.TestCase):
    def test_provider_accepts_opencode_go(self):
        self.assertEqual(lm.provider({"LLM_PROVIDER": " OpenCode_Go "}), "opencode_go")
        self.assertEqual(lm.key_env({"LLM_PROVIDER": "opencode_go"}), "OPENCODE_GO_API_KEY")
        self.assertEqual(lm.key_env({}), "DEEPSEEK_API_KEY")

    def test_every_whitelisted_name_has_a_go_id(self):
        # 少一個對應，切到 Go 時那個名稱就會原樣送出、被 Go 以模型不存在拒絕
        self.assertEqual(set(lm.GO_MODEL_IDS), set(lm.HTTP_MODELS))

    def test_wire_model_only_translates_for_go(self):
        self.assertEqual(lm.wire_model("deepseek-flash", "opencode_go"), "deepseek-v4.1-flash")
        self.assertEqual(lm.wire_model("deepseek-flash", "deepseek"), "deepseek-flash")

    def test_internal_names_unchanged(self):
        # 換的是路徑、不是模型：預設表、judge 系譜照舊是 DeepSeek 名稱
        env = {**GO, **{k: "" for k in lm.TASK_ENV.values()}}
        self.assertEqual(lm.resolve_model(lm.TASK_TAG, env=env), "deepseek-flash")
        self.assertEqual(lm.judge_lineage("deepseek-flash"), lm.JUDGE_LINEAGE_DEEPSEEK)

    def test_diagnose_names_the_go_key(self):
        out = lm.diagnose({"ask_answer": "deepseek-flash"}, has_key=False, prov="opencode_go")
        self.assertIn("OPENCODE_GO_API_KEY 為空", out[0][1])


class RequestTests(_Transport):
    def test_batch_request_goes_to_go_with_go_model_id(self):
        res = self.chat()
        self.assertEqual(res.text, "好")
        (req,) = self.requests
        self.assertEqual(str(req.url), GO_BASE + "/chat/completions")
        self.assertEqual(req.headers["authorization"], f"Bearer {GO_KEY}")
        self.assertEqual(req.headers["user-agent"], llm_http.GO_USER_AGENT)
        self.assertEqual(req.headers["x-opencode-session"], "batch-summary")
        body = json.loads(req.content)
        self.assertEqual(body["model"], "deepseek-v4.1-flash")
        self.assertNotIn("user_id", body)
        # thinking 照樣關（同一顆模型，預設是開的）
        self.assertEqual((body["thinking"], body["reasoning_effort"]), ({"type": "disabled"}, "none"))

    def test_deepseek_mode_unchanged(self):
        with mock.patch.dict(os.environ, {"LLM_PROVIDER": "deepseek", "DEEPSEEK_API_KEY": "fixed-test-secret-ds0",
                                          "DEEPSEEK_BASE_URL": "https://ds.example.test"}):
            self.chat()
        req = self.requests[0]
        self.assertEqual(str(req.url), "https://ds.example.test/chat/completions")
        self.assertNotIn("x-opencode-session", req.headers)
        body = json.loads(req.content)
        self.assertEqual((body["model"], body["user_id"]), ("deepseek-flash", "batch-summary"))

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

    def test_deepseek_429_never_becomes_quota(self):
        kind, _ = llm_http.classify_status(429, b'{"error": {"message": "usage limit reached"}}', "deepseek")
        self.assertEqual(kind, llm_http.OVERLOADED)

    def test_go_key_is_redacted(self):
        fake = "oc_" + "x" * 24  # 執行期組字串（gitleaks 規則見 AGENTS.md）
        self.assertNotIn(fake, llm_http.error_string(llm_http.AUTH, f"HTTP 401 bad key {fake}"))

    def test_go_unavailable_model_is_config(self):
        raw = b'{"error":{"type":"server_error","message":"Upstream request failed: Model is unavailable."}}'
        self.assertEqual(llm_http.classify_status(400, raw)[0], llm_http.CONFIG)

    def test_go_usage_is_normalized_to_cache_keys(self):
        # 10/28 探測的實際格式：OpenAI 式 prompt_tokens＋cached_tokens，沒有 DeepSeek 的 hit／miss
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
        ds = {"prompt_cache_hit_tokens": 1, "prompt_cache_miss_tokens": 2, "prompt_tokens": 3}
        self.assertIs(llm_http._normalize_usage(ds), ds)

    def test_metadata_and_usage_rows_carry_provider(self):
        seen = []
        llm_http.add_observer(seen.append)
        try:
            self.chat()
        finally:
            llm_http.remove_observer(seen.append)
        self.assertEqual(seen[0]["provider"], "opencode_go")
        self.assertEqual(seen[0]["model"], "deepseek-flash")

    def test_account_hints_follow_provider(self):
        self.assertIn("額度", _claude_cli._ACCOUNT_HINTS["opencode_go"][llm_http.QUOTA])
        self.assertIn("切換到 OpenCode Go", _claude_cli._ACCOUNT_HINTS["deepseek"][llm_http.QUOTA])
        for prov in lm.PROVIDERS:
            self.assertEqual(set(_claude_cli._ACCOUNT_HINTS[prov]), set(llm_http.ACCOUNT_KINDS))


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
                _llm_env.require_llm_key({"tag": "deepseek-flash"})
        self.assertEqual(cm.exception.code, _llm_env.RC_CONFIG)

    def test_main_compares_go_key_and_provider(self):
        with tempfile.TemporaryDirectory() as d:
            a, b = Path(d, "a.env"), Path(d, "b.env")
            common = "DEEPSEEK_API_KEY=fixed-test-secret-ds0\nOPENCODE_GO_API_KEY=fixed-test-secret-go0\n"
            a.write_text(common + "LLM_PROVIDER=opencode_go\n", encoding="utf-8")
            b.write_text(common + "LLM_PROVIDER=opencode_go\n", encoding="utf-8")
            with redirect_stdout(io.StringIO()):
                self.assertEqual(_llm_env.main([str(a), str(b)]), 0)
            b.write_text(common, encoding="utf-8")  # 批次那份沒切：web 走 Go、批次走 DeepSeek
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(_llm_env.main([str(a), str(b)]), 1)
            self.assertIn("LLM_PROVIDER=deepseek（未設）", out.getvalue())
            b.write_text("DEEPSEEK_API_KEY=fixed-test-secret-ds0\nLLM_PROVIDER=opencode_go\n", encoding="utf-8")
            with redirect_stdout(io.StringIO()):
                self.assertEqual(_llm_env.main([str(a), str(b)]), 1)  # Go 金鑰只有一份


if __name__ == "__main__":
    unittest.main()
