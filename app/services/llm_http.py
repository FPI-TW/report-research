"""DeepSeek Chat Completions 的 HTTP 客戶端（官方 OpenAI 相容端點）。

本模組只負責「打一次 API、把結果與失敗原因說清楚」，不決定哪個任務用哪個模型、也不做
呼叫端的重試策略——那兩件事分別在分派層（`llm.stream_completion`、`scripts/_claude_cli.
run_claude`）與各呼叫點。刻意是**葉模組**：只 import 標準函式庫與 httpx，專案內唯一的例外
是同樣只依賴標準函式庫的 `app.services.llm_models`（白名單的所在）；不 import 其他
`app.*`／`web.*`（`query_planner.py` 開頭的依賴約束、`retrieval_pipeline`↔`answer` 的
刻意循環都不能被它牽動；由 tests/test_llm_http.py 的 AST 測試釘住）。

設計要點（每一條都有對應的事故或官方文件，改之前先讀）：

- **thinking 預設是開的**（官方 thinking_mode 指南）。不關的話 TTFT 變長、token 成本上升，
  `max_tokens` 被推理吃光時 content 會是空的。每個請求都**同時**送兩個開關
  `thinking.type=disabled` 與 `reasoning_effort=none`：文件沒寫兩者衝突時誰優先，只送一個
  不保證關得掉。`reasoning_content` 一律丟棄、只記長度（比照 PR-M 前 CLI 路徑忽略 thinking_delta）。
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
    單元測試看到的就是生產行為。
    - 首字之前 httpx 的 read 逾時（伺服器 `_READ_TIMEOUT` 秒沒送任何位元組，連 keep-alive 都沒有）
      歸 `TIMEOUT` 而不是 `NETWORK`：它和首字期限到了是同一件事——伺服器沒回應，再等一輪無益
      （PR-M 前的 CLI 路徑也是這個語意：逾時不重試）。歸 NETWORK 的話外層會重試，主答（首字期限 120 秒）最壞
      要 3×60 秒加退避才失敗。連線逾時（connect）與其他傳輸錯誤仍是 NETWORK。批次（`complete_chat`）
      刻意不同：它有涵蓋所有嘗試的總期限兜底，沉默在期限內仍歸 NETWORK 重試（`_timed_out_kind`）。
    - 第一個字之後另有寬鬆的**總時限**（`total_timeout`，從呼叫開始算；呼叫端傳
      `LLM_HTTP_TOTAL_TIMEOUT`），同樣每個 await 各自包 `timeout_at`、不跨 yield。它只是最後一道
      牆鐘上限：正常收尾靠 `max_tokens` 與 read 逾時，而伺服器每 60 秒內滴一點內容時兩者都收不了。
      到期時已吐字＝`TIMEOUT` 且 `streamed=True`（呼叫端當截斷處理），已收到 finish_reason 則以
      它為準。
  - 批次（`complete_chat`）的 `timeout` 是涵蓋傳輸層重試的**總期限**，用 `time.monotonic()`
    **逐 chunk** 檢查（不是逐行：伺服器持續送沒有換行的位元組時一行永遠湊不滿）。httpx 的 read
    逾時會被伺服器排隊時的 `: keep-alive` 一直重置（官方：最長 10 分鐘），不能拿來當總時限。
    已吐字後才到期歸 `TIMEOUT_STREAMED`（期限型截斷：可重放、連續 3 輪才跳過、計入斷路器；**不是**
    `TRUNCATED`，那只留給 `finish_reason=length`）；已吐字後的任何失敗都不在傳輸層重試
    （`complete_chat` docstring）。
- **金鑰在呼叫時才讀 `os.environ`**，不進 Settings：批次腳本不讀 repo 根 `.env`，而
  `get_settings()` 是 import 期就快取的單例（`app/services/db.py`），先快取到空值就一路空到底。
- **不用 openai SDK**：它內建的重試會吃掉失敗原因，違反 `scripts/_claude_cli.py` 開頭四天
  停擺紀錄的教訓（失敗原因必須說得出口）。
- **judge 走非串流 JSON 模式**（`complete_json`，遷移 PR-18）：judge 不需要串流，而非串流回應才帶
  `system_fingerprint`（9/24 探測）。重試只在這一層、有界（`JSON_MAX_ATTEMPTS`），呼叫端再依自己的
  預算壓低，不另外疊一層（app/services/faithfulness.py 模組 docstring 的「重試層數」）。
"""

from __future__ import annotations

import asyncio
import atexit
import contextlib
import json
import logging
import math
import os
import random
import re
import threading
import time
import weakref
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import httpx

