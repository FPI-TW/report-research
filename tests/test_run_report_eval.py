"""M1b 研報 eval runner（eval/run_report_eval.py）測試：以注入的 fake generate_report
驗證事件收集、逐題 fail-open、timeout 護欄、聚合與寫檔——不觸 DB、不觸 claude CLI。
"""

import asyncio
import json
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from eval import run_report_eval as rre  # noqa: E402

_MD = (
    "# 主題研報\n\n## 執行摘要\n\n重點[1]。\n\n## 關鍵發現\n\n發現[2]。\n\n"
    "## 重點分析\n\n分析[1]（網路）。\n\n## 風險與展望\n\n風險[2]。\n\n"
    "## 引用來源\n\n[1] A\n[2] B\n\n## 外部參考（網路）\n\n- [x](https://e.com/a)\n"
)

_SOURCES = [
    {"report_id": "ra", "market": "TW", "report_date": "2026-06-01"},
    {"report_id": "rb", "market": "TW", "report_date": "2026-04-20"},
]

_Q = {
    "id": "r001",
    "topic": "台積電先進製程展望",
    "market": "TW",
    "expected_facets": [
        {"name": "先進製程", "keywords": ["3奈米"]},
        {"name": "估值", "keywords": ["目標價"]},
    ],
    "allowed_source_types": ["corpus", "web"],
    "no_data": False,
    "filters": {},
}


def _gen_ok(topic, *, filters=None, persist=True, **kw):
    assert persist is False, "runner 必須以 persist=False 呼叫（不汙染 report_doc）"

    async def _g():
        yield ("status", {"stage": "retrieving"})
        yield ("sources", _SOURCES)
        yield ("status", {"stage": "writing"})
        yield ("token", "# 主題研報")
        yield ("done", {"report_id": None, "title": topic, "markdown": _MD,
                        "context": "[1] 報告：x\n3奈米製程與 CoWoS 需求", "thinking_ms": 7})

    return _g()


def _gen_error(topic, **kw):
    async def _g():
        yield ("status", {"stage": "retrieving"})
        yield ("sources", [])
        yield ("error", {"detail": "找不到足夠資料生成研報"})

    return _g()


def _gen_boom(topic, **kw):
    async def _g():
        yield ("status", {"stage": "retrieving"})
        raise RuntimeError("CLI exploded")

    return _g()


def _gen_slow(topic, **kw):
    async def _g():
        await asyncio.sleep(0.5)
        yield ("token", "never")

    return _g()


async def _brokers_ok(report_ids):
    return ["群益", "凱基"]


async def _brokers_fail(report_ids):
    return None


class EvalQuestionTests(unittest.IsolatedAsyncioTestCase):
    async def test_ok_case_has_metrics(self):
        case = await rre.eval_question(
            _Q, gen=_gen_ok, broker_lookup=_brokers_ok, question_timeout=5.0
        )
        self.assertNotIn("error", case)
        self.assertEqual(case["id"], "r001")
        self.assertEqual(case["n_sources"], 2)
        self.assertAlmostEqual(case["facet_coverage"]["rate"], 0.5)
        self.assertAlmostEqual(case["section_coverage"]["rate"], 1.0)
        self.assertAlmostEqual(case["citation_validity"], 1.0)
        self.assertEqual(case["source_diversity"]["n_brokers"], 2)
        self.assertEqual(case["date_diversity"]["n_months"], 2)
        self.assertAlmostEqual(case["external_labeling"]["score"], 1.0)
        self.assertIsNone(case["no_data_handled"])  # 非 no_data 題
        self.assertIn("markdown", case)
        self.assertNotIn("context", case)  # 脈絡不落地（體積），只記長度
        self.assertGreater(case["context_chars"], 0)
        self.assertEqual(case["stages"], ["retrieving", "writing"])

    async def test_structured_error_event_is_report_error_not_runner_error(self):
        """審查 M1b-1：generate_report 的結構化 error（研報婉拒）不是 runner 失敗；
        no_data 題的婉拒是 spec 定義的安全形態，不得計入 n_errors。"""
        q = dict(_Q, id="r009", no_data=True, expected_facets=[])
        case = await rre.eval_question(
            q, gen=_gen_error, broker_lookup=_brokers_ok, question_timeout=5.0
        )
        self.assertNotIn("error", case)
        self.assertEqual(case["report_error"], "找不到足夠資料生成研報")
        self.assertTrue(case["no_data"])
        self.assertTrue(case["no_data_handled"])  # 結構化婉拒＝安全

    async def test_normal_question_decline_recorded_as_report_error(self):
        case = await rre.eval_question(
            _Q, gen=_gen_error, broker_lookup=_brokers_ok, question_timeout=5.0
        )
        self.assertNotIn("error", case)
        self.assertEqual(case["report_error"], "找不到足夠資料生成研報")
        self.assertIsNone(case["no_data_handled"])  # 非 no_data 題不適用
        self.assertNotIn("facet_coverage", case)  # 無產出 → 不算結構指標

    async def test_exception_fail_open(self):
        case = await rre.eval_question(
            _Q, gen=_gen_boom, broker_lookup=_brokers_ok, question_timeout=5.0
        )
        self.assertIn("RuntimeError", case["error"])

    async def test_timeout_guard(self):
        case = await rre.eval_question(
            _Q, gen=_gen_slow, broker_lookup=_brokers_ok, question_timeout=0.05
        )
        self.assertIn("timeout", case["error"].lower())

    async def test_broker_lookup_failure_is_unknown(self):
        case = await rre.eval_question(
            _Q, gen=_gen_ok, broker_lookup=_brokers_fail, question_timeout=5.0
        )
        self.assertIsNone(case["source_diversity"]["n_brokers"])


