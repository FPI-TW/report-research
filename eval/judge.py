"""評審 LLM 原語：依 model 分派——白名單走 DeepSeek 非串流 JSON 模式，其餘 drain claude CLI 串流。

- HTTP（DeepSeek，遷移 PR-18）：`llm_http.complete_json`（stream=false、json_object、t=0、thinking 關）。
  重試只在 adapter 那一層、受本階段請求預算限制（`judge_schema.HTTP_STAGE_MAX_REQUESTS`），
  `judge_json` 自己的重試迴圈在這條路徑不跑。帳號層級錯誤（401／402／404）拋 `JudgeAccountError`：
  每一題都會踩到，run_ragas 整批中止（rc=2），不記成 N 題 judge_errors。
- CLI：沿用 intent.py 的 drain 慣例（parts=[] async for ... "".join(parts)）。judge 不上網
  （allow_web=False）。解析容忍 ```json 圍欄、前後散文，取第一個平衡的 {..}/[..]；
  空回應或解析失敗一律 raise JudgeError，交由 run_ragas 逐題 fail-open。
"""

from __future__ import annotations

import json
import logging
import os
import re

from app.services import llm_http
from app.services.judge_schema import http_attempts_allowed, record_http_requests
from app.services.llm import (
    KIND_OTHER,
    UNAVAILABLE_API_ERROR,
    UNAVAILABLE_EMPTY,
    UNAVAILABLE_TIMEOUT,
    LLMUnavailableError,
    stream_completion,
)
from app.services.llm_models import TASK_EVAL_JUDGE, is_http_model, resolve_model

logger = logging.getLogger(__name__)

# 旋鈕的 os.getenv 留在本檔；空字串視同未設，未設時查 LLM_PROVIDER 的預設表：deepseek 表自 PR-26/27
# 起是 deepseek-flash（新量尺系譜，見 app/services/llm_models.py 的 `judge_lineage`），claude_cli 表仍是 haiku。
DEFAULT_JUDGE_MODEL = resolve_model(TASK_EVAL_JUDGE, override=os.getenv("EVAL_JUDGE_MODEL"))

# 逾時 60s 曾讓整份評測不可用：8 題裡 3-5 題失敗，而且**兩種錯誤其實同源**——
# stream_completion 逾時後「已吐字就 fail-open、沒吐字就 raise」，於是同一個逾時
# 依運氣呈現為 LLMUnavailableError 或「截斷的 JSON」（JudgeError: unbalanced）。
#
# 2026-07-29 實測（decompose 長答案，3 筆）：
#     3999 字  timeout=60 → OK 32.9s        timeout=180 → OK 22.3s
#     3658 字  timeout=60 → 逾時失敗 60.0s   timeout=180 → OK 25.3s
#     3475 字  timeout=60 → 截斷 JSON 60.0s  timeout=180 → OK 23.0s
# 正常耗時只有 22-33 秒，60s 卡住的是「偶爾卡一下」的那些——上限訂得太貼近中位數。
# 同一種形狀的 bug 見 PR #72（rerank 30s 全逾時 → per-path 60/180s）。
DEFAULT_JUDGE_TIMEOUT = float(os.getenv("EVAL_JUDGE_TIMEOUT", "180"))
DEFAULT_JUDGE_RETRIES = int(os.getenv("EVAL_JUDGE_RETRIES", "1"))
# 輸出上限的預設（只作用在 HTTP 路徑，CLI 忽略）：取四個階段裡最大的拆解（8192）。逐階段的值
# 由呼叫端傳入（run_ragas 依系統提示查 `eval.ragas_metrics.JUDGE_MAX_TOKENS_BY_SYSTEM`）。
JUDGE_MAX_TOKENS = 8192
# DeepSeek 的 `user_id`（內容安全與排程隔離）；生產忠實度抽查是 web-faithfulness。
JUDGE_USER_ID = "eval-judge"

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


class JudgeError(Exception):
    """評審回應為空或無法解析為 JSON。"""


