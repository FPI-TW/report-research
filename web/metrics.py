"""應用層監控訊號 `GET /metrics`（Prometheus 文字格式）：券商自己的 Prometheus／Grafana、Zabbix、ELK 接得上。

指標清單、意義與建議告警規則寫在 `docs/CONTAINER_DEPLOYMENT.md`「監控對接」；這裡只寫實作上的約束。

## 標籤的兩條鐵律（`tests/test_metrics.py` 以 HTTP 層斷言）

1. **有界**：HTTP 指標的 `route` 是**路由模板**（`/api/reading/{report_id}`），從 FastAPI 比對結果
   （`scope["route"]`）取；沒進到路由的請求（認證 401、CSRF 403、找不到路由的 404、靜態檔 Mount）以同一份路由表
   重新比對一次取模板，比不到一律 `<unmatched>`——**絕不用原始路徑**。method 不在已知集合就是 `OTHER`；LLM 的
   task／provider／kind 都對照各自的封閉詞彙（`llm_models.TASK_ENV`、`llm_models.PROVIDERS`、`llm_http` 的失敗分類），
   認不得就是 `other`。
2. **不含敏感資料**：沒有查詢字串、沒有使用者識別（帳號、IP、session）、沒有問答內容、沒有研報 id。

## 存取控制（`access_allowed`）

`/metrics` 在認證白名單裡（Prometheus 不登入），所以由路由自己擋，不通過一律 **404**（與不存在的路由無從分辨，
比照 `/healthz/storage`）：
- `METRICS_ENABLED=0` → 關。
- 帶任何代理 header（`X-Forwarded-For`、`X-Real-IP`、`Forwarded`、`CF-Connecting-IP`、`X-Edge-Secret`…，
  `web/dev_mode.has_proxy_headers`）→ 經反向代理進來的就是外部請求，一律不收。
- 對端位址必須落在 `METRICS_ALLOWED_CIDRS`（預設只有 loopback）。容器部署讓 Prometheus 從同一個 compose 網路直接
  抓 web 容器的 8097，把那個網段加進來。

反向代理若與 web 在同一台、又**不設** `X-Forwarded-For`，對端會是 loopback：部署方的代理要照常帶這個 header，或
直接在代理上擋掉 `/metrics`（文件「監控對接」）。

## 多行程

計數器是**行程內**狀態（prometheus_client 的預設 registry 模式，刻意不用它的 multiprocess 模式）。成立的前提是
web 只有一個 worker——`web/concurrency.assert_single_worker` 在啟動時擋多 worker（fail-closed）。批次、上傳 worker、
排程是別的行程，它們的 LLM 呼叫**不在**這裡的 LLM 指標內（只有 web 行程的線上呼叫）；批次的健康改看
`report_mark_batch_freshness_*`（從 DB 算，與行程無關）與 webhook 告警。web 重啟時計數器歸零，Prometheus 的
`rate()`／`increase()` 本來就處理 counter reset。

## 從 DB 算的值

批次新鮮度與上傳佇列在抓取時查 DB（`app/services/monitoring_signals.py`），結果快取 `DB_SIGNAL_TTL` 秒、每次查詢
`DB_SIGNAL_TIMEOUT` 秒逾時；查不到時 `report_mark_signal_source_up{source}` 為 0、該組指標清空（不留舊值冒充現況），
`/metrics` 照樣回 200。
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import math
import time
from collections.abc import Iterable

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    disable_created_metrics,
    generate_latest,
)
from prometheus_client.core import GaugeMetricFamily
from prometheus_client.gc_collector import GCCollector
from prometheus_client.platform_collector import PlatformCollector
from prometheus_client.process_collector import ProcessCollector

from app.config import get_settings
from app.services import llm_http, llm_models
from web import dev_mode

logger = logging.getLogger(__name__)

CONTENT_TYPE = CONTENT_TYPE_LATEST
PREFIX = "report_mark"
UNMATCHED = "<unmatched>"
OTHER = "other"
METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"})
DB_SIGNAL_TTL = 15.0
DB_SIGNAL_TIMEOUT = 5.0

# 問答的結局（/api/ask）：ok＝串流跑完；llm_error＝LLM 不可用（類別另見 qa_llm_errors）；error＝其他例外；
# cancelled＝使用者中途斷線或停止；queue_full＝排隊已滿回 429（沒有進串流）。
QA_OUTCOMES = ("ok", "llm_error", "error", "cancelled", "queue_full")
LLM_KINDS = frozenset({
    llm_http.AUTH, llm_http.QUOTA, llm_http.CONFIG, llm_http.CONTENT_FILTER, llm_http.BAD_REQUEST,
    llm_http.OVERLOADED, llm_http.NETWORK, llm_http.TIMEOUT, llm_http.TRUNCATED, llm_http.TIMEOUT_STREAMED,
    llm_http.EMPTY, llm_http.INVALID_JSON, llm_http.OTHER,
})
# observer metadata 的 tokens 鍵（llm_http.call_metadata）→ label 值。reasoning 是 completion 的一部分。
TOKEN_TYPES = {"hit": "prompt_cached", "miss": "prompt_uncached", "completion": "completion", "reasoning": "reasoning"}

# counter 的 `*_created` 樣本只是「這個 label 組合第一次出現的時刻」，對告警沒有用、序列數卻翻倍。
disable_created_metrics()
REGISTRY = CollectorRegistry(auto_describe=True)
ProcessCollector(registry=REGISTRY)
PlatformCollector(registry=REGISTRY)
GCCollector(registry=REGISTRY)

HTTP_REQUESTS = Counter(
    f"{PREFIX}_http_requests", "HTTP 請求數（route＝路由模板）", ["method", "route", "status"], registry=REGISTRY)
HTTP_DURATION = Histogram(
    f"{PREFIX}_http_request_duration_seconds", "HTTP 請求耗時（SSE 為整條串流的長度）", ["method", "route"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300), registry=REGISTRY)
HTTP_IN_PROGRESS = Gauge(f"{PREFIX}_http_requests_in_progress", "處理中的 HTTP 請求數（含 SSE）", registry=REGISTRY)
QA_REQUESTS = Counter(f"{PREFIX}_qa_requests", "問答（/api/ask）的結局", ["outcome"], registry=REGISTRY)
QA_LLM_ERRORS = Counter(f"{PREFIX}_qa_llm_errors", "問答因 LLM 不可用而失敗，依失敗類別", ["kind"], registry=REGISTRY)
LLM_CALLS = Counter(
    f"{PREFIX}_llm_calls", "web 行程的 LLM 呼叫數（result＝ok／error）", ["task", "provider", "result"],
    registry=REGISTRY)
LLM_FAILURES = Counter(
    f"{PREFIX}_llm_call_failures", "web 行程的 LLM 呼叫失敗，依失敗類別", ["task", "provider", "kind"],
    registry=REGISTRY)
LLM_DURATION = Histogram(
    f"{PREFIX}_llm_call_duration_seconds", "web 行程的 LLM 呼叫耗時（含傳輸層重試）", ["task", "provider"],
    buckets=(0.25, 0.5, 1, 2, 5, 10, 20, 30, 60, 120, 300, 600), registry=REGISTRY)
LLM_TOKENS = Counter(
    f"{PREFIX}_llm_tokens", "web 行程的 LLM token 用量（供應商回報的 usage）", ["task", "provider", "type"],
    registry=REGISTRY)
SOURCE_UP = Gauge(f"{PREFIX}_signal_source_up", "從 DB 算的訊號這次查不查得到（1／0）", ["source"], registry=REGISTRY)
FRESHNESS_AGE = Gauge(
    f"{PREFIX}_batch_freshness_age_seconds", "批次資產最新產出距今（NaN＝從未產出）", ["asset"], registry=REGISTRY)
FRESHNESS_THRESHOLD = Gauge(
    f"{PREFIX}_batch_freshness_threshold_seconds", "批次資產的停更門檻（0＝不告警）", ["asset"], registry=REGISTRY)
FRESHNESS_STALE = Gauge(
    f"{PREFIX}_batch_freshness_stale", "批次資產停更（1）或正常／被抑制／不告警（0）；判定同 check_batch_freshness",
    ["asset"], registry=REGISTRY)
UPLOAD_QUEUE = Gauge(f"{PREFIX}_upload_queue", "研報上傳依狀態的筆數", ["state"], registry=REGISTRY)
UPLOAD_OLDEST = Gauge(
    f"{PREFIX}_upload_oldest_in_flight_age_seconds", "最舊一筆處理中上傳已等待的秒數（0＝沒有）", registry=REGISTRY)


# ── 標籤正規化 ─────────────────────────────────────────────────────────────


def _bounded(value, allowed: Iterable[str]) -> str:
    return value if isinstance(value, str) and value in allowed else OTHER


def task_label(task) -> str:
    return _bounded(task, llm_models.TASK_ENV)


def provider_label(provider) -> str:
    return _bounded(provider, llm_models.PROVIDERS)


def kind_label(kind) -> str:
    return _bounded(kind, LLM_KINDS)


def method_label(method) -> str:
    return method if method in METHODS else "OTHER"


# ── 路由模板 ───────────────────────────────────────────────────────────────

_ROUTES_CACHE: dict[int, list] = {}


def _flat_routes(app) -> list:
    """攤平的路由（FastAPI 0.137 起 app.routes 是樹，見 tests/test_router_helpers.py）；每個 app 算一次。"""
    key = id(app)
    cached = _ROUTES_CACHE.get(key)
    if cached is not None:
        return cached
    routes = list(getattr(app, "routes", []) or [])
    try:
        from fastapi.routing import iter_route_contexts  # FastAPI ≥ 0.137.2

        flat = [(ctx.path, ctx.route) for ctx in iter_route_contexts(routes)]
    except ImportError:
        flat = [(getattr(r, "path", None), r) for r in routes]
    flat = [(path, route) for path, route in flat if isinstance(path, str) and hasattr(route, "matches")]
    _ROUTES_CACHE[key] = flat
    return flat


def _match_template(app, probe: dict) -> str:
    from starlette.routing import Match

    partial = None
    for path, route in _flat_routes(app):
        try:
            match, _child = route.matches(probe)
        except Exception:  # noqa: BLE001 — 指標不能讓請求失敗
            continue
        if match == Match.FULL:
            return path
        if match == Match.PARTIAL and partial is None:
            partial = path
    return partial or UNMATCHED


def route_label(scope: dict, probe: dict) -> str:
    route = scope.get("route")
    path = getattr(route, "path", None)
    if isinstance(path, str) and path:
        return path
    app = scope.get("app")
    if app is None:
        return UNMATCHED
    try:
        return _match_template(app, probe)
    except Exception:  # noqa: BLE001
        return UNMATCHED


class MetricsMiddleware:
    """純 ASGI（理由同 web/request_log.py）：每個 HTTP 請求記一次數量與耗時。排在最外層，401／302／500 也算到。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        # 路由比對前的原樣：Mount 會改 scope 的 root_path，事後重新比對要用進來時的樣子。
        probe = {"type": "http", "path": scope.get("path", ""), "root_path": scope.get("root_path", ""),
                 "method": scope.get("method", "GET"), "headers": [], "query_string": b""}
        status = 500
        t0 = time.monotonic()
        HTTP_IN_PROGRESS.inc()

        async def send_wrapper(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            HTTP_IN_PROGRESS.dec()
            try:
                method = method_label(scope.get("method"))
                route = route_label(scope, probe)
                HTTP_REQUESTS.labels(method, route, str(int(status))).inc()
                HTTP_DURATION.labels(method, route).observe(time.monotonic() - t0)
            except Exception:  # noqa: BLE001
                logger.debug("HTTP 指標記錄失敗", exc_info=True)


# ── 問答與 LLM ─────────────────────────────────────────────────────────────


def record_qa(outcome: str, llm_kind: str | None = None) -> None:
    try:
        QA_REQUESTS.labels(outcome if outcome in QA_OUTCOMES else "error").inc()
        if outcome == "llm_error":
            QA_LLM_ERRORS.labels(kind_label(llm_kind)).inc()
    except Exception:  # noqa: BLE001
        logger.debug("問答指標記錄失敗", exc_info=True)


def observe_llm_call(meta: dict) -> None:
    """`llm_http.add_observer` 註冊的函式（web/server.py 的 lifespan）。

    meta 只有 metadata（`llm_http.call_metadata`）：沒有 prompt、回答、金鑰。
    """
    task, provider = task_label(meta.get("task")), provider_label(meta.get("provider"))
    kind = meta.get("kind")
    LLM_CALLS.labels(task, provider, "ok" if kind is None else "error").inc()
    if kind is not None:
        LLM_FAILURES.labels(task, provider, kind_label(kind)).inc()
    total_ms = meta.get("total_ms")
    if isinstance(total_ms, (int, float)) and total_ms >= 0:
        LLM_DURATION.labels(task, provider).observe(total_ms / 1000.0)
    tokens = meta.get("tokens")
    if isinstance(tokens, dict):
        for key, label in TOKEN_TYPES.items():
            value = tokens.get(key)
            if isinstance(value, int) and value > 0:
                LLM_TOKENS.labels(task, provider, label).inc(value)


# ── 抓取當下才算的值（便宜、行程內） ──────────────────────────────────────


class _SnapshotCollector:
    """DB 連線池、併發閘、LLM 批次斷路器、建置資訊：每次抓取現算（都不碰 DB）。"""

    def describe(self):
        return []

    def collect(self):
        yield from _pool_metrics()
        yield from _gate_metrics()
        from app.services import monitoring_signals

        breaker = GaugeMetricFamily(f"{PREFIX}_llm_breaker_active", "LLM 批次斷路器標記在有效期內（1／0）")
        breaker.add_metric([], 1.0 if monitoring_signals.breaker_active() else 0.0)
        yield breaker
        yield _build_metric()


def _pool_metrics():
    try:
        from app.services import db, diagnostics

        p = diagnostics.pool_section(db.engine)
    except Exception:  # noqa: BLE001
        return
    for key, help_text in (("size", "DB 連線池大小（DB_POOL_SIZE）"), ("max_overflow", "DB 連線池可超出的連線數"),
                           ("checked_out", "借出中的 DB 連線"), ("checked_in", "閒置在池裡的 DB 連線")):
        fam = GaugeMetricFamily(f"{PREFIX}_db_pool_{key}", help_text)
        fam.add_metric([], float(p[key]))
        yield fam


def _gate_metrics():
    from web import concurrency

    in_use = GaugeMetricFamily(f"{PREFIX}_gate_in_use", "併發閘使用中的名額", labels=["gate"])
    capacity = GaugeMetricFamily(f"{PREFIX}_gate_capacity", "併發閘的名額上限", labels=["gate"])
    waiting = GaugeMetricFamily(f"{PREFIX}_gate_waiting", "併發閘排隊中的請求", labels=["gate"])
    # 同名的閘門（理論上不會有；測試會建）依名稱加總：同一組標籤出現兩次，Prometheus 會拒收整份抓取。
    totals: dict[str, list[float]] = {}
    for g in concurrency.registered_gates():
        free = getattr(getattr(g, "_sem", None), "_value", None)
        t = totals.setdefault(str(g.name), [0.0, 0.0, 0.0])
        t[0] += float(g.capacity - free) if isinstance(free, int) else 0.0
        t[1] += float(g.capacity)
        t[2] += float(g.waiting)
    for name, (used, cap, wait) in totals.items():
        in_use.add_metric([name], used)
        capacity.add_metric([name], cap)
        waiting.add_metric([name], wait)
    yield from (in_use, capacity, waiting)


def _build_metric():
    from app.services import diagnostics

    info = diagnostics.build_section()
    fam = GaugeMetricFamily(f"{PREFIX}_build_info", "正在跑的程式（恆為 1；版本對帳看 label）",
                            labels=["commit", "version", "tree_clean", "source"])
    clean = info.get("tree_clean")
    fam.add_metric([info.get("commit") or "", info.get("version") or "",
                    "" if clean is None else str(bool(clean)).lower(), info.get("source") or ""], 1.0)
    return fam


REGISTRY.register(_SnapshotCollector())


# ── 從 DB 算的值 ───────────────────────────────────────────────────────────

_db_refreshed_at = 0.0


def _session_factory():
    from app.services import db

    return db.SessionFactory


def _seconds(asset: str, value, unit: float) -> float:
    return float(value) * unit if isinstance(value, (int, float)) else math.nan


def _apply_freshness(result: dict) -> None:
    FRESHNESS_AGE.clear()
    FRESHNESS_THRESHOLD.clear()
    FRESHNESS_STALE.clear()
    for f in result.get("findings") or []:
        asset = f.get("asset")
        if asset not in ("pipeline", "corpus", "summary", "takeaway", "signal"):
            continue
        # 管線心跳的門檻與年齡以小時給（batch_freshness.assess_pipeline 的 threshold_days 欄放的是小時數）。
        FRESHNESS_AGE.labels(asset).set(_seconds(asset, f.get("age_days"), 86400.0))
        threshold_unit = 3600.0 if asset == "pipeline" else 86400.0
        FRESHNESS_THRESHOLD.labels(asset).set(_seconds(asset, f.get("threshold_days"), threshold_unit))
        FRESHNESS_STALE.labels(asset).set(1.0 if f.get("state") in ("stale", "upstream_stale") else 0.0)
    SOURCE_UP.labels("freshness").set(0.0 if result.get("error") else 1.0)


async def _refresh_freshness() -> None:
    from app.services import monitoring_signals

    try:
        result = await asyncio.wait_for(monitoring_signals.freshness(_session_factory()), DB_SIGNAL_TIMEOUT)
    except Exception as exc:  # noqa: BLE001
        logger.warning("metrics：批次新鮮度查不到（%s）", type(exc).__name__)
        FRESHNESS_AGE.clear()
        FRESHNESS_THRESHOLD.clear()
        FRESHNESS_STALE.clear()
        SOURCE_UP.labels("freshness").set(0.0)
        return
    _apply_freshness(result)


async def _refresh_uploads() -> None:
    from app.services import monitoring_signals

    async def _query():
        async with _session_factory()() as session:
            return await monitoring_signals.upload_queue(session)

    try:
        result = await asyncio.wait_for(_query(), DB_SIGNAL_TIMEOUT)
    except Exception as exc:  # noqa: BLE001
        logger.warning("metrics：上傳佇列查不到（%s）", type(exc).__name__)
        UPLOAD_QUEUE.clear()
        UPLOAD_OLDEST.set(math.nan)
        SOURCE_UP.labels("uploads").set(0.0)
        return
    for state, n in result["counts"].items():
        UPLOAD_QUEUE.labels(state).set(float(n))
    UPLOAD_OLDEST.set(float(result["oldest_in_flight_age_s"] or 0.0))
    SOURCE_UP.labels("uploads").set(1.0)


async def refresh_db_signals(*, force: bool = False) -> None:
    global _db_refreshed_at
    now = time.monotonic()
    if not force and _db_refreshed_at and now - _db_refreshed_at < DB_SIGNAL_TTL:
        return
    _db_refreshed_at = now
    await _refresh_freshness()
    await _refresh_uploads()


def reset_db_cache() -> None:
    """測試用：下一次抓取一定重查（tests/conftest.py 每題前後呼叫）。"""
    global _db_refreshed_at
    _db_refreshed_at = 0.0


# ── 存取控制與輸出 ─────────────────────────────────────────────────────────


def _networks(cidrs: Iterable[str]) -> list:
    out = []
    for cidr in cidrs:
        try:
            out.append(ipaddress.ip_network(cidr, strict=False))
        except ValueError:
            continue
    return out


def access_allowed(request) -> bool:
    """模組 docstring「存取控制」。"""
    settings = get_settings()
    if not settings.metrics_enabled:
        return False
    if dev_mode.has_proxy_headers(request.headers):
        return False
    peer = request.client.host if request.client else None
    try:
        addr = ipaddress.ip_address(peer or "")
    except ValueError:
        return False
    if getattr(addr, "ipv4_mapped", None) is not None:
        addr = addr.ipv4_mapped
    return any(addr in net for net in _networks(settings.metrics_allowed_cidrs))


def render() -> bytes:
    return generate_latest(REGISTRY)
