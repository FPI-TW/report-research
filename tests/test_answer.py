# tests/test_answer.py
import asyncio
import json
import sys
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services import llm  # noqa: E402
from app.services import scope_router as sr  # noqa: E402
from app.services.answer import (  # noqa: E402
    Source,
    _recency_factor,
    build_context,
    build_history_block,
    build_user_prompt,
    cited_report_ids,
    history_item,
    split_external_sources,
)
from app.services.rows import ChunkRow  # noqa: E402


def make_row(report_id, file_name, market, content, report_date=None, distance=0.1):
    """造一列符合 hybrid_search 回傳結構的 ChunkRow。"""
    return ChunkRow(
        chunk_id=None,
        report_id=report_id,
        file_hash=f"h-{report_id}",
        file_name=file_name,
        title=None,
        market=market,
        source=None,
        summary=None,
        report_date=report_date,
        report_type=None,
        instrument_types=None,
        relates_stock=None,
        relates_futures=None,
        stock_targets=None,
        futures_targets=None,
        chunk_index=0,
        content=content,
        distance=distance,
    )


class StreamParseTests(unittest.TestCase):
    def test_text_delta_extracted(self):
        line = (
            '{"type":"stream_event","event":{"type":"content_block_delta",'
            '"delta":{"type":"text_delta","text":"哈囉"}}}'
        )
        self.assertEqual(llm.extract_text_delta(line), "哈囉")

    def test_thinking_delta_ignored(self):
        line = (
            '{"type":"stream_event","event":{"type":"content_block_delta",'
            '"delta":{"type":"thinking_delta","thinking":"x"}}}'
        )
        self.assertIsNone(llm.extract_text_delta(line))

    def test_non_stream_event_ignored(self):
        self.assertIsNone(llm.extract_text_delta('{"type":"system","subtype":"init"}'))

    def test_malformed_line_ignored(self):
        self.assertIsNone(llm.extract_text_delta("not json"))
        self.assertIsNone(llm.extract_text_delta(""))

    def test_non_dict_stream_json_ignored(self):
        self.assertIsNone(llm.extract_text_delta('"just text"'))
        self.assertIsNone(llm.extract_text_delta('["array"]'))
        self.assertIsNone(
            llm.extract_text_delta('{"type":"stream_event","event":"oops"}')
        )
        self.assertIsNone(
            llm.extract_text_delta(
                '{"type":"stream_event","event":{"type":"content_block_delta","delta":"oops"}}'
            )
        )

    def test_result_line(self):
        self.assertTrue(llm.is_result_line('{"type":"result","subtype":"success"}'))
        self.assertFalse(llm.is_result_line('{"type":"stream_event"}'))

    def test_non_dict_result_line_ignored(self):
        self.assertFalse(llm.is_result_line("123"))


class ScaleUpDefaultsTests(unittest.TestCase):
    NOW = datetime(2026, 6, 24, tzinfo=timezone.utc)
    D = date(2026, 6, 20)  # 近期，避免新近度截斷干擾

    def test_default_max_reports_is_15(self):
        scored = [
            (0, 0.70, make_row(f"r{i}", f"{i}.pdf", "TW", f"內容{i}", self.D))
            for i in range(20)
        ]
        sources, _ = build_context(scored, now=self.NOW)
        self.assertEqual(len(sources), 15)

    def test_default_max_passages_is_4(self):
        scored = [
            (0, 0.70, make_row("r1", "甲.pdf", "TW", f"第{i}段內容", self.D))
            for i in range(6)  # 同一報告 6 段
        ]
        sources, context = build_context(scored, now=self.NOW)
        self.assertEqual(len(sources), 1)
        self.assertIn("第0段內容", context)
        self.assertIn("第3段內容", context)
        self.assertNotIn("第4段內容", context)  # 受預設 max_passages=4 限制

    def test_system_prompt_encourages_synthesis(self):
        from app.services.answer import SYSTEM_PROMPT
        self.assertIn("綜合多篇研報、彼此佐證", SYSTEM_PROMPT)


class RelevanceFloorTests(unittest.TestCase):
    NOW = datetime(2026, 6, 24, tzinfo=timezone.utc)
    D = date(2026, 6, 20)  # 近期，factor 高，不觸發既有極舊截斷

    def test_tier0_below_floor_excluded_beyond_min(self):
        scored = [
            (0, 0.70, make_row("r1", "1.pdf", "TW", "強1", self.D)),
            (0, 0.69, make_row("r2", "2.pdf", "TW", "強2", self.D)),
            (0, 0.68, make_row("r3", "3.pdf", "TW", "強3", self.D)),
            (0, 0.50, make_row("r4", "4.pdf", "TW", "弱4", self.D)),  # < floor
            (0, 0.45, make_row("r5", "5.pdf", "TW", "弱5", self.D)),  # < floor
        ]
        sources, _ = build_context(
            scored, now=self.NOW, min_reports=3, relevance_floor=0.62
        )
        self.assertEqual([s.report_id for s in sources], ["r1", "r2", "r3"])

    def test_min_reports_guaranteed_even_below_floor(self):
        scored = [
            (0, 0.50, make_row("r1", "1.pdf", "TW", "弱1", self.D)),
            (0, 0.48, make_row("r2", "2.pdf", "TW", "弱2", self.D)),
            (0, 0.45, make_row("r3", "3.pdf", "TW", "弱3", self.D)),
        ]
        sources, _ = build_context(
            scored, now=self.NOW, min_reports=2, relevance_floor=0.62
        )
        self.assertEqual([s.report_id for s in sources], ["r1", "r2"])

    def test_tier1_below_floor_kept(self):
        scored = [
            (1, 0.40, make_row("r4", "4.pdf", "TW", "字面命中低分", self.D)),
            (0, 0.40, make_row("r5", "5.pdf", "TW", "純語意低分", self.D)),
        ]
        sources, _ = build_context(
            scored, now=self.NOW, min_reports=0, relevance_floor=0.62
        )
        self.assertEqual([s.report_id for s in sources], ["r4"])


class StaleCapTests(unittest.TestCase):
    NOW = datetime(2026, 6, 24, tzinfo=timezone.utc)
    RECENT = date(2026, 6, 20)        # 4 天：新
    OLD = date(2025, 11, 15)          # ~221 天：過舊(>180)但非極舊(factor>0.1，不被既有截斷丟)

    def test_old_reports_capped(self):
        scored = [
            (0, 0.70, make_row("f1", "f1.pdf", "TW", "新1", self.RECENT)),
            (0, 0.69, make_row("f2", "f2.pdf", "TW", "新2", self.RECENT)),
            (0, 0.68, make_row("o1", "o1.pdf", "TW", "舊1", self.OLD)),
            (0, 0.67, make_row("o2", "o2.pdf", "TW", "舊2", self.OLD)),
            (0, 0.66, make_row("o3", "o3.pdf", "TW", "舊3", self.OLD)),
            (0, 0.67, make_row("o4", "o4.pdf", "TW", "舊4", self.OLD)),
        ]
        sources, _ = build_context(
            scored, now=self.NOW, min_reports=0, relevance_floor=0.0,
            stale_age_days=180, max_stale=2,
        )
        ids = [s.report_id for s in sources]
        self.assertEqual(len([i for i in ids if i.startswith("o")]), 2)
        self.assertIn("f1", ids)
        self.assertIn("f2", ids)

    def test_min_reports_exempt_from_stale_cap(self):
        scored = [
            (0, 0.70, make_row("o1", "o1.pdf", "TW", "舊1", self.OLD)),
            (0, 0.69, make_row("o2", "o2.pdf", "TW", "舊2", self.OLD)),
            (0, 0.68, make_row("o3", "o3.pdf", "TW", "舊3", self.OLD)),
            (0, 0.67, make_row("o4", "o4.pdf", "TW", "舊4", self.OLD)),
        ]
        sources, _ = build_context(
            scored, now=self.NOW, min_reports=3, relevance_floor=0.0,
            stale_age_days=180, max_stale=1,
        )
        self.assertEqual([s.report_id for s in sources], ["o1", "o2", "o3"])


class BuildContextTests(unittest.TestCase):
    def test_numbers_reports_in_order(self):
        scored = [
            (
                2,
                0.9,
                make_row(
                    "r1",
                    "甲報告.pdf",
                    "TW",
                    "台積電先進封裝需求強勁。",
                    date(2026, 6, 1),
                ),
            ),
            (1, 0.8, make_row("r2", "乙報告.pdf", "US", "美國升息影響評估。")),
        ]
        sources, context = build_context(scored)
        self.assertEqual([s.report_id for s in sources], ["r1", "r2"])
        self.assertEqual([s.n for s in sources], [1, 2])
        self.assertIn("[1] 報告：甲報告.pdf", context)
        self.assertIn("[2] 報告：乙報告.pdf", context)
        self.assertIn("2026-06-01", context)  # 日期帶入脈絡

    def test_groups_passages_under_one_report(self):
        scored = [
            (2, 0.9, make_row("r1", "甲.pdf", "TW", "第一段內容關於封裝。")),
            (2, 0.8, make_row("r1", "甲.pdf", "TW", "第二段內容關於良率。")),
            (2, 0.7, make_row("r1", "甲.pdf", "TW", "第三段超過上限。")),
        ]
        sources, context = build_context(scored, max_passages=2)
        self.assertEqual(len(sources), 1)  # 同一篇 → 單一來源編號
        self.assertIn("第一段內容關於封裝。", context)
        self.assertIn("第二段內容關於良率。", context)
        self.assertNotIn("第三段超過上限。", context)  # 受 max_passages 限制

    def test_max_reports_limit(self):
        scored = [
            (0, 0.5, make_row(f"r{i}", f"{i}.pdf", "TW", f"內容{i}。"))
            for i in range(10)
        ]
        sources, _ = build_context(scored, max_reports=3)
        self.assertEqual(len(sources), 3)

    def test_empty_content_skipped(self):
        scored = [
            (0, 0.5, make_row("r1", "甲.pdf", "TW", "   ")),
            (0, 0.4, make_row("r2", "乙.pdf", "TW", "有效內容。")),
        ]
        sources, context = build_context(scored)
        self.assertEqual([s.report_id for s in sources], ["r2"])
        self.assertIn("[1] 報告：乙.pdf", context)

    def test_first_passage_respects_max_chars_budget(self):
        scored = [
            (0, 0.5, make_row("r1", "甲.pdf", "TW", "超" * 10)),
            (0, 0.4, make_row("r2", "乙.pdf", "TW", "短內容")),
        ]
        sources, context = build_context(scored, max_chars=6)
        self.assertEqual([s.report_id for s in sources], ["r2"])
        self.assertNotIn("甲.pdf", context)
        self.assertIn("乙.pdf", context)

    def test_no_results(self):
        sources, context = build_context([])
        self.assertEqual(sources, [])
        self.assertEqual(context, "")


class PromptAndCitationTests(unittest.TestCase):
    def test_user_prompt_contains_question_and_context(self):
        p = build_user_prompt("台積電如何？", "[1] 報告：甲.pdf\n內容。")
        self.assertIn("台積電如何？", p)
        self.assertIn("[1] 報告：甲.pdf", p)

    def test_cited_maps_in_range_only(self):
        sources = [
            Source(1, "r1", "甲.pdf", "TW", None),
            Source(2, "r2", "乙.pdf", "US", None),
        ]
        # [1] 與 [3]：1 在範圍內 → r1；3 超出 → 忽略
        self.assertEqual(cited_report_ids("結論如上[1]，另見[3]。", sources), ["r1"])

    def test_cited_dedups_and_preserves_source_order(self):
        sources = [
            Source(1, "r1", "甲.pdf", "TW", None),
            Source(2, "r2", "乙.pdf", "US", None),
        ]
        self.assertEqual(cited_report_ids("[2][1][2]", sources), ["r1", "r2"])

    def test_user_prompt_mentions_recency_preference(self):
        # user_prompt 應引導模型優先採用較新研報（呼應「以最新研報為主」）
        p = build_user_prompt("台積電?", "[1] 報告：甲.pdf\n內容。")
        self.assertIn("新近度", p)


class HistoryBlockTests(unittest.TestCase):
    def test_empty_turns_returns_empty_string(self):
        self.assertEqual(build_history_block([]), "")

    def test_formats_turns_oldest_first(self):
        block = build_history_block([("台積電?", "看好[1]"), ("那聯電?", "中立[1]")])
        self.assertIn("Q1: 台積電?", block)
        self.assertIn("A1: 看好[1]", block)
        self.assertIn("Q2: 那聯電?", block)

    def test_keeps_only_recent_max_turns(self):
        turns = [("q1", "a1"), ("q2", "a2"), ("q3", "a3"), ("q4", "a4")]
        block = build_history_block(turns, max_turns=3)
        self.assertNotIn("q1", block)  # 最舊一輪被丟
        self.assertIn("q4", block)

    def test_truncates_long_answer(self):
        block = build_history_block([("q", "x" * 1000)], max_answer_chars=600)
        self.assertIn("…", block)
        self.assertLess(len(block), 700)


class PromptWithHistoryTests(unittest.TestCase):
    def test_history_block_prepended_when_present(self):
        p = build_user_prompt("新問題", "[1] 報告甲\n內容", "Q1: 舊問\nA1: 舊答")
        self.assertIn("先前對話", p)
        self.assertIn("Q1: 舊問", p)
        self.assertIn("問題：新問題", p)

    def test_no_history_section_when_empty(self):
        p = build_user_prompt("新問題", "[1] 報告甲\n內容", "")
        self.assertNotIn("先前對話", p)
        self.assertIn("問題：新問題", p)


class RecencyTests(unittest.TestCase):
    NOW = datetime(2026, 6, 17, tzinfo=timezone.utc)

    def test_newer_report_ranked_first_when_relevance_close(self):
        # 同 tier、相關度接近：較新者（2026-06-10）應排在較舊者（2026-01-01）之前
        scored = [
            (
                0,
                0.80,
                make_row("old", "舊.pdf", "TW", "AI 伺服器需求強。", date(2026, 1, 1)),
            ),
            (
                0,
                0.78,
                make_row("new", "新.pdf", "TW", "AI 伺服器需求強。", date(2026, 6, 10)),
            ),
        ]
        sources, _ = build_context(scored, now=self.NOW)
        self.assertEqual([s.report_id for s in sources], ["new", "old"])
        self.assertEqual(sources[0].report_id, "new")  # 較新拿 [1]

    def test_recency_does_not_override_tier(self):
        # 字面強命中的舊篇（tier2）仍勝過弱相關的新篇（tier0）
        scored = [
            (
                2,
                0.70,
                make_row(
                    "strong_old", "強舊.pdf", "TW", "先進封裝。", date(2025, 1, 1)
                ),
            ),
            (
                0,
                0.95,
                make_row("weak_new", "弱新.pdf", "TW", "先進封裝。", date(2026, 6, 17)),
            ),
        ]
        sources, _ = build_context(scored, now=self.NOW)
        self.assertEqual(sources[0].report_id, "strong_old")

    def test_missing_date_treated_as_oldest(self):
        # 無日期者 recency_factor=0；同 tier 下有近日期者勝出
        scored = [
            (0, 0.80, make_row("nodate", "無日期.pdf", "TW", "內容。", None)),
            (
                0,
                0.79,
                make_row("dated", "有日期.pdf", "TW", "內容。", date(2026, 6, 15)),
            ),
        ]
        sources, _ = build_context(scored, now=self.NOW)
        self.assertEqual(sources[0].report_id, "dated")

    def test_recency_beats_relevance_within_band(self):
        # 同 tier、相關度落在同一 band（0.86 與 0.78）：較新者勝——
        # 新近度是「層內」主排序維度，不再只是會被 fused 差距蓋過的微小加分。
        scored = [
            (0, 0.86, make_row("old", "舊.pdf", "TW", "記憶體報價。", date(2025, 6, 17))),
            (0, 0.78, make_row("new", "新.pdf", "TW", "記憶體報價。", date(2026, 6, 10))),
        ]
        sources, _ = build_context(scored, now=self.NOW)
        self.assertEqual(sources[0].report_id, "new")

    def test_relevance_beats_recency_across_band(self):
        # 相關度差距跨越 band（0.95 vs 0.60）：高相關的舊篇仍勝——
        # 守住「不為了新而漏掉強相關研報」，新近度不會過度反轉相關度。
        scored = [
            (
                0,
                0.95,
                make_row("strong_old", "強舊.pdf", "TW", "記憶體報價。", date(2025, 6, 17)),
            ),
            (
                0,
                0.60,
                make_row("weak_new", "弱新.pdf", "TW", "記憶體報價。", date(2026, 6, 16)),
            ),
        ]
        sources, _ = build_context(scored, now=self.NOW)
        self.assertEqual(sources[0].report_id, "strong_old")

    def test_non_positive_half_life_does_not_crash(self):
        self.assertEqual(_recency_factor(date(2026, 6, 17), self.NOW.date(), 0), 1.0)
        self.assertEqual(_recency_factor(date(2026, 6, 17), self.NOW.date(), -1), 1.0)
        self.assertEqual(_recency_factor(None, self.NOW.date(), 0), 0.0)


