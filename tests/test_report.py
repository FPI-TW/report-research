import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services import report as rpt  # noqa: E402


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, *a, **k):
        return None

    async def commit(self):
        return None


class GenerateReportTests(unittest.IsolatedAsyncioTestCase):
    async def test_event_sequence_and_done_payload(self):
        async def fake_search(session, q, qvec, **k):
            return []

        async def fake_stream(*a, **k):
            yield "## 執行摘要\n"
            yield "重點[1]"

        captured = {}

        async def fake_persist(*a, **k):
            captured["persisted"] = True

        orig = (
            rpt.hybrid_search, rpt.embed_query_cached, rpt.build_context,
            rpt.stream_completion, rpt.render_report_pdf, rpt.write_report_pdf,
            rpt.persist_report_doc, rpt.SessionFactory,
        )
        rpt.hybrid_search = fake_search
        rpt.embed_query_cached = lambda q: [0.0]
        rpt.build_context = lambda scored, **k: ([], "脈絡內容")
        rpt.stream_completion = fake_stream
        rpt.render_report_pdf = lambda md, **k: b"%PDF-1.4 fake"
        rpt.write_report_pdf = lambda rid, b: f"/tmp/{rid}.pdf"
        rpt.persist_report_doc = fake_persist
        rpt.SessionFactory = lambda: _FakeSession()
        try:
            events = [e async for e in rpt.generate_report("請分析台積電趨勢")]
        finally:
            (
                rpt.hybrid_search, rpt.embed_query_cached, rpt.build_context,
                rpt.stream_completion, rpt.render_report_pdf, rpt.write_report_pdf,
                rpt.persist_report_doc, rpt.SessionFactory,
            ) = orig

        kinds = [e[0] for e in events]
        self.assertEqual(kinds[0], "status")
        self.assertEqual(events[0][1]["stage"], "retrieving")
        self.assertIn("sources", kinds)
        self.assertIn("token", kinds)
        self.assertEqual(kinds[-1], "done")
        done = events[-1][1]
        self.assertIn("report_id", done)
        self.assertTrue(done["download_url"].endswith("/pdf"))
        self.assertTrue(captured.get("persisted"))

    async def test_empty_context_emits_error(self):
        async def fake_search(session, q, qvec, **k):
            return []

        orig = (rpt.hybrid_search, rpt.embed_query_cached, rpt.build_context, rpt.SessionFactory)
        rpt.hybrid_search = fake_search
        rpt.embed_query_cached = lambda q: [0.0]
        rpt.build_context = lambda scored, **k: ([], "")
        rpt.SessionFactory = lambda: _FakeSession()
        try:
            events = [e async for e in rpt.generate_report("隨便問")]
        finally:
            (rpt.hybrid_search, rpt.embed_query_cached, rpt.build_context, rpt.SessionFactory) = orig

        self.assertEqual(events[-1][0], "error")