# 走 HTTP 的模型白名單搬到葉模組 llm_models（`app.config` 與批次預檢也要用它，留在這裡會
# 形成 config ↔ llm_http 的 import 循環）。這裡以原名重新匯出，既有呼叫端與測試不必改。
from app.services.llm_models import HTTP_MODELS, is_http_model  # noqa: F401

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.deepseek.com"

# ── 失敗分類 ─────────────────────────────────────────────────────────────────
AUTH = "auth"                      # 缺金鑰或 401
QUOTA = "quota"                    # 402 餘額不足
CONFIG = "config"                  # 404／模型不存在
CONTENT_FILTER = "content_filter"  # 400 Content Exists Risk、finish_reason=content_filter
BAD_REQUEST = "bad_request"        # 其他 400／422：多半是單篇輸入造成
OVERLOADED = "overloaded"          # 429、5xx、insufficient_system_resource
NETWORK = "network"                # 連線、DNS、TLS、串流中途斷線
TIMEOUT = "timeout"                # 首字期限（串流）或總期限（批次）到了
TRUNCATED = "truncated"            # finish_reason=length（決定性：同一輸入、同一上限重送結果不變）
TIMEOUT_STREAMED = "timeout_streamed"  # 批次：已吐字後總期限才到（期限型截斷，見 `_deadline_after_text`）
EMPTY = "empty"                    # 成功結束卻沒有 content（包括只有 reasoning）
INVALID_JSON = "invalid_json"      # JSON 模式（`complete_json`）：finish_reason=stop 但 content 不是合法 JSON
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
    TIMEOUT_STREAMED: "已吐字後逾時",
    EMPTY: "空回應",
    INVALID_JSON: "回應不是合法 JSON",
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

    前綴 `API[` 是契約：呼叫端（`scripts/_claude_cli.is_retryable`）靠它判定「傳輸層已重試過或本來就是決定性的」。
    """
    s = f"API[{kind}] {_PHRASES.get(kind, _PHRASES[OTHER])}"
    if detail:
        s += f"：{detail}"
    return _one_line(_redact(s))[:300]


def sanitize(text: str) -> str:
    """去 NUL、孤立代理字元換成 U+FFFD。

    PDF 抽出的文字偶有 `\\x00`（PR-M 前的 CLI 路徑為了 argv 剝掉；這裡沒有 argv 但仍要剝，
    否則同一篇研報與 Claude 時代的輸入不同）；孤立代理字元會讓 UTF-8 編碼直接拋錯。兩者都是
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
    temperature: float | None = None,
    response_format: dict | None = None,
) -> dict:
    """組請求 body（純函式）。thinking 一律關：兩個開關都送，見模組 docstring。

    `temperature`／`response_format` 預設不送（沿用伺服器預設）；judge（`complete_json`）送
    `temperature=0` 與 `{"type": "json_object"}`。
    """
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
    if temperature is not None:
        body["temperature"] = temperature
    if response_format is not None:
        body["response_format"] = response_format
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


class _DeadlineExceeded(Exception):
    """批次的總期限在串流中到了（`_iter_sse_lines` 逐 chunk 檢查）。"""


def _iter_sse_lines(response: httpx.Response, deadline: float | None = None):
    """同步版分行。`deadline`（monotonic）：**每收到一個 chunk 就檢查**，到了拋 `_DeadlineExceeded`。

    不能只在湊滿一行時檢查：伺服器持續送沒有換行的位元組時一行永遠湊不滿，期限就被無限延長
    （httpx 的 read 逾時也會被這些位元組一直重置）。
    """
    splitter = _LineSplitter()
    for text in response.iter_text():
        if deadline is not None and time.monotonic() > deadline:
            raise _DeadlineExceeded
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
    # 非串流回應才有（`complete_json`；9/24 探測實測有值）。同一個模型名可能在伺服端換了底層模型，
    # judge 靠它與 `model_resp` 事後分辨「量尺是不是悄悄換了」。
    system_fingerprint: str | None = None


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


# 本行程最近一次真實請求收到 402 的時刻（monotonic；0＝從未）。`/healthz/llm` 據此在餘額查詢之間
# 就翻成 exhausted，直到一次「開始於這個時刻之後」的成功餘額查詢才解除（`app/services/llm_health.py`）。
# 只記本行程：web 的問答碰到 402 立刻看得到；批次行程的 402 由它自己的整批 rc=2 → OnFailure 告警，
# web 端等餘額查詢的快取到期才會知道（取捨見 llm_health 的模組 docstring）。
_quota_seen_at: float = 0.0


def last_quota_at() -> float:
    return _quota_seen_at


