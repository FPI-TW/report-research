"""即時串流產出的簡體收尾：研報單次路徑，與 `_answer_correction` 的契約。

這條缺口與四支批次不同——批次是「產出完再寫」，串流是「邊產出邊送」。轉換是
整串決定的（見 `app/services/zh_hant.py` 的門檻），串流當下拿不到整串，於是兩條
路徑的處置刻意不同：

- **研報**：前端不渲染草稿（`askReducer` 的 report `token` 是 no-op），使用者拿到的
  是 PDF 與落庫的 markdown，所以只要在組裝點轉就完全補上，不需要任何顯示面機制。
- **問答**：畫面就是 token 累積出來的，所以 token 照原樣送、`done` 再帶一份校正後的
  整份答案讓畫面收斂（`answer.py` 的 `_answer_correction`）。

姊妹測試：
- 逐節研報路徑 → `tests/test_report_sectioned.py`（harness 在那裡，兩條收尾都要接）
- 問答端到端 → `tests/test_answer.py` 的 AnswerGateTests
- 門檻與轉換規則本身 → `tests/test_zh_hant.py`
"""

import unittest

import app.services.report as rpt
from app.services.answer import _answer_correction
from app.services.query_planner import QueryPlan, SubQuery


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, *a, **k):
        return None

    async def commit(self):
        return None


class AnswerCorrectionTests(unittest.TestCase):
    def test_omits_field_when_unchanged(self):
        self.assertEqual(_answer_correction("台積電營收成長", "台積電營收成長"), {})

    def test_carries_final_text_when_changed(self):
        self.assertEqual(
            _answer_correction("台积电营收成长", "台積電營收成長"),
            {"answer": "台積電營收成長"},
        )


class SingleShotReportConversionTests(unittest.IsolatedAsyncioTestCase):
    """單次生成路徑的收尾轉換（逐節路徑的對照在 test_report_sectioned.py）。"""

    def _install(self, stream_text: str):
        capture: dict = {}

        async def fake_retrieve_context_multi(question, queries, **k):
            return ([], "脈絡內容")

        # 簽章與回傳型別都必須與真 plan_queries 一致：回錯型別會在
        # `plan.subqueries` 當場 AttributeError（本檔第一版就是這樣紅的）。
        async def fake_plan(question, *, profile, **k):
            return QueryPlan((SubQuery(text=question),), profile=profile, degraded=True)

        async def fake_stream(*a, **k):
            yield stream_text

        async def fake_persist(*a, **k):
            capture["persist_args"] = a

        def fake_render(md, **k):
            capture["rendered"] = md
            return b"%PDF-1.4 fake"

        orig = {
            "plan_queries": rpt.plan_queries,
            "retrieve_context_multi": rpt.retrieve_context_multi,
            "stream_completion": rpt.stream_completion,
            "render_report_pdf": rpt.render_report_pdf,
            "write_report_pdf": rpt.write_report_pdf,
            "persist_report_doc": rpt.persist_report_doc,
            "SessionFactory": rpt.SessionFactory,
            "REPORT_SECTIONED_ENABLED": rpt.REPORT_SECTIONED_ENABLED,
        }
        rpt.plan_queries = fake_plan
        rpt.retrieve_context_multi = fake_retrieve_context_multi
        rpt.stream_completion = fake_stream
        rpt.render_report_pdf = fake_render
        rpt.write_report_pdf = lambda rid, b: f"/tmp/{rid}.pdf"
        rpt.persist_report_doc = fake_persist
        rpt.SessionFactory = lambda: _FakeSession()
        # 明確關掉逐節：預設是開的（config 的 REPORT_SECTIONED_ENABLED 預設 "1"），
        # 靠 fail-open 退回單次會讓這條測試其實在測另一條路。
        rpt.REPORT_SECTIONED_ENABLED = False

        def restore():
            for name, value in orig.items():
                setattr(rpt, name, value)

        return capture, restore

    async def test_simplified_stream_is_converted_before_render_and_persist(self):
        capture, restore = self._install(
            "# 群联电子 深度研报\n\n## 执行摘要\n\n营收创同期新高[1]。\n"
        )
        try:
            evs = [e async for e in rpt.generate_report("群聯營收")]
        finally:
            restore()

        self.assertEqual(evs[-1][0], "done")
        rendered = capture["rendered"]
        self.assertIn("群聯電子 深度研報", rendered)
        self.assertIn("執行摘要", rendered)
        self.assertNotIn("营收", rendered)
        # 落庫的 markdown 與送去渲染的是同一份（markdown 是真相，PDF 只是渲染）
        self.assertIn(rendered, capture["persist_args"])

    async def test_streamed_tokens_are_not_rewritten(self):
        """token 刻意維持原樣：研報前端不渲染草稿，改寫它只會製造兩份真相。"""
        src = "# 群联电子 深度研报\n\n## 执行摘要\n\n营收创同期新高[1]。\n"
        capture, restore = self._install(src)
        try:
            evs = [e async for e in rpt.generate_report("群聯營收")]
        finally:
            restore()
        self.assertEqual("".join(p for k, p in evs if k == "token"), src)

    async def test_traditional_stream_untouched(self):
        src = "# 台積電 深度研報\n\n## 執行摘要\n\n營收創同期新高[1]。\n"
        capture, restore = self._install(src)
        try:
            _ = [e async for e in rpt.generate_report("台積電營收")]
        finally:
            restore()
        self.assertEqual(capture["rendered"], src.strip())


if __name__ == "__main__":
    unittest.main()
