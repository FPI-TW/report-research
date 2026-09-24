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
- 匯入中途整批中止時，importer 把「已 commit 的篇」寫成 `<hashes_out>.partial`，殼改名
  保留成 `data/sync_hashes_retained_<ts>_partial.txt` 並印三段補跑指令——那幾篇重放
  delta 時會變 `skip_exists`，這份是它們跑下游的唯一依據。
- 只列殼自己產生的 `sync_delta_<YYYYMMDD>_<HHMMSS>.txt`，手動做的 delta 不列。

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
_PARTIAL_RE = re.compile(r"^sync_hashes_retained_\d{8}_\d{6}_partial\.txt$")


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
        """rc=75 仍是原本的 --all-local 提示、不列 delta；補一句補完要刪本輪 delta。"""
        self.h.set_rc(sync_new_reports=LOCK_BUSY_RC)
        p = self.h.run()
        self.assertIn("claude CLI 被另一支批次佔用", p.stdout)
        self.assertEqual(_replay_lines(p.stdout), [])
        self.assertRegex(p.stdout, r"補完後刪掉本輪 delta（rm -f data/sync_delta_\d{8}_\d{6}\.txt）")

    def test_manual_deltas_are_not_listed(self):
        """L1：生產上有 sync_delta_recover.txt、sync_delta_rehash_20260917.txt 之類的手動檔，
        它們不是整批中止保留下來的輪次，不可混進重放清單。"""
        _plant_delta(self.h, "sync_delta_recover.txt", 7200)
        _plant_delta(self.h, "sync_delta_rehash_20260917.txt", 5400)
        kept = _plant_delta(self.h, "sync_delta_20260901_000000.txt", 3600)
        self.h.set_rc(sync_new_reports=2)
        p = self.h.run()
        lines = _replay_lines(p.stdout)
        self.assertEqual(len(lines), 2, p.stdout)
        self.assertIn(kept, lines[0])
        self.assertNotIn("recover", p.stdout)
        self.assertNotIn("rehash", p.stdout)
        self.assertIn("刪掉該份 delta", p.stdout)

    def test_rc2_hint_mentions_argument_error(self):
        """L5：argparse 參數錯誤同樣是 rc=2，提示要叫人先看 log；rc=1 不必。"""
        self.h.set_rc(sync_new_reports=2)
        self.assertIn("也可能是參數錯誤", self.h.run().stdout)
        h = _SyncHarness()
        self.addCleanup(h.close)
        h.set_rc(sync_new_reports=1)
        self.assertNotIn("也可能是參數錯誤", h.run().stdout)

    def test_import_abort_does_not_retain_hashes(self):
        """匯入段自己中止時 .sync_last_hashes 是上一輪或半途的內容，要重放的是 delta；
        importer 沒寫 .partial（一篇都還沒入庫）時也沒有東西要保留。"""
        self.h.set_rc(sync_new_reports=2)
        self.h.run()
        self.assertEqual(list((self.h.root / "data").glob("sync_hashes_retained_*")), [])


class ImportAbortRetainsPartialHashesTests(unittest.TestCase):
    """M2：逐篇各自 commit，hashes 卻只在最後寫——中途中止時已入庫的篇要靠 .partial。"""

    def setUp(self):
        self.h = _SyncHarness()
        self.addCleanup(self.h.close)
        self.partial = ["cc" * 32 + "\n", "dd" * 32 + "\n"]

    def _partials(self, h=None) -> list[Path]:
        return sorted(((h or self.h).root / "data").glob("sync_hashes_retained_*_partial.txt"))

    def test_partial_is_retained_with_replay_commands(self):
        for rc in (1, 2):
            with self.subTest(rc=rc):
                h = _SyncHarness()
                self.addCleanup(h.close)
                h.partial_hashes = self.partial
                h.set_rc(sync_new_reports=rc)
                p = h.run()
                files = self._partials(h)
                self.assertEqual(len(files), 1, p.stdout)
                self.assertRegex(files[0].name, _PARTIAL_RE)
                self.assertEqual(files[0].read_text(encoding="utf-8"), "".join(self.partial))
                self.assertFalse((h.root / "data" / ".sync_last_hashes.partial").exists(), "要改名，不留原檔")
                rel = f"data/{files[0].name}"
                for script in ("generate_summaries.py", "generate_titles.py", "extract_takeaways.py"):
                    self.assertIn(f"scripts/{script} --hashes-file {rel}", p.stdout)
                blob = (h.root / "data" / "unit_failures.log").read_text(encoding="utf-8")
                self.assertIn(rel, blob, "保留位置要落在 unit_failures.log 的紀錄裡")

    def test_partial_name_does_not_collide_with_replay_hashes_out(self):
        """照指令重放本輪 delta 會寫 sync_hashes_retained_<ts>.txt；partial 若同名就被蓋掉。"""
        self.h.partial_hashes = self.partial
        self.h.set_rc(sync_new_reports=2)
        p = self.h.run()
        outs = re.findall(r"--hashes-out (data/\S+)", p.stdout)
        self.assertTrue(outs, p.stdout)
        self.assertNotIn(f"data/{self._partials()[0].name}", outs)

    def test_stale_partial_is_removed_before_import(self):
        """殘留的 .partial 不是本輪的：不可被誤認成本輪中止前已入庫的篇。"""
        (self.h.root / "data" / ".sync_last_hashes.partial").write_text("ee" * 32 + "\n", encoding="utf-8")
        self.h.set_rc(sync_new_reports=2)
        self.h.run()
        self.assertEqual(self._partials(), [])

    def test_success_leaves_no_partial(self):
        p = self.h.run()
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertEqual(self._partials(), [])
        self.assertFalse((self.h.root / "data" / ".sync_last_hashes.partial").exists())

    def test_lock_busy_does_not_retain(self):
        """rc=75 時 importer 根本沒跑（在取鎖時就退出），不會有 partial。"""
        self.h.partial_hashes = self.partial
        self.h.set_rc(sync_new_reports=LOCK_BUSY_RC)
        self.h.run()
        self.assertEqual(self._partials(), [])

    def test_partial_hashes_are_gitignored(self):
        for rel in ("data/.sync_last_hashes.partial", "data/sync_hashes_retained_20260924_120000_partial.txt"):
            with self.subTest(rel=rel):
                ignored = subprocess.run(["git", "check-ignore", rel], cwd=REPO_ROOT, capture_output=True, text=True)
                self.assertEqual(ignored.returncode, 0, "執行期檔案必須被 gitignore")


