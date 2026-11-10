"""多家 LLM provider 的原生 adapter（app/services/llm_http.py、app/services/llm_models.py）。

逐點釘住每個 provider 的對應規則（llm_http 模組 docstring「各 provider 的對應規則」）：請求組裝（網址、認證 header、
body）、串流解析、錯誤分類、usage 正規化、JSON 模式、推理強度與 temperature、白名單（內建＋設定宣告）、端點設定錯誤，
以及批次預檢與 `/healthz/llm` 認得新 provider。另以 golden 釘住廷豐現行兩條路徑（openai、opencode_go）送出的請求與
加入其他 provider 之前逐字相同。

全程不連網：一律以 `httpx.MockTransport` 注入（`llm_http._transport`）；金鑰只用 `fixed-test-secret-` 開頭的假值，
而且以 `mock.patch.dict` 限定在單一測試內（conftest 把每個 provider 的金鑰強制成空字串、端點指到不可達的本機埠）。
"""
from __future__ import annotations

import asyncio
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

from app.services import llm, llm_health  # noqa: E402
from app.services import llm_http as lh  # noqa: E402
from app.services import llm_models as lm  # noqa: E402
from scripts import _claude_cli  # noqa: E402
from scripts import _llm_env as le  # noqa: E402

KEY = "fixed-test-secret-provider0"
PROMPT = "請摘要\x00這份研報\ud800。"
SYSTEM = "你是分析師，只輸出 JSON。"
CLEAN_PROMPT = "請摘要這份研報�。"

AZURE_ENV = {
    "LLM_PROVIDER": "azure_openai", "AZURE_OPENAI_API_KEY": KEY, "AZURE_OPENAI_ENDPOINT": "https://res.example.test/",
    "AZURE_OPENAI_API_VERSION": "2025-04-01-preview",
    "AZURE_OPENAI_DEPLOYMENTS": "gpt-6-luna=luna-prod, gpt-4.1 = gpt41-eu",
}
ANTHROPIC_ENV = {
    "LLM_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": KEY, "ANTHROPIC_BASE_URL": "https://anthropic.example.test",
    "ANTHROPIC_MODELS": "claude-sonnet-5-5,claude-haiku-4-5",
}
COMPAT_ENV = {
    "LLM_PROVIDER": "openai_compatible", "OPENAI_COMPATIBLE_API_KEY": KEY,
    "OPENAI_COMPATIBLE_BASE_URL": "http://vllm.example.test:8000/v1", "OPENAI_COMPATIBLE_MODELS": "Qwen/Qwen3-72B",
}
ENVS = {"azure_openai": AZURE_ENV, "anthropic": ANTHROPIC_ENV, "openai_compatible": COMPAT_ENV}
MODEL = {"azure_openai": "gpt-6-luna", "anthropic": "claude-sonnet-5-5", "openai_compatible": "Qwen/Qwen3-72B"}


# ── 回應建構 ─────────────────────────────────────────────────────────────────
def _sse(*events: dict | str, done: bool = True) -> bytes:
    lines: list[str] = []
    for ev in events:
        lines.append(ev if isinstance(ev, str) else "data: " + json.dumps(ev, ensure_ascii=False))
        lines.append("")
    if done:
        lines += ["data: [DONE]", ""]
    return ("\n".join(lines) + "\n").encode("utf-8")


