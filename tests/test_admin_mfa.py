"""管理員 TOTP 強制（`ADMIN_MFA_REQUIRED`，web/authz.py 的 require_admin；Admin v2、使用者定案 10）。

- 政策值：預設開、拼錯也開；只有明確的 0／false／no／off 才關（`app.config._admin_mfa_required`）。
- 開啟時：管理員沒開 TOTP → /api/admin/*、/api/review/* 一律 403 `mfa_enrollment_required`；
  完成設定所需的 /api/me、/api/me/totp*、/logout 照常可用，設定完成的下一個請求就放行。
- 關閉時：行為與 v1 相同（tests/conftest.py 以賦值設 0，讓既有測試照常）。
- 白名單是結構性的：那幾條路由的 dependency 樹裡沒有 require_admin。
"""

from __future__ import annotations

import ast
import dataclasses
import os
import unittest
from pathlib import Path
from unittest import mock

from fake_accounts import FakeAccounts, install, session_cookies
from fastapi.testclient import TestClient

from app import config
from app.services import totp
from web import auth, authz
from web.server import app

REPO_ROOT = Path(__file__).resolve().parents[1]

try:
    from fastapi.routing import iter_route_contexts  # FastAPI ≥ 0.137.2
except ImportError:
    iter_route_contexts = None


def _routes():
    from fastapi.routing import APIRoute

    if iter_route_contexts is None:
        yield from (r for r in app.routes if isinstance(r, APIRoute))
        return
    for ctx in iter_route_contexts(app.routes):
        if getattr(ctx, "dependant", None) is not None and getattr(ctx, "path", None):
            yield ctx


def _calls(dependant):
    for dep in dependant.dependencies:
        yield dep.call
        yield from _calls(dep)


def _policy(on: bool):
    """在範圍內把 Settings 的 admin_mfa_required 換成指定值（不碰 os.environ）。"""
    return mock.patch.object(config, "_SETTINGS", dataclasses.replace(config.get_settings(), admin_mfa_required=on))


class PolicyParsingTests(unittest.TestCase):
    def _parse(self, value):
        env = dict(os.environ)
        env.pop("ADMIN_MFA_REQUIRED", None)
        if value is not None:
            env["ADMIN_MFA_REQUIRED"] = value
        with mock.patch.dict(os.environ, env, clear=True):
            return config._admin_mfa_required()

    def test_unset_and_empty_mean_on(self):
        self.assertTrue(self._parse(None))
        self.assertTrue(self._parse(""))
        self.assertTrue(self._parse("   "))

    def test_explicit_off_values(self):
        for v in ("0", "false", "FALSE", " no ", "off", "Off"):
            with self.subTest(v=v):
                self.assertFalse(self._parse(v))

    def test_typos_fail_safe_to_on(self):
        for v in ("1", "true", "O", "flase", "disabled", "nope", "0 # off"):
            with self.subTest(v=v):
                self.assertTrue(self._parse(v))

    def test_settings_default_is_on(self):
        self.assertTrue(config.Settings.__dataclass_fields__["admin_mfa_required"].default)

    def test_conftest_assigns_zero(self):
        """既有測試靠 conftest 關掉這道閘；靜態釘住是**賦值**（部署目錄 .env 的值擋得住）。"""
        self.assertEqual(os.environ.get("ADMIN_MFA_REQUIRED"), "0")
        tree = ast.parse((REPO_ROOT / "tests" / "conftest.py").read_text(encoding="utf-8"))
        assigned = {
            t.slice.value for node in ast.walk(tree) if isinstance(node, ast.Assign) for t in node.targets
            if isinstance(t, ast.Subscript) and isinstance(t.slice, ast.Constant)
        }
        self.assertIn("ADMIN_MFA_REQUIRED", assigned)


class WhitelistStructureTests(unittest.TestCase):
    """完成 TOTP 設定所需的端點不得掛 require_admin（否則強制開啟時沒有人能設定）。"""

    _MUST_STAY_OPEN = {"/api/me", "/api/me/totp", "/api/me/totp/setup", "/api/me/totp/confirm",
                       "/api/me/totp/disable", "/api/me/elevate", "/logout", "/login"}

    def test_enrollment_routes_do_not_require_admin(self):
        seen = set()
        for r in _routes():
            if r.path in self._MUST_STAY_OPEN:
                seen.add(r.path)
                self.assertNotIn(authz.require_admin, set(_calls(r.dependant)), r.path)
        self.assertEqual(seen, self._MUST_STAY_OPEN, "白名單裡的路由改名或消失了？")


