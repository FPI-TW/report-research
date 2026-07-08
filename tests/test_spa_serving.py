import time
import unittest

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request
from starlette.responses import Response

from web import auth
from web.server import SPA_DIST, app, require_login


def _auth_cookies() -> dict[str, str]:
    return {auth.COOKIE_NAME: auth.issue_token(int(time.time()))}


def test_unauthed_app_redirects_to_login():
    client = TestClient(app)
    resp = client.get("/app/search", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/login"


def test_authed_app_deeplink_serves_shell():
    if not (SPA_DIST / "index.html").is_file():
        pytest.skip("frontend/dist not built")
    client = TestClient(app, cookies=_auth_cookies())
    resp = client.get("/app/ask", follow_redirects=False)
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert resp.headers.get("cache-control") == "no-cache"


class SpaAssetAuthTests(unittest.IsolatedAsyncioTestCase):
    async def test_require_login_allows_spa_assets_without_auth(self):
        seen = {"called": False}

        async def call_next(_request: Request):
            seen["called"] = True
            return Response("missing", status_code=404, media_type="text/plain")

        req = Request({
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/app/assets/chunk.js",
            "raw_path": b"/app/assets/chunk.js",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
        })

        resp = await require_login(req, call_next)

        self.assertTrue(seen["called"])
        self.assertEqual(resp.status_code, 404)
