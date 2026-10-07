"""安全事件探針（`scripts/check_security_health.sh`）的退出碼契約與 systemd unit 守門。

退出碼是 P5（`scripts/incident_handler.sh`，不改它）分級的依據：1／2 → CRITICAL、3 → 本組設為 hold（判不出來）、
4 → WARNING。不對真實站台發請求：`/healthz/security` 用本機假伺服器代替。
"""
from __future__ import annotations

import os
import subprocess
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread

REPO_ROOT = Path(__file__).resolve().parents[1]
PROBE = REPO_ROOT / "scripts" / "check_security_health.sh"
HANDLER = REPO_ROOT / "scripts" / "incident_handler.sh"
SYSTEMD_DIR = REPO_ROOT / "deploy" / "systemd"
SERVICE = SYSTEMD_DIR / "report-mark-security-health.service"
TIMER = SYSTEMD_DIR / "report-mark-security-health.timer"
INCIDENT_SERVICE = SYSTEMD_DIR / "report-mark-security-incident.service"
INCIDENT_TIMER = SYSTEMD_DIR / "report-mark-security-incident.timer"

EXIT_OK, EXIT_TAMPER, EXIT_ALERT, EXIT_UNKNOWN, EXIT_TOOLING = 0, 1, 2, 3, 4
REQUIRED_FIELDS = ("ts", "component", "probe", "status", "http_code", "latency_ms", "attempts", "reason")


def _directives(path: Path, key: str) -> list[str]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or "=" not in stripped:
            continue
        name, _, value = stripped.partition("=")
        if name.strip() == key:
            out.append(value.strip())
    return out


class _Server:
    def __init__(self, status: int, body: bytes):
        self.hits = 0
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                outer.hits += 1
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.srv = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_port}/healthz/security"

    def __enter__(self):
        Thread(target=self.srv.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *a):
        self.srv.shutdown()
        self.srv.server_close()


def _closed_url() -> str:
    srv = HTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
    port = srv.server_port
    srv.server_close()
    return f"http://127.0.0.1:{port}/healthz/security"


def run_probe(url: str, **extra) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not k.startswith("SECURITY_HEALTH_")}
    env.update({"SECURITY_HEALTH_URL": url, "SECURITY_HEALTH_RETRIES": "2", "SECURITY_HEALTH_RETRY_WAIT": "0",
                "SECURITY_HEALTH_TIMEOUT": "3", **extra})
    return subprocess.run(["bash", str(PROBE)], capture_output=True, text=True, env=env, timeout=60)


def parse(stdout: str) -> dict:
    line = stdout.strip().splitlines()[-1]
    return dict(kv.split("=", 1) for kv in line.split() if "=" in kv)


