"""eval/observe_switch.py：DeepSeek 切換後批次產出觀測（遷移 PR-16）。

不連 DB、不讀生產檔案：DB 用假 session（記下收到的 SQL），用量紀錄與 tags 快取用 tempfile 或假函式。
釘住的是分群規則、指標算法、CI 取端與三態判讀、唯讀（`SET TRANSACTION READ ONLY` 是交易第一句、
不發任何寫入語句、最後 rollback）與零 LLM（AST：不 import 呼叫層、不被 `_llm_env` 入口掃描當成 LLM 入口）。
"""
from __future__ import annotations

import ast
import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services.textnorm import clean_extracted  # noqa: E402
from eval import observe_switch as obs  # noqa: E402

SCRIPT = REPO_ROOT / "eval" / "observe_switch.py"
UTC = timezone.utc
SWITCH = datetime(2026, 9, 25, 2, 0, tzinfo=UTC)
UNTIL = SWITCH + timedelta(days=14)
WINDOW = obs.Window(switch_at=SWITCH, before_start=SWITCH - timedelta(days=30), until=UNTIL,
                    grace=timedelta(hours=6))
BEFORE = SWITCH - timedelta(days=3)
AFTER = SWITCH + timedelta(days=3)

FULL_TEXT = ("前言。\n台積電第三季營收成長百分之二十，優於市場預期。\n"
             "聯發科天璣晶片出貨量本季創新高主要受惠於中國手機品牌備貨需求強勁。\n結語重複句子。")
CANON = clean_extracted(FULL_TEXT)
SHA = obs._sha256(CANON)
Q_EXACT = "台積電第三季營收成長百分之二十，優於市場預期。"
Q_NORMALIZED = "台積電 第三季營收成長百分之二十， 優於市場預期"
Q_PREFIX = "聯發科天璣晶片出貨量本季創新高主要受惠於歐美市場需求"
Q_MISS = "完全不存在於原文的一段引文內容測試用句子"


def _tk(report_id, quote, created_at, model=None, sha=SHA, status="valid"):
    return {"report_id": report_id, "file_hash": f"h-{report_id}", "quote": quote, "extraction_status": status,
            "text_sha256": sha, "created_at": created_at, "model": model}


# ── 分群 ──────────────────────────────────────────────────────────────────────


class AttributionTests(unittest.TestCase):
    def test_model_group(self):
        self.assertEqual(obs.model_group("deepseek-flash"), obs.DEEPSEEK)
        self.assertEqual(obs.model_group("deepseek-v4-pro"), obs.DEEPSEEK)
        self.assertEqual(obs.model_group("claude-sonnet-4-5"), obs.CLAUDE)
        self.assertEqual(obs.model_group(None), obs.CLAUDE)

    def test_missing_key_falls_back_to_time(self):
        self.assertEqual(obs.attribute(model_key=None, ts=BEFORE, window=WINDOW), obs.CLAUDE)
        self.assertEqual(obs.attribute(model_key=None, ts=AFTER, window=WINDOW), obs.DEEPSEEK)

    def test_out_of_window_is_none(self):
        self.assertIsNone(obs.attribute(model_key=None, ts=WINDOW.before_start - timedelta(seconds=1), window=WINDOW))
        self.assertIsNone(obs.attribute(model_key=None, ts=UNTIL, window=WINDOW))
        self.assertIsNone(obs.attribute(model_key=None, ts=None, window=WINDOW))

    def test_explicit_deepseek_key_wins_over_old_created_at(self):
        # 訊號 upsert 不更新 created_at：切換後重擷取的舊列帶 raw_payload.model
        old = WINDOW.before_start - timedelta(days=100)
        self.assertEqual(obs.attribute(model_key="deepseek-flash", ts=old, window=WINDOW), obs.DEEPSEEK)

    def test_explicit_claude_key_still_needs_window(self):
        self.assertEqual(obs.attribute(model_key="claude-sonnet-4-5", ts=BEFORE, window=WINDOW), obs.CLAUDE)
        old = WINDOW.before_start - timedelta(days=1)
        self.assertIsNone(obs.attribute(model_key="claude-sonnet-4-5", ts=old, window=WINDOW))

    def test_usage_after_switch_overrides_time(self):
        hit = obs.UsageHit(AFTER, "deepseek-flash")
        old = WINDOW.before_start - timedelta(days=50)
        self.assertEqual(obs.attribute(model_key=None, ts=BEFORE, window=WINDOW, usage=hit), obs.DEEPSEEK)
        self.assertEqual(obs.attribute(model_key=None, ts=old, window=WINDOW, usage=hit), obs.DEEPSEEK)

    def test_usage_before_switch_is_ignored(self):
        hit = obs.UsageHit(BEFORE, "deepseek-flash")
        self.assertEqual(obs.attribute(model_key=None, ts=BEFORE, window=WINDOW, usage=hit), obs.CLAUDE)

    def test_cohort_excludes_grace_hours(self):
        self.assertEqual(WINDOW.cohort(BEFORE), obs.CLAUDE)
        self.assertEqual(WINDOW.cohort(AFTER), obs.DEEPSEEK)
        self.assertIsNone(WINDOW.cohort(UNTIL - timedelta(hours=1)))


