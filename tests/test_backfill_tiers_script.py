"""scripts/backfill_tiers.py：分批、上限停止與續跑、dry-run 不呼叫 LLM、子批次退出碼的處置。

不連 DB、不呼叫 LLM、不起子行程：候選由假的 fetch 給（模擬 DB：處理過就不再待補）、子批次由假的 run 接
（可順手往暫存的用量檔寫幾行，模擬批次的用量記錄）。帳與 hashes 檔一律寫在 tempfile。
"""
from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services import backfill_tiers as bt  # noqa: E402
from scripts import backfill_tiers as bf  # noqa: E402

TODAY = date(2026, 11, 10)


def _cand(h, tokens=100, tasks=("summary", "title", "takeaway")):
    return bt.Candidate(file_hash=h, report_date=None, tasks=tuple(tasks), tokens=tokens)


class FakeCorpus:
    """模擬 DB：候選依序回傳；`run` 跑過摘要的研報就不再待補（各批次自己的「已完成」判斷）。"""

    def __init__(self, cands, usage_log: Path | None = None, rc_for=None, tokens_per_call=0):
        self.cands = list(cands)
        self.done: set[str] = set()
        self.calls: list[list[str]] = []
        self.fetches: list[tuple[set[str], int]] = []
        self.usage_log = usage_log
        self.rc_for = rc_for or {}
        self.tokens_per_call = tokens_per_call

    def fetch(self, exclude, limit):
        self.fetches.append((set(exclude), limit))
        return [c for c in self.cands if c.file_hash not in self.done and c.file_hash not in exclude][:limit]

    def run(self, argv):
        argv = list(argv)
        self.calls.append(argv)
        script = Path(argv[1]).name
        hashes = Path(argv[argv.index("--hashes-file") + 1]).read_text().split()
        if self.usage_log is not None and self.tokens_per_call:
            with self.usage_log.open("a", encoding="utf-8") as fh:
                for h in hashes:
                    task = next(t for t, name in bf.STEP_SCRIPTS.items() if name == script)
                    fh.write(json.dumps({"task": task,
                                         "file_hash": h, "tokens": {"hit": 0, "miss": self.tokens_per_call,
                                                                     "completion": 0}}) + "\n")
        rc = self.rc_for.get(script, 0)
        if rc == 0 and script == "generate_summaries.py":
            self.done.update(hashes)
        return rc


