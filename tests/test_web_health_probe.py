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
import tempfile
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread

REPO_ROOT = Path(__file__).resolve().parents[1]
PROBE = REPO_ROOT / "scripts" / "check_web_health.sh"
SYSTEMD_DIR = REPO_ROOT / "deploy" / "systemd"
SERVICE = SYSTEMD_DIR / "report-mark-health.service"
TIMER = SYSTEMD_DIR / "report-mark-health.timer"

EXIT_OK, EXIT_HTTP, EXIT_PROCESS, EXIT_GRACE, EXIT_TOOLING, EXIT_DEGRADED = 0, 1, 2, 3, 4, 5
EXIT_STORAGE = 6
EXIT_LLM = 7

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

    def __init__(self, status=200, body=b'{"status":"ok"}', delay=0.0, storage_status=None,
                 llm_status=None, llm_body=b'{"llm":"exhausted"}'):
        self.status, self.body, self.delay = status, body, delay
        # /healthz/storage、/healthz/llm 的狀態碼；None＝與其他路徑相同（既有測試的行為不變）。
        self.storage_status = storage_status
        self.llm_status, self.llm_body = llm_status, llm_body
        self.paths: list[str] = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                outer.paths.append(self.path)
                if self.path == "/healthz/storage" and outer.storage_status is not None:
                    self.send_response(outer.storage_status)
                    self.end_headers()
                    self.wfile.write(b'{"storage":"x"}')
                    return
                if self.path == "/healthz/llm" and outer.llm_status is not None:
                    self.send_response(outer.llm_status)
                    self.end_headers()
                    self.wfile.write(outer.llm_body)
                    return
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


