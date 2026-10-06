"""app/services/totp.py：RFC 6238 測試向量、時間窗、重放判斷與 otpauth URI（純計算，不連 DB）。"""

from __future__ import annotations

import base64
import unittest
from urllib.parse import parse_qs, unquote, urlsplit

from app.services import totp

# RFC 6238 附錄 B 的 SHA1 金鑰（ASCII "12345678901234567890"）。
RFC_SECRET = base64.b32encode(b"12345678901234567890").decode().rstrip("=")
# (Unix 時間, 8 位 TOTP)；6 位＝取最後 6 位（同一個截斷值取模）。
RFC_VECTORS = [
    (59, "94287082"),
    (1111111109, "07081804"),
    (1111111111, "14050471"),
    (1234567890, "89005924"),
    (2000000000, "69279037"),
    (20000000000, "65353130"),
]


class RfcVectorTests(unittest.TestCase):
    def test_rfc6238_sha1_vectors(self):
        for t, expected in RFC_VECTORS:
            with self.subTest(t=t):
                step = totp.current_step(t)
                self.assertEqual(totp.hotp(RFC_SECRET, step, digits=8), expected)
                self.assertEqual(totp.code_at(RFC_SECRET, step), expected[-6:])

    def test_rfc4226_hotp_vectors(self):
        # RFC 4226 附錄 D：同一把金鑰，counter 0..2
        self.assertEqual([totp.hotp(RFC_SECRET, c) for c in range(3)], ["755224", "287082", "359152"])


class MatchStepTests(unittest.TestCase):
    T = 1_800_000_015.0

    def setUp(self):
        self.secret = totp.generate_secret()
        self.cur = totp.current_step(self.T)

    def code(self, step):
        return totp.code_at(self.secret, step)

    def test_accepts_current_and_adjacent_steps(self):
        for delta in (-1, 0, 1):
            self.assertEqual(totp.match_step(self.secret, self.code(self.cur + delta), last_step=None, now=self.T),
                             self.cur + delta)

    def test_rejects_outside_window(self):
        for delta in (-2, 2):
            self.assertIsNone(totp.match_step(self.secret, self.code(self.cur + delta), last_step=None, now=self.T))

    def test_rejects_reused_or_older_step(self):
        self.assertIsNone(totp.match_step(self.secret, self.code(self.cur), last_step=self.cur, now=self.T))
        self.assertIsNone(totp.match_step(self.secret, self.code(self.cur - 1), last_step=self.cur - 1, now=self.T))
        self.assertEqual(totp.match_step(self.secret, self.code(self.cur + 1), last_step=self.cur, now=self.T),
                         self.cur + 1)

    def test_normalizes_spaces_and_rejects_garbage(self):
        c = self.code(self.cur)
        self.assertEqual(totp.match_step(self.secret, f" {c[:3]} {c[3:]} ", last_step=None, now=self.T), self.cur)
        for bad in (None, "", "12345", "1234567", "abcdef", "１２３４５６"):
            self.assertIsNone(totp.match_step(self.secret, bad, last_step=None, now=self.T), bad)

    def test_bad_secret_never_matches(self):
        self.assertIsNone(totp.match_step("not base32 !!", "123456", last_step=None, now=self.T))
        self.assertIsNone(totp.match_step("", "123456", last_step=None, now=self.T))


class SecretAndUriTests(unittest.TestCase):
    def test_secret_is_160_bit_base32(self):
        s = totp.generate_secret()
        self.assertEqual(len(s), 32)
        self.assertEqual(len(base64.b32decode(s)), 20)
        self.assertNotEqual(s, totp.generate_secret())

    def test_otpauth_uri(self):
        uri = totp.otpauth_uri("ABCDEFGH", "alice@x")
        parts = urlsplit(uri)
        self.assertEqual((parts.scheme, parts.netloc), ("otpauth", "totp"))
        self.assertEqual(unquote(parts.path), f"/{totp.ISSUER}:alice@x")
        q = parse_qs(parts.query)
        self.assertEqual(q["secret"], ["ABCDEFGH"])
        self.assertEqual(q["issuer"], [totp.ISSUER])
        self.assertEqual((q["digits"], q["period"], q["algorithm"]), (["6"], ["30"], ["SHA1"]))


if __name__ == "__main__":
    unittest.main()