class UsageLogTests(unittest.TestCase):
    def test_keeps_latest_success_and_skips_failures(self):
        lines = [
            {"ts": "2026-09-25T03:00:00+00:00", "task": "title", "file_hash": "a", "kind": None,
             "model_req": "deepseek-flash", "model_resp": None},
            {"ts": "2026-09-26T03:00:00+00:00", "task": "title", "file_hash": "a", "kind": None,
             "model_req": "deepseek-flash", "model_resp": "deepseek-v4-pro"},
            {"ts": "2026-09-27T03:00:00+00:00", "task": "title", "file_hash": "a", "kind": "timeout",
             "model_req": "claude-x", "model_resp": None},
            {"ts": "2026-09-27T03:00:00+00:00", "task": "brief", "file_hash": None, "kind": None,
             "model_req": "deepseek-flash"},
        ]
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "llm_usage.jsonl"
            p.write_text("\n".join(json.dumps(x) for x in lines) + "\n{壞行\n", encoding="utf-8")
            index, stats = obs.load_usage(p, ["title"])
        self.assertEqual(index[("title", "a")].model, "deepseek-v4-pro")
        self.assertEqual(stats["bad_lines"], 1)
        self.assertEqual(stats["used"], 2)
        self.assertEqual(obs.usage_hashes(index, ["title"], SWITCH), ["a"])

    def test_missing_file_is_empty(self):
        index, stats = obs.load_usage(Path("/nonexistent/llm_usage.jsonl"), ["title"])
        self.assertEqual(index, {})
        self.assertFalse(stats["exists"])


# ── 統計與判讀 ────────────────────────────────────────────────────────────────


class StatsTests(unittest.TestCase):
    def test_wilson_known_values(self):
        lo, hi = obs.wilson(5, 10)
        self.assertAlmostEqual(lo, 0.2366, places=3)
        self.assertAlmostEqual(hi, 0.7634, places=3)
        self.assertAlmostEqual(obs.wilson(0, 10)[1], 0.2775, places=3)
        self.assertIsNone(obs.wilson(0, 0))

    def test_newcombe_matches_published_example(self):
        # Newcombe (1998) 例：56/70 − 48/80 → 0.2000 (0.0524, 0.3339)
        d, lo, hi = obs.newcombe(56, 70, 48, 80)
        self.assertAlmostEqual(d, 0.2, places=6)
        self.assertAlmostEqual(lo, 0.0524, places=3)
        self.assertAlmostEqual(hi, 0.3339, places=3)
        self.assertIsNone(obs.newcombe(1, 1, 0, 0))

    def test_cluster_bootstrap_is_deterministic_and_brackets_point(self):
        ds = [(5, 5), (4, 5), (5, 5), (3, 4)]
        cl = [(4, 5), (3, 5), (5, 5), (2, 4)]
        a = obs.cluster_bootstrap_diff(ds, cl, n_boot=500, seed=7)
        b = obs.cluster_bootstrap_diff(ds, cl, n_boot=500, seed=7)
        self.assertEqual(a, b)
        point, lo, hi = a
        self.assertAlmostEqual(point, 17 / 19 - 14 / 19)
        self.assertLessEqual(lo, point)
        self.assertGreaterEqual(hi, point)
        self.assertIsNone(obs.cluster_bootstrap_diff([], cl, n_boot=10, seed=1))