class AskRecallConfigTests(unittest.IsolatedAsyncioTestCase):
    """問答路徑顯式傳 dense_scan 給 hybrid_search（擴召回、不改共用函式預設）。"""

    async def test_dense_scan_forwarded_from_ask_path(self):
        import app.services.retrieval_pipeline as rp
        from app.services import answer as ans

        captured: dict = {}

        async def recording_search(session, q, qvec, **k):
            captured.update(k)
            return [(0, 0.80, make_row("r1", "x.pdf", "TW", "內容。", date(2026, 6, 1)))]

        async def fake_stream(*a, **k):
            yield "答案[1]"

        async def fake_route(question, **k):
            return sr._decision(sr.CORPUS_QA)

        orig = (
            rp.hybrid_search,
            rp.embed_query_cached,
            ans.stream_completion,
            rp.SessionFactory,
            ans.SessionFactory,
            ans.classify_non_overview,
        )
        rp.hybrid_search = recording_search
        rp.embed_query_cached = lambda q: [0.0]
        ans.stream_completion = fake_stream
        rp.SessionFactory = lambda: _FakeSession()
        ans.SessionFactory = lambda: _FakeSession()
        ans.classify_non_overview = fake_route
        try:
            _ = [e async for e in ans.answer_question("台積電展望")]
        finally:
            (
                rp.hybrid_search,
                rp.embed_query_cached,
                ans.stream_completion,
                rp.SessionFactory,
                ans.SessionFactory,
                ans.classify_non_overview,
            ) = orig

        self.assertEqual(captured.get("dense_scan"), ans.ASK_DENSE_SCAN)

    async def test_rerank_top_m_forwarded_from_ask_path(self):
        import app.services.retrieval_pipeline as rp
        from app.services import answer as ans

        captured = {}

        async def recording_search(session, q, qvec, **k):
            return [(0, 0.80, make_row("r1", "x.pdf", "TW", "內容。", date(2026, 6, 1)))]

        def recording_rerank(question, scored, *, top_m, timer=None, deadline=None):
            captured["top_m"] = top_m
            captured["deadline"] = deadline
            return scored

        async def fake_stream(*a, **k):
            yield "答案[1]"

        async def fake_route(question, **k):
            return sr._decision(sr.CORPUS_QA)

        orig = (
            rp.hybrid_search, rp.embed_query_cached, rp.rerank_scored,
            ans.stream_completion, rp.SessionFactory, ans.SessionFactory,
            ans.classify_non_overview,
        )
        rp.hybrid_search = recording_search
        rp.embed_query_cached = lambda q: [0.0]
        rp.rerank_scored = recording_rerank
        ans.stream_completion = fake_stream
        rp.SessionFactory = lambda: _FakeSession()
        ans.SessionFactory = lambda: _FakeSession()
        ans.classify_non_overview = fake_route
        try:
            _ = [e async for e in ans.answer_question("台積電展望")]
        finally:
            (
                rp.hybrid_search, rp.embed_query_cached, rp.rerank_scored,
                ans.stream_completion, rp.SessionFactory, ans.SessionFactory,
                ans.classify_non_overview,
            ) = orig

        self.assertEqual(captured.get("top_m"), ans.ASK_RERANK_TOP_M)
        self.assertEqual(ans.ASK_RERANK_TOP_M, 50)  # 預設啟用
        self.assertIsNotNone(captured.get("deadline"))  # 問答路徑逾時 → deadline 傳遞
        self.assertEqual(ans.ASK_RERANK_TIMEOUT, 60.0)  # 預設 60s（實測 50 對 ~34s + 餘裕）


class AskLexTelemetryTests(unittest.IsolatedAsyncioTestCase):
    """字面路 cap 截斷必須進 qa_timing。

    截斷是靜默的：`store._lexical_sql` 的 `LIMIT :cap` 沒有 ORDER BY，取到哪 cap 列由
    heap 物理順序決定，而回應與日誌裡原本沒有任何線索。**先量出實際發生率**才有依據
    決定要不要付排序的代價（加 ORDER BY 會逼掃完全部命中列，見該函式 docstring）。
    """

    async def _ask_with_lex_stats(self, *, lex_hits, cap, truncated):
        import app.services.retrieval_pipeline as rp
        from app.services import answer as ans

        captured: dict = {}

        async def recording_search(session, q, qvec, *, stats=None, **k):
            captured["stats_obj"] = stats
            if stats is not None:
                stats.update(
                    {"lex_hits": lex_hits, "lex_cap": cap, "lex_truncated": truncated}
                )
            return [(0, 0.80, make_row("r1", "x.pdf", "TW", "內容。", date(2026, 6, 1)))]

        async def fake_stream(*a, **k):
            yield "答案[1]"

        async def fake_route(question, **k):
            return sr._decision(sr.CORPUS_QA)

        orig = (
            rp.hybrid_search, rp.embed_query_cached, ans.stream_completion,
            rp.SessionFactory, ans.SessionFactory, ans.classify_non_overview,
        )
        rp.hybrid_search = recording_search
        rp.embed_query_cached = lambda q: [0.0]
        ans.stream_completion = fake_stream
        rp.SessionFactory = lambda: _FakeSession()
        ans.SessionFactory = lambda: _FakeSession()
        ans.classify_non_overview = fake_route
        try:
            with self.assertLogs("app.services.answer", level="INFO") as logs:
                _ = [e async for e in ans.answer_question("台積電展望")]
        finally:
            (
                rp.hybrid_search, rp.embed_query_cached, ans.stream_completion,
                rp.SessionFactory, ans.SessionFactory, ans.classify_non_overview,
            ) = orig
        timing = [m for m in logs.output if "qa_timing" in m]
        self.assertEqual(len(timing), 1, logs.output)
        return captured, timing[0]

    async def test_stats_dict_reaches_retrieval_and_lands_in_qa_timing(self):
        captured, line = await self._ask_with_lex_stats(
            lex_hits=2000, cap=2000, truncated=True
        )
        # 傳 None 等於整條遙測落空（hybrid_search 只在 stats is not None 時填）
        self.assertIsInstance(captured["stats_obj"], dict)
        self.assertIn("lex_hits=2000", line)
        self.assertIn("lex_cap=2000", line)
        self.assertIn("lex_truncated=True", line)

    async def test_untruncated_run_logs_false_not_missing(self):
        _captured, line = await self._ask_with_lex_stats(
            lex_hits=37, cap=2000, truncated=False
        )
        self.assertIn("lex_truncated=False", line)


class StageTimerTests(unittest.TestCase):
    def test_records_intervals_and_total(self):
        from app.services.answer import _StageTimer

        ticks = iter([100.0, 100.5, 101.2, 102.0])  # init, embed, retrieve, total
        t = _StageTimer(clock=lambda: next(ticks))
        t.mark("embed")
        t.mark("retrieve")
        self.assertEqual(t.stages["embed"], 500)
        self.assertEqual(t.stages["retrieve"], 700)
        self.assertEqual(t.total_ms(), 2000)

    def test_stage_str_lists_stages(self):
        from app.services.answer import _StageTimer

        ticks = iter([0.0, 0.1, 0.2])
        t = _StageTimer(clock=lambda: next(ticks))
        t.mark("embed")
        s = t.stage_str()
        self.assertIn("embed=100", s)


class SourceLatestTests(unittest.TestCase):
    NOW = datetime(2026, 6, 17, tzinfo=timezone.utc)

    def test_newest_source_flagged_latest(self):
        # 日期最新的來源被標 is_latest（即使它因 tier 而非排在 [1]）
        scored = [
            (2, 0.90, make_row("a", "a.pdf", "TW", "封裝。", date(2025, 1, 1))),
            (0, 0.80, make_row("b", "b.pdf", "TW", "封裝。", date(2026, 6, 10))),
        ]
        sources, _ = build_context(scored, now=self.NOW)
        by_id = {s.report_id: s for s in sources}
        self.assertTrue(by_id["b"].is_latest)
        self.assertFalse(by_id["a"].is_latest)

    def test_no_dates_no_latest(self):
        scored = [
            (0, 0.5, make_row("a", "a.pdf", "TW", "x。")),
            (0, 0.4, make_row("b", "b.pdf", "TW", "y。")),
        ]
        sources, _ = build_context(scored, now=self.NOW)
        self.assertFalse(any(s.is_latest for s in sources))


class StaleCutoffTests(unittest.TestCase):
    NOW = datetime(2026, 6, 17, tzinfo=timezone.utc)

    def test_stale_excluded_when_enough_fresh(self):
        # 有 ≥2 篇夠新（90 天內）→ 排除過舊報告（強烈偏好最新）
        scored = [
            (0, 0.80, make_row("f1", "新1.pdf", "TW", "記憶體報價。", date(2026, 6, 10))),
            (0, 0.80, make_row("f2", "新2.pdf", "TW", "記憶體報價。", date(2026, 6, 1))),
            (0, 0.80, make_row("s1", "舊.pdf", "TW", "記憶體報價。", date(2024, 1, 1))),
        ]
        sources, _ = build_context(scored, now=self.NOW)
        rids = [s.report_id for s in sources]
        self.assertIn("f1", rids)
        self.assertIn("f2", rids)
        self.assertNotIn("s1", rids)  # 過舊者被截斷

    def test_stale_kept_when_few_fresh(self):
        # 僅 1 篇夠新 → 不截斷（fail-open）：歷史性問題仍保留舊研報，不漏
        scored = [
            (0, 0.80, make_row("f1", "新.pdf", "TW", "記憶體報價。", date(2026, 6, 10))),
            (0, 0.80, make_row("s1", "舊1.pdf", "TW", "記憶體報價。", date(2024, 1, 1))),
            (0, 0.80, make_row("s2", "舊2.pdf", "TW", "記憶體報價。", date(2023, 6, 1))),
        ]
        sources, _ = build_context(scored, now=self.NOW)
        rids = [s.report_id for s in sources]
        self.assertEqual(len(rids), 3)
        self.assertIn("s1", rids)
        self.assertIn("s2", rids)


class _FakeSession:
    """假 async session：滿足 answer_question 的檢索/寫 log 連線，不碰真實 DB。"""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, *a, **k):
        return None

    async def commit(self):
        return None


