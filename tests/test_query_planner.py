"""query_planner 共用核心測試：解析、正規化與 fail-open 矩陣。"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services import query_planner as qp  # noqa: E402


def _texts(plan):
    return [s.text for s in plan.subqueries]


class ParsePlanJsonTests(unittest.TestCase):
    def test_plain_object(self):
        data = qp.parse_plan_json('{"subqueries": ["a"]}')
        self.assertEqual(data["subqueries"], ["a"])

    def test_fenced_json(self):
        raw = '```json\n{"subqueries": [{"q": "台積電 資本支出"}]}\n```'
        data = qp.parse_plan_json(raw)
        self.assertEqual(data["subqueries"][0]["q"], "台積電 資本支出")

    def test_prose_wrapped_object(self):
        raw = '規劃如下：{"subqueries": ["聯發科 展望"]}，請參考。'
        data = qp.parse_plan_json(raw)
        self.assertEqual(data["subqueries"], ["聯發科 展望"])

    def test_object_preferred_over_leading_array(self):
        # 教訓沿用 eval/judge：散文引用 [n] 不得被誤截成頂層小陣列。
        raw = '依據 [1] 與 [3]：{"subqueries": [{"q": "先進製程"}]}'
        data = qp.parse_plan_json(raw)
        self.assertEqual(data["subqueries"][0]["q"], "先進製程")

    def test_top_level_array_rejected(self):
        with self.assertRaises(ValueError):
            qp.parse_plan_json('["a", "b"]')

    def test_no_json_raises(self):
        with self.assertRaises(ValueError):
            qp.parse_plan_json("完全沒有 JSON 的散文")

    def test_unbalanced_raises(self):
        with self.assertRaises(ValueError):
            qp.parse_plan_json('{"subqueries": ["a"')


class NormalizeSubqueriesTests(unittest.TestCase):
    Q = "台積電 2026 展望"

    def test_original_first(self):
        subs = qp.normalize_subqueries(
            ["先進製程 資本支出"], question=self.Q, max_subqueries=4
        )
        self.assertEqual(subs[0].text, self.Q)
        self.assertEqual(subs[1].text, "先進製程 資本支出")

    def test_string_and_object_items(self):
        subs = qp.normalize_subqueries(
            ["純字串查詢", {"q": "物件查詢", "fresh": True, "facet": "估值"}],
            question=self.Q,
            max_subqueries=4,
        )
        self.assertEqual(subs[1].text, "純字串查詢")
        self.assertFalse(subs[1].fresh)
        self.assertEqual(subs[2].text, "物件查詢")
        self.assertTrue(subs[2].fresh)
        self.assertEqual(subs[2].facet, "估值")

    def test_alt_keys_query_text(self):
        subs = qp.normalize_subqueries(
            [{"query": "鍵名 query"}, {"text": "鍵名 text"}],
            question=self.Q,
            max_subqueries=4,
        )
        self.assertEqual([s.text for s in subs[1:]], ["鍵名 query", "鍵名 text"])

    def test_dedupe_norm_for_match(self):
        # NFKC＋小寫＋去空白後相同 → 視為重複（含與原始問題重複）。
        subs = qp.normalize_subqueries(
            ["台積電  2026 展望", "ＴＳＭＣ", "tsmc", {"q": "TSMC"}],
            question=self.Q,
            max_subqueries=8,
        )
        self.assertEqual([s.text for s in subs], [self.Q, "ＴＳＭＣ"])

    def test_empty_items_dropped(self):
        subs = qp.normalize_subqueries(
            ["", "   ", {"q": ""}, {"q": None}, 42, None, ["x"]],
            question=self.Q,
            max_subqueries=8,
        )
        self.assertEqual([s.text for s in subs], [self.Q])

    def test_whitespace_collapsed_and_truncated(self):
        long = "很長 " * 200
        subs = qp.normalize_subqueries([long], question=self.Q, max_subqueries=4)
        self.assertLessEqual(len(subs[1].text), qp.SUBQUERY_MAX_LEN)
        self.assertNotIn("  ", subs[1].text)

    def test_cap_includes_original(self):
        subs = qp.normalize_subqueries(
            ["一", "二", "三", "四", "五"], question=self.Q, max_subqueries=3
        )
        self.assertEqual(len(subs), 3)
        self.assertEqual(subs[0].text, self.Q)

    def test_include_original_false(self):
        subs = qp.normalize_subqueries(
            ["獨立查詢"], question=self.Q, max_subqueries=3, include_original=False
        )
        self.assertEqual([s.text for s in subs], ["獨立查詢"])

    def test_empty_question_and_items(self):
        subs = qp.normalize_subqueries([], question="   ", max_subqueries=3)
        self.assertEqual(subs, ())

    def test_facet_truncated(self):
        subs = qp.normalize_subqueries(
            [{"q": "查詢", "facet": "長" * 200}], question=self.Q, max_subqueries=4
        )
        self.assertEqual(len(subs[1].facet), qp.FACET_MAX_LEN)


def _profile(**over):
    base = dict(
        name="qa",
        max_subqueries=4,
        model="test-model",
        timeout=5.0,
        include_original=True,
        build_prompt=lambda q, n: ("SYS", f"PLAN {n}: {q}"),
    )
    base.update(over)
    return qp.PlannerProfile(**base)


def _stream(chunks, calls=None):
    async def gen(prompt, **kwargs):
        if calls is not None:
            calls.append({"prompt": prompt, **kwargs})
        for c in chunks:
            yield c

    return gen


class PlanQueriesTests(unittest.IsolatedAsyncioTestCase):
    Q = "台積電 2026 展望"

    async def test_unknown_profile_fail_open(self):
        plan = await qp.plan_queries(self.Q, profile="nope")
        self.assertTrue(plan.degraded)
        self.assertEqual(_texts(plan), [self.Q])
        self.assertEqual(plan.profile, "nope")

    async def test_qa_profile_fail_open_without_llm(self):
        # Step 0（M5 區段）：qa profile 未填 prompt → fail-open 且不得呼叫 LLM。
        # M5 填入 build_prompt 後，本測試由 M5 里程碑改寫／移除。
        with patch.object(qp, "stream_completion") as spy:
            plan = await qp.plan_queries(self.Q, profile="qa")
        spy.assert_not_called()
        self.assertTrue(plan.degraded)
        self.assertEqual(_texts(plan), [self.Q])

    async def test_llm_error_fail_open(self):
        async def boom(prompt, **kwargs):
            raise RuntimeError("LLM down")
            yield  # pragma: no cover

        with patch.dict(qp._PROFILES, {"qa": _profile()}):
            with patch.object(qp, "stream_completion", boom):
                plan = await qp.plan_queries(self.Q, profile="qa")
        self.assertTrue(plan.degraded)
        self.assertEqual(_texts(plan), [self.Q])

    async def test_garbage_output_fail_open(self):
        with patch.dict(qp._PROFILES, {"qa": _profile()}):
            with patch.object(qp, "stream_completion", _stream(["不是 JSON"])):
                plan = await qp.plan_queries(self.Q, profile="qa")
        self.assertTrue(plan.degraded)

    async def test_subqueries_not_list_fail_open(self):
        with patch.dict(qp._PROFILES, {"qa": _profile()}):
            with patch.object(
                qp, "stream_completion", _stream(['{"subqueries": "oops"}'])
            ):
                plan = await qp.plan_queries(self.Q, profile="qa")
        self.assertTrue(plan.degraded)

    async def test_happy_path_forwards_profile_params(self):
        calls = []
        chunks = ['```json\n{"subqueries": [{"q": "先進製程", "fresh": true},', ' "海外擴廠"]}\n```']
        with patch.dict(qp._PROFILES, {"qa": _profile()}):
            with patch.object(qp, "stream_completion", _stream(chunks, calls)):
                plan = await qp.plan_queries(self.Q, profile="qa")
        self.assertFalse(plan.degraded)
        self.assertEqual(_texts(plan), [self.Q, "先進製程", "海外擴廠"])
        self.assertTrue(plan.subqueries[1].fresh)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["prompt"], f"PLAN 4: {self.Q}")
        self.assertEqual(calls[0]["model"], "test-model")
        self.assertEqual(calls[0]["system"], "SYS")
        self.assertEqual(calls[0]["timeout"], 5.0)

    async def test_kwarg_overrides(self):
        calls = []
        with patch.dict(qp._PROFILES, {"qa": _profile()}):
            with patch.object(
                qp, "stream_completion", _stream(['{"subqueries": ["a", "b", "c"]}'], calls)
            ):
                plan = await qp.plan_queries(
                    self.Q, profile="qa", max_subqueries=2, model="m2", timeout=9.0
                )
        self.assertEqual(len(plan.subqueries), 2)
        self.assertEqual(calls[0]["prompt"], f"PLAN 2: {self.Q}")
        self.assertEqual(calls[0]["model"], "m2")
        self.assertEqual(calls[0]["timeout"], 9.0)

    async def test_empty_subqueries_keeps_original_not_degraded(self):
        # LLM 合法回空清單＝「原始問題已足夠」，非 fail-open。
        with patch.dict(qp._PROFILES, {"qa": _profile()}):
            with patch.object(qp, "stream_completion", _stream(['{"subqueries": []}'])):
                plan = await qp.plan_queries(self.Q, profile="qa")
        self.assertFalse(plan.degraded)
        self.assertEqual(_texts(plan), [self.Q])


if __name__ == "__main__":
    unittest.main()
