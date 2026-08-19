# tests/test_incident_handler.py
"""事件偵測與通知（P5）的狀態機與契約守門。

為什麼要有這一支：2026-08-18 的中斷持續 4 小時 50 分，期間告警機制**確實運作了**
——`data/unit_failures.log` 累積 854 筆完整紀錄——但那個檔案沒有任何消費端，
中斷是在一次無關的遷移工作中被順帶發現的。854 筆同源紀錄同時說明另一件事：
**沒有去重就等於沒有告警**。

這裡守的核心是「一次事件只產生一則 FIRING」。那條規約壞掉時完全沒有症狀：
通知照送、狀態檔照寫，只是收件者會在第一週就學會忽略它。

測試以真實的 systemd unit 作為觀測來源不可行（需要安裝 unit 且等待排程），
因此改用 fake `systemctl`：把一個假二進位放進 PATH 前段，讓腳本讀到受控的
Result／ExecMainStatus／ExecMainExitTimestampMonotonic。這是刻意的——
狀態機的正確性不該依賴機器上剛好有什麼 unit。
"""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
HANDLER = REPO_ROOT / "scripts" / "incident_handler.sh"
SYSTEMD_DIR = REPO_ROOT / "deploy" / "systemd"
SERVICE = SYSTEMD_DIR / "report-mark-incident.service"
TIMER = SYSTEMD_DIR / "report-mark-incident.timer"


def _directives(path: Path, key: str) -> list[str]:
    """取出 unit 內所有 `key=` 的值，**略過註解行**。

    刻意不做整檔子字串比對：本專案的 unit 註解會解釋「為什麼不設某個 directive」，
    文字比對會把那些解釋當成違規——那是假守門（擋不住真設定，卻被正確的文件觸發）。
    """
    out: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or "=" not in stripped:
            continue
        name, _, value = stripped.partition("=")
        if name.strip() == key:
            out.append(value.strip())
    return out


class _Harness:
    """建立臨時的 state dir、fake systemctl 與 fake curl。"""

    def __init__(self, exit_status=0, result="success", mono=None, webhook=None):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.state_dir = self.root / "incidents"
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.webhook_log = self.root / "webhook.log"
        self.exit_status = exit_status
        self.result = result
        # 預設用「現在」的單調時鐘，讓觀測看起來是新鮮的
        self.mono = mono if mono is not None else self._now_mono()
        self.webhook = webhook
        self._write_fakes()

    @staticmethod
    def _now_mono() -> int:
        with open("/proc/uptime") as f:
            return int(float(f.read().split()[0]) * 1_000_000)

    def _write_fakes(self):
        sc = self.bin / "systemctl"
        sc.write_text(
            "#!/usr/bin/env bash\n"
            'for a in "$@"; do case "$a" in\n'
            f'  -p) ;; Result) echo "{self.result}"; exit 0 ;;\n'
            f'  ExecMainStatus) echo "{self.exit_status}"; exit 0 ;;\n'
            f'  ExecMainExitTimestampMonotonic) echo "{self.mono}"; exit 0 ;;\n'
            "esac; done\n"
            "exit 0\n",
            encoding="utf-8",
        )
        sc.chmod(0o755)
        curl = self.bin / "curl"
        curl.write_text(
            "#!/usr/bin/env bash\n"
            f'echo "$@" >> "{self.webhook_log}"\n'
            "exit 0\n",
            encoding="utf-8",
        )
        curl.chmod(0o755)

    def run(self, **env_extra):
        env = dict(os.environ)
        env["PATH"] = f"{self.bin}:{env['PATH']}"
        env["INCIDENT_STATE_DIR"] = str(self.state_dir)
        env["INCIDENT_COMPONENT"] = "web"
        env.pop("REPORT_MARK_ALERT_WEBHOOK", None)
        if self.webhook:
            env["REPORT_MARK_ALERT_WEBHOOK"] = self.webhook
        env.update(env_extra)
        return subprocess.run(
            ["bash", str(HANDLER)], capture_output=True, text=True, env=env, timeout=60
        )

    def set_probe(self, exit_status, result="exit-code", bump_mono=True):
        self.exit_status = exit_status
        self.result = result
        if bump_mono:
            self.mono += 120_000_000  # 模擬 P4 又跑了一輪（+2 分鐘）
        self._write_fakes()

    def seed_state(self, text: str):
        self.state_dir.mkdir(parents=True, exist_ok=True)
        (self.state_dir / "web.state").write_text(text, encoding="utf-8")

    @staticmethod
    def boot_id() -> str:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()

    @property
    def state(self) -> dict:
        f = self.state_dir / "web.state"
        if not f.is_file():
            return {}
        return dict(
            ln.split("=", 1) for ln in f.read_text(encoding="utf-8").splitlines() if "=" in ln
        )

    def webhook_calls(self) -> int:
        if not self.webhook_log.exists():
            return 0
        return len([ln for ln in self.webhook_log.read_text(encoding="utf-8").splitlines() if ln.strip()])

    def close(self):
        self.tmp.cleanup()


