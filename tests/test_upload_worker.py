"""上傳 worker 不需要 DB 的部分（app/services/upload_worker.py、scripts/process_uploads.py 的閘門）。

整輪鎖（含殼 → Python 的 fd 繼承）、檔名與路徑、搬正、backfill 偵測、webhook（URL 不進 argv）、
`ingest` 子命令在碰 LLM 之前的每一道閘（沒有待處理、backfill、斷路器、撞鎖、DB 不可用、LLM 環境錯誤）。
狀態轉移全集與草稿原子性在 tests/test_upload_worker_db.py（真 PostgreSQL）。全程只碰 tempfile。
"""

from __future__ import annotations

import argparse
import asyncio
import errno
import fcntl
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from app.services import upload_worker as uw
from scripts import process_uploads as pu

REPO_ROOT = Path(__file__).resolve().parents[1]
HASH = "ab" * 32


class _Tmp(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        patcher = mock.patch.dict(os.environ)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop(uw.LOCK_FD_ENV, None)


# ── 路徑 ────────────────────────────────────────────────────────────────


class PathTests(_Tmp):
    def test_clean_file_keeps_original_name_under_hash_dir(self):
        """parse_filename 靠原始檔名推券商、日期、行政文件，所以乾淨檔保留原名（一個 hash 一個目錄）。"""
        name = "20260901_券商甲_台積電(2330)研究報告.pdf"
        self.assertEqual(uw.clean_file(self.dir, HASH, name), self.dir / HASH / name)
        with self.assertRaises(ValueError):
            uw.clean_file(self.dir, "../etc", name)

    def test_fs_name_fits_ext4_byte_limit(self):
        long_name = "研" * 250 + ".pdf"  # 254 字、754 bytes
        out = uw.fs_name(long_name)
        self.assertLessEqual(len(out.encode("utf-8")), 255)
        self.assertTrue(out.endswith(".pdf"))
        self.assertTrue(out.startswith("研研"))
        self.assertEqual(uw.fs_name("a/b\\c.pdf"), "a_b_c.pdf")
        self.assertEqual(uw.fs_name(".."), "upload.pdf")

    def test_defaults_live_under_repo_data_and_conftest_redirects_them(self):
        self.assertEqual(uw.DEFAULT_LOCK_FILE, REPO_ROOT / "data" / ".upload_worker.lock")
        self.assertEqual(uw.DEFAULT_CLEAN_DIR, REPO_ROOT / "data" / "uploads" / "clean")
        from app.config import get_settings

        self.assertTrue(str(uw.lock_file(get_settings())).startswith("/nonexistent/"))
        self.assertTrue(str(uw.clean_root(get_settings())).startswith("/nonexistent/"))

    def test_write_hashes_is_atomic_and_empty_is_zero_bytes(self):
        out = self.dir / "sub" / "hashes"
        uw.write_hashes(out, [])
        self.assertEqual(out.read_bytes(), b"")
        uw.write_hashes(out, ["a", "b"])
        self.assertEqual(out.read_text(), "a\nb")
        self.assertEqual(sorted(p.name for p in out.parent.iterdir()), ["hashes"])


# ── 整輪鎖 ──────────────────────────────────────────────────────────────


class RoundLockTests(_Tmp):
    def setUp(self):
        super().setUp()
        self.lock = self.dir / "data" / ".upload_worker.lock"

    def test_second_holder_is_refused_and_lock_released_after(self):
        with uw.round_lock(self.lock) as first:
            self.assertTrue(first)
            with uw.round_lock(self.lock) as second:
                self.assertFalse(second)
        with uw.round_lock(self.lock) as again:
            self.assertTrue(again)

    def test_holder_payload_for_ops_probe(self):
        with uw.round_lock(self.lock, owner="process_uploads"):
            self.assertIn('"script": "process_uploads"', self.lock.read_text())
        self.assertEqual(self.lock.read_text(), "")

    def test_inherited_fd_proves_it_holds_the_lock_and_never_releases_it(self):
        self.lock.parent.mkdir(parents=True)
        fd = os.open(self.lock, os.O_RDWR | os.O_CREAT, 0o644)
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)  # 殼取的
        os.environ[uw.LOCK_FD_ENV] = str(fd)
        with uw.round_lock(self.lock) as held:
            self.assertTrue(held)
        # 子命令結束後鎖仍屬於殼：另一個 open file description 取不到
        os.environ.pop(uw.LOCK_FD_ENV)
        with uw.round_lock(self.lock) as other:
            self.assertFalse(other)

    def test_inherited_fd_not_holding_the_lock_is_refused(self):
        self.lock.parent.mkdir(parents=True)
        holder = os.open(self.lock, os.O_RDWR | os.O_CREAT, 0o644)
        self.addCleanup(os.close, holder)
        fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)  # 別人持有
        mine = os.open(self.lock, os.O_RDWR)
        self.addCleanup(os.close, mine)
        os.environ[uw.LOCK_FD_ENV] = str(mine)
        with uw.round_lock(self.lock) as held:
            self.assertFalse(held, "不盲信環境變數：fd 沒持有鎖就是沒持有")

    def test_inherited_fd_must_point_at_the_lock_file(self):
        self.lock.parent.mkdir(parents=True)
        self.lock.touch()
        other = os.open(self.dir / "other", os.O_RDWR | os.O_CREAT, 0o644)
        self.addCleanup(os.close, other)
        os.environ[uw.LOCK_FD_ENV] = str(other)
        with self.assertRaises(RuntimeError):
            with uw.round_lock(self.lock):
                pass

    @unittest.skipUnless(shutil.which("flock"), "需要 util-linux 的 flock")
    def test_shell_flock_is_inherited_by_python(self):
        """殼的寫法（`exec 9>>鎖; flock -n 9; export UPLOAD_WORKER_LOCK_FD=9`）真的讓 Python 認得鎖。"""
        self.lock.parent.mkdir(parents=True)
        code = (
            "import sys; from pathlib import Path; from app.services import upload_worker as uw\n"
            "with uw.round_lock(Path(sys.argv[1])) as held: print('held' if held else 'busy')\n"
        )
        script = (
            f'exec 9>>"$1"; flock -n 9 || exit 9; export {uw.LOCK_FD_ENV}=9; '
            f'"$2" -c "$3" "$1"; "$2" -c "$3" "$1"'
        )
        env = {k: v for k, v in os.environ.items() if k != uw.LOCK_FD_ENV}
        out = subprocess.run(["bash", "-c", script, "x", str(self.lock), sys.executable, code], cwd=REPO_ROOT,
                             env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.split(), ["held", "held"], "兩個子命令都在同一輪的鎖底下")
        # 殼持鎖時，不在這一輪裡的行程（沒有繼承 fd）取不到
        script_busy = 'exec 9>>"$1"; flock -n 9 || exit 9; "$2" -c "$3" "$1"'
        env_busy = dict(env)
        out = subprocess.run(["bash", "-c", script_busy, "x", str(self.lock), sys.executable, code],
                             cwd=REPO_ROOT, env=env_busy, capture_output=True, text=True, timeout=60)
        self.assertEqual(out.stdout.split(), ["busy"])


