"""/api/progress 與 /api/review/judge-scale 的授權（issue #348，HTTP 層）。

/api/progress 帶完整管線資料（runtime、pipelines、log 尾巴），只給有 `ops.read` 的管理員；
待複核頁只需要判定尺，改打 /api/review/judge-scale（review router 的 `review.manage`），回應只有
`evaluation.qa` 的三個鍵、不含 runtime。兩條都經 `require_admin`，所以 `ADMIN_MFA_REQUIRED`
開啟時沒設 TOTP 的管理員一律 403 `mfa_enrollment_required`。

DB 快照與 runtime 都是假資料（patch `web.stats_snapshot.db_stats_snapshot` 與
`monitor._gather_runtime`），不連 DB、不掃 /proc；被擋下的請求兩者都不該被呼叫。
結構面（每條路由的 dependency 樹）在 tests/test_authz.py。
"""

from __future__ import annotations

import dataclasses
import unittest
from unittest import mock

from fake_accounts import FakeAccounts, install, session_cookies
from fastapi.testclient import TestClient

from app import config
from app.services import totp
from web import stats_snapshot
from web.routers import monitor
from web.server import app

_SNAPSHOT = {
    "total_reports": 6,
    "total_chunks": 12,
    "markets": [{"market": "TW", "count": 6}],
    "sources": [],
    "instrument_types": [],
    "report_types": [],
    "summary_done": 3,
    "summary_total": 7,
    "takeaway_done_30d": 4,
    "takeaway_total_30d": 10,
    "takeaway_latest": "2026-07-20",
    "signal_done_30d": 1,
    "signal_total_30d": 10,
    "signal_latest": "2026-07-16",
    "evaluation": {
        "qa": {"total": 40, "checked": 5, "judge_checked": 3, "degraded": 1, "below_min": 1,
               "avg_score": 0.5634, "avg_n": 2, "latest": "2026-09-30",
               "judge_model": "deepseek-flash", "judge_since": "2026-09-25", "other_judge_checked": 2},
        "min_score": 0.9,
    },
    "extraction": None,
}
_RUNTIME = {"tagging": None, "ingest": None, "pipelines": {"web": True}, "orchestrator": None}


class _Limited(FakeAccounts):
    """`noops` 被拿掉 ops.read、`noreview` 被拿掉 review.manage（其他 scope 照常）。"""

    _DROP = {"noops": {"ops.read"}, "noreview": {"review.manage"}}

    def _user(self, row, elevated_until=None):
        user = super()._user(row, elevated_until)
        drop = self._DROP.get(user.username)
        return dataclasses.replace(user, scopes=user.scopes - drop) if drop else user


def _policy(on: bool):
    """在範圍內把 Settings 的 admin_mfa_required 換成指定值（同 tests/test_admin_mfa.py）。"""
    return mock.patch.object(config, "_SETTINGS", dataclasses.replace(config.get_settings(), admin_mfa_required=on))


class _Base(unittest.TestCase):
    def setUp(self):
        self.store = _Limited()
        self.store.add_user("alice", "alice-password", "user")
        self.store.add_user("root", "root-password", "admin")
        self.store.add_user("noops", "noops-password", "admin")
        self.store.add_user("noreview", "noreview-password", "admin")
        mfa_id = self.store.add_user("withmfa", "withmfa-password", "admin")
        row = self.store.users[mfa_id]
        row.totp_secret, row.totp_enabled = totp.generate_secret(), True
        ctx = install(self.store)
        ctx.__enter__()
        self.addCleanup(ctx.__exit__, None, None, None)

        self.snapshot_calls = 0
        self.runtime_calls = 0

        async def fake_snapshot():
            self.snapshot_calls += 1
            return dict(_SNAPSHOT)

        def fake_runtime():
            self.runtime_calls += 1
            return dict(_RUNTIME)

        for target, attr, fake in ((stats_snapshot, "db_stats_snapshot", fake_snapshot),
                                   (monitor, "_gather_runtime", fake_runtime)):
            patcher = mock.patch.object(target, attr, fake)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _client(self, username: str | None = None) -> TestClient:
        c = TestClient(app, follow_redirects=False, base_url="http://127.0.0.1")
        if username is not None:
            c.cookies.update(session_cookies(username))
        return c


