import json
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services import llm  # noqa: E402

# 假 claude 子程序：讀掉 stdin（_run_attempt 會寫 prompt 後關閉），於子程序內自行組出
# 一行遠超 64KB 的 text_delta 事件（避免把 300KB 塞進 argv），再補一個 result 終點事件。
_FAKE_CLAUDE_SCRIPT = r'''
import sys, json
sys.stdin.buffer.read()
big = "台" * 100000  # 台 x 100000 ~= 300KB UTF-8，遠超 asyncio 預設 64KB 行上限
line = json.dumps(
    {"type": "stream_event",
     "event": {"type": "content_block_delta",
               "delta": {"type": "text_delta", "text": big}}},
    ensure_ascii=False,
)
sys.stdout.write(line + "\n")
sys.stdout.write(json.dumps({"type": "result"}) + "\n")
'''


class RunAttemptLargeLineTests(unittest.IsolatedAsyncioTestCase):
    """回歸：單行 stream-json 事件遠超 asyncio 預設 64KB 上限時，_run_attempt 不得因
    LimitOverrunError 中斷，須完整讀出文字（長篇回答結尾 result 事件的實況）。

    修法＝create_subprocess_exec 傳入較大的 limit（_STDOUT_LINE_LIMIT）。移除該參數
    會使本測試在讀取巨行時拋 ValueError/LimitOverrunError 而失敗。
    """

    async def _collect(self, cmd: list[str]) -> list:
        out: list = []
        async for chunk in llm._run_attempt(cmd, prompt="x", timeout=30.0):
            out.append(chunk)
        return out

    async def test_text_delta_line_over_64kb_is_streamed(self):
        cmd = [sys.executable, "-c", _FAKE_CLAUDE_SCRIPT]
        chunks = await self._collect(cmd)

        streamed = "".join(c for c in chunks if isinstance(c, str))
        expected_big = "台" * 100000
        self.assertIn(expected_big, streamed)
        # 未以 ("__error__", ...) tuple 收尾 → 走的是正常串流路徑，非 fallback/失敗
        self.assertFalse(any(isinstance(c, tuple) for c in chunks))

    def test_limit_constant_exceeds_default_64kb(self):
        # 明示修法意圖：上限須遠大於 asyncio 預設 64KB。
        self.assertGreater(llm._STDOUT_LINE_LIMIT, 64 * 1024)


# 假 claude：先送 system/init（工具集由 argv[1] 的 JSON 指定），再送一段文字與 result。
_FAKE_INIT_SCRIPT = r'''
import sys, json
sys.stdin.buffer.read()
tools = json.loads(sys.argv[1])
sys.stdout.write(json.dumps({"type": "system", "subtype": "init", "tools": tools}) + "\n")
sys.stdout.write(json.dumps(
    {"type": "stream_event",
     "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "ok"}}}) + "\n")
sys.stdout.write(json.dumps({"type": "result"}) + "\n")
'''


def _init(tools) -> str:
    return json.dumps({"type": "system", "subtype": "init", "tools": tools})


class CheckInitToolsTests(unittest.TestCase):
    """旗標失效要看得見：init 事件回報的工具集與預期不符 → 回說明字串。"""

    def test_no_web_expects_empty(self):
        self.assertIsNone(llm.check_init_tools(_init([]), False))
        self.assertIsNotNone(llm.check_init_tools(_init(["Read", "Bash"]), False))

    def test_web_expects_exactly_websearch(self):
        self.assertIsNone(llm.check_init_tools(_init(["WebSearch"]), True))
        self.assertIsNotNone(llm.check_init_tools(_init([]), True))
        self.assertIsNotNone(llm.check_init_tools(_init(["WebSearch", "Read"]), True))

    def test_defensive_on_other_shapes(self):
        """非 init、沒帶 tools、tools 非 list、壞 JSON 一律不判（事件格式可能變）。"""
        for line in (
            "",
            "not json",
            json.dumps({"type": "system", "subtype": "init"}),
            json.dumps({"type": "system", "subtype": "init", "tools": "Read"}),
            json.dumps({"type": "system", "subtype": "other", "tools": ["Read"]}),
            json.dumps({"type": "result", "tools": ["Read"]}),
        ):
            with self.subTest(line=line):
                self.assertIsNone(llm.check_init_tools(line, False))


