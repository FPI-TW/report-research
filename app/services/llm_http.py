"""DeepSeek Chat Completions 的 HTTP 客戶端（官方 OpenAI 相容端點）。

本模組只負責「打一次 API、把結果與失敗原因說清楚」，不決定哪個任務用哪個模型、也不做
呼叫端的重試策略——那兩件事分別在分派層（`llm.stream_completion`、`scripts/_claude_cli.
run_claude`）與各呼叫點。刻意是**葉模組**：只 import 標準函式庫與 httpx，不 import
`app.*`／`web.*`（`query_planner.py` 開頭的依賴約束、`retrieval_pipeline`↔`answer` 的
刻意循環都不能被它牽動；由 tests/test_llm_http.py 的 AST 測試釘住）。

設計要點（每一條都有對應的事故或官方文件，改之前先讀）：

- **thinking 預設是開的**（官方 thinking_mode 指南）。不關的話 TTFT 變長、token 成本上升，
  `max_tokens` 被推理吃光時 content 會是空的。每個請求都**同時**送兩個開關
  `thinking.type=disabled` 與 `reasoning_effort=none`：文件沒寫兩者衝突時誰優先，只送一個
  不保證關得掉。`reasoning_content` 一律丟棄、只記長度，比照 CLI 路徑忽略 thinking_delta。
- **`max_tokens` 由呼叫端逐點給**，沒有預設值：非 thinking 模式不設時上限只有 8K，多標的
  的訊號擷取會被截斷，而截斷在舊架構裡會被誤判成「JSON 解析失敗」。
- **錯誤依 HTTP 狀態碼分類**（`classify_status`）。只有兩種情況不得不看訊息文字：審查拒答
  （400 `Content Exists Risk`，官方錯誤碼表沒列）與模型不存在（400 `Model Not Exist`）。
  兩者都沒有專屬狀態碼，只能比對字串；其餘一律不解析文字——`answer._llm_error_kind` 的
  docstring 記載過「分得越細越容易在改版後靜默落到其他」。
- **逾時語意**：
  - 串流（`astream_chat`）的 `first_token_timeout` 是**首字期限**。在第一個 content 字到達之前，
    每一個 await 各自包 `asyncio.timeout_at`，**絕不跨越 yield**。理由是 `web/deps.py` 的
    `_with_heartbeat` 每次 `__anext__` 都開新 Task，而 `asyncio.timeout` 綁定進入時的 Task：
    跨 yield 的逾時在生產上第一個 token 之後就失效（本機重現：0.5 秒逾時、6 段每 0.2 秒
    一段，經新 Task 驅動時 1.2 秒全吐完）。每個 await 自己包，任何驅動方式下行為都一樣，
    單元測試看到的就是生產行為。第一個字之後不設牆鐘上限，由 `max_tokens` 與 read 逾時收尾。
  - 批次（`complete_chat`）的 `timeout` 是涵蓋傳輸層重試的**總期限**，用 `time.monotonic()`
    逐行檢查。httpx 的 read 逾時會被伺服器排隊時的 `: keep-alive` 一直重置（官方：最長 10
    分鐘），不能拿來當總時限。
- **金鑰在呼叫時才讀 `os.environ`**，不進 Settings：批次腳本不讀 repo 根 `.env`，而
  `get_settings()` 是 import 期就快取的單例（`app/services/db.py`），先快取到空值就一路空到底。
- **不用 openai SDK**：它內建的重試會吃掉失敗原因，違反 `scripts/_claude_cli.py` 開頭四天
  停擺紀錄的教訓（失敗原因必須說得出口）。
"""

from __future__ import annotations

import asyncio
import atexit
import contextlib
import json
import logging
import os
import random
import re
import threading
import time
import weakref
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.deepseek.com"

# 走 HTTP 的模型**明確白名單**。不在這裡的名稱一律留給 CLI 路徑：若寫成「`claude-` 以外都走
# HTTP」，CLI 別名（`sonnet`）或打錯的模型名會被送到付費端點、拿到 400 後被當成帳號錯誤
# 中止整批。`deepseek-v4-flash` 是官方保留的舊名（導向 V4.1-Flash、按 Flash 計價）。
# 要加新名稱就發 PR——換模型本來就需要重新評測。
HTTP_MODELS = frozenset({"deepseek-flash", "deepseek-v4-pro", "deepseek-v4-flash"})


