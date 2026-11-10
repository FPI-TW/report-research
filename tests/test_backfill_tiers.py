"""app/services/backfill_tiers.py：分層（含 NULL 與邊界月份）、待補判斷的 SQL、token 估計與每日上限的帳。

純函式與 SQL 字串；不連 DB、不呼叫 LLM。SQL 在真的 PostgreSQL 上的語意由 `tests/test_backfill_tiers_db.py` 驗。
"""
from __future__ import annotations

import ast
import json
import re
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services import backfill_tiers as bt  # noqa: E402
from app.services import llm_failures  # noqa: E402

MODELS = {t: f"model-{t}" for t in bt.DERIVED_TASKS}


def _binds(sql: str) -> set[str]:
    return set(re.findall(r"(?<!:):([a-z_]+)", sql))


class RecentCutoffTests(unittest.TestCase):
    def test_same_day_months_back(self):
        self.assertEqual(bt.recent_cutoff(date(2026, 11, 10), 12), date(2025, 11, 10))
        self.assertEqual(bt.recent_cutoff(date(2026, 11, 10), 1), date(2026, 10, 10))
        self.assertEqual(bt.recent_cutoff(date(2026, 11, 10), 24), date(2024, 11, 10))

    def test_crosses_year(self):
        self.assertEqual(bt.recent_cutoff(date(2026, 1, 15), 1), date(2025, 12, 15))
        self.assertEqual(bt.recent_cutoff(date(2026, 2, 1), 14), date(2024, 12, 1))

    def test_month_end_is_clamped(self):
        self.assertEqual(bt.recent_cutoff(date(2026, 3, 31), 1), date(2026, 2, 28))
        self.assertEqual(bt.recent_cutoff(date(2024, 3, 31), 1), date(2024, 2, 29))  # 閏年
        self.assertEqual(bt.recent_cutoff(date(2026, 5, 31), 1), date(2026, 4, 30))
        self.assertEqual(bt.recent_cutoff(date(2025, 2, 28), 12), date(2024, 2, 28))

    def test_months_must_be_positive(self):
        with self.assertRaises(ValueError):
            bt.recent_cutoff(date(2026, 1, 1), 0)


class TierOfTests(unittest.TestCase):
    CUT = date(2025, 11, 10)

    def test_null_report_date_is_tier3(self):
        self.assertEqual(bt.tier_of(None, self.CUT), bt.TIER_OLDER)

    def test_boundary_day_is_recent(self):
        self.assertEqual(bt.tier_of(self.CUT, self.CUT), bt.TIER_RECENT)

    def test_day_before_boundary_is_older(self):
        self.assertEqual(bt.tier_of(date(2025, 11, 9), self.CUT), bt.TIER_OLDER)

    def test_boundary_month_edges(self):
        # 2026-03-31 起算一個月：窗期起點夾到 2026-02-28，2/27 是第三層、2/28 與 3/1 是第二層。
        cut = bt.recent_cutoff(date(2026, 3, 31), 1)
        self.assertEqual(bt.tier_of(date(2026, 2, 27), cut), bt.TIER_OLDER)
        self.assertEqual(bt.tier_of(date(2026, 2, 28), cut), bt.TIER_RECENT)
        self.assertEqual(bt.tier_of(date(2026, 3, 1), cut), bt.TIER_RECENT)

    def test_future_date_is_recent(self):
        self.assertEqual(bt.tier_of(date(2099, 1, 1), self.CUT), bt.TIER_RECENT)

    def test_condition_sql_matches_tier_of(self):
        """SQL 片段與 tier_of 同義：第二層 `>=`、第三層 `< 或 NULL`（兩者互補，NULL 只落在第三層）。"""
        self.assertEqual(bt.tier_condition_sql(2), "r.report_date >= CAST(:cutoff AS date)")
        self.assertEqual(bt.tier_condition_sql(3),
                         "(r.report_date IS NULL OR r.report_date < CAST(:cutoff AS date))")
        with self.assertRaises(ValueError):
            bt.tier_condition_sql(1)

    def test_condition_sql_semantics_on_sqlite(self):
        import sqlite3

        con = sqlite3.connect(":memory:")
        con.execute("CREATE TABLE research_report (id INTEGER, report_date TEXT)")
        rows = [(1, None), (2, "2025-11-09"), (3, "2025-11-10"), (4, "2026-01-01")]
        con.executemany("INSERT INTO research_report VALUES (?, ?)", rows)
        for tier in (2, 3):
            cond = bt.tier_condition_sql(tier).replace("CAST(:cutoff AS date)", "'2025-11-10'")
            got = {i for (i,) in con.execute(f"SELECT id FROM research_report r WHERE {cond}")}
            want = {i for i, d in rows if bt.tier_of(date.fromisoformat(d) if d else None, self.CUT) == tier}
            self.assertEqual(got, want, tier)


