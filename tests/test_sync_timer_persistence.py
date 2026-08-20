# tests/test_sync_timer_persistence.py
"""`report-mark-sync.timer` 為什麼刻意不補跑，以及補跑一旦被中斷會留下什麼。

**這不是「timer 設定的風格偏好」，是一條資料完整性規則。**

`Persistent=true` 的補跑時機必然是「機器剛醒來」，而那正是最可能立刻又被收掉的
一刻。2026-08-17 16:34 的生產 journal 是完整的一次：

    16:34:25  boot
    16:34:26  Started report-mark-sync.timer
    16:34:28  Starting report-mark-sync.service     ← 補跑，開機後 3 秒
    16:34:28  sudo mount-nas-research               ← 掛載此時才在設定
    16:34:53  Stopped report-mark-sync.service      ← 存活 25 秒
    16:34:53  Failed to enqueue OnFailure= job, ignoring:
              Transaction for report-mark-alert@... is destructive
              (time-set.target has 'stop' job queued …)

兩件事同時成立，而且**互相掩蓋**：

1. **關機期間 `OnFailure=` 完全無聲。** systemd 拒絕把 alert unit 排進已含 stop job
   的 transaction，訊息是 `Failed to enqueue OnFailure= job, ignoring`——不是錯誤、
   不進 `data/unit_failures.log`。同一形狀在本機 journal 出現 5 次以上，且不限 sync
   （`report-mark-web.service`、`report-mark-audit.service` 都中過）。
2. **被砍在 rsync 中途會留下拿不回來的檔案。** `--size-only` 讓下一輪 rsync 認定
   那些檔案已同步，於是**不再列進 delta**——而 delta 是匯入的唯一輸入。檔案在本地、
   DB 裡沒有、下一輪不會自己補。`sync_delta_20260817_163428.txt` 與
   `sync_delta_20260818_085003.txt` 都是 0 bytes，正是這個指紋。

隔離實驗（user-scope systemd，未動生產）確認語意本身：回溯 stamp 檔模擬「關機兩天」，
`Persistent=true` 在 timer 啟動同秒觸發，`Persistent=false` 與**完全移除該行**都不觸發、
只等下一個排程。

替代方案不是「不知道漏跑了」——**P1 心跳／freshness 會把它報成 `UPSTREAM_STALE`**，
那是一個有讀取端的訊號，而補跑買到的一小時抵不過偶爾製造一個要人工補的洞。
"""
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SYSTEMD = REPO_ROOT / "deploy" / "systemd"
SYNC_TIMER = SYSTEMD / "report-mark-sync.timer"
SYNC_SH = REPO_ROOT / "scripts" / "sync_new_reports.sh"


def _live(path: Path) -> str:
    """去掉註解與空行——`Persistent` 出現在說明文字裡不算生效設定。"""
    out = []
    for ln in path.read_text(encoding="utf-8").splitlines():
        s = ln.strip()
        if s and not s.startswith("#"):
            out.append(s)
    return "\n".join(out)


class SyncTimerPersistenceTests(unittest.TestCase):
    def test_sync_timer_does_not_catch_up(self):
        live = _live(SYNC_TIMER)
        self.assertNotIn("Persistent=true", live)
        self.assertIn(
            "Persistent=false", live,
            "刻意寫成 false 而不是省略：省略與 false 行為相同，但讀 unit 的人看不出"
            "「這是想過的決定」還是「忘了設」",
        )

    def test_cadence_is_unchanged(self):
        """本次只動補跑語意，**不得順手改排程**——9 小時的 SLA 由它回推。"""
        self.assertIn("OnCalendar=*-*-* 00/3:00:00", _live(SYNC_TIMER))

    def test_daily_timers_keep_persistent(self):
        """非對稱是刻意的，不是不一致。

        每日一次的 timer 錯過一次就是整天沒有備份／沒有停更偵測／沒有耐久性稽核，
        而那一天無法由下一次涵蓋；且三支都短命、唯讀（或原子改名），被砍在中途
        不會留下半成品。sync 兩個條件都不成立。
        """
        for name in ("report-mark-backup", "report-mark-freshness", "report-mark-audit"):
            live = _live(SYSTEMD / f"{name}.timer")
            self.assertIn("Persistent=true", live, f"{name}.timer 必須保留補跑")

    def test_timer_unit_still_parses(self):
        r = subprocess.run(
            ["systemd-analyze", "verify", str(SYNC_TIMER)],
            capture_output=True, text=True,
        )
        self.assertNotIn("Failed", r.stderr, r.stderr)

    def test_onfailure_is_still_declared(self):
        """關機期間送不出去，不代表平時不該送——OnFailure 保留。"""
        self.assertIn(
            "OnFailure=report-mark-alert@%n.service",
            _live(SYSTEMD / "report-mark-sync.service"),
        )


