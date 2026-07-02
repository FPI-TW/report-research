# tests/test_auth.py
import os
import unittest
from types import SimpleNamespace

# web.auth 匯入時即讀取共用帳密(fail-closed),故須在匯入前設好測試用值。
os.environ.setdefault("REPORT_MARK_ACCESS_USERNAME", "tester")
os.environ.setdefault("REPORT_MARK_ACCESS_PASSWORD", "testpass")
os.environ.setdefault("REPORT_MARK_SESSION_SECRET", "fixed-test-secret-0123456789")

from web import auth  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from web.server import app  # noqa: E402


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

    def test_non_ascii_signature_segment_rejected(self):
        # 簽章段含非 ASCII 不應崩潰,應視為無效
        self.assertFalse(auth.verify_token("1000000.café", 999))


class CredentialTests(unittest.TestCase):
    def test_correct_credentials_accepted(self):
        self.assertTrue(auth.check_credentials("tester", "testpass"))

    def test_wrong_password_rejected(self):
        self.assertFalse(auth.check_credentials("tester", "nope"))

    def test_wrong_username_rejected(self):
        self.assertFalse(auth.check_credentials("nobody", "testpass"))

    def test_empty_credentials_rejected(self):
        self.assertFalse(auth.check_credentials("", ""))

    def test_non_ascii_credentials_rejected(self):
        # 含中文/非 ASCII 的帳密不應崩潰,應回 False
        self.assertFalse(auth.check_credentials("使用者", "密碼"))


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

    def test_record_failure_prunes_globally_stale_ips(self):
        now = 2_000_000
        auth._FAILS.update(
            {
                "10.0.0.1": [now - auth.FAIL_WINDOW - 10],
                "10.0.0.2": [now - auth.FAIL_WINDOW - 20],
            }
        )

        auth.record_failure("10.0.0.9", now)

        self.assertEqual(set(auth._FAILS), {"10.0.0.9"})

    def test_record_failure_caps_total_tracked_ips(self):
        now = 2_000_000
        orig_limit = getattr(auth, "MAX_TRACKED_IPS", None)
        try:
            auth.MAX_TRACKED_IPS = 8
            for i in range(9):
                auth.record_failure(f"10.0.0.{i}", now + i)
        finally:
            if orig_limit is None:
                delattr(auth, "MAX_TRACKED_IPS")
            else:
                auth.MAX_TRACKED_IPS = orig_limit

        self.assertLessEqual(len(auth._FAILS), 8)


def _client():
    return TestClient(app, follow_redirects=False, base_url="http://127.0.0.1")


def _client_for(base_url: str):
    return TestClient(app, follow_redirects=False, base_url=base_url)


class ClientIpTests(unittest.TestCase):
    def test_direct_client_ignores_spoofed_x_real_ip(self):
        req = SimpleNamespace(
            headers={"x-real-ip": "8.8.8.8"},
            client=SimpleNamespace(host="192.168.1.50"),
        )
        self.assertEqual(auth.client_ip(req), "192.168.1.50")

    def test_loopback_proxy_can_supply_x_real_ip(self):
        req = SimpleNamespace(
            headers={"x-real-ip": "8.8.8.8"},
            client=SimpleNamespace(host="127.0.0.1"),
        )
        self.assertEqual(auth.client_ip(req), "8.8.8.8")


class RequestSecurityTests(unittest.TestCase):
    def test_untrusted_forwarded_proto_does_not_mark_request_secure(self):
        req = SimpleNamespace(
            url=SimpleNamespace(scheme="http"),
            headers={"x-forwarded-proto": "https"},
            client=SimpleNamespace(host="192.168.1.50"),
        )
        self.assertFalse(auth.request_is_secure(req))

    def test_trusted_loopback_forwarded_proto_marks_request_secure(self):
        req = SimpleNamespace(
            url=SimpleNamespace(scheme="http"),
            headers={"x-forwarded-proto": "https"},
            client=SimpleNamespace(host="127.0.0.1"),
        )
        self.assertTrue(auth.request_is_secure(req))


