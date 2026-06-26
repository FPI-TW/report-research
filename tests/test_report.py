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

    async def test_forwards_generous_timeout_to_stream_completion(self):
        """研報為長輸出：須以 > Q&A 預設(120s) 的逾時呼叫 stream_completion，

        否則在 120s 被 _run_attempt 靜默截斷（streamed_any→return），導致研報寫到一半就結束。
        """

        async def fake_search(session, q, qvec, **k):
            return []

        captured = {}

        async def fake_stream(*a, **k):
            captured["timeout"] = k.get("timeout")
            yield "## 執行摘要\n重點[1]"

        async def fake_persist(*a, **k):
            return None

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
            _ = [e async for e in rpt.generate_report("請分析台積電趨勢")]
        finally:
            (
                rpt.hybrid_search, rpt.embed_query_cached, rpt.build_context,
                rpt.stream_completion, rpt.render_report_pdf, rpt.write_report_pdf,
                rpt.persist_report_doc, rpt.SessionFactory,
            ) = orig

        self.assertIsNotNone(captured.get("timeout"), "未傳 timeout → 沿用 120s 預設會截斷")
        self.assertEqual(captured["timeout"], rpt.REPORT_TIMEOUT)
        self.assertGreater(rpt.REPORT_TIMEOUT, 120.0)

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
