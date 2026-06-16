# tests/test_summary.py
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from generate_summaries import MAX_SUMMARY_CHARS, parse_summary  # noqa: E402


class ParseSummaryTests(unittest.TestCase):
    def test_plain_json(self):
        self.assertEqual(
            parse_summary('{"summary": "本篇聚焦台股AI伺服器供應鏈。"}'),
            "本篇聚焦台股AI伺服器供應鏈。",
        )

    def test_json_with_fence_and_preamble(self):
        raw = '好的，以下是摘要：\n```json\n{"summary": "重點在記憶體報價反彈。"}\n```'
        self.assertEqual(parse_summary(raw), "重點在記憶體報價反彈。")

    def test_plain_text_fallback(self):
        # 模型若直接吐純文字（無大括號）也能接受
        self.assertEqual(
            parse_summary("  本篇分析半導體景氣循環。 "),
            "本篇分析半導體景氣循環。",
        )

    def test_whitespace_collapsed(self):
        self.assertEqual(
            parse_summary('{"summary": "第一句。\\n\\n  第二句。"}'),
            "第一句。 第二句。",
        )

    def test_corrupt_json_returns_none(self):
        # 有左大括號代表本應是 JSON，無法解析就當壞檔，不可把殘缺 JSON 當摘要存入
        self.assertIsNone(parse_summary('{"summary": "未閉合'))

    def test_missing_summary_key_returns_none(self):
        self.assertIsNone(parse_summary('{"foo": "bar"}'))

    def test_empty_returns_none(self):
        self.assertIsNone(parse_summary(""))
        self.assertIsNone(parse_summary("   "))

    def test_truncates_overlong(self):
        out = parse_summary('{"summary": "' + "字" * 1000 + '"}')
        self.assertEqual(len(out), MAX_SUMMARY_CHARS)


if __name__ == "__main__":
    unittest.main()
