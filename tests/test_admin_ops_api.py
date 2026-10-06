"""維運狀態 API（/api/admin/ops/*，HTTP 層）。代理本體在 tests/test_ops_agent.py。

重點：只有帶 `ops.read` 的管理員打得到；代理不可用回 503 `ops_agent_unavailable` 而且不影響其他端點
（fail-open）；參數在 web 端先驗一次、代理那端再驗一次；代理的錯誤 code 對到穩定的 HTTP 錯誤。
"""

from __future__ import annotations

import dataclasses
import os
import tempfile
import unittest
from unittest import mock

from fake_accounts import FakeAccounts, install
from fake_ops_agent import FakeOpsAgent, default_handler, err, ok
from fastapi.testclient import TestClient

from web import auth, ops_client
from web.server import app

ADMIN_PW = "root-password-1"
USER_PW = "alice-password-1"
ENDPOINTS = ("/api/admin/ops/services", "/api/admin/ops/services/web", "/api/admin/ops/services/web/logs")


def _client():
    return TestClient(app, follow_redirects=False, base_url="http://127.0.0.1")


class _NoOpsRead(FakeAccounts):
    """`limited` 是管理員但被拿掉 ops.read（目前的 scope 模型裡管理員預設都有，這裡模擬將來可收回的情況）。"""

    def _user(self, row, elevated_until=None):
        user = super()._user(row, elevated_until)
        if user.username == "limited":
            return dataclasses.replace(user, scopes=user.scopes - {"ops.read"})
        return user


