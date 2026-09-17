"""背景研報生成登錄表（web/report_runs.py）與 /api/report-runs/* 的行為契約。

這組測試守的是一件事：**斷線不等於取消**。舊實作把生成掛在 SSE 連線上，重整一次
就殺掉一份跑 5–12 分鐘的研報；改成背景任務後，斷線只是少一個訂閱者。
"""
import asyncio
import os
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

# web.auth 匯入時即讀取共用帳密 (fail-closed)，須在匯入前設好測試用值。
os.environ.setdefault("REPORT_MARK_ACCESS_USERNAME", "tester")
os.environ.setdefault("REPORT_MARK_ACCESS_PASSWORD", "testpass")
os.environ.setdefault("REPORT_MARK_SESSION_SECRET", "fixed-test-secret-0123456789")

from web import report_runs  # noqa: E402

CONV = "123e4567-e89b-12d3-a456-426614174000"


def _events(items, *, hold: asyncio.Event | None = None):
    """把一串 (kind, payload) 做成 async generator；hold 可用來卡在中途。"""

    async def gen():
        for i, item in enumerate(items):
            if hold is not None and i == 1:
                await hold.wait()
            yield item

    return gen


class RegistryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        report_runs._RUNS.clear()
        report_runs._BY_KEY.clear()
        report_runs._DELETED_CONVERSATIONS.clear()
        report_runs._DELETION_GENERATIONS.clear()
        report_runs._PENDING_DELETION_LEASES.clear()
        report_runs._COMPLETED_DELETIONS.clear()

    async def asyncTearDown(self):
        await report_runs.shutdown()

    async def _drain(self, run_id):
        return [ev async for ev in report_runs.subscribe(run_id)]

    async def test_subscriber_receives_all_events(self):
        evs = [("status", {"stage": "retrieving"}), ("done", {"report_id": "r1"})]
        run_id, is_new = report_runs.start_or_attach(
            question="q", conversation_id=CONV, qa_id=None, template_id=None,
            locale="zh-Hant", make_events=_events(evs),
        )
        self.assertTrue(is_new)
        self.assertEqual(await self._drain(run_id), evs)

    async def test_generation_survives_subscriber_disconnect(self):
        """核心契約：訂閱者中途離開，生成照跑到底，之後重連仍拿得到完整結果。"""
        hold = asyncio.Event()
        evs = [
            ("status", {"stage": "retrieving"}),
            ("status", {"stage": "writing"}),
            ("done", {"report_id": "r1"}),
        ]
        run_id, _ = report_runs.start_or_attach(
            question="q", conversation_id=CONV, qa_id=None, template_id=None,
            locale="zh-Hant", make_events=_events(evs, hold=hold),
        )
        # 第一個訂閱者收下第一則就走人（＝使用者重整）
        sub = report_runs.subscribe(run_id)
        first = await sub.__anext__()
        self.assertEqual(first, evs[0])
        await sub.aclose()
        self.assertEqual(report_runs.find_active(CONV)[0]["run_id"], run_id)

        hold.set()
        await report_runs._RUNS[run_id].task
        # 重連：完整重播，一則不漏
        self.assertEqual(await self._drain(run_id), evs)

    async def test_token_not_replayed_but_delivered_live(self):
        """token 不進重播緩衝（見模組 docstring），但在線訂閱者照收。"""
        evs = [("token", "x" * 100), ("done", {"report_id": "r1"})]
        run_id, _ = report_runs.start_or_attach(
            question="q", conversation_id=CONV, qa_id=None, template_id=None,
            locale="zh-Hant", make_events=_events(evs),
        )
        await report_runs._RUNS[run_id].task
        replayed = await self._drain(run_id)
        self.assertEqual([k for k, _ in replayed], ["done"])

    async def test_section_draft_markdown_dropped_from_replay(self):
        big = {"position": 1, "section_key": "exec_summary", "heading": "摘要", "markdown": "M" * 5000}
        run_id, _ = report_runs.start_or_attach(
            question="q", conversation_id=CONV, qa_id=None, template_id=None,
            locale="zh-Hant", make_events=_events([("section_draft", big), ("done", {})]),
        )
        await report_runs._RUNS[run_id].task
        replayed = dict(await self._drain(run_id))
        self.assertEqual(replayed["section_draft"]["heading"], "摘要")
        self.assertNotIn("markdown", replayed["section_draft"])

    async def test_same_question_attaches_instead_of_starting_twice(self):
        hold = asyncio.Event()
        calls = []

        def make():
            calls.append(1)
            return _events([("status", {"stage": "retrieving"}), ("done", {})], hold=hold)()

        first, new1 = report_runs.start_or_attach(
            question="q", conversation_id=CONV, qa_id=None, template_id=None,
            locale="zh-Hant", make_events=make,
        )
        second, new2 = report_runs.start_or_attach(
            question="q", conversation_id=CONV, qa_id=None, template_id=None,
            locale="zh-Hant", make_events=make,
        )
        self.assertTrue(new1)
        self.assertFalse(new2, "同題重按必須接回既有 run,不可跑兩份")
        self.assertEqual(first, second)
        self.assertEqual(len(calls), 1)
        hold.set()
        await report_runs._RUNS[first].task

    async def test_different_locale_is_a_different_run(self):
        hold = asyncio.Event()
        gen = _events([("done", {})], hold=hold)
        a, _ = report_runs.start_or_attach(
            question="q", conversation_id=CONV, qa_id=None, template_id=None,
            locale="zh-Hant", make_events=gen,
        )
        b, is_new = report_runs.start_or_attach(
            question="q", conversation_id=CONV, qa_id=None, template_id=None,
            locale="en", make_events=gen,
        )
        self.assertTrue(is_new)
        self.assertNotEqual(a, b)
        hold.set()

    async def test_find_active_excludes_finished(self):
        run_id, _ = report_runs.start_or_attach(
            question="q", conversation_id=CONV, qa_id=None, template_id=None,
            locale="zh-Hant", make_events=_events([("done", {})]),
        )
        await report_runs._RUNS[run_id].task
        self.assertEqual(report_runs.find_active(CONV), [])
        self.assertTrue(report_runs.exists(run_id), "保留期內仍可重連")

    async def test_cancel_emits_error_and_finishes(self):
        hold = asyncio.Event()
        run_id, _ = report_runs.start_or_attach(
            question="q", conversation_id=CONV, qa_id=None, template_id=None,
            locale="zh-Hant",
            make_events=_events([("status", {"stage": "writing"}), ("done", {})], hold=hold),
        )
        await asyncio.sleep(0)  # 讓任務跑到 hold
        self.assertTrue(report_runs.cancel(run_id))
        with self.assertRaises(asyncio.CancelledError):
            await report_runs._RUNS[run_id].task
        kinds = [k for k, _ in await self._drain(run_id)]
        self.assertIn("error", kinds, "取消也必須有終端事件,否則前端永遠等下去")
        self.assertEqual(report_runs.find_active(CONV), [])

    async def test_cancel_conversation_tombstones_and_waits_for_active_background_run(self):
        paused = asyncio.Event()
        release = asyncio.Event()
        finished = []

        async def run():
            try:
                yield ("status", {"stage": "rendering"})
                paused.set()
                await release.wait()
                # This is the point a real run would upload/persist.  Cancellation must make
                # it unreachable before conversation deletion snapshots DB/R2 artifacts.
                finished.append("would-persist")
                yield ("done", {"report_id": "late"})
            finally:
                finished.append("closed")

        run_id, _ = report_runs.start_or_attach(
            question="q", conversation_id=CONV, qa_id=None, template_id=None,
            locale="zh-Hant", make_events=run,
        )
        await paused.wait()
        self.assertEqual(await report_runs.cancel_conversation(CONV), 1)
        release.set()  # Resuming the paused producer after deletion cannot reach persistence.
        with self.assertRaises(asyncio.CancelledError):
            await report_runs._RUNS[run_id].task
        self.assertTrue(report_runs.conversation_deleted(CONV))
        self.assertNotIn("would-persist", finished)
        self.assertIn("closed", finished)
        self.assertEqual(report_runs.find_active(CONV), [])

    def _assert_tombstone_blocks_new_report(self):
        self.assertTrue(report_runs.conversation_deleted(CONV))
        with self.assertRaises(ValueError):
            report_runs.start_or_attach(
                question="q", conversation_id=CONV, qa_id=None, template_id=None,
                locale="zh-Hant", make_events=_events([]),
            )

    async def test_second_rollback_keeps_first_pending_delete_tombstoned(self):
        first = await report_runs.begin_conversation_deletion(CONV)
        second = await report_runs.begin_conversation_deletion(CONV)
        report_runs.rollback_conversation_deletion(second)
        self._assert_tombstone_blocks_new_report()
        report_runs.confirm_conversation_deletion(first)
        self._assert_tombstone_blocks_new_report()

    async def test_two_failed_deletions_allow_report_only_after_last_rollback(self):
        first = await report_runs.begin_conversation_deletion(CONV)
        second = await report_runs.begin_conversation_deletion(CONV)
        report_runs.rollback_conversation_deletion(second)
        self._assert_tombstone_blocks_new_report()
        report_runs.rollback_conversation_deletion(first)
        self.assertFalse(report_runs.conversation_deleted(CONV))

    async def test_second_committed_delete_cannot_be_revived_by_first_rollback(self):
        first = await report_runs.begin_conversation_deletion(CONV)
        second = await report_runs.begin_conversation_deletion(CONV)
        report_runs.confirm_conversation_deletion(second)
        report_runs.rollback_conversation_deletion(first)
        self._assert_tombstone_blocks_new_report()

    async def test_deletion_between_upload_and_persist_discards_orphan_and_never_writes_report_doc(self):
        """The service checkpoint complements task cancellation for an upload that already finished."""
        from app.services import report as report_service

        upload_started = asyncio.Event()
        resume = asyncio.Event()
        uploaded = []
        discarded = []
        persisted = []
        key = "generated/temporary/base-aaaaaaaaaaaa.pdf"

        def fake_render(*_args, **_kwargs):
            return b"%PDF"

        async def fake_persist(_report_id, _pdf):
            upload_started.set()
            await resume.wait()
            uploaded.append(key)
            return None, key

        async def fake_persist_doc(*_args, **_kwargs):
            persisted.append(True)

        async def fake_discard(object_key):
            discarded.append(object_key)

        async def finalize():
            return [
                event async for event in report_service._finalize_sectioned(
                    {"markdown": "# report", "sources": []},
                    question="q", conversation_id=CONV, qa_id=None, run_id=None,
                    eval_context="", started=time.monotonic(), persist=True,
                    can_persist=lambda: not report_runs.conversation_deleted(CONV),
                )
            ]

        with patch.object(report_service, "render_report_pdf", fake_render), \
             patch.object(report_service, "persist_generated_pdf", fake_persist), \
             patch.object(report_service, "persist_report_doc", fake_persist_doc), \
             patch.object(report_service, "_discard_cancelled_generated_pdf", fake_discard):
            task = asyncio.create_task(finalize())
            await upload_started.wait()
            await report_runs.cancel_conversation(CONV)
            resume.set()
            events = await task
        self.assertEqual(uploaded, [key])
        self.assertEqual(discarded, [key])
        self.assertEqual(persisted, [])
        self.assertEqual(events[-1], ("error", {"detail": "對話串已刪除，研報未儲存"}))

    async def test_generator_exception_becomes_error_event(self):
        async def boom():
            yield ("status", {"stage": "retrieving"})
            raise RuntimeError("生成炸了")

        run_id, _ = report_runs.start_or_attach(
            question="q", conversation_id=CONV, qa_id=None, template_id=None,
            locale="zh-Hant", make_events=boom,
        )
        await report_runs._RUNS[run_id].task
        kinds = [k for k, _ in await self._drain(run_id)]
        self.assertEqual(kinds[-1], "error")

    async def test_subscribe_unknown_run_ends_immediately(self):
        self.assertEqual(await self._drain("no-such-run"), [])

    async def test_elapsed_ms_freezes_after_finish(self):
        run_id, _ = report_runs.start_or_attach(
            question="q", conversation_id=CONV, qa_id=None, template_id=None,
            locale="zh-Hant", make_events=_events([("done", {})]),
        )
        await report_runs._RUNS[run_id].task
        first = report_runs.elapsed_ms(run_id)
        await asyncio.sleep(0.02)
        self.assertEqual(report_runs.elapsed_ms(run_id), first)


