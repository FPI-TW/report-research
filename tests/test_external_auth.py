"""對外 API 的 Bearer 認證閘門（`web/external_auth.py` ＋ `web/server.py` 的 `/external/` 放行）。

走 HTTP 層（`TestClient`），假的 `deps.api_clients`（`tests/external_fakes.py`）、檢索與 DB：

- 缺金鑰、格式錯、錯金鑰 → 401（帶 `WWW-Authenticate: Bearer`）；DB 例外 → 503。
- 停用與輪替下一個請求就生效（`resolve_key` 每次查、不快取）。
- scope 不足 → 403；每分鐘限流 → 429＋`Retry-After`；每日額度第 N+1 次 → 429，`Retry-After` 到台北隔日 0 點。
- `/external/*` 不查也不發 session cookie、dev_mode 不作用；`/external/` 以外的路徑不認 Bearer。
"""

from __future__ import annotations

import os
import re
import unittest
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("REPORT_MARK_SESSION_SECRET", "fixed-test-secret-0123456789")

import external_fakes as fx  # noqa: E402
from fake_accounts import session_cookies  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from test_authz import _dependency_calls, _iter_api_routes  # noqa: E402

from web import auth, deps, dev_mode, external_auth  # noqa: E402
from web.server import app  # noqa: E402

SEARCH = "/external/v1/search?q=台積電"


class _ExternalCase(unittest.TestCase):
    def setUp(self):
        self.api = fx.FakeApiClients()
        self.reports: dict[str, fx.Report] = {}
        self._orig = {
            name: getattr(deps, name)
            for name in ("api_clients", "hybrid_search", "embed_query_cached", "SessionFactory")
        }
        self._orig_clock = (external_auth._monotonic, external_auth._utcnow)
        deps.api_clients = self.api
        self.search_calls: list[dict] = []

        async def _hybrid(session, q, qvec, **kw):
            self.search_calls.append(kw)
            return []

        deps.hybrid_search = _hybrid
        deps.embed_query_cached = lambda q: [0.0]
        deps.SessionFactory = lambda: fx.FakeSession(self.reports)
        self.client = TestClient(app, follow_redirects=False)

    def tearDown(self):
        for name, value in self._orig.items():
            setattr(deps, name, value)
        external_auth._monotonic, external_auth._utcnow = self._orig_clock

    def get(self, url=SEARCH, key=None, **kw):
        headers = kw.pop("headers", {})
        if key is not None:
            headers["Authorization"] = f"Bearer {key}"
        return self.client.get(url, headers=headers, **kw)


class BearerParsingTests(_ExternalCase):
    def test_missing_key_is_401_with_challenge(self):
        r = self.get()
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.json()["code"], "api_key_missing")
        self.assertTrue(r.headers["www-authenticate"].startswith("Bearer"))
        self.assertIn("request_id", r.json())
        self.assertEqual(self.api.resolve_calls, 0)

    def test_malformed_header_is_401_invalid(self):
        for header in ("Basic dXNlcjpwYXNz", "Bearer", "Bearer ", "Bearer a b", "token abc"):
            with self.subTest(header=header):
                r = self.get(headers={"Authorization": header})
                self.assertEqual(r.status_code, 401)
                self.assertEqual(r.json()["code"], "api_key_invalid")
                self.assertTrue(r.headers["www-authenticate"].startswith("Bearer"))

    def test_unknown_key_is_401_invalid(self):
        self.api.add(fx.make_client(1))
        r = self.get(key=fx.make_key())
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.json()["code"], "api_key_invalid")
        self.assertTrue(r.headers["www-authenticate"].startswith("Bearer"))

    def test_auth_runs_before_query_validation(self):
        """未認證的請求拿不到 422 的欄位錯誤（不洩漏端點的參數形狀）。"""
        r = self.get("/external/v1/search")
        self.assertEqual(r.status_code, 401)

    def test_resolve_failure_is_503(self):
        self.api.fail_resolve = True
        r = self.get(key=fx.make_key())
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.json()["code"], "api_auth_unavailable")


