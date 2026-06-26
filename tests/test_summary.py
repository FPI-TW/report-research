# tests/test_summary.py
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from generate_summaries import (  # noqa: E402
    MAX_SUMMARY_CHARS,
    MODEL,
    build_cli_args,
    parse_summary,
    read_hashes_file,
)


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


class ReadHashesFileTests(unittest.TestCase):
    def test_reads_and_strips_blank_lines(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "h.txt"
            p.write_text("aaa\n\n  bbb \n\nccc\n", encoding="utf-8")
            self.assertEqual(read_hashes_file(str(p)), ["aaa", "bbb", "ccc"])

    def test_empty_file_returns_empty_list(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "h.txt"
            p.write_text("", encoding="utf-8")
            self.assertEqual(read_hashes_file(str(p)), [])


class BuildCliArgsTests(unittest.TestCase):
    def test_isolates_settings_to_cut_coldstart_io(self):
        # 每次 claude -p 冷啟動會載入全域 settings/hooks/plugins，是磁碟小檔 I/O 的主因；
        # 帶 --setting-sources ''（空＝不載入任何來源）可砍掉這段。
        args = build_cli_args("hello")
        self.assertIn("--setting-sources", args)
        i = args.index("--setting-sources")
        self.assertEqual(args[i + 1], "")

    def test_passes_prompt_and_model(self):
        args = build_cli_args("hello world")
        self.assertEqual(args[:3], ["claude", "-p", "hello world"])
        self.assertIn("--model", args)
        self.assertEqual(args[args.index("--model") + 1], MODEL)

    def test_strips_nul_from_prompt(self):
        # POSIX argv 不可含 NUL（部分 PDF 抽出的文字含 \x00），否則 subprocess 直接拋
        args = build_cli_args("ab\x00cd")
        self.assertEqual(args[2], "abcd")


if __name__ == "__main__":
    unittest.main()
