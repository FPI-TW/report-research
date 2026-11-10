"""OIDC 單一登入的 HTTP 層：以本機假 IdP 跑完整的授權碼＋PKCE 流程，並驗每一種該失敗的情況。

假 IdP（`FakeIdP`）經 `app.services.oidc._transport` 的 `httpx.MockTransport` 提供 discovery、JWKS 與 token 端點；
簽 ID token 的 RSA 金鑰在執行期產生（不寫進 repo）。授權端點不需要真的存在：測試從 GET /login/oidc 的 302 取出
參數，假裝使用者在 IdP 登入成功，再以 IdP 會帶的 code／state 打 callback。帳號用 `tests/fake_accounts.py`
（SQL 本身在 tests/test_accounts_db.py 的 `scenario_sso_*`）。

全程不連網；client secret 在執行期組字串（gitleaks 的假祕密前綴）。
"""

from __future__ import annotations

import base64
import dataclasses
import hashlib
import json
import logging
import time
import unittest
import uuid
from contextlib import contextmanager
from unittest import mock
from urllib.parse import parse_qs, urlsplit

import httpx
from fake_accounts import FakeAccounts, install
from fastapi.testclient import TestClient
from joserfc import jwt
from joserfc.jwk import OctKey, RSAKey

from app import config
from app.config import OidcSettings
from app.services import oidc, security_ops
from web import auth
from web.server import app

ISSUER = "https://idp.example.test/realms/corp"
CLIENT_ID = "report-mark"
CLIENT_SECRET = "fixed-test-secret-" + "oidcclient"
REDIRECT_URI = "http://127.0.0.1/login/oidc/callback"
PW = "correct-horse-battery-1"


def _rsa(kid: str) -> RSAKey:
    return RSAKey.generate_key(2048, parameters={"kid": kid, "use": "sig", "alg": "RS256"}, private=True)


# 產生 RSA 金鑰要幾十毫秒：整個模組共用三把（現行、輪替後、攻擊者自己的）。
KEY_A, KEY_B, ATTACKER = _rsa("key-a"), _rsa("key-b"), _rsa("key-a")


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


