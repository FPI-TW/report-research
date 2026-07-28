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
from web.server import app  # noqa: E402
from web.routers import health  # noqa: E402


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
