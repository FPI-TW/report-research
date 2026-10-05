"""授權（web/authz.py）：一般使用者打不到管理端點；真正擋人的是後端，不是前端 route guard。

兩層：
- HTTP 層：user 角色打 /api/review/*（以及 /api/admin/*，見 tests/test_admin_api.py）回 403。
- 結構層：app 上**每一條** /api/admin/*、/api/review/* 路由的 dependency 樹裡都要有
  `authz.require_admin`。新增管理端點時忘了掛，這裡會紅——不必等有人想到要寫 403 測試。
"""

from __future__ import annotations

import unittest

from fake_accounts import FakeAccounts, install
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

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
    def test_every_admin_route_requires_admin(self):
        guarded = [r for r in _iter_api_routes(app.routes) if r.path.startswith(_ADMIN_PREFIXES)]
        self.assertTrue(guarded, "找不到任何管理端點——路由前綴改了？")
        missing = [
            f"{sorted(r.methods)} {r.path}" for r in guarded
            if authz.require_admin not in set(_dependency_calls(r.dependant))
        ]
        self.assertEqual(missing, [], f"這些管理端點沒有掛 authz.require_admin：{missing}")


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
        self.assertEqual(body, {"id": None, "username": DEV_USER.username, "role": "admin"})


if __name__ == "__main__":
    unittest.main()
