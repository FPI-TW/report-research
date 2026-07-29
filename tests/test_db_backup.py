# tests/test_db_backup.py
"""DB 備份機制的靜態守門。

為什麼要有這一支：備份是**唯一一種「壞掉時完全沒有症狀」的基礎設施**——備份靜默
停掉、dump 檔靜默變空殼、落點靜默退回本地磁碟，站台照樣好好的，你要等到真的需要
還原那天才會知道。所以能機械化的規約一條都不要留在註解裡。

這裡守的四件事，每一條都對應一個本專案已經踩過或明確可預期的坑：

1. **`Environment=HOME=%h`**：系統層 unit 的 `%h` 一律解析為 `/root`，不受 `User=`
   影響。2026-07-28 `report-mark-sync.service` 因此連續 10 輪 exit 2、整條入庫管線
   停擺 24 小時。新 unit 一旦重蹈就會在這裡紅。（`tests/test_deploy_units.py` 已通掃
   全部 unit，這裡再針對備份 unit 釘一次，是因為那支測試的失敗訊息不會告訴你
   「備份沒在跑」。）
2. **`|| RC=$?` 而非裸呼叫 + `RC=$?`**：`set -e` 下裸呼叫失敗會就地中止，緊接其後的
   `RC=$?` 是死碼。`sync_new_reports.sh` 就是這樣讓一次 24 小時的故障「日誌永遠停在
   同一行」、事後完全看不出敗在哪一步。
3. **備份表清單**：`qa_log` 與 `report_doc` 是整個備份存在的理由（問答史／研報真相
   來源，研報原檔裡沒有這些東西）。有人為了縮小 dump 把它們拿掉，不會有任何症狀。
4. **`ingest_lowio.sh` 的備份新鮮度硬閘**：那支腳本會關掉 `fsync`，崩潰即可能整個
   pgdata 報廢。它檔頭原本的安全論證「本 DB 為衍生、可由原始研報重建」在 `qa_log`
   等表存在之後已經不成立——閘門與那段論證必須同時在。

刻意不做的：不真的跑 `pg_dump`、不連 DB。備份腳本的執行期行為（原子改名、檔頭
魔數、輪替）已在開發時以假 `docker` 二進位在沙箱驗過，但那需要建立暫存目錄與
假二進位，放進 CI 只會換來一支對環境敏感的測試。
"""
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SYSTEMD_DIR = REPO_ROOT / "deploy" / "systemd"
BACKUP_SH = REPO_ROOT / "scripts" / "db_backup.sh"
LOWIO_SH = REPO_ROOT / "scripts" / "ingest_lowio.sh"
BACKUP_SERVICE = SYSTEMD_DIR / "report-mark-backup.service"
BACKUP_TIMER = SYSTEMD_DIR / "report-mark-backup.timer"

