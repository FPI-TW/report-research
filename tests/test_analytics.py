"""使用分析（Admin v2 Analytics）不需要 DB 的部分：k 門檻、範圍規劃、彙總腳本與 unit 的守門。

SQL 對真 DB 的驗證在 tests/test_analytics_db.py，HTTP 層在 tests/test_admin_analytics_api.py。這裡驗：
- k=3 抑制的純函式（邊界 2／3 人、未知人數、排序不洩漏被隱藏的數值、跨段合併取人數最大值、開放與固定詞彙）。
- `plan_range`：預設 30 天、截到今天、顛倒與過長、即時／彙總兩段的切點。
- 忠實度欄位一律帶 `CURRENT_JUDGE_SQL`（只計現行 judge）；SQL 只在 count(DISTINCT) 裡碰 user_id、不讀問答文字。
- 彙總腳本：日子的規劃、參數錯誤回 1（不是 argparse 的 2＝DB 不可用）、不載入 torch／檢索／嵌入／LLM 模組；
  unit 的退出碼與排程錯開 sync／備份。
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from app.services import analytics
from app.services.judge_schema import CURRENT_JUDGE_SQL

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import analytics_rollup  # noqa: E402

TODAY = date(2026, 10, 7)


class SuppressionTests(unittest.TestCase):
    def test_boundary_two_hidden_three_shown(self):
        self.assertTrue(analytics.is_suppressed(2, 3))
        self.assertFalse(analytics.is_suppressed(3, 3))
        self.assertTrue(analytics.is_suppressed(None, 3))
        self.assertTrue(analytics.is_suppressed(0, 3))

    def test_cell_hides_value_and_users_not_zero(self):
        hidden = analytics.make_cell("2330", 40, 2, k=3, suppressible=True)
        self.assertEqual(hidden, {"key": "2330", "label": None, "value": None, "users": None, "suppressed": True,
                                  "suppression_reason": "min_users"})
        shown = analytics.make_cell("2330", 4.0, 3, k=3, suppressible=True, label="台積電")
        self.assertEqual((shown["value"], shown["users"], shown["suppressed"], shown["label"]), (4, 3, False, "台積電"))
        system = analytics.make_cell("llm", 1, 1, k=3, suppressible=False)
        self.assertEqual((system["value"], system["suppressed"]), (1, False))

    def test_order_does_not_leak_hidden_magnitudes(self):
        cells = [analytics.make_cell(k, v, u, k=3, suppressible=True)
                 for k, v, u in (("a", 1, 3), ("z", 999, 2), ("b", 5, 4), ("m", 1, 1))]
        self.assertEqual([c["key"] for c in analytics.order_cells(cells)], ["b", "a", "m", "z"])

    def test_merge_sums_values_and_takes_max_users(self):
        merged = analytics.merge_cells([("x", 2, 2), ("x", 3, 2), ("y", 1, None), ("y", 1, 3), ("z", 1, None)])
        self.assertEqual(merged, {"x": (5.0, 2), "y": (2.0, 3), "z": (1.0, None)})
        # 兩天各 2 人不會被當成 4 人。
        self.assertTrue(analytics.is_suppressed(merged["x"][1], 3))

    def test_open_vocabulary_drops_hidden_keys(self):
        merged = {"2330": (9.0, 3), "AAPL": (50.0, 2), "0700": (1.0, 1), "2317": (2.0, 5)}
        out = analytics.top_list(merged, k=3, limit=1, open_vocab=True)
        self.assertEqual([c["key"] for c in out["cells"]], ["2330"])
        self.assertEqual((out["suppressed_count"], out["truncated"]), (2, True))
        self.assertNotIn("AAPL", repr(out))

    def test_fixed_vocabulary_keeps_hidden_keys_and_ignores_unknown(self):
        merged = {"TW": (9.0, 3), "US": (50.0, 2), "HK": (4.0, 1), "BOGUS": (9.0, 9)}
        out = analytics.top_list(merged, k=3, limit=10, open_vocab=False, labels={"TW": "台股"},
                                 vocabulary=["TW", "US", "HK", "CN"])
        self.assertEqual([(c["key"], c["value"], c["suppressed"], c["suppression_reason"]) for c in out["cells"]],
                         [("TW", 9, False, None), ("HK", None, True, "min_users"), ("US", None, True, "min_users")])
        self.assertEqual(out["cells"][0]["label"], "台股")
        self.assertEqual((out["suppressed_count"], out["complementary_count"]), (2, 0))


def _cells(spec, k=3):
    return [analytics.make_cell(key, v, u, k=k, suppressible=True) for key, v, u in spec]


def _shape(cells):
    return {c["key"]: (c["value"], c["suppression_reason"]) for c in cells}


class ComplementarySuppressionTests(unittest.TestCase):
    """固定詞彙：被抑制恰好 1 格 → 再抑制數值最小的可見格；0 格、2 格不動；整個分布只剩它 → 連鍵拿掉。"""

    def test_zero_suppressed_untouched(self):
        cells, dropped = analytics.protect_fixed(_cells([("a", 9, 3), ("b", 4, 5)]))
        self.assertEqual((_shape(cells), dropped), ({"a": (9, None), "b": (4, None)}, 0))

    def test_exactly_one_suppressed_co_suppresses_smallest_visible(self):
        cells, dropped = analytics.protect_fixed(_cells([("a", 9, 3), ("b", 4, 5), ("c", 4, 4), ("z", 50, 1)]))
        # 同值時依鍵（b < c），選中的是「數值最小、未被抑制」的 b。
        self.assertEqual(_shape(cells), {"a": (9, None), "b": (None, "complementary"), "c": (4, None),
                                          "z": (None, "min_users")})
        self.assertEqual(dropped, 0)
        # 總量 67 減可見格（9＋4）＝54，是 b 與 z 的和：推不回 z 的 50。
        visible = sum(c["value"] for c in cells if not c["suppressed"])
        self.assertEqual(67 - visible, 4 + 50)
        hidden = [c for c in cells if c["suppressed"]]
        self.assertTrue(all(c["value"] is None and c["users"] is None for c in hidden))

    def test_two_suppressed_untouched(self):
        cells, dropped = analytics.protect_fixed(_cells([("a", 9, 3), ("y", 2, 2), ("z", 50, 1)]))
        self.assertEqual(_shape(cells), {"a": (9, None), "y": (None, "min_users"), "z": (None, "min_users")})
        self.assertEqual(dropped, 0)

    def test_sole_suppressed_cell_loses_its_key(self):
        self.assertEqual(analytics.protect_fixed(_cells([("z", 50, 1)])), ([], 1))

    def test_one_visible_and_one_suppressed_hides_both(self):
        cells, _ = analytics.protect_fixed(_cells([("a", 9, 3), ("z", 50, 1)]))
        self.assertEqual(_shape(cells), {"a": (None, "complementary"), "z": (None, "min_users")})

    def test_order_mixes_reasons_by_key_only(self):
        cells, _ = analytics.protect_fixed(_cells([("m", 9, 3), ("b", 4, 5), ("z", 50, 1), ("c", 20, 4)]))
        # 被抑制的兩格（b 互補、z 未達門檻）只依鍵排在最後，看不出哪格大。
        self.assertEqual([c["key"] for c in analytics.order_cells(cells)], ["c", "m", "b", "z"])

    def test_fixed_top_list_counts(self):
        out = analytics.top_list({"TW": (9.0, 3), "US": (50.0, 2), "HK": (20.0, 4)}, k=3, limit=10,
                                 open_vocab=False, vocabulary=["TW", "US", "HK"])
        self.assertEqual(_shape(out["cells"]), {"HK": (20, None), "TW": (None, "complementary"),
                                                "US": (None, "min_users")})
        self.assertEqual((out["suppressed_count"], out["complementary_count"]), (1, 1))
        sole = analytics.top_list({"US": (50.0, 2)}, k=3, limit=10, open_vocab=False, vocabulary=["US"])
        self.assertEqual((sole["cells"], sole["suppressed_count"]), ([], 1))

    def test_open_list_with_one_hidden_hides_smallest_visible(self):
        merged = {"2330": (9.0, 3), "AAPL": (50.0, 2), "2317": (2.0, 5)}
        out = analytics.top_list(merged, k=3, limit=10, open_vocab=True)
        self.assertEqual([c["key"] for c in out["cells"]], ["2330"])
        self.assertEqual((out["suppressed_count"], out["complementary_count"], out["truncated"]), (1, 1, False))
        self.assertNotIn("2317", repr(out))
        self.assertNotIn("AAPL", repr(out))

    def test_open_list_zero_or_two_hidden_or_truncated_untouched(self):
        none_hidden = analytics.top_list({"a": (9.0, 3), "b": (2.0, 5)}, k=3, limit=10, open_vocab=True)
        self.assertEqual(([c["key"] for c in none_hidden["cells"]], none_hidden["complementary_count"]),
                         (["a", "b"], 0))
        two = analytics.top_list({"a": (9.0, 3), "x": (1.0, 1), "y": (1.0, 2)}, k=3, limit=10, open_vocab=True)
        self.assertEqual(([c["key"] for c in two["cells"]], two["suppressed_count"], two["complementary_count"]),
                         (["a"], 2, 0))
        # 被 limit 截斷：截掉的可見項本身就讓總量差不唯一。
        cut = analytics.top_list({"a": (9.0, 3), "b": (5.0, 4), "x": (1.0, 1)}, k=3, limit=1, open_vocab=True)
        self.assertEqual(([c["key"] for c in cut["cells"]], cut["complementary_count"], cut["truncated"]),
                         (["a"], 0, True))


class PlanRangeTests(unittest.TestCase):
    def test_defaults_and_clamp(self):
        p = analytics.plan_range(None, None, today_=TODAY, live_days=90)
        self.assertEqual((p.since, p.until), (TODAY - timedelta(days=29), TODAY))
        self.assertEqual((p.live, p.rollup), ((p.since, TODAY), None))
        p = analytics.plan_range(None, TODAY + timedelta(days=5), today_=TODAY, live_days=90)
        self.assertEqual(p.until, TODAY)

    def test_split_between_rollup_and_live(self):
        p = analytics.plan_range(date(2026, 1, 1), TODAY, today_=TODAY, live_days=90)
        live_since = TODAY - timedelta(days=89)
        self.assertEqual(p.live_since, live_since)
        self.assertEqual(p.rollup, (date(2026, 1, 1), live_since - timedelta(days=1)))
        self.assertEqual(p.live, (live_since, TODAY))
        info = analytics.range_info(p, analytics.Params(min_users=3))
        self.assertEqual([s["source"] for s in info["spans"]], ["rollup", "live"])
        self.assertEqual((info["min_users"], info["timezone"]), (3, "Asia/Taipei"))
        old = analytics.plan_range(date(2025, 1, 1), date(2025, 2, 1), today_=TODAY, live_days=90)
        self.assertIsNone(old.live)

    def test_errors(self):
        with self.assertRaises(analytics.RangeError):
            analytics.plan_range(TODAY, TODAY - timedelta(days=1), today_=TODAY, live_days=90)
        with self.assertRaises(analytics.RangeError):
            analytics.plan_range(TODAY - timedelta(days=731), TODAY, today_=TODAY, live_days=90)
        analytics.plan_range(TODAY - timedelta(days=730), TODAY, today_=TODAY, live_days=90)

    def test_week_start_is_monday(self):
        self.assertEqual(analytics.week_start(date(2026, 10, 7)), date(2026, 10, 5))
        self.assertEqual(analytics.week_start(date(2026, 10, 5)), date(2026, 10, 5))


class JudgeFilterTests(unittest.TestCase):
    def test_score_columns_only_count_current_judge(self):
        sql = analytics._qa_sql(True)
        lines = {line.strip().rsplit(" AS ", 1)[-1].rstrip(","): line for line in sql.splitlines() if " AS " in line}
        for col in ("judge_checked", "degraded", "below_min", "score_sum", "score_n"):
            with self.subTest(col=col):
                self.assertIn(CURRENT_JUDGE_SQL, lines[col])
        self.assertNotIn(CURRENT_JUDGE_SQL, lines["checked_all"])   # 覆蓋率類：所有 judge
        self.assertNotIn("question", sql.replace("questions", ""))   # 問題文字連讀都不讀
        self.assertNotIn("answer", sql)

    def test_no_query_selects_or_groups_by_user(self):
        for name in ("_qa_sql", "_route_sql", "_hot_sql", "_active_sql"):
            for by_day in (True, False):
                sql = getattr(analytics, name)(by_day)
                with self.subTest(sql=name, by_day=by_day):
                    stripped = sql.replace("count(DISTINCT q.user_id)", "").replace("count(DISTINCT c.user_id)", "")
                    stripped = stripped.replace("count(DISTINCT x.uid)", "")
                    if name == "_hot_sql":
                        # CTE 內選 user_id 只為了外層 count(DISTINCT)；外層不輸出、不分組。
                        stripped = stripped.replace("q.user_id,", "")
                    if name == "_active_sql":
                        stripped = stripped.replace("q.user_id AS uid", "").replace("c.user_id", "")
                        stripped = stripped.replace("q.user_id IS NOT NULL", "")
                    self.assertNotIn("user_id", stripped)


# ── 彙總腳本與 unit ───────────────────────────────────────────────────────────


def _ns(**kw):
    base = {"day": None, "backfill": None, "force": False, "dry_run": False}
    return argparse.Namespace(**{**base, **kw})


class RollupScriptTests(unittest.TestCase):
    def test_plan_days(self):
        y = TODAY - timedelta(days=1)
        overwrite, fill = analytics_rollup.plan_days(_ns(), TODAY, 90)
        self.assertEqual(overwrite, [y])
        self.assertEqual((fill[0], fill[-1], len(fill)), (TODAY - timedelta(days=89), y - timedelta(days=1), 88))
        self.assertEqual(analytics_rollup.plan_days(_ns(backfill=3), TODAY, 90),
                         ([], [y - timedelta(days=2), y - timedelta(days=1), y]))
        self.assertEqual(analytics_rollup.plan_days(_ns(backfill=2, force=True), TODAY, 90),
                         ([y - timedelta(days=1), y], []))
        self.assertEqual(analytics_rollup.plan_days(_ns(day=date(2026, 1, 2)), TODAY, 90), ([date(2026, 1, 2)], []))

    def test_bad_arguments_exit_1_not_2(self):
        with mock.patch("sys.stderr"):
            with self.assertRaises(SystemExit) as cm:
                analytics_rollup.main(["--bogus"])
            self.assertEqual(cm.exception.code, 1)
            with self.assertRaises(SystemExit) as cm:
                analytics_rollup.main(["--day", "2026-10-01", "--backfill", "3"])
            self.assertEqual(cm.exception.code, 1)
            with mock.patch.object(analytics, "today", lambda: TODAY):
                self.assertEqual(analytics_rollup.main(["--day", TODAY.isoformat()]), 1)
                self.assertEqual(analytics_rollup.main(["--backfill", "0"]), 1)
                self.assertEqual(analytics_rollup.main(["--force"]), 1)

    def test_script_does_not_load_heavy_or_llm_modules(self):
        """零 LLM、不載嵌入模型：import 腳本（與它取用的服務層）不得拖進 torch、檢索、嵌入或 LLM 呼叫層。"""
        heavy = ("torch", "transformers", "FlagEmbedding", "sentence_transformers")
        banned = ("app.services.embed", "app.services.retrieval", "app.services.retrieval_pipeline",
                  "app.services.answer", "app.services.llm", "app.services.llm_http", "app.services.rerank",
                  "scripts._claude_cli", "scripts._claude_lock")
        code = (
            "import sys; sys.path.insert(0, 'scripts'); import analytics_rollup; "
            f"bad = [m for m in sys.modules if m.split('.')[0] in {heavy!r} or m in {banned!r}]; print(bad)"
        )
        proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=str(REPO_ROOT),
                              env={"PATH": "/usr/bin:/bin", "REPORT_MARK_DB_URL":
                                   "postgresql+asyncpg://nobody:x@127.0.0.1:45999/none"}, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "[]", proc.stdout + proc.stderr)

    def test_not_an_llm_entry(self):
        """不 import 任何 LLM 呼叫層（直接或間接），所以不在 test_llm_env_loading 的入口掃描範圍內。"""
        import ast

        sys.path.insert(0, str(REPO_ROOT / "tests"))
        import test_llm_env_loading as le

        tree = ast.parse((REPO_ROOT / "scripts" / "analytics_rollup.py").read_text(encoding="utf-8"))
        self.assertFalse(le._imports_llm(tree))
        self.assertNotIn("scripts/analytics_rollup.py", le._scanned_files())


class RollupUnitTests(unittest.TestCase):
    SYSTEMD = REPO_ROOT / "deploy" / "systemd"

    def _directives(self, name: str) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for line in (self.SYSTEMD / name).read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s and not s.startswith(("#", "[")) and "=" in s:
                k, _, v = s.partition("=")
                out.setdefault(k.strip(), []).append(v.strip())
        return out

    def test_service_exit_codes_and_entry(self):
        d = self._directives("report-mark-analytics-rollup.service")
        self.assertEqual(d["Type"], ["oneshot"])
        self.assertEqual(d["SuccessExitStatus"], ["2"])
        self.assertEqual(d["OnFailure"], ["report-mark-alert@%n.service"])
        self.assertIn("scripts/analytics_rollup.py", d["ExecStart"][0])
        self.assertNotIn("report-mark-llm", " ".join(d.get("EnvironmentFile", [])))   # 零 LLM：不載金鑰

    def test_timer_avoids_sync_and_backup(self):
        d = self._directives("report-mark-analytics-rollup.timer")
        (cal,) = d["OnCalendar"]
        hh, mm = (int(x) for x in cal.split()[-1].split(":")[:2])
        self.assertNotEqual(mm, 0)            # sync 在每 3 小時的整點
        self.assertNotIn(hh, (3, 4))          # 備份 03:30、稽核錨定 04:15、回放刪除 04:30
        self.assertEqual(d["Persistent"], ["true"])


if __name__ == "__main__":
    unittest.main()
