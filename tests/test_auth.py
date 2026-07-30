# tests/test_auth.py
import ast
import os
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

# web.auth 匯入時即讀取共用帳密(fail-closed),故須在匯入前設好測試用值。
os.environ.setdefault("REPORT_MARK_ACCESS_USERNAME", "tester")
os.environ.setdefault("REPORT_MARK_ACCESS_PASSWORD", "testpass")
os.environ.setdefault("REPORT_MARK_SESSION_SECRET", "fixed-test-secret-0123456789")

from fastapi.testclient import TestClient  # noqa: E402

from web import (
    auth,  # noqa: E402
    deps,  # noqa: E402
)
from web.server import app  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parent.parent


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


class TokenRevocationTests(unittest.TestCase):
    """token 格式 `<ver>.<iat>.<exp>.<sig>` 帶來的三種撤銷手段 + 絕對存活上限。

    在此之前:簽章訊息只有到期秒數,換密碼不會登出任何人、沒有任何撤銷開關,
    而 middleware 每個請求都重簽 7 天 ⇒ 一個活躍中的 session 永遠不會過期。
    """

    def test_v1_token_rejected_and_logged(self):
        # 舊格式必須被「認得出來地」拒絕,而不是碰巧簽章對不上
        now = 1_000_000
        exp = now + auth.SESSION_TTL
        v1 = f"{exp}.{auth._sign(str(exp))}"
        with self.assertLogs("web.auth", level="INFO") as cm:
            self.assertFalse(auth.verify_token(v1, now))
        self.assertTrue(any("舊版" in line for line in cm.output), cm.output)

    def test_unknown_version_rejected_and_logged(self):
        now = 1_000_000
        iat, exp = now, now + 100
        body = f"3.{iat}.{exp}"
        forged = f"{body}.{auth._sign(body)}"  # 簽章自洽,但版本不是現行版
        with self.assertLogs("web.auth", level="INFO") as cm:
            self.assertFalse(auth.verify_token(forged, now))
        self.assertTrue(any("version=3" in line for line in cm.output), cm.output)

    def test_password_change_invalidates_existing_tokens(self):
        now = 1_000_000
        tok = auth.issue_token(now)
        self.assertTrue(auth.verify_token(tok, now + 10))
        orig = auth._PASSWORD_B
        auth._PASSWORD_B = b"rotated-password"
        try:
            self.assertFalse(auth.verify_token(tok, now + 10))
        finally:
            auth._PASSWORD_B = orig
        self.assertTrue(auth.verify_token(tok, now + 10), "還原密碼後原 token 應再度有效")

    def test_session_epoch_change_invalidates_everything(self):
        now = 1_000_000
        tok = auth.issue_token(now)
        orig = auth._SESSION_EPOCH
        auth._SESSION_EPOCH = "2026-07-30"
        try:
            self.assertFalse(auth.verify_token(tok, now + 10))
            # bump 之後新簽發的 token 仍正常運作(這是登出開關,不是壞掉開關)
            self.assertTrue(auth.verify_token(auth.issue_token(now), now + 10))
        finally:
            auth._SESSION_EPOCH = orig

    def test_token_body_does_not_expose_credential_fingerprint(self):
        # 指紋只進簽章訊息;cookie 外流不得附贈任何密碼衍生值
        tok = auth.issue_token(1_000_000)
        self.assertNotIn(auth._identity_fingerprint(), tok)

    def test_absolute_cap_rejects_old_iat_even_with_future_exp(self):
        now = 2_000_000
        iat = now - auth.MAX_ABSOLUTE_TTL - 10
        exp = now + 3600  # 到期時間還很遠,只有 iat 過老
        forged = f"{auth.TOKEN_VERSION}.{iat}.{exp}.{auth._sign(auth._token_message(iat, exp))}"
        self.assertFalse(auth.verify_token(forged, now))

    def test_daily_sliding_renewal_dies_at_absolute_cap(self):
        # 每天續期一次:第 29 天仍活著,第 30 天(＝MAX_ABSOLUTE_TTL)起一定死
        t0 = 3_000_000
        day = 24 * 3600
        tok = auth.issue_token(t0)
        last_alive = None
        for d in range(1, 41):
            now = t0 + d * day
            session = auth.parse_token(tok, now)
            if session is None:
                break
            last_alive = d
            tok = auth.issue_token(now, issued_at=session.issued_at)
        self.assertEqual(last_alive, 29)

    def test_renewal_preserves_issued_at(self):
        now = 4_000_000
        iat = now - 3 * 24 * 3600
        tok = auth.issue_token(now, issued_at=iat)
        session = auth.parse_token(tok, now + 5)
        self.assertIsNotNone(session)
        self.assertEqual(session.issued_at, iat)

    def test_cookie_max_age_shrinks_near_absolute_cap(self):
        # 瀏覽器不該抱著一個伺服器早已拒收的 cookie
        now = 5_000_000
        iat = now - (auth.MAX_ABSOLUTE_TTL - 600)
        resp = SimpleNamespace(cookies={})

        def _set_cookie(name, value, **kw):
            resp.cookies[name] = kw

        resp.set_cookie = _set_cookie
        auth.set_session_cookie(resp, now, secure=False, issued_at=iat)
        self.assertEqual(resp.cookies[auth.COOKIE_NAME]["max_age"], 600)


