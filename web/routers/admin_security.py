"""Security Operations（`/api/admin/security/*`；Admin v2 Security lane）。

授權：router 層掛 `authz.require_admin`＋`audit.read`（登入事件、可疑 IP、高風險操作時間線、稽核鏈、TOTP 採用率、
告警明細）。session 清單另加 `accounts.manage`，撤銷單一 session 再加 `authz.require_elevated`；服務層在
`app/services/accounts.py`（`list_sessions`、`admin_revoke_session`，同交易寫稽核 `session.admin_revoke`）。
`tests/test_authz.py` 結構性檢查每條路由。

隱私：登入事件本身**沒有帳號名稱**（`accounts.record_auth_event` 介面上就沒有）；清單顯示的名稱是讀取時以 user_id
對 `app_user` 現值 join 出來的，已刪除帳號不顯示。IP、UA 只回給 `audit.read` 的管理員。

不做帳號鎖定與 app 層 IP 封鎖（使用者定案 8）：可疑 IP 只彙整，封鎖交給 Cloudflare WAF（操作手冊在
`docs/production_resilience.md`）。既有的每 IP 失敗限流（`web/auth.py`）保留不動。
`/healthz/security`（本機直連、狀態型告警）在 `web/routers/health.py`，與這裡的 `/alerts` 共用
`security_ops.evaluate_alerts`。
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel

from app.config import get_settings
from app.services import security_ops
from app.services.accounts import (
    AccountDeletedError,
    AccountError,
    PermissionDeniedError,
    SessionNotFoundError,
    User,
)
from web import authz, deps
from web.errors import AppError

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(authz.require_admin), Depends(authz.require_scope("audit.read"))])

_ACCOUNTS = Depends(authz.require_scope("accounts.manage"))

# 與 accounts.AUTH_EVENT_TYPES 逐字一致（tests/test_admin_security_api.py 釘住）；OpenAPI 與前端 client 靠它列舉。
AuthEventType = Literal[
    "login.success", "login.failure", "login.totp_failure", "login.locked", "login.insecure",
    "logout", "elevate.success", "elevate.failure",
]
# 與 security_ops.HIGH_RISK_CATEGORIES 逐字一致（同上）。
HighRiskCategory = Literal[
    "privilege", "elevation", "session", "credential", "deletion", "ops", "data_access", "config",
]
SecurityState = Literal["ok", "unknown", "audit_chain_broken", "elevate_failures", "account_failures", "login_failures"]


class AuthEventItem(BaseModel):
    id: int
    occurred_at: str | None = None
    event: str
    reason: str | None = None
    user_id: str | None = None
    username: str | None = None  # 讀取時 join 的現值；帳號不存在或已刪除為 null
    session_id: str | None = None
    ip: str | None = None
    user_agent: str | None = None
    count: int = 1


class AuthEventListResponse(BaseModel):
    items: list[AuthEventItem]
    next_before_id: int | None = None


class SuspiciousIpItem(BaseModel):
    ip: str
    failures: int
    locked: int
    insecure: int
    successes: int
    distinct_users: int
    first_seen: str | None = None
    last_seen: str | None = None


class SuspiciousIpResponse(BaseModel):
    hours: int
    min_failures: int
    items: list[SuspiciousIpItem]


class SecuritySessionItem(BaseModel):
    id: str
    user_id: str
    username: str
    created_at: str | None = None
    last_seen_at: str | None = None
    expires_at: str | None = None
    revoked_at: str | None = None
    ip: str | None = None
    user_agent: str | None = None
    elevated_until: str | None = None
    active: bool
    current: bool = False  # 就是發出這個請求的 session


class SecuritySessionListResponse(BaseModel):
    items: list[SecuritySessionItem]


class HighRiskItem(BaseModel):
    id: int
    category: HighRiskCategory
    action: str
    actor_user_id: str | None = None
    actor_username: str | None = None
    target_type: str
    target_id: str | None = None
    detail: dict
    created_at: str | None = None


class HighRiskResponse(BaseModel):
    days: int
    items: list[HighRiskItem]
    next_before_id: int | None = None


class AuditLiveItem(BaseModel):
    state: Literal["ok", "broken", "error"]
    total: int | None = None
    head_id: int | None = None
    broken_count: int = 0
    checked_at: str | None = None
    error: str | None = None
    cache_seconds: int


class AuditAnchorItem(BaseModel):
    state: Literal["ok", "tamper", "error", "stale", "missing", "corrupt"]
    at: str | None = None
    written_at: str | None = None
    age_hours: float | None = None
    head_id: int | None = None
    total: int | None = None
    anchors_checked: int | None = None
    message: str | None = None


class AuditChainStateResponse(BaseModel):
    live: AuditLiveItem
    anchor: AuditAnchorItem


class AdminWithoutTotp(BaseModel):
    id: str
    username: str
    is_super: bool


class TotpAdoptionResponse(BaseModel):
    users_total: int
    users_enabled: int
    admins_total: int
    admins_enabled: int
    admins_without_totp: list[AdminWithoutTotp]
    policy_required: bool  # ADMIN_MFA_REQUIRED（預設關；開啟時沒開 TOTP 的管理員用不了管理功能）


class AlertAccount(BaseModel):
    user_id: str
    username: str | None = None


class SecurityAlertsResponse(BaseModel):
    state: SecurityState
    triggered: list[SecurityState]
    window_minutes: int
    login_failures: int
    login_failure_threshold: int
    accounts_over: list[AlertAccount]
    max_account_failures: int
    account_failure_threshold: int
    elevate_failures: int
    elevate_failure_threshold: int
    audit_chain: Literal["ok", "broken", "error"]
    events_error: str | None = None


# ── 輔助函式一律放在所有 @router.* 裝飾器之上（夾在中間會讓端點回 422）──────────


def _iso(v) -> str | None:
    return v.isoformat() if hasattr(v, "isoformat") else (str(v) if v is not None else None)


def _check_uuid(value: str | None, name: str) -> None:
    if value is not None and not security_ops.valid_uuid(value):
        raise AppError(400, "invalid_input", f"{name} 不是合法的 UUID")


def _session_item(s, current_sid: str | None) -> SecuritySessionItem:
    return SecuritySessionItem(
        id=s.id, user_id=s.user_id, username=s.username, created_at=_iso(s.created_at),
        last_seen_at=_iso(s.last_seen_at), expires_at=_iso(s.expires_at), revoked_at=_iso(s.revoked_at), ip=s.ip,
        user_agent=s.user_agent, elevated_until=_iso(s.elevated_until), active=s.active,
        current=current_sid is not None and s.id == current_sid,
    )


_REVOKE_STATUS = (
    (SessionNotFoundError, 404, "not_found"),
    (AccountDeletedError, 409, "account_deleted"),
    (PermissionDeniedError, 403, "super_required"),
)


def _revoke_error(exc: AccountError) -> AppError:
    for cls, status, code in _REVOKE_STATUS:
        if isinstance(exc, cls):
            return AppError(status, code, str(exc))
    return AppError(400, "bad_request", str(exc))


@router.get("/api/admin/security/alerts", response_model=SecurityAlertsResponse)
async def security_alerts():
    """此刻的告警判斷明細（與 /healthz/security 同一個判斷，這裡多回計數與門檻）。"""
    ev = await security_ops.evaluate_alerts(deps.accounts.verify_audit_chain)
    names = {}
    if ev.accounts_over:
        try:
            names = await security_ops.usernames(ev.accounts_over)
        except Exception:  # 名稱只是輔助顯示
            logger.warning("告警明細的帳號名稱查詢失敗", exc_info=True)
    return SecurityAlertsResponse(
        state=ev.state, triggered=list(ev.triggered), window_minutes=ev.window_minutes,
        login_failures=ev.login_failures, login_failure_threshold=ev.login_failure_threshold,
        accounts_over=[AlertAccount(user_id=u, username=names.get(u)) for u in ev.accounts_over],
        max_account_failures=ev.max_account_failures, account_failure_threshold=ev.account_failure_threshold,
        elevate_failures=ev.elevate_failures, elevate_failure_threshold=ev.elevate_failure_threshold,
        audit_chain=ev.audit_chain.state, events_error=ev.events_error,
    )


@router.get("/api/admin/security/events", response_model=AuthEventListResponse)
async def security_events(
    event: AuthEventType | None = Query(None),
    user_id: str | None = Query(None, max_length=64),
    ip: str | None = Query(None, max_length=64),
    since: datetime | None = Query(None),
    until: datetime | None = Query(None),
    before_id: int | None = Query(None, ge=1),
    limit: int = Query(50, ge=1, le=security_ops.EVENT_LIST_MAX),
):
    """登入與安全事件，新的在前；`before_id` 取下一頁（上一頁回的 `next_before_id`）。"""
    _check_uuid(user_id, "user_id")
    rows = await security_ops.list_auth_events(event=event, user_id=user_id, ip=ip, since=since, until=until,
                                               before_id=before_id, limit=limit)
    items = [
        AuthEventItem(id=r.id, occurred_at=_iso(r.occurred_at), event=r.event, reason=r.reason, user_id=r.user_id,
                      username=r.username, session_id=r.session_id, ip=r.ip, user_agent=r.user_agent, count=r.count)
        for r in rows
    ]
    return AuthEventListResponse(items=items, next_before_id=items[-1].id if len(items) == limit else None)


@router.get("/api/admin/security/suspicious-ips", response_model=SuspiciousIpResponse)
async def security_suspicious_ips(
    hours: int = Query(24, ge=1, le=24 * 30), min_failures: int = Query(5, ge=1, le=10000),
):
    """視窗內登入失敗（含被限流擋下的請求）達門檻的 IP，多的在前。只彙整，不封鎖。"""
    rows = await security_ops.suspicious_ips(hours=hours, min_failures=min_failures)
    return SuspiciousIpResponse(hours=hours, min_failures=min_failures, items=[
        SuspiciousIpItem(ip=r.ip, failures=r.failures, locked=r.locked, insecure=r.insecure, successes=r.successes,
                         distinct_users=r.distinct_users, first_seen=_iso(r.first_seen), last_seen=_iso(r.last_seen))
        for r in rows
    ])


@router.get("/api/admin/security/sessions", response_model=SecuritySessionListResponse, dependencies=[_ACCOUNTS])
async def security_sessions(
    request: Request,
    active_only: bool = Query(True),
    user_id: str | None = Query(None, max_length=64),
    limit: int = Query(200, ge=1, le=500),
):
    """session 總覽（`accounts.manage`），最近活動的在前。"""
    _check_uuid(user_id, "user_id")
    items = await deps.accounts.list_sessions(user_id=user_id, active_only=active_only, limit=limit)
    current = getattr(request.state, "session_id", None)
    return SecuritySessionListResponse(items=[_session_item(s, current) for s in items])


@router.post("/api/admin/security/sessions/{session_id}/revoke", response_model=SecuritySessionItem,
             dependencies=[_ACCOUNTS, Depends(authz.require_elevated)])
async def security_revoke_session(session_id: str, request: Request, actor: User = Depends(authz.current_user)):
    """撤銷單一 session（`accounts.manage`＋已提升）；撤銷與稽核 `session.admin_revoke` 同一筆交易。

    其他裝置不受影響（整個帳號登出是 `POST /api/admin/users/{user_id}/logout`）。已撤銷的原樣回傳、不再寫稽核。
    對 super admin 的 session 只有 super admin 能動（403 `super_required`）。撤銷自己目前的 session 等同登出。
    """
    _check_uuid(session_id, "session_id")
    try:
        info = await deps.accounts.admin_revoke_session(session_id, actor_id=actor.id)
    except AccountError as exc:
        raise _revoke_error(exc) from exc
    logger.info("管理操作 actor=%s action=session.admin_revoke target=%s", actor.username, info.username)
    return _session_item(info, getattr(request.state, "session_id", None))


@router.get("/api/admin/security/high-risk", response_model=HighRiskResponse)
async def security_high_risk(
    days: int = Query(30, ge=1, le=400),
    category: HighRiskCategory | None = Query(None),
    before_id: int | None = Query(None, ge=1),
    limit: int = Query(100, ge=1, le=security_ops.TIMELINE_MAX),
):
    """高風險操作時間線：稽核紀錄裡屬於 `security_ops.HIGH_RISK_ACTIONS` 的列，新的在前。只顯示、不告警。"""
    rows = await security_ops.high_risk_timeline(days=days, category=category, before_id=before_id, limit=limit)
    items = [
        HighRiskItem(id=r.id, category=r.category, action=r.action, actor_user_id=r.actor_user_id,
                     actor_username=r.actor_username, target_type=r.target_type, target_id=r.target_id,
                     detail=r.detail, created_at=_iso(r.created_at))
        for r in rows
    ]
    return HighRiskResponse(days=days, items=items, next_before_id=items[-1].id if len(rows) == limit else None)


@router.get("/api/admin/security/audit-chain", response_model=AuditChainStateResponse)
async def security_audit_chain():
    """稽核鏈：即時驗證（快取 `AUDIT_VERIFY_CACHE_SECONDS`）＋最後一次外部錨定（`data/health/audit_anchor.json`）。"""
    snap = await security_ops.audit_chain_snapshot(deps.accounts.verify_audit_chain)
    anchor = security_ops.read_anchor_status()
    return AuditChainStateResponse(
        live=AuditLiveItem(state=snap.state, total=snap.total, head_id=snap.head_id, broken_count=snap.broken_count,
                           checked_at=_iso(snap.checked_at), error=snap.error,
                           cache_seconds=int(get_settings().audit_verify_cache_seconds)),
        anchor=AuditAnchorItem(state=anchor.state, at=anchor.at, written_at=anchor.written_at,
                               age_hours=anchor.age_hours, head_id=anchor.head_id, total=anchor.total,
                               anchors_checked=anchor.anchors_checked, message=anchor.message),
    )


@router.get("/api/admin/security/totp-adoption", response_model=TotpAdoptionResponse)
async def security_totp_adoption():
    """啟用中帳號的兩步驟驗證採用率（全體與管理員）。只顯示採用狀況，不強制。"""
    a = await security_ops.totp_adoption()
    return TotpAdoptionResponse(
        users_total=a.users_total, users_enabled=a.users_enabled, admins_total=a.admins_total,
        admins_enabled=a.admins_enabled,
        admins_without_totp=[AdminWithoutTotp(id=i, username=n, is_super=s) for i, n, s in a.admins_without_totp],
        policy_required=bool(get_settings().admin_mfa_required),
    )