class _FakeSystemd:
    """把受控的 `systemctl` 放進 PATH 前段，讓探針的 L1 判定與**執行主機無關**。

    存在理由是 2026-08-19 CI 首跑的 4 個紅燈（`AssertionError: 2 != 1`）：探針的
    退出碼同時取決於兩個維度——`EXIT_HTTP` 是「unit 在 active 但 HTTP 失敗」，
    `EXIT_PROCESS` 是「unit 不在 active」——而當時的測試只控制 HTTP 那一維，
    另一維由執行主機決定。開發機剛好有一個 active 的 `report-mark-web.service`，
    CI 沒有，於是同一份測試在兩邊得到不同答案。**那不是測試，是巧合。**

    刻意不只硬編目前這幾個斷言需要的回應：探針讀三個 systemd 事實
    （`is-active`／`NRestarts`／`ActiveEnterTimestamp`），三個都可從這裡指定，
    寬限期那條路徑才有辦法被行為測試涵蓋而不是只用靜態比對。
    """

    def __init__(self, state="active", restarts=0, entered_age=3600, broken=False):
        # entered_age 預設遠大於 HEALTH_GRACE(60)：長跑後才壞掉的服務才是 L2 的樣子。
        # 傳 None 代表 systemd 給不出時間戳（探針必須據此判定不在寬限期）。
        self.state, self.restarts, self.entered_age, self.broken = state, restarts, entered_age, broken
        self._tmp = None

    def __enter__(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="fake-systemd-")
        self.dir = Path(self._tmp.name)
        self.log = self.dir / "calls.log"
        self.log.write_text("", encoding="utf-8")
        entered = (
            ""
            if self.entered_age is None
            else time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - self.entered_age))
        )
        body = f'#!/usr/bin/env bash\nprintf \'%s\\n\' "$*" >> "{self.log}"\n'
        if self.broken:
            # systemctl 存在但失敗（權限、無 systemd、DBus 不通）。探針對此的契約是
            # 落入 unit_unknown 而非 EXIT_TOOLING——EXIT_TOOLING 專屬於「缺 curl」。
            body += "exit 127\n"
        else:
            body += (
                'case "$1" in\n'
                "  is-active)\n"
                f"    printf '%s\\n' '{self.state}'\n"
                f"    [ '{self.state}' = active ] && exit 0 || exit 3\n"
                "    ;;\n"
                "  show)\n"
                '    for a in "$@"; do\n'
                '      case "$a" in\n'
                f"        NRestarts) printf '%s\\n' '{self.restarts}'; exit 0 ;;\n"
                f"        ActiveEnterTimestamp) printf '%s\\n' '{entered}'; exit 0 ;;\n"
                "      esac\n"
                "    done\n"
                "    ;;\n"
                "esac\n"
                "exit 0\n"
            )
        sc = self.dir / "systemctl"
        sc.write_text(body, encoding="utf-8")
        sc.chmod(0o755)
        return self

    def __exit__(self, *a):
        self._tmp.cleanup()

    @property
    def env(self):
        return {"PATH": f"{self.dir}:{os.environ['PATH']}"}

    def calls(self):
        return [ln for ln in self.log.read_text(encoding="utf-8").splitlines() if ln.strip()]


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
        with _FakeSystemd(state="active") as sd, _Server(503, b'{"status":"degraded"}') as s:
            p = run_probe({"HEALTH_URL": s.url, **sd.env})
        self.assertEqual(p.returncode, EXIT_HTTP, p.stdout + p.stderr)
        f = parse(p.stdout)
        self.assertEqual(f["status"], "fail")
        self.assertEqual(f["http_code"], "503")

    def test_connection_refused_or_timeout_is_failure(self):
        """**2026-08-18 的實際失效型態**：uvicorn 沒綁上時是「連不上」而非回 503。

        實測本機在 WSL mirrored networking 下，連沒有 listener 的埠拿到的是逾時
        （curl rc=28）而非拒絕（rc=7），因為 Windows 側是丟棄而非拒絕。
        兩種都必須算失敗，斷言刻意不綁定其中一種。
        """
        with _FakeSystemd(state="active") as sd:
            p = run_probe({"HEALTH_URL": "http://127.0.0.1:59991/healthz", **sd.env})
        self.assertEqual(p.returncode, EXIT_HTTP, p.stdout + p.stderr)
        f = parse(p.stdout)
        self.assertEqual(f["status"], "fail")
        self.assertEqual(f["http_code"], "000")
        self.assertIn(f["reason"], ("connection_refused", "timeout"))

    def test_timeout_is_failure(self):
        with _FakeSystemd(state="active") as sd, _Server(200, delay=5.0) as s:
            p = run_probe({"HEALTH_URL": s.url, "HEALTH_TIMEOUT": "1", **sd.env})
        self.assertEqual(p.returncode, EXIT_HTTP, p.stdout + p.stderr)
        self.assertEqual(parse(p.stdout)["reason"], "timeout")

    def test_retry_then_success_is_healthy(self):
        """重試在單次執行內完成——跨執行狀態是 P5 的東西，P4 不得持有。"""
        with _Server(200) as s:
            p = run_probe({"HEALTH_URL": s.url, "HEALTH_RETRIES": "3", "HEALTH_RETRY_WAIT": "1"})
        self.assertEqual(p.returncode, EXIT_OK)
        self.assertEqual(parse(p.stdout)["status"], "ok")

    def test_all_retries_fail(self):
        with _FakeSystemd(state="active") as sd:
            p = run_probe(
                {
                    "HEALTH_URL": "http://127.0.0.1:59992/healthz",
                    "HEALTH_RETRIES": "2",
                    "HEALTH_RETRY_WAIT": "1",
                    **sd.env,
                }
            )
        self.assertEqual(p.returncode, EXIT_HTTP, p.stdout + p.stderr)
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
        with _FakeSystemd(state="active") as sd, _Server(503) as s:
            fail = parse(run_probe({"HEALTH_URL": s.url, **sd.env}).stdout)["status"]
        with _Server(200) as s:
            ok = parse(run_probe({"HEALTH_URL": s.url}).stdout)["status"]
        self.assertIn(ok, ("ok", "fail", "grace", "tooling", "degraded"))
        self.assertIn(fail, ("ok", "fail", "grace", "tooling", "degraded"))