def parse(line: str) -> dict:
    return dict(kv.split("=", 1) for kv in line.strip().split() if "=" in kv)


def last_emit(stdout: str) -> dict:
    lines = [ln for ln in stdout.splitlines() if "handler=incident" in ln]
    return parse(lines[-1]) if lines else {}


class StateMachineTests(unittest.TestCase):
    def setUp(self):
        self.h = _Harness(webhook="http://example.invalid/hook")
        self.addCleanup(self.h.close)

    def test_healthy_with_no_incident_is_noop(self):
        p = self.h.run()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(last_emit(p.stdout)["action"], "noop")
        self.assertEqual(self.h.webhook_calls(), 0)

    def test_first_failure_opens_incident_and_notifies_once(self):
        self.h.set_probe(1)
        p = self.h.run()
        e = last_emit(p.stdout)
        self.assertEqual(e["action"], "firing")
        self.assertEqual(e["severity"], "CRITICAL")
        self.assertEqual(self.h.state["state"], "FIRING")
        self.assertEqual(self.h.webhook_calls(), 1)

    def test_repeated_failure_before_reminder_sends_nothing(self):
        """**本缺陷的回歸測試**：854 次失敗必須只產生 1 則通知。"""
        self.h.set_probe(1)
        self.h.run()
        for _ in range(10):
            self.h.set_probe(1)
            p = self.h.run()
            self.assertEqual(last_emit(p.stdout)["action"], "suppress")
        self.assertEqual(self.h.webhook_calls(), 1, "去重失效：進行中的事件重複通知")
        self.assertEqual(int(self.h.state["count"]), 11)

    def test_reminder_fires_after_interval(self):
        self.h.set_probe(1)
        self.h.run()
        self.h.set_probe(1)
        p = self.h.run(INCIDENT_REMINDER_SECONDS="0")   # 提醒立刻到期
        self.assertEqual(last_emit(p.stdout)["action"], "reminder")
        self.assertEqual(self.h.webhook_calls(), 2)

    def test_recovery_closes_incident_and_notifies_once(self):
        self.h.set_probe(1)
        self.h.run()
        self.h.set_probe(0, result="success")
        p = self.h.run()
        e = last_emit(p.stdout)
        self.assertEqual(e["action"], "resolved")
        self.assertEqual(e["incident"], "CLOSED")
        self.assertEqual(self.h.state, {}, "RESOLVED 後狀態檔應被移除")
        self.assertEqual(self.h.webhook_calls(), 2)

    def test_recovery_when_no_incident_is_noop(self):
        p = self.h.run()
        self.assertEqual(last_emit(p.stdout)["action"], "noop")
        self.assertEqual(self.h.webhook_calls(), 0)

    def test_probe_grace_exit_counts_as_healthy(self):
        """P4 的 exit 3＝啟動寬限，不是故障——不得因此開事件。"""
        self.h.set_probe(3, result="success")
        p = self.h.run()
        self.assertEqual(last_emit(p.stdout)["action"], "noop")
        self.assertEqual(self.h.webhook_calls(), 0)

    def test_probe_tooling_failure_is_warning_not_critical(self):
        """exit 4＝探針自己壞了＝「我不知道」，與「服務壞了」是不同的事。"""
        self.h.set_probe(4)
        p = self.h.run()
        self.assertEqual(last_emit(p.stdout)["severity"], "WARNING")

    def test_stale_probe_is_its_own_warning(self):
        """P4 停止產出＝監控失明，必須有自己的訊號，不能靜默。"""
        self.h.mono = 1_000_000          # 開機後 1 秒，距今必然很久
        self.h._write_fakes()
        p = self.h.run(INCIDENT_STALE_SECONDS="60")
        e = last_emit(p.stdout)
        self.assertEqual(e["reason"], "probe_stale")
        self.assertEqual(e["severity"], "WARNING")

    def test_probe_never_ran_is_noop_not_incident(self):
        """P4 尚未部署時不得誤報成故障。"""
        self.h.mono = 0
        self.h._write_fakes()
        p = self.h.run()
        self.assertEqual(last_emit(p.stdout)["reason"], "probe_never_ran")
        self.assertEqual(self.h.webhook_calls(), 0)

    def test_same_observation_does_not_double_count(self):
        """P5 的 timer 比 P4 快或抖動時，同一次探測結果不得被算兩次。"""
        self.h.set_probe(1)
        self.h.run()
        self.h.set_probe(1, bump_mono=False)   # 同一次觀測
        self.h.run()
        self.assertEqual(int(self.h.state["count"]), 1)


