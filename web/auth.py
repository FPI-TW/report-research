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
