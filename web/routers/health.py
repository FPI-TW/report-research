# web/routers/health.py
"""存活探測：`GET /healthz`（**免認證**，回報 DB 可用性）。

## 為什麼需要它

本站的認證是 deny-by-default middleware，且**登入路徑完全不碰 DB**
（`web/auth.py` 無任何 DB 相依）。於是 DB 掛掉時的故障型態是最糟的一種
「假活著」：站台開得起來、登入還會成功、每一個查詢 500。

而在此之前**沒有任何探測能分辨**——`/healthz` 與 `/zzz-nonexistent` 都回 302
（被 auth middleware 導向 /login），外部監控看到的都是「有回應」。`/api/stats`
在 auth 之後且經 TTL 快取，也代替不了。

## 設計上的取捨（這是唯一免認證且對外可達的資料端點）

- **不洩漏任何資訊**：只回 `{"status": "ok"|"degraded"}`。不含 DB 版本、錯誤訊息、
  主機名、堆疊。探測失敗的細節只進 server 日誌。
- **抗打**：探測結果快取 `_TTL` 秒。免認證端點會被掃描器與監控同時打，沒有快取
  等於把 DB 往返暴露給任何人。快取讓最壞情況是每 `_TTL` 秒一次 `SELECT 1`。
- **有界**：探測包 `asyncio.timeout`，DB hang 住時回 degraded 而不是把連線耗在這裡。
- **語意正確**：健康 200、DB 不可用 **503**，讓 uptime 監控與負載平衡器能正確動作
  （回 200 帶 degraded 欄位的話，多數監控預設仍判定為健康）。

## `/healthz/storage`：物件儲存探測為什麼是另一支端點

`OBJECT_STORAGE_MODE=r2` 時 `has_file` 只認 object key、缺 key 即 503 不回退；bucket 或憑證
出問題時原檔與 PDF 全壞，而 `/healthz` 照樣綠。在這支端點之前，唯一的偵測是每週一次的
對帳 timer，最壞延遲七天。

不併進 `/healthz` 有兩個理由：

- **`/healthz` 的狀態碼是給外部監控與負載平衡器看的**。R2 掛掉時檢索、問答、雷達、簡報都
  還活著，回 503 會讓它們把一個活著的站判成死的。
- **`/healthz` 的回應只有 `status` 一個鍵是釘死的不變量**（對外免認證端點不洩漏任何資訊）。

所以另開一支、而且只回答**本機直連**的請求（`dev_mode.is_direct_loopback`：對端 loopback、
無代理 header、Host 是本機）。經邊緣進來的請求一律 404，與不存在的路由無從分辨——它在
auth 白名單裡，但對外等於不存在。消費端是 `scripts/check_web_health.sh`（本來就打 127.0.0.1）。

抗打的方式與上面相同但時間尺度不同：成功快取 5 分鐘（每次探測是一個計費的 R2 list 操作，
每月約 8,600 次），失敗快取 60 秒並要求連續兩次才翻 degraded。

## `/healthz/llm`：DeepSeek 帳號（餘額、金鑰、連線）

同樣的理由另開、同樣只回答本機直連（其餘 404）、同樣在白名單裡：問答與批次的 LLM 段全靠 DeepSeek，
402／401 時問答每題失敗而 `/healthz` 照樣綠。回應只有 `{"llm": state}`，**不回任何金額**（金額只進
日誌）。狀態、門檻、快取、402 閂鎖與審查 M15 的 `_unused` 規則都在 `app/services/llm_health.py`；
消費端是探針（只認 503，`low` 為退出碼 7、其餘為 8）。

## `/healthz/security`：安全事件的狀態型告警（Admin v2）

同樣另開、同樣只回答本機直連（其餘 404）、同樣在白名單裡。回應只有 `{"security": state}`，**不回任何事件細節**
（計數與帳號在 `/api/admin/security/alerts`，要登入＋`audit.read`）。判斷是 `security_ops.evaluate_alerts`：
`SECURITY_WINDOW_MINUTES`（15）分鐘內全站登入失敗（含被限流擋下的請求）達 `SECURITY_LOGIN_FAILURE_THRESHOLD`（20）、
同一帳號「上次登入成功之後」連續失敗達 `SECURITY_ACCOUNT_FAILURE_THRESHOLD`（5）、權限提升失敗達
`SECURITY_ELEVATE_FAILURE_THRESHOLD`（3）、稽核鏈驗證失敗（結果快取 `AUDIT_VERIFY_CACHE_SECONDS`）。
state：`ok`（200）、`unknown`（判不出來，200）、`audit_chain_broken`／`elevate_failures`／`account_failures`／
`login_failures`（503；多項同時成立回最前面那一個）。結論快取 `_SECURITY_TTL` 秒、整個判斷最多 `_SECURITY_WAIT` 秒
（逾時＝unknown）。每次重算前順手把到期的限流彙總落庫（探針每 2 分鐘打一次，等於彙總的定期 flush）。
消費端是 `scripts/check_security_health.sh`（接 P5 instance `report-mark-security-incident`）：只送狀態型告警
（開場／升級／恢復），不為每筆登入事件發 webhook（使用者定案 9）。

## `/api/status`：給一般使用者的粗粒度系統狀態（需登入）

主平台的小燈號用。**任何登入使用者可讀**（不在免登入白名單、不限管理員），所以回應只有
`{"status": "ok"|"degraded"|"unknown", "message": 一句中文}`，刻意不含服務清單、主機名、環境、
錯誤細節——那些只在 `/api/admin/ops/*`（管理員＋`ops.read`）。判斷順序：

1. DB 探測（與 `/healthz` 共用同一份 5 秒快取）失敗 → `degraded`。
2. 問維運代理的 `list`（逾時壓到 `_STATUS_AGENT_TIMEOUT`）：代理不可用、拒絕或格式不符 → `unknown`
   （fail-open：回 200，不是錯誤；細節只進日誌）。
3. 只看 `tier = critical` 的服務：任一 `failed`／`not_found`／`idle`（核心服務都是常駐的，停著就是壞了，
   例如 Docker 重啟後停在 Exited 的 nginx）→ `degraded`；否則任一 `unknown` → `unknown`；
   其餘（`running`／`transitioning`）→ `ok`。important／supporting 層不影響使用者看到的燈號。

代理那一段的結論以 `web.ttl_cache` 快取 `_STATUS_TTL` 秒（每個使用者的每一頁都會打，不能每次都問代理；
conftest 每題前後 `reset_all()`）。狀態碼一律 200：這是資訊，不是探活。
"""
import asyncio
import logging
import time
from typing import NamedTuple

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.config import get_settings
from app.services import llm_health, security_ops
from app.services.object_storage import get_object_storage
from web import deps, dev_mode, ops_client
from web.ttl_cache import TTLCache

