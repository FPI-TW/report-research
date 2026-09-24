import json
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services import llm  # noqa: E402


class LLMUnavailableErrorTests(unittest.TestCase):
    def test_reason_defaults_to_none_for_direct_construction(self):
        self.assertIsNone(llm.LLMUnavailableError("529").reason)

    def test_kind_and_partial_defaults(self):
        """kind 預設 other（未分類）、partial 預設 False。"""
        exc = llm.LLMUnavailableError("529")
        self.assertEqual(exc.kind, "other")
        self.assertIs(exc.partial, False)
        exc = llm.LLMUnavailableError("x", kind="quota", partial=True, reason="api_error")
        self.assertEqual((exc.kind, exc.partial, exc.reason), ("quota", True, "api_error"))

    def test_auth_kind_reaches_answer_and_ask_error_detail(self):
        from app.services import answer
        from web.routers import ask

        exc = llm.LLMUnavailableError("401", reason=llm.UNAVAILABLE_API_ERROR, kind="auth")
        self.assertEqual(answer._llm_error_kind(exc), "auth")
        self.assertIn("帳號異常", ask._llm_error_detail(exc))

    def test_config_kind_reaches_answer_and_ask_error_detail(self):
        """白名單外的 model／網搜的 config 錯誤：落庫 llm_error=config、使用者看到「設定有誤」（不是帳號異常）。"""
        from app.services import answer
        from web.routers import ask

        exc = llm.LLMUnavailableError("x", reason=llm.UNAVAILABLE_API_ERROR, kind="config")
        self.assertEqual(answer._llm_error_kind(exc), "config")
        self.assertIn("模型設定有誤", ask._llm_error_detail(exc))
        self.assertNotIn("帳號", ask._llm_error_detail(exc))


# ── 白名單分派：HTTP 路徑（DeepSeek）────────────────────────────────────────
# 全程不連網：`llm_http._transport` 注入 httpx.MockTransport；金鑰是 gitleaks allowlist 內的假值，
# 以 mock.patch.dict 限定在單一測試內（conftest 把 DEEPSEEK_API_KEY 強制成空字串）。
import ast  # noqa: E402
import asyncio  # noqa: E402
import os  # noqa: E402
import time  # noqa: E402
from unittest import mock  # noqa: E402

import httpx  # noqa: E402

from app.services import llm_http as lh  # noqa: E402

_FAKE_KEY = "fixed-test-secret-deepseek0"
_ENV = {"DEEPSEEK_API_KEY": _FAKE_KEY, "DEEPSEEK_BASE_URL": "https://api.example.test"}


def _sse(*events, done: bool = True) -> bytes:
    out = []
    for ev in events:
        out.append(ev if isinstance(ev, str) else "data: " + json.dumps(ev, ensure_ascii=False))
        out.append("")
    if done:
        out += ["data: [DONE]", ""]
    return ("\n".join(out) + "\n").encode("utf-8")


