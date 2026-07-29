import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services.rows import ChunkRow  # noqa: E402
from app.services.store import _meta_columns  # noqa: E402


class ChunkRowTests(unittest.TestCase):
    _VALS = (
        "c1", "r1", "a" * 64, "f.pdf", "報告內部標題", "TW", "元大", "摘要",
        "2026-06-20", "個股", ["equity"], True, False, ["2330"], [],
        3, "本文內容", 0.12,
    )

    def test_named_and_positional_access_agree(self):
        row = ChunkRow._make(self._VALS)
        # 具名
        self.assertEqual(row.chunk_id, "c1")
        self.assertEqual(row.report_id, "r1")
        self.assertEqual(row.file_hash, "a" * 64)
        self.assertEqual(row.file_name, "f.pdf")
        self.assertEqual(row.title, "報告內部標題")
        self.assertEqual(row.market, "TW")
        self.assertEqual(row.report_date, "2026-06-20")
        self.assertEqual(row.content, "本文內容")
        self.assertEqual(row.distance, 0.12)
        # 位移（tuple 相容，向後相容既有消費端）
        self.assertEqual(row[0], "c1")      # chunk_id
        self.assertEqual(row[1], "r1")      # report_id
        self.assertEqual(row[2], "a" * 64)  # file_hash
        self.assertEqual(row[8], "2026-06-20")  # report_date
        self.assertEqual(row[-1], 0.12)     # distance
        self.assertEqual(row[-2], "本文內容")  # content
        self.assertEqual(row[-3], 3)        # chunk_index
        self.assertEqual(len(row), 18)
        self.assertIsInstance(row, tuple)

    def test_from_sequence_preserves_order(self):
        # 模擬 SQLAlchemy Row（可迭代、依欄序）→ _make
        row = ChunkRow._make(list(self._VALS))
        self.assertEqual(tuple(row), self._VALS)


def _meta_column_names(alias: str = "c") -> list[str]:
    """_meta_columns() 的欄位字串 → 去掉 alias 與 ::text 的欄名清單。"""
    return [
        col.strip().split(".", 1)[1].replace("::text", "")
        for col in _meta_columns(alias).split(",")
    ]


class MetaColumnsAlignmentTests(unittest.TestCase):
    """把「欄序即契約」釘死：_meta_columns 與 ChunkRow 靠位置對齊（_make 不驗名）。

    錯位不會拋錯，只會靜默把值塞進錯欄位——故這裡的斷言是唯一的守門員。
    """

    def test_file_hash_is_not_in_last_two_columns(self):
        # 消費端以 row[-2]=content、row[-1]=distance 位移存取；新欄位 append 到尾端
        # 會擠走 content。file_hash 必須在中段。
        cols = _meta_column_names()
        self.assertNotIn("file_hash", cols[-2:])
        self.assertEqual(cols[-2:], ["chunk_index", "content"])
        # distance 由各查詢自己 append 在 _meta_columns 之後，故為 ChunkRow 末欄
        self.assertEqual(ChunkRow._fields[-1], "distance")
        self.assertNotIn("file_hash", ChunkRow._fields[-2:])

    def test_file_hash_sits_right_after_report_id(self):
        cols = _meta_column_names()
        self.assertEqual(cols[2], "file_hash")
        self.assertEqual(ChunkRow._fields[2], "file_hash")

    def test_title_sits_right_after_file_name(self):
        # 顯示標題與檔名成對：呈現層一律 title → 缺值才回退 file_name
        cols = _meta_column_names()
        self.assertEqual(cols[3:5], ["file_name", "title"])
        self.assertEqual(ChunkRow._fields[3:5], ("file_name", "title"))
        self.assertNotIn("title", cols[-2:])

    def test_column_order_matches_chunkrow_fields(self):
        # 前兩欄是 {alias}.id / r.id（ChunkRow 內分別叫 chunk_id / report_id），
        # 其餘欄名與 ChunkRow 欄位須逐一同序對齊。
        self.assertTrue(_meta_columns("c").startswith("c.id::text, r.id::text,"))
        cols = _meta_column_names()
        self.assertEqual(cols[2:], list(ChunkRow._fields[2:-1]))
        # _meta_columns 欄數 + distance == ChunkRow 欄數
        self.assertEqual(len(cols) + 1, len(ChunkRow._fields))


if __name__ == "__main__":
    unittest.main()
