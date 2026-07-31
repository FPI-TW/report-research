# tests/test_qa_history_api.py
"""問答歷史/回饋/對話串端點的煙霧測試。

/api/feedback（尤其其 like/dislike 驗證分支）、/api/history、/api/conversations
在拆到 web/routers/qa_history.py 之前沒有 HTTP 層測試（delete_history 與
qa_versions 另有 test_auth / test_pre_split_guards 覆蓋）。這裡補上認證閘門與
feedback 的驗證分支。

record_feedback 等非 deps 的服務函式定義/匯入於 web.routers.qa_history，故 patch
web.routers.qa_history.X；SessionFactory 等仍走 web.deps。
"""
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


if __name__ == "__main__":
    unittest.main()
