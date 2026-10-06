"""管理後台 API（/api/admin/*）：帳號管理、權限（scope／super admin）、權限提升、帳號刪除排程、
替遺失驗證器的人重設 TOTP，與管理操作稽核。整組限管理員。

帳號的規則（最後一位管理員、不能鎖死自己、停用即撤銷 session、稽核同交易）都在
`app/services/accounts.py`，這裡只做 HTTP 轉換：服務層拋的 `AccountError` 子類別對到
狀態碼，訊息原樣當 detail 給前端顯示。呼叫一律經 `deps.accounts`（測試的替換點）。

授權只認後端：`router` 層掛 `authz.require_admin`，每條路由再掛自己的 scope（`authz.require_scope`）；
調整權限要 super admin 加上近 10 分鐘內重新驗證過密碼（`require_super`＋`require_elevated`）；提出刪除、
重設別人的 TOTP 要 `accounts.manage`＋已提升。取消刪除不需要提升（它只是還原）。批次停用／啟用／強制登出
（`POST /api/admin/users/bulk`）是一次動到多個帳號的高風險操作，也要已提升；規則逐筆套用單筆操作
（`accounts.bulk_user_action`）。
`tests/test_authz.py` 結構性檢查每條 /api/admin/* 路由都有這兩層。前端 `/app/admin/*` 的 route guard
只是不顯示頁面。

日誌：每個管理動作記一行 INFO（誰對哪個帳號做了什麼），**絕不記密碼**；
完整紀錄在 `research.admin_audit_log`（GET /api/admin/audit）。
"""
from __future__ import annotations

import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, StringConstraints

from app.services.accounts import (
    BULK_MAX_USERS,
    AccountDeletedError,
    AccountError,
    DeletionPendingError,
    DeletionWindowClosedError,
    InvalidInputError,
    LastAdminError,
    LastSuperError,
    NoPendingDeletionError,
    PermissionDeniedError,
    SelfLockoutError,
    TotpStateError,
    User,
    UsernameTakenError,
    UserNotFoundError,
)
from web import authz, deps
from web.errors import AppError
from web.routers.account_security import ElevateRequest, ElevateResponse, perform_elevation

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
    totp_enabled: bool = False
    deletion_execute_after: str | None = None  # 有尚未執行的刪除排程時：執行時刻


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


class DeletionItem(BaseModel):
    id: int
    user_id: str
    username: str | None  # 執行後是 deleted-<uuid>
    requested_by: str | None
    requested_by_username: str | None
    requested_at: str | None
    execute_after: str | None
    cancelled_at: str | None
    executed_at: str | None
    status: Literal["pending", "cancelled", "executed"]


class DeletionListResponse(BaseModel):
    items: list[DeletionItem]


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


class BulkUsersRequest(BaseModel):
    action: Literal["disable", "enable", "logout"]
    # 1–BULK_MAX_USERS 筆（422）；重複的只處理一次，格式不對的 id 逐筆回 not_found。
    user_ids: list[Annotated[str, StringConstraints(max_length=64)]] = Field(..., min_length=1,
                                                                            max_length=BULK_MAX_USERS)


class BulkUserResultItem(BaseModel):
    user_id: str
    # ok：有變更並已寫稽核；unchanged：已是目標狀態（與單筆一樣不寫稽核）；skipped：被規則擋下。
    status: Literal["ok", "unchanged", "skipped"]
    code: str | None = None  # 略過的錯誤代碼（與單筆操作的 code 相同，例如 self_lockout、last_super）
    detail: str | None = None
    revoked_sessions: int | None = None  # 強制登出時撤銷的 session 數


class BulkUsersResponse(BaseModel):
    action: Literal["disable", "enable", "logout"]
    requested: int  # 去重後的筆數
    ok: int
    unchanged: int
    skipped: int
    results: list[BulkUserResultItem]


# ── 輔助函式一律放在所有 @router.* 裝飾器之上（夾在中間會讓端點回 422）──────────

_STATUS = (
    (UserNotFoundError, 404, "not_found"),
    (UsernameTakenError, 409, "username_taken"),
    (LastAdminError, 409, "last_admin"),
    (LastSuperError, 409, "last_super"),
    (SelfLockoutError, 409, "self_lockout"),
    (PermissionDeniedError, 403, "super_required"),
    (AccountDeletedError, 409, "account_deleted"),
    (DeletionPendingError, 409, "deletion_pending"),
    (NoPendingDeletionError, 404, "no_pending_deletion"),
    (DeletionWindowClosedError, 409, "deletion_window_closed"),
    (TotpStateError, 409, "totp_state"),
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
        is_super=info.is_super, scopes=list(info.scopes), totp_enabled=info.totp_enabled,
        deletion_execute_after=_iso(info.deletion_execute_after),
    )


