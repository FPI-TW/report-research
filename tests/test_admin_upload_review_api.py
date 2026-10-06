"""研報上傳的審核 API（/api/admin/uploads/{upload_id}/preview|file|publish|reject|unreject|retry，HTTP 層）。

DB 那一層換成假物件（`deps.upload_review`），假物件用 `app/services/uploads.py` 的詞彙實作同一張狀態閘門表、
拋真的錯誤類別：這裡驗的是授權（401／403／缺 scope）、CSRF、每個狀態的 HTTP 狀態碼與錯誤碼、請求驗證（422）、
成功才 commit、失敗一律 rollback、原檔 presign 的完整性檢查（假物件儲存，**不連 R2**）。
真的 SQL（條件式 UPDATE、並發、稽核同交易、檢索可見性、推導規則）在 tests/test_upload_review_db.py。
"""

from __future__ import annotations

import dataclasses
import unittest
from datetime import datetime, timezone
from unittest import mock

from fake_accounts import FakeAccounts, install
from fastapi.testclient import TestClient

from app import config
from app.services import upload_review as ur
from app.services import uploads
from app.services.object_storage import ObjectNotFound, ObjectStorageError
from app.services.upload_intake import UploadNotFoundError, UploadRow
from web import auth, deps
from web.routers import admin_uploads
from web.server import app

ADMIN_PW = "root-password-1"
USER_PW = "alice-password-1"
NOSCOPE_PW = "bob-password-1"
WHEN = datetime(2026, 10, 6, 9, 30, tzinfo=timezone.utc)
UPLOAD_ID = "33333333-3333-3333-3333-333333333333"
OTHER_ID = "44444444-4444-4444-4444-444444444444"
HASH = "ab" * 32
KEY = f"originals/ab/{HASH}.pdf"
FILE_NAME = "元大_台積電_20261001.pdf"

POSTS = ("publish", "unreject", "retry")  # 不帶 body 的寫入端點（reject 另外測）


def _client():
    return TestClient(app, follow_redirects=False, base_url="http://127.0.0.1")


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
        upload_id=UPLOAD_ID, file_hash=HASH, original_name=FILE_NAME, size_bytes=1234, client_mtime=WHEN,
        uploaded_by="root", uploaded_at=WHEN, state="draft", state_changed_at=WHEN, scan_attempts=1,
        scan_engine="ClamAV 1.4.3/27000/2026-10-06", scan_signature=None, scanned_at=WHEN, scan_last_error=None,
        process_attempts=1, failure_kind=None, failure_detail=None, processed_at=WHEN, decided_by=None,
        decided_at=None, decision_reason=None, purge_after=None, purged_at=None,
    )
    base.update(over)
    return UploadRow(**base)