class JudgeTests(unittest.TestCase):
    def test_higher_uses_lower_bound(self):
        j = obs.judge((0.0, -0.03, 0.03), direction=obs.HIGHER, margin=0.05)
        self.assertEqual(j["verdict"], obs.VERDICT_PASS)
        self.assertEqual(j["end"], "差值 CI 下界")
        self.assertEqual(j["end_value"], -0.03)

    def test_higher_unsure_when_ci_crosses_margin(self):
        j = obs.judge((-0.04, -0.08, 0.0), direction=obs.HIGHER, margin=0.05)
        self.assertEqual(j["verdict"], obs.VERDICT_UNSURE)
        self.assertTrue(j["point_within"])

    def test_higher_fail_when_whole_ci_outside(self):
        j = obs.judge((-0.2, -0.3, -0.1), direction=obs.HIGHER, margin=0.05)
        self.assertEqual(j["verdict"], obs.VERDICT_FAIL)
        self.assertFalse(j["point_within"])

    def test_lower_uses_upper_bound(self):
        self.assertEqual(obs.judge((-0.1, -0.2, -0.01), direction=obs.LOWER, margin=0.0)["verdict"], obs.VERDICT_PASS)
        j = obs.judge((0.01, -0.02, 0.04), direction=obs.LOWER, margin=0.0)
        self.assertEqual(j["verdict"], obs.VERDICT_UNSURE)
        self.assertEqual(j["end"], "差值 CI 上界")
        self.assertEqual(j["end_value"], 0.04)
        self.assertEqual(obs.judge((0.1, 0.05, 0.2), direction=obs.LOWER, margin=0.0)["verdict"], obs.VERDICT_FAIL)

    def test_no_data(self):
        j = obs.judge(None, direction=obs.HIGHER, margin=0.02)
        self.assertEqual(j["verdict"], obs.VERDICT_NO_DATA)
        self.assertIsNone(j["end_value"])


# ── 指標 ──────────────────────────────────────────────────────────────────────