# 「不可重建」的七張表：研報原檔裡沒有、刪了就永遠沒有的東西。
REQUIRED_TABLES = (
    "research.qa_log",
    "research.report_doc",
    "research.report_rendition",
    "research.report_takeaway",
    "research.report_signal",
    "research.report_run",
    "research.report_section",
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _live_lines(text: str) -> list[str]:
    """去掉註解行——註解裡的示例不算實際生效的設定／指令。"""
    return [ln for ln in text.splitlines() if not ln.strip().startswith("#")]


def _directives(path: Path, key: str) -> list[str]:
    out: list[str] = []
    for line in _live_lines(_read(path)):
        stripped = line.strip()
        if "=" not in stripped:
            continue
        name, _, value = stripped.partition("=")
        if name.strip() == key:
            out.append(value.strip())
    return out


class BackupUnitTests(unittest.TestCase):
    """`report-mark-backup.service` / `.timer` 的形狀。"""

    def test_unit_files_exist(self) -> None:
        for path in (BACKUP_SERVICE, BACKUP_TIMER):
            self.assertTrue(path.is_file(), f"缺少 {path.relative_to(REPO_ROOT)}")

    def test_service_does_not_use_percent_h_for_home(self) -> None:
        homes = [v for v in _directives(BACKUP_SERVICE, "Environment") if v.startswith("HOME=")]
        self.assertTrue(homes, "備份 unit 必須顯式設定 HOME（否則 systemd 預設環境不含它）")
        for value in homes:
            self.assertNotIn(
                "%h",
                value,
                "系統層 unit 的 %h 解析為 /root（不受 User= 影響）——sync unit 就是"
                "這樣停擺 24 小時。請硬編碼家目錄。",
            )

    def test_home_matches_other_units(self) -> None:
        """備份 unit 的 HOME 若與 web/sync 不同，等於埋下另一種漂移。"""
        others = {
            value.removeprefix("HOME=")
            for unit in sorted(SYSTEMD_DIR.glob("*.service"))
            if unit != BACKUP_SERVICE
            for value in _directives(unit, "Environment")
            if value.startswith("HOME=")
        }
        mine = {
            value.removeprefix("HOME=")
            for value in _directives(BACKUP_SERVICE, "Environment")
            if value.startswith("HOME=")
        }
        self.assertTrue(others, "同目錄應有其他 unit 設定 HOME 可供比對")
        self.assertEqual(mine, others, f"備份 unit 的 HOME 與其他 unit 不一致：{mine} vs {others}")

    def test_service_has_onfailure_alert(self) -> None:
        """備份靜默停掉不會有任何症狀，失敗必須留痕。"""
        self.assertIn(
            "report-mark-alert@%n.service",
            _directives(BACKUP_SERVICE, "OnFailure"),
            "備份 unit 缺 OnFailure=report-mark-alert@%n.service",
        )

    def test_service_runs_as_owning_user(self) -> None:
        self.assertEqual(
            _directives(BACKUP_SERVICE, "User"),
            ["kashionz"],
            "備份 unit 應以與其他 unit 相同的非 root 使用者執行",
        )

    def test_service_execstart_points_at_backup_script(self) -> None:
        exec_start = " ".join(_directives(BACKUP_SERVICE, "ExecStart"))
        self.assertIn("scripts/db_backup.sh", exec_start)

    def test_timer_is_daily_and_persistent(self) -> None:
        text = _read(BACKUP_TIMER)
        self.assertRegex(
            text,
            r"OnCalendar=\*-\*-\* \d{2}:\d{2}:\d{2}",
            "備份 timer 應為每日觸發（OnCalendar=*-*-* HH:MM:SS）",
        )
        self.assertIn(
            "Persistent=true",
            text,
            "少了 Persistent=true，機器關著錯過的那次就永遠不補跑——而缺一天沒有任何訊號",
        )
        self.assertIn("WantedBy=timers.target", text, "timer 缺 [Install] 就 enable 不起來")


class BackupScriptTests(unittest.TestCase):
    """`scripts/db_backup.sh` 的內容契約。"""

    def setUp(self) -> None:
        self.text = _read(BACKUP_SH)
        self.live = "\n".join(_live_lines(self.text))

    def test_strict_shell_options(self) -> None:
        self.assertIn("set -euo pipefail", self.text, "沿用專案殼腳本慣例：set -euo pipefail")

    def test_dumps_all_irreplaceable_tables(self) -> None:
        match = re.search(r"BACKUP_TABLES=\((.*?)\)", self.text, re.S)
        self.assertIsNotNone(match, "找不到 BACKUP_TABLES 陣列")
        listed = set(match.group(1).split())
        for table in REQUIRED_TABLES:
            self.assertIn(
                table,
                listed,
                f"{table} 不在備份清單。這些表是備份存在的理由——研報原檔裡沒有它們。",
            )

    def test_uses_pg_dump_custom_format(self) -> None:
        self.assertIn("pg_dump", self.live)
        self.assertIn("-Fc", self.live, "custom 格式才支援選擇性還原與內建壓縮")

    def test_docker_exec_never_allocates_a_tty(self) -> None:
        """`docker exec -it` 會對 stdout 做行尾轉換，把二進位 dump 悄悄弄壞。"""
        for line in _live_lines(self.text):
            if "exec" not in line or "DOCKER_BIN" not in line:
                continue
            self.assertNotRegex(
                line,
                r"exec\s+-\w*t",
                f"docker exec 不可配 TTY（會弄壞二進位 dump）：{line.strip()}",
            )

    def test_docker_bin_detection_matches_ingest_lowio(self) -> None:
        """兩支腳本的 docker 偵測必須逐字相同，否則會漂到只有一支跑得起來。"""

        def docker_bin_line(path: Path) -> str:
            for line in _live_lines(_read(path)):
                if line.startswith("DOCKER_BIN="):
                    return line.strip()
            return ""

        mine = docker_bin_line(BACKUP_SH)
        theirs = docker_bin_line(LOWIO_SH)
        self.assertTrue(mine, "db_backup.sh 缺 DOCKER_BIN 偵測")
        self.assertTrue(theirs, "ingest_lowio.sh 缺 DOCKER_BIN 偵測")
        self.assertEqual(mine, theirs, "兩支腳本的 DOCKER_BIN 偵測不一致")

    def test_fails_instead_of_falling_back_to_local_path(self) -> None:
        """落點不可用時必須失敗。與 pgdata 同一塊磁碟的『備份』等於沒有備份。"""
        self.assertIn("mountpoint -q", self.live, "缺少落點掛載檢查")
        self.assertRegex(
            self.live,
            r'mountpoint -q "\$BACKUP_MOUNT" \|\| die',
            "掛載檢查失敗時必須直接中止（不得改寫成本地路徑）",
        )
        self.assertRegex(
            self.live,
            r'REPORT_MARK_BACKUP_MOUNT:-/mnt/',
            "預設落點應在掛載點底下（/mnt/…），不是本機工作目錄",
        )

    def test_retention_policy_is_seven_daily_four_weekly(self) -> None:
        self.assertRegex(self.live, r"BACKUP_KEEP_DAILY:-7\b", "日備保留數應預設 7")
        self.assertRegex(self.live, r"BACKUP_KEEP_WEEKLY:-4\b", "週備保留數應預設 4")
        self.assertIn("prune_dir", self.live, "有保留參數卻沒有輪替實作＝參數是裝飾品")

    def test_validates_dump_before_publishing_it(self) -> None:
        """備份最惡劣的失敗型態是『檔案在、內容不能用』。"""
        self.assertIn("PGDMP", self.live, "缺少 pg_dump custom 格式的檔頭魔數驗證")
        self.assertRegex(
            self.live, r'mv "\$TMP" "\$OUT"', "應先寫中繼檔、驗過才原子改名為 *.dump"
        )


class ShellExitCodeCaptureTests(unittest.TestCase):
    """殼腳本一律以 `|| VAR=$?` 擷取退出碼，不得出現裸呼叫 + 獨立 `VAR=$?`。

    `set -e` 下獨立成行的 `VAR=$?` 永遠讀不到非 0——前一行失敗時行程已經走了。
    2026-07-28 的同步故障就是這樣：日誌永遠停在同一行，事後看不出敗在哪一步。
    """

    #  ^VAR=$?  獨立成行的擷取（死碼）；`cmd || VAR=$?` 不符合這個樣式。
    BARE_CAPTURE = re.compile(r"^\s*[A-Za-z_][A-Za-z0-9_]*=\$\?\s*(#.*)?$")

    def _scripts(self) -> list[Path]:
        return sorted(REPO_ROOT.glob("scripts/*.sh")) + sorted(
            REPO_ROOT.glob("deploy/systemd/*.sh")
        )

    def test_no_dead_exit_code_capture(self) -> None:
        offenders = []
        for path in self._scripts():
            for lineno, line in enumerate(_read(path).splitlines(), 1):
                if self.BARE_CAPTURE.match(line):
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno}: {line.strip()}")
        self.assertEqual(
            offenders,
            [],
            "裸呼叫 + 獨立 `VAR=$?` 在 set -e 下是死碼，請改成 `cmd || VAR=$?`",
        )

    def test_backup_script_actually_captures_exit_codes(self) -> None:
        """反面：上一題是禁止式的，這題確認備份腳本真的有在擷取退出碼。"""
        self.assertRegex(
            _read(BACKUP_SH),
            r"\|\|\s*[A-Za-z_][A-Za-z0-9_]*=\$\?",
            "db_backup.sh 應以 `|| VAR=$?` 擷取關鍵步驟的退出碼",
        )


