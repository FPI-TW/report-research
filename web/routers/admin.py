"""管理後台 API（/api/admin/*）：帳號管理、權限（scope／super admin）、權限提升與管理操作稽核。整組限管理員。

帳號的規則（最後一位管理員、不能鎖死自己、停用即撤銷 session、稽核同交易）都在
`app/services/accounts.py`，這裡只做 HTTP 轉換：服務層拋的 `AccountError` 子類別對到
狀態碼，訊息原樣當 detail 給前端顯示。呼叫一律經 `deps.accounts`（測試的替換點）。

授權只認後端：`router` 層掛 `authz.require_admin`，每條路由再掛自己的 scope（`authz.require_scope`）；
調整權限要 super admin 加上近 10 分鐘內重新驗證過密碼（`require_super`＋`require_elevated`）。
`tests/test_authz.py` 結構性檢查每條 /api/admin/* 路由都有這兩層。前端 `/app/admin/*` 的 route guard
只是不顯示頁面。

日誌：每個管理動作記一行 INFO（誰對哪個帳號做了什麼），**絕不記密碼**；
完整紀錄在 `research.admin_audit_log`（GET /api/admin/audit）。
"""
from __future__ import annotations

import logging
import time
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.services.accounts import (
    AccountError,
    InvalidInputError,
    LastAdminError,
    LastSuperError,
    PermissionDeniedError,
    SelfLockoutError,
    User,
    UsernameTakenError,
    UserNotFoundError,
)
from web import auth, authz, deps
from web.errors import AppError

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(authz.require_admin)])

Role = Literal["admin", "user"]
# 與 accounts.GRANTABLE_SCOPES 逐字一致（tests/test_admin_api.py 釘住）；OpenAPI 與產生的前端 client 靠它列舉。
GrantableScope = Literal["qa_content.read", "ops.operate"]

_ACCOUNTS = Depends(authz.require_scope("accounts.manage"))
_AUDIT = Depends(authz.require_scope("audit.read"))


class UserItem(BaseModel):
    id: str
    username: str
    role: Role
    enabled: bool
    created_at: str | None = None
    updated_at: str | None = None
    password_changed_at: str | None = None
    last_login_at: str | None = None
    last_seen_at: str | None = None
    active_sessions: int = 0
    is_super: bool = False
    scopes: list[GrantableScope] = []  # 另外授予的；管理員預設 scope 不列


class UserListResponse(BaseModel):
    items: list[UserItem]


class CreateUserRequest(BaseModel):
    # 長度上限只為擋巨大 payload；實際規則（2–64 字元、密碼 10–256）由服務層判，
    # 回 400 與中文原因，而不是 pydantic 的 422 英文訊息。
    username: str = Field(..., max_length=200)
    password: str = Field(..., max_length=1024)
    role: Role = "user"


class UpdateUserRequest(BaseModel):
    role: Role | None = None
    enabled: bool | None = None


class PasswordRequest(BaseModel):
    password: str = Field(..., max_length=1024)


class LogoutResponse(BaseModel):
    revoked: int


class ElevateRequest(BaseModel):
    password: str = Field(..., max_length=1024)


class ElevateResponse(BaseModel):
    elevated_until: str


class PrivilegesRequest(BaseModel):
    # 兩欄都省略＝沒有要變更（400）。scopes 是完整清單（取代），不是增量。
    is_super: bool | None = None
    scopes: list[GrantableScope] | None = None


class AuditChainResponse(BaseModel):
    ok: bool
    total: int
    head_id: int | None
    head_hash: str | None
    broken_ids: list[int]


class AuditItem(BaseModel):
    id: int
    actor_user_id: str | None
    actor_username: str | None
    action: str
    target_type: str
    target_id: str | None
    detail: dict
    created_at: str | None


class AuditResponse(BaseModel):
    total: int
    limit: int
    offset: int
    has_more: bool
    next_offset: int | None
    items: list[AuditItem]


# ── 輔助函式一律放在所有 @router.* 裝飾器之上（夾在中間會讓端點回 422）──────────

_STATUS = (
    (UserNotFoundError, 404, "not_found"),
    (UsernameTakenError, 409, "username_taken"),
    (LastAdminError, 409, "last_admin"),
    (LastSuperError, 409, "last_super"),
    (SelfLockoutError, 409, "self_lockout"),
    (PermissionDeniedError, 403, "super_required"),
    (InvalidInputError, 400, "invalid_input"),
)


def _http_error(exc: AccountError) -> AppError:
    for cls, status, code in _STATUS:
        if isinstance(exc, cls):
            return AppError(status, code, str(exc))
    return AppError(400, "bad_request", str(exc))


def _iso(v) -> str | None:
    return v.isoformat() if hasattr(v, "isoformat") else (str(v) if v is not None else None)


def _user_item(info) -> UserItem:
    return UserItem(
        id=info.id, username=info.username, role=info.role, enabled=info.enabled,
        created_at=_iso(info.created_at), updated_at=_iso(info.updated_at),
        password_changed_at=_iso(info.password_changed_at), last_login_at=_iso(info.last_login_at),
        last_seen_at=_iso(info.last_seen_at), active_sessions=info.active_sessions,
        is_super=info.is_super, scopes=list(info.scopes),
    )


def _log(actor: User, action: str, target: str, **extra) -> None:
    tail = " ".join(f"{k}={v}" for k, v in extra.items())
    logger.info("管理操作 actor=%s action=%s target=%s %s", actor.username, action, target, tail)


