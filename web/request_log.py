"""請求關聯 id 與 `/api/*` 的請求日誌（純 ASGI middleware）。

補兩個缺口：

1. **關聯 id**：每個請求設一個 id（見 `app/request_context.py`），回應帶 `X-Request-Id`。
   使用者回報問題時附上這個值，就能從日誌撈出那一個請求的全部行。
2. **耗時**：uvicorn 的 access log 沒有耗時、也不帶關聯 id，而多數 router 自己不記日誌。
   這裡對 `/api/*` 每個請求記一行 `method path status elapsed_ms`。SSE 的耗時是整條串流
   的長度（含使用者中途斷線），不是首位元組時間。

刻意寫成純 ASGI 而不是 `@app.middleware("http")`：後者（BaseHTTPMiddleware）把回應
包成自己的串流，這一層只需要讀 header、看狀態碼，沒有理由替每一條 SSE 多包一層。

不記 query string：`/api/search` 的 `q` 是使用者的查詢字串，該不該進日誌由那支 router
自己決定（它有更完整的檢索遙測可以一起記）。
"""

from __future__ import annotations

import logging
import time

from app import request_context

logger = logging.getLogger(__name__)

HEADER = "x-request-id"

# 高頻輪詢／探測：正常時不記（監控頁每 5 秒一次 /api/progress，會把其他行淹掉），
# 但出錯或變慢時照記——那正是需要看到的時候。
QUIET_PATHS = frozenset({"/healthz", "/api/progress"})
QUIET_SLOW_MS = 1000.0


def _should_log(path: str, status: int, elapsed_ms: float) -> bool:
    if status >= 500:
        return True
    if path in QUIET_PATHS:
        return elapsed_ms >= QUIET_SLOW_MS
    return path.startswith("/api/")


class RequestLogMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        inbound = None
        for name, value in scope.get("headers") or ():
            if name == HEADER.encode():
                inbound = value.decode("latin-1")
                break
        rid = request_context.accept_inbound(inbound)
        token = request_context.set_request_id(rid)
        t0 = time.monotonic()
        # 500＝「還沒送出回應就離開了」的預設：例外一路冒上來時沒有 response.start。
        status = 500

        async def send_with_id(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                headers = [(k, v) for k, v in message.get("headers", []) if k.lower() != HEADER.encode()]
                headers.append((HEADER.encode(), rid.encode()))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            elapsed_ms = (time.monotonic() - t0) * 1000
            path = scope.get("path", "")
            if _should_log(path, status, elapsed_ms):
                logger.info(
                    "http %s %s status=%d elapsed_ms=%.1f",
                    scope.get("method", "-"), path, status, elapsed_ms,
                )
            request_context.reset_request_id(token)
