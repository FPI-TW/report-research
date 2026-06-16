# 廷豐研報 App 內建登入頁 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 以 App 內建、品牌化的登入頁(共用帳密 + hmac 簽章 session cookie)取代 nginx Basic Auth 彈窗,讓 LAN 與外網走同一道、唯一的認證門檻。

**Architecture:** 新增 `web/auth.py`(純函式:驗帳密、簽/驗 token、每 IP 失敗限流);在 `web/server.py` 掛一個 deny-by-default 的 HTTP middleware(白名單僅 `/login`)並新增 `/login`、`/logout` 路由;登入頁為自包式 HTML(不依賴 `/static`);移除 nginx 的 Basic Auth 與 `.htpasswd` 機制。

**Tech Stack:** FastAPI / Starlette、Python 3.11+、stdlib `hmac`/`hashlib`/`secrets`、`python-multipart`(表單)、stdlib `unittest` + `fastapi.testclient`(專案未裝 pytest)。

> **測試與格式約定(重要):** 本專案走「零工具鏈」路線,**未安裝 pytest / black / ruff**。測試一律以 stdlib `unittest` 執行:
> - 全部:`uv run python -m unittest discover -s tests -v`
> - 單一檔:`uv run python -m unittest discover -s tests -p "test_auth.py" -v`
>
> 不使用 `conftest.py`(pytest 專屬);測試所需環境變數改在各測試模組頂部、import `web.*` 之前以 `os.environ.setdefault` 設定。無 black/ruff 步驟,新程式請**手動對齊既有風格**(4 空格縮排、`from __future__ import annotations`、繁中註解)。

**參考 spec:** `docs/superpowers/specs/2026-06-15-app-login-page-design.md`

---

## 檔案結構

| 檔案 | 動作 | 職責 |
|------|------|------|
| `web/auth.py` | 新增 | 認證核心:設定載入(fail-closed)、`issue_token`/`verify_token`、`check_credentials`、cookie 設/清、每 IP 失敗限流、`client_ip` |
| `web/server.py` | 修改 | 掛 `require_login` middleware;新增 `GET /login`、`POST /login`、`POST /logout` |
| `web/static/login.html` | 新增 | 自包式品牌登入頁(帳號+密碼+錯誤訊息),內嵌樣式 |
| `web/static/utils.js` | 修改 | `fetchJSON` 遇 401 自動導向 `/login`(處理 session 過期) |
| `web/static/index.html` | 修改 | `.nav-links` 內加「登出」表單按鈕 + 兩條 CSS |
| `tests/test_auth.py` | 新增 | 認證單元測試 + TestClient 整合測試(模組頂部以 `setdefault` 設測試用環境變數) |
| `tests/test_server_startup.py` | 修改 | 探測點由 `/api/markets`(已被閘門擋)改為白名單 `/login` |
| `pyproject.toml` | 修改 | 加 `python-multipart` 相依 |
| `.env.example`(repo 根) | 新增 | 三個認證環境變數範本 |
| `.gitignore` | 修改 | 忽略 repo 根 `/.env` |
| `Makefile` | 修改 | `serve` 載入 `.env`;移除 `edge-passwd` 及其專用變數 |
| `deploy/nginx.conf` | 修改 | 移除 `auth_basic` 兩行 |
| `deploy/docker-compose.yml` | 修改 | 移除 `.htpasswd` 的 secrets 掛載 |
| `docs/EXTERNAL_ACCESS.md` | 修改 | 改述為 App 登入,移除 Basic Auth/.htpasswd 段落 |

**全域常數(整份計畫一致):** `COOKIE_NAME = "tf_session"`、`SESSION_TTL = 7*24*3600`、`MAX_FAILS = 5`、`FAIL_WINDOW = 300`。環境變數:`REPORT_MARK_ACCESS_USERNAME`、`REPORT_MARK_ACCESS_PASSWORD`、`REPORT_MARK_SESSION_SECRET`。

---

## Task 1: 認證核心模組 `web/auth.py`(TDD)

**Files:**
- Create: `web/auth.py`
- Test: `tests/test_auth.py`

- [ ] **Step 1: 寫失敗的單元測試 `tests/test_auth.py`**

不使用 `conftest.py`(pytest 專屬);改在模組頂部、`from web import auth` **之前**用 `setdefault` 設好認證環境變數(`web.auth` 匯入時即讀取,未設會 fail-closed 報錯)。

