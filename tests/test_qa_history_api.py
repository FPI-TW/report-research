# tests/test_qa_history_api.py
"""問答歷史/回饋/對話串端點的煙霧測試。

/api/feedback（尤其其 like/dislike 驗證分支）、/api/history、/api/conversations
在拆到 web/routers/qa_history.py 之前沒有 HTTP 層測試（delete_history 與
qa_versions 另有 test_auth / test_pre_split_guards 覆蓋）。這裡補上認證閘門與
feedback 的驗證分支。

record_feedback 等非 deps 的服務函式定義/匯入於 web.routers.qa_history，故 patch
web.routers.qa_history.X；SessionFactory 等仍走 web.deps。
"""
import asyncio
import os
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("REPORT_MARK_ACCESS_USERNAME", "tester")
os.environ.setdefault("REPORT_MARK_ACCESS_PASSWORD", "testpass")
os.environ.setdefault("REPORT_MARK_SESSION_SECRET", "fixed-test-secret-0123456789")

from fastapi.testclient import TestClient  # noqa: E402

from app.services.object_storage import ObjectNotFound, ObjectStorageError, generated_object_key_for_sha  # noqa: E402
from web import report_runs  # noqa: E402
from web.routers import qa_history  # noqa: E402
from web.server import app  # noqa: E402


def _authed():
    c = TestClient(app, follow_redirects=False, base_url="http://127.0.0.1")
    r = c.post("/login", data={"username": "tester", "password": "testpass"})
    assert r.status_code == 303, r.status_code
    return c


class QaHistoryAuthTests(unittest.TestCase):
    def test_endpoints_require_login(self):
        c = TestClient(app, follow_redirects=False, base_url="http://127.0.0.1")
        for path in ("/api/history", "/api/conversations"):
            self.assertEqual(c.get(path).status_code, 401, path)
        self.assertEqual(
            c.post("/api/feedback", json={"qa_id": "x", "value": "like"}).status_code,
            401,
        )


class FeedbackValidationTests(unittest.TestCase):
    def setUp(self):
        self._orig = qa_history.record_feedback
        self.calls = []

        async def _fake(qa_id, value):
            self.calls.append((qa_id, value))
            return True

        qa_history.record_feedback = _fake

    def tearDown(self):
        qa_history.record_feedback = self._orig

    def test_bad_value_rejected_before_record(self):
        # value 非 like/dislike → 400，且不呼叫 record_feedback（驗證擋在前）
        r = _authed().post("/api/feedback", json={"qa_id": "q1", "value": "love"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.calls, [])

    def test_valid_value_records(self):
        r = _authed().post("/api/feedback", json={"qa_id": "q1", "value": "dislike"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"ok": True})
        self.assertEqual(self.calls, [("q1", "dislike")])

    def test_none_accepted_as_clear(self):
        # 'none'＝再點一次已亮起的那顆＝取消。若端點仍只收 like/dislike，取消會回 400，
        # 而前端的回饋失敗刻意不打擾使用者——按鈕看起來熄了，DB 裡的評價卻還在。
        r = _authed().post("/api/feedback", json={"qa_id": "q1", "value": "none"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.calls, [("q1", "none")])


class ConversationsListTests(unittest.TestCase):
    def setUp(self):
        self._orig = qa_history.list_conversations

    def tearDown(self):
        qa_history.list_conversations = self._orig

    def test_list_passes_through_service(self):
        async def _fake(limit):
            return [{"conversation_id": "c1", "limit": limit}]

        qa_history.list_conversations = _fake
        r = _authed().get("/api/conversations?limit=7")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), [{"conversation_id": "c1", "limit": 7}])