def is_http_model(model: str | None) -> bool:
    return model in HTTP_MODELS


# ── 失敗分類 ─────────────────────────────────────────────────────────────────
AUTH = "auth"                      # 缺金鑰或 401
QUOTA = "quota"                    # 402 餘額不足
CONFIG = "config"                  # 404／模型不存在
CONTENT_FILTER = "content_filter"  # 400 Content Exists Risk、finish_reason=content_filter
BAD_REQUEST = "bad_request"        # 其他 400／422：多半是單篇輸入造成
OVERLOADED = "overloaded"          # 429、5xx、insufficient_system_resource
NETWORK = "network"                # 連線、DNS、TLS、串流中途斷線
TIMEOUT = "timeout"                # 首字期限（串流）或總期限（批次）到了
TRUNCATED = "truncated"            # finish_reason=length
EMPTY = "empty"                    # 成功結束卻沒有 content（包括只有 reasoning）
OTHER = "other"

# 帳號層級：每一篇都會踩到，批次應中止整批而不是記 N 筆單篇失敗後 exit 0。
ACCOUNT_KINDS = frozenset({AUTH, QUOTA, CONFIG})
# 暫時性：值得在傳輸層退避重試。截斷、審查、空回應是決定性的，重打只是再付一次錢。
TRANSIENT_KINDS = frozenset({OVERLOADED, NETWORK})

_PHRASES = {
    AUTH: "金鑰無效或缺漏",
    QUOTA: "帳戶餘額不足",
    CONFIG: "模型或端點設定錯誤",
    CONTENT_FILTER: "觸發供應商內容審查",
    BAD_REQUEST: "請求被拒",
    OVERLOADED: "供應商過載或限流",
    NETWORK: "連線失敗",
    TIMEOUT: "逾時",
    TRUNCATED: "輸出截斷",
    EMPTY: "空回應",
    OTHER: "未預期錯誤",
}

_KEY_LIKE = re.compile(r"sk-[A-Za-z0-9*]{4,}")
_LONE_SURROGATE = re.compile(r"[\ud800-\udfff]")
_USER_ID_BAD = re.compile(r"[^A-Za-z0-9_-]")

_READ_TIMEOUT = 60.0
_WRITE_TIMEOUT = 30.0
_POOL_TIMEOUT = 10.0
_RETRY_AFTER_CAP = 60.0
_BATCH_BACKOFF = (2.0, 6.0)


def _one_line(text: str) -> str:
    """去掉 TAB 與換行：批次失敗 log 是 `路徑\\t階段\\t原因`，原因欄不能再切出新欄或新行。"""
    return re.sub(r"[\t\r\n]+", " ", text).strip()


def _redact(text: str) -> str:
    """伺服器的 401 訊息會回顯部分金鑰；寫進 log 前一律遮掉。"""
    return _KEY_LIKE.sub("sk-***", text)


def error_string(kind: str, detail: str = "") -> str:
    """批次用的失敗原因：`API[<kind>] <固定措辭>：<細節>`。

    前綴 `API[` 是契約：呼叫端靠它分辨「HTTP 路徑已在傳輸層重試過」與 CLI 的訊息。
    """
    s = f"API[{kind}] {_PHRASES.get(kind, _PHRASES[OTHER])}"
    if detail:
        s += f"：{detail}"
    return _one_line(_redact(s))[:300]


def sanitize(text: str) -> str:
    """去 NUL、孤立代理字元換成 U+FFFD。

    PDF 抽出的文字偶有 `\\x00`（CLI 路徑為了 argv 早就剝掉，HTTP 路徑沒有 argv 但仍要剝，
    否則同一篇研報兩條路徑的輸入不同）；孤立代理字元會讓 UTF-8 編碼直接拋錯。兩者都是
    「單篇輸入造成的失敗」的來源。
    """
    return _LONE_SURROGATE.sub("\ufffd", text.replace("\x00", ""))


def _user_id(value: str | None) -> str | None:
    """官方格式 `[a-zA-Z0-9\\-_]+`、最長 512；用於內容安全、KV cache 與排程的隔離。"""
    if not value:
        return None
    return _USER_ID_BAD.sub("-", value)[:512] or None


