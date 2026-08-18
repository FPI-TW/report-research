# tests/test_web_health_probe.py
"""應用層健康探針（P4）的行為與契約守門。

為什麼要有這一支：2026-08-18 的生產中斷持續 4 小時 50 分，期間
`systemctl is-active report-mark-web.service` 全程顯示 `active`，而 journal 的
access log 在 13:40 之前是 **0 筆**。uvicorn 先跑 lifespan 再 bind，bind 失敗後
行程仍存活約 10 秒（背景載入 BGE-M3），配合 `Restart=always`／`RestartSec=3`，
任何時間點查 `is-active` 都很可能看到 `active`——**這支探針存在的唯一理由就是
不再相信那個訊號**。

這裡守的東西分兩類：

1. **行為**：退出碼契約與 stdout 欄位。P5 會直接消費那一行 key=value，
   欄位語意漂移不會有任何症狀，只會讓告警分級悄悄失準。
2. **靜態契約**：探針不得相依 Python／uv／.venv。那次中斷的根因正是 venv 損毀
   （uv 的 .tmp→rename 在 9p 上以 os error 2 失敗），若探針跟著相依 Python，
   它會與被監控的服務一起死——那時最需要它，而它不在。

刻意不做的：不測 systemd 真的排程（那是 L4-B 那種需要等待自然觸發的驗證，
不屬單元測試）；不對真實生產站台發請求（測試必須能在沒有服務的機器上跑）。
"""
import os
import re
import subprocess
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread

REPO_ROOT = Path(__file__).resolve().parents[1]
PROBE = REPO_ROOT / "scripts" / "check_web_health.sh"
SYSTEMD_DIR = REPO_ROOT / "deploy" / "systemd"
SERVICE = SYSTEMD_DIR / "report-mark-health.service"
TIMER = SYSTEMD_DIR / "report-mark-health.timer"

EXIT_OK, EXIT_HTTP, EXIT_PROCESS, EXIT_GRACE, EXIT_TOOLING = 0, 1, 2, 3, 4

# stdout 契約：P5 消費的欄位。少一個都會讓告警分級失準且無症狀。
REQUIRED_FIELDS = ("ts", "component", "probe", "status", "http_code", "latency_ms", "reason")


