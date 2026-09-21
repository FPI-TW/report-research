# tests/test_healthz.py
"""免認證存活探測 /healthz（P3 生產韌性）。

它要解決的是一個**偵測不到的故障**：登入路徑完全不碰 DB，所以 DB 掛掉時站台開得
起來、登入還會成功、每個查詢 500；而在此之前 `/healthz` 與不存在的路由都回 302
（被 auth middleware 導向 /login），外部監控無從分辨。
"""
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("REPORT_MARK_ACCESS_USERNAME", "tester")
os.environ.setdefault("REPORT_MARK_ACCESS_PASSWORD", "testpass")
os.environ.setdefault("REPORT_MARK_SESSION_SECRET", "fixed-test-secret-0123456789")

from fastapi.testclient import TestClient  # noqa: E402

from web.routers import health  # noqa: E402
from web.server import app  # noqa: E402


class _FakeSession:
    def __init__(self, boom: bool = False):
        self.boom = boom

    async def __aenter__(self):
        if self.boom:
            raise RuntimeError("db down")
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, *a, **k):
        return None


def _client() -> TestClient:
    return TestClient(app, follow_redirects=False, base_url="http://127.0.0.1")


class HealthzTests(unittest.TestCase):
    def setUp(self):
        health._cache = (0.0, False)  # 每個測試從冷快取開始

    def tearDown(self):
        health._cache = (0.0, False)

    def test_healthy_returns_200_ok(self):
        with patch.object(health.deps, "SessionFactory", lambda: _FakeSession()):
            r = _client().get("/healthz")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"status": "ok"})

    def test_db_down_returns_503_degraded(self):
        """DB 不可用必須回 **503**——回 200 帶 degraded 欄位的話，多數 uptime 監控
        與負載平衡器預設仍判定為健康，等於白做。"""
        with patch.object(health.deps, "SessionFactory", lambda: _FakeSession(boom=True)):
            r = _client().get("/healthz")
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.json(), {"status": "degraded"})

    def test_no_auth_required(self):
        """不帶 cookie 也必須拿到真答案，而不是 302 導向 /login。

        沒有這個豁免，探測結果與「路由不存在」完全相同——那正是這個端點要解決的問題。
        """
        with patch.object(health.deps, "SessionFactory", lambda: _FakeSession()):
            r = _client().get("/healthz")
        self.assertNotEqual(r.status_code, 302)
        self.assertEqual(r.status_code, 200)

    def test_unknown_path_still_redirects(self):
        """對照組：豁免只開給 /healthz，deny-by-default 沒有被放寬。"""
        r = _client().get("/zzz-does-not-exist")
        self.assertEqual(r.status_code, 302)

    def test_response_leaks_nothing(self):
        """唯一免認證且對外可達的資料端點——回應不得含 DB 版本、錯誤訊息、主機名。"""
        with patch.object(health.deps, "SessionFactory", lambda: _FakeSession(boom=True)):
            r = _client().get("/healthz")
        self.assertEqual(set(r.json().keys()), {"status"})
        self.assertNotIn("db down", r.text)  # 例外訊息只進日誌

    def test_probe_is_cached(self):
        """免認證端點會被掃描器與監控同時打；沒有快取等於把 DB 往返暴露給任何人。"""
        calls = {"n": 0}

        def factory():
            calls["n"] += 1
            return _FakeSession()

        with patch.object(health.deps, "SessionFactory", factory):
            c = _client()
            for _ in range(5):
                c.get("/healthz")
        self.assertEqual(calls["n"], 1, "TTL 內應只探測一次")

    def test_cache_expiry_reprobes(self):
        calls = {"n": 0}

        def factory():
            calls["n"] += 1
            return _FakeSession()

        with patch.object(health.deps, "SessionFactory", factory):
            c = _client()
            c.get("/healthz")
            health._cache = (health._cache[0] - health._TTL - 1, health._cache[1])
            c.get("/healthz")
        self.assertEqual(calls["n"], 2)

    def test_allowlist_contains_healthz(self):
        """契約防護：路由與白名單必須成對存在。只掛路由沒放行 → 永遠 302。"""
        from web import server

        self.assertIn("/healthz", server._AUTH_ALLOWLIST)