# ── 搬正 ────────────────────────────────────────────────────────────────


class MoveToCleanTests(_Tmp):
    def setUp(self):
        super().setUp()
        self.src = self.dir / "q" / "id.bin"
        self.src.parent.mkdir()
        self.src.write_bytes(b"%PDF-1.7 content %%EOF")
        self.sha = uw.sha256_file(self.src)
        self.dst = self.dir / "clean" / self.sha / "原始檔名.pdf"

    def test_moves_sets_mode_and_client_mtime(self):
        when = datetime(2026, 9, 1, 8, 30, tzinfo=timezone.utc)
        uw.move_to_clean(self.src, self.dst, self.sha, when)
        self.assertFalse(self.src.exists())
        self.assertEqual(uw.sha256_file(self.dst), self.sha)
        self.assertEqual(stat.S_IMODE(self.dst.stat().st_mode), uw.CLEAN_FILE_MODE)
        self.assertEqual(int(self.dst.stat().st_mtime), int(when.timestamp()))

    def test_hash_mismatch_before_move_leaves_source(self):
        with self.assertRaises(uw.HashMismatchError):
            uw.move_to_clean(self.src, self.dst, "0" * 64, None)
        self.assertTrue(self.src.exists())
        self.assertFalse(self.dst.exists())

    def test_cross_filesystem_falls_back_to_verified_copy(self):
        real = os.replace
        calls = []

        def replace(a, b):
            calls.append((a, b))
            if len(calls) == 1:
                raise OSError(errno.EXDEV, "Invalid cross-device link")
            return real(a, b)

        with mock.patch.object(uw.os, "replace", replace):
            uw.move_to_clean(self.src, self.dst, self.sha, None)
        self.assertFalse(self.src.exists())
        self.assertEqual(uw.sha256_file(self.dst), self.sha)
        self.assertEqual([p.name for p in self.dst.parent.iterdir()], [self.dst.name], "暫存檔不得留下")