class AnswerGateTests(unittest.IsolatedAsyncioTestCase):
    """離題判定改由 classify_non_overview（看意圖路由）決定，與 scored 分數無關。"""

    def _patch(self, ans, rp, *, in_domain, called):
        async def fake_search(*a, **k):
            return [
                (
                    0,
                    0.60,
                    make_row(
                        "r1",
                        "x.pdf",
                        "TW",
                        "可口可樂財報。",
                        date(2026, 6, 1),
                        distance=0.40,
                    ),
                )
            ]

        def fake_embed(q):
            return [0.0]

        async def fake_stream(*a, **k):
            called["llm"] = True
            yield "答案[1]"

        def _route_for(in_domain_):
            return sr._decision(sr.CORPUS_QA if in_domain_ else sr.OFF_TOPIC)

        async def fake_route(question, **k):
            called["intent"] = True
            return _route_for(in_domain)

        async def fake_condense(history_text, question, **k):
            called["condense"] = True
            return (question, _route_for(in_domain))

        async def fake_load(conversation_id, **k):
            return []

        orig = (
            rp.hybrid_search,
            rp.embed_query_cached,
            ans.stream_completion,
            rp.SessionFactory,
            ans.SessionFactory,
            ans.classify_non_overview,
            ans.condense_and_route,
            ans.load_recent_turns,
        )
        rp.hybrid_search = fake_search
        rp.embed_query_cached = fake_embed
        ans.stream_completion = fake_stream
        rp.SessionFactory = lambda: _FakeSession()
        ans.SessionFactory = lambda: _FakeSession()
        ans.classify_non_overview = fake_route
        ans.condense_and_route = fake_condense
        ans.load_recent_turns = fake_load
        return orig

    @staticmethod
    def _restore(ans, rp, orig):
        (
            rp.hybrid_search,
            rp.embed_query_cached,
            ans.stream_completion,
            rp.SessionFactory,
            ans.SessionFactory,
            ans.classify_non_overview,
            ans.condense_and_route,
            ans.load_recent_turns,
        ) = orig

    async def test_off_topic_intent_skips_llm(self):
        # 意圖判定為離題（即使檢索分數不低）→ 拒答、空來源、不跑主 LLM
        import app.services.retrieval_pipeline as rp
        from app.services import answer as ans

        called = {"llm": False, "intent": False}
        orig = self._patch(ans, rp, in_domain=False, called=called)
        try:
            events = [e async for e in ans.answer_question("我想喝飲料推薦給我")]
        finally:
            self._restore(ans, rp, orig)

        kinds = [k for k, _ in events]
        self.assertEqual(
            kinds, ["status", "sources", "notice", "done"]
        )  # 開頭多 understanding
        self.assertEqual(events[0], ("status", {"stage": "understanding"}))
        self.assertEqual(events[1][1], [])  # 離題不顯示任何來源
        self.assertEqual(events[2][1], ans.OFF_TOPIC_MESSAGE)
        self.assertEqual(events[3][0], "done")
        self.assertEqual(events[3][1]["cited"], [])
        self.assertIn("conversation_id", events[3][1])
        self.assertTrue(called["intent"])  # 有跑意圖判定
        self.assertFalse(called["llm"])  # 未跑主 LLM

    async def test_numeric_answer_triggers_faithfulness_spot_check(self):
        # M8c 接線：答案含金融數字 → done 後觸發忠實度抽查（帶 qa_id 與答案本文）。
        import app.services.retrieval_pipeline as rp
        from app.services import answer as ans

        called = {"llm": False, "intent": False}
        orig = self._patch(ans, rp, in_domain=True, called=called)

        spot = {"called": False}

        async def numeric_stream(*a, **k):
            yield "營收年增 30%[1]"

        async def spy_spot(qa_id, answer, manifest):
            spot["called"] = True
            spot["qa_id"] = qa_id
            spot["answer"] = answer

        saved = (ans.stream_completion, ans._faithfulness_spot_check,
                 ans.ASK_FAITHFULNESS_SAMPLE_RATE)
        ans.stream_completion = numeric_stream
        ans._faithfulness_spot_check = spy_spot
        ans.ASK_FAITHFULNESS_SAMPLE_RATE = 1.0        # 抽樣必中
        try:
            _ = [e async for e in ans.answer_question("可口可樂營收")]
        finally:
            (ans.stream_completion, ans._faithfulness_spot_check,
             ans.ASK_FAITHFULNESS_SAMPLE_RATE) = saved
            self._restore(ans, rp, orig)

        self.assertTrue(spot["called"])                  # 抽查確實被 answer_question 呼叫
        self.assertTrue(spot.get("qa_id"))               # 帶 qa_id（真 uuid，非空）
        self.assertIn("30%", spot.get("answer", ""))     # 帶答案本文供 grounding

    async def test_non_numeric_answer_skips_spot_check(self):
        # 答案無金融數字 → 不觸發抽查（省成本）。
        import app.services.retrieval_pipeline as rp
        from app.services import answer as ans

        called = {"llm": False, "intent": False}
        orig = self._patch(ans, rp, in_domain=True, called=called)  # fake_stream 回「答案[1]」

        spot = {"called": False}

        async def spy_spot(*a, **k):
            spot["called"] = True

        saved = (ans._faithfulness_spot_check, ans.ASK_FAITHFULNESS_SAMPLE_RATE)
        ans._faithfulness_spot_check = spy_spot
        ans.ASK_FAITHFULNESS_SAMPLE_RATE = 1.0
        try:
            _ = [e async for e in ans.answer_question("可口可樂展望")]
        finally:
            (ans._faithfulness_spot_check, ans.ASK_FAITHFULNESS_SAMPLE_RATE) = saved
            self._restore(ans, rp, orig)

        self.assertFalse(spot["called"])  # 「答案[1]」無金融數字 → 不查

    async def test_on_topic_intent_calls_llm(self):
        # 意圖判定為在領域 → 正常檢索 + 串流回答 + 引用
        import app.services.retrieval_pipeline as rp
        from app.services import answer as ans

        called = {"llm": False, "intent": False}
        orig = self._patch(ans, rp, in_domain=True, called=called)
        try:
            events = [e async for e in ans.answer_question("可口可樂的投資評級如何")]
        finally:
            self._restore(ans, rp, orig)

        kinds = [k for k, _ in events]
        self.assertEqual(kinds[0], "status")  # 開頭為 understanding status
        self.assertIn("sources", kinds)
        srcs = next(p for k, p in events if k == "sources")
        self.assertTrue(len(srcs) >= 1)  # 有來源
        self.assertIn(("token", "答案[1]"), events)
        self.assertEqual(events[-1][0], "done")
        self.assertEqual(events[-1][1]["cited"], ["r1"])
        self.assertIn("qa_id", events[-1][1])  # done 帶 qa_id 供前端掛回饋
        self.assertIn("conversation_id", events[-1][1])
        self.assertTrue(called["llm"])  # 有跑主 LLM

    def _patch_simplified_stream(self, ans, text: str):
        """把主 LLM 換成吐指定字串的串流，並關掉 done 之後的兩個外呼。

        `generate_followups` 用的是 `followups.stream_completion`（不是 answer 的那個
        名字），不擋就會真的去 spawn `claude`。fake 的簽章刻意收 **k 以外的兩個位置
        參數——與真函式一致，漂移時會在呼叫點大聲 TypeError 而不是靜默走別條路。
        """
        async def fake_stream(*a, **k):
            yield text

        async def no_followups(question, answer, **k):
            return []

        saved = (ans.stream_completion, ans.generate_followups,
                 ans.ASK_FAITHFULNESS_SAMPLE_RATE)
        ans.stream_completion = fake_stream
        ans.generate_followups = no_followups
        ans.ASK_FAITHFULNESS_SAMPLE_RATE = 0.0  # 抽查必不中，測試不依賴亂數
        return saved

    @staticmethod
    def _restore_simplified_stream(ans, saved):
        (ans.stream_completion, ans.generate_followups,
         ans.ASK_FAITHFULNESS_SAMPLE_RATE) = saved

    async def test_simplified_answer_converted_before_persist_and_corrected_on_done(self):
        """串流吐簡體 → 落庫與 done 都是繁體；token 本身刻意維持原樣。

        轉換是整串決定的（見 app/services/zh_hant.py 的門檻），串流當下拿不到整串，
        所以不在中途改判——那會產生半繁半簡的畫面。畫面靠 done 的 answer 收斂。
        """
        import app.services.retrieval_pipeline as rp
        from app.services import answer as ans

        called = {"llm": False, "intent": False}
        orig = self._patch(ans, rp, in_domain=True, called=called)
        saved = self._patch_simplified_stream(ans, "群联电子营收创同期新高[1]")

        logged = {}

        async def spy_log(question, body, *a, **k):
            logged["body"] = body
            return "qa-1"

        saved_log = ans._log_qa
        ans._log_qa = spy_log
        try:
            events = [e async for e in ans.answer_question("群聯營收")]
        finally:
            ans._log_qa = saved_log
            self._restore_simplified_stream(ans, saved)
            self._restore(ans, rp, orig)

        expected = "群聯電子營收創同期新高[1]"
        # 比對 token 的**串接**：SentinelStreamParser 會為了看哨符而把首字單獨吐出，
        # token 的切法不是這條測試的契約，「串流內容未被改寫」才是。
        streamed = "".join(p for k, p in events if k == "token")
        self.assertEqual(streamed, "群联电子营收创同期新高[1]")
        done = next(p for k, p in events if k == "done")
        self.assertEqual(done["answer"], expected)   # 畫面校正
        self.assertEqual(logged["body"], expected)   # 落庫
        self.assertEqual(done["cited"], ["r1"])      # [n] 是 ASCII，轉換不影響引用解析

    async def test_traditional_answer_carries_no_correction_field(self):
        """沒改動就不帶 answer——否則每一次問答的 done 都要多背一份完整答案。"""
        import app.services.retrieval_pipeline as rp
        from app.services import answer as ans

        called = {"llm": False, "intent": False}
        orig = self._patch(ans, rp, in_domain=True, called=called)
        saved = self._patch_simplified_stream(ans, "台積電營收創同期新高[1]")
        try:
            events = [e async for e in ans.answer_question("台積電營收")]
        finally:
            self._restore_simplified_stream(ans, saved)
            self._restore(ans, rp, orig)

        done = next(p for k, p in events if k == "done")
        self.assertNotIn("answer", done)

    async def test_no_context_emits_retrieved_zero_without_reading(self):
        # 在領域但檢索無結果 → 無脈絡：retrieved(count=0)、不發 reading、回 NO_CONTEXT，且不跑主 LLM
        import app.services.retrieval_pipeline as rp
        from app.services import answer as ans

        called = {"llm": False, "intent": False}
        orig = self._patch(ans, rp, in_domain=True, called=called)

        async def empty_search(*a, **k):
            return []

        rp.hybrid_search = empty_search  # 覆寫 _patch 的單列 fake_search
        try:
            events = [e async for e in ans.answer_question("某個查不到的冷門問題")]
        finally:
            self._restore(ans, rp, orig)

        kinds = [k for k, _ in events]
        self.assertEqual(
            kinds,
            ["status", "status", "sources", "status", "status", "token", "done"],
        )
        self.assertEqual(events[0], ("status", {"stage": "understanding"}))
        # 檢索前先推進步驟（不帶 count），檢索完成後才補上數量
        self.assertEqual(events[1], ("status", {"stage": "retrieved"}))
        self.assertEqual(events[3], ("status", {"stage": "retrieved", "count": 0}))
        # 無脈絡路徑不得發 reading
        self.assertNotIn(("status", {"stage": "reading"}), events)
        # NO_CONTEXT token 前補發 generating，帶 thinking_ms
        self.assertEqual(events[4][0], "status")
        self.assertEqual(events[4][1]["stage"], "generating")
        self.assertIn("thinking_ms", events[4][1])
        self.assertEqual(events[5], ("token", ans.NO_CONTEXT_MESSAGE))
        self.assertFalse(called["llm"])  # 未跑主 LLM

    async def test_emits_process_status_steps(self):
        # 在領域問題：事件序須含 understanding(開頭) → retrieved(count) → reading(token 前)
        import app.services.retrieval_pipeline as rp
        from app.services import answer as ans

        called = {"llm": False, "intent": False}
        orig = self._patch(ans, rp, in_domain=True, called=called)
        try:
            events = [e async for e in ans.answer_question("可口可樂的投資評級如何")]
        finally:
            self._restore(ans, rp, orig)

        # 第一個事件即 understanding（在檢索/來源之前）
        self.assertEqual(events[0], ("status", {"stage": "understanding"}))

        statuses = [p for k, p in events if k == "status"]
        # retrieved 帶實際來源數（fake_search 回 1 列）
        self.assertIn({"stage": "retrieved", "count": 1}, statuses)
        self.assertIn({"stage": "reading"}, statuses)

        # 順序：understanding(0) < retrieved < reading < 第一個 token
        i_retrieved = next(
            i
            for i, (k, p) in enumerate(events)
            if k == "status" and p.get("stage") == "retrieved"
        )
        i_reading = next(
            i
            for i, (k, p) in enumerate(events)
            if k == "status" and p.get("stage") == "reading"
        )
        i_token = next(i for i, (k, _) in enumerate(events) if k == "token")
        self.assertLess(0, i_retrieved)
        self.assertLess(i_retrieved, i_reading)
        self.assertLess(i_reading, i_token)

    async def test_slow_retrieval_advances_stage_before_sources(self):
        # 線上的真實比例：路由（約 12s）先回、檢索含 rerank（約 48s）還在跑。此時
        # 必須先把步驟推進到 retrieved，否則前端整段停在「理解問題」，與伺服器卡死
        # 無從分辨。其餘測試的 fake 檢索是瞬間完成的，走不到這條分支。
        import app.services.retrieval_pipeline as rp
        from app.services import answer as ans

        called = {"llm": False, "intent": False}
        orig = self._patch(ans, rp, in_domain=True, called=called)
        instant_search = rp.hybrid_search

        async def slow_search(*a, **k):
            await asyncio.sleep(0.05)  # 讓 route_task 先完成、retrieve_task 仍在跑
            return await instant_search(*a, **k)

        rp.hybrid_search = slow_search
        try:
            events = [e async for e in ans.answer_question("可口可樂的投資評級如何")]
        finally:
            self._restore(ans, rp, orig)

        stages = [p["stage"] for k, p in events if k == "status"]
        # 兩筆 retrieved：檢索前推進（不帶 count）＋檢索後補數量
        self.assertEqual(stages.count("retrieved"), 2)
        i_advance = next(
            i
            for i, (k, p) in enumerate(events)
            if k == "status" and p == {"stage": "retrieved"}
        )
        i_sources = next(i for i, (k, _) in enumerate(events) if k == "sources")
        self.assertLess(i_advance, i_sources)

    async def test_emits_generating_with_thinking_ms(self):
        # 正常路徑：第一個 token 前發 generating 帶 int thinking_ms；done 亦帶 thinking_ms
        import app.services.retrieval_pipeline as rp
        from app.services import answer as ans

        called = {"llm": False, "intent": False}
        orig = self._patch(ans, rp, in_domain=True, called=called)
        try:
            events = [e async for e in ans.answer_question("可口可樂的投資評級如何")]
        finally:
            self._restore(ans, rp, orig)

        i_gen = next(
            i
            for i, (k, p) in enumerate(events)
            if k == "status" and isinstance(p, dict) and p.get("stage") == "generating"
        )
        i_token = next(i for i, (k, _) in enumerate(events) if k == "token")
        self.assertLess(i_gen, i_token)  # generating 在第一個 token 之前
        i_reading = next(
            i
            for i, (k, p) in enumerate(events)
            if k == "status" and isinstance(p, dict) and p.get("stage") == "reading"
        )
        self.assertLess(i_reading, i_gen)  # 事件序：reading → generating → token
        self.assertEqual(
            sum(
                1
                for k, p in events
                if k == "status" and isinstance(p, dict) and p.get("stage") == "generating"
            ),
            1,
        )  # generating 只發一次
        self.assertIsInstance(events[i_gen][1]["thinking_ms"], int)
        self.assertEqual(events[-1][0], "done")
        self.assertIsInstance(events[-1][1]["thinking_ms"], int)  # done 帶 thinking_ms


