"""使用分析（`/api/admin/analytics/*`；Admin v2 Analytics lane）：只出彙總的唯讀端點。

授權：router 層掛 `authz.require_admin`＋`analytics.read`（管理員預設 scope，不可授予）；每條端點照
`tests/test_authz.py` 的結構檢查自動繼承。

**只出彙總**（使用者定案 2、3、4；規則與計算在 `app/services/analytics.py`）：
- 標的、研報、市場、搜尋市場與路由類別這類可能回推個人偏好的格子，不重複人數少於 `ANALYTICS_MIN_USERS`（3）一律
  抑制：`suppressed=true`、`value`／`users` 為 null（前端顯示「<3」）。開放詞彙的熱門清單（標的、研報、閱讀、原檔）
  連鍵都不回，只回 `suppressed_count`。只有總量（每日問答數、活躍人數、延遲、上傳審核量）不設門檻。
- 沒有任何依使用者拆分的視圖；回應不含 user_id、帳號名稱、問題或答案文字（`tests/test_analytics_db.py` 掃描
  序列化結果）。問答原文仍只能走待複核的 `qa_content.read` 逐筆路徑。

**範圍**：`since`／`until` 是台北時間的日期（含兩端）；預設最近 30 天，最多 731 天，`until` 晚於今天時截到今天，
顛倒或過長 400 `invalid_params`。最近 `ANALYTICS_LIVE_WINDOW_DAYS`（90）天即時查 `qa_log`／`usage_counter`，更早讀
`analytics_daily`（`scripts/analytics_rollup.py` 每晚寫）；回應的 `range.spans` 標示每段的來源（`live`／`rollup`），
彙總段沒有資料的日子 `has_data=false`。`usage_daily`、`report_upload`、`admin_audit_log` 永久保存，整段直接查。

整份回應快取 60 秒（`web/ttl_cache.py`，鍵含參數與今天的日期）。DB 查不到 503 `db_unavailable`。
SQL 經 `app/services/analytics.py`（以模組物件 `analytics_service` 呼叫：測試以 `admin_analytics.analytics_service`
替換）；`deps.SessionFactory` 是 session 的替換點。輔助函式一律放在 `@router` 裝飾器之上。
"""
from __future__ import annotations

from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError

from app.services import analytics as analytics_service
from web import authz, deps
from web.errors import AppError
from web.ttl_cache import TTLCache

router = APIRouter(dependencies=[Depends(authz.require_admin), Depends(authz.require_scope("analytics.read"))])

_CACHE = TTLCache(ttl=60, max_entries=64, name="admin_analytics")

# 與 app/services/analytics.py 的 SOURCE_*、ROUTE_KEYS 一致；前端 zod 型別由 gen_admin_client.py 從這裡產生。
AnalyticsSource = Literal["live", "rollup"]
RouteName = Literal["path", "decided_by", "llm_model", "llm_error"]


class AnalyticsSpan(BaseModel):
    since: str
    until: str
    source: AnalyticsSource


class AnalyticsRange(BaseModel):
    since: str
    until: str
    today: str
    live_since: str
    timezone: str
    min_users: int
    spans: list[AnalyticsSpan]


class AnalyticsCell(BaseModel):
    """一個可能受 k 門檻抑制的格子。`suppressed=true` 時 `value`／`users` 一律 null。"""

    key: str
    label: str | None = None
    value: int | None = None
    users: int | None = None
    suppressed: bool


class AnalyticsDailyPoint(BaseModel):
    day: str
    source: AnalyticsSource
    has_data: bool
    questions: int | None = None
    askers: int | None = None
    active_users: int | None = None
    reading: int
    report_file: int
    search: int
    latency_p50_ms: float | None = None
    latency_p95_ms: float | None = None
    thinking_p50_ms: float | None = None
    thinking_p95_ms: float | None = None


class AnalyticsOverviewTotals(BaseModel):
    questions: int
    stopped: int
    reading: int
    report_file: int
    search: int
    active_users_peak: int
    distinct_users_live: int | None = None
    missing_days: int


class AnalyticsLatency(BaseModel):
    p50_ms: float | None = None
    p95_ms: float | None = None
    thinking_p50_ms: float | None = None
    thinking_p95_ms: float | None = None
    n: int


class AnalyticsOverviewResponse(BaseModel):
    range: AnalyticsRange
    totals: AnalyticsOverviewTotals
    latency: AnalyticsLatency
    daily: list[AnalyticsDailyPoint]


class AnalyticsDistribution(BaseModel):
    name: RouteName
    suppressible: bool
    cells: list[AnalyticsCell]


class AnalyticsRoutesResponse(BaseModel):
    range: AnalyticsRange
    questions: int
    stopped: int
    llm_truncated: int
    invalid_citation_rows: int
    invalid_citations: int
    distributions: list[AnalyticsDistribution]


class AnalyticsQualityWeek(BaseModel):
    week_start: str
    partial: bool
    questions: int
    checked_all: int
    judge_checked: int
    degraded: int
    below_min: int
    avg_score: float | None = None
    score_n: int
    likes: int
    dislikes: int


