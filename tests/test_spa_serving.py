import time

from fastapi.testclient import TestClient

from web import auth
from web.server import app


def _auth_cookies() -> dict[str, str]:
    return {auth.COOKIE_NAME: auth.issue_token(int(time.time()))}


def test_app_deeplink_serves_spa_shell_when_authed():
    client = TestClient(app)
    resp = client.get("/app/monitor", cookies=_auth_cookies())
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert resp.headers.get("cache-control") == "no-cache"
    assert '<div id="root">' in resp.text


def test_app_shell_requires_auth():
    client = TestClient(app)
    resp = client.get("/app/monitor", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"].startswith("/login")
