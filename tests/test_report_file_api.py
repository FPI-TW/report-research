# tests/test_report_file_api.py
"""舊 modal 原始檔端點 /api/report/{id}/full 與 /file 的煙霧測試。

這兩條路由在拆到 web/routers/report_file.py 之前完全沒有測試——搬動時只有
「有註冊、能 import」當安全網。這裡補上最小行為鎖：認證閘門、查無報告的
404、以及 file 端點「路徑一律由 DB 依 id 取得、不接受路徑注入」的性質。

SessionFactory 走 web.deps，故 patch web.deps.SessionFactory 即可注入假 DB。
"""
import hashlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("REPORT_MARK_ACCESS_USERNAME", "tester")
os.environ.setdefault("REPORT_MARK_ACCESS_PASSWORD", "testpass")
os.environ.setdefault("REPORT_MARK_SESSION_SECRET", "fixed-test-secret-0123456789")

from fastapi.testclient import TestClient  # noqa: E402

from app.services.object_storage import ObjectNotFound, ObjectStorageError  # noqa: E402
from web import deps  # noqa: E402
from web.routers import report_file as report_file_router  # noqa: E402
from web.server import app  # noqa: E402


class _Result:
    def __init__(self, row):
        self._row = row

    def first(self):
        return self._row


class _FakeSession:
    """execute 回固定一列（或 None）。以 report_id 無關的固定回應鎖行為。"""

    def __init__(self, row):
        self._row = row

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, *a, **k):
        return _Result(self._row)


def _authed():
    c = TestClient(app, follow_redirects=False, base_url="http://127.0.0.1")
    r = c.post("/login", data={"username": "tester", "password": "testpass"})
    assert r.status_code == 303, r.status_code
    return c


class ReportFileAuthTests(unittest.TestCase):
    def test_full_requires_login(self):
        c = TestClient(app, follow_redirects=False, base_url="http://127.0.0.1")
        self.assertEqual(c.get("/api/report/abc/full").status_code, 401)

    def test_file_requires_login(self):
        c = TestClient(app, follow_redirects=False, base_url="http://127.0.0.1")
        self.assertEqual(c.get("/api/report/abc/file").status_code, 401)