def _function_source(name: str) -> ast.FunctionDef:
    tree = ast.parse((_REPO_ROOT / "web" / "auth.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"web/auth.py 找不到函式 {name}")


class ConstantTimeComparisonTests(unittest.TestCase):
    """簽章與共享祕密一律 hmac.compare_digest——`==` 的提前返回會洩漏前綴長度。"""

    def _assert_constant_time(self, fn_name: str, secret_names: set[str]):
        node = _function_source(fn_name)
        calls = {
            ast.unparse(n.func)
            for n in ast.walk(node)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        }
        self.assertIn("hmac.compare_digest", calls, f"{fn_name} 未使用常數時間比對")
        for cmp_node in ast.walk(node):
            if not isinstance(cmp_node, ast.Compare):
                continue
            if not any(isinstance(op, (ast.Eq, ast.NotEq)) for op in cmp_node.ops):
                continue
            operands = [cmp_node.left, *cmp_node.comparators]
            names = {n.id for o in operands for n in ast.walk(o) if isinstance(n, ast.Name)}
            self.assertFalse(
                names & secret_names,
                f"{fn_name} 以 == 比對機密值 {names & secret_names}",
            )

    def test_parse_token_compares_signature_in_constant_time(self):
        self._assert_constant_time("parse_token", {"sig"})

    def test_edge_secret_compared_in_constant_time(self):
        self._assert_constant_time("_edge_secret_ok", {"presented", "_EDGE_SECRET"})


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
        self.assertIn('class="brand"', r.text)

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
        # 授權後根路徑導向 SPA 檢索頁（舊 vanilla 首頁已退場）
        r2 = client.get("/")
        self.assertEqual(r2.status_code, 302)
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
        # 每次通過認證的回應都應重新簽發 session cookie(滑動到期)；根路徑現為 302 導向 SPA
        client = _client()
        client.post("/login", data={"username": "tester", "password": "testpass"})
        r = client.get("/")
        self.assertEqual(r.status_code, 302)
        self.assertIn(auth.COOKIE_NAME, r.cookies)

    def test_unauthed_static_is_gated(self):
        # /static 不在白名單:未登入直接取 /static/index.html 應被擋(防繞過 route 門檻)
        r = _client().get("/static/index.html")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.headers["location"], "/login")


class LoginAuditLogTests(unittest.TestCase):
    """登入四種結果都要留下一行——在此之前整條登入路徑對 journald 完全無聲。

    每一題都順帶檢查「日誌裡沒有密碼」：稽核與洩密只差一個 %s。
    """

    AUDIT_LOGGER = "web.routers.auth_pages"
    BAD_PASSWORD = "wrong-password-value"

    def setUp(self):
        auth._FAILS.clear()

    def tearDown(self):
        auth._FAILS.clear()

    def _assert_no_secrets(self, output):
        blob = "\n".join(output)
        self.assertNotIn("testpass", blob, "稽核日誌洩漏了真實密碼")
        self.assertNotIn(self.BAD_PASSWORD, blob, "稽核日誌把使用者送來的密碼原樣寫出")

    def test_success_logged_at_info(self):
        client = _client()
        with self.assertLogs(self.AUDIT_LOGGER, level="INFO") as cm:
            r = client.post("/login", data={"username": "tester", "password": "testpass"})
        self.assertEqual(r.status_code, 303)
        hits = [rec for rec in cm.records if "登入成功" in rec.getMessage()]
        self.assertEqual(len(hits), 1, cm.output)
        # 成功是常規事件 → INFO；用 WARNING 會讓「警告」等級失去意義
        self.assertEqual(hits[0].levelname, "INFO")
        self._assert_no_secrets(cm.output)

    def test_failure_logged_at_warning_without_password(self):
        client = _client()
        with self.assertLogs(self.AUDIT_LOGGER, level="INFO") as cm:
            r = client.post(
                "/login", data={"username": "tester", "password": self.BAD_PASSWORD}
            )
        self.assertEqual(r.status_code, 303)
        hits = [rec for rec in cm.records if "登入失敗" in rec.getMessage()]
        self.assertEqual(len(hits), 1, cm.output)
        # 失敗是異常事件 → WARNING，才能在 LOG_LEVEL=WARNING 的環境活下來
        self.assertEqual(hits[0].levelname, "WARNING")
        self.assertIn("帳號相符=True", hits[0].getMessage())
        self._assert_no_secrets(cm.output)

    def test_failure_log_does_not_echo_submitted_username(self):
        # 帳號欄很常被誤填成密碼；只記布林結果，不記原樣字串
        client = _client()
        with self.assertLogs(self.AUDIT_LOGGER, level="INFO") as cm:
            client.post("/login", data={"username": self.BAD_PASSWORD, "password": "x"})
        self._assert_no_secrets(cm.output)
        hits = [rec for rec in cm.records if "登入失敗" in rec.getMessage()]
        self.assertIn("帳號相符=False", hits[0].getMessage())

    def test_lockout_logged_at_warning(self):
        client = _client()
        for _ in range(auth.MAX_FAILS):
            client.post("/login", data={"username": "tester", "password": self.BAD_PASSWORD})
        with self.assertLogs(self.AUDIT_LOGGER, level="INFO") as cm:
            r = client.post("/login", data={"username": "tester", "password": self.BAD_PASSWORD})
        self.assertIn("error=locked", r.headers["location"])
        hits = [rec for rec in cm.records if "登入遭限流鎖定" in rec.getMessage()]
        self.assertEqual(len(hits), 1, cm.output)
        self.assertEqual(hits[0].levelname, "WARNING")
        self._assert_no_secrets(cm.output)

    def test_insecure_rejection_logs_peer_address(self):
        """2026-07-17 外網全體登入被擋時唯一缺的線索：uvicorn 實際看到的對端是誰。"""
        client = _client_for("http://research.office")
        with self.assertLogs(self.AUDIT_LOGGER, level="INFO") as cm:
            r = client.post("/login", data={"username": "tester", "password": "testpass"})
        self.assertIn("error=insecure", r.headers["location"])
        hits = [rec for rec in cm.records if "登入遭拒" in rec.getMessage()]
        self.assertEqual(len(hits), 1, cm.output)
        self.assertEqual(hits[0].levelname, "WARNING")
        self.assertIn("peer=", hits[0].getMessage())
        self._assert_no_secrets(cm.output)


class EdgeSecretTrustTests(unittest.TestCase):
    """可信代理判定 = 共享祕密 header **OR** 來源 CIDR（滾動切換期間兩條都要能通）。"""

    def setUp(self):
        self._orig = auth._EDGE_SECRET

    def tearDown(self):
        auth._EDGE_SECRET = self._orig

    def _req(self, peer: str, headers: dict):
        return SimpleNamespace(
            url=SimpleNamespace(scheme="http"),
            headers=headers,
            client=SimpleNamespace(host=peer),
        )

    def test_header_makes_off_cidr_peer_trusted(self):
        auth._EDGE_SECRET = "edge-shared-secret"
        req = self._req(
            "203.0.113.9",
            {"x-forwarded-proto": "https", "x-edge-secret": "edge-shared-secret"},
        )
        self.assertTrue(auth.request_is_secure(req))

    def test_header_also_authorises_x_real_ip_restoration(self):
        auth._EDGE_SECRET = "edge-shared-secret"
        req = self._req(
            "203.0.113.9",
            {"x-real-ip": "8.8.8.8", "x-edge-secret": "edge-shared-secret"},
        )
        self.assertEqual(auth.client_ip(req), "8.8.8.8")

    def test_wrong_header_is_not_trusted(self):
        auth._EDGE_SECRET = "edge-shared-secret"
        req = self._req(
            "203.0.113.9", {"x-forwarded-proto": "https", "x-edge-secret": "guess"}
        )
        self.assertFalse(auth.request_is_secure(req))
        self.assertEqual(auth.client_ip(self._req("203.0.113.9", {"x-real-ip": "8.8.8.8"})), "203.0.113.9")

    def test_cidr_path_survives_when_secret_configured(self):
        # OR：設了祕密不代表 CIDR 失效，否則先改 app 的那一刻就把人全擋在外面
        auth._EDGE_SECRET = "edge-shared-secret"
        req = self._req("127.0.0.1", {"x-forwarded-proto": "https"})
        self.assertTrue(auth.request_is_secure(req))

    def test_unset_secret_never_trusts_the_header(self):
        # 空祕密 vs 空 header 在 compare_digest 下是相等的——必須先被短路擋掉
        auth._EDGE_SECRET = ""
        req = self._req("203.0.113.9", {"x-forwarded-proto": "https", "x-edge-secret": ""})
        self.assertFalse(auth.request_is_secure(req))


class EdgeProxyConfigTests(unittest.TestCase):
    """app 端的 X-Edge-Secret 判定要成立，邊緣必須真的注入它。

    這組是靜態守門：把 nginx.conf 掛回 conf.d（而非 templates）不會有任何錯誤，
    只會讓 header 的值變成字面量 `${EDGE_SECRET}`，App 靜默退回 CIDR 判定——
    也就是這次修的問題悄悄回來。
    """

    def test_nginx_conf_injects_edge_secret_header(self):
        text = (_REPO_ROOT / "deploy" / "nginx.conf").read_text(encoding="utf-8")
        live = [ln for ln in text.splitlines() if not ln.strip().startswith("#")]
        self.assertTrue(
            any('proxy_set_header X-Edge-Secret "${EDGE_SECRET}";' in ln for ln in live),
            "deploy/nginx.conf 未注入 X-Edge-Secret",
        )

    def test_compose_mounts_conf_as_envsubst_template(self):
        text = (_REPO_ROOT / "deploy" / "docker-compose.yml").read_text(encoding="utf-8")
        live = "\n".join(ln for ln in text.splitlines() if not ln.strip().startswith("#"))
        self.assertIn("/etc/nginx/templates/default.conf.template", live)
        self.assertIn("EDGE_SECRET=${EDGE_SECRET", live)
        # 沒有這個 filter，envsubst 會連 $host / $remote_addr 一起代換掉
        self.assertIn("NGINX_ENVSUBST_FILTER=^EDGE_", live)

    def test_both_env_examples_document_the_shared_secret(self):
        root = (_REPO_ROOT / ".env.example").read_text(encoding="utf-8")
        edge = (_REPO_ROOT / "deploy" / ".env.example").read_text(encoding="utf-8")
        self.assertIn("REPORT_MARK_EDGE_SECRET", root)
        self.assertIn("REPORT_MARK_SESSION_EPOCH", root)
        self.assertIn("EDGE_SECRET=", edge)


class SlidingRenewalWiringTests(unittest.TestCase):
    """middleware 續期時必須沿用原 iat，否則絕對存活上限每個請求都被重置。"""

    def test_middleware_renews_with_original_issued_at(self):
        now = int(time.time())
        iat = now - 3 * 24 * 3600
        token = auth.issue_token(now, issued_at=iat)
        seen = {}
        orig = auth.set_session_cookie

        def _spy(response, at, *, secure, issued_at=None):
            seen["issued_at"] = issued_at
            return orig(response, at, secure=secure, issued_at=issued_at)

        auth.set_session_cookie = _spy
        try:
            client = TestClient(
                app,
                cookies={auth.COOKIE_NAME: token},
                follow_redirects=False,
                base_url="http://127.0.0.1",
            )
            r = client.get("/")
        finally:
            auth.set_session_cookie = orig
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.headers["location"], "/app/search")
        self.assertEqual(seen.get("issued_at"), iat)