class ScopeRoutingTests(unittest.IsolatedAsyncioTestCase):
    """M4 四分支：off_topic/time_sensitive 不檢索不發 sources；advice_risk 加政策；corpus_qa 關網搜。"""

    def _patch(self, ans, rp, *, decision, called):
        async def fake_search(*a, **k):
            called["search"] = True
            return [
                (
                    0,
                    0.80,
                    make_row(
                        "r1", "x.pdf", "TW", "內容。", date(2026, 6, 1), distance=0.2
                    ),
                )
            ]

        def fake_embed(q):
            called["embed"] = True
            return [0.0]

        async def fake_stream(*a, **k):
            called["llm"] = True
            called["stream_kwargs"] = k
            yield "答案[1]"

        async def fake_route(question, **k):
            called["route"] = True
            return decision

        async def fake_load(conversation_id, **k):
            return []

        async def fake_log(question, answer, cited, filters, *a, **k):
            called["log_filters"] = filters
            return "qa-routed"

        orig = (
            rp.hybrid_search,
            rp.embed_query_cached,
            ans.stream_completion,
            rp.SessionFactory,
            ans.SessionFactory,
            ans.classify_non_overview,
            ans.load_recent_turns,
            ans._log_qa,
        )
        rp.hybrid_search = fake_search
        rp.embed_query_cached = fake_embed
        ans.stream_completion = fake_stream
        rp.SessionFactory = lambda: _FakeSession()
        ans.SessionFactory = lambda: _FakeSession()
        ans.classify_non_overview = fake_route
        ans.load_recent_turns = fake_load
        ans._log_qa = fake_log
        return orig

    @staticmethod
    def _restore(ans, rp, orig):
        (
            rp.hybrid_search,
            rp.embed_query_cached,
            ans.stream_completion,
            rp.SessionFactory,
            ans.SessionFactory,
            ans.classify_non_overview,
            ans.load_recent_turns,
            ans._log_qa,
        ) = orig

    async def test_time_sensitive_first_turn_skips_retrieval_entirely(self):
        # 首輪且前檢命中：檢索連起跑都不該起跑。
        # 反轉點——這題先前斷言的是 assertTrue(called["search"])，註解寫「並行取證
        # 確實跑過，僅結果被丟棄」。那份被丟棄的工作含 rerank（生產實測數十秒 CPU，
        # 且 semaphore 預設只有一個名額，會擋住後面排隊的人）。前檢是確定性的，
        # 沒有任何理由等檢索跑完才知道這題不需要語料。
        import app.services.retrieval_pipeline as rp
        from app.services import answer as ans

        called = {}
        orig = self._patch(
            ans, rp, decision=sr._decision(sr.TIME_SENSITIVE), called=called
        )
        try:
            events = [e async for e in ans.answer_question("台積電現在股價多少")]
        finally:
            self._restore(ans, rp, orig)

        kinds = [k for k, _ in events]
        self.assertEqual(kinds, ["status", "sources", "notice", "done"])
        self.assertEqual(events[1], ("sources", []))
        self.assertEqual(events[2], ("notice", ans.TIME_SENSITIVE_UNAVAILABLE_MESSAGE))
        self.assertFalse(called.get("search"))  # 檢索未起跑
        self.assertFalse(called.get("embed"))   # 連 embed 都不必
        self.assertFalse(called.get("route"))   # 前檢已定案，分類器不必呼叫
        self.assertFalse(called.get("llm"))     # 主 LLM 不得被呼叫
        lf = called.get("log_filters", {})
        self.assertEqual(lf.get("path"), "time_sensitive")
        self.assertEqual(lf.get("decided_by"), "precheck")
        done = events[-1][1]
        self.assertNotIn("qa_id", done)  # 比照既有離題 done payload 形狀
        self.assertEqual(
            done, {"cited": [], "conversation_id": done["conversation_id"],
                   "thinking_ms": done["thinking_ms"],
                   "notice_kind": "time_sensitive"},
        )

    async def test_off_topic_route_cancels_in_flight_retrieval(self):
        """分類先回且判離題 → 當場取消仍在跑的檢索，不等它跑完。

        離題靠前檢判不出來（_safety_precheck 只判時效與個人化），只能等分類器；
        而分類是秒級、檢索含 rerank 是數十秒。先前寫成「await 檢索 → await 路由」，
        於是離題題也是付完整檢索才丟棄——量比時效題大得多，白工主要在這裡。

        _patch 的替身都是瞬時的，誰先完成不穩定；這裡讓 hybrid_search 明確地慢，
        「路由先到」才是確定事實，取消才驗得到。
        """
        import app.services.retrieval_pipeline as rp
        from app.services import answer as ans

        called = {}
        orig = self._patch(
            ans, rp,
            decision=sr._decision(sr.OFF_TOPIC, decided_by=sr.BY_LLM),
            called=called,
        )
        faked_search = rp.hybrid_search
        search_finished = False

        async def slow_search(*a, **k):
            nonlocal search_finished
            await asyncio.sleep(0.2)
            search_finished = True
            return await faked_search(*a, **k)

        rp.hybrid_search = slow_search
        try:
            events = [e async for e in ans.answer_question("我想喝飲料推薦給我")]
        finally:
            self._restore(ans, rp, orig)

        self.assertEqual(
            [k for k, _ in events], ["status", "sources", "notice", "done"]
        )
        self.assertTrue(called.get("embed"))   # 檢索確實已經起跑
        self.assertFalse(search_finished)      # 但沒讓它跑完
        lf = called.get("log_filters", {})
        self.assertEqual(lf.get("path"), "off_topic")  # 先前 off_topic 根本不寫 path
        self.assertEqual(lf.get("decided_by"), "llm")
        self.assertEqual(events[-1][1]["notice_kind"], "off_topic")

    async def test_advice_risk_appends_policy_and_emits_sources(self):
        # advice_risk 走 RAG：附研究資訊限制政策、正常發真實 sources（有據回答須附出處）
        import app.services.retrieval_pipeline as rp
        from app.services import answer as ans

        called = {}
        orig = self._patch(
            ans, rp, decision=sr._decision(sr.ADVICE_RISK), called=called
        )
        try:
            events = [e async for e in ans.answer_question("台積電該不該買")]
        finally:
            self._restore(ans, rp, orig)

        kinds = [k for k, _ in events]
        self.assertIn("sources", kinds)
        srcs = next(p for k, p in events if k == "sources")
        self.assertTrue(len(srcs) >= 1)  # 有真實來源，非丟棄
        self.assertTrue(called.get("llm"))
        system_prompt = called["stream_kwargs"]["system"]
        self.assertTrue(system_prompt.endswith(ans.RESEARCH_ONLY_POLICY))
        self.assertEqual(called.get("log_filters", {}).get("path"), "advice_risk")

    async def test_corpus_qa_disables_web(self):
        # corpus_qa（M4 預設工具政策）：主 LLM 呼叫一律 allow_web=False
        import app.services.retrieval_pipeline as rp
        from app.services import answer as ans

        called = {}
        orig = self._patch(
            ans, rp,
            decision=sr._decision(sr.CORPUS_QA, decided_by=sr.BY_LLM),
            called=called,
        )
        try:
            _ = [e async for e in ans.answer_question("台積電展望")]
        finally:
            self._restore(ans, rp, orig)

        self.assertTrue(called.get("llm"))
        self.assertIs(called["stream_kwargs"]["allow_web"], False)
        # 反轉點：先前斷言「corpus_qa 不寫 path」。留白的代價是它與 off_topic 在
        # qa_log 裡長得一樣，誰都量不出來。
        lf = called.get("log_filters", {})
        self.assertEqual(lf.get("path"), "corpus_qa")
        self.assertEqual(lf.get("decided_by"), "llm")

    async def test_fail_open_corpus_qa_distinguishable_from_real_one(self):
        """分類器壞掉猜的 corpus_qa，必須與它真的判出來的 corpus_qa 在 log 裡分得開。

        fail-open 的落點就是 CORPUS_QA（scope_router 的設計），所以分類器每失敗
        一次，就有一題時效或離題問題被拿歷史研報回答——而且無聲。
        """
        import app.services.retrieval_pipeline as rp
        from app.services import answer as ans

        called = {}
        orig = self._patch(
            ans, rp,
            decision=sr._decision(sr.CORPUS_QA, decided_by=sr.BY_FAIL_OPEN),
            called=called,
        )
        try:
            _ = [e async for e in ans.answer_question("台積電展望")]
        finally:
            self._restore(ans, rp, orig)

        lf = called.get("log_filters", {})
        self.assertEqual(lf.get("path"), "corpus_qa")
        self.assertEqual(lf.get("decided_by"), "fail_open")

    async def test_multiturn_time_sensitive_skips_retrieval(self):
        # 續問：condense_and_route 直接判 time_sensitive → 提前返回，retrieve_context 完全未跑
        import app.services.retrieval_pipeline as rp
        from app.services import answer as ans

        called = {"search": False, "embed": False}

        async def fake_search(*a, **k):
            called["search"] = True
            return []

        def fake_embed(q):
            called["embed"] = True
            return [0.0]

        async def fake_condense(history_text, question, **k):
            return ("台積電現在股價多少", sr._decision(sr.TIME_SENSITIVE))

        async def fake_load(conversation_id, **k):
            return [("台積電前景?", "看好[1]")]

        async def fake_route(question, **k):
            raise AssertionError("續問不應呼叫 classify_non_overview")

        orig = (
            rp.hybrid_search,
            rp.embed_query_cached,
            rp.SessionFactory,
            ans.SessionFactory,
            ans.classify_non_overview,
            ans.condense_and_route,
            ans.load_recent_turns,
        )
        rp.hybrid_search = fake_search
        rp.embed_query_cached = fake_embed
        rp.SessionFactory = lambda: _FakeSession()
        ans.SessionFactory = lambda: _FakeSession()
        ans.classify_non_overview = fake_route
        ans.condense_and_route = fake_condense
        ans.load_recent_turns = fake_load
        try:
            events = [
                e
                async for e in ans.answer_question("現在多少?", conversation_id="c1")
            ]
        finally:
            (
                rp.hybrid_search,
                rp.embed_query_cached,
                rp.SessionFactory,
                ans.SessionFactory,
                ans.classify_non_overview,
                ans.condense_and_route,
                ans.load_recent_turns,
            ) = orig

        self.assertFalse(called["search"])  # retrieve_context 未被呼叫
        self.assertFalse(called["embed"])
        notice = next(p for k, p in events if k == "notice")
        self.assertEqual(notice, ans.TIME_SENSITIVE_UNAVAILABLE_MESSAGE)
        self.assertEqual(events[1], ("sources", []))


class AnswerWebTests(unittest.IsolatedAsyncioTestCase):
    async def test_body_excludes_sentinel_and_emits_ext_sources(self):
        import app.services.retrieval_pipeline as rp
        from app.services import answer as ans

        async def fake_search(*a, **k):
            return [
                (
                    1,
                    0.85,
                    make_row(
                        "r1",
                        "x.pdf",
                        "TW",
                        "台積電先進封裝。",
                        date(2026, 6, 1),
                        distance=0.2,
                    ),
                )
            ]

        def fake_embed(q):
            return [0.0]

        async def fake_stream(*a, **k):
            for ch in [
                "前段答案[1]。",
                "（網路）補充。",
                "\n[EXT_SOURCES]\n- 標題 | https://x.com\n",
            ]:
                yield ch

        async def fake_route(q, **k):
            return sr._decision(sr.CORPUS_QA)

        async def fake_load(*a, **k):
            return []

        async def fake_condense(*a, **k):
            return (a[1] if len(a) > 1 else "", sr._decision(sr.CORPUS_QA))

        orig = (
            rp.hybrid_search,
            rp.embed_query_cached,
            ans.stream_completion,
            rp.SessionFactory,
            ans.SessionFactory,
            ans.classify_non_overview,
            ans.condense_and_route,
            ans.load_recent_turns,
        )
        rp.hybrid_search = fake_search
        rp.embed_query_cached = fake_embed
        ans.stream_completion = fake_stream
        rp.SessionFactory = lambda: _FakeSession()
        ans.SessionFactory = lambda: _FakeSession()
        ans.classify_non_overview = fake_route
        ans.condense_and_route = fake_condense
        ans.load_recent_turns = fake_load
        try:
            events = [e async for e in ans.answer_question("台積電封裝")]
        finally:
            (
                rp.hybrid_search,
                rp.embed_query_cached,
                ans.stream_completion,
                rp.SessionFactory,
                ans.SessionFactory,
                ans.classify_non_overview,
                ans.condense_and_route,
                ans.load_recent_turns,
            ) = orig

        body = "".join(p for k, p in events if k == "token")
        self.assertIn("前段答案[1]。", body)
        self.assertIn("（網路）補充。", body)
        self.assertNotIn("[EXT_SOURCES]", body)  # sentinel 不外洩
        self.assertNotIn("https://x.com", body)  # 來源不混進正文
        ext = [p for k, p in events if k == "ext_sources"]
        self.assertEqual(len(ext), 1)
        self.assertEqual(ext[0], [{"title": "標題", "url": "https://x.com"}])
        self.assertEqual(
            [k for k, _ in events][0], "status"
        )  # 事件序起點＝understanding
        self.assertEqual(events[-2][0], "ext_sources")  # ext_sources 緊鄰 done 之前
        self.assertEqual(events[-1][0], "done")
        self.assertEqual(events[-1][1]["cited"], ["r1"])

    async def test_sentinel_split_across_chunks_not_leaked(self):
        # sentinel 被拆在兩個 chunk（"[EXT_" + "SOURCES]"）：hold 尾段須仍攔截、不外洩
        import app.services.retrieval_pipeline as rp
        from app.services import answer as ans

        async def fake_search(*a, **k):
            return [
                (
                    1,
                    0.85,
                    make_row(
                        "r1", "x.pdf", "TW", "內容。", date(2026, 6, 1), distance=0.2
                    ),
                )
            ]

        def fake_embed(q):
            return [0.0]

        async def fake_stream(*a, **k):
            for ch in ["前段答案[1]。\n[EXT_", "SOURCES]\n- 標題 | https://x.com\n"]:
                yield ch

        async def fake_route(q, **k):
            return sr._decision(sr.CORPUS_QA)

        async def fake_load(*a, **k):
            return []

        async def fake_condense(*a, **k):
            return (a[1] if len(a) > 1 else "", sr._decision(sr.CORPUS_QA))

        orig = (
            rp.hybrid_search,
            rp.embed_query_cached,
            ans.stream_completion,
            rp.SessionFactory,
            ans.SessionFactory,
            ans.classify_non_overview,
            ans.condense_and_route,
            ans.load_recent_turns,
        )
        rp.hybrid_search = fake_search
        rp.embed_query_cached = fake_embed
        ans.stream_completion = fake_stream
        rp.SessionFactory = lambda: _FakeSession()
        ans.SessionFactory = lambda: _FakeSession()
        ans.classify_non_overview = fake_route
        ans.condense_and_route = fake_condense
        ans.load_recent_turns = fake_load
        try:
            events = [e async for e in ans.answer_question("問題")]
        finally:
            (
                rp.hybrid_search,
                rp.embed_query_cached,
                ans.stream_completion,
                rp.SessionFactory,
                ans.SessionFactory,
                ans.classify_non_overview,
                ans.condense_and_route,
                ans.load_recent_turns,
            ) = orig

        body = "".join(p for k, p in events if k == "token")
        self.assertIn("前段答案[1]。", body)
        self.assertNotIn("[EXT_SOURCES]", body)  # 跨 chunk 仍不外洩
        self.assertNotIn("[EXT_", body)  # 半截 sentinel 也不外洩
        self.assertNotIn("https://x.com", body)
        ext = [p for k, p in events if k == "ext_sources"]
        self.assertEqual(ext[0], [{"title": "標題", "url": "https://x.com"}])

    async def test_emits_status_when_web_search_starts(self):
        # 模型開始搜尋（stream 吐 SEARCH_EVENT 標記）→ 發一次 ("status","searching_web")，標記不外洩
        import app.services.retrieval_pipeline as rp
        from app.services import answer as ans
        from app.services.llm import SEARCH_EVENT

        async def fake_search(*a, **k):
            return [
                (
                    1,
                    0.85,
                    make_row(
                        "r1", "x.pdf", "TW", "內容。", date(2026, 6, 1), distance=0.2
                    ),
                )
            ]

        def fake_embed(q):
            return [0.0]

        async def fake_stream(*a, **k):
            yield SEARCH_EVENT  # 模型開始上網
            yield "答案[1]。"

        async def fake_route(q, **k):
            return sr._decision(sr.CORPUS_QA)

        async def fake_load(*a, **k):
            return []

        async def fake_condense(*a, **k):
            return (a[1] if len(a) > 1 else "", sr._decision(sr.CORPUS_QA))

        orig = (
            rp.hybrid_search,
            rp.embed_query_cached,
            ans.stream_completion,
            rp.SessionFactory,
            ans.SessionFactory,
            ans.classify_non_overview,
            ans.condense_and_route,
            ans.load_recent_turns,
        )
        rp.hybrid_search = fake_search
        rp.embed_query_cached = fake_embed
        ans.stream_completion = fake_stream
        rp.SessionFactory = lambda: _FakeSession()
        ans.SessionFactory = lambda: _FakeSession()
        ans.classify_non_overview = fake_route
        ans.condense_and_route = fake_condense
        ans.load_recent_turns = fake_load
        try:
            events = [e async for e in ans.answer_question("問題")]
        finally:
            (
                rp.hybrid_search,
                rp.embed_query_cached,
                ans.stream_completion,
                rp.SessionFactory,
                ans.SessionFactory,
                ans.classify_non_overview,
                ans.condense_and_route,
                ans.load_recent_turns,
            ) = orig

        self.assertIn(("status", {"stage": "searching_web"}), events)
        self.assertEqual(
            sum(
                1
                for k, p in events
                if k == "status" and p.get("stage") == "searching_web"
            ),
            1,
        )  # 只發一次
        body = "".join(p for k, p in events if k == "token")
        self.assertNotIn(SEARCH_EVENT, body)  # 控制標記不外洩到正文
        self.assertIn("答案[1]。", body)


class WebSearchDetectTests(unittest.TestCase):
    def test_detects_websearch_tool_use(self):
        line = (
            '{"type":"stream_event","event":{"type":"content_block_start",'
            '"content_block":{"type":"tool_use","name":"WebSearch","input":{}}}}'
        )
        self.assertTrue(llm.is_web_search_start(line))

    def test_other_tool_not_detected(self):
        line = (
            '{"type":"stream_event","event":{"type":"content_block_start",'
            '"content_block":{"type":"tool_use","name":"ToolSearch","input":{}}}}'
        )
        self.assertFalse(llm.is_web_search_start(line))

    def test_text_thinking_and_malformed_not_detected(self):
        self.assertFalse(
            llm.is_web_search_start(
                '{"type":"stream_event","event":{"type":"content_block_start",'
                '"content_block":{"type":"text","text":""}}}'
            )
        )
        self.assertFalse(
            llm.is_web_search_start(
                '{"type":"stream_event","event":{"type":"content_block_delta",'
                '"delta":{"type":"text_delta","text":"hi"}}}'
            )
        )
        self.assertFalse(llm.is_web_search_start("not json"))
        self.assertFalse(llm.is_web_search_start(""))


class FeedbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_value_rejected_without_db(self):
        # 非 like/dislike/none 一律 False，且不觸碰 DB（純驗證分支）
        from app.services.answer import record_feedback

        self.assertFalse(await record_feedback("any-id", "love"))
        self.assertFalse(await record_feedback("any-id", ""))
        self.assertFalse(await record_feedback("any-id", "null"))

    async def test_none_binds_sql_null_not_the_string(self):
        # 'none'＝使用者再點一次取消。必須綁 SQL NULL：讀取端（/api/history、
        # /api/qa/{root_qa_id}/versions 與前端 zod）認的是 'like'|'dislike'|null，存進字面值
        # 'none' 會讓歷史清單整頁 parse 失敗，而 zod 的 strip/報錯都不會指回這裡。
        from app.services import answer as ans

        captured = {}

        class Result:
            rowcount = 1

        class Sess:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def execute(self, stmt, params=None):
                captured["params"] = params
                return Result()

            async def commit(self):
                return None

        orig = ans.SessionFactory
        ans.SessionFactory = lambda: Sess()
        try:
            ok = await ans.record_feedback("q1", "none")
        finally:
            ans.SessionFactory = orig

        self.assertTrue(ok)
        self.assertIsNone(captured["params"]["v"])

    async def test_missing_row_reports_failure(self):
        from app.services import answer as ans

        class Result:
            rowcount = 0

        class Sess:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def execute(self, *a, **k):
                return Result()

            async def commit(self):
                return None

        orig = ans.SessionFactory
        ans.SessionFactory = lambda: Sess()
        try:
            ok = await ans.record_feedback("missing-id", "like")
        finally:
            ans.SessionFactory = orig

        self.assertFalse(ok)


class BuildCmdTests(unittest.TestCase):
    def test_web_flag_adds_allowed_tools(self):
        cmd = llm._build_cmd("m", None, True)
        self.assertIn("--allowedTools", cmd)
        self.assertEqual(cmd[cmd.index("--allowedTools") + 1], "WebSearch")

    def test_no_web_flag_by_default(self):
        cmd = llm._build_cmd("m", None, False)
        self.assertNotIn("--allowedTools", cmd)

    def test_system_prompt_included_when_given(self):
        self.assertIn("--system-prompt", llm._build_cmd("m", "你是助理", False))
        self.assertNotIn("--system-prompt", llm._build_cmd("m", None, False))

    def test_core_flags_present(self):
        cmd = llm._build_cmd("claude-sonnet-4-6", None, False)
        for flag in (
            "claude",
            "-p",
            "--model",
            "stream-json",
            "--include-partial-messages",
        ):
            self.assertIn(flag, cmd)


class SplitExternalSourcesTests(unittest.TestCase):
    def test_no_sentinel_returns_text_and_empty(self):
        body, ext = split_external_sources("純研報答案[1]。")
        self.assertEqual(body, "純研報答案[1]。")
        self.assertEqual(ext, [])

    def test_parses_sentinel_block(self):
        text = (
            "答案內容（網路）。[1]\n\n"
            "[EXT_SOURCES]\n"
            "- 標題A | https://a.com/x\n"
            "- 標題B | http://b.com\n"
        )
        body, ext = split_external_sources(text)
        self.assertEqual(body, "答案內容（網路）。[1]")  # sentinel 前、尾端空白修整
        self.assertEqual(
            ext,
            [
                {"title": "標題A", "url": "https://a.com/x"},
                {"title": "標題B", "url": "http://b.com"},
            ],
        )

    def test_skips_malformed_and_non_http(self):
        text = "答案。\n[EXT_SOURCES]\n- 沒有管線的壞行\n- 標題 | ftp://x\n- 好的 | https://ok.com\n"
        body, ext = split_external_sources(text)
        self.assertEqual(ext, [{"title": "好的", "url": "https://ok.com"}])

    def test_empty_title_falls_back_to_url(self):
        body, ext = split_external_sources(
            "答案。\n[EXT_SOURCES]\n-  | https://a.com\n"
        )
        self.assertEqual(ext, [{"title": "https://a.com", "url": "https://a.com"}])


class LogQaSourcesTests(unittest.IsolatedAsyncioTestCase):
    async def test_log_qa_inserts_sources_and_ext_sources_json(self):
        from app.services import answer as ans

        captured = {}

        class Sess:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def execute(self, stmt, params=None):
                captured.update(params or {})

            async def commit(self):
                return None

        orig = ans.SessionFactory
        ans.SessionFactory = lambda: Sess()
        try:
            qid = await ans._log_qa(
                "q",
                "a",
                [],
                {},
                5,
                [{"n": 1, "report_id": "r1"}],
                [{"title": "外部", "url": "https://x.com"}],
            )
        finally:
            ans.SessionFactory = orig

        self.assertTrue(qid)
        self.assertIn("sources", captured)
        self.assertIn("ext_sources", captured)
        self.assertEqual(json.loads(captured["sources"]), [{"n": 1, "report_id": "r1"}])
        self.assertEqual(
            json.loads(captured["ext_sources"]),
            [{"title": "外部", "url": "https://x.com"}],
        )


class HistoryItemTests(unittest.TestCase):
    def test_maps_row_with_sources_and_ext_sources(self):
        d = date(2026, 6, 18)
        row = (
            "11111111-1111-1111-1111-111111111111",
            "台積電?",
            "答案[1]",
            d,
            "like",
            [{"n": 1, "report_id": "r1", "file_name": "甲.pdf"}],
            [{"title": "外部", "url": "https://x.com"}],
        )
        out = history_item(row)
        self.assertEqual(out["id"], "11111111-1111-1111-1111-111111111111")
        self.assertEqual(out["question"], "台積電?")
        self.assertEqual(out["answer"], "答案[1]")
        self.assertEqual(out["created_at"], "2026-06-18")
        self.assertEqual(out["feedback"], "like")
        self.assertEqual(
            out["sources"], [{"n": 1, "report_id": "r1", "file_name": "甲.pdf"}]
        )
        self.assertEqual(
            out["ext_sources"], [{"title": "外部", "url": "https://x.com"}]
        )

    def test_null_sources_and_ext_sources_become_empty_list(self):
        row = ("id2", "q", "a", date(2026, 6, 1), None, None, None)
        out = history_item(row)
        self.assertEqual(out["sources"], [])
        self.assertEqual(out["ext_sources"], [])
        self.assertIsNone(out["feedback"])

    def test_old_row_without_ext_sources_still_works(self):
        row = ("id3", "q", "a", date(2026, 6, 1), None, None)
        out = history_item(row)
        self.assertEqual(out["sources"], [])
        self.assertEqual(out["ext_sources"], [])

    def test_marks_offtopic_rows_for_frontend_notice_rendering(self):
        from app.services import answer as ans

        row = ("id4", "q", ans.OFF_TOPIC_MESSAGE, date(2026, 6, 1), None, None, None)
        out = history_item(row)
        self.assertTrue(out["is_offtopic"])

    def test_legacy_offtopic_answer_still_flagged(self):
        from app.services import answer as ans

        # 舊 qa_log 列存舊婉拒文案；文案改版後仍須標 is_offtopic
        legacy = ans.OFF_TOPIC_MESSAGES[-1]
        self.assertNotEqual(legacy, ans.OFF_TOPIC_MESSAGE)  # 確認 tuple 含舊版
        row = ("id4", "q", legacy, date(2026, 6, 1), None, None, None)
        item = history_item(row)
        self.assertTrue(item["is_offtopic"])

    def test_time_sensitive_notice_still_flagged_after_history_reload(self):
        from app.services import answer as ans

        row = (
            "id5", "台積電今天收盤價", ans.TIME_SENSITIVE_UNAVAILABLE_MESSAGE,
            date(2026, 6, 1), None, None, None,
        )
        item = history_item(row)
        self.assertTrue(item["is_offtopic"])

    def test_notice_kind_separates_off_topic_from_time_sensitive(self):
        """重播時要分得出兩種婉拒——is_offtopic 對兩者都是 True，那正是問題所在。"""
        from app.services import answer as ans

        def _item(answer_text):
            return history_item(
                ("idk", "q", answer_text, date(2026, 6, 1), None, None, None)
            )

        off = _item(ans.OFF_TOPIC_MESSAGE)
        ts = _item(ans.TIME_SENSITIVE_UNAVAILABLE_MESSAGE)
        ts_en = _item(ans.TIME_SENSITIVE_UNAVAILABLE_MESSAGE_EN)
        normal = _item("答案[1]")

        self.assertEqual(off["notice_kind"], "off_topic")
        self.assertEqual(ts["notice_kind"], "time_sensitive")
        self.assertEqual(ts_en["notice_kind"], "time_sensitive")
        self.assertIsNone(normal["notice_kind"])
        self.assertTrue(off["is_offtopic"])
        self.assertTrue(ts["is_offtopic"])  # 兩者的舊布林一樣，故前端分不出來

    def test_every_notice_message_maps_to_a_kind(self):
        """新增婉拒文案卻忘了給 kind → 前端悄悄退回離題那顆警告框，不會報錯。"""
        from app.services import answer as ans

        self.assertTrue(ans.NOTICE_MESSAGES)  # 空集合會讓下面的迴圈變成空斷言
        for msg in ans.NOTICE_MESSAGES:
            self.assertIsNotNone(ans.notice_kind_for(msg), msg[:24])


class _RowsResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _RowsSession:
    """假 session：execute 回固定列（供 load_recent_turns/get_conversation 測試）。"""

    def __init__(self, rows):
        self._rows = rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, *a, **k):
        return _RowsResult(self._rows)

    async def commit(self):
        return None


class _CaptureRowsSession(_RowsSession):
    def __init__(self, rows):
        super().__init__(rows)
        self.statement_text = None
        self.params = None

    async def execute(self, statement, params=None):
        self.statement_text = getattr(statement, "text", str(statement))
        self.params = params
        return _RowsResult(self._rows)


class LoadRecentTurnsTests(unittest.IsolatedAsyncioTestCase):
    async def test_returns_oldest_first(self):
        from app.services import answer as ans

        # DB 以 created_at DESC 回（新→舊）；函式須反轉成舊→新
        rows = [("新問", "新答"), ("舊問", "舊答")]
        orig = ans.SessionFactory
        ans.SessionFactory = lambda: _RowsSession(rows)
        try:
            turns = await ans.load_recent_turns("c1")
        finally:
            ans.SessionFactory = orig
        self.assertEqual(turns, [("舊問", "舊答"), ("新問", "新答")])

    async def test_db_error_returns_empty(self):
        from app.services import answer as ans

        class Boom:
            def __call__(self):
                raise RuntimeError("db down")

        orig = ans.SessionFactory
        ans.SessionFactory = Boom()
        try:
            turns = await ans.load_recent_turns("c1")
        finally:
            ans.SessionFactory = orig
        self.assertEqual(turns, [])


class ActiveFilterTests(unittest.IsolatedAsyncioTestCase):
    async def test_load_recent_turns_filters_active(self):
        from app.services import answer as ans

        captured = {}

        class _CapSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def execute(self, stmt, params=None):
                captured["sql"] = str(stmt)

                class _R:
                    def all(self_inner):
                        return []

                return _R()

            async def commit(self):
                return None

        orig = ans.SessionFactory
        ans.SessionFactory = lambda: _CapSession()
        try:
            await ans.load_recent_turns("c1")
        finally:
            ans.SessionFactory = orig

        self.assertIn("active", captured["sql"])
        self.assertIn("stopped IS NOT TRUE", captured["sql"])


class GetConversationTests(unittest.IsolatedAsyncioTestCase):
    async def test_maps_rows_via_history_item(self):
        from datetime import date

        import app.services.report as rpt
        from app.services import answer as ans

        # 13 欄須與新 SELECT 順序對齊：id, question, answer, created_at, feedback,
        # sources, ext_sources, thinking_ms, stages, followups, root_qa_id,
        # stopped, version_count
        rows = [
            ("id1", "Q1", "A1", date(2026, 6, 1), None, None, None,
             None, None, None, None, False, 1),
            ("id2", "Q2", "A2", date(2026, 6, 2), "like", None, None,
             None, None, None, None, False, 1),
        ]
        async def _no_reports(cid):
            return {}

        orig_sf = ans.SessionFactory
        orig_rfc = rpt.reports_for_conversation
        ans.SessionFactory = lambda: _RowsSession(rows)
        rpt.reports_for_conversation = _no_reports  # no-op: no reports in this test
        try:
            out = await ans.get_conversation("c1")
        finally:
            ans.SessionFactory = orig_sf
            rpt.reports_for_conversation = orig_rfc
        self.assertEqual([t["question"] for t in out], ["Q1", "Q2"])
        self.assertEqual(out[1]["feedback"], "like")


class ConversationVersionTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_conversation_includes_new_fields(self):
        from app.services import answer as ans

        # 假一列（順序須對齊新 SELECT）：
        # id, question, answer, created_at, feedback, sources, ext_sources,
        # thinking_ms, stages, followups, root_qa_id, stopped, version_count
        row = ("id1", "問題", "答案", None, None, [], [], 100,
               ["understanding", "generating"], ["追問A"], None, False, 2)

        class _Rows:
            def all(self):
                return [row]

        class _CapSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def execute(self, stmt, params=None):
                assert "active" in str(stmt)
                return _Rows()

            async def commit(self):
                return None

        async def no_reports(cid):
            return {}

        import app.services.report as rpt
        orig = (ans.SessionFactory, rpt.reports_for_conversation)
        ans.SessionFactory = lambda: _CapSession()
        rpt.reports_for_conversation = no_reports
        try:
            items = await ans.get_conversation("c1")
        finally:
            (ans.SessionFactory, rpt.reports_for_conversation) = orig

        it = items[0]
        self.assertEqual(it["stages"], ["understanding", "generating"])
        self.assertEqual(it["followups"], ["追問A"])
        self.assertEqual(it["version_count"], 2)
        self.assertFalse(it["stopped"])

    async def test_list_qa_versions_orders_ascending(self):
        from app.services import answer as ans

        rows = [
            ("v1", "答一", [], [], 100, ["understanding"], None, None, True),
            ("v2", "答二", [], [], 120, ["understanding", "generating"], "like", None, False),
        ]

        class _Rows:
            def all(self):
                return rows

        class _CapSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def execute(self, stmt, params=None):
                assert "ORDER BY created_at ASC" in str(stmt)
                return _Rows()

            async def commit(self):
                return None

        orig = ans.SessionFactory
        ans.SessionFactory = lambda: _CapSession()
        try:
            out = await ans.list_qa_versions("root1")
        finally:
            ans.SessionFactory = orig

        self.assertEqual([v["qa_id"] for v in out], ["v1", "v2"])
        self.assertEqual(out[1]["feedback"], "like")
        # 停止的部分答案在版本 pager 要標得出來
        self.assertEqual([v["stopped"] for v in out], [True, False])