logger = logging.getLogger(__name__)

router = APIRouter()

# 探測快取：免認證端點必須假設會被高頻打。TTL 內共用同一次探測結果。
_TTL = 5.0
_PROBE_TIMEOUT = 3.0
_cache: tuple[float, bool] = (0.0, False)


class _StorageState(NamedTuple):
    state: str  # unknown（尚無完成的探測）｜ok｜degraded
    consecutive_failures: int
    expires_at: float


_STORAGE_OK_TTL = 300.0
_STORAGE_FAIL_TTL = 60.0
_STORAGE_FAILS_TO_DEGRADE = 2
_STORAGE_WAIT = 4.0
_STORAGE_INITIAL = _StorageState("unknown", 0, 0.0)

# /api/status：代理那一段的結論快取（key 固定）；代理逾時壓短，別讓主平台的小燈號等 20 秒。
_STATUS_TTL = 30.0
_STATUS_AGENT_TIMEOUT = 3.0
_STATUS_CACHE = TTLCache(ttl=_STATUS_TTL, max_entries=1, name="api_status_ops")
_STATUS_MESSAGES = {
    "ok": "系統運作正常",
    "degraded": "部分核心服務異常，檢索或問答可能暫時受影響",
    "unknown": "暫時無法取得完整的系統狀態",
}
_DB_DEGRADED_MESSAGE = "資料庫連線異常，檢索或問答可能暫時無法使用"
# 核心服務停著（idle）也算壞：catalog 的 critical 層都是常駐服務。
_CRITICAL_BAD = frozenset({"failed", "not_found", "idle"})
# /healthz/security：結論快取（key 固定）與整個判斷的時限（探針的 curl 逾時是 10 秒）。
_SECURITY_TTL = 30.0
_SECURITY_WAIT = 8.0
_SECURITY_CACHE = TTLCache(ttl=_SECURITY_TTL, max_entries=1, name="healthz_security")
_storage: _StorageState = _STORAGE_INITIAL
_storage_task: asyncio.Task | None = None