# ── backfill 偵測 ────────────────────────────────────────────────────────


class BackfillProbeTests(_Tmp):
    def _proc(self, procs: dict[str, list[str]]) -> Path:
        root = self.dir / "proc"
        for pid, argv in procs.items():
            (root / pid).mkdir(parents=True)
            (root / pid / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + b"\0")
        (root / "self").mkdir(parents=True, exist_ok=True)
        return root

    def _runner(self, state: str):
        return lambda argv, **kw: subprocess.CompletedProcess(argv, 0, stdout=state + "\n", stderr="")

    def test_detects_manual_and_uv_wrapped_runs(self):
        for argv in (
            ["/usr/bin/python3", "scripts/backfill_extraction.py", "--max-minutes", "240"],
            ["/home/x/.local/bin/uv", "run", "python", "/srv/rm/scripts/backfill_extraction.py"],
        ):
            with self.subTest(argv=argv):
                root = self._proc({"4242": argv})
                why = uw.backfill_running(proc_root=root, systemctl=None)
                self.assertIn("4242", why)
                shutil.rmtree(root)

    def test_unit_state_counts(self):
        root = self._proc({"10": ["/usr/bin/python3", "scripts/process_uploads.py", "ingest"]})
        for state, running in (("activating", True), ("active", True), ("inactive", False), ("failed", False)):
            with self.subTest(state=state):
                why = uw.backfill_running(proc_root=root, runner=self._runner(state))
                self.assertEqual(bool(why), running)

    def test_probe_failures_mean_not_running(self):
        def boom(argv, **kw):
            raise FileNotFoundError("systemctl")

        self.assertIsNone(uw.backfill_running(proc_root=self.dir / "missing", runner=boom))

    def test_other_scripts_do_not_match(self):
        root = self._proc({"11": ["python", "scripts/backfill_extraction_report.py"],
                           "12": ["vim", "notes_backfill_extraction.py.txt"]})
        self.assertIsNone(uw.backfill_running(proc_root=root, systemctl=None))


# ── 通知 ────────────────────────────────────────────────────────────────


class NotifyTests(_Tmp):
    URL = "https://hooks.example.invalid/fixed-test-secret-webhook5150"

    def test_webhook_url_goes_through_stdin_not_argv(self):
        seen = {}

        def runner(argv, **kw):
            seen["argv"], seen["input"] = argv, kw.get("input")
            return subprocess.CompletedProcess(argv, 0, "", "")

        self.assertEqual(uw.send_webhook("hello", url=self.URL, runner=runner), "sent")
        self.assertNotIn(self.URL, " ".join(seen["argv"]))
        self.assertIn(self.URL, seen["input"])
        self.assertIn("-K", seen["argv"])

    def test_skipped_bad_url_and_failure_never_echo_the_url(self):
        calls = []
        runner = lambda argv, **kw: calls.append(argv) or subprocess.CompletedProcess(argv, 22, "", "")  # noqa: E731
        self.assertEqual(uw.send_webhook("x", url="", runner=runner), "skipped")
        err = _capture_stderr(lambda: self.assertEqual(
            uw.send_webhook("x", url='https://a/"injected', runner=runner), "bad_url"))
        self.assertEqual(calls, [], "不合法的 URL 不送（引號會讓 curl 設定檔被注入）")
        self.assertNotIn("injected", err)
        err = _capture_stderr(lambda: self.assertEqual(uw.send_webhook("x", url=self.URL, runner=runner), "failed:22"))
        self.assertIn("curl_rc=22", err)
        self.assertNotIn(self.URL, err)

    def test_reads_env_when_url_not_given(self):
        runner = mock.Mock(return_value=subprocess.CompletedProcess([], 0, "", ""))
        self.assertEqual(uw.send_webhook("x", runner=runner), "skipped")  # conftest 已移除這個鍵
        os.environ["REPORT_MARK_ALERT_WEBHOOK"] = self.URL
        self.assertEqual(uw.send_webhook("x", runner=runner), "sent")

    def test_journal_priority_prefix_only_under_systemd(self):
        os.environ.pop("JOURNAL_STREAM", None)
        self.assertTrue(_capture_stderr(lambda: uw.journal_error("x")).startswith("ERROR "))
        os.environ["JOURNAL_STREAM"] = "8:123"
        self.assertTrue(_capture_stderr(lambda: uw.journal_error("x")).startswith("<3>"))

    def test_notify_infected_logs_and_sends(self):
        with mock.patch.object(uw, "send_webhook") as send:
            err = _capture_stderr(lambda: uw.notify_infected(uw.InfectionNotice("uid-1", "Eicar-Signature", False)))
        self.assertIn("uid-1", err)
        self.assertIn("Eicar-Signature", err)
        self.assertIn("uid-1", send.call_args[0][0])