class DeleteConversationTests(unittest.IsolatedAsyncioTestCase):
    async def test_rowcount_zero_is_false(self):
        from app.services import answer as ans

        class Res:
            rowcount = 0

        class Sess:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def execute(self, *a, **k):
                return Res()

            async def commit(self):
                return None

        orig = ans.SessionFactory
        ans.SessionFactory = lambda: Sess()
        try:
            ok = await ans.delete_conversation("c1")
        finally:
            ans.SessionFactory = orig
        self.assertFalse(ok)

    async def test_deletes_report_derivatives_leaf_first(self):
        """研報衍生物必須一起刪，且順序是由葉往根。

        三張表（`report_doc` / `report_run` / `report_rendition`）刻意都沒有 FK，
        所以 DB 不會替你連刪；先前只刪 `qa_log` ⇒ 這些列與磁碟 PDF 全變永久孤兒。
        2026-07-30 實測生產 14 列 `report_doc` 有 2 列是孤兒。

        `report_rendition` 以 `report_id` 指向 `report_doc`——先刪 doc 就再也找不到
        要刪哪些 rendition，所以順序不是風格問題。
        """
        from app.services import answer as ans

        class Res:
            rowcount = 1

            def all(self):
                return []

        sqls: list[str] = []

        class Sess:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def execute(self, stmt, params=None):
                sqls.append(str(stmt))
                return Res()

            async def commit(self):
                return None

        orig = ans.SessionFactory
        ans.SessionFactory = lambda: Sess()
        try:
            ok = await ans.delete_conversation("c1")
        finally:
            ans.SessionFactory = orig
        self.assertTrue(ok)
        joined = "\n".join(sqls)
        for table in (
            "research.report_rendition",
            "research.report_doc",
            "research.report_run",
            "research.qa_log",
        ):
            self.assertIn(f"DELETE FROM {table}", joined, f"{table} 沒被刪到")
        i_rend = joined.index("DELETE FROM research.report_rendition")
        i_doc = joined.index("DELETE FROM research.report_doc")
        self.assertLess(i_rend, i_doc, "rendition 必須先於 report_doc（否則查不到要刪哪些）")

    async def test_report_section_relies_on_fk_cascade(self):
        """`report_section` 不直接刪——它以 run_id 對 `report_run` 有 FK CASCADE。

        寫成測試是因為「少一個 DELETE」看起來就像漏掉，下一個人很容易「補上」，
        而那會變成刪兩次（無害但誤導）或在 run 已刪後對不到列。
        """
        from app.services import answer as ans

        class Res:
            rowcount = 1

            def all(self):
                return []

        sqls: list[str] = []

        class Sess:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def execute(self, stmt, params=None):
                sqls.append(str(stmt))
                return Res()

            async def commit(self):
                return None

        orig = ans.SessionFactory
        ans.SessionFactory = lambda: Sess()
        try:
            await ans.delete_conversation("c1")
        finally:
            ans.SessionFactory = orig
        self.assertNotIn("DELETE FROM research.report_section", "\n".join(sqls))


class DeletedPdfPathsTests(unittest.IsolatedAsyncioTestCase):
    """路徑要在刪除**之前**查——列刪掉之後就查不到了。"""

    @staticmethod
    def _session(rows, *, boom=False):
        class Res:
            def all(self):
                return rows

        class Sess:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def execute(self, *a, **k):
                if boom:
                    raise RuntimeError("db down")
                return Res()

        return Sess

    async def test_collects_from_both_tables(self):
        from app.services import answer as ans

        orig = ans.SessionFactory
        ans.SessionFactory = self._session([("/d/a.pdf",), ("/d/b.pdf",)])
        try:
            out = await ans.deleted_pdf_paths("c1")
        finally:
            ans.SessionFactory = orig
        self.assertEqual(out, ["/d/a.pdf", "/d/b.pdf"])

    async def test_null_paths_dropped(self):
        from app.services import answer as ans

        orig = ans.SessionFactory
        ans.SessionFactory = self._session([(None,), ("/d/x.pdf",), ("",)])
        try:
            out = await ans.deleted_pdf_paths("c1")
        finally:
            ans.SessionFactory = orig
        self.assertEqual(out, ["/d/x.pdf"])

    async def test_db_error_returns_empty_not_raise(self):
        """刪對話不該因為「順便查個路徑失敗」而整體失敗。"""
        from app.services import answer as ans

        orig = ans.SessionFactory
        ans.SessionFactory = self._session([], boom=True)
        try:
            out = await ans.deleted_pdf_paths("c1")
        finally:
            ans.SessionFactory = orig
        self.assertEqual(out, [])


class ListConversationsTests(unittest.IsolatedAsyncioTestCase):
    async def test_maps_grouped_rows(self):
        from datetime import datetime, timezone

        from app.services import answer as ans

        last = datetime(2026, 6, 22, 3, 0, tzinfo=timezone.utc)
        # outer SELECT 回 (conv_id, title, last_at, turn_count)
        rows = [("c1", "第一題", last, 2)]
        orig = ans.SessionFactory
        ans.SessionFactory = lambda: _RowsSession(rows)
        try:
            out = await ans.list_conversations()
        finally:
            ans.SessionFactory = orig
        self.assertEqual(out[0]["conversation_id"], "c1")
        self.assertEqual(out[0]["title"], "第一題")
        self.assertEqual(out[0]["turn_count"], 2)
        self.assertEqual(out[0]["last_at"], last.isoformat())

    async def test_sql_uses_first_non_offtopic_question_and_turn_count_filter(self):
        from datetime import datetime, timezone

        from app.services import answer as ans

        last = datetime(2026, 6, 22, 3, 0, tzinfo=timezone.utc)
        session = _CaptureRowsSession([("c1", "第二題", last, 1)])
        orig = ans.SessionFactory
        ans.SessionFactory = lambda: session
        try:
            await ans.list_conversations()
        finally:
            ans.SessionFactory = orig

        sql = " ".join((session.statement_text or "").split())
        self.assertIn(
            "(array_agg(question ORDER BY created_at) FILTER (WHERE COALESCE(answer NOT IN :offtopics, TRUE) AND active))[1] AS title",  # noqa: E501
            sql,
        )
        self.assertIn("WHERE turn_count > 0", sql)
        self.assertNotIn("first_answer NOT IN :offtopics", sql)
        self.assertEqual(session.params["offtopics"], list(ans.OFF_TOPIC_MESSAGES))


class ConversationStaticContractTests(unittest.TestCase):
    def test_schema_uses_expression_index_for_conversation_lookup(self):
        schema = (REPO_ROOT / "db/schema.sql").read_text(encoding="utf-8")
        self.assertRegex(
            " ".join(schema.split()),
            r"CREATE INDEX IF NOT EXISTS idx_qa_log_conversation ON research\.qa_log \(\(COALESCE\(conversation_id, id\)\), created_at\);",  # noqa: E501
        )


class FollowUpTests(unittest.IsolatedAsyncioTestCase):
    async def test_followup_uses_condensed_query_for_retrieval(self):
        import app.services.retrieval_pipeline as rp
        from app.services import answer as ans

        seen = {}

        async def fake_load(conversation_id, **k):
            return [("台積電前景?", "看好[1]")]

        async def fake_condense(history_text, question, **k):
            return ("台積電 2026 先進封裝 展望", sr._decision(sr.CORPUS_QA))

        async def fake_search(session, query, qvec, **k):
            seen["query"] = query
            return [
                (
                    1,
                    0.9,
                    make_row("r1", "x.pdf", "TW", "封裝內容。", date(2026, 6, 1), 0.1),
                )
            ]

        def fake_embed(q):
            seen["embed"] = q
            return [0.0]

        async def fake_stream(*a, **k):
            yield "答案[1]"

        async def fake_route(q, **k):
            raise AssertionError("續問不應呼叫 classify_non_overview")

        orig = (
            rp.hybrid_search,
            rp.embed_query_cached,
            ans.stream_completion,
            rp.SessionFactory,
            ans.SessionFactory,
            ans.classify_non_overview,
            ans.condense_and_route,
            ans.load_recent_turns,
        )
        rp.hybrid_search = fake_search
        rp.embed_query_cached = fake_embed
        ans.stream_completion = fake_stream
        rp.SessionFactory = lambda: _FakeSession()
        ans.SessionFactory = lambda: _FakeSession()
        ans.classify_non_overview = fake_route
        ans.condense_and_route = fake_condense
        ans.load_recent_turns = fake_load
        try:
            events = [
                e
                async for e in ans.answer_question(
                    "那它的封裝呢?", conversation_id="c1"
                )
            ]
        finally:
            (
                rp.hybrid_search,
                rp.embed_query_cached,
                ans.stream_completion,
                rp.SessionFactory,
                ans.SessionFactory,
                ans.classify_non_overview,
                ans.condense_and_route,
                ans.load_recent_turns,
            ) = orig

        self.assertEqual(seen["query"], "台積電 2026 先進封裝 展望")  # 用改寫後查詢檢索
        self.assertEqual(seen["embed"], "台積電 2026 先進封裝 展望")
        self.assertEqual(events[-1][1]["conversation_id"], "c1")  # 沿用傳入對話 id

    async def test_followup_passes_history_block_to_prompt(self):
        import app.services.retrieval_pipeline as rp
        from app.services import answer as ans

        seen = {}

        async def fake_load(conversation_id, **k):
            return [("台積電前景?", "看好[1]")]

        async def fake_condense(history_text, question, **k):
            return ("台積電 先進封裝 展望", sr._decision(sr.CORPUS_QA))

        async def fake_search(session, query, qvec, **k):
            return [
                (
                    1,
                    0.9,
                    make_row("r1", "x.pdf", "TW", "封裝內容。", date(2026, 6, 1), 0.1),
                )
            ]

        def fake_embed(q):
            return [0.0]

        async def fake_stream(*a, **k):
            yield "答案[1]"

        async def fake_route(q, **k):
            return sr._decision(sr.CORPUS_QA)

        orig_bup = ans.build_user_prompt

        def spy_bup(question, context, history_block=""):
            seen["history_block"] = history_block
            return orig_bup(question, context, history_block)

        orig = (
            rp.hybrid_search,
            rp.embed_query_cached,
            ans.stream_completion,
            rp.SessionFactory,
            ans.SessionFactory,
            ans.classify_non_overview,
            ans.condense_and_route,
            ans.load_recent_turns,
            ans.build_user_prompt,
        )
        rp.hybrid_search = fake_search
        rp.embed_query_cached = fake_embed
        ans.stream_completion = fake_stream
        rp.SessionFactory = lambda: _FakeSession()
        ans.SessionFactory = lambda: _FakeSession()
        ans.classify_non_overview = fake_route
        ans.condense_and_route = fake_condense
        ans.load_recent_turns = fake_load
        ans.build_user_prompt = spy_bup
        try:
            _ = [
                e
                async for e in ans.answer_question(
                    "那它的封裝呢?", conversation_id="c1"
                )
            ]
        finally:
            (
                rp.hybrid_search,
                rp.embed_query_cached,
                ans.stream_completion,
                rp.SessionFactory,
                ans.SessionFactory,
                ans.classify_non_overview,
                ans.condense_and_route,
                ans.load_recent_turns,
                ans.build_user_prompt,
            ) = orig

        self.assertIn("history_block", seen)
        self.assertIn("台積電前景?", seen["history_block"])  # 先前對話確實內嵌進 prompt

    async def test_followup_offtopic_skips_llm(self):
        import app.services.retrieval_pipeline as rp
        from app.services import answer as ans

        called = {"llm": False}

        async def fake_load(conversation_id, **k):
            return [("台積電前景?", "看好[1]")]

        async def fake_condense(history_text, question, **k):
            return ("幫我寫一首詩", sr._decision(sr.OFF_TOPIC))  # 改寫後判定離題

        async def fake_search(session, query, qvec, **k):
            return [
                (1, 0.5, make_row("r1", "x.pdf", "TW", "內容。", date(2026, 6, 1), 0.4))
            ]

        def fake_embed(q):
            return [0.0]

        async def fake_stream(*a, **k):
            called["llm"] = True
            yield "不該被呼叫"

        async def fake_route(q, **k):
            raise AssertionError("續問不應呼叫 classify_non_overview")

        orig = (
            rp.hybrid_search,
            rp.embed_query_cached,
            ans.stream_completion,
            rp.SessionFactory,
            ans.SessionFactory,
            ans.classify_non_overview,
            ans.condense_and_route,
            ans.load_recent_turns,
        )
        rp.hybrid_search = fake_search
        rp.embed_query_cached = fake_embed
        ans.stream_completion = fake_stream
        rp.SessionFactory = lambda: _FakeSession()
        ans.SessionFactory = lambda: _FakeSession()
        ans.classify_non_overview = fake_route
        ans.condense_and_route = fake_condense
        ans.load_recent_turns = fake_load
        try:
            events = [
                e async for e in ans.answer_question("再來一首?", conversation_id="c1")
            ]
        finally:
            (
                rp.hybrid_search,
                rp.embed_query_cached,
                ans.stream_completion,
                rp.SessionFactory,
                ans.SessionFactory,
                ans.classify_non_overview,
                ans.condense_and_route,
                ans.load_recent_turns,
            ) = orig

        kinds = [k for k, _ in events]
        self.assertEqual(kinds, ["status", "sources", "notice", "done"])  # 離題提示卡
        self.assertEqual(events[0], ("status", {"stage": "understanding"}))
        self.assertEqual(events[2][1], ans.OFF_TOPIC_MESSAGE)
        self.assertEqual(events[-1][1]["conversation_id"], "c1")
        self.assertFalse(called["llm"])  # 未跑主 LLM


class HistoryItemThinkingTests(unittest.TestCase):
    def test_history_item_includes_thinking_ms(self):
        from app.services.answer import history_item

        row = ("id1", "q", "a", "2026-06-22T00:00:00+00:00", None, [], [], 1234)
        item = history_item(row)
        self.assertEqual(item["thinking_ms"], 1234)

    def test_history_item_thinking_ms_none_for_old_rows(self):
        from app.services.answer import history_item

        # 舊列（7 欄，無 thinking_ms）→ 回 None，不報錯
        row = ("id1", "q", "a", "2026-06-22T00:00:00+00:00", None, [], [])
        item = history_item(row)
        self.assertIsNone(item["thinking_ms"])

    def test_history_item_thinking_ms_none_for_legacy_6col_rows(self):
        from app.services.answer import history_item

        # 最舊列（6 欄，無 ext_sources、無 thinking_ms）→ 兩者皆安全預設
        row = ("id1", "q", "a", "2026-06-22T00:00:00+00:00", None, [])
        item = history_item(row)
        self.assertIsNone(item["thinking_ms"])
        self.assertEqual(item["ext_sources"], [])


class LogQaColumnsTests(unittest.IsolatedAsyncioTestCase):
    """_log_qa 寫入新欄 root_qa_id/stages/followups/active。"""

    async def test_log_qa_writes_new_columns(self):
        from app.services import answer as ans

        captured = {}

        class _CapSession:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def execute(self, stmt, params=None):
                captured["sql"] = str(stmt)
                captured["params"] = params
                return None
            async def commit(self): return None

        orig = ans.SessionFactory
        ans.SessionFactory = lambda: _CapSession()
        try:
            qa_id = await ans._log_qa(
                "問題", "答案", [], {}, 10, [], [],
                conversation_id="c1", thinking_ms=5,
                root_qa_id="root1", stages=["understanding", "generating"],
                followups=["追問A", "追問B"],
            )
        finally:
            ans.SessionFactory = orig

        self.assertTrue(qa_id)
        self.assertIn("root_qa_id", captured["sql"])
        self.assertIn("stages", captured["sql"])
        self.assertIn("followups", captured["sql"])
        self.assertEqual(captured["params"]["root"], "root1")
        self.assertEqual(json.loads(captured["params"]["stages"]),
                         ["understanding", "generating"])
        self.assertEqual(json.loads(captured["params"]["followups"]),
                         ["追問A", "追問B"])

    async def test_log_qa_new_columns_default_none(self):
        from app.services import answer as ans

        captured = {}

        class _CapSession:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def execute(self, stmt, params=None):
                captured["params"] = params
                return None
            async def commit(self): return None

        orig = ans.SessionFactory
        ans.SessionFactory = lambda: _CapSession()
        try:
            await ans._log_qa("q", "a", [], {}, 1, [], [])
        finally:
            ans.SessionFactory = orig

        self.assertIsNone(captured["params"]["root"])
        self.assertIsNone(captured["params"]["stages"])
        self.assertIsNone(captured["params"]["followups"])


