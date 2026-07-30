import os
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

# web.auth 匯入時即讀取共用帳密（fail-closed），須在匯入前設好測試用值。
os.environ.setdefault("REPORT_MARK_ACCESS_USERNAME", "tester")
os.environ.setdefault("REPORT_MARK_ACCESS_PASSWORD", "testpass")
os.environ.setdefault("REPORT_MARK_SESSION_SECRET", "fixed-test-secret-0123456789")

from fastapi.testclient import TestClient  # noqa: E402

import web.server as server  # noqa: E402
from web import deps  # noqa: E402


def _authed_client():
    c = TestClient(server.app, follow_redirects=False, base_url="http://127.0.0.1")
    r = c.post("/login", data={"username": "tester", "password": "testpass"})
    assert r.status_code == 303, f"login failed: {r.status_code}"
    return c


class AskStopEndpointTests(unittest.TestCase):
    def test_stop_returns_qa_id(self):
        client = _authed_client()
        called = {}

        async def fake_log(question, partial_answer, **k):
            called["q"] = question
            called["partial"] = partial_answer
            called["kw"] = k
            return "qa-stop-1"

        orig = deps.log_stopped_qa
        deps.log_stopped_qa = fake_log
        try:
            resp = client.post(
                "/api/ask/stop",
                json={"question": "台積電", "partial_answer": "部分答案",
                      "conversation_id": None, "sources": [{"n": 1}],
                      "request_id": "123e4567-e89b-42d3-a456-426614174000"},
            )
        finally:
            deps.log_stopped_qa = orig

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["qa_id"], "qa-stop-1")
        self.assertEqual(called["partial"], "部分答案")
        self.assertEqual(called["q"], "台積電")
        self.assertEqual(called["kw"]["sources"], [{"n": 1}])
        self.assertEqual(called["kw"]["request_id"], "123e4567-e89b-42d3-a456-426614174000")

    def test_invalid_regenerate_of_returns_400(self):
        client = _authed_client()
        resp = client.post(
            "/api/ask/stop",
            json={"question": "台積電", "partial_answer": "部分",
                  "regenerate_of": "not-a-uuid"},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("regenerate_of", resp.json().get("detail", ""))

    def test_stop_rejects_oversized_partial_answer(self):
        client = _authed_client()
        resp = client.post(
            "/api/ask/stop",
            json={"question": "台積電", "partial_answer": "x" * 20_001},
        )
        self.assertEqual(resp.status_code, 422)

    def test_stop_rejects_invalid_request_id(self):
        client = _authed_client()
        resp = client.post(
            "/api/ask/stop",
            json={"question": "台積電", "request_id": "not-a-uuid"},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("request_id", resp.json().get("detail", ""))


if __name__ == "__main__":
    unittest.main()