class TakeawayTests(unittest.TestCase):
    def _run(self, rows, reports=(), texts=None):
        texts = texts if texts is not None else {r["report_id"]: FULL_TEXT for r in rows}
        return obs.analyze_takeaways(rows, texts, list(reports), WINDOW, n_boot=200, seed=1)

    def test_any_anchor_counts_exact_normalized_prefix(self):
        rows = [
            _tk("d1", Q_EXACT, AFTER, model="deepseek-flash"),
            _tk("d1", Q_NORMALIZED, AFTER, model="deepseek-flash"),
            _tk("d1", Q_PREFIX, AFTER, model="deepseek-flash"),
            _tk("d1", Q_MISS, AFTER, model="deepseek-flash"),
            _tk("d1", None, AFTER, model="deepseek-flash"),  # 沒 quote：不進分母
            _tk("c1", Q_EXACT, BEFORE),                       # 沒有 model 鍵、切換前 → Claude
            _tk("c1", Q_MISS, BEFORE),
        ]
        out = self._run(rows)
        ds, cl = out["groups"][obs.DEEPSEEK], out["groups"][obs.CLAUDE]
        self.assertEqual((ds["anchor_any"]["k"], ds["anchor_any"]["n"]), (3, 4))
        self.assertEqual((ds["anchor_exact"]["k"], ds["anchor_exact"]["n"]), (1, 4))
        self.assertEqual((cl["anchor_any"]["k"], cl["anchor_any"]["n"]), (1, 2))
        self.assertEqual(ds["items_per_report"], 5)
        self.assertAlmostEqual(out["anchor_any_diff"]["point"], 0.75 - 0.5)
        self.assertEqual(out["anchor_any_judgement"]["end"], "差值 CI 下界")
        self.assertEqual(out["anchor_any_judgement"]["margin"], obs.TOL_ANCHOR)

    def test_anchoring_is_recomputed_not_read_from_stored_method(self):
        row = _tk("d1", Q_MISS, AFTER, model="deepseek-flash")
        row["anchor_method"] = "exact"  # 存的欄位說錨上了也不算：一律重算
        out = self._run([row])
        self.assertEqual(out["groups"][obs.DEEPSEEK]["anchor_any"]["k"], 0)

    def test_sha_mismatch_report_is_excluded_from_anchor_rate(self):
        rows = [_tk("d1", Q_EXACT, AFTER, model="deepseek-flash", sha="old"),
                _tk("d2", Q_EXACT, AFTER, model="deepseek-flash")]
        out = self._run(rows)
        ds = out["groups"][obs.DEEPSEEK]
        self.assertEqual(ds["sha_mismatch_reports"], 1)
        self.assertEqual(ds["anchor_any"]["n"], 1)
        self.assertEqual(ds["reports"], 2)

    def test_da_verdict_fails_on_big_drop(self):
        rows = []
        for i in range(30):
            rows.append(_tk(f"c{i}", Q_EXACT, BEFORE))
            rows.append(_tk(f"d{i}", Q_MISS, AFTER, model="deepseek-flash"))
        out = self._run(rows)
        self.assertEqual(out["anchor_any_judgement"]["verdict"], obs.VERDICT_FAIL)

    def test_da_verdict_passes_when_equal_and_large(self):
        rows = []
        for i in range(60):
            rows.append(_tk(f"c{i}", Q_EXACT, BEFORE))
            rows.append(_tk(f"d{i}", Q_NORMALIZED, AFTER, model="deepseek-flash"))
        out = self._run(rows)
        self.assertEqual(out["anchor_any_judgement"]["verdict"], obs.VERDICT_PASS)
        # exact 只是觀測值：DeepSeek 全是 normalized、exact 0%，也不影響主判讀
        self.assertEqual(out["groups"][obs.DEEPSEEK]["anchor_exact"]["rate"], 0.0)

    def test_produced_rate_by_ingest_cohort(self):
        reports = [
            {"report_id": "c1", "created_at": BEFORE, "has_takeaway": True},
            {"report_id": "c2", "created_at": BEFORE, "has_takeaway": False},
            {"report_id": "d1", "created_at": AFTER, "has_takeaway": True},
            {"report_id": "d2", "created_at": UNTIL - timedelta(hours=1), "has_takeaway": False},  # grace 內
        ]
        out = self._run([], reports=reports, texts={})
        self.assertEqual((out["produced"][obs.CLAUDE]["k"], out["produced"][obs.CLAUDE]["n"]), (1, 2))
        self.assertEqual((out["produced"][obs.DEEPSEEK]["k"], out["produced"][obs.DEEPSEEK]["n"]), (1, 1))
        self.assertEqual(out["produced_judgement"]["margin"], obs.TOL_PARSE)