class JudgeAccountError(Exception):
    """DeepSeek 帳號層級錯誤（401 金鑰、402 餘額、404 模型／端點）：每一次 judge 呼叫都會失敗。

    刻意不繼承 JudgeError／LLMUnavailableError：run_ragas 只把那兩類（加 JudgeSchemaError）當成
    「該指標記 None」，這一類要讓整批中止，而不是記成 8 題 judge_errors 後照樣寫出一份結果檔。
    """


# 值得重試的 LLM 失敗：再打一次有機會過的那幾種。依 LLMUnavailableError 的既有欄位判斷——
# - kind：HTTP 路徑的細分類；CLI 路徑一律 other（未分類）。overloaded／network 是暫時性；
#   timeout（HTTP 首字期限）與 CLI 的逾時同源，是這個重試存在的理由（見 judge_json docstring）。
# - reason：CLI 時代的三類，兩條路徑都填。逾時與 API 快速回錯（529 等）算暫時性。
# - empty 依路徑分兩種，形狀互不重疊（`llm._reason_for` 只把 HTTP 的 kind=empty 對到 reason=empty，
#   HTTP 的 kind=other 一律是 reason=api_error）：
#   - CLI：`kind=other, reason=empty`＝子程序結束卻一個字都沒吐（異常退出、被殺），與逾時一樣是
#     行程層的偶發故障，**重試**——也與 `JudgeError("empty judge response")`（吐了空白）一致。
#   - HTTP：`kind=empty, reason=empty`＝API 正常結束（finish_reason=stop）卻沒有 content。同一份
#     prompt 重打多半一樣，而且每次都計費，**不重試**。
# 帳號層級（quota／auth／config）、內容審查、單篇輸入錯誤（bad_request）一律不重試：結果不會變，
# 重打只是再付一次錢（DeepSeek 按量計費）。reason 為 None＝外部直接建構、無從判斷，不重試。
_RETRYABLE_KINDS = frozenset({"overloaded", "network", "timeout", "other"})
_RETRYABLE_REASONS = frozenset({UNAVAILABLE_TIMEOUT, UNAVAILABLE_API_ERROR})


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, JudgeError):  # 空回應或截斷 JSON：與逾時同源（見 judge_json docstring）
        return True
    if not isinstance(exc, LLMUnavailableError):
        return False
    if exc.kind == KIND_OTHER and exc.reason == UNAVAILABLE_EMPTY:  # CLI 無輸出退出（見上）
        return True
    return exc.kind in _RETRYABLE_KINDS and exc.reason in _RETRYABLE_REASONS


def _loads_robust(raw: str) -> dict | list:
    """從評審輸出解析 JSON：先去 ```json 圍欄、直接 loads；失敗則取第一個平衡括號子串。"""
    s = raw.strip()
    m = _FENCE_RE.search(s)
    if m:
        s = m.group(1).strip()
    try:
        return json.loads(s)
    except (ValueError, TypeError):
        pass
    brace = s.find("{")
    if brace != -1:
        start = brace
    else:
        start = s.find("[")
        if start == -1:
            raise JudgeError(f"no JSON found in judge output: {raw[:120]!r}")
    open_ch = s[start]
    close_ch = "}" if open_ch == "{" else "]"
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(s)):
        if in_str:
            if esc:
                esc = False
            elif s[i] == "\\":
                esc = True
            elif s[i] == '"':
                in_str = False
            continue
        if s[i] == '"':
            in_str = True
        elif s[i] == open_ch:
            depth += 1
        elif s[i] == close_ch:
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(s[start : i + 1])
                except (ValueError, TypeError) as e:
                    raise JudgeError(f"malformed JSON: {e}") from e
    raise JudgeError(f"unbalanced JSON in judge output: {raw[:120]!r}")


_HTTP_REASON = {llm_http.TIMEOUT: UNAVAILABLE_TIMEOUT, llm_http.EMPTY: UNAVAILABLE_EMPTY}


