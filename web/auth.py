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
import ipaddress
import logging
import os
import secrets

logger = logging.getLogger(__name__)

COOKIE_NAME = "tf_session"
SESSION_TTL = 7 * 24 * 3600  # 7 天;滑動到期由 middleware 每次回應刷新
MAX_FAILS = 5                # 視窗內允許的最大登入失敗次數
FAIL_WINDOW = 300            # 失敗計數視窗(秒)
MAX_TRACKED_IPS = max(1, int(os.environ.get("REPORT_MARK_MAX_TRACKED_FAIL_IPS", "4096")))

_USERNAME = os.environ.get("REPORT_MARK_ACCESS_USERNAME", "")
_PASSWORD = os.environ.get("REPORT_MARK_ACCESS_PASSWORD", "")
if not _USERNAME or not _PASSWORD:
    raise RuntimeError(
        "REPORT_MARK_ACCESS_USERNAME 與 REPORT_MARK_ACCESS_PASSWORD 必須設定(fail-closed)"
    )

_USERNAME_B = _USERNAME.encode()
_PASSWORD_B = _PASSWORD.encode()

# 共用帳號名稱（公開常數）：供前端在側欄底部顯示「目前登入帳號」。
# 本專案為單一共用帳號，session cookie 不帶個別身分，故此值對所有人相同。
ACCESS_USERNAME = _USERNAME

_SECRET = os.environ.get("REPORT_MARK_SESSION_SECRET", "")
if not _SECRET:
    _SECRET = secrets.token_hex(32)
    logger.warning("REPORT_MARK_SESSION_SECRET 未設定,已隨機產生(重啟將登出所有人)")

_TRUSTED_PROXY_CIDRS = os.environ.get(
    "REPORT_MARK_TRUSTED_PROXY_CIDRS",
    "127.0.0.1/32,::1/128",
)


def _parse_networks(raw: str) -> tuple[ipaddress._BaseNetwork, ...]:
    networks = []
    for part in raw.split(","):
        cidr = part.strip()
        if not cidr:
            continue
        try:
            networks.append(ipaddress.ip_network(cidr, strict=False))
        except ValueError:
            logger.warning("忽略無效的 REPORT_MARK_TRUSTED_PROXY_CIDRS: %s", cidr)
    return tuple(networks)


_TRUSTED_PROXY_NETWORKS = _parse_networks(_TRUSTED_PROXY_CIDRS)


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
    if not hmac.compare_digest(sig.encode(), _sign(exp_str).encode()):
        return False
    return exp > now


def check_credentials(username: str, password: str) -> bool:
    """常數時間比對帳號與密碼(先各算再 AND,不短路,避免時序側信道)。
    以 bytes 比對:compare_digest 對含非 ASCII 的 str 會丟 TypeError,
    統一編碼成 bytes,讓中文/任意字元帳密一律安全比對而非崩潰。"""
    u_ok = hmac.compare_digest((username or "").encode(), _USERNAME_B)
    p_ok = hmac.compare_digest((password or "").encode(), _PASSWORD_B)
    return u_ok and p_ok


def set_session_cookie(response, now: int, *, secure: bool) -> None:
    response.set_cookie(
        COOKIE_NAME,
        issue_token(now),
        max_age=SESSION_TTL,
        httponly=True,
        samesite="lax",
        secure=secure,
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


def _sweep_all(now: int) -> None:
    stale = [ip for ip in list(_FAILS) if not _prune(ip, now)]
    for ip in stale:
        _FAILS.pop(ip, None)
    overflow = len(_FAILS) - MAX_TRACKED_IPS
    if overflow <= 0:
        return
    # 若短時間冒出大量新 IP，保留最近有失敗紀錄者，其餘淘汰，避免 dict 無界成長。
    oldest = sorted(_FAILS.items(), key=lambda item: item[1][-1])[:overflow]
    for ip, _fails in oldest:
        _FAILS.pop(ip, None)
def is_locked(ip: str, now: int) -> bool:
    return len(_prune(ip, now)) >= MAX_FAILS


def record_failure(ip: str, now: int) -> None:
    _sweep_all(now)
    fails = _prune(ip, now)
    fails.append(now)
    _FAILS[ip] = fails
    if len(_FAILS) > MAX_TRACKED_IPS:
        _sweep_all(now)


def reset(ip: str) -> None:
    _FAILS.pop(ip, None)


def client_ip(request) -> str:
    """真實來源 IP:nginx 以 X-Real-IP 帶入還原後 IP;LAN 直連則用連線位址。
    外部無法偽造 X-Real-IP(nginx 以 $remote_addr 覆寫)。"""
    peer = request.client.host if request.client else "unknown"
    forwarded = request.headers.get("x-real-ip")
    if forwarded and _is_trusted_proxy(peer):
        return forwarded
    return peer


def _is_trusted_proxy(host: str | None) -> bool:
    if not host:
        return False
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return host == "localhost"
    return any(addr in network for network in _TRUSTED_PROXY_NETWORKS)


def _host_is_local(host: str | None) -> bool:
    if not host:
        return False
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def request_is_secure(request) -> bool:
    if request.url.scheme == "https":
        return True
    peer = request.client.host if request.client else None
    if not _is_trusted_proxy(peer):
        return False
    proto = request.headers.get("x-forwarded-proto", "")
    return proto.lower() == "https"


def allow_insecure_local(request) -> bool:
    host = request.url.hostname
    peer = request.client.host if request.client else None
    return _host_is_local(host) or _host_is_local(peer)


def login_allowed(request) -> bool:
    return request_is_secure(request) or allow_insecure_local(request)
