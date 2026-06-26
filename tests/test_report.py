import sys
import unittest
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services import report as rpt  # noqa: E402


@dataclass
class _FakeSource:
    """build_context 回傳的 sources 元素替身（generate_report 會 asdict 它）。"""

    i: int


class CoverageDirectiveTests(unittest.TestCase):
    """薄涵蓋偵測：命中研報數 < 門檻且網搜開 → 回傳「主動上網補充」指令。"""

    def test_web_off_returns_empty(self):
        self.assertEqual(rpt.coverage_directive(0, web_enabled=False), "")
        self.assertEqual(rpt.coverage_directive(3, web_enabled=False), "")

    def test_zero_reports_directs_web_primary(self):
        d = rpt.coverage_directive(0, web_enabled=True, threshold=8)
        self.assertIn("以網路搜尋為主", d)
        self.assertNotIn("僅找到", d)

    def test_thin_reports_nudges_supplement(self):
        d = rpt.coverage_directive(3, web_enabled=True, threshold=8)
        self.assertIn("僅找到 3 篇", d)
        self.assertIn("主動以網路搜尋補充", d)

    def test_at_threshold_no_directive(self):
        self.assertEqual(rpt.coverage_directive(8, web_enabled=True, threshold=8), "")

    def test_sufficient_reports_no_directive(self):
        self.assertEqual(rpt.coverage_directive(20, web_enabled=True, threshold=8), "")


class BuildReportPromptTests(unittest.TestCase):
    def test_includes_coverage_note_when_given(self):
        p = rpt.build_report_prompt("主題", "脈絡", "標題", "（涵蓋提示）")
        self.assertIn("（涵蓋提示）", p)

    def test_omits_note_when_empty(self):
        p = rpt.build_report_prompt("主題", "脈絡", "標題", "")
        self.assertEqual(p, rpt.build_report_prompt("主題", "脈絡", "標題"))


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
        # 廣度語意：不只看片段多寡，也看是否僅涵蓋局部面向
        self.assertIn("面向", p)

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

    async def test_thin_coverage_injects_web_nudge(self):
        """命中研報數 < 門檻且網搜開 → user prompt 注入「主動上網補充」指令。"""

        async def fake_search(session, q, qvec, **k):
            return []

        captured = {}

        async def fake_stream(*a, **k):
            captured["prompt"] = a[0] if a else k.get("prompt")
            yield "## 執行摘要\n內容[1]"

        async def fake_persist(*a, **k):
            return None

        orig = (
            rpt.hybrid_search, rpt.embed_query_cached, rpt.build_context,
            rpt.stream_completion, rpt.render_report_pdf, rpt.write_report_pdf,
            rpt.persist_report_doc, rpt.SessionFactory, rpt.REPORT_ENABLE_WEB,
        )
        rpt.hybrid_search = fake_search
        rpt.embed_query_cached = lambda q: [0.0]
        rpt.build_context = lambda scored, **k: ([_FakeSource(0), _FakeSource(1), _FakeSource(2)], "脈絡內容")
        rpt.stream_completion = fake_stream
        rpt.render_report_pdf = lambda md, **k: b"%PDF-1.4 fake"
        rpt.write_report_pdf = lambda rid, b: f"/tmp/{rid}.pdf"
        rpt.persist_report_doc = fake_persist
        rpt.SessionFactory = lambda: _FakeSession()
        rpt.REPORT_ENABLE_WEB = True
        try:
            _ = [e async for e in rpt.generate_report("分析材料行業")]
        finally:
            (
                rpt.hybrid_search, rpt.embed_query_cached, rpt.build_context,
                rpt.stream_completion, rpt.render_report_pdf, rpt.write_report_pdf,
                rpt.persist_report_doc, rpt.SessionFactory, rpt.REPORT_ENABLE_WEB,
            ) = orig

        self.assertIn("僅找到 3 篇", captured["prompt"])
        self.assertIn("主動以網路搜尋補充", captured["prompt"])

    async def test_sufficient_coverage_no_web_nudge(self):
        """命中研報數 ≥ 門檻 → 不注入薄涵蓋指令（避免充分涵蓋主題無謂搜尋）。"""

        async def fake_search(session, q, qvec, **k):
            return []

        captured = {}

        async def fake_stream(*a, **k):
            captured["prompt"] = a[0] if a else k.get("prompt")
            yield "## 執行摘要\n內容[1]"

        async def fake_persist(*a, **k):
            return None

        orig = (
            rpt.hybrid_search, rpt.embed_query_cached, rpt.build_context,
            rpt.stream_completion, rpt.render_report_pdf, rpt.write_report_pdf,
            rpt.persist_report_doc, rpt.SessionFactory, rpt.REPORT_ENABLE_WEB,
        )
        rpt.hybrid_search = fake_search
        rpt.embed_query_cached = lambda q: [0.0]
        rpt.build_context = lambda scored, **k: ([_FakeSource(i) for i in range(10)], "脈絡內容")
        rpt.stream_completion = fake_stream
        rpt.render_report_pdf = lambda md, **k: b"%PDF-1.4 fake"
        rpt.write_report_pdf = lambda rid, b: f"/tmp/{rid}.pdf"
        rpt.persist_report_doc = fake_persist
        rpt.SessionFactory = lambda: _FakeSession()
        rpt.REPORT_ENABLE_WEB = True
        try:
            _ = [e async for e in rpt.generate_report("分析台積電趨勢")]
        finally:
            (
                rpt.hybrid_search, rpt.embed_query_cached, rpt.build_context,
                rpt.stream_completion, rpt.render_report_pdf, rpt.write_report_pdf,
                rpt.persist_report_doc, rpt.SessionFactory, rpt.REPORT_ENABLE_WEB,
            ) = orig

        self.assertNotIn("涵蓋可能不足", captured["prompt"])
        self.assertNotIn("僅找到", captured["prompt"])
