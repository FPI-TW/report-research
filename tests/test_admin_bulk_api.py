"""管理後台的批次操作（HTTP 層）：研報批次隱藏／恢復。

服務層換成替身（規則與 SQL 對真 DB 的驗證在 `tests/test_report_visibility_db.py`）。

驗：未登入 401、一般使用者 403、缺 scope 403 `missing_scope`、空清單與超過上限 422、隱藏缺原因 422、
跨站 POST 被 CSRF 擋；逐筆結果與彙總、成功才 commit、失敗 rollback。
"""

from __future__ import annotations

import dataclasses
import unittest
from datetime import datetime, timezone

from fake_accounts import FakeAccounts, install
from fastapi.testclient import TestClient

from app.services import visibility
from web import auth, deps
from web.server import app

ADMIN_PW = "root-password-1"
USER_PW = "alice-password-1"
WHEN = datetime(2026, 10, 7, 9, 0, tzinfo=timezone.utc)
H_OK = "a" * 64
H_DRAFT = "b" * 64
H_MISSING = "c" * 64


def _client():
    return TestClient(app, follow_redirects=False, base_url="http://127.0.0.1")


class _Store(FakeAccounts):
    """`limited` 是管理員但被拿掉 reports.manage。"""

    def _user(self, row, elevated_until=None):
        user = super()._user(row, elevated_until)
        if user.username == "limited":
            return dataclasses.replace(user, scopes=user.scopes - {"reports.manage"})
        return user


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
    """deps.report_visibility 的替身：原因規則沿用服務層的 `_check_reason`，逐筆結果依 file_hash 決定。"""

    def __init__(self):
        self.calls: list = []
        self.boom: Exception | None = None

    async def bulk_set_visibility(self, session, file_hashes, *, hidden, reason, actor_id):
        self.calls.append((list(file_hashes), hidden, reason, actor_id))
        visibility._check_reason(hidden, reason)
        if self.boom is not None:
            raise self.boom
        out = []
        for h in dict.fromkeys(file_hashes):
            if h == H_DRAFT:
                out.append(visibility.BulkVisibilityResult(h, error=visibility.ReportIsDraftError("草稿")))
            elif h == H_MISSING or not visibility.valid_file_hash(h):
                out.append(visibility.BulkVisibilityResult(h, error=visibility.ReportNotFoundError("研報不存在")))
            else:
                out.append(visibility.BulkVisibilityResult(h, state=visibility.VisibilityState(
                    file_hash=h, hidden=hidden, reason=reason, updated_by="root", updated_at=WHEN)))
        return out


class _Base(unittest.TestCase):
    def setUp(self):
        auth._FAILS.clear()
        self.store = _Store()
        self.root = self.store.add_user("root", ADMIN_PW, "admin", is_super=True)
        self.alice = self.store.add_user("alice", USER_PW, "user")
        self.store.add_user("limited", ADMIN_PW, "admin")
        self._ctx = install(self.store)
        self._ctx.__enter__()
        self.addCleanup(self._ctx.__exit__, None, None, None)
        self.vis = _FakeVisibility()
        self.tx: list[str] = []
        orig = (deps.report_visibility, deps.SessionFactory)
        deps.report_visibility, deps.SessionFactory = self.vis, (lambda: _Session(self.tx))
        self.addCleanup(lambda: (setattr(deps, "report_visibility", orig[0]), setattr(deps, "SessionFactory", orig[1])))
        self.addCleanup(auth._FAILS.clear)

    def login(self, username="root", password=ADMIN_PW, *, elevate=False):
        client = _client()
        self.assertEqual(client.post("/login", data={"username": username, "password": password}).status_code, 303)
        if elevate:
            self.assertEqual(client.post("/api/admin/elevate", json={"password": password}).status_code, 200)
        return client