class AnalyticsQualityResponse(BaseModel):
    range: AnalyticsRange
    judge_model: str
    faithfulness_min: float
    other_judge_checked: int
    weeks: list[AnalyticsQualityWeek]


class AnalyticsTopList(BaseModel):
    cells: list[AnalyticsCell]
    suppressed_count: int
    truncated: bool


class AnalyticsTopResponse(BaseModel):
    range: AnalyticsRange
    limit: int
    targets: AnalyticsTopList
    reports: AnalyticsTopList
    markets: AnalyticsTopList
    reading: AnalyticsTopList
    report_file: AnalyticsTopList
    search_markets: AnalyticsTopList


class AnalyticsOpsWeek(BaseModel):
    week_start: str
    partial: bool
    uploads_received: int
    uploads_published: int
    uploads_rejected: int
    uploads_infected: int
    uploads_failed: int
    reviews: int
    qa_content_reads: int


class AnalyticsAuditCount(BaseModel):
    action: str
    count: int


class AnalyticsOperationsResponse(BaseModel):
    range: AnalyticsRange
    weeks: list[AnalyticsOpsWeek]
    audit_actions: list[AnalyticsAuditCount]


# ── 輔助函式一律放在所有 @router.* 裝飾器之上 ──────────────────────────────


def reset_caches() -> None:
    """測試用（conftest 的 `ttl_cache.reset_all()` 已涵蓋；這裡給單檔測試直接呼叫）。"""
    _CACHE.clear()


async def _serve(name: str, since: date | None, until: date | None, model, compute, *extra):
    params = analytics_service.params_from_settings()
    try:
        plan = analytics_service.plan_range(since, until, today_=analytics_service.today(),
                                            live_days=params.live_days)
    except analytics_service.RangeError as exc:
        raise AppError(400, "invalid_params", str(exc)) from exc
    key = (name, plan.since, plan.until, plan.today, *extra)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    try:
        async with deps.SessionFactory() as session:
            data = await compute(session, plan, params, *extra)
    except (SQLAlchemyError, OSError) as exc:
        raise AppError(503, "db_unavailable", "資料庫暫時無法使用，請稍後再試") from exc
    response = model(**data)
    _CACHE.put(key, response)
    return response


_SINCE = Query(None, description="起始日（台北時間，含）；預設 until 往前 30 天")
_UNTIL = Query(None, description="結束日（台北時間，含）；預設今天，晚於今天時截到今天")


@router.get("/api/admin/analytics/overview", response_model=AnalyticsOverviewResponse)
async def get_analytics_overview(since: date | None = _SINCE, until: date | None = _UNTIL):
    """總覽：每日問答量、問答人數、活躍人數（問答 ∪ 閱讀／原檔／搜尋／配額計數）、閱讀／原檔／搜尋總量、延遲 p50／p95
    與首字時間。總量不設門檻。範圍層級的延遲只算即時那一段（`latency.n` 是樣本數）。"""
    return await _serve("overview", since, until, AnalyticsOverviewResponse, analytics_service.overview)


@router.get("/api/admin/analytics/top", response_model=AnalyticsTopResponse)
async def get_analytics_top(since: date | None = _SINCE, until: date | None = _UNTIL,
                            limit: int = Query(analytics_service.TOP_DEFAULT_LIMIT, ge=1,
                                               le=analytics_service.TOP_MAX_LIMIT)):
    """熱門標的、研報、市場（問答引用）與閱讀、原檔、搜尋市場（每日主題計數）。全部受 k 門檻約束：
    開放詞彙的清單只回可見項目與 `suppressed_count`；市場清單的被抑制格子保留鍵、`suppressed=true`。"""
    return await _serve("top", since, until, AnalyticsTopResponse, analytics_service.top, limit)


@router.get("/api/admin/analytics/routes", response_model=AnalyticsRoutesResponse)
async def get_analytics_routes(since: date | None = _SINCE, until: date | None = _UNTIL):
    """路由類別（`path`，受 k 門檻約束）、判定者、回答模型、LLM 錯誤種類的分布，以及截斷、無效引用、停止的計數。"""
    return await _serve("routes", since, until, AnalyticsRoutesResponse, analytics_service.routes)


@router.get("/api/admin/analytics/quality", response_model=AnalyticsQualityResponse)
async def get_analytics_quality(since: date | None = _SINCE, until: date | None = _UNTIL):
    """忠實度（只計現行 judge，經 `CURRENT_JUDGE_SQL`）與回饋的週趨勢（週一起算，頭尾不完整的週 `partial=true`）。"""
    return await _serve("quality", since, until, AnalyticsQualityResponse, analytics_service.quality)


@router.get("/api/admin/analytics/operations", response_model=AnalyticsOperationsResponse)
async def get_analytics_operations(since: date | None = _SINCE, until: date | None = _UNTIL):
    """上傳（收檔、發布、退回、感染、失敗）與審核（待複核處理、問答原文調閱）的週量，以及各稽核 action 的次數。"""
    return await _serve("operations", since, until, AnalyticsOperationsResponse, analytics_service.operations)