class HistoryDeleteApiTests(unittest.TestCase):
    def _authed_client(self):
        client = _client()
        r = client.post("/login", data={"username": "tester", "password": "testpass"})
        self.assertEqual(r.status_code, 303)
        return client

    def test_delete_history_endpoint(self):

        seen = {}

        async def fake_delete_qa(qa_id: str):
            seen["qa_id"] = qa_id
            return True

        orig = deps.delete_qa
        deps.delete_qa = fake_delete_qa
        try:
            client = self._authed_client()
            qa_id = "11111111-1111-1111-1111-111111111111"
            r = client.delete(f"/api/history/{qa_id}")
        finally:
            deps.delete_qa = orig

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

        async def fake_hybrid_search(*_a, **_k):
            return []

        orig_embed = deps.embed_query_cached
        orig_search = deps.hybrid_search
        deps.embed_query_cached = lambda _q: [0.0]
        deps.hybrid_search = fake_hybrid_search
        try:
            client = self._authed_client()
            r = client.get("/api/search", params={"q": "x" * 5001})
        finally:
            deps.embed_query_cached = orig_embed
            deps.hybrid_search = orig_search

        self.assertEqual(r.status_code, 422)

    def test_ask_rejects_overlong_question(self):

        async def fake_answer_question(*_a, **_k):
            yield ("done", {"cited": []})

        orig_answer_question = deps.answer_question
        deps.answer_question = fake_answer_question
        try:
            client = self._authed_client()
            r = client.post("/api/ask", json={"question": "x" * 5001})
        finally:
            deps.answer_question = orig_answer_question

        self.assertEqual(r.status_code, 422)

    def test_post_delete_history_alias(self):

        seen = {}

        async def fake_delete_qa(qa_id: str):
            seen["qa_id"] = qa_id
            return True

        orig = deps.delete_qa
        deps.delete_qa = fake_delete_qa
        try:
            client = self._authed_client()
            qa_id = "22222222-2222-2222-2222-222222222222"
            r = client.post(f"/api/history/{qa_id}/delete")
        finally:
            deps.delete_qa = orig

        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"ok": True})
        self.assertEqual(seen["qa_id"], qa_id)


if __name__ == "__main__":
    unittest.main()
