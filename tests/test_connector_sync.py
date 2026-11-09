"""`scripts/connector_sync.py`：框架的導入排程照抄 sync 鏈第 4 段起的步驟，`extract_signals` 永遠帶 `--limit`。

與殼刻意不同的只有分層回填（`cs.FRAMEWORK_ONLY_DIFFERENCES`，白名單式對帳）：本輪新研報只把第二層交給摘要、
標題、摘錄，訊號與標題積壓帶 `--min-report-date`。

不跑任何子行程：`run_round` 的 `run` 換成記錄器。步驟與 `scripts/sync_new_reports.sh` 的對帳以文字比對殼裡的呼叫。
"""
from __future__ import annotations

import re
import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scripts import connector_sync as cs  # noqa: E402

SYNC_SH = REPO_ROOT / "scripts" / "sync_new_reports.sh"


def _names(steps):
    return [s.name for s in steps]


class DownstreamStepTests(unittest.TestCase):
    H = Path("/tmp/h.txt")

    def test_with_new_reports_runs_the_whole_chain_in_sync_order(self):
        steps = cs.downstream_steps(self.H, True, {})
        self.assertEqual(_names(steps), ["generate_summaries", "generate_titles", "extract_takeaways",
                                         "extract_signals", "generate_brief", "generate_titles_backlog"])
        for step in steps[:3]:
            self.assertEqual(step.argv[2:4], ("--hashes-file", str(self.H)))
            self.assertNotIn("--since-days", step.argv)
        self.assertIn("--exclude-hashes-file", steps[-1].argv)

    def test_without_new_reports_only_backlogs_and_brief(self):
        steps = cs.downstream_steps(self.H, False, {})
        self.assertEqual(_names(steps), ["extract_signals", "generate_brief", "generate_titles_backlog"])
        self.assertNotIn("--exclude-hashes-file", steps[-1].argv)

    def test_signals_and_title_backlog_always_have_a_limit(self):
        for env in ({}, {"SYNC_SIGNAL_LIMIT": "", "SYNC_TITLE_BACKLOG_LIMIT": "0"}, {"SYNC_SIGNAL_LIMIT": "abc"}):
            for has_new in (True, False):
                steps = {s.name: s.argv for s in cs.downstream_steps(self.H, has_new, env)}
                with self.subTest(env=env, has_new=has_new):
                    sig = steps["extract_signals"]
                    self.assertEqual(sig[sig.index("--limit") + 1], "100")
                    backlog = steps["generate_titles_backlog"]
                    self.assertEqual(backlog[backlog.index("--limit") + 1], "60")

    def test_knobs_are_honoured(self):
        env = {"SYNC_SIGNAL_LIMIT": "20", "SYNC_TITLE_BACKLOG_LIMIT": "7", "SYNC_SUMMARY_WORKERS": "3"}
        steps = {s.name: s.argv for s in cs.downstream_steps(self.H, True, env)}
        self.assertIn("20", steps["extract_signals"])
        self.assertIn("7", steps["generate_titles_backlog"])
        self.assertEqual(steps["generate_summaries"][-2:], ("--workers", "3"))

    def test_steps_match_the_tingfong_shell(self):
        """sync 殼第 4 段起呼叫的腳本與順序，這裡一支不少（殼是廷豐主機層、這支是框架，兩邊同步改）。"""
        text = SYNC_SH.read_text(encoding="utf-8")
        tail = text[text.index("# 4) 本次有新研報入庫才補摘要"):]
        shell = re.findall(r'"\$UV" run python scripts/(\w+)\.py', tail)
        mine = [s.argv[1].removeprefix("scripts/").removesuffix(".py") for s in cs.downstream_steps(self.H, True, {})]
        self.assertEqual(mine, shell)
        self.assertIn("SIGNAL_LIMIT=${SYNC_SIGNAL_LIMIT:-100}", text)
        self.assertIn("TITLE_BACKLOG_LIMIT=${SYNC_TITLE_BACKLOG_LIMIT:-60}", text)


class FrameworkOnlyDifferenceTests(unittest.TestCase):
    """與 sync 殼的差異只准是白名單列的那幾項（分層回填），其他參數逐字照殼。"""

    H = Path("/tmp/h.txt")
    ALL = Path("/tmp/all.txt")
    CUT = date(2025, 11, 10)

    def test_only_whitelisted_flags_are_added(self):
        plain = {s.name: s.argv for s in cs.downstream_steps(self.H, True, {})}
        tiered = {s.name: s.argv for s in cs.downstream_steps(self.H, True, {}, min_report_date=self.CUT,
                                                              exclude=self.ALL)}
        self.assertEqual(list(plain), list(tiered))
        added = {}
        for name in plain:
            extra = [a for a in tiered[name] if a not in plain[name]]
            if extra:
                added[name] = extra
        self.assertEqual(added, {"extract_signals": ["--min-report-date", "2025-11-10"],
                                 "generate_titles_backlog": [str(self.ALL), "--min-report-date", "2025-11-10"]})
        # 標題積壓排掉的是本輪全部新研報（不只第二層），參數位置同殼。
        backlog = tiered["generate_titles_backlog"]
        self.assertEqual(backlog[backlog.index("--exclude-hashes-file") + 1], str(self.ALL))
        whitelisted = {k.split(" ")[0] for k in cs.FRAMEWORK_ONLY_DIFFERENCES if " --min-report-date" in k}
        self.assertEqual(whitelisted, {"extract_signals", "generate_titles_backlog"})
        self.assertIn("hash 步驟的清單只含第二層", cs.FRAMEWORK_ONLY_DIFFERENCES)

    def test_the_shell_does_not_tier(self):
        """廷豐的 sync 殼不分層（廷豐已全量跑完派生）：白名單的旗標只出現在框架這一邊。"""
        self.assertNotIn("--min-report-date", SYNC_SH.read_text(encoding="utf-8"))

    def test_signals_still_always_limited_when_tiered(self):
        steps = {s.name: s.argv for s in cs.downstream_steps(self.H, False, {}, min_report_date=self.CUT)}
        self.assertIn("--limit", steps["extract_signals"])
        self.assertIn("--limit", steps["generate_titles_backlog"])


class RunRoundTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.data = Path(self._tmp.name)
        self.cmds: list[list[str]] = []
        self.logs: list[str] = []
        self.rcs: dict[str, int] = {}
        self.new_hashes = ["a" * 64]

    def run_cmd(self, cmd):
        self.cmds.append(list(cmd))
        script = Path(cmd[1]).stem
        if script == "ingest_connectors":
            out = Path(cmd[cmd.index("--hashes-out") + 1])
            out.write_text("\n".join(self.new_hashes), encoding="utf-8")
        return self.rcs.get(script, 0)

    def round(self, **kw):
        args = dict(data_dir=self.data, enabled=True, any_due=True, force=False, env={}, run=self.run_cmd,
                    log=self.logs.append, now=lambda: datetime(2026, 11, 10, 9, 0, 0))
        args.update(kw)
        return cs.run_round(**args)

    def scripts(self):
        return [Path(c[1]).stem for c in self.cmds]

    def test_noop_without_connectors_or_when_nothing_is_due(self):
        self.assertEqual(self.round(enabled=False), 0)
        self.assertEqual(self.round(any_due=False), 0)
        self.assertEqual(self.cmds, [])

    def test_full_round(self):
        self.assertEqual(self.round(), 0)
        self.assertEqual(self.scripts(), ["ingest_connectors", "generate_summaries", "generate_titles",
                                          "extract_takeaways", "extract_signals", "generate_brief",
                                          "generate_titles"])
        self.assertEqual(list(self.data.iterdir()), [], "本輪 hashes 用完就刪")

    def test_lock_busy_ingest_skips_the_round(self):
        self.rcs["ingest_connectors"] = cs.LOCK_BUSY_RC
        self.assertEqual(self.round(), 0)
        self.assertEqual(self.scripts(), ["ingest_connectors"])

    def test_failed_ingest_stops_the_round(self):
        self.rcs["ingest_connectors"] = 2
        self.assertEqual(self.round(), 1)
        self.assertEqual(self.scripts(), ["ingest_connectors"])

    def test_downstream_is_best_effort_and_env_abort_retains_hashes(self):
        self.rcs["generate_summaries"] = cs.ENV_ABORT_RC
        self.assertEqual(self.round(), 0)
        self.assertEqual(self.scripts()[-1], "generate_titles", "下游失敗照常往下跑")
        retained = list(self.data.glob("hashes_retained_*.txt"))
        self.assertEqual(len(retained), 1)
        self.assertEqual(retained[0].read_text(encoding="utf-8"), "a" * 64)
        self.assertTrue(any("--hashes-file" in line for line in self.logs))

    def test_tiered_round_hands_only_recent_reports_to_the_hash_steps(self):
        recent, old, nodate = "r" * 64, "o" * 64, "n" * 64
        self.new_hashes = [old, recent, nodate]
        fed = {}

        def run_cmd(cmd):
            rc = self.run_cmd(cmd)
            if "--hashes-file" in cmd:
                fed[Path(cmd[1]).stem] = Path(cmd[cmd.index("--hashes-file") + 1]).read_text().split()
            return rc

        self.assertEqual(self.round(run=run_cmd, recent_filter=lambda hs: [h for h in hs if h == recent],
                                    min_report_date=date(2025, 11, 10)), 0)
        self.assertEqual(fed, {"generate_summaries": [recent], "generate_titles": [recent],
                               "extract_takeaways": [recent]})
        signal = next(c for c in self.cmds if c[1].endswith("extract_signals.py"))
        self.assertEqual(signal[signal.index("--min-report-date") + 1], "2025-11-10")
        self.assertTrue(any("第二層 1 份" in line and "其餘 2 份" in line for line in self.logs))
        self.assertEqual(list(self.data.iterdir()), [], "本輪 hashes 與第二層清單用完就刪")

    def test_no_recent_reports_skips_the_hash_steps_but_excludes_the_round(self):
        self.new_hashes = ["o" * 64]
        self.round(recent_filter=lambda hs: [])
        self.assertEqual(self.scripts(), ["ingest_connectors", "extract_signals", "generate_brief", "generate_titles"])
        self.assertIn("--exclude-hashes-file", self.cmds[-1])

    def test_unknown_dates_fall_back_to_the_shell_behaviour(self):
        def broken(hashes):
            raise RuntimeError("db down")

        self.round(recent_filter=broken)
        self.assertEqual(self.scripts()[1:4], ["generate_summaries", "generate_titles", "extract_takeaways"])
        self.assertTrue(any("本輪不分層" in line for line in self.logs))

    def test_env_abort_retains_the_tiered_list(self):
        recent = "r" * 64
        self.new_hashes = ["o" * 64, recent]
        self.rcs["generate_summaries"] = cs.ENV_ABORT_RC
        self.round(recent_filter=lambda hs: [recent])
        retained = list(self.data.glob("hashes_retained_*.txt"))
        self.assertEqual([p.read_text(encoding="utf-8").split() for p in retained], [[recent]])

    def test_force_is_passed_to_ingest(self):
        self.round(force=True, any_due=False)
        self.assertIn("--force", self.cmds[0])


if __name__ == "__main__":
    unittest.main()