class PendingSqlTests(unittest.TestCase):
    def test_each_task_has_its_own_skip_params(self):
        sql = bt.build_candidates_sql(3, skip_blocked=True, limit=True, exclude=True)
        params = {**bt.skip_params(MODELS), "cutoff": None, "limit": None, "exclude": None}
        self.assertEqual(_binds(sql), set(params))
        for task in bt.DERIVED_TASKS:
            self.assertEqual(params[f"llm_skip_task_{task}"], task)
            self.assertEqual(params[f"llm_skip_model_{task}"], f"model-{task}")

    def test_without_skip_list(self):
        sql = bt.build_candidates_sql(2, skip_blocked=False, limit=False)
        self.assertNotIn("llm_task_failure", sql)
        self.assertEqual(_binds(sql), {"cutoff"})

    def test_selection_ignores_signal_but_reports_it(self):
        """挑候選只看摘要、標題、摘錄；訊號只回報（子集外的研報永遠沒有訊號列，不能拿來挑）。"""
        sql = bt.build_candidates_sql(2, skip_blocked=False, limit=False)
        where = sql.split("FROM research.research_report r WHERE ", 1)[1]
        self.assertNotIn("report_signal", where)
        self.assertIn("AS need_signal", sql)
        self.assertIn("ORDER BY r.report_date DESC NULLS LAST, r.file_hash", sql)

    def test_doc_type_feature_per_task(self):
        for task, feature in bt.TASK_FEATURE.items():
            with self.subTest(task=task):
                self.assertIn("r.doc_type IN ('research')", bt.pending_expr(task, skip_blocked=False))
                self.assertEqual(feature, task)

    def test_done_definitions(self):
        self.assertIn("r.summary IS NULL", bt.pending_expr("summary"))
        self.assertIn("r.title IS NULL", bt.pending_expr("title"))
        self.assertIn("research.report_takeaway t", bt.pending_expr("takeaway"))
        self.assertIn("extraction_status IN ('valid', 'partial')", bt.pending_expr("takeaway"))
        signal = bt.pending_expr("signal")
        self.assertIn("r.report_date IS NOT NULL", signal)
        self.assertIn("cardinality(r.stock_targets)", signal)
        with self.assertRaises(ValueError):
            bt.pending_expr("brief")

    def test_bind_casts_follow_the_guard(self):
        for sql in (bt.build_candidates_sql(3, skip_blocked=True, limit=True, exclude=True),
                    bt.build_status_sql(skip_blocked=True), bt.build_estimate_sql(skip_blocked=True)):
            self.assertNotRegex(sql, r":[a-z_]+::")

    def test_status_and_estimate_binds(self):
        skip = set(bt.skip_params(MODELS))
        self.assertEqual(_binds(bt.build_status_sql(skip_blocked=True)), skip | {"cutoff"})
        self.assertEqual(_binds(bt.build_estimate_sql(skip_blocked=True)), skip | {"after", "batch"})
        self.assertEqual(_binds(bt.build_estimate_sql(skip_blocked=False)), {"after", "batch"})