def build_body(
    model: str,
    prompt: str,
    *,
    max_tokens: int,
    system: str | None = None,
    stream: bool = True,
    user_id: str | None = None,
) -> dict:
    """組請求 body（純函式）。thinking 一律關：兩個開關都送，見模組 docstring。"""
    messages = []
    if system:
        messages.append({"role": "system", "content": sanitize(system)})
    messages.append({"role": "user", "content": sanitize(prompt)})
    body: dict = {
        "model": model,
        "messages": messages,
        "max_tokens": int(max_tokens),
        "stream": stream,
        "thinking": {"type": "disabled"},
        "reasoning_effort": "none",
    }
    if stream:
        body["stream_options"] = {"include_usage": True}
    uid = _user_id(user_id)
    if uid:
        body["user_id"] = uid
    return body


def _error_message(raw: bytes | str) -> str:
    text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
    try:
        obj = json.loads(text)
    except ValueError:
        return _one_line(text)[:200]
    if isinstance(obj, dict):
        err = obj.get("error")
        if isinstance(err, dict) and isinstance(err.get("message"), str):
            return _one_line(err["message"])[:200]
        if isinstance(err, str):
            return _one_line(err)[:200]
    return _one_line(text)[:200]


def _is_content_risk(message: str) -> bool:
    return "content exists risk" in message.lower()


def _is_model_missing(message: str) -> bool:
    low = message.lower()
    return "model not exist" in low or "model_not_found" in low or "model does not exist" in low


def classify_status(status: int, raw: bytes | str = b"") -> tuple[str, str]:
    """非 200 回應 → (kind, 細節)。只有 400／422 會看訊息文字（理由見模組 docstring）。"""
    message = _error_message(raw) if raw else ""
    detail = _redact(f"HTTP {status}" + (f" {message}" if message else ""))
    if status == 401:
        return AUTH, detail
    if status == 402:
        return QUOTA, detail
    if status == 404:
        return CONFIG, detail
    if status in (400, 422):
        if _is_content_risk(message):
            return CONTENT_FILTER, detail
        if _is_model_missing(message):
            return CONFIG, detail
        return BAD_REQUEST, detail
    if status == 429 or 500 <= status <= 599:
        return OVERLOADED, detail
    return OTHER, detail


def _retry_after(headers: httpx.Headers) -> float | None:
    raw = headers.get("retry-after")
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        return None  # HTTP-date 形式：不解析，交給呼叫端的預設退避


# ── SSE 解析 ─────────────────────────────────────────────────────────────────
DONE = object()


def parse_sse_line(line: str):
    """一行 SSE → None（略過）、`DONE`、或 dict。

    略過空行與 `:` 開頭的註解（伺服器排隊時送的 `: keep-alive`）；只認 `data:` 欄位。
    data 不是合法 JSON 時回 `{"error": {...}}`，讓累加器當成串流錯誤處理。
    """
    line = line.rstrip("\r")
    if not line or line.startswith(":") or not line.startswith("data:"):
        return None
    data = line[5:].strip()
    if data == "[DONE]":
        return DONE
    try:
        obj = json.loads(data)
    except ValueError:
        return {"error": {"message": f"malformed SSE data: {data[:80]}"}}
    return obj if isinstance(obj, dict) else None


# SSE 規範只以 CRLF／LF／CR 分行。httpx 的 `aiter_lines`／`iter_lines` 用 `str.splitlines()`，
# 還會在 U+2028、U+2029、U+0085 切開——JSON 不要求跳脫這三個字元，模型輸出裡出現時一行
# data 會被切成兩段、變成「malformed SSE data」，而那會被誤報成供應商過載。
_LINE_BREAK = re.compile(r"\r\n|\r|\n")


class _LineSplitter:
    """跨 chunk 的 SSE 分行器（保留殘段；結尾的 `\r` 要等下一段才知道是不是 CRLF）。"""

    def __init__(self) -> None:
        self._buf = ""

    def feed(self, text: str) -> list[str]:
        buf = self._buf + text
        hold_cr = buf.endswith("\r")
        if hold_cr:
            buf = buf[:-1]
        parts = _LINE_BREAK.split(buf)
        self._buf = parts.pop() + ("\r" if hold_cr else "")
        return parts

    def flush(self) -> list[str]:
        rest, self._buf = self._buf.rstrip("\r"), ""
        return [rest] if rest else []


