import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import sync_new_reports as snr  # noqa: E402


class WriteIngestedMarkerTests(unittest.TestCase):
    def test_writes_integer_as_text(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ".sync_last_ingested"
            snr.write_ingested_marker(p, 5)
            self.assertEqual(p.read_text(encoding="utf-8"), "5")
            self.assertEqual(int(p.read_text(encoding="utf-8")), 5)

    def test_overwrites_previous_value(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ".sync_last_ingested"
            snr.write_ingested_marker(p, 3)
            snr.write_ingested_marker(p, 0)
            self.assertEqual(p.read_text(encoding="utf-8"), "0")

    def test_creates_parent_dirs(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a" / "b" / ".sync_last_ingested"
            snr.write_ingested_marker(p, 7)
            self.assertTrue(p.exists())
            self.assertEqual(p.read_text(encoding="utf-8"), "7")

    def test_marker_constant_under_data(self):
        self.assertEqual(snr.INGESTED_MARKER.name, ".sync_last_ingested")
        self.assertEqual(snr.INGESTED_MARKER.parent.name, "data")


if __name__ == "__main__":
    unittest.main()