class SkipClauseSuffixTests(unittest.TestCase):
    def test_default_is_unchanged(self):
        self.assertIn(":llm_skip_task AND f.model = :llm_skip_model ", llm_failures.skip_clause_sql("r"))
        self.assertEqual(llm_failures.skip_params("summary", "m"),
                         {"llm_skip_task": "summary", "llm_skip_model": "m"})

    def test_suffix(self):
        sql = llm_failures.skip_clause_sql("r", param_suffix="_title")
        self.assertIn(":llm_skip_task_title AND f.model = :llm_skip_model_title ", sql)
        self.assertEqual(llm_failures.skip_params("title", "m", param_suffix="_title"),
                         {"llm_skip_task_title": "title", "llm_skip_model_title": "m"})

    def test_suffix_shape_is_checked(self):
        with self.assertRaises(ValueError):
            llm_failures.skip_clause_sql("r", param_suffix="; DROP")


class TokenEstimateTests(unittest.TestCase):
    def test_fixed_values(self):
        self.assertEqual(bt.estimate_text_tokens(""), 0)
        self.assertEqual(bt.estimate_text_tokens("台積電"), 2)  # 3 × 0.8
        self.assertEqual(bt.estimate_text_tokens("hello world"), 3)  # 10 ÷ 3.5，空白不計
        self.assertEqual(bt.estimate_text_tokens("台 積 電"), 2)  # CJK 字間空白不計
        self.assertEqual(bt.estimate_text_tokens("台積電 2330 EPS 上修" * 10), 60)  # 50 字 × 0.8 ＋ 70 ÷ 3.5

    def test_input_tokens_use_each_tasks_prefix(self):
        head = ("研" * 5000 + "a" * 5000) * 3  # 30000 字
        got = bt.input_tokens_by_task(head)
        for task, spec in bt.TASK_SPECS.items():
            with self.subTest(task=task):
                self.assertEqual(got[task], spec.prompt_tokens + bt.estimate_text_tokens(head[:spec.excerpt]))

    def test_short_text_counts_only_itself(self):
        got = bt.input_tokens_by_task("研報", ["summary", "title"])
        self.assertEqual(got, {"summary": bt.TASK_SPECS["summary"].prompt_tokens + 2,
                               "title": bt.TASK_SPECS["title"].prompt_tokens + 2})

    def test_prompt_overhead_tracks_the_real_prompts(self):
        """`prompt_tokens` 與各批次真正的 prompt（內文留空）估得的 token 相差不到 25%：prompt 改長了要跟著改。"""
        from app.services import signal_extract
        from scripts import extract_takeaways, generate_summaries, generate_titles, tag_all_cli

        prompts = {
            "tag": tag_all_cli.build_prompt("x.pdf", "", 10000),
            "summary": generate_summaries.build_prompt("x.pdf", "", 12000),
            "title": generate_titles.build_prompt("x.pdf", "", 3000),
            "takeaway": extract_takeaways.build_takeaway_prompt("x.pdf", "2026-01-01", "gs", ""),
            "signal": signal_extract.build_signal_prompt("x.pdf", "2026-01-01", "gs", ["2330"], ""),
        }
        for task, prompt in prompts.items():
            with self.subTest(task=task):
                real = bt.estimate_text_tokens(prompt)
                self.assertLess(abs(bt.TASK_SPECS[task].prompt_tokens - real), 0.25 * real, (task, real))

    def test_excerpts_match_the_batch_defaults(self):
        """`excerpt` 與各批次 `--excerpt` 的預設相同（標註：行內標註 `_tag_via_cli` 的預設）。"""
        def argparse_default(script: str) -> int:
            tree = ast.parse((REPO_ROOT / "scripts" / script).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant)
                        and node.args[0].value == "--excerpt"):
                    return next(k.value.value for k in node.keywords if k.arg == "default")
            raise AssertionError(script)

        for task, script in (("summary", "generate_summaries.py"), ("title", "generate_titles.py"),
                             ("takeaway", "extract_takeaways.py"), ("signal", "extract_signals.py")):
            with self.subTest(task=task):
                self.assertEqual(bt.TASK_SPECS[task].excerpt, argparse_default(script))
        import inspect

        from scripts import _ingest_core

        self.assertEqual(bt.TASK_SPECS["tag"].excerpt,
                         inspect.signature(_ingest_core._tag_via_cli).parameters["excerpt"].default)


