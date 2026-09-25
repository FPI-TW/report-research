# tests/test_edge_health_probe.py
"""對外邊緣健康探針（`scripts/check_edge_health.sh`）的行為與契約守門。

為什麼要有這一支：2026-09-24 Docker Desktop 重啟後 deploy-nginx-1 沒有起來，外網 502 約
36 小時，而本機探針（`check_web_health.sh`）全程健康——**本機 /healthz 綠不代表使用者
連得到**。這支探針的價值全在失敗歸因：邊緣壞（CRITICAL）、origin 壞（交給 web 元件）、
本機連不出去（判不出來）三者必須分得開，分錯就是重複通知或靜默。

不對真實站台發請求：對外網址與 origin 都用本機假伺服器代替。
"""
import os
import re
import subprocess
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread

REPO_ROOT = Path(__file__).resolve().parents[1]
PROBE = REPO_ROOT / "scripts" / "check_edge_health.sh"
SYSTEMD_DIR = REPO_ROOT / "deploy" / "systemd"
SERVICE = SYSTEMD_DIR / "report-mark-edge-health.service"
TIMER = SYSTEMD_DIR / "report-mark-edge-health.timer"
INCIDENT_SERVICE = SYSTEMD_DIR / "report-mark-edge-incident.service"

EXIT_OK, EXIT_DOWN, EXIT_ORIGIN, EXIT_TOOLING = 0, 1, 3, 4
REQUIRED_FIELDS = ("ts", "component", "probe", "status", "http_code", "latency_ms", "attempts", "reason")


def _directives(path: Path, key: str) -> list[str]:
    out: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or "=" not in stripped:
            continue
        name, _, value = stripped.partition("=")
        if name.strip() == key:
            out.append(value.strip())
    return out


class _Server:
    """回固定狀態碼的本機 HTTP 伺服器；用 127.0.0.1 避開 ::1 解析。"""

    def __init__(self, status: int):
        self.status = status
        self.hits = 0
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                outer.hits += 1
                self.send_response(outer.status)
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, *a):
                pass

        self.srv = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_port}/healthz"

    def __enter__(self):
        Thread(target=self.srv.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *a):
        self.srv.shutdown()
        self.srv.server_close()


def _closed_url() -> str:
    """拿一個確定沒有 listener 的埠：綁起來取號再關掉。"""
    srv = HTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
    port = srv.server_port
    srv.server_close()
    return f"http://127.0.0.1:{port}/healthz"


def run_probe(env_extra: dict) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not k.startswith("EDGE_")}
    env.update({
        "EDGE_HEALTH_RETRIES": "1",
        "EDGE_HEALTH_RETRY_WAIT": "1",
        "EDGE_HEALTH_TIMEOUT": "2",
        "EDGE_ORIGIN_TIMEOUT": "2",
    })
    env.update(env_extra)
    return subprocess.run(["bash", str(PROBE)], capture_output=True, text=True, env=env, timeout=60)


def parse(stdout: str) -> dict:
    line = stdout.strip().splitlines()[-1]
    return dict(kv.split("=", 1) for kv in line.split() if "=" in kv)