class _Case(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.work = self.dir / "backfill"
        self.usage = self.dir / "llm_usage.jsonl"
        self.state = self.dir / "state.json"
        self.logs: list[str] = []

    def go(self, corpus, **kw):
        args: dict = dict(tier=3, fetch=corpus.fetch, run=corpus.run, chunk_size=2, max_reports=None, token_limit=None,
                    dry_run=False, work_dir=self.work, usage_log=self.usage, log=self.logs.append,
                    stamp=lambda: "20261110_000000")
        args.update(kw)
        return bf.run_backfill(**args)


class DryRunTests(_Case):
    def test_dry_run_never_runs_a_batch_or_writes_state(self):
        corpus = FakeCorpus([_cand(f"h{i}") for i in range(5)])
        out = self.go(corpus, dry_run=True, state_file=self.state, daily_limit=10_000, today=TODAY)
        self.assertEqual(corpus.calls, [])
        self.assertFalse(self.state.exists())
        self.assertFalse(self.work.exists())
        self.assertEqual((out.reports, out.batches, out.tokens, out.stopped_by), (5, 3, 500, None))
        self.assertEqual(out.task_counts, {"summary": 5, "title": 5, "takeaway": 5})

    def test_dry_run_applies_the_same_limits(self):
        corpus = FakeCorpus([_cand(f"h{i}") for i in range(5)])
        out = self.go(corpus, dry_run=True, state_file=self.state, daily_limit=350, today=TODAY)
        self.assertEqual((out.reports, out.tokens, out.stopped_by), (3, 300, "tokens"))

    def test_main_dry_run_skips_key_check_and_subprocess(self):
        with mock.patch.object(bf, "require_llm_key", side_effect=AssertionError("dry-run 不得預檢金鑰")), \
             mock.patch.object(bf.subprocess, "run", side_effect=AssertionError("dry-run 不得起子行程")), \
             mock.patch.object(bf, "fetch_candidates", new=mock.AsyncMock(return_value=[])) as fetch, \
             mock.patch.object(bf, "orchestrator_lock", side_effect=AssertionError("dry-run 不取編排鎖")), \
             redirect_stdout(io.StringIO()) as buf:
            rc = bf.main(["--tier", "2", "--dry-run"])
        self.assertEqual(rc, 0)
        fetch.assert_awaited()
        self.assertIn("[dry-run] 會排入 0 份", buf.getvalue())


class ChunkTests(_Case):
    def test_chunks_and_step_arguments(self):
        corpus = FakeCorpus([_cand("a"), _cand("b", tasks=("title", "signal")), _cand("c", tasks=("summary",))])
        out = self.go(corpus, tier=2)
        self.assertEqual((out.rc, out.reports, out.batches, out.stopped_by), (0, 3, 2, None))
        scripts = [[Path(c[1]).name for c in corpus.calls if c] for _ in [0]][0]
        self.assertEqual(scripts, ["generate_summaries.py", "generate_titles.py", "extract_takeaways.py",
                                   "extract_signals.py", "generate_summaries.py"])
        signal = next(c for c in corpus.calls if c[1].endswith("extract_signals.py"))
        self.assertEqual(signal[signal.index("--limit") + 1], "2")
        self.assertEqual(corpus.calls[0][0], sys.executable)
        # 每批的 hashes 檔跑完就刪
        self.assertEqual(list(self.work.iterdir()), [])

    def test_workers_pass_through(self):
        corpus = FakeCorpus([_cand("a", tasks=("summary",))])
        self.go(corpus, workers=3)
        self.assertEqual(corpus.calls[0][-2:], ["--workers", "3"])

    def test_reports_already_handled_are_not_refetched(self):
        """沒補成（例如內容型失敗還沒進跳過名單）的研報，同一次執行不再挑——不會無限迴圈。"""
        corpus = FakeCorpus([_cand("a", tasks=("title",)), _cand("b", tasks=("title",))])
        out = self.go(corpus, tier=2)
        self.assertEqual((out.reports, out.stopped_by), (2, None))
        self.assertEqual(corpus.fetches[-1][0], {"a", "b"})

    def test_max_reports(self):
        corpus = FakeCorpus([_cand(f"h{i}") for i in range(5)])
        out = self.go(corpus, tier=2, max_reports=3)
        self.assertEqual((out.reports, out.stopped_by), (3, "reports"))
        self.assertEqual([f[1] for f in corpus.fetches], [2, 1])


class BudgetTests(_Case):
    def test_stops_at_daily_limit_then_resumes_next_round_and_next_day(self):
        corpus = FakeCorpus([_cand(f"h{i}") for i in range(6)], usage_log=self.usage, tokens_per_call=40)
        self.usage.write_text("")
        # 每份實測 120 token（摘要、標題、摘錄各 40）。上限 300：第一批 2 份（240）後剩 60，下一份估 100 → 停。
        out = self.go(corpus, state_file=self.state, daily_limit=300, today=TODAY)
        self.assertEqual((out.reports, out.tokens, out.stopped_by), (2, 240, "tokens"))
        state = bt.load_state(self.state)
        self.assertEqual((state.day, state.tokens, state.reports, state.measured), ("2026-11-10", 240, 2, True))
        # 同一天再叫一次：剩 60，一份都排不進，不跑任何批次。
        n = len(corpus.calls)
        again = self.go(corpus, state_file=self.state, daily_limit=300, today=TODAY)
        self.assertEqual((again.reports, again.stopped_by, len(corpus.calls)), (0, "tokens", n))
        # 隔天：帳歸零，從還沒補的 h2 接著補。
        nxt = self.go(corpus, state_file=self.state, daily_limit=300, today=date(2026, 11, 11))
        self.assertEqual(nxt.reports, 2)
        first = corpus.calls[n]
        self.assertEqual(Path(first[first.index("--hashes-file") + 1]).name.split("_")[0], "tier3")
        self.assertEqual(corpus.done, {"h0", "h1", "h2", "h3"})
        self.assertEqual(bt.load_state(self.state).tokens, 240)

    def test_estimate_is_booked_when_usage_log_is_unavailable(self):
        corpus = FakeCorpus([_cand("a", tokens=70)])
        out = self.go(corpus, state_file=self.state, daily_limit=1000, today=TODAY, usage_log=Path("/dev/null"))
        self.assertEqual(out.tokens, 70)
        self.assertEqual((bt.load_state(self.state).tokens, bt.load_state(self.state).measured), (70, False))

    def test_measured_zero_when_batches_skip_everything(self):
        """子批次判斷都已完成（沒送 LLM）時用量檔沒有新行：入帳 0，不是估計值。"""
        self.usage.write_text("")
        corpus = FakeCorpus([_cand("a", tokens=70)], usage_log=self.usage, tokens_per_call=0)
        out = self.go(corpus, state_file=self.state, daily_limit=1000, today=TODAY)
        self.assertEqual(out.tokens, 0)

    def test_token_limit_for_tier2(self):
        corpus = FakeCorpus([_cand(f"h{i}") for i in range(4)])
        out = self.go(corpus, tier=2, token_limit=250, usage_log=Path("/dev/null"))
        self.assertEqual((out.reports, out.stopped_by), (2, "tokens"))

    def test_oversized_report_does_not_block_the_queue(self):
        corpus = FakeCorpus([_cand("huge", tokens=5000), _cand("b", tokens=10)])
        out = self.go(corpus, state_file=self.state, daily_limit=1000, today=TODAY, usage_log=Path("/dev/null"))
        self.assertEqual((out.reports, out.oversized), (1, ["huge"]))
        self.assertTrue(any("超過每日上限" in line for line in self.logs))


class ExitCodeTests(_Case):
    def test_lock_busy_stops_the_round(self):
        corpus = FakeCorpus([_cand(f"h{i}") for i in range(4)], rc_for={"generate_titles.py": 75})
        out = self.go(corpus, tier=2)
        self.assertEqual((out.rc, out.stopped_by, out.batches), (75, "lock_busy", 1))
        self.assertEqual([Path(c[1]).name for c in corpus.calls], ["generate_summaries.py", "generate_titles.py"])

    def test_env_abort_keeps_hashes_and_prints_replay(self):
        corpus = FakeCorpus([_cand("a"), _cand("b")], rc_for={"extract_takeaways.py": 2})
        out = self.go(corpus, tier=2)
        self.assertEqual((out.rc, out.stopped_by), (2, "env_abort"))
        retained = list(self.work.glob("retained_*.txt"))
        self.assertEqual(len(retained), 1)
        self.assertEqual(retained[0].read_text().split(), ["a", "b"])
        self.assertTrue(any(f"scripts/extract_takeaways.py --hashes-file {retained[0]}" in line for line in self.logs))

    def test_other_failures_are_best_effort(self):
        corpus = FakeCorpus([_cand("a")], rc_for={"generate_titles.py": 1})
        out = self.go(corpus, tier=2)
        self.assertEqual((out.rc, out.reports), (0, 1))
        self.assertEqual(len(corpus.calls), 3)


class MainTests(unittest.TestCase):
    def test_scheduled_background_is_a_noop_when_disabled(self):
        with mock.patch.object(bf, "fetch_candidates", side_effect=AssertionError("不該挑候選")), \
             mock.patch.object(bf, "require_llm_key", side_effect=AssertionError("不該預檢")), \
             redirect_stdout(io.StringIO()) as buf:
            rc = bf.main(["--tier", "3", "--scheduled"])
        self.assertEqual(rc, 0)
        self.assertIn("背景補齊未啟用", buf.getvalue())

    def test_key_check_happens_before_the_lock(self):
        order = []
        with mock.patch.object(bf, "require_llm_key", side_effect=lambda m: order.append("key")), \
             mock.patch.object(bf, "orchestrator_lock", side_effect=lambda p: order.append("lock") or _Busy()), \
             redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            rc = bf.main(["--tier", "2"])
        self.assertEqual(order, ["key", "lock"])
        self.assertEqual(rc, bf.LOCK_BUSY_RC)

    def test_models_cover_every_derived_task(self):
        self.assertEqual(set(bf.task_models()), set(bt.DERIVED_TASKS))
        self.assertEqual(set(bf.STEP_SCRIPTS), set(bt.DERIVED_TASKS))
        for script in bf.STEP_SCRIPTS.values():
            self.assertTrue((REPO_ROOT / "scripts" / script).is_file(), script)


class _Busy:
    def __enter__(self):
        raise bf.OrchestratorBusy("busy")

    def __exit__(self, *exc):
        return False


class OrchestratorLockTests(unittest.TestCase):
    def test_second_holder_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sub" / ".lock"
            with bf.orchestrator_lock(path):
                with self.assertRaises(bf.OrchestratorBusy):
                    with bf.orchestrator_lock(path):
                        pass
            with bf.orchestrator_lock(path):
                pass


class ChunkStepsTests(unittest.TestCase):
    def test_only_needed_tasks(self):
        steps = bf.chunk_steps(Path("/tmp/x.txt"), [_cand("a", tasks=("title",))])
        self.assertEqual([s.task for s in steps], ["title"])
        self.assertEqual(steps[0].argv[1:], ("scripts/generate_titles.py", "--hashes-file", "/tmp/x.txt"))


if __name__ == "__main__":
    unittest.main()
