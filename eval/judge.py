"""評審 LLM 原語：DeepSeek 非串流 JSON 模式（`llm_http.complete_json`：stream=false、json_object、t=0、thinking 關）。

- 重試只在 adapter 那一層、受本階段請求預算限制（`judge_schema.HTTP_STAGE_MAX_REQUESTS`），`judge_json`
  自己不重試（PR-M 前 CLI judge 的逾時重試迴圈隨 CLI 移除）。
- 帳號層級錯誤（401／402／404）與 model 不在白名單拋 `JudgeAccountError`：每一題都會踩到，run_ragas
  整批中止（rc=2），不記成 N 題 judge_errors。非白名單 model 不送出（PR-M 起沒有 CLI 可以改走）。
- 回應不是合法 JSON 拋 `JudgeError`；其他失敗拋 `LLMUnavailableError`（帶 kind），交由 run_ragas 逐題
  fail-open。
"""

from __future__ import annotations

import logging
import os

from app.services import llm_http
from app.services.judge_schema import http_attempts_allowed, record_http_requests
from app.services.llm import (
    UNAVAILABLE_API_ERROR,
    UNAVAILABLE_EMPTY,
    UNAVAILABLE_TIMEOUT,
    LLMUnavailableError,
)
from app.services.llm_models import TASK_EVAL_JUDGE, is_http_model, resolve_model

logger = logging.getLogger(__name__)

# 旋鈕的 os.getenv 留在本檔；空字串視同未設，未設時查預設表：自 PR-26/27 起是 deepseek-flash
# （新量尺系譜，見 app/services/llm_models.py 的 `judge_lineage`）。
DEFAULT_JUDGE_MODEL = resolve_model(TASK_EVAL_JUDGE, override=os.getenv("EVAL_JUDGE_MODEL"))

# 逾時 60s 曾讓整份評測不可用（CLI judge 時代：8 題裡 3-5 題失敗）。180s 是那時定的；DeepSeek 下
# 它是 `complete_json` 涵蓋重試的總期限，照舊寬鬆。
#
# 2026-07-29 實測（CLI judge）（decompose 長答案，3 筆）：
#     3999 字  timeout=60 → OK 32.9s        timeout=180 → OK 22.3s
#     3658 字  timeout=60 → 逾時失敗 60.0s   timeout=180 → OK 25.3s
#     3475 字  timeout=60 → 截斷 JSON 60.0s  timeout=180 → OK 23.0s
# 正常耗時只有 22-33 秒，60s 卡住的是「偶爾卡一下」的那些——上限訂得太貼近中位數。
# 同一種形狀的 bug 見 PR #72（rerank 30s 全逾時 → per-path 60/180s）。
DEFAULT_JUDGE_TIMEOUT = float(os.getenv("EVAL_JUDGE_TIMEOUT", "180"))
# 輸出上限的預設：取四個階段裡最大的拆解（8192）。逐階段的值
# 由呼叫端傳入（run_ragas 依系統提示查 `eval.ragas_metrics.JUDGE_MAX_TOKENS_BY_SYSTEM`）。
JUDGE_MAX_TOKENS = 8192
# DeepSeek 的 `user_id`（內容安全與排程隔離）；生產忠實度抽查是 web-faithfulness。
JUDGE_USER_ID = "eval-judge"

class JudgeError(Exception):
    """評審回應不是合法 JSON（`llm_http` 的 invalid_json）。"""


class JudgeAccountError(Exception):
    """DeepSeek 帳號層級錯誤（401 金鑰、402 餘額、404 模型／端點）：每一次 judge 呼叫都會失敗。

    刻意不繼承 JudgeError／LLMUnavailableError：run_ragas 只把那兩類（加 JudgeSchemaError）當成
    「該指標記 None」，這一類要讓整批中止，而不是記成 8 題 judge_errors 後照樣寫出一份結果檔。
    """


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
    if not is_http_model(model):
        raise JudgeAccountError(
            f"judge model={model!r} 不在 DeepSeek 白名單（claude CLI 已於 PR-M 移除）：檢查 EVAL_JUDGE_MODEL"
        )
    return await _judge_http(
        prompt, system=system, model=model, timeout=timeout, max_tokens=max_tokens, meta=meta,
    )


async def judge_json(
    prompt: str,
    *,
    system: str,
    model: str = DEFAULT_JUDGE_MODEL,
    timeout: float = DEFAULT_JUDGE_TIMEOUT,
    max_tokens: int = JUDGE_MAX_TOKENS,
    meta: dict | None = None,
) -> dict | list:
    """呼叫 judge 取 JSON（失敗依模組 docstring 拋三種例外之一）。

    **這裡不重試**：重試只在 `llm_http.complete_json` 那一層，受本階段請求預算限制，再包一層就是相乘
    （模組 docstring）。`meta` 給定時填入回應的 `model_resp`、`system_fingerprint`、`requests` 與
    `usage`（run_ragas 寫進結果檔的 config）。
    """
    return await _judge_once(
        prompt, system=system, model=model, timeout=timeout, max_tokens=max_tokens, meta=meta,
    )
