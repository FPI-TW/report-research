from fake_accounts import FakeAccounts, install, session_cookies
from fastapi.testclient import TestClient

from web import auth
from web.server import _safe_next, app


def test_safe_next_accepts_same_origin_relative():
    assert _safe_next("/monitor") == "/monitor"
    assert _safe_next("/monitor?x=1") == "/monitor?x=1"


def test_safe_next_rejects_open_redirects():
    assert _safe_next("//evil.com") == "/"
    assert _safe_next("/\\evil.com") == "/"
    assert _safe_next("https://evil.com") == "/"
    assert _safe_next("evil") == "/"
    assert _safe_next(None) == "/"
    # CRLF injection guard
    assert _safe_next("/foo\r\nX-Injected: 1") == "/"
    # 其餘 C0 控制字元 / DEL 一律拒絕（縱深防禦）
    assert _safe_next("/mon\titor") == "/"
    assert _safe_next("/mon\x00itor") == "/"
    assert _safe_next("/mon\x7fitor") == "/"
    # javascript: scheme guard
    assert _safe_next("javascript:alert(1)") == "/"


def test_unauthed_page_redirects_to_plain_login():
    # vanilla 頁面未登入一律導回 /login（不帶 next；SPA 移除後恢復原行為）
    client = TestClient(app)
    resp = client.get("/monitor", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/login"


def _store_with_x() -> FakeAccounts:
    store = FakeAccounts()
    store.add_user("x", "y-password", "user")
    return store


def test_login_post_honors_safe_next(monkeypatch):
    monkeypatch.setattr(auth, "login_allowed", lambda r: True)
    with install(_store_with_x()):
        client = TestClient(app)
        resp = client.post(
            "/login",
            data={"username": "x", "password": "y-password", "next": "/monitor"},
            follow_redirects=False,
        )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/monitor"


def test_login_post_ignores_evil_next(monkeypatch):
    monkeypatch.setattr(auth, "login_allowed", lambda r: True)
    with install(_store_with_x()):
        client = TestClient(app)
        resp = client.post(
            "/login",
            data={"username": "x", "password": "y-password", "next": "https://evil.com"},
            follow_redirects=False,
        )
    assert resp.headers["location"] == "/"


def test_authed_login_get_honors_safe_next():
    # 用真 session（假帳號庫裡真的開一個）而非 stub 掉驗證：middleware 與 /login 是兩個
    # 不同的驗證呼叫點，stub 其中一個只會在下次改認證時無聲失準（本檔曾因此紅）。
    client = TestClient(app, cookies=_auth_cookies())
    # valid same-origin next: should redirect there
    resp = client.get("/login?next=/monitor", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/monitor"
    # evil next: should redirect to /
    resp2 = client.get("/login?next=https://evil.com", follow_redirects=False)
    assert resp2.status_code == 302
    assert resp2.headers["location"] == "/"


def test_authed_root_redirects_to_spa():
    # 舊 vanilla 首頁已退場：授權後根路徑導向 SPA 檢索頁
    client = TestClient(app, cookies=_auth_cookies(), follow_redirects=False)
    resp = client.get("/")
    assert resp.status_code == 302
    assert resp.headers["location"] == "/app/search"


def _auth_cookies() -> dict[str, str]:
    return session_cookies()


def test_authed_monitor_redirects_to_spa():
    # 舊 vanilla 監控頁已退場：導向 SPA 監控頁
    client = TestClient(app, cookies=_auth_cookies())
    resp = client.get("/monitor", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/app/monitor"


def test_authed_help_redirects_to_spa():
    # 舊 vanilla 說明頁已退場：導向 SPA 說明頁
    client = TestClient(app, cookies=_auth_cookies())
    resp = client.get("/help", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/app/help"
