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


if __name__ == "__main__":
    unittest.main()
