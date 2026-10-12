"""新檔來源探針（`scripts/check_feed_health.sh`）的退出碼契約，以及信標發布、探針、事件三組 systemd unit 的守門。

退出碼是 P5（`scripts/incident_handler.sh`，不改它）分級的依據：1／2 → CRITICAL、3 → 本組設為 hold（判不出來）、
4 → WARNING。不對真實站台發請求：`/healthz/feed` 用本機假伺服器代替。
"""
from __future__ import annotations

import os
import subprocess
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread

from app.services import host_beacon

REPO_ROOT = Path(__file__).resolve().parents[1]
PROBE = REPO_ROOT / "scripts" / "check_feed_health.sh"
HANDLER = REPO_ROOT / "scripts" / "incident_handler.sh"
SYSTEMD_DIR = REPO_ROOT / "deploy" / "systemd"
SERVICE = SYSTEMD_DIR / "report-mark-feed-health.service"
TIMER = SYSTEMD_DIR / "report-mark-feed-health.timer"
INCIDENT_SERVICE = SYSTEMD_DIR / "report-mark-feed-incident.service"
INCIDENT_TIMER = SYSTEMD_DIR / "report-mark-feed-incident.timer"
BEACON_SERVICE = SYSTEMD_DIR / "report-mark-beacon.service"
BEACON_TIMER = SYSTEMD_DIR / "report-mark-beacon.timer"

EXIT_OK, EXIT_SILENT, EXIT_BROKEN, EXIT_UNKNOWN, EXIT_TOOLING = 0, 1, 2, 3, 4
# 來源主機整台失聯（信標沒有在更新）與「有在回報但來源段壞了」分開：處置不同（前者去看那台主機，後者看 sync 日誌）。
SILENT_STATES = ("missing", "stale")
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
        self.url = f"http://127.0.0.1:{self.srv.server_port}/healthz/feed"

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
    return f"http://127.0.0.1:{port}/healthz/feed"


def run_probe(url: str, **extra) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not k.startswith("FEED_HEALTH_")}
    env.update({"FEED_HEALTH_URL": url, "FEED_HEALTH_RETRIES": "2", "FEED_HEALTH_RETRY_WAIT": "0",
                "FEED_HEALTH_TIMEOUT": "3", **extra})
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
        p, out, _ = self._case(200, b'{"feed":"ok"}')
        self.assertEqual(p.returncode, EXIT_OK, p.stderr)
        self.assertEqual((out["status"], out["reason"], out["component"]), ("ok", "healthy", "feed"))

    def test_every_alert_state_has_an_exit_code(self):
        """web 的每一個告警狀態探針都認得：新增狀態卻沒改探針，會落到「本體讀不懂」而失去 reason。"""
        for state in host_beacon.ALERT_STATES:
            with self.subTest(state=state):
                p, out, _ = self._case(503, f'{{"feed": "{state}"}}'.encode())
                self.assertEqual(p.returncode, EXIT_SILENT if state in SILENT_STATES else EXIT_BROKEN)
                self.assertEqual((out["status"], out["reason"]), ("alert", f"feed_{state}"))

    def test_unreadable_503_is_still_an_alert(self):
        p, out, _ = self._case(503, b"<html>oops</html>")
        self.assertEqual((p.returncode, out["reason"]), (EXIT_BROKEN, "feed_unavailable"))
        p, out, _ = self._case(503, b'{"feed":"some_future_state"}')
        self.assertEqual((p.returncode, out["reason"]), (EXIT_BROKEN, "feed_unavailable"))

    def test_disabled_is_a_warning_not_silently_healthy(self):
        """裝了探針卻沒設定要監看哪一台：不能每 2 分鐘回報一次沒事。"""
        p, out, _ = self._case(200, b'{"feed":"disabled"}')
        self.assertEqual((p.returncode, out["status"], out["reason"]), (EXIT_TOOLING, "tooling", "feed_disabled"))

    def test_unknown_and_unreadable_200_are_hold(self):
        p, out, _ = self._case(200, b'{"feed":"unknown"}')
        self.assertEqual((p.returncode, out["reason"]), (EXIT_UNKNOWN, "feed_unknown"))
        p, out, _ = self._case(200, b"{}")
        self.assertEqual((p.returncode, out["reason"]), (EXIT_UNKNOWN, "feed_unreadable"))

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
        _, _, srv = self._case(503, b'{"feed":"stale"}')
        self.assertEqual(srv.hits, 1)

    def test_missing_curl_is_tooling(self):
        p = subprocess.run(["/usr/bin/bash", str(PROBE)], capture_output=True, text=True, timeout=30,
                           env={"PATH": "/nonexistent", "FEED_HEALTH_URL": "http://127.0.0.1:1/x"})
        self.assertEqual(p.returncode, EXIT_TOOLING)
        self.assertEqual(parse(p.stdout)["reason"], "curl_not_found")

    def test_stdout_is_one_line_with_required_fields(self):
        p, _, _ = self._case(200, b'{"feed":"ok"}')
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