class SignalTests(unittest.TestCase):
    def test_status_and_stance(self):
        thesis = {"outlook": {"stance": "positive"}, "catalyst": {"stance": "neutral"},
                  "risk": {"stance": "rising"}, "valuation": {"stance": "bogus"}}
        rows = [
            {"file_hash": "a", "extraction_status": "valid", "thesis_dimensions": thesis,
             "created_at": BEFORE, "model": None},
            {"file_hash": "b", "extraction_status": "rejected", "thesis_dimensions": {},
             "created_at": BEFORE, "model": None},
            {"file_hash": "c", "extraction_status": "partial", "thesis_dimensions": json.dumps(thesis),
             "created_at": WINDOW.before_start - timedelta(days=90), "model": "deepseek-flash"},
            # 沒有 model 鍵的舊列，但切換後有 DeepSeek 成功呼叫（upsert 重寫過）
            {"file_hash": "d", "extraction_status": "valid", "thesis_dimensions": {},
             "created_at": BEFORE, "model": None},
        ]
        usage = {(obs.TASK_SIGNAL, "d"): obs.UsageHit(AFTER, "deepseek-flash")}
        out = obs.analyze_signals(rows, WINDOW, usage)
        cl, ds = out["groups"][obs.CLAUDE], out["groups"][obs.DEEPSEEK]
        self.assertEqual((cl["valid"]["k"], cl["rejected"]["k"], cl["rows"]), (1, 1, 2))
        self.assertEqual((ds["valid"]["k"], ds["partial"]["k"], ds["rows"]), (1, 1, 2))
        self.assertEqual(cl["stance"]["outlook"], {"positive": 1})
        self.assertEqual(cl["stance"]["valuation"], {"（缺或不在詞彙內）": 1})
        self.assertEqual(ds["stance"]["risk"], {"rising": 1, "（缺或不在詞彙內）": 1})
        self.assertAlmostEqual(out["ok_diff"]["point"], 1.0 - 0.5)
        self.assertEqual(out["ok_judgement"]["end"], "差值 CI 下界")


class TextFieldTests(unittest.TestCase):
    def test_missing_rate_simplified_and_length(self):
        reports = [
            {"file_hash": "c1", "created_at": BEFORE, "title": "台積電營收成長"},
            {"file_hash": "c2", "created_at": BEFORE, "title": None},
            {"file_hash": "d1", "created_at": AFTER, "title": "台积电营收成长"},
            {"file_hash": "d2", "created_at": AFTER, "title": "聯發科展望"},
            {"file_hash": "d3", "created_at": UNTIL - timedelta(hours=1), "title": None},  # grace 內不算缺
            # 切換前入庫、切換後才由 DeepSeek 補標題（用量紀錄）：缺值算 Claude 批次，品質算 DeepSeek
            {"file_hash": "c3", "created_at": BEFORE, "title": "鴻海伺服器"},
        ]
        usage = {(obs.TASK_TITLE, "c3"): obs.UsageHit(AFTER, "deepseek-flash")}
        out = obs.analyze_text_field(reports, WINDOW, usage, field_name="title", task=obs.TASK_TITLE)
        cl, ds = out["groups"][obs.CLAUDE], out["groups"][obs.DEEPSEEK]
        self.assertEqual((cl["missing"]["k"], cl["missing"]["n"]), (1, 3))
        self.assertEqual((ds["missing"]["k"], ds["missing"]["n"]), (0, 2))
        self.assertEqual((ds["simplified"]["k"], ds["simplified"]["n"]), (1, 3))
        self.assertEqual((cl["simplified"]["k"], cl["simplified"]["n"]), (0, 1))
        self.assertEqual(ds["length"]["n"], 3)
        self.assertEqual(out["filled_judgement"]["margin"], obs.TOL_PARSE)


