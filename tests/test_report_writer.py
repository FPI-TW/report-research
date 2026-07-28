"""M7 T2：report_writer 狀態機地基測試。

純邏輯（轉換守門／request_key 合成／checkpoint 序列化）以真斷言驗；DB 函式以
fake session 驗發出的 SQL/params（沿用本 repo「測試不連真 DB」慣例——DDL 正確性
已由 make schema 對真 DB 驗過）。
"""

from __future__ import annotations

import asyncio
import json
import time
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

    def test_forward_jump_allowed(self):
        """向前跳階是**刻意允許**的（審查 F1）。

        原本只准單步前進，配上 `_audit` 對每次寫入 fail-open，會讓任何一次 DB 抖動
        毒化整條稽核鏈：掉了 outlining 那一次寫入後，drafting/verifying/rendering/
        completed 全部變成非法轉換再被靜默吞掉，於是成功出貨的研報永遠停在
        retrieving 且 error_detail 為 NULL。守門要擋的是倒退，不是跳階。
        """
        self.assertTrue(is_valid_transition("queued", "rendering"))
        self.assertTrue(is_valid_transition("retrieving", "completed"))
        self.assertTrue(is_valid_transition("outlining", "completed"))

    def test_backward_transition_invalid(self):
        """倒退仍須拒絕——放寬跳階不等於放棄守門。"""
        self.assertFalse(is_valid_transition("rendering", "drafting"))
        self.assertFalse(is_valid_transition("completed", "retrieving"))
        self.assertFalse(is_valid_transition("drafting", "queued"))

    def test_dropped_intermediate_write_still_reaches_completed(self):
        """F1 回歸：中繼轉換整段掉光，仍必須能把 run 標成 completed。

        模擬 `_audit` 吞掉 outlining/drafting/verifying/rendering 四次寫入後，
        report.py 仍會以 run 實際狀態 retrieving 呼叫 completed。
        """
        current = "retrieving"  # outlining 之後全部寫入失敗，狀態停在此
        for dropped in ("outlining", "drafting", "verifying", "rendering"):
            self.assertTrue(is_valid_transition(current, dropped))  # 各自單獨仍合法
        self.assertTrue(
            is_valid_transition(current, "completed"),
            "掉寫入後無法收尾 → report_run 永遠假性卡住",
        )

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

    def test_locale_changes_key(self):
        """不同輸出語言是不同產出物,不可互相去重（M10）。

        漏掉 locale 時:同對話切成英文後重問同一句 → 命中舊鍵 → 被當重複請求,
        直接回傳先前那份**中文** PDF,而且事件序是正常的 done、無任何錯誤訊息。
        """
        zh = synthesize_request_key("q", model="m", conversation_id="c", locale="zh-Hant")
        en = synthesize_request_key("q", model="m", conversation_id="c", locale="en")
        self.assertNotEqual(zh, en)

    def test_locale_defaults_to_zh_hant(self):
        """未帶 locale 等同 zh-Hant——與全專案 fail-open→zh-Hant 的慣例一致。

        若預設是 None/""，舊呼叫端算出的鍵會與生產路徑（一律先 resolve_locale）
        算出的鍵不同,冪等去重會靜默失效。
        """
        self.assertEqual(
            synthesize_request_key("q", model="m", conversation_id="c"),
            synthesize_request_key("q", model="m", conversation_id="c", locale="zh-Hant"),
        )

    def test_template_id_not_part_of_key(self):
        """契約防護:template_id 不得進鍵。

        換版型屬 M9b 的 rendition 路徑（零 LLM 換皮）;把它加進冪等鍵等於每次換版型
        都重跑 5-12 分鐘 LLM,抵銷該路徑的設計意圖。此測試在有人「順手加上去」時變紅。
        """
        import inspect

        params = inspect.signature(synthesize_request_key).parameters
        self.assertNotIn("template_id", params)


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
        # INSERT RETURNING 空 → 重試用的條件式 UPDATE 也空（非終端失敗態）→ SELECT 既有
        s = _FakeSession(results=[[], [], [("exist-id",)]])
        with _use(s):
            run_id, is_new = await rw.open_run("rk-1")
        self.assertEqual((run_id, is_new), ("exist-id", False))
        self.assertTrue(s.executed[2][0].strip().upper().startswith("SELECT"))

    async def test_failed_run_is_reset_to_queued_and_retried(self):
        """終端失敗態 → 原子重置回 queued 並回 is_new=True（前端「重試」鈕的命脈）。

        沒有這個分支時 report.py 對任何非 completed 狀態一律回錯，該
        （問題×對話×語言）組合永久無法再生成。
        """
        s = _FakeSession(results=[[], [("failed-id",)]])  # INSERT 空 → UPDATE 命中 failed 列
        with _use(s):
            run_id, is_new = await rw.open_run("rk-1")
        self.assertEqual((run_id, is_new), ("failed-id", True))
        upd = s.executed[1][0]
        self.assertTrue(upd.strip().upper().startswith("UPDATE"))
        # 只重置終端失敗態,不得碰 in-flight／completed
        self.assertIn("status IN ('failed', 'cancelled')", upd)
        self.assertIn("status = 'queued'", upd)
        # 殘留的失敗狀態必須清乾淨,否則下一輪帶著上次的錯誤訊息與 revision 指標跑
        self.assertIn("error_detail = NULL", upd)
        self.assertIn("current_revision_id = NULL", upd)
        # 重置後 status='queued',呼叫端 advance_status(expected_current="queued") 才接得上
        self.assertGreaterEqual(s.commits, 1)

    async def test_completed_run_not_reset(self):
        """completed 不在重置條件內：同題重送必須回既有文件,不可重跑 5-12 分鐘 LLM。"""
        s = _FakeSession(results=[[], [], [("done-id",)]])  # UPDATE 不命中(status=completed)
        with _use(s):
            run_id, is_new = await rw.open_run("rk-1")
        self.assertEqual((run_id, is_new), ("done-id", False))


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
        """倒退轉換仍須 raise（審查 F1 放寬的是**跳階**，不是倒退）。

        原本此處以 queued→rendering 當非法案例；跳階改為合法後，改用真正該擋的
        倒退：rendering→drafting。
        """
        s = _FakeSession(results=[[("rendering",)]])
        with _use(s):
            with self.assertRaises(InvalidTransition):
                await rw.advance_status("run-1", "drafting")

    async def test_forward_jump_does_not_raise(self):
        """F1 回歸：中繼寫入掉光後，仍必須能從 retrieving 直接收尾到 completed。"""
        s = _FakeSession(results=[[("retrieving",)]])
        with _use(s):
            await rw.advance_status("run-1", "completed")
        self.assertEqual(s.executed[-1][1]["st"], "completed")

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
        # 預設（reset=False）維持 COALESCE 保留語意
        self.assertIn("COALESCE(EXCLUDED.draft_markdown", sql)
        self.assertIn("CAST(:evids AS text[])", sql)
        self.assertEqual(params["pos"], 0)
        self.assertEqual(params["evids"], ["a1b2"])
        self.assertIs(params["reset"], False)

    async def test_upsert_reset_clears_previous_attempt(self):
        """reset=True 明確把 draft/final/evidence_ids 清成 NULL。

        重試同一 run 時大綱會重新規劃,同一 position 可能換成不同標題;不清空的話
        上一次嘗試的 draft_markdown 會被新標題「領養」,留下章節數對、標題對、
        內容全錯的稽核列,而且沒有任何一層會報錯。
        """
        s = _FakeSession()
        with _use(s):
            await rw.upsert_section(
                "run-1", 0, section_key="exec_summary", heading="執行摘要",
                status="pending", reset=True,
            )
        sql, params = s.executed[-1]
        self.assertIs(params["reset"], True)
        for col in ("draft_markdown", "final_markdown", "evidence_ids"):
            self.assertIn(f"{col} = CASE WHEN :reset THEN NULL", sql)

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

    async def test_section_retrieve_failure_does_not_ship_ungrounded_report(self):
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
        # run-level 曾有脈絡不代表每一節都可在無證據下自由發揮；網搜關閉時，逐節
        # 檢索故障不得偽裝成 completed 的零證據研報。
        self.assertEqual(events[-1][0], "__fallback__")

    async def test_web_enabled_empty_section_requires_recorded_web_sources(self):
        """無語料時即使開網搜，也不能接受沒有外部來源帳目的泛泛文字。"""
        outline = {
            "title": "T",
            "sections": [
                {"position": 0, "key": "analysis", "heading": "A", "topic": "ta", "kind": "analysis"},
            ],
        }

        async def fake_outline(*a, **k):
            return outline

        async def no_sources(*a, **k):
            return [], ""

        with patch.object(rw, "plan_outline", fake_outline), patch.object(
            rw, "retrieve_for_section", no_sources
        ), patch.object(rw, "stream_completion", _draft_stream("沒有列來源的網路結論")):
            events = [e async for e in rw.draft_report(
                "q", "ctx", web_enabled=True, thin_coverage=3)]

        self.assertEqual(events[-1][0], "__fallback__")


