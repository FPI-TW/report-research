# tests/test_answer.py
import json
import sys
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services import llm  # noqa: E402
from app.services.answer import (  # noqa: E402
    Source,
    _recency_factor,
    build_context,
    build_user_prompt,
    cited_report_ids,
    history_item,
    split_external_sources,
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

    def test_non_positive_half_life_does_not_crash(self):
        self.assertEqual(_recency_factor(date(2026, 6, 17), self.NOW.date(), 0), 1.0)
        self.assertEqual(_recency_factor(date(2026, 6, 17), self.NOW.date(), -1), 1.0)
        self.assertEqual(_recency_factor(None, self.NOW.date(), 0), 0.0)


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
    """離題判定改由 classify_intent（看意圖）決定，與 scored 分數無關。"""

    def _patch(self, ans, *, in_domain, called):
        async def fake_search(*a, **k):
            return [
                (0, 0.60, make_row("r1", "x.pdf", "TW", "可口可樂財報。", date(2026, 6, 1), distance=0.40))
            ]

        def fake_embed(q):
            return [0.0]

        async def fake_stream(*a, **k):
            called["llm"] = True
            yield "答案[1]"

        async def fake_intent(question, **k):
            called["intent"] = True
            return in_domain

        orig = (
            ans.hybrid_search,
            ans.embed_query_cached,
            ans.stream_completion,
            ans.SessionFactory,
            ans.classify_intent,
        )
        ans.hybrid_search = fake_search
        ans.embed_query_cached = fake_embed
        ans.stream_completion = fake_stream
        ans.SessionFactory = lambda: _FakeSession()
        ans.classify_intent = fake_intent
        return orig

    @staticmethod
    def _restore(ans, orig):
        (
            ans.hybrid_search,
            ans.embed_query_cached,
            ans.stream_completion,
            ans.SessionFactory,
            ans.classify_intent,
        ) = orig

    async def test_off_topic_intent_skips_llm(self):
        # 意圖判定為離題（即使檢索分數不低）→ 拒答、空來源、不跑主 LLM
        from app.services import answer as ans

        called = {"llm": False, "intent": False}
        orig = self._patch(ans, in_domain=False, called=called)
        try:
            events = [e async for e in ans.answer_question("我想喝飲料推薦給我")]
        finally:
            self._restore(ans, orig)

        kinds = [k for k, _ in events]
        self.assertEqual(kinds, ["sources", "notice", "done"])  # notice：前端以提示卡渲染
        self.assertEqual(events[0][1], [])  # 離題不顯示任何來源
        self.assertEqual(events[1][1], ans.OFF_TOPIC_MESSAGE)
        self.assertEqual(events[2][1], {"cited": []})
        self.assertTrue(called["intent"])  # 有跑意圖判定
        self.assertFalse(called["llm"])  # 未跑主 LLM

    async def test_on_topic_intent_calls_llm(self):
        # 意圖判定為在領域 → 正常檢索 + 串流回答 + 引用
        from app.services import answer as ans

        called = {"llm": False, "intent": False}
        orig = self._patch(ans, in_domain=True, called=called)
        try:
            events = [e async for e in ans.answer_question("可口可樂的投資評級如何")]
        finally:
            self._restore(ans, orig)

        kinds = [k for k, _ in events]
        self.assertEqual(kinds[0], "sources")
        self.assertTrue(len(events[0][1]) >= 1)  # 有來源
        self.assertIn(("token", "答案[1]"), events)
        self.assertEqual(events[-1][0], "done")
        self.assertEqual(events[-1][1]["cited"], ["r1"])
        self.assertIn("qa_id", events[-1][1])  # done 帶 qa_id 供前端掛回饋
        self.assertTrue(called["llm"])  # 有跑主 LLM


class AnswerWebTests(unittest.IsolatedAsyncioTestCase):
    async def test_body_excludes_sentinel_and_emits_ext_sources(self):
        from app.services import answer as ans

        async def fake_search(*a, **k):
            return [(1, 0.85, make_row("r1", "x.pdf", "TW", "台積電先進封裝。", date(2026, 6, 1), distance=0.2))]

        def fake_embed(q):
            return [0.0]

        async def fake_stream(*a, **k):
            for ch in ["前段答案[1]。", "（網路）補充。", "\n[EXT_SOURCES]\n- 標題 | https://x.com\n"]:
                yield ch

        async def fake_intent(q, **k):
            return True

        orig = (ans.hybrid_search, ans.embed_query_cached, ans.stream_completion,
                ans.SessionFactory, ans.classify_intent)
        ans.hybrid_search = fake_search
        ans.embed_query_cached = fake_embed
        ans.stream_completion = fake_stream
        ans.SessionFactory = lambda: _FakeSession()
        ans.classify_intent = fake_intent
        try:
            events = [e async for e in ans.answer_question("台積電封裝")]
        finally:
            (ans.hybrid_search, ans.embed_query_cached, ans.stream_completion,
             ans.SessionFactory, ans.classify_intent) = orig

        body = "".join(p for k, p in events if k == "token")
        self.assertIn("前段答案[1]。", body)
        self.assertIn("（網路）補充。", body)
        self.assertNotIn("[EXT_SOURCES]", body)        # sentinel 不外洩
        self.assertNotIn("https://x.com", body)        # 來源不混進正文
        ext = [p for k, p in events if k == "ext_sources"]
        self.assertEqual(len(ext), 1)
        self.assertEqual(ext[0], [{"title": "標題", "url": "https://x.com"}])
        self.assertEqual([k for k, _ in events][0], "sources")  # 事件序起點
        self.assertEqual(events[-2][0], "ext_sources")          # ext_sources 緊鄰 done 之前
        self.assertEqual(events[-1][0], "done")
        self.assertEqual(events[-1][1]["cited"], ["r1"])

    async def test_sentinel_split_across_chunks_not_leaked(self):
        # sentinel 被拆在兩個 chunk（"[EXT_" + "SOURCES]"）：hold 尾段須仍攔截、不外洩
        from app.services import answer as ans

        async def fake_search(*a, **k):
            return [(1, 0.85, make_row("r1", "x.pdf", "TW", "內容。", date(2026, 6, 1), distance=0.2))]

        def fake_embed(q):
            return [0.0]

        async def fake_stream(*a, **k):
            for ch in ["前段答案[1]。\n[EXT_", "SOURCES]\n- 標題 | https://x.com\n"]:
                yield ch

        async def fake_intent(q, **k):
            return True

        orig = (ans.hybrid_search, ans.embed_query_cached, ans.stream_completion,
                ans.SessionFactory, ans.classify_intent)
        ans.hybrid_search = fake_search
        ans.embed_query_cached = fake_embed
        ans.stream_completion = fake_stream
        ans.SessionFactory = lambda: _FakeSession()
        ans.classify_intent = fake_intent
        try:
            events = [e async for e in ans.answer_question("問題")]
        finally:
            (ans.hybrid_search, ans.embed_query_cached, ans.stream_completion,
             ans.SessionFactory, ans.classify_intent) = orig

        body = "".join(p for k, p in events if k == "token")
        self.assertIn("前段答案[1]。", body)
        self.assertNotIn("[EXT_SOURCES]", body)        # 跨 chunk 仍不外洩
        self.assertNotIn("[EXT_", body)                # 半截 sentinel 也不外洩
        self.assertNotIn("https://x.com", body)
        ext = [p for k, p in events if k == "ext_sources"]
        self.assertEqual(ext[0], [{"title": "標題", "url": "https://x.com"}])

    async def test_emits_status_when_web_search_starts(self):
        # 模型開始搜尋（stream 吐 SEARCH_EVENT 標記）→ 發一次 ("status","searching_web")，標記不外洩
        from app.services import answer as ans
        from app.services.llm import SEARCH_EVENT

        async def fake_search(*a, **k):
            return [(1, 0.85, make_row("r1", "x.pdf", "TW", "內容。", date(2026, 6, 1), distance=0.2))]

        def fake_embed(q):
            return [0.0]

        async def fake_stream(*a, **k):
            yield SEARCH_EVENT          # 模型開始上網
            yield "答案[1]。"

        async def fake_intent(q, **k):
            return True

        orig = (ans.hybrid_search, ans.embed_query_cached, ans.stream_completion,
                ans.SessionFactory, ans.classify_intent)
        ans.hybrid_search = fake_search
        ans.embed_query_cached = fake_embed
        ans.stream_completion = fake_stream
        ans.SessionFactory = lambda: _FakeSession()
        ans.classify_intent = fake_intent
        try:
            events = [e async for e in ans.answer_question("問題")]
        finally:
            (ans.hybrid_search, ans.embed_query_cached, ans.stream_completion,
             ans.SessionFactory, ans.classify_intent) = orig

        self.assertIn(("status", "searching_web"), events)
        self.assertEqual(sum(1 for k, _ in events if k == "status"), 1)  # 只發一次
        body = "".join(p for k, p in events if k == "token")
        self.assertNotIn(SEARCH_EVENT, body)     # 控制標記不外洩到正文
        self.assertIn("答案[1]。", body)


class WebSearchDetectTests(unittest.TestCase):
    def test_detects_websearch_tool_use(self):
        line = ('{"type":"stream_event","event":{"type":"content_block_start",'
                '"content_block":{"type":"tool_use","name":"WebSearch","input":{}}}}')
        self.assertTrue(llm.is_web_search_start(line))

    def test_other_tool_not_detected(self):
        line = ('{"type":"stream_event","event":{"type":"content_block_start",'
                '"content_block":{"type":"tool_use","name":"ToolSearch","input":{}}}}')
        self.assertFalse(llm.is_web_search_start(line))

    def test_text_thinking_and_malformed_not_detected(self):
        self.assertFalse(llm.is_web_search_start(
            '{"type":"stream_event","event":{"type":"content_block_start",'
            '"content_block":{"type":"text","text":""}}}'))
        self.assertFalse(llm.is_web_search_start(
            '{"type":"stream_event","event":{"type":"content_block_delta",'
            '"delta":{"type":"text_delta","text":"hi"}}}'))
        self.assertFalse(llm.is_web_search_start("not json"))
        self.assertFalse(llm.is_web_search_start(""))


class FeedbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_value_rejected_without_db(self):
        # 非 like/dislike 一律 False，且不觸碰 DB（純驗證分支）
        from app.services.answer import record_feedback

        self.assertFalse(await record_feedback("any-id", "love"))
        self.assertFalse(await record_feedback("any-id", ""))

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
        for flag in ("claude", "-p", "--model", "stream-json", "--include-partial-messages"):
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
        self.assertEqual(ext, [
            {"title": "標題A", "url": "https://a.com/x"},
            {"title": "標題B", "url": "http://b.com"},
        ])

    def test_skips_malformed_and_non_http(self):
        text = "答案。\n[EXT_SOURCES]\n- 沒有管線的壞行\n- 標題 | ftp://x\n- 好的 | https://ok.com\n"
        body, ext = split_external_sources(text)
        self.assertEqual(ext, [{"title": "好的", "url": "https://ok.com"}])

    def test_empty_title_falls_back_to_url(self):
        body, ext = split_external_sources("答案。\n[EXT_SOURCES]\n-  | https://a.com\n")
        self.assertEqual(ext, [{"title": "https://a.com", "url": "https://a.com"}])


class LogQaSourcesTests(unittest.IsolatedAsyncioTestCase):
    async def test_log_qa_inserts_sources_json(self):
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
            qid = await ans._log_qa("q", "a", [], {}, 5, [{"n": 1, "report_id": "r1"}])
        finally:
            ans.SessionFactory = orig

        self.assertTrue(qid)
        self.assertIn("sources", captured)
        self.assertEqual(json.loads(captured["sources"]), [{"n": 1, "report_id": "r1"}])


class HistoryItemTests(unittest.TestCase):
    def test_maps_row_with_sources(self):
        d = date(2026, 6, 18)
        row = ("11111111-1111-1111-1111-111111111111", "台積電?", "答案[1]",
               d, "like", [{"n": 1, "report_id": "r1", "file_name": "甲.pdf"}])
        out = history_item(row)
        self.assertEqual(out["id"], "11111111-1111-1111-1111-111111111111")
        self.assertEqual(out["question"], "台積電?")
        self.assertEqual(out["answer"], "答案[1]")
        self.assertEqual(out["created_at"], "2026-06-18")
        self.assertEqual(out["feedback"], "like")
        self.assertEqual(out["sources"], [{"n": 1, "report_id": "r1", "file_name": "甲.pdf"}])

    def test_null_sources_becomes_empty_list(self):
        row = ("id2", "q", "a", date(2026, 6, 1), None, None)
        out = history_item(row)
        self.assertEqual(out["sources"], [])
        self.assertIsNone(out["feedback"])


if __name__ == "__main__":
    unittest.main()