def _log_call(task: str, model: str, out: ChatOutcome, attempts: int = 1) -> None:
    """每次呼叫一行結構化 log（不含 prompt、不含 header）；402 另記下時刻（見 `_quota_seen_at`）。

    放在這裡是因為它是線上（`astream_chat`）與批次（`complete_chat`）共同的收尾點，每次呼叫恰好一次。
    """
    global _quota_seen_at
    if out.kind == QUOTA:
        _quota_seen_at = time.monotonic()
    usage = out.usage or {}
    details = usage.get("completion_tokens_details") or {}
    logger.info(
        "llm_call task=%s model=%s model_resp=%s backend=http kind=%s finish=%s attempts=%d "
        "ttft_ms=%s total_ms=%d hit=%s miss=%s out=%s reasoning=%s fp=%s",
        task, model, out.model_resp, out.kind or "ok", out.finish_reason, attempts,
        out.ttft_ms, out.total_ms, usage.get("prompt_cache_hit_tokens"),
        usage.get("prompt_cache_miss_tokens"), usage.get("completion_tokens"),
        details.get("reasoning_tokens"), out.system_fingerprint,
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


def _base_url() -> str:
    return ((os.getenv("DEEPSEEK_BASE_URL") or "").strip() or DEFAULT_BASE_URL).rstrip("/")


def _url() -> str:
    return _base_url() + "/chat/completions"


def api_key_configured() -> bool:
    """`DEEPSEEK_API_KEY` 有沒有值（不看內容；`/healthz/llm` 判 disabled 用）。"""
    return bool(_api_key())


def _timeout(read: float, cap: float | None = None) -> httpx.Timeout:
    """`cap`＝剩餘期限（批次）：connect／read／write 都不超過它。

    `DEEPSEEK_CONNECT_TIMEOUT` 非數字、nan／inf 或 ≤0 一律退回 10 秒（同 `app.config._positive_float`；
    本模組是葉模組，不 import `app.config`）：`float()` 收 `"nan"`／`"inf"`，而 nan、inf、負數都
    不是合法的 socket 逾時值。
    """
    try:
        connect = float(os.getenv("DEEPSEEK_CONNECT_TIMEOUT") or 10)
    except ValueError:
        connect = 10.0
    if not math.isfinite(connect) or connect <= 0:
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


def _silent_detail(exc: BaseException, reasoning_chars: int = 0) -> str:
    """首字前 read 逾時的 detail：說出是伺服器沉默，不是我們的首字期限。

    已收到 reasoning 才沉默時換一種說法：thinking 應已關閉（見模組 docstring），這時伺服器其實
    回應過，「未送出任何位元組」會把人帶去查連線。分類不變（TIMEOUT、不重試）。
    """
    if reasoning_chars > 0:
        msg = f"reasoning（{reasoning_chars} 字）後伺服器沉默 {_READ_TIMEOUT:g}s、尚未輸出內容"
    else:
        msg = f"首字前伺服器 {_READ_TIMEOUT:g}s 未送出任何位元組"
    return _one_line(f"{msg}（{type(exc).__name__}）")[:200]


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
    total_timeout: float | None = None,
) -> AsyncIterator[str | ChatOutcome]:
    """串流呼叫：逐段 yield content 文字，最後 yield 一個 `ChatOutcome`（恰好一次）。

    不做重試（重試策略在 `llm.stream_completion`，它才知道「已吐字就不重試」）。任何
    httpx 例外都轉成 outcome，不往外拋；只有 `CancelledError` 原樣傳遞，並經 finally
    關閉回應。

    `total_timeout`：整次呼叫的牆鐘上限（None＝不設）；首字期限取兩者較早的。語意見模組
    docstring 的「逾時語意」。
    """
    t0 = time.monotonic()
    started = asyncio.get_running_loop().time()
    deadline = started + first_token_timeout
    total_deadline = None if total_timeout is None else started + total_timeout
    if total_deadline is not None and total_deadline < deadline:
        deadline = total_deadline
        first_token_timeout = total_timeout
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
        except UnicodeError as exc:  # 先於 ValueError（它是子類）：單篇輸入的編碼問題，見 `_complete_once`
            out = _fail(BAD_REQUEST, _transport_detail(exc), t0)
        except (httpx.InvalidURL, ValueError) as exc:
            out = _fail(CONFIG, _transport_detail(exc), t0)
        else:
            try:
                async with asyncio.timeout_at(deadline):
                    response = await client.send(request, stream=True)
            except TimeoutError:
                out = _fail(TIMEOUT, f"首字期限 {first_token_timeout:g}s 內未收到回應", t0)
            except httpx.ReadTimeout as exc:  # 伺服器沉默（首字前）＝逾時，不重試（見模組 docstring）
                out = _fail(TIMEOUT, _silent_detail(exc), t0)
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
                    # 每個 await 各自包期限，不跨越 yield（見模組 docstring）：首字前是首字期限，
                    # 之後是總時限（None＝不設）。
                    async with asyncio.timeout_at(total_deadline if got_text else deadline):
                        line = await anext(lines)
                except StopAsyncIteration:
                    break
                except TimeoutError:
                    if not got_text:
                        out = _fail(TIMEOUT, f"首字期限 {first_token_timeout:g}s 內未收到內容", t0)
                    elif acc.finish_reason is None:
                        # 已吐字才到總時限：與 read 逾時同樣是「中途收掉」，呼叫端當截斷處理
                        acc.error = (TIMEOUT, f"總時限 {total_timeout:g}s 到期（已吐字）")
                    break
                except httpx.ReadTimeout as exc:
                    if not got_text:  # 首字前的沉默＝逾時，不是網路錯誤（不重試）
                        out = _fail(TIMEOUT, _silent_detail(exc, acc.reasoning_chars), t0,
                                    reasoning_chars=acc.reasoning_chars)
                    elif acc.finish_reason is None:
                        acc.error = (NETWORK, _transport_detail(exc))
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


