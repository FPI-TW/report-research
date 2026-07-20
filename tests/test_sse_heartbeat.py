"""SSE 心跳：長靜默不得讓反向代理切斷連線，且不得干擾既有事件契約。

背景：nginx proxy_read_timeout=60s，而研報逐節路徑每節的針對性檢索（含 rerank）
可達 90s+ 無事件。這條路徑本機看不到（eval 不走 HTTP、測試不走 HTTP、直連 :8097
繞過 nginx），故這裡直接測心跳包裝器本身。
"""

from __future__ import annotations

import asyncio
import unittest

import web.server as srv


async def _drain(agen) -> list[str]:
    return [x async for x in agen]


class SseHeartbeatTests(unittest.IsolatedAsyncioTestCase):
    async def test_silence_emits_heartbeat_then_real_event(self):
        """靜默超過 interval → 吐心跳；真事件到達後照常轉發。"""

        async def slow():
            await asyncio.sleep(0.25)
            yield "event: done\ndata: {}\n\n"

        out = await _drain(srv._with_heartbeat(slow(), interval=0.05))
        self.assertGreaterEqual(out.count(": keep-alive\n\n"), 2)
        self.assertEqual(out[-1], "event: done\ndata: {}\n\n")

    async def test_fast_stream_emits_no_heartbeat(self):
        """事件密集時不插心跳——避免污染既有事件序。"""

        async def fast():
            for i in range(5):
                yield f"event: token\ndata: {i}\n\n"

        out = await _drain(srv._with_heartbeat(fast(), interval=10))
        self.assertEqual(len(out), 5)
        self.assertNotIn(": keep-alive\n\n", out)

    async def test_event_order_preserved(self):
        """心跳不得改變事件相對順序（done 仍是最後一個真事件）。"""

        async def mixed():
            yield "event: status\ndata: 1\n\n"
            await asyncio.sleep(0.12)
            yield "event: token\ndata: 2\n\n"
            yield "event: done\ndata: 3\n\n"

        out = await _drain(srv._with_heartbeat(mixed(), interval=0.05))
        real = [x for x in out if not x.startswith(":")]
        self.assertEqual(
            real,
            [
                "event: status\ndata: 1\n\n",
                "event: token\ndata: 2\n\n",
                "event: done\ndata: 3\n\n",
            ],
        )

    async def test_client_disconnect_runs_underlying_finally(self):
        """用戶端中斷 → 底層產生器的 finally 必須跑到。

        研報逐節路徑靠這個 finally kill claude 子程序；若心跳包裝器吞掉關閉訊號，
        會留下孤兒子程序。
        """
        closed = asyncio.Event()

        async def slow():
            try:
                await asyncio.sleep(30)
                yield "never"
            finally:
                closed.set()

        agen = srv._with_heartbeat(slow(), interval=0.05)
        it = agen.__aiter__()
        first = await it.__anext__()  # 先拿到一個心跳，確認已進入靜默等待
        self.assertEqual(first, ": keep-alive\n\n")
        await agen.aclose()
        await asyncio.wait_for(closed.wait(), timeout=1.0)

    async def test_underlying_exception_propagates(self):
        """底層例外不得被心跳吞掉（否則 error 事件永遠不會發出）。"""

        async def boom():
            yield "event: status\ndata: 1\n\n"
            raise RuntimeError("boom")

        with self.assertRaises(RuntimeError):
            await _drain(srv._with_heartbeat(boom(), interval=10))


if __name__ == "__main__":
    unittest.main()
