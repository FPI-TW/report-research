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


if __name__ == "__main__":
    unittest.main()