# ── 餘額查詢（`/healthz/llm`）────────────────────────────────────────────────
BALANCE_TIMEOUT = 4.0


@dataclass
class BalanceResult:
    """`GET /user/balance` 的結果。

    `kind is None`＝HTTP 200 且本體是 JSON 物件，`body` 原樣交給呼叫端判讀（幣別、門檻是呼叫端的
    事，這裡不解讀金額）；否則 `kind` 是本模組的錯誤 kind：401→AUTH、402→QUOTA、429／5xx→OVERLOADED、
    連線→NETWORK、逾時→TIMEOUT、端點設定錯→CONFIG、200 但不是 JSON 物件→OTHER。
    """

    kind: str | None
    detail: str = ""
    status: int | None = None
    body: dict | None = None


async def fetch_balance(timeout: float = BALANCE_TIMEOUT) -> BalanceResult:
    """查一次帳戶餘額（官方 `GET /user/balance`；9/24 實測不扣費）。任何失敗都轉成結果，不往外拋。

    `timeout` 是整次的牆鐘上限（連線、讀取、DNS 全算在內），呼叫端是每 2 分鐘一次的本機探針，
    它的 curl 只等 5 秒。不重試：下一輪探針就是重試，連續失敗的判定在呼叫端。
    """
    t0 = time.monotonic()
    pre = _preflight(t0)
    if isinstance(pre, ChatOutcome):
        return BalanceResult(pre.kind, pre.detail)
    key, _chat_url = pre
    headers = {"Authorization": f"Bearer {key}", "Accept": "application/json"}
    try:
        async with asyncio.timeout(timeout):
            response = await _async_client().get(
                _base_url() + "/user/balance", headers=headers, timeout=_timeout(timeout, cap=timeout),
            )
    except (TimeoutError, httpx.TimeoutException) as exc:
        return BalanceResult(TIMEOUT, f"餘額查詢 {timeout:g}s 內未完成（{type(exc).__name__}）")
    except httpx.TransportError as exc:
        return BalanceResult(NETWORK, _transport_detail(exc))
    except (httpx.InvalidURL, ValueError) as exc:
        return BalanceResult(CONFIG, _transport_detail(exc))
    if response.status_code != 200:
        kind, detail = classify_status(response.status_code, response.content)
        return BalanceResult(kind, detail, status=response.status_code)
    try:
        body = response.json()
    except ValueError:
        return BalanceResult(OTHER, "餘額回應不是 JSON", status=200)
    if not isinstance(body, dict):
        return BalanceResult(OTHER, "餘額回應不是 JSON 物件", status=200)
    return BalanceResult(None, "", status=200, body=body)


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


