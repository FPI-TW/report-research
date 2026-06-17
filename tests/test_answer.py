# tests/test_answer.py
import sys
import unittest
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services import llm  # noqa: E402
from app.services.answer import (  # noqa: E402
    Source,
    build_context,
    build_user_prompt,
    cited_report_ids,
)


def make_row(report_id, file_name, market, content, report_date=None):
    """造一列符合 hybrid_search 回傳結構的 row（只填 build_context 會讀的位置）。"""
    row = [None] * 16
    row[1] = report_id  # _RID
    row[2] = file_name  # _FNAME
    row[3] = market  # _MARKET
    row[6] = report_date  # _RDATE
    row[14] = content  # _CONTENT
    row[15] = 0.1  # distance
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

    def test_result_line(self):
        self.assertTrue(llm.is_result_line('{"type":"result","subtype":"success"}'))
        self.assertFalse(llm.is_result_line('{"type":"stream_event"}'))


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


if __name__ == "__main__":
    unittest.main()
