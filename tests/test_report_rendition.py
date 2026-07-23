# tests/test_report_rendition.py
"""M9b-2 report_rendition + 換皮重出（rerender）。

rerender 端點（認證/404/成功建 rendition＋原子切指標/渲染失敗保留上一版/零 LLM）、
下載服務「當前 rendition 否則原始」、report.py 持久化 helper。假 session/渲染，零真 DB/LLM。
"""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("REPORT_MARK_ACCESS_USERNAME", "tester")
os.environ.setdefault("REPORT_MARK_ACCESS_PASSWORD", "testpass")
os.environ.setdefault("REPORT_MARK_SESSION_SECRET", "fixed-test-secret-0123456789")

from fastapi.testclient import TestClient  # noqa: E402

from app.services import report as rpt  # noqa: E402
from web.routers import report as rr  # noqa: E402
from web.server import app  # noqa: E402


def _authed():
    c = TestClient(app, follow_redirects=False, base_url="http://127.0.0.1")
    r = c.post("/login", data={"username": "tester", "password": "testpass"})
    assert r.status_code == 303, r.status_code
    return c


class _RecordingSession:
    """記錄 execute 的 SQL/params；execute 回可控 result。"""

    def __init__(self, rows=None):
        self.rows = rows or []
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, stmt, params=None):
        self.calls.append((str(stmt), params))

        class _R:
            def __init__(self, rows):
                self._rows = rows

            def first(self):
                return self._rows[0] if self._rows else None

        return _R(self.rows)

    async def commit(self):
        self.committed = True


class RenditionHelpersTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_rendition_inserts_and_returns_id(self):
        sess = _RecordingSession()
        with patch.object(rpt, "SessionFactory", lambda: sess):
            rid = await rpt.create_rendition(
                "rep-1", renderer="typst", template_id="ib-classic",
                content_hash="abc", pdf_path="/tmp/x.pdf",
            )
        self.assertTrue(rid)
        sql, params = sess.calls[0]
        self.assertIn("INSERT INTO research.report_rendition", sql)
        self.assertEqual(params["rid"], "rep-1")
        self.assertEqual(params["tid"], "ib-classic")

    async def test_set_current_rendition_updates_pointer(self):
        sess = _RecordingSession()
        with patch.object(rpt, "SessionFactory", lambda: sess):
            await rpt.set_current_rendition("rep-1", "ren-9")
        sql, params = sess.calls[0]
        self.assertIn("current_rendition_id", sql)
        self.assertEqual(params, {"cr": "ren-9", "id": "rep-1"})

    async def test_fetch_current_rendition_pdf_none_when_no_pointer(self):
        sess = _RecordingSession(rows=[])
        with patch.object(rpt, "SessionFactory", lambda: sess):
            self.assertIsNone(await rpt.fetch_current_rendition_pdf("rep-1"))

    async def test_fetch_current_rendition_pdf_returns_path(self):
        sess = _RecordingSession(rows=[("/tmp/rep-ab.pdf",)])
        with patch.object(rpt, "SessionFactory", lambda: sess):
            self.assertEqual(
                await rpt.fetch_current_rendition_pdf("rep-1"), "/tmp/rep-ab.pdf"
            )


