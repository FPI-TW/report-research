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
import json
import os
import shlex
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
                 timer_enter_mono=0, show_rc=0, probe_state="inactive",
                 fake_curl=True, probe_start_mono=0):
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
        # 投遞測試要用**真的 curl** 打到本機假接收端，才驗得到 payload、逾時與狀態碼。
        # 其餘測試沿用 fake curl（只數呼叫次數，不需要網路）。
        self.fake_curl = fake_curl
        # 當前呼叫的起始單調時戳（systemd 的 InactiveExitTimestampMonotonic）。
        # 用來判「探針卡住」；0＝不提供（既有測試沿用舊路徑）。
        self.probe_start_mono = probe_start_mono
        # 讓 fake systemctl 對「接下來 N 次 ExecMainExitTimestampMonotonic 查詢」回 0，
        # 而 ActiveState 維持 inactive——那正是跨 P4 invocation 邊界的不可能組合。
        self.torn_file = self.root / "torn_reads"
        self.torn_reads = 0
        # 預設用「現在」的單調時鐘，讓觀測看起來是新鮮的
        self.mono = mono if mono is not None else self._now_mono()
        self.webhook = webhook
        self._write_fakes()

    @staticmethod
    def _now_mono() -> int:
        with open("/proc/uptime") as f:
            return int(float(f.read().split()[0]) * 1_000_000)

    _SYSTEMCTL_FAKE = """#!/usr/bin/env bash
verb="$1"; shift
unit="${{1:-}}"
case "$verb" in
  is-enabled) printf '%s\\n' "{timer_enabled}"
    [ "{timer_enabled}" = enabled ] && exit 0 || exit 1 ;;
  is-active) printf '%s\\n' "{timer_active}"
    [ "{timer_active}" = active ] && exit 0 || exit 3 ;;
esac
[ "{show_rc}" -ne 0 ] && exit {show_rc}

# **必須支援多屬性查詢。** 真的 `systemctl show -p A -p B` 輸出 `A=..\\nB=..`；
# 舊版這支 fake 在第一個匹配屬性就 exit，於是 handler 改用單次快照查詢後只拿得到
# 一行。那不是 handler 的錯，是 fake 沒跟上 systemctl 的契約。
value_only=no
want=()
for a in "$@"; do
  case "$a" in
    --value) value_only=yes ;;
    LoadState|ActiveState|ActiveEnterTimestampMonotonic|InactiveExitTimestampMonotonic|Result|ExecMainStatus|ExecMainExitTimestampMonotonic)
      want+=("$a") ;;
  esac
done

# 「接下來 N 次 ExecMainExitTimestampMonotonic 查詢回 0」：模擬讀數跨越 P4 的
# invocation 邊界（state 仍是 inactive，但 exit 時戳被歸零）。
torn=no
if [ -f "{torn_file}" ]; then
  n=$(cat "{torn_file}" 2>/dev/null || echo 0)
  case "$n" in ""|*[!0-9]*) n=0 ;; esac
  if [ "$n" -gt 0 ]; then torn=yes; echo $((n-1)) > "{torn_file}"; fi
fi

emit() {{
  if [ "$value_only" = yes ]; then printf '%s\\n' "$2"; else printf '%s=%s\\n' "$1" "$2"; fi
}}
for k in "${{want[@]}}"; do
  case "$k" in
    LoadState)
      case "$unit" in
        *.timer) emit LoadState "{timer_load}" ;;
        *)       emit LoadState "{service_load}" ;;
      esac ;;
    ActiveEnterTimestampMonotonic) emit ActiveEnterTimestampMonotonic "{timer_enter_mono}" ;;
    InactiveExitTimestampMonotonic) emit InactiveExitTimestampMonotonic "{probe_start_mono}" ;;
    ActiveState)
      case "$unit" in
        *.timer) emit ActiveState "{timer_active}" ;;
        *)       emit ActiveState "{probe_state}" ;;
      esac ;;
    Result) emit Result {result_q} ;;
    ExecMainStatus) emit ExecMainStatus "{exit_status}" ;;
    ExecMainExitTimestampMonotonic)
      if [ "$torn" = yes ]; then emit ExecMainExitTimestampMonotonic 0
      else emit ExecMainExitTimestampMonotonic "{mono}"; fi ;;
  esac
done
exit 0
"""

    def _write_fakes(self):
        if self.torn_reads > 0:
            self.torn_file.write_text(str(self.torn_reads), encoding="utf-8")
        elif self.torn_file.exists():
            self.torn_file.unlink()
        sc = self.bin / "systemctl"
        # 受控的 systemctl：**同時**表達 service 的執行結果與 timer 的存活狀態。
        # 兩者缺一不可——2026-08-19 的實測顯示 timer 一旦 disabled，service 的
        # ExecMain* 會當場被 GC 清空，只看 service 那一維根本分不出「還沒跑」與
        # 「監控被停掉」。
        sc.write_text(
            self._SYSTEMCTL_FAKE.format(
                timer_enabled=self.timer_enabled,
                timer_active=self.timer_active,
                show_rc=self.show_rc,
                timer_load=self.timer_load,
                service_load=self.service_load,
                timer_enter_mono=self.timer_enter_mono,
                probe_start_mono=self.probe_start_mono,
                probe_state=self.probe_state,
                result_q=shlex.quote(str(self.result)),
                exit_status=self.exit_status,
                mono=self.mono,
                torn_file=self.torn_file,
            ),
            encoding="utf-8",
        )
        sc.chmod(0o755)
        if self.fake_curl:
            curl = self.bin / "curl"
            # **必須印出 HTTP 狀態碼。** handler 的成功判準是 `-w %{http_code}` 的
            # stdout，不是 curl 的 exit code；只 `exit 0` 會讓每一次投遞都被判失敗
            # （#215 改判準時這支 fake 沒跟上，而當時的狀態機無論成敗都推進，
            # 所以沒有任何測試看得見）。**刻意不排空 stdin**：URL 走 `-K -` 進來但
            # 只有幾十位元組，遠小於管線緩衝，上游 printf 不會阻塞也不會 SIGPIPE；
            # 加 `cat` 反而會在沒有管線的呼叫形狀下卡住整個測試。
            curl.write_text(
                "#!/usr/bin/env bash\n"
                f'echo "$@" >> "{self.webhook_log}"\n'
                'echo "${FAKE_CURL_CODE:-200}"\n'
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
        # **空窗上限一律明確宣告，預設壓到 1 秒。**
        # 這一類錯誤在 2026-08-19 犯了兩次、兩輪 CI 都紅在它：測試用
        # `timer_enter_mono=1` 表達「timer 早就 active」，於是 bootstrap_age 實際上
        # 等於**宿主的 uptime**——開發機數十小時遠超 300 秒預設而綠，剛開機的 CI
        # runner 數十秒卻落在空窗內而紅。逐條修不會收斂（第二次就是在修完第一條之後
        # 於新測試裡重犯），所以改在 harness 層兜住：任何跑得動測試的機器 uptime 都
        # 大於 1 秒，判定於是與 uptime 無關；想測 bootstrap 語意的測試自己傳大值覆蓋。
        env.setdefault("INCIDENT_BOOTSTRAP_SECONDS", "1")
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

    def set_observation_age(self, seconds: float):
        """把最後一筆觀測設成「距今 N 秒」。

        **不要用 `mono = 1_000_000`（開機後 1 秒）來表達「很舊」**——那個值有多舊
        取決於宿主 uptime：開發機上是數十小時前，剛開機的 CI runner 上只有數十秒前。

        **也不要 `max(0, ...)` 了事**：單調時鐘的原點是本次開機，所以在剛開機的機器上
        「一小時前」根本不存在，夾成 0 之後測試會**靜默改成在走 observation_missing
        分支**——那比紅燈更糟。這裡改成明確斷言，要求年齡必須是這台機器上真的表達得出
        來的值；配合小門檻使用（例如 age=5 搭 STALE=1）。
        """
        now = self._now_mono()
        need = int(seconds * 1_000_000)
        assert now > need, (
            f"無法在此機器上表達「距今 {seconds}s 的觀測」："
            f"uptime 只有 {now / 1_000_000:.1f}s。請改用更小的年齡與門檻。"
        )
        self.mono = now - need
        self._write_fakes()

    def set_torn(self, n: int):
        """接下來 n 次 ExecMainExitTimestampMonotonic 查詢回 0（模擬 torn snapshot）。"""
        self.torn_reads = n
        self._write_fakes()

    def set_timer(self, **kw):
        """翻轉訊號源那一維（enabled/active/load/enter_mono），其餘不動。"""
        for k, v in kw.items():
            setattr(self, k, v)
        self._write_fakes()

    def seed_observation(self, status, age_seconds=10, result="success", boot_id=None):
        """播種「上一筆已完成的觀測」。

        age 以**現在的單調時鐘回推**，不用固定值——固定值有多舊取決於宿主 uptime，
        那正是 2026-08-19 兩輪 CI 都紅掉的那一類錯誤。
        """
        now = self._now_mono()
        need = int(age_seconds * 1_000_000)
        assert now > need, f"uptime 只有 {now/1e6:.1f}s，表達不出 {age_seconds}s 前的觀測"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        (self.state_dir / "probe_observation.state").write_text(
            f"boot_id={boot_id or self.boot_id()}\n"
            f"monotonic={now - need}\n"
            f"status={status}\n"
            f"result={result}\n",
            encoding="utf-8",
        )

    @property
    def observation_cache(self) -> dict:
        f = self.state_dir / "probe_observation.state"
        if not f.is_file():
            return {}
        return dict(
            ln.split("=", 1) for ln in f.read_text(encoding="utf-8").splitlines() if "=" in ln
        )

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
        # age=5／STALE=1：小到任何機器的 uptime 都表達得出來，大到穩定超過門檻
        self.h.set_observation_age(5)
        p = self.h.run(INCIDENT_STALE_SECONDS="1", INCIDENT_BLIND_CRITICAL_SECONDS="99999999")
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
        self.h.set_observation_age(5)
        p1 = self.h.run(INCIDENT_STALE_SECONDS="1", INCIDENT_BLIND_CRITICAL_SECONDS="99999999")
        self.assertEqual(monitor_emit(p1.stdout)["severity"], "WARNING")
        p2 = self.h.run(INCIDENT_STALE_SECONDS="1", INCIDENT_BLIND_CRITICAL_SECONDS="2")
        m = monitor_emit(p2.stdout)
        self.assertEqual(m["action"], "escalated")
        self.assertEqual(m["severity"], "CRITICAL")
        self.assertEqual(self.h.webhook_calls(), 2, "升級必須立刻送，不得被去重吞掉")

    def test_no_observation_within_bootstrap_is_not_an_incident(self):
        """剛開機／剛 enable、還沒跑第一輪：不得誤報。"""
        self.h.mono = 0
        # timer 剛進入 active（單調時鐘上就在剛才）
        self.h.set_timer(timer_enter_mono=self.h._now_mono())
        p = self.h.run(INCIDENT_BOOTSTRAP_SECONDS="300")
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
        p = self.h.run()          # 不傳＝用 harness 的預設，證明預設有效
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
        blind = monitor_emit(self.h.run().stdout)
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
        self.h.set_observation_age(5)
        p1 = self.h.run(INCIDENT_STALE_SECONDS="1", INCIDENT_BLIND_CRITICAL_SECONDS="99999999")
        self.assertEqual(monitor_emit(p1.stdout)["action"], "firing")
        p2 = self.h.run(INCIDENT_STALE_SECONDS="1", INCIDENT_BLIND_CRITICAL_SECONDS="99999999",
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

    def test_harness_always_declares_the_bootstrap_window(self):
        """守住 harness 層的修正：預設不得留給宿主 uptime 決定。

        這條測 harness 自己，不測腳本——因為缺陷兩次都出在 harness 沒宣告維度。
        """
        import inspect
        src = inspect.getsource(_Harness.run)
        self.assertIn("INCIDENT_BOOTSTRAP_SECONDS", src, "harness.run() 必須明確宣告空窗上限")
        self.assertIn("setdefault", src, "必須是 setdefault，讓個別測試仍可覆蓋")

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
        p = self.h.run(INCIDENT_BOOTSTRAP_SECONDS="300")
        self.assertNotEqual(monitor_emit(p.stdout)["action"], "resolved")
        self.assertEqual(self.h.monitor_state["state"], "FIRING",
                         "沒有新觀測就宣告恢復＝把失明當成健康")

    # ── 重開機 ───────────────────────────────────────────────────────────
    def test_reboot_with_no_first_probe_is_bootstrap(self):
        self.h.mono = 0
        self.h.set_timer(timer_enter_mono=self.h._now_mono())
        p = self.h.run(INCIDENT_BOOTSTRAP_SECONDS="300")
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


class _Receiver:
    """本機假 webhook 接收端。**第一個投遞測試絕不對真的 endpoint 打。**

    可控制狀態碼與延遲，才驗得到「哪些碼算成功」與逾時行為——那兩件事是投遞契約的
    核心，用 fake curl（只數次數）驗不到。
    """

    def __init__(self, status=200, delay=0.0):
        import http.server
        import socketserver
        import threading

        self.status, self.delay, self.requests = status, delay, []
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                n = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(n).decode("utf-8", "replace")
                outer.requests.append({"body": body, "ct": self.headers.get("Content-Type")})
                if outer.delay:
                    import time

                    time.sleep(outer.delay)
                self.send_response(outer.status)
                self.end_headers()
                self.wfile.write(b"ok")

            def log_message(self, *a):
                pass

        self.srv = socketserver.TCPServer(("127.0.0.1", 0), H)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}/hook"

    def payloads(self):
        return [json.loads(r["body"]) for r in self.requests]

    def close(self):
        self.srv.shutdown()


class AlertDeliveryTests(unittest.TestCase):
    """投遞路徑：真的 curl、真的 HTTP、假的接收端。

    P5 的偵測與狀態機在 2026-08-19 就上線了，但 `REPORT_MARK_ALERT_WEBHOOK` 一直沒設，
    所以事件只進 journal 與狀態檔、**沒有任何外部投遞**。這個類別守的是投遞本身。
    """

    def setUp(self):
        self.rx = _Receiver()
        self.addCleanup(self.rx.close)
        self.h = _Harness(webhook=self.rx.url, fake_curl=False)
        self.addCleanup(self.h.close)

    def _fail(self, **kw):
        """讓探針回報失敗（web 事件），觸發通知。kwargs 可覆寫預設。"""
        kw.setdefault("result", "exit-code")
        self.h.set_timer(exit_status=1, **kw)

    def test_firing_sends_exactly_one_request(self):
        self._fail()
        p = self.h.run()
        self.assertEqual(len(self.rx.requests), 1, p.stdout + p.stderr)
        self.assertEqual(last_emit(p.stdout)["notified"], "yes")
        d = self.rx.payloads()[0]
        self.assertEqual(sorted(d), ["action", "component", "reason", "severity", "text"])
        self.assertEqual(d["component"], "web")
        self.assertEqual(d["action"], "FIRING")
        self.assertEqual(d["severity"], "CRITICAL")
        self.assertEqual(d["reason"], "probe_exit_1")
        self.assertEqual(self.rx.requests[0]["ct"], "application/json")

    def test_same_incident_does_not_resend_firing(self):
        self._fail()
        self.h.run()
        self.h.set_probe(1)          # 新觀測、同一個事件
        self.h.run()
        self.assertEqual(len(self.rx.requests), 1, "同一事件重複發 FIRING")

    def test_reminder_sends_exactly_one_when_due(self):
        self._fail()
        self.h.run()
        self.h.set_probe(1)
        self.h.run(INCIDENT_REMINDER_SECONDS="0")
        self.assertEqual(len(self.rx.requests), 2)
        self.assertEqual(self.rx.payloads()[1]["action"], "REMINDER")

    def test_no_request_before_reminder_threshold(self):
        self._fail()
        self.h.run()
        self.h.set_probe(1)
        self.h.run(INCIDENT_REMINDER_SECONDS="99999")
        self.assertEqual(len(self.rx.requests), 1)

    def test_resolved_sends_exactly_one(self):
        self._fail()
        self.h.run()
        self.h.set_timer(exit_status=0, result="success", mono=self.h._now_mono())
        p = self.h.run()
        self.assertEqual(len(self.rx.requests), 2)
        self.assertEqual(self.rx.payloads()[1]["action"], "RESOLVED")
        self.assertEqual(last_emit(p.stdout)["action"], "resolved")

    def test_repeated_healthy_does_not_resend_resolved(self):
        self._fail()
        self.h.run()
        self.h.set_timer(exit_status=0, result="success", mono=self.h._now_mono())
        self.h.run()
        self.h.set_probe(0, result="success")
        self.h.run()
        self.assertEqual(len(self.rx.requests), 2, "healthy 重複發 RESOLVED")

    def test_http_500_is_not_marked_notified_but_state_advances(self):
        self.rx.status = 500
        self._fail()
        p = self.h.run()
        self.assertEqual(len(self.rx.requests), 1, "請求應已送出（是對方回 500）")
        self.assertEqual(last_emit(p.stdout)["notified"], "no")
        self.assertEqual(self.h.state["state"], "FIRING", "投遞失敗仍須記錄事件")
        self.assertIn("HTTP 500", p.stdout)

    def test_http_3xx_is_not_counted_as_success(self):
        """**未跟隨的重導向代表 POST 沒到目的地。** 不能因為「不是 4xx/5xx」就算成功。"""
        self.rx.status = 302
        self._fail()
        p = self.h.run()
        self.assertEqual(last_emit(p.stdout)["notified"], "no")
        self.assertIn("HTTP 302", p.stdout)

    def test_timeout_does_not_corrupt_state(self):
        self.rx.delay = 3.0
        self._fail()
        p = self.h.run(INCIDENT_NOTIFY_MAX_TIME="1")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(last_emit(p.stdout)["notified"], "no")
        self.assertEqual(self.h.state["state"], "FIRING")

    def test_malformed_url_does_not_crash(self):
        h = _Harness(webhook="not-a-url://%%%", fake_curl=False)
        self.addCleanup(h.close)
        h.set_timer(exit_status=1, result="exit-code")
        p = h.run()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(last_emit(p.stdout)["notified"], "no")
        self.assertEqual(h.state["state"], "FIRING")

    def test_unreachable_endpoint_fails_fast_and_cleanly(self):
        h = _Harness(webhook="http://127.0.0.1:59999/hook", fake_curl=False)
        self.addCleanup(h.close)
        h.set_timer(exit_status=1, result="exit-code")
        p = h.run(INCIDENT_NOTIFY_CONNECT_TIMEOUT="1")
        self.assertEqual(p.returncode, 0)
        self.assertEqual(last_emit(p.stdout)["notified"], "no")
        self.assertRegex(p.stdout, r"HTTP 0*")

    def test_payload_with_special_chars_stays_valid_json(self):
        """summary 含 systemctl 讀來的值——未跳脫的引號會產出壞 JSON，而對方只回 400。"""
        self._fail(result='he said "boom" \\ and\ttabbed')
        p = self.h.run()
        self.assertEqual(len(self.rx.requests), 1, p.stdout)
        d = self.rx.payloads()[0]           # json.loads 成功即證明跳脫正確
        self.assertIn("boom", d["text"])
        self.assertEqual(last_emit(p.stdout)["notified"], "yes")

    def test_long_summary_is_bounded(self):
        self._fail(result="X" * 5000)
        self.h.run()
        d = self.rx.payloads()[0]
        self.assertLess(len(d["text"]), 1200, "body 未設上限")
        self.assertTrue(d["text"].endswith("…") or len(d["text"]) < 600)

    def test_secret_never_appears_in_output_or_argv(self):
        self._fail()
        p = self.h.run()
        self.assertNotIn(self.rx.url, p.stdout)
        self.assertNotIn(self.rx.url, p.stderr)
        self.assertNotIn(str(self.rx.port), p.stdout)
        # **必須比對程式碼本體，不能比對整檔**：notify() 的註解裡就寫著
        # `curl ... "$WEBHOOK"` 當作反例，整檔比對會被自己的說明觸發＝假守門。
        code_only = "\n".join(
            ln for ln in HANDLER.read_text(encoding="utf-8").splitlines()
            if not ln.strip().startswith("#")
        )
        self.assertIn("-K -", code_only, "URL 應經 -K - 由 stdin 餵入")
        self.assertNotRegex(code_only, r'curl[^\n]*"\$WEBHOOK"', "URL 不得出現在 curl 的 argv")

    def test_concurrent_handlers_send_exactly_one(self):
        self._fail()
        env = dict(os.environ)
        env["PATH"] = f"{self.h.bin}:{env['PATH']}"
        env["INCIDENT_STATE_DIR"] = str(self.h.state_dir)
        env["REPORT_MARK_ALERT_WEBHOOK"] = self.rx.url
        env["INCIDENT_BOOTSTRAP_SECONDS"] = "1"
        self.h.state_dir.mkdir(parents=True, exist_ok=True)
        procs = [
            subprocess.Popen(["bash", str(HANDLER)], stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True, env=env)
            for _ in range(6)
        ]
        for pr in procs:
            pr.wait(timeout=60)
        self.assertEqual(len(self.rx.requests), 1, "並發下重複投遞")

    def test_web_and_monitor_payloads_are_distinguishable(self):
        self._fail()
        self.h.run()
        self.h.set_timer(timer_enabled="disabled", timer_active="inactive", mono=0)
        self.h.run()
        comps = {d["component"] for d in self.rx.payloads()}
        self.assertEqual(comps, {"web", "monitor"})
        by = {d["component"]: d for d in self.rx.payloads()}
        self.assertEqual(by["web"]["reason"], "probe_exit_1")
        self.assertEqual(by["monitor"]["reason"], "timer_disabled")

    def test_no_webhook_configured_means_no_request_and_no_error(self):
        h = _Harness(webhook=None, fake_curl=False)
        self.addCleanup(h.close)
        h.set_timer(exit_status=1, result="exit-code")
        p = h.run()
        self.assertEqual(p.returncode, 0)
        self.assertEqual(len(self.rx.requests), 0)
        self.assertEqual(last_emit(p.stdout)["notified"], "no")
        self.assertEqual(h.state["state"], "FIRING", "未設 webhook 仍須維護事件狀態")


class DeliveryFailureRetryTests(unittest.TestCase):
    """**投遞失敗不得被記成「已通知」。**

    通知節流的時鐘是 `last_notified`。把一則根本沒送達的通知寫進去，等於讓 30 分鐘的
    提醒週期從零開始計時——事故於是靜默到下一個提醒週期為止，而 journal 裡看起來一切
    正常（`action=firing` 有出現過）。事件本身照記，那是觀測到的事實；只有「已通知」
    不記。重送不會形成風暴：端點掛著時每一輪都失敗、實際送出 0 則，端點恢復後只送
    一則，之後去重照常生效。
    """

    def setUp(self):
        self.rx = _Receiver()
        self.addCleanup(self.rx.close)
        self.h = _Harness(webhook=self.rx.url, fake_curl=False)
        self.addCleanup(self.h.close)
        self._tick = 0

    def _obs(self):
        """每輪給一個不同的觀測時戳，否則會被 new_observation=no 去重。"""
        self._tick += 1
        return self.h._now_mono() - self._tick * 1000

    def _fail(self):
        self.h.set_timer(exit_status=1, result="exit-code", mono=self._obs())

    def _ok(self):
        self.h.set_timer(exit_status=0, result="success", mono=self._obs())

    def _monitor_state(self):
        f = self.h.state_dir / "monitor.state"
        if not f.is_file():
            return {}
        return dict(
            ln.split("=", 1) for ln in f.read_text(encoding="utf-8").splitlines() if "=" in ln
        )

    def _actions(self, comp="web"):
        return [d["action"] for d in self.rx.payloads() if d["component"] == comp]

    # ── FIRING ───────────────────────────────────────────────────────────
    def test_failed_firing_does_not_advance_the_notify_clock(self):
        self.rx.status = 500
        self._fail()
        p = self.h.run()
        self.assertEqual(last_emit(p.stdout)["notified"], "no")
        self.assertEqual(self.h.state["state"], "FIRING", "事件本身仍須記錄")
        self.assertEqual(self.h.state["last_notified"], "0", "沒送到就不能算已通知")
        self.assertEqual(self.h.state["opened_sent"], "no")

    def test_failed_firing_is_retried_as_firing_not_reminder(self):
        """重試必須仍是 FIRING。

        若讓它掉進提醒分支，操作者收到的第一則會是「仍未恢復」——而他從沒收到過
        「開始了」。
        """
        self.rx.status = 500
        self._fail()
        self.h.run()
        self.rx.status = 200
        self._fail()
        p = self.h.run()
        self.assertEqual(last_emit(p.stdout)["action"], "firing")
        self.assertEqual(last_emit(p.stdout)["notified"], "yes")
        self.assertEqual(self.rx.payloads()[-1]["action"], "FIRING")
        self.assertEqual(self.h.state["opened_sent"], "yes")
        self.assertNotEqual(self.h.state["last_notified"], "0")

    def test_delivered_firing_is_never_repeated(self):
        self._fail()
        self.h.run()
        self._fail()
        self.h.run()
        self._fail()
        self.h.run()
        self.assertEqual(self._actions().count("FIRING"), 1, self.rx.payloads())

    def test_endpoint_down_for_several_rounds_delivers_exactly_one_firing(self):
        self.rx.status = 500
        for _ in range(3):
            self._fail()
            self.h.run()
        self.assertEqual(len(self.rx.requests), 3, "每輪都應重試投遞")
        self.rx.status = 200
        self._fail()
        self.h.run()
        self._fail()
        self.h.run()
        self.assertEqual(
            self._actions().count("FIRING"), 4,
            "3 次失敗的嘗試 + 1 次成功；成功之後不得再送",
        )
        self.assertEqual(self.h.state["opened_sent"], "yes")

    # ── RESOLVED ─────────────────────────────────────────────────────────
    def test_failed_resolved_keeps_the_incident_open_for_retry(self):
        self._fail()
        self.h.run()
        self.rx.status = 500
        self._ok()
        p = self.h.run()
        self.assertEqual(last_emit(p.stdout)["action"], "resolve_retry")
        self.assertEqual(self.h.state["state"], "FIRING", "RESOLVED 沒送到就不能關閉事件")
        self.rx.status = 200
        self._ok()
        p2 = self.h.run()
        self.assertEqual(last_emit(p2.stdout)["action"], "resolved")
        self.assertEqual(self.h.state, {}, "送達後才刪狀態檔")

    def test_repeated_healthy_after_delivered_resolved_stays_silent(self):
        self._fail()
        self.h.run()
        self._ok()
        self.h.run()
        before = len(self.rx.requests)
        self._ok()
        self._ok()
        self.h.run()
        self.assertEqual(len(self.rx.requests), before, "已關閉的事件不得再送")

    # ── ESCALATED ────────────────────────────────────────────────────────
    def test_failed_escalation_keeps_severity_at_warning_for_retry(self):
        """升級沒送到卻把 severity 寫成 CRITICAL，升級條件下一輪就不再成立。"""
        self.h.set_observation_age(5)
        self.h.run(INCIDENT_STALE_SECONDS="1", INCIDENT_BLIND_CRITICAL_SECONDS="99999999")
        self.assertEqual(self._monitor_state()["severity"], "WARNING")
        self.rx.status = 500
        self.h.run(INCIDENT_STALE_SECONDS="1", INCIDENT_BLIND_CRITICAL_SECONDS="2")
        self.assertEqual(
            self._monitor_state()["severity"], "WARNING",
            "升級通知沒送到，嚴重度必須留在 WARNING 才會再試",
        )
        self.rx.status = 200
        p = self.h.run(INCIDENT_STALE_SECONDS="1", INCIDENT_BLIND_CRITICAL_SECONDS="2")
        self.assertEqual(monitor_emit(p.stdout)["action"], "escalated")
        self.assertEqual(self._monitor_state()["severity"], "CRITICAL")

    # ── REMINDER ─────────────────────────────────────────────────────────
    def test_failed_reminder_does_not_advance_the_reminder_clock(self):
        self._fail()
        self.h.run()
        notified_after_firing = self.h.state["last_notified"]
        self.rx.status = 500
        self._fail()
        p = self.h.run(INCIDENT_REMINDER_SECONDS="0")
        self.assertEqual(last_emit(p.stdout)["action"], "reminder")
        self.assertEqual(
            self.h.state["last_notified"], notified_after_firing,
            "提醒沒送到，提醒時鐘不得前進",
        )

    # ── 未設定 webhook（＝目前生產）─────────────────────────────────────
    def test_without_webhook_the_incident_still_closes(self):
        """**沒有 webhook 時「投遞成功」必須真空成立。**

        若把「沒送出」一律當成失敗，未設定 webhook 的部署會永遠關不掉事件——
        偵測功能被通知功能反噬，而生產目前正是這個狀態。
        """
        h = _Harness(webhook=None, fake_curl=False)
        self.addCleanup(h.close)
        h.set_timer(exit_status=1, result="exit-code", mono=h._now_mono() - 1000)
        p1 = h.run()
        self.assertEqual(last_emit(p1.stdout)["action"], "firing")
        self.assertEqual(last_emit(p1.stdout)["notified"], "no")
        self.assertEqual(h.state["opened_sent"], "yes", "沒東西要送＝沒有待投遞的通知")
        h.set_timer(exit_status=0, result="success", mono=h._now_mono() - 500)
        p2 = h.run()
        self.assertEqual(last_emit(p2.stdout)["action"], "resolved")
        self.assertEqual(h.state, {})

    # ── 與 #216/#217/#218 的偵測語意共存 ────────────────────────────────
    def test_inflight_with_cached_failure_still_notifies(self):
        """探針執行中 ＋ 上一筆完成觀測是 FAIL：通知照走，欄位仍描述那筆快取。"""
        self.h.seed_observation(1, age_seconds=3, result="exit-code")
        self.h.set_timer(probe_state="activating", exit_status=0, result="success",
                         mono=0, probe_start_mono=self.h._now_mono() - 2_000_000)
        p = self.h.run()
        w = last_emit(p.stdout)
        self.assertEqual(w["action"], "firing")
        self.assertEqual(w["current_probe"], "in_flight")
        self.assertEqual(w["last_completed"], "fail")
        self.assertEqual(w["notified"], "yes")
        self.assertEqual(self.rx.payloads()[-1]["component"], "web")

    def test_delivery_does_not_change_the_two_lines_agreement(self):
        """#218 的不變量在投遞路徑上仍成立。"""
        self.h.seed_observation(1, age_seconds=3, result="exit-code")
        self.h.set_timer(probe_state="activating", exit_status=0, result="success",
                         mono=0, probe_start_mono=self.h._now_mono() - 2_000_000)
        out = self.h.run().stdout
        w, m = last_emit(out), monitor_emit(out)
        self.assertEqual(w["last_completed"], m["last_completed"])
        self.assertEqual(w["last_completed_age"], m["last_completed_age"])


class SnapshotConsistencyTests(unittest.TestCase):
    """**單次暫態的 systemd 讀數不得直接變成 MONITOR_BLIND。**

    2026-08-20 生產實測三次假 FIRING（04:49／07:09／13:20），全為 monitor 元件、
    `notified=no`、約 2 分鐘內自行 RESOLVED。而 P4 當日 413 次執行、最大間隔 142 秒、
    **零次超過 240 秒**——探針從未真的斷過。

    `last_completed_age` 序列 `69→81→89→99→109→249→0→10`：正常每輪增約 10 秒，
    卻在單一取樣跳 +140（約一個完整 P4 週期），下一輪立即回 0。該輪 `current_probe=idle`，
    所以不是 #216 修的 in_flight 路徑。

    根因是 handler 對每個屬性各發一次 `systemctl show`（原本 8 次）。兩次查詢之間若
    跨越 P4 的 invocation 邊界，就會拼出 `ActiveState=inactive` ＋
    `ExecMainExitTimestampMonotonic=0`——**在單一一致快照中不可能出現的組合**
    （該 unit 有 timer 引用、不會被 GC，idle 時必定有完成時戳）。

    隔離實驗（user-scope oneshot ＋ timer，804 次取樣）：
      一次 `show -p A -p B` → 矛盾 0/804 = 0.00%
      兩次獨立 `show`       → 矛盾 3/804 = 0.37%
    生產 3 次 ÷ 約 660 個 P5 週期 = 0.45%，同一量級。
    """

    def setUp(self):
        self.h = _Harness()
        self.addCleanup(self.h.close)

    def _run(self, **env):
        return self.h.run(**env)

    def _idle_fresh(self):
        """一個乾淨的 idle 觀測：剛完成、有啟動時戳。"""
        now = self.h._now_mono()
        self.h.set_timer(probe_state="inactive", exit_status=0, result="success",
                         mono=now - 5_000_000, probe_start_mono=now - 6_000_000)

    # ── 正常路徑 ────────────────────────────────────────────────────────
    def test_idle_with_fresh_observation_is_ok(self):
        self._idle_fresh()
        m = monitor_emit(self._run().stdout)
        self.assertEqual(m["status"], "ok")
        self.assertEqual(m["snapshot"], "stable")
        self.assertEqual(m["observation_source"], "live")

    def test_never_run_is_bootstrap_not_unstable(self):
        """**從未跑過（start=0 且 exit=0）是自洽的**，不可被誤判成 torn。"""
        self.h.set_timer(probe_state="inactive", mono=0, probe_start_mono=0,
                         timer_enter_mono=self.h._now_mono())
        m = monitor_emit(self._run(INCIDENT_BOOTSTRAP_SECONDS="99999999").stdout)
        self.assertEqual(m["status"], "bootstrap")
        self.assertEqual(m["snapshot"], "stable")

    # ── 暫態 ────────────────────────────────────────────────────────────
    def test_single_torn_read_is_retried_and_recovers(self):
        """重讀一次就跨過那個毫秒級窗口 → 仍是 ok，且看得出重讀過。"""
        self._idle_fresh()
        self.h.set_torn(1)
        m = monitor_emit(self._run().stdout)
        self.assertEqual(m["status"], "ok", "單次暫態不得變成健康結論")
        self.assertEqual(m["snapshot"], "retried")
        self.assertEqual(m["snapshot_retries"], "1")

    def test_persistent_torn_falls_back_to_cache_without_firing(self):
        """重讀後仍矛盾：沿用信任窗內的完成觀測，**不開事件**。"""
        self.h.seed_observation(0, age_seconds=40, result="success")
        self._idle_fresh()
        self.h.set_torn(9)
        m = monitor_emit(self._run().stdout)
        self.assertNotEqual(m["action"], "firing")
        self.assertEqual(m["snapshot"], "unstable")
        self.assertEqual(m["reason"], "snapshot_unstable")
        self.assertEqual(m["observation_source"], "cache")

    def test_the_production_false_page_does_not_fire(self):
        """**真實事故重播。**

        生產序列是 `…→109→249→0`：age 走到 109（仍在 OBS_TRUST=240 內）之後撞上
        torn read，舊版退回快取並讓 age 跳到 249、越過信任上限而 FIRING。

        這裡用 40s 表達「仍在信任窗內」——**CI runner uptime 實測只有 85.6s，
        109 這個數字在 CI 上根本表達不出來**（`test_no_scenario_exceeds_a_plausible_ci_uptime`
        會擋）。真實數字保留在這段文字裡，不進程式碼。
        """
        self.h.seed_observation(0, age_seconds=40, result="success")
        self._idle_fresh()
        self.h.set_torn(9)
        out = self._run().stdout
        m = monitor_emit(out)
        self.assertNotEqual(m["action"], "firing", out)
        self.assertNotEqual(m["status"], "monitor_blind", out)
        self.assertEqual(m["notified"], "no")

    def test_transient_does_not_resolve_an_open_web_incident(self):
        """快取是 FAIL 時，暫態不得把 WEB_HEALTH 事件錯誤解除。"""
        self.h.seed_observation(1, age_seconds=30, result="exit-code")
        self._idle_fresh()
        self.h.set_torn(9)
        w = last_emit(self._run().stdout)
        self.assertEqual(w["last_completed"], "fail")
        self.assertNotEqual(w["action"], "resolved")

    # ── 真 stale 必須保留 ───────────────────────────────────────────────
    def test_consistent_but_stale_snapshot_still_fires(self):
        """快照自洽、但真的沒有新完成觀測 ⇒ 仍須 MONITOR_BLIND。"""
        # **不可用大的絕對偏移。** CI runner uptime 僅約 85.6s，`now - 900s` 會變成
        # 負數，被數值驗證判成 malformed 而走進完全不同的分支。改用小尺度 ＋ 縮小門檻。
        now = self.h._now_mono()
        self.h.set_timer(probe_state="inactive", exit_status=0, result="success",
                         mono=now - 30_000_000, probe_start_mono=now - 31_000_000,
                         timer_enter_mono=1)
        m = monitor_emit(self._run(INCIDENT_STALE_SECONDS="5").stdout)
        self.assertEqual(m["status"], "monitor_blind")
        self.assertEqual(m["snapshot"], "stable", "這不是快照問題，別歸錯因")

    def test_persistent_torn_without_cache_is_blind_with_distinct_reason(self):
        """持續矛盾且無快取可用：確實看不見，但原因要與真 stale 分得開。"""
        self._idle_fresh()
        self.h.set_torn(9)
        m = monitor_emit(self._run(INCIDENT_BOOTSTRAP_SECONDS="1").stdout)
        self.assertEqual(m["status"], "monitor_blind")
        self.assertEqual(m["reason"], "snapshot_unstable")
        self.assertEqual(m["snapshot"], "unstable")

    # ── 相容性 ──────────────────────────────────────────────────────────
    def test_in_flight_semantics_unchanged(self):
        """#216/#217/#218 完全不得回歸。"""
        self.h.seed_observation(1, age_seconds=3, result="exit-code")
        self.h.set_timer(probe_state="activating", exit_status=0, result="success",
                         mono=0, probe_start_mono=self.h._now_mono() - 2_000_000)
        out = self._run().stdout
        w, m = last_emit(out), monitor_emit(out)
        self.assertEqual(w["current_probe"], "in_flight")
        self.assertEqual(w["last_completed"], "fail")
        self.assertEqual(w["last_completed_age"], m["last_completed_age"])
        self.assertEqual(m["snapshot"], "stable", "activating 期間 exit=0 是自洽的")

    def test_retry_is_bounded(self):
        """重讀有上限，不得變成慢迴圈。"""
        self._idle_fresh()
        self.h.set_torn(999)
        p = self._run(INCIDENT_SNAPSHOT_RETRIES="2", INCIDENT_SNAPSHOT_RETRY_DELAY="0")
        self.assertEqual(p.returncode, 0)
        self.assertLessEqual(int(monitor_emit(p.stdout)["snapshot_retries"]), 2)

    def test_query_failure_semantics_preserved(self):
        self.h.set_timer(show_rc=1)
        m = monitor_emit(self._run().stdout)
        self.assertEqual(m["status"], "monitor_blind")
        self.assertEqual(m["reason"], "query_failed")

    def test_snapshot_values_are_data_not_code(self):
        """屬性值來自 systemd，解析不得 source/eval。"""
        canary = self.h.root / "pwned"
        self.h.set_timer(result=f'$(touch "{canary}")`touch "{canary}"`;id')
        self._run()
        self.assertFalse(canary.exists(), "快照值被當成 shell 程式碼求值了")

    def test_handler_makes_one_snapshot_query_per_unit(self):
        """靜態守門：屬性不得再回到「一個一次」的讀法。"""
        body = HANDLER.read_text(encoding="utf-8")
        live = [ln for ln in body.splitlines()
                if "systemctl show" in ln and not ln.strip().startswith("#")]
        self.assertLessEqual(
            len(live), 2,
            f"每個屬性各發一次 show 正是假 FIRING 的成因；實得 {len(live)} 處：{live}",
        )


class SecretPlacementTests(unittest.TestCase):
    """secret 落點的形狀。**不驗值，只驗機制**——值不進 repo、不進測試。"""

    def test_units_read_the_dedicated_secret_file(self):
        for unit in (SERVICE, SYSTEMD_DIR / "report-mark-alert@.service"):
            with self.subTest(unit=unit.name):
                vals = _directives(unit, "EnvironmentFile")
                self.assertIn("-/etc/report-mark/alert.env", vals,
                              "缺專用 secret 落點")
                self.assertTrue(
                    any(v.startswith("-") for v in vals if "alert.env" in v),
                    "必須用 `-` 前綴：secret 尚未注入時 unit 不該啟動失敗",
                )

    def test_secret_is_not_in_the_shared_config_file(self):
        """**不可放進 /etc/default/report-mark-sync。**

        那個檔必須使用者可讀——`scripts/db_backup.sh` 會自己逐鍵讀它，因為手動
        `make db-backup` 不經過 systemd 的 EnvironmentFile（2026-07-30 的事故）。
        把 webhook URL 放進去等於全域可讀；收緊權限則弄壞手動備份。
        """
        for unit in (SERVICE, SYSTEMD_DIR / "report-mark-alert@.service"):
            vals = _directives(unit, "EnvironmentFile")
            self.assertIn("-/etc/default/report-mark-sync", vals, "共用設定仍應讀")
        backup = REPO_ROOT / "scripts" / "db_backup.sh"
        self.assertIn("/etc/default/report-mark-sync", backup.read_text(encoding="utf-8"),
                      "這條斷言的前提是 db_backup.sh 仍自行讀那個檔；前提消失就要重新評估落點")

    def test_no_webhook_value_committed_anywhere(self):
        for f in (HANDLER, SERVICE, SYSTEMD_DIR / "report-mark-alert@.service",
                  SYSTEMD_DIR / "report-mark-alert.sh"):
            body = f.read_text(encoding="utf-8")
            with self.subTest(file=f.name):
                self.assertNotRegex(body, r"https://hooks\.", "疑似寫死 webhook URL")
                self.assertNotRegex(body, r"REPORT_MARK_ALERT_WEBHOOK=\S", "不得寫死值")

    def test_alert_script_also_keeps_url_out_of_argv(self):
        code_only = "\n".join(
            ln for ln in (SYSTEMD_DIR / "report-mark-alert.sh").read_text(encoding="utf-8").splitlines()
            if not ln.strip().startswith("#")
        )
        self.assertIn("-K -", code_only)
        self.assertNotRegex(code_only, r'curl[^\n]*"\$REPORT_MARK_ALERT_WEBHOOK"')


class InFlightObservationTests(unittest.TestCase):
    """**in_flight 是觀測的生命週期，不是健康結論。**

    2026-08-20 的真實中斷（00:48:03–00:52:57，4 分 54 秒）證明舊語意會整場靜默：

        P4  00:49:03 Starting ──────────► 00:49:49 fail（exit 2）
                        P5 00:49:44 ▲ 落在執行窗內 → in_flight → web skip
        P4  00:51:13 Starting ──────────► 00:51:59 fail（exit 2）
                        P5 00:51:58 ▲ 落在執行窗內 → in_flight → web skip

    P4 偵測到了，P5 全程零 FIRING。根因是**探針失敗時執行窗暴增**：健康時約 30ms，
    失敗時 3 次重試共 46s（3×5s timeout ＋ 2×15s wait），佔 P5 週期的 35%——
    連續兩次撞上一點都不意外。timer 抖動只降低碰撞機率，不是正確性機制。

    另有一個實測否定的假設：systemd 在新一輪執行開始時把 `Result` 重設為 `success`、
    `ExecMainStatus` 重設為 `0`，**即使上一輪是 exit-code**。所以 in-flight 期間讀
    `Result` 不但無用，還會給出「健康」的錯誤結論。上一筆結論只能由 P5 自己記住。
    """

    def setUp(self):
        self.h = _Harness(webhook="http://example.invalid/hook")
        self.addCleanup(self.h.close)

    # **所有情境用小尺度表達。** 真實中斷的數字（138s/272s）超過 CI runner 的 uptime
    # （實測 85.6s），單調時鐘上根本不存在那麼久以前——2026-08-20 這批測試第一次
    # 送 CI 就是這樣紅的。門檻等比例縮小後，語意完全相同而與 uptime 無關；
    # 預設值的真實數字改由 test_default_thresholds_are_derived_from_p4_contract 靜態釘住。
    SCALED = {
        "INCIDENT_OBS_TRUST_SECONDS": "6",
        "INCIDENT_BLIND_CRITICAL_SECONDS": "20",
        "INCIDENT_STALE_SECONDS": "12",
        "INCIDENT_PROBE_MAX_INFLIGHT": "10",
    }

    def _run(self, **env):
        return self.h.run(**{**self.SCALED, **env})

    def _inflight(self, **kw):
        """讓探針處於執行中（ExecMain* 被 systemd 歸零）。"""
        kw.setdefault("probe_state", "activating")
        kw.setdefault("mono", 0)
        kw.setdefault("probe_start_mono", self.h._now_mono() - 5_000_000)  # 已跑 5s
        self.h.set_timer(**kw)

    # ── CASE 1 ────────────────────────────────────────────────────────────
    def test_completed_ok_then_short_inflight_is_not_an_incident(self):
        self.h.seed_observation(0, age_seconds=2, result="success")
        self._inflight()
        p = self._run()
        w = last_emit(p.stdout)
        self.assertEqual(w["action"], "noop", p.stdout)
        self.assertEqual(w["incident"], "CLOSED")
        self.assertEqual(w["current_probe"], "in_flight")
        self.assertEqual(w["last_completed"], "ok")
        self.assertNotEqual(w["last_completed_age"], "-", "必須讓 operator 看到理由")
        self.assertEqual(self.h.webhook_calls(), 0)

    # ── CASE 2：這是修掉真實中斷的那一條 ──────────────────────────────────
    def test_completed_fail_then_inflight_opens_web_incident(self):
        """**絕不能 action=skip 讓 outage 消失。**"""
        self.h.seed_observation(1, age_seconds=2, result="exit-code")
        self._inflight()
        p = self._run()
        w = last_emit(p.stdout)
        self.assertEqual(w["action"], "firing", p.stdout)
        self.assertEqual(w["severity"], "CRITICAL")
        self.assertEqual(w["incident"], "FIRING")
        self.assertEqual(w["current_probe"], "in_flight")
        self.assertEqual(w["last_completed"], "fail")
        self.assertEqual(self.h.state["state"], "FIRING")
        self.assertEqual(self.h.webhook_calls(), 1)

    # ── CASE 3 ────────────────────────────────────────────────────────────
    def test_fail_inflight_fail_does_not_duplicate_firing(self):
        self.h.seed_observation(1, age_seconds=2, result="exit-code")
        self._inflight()
        self._run()
        self.h.set_timer(probe_state="inactive", exit_status=1, result="exit-code",
                         mono=self.h._now_mono())
        self._run()
        self.assertEqual(self.h.webhook_calls(), 1, "同一事件重複 FIRING")
        self.assertEqual(self.h.state["state"], "FIRING")

    # ── CASE 4 ────────────────────────────────────────────────────────────
    def test_fail_inflight_then_ok_resolves_once(self):
        self.h.seed_observation(1, age_seconds=2, result="exit-code")
        self._inflight()
        self._run()
        self.h.set_timer(probe_state="inactive", exit_status=0, result="success",
                         mono=self.h._now_mono())
        p = self._run()
        self.assertEqual(last_emit(p.stdout)["action"], "resolved")
        self.assertEqual(self.h.webhook_calls(), 2)
        self.assertEqual(self.h.state, {})

    # ── CASE 5 ────────────────────────────────────────────────────────────
    def test_repeated_short_inflight_after_ok_never_false_positives(self):
        self.h.seed_observation(0, age_seconds=2, result="success")
        for _ in range(4):
            self._inflight()
            p = self._run()
            self.assertEqual(last_emit(p.stdout)["action"], "noop", p.stdout)
        self.assertEqual(self.h.webhook_calls(), 0)

    # ── CASE 6 ────────────────────────────────────────────────────────────
    def test_inflight_beyond_max_duration_is_probe_stuck(self):
        """超過 P4 契約上限（45s，unit 硬上限 90s）代表探針卡住，不是服務故障。"""
        self.h.seed_observation(0, age_seconds=2, result="success")
        self._inflight(probe_start_mono=self.h._now_mono() - 20_000_000)  # 已跑 20s > 上限 10s
        p = self._run()
        m = monitor_emit(p.stdout)
        self.assertEqual(m["status"], "monitor_blind", p.stdout)
        self.assertEqual(m["reason"], "probe_stuck")
        self.assertEqual(m["severity"], "CRITICAL")
        self.assertEqual(m["action"], "firing")

    # ── CASE 7／8：無任何已完成觀測 ────────────────────────────────────────
    def test_no_completed_observation_inflight_within_bootstrap(self):
        self._inflight(timer_enter_mono=self.h._now_mono())
        m = monitor_emit(self._run(INCIDENT_BOOTSTRAP_SECONDS="300").stdout)
        self.assertEqual(m["status"], "in_flight")
        self.assertEqual(m["last_completed"], "none")
        self.assertEqual(self.h.webhook_calls(), 0)

    def test_no_completed_observation_not_inflight_beyond_bootstrap_is_blind(self):
        self.h.set_timer(probe_state="inactive", mono=0, timer_enter_mono=1)
        m = monitor_emit(self._run().stdout)
        self.assertEqual(m["status"], "monitor_blind")
        self.assertEqual(m["reason"], "observation_missing")

    # ── CASE 9：漏讀 vs 過期是兩件事 ──────────────────────────────────────
    def test_cache_older_than_one_cycle_means_missed_observation(self):
        """**真實中斷的形狀。**

        00:47:26 讀到 OK；P4 在 00:49:49 完成一筆 fail 而 P5 沒讀到；
        00:51:58 再取樣時快取已 272s。若無信任上限就會沿用那筆 OK 而繼續靜默。
        """
        self.h.seed_observation(0, age_seconds=10, result="success")   # 真實中斷的 272s，等比縮小
        self._inflight()
        p = self._run()
        m = monitor_emit(p.stdout)
        self.assertEqual(m["status"], "monitor_blind", p.stdout)
        self.assertEqual(m["reason"], "observation_missed")
        self.assertNotEqual(m["action"], "noop", "不得靜默")
        self.assertEqual(self.h.webhook_calls(), 1)

    def test_cache_within_trust_window_is_still_used(self):
        """138s（一個週期內）時沿用 OK 是**正確**的——那當下確實是最新的已完成觀測。"""
        self.h.seed_observation(0, age_seconds=3, result="success")    # 真實中斷的 138s，等比縮小
        self._inflight()
        w = last_emit(self._run().stdout)
        self.assertEqual(w["action"], "noop")
        self.assertEqual(w["last_completed"], "ok")

    def test_very_old_cache_escalates_to_critical(self):
        self.h.seed_observation(0, age_seconds=30, result="success")
        self._inflight()
        m = monitor_emit(self._run().stdout)
        self.assertEqual(m["reason"], "observation_stale")
        self.assertEqual(m["severity"], "CRITICAL")

    # ── CASE 10：重開機 ───────────────────────────────────────────────────
    def test_cache_from_another_boot_is_not_reused(self):
        self.h.seed_observation(1, age_seconds=30, result="exit-code",
                                boot_id="00000000-1111-2222-3333-444444444444")
        self._inflight(timer_enter_mono=self.h._now_mono())
        p = self._run(INCIDENT_BOOTSTRAP_SECONDS="300")
        w = last_emit(p.stdout)
        self.assertEqual(w["last_completed"], "none", "跨開機的快取不得沿用")
        self.assertEqual(w["action"], "skip")
        self.assertEqual(self.h.webhook_calls(), 0, "不得依據別次開機的 fail 開事件")

    # ── CASE 11：損毀快取 ─────────────────────────────────────────────────
    def test_malformed_cache_does_not_crash_or_execute(self):
        for content in ("", "garbage\n", "monotonic=abc\nstatus=x\n",
                        "boot_id=$(id)\nmonotonic=1\nstatus=1\n",
                        "monotonic=-5\nstatus=1\n", "status=1\n",
                        "boot_id=not-a-uuid\nmonotonic=1\nstatus=1\n"):
            with self.subTest(content=content):
                self.h.state_dir.mkdir(parents=True, exist_ok=True)
                (self.h.state_dir / "probe_observation.state").write_text(content, encoding="utf-8")
                self._inflight(timer_enter_mono=self.h._now_mono())
                r = self._run(INCIDENT_BOOTSTRAP_SECONDS="300")
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertNotIn("uid=", r.stdout, "快取內容被 shell 展開了")
                self.assertTrue(monitor_emit(r.stdout))

    # ── CASE 12 ───────────────────────────────────────────────────────────
    def test_query_failure_while_inflight_is_monitor_blind(self):
        h = _Harness(webhook="http://example.invalid/hook", show_rc=1)
        self.addCleanup(h.close)
        m = monitor_emit(h.run().stdout)
        self.assertEqual(m["status"], "monitor_blind")
        self.assertEqual(m["reason"], "query_failed")

    # ── CASE 13／14：兩類事件並存與獨立解除 ────────────────────────────────
    def test_web_incident_from_cache_coexists_with_monitor_blind(self):
        self.h.seed_observation(1, age_seconds=2, result="exit-code")
        self._inflight()
        self._run()                                   # web FIRING（來自快取）
        self.assertEqual(self.h.state["state"], "FIRING")
        self.h.set_timer(timer_enabled="disabled", timer_active="inactive",
                         probe_state="inactive", mono=0)
        self._run()                                   # monitor FIRING
        self.assertEqual(self.h.state["state"], "FIRING", "web 事件不得被覆蓋")
        self.assertEqual(self.h.monitor_state["state"], "FIRING")

    def test_monitor_recovery_does_not_resolve_web_from_cache(self):
        self.h.seed_observation(1, age_seconds=2, result="exit-code")
        self._inflight()
        self._run()
        self.h.set_timer(probe_state="inactive", exit_status=1, result="exit-code",
                         mono=self.h._now_mono())
        self._run()
        self.assertEqual(self.h.state["state"], "FIRING")
        self.assertEqual(self.h.monitor_state.get("state", "CLOSED"), "CLOSED")

    # ── CASE 17：不同的失敗耗時 ───────────────────────────────────────────
    def test_semantics_hold_across_probe_durations(self):
        for secs in (0.03, 1, 3, 8):
            with self.subTest(inflight_seconds=secs):
                h = _Harness(webhook="http://example.invalid/hook")
                self.addCleanup(h.close)
                h.seed_observation(1, age_seconds=2, result="exit-code")
                h.set_timer(probe_state="activating", mono=0,
                            probe_start_mono=h._now_mono() - int(secs * 1_000_000))
                w = last_emit(h.run(**self.SCALED).stdout)
                self.assertEqual(w["action"], "firing", f"{secs}s: {w}")
                self.assertEqual(h.webhook_calls(), 1)

    # ── CASE 18：固定相位碰撞——每一次取樣都落在執行窗內 ──────────────────
    def test_outage_detected_even_when_every_sample_lands_in_flight(self):
        """**真實中斷的核心迴歸測試。**

        模擬 P5 每一輪都恰好落在 P4 的執行窗內。舊語意下整場零 FIRING；
        新語意下必須在漏讀被察覺時發出事件，且不得重複。
        """
        # 第 1 輪：讀到一筆完成的 OK（中斷尚未開始）
        self.h.set_timer(probe_state="inactive", exit_status=0, result="success",
                         mono=self.h._now_mono())
        self._run()
        self.assertEqual(self.h.webhook_calls(), 0)
        self.assertEqual(self.h.observation_cache["status"], "0")

        # 第 2 輪：中斷開始，P5 落在執行窗內；快取 138s（信任窗內）→ 仍判健康，正確
        self.h.seed_observation(0, age_seconds=3, result="success")    # 真實 138s，信任窗內
        self._inflight()
        p2 = self._run()
        self.assertEqual(last_emit(p2.stdout)["action"], "noop")

        # 第 3 輪：又落在執行窗內，但快取已 272s → 漏讀 → 必須出聲
        self.h.seed_observation(0, age_seconds=10, result="success")   # 真實 272s，超過信任上限
        self._inflight()
        p3 = self._run()
        m3 = monitor_emit(p3.stdout)
        self.assertEqual(m3["status"], "monitor_blind", p3.stdout)
        self.assertEqual(m3["reason"], "observation_missed")
        self.assertEqual(self.h.webhook_calls(), 1, "漏讀必須且只發一次")

        # 第 4 輪：仍漏讀 → 不得重複 FIRING
        self.h.seed_observation(0, age_seconds=15, result="success")
        self._inflight()
        p4 = self._run()
        self.assertNotEqual(monitor_emit(p4.stdout)["action"], "firing")
        self.assertEqual(self.h.webhook_calls(), 1)

    def test_both_component_lines_report_the_same_observation(self):
        """**兩行描述同一輪，欄位不得互相矛盾。**

        2026-08-20 部署後實測到 `component=web last_completed_age=58` 而
        `component=monitor` 是 179——因為重算被放在 web 分派裡，monitor 早就 emit 過了。
        同一個缺陷只修好一半，而「一半正確的輸出」比全錯更難察覺。
        """
        self.h.set_timer(probe_state="inactive", exit_status=0, result="success",
                         mono=self.h._now_mono())
        out = self._run().stdout
        w, m = last_emit(out), monitor_emit(out)
        self.assertEqual(w["last_completed"], m["last_completed"], out)
        self.assertEqual(w["last_completed_age"], m["last_completed_age"], out)
        self.assertEqual(w["current_probe"], m["current_probe"])
        self.assertLess(int(w["last_completed_age"]), 5)

    def test_both_lines_agree_while_in_flight(self):
        self.h.seed_observation(1, age_seconds=3, result="exit-code")
        self._inflight()
        out = self._run().stdout
        w, m = last_emit(out), monitor_emit(out)
        self.assertEqual(w["last_completed"], "fail")
        self.assertEqual(m["last_completed"], "fail")
        self.assertEqual(w["last_completed_age"], m["last_completed_age"])

    def test_last_completed_age_describes_the_observation_actually_used(self):
        """**欄位必須描述本輪實際用的那一筆，不是上一輪消費的那筆。**

        初版在載入快取當下就算好，於是 signal=ok 時顯示的是上一輪的觀測，年齡累積成
        「上一輪間隔 ＋ 那筆當時的年齡」。2026-08-20 部署後實測顯示 239s／255s，
        而快取其實完全同步、真實年齡只有 83.6s——逼近 OBS_TRUST=240 的門檻值，
        會讓人以為觀測已經四分鐘沒更新。
        """
        # 先讓快取裡是一筆「很舊」的觀測
        self.h.seed_observation(0, age_seconds=30, result="success")
        # 本輪讀到一筆全新的完成觀測（probe idle）
        self.h.set_timer(probe_state="inactive", exit_status=0, result="success",
                         mono=self.h._now_mono())
        w = last_emit(self._run().stdout)
        self.assertEqual(w["current_probe"], "idle")
        self.assertEqual(w["last_completed"], "ok")
        self.assertLess(int(w["last_completed_age"]), 5,
                        f"應描述本輪那筆新鮮觀測，實得 {w['last_completed_age']}s")

    def test_last_completed_age_uses_cache_when_in_flight(self):
        self.h.seed_observation(1, age_seconds=2, result="exit-code")
        self._inflight()
        w = last_emit(self._run().stdout)
        self.assertEqual(w["current_probe"], "in_flight")
        self.assertEqual(w["last_completed"], "fail")
        self.assertGreaterEqual(int(w["last_completed_age"]), 2)
        self.assertLess(int(w["last_completed_age"]), 10)

    def test_no_scenario_exceeds_a_plausible_ci_uptime(self):
        """**測測試自己。** 這一類錯誤 2026-08 已經犯了三次：

        用真實世界的秒數（138s／272s／1000s）表達「多久以前」，在開發機（uptime 數十
        小時）通過，在剛開機的 CI runner（實測 **85.6s**）卻因為單調時鐘上不存在那麼久
        以前而失敗。`seed_observation` 的斷言會大聲擋下（不會靜默夾成 0），但那仍是紅燈。

        情境一律用小尺度 ＋ 等比縮小的門檻表達；真實數字由
        `test_default_thresholds_are_derived_from_p4_contract` 靜態釘住。
        """
        import re

        src = Path(__file__).read_text(encoding="utf-8")
        ages = [int(m) for m in re.findall(r"age_seconds=(\d+)", src)]
        self.assertTrue(ages, "找不到任何 age_seconds，這條守門會失效")
        self.assertLessEqual(
            max(ages), 60,
            f"有情境用了 {max(ages)}s 的年齡；CI runner uptime 實測僅 85.6s，"
            "情境請改用小尺度並等比縮小門檻",
        )
        starts = [int(m) for m in re.findall(r"_now_mono\(\) - (\d[\d_]*)", src)]
        if starts:
            self.assertLessEqual(
                max(starts), 60_000_000,
                f"有情境用了 {max(starts)/1e6:.0f}s 的執行時長，同上",
            )

    def test_default_thresholds_are_derived_from_p4_contract(self):
        """真實數字釘在這裡（情境測試用等比小尺度，因為 CI runner uptime 只有約 85s）。

        OBS_TRUST=240：P4 每 ~130s 完成一輪，一個週期上界 140s ＋ unit 硬上限 90s = 230s。
        PROBE_MAX_INFLIGHT=120：契約最壞 3×5s ＋ 2×15s = 45s，unit 硬上限 90s。
        """
        body = HANDLER.read_text(encoding="utf-8")
        self.assertIn("INCIDENT_OBS_TRUST_SECONDS:-240", body)
        self.assertIn("INCIDENT_PROBE_MAX_INFLIGHT:-120", body)
        probe = (REPO_ROOT / "scripts" / "check_web_health.sh").read_text(encoding="utf-8")
        self.assertIn("HEALTH_RETRIES:-3", probe, "門檻的推導前提是 3 次重試")
        self.assertIn("HEALTH_RETRY_WAIT:-15", probe, "推導前提是每次等 15s")
        unit = (SYSTEMD_DIR / "report-mark-health.service").read_text(encoding="utf-8")
        self.assertIn("TimeoutStartSec=90", unit, "推導前提是 unit 硬上限 90s")

    def test_structured_output_exposes_lifecycle_and_conclusion_separately(self):
        """只印 status=in_flight action=skip 正是 operator 看不出問題的原因。"""
        self.h.seed_observation(1, age_seconds=2, result="exit-code")
        self._inflight()
        w = last_emit(self._run().stdout)
        for k in ("current_probe", "last_completed", "last_completed_age"):
            self.assertIn(k, w, f"缺欄位 {k}")
        self.assertIn(w["current_probe"], ("idle", "in_flight"))
        self.assertIn(w["last_completed"], ("ok", "fail", "tooling", "none"))

    def test_cache_is_written_only_on_a_completed_observation(self):
        self._inflight(timer_enter_mono=self.h._now_mono())
        self._run(INCIDENT_BOOTSTRAP_SECONDS="300")
        self.assertEqual(self.h.observation_cache, {}, "執行中不得寫快取")
        self.h.set_timer(probe_state="inactive", exit_status=0, result="success",
                         mono=self.h._now_mono())
        self._run()
        self.assertEqual(self.h.observation_cache["status"], "0")

    def test_cache_is_a_separate_file_from_web_state(self):
        """web.state 在 RESOLVED 時會被 rm——快取不能跟著被抹掉。"""
        self.h.set_timer(probe_state="inactive", exit_status=1, result="exit-code",
                         mono=self.h._now_mono())
        self._run()
        self.h.set_timer(probe_state="inactive", exit_status=0, result="success",
                         mono=self.h._now_mono())
        self._run()
        self.assertEqual(self.h.state, {}, "web.state 應已移除")
        self.assertTrue(self.h.observation_cache, "觀測快取不得被一起刪掉")


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