class RunAttemptInitToolsTests(unittest.IsolatedAsyncioTestCase):
    async def _collect(self, tools, allow_web):
        cmd = [sys.executable, "-c", _FAKE_INIT_SCRIPT, json.dumps(tools)]
        return [c async for c in llm._run_attempt(cmd, prompt="x", timeout=30.0, allow_web=allow_web)]

    async def test_mismatch_logs_warning_but_still_streams(self):
        with self.assertLogs("app.services.llm", level="WARNING") as cm:
            chunks = await self._collect(["Read", "Bash"], False)
        self.assertEqual(chunks, ["ok"])  # fail-open：照常出字
        self.assertEqual(len(cm.records), 1)
        self.assertIn("Read", cm.output[0])

    async def test_match_is_silent(self):
        with self.assertNoLogs("app.services.llm", level="WARNING"):
            self.assertEqual(await self._collect([], False), ["ok"])
            self.assertEqual(await self._collect(["WebSearch"], True), ["ok"])

    async def test_no_check_when_allow_web_not_given(self):
        with self.assertNoLogs("app.services.llm", level="WARNING"):
            chunks = [c async for c in llm._run_attempt(
                [sys.executable, "-c", _FAKE_INIT_SCRIPT, json.dumps(["Read"])], prompt="x", timeout=30.0)]
        self.assertEqual(chunks, ["ok"])


# 假 claude：argv[1] 決定行為。
#   partial  先吐一段字、再卡住（模擬串到一半被逾時截斷）
#   silent   什麼都不吐就卡住（模擬沒吐字就逾時）
#   full     吐字後送 result（正常結束）
#   overload result 事件帶 is_error（模擬 529）
_FAKE_BEHAVIOR_SCRIPT = r'''
import sys, json, time
sys.stdin.buffer.read()
mode = sys.argv[1]
def delta(t):
    return json.dumps({"type": "stream_event",
                       "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": t}}})
if mode in ("partial", "full"):
    sys.stdout.write(delta('{"statements": ["半') + "\n"); sys.stdout.flush()
if mode == "full":
    sys.stdout.write(json.dumps({"type": "result"}) + "\n")
elif mode == "overload":
    sys.stdout.write(json.dumps({"type": "result", "is_error": True, "result": "API Error: 529 Overloaded"}) + "\n")
else:
    time.sleep(30)
'''


class StreamCompletionSignalTests(unittest.IsolatedAsyncioTestCase):
    """截斷與失敗原因要讓呼叫端拿得到：CLI 逾時對已吐字 fail-open、不拋例外，
    沒有 meta 就分不出「完整回應」與「被砍掉一半」。"""

    async def _run(self, mode: str, *, timeout: float = 5.0, meta: dict | None = None):
        from unittest import mock

        cmd = [sys.executable, "-c", _FAKE_BEHAVIOR_SCRIPT, mode]
        with mock.patch.object(llm, "_build_cmd", lambda *a, **k: cmd):
            return [c async for c in llm.stream_completion("x", timeout=timeout, retries=0, meta=meta)]

    async def test_partial_output_cut_by_timeout_is_marked_truncated(self):
        meta: dict = {}
        chunks = await self._run("partial", timeout=1.0, meta=meta)
        self.assertEqual("".join(chunks), '{"statements": ["半')
        self.assertIs(meta["truncated"], True)

    async def test_normal_completion_is_not_truncated(self):
        meta: dict = {}
        await self._run("full", meta=meta)
        self.assertIs(meta["truncated"], False)

    async def test_meta_is_optional(self):
        self.assertEqual("".join(await self._run("full")), '{"statements": ["半')

    async def test_silent_timeout_reason(self):
        with self.assertRaises(llm.LLMUnavailableError) as cm:
            await self._run("silent", timeout=1.0)
        self.assertEqual(cm.exception.reason, llm.UNAVAILABLE_TIMEOUT)

    async def test_api_error_reason(self):
        with self.assertRaises(llm.LLMUnavailableError) as cm:
            await self._run("overload")
        self.assertEqual(cm.exception.reason, llm.UNAVAILABLE_API_ERROR)

    def test_reason_defaults_to_none_for_direct_construction(self):
        self.assertIsNone(llm.LLMUnavailableError("529").reason)

    def test_kind_and_partial_defaults(self):
        """kind 預設 other（未分類）、partial 預設 False；CLI 路徑兩者都不填。"""
        exc = llm.LLMUnavailableError("529")
        self.assertEqual(exc.kind, "other")
        self.assertIs(exc.partial, False)
        exc = llm.LLMUnavailableError("x", kind="quota", partial=True, reason="api_error")
        self.assertEqual((exc.kind, exc.partial, exc.reason), ("quota", True, "api_error"))

    async def test_cli_failure_leaves_kind_unclassified(self):
        with self.assertRaises(llm.LLMUnavailableError) as cm:
            await self._run("overload")
        self.assertEqual(cm.exception.kind, "other")
        self.assertIs(cm.exception.partial, False)


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


