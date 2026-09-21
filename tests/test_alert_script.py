"""`deploy/systemd/report-mark-alert.sh`（各 unit 的 OnFailure 告警器）的執行層測試。

這支腳本在「東西已經壞掉」時執行，鐵律是全程容錯、恆 exit 0。2026-09-06 至 09-21
`REPORT_MARK_ALERT_WEBHOOK` 一直是安裝文件的佔位字串，九個 unit 的失敗告警沒有一則送達，
而腳本只記一句「投遞失敗」——這裡釘住的是：失敗原因讀得出來、secret 讀不出來。

全部用假的 curl／logger／systemctl／journalctl，不連網；`REPORT_MARK_ROOT` 指向暫存目錄，
不碰 repo 根的 `data/`。
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "deploy" / "systemd" / "report-mark-alert.sh"


class _Harness:
    def __init__(self, curl_rc: int = 0):
        self._tmp = tempfile.TemporaryDirectory(prefix="report-mark-alert-test-")
        self.root = Path(self._tmp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.curl_log = self.root / "curl.log"
        self.logger_log = self.root / "logger.log"
        self._fake("curl", f'echo called >> "{self.curl_log}"\nexit {curl_rc}\n')
        self._fake("logger", f'echo "$@" >> "{self.logger_log}"\n')
        self._fake("systemctl", "exit 0\n")
        self._fake("journalctl", "exit 0\n")

    def _fake(self, name: str, body: str) -> None:
        path = self.bin / name
        path.write_text("#!/usr/bin/env bash\n" + body, encoding="utf-8")
        path.chmod(0o755)

    def close(self) -> None:
        self._tmp.cleanup()

    def run(self, webhook: str | None) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env["PATH"] = f"{self.bin}:{env['PATH']}"
        env["REPORT_MARK_ROOT"] = str(self.root)
        env.pop("REPORT_MARK_ALERT_WEBHOOK", None)
        if webhook is not None:
            env["REPORT_MARK_ALERT_WEBHOOK"] = webhook
        return subprocess.run(
            ["bash", str(SCRIPT), "report-mark-sync.service"],
            capture_output=True, text=True, env=env, timeout=30,
        )

    @property
    def curl_calls(self) -> int:
        return len(self.curl_log.read_text(encoding="utf-8").splitlines()) if self.curl_log.exists() else 0

    @property
    def logged(self) -> str:
        return self.logger_log.read_text(encoding="utf-8") if self.logger_log.exists() else ""


class AlertScriptWebhookTests(unittest.TestCase):
    def _harness(self, **kw) -> _Harness:
        h = _Harness(**kw)
        self.addCleanup(h.close)
        return h

    def test_unset_webhook_sends_nothing_and_still_records_failure(self):
        h = self._harness()
        p = h.run(None)
        self.assertEqual(p.returncode, 0)
        self.assertEqual(h.curl_calls, 0)
        self.assertIn("UNIT=report-mark-sync.service", (h.root / "data" / "unit_failures.log").read_text("utf-8"))

    def test_successful_delivery_logs_no_warning(self):
        h = self._harness(curl_rc=0)
        p = h.run("https://hooks.example.invalid/services/TOKEN123")
        self.assertEqual(p.returncode, 0)
        self.assertEqual(h.curl_calls, 1)
        self.assertNotIn("webhook", h.logged)

    def test_delivery_failure_logs_curl_exit_code_but_never_the_url(self):
        h = self._harness(curl_rc=6)
        p = h.run("https://secret-host.invalid/services/TOKEN123")
        self.assertEqual(p.returncode, 0, "告警器自己不得失敗")
        self.assertIn("curl_rc=6", h.logged)
        everything = h.logged + p.stdout + p.stderr
        self.assertNotIn("TOKEN123", everything)
        self.assertNotIn("secret-host", everything)

    def test_placeholder_value_is_reported_as_misconfiguration_without_calling_curl(self):
        for bad in ("<你的 webhook URL>", "hooks.slack.com/services/T0/B0/SECRETVALUE",
                    "https://hooks.example.com/x y", "https://"):
            with self.subTest(value=bad):
                h = self._harness()
                p = h.run(bad)
                self.assertEqual(p.returncode, 0)
                self.assertEqual(h.curl_calls, 0, "形狀不對的值不該交給 curl")
                self.assertIn("不是合法 URL", h.logged)
                if bad != "https://":  # 這個值本來就出現在固定的說明文字裡
                    self.assertNotIn(bad, h.logged + p.stdout + p.stderr)

    def test_url_never_reaches_argv(self):
        """URL 走 `-K -` 從 stdin 進 curl；出現在 argv 就等於出現在 `ps`。"""
        h = self._harness()
        h._fake("curl", f'echo "$@" >> "{h.curl_log}"\nexit 0\n')
        h.run("https://hooks.example.invalid/services/TOKEN123")
        self.assertNotIn("TOKEN123", h.curl_log.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
