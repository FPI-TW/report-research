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
消費端是探針退出碼 7（只認 503）。
"""
import asyncio
import logging
import time
from typing import NamedTuple

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.config import get_settings
from app.services import llm_health
from app.services.object_storage import get_object_storage
from web import deps, dev_mode

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


@router.get("/healthz")
async def healthz() -> JSONResponse:
    """存活探測。健康 200 `{"status":"ok"}`；DB 不可用 503 `{"status":"degraded"}`。"""
    global _cache
    now = time.monotonic()
    ts, ok = _cache
    if now - ts >= _TTL:
        ok = await _probe_db()
        _cache = (now, ok)
    if ok:
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
    indeterminate 在線上任務走 DeepSeek 時回 503，否則加 `_unused` 回 200。由 `scripts/check_web_health.sh`
    消費（退出碼 7）。
    """
    if not dev_mode.is_direct_loopback(request):
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    settings = get_settings()
    state, status = await llm_health.report(
        currency=settings.llm_budget_currency, floor=settings.llm_balance_floor,
    )
    return JSONResponse({"llm": state}, status_code=status)