class ConversationDeleteHttpTests(unittest.TestCase):
    """刪對話串的兩支端點都走 HTTP 層驗。

    `_delete_conversation_and_files` 是新加的**輔助函式**——夾在裝飾器與 handler
    之間會讓裝飾器套到它身上，端點對正常請求回 422（2026-07-28 實際事故）。
    直接呼叫 handler 物件的測試看不到那條縫，所以這裡一定得走 TestClient。
    """

    def setUp(self):
        report_runs._DELETED_CONVERSATIONS.clear()
        report_runs._DELETION_GENERATIONS.clear()
        report_runs._PENDING_DELETION_LEASES.clear()
        report_runs._COMPLETED_DELETIONS.clear()
        self._orig = (qa_history.delete_conversation, qa_history.deleted_pdf_paths)
        self.order: list[str] = []
        self.unlinked: list[str] = []

        async def _fake_paths(cid):
            self.order.append(f"paths:{cid}")
            return ["/tmp/report-a.pdf", "/tmp/report-b.pdf"]

        async def _fake_delete(cid):
            self.order.append(f"delete:{cid}")
            return True

        qa_history.deleted_pdf_paths = _fake_paths
        qa_history.delete_conversation = _fake_delete

        self._orig_unlink = qa_history.Path.unlink

        def _fake_unlink(p, missing_ok=False):
            self.unlinked.append(str(p))

        qa_history.Path.unlink = _fake_unlink

    def tearDown(self):
        qa_history.delete_conversation, qa_history.deleted_pdf_paths = self._orig
        qa_history.Path.unlink = self._orig_unlink
        report_runs._DELETED_CONVERSATIONS.clear()
        report_runs._DELETION_GENERATIONS.clear()
        report_runs._PENDING_DELETION_LEASES.clear()
        report_runs._COMPLETED_DELETIONS.clear()

    def test_delete_verb_returns_ok_and_cleans_files(self):
        r = _authed().delete("/api/conversations/c1")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json(), {"ok": True})
        self.assertEqual(
            self.unlinked, ["/tmp/report-a.pdf", "/tmp/report-b.pdf"]
        )

    def test_post_alias_behaves_the_same(self):
        r = _authed().post("/api/conversations/c1/delete")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json(), {"ok": True})
        self.assertEqual(len(self.unlinked), 2)

    def test_paths_are_queried_before_the_delete(self):
        """順序不是風格問題：列刪掉之後 pdf_path 就查不到了。"""
        _authed().delete("/api/conversations/c1")
        self.assertEqual(self.order, ["paths:c1", "delete:c1"])

    def test_nothing_deleted_means_no_file_removal(self):
        """DB 沒刪到任何列時不得刪檔——那些檔案屬於還存在的對話串。"""

        async def _no_rows(cid):
            self.order.append(f"delete:{cid}")
            return False

        qa_history.delete_conversation = _no_rows
        r = _authed().delete("/api/conversations/c1")
        self.assertEqual(r.json(), {"ok": False})
        self.assertEqual(self.unlinked, [])

    def test_committed_delete_keeps_report_endpoint_tombstoned(self):
        """Only a committed DB deletion gets the durable in-process 404 guard."""
        conversation_id = "123e4567-e89b-12d3-a456-426614174098"
        client = _authed()
        self.assertEqual(client.delete(f"/api/conversations/{conversation_id}").json(), {"ok": True})
        response = client.post(
            "/api/report",
            json={"question": "重試已刪除的對話", "conversation_id": conversation_id},
        )
        self.assertEqual(response.status_code, 404)

    def test_unlink_failure_does_not_fail_the_request(self):
        """DB 已提交而檔案殘留是可容忍的；反過來（檔案刪了 DB 沒刪）才是永久 500。"""

        def _boom(p, missing_ok=False):
            raise OSError("read-only fs")

        qa_history.Path.unlink = _boom
        with self.assertLogs("web.routers.qa_history", level="WARNING") as cm:
            r = _authed().delete("/api/conversations/c1")
        self.assertEqual(r.json(), {"ok": True})
        self.assertTrue(any("PDF" in m for m in cm.output), cm.output)

    def test_requires_login(self):
        c = TestClient(app, follow_redirects=False, base_url="http://127.0.0.1")
        self.assertEqual(c.delete("/api/conversations/c1").status_code, 401)
        self.assertEqual(
            c.post("/api/conversations/c1/delete").status_code, 401
        )
        self.assertEqual(self.order, [], "未認證不得碰到服務層")


