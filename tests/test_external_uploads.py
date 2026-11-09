"""對外上傳 API（`POST /external/v1/uploads`、`GET /external/v1/uploads/{upload_id}`）的 HTTP 行為。

走 `TestClient`：API 金鑰是假的 `deps.api_clients`（`tests/external_fakes.py`），收檔的 DB 那一層是假的
`deps.upload_intake`，隔離區是真的檔案系統（每題自己的 tempfile）。驗的是：scope、檔案進隔離區而不是入庫、
上傳者記成 API 用戶端、錯誤碼是對外文件那一組、狀態只查得到自己的。真的 SQL（依用戶端計配額、只查得到
自己的）在 `tests/test_admin_uploads_db.py`。
"""
from __future__ import annotations

import dataclasses
import hashlib
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

os.environ.setdefault("REPORT_MARK_SESSION_SECRET", "fixed-test-secret-0123456789")

import external_fakes as fx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import config  # noqa: E402
from app.services import quarantine, upload_intake  # noqa: E402
from app.services.upload_intake import UploadRow  # noqa: E402
from web import deps  # noqa: E402
from web.routers import external  # noqa: E402
from web.server import app  # noqa: E402

WHEN = datetime(2026, 11, 9, 9, 30, tzinfo=timezone.utc)
PDF = b"%PDF-1.7\n" + b"0123456789" * 600 + b"\ntrailer\n%%EOF\n"
UPLOAD_ID = "33333333-3333-4333-8333-333333333333"


def _row(**over) -> UploadRow:
    base = dict(
        upload_id=UPLOAD_ID, file_hash="a" * 64, original_name="20261101_券商_報告.pdf", size_bytes=len(PDF),
        client_mtime=None, uploaded_by=None, uploaded_at=WHEN, state="quarantined", state_changed_at=WHEN,
        scan_attempts=0, scan_engine=None, scan_signature="Eicar", scanned_at=None, scan_last_error="x",
        process_attempts=0, failure_kind="tag_failed", failure_detail="內部細節", processed_at=None, decided_by=None,
        decided_at=None, decision_reason="內部原因", purge_after=None, purged_at=None, api_client_id=1,
        api_client="外部系統",
    )
    base.update(over)
    return UploadRow(**base)


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


class _FakeIntake:
    def __init__(self, test):
        self.test = test
        self.calls: list = []
        self.create_error: Exception | None = None
        self.quota_error: Exception | None = None
        self.rows: dict[tuple[str, int], UploadRow] = {}

    async def check_quota(self, session, *, actor_id, daily_quota, max_in_flight, api_client_id=None):
        self.calls.append(("quota", actor_id, api_client_id))
        if self.quota_error is not None:
            raise self.quota_error

    async def create_upload(self, session, *, upload_id, file_hash, original_name, size_bytes, client_mtime,
                            actor_id, daily_quota, max_in_flight, api_client_id=None):
        path = quarantine.bin_path(self.test.root, upload_id)
        self.test.assertTrue(path.exists(), "寫 DB 的當下檔案已經在隔離區")
        self.test.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), file_hash)
        self.calls.append(("create", original_name, actor_id, api_client_id, client_mtime))
        if self.create_error is not None:
            raise self.create_error
        row = _row(upload_id=upload_id, file_hash=file_hash, original_name=original_name, size_bytes=size_bytes,
                   api_client_id=api_client_id)
        self.rows[(upload_id, api_client_id)] = row
        return row

    async def get_client_upload(self, session, upload_id, api_client_id):
        self.calls.append(("get", upload_id, api_client_id))
        row = self.rows.get((upload_id, api_client_id))
        if row is None:
            raise upload_intake.UploadNotFoundError("上傳紀錄不存在")
        return row


