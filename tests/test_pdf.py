import sys
import unittest
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

pytest.importorskip("weasyprint")  # 缺原生庫的環境略過（部署機必裝）


class InjectChartsTests(unittest.TestCase):
    def test_chart_block_becomes_svg_figure(self):
        from app.services.pdf import inject_charts

        md = (
            "## 重點分析\n\n前言。\n\n"
            '```chart\n{"type":"bar","title":"各廠營收","x":["A","B"],'
            '"series":[{"name":"營收","values":[10,20]}],"source":"[3]"}\n```\n\n結語。'
        )
        out = inject_charts(md)
        self.assertIn("<figure class=\"chart\">", out)
        self.assertIn("<svg", out)
        self.assertIn("<figcaption>", out)
        self.assertIn("各廠營收", out)
        self.assertIn("（來源 [3]）", out)
        self.assertNotIn("```chart", out)  # 原始圍欄已被取代

    def test_bad_chart_block_is_dropped(self):
        from app.services.pdf import inject_charts

        md = "前言。\n\n```chart\n{壞 JSON}\n```\n\n結語。"
        out = inject_charts(md)
        self.assertNotIn("```chart", out)
        self.assertNotIn("<svg", out)
        self.assertIn("前言。", out)
        self.assertIn("結語。", out)

    def test_no_chart_block_unchanged(self):
        from app.services.pdf import inject_charts

        md = "## 標題\n\n一般內文[1]。"
        self.assertEqual(inject_charts(md), md)


class RenderReportPdfTests(unittest.TestCase):
    def test_returns_pdf_bytes(self):
        from app.services.pdf import render_report_pdf

        pdf = render_report_pdf(
            "# 標題\n\n## 執行摘要\n\n這是一段內文[1]。",
            title="測試深度研報",
            meta={"date": "2026-06-26", "question": "測試問題"},
        )
        self.assertEqual(pdf[:4], b"%PDF")
        self.assertGreater(len(pdf), 1000)

    def test_renders_pdf_with_chart_block(self):
        from app.services.pdf import render_report_pdf

        md = (
            "# 標題\n\n## 重點分析\n\n內文[1]。\n\n"
            '```chart\n{"type":"pie","title":"市佔","x":["A","B","C"],'
            '"series":[{"name":"市佔","values":[50,30,20]}],"source":"[1]"}\n```\n'
        )
        pdf = render_report_pdf(md, title="測試", meta={"date": "2026-06-26"})
        self.assertEqual(pdf[:4], b"%PDF")
        self.assertGreater(len(pdf), 1000)


class SplitReportTests(unittest.TestCase):
    def test_drops_preamble_before_title(self):
        from app.services.pdf import split_report

        md = (
            "好的，現在我來進行多面向的網路搜尋，補充材料行業的全面資料。"
            "已取得足夠的網路資料，現在整合所有參考片段與搜尋結果，撰寫完整深度研報。\n\n"
            "# 材料行業深度研報\n\n## 執行摘要\n\n摘要內文[1]。\n\n"
            "## 關鍵發現\n\n1. 發現一[1]。\n\n## 重點分析\n\n分析內文[1]。\n"
        )
        title, sections = split_report(md)
        self.assertEqual(title, "材料行業深度研報")
        self.assertEqual([name for name, _ in sections], ["執行摘要", "關鍵發現", "重點分析"])
        joined = "\n".join(b for _, b in sections)
        self.assertNotIn("好的，現在我來", joined)
        self.assertNotIn("好的，現在我來", title)
        self.assertIn("摘要內文[1]。", sections[0][1])

    def test_no_title_returns_empty(self):
        from app.services.pdf import split_report

        title, sections = split_report("找不到相關資料。")
        self.assertEqual(title, "")
        self.assertEqual(sections, [])

    def test_empty_input_safe(self):
        from app.services.pdf import split_report

        self.assertEqual(split_report(""), ("", []))
        self.assertEqual(split_report(None), ("", []))

    def test_title_but_no_sections(self):
        from app.services.pdf import split_report
        title, sections = split_report("# 只有標題\n\n一些正文但無 ## 章節。")
        self.assertEqual(title, "只有標題")
        self.assertEqual(sections, [])