class RobustnessTests(unittest.TestCase):
    def setUp(self):
        self.h = _Harness(webhook="http://example.invalid/hook")
        self.addCleanup(self.h.close)

    def test_malformed_state_recovers_fail_open(self):
        """狀態檔損毀時寧可多送一則，不要靜默漏送。"""
        self.h.set_probe(1)
        self.h.run()
        (self.h.state_dir / "web.state").write_text("state=GARBAGE\ncount=NOT_A_NUMBER\n", encoding="utf-8")
        self.h.set_probe(1)
        p = self.h.run()
        self.assertEqual(p.returncode, 0)
        self.assertEqual(last_emit(p.stdout)["action"], "firing")

    def test_state_file_is_not_sourced(self):
        """狀態檔以 `source` 讀取＝任意程式碼執行。必須逐鍵解析。"""
        body = "\n".join(
            ln for ln in HANDLER.read_text(encoding="utf-8").splitlines()
            if not ln.strip().startswith("#")
        )
        self.assertNotIn(". \"$STATE_FILE\"", body)
        self.assertNotIn("source ", body)
        self.assertIn("while IFS='='", body)

    def test_atomic_write_via_rename(self):
        """中途崩潰只能留下舊檔或新檔，不得有寫到一半的狀態。"""
        body = HANDLER.read_text(encoding="utf-8")
        self.assertIn(".tmp.$$", body)
        self.assertIn("mv -f", body)

    def test_concurrent_invocation_is_serialised(self):
        body = HANDLER.read_text(encoding="utf-8")
        self.assertIn("flock", body)

    def test_webhook_unset_still_maintains_state(self):
        """webhook 未設定時仍要維護狀態，否則日後設定當下會湧出一批補送。"""
        h = _Harness(webhook=None)
        self.addCleanup(h.close)
        h.set_probe(1)
        p = h.run()
        self.assertEqual(last_emit(p.stdout)["action"], "firing")
        self.assertEqual(h.state["state"], "FIRING")
        self.assertEqual(h.webhook_calls(), 0)

    def test_webhook_failure_does_not_corrupt_state(self):
        """投遞失敗不得讓狀態機停滯——否則下一輪會重送，變成投遞端故障時的風暴。"""
        h = _Harness(webhook="http://example.invalid/hook")
        self.addCleanup(h.close)
        h.set_probe(1)
        # **必須在 set_probe 之後覆寫**——set_probe 會重寫全部 fake（含 curl）。
        # 初版把這兩步寫反了，於是「投遞失敗」其實從未發生，測試等於沒測到。
        (h.bin / "curl").write_text("#!/usr/bin/env bash\nexit 7\n", encoding="utf-8")
        (h.bin / "curl").chmod(0o755)
        p = h.run()
        self.assertEqual(p.returncode, 0)
        self.assertEqual(h.state["state"], "FIRING")
        self.assertEqual(last_emit(p.stdout)["notified"], "no")

    def test_uses_monotonic_clock_not_wall_clock_for_observations(self):
        """WSL 休眠喚醒與時區調整會讓牆鐘跳動；觀測游標必須用單調時鐘。"""
        body = HANDLER.read_text(encoding="utf-8")
        self.assertIn("ExecMainExitTimestampMonotonic", body)

    def test_multiple_components_tracked_independently(self):
        """v1 只監控 web，但資料模型不得硬編到未來無法擴展。"""
        h = _Harness(webhook="http://example.invalid/hook")
        self.addCleanup(h.close)
        h.set_probe(1)
        h.run(INCIDENT_COMPONENT="web")
        h.run(INCIDENT_COMPONENT="edge")
        self.assertTrue((h.state_dir / "web.state").is_file())
        self.assertTrue((h.state_dir / "edge.state").is_file())


