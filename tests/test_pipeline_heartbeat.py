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

本檔另涵蓋 `data/sync_round_state`（RoundStateTests）。兩者分工，不可互相取代：
心跳量「最近一次完整成功有多久以前」，本輪狀態量「這一輪是怎麼收場的」。後者存在的
理由是 `OnFailure=` 在系統關機時**結構性地不會觸發**（systemd 拒絕把告警排進已含
stop job 的 transaction），於是被關機砍掉的那一輪完全無聲——心跳看得到「久沒成功」，
但看不到「那一輪被砍在 rsync 中途、新檔已落地而 --size-only 讓下一輪不再列出它們」。
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
ROUND_STATE_REL = "data/sync_round_state"
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
        self.stats_file = self.root / "stats_out"
        # importer 的計數器。None＝importer 什麼都沒寫（模擬工具層異常）；
        # 字串＝逐字寫出（模擬格式壞掉）。預設是一輪乾淨的匯入。
        self.stats: dict | str | None = {"ingested": 1, "fail": 0, "skip_untagged": 0}
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
        if self.stats is None:
            self.stats_file.write_text("", encoding="utf-8")
        elif isinstance(self.stats, str):
            self.stats_file.write_text(self.stats, encoding="utf-8")
        else:
            body = "".join(f"{k}={v}\n" for k, v in self.stats.items())
            abn = sum(int(self.stats.get(k, 0)) for k in ("fail", "skip_untagged"))
            self.stats_file.write_text(body + f"abnormal={abn}\n", encoding="utf-8")
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
            f'if [ "$N" = sync_new_reports ] && [ -s "{self.stats_file}" ]; then '
            f'cat "{self.stats_file}" > data/.sync_last_stats; fi\n'
            f'RC=$(grep "^$N=" "{self.rc_file}" 2>/dev/null | head -1 | cut -d= -f2)\n'
            'exit "${RC:-0}"\n',
        )

    def set_stats(self, stats):
        self.stats = stats
        self._write_fakes()

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

    def test_stats_file_is_gitignored(self):
        """**新增執行期檔案必須同步加 .gitignore。** `data/` 是逐項忽略。

        PR #221 加了 `data/.sync_last_stats` 卻漏了這一行，於是它永遠掛在
        `git status` 上——而本 repo 明令禁止 `git add -A`，正是因為那種雜訊會讓人
        開始忽略 status，接著就會漏看真正該看的東西。
        """
        ignored = subprocess.run(
            ["git", "check-ignore", "data/.sync_last_stats"],
            cwd=REPO_ROOT, capture_output=True, text=True,
        )
        self.assertEqual(ignored.returncode, 0, "匯入計數是執行期狀態，必須被 gitignore")

    def test_heartbeat_is_gitignored(self):
        ignored = subprocess.run(
            ["git", "check-ignore", HEARTBEAT_REL],
            cwd=REPO_ROOT, capture_output=True, text=True,
        )
        self.assertEqual(ignored.returncode, 0, "心跳是執行期狀態，必須被 gitignore")


