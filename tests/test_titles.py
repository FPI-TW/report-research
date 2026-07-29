# tests/test_titles.py
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from generate_titles import (  # noqa: E402
    MAX_TITLE_CHARS,
    MODEL,
    build_cli_args,
    build_prompt,
    parse_title,
)


class ParseTitleTests(unittest.TestCase):
    def test_chinese_title_extracted(self):
        r = parse_title(
            '{"title": "中東危機引爆台股重挫逾千點", "title_original": null,'
            ' "title_source": "extracted"}'
        )
        self.assertEqual(r.title, "中東危機引爆台股重挫逾千點")
        self.assertIsNone(r.title_original)
        self.assertEqual(r.title_source, "extracted")

    def test_english_title_translated_keeps_original(self):
        r = parse_title(
            '```json\n{"title": "先進封裝用玻璃：台灣面板廠短期上檔有限",'
            ' "title_original": "Glass in advanced packaging: Limited near-term'
            ' upside for Taiwan display makers", "title_source": "translated"}\n```'
        )
        self.assertEqual(r.title, "先進封裝用玻璃：台灣面板廠短期上檔有限")
        self.assertTrue(r.title_original.startswith("Glass in advanced packaging"))
        self.assertEqual(r.title_source, "translated")

    def test_original_identical_to_title_is_dropped(self):
        # 中文報告時模型常把原文複製一份到 title_original，無資訊量、不必存
        r = parse_title(
            '{"title": "凱基投資早報", "title_original": "凱基投資早報",'
            ' "title_source": "extracted"}'
        )
        self.assertIsNone(r.title_original)

    def test_multiline_title_collapsed(self):
        # PDF 抽出的標題常帶換行（"AI Networking: Scale-up\n Technology..."）
        r = parse_title('{"title": "AI 網路：\\n  Scale-up 技術", "title_source": "translated"}')
        self.assertEqual(r.title, "AI 網路： Scale-up 技術")

    def test_strips_wrapping_quotes_and_markdown(self):
        self.assertEqual(parse_title('{"title": "「台積電法說會摘要」"}').title, "台積電法說會摘要")
        self.assertEqual(parse_title('{"title": "## 記憶體報價反彈"}').title, "記憶體報價反彈")

    def test_unknown_title_source_becomes_none_but_title_kept(self):
        r = parse_title('{"title": "記憶體報價反彈", "title_source": "guessed"}')
        self.assertEqual(r.title, "記憶體報價反彈")
        self.assertIsNone(r.title_source)

    def test_null_title_returns_none(self):
        # 抽字損毀時模型應回 null，不可猜；該篇維持 NULL → 前端回退檔名
        self.assertIsNone(parse_title('{"title": null, "title_source": "extracted"}'))
        self.assertIsNone(parse_title('{"title": "   "}'))

    def test_no_plain_text_fallback(self):
        # 與 parse_summary 的關鍵差異：純文字不接受。模型沒照格式輸出時多半是把
        # 整段內文吐回來，寧可留 NULL 回退檔名，也不要把一段內文當標題顯示。
        self.assertIsNone(parse_title("這份報告分析半導體景氣循環，並上調目標價。"))

    def test_corrupt_json_returns_none(self):
        self.assertIsNone(parse_title('{"title": "未閉合'))
        self.assertIsNone(parse_title(""))

    def test_filename_echoed_back_is_rejected(self):
        # 規則 1 要求不得用檔名當標題；模型照抄＝失敗，不是可用結果
        fn = "624726992507895929_260728_gs_umt.pdf"
        self.assertIsNone(parse_title('{"title": "%s"}' % fn, fn))
        self.assertIsNone(parse_title('{"title": "%s"}' % fn[:-4], fn))
        # 但只是「含」檔名字樣的正常標題不受影響
        self.assertIsNotNone(parse_title('{"title": "友達 2026 年展望"}', fn))

    def test_truncates_overlong(self):
        r = parse_title('{"title": "' + "字" * 500 + '"}')
        self.assertEqual(len(r.title), MAX_TITLE_CHARS)


class BuildPromptTests(unittest.TestCase):
    def test_excerpt_is_cleaned_of_cjk_gaps(self):
        # full_text 存的是未清理抽取文字（「台 積 電」）；不清理會讓模型讀錯詞
        p = build_prompt("a.pdf", "台 積 電 法 說 會 摘 要\n\n內文", excerpt=100)
        self.assertIn("台積電法說會摘要", p)

    def test_excerpt_length_capped(self):
        p = build_prompt("a.pdf", "餘" * 50_000, excerpt=200)
        self.assertNotIn("餘" * 201, p)

    def test_filename_included_but_marked_unreliable(self):
        p = build_prompt("624726992507895929_260728_gs_umt.pdf", "內文", excerpt=100)
        self.assertIn("624726992507895929_260728_gs_umt.pdf", p)
        self.assertIn("不可當標題", p)


class BuildCliArgsTests(unittest.TestCase):
    def test_isolates_settings_to_cut_coldstart_io(self):
        args = build_cli_args("hello")
        self.assertIn("--setting-sources", args)
        self.assertEqual(args[args.index("--setting-sources") + 1], "")

    def test_passes_prompt_and_model(self):
        args = build_cli_args("hello world")
        self.assertEqual(args[:3], ["claude", "-p", "hello world"])
        self.assertEqual(args[args.index("--model") + 1], MODEL)

    def test_strips_nul_from_prompt(self):
        # POSIX argv 不可含 NUL（部分 PDF 抽出的文字含 \x00），否則 subprocess 直接拋
        self.assertEqual(build_cli_args("ab\x00cd")[2], "abcd")


if __name__ == "__main__":
    unittest.main()
