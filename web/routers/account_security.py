"""自助帳號安全（/api/me/*）：兩步驟驗證（TOTP）的開啟／確認／關閉，以及任何使用者都能用的權限提升。

與 `/api/admin/*` 分開：這裡每個登入的人都能打（middleware 已確認登入，`authz.current_user` 取身分），
只能動**自己的**帳號。規則（已啟用不能覆蓋 secret、確認才啟用、時間步不可重用）都在
`app/services/accounts.py`，這裡只做 HTTP 轉換；呼叫一律經 `deps.accounts`（測試的替換點）。

權限提升（`perform_elevation`）是 `POST /api/me/elevate` 與 `POST /api/admin/elevate` 共用的實作：
一般使用者關閉 TOTP 也需要「近 10 分鐘內在這個 session 重新驗證過」，但打不到限管理員的
`/api/admin/elevate`。帳號開了 TOTP 時兩者都要驗證碼；密碼正確卻沒給驗證碼回 403 `totp_required`
（不計入失敗限流），前端據此補問驗證碼。

開發模式免登入（`accounts.DEV_USER`，id=None）沒有真的帳號：TOTP 端點回 400 `dev_mode`。
"""
from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.services.accounts import (
    AccountDeletedError,
    AccountError,
    InvalidInputError,
    TotpRequiredError,
    TotpStateError,
    User,
    UserNotFoundError,
)
from web import auth, authz, deps
from web.errors import AppError

logger = logging.getLogger(__name__)

router = APIRouter()


class TotpStatusResponse(BaseModel):
    enabled: bool
    pending: bool  # 已產生 secret、尚未輸入第一個正確的驗證碼


class TotpSetupResponse(BaseModel):
    secret: str  # 只在這一次回傳；驗證器 App 可手動輸入
    otpauth_uri: str


class TotpCodeRequest(BaseModel):
    code: str = Field(..., max_length=32)


class ElevateRequest(BaseModel):
    password: str = Field(..., max_length=1024)
    code: str | None = Field(None, max_length=32)  # 帳號開了 TOTP 時必填


class ElevateResponse(BaseModel):
    elevated_until: str


# ── 輔助函式一律放在所有 @router.* 裝飾器之上（夾在中間會讓端點回 422）──────────

_STATUS = (
    (UserNotFoundError, 404, "not_found"),
    (AccountDeletedError, 409, "account_deleted"),
    (TotpStateError, 409, "totp_state"),
    (InvalidInputError, 400, "invalid_input"),
)


def _http_error(exc: AccountError) -> AppError:
    for cls, status, code in _STATUS:
        if isinstance(exc, cls):
            return AppError(status, code, str(exc))
    return AppError(400, "bad_request", str(exc))


def _own_id(user: User) -> str:
    if user.id is None:
        raise AppError(400, "dev_mode", "開發模式免登入沒有真的帳號，無法設定兩步驟驗證")
    return user.id


async def perform_elevation(request: Request, actor: User, password: str, code: str | None) -> ElevateResponse:
    """重新驗證密碼（＋TOTP），取得 10 分鐘、綁在目前 session 的權限提升。與登入共用每 IP 失敗限流。"""
    if actor.is_elevated and actor.id is None:  # 開發模式免登入：沒有 session，本來就視為已提升
        return ElevateResponse(elevated_until=actor.elevated_until.isoformat() if actor.elevated_until else "")
    ip, now = auth.client_ip(request), int(time.time())
    if auth.is_locked(ip, now):
        raise AppError(429, "rate_limited", "嘗試次數過多，請稍後再試")
    session_id = getattr(request.state, "session_id", None)
    try:
        until = await deps.accounts.elevate_session(session_id, password, code) if session_id else None
    except TotpRequiredError as exc:
        raise AppError(403, "totp_required", str(exc)) from exc
    if until is None:
        auth.record_failure(ip, now)
        logger.warning("權限提升失敗 user=%s ip=%s（第 %s 次）", actor.username, ip, auth.failure_count(ip, now))
        raise AppError(403, "bad_password", "密碼或驗證碼不正確" if code else "密碼不正確")
    auth.reset(ip)
    logger.info("權限提升 user=%s", actor.username)
    return ElevateResponse(elevated_until=until.isoformat() if hasattr(until, "isoformat") else str(until))


@router.post("/api/me/elevate", response_model=ElevateResponse)
async def elevate_me(body: ElevateRequest, request: Request, actor: User = Depends(authz.current_user)):
    """任何登入的使用者：重新驗證密碼（開了 TOTP 的帳號＋驗證碼），取得 10 分鐘的權限提升。"""
    return await perform_elevation(request, actor, body.password, body.code)


@router.get("/api/me/totp", response_model=TotpStatusResponse)
async def totp_status(user: User = Depends(authz.current_user)):
    try:
        status = await deps.accounts.totp_status(_own_id(user))
    except AccountError as exc:
        raise _http_error(exc) from exc
    return TotpStatusResponse(enabled=status.enabled, pending=status.pending)


@router.post("/api/me/totp/setup", response_model=TotpSetupResponse)
async def totp_setup(user: User = Depends(authz.current_user)):
    """產生新的 secret（尚未啟用）；已啟用時 409 `totp_state`（要換裝置請先關閉）。"""
    try:
        setup = await deps.accounts.begin_totp_setup(_own_id(user))
    except AccountError as exc:
        raise _http_error(exc) from exc
    logger.info("TOTP 設定開始 user=%s", user.username)
    return TotpSetupResponse(secret=setup.secret, otpauth_uri=setup.otpauth_uri)


@router.post("/api/me/totp/confirm", response_model=TotpStatusResponse)
async def totp_confirm(body: TotpCodeRequest, user: User = Depends(authz.current_user)):
    """輸入驗證器 App 顯示的第一個碼；正確才啟用（400 `bad_totp`）。"""
    try:
        ok = await deps.accounts.confirm_totp(_own_id(user), body.code)
    except AccountError as exc:
        raise _http_error(exc) from exc
    if not ok:
        raise AppError(400, "bad_totp", "驗證碼不正確，請確認手機時間正確後輸入目前顯示的碼")
    logger.info("TOTP 已啟用 user=%s", user.username)
    return TotpStatusResponse(enabled=True, pending=False)


@router.post("/api/me/totp/disable", response_model=TotpStatusResponse,
             dependencies=[Depends(authz.require_elevated)])
async def totp_disable(user: User = Depends(authz.current_user)):
    """關閉自己的兩步驟驗證；需要近 10 分鐘內重新驗證過（403 `elevation_required`）。"""
    uid = _own_id(user)
    try:
        await deps.accounts.disable_totp(uid, actor_id=uid)
    except AccountError as exc:
        raise _http_error(exc) from exc
    logger.info("TOTP 已關閉 user=%s", user.username)
    return TotpStatusResponse(enabled=False, pending=False)
