"""app/services/llm_http.py：DeepSeek HTTP 客戶端。

全程不連網：一律以 `httpx.MockTransport` 注入（`llm_http._transport`）。金鑰只用 gitleaks
allowlist 內的假值（`fixed-test-secret-…`），而且以 `mock.patch.dict` 限定在單一測試內——
conftest 在最上方把 `DEEPSEEK_API_KEY` 強制成空字串，這裡的每一條都要自己給。
"""
from __future__ import annotations

import ast
import asyncio
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services import llm_http as lh  # noqa: E402

FAKE_KEY = "fixed-test-secret-deepseek0"
ENV = {"DEEPSEEK_API_KEY": FAKE_KEY, "DEEPSEEK_BASE_URL": "https://api.example.test"}


def _sse(*events, done: bool = True) -> bytes:
    out = []
    for ev in events:
        out.append(ev if isinstance(ev, str) else "data: " + json.dumps(ev, ensure_ascii=False))
        out.append("")
    if done:
        out += ["data: [DONE]", ""]
    return ("\n".join(out) + "\n").encode("utf-8")


def _chunk(content=None, reasoning=None, finish=None, usage=None, model="deepseek-v4.1-flash"):
    delta = {}
    if content is not None:
        delta["content"] = content
    if reasoning is not None:
        delta["reasoning_content"] = reasoning
    obj = {"model": model, "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
    obj["usage"] = usage
    return obj


USAGE = {
    "prompt_tokens": 12, "completion_tokens": 3, "prompt_cache_hit_tokens": 0,
    "prompt_cache_miss_tokens": 12, "completion_tokens_details": {"reasoning_tokens": 0},
}


class _TransportMixin:
    """把 handler 裝進 llm_http，記下每一個送出的請求。"""

    def install(self, handler):
        self.requests: list[httpx.Request] = []

        def recording(request: httpx.Request):
            self.requests.append(request)
            return handler(request)

        lh._transport = httpx.MockTransport(recording)
        lh._reset_clients()

    def uninstall(self):
        lh._transport = None
        lh._reset_clients()

    def body(self, i: int = 0) -> dict:
        return json.loads(self.requests[i].content)


# ── 純函式 ───────────────────────────────────────────────────────────────────
class WhitelistTests(unittest.TestCase):
    def test_only_explicit_names_go_http(self):
        for name in ("deepseek-flash", "deepseek-v4-pro", "deepseek-v4-flash"):
            self.assertTrue(lh.is_http_model(name), name)
        # 打錯字、CLI 別名、Claude 名稱、空值都不得被送到付費端點
        for name in ("deepseek-flsh", "sonnet", "haiku", "claude-sonnet-5", "", None, "deepseek-chat"):
            self.assertFalse(lh.is_http_model(name), name)


class BuildBodyTests(unittest.TestCase):
    def test_thinking_off_with_both_switches(self):
        """文件沒寫兩個開關衝突時誰優先，只送一個不保證關得掉。"""
        body = lh.build_body("deepseek-flash", "q", max_tokens=512)
        self.assertEqual(body["thinking"], {"type": "disabled"})
        self.assertEqual(body["reasoning_effort"], "none")

    def test_max_tokens_and_usage_option(self):
        body = lh.build_body("deepseek-flash", "q", max_tokens=16384)
        self.assertEqual(body["max_tokens"], 16384)
        self.assertTrue(body["stream"])
        self.assertEqual(body["stream_options"], {"include_usage": True})

    def test_non_stream_has_no_stream_options(self):
        """官方：stream_options 沒搭配 stream:true 會回 400。"""
        body = lh.build_body("deepseek-flash", "q", max_tokens=16, stream=False)
        self.assertNotIn("stream_options", body)

    def test_system_optional_and_sanitized(self):
        body = lh.build_body("deepseek-flash", "a\x00b\ud800", max_tokens=16, system="s\x00ys")
        self.assertEqual(body["messages"][0], {"role": "system", "content": "sys"})
        self.assertEqual(body["messages"][1]["content"], "ab\ufffd")
        body = lh.build_body("deepseek-flash", "q", max_tokens=16)
        self.assertEqual([m["role"] for m in body["messages"]], ["user"])

    def test_sanitized_body_is_utf8_encodable(self):
        body = lh.build_body("deepseek-flash", "x\udfff", max_tokens=16)
        json.dumps(body, ensure_ascii=False).encode("utf-8")

    def test_user_id_shape(self):
        body = lh.build_body("deepseek-flash", "q", max_tokens=16, user_id="web ask/主答")
        self.assertRegex(body["user_id"], r"^[A-Za-z0-9_-]+$")
        self.assertNotIn("user_id", lh.build_body("deepseek-flash", "q", max_tokens=16))


class ParseSseTests(unittest.TestCase):
    def test_skips_blank_comment_and_other_fields(self):
        for line in ("", ": keep-alive", ":", "event: x", "id: 3", "\r"):
            self.assertIsNone(lh.parse_sse_line(line), repr(line))

    def test_done_and_json(self):
        self.assertIs(lh.parse_sse_line("data: [DONE]"), lh.DONE)
        self.assertEqual(lh.parse_sse_line('data: {"a": 1}\r'), {"a": 1})

    def test_malformed_becomes_error(self):
        self.assertIn("error", lh.parse_sse_line("data: {oops"))


class ClassifyStatusTests(unittest.TestCase):
    def test_table(self):
        cases = [
            (401, {"error": {"message": "Authentication Fails"}}, lh.AUTH),
            (402, {"error": {"message": "Insufficient Balance"}}, lh.QUOTA),
            (404, "", lh.CONFIG),
            (400, {"error": {"message": "Content Exists Risk", "type": "invalid_request_error"}}, lh.CONTENT_FILTER),
            (400, {"error": {"message": "Model Not Exist"}}, lh.CONFIG),
            (400, {"error": {"message": "Invalid request: messages[0]"}}, lh.BAD_REQUEST),
            (422, {"error": {"message": "Invalid Parameters"}}, lh.BAD_REQUEST),
            (429, "", lh.OVERLOADED),
            (500, "", lh.OVERLOADED),
            (503, "Server Busy", lh.OVERLOADED),
            (418, "", lh.OTHER),
        ]
        for status, payload, kind in cases:
            raw = json.dumps(payload) if isinstance(payload, dict) else payload
            with self.subTest(status=status, payload=payload):
                self.assertEqual(lh.classify_status(status, raw)[0], kind)

    def test_detail_redacts_echoed_key(self):
        # 執行期組字串：寫成字面值的話，gitleaks 內建的 generic-api-key 規則會抓這個測試檔
        echoed = "sk-" + "abcd1234" + "efgh"
        _, detail = lh.classify_status(
            401, json.dumps({"error": {"message": "Your api key: " + echoed + " is invalid"}})
        )
        self.assertNotIn("abcd1234", detail)
        self.assertIn("HTTP 401", detail)


class ErrorStringTests(unittest.TestCase):
    def test_prefix_and_single_line(self):
        s = lh.error_string(lh.OVERLOADED, "HTTP 503\tbusy\nline2")
        self.assertTrue(s.startswith("API[overloaded] "))
        self.assertNotIn("\t", s)
        self.assertNotIn("\n", s)

    def test_every_kind_distinct(self):
        kinds = [lh.AUTH, lh.QUOTA, lh.CONFIG, lh.CONTENT_FILTER, lh.BAD_REQUEST, lh.OVERLOADED,
                 lh.NETWORK, lh.TIMEOUT, lh.TRUNCATED, lh.EMPTY, lh.OTHER]
        strings = {lh.error_string(k) for k in kinds}
        self.assertEqual(len(strings), len(kinds))

    def test_length_capped_and_redacted(self):
        s = lh.error_string(lh.OTHER, "sk-0123456789abcdef " + "x" * 1000)
        self.assertLessEqual(len(s), 300)
        self.assertNotIn("0123456789abcdef", s)

    def test_account_and_transient_sets(self):
        self.assertEqual(lh.ACCOUNT_KINDS, {lh.AUTH, lh.QUOTA, lh.CONFIG})
        self.assertEqual(lh.TRANSIENT_KINDS, {lh.OVERLOADED, lh.NETWORK})
        # 決定性的失敗不可列為可重試：重打只是再付一次錢
        for kind in (lh.TRUNCATED, lh.CONTENT_FILTER, lh.EMPTY, lh.BAD_REQUEST, lh.TIMEOUT):
            self.assertNotIn(kind, lh.TRANSIENT_KINDS)


# ── 線上串流 ─────────────────────────────────────────────────────────────────
async def _collect(agen) -> tuple[list[str], lh.ChatOutcome]:
    """收齊文字；結局物件必須恰好一個、而且在最後。"""
    texts: list[str] = []
    outcomes: list[lh.ChatOutcome] = []
    async for item in agen:
        if isinstance(item, lh.ChatOutcome):
            outcomes.append(item)
        else:
            if outcomes:
                raise AssertionError("ChatOutcome 之後不得再有文字")
            texts.append(item)
    if len(outcomes) != 1:
        raise AssertionError(f"ChatOutcome 應恰好一個，實際 {len(outcomes)}")
    return texts, outcomes[0]


class AstreamChatTests(_TransportMixin, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._env = mock.patch.dict(os.environ, ENV)
        self._env.start()

    def tearDown(self):
        self._env.stop()
        self.uninstall()

    def _stream(self, **kw):
        kw.setdefault("max_tokens", 256)
        kw.setdefault("first_token_timeout", 5.0)
        return lh.astream_chat("deepseek-flash", "問題", **kw)

    async def test_happy_path_filters_reasoning_and_keepalive(self):
        payload = _sse(
            ": keep-alive",
            _chunk(reasoning="思考中……"),
            _chunk(content="台積電"),
            ": keep-alive",
            _chunk(content="目標價"),
            _chunk(content="", finish="stop"),
            {"model": "deepseek-v4.1-flash", "choices": [], "usage": USAGE},
        )
        self.install(lambda req: httpx.Response(200, content=payload))
        texts, out = await _collect(self._stream(system="系統", task="t", user_id="web-ask"))
        self.assertEqual(texts, ["台積電", "目標價"])
        self.assertIsNone(out.kind)
        self.assertTrue(out.streamed)
        self.assertEqual(out.usage, USAGE)
        self.assertEqual(out.reasoning_chars, len("思考中……"))
        self.assertEqual(out.model_resp, "deepseek-v4.1-flash")
        self.assertIsNotNone(out.ttft_ms)
        # 請求形狀
        req = self.requests[0]
        self.assertEqual(str(req.url), "https://api.example.test/chat/completions")
        self.assertEqual(req.headers["authorization"], f"Bearer {FAKE_KEY}")
        body = self.body()
        self.assertEqual(body["thinking"], {"type": "disabled"})
        self.assertEqual(body["reasoning_effort"], "none")
        self.assertEqual(body["max_tokens"], 256)
        self.assertEqual(body["messages"][0]["content"], "系統")

    async def test_missing_key_sends_nothing(self):
        self.install(lambda req: httpx.Response(200, content=_sse()))
        with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": ""}):
            texts, out = await _collect(self._stream())
        self.assertEqual(texts, [])
        self.assertEqual(out.kind, lh.AUTH)
        self.assertEqual(self.requests, [])

    async def test_error_statuses(self):
        cases = [
            (402, {"error": {"message": "Insufficient Balance"}}, {}, lh.QUOTA, None),
            (429, {"error": {"message": "Rate Limit"}}, {"Retry-After": "5"}, lh.OVERLOADED, 5.0),
            (400, {"error": {"message": "Content Exists Risk"}}, {}, lh.CONTENT_FILTER, None),
            (401, {"error": {"message": "Authentication Fails"}}, {}, lh.AUTH, None),
        ]
        for status, payload, headers, kind, retry_after in cases:
            with self.subTest(status=status):
                self.install(lambda req, s=status, p=payload, h=headers: httpx.Response(s, json=p, headers=h))
                texts, out = await _collect(self._stream())
                self.assertEqual(texts, [])
                self.assertEqual(out.kind, kind)
                self.assertEqual(out.status, status)
                self.assertEqual(out.retry_after, retry_after)
                self.assertFalse(out.streamed)

    async def test_length_after_text_is_truncated_but_streamed(self):
        payload = _sse(_chunk(content="一半"), _chunk(content="", finish="length", usage=USAGE))
        self.install(lambda req: httpx.Response(200, content=payload))
        texts, out = await _collect(self._stream())
        self.assertEqual(texts, ["一半"])
        self.assertEqual(out.kind, lh.TRUNCATED)
        self.assertTrue(out.streamed)

    async def test_content_filter_finish_reason(self):
        payload = _sse(_chunk(content="部分"), _chunk(content="", finish="content_filter"))
        self.install(lambda req: httpx.Response(200, content=payload))
        _, out = await _collect(self._stream())
        self.assertEqual(out.kind, lh.CONTENT_FILTER)

    async def test_reasoning_only_is_empty_never_substituted(self):
        """content 空時絕不拿 reasoning 補位——它會經純文字 fallback 寫進 DB。"""
        payload = _sse(_chunk(reasoning="長長的推理"), _chunk(content="", finish="stop"))
        self.install(lambda req: httpx.Response(200, content=payload))
        texts, out = await _collect(self._stream())
        self.assertEqual(texts, [])
        self.assertEqual(out.kind, lh.EMPTY)
        self.assertNotIn("推理", "".join(texts))

    async def test_stream_cut_without_done_is_network(self):
        payload = _sse(_chunk(content="開頭"), done=False)
        self.install(lambda req: httpx.Response(200, content=payload))
        texts, out = await _collect(self._stream())
        self.assertEqual(texts, ["開頭"])
        self.assertEqual(out.kind, lh.NETWORK)

    async def test_in_stream_error_objects(self):
        for message, kind in (("Content Exists Risk", lh.CONTENT_FILTER), ("server error", lh.OVERLOADED)):
            with self.subTest(message=message):
                payload = _sse({"error": {"message": message}})
                self.install(lambda req, p=payload: httpx.Response(200, content=p))
                texts, out = await _collect(self._stream())
                self.assertEqual(texts, [])
                self.assertEqual(out.kind, kind)

    async def test_connect_error_is_network(self):
        def boom(req):
            raise httpx.ConnectError("refused", request=req)

        self.install(boom)
        _, out = await _collect(self._stream())
        self.assertEqual(out.kind, lh.NETWORK)

    async def test_first_token_deadline_under_keepalive(self):
        """伺服器排隊時一直送 keep-alive：read 逾時永遠不觸發，只能靠首字期限收手。"""

        async def queued():
            # 有上限：期限機制若被改壞，測試要以斷言失敗收場，而不是卡住整個 pytest
            for _ in range(150):
                yield b": keep-alive\n\n"
                await asyncio.sleep(0.02)

        self.install(lambda req: httpx.Response(200, content=queued()))
        t0 = time.monotonic()
        texts, out = await _collect(self._stream(first_token_timeout=0.2))
        self.assertLess(time.monotonic() - t0, 2.0)
        self.assertEqual(texts, [])
        self.assertEqual(out.kind, lh.TIMEOUT)

    async def test_deadline_does_not_cut_after_first_token(self):
        """首字期限只管到第一個字；之後的慢串流不得被靜默截斷。"""

        async def slow_answer():
            yield _sse(_chunk(content="第一段"), done=False)
            await asyncio.sleep(0.4)
            yield _sse(_chunk(content="第二段"), _chunk(content="", finish="stop"))

        self.install(lambda req: httpx.Response(200, content=slow_answer()))
        texts, out = await _collect(self._stream(first_token_timeout=0.15))
        self.assertEqual(texts, ["第一段", "第二段"])
        self.assertIsNone(out.kind)

    async def test_deadline_holds_when_driven_by_with_heartbeat(self):
        """生產的驅動方式：`_with_heartbeat` 每次 `__anext__` 開新 Task。

        跨 yield 的 `asyncio.timeout` 在這種驅動下第一個 yield 之後就失效；llm_http 的期限
        必須在這裡也準時生效。
        """
        from web import deps

        async def queued():
            # 有上限：期限機制若被改壞，測試要以斷言失敗收場，而不是卡住整個 pytest
            for _ in range(150):
                yield b": keep-alive\n\n"
                await asyncio.sleep(0.02)

        self.install(lambda req: httpx.Response(200, content=queued()))
        t0 = time.monotonic()
        items = []
        async for item in deps._with_heartbeat(self._stream(first_token_timeout=0.2), interval=0.05):
            items.append(item)
        self.assertLess(time.monotonic() - t0, 2.0)
        outcomes = [i for i in items if isinstance(i, lh.ChatOutcome)]
        self.assertEqual(len(outcomes), 1)
        self.assertEqual(outcomes[0].kind, lh.TIMEOUT)

    async def test_consumer_close_releases_response(self):
        closed = asyncio.Event()

        class Stream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield _sse(_chunk(content="a"), done=False)
                await asyncio.sleep(10)
                yield b""

            async def aclose(self):
                closed.set()

        self.install(lambda req: httpx.Response(200, stream=Stream()))
        agen = self._stream()
        first = await agen.__anext__()
        self.assertEqual(first, "a")
        await agen.aclose()
        self.assertTrue(closed.is_set(), "中途關閉產生器時必須釋放 HTTP 回應")

    async def test_cancel_releases_response(self):
        closed = asyncio.Event()
        started = asyncio.Event()

        class Stream(httpx.AsyncByteStream):
            async def __aiter__(self):
                started.set()
                await asyncio.sleep(10)
                yield b""

            async def aclose(self):
                closed.set()

        self.install(lambda req: httpx.Response(200, stream=Stream()))

        async def consume():
            async for _ in self._stream():
                pass

        task = asyncio.ensure_future(consume())
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(closed.is_set())


class EventLoopIsolationTests(_TransportMixin, unittest.TestCase):
    """批次的 asyncio.run 與測試每次都換 loop；全域 client 綁在舊 loop 上會出錯。"""

    def tearDown(self):
        self.uninstall()

    def test_two_loops_in_a_row(self):
        payload = _sse(_chunk(content="ok"), _chunk(content="", finish="stop"))
        self.install(lambda req: httpx.Response(200, content=payload))
        with mock.patch.dict(os.environ, ENV):
            for _ in range(2):
                texts, out = asyncio.run(_collect(lh.astream_chat(
                    "deepseek-flash", "q", max_tokens=16, first_token_timeout=5)))
                self.assertEqual(texts, ["ok"])
                self.assertIsNone(out.kind)


# ── 批次同步 ─────────────────────────────────────────────────────────────────
class CompleteChatTests(_TransportMixin, unittest.TestCase):
    def setUp(self):
        self._env = mock.patch.dict(os.environ, ENV)
        self._env.start()
        self.sleeps: list[float] = []

    def tearDown(self):
        self._env.stop()
        self.uninstall()

    def _call(self, **kw):
        kw.setdefault("max_tokens", 512)
        kw.setdefault("timeout", 30.0)
        kw.setdefault("sleep", self.sleeps.append)
        return lh.complete_chat("deepseek-flash", "標註這篇", **kw)

    def _seq(self, *responses):
        it = iter(responses)
        self.install(lambda req: next(it)())

    def test_success_joins_content(self):
        payload = _sse(
            _chunk(content='{"a"'), _chunk(content=": 1}"), _chunk(content="", finish="stop"),
            {"choices": [], "usage": USAGE},
        )
        self.install(lambda req: httpx.Response(200, content=payload))
        res = self._call(task="tag", system="系統", user_id="batch-tag", max_tokens=16384)
        self.assertEqual(res.text, '{"a": 1}')
        self.assertIsNone(res.error)
        self.assertEqual(res.attempts, 1)
        self.assertEqual(res.outcome.usage, USAGE)
        # 請求形狀：批次改壞 stream／system／max_tokens 時，這裡要紅
        req = self.requests[0]
        self.assertEqual(str(req.url), "https://api.example.test/chat/completions")
        self.assertEqual(req.headers["authorization"], f"Bearer {FAKE_KEY}")
        body = self.body()
        self.assertIs(body["stream"], True)
        self.assertEqual(body["stream_options"], {"include_usage": True})
        self.assertEqual(body["thinking"], {"type": "disabled"})
        self.assertEqual(body["reasoning_effort"], "none")
        self.assertEqual(body["max_tokens"], 16384)
        self.assertEqual(body["messages"][0], {"role": "system", "content": "系統"})
        self.assertEqual(body["user_id"], "batch-tag")

    def test_transient_retried_with_retry_after(self):
        ok = _sse(_chunk(content="好"), _chunk(content="", finish="stop"))
        self._seq(
            lambda: httpx.Response(429, json={"error": {"message": "Rate Limit"}}, headers={"Retry-After": "3"}),
            lambda: httpx.Response(200, content=ok),
        )
        res = self._call()
        self.assertEqual(res.text, "好")
        self.assertEqual(res.attempts, 2)
        self.assertEqual(self.sleeps, [3.0])

    def test_transient_exhausted(self):
        self._seq(*[lambda: httpx.Response(503, text="busy")] * 3)
        res = self._call()
        self.assertIsNone(res.text)
        self.assertTrue(res.error.startswith("API[overloaded]"))
        self.assertEqual(res.attempts, 3)
        self.assertEqual(len(self.sleeps), 2)

    def test_deterministic_failures_not_retried(self):
        """截斷、審查、空回應、400、402：重打只是再付一次錢，一律只打一次。"""
        cases = [
            (lambda: httpx.Response(400, json={"error": {"message": "Content Exists Risk"}}), "API[content_filter]"),
            (lambda: httpx.Response(200, content=_sse(_chunk(content="半"), _chunk(content="", finish="length"))),
             "API[truncated]"),
            (lambda: httpx.Response(200, content=_sse(_chunk(reasoning="r"), _chunk(content="", finish="stop"))),
             "API[empty]"),
            (lambda: httpx.Response(400, json={"error": {"message": "bad"}}), "API[bad_request]"),
            (lambda: httpx.Response(402, json={"error": {"message": "Insufficient Balance"}}), "API[quota]"),
        ]
        for make, prefix in cases:
            with self.subTest(prefix=prefix):
                self.install(lambda req, m=make: m())
                res = self._call()
                self.assertIsNone(res.text)
                self.assertTrue(res.error.startswith(prefix), res.error)
                self.assertEqual(len(self.requests), 1)

    def test_truncated_reports_max_tokens(self):
        payload = _sse(_chunk(content="半"), _chunk(content="", finish="length"))
        self.install(lambda req: httpx.Response(200, content=payload))
        res = self._call(max_tokens=16384)
        self.assertIn("max_tokens=16384", res.error)
        self.assertEqual(res.kind, lh.TRUNCATED)

    def test_missing_key_sends_nothing(self):
        self.install(lambda req: httpx.Response(200, content=_sse()))
        with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": "  "}):
            res = self._call()
        self.assertTrue(res.error.startswith("API[auth]"))
        self.assertEqual(self.requests, [])

    def test_total_deadline_under_keepalive(self):
        """排隊時的 keep-alive 會一直重置 read 逾時；總期限要靠逐行檢查 monotonic。"""

        def queued():
            for _ in range(150):  # 有上限，理由同上
                yield b": keep-alive\n\n"
                time.sleep(0.02)

        self.install(lambda req: httpx.Response(200, content=queued()))
        t0 = time.monotonic()
        res = self._call(timeout=0.3)
        self.assertLess(time.monotonic() - t0, 2.0)
        self.assertTrue(res.error.startswith("API[timeout]"))
        # 伺服器完全不送位元組時只能靠 read 逾時收手；它不得超過呼叫端給的總期限
        timeouts = self.requests[0].extensions["timeout"]
        for name in ("connect", "read", "write"):
            self.assertLessEqual(timeouts[name], 0.3, name)

    def test_backoff_never_sleeps_past_deadline(self):
        self._seq(*[lambda: httpx.Response(429, headers={"Retry-After": "50"})] * 3)
        res = self._call(timeout=5.0)
        self.assertEqual(self.sleeps, [])
        self.assertEqual(res.attempts, 1)
        self.assertTrue(res.error.startswith("API[overloaded]"))

    def test_error_strings_are_single_line(self):
        self.install(lambda req: httpx.Response(400, json={"error": {"message": "a\tb\nc"}}))
        res = self._call()
        self.assertNotIn("\t", res.error)
        self.assertNotIn("\n", res.error)


# ── 審查補強：串流中途、設定錯誤、分行、連線回收 ───────────────────────────────
class _AsyncStream(httpx.AsyncByteStream):
    """依序吐出 chunks；遇到例外物件就拋出（模擬串流中途斷線）。"""

    def __init__(self, *chunks, on_consumed=None):
        self.chunks = chunks
        self.on_consumed = on_consumed

    async def __aiter__(self):
        for c in self.chunks:
            if isinstance(c, BaseException):
                raise c
            yield c
        if self.on_consumed:
            self.on_consumed()


class _SyncStream(httpx.SyncByteStream):
    def __init__(self, *chunks):
        self.chunks = chunks

    def __iter__(self):
        for c in self.chunks:
            if isinstance(c, BaseException):
                raise c
            yield c


class LineSplitterTests(unittest.TestCase):
    def test_only_cr_lf_split_lines(self):
        """SSE 只以 CRLF／LF／CR 分行；U+2028 等在 JSON 裡可以不跳脫，不能拿來切。"""
        sp = lh._LineSplitter()
        self.assertEqual(sp.feed("a\u2028b\nc\u0085d\r\ne\u2029f\rg"), ["a\u2028b", "c\u0085d", "e\u2029f"])
        self.assertEqual(sp.flush(), ["g"])

    def test_crlf_split_across_chunks(self):
        sp = lh._LineSplitter()
        self.assertEqual(sp.feed("data: 1\r"), [])
        self.assertEqual(sp.feed("\ndata: 2\r\n"), ["data: 1", "data: 2"])
        self.assertEqual(sp.flush(), [])


class StreamEdgeTests(_TransportMixin, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._env = mock.patch.dict(os.environ, ENV)
        self._env.start()

    def tearDown(self):
        self._env.stop()
        self.uninstall()

    def _stream(self, **kw):
        kw.setdefault("max_tokens", 256)
        kw.setdefault("first_token_timeout", 5.0)
        return lh.astream_chat("deepseek-flash", "問題", **kw)

    async def test_unicode_line_separator_inside_content(self):
        payload = _sse(_chunk(content="甲\u2028乙"), _chunk(content="", finish="stop"))
        self.install(lambda req: httpx.Response(200, content=payload))
        texts, out = await _collect(self._stream())
        self.assertEqual("".join(texts), "甲\u2028乙")
        self.assertIsNone(out.kind)

    async def test_bad_finish_reasons_after_text(self):
        """已吐字後才出現的失敗結局，不得落到「成功」：半截回答會被當成完整答案。"""
        for finish, kind in (("insufficient_system_resource", lh.OVERLOADED), ("aborted", lh.OTHER)):
            with self.subTest(finish=finish):
                payload = _sse(_chunk(content="一半"), _chunk(content="", finish=finish))
                self.install(lambda req, p=payload: httpx.Response(200, content=p))
                texts, out = await _collect(self._stream())
                self.assertEqual(texts, ["一半"])
                self.assertEqual(out.kind, kind)
                self.assertTrue(out.streamed)

    async def test_error_object_after_text(self):
        payload = _sse(_chunk(content="一半"), {"error": {"message": "server error"}})
        self.install(lambda req: httpx.Response(200, content=payload))
        texts, out = await _collect(self._stream())
        self.assertEqual(texts, ["一半"])
        self.assertEqual(out.kind, lh.OVERLOADED)
        self.assertTrue(out.streamed)

    async def test_disconnect_after_finish_reason_is_success(self):
        """finish_reason 已到才斷線：答案完整，不得判成網路錯誤（批次會因此重打、丟掉答案）。"""
        stream = _AsyncStream(
            _sse(_chunk(content="完整答案"), _chunk(content="", finish="stop"), done=False),
            httpx.RemoteProtocolError("incomplete chunked read"),
        )
        self.install(lambda req: httpx.Response(200, stream=stream))
        texts, out = await _collect(self._stream())
        self.assertEqual(texts, ["完整答案"])
        self.assertIsNone(out.kind)

    async def test_disconnect_before_finish_is_network(self):
        stream = _AsyncStream(_sse(_chunk(content="一半"), done=False), httpx.ReadError("reset"))
        self.install(lambda req: httpx.Response(200, stream=stream))
        texts, out = await _collect(self._stream())
        self.assertEqual(texts, ["一半"])
        self.assertEqual(out.kind, lh.NETWORK)
        self.assertTrue(out.streamed)

    async def test_read_timeout_before_first_token_is_timeout(self):
        """首字前伺服器沉默（httpx read 逾時）歸 TIMEOUT：外層不重試（見模組 docstring）。
        標頭之前與標頭之後、首字之前兩個時點都一樣；首字之後的 read 逾時仍是 NETWORK（截斷）。"""

        def silent_headers(req):
            raise httpx.ReadTimeout("no bytes", request=req)

        for name, handler in (
            ("send", silent_headers),
            ("body", lambda req: httpx.Response(
                200, stream=_AsyncStream(b": keep-alive\n\n", httpx.ReadTimeout("no bytes")))),
        ):
            with self.subTest(at=name):
                self.install(handler)
                texts, out = await _collect(self._stream())
                self.assertEqual(texts, [])
                self.assertEqual(out.kind, lh.TIMEOUT)
                self.assertFalse(out.streamed)
                self.assertIn("ReadTimeout", out.detail)
        self.install(lambda req: httpx.Response(
            200, stream=_AsyncStream(_sse(_chunk(content="一半"), done=False), httpx.ReadTimeout("no bytes"))))
        texts, out = await _collect(self._stream())
        self.assertEqual((texts, out.kind, out.streamed), (["一半"], lh.NETWORK, True))

    async def test_total_timeout_after_text(self):
        """吐字後的牆鐘總時限：到期＝TIMEOUT 且 streamed（呼叫端當截斷）；None＝不設。"""
        async def drip():
            for i in range(100):
                yield _sse(_chunk(content=f"{i}"), done=False)
                await asyncio.sleep(0.02)
            yield _sse(_chunk(content="", finish="stop"))

        self.install(lambda req: httpx.Response(200, content=drip()))
        t0 = time.monotonic()
        texts, out = await _collect(self._stream(total_timeout=0.3))
        self.assertLess(time.monotonic() - t0, 1.5)
        self.assertTrue(0 < len(texts) < 100)
        self.assertEqual(out.kind, lh.TIMEOUT)
        self.assertTrue(out.streamed)
        self.assertIn("總時限", out.detail)

    async def test_total_timeout_after_finish_reason_keeps_success(self):
        """finish_reason 已到、只差 [DONE] 時到期：答案完整，以 finish_reason 為準。"""
        async def finished_then_hang():
            yield _sse(_chunk(content="完整"), _chunk(content="", finish="stop"), done=False)
            await asyncio.sleep(5)
            yield b""

        self.install(lambda req: httpx.Response(200, content=finished_then_hang()))
        texts, out = await _collect(self._stream(total_timeout=0.2))
        self.assertEqual(texts, ["完整"])
        self.assertIsNone(out.kind)

    async def test_total_timeout_shorter_than_first_token_bounds_first_token(self):
        async def queued():
            for _ in range(100):
                yield b": keep-alive\n\n"
                await asyncio.sleep(0.02)

        self.install(lambda req: httpx.Response(200, content=queued()))
        t0 = time.monotonic()
        texts, out = await _collect(self._stream(first_token_timeout=5.0, total_timeout=0.2))
        self.assertLess(time.monotonic() - t0, 1.5)
        self.assertEqual((texts, out.kind, out.streamed), ([], lh.TIMEOUT, False))

    async def test_first_token_deadline_covers_send(self):
        """TLS 或代理卡在標頭之前：也要受首字期限約束，不是等 connect／read 逾時。"""

        async def hang(req):
            await asyncio.sleep(10)
            return httpx.Response(200, content=_sse())

        self.install(hang)
        t0 = time.monotonic()
        _, out = await _collect(self._stream(first_token_timeout=0.2))
        self.assertLess(time.monotonic() - t0, 2.0)
        self.assertEqual(out.kind, lh.TIMEOUT)

    async def test_first_token_deadline_covers_error_body(self):
        class Slow(httpx.AsyncByteStream):
            async def __aiter__(self):
                await asyncio.sleep(10)
                yield b"{}"

        self.install(lambda req: httpx.Response(503, stream=Slow()))
        t0 = time.monotonic()
        _, out = await _collect(self._stream(first_token_timeout=0.2))
        self.assertLess(time.monotonic() - t0, 2.0)
        self.assertEqual(out.kind, lh.OVERLOADED)
        self.assertEqual(out.status, 503)

    async def test_drains_after_done_so_connection_can_be_reused(self):
        """讀到 [DONE] 後要把結尾讀完：否則 h11 狀態不是 DONE，httpcore 直接關連線、不回池。"""
        consumed = []
        stream = _AsyncStream(
            _sse(_chunk(content="好"), _chunk(content="", finish="stop")),
            b"\n",
            on_consumed=lambda: consumed.append(True),
        )
        self.install(lambda req: httpx.Response(200, stream=stream))
        _, out = await _collect(self._stream())
        self.assertIsNone(out.kind)
        self.assertEqual(consumed, [True])

    async def test_config_errors_become_outcomes(self):
        """設定錯誤一律回帳號層級的 outcome、不往外拋，也不送出請求。"""
        cases = [
            ({"DEEPSEEK_API_KEY": "\u201c" + FAKE_KEY + "\u201d"}, lh.AUTH),
            ({"DEEPSEEK_API_KEY": FAKE_KEY + "\u200b"}, lh.AUTH),
            ({"DEEPSEEK_BASE_URL": "api.deepseek.com"}, lh.CONFIG),
            ({"DEEPSEEK_BASE_URL": "https://api.deepseek.com:abc"}, lh.CONFIG),
        ]
        for env, kind in cases:
            with self.subTest(env=env):
                self.install(lambda req: httpx.Response(200, content=_sse()))
                with mock.patch.dict(os.environ, env):
                    texts, out = await _collect(self._stream())
                self.assertEqual(out.kind, kind)
                self.assertIn(kind, lh.ACCOUNT_KINDS)
                self.assertEqual(self.requests, [])

    async def test_call_log_has_no_key_and_no_prompt(self):
        payload = _sse(_chunk(content="好"), _chunk(content="", finish="stop"), {"choices": [], "usage": USAGE})
        self.install(lambda req: httpx.Response(200, content=payload))
        with self.assertLogs("app.services.llm_http", "INFO") as logs:
            await _collect(lh.astream_chat(
                "deepseek-flash", "機密提問內容", max_tokens=16, first_token_timeout=5, task="ask"))
        text = "\n".join(logs.output)
        self.assertIn("llm_call task=ask", text)
        self.assertIn("kind=ok", text)
        self.assertIn("miss=12", text)
        self.assertNotIn(FAKE_KEY, text)
        self.assertNotIn("機密提問內容", text)


class StaleLoopTests(_TransportMixin, unittest.TestCase):
    def tearDown(self):
        self.uninstall()

    def test_closed_loops_are_purged(self):
        """池裡的連線經 transport 強引用 loop，WeakKeyDictionary 自己回收不了。"""
        payload = _sse(_chunk(content="ok"), _chunk(content="", finish="stop"))
        self.install(lambda req: httpx.Response(200, content=payload))
        kept = []

        async def one():
            kept.append(asyncio.get_running_loop())  # 模擬「還被引用」的舊 loop
            await _collect(lh.astream_chat("deepseek-flash", "q", max_tokens=16, first_token_timeout=5))

        with mock.patch.dict(os.environ, ENV):
            asyncio.run(one())
            asyncio.run(one())
        self.assertTrue(kept[0].is_closed())
        self.assertNotIn(kept[0], list(lh._ASYNC_CLIENTS.keys()))


class CompleteChatEdgeTests(_TransportMixin, unittest.TestCase):
    def setUp(self):
        self._env = mock.patch.dict(os.environ, ENV)
        self._env.start()
        self.sleeps: list[float] = []

    def tearDown(self):
        self._env.stop()
        self.uninstall()

    def _call(self, **kw):
        kw.setdefault("max_tokens", 512)
        kw.setdefault("timeout", 30.0)
        kw.setdefault("sleep", self.sleeps.append)
        return lh.complete_chat("deepseek-flash", "標註這篇", **kw)

    def test_connect_errors_are_retried_as_network(self):
        for exc in (httpx.ConnectError("refused"), httpx.ConnectTimeout("slow")):
            with self.subTest(exc=type(exc).__name__):
                self.sleeps.clear()

                def boom(req, e=exc):
                    raise e

                self.install(boom)
                res = self._call()
                self.assertIsNone(res.text)
                self.assertTrue(res.error.startswith("API[network]"), res.error)
                self.assertEqual(res.attempts, 3)
                self.assertEqual(len(self.sleeps), 2)

    def test_read_timeout_with_budget_left_is_network(self):
        """60 秒完全沉默但總期限還很充裕：是連線問題，要重試，不是「期限到了」。"""

        def silent(req):
            raise httpx.ReadTimeout("no bytes")

        self.install(silent)
        res = self._call(timeout=600)
        self.assertTrue(res.error.startswith("API[network]"), res.error)
        self.assertEqual(res.attempts, 3)

    def test_no_partial_success(self):
        """批次沒有部分成功：中途斷線、或沒有結束訊號，已收到的半段都不得當成功回傳。"""
        cases = [
            lambda: httpx.Response(200, stream=_SyncStream(
                _sse(_chunk(content='{"半'), done=False), httpx.ReadError("reset"))),
            lambda: httpx.Response(200, content=_sse(_chunk(content='{"半'), done=False)),
        ]
        for make in cases:
            with self.subTest():
                self.install(lambda req, m=make: m())
                res = self._call()
                self.assertIsNone(res.text)
                self.assertTrue(res.error.startswith("API[network]"), res.error)

    def test_disconnect_after_finish_reason_is_success(self):
        self.install(lambda req: httpx.Response(200, stream=_SyncStream(
            _sse(_chunk(content="完整"), _chunk(content="", finish="stop"), done=False),
            httpx.RemoteProtocolError("incomplete chunked read"),
        )))
        res = self._call()
        self.assertEqual(res.text, "完整")
        self.assertEqual(res.attempts, 1)

    def test_bad_finish_reasons_after_text(self):
        for finish, prefix in (("insufficient_system_resource", "API[overloaded]"), ("aborted", "API[other]")):
            with self.subTest(finish=finish):
                payload = _sse(_chunk(content="半"), _chunk(content="", finish=finish))
                self.install(lambda req, p=payload: httpx.Response(200, content=p))
                res = self._call()
                self.assertIsNone(res.text)
                self.assertTrue(res.error.startswith(prefix), res.error)

    def test_unicode_line_separator_inside_content(self):
        payload = _sse(_chunk(content='{"s": "甲\u2028乙"}'), _chunk(content="", finish="stop"))
        self.install(lambda req: httpx.Response(200, content=payload))
        res = self._call()
        self.assertEqual(res.text, '{"s": "甲\u2028乙"}')

    def test_account_errors_single_request(self):
        for status in (401, 404):
            with self.subTest(status=status):
                self.install(lambda req, s=status: httpx.Response(s, json={"error": {"message": "x"}}))
                res = self._call()
                self.assertEqual(len(self.requests), 1)
                self.assertIn(res.kind, lh.ACCOUNT_KINDS)

    def test_config_errors_become_results(self):
        for env, prefix in (({"DEEPSEEK_API_KEY": FAKE_KEY + "\u200b"}, "API[auth]"),
                            ({"DEEPSEEK_BASE_URL": "api.deepseek.com"}, "API[config]")):
            with self.subTest(env=env):
                self.install(lambda req: httpx.Response(200, content=_sse()))
                with mock.patch.dict(os.environ, env):
                    res = self._call()
                self.assertTrue(res.error.startswith(prefix), res.error)
                self.assertEqual(self.requests, [])

    def test_retry_after_is_capped(self):
        ok = _sse(_chunk(content="好"), _chunk(content="", finish="stop"))
        seq = iter([httpx.Response(429, headers={"Retry-After": "3600"}), httpx.Response(200, content=ok)])
        self.install(lambda req: next(seq))
        res = self._call(timeout=7200)
        self.assertEqual(res.text, "好")
        self.assertEqual(self.sleeps, [60.0])

    def test_default_backoff_when_no_or_unparseable_retry_after(self):
        """沒有 Retry-After，或它是 HTTP-date 形式時：走 2／6 秒加抖動，不得拋例外或不睡就連打。"""
        for headers in ({}, {"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}):
            with self.subTest(headers=headers):
                self.sleeps.clear()
                self.install(lambda req, h=headers: httpx.Response(503, headers=h))
                res = self._call()
                self.assertEqual(res.attempts, 3)
                self.assertEqual(len(self.sleeps), 2)
                self.assertTrue(1.6 <= self.sleeps[0] <= 2.4, self.sleeps)
                self.assertTrue(4.8 <= self.sleeps[1] <= 7.2, self.sleeps)


# ── 依賴方向與測試防線 ───────────────────────────────────────────────────────
class LeafModuleTests(unittest.TestCase):
    def test_imports_only_stdlib_and_httpx(self):
        """葉模組：不得 import app.*／web.*／scripts.*，唯一例外是同為葉模組的 llm_models。

        `query_planner.py` 開頭的依賴約束與 `retrieval_pipeline`↔`answer` 的刻意循環都經過
        llm 層；讓這裡往回 import 任何專案模組，就可能在 import 期形成新的循環。llm_models
        本身只 import 標準函式庫（tests/test_llm_models.py 釘住），所以不會把循環帶進來。
        """
        tree = ast.parse((REPO_ROOT / "app" / "services" / "llm_http.py").read_text(encoding="utf-8"))
        roots = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.level > 0:
                    roots.add("app")  # 相對 import 必然是專案內模組
                elif node.module == "app.services.llm_models":
                    continue
                elif node.module:
                    roots.add(node.module.split(".")[0])
        project = {"app", "web", "scripts", "eval", "tests"}
        self.assertEqual(roots & project, set())
        third_party = roots - set(sys.stdlib_module_names) - {"__future__"}
        self.assertEqual(third_party, {"httpx"})


class ConftestGuardTests(unittest.TestCase):
    """conftest 以**賦值**（不是 setdefault）佔住金鑰與端點。

    repo 根就是部署目錄：`web/server.py`、`web/deps.py` 在 import 期把真的 `.env` 灌進
    os.environ，而 `load_env_file` 只補「還不存在」的鍵。先佔位，真金鑰就進不來。
    """

    def test_key_and_endpoint_are_neutralized(self):
        self.assertEqual(os.environ.get("DEEPSEEK_API_KEY"), "")
        self.assertEqual(os.environ.get("DEEPSEEK_BASE_URL"), "http://127.0.0.1:9")

    def test_real_env_file_cannot_override(self):
        from web.env_loader import load_env_file

        # 包在 patch.dict 裡：萬一中和機制退化，被載入的假金鑰與官方端點不會外溢到其他測試
        with mock.patch.dict(os.environ), tempfile.TemporaryDirectory() as d:
            # 檔名刻意不叫 `.env`：tests/test_env_loading.py 的 AST 守門依檔名判定寫入目標
            probe = Path(d) / "probe.env"
            probe.write_text(
                "DEEPSEEK_API_KEY=fixed-test-secret-leaked0\n"
                "DEEPSEEK_BASE_URL=https://api.deepseek.com\n",
                encoding="utf-8",
            )
            load_env_file(probe)
            self.assertEqual(os.environ.get("DEEPSEEK_API_KEY"), "")
            self.assertEqual(os.environ.get("DEEPSEEK_BASE_URL"), "http://127.0.0.1:9")

    def test_conftest_assigns_rather_than_setdefault(self):
        """靜態守門：CI 的 runner 環境裡本來就沒有金鑰，改回 setdefault 時上面兩條照樣綠。

        賦值與 setdefault 的差別只在「執行者的環境裡已經有真金鑰」（shell 已 export、或先
        載入過部署環境檔）——那正是要防的情況，所以直接釘住寫法。
        """
        tree = ast.parse((REPO_ROOT / "tests" / "conftest.py").read_text(encoding="utf-8"))
        assigned = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if (
                        isinstance(target, ast.Subscript)
                        and isinstance(target.value, ast.Attribute)
                        and target.value.attr == "environ"
                        and isinstance(target.slice, ast.Constant)
                    ):
                        assigned.add(target.slice.value)
        self.assertLessEqual({"DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL"}, assigned)


if __name__ == "__main__":
    unittest.main()
