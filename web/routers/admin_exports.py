"""管理清單的 CSV 匯出（/api/admin/export/*.csv）。整組限管理員，每條宣告與對應清單頁相同的 scope。

| 匯出 | scope | 來源（與清單頁同一支服務函式） |
|---|---|---|
| `audit.csv` | `audit.read` | `deps.accounts.list_audit`（新→舊） |
| `users.csv` | `accounts.manage` | `deps.accounts.list_users`（不含已刪除帳號） |
| `reports.csv` | `reports.manage` | `deps.report_visibility.list_reports`（篩選同清單：`q`、`hidden`、`publication`） |
| `incidents.csv` | `ops.read` | `deps.ops_monitoring.list_incidents`（窗期規則同 `/api/admin/incidents`） |
| `jobs.csv` | `ops.read` | `deps.ops_monitoring.list_jobs`（窗期規則同 `/api/admin/jobs`） |

規則：

- **筆數上限** `EXPORT_MAX_ROWS`（`limit` 可再調小）；超過時只匯出最新的那幾筆，回應標頭 `X-Export-Truncated: true`。
- **先寫稽核再交資料**：每次匯出以 `deps.accounts.record_export`（內部是 `record_audit`）寫一筆 `data.export`
  （誰、種類、篩選條件、筆數、是否達上限；不含內容）。稽核寫不進去 → 503 `export_audit_failed`，不匯出。
- **帳號匯出不含任何密碼／TOTP 衍生值**：欄位是白名單（`USER_COLUMNS`）；`UserInfo` 本身也沒有雜湊、secret、
  fingerprint。刻意連 `password_changed_at` 也不列。
- **絕不提供問答原文的匯出**：這裡沒有、也不該有 `qa_log` 的匯出；問答原文只能經待複核佇列逐筆讀並留稽核。
- 儲存格防公式注入、UTF-8 BOM、串流送出（`web/csv_export.py`）；檔名 `report-mark-<種類>-<YYYYMMDD>.csv`。
- 用 GET（下載網址可直接給瀏覽器）；回應 `Cache-Control: no-store`。

輔助函式一律放在 `@router` 裝飾器之上（夾在裝飾器與 handler 之間會讓端點回 422）。
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query

from app.services.accounts import User
from app.services.ops_monitoring import (
    INCIDENT_DEFAULT_WINDOW,
    INCIDENT_MAX_WINDOW,
    JOB_DEFAULT_WINDOW,
    JOB_MAX_WINDOW,
)
from web import authz, deps
from web.csv_export import CsvResponse, csv_response
from web.errors import AppError
from web.routers.admin_monitoring import IncidentStatus, JobState, _incident, _job, _window

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(authz.require_admin)])

EXPORT_MAX_ROWS = 10000

_CSV = {
    "response_class": CsvResponse,
    "responses": {200: {"description": "CSV（UTF-8 BOM）", "content": {"text/csv": {"schema": {"type": "string"}}}}},
}

AUDIT_COLUMNS = ("id", "created_at", "actor_user_id", "actor_username", "action", "target_type", "target_id",
                 "detail")
# 白名單：不得加入任何密碼、TOTP secret 或其衍生值（tests/test_admin_exports_api.py 釘住）。
USER_COLUMNS = ("id", "username", "role", "enabled", "is_super", "scopes", "totp_enabled", "created_at",
                "updated_at", "last_login_at", "last_seen_at", "active_sessions", "deletion_execute_after")
REPORT_COLUMNS = ("file_hash", "report_id", "title", "file_name", "source", "market", "report_date", "created_at",
                  "hidden", "hidden_reason", "visibility_updated_by", "visibility_updated_at", "publication")
INCIDENT_COLUMNS = ("incident_id", "host", "component", "kind", "probe_unit", "status", "severity", "reason",
                    "summary", "opened_at", "last_event_at", "resolved_at", "duration_seconds", "event_count")
JOB_COLUMNS = ("host", "unit", "service", "invocation_id", "state", "started_at", "finished_at", "duration_seconds",
               "result", "exit_status", "exec_main_code", "last_seen_at")

_LIMIT = Query(EXPORT_MAX_ROWS, ge=1, le=EXPORT_MAX_ROWS, description="最多匯出幾筆（新的在前）")


# ── 輔助函式一律放在所有 @router.* 裝飾器之上 ──────────────────────────────


def _iso(v) -> str | None:
    return v.isoformat() if hasattr(v, "isoformat") else (str(v) if v is not None else None)


async def _finish(actor: User, kind: str, filters: dict, columns, rows: list[dict], *, truncated: bool):
    """寫稽核（失敗就不匯出），再回串流 CSV。"""
    try:
        await deps.accounts.record_export(actor_id=actor.id, kind=kind, filters=filters, row_count=len(rows),
                                          truncated=truncated)
    except Exception as exc:  # noqa: BLE001 — 沒有稽核就不交資料
        logger.warning("匯出稽核寫入失敗 actor=%s kind=%s：%s", actor.username, kind, type(exc).__name__)
        raise AppError(503, "export_audit_failed", "無法寫入稽核紀錄，這次沒有匯出；請稍後再試") from exc
    logger.info("管理操作 actor=%s action=data.export target=%s rows=%d truncated=%s", actor.username, kind,
                len(rows), truncated)
    return csv_response(kind, columns, rows, truncated=truncated)


@router.get("/api/admin/export/audit.csv", dependencies=[Depends(authz.require_scope("audit.read"))], **_CSV)
async def export_audit(limit: int = _LIMIT, actor: User = Depends(authz.current_user)):
    """管理操作稽核（新→舊）；`detail` 欄是 JSON 字串。匯出本身也會寫一筆 `data.export`。"""
    total, entries = await deps.accounts.list_audit(limit=limit, offset=0)
    rows = [{"id": e.id, "created_at": _iso(e.created_at), "actor_user_id": e.actor_user_id,
             "actor_username": e.actor_username, "action": e.action, "target_type": e.target_type,
             "target_id": e.target_id, "detail": e.detail} for e in entries]
    return await _finish(actor, "audit", {"limit": limit}, AUDIT_COLUMNS, rows, truncated=total > len(rows))


@router.get("/api/admin/export/users.csv", dependencies=[Depends(authz.require_scope("accounts.manage"))], **_CSV)
async def export_users(limit: int = _LIMIT, actor: User = Depends(authz.current_user)):
    """帳號清單（不含已刪除帳號；`scopes` 只列另外授予的，以 `;` 分隔）。不含任何密碼或 TOTP 衍生值。"""
    users = await deps.accounts.list_users()
    rows = [{"id": u.id, "username": u.username, "role": u.role, "enabled": u.enabled, "is_super": u.is_super,
             "scopes": ";".join(u.scopes), "totp_enabled": u.totp_enabled, "created_at": _iso(u.created_at),
             "updated_at": _iso(u.updated_at), "last_login_at": _iso(u.last_login_at),
             "last_seen_at": _iso(u.last_seen_at), "active_sessions": u.active_sessions,
             "deletion_execute_after": _iso(u.deletion_execute_after)} for u in users[:limit]]
    return await _finish(actor, "users", {"limit": limit}, USER_COLUMNS, rows, truncated=len(users) > limit)


@router.get("/api/admin/export/reports.csv", dependencies=[Depends(authz.require_scope("reports.manage"))], **_CSV)
async def export_reports(
    q: str | None = Query(None, max_length=200),
    hidden: bool | None = Query(None),
    publication: Literal["draft", "published"] | None = Query(None),
    limit: int = _LIMIT,
    actor: User = Depends(authz.current_user),
):
    """研報可見性清單（入庫新→舊；篩選同 `/api/admin/reports`）。含隱藏原因（只有管理員看得到的註記）。"""
    async with deps.SessionFactory() as session:
        total, items = await deps.report_visibility.list_reports(
            session, q=q, hidden=hidden, publication=publication, limit=limit, offset=0,
        )
    rows = [{"file_hash": r.file_hash, "report_id": r.report_id, "title": r.title, "file_name": r.file_name,
             "source": r.source, "market": r.market, "report_date": _iso(r.report_date),
             "created_at": _iso(r.created_at), "hidden": r.hidden, "hidden_reason": r.reason,
             "visibility_updated_by": r.updated_by, "visibility_updated_at": _iso(r.updated_at),
             "publication": r.publication} for r in items]
    return await _finish(actor, "reports", {"q": q, "hidden": hidden, "publication": publication, "limit": limit},
                         REPORT_COLUMNS, rows,
                         truncated=total > len(rows))


@router.get("/api/admin/export/incidents.csv", dependencies=[Depends(authz.require_scope("ops.read"))], **_CSV)
async def export_incidents(
    status: IncidentStatus | None = Query(None),
    component: str | None = Query(None, min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$"),
    since: datetime | None = Query(None),
    until: datetime | None = Query(None),
    limit: int = _LIMIT,
    actor: User = Depends(authz.current_user),
):
    """事件清單（依開場時間新→舊；篩選與窗期同 `/api/admin/incidents`：預設最近 30 天、最多 366 天）。"""
    since_used, until_used = _window(since, until, INCIDENT_DEFAULT_WINDOW, INCIDENT_MAX_WINDOW)
    async with deps.SessionFactory() as session:
        total, items = await deps.ops_monitoring.list_incidents(
            session, since=since_used, until=until_used, status=status, component=component, limit=limit, offset=0,
        )
    rows = [_incident(r) for r in items]
    filters = {"status": status, "component": component, "since": _iso(since_used), "until": _iso(until_used),
               "limit": limit}
    return await _finish(actor, "incidents", filters, INCIDENT_COLUMNS, rows, truncated=total > len(rows))


@router.get("/api/admin/export/jobs.csv", dependencies=[Depends(authz.require_scope("ops.read"))], **_CSV)
async def export_jobs(
    service: str | None = Query(None, max_length=40, pattern=r"^[a-z][a-z0-9-]*$"),
    unit: str | None = Query(None, max_length=208, pattern=r"^[A-Za-z0-9][A-Za-z0-9@._:-]*\.service$"),
    state: JobState | None = Query(None),
    result: str | None = Query(None, max_length=64, pattern=r"^[a-z][a-z-]*$"),
    since: datetime | None = Query(None),
    until: datetime | None = Query(None),
    limit: int = _LIMIT,
    actor: User = Depends(authz.current_user),
):
    """排程工作的執行紀錄（依開始時間新→舊；篩選與窗期同 `/api/admin/jobs`：預設最近 7 天、最多 90 天）。"""
    since_used, until_used = _window(since, until, JOB_DEFAULT_WINDOW, JOB_MAX_WINDOW)
    async with deps.SessionFactory() as session:
        total, items = await deps.ops_monitoring.list_jobs(
            session, since=since_used, until=until_used, service=service, unit=unit, state=state, result=result,
            limit=limit, offset=0,
        )
    rows = [_job(r).model_dump() for r in items]
    filters = {"service": service, "unit": unit, "state": state, "result": result, "since": _iso(since_used),
               "until": _iso(until_used), "limit": limit}
    return await _finish(actor, "jobs", filters, JOB_COLUMNS, rows, truncated=total > len(rows))