async def _aiter_sse_lines(response: httpx.Response) -> AsyncIterator[str]:
    splitter = _LineSplitter()
    async for text in response.aiter_text():
        for line in splitter.feed(text):
            yield line
    for line in splitter.flush():
        yield line


def _iter_sse_lines(response: httpx.Response):
    splitter = _LineSplitter()
    for text in response.iter_text():
        yield from splitter.feed(text)
    yield from splitter.flush()


@dataclass
class _Acc:
    """串流累加器：只收 content；reasoning 只記長度；usage 取最後一個非 null 值。"""

    reasoning_chars: int = 0
    finish_reason: str | None = None
    usage: dict | None = None
    model_resp: str | None = None
    error: tuple[str, str] | None = None
    done: bool = False

    def feed(self, obj: dict) -> str | None:
        err = obj.get("error")
        if err is not None:
            message = _error_message(json.dumps({"error": err}))
            # 串流中途的錯誤物件：審查照審查分；其餘多半是伺服端中斷，歸暫時性。
            kind = CONTENT_FILTER if _is_content_risk(message) else OVERLOADED
            self.error = (kind, _redact(f"stream error {message}"))
            return None
        if isinstance(obj.get("model"), str):
            self.model_resp = obj["model"]
        if isinstance(obj.get("usage"), dict):
            self.usage = obj["usage"]
        choices = obj.get("choices") or []
        if not choices or not isinstance(choices[0], dict):
            return None
        choice = choices[0]
        if choice.get("finish_reason"):
            self.finish_reason = choice["finish_reason"]
        delta = choice.get("delta") or {}
        reasoning = delta.get("reasoning_content")
        if isinstance(reasoning, str):
            self.reasoning_chars += len(reasoning)
        content = delta.get("content")
        return content if isinstance(content, str) and content else None


@dataclass
class ChatOutcome:
    """一次呼叫的結局。`kind is None` 表示成功。

    串流時 `streamed=True` 代表已經有字送出去了：此時的 kind 是「中途出事」，呼叫端依自己的
    語意決定 fail-open（線上保留已吐的字）或整篇失敗（批次）。
    """

    kind: str | None
    detail: str = ""
    status: int | None = None
    retry_after: float | None = None
    finish_reason: str | None = None
    usage: dict | None = None
    model_resp: str | None = None
    streamed: bool = False
    reasoning_chars: int = 0
    ttft_ms: int | None = None
    total_ms: int = 0


def _final_kind(acc: _Acc, got_text: bool) -> tuple[str | None, str]:
    if acc.error is not None:
        return acc.error
    if acc.finish_reason == "content_filter":
        return CONTENT_FILTER, "finish_reason=content_filter"
    if acc.finish_reason == "length":
        return TRUNCATED, "finish_reason=length"
    if acc.finish_reason == "insufficient_system_resource":
        return OVERLOADED, "finish_reason=insufficient_system_resource"
    if acc.finish_reason == "aborted":
        return OTHER, "finish_reason=aborted"
    if not acc.done and acc.finish_reason is None:
        return NETWORK, "串流未收到結束訊號即中斷"
    if not got_text:
        detail = "只有 reasoning、沒有 content" if acc.reasoning_chars else "沒有 content"
        return EMPTY, detail
    return None, ""


def _outcome(acc: _Acc, got_text: bool, t0: float, ttft_ms: int | None) -> ChatOutcome:
    kind, detail = _final_kind(acc, got_text)
    return ChatOutcome(
        kind=kind,
        detail=detail,
        finish_reason=acc.finish_reason,
        usage=acc.usage,
        model_resp=acc.model_resp,
        streamed=got_text,
        reasoning_chars=acc.reasoning_chars,
        ttft_ms=ttft_ms,
        total_ms=int((time.monotonic() - t0) * 1000),
    )


def _fail(kind: str, detail: str, t0: float, **kw) -> ChatOutcome:
    return ChatOutcome(kind=kind, detail=detail, total_ms=int((time.monotonic() - t0) * 1000), **kw)