class BoundaryTests(unittest.TestCase):
    """P5 不得重新實作 P4 的探測。"""

    def test_does_not_probe_healthz_itself(self):
        body = "\n".join(
            ln for ln in HANDLER.read_text(encoding="utf-8").splitlines()
            if not ln.strip().startswith("#")
        )
        self.assertNotIn("/healthz", body, "P5 應消費 P4 的訊號，不得建立平行探測")
        self.assertNotIn("8097", body)

    def test_does_not_depend_on_python_or_venv(self):
        body = "\n".join(
            ln for ln in HANDLER.read_text(encoding="utf-8").splitlines()
            if not ln.strip().startswith("#")
        )
        for forbidden in ("uv run", ".venv", "python3 ", "python "):
            with self.subTest(token=forbidden):
                self.assertNotIn(forbidden, body)

    def test_does_not_import_application_code(self):
        body = HANDLER.read_text(encoding="utf-8")
        for forbidden in ("app.services", "app.config", "web.server"):
            with self.subTest(token=forbidden):
                self.assertNotIn(forbidden, body)


class SystemdContractTests(unittest.TestCase):
    def test_unit_files_exist(self):
        self.assertTrue(SERVICE.is_file())
        self.assertTrue(TIMER.is_file())

    def test_execstart_points_at_an_existing_script(self):
        self.assertIn("incident_handler.sh", SERVICE.read_text(encoding="utf-8"))
        self.assertTrue((REPO_ROOT / "scripts" / "incident_handler.sh").is_file())

    def test_execstart_uses_bash_not_bare_exec(self):
        self.assertRegex(
            SERVICE.read_text(encoding="utf-8"), r"ExecStart=/usr/bin/bash -c 'exec /usr/bin/bash "
        )

    def test_handler_unit_declares_no_onfailure(self):
        """告警器失敗再觸發告警會遞迴，且 report-mark-alert@ 讀同一個 webhook 變數。"""
        self.assertEqual(_directives(SERVICE, "OnFailure"), [])

    def test_service_home_is_hardcoded(self):
        homes = [v for v in _directives(SERVICE, "Environment") if v.startswith("HOME=")]
        self.assertEqual(homes, ["HOME=/home/kashionz"])

    def test_service_path_excludes_nvm_and_uv(self):
        envs = [v for v in _directives(SERVICE, "Environment") if v.startswith("PATH=")]
        self.assertEqual(len(envs), 1)
        self.assertNotIn(".nvm", envs[0])
        self.assertNotIn(".local/bin", envs[0])

    def test_reads_env_file_for_webhook(self):
        self.assertIn("-/etc/default/report-mark-sync", _directives(SERVICE, "EnvironmentFile"))

    def test_timeout_is_strictly_below_timer_interval(self):
        timeout = int(_directives(SERVICE, "TimeoutStartSec")[0])
        interval = _directives(TIMER, "OnUnitActiveSec")[0]
        seconds = int(interval.rstrip("min")) * 60
        self.assertLess(timeout, seconds)

    def test_timer_is_not_persistent(self):
        self.assertEqual(_directives(TIMER, "Persistent"), [])

    def test_state_dir_is_gitignored(self):
        self.assertIn("data/.incidents/", (REPO_ROOT / ".gitignore").read_text(encoding="utf-8"))