class DependencyCheckTests(unittest.TestCase):
    """L3：/healthz 綠不代表問答可用。

    2026-09-02 claude CLI 改裝成原生安裝後，web unit 的 PATH drop-in 仍指向 nvm 舊路徑，
    /api/ask 全數以 FileNotFoundError 失敗三小時，而本探針因為 /healthz 只探 DB 而全程 ok。
    這裡守的是：探針要讀 drop-in 宣告的 PATH 去找 claude，找不到就退出 5；且這一段
    **不得呼叫 systemctl**（健康路徑不相依 systemd 的不變量由 HostSystemdIsolationTests 釘住）。
    """

    def _dropin(self, tmp: Path, path_value: str, quoted=False) -> Path:
        d = tmp / "path.conf"
        line = f'Environment="PATH={path_value}"' if quoted else f"Environment=PATH={path_value}"
        d.write_text(f"[Service]\n# 註解裡的 Environment=PATH=/nope 不算數\n{line}\n", encoding="utf-8")
        return d

    def test_missing_dependency_on_unit_path_is_degraded(self):
        with tempfile.TemporaryDirectory() as tmp, _FakeSystemd(state="inactive") as sd, _Server(200) as s:
            t = Path(tmp)
            (t / "bin").mkdir()
            dropin = self._dropin(t, f"{t}/bin:/usr/bin")
            env = {"HEALTH_URL": s.url, "HEALTH_DEP_DROPIN": str(dropin), "HEALTH_DEP_BIN": "claude-x9"}
            p = run_probe({**env, **sd.env})
            calls = sd.calls()
        self.assertEqual(p.returncode, EXIT_DEGRADED, p.stdout + p.stderr)
        f = parse(p.stdout)
        self.assertEqual(f["status"], "degraded")
        self.assertEqual(f["http_code"], "200")
        self.assertEqual(f["reason"], "dep_missing_claude-x9")
        self.assertEqual(calls, [], "相依檢查不得呼叫 systemctl")

    def test_dependency_present_on_unit_path_is_healthy(self):
        with tempfile.TemporaryDirectory() as tmp, _Server(200) as s:
            t = Path(tmp)
            (t / "bin").mkdir()
            exe = t / "bin" / "claude-x9"
            exe.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            exe.chmod(0o755)
            dropin = self._dropin(t, f"/nonexistent-dir:{t}/bin", quoted=True)
            p = run_probe({"HEALTH_URL": s.url, "HEALTH_DEP_DROPIN": str(dropin), "HEALTH_DEP_BIN": "claude-x9"})
        self.assertEqual(p.returncode, EXIT_OK, p.stdout + p.stderr)
        self.assertEqual(parse(p.stdout)["status"], "ok")

    def test_non_executable_file_does_not_count(self):
        """symlink 斷掉或檔案沒有 exec bit 都等於「找不到」——Popen 一樣會炸。"""
        with tempfile.TemporaryDirectory() as tmp, _Server(200) as s:
            t = Path(tmp)
            (t / "bin").mkdir()
            (t / "bin" / "claude-x9").symlink_to(t / "gone")
            dropin = self._dropin(t, f"{t}/bin")
            p = run_probe({"HEALTH_URL": s.url, "HEALTH_DEP_DROPIN": str(dropin), "HEALTH_DEP_BIN": "claude-x9"})
        self.assertEqual(p.returncode, EXIT_DEGRADED, p.stdout + p.stderr)

    def test_missing_dropin_skips_the_check(self):
        """沒部署的機器（CI）判不出來；「判不出來」不是「壞了」。"""
        with _Server(200) as s:
            p = run_probe({"HEALTH_URL": s.url, "HEALTH_DEP_DROPIN": "/definitely/not/here.conf"})
        self.assertEqual(p.returncode, EXIT_OK, p.stdout + p.stderr)

    def test_dropin_without_path_line_skips_the_check(self):
        with tempfile.TemporaryDirectory() as tmp, _Server(200) as s:
            d = Path(tmp) / "path.conf"
            d.write_text("[Service]\nEnvironment=HOME=/home/x\n", encoding="utf-8")
            p = run_probe({"HEALTH_URL": s.url, "HEALTH_DEP_DROPIN": str(d)})
        self.assertEqual(p.returncode, EXIT_OK, p.stdout + p.stderr)

    def test_http_failure_takes_precedence_over_dependency(self):
        """服務連不上時不做相依檢查——那時退出碼要說的是 L1/L2，不是 L3。"""
        with tempfile.TemporaryDirectory() as tmp, _FakeSystemd(state="active") as sd, _Server(503) as s:
            t = Path(tmp)
            dropin = self._dropin(t, f"{t}/nope")
            p = run_probe({"HEALTH_URL": s.url, "HEALTH_DEP_DROPIN": str(dropin), **sd.env})
        self.assertEqual(p.returncode, EXIT_HTTP, p.stdout + p.stderr)

    def test_repo_dropin_declares_native_claude_path_first(self):
        """repo 內的 drop-in 是真相來源：原生安裝的 ~/.local/bin 必須排在 nvm 之前。"""
        dropin = SYSTEMD_DIR / "report-mark-web.service.d" / "path.conf"
        vals = _directives(dropin, "Environment")
        path = next(v for v in vals if v.startswith("PATH="))[len("PATH="):]
        self.assertTrue(path.split(":")[0].endswith("/.local/bin"), path)