class HttpDispatchTests(_HttpCase):
    async def test_whitelisted_model_goes_http_not_cli(self):
        self.install(lambda req: httpx.Response(200, content=_ok("台積電", "展望")))

        def no_cli(*a, **k):
            raise AssertionError("白名單 model 不得 spawn claude CLI")

        meta: dict = {}
        with mock.patch.object(llm, "_build_cmd", no_cli):
            chunks = await self.collect(system="系統", meta=meta, max_tokens=4096, task="ask_overview")
        self.assertEqual(chunks, ["台積電", "展望"])
        self.assertIs(meta["truncated"], False)
        body = json.loads(self.requests[0].content)
        self.assertEqual(body["model"], "deepseek-flash")
        self.assertEqual(body["max_tokens"], 4096)
        self.assertEqual(body["user_id"], "web-ask_overview")
        self.assertEqual(body["messages"][0], {"role": "system", "content": "系統"})

    async def test_claude_model_goes_cli_not_http(self):
        self.install(lambda req: httpx.Response(200, content=_ok("x")))

        async def fake_attempt(cmd, prompt, timeout, allow_web=None, meta=None):
            if meta is not None:
                meta["timed_out"] = False
            yield "cli"

        with mock.patch.object(llm, "_run_attempt", fake_attempt):
            chunks = await self.collect(model="claude-sonnet-5", max_tokens=16, task="ask_intent")
        self.assertEqual(chunks, ["cli"])
        self.assertEqual(self.requests, [], "CLI model 不得送出 HTTP 請求")

    async def test_near_miss_names_are_not_http(self):
        """白名單是逐字比對：打錯字或 CLI 別名一律走 CLI（不會被送到付費端點）。"""
        for name in ("deepseek-flsh", "sonnet", "DeepSeek-Flash"):
            with self.subTest(name=name):
                self.assertFalse(llm.is_http_model(name))

    async def test_web_search_with_deepseek_is_config_error(self):
        self.install(lambda req: httpx.Response(200, content=_ok("x")))
        with self.assertRaises(llm.LLMUnavailableError) as cm:
            await self.collect(allow_web=True)
        self.assertEqual(cm.exception.kind, "config")
        self.assertIn("ASK_WEB_MODEL", str(cm.exception))
        self.assertEqual(self.requests, [])

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


class HttpCancelTests(_HttpCase):
    def _hanging(self, closed: asyncio.Event, started: asyncio.Event, first: bytes | None = None):
        class Stream(httpx.AsyncByteStream):
            async def __aiter__(self):
                if first is not None:
                    yield first
                started.set()
                await asyncio.sleep(10)
                yield b""

            async def aclose(self):
                closed.set()

        return Stream()

    async def test_cancel_closes_response(self):
        closed, started = asyncio.Event(), asyncio.Event()
        self.install(lambda req: httpx.Response(200, stream=self._hanging(closed, started)))

        async def consume():
            async for _ in llm.stream_completion("q", model="deepseek-flash", max_tokens=16, task="t"):
                pass

        task = asyncio.ensure_future(consume())
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(closed.is_set(), "CancelledError 要原樣上拋，並關閉 HTTP 回應")

    async def test_heartbeat_close_mid_stream_closes_response(self):
        """用戶端中斷：`_with_heartbeat` 的 finally 會 cancel 未完成的 `__anext__` 再 aclose。"""
        from web import deps

        closed, started = asyncio.Event(), asyncio.Event()
        self.install(lambda req: httpx.Response(
            200, stream=self._hanging(closed, started, first=_sse(_chunk("a"), done=False))))
        hb = deps._with_heartbeat(
            llm.stream_completion("q", model="deepseek-flash", max_tokens=16, task="t"), interval=0.05)
        first = await hb.__anext__()
        self.assertEqual(first, "a")
        self.assertFalse(closed.is_set())
        await hb.aclose()
        self.assertTrue(closed.is_set())


class HttpEventLoopTests(unittest.TestCase):
    """批次的 asyncio.run 與測試每次都換 loop；client 綁在舊 loop 上會出錯。"""

    def tearDown(self):
        lh._transport = None
        lh._reset_clients()

    def test_two_loops_in_a_row(self):
        lh._transport = httpx.MockTransport(lambda req: httpx.Response(200, content=_ok("ok")))
        lh._reset_clients()

        async def once():
            return [c async for c in llm.stream_completion("q", model="deepseek-flash", max_tokens=16, task="t")]

        with mock.patch.dict(os.environ, _ENV):
            for _ in range(2):
                self.assertEqual(asyncio.run(once()), ["ok"])


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