class ReportFileBehaviourTests(unittest.TestCase):
    def setUp(self):
        self._orig = deps.SessionFactory
        self._orig_storage = report_file_router.get_object_storage

    def tearDown(self):
        deps.SessionFactory = self._orig
        report_file_router.get_object_storage = self._orig_storage

    def test_full_returns_404_when_missing(self):
        deps.SessionFactory = lambda: _FakeSession(None)
        r = _authed().get("/api/report/44444444-4444-4444-8444-444444444444/full")
        self.assertEqual(r.status_code, 404)

    def test_malformed_id_is_404_without_touching_db(self):
        """id 是 uuid 欄位：非法字串若進到 SQL，驅動會在編碼期拋例外變 500。"""
        def _boom():
            raise AssertionError("非法 id 不該開 DB session")

        deps.SessionFactory = _boom
        for suffix in ("full", "file"):
            r = _authed().get(f"/api/report/not-a-uuid/{suffix}")
            self.assertEqual(r.status_code, 404, suffix)

    def test_full_returns_metadata(self):
        from datetime import date

        # _fetch_report 的 SELECT 欄序：file_name, market, source, report_date,
        # report_type, file_path, full_text, summary, title
        row = ("a.pdf", "TW", "kgi", date(2026, 7, 14), "note",
               "/nonexistent/a.pdf", "全文", "摘要", "內部標題")
        deps.SessionFactory = lambda: _FakeSession(row)
        r = _authed().get("/api/report/11111111-1111-4111-8111-111111111111/full")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["file_name"], "a.pdf")
        # modal 標題用它（缺值時前端才回退檔名）
        self.assertEqual(body["title"], "內部標題")
        self.assertEqual(body["market"], "TW")
        # 檔案不存在 → has_file False（不因 file_path 有值就當有檔）
        self.assertFalse(body["has_file"])

    def test_file_404_when_path_missing_on_disk(self):
        row = ("a.pdf", "TW", "kgi", None, None, "/nonexistent/a.pdf", None, None)
        deps.SessionFactory = lambda: _FakeSession(row)
        r = _authed().get("/api/report/11111111-1111-4111-8111-111111111111/file")
        self.assertEqual(r.status_code, 404)

    def test_file_path_comes_from_db_not_url(self):
        # 路徑注入防護：report_id 只當 DB 主鍵（WHERE id = :id，參數化），回傳的
        # 檔案永遠是 DB 那筆的 file_path，URL 的 id 從不碰檔案系統。這裡讓 DB 回一
        # 個存在的檔（本測試檔自己），確認不論 id 為何，回的都是 DB 指定的那個檔。
        here = str(Path(__file__).resolve())
        row = ("self.py", "TW", "kgi", None, None, here, None, None)
        deps.SessionFactory = lambda: _FakeSession(row)
        r = _authed().get("/api/report/33333333-3333-4333-8333-333333333333/file")
        self.assertEqual(r.status_code, 200)
        self.assertIn("test_report_file_api", r.headers.get("content-disposition", ""))

    def test_private_r2_file_redirects_without_cache(self):
        class _Storage:
            enabled = True
            mode = "r2"

            def head_object(self, key):
                self.key = key
                return {"ContentLength": 1, "Metadata": {"sha256": "a" * 64}}

            def presign_get(self, key, **_kwargs):
                return "https://private.example.test/signed"

        digest = "a" * 64
        row = (
            "a.pdf", "TW", "kgi", None, None, "/not-used.pdf", None, None, None,
            f"originals/aa/{digest}.pdf", digest,
        )
        deps.SessionFactory = lambda: _FakeSession(row)
        report_file_router.get_object_storage = lambda: _Storage()
        r = _authed().get("/api/report/22222222-2222-4222-8222-222222222222/file")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.headers["location"], "https://private.example.test/signed")
        self.assertEqual(r.headers["cache-control"], "no-store")

    def test_original_missing_or_mismatched_metadata_is_503_without_presign_or_hybrid_fallback(self):
        digest = "a" * 64

        for mode in ("hybrid", "r2"):
            for metadata in ({}, {"sha256": "b" * 64}):
                with self.subTest(mode=mode, metadata=metadata):
                    class _Storage:
                        enabled = True

                        def head_object(self, _key):
                            return {"Metadata": metadata}

                        def presign_get(self, _key, **_kwargs):
                            raise AssertionError("invalid original metadata must never be presigned")

                    _Storage.mode = mode
                    with tempfile.NamedTemporaryFile(suffix=".pdf") as local:
                        local.write(b"%PDF must not fallback")
                        local.flush()
                        row = (
                            "a.pdf", "TW", "kgi", None, None, local.name, None, None, None,
                            f"originals/aa/{digest}.pdf", digest,
                        )
                        deps.SessionFactory = lambda: _FakeSession(row)
                        report_file_router.get_object_storage = lambda: _Storage()
                        response = _authed().get("/api/report/22222222-2222-4222-8222-222222222222/file")
                    self.assertEqual(response.status_code, 503)

    def test_bucket_failure_is_503_and_hybrid_never_falls_back_local(self):
        class _Storage:
            enabled = True
            mode = "hybrid"

            def head_object(self, key):
                raise ObjectStorageError("NoSuchBucket")

        here = str(Path(__file__).resolve())
        digest = "a" * 64
        row = ("self.py", "TW", "kgi", None, None, here, None, None, None, f"originals/aa/{digest}.py", digest)
        deps.SessionFactory = lambda: _FakeSession(row)
        report_file_router.get_object_storage = lambda: _Storage()
        r = _authed().get("/api/report/22222222-2222-4222-8222-222222222222/file")
        self.assertEqual(r.status_code, 503)

    def test_confirmed_missing_r2_original_is_404(self):
        class _Storage:
            enabled = True
            mode = "r2"

            def head_object(self, key):
                raise ObjectNotFound(key)

        digest = "a" * 64
        row = (
            "a.pdf", "TW", "kgi", None, None, "/must-not-read.pdf", None, None, None,
            f"originals/aa/{digest}.pdf", digest,
        )
        deps.SessionFactory = lambda: _FakeSession(row)
        report_file_router.get_object_storage = lambda: _Storage()
        r = _authed().get("/api/report/22222222-2222-4222-8222-222222222222/file")
        self.assertEqual(r.status_code, 404)

    def test_confirmed_missing_hybrid_original_falls_back_to_its_local_file(self):
        class _Storage:
            enabled = True
            mode = "hybrid"

            def head_object(self, key):
                raise ObjectNotFound(key)

        data = b"%PDF local fallback"
        digest = hashlib.sha256(data).hexdigest()
        with tempfile.NamedTemporaryFile(suffix=".pdf") as local:
            local.write(data)
            local.flush()
            row = (
                "a.pdf", "TW", "kgi", None, None, local.name, None, None, None,
                f"originals/{digest[:2]}/{digest}.pdf", digest,
            )
            deps.SessionFactory = lambda: _FakeSession(row)
            report_file_router.get_object_storage = lambda: _Storage()
            response = _authed().get("/api/report/22222222-2222-4222-8222-222222222222/file")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, data)

    def test_hybrid_legacy_no_key_matching_local_original_is_served(self):
        class _Storage:
            enabled = True
            mode = "hybrid"

        data = b"%PDF legacy local original"
        digest = hashlib.sha256(data).hexdigest()
        with tempfile.NamedTemporaryFile(suffix=".pdf") as local:
            local.write(data)
            local.flush()
            row = ("a.pdf", "TW", "kgi", None, None, local.name, None, None, None, None, digest)
            deps.SessionFactory = lambda: _FakeSession(row)
            report_file_router.get_object_storage = lambda: _Storage()
            response = _authed().get("/api/report/22222222-2222-4222-8222-222222222222/file")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, data)

    def test_hybrid_legacy_no_key_mismatched_or_unreadable_local_original_is_503(self):
        class _Storage:
            enabled = True
            mode = "hybrid"

        digest = hashlib.sha256(b"expected legacy original").hexdigest()
        with self.subTest("mismatch"), tempfile.NamedTemporaryFile(suffix=".pdf") as local:
            local.write(b"%PDF stale legacy original")
            local.flush()
            row = ("a.pdf", "TW", "kgi", None, None, local.name, None, None, None, None, digest)
            deps.SessionFactory = lambda: _FakeSession(row)
            report_file_router.get_object_storage = lambda: _Storage()
            response = _authed().get("/api/report/22222222-2222-4222-8222-222222222222/file")
        self.assertEqual(response.status_code, 503)

        with self.subTest("unreadable"):
            row = ("a.pdf", "TW", "kgi", None, None, "/not-readable.pdf", None, None, None, None, digest)
            deps.SessionFactory = lambda: _FakeSession(row)
            report_file_router.get_object_storage = lambda: _Storage()
            with patch.object(report_file_router.os.path, "isfile", return_value=True):
                response = _authed().get("/api/report/22222222-2222-4222-8222-222222222222/file")
            self.assertEqual(response.status_code, 503)

        with self.subTest("missing-db-hash"):
            with tempfile.NamedTemporaryFile(suffix=".pdf") as local:
                local.write(b"%PDF no DB hash")
                local.flush()
                row = ("a.pdf", "TW", "kgi", None, None, local.name, None, None, None, None, None)
                deps.SessionFactory = lambda: _FakeSession(row)
                report_file_router.get_object_storage = lambda: _Storage()
                response = _authed().get("/api/report/22222222-2222-4222-8222-222222222222/file")
            self.assertEqual(response.status_code, 503)

    def test_r2_legacy_no_key_remains_404(self):
        class _Storage:
            enabled = True
            mode = "r2"

        row = ("a.pdf", "TW", "kgi", None, None, "/must-not-read.pdf", None, None, None, None, "a" * 64)
        deps.SessionFactory = lambda: _FakeSession(row)
        report_file_router.get_object_storage = lambda: _Storage()
        response = _authed().get("/api/report/22222222-2222-4222-8222-222222222222/file")
        self.assertEqual(response.status_code, 404)

    def test_confirmed_missing_hybrid_original_with_mismatched_local_bytes_is_503(self):
        class _Storage:
            enabled = True
            mode = "hybrid"

            def head_object(self, key):
                raise ObjectNotFound(key)

        digest = hashlib.sha256(b"expected original").hexdigest()
        with tempfile.NamedTemporaryFile(suffix=".pdf") as local:
            local.write(b"%PDF stale original")
            local.flush()
            row = (
                "a.pdf", "TW", "kgi", None, None, local.name, None, None, None,
                f"originals/{digest[:2]}/{digest}.pdf", digest,
            )
            deps.SessionFactory = lambda: _FakeSession(row)
            report_file_router.get_object_storage = lambda: _Storage()
            response = _authed().get("/api/report/22222222-2222-4222-8222-222222222222/file")
        self.assertEqual(response.status_code, 503)

    def test_confirmed_missing_hybrid_original_with_unreadable_local_file_is_503(self):
        class _Storage:
            enabled = True
            mode = "hybrid"

            def head_object(self, key):
                raise ObjectNotFound(key)

        digest = hashlib.sha256(b"expected original").hexdigest()
        row = (
            "a.pdf", "TW", "kgi", None, None, "/not-readable.pdf", None, None, None,
            f"originals/{digest[:2]}/{digest}.pdf", digest,
        )
        deps.SessionFactory = lambda: _FakeSession(row)
        report_file_router.get_object_storage = lambda: _Storage()
        # ``isfile`` can succeed even if a mount/ACL makes the file unreadable; the streaming
        # hash is the final integrity gate and must fail closed.
        with patch.object(report_file_router.os.path, "isfile", return_value=True):
            response = _authed().get("/api/report/22222222-2222-4222-8222-222222222222/file")
        self.assertEqual(response.status_code, 503)

    def test_wrong_original_pointer_is_not_presigned_or_locally_fallback(self):
        class _Storage:
            enabled = True
            mode = "hybrid"

            def head_object(self, _key):
                raise AssertionError("wrong pointer must fail before HEAD")

        digest = "a" * 64
        here = str(Path(__file__).resolve())
        row = ("a.pdf", "TW", "kgi", None, None, here, None, None, None, "originals/bb/other.pdf", digest)
        deps.SessionFactory = lambda: _FakeSession(row)
        report_file_router.get_object_storage = lambda: _Storage()
        r = _authed().get("/api/report/22222222-2222-4222-8222-222222222222/file")
        self.assertEqual(r.status_code, 503)