class FeedUnitTests(unittest.TestCase):
    def test_probe_service_accepts_unknown_and_fits_in_timer_interval(self):
        self.assertEqual(_directives(SERVICE, "SuccessExitStatus"), ["3"])
        self.assertEqual(_directives(SERVICE, "OnFailure"), [])
        self.assertEqual(_directives(SERVICE, "Type"), ["oneshot"])
        self.assertIn("check_feed_health.sh", _directives(SERVICE, "ExecStart")[0])
        timeout = int(_directives(SERVICE, "TimeoutStartSec")[0])
        self.assertGreater(timeout, 2 * 10 + 10)
        self.assertLess(timeout, 120)
        self.assertEqual(_directives(TIMER, "OnUnitActiveSec"), ["2min"])
        self.assertEqual(_directives(TIMER, "Persistent"), [])

    def test_probe_service_path_has_no_python_tooling(self):
        path = next(v for v in _directives(SERVICE, "Environment") if v.startswith("PATH="))
        self.assertNotIn("nvm", path)
        self.assertNotIn(".local/bin", path)

    def test_incident_unit_points_at_feed_probe_and_holds_on_3(self):
        env = dict(v.split("=", 1) for v in _directives(INCIDENT_SERVICE, "Environment"))
        self.assertEqual(env["INCIDENT_PROBE_UNIT"], SERVICE.name)
        self.assertEqual(env["INCIDENT_TIMER_UNIT"], TIMER.name)
        self.assertEqual(env["INCIDENT_COMPONENT"], "feed")
        self.assertEqual(env["INCIDENT_MONITOR_COMPONENT"], "feed_monitor")
        self.assertEqual(env["INCIDENT_HOLD_EXIT_CODES"], "3")
        self.assertTrue(env["INCIDENT_STATE_DIR"].endswith("/data/.incidents-feed"))
        self.assertIn("incident_handler.sh", _directives(INCIDENT_SERVICE, "ExecStart")[0])
        self.assertEqual(_directives(INCIDENT_SERVICE, "OnFailure"), [])
        self.assertIn("-/etc/report-mark/alert.env", _directives(INCIDENT_SERVICE, "EnvironmentFile"))
        self.assertEqual(_directives(INCIDENT_TIMER, "OnUnitActiveSec"), ["2min"])

    def test_incident_state_dir_is_not_shared_with_another_instance(self):
        """`handler.lock` 與觀測快取是固定檔名：兩組共用狀態目錄會互相覆寫。"""
        dirs = {}
        for unit in sorted(SYSTEMD_DIR.glob("report-mark-*incident.service")):
            for value in _directives(unit, "Environment"):
                if value.startswith("INCIDENT_STATE_DIR="):
                    dirs.setdefault(value.split("=", 1)[1], []).append(unit.name)
        self.assertEqual({k: v for k, v in dirs.items() if len(v) > 1}, {})

    def test_reminder_interval_is_one_sync_cycle(self):
        """來源每 3 小時才推一輪、可能一壞就是整個週末：提醒間隔拉長，免得兩天 96 則。"""
        env = dict(v.split("=", 1) for v in _directives(INCIDENT_SERVICE, "Environment"))
        self.assertEqual(env["INCIDENT_REMINDER_SECONDS"], "10800")


class BeaconUnitTests(unittest.TestCase):
    def test_publishes_every_ten_minutes_without_catch_up(self):
        self.assertEqual(_directives(BEACON_TIMER, "OnCalendar"), ["*-*-* *:05/10:00"])
        self.assertEqual(_directives(BEACON_TIMER, "Persistent"), [])

    def test_runs_the_publisher_with_the_sync_environment(self):
        """信標要回報的是「sync 實際上用什麼設定在跑」：讀 sync 那份環境檔，不載 LLM 金鑰。"""
        self.assertIn("scripts/publish_host_beacon.py", _directives(BEACON_SERVICE, "ExecStart")[0])
        self.assertEqual(_directives(BEACON_SERVICE, "EnvironmentFile"), ["-/etc/default/report-mark-sync"])
        self.assertEqual(_directives(BEACON_SERVICE, "Type"), ["oneshot"])

    def test_no_onfailure_and_fits_in_timer_interval(self):
        """失敗的後果是信標變舊，由另一端的探針告警；這裡再掛 OnFailure 只是每 10 分鐘一則重複通知。"""
        self.assertEqual(_directives(BEACON_SERVICE, "OnFailure"), [])
        self.assertLess(int(_directives(BEACON_SERVICE, "TimeoutStartSec")[0]), 600)

    def test_stale_threshold_tolerates_missed_runs(self):
        """預設的失聯門檻（45 分鐘）要涵蓋至少三班發布（每班 10 分鐘），單次失敗不誤報。"""
        from app import config
        self.assertGreaterEqual(config.Settings.__dataclass_fields__["host_beacon_stale_minutes"].default, 3 * 10 + 10)


if __name__ == "__main__":
    unittest.main()
