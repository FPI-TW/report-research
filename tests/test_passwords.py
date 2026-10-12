"""Argon2id 密碼雜湊（app/services/passwords.py）。"""

from __future__ import annotations

import unittest

from app.services import passwords


class PasswordHashTests(unittest.TestCase):
    def test_hash_is_argon2id_phc_string_and_verifies(self):
        h = passwords.hash_password("correct horse battery")
        self.assertTrue(h.startswith("$argon2id$"), h)
        self.assertTrue(passwords.verify_password(h, "correct horse battery"))
        self.assertFalse(passwords.verify_password(h, "correct horse batterY"))

    def test_same_password_gets_different_salt(self):
        self.assertNotEqual(passwords.hash_password("same-password"), passwords.hash_password("same-password"))

    def test_corrupt_hash_is_false_not_exception(self):
        self.assertFalse(passwords.verify_password("not-a-hash", "x"))
        self.assertFalse(passwords.verify_password("", "x"))

    def test_non_ascii_password_round_trips(self):
        h = passwords.hash_password("研究部的密碼很長很長")
        self.assertTrue(passwords.verify_password(h, "研究部的密碼很長很長"))

    def test_weaker_params_need_rehash(self):
        from argon2 import PasswordHasher

        weak = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1).hash("some-password")
        self.assertTrue(passwords.needs_rehash(weak))
        self.assertFalse(passwords.needs_rehash(passwords.hash_password("some-password")))
        self.assertFalse(passwords.needs_rehash("garbage"))

    def test_burn_verify_does_not_raise(self):
        passwords.burn_verify("anything")
        passwords.burn_verify("")


class PasswordPolicyTests(unittest.TestCase):
    def test_too_short(self):
        self.assertIn("至少", passwords.password_problem("x" * (passwords.MIN_PASSWORD_LENGTH - 1)))

    def test_min_length_ok(self):
        self.assertIsNone(passwords.password_problem("x" * passwords.MIN_PASSWORD_LENGTH))

    def test_too_long(self):
        self.assertIn("最多", passwords.password_problem("x" * (passwords.MAX_PASSWORD_LENGTH + 1)))

    def test_surrounding_whitespace_rejected(self):
        self.assertIsNotNone(passwords.password_problem(" long-enough-password"))
        self.assertIsNotNone(passwords.password_problem("long-enough-password\n"))
        self.assertIsNone(passwords.password_problem("long enough password"))


if __name__ == "__main__":
    unittest.main()