class ProgressAuthzTests(_Base):
    def test_unauthenticated_gets_401(self):
        self.assertEqual(self._client().get("/api/progress").status_code, 401)

    def test_plain_user_gets_403_without_touching_data(self):
        r = self._client("alice").get("/api/progress")
        self.assertEqual(r.status_code, 403)
        self.assertNotIn("pipelines", r.text)
        self.assertEqual((self.snapshot_calls, self.runtime_calls), (0, 0))

    def test_admin_without_ops_read_gets_missing_scope(self):
        r = self._client("noops").get("/api/progress")
        self.assertEqual((r.status_code, r.json()["code"]), (403, "missing_scope"))
        self.assertEqual((self.snapshot_calls, self.runtime_calls), (0, 0))

    def test_admin_with_ops_read_gets_full_payload(self):
        r = self._client("root").get("/api/progress")
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["pipelines"], {"web": True})
        self.assertEqual(body["evaluation"]["qa"]["below_min"], 1)

    def test_stats_stays_open_to_plain_user(self):
        """/api/stats 與 /api/progress 同一支 router：守門掛在路由上，stats 不受影響。"""
        r = self._client("alice").get("/api/stats")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["username"], "alice")


class JudgeScaleAuthzTests(_Base):
    def test_unauthenticated_gets_401(self):
        self.assertEqual(self._client().get("/api/review/judge-scale").status_code, 401)

    def test_plain_user_gets_403(self):
        r = self._client("alice").get("/api/review/judge-scale")
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.snapshot_calls, 0)

    def test_admin_without_review_manage_gets_missing_scope(self):
        r = self._client("noreview").get("/api/review/judge-scale")
        self.assertEqual((r.status_code, r.json()["code"]), (403, "missing_scope"))
        self.assertEqual(self.snapshot_calls, 0)

    def test_review_manage_gets_only_the_scale(self):
        """回應只有 evaluation.qa 的三個鍵：不含 runtime、管線，也不含覆蓋率與分數統計。"""
        r = self._client("root").get("/api/review/judge-scale")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json(), {"judge_model": "deepseek-flash", "judge_since": "2026-09-25",
                                    "other_judge_checked": 2})
        self.assertLessEqual(set(r.json()), set(_SNAPSHOT["evaluation"]["qa"]))
        self.assertEqual(self.runtime_calls, 0)

    def test_admin_without_ops_read_still_gets_the_scale(self):
        """判定尺只看 review.manage：沒有 ops.read 的管理員拿得到判定尺、拿不到 /api/progress。"""
        c = self._client("noops")
        self.assertEqual(c.get("/api/review/judge-scale").status_code, 200)
        self.assertEqual(c.get("/api/progress").status_code, 403)

    def test_missing_qa_block_falls_back_to_current_judge(self):
        """快照缺 evaluation.qa（理論上不會）時只回現行判定尺，不 500、不編日期。"""
        snap = {**_SNAPSHOT, "evaluation": {"qa": None, "min_score": 0.9}}

        async def fake_snapshot():
            return snap

        with mock.patch.object(stats_snapshot, "db_stats_snapshot", fake_snapshot):
            r = self._client("root").get("/api/review/judge-scale")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json(), {"judge_model": stats_snapshot._JUDGE_MODEL, "judge_since": None,
                                    "other_judge_checked": 0})


class MfaPolicyTests(_Base):
    """兩條都經 require_admin：ADMIN_MFA_REQUIRED 開啟時，沒設 TOTP 的管理員先被擋在 TOTP 設定。"""

    _PATHS = ("/api/progress", "/api/review/judge-scale")

    def test_admin_without_totp_is_blocked_when_on(self):
        c = self._client("root")
        with _policy(True):
            for path in self._PATHS:
                with self.subTest(path=path):
                    r = c.get(path)
                    self.assertEqual((r.status_code, r.json().get("code")), (403, "mfa_enrollment_required"))
        self.assertEqual((self.snapshot_calls, self.runtime_calls), (0, 0))

    def test_admin_with_totp_passes_when_on(self):
        c = self._client("withmfa")
        with _policy(True):
            for path in self._PATHS:
                with self.subTest(path=path):
                    self.assertEqual(c.get(path).status_code, 200)

    def test_policy_off_lets_admin_without_totp_through(self):
        c = self._client("root")
        with _policy(False):
            for path in self._PATHS:
                with self.subTest(path=path):
                    self.assertEqual(c.get(path).status_code, 200)

    def test_plain_user_gets_the_ordinary_403_when_on(self):
        c = self._client("alice")
        with _policy(True):
            for path in self._PATHS:
                with self.subTest(path=path):
                    r = c.get(path)
                    self.assertEqual(r.status_code, 403)
                    self.assertNotEqual(r.json().get("code"), "mfa_enrollment_required")


if __name__ == "__main__":
    unittest.main()
