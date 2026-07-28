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
"""
import asyncio
import logging
import time

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from web import deps

logger = logging.getLogger(__name__)

router = APIRouter()

# 探測快取：免認證端點必須假設會被高頻打。TTL 內共用同一次探測結果。
_TTL = 5.0
_PROBE_TIMEOUT = 3.0
_cache: tuple[float, bool] = (0.0, False)


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
