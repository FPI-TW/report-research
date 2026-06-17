# tests/test_intent.py
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services.intent import parse_intent  # noqa: E402


class ParseIntentTests(unittest.TestCase):
    def test_in_is_in_domain(self):
        self.assertTrue(parse_intent("IN"))

    def test_out_is_off_topic(self):
        self.assertFalse(parse_intent("OUT"))

    def test_case_and_whitespace_insensitive(self):
        self.assertFalse(parse_intent("  out\n"))
        self.assertTrue(parse_intent("in\n"))

    def test_out_with_trailing_text(self):
        # 判斷器若多嘴，仍以開頭 OUT 為準
        self.assertFalse(parse_intent("OUT（這是要求推薦飲品）"))

    def test_in_with_trailing_text(self):
        self.assertTrue(parse_intent("IN - 投資提問"))

    def test_garbage_fails_open_to_in_domain(self):
        # 無法判讀 → fail-open 回 True，不誤擋真問題
        self.assertTrue(parse_intent("我不確定"))
        self.assertTrue(parse_intent(""))

    def test_contains_out_without_in(self):
        self.assertFalse(parse_intent("結論：OUT"))


if __name__ == "__main__":
    unittest.main()
