"""管理後台 API（/api/admin/*）：帳號管理與管理操作稽核。整組限管理員。

帳號的規則（最後一位管理員、不能鎖死自己、停用即撤銷 session、稽核同交易）都在
`app/services/accounts.py`，這裡只做 HTTP 轉換：服務層拋的 `AccountError` 子類別對到
狀態碼，訊息原樣當 detail 給前端顯示。呼叫一律經 `deps.accounts`（測試的替換點）。

授權只認後端：`router` 層掛 `authz.require_admin`，`tests/test_authz.py` 結構性檢查
所有 /api/admin/* 路由都有它。前端 `/app/admin/*` 的 route guard 只是不顯示頁面。

日誌：每個管理動作記一行 INFO（誰對哪個帳號做了什麼），**絕不記密碼**；
完整紀錄在 `research.admin_audit_log`（GET /api/admin/audit）。
"""
from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.services.accounts import (
    AccountError,
    InvalidInputError,
    LastAdminError,
    SelfLockoutError,
    User,
    UsernameTakenError,
    UserNotFoundError,
)
from web import authz, deps

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(authz.require_admin)])

Role = Literal["admin", "user"]


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
    (UserNotFoundError, 404),
    (UsernameTakenError, 409),
    (LastAdminError, 409),
    (SelfLockoutError, 409),
    (InvalidInputError, 400),
)


def _http_error(exc: AccountError) -> HTTPException:
    for cls, code in _STATUS:
        if isinstance(exc, cls):
            return HTTPException(status_code=code, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


def _iso(v) -> str | None:
    return v.isoformat() if hasattr(v, "isoformat") else (str(v) if v is not None else None)


def _user_item(info) -> UserItem:
    return UserItem(
        id=info.id, username=info.username, role=info.role, enabled=info.enabled,
        created_at=_iso(info.created_at), updated_at=_iso(info.updated_at),
        password_changed_at=_iso(info.password_changed_at), last_login_at=_iso(info.last_login_at),
        last_seen_at=_iso(info.last_seen_at), active_sessions=info.active_sessions,
    )


def _log(actor: User, action: str, target: str, **extra) -> None:
    tail = " ".join(f"{k}={v}" for k, v in extra.items())
    logger.info("管理操作 actor=%s action=%s target=%s %s", actor.username, action, target, tail)


@router.get("/api/admin/users", response_model=UserListResponse)
async def list_users():
    return UserListResponse(items=[_user_item(u) for u in await deps.accounts.list_users()])


@router.post("/api/admin/users", response_model=UserItem, status_code=201)
async def create_user(body: CreateUserRequest, actor: User = Depends(authz.current_user)):
    try:
        info = await deps.accounts.create_user(body.username, body.password, body.role, actor_id=actor.id)
    except AccountError as exc:
        raise _http_error(exc) from exc
    _log(actor, "user.create", info.username, role=info.role)
    return _user_item(info)


@router.patch("/api/admin/users/{user_id}", response_model=UserItem)
async def update_user(user_id: str, body: UpdateUserRequest, actor: User = Depends(authz.current_user)):
    if body.role is None and body.enabled is None:
        raise HTTPException(status_code=400, detail="沒有要變更的欄位（role 或 enabled）")
    try:
        info = await deps.accounts.update_user(user_id, role=body.role, enabled=body.enabled, actor_id=actor.id)
    except AccountError as exc:
        raise _http_error(exc) from exc
    _log(actor, "user.update", info.username, role=info.role, enabled=info.enabled)
    return _user_item(info)


@router.post("/api/admin/users/{user_id}/password", response_model=UserItem)
async def reset_password(user_id: str, body: PasswordRequest, actor: User = Depends(authz.current_user)):
    try:
        info = await deps.accounts.reset_password(user_id, body.password, actor_id=actor.id)
    except AccountError as exc:
        raise _http_error(exc) from exc
    _log(actor, "user.reset_password", info.username)
    return _user_item(info)


@router.post("/api/admin/users/{user_id}/logout", response_model=LogoutResponse)
async def force_logout(user_id: str, actor: User = Depends(authz.current_user)):
    try:
        revoked = await deps.accounts.force_logout(user_id, actor_id=actor.id)
    except AccountError as exc:
        raise _http_error(exc) from exc
    _log(actor, "user.force_logout", user_id, revoked=revoked)
    return LogoutResponse(revoked=revoked)


@router.get("/api/admin/audit", response_model=AuditResponse)
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
