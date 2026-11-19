"""標註與訊號擷取 prompt 的代號格式片段由 profile 的 `[instruments]` 推導；廷豐渲染出的全文逐字不變。

`tests/fixtures/instrument_prompts_tingfong.json` 是代號格式改由 profile 推導**之前**的程式直接印出來的全文
（golden）。prompt 差一個字，標註與訊號的評測就不再可比，LLM 跳過名單（以 model 為鍵、不看 prompt）也會繼續擋著
已經改過 prompt 的任務，所以這份 golden 只能在「刻意改 prompt」時重生。

另一半：宣告其他格式的 profile（示範券商：台股 4～6 碼數字＋美股 1～5 碼字母含股別後綴）得到相應的格式說明與範例，
而且格式片段以外的文字逐字相同（profile 不能覆寫 prompt 文字）。
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app import profile as P  # noqa: E402
from app.services import brief, signal_extract, tagging  # noqa: E402
from app.services.tagging import CodeFormat  # noqa: E402
from scripts import _ingest_core, tag_all_cli  # noqa: E402

GOLDEN = REPO_ROOT / "tests" / "fixtures" / "instrument_prompts_tingfong.json"
DEMO = REPO_ROOT / "profiles" / "demo"
TINGFONG = REPO_ROOT / "profiles" / "tingfong"


def _ingest_prompt() -> str:
    """sync 行內標註（`_tag_via_cli`）實際送出的 prompt。"""
    captured: dict[str, str] = {}

    def fake_run(prompt, model, **kw):
        captured["prompt"] = prompt
        return mock.Mock(text=None, error="x")

    with mock.patch.object(_ingest_core, "run_claude", fake_run):
        _ingest_core._tag_via_cli("a.pdf", "報告內文", 100)
    return captured["prompt"]


def _brief_reports() -> list[brief.BriefReport]:
    return [
        brief.BriefReport(report_id=f"r{i}", file_hash=f"h{i}", file_name=f"{m}.pdf", title=f"{m} 標題",
                          market=m, source="元大", report_date=date(2026, 11, 3), summary="摘要")
        for i, m in enumerate([*tagging.MARKETS, None])
    ]


def render_all() -> dict[str, str]:
    """與產生 golden 時同一組鍵與輸入。"""
    reports = _brief_reports()
    return {
        "tagging.TAG_INSTRUCTION": tagging.tag_instruction(P.get_profile().instruments.code_formats),
        "signal_extract.SIGNAL_INSTRUCTION": signal_extract.signal_instruction(
            P.get_profile().instruments.code_formats),
        "signal_extract.build_signal_prompt": signal_extract.build_signal_prompt(
            "a.pdf", "2026-11-03", "元大", ["2330", "2317"], "報告內文"),
        "tag_all_cli.build_prompt": tag_all_cli.build_prompt("a.pdf", "報告內文", 100),
        "_ingest_core._tag_via_cli": _ingest_prompt(),
        "brief.build_material": brief.build_material(reports, [], total_reports=len(reports)),
    }


class _ProfileDir(unittest.TestCase):
    PROFILE: Path

    def setUp(self):
        self._env = mock.patch.dict(os.environ, {"PROFILE_DIR": str(self.PROFILE)})
        self._env.start()
        P.reset_profile_cache()

    def tearDown(self):
        self._env.stop()
        P.reset_profile_cache()


class TingfongGoldenTests(_ProfileDir):
    PROFILE = TINGFONG

    def test_rendered_prompts_match_golden_verbatim(self):
        golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
        rendered = render_all()
        self.assertEqual(set(rendered), set(golden))
        for key, expected in golden.items():
            with self.subTest(key=key):
                self.assertEqual(rendered[key], expected)

    def test_constants_are_the_default_profile_version(self):
        golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
        self.assertEqual(tagging.TAG_INSTRUCTION, golden["tagging.TAG_INSTRUCTION"])
        self.assertEqual(signal_extract.SIGNAL_INSTRUCTION, golden["signal_extract.SIGNAL_INSTRUCTION"])
        # section 缺席的框架預設＝廷豐宣告的格式
        self.assertEqual(P.Instruments().code_formats, P.get_profile().instruments.code_formats)
        self.assertEqual(tagging.tag_instruction(P.Instruments().code_formats), tagging.TAG_INSTRUCTION)

    def test_format_arguments_are_required(self):
        with self.assertRaises(TypeError):
            tagging.tag_instruction()  # type: ignore[call-arg]
        with self.assertRaises(TypeError):
            tagging.build_tagging_prompt("a.pdf", "a.pdf")  # type: ignore[call-arg]


class DemoProfileTests(_ProfileDir):
    PROFILE = DEMO

    def setUp(self):
        super().setUp()
        self.formats = P.get_profile().instruments.code_formats
        self.tag = tagging.tag_instruction(self.formats)

    def test_market_lines_describe_each_declared_format(self):
        self.assertIn("- TW：台股，台灣上市櫃個股或產業（4～6 碼代碼如 1102、00878；本土券商個股報告）\n", self.tag)
        self.assertIn("- US：美股，美國個股或市場（1～5 碼大寫英文字母代碼如 NVDA、GOOGL，股別後綴寫成 BRK.B；"
                      "多為英文外資報告、webcast/flyer 法說）\n", self.tag)
        self.assertNotIn("4 碼代碼如 1102、2317", self.tag)

    def test_stock_targets_rules_point_to_the_formats(self):
        self.assertIn("輸出股票代碼字串清單（代碼格式見上方各市場的說明，不限報告的主要市場；依重要性排序，最多 8 檔）",
                      self.tag)
        self.assertIn("- 只輸出能明確判定代碼者；", self.tag)
        self.assertIn('"stock_targets": ["2330", "NVDA", ...]', self.tag)
        self.assertNotIn("4 碼股票代碼", self.tag)

    def test_examples_pass_the_profile_rule(self):
        """prompt 給的範例本身必須通過同一份 profile 的驗證（範例教模型輸出會被丟掉的代號就是自相矛盾）。"""
        rule = P.get_profile().instruments.normalize_code
        for code in ("1102", "00878", "2330", "NVDA", "GOOGL", "BRK.B"):
            with self.subTest(code=code):
                self.assertEqual(rule(code), code)

    def test_signal_code_field(self):
        text = signal_extract.signal_instruction(self.formats)
        self.assertIn('"instrument_code": "<代碼，須在指定清單內>"', text)
        prompt = signal_extract.build_signal_prompt("a.pdf", None, None, ["2330", "NVDA"], "內文")
        self.assertTrue(prompt.startswith(text))
        self.assertIn("指定標的清單（只擷取這些代碼）：2330、NVDA", prompt)

    def test_batch_prompts_follow_the_profile(self):
        self.assertTrue(tag_all_cli.build_prompt("a.pdf", "內文", 100).startswith(self.tag))
        self.assertTrue(_ingest_prompt().startswith(self.tag))

    def test_only_the_format_fragments_change(self):
        """格式片段以外的文字與廷豐逐字相同：profile 不能覆寫 prompt 文字。"""
        tf = tagging.TAG_INSTRUCTION.splitlines()
        demo = self.tag.splitlines()
        self.assertEqual(len(tf), len(demo))
        changed = [i for i, (a, b) in enumerate(zip(tf, demo)) if a != b]
        self.assertEqual([tf[i][:8] for i in changed],
                         ["- TW：台股，", "- US：美股，", "stock_ta", "- 只輸出能明確", "2. JSON "])


class FormatPhraseTests(unittest.TestCase):
    def test_single_formats(self):
        cases = [
            (CodeFormat("TW", "digits", 4, 4), "4 碼"),
            (CodeFormat("TW", "digits", 4, 6), "4～6 碼"),
            (CodeFormat("US", "letters", 1, 5, share_class=True), "1～5 碼英文字母"),
        ]
        for fmt, phrase in cases:
            with self.subTest(fmt=fmt):
                self.assertEqual(tagging.code_length_phrase((fmt,)), phrase)
                text = tagging.tag_instruction((fmt,))
                self.assertIn(f"輸出 {phrase}股票代碼字串清單（依重要性排序", text)
                self.assertIn(f"只輸出能明確判定 {phrase}代碼者", text)
                self.assertIn(f'"<{phrase}代碼，須在指定清單內>"', signal_extract.signal_instruction((fmt,)))

    def test_us_only_profile_drops_the_tw_code_note(self):
        text = tagging.tag_instruction((CodeFormat("US", "letters", 1, 5),))
        self.assertIn("- TW：台股，台灣上市櫃個股或產業（本土券商個股報告）\n", text)
        self.assertIn("- US：美股，美國個股或市場（1～5 碼大寫英文字母代碼如 NVDA、GOOGL；", text)
        self.assertIn('"stock_targets": ["NVDA", ...]', text)

    def test_markets_without_examples_state_only_the_length(self):
        text = tagging.tag_instruction((CodeFormat("CN", "digits", 6, 6), CodeFormat("HK", "digits", 4, 5)))
        self.assertIn("- CN：陸股，中國 A 股、陸股策略（6 碼代碼如 600519、000001）\n", text)
        self.assertIn("- HK：港股，香港掛牌個股或市場（4～5 碼代碼如 0700、09988）\n", text)
        text = tagging.tag_instruction((CodeFormat("GLOBAL", "digits", 5, 5),))
        self.assertIn("加密貨幣", text)
        self.assertIn("也歸 GLOBAL**（5 碼代碼）\n", text)
        self.assertIn('"stock_targets": ["<代碼>", ...]', text)

    def test_market_lines_follow_the_standard_codes(self):
        text = tagging.TAG_INSTRUCTION
        listed = [line[2:].split("：")[0] for line in text.splitlines()
                  if line.startswith("- ") and "：" in line and line[2:].split("：")[0].isupper()]
        self.assertEqual(listed, tagging.MARKETS)


if __name__ == "__main__":
    unittest.main()
