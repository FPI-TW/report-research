"""HTTP 請求的關聯 id：讓同一個請求產生的日誌行串得起來。

一次 `/api/ask` 會印出 `qa_timing`、`qa_agentic`、rerank 逾時、忠實度抽查等好幾行，
分屬不同模組、其中幾行還在請求結束之後才出現。沒有共同的鍵，併發時完全無法分辨
哪一行屬於哪一題。

機制是 `contextvars`：`web/request_log.py` 的 middleware 在請求進來時設值，
`app/logging_setup.py` 的 filter 把它蓋到每一筆 LogRecord 上。`asyncio.create_task`
與 `asyncio.to_thread` 都會複製當下的 context，所以背景任務（忠實度抽查）與執行緒
（嵌入、rerank）印的日誌自動帶著發起它的那個請求的 id，不必逐層傳參數。

**不要與 `qa_log.request_id` 混淆**：那是 `/api/ask` 由前端產生的冪等鍵（UUID，
落庫、UNIQUE），語意是「同一次送出」；這裡的是伺服器端每個 HTTP 請求一個的日誌關聯鍵，
不落庫。批次腳本沒有 HTTP 請求，值恆為 `NO_REQUEST`。

**使用者 id**（Admin v2）：同一個機制再帶一個值——這個請求是哪個帳號發的。`web/server.py` 的
`require_login` 在 session 查驗通過後設值（開發模式免登入的 `DEV_USER` 沒有 id，維持 None），
讓不經參數傳遞的地方也能歸因：`llm_http` 的 observer（線上 LLM 用量歸到人，`llm_usage_daily`）、
usage middleware（`usage_counter`）。忠實度抽查是 `create_task` 起的背景任務，複製了 context，
所以也歸得到發問的人。**它只是歸因用的標記，不是授權**——授權一律經 `web/authz.py` 讀
`request.state.user`。批次腳本恆為 None。
"""

from __future__ import annotations

import re
import uuid
from contextvars import ContextVar

NO_REQUEST = "-"

_request_id: ContextVar[str] = ContextVar("http_request_id", default=NO_REQUEST)

# 上游（nginx／Cloudflare）帶進來的 id 只在形狀安全時沿用：它會被原樣寫進日誌行與回應
# header，任意字串等於讓外部決定日誌內容（換行即可偽造一整行）。
_INBOUND_OK = re.compile(r"^[A-Za-z0-9._-]{8,64}$")


def new_request_id() -> str:
    return uuid.uuid4().hex[:16]


def accept_inbound(value: str | None) -> str:
    """上游給的 id 形狀安全就沿用（跨層可對），否則自己產一個。"""
    if value and _INBOUND_OK.fullmatch(value):
        return value
    return new_request_id()


def current_request_id() -> str:
    return _request_id.get()


def set_request_id(value: str):
    """回傳 token，呼叫端負責在 finally 以 `reset_request_id` 還原。"""
    return _request_id.set(value)


def reset_request_id(token) -> None:
    _request_id.reset(token)


_user_id: ContextVar[str | None] = ContextVar("http_user_id", default=None)


def current_user_id() -> str | None:
    """這個請求的帳號 UUID；沒有登入身分（批次、免登入開發模式、白名單路徑）時為 None。"""
    return _user_id.get()


def set_user_id(value: str | None):
    """回傳 token，呼叫端負責在 finally 以 `reset_user_id` 還原。"""
    return _user_id.set(value)


def reset_user_id(token) -> None:
    _user_id.reset(token)
