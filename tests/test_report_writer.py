"""M7 T2：report_writer 狀態機地基測試。

純邏輯（轉換守門／request_key 合成／checkpoint 序列化）以真斷言驗；DB 函式以
fake session 驗發出的 SQL/params（沿用本 repo「測試不連真 DB」慣例——DDL 正確性
已由 make schema 對真 DB 驗過）。
"""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

import app.services.report_writer as rw
from app.services.report_writer import (
    Checkpoint,
    InvalidTransition,
    is_valid_transition,
    synthesize_request_key,
)


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return list(self._rows)


class _FakeSession:
    """記錄 execute 的 (sql, params)，依序吐 scripted 結果列；async CM。"""

    def __init__(self, results=None):
        self.results = list(results or [])
        self.executed: list[tuple[str, dict]] = []
        self.commits = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, stmt, params=None):
        self.executed.append((str(stmt), params or {}))
        rows = self.results.pop(0) if self.results else []
        return _Result(rows)

    async def commit(self):
        self.commits += 1


def _use(session):
    """把 report_writer.SessionFactory 換成回傳此 fake session 的工廠。"""
    return patch.object(rw, "SessionFactory", lambda: session)


# ── 純邏輯 ──────────────────────────────────────────────────────────────────
class TransitionTests(unittest.TestCase):
    def test_forward_path_valid(self):
        self.assertTrue(is_valid_transition("queued", "retrieving"))
        self.assertTrue(is_valid_transition("rendering", "completed"))

    def test_skipping_states_invalid(self):
        self.assertFalse(is_valid_transition("queued", "rendering"))
        self.assertFalse(is_valid_transition("retrieving", "completed"))

    def test_same_state_idempotent(self):
        self.assertTrue(is_valid_transition("drafting", "drafting"))

    def test_any_nonterminal_to_failed_or_cancelled(self):
        for s in ("queued", "retrieving", "outlining", "drafting", "verifying", "rendering"):
            self.assertTrue(is_valid_transition(s, "failed"))
            self.assertTrue(is_valid_transition(s, "cancelled"))

    def test_terminal_cannot_transition(self):
        self.assertFalse(is_valid_transition("completed", "rendering"))
        self.assertFalse(is_valid_transition("failed", "cancelled"))

    def test_unknown_state(self):
        self.assertFalse(is_valid_transition("bogus", "retrieving"))


class RequestKeyTests(unittest.TestCase):
    def test_deterministic(self):
        a = synthesize_request_key("台積電展望", model="claude-sonnet-5")
        b = synthesize_request_key("台積電展望", model="claude-sonnet-5")
        self.assertEqual(a, b)

    def test_filters_order_independent(self):
        a = synthesize_request_key("q", filters={"market": "TW", "k": 5})
        b = synthesize_request_key("q", filters={"k": 5, "market": "TW"})
        self.assertEqual(a, b)

    def test_distinct_inputs_distinct_keys(self):
        base = synthesize_request_key("q", model="m", conversation_id="c1")
        self.assertNotEqual(base, synthesize_request_key("q2", model="m", conversation_id="c1"))
        self.assertNotEqual(base, synthesize_request_key("q", model="m", conversation_id="c2"))
        self.assertNotEqual(base, synthesize_request_key("q", model="m2", conversation_id="c1"))

    def test_hex_length(self):
        self.assertRegex(synthesize_request_key("q"), r"^[0-9a-f]{32}$")


class CheckpointTests(unittest.TestCase):
    def test_roundtrip(self):
        c = Checkpoint(outline_ready=True, final_positions=[2, 0, 1], current_revision_id="r1")
        obj = c.to_json()
        self.assertEqual(obj["final_positions"], [0, 1, 2])  # 排序去重
        back = Checkpoint.load(obj)
        self.assertTrue(back.outline_ready)
        self.assertEqual(back.final_positions, [0, 1, 2])
        self.assertEqual(back.current_revision_id, "r1")

    def test_dedup(self):
        self.assertEqual(Checkpoint(final_positions=[1, 1, 0]).to_json()["final_positions"], [0, 1])

    def test_lenient_load(self):
        for bad in (None, {}, "oops", 123, {"final_positions": "x"}):
            c = Checkpoint.load(bad)
            self.assertFalse(c.outline_ready)
            self.assertEqual(c.final_positions, [])
            self.assertIsNone(c.current_revision_id)