# ── 共用：以 stub 過的 outline/檢索/串流跑 draft_report ─────────────────────
def _outline_3():
    return {
        "title": "研報T",
        "sections": [
            {"position": 0, "key": "exec_summary", "heading": "執行摘要",
             "topic": "q", "kind": "framing"},
            {"position": 1, "key": "analysis", "heading": "面向A",
             "topic": "ta", "kind": "analysis"},
            {"position": 2, "key": "risk_outlook", "heading": "風險與展望",
             "topic": "r", "kind": "framing"},
        ],
    }


async def _run_draft(outline, *, draft="內文", run_id=None, extra=None, **kw):
    from types import SimpleNamespace

    src = SimpleNamespace(n=1, report_id="r1", file_name="a.pdf", market="TW",
                          report_date="2026-01-01")

    async def fake_outline(*a, **k):
        return outline

    async def fake_retrieve(topic, **k):
        return ([src], "[1] 報告：a.pdf\n片段")

    stack = [
        patch.object(rw, "plan_outline", fake_outline),
        patch.object(rw, "retrieve_for_section", fake_retrieve),
    ]
    if not any(getattr(c, "attribute", None) == "stream_completion"
               for c in (extra or [])):
        stack.append(patch.object(rw, "stream_completion", _draft_stream(draft)))
    stack.extend(extra or [])
    for cm in stack:
        cm.__enter__()
    try:
        return [e async for e in rw.draft_report("q", "ctx", run_id=run_id, **kw)]
    finally:
        for cm in reversed(stack):
            cm.__exit__(None, None, None)