class FakeIdP:
    """最小的 OIDC 供應者：discovery、JWKS、token 端點（驗 PKCE 與 client 認證）。"""

    def __init__(self) -> None:
        self.keys = [KEY_A]
        self.signing_key = KEY_A
        self.codes: dict[str, dict] = {}
        self.jwks_fetches = 0
        self.token_requests: list[dict] = []
        self.discovery_issuer = ISSUER
        self.token_status = 200
        # 下一次發 token 時要覆寫／刪掉的 claims、改用的 header（測試竄改用）。
        self.claim_overrides: dict = {}
        self.header_overrides: dict = {}
        self.sign_with = None

    def metadata(self) -> dict:
        return {
            "issuer": self.discovery_issuer,
            "authorization_endpoint": f"{ISSUER}/protocol/openid-connect/auth",
            "token_endpoint": f"{ISSUER}/protocol/openid-connect/token",
            "jwks_uri": f"{ISSUER}/protocol/openid-connect/certs",
            "response_types_supported": ["code"],
            "id_token_signing_alg_values_supported": ["RS256"],
            "token_endpoint_auth_methods_supported": ["client_secret_basic", "client_secret_post"],
        }

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == f"{ISSUER}/.well-known/openid-configuration":
            return httpx.Response(200, json=self.metadata())
        if url == self.metadata()["jwks_uri"]:
            self.jwks_fetches += 1
            return httpx.Response(200, json={"keys": [k.as_dict(private=False) for k in self.keys]})
        if url == self.metadata()["token_endpoint"] and request.method == "POST":
            return self._token(request)
        return httpx.Response(404)

    def authorize(self, location: str, *, sub: str = "sub-alice", username: str = "alice@corp.example",
                  extra: dict | None = None) -> dict:
        """使用者在 IdP 登入成功：記下這次授權的參數，回 IdP 導回本系統時會帶的 query。"""
        q = {k: v[0] for k, v in parse_qs(urlsplit(location).query).items()}
        code = uuid.uuid4().hex
        self.codes[code] = {"q": q, "sub": sub, "username": username, "extra": extra or {}}
        return {"code": code, "state": q["state"]}

    def _token(self, request: httpx.Request) -> httpx.Response:
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        self.token_requests.append({"form": form, "authorization": request.headers.get("authorization")})
        if self.token_status != 200:
            return httpx.Response(self.token_status, json={"error": "invalid_grant"})
        grant = self.codes.pop(form.get("code", ""), None)
        if grant is None or form.get("grant_type") != "authorization_code":
            return httpx.Response(400, json={"error": "invalid_grant"})
        q = grant["q"]
        expected_basic = "Basic " + base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
        if request.headers.get("authorization") != expected_basic:
            return httpx.Response(401, json={"error": "invalid_client"})
        challenge = _b64url(hashlib.sha256(form.get("code_verifier", "").encode()).digest())
        if q.get("code_challenge_method") != "S256" or challenge != q.get("code_challenge"):
            return httpx.Response(400, json={"error": "invalid_grant"})
        if form.get("redirect_uri") != q.get("redirect_uri"):
            return httpx.Response(400, json={"error": "invalid_grant"})
        now = int(time.time())
        claims = {"iss": ISSUER, "aud": CLIENT_ID, "sub": grant["sub"], "preferred_username": grant["username"],
                  "iat": now, "exp": now + 300, "auth_time": now, "nonce": q["nonce"], **grant["extra"]}
        claims.update(self.claim_overrides)
        claims = {k: v for k, v in claims.items() if v is not None}
        key = self.sign_with or self.signing_key
        header = {"alg": "RS256", "kid": key.kid, **self.header_overrides}
        id_token = jwt.encode(header, claims, key, algorithms=[header["alg"]])
        return httpx.Response(200, json={"access_token": "opaque-access", "token_type": "Bearer",
                                         "id_token": id_token, "expires_in": 300})


def _settings(**changes) -> OidcSettings:
    base = OidcSettings(issuer=ISSUER, client_id=CLIENT_ID, client_secret=CLIENT_SECRET, redirect_uri=REDIRECT_URI)
    return dataclasses.replace(base, **changes)


@contextmanager
def _patched(oidc_settings: OidcSettings | None, **other):
    patched = dataclasses.replace(config.get_settings(), oidc=oidc_settings or OidcSettings(), **other)
    with mock.patch.object(config, "_SETTINGS", patched):
        yield


class _Base(unittest.TestCase):
    oidc_changes: dict = {}

    def setUp(self):
        auth._FAILS.clear()
        security_ops.reset()
        oidc.reset_cache()
        self.idp = FakeIdP()
        self.store = FakeAccounts()
        self.root = self.store.add_user("root", PW, "admin", is_super=True)
        self._stack = [
            install(self.store),
            mock.patch.object(oidc, "_transport", httpx.MockTransport(self.idp.handler)),
            _patched(_settings(**self.oidc_changes)),
        ]
        for ctx in self._stack:
            ctx.__enter__()
        self.client = TestClient(app, follow_redirects=False, base_url="http://127.0.0.1")

    def tearDown(self):
        for ctx in reversed(self._stack):
            ctx.__exit__(None, None, None)
        oidc.reset_cache()
        security_ops.reset()

    def events(self, event: str | None = None) -> list[dict]:
        return [e for e in self.store.auth_events if event is None or e["event"] == event]

    def start(self, path: str = "/login/oidc") -> str:
        resp = self.client.get(path)
        self.assertEqual(resp.status_code, 302, resp.text)
        self.assertIn(auth.OIDC_COOKIE_NAME, resp.cookies)
        return resp.headers["location"]

    def login(self, *, next_path: str | None = None, **authorize) -> httpx.Response:
        location = self.start("/login/oidc" + (f"?next={next_path}" if next_path else ""))
        return self.client.get("/login/oidc/callback", params=self.idp.authorize(location, **authorize))

    def assert_error(self, resp, code: str):
        self.assertEqual(resp.status_code, 303, resp.text)
        self.assertTrue(resp.headers["location"].startswith(f"/login?error={code}"), resp.headers["location"])
        self.assertNotIn(auth.COOKIE_NAME, resp.cookies)

    def me(self):
        return self.client.get("/api/me")


