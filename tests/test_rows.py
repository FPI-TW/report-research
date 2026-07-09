import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services.rows import ChunkRow  # noqa: E402


class ChunkRowTests(unittest.TestCase):
    _VALS = (
        "c1", "r1", "f.pdf", "TW", "元大", "摘要",
        "2026-06-20", "個股", ["equity"], True, False, ["2330"], [],
        3, "本文內容", 0.12,
    )

    def test_named_and_positional_access_agree(self):
        row = ChunkRow._make(self._VALS)
        # 具名
        self.assertEqual(row.chunk_id, "c1")
        self.assertEqual(row.report_id, "r1")
        self.assertEqual(row.file_name, "f.pdf")
        self.assertEqual(row.market, "TW")
        self.assertEqual(row.report_date, "2026-06-20")
        self.assertEqual(row.content, "本文內容")
        self.assertEqual(row.distance, 0.12)
        # 位移（tuple 相容，向後相容既有消費端）
        self.assertEqual(row[0], "c1")      # chunk_id
        self.assertEqual(row[1], "r1")      # report_id
        self.assertEqual(row[6], "2026-06-20")  # report_date
        self.assertEqual(row[-1], 0.12)     # distance
        self.assertEqual(row[-2], "本文內容")  # content
        self.assertEqual(row[-3], 3)        # chunk_index
        self.assertEqual(len(row), 16)
        self.assertIsInstance(row, tuple)

    def test_from_sequence_preserves_order(self):
        # 模擬 SQLAlchemy Row（可迭代、依欄序）→ _make
        row = ChunkRow._make(list(self._VALS))
        self.assertEqual(tuple(row), self._VALS)


if __name__ == "__main__":
    unittest.main()