class StorageCheckTests(unittest.TestCase):
    """L3：/healthz 綠不代表原檔拿得到。

    OBJECT_STORAGE_MODE=r2 時缺 key 即 503 不回退；bucket 或憑證出問題時原檔與 PDF 全壞，
    而 /healthz 只探 DB。探測由 web 行程做，探針只讀 `/healthz/storage` 的狀態碼，
    且**只認 503**——其他一律當作判不出來，不開事件。
    """

    def test_storage_503_is_exit_6(self):
        with _FakeSystemd(state="inactive") as sd, _Server(200, storage_status=503) as s:
            p = run_probe({"HEALTH_URL": s.url, **sd.env})
            calls = sd.calls()
        self.assertEqual(p.returncode, EXIT_STORAGE, p.stdout + p.stderr)
        f = parse(p.stdout)
        self.assertEqual((f["status"], f["http_code"], f["reason"]), ("degraded", "200", "storage_unreachable"))
        self.assertEqual(calls, [], "儲存檢查不得呼叫 systemctl")

    def test_anything_but_503_is_not_an_incident(self):
        """404＝web 還是沒有這支端點的舊版本；200＝ok／disabled／unknown。都不是故障。"""
        for status in (200, 404, 500):
            with _Server(200, storage_status=status) as s:
                p = run_probe({"HEALTH_URL": s.url})
            self.assertEqual(p.returncode, EXIT_OK, f"storage={status}: {p.stdout}{p.stderr}")

    def test_storage_url_is_derived_from_health_url(self):
        with _Server(200, storage_status=200) as s:
            run_probe({"HEALTH_URL": s.url})
            self.assertEqual(s.paths, ["/healthz", "/healthz/storage", "/healthz/llm"])

    def test_empty_storage_url_disables_the_check(self):
        with _Server(200, storage_status=503) as s:
            p = run_probe({"HEALTH_URL": s.url, "HEALTH_STORAGE_URL": ""})
            self.assertEqual(s.paths, ["/healthz", "/healthz/llm"])
        self.assertEqual(p.returncode, EXIT_OK, p.stdout + p.stderr)

    def test_db_failure_takes_precedence_and_storage_is_not_asked(self):
        with _FakeSystemd(state="active") as sd, _Server(503, storage_status=503) as s:
            p = run_probe({"HEALTH_URL": s.url, **sd.env})
            self.assertNotIn("/healthz/storage", s.paths)
        self.assertEqual(p.returncode, EXIT_HTTP, p.stdout + p.stderr)

    def test_missing_claude_is_reported_before_storage(self):
        """兩個都壞時先報問答（影響面較大）；一次只開一個事件，修好一個下一輪就輪到另一個。"""
        with tempfile.TemporaryDirectory() as tmp, _Server(200, storage_status=503) as s:
            t = Path(tmp)
            (t / "bin").mkdir()
            dropin = t / "path.conf"
            dropin.write_text(f"[Service]\nEnvironment=PATH={t}/bin\n", encoding="utf-8")
            p = run_probe({"HEALTH_URL": s.url, "HEALTH_DEP_DROPIN": str(dropin), "HEALTH_DEP_BIN": "claude-x9"})
        self.assertEqual(p.returncode, EXIT_DEGRADED, p.stdout + p.stderr)


