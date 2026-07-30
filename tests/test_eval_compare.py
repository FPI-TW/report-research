# tests/test_eval_compare.py
"""`scripts/eval_compare.py` 的契約測試。

素材一律用 repo 內既有的五份基準線與 `eval/before.json` / `after.json`——它們是三支
產生器**真實寫出來的形狀**，自己編的 fixture 只能驗到自己想像中的形狀（`notes` 在
`report-m1b.json` 是陣列、在 `baseline-2026-07-29.json` 是單一字串，這種差異編不出來）。
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
REPORT_M1B = BASELINES / "report-m1b.json"
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
    """三種真實形狀都要讀得進來（平坦數值、{mean,n_valid} 嵌套、無布林門檻）。"""

    def test_ragas_flat_shape_self_compare_is_clean(self):
        code, out = run_main(["--baseline", str(RAGAS_CLEAN), "--candidate", str(RAGAS_CLEAN)])
        self.assertEqual(code, 0, out)
        self.assertIn("faithfulness", out)
        self.assertIn("無劣化", out)

    def test_report_nested_shape_reads_mean_and_n_valid(self):
        doc = load(REPORT_M1B)
        cmp_ = compare(doc, doc)
        self.assertEqual(cmp_.exit_code, 0)
        r = row(cmp_, "facet_coverage")
        # 嵌套形狀：值取 mean，n_valid 另存——直接拿 dict 去相減會 TypeError。
        self.assertEqual(r.base, 1.0)
        self.assertEqual(r.n_base, 8)
        self.assertEqual(row(cmp_, "source_citation_rate").n_base, 10)

    def test_report_shape_has_no_thresholds_pass(self):
        """研報那套沒有布林門檻——比較器不能假設它存在。"""
        self.assertNotIn("thresholds_pass", load(REPORT_M1B)["summary"])
        self.assertEqual(compare(load(REPORT_M1B), load(REPORT_M1B)).exit_code, 0)

    def test_retrieval_flat_shape(self):
        cmp_ = compare(load(RETRIEVAL_BEFORE), load(RETRIEVAL_AFTER))
        self.assertEqual(cmp_.shape_base, "retrieval")
        self.assertEqual(row(cmp_, "hit_rate").status, ec.STATUS_SAME)

    def test_cross_shape_comparison_is_refused(self):
        """RAGAS 與研報共用 n / n_errors，光看「有共同指標」會比出一個像樣的 delta。"""
        cmp_ = compare(load(RAGAS_M0), load(REPORT_M1B))
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
        """來源多樣性方向有歧義（MMR 刻意對同來源設上限），刻意不判定。"""
        base = load(REPORT_M1B)
        with tempfile.TemporaryDirectory() as tmp:
            fewer = load(write_variant(tmp, "c.json", base, n_reports={"mean": 3.0, "n_valid": 10}))
        cmp_ = compare(base, fewer)
        self.assertFalse(row(cmp_, "n_reports").gating)
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

    def test_per_metric_n_valid_change_marks_only_that_metric(self):
        """嵌套形狀每個指標有自己的分母：n_valid 不同的兩個均值不能相減。"""
        base = load(REPORT_M1B)
        with tempfile.TemporaryDirectory() as tmp:
            moved = load(
                write_variant(tmp, "v.json", base, facet_coverage={"mean": 0.5, "n_valid": 9})
            )
        cmp_ = compare(base, moved)
        r = row(cmp_, "facet_coverage")
        self.assertEqual(r.status, ec.STATUS_UNCOMPARABLE)
        self.assertFalse(r.regression)
        self.assertIn("n_valid", r.note)
        self.assertEqual(cmp_.exit_code, 0)  # 其餘指標無劣化

    def test_ruleset_version_change_is_incomparable(self):
        """report_metrics 自己聲明 v1 基準線不可與 v2 直接比較。"""
        base = load(REPORT_M1B)
        with tempfile.TemporaryDirectory() as tmp:
            v3 = load(write_variant(tmp, "r.json", base, ruleset_version=3))
        cmp_ = compare(base, v3)
        self.assertEqual(cmp_.exit_code, 2)
        self.assertTrue(any("ruleset_version" in r for r in cmp_.incomparable))

    def test_insufficient_n_declared_by_the_result_itself(self):
        """`sufficient_n=false` 是產生者自己說「這份不得用於比較」。"""
        base = load(REPORT_M1B)
        with tempfile.TemporaryDirectory() as tmp:
            thin = load(write_variant(tmp, "s.json", base, sufficient_n=False))
        self.assertEqual(compare(base, thin).exit_code, 2)

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
        for path in (RAGAS_CLEAN, RAGAS_M0, RAGAS_M2, RAGAS_M4, REPORT_M1B,
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
