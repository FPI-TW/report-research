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

    def __init__(self, exit_status=0, result="success", mono=None, webhook=None,
                 timer_enabled="enabled", timer_active="active",
                 timer_load="loaded", service_load="loaded",
                 timer_enter_mono=0, show_rc=0, probe_state="inactive"):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.state_dir = self.root / "incidents"
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.webhook_log = self.root / "webhook.log"
        self.exit_status = exit_status
        self.result = result
        # 訊號源（P4 timer）的狀態。預設是「健康且已跑很久」——絕大多數測試關心的是
        # web 的狀態機，不該每一條都被迫宣告 timer；但**它仍是被明確宣告的**，
        # 不是沿用執行主機的真實 systemd（那正是 P4 測試踩過的坑）。
        self.timer_enabled = timer_enabled
        self.timer_active = timer_active
        self.timer_load = timer_load
        self.service_load = service_load
        # 0 = timer 老早就 active（bootstrap 空窗早已過完）
        self.timer_enter_mono = timer_enter_mono
        self.show_rc = show_rc
        # 被觀測 unit 的 ActiveState。預設 inactive＝探針不在執行中（絕大多數情境）；
        # activating＝ExecStart 正在跑，此時 systemd 已把 ExecMain* 歸零。
        self.probe_state = probe_state
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
        # 受控的 systemctl：**同時**表達 service 的執行結果與 timer 的存活狀態。
        # 兩者缺一不可——2026-08-19 的實測顯示 timer 一旦 disabled，service 的
        # ExecMain* 會當場被 GC 清空，只看 service 那一維根本分不出「還沒跑」與
        # 「監控被停掉」。
        sc.write_text(
            "#!/usr/bin/env bash\n"
            'verb="$1"; shift\n'
            'unit="${1:-}"\n'
            'case "$verb" in\n'
            f'  is-enabled) printf \'%s\\n\' "{self.timer_enabled}"\n'
            f'    [ "{self.timer_enabled}" = enabled ] && exit 0 || exit 1 ;;\n'
            f'  is-active) printf \'%s\\n\' "{self.timer_active}"\n'
            f'    [ "{self.timer_active}" = active ] && exit 0 || exit 3 ;;\n'
            "esac\n"
            f'[ "{self.show_rc}" -ne 0 ] && exit {self.show_rc}\n'
            'for a in "$@"; do case "$a" in\n'
            "  LoadState)\n"
            '    case "$unit" in\n'
            f'      *.timer) printf \'%s\\n\' "{self.timer_load}" ;;\n'
            f'      *)       printf \'%s\\n\' "{self.service_load}" ;;\n'
            "    esac; exit 0 ;;\n"
            f'  ActiveEnterTimestampMonotonic) printf \'%s\\n\' "{self.timer_enter_mono}"; exit 0 ;;\n'
            "  ActiveState)\n"
            '    case "$unit" in\n'
            f'      *.timer) printf \'%s\\n\' "{self.timer_active}" ;;\n'
            f'      *)       printf \'%s\\n\' "{self.probe_state}" ;;\n'
            "    esac; exit 0 ;;\n"
            f'  Result) printf \'%s\\n\' "{self.result}"; exit 0 ;;\n'
            f'  ExecMainStatus) printf \'%s\\n\' "{self.exit_status}"; exit 0 ;;\n'
            f'  ExecMainExitTimestampMonotonic) printf \'%s\\n\' "{self.mono}"; exit 0 ;;\n'
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

    def set_timer(self, **kw):
        """翻轉訊號源那一維（enabled/active/load/enter_mono），其餘不動。"""
        for k, v in kw.items():
            setattr(self, k, v)
        self._write_fakes()

    def state_of(self, component: str) -> dict:
        f = self.state_dir / f"{component}.state"
        if not f.is_file():
            return {}
        return dict(
            ln.split("=", 1) for ln in f.read_text(encoding="utf-8").splitlines() if "=" in ln
        )

    @property
    def monitor_state(self) -> dict:
        return self.state_of("monitor")

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


def last_emit(stdout: str, component: str = "web") -> dict:
    """取指定元件的最後一行。

    一輪現在會輸出兩行（monitor 一行、web 一行），因為訊號源本身也是被監控的對象。
    預設取 web，讓既有的服務狀態機斷言維持原意。
    """
    lines = [
        ln for ln in stdout.splitlines()
        if "handler=incident" in ln and f"component={component} " in ln
    ]
    return parse(lines[-1]) if lines else {}


def monitor_emit(stdout: str) -> dict:
    return last_emit(stdout, "monitor")


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

    def test_stale_probe_is_a_monitor_incident_not_a_web_incident(self):
        """P4 停止產出＝**監控失明**，不是服務故障。兩者必須是不同元件的事件。"""
        self.h.mono = 1_000_000          # 開機後 1 秒，距今必然很久
        self.h._write_fakes()
        p = self.h.run(INCIDENT_STALE_SECONDS="60", INCIDENT_BLIND_CRITICAL_SECONDS="99999999")
        m = monitor_emit(p.stdout)
        self.assertEqual(m["status"], "monitor_blind")
        self.assertEqual(m["reason"], "observation_stale")
        self.assertEqual(m["severity"], "WARNING")
        # web 的狀態機這一輪不得被推進——我們對 web 一無所知
        w = last_emit(p.stdout)
        self.assertEqual(w["action"], "skip")
        self.assertEqual(w["reason"], "signal_observation_stale")

    def test_long_blindness_escalates_to_critical(self):
        """「暫時看不見」惡化成「很久看不見」必須升級，而且不能等 30 分鐘的提醒週期。"""
        self.h.mono = 1_000_000
        self.h._write_fakes()
        p1 = self.h.run(INCIDENT_STALE_SECONDS="60", INCIDENT_BLIND_CRITICAL_SECONDS="99999999")
        self.assertEqual(monitor_emit(p1.stdout)["severity"], "WARNING")
        p2 = self.h.run(INCIDENT_STALE_SECONDS="60", INCIDENT_BLIND_CRITICAL_SECONDS="60")
        m = monitor_emit(p2.stdout)
        self.assertEqual(m["action"], "escalated")
        self.assertEqual(m["severity"], "CRITICAL")
        self.assertEqual(self.h.webhook_calls(), 2, "升級必須立刻送，不得被去重吞掉")

    def test_no_observation_within_bootstrap_is_not_an_incident(self):
        """剛開機／剛 enable、還沒跑第一輪：不得誤報。"""
        self.h.mono = 0
        # timer 剛進入 active（單調時鐘上就在剛才）
        self.h.set_timer(timer_enter_mono=self.h._now_mono())
        p = self.h.run()
        m = monitor_emit(p.stdout)
        self.assertEqual(m["status"], "bootstrap")
        self.assertEqual(m["reason"], "awaiting_first_probe")
        self.assertEqual(m["action"], "noop")
        self.assertEqual(self.h.webhook_calls(), 0)

    def test_no_observation_beyond_bootstrap_is_monitor_blind(self):
        """**這是初版最危險的那條路徑。**

        timer 看似正常卻始終沒有觀測，超過合理空窗就不能再說「還沒跑」。
        初版在這裡 `emit noop probe_never_ran` 並早退——監控已經死了，
        而 P5 每 2 分鐘回報一次沒事。

        **空窗上限必須明確宣告，不能靠「機器已經開機很久」。**
        初版寫 `timer_enter_mono=1` 就交差，於是 `bootstrap_age` 實際上等於宿主的
        uptime——在開發機（uptime 數十小時）遠超 300 秒預設而通過，在剛開機的
        CI runner（uptime 數十秒）卻落在空窗內而被正確判成 bootstrap，測試因此變紅。
        腳本沒問題，是測試繼承了一個沒有被宣告的維度。這裡把上限壓到 1 秒：
        任何跑得動測試的機器 uptime 都大於 1 秒，判定於是與 uptime 無關。
        """
        self.h.mono = 0
        self.h.set_timer(timer_enter_mono=1)   # timer 自開機起就 active
        p = self.h.run(INCIDENT_BOOTSTRAP_SECONDS="1")
        m = monitor_emit(p.stdout)
        self.assertEqual(m["status"], "monitor_blind", p.stdout)
        self.assertEqual(m["reason"], "observation_missing")
        self.assertEqual(m["severity"], "CRITICAL")
        self.assertEqual(m["action"], "firing")
        self.assertEqual(self.h.webhook_calls(), 1)

    def test_bootstrap_classification_does_not_depend_on_host_uptime(self):
        """釘死上一條的教訓：同一組輸入，只改空窗上限，分類必須跟著翻轉。

        若判定仍受宿主 uptime 影響，這兩個斷言不可能同時成立。
        """
        self.h.mono = 0
        self.h.set_timer(timer_enter_mono=1)
        blind = monitor_emit(self.h.run(INCIDENT_BOOTSTRAP_SECONDS="1").stdout)
        self.assertEqual(blind["status"], "monitor_blind")
        h2 = _Harness(webhook="http://example.invalid/hook", mono=0, timer_enter_mono=1)
        self.addCleanup(h2.close)
        boot = monitor_emit(h2.run(INCIDENT_BOOTSTRAP_SECONDS="99999999").stdout)
        self.assertEqual(boot["status"], "bootstrap")

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

    def test_open_web_incident_is_preserved_when_signal_is_lost(self):
        """訊號消失時，進行中的 web 事件**既不得被解除、也不得被覆蓋**。

        「我看不見了」不是「已經好了」。把它當成恢復，會在真正的中斷中途送出
        RESOLVED——那比完全不告警更糟，因為它會讓人停止調查。
        """
        self.h.mono = 0
        self.h.set_timer(timer_enabled="disabled", timer_active="inactive")
        self.h.seed_state(
            f"state=FIRING\nseverity=CRITICAL\nfirst_seen=1\nlast_notified=1\n"
            f"last_obs_monotonic=9\ncount=5\nboot_id={self.h.boot_id()}\n"
        )
        p = self.h.run()
        self.assertEqual(p.returncode, 0)
        w = last_emit(p.stdout)
        self.assertEqual(w["action"], "skip")
        self.assertEqual(w["incident"], "FIRING", "web 事件必須仍然可見")
        self.assertEqual(self.h.state["state"], "FIRING")
        self.assertEqual(self.h.state["count"], "5", "web 狀態不得被監控事件改寫")
        # 而失明本身必須大聲說出來
        m = monitor_emit(p.stdout)
        self.assertEqual(m["status"], "monitor_blind")
        self.assertEqual(m["reason"], "timer_disabled")

    def test_monitor_blind_still_gets_reminders(self):
        """監控失明後，第一則之後不得永遠靜音。"""
        self.h.mono = 1_000_000
        self.h._write_fakes()
        p1 = self.h.run(INCIDENT_STALE_SECONDS="60", INCIDENT_BLIND_CRITICAL_SECONDS="99999999")
        self.assertEqual(monitor_emit(p1.stdout)["action"], "firing")
        p2 = self.h.run(INCIDENT_STALE_SECONDS="60", INCIDENT_BLIND_CRITICAL_SECONDS="99999999",
                        INCIDENT_REMINDER_SECONDS="0")
        self.assertEqual(monitor_emit(p2.stdout)["action"], "reminder")
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


class MonitorSignalTests(unittest.TestCase):
    """**監控自己的監控。**

    P5 的訊號來源是 P4 留在 systemd 裡的執行結果。2026-08-19 的生產部署實測發現
    那個來源會消失：成功的 oneshot 在沒有引用時會被 GC，`ExecMainExitTimestampMonotonic`
    歸 0，輸出與「從未執行過」**逐欄相同**；而 `systemctl disable` 那個 timer
    會當場造成這件事。初版對此的反應是 `emit noop probe_never_ran` 並早退——
    監控已經死了，P5 卻每 2 分鐘回報一次沒事。

    這個類別釘死的不變量是：**訊號缺席永遠不得被當成健康。**
    """

    def setUp(self):
        self.h = _Harness(webhook="http://example.invalid/hook")
        self.addCleanup(self.h.close)

    # ── 訊號源健康時 ─────────────────────────────────────────────────────
    def test_healthy_timer_with_fresh_probe_is_normal(self):
        p = self.h.run()
        m = monitor_emit(p.stdout)
        self.assertEqual(m["status"], "ok")
        self.assertEqual(m["action"], "noop")
        self.assertEqual(last_emit(p.stdout)["status"], "ok")
        self.assertEqual(self.h.webhook_calls(), 0)

    # ── 訊號源壞掉的五種形態 ─────────────────────────────────────────────
    def _assert_blind(self, reason, severity="CRITICAL", **timer_kw):
        self.h.set_timer(**timer_kw)
        p = self.h.run()
        m = monitor_emit(p.stdout)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(m["status"], "monitor_blind", p.stdout)
        self.assertEqual(m["reason"], reason)
        self.assertEqual(m["severity"], severity)
        self.assertEqual(m["action"], "firing")
        self.assertEqual(self.h.webhook_calls(), 1)
        return p

    def test_timer_disabled_is_monitor_blind(self):
        # disable 會讓 systemd 回收 service 的執行結果——mono 一併歸 0，正如實測
        self._assert_blind("timer_disabled", timer_enabled="disabled",
                           timer_active="inactive", mono=0)

    def test_timer_inactive_but_enabled_is_monitor_blind(self):
        """enabled 但被 stop：實測顯示觀測值仍在，所以只看觀測新鮮度會漏判。

        timer 停著就不會再有新觸發，觀測會慢慢變舊——但在變舊之前有一整段時間
        看起來完全正常。必須直接檢查 is-active，不能等它過期。
        """
        self._assert_blind("timer_inactive", timer_active="inactive")

    def test_timer_missing_is_monitor_blind(self):
        self._assert_blind("timer_not_found", timer_enabled="not-found",
                           timer_load="not-found")

    def test_service_missing_is_monitor_blind(self):
        self._assert_blind("service_not_found", service_load="not-found")

    def test_systemctl_query_failure_is_monitor_blind_warning(self):
        """查詢失敗是「我不知道」，不是「壞了」——WARNING 而非 CRITICAL。"""
        self._assert_blind("query_failed", severity="WARNING", show_rc=1)

    def test_missing_systemctl_is_tooling_not_healthy(self):
        """systemctl 不存在時必須大聲失敗，不得落進任何 noop 分支。"""
        env = dict(os.environ)
        env["PATH"] = "/nonexistent"
        env["INCIDENT_STATE_DIR"] = str(self.h.state_dir)
        r = subprocess.run(["/usr/bin/bash", str(HANDLER)], capture_output=True,
                           text=True, env=env, timeout=60)
        self.assertEqual(r.returncode, 4)
        self.assertIn("status=tooling", r.stdout)
        self.assertIn("systemctl_not_found", r.stdout)

    # ── 探針執行中（2026-08-19 生產實測到的競態）─────────────────────────
    def test_probe_in_flight_is_not_monitor_blind(self):
        """**2026-08-19 部署當天實測到的真實故障。**

        P4 與 P5 的 timer 週期都是 2 分鐘，enable 的時機讓它們落在**同一秒**觸發
        （15:34:22、15:36:28 皆同秒）。systemd 在 oneshot 啟動時把 ExecMain* 歸零、
        結束才寫入，所以勝負由次秒級順序決定：P5 讀在 P4 完成之前拿到 0、判成
        observation_missing 並開 CRITICAL；下一輪讀在完成之後就 RESOLVED。
        結果是 FIRING↔RESOLVED 震盪——正好重現 P5 存在要消除的那件事。

        實測的執行中狀態：ActiveState=activating、SubState=start、
        ExecMainExitTimestampMonotonic=0。
        """
        self.h.set_timer(probe_state="activating", mono=0, timer_enter_mono=1)
        p = self.h.run()
        m = monitor_emit(p.stdout)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(m["status"], "in_flight", p.stdout)
        self.assertEqual(m["reason"], "probe_in_flight")
        self.assertEqual(m["action"], "noop", "執行中不得開事件")
        self.assertEqual(m["severity"], "none")
        self.assertEqual(self.h.webhook_calls(), 0, "執行中不得發任何通知")

    def test_probe_in_flight_skips_the_web_component(self):
        """這一輪對 web 一無所知，所以不碰它的狀態機。"""
        self.h.set_timer(probe_state="activating", mono=0, timer_enter_mono=1)
        w = last_emit(self.h.run().stdout)
        self.assertEqual(w["action"], "skip")
        self.assertEqual(w["reason"], "signal_probe_in_flight")

    def test_probe_in_flight_does_not_resolve_an_open_monitor_incident(self):
        """「正在跑」不等於「有一筆完成的觀測」，不足以解除進行中的失明事件。

        否則震盪會換一個方向發生：真的失明期間只要撞上一次執行中就被誤判成恢復。
        """
        self.h.state_dir.mkdir(parents=True, exist_ok=True)
        (self.h.state_dir / "monitor.state").write_text(
            f"state=FIRING\nseverity=CRITICAL\nfirst_seen=1\nlast_notified=1\n"
            f"last_obs_monotonic=0\ncount=3\nboot_id={self.h.boot_id()}\n",
            encoding="utf-8",
        )
        self.h.set_timer(probe_state="activating", mono=0, timer_enter_mono=1)
        p = self.h.run()
        m = monitor_emit(p.stdout)
        self.assertNotEqual(m["action"], "resolved", "執行中不得解除失明事件")
        self.assertEqual(self.h.monitor_state["state"], "FIRING")

    def test_in_flight_only_applies_while_actually_running(self):
        """反面：同樣 mono=0，但探針**不在**執行中，就必須是真的失明。

        這條與上面三條成對——少了它，把 in_flight 寫成無條件放行也會全綠。
        """
        self.h.set_timer(probe_state="inactive", mono=0, timer_enter_mono=1)
        m = monitor_emit(self.h.run().stdout)
        self.assertEqual(m["status"], "monitor_blind")
        self.assertEqual(m["reason"], "observation_missing")

    def test_timer_has_jitter_to_avoid_systematic_collision(self):
        """P5 的 timer 必須帶抖動：與 P4 同為 2 分鐘週期，實測會系統性同秒觸發。"""
        vals = _directives(TIMER, "RandomizedDelaySec")
        self.assertTrue(vals, "缺 RandomizedDelaySec，兩個 timer 會維持同相位")

    # ── web 事件與監控事件互不干擾 ───────────────────────────────────────
    def test_web_and_monitor_incidents_coexist(self):
        self.h.set_probe(1)
        self.h.run()                                   # web FIRING
        self.assertEqual(self.h.state["state"], "FIRING")
        self.h.set_timer(timer_enabled="disabled", timer_active="inactive", mono=0)
        self.h.run()                                   # monitor FIRING
        self.assertEqual(self.h.state["state"], "FIRING", "web 事件不得被覆蓋")
        self.assertEqual(self.h.monitor_state["state"], "FIRING")

    def test_monitor_recovery_does_not_resolve_web_incident(self):
        """監控恢復只代表「我又看得見了」，不代表服務好了。"""
        self.h.set_probe(1)
        self.h.run()
        self.h.set_timer(timer_enabled="disabled", timer_active="inactive", mono=0)
        self.h.run()
        self.assertEqual(self.h.monitor_state["state"], "FIRING")
        # timer 恢復，而且真的有一筆**新鮮的**觀測（仍是失敗）。
        # 這裡刻意直接給當下的單調時戳：從 mono=0 累加只會得到「開機後 120 秒」，
        # 那在開機已數十小時的機器上仍然是過期觀測。
        self.h.set_timer(timer_enabled="enabled", timer_active="active",
                         mono=self.h._now_mono(), exit_status=1, result="exit-code")
        p = self.h.run()
        self.assertEqual(monitor_emit(p.stdout)["action"], "resolved")
        self.assertEqual(self.h.monitor_state, {}, "監控事件應已關閉")
        self.assertEqual(self.h.state["state"], "FIRING", "web 事件必須仍然開著")

    def test_monitor_recovery_requires_a_fresh_observation(self):
        """**只看 is-active 就宣告恢復是錯的。**

        timer 可以是 active 卻還沒產出任何觀測（剛 enable、或 GC 之後）。
        那個瞬間我們仍然看不見任何東西，不能說已經恢復。
        """
        self.h.set_timer(timer_enabled="disabled", timer_active="inactive", mono=0)
        self.h.run()
        self.assertEqual(self.h.monitor_state["state"], "FIRING")
        # timer 回來了，但還沒有觀測（仍在 bootstrap 空窗內）
        self.h.set_timer(timer_enabled="enabled", timer_active="active",
                         timer_enter_mono=self.h._now_mono(), mono=0)
        p = self.h.run()
        self.assertNotEqual(monitor_emit(p.stdout)["action"], "resolved")
        self.assertEqual(self.h.monitor_state["state"], "FIRING",
                         "沒有新觀測就宣告恢復＝把失明當成健康")

    # ── 重開機 ───────────────────────────────────────────────────────────
    def test_reboot_with_no_first_probe_is_bootstrap(self):
        self.h.mono = 0
        self.h.set_timer(timer_enter_mono=self.h._now_mono())
        p = self.h.run()
        self.assertEqual(monitor_emit(p.stdout)["status"], "bootstrap")
        self.assertEqual(self.h.webhook_calls(), 0)

    def test_reboot_does_not_reuse_previous_boot_monotonic(self):
        """上一次開機的單調時鐘值可能遠大於現在，直接比對會把舊觀測當成新的。"""
        (self.h.state_dir).mkdir(parents=True, exist_ok=True)
        (self.h.state_dir / "monitor.state").write_text(
            "state=FIRING\nseverity=CRITICAL\nfirst_seen=1\nlast_notified=1\n"
            "last_obs_monotonic=999999999999999\ncount=3\nboot_id=stale-boot-id\n",
            encoding="utf-8",
        )
        p = self.h.run()
        self.assertEqual(p.returncode, 0)
        self.assertEqual(monitor_emit(p.stdout)["action"], "resolved")

    # ── 惡意／損毀輸入 ───────────────────────────────────────────────────
    def test_malformed_systemctl_values_do_not_crash(self):
        for label, kw in (
            ("status=abc", {"exit_status": "abc"}),
            ("mono 空", {"mono": ""}),
            ("mono 負數", {"mono": "-1"}),
            ("is-enabled 非預期", {"timer_enabled": "weird-value"}),
            ("LoadState 非預期", {"timer_load": ";rm -rf /", "service_load": "$(id)"}),
        ):
            with self.subTest(case=label):
                h = _Harness(**kw)
                self.addCleanup(h.close)
                r = h.run()
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertTrue(monitor_emit(r.stdout), f"未產生可解析的 monitor 輸出: {r.stdout}")
                self.assertNotIn("uid=", r.stdout, "systemctl 輸出被 shell 展開了")

    def test_status_vocabulary_is_closed(self):
        allowed = {"ok", "web_incident", "monitor_blind", "bootstrap", "in_flight", "tooling"}
        seen = set()
        for kw, env in (
            ({}, {}),
            ({"exit_status": 1}, {}),
            ({"timer_enabled": "disabled", "timer_active": "inactive", "mono": 0}, {}),
            ({"mono": 0, "timer_enter_mono": 1}, {}),
        ):
            h = _Harness(**kw)
            self.addCleanup(h.close)
            if kw.get("timer_enter_mono") == 1:
                h.timer_enter_mono = 1
                h._write_fakes()
            out = h.run(**env).stdout
            for ln in out.splitlines():
                if "handler=incident" in ln:
                    seen.add(parse(ln)["status"])
        self.assertTrue(seen <= allowed, f"出現封閉詞彙以外的 status: {seen - allowed}")

    def test_webhook_failure_during_blindness_keeps_state_correct(self):
        """投遞失敗不得讓狀態機退化——否則下一輪會重送，變成通知風暴。"""
        h = _Harness(webhook="http://example.invalid/hook",
                     timer_enabled="disabled", timer_active="inactive", mono=0)
        self.addCleanup(h.close)
        (h.bin / "curl").write_text("#!/usr/bin/env bash\nexit 7\n", encoding="utf-8")
        (h.bin / "curl").chmod(0o755)
        p = h.run()
        self.assertEqual(p.returncode, 0)
        self.assertEqual(monitor_emit(p.stdout)["notified"], "no")
        self.assertEqual(h.monitor_state["state"], "FIRING", "投遞失敗仍須記錄事件")

    def test_parallel_handlers_produce_one_monitor_firing(self):
        """並發下不得兩份 handler 各發一則失明通知。"""
        h = _Harness(webhook="http://example.invalid/hook",
                     timer_enabled="disabled", timer_active="inactive", mono=0)
        self.addCleanup(h.close)
        env = dict(os.environ)
        env["PATH"] = f"{h.bin}:{env['PATH']}"
        env["INCIDENT_STATE_DIR"] = str(h.state_dir)
        env["REPORT_MARK_ALERT_WEBHOOK"] = h.webhook
        h.state_dir.mkdir(parents=True, exist_ok=True)
        procs = [
            subprocess.Popen(["bash", str(HANDLER)], stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True, env=env)
            for _ in range(6)
        ]
        for pr in procs:
            pr.wait(timeout=60)
        self.assertEqual(h.webhook_calls(), 1, "並發下重複發出失明 FIRING（flock 未涵蓋讀-改-寫）")

    def test_thresholds_are_calibrated_not_inherited(self):
        """門檻必須反映實測的觸發分布（min 125／max 136／mean 131.4），不是沿用初版。"""
        body = HANDLER.read_text(encoding="utf-8")
        self.assertIn("INCIDENT_STALE_SECONDS:-420", body)
        self.assertIn("INCIDENT_BOOTSTRAP_SECONDS:-300", body)
        self.assertIn("INCIDENT_BLIND_CRITICAL_SECONDS:-900", body)
        self.assertNotIn("INCIDENT_STALE_SECONDS:-600", body)


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
