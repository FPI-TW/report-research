# tests/test_extraction_layout.py
"""版面分析對**真 PDF** 的行為。PDF 在測試內即時生成（WeasyPrint），不簽入語料。

## 為什麼不簽入真實研報當 fixture

那是券商授權材料，repo 遠端是 GitHub。即時生成的 PDF 換來的是「版面是我們
自己指定的」——斷言才有意義：拿真研報只能斷言「抽出來的字數大於零」，那種
測試擋不住任何東西。

## 雙欄那組的設計

`_TWO_COL_HTML` **刻意把右欄的 HTML 放在左欄之前**。WeasyPrint 依 DOM 順序
寫 content stream，所以那份 PDF 的字元順序是「右欄→左欄」——正是
`pypdf.extract_text()` 會照單全收的順序。版面感知抽取器必須**逆著 content
stream** 輸出左欄→右欄才算對。沒有這個倒置，測試會在抽取器完全不做欄偵測
時照樣通過。

## 字型閘

結構性斷言（欄序、表格、頁首頁尾）一律用 ASCII，**完全不依賴 CJK 字型**。
只有「中文抽得回來」那一條需要字型，語意比照 `test_rendered_pdf_content.py`：
設了 `REPORT_MARK_REQUIRE_CJK` 就不准 skip。
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services.extraction.layout import detect_columns, extract_document  # noqa: E402
from app.services.extraction.model import serialize  # noqa: E402

_A4 = "@page { size: A4; margin: 2cm; }"

# 右欄在前、左欄在後——見模組 docstring。
_TWO_COL_HTML = f"""<html><head><meta charset="utf-8"><style>{_A4}
body{{font-family:sans-serif;font-size:11pt;margin:0}}
.col{{position:absolute;top:0;width:44%}}
.right{{left:56%}} .left{{left:0}}
p{{margin:0 0 10pt}}
</style></head><body>
<div class="col right">
{"".join(f"<p>RIGHT block number {i} with enough words to be a paragraph.</p>" for i in range(12))}
</div>
<div class="col left">
{"".join(f"<p>LEFT block number {i} with enough words to be a paragraph.</p>" for i in range(12))}
</div>
</body></html>"""

# 段落長度刻意參差。**第一版讓 30 段一字不差地重複，那是不真實的最壞情況**：
# 每一行的字都落在完全相同的 x 位置，於是 `vertical_strategy: "text"` 把整段
# 散文切成了 10 欄的假表格（實測，見 layout.py 的空列守衛）。真實散文是不
# 齊的，但那個 bug 是真的，所以另有 `ProseIsNotATableTests` 盯著。
_ONE_COL_HTML = f"""<html><head><meta charset="utf-8"><style>{_A4}
body{{font-family:sans-serif;font-size:11pt}}</style></head><body>
{"".join(
    f"<p>Single column paragraph number {i} "
    + " ".join(f"word{j}" for j in range(4 + (i * 7) % 23))
    + ".</p>"
    for i in range(30)
)}
</body></html>"""

# 完全齊頭的重複散文——文字策略表格偵測的病理輸入。
_UNIFORM_PROSE_HTML = f"""<html><head><meta charset="utf-8"><style>{_A4}
body{{font-family:sans-serif;font-size:11pt}}</style></head><body>
{"".join(f"<p>Single column paragraph number {i} with several words in it.</p>" for i in range(30))}
</body></html>"""

_TABLE_HTML = f"""<html><head><meta charset="utf-8"><style>{_A4}
body{{font-family:sans-serif;font-size:11pt}}
table{{border-collapse:collapse;width:100%}}
td,th{{border:1px solid #000;padding:4pt 8pt}}</style></head><body>
<p>Intro paragraph before the table with some words.</p>
<table>
<tr><th>Ticker</th><th>Rating</th><th>Target</th></tr>
<tr><td>TSMC</td><td>Buy</td><td>1200</td></tr>
<tr><td>UMC</td><td>Hold</td><td>55</td></tr>
<tr><td>MediaTek</td><td>Buy</td><td>1450</td></tr>
</table>
<p>Closing paragraph after the table with some words.</p>
</body></html>"""

_HDR_FTR_HTML = """<html><head><meta charset="utf-8"><style>
@page { size: A4; margin: 2cm;
  @top-center { content: "BROKER TEMPLATE HEADER"; font-family: sans-serif; font-size: 9pt; }
  @bottom-center { content: "TEMPLATE DISCLAIMER FOOTER"; font-family: sans-serif; font-size: 9pt; }
}
body{font-family:sans-serif;font-size:11pt}
.pb{break-after:page}
</style></head><body>
<div class="pb"><p>UNIQUEBODYONE paragraph on the first page with several words.</p></div>
<div class="pb"><p>UNIQUEBODYTWO paragraph on the second page with several words.</p></div>
<div><p>UNIQUEBODYTHREE paragraph on the third page with several words.</p></div>
</body></html>"""

_CJK_HTML = f"""<html><head><meta charset="utf-8"><style>{_A4}
body{{font-family:"Noto Sans CJK TC","Noto Sans TC",sans-serif;font-size:11pt}}</style></head><body>
<p>台積電第三季營收優於預期，毛利率維持在五成以上。</p>
<p>先進製程佔比持續提升，本次調升目標價至一千二百元。</p>
</body></html>"""


def _pdf(html: str) -> Path:
    from weasyprint import HTML

    fd, name = tempfile.mkstemp(suffix=".pdf")
    os.close(fd)
    path = Path(name)
    path.write_bytes(HTML(string=html).write_pdf())
    return path


class _PdfCase(unittest.TestCase):
    """每個 case 自己生 PDF 並在結束時刪掉。"""

    def make(self, html: str) -> Path:
        path = _pdf(html)
        self.addCleanup(path.unlink, missing_ok=True)
        return path


class ColumnOrderTests(_PdfCase):
    def test_two_column_reading_order_beats_content_stream(self):
        doc = extract_document(self.make(_TWO_COL_HTML))
        text = serialize(doc)
        self.assertIn("LEFT block number 0", text)
        self.assertIn("RIGHT block number 0", text)
        # 左欄整段必須排在右欄整段之前
        self.assertLess(
            text.index("LEFT block number 11"),
            text.index("RIGHT block number 0"),
            "右欄內容插進了左欄中間——欄偵測或排序壞了",
        )

    def test_two_column_layout_is_detected(self):
        doc = extract_document(self.make(_TWO_COL_HTML))
        self.assertEqual(max(b.column for b in doc.blocks()), 1)

    def test_single_column_reports_one_column(self):
        doc = extract_document(self.make(_ONE_COL_HTML))
        self.assertEqual(max(b.column for b in doc.blocks()), 0)

    def test_single_column_order_is_top_to_bottom(self):
        text = serialize(extract_document(self.make(_ONE_COL_HTML)))
        self.assertLess(text.index("number 0 "), text.index("number 29 "))

    def test_no_paragraph_is_lost(self):
        """段落編號 0-29 一個都不能少。"""
        text = serialize(extract_document(self.make(_ONE_COL_HTML)))
        missing = [i for i in range(30) if f"number {i} " not in text]
        self.assertEqual([], missing, f"這些段落在序列化後不見了：{missing}")


class ProseIsNotATableTests(_PdfCase):
    """`vertical_strategy: "text"` 會把齊頭的散文誤判成表格——實測過。

    症狀不是「多一個 table block」而已：那三段散文的文字會被拆成一格一個字，
    序列化成 `| Single | column | paragraph | number | 27 |`，於是任何搜尋
    「number 27」的人都找不到它。**內容沒有消失，但變得檢索不到**，而且不會
    有任何錯誤訊息。守門靠 layout.py 的 `_TEXT_TABLE_MAX_EMPTY_ROW_FRAC`。
    """

    def test_uniform_prose_does_not_become_a_table(self):
        doc = extract_document(self.make(_UNIFORM_PROSE_HTML))
        tables = [b for b in doc.blocks() if b.type == "table"]
        self.assertEqual([], tables, "齊頭散文被誤判成表格")

    def test_uniform_prose_keeps_every_paragraph_searchable(self):
        text = serialize(extract_document(self.make(_UNIFORM_PROSE_HTML)))
        missing = [i for i in range(30) if f"paragraph number {i} with" not in text]
        self.assertEqual([], missing, f"這些段落被拆散了：{missing}")


class DetectColumnsUnitTests(unittest.TestCase):
    """`detect_columns` 的守衛條件——它們錯了不會拋例外，只會讓欄序悄悄失效。"""

    @staticmethod
    def _w(x0: float, x1: float, top: float) -> dict:
        return {"x0": x0, "x1": x1, "top": top, "bottom": top + 10}

    def test_too_few_words_returns_single_column(self):
        words = [self._w(0, 50, 200 + i * 12) for i in range(5)]
        self.assertEqual(detect_columns(words, 595.0, 842.0), [])

    def test_ignores_words_outside_body_band(self):
        """頁首橫跨溝槽時仍要判得出雙欄——這是最容易犯的錯。"""
        body = [self._w(40, 250, 200 + i * 12) for i in range(40)]
        body += [self._w(340, 550, 200 + i * 12) for i in range(40)]
        banner = [self._w(40, 550, 20)]  # 跨欄頁首，落在版心帶之外
        self.assertEqual(len(detect_columns(body + banner, 595.0, 842.0)), 1)

    def test_narrow_rating_column_is_merged_into_its_list(self):
        """元大投資早報首頁：左側清單的「評等」欄只有 20pt 寬，詞數卻夠多。

        它不是一欄、是清單裡的一個欄位——整頁應判成兩欄（清單 vs 目次），
        窄欄併回左邊空隙較小的那一欄，而不是切成三欄或整頁退回單欄。
        """
        names = [self._w(74, 190 if i % 2 else 140, 260 + i * 20) for i in range(40)]  # 長短名混雜，右緣到 190
        ratings = [self._w(217, 237, 260 + i * 20) for i in range(40)]
        toc = [self._w(300, 560, 240 + i * 16) for i in range(40)]
        gutters = detect_columns(names + ratings + toc, 595.0, 842.0)
        self.assertEqual(len(gutters), 1, gutters)
        self.assertTrue(237 < gutters[0] < 300, gutters)

    def test_lopsided_split_is_rejected(self):
        """一側只有幾個詞時，那條空白帶是置中的圖不是欄界。"""
        left = [self._w(40, 250, 200 + i * 12) for i in range(58)]
        right = [self._w(340, 550, 200 + i * 12) for i in range(2)]
        self.assertEqual(detect_columns(left + right, 595.0, 842.0), [])


class TableTests(_PdfCase):
    def test_table_becomes_table_block(self):
        doc = extract_document(self.make(_TABLE_HTML))
        tables = [b for b in doc.blocks() if b.type == "table"]
        self.assertTrue(tables, "有框線的表格沒有被認出來")
        flat = " ".join(c for r in tables[0].table_cells for c in r)
        for token in ("Ticker", "TSMC", "1200", "MediaTek"):
            self.assertIn(token, flat)

    def test_table_serialises_with_visible_delimiters(self):
        """欄界必須是可見字元，否則入庫時會被 `_RE_CJK_GAP` 抹掉。"""
        text = serialize(extract_document(self.make(_TABLE_HTML)))
        self.assertIn("|", text)
        self.assertIn("| --- |", text)

    def test_surrounding_paragraphs_survive(self):
        text = serialize(extract_document(self.make(_TABLE_HTML)))
        self.assertIn("Intro paragraph", text)
        self.assertIn("Closing paragraph", text)


class HeaderFooterTests(_PdfCase):
    def test_repeated_template_is_classified_and_dropped(self):
        doc = extract_document(self.make(_HDR_FTR_HTML))
        types = {b.type for b in doc.blocks()}
        self.assertIn("header", types, "跨頁重複的頁首沒有被認出來")
        self.assertIn("footer", types, "跨頁重複的頁尾沒有被認出來")

        text = serialize(doc)
        # 頁首帶的重複文字**第一次出現保留**：券商研報的文件標題常同時是後續每頁的
        # 頁眉，全部丟掉會連首頁真標題一起丟（E0 golden set 實測漏 2 句）。
        self.assertEqual(text.count("BROKER TEMPLATE HEADER"), 1, "頁首重複文字應保留第一份、丟掉其餘")
        self.assertNotIn("TEMPLATE DISCLAIMER FOOTER", text)
        first_page_types = [b.type for b in doc.pages[0].blocks if "TEMPLATE HEADER" in b.text]
        self.assertTrue(first_page_types and all(t != "header" for t in first_page_types), first_page_types)
        later = [b.type for pg in doc.pages[1:] for b in pg.blocks if "TEMPLATE HEADER" in b.text]
        self.assertTrue(later and all(t == "header" for t in later), later)

    def test_unique_body_is_never_dropped(self):
        """只出現一次的內容不可以因為位置而被當成樣板丟掉。"""
        text = serialize(extract_document(self.make(_HDR_FTR_HTML)))
        for token in ("UNIQUEBODYONE", "UNIQUEBODYTWO", "UNIQUEBODYTHREE"):
            self.assertIn(token, text)

    def test_dropped_blocks_remain_in_the_model(self):
        """丟的是序列化，不是資料——否則之後想調門檻就得重抽。"""
        doc = extract_document(self.make(_HDR_FTR_HTML))
        self.assertIn("BROKER TEMPLATE HEADER", serialize(doc, drop=()))


class DeterminismTests(_PdfCase):
    def test_same_pdf_serialises_identically(self):
        """`E1` 驗收條款：同一份 PDF 兩次執行結果逐字元相同。"""
        path = self.make(_TABLE_HTML)
        self.assertEqual(serialize(extract_document(path)), serialize(extract_document(path)))

    def test_page_count_matches(self):
        doc = extract_document(self.make(_HDR_FTR_HTML))
        self.assertEqual(doc.page_count, 3)
        self.assertEqual(doc.pages_failed, ())


class BrokenInputTests(unittest.TestCase):
    def test_unopenable_file_returns_error_not_exception(self):
        """整份開不起來時要回帶 error 的空 Document，不可以往上拋——
        批次一拋就整批中斷，而那正是現況『單檔壞掉不影響整批』的契約。"""
        fd, name = tempfile.mkstemp(suffix=".pdf")
        os.close(fd)
        path = Path(name)
        path.write_bytes(b"not a pdf at all")
        self.addCleanup(path.unlink, missing_ok=True)

        doc = extract_document(path)
        self.assertTrue(doc.error)
        self.assertEqual(doc.page_count, 0)


_NO_CJK = "本環境無法從 PDF 抽回中文（缺 CJK 字型，例如 fonts-noto-cjk）"


class CjkTests(_PdfCase):
    def _probe(self) -> tuple[bool, str | None]:
        try:
            doc = extract_document(self.make(_CJK_HTML))
            if doc.error:
                return (False, doc.error)
            return ("台積電" in serialize(doc), None)
        except Exception as exc:  # 探針自己壞了，與字型無關
            return (False, f"{type(exc).__name__}: {exc}")

    def test_cjk_text_round_trips(self):
        ok, err = self._probe()
        if not ok:
            if err is not None:
                self.fail(f"CJK 抽字探針本身失敗（**不是**字型問題）：{err}")
            if os.getenv("REPORT_MARK_REQUIRE_CJK"):
                self.fail(_NO_CJK)
            self.skipTest(_NO_CJK)

        text = serialize(extract_document(self.make(_CJK_HTML)))
        self.assertIn("毛利率", text)
        self.assertIn("目標價", text)


if __name__ == "__main__":
    unittest.main()
