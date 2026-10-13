"""統一 LLM 串流客戶端：DeepSeek（`llm_http`）是唯一的 backend。

`stream_completion` 只接受 `llm_models.HTTP_MODELS` 白名單內的 model，交給
`llm_http.astream_chat`（首字期限、錯誤 kind、partial，見 `_stream_http`）。其他名稱（含 `claude-*`、
CLI 別名、打錯字、空字串）一律拋 `LLMUnavailableError(kind="config")`，`allow_web=True` 也是——
不會被送到付費端點，也沒有備援可以改走。

claude CLI backend（CLI 子行程、stream-json 解析、529 重試、`--tools` 旗標）已於遷移終局
PR-M 移除：CLI 的 OAuth 自 2026-09-23 過期、不再修復（計畫 D-C）。**網搜因此暫時沒有後端**：CLI 的
WebSearch 隨之消失，DeepSeek 網搜（Tavily 工具迴圈）延後到 P9。時效題在 `allow_web=True` 拿到 config
錯誤時退回 M4 婉拒（`answer._answer_time_sensitive_web`），`ASK_ENABLE_WEB` 預設關閉。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator

from app.config import get_settings
from app.services import llm_http
from app.services.llm_models import TASK_ASK_ANSWER, is_http_model, resolve_model

logger = logging.getLogger(__name__)

# 主答模型（ASK_ANSWER_MODEL，未設時查預設表：deepseek-flash）。
# 名稱保留：answer.py 的總覽／主答、faithfulness 的預設參數、eval/run_ragas 的生成端都讀它。
# import 期解析：web/server.py 在本模組被 import 之前就先載入 repo 根 .env；批次與評測入口則先呼叫
# scripts/_llm_env.load_llm_env()（/etc/default/report-mark-llm）。
DEFAULT_MODEL = resolve_model(TASK_ASK_ANSWER)

# 串流中表示「模型開始網搜」的控制標記（NUL 包夾，模型文字不可能等於它）。**目前沒有 backend 會送出**：
# 原本由 CLI 的 WebSearch 工具起點觸發，隨 PR-M 移除；保留給 P9 的 DeepSeek 網搜（Tavily 工具迴圈）
# 沿用同一個契約——answer.py 的三條串流路徑與前端的「正在搜尋網路」階段都接在它上面。
SEARCH_EVENT = "\x00WEBSEARCH\x00"


# LLMUnavailableError.reason 的詞彙（最後一次嘗試為什麼失敗）。
UNAVAILABLE_API_ERROR = "api_error"  # 服務回錯、帳號或設定錯誤（timeout／empty 以外的 kind 都歸這裡）
UNAVAILABLE_TIMEOUT = "timeout"      # 一個字都沒吐就逾時（不重試）
UNAVAILABLE_EMPTY = "empty"          # 進程結束卻沒有任何文字


# LLMUnavailableError.kind 的預設值：「未分類」。
KIND_OTHER = "other"


class LLMUnavailableError(RuntimeError):
    """LLM 重試後仍無有效回應，或這次呼叫在送出前就注定失敗（`kind="config"`）。

    三個屬性，粒度不同、並存不互斥：

    - `reason`：粗分三類（`UNAVAILABLE_*`：服務回錯／沒吐字就逾時／全空），外部直接建構時為
      None。**保留是為了相容**：它是 CLI 時代的詞彙，但 faithfulness 的 degraded_reason（逾時與
      其他分開記）與 eval/judge 的重試判斷仍讀它；`_stream_http` 由 kind 換算填入（逾時→timeout、
      空回應→empty、其餘→api_error，見 `_reason_for`），那些呼叫端不必改。新程式碼讀 `kind`。
    - `kind`：細分類，詞彙同 `llm_http` 的錯誤 kind（auth／quota／config／content_filter／
      bad_request／overloaded／network／timeout／empty／other），由狀態碼與 finish_reason 決定、
      不解析文字。model 不在白名單或要求網搜時是 `config`（`stream_completion` 送出前就拋）。
      外部直接建構且沒給時是 `other`＝未分類。
    - `partial`：True＝**已經吐過字**才失敗（目前只有內容審查截斷與非預期例外會這樣拋）。
      串流型呼叫端（總覽、時效網搜、主答）據此保留已送出的文字、由 Python 附註中斷原因；
      收齊型呼叫端照舊 `except Exception` fail-open。
    """

    def __init__(
        self, *args, reason: str | None = None, kind: str = KIND_OTHER, partial: bool = False,
    ) -> None:
        super().__init__(*args)
        self.reason = reason
        self.kind = kind or KIND_OTHER
        self.partial = partial


# ── HTTP 路徑（DeepSeek，白名單內的 model）───────────────────────────────────
# 呼叫端沒給 max_tokens 時的保底。每個呼叫點都該自己給（tests/test_llm.py 靜態釘住），
# 這裡只防漏網：不設的話官方非 thinking 預設上限 8K，與這個值相同，行為不會更差。
_HTTP_FALLBACK_MAX_TOKENS = 8192
# 外層重試的等待：優先照 Retry-After，但線上有人在等，上限 10 秒；沒有就 1.5／3 秒。
_HTTP_RETRY_AFTER_CAP = 10.0
# 外層重試的等待（測試注入點：換成只記秒數的假物件，不必 patch 全域的 asyncio.sleep）。
_retry_sleep = asyncio.sleep

# 已吐字後才出事時寫進 meta["truncated_reason"]（呼叫端落 `qa_log.filters.llm_truncated`）。
TRUNCATED_LENGTH = "length"              # finish_reason=length（撞到 max_tokens）
TRUNCATED_READ_TIMEOUT = "read_timeout"  # 首字之後 read 逾時（伺服器沉默超過 60 秒）
TRUNCATED_NETWORK = "network"            # 首字之後斷線、串流沒收到結束訊號
TRUNCATED_TOTAL_TIMEOUT = "total_timeout"  # 首字之後撞到 LLM_HTTP_TOTAL_TIMEOUT（我們自己的時限）
TRUNCATED_CONTENT_FILTER = "content_filter"  # 這個以 partial 例外拋出，不走 meta


def _http_total_timeout() -> float:
    """`LLM_HTTP_TOTAL_TIMEOUT`（app/config.py）。呼叫時才讀：本模組在 import 期不碰 Settings。"""
    return get_settings().llm_http_total_timeout


def _reason_for(kind: str) -> str:
    """kind → 相容用的 `reason` 詞彙（見 LLMUnavailableError docstring）。"""
    if kind == llm_http.TIMEOUT:
        return UNAVAILABLE_TIMEOUT
    if kind == llm_http.EMPTY:
        return UNAVAILABLE_EMPTY
    return UNAVAILABLE_API_ERROR


def _truncated_reason(out: llm_http.ChatOutcome) -> str:
    if out.kind == llm_http.TRUNCATED:
        return TRUNCATED_LENGTH
    if out.kind == llm_http.TIMEOUT:  # 已吐字才逾時只可能是總時限（首字期限只管首字之前）
        return TRUNCATED_TOTAL_TIMEOUT
    if out.kind == llm_http.NETWORK:
        # detail 是 llm_http._transport_detail 組的 `<例外類名>: …`（我們自己的格式，不是供應商文字）
        return TRUNCATED_READ_TIMEOUT if out.detail.startswith("ReadTimeout") else TRUNCATED_NETWORK
    return out.kind or llm_http.OTHER


def _user_id(task: str) -> str:
    """固定字串、不帶個資（官方用於內容安全、KV cache 與排程的隔離）。"""
    return f"{'eval' if task.startswith('eval') else 'web'}-{task}"


async def _stream_http(
    prompt: str,
    *,
    model: str,
    system: str | None,
    timeout: float,
    retries: int,
    meta: dict | None,
    max_tokens: int | None,
    task: str,
) -> AsyncIterator[str]:
    """HTTP 路徑的外層迴圈：重試策略、partial 與截斷訊號（逐次呼叫在 llm_http.astream_chat）。

    - `timeout` 是**首字期限**（每次嘗試各自計時）：llm_http 在第一個 content 字到達前的每個
      await 各自包 `asyncio.timeout_at`、絕不跨越 yield，所以經 `_with_heartbeat` 驅動（每次
      `__anext__` 開新 Task）也準時生效。首字前伺服器 60 秒完全沉默（httpx read 逾時）同樣歸
      timeout、不重試。第一個字之後正常靠 `max_tokens` 與 httpx read=60 收尾，另有寬鬆的牆鐘
      總時限 `LLM_HTTP_TOTAL_TIMEOUT`（每次嘗試各自從頭算；到期＝截斷，reason `total_timeout`）。
    - 只有 overloaded／network 且**還沒吐字**才重試；已吐字重試會讓畫面上出現兩份答案。
    - 還沒吐字就失敗 → 拋 `LLMUnavailableError(kind=…)`。
    - 已吐字後：內容審查 → 拋 `partial=True`（呼叫端保留已送出的文字並附註）；其他（`length`、
      read 逾時、斷線、總時限）→ 正常結束，`meta["truncated"]=True` 與 `meta["truncated_reason"]`。
    - 任何非預期例外一律包成 `LLMUnavailableError`；`CancelledError` 原樣上拋，並經
      `contextlib.aclosing` 關閉 httpx 回應。
    """
    tokens = max_tokens
    if tokens is None:
        logger.warning(
            "stream_completion task=%s model=%s 未給 max_tokens，改用 %d", task, model, _HTTP_FALLBACK_MAX_TOKENS,
        )
        tokens = _HTTP_FALLBACK_MAX_TOKENS
    total_timeout = _http_total_timeout()

    for attempt in range(retries + 1):
        streamed = False
        out: llm_http.ChatOutcome | None = None
        try:
            async with contextlib.aclosing(llm_http.astream_chat(
                model, prompt, max_tokens=tokens, first_token_timeout=timeout,
                system=system, task=task, user_id=_user_id(task), total_timeout=total_timeout,
            )) as agen:
                async for item in agen:
                    if isinstance(item, llm_http.ChatOutcome):
                        out = item
                        continue
                    streamed = True
                    yield item
        except Exception as exc:  # CancelledError／GeneratorExit 是 BaseException，不會進來
            logger.exception("stream_completion task=%s model=%s 非預期例外", task, model)
            raise LLMUnavailableError(
                f"{type(exc).__name__}: {exc}", reason=UNAVAILABLE_API_ERROR,
                kind=llm_http.OTHER, partial=streamed,
            ) from exc
        if out is None:  # astream_chat 的契約是恰好一個 outcome；防禦
            out = llm_http.ChatOutcome(kind=llm_http.OTHER, detail="串流未回報結局", streamed=streamed)

        if out.kind is None:
            if meta is not None:
                meta["truncated"] = False
            return
        if streamed:
            if out.kind == llm_http.CONTENT_FILTER:
                raise LLMUnavailableError(
                    llm_http.error_string(out.kind, out.detail), reason=UNAVAILABLE_API_ERROR,
                    kind=out.kind, partial=True,
                )
            reason = _truncated_reason(out)
            logger.warning(
                "stream_completion task=%s model=%s 吐字後中斷 truncated=%s detail=%s",
                task, model, reason, out.detail,
            )
            if meta is not None:
                meta["truncated"] = True
                meta["truncated_reason"] = reason
            return
        if out.kind in llm_http.TRANSIENT_KINDS and attempt < retries:
            if out.retry_after is not None:
                wait = min(out.retry_after, _HTTP_RETRY_AFTER_CAP)
            else:
                wait = 1.5 * (attempt + 1)
            await _retry_sleep(wait)
            continue
        raise LLMUnavailableError(
            llm_http.error_string(out.kind, out.detail), reason=_reason_for(out.kind), kind=out.kind,
        )


async def stream_completion(
    prompt: str,
    *,
    model: str = DEFAULT_MODEL,
    system: str | None = None,
    timeout: float = 120.0,
    allow_web: bool = False,
    retries: int = 2,
    meta: dict | None = None,
    max_tokens: int | None = None,
    task: str | None = None,
) -> AsyncIterator[str]:
    """串流呼叫 DeepSeek，逐段 yield 回答文字（語意見 `_stream_http`）。

    送出前就拋 `LLMUnavailableError(kind="config")`（一個字都不會 yield，呼叫端照「未吐字失敗」處理）：
    - `allow_web=True`：網搜沒有後端（claude CLI 已移除、DeepSeek 網搜延後到 P9）。先於 model 檢查，
      所以 `ASK_WEB_MODEL` 填什麼都一樣。時效題據此退回 M4 婉拒。
    - model 不在白名單（`llm_models.is_http_model`）：含 `claude-*`、CLI 別名、打錯字、空字串。

    `max_tokens`、`task`（keyword-only）：每個呼叫點都要給（tests/test_llm.py 靜態釘住）。`max_tokens`
    是請求的輸出上限（第二版計畫 §8 逐點訂）；`task` 進結構化 log 的 `llm_call task=` 與請求的 `user_id`。

    `timeout` 是**首字期限**，與驅動方式無關（經 `web.deps._with_heartbeat` 每次 `__anext__` 開新 Task
    也準時生效，見 `_stream_http`）；首字之後由 `max_tokens`、httpx read 逾時與 `LLM_HTTP_TOTAL_TIMEOUT`
    收尾。`retries`：只重試還沒吐字的 overloaded／network。

    meta（選填、呼叫端給的空 dict）：正常結束後寫入 `meta["truncated"]`（已吐的字照常送出，但後面被
    砍掉了），截斷時另寫 `meta["truncated_reason"]`。
    """
    if allow_web:
        raise LLMUnavailableError(
            f"網搜沒有可用的後端（task={task or '-'} model={model!r}）：claude CLI 已於 PR-M 移除，"
            "DeepSeek 網搜延後到 P9；ASK_ENABLE_WEB 應為 0",
            reason=UNAVAILABLE_API_ERROR, kind=llm_http.CONFIG,
        )
    if not is_http_model(model):
        raise LLMUnavailableError(
            f"model={model!r} 不在 DeepSeek 白名單（task={task or '-'}；claude CLI 已於 PR-M 移除，"
            "沒有其他 backend）：檢查該任務的模型旋鈕",
            reason=UNAVAILABLE_API_ERROR, kind=llm_http.CONFIG,
        )
    # aclosing：呼叫端在兩段文字之間 aclose 本產生器（`_with_heartbeat` 的用戶端中斷）時，
    # 內層產生器要當下收掉、關閉 httpx 回應；只靠 `async for` 的話要等 GC 才會關。
    async with contextlib.aclosing(_stream_http(
        prompt, model=model, system=system, timeout=timeout, retries=retries,
        meta=meta, max_tokens=max_tokens, task=task or "-",
    )) as chunks:
        async for chunk in chunks:
            yield chunk
