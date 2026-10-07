"""`/api/admin/security/*` 的 HTTP 層：授權、回應形狀、session 撤銷（已提升＋同交易稽核）、詞彙與後端逐字一致。

不連 DB：帳號與 session 用 `tests/fake_accounts.py`；`security_ops` 的查詢函式以假值替換（SQL 本身在
tests/test_security_ops_db.py）。錨定狀態檔寫 tempfile（`DATA_HEALTH_DIR`）。
"""

from __future__ import annotations

import os
import tempfile
import typing
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from fake_accounts import FakeAccounts, install
from fastapi.testclient import TestClient

from app.services import accounts, security_ops
from app.services.security_ops import AlertEvaluation, AuditChainSnapshot, AuthEventRow, TimelineEntry
from web import auth
from web.routers import admin_security
from web.server import app

PW = "root-password-1"
NOW = datetime(2026, 10, 7, 9, 0, tzinfo=timezone.utc)


def _client():
    return TestClient(app, follow_redirects=False, base_url="http://127.0.0.1")


class _Base(unittest.TestCase):
    def setUp(self):
        auth._FAILS.clear()
        self.store = FakeAccounts()
        self.boss = self.store.add_user("boss", PW, "admin", is_super=True)
        self.root = self.store.add_user("root", PW, "admin")
        self.alice = self.store.add_user("alice", PW, "user")
        self._ctx = install(self.store)
        self._ctx.__enter__()
        self.admin = self._login("root")

    def tearDown(self):
        self._ctx.__exit__(None, None, None)
        auth._FAILS.clear()

    def _login(self, name):
        c = _client()
        r = c.post("/login", data={"username": name, "password": PW})
        self.assertEqual(r.status_code, 303, r.headers.get("location"))
        return c


GET_PATHS = (
    "/api/admin/security/alerts", "/api/admin/security/events", "/api/admin/security/suspicious-ips",
    "/api/admin/security/sessions", "/api/admin/security/high-risk", "/api/admin/security/audit-chain",
    "/api/admin/security/totp-adoption",
)


class AuthorizationTests(_Base):
    def test_unauthenticated_is_401_and_user_is_403(self):
        user = self._login("alice")
        for path in GET_PATHS:
            with self.subTest(path=path):
                self.assertEqual(_client().get(path).status_code, 401)
                self.assertEqual(user.get(path).status_code, 403)
        sid = next(iter(self.store.sessions))
        self.assertEqual(user.post(f"/api/admin/security/sessions/{sid}/revoke").status_code, 403)


class VocabularyTests(unittest.TestCase):
    def test_event_types_match_backend(self):
        self.assertEqual(set(typing.get_args(admin_security.AuthEventType)), set(accounts.AUTH_EVENT_TYPES))

    def test_categories_match_backend(self):
        self.assertEqual(typing.get_args(admin_security.HighRiskCategory), security_ops.HIGH_RISK_CATEGORIES)

    def test_states_match_backend(self):
        self.assertEqual(set(typing.get_args(admin_security.SecurityState)), set(security_ops.HEALTH_STATES))


class EventsTests(_Base):
    def test_shape_filters_and_paging(self):
        rows = [AuthEventRow(id=10 - i, occurred_at=NOW, event="login.failure", reason="bad_password",
                             user_id=self.alice, username="alice", session_id=None, ip="10.0.0.1",
                             user_agent="UA", count=1) for i in range(2)]
        fake = mock.AsyncMock(return_value=rows)
        with mock.patch.object(security_ops, "list_auth_events", fake):
            r = self.admin.get("/api/admin/security/events", params={
                "event": "login.failure", "user_id": self.alice, "ip": "10.0.0.1", "limit": 2,
                "since": "2026-10-01T00:00:00+00:00",
            })
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["next_before_id"], 9)
        self.assertEqual(body["items"][0]["username"], "alice")
        kw = fake.call_args.kwargs
        self.assertEqual((kw["event"], kw["user_id"], kw["ip"], kw["limit"]),
                         ("login.failure", self.alice, "10.0.0.1", 2))
        self.assertEqual(kw["since"].year, 2026)

    def test_last_page_has_no_cursor_and_bad_input_is_rejected(self):
        with mock.patch.object(security_ops, "list_auth_events", mock.AsyncMock(return_value=[])):
            self.assertIsNone(self.admin.get("/api/admin/security/events").json()["next_before_id"])
        r = self.admin.get("/api/admin/security/events", params={"user_id": "nope"})
        self.assertEqual((r.status_code, r.json()["code"]), (400, "invalid_input"))
        self.assertEqual(self.admin.get("/api/admin/security/events", params={"event": "x"}).status_code, 422)


class SuspiciousIpTests(_Base):
    def test_shape(self):
        row = security_ops.SuspiciousIp(ip="1.2.3.4", failures=7, locked=40, insecure=0, successes=0,
                                        distinct_users=2, first_seen=NOW, last_seen=NOW)
        fake = mock.AsyncMock(return_value=[row])
        with mock.patch.object(security_ops, "suspicious_ips", fake):
            r = self.admin.get("/api/admin/security/suspicious-ips", params={"hours": 6, "min_failures": 3})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["items"][0]["locked"], 40)
        self.assertEqual(fake.call_args.kwargs, {"hours": 6, "min_failures": 3})


