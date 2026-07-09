import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services.stream_sentinel import SentinelStreamParser  # noqa: E402


class SentinelStreamParserTests(unittest.TestCase):
    def _drive(self, chunks, sentinel="[EXT_SOURCES]"):
        p = SentinelStreamParser(sentinel)
        emitted = "".join(p.feed(c) for c in chunks)
        emitted += p.flush()
        return emitted, p.sentinel_found

    def test_no_sentinel_emits_all(self):
        emitted, found = self._drive(["台積電", "展望", "良好"])
        self.assertEqual(emitted, "台積電展望良好")
        self.assertFalse(found)

    def test_sentinel_stops_emission(self):
        emitted, found = self._drive(["答案本文", "[EXT_SOURCES]", "- 標題 | http://x"])
        self.assertEqual(emitted, "答案本文")
        self.assertTrue(found)

    def test_sentinel_split_across_chunks(self):
        emitted, found = self._drive(["前文[EXT_", "SOURCES]尾"])
        self.assertEqual(emitted, "前文")
        self.assertTrue(found)

    def test_sentinel_at_very_end(self):
        emitted, found = self._drive(["內容", "[EXT_SOURCES]"])
        self.assertEqual(emitted, "內容")
        self.assertTrue(found)

    def test_tail_shorter_than_sentinel_held_then_flushed(self):
        # 未達 sentinel 長度的尾段先保留、無 sentinel 時最終 flush 補回
        emitted, found = self._drive(["abc"])
        self.assertEqual(emitted, "abc")
        self.assertFalse(found)


if __name__ == "__main__":
    unittest.main()