async def _probe_db() -> bool:
    """`SELECT 1` 探測；任何例外或逾時皆視為不健康（不外洩原因）。"""
    try:
        async with asyncio.timeout(_PROBE_TIMEOUT):
            async with deps.SessionFactory() as session:
                await session.execute(text("SELECT 1"))
        return True
    except asyncio.CancelledError:
        raise
    except Exception:
        # 細節只進日誌,不進回應——這是對外免認證端點
        logger.warning("healthz DB 探測失敗", exc_info=True)
        return False


async def _db_ok() -> bool:
    """DB 探測結果（`_TTL` 秒快取）；`/healthz` 與 `/api/status` 共用。"""
    global _cache
    now = time.monotonic()
    ts, ok = _cache
    if now - ts >= _TTL:
        ok = await _probe_db()
        _cache = (now, ok)
    return ok


async def _critical_services_state() -> str:
    """問維運代理、只看 critical 層，回 ok／degraded／unknown。永不拋例外（fail-open）。"""
    cached = _STATUS_CACHE.get("critical")
    if cached is not None:
        return cached
    state = "unknown"
    try:
        client = ops_client.default_client()
        client.timeout = min(client.timeout, _STATUS_AGENT_TIMEOUT)
        result = await client.request("list")
        summaries = [
            item.get("summary") for item in result.get("items", [])
            if isinstance(item, dict) and item.get("tier") == "critical"
        ]
        if any(s in _CRITICAL_BAD for s in summaries):
            state = "degraded"
        elif summaries and all(s in ("running", "transitioning") for s in summaries):
            state = "ok"
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # 代理不可用、拒絕、格式不符：都只是「不知道」，細節只進日誌
        logger.info("/api/status 取不到維運代理狀態：%s", exc)
    _STATUS_CACHE.put("critical", state)
    return state


async def _security_state() -> str:
    """安全事件的狀態字串（`security_ops.HEALTH_STATES`）。永不拋例外：逾時或任何失敗都是 unknown。"""
    cached = _SECURITY_CACHE.get("state")
    if cached is not None:
        return cached
    try:
        async with asyncio.timeout(_SECURITY_WAIT):
            await security_ops.flush_tallies(deps.accounts)
            ev = await security_ops.evaluate_alerts(deps.accounts.verify_audit_chain)
        state = ev.state
        if ev.triggered:
            # 只有計數與門檻（沒有 IP、帳號）：P5 的通知只說「哪一條」，細節到安全頁看。
            logger.warning(
                "安全告警 state=%s triggered=%s window=%smin login_failures=%s/%s accounts_over=%s "
                "elevate_failures=%s/%s audit_chain=%s",
                state, ",".join(ev.triggered), ev.window_minutes, ev.login_failures, ev.login_failure_threshold,
                len(ev.accounts_over), ev.elevate_failures, ev.elevate_failure_threshold, ev.audit_chain.state,
            )
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.warning("healthz 安全狀態判斷失敗或逾時（視為 unknown）", exc_info=True)
        state = security_ops.STATE_UNKNOWN
    _SECURITY_CACHE.put("state", state)
    return state


async def _refresh_storage() -> None:
    """跑一次物件儲存探測並更新 `_storage`。永不拋例外（它是 fire-and-forget 的 task）。"""
    global _storage
    try:
        await asyncio.to_thread(get_object_storage().ping)
        _storage = _StorageState("ok", 0, time.monotonic() + _STORAGE_OK_TTL)
    except asyncio.CancelledError:
        raise
    except Exception:
        fails = _storage.consecutive_failures + 1
        # 細節只進日誌。單次失敗不翻成 degraded（boto 自己已重試 3 次，但一次網路抖動
        # 不該開一個事件）；連續兩次才算，且失敗後縮短重探間隔，讓第二次很快到。
        logger.warning("healthz 物件儲存探測失敗（連續第 %d 次）", fails, exc_info=True)
        state = "degraded" if fails >= _STORAGE_FAILS_TO_DEGRADE else _storage.state
        _storage = _StorageState(state, fails, time.monotonic() + _STORAGE_FAIL_TTL)


