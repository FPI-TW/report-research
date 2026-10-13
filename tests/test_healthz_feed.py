"""`/healthz/feed` 與 `GET /api/admin/ops/remote-hosts`：新檔來源主機的信標，HTTP 層。

判讀規則本身在 tests/test_host_beacon.py；這裡守 HTTP 契約：`/healthz/feed` 本機直連以外一律 404、回應只有 `feed`
一鍵、告警狀態 503、判不出來與沒設定 200、逾時有界、結論快取；管理端點要登入＋`ops.read`、與 `/healthz/feed`
共用同一份快取、沒設定時是空清單、R2 讀不到不是 5xx。不連網：`host_beacon.read` 以假值替換。
"""
from __future__ import annotations

import dataclasses
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from fake_accounts import FakeAccounts, install
from fastapi.testclient import TestClient

from app.config import get_settings
from app.services import host_beacon
from web import auth
from web.routers import health
from web.server import _AUTH_ALLOWLIST, app

NOW = datetime(2026, 10, 12, 8, 0, tzinfo=timezone.utc)
ADMIN_PW = "root-password-1"
USER_PW = "alice-password-1"


def _local() -> TestClient:
    return TestClient(app, follow_redirects=False, base_url="http://127.0.0.1", client=("127.0.0.1", 51000))


def _reading(state: str = "ok", **kw) -> host_beacon.Reading:
    reasons = (state,) if state in host_beacon.ALERT_STATES else ()
    return host_beacon.Reading(name="office", state=state, reasons=kw.pop("reasons", reasons), checked_at=NOW,
                               published_at=NOW - timedelta(minutes=3), age_seconds=180, **kw)


def _watching(name: str = "office"):
    return mock.patch.object(health, "get_settings",
                             return_value=dataclasses.replace(get_settings(), host_beacon_watch=name))


class HealthzFeedTests(unittest.TestCase):
    def _get(self, reading: host_beacon.Reading | None = None, client: TestClient | None = None):
        fake = mock.Mock(return_value=reading or _reading())
        with _watching(), mock.patch.object(host_beacon, "read", fake):
            return (client or _local()).get("/healthz/feed"), fake

    def test_in_allowlist(self):
        self.assertIn("/healthz/feed", _AUTH_ALLOWLIST)

    def test_only_direct_loopback(self):
        for client, headers in (
            (TestClient(app, base_url="http://127.0.0.1", client=("203.0.113.9", 51000)), {}),
            (TestClient(app, base_url="http://research.example.com", client=("127.0.0.1", 51000)), {}),
            (_local(), {"X-Forwarded-For": "203.0.113.9"}),
            (_local(), {"X-Real-IP": "203.0.113.9"}),
        ):
            fake = mock.Mock(return_value=_reading("stale"))
            with _watching(), mock.patch.object(host_beacon, "read", fake):
                r = client.get("/healthz/feed", headers=headers)
            with self.subTest(headers=headers, base=str(client.base_url)):
                self.assertEqual(r.status_code, 404)
                self.assertNotIn("feed", r.text)
                fake.assert_not_called()

    def test_ok_is_200_single_key(self):
        r, fake = self._get()
        self.assertEqual((r.status_code, r.json()), (200, {"feed": "ok"}))
        self.assertEqual(fake.call_args.args[1], "office")
        self.assertEqual(fake.call_args.kwargs, {"stale_minutes": 45, "feed_stale_hours": 7})

    def test_every_alert_state_is_503_and_names_only_the_state(self):
        for state in host_beacon.ALERT_STATES:
            health._FEED_CACHE.clear()
            with self.subTest(state=state):
                r, _ = self._get(_reading(state, error="內部細節"))
                self.assertEqual((r.status_code, r.json()), (503, {"feed": state}))

    def test_unknown_is_200(self):
        r, _ = self._get(_reading("unknown", error="R2 讀取失敗"))
        self.assertEqual((r.status_code, r.json()), (200, {"feed": "unknown"}))

    def test_not_configured_is_disabled_without_touching_r2(self):
        fake = mock.Mock()
        with _watching(""), mock.patch.object(host_beacon, "read", fake):
            r = _local().get("/healthz/feed")
        self.assertEqual((r.status_code, r.json()), (200, {"feed": "disabled"}))
        fake.assert_not_called()

    def test_result_is_cached(self):
        fake = mock.Mock(return_value=_reading("push_failed"))
        with _watching(), mock.patch.object(host_beacon, "read", fake):
            for _ in range(3):
                self.assertEqual(_local().get("/healthz/feed").json(), {"feed": "push_failed"})
        self.assertEqual(fake.call_count, 1)

    def test_failure_is_unknown(self):
        with _watching(), mock.patch.object(host_beacon, "read", mock.Mock(side_effect=RuntimeError("boom"))):
            r = _local().get("/healthz/feed")
        self.assertEqual((r.status_code, r.json()), (200, {"feed": "unknown"}))


