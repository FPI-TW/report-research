# tests/test_sync_replay.py
"""整批中止後的重放素材：保留的 delta 與 hashes（`scripts/sync_new_reports.sh`）。

delta 與 `data/.sync_last_hashes` 都是「只有這一輪有」的輸入：rsync `--size-only`
讓下一輪 delta 不再列出已落地的檔，而 hashes 下一輪就被 importer 覆寫。帳號／環境型
中止（rc=2）不寫 `data/sync_failures.log`、不進 `research.llm_task_failure`，
`failures_to_delta.py` 撈不到，所以這兩份檔是事後重放的唯一依據。

本檔釘住：
- 匯入段 rc 不是 0／75 時，依時間序列出**所有**保留的 delta，每份配 `--delta`
  ＋自己的 `--hashes-out` 重放指令，而且不叫人跑 `--all-local`。
- 下游任一段 rc=2 時，把當輪 hashes 複製成 `data/sync_hashes_retained_<ts>.txt`
  並印出三段 `--hashes-file` 補跑指令；rc=1／rc=75 不保留。
- `sync_new_reports.py --hashes-out`（審查 M14）：手動重放多份 delta 時每份 hashes
  各寫一份，不互相覆寫，也不動排程殼讀的預設檔。

殼那一側沿用 `test_pipeline_heartbeat._SyncHarness`：假二進位跑真腳本。
"""
import argparse
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import sync_new_reports as snr  # noqa: E402
from test_pipeline_heartbeat import LOCK_BUSY_RC, _SyncHarness  # noqa: E402

_RETAINED_RE = re.compile(r"^sync_hashes_retained_\d{8}_\d{6}\.txt$")


def _plant_delta(h: _SyncHarness, name: str, age_seconds: int) -> str:
    """在假 repo 的 data/ 放一份前幾輪留下的 delta，mtime 往回推 age_seconds。"""
    p = h.root / "data" / name
    p.write_text("某券商/舊報告.pdf\n", encoding="utf-8")
    t = time.time() - age_seconds
    os.utime(p, (t, t))
    return f"data/{name}"


def _replay_lines(stdout: str) -> list[str]:
    return [ln for ln in stdout.splitlines() if "sync_new_reports.py --delta" in ln]


