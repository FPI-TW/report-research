# tests/test_extraction_model.py
"""文件模型與序列化的契約。全部用合成 Block，不碰真 PDF、不依賴字型。

守的是三件會靜默出錯的事：

1. **序列化的決定性。** `report_takeaway.text_sha256` 是對
   `clean_extracted(full_text)` 的驗章；同一份文件序列化出兩種字串，驗章
   就變成隨機紅燈，而摘錄批次會據此無止境重跑。
2. **markdown 表格穿得過 `clean_extracted`。** 這是整個「表格改走 markdown」
   設計的地基（docs/EXTRACTION_REDESIGN.md §4.2）。它若不成立，表格欄界
   仍然會在入庫時被 `_RE_CJK_GAP` 抹掉——而且不會有任何錯誤訊息。
3. **Block 的全序排序。** 少了 tie-break，兩個 bbox 相同的 Block 順序會取決
   於它們進 list 的先後，於是同一份 PDF 兩次跑出不同的 full_text。
"""
import re
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services.extraction.model import (  # noqa: E402
    Block,
    Document,
    Page,
    serialize,
    table_to_markdown,
)
from app.services.textnorm import clean_extracted  # noqa: E402


def _doc(*pages: Page) -> Document:
    return Document("test", "v0", tuple(pages))


class TableMarkdownTests(unittest.TestCase):
    def test_header_separator_always_emitted(self):
        md = table_to_markdown((("公司", "評等"), ("台積電", "買進")))
        self.assertEqual(
            md.splitlines(),
            ["| 公司 | 評等 |", "| --- | --- |", "| 台積電 | 買進 |"],
        )

    def test_short_rows_are_padded(self):
        """欄數以最寬的一列為準。短列不補會讓 markdown 表格渲染錯位。"""
        md = table_to_markdown((("a", "b", "c"), ("x",)))
        self.assertEqual(md.splitlines()[-1], "| x |  |  |")

    def test_pipe_in_cell_is_escaped(self):
        """儲存格內的 `|` 不跳脫會讓欄數錯亂——而錯亂不會報錯。

        數的是**未跳脫**的分隔符：`\\|` 本身仍是一個 `|` 字元，直接 count 會
        把它算進去（本測試第一版就是這樣寫錯的）。
        """
        md = table_to_markdown((("a|b", "c"),))
        self.assertIn(r"a\|b", md)
        structural = len(re.findall(r"(?<!\\)\|", md.splitlines()[0]))
        self.assertEqual(structural, 3)

    def test_newline_in_cell_becomes_space(self):
        """儲存格內的換行會被 chunk_text 的段落切分當成段界。"""
        md = table_to_markdown((("a\nb", "c"),))
        self.assertNotIn("\n", md.splitlines()[0])

    def test_none_cell_becomes_empty(self):
        self.assertIn("|  |", table_to_markdown(((None, "c"),)))


class CleanExtractedSurvivalTests(unittest.TestCase):
    """整個表格序列化設計的地基：欄界要穿得過入庫清理。"""

    def test_cjk_table_keeps_column_boundaries(self):
        md = table_to_markdown((("公司", "評等", "目標價"), ("台積電", "買進", "1200")))
        cleaned = clean_extracted(md)
        # `_RE_CJK_GAP` 只吃空白，可見分隔符會留下來
        self.assertIn("|", cleaned)
        self.assertEqual(cleaned.count("|"), md.count("|"))
        for token in ("公司", "評等", "目標價", "台積電", "買進", "1200"):
            self.assertIn(token, cleaned)

    def test_space_aligned_table_loses_boundaries(self):
        """對照組：這正是現況的死法，也是為什麼不能用空白對齊。"""
        aligned = "公司    評等    目標價\n台積電    買進    1200"
        cleaned = clean_extracted(aligned)
        self.assertIn("公司評等目標價", cleaned)

    def test_clean_extracted_is_idempotent_on_markdown(self):
        md = table_to_markdown((("公司", "評等"), ("台積電", "買進")))
        once = clean_extracted(md)
        self.assertEqual(once, clean_extracted(once))


