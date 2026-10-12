"""進階 DB 與事件趨勢（`/api/admin/db/*`；Admin v2 DB lane）。

授權：router 層掛 `authz.require_admin`＋`ops.read`（`tests/test_authz.py` 結構性檢查）。三條端點都唯讀：

- `GET /api/admin/db/overview`：即時快照，只查系統目錄、受短 statement_timeout／lock_timeout 約束（SET LOCAL）。
  五段各自降級：權限不足、逾時只讓那一段回 `error`，不讓整頁 500（正式環境 EC2 的 RDS 帳號權限較窄）。
- `GET /api/admin/db/slow-queries`：`pg_stat_statements` 的 top N。執行期偵測，不可用時 `available=false` 與
  `reason`（`extension_missing`／`not_preloaded`／`permission_denied`／`timeout`／`error`）。**不寫進 migration、
  程式也不 CREATE EXTENSION**（使用者定案 13：獨立的維護步驟）。查詢文字壓空白後截斷 200 字；查詢文字理論上可能
  帶字面值，所以只開給 `ops.read`。
- `GET /api/admin/db/trends`：讀 `research.db_stat_snapshot`（`scripts/db_snapshot.py` 每小時一列、每日彙總；
  逐時 30 天、每日 400 天，使用者定案 14）。區間起點在逐時保留期內用逐時，否則用每日（回應的 `granularity`）。

SQL 在 `app/services/db_insights.py`，經 `deps.db_insights` 呼叫（測試的替換點）。
輔助函式一律放在 `@router` 裝飾器之上（夾在裝飾器與 handler 之間會讓端點回 422）。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.services.db_insights import SLOW_QUERY_DEFAULT_LIMIT, SLOW_QUERY_MAX_LIMIT, TREND_METRICS
from app.services.ops_monitoring import resolve_window
from web import authz, deps
from web.errors import AppError

router = APIRouter(dependencies=[Depends(authz.require_admin), Depends(authz.require_scope("ops.read"))])

# 與 app/services/db_insights.py 的 ERROR_CODES／SLOW_QUERY_REASONS／SLOW_QUERY_SORTS／TREND_METRICS 一致
# （tests/test_admin_db_api.py 比對）；前端 zod 型別由 gen_admin_client.py 從這裡產生。
SectionErrorCode = Literal["permission_denied", "timeout", "error"]
SlowQueryReason = Literal["extension_missing", "not_preloaded", "permission_denied", "timeout", "error"]
SlowQuerySort = Literal["total", "mean", "calls"]
TrendMetric = Literal[
    "db_size_bytes", "connections_total", "connections_active", "connections_idle_in_tx", "dead_tuple_ratio",
    "table_bytes", "cache_hit_ratio", "temp_bytes", "deadlocks",
]
TrendKind = Literal["gauge", "rate", "ratio"]
Granularity = Literal["hour", "day"]

TREND_DEFAULT_WINDOW = timedelta(days=7)
TREND_MAX_WINDOW = timedelta(days=400)


class SectionError(BaseModel):
    code: SectionErrorCode
    message: str


class DatabaseSection(BaseModel):
    error: SectionError | None = None
    name: str | None = None
    size_bytes: int | None = None
    server_version: str | None = None


class TableStat(BaseModel):
    schema_name: str
    table: str
    total_bytes: int
    row_estimate: int | None = None
    live_tuples: int
    dead_tuples: int
    dead_ratio: float | None = None
    last_autovacuum: str | None = None
    last_vacuum: str | None = None
    last_autoanalyze: str | None = None
    last_analyze: str | None = None


class TablesSection(BaseModel):
    error: SectionError | None = None
    table_count: int | None = None
    live_tuples: int | None = None
    dead_tuples: int | None = None
    dead_ratio: float | None = None
    limit: int | None = None
    items: list[TableStat] = []


class UnusedIndex(BaseModel):
    schema_name: str
    table: str
    index: str
    size_bytes: int
    is_unique: bool
    is_primary: bool


class UnusedIndexesSection(BaseModel):
    error: SectionError | None = None
    count: int | None = None
    total_bytes: int | None = None
    limit: int | None = None
    items: list[UnusedIndex] = []


class ConnectionsSection(BaseModel):
    error: SectionError | None = None
    max_connections: int | None = None
    reserved_connections: int | None = None
    usable_connections: int | None = None
    total: int | None = None
    this_database: int | None = None
    hidden: int | None = None
    by_state: dict[str, int] = {}
    usage_ratio: float | None = None


class ActivitySection(BaseModel):
    error: SectionError | None = None
    blks_hit: int | None = None
    blks_read: int | None = None
    cache_hit_ratio: float | None = None
    temp_files: int | None = None
    temp_bytes: int | None = None
    deadlocks: int | None = None
    xact_commit: int | None = None
    xact_rollback: int | None = None
    stats_reset: str | None = None


class DbOverviewResponse(BaseModel):
    generated_at: str
    statement_timeout_ms: int
    database: DatabaseSection
    tables: TablesSection
    unused_indexes: UnusedIndexesSection
    connections: ConnectionsSection
    activity: ActivitySection


class SlowQueryItem(BaseModel):
    queryid: str | None = None
    query: str | None = None
    query_truncated: bool
    query_hidden: bool
    calls: int
    total_ms: float
    mean_ms: float
    rows: int
    cache_hit_ratio: float | None = None
    temp_blks_written: int


class SlowQueryResponse(BaseModel):
    available: bool
    reason: SlowQueryReason | None = None
    message: str | None = None
    stats_reset: str | None = None
    extension_version: str | None = None
    sort: SlowQuerySort | None = None
    limit: int | None = None
    hidden_count: int = 0
    items: list[SlowQueryItem] = []


class TrendPoint(BaseModel):
    t: str
    value: float | None = None
    min: float | None = None
    max: float | None = None


class DbTrendResponse(BaseModel):
    metric: TrendMetric
    kind: TrendKind
    table: str | None = None
    granularity: Granularity
    since: str
    until: str
    points: list[TrendPoint]
    tables: list[str]


# ── 輔助函式一律放在所有 @router.* 裝飾器之上 ──────────────────────────────


def _trend_window(since: datetime | None, until: datetime | None, now: datetime) -> tuple[datetime, datetime]:
    since, until = resolve_window(since, until, TREND_DEFAULT_WINDOW, now=now)
    if since > until:
        raise AppError(400, "invalid_params", "since 不可晚於 until")
    if until - since > TREND_MAX_WINDOW:
        raise AppError(400, "invalid_params", f"時間範圍最多 {TREND_MAX_WINDOW.days} 天")
    return since, until


@router.get("/api/admin/db/overview", response_model=DbOverviewResponse)
async def get_db_overview():
    """即時快照：庫大小、前 50 大表（列數估計、dead tuple 比例、最後 autovacuum／autoanalyze）、`idx_scan = 0` 的
    索引、連線數（依 state，對照 max_connections 扣保留）、快取命中率／temp／deadlocks（`pg_stat_database` 累計值，
    自 `stats_reset` 起）。每段獨立：失敗的段落只帶 `error`。"""
    async with deps.SessionFactory() as session:
        try:
            return await deps.db_insights.collect_overview(session)
        finally:
            await session.rollback()


@router.get("/api/admin/db/slow-queries", response_model=SlowQueryResponse)
async def list_db_slow_queries(
    limit: int = Query(SLOW_QUERY_DEFAULT_LIMIT, ge=1, le=SLOW_QUERY_MAX_LIMIT),
    sort: SlowQuerySort = Query("total"),
):
    """`pg_stat_statements` 依 `sort`（`total`＝總執行時間、`mean`＝平均、`calls`＝次數）的前 `limit` 筆，只限目前
    這個庫、自 `stats_reset` 起累計。不可用時 `available=false`＋`reason`＋`message`（不是錯誤狀態碼）。"""
    async with deps.SessionFactory() as session:
        try:
            return await deps.db_insights.slow_queries(session, limit=limit, sort=sort)
        finally:
            await session.rollback()


@router.get("/api/admin/db/trends", response_model=DbTrendResponse)
async def get_db_trends(
    metric: TrendMetric = Query(...),
    since: datetime | None = Query(None),
    until: datetime | None = Query(None),
    table: str | None = Query(None, min_length=3, max_length=130, pattern=r"^[A-Za-z0-9_$]+\.[A-Za-z0-9_$]+$"),
):
    """單一指標的趨勢。預設最近 7 天、最多 400 天；沒有時區的時間當 UTC。`table_bytes` 必須帶 `table`
    （`schema.table`，選項見回應的 `tables`＝最近一列快照記錄的前 20 大表）。gauge 取當下值（每日取平均，附
    min／max）；rate 是每小時／每日的增量；`cache_hit_ratio` 是區間內的命中率。範圍顛倒或過長 400。"""
    if metric == "table_bytes" and not table:
        raise AppError(400, "invalid_params", "table_bytes 需要 table 參數（schema.table）")
    now = datetime.now(timezone.utc)
    since_used, until_used = _trend_window(since, until, now)
    granularity = deps.db_insights.pick_granularity(since_used, now)
    async with deps.SessionFactory() as session:
        try:
            points = await deps.db_insights.trend_points(
                session, metric=metric, since=since_used, until=until_used, granularity=granularity,
                table=table if metric == "table_bytes" else None)
            tables = await deps.db_insights.latest_tables(session)
        finally:
            await session.rollback()
    return DbTrendResponse(
        metric=metric, kind=TREND_METRICS[metric], table=table if metric == "table_bytes" else None,
        granularity=granularity, since=since_used.isoformat(), until=until_used.isoformat(),
        points=[TrendPoint(**p) for p in points], tables=tables,
    )