class EdgeProbeBehaviourTests(unittest.TestCase):
    def test_external_200_is_ok_and_skips_origin(self) -> None:
        with _Server(200) as edge, _Server(200) as origin:
            p = run_probe({"EDGE_HEALTH_URL": edge.url, "EDGE_ORIGIN_URL": origin.url})
        self.assertEqual(p.returncode, EXIT_OK, p.stderr)
        out = parse(p.stdout)
        self.assertEqual((out["status"], out["reason"]), ("ok", "ok"))
        self.assertEqual(origin.hits, 0, "對外正常時不該多打 origin")

    def test_edge_502_with_healthy_origin_is_down(self) -> None:
        """2026-09-24 的形狀：nginx 不在，Cloudflare 回 502，web 本身健康。"""
        with _Server(502) as edge, _Server(200) as origin:
            p = run_probe({"EDGE_HEALTH_URL": edge.url, "EDGE_ORIGIN_URL": origin.url})
        self.assertEqual(p.returncode, EXIT_DOWN, p.stderr)
        out = parse(p.stdout)
        self.assertEqual((out["status"], out["reason"], out["http_code"]), ("down", "edge_http_502", "502"))
        self.assertIn("edge-reload", p.stderr)

    def test_tunnel_530_is_down(self) -> None:
        with _Server(530) as edge, _Server(200) as origin:
            p = run_probe({"EDGE_HEALTH_URL": edge.url, "EDGE_ORIGIN_URL": origin.url})
        self.assertEqual(p.returncode, EXIT_DOWN)
        self.assertEqual(parse(p.stdout)["reason"], "edge_http_530")

    def test_origin_unhealthy_defers_to_web_component(self) -> None:
        """web 自己壞時對外也一定失敗；這裡不得再開一個 CRITICAL 重複通知。"""
        with _Server(502) as edge, _Server(503) as origin:
            p = run_probe({"EDGE_HEALTH_URL": edge.url, "EDGE_ORIGIN_URL": origin.url})
        self.assertEqual(p.returncode, EXIT_ORIGIN)
        out = parse(p.stdout)
        self.assertEqual((out["status"], out["reason"]), ("origin_down", "origin_http_503"))

    def test_origin_unreachable_also_defers(self) -> None:
        with _Server(502) as edge:
            p = run_probe({"EDGE_HEALTH_URL": edge.url, "EDGE_ORIGIN_URL": _closed_url()})
        self.assertEqual(p.returncode, EXIT_ORIGIN)
        self.assertEqual(parse(p.stdout)["reason"], "origin_http_000")

    def test_no_http_response_is_indeterminate_not_down(self) -> None:
        """連不出去（DNS、逾時）可能是本機網路的事，歸「判不出來」而不是 CRITICAL。"""
        with _Server(200) as origin:
            p = run_probe({"EDGE_HEALTH_URL": _closed_url(), "EDGE_ORIGIN_URL": origin.url})
        self.assertEqual(p.returncode, EXIT_TOOLING)
        out = parse(p.stdout)
        self.assertEqual(out["status"], "tooling")
        self.assertRegex(out["reason"], r"^edge_curl_rc_[1-9][0-9]*$")

    def test_unset_url_is_tooling_not_silent_ok(self) -> None:
        p = run_probe({})
        self.assertEqual(p.returncode, EXIT_TOOLING)
        self.assertEqual(parse(p.stdout)["reason"], "edge_url_unset")

    def test_stdout_is_one_line_with_required_fields(self) -> None:
        with _Server(502) as edge, _Server(200) as origin:
            p = run_probe({"EDGE_HEALTH_URL": edge.url, "EDGE_ORIGIN_URL": origin.url})
        lines = p.stdout.strip().splitlines()
        self.assertEqual(len(lines), 1)
        out = parse(p.stdout)
        for field in REQUIRED_FIELDS:
            self.assertIn(field, out)
        self.assertEqual((out["component"], out["probe"]), ("edge", "external_http"))

    def test_retries_within_one_run(self) -> None:
        with _Server(502) as edge, _Server(200) as origin:
            p = run_probe({"EDGE_HEALTH_URL": edge.url, "EDGE_ORIGIN_URL": origin.url,
                           "EDGE_HEALTH_RETRIES": "2"})
        self.assertEqual(edge.hits, 2)
        self.assertEqual(parse(p.stdout)["attempts"], "2")


