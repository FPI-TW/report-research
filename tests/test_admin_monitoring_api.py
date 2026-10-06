"""監控投影的唯讀 API（/api/admin/jobs、/api/admin/observations、/api/admin/incidents*，HTTP 層）。

SQL 對真 DB 的驗證在 tests/test_ops_monitoring_db.py；這裡驗：一般使用者 403、未登入 401、沒有 `ops.read`
的管理員 403 `missing_scope`；參數原樣轉給服務層（`deps.ops_monitoring`）；預設時間範圍；時間範圍顛倒或
超過上限 400、筆數超過 422；回應形狀（時間轉 ISO、批次耗時、翻頁）。事件：清單的預設 30 天窗期與篩選、
事件耗時、詳情的轉換順序與 journal 片段、不存在 404、id 格式不合 422。
"""

from __future__ import annotations

import dataclasses
import unittest
from datetime import datetime, timedelta, timezone

from fake_accounts import FakeAccounts, install
from fastapi.testclient import TestClient

from web import auth, deps
from web.server import app

ADMIN_PW = "root-password-1"
USER_PW = "alice-password-1"
T0 = datetime(2026, 10, 6, 3, 0, tzinfo=timezone.utc)


def _client():
    return TestClient(app, follow_redirects=False, base_url="http://127.0.0.1")


class _NoOpsRead(FakeAccounts):
    """`limited` 是管理員但被拿掉 ops.read（其他 scope 照常）。"""

    def _user(self, row, elevated_until=None):
        user = super()._user(row, elevated_until)
        if user.username == "limited":
            return dataclasses.replace(user, scopes=user.scopes - {"ops.read"})
        return user


class _Session:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeMonitoring:
    def __init__(self):
        self.calls: list = []

    async def list_jobs(self, session, **kw):
        self.calls.append(("jobs", kw))
        return 3, [
            {"host": "office-host", "unit": "report-mark-sync.service", "service": "sync",
             "invocation_id": "a" * 32, "state": "finished", "started_at": T0,
             "finished_at": T0 + timedelta(seconds=90), "result": "success", "exit_status": 0,
             "exec_main_code": "exited", "last_seen_at": T0 + timedelta(seconds=120)},
            {"host": "office-host", "unit": "report-mark-backup.service", "service": "backup",
             "invocation_id": "b" * 32, "state": "lost", "started_at": T0, "finished_at": None,
             "result": None, "exit_status": None, "exec_main_code": None, "last_seen_at": T0},
        ]

    async def list_incidents(self, session, **kw):
        self.calls.append(("incidents", kw))
        return 2, [
            {"incident_id": "office-host:web:1791252000", "host": "office-host", "component": "web",
             "kind": "service", "probe_unit": "report-mark-health.service", "status": "resolved",
             "severity": "CRITICAL", "reason": "probe_exit_1", "summary": "探針回報失敗", "opened_at": T0,
             "last_event_at": T0 + timedelta(minutes=10), "resolved_at": T0 + timedelta(minutes=10),
             "event_count": 2},
            {"incident_id": "office-host:monitor:1791250000", "host": "office-host", "component": "monitor",
             "kind": "monitor_blind", "probe_unit": None, "status": "lost", "severity": "WARNING",
             "reason": "observation_missed", "summary": None, "opened_at": T0 - timedelta(hours=1),
             "last_event_at": T0 - timedelta(hours=1), "resolved_at": None, "event_count": 1},
        ]

    async def get_incident(self, session, incident_id):
        self.calls.append(("incident", incident_id))
        if incident_id.endswith(":404"):
            return None, [], False
        row = (await self.list_incidents(session))[1][0]
        self.calls.pop()
        return row, [
            {"event_id": f"{incident_id}:1:FIRING", "occurred_at": T0, "action": "FIRING", "severity": "CRITICAL",
             "reason": "probe_exit_1", "status": "web_incident", "summary": "探針回報失敗", "notified": True,
             "journal_excerpt": "2026-10-06T10:59:00+08:00 host web[1]: boom\n", "journal_truncated": True,
             "journal_since": T0 - timedelta(minutes=10), "journal_until": T0,
             "journal_units": "report-mark-health.service,report-mark-web.service"},
            {"event_id": f"{incident_id}:2:RESOLVED", "occurred_at": T0 + timedelta(minutes=10),
             "action": "RESOLVED", "severity": "RESOLVED", "reason": "healthy", "status": "ok", "summary": None,
             "notified": False, "journal_excerpt": None, "journal_truncated": False, "journal_since": None,
             "journal_until": None, "journal_units": None},
        ], True

    async def list_observations(self, session, **kw):
        self.calls.append(("observations", kw))
        if kw.get("resolution", "raw") != "raw":
            return False, [
                {"observed_at": T0, "host": "office-host", "scope": "host", "subject": "host", "metric": "cpu_pct",
                 "value": 4.75, "state": None, "detail": None, "sample_count": 4, "value_min": 1.0,
                 "value_max": 10.0, "value_last": 10.0, "first_state": None, "state_changes": 0},
            ]
        return True, [
            {"observed_at": T0, "host": "office-host", "scope": "service", "subject": "web",
             "metric": "active_state", "value": None, "state": "active", "detail": {"sub_state": "running"}},
            {"observed_at": T0, "host": "office-host", "scope": "host", "subject": "host",
             "metric": "cpu_pct", "value": 12.5, "state": None, "detail": None},
        ]


