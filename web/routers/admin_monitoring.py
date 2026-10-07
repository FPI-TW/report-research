"""監控投影的唯讀查詢（/api/admin/jobs、/api/admin/observations、/api/admin/incidents*）。整組限管理員＋`ops.read`。

資料來源是 DB 裡的 projection（revision 0005、0006）：`scripts/collect_resource_usage.py` 與 P5
（`scripts/incident_handler.sh`）先寫本機 spool，`scripts/load_observations.py` 每 5 分鐘冪等匯入。**這裡看到的不是
告警的真相來源**——告警只有 P5 在發（不經 web、不經 DB）；這裡最多晚幾分鐘，DB 掛掉期間的資料在恢復後補進來，
spool 寫入失敗的那一筆則會缺。即時狀態看 `/api/admin/ops/*`。

上限（`app/services/ops_monitoring.py` 的常數）：觀測一次最多 5000 筆、時間範圍最多 90 天（＝保留期，預設最近 1 小時）；
批次一頁最多 200 筆、時間範圍最多 90 天（預設最近 7 天）；事件一頁最多 200 筆、時間範圍最多 366 天（預設最近
30 天）、詳情最多 1000 則轉換。超過回 400 `invalid_params`（筆數超過是 422）。
沒有時區的時間一律當 UTC。

觀測的粒度依 `since` 距今多久自動選（`app/services/ops_rollup.py` 的 `pick_resolution`，回應的 `resolution`
標示實際用的）：24 小時內 `raw`（逐筆）、7 天內 `5m`、更早 `1h`——與保留期的分段一致（revision 0007），
選的是資料保證還在的最細粒度。聚合的桶：`observed_at` 是桶起點、`value` 是平均、`state` 是最後的狀態，另附
`sample_count`、`value_min`／`value_max`／`value_last`、`first_state`、`state_changes`。

事件趨勢（`/api/admin/incidents/trends`，Admin v2 DB lane）不新表，直接彙總 `incident` 與 `job_execution`：每週依元件
與嚴重度的件數、MTTR 與持續時間 p50／p90（只算 resolved，`lost` 另計不納入）、最常見的 reason、各 unit 90 天內的
失敗率。SQL 在 `app/services/db_insights.py`（經 `deps.db_insights`）。路由必須宣告在
`/api/admin/incidents/{incident_id}` 之前，否則 `trends` 會被當成事件 id（真的事件 id 一律帶 `:`，不會撞名）。

SQL 在 `app/services/ops_monitoring.py`，經 `deps.ops_monitoring` 呼叫（測試的替換點）。
輔助函式一律放在 `@router` 裝飾器之上（夾在裝飾器與 handler 之間會讓端點回 422）。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Path, Query
from pydantic import BaseModel

from app.services.db_insights import JOB_TREND_WINDOW
from app.services.ops_monitoring import (
    INCIDENT_DEFAULT_WINDOW,
    INCIDENT_MAX_LIMIT,
    INCIDENT_MAX_WINDOW,
    JOB_DEFAULT_WINDOW,
    JOB_MAX_LIMIT,
    JOB_MAX_WINDOW,
    OBSERVATION_DEFAULT_WINDOW,
    OBSERVATION_MAX_LIMIT,
    OBSERVATION_MAX_WINDOW,
    resolve_window,
)
from app.services.ops_rollup import pick_resolution
from web import authz, deps
from web.errors import AppError

router = APIRouter(dependencies=[Depends(authz.require_admin)])

_OPS_READ = Depends(authz.require_scope("ops.read"))

# 與 revision 0005 的 CHECK 逐字一致。
JobState = Literal["running", "finished", "lost"]
ExecMainCode = Literal["exited", "killed", "dumped"]
Scope = Literal["host", "container", "service"]
# 與 app/services/ops_rollup.py 的 RESOLUTIONS 一致。
Resolution = Literal["raw", "5m", "1h"]
# 與 revision 0006 的 CHECK 逐字一致；前端 zod 型別由 gen_admin_client.py 從這裡產生。
IncidentKind = Literal["service", "monitor_blind"]
IncidentStatus = Literal["firing", "resolved", "lost"]
IncidentSeverity = Literal["CRITICAL", "WARNING"]
EventAction = Literal["FIRING", "REMINDER", "ESCALATED", "RESOLVED"]
EventSeverity = Literal["CRITICAL", "WARNING", "RESOLVED"]

MAX_JOB_OFFSET = 10000
MAX_INCIDENT_OFFSET = 10000
INCIDENT_TREND_DEFAULT_WINDOW = timedelta(days=90)
IncidentId = Annotated[str, Path(min_length=1, max_length=300, pattern=r"^[A-Za-z0-9_.:-]+$")]


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
    exec_main_code: ExecMainCode | None = None
    last_seen_at: str


class JobListResponse(BaseModel):
    since: str
    until: str
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
    # 以下只在聚合的桶（resolution 為 5m／1h）出現；原始觀測一律 null。
    sample_count: int | None = None
    value_min: float | None = None
    value_max: float | None = None
    value_last: float | None = None
    first_state: str | None = None
    state_changes: int | None = None


class ObservationListResponse(BaseModel):
    since: str
    until: str
    limit: int
    truncated: bool
    resolution: Resolution
    items: list[ObservationItem]


class IncidentItem(BaseModel):
    incident_id: str
    host: str
    component: str
    kind: IncidentKind
    probe_unit: str | None = None
    status: IncidentStatus
    severity: IncidentSeverity
    reason: str
    summary: str | None = None
    opened_at: str
    last_event_at: str
    resolved_at: str | None = None
    duration_seconds: float | None = None
    event_count: int


class IncidentListResponse(BaseModel):
    since: str
    until: str
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
    journal_since: str | None = None
    journal_until: str | None = None
    journal_units: str | None = None


class IncidentDetail(IncidentItem):
    events: list[IncidentEventItem]
    events_truncated: bool


class IncidentTrendWeek(BaseModel):
    week_start: str
    component: str
    severity: IncidentSeverity
    total: int
    resolved: int
    lost: int
    firing: int


class IncidentTrendStats(BaseModel):
    total: int
    resolved: int
    lost: int
    firing: int
    critical: int
    warning: int
    mttr_seconds: float | None = None
    p50_seconds: float | None = None
    p90_seconds: float | None = None


class IncidentTrendComponent(IncidentTrendStats):
    component: str


class IncidentTrendReason(BaseModel):
    reason: str
    total: int
    components: list[str]


class JobFailureRate(BaseModel):
    unit: str
    runs: int
    finished: int
    failed: int
    lost: int
    running: int
    failure_rate: float | None = None
    last_failure_at: str | None = None
    last_started_at: str | None = None


class IncidentTrendsResponse(BaseModel):
    since: str
    until: str
    weeks: list[IncidentTrendWeek]
    summary: IncidentTrendStats
    by_component: list[IncidentTrendComponent]
    top_reasons: list[IncidentTrendReason]
    jobs_since: str
    jobs_until: str
    jobs: list[JobFailureRate]


# ── 輔助函式一律放在所有 @router.* 裝飾器之上 ──────────────────────────────


def _iso(v) -> str | None:
    return v.isoformat() if hasattr(v, "isoformat") else (str(v) if v is not None else None)


def _window(since: datetime | None, until: datetime | None, default: timedelta,
            maximum: timedelta, *, now: datetime | None = None) -> tuple[datetime, datetime]:
    since, until = resolve_window(since, until, default, now=now)
    if since > until:
        raise AppError(400, "invalid_params", "since 不可晚於 until")
    if until - since > maximum:
        raise AppError(400, "invalid_params", f"時間範圍最多 {maximum.days} 天")
    return since, until


def _job(row: dict) -> JobItem:
    started, finished = row["started_at"], row.get("finished_at")
    duration = (finished - started).total_seconds() if finished is not None else None
    return JobItem(**{**row, "started_at": _iso(started), "finished_at": _iso(finished),
                      "last_seen_at": _iso(row["last_seen_at"]), "duration_seconds": duration})


def _incident(row: dict) -> dict:
    opened, resolved = row["opened_at"], row.get("resolved_at")
    return {**row, "opened_at": _iso(opened), "last_event_at": _iso(row["last_event_at"]),
            "resolved_at": _iso(resolved),
            "duration_seconds": (resolved - opened).total_seconds() if resolved is not None else None}


def _incident_event(row: dict) -> IncidentEventItem:
    return IncidentEventItem(**{**row, "occurred_at": _iso(row["occurred_at"]),
                                "journal_since": _iso(row.get("journal_since")),
                                "journal_until": _iso(row.get("journal_until"))})


@router.get("/api/admin/jobs", response_model=JobListResponse, dependencies=[_OPS_READ])
async def list_jobs(
    service: str | None = Query(None, max_length=40, pattern=r"^[a-z][a-z0-9-]*$"),
    unit: str | None = Query(None, max_length=208, pattern=r"^[A-Za-z0-9][A-Za-z0-9@._:-]*\.service$"),
    state: JobState | None = Query(None),
    result: str | None = Query(None, max_length=64, pattern=r"^[a-z][a-z-]*$"),
    since: datetime | None = Query(None),
    until: datetime | None = Query(None),
    limit: int = Query(50, ge=1, le=JOB_MAX_LIMIT),
    offset: int = Query(0, ge=0, le=MAX_JOB_OFFSET),
):
    """批次 oneshot 的執行紀錄（依開始時間新→舊）。`result` 是 systemd 的 Result（`success`、`exit-code`…）；
    `state=lost` 是沒觀測到結束、之後已有新一輪開始的那一次（結果不明）。"""
    since_used, until_used = _window(since, until, JOB_DEFAULT_WINDOW, JOB_MAX_WINDOW)
    async with deps.SessionFactory() as session:
        total, rows = await deps.ops_monitoring.list_jobs(
            session, since=since_used, until=until_used, service=service, unit=unit, state=state, result=result,
            limit=limit, offset=offset,
        )
    next_offset = offset + len(rows)
    has_more = next_offset < total
    return JobListResponse(
        since=_iso(since_used), until=_iso(until_used), total=total, limit=limit, offset=offset, has_more=has_more,
        next_offset=next_offset if has_more else None, items=[_job(r) for r in rows],
    )


@router.get("/api/admin/observations", response_model=ObservationListResponse, dependencies=[_OPS_READ])
async def list_observations(
    scope: Scope | None = Query(None),
    subject: str | None = Query(None, min_length=1, max_length=200),
    metric: str | None = Query(None, max_length=64, pattern=r"^[a-z][a-z0-9_]*$"),
    since: datetime | None = Query(None),
    until: datetime | None = Query(None),
    limit: int = Query(500, ge=1, le=OBSERVATION_MAX_LIMIT),
):
    """觀測值（新→舊，最多 `limit` 筆，超過時 `truncated=true`）。沒給 `since` 時取 `until`（預設現在）前一小時。
    粒度依 `since` 距今自動選（24 小時內 `raw`、7 天內 `5m`、更早 `1h`），回應的 `resolution` 標示實際用的。"""
    now = datetime.now(timezone.utc)
    since_used, until_used = _window(since, until, OBSERVATION_DEFAULT_WINDOW, OBSERVATION_MAX_WINDOW, now=now)
    resolution = pick_resolution(since_used, now)
    async with deps.SessionFactory() as session:
        truncated, rows = await deps.ops_monitoring.list_observations(
            session, since=since_used, until=until_used, scope=scope, subject=subject, metric=metric, limit=limit,
            resolution=resolution,
        )
    return ObservationListResponse(
        since=_iso(since_used), until=_iso(until_used), limit=limit, truncated=truncated, resolution=resolution,
        items=[ObservationItem(**{**r, "observed_at": _iso(r["observed_at"])}) for r in rows],
    )


@router.get("/api/admin/incidents", response_model=IncidentListResponse, dependencies=[_OPS_READ])
async def list_incidents(
    status: IncidentStatus | None = Query(None),
    component: str | None = Query(None, min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$"),
    since: datetime | None = Query(None),
    until: datetime | None = Query(None),
    limit: int = Query(50, ge=1, le=INCIDENT_MAX_LIMIT),
    offset: int = Query(0, ge=0, le=MAX_INCIDENT_OFFSET),
):
    """P5 事件的投影（依開場時間新→舊）。`since`／`until` 取與區間重疊的事件：還在 firing 的一律算，resolved 看
    恢復時間，lost（沒收到 RESOLVED、同元件已有更晚的事件，結束時間不明）看最後一則轉換。"""
    since_used, until_used = _window(since, until, INCIDENT_DEFAULT_WINDOW, INCIDENT_MAX_WINDOW)
    async with deps.SessionFactory() as session:
        total, rows = await deps.ops_monitoring.list_incidents(
            session, since=since_used, until=until_used, status=status, component=component, limit=limit,
            offset=offset,
        )
    next_offset = offset + len(rows)
    has_more = next_offset < total
    return IncidentListResponse(
        since=_iso(since_used), until=_iso(until_used), total=total, limit=limit, offset=offset, has_more=has_more,
        next_offset=next_offset if has_more else None, items=[IncidentItem(**_incident(r)) for r in rows],
    )


@router.get("/api/admin/incidents/trends", response_model=IncidentTrendsResponse, dependencies=[_OPS_READ])
async def get_incident_trends(
    since: datetime | None = Query(None),
    until: datetime | None = Query(None),
):
    """事件趨勢：開場時間落在區間內的事件（預設最近 90 天、最多 366 天）每週（台北時間週一起）依元件與嚴重度的
    件數；整體與各元件的件數、MTTR、持續時間 p50／p90（只算 resolved；`lost` 結束時間不明，只計件數）；最常見的
    10 個 reason。另附各 unit 在 `until` 前 90 天內的執行次數與失敗率（finished 而 Result 不是 success 算失敗，
    分母是 finished；lost／running 另列）。範圍顛倒或過長 400 `invalid_params`。"""
    since_used, until_used = _window(since, until, INCIDENT_TREND_DEFAULT_WINDOW, INCIDENT_MAX_WINDOW)
    jobs_since = until_used - JOB_TREND_WINDOW
    async with deps.SessionFactory() as session:
        trends = await deps.db_insights.incident_trends(session, since=since_used, until=until_used)
        jobs = await deps.db_insights.job_failure_rates(session, since=jobs_since, until=until_used)
    return IncidentTrendsResponse(
        since=_iso(since_used), until=_iso(until_used), weeks=trends["weeks"], summary=trends["summary"],
        by_component=trends["by_component"], top_reasons=trends["top_reasons"],
        jobs_since=_iso(jobs_since), jobs_until=_iso(until_used), jobs=jobs,
    )


@router.get("/api/admin/incidents/{incident_id}", response_model=IncidentDetail, dependencies=[_OPS_READ])
async def get_incident(incident_id: IncidentId):
    """單一事件與它的狀態轉換（舊→新）。FIRING／RESOLVED 帶 P5 當下擷取的 journal 片段（已遮祕密、有大小上限；
    完整 log 以 journald 為準）。不存在 404 `not_found`。"""
    async with deps.SessionFactory() as session:
        row, events, truncated = await deps.ops_monitoring.get_incident(session, incident_id)
    if row is None:
        raise AppError(404, "not_found", "找不到這個事件")
    return IncidentDetail(**_incident(row), events=[_incident_event(e) for e in events], events_truncated=truncated)