def _openai_stream(text: str = "ok", finish: str = "stop", usage: dict | None = None) -> bytes:
    usage = usage or {"prompt_tokens": 3, "completion_tokens": 1}
    return _sse(
        {"model": "m", "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}]},
        {"model": "m", "choices": [{"index": 0, "delta": {}, "finish_reason": finish}], "usage": usage},
    )


def _anthropic_events(*blocks: tuple[str, str], stop: str | None = "end_turn", start_usage=None, delta_usage=None,
                      stop_event: bool = True, model: str = "claude-sonnet-5-5-20261001") -> bytes:
    """`blocks`：(`text`|`thinking`, 內容)；每段一個 block、一個 delta。`event:` 行照官方格式一併送出。"""
    start_usage = start_usage or {"input_tokens": 10, "cache_read_input_tokens": 4,
                                  "cache_creation_input_tokens": 0, "output_tokens": 1}
    delta_usage = delta_usage or {"output_tokens": 7}
    out: list[str] = []

    def ev(name: str, data: dict) -> None:
        out.extend([f"event: {name}", "data: " + json.dumps(data, ensure_ascii=False), ""])

    ev("message_start", {"type": "message_start", "message": {
        "id": "msg_1", "type": "message", "role": "assistant", "model": model, "content": [],
        "stop_reason": None, "usage": start_usage}})
    out.extend(["event: ping", 'data: {"type": "ping"}', ""])
    for i, (kind, text) in enumerate(blocks):
        start = {"type": "text", "text": ""} if kind == "text" else {"type": "thinking", "thinking": ""}
        ev("content_block_start", {"type": "content_block_start", "index": i, "content_block": start})
        delta = {"type": "text_delta", "text": text} if kind == "text" else {"type": "thinking_delta", "thinking": text}
        ev("content_block_delta", {"type": "content_block_delta", "index": i, "delta": delta})
        ev("content_block_stop", {"type": "content_block_stop", "index": i})
    if stop is not None:
        ev("message_delta", {"type": "message_delta", "delta": {"stop_reason": stop, "stop_sequence": None},
                             "usage": delta_usage})
    if stop_event:
        ev("message_stop", {"type": "message_stop"})
    return ("\n".join(out) + "\n").encode("utf-8")


def _anthropic_message(text: str | None = '{"a": 1}', stop: str = "end_turn", thinking: str | None = None,
                       usage: dict | None = None) -> dict:
    content = []
    if thinking is not None:
        content.append({"type": "thinking", "thinking": thinking, "signature": "sig"})
    if text is not None:
        content.append({"type": "text", "text": text})
    return {"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-sonnet-5-5-20261001",
            "content": content, "stop_reason": stop,
            "usage": usage or {"input_tokens": 20, "output_tokens": 5, "cache_read_input_tokens": 0,
                               "cache_creation_input_tokens": 0}}


def _openai_json(content: str = '{"a": 1}') -> dict:
    return {"model": "m", "choices": [{"index": 0, "message": {"content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 1}}


def _ok_response(prov: str, request: httpx.Request) -> httpx.Response:
    """各 provider 的成功回應（依請求是串流、非串流或模型清單）。"""
    if request.method == "GET":
        return httpx.Response(200, json={"data": []})
    body = json.loads(request.content)
    if prov == "anthropic":
        if body.get("stream"):
            return httpx.Response(200, content=_anthropic_events(("text", "ok")))
        return httpx.Response(200, json=_anthropic_message())
    if body.get("stream"):
        return httpx.Response(200, content=_openai_stream())
    return httpx.Response(200, json=_openai_json())


class _Case(unittest.IsolatedAsyncioTestCase):
    """裝 MockTransport、記下每個請求；`env()` 在測試範圍內換 provider。"""

    def setUp(self):
        lm._LOGGED.clear()
        _claude_cli._reset_state()
        self.requests: list[httpx.Request] = []
        self.handler = None

        def recording(request: httpx.Request):
            self.requests.append(request)
            return self.handler(request)

        lh._transport = httpx.MockTransport(recording)
        lh._reset_clients()

    def tearDown(self):
        lh._transport = None
        lh._reset_clients()
        _claude_cli._reset_state()

    def env(self, env: dict):
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)

    def body(self, i: int = -1) -> dict:
        return json.loads(self.requests[i].content)

    async def stream(self, model: str, max_tokens: int = 512, **kw) -> list:
        items = []
        async for item in lh.astream_chat(model, PROMPT, max_tokens=max_tokens, first_token_timeout=30,
                                          system=SYSTEM, task="ask_answer", user_id="web-ask_answer",
                                          total_timeout=600, **kw):
            items.append(item)
        return items

    async def json_call(self, model: str, **kw) -> lh.JsonResult:
        kw.setdefault("max_tokens", 2048)
        return await lh.complete_json(model, PROMPT, timeout=30, system=SYSTEM, task="faithfulness",
                                      user_id="judge-faithfulness", sleep=_no_sleep, **kw)


async def _no_sleep(_s):
    return None


def _outcome(items: list) -> lh.ChatOutcome:
    assert isinstance(items[-1], lh.ChatOutcome)
    return items[-1]


# ── golden：廷豐兩條路徑逐字不變 ─────────────────────────────────────────────
GOLDEN = json.loads((REPO_ROOT / "tests" / "fixtures" / "llm_wire_golden.json").read_text(encoding="utf-8"))
# httpx 自己加的 header（版本或安裝的解壓縮套件一變就不同）不是我們送的內容，不比。
_HTTPX_OWNED = {"accept-encoding", "connection", "content-length"}
GOLDEN_KEYS = {"openai": ("OPENAI_API_KEY", "fixed-test-secret-openai0"),
               "opencode_go": ("OPENCODE_GO_API_KEY", "fixed-test-secret-go0")}


def _wire(request: httpx.Request) -> dict:
    headers = {k: v for k, v in request.headers.items() if k not in _HTTPX_OWNED}
    if headers.get("user-agent", "").startswith("python-httpx/"):
        headers["user-agent"] = "<httpx 預設>"
    return {"method": request.method, "url": str(request.url), "headers": headers,
            "body": request.content.decode("utf-8")}


class GoldenWireTests(_Case):
    """openai／opencode_go 的 URL、header、body 與加入其他 provider 之前錄下的逐字相同（body 比位元組）。"""

    async def test_requests_match_the_golden_recording(self):
        # 自報身分由載入 profile 的入口設定（tests/test_llm_identity.py 釘住兩處）；這裡照預設 profile 設定。
        from app.profile import get_profile

        lm.set_identity_url(get_profile().institution.website)
        for prov, (key_env, key) in GOLDEN_KEYS.items():
            with self.subTest(prov=prov), mock.patch.dict(os.environ, {
                "LLM_PROVIDER": prov, key_env: key, "OPENAI_BASE_URL": "", "OPENCODE_GO_BASE_URL": "",
            }):
                self.handler = lambda r, p=prov: _ok_response(p, r)
                calls = {
                    "stream": lambda: self.stream("gpt-6-luna"),
                    "batch": lambda: asyncio.to_thread(
                        lh.complete_chat, "gpt-6-luna", PROMPT, max_tokens=1024, timeout=60, task="summary",
                        user_id="batch-summary"),
                    "json": lambda: self.json_call("gpt-6-luna"),
                    "probe": lambda: lh.probe_endpoint(3.5),
                }
                for name, call in calls.items():
                    self.requests.clear()
                    lh._reset_clients()
                    await call()
                    self.assertEqual(len(self.requests), 1, name)
                    expected = {k: v for k, v in GOLDEN[prov][name].items()}
                    expected["headers"] = {k: v for k, v in expected["headers"].items() if k not in _HTTPX_OWNED}
                    self.assertEqual(_wire(self.requests[0]), expected, f"{prov}/{name}")


# ── 請求組裝 ─────────────────────────────────────────────────────────────────
MESSAGES = [{"role": "system", "content": "你是分析師，只輸出 JSON。"}, {"role": "user", "content": CLEAN_PROMPT}]

# provider → 呼叫種類 → (方法, 網址, 認證 header, body)。body 以 JSON 字串比（鍵的順序就是送出的順序）。
EXPECTED_REQUESTS = {
    "azure_openai": {
        "stream": ("POST", "https://res.example.test/openai/deployments/luna-prod/chat/completions"
                           "?api-version=2025-04-01-preview", {"api-key": KEY},
                   {"messages": MESSAGES, "max_completion_tokens": 512, "stream": True, "reasoning_effort": "none",
                    "stream_options": {"include_usage": True}}),
        "batch": ("POST", "https://res.example.test/openai/deployments/luna-prod/chat/completions"
                          "?api-version=2025-04-01-preview", {"api-key": KEY},
                  {"messages": MESSAGES[1:], "max_completion_tokens": 1024, "stream": True,
                   "reasoning_effort": "none", "stream_options": {"include_usage": True}}),
        "json": ("POST", "https://res.example.test/openai/deployments/luna-prod/chat/completions"
                         "?api-version=2025-04-01-preview", {"api-key": KEY},
                 {"messages": MESSAGES, "max_completion_tokens": 2048, "stream": False, "reasoning_effort": "none",
                  "temperature": 0, "response_format": {"type": "json_object"}}),
        "probe": ("GET", "https://res.example.test/openai/models?api-version=2025-04-01-preview",
                  {"api-key": KEY}, None),
    },
    "anthropic": {
        "stream": ("POST", "https://anthropic.example.test/v1/messages",
                   {"x-api-key": KEY, "anthropic-version": "2023-06-01"},
                   {"model": "claude-sonnet-5-5", "max_tokens": 512, "system": "你是分析師，只輸出 JSON。",
                    "messages": [{"role": "user", "content": CLEAN_PROMPT}], "stream": True}),
        "batch": ("POST", "https://anthropic.example.test/v1/messages",
                  {"x-api-key": KEY, "anthropic-version": "2023-06-01"},
                  {"model": "claude-sonnet-5-5", "max_tokens": 1024,
                   "messages": [{"role": "user", "content": CLEAN_PROMPT}], "stream": True}),
        "json": ("POST", "https://anthropic.example.test/v1/messages",
                 {"x-api-key": KEY, "anthropic-version": "2023-06-01"},
                 {"model": "claude-sonnet-5-5", "max_tokens": 2048, "system": "你是分析師，只輸出 JSON。",
                  "messages": [{"role": "user", "content": CLEAN_PROMPT}], "stream": False, "temperature": 0}),
        "probe": ("GET", "https://anthropic.example.test/v1/models",
                  {"x-api-key": KEY, "anthropic-version": "2023-06-01"}, None),
    },
    "openai_compatible": {
        "stream": ("POST", "http://vllm.example.test:8000/v1/chat/completions", {"authorization": f"Bearer {KEY}"},
                   {"model": "Qwen/Qwen3-72B", "messages": MESSAGES, "max_tokens": 512, "stream": True,
                    "stream_options": {"include_usage": True}}),
        "batch": ("POST", "http://vllm.example.test:8000/v1/chat/completions", {"authorization": f"Bearer {KEY}"},
                  {"model": "Qwen/Qwen3-72B", "messages": MESSAGES[1:], "max_tokens": 1024, "stream": True,
                   "stream_options": {"include_usage": True}}),
        "json": ("POST", "http://vllm.example.test:8000/v1/chat/completions", {"authorization": f"Bearer {KEY}"},
                 {"model": "Qwen/Qwen3-72B", "messages": MESSAGES, "max_tokens": 2048, "stream": False,
                  "temperature": 0, "response_format": {"type": "json_object"}}),
        "probe": ("GET", "http://vllm.example.test:8000/v1/models", {"authorization": f"Bearer {KEY}"}, None),
    },
}
_AUTH_HEADERS = {"authorization", "api-key", "x-api-key", "anthropic-version"}


class RequestShapeTests(_Case):
    async def _run(self, prov: str) -> dict[str, httpx.Request]:
        self.handler = lambda r: _ok_response(prov, r)
        model = MODEL[prov]
        calls = {
            "stream": lambda: self.stream(model),
            "batch": lambda: asyncio.to_thread(lh.complete_chat, model, PROMPT, max_tokens=1024, timeout=60,
                                               task="summary", user_id="batch-summary"),
            "json": lambda: self.json_call(model),
            "probe": lambda: lh.probe_endpoint(3.5),
        }
        out = {}
        for name, call in calls.items():
            self.requests.clear()
            result = await call()
            if name == "probe":
                self.assertIsNone(result.kind, result.detail)
            self.assertEqual(len(self.requests), 1, name)
            out[name] = self.requests[0]
        return out

    async def test_each_provider_sends_the_expected_request(self):
        for prov, expected in EXPECTED_REQUESTS.items():
            with self.subTest(prov=prov), mock.patch.dict(os.environ, ENVS[prov]):
                seen = await self._run(prov)
                for name, (method, url, auth, body) in expected.items():
                    req = seen[name]
                    self.assertEqual((req.method, str(req.url)), (method, url), f"{prov}/{name}")
                    got_auth = {k: v for k, v in req.headers.items() if k in _AUTH_HEADERS}
                    self.assertEqual(got_auth, auth, f"{prov}/{name}")
                    if body is not None:
                        self.assertEqual(req.content.decode("utf-8"), json.dumps(body, ensure_ascii=False,
                                                                                 separators=(",", ":")),
                                         f"{prov}/{name}")
                    self.assertNotIn("x-opencode-session", req.headers)

    async def test_azure_without_reasoning_capability_omits_reasoning_effort(self):
        self.env({**AZURE_ENV, "AZURE_OPENAI_SUPPORTS_REASONING_EFFORT": "0"})
        self.handler = lambda r: _ok_response("azure_openai", r)
        await self.json_call("gpt-6-luna")
        body = self.body()
        self.assertNotIn("reasoning_effort", body)
        self.assertEqual(body["temperature"], 0)

    async def test_compat_with_reasoning_capability_sends_none(self):
        self.env({**COMPAT_ENV, "OPENAI_COMPATIBLE_SUPPORTS_REASONING_EFFORT": "1"})
        self.handler = lambda r: _ok_response("openai_compatible", r)
        await self.json_call("Qwen/Qwen3-72B")
        self.assertEqual(list(self.body()), ["model", "messages", "max_tokens", "stream", "reasoning_effort",
                                             "temperature", "response_format"])
        self.assertEqual(self.body()["reasoning_effort"], "none")

    async def test_azure_second_deployment_and_url_quoting(self):
        self.env(AZURE_ENV)
        self.handler = lambda r: _ok_response("azure_openai", r)
        await self.stream("gpt-4.1")
        self.assertEqual(self.requests[0].url.path, "/openai/deployments/gpt41-eu/chat/completions")

    async def test_anthropic_uses_no_bearer_and_no_go_headers(self):
        self.env(ANTHROPIC_ENV)
        self.handler = lambda r: _ok_response("anthropic", r)
        await self.stream("claude-sonnet-5-5")
        self.assertNotIn("authorization", self.requests[0].headers)
        self.assertEqual(self.requests[0].headers["accept"], "text/event-stream")


# ── 推理強度與 temperature ───────────────────────────────────────────────────
class ReasoningParamsTests(unittest.TestCase):
    """`reasoning_params` 的對應規則逐點釘住（現行一律 none；其他強度是開推理那天要用的規則）。"""

    EXPECTED = {
        # (provider, effort, max_tokens, temperature, supports_effort) → 送出的欄位
        ("openai", "none", 512, None, True): {"reasoning_effort": "none"},
        ("openai", "none", 512, 0, True): {"reasoning_effort": "none", "temperature": 0},
        ("openai", "medium", 512, 0, True): {"reasoning_effort": "medium"},
        ("azure_openai", "none", 512, 0, False): {"temperature": 0},
        ("azure_openai", "high", 512, 0, False): {"temperature": 0},
        ("openai_compatible", "none", 512, 0, False): {"temperature": 0},
        ("openai_compatible", "low", 512, 0, True): {"reasoning_effort": "low"},
        ("anthropic", "none", 512, 0, True): {"temperature": 0},
        ("anthropic", "none", 512, None, True): {},
        ("anthropic", "minimal", 4096, 0, True): {"thinking": {"type": "enabled", "budget_tokens": 1024}},
        ("anthropic", "low", 4096, None, True): {"thinking": {"type": "enabled", "budget_tokens": 2048}},
        ("anthropic", "medium", 16384, None, True): {"thinking": {"type": "enabled", "budget_tokens": 8192}},
        ("anthropic", "high", 16384, None, True): {"thinking": {"type": "enabled", "budget_tokens": 16383}},
        ("anthropic", "medium", 4096, None, True): {"thinking": {"type": "enabled", "budget_tokens": 4095}},
        # budget 壓到 max_tokens-1 後不到下限 1024：不開 thinking（降級），temperature 照送
        ("anthropic", "high", 1024, 0, True): {"temperature": 0},
        ("anthropic", "low", 16, 0, True): {"temperature": 0},
    }

    def test_table(self):
        for (prov, effort, max_tokens, temp, supports), expected in self.EXPECTED.items():
            with self.subTest(prov=prov, effort=effort, max_tokens=max_tokens):
                got = lh.reasoning_params(prov, effort, max_tokens=max_tokens, temperature=temp,
                                          supports_effort=supports)
                self.assertEqual(got, expected)

    def test_reasoning_is_off_by_constant_not_by_knob(self):
        """推理關是刻意的常數（換量尺要發 PR），不是環境旋鈕。"""
        self.assertEqual(lh.REASONING_EFFORT, "none")
        self.assertIn(lh.REASONING_EFFORT, lh.REASONING_EFFORTS)
        self.assertEqual(set(lh.ANTHROPIC_THINKING_BUDGET), set(lh.REASONING_EFFORTS) - {"none"})
        for rel in ("app/services/llm_http.py", "app/services/llm_models.py", "app/config.py"):
            self.assertNotIn("LLM_REASONING_EFFORT", (REPO_ROOT / rel).read_text(encoding="utf-8"), rel)

    def test_build_body_for_explicit_provider(self):
        body = lh.build_body("claude-sonnet-5-5", "q", max_tokens=64, provider="anthropic", system="s",
                             stream=False, temperature=0, response_format={"type": "json_object"})
        self.assertEqual(body, {"model": "claude-sonnet-5-5", "max_tokens": 64, "system": "s",
                                "messages": [{"role": "user", "content": "q"}], "stream": False, "temperature": 0})


# ── 串流解析 ─────────────────────────────────────────────────────────────────
class AnthropicStreamTests(_Case):
    def setUp(self):
        super().setUp()
        self.env(ANTHROPIC_ENV)

    def reply(self, content: bytes, status: int = 200, **kw):
        self.handler = lambda r: httpx.Response(status, content=content, **kw)

    async def test_text_deltas_usage_and_model(self):
        self.reply(_anthropic_events(("thinking", "先想想"), ("text", "第一段"), ("text", "第二段")))
        items = await self.stream("claude-sonnet-5-5")
        self.assertEqual(items[:-1], ["第一段", "第二段"])
        out = _outcome(items)
        self.assertIsNone(out.kind, out.detail)
        self.assertEqual(out.finish_reason, "stop")
        self.assertEqual(out.model_resp, "claude-sonnet-5-5-20261001")
        self.assertEqual(out.reasoning_chars, 3)
        self.assertEqual(
            {k: out.usage[k] for k in ("prompt_tokens", "prompt_cache_hit_tokens", "prompt_cache_miss_tokens",
                                       "completion_tokens")},
            {"prompt_tokens": 14, "prompt_cache_hit_tokens": 4, "prompt_cache_miss_tokens": 10,
             "completion_tokens": 7},
        )

    async def test_stop_reasons_map_to_finish_reasons(self):
        cases = {"end_turn": (None, "stop"), "stop_sequence": (None, "stop"), "max_tokens": (lh.TRUNCATED, "length"),
                 "model_context_window_exceeded": (lh.TRUNCATED, "length"),
                 "refusal": (lh.CONTENT_FILTER, "content_filter")}
        for stop, (kind, finish) in cases.items():
            with self.subTest(stop=stop):
                self.reply(_anthropic_events(("text", "部分"), stop=stop))
                out = _outcome(await self.stream("claude-sonnet-5-5"))
                self.assertEqual((out.kind, out.finish_reason, out.streamed), (kind, finish, True))

    async def test_error_event_before_text_is_overloaded(self):
        self.reply(b'event: error\ndata: {"type": "error", "error": {"type": "overloaded_error", '
                   b'"message": "Overloaded"}}\n\n')
        out = _outcome(await self.stream("claude-sonnet-5-5"))
        self.assertEqual(out.kind, lh.OVERLOADED)
        self.assertFalse(out.streamed)
        self.assertIn("overloaded_error", out.detail)

    async def test_stream_cut_without_stop_is_network(self):
        self.reply(_anthropic_events(("text", "半"), stop=None, stop_event=False))
        out = _outcome(await self.stream("claude-sonnet-5-5"))
        self.assertEqual((out.kind, out.streamed), (lh.NETWORK, True))

    async def test_thinking_only_is_empty(self):
        self.reply(_anthropic_events(("thinking", "只有推理")))
        out = _outcome(await self.stream("claude-sonnet-5-5"))
        self.assertEqual(out.kind, lh.EMPTY)
        self.assertIn("reasoning", out.detail)

    async def test_stream_completion_retries_overload_then_streams(self):
        """分派層（`llm.stream_completion`）的重試與截斷語意對 Anthropic 照樣成立。"""
        replies = iter([
            httpx.Response(529, json={"type": "error", "error": {"type": "overloaded_error", "message": "busy"}},
                           headers={"retry-after": "2"}),
            httpx.Response(200, content=_anthropic_events(("text", "答"), stop="max_tokens")),
        ])
        self.handler = lambda r: next(replies)
        waits: list[float] = []

        async def fake_sleep(s):
            waits.append(s)

        meta: dict = {}
        with mock.patch.object(llm, "_retry_sleep", fake_sleep):
            chunks = [c async for c in llm.stream_completion(
                "q", model="claude-sonnet-5-5", meta=meta, max_tokens=64, task="ask_answer")]
        self.assertEqual(chunks, ["答"])
        self.assertEqual(waits, [2.0])
        self.assertEqual(meta, {"truncated": True, "truncated_reason": llm.TRUNCATED_LENGTH})

    def test_batch_complete_chat_and_usage_log(self):
        self.reply(_anthropic_events(("text", "批次"), ("text", "結果")))
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {
            "LLM_USAGE_LOG": str(Path(d) / "usage.jsonl"), "SUMMARY_MODEL": "claude-sonnet-5-5",
        }):
            res = _claude_cli.run_claude("p", "claude-sonnet-5-5", 60, max_tokens=1024,
                                         meta={"task": "summary", "file_hash": "h1", "report_id": 1})
            row = json.loads((Path(d) / "usage.jsonl").read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual(res, _claude_cli.CliResult("批次結果", None, "claude-sonnet-5-5-20261001"))
        self.assertEqual(row["provider"], "anthropic")
        self.assertEqual(row["finish_reason"], "stop")
        self.assertEqual(row["tokens"], {"hit": 4, "miss": 10, "completion": 7, "reasoning": 0})
        self.assertEqual(set(row), {"ts", "task", "file_hash", "report_id", "backend", "model_req", "model_resp",
                                    "prompt_sha256", "tokens", "finish_reason", "kind", "attempts", "ttft_ms",
                                    "total_ms", "provider"})


class AzureAndCompatStreamTests(_Case):
    async def test_azure_prompt_filter_chunk_is_skipped(self):
        self.env(AZURE_ENV)
        filt = {"choices": [], "prompt_filter_results": [{"prompt_index": 0, "content_filter_results": {}}]}
        self.handler = lambda r: httpx.Response(200, content=_sse(
            filt, {"model": "gpt-6-luna", "choices": [{"index": 0, "delta": {"content": "答"}, "finish_reason": None}]},
            {"model": "gpt-6-luna", "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
             "usage": {"prompt_tokens": 9, "completion_tokens": 2,
                       "prompt_tokens_details": {"cached_tokens": 3}}},
        ))
        items = await self.stream("gpt-6-luna")
        out = _outcome(items)
        self.assertEqual(items[:-1], ["答"])
        self.assertIsNone(out.kind)
        self.assertEqual((out.usage["prompt_cache_hit_tokens"], out.usage["prompt_cache_miss_tokens"]), (3, 6))

    async def test_azure_content_filter_finish(self):
        self.env(AZURE_ENV)
        self.handler = lambda r: httpx.Response(200, content=_openai_stream("部分", finish="content_filter"))
        self.assertEqual(_outcome(await self.stream("gpt-6-luna")).kind, lh.CONTENT_FILTER)

    async def test_compat_reasoning_field_is_counted_not_streamed(self):
        self.env(COMPAT_ENV)
        self.handler = lambda r: httpx.Response(200, content=_sse(
            {"model": "Qwen/Qwen3-72B",
             "choices": [{"index": 0, "delta": {"reasoning": "思考中"}, "finish_reason": None}]},
            {"model": "Qwen/Qwen3-72B", "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
             "usage": {"prompt_tokens": 5, "completion_tokens": 3}},
        ))
        out = _outcome(await self.stream("Qwen/Qwen3-72B"))
        self.assertEqual((out.kind, out.reasoning_chars), (lh.EMPTY, 3))
        self.assertEqual((out.usage["prompt_cache_hit_tokens"], out.usage["prompt_cache_miss_tokens"]), (0, 5))

    def test_azure_retry_after_ms_wins(self):
        self.env(AZURE_ENV)
        replies = iter([
            httpx.Response(429, json={"error": {"code": "429", "message": "Rate limit"}},
                           headers={"retry-after": "9", "retry-after-ms": "1500"}),
            httpx.Response(200, content=_openai_stream()),
        ])
        self.handler = lambda r: next(replies)
        waits: list[float] = []
        res = lh.complete_chat("gpt-6-luna", "p", max_tokens=16, timeout=60, sleep=waits.append)
        self.assertEqual((res.kind, res.text, waits), (None, "ok", [1.5]))

    def test_retry_after_ms_is_azure_only(self):
        headers = httpx.Headers({"retry-after": "9", "retry-after-ms": "1500"})
        self.assertEqual(lh._retry_after(headers, "azure_openai"), 1.5)
        for prov in ("openai", "opencode_go", "anthropic", "openai_compatible"):
            self.assertEqual(lh._retry_after(headers, prov), 9.0, prov)


# ── 錯誤分類 ─────────────────────────────────────────────────────────────────
def _anth_err(etype: str, message: str) -> bytes:
    return json.dumps({"type": "error", "error": {"type": etype, "message": message}}).encode()


def _oa_err(message: str, code: str | None = None) -> bytes:
    return json.dumps({"error": {"message": message, "code": code}}).encode()


class ErrorClassificationTests(unittest.TestCase):
    EXPECTED = [
        # (provider, status, body, kind)
        ("anthropic", 402, _anth_err("billing_error", "Payment required"), lh.QUOTA),
        ("anthropic", 400, _anth_err("invalid_request_error",
                                     "Your credit balance is too low to access the Anthropic API."), lh.QUOTA),
        ("anthropic", 401, _anth_err("authentication_error", "invalid x-api-key"), lh.AUTH),
        ("anthropic", 403, _anth_err("permission_error", "not allowed"), lh.AUTH),
        ("anthropic", 404, _anth_err("not_found_error", "model: claude-nope"), lh.CONFIG),
        ("anthropic", 413, _anth_err("request_too_large", "Request exceeds the maximum allowed size"),
         lh.BAD_REQUEST),
        ("anthropic", 400, _anth_err("invalid_request_error", "max_tokens: must be at least 1"), lh.BAD_REQUEST),
        ("anthropic", 429, _anth_err("rate_limit_error", "Number of request tokens has exceeded your rate limit"),
         lh.OVERLOADED),
        ("anthropic", 500, _anth_err("api_error", "Internal server error"), lh.OVERLOADED),
        ("anthropic", 529, _anth_err("overloaded_error", "Overloaded"), lh.OVERLOADED),
        ("azure_openai", 400, _oa_err("The response was filtered due to the prompt triggering Azure OpenAI's "
                                      "content management policy.", "content_filter"), lh.CONTENT_FILTER),
        ("azure_openai", 404, _oa_err("The API deployment for this resource does not exist.", "DeploymentNotFound"),
         lh.CONFIG),
        ("azure_openai", 401, _oa_err("Access denied due to invalid subscription key.", "401"), lh.AUTH),
        ("azure_openai", 429, _oa_err("Requests have exceeded the token rate limit.", "429"), lh.OVERLOADED),
        ("azure_openai", 400, _oa_err("Unrecognized request argument supplied: reasoning_effort"), lh.BAD_REQUEST),
        ("openai_compatible", 404, _oa_err("The model `qwen-x` does not exist."), lh.CONFIG),
        ("openai_compatible", 400, _oa_err("The model `qwen-x` does not exist."), lh.CONFIG),
        ("openai_compatible", 400, _oa_err("This model's maximum context length is 32768 tokens."), lh.BAD_REQUEST),
        ("openai_compatible", 429, _oa_err("Too many requests"), lh.OVERLOADED),
        ("openai_compatible", 503, b"Service Unavailable", lh.OVERLOADED),
        # 既有路徑不變：OpenAI 的 413 仍是 other、credit balance 字樣不會被當成額度
        ("openai", 413, _oa_err("Request too large"), lh.OTHER),
        ("openai", 400, _oa_err("Your credit balance is too low"), lh.BAD_REQUEST),
    ]

    def test_table(self):
        for prov, status, raw, kind in self.EXPECTED:
            with self.subTest(prov=prov, status=status, raw=raw[:60]):
                self.assertEqual(lh.classify_status(status, raw, prov)[0], kind)

    def test_account_hints_cover_every_provider(self):
        for prov in lm.PROVIDERS:
            with self.subTest(prov=prov):
                self.assertEqual(set(_claude_cli._ACCOUNT_HINTS[prov]), set(lh.ACCOUNT_KINDS))
                self.assertIn(lm.PROVIDER_KEY_ENV[prov], _claude_cli._ACCOUNT_HINTS[prov][lh.AUTH])


class HttpErrorPathTests(_Case):
    async def test_anthropic_quota_in_stream_and_batch(self):
        self.env(ANTHROPIC_ENV)
        self.handler = lambda r: httpx.Response(400, content=_anth_err(
            "invalid_request_error", "Your credit balance is too low to access the Anthropic API."))
        self.assertEqual(_outcome(await self.stream("claude-sonnet-5-5")).kind, lh.QUOTA)
        with self.assertRaises(_claude_cli.LlmEnvironmentError) as cm:
            await asyncio.to_thread(_claude_cli.run_claude, "p", "claude-sonnet-5-5", 60, max_tokens=16,
                                    meta={"task": "summary", "file_hash": "h", "report_id": 1})
        self.assertIn("Anthropic Console", str(cm.exception))
        self.assertTrue(str(cm.exception).startswith("API[quota]"))


# ── JSON 模式 ────────────────────────────────────────────────────────────────
class AnthropicJsonTests(_Case):
    def setUp(self):
        super().setUp()
        self.env(ANTHROPIC_ENV)

    def reply(self, *messages: dict):
        it = iter(messages)
        self.handler = lambda r: httpx.Response(200, json=next(it))

    async def test_plain_and_fenced_json(self):
        for text, data in (('{"verdict": "ok"}', {"verdict": "ok"}), ('```json\n[1, 2]\n```', [1, 2]),
                           ('  ```\n{"a": 1}\n```  ', {"a": 1})):
            with self.subTest(text=text):
                self.reply(_anthropic_message(text))
                res = await self.json_call("claude-sonnet-5-5")
                self.assertEqual((res.kind, res.data), (None, data))
        self.assertNotIn("response_format", self.body())

    async def test_strict_parsing(self):
        for text, kind in (('結論：{"a": 1}', lh.INVALID_JSON), ("42", lh.INVALID_JSON),
                           ('```json\n{"a": 1}\n```\n補充說明', lh.INVALID_JSON)):
            with self.subTest(text=text):
                self.reply(_anthropic_message(text))
                res = await self.json_call("claude-sonnet-5-5", max_attempts=1)
                self.assertEqual(res.kind, kind)

    async def test_truncation_retries_with_double_budget_and_sums_usage(self):
        self.reply(_anthropic_message('{"a":', stop="max_tokens"), _anthropic_message('{"a": 1}'))
        res = await self.json_call("claude-sonnet-5-5", max_tokens=512)
        self.assertEqual((res.kind, res.data, res.attempts, res.max_tokens), (None, {"a": 1}, 2, 1024))
        self.assertEqual([json.loads(r.content)["max_tokens"] for r in self.requests], [512, 1024])
        self.assertEqual(res.usage, {"prompt_tokens": 40, "completion_tokens": 10, "prompt_cache_hit_tokens": 0,
                                     "prompt_cache_miss_tokens": 40})

    async def test_refusal_and_thinking_only(self):
        self.reply(_anthropic_message("I can't help with that.", stop="refusal"))
        self.assertEqual((await self.json_call("claude-sonnet-5-5")).kind, lh.CONTENT_FILTER)
        self.reply(_anthropic_message(None, thinking="想很久"), _anthropic_message(None, thinking="又想"))
        res = await self.json_call("claude-sonnet-5-5")
        self.assertEqual((res.kind, res.attempts), (lh.EMPTY, 2))
        self.assertEqual(res.outcome.reasoning_chars, 2)

    async def test_unexpected_stop_reason_is_other(self):
        self.reply(_anthropic_message('{"a": 1}', stop="tool_use"))
        res = await self.json_call("claude-sonnet-5-5")
        self.assertEqual(res.kind, lh.OTHER)
        self.assertIn("tool_use", res.detail)

    async def test_error_status_uses_anthropic_rules(self):
        self.handler = lambda r: httpx.Response(529, content=_anth_err("overloaded_error", "Overloaded"),
                                                headers={"retry-after": "1"})
        res = await self.json_call("claude-sonnet-5-5")
        self.assertEqual((res.kind, res.attempts), (lh.OVERLOADED, 2))


class OpenAiFamilyJsonTests(_Case):
    async def test_azure_and_compat_parse_like_openai(self):
        for prov in ("azure_openai", "openai_compatible"):
            with self.subTest(prov=prov), mock.patch.dict(os.environ, ENVS[prov]):
                self.handler = lambda r: httpx.Response(200, json=_openai_json('{"b": 2}'))
                res = await self.json_call(MODEL[prov])
                self.assertEqual((res.kind, res.data), (None, {"b": 2}))
                self.assertEqual(self.body()["response_format"], {"type": "json_object"})


# ── 白名單：內建＋設定宣告 ───────────────────────────────────────────────────
class WhitelistTests(_Case):
    def test_allowed_models_per_provider(self):
        cases = {
            "openai": ({"LLM_PROVIDER": "openai", "ANTHROPIC_MODELS": "claude-x"}, {"gpt-6-luna"}),
            "opencode_go": ({"LLM_PROVIDER": "opencode_go", "OPENAI_COMPATIBLE_MODELS": "qwen"}, {"gpt-6-luna"}),
            "azure_openai": (AZURE_ENV, {"gpt-6-luna", "gpt-4.1"}),
            "anthropic": (ANTHROPIC_ENV, {"claude-sonnet-5-5", "claude-haiku-4-5"}),
            "openai_compatible": (COMPAT_ENV, {"Qwen/Qwen3-72B"}),
        }
        for prov, (env, allowed) in cases.items():
            with self.subTest(prov=prov):
                self.assertEqual(lm.allowed_models(env), frozenset(allowed))

    def test_declarations_only_count_for_the_current_provider(self):
        env = {**COMPAT_ENV, "ANTHROPIC_MODELS": "claude-sonnet-5-5", "AZURE_OPENAI_DEPLOYMENTS": "x=y"}
        self.assertFalse(lm.is_http_model("claude-sonnet-5-5", env))
        self.assertFalse(lm.is_http_model("gpt-6-luna", env))  # 內建名稱對宣告型 provider 不放行
        self.assertTrue(lm.is_http_model("Qwen/Qwen3-72B", env))
        self.assertFalse(lm.is_http_model("", env))
        self.assertFalse(lm.is_http_model(None, env))

    def test_azure_needs_a_deployment_even_for_builtin_names(self):
        env = {**AZURE_ENV, "AZURE_OPENAI_DEPLOYMENTS": "gpt-4.1=gpt41"}
        self.assertFalse(lm.is_http_model("gpt-6-luna", env))

    def test_malformed_declarations_are_not_allowed(self):
        env = {**AZURE_ENV, "AZURE_OPENAI_DEPLOYMENTS": "good=dep-1,bad,also bad=x,evil=../../x,dup=a,dup=b"}
        self.assertEqual(lm.allowed_models(env), frozenset({"good"}))
        problems = "\n".join(lm.config_problems(env))
        for fragment in ("'bad'", "also bad", "../../x", "dup"):
            self.assertIn(fragment, problems)
        self.assertIn("AZURE_OPENAI_DEPLOYMENTS", problems)

    async def test_undeclared_model_never_leaves_the_process(self):
        self.env(ANTHROPIC_ENV)
        self.handler = lambda r: self.fail("不該送出")
        with self.assertRaises(llm.LLMUnavailableError) as cm:
            async for _ in llm.stream_completion("q", model="gpt-6-luna", max_tokens=16, task="ask_answer"):
                pass
        self.assertEqual(cm.exception.kind, lh.CONFIG)
        with self.assertRaises(_claude_cli.LlmEnvironmentError) as cm2:
            _claude_cli.run_claude("p", "claude-opus-9", 60, max_tokens=16, meta={"task": "summary"})
        self.assertIn("claude-haiku-4-5、claude-sonnet-5-5", str(cm2.exception))
        self.assertEqual(self.requests, [])

    def test_whitelist_is_not_a_bypassable_flag(self):
        """白名單是安全閘門：沒有任何環境變數能讓白名單外的名稱放行（宣告也只對目前 provider 有效）。"""
        env = {**ANTHROPIC_ENV, "LLM_DEFAULT_MODEL": "claude-opus-9", "ASK_ANSWER_MODEL": "claude-opus-9",
               "LLM_ALLOW_ANY_MODEL": "1", "LLM_WHITELIST": "0"}
        self.assertFalse(lm.is_http_model("claude-opus-9", env))

    def test_default_model_knob(self):
        env = {**ANTHROPIC_ENV, **{k: "" for k in lm.TASK_ENV.values()}, "LLM_DEFAULT_MODEL": " claude-sonnet-5-5 "}
        resolved = lm.resolve_all(lm.TASK_ENV, env)
        self.assertEqual(resolved.pop("ask_web"), "")
        self.assertEqual(set(resolved.values()), {"claude-sonnet-5-5"})
        env["SUMMARY_MODEL"] = "claude-haiku-4-5"
        self.assertEqual(lm.resolve_model("summary", env=env), "claude-haiku-4-5")

    def test_judge_lineage(self):
        self.assertEqual(lm.judge_lineage("gpt-6-luna", AZURE_ENV), lm.JUDGE_LINEAGE_LUNA)
        self.assertEqual(lm.judge_lineage("gpt-6-luna", ANTHROPIC_ENV), lm.JUDGE_LINEAGE_LUNA)
        self.assertEqual(lm.judge_lineage("claude-sonnet-5-5", ANTHROPIC_ENV), "declared:claude-sonnet-5-5")
        self.assertEqual(lm.judge_lineage("Qwen/Qwen3-72B", COMPAT_ENV), "declared:Qwen/Qwen3-72B")
        # 沒有宣告時照舊依名稱歸到歷史系譜
        self.assertEqual(lm.judge_lineage("claude-haiku", {"LLM_PROVIDER": "openai"}), lm.JUDGE_LINEAGE_CLAUDE)
        self.assertEqual(lm.judge_lineage("deepseek-chat", COMPAT_ENV), lm.JUDGE_LINEAGE_DEEPSEEK_ERA)


class ConfigProblemTests(_Case):
    EXPECTED = [
        # (環境, 訊息裡必須出現的旋鈕)
        ({**AZURE_ENV, "AZURE_OPENAI_ENDPOINT": ""}, "AZURE_OPENAI_ENDPOINT"),
        ({**AZURE_ENV, "AZURE_OPENAI_API_VERSION": ""}, "AZURE_OPENAI_API_VERSION"),
        ({**AZURE_ENV, "AZURE_OPENAI_DEPLOYMENTS": ""}, "AZURE_OPENAI_DEPLOYMENTS"),
        ({**AZURE_ENV, "AZURE_OPENAI_SUPPORTS_REASONING_EFFORT": "maybe"}, "AZURE_OPENAI_SUPPORTS_REASONING_EFFORT"),
        ({**ANTHROPIC_ENV, "ANTHROPIC_MODELS": " , "}, "ANTHROPIC_MODELS"),
        ({**ANTHROPIC_ENV, "ANTHROPIC_BASE_URL": "anthropic.example.test"}, "ANTHROPIC_BASE_URL"),
        ({**COMPAT_ENV, "OPENAI_COMPATIBLE_BASE_URL": ""}, "OPENAI_COMPATIBLE_BASE_URL"),
        ({**COMPAT_ENV, "OPENAI_COMPATIBLE_SUPPORTS_REASONING_EFFORT": "2"},
         "OPENAI_COMPATIBLE_SUPPORTS_REASONING_EFFORT"),
    ]

    def test_each_missing_or_bad_knob_is_named(self):
        for env, knob in self.EXPECTED:
            with self.subTest(knob=knob):
                problems = lm.config_problems(env)
                self.assertTrue(problems)
                self.assertIn(knob, "；".join(problems))

    def test_complete_configs_have_no_problems(self):
        for prov, env in ENVS.items():
            with self.subTest(prov=prov):
                self.assertEqual(lm.config_problems(env), [])
        self.assertEqual(lm.config_problems({"LLM_PROVIDER": "openai"}), [])
        self.assertEqual(lm.config_problems({"LLM_PROVIDER": "anthropic", "ANTHROPIC_MODELS": "c"}), [])

    async def test_llm_http_fails_config_before_sending(self):
        self.handler = lambda r: self.fail("不該送出")
        for env, knob in self.EXPECTED:
            with self.subTest(knob=knob), mock.patch.dict(os.environ, env):
                prov = lm.provider()
                out = _outcome(await self.stream(MODEL[prov]))
                self.assertEqual(out.kind, lh.CONFIG)
                self.assertIn(knob, out.detail)
                self.assertEqual((await lh.probe_endpoint(1)).kind, lh.CONFIG)
        self.assertEqual(self.requests, [])

    async def test_missing_key_names_the_provider_knob(self):
        for prov, env in ENVS.items():
            with self.subTest(prov=prov), mock.patch.dict(os.environ, {**env, lm.PROVIDER_KEY_ENV[prov]: ""}):
                out = _outcome(await self.stream(MODEL[prov]))
                self.assertEqual((out.kind, out.detail), (lh.AUTH, f"缺 {lm.PROVIDER_KEY_ENV[prov]}"))

    def test_diagnose_reports_declarations_and_problems(self):
        env = {**ANTHROPIC_ENV, "AZURE_OPENAI_DEPLOYMENTS": ""}
        resolved = {"ask_answer": "gpt-6-luna", "ask_intent": "claude-sonnet-5-5", "ask_web": ""}
        out = lm.diagnose(resolved, has_key=True, prov="anthropic", allowed=lm.allowed_models(env),
                          problems=["缺 X"])
        msgs = [m for _, m in out]
        self.assertTrue(any("ask_answer=gpt-6-luna" in m and "claude-haiku-4-5、claude-sonnet-5-5" in m for m in msgs))
        self.assertFalse(any("ask_intent=" in m for m in msgs))
        self.assertIn("LLM 端點設定（LLM_PROVIDER=anthropic）：缺 X", msgs)
        out = lm.diagnose({"ask_answer": "x"}, has_key=True, prov="openai_compatible", allowed=frozenset())
        self.assertIn("LLM_PROVIDER=openai_compatible 沒有宣告任何模型", out[0][1])


# ── 批次預檢 ─────────────────────────────────────────────────────────────────
class PreflightTests(unittest.TestCase):
    def setUp(self):
        le._STATE.clear()
        le._STATE.update({"path": Path("/nonexistent/report-mark-llm"), "error": "missing", "duplicates": [],
                          "file_keys": [], "preset_empty": []})
        self.addCleanup(le._STATE.clear)
        patcher = mock.patch.object(le, "_warn_if_not_deploy_root")
        patcher.start()
        self.addCleanup(patcher.stop)

    def require(self, env, models) -> tuple[int | None, str]:
        err = io.StringIO()
        code = None
        with mock.patch.dict(os.environ, env), contextlib.redirect_stderr(err):
            try:
                le.require_llm_key(models)
            except SystemExit as exc:
                code = exc.code
        return code, err.getvalue()

    def test_ok_prints_provider_label_and_fingerprint(self):
        for prov, env in ENVS.items():
            with self.subTest(prov=prov):
                code, err = self.require(env, {"summary": MODEL[prov]})
                self.assertIsNone(code, err)
                self.assertIn(f"{lm.PROVIDER_LABEL[prov]} 金鑰 fp={le.fingerprint(KEY)}", err)
                self.assertNotIn(KEY, err)

    def test_missing_key_names_the_knob(self):
        code, err = self.require({**ANTHROPIC_ENV, "ANTHROPIC_API_KEY": ""}, {"summary": "claude-sonnet-5-5"})
        self.assertEqual(code, 2)
        self.assertIn("ANTHROPIC_API_KEY", err)
        self.assertIn("LLM_PROVIDER=anthropic", err)

    def test_endpoint_problem_is_rc2_with_the_knob(self):
        code, err = self.require({**AZURE_ENV, "AZURE_OPENAI_API_VERSION": ""}, {"tag": "gpt-6-luna"})
        self.assertEqual(code, 2)
        self.assertIn("AZURE_OPENAI_API_VERSION", err)

    def test_undeclared_model_is_rc2_and_lists_the_declared_ones(self):
        code, err = self.require(COMPAT_ENV, {"summary": "gpt-6-luna"})
        self.assertEqual(code, 2)
        self.assertIn("只接受 Qwen/Qwen3-72B", err)
        self.assertIn("summary 預設 gpt-6-luna", err)

    def test_default_model_knob_is_named_as_the_source(self):
        env = {**COMPAT_ENV, "LLM_DEFAULT_MODEL": "llama-x", "SUMMARY_MODEL": ""}
        code, err = self.require(env, {"summary": lm.resolve_model("summary", env=env)})
        self.assertEqual(code, 2)
        self.assertIn("LLM_DEFAULT_MODEL=llama-x", err)

    def test_new_provider_values_are_valid(self):
        for prov in ("azure_openai", "anthropic", "openai_compatible"):
            with mock.patch.dict(os.environ, {"LLM_PROVIDER": prov}):
                self.assertIsNone(le._invalid_provider())


class EnvFileCompareTests(unittest.TestCase):
    def _files(self, *texts: str) -> list[str]:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        paths = []
        for i, text in enumerate(texts):
            p = Path(tmp.name) / f"f{i}.env"
            p.write_text(text, encoding="utf-8")
            paths.append(str(p))
        return paths

    def run_main(self, *texts: str) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = le.main(self._files(*texts))
        return rc, out.getvalue()

    def test_new_provider_key_and_endpoint_knobs_are_compared(self):
        base = (f"LLM_PROVIDER=anthropic\nANTHROPIC_API_KEY={KEY}\n"
                "ANTHROPIC_BASE_URL=https://a.example.test\nANTHROPIC_MODELS=claude-sonnet-5-5\n")
        rc, out = self.run_main(base, base)
        self.assertEqual(rc, 0, out)
        self.assertIn(f"ANTHROPIC_API_KEY fp={le.fingerprint(KEY)}", out)
        self.assertNotIn(KEY, out)
        rc, out = self.run_main(base, base.replace("claude-sonnet-5-5", "claude-haiku-4-5"))
        self.assertEqual(rc, 1, out)

    def test_builtin_providers_do_not_compare_endpoint_knobs(self):
        """既有部署的核對結果不變：openai／opencode_go 只比金鑰與 provider。"""
        a = f"LLM_PROVIDER=opencode_go\nOPENCODE_GO_API_KEY={KEY}\nOPENCODE_GO_BASE_URL=https://x.example.test\n"
        b = f"LLM_PROVIDER=opencode_go\nOPENCODE_GO_API_KEY={KEY}\n"
        rc, out = self.run_main(a, b)
        self.assertEqual(rc, 0, out)
        self.assertNotIn("OPENCODE_GO_BASE_URL", out)


# ── /healthz/llm ─────────────────────────────────────────────────────────────
class HealthTests(_Case):
    def setUp(self):
        super().setUp()
        llm_health.reset()
        self.addCleanup(llm_health.reset)
        self._marks = lh._quota_seen_at, lh._ok_seen_at
        lh._quota_seen_at = lh._ok_seen_at = 0.0

    def tearDown(self):
        lh._quota_seen_at, lh._ok_seen_at = self._marks
        super().tearDown()

    async def test_probe_per_provider(self):
        for prov, env in ENVS.items():
            with self.subTest(prov=prov), mock.patch.dict(os.environ, {**env, "ASK_ANSWER_MODEL": MODEL[prov]}):
                llm_health.reset()
                self.requests.clear()
                self.handler = lambda r: httpx.Response(200, json={"data": []})
                self.assertEqual(await llm_health.report(), ("ok", 200))
                self.assertEqual(str(self.requests[0].url), EXPECTED_REQUESTS[prov]["probe"][1])
                llm_health.reset()
                self.handler = lambda r: httpx.Response(401, json={"error": {"message": "bad key"}})
                self.assertEqual(await llm_health.report(), ("auth_failed", 503))

    async def test_endpoint_problems_are_misconfigured_without_probing(self):
        self.handler = lambda r: self.fail("不該探端點")
        with mock.patch.dict(os.environ, {**AZURE_ENV, "AZURE_OPENAI_API_VERSION": "",
                                          "ASK_ANSWER_MODEL": "gpt-6-luna"}):
            self.assertEqual(await llm_health.report(), ("misconfigured", 503))

    async def test_undeclared_answer_model_is_misconfigured(self):
        with mock.patch.dict(os.environ, {**ANTHROPIC_ENV, "ASK_ANSWER_MODEL": ""}):
            self.assertEqual(await llm_health.report(), ("misconfigured", 503))


if __name__ == "__main__":
    unittest.main()