async def _judge_http(
    prompt: str, *, system: str, model: str, timeout: float, max_tokens: int, meta: dict | None,
):
    """DeepSeek judge 的一次呼叫：成功回解析後的 JSON；失敗依 kind 拋三種例外之一（見模組 docstring）。"""
    res = await llm_http.complete_json(
        model, prompt, system=system, max_tokens=max_tokens, timeout=timeout, task="eval_judge",
        user_id=JUDGE_USER_ID, max_attempts=max(1, http_attempts_allowed(llm_http.JSON_MAX_ATTEMPTS)),
    )
    record_http_requests(res.attempts)
    if meta is not None:
        meta["requests"] = meta.get("requests", 0) + res.attempts
        meta["model_resp"] = res.outcome.model_resp
        meta["system_fingerprint"] = res.outcome.system_fingerprint
        usage = meta.setdefault("usage", {})
        for k, v in res.usage.items():
            usage[k] = usage.get(k, 0) + v
    if res.kind is None:
        return res.data
    if res.kind in llm_http.ACCOUNT_KINDS:
        raise JudgeAccountError(res.error)
    if res.kind == llm_http.INVALID_JSON:
        raise JudgeError(res.error)
    raise LLMUnavailableError(res.error, reason=_HTTP_REASON.get(res.kind, UNAVAILABLE_API_ERROR), kind=res.kind)


async def _judge_once(
    prompt: str, *, system: str, model: str, timeout: float, max_tokens: int = JUDGE_MAX_TOKENS,
    meta: dict | None = None,
):
    if is_http_model(model):
        return await _judge_http(
            prompt, system=system, model=model, timeout=timeout, max_tokens=max_tokens, meta=meta,
        )
    parts: list[str] = []
    async for chunk in stream_completion(
        prompt, model=model, system=system, timeout=timeout, allow_web=False,
        # CLI 忽略 max_tokens；逐階段上限只作用在 HTTP 路徑（_judge_http）。維持常數：
        # tests/test_llm.py 的 CallSiteMaxTokensValuesTests 以模組命名空間求值它。
        max_tokens=JUDGE_MAX_TOKENS, task="eval_judge",
    ):
        parts.append(chunk)
    text = "".join(parts)
    if not text.strip():
        raise JudgeError("empty judge response")
    return _loads_robust(text)


async def judge_json(
    prompt: str,
    *,
    system: str,
    model: str = DEFAULT_JUDGE_MODEL,
    timeout: float = DEFAULT_JUDGE_TIMEOUT,
    retries: int = DEFAULT_JUDGE_RETRIES,
    max_tokens: int = JUDGE_MAX_TOKENS,
    meta: dict | None = None,
) -> dict | list:
    """呼叫 judge 取 JSON。CLI：drain stream_completion 取全文 → robust 解析；空/畸形 → JudgeError。

    **HTTP 路徑不在這裡重試**（`retries` 忽略）：重試只在 `llm_http.complete_json` 那一層，受本階段
    請求預算限制，這裡再包一層就是相乘（模組 docstring）。`meta` 給定時填入 HTTP 回應的
    `model_resp`、`system_fingerprint`、`requests` 與 `usage`（run_ragas 寫進結果檔的 config）。

    **逾時是暫時性的，所以要重試**：evaluation 的每一題只要有一次 judge 呼叫失敗，
    整題就記成 error 而被排除在指標之外——n=8 的題集掉 3-5 題，剩下的平均值毫無意義
    （2026-07-29 就是這樣連續兩次跑出不可用的 baseline）。重試次數有界，仍失敗照樣拋，
    不會把真正的故障吞掉。只重試暫時性失敗（`_is_retryable`）：餘額不足、金鑰錯、內容審查
    重打結果不會變，只是再付一次錢。
    """
    if is_http_model(model):
        retries = 0
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            return await _judge_once(
                prompt, system=system, model=model, timeout=timeout, max_tokens=max_tokens, meta=meta,
            )
        except (JudgeError, LLMUnavailableError) as e:
            last = e
            if not _is_retryable(e):
                raise
            if attempt < retries:
                logger.warning(
                    "judge 第 %d 次失敗（%s），重試：%s",
                    attempt + 1, type(e).__name__, str(e)[:100],
                )
    raise last  # type: ignore[misc]