def _capture_stderr(fn) -> str:
    import contextlib
    import io

    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        fn()
    return buf.getvalue()


class DeterministicCountTests(unittest.TestCase):
    def test_parses_previous_label(self):
        self.assertEqual(uw._deterministic_count(None), 0)
        self.assertEqual(uw._deterministic_count("timeout: x"), 0)
        self.assertEqual(uw._deterministic_count("[決定性 2/3] size_limit: x"), 2)


# ── 孤兒檔 ──────────────────────────────────────────────────────────────


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeSession:
    def __init__(self, known):
        self.known = known

    async def execute(self, stmt, params=None):
        return _FakeResult([(i,) for i in (params or {}).get("ids", []) if i in self.known])

    async def rollback(self):
        pass


def _factory(session):
    @asynccontextmanager
    async def factory():
        yield session

    return factory


class OrphanTests(_Tmp):
    def test_only_old_files_without_db_rows_are_removed(self):
        q = self.dir / "quarantine"
        (q / "incoming").mkdir(parents=True)
        (q / "infected").mkdir()
        known, orphan, part, young = (
            "11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222",
            "33333333-3333-4333-8333-333333333333", "44444444-4444-4444-8444-444444444444",
        )
        files = {
            "known": q / f"{known}.bin", "orphan": q / f"{orphan}.bin", "part": q / "incoming" / f"{part}.part",
            "young": q / f"{young}.bin", "junk": q / "notes.txt", "infected": q / "infected" / f"{orphan}.bin",
        }
        for path in files.values():
            path.write_bytes(b"x")
        old = 10_000.0
        for key, path in files.items():
            if key != "young":
                os.utime(path, (old, old))
        now = old + uw.ORPHAN_MIN_AGE_SECONDS + 1
        os.utime(files["young"], (now - 10, now - 10))
        n = asyncio.run(uw.purge_orphans(_factory(_FakeSession({known})), qroot=q, now=now))
        self.assertEqual(n, 4)
        self.assertTrue(files["known"].exists())
        self.assertTrue(files["young"].exists())
        for key in ("orphan", "part", "junk", "infected"):
            self.assertFalse(files[key].exists(), key)
        self.assertTrue((q / "incoming").is_dir() and (q / "infected").is_dir(), "目錄本身不刪")


# ── ingest 子命令的閘門（DB 與 LLM 都是假的）──────────────────────────────


class _Dummy:
    async def rollback(self):
        pass

    async def commit(self):
        pass


