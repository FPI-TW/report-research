"""診斷（/api/admin/diagnostics）：這個 web 行程此刻的實際狀態，給管理員看、可整份複製成診斷包。

限管理員＋`ops.read`，唯讀。內容與「便宜、不含祕密」的兩條鐵律在 `app/services/diagnostics.py`；這裡只做
授權、TTL 快取（30 秒，`web/ttl_cache.py`）與回應 model。每一段各自帶 `error`（只記例外型別），某段失敗
整頁照樣 200。R2 與 DeepSeek 只被動讀 healthz 上一次的結論（`health.storage_snapshot`、
`llm_health.cached_snapshot`），診斷頁不會多打付費 API。

服務函式經 `deps.diagnostics` 呼叫（測試的替換點）。輔助函式一律放在 `@router` 裝飾器之上。
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from app.services import db, llm_health
from web import authz, concurrency, deps, ops_client
from web.routers import health as health_routes
from web.ttl_cache import TTLCache

router = APIRouter(dependencies=[Depends(authz.require_admin)])

_OPS_READ = Depends(authz.require_scope("ops.read"))
CACHE_TTL = 30
_CACHE = TTLCache(ttl=CACHE_TTL, max_entries=1, name="admin_diagnostics")

SchemaStatus = Literal["ok", "behind", "ahead", "unversioned", "ambiguous", "error"]
WarmupState = Literal["skipped", "absent", "running", "done", "failed", "cancelled"]


class GitInfo(BaseModel):
    available: bool = False
    reason: str | None = None
    commit_at_start: str | None = None
    branch_at_start: str | None = None
    commit_on_disk: str | None = None
    branch_on_disk: str | None = None
    restart_pending: bool | None = None


class FrontendBuild(BaseModel):
    available: bool = False
    built_at: str | None = None
    entry_assets: list[str] = []


class VersionsSection(BaseModel):
    git: GitInfo | None = None
    frontend: FrontendBuild | None = None
    python: str | None = None
    platform: str | None = None
    packages: dict[str, str | None] = {}
    error: str | None = None


class SchemaDailyCheck(BaseModel):
    available: bool = False
    unavailable_reason: str | None = None
    checked_at: str | None = None
    age_hours: float | None = None
    stale: bool = False
    mode: str | None = None
    exit_code: int | None = None
    alert: bool | None = None
    problems: list[str] = []
    message: str | None = None
    version_status: str | None = None
    drift_status: str | None = None
    drift_count: int | None = None
    error: str | None = None


class SchemaSection(BaseModel):
    status: SchemaStatus
    db_revisions: list[str]
    code_heads: list[str]
    pending: list[str]
    error: str | None = None
    daily_check: SchemaDailyCheck


class TimezoneInfo(BaseModel):
    tz_env: str | None = None
    name: str = ""
    utc_offset: str = ""


class RuntimeSection(BaseModel):
    pid: int | None = None
    hostname: str | None = None
    started_at: str | None = None
    uptime_s: float | None = None
    rss_bytes: int | None = None
    peak_rss_bytes: int | None = None
    threads: int | None = None
    timezone: TimezoneInfo | None = None
    python_executable: str | None = None
    error: str | None = None


class ConfigFlags(BaseModel):
    ask_enable_web: bool
    ask_rerank_enabled: bool
    qa_agentic_enabled: bool
    ask_faithfulness_enabled: bool
    trusted_data_enabled: bool
    skip_warmup: bool
    dev_no_auth: bool


class SecretsPresent(BaseModel):
    deepseek_api_key: bool
    session_secret: bool
    edge_secret: bool
    r2_credentials: bool
    alert_webhook: bool


class ConfigLimits(BaseModel):
    db_pool_size: int
    db_max_overflow: int
    db_pool_timeout_s: float
    db_statement_timeout_ms: int
    db_idle_tx_timeout_ms: int
    embed_max_concurrency: int
    ask_faithfulness_sample_rate: float
    ask_faithfulness_max_inflight: int


class ConfigSection(BaseModel):
    db_target: str | None = None
    object_storage_mode: str | None = None
    llm_provider: str | None = None
    models: dict[str, str] = {}
    extractor: str | None = None
    log_level: str | None = None
    ops_agent_environment: str | None = None
    ops_agent_socket: str | None = None
    flags: ConfigFlags | None = None
    secrets_present: SecretsPresent | None = None
    limits: ConfigLimits | None = None
    error: str | None = None


class ModelsSection(BaseModel):
    embed_model: str | None = None
    embed_loaded: bool = False
    rerank_loaded: bool = False
    rerank_load_failed: bool = False
    warmup: WarmupState | None = None
    warmup_error: str | None = None
    error: str | None = None


class DbPoolSection(BaseModel):
    pool_class: str | None = None
    size: int | None = None
    max_overflow: int | None = None
    checked_out: int | None = None
    checked_in: int | None = None
    open_connections: int | None = None
    error: str | None = None


class GateStatus(BaseModel):
    name: str
    capacity: int
    in_use: int | None = None
    waiting: int
    max_queue: int


class DbCheck(BaseModel):
    ok: bool
    latency_ms: float | None = None
    server_version: str | None = None
    error: str | None = None


class StorageCheck(BaseModel):
    state: str
    consecutive_failures: int = 0
    last_probe_age_s: float | None = None
    last_probe_ok: bool | None = None
    error: str | None = None


class LlmCheck(BaseModel):
    state: str
    key_configured: bool = False
    ask_uses_http: bool = False
    consecutive_failures: int = 0
    last_check_age_s: float | None = None
    quota_latched: bool = False
    error: str | None = None


class OpsAgentCheck(BaseModel):
    ok: bool
    latency_ms: float | None = None
    services: int | None = None
    error: str | None = None


class DiagnosticsChecks(BaseModel):
    db: DbCheck
    storage: StorageCheck
    llm: LlmCheck
    ops_agent: OpsAgentCheck


class DiagnosticsResponse(BaseModel):
    generated_at: str
    cache_ttl_s: int
    versions: VersionsSection
    schema_info: SchemaSection
    runtime: RuntimeSection
    config: ConfigSection
    models: ModelsSection
    db_pool: DbPoolSection
    gates: list[GateStatus]
    gates_error: str | None = None
    checks: DiagnosticsChecks


# ── 輔助函式一律放在所有 @router.* 裝飾器之上 ──────────────────────────────


def reset_caches() -> None:
    """測試用（conftest 的 `ttl_cache.reset_all()` 已涵蓋；這裡給單檔測試直接呼叫）。"""
    _CACHE.clear()


async def _build(request: Request) -> DiagnosticsResponse:
    raw = await deps.diagnostics.snapshot(
        session_factory=deps.SessionFactory,
        engine=db.engine,
        warmup_task=getattr(request.app.state, "embed_warmup_task", None),
        gates=concurrency.registered_gates(),
        storage_snapshot=health_routes.storage_snapshot,
        llm_snapshot=llm_health.cached_snapshot,
        ops_client_factory=ops_client.default_client,
    )
    return DiagnosticsResponse(cache_ttl_s=CACHE_TTL, **raw)


@router.get("/api/admin/diagnostics", response_model=DiagnosticsResponse, dependencies=[_OPS_READ])
async def get_diagnostics(request: Request):
    """web 行程的版本、schema、執行環境、白名單設定、模型與連線池、便宜的連通性檢查。快取 30 秒。

    不含任何祕密：設定只列白名單、祕密只回有沒有設、DB 只留 host:port/db，整份再過一次 scrub。
    某段失敗只讓該段帶 `error`（例外型別），整頁 200。
    """
    cached = _CACHE.get("snapshot")
    if cached is not None:
        return cached
    response = await _build(request)
    _CACHE.put("snapshot", response)
    return response