def _directives(path: Path, key: str) -> list[str]:
    """取出 unit 內所有 `key=` 的值，**略過註解行**。

    刻意不用「整檔子字串比對」：本檔的 unit 註解裡就寫著「刻意不設
    RandomizedDelaySec」「不設 Persistent=true」，用文字比對會把解釋規約的註解
    當成違規，那是假守門——它擋不住真的設定，卻會被正確的文件觸發。
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


def run_probe(env_extra=None, timeout=90):
    env = dict(os.environ)
    # 測試一律短逾時、少重試，否則單支測試會跑掉數分鐘。
    env.update({"HEALTH_RETRIES": "1", "HEALTH_RETRY_WAIT": "1", "HEALTH_TIMEOUT": "3"})
    env.update(env_extra or {})
    p = subprocess.run(["bash", str(PROBE)], capture_output=True, text=True, env=env, timeout=timeout)
    return p


def parse(line):
    return dict(kv.split("=", 1) for kv in line.strip().split() if "=" in kv)


class _Server:
    """啟一個回固定狀態碼的本機 HTTP 伺服器；用 127.0.0.1 避開 ::1 解析。"""

    def __init__(self, status=200, body=b'{"status":"ok"}', delay=0.0):
        self.status, self.body, self.delay = status, body, delay
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                if outer.delay:
                    import time

                    time.sleep(outer.delay)
                self.send_response(outer.status)
                self.end_headers()
                self.wfile.write(outer.body)

            def log_message(self, *a):
                pass

        self.srv = HTTPServer(("127.0.0.1", 0), H)
        self.port = self.srv.server_port

    def __enter__(self):
        Thread(target=self.srv.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *a):
        self.srv.shutdown()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}/healthz"


class ProbeBehaviourTests(unittest.TestCase):
    def test_http_200_is_healthy(self):
        with _Server(200) as s:
            p = run_probe({"HEALTH_URL": s.url})
        self.assertEqual(p.returncode, EXIT_OK, p.stdout + p.stderr)
        f = parse(p.stdout)
        self.assertEqual(f["status"], "ok")
        self.assertEqual(f["http_code"], "200")

    def test_http_503_is_failure(self):
        """/healthz 的 503 語意是 DB 不可用——必須算失敗，不能因為「連得上」就放行。"""
        with _Server(503, b'{"status":"degraded"}') as s:
            p = run_probe({"HEALTH_URL": s.url})
        self.assertEqual(p.returncode, EXIT_HTTP)
        f = parse(p.stdout)
        self.assertEqual(f["status"], "fail")
        self.assertEqual(f["http_code"], "503")

    def test_connection_refused_or_timeout_is_failure(self):
        """**2026-08-18 的實際失效型態**：uvicorn 沒綁上時是「連不上」而非回 503。

        實測本機在 WSL mirrored networking 下，連沒有 listener 的埠拿到的是逾時
        （curl rc=28）而非拒絕（rc=7），因為 Windows 側是丟棄而非拒絕。
        兩種都必須算失敗，斷言刻意不綁定其中一種。
        """
        p = run_probe({"HEALTH_URL": "http://127.0.0.1:59991/healthz"})
        self.assertEqual(p.returncode, EXIT_HTTP)
        f = parse(p.stdout)
        self.assertEqual(f["status"], "fail")
        self.assertEqual(f["http_code"], "000")
        self.assertIn(f["reason"], ("connection_refused", "timeout"))

    def test_timeout_is_failure(self):
        with _Server(200, delay=5.0) as s:
            p = run_probe({"HEALTH_URL": s.url, "HEALTH_TIMEOUT": "1"})
        self.assertEqual(p.returncode, EXIT_HTTP)
        self.assertEqual(parse(p.stdout)["reason"], "timeout")

    def test_retry_then_success_is_healthy(self):
        """重試在單次執行內完成——跨執行狀態是 P5 的東西，P4 不得持有。"""
        with _Server(200) as s:
            p = run_probe({"HEALTH_URL": s.url, "HEALTH_RETRIES": "3", "HEALTH_RETRY_WAIT": "1"})
        self.assertEqual(p.returncode, EXIT_OK)
        self.assertEqual(parse(p.stdout)["status"], "ok")

    def test_all_retries_fail(self):
        p = run_probe(
            {"HEALTH_URL": "http://127.0.0.1:59992/healthz", "HEALTH_RETRIES": "2", "HEALTH_RETRY_WAIT": "1"}
        )
        self.assertEqual(p.returncode, EXIT_HTTP)
        self.assertEqual(parse(p.stdout)["attempts"], "2")

    def test_unexpected_body_with_200_is_still_healthy(self):
        """刻意只看 http_code，不解析 body——對回應格式過度耦合會讓探針比服務還脆。"""
        with _Server(200, b"not json at all") as s:
            p = run_probe({"HEALTH_URL": s.url})
        self.assertEqual(p.returncode, EXIT_OK)

    def test_degraded_body_with_200_is_healthy(self):
        """現行 /healthz 不會這樣回（degraded 一律配 503），但契約要能表達這個情況。"""
        with _Server(200, b'{"status":"degraded"}') as s:
            p = run_probe({"HEALTH_URL": s.url})
        self.assertEqual(p.returncode, EXIT_OK)

    def test_missing_curl_is_tooling_error_not_health_failure(self):
        """探針自己不能執行 != 服務壞掉。P5 要把它當 WARNING 而非 CRITICAL。"""
        p = subprocess.run(
            ["/usr/bin/bash", str(PROBE)],
            capture_output=True, text=True, env={"PATH": "/nonexistent"}, timeout=30,
        )
        self.assertEqual(p.returncode, EXIT_TOOLING)
        self.assertIn("curl_not_found", p.stdout)

    def test_stdout_has_every_field_p5_consumes(self):
        with _Server(200) as s:
            p = run_probe({"HEALTH_URL": s.url})
        f = parse(p.stdout)
        for key in REQUIRED_FIELDS:
            with self.subTest(field=key):
                self.assertIn(key, f)
        self.assertEqual(f["component"], "web")
        self.assertEqual(f["probe"], "local_http")

    def test_status_vocabulary_is_closed(self):
        with _Server(503) as s:
            fail = parse(run_probe({"HEALTH_URL": s.url}).stdout)["status"]
        with _Server(200) as s:
            ok = parse(run_probe({"HEALTH_URL": s.url}).stdout)["status"]
        self.assertIn(ok, ("ok", "fail", "grace", "tooling"))
        self.assertIn(fail, ("ok", "fail", "grace", "tooling"))


class GraceTests(unittest.TestCase):
    """啟動寬限：**必須不能在 crash loop 下無限延長**。

    這是本設計最容易寫錯的一條。若寬限只看 `ActiveEnterTimestamp`，
    `Restart=always` ＋ `RestartSec=3` 會讓它每 3 秒更新一次，「距今 < 60 秒」
    於是恆為真——寬限會永遠抑制告警，**完整重現 2026-08-18 那次「壞了但沒人知道」**
    （當時累積 550 次重啟）。因此判定必須同時要求 `NRestarts == 0`。
    """

    def test_grace_requires_zero_restarts(self):
        """**必須比對程式碼本體，不能比對整檔**——本檔與腳本的註解都寫著 NRestarts，
        用整檔比對的話把實際判定刪掉仍然會過（實測反轉實驗確認過這件事）。
        """
        body = "\n".join(
            ln for ln in PROBE.read_text(encoding="utf-8").splitlines() if not ln.strip().startswith("#")
        )
        self.assertIn("NRestarts", body, "寬限判定必須查 NRestarts，否則 crash loop 會被永久抑制")
        self.assertIn("ActiveEnterTimestamp", body)
        # 光是「查了」還不夠：必須真的拿它當條件之一
        self.assertRegex(body, r'\$\{restarts:-0\}"?\s*=\s*"0"')

    def test_grace_not_applied_when_unit_missing(self):
        """探不到 unit 時不得誤判成寬限——寧可誤報也不要漏報。"""
        p = run_probe(
            {"HEALTH_URL": "http://127.0.0.1:59993/healthz", "HEALTH_UNIT": "definitely-not-a-unit.service"}
        )
        self.assertNotEqual(p.returncode, EXIT_GRACE)
        self.assertIn(p.returncode, (EXIT_HTTP, EXIT_PROCESS))


class ProbeIndependenceTests(unittest.TestCase):
    """探針不得與被監控的東西共命運。"""

    def test_does_not_import_application_code(self):
        src = PROBE.read_text(encoding="utf-8")
        for forbidden in ("app.services", "app.config", "web.server", "import app"):
            with self.subTest(token=forbidden):
                self.assertNotIn(forbidden, src)

    def test_does_not_depend_on_python_or_venv(self):
        """2026-08-18 的根因是 venv 損毀——探針相依它等於在最需要時一起死。"""
        body = "\n".join(
            ln for ln in PROBE.read_text(encoding="utf-8").splitlines() if not ln.strip().startswith("#")
        )
        for forbidden in ("uv run", ".venv", "python3 ", "python "):
            with self.subTest(token=forbidden):
                self.assertNotIn(forbidden, body)

    def test_uses_loopback_ip_not_localhost(self):
        """uvicorn 綁 0.0.0.0（只有 IPv4）；localhost 常先解析到 ::1 → 探針自造假故障。"""
        src = PROBE.read_text(encoding="utf-8")
        self.assertIn("http://127.0.0.1:8097/healthz", src)
        self.assertNotIn("http://localhost:8097", src)

    def test_strict_shell_options(self):
        """`set -u` 抓未定義變數。刻意**不用** `set -e`：需要自己接 curl 的退出碼。"""
        first = [ln for ln in PROBE.read_text(encoding="utf-8").splitlines() if ln.startswith("set ")]
        self.assertTrue(first, "腳本必須宣告 shell 選項")
        self.assertIn("-u", first[0])


class SystemdContractTests(unittest.TestCase):
    def test_unit_files_exist(self):
        self.assertTrue(SERVICE.is_file())
        self.assertTrue(TIMER.is_file())

    def test_execstart_points_at_an_existing_script(self):
        m = re.search(r"ExecStart=.*?/scripts/([A-Za-z0-9_.-]+\.sh)", SERVICE.read_text(encoding="utf-8"))
        self.assertIsNotNone(m, "ExecStart 必須指向 scripts/ 下的腳本")
        self.assertTrue((REPO_ROOT / "scripts" / m.group(1)).is_file())

    def test_execstart_uses_bash_not_bare_exec(self):
        """走 `bash <script>` 才不依賴 exec bit——2026-08-18 遷移剛因此踩過坑。"""
        self.assertRegex(SERVICE.read_text(encoding="utf-8"), r"ExecStart=/usr/bin/bash -c 'exec /usr/bin/bash ")

    def test_service_declares_grace_exit_as_success(self):
        """寬限退出碼不得讓 unit 變 failed，且必須用 SuccessExitStatus 而非吞掉錯誤。"""
        self.assertIn("3", _directives(SERVICE, "SuccessExitStatus"))

    def test_service_has_onfailure_alert(self):
        self.assertEqual(_directives(SERVICE, "OnFailure"), ["report-mark-alert@%n.service"])

    def test_service_home_is_hardcoded_not_percent_h(self):
        homes = [v for v in _directives(SERVICE, "Environment") if v.startswith("HOME=")]
        self.assertEqual(homes, ["HOME=/home/kashionz"])
        self.assertNotIn("%h", homes[0])

    def test_service_path_excludes_nvm_and_uv(self):
        """探針不需要 claude／uv；把它們放進 PATH 會誘導未來的人加 Python 相依。"""
        envs = [v for v in _directives(SERVICE, "Environment") if v.startswith("PATH=")]
        self.assertEqual(len(envs), 1)
        self.assertNotIn(".nvm", envs[0])
        self.assertNotIn(".local/bin", envs[0])

    def test_timeout_is_strictly_below_timer_interval(self):
        """必須是**嚴格**小於：取等號會讓逾時的那一輪正好撞上下一次觸發。"""
        timeout = int(_directives(SERVICE, "TimeoutStartSec")[0])
        interval = _directives(TIMER, "OnUnitActiveSec")[0]
        seconds = int(re.match(r"(\d+)min", interval).group(1)) * 60
        self.assertLess(timeout, seconds, "單次執行的上限必須嚴格小於觸發間隔，否則會疊輪")

    def test_timer_interval_is_two_minutes(self):
        self.assertEqual(_directives(TIMER, "OnUnitActiveSec"), ["2min"])

    def test_timer_is_not_persistent(self):
        """錯過的健康檢查沒有補跑價值；Persistent=true 還會在開機瞬間誤報。"""
        self.assertEqual(_directives(TIMER, "Persistent"), [])

    def test_timer_has_no_randomized_delay(self):
        """抖動只會讓偵測延遲不可預測，換不到任何東西。"""
        self.assertEqual(_directives(TIMER, "RandomizedDelaySec"), [])

    def test_no_stale_mnt_c_runtime_path(self):
        for f in (SERVICE, TIMER, PROBE):
            with self.subTest(file=f.name):
                self.assertNotIn("/mnt/c/Users/User/Desktop/Project/report-mark", f.read_text(encoding="utf-8"))


class BoundaryTests(unittest.TestCase):
    """P4 不得偷做 P5 的事。"""

    def test_probe_holds_no_persistent_state(self):
        body = "\n".join(
            ln for ln in PROBE.read_text(encoding="utf-8").splitlines() if not ln.strip().startswith("#")
        )
        for forbidden in (".incidents", "notify.sh", "FIRING", "RESOLVED", "webhook", "WEBHOOK"):
            with self.subTest(token=forbidden):
                self.assertNotIn(forbidden, body)

    def test_probe_does_not_write_any_file(self):
        body = "\n".join(
            ln for ln in PROBE.read_text(encoding="utf-8").splitlines() if not ln.strip().startswith("#")
        )
        self.assertNotIn(">>", body, "P4 是無狀態觀測，不得追加任何檔案")


if __name__ == "__main__":
    unittest.main()
