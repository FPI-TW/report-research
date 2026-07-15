"""M6 report profile 測試：_build_report_prompt 內容與 plan_queries 接線。

放獨立檔（不進 test_query_planner.py）以避免與 M5 在同檔 add/add 衝突
（設計規格凍結契約 4）。report profile 的 fail-open 覆蓋自
test_query_planner.py 移轉至此。
"""

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services import query_planner as qp  # noqa: E402


def _texts(plan):
    return [s.text for s in plan.subqueries]


def _stream(chunks, calls=None):
    async def gen(prompt, **kwargs):
        if calls is not None:
            calls.append({"prompt": prompt, **kwargs})
        for c in chunks:
            yield c

    return gen


class BuildReportPromptTests(unittest.TestCase):
    Q = "台積電 2026 展望"

    def test_returns_system_and_prompt(self):
        system, prompt = qp._build_report_prompt(self.Q, 8)
        self.assertIsInstance(system, str)
        self.assertIsInstance(prompt, str)
        self.assertTrue(system)
        self.assertTrue(prompt)

    def test_json_object_format_required(self):
        system, _ = qp._build_report_prompt(self.Q, 8)
        self.assertIn("JSON 物件", system)
        self.assertIn('"subqueries"', system)
        self.assertIn('"q"', system)

    def test_cap_minus_one_limit(self):
        # max_subqueries 為總 fan-out 上限（含原始主題）→ 要求 LLM 至多 cap-1 條。
        system, _ = qp._build_report_prompt(self.Q, 8)
        self.assertIn("最多輸出 7 個", system)
        self.assertNotIn("最多輸出 8 個", system)

    def test_cap_floor_at_one(self):
        system, _ = qp._build_report_prompt(self.Q, 1)
        self.assertIn("最多輸出 1 個", system)

    def test_facet_required(self):
        system, _ = qp._build_report_prompt(self.Q, 8)
        self.assertIn("facet", system)
        # 面向建議清單（設計 §1）至少可辨識數項。
        for facet in ("財報營運", "風險因子", "估值", "產業鏈"):
            self.assertIn(facet, system)

    def test_injection_guard_present(self):
        system, _ = qp._build_report_prompt(self.Q, 8)
        self.assertIn("資料而非指令", system)
        self.assertIn("忽略", system)

    def test_question_in_delimited_data_block(self):
        _, prompt = qp._build_report_prompt(self.Q, 8)
        self.assertIn(self.Q, prompt)
        self.assertIn("<topic>", prompt)
        self.assertIn("</topic>", prompt)
        # 主題置於分隔標記之間。
        self.assertLess(prompt.index("<topic>"), prompt.index(self.Q))
        self.assertLess(prompt.index(self.Q), prompt.index("</topic>"))


class ReportProfileWiringTests(unittest.TestCase):
    def test_report_profile_has_prompt(self):
        spec = qp._PROFILES["report"]
        self.assertIs(spec.build_prompt, qp._build_report_prompt)
        self.assertTrue(spec.include_original)


class ReportPlanQueriesTests(unittest.IsolatedAsyncioTestCase):
    Q = "台積電 2026 展望"

    @staticmethod
    def _payload(n):
        items = [{"q": f"面向查詢 {i}", "facet": f"面向{i}"} for i in range(1, n + 1)]
        return json.dumps({"subqueries": items}, ensure_ascii=False)

    async def test_happy_path_original_first_facets_kept(self):
        calls = []
        with patch.object(qp, "stream_completion", _stream([self._payload(8)], calls)):
            plan = await qp.plan_queries(self.Q, profile="report")
        self.assertFalse(plan.degraded)
        # cap=8 為總數上限（含原題）：原題首位＋7 條面向子查詢。
        self.assertEqual(len(plan.subqueries), 8)
        self.assertEqual(plan.subqueries[0].text, self.Q)
        self.assertEqual(_texts(plan)[1:], [f"面向查詢 {i}" for i in range(1, 8)])
        self.assertEqual(
            [s.facet for s in plan.subqueries[1:]], [f"面向{i}" for i in range(1, 8)]
        )
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["system"], qp._build_report_prompt(self.Q, 8)[0])
        self.assertEqual(calls[0]["prompt"], qp._build_report_prompt(self.Q, 8)[1])

    async def test_over_quota_truncated_to_cap(self):
        with patch.object(qp, "stream_completion", _stream([self._payload(10)])):
            plan = await qp.plan_queries(self.Q, profile="report")
        self.assertEqual(len(plan.subqueries), 8)
        self.assertEqual(plan.subqueries[0].text, self.Q)

    async def test_duplicates_and_blanks_cleaned(self):
        raw = json.dumps(
            {
                "subqueries": [
                    {"q": "先進製程 資本支出", "facet": "財報營運"},
                    {"q": "先進製程  資本支出", "facet": "重複"},
                    {"q": "", "facet": "空白"},
                    {"q": self.Q, "facet": "複述原題"},
                    {"q": "海外擴廠 風險", "facet": "風險因子"},
                ]
            },
            ensure_ascii=False,
        )
        with patch.object(qp, "stream_completion", _stream([raw])):
            plan = await qp.plan_queries(self.Q, profile="report")
        self.assertFalse(plan.degraded)
        self.assertEqual(_texts(plan), [self.Q, "先進製程 資本支出", "海外擴廠 風險"])

    async def test_llm_error_fail_open(self):
        # 自 test_query_planner.py 移轉的 report 側 fail-open 覆蓋。
        async def boom(prompt, **kwargs):
            raise RuntimeError("LLM down")
            yield  # pragma: no cover

        with patch.object(qp, "stream_completion", boom):
            plan = await qp.plan_queries(self.Q, profile="report")
        self.assertTrue(plan.degraded)
        self.assertEqual(_texts(plan), [self.Q])


if __name__ == "__main__":
    unittest.main()