class ImportAbortListsDeltasTests(unittest.TestCase):
    def setUp(self):
        self.h = _SyncHarness()
        self.addCleanup(self.h.close)

    def test_lists_all_retained_deltas_oldest_first(self):
        oldest = _plant_delta(self.h, "sync_delta_20260901_000000.txt", 7200)
        older = _plant_delta(self.h, "sync_delta_20260901_030000.txt", 3600)
        self.h.set_rc(sync_new_reports=2)
        p = self.h.run()
        self.assertNotEqual(p.returncode, 0)
        lines = _replay_lines(p.stdout)
        self.assertEqual(len(lines), 3, p.stdout)
        self.assertIn(oldest, lines[0])
        self.assertIn(older, lines[1])
        # 最後一份是本輪的 delta（檔名由殼在本輪產生）
        current = re.search(r"--delta (data/sync_delta_\d{8}_\d{6}\.txt)", lines[2])
        self.assertIsNotNone(current, lines[2])
        self.assertTrue((self.h.root / current.group(1)).is_file(), "本輪 delta 必須保留")

    def test_each_replay_writes_its_own_hashes(self):
        """M14：每份 delta 配自己的 --hashes-out，重放多份時才不會互相覆寫。"""
        _plant_delta(self.h, "sync_delta_20260901_000000.txt", 3600)
        self.h.set_rc(sync_new_reports=2)
        p = self.h.run()
        outs = []
        for ln in _replay_lines(p.stdout):
            m = re.search(r"--delta data/sync_delta_(\S+)\.txt --hashes-out data/sync_hashes_retained_(\S+)\.txt", ln)
            self.assertIsNotNone(m, ln)
            self.assertEqual(m.group(1), m.group(2), "hashes-out 要與 delta 同一個時間戳")
            outs.append(m.group(2))
        self.assertEqual(len(outs), len(set(outs)))

    def test_does_not_suggest_all_local(self):
        """整批中止不留逐篇紀錄；--all-local 是 O(全庫)，也不是這類中止的處置。"""
        for rc in (1, 2):
            with self.subTest(rc=rc):
                h = _SyncHarness()
                self.addCleanup(h.close)
                h.set_rc(sync_new_reports=rc)
                p = h.run()
                self.assertNotRegex(p.stdout, r"sync_new_reports\.py --all-local")
                self.assertTrue(_replay_lines(p.stdout), p.stdout)

    def test_hint_lands_in_unit_failures_record(self):
        """提示要寫在 record_unit_failure 之前，紀錄帶的 log 尾巴裡才看得到。"""
        self.h.set_rc(sync_new_reports=2)
        self.h.run()
        blob = (self.h.root / "data" / "unit_failures.log").read_text(encoding="utf-8")
        self.assertIn("STAGE=sync_new_reports(import)  RC=2", blob)
        self.assertIn("sync_new_reports.py --delta", blob)

    def test_lock_busy_branch_unchanged(self):
        """rc=75 的處置不在本次範圍：仍是原本的提示，不列 delta。"""
        self.h.set_rc(sync_new_reports=LOCK_BUSY_RC)
        p = self.h.run()
        self.assertIn("claude CLI 被另一支批次佔用", p.stdout)
        self.assertEqual(_replay_lines(p.stdout), [])

    def test_import_abort_does_not_retain_hashes(self):
        """匯入段自己中止時 .sync_last_hashes 是上一輪或半途的內容，要重放的是 delta。"""
        self.h.set_rc(sync_new_reports=2)
        self.h.run()
        self.assertEqual(list((self.h.root / "data").glob("sync_hashes_retained_*")), [])


class DownstreamAbortRetainsHashesTests(unittest.TestCase):
    def setUp(self):
        self.h = _SyncHarness()
        self.addCleanup(self.h.close)

    def _retained(self) -> list[Path]:
        return sorted((self.h.root / "data").glob("sync_hashes_retained_*"))

    def test_rc2_retains_hashes_and_prints_replay(self):
        self.h.set_rc(generate_summaries=2)
        p = self.h.run()
        self.assertEqual(p.returncode, 0, "下游仍是 best-effort，不改變 rc")
        files = self._retained()
        self.assertEqual(len(files), 1, p.stdout)
        self.assertRegex(files[0].name, _RETAINED_RE)
        self.assertEqual(
            files[0].read_text(encoding="utf-8"),
            (self.h.root / "data" / ".sync_last_hashes").read_text(encoding="utf-8"),
        )
        rel = f"data/{files[0].name}"
        for script in ("generate_summaries.py", "generate_titles.py", "extract_takeaways.py"):
            with self.subTest(script=script):
                self.assertIn(f"scripts/{script} --hashes-file {rel}", p.stdout)
        blob = (self.h.root / "data" / "unit_failures.log").read_text(encoding="utf-8")
        self.assertIn(rel, blob, "保留位置要落在 unit_failures.log 的紀錄裡")

    def test_later_stage_rc2_also_retains(self):
        """任一段 rc=2 都保留，包括不吃 hashes 的訊號、簡報（三段補跑冪等）。"""
        for stage in ("generate_titles", "extract_takeaways", "extract_signals", "generate_brief"):
            with self.subTest(stage=stage):
                h = _SyncHarness()
                self.addCleanup(h.close)
                h.set_rc(**{stage: 2})
                h.run()
                self.assertEqual(len(list((h.root / "data").glob("sync_hashes_retained_*"))), 1)

    def test_multiple_rc2_retain_once(self):
        self.h.set_rc(generate_summaries=2, extract_signals=2)
        p = self.h.run()
        self.assertEqual(len(self._retained()), 1)
        self.assertIn("已保留於", p.stdout)

    def test_other_nonzero_does_not_retain(self):
        for rc in (1, LOCK_BUSY_RC):
            with self.subTest(rc=rc):
                h = _SyncHarness()
                self.addCleanup(h.close)
                h.set_rc(generate_summaries=rc)
                h.run()
                self.assertEqual(list((h.root / "data").glob("sync_hashes_retained_*")), [])

    def test_no_new_reports_nothing_to_retain(self):
        self.h.new_hashes = []
        self.h._write_fakes()
        self.h.set_rc(extract_signals=2)
        p = self.h.run()
        self.assertEqual(self._retained(), [])
        self.assertIn("沒有 hashes 需要保留", p.stdout)

    def test_retained_copy_survives_next_round(self):
        """保留的意義就在這裡：下一輪 importer 會覆寫 .sync_last_hashes。"""
        self.h.set_rc(generate_summaries=2)
        self.h.run()
        kept = self._retained()[0]
        before = kept.read_text(encoding="utf-8")
        self.h.rcs = {}
        self.h.new_hashes = ["bb" * 32 + "\n"]
        self.h._write_fakes()
        self.h.run()
        self.assertEqual(kept.read_text(encoding="utf-8"), before)
        self.assertNotEqual(
            (self.h.root / "data" / ".sync_last_hashes").read_text(encoding="utf-8"), before
        )

    def test_retained_hashes_are_gitignored(self):
        ignored = subprocess.run(
            ["git", "check-ignore", "data/sync_hashes_retained_20260924_120000.txt"],
            cwd=REPO_ROOT, capture_output=True, text=True,
        )
        self.assertEqual(ignored.returncode, 0, "保留的 hashes 是執行期檔案，必須被 gitignore")


