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
from unittest import mock

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


GAP_START = SWITCH - timedelta(days=2)  # CLI 失效
GAP_WINDOW = obs.Window(switch_at=SWITCH, before_start=SWITCH - timedelta(days=30), until=UNTIL,
                        grace=timedelta(hours=6), claude_until=GAP_START)
IN_GAP = GAP_START + timedelta(hours=5)


class IncidentGapTests(unittest.TestCase):
    """M2：CLI 失效到切換之間的事故空窗，兩群都不收。"""

    def test_gap_is_excluded_from_both_groups(self):
        self.assertEqual(GAP_WINDOW.claude_end, GAP_START)
        self.assertEqual(GAP_WINDOW.gap, (GAP_START, SWITCH))
        self.assertIsNone(obs.attribute(model_key=None, ts=IN_GAP, window=GAP_WINDOW))
        self.assertIsNone(obs.attribute(model_key="claude-sonnet-4-5", ts=IN_GAP, window=GAP_WINDOW))
        self.assertIsNone(GAP_WINDOW.cohort(IN_GAP))
        self.assertEqual(obs.attribute(model_key=None, ts=GAP_START - timedelta(hours=1), window=GAP_WINDOW),
                         obs.CLAUDE)
        self.assertEqual(obs.attribute(model_key=None, ts=AFTER, window=GAP_WINDOW), obs.DEEPSEEK)
        # 空窗裡的 DeepSeek 明確紀錄照算 DeepSeek（模型紀錄優先於時間）
        self.assertEqual(obs.attribute(model_key="deepseek-flash", ts=IN_GAP, window=GAP_WINDOW), obs.DEEPSEEK)

    def test_claude_until_after_switch_is_clamped(self):
        w = obs.Window(switch_at=SWITCH, before_start=SWITCH - timedelta(days=30), until=UNTIL,
                       claude_until=SWITCH + timedelta(days=1))
        self.assertEqual(w.claude_end, SWITCH)
        self.assertIsNone(w.gap)

    def test_gap_reports_not_counted_in_fill_rate(self):
        reports = [{"file_hash": "g1", "created_at": IN_GAP, "title": None},
                   {"file_hash": "c1", "created_at": BEFORE, "title": "台積電"}]
        out = obs.analyze_text_field(reports, GAP_WINDOW, {}, field_name="title", task=obs.TASK_TITLE)
        self.assertEqual((out["groups"][obs.CLAUDE]["filled"]["k"], out["groups"][obs.CLAUDE]["filled"]["n"]), (1, 1))
        self.assertEqual(out["groups"][obs.DEEPSEEK]["filled"]["n"], 0)

    def test_default_claude_until_is_cli_failure_time(self):
        self.assertEqual(obs.parse_switch_at(obs.DEFAULT_CLAUDE_UNTIL),
                         datetime(2026, 9, 23, 1, 5, tzinfo=UTC))


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

    def test_backfilled_report_excluded_even_when_sha_matches(self):
        # M1：回填經 reanchor_takeaways 把 sha 換成新正典文字的，sha 相符；靠 extraction_log.updated_at 排除
        rows = [_tk("c1", Q_EXACT, BEFORE), _tk("c2", Q_EXACT, BEFORE)]
        rows[0]["log_updated_at"] = BEFORE + timedelta(days=1)   # 擷取後被回填
        rows[1]["log_updated_at"] = BEFORE - timedelta(days=1)   # 入庫在擷取之前：正常
        out = self._run(rows)
        cl = out["groups"][obs.CLAUDE]
        self.assertEqual(cl["backfilled_reports"], 1)
        self.assertEqual(cl["sha_mismatch_reports"], 0)
        self.assertEqual(cl["anchor_any"]["n"], 1)
        self.assertEqual(cl["reports"], 2)

    def test_produced_late_fill_by_deepseek_counts_as_not_produced_for_claude(self):
        reports = [
            {"report_id": "c1", "created_at": BEFORE, "has_takeaway": True, "takeaway_at": BEFORE},
            # Claude 批次、切換後由 DeepSeek 補的摘錄（raw_payload.model）
            {"report_id": "c2", "created_at": BEFORE, "has_takeaway": True, "takeaway_at": AFTER,
             "takeaway_model": "deepseek-flash"},
            # 沒有 model 鍵但寫入時間在切換後：依時間也是 DeepSeek
            {"report_id": "c3", "created_at": BEFORE, "has_takeaway": True, "takeaway_at": AFTER},
        ]
        out = self._run([], reports=reports, texts={})
        cl = out["produced"][obs.CLAUDE]
        self.assertEqual((cl["k"], cl["n"], cl["late_fill"]), (1, 3, 2))

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
            {"report_id": "c1", "created_at": BEFORE, "has_takeaway": True, "takeaway_at": BEFORE},
            {"report_id": "c2", "created_at": BEFORE, "has_takeaway": False},
            {"report_id": "d1", "created_at": AFTER, "has_takeaway": True, "takeaway_at": AFTER,
             "takeaway_model": "deepseek-flash"},
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
        # c3 由 DeepSeek 後補：Claude 批次算未填（M2），另列後補
        self.assertEqual((cl["missing"]["k"], cl["missing"]["n"]), (2, 3))
        self.assertEqual((cl["filled"]["k"], cl["late_fill"]), (1, 1))
        self.assertEqual(ds["late_fill"], 0)
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


