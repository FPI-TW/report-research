"""Admin v2 Wave 0 的接線（web/server.py）：7 個 router 已 include、router 層的授權已掛好、/healthz/security 只回本機。

Wave 1 的各 lane 只在自己的 router 檔加端點；這裡釘住 router 層的 dependency，讓新端點自動繼承正確的守門：
- 5 個管理 router：`require_admin`＋指定的 scope（新端點再由 tests/test_authz.py 的結構檢查逐條驗）。
- `features`、`me_quota`：任何登入的使用者（`current_user`），**不得**掛 require_admin（管理員 TOTP 強制不影響它們）。
"""

from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from web import authz
from web.routers import admin_analytics, admin_db, admin_flags, admin_quota, admin_security, features, me_quota
from web.server import _AUTH_ALLOWLIST, app

_ADMIN_ROUTERS = {
    admin_analytics: "analytics.read",
    admin_security: "audit.read",
    admin_quota: "accounts.manage",
    admin_flags: "ops.read",
    admin_db: "ops.read",
}


def _calls(router):
    return [d.dependency for d in router.dependencies]


class RouterWiringTests(unittest.TestCase):
    def test_admin_routers_require_admin_and_scope(self):
        for module, scope in _ADMIN_ROUTERS.items():
            with self.subTest(router=module.__name__):
                calls = _calls(module.router)
                self.assertIn(authz.require_admin, calls)
                scopes = set().union(*(getattr(c, "__scope__", frozenset()) for c in calls))
                self.assertEqual(scopes, {scope})
                self.assertIsNone(module.router.prefix or None)  # 不帶 prefix（tests/test_docs_contract.py）

    def test_user_routers_are_for_any_logged_in_user(self):
        for module in (features, me_quota):
            with self.subTest(router=module.__name__):
                calls = _calls(module.router)
                self.assertIn(authz.current_user, calls)
                self.assertNotIn(authz.require_admin, calls)

    def test_all_seven_are_included(self):
        source = (__import__("pathlib").Path(__file__).resolve().parents[1] / "web" / "server.py").read_text("utf-8")
        for name in ("admin_analytics", "admin_security", "admin_quota", "admin_flags", "admin_db", "me_quota",
                     "features"):
            self.assertIn(f"app.include_router({name}_routes.router)", source, name)


class HealthzSecurityTests(unittest.TestCase):
    def test_allowlisted_but_loopback_only(self):
        self.assertIn("/healthz/security", _AUTH_ALLOWLIST)
        local = TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 51000)).get("/healthz/security")
        self.assertEqual((local.status_code, local.json()), (200, {"security": "unknown"}))
        outside = TestClient(app, base_url="http://127.0.0.1", client=("203.0.113.9", 51000)).get("/healthz/security")
        self.assertEqual(outside.status_code, 404)
        public_host = TestClient(app, base_url="http://research.example.com",
                                 client=("127.0.0.1", 51000)).get("/healthz/security")
        self.assertEqual(public_host.status_code, 404)
        proxied = TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 51000)).get(
            "/healthz/security", headers={"X-Forwarded-For": "203.0.113.9"})
        self.assertEqual(proxied.status_code, 404)


if __name__ == "__main__":
    unittest.main()