class FullFlowTests(_Base):
    def test_authorization_code_flow_with_pkce_creates_plain_user(self):
        location = self.start("/login/oidc?next=/app/assistant")
        parts = urlsplit(location)
        self.assertEqual(f"{parts.scheme}://{parts.netloc}{parts.path}", f"{ISSUER}/protocol/openid-connect/auth")
        q = {k: v[0] for k, v in parse_qs(parts.query).items()}
        self.assertEqual(q["response_type"], "code")
        self.assertEqual(q["client_id"], CLIENT_ID)
        self.assertEqual(q["redirect_uri"], REDIRECT_URI)
        self.assertEqual(q["code_challenge_method"], "S256")
        self.assertEqual(q["scope"].split(), ["openid", "email", "profile"])
        self.assertGreaterEqual(len(q["state"]), 32)
        self.assertGreaterEqual(len(q["nonce"]), 32)
        self.assertNotIn("prompt", q)

        resp = self.client.get("/login/oidc/callback", params=self.idp.authorize(location))
        self.assertEqual(resp.status_code, 303, resp.text)
        self.assertEqual(resp.headers["location"], "/app/assistant")
        self.assertIn(auth.COOKIE_NAME, resp.cookies)
        # PKCE：token 端點收到的 verifier 對得上 challenge（FakeIdP 驗過才發 token），client 以 Basic 認證。
        sent = self.idp.token_requests[-1]
        self.assertNotIn("client_secret", sent["form"])
        self.assertTrue(sent["authorization"].startswith("Basic "))

        me = self.me().json()
        self.assertEqual((me["username"], me["role"], me["auth_method"]), ("alice@corp.example", "user", "sso"))
        self.assertEqual(me["scopes"], [])
        row = self.store._by_name("alice@corp.example")
        self.assertEqual((row.oidc_issuer, row.oidc_subject), (ISSUER, "sub-alice"))
        created = [e for e in self.store.audit if e.action == "user.create" and e.target_id == row.id]
        self.assertEqual(len(created), 1)
        self.assertIsNone(created[0].actor_user_id)  # JIT 是系統行為
        self.assertEqual(created[0].detail["via"], "sso_jit")
        ok = self.events("login.success")
        self.assertEqual([(e["reason"], e["user_id"]) for e in ok], [("sso", row.id)])

    def test_second_login_reuses_binding_and_flow_cookie_is_cleared(self):
        first = self.login()
        self.assertEqual(first.status_code, 303)
        self.assertIn(f'{auth.OIDC_COOKIE_NAME}=""', first.headers.get("set-cookie", ""))
        uid = self.store._by_name("alice@corp.example").id
        self.client.cookies.clear()
        again = self.login(username="renamed@corp.example")
        self.assertEqual(again.status_code, 303)
        self.assertEqual(self.me().json()["id"], uid)
        self.assertEqual(sum(1 for e in self.store.audit if e.action == "user.create"), 1)

    def test_existing_bound_account_keeps_system_granted_admin(self):
        uid = self.store.add_user("bob", FakeAccounts.SSO_PASSWORD_HASH, "admin", oidc=(ISSUER, "sub-bob"))
        resp = self.login(sub="sub-bob", username="whatever@corp.example")
        self.assertEqual(resp.status_code, 303)
        me = self.me().json()
        self.assertEqual((me["id"], me["username"], me["role"]), (uid, "bob", "admin"))
        self.assertEqual(self.client.get("/api/admin/users").status_code, 200)

    def test_disabled_account_is_refused(self):
        uid = self.store.add_user("carol", "x", "user", enabled=False, oidc=(ISSUER, "sub-carol"))
        self.assert_error(self.login(sub="sub-carol"), "disabled")
        self.assertEqual([(e["reason"], e["user_id"]) for e in self.events("login.sso_failure")], [("disabled", uid)])

    def test_session_is_revocable_like_any_other(self):
        self.login()
        uid = self.store._by_name("alice@corp.example").id
        self.assertEqual(self.me().status_code, 200)
        self.store._revoke_all(uid)
        self.assertEqual(self.me().status_code, 401)

    def test_unsafe_next_falls_back_to_root(self):
        resp = self.login(next_path="//evil.example/x")
        self.assertEqual(resp.headers["location"], "/")

    def test_no_secret_reaches_the_logs(self):
        with self.assertLogs(level=logging.DEBUG) as logs:
            location = self.start()
            params = self.idp.authorize(location)
            resp = self.client.get("/login/oidc/callback", params=params)
        self.assertEqual(resp.status_code, 303)
        text = "\n".join(logs.output)
        token = self.idp.token_requests[-1]["form"]
        for secret in (CLIENT_SECRET, params["code"], params["state"], token["code_verifier"]):
            self.assertNotIn(secret, text)
        self.assertNotIn("eyJ", text)  # 任何 JWT 的開頭