class KeyLifecycleTests(_ExternalCase):
    def test_disable_takes_effect_on_next_request(self):
        key = self.api.add(fx.make_client(1))
        self.assertEqual(self.get(key=key).status_code, 200)
        self.api.disable(1)
        r = self.get(key=key)
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.json()["code"], "api_key_invalid")

    def test_rotated_old_key_is_401_and_new_key_works(self):
        old = self.api.add(fx.make_client(1))
        self.assertEqual(self.get(key=old).status_code, 200)
        new = self.api.rotate(1)
        self.assertEqual(self.get(key=old).status_code, 401)
        self.assertEqual(self.get(key=new).status_code, 200)

    def test_missing_scope_is_403(self):
        key = self.api.add(fx.make_client(1, scopes=("report.file",)))
        r = self.get(key=key)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["code"], "api_scope_missing")
        key2 = self.api.add(fx.make_client(2, scopes=("search",)))
        r = self.get("/external/v1/reports/11111111-1111-4111-8111-111111111111/file-url", key=key2)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["code"], "api_scope_missing")


class RateLimitAndQuotaTests(_ExternalCase):
    def test_rate_limit_429_with_retry_after_then_refills(self):
        clock = [1000.0]
        external_auth._monotonic = lambda: clock[0]
        key = self.api.add(fx.make_client(1, rate=2))
        self.assertEqual(self.get(key=key).status_code, 200)
        self.assertEqual(self.get(key=key).status_code, 200)
        r = self.get(key=key)
        self.assertEqual(r.status_code, 429)
        self.assertEqual(r.json()["code"], "api_rate_limited")
        self.assertEqual(r.headers["retry-after"], "30")  # 每分鐘 2 個＝每 30 秒補 1 個
        # 被限流的請求不吃每日額度
        self.assertEqual(self.api.usage[1], 2)
        clock[0] += 30
        self.assertEqual(self.get(key=key).status_code, 200)

    def test_rate_limit_is_per_client(self):
        external_auth._monotonic = lambda: 5.0
        a = self.api.add(fx.make_client(1, rate=1))
        b = self.api.add(fx.make_client(2, rate=1))
        self.assertEqual(self.get(key=a).status_code, 200)
        self.assertEqual(self.get(key=a).status_code, 429)
        self.assertEqual(self.get(key=b).status_code, 200)

    def test_lowering_rate_limit_applies_immediately(self):
        clock = [0.0]
        external_auth._monotonic = lambda: clock[0]
        key = self.api.add(fx.make_client(1, rate=100))
        self.assertEqual(self.get(key=key).status_code, 200)
        self.api.clients[1] = fx.make_client(1, rate=1)
        self.assertEqual(self.get(key=key).status_code, 200)
        self.assertEqual(self.get(key=key).status_code, 429)

    def test_daily_quota_n_plus_one_is_429_until_taipei_midnight(self):
        # 2026-10-07 10:00 UTC＝台北 18:00，離台北隔日 0 點 6 小時。
        external_auth._utcnow = lambda: datetime(2026, 10, 7, 10, 0, tzinfo=timezone.utc)
        key = self.api.add(fx.make_client(1, quota=3))
        for _ in range(3):
            self.assertEqual(self.get(key=key).status_code, 200)
        r = self.get(key=key)
        self.assertEqual(r.status_code, 429)
        self.assertEqual(r.json()["code"], "api_quota_exceeded")
        self.assertEqual(r.headers["retry-after"], str(6 * 3600))

    def test_quota_reset_is_taipei_midnight(self):
        f = external_auth.seconds_until_quota_reset
        self.assertEqual(f(datetime(2026, 10, 7, 15, 59, 59, tzinfo=timezone.utc)), 1)
        self.assertEqual(f(datetime(2026, 10, 7, 16, 0, 0, tzinfo=timezone.utc)), 86400)
        self.assertEqual(f(datetime(2026, 10, 7, 15, 59, 59, 500000, tzinfo=timezone.utc)), 1)

    def test_conftest_resets_bucket_state(self):
        self.assertEqual(external_auth._BUCKETS, {})