class ConfigSnapshotTests(unittest.TestCase):
    def test_snapshot_includes_m6_keys(self):
        """M6 鍵入 snapshot（純加法）：eval 結果須可追溯多查詢／MMR 當時的組態。"""
        from app.config import get_settings

        snap = rre._config_snapshot({"version": 1})
        s = get_settings()
        for key in (
            "report_planner_model",
            "report_planner_timeout",
            "report_planner_max_subqueries",
            "report_fanout_concurrency",
            "report_subquery_dense_scan",
            "report_total_candidates",
            "report_mmr_enabled",
            "report_mmr_lambda",
            "report_mmr_max_per_source",
            "report_mmr_max_per_month",
        ):
            self.assertIn(key, snap)
            self.assertEqual(snap[key], getattr(s, key))

    def test_snapshot_keeps_existing_keys(self):
        snap = rre._config_snapshot({"version": 1})
        for key in (
            "dataset_version",
            "ruleset_version",
            "report_model",
            "report_enable_web",
            "report_rerank_enabled",
        ):
            self.assertIn(key, snap)


class RunTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_writes_report_with_summary_cases_config(self):
        import tempfile

        dataset = {
            "version": 1,
            "count": 2,
            "questions": [_Q, dict(_Q, id="r002", topic="另一題")],
        }
        with tempfile.TemporaryDirectory() as td:
            ds = Path(td) / "qs.json"
            ds.write_text(json.dumps(dataset, ensure_ascii=False), encoding="utf-8")
            out = Path(td) / "base.json"
            report = await rre.run(
                ds, out_path=out, gen=_gen_ok, broker_lookup=_brokers_ok,
                question_timeout=5.0,
            )
            self.assertEqual(len(report["cases"]), 2)
            self.assertEqual(report["summary"]["n"], 2)
            self.assertEqual(report["summary"]["n_errors"], 0)
            self.assertEqual(report["config"]["dataset_version"], 1)
            self.assertIn("report_enable_web", report["config"])
            on_disk = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(on_disk["summary"]["n"], 2)

    async def test_run_limit(self):
        import tempfile

        dataset = {"version": 1, "questions": [_Q, dict(_Q, id="r002")]}
        with tempfile.TemporaryDirectory() as td:
            ds = Path(td) / "qs.json"
            ds.write_text(json.dumps(dataset, ensure_ascii=False), encoding="utf-8")
            report = await rre.run(
                ds, out_path=None, gen=_gen_ok, broker_lookup=_brokers_ok,
                question_timeout=5.0, limit=1,
            )
            self.assertEqual(len(report["cases"]), 1)


if __name__ == "__main__":
    unittest.main()
