"""監控投影的唯讀 API（/api/admin/incidents*、/api/admin/jobs、/api/admin/observations，HTTP 層）。

SQL 對真 DB 的驗證在 tests/test_ops_monitoring_db.py；這裡驗：一般使用者 403、未登入 401、參數原樣轉給
服務層（`deps.ops_monitoring`）、時間範圍顛倒 400、找不到事件 404、回應形狀（時間轉 ISO、批次的耗時）。
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from fake_accounts import FakeAccounts, install
from fastapi.testclient import TestClient

from web import auth, deps
from web.server import app

ADMIN_PW = "root-password-1"
USER_PW = "alice-password-1"
T0 = datetime(2026, 10, 6, 3, 0, tzinfo=timezone.utc)
INCIDENT_ID = "office-host:web:1791190800"


def _client():
    return TestClient(app, follow_redirects=False, base_url="http://127.0.0.1")


class _Session:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _incident(**kw):
    row = {
        "incident_id": INCIDENT_ID, "host": "office-host", "component": "web", "kind": "service",
        "status": "resolved", "severity": "CRITICAL", "reason": "probe_exit_1", "summary": "探針回報失敗",
        "opened_at": T0, "last_event_at": T0 + timedelta(minutes=10), "resolved_at": T0 + timedelta(minutes=10),
        "event_count": 2,
    }
    row.update(kw)
    return row


class _FakeMonitoring:
    def __init__(self):
        self.calls: list = []
        self.incident = _incident()

    async def list_incidents(self, session, **kw):
        self.calls.append(("incidents", kw))
        return 7, [self.incident]

    async def get_incident(self, session, incident_id):
        self.calls.append(("incident", incident_id))
        if incident_id != INCIDENT_ID:
            return None, []
        return self.incident, [
            {"event_id": f"{INCIDENT_ID}:1791190800:FIRING", "occurred_at": T0, "action": "FIRING",
             "severity": "CRITICAL", "reason": "probe_exit_1", "status": "web_incident", "summary": "探針回報失敗",
             "notified": True, "journal_excerpt": "line1\nline2", "journal_truncated": False},
            {"event_id": f"{INCIDENT_ID}:1791191400:RESOLVED", "occurred_at": T0 + timedelta(minutes=10),
             "action": "RESOLVED", "severity": "RESOLVED", "reason": "healthy", "status": "ok",
             "summary": "已恢復", "notified": False, "journal_excerpt": None, "journal_truncated": False},
        ]

    async def list_jobs(self, session, **kw):
        self.calls.append(("jobs", kw))
        return 2, [
            {"host": "office-host", "unit": "report-mark-sync.service", "service": "sync",
             "invocation_id": "a" * 32, "state": "finished", "started_at": T0, "finished_at": T0 + timedelta(seconds=90),
             "result": "success", "exit_status": 0, "exec_main_code": "exited"},
            {"host": "office-host", "unit": "report-mark-backup.service", "service": "backup",
             "invocation_id": "b" * 32, "state": "running", "started_at": T0, "finished_at": None,
             "result": None, "exit_status": None, "exec_main_code": None},
        ]

    async def list_observations(self, session, **kw):
        self.calls.append(("observations", kw))
        since = kw["since"] or T0 - timedelta(hours=1)
        until = kw["until"] or T0
        return since, until, True, [
            {"observed_at": T0, "host": "office-host", "scope": "service", "subject": "web",
             "metric": "active_state", "value": None, "state": "active", "detail": {"sub_state": "running"}},
            {"observed_at": T0, "host": "office-host", "scope": "host", "subject": "host",
             "metric": "cpu_pct", "value": 12.5, "state": None, "detail": None},
        ]


class AdminMonitoringApiTests(unittest.TestCase):
    def setUp(self):
        auth._FAILS.clear()
        self.store = FakeAccounts()
        self.store.add_user("root", ADMIN_PW, "admin")
        self.store.add_user("alice", USER_PW, "user")
        self._ctx = install(self.store)
        self._ctx.__enter__()
        self.fake = _FakeMonitoring()
        self._orig = (deps.ops_monitoring, deps.SessionFactory)
        deps.ops_monitoring = self.fake
        deps.SessionFactory = lambda: _Session()

    def tearDown(self):
        deps.ops_monitoring, deps.SessionFactory = self._orig
        self._ctx.__exit__(None, None, None)
        auth._FAILS.clear()

    def _login(self, username, password):
        client = _client()
        r = client.post("/login", data={"username": username, "password": password})
        self.assertEqual(r.status_code, 303)
        return client

    def test_user_gets_403_and_unauthenticated_401(self):
        user = self._login("alice", USER_PW)
        for path in ("/api/admin/incidents", f"/api/admin/incidents/{INCIDENT_ID}", "/api/admin/jobs",
                     "/api/admin/observations"):
            with self.subTest(path=path):
                self.assertEqual(user.get(path).status_code, 403)
                self.assertEqual(_client().get(path).status_code, 401)
        self.assertEqual(self.fake.calls, [])

    def test_incident_list_passes_filters_and_pages(self):
        r = self._login("root", ADMIN_PW).get("/api/admin/incidents", params={
            "status": "firing", "component": "web", "since": "2026-10-06T00:00:00+00:00", "limit": 1,
        })
        self.assertEqual(r.status_code, 200, r.text)
        _, kw = self.fake.calls[0]
        self.assertEqual((kw["status"], kw["component"], kw["limit"], kw["offset"]), ("firing", "web", 1, 0))
        self.assertEqual(kw["since"], datetime(2026, 10, 6, tzinfo=timezone.utc))
        self.assertIsNone(kw["until"])
        body = r.json()
        self.assertEqual((body["total"], body["has_more"], body["next_offset"]), (7, True, 1))
        self.assertEqual(body["items"][0]["opened_at"], T0.isoformat())
        self.assertEqual(body["items"][0]["kind"], "service")

    def test_incident_list_rejects_unknown_status_and_reversed_range(self):
        admin = self._login("root", ADMIN_PW)
        self.assertEqual(admin.get("/api/admin/incidents", params={"status": "open"}).status_code, 422)
        r = admin.get("/api/admin/incidents", params={
            "since": "2026-10-06T10:00:00+00:00", "until": "2026-10-06T09:00:00+00:00"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["code"], "invalid_params")

    def test_incident_detail_includes_events_and_journal(self):
        r = self._login("root", ADMIN_PW).get(f"/api/admin/incidents/{INCIDENT_ID}")
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual([e["action"] for e in body["events"]], ["FIRING", "RESOLVED"])
        self.assertEqual(body["events"][0]["journal_excerpt"], "line1\nline2")
        self.assertEqual(body["events"][1]["occurred_at"], (T0 + timedelta(minutes=10)).isoformat())

    def test_missing_incident_is_404_and_bad_id_is_422(self):
        admin = self._login("root", ADMIN_PW)
        r = admin.get("/api/admin/incidents/office-host:web:1")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json()["code"], "not_found")
        self.assertEqual(admin.get("/api/admin/incidents/bad%20id").status_code, 422)

    def test_jobs_compute_duration(self):
        r = self._login("root", ADMIN_PW).get("/api/admin/jobs", params={"service": "sync", "result": "success"})
        self.assertEqual(r.status_code, 200, r.text)
        _, kw = self.fake.calls[0]
        self.assertEqual((kw["service"], kw["result"], kw["unit"], kw["state"]), ("sync", "success", None, None))
        items = r.json()["items"]
        self.assertEqual(items[0]["duration_seconds"], 90.0)
        self.assertIsNone(items[1]["duration_seconds"])
        self.assertIsNone(items[1]["finished_at"])

    def test_observations_limit_is_capped_and_shape(self):
        admin = self._login("root", ADMIN_PW)
        self.assertEqual(admin.get("/api/admin/observations", params={"limit": 5001}).status_code, 422)
        r = admin.get("/api/admin/observations", params={"scope": "service", "subject": "web", "limit": 2})
        self.assertEqual(r.status_code, 200, r.text)
        _, kw = self.fake.calls[0]
        self.assertEqual((kw["scope"], kw["subject"], kw["metric"], kw["limit"]), ("service", "web", None, 2))
        body = r.json()
        self.assertTrue(body["truncated"])
        self.assertEqual(body["items"][0]["detail"], {"sub_state": "running"})
        self.assertEqual(body["items"][1]["value"], 12.5)


if __name__ == "__main__":
    unittest.main()
