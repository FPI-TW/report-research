# tests/test_report_file_api.py
"""舊 modal 原始檔端點 /api/report/{id}/full 與 /file 的煙霧測試。

這兩條路由在拆到 web/routers/report_file.py 之前完全沒有測試——搬動時只有
「有註冊、能 import」當安全網。這裡補上最小行為鎖：認證閘門、查無報告的
404、以及 file 端點「路徑一律由 DB 依 id 取得、不接受路徑注入」的性質。

SessionFactory 走 web.deps，故 patch web.deps.SessionFactory 即可注入假 DB。
"""
import os
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("REPORT_MARK_ACCESS_USERNAME", "tester")
os.environ.setdefault("REPORT_MARK_ACCESS_PASSWORD", "testpass")
os.environ.setdefault("REPORT_MARK_SESSION_SECRET", "fixed-test-secret-0123456789")

from fastapi.testclient import TestClient  # noqa: E402

from web import deps  # noqa: E402
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

    def tearDown(self):
        deps.SessionFactory = self._orig

    def test_full_returns_404_when_missing(self):
        deps.SessionFactory = lambda: _FakeSession(None)
        r = _authed().get("/api/report/does-not-exist/full")
        self.assertEqual(r.status_code, 404)

    def test_full_returns_metadata(self):
        from datetime import date

        # _fetch_report 的 SELECT 欄序：file_name, market, source, report_date,
        # report_type, file_path, full_text, summary
        row = ("a.pdf", "TW", "kgi", date(2026, 7, 14), "note",
               "/nonexistent/a.pdf", "全文", "摘要")
        deps.SessionFactory = lambda: _FakeSession(row)
        r = _authed().get("/api/report/rid-1/full")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["file_name"], "a.pdf")
        self.assertEqual(body["market"], "TW")
        # 檔案不存在 → has_file False（不因 file_path 有值就當有檔）
        self.assertFalse(body["has_file"])

    def test_file_404_when_path_missing_on_disk(self):
        row = ("a.pdf", "TW", "kgi", None, None, "/nonexistent/a.pdf", None, None)
        deps.SessionFactory = lambda: _FakeSession(row)
        r = _authed().get("/api/report/rid-1/file")
        self.assertEqual(r.status_code, 404)

    def test_file_path_comes_from_db_not_url(self):
        # 路徑注入防護：report_id 只當 DB 主鍵（WHERE id = :id，參數化），回傳的
        # 檔案永遠是 DB 那筆的 file_path，URL 的 id 從不碰檔案系統。這裡讓 DB 回一
        # 個存在的檔（本測試檔自己），確認不論 id 為何，回的都是 DB 指定的那個檔。
        here = str(Path(__file__).resolve())
        row = ("self.py", "TW", "kgi", None, None, here, None, None)
        deps.SessionFactory = lambda: _FakeSession(row)
        r = _authed().get("/api/report/whatever-id/file")
        self.assertEqual(r.status_code, 200)
        self.assertIn("test_report_file_api", r.headers.get("content-disposition", ""))


if __name__ == "__main__":
    unittest.main()