if __name__ == "__main__":
    unittest.main()


class R2OriginalFilenameAndAvailabilityTests(unittest.TestCase):
    """presign 要帶原檔名；r2 模式的 has_file 只認 key。"""

    def setUp(self):
        self._orig_session = deps.SessionFactory
        self._orig_storage = report_file_router.get_object_storage

    def tearDown(self):
        deps.SessionFactory = self._orig_session
        report_file_router.get_object_storage = self._orig_storage

    def test_redirect_presigns_with_original_filename_inline_for_pdf(self):
        captured = {}

        class _Storage:
            enabled = True
            mode = "r2"

            def head_object(self, key):
                return {"ContentLength": 1, "Metadata": {"sha256": "a" * 64}}

            def presign_get(self, key, **kwargs):
                captured.update(kwargs)
                return "https://private.example.test/signed"

        digest = "a" * 64
        row = ("台積電.pdf", "TW", "kgi", None, None, "/not-used.pdf", None, None, None,
               f"originals/aa/{digest}.pdf", digest)
        deps.SessionFactory = lambda: _FakeSession(row)
        report_file_router.get_object_storage = lambda: _Storage()
        r = _authed().get("/api/report/11111111-1111-4111-8111-111111111111/file")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(captured, {"filename": "台積電.pdf", "inline": True})

    def test_redirect_presigns_docx_as_attachment(self):
        captured = {}

        class _Storage:
            enabled = True
            mode = "hybrid"

            def head_object(self, key):
                return {"Metadata": {"sha256": "b" * 64}}

            def presign_get(self, key, **kwargs):
                captured.update(kwargs)
                return "https://private.example.test/signed"

        digest = "b" * 64
        row = ("memo.docx", "TW", "kgi", None, None, "/not-used.docx", None, None, None,
               f"originals/bb/{digest}.docx", digest)
        deps.SessionFactory = lambda: _FakeSession(row)
        report_file_router.get_object_storage = lambda: _Storage()
        r = _authed().get("/api/report/11111111-1111-4111-8111-111111111111/file")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(captured, {"filename": "memo.docx", "inline": False})

    def test_full_in_r2_mode_reports_no_file_without_key_even_if_local_exists(self):
        here = str(Path(__file__).resolve())
        row = ("self.py", "TW", "kgi", None, None, here, None, None, None, None, "c" * 64)
        deps.SessionFactory = lambda: _FakeSession(row)
        report_file_router.get_object_storage = lambda: type("S", (), {"enabled": True, "mode": "r2"})()
        body = _authed().get("/api/report/11111111-1111-4111-8111-111111111111/full").json()
        self.assertFalse(body["has_file"])

    def test_full_in_hybrid_mode_still_counts_existing_local_file(self):
        here = str(Path(__file__).resolve())
        row = ("self.py", "TW", "kgi", None, None, here, None, None, None, None, "c" * 64)
        deps.SessionFactory = lambda: _FakeSession(row)
        report_file_router.get_object_storage = lambda: type("S", (), {"enabled": True, "mode": "hybrid"})()
        body = _authed().get("/api/report/11111111-1111-4111-8111-111111111111/full").json()
        self.assertTrue(body["has_file"])
