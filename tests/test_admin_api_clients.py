"""API 用戶端管理 API（/api/admin/api-clients*，HTTP 層）。規則與交易對真 DB 的驗證在 test_api_clients_db.py。

重點：一般使用者與沒有 `api_clients.manage` 的管理員打不到（403）、建立與輪替要已提升、原始金鑰只在那兩個
回應出現且帶 `no-store`、列表不含金鑰或 hash、服務層的錯誤代碼對到 400／404／409。
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from typing import get_args

from fake_accounts import FakeAccounts, install
from fastapi.testclient import TestClient

from app.services import api_clients
from app.services.api_clients import ApiClient, ApiClientError
from web import auth, deps
from web.routers import admin_api_clients
from web.server import app

ADMIN_PW = "root-password-1"
USER_PW = "alice-password-1"
WHEN = datetime(2026, 10, 7, 9, 30, tzinfo=timezone.utc)
# 假金鑰在執行期組出：寫成 `KEY = "rmk_…"` 的字面值會被 gitleaks 的 generic-api-key 判成金鑰。
RAW_KEY = "_".join(["rmk", "0123abcd", "x" * 43])
HASH = api_clients.hash_key(RAW_KEY)

BODY = {
    "name": "外部系統 A", "scopes": ["search", "report.file"], "rate_limit_per_min": 60, "daily_quota": 1000,
    "entitlements": {"market": ["TW", "US"], "source": ["元大"]}, "note": "測試",
}


def _client():
    return TestClient(app, follow_redirects=False, base_url="http://127.0.0.1")


def _api_client(client_id=7, *, enabled=True, prefix="0123abcd") -> ApiClient:
    return ApiClient(
        id=client_id, name="外部系統 A", key_prefix=prefix, enabled=enabled, scopes=frozenset({"search"}),
        rate_limit_per_min=60, daily_quota=1000, entitlements={"market": ("TW", "US"), "source": ("元大",)},
        note=None, created_at=WHEN, updated_at=WHEN, key_rotated_at=None, last_used_at=None,
    )


class _FakeApiClients:
    """deps.api_clients 的替身：記下呼叫，回固定結果或拋指定錯誤。"""

    ApiClientError = ApiClientError

    def __init__(self):
        self.calls: list = []
        self.error: Exception | None = None

    def _maybe_raise(self):
        if self.error is not None:
            raise self.error

    async def list_clients(self):
        self.calls.append(("list",))
        return [_api_client(), _api_client(8, enabled=False, prefix="89abcdef")]

    async def create_client(self, *, actor_id, name, scopes, rate_limit_per_min, daily_quota, entitlements,
                            note=None):
        self.calls.append(("create", actor_id, name, sorted(scopes), rate_limit_per_min, daily_quota,
                           entitlements, note))
        self._maybe_raise()
        return _api_client(), RAW_KEY

    async def update_client(self, *, actor_id, client_id, enabled=None, scopes=None, rate_limit_per_min=None,
                            daily_quota=None, note=None):
        self.calls.append(("update", actor_id, client_id, enabled, scopes, rate_limit_per_min, daily_quota, note))
        self._maybe_raise()
        return _api_client(client_id, enabled=enabled if enabled is not None else True)

    async def replace_entitlements(self, *, actor_id, client_id, entitlements):
        self.calls.append(("entitlements", actor_id, client_id, entitlements))
        self._maybe_raise()
        return _api_client(client_id)

    async def rotate_key(self, *, actor_id, client_id):
        self.calls.append(("rotate", actor_id, client_id))
        self._maybe_raise()
        return _api_client(client_id, prefix="fedcba98"), RAW_KEY


class AdminApiClientsTests(unittest.TestCase):
    def setUp(self):
        auth._FAILS.clear()
        self.store = FakeAccounts()
        self.boss = self.store.add_user("boss", ADMIN_PW, "admin", scopes={"api_clients.manage"})
        self.store.add_user("plain", ADMIN_PW, "admin")
        self.store.add_user("alice", USER_PW, "user")
        self._ctx = install(self.store)
        self._ctx.__enter__()
        self.fake = _FakeApiClients()
        self._orig = deps.api_clients
        deps.api_clients = self.fake

    def tearDown(self):
        deps.api_clients = self._orig
        self._ctx.__exit__(None, None, None)
        auth._FAILS.clear()

    def _login(self, username, password, *, elevate=False):
        client = _client()
        self.assertEqual(client.post("/login", data={"username": username, "password": password}).status_code, 303)
        if elevate:
            self.assertEqual(client.post("/api/admin/elevate", json={"password": password}).status_code, 200)
        return client

    def _all_routes(self, client):
        return [
            client.get("/api/admin/api-clients"),
            client.post("/api/admin/api-clients", json=BODY),
            client.patch("/api/admin/api-clients/7", json={"enabled": False}),
            client.put("/api/admin/api-clients/7/entitlements", json={"market": ["TW"]}),
            client.post("/api/admin/api-clients/7/rotate"),
        ]

    # ── 授權 ──────────────────────────────────────────────────────────

    def test_non_admin_gets_403_everywhere(self):
        for r in self._all_routes(self._login("alice", USER_PW)):
            self.assertEqual(r.status_code, 403, r.text)
        self.assertEqual(self.fake.calls, [])

    def test_admin_without_scope_gets_403_everywhere(self):
        for r in self._all_routes(self._login("plain", ADMIN_PW, elevate=True)):
            self.assertEqual(r.status_code, 403, r.text)
            self.assertEqual(r.json()["code"], "missing_scope")
        self.assertEqual(self.fake.calls, [])

    def test_unauthenticated_gets_401(self):
        self.assertEqual(_client().get("/api/admin/api-clients").status_code, 401)

    def test_create_and_rotate_require_elevation(self):
        boss = self._login("boss", ADMIN_PW)
        for r in (boss.post("/api/admin/api-clients", json=BODY), boss.post("/api/admin/api-clients/7/rotate")):
            self.assertEqual(r.status_code, 403, r.text)
            self.assertEqual(r.json()["code"], "elevation_required")
            self.assertNotIn("api_key", r.json())
        self.assertEqual(self.fake.calls, [])

    def test_update_and_entitlements_do_not_require_elevation(self):
        boss = self._login("boss", ADMIN_PW)
        self.assertEqual(boss.patch("/api/admin/api-clients/7", json={"enabled": False}).status_code, 200)
        self.assertEqual(boss.put("/api/admin/api-clients/7/entitlements", json={"market": ["TW"]}).status_code, 200)

    # ── 金鑰只出現一次 ────────────────────────────────────────────────

    def test_create_returns_key_once_with_no_store(self):
        r = self._login("boss", ADMIN_PW, elevate=True).post("/api/admin/api-clients", json=BODY)
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(r.headers.get("cache-control"), "no-store")
        body = r.json()
        self.assertEqual(body["api_key"], RAW_KEY)
        self.assertEqual(body["key_prefix"], "0123abcd")
        self.assertNotIn("key_hash", body)
        self.assertEqual(self.fake.calls, [(
            "create", self.boss, "外部系統 A", ["report.file", "search"], 60, 1000,
            {"market": ["TW", "US"], "source": ["元大"]}, "測試",
        )])

    def test_rotate_returns_new_key_once_with_no_store(self):
        r = self._login("boss", ADMIN_PW, elevate=True).post("/api/admin/api-clients/7/rotate")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.headers.get("cache-control"), "no-store")
        self.assertEqual((r.json()["api_key"], r.json()["key_prefix"]), (RAW_KEY, "fedcba98"))
        self.assertEqual(self.fake.calls, [("rotate", self.boss, 7)])

    def test_list_has_no_key_or_hash(self):
        r = self._login("boss", ADMIN_PW).get("/api/admin/api-clients")
        self.assertEqual(r.status_code, 200, r.text)
        items = r.json()["items"]
        self.assertEqual([i["id"] for i in items], [7, 8])
        self.assertEqual(items[0]["entitlements"], {
            "market": ["TW", "US"], "source": ["元大"], "report_type": None, "instrument_type": None,
        })
        self.assertEqual(items[0]["scopes"], ["search"])
        self.assertFalse(items[1]["enabled"])
        for item in items:
            self.assertNotIn("api_key", item)
            self.assertNotIn("key_hash", item)
        self.assertNotIn(RAW_KEY, r.text)
        self.assertNotIn(HASH, r.text)

    def test_update_and_entitlements_have_no_key(self):
        boss = self._login("boss", ADMIN_PW)
        for r in (boss.patch("/api/admin/api-clients/7", json={"note": ""}),
                  boss.put("/api/admin/api-clients/7/entitlements", json={"market": ["TW"]})):
            self.assertEqual(r.status_code, 200, r.text)
            self.assertNotIn("api_key", r.json())

    # ── 參數轉交 ──────────────────────────────────────────────────────

    def test_update_passes_only_given_fields(self):
        r = self._login("boss", ADMIN_PW).patch(
            "/api/admin/api-clients/7", json={"enabled": False, "daily_quota": 50},
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertFalse(r.json()["enabled"])
        self.assertEqual(self.fake.calls, [("update", self.boss, 7, False, None, None, 50, None)])

    def test_entitlements_omit_unset_dimensions(self):
        r = self._login("boss", ADMIN_PW).put(
            "/api/admin/api-clients/7/entitlements",
            json={"market": ["TW"], "source": None, "report_type": ["產業報告"]},
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.fake.calls, [
            ("entitlements", self.boss, 7, {"market": ["TW"], "report_type": ["產業報告"]}),
        ])

    def test_unknown_scope_or_dimension_is_rejected_before_service(self):
        boss = self._login("boss", ADMIN_PW, elevate=True)
        self.assertEqual(boss.post("/api/admin/api-clients", json={**BODY, "scopes": ["admin"]}).status_code, 422)
        r = boss.put("/api/admin/api-clients/7/entitlements", json={"market": ["TW"], "region": ["x"]})
        self.assertEqual(r.status_code, 422)
        self.assertEqual(self.fake.calls, [])

    # ── 錯誤對應 ──────────────────────────────────────────────────────

    def test_invalid_input_is_400(self):
        self.fake.error = ApiClientError("invalid_input", "必須至少允許一個市場（market）")
        r = self._login("boss", ADMIN_PW).put("/api/admin/api-clients/7/entitlements", json={"market": []})
        self.assertEqual(r.status_code, 400, r.text)
        self.assertEqual(r.json()["code"], "invalid_input")
        self.assertEqual(r.json()["detail"], "必須至少允許一個市場（market）")

    def test_not_found_is_404(self):
        self.fake.error = ApiClientError("not_found", "API 用戶端不存在")
        boss = self._login("boss", ADMIN_PW, elevate=True)
        for r in (boss.patch("/api/admin/api-clients/999", json={"enabled": True}),
                  boss.post("/api/admin/api-clients/999/rotate")):
            self.assertEqual(r.status_code, 404, r.text)
            self.assertEqual(r.json()["code"], "not_found")
            self.assertNotIn("api_key", r.json())

    def test_name_taken_is_409(self):
        self.fake.error = ApiClientError("name_taken", "API 用戶端「外部系統 A」已存在")
        r = self._login("boss", ADMIN_PW, elevate=True).post("/api/admin/api-clients", json=BODY)
        self.assertEqual(r.status_code, 409, r.text)
        self.assertEqual(r.json()["code"], "name_taken")
        self.assertIsNone(r.headers.get("cache-control"))

    # ── 詞彙與服務層一致 ──────────────────────────────────────────────

    def test_literals_match_service(self):
        self.assertEqual(set(get_args(admin_api_clients.ApiClientScope)), set(api_clients.KEY_SCOPES))
        self.assertEqual(get_args(admin_api_clients.EntitlementDimension), api_clients.ENTITLEMENT_DIMENSIONS)
        self.assertEqual(tuple(admin_api_clients.ApiClientEntitlements.model_fields),
                         api_clients.ENTITLEMENT_DIMENSIONS)


if __name__ == "__main__":
    unittest.main()
