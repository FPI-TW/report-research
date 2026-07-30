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

    def test_substring_collision_no_hit(self):
        # 裸詞子字串碰撞不得誤攔合法研報題（M4 最終審查實測案例）
        self.assertIsNone(_safety_precheck("大盤中長期趨勢"))
        self.assertIsNone(_safety_precheck("台積電如何體現價值投資"))
        self.assertIsNone(_safety_precheck("個股歷史成交價量分析"))


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

    def test_decided_by_defaults_to_unknown_not_llm(self):
        """預設刻意不是 llm：漏設的呼叫點要在 log 裡看得出來，不能偽裝成正常分類。"""
        self.assertEqual(_decision(CORPUS_QA).decided_by, sr.BY_UNKNOWN)


class DecidedByTests(unittest.TestCase):
    """每條判定路徑都要標明是誰判的。

    這組存在的唯一理由：fail-open 的落點是 CORPUS_QA，所以「分類器判的 corpus_qa」
    與「分類器壞掉猜的 corpus_qa」在行為上一模一樣。沒有 decided_by，分類器失敗率
    是完全不可觀測的量。
    """

    def _with_llm(self, output):
        async def fake_stream(prompt, **kw):
            yield output

        return mock.patch.object(sr, "stream_completion", fake_stream)

    def test_precheck_route_public_entry(self):
        d = sr.precheck_route("台積電現在股價多少")
        self.assertIsNotNone(d)
        self.assertEqual(d.scope, sr.TIME_SENSITIVE)
        self.assertEqual(d.decided_by, sr.BY_PRECHECK)
        self.assertEqual(sr.precheck_route("台積電該不該買").scope, sr.ADVICE_RISK)
        self.assertIsNone(sr.precheck_route("台積電展望如何"))

    def test_overview_marked(self):
        d = sr.resolve_overview_route("台灣市場有哪些券商的報告", date(2026, 7, 13))
        self.assertEqual(d.decided_by, sr.BY_OVERVIEW)

    def test_classifier_paths_marked(self):
        with self._with_llm("TIME_SENSITIVE"):
            d = _run(sr.classify_non_overview("那個東西的最新數字"))
        self.assertEqual(d.decided_by, sr.BY_LLM)

        # 前檢命中：不呼叫 LLM，標 precheck
        d = _run(sr.classify_non_overview("台積電現在股價多少"))
        self.assertEqual(d.decided_by, sr.BY_PRECHECK)

        # 解析不出來 → fail-open corpus_qa，但要標得出來是猜的
        with self._with_llm("我不知道"):
            d = _run(sr.classify_non_overview("台積電展望"))
        self.assertEqual((d.scope, d.decided_by), (CORPUS_QA, sr.BY_FAIL_OPEN))

    def test_condense_paths_marked(self):
        with self._with_llm("QUERY: 台積電展望\nROUTE: CORPUS_QA"):
            _, d = _run(sr.condense_and_route("h", "它呢", today=date(2026, 7, 13)))
        self.assertEqual(d.decided_by, sr.BY_LLM)

        with self._with_llm("壞掉的輸出"):
            _, d = _run(sr.condense_and_route("h", "它呢", today=date(2026, 7, 13)))
        self.assertEqual((d.scope, d.decided_by), (CORPUS_QA, sr.BY_FAIL_OPEN))

        # 原始問句前檢命中：改寫器連叫都不叫
        _, d = _run(sr.condense_and_route(
            "h", "台積電現在股價多少", today=date(2026, 7, 13)
        ))
        self.assertEqual(d.decided_by, sr.BY_PRECHECK)


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

    def test_original_advice_question_cannot_be_downgraded_by_rewrite(self):
        with self._with_llm("QUERY: 台積電產業展望\nROUTE: CORPUS_QA"):
            _, d = _run(sr.condense_and_route(
                "先前對話…", "我該不該買台積電？", today=self.TODAY
            ))
        self.assertEqual(d.scope, sr.ADVICE_RISK)

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