# ── DB 函式（fake session）──────────────────────────────────────────────────
class OpenRunTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_run_returns_is_new_true(self):
        s = _FakeSession(results=[[("new-id",)]])  # INSERT ... RETURNING id
        with _use(s):
            run_id, is_new = await rw.open_run("rk-1", input_config={"q": "x"})
        self.assertEqual((run_id, is_new), ("new-id", True))
        self.assertIn("ON CONFLICT (request_key) DO NOTHING RETURNING id", s.executed[0][0])
        self.assertGreaterEqual(s.commits, 1)

    async def test_existing_run_returns_is_new_false(self):
        s = _FakeSession(results=[[], [("exist-id",)]])  # RETURNING empty → SELECT existing
        with _use(s):
            run_id, is_new = await rw.open_run("rk-1")
        self.assertEqual((run_id, is_new), ("exist-id", False))
        self.assertTrue(s.executed[1][0].strip().upper().startswith("SELECT"))


class AdvanceStatusTests(unittest.IsolatedAsyncioTestCase):
    async def test_valid_transition_updates(self):
        s = _FakeSession(results=[[("queued",)]])  # SELECT status
        with _use(s):
            await rw.advance_status("run-1", "retrieving")
        update_sql = s.executed[-1][0]
        self.assertTrue(update_sql.strip().upper().startswith("UPDATE"))
        self.assertIn("status = :st", update_sql)
        self.assertIn("updated_at = now()", update_sql)
        self.assertEqual(s.executed[-1][1]["st"], "retrieving")

    async def test_invalid_transition_raises(self):
        s = _FakeSession(results=[[("queued",)]])
        with _use(s):
            with self.assertRaises(InvalidTransition):
                await rw.advance_status("run-1", "rendering")

    async def test_missing_run_raises(self):
        s = _FakeSession(results=[[]])  # SELECT returns nothing
        with _use(s):
            with self.assertRaises(InvalidTransition):
                await rw.advance_status("ghost", "retrieving")

    async def test_expected_current_mismatch_raises(self):
        s = _FakeSession(results=[[("retrieving",)]])
        with _use(s):
            with self.assertRaises(InvalidTransition):
                await rw.advance_status("run-1", "outlining", expected_current="queued")

    async def test_optional_fields_in_set(self):
        s = _FakeSession(results=[[("outlining",)]])
        ckpt = Checkpoint(outline_ready=True, final_positions=[0])
        with _use(s):
            await rw.advance_status(
                "run-1", "drafting", outline={"sections": ["a"]},
                checkpoint=ckpt, revision=1, error_detail=None,
            )
        sql, params = s.executed[-1]
        self.assertIn("outline = CAST(:outline AS jsonb)", sql)
        self.assertIn("checkpoint = CAST(:ckpt AS jsonb)", sql)
        self.assertIn("revision = :rev", sql)
        self.assertEqual(json.loads(params["outline"]), {"sections": ["a"]})
        self.assertEqual(json.loads(params["ckpt"])["outline_ready"], True)
        self.assertEqual(params["rev"], 1)

    async def test_status_empty_keeps_current(self):
        s = _FakeSession(results=[[("drafting",)]])
        with _use(s):
            await rw.advance_status("run-1", "", current_revision_id="rev-9")
        self.assertEqual(s.executed[-1][1]["st"], "drafting")  # 不改狀態
        self.assertIn("current_revision_id = :crid", s.executed[-1][0])


class SectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_upsert_on_conflict_do_update(self):
        s = _FakeSession()
        with _use(s):
            await rw.upsert_section(
                "run-1", 0, section_key="exec_summary", heading="執行摘要",
                draft_markdown="草稿", evidence_ids=["a1b2"], status="drafted",
            )
        sql, params = s.executed[-1]
        self.assertIn("ON CONFLICT (run_id, position) DO UPDATE", sql)
        self.assertIn("draft_markdown = COALESCE(EXCLUDED.draft_markdown", sql)
        self.assertIn("CAST(:evids AS text[])", sql)
        self.assertEqual(params["pos"], 0)
        self.assertEqual(params["evids"], ["a1b2"])

    async def test_load_run_maps_row(self):
        row = (
            "id-1", "rk", "drafting", {"a": 1},
            {"outline_ready": True, "final_positions": [1, 0]}, 2, "rev-id", "doc-id",
        )
        s = _FakeSession(results=[[row]])
        with _use(s):
            out = await rw.load_run("id-1")
        self.assertEqual(out["status"], "drafting")
        self.assertEqual(out["revision"], 2)
        self.assertEqual(out["current_revision_id"], "rev-id")
        self.assertEqual(out["report_doc_id"], "doc-id")
        self.assertTrue(out["checkpoint"].outline_ready)
        self.assertEqual(out["checkpoint"].final_positions, [0, 1])

    async def test_load_run_missing(self):
        s = _FakeSession(results=[[]])
        with _use(s):
            self.assertIsNone(await rw.load_run("ghost"))

    async def test_load_sections_maps_rows(self):
        rows = [
            (0, "exec_summary", "執行摘要", "草稿0", None, ["e1"], "drafted"),
            (1, "key_findings", "關鍵發現", None, "最終1", ["e2", "e3"], "final"),
        ]
        s = _FakeSession(results=[rows])
        with _use(s):
            out = await rw.load_sections("run-1")
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["position"], 0)
        self.assertEqual(out[1]["evidence_ids"], ["e2", "e3"])
        self.assertEqual(out[1]["status"], "final")


# ── T3 大綱 ─────────────────────────────────────────────────────────────────
class OutlineTests(unittest.TestCase):
    def test_skeleton_always_present(self):
        o = rw.build_outline("台積電展望", None, [{"heading": "先進製程", "topic": "N2 量產"}])
        keys = [s["key"] for s in o["sections"]]
        self.assertEqual(keys[0], "exec_summary")
        self.assertEqual(keys[1], "key_findings")
        self.assertEqual(keys[-1], "risk_outlook")
        self.assertIn("analysis", keys)
        self.assertEqual(
            [s["position"] for s in o["sections"]], list(range(len(o["sections"])))
        )
        self.assertTrue(o["title"])

    def test_default_title_and_no_analysis(self):
        o = rw.build_outline("主題X", "", [])
        self.assertEqual(o["title"], "主題X 深度研報")
        self.assertEqual([s["kind"] for s in o["sections"]], ["framing", "framing", "framing"])

    def test_dedup_clean_and_topic_default(self):
        o = rw.build_outline(
            "q", "T", [{"heading": "營運  展望"}, {"heading": "營運 展望"}, {"heading": ""}]
        )
        analysis = [s for s in o["sections"] if s["kind"] == "analysis"]
        self.assertEqual(len(analysis), 1)  # 折疊空白後去重 + 跳過空 heading
        self.assertEqual(analysis[0]["heading"], "營運 展望")
        self.assertEqual(analysis[0]["topic"], "營運 展望")  # topic 缺省用 heading

    def test_sections_from_outline_roundtrip(self):
        o = rw.build_outline("q", "T", [{"heading": "A", "topic": "ta"}])
        secs = rw.sections_from_outline(o)
        self.assertEqual(
            [s["heading"] for s in secs], [s["heading"] for s in o["sections"]]
        )

    def test_sections_from_outline_bad_shape(self):
        self.assertEqual(rw.sections_from_outline(None), [])
        self.assertEqual(rw.sections_from_outline({"sections": "x"}), [])
        self.assertEqual(rw.sections_from_outline({"sections": [{"key": "x"}]}), [])


def _fake_stream(text_out):
    def factory(*a, **k):
        async def gen():
            yield text_out
        return gen()
    return factory


def _raise_stream(*a, **k):
    async def gen():
        raise RuntimeError("boom")
        yield ""  # 使之為 async generator（不可達）
    return gen()


