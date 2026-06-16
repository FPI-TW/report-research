# tests/test_auth.py
import os
import unittest

# web.auth 匯入時即讀取共用帳密(fail-closed),故須在匯入前設好測試用值。
os.environ.setdefault("REPORT_MARK_ACCESS_USERNAME", "tester")
os.environ.setdefault("REPORT_MARK_ACCESS_PASSWORD", "testpass")
os.environ.setdefault("REPORT_MARK_SESSION_SECRET", "fixed-test-secret-0123456789")

from web import auth  # noqa: E402


class TokenTests(unittest.TestCase):
    def test_valid_token_round_trips(self):
        now = 1_000_000
        tok = auth.issue_token(now)
        self.assertTrue(auth.verify_token(tok, now + 10))

    def test_expired_token_rejected(self):
        now = 1_000_000
        tok = auth.issue_token(now)
        self.assertFalse(auth.verify_token(tok, now + auth.SESSION_TTL + 1))

    def test_tampered_signature_rejected(self):
        now = 1_000_000
        tok = auth.issue_token(now)
        self.assertFalse(auth.verify_token(tok + "x", now + 10))

    def test_garbage_and_empty_token_rejected(self):
        self.assertFalse(auth.verify_token("not-a-token", 1_000_000))
        self.assertFalse(auth.verify_token("", 1_000_000))
        self.assertFalse(auth.verify_token(None, 1_000_000))


class CredentialTests(unittest.TestCase):
    def test_correct_credentials_accepted(self):
        self.assertTrue(auth.check_credentials("tester", "testpass"))

    def test_wrong_password_rejected(self):
        self.assertFalse(auth.check_credentials("tester", "nope"))

    def test_wrong_username_rejected(self):
        self.assertFalse(auth.check_credentials("nobody", "testpass"))

    def test_empty_credentials_rejected(self):
        self.assertFalse(auth.check_credentials("", ""))


class RateLimitTests(unittest.TestCase):
    def setUp(self):
        auth._FAILS.clear()

    def tearDown(self):
        auth._FAILS.clear()

    def test_locks_after_max_failures(self):
        now = 2_000_000
        ip = "1.2.3.4"
        for _ in range(auth.MAX_FAILS):
            self.assertFalse(auth.is_locked(ip, now))
            auth.record_failure(ip, now)
        self.assertTrue(auth.is_locked(ip, now))

    def test_reset_clears_lock(self):
        now = 2_000_000
        ip = "1.2.3.4"
        for _ in range(auth.MAX_FAILS):
            auth.record_failure(ip, now)
        auth.reset(ip)
        self.assertFalse(auth.is_locked(ip, now))

    def test_failures_age_out_of_window(self):
        ip = "1.2.3.4"
        start = 2_000_000
        for _ in range(auth.MAX_FAILS):
            auth.record_failure(ip, start)
        self.assertTrue(auth.is_locked(ip, start))
        # 視窗過後(全部老化)→ 解鎖
        self.assertFalse(auth.is_locked(ip, start + auth.FAIL_WINDOW + 1))


if __name__ == "__main__":
    unittest.main()
