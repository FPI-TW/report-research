"""併發閘門與多 worker 守門的測試。

端點層一律走 HTTP（httpx + ASGITransport），不直接呼叫 handler 函式物件：本專案
兩次事故都出在「測試繞過路由層所以全綠」（2026-07-28 裝飾器套錯輔助函式回 422、
更早的 Pydantic 靜默丟欄位）。

httpx 的 ASGITransport **會把整個回應緩衝完才回傳**（`await self.app(...)` 跑完為止），
所以這裡不用「讀第一個事件就跳出」的寫法，改成讓請求完整跑完再驗事件順序——
排隊與否照樣看得出來，而且不必依賴串流時序。
"""
from __future__ import annotations

import os

os.environ.setdefault("REPORT_MARK_ACCESS_USERNAME", "tester")
os.environ.setdefault("REPORT_MARK_ACCESS_PASSWORD", "testpass")
os.environ.setdefault("REPORT_MARK_SESSION_SECRET", "fixed-test-secret-0123456789")

import asyncio  # noqa: E402
import sys  # noqa: E402
import unittest  # noqa: E402
from pathlib import Path  # noqa: E402
from unittest.mock import patch  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import httpx  # noqa: E402

import web.server as server  # noqa: E402
from web import concurrency, deps  # noqa: E402
from web.concurrency import ConcurrencyGate  # noqa: E402
from web.routers import ask as ask_routes  # noqa: E402


async def _wait_until(pred, timeout: float = 5.0) -> None:
    """輪詢等待條件成立。比固定 sleep 可靠：慢機器上不會偽陰性，快機器上不會空等。"""
    deadline = asyncio.get_running_loop().time() + timeout
    while not pred():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("等待條件逾時")
        await asyncio.sleep(0.005)


def _events(sse_body: str) -> list[str]:
    return [ln[len("event:"):].strip() for ln in sse_body.splitlines() if ln.startswith("event:")]


class WorkerDetectionTests(unittest.TestCase):
    """偵測必須是三值的：讀不到 ≠ 單行程。"""

    def test_no_flag_and_no_env_is_unknown(self):
        self.assertIsNone(concurrency.detect_worker_count(["uvicorn", "web.server:app"], {}))

    def test_reads_uvicorn_workers_flag_both_spellings(self):
        argv = ["uvicorn", "web.server:app", "--workers", "4"]
        self.assertEqual(concurrency.detect_worker_count(argv, {}), 4)
        self.assertEqual(
            concurrency.detect_worker_count(["uvicorn", "web.server:app", "--workers=4"], {}), 4
        )

    def test_reads_web_concurrency_env(self):
        self.assertEqual(concurrency.detect_worker_count(["uvicorn"], {"WEB_CONCURRENCY": "3"}), 3)

    def test_explicit_flag_beats_env(self):
        # uvicorn/config.py 只在 --workers 未指定時才看 WEB_CONCURRENCY；優先序照抄。
        argv = ["uvicorn", "web.server:app", "--workers", "1"]
        self.assertEqual(concurrency.detect_worker_count(argv, {"WEB_CONCURRENCY": "8"}), 1)

    def test_reload_is_not_mistaken_for_multi_worker(self):
        # --reload 也走子行程，但那是單 worker 的開發模式。用 parent_process() 當判準
        # 就會在這裡誤判並擋掉開發。
        argv = ["uvicorn", "web.server:app", "--reload"]
        self.assertIsNone(concurrency.detect_worker_count(argv, {}))

    def test_gunicorn_short_flag_only_for_gunicorn(self):
        self.assertEqual(concurrency.detect_worker_count(["/usr/bin/gunicorn", "-w", "2", "x"], {}), 2)
        # -w 對非 gunicorn 的啟動方式不解讀（別的工具可能拿 -w 當別的意思）
        self.assertIsNone(concurrency.detect_worker_count(["uvicorn", "-w", "2"], {}))

    def test_gunicorn_cmd_args_env(self):
        self.assertEqual(
            concurrency.detect_worker_count(
                ["/usr/bin/gunicorn", "x"], {"GUNICORN_CMD_ARGS": "--workers 5 --timeout 60"}
            ),
            5,
        )

    def test_assert_single_worker_raises_only_above_one(self):
        with self.assertRaises(RuntimeError):
            concurrency.assert_single_worker(["uvicorn", "--workers", "2"], {})
        self.assertEqual(concurrency.assert_single_worker(["uvicorn", "--workers", "1"], {}), 1)
        self.assertIsNone(concurrency.assert_single_worker(["uvicorn"], {}))


class LifespanGuardTests(unittest.TestCase):
    def test_startup_refuses_multi_worker(self):
        """守門必須在 lifespan 生效，不只是有一個函式可以呼叫。"""
        from fastapi.testclient import TestClient

        # 一併 patch 暖機：守門若失效，啟動會往下跑去載 BGE-M3（實測分鐘級），
        # 這題就會從「紅」變成「掛住」——反轉實驗要看得到紅色，不是等到逾時。
        with patch.object(sys, "argv", ["uvicorn", "web.server:app", "--workers", "2"]), \
             patch("web.deps.embed_texts", return_value=[[0.0]]), \
             patch("web.deps.rerank_warmup", return_value=True):
            with self.assertRaises(RuntimeError) as ctx:
                with TestClient(server.app):
                    pass
        self.assertIn("worker", str(ctx.exception))

    def test_startup_allows_single_process(self):
        from fastapi.testclient import TestClient

        with patch.object(sys, "argv", ["uvicorn", "web.server:app"]), \
             patch("web.deps.embed_texts", return_value=[[0.0]]), \
             patch("web.deps.rerank_warmup", return_value=True):
            with TestClient(server.app) as client:
                self.assertEqual(client.get("/login").status_code, 200)


class ConcurrencyGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_waiting_count_and_queue_full(self):
        gate = ConcurrencyGate(1, name="t", max_queue=1)
        self.assertFalse(gate.would_queue())
        self.assertFalse(gate.queue_full())

        await gate.acquire()
        self.assertTrue(gate.would_queue())
        self.assertEqual(gate.waiting, 0)  # 持有者不算排隊
        self.assertFalse(gate.queue_full())

        waiter = asyncio.create_task(gate.acquire())
        await _wait_until(lambda: gate.waiting == 1)
        self.assertTrue(gate.queue_full())
        self.assertEqual(gate.queue_event()["position"], 2)

        gate.release()
        await waiter
        self.assertEqual(gate.waiting, 0)
        gate.release()

    async def test_cancelled_waiter_releases_its_queue_slot(self):
        """洩漏一格就等於永久縮小排隊上限，久了所有請求都會 429。"""
        gate = ConcurrencyGate(1, name="t", max_queue=2)
        await gate.acquire()
        waiter = asyncio.create_task(gate.acquire())
        await _wait_until(lambda: gate.waiting == 1)
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        self.assertEqual(gate.waiting, 0)
        gate.release()

    async def test_max_queue_zero_means_unlimited(self):
        gate = ConcurrencyGate(1, name="t", max_queue=0)
        await gate.acquire()
        waiters = [asyncio.create_task(gate.acquire()) for _ in range(5)]
        await _wait_until(lambda: gate.waiting == 5)
        self.assertFalse(gate.queue_full())
        gate.release()
        for w in waiters:
            w.cancel()
        await asyncio.gather(*waiters, return_exceptions=True)


class _HttpTestBase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=server.app), base_url="http://127.0.0.1"
        )
        resp = await self._client.post(
            "/login", data={"username": "tester", "password": "testpass"}
        )
        assert resp.status_code == 303, f"login failed: {resp.status_code}"

    async def asyncTearDown(self):
        await self._client.aclose()


class AskQueuedEventTests(_HttpTestBase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self._orig_gate = ask_routes._ASK_GATE
        self._orig_answer = deps.answer_question

    async def asyncTearDown(self):
        ask_routes._ASK_GATE = self._orig_gate
        deps.answer_question = self._orig_answer
        await super().asyncTearDown()

    async def test_queued_event_precedes_answer_when_gate_is_busy(self):
        gate = ConcurrencyGate(1, name="ask", max_queue=0)
        ask_routes._ASK_GATE = gate
        hold = asyncio.Event()
        first_started = asyncio.Event()

        async def fake_answer(question, **kwargs):
            if question == "first":
                first_started.set()
                await hold.wait()
            yield ("done", {"conversation_id": "c1"})

        deps.answer_question = fake_answer

        first = asyncio.create_task(self._client.post("/api/ask", json={"question": "first"}))
        await asyncio.wait_for(first_started.wait(), 5)
        second = asyncio.create_task(self._client.post("/api/ask", json={"question": "second"}))
        # 第二題確實卡在閘門上（而不是搶先跑完）才放行，否則這題會偶爾偽陰性。
        await _wait_until(lambda: gate.waiting == 1)
        hold.set()

        r1, r2 = await asyncio.wait_for(asyncio.gather(first, second), 10)
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r2.status_code, 200)
        # 立刻拿到名額的那題不該收到排隊事件——否則等於每題都在喊排隊。
        self.assertNotIn("queued", _events(r1.text))
        self.assertEqual(_events(r2.text)[0], "queued")
        self.assertIn("done", _events(r2.text))
        self.assertIn('"position": 1', r2.text)

    async def test_no_queued_event_when_capacity_is_free(self):
        ask_routes._ASK_GATE = ConcurrencyGate(3, name="ask", max_queue=0)

        async def fake_answer(question, **kwargs):
            yield ("done", {"conversation_id": "c1"})

        deps.answer_question = fake_answer
        resp = await self._client.post("/api/ask", json={"question": "只有我一個"})
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("queued", _events(resp.text))

    async def test_returns_429_with_retry_after_when_queue_is_full(self):
        """429 只可能發生在取鎖之前：SSE 一送出 200 就改不了 status code。"""
        gate = ConcurrencyGate(1, name="ask", max_queue=1)
        ask_routes._ASK_GATE = gate
        holder = asyncio.create_task(gate.acquire())
        await holder
        waiter = asyncio.create_task(gate.acquire())
        await _wait_until(lambda: gate.waiting == 1)

        # 一定要有逾時：429 前檢若失效，這個請求會安靜地排隊到天荒地老——
        # 反轉實驗要看得到紅色，不是看著測試掛住。
        resp = await asyncio.wait_for(self._client.post("/api/ask", json={"question": "擠不進去"}), 10)
        self.assertEqual(resp.status_code, 429)
        self.assertEqual(resp.headers.get("Retry-After"), "30")
        self.assertIn("排隊", resp.json()["detail"])

        gate.release()
        await waiter
        gate.release()


if __name__ == "__main__":
    unittest.main()
