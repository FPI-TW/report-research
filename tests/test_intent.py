# tests/test_intent.py
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services.intent import parse_intent, parse_condense  # noqa: E402


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

    def test_malformed_or_ambiguous_tokens_fail_open(self):
        # 非標準輸出不應因字首/子字串命中而誤判離題
        self.assertTrue(parse_intent("OUTAGE"))
        self.assertTrue(parse_intent("OUT IN"))

    def test_contains_out_without_in(self):
        self.assertFalse(parse_intent("結論：OUT"))


class ParseCondenseTests(unittest.TestCase):
    def test_standard_two_lines(self):
        q, ok = parse_condense("QUERY: 台積電先進封裝的展望\nINTENT: IN")
        self.assertEqual(q, "台積電先進封裝的展望")
        self.assertTrue(ok)

    def test_intent_out(self):
        q, ok = parse_condense("QUERY: 幫我寫詩\nINTENT: OUT")
        self.assertEqual(q, "幫我寫詩")
        self.assertFalse(ok)

    def test_lowercase_keys_and_whitespace(self):
        q, ok = parse_condense("  query:  鴻海營收 \n  intent: in ")
        self.assertEqual(q, "鴻海營收")
        self.assertTrue(ok)

    def test_missing_intent_fails_open_in_domain(self):
        q, ok = parse_condense("QUERY: 只有查詢沒有意圖")
        self.assertEqual(q, "只有查詢沒有意圖")
        self.assertTrue(ok)  # 缺 INTENT → fail-open True

    def test_missing_query_returns_none(self):
        q, ok = parse_condense("INTENT: IN")
        self.assertIsNone(q)
        self.assertTrue(ok)

    def test_garbage_returns_none_and_in_domain(self):
        q, ok = parse_condense("我不知道怎麼改寫")
        self.assertIsNone(q)
        self.assertTrue(ok)


if __name__ == "__main__":
    unittest.main()