def _usage(task, fh, hit, miss, out, ms=1000, kind=None):
    return {"task": task, "file_hash": fh, "tokens": {"hit": hit, "miss": miss, "completion": out, "reasoning": 0},
            "kind": kind, "total_ms": ms}


class CalibrationTests(unittest.TestCase):
    def test_factor_from_matched_reports_only(self):
        rows = [_usage("summary", f"h{i}", 100, 1100, 200, ms=4000) for i in range(20)]
        rows += [_usage("summary", "other", 0, 9999, 400)]  # 不在語料裡：只算進輸出平均
        estimates = {("summary", f"h{i}"): 1000 for i in range(20)}
        cal = bt.calibration_from_usage(rows, estimates)
        self.assertAlmostEqual(cal.factor("summary"), 1.2)
        self.assertAlmostEqual(cal.out_tokens("summary"), (200 * 20 + 400) / 21)
        self.assertAlmostEqual(cal.call_seconds("summary"), (4.0 * 20 + 1.0) / 21)
        self.assertEqual(cal.samples["summary"], 20)

    def test_too_few_samples_keep_defaults(self):
        rows = [_usage("title", f"h{i}", 0, 500, 50) for i in range(19)]
        cal = bt.calibration_from_usage(rows, {("title", f"h{i}"): 1000 for i in range(19)})
        self.assertEqual(cal.factor("title"), 1.0)
        self.assertEqual(cal.out_tokens("title"), bt.TASK_SPECS["title"].output_tokens)
        self.assertEqual(cal.call_seconds("title"), bt.TASK_SPECS["title"].seconds)

    def test_failures_and_rows_without_usage_are_ignored(self):
        rows = [_usage("signal", "h", 0, 100, 10, kind="timeout"),
                {"task": "signal", "file_hash": "h", "tokens": None, "kind": None},
                {"task": "brief", "tokens": {"miss": 5}}]
        cal = bt.calibration_from_usage(rows, {("signal", "h"): 50}, min_samples=1)
        self.assertEqual(cal.input_factor, {})

    def test_last_row_per_report_wins(self):
        rows = [_usage("tag", "h", 0, 5000, 10), _usage("tag", "h", 0, 1000, 10)]
        cal = bt.calibration_from_usage(rows, {("tag", "h"): 500}, min_samples=1)
        self.assertAlmostEqual(cal.factor("tag"), 2.0)

    def test_iter_usage_rows_skips_bad_lines(self):
        lines = ['{"task": "tag"}', "not json", "", "[1]", '{"task": "title"}']
        self.assertEqual([r["task"] for r in bt.iter_usage_rows(lines)], ["tag", "title"])

    def test_measured_tokens_counts_retries_of_this_chunk_only(self):
        rows = [_usage("summary", "a", 10, 90, 5), _usage("summary", "a", 0, 100, 0),
                _usage("title", "b", 0, 30, 3), _usage("summary", "zzz", 0, 999, 9), _usage("tag", "a", 0, 50, 1),
                {"task": "summary", "file_hash": "a", "tokens": None}]
        self.assertEqual(bt.measured_tokens(rows, {"a", "b"}), (105 + 100 + 33, 3))