class EnforcementTests(unittest.TestCase):
    def setUp(self):
        auth._FAILS.clear()
        self.store = FakeAccounts()
        self.store.add_user("noauth", "noauth-password", "admin")
        self.mfa_id = self.store.add_user("withmfa", "withmfa-password", "admin")
        row = self.store.users[self.mfa_id]
        row.totp_secret, row.totp_enabled = totp.generate_secret(), True
        self.store.add_user("member", "member-password", "user")
        self._ctx = install(self.store)
        self._ctx.__enter__()

    def tearDown(self):
        self._ctx.__exit__(None, None, None)
        auth._FAILS.clear()

    def _client(self, username):
        c = TestClient(app, follow_redirects=False, base_url="http://127.0.0.1")
        c.cookies.update(session_cookies(username))
        return c

    def test_admin_without_totp_is_blocked_when_on(self):
        c = self._client("noauth")
        with _policy(True):
            for path in ("/api/admin/users", "/api/admin/audit"):
                r = c.get(path)
                self.assertEqual((r.status_code, r.json().get("code")), (403, "mfa_enrollment_required"), path)
            r = c.get("/api/review/queue", params={"kind": "feedback"})
            self.assertEqual((r.status_code, r.json().get("code")), (403, "mfa_enrollment_required"))
            me = c.get("/api/me").json()
        self.assertTrue(me["mfa_enrollment_required"])
        self.assertFalse(me["totp_enabled"])

    def test_enrollment_endpoints_work_and_unlock_on_next_request(self):
        c = self._client("noauth")
        with _policy(True):
            self.assertEqual(c.get("/api/me/totp").status_code, 200)
            setup = c.post("/api/me/totp/setup")
            self.assertEqual(setup.status_code, 200, setup.text)
            secret = setup.json()["secret"]
            code = totp.code_at(secret, totp.current_step())
            confirm = c.post("/api/me/totp/confirm", json={"code": code})
            self.assertEqual(confirm.status_code, 200, confirm.text)
            # 同一個 session、下一個請求就放行（每個請求都從帳號庫讀 totp_enabled）
            self.assertEqual(c.get("/api/admin/users").status_code, 200)
            self.assertFalse(c.get("/api/me").json()["mfa_enrollment_required"])

    def test_admin_with_totp_passes(self):
        c = self._client("withmfa")
        with _policy(True):
            self.assertEqual(c.get("/api/admin/users").status_code, 200)
            self.assertFalse(c.get("/api/me").json()["mfa_enrollment_required"])

    def test_admin_reset_totp_is_blocked_again(self):
        c = self._client("withmfa")
        with _policy(True):
            self.assertEqual(c.get("/api/admin/users").status_code, 200)
            self.store.users[self.mfa_id].totp_enabled = False  # 等同 create_admin.py --reset-totp
            r = c.get("/api/admin/users")
        self.assertEqual((r.status_code, r.json().get("code")), (403, "mfa_enrollment_required"))

    def test_policy_off_keeps_v1_behaviour(self):
        c = self._client("noauth")
        with _policy(False):
            self.assertEqual(c.get("/api/admin/users").status_code, 200)
            self.assertFalse(c.get("/api/me").json()["mfa_enrollment_required"])

    def test_plain_user_gets_the_ordinary_403(self):
        c = self._client("member")
        with _policy(True):
            r = c.get("/api/admin/users")
            me = c.get("/api/me").json()
        self.assertEqual(r.status_code, 403)
        self.assertNotEqual(r.json().get("code"), "mfa_enrollment_required")
        self.assertFalse(me["mfa_enrollment_required"])

    def test_logout_still_works(self):
        c = self._client("noauth")
        with _policy(True):
            self.assertEqual(c.post("/logout").status_code, 303)

    def test_dev_user_is_exempt(self):
        from app.services.accounts import DEV_USER

        with _policy(True):
            self.assertFalse(authz.mfa_enrollment_required(DEV_USER))


if __name__ == "__main__":
    unittest.main()
