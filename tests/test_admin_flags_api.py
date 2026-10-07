"""功能旗標的 HTTP 層（`/api/admin/flags*`、`/api/features`）。SQL 對真的 PostgreSQL 在 tests/test_feature_flags_db.py。

- 權限：未登入 401；一般使用者 403；管理員（預設有 `ops.read`）可讀與匯出；寫入（PUT／DELETE／匯入）缺
  `ops.operate` 403 `missing_scope`、未提升 403 `elevation_required`——兩者都不會碰到 DB。
- 錯誤對應：未登記的 key 404 `flag_not_found`、不合法的作用域 422 `invalid_input`（rollback）、DB 不可用 503。
- 寫入成功：commit 之後才 `invalidate()`；匯入 dry-run 不 commit，套用有錯誤時 rollback 並回 422 `flag_import_invalid`。
- `/api/features`：任何登入使用者，只回自己的 `{key: bool}`（套用作用域），不含上限、覆寫與名單。

服務層函式以假的取代（這裡驗的是 HTTP 轉換與守門），`deps.SessionFactory` 換成記錄 commit／rollback 的假 session。
"""

from __future__ import annotations

import os
import unittest
from datetime import datetime, timezone
from unittest import mock

os.environ.setdefault("REPORT_MARK_SESSION_SECRET", "fixed-test-secret-0123456789")

from fake_accounts import FakeAccounts, install  # noqa: E402
from fake_feature_flags import flag_rows  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.services import feature_flags as ff  # noqa: E402
from web import auth, deps  # noqa: E402
from web.server import app  # noqa: E402

OPERATOR_PW = "operator-password-1"
ADMIN_PW = "plain-admin-password-1"
USER_PW = "alice-password-1"


class _Tx:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    def factory(self):
        tx = self

        class _Session:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def commit(self):
                tx.commits += 1

            async def rollback(self):
                tx.rollbacks += 1

        return _Session()


def _view(override=None):
    spec = ff.REGISTRY["qa.agentic"]
    flags = [ff.AdminFlag(spec=s, ceiling=True, override=override if k == "qa.agentic" else None,
                          effective="on", effective_for_me=True) for k, s in ff.REGISTRY.items()]
    assert flags[[f.spec for f in flags].index(spec)].spec is spec
    return ff.AdminView(flags=flags, usernames={"u1": "alice"}, ignored_keys=["old.flag"])


def _client():
    return TestClient(app, follow_redirects=False, base_url="http://127.0.0.1")