class RunEndpointTests(unittest.TestCase):
    """HTTP 層契約。端點改動一律走 HTTP 測——直接呼叫 handler 物件會繞過路由層，
    那正是 /api/progress 回 422 事故裡 CI 全綠卻沒抓到的縫。"""

    def setUp(self):
        report_runs._RUNS.clear()
        report_runs._BY_KEY.clear()
        report_runs._DELETED_CONVERSATIONS.clear()
        report_runs._DELETION_GENERATIONS.clear()
        report_runs._PENDING_DELETION_LEASES.clear()
        report_runs._COMPLETED_DELETIONS.clear()

    def _client(self):
        from fastapi.testclient import TestClient

        from web.server import app

        client = TestClient(app, follow_redirects=False, base_url="http://127.0.0.1")
        r = client.post("/login", data={"username": "tester", "password": "testpass"})
        assert r.status_code == 303, f"login failed: {r.status_code}"
        return client

    def test_active_requires_valid_conversation_id(self):
        r = self._client().get("/api/report-runs", params={"conversation_id": "bad!!"})
        self.assertEqual(r.status_code, 400)

    def test_active_empty_by_default(self):
        r = self._client().get("/api/report-runs", params={"conversation_id": CONV})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"runs": []})

    def test_stream_unknown_run_is_404(self):
        r = self._client().get(f"/api/report-runs/{CONV}/stream")
        self.assertEqual(r.status_code, 404)

    def test_cancel_unknown_run_reports_false(self):
        r = self._client().post(f"/api/report-runs/{CONV}/cancel")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"cancelled": False})

    def test_run_event_leads_the_stream(self):
        """首事件恆為 run{run_id}：重連 handle 必須在任何生成事件之前抵達。"""
        from unittest.mock import patch

        async def fake_generate(*a, **k):
            yield ("status", {"stage": "retrieving"})
            yield ("done", {"report_id": "r1", "title": "T",
                            "download_url": "/api/report-doc/r1/pdf"})

        with patch("web.routers.report.generate_report", fake_generate):
            r = self._client().post("/api/report", json={"question": "台積電"})
        self.assertEqual(r.status_code, 200)
        frames = [f for f in r.text.split("\n\n") if f.strip() and not f.startswith(":")]
        self.assertTrue(frames[0].startswith("event: run"), frames[0])
        self.assertIn("run_id", frames[0])
        self.assertTrue(frames[-1].startswith("event: done"), frames[-1])


if __name__ == "__main__":
    unittest.main()
