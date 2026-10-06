"""帳號安全的 HTTP 層：登入第二步（TOTP）、/api/me/* 自助設定與權限提升、管理員的刪除排程與 TOTP 重設。

服務層規則（時間步不可重用、刪除清掉哪些東西）對真 DB 的驗證在 tests/test_accounts_db.py；
這裡驗 HTTP 轉換、cookie 流程、錯誤代碼與授權。TOTP 時鐘以 `totp._now` 固定，不靠真的時間。
"""

from __future__ import annotations

import time
import unittest
from unittest import mock

from fake_accounts import FakeAccounts, install
from fastapi.testclient import TestClient

from app.services import totp
from web import auth
from web.server import app

ADMIN_PW = "root-password-1"
USER_PW = "alice-password-1"
T0 = 1_800_000_000.0


def _client():
    return TestClient(app, follow_redirects=False, base_url="http://127.0.0.1")


def _clock(t: float):
    return mock.patch.object(totp, "_now", lambda: t)


def _code(secret: str, t: float) -> str:
    return totp.code_at(secret, totp.current_step(t))


class _Base(unittest.TestCase):
    def setUp(self):
        auth._FAILS.clear()
        self.store = FakeAccounts()
        self.boss = self.store.add_user("boss", ADMIN_PW, "admin", is_super=True)
        self.root = self.store.add_user("root", ADMIN_PW, "admin")
        self.alice = self.store.add_user("alice", USER_PW, "user")
        self._ctx = install(self.store)
        self._ctx.__enter__()

    def tearDown(self):
        self._ctx.__exit__(None, None, None)
        auth._FAILS.clear()

    def _login(self, username, password):
        client = _client()
        r = client.post("/login", data={"username": username, "password": password})
        self.assertEqual(r.status_code, 303, r.headers.get("location"))
        return client

    def _turn_on_totp(self, user_id, t=T0) -> str:
        row = self.store.users[user_id]
        row.totp_secret = totp.generate_secret()
        row.totp_enabled = True
        row.totp_last_step = totp.current_step(t)
        return row.totp_secret


class LoginTotpTests(_Base):
    def test_password_step_issues_short_lived_challenge_not_a_session(self):
        self._turn_on_totp(self.alice)
        c = _client()
        r = c.post("/login", data={"username": "alice", "password": USER_PW, "next": "/app/ask"})
        self.assertEqual(r.status_code, 303)
        self.assertTrue(r.headers["location"].startswith("/login?step=totp"))
        self.assertIn("next=%2Fapp%2Fask", r.headers["location"])
        cookies = r.headers.get_list("set-cookie")
        mfa = next(h for h in cookies if h.startswith(auth.MFA_COOKIE_NAME + "="))
        self.assertIn("Path=/login", mfa)
        self.assertIn("HttpOnly", mfa)
        self.assertIn(f"Max-Age={auth.MFA_TTL}", mfa)
        self.assertFalse(any(h.startswith(auth.COOKIE_NAME + "=") for h in cookies), "第一步不能發 session")
        self.assertEqual(self.store.sessions, {})
        self.assertEqual(c.get("/api/me").status_code, 401)

    def test_second_step_with_correct_code_logs_in_and_clears_challenge(self):
        secret = self._turn_on_totp(self.alice)
        c = _client()
        c.post("/login", data={"username": "alice", "password": USER_PW})
        with _clock(T0 + 30):
            r = c.post("/login", data={"step": "totp", "code": _code(secret, T0 + 30), "next": "/app/ask"})
        self.assertEqual((r.status_code, r.headers["location"]), (303, "/app/ask"))
        cookies = r.headers.get_list("set-cookie")
        self.assertTrue(any(h.startswith(auth.COOKIE_NAME + "=") for h in cookies))
        self.assertTrue(any(h.startswith(auth.MFA_COOKIE_NAME + '=""') or
                            (h.startswith(auth.MFA_COOKIE_NAME + "=") and "Max-Age=0" in h) for h in cookies))
        me = c.get("/api/me").json()
        self.assertEqual(me["username"], "alice")
        self.assertTrue(me["totp_enabled"])

    def test_wrong_or_reused_code_is_a_counted_failure(self):
        secret = self._turn_on_totp(self.alice)
        c = _client()
        r1 = c.post("/login", data={"username": "alice", "password": USER_PW})
        mfa = r1.cookies.get(auth.MFA_COOKIE_NAME)
        with _clock(T0):
            # 開啟時用掉的那一個時間步：不能再用
            r = c.post("/login", data={"step": "totp", "code": _code(secret, T0)})
        self.assertEqual(r.headers["location"], "/login?step=totp&error=totp")
        self.assertEqual(auth.failure_count("testclient", int(time.time())), 1)
        with _clock(T0 + 30):
            ok = c.post("/login", data={"step": "totp", "code": _code(secret, T0 + 30)})
        self.assertEqual(ok.status_code, 303)
        self.assertEqual(ok.headers["location"], "/")
        # 同一張暫時憑證不可重放：時間步前進後指紋對不上，即使碼是新的
        replay = _client()
        replay.cookies.set(auth.MFA_COOKIE_NAME, mfa, path="/login")
        with _clock(T0 + 60):
            r = replay.post("/login", data={"step": "totp", "code": _code(secret, T0 + 60)})
        self.assertEqual(r.headers["location"], "/login?step=totp&error=totp")

    def test_missing_tampered_or_expired_challenge(self):
        self._turn_on_totp(self.alice)
        r = _client().post("/login", data={"step": "totp", "code": "123456"})
        self.assertEqual(r.headers["location"], "/login?error=expired")
        now = int(time.time())
        good = auth.issue_mfa_token(now, user_id=self.alice, fingerprint="a" * 32)
        self.assertEqual(auth.parse_mfa_token(good, now), (self.alice, "a" * 32))
        self.assertIsNone(auth.parse_mfa_token(good[:-2] + "xx", now))
        self.assertIsNone(auth.parse_mfa_token(good, now + auth.MFA_TTL))
        self.assertIsNone(auth.parse_mfa_token(good.replace("m1.", "3.", 1), now))
        # session token 與暫時憑證不能互相冒充
        self.assertIsNone(auth.parse_token(good, now))
        c = _client()
        c.cookies.set(auth.MFA_COOKIE_NAME, good[:-2] + "xx", path="/login")
        self.assertEqual(c.post("/login", data={"step": "totp", "code": "123456"}).headers["location"],
                         "/login?error=expired")

    def test_locked_ip_cannot_try_codes(self):
        self._turn_on_totp(self.alice)
        c = _client()
        c.post("/login", data={"username": "alice", "password": USER_PW})
        for _ in range(auth.MAX_FAILS):
            c.post("/login", data={"step": "totp", "code": "000000"})
        r = c.post("/login", data={"step": "totp", "code": "000000"})
        self.assertEqual(r.headers["location"], "/login?step=totp&error=locked")

    def test_account_without_totp_logs_in_in_one_step(self):
        r = _client().post("/login", data={"username": "alice", "password": USER_PW})
        self.assertEqual(r.headers["location"], "/")