class IngestAbnormalTests(unittest.TestCase):
    """**importer rc=0 不代表該入庫的檔都入庫了。**

    2026-08-20 實測：claude CLI 自我更新到缺 native artifact 的版本，7 篇新研報全部
    記成 `skip_untagged`，importer 照常 rc=0，殼只印「本次無新研報入庫」——與「NAS
    真的沒有新檔」在畫面上完全一樣。那一輪之所以沒有錯誤更新心跳，**純粹因為後面
    的每日簡報剛好 rc=1**；若當時不在簡報的執行時段，7 篇會靜默消失。

    分界不看名字看「重跑會不會不一樣」：`fail`／`skip_untagged` 是前置條件失敗、
    環境修好重跑就會入庫 ⇒ 異常；`skip_exists`／`skip_admin`／`skip_non_research`
    是明確判定不該入庫 ⇒ 預期；`skip_scanned` 是檔案本身抽不出文字、重跑一萬次
    也一樣 ⇒ 預期（算成異常會讓心跳因語料裡固定存在的掃描件而永遠不更新）。
    """

    def setUp(self):
        self.h = _SyncHarness()
        self.addCleanup(self.h.close)

    def _run_ok(self, **stats):
        base = {"ingested": 0, "fail": 0, "skip_untagged": 0}
        base.update(stats)
        self.h.set_stats(base)
        return self.h.run()

    # ── 正常路徑必須保留 ────────────────────────────────────────────────
    def test_true_zero_new_reports_still_updates_heartbeat(self):
        """**NAS 真的沒有新檔仍是完整成功。** 週末沒新稿是常態，不是故障。"""
        self.h.delta = []
        self.h.new_hashes = []
        p = self._run_ok(ingested=0)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertTrue(self.h.heartbeat.is_file(), p.stdout)

    def test_clean_ingest_updates_heartbeat(self):
        p = self._run_ok(ingested=3)
        self.assertEqual(p.returncode, 0)
        self.assertTrue(self.h.heartbeat.is_file())

    def test_expected_skips_do_not_block_heartbeat(self):
        """預期的 skip 不得誤判成異常，否則心跳會因語料常態而永遠不更新。"""
        for k in ("skip_exists", "skip_admin", "skip_non_research", "skip_scanned"):
            with self.subTest(counter=k):
                h = _SyncHarness()
                self.addCleanup(h.close)
                h.set_stats({"ingested": 0, "fail": 0, "skip_untagged": 0, k: 9})
                h.run()
                self.assertTrue(h.heartbeat.is_file(), f"{k} 不該擋心跳")

    # ── 異常路徑 ────────────────────────────────────────────────────────
    def test_untagged_only_blocks_heartbeat(self):
        """這就是 2026-08-20 的形狀：ingested=0、skip_untagged=7、rc=0。"""
        p = self._run_ok(ingested=0, skip_untagged=7)
        self.assertEqual(p.returncode, 0, "殼仍照常跑完，只是不算完整成功")
        self.assertFalse(self.h.heartbeat.is_file(), p.stdout)
        self.assertIn("匯入異常", p.stdout)

    def test_mixed_ingest_and_untagged_blocks_heartbeat(self):
        """部分成功不能沖銷部分漏收——漏的那幾篇不會自己回來。"""
        p = self._run_ok(ingested=5, skip_untagged=2)
        self.assertFalse(self.h.heartbeat.is_file(), p.stdout)

    def test_fail_counter_also_blocks_heartbeat(self):
        """`fail`（抽字或寫入 DB 例外）同樣是 rc=0 的漏收路徑。"""
        p = self._run_ok(ingested=1, fail=3)
        self.assertFalse(self.h.heartbeat.is_file(), p.stdout)

    def test_later_downstream_success_cannot_clear_it(self):
        """異常是黏著的：後面每一段都成功也不能把它抹掉。"""
        p = self._run_ok(ingested=0, skip_untagged=1)
        self.assertNotIn("段異常", p.stdout.split("匯入異常")[0])
        self.assertFalse(self.h.heartbeat.is_file())
        self.assertIn("不更新心跳", p.stdout)

    def test_abnormal_recorded_even_without_any_downstream_failure(self):
        """**真實事故的關鍵條件**：沒有任何下游段回非零，仍必須擋住心跳。

        2026-08-20 是靠簡報那段的 rc=1 才救回來的。這個測試把那個巧合拿掉。
        """
        self.h.rcs = {}                      # 所有下游段一律 rc=0
        p = self._run_ok(ingested=0, skip_untagged=7)
        self.assertFalse(self.h.heartbeat.is_file(), p.stdout)
        self.assertIn("ingest_abnormal", (self.h.root / "data" / "unit_failures.log")
                      .read_text(encoding="utf-8"))

    def test_recovery_hint_names_the_targeted_path(self):
        """提示必須指向精準補救，不能叫人跑 O(全庫) 的 --all-local。"""
        p = self._run_ok(skip_untagged=1)
        self.assertIn("failures_to_delta.py", p.stdout)
        self.assertNotIn("--all-local", p.stdout)

    # ── 計數檔本身壞掉 ──────────────────────────────────────────────────
    def test_missing_stats_is_conservative_abnormal(self):
        """讀不到就當沒問題，等於在最需要它的時候把守門關掉。"""
        self.h.set_stats(None)
        p = self.h.run()
        self.assertFalse(self.h.heartbeat.is_file(), p.stdout)
        self.assertIn("計數檔不可讀", p.stdout)

    def test_malformed_stats_is_conservative_abnormal(self):
        for body in (
            "abnormal=\n",        # 空值
            "abnormal=abc\n",     # 非數字
            "abnormal=-1\n",      # 負數
            "garbage\n",          # 沒有這個鍵
            "\n",                 # 空檔
        ):
            with self.subTest(body=body):
                h = _SyncHarness()
                self.addCleanup(h.close)
                h.set_stats(body)
                p = h.run()
                self.assertFalse(h.heartbeat.is_file(), f"{body!r}: {p.stdout}")

    def test_stale_stats_from_a_previous_round_cannot_be_reused(self):
        """importer 中途死掉會留下舊檔；讀到上一輪的 abnormal=0 等於守門不存在。"""
        (self.h.root / "data").mkdir(exist_ok=True)
        (self.h.root / "data" / ".sync_last_stats").write_text(
            "abnormal=0\n", encoding="utf-8"
        )
        self.h.set_stats(None)               # 這一輪 importer 沒寫出任何計數
        p = self.h.run()
        self.assertFalse(self.h.heartbeat.is_file(), p.stdout)

    def test_stats_file_is_not_sourced_or_evaled(self):
        """那個檔的內容源自檔名，而檔名來自 NAS。"""
        canary = self.h.root / "pwned"
        self.h.set_stats(f'abnormal=$(touch "{canary}")\nabnormal=`touch "{canary}"`\n')
        self.h.run()
        self.assertFalse(canary.exists(), "計數檔被當成 shell 程式碼求值了")

    def test_recovery_round_can_update_heartbeat_again(self):
        """異常不是永久的：補救成功後下一輪必須能恢復。"""
        self._run_ok(skip_untagged=2)
        self.assertFalse(self.h.heartbeat.is_file())
        p = self._run_ok(ingested=2)
        self.assertEqual(p.returncode, 0)
        self.assertTrue(self.h.heartbeat.is_file(), p.stdout)

    def test_import_nonzero_rc_behaviour_unchanged(self):
        """rc!=0 仍走原本的 exit 1 早退，不因新閘而改變。"""
        self.h.set_rc(sync_new_reports=1)
        p = self.h.run()
        self.assertNotEqual(p.returncode, 0)
        self.assertFalse(self.h.heartbeat.is_file())


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