def _deadline_after_text(acc: _Acc, t0: float, ttft_ms: int | None, chars: int) -> ChatOutcome:
    """已吐字後總期限才到：`TIMEOUT_STREAMED`（期限型截斷、`streamed=True`），不是 `TIMEOUT` 也不是 `TRUNCATED`。

    - 不是 `TRUNCATED`：`finish_reason=length` 是決定性的（同一輸入、同一上限重送結果不變，1 次就跳過），
      期限型不是——「DeepSeek 暫時變慢」時它會落在每一篇上。初版歸 `TRUNCATED`，於是一次變慢就讓整批
      研報 1 次進跳過名單、行內標註進 `tag_truncated`（`failures_to_delta` 預設不撈），而且斷路器不跳。
    - 不是 `TIMEOUT`：已吐字＝已計費，要記跳過名單（連續 3 輪才跳過，`llm_failures.TIMEOUT_STREAMED`），
      否則真正「每次都寫不完」的那篇每輪都重打、每輪都付一次錢。
    - **計入斷路器**（`_claude_cli.BREAKER_KINDS`）：供應商整體變慢時整段中止，而不是每篇都等到期限。
    - 已吐字，照樣不在傳輸層重試（`complete_chat`）。
    線上的 `astream_chat` 沒有這個 kind：總時限到時已吐字是 `TIMEOUT`＋`streamed=True`，呼叫端當截斷附註。
    """
    return _fail(
        TIMEOUT_STREAMED, f"已吐字 {chars} 字後超過總期限", t0, streamed=True, usage=acc.usage,
        model_resp=acc.model_resp, reasoning_chars=acc.reasoning_chars, ttft_ms=ttft_ms,
    )


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
        # 各項逾時都不超過剩餘期限。逐 chunk 的期限檢查只在收到位元組時才執行，所以伺服器中途
        # 完全沉默時，最多會超出期限 min(60 秒, 剩餘期限)——這是刻意接受的上限：同步 httpx 無法
        # 從外部可靠地打斷阻塞中的讀取（另開執行緒關 socket 在 Linux 上不保證喚醒 recv）。
        request = client.build_request(
            "POST", url, json=body, headers=_headers(key),
            timeout=_timeout(_READ_TIMEOUT, cap=remaining),
        )
    except UnicodeError as exc:
        # 單篇輸入的編碼問題（孤立代理字元之類；`build_body` 已經 `sanitize`，照理不會再發生）：單篇失敗、
        # 記跳過名單。歸 CONFIG 的話一篇怪字元就整批 rc=2 中止、而且不留紀錄，下一輪同一篇再中止一次。
        # 必須在 ValueError 之前：UnicodeError 是 ValueError 的子類。status 為 None（本機就失敗），不參與 400 升級。
        return _fail(BAD_REQUEST, _transport_detail(exc), t0), ""
    except (httpx.InvalidURL, ValueError) as exc:
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
            for line in _iter_sse_lines(response, deadline):
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
        except _DeadlineExceeded:
            if acc.finish_reason is None:
                if parts:
                    return _deadline_after_text(acc, t0, ttft_ms, sum(map(len, parts))), ""
                return _fail(TIMEOUT, "超過總期限", t0, usage=acc.usage, model_resp=acc.model_resp), ""
        except httpx.TimeoutException as exc:
            if acc.finish_reason is None:
                kind = _timed_out_kind(deadline)
                if parts and kind == TIMEOUT:
                    return _deadline_after_text(acc, t0, ttft_ms, sum(map(len, parts))), ""
                # 已吐字但期限未到的沉默：NETWORK，且 streamed=True——complete_chat 不在傳輸層重試
                return _fail(
                    kind, _transport_detail(exc), t0, streamed=bool(parts), usage=acc.usage,
                    model_resp=acc.model_resp, ttft_ms=ttft_ms,
                ), ""
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

    - `timeout` 是涵蓋所有嘗試的總期限（每收到一個 chunk 檢查一次）。
    - 只對暫時性錯誤（429、5xx、網路）退避重試，最多 `retries` 次：優先照 `Retry-After`
      （上限 60 秒），否則 2／6 秒加抖動；等待會超過期限就不等了。
    - **已吐字（`outcome.streamed`）就不在傳輸層重試**，不管 kind 是什麼：那一次已經計費，串流中途
      的錯誤物件、`insufficient_system_resource`、中途斷線照 kind 回傳，批次當單篇失敗（`API[` 前綴，
      腳本層也不重試）。否則一篇一輪最多 3（傳輸）×3（腳本層的解析重試）＝9 個已計費請求（審查中3）。
      線上路徑（`astream_chat`）本來就是已吐字不重試。
    - 截斷、審查、空回應、400、帳號錯誤一律不重試：重打同一個 prompt 只是再付一次錢。
    - 已吐字後總期限才到＝`TIMEOUT_STREAMED`（見 `_deadline_after_text`），不是 `TIMEOUT`／`TRUNCATED`。
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
        if out.kind not in TRANSIENT_KINDS or out.streamed or attempt >= retries:
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
    if out.kind == TRUNCATED and out.finish_reason == "length":
        detail = f"max_tokens={max_tokens}"
    return ChatResult(
        text=None, error=error_string(out.kind, detail), kind=out.kind,
        attempts=attempts, outcome=out,
    )