def _multi_stream(texts):
    """依序回不同輸出的 stream_completion 替身（耗盡則重複最後一個）。"""
    box = {"i": 0}

    def factory(*a, **k):
        i = min(box["i"], len(texts) - 1)
        box["i"] += 1
        out = texts[i]

        async def gen():
            if out is None:
                raise RuntimeError("LLM down")
            yield out

        return gen()

    return factory


# ── #1：run 持久化全 fail-open（稽核失敗絕不阻斷生成）──────────────────────
class DraftReportRunPersistenceTests(unittest.IsolatedAsyncioTestCase):
    """run_id 給定時的狀態機持久化——**先前沒有任何測試碰過這條路**（審查 #6）：
    draft_report 的呼叫全部省略 run_id，`if run_id:` 區塊在整個測試套件裡一行都沒跑過。
    """

    async def test_run_id_drives_state_machine_and_sections(self):
        adv: list = []
        ups: list = []

        async def rec_advance(rid, status, **k):
            adv.append((rid, status))

        async def rec_upsert(rid, pos, **k):
            ups.append((rid, pos, k.get("status")))

        events = await _run_draft(
            _outline_3(), run_id="run-9",
            extra=[patch.object(rw, "advance_status", rec_advance),
                   patch.object(rw, "upsert_section", rec_upsert)],
        )
        self.assertEqual(events[-1][0], "__final__")
        # status="" 是預算遙測的 checkpoint 寫入（沿用 advance_status 的
        # `target = status or current` 語義，不推進狀態），不屬狀態序列。
        self.assertEqual(
            [s for _, s in adv if s], ["outlining", "drafting", "verifying", "rendering"]
        )
        # 每節都要即時落一次遙測——失敗的 run 才有耗時資料可供下次校準
        self.assertGreaterEqual(len([s for _, s in adv if not s]), 3)
        self.assertTrue(all(rid == "run-9" for rid, _ in adv))
        # 三節各：pending（展開）→ drafted（草稿）→ final（組裝後）
        self.assertEqual([p for _, p, s in ups if s == "pending"], [0, 1, 2])
        self.assertEqual([p for _, p, s in ups if s == "drafted"], [0, 1, 2])
        self.assertEqual([p for _, p, s in ups if s == "final"], [0, 1, 2])

    async def test_upsert_section_failure_does_not_abort_generation(self):
        """審查 #1：草稿 commit（純稽核）在已吐 token 後失敗——裸 await 會讓整份研報
        作廢（使用者拿不到研報、N 次 LLM 全丟）。run 只是稽核紀錄，必須 fail-open。"""
        calls = {"n": 0}

        async def flaky_upsert(rid, pos, **k):
            calls["n"] += 1
            if calls["n"] >= 4:   # 前 3 次＝outline 展開；第 4 次起＝草稿 commit
                raise RuntimeError("DB connection reset")

        async def ok_advance(*a, **k):
            return None

        events = await _run_draft(
            _outline_3(), run_id="run-9",
            extra=[patch.object(rw, "advance_status", ok_advance),
                   patch.object(rw, "upsert_section", flaky_upsert)],
        )
        kinds = [e[0] for e in events]
        self.assertEqual(kinds[-1], "__final__")           # 生成照常完成
        self.assertEqual(kinds.count("section_draft"), 3)  # 事件序零差異
        self.assertNotIn("__failed__", kinds)
        self.assertNotIn("__fallback__", kinds)

    async def test_advance_status_failure_does_not_abort_generation(self):
        async def dying(*a, **k):
            raise RuntimeError("DB down")

        async def ok_upsert(*a, **k):
            return None

        events = await _run_draft(
            _outline_3(), run_id="run-9",
            extra=[patch.object(rw, "advance_status", dying),
                   patch.object(rw, "upsert_section", ok_upsert)],
        )
        self.assertEqual(events[-1][0], "__final__")

    async def test_all_audit_writes_failing_still_produces_final(self):
        async def dying(*a, **k):
            raise RuntimeError("DB down")

        events = await _run_draft(
            _outline_3(), run_id="run-9",
            extra=[patch.object(rw, "advance_status", dying),
                   patch.object(rw, "upsert_section", dying)],
        )
        self.assertEqual(events[-1][0], "__final__")
        self.assertIn("## 執行摘要", events[-1][1]["markdown"])

    async def test_audit_propagates_cancellation(self):
        """客戶端斷線的取消不得被 fail-open 吞掉。"""
        async def cancelled(*a, **k):
            raise asyncio.CancelledError()

        with self.assertRaises(asyncio.CancelledError):
            await rw._audit(cancelled, "x")

    async def test_final_reports_evidence_total(self):
        """__final__ 帶 n_evidence（共用帳本大小）＝ source_citation_rate 的真分母。"""
        events = await _run_draft(_outline_3(), draft="無引用內文")
        self.assertEqual(events[-1][0], "__final__")
        self.assertEqual(events[-1][1]["n_evidence"], 1)  # r1 跨三節去重
        self.assertEqual(events[-1][1]["sources"], [])    # 沒引用 → [n] 表為空