def _log_call(task: str, model: str, out: ChatOutcome, attempts: int = 1) -> None:
    """每次呼叫一行結構化 log（不含 prompt、不含 header）。"""
    usage = out.usage or {}
    details = usage.get("completion_tokens_details") or {}
    logger.info(
        "llm_call task=%s model=%s model_resp=%s backend=http kind=%s finish=%s attempts=%d "
        "ttft_ms=%s total_ms=%d hit=%s miss=%s out=%s reasoning=%s",
        task, model, out.model_resp, out.kind or "ok", out.finish_reason, attempts,
        out.ttft_ms, out.total_ms, usage.get("prompt_cache_hit_tokens"),
        usage.get("prompt_cache_miss_tokens"), usage.get("completion_tokens"),
        details.get("reasoning_tokens"),
    )




# ── 連線 ─────────────────────────────────────────────────────────────────────
# 測試注入點：設成 httpx.MockTransport 後呼叫 `_reset_clients()`。
_transport: httpx.BaseTransport | httpx.AsyncBaseTransport | None = None

_ASYNC_CLIENTS: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, httpx.AsyncClient]" = (
    weakref.WeakKeyDictionary()
)
_SYNC_LOCAL = threading.local()
_SYNC_CLIENTS: list[httpx.Client] = []
_SYNC_LOCK = threading.Lock()

# 讀到 [DONE] 之後把 chunked 結尾讀完的上限。不讀完的話 h11 狀態不是 DONE，httpcore 會直接
# 關掉連線而不放回池，每次呼叫都重付 TCP＋TLS 交握（本機實測：5 次呼叫 5 條連線；讀完後 1 條）。
_DRAIN_TIMEOUT = 2.0

# 金鑰、端點、連線逾時三個鍵都在呼叫時讀（`os.getenv`，`grep -rn os.getenv app` 找得到），
# 不進 app/config.py 的 Settings：批次腳本不讀 repo 根 `.env`，而 Settings 是 import 期就
# 快取的單例——先快取到空值就一路空到底，手動執行時補設環境變數也不會生效。


def _api_key() -> str:
    return (os.getenv("DEEPSEEK_API_KEY") or "").strip()


def _url() -> str:
    base = (os.getenv("DEEPSEEK_BASE_URL") or "").strip() or DEFAULT_BASE_URL
    return base.rstrip("/") + "/chat/completions"


def _timeout(read: float, cap: float | None = None) -> httpx.Timeout:
    """`cap`＝剩餘期限（批次）：connect／read／write 都不超過它。"""
    try:
        connect = float(os.getenv("DEEPSEEK_CONNECT_TIMEOUT") or 10)
    except ValueError:
        connect = 10.0
    write = _WRITE_TIMEOUT
    if cap is not None:
        cap = max(cap, 0.001)
        connect, read, write = min(connect, cap), min(read, cap), min(write, cap)
    return httpx.Timeout(connect=connect, read=read, write=write, pool=_POOL_TIMEOUT)


def _headers(key: str) -> dict:
    return {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }


def _preflight(t0: float) -> tuple[str, str] | ChatOutcome:
    """送出前檢查金鑰與端點；設定錯誤一律回帳號層級的 outcome，不往外拋。

    兩種實際會發生的設定錯：金鑰被複製貼上帶進全形引號或零寬空白（`strip()` 去不掉，
    header 只能是 ASCII，httpx 會拋 UnicodeEncodeError）；`DEEPSEEK_BASE_URL` 少了 scheme
    （httpx 拋 UnsupportedProtocol，那是 TransportError，會被當成網路錯誤重試——每篇都記
    「連線失敗」、批次卻不中止，正是四天停擺那種型態）。
    """
    key = _api_key()
    if not key:
        return _fail(AUTH, "缺 DEEPSEEK_API_KEY", t0)
    if not (key.isascii() and key.isprintable()):
        return _fail(AUTH, "DEEPSEEK_API_KEY 含非 ASCII 或不可列印字元（常見於貼上時帶入的全形引號、零寬空白）", t0)
    url = _url()
    try:
        parsed = httpx.URL(url)
    except (httpx.InvalidURL, ValueError):
        return _fail(CONFIG, "DEEPSEEK_BASE_URL 不是合法網址", t0)
    if parsed.scheme not in ("http", "https") or not parsed.host:
        return _fail(CONFIG, "DEEPSEEK_BASE_URL 必須是 http(s):// 開頭的網址", t0)
    return key, url