class FeedReadingTimeoutTests(unittest.IsolatedAsyncioTestCase):
    """直接量 `feed_reading`：TestClient 每個請求收掉事件迴圈時會等執行緒跑完，量不到「回應有沒有先回來」。"""

    async def test_slow_r2_read_is_unknown_and_bounded(self):
        release = threading.Event()
        self.addCleanup(release.set)

        def slow(*_a, **_kw):
            release.wait(10)
            return _reading("stale")

        health._FEED_CACHE.clear()
        with _watching(), mock.patch.object(host_beacon, "read", slow), mock.patch.object(health, "_FEED_WAIT", 0.05):
            started = time.monotonic()
            reading = await health.feed_reading()
            elapsed = time.monotonic() - started
            # 逾時的結論照樣快取：下一個請求不再堆一條執行緒。
            again = await health.feed_reading()
        release.set()
        self.assertEqual((reading.state, reading.error), ("unknown", "R2 讀取逾時"))
        self.assertLess(elapsed, 5)
        self.assertIs(again, reading)


class RemoteHostsApiTests(unittest.TestCase):
    URL = "/api/admin/ops/remote-hosts"

    def setUp(self):
        auth._FAILS.clear()
        self.store = FakeAccounts()
        self.store.add_user("root", ADMIN_PW, "admin")
        self.store.add_user("alice", USER_PW, "user")
        self._ctx = install(self.store)
        self._ctx.__enter__()
        self.admin = self._login("root", ADMIN_PW)

    def tearDown(self):
        self._ctx.__exit__(None, None, None)
        auth._FAILS.clear()

    def _login(self, username, password):
        client = TestClient(app, follow_redirects=False, base_url="http://127.0.0.1")
        r = client.post("/login", data={"username": username, "password": password})
        self.assertEqual(r.status_code, 303, r.headers.get("location"))
        return client

    def test_requires_login_and_admin(self):
        anon = TestClient(app, follow_redirects=False, base_url="http://127.0.0.1").get(self.URL)
        self.assertIn(anon.status_code, (302, 303, 401))
        self.assertEqual(self._login("alice", USER_PW).get(self.URL).status_code, 403)

    def test_not_configured_is_empty_list(self):
        fake = mock.Mock()
        with _watching(""), mock.patch.object(host_beacon, "read", fake):
            r = self.admin.get(self.URL)
        self.assertEqual((r.status_code, r.json()), (200, {"items": []}))
        fake.assert_not_called()

    def test_returns_detail_and_shares_cache_with_healthz(self):
        beacon = host_beacon.parse(host_beacon.encode(host_beacon.build(
            name="office", host="office-pc", published_epoch=1_791_000_000,
            sync={"source": "nas", "inbox_push": True, "timer_active": True,
                  "round": {"state": "done", "started_epoch": 1_790_999_400, "updated_epoch": None, "rc": 0},
                  "feed": {"round": "20261012_150003", "epoch": 1_790_999_410, "result": "ok", "new_files": 2,
                           "push": "failed"},
                  "heartbeat_epoch": None, "pending_replays": 1},
            services=[{"name": "sync", "kind": "systemd", "tier": "important", "description": "NAS 增量同步匯入",
                       "summary": "idle", "health": "ok", "health_reason": "排程待命",
                       "timer": host_beacon.SYNC_TIMER_UNIT, "timer_active": True, "last_run_at": None,
                       "next_run_at": None, "exit_status": 0}],
            services_error=None)))
        fake = mock.Mock(return_value=_reading("push_failed", beacon=beacon))
        with _watching(), mock.patch.object(host_beacon, "read", fake):
            r = self.admin.get(self.URL)
            probe = _local().get("/healthz/feed")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(fake.call_count, 1)
        self.assertEqual((probe.status_code, probe.json()), (503, {"feed": "push_failed"}))
        (item,) = r.json()["items"]
        self.assertEqual((item["name"], item["state"], item["reasons"], item["host"], item["age_seconds"]),
                         ("office", "push_failed", ["push_failed"], "office-pc", 180))
        self.assertEqual((item["stale_after_minutes"], item["feed_stale_after_hours"]), (45, 7))
        self.assertEqual((item["sync"]["push"], item["sync"]["pending_replays"], item["sync"]["new_files"],
                          item["sync"]["round_state"]), ("failed", 1, 2, "done"))
        self.assertEqual(item["sync"]["feed_at"], (NOW - timedelta(minutes=3, seconds=590)).isoformat())
        self.assertEqual([s["name"] for s in item["services"]], ["sync"])

    def test_r2_unreadable_is_200_unknown_not_5xx(self):
        fake = mock.Mock(return_value=host_beacon.Reading(name="office", state="unknown", checked_at=NOW,
                                                          error="R2 讀取失敗：ObjectStorageError"))
        with _watching(), mock.patch.object(host_beacon, "read", fake):
            r = self.admin.get(self.URL)
        self.assertEqual(r.status_code, 200, r.text)
        (item,) = r.json()["items"]
        self.assertEqual((item["state"], item["reasons"], item["sync"], item["services"]), ("unknown", [], None, None))
        self.assertIn("R2", item["error"])


if __name__ == "__main__":
    unittest.main()
