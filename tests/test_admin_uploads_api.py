"""研報上傳的收檔與查詢 API（/api/admin/uploads*，HTTP 層）。

DB 那一層換成假物件（`deps.upload_intake`），隔離區是真的檔案系統（每題自己的 tempfile）：驗的是
授權、旗標、大小與字面檢查、檔案權限與命名、失敗時清檔、錯誤碼與統一錯誤格式、成功才 commit。
SQL、交易（稽核與 INSERT 同生共死）與 partial unique index 對真 DB 的驗證在 test_admin_uploads_db.py。
"""

from __future__ import annotations

import dataclasses
import hashlib
import os
import stat
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from fake_accounts import FakeAccounts, install
from fastapi.testclient import TestClient

from app import config
from app.services import quarantine, upload_intake
from app.services.upload_intake import ScannerSummary, UploadReport, UploadRow
from web import auth, deps
from web.server import app

ADMIN_PW = "root-password-1"
USER_PW = "alice-password-1"
NOSCOPE_PW = "bob-password-1"
WHEN = datetime(2026, 10, 6, 9, 30, tzinfo=timezone.utc)
PDF = b"%PDF-1.7\n" + b"0123456789" * 600 + b"\ntrailer\n%%EOF\n"
UPLOAD_ID = "33333333-3333-3333-3333-333333333333"


def _client():
    return TestClient(app, follow_redirects=False, base_url="http://127.0.0.1")


def _mode(path: Path) -> int:
    return stat.S_IMODE(os.lstat(path).st_mode)


class _Session:
    def __init__(self, log):
        self.log = log

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def commit(self):
        self.log.append("commit")

    async def rollback(self):
        self.log.append("rollback")


def _row(**over) -> UploadRow:
    base = dict(
        upload_id=UPLOAD_ID, file_hash="a" * 64, original_name="元大_台積電_20261001.pdf", size_bytes=len(PDF),
        client_mtime=WHEN, uploaded_by="root", uploaded_at=WHEN, state="quarantined", state_changed_at=WHEN,
        scan_attempts=0, scan_engine=None, scan_signature=None, scanned_at=None, scan_last_error=None,
        process_attempts=0, failure_kind=None, failure_detail=None, processed_at=None, decided_by=None,
        decided_at=None, decision_reason=None, purge_after=None, purged_at=None,
    )
    base.update(over)
    return UploadRow(**base)


class _FakeIntake:
    """deps.upload_intake 的替身：記下呼叫；`create_error`／`quota_error` 指定要拋的錯誤。"""

    def __init__(self, test):
        self.test = test
        self.calls: list = []
        self.quota_error: Exception | None = None
        self.create_error: Exception | None = None
        self.seen_files: list[Path] = []

    async def check_quota(self, session, *, actor_id, daily_quota, max_in_flight):
        self.calls.append(("quota", actor_id, daily_quota, max_in_flight))
        if self.quota_error is not None:
            raise self.quota_error

    async def create_upload(self, session, *, upload_id, file_hash, original_name, size_bytes, client_mtime,
                            actor_id, daily_quota, max_in_flight):
        path = quarantine.bin_path(self.test.root, upload_id)
        # 寫 DB 的當下，檔案必須已經 rename 成 .bin、內容與雜湊一致。
        self.test.assertTrue(path.exists())
        self.test.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), file_hash)
        self.seen_files.append(path)
        self.calls.append(("create", upload_id, file_hash, original_name, size_bytes, client_mtime, actor_id,
                           daily_quota, max_in_flight))
        if self.create_error is not None:
            raise self.create_error
        return _row(upload_id=upload_id, file_hash=file_hash, original_name=original_name, size_bytes=size_bytes,
                    client_mtime=client_mtime)

    async def list_uploads(self, session, *, state, limit, offset):
        self.calls.append(("list", state, limit, offset))
        return 3, [_row(), _row(upload_id="44444444-4444-4444-4444-444444444444", state="infected",
                                scan_signature="Eicar-Test-Signature")]

    async def scanner_summary(self, session):
        return ScannerSummary(pending=2, scanning=1, oldest_pending_at=WHEN, oldest_pending_seconds=600,
                              last_error="signatures_stale", last_error_at=WHEN)

    async def get_upload(self, session, upload_id):
        self.calls.append(("get", upload_id))
        if upload_id != UPLOAD_ID:
            raise upload_intake.UploadNotFoundError("上傳紀錄不存在")
        return _row(state="draft")

    async def get_upload_report(self, session, file_hash):
        return UploadReport(report_id="55555555-5555-5555-5555-555555555555", title="台積電", publication="draft",
                            hidden=False, extractor="pdfplumber", extraction_version="ext-v4", quality_score=0.9,
                            page_count=12, pages_failed=None, needs_review=False, created_at=WHEN)


