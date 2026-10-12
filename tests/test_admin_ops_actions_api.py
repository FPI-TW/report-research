"""維運寫入類 API（POST /api/admin/ops/services/{name}/restart|run，HTTP 層）。代理端的規則在
tests/test_ops_agent_actions.py。

重點：要管理員＋另外授予的 `ops.operate`＋10 分鐘內重新驗證過密碼（缺 scope 403 `missing_scope`、未提升 403
`elevation_required`，兩者都不會碰到代理）；代理的拒絕對到穩定的 HTTP 錯誤（409 `already_running`、403
`action_not_allowed`、404 `ops_service_not_found`、503 `ops_agent_unavailable`）；每次嘗試都寫稽核，
detail 不含代理的訊息全文。
"""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

from fake_accounts import FakeAccounts, install
from fake_ops_agent import FakeOpsAgent, action_result, default_handler, err, ok
from fastapi.testclient import TestClient

from web import auth, ops_client
from web.server import app

OPERATOR_PW = "operator-password-1"
PLAIN_ADMIN_PW = "plain-admin-password-1"
USER_PW = "alice-password-1"


def _client():
    return TestClient(app, follow_redirects=False, base_url="http://127.0.0.1")


class AdminOpsActionsApiTests(unittest.TestCase):
    def setUp(self):
        auth._FAILS.clear()
        self.store = FakeAccounts()
        self.operator_id = self.store.add_user("operator", OPERATOR_PW, "admin", scopes={"ops.operate"})
        self.store.add_user("plainadmin", PLAIN_ADMIN_PW, "admin")
        self.store.add_user("alice", USER_PW, "user")
        self._ctx = install(self.store)
        self._ctx.__enter__()
        self.operator = self._login("operator", OPERATOR_PW, elevate=True)

    def tearDown(self):
        self._ctx.__exit__(None, None, None)
        auth._FAILS.clear()

    def _login(self, username, password, *, elevate=False):
        client = _client()
        r = client.post("/login", data={"username": username, "password": password})
        self.assertEqual(r.status_code, 303, r.headers.get("location"))
        if elevate:
            self.assertEqual(client.post("/api/admin/elevate", json={"password": password}).status_code, 200)
        return client

    def _ops_audit(self):
        return [e for e in self.store.audit if e.target_type == "ops_service"]

    # ── 成功路徑 ─────────────────────────────────────────────────────
    def test_restart_web_is_202_scheduled_and_audited(self):
        with FakeOpsAgent() as agent, agent.installed():
            r = self.operator.post("/api/admin/ops/services/web/restart")
        self.assertEqual(r.status_code, 202, r.text)
        body = r.json()
        self.assertEqual((body["action"], body["state"], body["execute_after_ms"]), ("restart", "scheduled", 1500))
        self.assertEqual(body["previous_invocation_id"], "0f1e2d3c")
        req = agent.requests[0]
        self.assertEqual((req["op"], req["service"], req["env"], req["actor"]),
                         ("restart", "web", "production", "operator"))
        self.assertNotIn("params", req, "寫入類不送任何參數")
        (entry,) = self._ops_audit()
        self.assertEqual((entry.action, entry.target_id, entry.actor_user_id), ("ops.restart", "web", self.operator_id))
        self.assertEqual(entry.detail, {"environment": "production", "service": "web",
                                        "target": "report-mark-web.service", "result": "scheduled",
                                        "previous_invocation_id": "0f1e2d3c"})

    def test_run_is_202_queued_and_audited(self):
        def handler(req):
            return ok(req, action_result(req)) if req["op"] == "run" else default_handler(req)

        with FakeOpsAgent(handler) as agent, agent.installed():
            r = self.operator.post("/api/admin/ops/services/sync/run")
        self.assertEqual(r.status_code, 202, r.text)
        self.assertEqual((r.json()["action"], r.json()["state"]), ("run", "queued"))
        self.assertEqual((agent.requests[0]["op"], agent.requests[0]["service"]), ("run", "sync"))
        (entry,) = self._ops_audit()
        self.assertEqual((entry.action, entry.target_id, entry.detail["result"]), ("ops.run", "sync", "queued"))

    def test_body_and_query_are_not_forwarded(self):
        def handler(req):
            return ok(req, action_result(req))

        with FakeOpsAgent(handler) as agent, agent.installed():
            r = self.operator.post("/api/admin/ops/services/sync/run?unit=sshd.service&args=--all",
                                   json={"unit": "sshd.service", "params": {"x": 1}})
        self.assertEqual(r.status_code, 202, r.text)
        self.assertEqual(set(agent.requests[0]), {"v", "id", "env", "op", "service", "actor"})

    # ── 代理的拒絕 → 穩定的 HTTP 錯誤，且都寫稽核 ─────────────────────
    def test_agent_refusals_map_to_http_and_are_audited(self):
        mapping = {
            "already_running": (409, "already_running"),
            "action_not_allowed": (403, "action_not_allowed"),
            "unknown_service": (404, "ops_service_not_found"),
            "lock_unavailable": (503, "ops_lock_unavailable"),
            "command_failed": (502, "ops_command_failed"),
            "command_timeout": (504, "ops_timeout"),
            "busy": (503, "ops_agent_busy"),
            "invalid_params": (400, "invalid_params"),
            "internal_error": (502, "ops_agent_error"),
        }
        for code, expected in mapping.items():
            with self.subTest(code=code), FakeOpsAgent(lambda req, c=code: err(req, c, "代理說不行：細節")) as agent, \
                    agent.installed():
                r = self.operator.post("/api/admin/ops/services/sync/run")
                self.assertEqual((r.status_code, r.json()["code"]), expected)
                self.assertEqual(r.json()["detail"], "代理說不行：細節")
                entry = self._ops_audit()[0]
                self.assertEqual((entry.action, entry.detail["result"]), ("ops.run", code))
                self.assertNotIn("代理說不行", repr(entry.detail), "稽核不收代理的訊息全文")
        self.assertEqual(len(self._ops_audit()), len(mapping))

    def test_postgres_nginx_cloudflared_restart_refused(self):
        def handler(req):
            if req["op"] == "restart" and req["service"] != "web":
                return err(req, "action_not_allowed", f"服務 {req['service']} 永遠不允許 restart")
            return default_handler(req)

        with FakeOpsAgent(handler) as agent, agent.installed():
            for name in ("postgres", "nginx", "cloudflared"):
                with self.subTest(name=name):
                    r = self.operator.post(f"/api/admin/ops/services/{name}/restart")
                    self.assertEqual((r.status_code, r.json()["code"]), (403, "action_not_allowed"))
        self.assertEqual([e.detail["result"] for e in self._ops_audit()], ["action_not_allowed"] * 3)

    def test_unknown_service_is_404(self):
        with FakeOpsAgent() as agent, agent.installed():
            r = self.operator.post("/api/admin/ops/services/nope/restart")
        self.assertEqual((r.status_code, r.json()["code"]), (404, "ops_service_not_found"))

    def test_bad_names_rejected_before_agent(self):
        with FakeOpsAgent() as agent, agent.installed():
            for name in ("Web", "report-mark-web.service", "a" * 41, "web;reboot"):
                with self.subTest(name=name):
                    self.assertEqual(self.operator.post(f"/api/admin/ops/services/{name}/restart").status_code, 422)
        self.assertEqual(agent.requests, [])
        self.assertEqual(self._ops_audit(), [])

    def test_agent_unavailable_is_503_and_audited(self):
        missing = os.path.join(tempfile.gettempdir(), "rmops-missing", "agent.sock")
        client = ops_client.OpsClient(missing, "production", 1.0)
        with mock.patch.object(ops_client, "default_client", lambda: client):
            r = self.operator.post("/api/admin/ops/services/web/restart")
        self.assertEqual((r.status_code, r.json()["code"]), (503, "ops_agent_unavailable"))
        self.assertEqual(self._ops_audit()[0].detail["result"], "ops_agent_unavailable")
        self.assertEqual(self.operator.get("/api/me").status_code, 200)

    def test_malformed_success_is_502_and_audited_as_unverified(self):
        with FakeOpsAgent(lambda req: ok(req, {"unexpected": True})) as agent, agent.installed():
            r = self.operator.post("/api/admin/ops/services/web/restart")
        self.assertEqual((r.status_code, r.json()["code"]), (502, "ops_agent_error"))
        self.assertEqual(self._ops_audit()[0].detail["result"], "accepted_unverified")

    def test_audit_failure_does_not_hide_an_accepted_action(self):
        with FakeOpsAgent() as agent, agent.installed(), \
                mock.patch.object(self.store, "record_ops_action", side_effect=RuntimeError("db down")), \
                self.assertLogs("web.routers.admin_ops", "ERROR"):
            r = self.operator.post("/api/admin/ops/services/web/restart")
        self.assertEqual(r.status_code, 202, r.text)

    # ── dev／prod 交叉 ────────────────────────────────────────────────
    def test_development_web_cannot_drive_production_agent(self):
        def handler(req):
            return err(req, "environment_mismatch", "這個代理只服務 production", env="production")

        with FakeOpsAgent(handler) as agent, agent.installed(environment="development"):
            for path in ("/api/admin/ops/services/web/restart", "/api/admin/ops/services/sync/run"):
                with self.subTest(path=path):
                    r = self.operator.post(path)
                    self.assertEqual((r.status_code, r.json()["code"]), (503, "ops_agent_unavailable"))
        self.assertEqual([r["env"] for r in agent.requests], ["development", "development"])
        self.assertEqual({e.detail["environment"] for e in self._ops_audit()}, {"development"})

    def test_agent_answering_for_another_environment_is_not_trusted(self):
        with FakeOpsAgent(lambda req: {**ok(req, action_result(req)), "env": "development"}) as agent, \
                agent.installed():
            r = self.operator.post("/api/admin/ops/services/web/restart")
        self.assertEqual((r.status_code, r.json()["code"]), (503, "ops_agent_unavailable"))

    # ── 授權：scope 與 elevated，都在碰到代理之前 ──────────────────────
    def test_admin_without_ops_operate_gets_missing_scope(self):
        plain = self._login("plainadmin", PLAIN_ADMIN_PW, elevate=True)
        with FakeOpsAgent() as agent, agent.installed():
            for path in ("/api/admin/ops/services/web/restart", "/api/admin/ops/services/sync/run"):
                with self.subTest(path=path):
                    r = plain.post(path)
                    self.assertEqual((r.status_code, r.json()["code"]), (403, "missing_scope"))
            self.assertEqual(plain.get("/api/admin/ops/services").status_code, 200, "ops.read 照常")
        self.assertEqual([r["op"] for r in agent.requests], ["list"])
        self.assertEqual(self._ops_audit(), [])

    def test_operator_without_elevation_gets_elevation_required(self):
        not_elevated = self._login("operator", OPERATOR_PW)
        with FakeOpsAgent() as agent, agent.installed():
            for path in ("/api/admin/ops/services/web/restart", "/api/admin/ops/services/sync/run"):
                with self.subTest(path=path):
                    r = not_elevated.post(path)
                    self.assertEqual((r.status_code, r.json()["code"]), (403, "elevation_required"))
        self.assertEqual(agent.requests, [])
        self.assertEqual(self._ops_audit(), [])

    def test_plain_user_and_anonymous_are_refused(self):
        user = self._login("alice", USER_PW)
        with FakeOpsAgent() as agent, agent.installed():
            self.assertEqual(user.post("/api/admin/ops/services/web/restart").status_code, 403)
            self.assertEqual(_client().post("/api/admin/ops/services/web/restart").status_code, 401)
        self.assertEqual(agent.requests, [])

    def test_get_is_not_allowed_on_write_endpoints(self):
        with FakeOpsAgent() as agent, agent.installed():
            self.assertEqual(self.operator.get("/api/admin/ops/services/web/restart").status_code, 405)
        self.assertEqual(agent.requests, [])


class OpsClientActorTests(unittest.IsolatedAsyncioTestCase):
    async def test_actor_only_sent_when_it_matches_the_protocol(self):
        with FakeOpsAgent(lambda req: ok(req, action_result(req))) as agent:
            client = agent.client()
            await client.request("restart", "web", actor="王小明.ops")
            await client.request("restart", "web", actor="bad actor; rm -rf")
            await client.request("restart", "web")
        self.assertEqual([r.get("actor") for r in agent.requests], ["王小明.ops", None, None])


class FakeAccountsParityTests(unittest.TestCase):
    def test_fake_signature_matches_real(self):
        import inspect

        from app.services import accounts

        real = inspect.signature(accounts.record_ops_action)
        fake = inspect.signature(FakeAccounts.record_ops_action)
        self.assertEqual(list(real.parameters), [p for p in fake.parameters if p != "self"])


if __name__ == "__main__":
    unittest.main()
