# tests/test_template_registry.py
"""M9b-1 模板 registry + template_id 貫穿。

manifest 解析（含 fail-safe）、emit_typst 依 template 換 import、render 分派把
template_id 傳給 typst、/api/report-templates 端點、/api/report 把 template_id 轉發
給 generate_report。實際 typst 編譯另有 test_typst_render 覆蓋。
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

from app.templates import manifest  # noqa: E402


class ManifestTests(unittest.TestCase):
    def test_default_is_ib_classic(self):
        self.assertEqual(manifest.default_template().id, "ib-classic")
        self.assertTrue(manifest.default_template().is_default)

    def test_resolve_none_and_unknown_fall_back_to_default(self):
        d = manifest.default_template().id
        self.assertEqual(manifest.resolve(None).id, d)
        self.assertEqual(manifest.resolve("").id, d)
        self.assertEqual(manifest.resolve("does-not-exist").id, d)

    def test_resolve_known_id(self):
        self.assertEqual(manifest.resolve("ib-classic").id, "ib-classic")

    def test_list_templates_nonempty_with_filenames(self):
        ts = manifest.list_templates()
        self.assertTrue(ts)
        for t in ts:
            self.assertTrue(t.filename.endswith(".typ"))
            # registry 登錄的 .typ 檔須真的存在
            self.assertTrue(
                (REPO_ROOT / "app" / "templates" / t.filename).is_file(), t.filename
            )
        self.assertEqual(sum(1 for t in ts if t.is_default), 1)  # 恰一個預設


class EmitTypstTemplateTests(unittest.TestCase):
    def test_emit_uses_given_template_import_path(self):
        from app.services.typst_render import DocumentModel, DocMeta, emit_typst

        doc = DocumentModel(sections=(), meta=DocMeta(title="t", date="d", question="q"))
        src = emit_typst(doc, disclaimer="免責", template_import_path="/app/templates/foo.typ")
        self.assertIn('#import "/app/templates/foo.typ":', src)

    def test_emit_default_import_is_ib_classic(self):
        from app.services.typst_render import DocumentModel, DocMeta, emit_typst

        doc = DocumentModel(sections=(), meta=DocMeta(title="t", date="d", question="q"))
        src = emit_typst(doc, disclaimer="免責")
        self.assertIn("/app/templates/ib-classic.typ", src)


class RenderDispatchTemplateTests(unittest.TestCase):
    def test_dispatch_forwards_template_id_to_typst(self):
        from app.services import report as rpt

        seen = {}

        def fake_typst(markdown_text, *, title, meta, template_id=None):
            seen["template_id"] = template_id
            return b"%PDF-typst"

        with patch.object(rpt, "REPORT_RENDERER", "typst"), \
             patch("app.services.typst_render.render_report_pdf", fake_typst):
            out = rpt.render_report_pdf(
                "# x", title="T", meta={"date": "d"}, template_id="broker-modern"
            )
        self.assertEqual(out, b"%PDF-typst")
        self.assertEqual(seen["template_id"], "broker-modern")


class TemplatesEndpointTests(unittest.TestCase):
    def _authed(self):
        from fastapi.testclient import TestClient
        from web.server import app

        c = TestClient(app, follow_redirects=False, base_url="http://127.0.0.1")
        r = c.post("/login", data={"username": "tester", "password": "testpass"})
        assert r.status_code == 303, r.status_code
        return c

    def test_list_endpoint_requires_login(self):
        from fastapi.testclient import TestClient
        from web.server import app

        c = TestClient(app, follow_redirects=False, base_url="http://127.0.0.1")
        self.assertEqual(c.get("/api/report-templates").status_code, 401)

    def test_list_endpoint_returns_registry(self):
        r = self._authed().get("/api/report-templates")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        ids = [t["id"] for t in body["templates"]]
        self.assertIn("ib-classic", ids)
        self.assertTrue(any(t["is_default"] for t in body["templates"]))


class ReportRequestThreadingTests(unittest.IsolatedAsyncioTestCase):
    async def test_report_forwards_template_id_to_generate_report(self):
        from fastapi.testclient import TestClient
        from web.routers import report as rr
        from web.server import app

        seen = {}

        async def fake_generate(question, **kwargs):
            seen.update(kwargs)
            yield ("done", {"qa_id": None})

        c = TestClient(app, follow_redirects=False, base_url="http://127.0.0.1")
        c.post("/login", data={"username": "tester", "password": "testpass"})
        with patch.object(rr, "generate_report", fake_generate):
            r = c.post("/api/report", json={"question": "台積電", "template_id": "broker-modern"})
            _ = r.text  # drain SSE
        self.assertEqual(seen.get("template_id"), "broker-modern")

    async def test_report_template_id_defaults_none(self):
        from fastapi.testclient import TestClient
        from web.routers import report as rr
        from web.server import app

        seen = {}

        async def fake_generate(question, **kwargs):
            seen.update(kwargs)
            yield ("done", {"qa_id": None})

        c = TestClient(app, follow_redirects=False, base_url="http://127.0.0.1")
        c.post("/login", data={"username": "tester", "password": "testpass"})
        with patch.object(rr, "generate_report", fake_generate):
            r = c.post("/api/report", json={"question": "台積電"})
            _ = r.text
        self.assertIsNone(seen.get("template_id"))  # 未帶 → None（下游 manifest 解析為預設）


if __name__ == "__main__":
    unittest.main()