class _FakeReview:
    """deps.upload_review 的替身：以真的狀態詞彙實作閘門、拋真的錯誤類別。簽章與 app/services/upload_review.py 一致。"""

    def __init__(self):
        self.state = "draft"
        self.failure_kind: str | None = None
        self.in_grace = True
        self.active_conflict = False
        self.report_missing = False
        self.object_key: str | None = KEY
        self.calls: list = []

    def _exists(self, upload_id):
        if upload_id != UPLOAD_ID:
            raise UploadNotFoundError("上傳紀錄不存在")

    def _previewable(self, upload_id):
        self._exists(upload_id)
        if self.state not in ur.PREVIEWABLE_STATES:
            raise ur.UploadStateConflictError("只有草稿或已發布的上傳可以預覽與取原檔", state=self.state)
        if self.report_missing:
            raise ur.ReportMissingError("語料中找不到這份上傳的研報", state=self.state)

    async def get_preview(self, session, upload_id):
        self.calls.append(("preview", upload_id))
        self._previewable(upload_id)
        return ur.UploadPreview(
            upload=_row(state=self.state), report_id="55555555-5555-5555-5555-555555555555", file_name=FILE_NAME,
            publication="draft" if self.state == "draft" else "published", hidden=False, title=None,
            title_original=None, summary="摘要", market="TW", is_research=True, confidence=0.9, source="元大",
            report_date=datetime(2026, 10, 1).date(), report_type="個股", language="zh", stock_code="2330",
            company_name="台積電", instrument_types=["stock"], stock_targets=["2330"], futures_targets=[],
            relates_stock=True, relates_futures=None, text="台積電第三季營收成長", text_chars=10,
            text_truncated=False, text_sha256="f" * 64, takeaways_state="ready",
            takeaways=[ur.PreviewTakeaway(ordinal=1, claim="論點", quote="營收成長", quote_start=6, quote_end=10,
                                          anchor_method="exact")],
        )

    async def get_original(self, session, upload_id):
        self.calls.append(("original", upload_id))
        self._previewable(upload_id)
        return ur.OriginalRef(upload_id=upload_id, file_hash=HASH, file_name=FILE_NAME, object_key=self.object_key)

    async def publish(self, session, upload_id, *, actor_id):
        self.calls.append(("publish", upload_id, actor_id))
        self._exists(upload_id)
        if self.state != uploads.STATE_DRAFT:
            raise ur.UploadStateConflictError("只有草稿可以發布", state=self.state)
        return _row(state="published", decided_by="root", decided_at=WHEN)

    async def reject(self, session, upload_id, *, reason, actor_id, grace_hours):
        self.calls.append(("reject", upload_id, reason, actor_id, grace_hours))
        ur.normalize_reason(reason)
        self._exists(upload_id)
        if self.state in ur.BUSY_STATES:
            raise ur.UploadBusyError("忙碌", state=self.state)
        if self.state == uploads.STATE_PUBLISHED:
            raise ur.UploadPublishedError("請改用隱藏", state=self.state)
        if self.state not in uploads.REJECTABLE_STATES:
            raise ur.UploadStateConflictError("不能退回", state=self.state)
        return _row(state="rejected", decision_reason=reason, decided_by="root", decided_at=WHEN, purge_after=WHEN)

    async def unreject(self, session, upload_id, *, actor_id):
        self.calls.append(("unreject", upload_id, actor_id))
        self._exists(upload_id)
        if self.state != uploads.STATE_REJECTED:
            raise ur.UploadStateConflictError("只有已退回的可以撤銷", state=self.state)
        if not self.in_grace:
            raise ur.RejectExpiredError("過期", state=self.state)
        if self.active_conflict:
            raise ur.ActiveUploadConflictError("另一筆進行中", state=self.state)
        return _row(state="clean")

    async def retry(self, session, upload_id, *, actor_id):
        self.calls.append(("retry", upload_id, actor_id))
        self._exists(upload_id)
        if self.state != uploads.STATE_FAILED:
            raise ur.UploadStateConflictError("只有失敗的可以重試", state=self.state)
        if self.failure_kind not in uploads.RETRYABLE_FAILURE_KINDS:
            raise ur.NotRetryableError("不能重試", state=self.state, failure_kind=self.failure_kind)
        return _row(state="clean", process_attempts=2)


class _FakeStorage:
    """假物件儲存：記錄 HEAD／presign，絕不連網。"""

    def __init__(self, mode="r2"):
        self.mode = mode
        self.calls: list = []
        self.sha = HASH
        self.head_error: Exception | None = None
        self.presign_error: Exception | None = None

    @property
    def enabled(self):
        return self.mode != "local"

    def head_object(self, key):
        self.calls.append(("head", key))
        if self.head_error is not None:
            raise self.head_error
        return {"Metadata": {"sha256": self.sha}}

    def presign_get(self, key, *, filename=None, inline=False):
        self.calls.append(("presign", key, filename, inline))
        if self.presign_error is not None:
            raise self.presign_error
        return f"https://r2.example/{key}?sig=1"


class _ScopedAccounts(FakeAccounts):
    def _user(self, row, elevated_until=None):
        user = super()._user(row, elevated_until)
        if row.username == "bob":
            user = dataclasses.replace(user, scopes=user.scopes - {"reports.manage"})
        return user


