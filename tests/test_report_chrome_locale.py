# tests/test_report_chrome_locale.py
"""M10c：深度研報 PDF chrome（免責／品牌／頁尾／KPI 來源／圖表說明）依 locale。

涵蓋 Python 側 chrome 在地化、Typst emit（zh 零回歸／en 帶英文 chrome）、
以及**英文研報三模板實際編譯**（證明 .typ 新增參數不破壞編譯）與 WeasyPrint
英文 chrome。
"""
import io
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services import pdf  # noqa: E402
from app.services.typst_render import build_document, emit_typst  # noqa: E402
from app.services.typst_render import render_report_pdf as typst_render  # noqa: E402
from app.templates import manifest  # noqa: E402

_MD = (
    "# TSMC — Deep Research Report\n\n"
    "## Executive Summary\n\n"
    "```kpi\n"
    '{"items":[{"label":"Target","value":"1200","change":"+10%","dir":"up","source":"[1]"}]}\n'
    "```\n\n"
    "Bullish [1].\n\n"
    "## Key Findings\n\n- Strong.\n\n"
    "## In-Depth Analysis\n\n"
    "```chart\n"
    '{"type":"bar","title":"Rev","x":["Q1","Q2"],"series":[{"name":"R","values":[1,2]}],"unit":"B","source":"[1]"}\n'
    "```\n\n"
    "Analysis [1].\n\n"
    "## Risks & Outlook\n\nRisks.\n\n"
    "## References\n[1] a.pdf\n"
)
_META = {"date": "2026-07-23", "question": "TSMC"}


class PdfChromeLocaleTests(unittest.TestCase):
    def test_disclaimer_and_brand(self):
        self.assertIn("免責聲明", pdf.report_disclaimer("zh-Hant"))
        self.assertTrue(pdf.report_disclaimer("en").startswith("Disclaimer"))
        self.assertEqual(pdf.brand_name("zh-Hant"), "廷豐智能研報")
        self.assertEqual(pdf.brand_name("en"), "Tingfeng Intelligent Research")

    def test_unknown_locale_fails_open_to_zh(self):
        self.assertIn("免責聲明", pdf.report_disclaimer("fr"))
        self.assertEqual(pdf.brand_name("fr"), "廷豐智能研報")

    def test_chart_caption_locale(self):
        spec = {"title": "Rev", "source": "[1]"}
        self.assertEqual(pdf.chart_caption(spec, "en"), "Rev (Source [1])")
        self.assertEqual(pdf.chart_caption(spec, "zh-Hant"), "Rev（來源 [1]）")
        self.assertEqual(pdf.chart_caption({"source": "[1]"}, "en"), "(Source [1])")


class WeasyprintChromeLocaleTests(unittest.TestCase):
    def test_en_html_chrome(self):
        html = pdf._build_document(_MD, title="TSMC — Deep Research Report", meta=_META, locale="en")
        for s in ("In-Depth Research Report", "Contents", "Tingfeng Intelligent Research",
                  "Generated 2026-07-23", "Disclaimer"):
            self.assertIn(s, html, s)

    def test_inject_kpi_source_label_locale(self):
        block = '```kpi\n{"items":[{"label":"L","value":"1","source":"S"}]}\n```'
        self.assertIn('kpi-src">Source S', pdf.inject_kpi(block, "en"))
        self.assertIn('kpi-src">來源 S', pdf.inject_kpi(block, "zh-Hant"))

    def test_inject_charts_caption_locale(self):
        block = '```chart\n{"type":"bar","title":"Rev","x":["a"],"series":[{"name":"n","values":[1]}],"source":"[1]"}\n```'
        self.assertIn("Rev (Source [1])", pdf.inject_charts(block, "en"))
        self.assertIn("Rev（來源 [1]）", pdf.inject_charts(block, "zh-Hant"))

    def test_zh_html_chrome_unchanged(self):
        html = pdf._build_document(_MD.replace("Executive Summary", "執行摘要"),
                                   title="台積電 深度研報", meta=_META, locale="zh-Hant")
        self.assertIn("廷豐智能研報", html)
        self.assertIn("免責聲明", html)

    def test_en_weasyprint_renders_pdf(self):
        out = pdf.render_report_pdf(_MD, title="TSMC — Deep Research Report", meta=_META, locale="en")
        self.assertEqual(out[:4], b"%PDF")


class EmitTypstLocaleTests(unittest.TestCase):
    def test_zh_emit_has_no_extra_chrome(self):
        # 零回歸：zh 不 emit brand/footer-note/lang 等額外參數
        doc = build_document(_MD, title="T", meta=_META)
        src = emit_typst(doc, disclaimer=pdf.report_disclaimer("zh-Hant"))
        self.assertNotIn("brand:", src)
        self.assertNotIn("footer-note:", src)
        self.assertNotIn("kpi-source-label:", src)

    def test_en_emit_has_english_chrome(self):
        doc = build_document(_MD, title="T", meta=_META, locale="en")
        src = emit_typst(doc, disclaimer=pdf.report_disclaimer("en"), locale="en")
        self.assertIn("Tingfeng Intelligent Research", src)
        self.assertIn("footer-note:", src)
        self.assertIn('lang: "en"', src)
        self.assertIn("kpi-source-label:", src)
        self.assertIn("Disclaimer", src)
        # 圖表 supplement 與 KPI source-label 具名引數就地帶上
        self.assertIn("supplement:", src)
        self.assertIn("source-label:", src)


class EnglishTypstCompileTests(unittest.TestCase):
    """英文研報三模板實際編譯（.typ 新增參數不破壞編譯的最終防線）。"""

    @staticmethod
    def _pages(pdf_bytes: bytes) -> int:
        import pypdf

        return len(pypdf.PdfReader(io.BytesIO(pdf_bytes)).pages)

    def test_each_template_renders_english_bounded_pdf(self):
        for spec in manifest.list_templates():
            with self.subTest(template=spec.id):
                out = typst_render(
                    _MD, title="TSMC — Deep Research Report", meta=_META,
                    template_id=spec.id, locale="en",
                )
                self.assertEqual(out[:4], b"%PDF", spec.id)
                pages = self._pages(out)
                self.assertGreaterEqual(pages, 1, spec.id)
                self.assertLessEqual(pages, 4, f"{spec.id} 爆頁：{pages}")


if __name__ == "__main__":
    unittest.main()
