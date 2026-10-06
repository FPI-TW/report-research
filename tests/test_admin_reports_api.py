"""研報隱藏／恢復 API（/api/admin/reports*，HTTP 層）。SQL 與交易對真 DB 的驗證在 test_report_visibility_db.py。

重點：一般使用者打不到（403）、參數原樣轉給服務層、服務層的錯誤對到 404／400、成功才 commit、
失敗 rollback（稽核與可見性同一筆交易，不能只留一半）。
"""

from __future__ import annotations

import unittest
from datetime import date, datetime, timezone

from fake_accounts import FakeAccounts, install
from fastapi.testclient import TestClient

from app.services import visibility
from web import auth, deps
from web.server import app

ADMIN_PW = "root-password-1"
USER_PW = "alice-password-1"
HASH = "a" * 64
WHEN = datetime(2026, 10, 6, 9, 30, tzinfo=timezone.utc)


def _client():
    return TestClient(app, follow_redirects=False, base_url="http://127.0.0.1")


class _Session:
    def __init__(self, log):
        self.log = log

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def commit(self):
        self.log.append("commit")

    async def rollback(self):
        self.log.append("rollback")


class _FakeVisibility:
    """deps.report_visibility 的替身：記下呼叫，回固定結果或拋指定錯誤。"""

    def __init__(self):
        self.calls: list = []
        self.error: Exception | None = None

    async def list_reports(self, session, *, q, hidden, limit, offset):
        self.calls.append(("list", q, hidden, limit, offset))
        row = visibility.AdminReportRow(
            report_id="11111111-1111-1111-1111-111111111111", file_hash=HASH, file_name="x.pdf",
            title="台積電法說", source="元大", market="TW", report_date=date(2026, 10, 1), created_at=WHEN,
            hidden=True, reason="版權疑慮", updated_by="root", updated_at=WHEN,
        )
        return 3, [row]

    async def set_visibility(self, session, file_hash, *, hidden, reason, actor_id):
        self.calls.append(("set", file_hash, hidden, reason, actor_id))
        if self.error is not None:
            raise self.error
        return visibility.VisibilityState(
            file_hash=file_hash, hidden=hidden, reason=reason, updated_by="root", updated_at=WHEN,
        )


class AdminReportsApiTests(unittest.TestCase):
    def setUp(self):
        auth._FAILS.clear()
        self.store = FakeAccounts()
        self.root = self.store.add_user("root", ADMIN_PW, "admin")
        self.store.add_user("alice", USER_PW, "user")
        self._ctx = install(self.store)
        self._ctx.__enter__()
        self.fake = _FakeVisibility()
        self.tx: list[str] = []
        self._orig = (deps.report_visibility, deps.SessionFactory)
        deps.report_visibility = self.fake
        deps.SessionFactory = lambda: _Session(self.tx)

    def tearDown(self):
        deps.report_visibility, deps.SessionFactory = self._orig
        self._ctx.__exit__(None, None, None)
        auth._FAILS.clear()

    def _login(self, username, password):
        client = _client()
        r = client.post("/login", data={"username": username, "password": password})
        self.assertEqual(r.status_code, 303)
        return client

    def test_user_gets_403_and_nothing_happens(self):
        user = self._login("alice", USER_PW)
        self.assertEqual(user.get("/api/admin/reports").status_code, 403)
        r = user.put(f"/api/admin/reports/{HASH}/visibility", json={"hidden": True, "reason": "x"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.fake.calls, [])

    def test_unauthenticated_gets_401(self):
        self.assertEqual(_client().get("/api/admin/reports").status_code, 401)

    def test_list_passes_filters_and_pages(self):
        r = self._login("root", ADMIN_PW).get(
            "/api/admin/reports", params={"q": "台積電", "hidden": "true", "limit": 1, "offset": 0},
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.fake.calls, [("list", "台積電", True, 1, 0)])
        body = r.json()
        self.assertEqual((body["total"], body["has_more"], body["next_offset"]), (3, True, 1))
        item = body["items"][0]
        self.assertEqual(item["file_hash"], HASH)
        self.assertTrue(item["hidden"])
        self.assertEqual(item["hidden_reason"], "版權疑慮")
        self.assertEqual(item["visibility_updated_by"], "root")
        self.assertEqual(item["report_date"], "2026-10-01")

    def test_list_defaults(self):
        r = self._login("root", ADMIN_PW).get("/api/admin/reports")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.fake.calls, [("list", None, None, 50, 0)])

    def test_hide_commits_with_actor(self):
        r = self._login("root", ADMIN_PW).put(
            f"/api/admin/reports/{HASH}/visibility", json={"hidden": True, "reason": "版權疑慮"},
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.fake.calls, [("set", HASH, True, "版權疑慮", self.root)])
        self.assertEqual(self.tx, ["commit"])
        self.assertEqual(r.json()["hidden"], True)
        self.assertEqual(r.json()["updated_at"], WHEN.isoformat())

    def test_restore_without_reason(self):
        r = self._login("root", ADMIN_PW).put(f"/api/admin/reports/{HASH}/visibility", json={"hidden": False})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.fake.calls, [("set", HASH, False, None, self.root)])

    def test_service_errors_map_to_status_and_roll_back(self):
        admin = self._login("root", ADMIN_PW)
        cases = [
            (visibility.ReportNotFoundError("研報不存在"), 404, "not_found"),
            (visibility.InvalidReasonError("隱藏研報必須填寫原因"), 400, "invalid_input"),
            (visibility.ReportIsDraftError("這份研報是尚未發布的草稿"), 409, "report_is_draft"),
        ]
        for exc, status, code in cases:
            with self.subTest(code=code):
                self.fake.error, self.tx[:] = exc, []
                r = admin.put(f"/api/admin/reports/{HASH}/visibility", json={"hidden": True, "reason": ""})
                self.assertEqual(r.status_code, status, r.text)
                self.assertEqual(r.json()["code"], code)
                self.assertEqual(r.json()["detail"], str(exc))
                self.assertEqual(self.tx, ["rollback"])

    def test_body_validation(self):
        admin = self._login("root", ADMIN_PW)
        self.assertEqual(admin.put(f"/api/admin/reports/{HASH}/visibility", json={}).status_code, 422)
        too_long = {"hidden": True, "reason": "長" * 2001}
        self.assertEqual(admin.put(f"/api/admin/reports/{HASH}/visibility", json=too_long).status_code, 422)
        self.assertEqual(self.fake.calls, [])


if __name__ == "__main__":
    unittest.main()