# ── #5：失敗節處置（決策 #2）＋ n_unknown 把關（決策 #4）────────────────────
class SectionFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_analysis_subsection_is_skipped_not_shipped_empty(self):
        """決策 #2：動態子節草稿耗盡 → 跳過，**不得以空標題出貨**
        （section_coverage 只認標題，空章節在指標上看不出來）。"""
        outline = {
            "title": "T",
            "sections": [
                {"position": 0, "key": "exec_summary", "heading": "執行摘要",
                 "topic": "q", "kind": "framing"},
                {"position": 1, "key": "analysis", "heading": "壞子節",
                 "topic": "bad", "kind": "analysis"},
                {"position": 2, "key": "analysis", "heading": "好子節",
                 "topic": "good", "kind": "analysis"},
                {"position": 3, "key": "risk_outlook", "heading": "風險與展望",
                 "topic": "r", "kind": "framing"},
            ],
        }
        # 節 0 OK → 節 1 全空（含重試）→ 節 2、3 OK
        events = await _run_draft(
            outline,
            extra=[patch.object(
                rw, "stream_completion",
                _multi_stream(["摘要內文", "", "", "好子節內文", "風險內文"]),
            )],
        )
        self.assertEqual(events[-1][0], "__final__")
        md = events[-1][1]["markdown"]
        self.assertNotIn("### 壞子節", md)   # 空節整個不出現（無空標題）
        self.assertIn("### 好子節", md)
        self.assertIn("## 重點分析", md)
        # 跳過的節不入 claim_evidence（evidence_link_coverage 分母不含未出貨的節）
        self.assertEqual(sorted(events[-1][1]["claim_evidence"]), ["0", "2", "3"])

    async def test_skeleton_section_empty_before_token_falls_back(self):
        """硬邊界前：骨架節耗盡 → __fallback__（退單次，前端事件序零差異）。"""
        events = await _run_draft(
            _outline_3(),
            extra=[patch.object(rw, "stream_completion", _multi_stream(["", ""]))],
        )
        kinds = [e[0] for e in events]
        self.assertIn(("__fallback__", None), events)
        self.assertNotIn("token", kinds)   # 未吐內容才可退單次

    async def test_skeleton_section_empty_after_token_fails(self):
        """硬邊界後：骨架節耗盡 → __failed__（不得退單次，會重覆內容）。"""
        events = await _run_draft(
            _outline_3(),
            extra=[patch.object(
                rw, "stream_completion",
                _multi_stream(["摘要內文", "分析內文", "", ""]),
            )],
        )
        kinds = [e[0] for e in events]
        self.assertIn("token", kinds)
        self.assertEqual(events[-1][0], "__failed__")
        self.assertIn("detail", events[-1][1])

    async def test_all_analysis_skipped_fails_missing_chapter(self):
        """「重點分析」也是五章骨架之一：動態子節全滅＝缺章，不可出貨。"""
        events = await _run_draft(
            _outline_3(),
            extra=[patch.object(
                rw, "stream_completion",
                _multi_stream(["摘要內文", "", "", "風險內文"]),
            )],
        )
        self.assertEqual(events[-1][0], "__failed__")


class NUnknownGateTests(unittest.IsolatedAsyncioTestCase):
    """決策 #4／spec §3：n_unknown>0 → 有界重生違規節；耗盡 → failed（非只 log）。"""

    async def test_unknown_id_regenerated_then_ok(self):
        eid = _eid("r1")
        events = await _run_draft(
            _outline_3(),
            extra=[patch.object(
                rw, "stream_completion",
                _multi_stream([
                    "摘要[[ev:deadbeefdeadbeef]]",  # pos 0：抄歪成不存在的 id
                    "分析內文", "風險內文",
                    f"摘要重生[[ev:{eid}]]",         # pos 0 重生後正確
                ]),
            )],
        )
        self.assertEqual(events[-1][0], "__final__")
        self.assertEqual(events[-1][1]["n_unknown"], 0)
        self.assertIn("摘要重生[1]", events[-1][1]["markdown"])
        # 重生再吐一次該 position 的 section_draft（覆寫語意）
        pos0 = [p for k, p in events if k == "section_draft" and p["position"] == 0]
        self.assertEqual(len(pos0), 2)
        self.assertIn("摘要重生", pos0[-1]["markdown"])

    async def test_unknown_id_exhausted_fails(self):
        """重生後仍抄歪 → __failed__。只 log 等於出貨一份「有主張、無引用」的研報。"""
        events = await _run_draft(
            _outline_3(),
            extra=[patch.object(
                rw, "stream_completion",
                _multi_stream(["摘要[[ev:deadbeefdeadbeef]]", "分析內文", "風險內文",
                               "摘要仍歪[[ev:deadbeefdeadbeef]]"]),
            )],
        )
        self.assertEqual(events[-1][0], "__failed__")
        self.assertEqual(events[-1][1]["detail"], "研報引用標記異常")

    async def test_malformed_placeholder_also_gated(self):
        """變形佔位（大寫/過短）同樣計入 n_unknown → 同一把關。"""
        events = await _run_draft(
            _outline_3(),
            extra=[patch.object(
                rw, "stream_completion",
                _multi_stream(["摘要[[ev:ZZZ]]", "分析內文", "風險內文",
                               "摘要仍歪[[ev:ZZZ]]"]),
            )],
        )
        self.assertEqual(events[-1][0], "__failed__")

    async def test_plain_numeric_citation_without_ledger_source_fails(self):
        """模型直接輸出的 [42] 也必須受正文引用 gate 保護。"""
        events = await _run_draft(
            _outline_3(),
            extra=[patch.object(
                rw, "stream_completion",
                _multi_stream(["摘要[42]", "分析內文", "風險內文"]),
            )],
        )
        self.assertEqual(events[-1][0], "__failed__")
        self.assertEqual(events[-1][1]["detail"], "研報引用編號異常")