class AdminFlagsApiTests(unittest.TestCase):
    def setUp(self):
        auth._FAILS.clear()
        self.store = FakeAccounts()
        self.store.add_user("operator", OPERATOR_PW, "admin", scopes={"ops.operate"})
        self.store.add_user("plainadmin", ADMIN_PW, "admin")
        self.store.add_user("alice", USER_PW, "user")
        self._ctx = install(self.store)
        self._ctx.__enter__()
        self.tx = _Tx()
        self._orig_sf = deps.SessionFactory
        deps.SessionFactory = self.tx.factory
        self.invalidated = 0
        orig_invalidate = ff.invalidate

        def counting_invalidate():
            self.invalidated += 1
            orig_invalidate()

        self._inv = mock.patch.object(ff, "invalidate", counting_invalidate)
        self._inv.start()

    def tearDown(self):
        self._inv.stop()
        deps.SessionFactory = self._orig_sf
        self._ctx.__exit__(None, None, None)
        auth._FAILS.clear()

    def _login(self, username, password, *, elevate=False):
        client = _client()
        r = client.post("/login", data={"username": username, "password": password})
        self.assertEqual(r.status_code, 303, r.headers.get("location"))
        if elevate:
            self.assertEqual(client.post("/api/me/elevate", json={"password": password}).status_code, 200)
        return client

    # ── 權限 ───────────────────────────────────────────────────────
    def test_anonymous_is_401_and_user_is_403(self):
        anon = _client()
        for method, path in (("get", "/api/admin/flags"), ("get", "/api/admin/flags/export"),
                             ("put", "/api/admin/flags/qa.agentic"), ("get", "/api/features")):
            with self.subTest(path=path, method=method):
                kw = {"json": {"enabled": False}} if method == "put" else {}
                self.assertEqual(getattr(anon, method)(path, **kw).status_code, 401)
        alice = self._login("alice", USER_PW, elevate=True)
        for method, path in (("get", "/api/admin/flags"), ("get", "/api/admin/flags/export"),
                             ("put", "/api/admin/flags/qa.agentic"), ("delete", "/api/admin/flags/qa.agentic")):
            with self.subTest(path=path, method=method):
                kw = {"json": {"enabled": False}} if method == "put" else {}
                self.assertEqual(getattr(alice, method)(path, **kw).status_code, 403)

    def test_writes_need_ops_operate_and_elevation_before_touching_the_db(self):
        called = []

        async def boom(*a, **k):
            called.append(1)
            raise AssertionError("守門之前不得碰 DB")

        plain = self._login("plainadmin", ADMIN_PW, elevate=True)
        not_elevated = self._login("operator", OPERATOR_PW)
        doc = {"format": "report-mark/feature-flags", "format_version": 1, "registry_version": "x", "flags": []}
        writes = (("put", "/api/admin/flags/qa.agentic", {"json": {"enabled": False}}),
                  ("delete", "/api/admin/flags/qa.agentic", {}),
                  ("post", "/api/admin/flags/import", {"json": {"dry_run": True, "document": doc}}))
        with mock.patch.object(ff, "set_override", boom), mock.patch.object(ff, "clear_override", boom), \
                mock.patch.object(ff, "plan_import", boom), mock.patch.object(ff, "apply_import", boom):
            for method, path, kw in writes:
                with self.subTest(path=path, who="no ops.operate"):
                    r = getattr(plain, method)(path, **kw)
                    self.assertEqual((r.status_code, r.json()["code"]), (403, "missing_scope"))
                with self.subTest(path=path, who="not elevated"):
                    r = getattr(not_elevated, method)(path, **kw)
                    self.assertEqual((r.status_code, r.json()["code"]), (403, "elevation_required"))
        self.assertEqual(called, [])

    # ── 讀取 ───────────────────────────────────────────────────────
    def test_list_shape(self):
        when = datetime(2026, 10, 7, 8, 0, tzinfo=timezone.utc)
        o = ff.StoredOverride("qa.agentic", True, ("admin",), ("u1",), note="觀察中", updated_by="u1", updated_at=when)
        seen = {}

        async def fake_view(session, me=None):
            seen["me"] = me
            return _view(o)

        with mock.patch.object(ff, "admin_view", fake_view):
            r = self._login("plainadmin", ADMIN_PW).get("/api/admin/flags")
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["registry_version"], ff.REGISTRY_VERSION)
        self.assertEqual(body["ignored_keys"], ["old.flag"])
        self.assertEqual([i["key"] for i in body["items"]], list(ff.REGISTRY))
        item = next(i for i in body["items"] if i["key"] == "qa.agentic")
        self.assertEqual(item["ceiling_env"], "QA_AGENTIC_ENABLED")
        self.assertEqual(item["override"], {
            "enabled": True, "allow_roles": ["admin"], "allow_users": [{"id": "u1", "username": "alice"}],
            "note": "觀察中", "updated_by": "alice", "updated_at": when.isoformat(),
        })
        self.assertEqual(seen["me"].username, "plainadmin")  # effective_for_me 用的是發請求的人

    def test_list_db_failure_is_503(self):
        async def broken(session, me=None):
            raise OSError("connection refused")

        with mock.patch.object(ff, "admin_view", broken), self.assertLogs("web.routers.admin_flags", "ERROR"):
            r = self._login("plainadmin", ADMIN_PW).get("/api/admin/flags")
        self.assertEqual((r.status_code, r.json()["code"]), (503, "flags_unavailable"))

    def test_export_is_read_only_for_any_admin(self):
        doc = {"format": "report-mark/feature-flags", "format_version": 1, "registry_version": ff.REGISTRY_VERSION,
               "source_environment": "production",
               "flags": [{"key": "qa.agentic", "override": {"enabled": False, "allow_roles": None,
                                                           "allow_users": None, "note": None}}]}

        async def fake_export(session):
            return doc

        with mock.patch.object(ff, "export_document", fake_export):
            r = self._login("plainadmin", ADMIN_PW).get("/api/admin/flags/export")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json(), doc)
        self.assertEqual(self.tx.commits, 0)

    # ── 寫入 ───────────────────────────────────────────────────────
    def test_put_commits_then_invalidates(self):
        calls = []

        async def fake_set(session, key, **kw):
            calls.append((key, kw))
            after = ff.StoredOverride(key, kw["enabled"], None, None)
            return None, after, True

        async def fake_view(session, me=None):
            return _view(ff.StoredOverride("qa.agentic", False, None, None))

        with mock.patch.object(ff, "set_override", fake_set), mock.patch.object(ff, "admin_view", fake_view):
            r = self._login("operator", OPERATOR_PW, elevate=True).put(
                "/api/admin/flags/qa.agentic", json={"enabled": False, "note": "先關"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["key"], "qa.agentic")
        self.assertEqual(r.json()["override"]["enabled"], False)
        key, kw = calls[0]
        self.assertEqual((key, kw["enabled"], kw["allow_roles"], kw["allow_users"], kw["note"]),
                         ("qa.agentic", False, None, None, "先關"))
        self.assertIsNotNone(kw["actor_id"])
        self.assertEqual((self.tx.commits, self.tx.rollbacks), (1, 0))
        self.assertGreaterEqual(self.invalidated, 1)

    def test_put_maps_errors_and_rolls_back(self):
        async def unknown(session, key, **kw):
            raise ff.UnknownFlagError("沒有這個旗標：x")

        async def invalid(session, key, **kw):
            raise ff.InvalidFlagInputError("作用域不可為空")

        op = self._login("operator", OPERATOR_PW, elevate=True)
        with mock.patch.object(ff, "set_override", unknown):
            r = op.put("/api/admin/flags/no.such", json={"enabled": True})
        self.assertEqual((r.status_code, r.json()["code"]), (404, "flag_not_found"))
        with mock.patch.object(ff, "set_override", invalid):
            r = op.put("/api/admin/flags/qa.agentic", json={"enabled": True, "allow_roles": []})
        self.assertEqual((r.status_code, r.json()["code"]), (422, "invalid_input"))
        self.assertEqual((self.tx.commits, self.tx.rollbacks), (0, 2))
        # pydantic 擋：不認得的角色
        r = op.put("/api/admin/flags/qa.agentic", json={"enabled": True, "allow_roles": ["root"]})
        self.assertEqual(r.status_code, 422)

    def test_delete(self):
        async def fake_clear(session, key, **kw):
            return ff.StoredOverride(key, False, None, None), True

        async def fake_view(session, me=None):
            return _view(None)

        with mock.patch.object(ff, "clear_override", fake_clear), mock.patch.object(ff, "admin_view", fake_view):
            r = self._login("operator", OPERATOR_PW, elevate=True).delete("/api/admin/flags/qa.agentic")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIsNone(r.json()["override"])
        self.assertEqual(self.tx.commits, 1)

    def test_import_dry_run_and_apply(self):
        doc = {"format": "report-mark/feature-flags", "format_version": 1, "registry_version": "000000000000",
               "flags": [{"key": "qa.agentic", "override": {"enabled": False}},
                         {"key": "ask.rerank", "override": None}]}
        plan = ff.ImportPlan(
            changes=[ff.ImportChange("qa.agentic", "create", None, ff.StoredOverride("qa.agentic", False, None, None)),
                     ff.ImportChange("ask.rerank", "unchanged", None, None)],
            errors=[], document_registry_version="000000000000", usernames={})
        seen = {}

        async def fake_plan(session, document):
            seen["plan"] = document
            return plan

        async def fake_apply(session, document, *, actor_id):
            seen["apply"] = (document, actor_id)
            return plan

        op = self._login("operator", OPERATOR_PW, elevate=True)
        with mock.patch.object(ff, "plan_import", fake_plan), mock.patch.object(ff, "apply_import", fake_apply):
            r = op.post("/api/admin/flags/import", json={"document": doc})  # dry_run 預設 true
            self.assertEqual(r.status_code, 200, r.text)
            body = r.json()
            self.assertEqual((body["dry_run"], body["applied"], body["registry_version_match"]), (True, False, False))
            self.assertEqual(body["changes"][0]["action"], "create")
            self.assertEqual(body["changes"][0]["after"]["enabled"], False)
            self.assertEqual(self.tx.commits, 0)
            self.assertNotIn("apply", seen)
            r = op.post("/api/admin/flags/import", json={"dry_run": False, "document": doc})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((r.json()["dry_run"], r.json()["applied"]), (False, True))
        self.assertEqual(self.tx.commits, 1)
        self.assertEqual(seen["apply"][0]["flags"][0]["key"], "qa.agentic")

    def test_import_with_errors(self):
        doc = {"format": "report-mark/feature-flags", "format_version": 1, "registry_version": ff.REGISTRY_VERSION,
               "flags": [{"key": "brand.new", "override": {"enabled": True}}]}
        problem = ff.ImportProblem("brand.new", "unknown_key", "這個環境沒有旗標 brand.new")
        bad = ff.ImportPlan(changes=[], errors=[problem], document_registry_version=ff.REGISTRY_VERSION, usernames={})

        async def fake(session, document, **kw):
            return bad

        op = self._login("operator", OPERATOR_PW, elevate=True)
        with mock.patch.object(ff, "plan_import", fake), mock.patch.object(ff, "apply_import", fake):
            r = op.post("/api/admin/flags/import", json={"document": doc})
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.json()["errors"][0]["code"], "unknown_key")
            r = op.post("/api/admin/flags/import", json={"dry_run": False, "document": doc})
        self.assertEqual((r.status_code, r.json()["code"]), (422, "flag_import_invalid"))
        self.assertEqual(r.json()["errors"][0]["key"], "brand.new")
        self.assertEqual((self.tx.commits, self.tx.rollbacks), (0, 1))

    def test_import_rejects_a_foreign_format(self):
        op = self._login("operator", OPERATOR_PW, elevate=True)
        r = op.post("/api/admin/flags/import", json={"document": {"format": "other", "format_version": 1,
                                                                   "registry_version": "x", "flags": []}})
        self.assertEqual(r.status_code, 422)


