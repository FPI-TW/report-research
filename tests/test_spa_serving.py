import time

import pytest
from fastapi.testclient import TestClient

from web import auth
from web.server import SPA_DIST, app


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
