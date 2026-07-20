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


def _fake_draft(events, *, capture=None):
    """把事件序包成 report_writer.draft_report 相容的 async generator。

    **簽章必須與真 draft_report 一致**：不一致時 generate_report 的 `except Exception`
    會把 TypeError 當成「生成失敗」→ 靜默退單次生成，測試看似仍過但測的是另一條路
    （本檔曾因此讓整組測試改跑真 claude CLI 而卡死）。組合測試
    SectionedComposedTests 用真 draft_report，是這種簽章漂移的最終防線。
    """

    async def _gen(question, context, *, filters=None, run_id=None, draft_model=None,
                   web_enabled=False, coverage_note="", thin_coverage=0, deadline=None):
        if capture is not None:
            capture["draft_kwargs"] = {
                "filters": filters, "run_id": run_id, "draft_model": draft_model,
                "web_enabled": web_enabled, "coverage_note": coverage_note,
                "thin_coverage": thin_coverage, "deadline": deadline,
            }
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
        "n_evidence": 3,   # 共用帳本大小（可用證據）＞ 已被引用的 1 筆
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
            return "run-1", True

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
        rw.draft_report = _fake_draft(draft_events, capture=capture)
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

    async def test_completed_request_key_reuses_existing_document(self):
        """同一 request_key 已完成時回既有文件，不能再渲染／persist 一份新文件。"""
        capture, restore = self._install([_FINAL_OK])

        async def existing_run(*a, **k):
            return "run-existing", False

        async def load_existing(run_id):
            self.assertEqual(run_id, "run-existing")
            return {"status": "completed", "report_doc_id": "doc-existing"}

        async def fetch_existing(report_id):
            self.assertEqual(report_id, "doc-existing")
            return {"title": "既有研報"}

        original_load = rw.load_run
        original_fetch = rpt.fetch_report_doc
        rpt._open_sectioned_run = existing_run
        rw.load_run = load_existing
        rpt.fetch_report_doc = fetch_existing
        try:
            evs = [e async for e in rpt.generate_report("台積電趨勢")]
        finally:
            rw.load_run = original_load
            rpt.fetch_report_doc = original_fetch
            restore()

        self.assertEqual(evs[-1][0], "done")
        self.assertEqual(evs[-1][1]["report_id"], "doc-existing")
        self.assertEqual(evs[-1][1]["title"], "既有研報")
        self.assertNotIn("persist_args", capture)

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
        self.assertTrue(completed[2].get("evidence_manifest_hash"))
        # 審查 F1：收尾**不得**帶 expected_current。研報已渲染並落庫，「完成」是既成
        # 事實；把它綁在某一次中繼稽核寫入的成敗上，只要那次被 _audit fail-open 吞掉，
        # 收尾就會連帶失敗，讓成功的 run 永遠停在中繼態、report_doc_id 從未回填。
        self.assertIsNone(
            completed[2].get("expected_current"),
            "收尾綁 expected_current 會讓掉一次稽核寫入就永久假性卡住",
        )

    async def test_render_failure_marks_run_failed_and_emits_error(self):
        """__final__ 後的 PDF 失敗也必須終結 report_run，不能遺留 rendering。"""
        capture, restore = self._install([_FINAL_OK])

        def boom_render(*a, **k):
            raise RuntimeError("pdf boom")

        rpt.render_report_pdf = boom_render
        try:
            try:
                evs = [e async for e in rpt.generate_report("台積電趨勢")]
            except RuntimeError:
                evs = [("raised", {})]
        finally:
            restore()

        self.assertEqual(evs[-1][0], "error")
        self.assertIn("failed", [m[1] for m in capture.get("marks", [])])

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

    async def test_eval_done_carries_cited_sources_and_evidence_total(self):
        """審查 #2：eval done 必須帶 done.sources（正文 [n] 的對應表）與 n_evidence
        （共用帳本大小）。少了它們，harness 只能拿 run-level sources 事件當引用分母
        ——但逐節路徑的 [n] 由逐節帳本編號，兩者毫無關係 → 指標失真。"""
        capture, restore = self._install([_FINAL_OK])
        try:
            evs = [e async for e in rpt.generate_report("台積電趨勢", persist=False)]
        finally:
            restore()

        done = evs[-1][1]
        # done.sources ＝ __final__ 的 final_sources（[n] 序），非 run-level 的 [_Src(1)]
        self.assertEqual([s["report_id"] for s in done["sources"]], ["r-1"])
        self.assertEqual(done["sources"][0]["n"], 1)
        self.assertEqual(done["n_evidence"], 3)
        self.assertEqual(done["claim_evidence"], {"0": ["abc123"]})

    async def test_persist_true_done_has_no_eval_fields(self):
        """線上路徑契約不變：done 不外洩 markdown/context/sources/n_evidence/
        claim_evidence（對齊單次路徑的 test_report 斷言）。"""
        capture, restore = self._install([_FINAL_OK])
        try:
            evs = [e async for e in rpt.generate_report("台積電趨勢")]
        finally:
            restore()

        done = evs[-1][1]
        self.assertEqual(evs[-1][0], "done")
        for leaked in ("markdown", "context", "sources", "n_evidence", "claim_evidence"):
            self.assertNotIn(leaked, done)
        self.assertEqual(
            sorted(done), ["download_url", "report_id", "thinking_ms", "title"]
        )

    async def test_web_enabled_and_coverage_note_forwarded_to_writer(self):
        """審查 #3：REPORT_ENABLE_WEB 與薄涵蓋 nudge 必須傳進 writer，否則逐節路徑
        完全不會上網（空脈絡守門「網搜會補」的前提就不成立）。"""
        capture, restore = self._install([_FINAL_OK])
        orig_web = rpt.REPORT_ENABLE_WEB
        rpt.REPORT_ENABLE_WEB = True
        try:
            _ = [e async for e in rpt.generate_report("台積電趨勢")]
        finally:
            rpt.REPORT_ENABLE_WEB = orig_web
            restore()

        kw = capture["draft_kwargs"]
        self.assertIs(kw["web_enabled"], True)
        # run-level 只命中 1 篇（_Src(1)）< REPORT_THIN_COVERAGE=8 → 注入薄涵蓋 nudge
        self.assertIn("僅找到 1 篇", kw["coverage_note"])
        self.assertIn("主動以網路搜尋補充", kw["coverage_note"])

    async def test_coverage_note_empty_when_web_off(self):
        capture, restore = self._install([_FINAL_OK])
        orig_web = rpt.REPORT_ENABLE_WEB
        rpt.REPORT_ENABLE_WEB = False
        try:
            _ = [e async for e in rpt.generate_report("台積電趨勢")]
        finally:
            rpt.REPORT_ENABLE_WEB = orig_web
            restore()

        kw = capture["draft_kwargs"]
        self.assertIs(kw["web_enabled"], False)
        self.assertEqual(kw["coverage_note"], "")


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

        async def _boom_gen(question, context, *, filters=None, run_id=None,
                            draft_model=None, web_enabled=False, coverage_note="",
                            thin_coverage=0, deadline=None):
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


    async def test_failed_sentinel_emits_error_and_no_single_shot(self):
        """審查 #5：writer 在硬邊界後判定不可續（__failed__）→ 回 error、不退單次。"""
        events = [
            ("status", {"stage": "writing"}),
            ("token", "執行摘要內文"),
            ("__failed__", {"detail": "研報引用標記異常"}),
        ]
        capture, restore = self._install(events)
        try:
            evs = [e async for e in rpt.generate_report("台積電趨勢")]
        finally:
            restore()

        self.assertEqual(evs[-1][0], "error")
        self.assertEqual(evs[-1][1]["detail"], "研報引用標記異常")
        self.assertNotIn("single_shot", capture)  # 不得重跑一份完整內容
        marks = [m for m in capture.get("marks", []) if m[1] == "failed"]
        self.assertTrue(marks)
        self.assertEqual(marks[-1][2].get("error_detail"), "研報引用標記異常")


