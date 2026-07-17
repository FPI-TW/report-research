"""M7 T7：generate_report 逐節（sectioned）預設路徑的編排契約。

單次生成路徑的契約在 tests/test_report.py（該檔 autouse 釘 REPORT_SECTIONED_ENABLED=
False）。本檔專測逐節分支：委派 report_writer.draft_report、__final__ 收尾（渲染/落地/
done 形狀/新欄）、__fallback__ 退單次（事件序零差異）、已吐 token 後失敗 → error、
persist=False eval 旁路、run 生命週期 fail-open。
"""

import sys
import unittest
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services import report as rpt  # noqa: E402
from app.services import report_writer as rw  # noqa: E402
from app.services.query_planner import QueryPlan, SubQuery  # noqa: E402


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, *a, **k):
        return None

    async def commit(self):
        return None


@dataclass
class _Src:
    """run-level 檢索回傳的 source 替身（generate_report 會 asdict 它）。"""

    n: int
    report_id: str = "r-run"
    file_name: str = "run.pdf"
    market: str = "TW"
    report_date: str = "2026-06-01"


def _fake_draft(events):
    """把事件序包成 report_writer.draft_report 相容的 async generator。"""

    async def _gen(question, context, *, filters=None, run_id=None, draft_model=None):
        for e in events:
            yield e

    return _gen


_FINAL_OK = (
    "__final__",
    {
        "markdown": "# 台積電 深度研報\n\n## 執行摘要\n\n綜述[1]。\n\n## 引用來源\n[1] a.pdf（TW·2026-06-01）\n",
        "manifest": {"schema_version": 1, "evidence": []},
        "sources": [
            {"n": 1, "report_id": "r-1", "file_name": "a.pdf",
             "market": "TW", "report_date": "2026-06-01"},
        ],
        "outline": {"title": "台積電 深度研報", "sections": []},
        "claim_evidence": {"0": ["abc123"]},
        "revision_id": "rev-xyz",
        "markdown_hash": "hhh",
        "n_unknown": 0,
    },
)


class _SectionedBase(unittest.IsolatedAsyncioTestCase):
    """安裝共用 stub 鏈；子類覆寫 draft_report 事件序。"""

    def _install(self, draft_events, *, capture=None):
        capture = capture if capture is not None else {}

        async def fake_plan(question, *, profile, **k):
            return QueryPlan((SubQuery(text=question),), profile=profile, degraded=True)

        async def fake_retrieve(question, queries, **k):
            return ([_Src(1)], "run-level 脈絡")

        async def fake_stream(*a, **k):
            # 僅 fallback→單次路徑會用到
            capture["single_shot"] = True
            yield "# 單次標題\n\n## 執行摘要\n\n單次內文[1]。"

        async def fake_persist(*a, **k):
            capture["persist_args"] = a
            capture["persist_kwargs"] = k

        async def fake_open_run(question, filters, model, qa_id, conversation_id):
            capture["opened"] = True
            return "run-1"

        async def fake_mark(run_id, status, **fields):
            capture.setdefault("marks", []).append((run_id, status, fields))

        orig = {
            "plan_queries": rpt.plan_queries,
            "retrieve_context_multi": rpt.retrieve_context_multi,
            "stream_completion": rpt.stream_completion,
            "render_report_pdf": rpt.render_report_pdf,
            "write_report_pdf": rpt.write_report_pdf,
            "persist_report_doc": rpt.persist_report_doc,
            "SessionFactory": rpt.SessionFactory,
            "_open_sectioned_run": rpt._open_sectioned_run,
            "_mark_run": rpt._mark_run,
            "draft_report": rw.draft_report,
            "REPORT_SECTIONED_ENABLED": rpt.REPORT_SECTIONED_ENABLED,
        }
        rpt.plan_queries = fake_plan
        rpt.retrieve_context_multi = fake_retrieve
        rpt.stream_completion = fake_stream

        def _render(md, **k):
            capture["rendered"] = md
            return b"%PDF-1.4 fake"

        rpt.render_report_pdf = _render
        rpt.write_report_pdf = lambda rid, b: f"/tmp/{rid}.pdf"
        rpt.persist_report_doc = fake_persist
        rpt.SessionFactory = lambda: _FakeSession()
        rpt._open_sectioned_run = fake_open_run
        rpt._mark_run = fake_mark
        rw.draft_report = _fake_draft(draft_events)
        rpt.REPORT_SECTIONED_ENABLED = True

        def restore():
            rpt.plan_queries = orig["plan_queries"]
            rpt.retrieve_context_multi = orig["retrieve_context_multi"]
            rpt.stream_completion = orig["stream_completion"]
            rpt.render_report_pdf = orig["render_report_pdf"]
            rpt.write_report_pdf = orig["write_report_pdf"]
            rpt.persist_report_doc = orig["persist_report_doc"]
            rpt.SessionFactory = orig["SessionFactory"]
            rpt._open_sectioned_run = orig["_open_sectioned_run"]
            rpt._mark_run = orig["_mark_run"]
            rw.draft_report = orig["draft_report"]
            rpt.REPORT_SECTIONED_ENABLED = orig["REPORT_SECTIONED_ENABLED"]

        return capture, restore


