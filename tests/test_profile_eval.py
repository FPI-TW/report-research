"""profile 範圍的評測設定（`eval/profile_eval.py`）：`eval.toml` 的 fail-closed 驗證、廷豐沿用 repo 題集、
`--profile` 與預設行為、與其他模組逐字同值的常數。凍結流程本身見 `tests/test_eval_question_draft.py`。"""

from __future__ import annotations

import hashlib
import tempfile
import unittest
from datetime import date
from pathlib import Path

from eval import profile_eval as pe

REPO_ROOT = Path(__file__).resolve().parents[1]
TINGFONG = REPO_ROOT / "profiles" / "tingfong"


def _write_config(profile: Path, body: str) -> None:
    (profile / "eval").mkdir(parents=True, exist_ok=True)
    (profile / "eval" / pe.CONFIG_FILE).write_text(body, encoding="utf-8")


class ConstantsTests(unittest.TestCase):
    def test_mirrors_of_other_modules_stay_identical(self):
        """本模組刻意不 import app.*（scheduler 與離線工具要用），同值的常數由這裡對帳。"""
        from app import profile as app_profile
        from app.services import judge_schema, llm_models
        from scripts import eval_compare

        self.assertEqual(pe.PROFILE_DIR_ENV, app_profile.PROFILE_DIR_ENV)
        self.assertEqual(pe.DEFAULT_PROFILE_DIR, app_profile.DEFAULT_PROFILE_DIR)
        self.assertEqual(judge_schema.DECLARED_LINEAGE_PREFIX, llm_models.JUDGE_LINEAGE_DECLARED_PREFIX)
        self.assertEqual(eval_compare.DECLARED_LINEAGE_PREFIX, llm_models.JUDGE_LINEAGE_DECLARED_PREFIX)

    def test_module_only_imports_stdlib_and_the_contract(self):
        import ast

        tree = ast.parse((REPO_ROOT / "eval" / "profile_eval.py").read_text(encoding="utf-8"))
        project = {
            n.module for n in ast.walk(tree)
            if isinstance(n, ast.ImportFrom) and n.module
            and n.module.split(".")[0] in {"app", "web", "scripts", "eval"}
        }
        self.assertEqual(project, {"eval.question_contract"})

    def test_policy_is_the_decided_one(self):
        self.assertEqual(pe.MIN_QUESTIONS, 30)
        self.assertEqual(set(pe.CATEGORIES), {"stock", "industry", "macro", "cross_report"})
        self.assertEqual(pe.GATE_METRICS, ("faithfulness", "context_precision", "answer_relevancy"))


class TingfongTests(unittest.TestCase):
    def test_tingfong_keeps_the_repo_question_set(self):
        ds = pe.load_profile_dataset(TINGFONG)
        repo = REPO_ROOT / "eval" / "ragas_questions.json"
        self.assertEqual(ds.path, repo.resolve())
        self.assertEqual(ds.sha256, hashlib.sha256(repo.read_bytes()).hexdigest())
        self.assertIsNone(ds.manifest)
        self.assertEqual(ds.rel_path, "eval/ragas_questions.json")
        self.assertIsNone(pe.own_questions_path(TINGFONG), "廷豐的題集不搬家，profile 不自帶")

    def test_tingfong_waives_only_cp_with_the_recorded_decision(self):
        cfg = pe.load_eval_config(TINGFONG)
        self.assertEqual(set(cfg.waivers), {"context_precision"})
        waiver = cfg.waivers["context_precision"]
        self.assertEqual(waiver.decided_on, date(2026, 11, 10))
        self.assertIn("fw-cp", waiver.reason)
        self.assertIn("被引用的比例", waiver.reason)
        self.assertEqual(cfg.judge_lineage, "gpt-6-luna-2026-11")

    def test_profile_arg_resolution(self):
        self.assertEqual(pe.resolve_profile_arg("tingfong"), TINGFONG.resolve())
        self.assertEqual(pe.resolve_profile_arg("profiles/tingfong"), TINGFONG.resolve())
        self.assertEqual(pe.resolve_profile_arg(str(TINGFONG)), TINGFONG.resolve())
        for bad in ("", "nosuch", "profiles/nosuch"):
            with self.subTest(bad=bad), self.assertRaises(pe.EvalProfileError):
                pe.resolve_profile_arg(bad)

    def test_active_profile_follows_profile_dir(self):
        self.assertEqual(pe.active_profile_dir({}), REPO_ROOT / "profiles" / "tingfong")
        self.assertEqual(pe.active_profile_dir({"PROFILE_DIR": "/srv/p"}), Path("/srv/p"))
        self.assertEqual(pe.active_profile_dir({"PROFILE_DIR": "profiles/x"}), REPO_ROOT / "profiles" / "x")


