# tests/test_answer.py
import sys
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services import llm  # noqa: E402
from app.services.answer import (  # noqa: E402
    Source,
    build_context,
    build_user_prompt,
    cited_report_ids,
    is_off_topic,
)


def make_row(report_id, file_name, market, content, report_date=None, distance=0.1):
    """造一列符合 hybrid_search 回傳結構的 row（只填會被讀到的位置）。"""
    row = [None] * 16
    row[1] = report_id  # _RID
    row[2] = file_name  # _FNAME
    row[3] = market  # _MARKET
    row[6] = report_date  # _RDATE
    row[14] = content  # _CONTENT
    row[15] = distance  # distance（row[-1]）
    return tuple(row)


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
        self.assertIsNone(llm.extract_text_delta('{"type":"stream_event","event":"oops"}'))
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


class BuildContextTests(unittest.TestCase):
    def test_numbers_reports_in_order(self):
        scored = [
            (2, 0.9, make_row("r1", "甲報告.pdf", "TW", "台積電先進封裝需求強勁。", date(2026, 6, 1))),
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
            (0, 0.5, make_row(f"r{i}", f"{i}.pdf", "TW", f"內容{i}。")) for i in range(10)
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


class OffTopicTests(unittest.TestCase):
    def test_empty_scored_is_off_topic(self):
        self.assertTrue(is_off_topic([]))

    def test_lexical_hit_never_off_topic(self):
        # tier>=1（字面命中）即視為在領域內，縱使 dense 很低
        scored = [(1, 0.20, make_row("r1", "甲.pdf", "TW", "內容。", distance=0.95))]
        self.assertFalse(is_off_topic(scored, min_relevance=0.45))

    def test_high_dense_not_off_topic(self):
        # tier0 但最相似塊 cosine=0.6 >= 門檻
        scored = [(0, 0.60, make_row("r1", "甲.pdf", "TW", "內容。", distance=0.40))]
        self.assertFalse(is_off_topic(scored, min_relevance=0.45))

    def test_low_dense_is_off_topic(self):
        # tier0 且最相似塊 cosine=0.30 < 門檻
        scored = [(0, 0.30, make_row("r1", "甲.pdf", "TW", "內容。", distance=0.70))]
        self.assertTrue(is_off_topic(scored, min_relevance=0.45))

    def test_uses_max_dense_across_candidates(self):
        # 取全候選最相似塊：第二列 cosine=0.55 >= 門檻 → 非離題
        scored = [
            (0, 0.30, make_row("r1", "甲.pdf", "TW", "內容。", distance=0.70)),
            (0, 0.55, make_row("r2", "乙.pdf", "TW", "內容。", distance=0.45)),
        ]
        self.assertFalse(is_off_topic(scored, min_relevance=0.45))


class RecencyTests(unittest.TestCase):
    NOW = datetime(2026, 6, 17, tzinfo=timezone.utc)

    def test_newer_report_ranked_first_when_relevance_close(self):
        # 同 tier、相關度接近：較新者（2026-06-10）應排在較舊者（2026-01-01）之前
        scored = [
            (0, 0.80, make_row("old", "舊.pdf", "TW", "AI 伺服器需求強。", date(2026, 1, 1))),
            (0, 0.78, make_row("new", "新.pdf", "TW", "AI 伺服器需求強。", date(2026, 6, 10))),
        ]
        sources, _ = build_context(scored, now=self.NOW)
        self.assertEqual([s.report_id for s in sources], ["new", "old"])
        self.assertEqual(sources[0].report_id, "new")  # 較新拿 [1]

    def test_recency_does_not_override_tier(self):
        # 字面強命中的舊篇（tier2）仍勝過弱相關的新篇（tier0）
        scored = [
            (2, 0.70, make_row("strong_old", "強舊.pdf", "TW", "先進封裝。", date(2025, 1, 1))),
            (0, 0.95, make_row("weak_new", "弱新.pdf", "TW", "先進封裝。", date(2026, 6, 17))),
        ]
        sources, _ = build_context(scored, now=self.NOW)
        self.assertEqual(sources[0].report_id, "strong_old")

    def test_missing_date_treated_as_oldest(self):
        # 無日期者 recency_factor=0；同 tier 下有近日期者勝出
        scored = [
            (0, 0.80, make_row("nodate", "無日期.pdf", "TW", "內容。", None)),
            (0, 0.79, make_row("dated", "有日期.pdf", "TW", "內容。", date(2026, 6, 15))),
        ]
        sources, _ = build_context(scored, now=self.NOW)
        self.assertEqual(sources[0].report_id, "dated")


class AnswerGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_off_topic_skips_llm(self):
        from app.services import answer as ans

        called = {"llm": False}

        async def fake_search(*a, **k):
            # tier0 + 低 cosine（distance 0.70 → dense 0.30）→ 離題
            return [(0, 0.30, make_row("r1", "x.pdf", "TW", "完全不相關內容。", distance=0.70))]

        def fake_embed(q):
            return [0.0]

        async def fake_stream(*a, **k):
            called["llm"] = True
            if False:  # 讓函式成為 async generator 但永不 yield
                yield ""

        class _FakeSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def execute(self, *a, **k):
                return None

            async def commit(self):
                return None

        orig = (ans.hybrid_search, ans.embed_query_cached, ans.stream_completion, ans.SessionFactory)
        ans.hybrid_search = fake_search
        ans.embed_query_cached = fake_embed
        ans.stream_completion = fake_stream
        ans.SessionFactory = lambda: _FakeSession()
        try:
            events = [e async for e in ans.answer_question("今天天氣如何？")]
        finally:
            (ans.hybrid_search, ans.embed_query_cached, ans.stream_completion, ans.SessionFactory) = orig

        kinds = [k for k, _ in events]
        self.assertEqual(kinds, ["sources", "token", "done"])
        self.assertEqual(events[0][1], [])  # 離題不顯示任何來源
        self.assertEqual(events[1][1], ans.OFF_TOPIC_MESSAGE)
        self.assertEqual(events[2][1], {"cited": []})
        self.assertFalse(called["llm"])  # 未呼叫 LLM

    async def test_on_topic_calls_llm(self):
        from app.services import answer as ans

        async def fake_search(*a, **k):
            # tier1（字面命中）→ 非離題，應進入 LLM 串流
            return [(1, 0.85, make_row("r1", "x.pdf", "TW", "台積電先進封裝。", date(2026, 6, 1), distance=0.20))]

        def fake_embed(q):
            return [0.0]

        async def fake_stream(*a, **k):
            yield "答案[1]"

        class _FakeSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def execute(self, *a, **k):
                return None

            async def commit(self):
                return None

        orig = (ans.hybrid_search, ans.embed_query_cached, ans.stream_completion, ans.SessionFactory)
        ans.hybrid_search = fake_search
        ans.embed_query_cached = fake_embed
        ans.stream_completion = fake_stream
        ans.SessionFactory = lambda: _FakeSession()
        try:
            events = [e async for e in ans.answer_question("台積電封裝如何？")]
        finally:
            (ans.hybrid_search, ans.embed_query_cached, ans.stream_completion, ans.SessionFactory) = orig

        kinds = [k for k, _ in events]
        self.assertEqual(kinds[0], "sources")
        self.assertTrue(len(events[0][1]) >= 1)  # 有來源
        self.assertIn(("token", "答案[1]"), events)
        self.assertEqual(events[-1], ("done", {"cited": ["r1"]}))


if __name__ == "__main__":
    unittest.main()