# ── judge：非串流 JSON 模式（DeepSeek 遷移 PR-18）──────────────────────────────
# 一次 `complete_json` 最多送出的請求數（首次＋1 次重試）。三種可重試的結局共用這 1 次：
# finish_reason=length（以 2 倍 max_tokens 重送）、空 content（官方文件記載 JSON 模式偶發）、暫時性錯誤
# （429／5xx／網路）。呼叫端可再壓低（`max_attempts`）：judge 的「單層重試」就是由呼叫端依剩餘預算
# 傳入，見 app/services/judge_schema.py 的 `HTTP_STAGE_MAX_REQUESTS`。
JSON_MAX_ATTEMPTS = 2
_JSON_BACKOFF = 2.0
# judge 是背景抽查與離線評測，等 Retry-After 最多這麼久；再久就放棄這一次（總期限也會先到）。
_JSON_RETRY_AFTER_CAP = 10.0
# 截斷重試的時間門檻：剩餘期限 < 這次耗時 × 這個倍數就不重試（見 `complete_json` docstring）。
_JSON_TRUNC_RETRY_TIME_RATIO = 1.5
_USAGE_KEYS = ("prompt_tokens", "completion_tokens", "prompt_cache_hit_tokens", "prompt_cache_miss_tokens")


@dataclass
class JsonResult:
    """`complete_json` 的結果。`kind is None`＝成功，`data` 是解析後的 JSON（dict 或 list）。

    - `attempts`：實際送出的請求數（含重試）。judge 的重試預算據此扣。
    - `max_tokens`：最後一次請求用的上限（截斷重試後是 2 倍）。
    - `usage`：**所有嘗試**的 token 加總（每次嘗試都計費）；`outcome.usage` 只是最後一次。
    - `outcome`：最後一次嘗試的結局（`model_resp`、`system_fingerprint`、`finish_reason`、`status`）。
    """

    data: dict | list | None
    kind: str | None
    detail: str = ""
    attempts: int = 0
    max_tokens: int = 0
    usage: dict = field(default_factory=dict)
    outcome: ChatOutcome = field(default_factory=lambda: ChatOutcome(kind=None))

    @property
    def error(self) -> str | None:
        return None if self.kind is None else error_string(self.kind, self.detail)


def _json_headers(key: str) -> dict:
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json", "Accept": "application/json"}


def _add_usage(total: dict, usage: dict | None) -> None:
    for k in _USAGE_KEYS:
        v = (usage or {}).get(k)
        if isinstance(v, int) and not isinstance(v, bool):
            total[k] = total.get(k, 0) + v


def _parse_json_response(raw: bytes, t0: float) -> tuple[ChatOutcome, object]:
    """HTTP 200 的本體 → (outcome, data)。finish_reason 必須是 stop 且 content 是合法 JSON 才算成功。"""
    try:
        # 伺服器排隊時在本體前送空行保持連線（官方文件）；JSON 允許前導空白，不必先剝。
        obj = json.loads(raw)
    except ValueError:
        return _fail(OTHER, "回應本體不是 JSON", t0, status=200), None
    if not isinstance(obj, dict):
        return _fail(OTHER, "回應本體不是 JSON 物件", t0, status=200), None
    if obj.get("error") is not None:
        message = _error_message(json.dumps({"error": obj["error"]}))
        kind = CONTENT_FILTER if _is_content_risk(message) else OVERLOADED
        return _fail(kind, _redact(f"response error {message}"), t0, status=200), None
    choices = obj.get("choices") or []
    choice = choices[0] if choices and isinstance(choices[0], dict) else {}
    message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
    content = message.get("content") if isinstance(message.get("content"), str) else ""
    reasoning = message.get("reasoning_content")
    finish = choice.get("finish_reason")
    fp = obj.get("system_fingerprint")
    meta = dict(
        status=200, finish_reason=finish,
        usage=obj.get("usage") if isinstance(obj.get("usage"), dict) else None,
        model_resp=obj.get("model") if isinstance(obj.get("model"), str) else None,
        reasoning_chars=len(reasoning) if isinstance(reasoning, str) else 0,
        system_fingerprint=fp if isinstance(fp, str) else None,
    )
    if finish == "content_filter":
        return _fail(CONTENT_FILTER, "finish_reason=content_filter", t0, **meta), None
    if finish == "length":
        return _fail(TRUNCATED, "finish_reason=length", t0, **meta), None
    if finish == "insufficient_system_resource":
        return _fail(OVERLOADED, "finish_reason=insufficient_system_resource", t0, **meta), None
    if finish != "stop":
        return _fail(OTHER, f"finish_reason={finish}", t0, **meta), None
    if not content.strip():
        detail = "只有 reasoning、沒有 content" if meta["reasoning_chars"] else "沒有 content"
        return _fail(EMPTY, detail, t0, **meta), None
    try:
        data = json.loads(content)
    except ValueError:
        return _fail(INVALID_JSON, f"content 不是合法 JSON（{len(content)} 字）", t0, **meta), None
    if not isinstance(data, (dict, list)):
        return _fail(INVALID_JSON, f"content 是 JSON 純量（{type(data).__name__}）", t0, **meta), None
    return ChatOutcome(kind=None, total_ms=int((time.monotonic() - t0) * 1000), **meta), data