class RerenderEndpointTests(unittest.TestCase):
    def test_requires_login(self):
        c = TestClient(app, follow_redirects=False, base_url="http://127.0.0.1")
        r = c.post(
            "/api/report-doc/123e4567-e89b-12d3-a456-426614174000/rerender",
            json={"template_id": "ib-classic"},
        )
        self.assertEqual(r.status_code, 401)

    def test_invalid_uuid_404(self):
        r = _authed().post("/api/report-doc/not-a-uuid/rerender", json={"template_id": "x"})
        self.assertEqual(r.status_code, 404)

    def test_missing_report_404(self):
        async def fake_doc(rid):
            return None

        with patch.object(rr, "fetch_report_doc", fake_doc):
            r = _authed().post(
                "/api/report-doc/123e4567-e89b-12d3-a456-426614174000/rerender",
                json={"template_id": "ib-classic"},
            )
        self.assertEqual(r.status_code, 404)

    def test_success_creates_rendition_and_switches_pointer(self):
        # 零 LLM：只 render 既有 markdown、建 rendition、切指標
        state = {}

        async def fake_doc(rid):
            return {"markdown": "# R", "title": "T", "date": "2026-07-23", "question": "q"}

        def fake_render(md, *, title, meta, template_id=None):
            state["rendered_template"] = template_id
            state["rendered_md"] = md
            return b"%PDF-1.4 x"

        def fake_write(rid, b, *, suffix=""):
            state["suffix"] = suffix
            return f"/tmp/{rid}{suffix}.pdf"

        async def fake_create(report_id, *, renderer, template_id, content_hash, pdf_path, status="ready"):
            state["created"] = dict(
                report_id=report_id, renderer=renderer, template_id=template_id,
                pdf_path=pdf_path,
            )
            return "ren-new"

        async def fake_set(report_id, rendition_id):
            state["switched_to"] = rendition_id

        with patch.object(rr, "fetch_report_doc", fake_doc), \
             patch.object(rr, "render_report_pdf", fake_render), \
             patch.object(rr, "write_report_pdf", fake_write), \
             patch.object(rr, "create_rendition", fake_create), \
             patch.object(rr, "set_current_rendition", fake_set):
            r = _authed().post(
                "/api/report-doc/123e4567-e89b-12d3-a456-426614174000/rerender",
                json={"template_id": "broker-modern"},
            )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["rendition_id"], "ren-new")
        self.assertEqual(state["rendered_template"], "broker-modern")  # 用新模板渲染既有 md
        self.assertEqual(state["rendered_md"], "# R")                   # 零重新生成
        self.assertNotEqual(state["suffix"], "")                        # 不覆蓋歷史 PDF
        self.assertEqual(state["switched_to"], "ren-new")               # 原子切指標

    def test_render_failure_keeps_previous_returns_500(self):
        async def fake_doc(rid):
            return {"markdown": "# R", "title": "T", "date": "d", "question": "q"}

        def boom_render(*a, **k):
            raise RuntimeError("兩軌皆炸")

        switched = []

        async def fake_set(report_id, rendition_id):
            switched.append(rendition_id)

        with patch.object(rr, "fetch_report_doc", fake_doc), \
             patch.object(rr, "render_report_pdf", boom_render), \
             patch.object(rr, "set_current_rendition", fake_set):
            r = _authed().post(
                "/api/report-doc/123e4567-e89b-12d3-a456-426614174000/rerender",
                json={"template_id": "x"},
            )
        self.assertEqual(r.status_code, 500)
        self.assertEqual(switched, [])  # 未切指標 → 保留上一版


class PdfServesCurrentRenditionTests(unittest.TestCase):
    def test_serves_current_rendition_when_present(self):
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(b"%PDF current")
            cur = f.name

        async def fake_current(rid):
            return cur

        try:
            with patch.object(rr, "fetch_current_rendition_pdf", fake_current):
                r = _authed().get(
                    "/api/report-doc/123e4567-e89b-12d3-a456-426614174000/pdf"
                )
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.content, b"%PDF current")
        finally:
            os.unlink(cur)

    def test_falls_back_to_original_when_no_rendition(self):
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(b"%PDF original")
            orig = f.name

        async def no_rendition(rid):
            return None

        async def fake_doc(rid):
            return {"pdf_path": orig, "markdown": "# R", "title": "T",
                    "date": "d", "question": "q"}

        try:
            with patch.object(rr, "fetch_current_rendition_pdf", no_rendition), \
                 patch.object(rr, "fetch_report_doc", fake_doc):
                r = _authed().get(
                    "/api/report-doc/123e4567-e89b-12d3-a456-426614174000/pdf"
                )
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.content, b"%PDF original")
        finally:
            os.unlink(orig)


if __name__ == "__main__":
    unittest.main()
