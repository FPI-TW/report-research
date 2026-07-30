import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


class ValidUuidTests(unittest.TestCase):
    def test_accepts_uuid_rejects_garbage(self):
        from web.server import _valid_uuid

        self.assertTrue(_valid_uuid("123e4567-e89b-12d3-a456-426614174000"))
        self.assertFalse(_valid_uuid("../../etc/passwd"))
        self.assertFalse(_valid_uuid(""))
        self.assertFalse(_valid_uuid(None))


# web.auth 匯入時即讀取共用帳密 (fail-closed)，須在匯入前設好測試用值。
import os  # noqa: E402

os.environ.setdefault("REPORT_MARK_ACCESS_USERNAME", "tester")
os.environ.setdefault("REPORT_MARK_ACCESS_PASSWORD", "testpass")
os.environ.setdefault("REPORT_MARK_SESSION_SECRET", "fixed-test-secret-0123456789")

from fastapi.testclient import TestClient  # noqa: E402

from web.server import app  # noqa: E402


def _client():
    return TestClient(app, follow_redirects=False, base_url="http://127.0.0.1")


def _authed_client():
    client = _client()
    r = client.post("/login", data={"username": "tester", "password": "testpass"})
    assert r.status_code == 303, f"login failed: {r.status_code}"
    return client


class ReportEndpointGuardTests(unittest.TestCase):
    """早期驗證 qa_id / conversation_id 格式（400 在生成前回傳）。"""

    def test_invalid_qa_id_returns_400(self):
        client = _authed_client()
        r = client.post(
            "/api/report",
            json={"question": "x", "qa_id": "not-a-uuid"},
        )
        self.assertEqual(r.status_code, 400)
        self.assertIn("qa_id", r.json().get("detail", ""))

    def test_invalid_conversation_id_returns_400(self):
        client = _authed_client()
        r = client.post(
            "/api/report",
            json={"question": "x", "conversation_id": "bad-id!!"},
        )
        self.assertEqual(r.status_code, 400)
        self.assertIn("conversation_id", r.json().get("detail", ""))

    def test_none_ids_pass_guard(self):
        """None (omitted) ids は guard を通過する。生成は起動しないよう question を空にして別エラーで止める。"""
        client = _authed_client()
        # question 空 → 400「question 不可為空」で止まる（生成前）
        r = client.post("/api/report", json={"question": ""})
        # guard は通過済み（400 は question バリデーション起因）
        self.assertEqual(r.status_code, 400)
        self.assertNotIn("qa_id", r.json().get("detail", ""))

    def test_empty_string_qa_id_returns_400(self):
        client = _authed_client()
        r = client.post(
            "/api/report",
            json={"question": "x", "qa_id": ""},
        )
        self.assertEqual(r.status_code, 400)
        self.assertIn("qa_id", r.json().get("detail", ""))


if __name__ == "__main__":
    unittest.main()