async def _json_once(
    model: str, prompt: str, *, system: str | None, max_tokens: int, user_id: str | None, deadline: float,
) -> tuple[ChatOutcome, object]:
    t0 = time.monotonic()
    pre = _preflight(t0)
    if isinstance(pre, ChatOutcome):
        return pre, None
    key, url = pre
    loop = asyncio.get_running_loop()
    remaining = deadline - loop.time()
    if remaining <= 0:
        return _fail(TIMEOUT, "總期限已過", t0), None
    try:
        body = build_body(
            model, prompt, max_tokens=max_tokens, system=system, stream=False, user_id=user_id,
            temperature=0, response_format={"type": "json_object"},
        )
        client = _async_client()
        request = client.build_request(
            "POST", url, json=body, headers=_json_headers(key), timeout=_timeout(_READ_TIMEOUT, cap=remaining),
        )
    except UnicodeError as exc:  # 先於 ValueError（它是子類）：單篇輸入的編碼問題，見 `_complete_once`
        return _fail(BAD_REQUEST, _transport_detail(exc), t0), None
    except (httpx.InvalidURL, ValueError) as exc:
        return _fail(CONFIG, _transport_detail(exc), t0), None
    try:
        # 總期限用 asyncio 的期限包住整次請求：伺服器排隊時持續送空行，httpx 的 read 逾時會一直被重置。
        async with asyncio.timeout_at(deadline):
            response = await client.send(request)
    except TimeoutError:
        return _fail(TIMEOUT, f"總期限內未完成（剩 {remaining:.0f}s 時送出）", t0), None
    except httpx.ReadTimeout as exc:
        # 請求已送達、伺服器 `_READ_TIMEOUT` 秒沒回任何位元組：非串流時伺服器要整份生成完才回本體，
        # 生成超過 60 秒又沒送 keep-alive 就會落到這裡（9/24 探測：短回應沒有 keep-alive）。它很可能
        # 已經在生成、會計費，歸 NETWORK 重試就是同一份輸出再付一次錢，而且第二次多半同樣慢。所以
        # 歸 TIMEOUT、不重試——與線上 `astream_chat` 首字前的 ReadTimeout 同一個理由。
        detail = f"伺服器 {_READ_TIMEOUT:g}s 未送出任何位元組（非串流，可能仍在生成）（{type(exc).__name__}）"
        return _fail(TIMEOUT, detail, t0), None
    except httpx.TimeoutException as exc:
        # 連線／寫入／連線池逾時：請求還沒送到伺服器、不會計費，總期限未到時歸 NETWORK 讓呼叫端照暫時性處理
        kind = TIMEOUT if loop.time() >= deadline else NETWORK
        return _fail(kind, _transport_detail(exc), t0), None
    except httpx.TransportError as exc:
        return _fail(NETWORK, _transport_detail(exc), t0), None
    except httpx.HTTPError as exc:
        # 其餘 httpx 例外（DecodingError：本體的 Content-Encoding 解不開；TooManyRedirects）：不是傳輸層
        # 斷線，重送多半得到同一個結果、而且本體已經生成＝已計費，所以歸 OTHER、不重試。同檔另兩條路
        # （`astream_chat`、`_complete_once`）也接 HTTPError；只接 TransportError 的話它會漏出 complete_json，
        # 違反「任何失敗都轉成結果」的契約。
        return _fail(OTHER, _transport_detail(exc), t0), None
    if response.status_code != 200:
        kind, detail = classify_status(response.status_code, response.content)
        return _fail(
            kind, detail, t0, status=response.status_code, retry_after=_retry_after(response.headers),
        ), None
    return _parse_json_response(response.content, t0)


