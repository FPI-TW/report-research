# tests/test_report_locale.py
"""M10a-2：深度研報輸出語言（locale）。

涵蓋 emitter（骨架標題／參考節）、parser 正則（中英雙認）、prompt 選擇（單次英文
系統提示、逐節／大綱英文），以及**三處標題對齊**的端到端往返：
逐節英文「### Web Sources for This Section」→ split_web_refs 抽出 → build_external_refs
以英文「## External References (Web)」重發 → report.parse_external_refs 認得。
"""
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services import report as rpt  # noqa: E402
from app.services import report_writer as rw  # noqa: E402
from app.services.report_gate import suggested_title  # noqa: E402


class SuggestedTitleLocaleTests(unittest.TestCase):
    def test_zh_default(self):
        self.assertEqual(suggested_title("台積電展望"), "台積電展望 深度研報")

    def test_en(self):
        self.assertEqual(
            suggested_title("TSMC outlook?", "en"), "TSMC outlook — Deep Research Report"
        )

    def test_en_empty(self):
        self.assertEqual(suggested_title("  ", "en"), "Research Report")


class ReportSystemPromptTests(unittest.TestCase):
    def test_en_variant_english_skeleton(self):
        p = rpt.report_system_prompt("en")
        for h in ("Executive Summary", "Key Findings", "In-Depth Analysis",
                  "Risks & Outlook", "References", "## External References (Web)"):
            self.assertIn(h, p)
        self.assertIn("Write entirely in English", p)

    def test_default_is_chinese(self):
        p = rpt.report_system_prompt("zh-Hant")
        self.assertIn("執行摘要", p)
        self.assertIn("一律繁體中文", p)

    def test_unknown_locale_fails_open_to_zh(self):
        self.assertIn("執行摘要", rpt.report_system_prompt("fr"))


class BuildReportPromptTests(unittest.TestCase):
    def test_en_framing(self):
        p = rpt.build_report_prompt("TSMC", "ctx", "TSMC — Deep Research Report", "", "en")
        self.assertIn("in-depth research report", p)
        self.assertIn("Reference passages:", p)

    def test_zh_default(self):
        p = rpt.build_report_prompt("台積電", "ctx", "標題")
        self.assertIn("撰寫一份深度研究報告", p)


class CoverageDirectiveLocaleTests(unittest.TestCase):
    def test_en_zero(self):
        d = rpt.coverage_directive(0, web_enabled=True, locale="en")
        self.assertIn("no reports related to this topic", d)

    def test_en_thin(self):
        d = rpt.coverage_directive(2, web_enabled=True, threshold=8, locale="en")
        self.assertIn("only 2 related reports", d)

    def test_zh_default_zero(self):
        d = rpt.coverage_directive(0, web_enabled=True)
        self.assertIn("找不到與本主題相關", d)

    def test_web_disabled_empty_regardless_of_locale(self):
        self.assertEqual(rpt.coverage_directive(0, web_enabled=False, locale="en"), "")


class BuildOutlineLocaleTests(unittest.TestCase):
    def test_en_skeleton_and_title(self):
        o = rw.build_outline("TSMC roadmap", None,
                             [{"heading": "N2 ramp", "topic": "N2 量產"}], "en")
        heads = [s["heading"] for s in o["sections"]]
        self.assertIn("Executive Summary", heads)
        self.assertIn("Key Findings", heads)
        self.assertIn("Risks & Outlook", heads)
        self.assertEqual(o["title"], "TSMC roadmap — Deep Research Report")

    def test_zh_default_unchanged(self):
        o = rw.build_outline("台積電", None, [{"heading": "先進製程", "topic": "N2"}])
        heads = [s["heading"] for s in o["sections"]]
        self.assertIn("執行摘要", heads)
        self.assertEqual(o["title"], "台積電 深度研報")


class AssembleEmittersLocaleTests(unittest.TestCase):
    def test_build_external_refs_en_heading(self):
        out = rw.build_external_refs([{"title": "T", "url": "http://x.com"}], "en")
        self.assertIn("## External References (Web)", out)
        self.assertIn("- [T](http://x.com)", out)

    def test_build_references_en_heading_and_empty_note(self):
        out = rw.build_references([], "en")
        self.assertIn("## References", out)
        self.assertIn("External References (Web)", out)  # 空引用時的英文提示

    def test_assemble_body_en_analysis_heading(self):
        secs = [{"key": "analysis", "heading": "A", "kind": "analysis", "draft": "x"}]
        body = rw.assemble_body("Title", secs, "en")
        self.assertIn("## In-Depth Analysis", body)


class OutlineSectionPromptLocaleTests(unittest.TestCase):
    def test_outline_prompt_en(self):
        system, _ = rw._build_outline_prompt("TSMC", "ctx", 3, "en")
        self.assertIn("In-Depth Analysis", system)
        self.assertIn("MUST be in English", system)

    def test_section_prompt_en_override_and_web_heading(self):
        sec = {"key": "analysis", "heading": "A", "kind": "analysis"}
        system, prompt = rw._build_section_prompt(
            "TSMC", sec, "ctx", True, web_enabled=True, locale="en"
        )
        self.assertIn("OUTPUT LANGUAGE OVERRIDE", system)
        self.assertIn("Web Sources for This Section", system)

    def test_section_prompt_zh_default_no_override(self):
        sec = {"key": "analysis", "heading": "A", "kind": "analysis"}
        system, _ = rw._build_section_prompt("台積電", sec, "ctx", True)
        self.assertNotIn("OUTPUT LANGUAGE OVERRIDE", system)
        self.assertIn("繁體中文深度研報", system)


class EnglishRoundTripTests(unittest.TestCase):
    """三處標題對齊：逐節英文 web 標題 → 抽出 → 英文外部參考節 → parse 認得。"""

    def test_section_web_extracted_and_external_parsed(self):
        ledger = rw.EvidenceLedger()
        e1 = ledger.add_corpus(report_id="r1", file_name="a.pdf", market="TW")
        secs = [
            {"key": "exec_summary", "heading": "Executive Summary", "kind": "framing",
             "draft": f"Bullish[[ev:{e1.evidence_id}]]"},
            {"key": "analysis", "heading": "A", "kind": "analysis",
             "draft": ("Analysis (web).\n\n### Web Sources for This Section\n"
                       "- [SourceT](https://ex.com/a)")},
            {"key": "risk_outlook", "heading": "Risks & Outlook", "kind": "framing",
             "draft": "Risks."},
        ]
        final, rendered = rw.assemble_final("Report", secs, ledger, "en")
        self.assertEqual(rendered.n_unknown, 0)
        # 逐節 web 區塊已被抽走、不留在內文
        self.assertNotIn("### Web Sources for This Section", final)
        # 彙整為英文外部參考節
        self.assertIn("## External References (Web)", final)
        self.assertIn("- [SourceT](https://ex.com/a)", final)
        self.assertIn("## References", final)
        self.assertIn("## In-Depth Analysis", final)
        # report 端受控解析認得英文外部節
        refs = rpt.parse_external_refs(final)
        self.assertTrue(any(r.get("url") == "https://ex.com/a" for r in refs))


if __name__ == "__main__":
    unittest.main()