class ExternalUploadTests(unittest.TestCase):
    def setUp(self):
        self.api = fx.FakeApiClients()
        self.key = self.api.add(fx.make_client(1, scopes=("report.upload",)))
        self.other = self.api.add(fx.make_client(2, scopes=("report.upload",)))
        self.search_only = self.api.add(fx.make_client(3, scopes=("search",)))
        self.fake = _FakeIntake(self)
        self.tx: list[str] = []
        self._orig = (deps.api_clients, deps.upload_intake, deps.SessionFactory)
        deps.api_clients = self.api
        deps.upload_intake = self.fake
        deps.SessionFactory = lambda: _Session(self.tx)
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "quarantine"
        self._settings_patch = None
        self.settings(upload_enabled=True)
        self.client = TestClient(app, follow_redirects=False)

    def tearDown(self):
        self._settings_patch.stop()
        deps.api_clients, deps.upload_intake, deps.SessionFactory = self._orig
        self._tmp.cleanup()

    def settings(self, **over):
        if self._settings_patch is not None:
            self._settings_patch.stop()
        values = dict(upload_quarantine_dir=str(self.root), upload_max_bytes=1 << 20, upload_min_free_mb=0,
                      upload_daily_quota=5, upload_max_in_flight=50)
        values.update(over)
        self._settings_patch = mock.patch.object(config, "_SETTINGS",
                                                 dataclasses.replace(config.get_settings(), **values))
        self._settings_patch.start()

    def post(self, body=PDF, *, key=None, filename="20261101_券商_報告.pdf", headers=None, params=None):
        h = {"Authorization": f"Bearer {key or self.key}", "Content-Type": "application/pdf", **(headers or {})}
        return self.client.post("/external/v1/uploads", params={"filename": filename, **(params or {})},
                                content=body, headers=h)

    def leftovers(self) -> list[str]:
        return sorted(p.name for p in self.root.rglob("*") if p.is_file()) if self.root.exists() else []

    # ── 成功 ────────────────────────────────────────────────────────────────
    def test_upload_lands_in_quarantine_attributed_to_the_client(self):
        r = self.post(params={"last_modified": "1762300000000"})
        self.assertEqual(r.status_code, 202, r.text)
        body = r.json()
        self.assertEqual(set(body), {"upload_id", "file_hash", "file_name", "size_bytes", "status", "uploaded_at",
                                     "updated_at"})
        self.assertEqual(body["status"], "processing")
        self.assertEqual(body["file_hash"], hashlib.sha256(PDF).hexdigest())
        self.assertEqual(r.headers["cache-control"], "no-store")
        self.assertEqual(self.fake.calls[0], ("quota", None, 1))
        create = self.fake.calls[1]
        self.assertEqual(create[:4], ("create", "20261101_券商_報告.pdf", None, 1), "上傳者是 API 用戶端，不是帳號")
        self.assertEqual(create[4], datetime.fromtimestamp(1762300000, tz=timezone.utc))
        self.assertEqual(self.tx, ["commit"])
        self.assertEqual(self.leftovers(), [f"{body['upload_id']}.bin"], "檔案留在隔離區等掃描，不直接入庫")

    def test_status_only_for_own_uploads_and_hides_internal_fields(self):
        upload_id = self.post().json()["upload_id"]
        r = self.client.get(f"/external/v1/uploads/{upload_id}", headers={"Authorization": f"Bearer {self.key}"})
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("scan_signature", r.text)
        self.assertNotIn("內部", r.text)
        other = self.client.get(f"/external/v1/uploads/{upload_id}",
                                headers={"Authorization": f"Bearer {self.other}"})
        self.assertEqual((other.status_code, other.json()["code"]), (404, "upload_not_found"))
        bad = self.client.get("/external/v1/uploads/not-a-uuid", headers={"Authorization": f"Bearer {self.key}"})
        self.assertEqual(bad.status_code, 404)

    def test_external_status_vocabulary_covers_every_state(self):
        from app.services import uploads

        self.assertEqual(set(external.EXTERNAL_UPLOAD_STATUS), set(uploads.STATES))
        self.assertEqual(set(external.EXTERNAL_UPLOAD_STATUS.values()),
                         {"processing", "pending_review", "published", "rejected"})

    # ── 擋下 ────────────────────────────────────────────────────────────────
    def test_requires_upload_scope(self):
        r = self.post(key=self.search_only)
        self.assertEqual((r.status_code, r.json()["code"]), (403, "api_scope_missing"))
        self.assertEqual(self.fake.calls, [])
        r = self.client.post("/external/v1/uploads", params={"filename": "a.pdf"}, content=PDF,
                             headers={"Content-Type": "application/pdf"})
        self.assertEqual(r.status_code, 401)

    def test_disabled_uploads_503(self):
        self.settings(upload_enabled=False)
        r = self.post()
        self.assertEqual((r.status_code, r.json()["code"]), (503, "upload_unavailable"))
        self.assertEqual(self.leftovers(), [])

    def test_paused_by_feature_flag_503(self):
        with mock.patch("app.services.feature_flags.policy", mock.AsyncMock(return_value=False)):
            r = self.post()
        self.assertEqual((r.status_code, r.json()["code"]), (503, "upload_unavailable"))

    def test_not_pdf(self):
        self.assertEqual(self.post(headers={"Content-Type": "application/octet-stream"}).json()["code"],
                         "upload_not_pdf")
        self.assertEqual(self.post(filename="a.docx").json()["code"], "upload_not_pdf")
        r = self.post(body=b"MZ" + b"0" * 100)
        self.assertEqual((r.status_code, r.json()["code"]), (415, "upload_not_pdf"))
        self.assertEqual(self.leftovers(), [], "內容不是 PDF 的不留在隔離區")

    def test_too_large(self):
        self.settings(upload_enabled=True, upload_max_bytes=100)
        r = self.post()
        self.assertEqual((r.status_code, r.json()["code"]), (413, "upload_too_large"))

    def test_duplicate_does_not_leak_corpus_state_and_removes_file(self):
        self.fake.create_error = upload_intake.DuplicateInCorpusError("a" * 64, "hidden")
        r = self.post()
        self.assertEqual((r.status_code, r.json()["code"]), (409, "upload_duplicate"))
        self.assertNotIn("hidden", r.text)
        self.assertNotIn("隱藏", r.text)
        self.assertEqual(self.leftovers(), [])
        self.assertEqual(self.tx, ["rollback"])

    def test_quota(self):
        self.fake.quota_error = upload_intake.QuotaExceededError("daily", 5, 5)
        r = self.post()
        self.assertEqual((r.status_code, r.json()["code"]), (429, "upload_quota_exceeded"))
        self.fake.quota_error = upload_intake.QuotaExceededError("in_flight", 50, 50)
        self.assertEqual(self.post().json()["code"], "upload_busy")
        self.assertEqual(self.leftovers(), [])

    def test_known_infected(self):
        self.fake.create_error = upload_intake.KnownInfectedError("a" * 64)
        r = self.post()
        self.assertEqual((r.status_code, r.json()["code"]), (422, "upload_rejected"))

    def test_db_failure_removes_quarantined_file(self):
        self.fake.create_error = RuntimeError("db down")
        r = self.post()
        self.assertEqual((r.status_code, r.json()["code"]), (503, "unavailable"))
        self.assertEqual(self.leftovers(), [])


if __name__ == "__main__":
    unittest.main()
