"""監控投影的唯讀查詢（/api/admin/jobs、/api/admin/observations）。整組限管理員＋`ops.read`。

資料來源是 DB 裡的 projection（revision 0005）：`scripts/collect_resource_usage.py` 先寫本機 spool，
`scripts/load_observations.py` 每 5 分鐘冪等匯入。**這裡看到的不是告警的真相來源**——告警只有 P5 在發
（不經 web、不經 DB）；這裡最多晚幾分鐘，DB 掛掉期間的觀測在恢復後補進來。即時狀態看 `/api/admin/ops/*`。

上限（`app/services/ops_monitoring.py` 的常數）：觀測一次最多 5000 筆、時間範圍最多 7 天（預設最近 1 小時）；
批次一頁最多 200 筆、時間範圍最多 90 天（預設最近 7 天）。超過回 400 `invalid_params`（筆數超過是 422）。
沒有時區的時間一律當 UTC。

SQL 在 `app/services/ops_monitoring.py`，經 `deps.ops_monitoring` 呼叫（測試的替換點）。
輔助函式一律放在 `@router` 裝飾器之上（夾在裝飾器與 handler 之間會讓端點回 422）。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.services.ops_monitoring import (
    JOB_DEFAULT_WINDOW,
    JOB_MAX_LIMIT,
    JOB_MAX_WINDOW,
    OBSERVATION_DEFAULT_WINDOW,
    OBSERVATION_MAX_LIMIT,
    OBSERVATION_MAX_WINDOW,
    resolve_window,
)
from web import authz, deps
from web.errors import AppError

router = APIRouter(dependencies=[Depends(authz.require_admin)])

_OPS_READ = Depends(authz.require_scope("ops.read"))

# 與 revision 0005 的 CHECK 逐字一致。
JobState = Literal["running", "finished", "lost"]
ExecMainCode = Literal["exited", "killed", "dumped"]
Scope = Literal["host", "container", "service"]

MAX_JOB_OFFSET = 10000


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


class ObservationListResponse(BaseModel):
    since: str
    until: str
    limit: int
    truncated: bool
    items: list[ObservationItem]


# ── 輔助函式一律放在所有 @router.* 裝飾器之上 ──────────────────────────────


def _iso(v) -> str | None:
    return v.isoformat() if hasattr(v, "isoformat") else (str(v) if v is not None else None)


def _window(since: datetime | None, until: datetime | None, default: timedelta,
            maximum: timedelta) -> tuple[datetime, datetime]:
    since, until = resolve_window(since, until, default)
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
    """觀測值（新→舊，最多 `limit` 筆，超過時 `truncated=true`）。沒給 `since` 時取 `until`（預設現在）前一小時。"""
    since_used, until_used = _window(since, until, OBSERVATION_DEFAULT_WINDOW, OBSERVATION_MAX_WINDOW)
    async with deps.SessionFactory() as session:
        truncated, rows = await deps.ops_monitoring.list_observations(
            session, since=since_used, until=until_used, scope=scope, subject=subject, metric=metric, limit=limit,
        )
    return ObservationListResponse(
        since=_iso(since_used), until=_iso(until_used), limit=limit, truncated=truncated,
        items=[ObservationItem(**{**r, "observed_at": _iso(r["observed_at"])}) for r in rows],
    )
