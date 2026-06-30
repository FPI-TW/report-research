import os
import time

import pytest
from fastapi.testclient import TestClient

from web import auth
from web.server import SPA_DIST, app


def _auth_cookies() -> dict[str, str]:
    return {auth.COOKIE_NAME: auth.issue_token(int(time.time()))}


def _require_spa_dist() -> None:
    """clean checkout 未跑 make spa-build 時 dist 不存在，shell 端點會 503；跳過而非偽紅。"""
    if not (SPA_DIST / "index.html").is_file():
        pytest.skip("frontend/dist/index.html 不存在，需先執行 make spa-build")


def test_app_deeplink_serves_spa_shell_when_authed():
    _require_spa_dist()
    client = TestClient(app, cookies=_auth_cookies())
    resp = client.get("/app/monitor")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert resp.headers.get("cache-control") == "no-cache"
    assert '<div id="root">' in resp.text


def test_app_shell_requires_auth():
    client = TestClient(app)
    resp = client.get("/app/monitor", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"].startswith("/login")


def test_legacy_monitor_redirects_to_spa():
    client = TestClient(app, cookies=_auth_cookies())
    resp = client.get("/monitor", follow_redirects=False)
    assert resp.status_code == 307
    assert resp.headers["location"] == "/app/monitor"


def test_spa_assets_have_immutable_cache():
    assets_dir = SPA_DIST / "assets"
    if not assets_dir.is_dir():
        pytest.skip("frontend/dist/assets 不存在，需先執行 make spa-build")
    files = os.listdir(assets_dir)
    if not files:
        pytest.skip("frontend/dist/assets 目錄為空")
    asset_file = files[0]
    client = TestClient(app, cookies=_auth_cookies())
    resp = client.get(f"/app/assets/{asset_file}")
    assert resp.status_code == 200
    cc = resp.headers.get("cache-control", "")
    assert "immutable" in cc
    assert "max-age=31536000" in cc


def test_spa_shell_cache_is_no_cache():
    _require_spa_dist()
    client = TestClient(app, cookies=_auth_cookies())
    resp = client.get("/app/monitor")
    assert resp.status_code == 200
    assert resp.headers.get("cache-control") == "no-cache"