class FeaturesApiTests(unittest.TestCase):
    def setUp(self):
        auth._FAILS.clear()
        self.store = FakeAccounts()
        self.alice = self.store.add_user("alice", USER_PW, "user")
        self.bob = self.store.add_user("bob", "bob-password-1", "user")
        self._ctx = install(self.store)
        self._ctx.__enter__()
        ff.invalidate()

    def tearDown(self):
        ff.invalidate()
        self._ctx.__exit__(None, None, None)
        auth._FAILS.clear()

    def _get(self, username, password):
        client = _client()
        self.assertEqual(client.post("/login", data={"username": username, "password": password}).status_code, 303)
        return client.get("/api/features")

    def test_any_user_gets_only_their_own_effective_values(self):
        with flag_rows(("qa.agentic", True, None, [self.alice]), ("quota.enforce", True, None, None)):
            a = self._get("alice", USER_PW)
            b = self._get("bob", "bob-password-1")
        self.assertEqual(a.status_code, 200, a.text)
        self.assertEqual(set(a.json()), {"features"})  # 不含上限、覆寫、作用域名單
        self.assertEqual(set(a.json()["features"]), set(ff.REGISTRY))
        self.assertTrue(all(isinstance(v, bool) for v in a.json()["features"].values()))
        agentic_ceiling = ff.REGISTRY["qa.agentic"].ceiling(__import__("app.config").config.get_settings())
        self.assertEqual(a.json()["features"]["qa.agentic"], agentic_ceiling)
        self.assertFalse(b.json()["features"]["qa.agentic"])  # 作用域只開給 alice
        self.assertNotIn(self.alice, a.text)

    def test_defaults_equal_the_environment_ceilings(self):
        from app import config

        s = config.get_settings()
        body = self._get("alice", USER_PW).json()["features"]
        for key, spec in ff.REGISTRY.items():
            self.assertEqual(body[key], spec.ceiling(s) and spec.default, key)
        # 網搜：沒有覆寫時一律 false（前端 useWebSearchPaused 因此隱藏開關），不論 ASK_ENABLE_WEB
        with mock.patch.object(config, "_SETTINGS", __import__("dataclasses").replace(s, ask_enable_web=True)):
            self.assertFalse(self._get("alice", USER_PW).json()["features"]["ask.web_search"])


if __name__ == "__main__":
    unittest.main()