class FancyLayoutTests(unittest.TestCase):
    DEEP_MD = (
        "# 台灣半導體產業營收與成長分析\n\n"
        "## 執行摘要\n\n產業在 AI 驅動下高速成長[1]。\n\n"
        "## 關鍵發現\n\n1. 台積電規模斷層式領先[1]。\n\n2. 月營收逼近兆元[1]。\n\n"
        "## 重點分析\n\n分析內文[1]。\n\n"
        "## 風險與展望\n\n關注高基期效應[1]。\n\n"
        "## 引用來源\n\n[1] 永豐金證券，《半導體產業月報》，2026-06-01\n"
    )

    def test_deep_report_uses_fancy_layout(self):
        from app.services.pdf import _build_document

        html = _build_document(self.DEEP_MD, title="後備標題", meta={"date": "2026-06-26"})
        self.assertIn('class="cover"', html)
        self.assertIn('class="toc"', html)
        self.assertIn('id="sec-0"', html)
        self.assertIn("台灣半導體產業營收與成長分析", html)  # 用 markdown 內標題，非後備
        self.assertNotIn('class="brand-bar"', html)

    def test_section_slugs_applied(self):
        from app.services.pdf import _build_document

        html = _build_document(self.DEEP_MD, title="x", meta={})
        self.assertIn('class="s-exec"', html)
        self.assertIn('class="s-findings"', html)
        self.assertIn('class="s-refs"', html)

    def test_degenerate_uses_simple_layout(self):
        from app.services.pdf import _build_document

        html = _build_document("# 標題\n\n## 執行摘要\n\n只有一段[1]。", title="x", meta={})
        self.assertIn('class="brand-bar"', html)
        self.assertNotIn('class="cover"', html)

    def test_no_title_uses_simple_layout(self):
        from app.services.pdf import _build_document

        html = _build_document("找不到相關資料。", title="x", meta={})
        self.assertIn('class="brand-bar"', html)
        self.assertNotIn('class="cover"', html)

    def test_fancy_renders_to_pdf(self):
        from app.services.pdf import render_report_pdf

        pdf = render_report_pdf(self.DEEP_MD, title="x", meta={"date": "2026-06-26"})
        self.assertEqual(pdf[:4], b"%PDF")
        self.assertGreater(len(pdf), 1000)


class StripPreambleTests(unittest.TestCase):
    def test_drops_text_before_title(self):
        from app.services.pdf import strip_preamble

        md = (
            "好的，現在我來進行多面向的網路搜尋，補充材料行業資料。已取得足夠資料，"
            "現在整合所有參考片段與搜尋結果，撰寫完整深度研報。\n\n"
            "# 材料行業深度研報\n\n## 執行摘要\n\n內文[1]。\n"
        )
        out = strip_preamble(md)
        self.assertTrue(out.startswith("# 材料行業深度研報"))
        self.assertNotIn("好的，現在我來", out)
        self.assertIn("## 執行摘要", out)

    def test_no_title_unchanged(self):
        from app.services.pdf import strip_preamble

        md = "找不到與本主題相關的研報資料。"
        self.assertEqual(strip_preamble(md), md)

    def test_no_preamble_unchanged(self):
        from app.services.pdf import strip_preamble

        md = "# 標題\n\n## 執行摘要\n\n內文。"
        self.assertEqual(strip_preamble(md), md)

    def test_empty_safe(self):
        from app.services.pdf import strip_preamble

        self.assertEqual(strip_preamble(""), "")
        self.assertEqual(strip_preamble(None), "")