class BudgetStateTests(unittest.TestCase):
    def test_new_day_resets(self):
        s = bt.BudgetState(day="2026-11-09", tokens=500, reports=3, measured=False)
        fresh = s.for_day(date(2026, 11, 10))
        self.assertEqual((fresh.day, fresh.tokens, fresh.reports, fresh.measured), ("2026-11-10", 0, 0, True))
        self.assertIs(s.for_day(date(2026, 11, 9)), s)
        self.assertEqual(bt.BudgetState(tokens=900).remaining(1000), 100)
        self.assertEqual(bt.BudgetState(tokens=1200).remaining(1000), 0)

    def test_roundtrip_and_bad_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            self.assertEqual(bt.load_state(path), bt.BudgetState())
            bt.save_state(path, bt.BudgetState(day="2026-11-10", tokens=42, reports=2, measured=False))
            self.assertEqual(bt.load_state(path), bt.BudgetState("2026-11-10", 42, 2, False))
            self.assertEqual(json.loads(path.read_text())["tokens"], 42)
            for bad in ("{", "[]", '{"tokens": "x"}'):
                path.write_text(bad)
                self.assertEqual(bt.load_state(path), bt.BudgetState())

    def test_missing_directory_is_not_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nope" / "state.json"
            bt.save_state(path, bt.BudgetState(day="d", tokens=1))
            self.assertFalse(path.parent.exists())

    def test_conftest_points_the_work_dir_away_from_the_repo(self):
        self.assertTrue(str(bt.work_dir()).startswith("/nonexistent/"))
        self.assertEqual(bt.state_path().parent, bt.work_dir())


def _cand(h, tokens, tasks=("summary",)):
    return bt.Candidate(file_hash=h, report_date=None, tasks=tuple(tasks), tokens=tokens)


class PlanRoundTests(unittest.TestCase):
    def test_stops_at_token_budget_in_order(self):
        plan = bt.plan_round([_cand("a", 40), _cand("b", 40), _cand("c", 10)], token_budget=85, max_reports=None)
        self.assertEqual([c.file_hash for c in plan.selected], ["a", "b"])  # 不跳著挑小的 c
        self.assertEqual((plan.tokens, plan.stopped_by), (80, "tokens"))

    def test_stops_at_report_cap(self):
        plan = bt.plan_round([_cand("a", 1), _cand("b", 1), _cand("c", 1)], token_budget=None, max_reports=2)
        self.assertEqual(([c.file_hash for c in plan.selected], plan.stopped_by), (["a", "b"], "reports"))

    def test_everything_fits(self):
        plan = bt.plan_round([_cand("a", 1)], token_budget=10, max_reports=5)
        self.assertEqual((len(plan.selected), plan.stopped_by), (1, None))

    def test_oversized_report_is_skipped_not_blocking(self):
        plan = bt.plan_round([_cand("huge", 5000), _cand("b", 10)], token_budget=1000, max_reports=None,
                             full_budget=1000)
        self.assertEqual(([c.file_hash for c in plan.selected], plan.oversized), (["b"], ("huge",)))

    def test_candidate_tokens_cover_pending_tasks_only(self):
        head = "研" * 100
        cand = bt.candidate_from_row("h", None, {"summary": True, "title": False, "takeaway": True, "signal": False},
                                     head)
        ins = bt.input_tokens_by_task(head, ["summary", "takeaway"])
        want = sum(ins[t] + bt.TASK_SPECS[t].output_tokens for t in ("summary", "takeaway"))
        self.assertEqual((cand.tasks, cand.tokens), (("summary", "takeaway"), want))

    def test_calibration_scales_candidate_tokens(self):
        cal = bt.Calibration(input_factor={"title": 2.0}, output_tokens={"title": 10})
        cand = bt.candidate_from_row("h", None, {"title": True}, "", cal)
        self.assertEqual(cand.tokens, bt.TASK_SPECS["title"].prompt_tokens * 2 + 10)


if __name__ == "__main__":
    unittest.main()