def _deletion_item(d) -> DeletionItem:
    return DeletionItem(
        id=d.id, user_id=d.user_id, username=d.username, requested_by=d.requested_by,
        requested_by_username=d.requested_by_username, requested_at=_iso(d.requested_at),
        execute_after=_iso(d.execute_after), cancelled_at=_iso(d.cancelled_at), executed_at=_iso(d.executed_at),
        status=d.status,
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


@router.post("/api/admin/users/bulk", response_model=BulkUsersResponse,
             dependencies=[_ACCOUNTS, Depends(authz.require_elevated)])
async def bulk_user_action(body: BulkUsersRequest, actor: User = Depends(authz.current_user)):
    """一次停用／啟用／強制登出多個帳號：逐筆套用單筆操作的規則，回逐筆結果與彙總（200，即使全部略過）。

    同一筆交易：被規則擋下的逐筆略過（`code` 與單筆相同），其餘照做、各寫一筆稽核並加一筆批次摘要
    `user.bulk_action`；非規則性的失敗整批回滾。批次強制登出不含自己（`self_lockout`）。
    """
    try:
        results = await deps.accounts.bulk_user_action(body.user_ids, body.action, actor_id=actor.id)
    except AccountError as exc:
        err = _http_error(exc)
        raise AppError(422 if isinstance(exc, InvalidInputError) else err.status_code, err.code, str(exc)) from exc
    items = [
        BulkUserResultItem(user_id=r.user_id, status=r.status, revoked_sessions=r.revoked_sessions,
                           code=_http_error(r.error).code if r.error is not None else None,
                           detail=str(r.error) if r.error is not None else None)
        for r in results
    ]
    counts = {k: sum(1 for i in items if i.status == k) for k in ("ok", "unchanged", "skipped")}
    _log(actor, f"user.bulk_{body.action}", f"{len(items)} 筆", **counts)
    return BulkUsersResponse(action=body.action, requested=len(items), results=items, **counts)


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
    """重新驗證密碼（帳號開了 TOTP 時＋驗證碼），取得 10 分鐘的權限提升（綁在目前這個 session）。

    實作與 POST /api/me/elevate 共用（`account_security.perform_elevation`）；與登入共用每 IP 失敗限流。
    """
    result = await perform_elevation(request, actor, body.password, body.code)
    _log(actor, "session.elevate", actor.username)
    return result


@router.post("/api/admin/users/{user_id}/deletion", response_model=DeletionItem,
             dependencies=[_ACCOUNTS, Depends(authz.require_elevated)])
async def request_deletion(user_id: str, actor: User = Depends(authz.current_user)):
    """提出刪除：立即停用並撤銷所有 session，24 小時後由批次執行（之前可取消）。"""
    try:
        d = await deps.accounts.request_deletion(user_id, actor_id=actor.id)
    except AccountError as exc:
        raise _http_error(exc) from exc
    _log(actor, "user.delete_requested", user_id, execute_after=_iso(d.execute_after))
    return _deletion_item(d)


@router.post("/api/admin/users/{user_id}/deletion/cancel", response_model=DeletionItem, dependencies=[_ACCOUNTS])
async def cancel_deletion(user_id: str, actor: User = Depends(authz.current_user)):
    """撤銷窗口內取消刪除，還原提出前的啟用狀態；已到執行時刻 409 `deletion_window_closed`。"""
    try:
        d = await deps.accounts.cancel_deletion(user_id, actor_id=actor.id)
    except AccountError as exc:
        raise _http_error(exc) from exc
    _log(actor, "user.delete_cancelled", user_id)
    return _deletion_item(d)


@router.get("/api/admin/deletions", response_model=DeletionListResponse, dependencies=[_ACCOUNTS])
async def list_deletions(status: Literal["pending", "all"] = Query("pending")):
    items = await deps.accounts.list_deletions(include_done=status == "all")
    return DeletionListResponse(items=[_deletion_item(d) for d in items])


@router.post("/api/admin/users/{user_id}/totp/reset", response_model=UserItem,
             dependencies=[_ACCOUNTS, Depends(authz.require_elevated)])
async def reset_totp(user_id: str, actor: User = Depends(authz.current_user)):
    """替遺失驗證器的人關閉兩步驟驗證（對方之後可自行重新開啟）。對 super admin 只有 super admin 能做。"""
    try:
        info = await deps.accounts.disable_totp(user_id, actor_id=actor.id)
    except AccountError as exc:
        raise _http_error(exc) from exc
    _log(actor, "user.totp_reset", info.username)
    return _user_item(info)


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
