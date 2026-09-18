# tests/test_lost_anchors_to_delta.py
"""`scripts/lost_anchors_to_delta.py`：SQL 形狀與 delta 檔格式。不連 DB。

釘住兩件事：(1) 輸出格式與 `extract_takeaways.read_hashes_file` 的「一行一個 hash」一致，
否則補救鏈接不起來；(2) SQL 用 `anchor_method IS NULL` 當「錨不回」的判準——那正是
`store.reanchor_takeaways` 錨不回時寫入的值。
"""
import importlib.util
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _load():
    path = REPO_ROOT / "scripts" / "lost_anchors_to_delta.py"
    spec = importlib.util.spec_from_file_location("lost_anchors_to_delta", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


class DeltaFormatTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load()

    def test_one_hash_per_line(self):
        rows = [("a" * 64, 3, 5, None), ("b" * 64, 1, 4, None)]
        self.assertEqual(self.mod.render_delta(rows), "a" * 64 + "\n" + "b" * 64 + "\n")

    def test_empty_is_empty_file(self):
        self.assertEqual(self.mod.render_delta([]), "")

    def test_sql_uses_null_anchor_as_the_criterion(self):
        sql = self.mod.LOST_SQL
        self.assertIn("anchor_method IS NULL", sql)
        self.assertIn("research.report_takeaway", sql)
        self.assertIn(":min_lost", sql)
        self.assertIn(":limit", sql)

    def test_read_back_matches_takeaways_reader(self):
        """與 extract_takeaways.read_hashes_file 對接：那邊每行 strip 後非空即一個 hash。"""
        text = self.mod.render_delta([("c" * 64, 1, 1, None)])
        hashes = [ln.strip() for ln in text.splitlines() if ln.strip()]
        self.assertEqual(hashes, ["c" * 64])


if __name__ == "__main__":
    unittest.main()