class LlmCheckTests(unittest.TestCase):
    """L3：/healthz 綠不代表 LLM 帳號能用（402、401、連不上、餘額低於門檻）。

    餘額查詢由 web 行程做，探針只讀 `/healthz/llm` 的狀態碼，**只認 503**；503 本體的 state
    併進 reason（封閉詞彙 `llm_<小寫與底線>`，其餘 `llm_unavailable`）。多項 L3 同時成立時
    一行帶出全部 reason，退出碼取 5 → 6 → 7 最前面的（審查 M15：7 不得遮蔽 5、6）。
    """

    def test_llm_503_is_exit_7_with_state_in_reason(self):
        for state in ("exhausted", "low", "auth_failed", "unreachable", "indeterminate"):
            with self.subTest(state=state), _FakeSystemd(state="inactive") as sd, \
                    _Server(200, llm_status=503, llm_body=b'{"llm":"%s"}' % state.encode()) as s:
                p = run_probe({"HEALTH_URL": s.url, **sd.env})
                calls = sd.calls()
            self.assertEqual(p.returncode, EXIT_LLM, p.stdout + p.stderr)
            f = parse(p.stdout)
            self.assertEqual((f["status"], f["http_code"], f["reason"]), ("degraded", "200", f"llm_{state}"))
            self.assertEqual(calls, [], "LLM 檢查不得呼叫 systemctl")
            self.assertIn("LLM 帳號", p.stderr)

    def test_unparseable_or_hostile_body_is_llm_unavailable(self):
        for body in (b"", b"<html>oops</html>", b'{"llm":"ok; rm -rf /"}', b'{"llm":"EXHAUSTED"}',
                     b'{"llm":"' + b"a" * 40 + b'"}', b'{"llm":"low reason=forged"}'):
            with self.subTest(body=body), _Server(200, llm_status=503, llm_body=body) as s:
                p = run_probe({"HEALTH_URL": s.url})
            self.assertEqual(p.returncode, EXIT_LLM, p.stdout + p.stderr)
            self.assertEqual(parse(p.stdout)["reason"], "llm_unavailable")
            self.assertEqual(len(p.stdout.strip().splitlines()), 1)

    def test_anything_but_503_is_not_an_incident(self):
        """200＝ok／disabled／unknown／*_unused；404＝web 還是沒有這支端點的舊版本；500 判不出來。"""
        for status in (200, 404, 500):
            with _Server(200, llm_status=status, llm_body=b'{"llm":"exhausted_unused"}') as s:
                p = run_probe({"HEALTH_URL": s.url})
            self.assertEqual(p.returncode, EXIT_OK, f"llm={status}: {p.stdout}{p.stderr}")

    def test_unreachable_llm_endpoint_is_not_an_incident(self):
        with _Server(200) as s:
            p = run_probe({"HEALTH_URL": s.url, "HEALTH_LLM_URL": "http://127.0.0.1:59993/healthz/llm"})
        self.assertEqual(p.returncode, EXIT_OK, p.stdout + p.stderr)

    def test_empty_llm_url_disables_the_check(self):
        with _Server(200, llm_status=503) as s:
            p = run_probe({"HEALTH_URL": s.url, "HEALTH_LLM_URL": ""})
            self.assertEqual(s.paths, ["/healthz", "/healthz/storage"])
        self.assertEqual(p.returncode, EXIT_OK, p.stdout + p.stderr)

    def test_db_failure_takes_precedence_and_llm_is_not_asked(self):
        with _FakeSystemd(state="active") as sd, _Server(503, llm_status=503) as s:
            p = run_probe({"HEALTH_URL": s.url, **sd.env})
            self.assertNotIn("/healthz/llm", s.paths)
        self.assertEqual(p.returncode, EXIT_HTTP, p.stdout + p.stderr)

    def test_storage_beats_llm_and_both_reasons_are_reported(self):
        with _Server(200, storage_status=503, llm_status=503, llm_body=b'{"llm":"low"}') as s:
            p = run_probe({"HEALTH_URL": s.url})
            self.assertEqual(s.paths, ["/healthz", "/healthz/storage", "/healthz/llm"])
        self.assertEqual(p.returncode, EXIT_STORAGE, p.stdout + p.stderr)
        self.assertEqual(parse(p.stdout)["reason"], "storage_unreachable,llm_low")
        self.assertIn("物件儲存", p.stderr)
        self.assertIn("LLM 帳號", p.stderr)

    def _missing_claude(self, tmp):
        t = Path(tmp)
        (t / "bin").mkdir()
        dropin = t / "path.conf"
        dropin.write_text(f"[Service]\nEnvironment=PATH={t}/bin\n", encoding="utf-8")
        return {"HEALTH_DEP_DROPIN": str(dropin), "HEALTH_DEP_BIN": "claude-x9"}

    def test_dependency_beats_everything_and_all_three_are_reported(self):
        with tempfile.TemporaryDirectory() as tmp, \
                _Server(200, storage_status=503, llm_status=503, llm_body=b'{"llm":"exhausted"}') as s:
            p = run_probe({"HEALTH_URL": s.url, **self._missing_claude(tmp)})
            self.assertEqual(s.paths, ["/healthz", "/healthz/storage", "/healthz/llm"], "5 成立時仍要查 6、7")
        self.assertEqual(p.returncode, EXIT_DEGRADED, p.stdout + p.stderr)
        self.assertEqual(parse(p.stdout)["reason"], "dep_missing_claude-x9,storage_unreachable,llm_exhausted")
        self.assertEqual(len(p.stdout.strip().splitlines()), 1, "一行帶出全部 reason")

    def test_dependency_beats_llm(self):
        with tempfile.TemporaryDirectory() as tmp, _Server(200, llm_status=503) as s:
            p = run_probe({"HEALTH_URL": s.url, **self._missing_claude(tmp)})
        self.assertEqual(p.returncode, EXIT_DEGRADED, p.stdout + p.stderr)
        self.assertEqual(parse(p.stdout)["reason"], "dep_missing_claude-x9,llm_exhausted")

    def test_dependency_and_storage_report_both(self):
        with tempfile.TemporaryDirectory() as tmp, _Server(200, storage_status=503) as s:
            p = run_probe({"HEALTH_URL": s.url, **self._missing_claude(tmp)})
        self.assertEqual(p.returncode, EXIT_DEGRADED, p.stdout + p.stderr)
        self.assertEqual(parse(p.stdout)["reason"], "dep_missing_claude-x9,storage_unreachable")

    def test_worst_case_duration_fits_the_unit_timeout(self):
        """三次探測 × 逾時 ＋ 兩次等待 ＋ L3 兩支各一次逾時，必須小於 unit 的 TimeoutStartSec。"""
        timeout = _directives(SERVICE, "TimeoutStartSec")
        text = PROBE.read_text(encoding="utf-8")

        def default(name):
            m = re.search(rf'^{name}="\$\{{{name}:-(\d+)\}}"', text, re.M)
            self.assertIsNotNone(m, name)
            return int(m.group(1))

        worst = (default("HEALTH_RETRIES") * default("HEALTH_TIMEOUT")
                 + (default("HEALTH_RETRIES") - 1) * default("HEALTH_RETRY_WAIT")
                 + 2 * default("HEALTH_TIMEOUT"))
        self.assertEqual(worst, 55)
        self.assertLess(worst, int(timeout[0]))


