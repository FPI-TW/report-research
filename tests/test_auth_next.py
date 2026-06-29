import time

from fastapi.testclient import TestClient

from web import auth
from web.server import _safe_next, app


def test_safe_next_accepts_same_origin_relative():
    assert _safe_next("/app/monitor") == "/app/monitor"
    assert _safe_next("/app/monitor?x=1") == "/app/monitor?x=1"


def test_safe_next_rejects_open_redirects():
    assert _safe_next("//evil.com") == "/"
    assert _safe_next("/\\evil.com") == "/"
    assert _safe_next("https://evil.com") == "/"
    assert _safe_next("evil") == "/"
    assert _safe_next(None) == "/"


def test_unauthed_app_deeplink_redirects_with_next():
    client = TestClient(app)
    resp = client.get("/app/monitor", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/login?next=%2Fapp%2Fmonitor"


def test_login_post_honors_safe_next(monkeypatch):
    monkeypatch.setattr(auth, "check_credentials", lambda u, p: True)
    monkeypatch.setattr(auth, "login_allowed", lambda r: True)
    client = TestClient(app)
    resp = client.post(
        "/login",
        data={"username": "x", "password": "y", "next": "/app/monitor"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/app/monitor"


def test_login_post_ignores_evil_next(monkeypatch):
    monkeypatch.setattr(auth, "check_credentials", lambda u, p: True)
    monkeypatch.setattr(auth, "login_allowed", lambda r: True)
    client = TestClient(app)
    resp = client.post(
        "/login",
        data={"username": "x", "password": "y", "next": "https://evil.com"},
        follow_redirects=False,
    )
    assert resp.headers["location"] == "/"