def _async_client() -> httpx.AsyncClient:
    """以 event loop 為鍵延遲建立。

    `IsolatedAsyncioTestCase` 與批次的 `asyncio.run` 每次都換 loop，一個全域 client 會綁在
    已經關閉的 loop 上。上限 10 條連線：`/api/ask` 閘門 3 格加上派生呼叫，綽綽有餘。

    WeakKeyDictionary 單靠自己回收不了：池裡的連線經 transport 強引用 loop（也就是 key），
    所以每次先把已關閉 loop 的條目丟掉（已關閉的 loop 無法再 await aclose，交給 GC 收 socket）。
    同一個行程會跑多個 loop 的使用端，應在 loop 結束前 `await llm_http.aclose()`。
    """
    for stale in [lp for lp in list(_ASYNC_CLIENTS.keys()) if lp.is_closed()]:
        _ASYNC_CLIENTS.pop(stale, None)
    loop = asyncio.get_running_loop()
    client = _ASYNC_CLIENTS.get(loop)
    if client is None or client.is_closed:
        client = httpx.AsyncClient(
            transport=_transport,  # type: ignore[arg-type]
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
            timeout=_timeout(_READ_TIMEOUT),
        )
        _ASYNC_CLIENTS[loop] = client
    return client


def _sync_client() -> httpx.Client:
    """每條執行緒一個：不依賴 Client 的執行緒安全性，同時涵蓋 to_thread 與 ThreadPoolExecutor。"""
    client = getattr(_SYNC_LOCAL, "client", None)
    if client is None or client.is_closed:
        client = httpx.Client(transport=_transport, timeout=_timeout(_READ_TIMEOUT))  # type: ignore[arg-type]
        _SYNC_LOCAL.client = client
        with _SYNC_LOCK:
            _SYNC_CLIENTS.append(client)
    return client


async def aclose() -> None:
    """關閉目前 event loop 的 AsyncClient（web lifespan 結束時呼叫）。"""
    client = _ASYNC_CLIENTS.pop(asyncio.get_running_loop(), None)
    if client is not None:
        await client.aclose()


def _close_sync_clients() -> None:
    with _SYNC_LOCK:
        clients, _SYNC_CLIENTS[:] = list(_SYNC_CLIENTS), []
    for client in clients:
        with contextlib.suppress(Exception):
            client.close()


atexit.register(_close_sync_clients)


def _reset_clients() -> None:
    """僅供測試：丟掉快取的 client，讓下一次呼叫用新的 `_transport`。"""
    _ASYNC_CLIENTS.clear()
    _close_sync_clients()
    _SYNC_LOCAL.client = None


def _transport_detail(exc: BaseException) -> str:
    return _one_line(f"{type(exc).__name__}: {exc}")[:200]


# ── 線上：非同步串流 ─────────────────────────────────────────────────────────
async def _drain(lines) -> None:
    """[DONE] 之後把 chunked 結尾讀完，連線才能回池；有界、任何錯誤都忽略。"""
    with contextlib.suppress(Exception):
        async with asyncio.timeout(_DRAIN_TIMEOUT):
            async for _ in lines:
                pass