class StagesPersistTests(unittest.IsolatedAsyncioTestCase):
    """主 RAG 路徑把經過的 stage 序列寫入 _log_qa 的 stages 參數。"""

    async def test_main_path_persists_stages(self):
        import app.services.retrieval_pipeline as rp
        from app.services import answer as ans

        logged = {}

        async def fake_log(*a, **k):
            logged.update(k)
            return "qa-1"

        async def fake_search(*a, **k):
            return [(0, 0.80, make_row("r1", "x.pdf", "TW", "內容[1]。", date(2026, 6, 1)))]

        async def fake_stream(*a, **k):
            yield "答案[1]"

        async def fake_route(question, **k):
            return sr._decision(sr.CORPUS_QA)

        orig = (rp.hybrid_search, rp.embed_query_cached, ans.stream_completion,
                rp.SessionFactory, ans.SessionFactory, ans.classify_non_overview, ans._log_qa)
        rp.hybrid_search = fake_search
        rp.embed_query_cached = lambda q: [0.0]
        ans.stream_completion = fake_stream
        rp.SessionFactory = lambda: _FakeSession()
        ans.SessionFactory = lambda: _FakeSession()
        ans.classify_non_overview = fake_route
        ans._log_qa = fake_log
        try:
            _ = [e async for e in ans.answer_question("台積電展望")]
        finally:
            (rp.hybrid_search, rp.embed_query_cached, ans.stream_completion,
             rp.SessionFactory, ans.SessionFactory, ans.classify_non_overview, ans._log_qa) = orig

        self.assertIn("stages", logged)
        self.assertEqual(logged["stages"][0], "understanding")
        self.assertIn("retrieved", logged["stages"])
        self.assertIn("generating", logged["stages"])


class LlmFailureStillLogsTests(unittest.IsolatedAsyncioTestCase):
    """LLM 掛掉時那一輪**仍然要落庫**——這是本修正的核心不變量。

    `_log_qa` 在串流之後，所以在此之前拋例外等於該輪完全不進 `qa_log`：使用者看到
    「問答服務發生錯誤」，而監控端**分不出 API 529 過載與程式 bug**（兩者都是什麼
    紀錄都沒有）。2026-07-30 生產有一筆這樣的失敗，事後只能從 journald 猜。

    例外仍必須往外拋（router 的錯誤處理與 SSE error 事件靠它），落庫是**額外**做的
    事而不是取代——所以這裡同時斷言「有寫」與「有拋」。
    """

    @staticmethod
    def _patch(logged, *, exc):
        from app.services import answer as ans
        from app.services import retrieval_pipeline as rp
        from app.services import scope_router as sr

        async def boom(*a, **k):
            raise exc
            yield ""  # pragma: no cover - 讓它成為 async generator

        async def fake_route(question, **k):
            return sr._decision(sr.CORPUS_QA)

        async def fake_log(question, answer, cited, filters, latency, sources, *a, **kw):
            logged.append({"answer": answer, "filters": filters, "kwargs": kw})
            return "qa-1"

        orig = (
            rp.hybrid_search, rp.embed_query_cached, ans.stream_completion,
            rp.SessionFactory, ans.SessionFactory, ans.classify_non_overview,
            ans._log_qa,
        )

        async def some_hits(session, q, qvec, **k):
            # **必須有命中**：零命中時 answer_question 走「找不到相關研報」的短路，
            # 根本不會呼叫 LLM，於是這條測試會以「例外沒被拋出」失敗——那是測試
            # 沒走到待驗路徑，不是修正沒生效。
            return [(0, 0.80, make_row("r1", "x.pdf", "TW", "內容。", date(2026, 6, 1)))]

        rp.hybrid_search = some_hits
        rp.embed_query_cached = lambda q: [0.0]
        ans.stream_completion = boom
        rp.SessionFactory = lambda: _FakeSession()
        ans.SessionFactory = lambda: _FakeSession()
        ans.classify_non_overview = fake_route
        ans._log_qa = fake_log
        return orig

    @staticmethod
    def _restore(orig):
        from app.services import answer as ans
        from app.services import retrieval_pipeline as rp

        (
            rp.hybrid_search, rp.embed_query_cached, ans.stream_completion,
            rp.SessionFactory, ans.SessionFactory, ans.classify_non_overview,
            ans._log_qa,
        ) = orig

    async def test_overloaded_failure_writes_row_and_reraises(self):
        from app.services import answer as ans
        from app.services.llm import LLMUnavailableError

        logged: list[dict] = []
        orig = self._patch(logged, exc=LLMUnavailableError("529 Overloaded"))
        try:
            with self.assertRaises(LLMUnavailableError):
                _ = [e async for e in ans.answer_question("台積電展望")]
        finally:
            self._restore(orig)

        self.assertEqual(len(logged), 1, "LLM 失敗那輪沒有落庫")
        row = logged[0]
        self.assertIsNone(row["answer"], "不得寫假答案——NULL 才能讓讀取路徑自然跳過")
        self.assertEqual(row["filters"]["llm_error"], "overloaded")
        self.assertIs(row["kwargs"]["active"], False)

    async def test_non_overload_failure_classified_as_other(self):
        from app.services import answer as ans
        from app.services.llm import LLMUnavailableError

        logged: list[dict] = []
        orig = self._patch(logged, exc=LLMUnavailableError("claude 無有效回應"))
        try:
            with self.assertRaises(LLMUnavailableError):
                _ = [e async for e in ans.answer_question("台積電展望")]
        finally:
            self._restore(orig)
        self.assertEqual(logged[0]["filters"]["llm_error"], "other")

    async def test_route_telemetry_survives_into_the_failure_row(self):
        """`path`/`decided_by` 也要留著——否則失敗列答不出「這題走了哪條路」。"""
        from app.services import answer as ans
        from app.services.llm import LLMUnavailableError

        logged: list[dict] = []
        orig = self._patch(logged, exc=LLMUnavailableError("529 Overloaded"))
        try:
            with self.assertRaises(LLMUnavailableError):
                _ = [e async for e in ans.answer_question("台積電展望")]
        finally:
            self._restore(orig)
        self.assertIn("path", logged[0]["filters"])

    async def test_regenerate_failure_keeps_old_row_active(self):
        """重生撞上 LLM 失敗：失敗列不得停用舊版列、不得截斷後續輪次。

        docstring 的契約是「新列**成功**寫入時才停用舊列／截斷」。失敗列是
        active=false 的遙測列，照常帶 deactivate_qa_id 會把使用者原本的答案從
        對話歷史藏掉（症狀靜默：重整後那一輪整個消失）。
        """
        from app.services import answer as ans
        from app.services.llm import LLMUnavailableError

        old_id = "00000000-0000-4000-8000-000000000001"
        logged: list[dict] = []
        orig = self._patch(logged, exc=LLMUnavailableError("529 Overloaded"))

        async def meta(qid):
            return (None, "conv-1", None)

        orig_meta = ans._load_qa_meta
        ans._load_qa_meta = meta
        try:
            with self.assertRaises(LLMUnavailableError):
                _ = [e async for e in ans.answer_question(
                    "台積電展望", regenerate_of=old_id)]
        finally:
            ans._load_qa_meta = orig_meta
            self._restore(orig)

        self.assertEqual(len(logged), 1)
        kw = logged[0]["kwargs"]
        self.assertIsNone(kw.get("deactivate_qa_id"), "失敗列不得停用舊版列")
        self.assertIsNone(kw.get("truncate_from"), "失敗列不得截斷後續輪次")
        # 版本鏈 metadata 仍要保留：監控端才對得回這次失敗屬於哪一組重生
        self.assertEqual(kw.get("root_qa_id"), old_id)


class LogQaActiveFlagTests(unittest.IsolatedAsyncioTestCase):
    """`_log_qa(active=...)` 必須真的 bind 出去，不是寫死 true。

    這是 LLM 失敗落庫的關鍵——`active=false` 讓四條使用者面讀取路徑中的三條自動
    跳過那一列（condense 脈絡、`/api/history`、`list_conversations` 的 FILTER）。
    寫死 true 的話，失敗那輪會以一則**空答案**出現在使用者的側欄裡。
    """

    @staticmethod
    def _cap(captured):
        class _CapSession:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def execute(self, stmt, params=None):
                captured.setdefault("sqls", []).append(str(stmt))
                captured["params"] = params
                return None
            async def commit(self): return None
        return _CapSession

    async def test_active_defaults_true(self):
        from app.services import answer as ans

        cap = {}
        orig = ans.SessionFactory
        ans.SessionFactory = self._cap(cap)
        try:
            await ans._log_qa("q", "a", [], {}, 1, [])
        finally:
            ans.SessionFactory = orig
        self.assertIs(cap["params"]["active"], True)

    async def test_active_false_is_bound_not_hardcoded(self):
        from app.services import answer as ans

        cap = {}
        orig = ans.SessionFactory
        ans.SessionFactory = self._cap(cap)
        try:
            await ans._log_qa("q", None, [], {"llm_error": "overloaded"}, 1, [], active=False)
        finally:
            ans.SessionFactory = orig
        self.assertIs(cap["params"]["active"], False)
        # SQL 不得再把 active 寫成字面 true
        self.assertNotIn(":root, true,", cap["sqls"][0])
        self.assertIn(":active", cap["sqls"][0])

    async def test_null_answer_is_accepted(self):
        """`answer=None` 要能寫進去——schema 的 `answer text` 允許 NULL。"""
        from app.services import answer as ans

        cap = {}
        orig = ans.SessionFactory
        ans.SessionFactory = self._cap(cap)
        try:
            qa_id = await ans._log_qa("q", None, [], {}, 1, [], active=False)
        finally:
            ans.SessionFactory = orig
        self.assertTrue(qa_id)
        self.assertIsNone(cap["params"]["a"])


class LogQaTruncateExcludesSelfTests(unittest.IsolatedAsyncioTestCase):
    """truncate_from 的 UPDATE 必須排除同交易剛插入的新列自己。

    INSERT 在前、截斷在後：新列的 created_at（transaction 起始時刻）必然 >= 被
    編輯列的時刻，同一交易又讀得到自己剛寫的列——不排除的話，每一次編輯重問都
    把新答案標成 inactive，重整後編輯點之後整段對話消失。2026-08-01 以 probe 對
    真實 DB 實證過（先前測試只驗 truncate_from 有被轉傳，驗不到 SQL 語意）。
    """

    @staticmethod
    def _cap(executed):
        class _CapSession:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def execute(self, stmt, params=None):
                executed.append((str(stmt), params))
                return None
            async def commit(self): return None
        return _CapSession

    async def test_log_qa_truncate_excludes_new_row(self):
        from app.services import answer as ans

        executed: list[tuple[str, dict]] = []
        orig = ans.SessionFactory
        ans.SessionFactory = self._cap(executed)
        try:
            qa_id = await ans._log_qa(
                "編輯後問題", "編輯後答案", [], {}, 1, [],
                conversation_id="c1", truncate_from=("c1", "TS"),
            )
        finally:
            ans.SessionFactory = orig

        truncs = [(s, p) for s, p in executed if "created_at >= :ts" in s]
        self.assertEqual(len(truncs), 1, "缺 truncate UPDATE")
        sql, params = truncs[0]
        self.assertIn("id <> :self", sql, "truncate 沒有排除新列自己")
        self.assertEqual(params["self"], qa_id)

    async def test_log_stopped_edit_of_truncates_and_excludes_self(self):
        """編輯途中停止：停止列落庫時要做與完成路徑相同的截斷（同樣排除自己）。"""
        from app.services import answer as ans

        executed: list[tuple[str, dict]] = []

        async def meta(qid):
            return (None, "c1", "TS")

        orig = (ans.SessionFactory, ans._load_qa_meta)
        ans.SessionFactory = self._cap(executed)
        ans._load_qa_meta = meta
        try:
            qa_id = await ans.log_stopped_qa(
                "編輯後問題", "部分答案", edit_of="edited-1",
            )
        finally:
            (ans.SessionFactory, ans._load_qa_meta) = orig

        truncs = [(s, p) for s, p in executed if "created_at >= :ts" in s]
        self.assertEqual(len(truncs), 1, "edit_of 停止未觸發截斷")
        sql, params = truncs[0]
        self.assertIn("id <> :self", sql)
        self.assertEqual(params["self"], qa_id)
        self.assertEqual(params["cid"], "c1")
        self.assertEqual(params["ts"], "TS")
        # 編輯是全新輪次、不進版本群組；也不得誤發 regenerate 的單列停用
        self.assertFalse([s for s, _ in executed
                          if "SET active = false WHERE id = :id" in s])


class LlmErrorKindTests(unittest.TestCase):
    """`filters.llm_error` 要回答的問題只有一個：Anthropic 過載，還是我們的 bug。

    先前兩者在監控上**完全無法區分**——LLM 失敗那輪根本不落庫（`_log_qa` 在串流
    之後），所以兩種情況都是「什麼紀錄都沒有」。
    """

    def test_overload_detected(self):
        from app.services import answer as ans
        from app.services.llm import LLMUnavailableError

        for msg in ("529 Overloaded", "API Error: 529 {\"type\":\"overloaded_error\"}"):
            with self.subTest(msg=msg):
                self.assertEqual(
                    ans._llm_error_kind(LLMUnavailableError(msg)), "overloaded"
                )

    def test_other_is_the_fallback_not_overloaded(self):
        """認不出來要落到 other，不能猜成 overloaded。

        猜成過載＝把我們自己的 bug 記成「上游的問題」，那比沒有分類更糟。
        """
        from app.services import answer as ans
        from app.services.llm import LLMUnavailableError

        for msg in ("claude 無有效回應", "", "FileNotFoundError: 'claude'"):
            with self.subTest(msg=msg):
                self.assertEqual(ans._llm_error_kind(LLMUnavailableError(msg)), "other")


class QaVersionsExcludesFailedTurnsTests(unittest.TestCase):
    """`list_qa_versions` 是唯一沒有 active 過濾的使用者面路徑，要自己擋 NULL 答案。

    版本 pager 刻意顯示所有版本（含被取代的），所以不能靠 `active`。走原始碼靜態
    驗：這條 SQL 沒有可注入的 fake，而要驗的就是一個子句在不在。
    """

    def test_query_filters_null_answer(self):
        import inspect

        from app.services import answer as ans

        src = inspect.getsource(ans.list_qa_versions)
        self.assertIn("answer IS NOT NULL", src)
        # 必須是 AND 而非第二個 WHERE（兩個 WHERE 是語法錯誤）
        self.assertEqual(src.count("WHERE"), 1, "只該有一個 WHERE")