class ConversationR2CleanupTests(unittest.TestCase):
    """R2 清理必須在 DB commit 後，再以擷取當時的 immutable identity 驗證。"""

    def setUp(self):
        report_runs._DELETED_CONVERSATIONS.clear()
        report_runs._DELETION_GENERATIONS.clear()
        report_runs._PENDING_DELETION_LEASES.clear()
        report_runs._COMPLETED_DELETIONS.clear()
        self.events: list[str] = []
        self.object_refs = []
        self._orig = (
            qa_history.delete_conversation,
            qa_history.deleted_pdf_paths,
            qa_history.deleted_pdf_object_keys,
            qa_history.get_object_storage,
        )

        async def paths(_cid):
            return []

        async def object_keys(_cid):
            return self.object_refs

        async def delete(_cid):
            self.events.append("db-commit")
            return True

        qa_history.deleted_pdf_paths = paths
        qa_history.deleted_pdf_object_keys = object_keys
        qa_history.delete_conversation = delete

    def tearDown(self):
        (
            qa_history.delete_conversation,
            qa_history.deleted_pdf_paths,
            qa_history.deleted_pdf_object_keys,
            qa_history.get_object_storage,
        ) = self._orig
        report_runs._DELETED_CONVERSATIONS.clear()
        report_runs._DELETION_GENERATIONS.clear()
        report_runs._PENDING_DELETION_LEASES.clear()
        report_runs._COMPLETED_DELETIONS.clear()

    def test_cross_report_or_corrupt_pointer_never_calls_delete(self):
        report_id = "123e4567-e89b-12d3-a456-426614174000"
        key = generated_object_key_for_sha("other-report", "a" * 64)
        self.object_refs = [
            qa_history.DeletedGeneratedObject(report_id, "base", None, key),
        ]

        class _Storage:
            enabled = True

            def head_object(self, _key):
                raise AssertionError("cross-report key must not be HEADed")

            def delete(self, _key):
                raise AssertionError("cross-report key must never be deleted")

        qa_history.get_object_storage = lambda: _Storage()
        response = _authed().delete("/api/conversations/c1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.events, ["db-commit"])

    def test_metadata_mismatch_or_missing_never_calls_delete(self):
        report_id = "123e4567-e89b-12d3-a456-426614174000"
        key = generated_object_key_for_sha(report_id, "a" * 64)
        self.object_refs = [qa_history.DeletedGeneratedObject(report_id, "base", None, key)]

        for metadata in ({}, {"sha256": "b" * 64}):
            with self.subTest(metadata=metadata):
                deleted = []

                class _Storage:
                    enabled = True

                    def head_object(self, _key):
                        return {"Metadata": metadata}

                    def delete(self, seen_key):
                        deleted.append(seen_key)

                qa_history.get_object_storage = lambda: _Storage()
                response = _authed().delete("/api/conversations/c1")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(deleted, [])

    def test_valid_base_and_rendition_are_deleted_after_db_commit(self):
        report_id = "123e4567-e89b-12d3-a456-426614174000"
        base_sha = "a" * 64
        rendition_sha = "b" * 64
        base_key = generated_object_key_for_sha(report_id, base_sha)
        rendition_key = generated_object_key_for_sha(report_id, rendition_sha, "ren-1")
        self.object_refs = [
            qa_history.DeletedGeneratedObject(report_id, "base", None, base_key),
            qa_history.DeletedGeneratedObject(report_id, "rendition", "ren-1", rendition_key),
        ]
        events = self.events

        class _Storage:
            enabled = True

            def head_object(self, key):
                events.append(f"head:{key}")
                return {"Metadata": {"sha256": base_sha if key == base_key else rendition_sha}}

            def delete(self, key):
                events.append(f"delete:{key}")

        storage = _Storage()
        qa_history.get_object_storage = lambda: storage
        response = _authed().delete("/api/conversations/c1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.events[0], "db-commit")
        self.assertEqual(
            self.events,
            ["db-commit", f"head:{base_key}", f"delete:{base_key}", f"head:{rendition_key}", f"delete:{rendition_key}"],
        )

    def test_confirmed_missing_object_is_harmless(self):
        report_id = "123e4567-e89b-12d3-a456-426614174000"
        key = generated_object_key_for_sha(report_id, "a" * 64)
        self.object_refs = [qa_history.DeletedGeneratedObject(report_id, "base", None, key)]
        deleted = []

        class _Storage:
            enabled = True

            def head_object(self, seen_key):
                raise ObjectNotFound(seen_key)

            def delete(self, seen_key):
                deleted.append(seen_key)

        qa_history.get_object_storage = lambda: _Storage()
        response = _authed().delete("/api/conversations/c1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(deleted, [])

    def test_head_service_failure_leaves_reconcilable_orphan(self):
        report_id = "123e4567-e89b-12d3-a456-426614174000"
        key = generated_object_key_for_sha(report_id, "a" * 64)
        self.object_refs = [qa_history.DeletedGeneratedObject(report_id, "base", None, key)]
        deleted = []

        class _Storage:
            enabled = True

            def head_object(self, _key):
                raise ObjectStorageError("NoSuchBucket")

            def delete(self, seen_key):
                deleted.append(seen_key)

        qa_history.get_object_storage = lambda: _Storage()
        with self.assertLogs("web.routers.qa_history", level="WARNING"):
            response = _authed().delete("/api/conversations/c1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(deleted, [])


class ConversationDeleteTombstoneRollbackTests(unittest.IsolatedAsyncioTestCase):
    """A failed HTTP deletion must not leave the in-process report-run tombstone behind."""

    CONVERSATION = "123e4567-e89b-12d3-a456-426614174099"

    def setUp(self):
        report_runs._RUNS.clear()
        report_runs._BY_KEY.clear()
        report_runs._DELETED_CONVERSATIONS.clear()
        report_runs._DELETION_GENERATIONS.clear()
        report_runs._PENDING_DELETION_LEASES.clear()
        report_runs._COMPLETED_DELETIONS.clear()
        self._orig = (
            qa_history.deleted_pdf_paths,
            qa_history.deleted_pdf_object_keys,
            qa_history.delete_conversation,
        )

        async def paths(_cid):
            return []

        async def object_keys(_cid):
            return []

        qa_history.deleted_pdf_paths = paths
        qa_history.deleted_pdf_object_keys = object_keys

    async def asyncTearDown(self):
        (
            qa_history.deleted_pdf_paths,
            qa_history.deleted_pdf_object_keys,
            qa_history.delete_conversation,
        ) = self._orig
        await report_runs.shutdown()

    @staticmethod
    def _events():
        async def events():
            yield ("done", {})

        return events()

    async def _assert_report_creation_allowed(self):
        run_id, is_new = report_runs.start_or_attach(
            question="retry", conversation_id=self.CONVERSATION, qa_id=None, template_id=None,
            locale="zh-Hant", make_events=self._events,
        )
        self.assertTrue(is_new)
        await report_runs._RUNS[run_id].task

    async def test_false_delete_releases_tombstone_and_allows_report_creation(self):
        async def no_rows(_cid):
            return False

        qa_history.delete_conversation = no_rows
        self.assertFalse(await qa_history._delete_conversation_and_files(self.CONVERSATION))
        self.assertFalse(report_runs.conversation_deleted(self.CONVERSATION))
        await self._assert_report_creation_allowed()

    async def test_delete_exception_releases_tombstone(self):
        async def boom(_cid):
            raise RuntimeError("db unavailable")

        qa_history.delete_conversation = boom
        with self.assertRaisesRegex(RuntimeError, "db unavailable"):
            await qa_history._delete_conversation_and_files(self.CONVERSATION)
        self.assertFalse(report_runs.conversation_deleted(self.CONVERSATION))
        await self._assert_report_creation_allowed()

    async def test_cancelled_delete_releases_tombstone(self):
        entered = asyncio.Event()
        never = asyncio.Event()

        async def blocked(_cid):
            entered.set()
            await never.wait()
            return True

        qa_history.delete_conversation = blocked
        task = asyncio.create_task(qa_history._delete_conversation_and_files(self.CONVERSATION))
        await entered.wait()
        self.assertTrue(report_runs.conversation_deleted(self.CONVERSATION))
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(report_runs.conversation_deleted(self.CONVERSATION))
        await self._assert_report_creation_allowed()

    async def test_cancelling_newer_overlapping_delete_keeps_older_lease_tombstoned(self):
        entered = [asyncio.Event(), asyncio.Event()]
        never = asyncio.Event()
        calls = 0

        async def blocked(_cid):
            nonlocal calls
            position = calls
            calls += 1
            entered[position].set()
            await never.wait()
            return True

        qa_history.delete_conversation = blocked
        first = asyncio.create_task(qa_history._delete_conversation_and_files(self.CONVERSATION))
        await entered[0].wait()
        second = asyncio.create_task(qa_history._delete_conversation_and_files(self.CONVERSATION))
        await entered[1].wait()

        second.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await second
        self.assertTrue(report_runs.conversation_deleted(self.CONVERSATION))

        first.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await first
        self.assertFalse(report_runs.conversation_deleted(self.CONVERSATION))
        await self._assert_report_creation_allowed()

    async def test_confirmed_delete_keeps_tombstone_and_cannot_be_rolled_back(self):
        async def committed(_cid):
            return True

        qa_history.delete_conversation = committed
        self.assertTrue(await qa_history._delete_conversation_and_files(self.CONVERSATION))
        self.assertTrue(report_runs.conversation_deleted(self.CONVERSATION))
        with self.assertRaises(ValueError):
            await self._assert_report_creation_allowed()


if __name__ == "__main__":
    unittest.main()


class ReportOfferEndpointTests(unittest.TestCase):
    """POST /api/qa/{qa_id}/report-offer：研報邀請的收合／還原（跨重整持久）。"""

    QA = "123e4567-e89b-42d3-a456-426614174000"

    def _patched(self):
        from web import deps
        calls = []

        async def _fake(qa_id, declined):
            calls.append((qa_id, declined))
            return True

        return deps, deps.set_report_offer_declined, _fake, calls

    def test_decline_and_restore_forwarded(self):
        deps, orig, fake, calls = self._patched()
        deps.set_report_offer_declined = fake
        try:
            c = _authed()
            r1 = c.post(f"/api/qa/{self.QA}/report-offer", json={"action": "decline"})
            r2 = c.post(f"/api/qa/{self.QA}/report-offer", json={"action": "restore"})
        finally:
            deps.set_report_offer_declined = orig
        self.assertEqual((r1.status_code, r2.status_code), (200, 200))
        self.assertTrue(r1.json()["ok"] and r2.json()["ok"])
        self.assertEqual(calls, [(self.QA, True), (self.QA, False)])

    def test_invalid_action_400(self):
        c = _authed()
        r = c.post(f"/api/qa/{self.QA}/report-offer", json={"action": "maybe"})
        self.assertEqual(r.status_code, 400)

    def test_invalid_uuid_404(self):
        c = _authed()
        r = c.post("/api/qa/not-a-uuid/report-offer", json={"action": "decline"})
        self.assertEqual(r.status_code, 404)