def _chunk(content=None, finish=None):
    delta = {} if content is None else {"content": content}
    return {"model": "deepseek-v4.1-flash", "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}


def _ok(*texts) -> bytes:
    return _sse(*[_chunk(t) for t in texts], _chunk("", finish="stop"))


class _HttpCase(unittest.IsolatedAsyncioTestCase):
    """裝好 MockTransport 與假金鑰；記下每個請求與外層重試的等待秒數。"""

    patch_sleep = True  # 外層重試的 asyncio.sleep 換成只記秒數（不真的等）

    def setUp(self):
        self._env = mock.patch.dict(os.environ, _ENV)
        self._env.start()
        self.requests: list[httpx.Request] = []
        self.sleeps: list[float] = []
        async def fake_sleep(delay):
            self.sleeps.append(delay)

        self._sleep = mock.patch.object(llm, "_retry_sleep", fake_sleep) if self.patch_sleep else None
        if self._sleep is not None:
            self._sleep.start()

    def tearDown(self):
        if self._sleep is not None:
            self._sleep.stop()
        self._env.stop()
        lh._transport = None
        lh._reset_clients()

    def install(self, handler):
        def recording(request):
            self.requests.append(request)
            return handler(request)

        lh._transport = httpx.MockTransport(recording)
        lh._reset_clients()

    def install_seq(self, *responses):
        it = iter(responses)
        self.install(lambda req: next(it))

    async def collect(self, **kw):
        kw.setdefault("model", "deepseek-flash")
        kw.setdefault("max_tokens", 256)
        kw.setdefault("task", "ask_answer")
        return [c async for c in llm.stream_completion("問題", **kw)]


class HttpInputSanitizeTests(_HttpCase):
    """線上 HTTP 路徑同樣清掉 NUL／孤立代理字元（prompt 與 system 都要）；清不到的編碼錯誤是 bad_request。"""

    async def test_prompt_and_system_are_sanitized(self):
        self.install(lambda req: httpx.Response(200, content=_ok("好")))
        chunks = [c async for c in llm.stream_completion(
            "問\x00題\ud800", model="deepseek-flash", system="系\x00統", max_tokens=64, task="ask_answer")]
        self.assertEqual(chunks, ["好"])
        self.assertNotIn(b"\x00", self.requests[0].content)
        body = json.loads(self.requests[0].content)
        self.assertEqual(body["messages"], [{"role": "system", "content": "系統"},
                                            {"role": "user", "content": "問題\ufffd"}])

    async def test_unicode_error_is_bad_request_not_config(self):
        self.install(lambda req: httpx.Response(200, content=_ok("好")))
        with mock.patch.object(lh, "sanitize", lambda text: text):
            with self.assertRaises(llm.LLMUnavailableError) as cm:
                await self.collect(system="孤立\ud800代理")  # 繞過 sanitize：編碼時才失敗
        self.assertEqual(cm.exception.kind, lh.BAD_REQUEST)
        self.assertEqual(self.requests, [])


class HttpDispatchTests(_HttpCase):
    async def test_whitelisted_model_goes_http(self):
        self.install(lambda req: httpx.Response(200, content=_ok("台積電", "展望")))
        meta: dict = {}
        chunks = await self.collect(system="系統", meta=meta, max_tokens=4096, task="ask_overview")
        self.assertEqual(chunks, ["台積電", "展望"])
        self.assertIs(meta["truncated"], False)
        body = json.loads(self.requests[0].content)
        self.assertEqual(body["model"], "deepseek-flash")
        self.assertEqual(body["max_tokens"], 4096)
        self.assertEqual(body["user_id"], "web-ask_overview")
        self.assertEqual(body["messages"][0], {"role": "system", "content": "系統"})

    async def test_every_whitelisted_name_goes_http(self):
        for name in sorted(llm.HTTP_MODELS if hasattr(llm, "HTTP_MODELS") else lh.HTTP_MODELS):
            with self.subTest(name=name):
                self.requests.clear()
                self.install(lambda req: httpx.Response(200, content=_ok("x")))
                self.assertEqual(await self.collect(model=name), ["x"])
                self.assertEqual(json.loads(self.requests[0].content)["model"], name)


    async def test_missing_max_tokens_falls_back_with_warning(self):
        self.install(lambda req: httpx.Response(200, content=_ok("x")))
        with self.assertLogs("app.services.llm", "WARNING") as logs:
            await self.collect(max_tokens=None)
        self.assertIn("未給 max_tokens", "\n".join(logs.output))
        self.assertEqual(json.loads(self.requests[0].content)["max_tokens"], llm._HTTP_FALLBACK_MAX_TOKENS)

    async def test_call_log_line_has_task_and_model(self):
        self.install(lambda req: httpx.Response(200, content=_ok("好")))
        with self.assertLogs("app.services.llm_http", "INFO") as logs:
            await self.collect(task="ask_followup", max_tokens=512)
        text = "\n".join(logs.output)
        self.assertIn("llm_call task=ask_followup model=deepseek-flash", text)
        self.assertIn("backend=http", text)
        self.assertNotIn(_FAKE_KEY, text)


class NonWhitelistIsConfigErrorTests(_HttpCase):
    """PR-M：白名單外的 model（含 claude-*、CLI 別名、打錯字、空字串）一律 config 錯誤——不送 HTTP、
    不 spawn 任何子行程、一個字都不 yield。"""

    NAMES = ("claude-sonnet-5", "claude-haiku-4-5", "claude-haiku-4-5-20251001", "sonnet", "haiku", "",
             "deepseek-flsh", "DeepSeek-Flash", " deepseek-flash")

    def setUp(self):
        super().setUp()
        self.install(lambda req: httpx.Response(200, content=_ok("不該送出")))

        def no_spawn(*a, **k):
            raise AssertionError("PR-M 後不得 spawn 任何子行程")

        self._spawn = mock.patch.object(asyncio, "create_subprocess_exec", no_spawn)
        self._spawn.start()

    def tearDown(self):
        self._spawn.stop()
        super().tearDown()

    async def _assert_config(self, **kw):
        got: list = []
        with self.assertRaises(llm.LLMUnavailableError) as cm:
            async for c in llm.stream_completion("問題", max_tokens=16, task="ask_intent", **kw):
                got.append(c)
        self.assertEqual(got, [], "送出前就該失敗，不得 yield")
        self.assertEqual(cm.exception.kind, "config")
        self.assertIs(cm.exception.partial, False)
        self.assertEqual(cm.exception.reason, llm.UNAVAILABLE_API_ERROR)
        self.assertEqual(self.requests, [], "不得送到付費端點")
        return cm.exception

    async def test_non_whitelisted_names(self):
        for name in self.NAMES:
            with self.subTest(name=name):
                exc = await self._assert_config(model=name)
                self.assertIn("白名單", str(exc))
                self.assertIn(repr(name), str(exc))

    async def test_near_miss_names_are_not_http(self):
        for name in ("deepseek-flsh", "sonnet", "DeepSeek-Flash"):
            with self.subTest(name=name):
                self.assertFalse(llm.is_http_model(name))

    async def test_web_search_is_config_error_for_any_model(self):
        """網搜沒有後端：不論 model（白名單、claude-*、空字串＝ASK_WEB_MODEL 的預設）一律 config。"""
        for name in ("deepseek-flash", "claude-sonnet-5", ""):
            with self.subTest(name=name):
                exc = await self._assert_config(model=name, allow_web=True)
                self.assertIn("網搜", str(exc))

    async def test_default_model_is_whitelisted(self):
        """conftest 強制 deepseek：主答預設是白名單名稱（否則每個走預設的呼叫點都會 config 失敗）。"""
        self.assertTrue(llm.is_http_model(llm.DEFAULT_MODEL))


class NoCliBackendTests(unittest.TestCase):
    """PR-M：線上與評測路徑不得殘留 claude CLI backend（驗收 grep 的測試版；批次的在 test_claude_cli.py）。"""

    GONE = ("_build_cmd", "_run_attempt", "claude_cli_path", "CLAUDE_BIN", "check_init_tools",
            "extract_text_delta", "looks_like_api_error", "_STDOUT_LINE_LIMIT")
    PATTERNS = ("claude -p", "claude_cli_path", "_run_cli", "build_cli_args", "create_subprocess_exec")

    def test_llm_module_has_no_cli_symbols(self):
        for name in self.GONE:
            with self.subTest(name=name):
                self.assertFalse(hasattr(llm, name), name)

    def test_online_sources_have_no_cli_spawn(self):
        hits = []
        for base in ("app", "web", "eval"):
            for path in sorted((REPO_ROOT / base).rglob("*.py")):
                text = path.read_text(encoding="utf-8")
                for pat in self.PATTERNS:
                    if pat in text:
                        hits.append(f"{path.relative_to(REPO_ROOT)}: {pat}")
        self.assertEqual(hits, [])


class HttpRetryTests(_HttpCase):
    async def test_429_then_200_retries_honoring_retry_after(self):
        self.install_seq(
            httpx.Response(429, json={"error": {"message": "Rate Limit"}}, headers={"Retry-After": "2"}),
            httpx.Response(200, content=_ok("成功")),
        )
        self.assertEqual(await self.collect(), ["成功"])
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(self.sleeps, [2.0])

    async def test_retry_after_is_capped_at_ten_seconds(self):
        self.install_seq(
            httpx.Response(503, headers={"Retry-After": "120"}),
            httpx.Response(200, content=_ok("ok")),
        )
        await self.collect()
        self.assertEqual(self.sleeps, [10.0])

    async def test_default_backoff_then_gives_up(self):
        self.install(lambda req: httpx.Response(500, json={"error": {"message": "boom"}}))
        with self.assertRaises(llm.LLMUnavailableError) as cm:
            await self.collect(retries=2)
        self.assertEqual(len(self.requests), 3)
        self.assertEqual(self.sleeps, [1.5, 3.0])
        self.assertEqual(cm.exception.kind, "overloaded")
        self.assertEqual(cm.exception.reason, llm.UNAVAILABLE_API_ERROR)
        self.assertIs(cm.exception.partial, False)

    async def test_network_error_is_retried(self):
        calls = []

        def handler(req):
            calls.append(req)
            if len(calls) == 1:
                raise httpx.ConnectError("refused", request=req)
            return httpx.Response(200, content=_ok("ok"))

        self.install(handler)
        self.assertEqual(await self.collect(), ["ok"])
        self.assertEqual(len(self.requests), 2)

    async def test_silent_server_before_first_token_is_timeout_not_retried(self):
        """首字前伺服器 60 秒沒送任何位元組（httpx ReadTimeout）＝逾時，與首字期限到了同一件事，
        不重試。歸 network 的話主答最壞要 3×60 秒加退避才失敗（與「首字逾時不重試」矛盾）。
        兩個時點都要：等標頭（`client.send`）與標頭之後、首字之前（讀 body）。"""

        class SilentBody(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b": keep-alive\n\n"
                raise httpx.ReadTimeout("read timed out")

        def silent_headers(req):
            raise httpx.ReadTimeout("read timed out", request=req)

        for name, handler in (
            ("send", silent_headers),
            ("body", lambda req: httpx.Response(200, stream=SilentBody())),
        ):
            with self.subTest(at=name):
                self.requests.clear()
                self.sleeps.clear()
                self.install(handler)
                with self.assertRaises(llm.LLMUnavailableError) as cm:
                    await self.collect(retries=2)
                self.assertEqual(len(self.requests), 1, "首字前沉默不重試")
                self.assertEqual(self.sleeps, [])
                self.assertEqual((cm.exception.kind, cm.exception.reason), ("timeout", llm.UNAVAILABLE_TIMEOUT))
                self.assertIs(cm.exception.partial, False)

    async def test_connect_timeout_is_still_retried_as_network(self):
        """只有 read 逾時改判：連線逾時（connect=10 秒）照樣是暫時性網路錯誤。"""
        calls = []

        def handler(req):
            calls.append(req)
            if len(calls) == 1:
                raise httpx.ConnectTimeout("slow", request=req)
            return httpx.Response(200, content=_ok("ok"))

        self.install(handler)
        self.assertEqual(await self.collect(), ["ok"])
        self.assertEqual(len(self.requests), 2)

    async def test_account_and_input_errors_are_not_retried(self):
        cases = [
            (402, {"error": {"message": "Insufficient Balance"}}, "quota"),
            (401, {"error": {"message": "Authentication Fails"}}, "auth"),
            (400, {"error": {"message": "Content Exists Risk"}}, "content_filter"),
            (400, {"error": {"message": "bad param"}}, "bad_request"),
        ]
        for status, payload, kind in cases:
            with self.subTest(status=status, kind=kind):
                self.requests.clear()
                self.sleeps.clear()
                self.install(lambda req, s=status, p=payload: httpx.Response(s, json=p))
                with self.assertRaises(llm.LLMUnavailableError) as cm:
                    await self.collect(retries=2)
                self.assertEqual(len(self.requests), 1)
                self.assertEqual(self.sleeps, [])
                self.assertEqual(cm.exception.kind, kind)
                self.assertIs(cm.exception.partial, False)

    async def test_no_retry_once_text_was_streamed(self):
        """已吐字就不重試：重打會讓畫面上出現兩份答案。"""
        self.install(lambda req: httpx.Response(200, content=_sse(_chunk("開頭"), done=False)))
        meta: dict = {}
        chunks = await self.collect(retries=2, meta=meta)
        self.assertEqual(chunks, ["開頭"])
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(meta, {"truncated": True, "truncated_reason": llm.TRUNCATED_NETWORK})

    async def test_empty_response_reason(self):
        self.install(lambda req: httpx.Response(200, content=_sse(_chunk("", finish="stop"))))
        with self.assertRaises(llm.LLMUnavailableError) as cm:
            await self.collect()
        self.assertEqual((cm.exception.kind, cm.exception.reason), ("empty", llm.UNAVAILABLE_EMPTY))
        self.assertEqual(len(self.requests), 1)

    async def test_unexpected_exception_is_wrapped(self):
        async def boom(*a, **k):
            raise RuntimeError("程式錯誤")
            yield  # pragma: no cover

        with mock.patch.object(lh, "astream_chat", boom):
            with self.assertRaises(llm.LLMUnavailableError) as cm:
                await self.collect()
        self.assertEqual(cm.exception.kind, "other")
        self.assertIs(cm.exception.partial, False)
        self.assertIsInstance(cm.exception.__cause__, RuntimeError)

    async def test_unexpected_exception_after_text_is_partial(self):
        async def half(*a, **k):
            yield "一半"
            raise RuntimeError("程式錯誤")

        got: list[str] = []
        with mock.patch.object(lh, "astream_chat", half):
            with self.assertRaises(llm.LLMUnavailableError) as cm:
                async for c in llm.stream_completion("q", model="deepseek-flash", max_tokens=16, task="t"):
                    got.append(c)
        self.assertEqual(got, ["一半"])
        self.assertIs(cm.exception.partial, True)


class HttpPartialTests(_HttpCase):
    async def test_content_filter_after_text_raises_partial(self):
        self.install(lambda req: httpx.Response(
            200, content=_sse(_chunk("部分答案"), _chunk("", finish="content_filter"))))
        got: list[str] = []
        with self.assertRaises(llm.LLMUnavailableError) as cm:
            async for c in llm.stream_completion("q", model="deepseek-flash", max_tokens=16, task="t"):
                got.append(c)
        self.assertEqual(got, ["部分答案"], "已吐的字要先送到呼叫端，例外才在最後拋")
        self.assertEqual(cm.exception.kind, "content_filter")
        self.assertIs(cm.exception.partial, True)
        self.assertEqual(len(self.requests), 1)

    async def test_content_filter_before_text_is_not_partial(self):
        self.install(lambda req: httpx.Response(200, content=_sse({"error": {"message": "Content Exists Risk"}})))
        with self.assertRaises(llm.LLMUnavailableError) as cm:
            await self.collect()
        self.assertEqual(cm.exception.kind, "content_filter")
        self.assertIs(cm.exception.partial, False)

    async def test_length_marks_truncated(self):
        """finish_reason=length：正常結束（字都送出了），但 meta 要說出被截斷（審查 M2）。"""
        self.install(lambda req: httpx.Response(200, content=_sse(_chunk("很長的"), _chunk("", finish="length"))))
        meta: dict = {}
        self.assertEqual(await self.collect(meta=meta), ["很長的"])
        self.assertEqual(meta, {"truncated": True, "truncated_reason": llm.TRUNCATED_LENGTH})

    async def test_read_timeout_after_text_marks_truncated(self):
        class Stream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield _sse(_chunk("前半"), done=False)
                raise httpx.ReadTimeout("read timed out")

        self.install(lambda req: httpx.Response(200, stream=Stream()))
        meta: dict = {}
        self.assertEqual(await self.collect(meta=meta), ["前半"])
        self.assertEqual(meta, {"truncated": True, "truncated_reason": llm.TRUNCATED_READ_TIMEOUT})
        self.assertEqual(len(self.requests), 1)


class HttpFirstTokenDeadlineTests(_HttpCase):
    """首字期限必須在生產的驅動方式（`web.deps._with_heartbeat`，每次 `__anext__` 開新 Task）
    下照樣生效；吐字之後不得被牆鐘截斷。"""

    patch_sleep = False  # 這組要真的等（慢首字、慢串流）

    async def _drive(self, gen, interval=0.05):
        from web import deps

        items = []
        async for item in deps._with_heartbeat(gen, interval=interval):
            if not item.startswith(": keep-alive"):
                items.append(item)
        return items

    async def test_slow_first_token_times_out_under_heartbeat(self):
        async def queued():
            # 有上限：期限機制若被改壞，測試要以斷言失敗收場，而不是卡住整個 pytest
            for _ in range(150):
                yield b": keep-alive\n\n"
                await asyncio.sleep(0.02)

        self.install(lambda req: httpx.Response(200, content=queued()))
        t0 = time.monotonic()
        with self.assertRaises(llm.LLMUnavailableError) as cm:
            await self._drive(llm.stream_completion(
                "q", model="deepseek-flash", timeout=0.2, retries=2, max_tokens=16, task="t"))
        self.assertLess(time.monotonic() - t0, 2.0)
        self.assertEqual(cm.exception.kind, "timeout")
        self.assertEqual(cm.exception.reason, llm.UNAVAILABLE_TIMEOUT)
        self.assertEqual(len(self.requests), 1, "首字逾時不重試（與 CLI 相同）")

    async def test_slow_stream_after_first_token_is_not_cut_under_heartbeat(self):
        async def slow_answer():
            yield _sse(_chunk("第一段"), done=False)
            await asyncio.sleep(0.5)
            yield _sse(_chunk("第二段"), done=False)
            await asyncio.sleep(0.3)
            yield _sse(_chunk("", finish="stop"))

        self.install(lambda req: httpx.Response(200, content=slow_answer()))
        meta: dict = {}
        items = await self._drive(llm.stream_completion(
            "q", model="deepseek-flash", timeout=0.15, meta=meta, max_tokens=16, task="t"))
        self.assertEqual(items, ["第一段", "第二段"])
        self.assertIs(meta["truncated"], False)


class HttpTotalTimeoutTests(_HttpCase):
    """吐字後的牆鐘總時限（`LLM_HTTP_TOTAL_TIMEOUT`）：伺服器每 60 秒內滴一點內容時，read 逾時與
    max_tokens 都收不了。到期比照 read 逾時：已吐字＝正常結束、標截斷原因（呼叫端附註）。
    要在生產的驅動方式（`_with_heartbeat`，每次 `__anext__` 開新 Task）下生效。"""

    patch_sleep = False

    async def _drive(self, gen, interval=0.05):
        from web import deps

        items = []
        async for item in deps._with_heartbeat(gen, interval=interval):
            if not item.startswith(": keep-alive"):
                items.append(item)
        return items

    def _dripping(self, closed: asyncio.Event | None = None):
        class Drip(httpx.AsyncByteStream):
            async def __aiter__(self):
                # 有上限：期限機制若被改壞，測試要以斷言失敗收場，而不是卡住整個 pytest
                for i in range(60):
                    yield _sse(_chunk(f"段{i}"), done=False)
                    await asyncio.sleep(0.05)
                yield _sse(_chunk("", finish="stop"))

            async def aclose(self):
                if closed is not None:
                    closed.set()

        return Drip()

    async def test_total_timeout_after_text_marks_truncated_under_heartbeat(self):
        closed = asyncio.Event()
        self.install(lambda req: httpx.Response(200, stream=self._dripping(closed)))
        meta: dict = {}
        t0 = time.monotonic()
        with mock.patch.object(llm, "_http_total_timeout", return_value=0.4):
            items = await self._drive(llm.stream_completion(
                "q", model="deepseek-flash", timeout=5, retries=2, meta=meta, max_tokens=16, task="t"))
        self.assertLess(time.monotonic() - t0, 2.0, "總時限要在新 Task 驅動下照樣生效")
        self.assertTrue(items and items[0] == "段0")
        self.assertLess(len(items), 60)
        self.assertEqual(meta, {"truncated": True, "truncated_reason": llm.TRUNCATED_TOTAL_TIMEOUT})
        self.assertEqual(len(self.requests), 1, "已吐字不重試")
        self.assertTrue(closed.is_set(), "到期要關掉 httpx 回應")

    async def test_generous_total_timeout_does_not_cut_normal_stream(self):
        self.install(lambda req: httpx.Response(200, stream=self._dripping()))
        meta: dict = {}
        with mock.patch.object(llm, "_http_total_timeout", return_value=30.0):
            items = await self._drive(llm.stream_completion(
                "q", model="deepseek-flash", timeout=5, meta=meta, max_tokens=16, task="t"))
        self.assertEqual(len(items), 60)
        self.assertIs(meta["truncated"], False)

    async def test_total_timeout_reaches_the_http_request_from_settings(self):
        seen: dict = {}

        async def fake(*a, **k):
            seen.update(k)
            yield lh.ChatOutcome(kind=None)

        with mock.patch.object(lh, "astream_chat", fake), \
             mock.patch.object(llm, "get_settings", return_value=mock.Mock(llm_http_total_timeout=123.0)):
            await self.collect()
        self.assertEqual(seen["total_timeout"], 123.0)
        self.assertEqual(seen["first_token_timeout"], 120.0)


class CallSitesPassMaxTokensAndTaskTests(unittest.TestCase):
    """每個 `stream_completion` 呼叫點都要帶 `max_tokens` 與 `task`（第二版計畫 §8、§4.8）。

    漏帶的話上限退回保底值、log 認不出任務，而且不會有任何錯誤——所以在這裡靜態釘住。
    """

    def test_all_call_sites(self):
        missing = []
        seen = 0
        for base in ("app", "eval", "scripts", "web"):
            for path in sorted((REPO_ROOT / base).rglob("*.py")):
                tree = ast.parse(path.read_text(encoding="utf-8"))
                for node in ast.walk(tree):
                    if not isinstance(node, ast.Call):
                        continue
                    fn = node.func
                    name = fn.id if isinstance(fn, ast.Name) else fn.attr if isinstance(fn, ast.Attribute) else None
                    if name != "stream_completion":
                        continue
                    seen += 1
                    kws = {k.arg for k in node.keywords}
                    if not {"max_tokens", "task"} <= kws:
                        missing.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
        self.assertGreaterEqual(seen, 9, "掃描範圍漏掉呼叫點（守門空轉）")
        self.assertEqual(missing, [])


class CallSiteMaxTokensValuesTests(unittest.TestCase):
    """各呼叫點實際送出的 `max_tokens` 值（第二版計畫 §8）逐一釘住：上面那組只檢查「有帶」，
    值被改壞（例如路由 16 改成 1024、追問 512 被刪一位）不會紅。表格鍵是（檔案, task 的原始碼），
    多一個或少一個呼叫點也會紅。兩個 judge（faithfulness、eval_judge）走 `llm_http.complete_json`、
    不經 `stream_completion`，上限另在 `JUDGE_MAX_TOKENS_BY_SYSTEM` 釘住。

    值以該檔的模組命名空間求值 max_tokens 的運算式（常數、模組常數或 `query_planner.X`），
    所以量到的是呼叫當下真正會送出的數字，不是常數名。改值要同步改這張表，並在 PR 說明理由。
    """

    EXPECTED = {
        ("app/services/answer.py", "'ask_overview'"): 4096,
        ("app/services/answer.py", "'ask_web'"): 8192,
        ("app/services/answer.py", "'ask_web' if web_on else 'ask_answer'"): 8192,
        ("app/services/scope_router.py", "'ask_intent'"): 16,
        ("app/services/scope_router.py", "'ask_condense'"): 256,
        ("app/services/query_planner.py", "'qa_planner'"): 1024,
        ("app/services/agentic_qa.py", "'qa_agentic_eval'"): 1024,
        ("app/services/followups.py", "'ask_followup'"): 512,
        ("eval/run_ragas.py", "'eval_answer'"): 8192,
    }

    def test_values(self):
        import importlib

        found: dict[tuple[str, str], int] = {}
        for base in ("app", "eval", "scripts", "web"):
            for path in sorted((REPO_ROOT / base).rglob("*.py")):
                tree = ast.parse(path.read_text(encoding="utf-8"))
                rel = str(path.relative_to(REPO_ROOT))
                for node in ast.walk(tree):
                    if not isinstance(node, ast.Call):
                        continue
                    fn = node.func
                    name = fn.id if isinstance(fn, ast.Name) else fn.attr if isinstance(fn, ast.Attribute) else None
                    if name != "stream_completion":
                        continue
                    kws = {k.arg: k.value for k in node.keywords}
                    module = importlib.import_module(rel[:-3].replace("/", "."))
                    expr = ast.Expression(kws["max_tokens"])
                    value = eval(compile(expr, rel, "eval"), vars(module))  # noqa: S307 — 只求值 repo 內常數
                    key = (rel, ast.unparse(kws["task"]))
                    self.assertNotIn(key, found, f"同一 task 出現兩個呼叫點：{key}")
                    found[key] = value
        self.assertEqual(found, self.EXPECTED)


class DependencyDirectionTests(unittest.TestCase):
    """`llm_http`／`llm_models` 是葉模組：不得往回 import 分派層與問答層。

    `retrieval_pipeline`↔`answer` 的刻意循環、`query_planner.py` 開頭的依賴約束都經過 llm 層；
    葉模組一旦 import 它們，就可能在 import 期形成新的循環。
    """

    FORBIDDEN = {
        "app.services.answer", "app.services.retrieval_pipeline", "app.services.agentic_qa", "app.services.llm",
    }

    def _imports(self, rel: str) -> set[str]:
        tree = ast.parse((REPO_ROOT / rel).read_text(encoding="utf-8"))
        mods: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods.add(node.module)
                mods.update(f"{node.module}.{a.name}" for a in node.names)
        return mods

    def test_leaf_modules_do_not_import_upward(self):
        for rel in ("app/services/llm_http.py", "app/services/llm_models.py"):
            with self.subTest(module=rel):
                self.assertEqual(self._imports(rel) & self.FORBIDDEN, set())


if __name__ == "__main__":
    unittest.main()