async def astream_chat(
    model: str,
    prompt: str,
    *,
    max_tokens: int,
    first_token_timeout: float,
    system: str | None = None,
    task: str = "-",
    user_id: str | None = None,
) -> AsyncIterator[str | ChatOutcome]:
    """串流呼叫：逐段 yield content 文字，最後 yield 一個 `ChatOutcome`（恰好一次）。

    不做重試（重試策略在 `llm.stream_completion`，它才知道「已吐字就不重試」）。任何
    httpx 例外都轉成 outcome，不往外拋；只有 `CancelledError` 原樣傳遞，並經 finally
    關閉回應，對應 CLI 路徑的 `proc.kill()`。
    """
    t0 = time.monotonic()
    deadline = asyncio.get_running_loop().time() + first_token_timeout
    pre = _preflight(t0)
    if isinstance(pre, ChatOutcome):
        _log_call(task, model, pre)
        yield pre
        return
    key, url = pre

    acc = _Acc()
    got_text = False
    ttft_ms: int | None = None
    response: httpx.Response | None = None
    lines = None
    out: ChatOutcome | None = None
    try:
        try:
            body = build_body(model, prompt, max_tokens=max_tokens, system=system, user_id=user_id)
            client = _async_client()
            request = client.build_request(
                "POST", url, json=body, headers=_headers(key), timeout=_timeout(_READ_TIMEOUT)
            )
        except (httpx.InvalidURL, ValueError, UnicodeError) as exc:
            out = _fail(CONFIG, _transport_detail(exc), t0)
        else:
            try:
                async with asyncio.timeout_at(deadline):
                    response = await client.send(request, stream=True)
            except TimeoutError:
                out = _fail(TIMEOUT, f"首字期限 {first_token_timeout:g}s 內未收到回應", t0)
            except httpx.TransportError as exc:
                out = _fail(NETWORK, _transport_detail(exc), t0)

        if out is None and response is not None and response.status_code != 200:
            raw = b""
            with contextlib.suppress(TimeoutError, httpx.HTTPError):
                async with asyncio.timeout_at(deadline):
                    raw = await response.aread()
            kind, detail = classify_status(response.status_code, raw)
            out = _fail(
                kind, detail, t0, status=response.status_code,
                retry_after=_retry_after(response.headers),
            )

        if out is None and response is not None:
            lines = _aiter_sse_lines(response)
            while True:
                try:
                    if got_text:
                        line = await anext(lines)
                    else:
                        # 首字前：每個 await 各自包期限，不跨越 yield（見模組 docstring）
                        async with asyncio.timeout_at(deadline):
                            line = await anext(lines)
                except StopAsyncIteration:
                    break
                except TimeoutError:
                    out = _fail(TIMEOUT, f"首字期限 {first_token_timeout:g}s 內未收到內容", t0)
                    break
                except httpx.HTTPError as exc:
                    # 已經收到 finish_reason 才斷線：答案本身已完整，以 finish_reason 為準
                    if acc.finish_reason is None:
                        acc.error = (NETWORK, _transport_detail(exc))
                    break
                event = parse_sse_line(line)
                if event is None:
                    continue
                if event is DONE:
                    acc.done = True
                    await _drain(lines)
                    break
                if not isinstance(event, dict):
                    continue
                text = acc.feed(event)
                if acc.error is not None:
                    break
                if text:
                    if not got_text:
                        got_text = True
                        ttft_ms = int((time.monotonic() - t0) * 1000)
                    yield text
    finally:
        closer = getattr(lines, "aclose", None)
        if closer is not None:
            with contextlib.suppress(Exception):
                await closer()
        if response is not None:
            with contextlib.suppress(Exception):
                await response.aclose()

    if out is None:
        out = _outcome(acc, got_text, t0, ttft_ms)
    _log_call(task, model, out)
    yield out


# ── 批次：同步呼叫 ───────────────────────────────────────────────────────────
@dataclass
class ChatResult:
    """批次呼叫結果。`text is None` 時 `error` 必有值，且以 `API[<kind>]` 開頭。"""

    text: str | None
    error: str | None
    kind: str | None
    attempts: int = 1
    outcome: ChatOutcome = field(default_factory=lambda: ChatOutcome(kind=None))


def _timed_out_kind(deadline: float) -> str:
    """httpx 的逾時只有在總期限真的到了才算 TIMEOUT；否則是連線沉默（半開連線之類），
    歸 NETWORK 讓外層照暫時性錯誤重試。伺服器排隊時會送 keep-alive，60 秒完全沒有位元組
    不是「排隊中」。"""
    return TIMEOUT if time.monotonic() >= deadline else NETWORK


