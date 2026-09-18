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
3. **備份表清單**：`qa_log` 與三張 LLM 批次產物表是整個備份存在的理由（問答史／
   摘錄／訊號／簡報，研報原檔裡沒有這些東西）。有人為了縮小 dump 把它們拿掉，不會有任何症狀。
4. **`ingest_lowio.sh` 的備份新鮮度硬閘**：那支腳本會關掉 `fsync`，崩潰即可能整個
   pgdata 報廢。它檔頭原本的安全論證「本 DB 為衍生、可由原始研報重建」在 `qa_log`
   等表存在之後已經不成立——閘門與那段論證必須同時在。

刻意不做的：不真的跑 `pg_dump`、不連 DB。備份腳本的執行期行為（原子改名、檔頭
魔數、輪替）已在開發時以假 `docker` 二進位在沙箱驗過，但那需要建立暫存目錄與
假二進位，放進 CI 只會換來一支對環境敏感的測試。
"""
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SYSTEMD_DIR = REPO_ROOT / "deploy" / "systemd"
BACKUP_SH = REPO_ROOT / "scripts" / "db_backup.sh"
LOWIO_SH = REPO_ROOT / "scripts" / "ingest_lowio.sh"
BACKUP_SERVICE = SYSTEMD_DIR / "report-mark-backup.service"
BACKUP_TIMER = SYSTEMD_DIR / "report-mark-backup.timer"

# 「不可重建」的四張表：研報原檔裡沒有、刪了就永遠沒有的東西。
REQUIRED_TABLES = (
    "research.qa_log",
    "research.report_takeaway",
    "research.report_signal",
    "research.report_brief",
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


MOUNT_HELPER = SYSTEMD_DIR / "mount-nas-backup"
SYNC_ENV_EXAMPLE = SYSTEMD_DIR / "report-mark-sync.env.example"


DOCKER_BIN_SH = REPO_ROOT / "scripts" / "_docker_bin.sh"


class DockerDetectionRuntimeTests(unittest.TestCase):
    """`scripts/_docker_bin.sh` 的**執行期**語意。

    這一段的缺陷靜態測試看不出來：`command -v docker` 在語法上毫無問題，只是
    語意錯——它只確認檔案存在，不確認 daemon 連得上。2026-07-30 備份因此挑到
    `/usr/bin/docker` 並在 `pg_isready` 死掉，錯誤訊息還叫人手動設 `DOCKER_BIN`，
    把一個可以自己偵測的東西變成每台機器都要人工設定一次的隱性前置條件。

    測試用假 bin 餵 `DOCKER_BIN_CANDIDATES`，所以**不依賴這台機器（或 CI）有沒有
    docker**——那正是原本這一段沒有測試的原因。
    """

    def _detect(self, candidates: str) -> str:
        got = subprocess.run(
            ["bash", "-c", f'. "{DOCKER_BIN_SH}"; detect_docker_bin'],
            capture_output=True, text=True, timeout=60,
            env={**os.environ, "DOCKER_BIN_CANDIDATES": candidates},
        )
        self.assertEqual(got.returncode, 0, got.stderr)
        return got.stdout.strip()

    def test_candidate_that_exists_but_cannot_reach_daemon_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            bad = Path(d) / "docker-broken"
            good = Path(d) / "docker-working"
            bad.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
            good.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            bad.chmod(0o755)
            good.chmod(0o755)
            self.assertEqual(
                self._detect(f"{bad}:{good}"), str(good),
                "存在但 `info` 失敗的候選必須被跳過——這正是 /usr/bin/docker 的情況",
            )

    def test_falls_back_to_plain_docker_when_nothing_works(self) -> None:
        """全探不到時回 `docker`，讓後續指令吐出真正的 daemon 錯誤。

        刻意不在這裡就 die：「連不到 daemon」與「找不到指令」是兩件事，處置不同，
        偵測層把前者偽裝成後者會讓排查走錯方向。
        """
        with tempfile.TemporaryDirectory() as d:
            bad = Path(d) / "docker-broken"
            bad.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
            bad.chmod(0o755)
            self.assertEqual(self._detect(str(bad)), "docker")

    def test_probe_uses_info_not_version(self) -> None:
        """`--version` 不碰 daemon，用它探測就是原本那個誤判的來源。"""
        live = "\n".join(_live_lines(_read(DOCKER_BIN_SH)))
        self.assertIn("info", live)
        self.assertNotIn("--version", live)

    def test_explicit_docker_bin_still_wins(self) -> None:
        """偵測是便利功能，不該奪走覆寫權（非標準安裝位置仍要能指定）。"""
        for name, path in (("db_backup.sh", BACKUP_SH), ("ingest_lowio.sh", LOWIO_SH)):
            live = "\n".join(_live_lines(_read(path)))
            with self.subTest(script=name):
                self.assertRegex(
                    live, r'DOCKER_BIN[:=]?[^\n]*\$\{DOCKER_BIN',
                    f"{name} 必須讓既有的 DOCKER_BIN 優先於偵測",
                )


class ConfigResolutionRuntimeTests(unittest.TestCase):
    """**執行期**測試，因為這一段的兩個缺陷靜態測試都看不出來（2026-07-30 實際踩到）。

    缺陷一：腳本原本只從環境變數讀落點，而 `/etc/default/report-mark-sync` 只有
    systemd 的 `EnvironmentFile=` 會載入——手動 `make db-backup` 完全不會。結果是
    **unit 與手動跑寫到不同地方**，而手動那條落回 share 根目錄（不可寫），失敗訊息
    看起來像 NAS 沒給權限。設定只該有一個真相來源。

    缺陷二：診斷訊息原本寫成 `HINT="$(mkdir …)"`。在 `set -e` 下，命令替換失敗會讓
    整個賦值失敗、腳本就地中止——所以那段精心寫的 EROFS/EACCES 分辨**一行都沒印出來**，
    使用者只看到兩行裸 mkdir 錯誤。這種缺陷只有真的跑一次才會現形。

    測試手法：拿 `/` 當「掛載點」——它必然是 mountpoint（所以腳本不會嘗試 sudo 掛載），
    而非 root 對它 mkdir 必然是 EACCES。不需要 NAS、不需要 docker、不需要 root，
    腳本會在碰 docker 之前就 die。
    """

    def _run(self, env: dict[str, str]) -> subprocess.CompletedProcess:
        merged = {**os.environ, **env}
        return subprocess.run(
            ["bash", str(BACKUP_SH)],
            capture_output=True, text=True, timeout=60, env=merged,
        )

    def test_diagnosis_is_actually_printed_not_swallowed_by_set_e(self) -> None:
        got = self._run({
            "REPORT_MARK_BACKUP_MOUNT": "/",
            "REPORT_MARK_BACKUP_DIR": "/tf_backup_probe",
            "REPORT_MARK_BACKUP_DEFAULTS": "/nonexistent-on-purpose",
        })
        self.assertNotEqual(got.returncode, 0)
        self.assertIn("!!", got.stderr, "die 的訊息必須印出來——set -e 不該先把腳本殺掉")
        self.assertIn("EACCES", got.stderr, "應辨識出是伺服器端／權限在擋，而非唯讀掛載")
        self.assertIn(
            "加 rw 掛載旗標沒有用", got.stderr,
            "EACCES 的處置與 EROFS 相反，訊息要講清楚免得有人去改掛載選項",
        )

    def test_failure_message_names_where_the_destination_came_from(self) -> None:
        """落點來源必須出現在訊息裡：同一個 EACCES，可能是 NAS 沒權限，
        也可能只是設定沒被讀到而落回內建預設——兩者處置完全不同。"""
        got = self._run({
            "REPORT_MARK_BACKUP_MOUNT": "/",
            "REPORT_MARK_BACKUP_DIR": "/tf_backup_probe",
            "REPORT_MARK_BACKUP_DEFAULTS": "/nonexistent-on-purpose",
        })
        self.assertIn("落點來源", got.stderr)
        self.assertIn("環境變數", got.stderr)

    def test_reads_destination_from_defaults_file_for_manual_runs(self) -> None:
        """手動跑（沒有 systemd 的 EnvironmentFile）也必須讀到設定檔的落點，
        否則 `make db-backup` 與 timer 會寫到不同地方。值刻意含括號——那是實際
        落點的形狀，也是不能用 `source` 讀這個檔的原因。"""
        with tempfile.TemporaryDirectory() as d:
            defaults = Path(d) / "report-mark-sync"
            defaults.write_text(
                "REPORT_MARK_BACKUP_MOUNT=/\n"
                "REPORT_MARK_BACKUP_DIR=/tf_probe_from_file(x)\n",
                encoding="utf-8",
            )
            env = {"REPORT_MARK_BACKUP_DEFAULTS": str(defaults)}
            for key in ("REPORT_MARK_BACKUP_MOUNT", "REPORT_MARK_BACKUP_DIR"):
                env.pop(key, None)
            merged = {k: v for k, v in os.environ.items()
                      if k not in ("REPORT_MARK_BACKUP_MOUNT", "REPORT_MARK_BACKUP_DIR")}
            merged.update(env)
            got = subprocess.run(
                ["bash", str(BACKUP_SH)],
                capture_output=True, text=True, timeout=60, env=merged,
            )
        self.assertNotEqual(got.returncode, 0)
        self.assertIn(
            "/tf_probe_from_file(x)", got.stderr,
            "落點沒有從設定檔讀到——手動跑與 timer 會寫到不同地方",
        )
        self.assertIn(str(defaults), got.stderr, "訊息應指出落點取自哪個檔")


class MountHelperTests(unittest.TestCase):
    """掛載腳本的兩條契約，都來自 2026-07-30 的實測。

    (1) 落點**不能**是 `投資研究處`。那組 NAS 帳號對它只有讀取權——第二個掛載點即使
        `/proc/mounts` 確認是 `rw`，寫入仍得 `Permission denied`（EACCES，伺服器端在
        擋），而唯讀那支得到的是 `Read-only file system`（EROFS，Linux 旗標在擋）。
        兩個 errno 不同正是判定依據。**加 rw 旗標救不了 ACL**，這條測試防的是有人
        照直覺改回同一個 share。

    (2) 讀環境檔**不能用 `source`**。那個檔是給 systemd 的 `EnvironmentFile` 讀的，
        systemd 不做 shell 解析，所以值合法地可能含 `(` `)`（實際落點就是
        `01.會議暫存(會後刪除)`）。實測 `bash -c '. 該檔'` 直接
        `syntax error near unexpected token '('`——source 一個給 systemd 讀的檔是
        安靜的地雷，更糟的情況是值被當指令求值。
    """

    def setUp(self) -> None:
        self.text = _read(MOUNT_HELPER)
        self.live = "\n".join(_live_lines(self.text))

    def test_does_not_mount_the_read_only_research_share(self) -> None:
        self.assertNotIn(
            "投資研究處",
            self.live,
            "那個 share 的 NAS 帳號只有讀取權（EACCES，非 mount 旗標問題）；"
            "備份必須落在另一個寫得進去的 share。",
        )

    def test_reads_env_file_without_sourcing_it(self) -> None:
        for forbidden in ("source ", ". /etc/default", ". \"$DEFAULTS\"", ". $DEFAULTS"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(
                    forbidden,
                    self.live,
                    "環境檔的值含括號，source 會 syntax error；請逐鍵取值。",
                )
        self.assertIn("sed -n", self.live, "應以逐鍵解析取代 source")

    def test_unc_and_mount_point_are_overridable(self) -> None:
        for key in ("NAS_BACKUP_UNC", "REPORT_MARK_BACKUP_MOUNT"):
            with self.subTest(key=key):
                self.assertIn(key, self.live, "主機專屬值不該寫死在 repo")


class BackupDestinationTests(unittest.TestCase):
    """環境檔範例必須指向一個實際寫得進去的 share，且標明落點是臨時的。"""

    def setUp(self) -> None:
        self.text = _read(SYNC_ENV_EXAMPLE)
        self.live = "\n".join(_live_lines(self.text))

    def test_backup_unc_is_set_and_not_the_read_only_share(self) -> None:
        self.assertIn("NAS_BACKUP_UNC=", self.live)
        unc = next(
            ln.split("=", 1)[1] for ln in _live_lines(self.text) if "NAS_BACKUP_UNC=" in ln
        )
        self.assertNotIn("投資研究處", unc, "該 share 唯讀（伺服器端 ACL）")

    def test_interim_destination_is_flagged_as_interim(self) -> None:
        """落點目前在一個名為「會後刪除」的暫存區——那是刻意的過渡，但必須寫明，
        否則下一個人會以為那裡是永久位置。備份內容（qa_log／report_takeaway 等）不可重建。"""
        backup_dir = next(
            (ln for ln in _live_lines(self.text) if "REPORT_MARK_BACKUP_DIR=" in ln), ""
        )
        if "會後刪除" in backup_dir:
            self.assertIn(
                "臨時",
                self.text,
                "落點在會被清掉的暫存區時，環境檔必須明寫這是臨時安排",
            )


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

    def test_both_scripts_share_the_single_docker_detection(self) -> None:
        """兩支腳本必須共用 `scripts/_docker_bin.sh`，不得各自寫一份偵測。

        兩套邏輯並存的實際後果：2026-07-30 備份在 `pg_isready` 掛掉，因為它挑到
        `/usr/bin/docker`——**那支存在但連不到 daemon**（Docker Desktop 的 WSL
        integration 沒開給這個 distro），而 `command -v` 只看檔案存不存在。
        `Makefile:15` 從一開始就用實際探測，只有這兩支 shell 用了弱版。
        """
        for name, path in (("db_backup.sh", BACKUP_SH), ("ingest_lowio.sh", LOWIO_SH)):
            live = "\n".join(_live_lines(_read(path)))
            with self.subTest(script=name):
                self.assertIn("_docker_bin.sh", live, f"{name} 應 source 共用偵測")
                self.assertIn("detect_docker_bin", live, f"{name} 應呼叫共用偵測")
                self.assertNotIn(
                    "command -v docker ||", live,
                    f"{name} 不該再自帶弱版偵測（只看檔案存不存在）",
                )

    def test_destination_falls_back_through_defaults_file(self) -> None:
        """腳本自己要讀 /etc/default/report-mark-sync。

        systemd 的 `EnvironmentFile=` 只在 unit 執行時生效，手動 `make db-backup`
        不會載入——2026-07-30 就因此讓兩條路徑寫到不同地方（詳見
        ConfigResolutionRuntimeTests 的 docstring）。
        """
        self.assertIn("DEFAULTS_FILE", self.live, "缺設定檔來源")
        self.assertIn("_default_key REPORT_MARK_BACKUP_DIR", self.live)
        for forbidden in ("source ", '. "$DEFAULTS_FILE"', ". $DEFAULTS_FILE"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(
                    forbidden, self.live,
                    "設定檔的值含括號，source 會 syntax error；請逐鍵取值",
                )

    def test_no_command_substitution_that_set_e_would_abort(self) -> None:
        """`X="$(可能失敗的指令)"` 在 set -e 下會讓腳本就地中止。

        原本的 mkdir 診斷就是這樣寫的，結果那段 EROFS/EACCES 分辨一行都沒印出來
        （2026-07-30 實測）。要取回退出碼就得把 `|| RC=$?` 掛在賦值上。
        """
        self.assertNotRegex(
            self.live,
            r'^\s*\w+="\$\(mkdir[^)]*\)"\s*$',
            "命令替換裡的 mkdir 失敗會被 set -e 吃掉整個腳本，診斷訊息永遠印不出來",
        )
        self.assertRegex(
            self.live,
            r'MKDIR_ERR="\$\(mkdir[^)]*\)"\s*\|\|\s*MKDIR_RC=\$\?',
            "取 stderr 與退出碼要用同一次呼叫，且 `|| RC=$?` 掛在賦值上",
        )

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
            r'BACKUP_MOUNT:=/mnt/',
            "預設落點應在掛載點底下（/mnt/…），不是本機工作目錄",
        )

    def test_retention_policy_is_seven_daily_four_weekly(self) -> None:
        self.assertRegex(self.live, r"KEEP_DAILY:=7\b", "日備保留數應預設 7")
        self.assertRegex(self.live, r"KEEP_WEEKLY:=4\b", "週備保留數應預設 4")
        # 保留數同樣要能由設定檔覆寫，否則 unit 與手動跑的輪替深度會不一致
        self.assertIn("_default_key BACKUP_KEEP_DAILY", self.live)
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