class ProbeExitCodeTests(unittest.TestCase):
    def _case(self, status: int, body: bytes):
        with _Server(status, body) as srv:
            p = run_probe(srv.url)
        return p, parse(p.stdout), srv

    def test_ok(self):
        p, out, _ = self._case(200, b'{"security":"ok"}')
        self.assertEqual(p.returncode, EXIT_OK, p.stderr)
        self.assertEqual((out["status"], out["reason"], out["component"]), ("ok", "healthy", "security"))

    def test_audit_chain_broken_is_tamper(self):
        p, out, _ = self._case(503, b'{"security":"audit_chain_broken"}')
        self.assertEqual(p.returncode, EXIT_TAMPER)
        self.assertEqual(out["reason"], "security_audit_chain_broken")

    def test_threshold_alerts(self):
        for state in ("login_failures", "account_failures", "elevate_failures"):
            with self.subTest(state=state):
                p, out, _ = self._case(503, f'{{"security": "{state}"}}'.encode())
                self.assertEqual(p.returncode, EXIT_ALERT)
                self.assertEqual((out["status"], out["reason"]), ("alert", f"security_{state}"))

    def test_unreadable_503_is_still_an_alert(self):
        p, out, _ = self._case(503, b"<html>oops</html>")
        self.assertEqual((p.returncode, out["reason"]), (EXIT_ALERT, "security_unavailable"))

    def test_unknown_and_unreadable_200_are_hold(self):
        p, out, _ = self._case(200, b'{"security":"unknown"}')
        self.assertEqual((p.returncode, out["reason"]), (EXIT_UNKNOWN, "security_unknown"))
        p, out, _ = self._case(200, b"{}")
        self.assertEqual((p.returncode, out["reason"]), (EXIT_UNKNOWN, "security_unreadable"))

    def test_endpoint_missing_or_odd_status_is_hold(self):
        p, out, _ = self._case(404, b'{"detail":"Not Found"}')
        self.assertEqual((p.returncode, out["reason"]), (EXIT_UNKNOWN, "endpoint_not_found"))
        p, out, _ = self._case(500, b"")
        self.assertEqual((p.returncode, out["reason"]), (EXIT_UNKNOWN, "unexpected_http_500"))

    def test_web_unreachable_is_hold_after_retries(self):
        p = run_probe(_closed_url())
        out = parse(p.stdout)
        self.assertEqual(p.returncode, EXIT_UNKNOWN)
        self.assertRegex(out["reason"], r"^web_unreachable_curl_rc_[0-9]+$")
        self.assertEqual(out["attempts"], "2")

    def test_http_response_is_not_retried(self):
        p, _, srv = self._case(503, b'{"security":"login_failures"}')
        self.assertEqual(srv.hits, 1)

    def test_missing_curl_is_tooling(self):
        p = subprocess.run(["/usr/bin/bash", str(PROBE)], capture_output=True, text=True, timeout=30,
                           env={"PATH": "/nonexistent", "SECURITY_HEALTH_URL": "http://127.0.0.1:1/x"})
        self.assertEqual(p.returncode, EXIT_TOOLING)
        self.assertEqual(parse(p.stdout)["reason"], "curl_not_found")

    def test_stdout_is_one_line_with_required_fields(self):
        p, _, _ = self._case(200, b'{"security":"ok"}')
        lines = p.stdout.strip().splitlines()
        self.assertEqual(len(lines), 1)
        out = parse(p.stdout)
        for field in REQUIRED_FIELDS:
            self.assertIn(field, out)

    def test_probe_does_not_depend_on_python_or_send_webhooks(self):
        code = "\n".join(line for line in PROBE.read_text(encoding="utf-8").splitlines()
                         if not line.lstrip().startswith("#"))
        for word in ("python", "uv run", ".venv", "WEBHOOK", "hooks.slack"):
            self.assertNotIn(word, code)

    def test_exit_codes_map_onto_handler_branches(self):
        """1／2 落在 handler 的 CRITICAL 分支、4 是 WARNING——不必改 incident_handler.sh。"""
        handler = HANDLER.read_text(encoding="utf-8")
        self.assertIn('1|2) run_state_machine "$COMPONENT" failing CRITICAL', handler)
        self.assertIn('4)   run_state_machine "$COMPONENT" failing WARNING', handler)


class SecurityUnitTests(unittest.TestCase):
    def test_probe_service_accepts_unknown_and_fits_in_timer_interval(self):
        self.assertEqual(_directives(SERVICE, "SuccessExitStatus"), ["3"])
        self.assertEqual(_directives(SERVICE, "OnFailure"), [])
        self.assertEqual(_directives(SERVICE, "Type"), ["oneshot"])
        self.assertIn("check_security_health.sh", _directives(SERVICE, "ExecStart")[0])
        timeout = int(_directives(SERVICE, "TimeoutStartSec")[0])
        self.assertGreater(timeout, 2 * 10 + 10)
        self.assertLess(timeout, 120)
        self.assertEqual(_directives(TIMER, "OnUnitActiveSec"), ["2min"])
        self.assertEqual(_directives(TIMER, "Persistent"), [])

    def test_probe_service_path_has_no_python_tooling(self):
        path = next(v for v in _directives(SERVICE, "Environment") if v.startswith("PATH="))
        self.assertNotIn("nvm", path)
        self.assertNotIn(".local/bin", path)

    def test_incident_unit_points_at_security_probe_and_holds_on_3(self):
        env = dict(v.split("=", 1) for v in _directives(INCIDENT_SERVICE, "Environment"))
        self.assertEqual(env["INCIDENT_PROBE_UNIT"], SERVICE.name)
        self.assertEqual(env["INCIDENT_TIMER_UNIT"], TIMER.name)
        self.assertEqual(env["INCIDENT_COMPONENT"], "security")
        self.assertEqual(env["INCIDENT_MONITOR_COMPONENT"], "security_monitor")
        self.assertEqual(env["INCIDENT_HOLD_EXIT_CODES"], "3")
        self.assertTrue(env["INCIDENT_STATE_DIR"].endswith("/data/.incidents-security"))
        self.assertIn("incident_handler.sh", _directives(INCIDENT_SERVICE, "ExecStart")[0])
        self.assertEqual(_directives(INCIDENT_SERVICE, "OnFailure"), [])
        self.assertIn("-/etc/report-mark/alert.env", _directives(INCIDENT_SERVICE, "EnvironmentFile"))
        self.assertEqual(_directives(INCIDENT_TIMER, "OnUnitActiveSec"), ["2min"])


if __name__ == "__main__":
    unittest.main()
