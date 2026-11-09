"""上線驗收（`scripts/eval_gate.py`）：退出碼是結論——0 通過／1 有未豁免的指標沒過／2 不完整或不可比。

結果檔一律是測試裡合成的（不讀 `eval/baselines/`：那是別的 lane 會換的檔）；廷豐的題集與豁免讀 repo 的
`profiles/tingfong/eval/eval.toml`，其他情境的 profile 建在 tempfile 裡。
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from eval import profile_eval as pe
from scripts import eval_gate as gate

REPO_ROOT = Path(__file__).resolve().parents[1]
TINGFONG_SET = REPO_ROOT / "eval" / "ragas_questions.json"


def _result(*, f=0.95, cp=0.85, ar=0.65, n=18, sha=None, lineage="gpt-6-luna-2026-11", **summary) -> dict:
    base = {
        "faithfulness": f, "context_precision": cp, "answer_relevancy": ar,
        "n": n, "n_errors": 0, "n_no_context": 0, "n_judge_errors": 0,
        "n_effective_faithfulness": n, "n_effective_context_precision": n, "n_effective_answer_relevancy": n,
        "thresholds_pass": True, "judge_model": "gpt-6-luna", "judge_prompt_sha": "a" * 64,
        "judge_schema_version": 2, "judge_lineage": lineage,
    }
    base.update(summary)
    if lineage is None:
        base.pop("judge_lineage")
    return {
        "summary": base,
        "config": {"queryset_sha256": sha or hashlib.sha256(TINGFONG_SET.read_bytes()).hexdigest(),
                   "limit": None, "scope": None, "judge": {"lineage": "gpt-6-luna-2026-11"}},
    }


def _evaluate(doc: dict, profile: str | Path = "tingfong") -> gate.GateResult:
    profile_dir = pe.resolve_profile_arg(str(profile))
    return gate.evaluate(doc, pe.load_profile_dataset(profile_dir), pe.load_eval_config(profile_dir))


class PolicyTests(unittest.TestCase):
    def test_thresholds_equal_run_ragas_constants(self):
        """門檻是政策常數（量測紀錄在 run_ragas 那邊），數值兩邊逐字相同。"""
        from eval import run_ragas as rr

        self.assertEqual(gate.THRESHOLDS, {
            "faithfulness": rr.FAITHFULNESS_MIN,
            "context_precision": rr.CONTEXT_PRECISION_MIN,
            "answer_relevancy": rr.ANSWER_RELEVANCY_MIN,
        })
        self.assertEqual(tuple(gate.THRESHOLDS), pe.GATE_METRICS)


class TingfongGateTests(unittest.TestCase):
    def test_all_pass_is_0(self):
        res = _evaluate(_result())
        self.assertEqual(res.exit_code, 0, res.undecidable)
        self.assertEqual({m.status for m in res.metrics}, {gate.STATUS_PASS})

    def test_cp_below_floor_is_waived_with_its_reason(self):
        res = _evaluate(_result(cp=0.78))
        self.assertEqual(res.exit_code, 0)
        cp = next(m for m in res.metrics if m.metric == "context_precision")
        self.assertEqual(cp.status, gate.STATUS_WAIVED)
        self.assertIn("2026-11-10", cp.waiver)
        self.assertIn("未過（豁免）", gate.render(res))

    def test_unwaived_failure_is_1(self):
        for kwargs in ({"f": 0.9}, {"ar": 0.5}, {"f": 0.85, "cp": 0.5}):
            with self.subTest(**kwargs):
                res = _evaluate(_result(**kwargs))
                self.assertEqual(res.exit_code, 1)
                self.assertIn(gate.STATUS_FAIL, {m.status for m in res.metrics})

    def test_the_results_own_threshold_flag_is_not_trusted(self):
        res = _evaluate(_result(ar=0.5, thresholds_pass=True))
        self.assertEqual(res.exit_code, 1)

    def test_incomplete_or_incomparable_is_2(self):
        cases = {
            "題數不符": _result(n=8),
            "不完整：n_errors": _result(n_errors=1),
            "不完整：n_judge_errors": _result(n_judge_errors=2),
            "入均值": _result(n_effective_context_precision=17),
            "沒有值": _result(f=None),
            "不是這份凍結題集": _result(sha="b" * 64),
            "解讀規則版本": _result(judge_schema_version=1),
            "量尺系譜不符": _result(lineage="declared:Qwen/Qwen3-72B"),
        }
        for pattern, doc in cases.items():
            with self.subTest(pattern=pattern):
                res = _evaluate(doc)
                self.assertEqual(res.exit_code, 2)
                self.assertTrue(any(pattern.split("：")[-1] in r for r in res.undecidable), res.undecidable)

    def test_limit_scope_and_missing_meta_are_2(self):
        doc = _result()
        doc["config"]["limit"] = 3
        self.assertEqual(_evaluate(doc).exit_code, 2)
        doc = _result()
        del doc["summary"]["judge_prompt_sha"]
        self.assertEqual(_evaluate(doc).exit_code, 2)

    def test_lineage_falls_back_to_config_for_older_results(self):
        res = _evaluate(_result(lineage=None))
        self.assertEqual(res.exit_code, 0, res.undecidable)
        self.assertEqual(res.lineage, "gpt-6-luna-2026-11")
        doc = _result(lineage=None)
        doc["config"]["judge"]["lineage"] = "claude-haiku"
        self.assertEqual(_evaluate(doc).exit_code, 2)

    def test_non_ragas_result_is_2(self):
        res = _evaluate({"summary": {"hit_rate": 0.9, "n_cases": 10}})
        self.assertEqual(res.exit_code, 2)
        self.assertEqual(res.metrics, [])


class LatestBaselineTests(unittest.TestCase):
    def test_latest_baseline_named_in_agents_md_passes_the_tingfong_gate(self):
        """AGENTS.md「最新基準線」指名的那份，對廷豐的上線驗收必須是 0（CP 以豁免通過）。"""
        import re

        text = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")
        m = re.search(r"最新基準線 `(eval/baselines/[^`]+\.json)`", text)
        self.assertIsNotNone(m, "AGENTS.md 找不到「最新基準線」")
        doc = json.loads((REPO_ROOT / m.group(1)).read_text(encoding="utf-8"))
        res = _evaluate(doc)
        self.assertEqual(res.exit_code, 0, (res.undecidable, [(x.metric, x.status) for x in res.metrics]))


class OtherProfileGateTests(unittest.TestCase):
    """沒有豁免、也沒有釘系譜的 profile：CP 沒過就是 1，任何系譜都接受。"""

    def setUp(self):
        from test_eval_question_draft import NOW, _draft

        from eval import question_draft as qd

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.profile = Path(self._tmp.name) / "demo"
        (self.profile / "eval").mkdir(parents=True)
        raw, manifest = qd.freeze(_draft(), draft_sha256="0" * 64, profile="demo", frozen_at=NOW)
        (self.profile / "eval" / pe.QUESTIONS_FILE).write_bytes(raw)
        (self.profile / "eval" / pe.MANIFEST_FILE).write_bytes(qd.dumps(manifest))
        self.sha = manifest["questions_sha256"]

    def test_no_waivers_means_cp_counts(self):
        res = _evaluate(_result(cp=0.78, n=32, sha=self.sha, lineage="declared:Qwen/Qwen3-72B"), self.profile)
        self.assertEqual(res.exit_code, 1)
        self.assertEqual(_evaluate(_result(n=32, sha=self.sha, lineage="declared:x"), self.profile).exit_code, 0)

    def test_result_of_another_question_set_is_2(self):
        self.assertEqual(_evaluate(_result(n=32), self.profile).exit_code, 2)


class CliTests(unittest.TestCase):
    def _main(self, doc: dict | None, *args: str) -> tuple[int, str]:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "result.json"
            if doc is not None:
                path.write_text(json.dumps(doc), encoding="utf-8")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = gate.main(["--profile", "tingfong", "--result", str(path), *args])
        return rc, out.getvalue()

    def test_exit_codes_through_main(self):
        self.assertEqual(self._main(_result())[0], 0)
        self.assertEqual(self._main(_result(ar=0.1))[0], 1)
        self.assertEqual(self._main(_result(n=3))[0], 2)
        rc, out = self._main(None)
        self.assertEqual(rc, 2)
        self.assertIn("讀不到檔案", out)

    def test_json_output(self):
        rc, out = self._main(_result(cp=0.7), "--json")
        payload = json.loads(out)
        self.assertEqual(rc, payload["exit_code"])
        self.assertEqual([m["metric"] for m in payload["metrics"]], list(gate.THRESHOLDS))

    def test_bad_profile_config_is_2(self):
        with tempfile.TemporaryDirectory() as td:
            profile = Path(td) / "p"
            (profile / "eval").mkdir(parents=True)
            (profile / "eval" / pe.CONFIG_FILE).write_text("[gate.waivers.faithfulness]\nreason = 1\n",
                                                           encoding="utf-8")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = gate.main(["--profile", str(profile), "--result", str(Path(td) / "r.json")])
        self.assertEqual(rc, 2)
        self.assertIn("必須附理由", out.getvalue())

    def test_makefile_target_passes_both_arguments(self):
        text = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
        self.assertIn('scripts/eval_gate.py --profile "$(PROFILE)" --result "$(RESULT)"', text)


if __name__ == "__main__":
    unittest.main()
