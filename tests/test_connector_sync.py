"""`scripts/connector_sync.py`：框架的導入排程照抄 sync 鏈第 4 段起的步驟，`extract_signals` 永遠帶 `--limit`。

不跑任何子行程：`run_round` 的 `run` 換成記錄器。步驟與 `scripts/sync_new_reports.sh` 的對帳以文字比對殼裡的呼叫。
"""
from __future__ import annotations

import re
import sys
import tempfile
import unittest
from datetime import datetime
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

    def test_force_is_passed_to_ingest(self):
        self.round(force=True, any_due=False)
        self.assertIn("--force", self.cmds[0])


if __name__ == "__main__":
    unittest.main()