class TagTests(unittest.TestCase):
    def test_skip_and_market_none(self):
        rows = [
            {"file_hash": "c1", "stopped_at": "ingested", "at": BEFORE, "market": "TW", "is_research": True},
            {"file_hash": "c2", "stopped_at": "not_research", "at": BEFORE, "market": None, "is_research": None},
            {"file_hash": "d1", "stopped_at": "ingested", "at": AFTER, "market": "US", "is_research": True},
            {"file_hash": "d2", "stopped_at": "not_research", "at": AFTER, "market": None, "is_research": None},
            {"file_hash": "d3", "stopped_at": "not_research", "at": AFTER, "market": None, "is_research": None},
        ]
        tags = {"c2": (None, False), "d2": (None, True)}  # d3 讀不到快取
        out = obs.analyze_tags(rows, WINDOW, {}, tags.get)
        cl, ds = out["groups"][obs.CLAUDE], out["groups"][obs.DEEPSEEK]
        self.assertEqual((cl["skip_non_research"]["k"], cl["skip_non_research"]["n"]), (1, 2))
        self.assertEqual((ds["skip_non_research"]["k"], ds["skip_non_research"]["n"]), (2, 3))
        self.assertEqual((ds["market_none"]["k"], ds["market_none"]["n"]), (1, 2))
        self.assertEqual(ds["tag_unknown"], 1)
        self.assertEqual(cl["is_research"], {"true": 1, "false": 1})
        self.assertEqual(ds["market"], {"US": 1, "None": 1})
        self.assertEqual(out["skip_judgement"]["end"], "差值 CI 上界")
        self.assertEqual(out["skip_judgement"]["direction"], obs.LOWER)


class SkipListTests(unittest.TestCase):
    def test_counts_and_should_skip(self):
        rows = [
            {"task": "tag", "reason": "content_filter", "model": "deepseek-flash", "fail_count": 1},
            {"task": "title", "reason": "unparseable", "model": "deepseek-flash", "fail_count": 1},
            {"task": "title", "reason": "unparseable", "model": "deepseek-flash", "fail_count": 3},
        ]
        out = obs.analyze_skip_list(rows)
        by = {(i["task"], i["reason"]): i for i in out["by_task_reason"]}
        self.assertEqual(by[("tag", "content_filter")]["skipping"], 1)
        self.assertEqual((by[("title", "unparseable")]["count"], by[("title", "unparseable")]["skipping"]), (2, 1))
        self.assertEqual(out["total"], 3)
        self.assertFalse(obs.analyze_skip_list(None)["table_ready"])


# ── 唯讀取數（假 session）──────────────────────────────────────────────────────


class _Result:
    def __init__(self, rows=None, scalar=None):
        self._rows, self._scalar = rows or [], scalar

    def mappings(self):
        return self

    def all(self):
        return list(self._rows)

    def scalar(self):
        return self._scalar


class _FakeSession:
    def __init__(self, canned, table_ready=True):
        self.canned = canned
        self.table_ready = table_ready
        self.statements: list[str] = []
        self.params: list[dict] = []
        self.rolled_back = False
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        self.statements.append(sql)
        self.params.append(params or {})
        if sql == obs.FAILURE_TABLE_READY_SQL:
            return _Result(scalar=self.table_ready)
        for const, rows in self.canned.items():
            if sql == const:
                return _Result(rows=rows)
        return _Result()

    async def rollback(self):
        self.rolled_back = True

    async def commit(self):  # pragma: no cover - 被呼叫就是 bug
        self.committed = True


def _canned():
    return {
        obs.TAKEAWAY_SQL: [_tk("d1", Q_EXACT, AFTER, model="deepseek-flash"), _tk("c1", Q_EXACT, BEFORE)],
        obs.FULL_TEXT_SQL: [{"report_id": "d1", "full_text": FULL_TEXT}, {"report_id": "c1", "full_text": FULL_TEXT}],
        obs.SIGNAL_SQL: [{"file_hash": "a", "extraction_status": "valid", "thesis_dimensions": {},
                          "created_at": BEFORE, "model": None}],
        obs.REPORT_SQL: [{"report_id": "c1", "file_hash": "h-c1", "created_at": BEFORE, "title": "標題",
                          "summary": "摘要", "has_takeaway": True}],
        obs.TAG_SQL: [{"file_hash": "h-c1", "stopped_at": "ingested", "at": BEFORE, "market": "TW",
                       "is_research": True}],
        obs.SKIP_LIST_SQL: [{"task": "tag", "reason": "content_filter", "model": "deepseek-flash",
                             "fail_count": 1, "file_hash": "x"}],
    }