# ── #3：逐節網搜（allow_web／searching_web／外部參考彙整）───────────────────
class StreamSectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_allow_web_forwarded_and_search_status_emitted(self):
        cap = {}

        def stream(*a, **k):
            cap["allow_web"] = k.get("allow_web")

            async def gen():
                yield rw.SEARCH_EVENT
                yield "網路補充內文（網路）"

            return gen()

        with patch.object(rw, "stream_completion", stream):
            evs = [e async for e in rw._stream_section(
                "sys", "p", timeout=1, retry=0, model="m", allow_web=True)]

        self.assertIs(cap["allow_web"], True)
        stages = [p["stage"] for k, p in evs if k == "status"]
        self.assertEqual(stages, ["searching_web", "writing"])  # 搜尋後重設回 writing
        self.assertEqual(evs[-1], ("__text__", "網路補充內文（網路）"))

    async def test_search_event_never_leaks_into_text(self):
        def stream(*a, **k):
            async def gen():
                yield rw.SEARCH_EVENT
                yield "內文"
                yield rw.SEARCH_EVENT
            return gen()

        with patch.object(rw, "stream_completion", stream):
            evs = [e async for e in rw._stream_section(
                "sys", "p", timeout=1, retry=0, model="m", allow_web=True)]
        self.assertEqual(evs[-1], ("__text__", "內文"))
        stages = [p["stage"] for k, p in evs if k == "status"]
        self.assertEqual(stages.count("searching_web"), 1)  # 只發一次

    async def test_retry_only_when_nothing_streamed(self):
        with patch.object(rw, "stream_completion", _multi_stream([None, "第二次成功"])):
            evs = [e async for e in rw._stream_section(
                "sys", "p", timeout=1, retry=1, model="m")]
        self.assertEqual(evs[-1], ("__text__", "第二次成功"))

    async def test_exhausted_returns_empty_text(self):
        with patch.object(rw, "stream_completion", _multi_stream([None, None])):
            evs = [e async for e in rw._stream_section(
                "sys", "p", timeout=1, retry=1, model="m")]
        self.assertEqual(evs[-1], ("__text__", ""))


class SectionPromptTests(unittest.TestCase):
    def _sys(self, **kw):
        sec = kw.pop("section", {"key": "analysis", "heading": "A", "kind": "analysis"})
        return rw._build_section_prompt(
            "q", sec, "[[ev:abc]] 報告：a.pdf", True, **kw
        )[0]

    def test_web_rules_only_when_enabled(self):
        off = self._sys(web_enabled=False)
        on = self._sys(web_enabled=True)
        self.assertNotIn("（網路）", off)
        self.assertNotIn(rw.SECTION_WEB_HEADING, off)
        self.assertIn("網路搜尋", on)
        self.assertIn("（網路）", on)
        self.assertIn(rw.SECTION_WEB_HEADING, on)

    def test_kpi_rule_in_exec_summary_and_analysis_only(self):
        self.assertIn("```kpi", self._sys())
        self.assertIn("```kpi", self._sys(
            section={"key": "exec_summary", "heading": "執行摘要", "kind": "framing"}))
        self.assertNotIn("```kpi", self._sys(
            section={"key": "risk_outlook", "heading": "風險", "kind": "framing"}))

    def test_chart_rule_only_in_analysis(self):
        self.assertIn("```chart", self._sys())
        self.assertNotIn("```chart", self._sys(
            section={"key": "exec_summary", "heading": "執行摘要", "kind": "framing"}))

    def test_kpi_and_chart_source_uses_ev_placeholder_not_bare_number(self):
        """圍欄內的 source 必須寫 [[ev:]]（render_citations 會一併換成 [n]）；
        叫模型自行寫 [1] 會產生與帳本無關的假編號。"""
        s = self._sys()
        self.assertIn('"source":"[[ev:xxx]]"', s)
        self.assertIn("不得杜撰", s)

    def test_kpi_source_offers_web_only_when_web_enabled(self):
        """網搜關時 KPI 的 source 不得提示「（網路）」——等於邀請模型標一個它根本
        查不到的來源。"""
        self.assertIn('"source":"[[ev:xxx]] 或 （網路）"', self._sys(web_enabled=True))
        self.assertIn('"source":"[[ev:xxx]]"', self._sys(web_enabled=False))
        self.assertNotIn("網路", self._sys(web_enabled=False))

    def test_forbids_self_numbering(self):
        self.assertIn("不得自行編造", self._sys())
        self.assertIn("自行編號", self._sys())

    def test_coverage_note_injected_into_prompt(self):
        _, p = rw._build_section_prompt(
            "q", {"key": "analysis", "heading": "A", "kind": "analysis"},
            "ctx", True, web_enabled=True, coverage_note="（薄涵蓋提示）",
        )
        self.assertIn("（薄涵蓋提示）", p)

    def test_no_evidence_with_web_directs_web_primary(self):
        _, p = rw._build_section_prompt(
            "q", {"key": "analysis", "heading": "A", "kind": "analysis"},
            "", False, web_enabled=True, coverage_note="（薄涵蓋提示）",
        )
        self.assertIn("本節無檢索到的參考片段", p)
        self.assertIn("以網路搜尋為主", p)

    def test_no_evidence_without_web_stays_conservative(self):
        _, p = rw._build_section_prompt(
            "q", {"key": "analysis", "heading": "A", "kind": "analysis"},
            "", False, web_enabled=False,
        )
        self.assertIn("本節無檢索到的參考片段", p)
        self.assertNotIn("以網路搜尋為主", p)