class _ScopedAccounts(FakeAccounts):
    """`bob` 是管理員但被拿掉 reports.manage（真的庫裡管理員預設就有；這裡模擬未來 scope 可撤銷）。"""

    def _user(self, row, elevated_until=None):
        user = super()._user(row, elevated_until)
        if row.username == "bob":
            user = dataclasses.replace(user, scopes=user.scopes - {"reports.manage"})
        return user


class AdminUploadsApiTests(unittest.TestCase):
    def setUp(self):
        auth._FAILS.clear()
        self.store = _ScopedAccounts()
        self.root_id = self.store.add_user("root", ADMIN_PW, "admin")
        self.store.add_user("alice", USER_PW, "user")
        self.store.add_user("bob", NOSCOPE_PW, "admin")
        self._ctx = install(self.store)
        self._ctx.__enter__()
        self.fake = _FakeIntake(self)
        self.tx: list[str] = []
        self._orig = (deps.upload_intake, deps.SessionFactory)
        deps.upload_intake = self.fake
        deps.SessionFactory = lambda: _Session(self.tx)
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "quarantine"
        self.settings(upload_enabled=True)

    def tearDown(self):
        self._settings_patch.stop()
        deps.upload_intake, deps.SessionFactory = self._orig
        self._ctx.__exit__(None, None, None)
        self._tmp.cleanup()
        auth._FAILS.clear()

    def settings(self, **over):
        if getattr(self, "_settings_patch", None) is not None:
            self._settings_patch.stop()
        values = dict(upload_quarantine_dir=str(self.root), upload_max_bytes=1 << 20, upload_min_free_mb=0,
                      upload_daily_quota=30, upload_max_in_flight=50)
        values.update(over)
        self._settings_patch = mock.patch.object(config, "_SETTINGS",
                                                 dataclasses.replace(config.get_settings(), **values))
        self._settings_patch.start()

    def _login(self, username="root", password=ADMIN_PW):
        client = _client()
        r = client.post("/login", data={"username": username, "password": password})
        self.assertEqual(r.status_code, 303)
        return client

    def _post(self, client, body=PDF, *, filename="元大_台積電_20261001.pdf", params=None, headers=None):
        query = {"filename": filename, **(params or {})}
        hdrs = {"content-type": "application/pdf", **(headers or {})}
        return client.post("/api/admin/uploads", params=query, content=body, headers=hdrs)

    def _leftovers(self) -> list[str]:
        if not self.root.exists():
            return []
        return sorted(str(p.relative_to(self.root)) for p in self.root.rglob("*") if p.is_file())

    def _created(self):
        return [c for c in self.fake.calls if c[0] == "create"]

    # ── 授權 ────────────────────────────────────────────────────────────

    def test_unauthenticated_401(self):
        c = _client()
        self.assertEqual(self._post(c).status_code, 401)
        self.assertEqual(c.get("/api/admin/uploads").status_code, 401)
        self.assertEqual(c.get(f"/api/admin/uploads/{UPLOAD_ID}").status_code, 401)
        self.assertEqual(self._leftovers(), [])

    def test_user_403(self):
        c = self._login("alice", USER_PW)
        self.assertEqual(self._post(c).status_code, 403)
        self.assertEqual(c.get("/api/admin/uploads").status_code, 403)
        self.assertEqual(c.get(f"/api/admin/uploads/{UPLOAD_ID}").status_code, 403)
        self.assertEqual(self.fake.calls, [])
        self.assertEqual(self._leftovers(), [])

    def test_missing_scope_403(self):
        c = self._login("bob", NOSCOPE_PW)
        for r in (self._post(c), c.get("/api/admin/uploads"), c.get(f"/api/admin/uploads/{UPLOAD_ID}")):
            self.assertEqual((r.status_code, r.json()["code"]), (403, "missing_scope"))
        self.assertEqual(self.fake.calls, [])
        self.assertEqual(self._leftovers(), [])

    def test_cross_site_post_rejected_by_csrf(self):
        c = self._login()
        for headers in ({"origin": "https://evil.example"}, {"sec-fetch-site": "cross-site"}):
            with self.subTest(headers=headers):
                r = self._post(c, headers=headers)
                self.assertEqual((r.status_code, r.json()["code"]), (403, "csrf_rejected"))
        self.assertEqual(self.fake.calls, [])
        self.assertEqual(self._leftovers(), [])
        ok = self._post(c, headers={"origin": "http://127.0.0.1", "sec-fetch-site": "same-origin"})
        self.assertEqual(ok.status_code, 202, ok.text)

    # ── 旗標、請求格式 ───────────────────────────────────────────────────

    def test_disabled_503(self):
        self.settings(upload_enabled=False)
        r = self._post(self._login())
        self.assertEqual((r.status_code, r.json()["code"]), (503, "uploads_disabled"))
        self.assertIn("request_id", r.json())
        self.assertEqual(self.fake.calls, [])
        self.assertFalse(self.root.exists())

    def test_disabled_still_lists(self):
        self.settings(upload_enabled=False)
        self.assertEqual(self._login().get("/api/admin/uploads").status_code, 200)

    def test_flag_defaults_off(self):
        self.assertFalse(config.Settings.__dataclass_fields__["upload_enabled"].default)

    def test_wrong_content_type_415(self):
        c = self._login()
        for ctype in ("application/octet-stream", "multipart/form-data; boundary=x", "text/plain"):
            with self.subTest(ctype=ctype):
                r = self._post(c, headers={"content-type": ctype})
                self.assertEqual((r.status_code, r.json()["code"]), (415, "upload_not_pdf"))
        self.assertEqual(self._post(c, headers={"content-type": "Application/PDF; charset=binary"}).status_code, 202)

    def test_filename_rules(self):
        c = self._login()
        r = self._post(c, filename="report.exe")
        self.assertEqual((r.status_code, r.json()["code"]), (415, "upload_not_pdf"))
        r = self._post(c, filename="../ .pdf")
        self.assertEqual((r.status_code, r.json()["code"]), (400, "invalid_filename"))
        self.assertEqual(c.post("/api/admin/uploads", content=PDF,
                                headers={"content-type": "application/pdf"}).status_code, 422)
        r = self._post(c, filename="C:\\Users\\x\\..\\元大\u202e_報告\x07.PDF")
        self.assertEqual(r.status_code, 202, r.text)
        self.assertEqual(self._created()[-1][3], "元大_報告.pdf")

    def test_last_modified_becomes_client_mtime(self):
        r = self._post(self._login(), params={"last_modified": 1759743000000})
        self.assertEqual(r.status_code, 202, r.text)
        self.assertEqual(self._created()[0][5], datetime(2025, 10, 6, 9, 30, tzinfo=timezone.utc))
        self.assertEqual(r.json()["client_mtime"], "2025-10-06T09:30:00+00:00")
        bad = self._post(self._login(), params={"last_modified": -1})
        self.assertEqual(bad.status_code, 422)

    # ── 成功路徑 ────────────────────────────────────────────────────────

    def test_success_202_file_layout_and_permissions(self):
        r = self._post(self._login())
        self.assertEqual(r.status_code, 202, r.text)
        body = r.json()
        self.assertEqual(body["state"], "quarantined")
        self.assertEqual(body["file_hash"], hashlib.sha256(PDF).hexdigest())
        self.assertEqual(body["size_bytes"], len(PDF))
        call = self._created()[0]
        upload_id = call[1]
        self.assertEqual(body["upload_id"], upload_id)
        self.assertEqual(call[6], self.root_id)
        self.assertEqual(call[7:], (30, 50))
        self.assertEqual(self.tx, ["commit"])
        # 檔名只有 upload_id：不含使用者檔名、不帶 .pdf；incoming/ 不留 .part
        self.assertEqual(self._leftovers(), [f"{upload_id}.bin"])
        stored = self.root / f"{upload_id}.bin"
        self.assertEqual(stored.read_bytes(), PDF)
        self.assertEqual(_mode(stored), 0o600)
        self.assertEqual(_mode(self.root), 0o700)
        self.assertEqual(_mode(self.root / "incoming"), 0o700)
        self.assertNotIn("元大", " ".join(os.listdir(self.root)))

    def test_chunked_body_without_content_length(self):
        def gen():
            for i in range(0, len(PDF), 1000):
                yield PDF[i:i + 1000]

        c = self._login()
        r = c.post("/api/admin/uploads", params={"filename": "x.pdf"}, content=gen(),
                   headers={"content-type": "application/pdf"})
        self.assertEqual(r.status_code, 202, r.text)
        self.assertEqual(r.json()["file_hash"], hashlib.sha256(PDF).hexdigest())

    # ── 413／415 ────────────────────────────────────────────────────────

    def test_413_from_content_length_header(self):
        self.settings(upload_enabled=True, upload_max_bytes=len(PDF) - 1)
        r = self._post(self._login())
        self.assertEqual((r.status_code, r.json()["code"]), (413, "upload_too_large"))
        self.assertEqual(r.json()["max_bytes"], len(PDF) - 1)
        self.assertEqual(self.fake.calls, [])  # 連配額都沒查
        self.assertEqual(self._leftovers(), [])

    def test_413_while_streaming(self):
        self.settings(upload_enabled=True, upload_max_bytes=len(PDF) - 1)
        sent = []

        def gen():
            for i in range(0, len(PDF), 1000):
                sent.append(i)
                yield PDF[i:i + 1000]

        c = self._login()
        r = c.post("/api/admin/uploads", params={"filename": "x.pdf"}, content=gen(),
                   headers={"content-type": "application/pdf"})
        self.assertEqual((r.status_code, r.json()["code"]), (413, "upload_too_large"))
        self.assertEqual(self._created(), [])
        self.assertEqual(self._leftovers(), [])  # .part 已刪

    def test_415_magic_and_truncation(self):
        c = self._login()
        cases = {
            "no_magic": b"PK\x03\x04" + b"x" * 2000 + b"\n%%EOF\n",
            "magic_too_late": b" " * 1024 + PDF,
            "no_eof": PDF.replace(b"%%EOF", b"") + b"x",
            "eof_too_early": PDF + b"y" * 1100,
            "empty": b"",
        }
        for name, body in cases.items():
            with self.subTest(case=name):
                r = self._post(c, body)
                self.assertEqual((r.status_code, r.json()["code"]), (415, "upload_not_pdf"), r.text)
                self.assertEqual(self._leftovers(), [])
        self.assertEqual(self._created(), [])

    def test_magic_anywhere_in_first_1024_bytes_is_accepted(self):
        """PDF 規格容許檔頭前有垃圾位元組（閱讀器只找前 1024 bytes）；這裡跟著同一個判準。"""
        r = self._post(self._login(), b"\xef\xbb\xbf" + b" " * 500 + PDF)
        self.assertEqual(r.status_code, 202, r.text)

    # ── 409／422／429 ────────────────────────────────────────────────────

    def test_duplicate_in_corpus_409(self):
        h = hashlib.sha256(PDF).hexdigest()
        for status in ("published", "hidden", "draft"):
            with self.subTest(status=status):
                self.fake.create_error = upload_intake.DuplicateInCorpusError(h, status)
                self.tx.clear()
                r = self._post(self._login())
                self.assertEqual(r.status_code, 409, r.text)
                body = r.json()
                self.assertEqual((body["code"], body["file_hash"], body["existing"], body["status"]),
                                 ("upload_duplicate", h, "corpus", status))
                self.assertEqual(self.tx, ["rollback"])
                self.assertEqual(self._leftovers(), [])

    def test_in_progress_409_including_unique_race(self):
        h = hashlib.sha256(PDF).hexdigest()
        for exc in (upload_intake.UploadInProgressError(h, UPLOAD_ID, "scanning"),
                    upload_intake.UploadInProgressError(h)):  # 後者＝撞 partial unique index
            with self.subTest(upload_id=exc.upload_id):
                self.fake.create_error = exc
                r = self._post(self._login())
                self.assertEqual(r.status_code, 409, r.text)
                body = r.json()
                self.assertEqual((body["code"], body["existing"], body["upload_id"]),
                                 ("upload_duplicate", "upload", exc.upload_id))
                self.assertEqual(self._leftovers(), [])

    def test_known_infected_422(self):
        h = hashlib.sha256(PDF).hexdigest()
        self.fake.create_error = upload_intake.KnownInfectedError(h)
        r = self._post(self._login())
        self.assertEqual((r.status_code, r.json()["code"], r.json()["file_hash"]),
                         (422, "upload_known_infected", h))
        self.assertEqual(self._leftovers(), [])

    def test_quota_429_before_streaming(self):
        for scope in ("daily", "in_flight"):
            with self.subTest(scope=scope):
                self.fake.quota_error = upload_intake.QuotaExceededError(scope, 30, 30)
                r = self._post(self._login())
                self.assertEqual((r.status_code, r.json()["code"], r.json()["quota"]),
                                 (429, "upload_quota_exceeded", scope))
        self.assertEqual(self._created(), [])
        self.assertEqual(self._leftovers(), [])

    def test_quota_429_inside_transaction_cleans_file(self):
        self.fake.create_error = upload_intake.QuotaExceededError("in_flight", 50, 50)
        r = self._post(self._login())
        self.assertEqual((r.status_code, r.json()["code"]), (429, "upload_quota_exceeded"))
        self.assertEqual(self.tx, ["rollback"])
        self.assertEqual(self._leftovers(), [])

    def test_quota_knobs_are_passed(self):
        self.settings(upload_enabled=True, upload_daily_quota=7, upload_max_in_flight=9)
        self._post(self._login())
        self.assertEqual(self.fake.calls[0], ("quota", self.root_id, 7, 9))

    # ── 503（隔離區）與 DB 失敗 ─────────────────────────────────────────

    def test_quarantine_dir_unavailable_503(self):
        self.settings(upload_enabled=True, upload_quarantine_dir="/nonexistent/report-mark-quarantine-api")
        r = self._post(self._login())
        self.assertEqual((r.status_code, r.json()["code"]), (503, "quarantine_unavailable"))
        self.assertEqual(self.fake.calls, [])

    def test_low_disk_space_503(self):
        self.settings(upload_enabled=True, upload_min_free_mb=10)
        with mock.patch.object(quarantine.shutil, "disk_usage", return_value=mock.Mock(free=10 * 1024 * 1024)):
            r = self._post(self._login())
        self.assertEqual((r.status_code, r.json()["code"]), (503, "quarantine_unavailable"))
        self.assertIn("空間", r.json()["detail"])
        self.assertEqual(self.fake.calls, [])
        self.assertEqual(self._leftovers(), [])

    def test_db_failure_deletes_file(self):
        self.fake.create_error = RuntimeError("connection reset")
        r = self._post(self._login())
        self.assertEqual(r.status_code, 503, r.text)
        self.assertEqual(r.json()["code"], "unavailable")
        self.assertEqual(self.tx, ["rollback"])
        self.assertEqual(len(self.fake.seen_files), 1)  # 寫 DB 時 .bin 確實存在
        self.assertFalse(self.fake.seen_files[0].exists())
        self.assertEqual(self._leftovers(), [])

    def test_audit_failure_leaves_no_row_and_no_file(self):
        """create_upload 在 INSERT 之後寫稽核失敗：rollback（不 commit），檔案刪掉。"""
        self.fake.create_error = RuntimeError("admin_audit_log insert failed")
        r = self._post(self._login())
        self.assertEqual(r.status_code, 503)
        self.assertNotIn("commit", self.tx)
        self.assertEqual(self._leftovers(), [])

    def test_quota_db_failure_503(self):
        self.fake.quota_error = RuntimeError("db down")
        r = self._post(self._login())
        self.assertEqual(r.status_code, 503)
        self.assertEqual(self._leftovers(), [])

    # ── 清單與詳情 ──────────────────────────────────────────────────────

    def test_list_with_scanner_summary(self):
        r = self._login().get("/api/admin/uploads", params={"state": "quarantined", "limit": 2, "offset": 0})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.fake.calls, [("list", "quarantined", 2, 0)])
        body = r.json()
        self.assertEqual((body["total"], body["has_more"], body["next_offset"]), (3, True, 2))
        self.assertEqual(body["items"][1]["scan_signature"], "Eicar-Test-Signature")
        self.assertEqual(body["scanner"], {
            "pending": 2, "scanning": 1, "oldest_pending_at": WHEN.isoformat(), "oldest_pending_seconds": 600,
            "last_error": "signatures_stale", "last_error_at": WHEN.isoformat(),
        })

    def test_list_defaults_and_validation(self):
        c = self._login()
        self.assertEqual(c.get("/api/admin/uploads").status_code, 200)
        self.assertEqual(self.fake.calls, [("list", None, 50, 0)])
        self.assertEqual(c.get("/api/admin/uploads", params={"state": "bogus"}).status_code, 422)
        self.assertEqual(c.get("/api/admin/uploads", params={"limit": 0}).status_code, 422)

    def test_detail_and_404(self):
        c = self._login()
        r = c.get(f"/api/admin/uploads/{UPLOAD_ID}")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["state"], "draft")
        self.assertEqual(r.json()["report"]["publication"], "draft")
        self.assertEqual(r.json()["report"]["quality_score"], 0.9)
        missing = c.get("/api/admin/uploads/not-a-uuid")
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.json()["code"], "not_found")
        self.assertIn("request_id", missing.json())


