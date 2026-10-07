"""授權（web/authz.py）：一般使用者打不到管理端點；真正擋人的是後端，不是前端 route guard。

兩層：
- HTTP 層：user 角色打 /api/review/*（以及 /api/admin/*，見 tests/test_admin_api.py）回 403；
  缺 scope、不是 super admin、沒有重新驗證，各自回帶 code 的 403。
- 結構層：app 上**每一條** /api/admin/*、/api/review/* 路由的 dependency 樹裡都要有
  `authz.require_admin`，而且至少一個帶 `__scope__` 的 dependency（`require_scope`／`require_super`）。
  新增管理端點時忘了掛，這裡會紅——不必等有人想到要寫 403 測試。
"""

from __future__ import annotations

import unittest

from fake_accounts import FakeAccounts, install
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.services import accounts
from app.services.accounts import DEV_USER
from web import auth, authz, dev_mode
from web.routers import review
from web.server import app

try:
    from fastapi.routing import iter_route_contexts  # FastAPI ≥ 0.137.2
except ImportError:
    iter_route_contexts = None

_ADMIN_PREFIXES = ("/api/admin/", "/api/review/")


def _iter_api_routes(routes):
    """全 app 攤平後的端點（含 include_router 時合併進來的 router 層 dependencies）。

    FastAPI 0.137 起 app.routes 是樹，直接迭代只看得到 /docs 那幾條（理由與回退分支
    同 tests/test_monitor_http.py 的 _app_routes）。
    """
    if iter_route_contexts is None:
        yield from (r for r in routes if isinstance(r, APIRoute))
        return
    for ctx in iter_route_contexts(routes):
        if getattr(ctx, "dependant", None) is not None and getattr(ctx, "path", None):
            yield ctx


def _dependency_calls(dependant):
    for dep in dependant.dependencies:
        yield dep.call
        yield from _dependency_calls(dep)


def _client():
    return TestClient(app, follow_redirects=False, base_url="http://127.0.0.1")


class AdminRouteStructureTests(unittest.TestCase):
    def _guarded(self):
        guarded = [r for r in _iter_api_routes(app.routes) if r.path.startswith(_ADMIN_PREFIXES)]
        self.assertTrue(guarded, "找不到任何管理端點——路由前綴改了？")
        return guarded

    def test_every_admin_route_requires_admin(self):
        missing = [
            f"{sorted(r.methods)} {r.path}" for r in self._guarded()
            if authz.require_admin not in set(_dependency_calls(r.dependant))
        ]
        self.assertEqual(missing, [], f"這些管理端點沒有掛 authz.require_admin：{missing}")

    def test_every_admin_route_declares_a_scope(self):
        missing = [
            f"{sorted(r.methods)} {r.path}" for r in self._guarded()
            if not any(getattr(c, "__scope__", None) for c in _dependency_calls(r.dependant))
        ]
        self.assertEqual(missing, [], f"這些管理端點沒有宣告 scope（authz.require_scope／require_super）：{missing}")

    def test_privileges_route_requires_super_and_elevation(self):
        route = next(r for r in self._guarded() if r.path == "/api/admin/users/{user_id}/privileges")
        calls = set(_dependency_calls(route.dependant))
        self.assertIn(authz.require_super, calls)
        self.assertIn(authz.require_elevated, calls)

    def test_qa_content_access_requires_its_own_scope(self):
        """讀問答原文另要 qa_content.read（不是管理員預設就有），router 層的 review.manage 照樣要。"""
        route = next(r for r in self._guarded() if r.path == "/api/review/qa/{qa_id}/access")
        self.assertEqual(route.methods, {"POST"})
        scopes = set().union(*(getattr(c, "__scope__", frozenset()) for c in _dependency_calls(route.dependant)))
        self.assertLessEqual({"qa_content.read", "review.manage"}, scopes)
        self.assertNotIn("qa_content.read", accounts.ADMIN_DEFAULT_SCOPES)

    def test_security_routes_scopes_and_session_revoke_elevation(self):
        """安全頁：整組 audit.read；session 清單另要 accounts.manage，撤銷單一 session 再要已提升。"""
        routes = [r for r in self._guarded() if r.path.startswith("/api/admin/security/")]
        self.assertGreaterEqual(len(routes), 8)

        def scopes(route):
            return set().union(*(getattr(c, "__scope__", frozenset()) for c in _dependency_calls(route.dependant)))

        for route in routes:
            with self.subTest(path=route.path):
                self.assertIn("audit.read", scopes(route))
        by_path = {r.path: r for r in routes}
        self.assertIn("accounts.manage", scopes(by_path["/api/admin/security/sessions"]))
        revoke = by_path["/api/admin/security/sessions/{session_id}/revoke"]
        self.assertEqual(revoke.methods, {"POST"})
        self.assertIn("accounts.manage", scopes(revoke))
        self.assertIn(authz.require_elevated, set(_dependency_calls(revoke.dependant)))

    def test_unknown_scope_fails_at_import_time(self):
        with self.assertRaises(ValueError):
            authz.require_scope("no.such.scope")
        with self.assertRaises(ValueError):
            authz.require_scope()