class SessionIsolationTests(_ExternalCase):
    def test_external_never_sets_session_cookie_even_with_valid_cookie(self):
        key = self.api.add(fx.make_client(1))
        r = self.get(key=key, cookies=session_cookies())
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("set-cookie", {k.lower() for k in r.headers.keys()})
        r = self.get()
        self.assertEqual(r.status_code, 401)
        self.assertNotIn("set-cookie", {k.lower() for k in r.headers.keys()})

    def test_session_cookie_alone_does_not_authenticate_external(self):
        r = self.get(cookies=session_cookies())
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.json()["code"], "api_key_missing")

    def test_bearer_is_not_accepted_on_internal_api(self):
        key = self.api.add(fx.make_client(1))
        r = self.get("/api/search?q=台積電", key=key)
        self.assertEqual(r.status_code, 401)
        self.assertEqual(self.api.resolve_calls, 0)
        self.assertNotIn(auth.COOKIE_NAME, r.cookies)

    def test_dev_mode_does_not_bypass_external(self):
        orig = dev_mode.bypass_allowed
        dev_mode.bypass_allowed = lambda request: True
        try:
            r = self.get()
        finally:
            dev_mode.bypass_allowed = orig
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.json()["code"], "api_key_missing")

    def test_logs_client_identity_but_never_the_raw_key(self):
        key = self.api.add(fx.make_client(1))
        with self.assertLogs("web.external_auth", level="INFO") as logs:
            self.assertEqual(self.get(key=key).status_code, 200)
            self.get(key=key + "x")
        joined = "\n".join(logs.output)
        self.assertIn("client_id=1", joined)
        self.assertIn("key_prefix=00000001", joined)
        self.assertNotIn(key, joined)


class ExternalRouteStructureTests(unittest.TestCase):
    """middleware 對 `/external/` 整個放行，所以每條路由都必須自己掛 `require_api_client`。"""

    def test_every_external_route_requires_an_api_key_scope(self):
        routes = [r for r in _iter_api_routes(app.routes) if r.path.startswith("/external/")]
        self.assertEqual(
            {r.path: {getattr(c, "api_scope", None) for c in _dependency_calls(r.dependant)} - {None} for r in routes},
            {
                "/external/v1/search": {"search"},
                "/external/v1/reports/{report_id}/file-url": {"report.file"},
            },
        )



class EdgeNginxAuthorizationTests(unittest.TestCase):
    """邊緣 nginx 對 `Authorization` 的處理：只有 `/external/` 透傳，其餘 location 一律清空。

    2026-10-07 部署到 EC2 時發現：兩份設定原本每個 location 都 `proxy_set_header Authorization ""`，
    對外 API 經 Cloudflare 進來的 Bearer 金鑰全被拿掉，外網只會拿到 401 `api_key_missing`
    （本機直連 8097 卻一切正常）。
    """

    CONFS = ("deploy/nginx-origin.conf", "deploy/nginx.conf")

    def _locations(self, rel: str) -> dict[str, str]:
        text = (Path(__file__).resolve().parents[1] / rel).read_text(encoding="utf-8")
        blocks = {}
        for m in re.finditer(r"^    location ([^{]+?) \{\n(.*?)^    \}", text, re.S | re.M):
            body = "\n".join(line for line in m.group(2).splitlines() if not line.strip().startswith("#"))
            blocks[m.group(1).strip()] = body
        return blocks

    def test_only_external_prefix_passes_authorization(self):
        for rel in self.CONFS:
            with self.subTest(conf=rel):
                blocks = self._locations(rel)
                self.assertIn("^~ /external/", blocks, f"{rel} 缺少 location ^~ /external/")
                self.assertNotIn("Authorization", blocks["^~ /external/"])
                self.assertIn("proxy_pass", blocks["^~ /external/"])
                self.assertIn("X-Edge-Secret", blocks["^~ /external/"], "external 也要覆寫 X-Edge-Secret")
                for loc, body in blocks.items():
                    if loc != "^~ /external/":
                        self.assertIn('proxy_set_header Authorization "";', body, f"{rel} 的 location {loc} 要清空")

if __name__ == "__main__":
    unittest.main()
