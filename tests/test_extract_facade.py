"""app/services/extract.py 門面（E1a）：簽章不變、預設 pypdf、pdfplumber 路徑與退回規則。

生成 PDF 用 weasyprint（同 tests/test_extraction_layout.py）。**不碰語料、不碰 DB。**
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.services import extract as ex
from app.services.extraction import EXTRACTION_VERSION

_HTML = """<html><head><meta charset="utf-8"><style>
@page { size: A4; margin: 2cm; } body{font-family:sans-serif;font-size:11pt}
table{border-collapse:collapse} td{border:1px solid #000;padding:2px 8px}
</style></head><body>
<h1>FACADE TITLE ONE</h1>
<p>Paragraph alpha with enough words to be counted as a real paragraph of text here.</p>
<p>Paragraph beta with enough words to be counted as a real paragraph of text here too.</p>
<table><tr><td>公司</td><td>評等</td><td>目標價</td></tr><tr><td>台積電</td><td>買進</td><td>1200</td></tr>
<tr><td>聯發科</td><td>持有</td><td>900</td></tr><tr><td>鴻海</td><td>買進</td><td>250</td></tr></table>
<p>Paragraph gamma closes the page with a few more words for good measure and length.</p>
</body></html>"""


def _pdf(html: str) -> Path:
    from weasyprint import HTML

    fd, name = tempfile.mkstemp(suffix=".pdf")
    os.close(fd)
    Path(name).write_bytes(HTML(string=html).write_pdf())
    return Path(name)


class _Case(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pdf = _pdf(_HTML)

    @classmethod
    def tearDownClass(cls):
        cls.pdf.unlink(missing_ok=True)


class DefaultPathTests(_Case):
    def test_default_is_pypdf_and_shape_is_unchanged(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("EXTRACTOR", None)
            res = ex.extract_text(self.pdf)
        self.assertEqual(res.extractor, "pypdf")
        self.assertTrue(res.extraction_version.startswith("pypdf-"))
        self.assertIn("FACADE TITLE ONE", res.text)
        self.assertFalse(res.scanned)
        self.assertEqual(res.page_count, 1)
        self.assertEqual(res.pages_failed, ())
        self.assertEqual(res.blocks, ())
        self.assertEqual(len(res.file_hash), 64)

    def test_pypdf_records_failed_pages_instead_of_swallowing(self):
        """診斷 #2：失敗的頁仍跳過，但頁碼要留下來。"""

        class BoomPage:
            def extract_text(self):
                raise RuntimeError("boom")

        class GoodPage:
            def extract_text(self):
                return "fine page text " * 20

        class FakeReader:
            def __init__(self, _):
                self.pages = [GoodPage(), BoomPage(), GoodPage()]

        with mock.patch.object(ex, "PdfReader", FakeReader):
            res = ex.extract_text(self.pdf, extractor="pypdf")
        self.assertEqual(res.pages_failed, (2,))
        self.assertEqual(res.page_count, 3)
        self.assertAlmostEqual(res.quality["pages_failed_ratio"], 1 / 3, places=3)
        self.assertFalse(res.scanned)


class PdfplumberPathTests(_Case):
    def test_layout_path_returns_version_blocks_and_quality(self):
        res = ex.extract_text(self.pdf, extractor="pdfplumber")
        self.assertEqual(res.extractor, "pdfplumber")
        self.assertEqual(res.extraction_version, EXTRACTION_VERSION)
        self.assertIn("FACADE TITLE ONE", res.text)
        self.assertEqual(res.page_count, 1)
        self.assertTrue(res.blocks, "Block 索引不得為空")
        types = {b[0] for b in res.blocks}
        self.assertIn("paragraph", types)
        # 索引與文字同源：區間切出來就是該 Block 的文字，首尾不得帶換行（位移的第一個症狀）
        for _type, _page, start, end in res.blocks:
            seg = res.text[start:end]
            self.assertTrue(seg.strip())
            self.assertEqual(seg, seg.strip("\n"))
        self.assertEqual(res.blocks[0][2], 0)
        self.assertEqual(res.blocks[-1][3], len(res.text))
        self.assertIn("quality_score", res.quality)
        self.assertNotIn("fallback_from", res.quality)

    def test_table_block_is_indexed_and_serialised_with_pipes(self):
        res = ex.extract_text(self.pdf, extractor="pdfplumber")
        tables = [b for b in res.blocks if b[0] == "table"]
        self.assertTrue(tables, "表格沒有被認出來")
        _type, _page, start, end = tables[0]
        self.assertIn("|", res.text[start:end])
        self.assertIn("台積電", res.text[start:end])

    def test_exception_falls_back_to_pypdf_and_records_why(self):
        with mock.patch("app.services.extraction.layout.extract_document", side_effect=RuntimeError("kaput")):
            res = ex.extract_text(self.pdf, extractor="pdfplumber")
        self.assertEqual(res.extractor, "pypdf")
        self.assertEqual(res.quality.get("fallback_from"), "pdfplumber")
        self.assertIn("kaput", res.quality.get("fallback_reason", ""))
        self.assertIn("FACADE TITLE ONE", res.text)

    def test_empty_output_falls_back_but_more_text_does_not(self):
        """字數低於 MIN_TEXT_CHARS 才退回；不是「哪邊字多用哪邊」。"""
        from app.services.extraction.model import Document

        empty = Document(extractor="pdfplumber", extraction_version=EXTRACTION_VERSION, pages=())
        with mock.patch("app.services.extraction.layout.extract_document", return_value=empty):
            res = ex.extract_text(self.pdf, extractor="pdfplumber")
        self.assertEqual(res.extractor, "pypdf")
        self.assertEqual(res.quality.get("fallback_reason"), "below_min_chars")

    def test_unknown_suffix_is_scanned(self):
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            f.write(b"\xff\xd8\xff")
            p = Path(f.name)
        try:
            res = ex.extract_text(p, extractor="pdfplumber")
        finally:
            p.unlink(missing_ok=True)
        self.assertTrue(res.scanned)
        self.assertEqual(res.text, "")


class DocxTests(unittest.TestCase):
    def _docx(self) -> Path:
        import docx

        d = docx.Document()
        d.add_paragraph("第一段 台積電 展望")
        d.add_paragraph("")
        d.add_paragraph("第二段 聯發科 評等")
        fd, name = tempfile.mkstemp(suffix=".docx")
        os.close(fd)
        d.save(name)
        return Path(name)

    def test_docx_under_layout_mode_is_block_model(self):
        p = self._docx()
        try:
            res = ex.extract_text(p, extractor="pdfplumber")
        finally:
            p.unlink(missing_ok=True)
        self.assertEqual(res.extractor, "python-docx")
        self.assertEqual([b[0] for b in res.blocks], ["paragraph", "paragraph"])
        self.assertIn("第一段 台積電 展望\n\n第二段 聯發科 評等", res.text)

    def test_docx_under_pypdf_mode_keeps_single_newline_join(self):
        p = self._docx()
        try:
            res = ex.extract_text(p, extractor="pypdf")
        finally:
            p.unlink(missing_ok=True)
        self.assertEqual(res.extractor, "python-docx")
        self.assertIn("第一段 台積電 展望\n\n第二段 聯發科 評等", res.text)  # 空段落折成一個空行


if __name__ == "__main__":
    unittest.main()
