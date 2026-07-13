# tests/test_scope_router.py
import asyncio
import sys
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services import scope_router as sr  # noqa: E402
from app.services.scope_router import (  # noqa: E402
    parse_intent,
    parse_condense,
    parse_route,
    parse_condense_route,
    condense_and_route,
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


def _run(coro):
    return asyncio.run(coro)


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

    def test_corpus_price_trend_no_hit(self):
        # 研報常見的商品/記憶體「報價走勢」分析不得被裸詞誤攔（eval memory-prices 保護案例）
        self.assertIsNone(_safety_precheck("記憶體報價的近期走勢"))
        self.assertIsNone(_safety_precheck("台積電近期漲停後法人如何看待"))

    def test_institutional_positioning_no_hit(self):
        # 法人持倉/買賣超分析是 corpus 題，不得誤判 advice_risk
        self.assertIsNone(_safety_precheck("外資倉位增減分析"))
        self.assertIsNone(_safety_precheck("三大法人買多少台積電"))


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


class ResolveOverviewRouteTests(unittest.TestCase):
    def test_overview_question_returns_decision_with_filters(self):
        d = sr.resolve_overview_route("台灣市場有哪些券商的報告", date(2026, 7, 13))
        self.assertIsNotNone(d)
        self.assertEqual(d.scope, sr.OVERVIEW)
        self.assertEqual(d.tool_policy, sr.CORPUS_ONLY)
        self.assertIsNotNone(d.overview_filters)
        self.assertTrue(d.overview_filters.any())

    def test_non_overview_returns_none(self):
        self.assertIsNone(sr.resolve_overview_route("台積電的先進封裝展望", date(2026, 7, 13)))


class ClassifyNonOverviewTests(unittest.TestCase):
    def _with_llm(self, output):
        async def fake_stream(prompt, **kw):
            yield output
        return mock.patch.object(sr, "stream_completion", fake_stream)

    def test_llm_token_routes(self):
        with self._with_llm("CORPUS_QA"):
            self.assertEqual(_run(sr.classify_non_overview("台積電展望")).scope, sr.CORPUS_QA)
        with self._with_llm("OFF_TOPIC"):
            self.assertEqual(_run(sr.classify_non_overview("幫我寫一首詩")).scope, sr.OFF_TOPIC)
        with self._with_llm("TIME_SENSITIVE"):
            self.assertEqual(_run(sr.classify_non_overview("台積電下季財報數字")).scope, sr.TIME_SENSITIVE)
        with self._with_llm("ADVICE_RISK"):
            self.assertEqual(_run(sr.classify_non_overview("現在適合進場嗎")).scope, sr.ADVICE_RISK)

    def test_precheck_hit_skips_llm(self):
        called = False

        async def fake_stream(prompt, **kw):
            nonlocal called
            called = True
            yield "CORPUS_QA"

        with mock.patch.object(sr, "stream_completion", fake_stream):
            d = _run(sr.classify_non_overview("查詢緯創最新的收盤價"))
        self.assertEqual(d.scope, sr.TIME_SENSITIVE)
        self.assertFalse(called)  # 前檢命中 → 不呼叫 LLM

    def test_llm_failure_fails_open_to_corpus_qa(self):
        async def boom(prompt, **kw):
            raise RuntimeError("cli down")
            yield  # pragma: no cover

        with mock.patch.object(sr, "stream_completion", boom):
            self.assertEqual(_run(sr.classify_non_overview("台積電展望")).scope, sr.CORPUS_QA)

    def test_garbage_output_fails_open(self):
        with self._with_llm("我不確定"):
            self.assertEqual(_run(sr.classify_non_overview("台積電展望")).scope, sr.CORPUS_QA)
        with self._with_llm(""):
            self.assertEqual(_run(sr.classify_non_overview("台積電展望")).scope, sr.CORPUS_QA)


class RouteQuestionTests(unittest.TestCase):
    def test_overview_precedence_no_llm(self):
        called = False

        async def fake_stream(prompt, **kw):
            nonlocal called
            called = True
            yield "CORPUS_QA"

        with mock.patch.object(sr, "stream_completion", fake_stream):
            d = _run(sr.route_question("台灣市場有哪些券商的報告", today=date(2026, 7, 13)))
        self.assertEqual(d.scope, sr.OVERVIEW)
        self.assertFalse(called)

    def test_falls_to_classifier(self):
        async def fake_stream(prompt, **kw):
            yield "OFF_TOPIC"

        with mock.patch.object(sr, "stream_completion", fake_stream):
            d = _run(sr.route_question("幫我寫一首詩", today=date(2026, 7, 13)))
        self.assertEqual(d.scope, sr.OFF_TOPIC)


class ParseCondenseRouteTests(unittest.TestCase):
    def test_standard_two_lines(self):
        q, s = sr.parse_condense_route("QUERY: 台積電先進封裝的展望\nROUTE: CORPUS_QA")
        self.assertEqual(q, "台積電先進封裝的展望")
        self.assertEqual(s, sr.CORPUS_QA)

    def test_lowercase_and_whitespace(self):
        q, s = sr.parse_condense_route("  query:  鴻海營收 \n  route: off_topic ")
        self.assertEqual(q, "鴻海營收")
        self.assertEqual(s, sr.OFF_TOPIC)

    def test_missing_route_returns_none_scope(self):
        q, s = sr.parse_condense_route("QUERY: 只有查詢")
        self.assertEqual(q, "只有查詢")
        self.assertIsNone(s)

    def test_garbage(self):
        q, s = sr.parse_condense_route("我不知道怎麼改寫")
        self.assertIsNone(q)
        self.assertIsNone(s)


class CondenseAndRouteTests(unittest.TestCase):
    TODAY = date(2026, 7, 13)

    def _with_llm(self, output):
        async def fake_stream(prompt, **kw):
            yield output
        return mock.patch.object(sr, "stream_completion", fake_stream)

    def test_normal_rewrite_and_route(self):
        with self._with_llm("QUERY: 台積電的資本支出計畫\nROUTE: CORPUS_QA"):
            q, d = _run(sr.condense_and_route("先前對話…", "那資本支出呢", today=self.TODAY))
        self.assertEqual(q, "台積電的資本支出計畫")
        self.assertEqual(d.scope, sr.CORPUS_QA)

    def test_rewritten_query_overview_overrides_llm_route(self):
        # 改寫後命中 overview 規則 → 覆蓋 LLM 的 ROUTE token（overview 不交 LLM 判斷）
        with self._with_llm("QUERY: 台灣市場有哪些券商的報告\nROUTE: CORPUS_QA"):
            q, d = _run(sr.condense_and_route("先前對話…", "那有哪些券商", today=self.TODAY))
        self.assertEqual(d.scope, sr.OVERVIEW)
        self.assertIsNotNone(d.overview_filters)

    def test_rewritten_query_precheck_overrides_llm_route(self):
        # 改寫還原主語後浮現報價詞 → 前檢覆蓋 LLM 判斷（保守安全優先）
        with self._with_llm("QUERY: 緯創今天的收盤價\nROUTE: CORPUS_QA"):
            q, d = _run(sr.condense_and_route("先前對話…", "那它今天收多少", today=self.TODAY))
        self.assertEqual(d.scope, sr.TIME_SENSITIVE)

    def test_failure_falls_back_to_original_question(self):
        async def boom(prompt, **kw):
            raise RuntimeError("cli down")
            yield  # pragma: no cover

        with mock.patch.object(sr, "stream_completion", boom):
            q, d = _run(sr.condense_and_route("先前對話…", "追問原文", today=self.TODAY))
        self.assertEqual(q, "追問原文")
        self.assertEqual(d.scope, sr.CORPUS_QA)  # fail-open

    def test_failure_with_precheck_hit_keeps_safe_scope(self):
        async def boom(prompt, **kw):
            raise RuntimeError("cli down")
            yield  # pragma: no cover

        with mock.patch.object(sr, "stream_completion", boom):
            q, d = _run(sr.condense_and_route("先前對話…", "我該不該買台積電", today=self.TODAY))
        self.assertEqual(d.scope, sr.ADVICE_RISK)


if __name__ == "__main__":
    unittest.main()
