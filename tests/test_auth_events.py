"""登入事件（`research.auth_event`）的 HTTP 層：每種登入結果寫哪一列、絕不含帳號名稱、記錄失敗不影響登入、
被限流期間只寫彙總。

事件經 `deps.accounts.record_auth_event` 寫入；這裡用 `tests/fake_accounts.py` 的記憶體版（`auth_events` 清單，
欄位同 DB 表）。SQL 本身在 tests/test_accounts_db.py 的 `scenario_auth_event_record`。
"""

from __future__ import annotations

import time
import unittest
from unittest import mock

from fake_accounts import FakeAccounts, install
from fastapi.testclient import TestClient

from app.services import security_ops, totp
from web import auth
from web.server import app

PW = "correct-horse-battery-1"
# 使用者常把密碼誤打進帳號欄：這個字串絕不能出現在任何事件欄位裡。
TYPO_PASSWORD_AS_USERNAME = "MySecret-Pa55word!"
T0 = 1_800_000_000.0


def _client(base_url: str = "http://127.0.0.1"):
    return TestClient(app, follow_redirects=False, base_url=base_url)


class _Base(unittest.TestCase):
    def setUp(self):
        auth._FAILS.clear()
        security_ops.reset()
        self.store = FakeAccounts()
        self.alice = self.store.add_user("alice", PW, "user")
        self.root = self.store.add_user("root", PW, "admin")
        self.off = self.store.add_user("offline", PW, "user", enabled=False)
        self._ctx = install(self.store)
        self._ctx.__enter__()

    def tearDown(self):
        self._ctx.__exit__(None, None, None)
        auth._FAILS.clear()
        security_ops.reset()

    def events(self, event: str | None = None) -> list[dict]:
        return [e for e in self.store.auth_events if event is None or e["event"] == event]

    def assert_no_names(self):
        """掃描每一列事件的每一個欄位：不得含任何帳號名稱、密碼或誤打進帳號欄的字串。"""
        forbidden = ("alice", "root", "offline", PW, TYPO_PASSWORD_AS_USERNAME, "nobody-here")
        for e in self.store.auth_events:
            self.assertNotIn("username", e)
            for key, value in e.items():
                for word in forbidden:
                    self.assertNotIn(word, str(value), f"{e['event']}.{key} 含 {word!r}")


class LoginEventTests(_Base):
    def test_success_records_user_session_ip_and_ua(self):
        c = _client()
        r = c.post("/login", data={"username": "alice", "password": PW}, headers={"user-agent": "UA-Test/1"})
        self.assertEqual(r.status_code, 303)
        [e] = self.events()
        self.assertEqual((e["event"], e["reason"], e["user_id"], e["user_agent"], e["count"]),
                         ("login.success", "password", self.alice, "UA-Test/1", 1))
        self.assertIn(e["session_id"], self.store.sessions)
        self.assertTrue(e["ip"])
        self.assert_no_names()

    def test_failures_by_reason_without_names(self):
        c = _client()
        c.post("/login", data={"username": "alice", "password": "wrong"})
        c.post("/login", data={"username": TYPO_PASSWORD_AS_USERNAME, "password": "x"})
        c.post("/login", data={"username": "nobody-here", "password": "x"})
        c.post("/login", data={"username": "offline", "password": PW})
        got = [(e["event"], e["reason"], e["user_id"]) for e in self.events()]
        self.assertEqual(got, [
            ("login.failure", "bad_password", self.alice),
            ("login.failure", "unknown_user", None),
            ("login.failure", "unknown_user", None),
            ("login.failure", "disabled", self.off),
        ])
        self.assert_no_names()

    def test_totp_second_step(self):
        row = self.store.users[self.alice]
        row.totp_secret = totp.generate_secret()
        row.totp_enabled = True
        c = _client()
        with mock.patch.object(totp, "_now", lambda: T0):
            r = c.post("/login", data={"username": "alice", "password": PW})
            self.assertIn("step=totp", r.headers["location"])
            self.assertEqual(self.events(), [], "第一步通過還不是登入成功")
            good = totp.code_at(row.totp_secret, totp.current_step(T0))
            c.post("/login", data={"step": "totp", "code": "000000" if good != "000000" else "111111"})
            c.post("/login", data={"step": "totp", "code": good})
        got = [(e["event"], e["reason"], e["user_id"]) for e in self.events()]
        self.assertEqual(got, [("login.totp_failure", "bad_code", self.alice), ("login.success", "totp", self.alice)])
        self.assert_no_names()

    def test_expired_challenge_is_only_counted_in_memory(self):
        c = _client()
        for _ in range(50):
            r = c.post("/login", data={"step": "totp", "code": "123456"})
            self.assertIn("error=expired", r.headers["location"])
        self.assertEqual(self.events(), [], "沒有暫時憑證的第二步不得逐筆寫 DB")
        self.assertEqual(security_ops.EXPIRED_CHALLENGE_TALLY.pending, 50)

    def test_logout_records_user_and_session(self):
        c = _client()
        c.post("/login", data={"username": "alice", "password": PW})
        sid = self.events("login.success")[0]["session_id"]
        r = c.post("/logout")
        self.assertEqual(r.status_code, 303)
        [e] = self.events("logout")
        self.assertEqual((e["user_id"], e["session_id"]), (self.alice, sid))
        self.assert_no_names()


