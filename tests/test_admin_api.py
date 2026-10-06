"""管理後台 API（/api/admin/*，HTTP 層）。帳號規則對真 DB 的驗證在 test_accounts_db.py。

重點是「管理動作對被管理者立即生效」與「每個動作都留稽核」：被停用、被強制登出、
被重設密碼的人，下一個請求就進不來；一般使用者打任何 /api/admin/* 都是 403。
"""

from __future__ import annotations

import unittest

from fake_accounts import FakeAccounts, install
from fastapi.testclient import TestClient

from web import auth, dev_mode
from web.server import app

ADMIN_PW = "root-password-1"
USER_PW = "alice-password-1"


def _client():
    return TestClient(app, follow_redirects=False, base_url="http://127.0.0.1")


class AdminApiTests(unittest.TestCase):
    def setUp(self):
        auth._FAILS.clear()
        self.store = FakeAccounts()
        self.root = self.store.add_user("root", ADMIN_PW, "admin")
        self.alice = self.store.add_user("alice", USER_PW, "user")
        self._ctx = install(self.store)
        self._ctx.__enter__()
        self.admin = self._login("root", ADMIN_PW)

    def tearDown(self):
        self._ctx.__exit__(None, None, None)
        auth._FAILS.clear()

    def _login(self, username, password):
        client = _client()
        r = client.post("/login", data={"username": username, "password": password})
        self.assertEqual(r.status_code, 303, r.headers.get("location"))
        return client

    def _actions(self):
        return [e.action for e in self.store.audit]

    # ── 授權 ────────────────────────────────────────────────────────
    def test_user_gets_403_on_every_admin_endpoint(self):
        user = self._login("alice", USER_PW)
        calls = [
            ("get", "/api/admin/users", None),
            ("post", "/api/admin/users", {"username": "eve", "password": "eve-password-1", "role": "admin"}),
            ("patch", f"/api/admin/users/{self.alice}", {"role": "admin"}),
            ("post", f"/api/admin/users/{self.root}/password", {"password": "hijacked-password"}),
            ("post", f"/api/admin/users/{self.root}/logout", None),
            ("get", "/api/admin/audit", None),
            ("get", "/api/admin/audit/verify", None),
            ("post", "/api/admin/elevate", {"password": USER_PW}),
            ("put", f"/api/admin/users/{self.alice}/privileges", {"scopes": ["qa_content.read"]}),
        ]
        for method, path, body in calls:
            with self.subTest(f"{method} {path}"):
                r = getattr(user, method)(path, **({"json": body} if body is not None else {}))
                self.assertEqual(r.status_code, 403, r.text)
        # 一個都沒生效：自我升權、改別人密碼都沒有發生
        self.assertEqual(self.store.users[self.alice].role, "user")
        self.assertEqual(self.store.users[self.root].password, ADMIN_PW)
        self.assertEqual(self.store.audit, [])

    def test_unauthenticated_gets_401(self):
        self.assertEqual(_client().get("/api/admin/users").status_code, 401)

    # ── 帳號清單與建立 ──────────────────────────────────────────────
    def test_list_users_has_no_password_material(self):
        r = self.admin.get("/api/admin/users")
        self.assertEqual(r.status_code, 200)
        items = r.json()["items"]
        self.assertEqual([u["username"] for u in items], ["alice", "root"])
        self.assertEqual(items[1]["active_sessions"], 1)  # 自己這個登入
        self.assertNotIn("password", r.text.lower().replace("password_changed_at", ""))

    def test_create_user_then_new_user_can_log_in(self):
        r = self.admin.post("/api/admin/users", json={"username": "bob", "password": "bob-password-1"})
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual((r.json()["username"], r.json()["role"], r.json()["enabled"]), ("bob", "user", True))
        self._login("bob", "bob-password-1")
        entry = self.store.audit[0]  # 新的在前
        self.assertEqual((entry.action, entry.actor_user_id, entry.target_id),
                         ("user.create", self.root, r.json()["id"]))

    def test_create_user_errors(self):
        dup = self.admin.post("/api/admin/users", json={"username": "ALICE", "password": "x-password-12"})
        self.assertEqual(dup.status_code, 409)
        self.assertIn("已存在", dup.json()["detail"])
        weak = self.admin.post("/api/admin/users", json={"username": "carol", "password": "short"})
        self.assertEqual(weak.status_code, 400)
        self.assertIn("至少", weak.json()["detail"])
        bad_name = self.admin.post("/api/admin/users", json={"username": "a b", "password": "x-password-12"})
        self.assertEqual(bad_name.status_code, 400)
        bad_role = self.admin.post("/api/admin/users", json={"username": "dave", "password": "x-password-12",
                                                             "role": "superuser"})
        self.assertEqual(bad_role.status_code, 422)

    # ── 角色、停用 ──────────────────────────────────────────────────
    def test_promote_and_demote(self):
        r = self.admin.patch(f"/api/admin/users/{self.alice}", json={"role": "admin"})
        self.assertEqual((r.status_code, r.json()["role"]), (200, "admin"))
        alice = self._login("alice", USER_PW)
        self.assertEqual(alice.get("/api/admin/users").status_code, 200)
        self.admin.patch(f"/api/admin/users/{self.alice}", json={"role": "user"})
        self.assertEqual(alice.get("/api/admin/users").status_code, 403)  # 不必重登就降級
        self.assertEqual(self._actions().count("user.set_role"), 2)

    def test_disable_kicks_user_out_immediately(self):
        alice = self._login("alice", USER_PW)
        self.assertEqual(alice.get("/api/me").status_code, 200)
        r = self.admin.patch(f"/api/admin/users/{self.alice}", json={"enabled": False})
        self.assertEqual((r.status_code, r.json()["enabled"], r.json()["active_sessions"]), (200, False, 0))
        self.assertEqual(alice.get("/api/me").status_code, 401)
        relogin = _client().post("/login", data={"username": "alice", "password": USER_PW})
        self.assertIn("error=disabled", relogin.headers["location"])
        self.assertIn("user.disable", self._actions())
        self.admin.patch(f"/api/admin/users/{self.alice}", json={"enabled": True})
        self._login("alice", USER_PW)
        self.assertIn("user.enable", self._actions())

    def test_cannot_lock_yourself_out(self):
        self.store.add_user("other", "other-password", "admin")
        for body in ({"enabled": False}, {"role": "user"}):
            r = self.admin.patch(f"/api/admin/users/{self.root}", json=body)
            self.assertEqual(r.status_code, 409, body)
            self.assertIn("自己", r.json()["detail"])
        self.assertEqual(self.store.users[self.root].role, "admin")

    def test_last_admin_cannot_be_removed(self):
        """經網頁時「最後一位管理員」只可能是自己（由「不能鎖死自己」先擋），所以這條規則
        要從沒有帳號的管理身分驗：免登入開發模式（DEV_USER，id=None）——CLI 也是同一種。"""
        orig = dev_mode.bypass_allowed
        dev_mode.bypass_allowed = lambda request: True
        try:
            for body in ({"enabled": False}, {"role": "user"}):
                r = _client().patch(f"/api/admin/users/{self.root}", json=body)
                self.assertEqual(r.status_code, 409, body)
                self.assertIn("至少要保留一位", r.json()["detail"])
        finally:
            dev_mode.bypass_allowed = orig
        self.assertEqual((self.store.users[self.root].role, self.store.users[self.root].enabled), ("admin", True))

    def test_patch_needs_a_field_and_unknown_user_is_404(self):
        self.assertEqual(self.admin.patch(f"/api/admin/users/{self.alice}", json={}).status_code, 400)
        missing = "00000000-0000-4000-8000-000000000000"
        self.assertEqual(self.admin.patch(f"/api/admin/users/{missing}", json={"enabled": False}).status_code, 404)
        self.assertEqual(self.admin.post(f"/api/admin/users/{missing}/logout").status_code, 404)
        self.assertEqual(self.admin.post(f"/api/admin/users/{missing}/password",
                                         json={"password": "whatever-123"}).status_code, 404)

    # ── 重設密碼、強制登出 ─────────────────────────────────────────
    def test_reset_password_logs_out_and_old_password_stops_working(self):
        alice = self._login("alice", USER_PW)
        r = self.admin.post(f"/api/admin/users/{self.alice}/password", json={"password": "new-alice-pass-1"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(alice.get("/api/me").status_code, 401)
        self.assertIn("error=1", _client().post("/login", data={"username": "alice", "password": USER_PW})
                      .headers["location"])
        self._login("alice", "new-alice-pass-1")
        weak = self.admin.post(f"/api/admin/users/{self.alice}/password", json={"password": "short"})
        self.assertEqual(weak.status_code, 400)
        entry = next(e for e in self.store.audit if e.action == "user.reset_password")
        self.assertNotIn("new-alice-pass-1", repr(entry.detail))

    def test_force_logout(self):
        a1 = self._login("alice", USER_PW)
        a2 = self._login("alice", USER_PW)
        r = self.admin.post(f"/api/admin/users/{self.alice}/logout")
        self.assertEqual(r.json(), {"revoked": 2})
        self.assertEqual(a1.get("/api/me").status_code, 401)
        self.assertEqual(a2.get("/api/me").status_code, 401)
        self._login("alice", USER_PW)  # 強制登出不是停用：可以重新登入

    # ── 稽核 ────────────────────────────────────────────────────────
    def test_audit_lists_admin_actions_newest_first_with_actor(self):
        self.admin.patch(f"/api/admin/users/{self.alice}", json={"role": "admin"})
        self.admin.post(f"/api/admin/users/{self.alice}/logout")
        body = self.admin.get("/api/admin/audit", params={"limit": 1}).json()
        self.assertEqual((body["total"], body["has_more"], body["next_offset"]), (2, True, 1))
        top = body["items"][0]
        self.assertEqual((top["action"], top["actor_username"], top["target_id"]),
                         ("user.force_logout", "root", self.alice))
        rest = self.admin.get("/api/admin/audit", params={"limit": 50, "offset": 1}).json()
        self.assertEqual([i["action"] for i in rest["items"]], ["user.set_role"])
        self.assertEqual(rest["items"][0]["detail"]["to"], "admin")

    def test_audit_paging_bounds(self):
        for qs in ({"limit": 0}, {"limit": 201}, {"offset": -1}):
            self.assertEqual(self.admin.get("/api/admin/audit", params=qs).status_code, 422, qs)

    def test_admin_actions_are_logged_without_password(self):
        with self.assertLogs("web.routers.admin", level="INFO") as cm:
            self.admin.post(f"/api/admin/users/{self.alice}/password", json={"password": "logged-password"})
        blob = "\n".join(cm.output)
        self.assertIn("user.reset_password", blob)
        self.assertNotIn("logged-password", blob)



class PrivilegeApiTests(unittest.TestCase):
    """super admin、scope 授予與稽核鏈驗證（權限提升本身的 403 分流見 tests/test_authz.py）。"""

    def setUp(self):
        auth._FAILS.clear()
        self.store = FakeAccounts()
        self.boss = self.store.add_user("boss", ADMIN_PW, "admin", is_super=True)
        self.plain = self.store.add_user("plain", ADMIN_PW, "admin")
        self.alice = self.store.add_user("alice", USER_PW, "user")
        self._ctx = install(self.store)
        self._ctx.__enter__()
        self.client = _client()
        self.assertEqual(self.client.post("/login", data={"username": "boss", "password": ADMIN_PW}).status_code, 303)
        self.assertEqual(self.client.post("/api/admin/elevate", json={"password": ADMIN_PW}).status_code, 200)

    def tearDown(self):
        self._ctx.__exit__(None, None, None)
        auth._FAILS.clear()

    def test_grantable_scope_literal_matches_service(self):
        from typing import get_args

        from app.services import accounts
        from web.routers import admin

        self.assertEqual(set(get_args(admin.GrantableScope)), set(accounts.GRANTABLE_SCOPES))

    def test_user_list_exposes_super_and_granted_scopes(self):
        items = {u["username"]: u for u in self.client.get("/api/admin/users").json()["items"]}
        self.assertTrue(items["boss"]["is_super"])
        self.assertEqual(items["plain"]["scopes"], [])

    def test_grant_and_revoke_scope(self):
        r = self.client.put(f"/api/admin/users/{self.plain}/privileges", json={"scopes": ["qa_content.read"]})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["scopes"], ["qa_content.read"])
        r = self.client.put(f"/api/admin/users/{self.plain}/privileges", json={"scopes": []})
        self.assertEqual(r.json()["scopes"], [])

    def test_privilege_errors_carry_codes(self):
        cases = [
            ({"scopes": ["root"]}, self.plain, 422, "validation_error"),
            ({}, self.plain, 400, "bad_request"),
            ({"scopes": ["ops.operate"]}, self.alice, 400, "invalid_input"),
            ({"is_super": False}, self.boss, 409, "self_lockout"),
            ({"scopes": []}, "00000000-0000-0000-0000-000000000000", 404, "not_found"),
        ]
        for body, target, status, code in cases:
            with self.subTest(body=body, code=code):
                r = self.client.put(f"/api/admin/users/{target}/privileges", json=body)
                self.assertEqual((r.status_code, r.json()["code"]), (status, code), r.text)

    def test_non_super_admin_cannot_touch_super(self):
        plain = _client()
        self.assertEqual(plain.post("/login", data={"username": "plain", "password": ADMIN_PW}).status_code, 303)
        r = plain.post(f"/api/admin/users/{self.boss}/password", json={"password": "hijacked-password"})
        self.assertEqual((r.status_code, r.json()["code"]), (403, "super_required"))
        r = plain.patch(f"/api/admin/users/{self.boss}", json={"enabled": False})
        self.assertEqual((r.status_code, r.json()["code"]), (403, "super_required"))

    def test_audit_chain_verify_reports_broken_rows(self):
        self.client.put(f"/api/admin/users/{self.plain}/privileges", json={"scopes": ["qa_content.read"]})
        ok = self.client.get("/api/admin/audit/verify").json()
        self.assertTrue(ok["ok"])
        self.assertEqual(ok["broken_ids"], [])
        self.store.broken_audit_ids.add(ok["head_id"])
        bad = self.client.get("/api/admin/audit/verify").json()
        self.assertFalse(bad["ok"])
        self.assertEqual(bad["broken_ids"], [ok["head_id"]])

    def test_me_reports_scopes_and_elevation(self):
        me = self.client.get("/api/me").json()
        self.assertTrue(me["is_super"])
        self.assertIn("qa_content.read", me["scopes"])
        self.assertTrue(me["elevated_until"])


if __name__ == "__main__":
    unittest.main()