class PlanOutlineTests(unittest.IsolatedAsyncioTestCase):
    async def test_valid_outline(self):
        js = '{"title":"標題","analysis_subsections":[{"heading":"營運","topic":"營運展望"}]}'
        with patch.object(rw, "stream_completion", _fake_stream(js)):
            o = await rw.plan_outline("台積電", "ctx")
        self.assertEqual(o["title"], "標題")
        self.assertTrue(
            any(s["kind"] == "analysis" and s["heading"] == "營運" for s in o["sections"])
        )

    async def test_no_analysis_returns_none(self):
        with patch.object(rw, "stream_completion", _fake_stream('{"analysis_subsections":[]}')):
            self.assertIsNone(await rw.plan_outline("q", "ctx"))

    async def test_parse_failure_returns_none(self):
        with patch.object(rw, "stream_completion", _fake_stream("這不是 JSON")):
            self.assertIsNone(await rw.plan_outline("q", "ctx"))

    async def test_llm_exception_returns_none(self):
        with patch.object(rw, "stream_completion", _raise_stream):
            self.assertIsNone(await rw.plan_outline("q", "ctx"))


# ── T4 逐節檢索 ──────────────────────────────────────────────────────────────
class _AsyncRec:
    """記錄呼叫並回固定值的 async callable。"""

    def __init__(self, ret):
        self.ret = ret
        self.calls: list[tuple[tuple, dict]] = []

    async def __call__(self, *a, **k):
        self.calls.append((a, k))
        return self.ret


def _patch_plan(*subquery_texts):
    from types import SimpleNamespace

    plan = SimpleNamespace(
        subqueries=[SimpleNamespace(text=t) for t in subquery_texts],
        profile="report",
        degraded=False,
    )

    async def fake(topic, **k):
        return plan

    return patch.object(rw, "plan_queries", fake)


class RetrieveForSectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_single_query_uses_retrieve_context(self):
        rc = _AsyncRec((["src"], "ctx"))
        rcm = _AsyncRec((["x"], "y"))
        with _patch_plan("topic"), patch.object(rw, "retrieve_context", rc), patch.object(
            rw, "retrieve_context_multi", rcm
        ):
            sources, ctx = await rw.retrieve_for_section("topic")
        self.assertEqual((sources, ctx), (["src"], "ctx"))
        self.assertEqual((len(rc.calls), len(rcm.calls)), (1, 0))
        _, kw = rc.calls[0]
        self.assertEqual(kw["max_reports"], 8)  # 逐節配額（非整份 25）
        self.assertEqual(kw["max_passages"], 4)  # 非整份 6
        self.assertEqual(kw["max_chars"], 12000)  # 非整份 40000
        self.assertEqual(kw["rerank_top_m"], 40)  # 逐節 rerank 候選下修（非 120）

    async def test_multi_query_uses_retrieve_context_multi(self):
        rc = _AsyncRec((["x"], "y"))
        rcm = _AsyncRec((["src"], "ctx"))
        with _patch_plan("topic", "面向2"), patch.object(
            rw, "retrieve_context", rc
        ), patch.object(rw, "retrieve_context_multi", rcm):
            sources, ctx = await rw.retrieve_for_section("topic", filters={"market": "TW"})
        self.assertEqual((len(rc.calls), len(rcm.calls)), (0, 1))
        args, kw = rcm.calls[0]
        self.assertEqual(args[0], "topic")
        self.assertEqual(args[1], ["topic", "面向2"])  # queries 首項為原題
        self.assertEqual(kw["filters"], {"market": "TW"})


