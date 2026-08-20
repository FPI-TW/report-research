# tests/test_pipeline_heartbeat.py
"""管線心跳（`data/.last_successful_sync`）與 `check_batch_freshness.py` 的上游狀態。

存在理由是一個**只靠既有訊號完全看不見**的失效組合：

1. `sync_new_reports.sh` 有一條 rc=0 的早退路徑——PID lock 被佔用時 `exit 0`。
2. 下游六段刻意 best-effort：失敗只 `log` 不 `exit`（那個設計是對的，摘要失敗不該
   擋住下一輪匯入）。

兩者合起來的後果是整條管線可以連續數天完全沒有成功跑完，而 systemd 全程正常——
2026-08-12 的事故正是這個形狀（所有 claude 批次連續 4 天 100% 失敗、入庫歸零，
unit 全綠，症狀長得像「NAS 沒有新檔」）。

心跳量的是**管線執行新鮮度**，`check_batch_freshness.py` 原有的四個 `max(created_at)`
量的是**資料新鮮度**。兩者不可互相取代：0 篇新研報只要跑完就更新心跳（週末不誤報），
而資料停更也可能發生在管線一切正常時（模型持續回空）。

殼那一側刻意用**假二進位跑真腳本**而不是靜態比對：心跳的正確性全在「哪些路徑會走到
那一行」，而那正是靜態比對看不到的東西。
"""
import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SYNC = REPO_ROOT / "scripts" / "sync_new_reports.sh"
FRESHNESS = REPO_ROOT / "scripts" / "check_batch_freshness.py"
FRESHNESS_UNIT = REPO_ROOT / "deploy" / "systemd" / "report-mark-freshness.service"

sys.path.insert(0, str(REPO_ROOT / "scripts"))
import check_batch_freshness as cbf  # noqa: E402

HEARTBEAT_REL = "data/.last_successful_sync"
LOCK_BUSY_RC = 75


class _SyncHarness:
    """把真的 `sync_new_reports.sh` 放進一個假 repo root，用假二進位控制每一段的 rc。"""

    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "scripts").mkdir()
        (self.root / "data").mkdir()
        (self.root / "研報自動匯入").mkdir()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        shutil.copy2(SYNC, self.root / "scripts" / "sync_new_reports.sh")
        self.rc_file = self.root / "rcs"
        self.delta_file = self.root / "delta_out"
        self.hashes_file = self.root / "hashes_out"
        self.rsync_rc = 0
        self.rcs: dict[str, int] = {}
        self.delta = ["某券商/報告A.pdf\n"]
        self.new_hashes = ["aa" * 32 + "\n"]
        self._write_fakes()

    def _fake(self, name: str, body: str):
        f = self.bin / name
        f.write_text("#!/usr/bin/env bash\n" + body, encoding="utf-8")
        f.chmod(0o755)

    def _write_fakes(self):
        self.rc_file.write_text(
            "".join(f"{k}={v}\n" for k, v in self.rcs.items()), encoding="utf-8"
        )
        self.delta_file.write_text("".join(self.delta), encoding="utf-8")
        self.hashes_file.write_text("".join(self.new_hashes), encoding="utf-8")
        # 掛載一律視為已掛好——本檔測的是心跳，不是掛載偵測
        self._fake("mountpoint", "exit 0\n")
        self._fake("sudo", "exit 0\n")
        self._fake("rsync", f'cat "{self.delta_file}"\nexit {self.rsync_rc}\n')
        # uv：從參數裡認出被呼叫的腳本，查表決定 rc；匯入段順便寫出本輪 hashes
        self._fake(
            "uv",
            'S=""\n'
            'for a in "$@"; do case "$a" in scripts/*.py) S="$a" ;; esac; done\n'
            'N=$(basename "${S:-none}" .py)\n'
            f'if [ "$N" = sync_new_reports ]; then cat "{self.hashes_file}" > data/.sync_last_hashes; fi\n'
            f'RC=$(grep "^$N=" "{self.rc_file}" 2>/dev/null | head -1 | cut -d= -f2)\n'
            'exit "${RC:-0}"\n',
        )

    def set_rc(self, **kw):
        self.rcs.update(kw)
        self._write_fakes()

    def run(self, **env_extra):
        env = dict(os.environ)
        env["PATH"] = f"{self.bin}:{env['PATH']}"
        # **`UV` 必須明確指定，光靠 PATH 注入會被繞過。**
        # 腳本寫的是 `UV="${UV:-}"`，而 `uv run pytest` 會把 UV= 匯出到子環境，
        # 於是它拿到的是真的 uv、完全不看 PATH。第一版就是這樣：假二進位全被繞過，
        # 匯入段以 rc=2 失敗（真 uv 找不到暫存 root 裡的 .py），而症狀看起來像
        # 「心跳沒寫」。環境變數優先於 PATH 是這類 harness 的固定陷阱。
        env["UV"] = str(self.bin / "uv")
        env.update(env_extra)
        return subprocess.run(
            ["bash", "scripts/sync_new_reports.sh"],
            cwd=self.root, capture_output=True, text=True, env=env, timeout=120,
        )

    def run_and_kill(self, after_seconds: float, sig=signal.SIGTERM):
        """讓 rsync 卡住，在中途送訊號——模擬補跑被關機收掉。

        **這與 `set_rc(rsync=1)` 不是同一件事。** 那個測的是「rsync 回報失敗」，
        腳本會走到 `exit 1`；這個測的是「腳本連走都沒走完就被砍」，兩者對心跳的
        影響必須分別驗證，否則「因為沒走到那一行所以沒寫」只是推論而不是事實。
        """
        self._fake("rsync", 'sleep 60\nexit 0\n')
        env = dict(os.environ)
        env["PATH"] = f"{self.bin}:{env['PATH']}"
        env["UV"] = str(self.bin / "uv")
        proc = subprocess.Popen(
            ["bash", "scripts/sync_new_reports.sh"],
            cwd=self.root, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, env=env, start_new_session=True,
        )
        time.sleep(after_seconds)
        os.killpg(os.getpgid(proc.pid), sig)
        try:
            proc.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
        return proc.returncode

    @property
    def heartbeat(self) -> Path:
        return self.root / HEARTBEAT_REL

    def heartbeat_fields(self) -> dict:
        if not self.heartbeat.is_file():
            return {}
        return dict(
            ln.split("=", 1)
            for ln in self.heartbeat.read_text(encoding="utf-8").splitlines()
            if "=" in ln
        )

    def close(self):
        self.tmp.cleanup()