class ReportsBulkTests(_Base):
    URL = "/api/admin/reports/bulk-visibility"

    def test_gates(self):
        body = {"action": "hide", "file_hashes": [H_OK], "reason": "x"}
        self.assertEqual(_client().post(self.URL, json=body).status_code, 401)
        self.assertEqual(self.login("alice", USER_PW).post(self.URL, json=body).status_code, 403)
        r = self.login("limited").post(self.URL, json=body)
        self.assertEqual((r.status_code, r.json()["code"]), (403, "missing_scope"))
        self.assertEqual(self.vis.calls, [])

    def test_cross_site_post_is_rejected(self):
        r = self.login().post(self.URL, json={"action": "restore", "file_hashes": [H_OK]},
                              headers={"Origin": "http://evil.example"})
        self.assertEqual((r.status_code, r.json()["code"]), (403, "csrf_rejected"))
        self.assertEqual(self.vis.calls, [])

    def test_body_validation(self):
        admin = self.login()
        cases = [
            {"action": "hide", "file_hashes": [], "reason": "x"},
            {"action": "hide", "file_hashes": [H_OK] * (visibility.BULK_MAX_REPORTS + 1), "reason": "x"},
            {"action": "delete", "file_hashes": [H_OK]},
            {"action": "hide", "file_hashes": ["a" * 65], "reason": "x"},
            {"file_hashes": [H_OK]},
        ]
        for body in cases:
            with self.subTest(body=str(body)[:60]):
                self.assertEqual(admin.post(self.URL, json=body).status_code, 422)
        self.assertEqual(self.vis.calls, [])

    def test_hide_requires_reason(self):
        admin = self.login()
        for reason in (None, "   ", "長" * 501):
            with self.subTest(reason=str(reason)[:5]):
                self.tx[:] = []
                r = admin.post(self.URL, json={"action": "hide", "file_hashes": [H_OK], "reason": reason})
                self.assertEqual((r.status_code, r.json()["code"]), (422, "invalid_input"), r.text)
                self.assertEqual(self.tx, ["rollback"])

    def test_per_item_results_and_commit(self):
        r = self.login().post(self.URL, json={
            "action": "hide", "file_hashes": [H_OK, H_DRAFT, H_MISSING, "not-a-hash", H_OK], "reason": "版權疑慮",
        })
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual((body["action"], body["requested"], body["ok"], body["skipped"]), ("hide", 4, 1, 3))
        by = {i["file_hash"]: i for i in body["results"]}
        self.assertEqual(by[H_OK], {"file_hash": H_OK, "status": "ok", "code": None, "detail": None, "hidden": True})
        self.assertEqual((by[H_DRAFT]["status"], by[H_DRAFT]["code"]), ("skipped", "report_is_draft"))
        self.assertEqual(by[H_MISSING]["code"], "not_found")
        self.assertEqual(by["not-a-hash"]["code"], "not_found")
        self.assertEqual(self.vis.calls[0][1:], (True, "版權疑慮", self.root))
        self.assertEqual(self.tx, ["commit"])

    def test_restore_without_reason(self):
        r = self.login().post(self.URL, json={"action": "restore", "file_hashes": [H_OK]})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["results"][0]["hidden"], False)
        self.assertEqual(self.vis.calls[0][1:], (False, None, self.root))

    def test_unexpected_failure_rolls_back_everything(self):
        self.vis.boom = RuntimeError("稽核寫不進去")
        client = TestClient(app, follow_redirects=False, base_url="http://127.0.0.1", raise_server_exceptions=False)
        self.assertEqual(client.post("/login", data={"username": "root", "password": ADMIN_PW}).status_code, 303)
        r = client.post(self.URL, json={"action": "restore", "file_hashes": [H_OK]})
        self.assertEqual(r.status_code, 500)
        self.assertEqual(self.tx, ["rollback"])


class ErrorCodeContractTests(unittest.TestCase):
    def test_visibility_error_codes_match_single_endpoint(self):
        from web.routers import admin_reports

        for exc in (visibility.ReportNotFoundError("x"), visibility.InvalidReasonError("x"),
                    visibility.ReportIsDraftError("x")):
            with self.subTest(cls=type(exc).__name__):
                self.assertEqual(admin_reports._http_error(exc).code, exc.code)


if __name__ == "__main__":
    unittest.main()