class RoundStateTests(unittest.TestCase):
    """`data/sync_round_state`：這一輪是怎麼收場的，以及下一輪要不要補記告警。"""

    def setUp(self):
        self.h = _SyncHarness()
        self.addCleanup(self.h.close)

    @property
    def _state_path(self) -> Path:
        return self.h.root / ROUND_STATE_REL

    def _state(self) -> dict:
        if not self._state_path.is_file():
            return {}
        return dict(
            ln.split("=", 1)
            for ln in self._state_path.read_text(encoding="utf-8").splitlines()
            if "=" in ln
        )

    def _seed_state(self, **fields):
        """偽造上一輪留下的狀態檔。"""
        body = "".join(f"{k}={v}\n" for k, v in fields.items())
        self._state_path.write_text(body, encoding="utf-8")

    def _unit_failures(self) -> str:
        f = self.h.root / "data" / "unit_failures.log"
        return f.read_text(encoding="utf-8") if f.is_file() else ""

    # ── 終態 ────────────────────────────────────────────────────────────────
    def test_clean_round_writes_done(self):
        self.h.run()
        st = self._state()
        self.assertEqual(st.get("state"), "done")
        self.assertEqual(st.get("rc"), "0")
        self.assertEqual(st.get("signal"), "", "沒被訊號砍時 signal 必須是空字串而非缺鍵")

    def test_failed_round_writes_failed(self):
        self.h.rsync_rc = 1
        self.h._write_fakes()
        self.h.run()
        st = self._state()
        self.assertEqual(st.get("state"), "failed")
        self.assertEqual(st.get("rc"), "1")

    def test_sigterm_writes_signalled_with_signal_name(self):
        """關機那條路：EXIT trap 有跑到，但終態必須與乾淨失敗分得開。

        `signal=` 不可省——沒有它，事後分不出「被砍」與「連 trap 都沒跑到」。
        """
        self.h.run_and_kill(1.0, sig=signal.SIGTERM)
        st = self._state()
        self.assertEqual(st.get("state"), "signalled")
        self.assertEqual(st.get("signal"), "TERM")

    def test_sigkill_leaves_running(self):
        """SIGKILL 擋不住：狀態停在 running，正是下一輪要補記的那一種。"""
        self.h.run_and_kill(1.0, sig=signal.SIGKILL)
        self.assertEqual(self._state().get("state"), "running")

    # ── 下一輪的補記 ────────────────────────────────────────────────────────
    def test_next_round_backfills_aborted_from_running(self):
        self._seed_state(state="running", started_at="2026-08-18T08:50:00+08:00",
                         pid="4242", log="data/sync_run_20260818.log", signal="", rc="")
        self.h.run()
        blob = self._unit_failures()
        self.assertIn("STAGE=sync_round(aborted)", blob)
        self.assertIn("--all-local", blob, "復原指令不可省：那批新檔不會自己補回來")

    def test_next_round_backfills_aborted_from_signalled(self):
        self._seed_state(state="signalled", started_at="2026-08-18T08:50:00+08:00",
                         pid="4242", log="data/sync_run_20260818.log", signal="TERM", rc="143")
        self.h.run()
        self.assertIn("STAGE=sync_round(aborted)", self._unit_failures())

    def test_failed_state_is_not_backfilled(self):
        """乾淨的非零退出不補記——那條路 unit 會變紅，OnFailure 正常運作。

        補記它等於同一次失敗在監控頁記兩筆。
        """
        self._seed_state(state="failed", started_at="2026-08-18T08:50:00+08:00",
                         pid="4242", log="x", signal="", rc="1")
        self.h.run()
        self.assertNotIn("sync_round(aborted)", self._unit_failures())

    def test_done_state_is_not_backfilled(self):
        self._seed_state(state="done", started_at="2026-08-18T08:50:00+08:00",
                         pid="4242", log="x", signal="", rc="0")
        self.h.run()
        self.assertNotIn("sync_round(aborted)", self._unit_failures())

    def test_backfilled_header_parses_as_monitor_record(self):
        """補記的標頭必須被 monitor 的 `_UNIT_FAIL_RE` 認得，否則寫了也沒有讀取路徑。

        rc 不明時整段 `RC=` 省略而不猜——被 SIGKILL 收掉的那一輪本來就沒有退出碼。
        """
        from web.routers.monitor import _UNIT_FAIL_RE
        self._seed_state(state="running", started_at="2026-08-18T08:50:00+08:00",
                         pid="4242", log="x", signal="", rc="")
        self.h.run()
        headers = [
            ln.strip() for ln in self._unit_failures().splitlines()
            if "sync_round(aborted)" in ln
        ]
        self.assertTrue(headers, "沒有補記標頭")
        m = _UNIT_FAIL_RE.match(headers[0])
        self.assertIsNotNone(m, f"monitor 解析不出這一行：{headers[0]}")
        self.assertEqual(m.group("stage"), "sync_round(aborted)")
        self.assertIsNone(m.group("rc"), "rc 不明時不得猜一個數字填進去")

    def test_backfill_happens_before_state_is_overwritten(self):
        """補記必須早於本輪寫 running，否則上一輪的狀態已被抹掉、永遠讀不到。"""
        self._seed_state(state="running", started_at="2026-08-18T08:50:00+08:00",
                         pid="4242", log="x", signal="", rc="")
        self.h.run()
        self.assertIn("sync_round(aborted)", self._unit_failures())
        self.assertEqual(self._state().get("state"), "done", "本輪自己的終態仍要寫出")

    # ── 兩條早退路徑 ────────────────────────────────────────────────────────
    def test_lock_held_skip_does_not_touch_state(self):
        """被 PID lock 跳過的那一次，不得動到仍在跑的那一輪的狀態。

        整組 trap 刻意裝在取得 lock 之後；裝在之前的話，被跳過的這次一 exit 就會把
        別人的鎖與狀態一起清掉——而那一輪還在跑，症狀是憑空多出一筆 aborted。
        """
        proc = subprocess.Popen(["sleep", "30"])
        self.addCleanup(proc.kill)
        (self.h.root / "data" / ".sync_new_reports.lock").write_text(str(proc.pid))
        self._seed_state(state="running", started_at="2026-08-18T08:50:00+08:00",
                         pid=str(proc.pid), log="x", signal="", rc="")
        r = self.h.run()
        self.assertEqual(r.returncode, 0)
        self.assertEqual(self._state().get("state"), "running", "別人的狀態被覆寫了")
        self.assertNotIn("sync_round(aborted)", self._unit_failures())

    def test_system_stopping_exits_clean_without_starting(self):
        """關機中就別開工——這是補集不是替代品，擋得住觸發點落在關機 transaction 裡。"""
        self.h._fake("systemctl", 'echo stopping\nexit 1\n')
        r = self.h.run()
        self.assertEqual(r.returncode, 0)
        self.assertFalse(self._state_path.is_file(), "不開工就不該留下本輪狀態")

    def test_unknown_systemd_is_fail_open(self):
        """認不出 systemd（手動執行、容器）一律放行，否則本機根本跑不了。"""
        self.h._fake("systemctl", 'exit 127\n')
        self.h.run()
        self.assertEqual(self._state().get("state"), "done")


if __name__ == "__main__":
    unittest.main()
