"""管理清單的 CSV 匯出（/api/admin/export/*.csv，HTTP 層）與 `web/csv_export.py` 的格式規則。

驗：未登入 401、一般使用者 403、缺 scope 403 `missing_scope`；CSV 形狀（BOM、標頭列、檔名帶日期、no-store）；
公式注入防護；每次匯出寫一筆 `data.export` 稽核（篩選條件、筆數、是否達上限，不含內容）；稽核寫不進去就 503
不匯出；帳號匯出不含任何密碼／TOTP 衍生欄位；空資料只有標頭；筆數上限；沒有問答原文的匯出端點。
"""

from __future__ import annotations

import csv
import dataclasses
import io
import re
import unittest
from datetime import date, datetime, timedelta, timezone

from fake_accounts import FakeAccounts, install
from fastapi.testclient import TestClient

from app.services.visibility import AdminReportRow
from web import auth, csv_export, deps
from web.routers import admin_exports
from web.server import app

ADMIN_PW = "root-password-1"
USER_PW = "alice-password-1"
T0 = datetime(2026, 10, 6, 3, 0, tzinfo=timezone.utc)
PATHS = ("/api/admin/export/audit.csv", "/api/admin/export/users.csv", "/api/admin/export/reports.csv",
         "/api/admin/export/incidents.csv", "/api/admin/export/jobs.csv")


def _client():
    return TestClient(app, follow_redirects=False, base_url="http://127.0.0.1")


class _Store(FakeAccounts):
    """`limited` 是管理員但被拿掉匯出用到的四個 scope。"""

    def _user(self, row, elevated_until=None):
        user = super()._user(row, elevated_until)
        if user.username == "limited":
            return dataclasses.replace(
                user, scopes=user.scopes - {"audit.read", "accounts.manage", "reports.manage", "ops.read"})
        return user


class _Session:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeVisibility:
    def __init__(self):
        self.calls = []
        self.total = None
        self.rows = [
            AdminReportRow(report_id="r1", file_hash="a" * 64, file_name="=cmd|' /C calc'!A0.pdf",
                           title="+SUM(1,2)", source="@券商", market="TW", report_date=date(2026, 10, 1),
                           created_at=T0, hidden=True, reason="-重複上傳", updated_by="root", updated_at=T0),
            AdminReportRow(report_id="r2", file_hash="b" * 64, file_name="ok.pdf", title=None, source=None,
                           market=None, report_date=None, created_at=None, hidden=False, reason=None,
                           updated_by=None, updated_at=None),
        ]

    async def list_reports(self, session, **kw):
        self.calls.append(kw)
        rows = self.rows[: kw["limit"]]
        return (self.total if self.total is not None else len(self.rows)), rows


class _FakeMonitoring:
    def __init__(self):
        self.calls = []
        self.incidents: list[dict] = []
        self.total = None

    async def list_incidents(self, session, **kw):
        self.calls.append(("incidents", kw))
        return (self.total if self.total is not None else len(self.incidents)), self.incidents

    async def list_jobs(self, session, **kw):
        self.calls.append(("jobs", kw))
        return 1, [{"host": "office-host", "unit": "report-mark-sync.service", "service": "sync",
                    "invocation_id": "a" * 32, "state": "finished", "started_at": T0,
                    "finished_at": T0 + timedelta(seconds=90), "result": "exit-code", "exit_status": -1,
                    "exec_main_code": "exited", "last_seen_at": T0}]


def _rows(resp) -> list[list[str]]:
    text = resp.content.decode("utf-8")
    assert text.startswith("﻿"), "缺 UTF-8 BOM"
    return list(csv.reader(io.StringIO(text[1:])))


class _Base(unittest.TestCase):
    def setUp(self):
        auth._FAILS.clear()
        self.store = _Store()
        self.root_id = self.store.add_user("root", ADMIN_PW, "admin")
        self.store.add_user("alice", USER_PW, "user")
        self.store.add_user("limited", ADMIN_PW, "admin")
        self._ctx = install(self.store)
        self._ctx.__enter__()
        self.addCleanup(self._ctx.__exit__, None, None, None)
        self.vis = _FakeVisibility()
        self.mon = _FakeMonitoring()
        orig = (deps.report_visibility, deps.ops_monitoring, deps.SessionFactory)
        deps.report_visibility, deps.ops_monitoring, deps.SessionFactory = self.vis, self.mon, lambda: _Session()
        self.addCleanup(lambda: setattr(deps, "report_visibility", orig[0]))
        self.addCleanup(lambda: setattr(deps, "ops_monitoring", orig[1]))
        self.addCleanup(lambda: setattr(deps, "SessionFactory", orig[2]))

    def login(self, username="root", password=ADMIN_PW):
        client = _client()
        r = client.post("/login", data={"username": username, "password": password})
        self.assertEqual(r.status_code, 303)
        return client

    def exports(self):
        return [e for e in self.store.audit if e.action == "data.export"]