class DatasetArgTests(unittest.TestCase):
    def test_without_profile_the_dataset_argument_is_passed_through_verbatim(self):
        self.assertEqual(pe.dataset_arg(None, None), "eval/ragas_questions.json")
        self.assertEqual(pe.dataset_arg(None, "x/y.json"), "x/y.json")

    def test_profile_and_dataset_are_exclusive(self):
        with self.assertRaisesRegex(pe.EvalProfileError, "擇一"):
            pe.dataset_arg("tingfong", "eval/ragas_questions.json")

    def test_profile_resolves_to_its_verified_set(self):
        path = Path(pe.dataset_arg("tingfong", None))
        self.assertEqual(path.resolve(), (REPO_ROOT / "eval" / "ragas_questions.json").resolve())

    def test_profile_without_a_frozen_set_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaisesRegex(pe.EvalProfileError, "沒有凍結題集"):
                pe.load_profile_dataset(Path(td))


class EvalConfigTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.profile = Path(self._tmp.name)

    def test_missing_file_is_all_defaults(self):
        cfg = pe.load_eval_config(self.profile)
        self.assertEqual(dict(cfg.waivers), {})
        self.assertIsNone(cfg.legacy_repo_path)
        self.assertIsNone(cfg.judge_lineage)

    def test_valid_waiver(self):
        _write_config(self.profile, '[gate.waivers.answer_relevancy]\nreason = "這一項豁免的理由寫得夠清楚"\n'
                                    "decided_on = 2026-11-01\n")
        waiver = pe.load_eval_config(self.profile).waivers["answer_relevancy"]
        self.assertEqual(waiver.decided_on, date(2026, 11, 1))

    def test_fail_closed(self):
        reason = 'reason = "這一項豁免的理由寫得夠清楚"\n'
        cases = {
            "未知的鍵": "[gate]\nwaiver = 1\n",
            "未知的鍵 extra": "extra = 1\n",
            "必須附理由": '[gate.waivers.faithfulness]\ndecided_on = 2026-11-01\n',
            "至少 10 字": '[gate.waivers.faithfulness]\nreason = "太短"\ndecided_on = 2026-11-01\n',
            "決定日期": '[gate.waivers.faithfulness]\n' + reason + 'decided_on = "2026-11-01"\n',
            "不是可豁免的指標": '[gate.waivers.latency]\n' + reason + "decided_on = 2026-11-01\n",
            "不在 repo 內或不存在": '[questions]\nlegacy_repo_path = "eval/nosuch.json"\n',
            "相對 repo 根": '[questions]\nlegacy_repo_path = "/etc/passwd"\n',
            "不在 repo 內": '[questions]\nlegacy_repo_path = "../outside.json"\n',
            "judge_lineage": '[gate]\njudge_lineage = ""\n',
            "TOML 語法錯誤": "[gate\n",
        }
        for pattern, body in cases.items():
            with self.subTest(pattern=pattern):
                _write_config(self.profile, body)
                with self.assertRaisesRegex(pe.EvalProfileError, pattern.split(" ")[0]):
                    pe.load_eval_config(self.profile)

    def test_legacy_and_own_set_together_is_ambiguous(self):
        _write_config(self.profile, '[questions]\nlegacy_repo_path = "eval/ragas_questions.json"\n')
        (self.profile / "eval" / pe.QUESTIONS_FILE).write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(pe.EvalProfileError, "不知道要用哪一份"):
            pe.load_eval_config(self.profile)


class CoverageTests(unittest.TestCase):
    def test_coverage_problems_say_what_is_missing(self):
        cats = ["stock"] * 20 + ["industry"] * 6 + ["macro"] * 2
        problems = pe.coverage_problems(cats)
        text = "\n".join(problems)
        self.assertIn("核准題數 28", text)
        self.assertIn("總經（macro）只有 2 題", text)
        self.assertIn("跨篇比較（cross_report）只有 0 題", text)
        self.assertNotIn("產業", text)
        self.assertEqual(pe.coverage_problems(["stock", "industry", "macro", "cross_report"] * 8), [])


if __name__ == "__main__":
    unittest.main()