class AccountLinkTests(_Base):
    def test_existing_local_account_is_not_taken_over_by_default(self):
        uid = self.store.add_user("alice@corp.example", PW, "admin")
        self.assert_error(self.login(), "sso_account")
        self.assertEqual([(e["reason"], e["user_id"]) for e in self.events("login.sso_failure")],
                         [("username_conflict", uid)])
        self.assertIsNone(self.store.users[uid].oidc_subject)

    def test_invalid_username_claim(self):
        self.assert_error(self.login(username="has space"), "sso_account")
        self.assertEqual([e["reason"] for e in self.events("login.sso_failure")], ["invalid_username"])


class LinkByUsernameTests(_Base):
    oidc_changes = {"link_by_username": True, "break_glass_users": ("root",)}

    def test_links_existing_account_once(self):
        uid = self.store.add_user("Alice@Corp.Example", PW, "user")
        self.assertEqual(self.login().status_code, 303)
        self.assertEqual(self.me().json()["id"], uid)
        self.assertEqual(self.store.users[uid].oidc_subject, "sub-alice")
        links = [e for e in self.store.audit if e.action == "user.sso_link"]
        self.assertEqual([(e.target_id, e.actor_user_id) for e in links], [(uid, None)])

    def test_break_glass_account_is_never_linked(self):
        self.assert_error(self.login(username="root"), "sso_account")
        self.assertEqual([e["reason"] for e in self.events("login.sso_failure")], ["break_glass"])
        self.assertIsNone(self.store.users[self.root].oidc_subject)