class PartialHashesOnAbortTests(unittest.TestCase):
    """importer 端：`partial_hashes_on_abort` 在中止時寫出已 commit 的 hashes。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.out = Path(self._tmp.name) / ".sync_last_hashes"
        self.partial = snr.partial_hashes_path(self.out)

    def test_partial_path_naming(self):
        self.assertEqual(self.partial.name, ".sync_last_hashes.partial")
        self.assertEqual(
            snr.partial_hashes_path(Path("data/sync_hashes_retained_X.txt")),
            Path("data/sync_hashes_retained_X.txt.partial"),
        )

    def test_abort_writes_hashes_committed_so_far_and_reraises(self):
        """CliNotFoundError（→rc=2）與 DB 例外（→rc=1）都要寫出中止當下的清單。"""
        for exc in (snr.CliNotFoundError("claude 不在 PATH"), RuntimeError("DB 斷線")):
            with self.subTest(exc=type(exc).__name__):
                hashes: list[str] = []
                with self.assertRaises(type(exc)):
                    with snr.partial_hashes_on_abort(self.out, hashes):
                        hashes.append("a1")
                        hashes.append("a2")
                        raise exc
                self.assertEqual(self.partial.read_text(encoding="utf-8").splitlines(), ["a1", "a2"])
                self.assertFalse(self.out.exists(), "中止時不可寫正式的 hashes 檔")
                self.partial.unlink()

    def test_base_exceptions_also_write(self):
        hashes = ["a1"]
        with self.assertRaises(SystemExit):
            with snr.partial_hashes_on_abort(self.out, hashes):
                raise SystemExit(2)
        self.assertTrue(self.partial.exists())

    def test_success_writes_nothing(self):
        hashes: list[str] = []
        with snr.partial_hashes_on_abort(self.out, hashes):
            hashes.append("a1")
        self.assertFalse(self.partial.exists())

    def test_nothing_committed_or_dry_run_writes_nothing(self):
        for hashes, enabled in (([], True), (["a1"], False)):
            with self.subTest(hashes=hashes, enabled=enabled):
                with self.assertRaises(RuntimeError):
                    with snr.partial_hashes_on_abort(self.out, hashes, enabled=enabled):
                        raise RuntimeError("x")
                self.assertFalse(self.partial.exists())

    def test_write_failure_does_not_mask_original(self):
        blocker = Path(self._tmp.name) / "not_a_dir"
        blocker.write_text("", encoding="utf-8")
        with self.assertRaises(RuntimeError):
            with snr.partial_hashes_on_abort(blocker / "h.txt", ["a1"]):
                raise RuntimeError("原本的例外")

    def test_run_wraps_ingest_loop(self):
        """_run 要真 DB，靜態釘住：逐篇迴圈必須包在 guard 內、共用同一個 ingested_hashes。"""
        src = (REPO_ROOT / "scripts" / "sync_new_reports.py").read_text(encoding="utf-8")
        guard = src.index("with partial_hashes_on_abort(hashes_out_path(args), ingested_hashes")
        loop = src.index("async with SessionFactory() as session:")
        final = src.index("write_ingested_hashes(hashes_out_path(args), ingested_hashes)")
        self.assertLess(guard, loop)
        self.assertLess(loop, final)


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