class RoleEnforcementTests(unittest.TestCase):
    def setUp(self):
        auth._FAILS.clear()
        self.store = FakeAccounts()
        self.store.add_user("alice", "alice-password", "user")
        self.store.add_user("root", "root-password", "admin")
        self._ctx = install(self.store)
        self._ctx.__enter__()
        self._orig_fetch = review._fetch

        async def fake_fetch(session, kind, **_k):
            return 0, []

        class _Session:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

        review._fetch = fake_fetch
        from web import deps

        self._orig_sf = deps.SessionFactory
        deps.SessionFactory = lambda: _Session()

    def tearDown(self):
        from web import deps

        review._fetch = self._orig_fetch
        deps.SessionFactory = self._orig_sf
        self._ctx.__exit__(None, None, None)
        auth._FAILS.clear()

    def _login(self, username, password):
        client = _client()
        r = client.post("/login", data={"username": username, "password": password})
        self.assertEqual(r.status_code, 303)
        return client

    def test_user_gets_403_on_review_queue(self):
        r = self._login("alice", "alice-password").get("/api/review/queue", params={"kind": "feedback"})
        self.assertEqual(r.status_code, 403)

    def test_user_gets_403_on_review_update(self):
        r = self._login("alice", "alice-password").put(
            "/api/review/feedback/11111111-1111-1111-1111-111111111111",
            json={"status": "resolved"},
        )
        self.assertEqual(r.status_code, 403)

    def test_admin_passes_the_gate(self):
        r = self._login("root", "root-password").get("/api/review/queue", params={"kind": "feedback"})
        self.assertEqual(r.status_code, 200, r.text)

    def test_demoted_admin_loses_access_on_next_request(self):
        # 角色每個請求都從 DB 讀：降級不必等對方重新登入
        client = self._login("root", "root-password")
        self.assertEqual(client.get("/api/review/queue", params={"kind": "feedback"}).status_code, 200)
        next(u for u in self.store.users.values() if u.username == "root").role = "user"
        self.assertEqual(client.get("/api/review/queue", params={"kind": "feedback"}).status_code, 403)

    def test_unauthenticated_gets_401_not_403(self):
        self.assertEqual(_client().get("/api/review/queue", params={"kind": "feedback"}).status_code, 401)


class DevModeIdentityTests(unittest.TestCase):
    def test_dev_bypass_runs_as_dev_admin_without_db(self):
        store = FakeAccounts()
        store.fail_with = RuntimeError("免登入模式不該碰帳號庫")
        orig = dev_mode.bypass_allowed
        dev_mode.bypass_allowed = lambda request: True
        try:
            with install(store):
                body = _client().get("/api/me").json()
        finally:
            dev_mode.bypass_allowed = orig
        self.assertEqual(body["id"], None)
        self.assertEqual(body["username"], DEV_USER.username)
        self.assertEqual(body["role"], "admin")
        self.assertTrue(body["is_super"])
        self.assertEqual(set(body["scopes"]), set(accounts.ALL_SCOPES))