async def complete_json(
    model: str,
    prompt: str,
    *,
    max_tokens: int,
    timeout: float,
    system: str | None = None,
    task: str = "-",
    user_id: str | None = None,
    max_attempts: int = JSON_MAX_ATTEMPTS,
    sleep=asyncio.sleep,
) -> JsonResult:
    """非串流、JSON 模式的單次呼叫（judge 用）。任何失敗都轉成結果，不往外拋（`CancelledError` 除外）。

    請求固定 `stream=false`、`response_format={"type":"json_object"}`、`temperature=0`、thinking 兩個開關
    都關（`build_body`）。JSON 模式要求 prompt 含「json」字樣：9/24 探測實測大寫 `JSON` 也算數，現行四支
    judge 系統提示都有「只輸出 JSON」，所以不必改提示（改了就是換尺，`judge_prompt_sha` 會變）。

    - `timeout`：涵蓋所有嘗試（含退避）的**總期限**，以 asyncio 期限實作（見 `_json_once`）。
    - 回應必須 `finish_reason=stop` 且 content 是 JSON 物件或陣列，否則照 kind 回傳：
      `length`→`TRUNCATED`、`content_filter`→`CONTENT_FILTER`、空 content→`EMPTY`、不是 JSON→`INVALID_JSON`。
    - 重試（最多 `max_attempts`，預設 `JSON_MAX_ATTEMPTS`＝2 個請求）只給三種：`TRUNCATED` 以 2 倍
      `max_tokens` 重送、`EMPTY` 原樣重送、暫時性錯誤（`TRANSIENT_KINDS`）退避後重送（優先照
      `Retry-After`，上限 `_JSON_RETRY_AFTER_CAP`；等待會超過總期限就不等）。
    - 審查、帳號（401／402／404）、400、`INVALID_JSON`、逾時一律不重試：結果不會變，重打只是再付一次錢。
      帳號錯誤要由呼叫端升級（離線整批中止、生產記 degraded），不在這裡處理。
    - httpx 的 read 逾時（請求已送出、伺服器 60 秒沒回任何位元組）歸 `TIMEOUT`、不重試：非串流時
      伺服器整份生成完才回本體，這時多半已在生成、會計費（見 `_json_once`）。連線／寫入逾時仍是
      `NETWORK`（沒送到、不計費）。

    **已知限制**：截斷重試與期限共用同一個 `timeout`。剩餘期限不到這次耗時的
    `_JSON_TRUNC_RETRY_TIME_RATIO`（1.5）倍時不重試、直接回 `TRUNCATED`（detail 註明）——大上限的
    階段（拆解 8192 token）一次就可能吃掉大半期限，這時 2 倍上限的重送在期限內幾乎跑不完，送出去
    只會多付一次錢、再被記成 `TIMEOUT`。門檻是估的（輸出時間大致與 token 數成正比，重送至少要重新
    生成已截斷的那一段），所以仍可能有重送後逾時的情況；反過來，單次生成超過 60 秒（read 逾時）
    的階段在這裡拿不到結果，只能記 `TIMEOUT`。
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    budget = max(1, int(max_attempts))
    cur = int(max_tokens)
    attempts = 0
    usage: dict = {}
    trunc_no_time = False
    while True:
        attempts += 1
        started = loop.time()
        out, data = await _json_once(
            model, prompt, system=system, max_tokens=cur, user_id=user_id, deadline=deadline,
        )
        _add_usage(usage, out.usage)
        if out.kind is None or attempts >= budget:
            break
        if out.kind == TRUNCATED:
            # 以 2 倍上限重送至少要重新生成已截斷的那一段：剩餘期限連這次耗時的
            # `_JSON_TRUNC_RETRY_TIME_RATIO` 倍都不到，送出去多半只會以逾時收場、白付一次錢，
            # 而且 degraded_reason 會被記成 timeout、蓋掉真正的原因（截斷）。
            now = loop.time()
            if deadline - now < _JSON_TRUNC_RETRY_TIME_RATIO * (now - started):
                trunc_no_time = True
                break
            cur *= 2
        elif out.kind == EMPTY:
            pass
        elif out.kind in TRANSIENT_KINDS:
            if out.retry_after is not None:
                wait = min(out.retry_after, _JSON_RETRY_AFTER_CAP)
            else:
                wait = _JSON_BACKOFF * random.uniform(0.8, 1.2)
            if loop.time() + wait >= deadline:
                break
            await sleep(wait)
        else:
            break
    _log_call(task, model, out, attempts)
    if out.kind is None:
        return JsonResult(data=data, kind=None, attempts=attempts, max_tokens=cur, usage=usage, outcome=out)
    detail = f"max_tokens={cur}" if out.kind == TRUNCATED else out.detail
    if trunc_no_time:
        detail += "；剩餘期限不足，未以 2 倍上限重試"
    return JsonResult(
        data=None, kind=out.kind, detail=detail, attempts=attempts, max_tokens=cur, usage=usage, outcome=out,
    )