class StopLogTests(unittest.IsolatedAsyncioTestCase):
    """log_stopped_qa 寫入 stopped=true 的部分答案列；regenerate_of 解析 root_qa_id。"""

    async def test_log_stopped_writes_stopped_true(self):
        from app.services import answer as ans

        captured = {}

        class _CapSession:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def execute(self, stmt, params=None):
                captured["sql"] = str(stmt)
                captured["params"] = params
                return None
            async def commit(self): return None

        orig = (ans.SessionFactory, ans._load_qa_meta)
        ans.SessionFactory = lambda: _CapSession()

        async def no_meta(qid): return None
        ans._load_qa_meta = no_meta
        try:
            qa_id = await ans.log_stopped_qa(
                "問題", "部分答", conversation_id="c1",
                sources=[{"n": 1}], stages=["understanding", "generating"],
            )
        finally:
            (ans.SessionFactory, ans._load_qa_meta) = orig

        self.assertTrue(qa_id)
        self.assertIn("stopped", captured["sql"])
        self.assertEqual(captured["params"]["conv"], "c1")
        self.assertIsNone(captured["params"]["root"])  # 無 regenerate_of

    async def test_log_stopped_resolves_root_from_regenerate_of(self):
        from app.services import answer as ans

        executed = []

        class _CapSession:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def execute(self, stmt, params=None):
                executed.append((str(stmt), params))
                return None
            async def commit(self): return None

        async def meta(qid):
            return ("root-x", "c1", None)  # 舊列已有 root

        orig = (ans.SessionFactory, ans._load_qa_meta)
        ans.SessionFactory = lambda: _CapSession()
        ans._load_qa_meta = meta
        try:
            await ans.log_stopped_qa("q", "部分", regenerate_of="old-1")
        finally:
            (ans.SessionFactory, ans._load_qa_meta) = orig

        # 停止列落庫後另有一發停用舊版列的 UPDATE，故只看 INSERT 那一發的參數
        insert_params = next(p for s, p in executed if "INSERT" in s)
        self.assertEqual(insert_params["root"], "root-x")

    async def test_log_stopped_deactivates_old_version_row(self):
        """停止重生時舊版列要在同一交易內停用（與 `_log_qa` 的 deactivate 對稱）。

        少了這一步，停止列與舊版列同時 active，`get_conversation` 只濾 `q.active`，
        重整後同一問題會出現兩輪——2026-08-01 對生產的端對端實測重現過。
        """
        from app.services import answer as ans

        executed = []

        class _CapSession:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def execute(self, stmt, params=None):
                executed.append((str(stmt), params))
                return None
            async def commit(self): return None

        async def meta(qid):
            return (None, "c1", None)  # 舊列無 root → 以自身為群組鍵

        orig = (ans.SessionFactory, ans._load_qa_meta)
        ans.SessionFactory = lambda: _CapSession()
        ans._load_qa_meta = meta
        try:
            await ans.log_stopped_qa("q", "部分", regenerate_of="old-1")
        finally:
            (ans.SessionFactory, ans._load_qa_meta) = orig

        deacts = [(s, p) for s, p in executed if "active = false" in s]
        self.assertEqual(len(deacts), 1, "缺少舊版列停用（UPDATE active=false）")
        self.assertEqual(deacts[0][1]["id"], "old-1")

    async def test_log_stopped_without_regenerate_does_not_deactivate(self):
        """一般停止（非重生）沒有舊版列可停用，不得多發 UPDATE。"""
        from app.services import answer as ans

        executed = []

        class _CapSession:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def execute(self, stmt, params=None):
                executed.append(str(stmt))
                return None
            async def commit(self): return None

        orig = ans.SessionFactory
        ans.SessionFactory = lambda: _CapSession()
        try:
            await ans.log_stopped_qa("q", "部分", conversation_id="c1")
        finally:
            ans.SessionFactory = orig

        self.assertFalse([s for s in executed if "active = false" in s])

    async def test_log_stopped_reuses_existing_row_for_same_request_id(self):
        from app.services import answer as ans

        captured = {}

        class _Result:
            def scalar_one(self): return "existing-qa"

        class _CapSession:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def execute(self, stmt, params=None):
                captured["sql"] = str(stmt)
                captured["params"] = params
                return _Result()
            async def commit(self): return None

        orig = ans.SessionFactory
        ans.SessionFactory = lambda: _CapSession()
        try:
            qa_id = await ans.log_stopped_qa(
                "問題", "部分答", request_id="123e4567-e89b-42d3-a456-426614174000",
            )
        finally:
            ans.SessionFactory = orig

        self.assertEqual(qa_id, "existing-qa")
        self.assertIn("ON CONFLICT (request_id)", captured["sql"])
        self.assertEqual(captured["params"]["request_id"], "123e4567-e89b-42d3-a456-426614174000")


class FollowupsEmitTests(unittest.IsolatedAsyncioTestCase):
    """主 RAG 路徑在 done 之後補發 followups 事件（非空才發，fail-open 不擋主答）。"""

    async def test_followups_event_after_done(self):
        import app.services.retrieval_pipeline as rp
        from app.services import answer as ans

        async def fake_search(*a, **k):
            return [(0, 0.80, make_row("r1", "x.pdf", "TW", "內容[1]。", date(2026, 6, 1)))]

        async def fake_stream(*a, **k):
            yield "答案[1]"

        async def fake_route(question, **k):
            return sr._decision(sr.CORPUS_QA)

        async def fake_followups(q, a, **k):
            return ["追問一", "追問二"]

        orig = (rp.hybrid_search, rp.embed_query_cached, ans.stream_completion,
                rp.SessionFactory, ans.SessionFactory, ans.classify_non_overview,
                ans.generate_followups)
        rp.hybrid_search = fake_search
        rp.embed_query_cached = lambda q: [0.0]
        ans.stream_completion = fake_stream
        rp.SessionFactory = lambda: _FakeSession()
        ans.SessionFactory = lambda: _FakeSession()
        ans.classify_non_overview = fake_route
        ans.generate_followups = fake_followups
        try:
            events = [e async for e in ans.answer_question("台積電展望")]
        finally:
            (rp.hybrid_search, rp.embed_query_cached, ans.stream_completion,
             rp.SessionFactory, ans.SessionFactory, ans.classify_non_overview,
             ans.generate_followups) = orig

        kinds = [k for k, _ in events]
        self.assertIn("done", kinds)
        self.assertIn("followups", kinds)
        # followups 在 done 之後
        self.assertGreater(kinds.index("followups"), kinds.index("done"))
        fu_payload = next(p for k, p in events if k == "followups")
        self.assertEqual(fu_payload, ["追問一", "追問二"])

    async def test_empty_followups_not_emitted(self):
        import app.services.retrieval_pipeline as rp
        from app.services import answer as ans

        async def fake_search(*a, **k):
            return [(0, 0.80, make_row("r1", "x.pdf", "TW", "內容[1]。", date(2026, 6, 1)))]

        async def fake_stream(*a, **k):
            yield "答案[1]"

        async def fake_route(q, **k): return sr._decision(sr.CORPUS_QA)

        async def empty_followups(q, a, **k): return []

        orig = (rp.hybrid_search, rp.embed_query_cached, ans.stream_completion,
                rp.SessionFactory, ans.SessionFactory, ans.classify_non_overview,
                ans.generate_followups)
        rp.hybrid_search = fake_search
        rp.embed_query_cached = lambda q: [0.0]
        ans.stream_completion = fake_stream
        rp.SessionFactory = lambda: _FakeSession()
        ans.SessionFactory = lambda: _FakeSession()
        ans.classify_non_overview = fake_route
        ans.generate_followups = empty_followups
        try:
            events = [e async for e in ans.answer_question("台積電展望")]
        finally:
            (rp.hybrid_search, rp.embed_query_cached, ans.stream_completion,
             rp.SessionFactory, ans.SessionFactory, ans.classify_non_overview,
             ans.generate_followups) = orig
        self.assertNotIn("followups", [k for k, _ in events])


class RegenerateTests(unittest.IsolatedAsyncioTestCase):
    async def test_regenerate_failure_does_not_deactivate_old_answer(self):
        import app.services.retrieval_pipeline as rp
        from app.services import answer as ans

        state = {"logged": False}

        async def fake_meta(qid): return (None, "c1", None)
        async def failed_search(*a, **k): raise RuntimeError("retrieval failed")
        async def fake_route(q, **k): return sr._decision(sr.CORPUS_QA)
        async def fake_log(*a, **k):
            state["logged"] = True
            return "new-qa"

        orig = (rp.hybrid_search, rp.embed_query_cached, ans._load_qa_meta,
                ans.classify_non_overview, ans._log_qa)
        rp.hybrid_search = failed_search
        rp.embed_query_cached = lambda q: [0.0]
        ans._load_qa_meta = fake_meta
        ans.classify_non_overview = fake_route
        ans._log_qa = fake_log
        try:
            with self.assertRaises(RuntimeError):
                _ = [event async for event in ans.answer_question("台積電展望", regenerate_of="old-1")]
        finally:
            (rp.hybrid_search, rp.embed_query_cached, ans._load_qa_meta,
             ans.classify_non_overview, ans._log_qa) = orig

        self.assertFalse(state["logged"])

    async def test_regenerate_deactivates_old_and_groups(self):
        import app.services.retrieval_pipeline as rp
        from app.services import answer as ans

        state = {"logged_root": "unset", "deactivate": None}

        async def fake_meta(qid):
            return (None, "c1", None)  # 舊列無 root → 群組=舊 id

        async def fake_count(gk):
            return 2

        async def fake_log(*a, **k):
            state["logged_root"] = k.get("root_qa_id")
            state["deactivate"] = k.get("deactivate_qa_id")
            return "new-qa"

        async def fake_search(*a, **k):
            return [(0, 0.80, make_row("r1", "x.pdf", "TW", "內容[1]。", date(2026, 6, 1)))]

        async def fake_stream(*a, **k):
            yield "新答案[1]"

        async def fake_route(q, **k): return sr._decision(sr.CORPUS_QA)
        async def no_followups(q, a, **k): return []

        orig = (rp.hybrid_search, rp.embed_query_cached, ans.stream_completion,
                rp.SessionFactory, ans.SessionFactory, ans.classify_non_overview,
                ans._load_qa_meta, ans._count_versions,
                ans._log_qa, ans.generate_followups, ans.ASK_RERANK_TOP_M)
        rp.hybrid_search = fake_search
        rp.embed_query_cached = lambda q: [0.0]
        ans.stream_completion = fake_stream
        rp.SessionFactory = lambda: _FakeSession()
        ans.SessionFactory = lambda: _FakeSession()
        ans.classify_non_overview = fake_route
        ans._load_qa_meta = fake_meta
        ans._count_versions = fake_count
        ans._log_qa = fake_log
        ans.generate_followups = no_followups
        ans.ASK_RERANK_TOP_M = 0
        try:
            events = [e async for e in ans.answer_question(
                "台積電展望", regenerate_of="old-1")]
        finally:
            (rp.hybrid_search, rp.embed_query_cached, ans.stream_completion,
             rp.SessionFactory, ans.SessionFactory, ans.classify_non_overview,
             ans._load_qa_meta, ans._count_versions,
             ans._log_qa, ans.generate_followups, ans.ASK_RERANK_TOP_M) = orig

        self.assertEqual(state["deactivate"], "old-1")
        self.assertEqual(state["logged_root"], "old-1")  # 群組鍵=舊 id
        done = next(p for k, p in events if k == "done")
        self.assertEqual(done["version_count"], 2)
        self.assertEqual(done["root_qa_id"], "old-1")


class EditResubmitTests(unittest.IsolatedAsyncioTestCase):
    async def test_edit_truncates_from_edited_turn(self):
        import app.services.retrieval_pipeline as rp
        from app.services import answer as ans

        state = {"truncated": None}

        async def fake_meta(qid):
            return (None, "c1", "TS")  # created_at 標記

        async def fake_search(*a, **k):
            return [(0, 0.80, make_row("r1", "x.pdf", "TW", "內容[1]。", date(2026, 6, 1)))]

        async def fake_stream(*a, **k):
            yield "編輯後答案[1]"

        async def fake_route(q, **k): return sr._decision(sr.CORPUS_QA)
        async def no_followups(q, a, **k): return []
        async def fake_log(*a, **k):
            state["truncated"] = k.get("truncate_from")
            return "new-qa"

        orig = (rp.hybrid_search, rp.embed_query_cached, ans.stream_completion,
                rp.SessionFactory, ans.SessionFactory, ans.classify_non_overview,
                ans._load_qa_meta, ans._log_qa, ans.generate_followups,
                ans.ASK_RERANK_TOP_M)
        rp.hybrid_search = fake_search
        rp.embed_query_cached = lambda q: [0.0]
        ans.stream_completion = fake_stream
        rp.SessionFactory = lambda: _FakeSession()
        ans.SessionFactory = lambda: _FakeSession()
        ans.classify_non_overview = fake_route
        ans._load_qa_meta = fake_meta
        ans._log_qa = fake_log
        ans.generate_followups = no_followups
        ans.ASK_RERANK_TOP_M = 0
        try:
            events = [e async for e in ans.answer_question(
                "台積電最新展望", edit_of="turn-2")]
        finally:
            (rp.hybrid_search, rp.embed_query_cached, ans.stream_completion,
             rp.SessionFactory, ans.SessionFactory, ans.classify_non_overview,
             ans._load_qa_meta, ans._log_qa, ans.generate_followups,
             ans.ASK_RERANK_TOP_M) = orig

        self.assertEqual(state["truncated"], ("c1", "TS"))
        self.assertIn("done", [k for k, _ in events])


class AgenticDisabledFuseTests(unittest.IsolatedAsyncioTestCase):
    """M5 保險絲：qa_agentic_enabled=0 時主 RAG 事件序與既有（M4）完全一致，
    且 planner 零呼叫（回退開關保證；agentic 行為測試在 tests/test_agentic_qa.py）。"""

    async def test_disabled_agentic_keeps_baseline_event_sequence(self):
        import dataclasses
        from unittest.mock import patch

        import app.services.query_planner as qp
        import app.services.retrieval_pipeline as rp
        from app.config import get_settings
        from app.services import answer as ans

        stub_settings = dataclasses.replace(get_settings(), qa_agentic_enabled=False)
        plan_calls = []

        async def fake_plan(question, **kwargs):
            plan_calls.append(question)
            return qp.QueryPlan(
                (qp.SubQuery(text=question),), profile="qa", degraded=True
            )

        async def fake_retrieve(question, **kwargs):
            row = make_row("r1", "x.pdf", "TW", "內容。", date(2026, 6, 1))
            from app.services.answer import build_context

            return build_context(
                [(0, 0.80, row)],
                now=datetime(2026, 6, 24, tzinfo=timezone.utc),
            )

        async def fake_stream(*a, **k):
            yield "答案[1]"

        async def fake_route(question, **k):
            return sr._decision(sr.CORPUS_QA)

        with (
            patch.object(ans, "get_settings", lambda: stub_settings),
            patch.object(qp, "plan_queries", fake_plan),
            patch.object(rp, "retrieve_context", fake_retrieve),
            patch.object(ans, "SessionFactory", lambda: _FakeSession()),
            patch.object(ans, "stream_completion", fake_stream),
            patch.object(ans, "classify_non_overview", fake_route),
        ):
            events = [e async for e in ans.answer_question("台積電展望")]

        self.assertEqual(plan_calls, [])  # 關閉時不建 plan_task
        kinds = [k for k, _ in events]
        self.assertEqual(
            kinds,
            ["status", "sources", "status", "status", "status", "token",
             "ext_sources", "done"],
        )
        stages = [p["stage"] for k, p in events if k == "status"]
        self.assertEqual(
            stages, ["understanding", "retrieved", "reading", "generating"]
        )
        self.assertEqual(events[-1][1]["cited"], ["r1"])


if __name__ == "__main__":
    unittest.main()