class HeartbeatWriteTests(unittest.TestCase):
    """哪些路徑該更新心跳、哪些絕對不能。"""

    def setUp(self):
        self.h = _SyncHarness()
        self.addCleanup(self.h.close)

    def test_successful_sync_updates_heartbeat(self):
        p = self.h.run()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("=== sync done ===", p.stdout)
        f = self.h.heartbeat_fields()
        self.assertTrue(f, "完整成功卻沒有心跳")
        self.assertTrue(f["epoch"].isdigit())
        self.assertAlmostEqual(int(f["epoch"]), int(time.time()), delta=90)

    def test_zero_new_reports_still_updates_heartbeat(self):
        """**週末不得誤報。** 心跳量的是管線跑完，不是有沒有新資料。"""
        self.h.delta = []
        self.h.new_hashes = []
        self.h._write_fakes()
        p = self.h.run()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("本次無新研報入庫", p.stdout)
        self.assertEqual(self.h.heartbeat_fields().get("new_reports"), "0")

    def test_pid_lock_skip_does_not_update(self):
        """撞鎖跳過是 rc=0，最容易被誤當成成功——它必須不留心跳。"""
        (self.h.root / "data" / ".sync_new_reports.lock").write_text(str(os.getpid()))
        p = self.h.run()
        self.assertEqual(p.returncode, 0)
        self.assertIn("本次跳過", p.stdout)
        self.assertFalse(self.h.heartbeat.exists(), "撞鎖跳過不得更新心跳")

    def test_rsync_failure_does_not_update(self):
        self.h.rsync_rc = 23
        self.h._write_fakes()
        p = self.h.run()
        self.assertNotEqual(p.returncode, 0)
        self.assertFalse(self.h.heartbeat.exists())

    def test_import_failure_does_not_update(self):
        self.h.set_rc(sync_new_reports=1)
        p = self.h.run()
        self.assertNotEqual(p.returncode, 0)
        self.assertFalse(self.h.heartbeat.exists())

    def test_downstream_abnormal_failure_does_not_update(self):
        """下游失敗仍然不擋 sync（rc=0、仍印 sync done），但**不留心跳**。

        這正是 2026-08-12 那類事故的形狀：管線每輪都「成功」結束而實際什麼都沒產出。
        """
        self.h.set_rc(generate_summaries=1)
        p = self.h.run()
        self.assertEqual(p.returncode, 0, "下游失敗不得改變 best-effort 語意")
        self.assertIn("=== sync done ===", p.stdout)
        self.assertIn("不更新心跳", p.stdout)
        self.assertFalse(self.h.heartbeat.exists())

    def test_claude_lock_busy_downstream_still_updates(self):
        """rc=75 是 EX_TEMPFAIL（claude CLI 被別的批次佔用），**不是異常**。

        把它算成異常會讓每次批次撞鎖都抑制心跳——而撞鎖在本 repo 是常態。
        """
        self.h.set_rc(extract_signals=LOCK_BUSY_RC)
        p = self.h.run()
        self.assertEqual(p.returncode, 0)
        self.assertTrue(self.h.heartbeat.exists(), "rc=75 不得抑制心跳")

    def test_mixed_lock_busy_and_real_failure_does_not_update(self):
        self.h.set_rc(extract_signals=LOCK_BUSY_RC, generate_brief=1)
        self.h.run()
        self.assertFalse(self.h.heartbeat.exists())

    def test_write_is_atomic_and_leaves_no_temp(self):
        self.h.run()
        leftovers = list((self.h.root / "data").glob(".last_successful_sync.tmp*"))
        self.assertEqual(leftovers, [], f"殘留暫存檔: {leftovers}")

    def test_write_uses_temp_then_rename(self):
        body = "\n".join(
            ln for ln in SYNC.read_text(encoding="utf-8").splitlines()
            if not ln.strip().startswith("#")
        )
        self.assertRegex(body, r'HEARTBEAT\}?\.tmp', "必須先寫暫存檔")
        self.assertIn("mv -f", body, "必須用 rename 而非直接覆寫")
        self.assertRegex(body, r"sync -f", "rename 前必須 fsync，否則可能落地半寫檔")

    def test_second_run_advances_heartbeat(self):
        self.h.run()
        first = int(self.h.heartbeat_fields()["epoch"])
        time.sleep(1.1)
        self.h.run()
        self.assertGreater(int(self.h.heartbeat_fields()["epoch"]), first)

    def test_heartbeat_holds_no_secret_and_is_not_world_writable(self):
        self.h.run()
        text = self.h.heartbeat.read_text(encoding="utf-8")
        for bad in ("PASSWORD", "SECRET", "TOKEN", "postgresql://", "sk-"):
            self.assertNotIn(bad, text, f"心跳不得含 {bad}")
        self.assertEqual(set(self.h.heartbeat_fields()) , {"ts", "epoch", "new_reports", "pid"})
        mode = stat.S_IMODE(self.h.heartbeat.stat().st_mode)
        self.assertEqual(mode & stat.S_IWOTH, 0, "心跳不得 world-writable")
        self.assertTrue(mode & stat.S_IRUSR, "心跳必須可讀，否則偵測器讀不到")

    def test_harness_actually_controls_uv(self):
        """守住上面那個陷阱：若 harness 沒有蓋掉 UV，整組殼測試會靜默測到真的 uv。"""
        p = self.h.run()
        log = next((self.h.root / "data").glob("sync_run_*.log")).read_text(encoding="utf-8")
        self.assertNotIn("/.local/bin/uv", log, "腳本用到了真的 uv，假二進位被繞過")
        self.assertNotIn("No such file or directory", log)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_heartbeat_is_gitignored(self):
        ignored = subprocess.run(
            ["git", "check-ignore", HEARTBEAT_REL],
            cwd=REPO_ROOT, capture_output=True, text=True,
        )
        self.assertEqual(ignored.returncode, 0, "心跳是執行期狀態，必須被 gitignore")


