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
        async def fake_retrieve_context(question, **k):
            return ([], "脈絡內容")

        async def fake_stream(*a, **k):
            yield "## 執行摘要\n"
            yield "重點[1]"

        captured = {}

        async def fake_persist(*a, **k):
            captured["persisted"] = True

        orig = (
            rpt.retrieve_context,
            rpt.stream_completion, rpt.render_report_pdf, rpt.write_report_pdf,
            rpt.persist_report_doc, rpt.SessionFactory,
        )
        rpt.retrieve_context = fake_retrieve_context
        rpt.stream_completion = fake_stream
        rpt.render_report_pdf = lambda md, **k: b"%PDF-1.4 fake"
        rpt.write_report_pdf = lambda rid, b: f"/tmp/{rid}.pdf"
        rpt.persist_report_doc = fake_persist
        rpt.SessionFactory = lambda: _FakeSession()
        try:
            events = [e async for e in rpt.generate_report("請分析台積電趨勢")]
        finally:
            (
                rpt.retrieve_context,
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

        async def fake_retrieve_context(question, **k):
            return ([], "脈絡內容")

        captured = {}

        async def fake_stream(*a, **k):
            captured["timeout"] = k.get("timeout")
            yield "## 執行摘要\n重點[1]"

        async def fake_persist(*a, **k):
            return None

        orig = (
            rpt.retrieve_context,
            rpt.stream_completion, rpt.render_report_pdf, rpt.write_report_pdf,
            rpt.persist_report_doc, rpt.SessionFactory,
        )
        rpt.retrieve_context = fake_retrieve_context
        rpt.stream_completion = fake_stream
        rpt.render_report_pdf = lambda md, **k: b"%PDF-1.4 fake"
        rpt.write_report_pdf = lambda rid, b: f"/tmp/{rid}.pdf"
        rpt.persist_report_doc = fake_persist
        rpt.SessionFactory = lambda: _FakeSession()
        try:
            _ = [e async for e in rpt.generate_report("請分析台積電趨勢")]
        finally:
            (
                rpt.retrieve_context,
                rpt.stream_completion, rpt.render_report_pdf, rpt.write_report_pdf,
                rpt.persist_report_doc, rpt.SessionFactory,
            ) = orig

        self.assertIsNotNone(captured.get("timeout"), "未傳 timeout → 沿用 120s 預設會截斷")
        self.assertEqual(captured["timeout"], rpt.REPORT_TIMEOUT)
        self.assertGreater(rpt.REPORT_TIMEOUT, 120.0)

    async def test_enables_web_by_default(self):
        """REPORT_ENABLE_WEB 預設開，且以 allow_web=True 呼叫 stream_completion。"""
        self.assertTrue(rpt.REPORT_ENABLE_WEB)

        async def fake_retrieve_context(question, **k):
            return ([], "脈絡內容")

        captured = {}

        async def fake_stream(*a, **k):
            captured["allow_web"] = k.get("allow_web")
            yield "## 執行摘要\n重點[1]"

        async def fake_persist(*a, **k):
            return None

        orig = (
            rpt.retrieve_context,
            rpt.stream_completion, rpt.render_report_pdf, rpt.write_report_pdf,
            rpt.persist_report_doc, rpt.SessionFactory,
        )
        rpt.retrieve_context = fake_retrieve_context
        rpt.stream_completion = fake_stream
        rpt.render_report_pdf = lambda md, **k: b"%PDF-1.4 fake"
        rpt.write_report_pdf = lambda rid, b: f"/tmp/{rid}.pdf"
        rpt.persist_report_doc = fake_persist
        rpt.SessionFactory = lambda: _FakeSession()
        try:
            _ = [e async for e in rpt.generate_report("分析材料行業")]
        finally:
            (
                rpt.retrieve_context,
                rpt.stream_completion, rpt.render_report_pdf, rpt.write_report_pdf,
                rpt.persist_report_doc, rpt.SessionFactory,
            ) = orig

        self.assertIs(captured.get("allow_web"), True)

    async def test_search_event_emits_searching_web_status(self):
        """串流中出現 SEARCH_EVENT → 事件序含 status searching_web（只發一次）。"""

        async def fake_retrieve_context(question, **k):
            return ([], "脈絡內容")

        async def fake_stream(*a, **k):
            yield rpt.SEARCH_EVENT
            yield "## 執行摘要\n重點[1]（網路）"
            yield rpt.SEARCH_EVENT

        async def fake_persist(*a, **k):
            return None

        orig = (
            rpt.retrieve_context,
            rpt.stream_completion, rpt.render_report_pdf, rpt.write_report_pdf,
            rpt.persist_report_doc, rpt.SessionFactory,
        )
        rpt.retrieve_context = fake_retrieve_context
        rpt.stream_completion = fake_stream
        rpt.render_report_pdf = lambda md, **k: b"%PDF-1.4 fake"
        rpt.write_report_pdf = lambda rid, b: f"/tmp/{rid}.pdf"
        rpt.persist_report_doc = fake_persist
        rpt.SessionFactory = lambda: _FakeSession()
        try:
            events = [e async for e in rpt.generate_report("分析材料行業")]
        finally:
            (
                rpt.retrieve_context,
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

        async def fake_retrieve_context(question, **k):
            return ([], "")

        async def fake_stream(*a, **k):
            yield "## 執行摘要\n全由網路整理[1]（網路）"

        async def fake_persist(*a, **k):
            return None

        orig = (
            rpt.retrieve_context,
            rpt.stream_completion, rpt.render_report_pdf, rpt.write_report_pdf,
            rpt.persist_report_doc, rpt.SessionFactory, rpt.REPORT_ENABLE_WEB,
        )
        rpt.retrieve_context = fake_retrieve_context
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
                rpt.retrieve_context,
                rpt.stream_completion, rpt.render_report_pdf, rpt.write_report_pdf,
                rpt.persist_report_doc, rpt.SessionFactory, rpt.REPORT_ENABLE_WEB,
            ) = orig

        kinds = [e[0] for e in events]
        self.assertNotIn("error", kinds)
        self.assertEqual(kinds[-1], "done")

    async def test_system_prompt_has_chart_rule(self):
        """REPORT_SYSTEM_PROMPT 含圖表指令：適時輸出 ```chart、數據不得杜撰。"""
        p = rpt.REPORT_SYSTEM_PROMPT
        self.assertIn("```chart", p)
        self.assertIn("不得杜撰", p)

    async def test_system_prompt_allows_web_and_external_refs(self):
        """REPORT_SYSTEM_PROMPT 立場已改：允許網搜補充、要求外部參考段與（網路）標註。"""
        p = rpt.REPORT_SYSTEM_PROMPT
        self.assertIn("網路搜尋", p)
        self.assertIn("外部參考（網路）", p)
        self.assertIn("（網路）", p)
        self.assertNotIn("僅根據", p)  # 舊「僅根據參考片段」立場已移除
        # 廣度語意：不只看片段多寡，也看是否僅涵蓋局部面向
        self.assertIn("面向", p)

    async def test_system_prompt_forbids_preamble(self):
        """REPORT_SYSTEM_PROMPT 要求首字即 # 標題、不要流程旁白前言。"""
        p = rpt.REPORT_SYSTEM_PROMPT
        self.assertIn("第一個字元", p)
        self.assertIn("前言", p)

    async def test_system_prompt_has_kpi_and_callout(self):
        """REPORT_SYSTEM_PROMPT 含 KPI 卡片與引言 callout 規則。"""
        p = rpt.REPORT_SYSTEM_PROMPT
        self.assertIn("```kpi", p)
        self.assertIn("3–5 個可比較", p)
        self.assertIn('"items"', p)
        self.assertIn('"source"', p)
        self.assertIn("每個 item", p)
        self.assertIn("單一研報編號或網路來源", p)
        self.assertIn("引言", p)
        self.assertIn("不得杜撰", p)  # KPI 沿用嚴格接地措辭

    async def test_status_resets_to_writing_after_search(self):
        """SEARCH_EVENT 後應重設回 writing 狀態，不讓「搜尋網路補充…」卡住整個撰寫段。"""

        async def fake_retrieve_context(question, **k):
            return ([], "脈絡內容")

        async def fake_stream(*a, **k):
            yield rpt.SEARCH_EVENT
            yield "## 執行摘要\n內容[1]（網路）"

        async def fake_persist(*a, **k):
            return None

        orig = (
            rpt.retrieve_context,
            rpt.stream_completion, rpt.render_report_pdf, rpt.write_report_pdf,
            rpt.persist_report_doc, rpt.SessionFactory,
        )
        rpt.retrieve_context = fake_retrieve_context
        rpt.stream_completion = fake_stream
        rpt.render_report_pdf = lambda md, **k: b"%PDF-1.4 fake"
        rpt.write_report_pdf = lambda rid, b: f"/tmp/{rid}.pdf"
        rpt.persist_report_doc = fake_persist
        rpt.SessionFactory = lambda: _FakeSession()
        try:
            events = [e async for e in rpt.generate_report("分析材料行業")]
        finally:
            (
                rpt.retrieve_context,
                rpt.stream_completion, rpt.render_report_pdf, rpt.write_report_pdf,
                rpt.persist_report_doc, rpt.SessionFactory,
            ) = orig

        stages = [p["stage"] for (k, p) in events if k == "status"]
        i = stages.index("searching_web")
        self.assertIn("writing", stages[i + 1:])  # 搜尋後重設回 writing
        self.assertLess(stages.index("searching_web"), stages.index("rendering"))

    async def test_persisted_markdown_strips_preamble(self):
        """串流首段為流程旁白時，持久化（與渲染）的 markdown 應已去旁白，首字即 # 標題。"""
        captured = {}

        async def fake_retrieve_context(question, **k):
            return ([], "脈絡內容")

        async def fake_stream(*a, **k):
            yield "好的，現在我來進行網路搜尋補充材料行業資料。"
            yield "已取得資料，現在整合所有片段撰寫研報。\n\n"
            yield "# 材料行業深度研報\n\n## 執行摘要\n\n內文[1]。"

        async def fake_persist(report_id, qa_id, conv, question, title, markdown, *a, **k):
            captured["markdown"] = markdown

        def fake_render(md, **k):
            captured["rendered"] = md
            return b"%PDF-1.4 fake"

        orig = (
            rpt.retrieve_context,
            rpt.stream_completion, rpt.render_report_pdf, rpt.write_report_pdf,
            rpt.persist_report_doc, rpt.SessionFactory,
        )
        rpt.retrieve_context = fake_retrieve_context
        rpt.stream_completion = fake_stream
        rpt.render_report_pdf = fake_render
        rpt.write_report_pdf = lambda rid, b: f"/tmp/{rid}.pdf"
        rpt.persist_report_doc = fake_persist
        rpt.SessionFactory = lambda: _FakeSession()
        try:
            _ = [e async for e in rpt.generate_report("分析材料行業")]
        finally:
            (
                rpt.retrieve_context,
                rpt.stream_completion, rpt.render_report_pdf, rpt.write_report_pdf,
                rpt.persist_report_doc, rpt.SessionFactory,
            ) = orig

        self.assertTrue(captured["markdown"].startswith("# 材料行業深度研報"))
        self.assertNotIn("好的，現在我來", captured["markdown"])
        self.assertNotIn("好的，現在我來", captured["rendered"])

    async def test_empty_context_without_web_emits_error(self):
        """空脈絡 + 網搜關 → 仍回 error（守住舊行為）。"""
        async def fake_retrieve_context(question, **k):
            return ([], "")

        orig = (
            rpt.retrieve_context,
            rpt.SessionFactory, rpt.REPORT_ENABLE_WEB,
        )
        rpt.retrieve_context = fake_retrieve_context
        rpt.SessionFactory = lambda: _FakeSession()
        rpt.REPORT_ENABLE_WEB = False
        try:
            events = [e async for e in rpt.generate_report("隨便問")]
        finally:
            (
                rpt.retrieve_context,
                rpt.SessionFactory, rpt.REPORT_ENABLE_WEB,
            ) = orig

        self.assertEqual(events[-1][0], "error")

    async def test_thin_coverage_injects_web_nudge(self):
        """命中研報數 < 門檻且網搜開 → user prompt 注入「主動上網補充」指令。"""

        async def fake_retrieve_context(question, **k):
            return ([_FakeSource(0), _FakeSource(1), _FakeSource(2)], "脈絡內容")

        captured = {}

        async def fake_stream(*a, **k):
            captured["prompt"] = a[0] if a else k.get("prompt")
            yield "## 執行摘要\n內容[1]"

        async def fake_persist(*a, **k):
            return None

        orig = (
            rpt.retrieve_context,
            rpt.stream_completion, rpt.render_report_pdf, rpt.write_report_pdf,
            rpt.persist_report_doc, rpt.SessionFactory, rpt.REPORT_ENABLE_WEB,
        )
        rpt.retrieve_context = fake_retrieve_context
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
                rpt.retrieve_context,
                rpt.stream_completion, rpt.render_report_pdf, rpt.write_report_pdf,
                rpt.persist_report_doc, rpt.SessionFactory, rpt.REPORT_ENABLE_WEB,
            ) = orig

        self.assertIn("僅找到 3 篇", captured["prompt"])
        self.assertIn("主動以網路搜尋補充", captured["prompt"])

    async def test_sufficient_coverage_no_web_nudge(self):
        """命中研報數 ≥ 門檻 → 不注入薄涵蓋指令（避免充分涵蓋主題無謂搜尋）。"""

        async def fake_retrieve_context(question, **k):
            return ([_FakeSource(i) for i in range(10)], "脈絡內容")

        captured = {}

        async def fake_stream(*a, **k):
            captured["prompt"] = a[0] if a else k.get("prompt")
            yield "## 執行摘要\n內容[1]"

        async def fake_persist(*a, **k):
            return None

        orig = (
            rpt.retrieve_context,
            rpt.stream_completion, rpt.render_report_pdf, rpt.write_report_pdf,
            rpt.persist_report_doc, rpt.SessionFactory, rpt.REPORT_ENABLE_WEB,
        )
        rpt.retrieve_context = fake_retrieve_context
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
                rpt.retrieve_context,
                rpt.stream_completion, rpt.render_report_pdf, rpt.write_report_pdf,
                rpt.persist_report_doc, rpt.SessionFactory, rpt.REPORT_ENABLE_WEB,
            ) = orig

        self.assertNotIn("涵蓋可能不足", captured["prompt"])
        self.assertNotIn("僅找到", captured["prompt"])