@router.get("/api/admin/users", response_model=UserListResponse, dependencies=[_ACCOUNTS])
async def list_users():
    return UserListResponse(items=[_user_item(u) for u in await deps.accounts.list_users()])


@router.post("/api/admin/users", response_model=UserItem, status_code=201, dependencies=[_ACCOUNTS])
async def create_user(body: CreateUserRequest, actor: User = Depends(authz.current_user)):
    try:
        info = await deps.accounts.create_user(body.username, body.password, body.role, actor_id=actor.id)
    except AccountError as exc:
        raise _http_error(exc) from exc
    _log(actor, "user.create", info.username, role=info.role)
    return _user_item(info)


@router.patch("/api/admin/users/{user_id}", response_model=UserItem, dependencies=[_ACCOUNTS])
async def update_user(user_id: str, body: UpdateUserRequest, actor: User = Depends(authz.current_user)):
    if body.role is None and body.enabled is None:
        raise HTTPException(status_code=400, detail="沒有要變更的欄位（role 或 enabled）")
    try:
        info = await deps.accounts.update_user(user_id, role=body.role, enabled=body.enabled, actor_id=actor.id)
    except AccountError as exc:
        raise _http_error(exc) from exc
    _log(actor, "user.update", info.username, role=info.role, enabled=info.enabled)
    return _user_item(info)


@router.post("/api/admin/users/{user_id}/password", response_model=UserItem, dependencies=[_ACCOUNTS])
async def reset_password(user_id: str, body: PasswordRequest, actor: User = Depends(authz.current_user)):
    try:
        info = await deps.accounts.reset_password(user_id, body.password, actor_id=actor.id)
    except AccountError as exc:
        raise _http_error(exc) from exc
    _log(actor, "user.reset_password", info.username)
    return _user_item(info)


@router.post("/api/admin/users/{user_id}/logout", response_model=LogoutResponse, dependencies=[_ACCOUNTS])
async def force_logout(user_id: str, actor: User = Depends(authz.current_user)):
    try:
        revoked = await deps.accounts.force_logout(user_id, actor_id=actor.id)
    except AccountError as exc:
        raise _http_error(exc) from exc
    _log(actor, "user.force_logout", user_id, revoked=revoked)
    return LogoutResponse(revoked=revoked)


@router.put(
    "/api/admin/users/{user_id}/privileges", response_model=UserItem,
    dependencies=[Depends(authz.require_super), Depends(authz.require_elevated)],
)
async def set_privileges(user_id: str, body: PrivilegesRequest, actor: User = Depends(authz.current_user)):
    if body.is_super is None and body.scopes is None:
        raise HTTPException(status_code=400, detail="沒有要變更的欄位（is_super 或 scopes）")
    try:
        info = await deps.accounts.set_privileges(user_id, is_super=body.is_super, scopes=body.scopes,
                                                  actor_id=actor.id)
    except AccountError as exc:
        raise _http_error(exc) from exc
    _log(actor, "user.set_privileges", info.username, is_super=info.is_super, scopes=",".join(info.scopes))
    return _user_item(info)


@router.post("/api/admin/elevate", response_model=ElevateResponse,
             dependencies=[Depends(authz.require_scope("admin"))])
async def elevate(body: ElevateRequest, request: Request, actor: User = Depends(authz.current_user)):
    """重新驗證密碼，取得 10 分鐘的權限提升（綁在目前這個 session）。與登入共用每 IP 失敗限流。"""
    if actor.is_elevated and actor.id is None:  # 開發模式免登入：沒有 session，本來就視為已提升
        return ElevateResponse(elevated_until=_iso(actor.elevated_until) or "")
    ip, now = auth.client_ip(request), int(time.time())
    if auth.is_locked(ip, now):
        raise AppError(429, "rate_limited", "嘗試次數過多，請稍後再試")
    session_id = getattr(request.state, "session_id", None)
    until = await deps.accounts.elevate_session(session_id, body.password) if session_id else None
    if until is None:
        auth.record_failure(ip, now)
        logger.warning("權限提升失敗 user=%s ip=%s（第 %s 次）", actor.username, ip, auth.failure_count(ip, now))
        raise AppError(403, "bad_password", "密碼不正確")
    auth.reset(ip)
    _log(actor, "session.elevate", actor.username)
    return ElevateResponse(elevated_until=_iso(until) or "")


@router.get("/api/admin/audit/verify", response_model=AuditChainResponse, dependencies=[_AUDIT])
async def verify_audit_chain():
    status = await deps.accounts.verify_audit_chain()
    return AuditChainResponse(ok=status.ok, total=status.total, head_id=status.head_id,
                              head_hash=status.head_hash, broken_ids=list(status.broken_ids))


@router.get("/api/admin/audit", response_model=AuditResponse, dependencies=[_AUDIT])
async def audit_log(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    total, entries = await deps.accounts.list_audit(limit=limit, offset=offset)
    next_offset = offset + len(entries)
    has_more = next_offset < total
    return AuditResponse(
        total=total, limit=limit, offset=offset, has_more=has_more,
        next_offset=next_offset if has_more else None,
        items=[
            AuditItem(
                id=e.id, actor_user_id=e.actor_user_id, actor_username=e.actor_username, action=e.action,
                target_type=e.target_type, target_id=e.target_id, detail=e.detail, created_at=_iso(e.created_at),
            )
            for e in entries
        ],
    )