def _u(ts, task="title", kind=None, fh="h1", backend="http", tokens=None):
    return {"ts": ts.isoformat(), "task": task, "file_hash": fh, "kind": kind, "backend": backend,
            "model_req": "deepseek-flash", "model_resp": "deepseek-flash" if kind is None else None,
            "tokens": tokens}


def _write_jsonl(d, rows, extra=""):
    path = Path(d) / "llm_usage.jsonl"
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n" + extra, encoding="utf-8")
    return path


class UsageHealthTests(unittest.TestCase):
    """M3：用量紀錄零 LLM 算 content_filter、截斷、401／402。"""

    def test_read_rows_keeps_only_http_after_switch(self):
        rows = [_u(AFTER), _u(BEFORE), _u(AFTER, backend="cli"), _u(UNTIL + timedelta(hours=1))]
        with tempfile.TemporaryDirectory() as d:
            got, stats = obs.read_usage_rows(_write_jsonl(d, rows, "{壞行\n"), WINDOW)
        self.assertEqual(len(got), 1)
        self.assertEqual(stats["bad_lines"], 1)
        self.assertTrue(stats["exists"])
        got, stats = obs.read_usage_rows(Path("/nonexistent/x.jsonl"), WINDOW)
        self.assertEqual((got, stats["exists"]), ([], False))

    def _health(self, rows, skip_rows=()):
        with tempfile.TemporaryDirectory() as d:
            got, stats = obs.read_usage_rows(_write_jsonl(d, rows), WINDOW)
        return obs.analyze_usage_health(got, stats, None if skip_rows is None else list(skip_rows))

    def test_content_filter_denominator_excludes_non_content_kinds(self):
        rows = [_u(AFTER, task="tag", fh=f"t{i}") for i in range(8)]
        rows += [_u(AFTER, task="tag", kind="content_filter", fh="cf1"),
                 _u(AFTER, task="tag", kind="content_filter", fh="cf1"),
                 _u(AFTER, task="tag", kind="timeout", fh="x"),
                 _u(AFTER, task="tag", kind="overloaded", fh="x"),
                 _u(AFTER, task="tag", kind="truncated", fh="tr")]
        h = self._health(rows)
        cf = h["tasks"]["tag"]["content_filter"]
        self.assertEqual((cf["k"], cf["n"], cf["reports"]), (2, 11, 1))
        self.assertEqual(cf["judgement"]["end"], "比例 CI 上界")
        self.assertEqual(h["tasks"]["tag"]["calls"], 13)

    def test_judge_rate_cap_uses_wilson_upper(self):
        self.assertEqual(obs.judge_rate_cap(0, 400, 0.01)["verdict"], obs.VERDICT_PASS)
        self.assertEqual(obs.judge_rate_cap(0, 10, 0.01)["verdict"], obs.VERDICT_UNSURE)  # 小樣本未定
        self.assertEqual(obs.judge_rate_cap(50, 100, 0.01)["verdict"], obs.VERDICT_FAIL)
        self.assertEqual(obs.judge_rate_cap(0, 0, 0.01)["verdict"], obs.VERDICT_NO_DATA)

    def test_truncated_vs_timeout_streamed_and_account_errors(self):
        rows = [_u(AFTER, kind="truncated", fh="a"), _u(AFTER, kind="timeout_streamed", fh="b"),
                _u(AFTER, kind="timeout_streamed", fh="c"), _u(AFTER, kind="auth", fh="d"),
                _u(AFTER, kind="quota", fh="e"), _u(AFTER, kind="quota", fh="f")]
        h = self._health(rows)
        self.assertEqual((h["truncated"], h["timeout_streamed"], h["auth_401"], h["quota_402"]), (1, 2, 1, 2))
        self.assertEqual(obs.judge_zero(h["truncated"]), obs.VERDICT_FAIL)
        self.assertEqual(obs.judge_zero(0), obs.VERDICT_PASS)
        self.assertEqual(obs.judge_zero(None), obs.VERDICT_NO_DATA)

    def test_truncation_cross_checked_against_skip_list(self):
        t0 = AFTER
        rows = [_u(t0, kind="truncated", fh="rec"),
                _u(t0, kind="truncated", fh="fixed"), _u(t0 + timedelta(hours=3), fh="fixed"),
                _u(t0, kind="truncated", fh="lost"),
                _u(t0, task="brief", kind="truncated", fh=None)]
        skip = [{"task": "title", "file_hash": "rec", "reason": "truncated", "model": "deepseek-flash",
                 "fail_count": 1}]
        tc = self._health(rows, skip)["truncation_skip_check"]
        self.assertEqual((tc["recorded"], tc["later_success"], tc["no_file_hash"]), (1, 1, 1))
        self.assertEqual(tc["unrecorded"], [{"task": "title", "file_hash": "lost"}])
        tc = self._health(rows, None)["truncation_skip_check"]  # 跳過名單表不存在
        self.assertEqual(tc["unknown"], 3)

    def test_daily_tokens_by_taipei_date(self):
        tok = {"hit": 10, "miss": 5, "completion": 7, "reasoning": 0}
        late_utc = datetime(2026, 9, 26, 17, 0, tzinfo=UTC)  # 台北 9/27 01:00
        h = self._health([_u(late_utc, tokens=tok), _u(late_utc, tokens=tok), _u(AFTER, kind="network")])
        self.assertEqual(h["daily_tokens"]["2026-09-27"], {"calls": 2, "hit": 20, "miss": 10, "completion": 14,
                                                           "reasoning": 0})

    def test_missing_log_is_no_data(self):
        h = obs.analyze_usage_health([], {"exists": False}, [])
        self.assertFalse(h["available"])
        self.assertIsNone(h["truncated"])
        self.assertIsNone(h["auth_401"])