class SelfServiceTotpTests(_Base):
    def test_enable_flow_for_a_regular_user(self):
        c = self._login("alice", USER_PW)
        self.assertEqual(c.get("/api/me/totp").json(), {"enabled": False, "pending": False})
        setup = c.post("/api/me/totp/setup").json()
        self.assertTrue(setup["otpauth_uri"].startswith("otpauth://totp/"))
        self.assertIn(setup["secret"], setup["otpauth_uri"])
        self.assertEqual(c.get("/api/me/totp").json(), {"enabled": False, "pending": True})
        with _clock(T0):
            bad = c.post("/api/me/totp/confirm", json={"code": "000000" if _code(setup["secret"], T0) != "000000"
                                                       else "111111"})
            self.assertEqual((bad.status_code, bad.json()["code"]), (400, "bad_totp"))
            ok = c.post("/api/me/totp/confirm", json={"code": _code(setup["secret"], T0)})
        self.assertEqual(ok.json(), {"enabled": True, "pending": False})
        again = c.post("/api/me/totp/setup")
        self.assertEqual((again.status_code, again.json()["code"]), (409, "totp_state"))
        self.assertTrue(c.get("/api/me").json()["totp_enabled"])

    def test_disable_needs_elevation_with_code(self):
        c = self._login("alice", USER_PW)
        secret = self._turn_on_totp(self.alice)
        r = c.post("/api/me/totp/disable")
        self.assertEqual((r.status_code, r.json()["code"]), (403, "elevation_required"))
        r = c.post("/api/me/elevate", json={"password": USER_PW})
        self.assertEqual((r.status_code, r.json()["code"]), (403, "totp_required"))
        self.assertEqual(auth.failure_count("testclient", int(time.time())), 0, "沒給驗證碼不算失敗")
        with _clock(T0 + 30):
            r = c.post("/api/me/elevate", json={"password": USER_PW, "code": "999999"
                                                if _code(secret, T0 + 30) != "999999" else "888888"})
            self.assertEqual((r.status_code, r.json()["code"]), (403, "bad_password"))
            r = c.post("/api/me/elevate", json={"password": USER_PW, "code": _code(secret, T0 + 30)})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["elevated_until"])
        r = c.post("/api/me/totp/disable")
        self.assertEqual(r.json(), {"enabled": False, "pending": False})
        self.assertFalse(self.store.users[self.alice].totp_enabled)
        self.assertIn("user.totp_disable", [e.action for e in self.store.audit])

    def test_admin_elevate_also_requires_code(self):
        c = self._login("root", ADMIN_PW)
        secret = self._turn_on_totp(self.root)
        r = c.post("/api/admin/elevate", json={"password": ADMIN_PW})
        self.assertEqual((r.status_code, r.json()["code"]), (403, "totp_required"))
        with _clock(T0 + 30):
            r = c.post("/api/admin/elevate", json={"password": ADMIN_PW, "code": _code(secret, T0 + 30)})
        self.assertEqual(r.status_code, 200, r.text)

    def test_unauthenticated_gets_401(self):
        for method, path in (("get", "/api/me/totp"), ("post", "/api/me/totp/setup"),
                             ("post", "/api/me/elevate")):
            self.assertEqual(getattr(_client(), method)(path).status_code, 401, path)