class SectionedComposedTests(unittest.IsolatedAsyncioTestCase):
    """**組合測試**：generate_report ↔ 真 report_writer.draft_report（不 stub 掉）。

    只替換最外層 I/O（run-level 檢索／大綱／逐節檢索／stream_completion／渲染／落地），
    中間整條 M7 生產路徑照跑。這是 #6 測試盲點的正解：draft_report 被整支 stub 時，
    report.py↔report_writer.py 的模組縫（簽章、事件契約、run 持久化）完全沒被驗過。
    """

    def _install(self, capture, *, stream, run_id="run-1", web=False):
        eid = rw.EvidenceLedger().add_corpus(report_id="r-sec").evidence_id
        capture["eid"] = eid
        outline = {
            "title": "台積電 深度研報",
            "sections": [
                {"position": 0, "key": "exec_summary", "heading": "執行摘要",
                 "topic": "q", "kind": "framing"},
                {"position": 1, "key": "analysis", "heading": "先進製程",
                 "topic": "n2", "kind": "analysis"},
                {"position": 2, "key": "risk_outlook", "heading": "風險與展望",
                 "topic": "r", "kind": "framing"},
            ],
        }
        src = _Src(1, report_id="r-sec", file_name="sec.pdf")

        async def fake_plan(question, *, profile, **k):
            return QueryPlan((SubQuery(text=question),), profile=profile, degraded=True)

        async def fake_rcm(question, queries, **k):
            return ([_Src(1)], "run-level 脈絡")

        async def fake_outline(*a, **k):
            return outline

        async def fake_sec_retrieve(topic, **k):
            capture.setdefault("topics", []).append(topic)
            return ([src], "[1] 報告：sec.pdf\n片段內容")

        async def fake_open_run(*a, **k):
            return run_id, True

        async def fake_mark(rid, status, **f):
            capture.setdefault("marks", []).append((rid, status, f))

        async def fake_advance(rid, status, **k):
            capture.setdefault("advances", []).append(status)

        async def fake_upsert(rid, pos, **k):
            capture.setdefault("upserts", []).append((pos, k.get("status")))

        async def fake_persist(*a, **k):
            capture["persist_args"] = a
            capture["persist_kwargs"] = k

        def fake_render(md, **k):
            capture["rendered"] = md
            return b"%PDF-1.4 fake"

        async def boom_single(*a, **k):
            raise AssertionError("不得退單次生成")
            yield ""

        orig = {n: getattr(rpt, n) for n in (
            "plan_queries", "retrieve_context_multi", "stream_completion",
            "render_report_pdf", "write_report_pdf", "persist_report_doc",
            "SessionFactory", "_open_sectioned_run", "_mark_run",
            "REPORT_SECTIONED_ENABLED", "REPORT_ENABLE_WEB",
        )}
        orig_rw = {n: getattr(rw, n) for n in (
            "plan_outline", "retrieve_for_section", "stream_completion",
            "advance_status", "upsert_section",
        )}
        rpt.plan_queries = fake_plan
        rpt.retrieve_context_multi = fake_rcm
        rpt.stream_completion = boom_single
        rpt.render_report_pdf = fake_render
        rpt.write_report_pdf = lambda rid, b: f"/tmp/{rid}.pdf"
        rpt.persist_report_doc = fake_persist
        rpt.SessionFactory = lambda: _FakeSession()
        rpt._open_sectioned_run = fake_open_run
        rpt._mark_run = fake_mark
        rpt.REPORT_SECTIONED_ENABLED = True
        rpt.REPORT_ENABLE_WEB = web
        rw.plan_outline = fake_outline
        rw.retrieve_for_section = fake_sec_retrieve
        rw.stream_completion = stream
        rw.advance_status = fake_advance
        rw.upsert_section = fake_upsert

        def restore():
            for n, v in orig.items():
                setattr(rpt, n, v)
            for n, v in orig_rw.items():
                setattr(rw, n, v)

        return restore

    async def test_real_writer_end_to_end_produces_report(self):
        capture = {}

        def stream(*a, **k):
            async def gen():
                yield f"本節結論[[ev:{capture['eid']}]]。"
            return gen()

        restore = self._install(capture, stream=stream)
        try:
            evs = [e async for e in rpt.generate_report("台積電趨勢")]
        finally:
            restore()

        kinds = [e[0] for e in evs]
        self.assertEqual(kinds[0], "status")
        self.assertEqual(evs[0][1]["stage"], "retrieving")
        self.assertEqual(kinds.count("section_draft"), 3)
        self.assertIn("document_revision", kinds)
        self.assertEqual(kinds[-1], "done")   # done 仍為最後且形狀不變

        md = capture["rendered"]
        self.assertTrue(md.startswith("# 台積電 深度研報"))
        for h in ("## 執行摘要", "## 重點分析", "### 先進製程", "## 風險與展望",
                  "## 引用來源"):
            self.assertIn(h, md)
        self.assertIn("本節結論[1]。", md)      # [[ev:]] → [n]
        self.assertNotIn("[[ev:", md)          # 內部 token 不漏到輸出
        # 逐節檢索確實用了 outline 的 topic（非整份原題）
        self.assertEqual(capture["topics"], ["q", "n2", "r"])
        # 狀態機走完整條前進路徑
        self.assertEqual(
            capture["advances"], ["outlining", "drafting", "verifying", "rendering"]
        )
        self.assertIn("completed", [m[1] for m in capture["marks"]])
        # persist 的 sources 與正文 [n] 同一份表
        self.assertEqual(capture["persist_args"][7][0]["report_id"], "r-sec")

    async def test_real_writer_audit_failure_still_produces_report(self):
        """審查 #1 的端到端回歸：稽核寫入全炸（DB down）→ 研報仍完成到 done。"""
        capture = {}

        def stream(*a, **k):
            async def gen():
                yield f"本節結論[[ev:{capture['eid']}]]。"
            return gen()

        restore = self._install(capture, stream=stream)

        async def dying(*a, **k):
            raise RuntimeError("DB connection reset")

        rw.advance_status = dying
        rw.upsert_section = dying
        try:
            evs = [e async for e in rpt.generate_report("台積電趨勢")]
        finally:
            restore()

        self.assertEqual(evs[-1][0], "done")
        self.assertTrue(evs[-1][1]["report_id"])
        self.assertNotIn("error", [e[0] for e in evs])

    async def test_real_writer_forwards_allow_web_to_stream_completion(self):
        """審查 #3 的端到端回歸：REPORT_ENABLE_WEB → 逐節 stream_completion 開網搜。"""
        capture = {}

        def stream(*a, **k):
            capture.setdefault("allow_web", []).append(k.get("allow_web"))
            async def gen():
                yield f"本節結論[[ev:{capture['eid']}]]。"
            return gen()

        restore = self._install(capture, stream=stream, web=True)
        try:
            evs = [e async for e in rpt.generate_report("台積電趨勢")]
        finally:
            restore()

        self.assertEqual(evs[-1][0], "done")
        self.assertTrue(capture["allow_web"])
        self.assertTrue(all(v is True for v in capture["allow_web"]))


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

        async def boom_single(*a, **k):
            # 護欄：真 stream_completion 會 spawn claude CLI 並卡到 REPORT_TIMEOUT=600s
            raise AssertionError("不得走到單次生成")
            yield ""

        orig = {
            "plan_queries": rpt.plan_queries,
            "retrieve_context_multi": rpt.retrieve_context_multi,
            "stream_completion": rpt.stream_completion,
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
        rpt.stream_completion = boom_single
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
            rpt.stream_completion = orig["stream_completion"]
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

    async def test_existing_run_is_returned_for_deduplication(self):
        """既有 request_key 不能被降級成無 run 的新生成。"""

        async def existing_open(*a, **k):
            return "existing-run", False

        orig_open = rw.open_run
        try:
            rw.open_run = existing_open
            result = await rpt._open_sectioned_run("q", {}, "model", None, "conv")
        finally:
            rw.open_run = orig_open

        self.assertEqual(result, ("existing-run", False))


if __name__ == "__main__":
    unittest.main()
