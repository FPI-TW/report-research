"""CSRF／Origin 檢查（web/csrf.py）與統一錯誤格式（web/errors.py）。

CSRF：會改變狀態的請求只收本站來的；非瀏覽器（沒有 Origin、也沒有 Sec-Fetch-Site）放行。
錯誤格式：所有 JSON 錯誤都有 detail（原本的意義不變）、code（穩定字串）與 request_id。
"""

from __future__ import annotations

import unittest

from fake_accounts import session_cookies
from fastapi.testclient import TestClient

from web import auth, csrf
from web.server import app


def _client(*, logged_in: bool = False):
    client = TestClient(app, follow_redirects=False, base_url="http://127.0.0.1:8097")
    if logged_in:
        client.cookies.update(session_cookies())
    return client


class OriginProblemTests(unittest.TestCase):
    H = "research.example.com"

    def check(self, method="POST", **headers):
        return csrf.origin_problem(method, {"host": self.H, **{k.replace("_", "-"): v for k, v in headers.items()}})

    def test_safe_methods_are_never_checked(self):
        for m in ("GET", "HEAD", "OPTIONS"):
            self.assertIsNone(self.check(m, origin="https://evil.example"))

    def test_same_origin_passes(self):
        self.assertIsNone(self.check(origin=f"https://{self.H}"))
        self.assertIsNone(self.check(origin=f"https://{self.H.upper()}"))

    def test_cross_origin_rejected(self):
        for o in ("https://evil.example", f"https://{self.H}.evil.example", f"https://sub.{self.H}", "null", ""):
            self.assertIsNotNone(self.check(origin=o), o)

    def test_port_must_match(self):
        self.assertIsNotNone(csrf.origin_problem("POST", {"host": "127.0.0.1:8097", "origin": "http://127.0.0.1:5173"}))
        self.assertIsNone(csrf.origin_problem("POST", {"host": "127.0.0.1:8097", "origin": "http://127.0.0.1:8097"}))

    def test_no_origin_falls_back_to_fetch_metadata(self):
        self.assertIsNotNone(self.check(sec_fetch_site="cross-site"))
        self.assertIsNotNone(self.check(sec_fetch_site="same-site"))
        self.assertIsNone(self.check(sec_fetch_site="same-origin"))
        self.assertIsNone(self.check(sec_fetch_site="none"))

    def test_non_browser_clients_pass(self):
        self.assertIsNone(self.check())


class CsrfMiddlewareTests(unittest.TestCase):
    def setUp(self):
        auth._FAILS.clear()

    def test_cross_site_api_post_is_rejected_before_auth(self):
        r = _client().post("/api/ask/stop", json={}, headers={"origin": "https://evil.example"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["code"], "csrf_rejected")

    def test_cross_site_login_post_is_rejected(self):
        r = _client().post("/login", data={"username": "tester", "password": "testpass"},
                           headers={"origin": "https://evil.example"})
        self.assertEqual(r.status_code, 403)
        self.assertNotIn(auth.COOKIE_NAME, r.cookies)

    def test_same_origin_login_post_passes(self):
        r = _client().post("/login", data={"username": "tester", "password": "testpass"},
                           headers={"origin": "http://127.0.0.1:8097", "sec-fetch-site": "same-origin"})
        self.assertEqual(r.status_code, 303)

    def test_cross_site_get_is_not_blocked(self):
        r = _client().get("/healthz", headers={"origin": "https://evil.example"})
        self.assertNotEqual(r.status_code, 403)


class ErrorContractTests(unittest.TestCase):
    def assertContract(self, r, status, code):
        self.assertEqual(r.status_code, status, r.text)
        body = r.json()
        self.assertEqual(body["code"], code)
        self.assertIn("detail", body)
        self.assertTrue(body["request_id"])
        self.assertEqual(r.headers.get("x-request-id"), body["request_id"])

    def test_unauthenticated(self):
        self.assertContract(_client().get("/api/me"), 401, "unauthenticated")

    def test_not_found_route(self):
        self.assertContract(_client(logged_in=True).get("/api/no-such-route"), 404, "not_found")

    def test_validation_error_keeps_field_list_in_detail(self):
        r = _client(logged_in=True).get("/api/admin/audit", params={"limit": 0})
        self.assertContract(r, 422, "validation_error")
        self.assertIsInstance(r.json()["detail"], list)

    def test_http_exception_detail_is_unchanged(self):
        r = _client(logged_in=True).patch("/api/admin/users/00000000-0000-0000-0000-000000000000", json={})
        self.assertContract(r, 400, "bad_request")
        self.assertEqual(r.json()["detail"], "沒有要變更的欄位（role 或 enabled）")


if __name__ == "__main__":
    unittest.main()