class SessionTests(_Base):
    def _sid_of(self, user_id):
        return next(s.id for s in self.store.sessions.values() if s.user_id == user_id and not s.revoked)

    def test_list_marks_current_session(self):
        self._login("alice")
        r = self.admin.get("/api/admin/security/sessions")
        self.assertEqual(r.status_code, 200, r.text)
        items = r.json()["items"]
        self.assertEqual({i["username"] for i in items}, {"root", "alice"})
        self.assertEqual([i["username"] for i in items if i["current"]], ["root"])
        only = self.admin.get("/api/admin/security/sessions", params={"user_id": self.alice}).json()["items"]
        self.assertEqual({i["username"] for i in only}, {"alice"})

    def test_revoke_needs_elevation_then_is_audited_and_immediate(self):
        alice = self._login("alice")
        sid = self._sid_of(self.alice)
        r = self.admin.post(f"/api/admin/security/sessions/{sid}/revoke")
        self.assertEqual((r.status_code, r.json()["code"]), (403, "elevation_required"))
        self.assertEqual(alice.get("/api/me").status_code, 200)
        self.assertEqual(self.admin.post("/api/admin/elevate", json={"password": PW}).status_code, 200)
        r = self.admin.post(f"/api/admin/security/sessions/{sid}/revoke")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertFalse(r.json()["active"])
        self.assertEqual(alice.get("/api/me").status_code, 401, "撤銷下一個請求就生效")
        revokes = [e for e in self.store.audit if e.action == "session.admin_revoke"]
        self.assertEqual(len(revokes), 1)
        self.assertEqual(revokes[0].target_id, sid)
        self.assertNotIn("ip", revokes[0].detail)
        # 已撤銷：原樣回傳、不再寫稽核
        self.assertEqual(self.admin.post(f"/api/admin/security/sessions/{sid}/revoke").status_code, 200)
        self.assertEqual(len([e for e in self.store.audit if e.action == "session.admin_revoke"]), 1)

    def test_revoke_errors(self):
        self._login("boss")
        self.assertEqual(self.admin.post("/api/admin/elevate", json={"password": PW}).status_code, 200)
        r = self.admin.post(f"/api/admin/security/sessions/{self._sid_of(self.boss)}/revoke")
        self.assertEqual((r.status_code, r.json()["code"]), (403, "super_required"))
        r = self.admin.post("/api/admin/security/sessions/00000000-0000-4000-8000-000000000000/revoke")
        self.assertEqual((r.status_code, r.json()["code"]), (404, "not_found"))
        r = self.admin.post("/api/admin/security/sessions/not-a-uuid/revoke")
        self.assertEqual((r.status_code, r.json()["code"]), (400, "invalid_input"))


class HighRiskTests(_Base):
    def test_shape_and_category_filter(self):
        entry = TimelineEntry(id=5, category="privilege", action="user.set_privileges", actor_user_id=self.boss,
                              actor_username="boss", target_type="user", target_id=self.root,
                              detail={"is_super": True}, created_at=NOW)
        fake = mock.AsyncMock(return_value=[entry])
        with mock.patch.object(security_ops, "high_risk_timeline", fake):
            r = self.admin.get("/api/admin/security/high-risk", params={"category": "privilege", "limit": 1})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual((body["items"][0]["category"], body["next_before_id"]), ("privilege", 5))
        self.assertEqual(fake.call_args.kwargs["category"], "privilege")
        self.assertEqual(self.admin.get("/api/admin/security/high-risk", params={"category": "x"}).status_code, 422)


class AuditChainTests(_Base):
    def setUp(self):
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        p = mock.patch.dict(os.environ, {"DATA_HEALTH_DIR": str(Path(self._tmp.name) / "health")})
        p.start()
        self.addCleanup(p.stop)

    def test_live_and_missing_anchor(self):
        r = self.admin.get("/api/admin/security/audit-chain")
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["live"]["state"], "ok")
        self.assertEqual(body["live"]["cache_seconds"], 3600)
        self.assertEqual(body["anchor"]["state"], "missing")

    def test_broken_chain_and_recent_anchor(self):
        security_ops.write_anchor_status({"result": "ok", "at": NOW.isoformat(), "head_id": 3, "total": 3,
                                          "anchors_checked": 1})
        self.store.broken_audit_ids.add(1)
        self.store._audit(None, "user.create", "x", {}, "user")
        body = self.admin.get("/api/admin/security/audit-chain").json()
        self.assertEqual(body["live"]["state"], "broken")
        self.assertEqual((body["anchor"]["state"], body["anchor"]["head_id"]), ("ok", 3))


class TotpAdoptionTests(_Base):
    def test_shape(self):
        res = security_ops.TotpAdoption(users_total=10, users_enabled=4, admins_total=3, admins_enabled=1,
                                        admins_without_totp=((self.root, "root", False), (self.boss, "boss", True)))
        with mock.patch.object(security_ops, "totp_adoption", mock.AsyncMock(return_value=res)):
            body = self.admin.get("/api/admin/security/totp-adoption").json()
        self.assertEqual((body["admins_total"], body["admins_enabled"], body["policy_required"]), (3, 1, False))
        self.assertEqual([a["username"] for a in body["admins_without_totp"]], ["root", "boss"])


class AlertsTests(_Base):
    def test_shape_with_account_names(self):
        ev = AlertEvaluation(window_minutes=15, login_failures=25, login_failure_threshold=20,
                             accounts_over=(self.alice,), max_account_failures=6, account_failure_threshold=5,
                             elevate_failures=0, elevate_failure_threshold=3,
                             audit_chain=AuditChainSnapshot(state="ok"))
        with mock.patch.object(security_ops, "evaluate_alerts", mock.AsyncMock(return_value=ev)), \
                mock.patch.object(security_ops, "usernames", mock.AsyncMock(return_value={self.alice: "alice"})):
            body = self.admin.get("/api/admin/security/alerts").json()
        self.assertEqual(body["state"], "account_failures")
        self.assertEqual(body["triggered"], ["account_failures", "login_failures"])
        self.assertEqual(body["accounts_over"], [{"user_id": self.alice, "username": "alice"}])


if __name__ == "__main__":
    unittest.main()
