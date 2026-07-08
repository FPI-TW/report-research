import sys
import unittest
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services import answer as ans  # noqa: E402
import app.services.retrieval_pipeline as rp  # noqa: E402
from tests.test_answer import _FakeSession, make_row  # noqa: E402


class DoneOffersReportTests(unittest.IsolatedAsyncioTestCase):
    async def test_done_includes_offer_report_when_worthy(self):
        async def fake_search(session, q, qvec, **k):
            return [
                (0, 0.80, make_row("r1", "a.pdf", "TW", "甲內容。", date(2026, 6, 1))),
                (0, 0.78, make_row("r2", "b.pdf", "TW", "乙內容。", date(2026, 6, 1))),
                (0, 0.76, make_row("r3", "c.pdf", "TW", "丙內容。", date(2026, 6, 1))),
            ]

        async def fake_stream(*a, **k):
            yield "分析結論[1][2][3]"

        async def fake_intent(question, **k):
            return True

        orig = (rp.hybrid_search, rp.embed_query_cached, ans.stream_completion,
                rp.SessionFactory, ans.SessionFactory, ans.classify_intent)
        rp.hybrid_search = fake_search
        rp.embed_query_cached = lambda q: [0.0]
        ans.stream_completion = fake_stream
        rp.SessionFactory = lambda: _FakeSession()
        ans.SessionFactory = lambda: _FakeSession()
        ans.classify_intent = fake_intent
        try:
            events = [e async for e in ans.answer_question("請分析台積電產業趨勢")]
        finally:
            (rp.hybrid_search, rp.embed_query_cached, ans.stream_completion,
             rp.SessionFactory, ans.SessionFactory, ans.classify_intent) = orig

        done = [p for (k, p) in events if k == "done"][-1]
        self.assertTrue(done.get("offer_report"))
        self.assertTrue((done.get("report_title") or "").endswith("深度研報"))


class GetConversationAttachesReportsTests(unittest.IsolatedAsyncioTestCase):
    async def test_attaches_reports_by_qa_id(self):
        # get_conversation 內部用 SessionFactory 撈 qa_log；用 _FakeSession 的空結果即可，
        # 重點驗：reports_for_conversation 的回傳被掛到對應 turn。
        async def fake_reports(cid):
            return {"qa-1": [{"report_id": "rep-1", "title": "X 深度研報",
                              "download_url": "/api/report-doc/rep-1/pdf", "created_at": "t"}]}

        # 假 get_conversation 的 qa 行：history_item 需要的欄位
        class _Result:
            def all(self_inner):
                return [("qa-1", "問題", "答案", None, None, None, None, 100)]

        class _Sess(_FakeSession):
            async def execute(self_inner, *a, **k):
                return _Result()

        import app.services.report as rpt
        orig_reports = rpt.reports_for_conversation
        orig_sf = ans.SessionFactory
        rpt.reports_for_conversation = fake_reports
        ans.SessionFactory = lambda: _Sess()
        try:
            items = await ans.get_conversation("conv-1")
        finally:
            rpt.reports_for_conversation = orig_reports
            ans.SessionFactory = orig_sf

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["reports"][0]["report_id"], "rep-1")