class WebRefsAssemblyTests(unittest.TestCase):
    def test_split_web_refs_extracts_and_removes_block(self):
        draft = (
            "內文（網路）。\n\n"
            f"### {rw.SECTION_WEB_HEADING}\n"
            "- [新聞A](https://e.com/a)\n"
            "- [壞](ftp://e.com/b)\n"
        )
        body, refs = rw.split_web_refs(draft)
        self.assertEqual(body, "內文（網路）。")
        self.assertEqual(refs, [{"title": "新聞A", "url": "https://e.com/a"}])

    def test_split_web_refs_no_block(self):
        self.assertEqual(rw.split_web_refs("純內文"), ("純內文", []))

    def test_build_external_refs_dedups_by_url(self):
        out = rw.build_external_refs([
            {"title": "A", "url": "https://e.com/a"},
            {"title": "A 重複", "url": "https://e.com/a"},
            {"title": "B", "url": "https://e.com/b"},
        ])
        self.assertEqual(
            out,
            f"## {rw.EXTERNAL_HEADING}\n- [A](https://e.com/a)\n- [B](https://e.com/b)",
        )

    def test_build_external_refs_empty_returns_blank(self):
        self.assertEqual(rw.build_external_refs([]), "")

    def test_assemble_final_merges_section_web_refs_into_one_section(self):
        ledger = rw.EvidenceLedger()
        e1 = ledger.add_corpus(report_id="r1", file_name="a.pdf", market="TW")
        secs = [
            {"key": "exec_summary", "heading": "執行摘要", "kind": "framing",
             "draft": f"摘要[[ev:{e1.evidence_id}]]（網路）\n\n"
                      f"### {rw.SECTION_WEB_HEADING}\n- [N1](https://e.com/1)\n"},
            {"key": "analysis", "heading": "A", "kind": "analysis",
             "draft": f"分析（網路）\n\n### {rw.SECTION_WEB_HEADING}\n"
                      "- [N1](https://e.com/1)\n- [N2](https://e.com/2)\n"},
            {"key": "risk_outlook", "heading": "風險與展望", "kind": "framing",
             "draft": "風險"},
        ]
        final, rendered = rw.assemble_final("研報", secs, ledger)
        self.assertEqual(rendered.n_unknown, 0)
        self.assertEqual(final.count(f"## {rw.EXTERNAL_HEADING}"), 1)  # 只有一節
        self.assertNotIn(rw.SECTION_WEB_HEADING, final)  # 中繼標題不外漏
        self.assertEqual(final.count("https://e.com/1"), 1)  # 跨節去重
        self.assertIn("- [N2](https://e.com/2)", final)
        self.assertLess(final.index("## 引用來源"), final.index(f"## {rw.EXTERNAL_HEADING}"))

    def test_assembled_web_section_parses_with_report_parse_external_refs(self):
        """組裝出來的外部參考節必須能被 report.parse_external_refs（M4b 唯一受控
        外部入口）解析——兩邊格式必須逐字對齊。"""
        from app.services.report import parse_external_refs

        ledger = rw.EvidenceLedger()
        secs = [{"key": "analysis", "heading": "A", "kind": "analysis",
                 "draft": f"分析（網路）\n\n### {rw.SECTION_WEB_HEADING}\n"
                          "- [新聞](https://news.example.com/a)\n"}]
        final, _ = rw.assemble_final("研報", secs, ledger)
        self.assertEqual(
            parse_external_refs(final),
            [{"title": "新聞", "url": "https://news.example.com/a"}],
        )

    def test_references_section_never_empty(self):
        """無語料引用時仍寫一行說明（空章節會讓 PDF 看起來壞掉、也會被
        section_coverage 的『章節須有內文』把關判為缺章）。"""
        out = rw.build_references([])
        self.assertIn("## 引用來源", out)
        self.assertIn(rw.EXTERNAL_HEADING, out)
        self.assertGreater(len(out.splitlines()), 1)