class SectionedFinalTests(_SectionedBase):
    async def test_final_renders_persists_and_done_shape(self):
        events = [
            ("status", {"stage": "writing"}),
            ("token", "執行摘要內文"),
            ("section_draft", {"position": 0, "section_key": "exec_summary",
                               "heading": "執行摘要", "markdown": "執行摘要內文"}),
            ("token", "分析內文"),
            ("document_revision", {"revision_id": "rev-xyz", "revision": 1,
                                   "markdown_hash": "hhh"}),
            _FINAL_OK,
        ]
        capture, restore = self._install(events)
        try:
            evs = [e async for e in rpt.generate_report("台積電趨勢")]
        finally:
            restore()

        kinds = [e[0] for e in evs]
        self.assertEqual(kinds[0], "status")
        self.assertEqual(evs[0][1]["stage"], "retrieving")
        self.assertIn("sources", kinds)
        self.assertIn("token", kinds)
        self.assertIn("section_draft", kinds)
        self.assertIn("document_revision", kinds)
        self.assertEqual(kinds[-1], "done")

        done = evs[-1][1]
        self.assertTrue(done["report_id"])
        self.assertEqual(done["title"], "台積電 深度研報")
        self.assertTrue(done["download_url"].endswith("/pdf"))
        # 渲染的是逐節組裝後的最終 markdown
        self.assertTrue(capture["rendered"].startswith("# 台積電 深度研報"))
        # 單次串流未被觸發
        self.assertNotIn("single_shot", capture)

    async def test_final_persists_new_m7_columns(self):
        capture, restore = self._install([_FINAL_OK])
        try:
            _ = [e async for e in rpt.generate_report("台積電趨勢")]
        finally:
            restore()

        kw = capture["persist_kwargs"]
        self.assertEqual(kw["outline"], {"title": "台積電 深度研報", "sections": []})
        self.assertEqual(kw["claim_evidence"], {"0": ["abc123"]})
        self.assertEqual(kw["current_revision_id"], "rev-xyz")
        self.assertEqual(kw["report_run_id"], "run-1")
        # persist 的 sources 為 [n] 序 final_sources
        self.assertEqual(capture["persist_args"][7][0]["report_id"], "r-1")

    async def test_final_completes_run(self):
        capture, restore = self._install([_FINAL_OK])
        try:
            evs = [e async for e in rpt.generate_report("台積電趨勢")]
        finally:
            restore()

        report_id = evs[-1][1]["report_id"]
        statuses = [m[1] for m in capture.get("marks", [])]
        self.assertIn("completed", statuses)
        completed = next(m for m in capture["marks"] if m[1] == "completed")
        # 收尾 run 綁 report_doc_id＝done 的 report_id、帶 revision 與 manifest hash
        self.assertEqual(completed[2].get("report_doc_id"), report_id)
        self.assertEqual(completed[2].get("current_revision_id"), "rev-xyz")
        self.assertEqual(completed[2].get("expected_current"), "rendering")
        self.assertTrue(completed[2].get("evidence_manifest_hash"))

    async def test_eval_persist_false_skips_render_and_persist(self):
        capture, restore = self._install([_FINAL_OK])

        def _boom(*a, **k):
            raise AssertionError("persist=False 不得渲染")

        async def _boom_persist(*a, **k):
            raise AssertionError("persist=False 不得落地")

        rpt.render_report_pdf = _boom
        rpt.write_report_pdf = _boom
        rpt.persist_report_doc = _boom_persist
        try:
            evs = [e async for e in rpt.generate_report("台積電趨勢", persist=False)]
        finally:
            restore()

        # persist=False：run 不開（run_id 恆 None）
        self.assertNotIn("opened", capture)
        stages = [p["stage"] for (k, p) in evs if k == "status"]
        self.assertNotIn("rendering", stages)
        done = evs[-1][1]
        self.assertEqual(evs[-1][0], "done")
        self.assertIsNone(done["report_id"])
        self.assertIn("執行摘要", done["markdown"])
        self.assertEqual(done["context"], "run-level 脈絡")