class RsyncFlagContractTests(unittest.TestCase):
    """下面的 stranded 測試用的旗標必須與腳本逐字相同，否則測到的是別的東西。"""

    FLAGS = ["-rt", "--size-only", "--no-motd"]

    def test_script_still_uses_these_flags(self):
        line = [ln for ln in SYNC_SH.read_text(encoding="utf-8").splitlines()
                if re.match(r"\s*rsync ", ln)]
        self.assertEqual(len(line), 1, f"預期恰好一處 rsync 呼叫，實得 {len(line)}")
        for f in self.FLAGS:
            self.assertIn(f, line[0], f"腳本已不再使用 {f}，本檔的 stranded 測試需重寫")

    def test_delta_is_the_only_import_input(self):
        """匯入吃的是 delta；這是「delta 只有一次機會」的前提。"""
        body = SYNC_SH.read_text(encoding="utf-8")
        self.assertIn("--delta", body)


class StrandedIngestionRegressionTests(unittest.TestCase):
    """用**真的 rsync** 驗 `--size-only` 的後果，不用假二進位。

    這條規則的全部重量都在 rsync 自己的判定上，假的 rsync 只會複述我寫的假設。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.src = root / "src"
        self.dst = root / "dst"
        self.src.mkdir()
        self.dst.mkdir()

    def _write(self, name: str, body: str):
        (self.src / name).write_text(body, encoding="utf-8")

    def _rsync(self) -> list[str]:
        """與 `sync_new_reports.sh` 同旗標；回傳 delta（已傳輸的檔案列）。"""
        r = subprocess.run(
            ["rsync", "-rt", "--size-only", "--no-motd", "--out-format=%n",
             f"{self.src}/", f"{self.dst}/"],
            capture_output=True, text=True, check=True,
        )
        return [ln for ln in r.stdout.splitlines() if ln and not ln.endswith("/")]

    def test_first_pass_lists_new_files(self):
        self._write("a.pdf", "AAA")
        self._write("b.pdf", "BBB")
        self.assertEqual(sorted(self._rsync()), ["a.pdf", "b.pdf"])

    def test_second_pass_does_not_relist_them(self):
        """**stranded 的成因。** 檔案已落地 ⇒ 下一輪 delta 是空的。"""
        self._write("a.pdf", "AAA")
        self.assertEqual(self._rsync(), ["a.pdf"])
        self.assertEqual(self._rsync(), [], "delta 只有一次機會")

    def test_files_are_on_disk_even_when_delta_is_empty(self):
        """「delta 空」與「沒有新檔」在磁碟上是兩件完全不同的事。"""
        self._write("a.pdf", "AAA")
        self._rsync()                       # 第一輪：傳輸成功
        second = self._rsync()              # 模擬：第一輪在匯入前被砍，第二輪照常
        self.assertEqual(second, [])
        self.assertTrue((self.dst / "a.pdf").is_file(), "檔案在本地，卻不在任何 delta 裡")

    def test_local_enumeration_still_finds_them(self):
        """`--all-local` 的前提：改看本地鏡像而不是 delta，所以救得回來。"""
        self._write("a.pdf", "AAA")
        self._rsync()
        self._rsync()
        local = sorted(p.name for p in self.dst.iterdir() if p.is_file())
        self.assertEqual(local, ["a.pdf"])

    def test_size_only_ignores_same_size_content_change(self):
        """`--size-only` 的具體語意：大小相同就當作已同步，內容變了也不重傳。

        釘住它是因為「下一輪會自己補上」這個直覺**在兩個層面都錯**——不只漏掉
        已傳輸的檔案，連來源被就地修改過的檔案也漏。
        """
        self._write("a.pdf", "AAA")
        self._rsync()
        self._write("a.pdf", "BBB")         # 同長度、不同內容
        self.assertEqual(self._rsync(), [])
        self.assertEqual((self.dst / "a.pdf").read_text(encoding="utf-8"), "AAA")

    def test_a_genuinely_new_file_is_still_picked_up(self):
        """反向對照：機制沒有壞掉，只是對「已落地」的檔案無能為力。"""
        self._write("a.pdf", "AAA")
        self._rsync()
        self._write("c.pdf", "CCC")
        self.assertEqual(self._rsync(), ["c.pdf"])


if __name__ == "__main__":
    unittest.main()
