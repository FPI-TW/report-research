"""用量收集的 middleware（純 ASGI；Admin v2）：只計回 200 的四類請求，交給 `app/services/usage_events.py`。

| 請求 | kind | subject |
|---|---|---|
| `GET /api/reading/{file_hash}`（閱讀頁本體；`/text`、`/similar` 不算） | `reading` | file_hash |
| `GET /api/report/{report_id}/file`（原檔） | `report_file` | report_id |
| `GET /api/search` | `search` | `market` 篩選（只收 findb 市場代碼），沒有篩選＝只計總量 |
| `POST /api/ask` | `ask` | 只計總量 |

**搜尋字串永遠不碰**：query string 只逐段比對鍵名 `market`，其餘段落（含 `q`）不解碼、不保存。
使用者身分取 `app.request_context.current_user_id()`——所以這一層必須註冊在 `require_login` **之內**
（`web/server.py` 先 `add_middleware(UsageMiddleware)` 再定義 `require_login`：越早加的越內層）。

只計 200：401／403／404／429（配額、排隊滿）都不算使用；SSE 的 `/api/ask` 在串流開始時就是 200。
每個請求只做記憶體操作（字典累加），寫 DB 在 lifespan 的 flusher。任何例外都吞掉——收集不能讓請求失敗。
刻意寫成純 ASGI 而不是 `@app.middleware("http")`，理由同 `web/request_log.py`（不替每條 SSE 多包一層）。
"""

from __future__ import annotations

import logging
import re

from app.services import usage_events
from app.services.tagging import MARKETS

logger = logging.getLogger(__name__)

_READING = re.compile(r"^/api/reading/([0-9a-f]{64})$")
_REPORT_FILE = re.compile(r"^/api/report/([0-9a-fA-F-]{36})/file$")
_MARKETS = frozenset(MARKETS)


def market_filter(query_string: bytes) -> str:
    """query string 裡 `market` 的值（只接受市場代碼，其餘一律空字串）。不解析、不保存任何其他鍵。"""
    for part in (query_string or b"").split(b"&"):
        key, sep, value = part.partition(b"=")
        if sep and key == b"market":
            v = value.decode("ascii", "ignore")
            return v if v in _MARKETS else ""
    return ""


def classify(method: str, path: str, query_string: bytes) -> tuple[str, str] | None:
    """(kind, subject)；不是要計的請求回 None。純函式（測試直接驗）。"""
    if method == "GET":
        m = _READING.fullmatch(path)
        if m:
            return usage_events.KIND_READING, m.group(1)
        m = _REPORT_FILE.fullmatch(path)
        if m:
            return usage_events.KIND_REPORT_FILE, m.group(1).lower()
        if path == "/api/search":
            return usage_events.KIND_SEARCH, market_filter(query_string)
    elif method == "POST" and path == "/api/ask":
        return usage_events.KIND_ASK, ""
    return None


class UsageMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        target = classify(scope.get("method", ""), scope.get("path", ""), scope.get("query_string", b""))
        if target is None:
            await self.app(scope, receive, send)
            return
        recorded = False

        async def _send(message):
            nonlocal recorded
            if message["type"] == "http.response.start" and not recorded:
                recorded = True
                if message.get("status") == 200:
                    try:
                        usage_events.record_request(*target)
                    except Exception:  # noqa: BLE001 — 收集不能讓請求失敗
                        logger.debug("用量收集失敗", exc_info=True)
            await send(message)

        await self.app(scope, receive, _send)