class AuthzTests(_Base):
    def test_gates(self):
        for path in PATHS:
            with self.subTest(path=path):
                self.assertEqual(_client().get(path).status_code, 401)
                self.assertEqual(self.login("alice", USER_PW).get(path).status_code, 403)
                r = self.login("limited").get(path)
                self.assertEqual((r.status_code, r.json()["code"]), (403, "missing_scope"))
        self.assertEqual(self.exports(), [], "被擋下的請求不得留下匯出稽核")

    def test_no_qa_content_export_exists(self):
        """問答原文絕不提供匯出：任何 export 路徑都不得指向 qa。"""
        exports = sorted(p for p in app.openapi()["paths"] if "/export" in p)
        self.assertEqual(exports, sorted(PATHS))
        self.assertFalse([p for p in exports if "qa" in p])


class ExportShapeTests(_Base):
    def test_audit_export_shape_and_audit_entry(self):
        client = self.login()
        self.store._audit(self.root_id, "report.hide", "a" * 64, {"file_name": "=evil.pdf", "has_reason": True},
                          "report")
        r = client.get("/api/admin/export/audit.csv", params={"limit": 5})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.headers["content-type"].startswith("text/csv"))
        self.assertEqual(r.headers["cache-control"], "no-store")
        self.assertRegex(r.headers["content-disposition"], r'^attachment; filename="report-mark-audit-\d{8}\.csv"$')
        rows = _rows(r)
        self.assertEqual(tuple(rows[0]), admin_exports.AUDIT_COLUMNS)
        hide = next(row for row in rows[1:] if row[4] == "report.hide")
        # detail 是 JSON 字串（開頭是 `{`，不需要前綴）；裡面的值不會被當成公式。
        self.assertTrue(hide[7].startswith("{"))
        self.assertEqual(r.headers["x-export-rows"], str(len(rows) - 1))
        entry = self.exports()[0]
        self.assertEqual((entry.actor_user_id, entry.target_type, entry.target_id), (self.root_id, "export", "audit"))
        self.assertEqual(entry.detail, {"kind": "audit", "format": "csv", "filters": {"limit": 5},
                                        "row_count": len(rows) - 1, "truncated": False})

    def test_every_export_writes_exactly_one_audit_row_without_content(self):
        client = self.login()
        self.mon.incidents = [{"incident_id": "office-host:web:1", "host": "office-host", "component": "web",
                               "kind": "service", "probe_unit": None, "status": "firing", "severity": "CRITICAL",
                               "reason": "probe_exit_1", "summary": "=HYPERLINK(\"http://x\")", "opened_at": T0,
                               "last_event_at": T0, "resolved_at": None, "event_count": 1}]
        for path in PATHS:
            self.assertEqual(client.get(path).status_code, 200, path)
        kinds = [e.target_id for e in self.exports()]
        self.assertEqual(sorted(kinds), ["audit", "incidents", "jobs", "reports", "users"])
        blob = repr([e.detail for e in self.exports()])
        for content in ("HYPERLINK", "SUM(1,2)", "重複上傳", "calc", "ADMIN_PW", ADMIN_PW):
            self.assertNotIn(content, blob)

    def test_formula_injection_is_neutralised(self):
        client = self.login()
        rows = _rows(client.get("/api/admin/export/reports.csv", params={"q": "=x", "hidden": "true"}))
        header, first = rows[0], dict(zip(rows[0], rows[1]))
        self.assertEqual(tuple(header), admin_exports.REPORT_COLUMNS)
        self.assertEqual(first["file_name"], "'=cmd|' /C calc'!A0.pdf")
        self.assertEqual(first["title"], "'+SUM(1,2)")
        self.assertEqual(first["source"], "'@券商")
        self.assertEqual(first["hidden_reason"], "'-重複上傳")
        self.assertEqual(first["hidden"], "true")
        self.assertEqual(self.vis.calls[-1], {"q": "=x", "hidden": True, "limit": admin_exports.EXPORT_MAX_ROWS,
                                              "offset": 0})
        # 篩選條件進稽核（字串照記；它是條件不是內容）。
        self.assertEqual(self.exports()[-1].detail["filters"],
                         {"q": "=x", "hidden": True, "limit": admin_exports.EXPORT_MAX_ROWS})
        # 數字型別不加前綴：負的 exit status 仍是 -1。
        jobs = _rows(client.get("/api/admin/export/jobs.csv"))
        job = dict(zip(jobs[0], jobs[1]))
        self.assertEqual(job["exit_status"], "-1")
        self.assertEqual(job["duration_seconds"], "90.0")

    def test_user_export_has_no_password_or_totp_material(self):
        other = self.store.add_user("=HYPERLINK(1)", "super-secret-password-9", "user")
        self.store.users[other].totp_secret = "JBSWY3DPEHPK3PXP"
        self.store.users[other].totp_enabled = True
        r = self.login().get("/api/admin/export/users.csv")
        self.assertEqual(r.status_code, 200)
        rows = _rows(r)
        self.assertEqual(tuple(rows[0]), admin_exports.USER_COLUMNS)
        for col in rows[0]:
            self.assertIsNone(re.search(r"password|secret|hash|fingerprint|totp_last|mfa", col), col)
        text = r.content.decode("utf-8")
        for secret in ("super-secret-password-9", ADMIN_PW, USER_PW, "JBSWY3DPEHPK3PXP", "$argon2"):
            self.assertNotIn(secret, text)
        names = {row[1] for row in rows[1:]}
        self.assertIn("'=HYPERLINK(1)", names)
        mfa = next(dict(zip(rows[0], row)) for row in rows[1:] if row[1] == "'=HYPERLINK(1)")
        self.assertEqual(mfa["totp_enabled"], "true")

    def test_empty_export_is_header_only(self):
        r = self.login().get("/api/admin/export/incidents.csv", params={"status": "firing"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(_rows(r), [list(admin_exports.INCIDENT_COLUMNS)])
        self.assertEqual(r.headers["x-export-rows"], "0")
        detail = self.exports()[-1].detail
        self.assertEqual((detail["row_count"], detail["truncated"]), (0, False))
        self.assertEqual(detail["filters"]["status"], "firing")
        self.assertIn("since", detail["filters"])

    def test_limits(self):
        client = self.login()
        self.assertEqual(client.get("/api/admin/export/audit.csv",
                                    params={"limit": admin_exports.EXPORT_MAX_ROWS + 1}).status_code, 422)
        self.vis.total = 5000
        r = client.get("/api/admin/export/reports.csv", params={"limit": 1})
        self.assertEqual(r.headers["x-export-truncated"], "true")
        self.assertEqual(len(_rows(r)) - 1, 1)
        self.assertTrue(self.exports()[-1].detail["truncated"])
        r = client.get("/api/admin/export/jobs.csv",
                       params={"since": "2026-10-06T00:00:00Z", "until": "2026-10-01T00:00:00Z"})
        self.assertEqual((r.status_code, r.json()["code"]), (400, "invalid_params"))

    def test_audit_failure_blocks_export(self):
        client = self.login()

        async def boom(**_kw):
            raise RuntimeError("db down")

        self.store.record_export = boom
        r = client.get("/api/admin/export/users.csv")
        self.assertEqual((r.status_code, r.json()["code"]), (503, "export_audit_failed"))
        self.assertNotIn("root", r.text)


class CsvFormatTests(unittest.TestCase):
    def test_safe_cell(self):
        for prefix in csv_export.FORMULA_PREFIXES:
            with self.subTest(prefix=repr(prefix)):
                self.assertEqual(csv_export.safe_cell(prefix + "x"), "'" + prefix + "x")
        self.assertEqual(csv_export.safe_cell("正常"), "正常")
        self.assertEqual(csv_export.safe_cell(None), "")
        self.assertEqual(csv_export.safe_cell(False), "false")
        self.assertEqual(csv_export.safe_cell(-3), "-3")
        self.assertEqual(csv_export.safe_cell(-0.5), "-0.5")
        self.assertEqual(csv_export.safe_cell({"b": 1, "a": "=x"}), '{"a": "=x", "b": 1}')
        self.assertEqual(csv_export.safe_cell(["-x"]), '["-x"]')

    def test_filename_uses_taipei_date(self):
        self.assertEqual(csv_export.filename("audit", datetime(2026, 10, 5, 17, 0, tzinfo=timezone.utc)),
                         "report-mark-audit-20261006.csv")

    def test_large_output_is_chunked(self):
        rows = [{"a": i} for i in range(1200)]
        chunks = list(csv_export._encode(["a"], rows))
        self.assertGreater(len(chunks), 1)
        text = b"".join(chunks).decode("utf-8")
        self.assertEqual(text.count("\r\n"), 1201)


if __name__ == "__main__":
    unittest.main()