class ObservationIdentityTests(unittest.TestCase):
    """新觀測的判定必須在 reboot 之後仍然正確。

    ExecMainExitTimestampMonotonic 的 epoch 是「本次開機」。重開機後狀態檔裡的
    值來自上一個 boot，與新 boot 的值不可比——兩者相等就會被誤判成「同一次觀測」，
    使該輪的失敗不被計數。機率極低（要撞到同一微秒），但這個不變量應該是明示且
    可驗證的，而不是靠運氣。
    """

    def setUp(self):
        self.h = _Harness(webhook="http://example.invalid/hook")
        self.addCleanup(self.h.close)

    def test_state_records_boot_id(self):
        self.h.set_probe(1)
        self.h.run()
        self.assertEqual(self.h.state.get("boot_id"), self.h.boot_id())

    def test_monotonic_from_previous_boot_is_not_reused(self):
        """同一個 monotonic 值，但 boot_id 不同 ⇒ 必須算成新觀測。"""
        self.h.set_probe(1)
        self.h.seed_state(
            f"state=FIRING\nseverity=CRITICAL\nfirst_seen=1\n"
            f"last_notified={int(__import__('time').time())}\n"
            f"last_obs_monotonic={self.h.mono}\ncount=1\nboot_id=OLD-BOOT\n"
        )
        self.h.run()
        self.assertEqual(int(self.h.state["count"]), 2, "跨 boot 的 monotonic 被誤認成同一次觀測")

    def test_same_boot_same_monotonic_is_not_double_counted(self):
        self.h.set_probe(1)
        self.h.run()
        self.h.set_probe(1, bump_mono=False)
        self.h.run()
        self.assertEqual(int(self.h.state["count"]), 1)


class StrandedIncidentTests(unittest.TestCase):
    """**已開啟的事件不得被任何早退路徑擱置。**

    初版在「探針從未執行」（probe_mono=0）時直接早退，於是重開機後 P4 還沒跑第一輪、
    或 P4 的 timer 被停用，都會讓進行中的 FIRING 永遠卡住：不再有提醒、也不會 RESOLVED。
    對告警系統而言那等於靜音。
    """

    def setUp(self):
        self.h = _Harness(webhook="http://example.invalid/hook")
        self.addCleanup(self.h.close)

    def test_open_incident_survives_probe_never_ran(self):
        self.h.mono = 0
        self.h._write_fakes()
        self.h.seed_state(
            f"state=FIRING\nseverity=CRITICAL\nfirst_seen=1\nlast_notified=1\n"
            f"last_obs_monotonic=9\ncount=5\nboot_id={self.h.boot_id()}\n"
        )
        p = self.h.run()
        e = last_emit(p.stdout)
        self.assertEqual(p.returncode, 0)
        self.assertEqual(e["reason"], "probe_stale", "既有事件必須落到 stale 分支，不得早退")
        self.assertEqual(e["action"], "reminder")
        self.assertEqual(self.h.webhook_calls(), 1)

    def test_closed_state_with_probe_never_ran_is_still_noop(self):
        """沒有事件時（例如 P4 尚未部署）仍不得誤報。"""
        self.h.mono = 0
        self.h._write_fakes()
        p = self.h.run()
        self.assertEqual(last_emit(p.stdout)["reason"], "probe_never_ran")
        self.assertEqual(self.h.webhook_calls(), 0)

    def test_stale_incident_still_gets_reminders(self):
        """探針停掉後，第一則之後不得永遠靜音。"""
        self.h.mono = 1_000_000
        self.h._write_fakes()
        p1 = self.h.run(INCIDENT_STALE_SECONDS="60")
        self.assertEqual(last_emit(p1.stdout)["action"], "firing")
        p2 = self.h.run(INCIDENT_STALE_SECONDS="60", INCIDENT_REMINDER_SECONDS="0")
        self.assertEqual(last_emit(p2.stdout)["action"], "reminder")
        self.assertEqual(self.h.webhook_calls(), 2)


class ClockSafetyTests(unittest.TestCase):
    def setUp(self):
        self.h = _Harness(webhook="http://example.invalid/hook")
        self.addCleanup(self.h.close)

    def test_future_last_notified_does_not_silence_reminders(self):
        """牆鐘回跳（WSL 休眠喚醒／NTP 校正）或損毀成未來時間戳，會讓
        `now - last_notified` 變成負數而永遠小於門檻 ⇒ 提醒永遠不觸發、事件無聲卡住。
        """
        self.h.set_probe(1)
        self.h.seed_state(
            f"state=FIRING\nseverity=CRITICAL\nfirst_seen=1\nlast_notified=9999999999\n"
            f"last_obs_monotonic=1\ncount=1\nboot_id={self.h.boot_id()}\n"
        )
        p = self.h.run()
        self.assertEqual(last_emit(p.stdout)["action"], "reminder")
        self.assertEqual(self.h.webhook_calls(), 1)


