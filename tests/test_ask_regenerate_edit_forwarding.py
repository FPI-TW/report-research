"""鎖定 /api/ask 轉發 regenerate_of/edit_of 至 answer_question 的縫（M3 最終審查 Critical finding）。

根因：AskRequest 先前未宣告這兩欄（Pydantic 預設 extra='ignore' 靜默捨棄），
且 ask() 呼叫 answer_question(...) 也沒轉發 → 重生/編輯兩條後端正解在真實
HTTP 流程整條失效（單元測試測不到，因為它們直接呼叫 answer_question 本體）。
"""

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

_VALID_UUID = "13c3af97-b458-4108-836d-654a89e76fb7"


def _authed_client():
    c = TestClient(server.app, follow_redirects=False, base_url="http://127.0.0.1")
    r = c.post("/login", data={"username": "tester", "password": "testpass"})
    assert r.status_code == 303, f"login failed: {r.status_code}"
    return c


class AskForwardingTests(unittest.TestCase):
    def _patched_answer_question(self, captured):
        async def fake_aq(question, **kwargs):
            captured["question"] = question
            captured.update(kwargs)
            yield ("done", {"qa_id": "x", "conversation_id": "c"})

        return fake_aq

    def test_ask_forwards_regenerate_of(self):
        client = _authed_client()
        captured: dict = {}
        orig = server.answer_question
        server.answer_question = self._patched_answer_question(captured)
        try:
            resp = client.post(
                "/api/ask",
                json={"question": "台積電展望", "regenerate_of": _VALID_UUID},
            )
        finally:
            server.answer_question = orig

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(captured.get("regenerate_of"), _VALID_UUID)
        self.assertIsNone(captured.get("edit_of"))

    def test_ask_forwards_edit_of(self):
        client = _authed_client()
        captured: dict = {}
        orig = server.answer_question
        server.answer_question = self._patched_answer_question(captured)
        try:
            resp = client.post(
                "/api/ask",
                json={"question": "台積電展望", "edit_of": _VALID_UUID},
            )
        finally:
            server.answer_question = orig

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(captured.get("edit_of"), _VALID_UUID)
        self.assertIsNone(captured.get("regenerate_of"))

    def test_ask_invalid_regenerate_of_returns_400(self):
        client = _authed_client()
        resp = client.post(
            "/api/ask",
            json={"question": "台積電展望", "regenerate_of": "not-a-uuid"},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("regenerate_of", resp.json().get("detail", ""))

    def test_ask_invalid_edit_of_returns_400(self):
        client = _authed_client()
        resp = client.post(
            "/api/ask",
            json={"question": "台積電展望", "edit_of": "not-a-uuid"},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("edit_of", resp.json().get("detail", ""))


if __name__ == "__main__":
    unittest.main()