if __name__ == "__main__":
    unittest.main()


# ── M7 成本控制：逐節網搜門檻與逾時預算 ────────────────────────────────────
def _outline_5_analysis():
    """五章骨架 + 3 個動態子節（deadline 測試需要多個 analysis 節可砍）。"""
    return {
        "title": "研報T",
        "sections": [
            {"position": 0, "key": "exec_summary", "heading": "執行摘要",
             "topic": "q", "kind": "framing"},
            {"position": 1, "key": "key_findings", "heading": "關鍵發現",
             "topic": "q", "kind": "framing"},
            {"position": 2, "key": "analysis", "heading": "面向A",
             "topic": "ta", "kind": "analysis"},
            {"position": 3, "key": "analysis", "heading": "面向B",
             "topic": "tb", "kind": "analysis"},
            {"position": 4, "key": "analysis", "heading": "面向C",
             "topic": "tc", "kind": "analysis"},
            {"position": 5, "key": "risk_outlook", "heading": "風險與展望",
             "topic": "r", "kind": "framing"},
        ],
    }


class SectionWebGatingTests(unittest.IsolatedAsyncioTestCase):
    """逐節網搜必須依「該節自己的命中數」決定。

    無條件開的代價是成本放大 N 倍：單次路徑一份研報搜 1 次，逐節 8 節就搜 8 次
    （M1b 實測 r005/r009 各 8/7 次網搜，雙雙撞破 1500s）。門檻必須明顯低於逐節配額
    （REPORT_SECTION_MAX_REPORTS=8）——拿 run-level 的 REPORT_THIN_COVERAGE=8 來套
    會幾乎每節都觸發，等於沒關。
    """

    async def _allow_web_calls(self, n_sources, *, thin_coverage, web_enabled=True):
        from types import SimpleNamespace

        seen = []
        src = SimpleNamespace(n=1, report_id="r1", file_name="a.pdf", market="TW",
                              report_date="2026-01-01")

        async def fake_outline(*a, **k):
            return _outline_3()

        async def fake_retrieve(topic, **k):
            return ([src] * n_sources, "[1] 報告：a.pdf\n片段")

        def fake_stream(*a, **k):
            seen.append(k.get("allow_web"))

            async def gen():
                yield "內文"

            return gen()

        with patch.object(rw, "plan_outline", fake_outline), patch.object(
            rw, "retrieve_for_section", fake_retrieve
        ), patch.object(rw, "stream_completion", fake_stream):
            [e async for e in rw.draft_report(
                "q", "ctx", web_enabled=web_enabled, thin_coverage=thin_coverage)]
        return seen

    async def test_rich_section_does_not_search_web(self):
        """該節命中數達門檻 → 不開網搜（一般題的常態，省下 N 次網搜）。"""
        seen = await self._allow_web_calls(8, thin_coverage=3)
        self.assertTrue(seen)
        self.assertTrue(all(v is False for v in seen), f"不該有節開網搜：{seen}")

    async def test_thin_section_searches_web(self):
        """該節真的缺料 → 仍要上網補，否則會產出無資料支撐的章節。"""
        seen = await self._allow_web_calls(1, thin_coverage=3)
        self.assertTrue(all(v is True for v in seen), f"缺料節應開網搜：{seen}")

    async def test_web_disabled_globally_never_searches(self):
        seen = await self._allow_web_calls(0, thin_coverage=3, web_enabled=False)
        self.assertTrue(all(v is False for v in seen), f"全域關網搜仍被開：{seen}")

    async def test_regeneration_reuses_section_gate_not_run_level(self):
        """審查 F2 回歸：n_unknown 重生必須沿用該節的閘門結果，不可退回 run-level。

        重生迴圈原本寫死 `allow_web=web_enabled`，於是閘門已明確關掉網搜的節在重生時
        被重新打開。雙重危害：(1) 成本回歸——模型抄壞 [[ev:]] 時往往多節同壞，一次
        重生就把 N 次網搜加回來，正是 28b8a68 要消滅的形態；(2) 證據完整性——重生沿用
        的 prompt 是 sec_web=False 時建的、不含網路標註規則，模型拿到工具卻沒拿到規則，
        網路內容會未標註地混進正文，永遠進不了 evidence_manifest。
        """
        from types import SimpleNamespace

        seen = []
        src = SimpleNamespace(n=1, report_id="r1", file_name="a.pdf", market="TW",
                              report_date="2026-01-01")

        async def fake_outline(*a, **k):
            return _outline_3()

        async def fake_retrieve(topic, **k):
            # 命中數(8) 遠高於門檻(3) → 每節閘門都應關閉網搜
            return ([src] * 8, "[1] 報告：a.pdf\n片段")

        def fake_stream(*a, **k):
            seen.append(k.get("allow_web"))

            async def gen():
                # 抄出一個 ledger 中不存在的 id → n_unknown>0 → 觸發重生迴圈
                yield "內文[[ev:deadbeefdeadbeef]]"

            return gen()

        with patch.object(rw, "plan_outline", fake_outline), patch.object(
            rw, "retrieve_for_section", fake_retrieve
        ), patch.object(rw, "stream_completion", fake_stream):
            [e async for e in rw.draft_report(
                "q", "ctx", web_enabled=True, thin_coverage=3)]

        # 必須真的走到重生（否則這條測試等於沒驗到東西）
        self.assertGreater(
            len(seen), 3, f"未觸發重生迴圈，本測試失去意義：{seen}"
        )
        self.assertTrue(
            all(v is False for v in seen),
            f"重生繞過逐節網搜閘門（run-level web_enabled 洩漏進重生）：{seen}",
        )

    async def test_run_level_threshold_would_defeat_the_gate(self):
        """回歸：拿 run-level 門檻（8）套逐節配額（8）會幾乎每節誤觸發。

        這條記錄的是「為什麼 REPORT_SECTION_THIN_COVERAGE 要跟 REPORT_THIN_COVERAGE
        分開」——同一組 sources 在門檻 8 下全開、門檻 3 下全關。
        """
        self.assertTrue(all(v is True for v in await self._allow_web_calls(7, thin_coverage=8)))
        self.assertTrue(all(v is False for v in await self._allow_web_calls(7, thin_coverage=3)))