class AdminUploadReviewApiTests(unittest.TestCase):
    def setUp(self):
        auth._FAILS.clear()
        self.store = _ScopedAccounts()
        self.root_id = self.store.add_user("root", ADMIN_PW, "admin")
        self.store.add_user("alice", USER_PW, "user")
        self.store.add_user("bob", NOSCOPE_PW, "admin")
        self._ctx = install(self.store)
        self._ctx.__enter__()
        self.fake = _FakeReview()
        self.tx: list[str] = []
        self.storage = _FakeStorage()
        self._orig = (deps.upload_review, deps.SessionFactory, admin_uploads.get_object_storage)
        deps.upload_review = self.fake
        deps.SessionFactory = lambda: _Session(self.tx)
        admin_uploads.get_object_storage = lambda: self.storage
        self._settings_patch = mock.patch.object(config, "_SETTINGS", dataclasses.replace(
            config.get_settings(), upload_enabled=False, upload_reject_grace_hours=24, r2_presign_ttl_seconds=600,
        ))
        self._settings_patch.start()

    def tearDown(self):
        self._settings_patch.stop()
        deps.upload_review, deps.SessionFactory, admin_uploads.get_object_storage = self._orig
        self._ctx.__exit__(None, None, None)
        auth._FAILS.clear()

    def _login(self, username="root", password=ADMIN_PW):
        client = _client()
        r = client.post("/login", data={"username": username, "password": password})
        self.assertEqual(r.status_code, 303)
        return client

    def _requests(self, c, upload_id=UPLOAD_ID, headers=None):
        base = f"/api/admin/uploads/{upload_id}"
        return {
            "preview": lambda: c.get(f"{base}/preview"),
            "file": lambda: c.get(f"{base}/file"),
            "publish": lambda: c.post(f"{base}/publish", headers=headers),
            "reject": lambda: c.post(f"{base}/reject", json={"reason": "內容有誤"}, headers=headers),
            "unreject": lambda: c.post(f"{base}/unreject", headers=headers),
            "retry": lambda: c.post(f"{base}/retry", headers=headers),
        }

    def _assert_error(self, r, status, code):
        self.assertEqual((r.status_code, r.json().get("code")), (status, code), r.text)
        self.assertIn("request_id", r.json())

    # ── 授權與 CSRF ─────────────────────────────────────────────────────

    def test_unauthenticated_401(self):
        for name, call in self._requests(_client()).items():
            with self.subTest(endpoint=name):
                self.assertEqual(call().status_code, 401)
        self.assertEqual(self.fake.calls, [])

    def test_user_403(self):
        for name, call in self._requests(self._login("alice", USER_PW)).items():
            with self.subTest(endpoint=name):
                self.assertEqual(call().status_code, 403)
        self.assertEqual(self.fake.calls, [])

    def test_missing_scope_403(self):
        for name, call in self._requests(self._login("bob", NOSCOPE_PW)).items():
            with self.subTest(endpoint=name):
                self._assert_error(call(), 403, "missing_scope")
        self.assertEqual(self.fake.calls, [])

    def test_cross_site_post_rejected_by_csrf(self):
        c = self._login()
        for headers in ({"origin": "https://evil.example"}, {"sec-fetch-site": "cross-site"}):
            reqs = self._requests(c, headers=headers)
            for name in ("publish", "reject", "unreject", "retry"):
                with self.subTest(endpoint=name, headers=headers):
                    self._assert_error(reqs[name](), 403, "csrf_rejected")
        self.assertEqual(self.fake.calls, [])
        ok = self._requests(c, headers={"origin": "http://127.0.0.1", "sec-fetch-site": "same-origin"})["publish"]()
        self.assertEqual(ok.status_code, 200, ok.text)

    def test_unknown_upload_404(self):
        c = self._login()
        for upload_id in (OTHER_ID, "not-a-uuid"):
            for name, call in self._requests(c, upload_id=upload_id).items():
                with self.subTest(endpoint=name, upload_id=upload_id):
                    self._assert_error(call(), 404, "not_found")

    def test_review_works_while_uploads_disabled(self):
        self.assertFalse(config.get_settings().upload_enabled)
        self.assertEqual(self._login().post(f"/api/admin/uploads/{UPLOAD_ID}/publish").status_code, 200)

    # ── preview ─────────────────────────────────────────────────────────

    def test_preview_shape(self):
        r = self._login().get(f"/api/admin/uploads/{UPLOAD_ID}/preview")
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["upload"]["upload_id"], UPLOAD_ID)
        self.assertEqual((body["title"], body["title_state"]), (None, "pending"))
        self.assertEqual((body["summary"], body["summary_state"]), ("摘要", "ready"))
        self.assertEqual((body["text_state"], body["text_chars"], body["text_truncated"]), ("ready", 10, False))
        self.assertEqual(body["tags"]["stock_targets"], ["2330"])
        self.assertEqual(body["tags"]["report_date"], "2026-10-01")
        self.assertEqual(body["takeaways_state"], "ready")
        self.assertEqual(body["takeaways"][0]["quote_start"], 6)
        self.assertEqual(self.tx, [])  # 唯讀：不 commit

    def test_preview_and_file_gate_table(self):
        c = self._login()
        for state in uploads.STATES:
            self.fake.state = state
            for name in ("preview", "file"):
                with self.subTest(state=state, endpoint=name):
                    r = self._requests(c)[name]()
                    if state in ("draft", "published"):
                        self.assertEqual(r.status_code, 200, r.text)
                    else:
                        self._assert_error(r, 409, "upload_state_conflict")
                        self.assertEqual(r.json()["state"], state)

    def test_preview_report_missing_409_file_404(self):
        self.fake.report_missing = True
        c = self._login()
        self._assert_error(self._requests(c)["preview"](), 409, "upload_report_missing")
        self._assert_error(self._requests(c)["file"](), 404, "upload_report_missing")

    # ── file ────────────────────────────────────────────────────────────

    def test_file_presigns_with_filename_and_short_ttl(self):
        r = self._login().get(f"/api/admin/uploads/{UPLOAD_ID}/file")
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body, {"url": f"https://r2.example/{KEY}?sig=1", "expires_in": 600, "file_name": FILE_NAME})
        self.assertLessEqual(body["expires_in"], 3600)
        self.assertEqual(r.headers.get("cache-control"), "no-store")
        self.assertEqual(self.storage.calls, [("head", KEY), ("presign", KEY, FILE_NAME, True)])

    def test_file_never_falls_back_to_local_files(self):
        c = self._login()
        for mode, key in (("local", KEY), ("hybrid", None), ("r2", None)):
            self.storage.mode, self.fake.object_key = mode, key
            self.storage.calls.clear()
            with self.subTest(mode=mode, key=key):
                self._assert_error(c.get(f"/api/admin/uploads/{UPLOAD_ID}/file"), 404, "upload_file_unavailable")
                self.assertEqual(self.storage.calls, [])

    def test_file_integrity_and_storage_errors(self):
        c = self._login()
        url = f"/api/admin/uploads/{UPLOAD_ID}/file"
        self.fake.object_key = f"originals/zz/{'c' * 64}.pdf"  # 指向別的物件
        self._assert_error(c.get(url), 503, "original_integrity_error")
        self.assertEqual(self.storage.calls, [])  # 不一致時連 HEAD 都不打
        self.fake.object_key = KEY
        self.storage.sha = "d" * 64
        self._assert_error(c.get(url), 503, "original_integrity_error")
        self.assertNotIn("presign", [x[0] for x in self.storage.calls])
        self.storage.sha = HASH
        self.storage.head_error = ObjectNotFound("gone")
        self._assert_error(c.get(url), 404, "upload_file_unavailable")
        self.storage.head_error = ObjectStorageError("down")
        self._assert_error(c.get(url), 503, "object_storage_unavailable")
        self.storage.head_error = None
        self.storage.presign_error = ObjectStorageError("presign failed")
        self._assert_error(c.get(url), 503, "object_storage_unavailable")

    # ── publish ─────────────────────────────────────────────────────────

    def test_publish_gate_table(self):
        c = self._login()
        for state in uploads.STATES:
            self.fake.state = state
            self.tx.clear()
            with self.subTest(state=state):
                r = c.post(f"/api/admin/uploads/{UPLOAD_ID}/publish")
                if state == "draft":
                    self.assertEqual((r.status_code, r.json()["state"]), (200, "published"))
                    self.assertEqual(self.tx, ["commit"])
                else:
                    self._assert_error(r, 409, "upload_state_conflict")
                    self.assertEqual(self.tx, ["rollback"])
        self.assertEqual(self.fake.calls[-1], ("publish", UPLOAD_ID, self.root_id))

    # ── reject ──────────────────────────────────────────────────────────

    def test_reject_gate_table(self):
        expected = {
            **{s: (200, None) for s in uploads.REJECTABLE_STATES},
            "scanning": (409, "upload_busy"), "processing": (409, "upload_busy"),
            "published": (409, "upload_published_use_hide"),
            "infected": (409, "upload_state_conflict"), "blocked": (409, "upload_state_conflict"),
            "duplicate": (409, "upload_state_conflict"), "rejected": (409, "upload_state_conflict"),
        }
        self.assertEqual(set(expected), set(uploads.STATES))
        c = self._login()
        for state, (status, code) in expected.items():
            self.fake.state = state
            self.tx.clear()
            with self.subTest(state=state):
                r = c.post(f"/api/admin/uploads/{UPLOAD_ID}/reject", json={"reason": "內容有誤"})
                if code is None:
                    self.assertEqual((r.status_code, r.json()["state"]), (200, "rejected"), r.text)
                    self.assertEqual(self.tx, ["commit"])
                else:
                    self._assert_error(r, status, code)
                    self.assertEqual(r.json()["state"], state)
                    self.assertEqual(self.tx, ["rollback"])

    def test_reject_reason_validation_422(self):
        c = self._login()
        url = f"/api/admin/uploads/{UPLOAD_ID}/reject"
        for body in (None, {}, {"reason": ""}, {"reason": "   \n"}, {"reason": "字" * 501}, {"reason": 5}):
            with self.subTest(body=body if body is None or len(str(body)) < 40 else "long"):
                r = c.post(url, json=body) if body is not None else c.post(url)
                self._assert_error(r, 422, "validation_error")
        self.assertEqual(self.fake.calls, [])
        ok = c.post(url, json={"reason": "  " + "字" * 500 + "  "})
        self.assertEqual(ok.status_code, 200, ok.text)
        # 去頭尾空白後交給服務層；寬限期取自設定
        self.assertEqual(self.fake.calls[-1], ("reject", UPLOAD_ID, "字" * 500, self.root_id, 24))

    def test_reject_grace_from_settings(self):
        self._settings_patch.stop()
        self._settings_patch = mock.patch.object(config, "_SETTINGS", dataclasses.replace(
            config.get_settings(), upload_reject_grace_hours=48,
        ))
        self._settings_patch.start()
        self._login().post(f"/api/admin/uploads/{UPLOAD_ID}/reject", json={"reason": "x"})
        self.assertEqual(self.fake.calls[-1][-1], 48)

    # ── unreject ────────────────────────────────────────────────────────

    def test_unreject_gate_table(self):
        c = self._login()
        url = f"/api/admin/uploads/{UPLOAD_ID}/unreject"
        for state in uploads.STATES:
            self.fake.state = state
            with self.subTest(state=state):
                r = c.post(url)
                if state == "rejected":
                    self.assertEqual(r.status_code, 200, r.text)
                else:
                    self._assert_error(r, 409, "upload_state_conflict")
        self.fake.state = "rejected"
        self.fake.in_grace = False
        self.tx.clear()
        self._assert_error(c.post(url), 409, "upload_reject_expired")
        self.assertEqual(self.tx, ["rollback"])
        self.fake.in_grace = True
        self.fake.active_conflict = True
        self._assert_error(c.post(url), 409, "upload_active_conflict")

    # ── retry ───────────────────────────────────────────────────────────

    def test_retry_gate_table(self):
        c = self._login()
        url = f"/api/admin/uploads/{UPLOAD_ID}/retry"
        self.fake.failure_kind = "tag_failed"
        for state in uploads.STATES:
            self.fake.state = state
            with self.subTest(state=state):
                r = c.post(url)
                if state == "failed":
                    self.assertEqual((r.status_code, r.json()["state"]), (200, "clean"), r.text)
                else:
                    self._assert_error(r, 409, "upload_state_conflict")
        self.fake.state = "failed"
        for kind in uploads.FAILURE_KINDS:
            self.fake.failure_kind = kind
            with self.subTest(kind=kind):
                r = c.post(url)
                if kind in uploads.RETRYABLE_FAILURE_KINDS:
                    self.assertEqual(r.status_code, 200, r.text)
                else:
                    self._assert_error(r, 409, "upload_not_retryable")
                    self.assertEqual(r.json()["failure_kind"], kind)

    def test_retry_expected_kinds(self):
        """設計文件：只有 tag_failed／ingest_error／extract_timeout 可重試。"""
        self.assertEqual(set(uploads.RETRYABLE_FAILURE_KINDS), {"tag_failed", "ingest_error", "extract_timeout"})


if __name__ == "__main__":
    unittest.main()
