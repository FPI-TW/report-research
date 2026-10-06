"""資料健康與 LLM 用量（/api/admin/data-health、/api/admin/llm-usage）。整組限管理員＋`ops.read`，全部唯讀。

- **資料健康**：批次新鮮度即時判讀（`app/services/batch_freshness.py`，四個 `max()`＋心跳檔，與
  `scripts/check_batch_freshness.py` 同一組函式與門檻）；資料完整性稽核與 R2 對帳讀兩支腳本最後一次寫的結果檔
  （`app/services/data_health.py`，`data/health/*.json`）——那兩個檢查太重，刻意不在 web 裡跑。整份回應
  TTL 快取 60 秒（`web/ttl_cache.py`）。
- **LLM 用量**：讀 `data/llm_usage.jsonl`（批次的費用歸因依據），依日期（台北時間）／任務／模型彙總 token 與
  呼叫次數。只回彙總，不含 prompt 雜湊、file_hash、report_id；讀檔有位元組與行數上限，檔案不存在回空結果。
  在 thread 裡讀（不卡 event loop），快取鍵帶檔案大小與 mtime。

服務函式經 `deps.data_health`／`deps.llm_usage` 呼叫（測試的替換點）。
輔助函式一律放在 `@router` 裝飾器之上（夾在裝飾器與 handler 之間會讓端點回 422）。
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.services.ops_monitoring import resolve_window
from web import authz, deps
from web.errors import AppError
from web.ttl_cache import TTLCache

router = APIRouter(dependencies=[Depends(authz.require_admin)])

_OPS_READ = Depends(authz.require_scope("ops.read"))

HealthStatus = Literal["ok", "warn", "fail", "unknown"]
# 與 app/services/batch_freshness.py 的 STATE_* 一致。
FreshnessState = Literal["fresh", "stale", "suppressed", "disabled", "upstream_stale"]
AuditSeverity = Literal["error", "warn"]
OrphanScan = Literal["done", "skipped", "error"]
StorageMode = Literal["local", "r2"]

_HEALTH_CACHE = TTLCache(ttl=60, max_entries=1, name="admin_data_health")
_USAGE_CACHE = TTLCache(ttl=60, max_entries=32, name="admin_llm_usage")


class FreshnessFinding(BaseModel):
    asset: str
    label: str
    state: FreshnessState
    latest: str | None = None
    age_days: float | None = None
    threshold_days: int
    detail: str


class FreshnessSection(BaseModel):
    status: HealthStatus
    exit_code: int
    error: str | None = None
    findings: list[FreshnessFinding]


class AuditFinding(BaseModel):
    key: str
    label: str
    severity: AuditSeverity
    count: int
    detail: str


class DbAuditSection(BaseModel):
    status: HealthStatus
    available: bool
    unavailable_reason: str | None = None
    finished_at: str | None = None
    age_hours: float | None = None
    stale: bool
    exit_code: int | None = None
    error: str | None = None
    skipped: list[str]
    findings: list[AuditFinding]


class ReconcileStats(BaseModel):
    checked: int = 0
    errors: int = 0
    unkeyed: int = 0
    key_mismatch: int = 0
    missing: int = 0
    size_mismatch: int = 0
    sha_mismatch: int = 0
    sha_metadata_missing: int = 0
    orphans: int = 0


class ReconcileIssue(BaseModel):
    type: str
    ref: str


class R2ReconcileSection(BaseModel):
    status: HealthStatus
    available: bool
    unavailable_reason: str | None = None
    finished_at: str | None = None
    age_hours: float | None = None
    stale: bool
    exit_code: int | None = None
    mode: StorageMode | None = None
    dry_run: bool | None = None
    limit: int | None = None
    orphan_scan: OrphanScan | None = None
    stats: ReconcileStats | None = None
    issues: list[ReconcileIssue]
    issues_total: int


class DataHealthResponse(BaseModel):
    generated_at: str
    overall: HealthStatus
    freshness: FreshnessSection
    db_audit: DbAuditSection
    r2_reconcile: R2ReconcileSection


class LlmUsageTotals(BaseModel):
    calls: int
    failures: int
    prompt_hit_tokens: int
    prompt_miss_tokens: int
    completion_tokens: int
    reasoning_tokens: int
    calls_without_tokens: int
    total_ms: int
    cost: float | None = None


class LlmUsageDay(LlmUsageTotals):
    day: str


class LlmUsageTask(LlmUsageTotals):
    task: str


class LlmUsageModel(LlmUsageTotals):
    model: str


class LlmUsageRow(LlmUsageTotals):
    day: str
    task: str
    model: str


class LlmUsageSource(BaseModel):
    exists: bool
    size_bytes: int
    scanned_bytes: int
    truncated: bool
    lines_scanned: int
    lines_invalid: int
    lines_in_range: int
    earliest_ts: str | None = None
    latest_ts: str | None = None


class LlmUsageResponse(BaseModel):
    since: str
    until: str
    timezone: str
    source: LlmUsageSource
    totals: LlmUsageTotals
    by_day: list[LlmUsageDay]
    by_task: list[LlmUsageTask]
    by_model: list[LlmUsageModel]
    rows: list[LlmUsageRow]
    rows_truncated: bool
    cost_available: bool


# ── 輔助函式一律放在所有 @router.* 裝飾器之上 ──────────────────────────────


def reset_caches() -> None:
    """測試用（conftest 的 `ttl_cache.reset_all()` 已涵蓋；這裡給單檔測試直接呼叫）。"""
    _HEALTH_CACHE.clear()
    _USAGE_CACHE.clear()


def _usage_window(since: datetime | None, until: datetime | None) -> tuple[datetime, datetime]:
    since, until = resolve_window(since, until, deps.llm_usage.DEFAULT_WINDOW, now=datetime.now(timezone.utc))
    if since > until:
        raise AppError(400, "invalid_params", "since 不可晚於 until")
    if until - since > deps.llm_usage.MAX_WINDOW:
        raise AppError(400, "invalid_params", f"時間範圍最多 {deps.llm_usage.MAX_WINDOW.days} 天")
    return since, until


@router.get("/api/admin/data-health", response_model=DataHealthResponse, dependencies=[_OPS_READ])
async def get_data_health():
    """批次新鮮度（即時）、資料完整性稽核與 R2 對帳（最後一次結果檔）的彙整。快取 60 秒。

    每段的 `status`：ok／warn／fail／unknown；結果檔比排程週期還舊時 `stale=true` 且狀態至少 warn；
    還沒有結果檔時 `available=false`、狀態 unknown。`overall` 取三段中最嚴重的。
    """
    cached = _HEALTH_CACHE.get("snapshot")
    if cached is not None:
        return cached
    response = DataHealthResponse(**await deps.data_health.snapshot(deps.SessionFactory))
    _HEALTH_CACHE.put("snapshot", response)
    return response


@router.get("/api/admin/llm-usage", response_model=LlmUsageResponse, dependencies=[_OPS_READ])
async def get_llm_usage(since: datetime | None = Query(None), until: datetime | None = Query(None)):
    """批次 LLM 用量的彙總（`[since, until)`；預設最近 30 天、最多 366 天；沒有時區當 UTC）。

    `source.truncated=true` 表示檔案超過讀取上限、只涵蓋 `source.earliest_ts` 之後。檔案不存在回全零。
    """
    since_used, until_used = _usage_window(since, until)
    key = (since_used.isoformat(), until_used.isoformat(), deps.llm_usage.file_signature())
    cached = _USAGE_CACHE.get(key)
    if cached is not None:
        return cached
    summary = await asyncio.to_thread(deps.llm_usage.summarize, since_used, until_used)
    response = LlmUsageResponse(**summary)
    _USAGE_CACHE.put(key, response)
    return response
