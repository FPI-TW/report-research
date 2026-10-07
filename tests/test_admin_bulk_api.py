"""管理後台的批次操作（HTTP 層）：研報批次隱藏／恢復與帳號批次停用／啟用／強制登出。

研報那支的服務層換成替身（規則與 SQL 對真 DB 的驗證在 `tests/test_report_visibility_db.py`）；帳號那支走
`tests/fake_accounts.py` 的完整語意（它與真 SQL 的一致性由 `tests/test_accounts_db.py` 兩邊各跑一次）。

驗：未登入 401、一般使用者 403、缺 scope 403 `missing_scope`、帳號批次未提升 403 `elevation_required`、
空清單與超過上限 422、跨站 POST 被 CSRF 擋；逐筆結果與彙總、成功才 commit、失敗 rollback；
帳號規則在批次內的組合（自己、最後一位 super admin、非 super 動 super、已停用再停用）與每筆變更各一筆稽核。
"""

from __future__ import annotations

import dataclasses
import unittest
from datetime import datetime, timezone

from fake_accounts import FakeAccounts, install
from fastapi.testclient import TestClient

from app.services import accounts, visibility
from web import auth, deps
from web.routers import admin as admin_router
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
    """`limited` 是管理員但被拿掉 reports.manage 與 accounts.manage。"""

    def _user(self, row, elevated_until=None):
        user = super()._user(row, elevated_until)
        if user.username == "limited":
            return dataclasses.replace(user, scopes=user.scopes - {"reports.manage", "accounts.manage"})
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