class TokenValidationTests(_Base):
    """竄改 state／nonce／aud／簽章、過期、未知 issuer：一律不發 session。"""

    def _token_failure(self, resp):
        self.assert_error(resp, "sso")
        self.assertEqual([e["reason"] for e in self.events("login.sso_failure")], ["token"])
        self.assertEqual(len(self.store.sessions), 0)

    def test_tampered_state(self):
        location = self.start()
        params = self.idp.authorize(location)
        params["state"] = params["state"][:-2] + "xx"
        resp = self.client.get("/login/oidc/callback", params=params)
        self.assert_error(resp, "sso")
        self.assertEqual(self.idp.token_requests, [])  # 沒有拿授權碼去換
        self.assertEqual(security_ops.SSO_STATE_TALLY.pending, 1)  # 只在記憶體計數
        self.assertEqual(self.events(), [])

    def test_missing_flow_cookie(self):
        params = self.idp.authorize(self.start())
        self.client.cookies.clear()
        self.assert_error(self.client.get("/login/oidc/callback", params=params), "sso")
        self.assertEqual(self.idp.token_requests, [])

    def test_forged_flow_cookie(self):
        params = self.idp.authorize(self.start())
        forged = base64.urlsafe_b64encode(json.dumps({
            "s": params["state"], "n": "n", "v": "v", "x": "/", "i": "login", "sid": None,
            "e": int(time.time()) + 60}).encode()).decode().rstrip("=")
        self.client.cookies.clear()
        self.client.cookies.set(auth.OIDC_COOKIE_NAME, f"o1.{forged}.not-a-signature", path=auth.OIDC_COOKIE_PATH)
        self.assert_error(self.client.get("/login/oidc/callback", params=params), "sso")
        self.assertEqual(self.idp.token_requests, [])

    def test_nonce_mismatch(self):
        self.idp.claim_overrides = {"nonce": "replayed-nonce"}
        self._token_failure(self.login())

    def test_missing_nonce(self):
        self.idp.claim_overrides = {"nonce": None}
        self._token_failure(self.login())

    def test_wrong_audience(self):
        self.idp.claim_overrides = {"aud": "some-other-app"}
        self._token_failure(self.login())

    def test_multiple_audiences_need_matching_azp(self):
        self.idp.claim_overrides = {"aud": [CLIENT_ID, "other"], "azp": "other"}
        self._token_failure(self.login())

    def test_expired_token(self):
        now = int(time.time())
        self.idp.claim_overrides = {"iat": now - 3600, "exp": now - 1800}
        self._token_failure(self.login())

    def test_unknown_issuer(self):
        self.idp.claim_overrides = {"iss": "https://evil.example.test/realms/corp"}
        self._token_failure(self.login())

    def test_signature_from_a_key_not_in_jwks(self):
        self.idp.sign_with = ATTACKER  # 同一個 kid，不同的金鑰
        self._token_failure(self.login())

    def test_hs256_signed_with_the_client_secret_is_rejected(self):
        self.idp.sign_with = OctKey.import_key(CLIENT_SECRET.encode())
        self.idp.header_overrides = {"alg": "HS256", "kid": "key-a"}
        self._token_failure(self.login())

    def test_token_endpoint_error(self):
        self.idp.token_status = 400
        self._token_failure(self.login())

    def test_idp_error_response(self):
        location = self.start()
        q = {k: v[0] for k, v in parse_qs(urlsplit(location).query).items()}
        resp = self.client.get("/login/oidc/callback", params={"error": "access_denied", "state": q["state"]})
        self.assert_error(resp, "sso")
        self.assertEqual(security_ops.SSO_IDP_ERROR_TALLY.pending, 1)

    def test_clock_skew_is_tolerated(self):
        now = int(time.time())
        self.idp.claim_overrides = {"iat": now + 60}  # IdP 的時鐘快一分鐘（預設容忍 120 秒）
        self.assertEqual(self.login().status_code, 303)


class DiscoveryAndKeyRotationTests(_Base):
    def test_discovery_issuer_mismatch_refuses_to_start(self):
        self.idp.discovery_issuer = "https://idp.example.test/realms/other"
        resp = self.client.get("/login/oidc")
        self.assertEqual(resp.status_code, 303)
        self.assertTrue(resp.headers["location"].startswith("/login?error=sso_unavailable"))

    def test_idp_unreachable(self):
        def down(request):
            raise httpx.ConnectError("down", request=request)

        with mock.patch.object(oidc, "_transport", httpx.MockTransport(down)):
            resp = self.client.get("/login/oidc")
        self.assertTrue(resp.headers["location"].startswith("/login?error=sso_unavailable"))

    def test_key_rotation_refetches_jwks_once(self):
        self.assertEqual(self.login().status_code, 303)
        self.assertEqual(self.idp.jwks_fetches, 1)
        self.client.cookies.clear()
        self.idp.keys, self.idp.signing_key = [KEY_B], KEY_B  # IdP 換了金鑰
        oidc._cache.jwks_at -= oidc.JWKS_MIN_REFRESH_SECONDS  # 距上次抓 JWKS 已超過重抓的最短間隔
        self.assertEqual(self.login().status_code, 303)
        self.assertEqual(self.idp.jwks_fetches, 2)
        self.client.cookies.clear()
        self.assertEqual(self.login().status_code, 303)  # 新金鑰已在快取
        self.assertEqual(self.idp.jwks_fetches, 2)

    def test_unknown_kid_does_not_hammer_the_idp(self):
        self.assertEqual(self.login().status_code, 303)
        for _ in range(3):
            self.client.cookies.clear()
            self.idp.header_overrides = {"kid": f"nope-{uuid.uuid4().hex[:6]}"}
            self.assert_error(self.login(), "sso")
        self.assertLessEqual(self.idp.jwks_fetches, 2)


