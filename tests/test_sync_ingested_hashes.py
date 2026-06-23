import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import sync_new_reports as snr  # noqa: E402


class WriteIngestedHashesTests(unittest.TestCase):
    def test_writes_newline_joined(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ".sync_last_hashes"
            snr.write_ingested_hashes(p, ["aaa", "bbb", "ccc"])
            self.assertEqual(
                p.read_text(encoding="utf-8").splitlines(), ["aaa", "bbb", "ccc"]
            )

    def test_empty_list_writes_empty_file(self):
        # 殼層用 [ -s file ] gate：空清單必須產生 0-byte 檔 → 視同無新研報、跳過摘要
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ".sync_last_hashes"
            snr.write_ingested_hashes(p, [])
            self.assertTrue(p.exists())
            self.assertEqual(p.stat().st_size, 0)

    def test_overwrites_previous(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ".sync_last_hashes"
            snr.write_ingested_hashes(p, ["x", "y"])
            snr.write_ingested_hashes(p, ["z"])
            self.assertEqual(p.read_text(encoding="utf-8").splitlines(), ["z"])

    def test_creates_parent_dirs(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a" / "b" / ".sync_last_hashes"
            snr.write_ingested_hashes(p, ["h"])
            self.assertTrue(p.exists())

    def test_constant_points_at_hashes_file(self):
        self.assertEqual(snr.INGESTED_HASHES_FILE.name, ".sync_last_hashes")
        self.assertEqual(snr.INGESTED_HASHES_FILE.parent.name, "data")


if __name__ == "__main__":
    unittest.main()
