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