class IngestGateTests(_Tmp):
    def setUp(self):
        super().setUp()
        self.hashes = self.dir / "hashes"
        self.claude_lock = self.dir / "claude.lock"
        self.ctx = pu.Ctx(
            session_factory=_factory(_Dummy()), qroot=self.dir / "q", clean_dir=self.dir / "clean",
            lock_path=self.dir / "round.lock", claude_lock_path=self.claude_lock, backfill_probe=lambda: None,
        )
        self.calls: list[str] = []
        self.pending = 2

        async def recover(session):
            self.calls.append("recover")
            return uw.Recovery()

        async def count(session):
            return self.pending

        async def defer(session, *, detail):
            self.calls.append("defer")
            return self.pending

        for name, fn in (("recover_stale", recover), ("count_clean", count), ("defer_all_clean", defer)):
            p = mock.patch.object(uw, name, fn)
            p.start()
            self.addCleanup(p.stop)
        self.require = mock.patch.object(pu, "require_llm_key", side_effect=lambda m: self.calls.append("require"))
        self.require.start()
        self.addCleanup(self.require.stop)
        self.breaker = mock.patch.object(pu, "breaker_active", return_value=None)
        self.breaker.start()
        self.addCleanup(self.breaker.stop)

        async def fake_round(ctx, *, limit, hashes):
            self.calls.append("ingest")
            hashes.append(HASH)
            return pu.IngestStats(claimed=1, drafts=1)

        self.round = mock.patch.object(pu, "ingest_round", fake_round)
        self.round.start()
        self.addCleanup(self.round.stop)

    def run_ingest(self):
        return pu.cmd_ingest(argparse.Namespace(limit=5, hashes_out=str(self.hashes)), self.ctx)

    def test_happy_path_writes_hashes(self):
        self.assertEqual(self.run_ingest(), 0)
        self.assertEqual(self.calls, ["recover", "require", "ingest"])
        self.assertEqual(self.hashes.read_text(), HASH)

    def test_nothing_pending_touches_no_llm(self):
        self.pending = 0
        self.assertEqual(self.run_ingest(), 0)
        self.assertEqual(self.calls, ["recover"])
        self.assertEqual(self.hashes.read_bytes(), b"")

    def test_backfill_running_only_scans(self):
        self.ctx.backfill_probe = lambda: "pid=1 正在跑 backfill_extraction.py"
        self.assertEqual(self.run_ingest(), 0)
        self.assertEqual(self.calls, ["recover"], "不預檢、不取 claude 鎖、不入庫")
        self.assertEqual(self.hashes.read_bytes(), b"")

    def test_breaker_defers_instead_of_failing(self):
        self.breaker.stop()
        self.breaker = mock.patch.object(pu, "breaker_active", return_value="ts=… reason=逾時")
        self.breaker.start()
        self.assertEqual(self.run_ingest(), 0)
        self.assertEqual(self.calls, ["recover", "defer"])

    def test_claude_lock_busy_exits_75_and_still_writes_hashes(self):
        fd = os.open(self.claude_lock, os.O_RDWR | os.O_CREAT, 0o644)
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with self.assertRaises(SystemExit) as cm:
            _capture_stderr(self.run_ingest)
        self.assertEqual(cm.exception.code, 75)
        self.assertNotIn("ingest", self.calls)
        self.assertEqual(self.hashes.read_bytes(), b"")

    def test_require_llm_key_runs_before_lock_and_aborts_with_2(self):
        self.require.stop()
        self.require = mock.patch.object(pu, "require_llm_key", side_effect=SystemExit(2))
        self.require.start()
        fd = os.open(self.claude_lock, os.O_RDWR | os.O_CREAT, 0o644)  # 鎖也被佔：仍要先說缺金鑰
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with self.assertRaises(SystemExit) as cm:
            self.run_ingest()
        self.assertEqual(cm.exception.code, 2)
        self.assertTrue(self.hashes.exists())

    def test_round_lock_busy_is_rc0_and_does_nothing(self):
        self.hashes.write_text("正在跑的那一輪的 hashes")
        with uw.round_lock(self.ctx.lock_path):
            self.assertEqual(self.run_ingest(), 0)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.hashes.read_text(), "正在跑的那一輪的 hashes", "沒持鎖不得覆寫別輪的 hashes")

    def test_db_unavailable_is_rc2(self):
        async def down(session):
            raise ConnectionRefusedError(111, "Connect call failed")

        with mock.patch.object(uw, "recover_stale", down):
            self.assertEqual(self.run_ingest(), 2)

    def test_llm_environment_error_is_rc2_with_committed_hashes(self):
        from scripts._claude_cli import LlmEnvironmentError

        async def broken(ctx, *, limit, hashes):
            hashes.append(HASH)
            raise LlmEnvironmentError("API[auth] 401")

        with mock.patch.object(pu, "ingest_round", broken):
            self.assertEqual(self.run_ingest(), 2)
        self.assertEqual(self.hashes.read_text(), HASH, "已 commit 的那幾篇照樣交給下游")

    def test_unexpected_error_propagates_as_failure(self):
        async def bug(ctx, *, limit, hashes):
            raise KeyError("bug")

        with mock.patch.object(pu, "ingest_round", bug):
            with self.assertRaises(KeyError):
                self.run_ingest()


class SubcommandLockTests(_Tmp):
    def test_scan_and_cleanup_respect_round_lock(self):
        ctx = pu.Ctx(session_factory=None, qroot=self.dir, clean_dir=self.dir, lock_path=self.dir / "round.lock")
        with uw.round_lock(ctx.lock_path):
            self.assertEqual(pu.cmd_scan(argparse.Namespace(limit=5), ctx), 0)
            self.assertEqual(pu.cmd_cleanup(argparse.Namespace(), ctx), 0)

    def test_parser(self):
        args = pu.build_parser().parse_args(["ingest", "--hashes-out", "x"])
        self.assertEqual((args.cmd, args.hashes_out, args.limit), ("ingest", "x", pu.INGEST_BATCH))
        self.assertEqual(pu.build_parser().parse_args(["scan"]).limit, uw.SCAN_BATCH)


if __name__ == "__main__":
    unittest.main()