class SerializeTests(unittest.TestCase):
    def test_deterministic_across_runs(self):
        blocks = tuple(
            Block("paragraph", 1, i, (10.0, 10.0 * i, 100.0, 10.0 * i + 8), f"p{i}")
            for i in range(6)
        )
        doc = _doc(Page(1, 595.0, 842.0, blocks))
        self.assertEqual(serialize(doc), serialize(doc))

    def test_header_and_footer_dropped_by_default(self):
        doc = _doc(
            Page(
                1,
                595.0,
                842.0,
                (
                    Block("header", 1, 0, (0.0, 0.0, 595.0, 20.0), "券商樣板頁首"),
                    Block("paragraph", 1, 1, (0.0, 100.0, 595.0, 120.0), "正文"),
                    Block("footer", 1, 2, (0.0, 800.0, 595.0, 820.0), "免責頁尾"),
                ),
            )
        )
        out = serialize(doc)
        self.assertEqual(out, "正文")
        # 但它們沒有從模型裡消失——只是沒被序列化
        self.assertEqual(len(doc.blocks()), 3)
        self.assertIn("券商樣板頁首", serialize(doc, drop=()))

    def test_blocks_separated_by_blank_line(self):
        """`chunk_text` 依 `\\n\\s*\\n` 切段；用單一換行會讓整頁併成一段。"""
        doc = _doc(
            Page(
                1,
                595.0,
                842.0,
                (
                    Block("paragraph", 1, 0, (0.0, 0.0, 10.0, 10.0), "甲"),
                    Block("paragraph", 1, 1, (0.0, 20.0, 10.0, 30.0), "乙"),
                ),
            )
        )
        self.assertEqual(serialize(doc), "甲\n\n乙")

    def test_empty_blocks_are_skipped(self):
        doc = _doc(
            Page(
                1,
                595.0,
                842.0,
                (
                    Block("paragraph", 1, 0, (0.0, 0.0, 10.0, 10.0), "  "),
                    Block("paragraph", 1, 1, (0.0, 20.0, 10.0, 30.0), "有內容"),
                ),
            )
        )
        self.assertEqual(serialize(doc), "有內容")


class OrderingTests(unittest.TestCase):
    def test_column_before_vertical_position(self):
        """雙欄：右欄第一段不可以排在左欄最後一段之前。"""
        left_bottom = Block("paragraph", 1, 9, (50.0, 700.0, 250.0, 720.0), "LEFT-LAST", column=0)
        right_top = Block("paragraph", 1, 0, (320.0, 100.0, 520.0, 120.0), "RIGHT-FIRST", column=1)
        doc = _doc(Page(1, 595.0, 842.0, (right_top, left_bottom)))
        self.assertEqual(serialize(doc), "LEFT-LAST\n\nRIGHT-FIRST")

    def test_page_order_wins_over_position(self):
        p2 = Block("paragraph", 2, 0, (0.0, 0.0, 10.0, 10.0), "第二頁")
        p1 = Block("paragraph", 1, 0, (0.0, 800.0, 10.0, 810.0), "第一頁")
        doc = _doc(Page(1, 595.0, 842.0, (p1,)), Page(2, 595.0, 842.0, (p2,)))
        self.assertEqual(serialize(doc), "第一頁\n\n第二頁")

    def test_identical_bbox_breaks_tie_deterministically(self):
        """bbox 完全相同（浮水印疊在正文上）時，順序仍必須是決定性的。"""
        bbox = (10.0, 10.0, 100.0, 20.0)
        a = Block("paragraph", 1, 1, bbox, "B")
        b = Block("paragraph", 1, 0, bbox, "A")
        self.assertEqual(serialize(_doc(Page(1, 595.0, 842.0, (a, b)))), "A\n\nB")
        self.assertEqual(serialize(_doc(Page(1, 595.0, 842.0, (b, a)))), "A\n\nB")


class FailedPageTests(unittest.TestCase):
    def test_failed_pages_are_recorded_not_dropped(self):
        """現況 `except Exception: continue` 讓整頁無聲消失（診斷 #2）。"""
        doc = _doc(
            Page(1, 595.0, 842.0, (Block("paragraph", 1, 0, (0.0, 0.0, 1.0, 1.0), "ok"),)),
            Page(2, 595.0, 842.0, (), failed=True, error="boom"),
            Page(3, 595.0, 842.0, (Block("paragraph", 3, 0, (0.0, 0.0, 1.0, 1.0), "ok3"),)),
        )
        self.assertEqual(doc.page_count, 3)
        self.assertEqual(doc.pages_failed, (2,))


if __name__ == "__main__":
    unittest.main()