class FetchTests(unittest.IsolatedAsyncioTestCase):
    async def test_read_only_first_then_relax_then_rollback(self):
        session = _FakeSession(_canned())
        data = await obs.fetch(WINDOW, {}, session_factory=lambda: session)
        self.assertEqual(session.statements[0], "SET TRANSACTION READ ONLY")
        self.assertTrue(session.statements[1].startswith("SET LOCAL statement_timeout"))
        self.assertTrue(session.rolled_back)
        self.assertFalse(session.committed)
        for sql in session.statements:
            head = sql.lstrip().split()[0].upper()
            self.assertIn(head, {"SET", "SELECT"}, sql)
        self.assertEqual(len(data.takeaways), 2)
        self.assertEqual(set(data.texts), {"d1", "c1"})
        self.assertEqual(len(data.skip_list), 1)
        full_text_params = session.params[session.statements.index(obs.FULL_TEXT_SQL)]
        self.assertEqual(full_text_params["ids"], ["c1", "d1"])

    async def test_skip_list_skipped_when_table_missing(self):
        session = _FakeSession(_canned(), table_ready=False)
        data = await obs.fetch(WINDOW, {}, session_factory=lambda: session)
        self.assertIsNone(data.skip_list)
        self.assertNotIn(obs.SKIP_LIST_SQL, session.statements)

    async def test_no_full_text_query_without_takeaways(self):
        canned = _canned()
        canned[obs.TAKEAWAY_SQL] = []
        session = _FakeSession(canned)
        await obs.fetch(WINDOW, {}, session_factory=lambda: session)
        self.assertNotIn(obs.FULL_TEXT_SQL, session.statements)

    def test_query_constants_are_select_only(self):
        for name, sql, _ in obs.query_plan(WINDOW, {}):
            head = sql.split()[0].upper()
            self.assertIn(head, {"SET", "SELECT"}, name)
            for word in ("INSERT", "UPDATE ", "DELETE", "TRUNCATE", "DROP", "ALTER"):
                self.assertNotIn(word, sql.upper(), name)


# ── CLI ──────────────────────────────────────────────────────────────────────


def _no_db():
    raise AssertionError("dry-run 不該開 DB session")


