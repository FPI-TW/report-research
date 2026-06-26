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

    async def test_enables_web_by_default(self):
        """REPORT_ENABLE_WEB 預設開，且以 allow_web=True 呼叫 stream_completion。"""
        self.assertTrue(rpt.REPORT_ENABLE_WEB)

        async def fake_search(session, q, qvec, **k):
            return []

        captured = {}

        async def fake_stream(*a, **k):
            captured["allow_web"] = k.get("allow_web")
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
            _ = [e async for e in rpt.generate_report("分析材料行業")]
        finally:
            (
                rpt.hybrid_search, rpt.embed_query_cached, rpt.build_context,
                rpt.stream_completion, rpt.render_report_pdf, rpt.write_report_pdf,
                rpt.persist_report_doc, rpt.SessionFactory,
            ) = orig

        self.assertIs(captured.get("allow_web"), True)

    async def test_search_event_emits_searching_web_status(self):
        """串流中出現 SEARCH_EVENT → 事件序含 status searching_web（只發一次）。"""

        async def fake_search(session, q, qvec, **k):
            return []

        async def fake_stream(*a, **k):
            yield rpt.SEARCH_EVENT
            yield "## 執行摘要\n重點[1]（網路）"
            yield rpt.SEARCH_EVENT

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
            events = [e async for e in rpt.generate_report("分析材料行業")]
        finally:
            (
                rpt.hybrid_search, rpt.embed_query_cached, rpt.build_context,
                rpt.stream_completion, rpt.render_report_pdf, rpt.write_report_pdf,
                rpt.persist_report_doc, rpt.SessionFactory,
            ) = orig

        statuses = [p.get("stage") for (k, p) in events if k == "status"]
        self.assertEqual(statuses.count("searching_web"), 1)
        # SEARCH_EVENT 不可被當成研報內文 token
        tokens = "".join(p for (k, p) in events if k == "token")
        self.assertNotIn(rpt.SEARCH_EVENT, tokens)

    async def test_empty_context_with_web_proceeds(self):
        """空脈絡 + 網搜開 → 不回 error，照常生成到 done（由模型上網補）。"""

        async def fake_search(session, q, qvec, **k):
            return []

        async def fake_stream(*a, **k):
            yield "## 執行摘要\n全由網路整理[1]（網路）"

        async def fake_persist(*a, **k):
            return None

        orig = (
            rpt.hybrid_search, rpt.embed_query_cached, rpt.build_context,
            rpt.stream_completion, rpt.render_report_pdf, rpt.write_report_pdf,
            rpt.persist_report_doc, rpt.SessionFactory, rpt.REPORT_ENABLE_WEB,
        )
        rpt.hybrid_search = fake_search
        rpt.embed_query_cached = lambda q: [0.0]
        rpt.build_context = lambda scored, **k: ([], "")
        rpt.stream_completion = fake_stream
        rpt.render_report_pdf = lambda md, **k: b"%PDF-1.4 fake"
        rpt.write_report_pdf = lambda rid, b: f"/tmp/{rid}.pdf"
        rpt.persist_report_doc = fake_persist
        rpt.SessionFactory = lambda: _FakeSession()
        rpt.REPORT_ENABLE_WEB = True
        try:
            events = [e async for e in rpt.generate_report("分析材料行業")]
        finally:
            (
                rpt.hybrid_search, rpt.embed_query_cached, rpt.build_context,
                rpt.stream_completion, rpt.render_report_pdf, rpt.write_report_pdf,
                rpt.persist_report_doc, rpt.SessionFactory, rpt.REPORT_ENABLE_WEB,
            ) = orig

        kinds = [e[0] for e in events]
        self.assertNotIn("error", kinds)
        self.assertEqual(kinds[-1], "done")

    async def test_system_prompt_allows_web_and_external_refs(self):
        """REPORT_SYSTEM_PROMPT 立場已改：允許網搜補充、要求外部參考段與（網路）標註。"""
        p = rpt.REPORT_SYSTEM_PROMPT
        self.assertIn("網路搜尋", p)
        self.assertIn("外部參考（網路）", p)
        self.assertIn("（網路）", p)
        self.assertNotIn("僅根據", p)  # 舊「僅根據參考片段」立場已移除

    async def test_status_resets_to_writing_after_search(self):
        """SEARCH_EVENT 後應重設回 writing 狀態，不讓「搜尋網路補充…」卡住整個撰寫段。"""

        async def fake_search(session, q, qvec, **k):
            return []

        async def fake_stream(*a, **k):
            yield rpt.SEARCH_EVENT
            yield "## 執行摘要\n內容[1]（網路）"

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
            events = [e async for e in rpt.generate_report("分析材料行業")]
        finally:
            (
                rpt.hybrid_search, rpt.embed_query_cached, rpt.build_context,
                rpt.stream_completion, rpt.render_report_pdf, rpt.write_report_pdf,
                rpt.persist_report_doc, rpt.SessionFactory,
            ) = orig

        stages = [p["stage"] for (k, p) in events if k == "status"]
        i = stages.index("searching_web")
        self.assertIn("writing", stages[i + 1:])  # 搜尋後重設回 writing
        self.assertLess(stages.index("searching_web"), stages.index("rendering"))

    async def test_empty_context_without_web_emits_error(self):
        """空脈絡 + 網搜關 → 仍回 error（守住舊行為）。"""
        async def fake_search(session, q, qvec, **k):
            return []

        orig = (
            rpt.hybrid_search, rpt.embed_query_cached, rpt.build_context,
            rpt.SessionFactory, rpt.REPORT_ENABLE_WEB,
        )
        rpt.hybrid_search = fake_search
        rpt.embed_query_cached = lambda q: [0.0]
        rpt.build_context = lambda scored, **k: ([], "")
        rpt.SessionFactory = lambda: _FakeSession()
        rpt.REPORT_ENABLE_WEB = False
        try:
            events = [e async for e in rpt.generate_report("隨便問")]
        finally:
            (
                rpt.hybrid_search, rpt.embed_query_cached, rpt.build_context,
                rpt.SessionFactory, rpt.REPORT_ENABLE_WEB,
            ) = orig

        self.assertEqual(events[-1][0], "error")