class MaliciousStateTests(unittest.TestCase):
    """狀態檔是本程式自己寫的，但**必須假設它會損毀或被人手改**。

    以 `source` 讀取等於任意程式碼執行；未驗證的數值欄位進入算術展開，
    輕則整支腳本靜默死亡（實測 rc=1、stdout/stderr 全空），重則是注入面。
    """

    def setUp(self):
        self.h = _Harness(webhook="http://example.invalid/hook")
        self.addCleanup(self.h.close)

    def _assert_no_exec(self, payload_state):
        marker = Path("/tmp/p5_pwned_marker")
        marker.unlink(missing_ok=True)
        self.h.set_probe(1)
        self.h.seed_state(payload_state)
        p = self.h.run(INCIDENT_REMINDER_SECONDS="0")
        self.assertFalse(marker.exists(), "狀態檔內容被當成程式碼執行")
        self.assertEqual(p.returncode, 0, f"損毀的狀態不得讓 handler 死掉\n{p.stdout}\n{p.stderr}")
        self.assertTrue(p.stdout.strip(), "handler 必須仍有結構化輸出")
        return p

    def test_command_substitution_in_value_is_inert(self):
        self._assert_no_exec(
            'state=FIRING\nseverity=$(touch /tmp/p5_pwned_marker)\n'
            'first_seen=1\nlast_notified=0\nlast_obs_monotonic=1\ncount=0\n'
        )

    def test_backticks_in_value_is_inert(self):
        self._assert_no_exec(
            'state=FIRING\nseverity=CRITICAL\n'
            'first_seen=`touch /tmp/p5_pwned_marker`\n'
            'last_notified=0\nlast_obs_monotonic=1\ncount=0\n'
        )

    def test_non_numeric_first_seen_does_not_kill_handler(self):
        """**實測過的靜默死亡**：first_seen 是唯一漏做數值驗證的欄位。"""
        p = self._assert_no_exec(
            'state=FIRING\nseverity=CRITICAL\nfirst_seen=NOT_A_NUMBER\n'
            'last_notified=0\nlast_obs_monotonic=1\ncount=0\n'
        )
        self.assertIn("handler=incident", p.stdout)

    def test_quote_injection_does_not_break_emit_line(self):
        self.h.set_probe(1)
        self.h.seed_state(
            'state=FIRING\nseverity="; rm -rf /\nfirst_seen=1\n'
            'last_notified=0\nlast_obs_monotonic=1\ncount=0\n'
        )
        p = self.h.run(INCIDENT_REMINDER_SECONDS="0")
        self.assertEqual(p.returncode, 0)
        # severity 是封閉詞彙，損毀值必須被丟棄而不是原樣輸出
        self.assertNotIn("rm -rf", last_emit(p.stdout).get("severity", ""))

    def test_all_numeric_fields_are_validated(self):
        """靜態：每個進入算術展開的欄位都要有數值驗證。"""
        body = HANDLER.read_text(encoding="utf-8")
        for field in ("st_first_seen", "st_last_notified", "st_last_obs_monotonic", "st_count"):
            with self.subTest(field=field):
                self.assertRegex(body, rf'case "\${field}" in')


class ConcurrencyTests(unittest.TestCase):
    def test_parallel_handlers_produce_one_firing(self):
        """反轉可驗的核心守門：兩個 handler 同時看到 CLOSED 不得都發 FIRING。"""
        h = _Harness(webhook="http://example.invalid/hook")
        self.addCleanup(h.close)
        h.set_probe(1)
        env = dict(os.environ)
        env["PATH"] = f"{h.bin}:{env['PATH']}"
        env["INCIDENT_STATE_DIR"] = str(h.state_dir)
        env["INCIDENT_COMPONENT"] = "web"
        env["REPORT_MARK_ALERT_WEBHOOK"] = h.webhook
        h.state_dir.mkdir(parents=True, exist_ok=True)
        procs = [
            subprocess.Popen(["bash", str(HANDLER)], stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True, env=env)
            for _ in range(6)
        ]
        for p in procs:
            p.wait(timeout=60)
        self.assertEqual(h.webhook_calls(), 1, "並發下重複發出 FIRING（flock 未涵蓋讀-改-寫）")


if __name__ == "__main__":
    unittest.main()