class ErrorExtraTests(unittest.TestCase):
    def test_extra_fields_cannot_override_contract_keys(self):
        import json

        from web import errors

        r = errors.error_response(409, "重複", "upload_duplicate",
                                  extra={"file_hash": "f", "code": "evil", "detail": "evil", "request_id": "x"})
        body = json.loads(r.body)
        self.assertEqual((body["detail"], body["code"], body["file_hash"]), ("重複", "upload_duplicate", "f"))
        self.assertNotEqual(body["request_id"], "x")


class SanitizeFilenameTests(unittest.TestCase):
    def test_cases(self):
        s = upload_intake.sanitize_filename
        self.assertEqual(s("report.PDF"), "report.pdf")
        self.assertEqual(s("a/b/c/報告.pdf"), "報告.pdf")
        self.assertEqual(s("..\\..\\x.pdf"), "x.pdf")
        self.assertEqual(s(" 報告\t\n.pdf "), "報告.pdf")
        self.assertEqual(s("e\u0301.pdf"), "\u00e9.pdf")  # NFC
        self.assertEqual(s("x\u202egpj.pdf"), "xgpj.pdf")  # RTL 覆寫字元剔除
        long = s("長" * 400 + ".pdf")
        self.assertEqual(len(long), 255)
        self.assertTrue(long.endswith(".pdf"))
        for bad in ("x.exe", "pdf", "x.pdf.exe", ""):
            with self.subTest(bad=bad), self.assertRaises(upload_intake.NotPdfFilenameError):
                s(bad)
        for bad in (".pdf", " .pdf", "../.pdf", "\x00.pdf", "...pdf"):
            with self.subTest(bad=bad), self.assertRaises(upload_intake.InvalidFilenameError):
                s(bad)


if __name__ == "__main__":
    unittest.main()