class SectionedFallbackTests(_SectionedBase):
    async def test_outline_fallback_runs_single_shot(self):
        """大綱 fail-open（__fallback__，未吐內容）→ 退單次；事件序＝單次路徑。"""
        capture, restore = self._install([("__fallback__", None)])
        try:
            evs = [e async for e in rpt.generate_report("台積電趨勢")]
        finally:
            restore()

        kinds = [e[0] for e in evs]
        # 單次串流被觸發、無逐節加法事件
        self.assertTrue(capture.get("single_shot"))
        self.assertNotIn("section_draft", kinds)
        self.assertNotIn("document_revision", kinds)
        # 事件序＝單次：retrieving → sources → writing → token → rendering → done
        stages = [p["stage"] for (k, p) in evs if k == "status"]
        self.assertEqual(stages[0], "retrieving")
        self.assertIn("writing", stages)
        self.assertIn("rendering", stages)
        self.assertEqual(kinds[-1], "done")
        self.assertTrue(evs[-1][1]["report_id"])
        # fallback run 被標 cancelled
        self.assertIn("cancelled", [m[1] for m in capture.get("marks", [])])

    async def test_exception_after_token_emits_error_no_single_shot(self):
        """已吐內容 token 後 draft_report 例外 → 回 error、不退單次（避免重覆內容）。"""

        async def _boom_gen(question, context, *, filters=None, run_id=None, draft_model=None):
            yield ("status", {"stage": "writing"})
            yield ("token", "部分內容")
            raise RuntimeError("draft boom")

        capture, restore = self._install([_FINAL_OK])
        rw.draft_report = _boom_gen
        try:
            evs = [e async for e in rpt.generate_report("台積電趨勢")]
        finally:
            restore()

        kinds = [e[0] for e in evs]
        self.assertIn("token", kinds)
        self.assertEqual(evs[-1][0], "error")
        # 未退單次
        self.assertNotIn("single_shot", capture)
        # run 標 failed
        self.assertIn("failed", [m[1] for m in capture.get("marks", [])])


class SectionedRunLifecycleTests(unittest.IsolatedAsyncioTestCase):
    """_open_sectioned_run 的真實 fail-open（不 stub 該函式，改 stub 底層 open_run）。"""

    async def test_open_run_failure_still_generates(self):
        capture = {}

        async def fake_plan(question, *, profile, **k):
            return QueryPlan((SubQuery(text=question),), profile=profile, degraded=True)

        async def fake_retrieve(question, queries, **k):
            return ([_Src(1)], "脈絡")

        async def boom_open_run(*a, **k):
            raise RuntimeError("db down")

        async def fake_persist(*a, **k):
            capture["persist_kwargs"] = k

        orig = {
            "plan_queries": rpt.plan_queries,
            "retrieve_context_multi": rpt.retrieve_context_multi,
            "render_report_pdf": rpt.render_report_pdf,
            "write_report_pdf": rpt.write_report_pdf,
            "persist_report_doc": rpt.persist_report_doc,
            "SessionFactory": rpt.SessionFactory,
            "draft_report": rw.draft_report,
            "open_run": rw.open_run,
            "REPORT_SECTIONED_ENABLED": rpt.REPORT_SECTIONED_ENABLED,
        }
        rpt.plan_queries = fake_plan
        rpt.retrieve_context_multi = fake_retrieve
        rpt.render_report_pdf = lambda md, **k: b"%PDF-1.4 fake"
        rpt.write_report_pdf = lambda rid, b: f"/tmp/{rid}.pdf"
        rpt.persist_report_doc = fake_persist
        rpt.SessionFactory = lambda: _FakeSession()
        rw.draft_report = _fake_draft([_FINAL_OK])
        rw.open_run = boom_open_run
        rpt.REPORT_SECTIONED_ENABLED = True
        try:
            evs = [e async for e in rpt.generate_report("台積電趨勢")]
        finally:
            rpt.plan_queries = orig["plan_queries"]
            rpt.retrieve_context_multi = orig["retrieve_context_multi"]
            rpt.render_report_pdf = orig["render_report_pdf"]
            rpt.write_report_pdf = orig["write_report_pdf"]
            rpt.persist_report_doc = orig["persist_report_doc"]
            rpt.SessionFactory = orig["SessionFactory"]
            rw.draft_report = orig["draft_report"]
            rw.open_run = orig["open_run"]
            rpt.REPORT_SECTIONED_ENABLED = orig["REPORT_SECTIONED_ENABLED"]

        # 生成照常完成、report_run_id 為 None（開 run 失敗被 fail-open 吞掉）
        self.assertEqual(evs[-1][0], "done")
        self.assertTrue(evs[-1][1]["report_id"])
        self.assertIsNone(capture["persist_kwargs"]["report_run_id"])


if __name__ == "__main__":
    unittest.main()
