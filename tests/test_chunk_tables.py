# tests/test_chunk_tables.py
"""`chunk_text` 的表格分支（v4）：markdown 表格依列切、每塊帶表頭、不套 overlap。

散文分支的不變量在 `tests/test_chunk.py`（尤其 overlap 合併與 head-drop 補償），這裡只釘
表格：它在 v3 是「超長段落走滑動視窗」，實測全庫 20,155 個以 `|` 開頭的 chunk 有 18,462 個
沒有表頭、還有前一塊尾巴黏在表頭前的「…彙總| | 2020 …」。
"""
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services.chunk import _is_table_para, chunk_text  # noqa: E402

HEADER = "| 指數 | 收盤 | 漲跌 | 漲跌幅(%) | YTD(%) |"
SEP = "| --- | --- | --- | --- | --- |"


def _table(n_rows: int) -> str:
    rows = [f"| 指數{i:02d} | {50000 + i * 7:,}.35 | {600 + i}.5 | 1.{i % 9} | {i % 40}.0 |" for i in range(n_rows)]
    return "\n".join([HEADER, SEP, *rows])


class TableDetectionTests(unittest.TestCase):
    def test_all_pipe_lines_is_a_table(self):
        self.assertTrue(_is_table_para(_table(3)))

    def test_prose_and_single_pipe_line_are_not(self):
        self.assertFalse(_is_table_para("台積電 | 買進"))
        self.assertFalse(_is_table_para("第一行\n| 只有第二行像表格 |"))


class TableChunkingTests(unittest.TestCase):
    def test_every_piece_starts_with_the_header(self):
        out = chunk_text(_table(40), size=600, overlap=80)
        self.assertGreater(len(out), 1)
        for c in out:
            with self.subTest(c=c[:40]):
                self.assertTrue(c.startswith(HEADER + "\n" + SEP + "\n"))

    def test_rows_are_never_split_and_each_appears_once(self):
        table = _table(40)
        out = chunk_text(table, size=600, overlap=80)
        body_rows = table.split("\n")[2:]
        seen = "\n".join(c.split("\n", 2)[2] for c in out).split("\n")
        self.assertEqual(seen, body_rows)

    def test_pieces_respect_size_when_rows_fit(self):
        for c in chunk_text(_table(40), size=600, overlap=80):
            with self.subTest(c=c[:40]):
                self.assertLessEqual(len(c), 600)

    def test_single_oversize_row_is_not_split(self):
        row = "| " + " | ".join(["x" * 50] * 20) + " |"
        out = chunk_text("\n".join([HEADER, SEP, row]), size=600, overlap=80)
        self.assertEqual(len(out), 1)
        self.assertIn(row, out[0])

    def test_header_without_separator_row_is_still_repeated(self):
        rows = [f"| a{i} | b{i} |" for i in range(200)]
        out = chunk_text("\n".join(["| A | B |", *rows]), size=200, overlap=0)
        self.assertGreater(len(out), 1)
        for c in out:
            self.assertTrue(c.startswith("| A | B |\n"))


class TableOverlapTests(unittest.TestCase):
    def test_prose_tail_is_not_glued_onto_a_table(self):
        prose = "資料來源：Bloomberg；凱基。" * 8
        text = f"{prose}\n\n{_table(5)}"
        out = chunk_text(text, size=600, overlap=80)
        table_chunks = [c for c in out if c.startswith("|")]
        self.assertEqual(len(table_chunks), 1)
        self.assertTrue(table_chunks[0].startswith(HEADER))

    def test_table_tail_is_not_glued_onto_following_prose(self):
        prose = "圖 8 之後的正文。" * 10
        text = f"{_table(5)}\n\n{prose}"
        out = chunk_text(text, size=600, overlap=80)
        self.assertEqual(out[-1], prose.strip())

    def test_prose_to_prose_overlap_is_unchanged(self):
        a, b = "甲" * 50, "乙" * 50
        out = chunk_text(f"{a}\n\n{b}", size=60, overlap=10)
        self.assertEqual(out[1], "甲" * 10 + b)


if __name__ == "__main__":
    unittest.main()
