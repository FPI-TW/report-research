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


if __name__ == "__main__":
    unittest.main()
