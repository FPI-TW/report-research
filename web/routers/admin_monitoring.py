"""監控投影的唯讀查詢（/api/admin/incidents*、/api/admin/jobs、/api/admin/observations）。整組限管理員＋`ops.read`。

資料來源是 DB 裡的 projection（revision 0005／0006），由 `scripts/load_observations.py` 從本機 spool 冪等
匯入：事件來自 `scripts/incident_handler.sh`（P5）的狀態轉換，觀測與批次執行來自
`scripts/collect_resource_usage.py`。**這裡看到的不是告警的真相來源**——告警只有 P5 在發（不經 web、
不經 DB），loader 每 5 分鐘匯入一次，所以這裡最多晚幾分鐘；DB 掛掉期間的觀測在恢復後補進來。

SQL 在 `app/services/ops_monitoring.py`，經 `deps.ops_monitoring` 呼叫（測試的替換點）。
輔助函式一律放在 `@router` 裝飾器之上（夾在裝飾器與 handler 之間會讓端點回 422）。
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Path, Query
from pydantic import BaseModel

from web import authz, deps
from web.errors import AppError

router = APIRouter(dependencies=[Depends(authz.require_admin)])

_OPS_READ = Depends(authz.require_scope("ops.read"))

IncidentKind = Literal["service", "monitor_blind"]
IncidentStatus = Literal["firing", "resolved"]
IncidentSeverity = Literal["CRITICAL", "WARNING"]
EventAction = Literal["FIRING", "REMINDER", "ESCALATED", "RESOLVED"]
EventSeverity = Literal["CRITICAL", "WARNING", "RESOLVED"]
JobState = Literal["running", "finished"]
Scope = Literal["host", "container", "service"]

IncidentId = Annotated[str, Path(pattern=r"^[A-Za-z0-9_.:-]{1,300}$", max_length=300)]


class IncidentItem(BaseModel):
    incident_id: str
    host: str
    component: str
    kind: IncidentKind
    status: IncidentStatus
    severity: IncidentSeverity
    reason: str
    summary: str | None = None
    opened_at: str
    last_event_at: str
    resolved_at: str | None = None
    event_count: int


class IncidentListResponse(BaseModel):
    total: int
    limit: int
    offset: int
    has_more: bool
    next_offset: int | None
    items: list[IncidentItem]


class IncidentEventItem(BaseModel):
    event_id: str
    occurred_at: str
    action: EventAction
    severity: EventSeverity
    reason: str
    status: str | None = None
    summary: str | None = None
    notified: bool
    journal_excerpt: str | None = None
    journal_truncated: bool = False


class IncidentDetail(IncidentItem):
    events: list[IncidentEventItem]


class JobItem(BaseModel):
    host: str
    unit: str
    service: str | None = None
    invocation_id: str
    state: JobState
    started_at: str
    finished_at: str | None = None
    duration_seconds: float | None = None
    result: str | None = None
    exit_status: int | None = None
    exec_main_code: str | None = None


class JobListResponse(BaseModel):
    total: int
    limit: int
    offset: int
    has_more: bool
    next_offset: int | None
    items: list[JobItem]


class ObservationItem(BaseModel):
    observed_at: str
    host: str
    scope: Scope
    subject: str
    metric: str
    value: float | None = None
    state: str | None = None
    detail: dict[str, Any] | None = None


class ObservationListResponse(BaseModel):
    since: str
    until: str
    limit: int
    truncated: bool
    items: list[ObservationItem]


# ── 輔助函式一律放在所有 @router.* 裝飾器之上 ──────────────────────────────


def _iso(v) -> str | None:
    return v.isoformat() if hasattr(v, "isoformat") else (str(v) if v is not None else None)


def _incident(row: dict) -> dict:
    return {
        **row,
        "opened_at": _iso(row["opened_at"]),
        "last_event_at": _iso(row["last_event_at"]),
        "resolved_at": _iso(row.get("resolved_at")),
    }


def _page(total: int, limit: int, offset: int, n: int) -> dict:
    next_offset = offset + n
    has_more = next_offset < total
    return {"total": total, "limit": limit, "offset": offset, "has_more": has_more,
            "next_offset": next_offset if has_more else None}


def _check_range(since: datetime | None, until: datetime | None) -> None:
    if since and until and since > until:
        raise AppError(400, "invalid_params", "since 不可晚於 until")


@router.get("/api/admin/incidents", response_model=IncidentListResponse, dependencies=[_OPS_READ])
async def list_incidents(
    status: IncidentStatus | None = Query(None),
    component: str | None = Query(None, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$"),
    since: datetime | None = Query(None),
    until: datetime | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """事件清單（新→舊）。`since`／`until` 取與該區間重疊的事件（ISO 8601，建議帶時區）。"""
    _check_range(since, until)
    async with deps.SessionFactory() as session:
        total, rows = await deps.ops_monitoring.list_incidents(
            session, status=status, component=component, since=since, until=until, limit=limit, offset=offset,
        )
    return IncidentListResponse(**_page(total, limit, offset, len(rows)),
                                items=[IncidentItem(**_incident(r)) for r in rows])


@router.get("/api/admin/incidents/{incident_id}", response_model=IncidentDetail, dependencies=[_OPS_READ])
async def get_incident(incident_id: IncidentId):
    """單一事件與它的全部狀態轉換；FIRING 那一則帶當時擷取的 journal 片段（已遮祕密、有大小上限）。"""
    async with deps.SessionFactory() as session:
        row, events = await deps.ops_monitoring.get_incident(session, incident_id)
    if row is None:
        raise AppError(404, "not_found", "找不到這個事件")
    return IncidentDetail(
        **_incident(row),
        events=[IncidentEventItem(**{**e, "occurred_at": _iso(e["occurred_at"])}) for e in events],
    )


@router.get("/api/admin/jobs", response_model=JobListResponse, dependencies=[_OPS_READ])
async def list_jobs(
    unit: str | None = Query(None, max_length=200, pattern=r"^[A-Za-z0-9@._:-]+$"),
    service: str | None = Query(None, max_length=40, pattern=r"^[a-z][a-z0-9-]*$"),
    state: JobState | None = Query(None),
    result: str | None = Query(None, max_length=64, pattern=r"^[a-z-]+$"),
    since: datetime | None = Query(None),
    until: datetime | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """批次 oneshot 的執行紀錄（依開始時間新→舊）。`result` 是 systemd 的 Result（`success`、`exit-code`…）。"""
    _check_range(since, until)
    async with deps.SessionFactory() as session:
        total, rows = await deps.ops_monitoring.list_jobs(
            session, unit=unit, service=service, state=state, result=result, since=since, until=until,
            limit=limit, offset=offset,
        )
    items = []
    for r in rows:
        dur = (r["finished_at"] - r["started_at"]).total_seconds() if r.get("finished_at") else None
        items.append(JobItem(**{**r, "started_at": _iso(r["started_at"]), "finished_at": _iso(r.get("finished_at")),
                                "duration_seconds": dur}))
    return JobListResponse(**_page(total, limit, offset, len(rows)), items=items)


@router.get("/api/admin/observations", response_model=ObservationListResponse, dependencies=[_OPS_READ])
async def list_observations(
    scope: Scope | None = Query(None),
    subject: str | None = Query(None, max_length=200),
    metric: str | None = Query(None, max_length=64, pattern=r"^[a-z][a-z0-9_]*$"),
    since: datetime | None = Query(None),
    until: datetime | None = Query(None),
    limit: int = Query(500, ge=1, le=5000),
):
    """觀測值（新→舊，最多 `limit` 筆，超過時 `truncated=true`）。沒給 `since` 時取 `until`（預設現在）前一小時。"""
    _check_range(since, until)
    async with deps.SessionFactory() as session:
        since_used, until_used, truncated, rows = await deps.ops_monitoring.list_observations(
            session, scope=scope, subject=subject, metric=metric, since=since, until=until, limit=limit,
        )
    return ObservationListResponse(
        since=_iso(since_used), until=_iso(until_used), limit=limit, truncated=truncated,
        items=[ObservationItem(**{**r, "observed_at": _iso(r["observed_at"])}) for r in rows],
    )