class SectionDeadlineTests(unittest.IsolatedAsyncioTestCase):
    """逐節路徑的總預算：單次路徑有 REPORT_TIMEOUT 上限，逐節先前完全無界。"""

    async def _headings(self, *, deadline):
        from types import SimpleNamespace

        src = SimpleNamespace(n=1, report_id="r1", file_name="a.pdf", market="TW",
                              report_date="2026-01-01")

        async def fake_outline(*a, **k):
            return _outline_5_analysis()

        async def fake_retrieve(topic, **k):
            return ([src] * 8, "[1] 報告：a.pdf\n片段")

        with patch.object(rw, "plan_outline", fake_outline), patch.object(
            rw, "retrieve_for_section", fake_retrieve
        ), patch.object(rw, "stream_completion", _draft_stream("內文")):
            events = [e async for e in rw.draft_report(
                "q", "ctx", thin_coverage=3, deadline=deadline)]
        md = events[-1][1]["markdown"]
        return md, [e for e in events if e[0] == "section_draft"]

    async def test_expired_deadline_stops_before_any_section_work(self):
        """總預算已耗盡時不得再啟動逐節工作，否則 timeout 會按節次累加。"""
        from types import SimpleNamespace

        seen = []
        src = SimpleNamespace(n=1, report_id="r1", file_name="a.pdf", market="TW",
                              report_date="2026-01-01")

        async def fake_outline(*a, **k):
            return _outline_5_analysis()

        async def fake_retrieve(topic, **k):
            seen.append(topic)
            return ([src], "[1] 報告：a.pdf\n片段")

        with patch.object(rw, "plan_outline", fake_outline), patch.object(
            rw, "retrieve_for_section", fake_retrieve
        ), patch.object(rw, "stream_completion", _draft_stream("內文")):
            events = [e async for e in rw.draft_report(
                "q", "ctx", thin_coverage=3, deadline=time.monotonic() - 1)]

        self.assertEqual(seen, [])
        self.assertEqual(events[-1][0], "__fallback__")

    async def test_no_deadline_keeps_all_sections(self):
        md, _ = await self._headings(deadline=None)
        for h in ("執行摘要", "關鍵發現", "面向A", "面向B", "面向C", "風險與展望"):
            self.assertIn(h, md)

    async def test_future_deadline_keeps_all_sections(self):
        md, _ = await self._headings(deadline=time.monotonic() + 600)
        for h in ("面向A", "面向B", "面向C"):
            self.assertIn(h, md)

    async def test_expired_deadline_does_not_start_analysis_retrieval(self):
        """總預算耗盡後不可再啟動動態節檢索，避免 N 節各自耗盡 timeout。"""
        from types import SimpleNamespace

        seen_topics = []
        src = SimpleNamespace(n=1, report_id="r1", file_name="a.pdf", market="TW",
                              report_date="2026-01-01")

        async def fake_outline(*a, **k):
            return _outline_5_analysis()

        async def fake_retrieve(topic, **k):
            seen_topics.append(topic)
            return ([src], "[1] 報告：a.pdf\n片段")

        with patch.object(rw, "plan_outline", fake_outline), patch.object(
            rw, "retrieve_for_section", fake_retrieve
        ), patch.object(rw, "stream_completion", _draft_stream("內文")):
            events = [e async for e in rw.draft_report(
                "q", "ctx", thin_coverage=3, deadline=time.monotonic() - 1)]

        self.assertNotIn("ta", seen_topics)
        self.assertNotIn("tb", seen_topics)
        self.assertNotIn("tc", seen_topics)
        self.assertEqual(events[-1][0], "__fallback__")