class BreakerTests(unittest.TestCase):
    def test_read_and_judge(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / ".llm_breaker"
            path.write_text(f"ts={AFTER.isoformat()}\nround=20260928_030000\nreason=5/10 逾時\n", encoding="utf-8")
            marker = obs.read_breaker(path)
            self.assertEqual((marker["ts"], marker["round"], marker["reason"]), (AFTER, "20260928_030000", "5/10 逾時"))
            self.assertEqual(obs.judge_breaker(marker, WINDOW), obs.VERDICT_FAIL)
            path.write_text(f"ts={BEFORE.isoformat()}\nreason=x\n", encoding="utf-8")
            self.assertEqual(obs.judge_breaker(obs.read_breaker(path), WINDOW), obs.VERDICT_PASS)
            path.write_text("", encoding="utf-8")
            self.assertTrue(obs.judge_breaker(obs.read_breaker(path), WINDOW).startswith("未定"))
        missing = obs.read_breaker(Path("/nonexistent/.llm_breaker"))
        self.assertFalse(missing["exists"])
        self.assertEqual(obs.judge_breaker(missing, WINDOW), obs.VERDICT_PASS)


class RepoRootsTests(unittest.TestCase):
    """L6：worktree 的 `.git` 檔指回主 checkout；兩者底下都不收 --out。"""

    def test_worktree_git_file_points_to_main_checkout(self):
        with tempfile.TemporaryDirectory() as d:
            main = Path(d) / "main"
            wt = main / ".claude" / "worktrees" / "w1"
            (main / ".git" / "worktrees" / "w1").mkdir(parents=True)
            wt.mkdir(parents=True)
            (wt / ".git").write_text(f"gitdir: {main / '.git' / 'worktrees' / 'w1'}\n", encoding="utf-8")
            self.assertEqual(obs.repo_roots(wt), [wt.resolve(), main.resolve()])
            self.assertEqual(obs.repo_roots(main), [main.resolve()])  # 主 checkout 的 .git 是目錄
            with mock.patch.object(obs, "repo_roots", return_value=[wt.resolve(), main.resolve()]):
                self.assertFalse(obs._out_allowed(main / "data" / "x.md"))
                self.assertFalse(obs._out_allowed(main / "README.md"))
                self.assertFalse(obs._out_allowed(wt / "eval" / "x.md"))
                self.assertTrue(obs._out_allowed(Path(d) / "elsewhere.md"))

    def test_deploy_hint_only_for_missing_default_in_worktree(self):
        main = Path("/srv/main")
        with mock.patch.object(obs, "repo_roots", return_value=[obs.ROOT, main]):
            hint = obs._deploy_hint(obs.DEFAULT_USAGE_LOG, obs.DEFAULT_USAGE_LOG)
            if not obs.DEFAULT_USAGE_LOG.exists():
                self.assertEqual(hint, str(main / "data" / "llm_usage.jsonl"))
            self.assertIsNone(obs._deploy_hint(obs.DEFAULT_USAGE_LOG, Path("/other/llm_usage.jsonl")))
        with mock.patch.object(obs, "repo_roots", return_value=[obs.ROOT]):
            self.assertIsNone(obs._deploy_hint(obs.DEFAULT_USAGE_LOG, obs.DEFAULT_USAGE_LOG))


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
                          "summary": "摘要", "has_takeaway": True, "takeaway_at": BEFORE}],
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
        rc, _, err = self._main("--switch-at", "2026-09-25T10:00", "--until", "2026-09-20T10:00",
                                session=_FakeSession(_canned()))
        self.assertEqual(rc, obs.RC_CONFIG)
        self.assertIn("--until", err)

    def test_dry_run_skips_until_check(self):
        # L3：切換時點在未來（until 預設現在）也能先看查詢
        rc, out, _ = self._main("--switch-at", "2099-01-01T10:00", "--usage-log", "/nonexistent/x", "--dry-run")
        self.assertEqual(rc, obs.RC_OK)
        self.assertIn("SET TRANSACTION READ ONLY;", out)

    def test_rejects_out_under_repo_data(self):
        rc, _, err = self._main("--switch-at", "2026-09-25T10:00", "--until", "2026-10-02T10:00",
                                "--out", str(REPO_ROOT / "data" / "observe.md"), "--dry-run")
        self.assertEqual(rc, obs.RC_CONFIG)
        self.assertIn("repo 外", err)

    def test_rejects_out_onto_tracked_file(self):
        # L6：不只 data/，repo 內任何路徑（例如已追蹤的 README）都拒收
        for target in (REPO_ROOT / "README.md", REPO_ROOT / "eval" / "new-report.md"):
            rc, _, _ = self._main("--switch-at", "2026-09-25T10:00", "--until", "2026-10-02T10:00",
                                  "--out", str(target), "--dry-run")
            self.assertEqual(rc, obs.RC_CONFIG, target)

    def test_markdown_and_json_end_to_end(self):
        args = ("--switch-at", SWITCH.isoformat(), "--until", UNTIL.isoformat(), "--usage-log", "/nonexistent/x",
                "--tags-dir", "/nonexistent/tags", "--bootstrap", "50")
        rc, md, _ = self._main(*args, session=_FakeSession(_canned()))
        self.assertEqual(rc, obs.RC_OK)
        self.assertIn("## 判讀總表", md)
        self.assertIn("摘錄：任一方式錨定成功率（主，D-A）", md)
        self.assertIn("差值 CI 下界", md)
        self.assertIn("只輸出判讀，不做任何切換", md)
        self.assertIn("判讀總表全部通過不等於批次 A 觀測完成", md.split("## 判讀總表")[0])
        self.assertIn("未涵蓋：幻覺率", md)
        self.assertIn("## 用量紀錄判準", md)
        self.assertIn("事故空窗", md)  # 預設 --claude-until 早於 SWITCH
        rc, js, _ = self._main(*args, "--json", session=_FakeSession(_canned()))
        rep = json.loads(js)
        self.assertEqual(rep["verdicts"][0]["end"], "差值 CI 下界")
        self.assertEqual(rep["takeaway"]["groups"]["deepseek"]["anchor_any"]["k"], 1)
        self.assertTrue(any("用量紀錄" in x for x in rep["limitations"]))
        self.assertTrue(any(x.startswith("幻覺率") for x in rep["uncovered"]))
        self.assertEqual(rep["window"]["claude_end"], "2026-09-23T09:05:00+08:00")

    def test_usage_verdicts_end_to_end(self):
        with tempfile.TemporaryDirectory() as d:
            log = _write_jsonl(d, [_u(AFTER, task="tag", fh="a"), _u(AFTER, task="tag", kind="content_filter",
                                                                    fh="b"), _u(AFTER, kind="quota", fh="c")])
            breaker = Path(d) / ".llm_breaker"
            breaker.write_text(f"ts={AFTER.isoformat()}\nreason=過載\n", encoding="utf-8")
            rc, js, _ = self._main("--switch-at", SWITCH.isoformat(), "--until", UNTIL.isoformat(),
                                   "--usage-log", str(log), "--breaker-file", str(breaker), "--json",
                                   "--bootstrap", "20", session=_FakeSession(_canned()))
        self.assertEqual(rc, obs.RC_OK)
        rep = json.loads(js)
        by = {v["metric"]: v for v in rep["usage_verdicts"]}
        self.assertEqual((by["content_filter 比例：tag"]["value"]["k"], by["content_filter 比例：tag"]["value"]["n"]),
                         (1, 2))
        self.assertEqual(by["402（quota）"]["verdict"], obs.VERDICT_FAIL)
        self.assertEqual(by["401（auth）"]["verdict"], obs.VERDICT_PASS)
        self.assertEqual(by["截斷（truncated，finish_reason=length）"]["verdict"], obs.VERDICT_PASS)
        self.assertEqual(by["斷路器標記"]["verdict"], obs.VERDICT_FAIL)

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
