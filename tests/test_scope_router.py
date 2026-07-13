# tests/test_intent.py
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services.scope_router import (  # noqa: E402
    parse_intent,
    parse_condense,
    parse_route,
    _safety_precheck,
    _decision,
    RouteDecision,
    OFF_TOPIC,
    OVERVIEW,
    CORPUS_QA,
    TIME_SENSITIVE,
    ADVICE_RISK,
    CORPUS_ONLY,
    TRUSTED_EXTERNAL_REQUIRED,
    RESEARCH_ONLY,
    NO_ANSWER,
)


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


class ParseRouteTests(unittest.TestCase):
    def test_four_valid_tokens(self):
        self.assertEqual(parse_route("OFF_TOPIC"), OFF_TOPIC)
        self.assertEqual(parse_route("CORPUS_QA"), CORPUS_QA)
        self.assertEqual(parse_route("TIME_SENSITIVE"), TIME_SENSITIVE)
        self.assertEqual(parse_route("ADVICE_RISK"), ADVICE_RISK)

    def test_case_and_whitespace(self):
        self.assertEqual(parse_route("  corpus_qa\n"), CORPUS_QA)

    def test_strict_rejects_trailing_text(self):
        # 嚴格 parser：多嘴一律視為解析失敗（交上層 fallback），不寬鬆猜測
        self.assertIsNone(parse_route("OFF_TOPIC（寫詩）"))
        self.assertIsNone(parse_route("結論：CORPUS_QA"))
        self.assertIsNone(parse_route(""))
        self.assertIsNone(parse_route("IN"))


class SafetyPrecheckTests(unittest.TestCase):
    def test_quote_terms_hit_time_sensitive(self):
        self.assertEqual(_safety_precheck("查詢緯創最新的收盤價"), TIME_SENSITIVE)
        self.assertEqual(_safety_precheck("台積電現在股價多少"), TIME_SENSITIVE)
        self.assertEqual(_safety_precheck("鴻海即時報價"), TIME_SENSITIVE)

    def test_advice_terms_hit_advice_risk(self):
        self.assertEqual(_safety_precheck("我該不該買台積電"), ADVICE_RISK)
        self.assertEqual(_safety_precheck("幫我配置倉位"), ADVICE_RISK)
        self.assertEqual(_safety_precheck("建議我買哪一檔"), ADVICE_RISK)

    def test_advice_wins_over_time(self):
        # 兩者同時命中 → advice_risk 優先（spec 路由順序 2）
        self.assertEqual(_safety_precheck("看今天收盤價我該不該買"), ADVICE_RISK)

    def test_plain_outlook_no_hit(self):
        # 「最新展望」是研報題，不得被前檢誤攔（eval q001 保護案例）
        self.assertIsNone(_safety_precheck("台積電最新的營運展望如何"))
        self.assertIsNone(_safety_precheck("散熱產業的競爭格局"))


class DecisionTests(unittest.TestCase):
    def test_policy_mapping(self):
        self.assertEqual(_decision(OFF_TOPIC).tool_policy, NO_ANSWER)
        self.assertEqual(_decision(CORPUS_QA).tool_policy, CORPUS_ONLY)
        self.assertEqual(_decision(OVERVIEW).tool_policy, CORPUS_ONLY)
        self.assertEqual(_decision(TIME_SENSITIVE).tool_policy, TRUSTED_EXTERNAL_REQUIRED)
        self.assertEqual(_decision(ADVICE_RISK).tool_policy, RESEARCH_ONLY)

    def test_frozen(self):
        d = _decision(CORPUS_QA)
        with self.assertRaises(Exception):
            d.scope = OFF_TOPIC  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()