class EdgeProbeStaticTests(unittest.TestCase):
    def test_probe_does_not_depend_on_python(self) -> None:
        code = "\n".join(
            line for line in PROBE.read_text(encoding="utf-8").splitlines() if not line.lstrip().startswith("#")
        )
        for word in ("python", "uv run", ".venv"):
            self.assertNotIn(word, code)

    def test_domain_is_not_hardcoded(self) -> None:
        """對外網域只放在 /etc/default/report-mark-sync，不進 repo。"""
        self.assertIsNone(re.search(r"https://[a-z0-9.-]+\.[a-z]{2,}", PROBE.read_text(encoding="utf-8")))

    def test_service_accepts_origin_exit_and_fits_in_timer_interval(self) -> None:
        self.assertEqual(_directives(SERVICE, "SuccessExitStatus"), ["3"])
        self.assertEqual(_directives(SERVICE, "OnFailure"), [])
        timeout = int(_directives(SERVICE, "TimeoutStartSec")[0])
        # 3 × 10 秒逾時 ＋ 2 × 15 秒等待 ＋ origin 5 秒
        self.assertGreater(timeout, 3 * 10 + 2 * 15 + 5)
        interval = _directives(TIMER, "OnUnitActiveSec")[0]
        self.assertEqual(interval, "2min")
        self.assertLess(timeout, 120)

    def test_incident_unit_points_at_edge_probe_with_own_state_dir(self) -> None:
        env = dict(v.split("=", 1) for v in _directives(INCIDENT_SERVICE, "Environment"))
        self.assertEqual(env["INCIDENT_PROBE_UNIT"], SERVICE.name)
        self.assertEqual(env["INCIDENT_TIMER_UNIT"], TIMER.name)
        self.assertEqual(env["INCIDENT_COMPONENT"], "edge")
        others = set()
        for unit in SYSTEMD_DIR.glob("*incident.service"):
            if unit == INCIDENT_SERVICE:
                continue
            for v in _directives(unit, "Environment"):
                if v.startswith("INCIDENT_STATE_DIR="):
                    others.add(v.split("=", 1)[1])
        self.assertNotIn(env["INCIDENT_STATE_DIR"], others)
        self.assertNotEqual(env["INCIDENT_STATE_DIR"].rstrip("/").rsplit("/", 1)[-1], ".incidents")

    def test_incident_unit_holds_on_origin_exit(self) -> None:
        """exit 3＝origin 也壞了、交給 web 元件：edge 這組必須 hold，不得當健康關事件。

        handler 預設把 3 當健康（web 探針的 3 是啟動寬限）；少了這一行，進行中的 edge 事件會在
        web 重啟那一輪被 RESOLVED 關掉並刪掉狀態檔，送出不實的「已恢復」。
        """
        env = dict(v.split("=", 1) for v in _directives(INCIDENT_SERVICE, "Environment"))
        codes = env.get("INCIDENT_HOLD_EXIT_CODES", "").replace(",", " ").split()
        self.assertIn(str(EXIT_ORIGIN), codes)
        # 反向：web 與 LineBot 兩組不得設（它們的 3 仍是健康）
        for unit in SYSTEMD_DIR.glob("*incident.service"):
            if unit == INCIDENT_SERVICE:
                continue
            with self.subTest(unit=unit.name):
                self.assertEqual(
                    [v for v in _directives(unit, "Environment") if v.startswith("INCIDENT_HOLD_EXIT_CODES=")], []
                )

    def test_incident_unit_declares_no_onfailure(self) -> None:
        """本 unit 自己就是告警器，失敗再觸發告警會形成遞迴。"""
        self.assertEqual(_directives(INCIDENT_SERVICE, "OnFailure"), [])

    def test_incident_components_are_unique_across_instances(self) -> None:
        """每個 incident 實例的 COMPONENT／MONITOR_COMPONENT 不得重名。

        元件名是 webhook payload 的 `component` 與狀態檔名；重名會讓兩組的告警在 Slack 上
        分不出來。沒設的實例用 handler 的預設值（web／monitor）。
        """
        seen: dict[str, str] = {}
        units = sorted(SYSTEMD_DIR.glob("*incident.service"))
        self.assertGreaterEqual(len(units), 3)
        for unit in units:
            env = dict(v.split("=", 1) for v in _directives(unit, "Environment") if "=" in v)
            for key, default in (("INCIDENT_COMPONENT", "web"), ("INCIDENT_MONITOR_COMPONENT", "monitor")):
                name = env.get(key, default)
                self.assertNotIn(name, seen, f"{unit.name} 的 {key}={name} 與 {seen.get(name)} 重名")
                seen[name] = f"{unit.name} {key}"


if __name__ == "__main__":
    unittest.main()