# ── T5 帳本組裝 + render_citations 單次 ─────────────────────────────────────
class LedgerAssemblyTests(unittest.TestCase):
    def test_build_ledger_report_level_dedup(self):
        s1 = [
            {"report_id": "r1", "file_name": "a.pdf", "market": "TW", "report_date": "2026-01-01"},
            {"report_id": "r2", "file_name": "b.pdf", "market": "TW", "report_date": "2026-02-01"},
        ]
        s2 = [{"report_id": "r1", "file_name": "a.pdf", "market": "TW", "report_date": "2026-01-01"}]
        ledger, per = rw.build_ledger([s1, s2])
        self.assertEqual(len(list(ledger)), 2)  # r1/r2 各一（去重）
        self.assertEqual(len(per[0]), 2)
        self.assertEqual(len(per[1]), 1)
        self.assertEqual(per[1][0], per[0][0])  # r1 跨節同一 evidence_id

    def test_build_ledger_skips_missing_report_id(self):
        ledger, per = rw.build_ledger([[{"file_name": "x"}]])
        self.assertEqual(len(list(ledger)), 0)
        self.assertEqual(per, [[]])

    def test_assemble_body_skeleton_and_strip_heading(self):
        secs = [
            {"key": "exec_summary", "heading": "執行摘要", "kind": "framing", "draft": "## 執行摘要\n摘要內容"},
            {"key": "key_findings", "heading": "關鍵發現", "kind": "framing", "draft": "發現內容"},
            {"key": "analysis", "heading": "子題A", "kind": "analysis", "draft": "### 子題A\nA內容"},
            {"key": "analysis", "heading": "子題B", "kind": "analysis", "draft": "B內容"},
            {"key": "risk_outlook", "heading": "風險與展望", "kind": "framing", "draft": "風險內容"},
        ]
        body = rw.assemble_body("我的研報", secs)
        self.assertTrue(body.startswith("# 我的研報"))
        for h in ("## 執行摘要", "## 關鍵發現", "## 風險與展望", "### 子題A", "### 子題B"):
            self.assertIn(h, body)
        self.assertEqual(body.count("## 重點分析"), 1)  # analysis 只包裝一次
        self.assertEqual(body.count("執行摘要"), 1)  # 草稿前導標題被剝除，不雙標題

    def test_assemble_final_citation_numbering(self):
        ledger = rw.EvidenceLedger()
        e1 = ledger.add_corpus(report_id="r1", file_name="a.pdf", market="TW", report_date="2026-01-01")
        e2 = ledger.add_corpus(report_id="r2", file_name="b.pdf", market="US", report_date="2026-02-01")
        secs = [
            {"key": "exec_summary", "heading": "執行摘要", "kind": "framing",
             "draft": f"看好[[ev:{e1.evidence_id}]]"},
            {"key": "analysis", "heading": "A", "kind": "analysis",
             "draft": f"分析[[ev:{e2.evidence_id}]]又見[[ev:{e1.evidence_id}]]"},
            {"key": "risk_outlook", "heading": "風險與展望", "kind": "framing", "draft": "風險"},
        ]
        final, rendered = rw.assemble_final("研報", secs, ledger)
        self.assertEqual(rendered.n_unknown, 0)
        self.assertIn("看好[1]", final)  # e1 首見=1
        self.assertIn("分析[2]", final)  # e2 第二見=2
        self.assertIn("又見[1]", final)  # e1 全文恆同號
        self.assertIn("## 引用來源", final)
        self.assertIn("[1] a.pdf（TW·2026-01-01）", final)
        self.assertIn("[2] b.pdf（US·2026-02-01）", final)

    def test_assemble_final_unknown_id_counted_and_stripped(self):
        ledger = rw.EvidenceLedger()
        ledger.add_corpus(report_id="r1", file_name="a.pdf")
        secs = [{"key": "analysis", "heading": "A", "kind": "analysis",
                 "draft": "引用[[ev:deadbeefdeadbeef]]"}]
        final, rendered = rw.assemble_final("研報", secs, ledger)
        self.assertEqual(rendered.n_unknown, 1)  # 未知 id 計數（把關訊號）
        self.assertNotIn("[[ev:", final)  # 內部 token 不漏到輸出


# ── T6 逐節草稿 + 串流編排 ───────────────────────────────────────────────────
def _eid(report_id):
    return rw.EvidenceLedger().add_corpus(report_id=report_id).evidence_id


class EvidenceContextTests(unittest.TestCase):
    def test_relabels_n_to_evidence_and_registers(self):
        from types import SimpleNamespace

        ledger = rw.EvidenceLedger()
        src = SimpleNamespace(n=1, report_id="r1", file_name="a.pdf", market="TW", report_date="2026-01-01")
        labeled, ids = rw._evidence_context([src], "[1] 報告：a.pdf\n內容片段", ledger)
        self.assertEqual(ids, [_eid("r1")])
        self.assertIn(f"[[ev:{_eid('r1')}]] 報告：a.pdf", labeled)
        self.assertEqual(len(list(ledger)), 1)  # 已註冊

    def test_skips_missing_report_id(self):
        from types import SimpleNamespace

        ledger = rw.EvidenceLedger()
        labeled, ids = rw._evidence_context(
            [SimpleNamespace(n=1, report_id=None, file_name="x")], "[1] 報告：x", ledger
        )
        self.assertEqual(ids, [])
        self.assertIn("[1] 報告：x", labeled)  # 無 eid 對應時保留原標記