class AdminOpsApiTests(unittest.TestCase):
    def setUp(self):
        auth._FAILS.clear()
        self.store = _NoOpsRead()
        self.store.add_user("root", ADMIN_PW, "admin")
        self.store.add_user("alice", USER_PW, "user")
        self.store.add_user("limited", ADMIN_PW, "admin")
        self._ctx = install(self.store)
        self._ctx.__enter__()
        self.admin = self._login("root", ADMIN_PW)

    def tearDown(self):
        self._ctx.__exit__(None, None, None)
        auth._FAILS.clear()

    def _login(self, username, password):
        client = _client()
        r = client.post("/login", data={"username": username, "password": password})
        self.assertEqual(r.status_code, 303, r.headers.get("location"))
        return client

    # ── 正常路徑 ─────────────────────────────────────────────────────
    def test_list_services(self):
        with FakeOpsAgent() as agent, agent.installed():
            r = self.admin.get("/api/admin/ops/services")
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["environment"], "production")
        self.assertEqual([i["name"] for i in body["items"]], ["web", "postgres"])
        self.assertEqual(body["items"][0]["systemd"]["active_state"], "active")
        self.assertEqual(body["items"][1]["container"]["image"], "pgvector/pgvector:pg16")
        self.assertEqual(agent.requests[0]["op"], "list")
        self.assertEqual(agent.requests[0]["env"], "production")
        self.assertNotIn("service", agent.requests[0])

    def test_service_detail(self):
        with FakeOpsAgent() as agent, agent.installed():
            r = self.admin.get("/api/admin/ops/services/postgres")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["kind"], "container")
        self.assertEqual(r.json()["checked_at"], "2026-10-06T02:00:00Z")
        self.assertEqual((agent.requests[0]["op"], agent.requests[0]["service"]), ("status", "postgres"))

    def test_logs_forward_only_since_and_lines(self):
        with FakeOpsAgent() as agent, agent.installed():
            r = self.admin.get("/api/admin/ops/services/web/logs", params={"since": "15m", "lines": 50})
            default = self.admin.get("/api/admin/ops/services/web/logs")
            iso = self.admin.get("/api/admin/ops/services/web/logs", params={"since": "2026-10-06T09:00:00+08:00"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["entries"], ["2026-10-06T10:00:00+08:00 host uv[1]: hello"])
        self.assertEqual(agent.requests[0]["params"], {"since": "15m", "lines": 50})
        self.assertEqual(default.status_code, 200)
        self.assertEqual(agent.requests[1]["params"], {"since": "1h", "lines": 200})
        self.assertEqual(iso.status_code, 200, iso.text)
        self.assertEqual(agent.requests[2]["params"]["since"], "2026-10-06T09:00:00+08:00")

    # ── 參數在 web 端先擋 ────────────────────────────────────────────
    def test_bad_params_rejected_before_reaching_agent(self):
        with FakeOpsAgent() as agent, agent.installed():
            cases = [
                ("/api/admin/ops/services/web/logs", {"lines": 0}),
                ("/api/admin/ops/services/web/logs", {"lines": 1001}),
                ("/api/admin/ops/services/web/logs", {"lines": "many"}),
                ("/api/admin/ops/services/web/logs", {"since": "1 day"}),
                ("/api/admin/ops/services/web/logs", {"since": "x" * 41}),
                ("/api/admin/ops/services/web/logs", {"since": "$(id)"}),
                ("/api/admin/ops/services/Web", {}),
                ("/api/admin/ops/services/report-mark-web.service", {}),
                ("/api/admin/ops/services/" + "a" * 41, {}),
            ]
            for path, params in cases:
                with self.subTest(path=path, params=params):
                    self.assertEqual(self.admin.get(path, params=params).status_code, 422)
        self.assertEqual(agent.requests, [])

    def test_agent_error_codes_map_to_http(self):
        mapping = {
            "unknown_service": (404, "ops_service_not_found"),
            "action_not_allowed": (403, "ops_action_not_allowed"),
            "invalid_params": (400, "invalid_params"),
            "command_timeout": (504, "ops_timeout"),
            "command_failed": (502, "ops_command_failed"),
            "busy": (503, "ops_agent_busy"),
            "internal_error": (502, "ops_agent_error"),
        }
        for code, expected in mapping.items():
            with self.subTest(code=code), FakeOpsAgent(lambda req, c=code: err(req, c, "代理說不行")) as agent, \
                    agent.installed():
                r = self.admin.get("/api/admin/ops/services/web/logs")
                self.assertEqual((r.status_code, r.json()["code"]), expected)
                self.assertEqual(r.json()["detail"], "代理說不行")

    def test_unknown_service_is_404(self):
        with FakeOpsAgent() as agent, agent.installed():
            r = self.admin.get("/api/admin/ops/services/nope")
        self.assertEqual((r.status_code, r.json()["code"]), (404, "ops_service_not_found"))

    def test_malformed_agent_result_is_502(self):
        with FakeOpsAgent(lambda req: ok(req, {"unexpected": True})) as agent, agent.installed():
            r = self.admin.get("/api/admin/ops/services")
        self.assertEqual((r.status_code, r.json()["code"]), (502, "ops_agent_error"))

    # ── fail-open：代理不可用 ────────────────────────────────────────
    def _assert_unavailable(self, r):
        self.assertEqual(r.status_code, 503, r.text)
        self.assertEqual(r.json()["code"], "ops_agent_unavailable")

    def test_agent_not_running_is_503_and_rest_of_web_works(self):
        missing = os.path.join(tempfile.gettempdir(), "rmops-missing", "agent.sock")
        client = ops_client.OpsClient(missing, "production", 1.0)
        with mock.patch.object(ops_client, "default_client", lambda: client):
            for path in ENDPOINTS:
                with self.subTest(path=path):
                    self._assert_unavailable(self.admin.get(path))
            self.assertEqual(self.admin.get("/api/admin/users").status_code, 200)
            self.assertEqual(self.admin.get("/api/me").status_code, 200)

    def test_default_settings_point_nowhere_in_tests(self):
        """conftest 把 OPS_AGENT_SOCKET 導向不存在的路徑：沒裝假代理的測試絕不會連到本機真的代理。"""
        self.assertTrue(ops_client.default_client().socket_path.startswith("/nonexistent/"))
        self._assert_unavailable(self.admin.get("/api/admin/ops/services"))

    def test_agent_hang_times_out_as_503(self):
        with FakeOpsAgent(lambda req: "hang") as agent, agent.installed(timeout=0.3):
            self._assert_unavailable(self.admin.get("/api/admin/ops/services"))

    def test_agent_closing_or_garbage_is_503(self):
        for mode in ("close", "garbage"):
            with self.subTest(mode=mode), FakeOpsAgent(lambda req, m=mode: m) as agent, agent.installed():
                self._assert_unavailable(self.admin.get("/api/admin/ops/services"))

    def test_environment_mismatch_is_503(self):
        # web 宣告 development、代理是 production：代理拒絕
        def handler(req):
            return err(req, "environment_mismatch", "這個代理只服務 production", env="production")

        with FakeOpsAgent(handler) as agent, agent.installed(environment="development"):
            self._assert_unavailable(self.admin.get("/api/admin/ops/services"))
        # 代理回的環境與 web 宣告的不同（即使 ok）：也不採信
        with FakeOpsAgent(lambda req: {**default_handler(req), "env": "development"}) as agent, agent.installed():
            self._assert_unavailable(self.admin.get("/api/admin/ops/services"))

    def test_forbidden_peer_is_503(self):
        def handler(_req):
            return {"v": 1, "id": None, "env": "production", "ok": False,
                    "error": {"code": "forbidden_peer", "message": "這個使用者不能連線到維運代理"}}

        with FakeOpsAgent(handler) as agent, agent.installed():
            r = self.admin.get("/api/admin/ops/services")
        self._assert_unavailable(r)
        self.assertIn("不能連線", r.json()["detail"])

    def test_invalid_environment_setting_disables_client(self):
        client = ops_client.OpsClient("/nonexistent/x.sock", "", 1.0)
        with mock.patch.object(ops_client, "default_client", lambda: client):
            self._assert_unavailable(self.admin.get("/api/admin/ops/services"))

    # ── 授權 ─────────────────────────────────────────────────────────
    def test_plain_user_gets_403_and_agent_is_never_asked(self):
        user = self._login("alice", USER_PW)
        with FakeOpsAgent() as agent, agent.installed():
            for path in ENDPOINTS:
                with self.subTest(path=path):
                    self.assertEqual(user.get(path).status_code, 403)
        self.assertEqual(agent.requests, [])

    def test_admin_without_ops_read_gets_missing_scope(self):
        limited = self._login("limited", ADMIN_PW)
        with FakeOpsAgent() as agent, agent.installed():
            for path in ENDPOINTS:
                with self.subTest(path=path):
                    r = limited.get(path)
                    self.assertEqual((r.status_code, r.json()["code"]), (403, "missing_scope"))
        self.assertEqual(agent.requests, [])

    def test_unauthenticated_gets_401(self):
        for path in ENDPOINTS:
            self.assertEqual(_client().get(path).status_code, 401)


class OpsSettingsTests(unittest.TestCase):
    def test_config_sockets_match_agent_protocol(self):
        from app import config
        from ops_agent.protocol import CANONICAL_SOCKETS

        self.assertEqual(config._OPS_SOCKETS, CANONICAL_SOCKETS)

    def test_environment_and_socket_defaults(self):
        from app import config

        with mock.patch.dict(os.environ, {"OPS_AGENT_ENVIRONMENT": "development", "OPS_AGENT_SOCKET": ""}):
            env = config._ops_agent_environment()
            self.assertEqual((env, config._ops_agent_socket(env)),
                             ("development", "/run/report-mark-ops-dev/agent.sock"))
        with mock.patch.dict(os.environ, {"OPS_AGENT_ENVIRONMENT": "", "OPS_AGENT_SOCKET": ""}):
            env = config._ops_agent_environment()
            self.assertEqual((env, config._ops_agent_socket(env)),
                             ("production", "/run/report-mark-ops/agent.sock"))

    def test_typo_disables_instead_of_falling_back_to_production(self):
        from app import config

        with mock.patch.dict(os.environ, {"OPS_AGENT_ENVIRONMENT": "dev", "OPS_AGENT_SOCKET": ""}):
            with self.assertLogs("app.config", "WARNING"):
                env = config._ops_agent_environment()
            self.assertEqual((env, config._ops_agent_socket(env)), ("", ""))


if __name__ == "__main__":
    unittest.main()