class DomainTests(_Base):
    oidc_changes = {"allowed_domains": ("corp.example",)}

    def test_allowed_domain(self):
        self.assertEqual(self.login(username="dave@CORP.example").status_code, 303)

    def test_other_domain_is_refused(self):
        self.assert_error(self.login(username="mallory@other.example"), "sso")
        self.assertEqual([e["reason"] for e in self.events("login.sso_failure")], ["domain"])
        self.assertIsNone(self.store._by_name("mallory@other.example"))


class InsecureTests(_Base):
    def test_plain_http_from_outside_is_refused(self):
        client = TestClient(app, follow_redirects=False, base_url="http://research.example.test")
        with mock.patch.object(auth, "allow_insecure_local", return_value=False):
            resp = client.get("/login/oidc")
        self.assertEqual(resp.status_code, 303)
        self.assertTrue(resp.headers["location"].startswith("/login?error=insecure"))


class NotConfiguredTests(unittest.TestCase):
    """OIDC_ISSUER 沒設：兩條路由 404、登入頁沒有公司帳號按鈕（與沒有這個功能時一樣）。"""

    def test_routes_404_and_page_unchanged(self):
        client = TestClient(app, follow_redirects=False, base_url="http://127.0.0.1")
        self.assertFalse(config.get_settings().oidc.enabled)
        self.assertEqual(client.get("/login/oidc").status_code, 404)
        self.assertEqual(client.get("/login/oidc/callback?code=x&state=y").status_code, 404)
        page = client.get("/login").text
        self.assertIn('data-sso="off"', page)
        self.assertIn("請以管理者配發的帳號登入。", page)
        self.assertNotIn("{{", page)


class LoginPageTests(_Base):
    def test_button_shown_when_configured(self):
        page = self.client.get("/login").text
        self.assertIn('data-sso="on"', page)
        self.assertIn('href="/login/oidc"', page)
        self.assertIn("以公司帳號登入", page)
        self.assertNotIn("{{", page)


class SsoOnlyTests(_Base):
    oidc_changes = {"sso_only": True, "break_glass_users": ("root",)}

    def setUp(self):
        super().setUp()
        self.member = self.store.add_user("member", PW, "user")

    def test_page_collapses_local_form(self):
        page = self.client.get("/login").text
        self.assertIn('data-sso="only"', page)
        self.assertIn("緊急管理員登入", page)

    def test_local_password_only_for_break_glass_admin(self):
        resp = self.client.post("/login", data={"username": "member", "password": PW})
        self.assertEqual(resp.headers["location"], "/login?error=sso_only")
        self.assertNotIn(auth.COOKIE_NAME, resp.cookies)
        self.assertEqual([e["reason"] for e in self.events("login.failure")], ["sso_only"])
        resp = self.client.post("/login", data={"username": "root", "password": PW})
        self.assertEqual(resp.headers["location"], "/")
        self.assertEqual(self.me().json()["username"], "root")

    def test_sso_still_works(self):
        self.assertEqual(self.login().status_code, 303)
        self.assertEqual(self.me().json()["auth_method"], "sso")


