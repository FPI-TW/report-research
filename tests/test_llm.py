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


if __name__ == "__main__":
    unittest.main()