def _complete_once(
    model: str, prompt: str, *, max_tokens: int, system: str | None,
    user_id: str | None, deadline: float,
) -> tuple[ChatOutcome, str]:
    t0 = time.monotonic()
    pre = _preflight(t0)
    if isinstance(pre, ChatOutcome):
        return pre, ""
    key, url = pre
    remaining = deadline - t0
    if remaining <= 0:
        return _fail(TIMEOUT, "總期限已過", t0), ""

    try:
        body = build_body(model, prompt, max_tokens=max_tokens, system=system, user_id=user_id)
        client = _sync_client()
        # 各項逾時都不超過剩餘期限。逐行的期限檢查只在收到一行時才執行，所以伺服器中途完全
        # 沉默時，最多會超出期限 min(60 秒, 剩餘期限)——這是刻意接受的上限：同步 httpx 無法
        # 從外部可靠地打斷阻塞中的讀取（另開執行緒關 socket 在 Linux 上不保證喚醒 recv）。
        request = client.build_request(
            "POST", url, json=body, headers=_headers(key),
            timeout=_timeout(_READ_TIMEOUT, cap=remaining),
        )
    except (httpx.InvalidURL, ValueError, UnicodeError) as exc:
        return _fail(CONFIG, _transport_detail(exc), t0), ""
    try:
        response = client.send(request, stream=True)
    except httpx.ConnectTimeout as exc:
        return _fail(NETWORK, _transport_detail(exc), t0), ""
    except httpx.TimeoutException as exc:
        return _fail(_timed_out_kind(deadline), _transport_detail(exc), t0), ""
    except httpx.TransportError as exc:
        return _fail(NETWORK, _transport_detail(exc), t0), ""

    acc = _Acc()
    parts: list[str] = []
    ttft_ms: int | None = None
    try:
        if response.status_code != 200:
            raw = b""
            with contextlib.suppress(httpx.HTTPError):
                raw = response.read()
            kind, detail = classify_status(response.status_code, raw)
            return _fail(
                kind, detail, t0, status=response.status_code,
                retry_after=_retry_after(response.headers),
            ), ""
        try:
            for line in _iter_sse_lines(response):
                if time.monotonic() > deadline:
                    return _fail(TIMEOUT, "超過總期限", t0), ""
                event = parse_sse_line(line)
                if event is None:
                    continue
                if event is DONE:
                    # 批次刻意不像線上那樣讀完 chunked 結尾：同步讀取無法有界地收手，伺服器
                    # 異常時最久會卡住一個 read 逾時；而批次每篇本來就要數秒，省一次交握可忽略。
                    acc.done = True
                    break
                if not isinstance(event, dict):
                    continue
                text = acc.feed(event)
                if acc.error is not None:
                    break
                if text:
                    if ttft_ms is None:
                        ttft_ms = int((time.monotonic() - t0) * 1000)
                    parts.append(text)
        except httpx.TimeoutException as exc:
            if acc.finish_reason is None:
                return _fail(_timed_out_kind(deadline), _transport_detail(exc), t0), ""
        except httpx.HTTPError as exc:
            if acc.finish_reason is None:
                acc.error = (NETWORK, _transport_detail(exc))
    finally:
        with contextlib.suppress(Exception):
            response.close()
    return _outcome(acc, bool(parts), t0, ttft_ms), "".join(parts)


def complete_chat(
    model: str,
    prompt: str,
    *,
    max_tokens: int,
    timeout: float,
    system: str | None = None,
    task: str = "-",
    user_id: str | None = None,
    retries: int = 2,
    sleep=time.sleep,
) -> ChatResult:
    """同步呼叫（批次用）。回完整文字，或 `API[<kind>]` 開頭、說得出原因的錯誤。

    - `timeout` 是涵蓋所有嘗試的總期限。
    - 只對暫時性錯誤（429、5xx、網路）退避重試，最多 `retries` 次：優先照 `Retry-After`
      （上限 60 秒），否則 2／6 秒加抖動；等待會超過期限就不等了。
    - 截斷、審查、空回應、400、帳號錯誤一律不重試：重打同一個 prompt 只是再付一次錢。
    - 批次沒有「部分成功」：串流中途出事就整篇失敗。
    """
    deadline = time.monotonic() + timeout
    attempts = 0
    out = ChatOutcome(kind=OTHER)
    text = ""
    for attempt in range(retries + 1):
        attempts += 1
        out, text = _complete_once(
            model, prompt, max_tokens=max_tokens, system=system,
            user_id=user_id, deadline=deadline,
        )
        if out.kind is None:
            break
        if out.kind not in TRANSIENT_KINDS or attempt >= retries:
            break
        if out.retry_after is not None:
            wait = min(out.retry_after, _RETRY_AFTER_CAP)
        else:
            wait = _BATCH_BACKOFF[min(attempt, len(_BATCH_BACKOFF) - 1)] * random.uniform(0.8, 1.2)
        if time.monotonic() + wait >= deadline:
            break
        sleep(wait)
    _log_call(task, model, out, attempts)
    if out.kind is None:
        return ChatResult(text=text, error=None, kind=None, attempts=attempts, outcome=out)
    detail = out.detail
    if out.kind == TRUNCATED:
        detail = f"max_tokens={max_tokens}"
    return ChatResult(
        text=None, error=error_string(out.kind, detail), kind=out.kind,
        attempts=attempts, outcome=out,
    )