class IngestLowIoBackupGateTests(unittest.TestCase):
    """`make ingest-lowio` 在關掉 fsync 之前必須先確認有近期備份。"""

    def setUp(self) -> None:
        self.text = _read(LOWIO_SH)
        self.lines = self.text.splitlines()

    def _index_of(self, needle: str) -> int:
        for i, line in enumerate(self.lines):
            if needle in line:
                return i
        return -1

    def test_has_backup_freshness_check(self) -> None:
        self.assertIn(
            "BACKUP_MAX_AGE_MIN",
            self.text,
            "缺少備份新鮮度閘門——關 fsync 崩潰即可能整個 pgdata 報廢",
        )
        self.assertRegex(
            self.text,
            r"find .*-mmin",
            "新鮮度要看檔案 mtime（備份靜默停掉時目錄照樣在，只檢查目錄存在擋不住）",
        )

    def test_gate_runs_before_durability_is_disabled(self) -> None:
        """閘門必須在碰 DB 之前。擋在後面等於沒擋。"""
        gate = self._index_of("BACKUP_MAX_AGE_MIN")
        fsync_off = self._index_of("fsync=off")
        self.assertGreater(fsync_off, -1, "找不到 ALTER SYSTEM SET fsync=off")
        self.assertGreater(gate, -1, "找不到備份新鮮度閘門")
        self.assertLess(gate, fsync_off, "備份閘門必須早於關閉 fsync 的那一步")

    def test_gate_aborts_by_default(self) -> None:
        self.assertIn("exit 1", self.text, "沒有近期備份時必須 exit 1，而不是印個提醒繼續跑")

    def test_stale_safety_argument_is_retracted_not_asserted(self) -> None:
        """檔頭那句『本 DB 為衍生、可由原始研報重建』在 qa_log 等表存在後已不成立。

        刻意不是「這串字不准出現」——把舊論證原樣引用再標記作廢，比默默刪掉更有用
        （下一個人才知道為什麼結論變了）。要擋的是它**仍以現況的口吻被斷言**。
        """
        for lineno, line in enumerate(self.lines, 1):
            if "本 DB 為衍生" not in line:
                continue
            self.assertIn(
                "不成立",
                line,
                f"ingest_lowio.sh:{lineno} 仍在斷言已失效的安全論證——"
                "它正是讓人放心去跑這支腳本的理由",
            )
        self.assertIn(
            "qa_log",
            self.text,
            "檔頭應說明現況：這座 DB 裝著 qa_log 等重建不回來的資料",
        )


class MakefileTargetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = _read(REPO_ROOT / "Makefile")

    def test_db_backup_target_exists(self) -> None:
        self.assertRegex(self.text, r"(?m)^db-backup:.*##", "Makefile 缺 db-backup target（含說明）")
        self.assertIn("scripts/db_backup.sh", self.text)

    def test_db_backup_is_phony(self) -> None:
        """漏宣告 .PHONY 時，只要出現同名檔案 target 就會靜默變成 no-op。"""
        phony = re.search(r"\.PHONY:(.*?)(?=\n[^\s])", self.text, re.S)
        self.assertIsNotNone(phony, "找不到 .PHONY 宣告")
        self.assertIn("db-backup", phony.group(1).split())


class BackupDocsTests(unittest.TestCase):
    """沒演練過的備份不算備份——還原步驟必須寫下來。"""

    def test_resilience_doc_documents_restore(self) -> None:
        text = _read(REPO_ROOT / "docs" / "production_resilience.md")
        self.assertIn("備份與還原", text, "docs/production_resilience.md 缺備份章節")
        self.assertIn(
            "pg_restore",
            text,
            "備份章節必須包含實際的還原指令，不能只寫『有備份』",
        )


if __name__ == "__main__":
    unittest.main()