class AdminMfaTests(_Base):
    """ADMIN_MFA_REQUIRED 對 SSO 管理員：看這次登入 IdP 的 amr／acr，不看本系統 TOTP。"""

    def setUp(self):
        super().setUp()
        self.uid = self.store.add_user("erin", FakeAccounts.SSO_PASSWORD_HASH, "admin", oidc=(ISSUER, "sub-erin"))
        self._mfa = mock.patch.object(config, "_SETTINGS",
                                      dataclasses.replace(config.get_settings(), admin_mfa_required=True))
        self._mfa.__enter__()

    def tearDown(self):
        self._mfa.__exit__(None, None, None)
        super().tearDown()

    def test_without_idp_mfa_admin_api_is_refused(self):
        self.login(sub="sub-erin", extra={"amr": ["pwd"]})
        me = self.me().json()
        self.assertTrue(me["sso_mfa_required"])
        self.assertFalse(me["mfa_enrollment_required"])  # 不是叫他去設本系統 TOTP
        resp = self.client.get("/api/admin/users")
        self.assertEqual((resp.status_code, resp.json()["code"]), (403, "sso_mfa_required"))
        # 在 SSO session 裡開本系統 TOTP 不能拿來抵 IdP 的 MFA
        self.store.users[self.uid].totp_enabled = True
        self.store.users[self.uid].totp_secret = "JBSWY3DPEHPK3PXP"
        self.assertEqual(self.client.get("/api/admin/users").status_code, 403)

    def test_amr_mfa_satisfies_policy(self):
        self.login(sub="sub-erin", extra={"amr": ["pwd", "mfa"]})
        self.assertFalse(self.me().json()["sso_mfa_required"])
        self.assertEqual(self.client.get("/api/admin/users").status_code, 200)

    def test_acr_value_satisfies_policy(self):
        with _patched(_settings(mfa_acr=("gold",)), admin_mfa_required=True):
            self.login(sub="sub-erin", extra={"acr": "gold"})
            self.assertEqual(self.client.get("/api/admin/users").status_code, 200)

    def test_assume_idp_mfa(self):
        with _patched(_settings(assume_idp_mfa=True), admin_mfa_required=True):
            self.login(sub="sub-erin")
            self.assertEqual(self.client.get("/api/admin/users").status_code, 200)

    def test_policy_off_does_not_care(self):
        with _patched(_settings(), admin_mfa_required=False):
            self.login(sub="sub-erin")
            self.assertEqual(self.client.get("/api/admin/users").status_code, 200)


class ElevationTests(_Base):
    def setUp(self):
        super().setUp()
        self.uid = self.store.add_user("frank", FakeAccounts.SSO_PASSWORD_HASH, "admin", is_super=True,
                                       oidc=(ISSUER, "sub-frank"))
        self.assertEqual(self.login(sub="sub-frank").status_code, 303)
        self.assertIsNone(self.me().json()["elevated_until"])

    def _elevate(self, **authorize):
        location = self.start("/login/oidc?intent=elevate&next=/app/admin/users")
        q = {k: v[0] for k, v in parse_qs(urlsplit(location).query).items()}
        self.assertEqual((q["prompt"], q["max_age"]), ("login", "0"))
        resp = self.client.get("/login/oidc/callback", params=self.idp.authorize(location, **authorize))
        self.assertEqual((resp.status_code, resp.headers["location"]), (303, "/app/admin/users"))
        return resp

    def test_fresh_reauth_elevates_this_session(self):
        self._elevate(sub="sub-frank")
        self.assertIsNotNone(self.me().json()["elevated_until"])
        self.assertEqual([e["reason"] for e in self.events("elevate.success")], ["sso"])
        self.assertTrue(any(e.action == "session.elevate" and e.detail.get("via") == "sso" for e in self.store.audit))
        self.assertEqual(len(self.store.sessions), 1)  # 沒有另開 session

    def test_stale_auth_time_is_refused(self):
        self._elevate(sub="sub-frank", extra={"auth_time": int(time.time()) - 3600})
        self.assertIsNone(self.me().json()["elevated_until"])
        self.assertEqual([e["reason"] for e in self.events("elevate.failure")], ["sso_stale_auth"])

    def test_different_idp_identity_is_refused(self):
        self._elevate(sub="sub-somebody-else")
        self.assertIsNone(self.me().json()["elevated_until"])
        self.assertEqual([e["reason"] for e in self.events("elevate.failure")], ["sso_identity"])

    def test_password_session_is_not_sent_to_the_idp(self):
        self.client.cookies.clear()
        self.client.post("/login", data={"username": "root", "password": PW})
        resp = self.client.get("/login/oidc?intent=elevate&next=/app/admin")
        self.assertEqual((resp.status_code, resp.headers["location"]), (303, "/app/admin"))

    def test_requires_a_session(self):
        self.client.cookies.clear()
        resp = self.client.get("/login/oidc?intent=elevate&next=/app/admin")
        self.assertEqual(resp.headers["location"], "/login?next=%2Fapp%2Fadmin")