class MainTests(unittest.TestCase):
    def _main(self, *argv, session=None):
        out, err = io.StringIO(), io.StringIO()
        factory = (lambda: session) if session is not None else _no_db
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = obs.main(list(argv), session_factory=factory)
        return rc, out.getvalue(), err.getvalue()

    def test_dry_run_prints_queries_without_db(self):
        rc, out, _ = self._main("--switch-at", "2026-09-25T10:00", "--until", "2026-10-02T10:00",
                                "--usage-log", "/nonexistent/x.jsonl", "--dry-run")
        self.assertEqual(rc, obs.RC_OK)
        self.assertIn("SET TRANSACTION READ ONLY;", out)
        self.assertIn("research.report_takeaway", out)
        self.assertIn("2026-09-25T10:00:00+08:00", out)  # 沒帶時區視為台北時間

    def test_switch_at_required(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            obs.main([], session_factory=_no_db)

    def test_rejects_until_before_switch(self):
        rc, _, err = self._main("--switch-at", "2026-09-25T10:00", "--until", "2026-09-20T10:00", "--dry-run")
        self.assertEqual(rc, obs.RC_CONFIG)

    def test_rejects_out_under_repo_data(self):
        rc, _, err = self._main("--switch-at", "2026-09-25T10:00", "--until", "2026-10-02T10:00",
                                "--out", str(REPO_ROOT / "data" / "observe.md"), "--dry-run")
        self.assertEqual(rc, obs.RC_CONFIG)
        self.assertIn("data", err)

    def test_markdown_and_json_end_to_end(self):
        args = ("--switch-at", SWITCH.isoformat(), "--until", UNTIL.isoformat(), "--usage-log", "/nonexistent/x",
                "--tags-dir", "/nonexistent/tags", "--bootstrap", "50")
        rc, md, _ = self._main(*args, session=_FakeSession(_canned()))
        self.assertEqual(rc, obs.RC_OK)
        self.assertIn("## 判讀總表", md)
        self.assertIn("摘錄：任一方式錨定成功率（主，D-A）", md)
        self.assertIn("差值 CI 下界", md)
        self.assertIn("只輸出判讀，不做任何切換", md)
        rc, js, _ = self._main(*args, "--json", session=_FakeSession(_canned()))
        rep = json.loads(js)
        self.assertEqual(rep["verdicts"][0]["end"], "差值 CI 下界")
        self.assertEqual(rep["takeaway"]["groups"]["deepseek"]["anchor_any"]["k"], 1)
        self.assertTrue(any("用量紀錄" in x for x in rep["limitations"]))

    def test_out_file_elsewhere(self):
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "observe.json"
            rc, out, _ = self._main("--switch-at", SWITCH.isoformat(), "--until", UNTIL.isoformat(),
                                    "--usage-log", "/nonexistent/x", "--json", "--bootstrap", "20",
                                    "--out", str(target), session=_FakeSession(_canned()))
            self.assertEqual(rc, obs.RC_OK)
            self.assertEqual(out, "")
            self.assertIn("verdicts", json.loads(target.read_text(encoding="utf-8")))


# ── 零 LLM 守門 ──────────────────────────────────────────────────────────────

_FORBIDDEN_MODULES = {
    "app.services.llm", "app.services.llm_http", "scripts._claude_cli", "scripts._claude_lock", "eval.judge",
    "scripts._llm_env", "app.services.answer", "app.services.retrieval_pipeline", "app.services.faithfulness",
    "app.services.scope_router", "app.services.query_planner", "app.services.agentic_qa", "app.services.followups",
}
_FORBIDDEN_NAMES = {"run_claude", "stream_completion", "complete_json", "astream", "call_cli"}


def _load_env_loading_test_module():
    spec = importlib.util.spec_from_file_location("_llm_env_loading_for_observe",
                                                  REPO_ROOT / "tests" / "test_llm_env_loading.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class ZeroLlmTests(unittest.TestCase):
    def setUp(self):
        self.tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))

    def test_imports_no_llm_call_layer(self):
        modules, names = set(), set()
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                modules |= {a.name for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module)
                names |= {a.name for a in node.names}
                if node.module in {"app.services", "scripts", "eval"}:
                    modules |= {f"{node.module}.{a.name}" for a in node.names}
        self.assertFalse(modules & _FORBIDDEN_MODULES, modules & _FORBIDDEN_MODULES)
        self.assertFalse(names & _FORBIDDEN_NAMES, names & _FORBIDDEN_NAMES)
        llm_like = {m for m in modules if m.split(".")[-1].startswith("llm")}
        self.assertLessEqual(llm_like, {"app.services.llm_models", "app.services.llm_failures"})

    def test_not_an_llm_entry_for_env_loading_scan(self):
        mod = _load_env_loading_test_module()
        self.assertFalse(mod._imports_llm(self.tree))
        self.assertNotIn("eval/observe_switch.py", mod._scanned_files())
        self.assertNotIn("eval/observe_switch.py", mod.NON_LLM_ENTRIES)

    def test_import_does_not_pull_llm_modules(self):
        # 已 import 過 eval.observe_switch；它的傳遞相依不該把呼叫層帶進來
        # （別的測試可能已經 import 過，所以用子行程量乾淨的 sys.modules）
        import subprocess

        code = ("import sys; sys.path.insert(0, %r); import eval.observe_switch; "
                "bad = [m for m in ('app.services.llm', 'app.services.llm_http', 'scripts._claude_cli', "
                "'scripts._claude_lock', 'eval.judge') if m in sys.modules]; print(bad)") % str(REPO_ROOT)
        res = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(res.stdout.strip(), "[]")


if __name__ == "__main__":
    unittest.main()