class InterruptedSyncTests(unittest.TestCase):
    """被關機收掉的那一輪不得留下「成功」的痕跡。

    這是 `Persistent=false` 的另一半理由：補跑在開機後數秒啟動、常在 25 秒內被砍，
    而那個當下 `OnFailure=` 也送不出去（systemd 拒絕把 alert 排進含 stop job 的
    transaction）。若心跳還前進，就等於「靜默失敗 ＋ 看起來成功」——最壞的組合。
    """

    def setUp(self):
        self.h = _SyncHarness()
        self.addCleanup(self.h.close)

    def test_sigterm_during_rsync_leaves_no_heartbeat(self):
        self.h.run_and_kill(1.5)
        self.assertFalse(self.h.heartbeat.is_file(), "被砍的一輪不得寫心跳")

    def test_sigkill_during_rsync_leaves_no_heartbeat(self):
        """SIGKILL 連 trap 都不會跑——心跳仍不得出現。"""
        self.h.run_and_kill(1.5, sig=signal.SIGKILL)
        self.assertFalse(self.h.heartbeat.is_file())

    def test_previous_heartbeat_is_not_advanced_by_an_interrupted_round(self):
        first = self.h.run()
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        before = self.h.heartbeat_fields()["epoch"]
        self.h.run_and_kill(1.5)
        self.assertEqual(
            self.h.heartbeat_fields()["epoch"], before,
            "被中斷的一輪不得覆寫上一次成功的時間戳",
        )