class HashesOutTests(unittest.TestCase):
    """`--hashes-out`（審查 M14）。"""

    def test_default_is_sync_last_hashes(self):
        args = snr.build_parser().parse_args(["--delta", "d.txt"])
        self.assertIsNone(args.hashes_out)
        self.assertEqual(snr.hashes_out_path(args), snr.INGESTED_HASHES_FILE)

    def test_override_path(self):
        args = snr.build_parser().parse_args(
            ["--delta", "d.txt", "--hashes-out", "data/sync_hashes_retained_X.txt"]
        )
        self.assertEqual(snr.hashes_out_path(args), Path("data/sync_hashes_retained_X.txt"))

    def test_namespace_without_attribute_falls_back(self):
        self.assertEqual(snr.hashes_out_path(argparse.Namespace()), snr.INGESTED_HASHES_FILE)

    def test_replays_do_not_overwrite_each_other(self):
        with tempfile.TemporaryDirectory() as d:
            outs = []
            for i, hashes in enumerate((["a1", "a2"], ["b1"])):
                args = snr.build_parser().parse_args(
                    ["--delta", f"d{i}.txt", "--hashes-out", str(Path(d) / f"h{i}.txt")]
                )
                snr.write_ingested_hashes(snr.hashes_out_path(args), hashes)
                outs.append(snr.hashes_out_path(args))
            self.assertEqual(outs[0].read_text(encoding="utf-8").splitlines(), ["a1", "a2"])
            self.assertEqual(outs[1].read_text(encoding="utf-8").splitlines(), ["b1"])

    def test_run_writes_through_hashes_out_path(self):
        """_run 寫 hashes 必須經 hashes_out_path，不可寫死預設檔（靜態釘住：_run 要真 DB）。"""
        src = (REPO_ROOT / "scripts" / "sync_new_reports.py").read_text(encoding="utf-8")
        self.assertIn("write_ingested_hashes(hashes_out_path(args), ingested_hashes)", src)
        self.assertNotIn("write_ingested_hashes(INGESTED_HASHES_FILE", src)


if __name__ == "__main__":
    unittest.main()
