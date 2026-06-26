import sys
import unittest
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

pytest.importorskip("weasyprint")  # 缺原生庫的環境略過（部署機必裝）


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