```python
# tests/test_auth.py
import os
import unittest

# web.auth 匯入時即讀取共用帳密(fail-closed),故須在匯入前設好測試用值。
os.environ.setdefault("REPORT_MARK_ACCESS_USERNAME", "tester")
os.environ.setdefault("REPORT_MARK_ACCESS_PASSWORD", "testpass")
os.environ.setdefault("REPORT_MARK_SESSION_SECRET", "fixed-test-secret-0123456789")

from web import auth  # noqa: E402


class TokenTests(unittest.TestCase):
    def test_valid_token_round_trips(self):
        now = 1_000_000
        tok = auth.issue_token(now)
        self.assertTrue(auth.verify_token(tok, now + 10))

    def test_expired_token_rejected(self):
        now = 1_000_000
        tok = auth.issue_token(now)
        self.assertFalse(auth.verify_token(tok, now + auth.SESSION_TTL + 1))

    def test_tampered_signature_rejected(self):
        now = 1_000_000
        tok = auth.issue_token(now)
        self.assertFalse(auth.verify_token(tok + "x", now + 10))

    def test_garbage_and_empty_token_rejected(self):
        self.assertFalse(auth.verify_token("not-a-token", 1_000_000))
        self.assertFalse(auth.verify_token("", 1_000_000))
        self.assertFalse(auth.verify_token(None, 1_000_000))


class CredentialTests(unittest.TestCase):
    def test_correct_credentials_accepted(self):
        self.assertTrue(auth.check_credentials("tester", "testpass"))

    def test_wrong_password_rejected(self):
        self.assertFalse(auth.check_credentials("tester", "nope"))

    def test_wrong_username_rejected(self):
        self.assertFalse(auth.check_credentials("nobody", "testpass"))

    def test_empty_credentials_rejected(self):
        self.assertFalse(auth.check_credentials("", ""))


class RateLimitTests(unittest.TestCase):
    def setUp(self):
        auth._FAILS.clear()

    def tearDown(self):
        auth._FAILS.clear()

    def test_locks_after_max_failures(self):
        now = 2_000_000
        ip = "1.2.3.4"
        for _ in range(auth.MAX_FAILS):
            self.assertFalse(auth.is_locked(ip, now))
            auth.record_failure(ip, now)
        self.assertTrue(auth.is_locked(ip, now))

    def test_reset_clears_lock(self):
        now = 2_000_000
        ip = "1.2.3.4"
        for _ in range(auth.MAX_FAILS):
            auth.record_failure(ip, now)
        auth.reset(ip)
        self.assertFalse(auth.is_locked(ip, now))

    def test_failures_age_out_of_window(self):
        ip = "1.2.3.4"
        start = 2_000_000
        for _ in range(auth.MAX_FAILS):
            auth.record_failure(ip, start)
        self.assertTrue(auth.is_locked(ip, start))
        # 視窗過後(全部老化)→ 解鎖
        self.assertFalse(auth.is_locked(ip, start + auth.FAIL_WINDOW + 1))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 執行測試,確認失敗**

Run: `uv run python -m unittest discover -s tests -p "test_auth.py" -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'web.auth'`

- [ ] **Step 3: 實作 `web/auth.py`**

```python
# web/auth.py
"""App 層登入認證:共用帳密驗證、hmac 簽章 session cookie、每 IP 失敗限流。

設定來自環境變數(沿用本專案 os.environ 慣例):
  REPORT_MARK_ACCESS_USERNAME / REPORT_MARK_ACCESS_PASSWORD  共用帳密(未設則 fail-closed 報錯)
  REPORT_MARK_SESSION_SECRET                                  cookie 簽章金鑰(未設則隨機,重啟登出所有人)
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import secrets

logger = logging.getLogger(__name__)

COOKIE_NAME = "tf_session"
SESSION_TTL = 7 * 24 * 3600  # 7 天;滑動到期由 middleware 每次回應刷新
MAX_FAILS = 5                # 視窗內允許的最大登入失敗次數
FAIL_WINDOW = 300            # 失敗計數視窗(秒)

_USERNAME = os.environ.get("REPORT_MARK_ACCESS_USERNAME", "")
_PASSWORD = os.environ.get("REPORT_MARK_ACCESS_PASSWORD", "")
if not _USERNAME or not _PASSWORD:
    raise RuntimeError(
        "REPORT_MARK_ACCESS_USERNAME 與 REPORT_MARK_ACCESS_PASSWORD 必須設定(fail-closed)"
    )

_SECRET = os.environ.get("REPORT_MARK_SESSION_SECRET", "")
if not _SECRET:
    _SECRET = secrets.token_hex(32)
    logger.warning("REPORT_MARK_SESSION_SECRET 未設定,已隨機產生(重啟將登出所有人)")


def _sign(msg: str) -> str:
    digest = hmac.new(_SECRET.encode(), msg.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def issue_token(now: int) -> str:
    """簽發 `<exp>.<sig>`;exp 為到期 Unix 秒。"""
    exp = now + SESSION_TTL
    return f"{exp}.{_sign(str(exp))}"


def verify_token(token: str | None, now: int) -> bool:
    """簽章正確且未過期才視為有效。"""
    if not token:
        return False
    try:
        exp_str, sig = token.split(".", 1)
        exp = int(exp_str)
    except (ValueError, AttributeError):
        return False
    if not hmac.compare_digest(sig, _sign(exp_str)):
        return False
    return exp > now


def check_credentials(username: str, password: str) -> bool:
    """常數時間比對帳號與密碼(先各算再 AND,不短路,避免時序側信道)。"""
    u_ok = hmac.compare_digest(username or "", _USERNAME)
    p_ok = hmac.compare_digest(password or "", _PASSWORD)
    return u_ok and p_ok


def set_session_cookie(response, now: int) -> None:
    response.set_cookie(
        COOKIE_NAME,
        issue_token(now),
        max_age=SESSION_TTL,
        httponly=True,
        samesite="lax",
        secure=False,  # LAN 走 HTTP;外網經 Cloudflare 仍是 HTTPS 加密傳輸
        path="/",
    )


def clear_session_cookie(response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")


# ───── 每 IP 失敗限流(in-memory,重啟即重置)─────
_FAILS: dict[str, list[int]] = {}


def _prune(ip: str, now: int) -> list[int]:
    fails = [t for t in _FAILS.get(ip, []) if t > now - FAIL_WINDOW]
    if fails:
        _FAILS[ip] = fails
    else:
        _FAILS.pop(ip, None)
    return fails


def is_locked(ip: str, now: int) -> bool:
    return len(_prune(ip, now)) >= MAX_FAILS


def record_failure(ip: str, now: int) -> None:
    fails = _prune(ip, now)
    fails.append(now)
    _FAILS[ip] = fails


def reset(ip: str) -> None:
    _FAILS.pop(ip, None)


def client_ip(request) -> str:
    """真實來源 IP:nginx 以 X-Real-IP 帶入還原後 IP;LAN 直連則用連線位址。
    外部無法偽造 X-Real-IP(nginx 以 $remote_addr 覆寫)。"""
    return request.headers.get("x-real-ip") or (
        request.client.host if request.client else "unknown"
    )
```

- [ ] **Step 4: 執行測試,確認通過**

Run: `uv run python -m unittest discover -s tests -p "test_auth.py" -v`
Expected: PASS(11 tests, OK)

- [ ] **Step 5: 提交**

新程式請手動對齊既有風格(4 空格縮排、繁中註解);專案無 black/ruff。

```bash
git add web/auth.py tests/test_auth.py
git commit -m "$(cat <<'EOF'
feat(auth): add session-cookie auth core for app login

Add web/auth.py: shared username+password check (constant-time),
hmac-signed session token with 7-day expiry, cookie set/clear helpers,
and a per-IP failed-login throttle. Config is read from env and is
fail-closed (refuses to import without username+password). Pure
functions, unit-tested independently of FastAPI.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: 把認證接進 App(middleware + 路由 + 登入頁)

**Files:**
- Modify: `pyproject.toml`(加 `python-multipart`)
- Modify: `web/server.py`
- Create: `web/static/login.html`
- Modify: `web/static/utils.js`
- Modify: `tests/test_server_startup.py`
- Test: `tests/test_auth.py`(追加整合測試)

- [ ] **Step 1: 加入 `python-multipart` 相依並同步**

`POST /login` 用 FastAPI `Form(...)` 解析表單,需要 `python-multipart`。
編輯 `pyproject.toml`,在 `dependencies` 陣列加入一行(放在 `"uvicorn>=0.48.0",` 之後):

```toml
    "uvicorn>=0.48.0",
    "python-multipart>=0.0.9",
```

Run: `uv sync`
Expected: 安裝 `python-multipart`,無錯誤。

- [ ] **Step 2: 寫失敗的整合測試**

說明:`TestClient(app)` **不使用** `with` 內容管理器 → 不觸發 lifespan 暖機(不會載入 BGE-M3、不連 DB);`follow_redirects=False` 以便斷言轉址狀態碼與 `Location`。只探測 `/`、`/login`、`/api/stats`(401 在路由前就被擋,不碰 DB)。

(2a) 在 `tests/test_auth.py` 頂部、`from web import auth  # noqa: E402` 那行**之後**新增兩行 import(env 已在檔案最上方設好,故此處 import `web.server` 不會 fail-closed):

```python
from fastapi.testclient import TestClient  # noqa: E402
from web.server import app  # noqa: E402
```

(2b) 在 `tests/test_auth.py` **結尾**、`if __name__ == "__main__":` 之前追加 `_client` 與 `AuthFlowTests`:

```python
def _client():
    return TestClient(app, follow_redirects=False)


class AuthFlowTests(unittest.TestCase):
    def setUp(self):
        auth._FAILS.clear()

    def tearDown(self):
        auth._FAILS.clear()

    def test_unauthed_html_redirects_to_login(self):
        r = _client().get("/")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.headers["location"], "/login")

    def test_unauthed_api_returns_401(self):
        r = _client().get("/api/stats")
        self.assertEqual(r.status_code, 401)

    def test_login_page_served_without_auth(self):
        r = _client().get("/login")
        self.assertEqual(r.status_code, 200)
        self.assertIn('name="username"', r.text)
        self.assertIn('name="password"', r.text)

    def test_wrong_credentials_redirect_with_error(self):
        r = _client().post("/login", data={"username": "tester", "password": "bad"})
        self.assertEqual(r.status_code, 303)
        self.assertIn("error=1", r.headers["location"])
        self.assertNotIn(auth.COOKIE_NAME, r.cookies)

    def test_correct_credentials_set_cookie_and_grant_access(self):
        client = _client()
        r = client.post("/login", data={"username": "tester", "password": "testpass"})
        self.assertEqual(r.status_code, 303)
        self.assertEqual(r.headers["location"], "/")
        self.assertIn(auth.COOKIE_NAME, r.cookies)
        # 帶著 cookie(client 的 cookie jar 已保存)再取首頁 → 200
        r2 = client.get("/")
        self.assertEqual(r2.status_code, 200)

    def test_logout_clears_session(self):
        client = _client()
        client.post("/login", data={"username": "tester", "password": "testpass"})
        client.get("/")  # 確認已登入
        client.post("/logout")
        r = client.get("/")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.headers["location"], "/login")

    def test_lockout_after_repeated_failures(self):
        client = _client()
        for _ in range(auth.MAX_FAILS):
            client.post("/login", data={"username": "tester", "password": "bad"})
        r = client.post("/login", data={"username": "tester", "password": "bad"})
        self.assertEqual(r.status_code, 303)
        self.assertIn("error=locked", r.headers["location"])
```

- [ ] **Step 3: 執行測試,確認失敗**

Run: `uv run python -m unittest discover -s tests -p "test_auth.py" -v`
Expected: FAIL — `AuthFlowTests` 各項斷言失敗:此時 `web.server` 尚無 middleware 與 `/login` 路由,未登入取 `/` 仍回 200、取 `/login` 為 404。(Task 1 的 `TokenTests`/`CredentialTests`/`RateLimitTests` 仍綠。)

- [ ] **Step 4: 在 `web/server.py` 加入 import、middleware 與路由**

(4a) 在現有 import 區(`from pathlib import Path` 之後、`from fastapi import ...` 之前或附近)補上:

```python
import time
```

並把現有的 `from fastapi import FastAPI, HTTPException, Query` 改為:

```python
from fastapi import FastAPI, Form, HTTPException, Query, Request
```

在 `from fastapi.responses import FileResponse` 改為:

```python
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
```

在 `from app.services...` 區塊之後(檔案頂部 import 群尾端)加入:

```python
from web import auth  # noqa: E402
```

(4b) 在 `app = FastAPI(title="研報市場標籤檢索", lifespan=lifespan)` 這行**之後**緊接加入 middleware:

```python
# ───── 認證閘門(deny-by-default;白名單僅 /login)─────
_AUTH_ALLOWLIST = {"/login"}


@app.middleware("http")
async def require_login(request: Request, call_next):
    path = request.url.path
    if path in _AUTH_ALLOWLIST:
        return await call_next(request)
    now = int(time.time())
    if auth.verify_token(request.cookies.get(auth.COOKIE_NAME), now):
        response = await call_next(request)
        if path != "/logout":  # 登出會清 cookie,勿在此又刷新蓋回
            auth.set_session_cookie(response, now)
        return response
    if path.startswith("/api/"):
        return JSONResponse({"detail": "未登入"}, status_code=401)
    return RedirectResponse("/login", status_code=302)
```

(4c) 在 `@app.get("/")`(現有首頁路由)**之前**加入登入/登出路由:

```python
@app.get("/login")
async def login_page(request: Request):
    if auth.verify_token(request.cookies.get(auth.COOKIE_NAME), int(time.time())):
        return RedirectResponse("/", status_code=302)
    return _static_page("login.html")


@app.post("/login")
async def login_submit(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
):
    now = int(time.time())
    ip = auth.client_ip(request)
    if auth.is_locked(ip, now):
        return RedirectResponse("/login?error=locked", status_code=303)
    if auth.check_credentials(username, password):
        auth.reset(ip)
        resp = RedirectResponse("/", status_code=303)
        auth.set_session_cookie(resp, now)
        return resp
    auth.record_failure(ip, now)
    return RedirectResponse("/login?error=1", status_code=303)


@app.post("/logout")
async def logout():
    resp = RedirectResponse("/login", status_code=303)
    auth.clear_session_cookie(resp)
    return resp
```

- [ ] **Step 5: 建立 `web/static/login.html`(自包式,不依賴 /static)**

```html
<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>登入 · 廷豐研報</title>
<style>
  :root {
    --blue: #007aff;
    --bg-grad-1: #f7f8fa;
    --bg-grad-2: #eceef3;
    --card: #fff;
    --label: #1c1c1e;
    --label-3: #3c3c4399;
    --sep: #3c3c431f;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI",
                 "PingFang TC", "Microsoft JhengHei", system-ui, sans-serif;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; min-height: 100vh; display: flex;
    align-items: center; justify-content: center;
    background: linear-gradient(160deg, var(--bg-grad-1), var(--bg-grad-2));
    color: var(--label);
  }
  .card {
    width: 100%; max-width: 360px; margin: 24px;
    background: var(--card); border-radius: 16px; padding: 32px 28px;
    box-shadow: 0 1px 2px rgba(0,0,0,.04), 0 8px 24px rgba(0,0,0,.06);
  }
  h1 { margin: 0 0 4px; font-size: 24px; }
  h1 .accent { color: var(--blue); }
  .sub { margin: 0 0 20px; font-size: 13px; color: var(--label-3); }
  label { display: block; font-size: 13px; margin: 14px 0 6px; }
  input {
    width: 100%; padding: 11px 12px; font-size: 15px;
    border: 1px solid var(--sep); border-radius: 10px; background: #fff;
  }
  input:focus { outline: 2px solid var(--blue); outline-offset: 0; border-color: transparent; }
  button {
    width: 100%; margin-top: 22px; padding: 12px; font-size: 15px;
    font-weight: 600; color: #fff; background: var(--blue);
    border: none; border-radius: 10px; cursor: pointer;
  }
  button:hover { filter: brightness(.96); }
  .error {
    display: none; margin-top: 16px; padding: 10px 12px; font-size: 13px;
    color: #b00020; background: #b000200f; border-radius: 8px;
  }
</style>
</head>
<body>
  <main class="card">
    <h1>廷豐<span class="accent">研報</span></h1>
    <p class="sub">請登入以使用研究報告檢索</p>
    <form method="post" action="/login" autocomplete="on">
      <label for="username">帳號</label>
      <input id="username" name="username" type="text" autocomplete="username" autofocus required>
      <label for="password">密碼</label>
      <input id="password" name="password" type="password" autocomplete="current-password" required>
      <button type="submit">登入</button>
    </form>
    <p class="error" id="error"></p>
  </main>
  <script>
    var p = new URLSearchParams(location.search);
    if (p.has("error")) {
      var el = document.getElementById("error");
      el.textContent = p.get("error") === "locked"
        ? "嘗試次數過多,請稍後再試。"
        : "帳號或密碼錯誤。";
      el.style.display = "block";
    }
  </script>
</body>
</html>
```

- [ ] **Step 6: `web/static/utils.js` — `fetchJSON` 遇 401 導向登入頁**

把 `fetchJSON` 內這段:

```javascript
    const r = await fetch(url, init);
    if (!r.ok) throw new Error("HTTP " + r.status);
```

改為:

```javascript
    const r = await fetch(url, init);
    if (r.status === 401) { window.location.href = "/login"; throw new Error("unauthorized"); }
    if (!r.ok) throw new Error("HTTP " + r.status);
```

- [ ] **Step 7: 修正既有 `tests/test_server_startup.py`(設環境變數 + 探測點改白名單 `/login`)**

(7a) 加上閘門後 `web.server` 會 import `web.auth`(fail-closed)。在檔案最上方(第 1 行 `import threading` 之前)插入環境變數 `setdefault`,確保單獨執行本檔時也能 import:

```python
import os

os.environ.setdefault("REPORT_MARK_ACCESS_USERNAME", "tester")
os.environ.setdefault("REPORT_MARK_ACCESS_PASSWORD", "testpass")
os.environ.setdefault("REPORT_MARK_SESSION_SECRET", "fixed-test-secret-0123456789")
```

(7b) `/api/markets` 加上閘門後未登入會被擋;此測試只在乎「暖機期間伺服器仍可回應」,改打白名單 `/login` 即可保留原意。把第 35 行:

```python
                    response = client.get("/api/markets")
```

改為:

```python
                    response = client.get("/login")
```

- [ ] **Step 8: 執行全部測試,確認通過**

Run: `uv run python -m unittest discover -s tests -v`
Expected: PASS(Task 1 的 11 項 + AuthFlow 7 項 + test_server_startup 1 項,共 19 tests,OK)

- [ ] **Step 9: 提交**

新程式請手動對齊既有風格;專案無 black/ruff。

```bash
git add pyproject.toml uv.lock web/server.py web/static/login.html web/static/utils.js tests/test_auth.py tests/test_server_startup.py
git commit -m "$(cat <<'EOF'
feat(auth): gate the app behind an in-app login page

Add a deny-by-default HTTP middleware (allowlist: /login only) plus
GET/POST /login and POST /logout routes. The login page is a
self-contained branded HTML form (no /static dependency). fetchJSON
redirects to /login on 401 so expired sessions recover gracefully.
Add python-multipart for form parsing. Re-point the warmup smoke test
at the allowlisted /login endpoint.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: 環境變數供給(讓 `make serve` 可跑)

接上 Task 2 後,`web.auth` 匯入即要求帳密,否則 App 拒絕啟動。本任務提供本機 `.env` 機制。

**Files:**
- Create: `.env.example`(repo 根)
- Modify: `.gitignore`
- Modify: `Makefile`(`serve` 載入 `.env`)

- [ ] **Step 1: 建立 repo 根 `.env.example`**

```bash
# 廷豐研報 App 登入帳密與 session 簽章金鑰
# 複製本檔為 .env(已 gitignore),填入實際值。
# 未設 USERNAME / PASSWORD 時 App 會拒絕啟動(fail-closed)。
REPORT_MARK_ACCESS_USERNAME=tingfeng
REPORT_MARK_ACCESS_PASSWORD=請改成你的密碼

# 固定一組長隨機字串;未設則每次重啟都會把所有人登出。
# 產生:python -c "import secrets; print(secrets.token_hex(32))"
REPORT_MARK_SESSION_SECRET=請填入長隨機字串

# (選用)DB 連線;不設則用預設 localhost:5436
# REPORT_MARK_DB_URL=postgresql+asyncpg://postgres:postgres@localhost:5436/research
```

- [ ] **Step 2: `.gitignore` 加入 repo 根 `/.env`**

在檔案結尾加入:

```gitignore

# App 登入帳密 / session 金鑰(本機環境設定)
/.env
```

- [ ] **Step 3: `Makefile` 的 `serve` 載入 `.env`**

把現有的:

```makefile
serve:  ## 啟動查詢網頁（BGE-M3 常駐）→ http://localhost:$(PORT)
	uv run uvicorn web.server:app --host 0.0.0.0 --port $(PORT)
```

改為(載入 repo 根 `.env`,存在才載;以 `set -a` 自動 export):

```makefile
serve:  ## 啟動查詢網頁（BGE-M3 常駐）→ http://localhost:$(PORT)
	@set -a; [ -f .env ] && . ./.env; set +a; \
	  uv run uvicorn web.server:app --host 0.0.0.0 --port $(PORT)
```

- [ ] **Step 4: 驗證 fail-closed 與正常啟動**

```bash
# 無 .env、無 env → 應報錯拒啟(預期看到 RuntimeError 訊息後退出)
env -u REPORT_MARK_ACCESS_USERNAME -u REPORT_MARK_ACCESS_PASSWORD \
  uv run python -c "import web.auth" ; echo "exit=$?"
```
Expected: 印出 `RuntimeError: REPORT_MARK_ACCESS_USERNAME 與 REPORT_MARK_ACCESS_PASSWORD 必須設定(fail-closed)`,`exit=1`。

```bash
# 設好後可正常匯入
cp .env.example .env   # 編輯填入實際密碼與 SESSION_SECRET
REPORT_MARK_ACCESS_USERNAME=x REPORT_MARK_ACCESS_PASSWORD=y \
  uv run python -c "import web.auth; print('ok')"
```
Expected: 印出 `ok`。

- [ ] **Step 5: 提交**

```bash
git add .env.example .gitignore Makefile
git commit -m "$(cat <<'EOF'
chore(auth): provision login env vars and load .env in make serve

Add a repo-root .env.example for the three auth env vars, gitignore the
local .env, and have `make serve` source .env (auto-exported) so the
fail-closed app can start locally without manual exports.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: 首頁加「登出」連結

**Files:**
- Modify: `web/static/index.html`

monitor.html / help.html 皆有「回到廷豐研報」連結,從首頁即可登出,故只需改首頁(YAGNI)。

- [ ] **Step 1: 加兩條 CSS**

在 `index.html` 的 `.monitor-link svg { flex: none; }`(第 51 行)**之後**加入:

```css
  .logout-form { margin: 0; display: inline-flex; }
  .logout-btn { border: none; cursor: pointer; font-family: inherit; }
```

- [ ] **Step 2: 在 `.nav-links` 內加登出表單按鈕**

把「導入監控」連結後、`</span>`(`.nav-links` 結尾)之前這段:

```html
            <a class="monitor-link" href="/monitor" title="導入監控">
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                   stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12h4l2.5 6 4-13L16 12h5"/></svg>
              導入監控
            </a>
          </span>
```

改為(在 `</a>` 與 `</span>` 之間插入登出表單):

```html
            <a class="monitor-link" href="/monitor" title="導入監控">
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                   stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12h4l2.5 6 4-13L16 12h5"/></svg>
              導入監控
            </a>
            <form class="logout-form" method="post" action="/logout">
              <button class="monitor-link logout-btn" type="submit" title="登出">
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                     stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><path d="M16 17l5-5-5-5"/><path d="M21 12H9"/></svg>
                登出
              </button>
            </form>
          </span>
```

- [ ] **Step 3: 手動驗證(目視)**

啟動 `make serve`,登入後在首頁右上 nav 應看到「登出」鈕,外觀與「使用說明 / 導入監控」一致;點擊後導回 `/login`。

- [ ] **Step 4: 提交**

```bash
git add web/static/index.html
git commit -m "$(cat <<'EOF'
feat(auth): add logout control to the home page nav

Add a "登出" submit button (POST /logout) in the index nav-links,
styled to match the existing nav pills.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: 移除 nginx Basic Auth 機制

**Files:**
- Modify: `deploy/nginx.conf`
- Modify: `deploy/docker-compose.yml`
- Modify: `Makefile`

- [ ] **Step 1: `deploy/nginx.conf` 移除 Basic Auth 兩行**

刪除這兩行(第 27-29 行附近):

```nginx
    # Basic Auth 共用密碼門檻
    auth_basic "tingfeng-research";
    auth_basic_user_file /etc/nginx/secrets/.htpasswd;
```

(保留 `real_ip`、安全標頭、`limit_req`、`proxy_set_header Authorization "";` — 後者無害且仍能避免任何 Authorization 外流。)

- [ ] **Step 2: `deploy/docker-compose.yml` 移除 secrets 掛載**

把 nginx 服務的 volumes:

```yaml
    volumes:
      - ./nginx.conf:/etc/nginx/conf.d/default.conf:ro
      - ./secrets:/etc/nginx/secrets:ro
```

改為:

```yaml
    volumes:
      - ./nginx.conf:/etc/nginx/conf.d/default.conf:ro
```

並把檔案頂部用法註解(第 2 行):

```yaml
# 用法：先 `make edge-passwd` 設定 Basic Auth，填好 deploy/.env 的 TUNNEL_TOKEN，再 `make up-edge`
```

改為:

```yaml
# 用法：填好 deploy/.env 的 TUNNEL_TOKEN，再 `make up-edge`（登入改由 App 處理，邊緣不再設 Basic Auth）
```

- [ ] **Step 3: `Makefile` 移除 `edge-passwd` 及其專用變數**

(3a) 刪除第 12 行 `EDGE_USER ?= tingfeng`。

(3b) 刪除 `SECRETS_MOUNT` 整段(第 18-23 行):

```makefile
# docker.exe 的 -v 掛載需 Windows 路徑（C:/...）；原生 docker 用一般路徑
ifeq ($(DOCKER),docker.exe)
SECRETS_MOUNT := $(shell wslpath -m "$(CURDIR)/deploy/secrets")
else
SECRETS_MOUNT := $(CURDIR)/deploy/secrets
endif
```

(3c) `.PHONY` 行移除 `edge-passwd`:

```makefile
        edge-passwd up-edge down-edge edge-logs edge-reload
```

改為:

```makefile
        up-edge down-edge edge-logs edge-reload
```

(3d) 刪除 `edge-passwd` target 整段(第 101-105 行):

```makefile
edge-passwd:  ## 設定/更換對外 Basic Auth 共用密碼（覆蓋舊密碼；需互動終端輸入兩次）
	@test -t 0 || { echo "edge-passwd 需在互動終端執行（stdin 必須是 TTY）"; exit 1; }
	@mkdir -p deploy/secrets
	$(DOCKER) run --rm -it -v "$(SECRETS_MOUNT):/secrets" httpd:alpine \
	  htpasswd -B -c /secrets/.htpasswd $(EDGE_USER)
```

(3e) `up-edge` 移除 `.htpasswd` 前置檢查(第 108 行):

```makefile
	@test -f deploy/secrets/.htpasswd || { echo "缺少 deploy/secrets/.htpasswd，請先執行 make edge-passwd"; exit 1; }
```

刪除該行;`up-edge` 保留 `deploy/.env` 與 `TUNNEL_TOKEN` 兩項檢查。

- [ ] **Step 4: 驗證設定無殘留、語法正確**

```bash
grep -rn "auth_basic\|htpasswd\|edge-passwd\|SECRETS_MOUNT\|EDGE_USER" deploy/ Makefile
```
Expected: 無任何輸出(全部清除)。

```bash
make help
```
Expected: 指令列表不再出現 `edge-passwd`,其餘 edge 指令(`up-edge`/`down-edge`/`edge-logs`/`edge-reload`)仍在,無 Makefile 解析錯誤。

- [ ] **Step 5: 提交**

```bash
git add deploy/nginx.conf deploy/docker-compose.yml Makefile
git commit -m "$(cat <<'EOF'
refactor(deploy): drop nginx Basic Auth in favour of app login

The app now owns authentication, so remove the edge Basic Auth: delete
auth_basic from nginx.conf, the .htpasswd secrets mount from compose,
and the edge-passwd target plus its EDGE_USER/SECRETS_MOUNT vars and the
up-edge .htpasswd precheck from the Makefile.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: 更新 `docs/EXTERNAL_ACCESS.md`

**Files:**
- Modify: `docs/EXTERNAL_ACCESS.md`

- [ ] **Step 1: 開頭與架構圖(第 3、11、17 行)**

第 3 行:

```markdown
讓辦公室外的同事從外網連入廷豐研報檢索網頁，並以共用密碼（Basic Auth）設門檻。
```
→
```markdown
讓辦公室外的同事從外網連入廷豐研報檢索網頁。登入由 App 內建登入頁處理（共用帳密），邊緣 nginx 只做反向代理與限流。
```

第 11 行(架構圖內):
```
        nginx（Basic Auth + 安全標頭 + 限流）
```
→
```
        nginx（安全標頭 + 限流）
```

第 17 行:
```markdown
對外唯一路徑是 Cloudflare → 隧道 → nginx(Basic Auth) → uvicorn。origin 不對公網開任何埠；外網路徑與既有 LAN portproxy/防火牆互不影響，也不受 WSL 重開機換 IP 影響。
```
→
```markdown
對外唯一路徑是 Cloudflare → 隧道 → nginx → uvicorn（App 登入把關）。origin 不對公網開任何埠；外網路徑與既有 LAN portproxy/防火牆互不影響，也不受 WSL 重開機換 IP 影響。
```

- [ ] **Step 2: 「3) 設定 Basic Auth 共用密碼」整段(第 46-53 行)改為設定登入帳密**

把:

```markdown
### 3) 設定 Basic Auth 共用密碼

```bash
make edge-passwd          # 互動輸入密碼兩次（帳號預設 tingfeng）
# 想自訂帳號：make edge-passwd EDGE_USER=yourname
```

> `make edge-passwd` 需在**互動終端**執行（會提示輸入密碼兩次），且每次執行都會**覆蓋**舊的共用密碼。
```

整段替換為:

```markdown
### 3) 設定 App 登入帳密

登入由 App 處理,憑證來自環境變數(複製 repo 根 `.env.example` 為 `.env`):

```bash
cp .env.example .env
# 編輯 .env：
#   REPORT_MARK_ACCESS_USERNAME / REPORT_MARK_ACCESS_PASSWORD  共用帳密
#   REPORT_MARK_SESSION_SECRET  固定長隨機字串(未設則重啟登出所有人)
#   產生 secret：python -c "import secrets; print(secrets.token_hex(32))"
```

> `make serve` 會自動載入 `.env`。未設帳密時 App 會 fail-closed 拒絕啟動。
```

- [ ] **Step 3: 「5) 外網實測」(第 68-69 行)**

```markdown
- 應先跳 Basic Auth 帳密視窗；
- 輸入正確帳密後可正常檢索研報。
```
→
```markdown
- 應看到廷豐研報的登入頁；
- 輸入正確帳密後導向首頁,可正常檢索研報。
```

- [ ] **Step 4: 日常維運表(第 78 行)**

```markdown
| 換共用密碼 | `make edge-passwd`（改 `.htpasswd` 即時生效；如要保險可 `make edge-reload`） |
```
→
```markdown
| 換登入帳密 | 編輯 `.env` 的帳密 → 重啟 `make serve` |
```

- [ ] **Step 5: 安全備註(第 85、87 行)**

第 85 行:
```markdown
- Basic Auth 為**共用密碼**，透過 Cloudflare TLS 加密傳輸（非明文）。請定期 `make edge-passwd` 輪替。
```
→
```markdown
- 登入為**共用帳密**,App 以 hmac 簽章 session cookie 維持登入(7 天滑動到期),並對登入失敗做每 IP 限流。請定期更換 `.env` 的密碼。
```

第 87 行:
```markdown
- `deploy/.env`（token）與 `deploy/secrets/.htpasswd`（帳密）皆已 gitignore，切勿提交。
```
→
```markdown
- `deploy/.env`（隧道 token）與 repo 根 `.env`（登入帳密 / session 金鑰）皆已 gitignore，切勿提交。
```

- [ ] **Step 6: 疑難排解表(第 94-95 行)**

把這兩列:

```markdown
| 一直跳帳密、輸入正確仍進不去 | htpasswd 沒設或帳號不符 → 重跑 `make edge-passwd`，必要時 `make edge-reload` |
| 開站回 500（非 401） | `deploy/secrets/.htpasswd` 不存在就啟動了 → 先 `make edge-passwd` 再 `make up-edge`（`up-edge` 已內建此守門） |
```

替換為:

```markdown
| 一直回登入頁、輸入正確仍進不去 | session cookie 沒被接受(瀏覽器擋第三方/封鎖 cookie),或 `REPORT_MARK_SESSION_SECRET` 每次重啟都變(請在 `.env` 固定一組) |
| App 啟動即報錯退出 | 未設 `REPORT_MARK_ACCESS_USERNAME` / `_ACCESS_PASSWORD`(fail-closed)→ 補進 `.env` 再 `make serve` |
```

- [ ] **Step 7: 驗證無殘留並提交**

```bash
grep -n "Basic Auth\|htpasswd\|edge-passwd" docs/EXTERNAL_ACCESS.md
```
Expected: 無輸出。

```bash
git add docs/EXTERNAL_ACCESS.md
git commit -m "$(cat <<'EOF'
docs: update external-access guide for app login

Replace the nginx Basic Auth / .htpasswd setup with the app-level login
flow: env-var credentials in repo-root .env, make serve auto-loads it,
and refreshed verification, maintenance, and troubleshooting notes.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## 最終驗證(全部任務完成後)

- [ ] `uv run python -m unittest discover -s tests -v` — 全綠(auth 單元 + 整合 + 啟動 smoke,共 19 tests)。
- [ ] `grep -rn "auth_basic\|htpasswd\|edge-passwd" deploy/ Makefile docs/` — 無輸出。
- [ ] 手動端到端:`cp .env.example .env`(填值)→ `make serve` →
  - 未登入開 `http://localhost:8097/` → 導向 `/login`;
  - 輸入錯帳密 → 顯示「帳號或密碼錯誤」;連續 5 次 → 「嘗試次數過多」;
  - 正確帳密 → 進首頁可檢索;右上「登出」→ 回 `/login`。
- [ ] (選用,需 Docker)`make up-edge` 後外網開站直接看到 App 登入頁(不再有瀏覽器彈窗)。

## 安全與相容性備註

- App session 成為**唯一**門檻:`secure=False`(因 LAN 走 HTTP),外網經 Cloudflare 仍 HTTPS;`HttpOnly` + `SameSite=Lax` 已設。
- 限流為 in-memory,程序重啟即重置(可接受;非持久化封鎖)。
- 既有 API 回應格式不變,僅多了 401(未登入)與登入頁;前端 `fetchJSON` 已處理 401。
