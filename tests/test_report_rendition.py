# tests/test_report_rendition.py
"""M9b-2 report_rendition + 換皮重出（rerender）。

rerender 端點（認證/404/成功建 rendition＋原子切指標/渲染失敗保留上一版/零 LLM）、
下載服務「當前 rendition 否則原始」、report.py 持久化 helper。假 session/渲染，零真 DB/LLM。
"""
import hashlib
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
from app.services.object_storage import (  # noqa: E402
    ObjectNotFound,
    ObjectStorageError,
    generated_object_key_for_sha,
)
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

    async def test_create_rendition_allows_r2_object_only_row(self):
        sess = _RecordingSession()
        with patch.object(rpt, "SessionFactory", lambda: sess):
            rendition_id = await rpt.create_rendition(
                "rep-1",
                renderer="typst",
                template_id="ib-classic",
                content_hash="abc",
                pdf_path=None,
                pdf_object_key="generated/rep-1/renditions/ren-1-aaaaaaaaaaaa.pdf",
                rendition_id="ren-1",
            )
        sql, params = sess.calls[0]
        self.assertEqual(rendition_id, "ren-1")
        self.assertIn("pdf_object_key", sql)
        self.assertIsNone(params["pp"])
        self.assertEqual(params["pok"], "generated/rep-1/renditions/ren-1-aaaaaaaaaaaa.pdf")

    async def test_set_current_rendition_updates_pointer(self):
        sess = _RecordingSession()
        with patch.object(rpt, "SessionFactory", lambda: sess):
            await rpt.set_current_rendition("rep-1", "ren-9")
        sql, params = sess.calls[0]
        self.assertIn("current_rendition_id", sql)
        self.assertEqual(params, {"cr": "ren-9", "id": "rep-1"})

    async def test_current_rendition_repair_is_compare_and_set(self):
        class _Result:
            rowcount = 0

        class _Session(_RecordingSession):
            async def execute(self, stmt, params=None):
                self.calls.append((str(stmt), params))
                return _Result()

        session = _Session()
        with patch.object(rpt, "SessionFactory", lambda: session):
            updated = await rpt.update_current_rendition_pdf_location(
                "rep-1", "ren-old", None, "generated/rep-1/renditions/ren-old-aaaaaaaaaaaa.pdf"
            )
        sql, params = session.calls[0]
        self.assertFalse(updated)
        self.assertIn("current_rendition_id = :rid", sql)
        self.assertEqual(params["report_id"], "rep-1")
        self.assertEqual(params["rid"], "ren-old")

    async def test_current_base_repair_is_compare_and_set_while_pointer_is_null(self):
        class _Result:
            rowcount = 0

        class _Session(_RecordingSession):
            async def execute(self, stmt, params=None):
                self.calls.append((str(stmt), params))
                return _Result()

        session = _Session()
        with patch.object(rpt, "SessionFactory", lambda: session):
            updated = await rpt.update_current_base_pdf_location(
                "rep-1", None, "generated/rep-1/base-aaaaaaaaaaaa.pdf"
            )
        sql, params = session.calls[0]
        self.assertFalse(updated)
        self.assertIn("current_rendition_id IS NULL", sql)
        self.assertEqual(params["id"], "rep-1")

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

        # **簽章必須與真 render_report_pdf 一致**（本 session 第五次踩簽章漂移）：
        # locale 是 A 項加入的參數,漏了會在呼叫點 TypeError → 端點回 500。
        def fake_render(md, *, title, meta, template_id=None, locale=None, renderer=None, return_result=False):
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

    def test_r2_rerender_persists_object_key_without_local_path(self):
        captured = {}

        class _Storage:
            mode = "r2"

        async def fake_doc(rid):
            return {"markdown": "# R", "title": "T", "date": "d", "question": "q"}

        async def fake_persist(report_id, data, *, rendition_id, suffix):
            captured["persist"] = (report_id, rendition_id, suffix)
            return None, "generated/doc/renditions/ren-r2-aaaaaaaaaaaa.pdf"

        async def fake_create(report_id, **kwargs):
            captured["create"] = kwargs
            return kwargs["rendition_id"]

        async def fake_set(report_id, rendition_id):
            captured["pointer"] = rendition_id

        with patch.object(rr, "fetch_report_doc", fake_doc), \
             patch.object(rr, "render_report_pdf", return_value=b"%PDF r2"), \
             patch.object(rr, "get_object_storage", return_value=_Storage()), \
             patch.object(rr, "persist_generated_pdf", fake_persist), \
             patch.object(rr, "create_rendition", fake_create), \
             patch.object(rr, "set_current_rendition", fake_set):
            r = _authed().post(
                "/api/report-doc/123e4567-e89b-12d3-a456-426614174000/rerender", json={}
            )
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(captured["create"]["pdf_path"])
        self.assertEqual(captured["create"]["pdf_object_key"], "generated/doc/renditions/ren-r2-aaaaaaaaaaaa.pdf")
        self.assertEqual(captured["pointer"], captured["create"]["rendition_id"])

    def test_rerender_persists_actual_renderer_after_typst_fallback(self):
        captured = {}

        class _Storage:
            mode = "local"

        async def fake_doc(_rid):
            return {"markdown": "# R", "title": "T", "renderer": "typst"}

        def fake_write(*_args, **_kwargs):
            return "/tmp/rendition.pdf"

        async def fake_create(_report_id, **kwargs):
            captured["renderer"] = kwargs["renderer"]
            return "ren-actual"

        async def fake_set(*_args):
            return None

        with patch.object(rr, "fetch_report_doc", fake_doc), \
             patch.object(rr, "get_object_storage", return_value=_Storage()), \
             patch.object(rr, "render_report_pdf", return_value=rpt.RenderedPdf(b"%PDF", "weasyprint")), \
             patch.object(rr, "write_report_pdf", fake_write), \
             patch.object(rr, "create_rendition", fake_create), \
             patch.object(rr, "set_current_rendition", fake_set):
            response = _authed().post(
                "/api/report-doc/123e4567-e89b-12d3-a456-426614174000/rerender", json={}
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(captured["renderer"], "weasyprint")


class PdfServesCurrentRenditionTests(unittest.TestCase):
    def test_serves_current_rendition_when_present(self):
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(b"%PDF current")
            cur = f.name

        async def fake_current(rid):
            return {"id": "ren-1", "pdf_path": cur, "pdf_object_key": None, "template_id": "ib-classic"}

        try:
            with patch.object(rr, "fetch_current_rendition", fake_current):
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
            with patch.object(rr, "fetch_current_rendition", no_rendition), \
                 patch.object(rr, "fetch_report_doc", fake_doc):
                r = _authed().get(
                    "/api/report-doc/123e4567-e89b-12d3-a456-426614174000/pdf"
                )
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.content, b"%PDF original")
        finally:
            os.unlink(orig)

    def test_hybrid_falls_back_local_when_generated_key_is_missing(self):
        import tempfile

        class _Storage:
            enabled = True
            mode = "hybrid"

            def head_object(self, key):
                raise ObjectNotFound(key)  # ObjectNotFound/NoSuchKey is the only permitted fallback case.

        data = b"%PDF hybrid"
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(data)
            local = f.name

        async def fake_current(rid):
            return {
                "id": "ren-1", "pdf_path": local,
                "pdf_object_key": generated_object_key_for_sha(
                    "123e4567-e89b-12d3-a456-426614174000", hashlib.sha256(data).hexdigest(), "ren-1"
                ), "template_id": "ib-classic",
            }

        try:
            with patch.object(rr, "fetch_current_rendition", fake_current), \
                 patch.object(rr, "get_object_storage", return_value=_Storage()):
                r = _authed().get("/api/report-doc/123e4567-e89b-12d3-a456-426614174000/pdf")
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.content, b"%PDF hybrid")
        finally:
            os.unlink(local)

    def test_hybrid_missing_base_with_mismatched_local_pdf_rebuilds_instead_of_serving_stale_file(self):
        import tempfile

        report_id = "123e4567-e89b-12d3-a456-426614174000"
        captured = {}

        class _Storage:
            enabled = True
            mode = "hybrid"

            def head_object(self, key):
                raise ObjectNotFound(key)

            def presign_get(self, key):
                captured["presigned"] = key
                return "https://private.example.test/rebuilt-base"

        async def no_rendition(_rid):
            return None

        async def fake_persist(_rid, pdf, **kwargs):
            captured["persist"] = (pdf, kwargs)
            return "/tmp/rebuilt-base.pdf", "generated/rebuilt/base.pdf"

        async def fake_update(*args):
            captured["updated"] = args
            return True

        with tempfile.NamedTemporaryFile(suffix=".pdf") as local:
            local.write(b"%PDF stale base")
            local.flush()

            async def fake_doc(_rid):
                return {
                    "pdf_path": local.name,
                    "pdf_object_key": generated_object_key_for_sha(report_id, "a" * 64),
                    "markdown": "# rebuilt", "title": "T", "locale": "en",
                    "template_id": "base", "renderer": "weasyprint",
                }

            with patch.object(rr, "fetch_current_rendition", no_rendition), \
                 patch.object(rr, "fetch_report_doc", fake_doc), \
                 patch.object(rr, "get_object_storage", return_value=_Storage()), \
                 patch.object(rr, "render_report_pdf", return_value=b"%PDF rebuilt") as render, \
                 patch.object(rr, "persist_generated_pdf", fake_persist), \
                 patch.object(rr, "update_current_base_pdf_location", fake_update):
                response = _authed().get(f"/api/report-doc/{report_id}/pdf")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["location"], "https://private.example.test/rebuilt-base")
        self.assertEqual(captured["persist"][0], b"%PDF rebuilt")
        self.assertEqual(captured["updated"], (report_id, "/tmp/rebuilt-base.pdf", "generated/rebuilt/base.pdf"))
        self.assertTrue(render.called)

    def test_hybrid_missing_rendition_with_mismatched_local_pdf_never_serves_stale_file(self):
        import tempfile

        report_id = "123e4567-e89b-12d3-a456-426614174000"

        class _Storage:
            enabled = True
            mode = "hybrid"

            def head_object(self, key):
                raise ObjectNotFound(key)

        with tempfile.NamedTemporaryFile(suffix=".pdf") as local:
            local.write(b"%PDF stale rendition")
            local.flush()

            async def fake_current(_rid):
                return {
                    "id": "ren-1", "pdf_path": local.name,
                    "pdf_object_key": generated_object_key_for_sha(report_id, "a" * 64, "ren-1"),
                    "template_id": "t", "renderer": "weasyprint",
                }

            async def fake_doc(_rid):
                return {"markdown": "# rebuilt", "title": "T", "locale": "en"}

            async def should_not_persist(*_args, **_kwargs):
                raise AssertionError("strict renderer failure must not upload")

            with patch.object(rr, "fetch_current_rendition", fake_current), \
                 patch.object(rr, "fetch_report_doc", fake_doc), \
                 patch.object(rr, "get_object_storage", return_value=_Storage()), \
                 patch.object(rr, "render_report_pdf", side_effect=RuntimeError("renderer unavailable")), \
                 patch.object(rr, "persist_generated_pdf", should_not_persist):
                response = _authed().get(f"/api/report-doc/{report_id}/pdf")
        self.assertEqual(response.status_code, 503)

    def test_missing_base_repair_lost_race_never_serves_or_replaces_new_rendition(self):
        report_id = "123e4567-e89b-12d3-a456-426614174000"
        state = {"current": None}

        class _Storage:
            enabled = True
            mode = "r2"

            def presign_get(self, _key):
                raise AssertionError("lost base repair race must not presign stale base")

        async def no_rendition(_rid):
            return None

        async def fake_doc(_rid):
            return {
                "pdf_path": None, "pdf_object_key": None,
                "markdown": "# base", "title": "T", "locale": "en",
                "template_id": "base", "renderer": "weasyprint",
            }

        async def fake_persist(_rid, _pdf, **_kwargs):
            return None, "generated/rebuilt/base.pdf"

        async def lost_compare_and_set(_report_id, _path, _key):
            state["current"] = "ren-new"  # A concurrent rerender selected its new rendition first.
            return False

        with patch.object(rr, "fetch_current_rendition", no_rendition), \
             patch.object(rr, "fetch_report_doc", fake_doc), \
             patch.object(rr, "get_object_storage", return_value=_Storage()), \
             patch.object(rr, "render_report_pdf", return_value=b"%PDF base"), \
             patch.object(rr, "persist_generated_pdf", fake_persist), \
             patch.object(rr, "update_current_base_pdf_location", lost_compare_and_set):
            response = _authed().get(f"/api/report-doc/{report_id}/pdf")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(state["current"], "ren-new")

    def test_missing_rendition_repair_lost_race_never_restores_stale_current_pointer(self):
        report_id = "123e4567-e89b-12d3-a456-426614174000"
        state = {"current": "ren-old"}

        class _Storage:
            enabled = True
            mode = "r2"

            def head_object(self, key):
                raise ObjectNotFound(key)

            def presign_get(self, _key):
                raise AssertionError("lost repair race must not sign stale rendition")

        async def fake_current(_rid):
            return {
                "id": "ren-old", "pdf_path": None,
                "pdf_object_key": generated_object_key_for_sha(report_id, "a" * 64, "ren-old"),
                "template_id": "old", "renderer": "weasyprint",
            }

        async def fake_doc(_rid):
            return {"markdown": "# R", "title": "T", "locale": "en"}

        async def fake_persist(_rid, _pdf, **_kwargs):
            return None, "generated/rebuilt/rendition.pdf"

        async def lost_compare_and_set(_report_id, _rendition_id, _path, _key):
            state["current"] = "ren-new"  # Interleaved rerender won before stale repair's CAS.
            return False

        async def must_not_reset(*_args):
            raise AssertionError("stale repair must never set current_rendition_id")

        with patch.object(rr, "fetch_current_rendition", fake_current), \
             patch.object(rr, "fetch_report_doc", fake_doc), \
             patch.object(rr, "get_object_storage", return_value=_Storage()), \
             patch.object(rr, "render_report_pdf", return_value=b"%PDF repaired"), \
             patch.object(rr, "persist_generated_pdf", fake_persist), \
             patch.object(rr, "update_current_rendition_pdf_location", lost_compare_and_set), \
             patch.object(rr, "set_current_rendition", must_not_reset):
            response = _authed().get(f"/api/report-doc/{report_id}/pdf")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(state["current"], "ren-new")

    def test_bucket_failure_is_503_and_hybrid_never_falls_back_local(self):
        import tempfile

        class _Storage:
            enabled = True
            mode = "hybrid"

            def head_object(self, key):
                raise ObjectStorageError("NoSuchBucket")

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(b"%PDF local")
            local = f.name

        async def fake_current(rid):
            return {
                "id": "ren-1", "pdf_path": local,
                "pdf_object_key": generated_object_key_for_sha(
                    "123e4567-e89b-12d3-a456-426614174000", "a" * 64, "ren-1"
                ), "template_id": "ib-classic",
            }

        try:
            with patch.object(rr, "fetch_current_rendition", fake_current), \
                 patch.object(rr, "get_object_storage", return_value=_Storage()):
                r = _authed().get("/api/report-doc/123e4567-e89b-12d3-a456-426614174000/pdf")
            self.assertEqual(r.status_code, 503)
        finally:
            os.unlink(local)

    def test_r2_missing_base_key_rebuilds_and_redirects(self):
        captured = {}

        class _Storage:
            enabled = True
            mode = "r2"

            def head_object(self, key):
                raise ObjectNotFound(key)

            def presign_get(self, key):
                captured["presigned"] = key
                return "https://private.example.test/base-rebuilt"

        async def fake_current(rid):
            return None

        async def fake_doc(rid):
            return {
                "pdf_path": None, "pdf_object_key": generated_object_key_for_sha(
                    "123e4567-e89b-12d3-a456-426614174000", "a" * 64
                ),
                "markdown": "# R", "title": "T", "locale": "en",
                "template_id": "base-template", "renderer": "weasyprint",
            }

        async def fake_persist(rid, pdf, **kwargs):
            captured["persist"] = kwargs
            return None, "generated/doc/base-new.pdf"

        async def fake_update(rid, path, key):
            captured["updated"] = (path, key)
            return True

        with patch.object(rr, "fetch_current_rendition", fake_current), \
             patch.object(rr, "fetch_report_doc", fake_doc), \
             patch.object(rr, "get_object_storage", return_value=_Storage()), \
             patch.object(rr, "render_report_pdf", return_value=b"%PDF rebuilt") as render, \
             patch.object(rr, "persist_generated_pdf", fake_persist), \
             patch.object(rr, "update_current_base_pdf_location", fake_update):
            r = _authed().get("/api/report-doc/123e4567-e89b-12d3-a456-426614174000/pdf")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(captured["persist"], {"rendition_id": None, "suffix": ""})
        self.assertEqual(captured["updated"], (None, "generated/doc/base-new.pdf"))
        self.assertEqual(render.call_args.kwargs["renderer"], "weasyprint")

    def test_r2_legacy_doc_without_key_rebuilds_and_redirects(self):
        captured = {}

        class _Storage:
            enabled = True
            mode = "r2"

            def presign_get(self, key):
                captured["presigned"] = key
                return "https://private.example.test/rebuilt"

        async def fake_current(rid):
            return None

        async def fake_doc(rid):
            return {
                "pdf_path": "/must-not-read.pdf", "pdf_object_key": None,
                "markdown": "# Legacy", "title": "T", "date": "d", "question": "q",
                "locale": "zh-Hant", "template_id": "ib-classic",
            }

        async def fake_persist(rid, pdf, **_kwargs):
            captured["rendered"] = pdf
            return None, "generated/doc/base-aaaaaaaaaaaa.pdf"

        async def fake_update(rid, path, key):
            captured["updated"] = (path, key)
            return True

        with patch.object(rr, "fetch_current_rendition", fake_current), \
             patch.object(rr, "fetch_report_doc", fake_doc), \
             patch.object(rr, "get_object_storage", return_value=_Storage()), \
             patch.object(rr, "render_report_pdf", return_value=b"%PDF rebuilt"), \
             patch.object(rr, "persist_generated_pdf", fake_persist), \
             patch.object(rr, "update_current_base_pdf_location", fake_update):
            r = _authed().get("/api/report-doc/123e4567-e89b-12d3-a456-426614174000/pdf")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.headers["location"], "https://private.example.test/rebuilt")
        self.assertEqual(captured["updated"], (None, "generated/doc/base-aaaaaaaaaaaa.pdf"))

    def test_r2_legacy_rebuild_r2_failure_is_503(self):
        class _Storage:
            enabled = True
            mode = "r2"

        async def fake_current(rid):
            return None

        async def fake_doc(rid):
            return {"pdf_path": "/must-not-read.pdf", "pdf_object_key": None, "markdown": "# L", "title": "T"}

        async def fail_persist(rid, pdf, **_kwargs):
            raise ObjectStorageError("NoSuchBucket")

        with patch.object(rr, "fetch_current_rendition", fake_current), \
             patch.object(rr, "fetch_report_doc", fake_doc), \
             patch.object(rr, "get_object_storage", return_value=_Storage()), \
             patch.object(rr, "render_report_pdf", return_value=b"%PDF rebuilt"), \
             patch.object(rr, "persist_generated_pdf", fail_persist):
            r = _authed().get("/api/report-doc/123e4567-e89b-12d3-a456-426614174000/pdf")
        self.assertEqual(r.status_code, 503)

    def test_r2_missing_current_rendition_rebuilds_that_rendition(self):
        captured = {}

        class _Storage:
            enabled = True
            mode = "r2"

            def head_object(self, key):
                raise ObjectNotFound(key)

            def presign_get(self, key):
                captured["presigned"] = key
                return "https://private.example.test/rendition-rebuilt"

        async def fake_current(_rid):
            return {
                "id": "ren-current",
                "pdf_path": None,
                "pdf_object_key": generated_object_key_for_sha(
                    "123e4567-e89b-12d3-a456-426614174000", "a" * 64, "ren-current"
                ),
                "template_id": "broker-modern",
                "renderer": "weasyprint",
            }

        async def fake_doc(_rid):
            return {
                "markdown": "# R", "title": "T", "locale": "en",
                "template_id": "base-template", "renderer": "typst",
            }

        async def fake_persist(_rid, _pdf, **kwargs):
            captured["persist"] = kwargs
            return None, "generated/doc/renditions/ren-current-new.pdf"

        async def fake_update(report_id, rendition_id, path, key):
            captured["updated"] = (report_id, rendition_id, path, key)
            return True

        with patch.object(rr, "fetch_current_rendition", fake_current), \
             patch.object(rr, "fetch_report_doc", fake_doc), \
             patch.object(rr, "get_object_storage", return_value=_Storage()), \
             patch.object(rr, "render_report_pdf", return_value=b"%PDF rendition") as render, \
             patch.object(rr, "persist_generated_pdf", fake_persist), \
             patch.object(rr, "update_current_rendition_pdf_location", fake_update):
            r = _authed().get("/api/report-doc/123e4567-e89b-12d3-a456-426614174000/pdf")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(captured["persist"]["rendition_id"], "ren-current")
        self.assertEqual(
            captured["updated"],
            (
                "123e4567-e89b-12d3-a456-426614174000",
                "ren-current",
                None,
                "generated/doc/renditions/ren-current-new.pdf",
            ),
        )
        self.assertEqual(render.call_args.kwargs["renderer"], "weasyprint")
        self.assertEqual(render.call_args.kwargs["locale"], "en")

    def test_missing_persisted_renderer_fails_closed_without_replacement_upload(self):
        class _Storage:
            enabled = True
            mode = "r2"

            def head_object(self, key):
                raise ObjectNotFound(key)

        async def fake_current(_rid):
            return {
                "id": "ren-1", "pdf_path": None, "pdf_object_key": generated_object_key_for_sha(
                    "123e4567-e89b-12d3-a456-426614174000", "a" * 64, "ren-1"
                ),
                "template_id": "t", "renderer": "weasyprint",
            }

        async def fake_doc(_rid):
            return {"markdown": "# R", "title": "T", "locale": "en"}

        async def fail_persist(*_args, **_kwargs):
            raise AssertionError("must not upload a different renderer")

        with patch.object(rr, "fetch_current_rendition", fake_current), \
             patch.object(rr, "fetch_report_doc", fake_doc), \
             patch.object(rr, "get_object_storage", return_value=_Storage()), \
             patch.object(rr, "render_report_pdf", side_effect=RuntimeError("persisted renderer down")), \
             patch.object(rr, "persist_generated_pdf", fail_persist):
            response = _authed().get("/api/report-doc/123e4567-e89b-12d3-a456-426614174000/pdf")
        self.assertEqual(response.status_code, 503)

    def test_hybrid_missing_current_rendition_without_local_copy_rebuilds_rendition(self):
        captured = {}

        class _Storage:
            enabled = True
            mode = "hybrid"

            def head_object(self, key):
                raise ObjectNotFound(key)

            def presign_get(self, _key):
                return "https://private.example.test/hybrid-rendition"

        async def fake_current(_rid):
            return {
                "id": "ren-1", "pdf_path": None,
                "pdf_object_key": generated_object_key_for_sha(
                    "123e4567-e89b-12d3-a456-426614174000", "a" * 64, "ren-1"
                ), "template_id": "t",
            }

        async def fake_doc(_rid):
            return {"markdown": "# R", "title": "T", "locale": "zh-Hant", "template_id": "base"}

        async def fake_persist(_rid, _pdf, **kwargs):
            captured["rendition_id"] = kwargs["rendition_id"]
            return "/tmp/new-rendition.pdf", "generated/new.pdf"

        async def fake_update(*args):
            captured["updated"] = args
            return True

        with patch.object(rr, "fetch_current_rendition", fake_current), \
             patch.object(rr, "fetch_report_doc", fake_doc), \
             patch.object(rr, "get_object_storage", return_value=_Storage()), \
             patch.object(rr, "render_report_pdf", return_value=b"%PDF rendition"), \
             patch.object(rr, "persist_generated_pdf", fake_persist), \
             patch.object(rr, "update_current_rendition_pdf_location", fake_update):
            r = _authed().get("/api/report-doc/123e4567-e89b-12d3-a456-426614174000/pdf")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(captured["rendition_id"], "ren-1")
        self.assertEqual(captured["updated"][1], "ren-1")

    def test_generated_base_wrong_owner_is_not_headed_or_served(self):
        report_id = "123e4567-e89b-12d3-a456-426614174000"

        class _Storage:
            enabled = True
            mode = "hybrid"

            def head_object(self, _key):
                raise AssertionError("foreign base pointer must fail before HEAD")

            def presign_get(self, _key):
                raise AssertionError("foreign base pointer must never be signed")

        async def no_rendition(_rid):
            return None

        async def fake_doc(_rid):
            return {
                "pdf_path": "/must-not-read.pdf",
                # Same 12-char digest shape, but belonging to another report.
                "pdf_object_key": generated_object_key_for_sha("other-report", "a" * 64),
            }

        with patch.object(rr, "fetch_current_rendition", no_rendition), \
             patch.object(rr, "fetch_report_doc", fake_doc), \
             patch.object(rr, "get_object_storage", return_value=_Storage()):
            response = _authed().get(f"/api/report-doc/{report_id}/pdf")
        self.assertEqual(response.status_code, 503)

    def test_generated_rendition_wrong_owner_or_path_is_not_headed_or_served(self):
        report_id = "123e4567-e89b-12d3-a456-426614174000"

        class _Storage:
            enabled = True
            mode = "r2"

            def head_object(self, _key):
                raise AssertionError("foreign rendition pointer must fail before HEAD")

        async def fake_doc(_rid):
            return {"markdown": "# R", "title": "T"}

        for wrong_key in (
            generated_object_key_for_sha("other-report", "a" * 64, "ren-1"),
            # The report owner matches, but the key is a base object rather than ren-1.
            generated_object_key_for_sha(report_id, "a" * 64),
        ):
            async def fake_current(_rid, key=wrong_key):
                return {"id": "ren-1", "pdf_path": None, "pdf_object_key": key, "template_id": "t"}

            with self.subTest(key=wrong_key), \
                 patch.object(rr, "fetch_current_rendition", fake_current), \
                 patch.object(rr, "fetch_report_doc", fake_doc), \
                 patch.object(rr, "get_object_storage", return_value=_Storage()):
                response = _authed().get(f"/api/report-doc/{report_id}/pdf")
            self.assertEqual(response.status_code, 503)

    def test_generated_metadata_digest_must_match_db_pointer_before_presign(self):
        report_id = "123e4567-e89b-12d3-a456-426614174000"
        pointer_sha = "a" * 64
        remote_sha = "b" * 64
        key = generated_object_key_for_sha(report_id, pointer_sha)

        class _Storage:
            enabled = True
            mode = "r2"

            def head_object(self, seen_key):
                if seen_key != key:
                    raise AssertionError(f"unexpected key: {seen_key}")
                return {"Metadata": {"sha256": remote_sha}}

            def presign_get(self, _key):
                raise AssertionError("metadata/key mismatch must never be signed")

        async def no_rendition(_rid):
            return None

        async def fake_doc(_rid):
            return {"pdf_path": None, "pdf_object_key": key}

        with patch.object(rr, "fetch_current_rendition", no_rendition), \
             patch.object(rr, "fetch_report_doc", fake_doc), \
             patch.object(rr, "get_object_storage", return_value=_Storage()):
            response = _authed().get(f"/api/report-doc/{report_id}/pdf")
        self.assertEqual(response.status_code, 503)

    def test_r2_base_service_failure_is_503_not_local_fallback(self):
        class _Storage:
            enabled = True
            mode = "r2"

            def head_object(self, _key):
                raise ObjectStorageError("NoSuchBucket")

        async def no_rendition(_rid):
            return None

        async def fake_doc(_rid):
            return {
                "pdf_path": "/must-not-read.pdf",
                "pdf_object_key": generated_object_key_for_sha(
                    "123e4567-e89b-12d3-a456-426614174000", "a" * 64
                ),
            }

        with patch.object(rr, "fetch_current_rendition", no_rendition), \
             patch.object(rr, "fetch_report_doc", fake_doc), \
             patch.object(rr, "get_object_storage", return_value=_Storage()):
            r = _authed().get("/api/report-doc/123e4567-e89b-12d3-a456-426614174000/pdf")
        self.assertEqual(r.status_code, 503)


if __name__ == "__main__":
    unittest.main()