class AuthFlowTests(unittest.TestCase):
    def setUp(self):
        auth._FAILS.clear()

    def tearDown(self):
        auth._FAILS.clear()

    def test_unauthed_html_redirects_to_login(self):
        r = _client().get("/")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.headers["location"], "/login")

    def test_unauthed_api_returns_401(self):
        r = _client().get("/api/stats")
        self.assertEqual(r.status_code, 401)

    def test_login_page_served_without_auth(self):
        r = _client().get("/login")
        self.assertEqual(r.status_code, 200)
        self.assertIn('name="username"', r.text)
        self.assertIn('name="password"', r.text)
        self.assertNotIn("請登入以使用研究報告檢索", r.text)
        self.assertIn('class="brand-row"', r.text)

    def test_wrong_credentials_redirect_with_error(self):
        r = _client().post("/login", data={"username": "tester", "password": "bad"})
        self.assertEqual(r.status_code, 303)
        self.assertIn("error=1", r.headers["location"])
        self.assertNotIn(auth.COOKIE_NAME, r.cookies)

    def test_correct_credentials_set_cookie_and_grant_access(self):
        client = _client()
        r = client.post("/login", data={"username": "tester", "password": "testpass"})
        self.assertEqual(r.status_code, 303)
        self.assertEqual(r.headers["location"], "/")
        self.assertIn(auth.COOKIE_NAME, r.cookies)
        # cutover 後 / 對已認證者導向 SPA 檢索頁（不再直接回 200）。
        r2 = client.get("/", follow_redirects=False)
        self.assertEqual(r2.status_code, 307)
        self.assertEqual(r2.headers["location"], "/app/search")

    def test_logout_clears_session(self):
        client = _client()
        client.post("/login", data={"username": "tester", "password": "testpass"})
        client.get("/")
        client.post("/logout")
        r = client.get("/")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.headers["location"], "/login")

    def test_lockout_after_repeated_failures(self):
        client = _client()
        for _ in range(auth.MAX_FAILS):
            client.post("/login", data={"username": "tester", "password": "bad"})
        r = client.post("/login", data={"username": "tester", "password": "bad"})
        self.assertEqual(r.status_code, 303)
        self.assertIn("error=locked", r.headers["location"])

    def test_https_login_sets_secure_cookie(self):
        client = _client_for("https://research.example.com")
        r = client.post("/login", data={"username": "tester", "password": "testpass"})
        self.assertEqual(r.status_code, 303)
        self.assertIn("Secure", r.headers["set-cookie"])

    def test_plain_http_non_loopback_login_rejected(self):
        client = _client_for("http://research.office")
        r = client.post("/login", data={"username": "tester", "password": "testpass"})
        self.assertEqual(r.status_code, 303)
        self.assertIn("error=insecure", r.headers["location"])
        self.assertNotIn(auth.COOKIE_NAME, r.cookies)

    def test_spoofed_forwarded_proto_does_not_bypass_http_login_block(self):
        client = _client_for("http://research.office")
        r = client.post(
            "/login",
            data={"username": "tester", "password": "testpass"},
            headers={"x-forwarded-proto": "https"},
        )
        self.assertEqual(r.status_code, 303)
        self.assertIn("error=insecure", r.headers["location"])

    def test_authed_request_refreshes_cookie(self):
        # 每次通過認證的回應都應重新簽發 session cookie(滑動到期)。
        # cutover 後 / 回 307 導向，middleware 仍應在該（已認證）回應上重簽 cookie。
        client = _client()
        client.post("/login", data={"username": "tester", "password": "testpass"})
        r = client.get("/", follow_redirects=False)
        self.assertEqual(r.status_code, 307)
        self.assertIn(auth.COOKIE_NAME, r.cookies)

    def test_avatar_asset_served(self):
        # cutover 後首頁改由 SPA 服務；帳號頭像移至 SPA 導覽列，仍取用同一靜態圖片。
        client = _client()
        client.post("/login", data={"username": "tester", "password": "testpass"})
        r = client.get("/static/img/avatar.jpg")
        self.assertEqual(r.status_code, 200)

    def test_unauthed_static_is_gated(self):
        # /static 不在白名單:未登入直接取 /static/index.html 應被擋(防繞過 route 門檻)
        r = _client().get("/static/index.html")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.headers["location"], "/login")


class HistoryDeleteApiTests(unittest.TestCase):
    def _authed_client(self):
        client = _client()
        r = client.post("/login", data={"username": "tester", "password": "testpass"})
        self.assertEqual(r.status_code, 303)
        return client

    def test_delete_history_endpoint(self):
        import web.server as server

        seen = {}

        async def fake_delete_qa(qa_id: str):
            seen["qa_id"] = qa_id
            return True

        orig = server.delete_qa
        server.delete_qa = fake_delete_qa
        try:
            client = self._authed_client()
            qa_id = "11111111-1111-1111-1111-111111111111"
            r = client.delete(f"/api/history/{qa_id}")
        finally:
            server.delete_qa = orig

        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"ok": True})
        self.assertEqual(seen["qa_id"], qa_id)


class InputLimitTests(unittest.TestCase):
    def _authed_client(self):
        client = _client()
        r = client.post("/login", data={"username": "tester", "password": "testpass"})
        self.assertEqual(r.status_code, 303)
        return client

    def test_search_rejects_overlong_query(self):
        import web.server as server

        async def fake_hybrid_search(*_a, **_k):
            return []

        orig_embed = server.embed_query_cached
        orig_search = server.hybrid_search
        server.embed_query_cached = lambda _q: [0.0]
        server.hybrid_search = fake_hybrid_search
        try:
            client = self._authed_client()
            r = client.get("/api/search", params={"q": "x" * 5001})
        finally:
            server.embed_query_cached = orig_embed
            server.hybrid_search = orig_search

        self.assertEqual(r.status_code, 422)

    def test_ask_rejects_overlong_question(self):
        import web.server as server

        async def fake_answer_question(*_a, **_k):
            yield ("done", {"cited": []})

        orig_answer_question = server.answer_question
        server.answer_question = fake_answer_question
        try:
            client = self._authed_client()
            r = client.post("/api/ask", json={"question": "x" * 5001})
        finally:
            server.answer_question = orig_answer_question

        self.assertEqual(r.status_code, 422)

    def test_post_delete_history_alias(self):
        import web.server as server

        seen = {}

        async def fake_delete_qa(qa_id: str):
            seen["qa_id"] = qa_id
            return True

        orig = server.delete_qa
        server.delete_qa = fake_delete_qa
        try:
            client = self._authed_client()
            qa_id = "22222222-2222-2222-2222-222222222222"
            r = client.post(f"/api/history/{qa_id}/delete")
        finally:
            server.delete_qa = orig

        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"ok": True})
        self.assertEqual(seen["qa_id"], qa_id)


if __name__ == "__main__":
    unittest.main()