class IdentityUnitTests(unittest.TestCase):
    def test_mfa_rules(self):
        s = _settings(mfa_acr=("urn:mace:incommon:iap:silver",))
        self.assertTrue(oidc.is_mfa(s, {"amr": ["pwd", "mfa"]}))
        self.assertTrue(oidc.is_mfa(s, {"amr": ["http://schemas.microsoft.com/claims/multipleauthn"]}))
        self.assertTrue(oidc.is_mfa(s, {"acr": "urn:mace:incommon:iap:silver"}))
        self.assertFalse(oidc.is_mfa(s, {"amr": ["pwd"], "acr": "1"}))
        self.assertFalse(oidc.is_mfa(s, {"amr": "mfa"}))  # amr 必須是陣列
        self.assertTrue(oidc.is_mfa(_settings(assume_idp_mfa=True), {}))

    def test_identity_requires_claims(self):
        s = _settings()
        with self.assertRaises(oidc.OidcError) as cm:
            oidc.identity(s, {"sub": "x"})
        self.assertEqual(cm.exception.reason, "claims")
        with self.assertRaises(oidc.OidcError):
            oidc.identity(s, {"sub": "x" * 256, "preferred_username": "a@b"})
        ident = oidc.identity(_settings(username_claim="email"), {"sub": "x", "email": " a@b ", "auth_time": 5})
        self.assertEqual((ident.username, ident.auth_time), ("a@b", 5))

    def test_pkce_pair(self):
        verifier, challenge = oidc.new_pkce()
        self.assertTrue(43 <= len(verifier) <= 128)
        self.assertEqual(challenge, _b64url(hashlib.sha256(verifier.encode()).digest()))


class ConfigTests(unittest.TestCase):
    def _load(self, **env):
        base = {k: "" for k in ("OIDC_ISSUER", "OIDC_CLIENT_ID", "OIDC_REDIRECT_URI", "OIDC_SSO_ONLY",
                                 "OIDC_LINK_BY_USERNAME", "OIDC_MFA_AMR", "OIDC_BREAK_GLASS_USERS")}
        with mock.patch.dict("os.environ", {**base, **env}):
            return config._oidc_settings()

    def test_not_configured_is_disabled(self):
        s = self._load()
        self.assertFalse(s.enabled)
        self.assertFalse(s.sso_only)

    def test_half_configured_refuses_to_start(self):
        with self.assertRaises(ValueError):
            self._load(OIDC_ISSUER=ISSUER)
        with self.assertRaises(ValueError):
            self._load(OIDC_ISSUER="http://idp.example.test", OIDC_CLIENT_ID="c",
                       OIDC_REDIRECT_URI="https://r.example.test/login/oidc/callback")

    def test_full_config(self):
        s = self._load(OIDC_ISSUER=ISSUER, OIDC_CLIENT_ID="c", OIDC_REDIRECT_URI="https://r.example.test/cb",
                       OIDC_BREAK_GLASS_USERS="Root, ops-admin", OIDC_LINK_BY_USERNAME="off", OIDC_SSO_ONLY="yes")
        self.assertTrue(s.enabled and s.sso_only)
        self.assertFalse(s.link_by_username)  # off 不是開
        self.assertEqual(s.break_glass_users, ("root", "ops-admin"))
        self.assertTrue(s.is_break_glass("ROOT"))
        self.assertIn("mfa", s.mfa_amr)

    def test_secret_not_in_repr(self):
        self.assertNotIn(CLIENT_SECRET, repr(_settings()))
        self.assertNotIn(CLIENT_SECRET, repr(dataclasses.replace(config.get_settings(), oidc=_settings())))


if __name__ == "__main__":
    unittest.main()
