"""選配的通用 webhook 告警（app/services/alert_webhook.py）與三個觸發點。

驗：預設關（沒設 URL 什麼都不送）；payload 形狀（含 `text`）與不含 URL；不合法 URL 不送；冷卻（同一事件＋鍵只送
一次，不同鍵各自送）；送不出去 fail-open 且只回類別；容器排程包裝（非零退出才送、撞鎖 75 不送、退出碼原樣傳回、
crontab 每個工作都包）；批次斷路器跳脫時送；上傳積壓的檢查只在超過門檻時送；web 只在設了 URL 時啟動檢查。

HTTP 一律經 `httpx.MockTransport`，不連網。假 URL 用 `.invalid` 網域。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services import alert_webhook as aw  # noqa: E402
from deploy.container import run_job, scheduler  # noqa: E402

URL = "https://hooks.example.invalid/fixed-test-secret-generic-hook"
NOW = datetime(2026, 11, 10, 3, 0, tzinfo=timezone.utc)


class _Hook:
    def __init__(self, status=200, fail: Exception | None = None):
        self.status, self.fail, self.requests = status, fail, []

    def transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            if self.fail is not None:
                raise self.fail
            return httpx.Response(self.status)

        return httpx.MockTransport(handler)


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state = Path(self._tmp.name) / "state.json"
        p = mock.patch.dict(os.environ, {aw.URL_ENV: URL, aw.STATE_FILE_ENV: str(self.state)})
        p.start()
        self.addCleanup(p.stop)
        self.hook = _Hook()


class NotifyTests(_Base):
    def test_disabled_by_default(self):
        with mock.patch.dict(os.environ, {aw.URL_ENV: ""}):
            self.assertEqual(aw.notify(aw.EVENT_BATCH_FAILED, "x", transport=self.hook.transport()), "disabled")
        self.assertEqual(self.hook.requests, [])

    def test_payload(self):
        rc = aw.notify(aw.EVENT_BATCH_FAILED, "排程工作 backup 失敗", key="backup", details={"exit_code": 1},
                       now=NOW, transport=self.hook.transport(), cooldown_minutes=60)
        self.assertEqual(rc, "sent")
        (req,) = self.hook.requests
        self.assertEqual(str(req.url), URL)
        self.assertEqual(req.headers["content-type"], "application/json")
        body = json.loads(req.content)
        self.assertEqual(set(body), {"source", "event", "severity", "summary", "text", "details", "host", "at"})
        self.assertEqual(body["event"], "batch_failed")
        self.assertEqual(body["details"], {"exit_code": 1})
        self.assertEqual(body["at"], "2026-11-10T03:00:00Z")
        self.assertIn("backup", body["text"])
        self.assertNotIn("fixed-test-secret", json.dumps(body))

    def test_bad_url_is_not_sent(self):
        for bad in ("ftp://x.invalid/a", "https://x.invalid/a b", "not a url"):
            with self.subTest(url=bad), mock.patch.dict(os.environ, {aw.URL_ENV: bad}):
                self.assertEqual(aw.notify(aw.EVENT_BATCH_FAILED, "x", transport=self.hook.transport()), "bad_url")
        self.assertEqual(self.hook.requests, [])

    def test_cooldown_per_event_and_key(self):
        t = self.hook.transport()
        send = lambda key, minutes=60: aw.notify(aw.EVENT_BATCH_FAILED, "x", key=key, now=NOW, transport=t,  # noqa: E731
                                                 cooldown_minutes=minutes)
        self.assertEqual(send("backup"), "sent")
        self.assertEqual(send("backup"), "cooldown")
        self.assertEqual(send("db-audit"), "sent", "不同工作各自冷卻")
        self.assertEqual(aw.notify(aw.EVENT_LLM_BREAKER, "x", now=NOW, transport=t, cooldown_minutes=60), "sent")
        later = NOW.replace(hour=5)
        self.assertEqual(aw.notify(aw.EVENT_BATCH_FAILED, "x", key="backup", now=later, transport=t,
                                   cooldown_minutes=60), "sent", "冷卻期過了再送")
        self.assertEqual(len(self.hook.requests), 4)

    def test_failures_are_fail_open_and_say_only_the_class(self):
        for hook, expected in ((_Hook(status=502), "failed:http_502"),
                               (_Hook(fail=httpx.ConnectTimeout(f"timeout to {URL}")), "failed:ConnectTimeout")):
            with self.subTest(expected=expected):
                with self.assertLogs("app.services.alert_webhook", level="WARNING") as logs:
                    rc = aw.notify(aw.EVENT_BATCH_FAILED, "x", key="k", now=NOW, transport=hook.transport(),
                                   cooldown_minutes=60)
                self.assertEqual(rc, expected)
                self.assertNotIn("fixed-test-secret", "\n".join(logs.output))
        # 失敗不記冷卻：下一次照樣送。
        self.assertEqual(aw.notify(aw.EVENT_BATCH_FAILED, "x", key="k", now=NOW, transport=self.hook.transport(),
                                   cooldown_minutes=60), "sent")

    def test_unknown_event_never_raises(self):
        self.assertTrue(aw.notify("whatever", "x", transport=self.hook.transport()).startswith("failed:"))

    def test_unwritable_state_still_sends(self):
        with mock.patch.dict(os.environ, {aw.STATE_FILE_ENV: "/nonexistent/x/state.json"}):
            self.assertEqual(aw.notify(aw.EVENT_BATCH_FAILED, "x", now=NOW, transport=self.hook.transport(),
                                       cooldown_minutes=60), "sent")


class RunJobTests(unittest.TestCase):
    def _run(self, rc):
        alerts = []
        out = run_job.main(["run_job.py", "backup", "--", "bash", "scripts/db_backup.sh"],
                           runner=lambda cmd: rc, alert=lambda *a: alerts.append(a))
        return out, alerts

    def test_failure_alerts_and_keeps_exit_code(self):
        rc, alerts = self._run(3)
        self.assertEqual(rc, 3)
        self.assertEqual([(a[0], a[1]) for a in alerts], [("backup", 3)])

    def test_success_and_lock_busy_do_not_alert(self):
        for code in (0, run_job.EXIT_LOCK_BUSY):
            with self.subTest(code=code):
                rc, alerts = self._run(code)
                self.assertEqual((rc, alerts), (code, []))

    def test_usage(self):
        self.assertEqual(run_job.main(["run_job.py", "backup", "bash"]), run_job.EXIT_USAGE)

    def test_alert_payload_has_no_command_output(self):
        sent = []
        with mock.patch.object(aw, "notify", lambda *a, **k: sent.append((a, k)) or "sent"):
            run_job._alert("backup", 2, 1.25)
        ((args, kw),) = sent
        self.assertEqual(args[0], aw.EVENT_BATCH_FAILED)
        self.assertEqual(kw["key"], "backup")
        self.assertEqual(kw["details"], {"job": "backup", "exit_code": 2, "elapsed_s": 1.2})

    def test_every_scheduled_job_is_wrapped(self):
        tab = scheduler.render_crontab(list(scheduler.JOBS))
        lines = [ln for ln in tab.splitlines() if ln and not ln.startswith("#")]
        self.assertEqual(len(lines), len(scheduler.JOBS))
        for job, line in zip(scheduler.JOBS, lines):
            with self.subTest(job=job.name):
                self.assertIn(f"python deploy/container/run_job.py {job.name} -- ", line)


class BreakerHookTests(unittest.TestCase):
    def test_trip_sends_once(self):
        from scripts import _claude_cli
        from scripts._claude_cli import LlmEnvironmentError

        sent = []
        breaker = _claude_cli._Breaker()
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.dict(os.environ, {"LLM_BREAKER_FILE": str(Path(tmp) / ".llm_breaker")}), \
                mock.patch.object(aw, "notify", lambda *a, **k: sent.append((a, k)) or "sent"):
            with self.assertRaises(LlmEnvironmentError):
                for _ in range(_claude_cli.BREAKER_WINDOW):
                    breaker.observe("timeout")
        self.assertEqual(len(sent), 1)
        (args, kw), = sent
        self.assertEqual(args[0], aw.EVENT_LLM_BREAKER)
        self.assertEqual(kw["severity"], "critical")

    def test_notify_failure_does_not_change_breaker_behaviour(self):
        from scripts import _claude_cli
        from scripts._claude_cli import LlmEnvironmentError

        breaker = _claude_cli._Breaker()

        def boom(*a, **k):
            raise RuntimeError("x")

        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.dict(os.environ, {"LLM_BREAKER_FILE": str(Path(tmp) / ".llm_breaker")}), \
                mock.patch.object(aw, "notify", boom):
            with self.assertRaises(LlmEnvironmentError):
                for _ in range(_claude_cli.BREAKER_WINDOW):
                    breaker.observe("timeout")


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class _Session:
    def __init__(self, rows):
        self.rows = rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, stmt, params=None):
        return _Result(self.rows)


class UploadBacklogTests(_Base):
    def _check(self, rows, threshold=120):
        return asyncio.run(aw.check_upload_backlog(lambda: _Session(rows), threshold_minutes=threshold, now=NOW,
                                                   transport=self.hook.transport()))

    def test_under_threshold_is_quiet(self):
        self.assertEqual(self._check([("clean", 3, 60 * 60.0), ("published", 9, 9e6)]), "ok")
        self.assertEqual(self.hook.requests, [])

    def test_over_threshold_alerts_with_counts_only(self):
        self.assertEqual(self._check([("clean", 3, 3 * 3600.0), ("scanning", 1, 100.0)]), "sent")
        body = json.loads(self.hook.requests[0].content)
        self.assertEqual(body["event"], "upload_backlog")
        self.assertEqual(body["details"]["in_flight"], 4)
        self.assertEqual(body["details"]["oldest_minutes"], 180)

    def test_watch_starts_only_when_configured(self):
        async def go():
            with mock.patch.dict(os.environ, {aw.URL_ENV: ""}):
                self.assertIsNone(aw.start_watch())
            task = aw.start_watch()
            self.assertIsNotNone(task)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        asyncio.run(go())


if __name__ == "__main__":
    unittest.main()