async def _storage_state() -> str:
    """目前的物件儲存狀態；過期就在背景重探，最多等 `_STORAGE_WAIT` 秒。

    探測跑在執行緒裡、取消不了（boto 的連線逾時是 5 秒、還會重試），所以不直接 await：
    shield 住 task、等不到就回上一次的結論，task 跑完自己會更新。這支端點的呼叫端是
    本機探針，它的 curl 逾時只有 5 秒。
    """
    global _storage_task
    if not get_object_storage().enabled:
        return "disabled"
    if time.monotonic() >= _storage.expires_at:
        if _storage_task is None or _storage_task.done():
            _storage_task = asyncio.create_task(_refresh_storage(), name="healthz-storage-probe")
        try:
            await asyncio.wait_for(asyncio.shield(_storage_task), timeout=_STORAGE_WAIT)
        except asyncio.TimeoutError:
            pass
    return _storage.state


def storage_snapshot() -> dict:
    """物件儲存探測的**被動**讀取（給 `/api/admin/diagnostics`）：只讀上一次的結論，不觸發探測。

    每次探測是一個計費的 R2 list 操作，診斷頁不得多打。`last_probe_age_s` 由到期時刻反推
    （成功快取 `_STORAGE_OK_TTL`、失敗 `_STORAGE_FAIL_TTL`）；從未探測過為 None。
    """
    if not get_object_storage().enabled:
        return {"state": "disabled", "consecutive_failures": 0, "last_probe_age_s": None, "last_probe_ok": None}
    snap = _storage
    if snap.expires_at <= 0:
        return {"state": snap.state, "consecutive_failures": 0, "last_probe_age_s": None, "last_probe_ok": None}
    ttl = _STORAGE_FAIL_TTL if snap.consecutive_failures else _STORAGE_OK_TTL
    age = max(0.0, time.monotonic() - (snap.expires_at - ttl))
    return {
        "state": snap.state,
        "consecutive_failures": snap.consecutive_failures,
        "last_probe_age_s": round(age, 1),
        "last_probe_ok": snap.consecutive_failures == 0,
    }


@router.get("/healthz")
async def healthz() -> JSONResponse:
    """存活探測。健康 200 `{"status":"ok"}`；DB 不可用 503 `{"status":"degraded"}`。"""
    if await _db_ok():
        return JSONResponse({"status": "ok"})
    return JSONResponse({"status": "degraded"}, status_code=503)


@router.get("/healthz/storage")
async def healthz_storage(request: Request) -> JSONResponse:
    """物件儲存（R2）可達性。**只回答本機直連的請求**，其餘一律 404。

    回 `{"storage": "disabled"|"unknown"|"ok"|"degraded"}`；只有 degraded 回 503。
    由 `scripts/check_web_health.sh` 消費（退出碼 6）。設計理由見模組 docstring。
    """
    if not dev_mode.is_direct_loopback(request):
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    state = await _storage_state()
    return JSONResponse({"storage": state}, status_code=503 if state == "degraded" else 200)


@router.get("/healthz/llm")
async def healthz_llm(request: Request) -> JSONResponse:
    """DeepSeek 帳號可用性。**只回答本機直連的請求**，其餘一律 404。

    回 `{"llm": state}`（詞彙見 `app/services/llm_health.py`）；low／exhausted／auth_failed／unreachable／
    indeterminate 在問答主答走 DeepSeek 時回 503，否則加 `_unused` 回 200。由 `scripts/check_web_health.sh`
    消費（low 為退出碼 7，其餘 503 為 8）。
    """
    if not dev_mode.is_direct_loopback(request):
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    settings = get_settings()
    state, status = await llm_health.report(
        currency=settings.llm_budget_currency, floor=settings.llm_balance_floor,
    )
    return JSONResponse({"llm": state}, status_code=status)


@router.get("/healthz/security")
async def healthz_security(request: Request) -> JSONResponse:
    """安全事件的狀態。**只回答本機直連的請求**，其餘一律 404。

    回 `{"security": state}`；告警狀態（`security_ops.ALERT_STATES`）回 503，ok／unknown 回 200。
    由 `scripts/check_security_health.sh` 消費（audit_chain_broken 為退出碼 1、其餘告警為 2、判不出來為 3）。
    """
    if not dev_mode.is_direct_loopback(request):
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    state = await _security_state()
    return JSONResponse({"security": state}, status_code=503 if state in security_ops.ALERT_STATES else 200)


@router.get("/api/status")
async def system_status() -> JSONResponse:
    """一般使用者看的粗粒度系統狀態（需登入）。只回 `{status, message}`；規則見模組 docstring。"""
    if not await _db_ok():
        status, message = "degraded", _DB_DEGRADED_MESSAGE
    else:
        status = await _critical_services_state()
        message = _STATUS_MESSAGES[status]
    return JSONResponse({"status": status, "message": message}, headers={"Cache-Control": "no-store"})
