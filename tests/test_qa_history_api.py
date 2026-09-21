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


class ConversationDetailTests(unittest.TestCase):
    def setUp(self):
        self._orig = qa_history.get_conversation

    def tearDown(self):
        qa_history.get_conversation = self._orig

    def test_detail_passes_through_service(self):
        cid = "55555555-5555-4555-8555-555555555555"

        async def _fake(conversation_id):
            return [{"conversation_id": conversation_id}]

        qa_history.get_conversation = _fake
        r = _authed().get(f"/api/conversations/{cid}")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), [{"conversation_id": cid}])

    def test_malformed_id_is_404_without_touching_service(self):
        """conversation_id 進 uuid 欄位的 WHERE；非法字串不擋會變 500。"""
        async def _boom(conversation_id):
            raise AssertionError("非法 id 不該進到 service")

        qa_history.get_conversation = _boom
        r = _authed().get("/api/conversations/not-a-uuid")
        self.assertEqual(r.status_code, 404)


class ConversationDeleteHttpTests(unittest.TestCase):
    """刪對話串的兩支端點都走 HTTP 層驗。

    `_delete_conversation_and_files` 是**輔助函式**——夾在裝飾器與 handler
    之間會讓裝飾器套到它身上，端點對正常請求回 422（2026-07-28 實際事故）。
    直接呼叫 handler 物件的測試看不到那條縫，所以這裡一定得走 TestClient。
    """

    def setUp(self):
        self._orig = qa_history.delete_conversation
        self.order: list[str] = []

        async def _fake_delete(cid):
            self.order.append(f"delete:{cid}")
            return True

        qa_history.delete_conversation = _fake_delete

    def tearDown(self):
        qa_history.delete_conversation = self._orig

    def test_delete_verb_returns_ok(self):
        r = _authed().delete("/api/conversations/c1")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json(), {"ok": True})
        self.assertEqual(self.order, ["delete:c1"])

    def test_post_alias_behaves_the_same(self):
        r = _authed().post("/api/conversations/c1/delete")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json(), {"ok": True})
        self.assertEqual(self.order, ["delete:c1"])

    def test_nothing_deleted_reports_false(self):
        async def _no_rows(cid):
            self.order.append(f"delete:{cid}")
            return False

        qa_history.delete_conversation = _no_rows
        r = _authed().delete("/api/conversations/c1")
        self.assertEqual(r.json(), {"ok": False})

    def test_requires_login(self):
        c = TestClient(app, follow_redirects=False, base_url="http://127.0.0.1")
        self.assertEqual(c.delete("/api/conversations/c1").status_code, 401)
        self.assertEqual(
            c.post("/api/conversations/c1/delete").status_code, 401
        )
        self.assertEqual(self.order, [], "未認證不得碰到服務層")


if __name__ == "__main__":
    unittest.main()
