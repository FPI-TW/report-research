# tests/test_eval_compare.py
"""`scripts/eval_compare.py` 的契約測試。

素材一律用 repo 內既有的四份 RAGAS 基準線與 `eval/before.json` / `after.json`——它們是
產生器**真實寫出來的形狀**，自己編的 fixture 只能驗到自己想像中的形狀（`notes` 在
`baseline-2026-07-29.json` 是單一字串、在舊基準線是陣列，這種差異編不出來）。
要造「劣化」時才在 tmpdir 改一份副本，且只動 summary、不動形狀。
"""

import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from copy import deepcopy
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import eval_compare as ec  # noqa: E402

BASELINES = REPO_ROOT / "eval" / "baselines"
RAGAS_CLEAN = BASELINES / "baseline-2026-07-29.json"
RAGAS_M0 = BASELINES / "baseline-m0.json"
RAGAS_M2 = BASELINES / "baseline-m2.json"
RAGAS_M4 = BASELINES / "m4-corpus-qa.json"
RAGAS_0902 = BASELINES / "baseline-2026-09-02.json"
RETRIEVAL_BEFORE = REPO_ROOT / "eval" / "before.json"
RETRIEVAL_AFTER = REPO_ROOT / "eval" / "after.json"


def load(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def run_main(argv: list[str]) -> tuple[int, str]:
    """跑 CLI 進入點並回 (退出碼, stdout)。退出碼是這支工具唯一的對外契約。"""
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = ec.main(argv)
    return code, buf.getvalue()


def compare(base: dict, cand: dict, **kw) -> ec.Comparison:
    return ec.build_comparison(base, cand, **kw)


def row(cmp_: ec.Comparison, key: str) -> ec.Row:
    return next(r for r in cmp_.rows if r.key == key)


def write_variant(tmpdir: str, name: str, doc: dict, **summary_updates) -> str:
    """把某份真實結果檔複製一份、只覆寫 summary 的指定鍵，回寫出的路徑。"""
    variant = deepcopy(doc)
    variant["summary"].update(summary_updates)
    path = Path(tmpdir) / name
    path.write_text(json.dumps(variant, ensure_ascii=False), encoding="utf-8")
    return str(path)


class TestShapes(unittest.TestCase):
    """兩種真實形狀都要讀得進來（RAGAS 平坦數值＋布林門檻、檢索平坦數值無門檻）。"""

    def test_ragas_flat_shape_self_compare_is_clean(self):
        code, out = run_main(["--baseline", str(RAGAS_CLEAN), "--candidate", str(RAGAS_CLEAN)])
        self.assertEqual(code, 0, out)
        self.assertIn("faithfulness", out)
        self.assertIn("無劣化", out)

    def test_retrieval_flat_shape(self):
        cmp_ = compare(load(RETRIEVAL_BEFORE), load(RETRIEVAL_AFTER))
        self.assertEqual(cmp_.shape_base, "retrieval")
        self.assertEqual(row(cmp_, "hit_rate").status, ec.STATUS_SAME)

    def test_cross_shape_comparison_is_refused(self):
        """RAGAS 與檢索共用 n_errors 之類的鍵，光看「有共同指標」會比出一個像樣的 delta。"""
        cmp_ = compare(load(RAGAS_M0), load(RETRIEVAL_BEFORE))
        self.assertEqual(cmp_.exit_code, 2)
        self.assertTrue(any("形狀不同" in r for r in cmp_.incomparable), cmp_.incomparable)

    def test_notes_string_is_not_iterated_char_by_char(self):
        """`notes` 在乾淨基準是單一字串。對字串迭代不會拋例外，只會印幾百行單字。"""
        self.assertIsInstance(load(RAGAS_CLEAN)["notes"], str)
        cmp_ = compare(load(RAGAS_M4), load(RAGAS_CLEAN))
        self.assertEqual(len(cmp_.notes_cand), 1)
        self.assertIn("乾淨基準", cmp_.notes_cand[0])


class TestRegressionGate(unittest.TestCase):
    def test_degradation_beyond_tolerance_exits_nonzero(self):
        """m4 → 2026-07-29 的 context_precision 掉 0.098，遠超預設容忍值。"""
        code, out = run_main(["--baseline", str(RAGAS_M4), "--candidate", str(RAGAS_CLEAN)])
        self.assertEqual(code, 1)
        self.assertIn("context_precision", out)
        self.assertIn("劣化", out)

    def test_change_within_tolerance_is_not_a_regression(self):
        """m0 → m4：faithfulness -0.014（雜訊內）、CP/AR 皆改善 ⇒ 通過。"""
        cmp_ = compare(load(RAGAS_M0), load(RAGAS_M4))
        self.assertEqual(cmp_.exit_code, 0)
        self.assertEqual(row(cmp_, "faithfulness").status, ec.STATUS_SAME)
        self.assertEqual(row(cmp_, "context_precision").status, ec.STATUS_BETTER)

    def test_tighter_tolerance_makes_the_same_pair_red(self):
        cmp_ = compare(load(RAGAS_M0), load(RAGAS_M4), tolerance=0.01)
        self.assertEqual(cmp_.exit_code, 1)
        self.assertEqual([r.key for r in cmp_.regressions], ["faithfulness"])

    def test_improvement_exits_zero(self):
        code, _ = run_main(["--baseline", str(RETRIEVAL_BEFORE), "--candidate", str(RETRIEVAL_AFTER)])
        self.assertEqual(code, 0)


class TestDirectionality(unittest.TestCase):
    """越小越好的指標：變大才是劣化。方向猜錯就是製造假綠。"""

    def test_lower_is_better_metric_improves_when_it_drops(self):
        cmp_ = compare(load(RETRIEVAL_BEFORE), load(RETRIEVAL_AFTER))
        r = row(cmp_, "median_cited_age")  # 233 → 163 天
        self.assertEqual(r.spec.direction, ec.LOWER)
        self.assertEqual(r.status, ec.STATUS_BETTER)
        self.assertFalse(r.regression)

    def test_lower_is_better_metric_regresses_when_it_grows(self):
        cmp_ = compare(load(RETRIEVAL_AFTER), load(RETRIEVAL_BEFORE))  # 反過來比
        self.assertEqual(cmp_.exit_code, 1)
        keys = [r.key for r in cmp_.regressions]
        self.assertIn("median_cited_age", keys)
        self.assertIn("pct_over_max_age", keys)

    def test_error_count_growth_is_a_regression(self):
        """n_errors 是越小越好。m2 比 m0 多掉一題（同時也讓兩者不可比）。"""
        cmp_ = compare(load(RAGAS_M0), load(RAGAS_M2))
        r = row(cmp_, "n_errors")
        self.assertEqual(r.spec.direction, ec.LOWER)
        self.assertEqual(r.status, ec.STATUS_WORSE)

    def test_latency_uses_relative_tolerance(self):
        """對 45,388 ms 套絕對 0.03 等於「差 0.03 毫秒就是回歸」。"""
        base = load(RAGAS_CLEAN)
        mean = base["summary"]["latency_ms_mean"]
        with tempfile.TemporaryDirectory() as tmp:
            slower_a = load(write_variant(tmp, "a.json", base, latency_ms_mean=mean * 1.05))
            slower_b = load(write_variant(tmp, "b.json", base, latency_ms_mean=mean * 1.40))
        self.assertEqual(row(compare(base, slower_a), "latency_ms_mean").status, ec.STATUS_SAME)
        self.assertEqual(row(compare(base, slower_b), "latency_ms_mean").status, ec.STATUS_WORSE)

    def test_descriptive_metrics_never_gate(self):
        """引用總數隨脈絡篇數浮動、方向有歧義，刻意不判定。"""
        base = load(RETRIEVAL_BEFORE)
        with tempfile.TemporaryDirectory() as tmp:
            fewer = load(write_variant(tmp, "c.json", base, n_cited=base["summary"]["n_cited"] - 5))
        cmp_ = compare(base, fewer)
        self.assertFalse(row(cmp_, "n_cited").gating)
        self.assertEqual(cmp_.exit_code, 0)

    def test_flag_true_to_false_is_a_regression(self):
        base = load(RAGAS_CLEAN)
        with tempfile.TemporaryDirectory() as tmp:
            passing = load(write_variant(tmp, "p.json", base, thresholds_pass=True))
        cmp_ = compare(passing, base)  # True → False
        self.assertEqual([r.key for r in cmp_.regressions], ["thresholds_pass"])
        self.assertEqual(cmp_.exit_code, 1)


class TestComparability(unittest.TestCase):
    def test_effective_sample_change_refuses_verdict_but_still_prints(self):
        """m2 名目 n=8、實際只有 7 題入均值（一題 error）。光比 n 看不出來。"""
        code, out = run_main(["--baseline", str(RAGAS_M0), "--candidate", str(RAGAS_M2)])
        self.assertEqual(code, 2)
        self.assertIn("不可比", out)
        self.assertIn("n_effective 8 → 7", out)
        self.assertIn("faithfulness", out)  # 表照印，只是不給結論

    def test_nominal_n_change_is_caught(self):
        base = load(RAGAS_CLEAN)
        with tempfile.TemporaryDirectory() as tmp:
            bigger = load(write_variant(tmp, "n.json", base, n=30))
        self.assertEqual(compare(base, bigger).exit_code, 2)

    def test_queryset_parameter_change_is_incomparable(self):
        """max_age_days 變了，recency_pass_rate 與 pct_over_max_age 的定義就變了。"""
        base = load(RETRIEVAL_BEFORE)
        with tempfile.TemporaryDirectory() as tmp:
            relaxed = load(write_variant(tmp, "q.json", base, max_age_days=730))
        self.assertEqual(compare(base, relaxed).exit_code, 2)

    def test_key_present_on_one_side_only_is_reported_not_gated(self):
        """舊基準線沒有 latency_ms_*：不能拿「缺值」當劣化，也不能當沒看見。"""
        clean = load(RAGAS_CLEAN)
        older = deepcopy(clean)
        for key in ("latency_ms_mean", "latency_ms_p50", "latency_ms_p95"):
            older["summary"].pop(key)
        cmp_ = compare(clean, older)
        self.assertEqual(cmp_.only_in_baseline, ["latency_ms_mean", "latency_ms_p50", "latency_ms_p95"])
        self.assertEqual(cmp_.exit_code, 0)


# run_ragas（PR-06 起）寫出的量尺三鍵與新指標；值取自一份真實的 run 形狀即可。
_SCALE = {
    "judge_model": "claude-haiku-4-5",
    "judge_prompt_sha": "a" * 64,
    "judge_schema_version": 1,
}
_NEW_METRICS = {
    "n_judge_errors": 0,
    "citation_rate": 1.0,
    "simplified_residual_rate": 0.0,
    "n_truncated": 0,
}


class TestJudgeScale(unittest.TestCase):
    """META 量尺：不同即不可比；**只有一邊有記錄也不可比**（非 META 鍵維持只列出）。"""

    def _variant(self, tmp, name, doc, **updates):
        return load(write_variant(tmp, name, doc, **updates))

    def test_same_scale_on_both_sides_is_comparable(self):
        base = load(RAGAS_CLEAN)
        with tempfile.TemporaryDirectory() as tmp:
            a = self._variant(tmp, "a.json", base, **_SCALE, **_NEW_METRICS)
            b = self._variant(tmp, "b.json", base, **_SCALE, **_NEW_METRICS)
        cmp_ = compare(a, b)
        self.assertEqual(cmp_.exit_code, 0, cmp_.incomparable)
        self.assertEqual(cmp_.unclassified, [])

    def test_different_judge_model_is_incomparable(self):
        base = load(RAGAS_CLEAN)
        with tempfile.TemporaryDirectory() as tmp:
            a = self._variant(tmp, "a.json", base, **_SCALE)
            b = self._variant(tmp, "b.json", base, **{**_SCALE, "judge_model": "deepseek-flash"})
        cmp_ = compare(a, b)
        self.assertEqual(cmp_.exit_code, 2)
        self.assertTrue(any("judge_model" in r for r in cmp_.incomparable), cmp_.incomparable)

    def test_prompt_or_schema_change_is_incomparable(self):
        base = load(RAGAS_CLEAN)
        with tempfile.TemporaryDirectory() as tmp:
            a = self._variant(tmp, "a.json", base, **_SCALE)
            b = self._variant(tmp, "b.json", base, **{**_SCALE, "judge_prompt_sha": "b" * 64})
            c = self._variant(tmp, "c.json", base, **{**_SCALE, "judge_schema_version": 2})
        self.assertEqual(compare(a, b).exit_code, 2)
        self.assertEqual(compare(a, c).exit_code, 2)

    def test_meta_on_one_side_only_is_incomparable_both_directions(self):
        """舊基準線沒有記錄量尺：不知道它是哪把尺量的，就不能當成同一把。"""
        old = load(RAGAS_0902)
        self.assertNotIn("judge_model", old["summary"])
        with tempfile.TemporaryDirectory() as tmp:
            new = self._variant(tmp, "n.json", old, **_SCALE, **_NEW_METRICS)
        for base, cand, side in ((old, new, "candidate"), (new, old, "baseline")):
            cmp_ = compare(base, cand)
            self.assertEqual(cmp_.exit_code, 2)
            msgs = [r for r in cmp_.incomparable if "量尺只有" in r]
            self.assertEqual(len(msgs), 3, cmp_.incomparable)
            self.assertTrue(all(side in m for m in msgs))

    def test_old_vs_old_without_meta_stays_comparable(self):
        cmp_ = compare(load(RAGAS_M0), load(RAGAS_M4))
        self.assertEqual(cmp_.exit_code, 0)

    def test_new_metric_directions(self):
        specs = ec.METRIC_SPECS
        self.assertEqual(specs["citation_rate"].direction, ec.HIGHER)
        self.assertEqual(specs["simplified_residual_rate"].direction, ec.LOWER)
        self.assertEqual(specs["n_truncated"].direction, ec.LOWER)
        # judge 出錯是量尺故障，不判方向；它的後果由題目集合檢查擋成不可比（TestJudgedQuestionSet）。
        self.assertEqual(specs["n_judge_errors"].direction, ec.INFO)
        for key in ("n_effective_faithfulness", "n_effective_context_precision",
                    "n_effective_answer_relevancy", "judged_ids_sha"):
            self.assertEqual(specs[key].direction, ec.SAMPLE)
        for key in _SCALE:
            self.assertEqual(specs[key].direction, ec.META)


def _judged_doc(base: dict, *, judge_errors: dict[str, list[str]] | None = None, **cp_override) -> dict:
    """以真實 RAGAS 結果檔為底，模擬 run_ragas 的輸出：指定題目的指定指標 judge 出錯（None＋
    judge_errors），再照 run_ragas.aggregate 的規則重寫三個均值、n_effective_* 與 judged_ids_sha。

    cp_override：{"q001": 0.2} 之類，改寫某題的 context_precision（造「分數真的變了」）。
    """
    doc = deepcopy(base)
    judge_errors = judge_errors or {}
    for c in doc["cases"]:
        for metric, ids in judge_errors.items():
            if c["id"] in ids:
                c[metric] = None
                c.setdefault("judge_errors", {})[metric] = "JudgeError: unbalanced JSON"
        if c["id"] in cp_override:
            c["context_precision"] = cp_override[c["id"]]
    summary = doc["summary"]
    summary.update(_SCALE)
    summary.update(_NEW_METRICS)
    summary["n_judge_errors"] = sum(len(ids) for ids in judge_errors.values())
    for m in ec.JUDGE_METRICS:
        vals = [c[m] for c in doc["cases"] if "error" not in c and c.get(m) is not None]
        summary[m] = sum(vals) / len(vals)
        summary[f"n_effective_{m}"] = len(ec.judged_ids(doc["cases"], m))
    summary["judged_ids_sha"] = ec.judged_ids_sha(doc["cases"])
    return doc


def _dump(tmp: str, name: str, doc: dict) -> str:
    path = Path(tmp) / name
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return str(path)


class TestJudgedQuestionSet(unittest.TestCase):
    """judge 出錯不能被靜默當成可比，也不能被誤報成生成端劣化（審查 M-1）。

    judge 出錯只讓該題該指標為 None；於是 F／CP／AR 的均值可能是在不同題集上算的。
    判定：題目集合不同 → 2（附補救方法）；集合相同 → 照常比；--common-only → 在交集上比。
    """

    def setUp(self):
        self.base = load(RAGAS_CLEAN)

    def test_judge_error_on_one_side_is_incomparable_not_a_regression(self):
        a = _judged_doc(self.base)
        b = _judged_doc(self.base, judge_errors={"context_precision": ["q003"]})
        cmp_ = compare(a, b)
        self.assertEqual(cmp_.exit_code, 2)  # 不是 1：尺壞了不等於東西變差了
        self.assertEqual(cmp_.regressions, [])
        self.assertFalse(row(cmp_, "n_judge_errors").gating)
        msg = next(r for r in cmp_.incomparable if "入均值的題目不同" in r)
        self.assertIn("q003", msg)
        self.assertIn("--common-only", msg)
        self.assertIn("補跑", msg)

    def test_same_error_count_on_different_questions_is_incomparable(self):
        """兩邊各錯一題、題數相同：只比 n 看不出來，F／CP／AR 其實是在不同題集上算的。"""
        a = _judged_doc(self.base, judge_errors={"context_precision": ["q002"]})
        b = _judged_doc(self.base, judge_errors={"context_precision": ["q005"]})
        self.assertEqual(
            a["summary"]["n_effective_context_precision"], b["summary"]["n_effective_context_precision"]
        )
        cmp_ = compare(a, b)
        self.assertEqual(cmp_.exit_code, 2)
        msg = next(r for r in cmp_.incomparable if "入均值的題目不同" in r)
        self.assertIn("只在 baseline 入均值 q005", msg)
        self.assertIn("只在 candidate 入均值 q002", msg)

    def test_same_failed_questions_on_both_sides_stay_comparable(self):
        a = _judged_doc(self.base, judge_errors={"faithfulness": ["q004"]})
        b = _judged_doc(self.base, judge_errors={"faithfulness": ["q004"]})
        cmp_ = compare(a, b)
        self.assertEqual(cmp_.exit_code, 0, cmp_.incomparable)
        self.assertEqual(cmp_.unclassified, [])

    def test_real_regression_on_the_same_question_set_still_gates(self):
        a = _judged_doc(self.base)
        b = _judged_doc(self.base, **{f"q00{i}": 0.0 for i in range(1, 9)})
        cmp_ = compare(a, b)
        self.assertEqual(cmp_.exit_code, 1)
        self.assertIn("context_precision", [r.key for r in cmp_.regressions])

    def test_common_only_compares_on_the_intersection(self):
        """交集上重取平均：q003 在 candidate 出錯 → 兩邊都只用其餘 7 題，兩邊逐字相同 ⇒ 可比且無劣化。"""
        a = _judged_doc(self.base)
        b = _judged_doc(self.base, judge_errors={"context_precision": ["q003"]})
        with tempfile.TemporaryDirectory() as tmp:
            pa, pb = _dump(tmp, "a.json", a), _dump(tmp, "b.json", b)
            code_plain, _ = run_main(["--baseline", pa, "--candidate", pb])
            code, out = run_main(["--baseline", pa, "--candidate", pb, "--common-only", "--json"])
        self.assertEqual(code_plain, 2)
        payload = json.loads(out)
        self.assertEqual(code, 0, payload["incomparable"])
        cp = next(m for m in payload["metrics"] if m["key"] == "context_precision")
        expected = [c["context_precision"] for c in a["cases"] if c["id"] != "q003"]
        self.assertAlmostEqual(cp["baseline"], sum(expected) / len(expected))
        self.assertAlmostEqual(cp["candidate"], cp["baseline"])
        self.assertEqual(payload["sample"]["baseline"]["n_effective_context_precision"], 7)
        # 門檻旗標是在原題集上算的：這個模式下不判定
        self.assertNotIn("thresholds_pass", [m["key"] for m in payload["metrics"]])
        self.assertTrue(any("7 題" in n for n in payload["common_only"]))

    def test_common_only_still_catches_a_real_regression(self):
        a = _judged_doc(self.base)
        b = _judged_doc(
            self.base, judge_errors={"context_precision": ["q003"]}, **{f"q00{i}": 0.0 for i in (1, 2, 4, 5)}
        )
        with tempfile.TemporaryDirectory() as tmp:
            code, out = run_main(
                ["--baseline", _dump(tmp, "a.json", a), "--candidate", _dump(tmp, "b.json", b), "--common-only"]
            )
        self.assertEqual(code, 1, out)
        self.assertIn("context_precision", out)

    def test_common_only_needs_per_case_results(self):
        a = _judged_doc(self.base)
        b = deepcopy(a)
        del b["cases"]
        with tempfile.TemporaryDirectory() as tmp:
            code, out = run_main(
                ["--baseline", _dump(tmp, "a.json", a), "--candidate", _dump(tmp, "b.json", b), "--common-only"]
            )
        self.assertEqual(code, 2)
        self.assertIn("cases", out)

    def test_judged_ids_rules(self):
        """與 run_ragas._mean_of 同一條規則：error 題與 None 不入均值；沒有 id 時退回問題文字。"""
        cases = [
            {"id": "q2", "faithfulness": 0.5},
            {"id": "q1", "faithfulness": 1.0},
            {"id": "q3", "faithfulness": None},
            {"id": "q4", "error": "boom", "faithfulness": 1.0},
            {"question": "沒有 id 的題", "faithfulness": 0.0},
        ]
        self.assertEqual(ec.judged_ids(cases, "faithfulness"), ["q1", "q2", "沒有 id 的題"])
        self.assertEqual(ec.judged_ids_sha(cases), ec.judged_ids_sha(list(reversed(cases))))
        self.assertNotEqual(ec.judged_ids_sha(cases), ec.judged_ids_sha(cases[1:]))


class TestUnclassifiedKeys(unittest.TestCase):
    def test_unknown_key_is_printed_and_blocks_a_clean_pass(self):
        """不認得的鍵不能默默當成「越大越好」——那是製造假綠的主要方式。"""
        base = load(RAGAS_CLEAN)
        with tempfile.TemporaryDirectory() as tmp:
            path = write_variant(tmp, "u.json", base, brand_new_metric=0.5)
            code, out = run_main(["--baseline", str(RAGAS_CLEAN), "--candidate", path])
        self.assertEqual(code, 3)
        self.assertIn("未分類", out)
        self.assertIn("brand_new_metric", out)
        self.assertIn("METRIC_SPECS", out)  # 印出修法

    def test_unclassified_key_does_not_mask_a_real_regression(self):
        base = load(RAGAS_M4)
        with tempfile.TemporaryDirectory() as tmp:
            path = write_variant(tmp, "u2.json", load(RAGAS_CLEAN), brand_new_metric=0.5)
            cmp_ = compare(base, load(path))
        self.assertEqual(cmp_.exit_code, 1)  # 劣化優先於「未分類」
        self.assertIn("brand_new_metric", cmp_.unclassified)

    def test_every_key_in_every_shipped_baseline_is_classified(self):
        """方向表必須覆蓋 repo 內所有真實結果檔——否則這支工具第一次跑就是黃燈。"""
        for path in (RAGAS_CLEAN, RAGAS_M0, RAGAS_M2, RAGAS_M4, RAGAS_0902,
                     RETRIEVAL_BEFORE, RETRIEVAL_AFTER):
            unknown = sorted(set(load(path)["summary"]) - set(ec.METRIC_SPECS))
            self.assertEqual(unknown, [], f"{path.name} 有未分類的鍵：{unknown}")


class TestCli(unittest.TestCase):
    def test_missing_summary_is_an_input_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "not-a-result.json"
            path.write_text('{"hello": 1}', encoding="utf-8")
            code, out = run_main(["--baseline", str(RAGAS_CLEAN), "--candidate", str(path)])
        self.assertEqual(code, 2)
        self.assertIn("summary", out)

    def test_missing_file_is_an_input_error(self):
        code, out = run_main(["--baseline", str(RAGAS_CLEAN), "--candidate", "/nonexistent.json"])
        self.assertEqual(code, 2)
        self.assertIn("讀不到檔案", out)

    def test_json_output_is_machine_readable(self):
        code, out = run_main(
            ["--baseline", str(RAGAS_M4), "--candidate", str(RAGAS_CLEAN), "--json"]
        )
        payload = json.loads(out)
        self.assertEqual(code, 1)
        self.assertEqual(payload["exit_code"], 1)
        cp = next(m for m in payload["metrics"] if m["key"] == "context_precision")
        self.assertTrue(cp["regression"])
        self.assertLess(cp["delta"], 0)

    def test_real_subprocess_exit_code(self):
        """退出碼是給 shell 與 make 用的契約，在同一個行程內驗不到真的 exit。"""
        proc = subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "eval_compare.py"),
             "--baseline", str(RAGAS_M4), "--candidate", str(RAGAS_CLEAN)],
            capture_output=True, text=True, cwd=str(REPO_ROOT),
        )
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertIn("context_precision", proc.stdout)

    def test_script_has_no_heavy_imports(self):
        """刻意只吃 stdlib：一旦 import app.services.*，這支工具就要先載 torch。"""
        proc = subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.path.insert(0, 'scripts'); import eval_compare; "
             "print([m for m in sys.modules if m.split('.')[0] in ('torch', 'app', 'web')])"],
            capture_output=True, text=True, cwd=str(REPO_ROOT),
        )
        self.assertEqual(proc.stdout.strip(), "[]", proc.stdout + proc.stderr)


if __name__ == "__main__":
    unittest.main()