if __name__ == "__main__":
    unittest.main()


class _Storage:
    def __init__(self, *, enabled=True, fail=False):
        self.enabled = enabled
        self.fail = fail
        self.pings = 0

    def ping(self):
        self.pings += 1
        if self.fail:
            raise RuntimeError("SignatureDoesNotMatch: secret-ish detail")


def _local() -> TestClient:
    return TestClient(
        app, follow_redirects=False, base_url="http://127.0.0.1", client=("127.0.0.1", 51000)
    )


class HealthzStorageTests(unittest.TestCase):
    """`/healthz/storage`：R2 可達性，只回答本機直連（設計理由見 routers/health.py）。"""

    def setUp(self):
        health._storage = health._STORAGE_INITIAL
        health._storage_task = None

    tearDown = setUp

    def _with(self, storage):
        return patch.object(health, "get_object_storage", lambda: storage)

    def test_only_direct_loopback_gets_an_answer(self):
        """它在 auth 白名單裡，所以『對外等於不存在』要靠 handler 自己守住。"""
        storage = _Storage()
        with self._with(storage):
            outside = _client().get("/healthz/storage")  # 對端不是 loopback
            proxied = _local().get("/healthz/storage", headers={"X-Forwarded-For": "203.0.113.9"})
            public_host = TestClient(
                app, base_url="http://research.example.com", client=("127.0.0.1", 51000)
            ).get("/healthz/storage")
        for r in (outside, proxied, public_host):
            self.assertEqual(r.status_code, 404)
            self.assertNotIn("storage", r.json())
        self.assertEqual(storage.pings, 0)  # 被拒絕的請求不得觸發計費的 R2 操作

    def test_local_mode_reports_disabled_without_probing(self):
        storage = _Storage(enabled=False)
        with self._with(storage):
            r = _local().get("/healthz/storage")
        self.assertEqual((r.status_code, r.json()), (200, {"storage": "disabled"}))
        self.assertEqual(storage.pings, 0)

    def test_reachable_is_ok_and_cached(self):
        storage = _Storage()
        with self._with(storage):
            c = _local()
            bodies = [c.get("/healthz/storage").json() for _ in range(4)]
        self.assertEqual(bodies, [{"storage": "ok"}] * 4)
        self.assertEqual(storage.pings, 1)

    def test_single_failure_is_not_degraded_two_in_a_row_is_503(self):
        storage = _Storage(fail=True)
        with self._with(storage):
            c = _local()
            first = c.get("/healthz/storage")
            health._storage = health._storage._replace(expires_at=0.0)  # 失敗 TTL 到期
            second = c.get("/healthz/storage")
        self.assertEqual((first.status_code, first.json()), (200, {"storage": "unknown"}))
        self.assertEqual((second.status_code, second.json()), (503, {"storage": "degraded"}))
        self.assertNotIn("secret-ish", second.text)  # 例外訊息只進日誌

    def test_recovery_clears_the_failure_streak(self):
        storage = _Storage(fail=True)
        with self._with(storage):
            c = _local()
            c.get("/healthz/storage")
            storage.fail = False
            health._storage = health._storage._replace(expires_at=0.0)
            r = c.get("/healthz/storage")
        self.assertEqual((r.status_code, r.json()), (200, {"storage": "ok"}))
        self.assertEqual(health._storage.consecutive_failures, 0)

    def test_healthz_itself_is_untouched_by_storage_failure(self):
        """R2 掛掉時檢索、問答、雷達都還活著：對外的 /healthz 不得因此變 503。"""
        health._cache = (0.0, False)
        health._storage = health._StorageState("degraded", 5, float("inf"))
        with self._with(_Storage(fail=True)), \
                patch.object(health.deps, "SessionFactory", lambda: _FakeSession()):
            r = _client().get("/healthz")
        health._cache = (0.0, False)
        self.assertEqual((r.status_code, r.json()), (200, {"status": "ok"}))