class ThrottledLoginTests(_Base):
    def test_locked_requests_are_aggregated_not_written_one_by_one(self):
        now = [1000.0]
        tally = security_ops.ThrottledTally("login.locked", clock=lambda: now[0])
        c = _client()
        with mock.patch.object(security_ops, "LOCKED_TALLY", tally), \
                mock.patch.object(security_ops, "_TALLIES", (tally,)):
            for _ in range(auth.MAX_FAILS):
                c.post("/login", data={"username": "alice", "password": "wrong"})
            self.assertEqual(len(self.events("login.failure")), auth.MAX_FAILS)
            for _ in range(200):
                r = c.post("/login", data={"username": "alice", "password": PW})
                self.assertIn("error=locked", r.headers["location"])
            self.assertEqual(self.events("login.locked"), [], "限流期間不逐筆寫 DB")
            self.assertEqual(len(self.events("login.failure")), auth.MAX_FAILS, "被擋下的請求不算登入失敗")
            now[0] += security_ops.TALLY_FLUSH_SECONDS
            # 到期後的下一個請求：先把前 200 筆寫成一列彙總，自己再開始下一期的計數
            c.post("/login", data={"username": "alice", "password": PW})
            self.assertEqual(tally.pending, 1)
        locked = self.events("login.locked")
        self.assertEqual(len(locked), 1)
        self.assertEqual((locked[0]["count"], locked[0]["user_id"]), (200, None))
        self.assertTrue(locked[0]["ip"])
        self.assert_no_names()

    def test_insecure_rejections_are_aggregated(self):
        c = _client("http://research.office")
        for _ in range(30):
            r = c.post("/login", data={"username": "alice", "password": PW})
            self.assertIn("error=insecure", r.headers["location"])
        self.assertEqual(self.events(), [])
        self.assertEqual(security_ops.INSECURE_TALLY.pending, 30)

    def test_rate_limit_itself_is_unchanged(self):
        """定案 8：既有的每 IP 失敗限流不移除、不放寬。"""
        self.assertEqual((auth.MAX_FAILS, auth.FAIL_WINDOW), (5, 300))


class RecordingFailureDoesNotAffectLoginTests(_Base):
    def setUp(self):
        super().setUp()

        async def boom(*a, **kw):
            raise RuntimeError("auth_event 寫不進去")

        self.store.record_auth_event = boom

    def test_login_logout_and_failures_still_work(self):
        c = _client()
        with self.assertLogs("app.services.security_ops", "WARNING"):
            r = c.post("/login", data={"username": "alice", "password": PW})
        self.assertEqual((r.status_code, r.headers["location"]), (303, "/"))
        self.assertIn(auth.COOKIE_NAME, r.cookies)
        self.assertEqual(c.get("/api/me").json()["username"], "alice")
        with self.assertLogs("app.services.security_ops", "WARNING"):
            self.assertEqual(c.post("/logout").status_code, 303)
        with self.assertLogs("app.services.security_ops", "WARNING"):
            bad = _client().post("/login", data={"username": "alice", "password": "wrong"})
        self.assertIn("error=1", bad.headers["location"])

    def test_hanging_recorder_is_bounded(self):
        import asyncio

        async def hang(*a, **kw):
            await asyncio.sleep(30)

        self.store.record_auth_event = hang
        start = time.monotonic()
        with mock.patch.object(security_ops, "RECORD_TIMEOUT_SECONDS", 0.2), \
                self.assertLogs("app.services.security_ops", "WARNING"):
            r = _client().post("/login", data={"username": "alice", "password": PW})
        self.assertEqual(r.status_code, 303)
        self.assertLess(time.monotonic() - start, 10)


class ElevationEventTests(_Base):
    def _login(self, name="root"):
        c = _client()
        c.post("/login", data={"username": name, "password": PW})
        return c

    def test_failure_and_success(self):
        c = self._login()
        sid = self.events("login.success")[0]["session_id"]
        r = c.post("/api/me/elevate", json={"password": "wrong"})
        self.assertEqual(r.status_code, 403)
        r = c.post("/api/admin/elevate", json={"password": PW})
        self.assertEqual(r.status_code, 200, r.text)
        got = [(e["event"], e["reason"], e["user_id"], e["session_id"]) for e in self.events()
               if e["event"].startswith("elevate.")]
        self.assertEqual(got, [("elevate.failure", "bad_password", self.root, sid),
                               ("elevate.success", None, self.root, sid)])
        self.assert_no_names()

    def test_failure_with_code_is_bad_credentials(self):
        row = self.store.users[self.alice]
        row.totp_secret = totp.generate_secret()
        c = self._login("alice")
        row.totp_enabled = True
        with mock.patch.object(totp, "_now", lambda: T0):
            good = totp.code_at(row.totp_secret, totp.current_step(T0))
            r = c.post("/api/me/elevate", json={"password": PW, "code": "000000" if good != "000000" else "111111"})
        self.assertEqual(r.status_code, 403)
        [e] = self.events("elevate.failure")
        self.assertEqual(e["reason"], "bad_credentials")

    def test_locked_elevation_is_aggregated(self):
        c = self._login()
        for _ in range(auth.MAX_FAILS):
            c.post("/api/me/elevate", json={"password": "wrong"})
        r = c.post("/api/me/elevate", json={"password": PW})
        self.assertEqual(r.status_code, 429)
        self.assertEqual(len(self.events("elevate.failure")), auth.MAX_FAILS)
        self.assertEqual(security_ops.LOCKED_TALLY.pending, 1)

    def test_recorder_failure_does_not_block_elevation(self):
        c = self._login()

        async def boom(*a, **kw):
            raise RuntimeError("down")

        self.store.record_auth_event = boom
        with self.assertLogs("app.services.security_ops", "WARNING"):
            r = c.post("/api/admin/elevate", json={"password": PW})
        self.assertEqual(r.status_code, 200, r.text)


if __name__ == "__main__":
    unittest.main()