class HostSystemdIsolationTests(unittest.TestCase):
    """釘死「HTTP 退出碼不得取決於執行主機的 systemd 狀態」這條不變量。

    2026-08-19：#209 的 CI 首跑讓 4 個 `ProbeBehaviourTests` 同時紅在
    `AssertionError: 2 != 1`。腳本沒有壞——壞的是測試只控制 HTTP 一維，
    另一維（`systemctl is-active`）默默沿用執行主機的真實狀態。開發機上有
    active 的 `report-mark-web.service` 所以是 1，CI 上沒有所以是 2。
    這個類別的每一條都必須在「有 web unit」與「沒有 web unit」的機器上得到同一個答案。
    """

    # 刻意用一個保證不存在的 unit 名：如果 PATH 注入失效、fake 被繞過，
    # 真的 systemctl 會回報它不存在 → 探針走 EXIT_PROCESS(2) → 測試紅。
    # 也就是說這條測試會**證明**答案來自 fake，而不是碰巧與宿主一致。
    ABSENT_UNIT = "definitely-not-a-real-unit-9f3c1a.service"
    DEAD_URL = "http://127.0.0.1:59994/healthz"

    def test_http_failure_is_independent_of_host_systemd_state(self):
        with _FakeSystemd(state="active") as sd:
            p = run_probe({"HEALTH_URL": self.DEAD_URL, "HEALTH_UNIT": self.ABSENT_UNIT, **sd.env})
            calls = sd.calls()
        self.assertEqual(p.returncode, EXIT_HTTP, p.stdout + p.stderr)
        self.assertEqual(parse(p.stdout)["status"], "fail")
        # fake 真的被呼叫過——否則上面的斷言可能只是碰巧與宿主狀態一致
        self.assertTrue(
            any(c.startswith("is-active") for c in calls),
            f"探針沒有呼叫注入的 systemctl，PATH 注入可能失效: {calls}",
        )
        self.assertTrue(all(self.ABSENT_UNIT in c for c in calls if "is-active" in c))

    def test_inactive_unit_turns_the_same_http_failure_into_process_failure(self):
        """同一組 HTTP 條件，只翻轉 systemd 那一維，退出碼必須跟著翻轉。

        這正是 CI 與開發機的差異所在；把它寫成測試之後，那個差異不再是環境問題。
        """
        with _FakeSystemd(state="inactive") as sd:
            p = run_probe({"HEALTH_URL": self.DEAD_URL, "HEALTH_UNIT": self.ABSENT_UNIT, **sd.env})
        self.assertEqual(p.returncode, EXIT_PROCESS, p.stdout + p.stderr)
        self.assertEqual(parse(p.stdout)["reason"], "unit_inactive")

    def test_systemctl_itself_failing_is_not_a_tooling_exit(self):
        """systemctl 不可用時退回 unit_unknown，**不是** EXIT_TOOLING。

        EXIT_TOOLING 專屬於「缺 curl」＝探針自己不能探測；systemctl 探不到只是
        L1 判不出來，此時寧可誤報 L1 也不要因為分不清而放行。
        """
        with _FakeSystemd(broken=True) as sd:
            p = run_probe({"HEALTH_URL": self.DEAD_URL, "HEALTH_UNIT": self.ABSENT_UNIT, **sd.env})
        self.assertEqual(p.returncode, EXIT_PROCESS, p.stdout + p.stderr)
        self.assertEqual(parse(p.stdout)["reason"], "unit_unknown")

    def test_healthy_path_never_consults_systemd(self):
        """200 就直接收工——健康時去查 systemd 只會讓探針多一個壞掉的理由。"""
        with _FakeSystemd(state="inactive") as sd, _Server(200) as s:
            p = run_probe({"HEALTH_URL": s.url, "HEALTH_UNIT": self.ABSENT_UNIT, **sd.env})
            calls = sd.calls()
        # fake 說 unit 不在 active，若成功路徑會查 systemd，這裡就不會是 EXIT_OK
        self.assertEqual(p.returncode, EXIT_OK, p.stdout + p.stderr)
        self.assertEqual(calls, [], f"成功路徑不應呼叫 systemctl，實際呼叫: {calls}")


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

    def test_fresh_start_within_window_is_grace(self):
        """人為 restart 後的空窗：active ＋ NRestarts=0 ＋ 剛進 active → 不算故障。"""
        with _FakeSystemd(state="active", restarts=0, entered_age=1) as sd:
            p = run_probe({"HEALTH_URL": "http://127.0.0.1:59995/healthz", **sd.env})
        self.assertEqual(p.returncode, EXIT_GRACE, p.stdout + p.stderr)
        self.assertEqual(parse(p.stdout)["status"], "grace")

    def test_crash_loop_is_not_grace(self):
        """**本設計最容易寫錯的一條，之前只有靜態比對守著。**

        `Restart=always` ＋ `RestartSec=3` 會讓 ActiveEnterTimestamp 每 3 秒更新，
        只看時間戳的話寬限恆成立、告警被永久抑制——完整重現 2026-08-18 那次
        累積 550 次重啟卻沒有人知道的事故。NRestarts 非零就必須離開寬限。
        """
        with _FakeSystemd(state="active", restarts=550, entered_age=1) as sd:
            p = run_probe({"HEALTH_URL": "http://127.0.0.1:59996/healthz", **sd.env})
        self.assertNotEqual(p.returncode, EXIT_GRACE, p.stdout + p.stderr)
        self.assertEqual(p.returncode, EXIT_HTTP)

    def test_missing_timestamp_is_not_grace(self):
        """systemd 給不出 ActiveEnterTimestamp 時不得假設在寬限期內。"""
        with _FakeSystemd(state="active", restarts=0, entered_age=None) as sd:
            p = run_probe({"HEALTH_URL": "http://127.0.0.1:59997/healthz", **sd.env})
        self.assertNotEqual(p.returncode, EXIT_GRACE, p.stdout + p.stderr)

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

    def test_service_declares_no_notification_capable_onfailure(self):
        """**P4 不得接上任何能送通知的失敗鏈。**

        `report-mark-alert.sh` 的 webhook 只由單一全域變數
        `REPORT_MARK_ALERT_WEBHOOK` 控制，而 `report-mark-alert@.service` 讀
        `/etc/default/report-mark-sync`。只要有人為了其他 unit 設定它，本探針
        每 2 分鐘的失敗就會變成每 2 分鐘一則通知——P4 於是從「健康偵測」暗中
        變成「健康偵測 ＋ 不受控通知」，而通知去重需要跨執行狀態（屬 P5）。

        這條測試把邊界交給架構而不是設定紀律：不接告警鏈，P4 結構上就不可能通知。
        """
        self.assertEqual(
            _directives(SERVICE, "OnFailure"),
            [],
            "P4 不得宣告 OnFailure；通知（含去重與節奏）屬於 P5",
        )

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

    def test_failure_stays_observable_without_onfailure(self):
        """移除 OnFailure 的前提：失敗必須仍能被 P5 可靠觀察到。

        對 `Type=oneshot` 而言，非零退出會讓 unit 停在 `failed` 並保留
        `Result` / `ExecMainStatus` / `ExecMainExitTimestamp`，加上 journal 內
        本次執行的 key=value 單行結果——這三者足以讓 P5 判定「這一輪失敗了」。
        因此本 unit 必須是 oneshot，且**不得**宣告會把失敗吞掉的設定。
        """
        self.assertEqual(_directives(SERVICE, "Type"), ["oneshot"])
        # SuccessExitStatus 只准放行寬限碼 3；放行 1/2/4 會讓真失敗變成 success
        self.assertEqual(_directives(SERVICE, "SuccessExitStatus"), ["3"])
        for forbidden in ("Restart", "RestartSec"):
            with self.subTest(directive=forbidden):
                self.assertEqual(
                    _directives(SERVICE, forbidden), [],
                    f"{forbidden} 會讓失敗的探針自動重試，遮蔽 failed 狀態",
                )

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

    def test_unit_cannot_reach_the_webhook_alert_chain(self):
        """unit 層的禁令：不得以任何形式接上 report-mark-alert（它會讀 webhook 變數）。"""
        for unit in (SERVICE, TIMER):
            body = "\n".join(
                ln for ln in unit.read_text(encoding="utf-8").splitlines()
                if not ln.strip().startswith("#")
            )
            with self.subTest(unit=unit.name):
                self.assertNotIn("report-mark-alert", body)
                self.assertNotIn("WEBHOOK", body)

    def test_probe_does_not_write_any_file(self):
        body = "\n".join(
            ln for ln in PROBE.read_text(encoding="utf-8").splitlines() if not ln.strip().startswith("#")
        )
        self.assertNotIn(">>", body, "P4 是無狀態觀測，不得追加任何檔案")


if __name__ == "__main__":
    unittest.main()