class AdminDeletionApiTests(_Base):
    def setUp(self):
        super().setUp()
        self.admin = self._login("root", ADMIN_PW)

    def _elevate(self, client=None, pw=ADMIN_PW):
        r = (client or self.admin).post("/api/admin/elevate", json={"password": pw})
        self.assertEqual(r.status_code, 200, r.text)

    def test_request_needs_elevation_then_disables_immediately(self):
        victim = self._login("alice", USER_PW)
        r = self.admin.post(f"/api/admin/users/{self.alice}/deletion")
        self.assertEqual((r.status_code, r.json()["code"]), (403, "elevation_required"))
        self._elevate()
        r = self.admin.post(f"/api/admin/users/{self.alice}/deletion")
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual((body["status"], body["user_id"], body["requested_by"]), ("pending", self.alice, self.root))
        self.assertTrue(body["execute_after"])
        self.assertEqual(victim.get("/api/me").status_code, 401)  # 立即踢出
        users = {u["username"]: u for u in self.admin.get("/api/admin/users").json()["items"]}
        self.assertFalse(users["alice"]["enabled"])
        self.assertEqual(users["alice"]["deletion_execute_after"], body["execute_after"])
        listed = self.admin.get("/api/admin/deletions").json()["items"]
        self.assertEqual([d["user_id"] for d in listed], [self.alice])
        again = self.admin.post(f"/api/admin/users/{self.alice}/deletion")
        self.assertEqual((again.status_code, again.json()["code"]), (409, "deletion_pending"))
        enable = self.admin.patch(f"/api/admin/users/{self.alice}", json={"enabled": True})
        self.assertEqual((enable.status_code, enable.json()["code"]), (409, "deletion_pending"))

    def test_cancel_restores_and_needs_no_elevation(self):
        self._elevate()
        self.admin.post(f"/api/admin/users/{self.alice}/deletion")
        fresh = self._login("boss", ADMIN_PW)  # 沒有提升過的另一位管理員
        r = fresh.post(f"/api/admin/users/{self.alice}/deletion/cancel")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["status"], "cancelled")
        self.assertTrue(self.store.users[self.alice].enabled)
        self._login("alice", USER_PW)
        r = fresh.post(f"/api/admin/users/{self.alice}/deletion/cancel")
        self.assertEqual((r.status_code, r.json()["code"]), (404, "no_pending_deletion"))
        self.assertEqual(self.admin.get("/api/admin/deletions").json()["items"], [])
        done = self.admin.get("/api/admin/deletions?status=all").json()["items"]
        self.assertEqual([d["status"] for d in done], ["cancelled"])

    def test_error_codes(self):
        self._elevate()
        cases = [
            (self.root, 409, "self_lockout"),
            (self.boss, 403, "super_required"),
            ("00000000-0000-0000-0000-000000000000", 404, "not_found"),
        ]
        for target, status, code in cases:
            with self.subTest(code=code):
                r = self.admin.post(f"/api/admin/users/{target}/deletion")
                self.assertEqual((r.status_code, r.json()["code"]), (status, code), r.text)
        self.store.add_user("closed", USER_PW, "user")
        uid = next(u.id for u in self.store.users.values() if u.username == "closed")
        import asyncio
        asyncio.run(self.store.request_deletion(uid, actor_id=self.root, delay_seconds=0))
        r = self.admin.post(f"/api/admin/users/{uid}/deletion/cancel")
        self.assertEqual((r.status_code, r.json()["code"]), (409, "deletion_window_closed"))
        asyncio.run(self.store.execute_deletion(uid))
        r = self.admin.post(f"/api/admin/users/{uid}/password", json={"password": "another-password-1"})
        self.assertEqual((r.status_code, r.json()["code"]), (409, "account_deleted"))

    def test_admin_resets_lost_totp(self):
        self._turn_on_totp(self.alice)
        r = self.admin.post(f"/api/admin/users/{self.alice}/totp/reset")
        self.assertEqual((r.status_code, r.json()["code"]), (403, "elevation_required"))
        self._elevate()
        r = self.admin.post(f"/api/admin/users/{self.alice}/totp/reset")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertFalse(r.json()["totp_enabled"])
        self.assertEqual(self.store.audit[0].action, "user.totp_reset")
        self._turn_on_totp(self.boss)
        r = self.admin.post(f"/api/admin/users/{self.boss}/totp/reset")
        self.assertEqual((r.status_code, r.json()["code"]), (403, "super_required"))


if __name__ == "__main__":
    unittest.main()