class UsersBulkTests(_Base):
    URL = "/api/admin/users/bulk"

    def setUp(self):
        super().setUp()
        self.bob = self.store.add_user("bob", USER_PW, "user")
        self.carol = self.store.add_user("carol", USER_PW, "user", enabled=False)
        self.sid_alice = None

    def audits(self, action):
        return [e for e in self.store.audit if e.action == action]

    def test_gates(self):
        body = {"action": "disable", "user_ids": [self.bob]}
        self.assertEqual(_client().post(self.URL, json=body).status_code, 401)
        self.assertEqual(self.login("alice", USER_PW).post(self.URL, json=body).status_code, 403)
        r = self.login("limited").post(self.URL, json=body)
        self.assertEqual((r.status_code, r.json()["code"]), (403, "missing_scope"))
        r = self.login().post(self.URL, json=body)  # 管理員但未提升
        self.assertEqual((r.status_code, r.json()["code"]), (403, "elevation_required"))
        self.assertTrue(self.store.users[self.bob].enabled)
        self.assertEqual(self.audits("user.disable"), [])

    def test_cross_site_post_is_rejected(self):
        r = self.login(elevate=True).post(self.URL, json={"action": "disable", "user_ids": [self.bob]},
                                          headers={"Origin": "http://evil.example"})
        self.assertEqual((r.status_code, r.json()["code"]), (403, "csrf_rejected"))
        self.assertTrue(self.store.users[self.bob].enabled)

    def test_body_validation(self):
        admin = self.login(elevate=True)
        for body in ({"action": "disable", "user_ids": []},
                     {"action": "disable", "user_ids": [self.bob] * (accounts.BULK_MAX_USERS + 1)},
                     {"action": "delete", "user_ids": [self.bob]},
                     {"action": "disable", "user_ids": ["x" * 65]}):
            with self.subTest(body=str(body)[:50]):
                self.assertEqual(admin.post(self.URL, json=body).status_code, 422)
        self.assertTrue(self.store.users[self.bob].enabled)

    def test_disable_per_item_results_and_audit(self):
        import asyncio

        sid = asyncio.run(self.store.create_session(self.bob, max_age_seconds=3600))
        admin = self.login(elevate=True)
        missing = "00000000-0000-0000-0000-000000000000"
        r = admin.post(self.URL, json={
            "action": "disable", "user_ids": [self.bob, self.carol, self.root, missing, "not-a-uuid", self.bob],
        })
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual((body["requested"], body["ok"], body["unchanged"], body["skipped"]), (5, 1, 1, 3))
        by = {i["user_id"]: i for i in body["results"]}
        self.assertEqual(by[self.bob]["status"], "ok")
        self.assertEqual(by[self.carol]["status"], "unchanged")  # 已停用：與單筆一樣冪等、不寫稽核
        self.assertEqual((by[self.root]["status"], by[self.root]["code"]), ("skipped", "self_lockout"))
        self.assertEqual(by[missing]["code"], "not_found")
        self.assertEqual(by["not-a-uuid"]["code"], "not_found")
        self.assertFalse(self.store.users[self.bob].enabled)
        self.assertIsNone(asyncio.run(self.store.resolve_session(sid)))
        disables = self.audits("user.disable")
        self.assertEqual([e.target_id for e in disables], [self.bob])
        self.assertEqual(disables[0].detail, {"username": "bob", "revoked_sessions": 1, "via": "web_bulk"})
        summary = self.audits("user.bulk_action")
        self.assertEqual(len(summary), 1)
        self.assertEqual(summary[0].target_id, "disable")
        self.assertEqual(summary[0].detail["user_ids"], [self.bob])
        self.assertEqual(summary[0].detail["skipped"], {"not_found": 2, "self_lockout": 1})

    def test_last_super_is_protected_within_the_batch(self):
        """root 以外再兩位 super：root 停用另外兩位都可以（root 還在）；再加 root 自己是 self_lockout。
        改由 CLI 等級的批次（actor=None）一次停用三位 super 時，順序上的最後一位被 last_super 擋下。"""
        import asyncio

        s1 = self.store.add_user("sup1", ADMIN_PW, "admin", is_super=True)
        s2 = self.store.add_user("sup2", ADMIN_PW, "admin", is_super=True)
        r = self.login(elevate=True).post(self.URL, json={"action": "disable", "user_ids": [s1, s2, self.root]})
        self.assertEqual([i["status"] for i in r.json()["results"]], ["ok", "ok", "skipped"])
        self.store.users[s1].enabled = self.store.users[s2].enabled = True
        res = asyncio.run(self.store.bulk_user_action([s1, s2, self.root], "disable", actor_id=None))
        self.assertEqual([(x.status, x.error.code if x.error else None) for x in res],
                         [("ok", None), ("ok", None), ("skipped", "last_super")])
        self.assertTrue(self.store.users[self.root].enabled)

    def test_non_super_cannot_touch_super_in_batch(self):
        self.store.add_user("plain", ADMIN_PW, "admin")
        r = self.login("plain", elevate=True).post(self.URL, json={"action": "logout",
                                                                   "user_ids": [self.root, self.bob]})
        self.assertEqual(r.status_code, 200, r.text)
        by = {i["user_id"]: i for i in r.json()["results"]}
        self.assertEqual((by[self.root]["status"], by[self.root]["code"]), ("skipped", "super_required"))
        self.assertEqual(by[self.bob]["status"], "ok")

    def test_enable_and_logout(self):
        import asyncio

        asyncio.run(self.store.create_session(self.bob, max_age_seconds=3600))
        asyncio.run(self.store.create_session(self.bob, max_age_seconds=3600))
        admin = self.login(elevate=True)
        r = admin.post(self.URL, json={"action": "enable", "user_ids": [self.carol, self.bob]})
        self.assertEqual([i["status"] for i in r.json()["results"]], ["ok", "unchanged"])
        self.assertTrue(self.store.users[self.carol].enabled)
        r = admin.post(self.URL, json={"action": "logout", "user_ids": [self.bob, self.root]})
        by = {i["user_id"]: i for i in r.json()["results"]}
        self.assertEqual((by[self.bob]["status"], by[self.bob]["revoked_sessions"]), ("ok", 2))
        # 批次強制登出不含自己：操作者的 session 還在，這個 client 照樣能用。
        self.assertEqual((by[self.root]["status"], by[self.root]["code"]), ("skipped", "self_lockout"))
        self.assertEqual(admin.get("/api/admin/users").status_code, 200)
        self.assertEqual([e.target_id for e in self.audits("user.force_logout")], [self.bob])

    def test_pending_deletion_cannot_be_enabled(self):
        import asyncio

        asyncio.run(self.store.request_deletion(self.bob, actor_id=self.root))
        r = self.login(elevate=True).post(self.URL, json={"action": "enable", "user_ids": [self.bob]})
        self.assertEqual((r.json()["results"][0]["status"], r.json()["results"][0]["code"]),
                         ("skipped", "deletion_pending"))
        self.assertEqual(self.audits("user.bulk_action"), [], "全部略過時不寫批次摘要")

    def test_unexpected_failure_rolls_back_the_whole_batch(self):
        orig = self.store.update_user
        calls = {"n": 0}

        async def flaky(*a, **kw):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("DB 掛了")
            return await orig(*a, **kw)

        self.store.update_user = flaky
        dave = self.store.add_user("dave", USER_PW, "user")
        client = TestClient(app, follow_redirects=False, base_url="http://127.0.0.1", raise_server_exceptions=False)
        self.assertEqual(client.post("/login", data={"username": "root", "password": ADMIN_PW}).status_code, 303)
        self.assertEqual(client.post("/api/admin/elevate", json={"password": ADMIN_PW}).status_code, 200)
        before = len(self.store.audit)
        r = client.post(self.URL, json={"action": "disable", "user_ids": [self.bob, dave]})
        self.assertEqual(r.status_code, 500)
        self.assertTrue(self.store.users[self.bob].enabled, "第一筆的變更也要回滾")
        self.assertEqual(len(self.store.audit), before, "回滾時稽核也不留")


class ErrorCodeContractTests(unittest.TestCase):
    def test_account_error_codes_match_router_table(self):
        """`AccountError.code`（批次結果與稽核摘要用）與路由層 `_STATUS` 的代碼逐字一致。"""
        for cls, _status, code in admin_router._STATUS:
            with self.subTest(cls=cls.__name__):
                self.assertEqual(cls.code, code)

    def test_visibility_error_codes_match_single_endpoint(self):
        from web.routers import admin_reports

        for exc in (visibility.ReportNotFoundError("x"), visibility.InvalidReasonError("x"),
                    visibility.ReportIsDraftError("x")):
            with self.subTest(cls=type(exc).__name__):
                self.assertEqual(admin_reports._http_error(exc).code, exc.code)


if __name__ == "__main__":
    unittest.main()