class ScopeEnforcementTests(unittest.TestCase):
    """缺 scope／不是 super／沒有重新驗證，各回帶 code 的 403。"""

    def setUp(self):
        auth._FAILS.clear()
        self.store = FakeAccounts()
        self.store.add_user("plain", "plain-password", "admin")
        self.store.add_user("boss", "boss-password", "admin", is_super=True)
        self.target = self.store.add_user("other", "other-password", "admin")
        self._ctx = install(self.store)
        self._ctx.__enter__()

    def tearDown(self):
        self._ctx.__exit__(None, None, None)
        auth._FAILS.clear()

    def _login(self, username, password):
        client = _client()
        self.assertEqual(client.post("/login", data={"username": username, "password": password}).status_code, 303)
        return client

    def _put_privileges(self, client):
        return client.put(f"/api/admin/users/{self.target}/privileges", json={"scopes": ["qa_content.read"]})

    def test_non_super_gets_super_required(self):
        r = self._put_privileges(self._login("plain", "plain-password"))
        self.assertEqual((r.status_code, r.json()["code"]), (403, "super_required"))

    def test_super_without_elevation_gets_elevation_required(self):
        r = self._put_privileges(self._login("boss", "boss-password"))
        self.assertEqual((r.status_code, r.json()["code"]), (403, "elevation_required"))

    def test_super_after_elevation_succeeds_and_is_audited(self):
        client = self._login("boss", "boss-password")
        bad = client.post("/api/admin/elevate", json={"password": "wrong-password"})
        self.assertEqual((bad.status_code, bad.json()["code"]), (403, "bad_password"))
        ok = client.post("/api/admin/elevate", json={"password": "boss-password"})
        self.assertEqual(ok.status_code, 200, ok.text)
        self.assertTrue(ok.json()["elevated_until"])
        r = self._put_privileges(client)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["scopes"], ["qa_content.read"])
        actions = [e.action for e in self.store.audit]
        self.assertIn("session.elevate", actions)
        self.assertIn("session.elevate_failed", actions)
        self.assertIn("user.set_privileges", actions)

    def test_elevation_is_bound_to_the_session(self):
        first = self._login("boss", "boss-password")
        self.assertEqual(first.post("/api/admin/elevate", json={"password": "boss-password"}).status_code, 200)
        second = self._login("boss", "boss-password")  # 另一個 session（裝置）
        self.assertEqual(self._put_privileges(second).json()["code"], "elevation_required")

    def test_missing_scope_returns_missing_scope_code(self):
        from fastapi import Depends, FastAPI

        mini = FastAPI()
        from web import errors

        errors.install(mini)

        @mini.middleware("http")
        async def _as_user(request, call_next):
            request.state.user = accounts.User(id="u", username="u", role="admin",
                                               scopes=accounts.ADMIN_DEFAULT_SCOPES)
            return await call_next(request)

        @mini.get("/x", dependencies=[Depends(authz.require_scope("qa_content.read"))])
        async def _x():
            return {}

        r = TestClient(mini).get("/x")
        self.assertEqual((r.status_code, r.json()["code"]), (403, "missing_scope"))

    def test_elevate_is_rate_limited_with_login_failures(self):
        client = self._login("boss", "boss-password")
        for _ in range(auth.MAX_FAILS):
            client.post("/api/admin/elevate", json={"password": "wrong-password"})
        r = client.post("/api/admin/elevate", json={"password": "boss-password"})
        self.assertEqual((r.status_code, r.json()["code"]), (429, "rate_limited"))


if __name__ == "__main__":
    unittest.main()