class AdminMonitoringApiTests(unittest.TestCase):
    def setUp(self):
        auth._FAILS.clear()
        self.store = _NoOpsRead()
        self.store.add_user("root", ADMIN_PW, "admin")
        self.store.add_user("alice", USER_PW, "user")
        self.store.add_user("limited", ADMIN_PW, "admin")
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
        for path in ("/api/admin/jobs", "/api/admin/observations", "/api/admin/incidents",
                     "/api/admin/incidents/office-host:web:1"):
            with self.subTest(path=path):
                self.assertEqual(user.get(path).status_code, 403)
                self.assertEqual(_client().get(path).status_code, 401)
        self.assertEqual(self.fake.calls, [])

    def test_admin_without_ops_read_gets_missing_scope(self):
        admin = self._login("limited", ADMIN_PW)
        for path in ("/api/admin/jobs", "/api/admin/observations", "/api/admin/incidents",
                     "/api/admin/incidents/office-host:web:1"):
            with self.subTest(path=path):
                r = admin.get(path)
                self.assertEqual(r.status_code, 403)
                self.assertEqual(r.json()["code"], "missing_scope")
        self.assertEqual(self.fake.calls, [])

    def test_jobs_pass_filters_compute_duration_and_page(self):
        r = self._login("root", ADMIN_PW).get("/api/admin/jobs", params={
            "service": "sync", "result": "exit-code", "state": "finished", "limit": 2,
            "since": "2026-10-01T00:00:00+08:00", "until": "2026-10-06T00:00:00+08:00",
        })
        self.assertEqual(r.status_code, 200, r.text)
        _, kw = self.fake.calls[0]
        self.assertEqual((kw["service"], kw["result"], kw["state"], kw["unit"], kw["limit"], kw["offset"]),
                         ("sync", "exit-code", "finished", None, 2, 0))
        self.assertEqual(kw["since"], datetime(2026, 9, 30, 16, tzinfo=timezone.utc))
        body = r.json()
        self.assertEqual((body["total"], body["has_more"], body["next_offset"]), (3, True, 2))
        self.assertEqual(body["items"][0]["duration_seconds"], 90.0)
        self.assertEqual(body["items"][0]["started_at"], T0.isoformat())
        self.assertEqual(body["items"][1]["state"], "lost")
        self.assertIsNone(body["items"][1]["duration_seconds"])

    def test_jobs_default_window_is_seven_days_ending_now(self):
        before = datetime.now(timezone.utc)
        r = self._login("root", ADMIN_PW).get("/api/admin/jobs")
        self.assertEqual(r.status_code, 200, r.text)
        _, kw = self.fake.calls[0]
        self.assertEqual(kw["until"] - kw["since"], timedelta(days=7))
        self.assertGreaterEqual(kw["until"], before)

    def test_jobs_reject_bad_params(self):
        admin = self._login("root", ADMIN_PW)
        cases = [
            ({"state": "done"}, 422),
            ({"limit": 201}, 422),
            ({"offset": 10001}, 422),
            ({"service": "Bad Name"}, 422),
            ({"unit": "-x.service"}, 422),
            ({"since": "2026-10-06T10:00:00+00:00", "until": "2026-10-06T09:00:00+00:00"}, 400),
            ({"since": "2026-01-01T00:00:00+00:00", "until": "2026-10-06T00:00:00+00:00"}, 400),
        ]
        for params, status in cases:
            with self.subTest(params=params):
                r = admin.get("/api/admin/jobs", params=params)
                self.assertEqual(r.status_code, status, r.text)
                if status == 400:
                    self.assertEqual(r.json()["code"], "invalid_params")
        self.assertEqual(self.fake.calls, [])

    def test_observations_shape_and_filters(self):
        r = self._login("root", ADMIN_PW).get("/api/admin/observations", params={
            "scope": "service", "subject": "web", "limit": 2})
        self.assertEqual(r.status_code, 200, r.text)
        _, kw = self.fake.calls[0]
        self.assertEqual((kw["scope"], kw["subject"], kw["metric"], kw["limit"]), ("service", "web", None, 2))
        self.assertEqual(kw["until"] - kw["since"], timedelta(hours=1), "預設最近一小時")
        body = r.json()
        self.assertTrue(body["truncated"])
        self.assertEqual(body["resolution"], "raw")
        self.assertIsNone(body["items"][0]["sample_count"], "原始觀測沒有聚合欄位")
        self.assertEqual(body["items"][0]["detail"], {"sub_state": "running"})
        self.assertEqual(body["items"][1]["value"], 12.5)
        self.assertEqual(body["items"][1]["observed_at"], T0.isoformat())

    def test_naive_time_is_treated_as_utc(self):
        r = self._login("root", ADMIN_PW).get("/api/admin/observations", params={
            "since": "2026-10-06T00:00:00", "until": "2026-10-06T01:00:00"})
        self.assertEqual(r.status_code, 200, r.text)
        _, kw = self.fake.calls[0]
        self.assertEqual(kw["since"], datetime(2026, 10, 6, tzinfo=timezone.utc))

    def test_observations_limits(self):
        admin = self._login("root", ADMIN_PW)
        self.assertEqual(admin.get("/api/admin/observations", params={"limit": 5001}).status_code, 422)
        self.assertEqual(admin.get("/api/admin/observations", params={"scope": "disk"}).status_code, 422)
        self.assertEqual(admin.get("/api/admin/observations", params={"metric": "CPU"}).status_code, 422)
        r = admin.get("/api/admin/observations", params={
            "since": "2026-06-01T00:00:00+00:00", "until": "2026-08-31T00:00:00+00:00"})
        self.assertEqual(r.status_code, 400, "範圍最多 90 天（＝保留期）")
        self.assertEqual(r.json()["code"], "invalid_params")
        self.assertEqual(self.fake.calls, [])

    def test_observations_pick_resolution_from_since(self):
        admin = self._login("root", ADMIN_PW)
        now = datetime.now(timezone.utc)
        for ago, want in ((None, "raw"), (timedelta(hours=23), "raw"), (timedelta(days=3), "5m"),
                          (timedelta(days=30), "1h"), (timedelta(days=89), "1h")):
            with self.subTest(ago=ago):
                self.fake.calls.clear()
                params = {} if ago is None else {"since": (now - ago).isoformat()}
                r = admin.get("/api/admin/observations", params=params)
                self.assertEqual(r.status_code, 200, r.text)
                self.assertEqual(r.json()["resolution"], want)
                self.assertEqual(self.fake.calls[0][1]["resolution"], want, "粒度原樣轉給服務層")
                item = r.json()["items"][0]
                if want != "raw":
                    self.assertEqual((item["value"], item["sample_count"], item["value_max"]), (4.75, 4, 10.0))


    def test_incidents_pass_filters_default_window_and_shape(self):
        before = datetime.now(timezone.utc)
        admin = self._login("root", ADMIN_PW)
        r = admin.get("/api/admin/incidents")
        self.assertEqual(r.status_code, 200, r.text)
        _, kw = self.fake.calls[0]
        self.assertEqual(kw["until"] - kw["since"], timedelta(days=30), "預設最近 30 天")
        self.assertGreaterEqual(kw["until"], before)
        self.assertEqual((kw["status"], kw["component"], kw["limit"], kw["offset"]), (None, None, 50, 0))
        body = r.json()
        self.assertEqual((body["total"], body["has_more"], body["next_offset"]), (2, False, None))
        first, second = body["items"]
        self.assertEqual((first["status"], first["duration_seconds"], first["opened_at"]),
                         ("resolved", 600.0, T0.isoformat()))
        self.assertEqual((second["status"], second["kind"], second["duration_seconds"]),
                         ("lost", "monitor_blind", None))
        r = admin.get("/api/admin/incidents", params={
            "status": "firing", "component": "container_monitor", "limit": 1, "offset": 1,
            "since": "2026-10-01T00:00:00+08:00", "until": "2026-10-06T00:00:00+08:00"})
        self.assertEqual(r.status_code, 200, r.text)
        _, kw = self.fake.calls[1]
        self.assertEqual((kw["status"], kw["component"], kw["limit"], kw["offset"]),
                         ("firing", "container_monitor", 1, 1))
        self.assertEqual(kw["since"], datetime(2026, 9, 30, 16, tzinfo=timezone.utc))
        self.assertEqual((r.json()["has_more"], r.json()["next_offset"]), (False, None))

    def test_incidents_reject_bad_params(self):
        admin = self._login("root", ADMIN_PW)
        cases = [
            ({"status": "open"}, 422),
            ({"limit": 201}, 422),
            ({"offset": 10001}, 422),
            ({"component": "web;drop"}, 422),
            ({"since": "2026-10-06T10:00:00+00:00", "until": "2026-10-06T09:00:00+00:00"}, 400),
            ({"since": "2025-01-01T00:00:00+00:00", "until": "2026-10-06T00:00:00+00:00"}, 400),
        ]
        for params, status in cases:
            with self.subTest(params=params):
                r = admin.get("/api/admin/incidents", params=params)
                self.assertEqual(r.status_code, status, r.text)
                if status == 400:
                    self.assertEqual(r.json()["code"], "invalid_params")
        self.assertEqual(self.fake.calls, [])

    def test_incident_detail_events_and_journal(self):
        r = self._login("root", ADMIN_PW).get("/api/admin/incidents/office-host:web:1791252000")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.fake.calls, [("incident", "office-host:web:1791252000")])
        body = r.json()
        self.assertEqual(body["status"], "resolved")
        self.assertTrue(body["events_truncated"])
        firing, resolved = body["events"]
        self.assertEqual((firing["action"], resolved["action"]), ("FIRING", "RESOLVED"))
        self.assertIn("boom", firing["journal_excerpt"])
        self.assertTrue(firing["journal_truncated"])
        self.assertEqual(firing["journal_since"], (T0 - timedelta(minutes=10)).isoformat())
        self.assertEqual(firing["journal_units"], "report-mark-health.service,report-mark-web.service")
        self.assertIsNone(resolved["journal_excerpt"])
        self.assertIsNone(resolved["journal_since"])

    def test_incident_detail_404_and_bad_id(self):
        admin = self._login("root", ADMIN_PW)
        r = admin.get("/api/admin/incidents/office-host:web:404")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json()["code"], "not_found")
        self.assertEqual(admin.get("/api/admin/incidents/bad%20id").status_code, 422)
        self.assertEqual(admin.get("/api/admin/incidents/" + "a" * 301).status_code, 422)


if __name__ == "__main__":
    unittest.main()