class PipelineFreshnessTests(unittest.TestCase):
    """`assess_pipeline()` 與 `exit_code()`——純函式，不需 DB。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.hb = Path(self.tmp.name) / "hb"
        # 對齊到整秒：心跳只存整數 epoch，若 now 帶微秒則「剛好在門檻上」永遠會
        # 因為截斷而變成「略微超過」，邊界測試會變成一條測不到東西的測試。
        self.now = datetime.now(timezone.utc).replace(microsecond=0)

    def _write_age(self, hours: float):
        epoch = int((self.now - timedelta(hours=hours)).timestamp())
        self.hb.write_text(f"ts=irrelevant\nepoch={epoch}\n", encoding="utf-8")

    def _assess(self, hours=9):
        return cbf.assess_pipeline(self.now, hours, self.hb)

    def test_missing_heartbeat_is_upstream_stale(self):
        f = self._assess()
        self.assertEqual(f.state, cbf.STATE_UPSTREAM_STALE)
        self.assertIn("不存在", f.detail)
        self.assertEqual(cbf.exit_code([f]), cbf.EXIT_UPSTREAM_STALE)

    def test_fresh_heartbeat_is_pass(self):
        self._write_age(1)
        self.assertEqual(self._assess().state, cbf.STATE_FRESH)
        self.assertEqual(cbf.exit_code([self._assess()]), cbf.EXIT_OK)

    def test_exactly_at_threshold_is_fresh(self):
        self._write_age(9)
        self.assertEqual(self._assess().state, cbf.STATE_FRESH)

    def test_just_over_threshold_is_upstream_stale(self):
        self._write_age(9.2)
        self.assertEqual(self._assess().state, cbf.STATE_UPSTREAM_STALE)

    def test_stale_heartbeat_is_upstream_stale(self):
        self._write_age(20)
        f = self._assess()
        self.assertEqual(f.state, cbf.STATE_UPSTREAM_STALE)
        self.assertEqual(cbf.exit_code([f]), cbf.EXIT_UPSTREAM_STALE)

    def test_future_timestamp_is_not_treated_as_fresh(self):
        """時鐘回跳或檔案被動過時，未來時間戳會讓門檻永久成立——抑制是最危險的方向。"""
        self._write_age(-3)
        f = self._assess()
        self.assertEqual(f.state, cbf.STATE_UPSTREAM_STALE)
        self.assertIn("未來", f.detail)

    def test_malformed_heartbeat_is_upstream_stale(self):
        for content in ("epoch=abc\n", "epoch=\n", "epoch=-1\n", "ts=x\n", "",
                        "epoch=$(id)\n", "epoch=1;rm -rf /\n"):
            with self.subTest(content=content):
                self.hb.write_text(content, encoding="utf-8")
                f = self._assess()
                self.assertEqual(f.state, cbf.STATE_UPSTREAM_STALE)
                self.assertNotIn("uid=", f.detail, "心跳內容被 shell 展開了")

    def test_state_file_is_not_sourced_or_evaled(self):
        body = FRESHNESS.read_text(encoding="utf-8")
        self.assertNotIn("eval(", body)
        self.assertNotIn("exec(", body)
        self.assertNotIn("json.load", body.split("def read_heartbeat")[1].split("def ")[1])

    def test_threshold_is_configurable_from_cli(self):
        args = cbf.parse_args(["--pipeline-hours", "4"])
        self.assertEqual(args.pipeline_hours, 4)
        self.assertEqual(cbf.parse_args([]).pipeline_hours, cbf.DEFAULT_PIPELINE_HOURS)

    def test_default_threshold_matches_three_sync_cycles(self):
        """SLA 由排程回推，不是硬編。timer 每 3 小時 → 3 個週期 = 9 小時。"""
        self.assertEqual(cbf.DEFAULT_PIPELINE_HOURS, 9)
        timer = (REPO_ROOT / "deploy" / "systemd" / "report-mark-sync.timer").read_text(
            encoding="utf-8"
        )
        self.assertIn("00/3:00:00", timer, "門檻的推導前提是每 3 小時；排程若改，門檻要重算")


class ExitCodePrecedenceTests(unittest.TestCase):
    """四個狀態的 rc 對映與優先序。"""

    def _f(self, asset, state, threshold=3):
        return cbf.Finding(asset, asset, state, None, None, threshold, "")

    def test_upstream_stale_wins_over_asset_stale(self):
        """管線沒跑完時資產停更只是症狀，先報症狀會讓人去查錯的地方。"""
        rc = cbf.exit_code([
            self._f("pipeline", cbf.STATE_UPSTREAM_STALE),
            self._f("summary", cbf.STATE_STALE),
        ])
        self.assertEqual(rc, cbf.EXIT_UPSTREAM_STALE)

    def test_asset_stale_alone_is_exit_stale(self):
        rc = cbf.exit_code([
            self._f("pipeline", cbf.STATE_FRESH),
            self._f("summary", cbf.STATE_STALE),
        ])
        self.assertEqual(rc, cbf.EXIT_STALE)

    def test_expected_skip_never_affects_exit_code(self):
        for skip in (cbf.STATE_DISABLED, cbf.STATE_SUPPRESSED):
            with self.subTest(state=skip):
                rc = cbf.exit_code([
                    self._f("pipeline", cbf.STATE_FRESH),
                    self._f("signal", skip, threshold=0),
                ])
                self.assertEqual(rc, cbf.EXIT_OK)

    def test_corpus_gate_cannot_suppress_upstream_stale(self):
        """**最關鍵的一條。**

        語料閘的用意是「沒有新稿時別怪派生批次」。但管線有沒有跑完與有沒有新稿無關，
        把它放進閘內會製造致命抑制：連假期間管線整個停掉會被讀成「本來就沒事做」。
        """
        findings = [
            self._f("pipeline", cbf.STATE_UPSTREAM_STALE),
            self._f("summary", cbf.STATE_SUPPRESSED),
            self._f("takeaway", cbf.STATE_SUPPRESSED),
            self._f("signal", cbf.STATE_DISABLED, threshold=0),
        ]
        self.assertEqual(cbf.exit_code(findings), cbf.EXIT_UPSTREAM_STALE)

    def test_assess_pipeline_is_outside_assess(self):
        """管線那筆不能由 assess() 產生——它不吃 latest，也不該被語料閘的邏輯碰到。"""
        src = FRESHNESS.read_text(encoding="utf-8")
        body = src.split("def assess(")[1].split("\ndef ")[0]
        self.assertNotIn("pipeline", body, "assess() 不得處理 pipeline")
        self.assertNotIn("HEARTBEAT", body)

    def test_report_names_the_four_states(self):
        now = datetime.now(timezone.utc)
        up = self._f("pipeline", cbf.STATE_UPSTREAM_STALE)
        self.assertIn("UPSTREAM_STALE", cbf.fmt_report([up], now))
        ok = self._f("pipeline", cbf.STATE_FRESH)
        self.assertIn("PASS", cbf.fmt_report([ok], now))
        self.assertIn("FAIL", cbf.fmt_report([ok, self._f("summary", cbf.STATE_STALE)], now))


class FreshnessUnitContractTests(unittest.TestCase):
    def test_unit_does_not_swallow_the_new_exit_code(self):
        """**加退出碼之前必須驗這件事。**

        `SuccessExitStatus=3` 會讓真正的 UPSTREAM_STALE 變成 systemd 眼中的成功，
        OnFailure 一次都不會觸發——而那個 unit 存在的唯一理由就是觸發它。
        """
        body = FRESHNESS_UNIT.read_text(encoding="utf-8")
        directives = [
            ln.strip() for ln in body.splitlines()
            if not ln.strip().startswith("#") and "=" in ln
        ]
        for d in directives:
            key, _, value = d.partition("=")
            if key.strip() == "SuccessExitStatus":
                self.fail(f"freshness unit 宣告了 SuccessExitStatus={value}，會吞掉 rc=3")

    def test_unit_still_alerts_on_failure(self):
        directives = [
            ln.strip() for ln in FRESHNESS_UNIT.read_text(encoding="utf-8").splitlines()
            if not ln.strip().startswith("#") and "=" in ln
        ]
        self.assertTrue(
            any(d.startswith("OnFailure=") for d in directives),
            "rc=3 必須有告警落點，否則新狀態沒有讀取路徑",
        )

    def test_exit_codes_are_distinct(self):
        codes = {cbf.EXIT_OK, cbf.EXIT_STALE, cbf.EXIT_UNKNOWN, cbf.EXIT_UPSTREAM_STALE}
        self.assertEqual(len(codes), 4, "四個退出碼不得相撞")
        self.assertNotEqual(cbf.EXIT_UPSTREAM_STALE, 0, "UPSTREAM_STALE 必須非零")


if __name__ == "__main__":
    unittest.main()