class InjectKpiTests(unittest.TestCase):
    def test_kpi_block_becomes_strip(self):
        from app.services.pdf import inject_kpi

        md = (
            "前言。\n\n```kpi\n"
            '{"items":[{"label":"營收年增","value":"+30.2%","change":"YoY","dir":"up"},'
            '{"label":"毛利率","value":"62.0%"}],"source":"[1]"}\n```\n\n結語。'
        )
        out = inject_kpi(md)
        self.assertIn('class="kpi-strip"', out)
        self.assertIn('class="kpi-value"', out)
        self.assertIn("+30.2%", out)
        self.assertIn("營收年增", out)
        self.assertIn('class="kpi-change up"', out)
        self.assertIn("來源 [1]", out)
        self.assertNotIn("```kpi", out)
        # dir=down → 紅色 class
        down = inject_kpi(
            '```kpi\n{"items":[{"label":"記憶體","value":"-9%","change":"YoY","dir":"down"}]}\n```'
        )
        self.assertIn('class="kpi-change down"', down)
        self.assertIn("前言。", out)
        self.assertIn("結語。", out)

    def test_bad_kpi_block_dropped(self):
        from app.services.pdf import inject_kpi

        self.assertNotIn("kpi-strip", inject_kpi("a\n\n```kpi\n{壞}\n```\n\nb"))
        self.assertNotIn("kpi-strip", inject_kpi('a\n\n```kpi\n{"items":[]}\n```\n\nb'))

    def test_no_kpi_unchanged(self):
        from app.services.pdf import inject_kpi

        md = "## 標題\n\n一般內文[1]。"
        self.assertEqual(inject_kpi(md), md)


class CiteBadgesTests(unittest.TestCase):
    def test_single_and_multi(self):
        from app.services.pdf import cite_badges

        self.assertEqual(cite_badges("成長[1]。"), '成長<sup class="cite">1</sup>。')
        self.assertIn('<sup class="cite">1,2</sup>', cite_badges("見[1,2]"))
        self.assertIn('<sup class="cite">1，2</sup>', cite_badges("見[1，2]"))  # 全形逗號
        self.assertIn('<sup class="cite">1、3</sup>', cite_badges("見[1、3]"))  # 頓號

    def test_non_citation_untouched(self):
        from app.services.pdf import cite_badges

        self.assertEqual(cite_badges("陣列 a[i] 與文字"), "陣列 a[i] 與文字")


class NormalizeRefsTests(unittest.TestCase):
    def test_consecutive_refs_split(self):
        from app.services.pdf import _normalize_refs

        out = _normalize_refs("[1] 甲\n[2] 乙\n[3] 丙")
        self.assertEqual(out, "[1] 甲\n\n[2] 乙\n\n[3] 丙")

    def test_already_spaced_idempotent(self):
        from app.services.pdf import _normalize_refs

        out = _normalize_refs("[1] 甲\n\n[2] 乙")
        self.assertEqual(out, "[1] 甲\n\n[2] 乙")


class ContentPipelineTests(unittest.TestCase):
    MD = (
        "# 台積電 2026 展望\n\n"
        "## 執行摘要\n\n結論[1]。\n\n"
        "```kpi\n{\"items\":[{\"label\":\"營收年增\",\"value\":\"+30.2%\",\"dir\":\"up\"}],\"source\":\"[1]\"}\n```\n\n"
        "> 關鍵觀點一句[1]。\n\n"
        "## 關鍵發現\n\n1. 發現[1]。\n\n"
        "## 重點分析\n\n分析[1]。\n\n"
        "## 風險與展望\n\n風險[1]。\n\n"
        "## 引用來源\n\n[1] 統一證券，《報告》，2026-06-19\n[2] 群益投顧，《月報》，2026-06-04\n"
    )

    def test_fancy_pipeline_html(self):
        from app.services.pdf import _build_document

        html = _build_document(self.MD, title="x", meta={"date": "2026-06-28"})
        self.assertIn('class="kpi-strip"', html)            # KPI 注入
        self.assertIn("<blockquote>", html)                 # callout
        self.assertIn('<sup class="cite">1</sup>', html)    # 內文徽章
        # 引用來源段：[1] 維持純文字（不轉徽章），且兩條各自成段
        self.assertIn("[1] 統一證券", html)
        self.assertIn("[2] 群益投顧", html)
        self.assertNotIn('<sup class="cite">1</sup> 統一證券', html)

    def test_fancy_pipeline_renders_pdf(self):
        from app.services.pdf import render_report_pdf

        pdf = render_report_pdf(self.MD, title="x", meta={"date": "2026-06-28"})
        self.assertEqual(pdf[:4], b"%PDF")
        self.assertGreater(len(pdf), 1000)