def _draft_stream(text_out):
    def factory(*a, **k):
        async def gen():
            yield text_out
        return gen()
    return factory


class DraftReportTests(unittest.IsolatedAsyncioTestCase):
    async def test_fallback_when_outline_none(self):
        async def none_outline(*a, **k):
            return None

        with patch.object(rw, "plan_outline", none_outline):
            events = [e async for e in rw.draft_report("q", "ctx")]
        self.assertEqual(events, [("__fallback__", None)])

    async def test_fallback_when_no_analysis(self):
        async def framing_only(*a, **k):
            return rw.build_outline("q", "T", [])  # 無 analysis 子節

        with patch.object(rw, "plan_outline", framing_only):
            events = [e async for e in rw.draft_report("q", "ctx")]
        self.assertEqual(events[0], ("__fallback__", None))

    async def test_happy_path_events_and_citations(self):
        from types import SimpleNamespace

        eid = _eid("r1")
        outline = {
            "title": "研報T",
            "sections": [
                {"position": 0, "key": "exec_summary", "heading": "執行摘要", "topic": "q", "kind": "framing"},
                {"position": 1, "key": "analysis", "heading": "面向A", "topic": "ta", "kind": "analysis"},
                {"position": 2, "key": "risk_outlook", "heading": "風險與展望", "topic": "r", "kind": "framing"},
            ],
        }

        async def fake_outline(*a, **k):
            return outline

        src = SimpleNamespace(n=1, report_id="r1", file_name="a.pdf", market="TW", report_date="2026-01-01")

        async def fake_retrieve(topic, **k):
            return ([src], "[1] 報告：a.pdf\n內容片段")

        with patch.object(rw, "plan_outline", fake_outline), patch.object(
            rw, "retrieve_for_section", fake_retrieve
        ), patch.object(rw, "stream_completion", _draft_stream(f"分析結論[[ev:{eid}]]")):
            events = [e async for e in rw.draft_report("台積電", "ctx")]

        kinds = [e[0] for e in events]
        self.assertIn("status", kinds)
        self.assertIn("token", kinds)
        self.assertEqual(kinds.count("section_draft"), 3)  # 三節各一
        self.assertIn("document_revision", kinds)
        self.assertEqual(kinds[-1], "__final__")

        final = events[-1][1]
        self.assertEqual(final["n_unknown"], 0)  # 引用 token 皆可解析
        self.assertIn("[1]", final["markdown"])  # [[ev:]] → [n]
        self.assertNotIn("[[ev:", final["markdown"])  # 內部 token 不漏
        self.assertIn("## 引用來源", final["markdown"])
        self.assertIn("a.pdf", final["markdown"])
        self.assertEqual(final["sources"][0]["report_id"], "r1")
        self.assertEqual(final["manifest"]["schema_version"], 1)

    async def test_section_retrieve_failure_is_fail_open(self):
        outline = {
            "title": "T",
            "sections": [
                {"position": 0, "key": "analysis", "heading": "A", "topic": "ta", "kind": "analysis"},
            ],
        }

        async def fake_outline(*a, **k):
            return outline

        async def boom_retrieve(topic, **k):
            raise RuntimeError("retrieval down")

        with patch.object(rw, "plan_outline", fake_outline), patch.object(
            rw, "retrieve_for_section", boom_retrieve
        ), patch.object(rw, "stream_completion", _draft_stream("在無片段下審慎撰寫")):
            events = [e async for e in rw.draft_report("q", "ctx")]
        # 檢索炸掉仍走完（fail-open），產出最終文件
        self.assertEqual(events[-1][0], "__final__")
        self.assertIn("在無片段下審慎撰寫", events[-1][1]["markdown"])


if __name__ == "__main__":
    unittest.main()
