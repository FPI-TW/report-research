"""評審 LLM 原語：drain claude CLI 串流 + robust JSON 解析。

沿用 intent.py 的 drain 慣例（parts=[] async for ... "".join(parts)）。judge 不上網
（allow_web=False）。解析容忍 ```json 圍欄、前後散文，取第一個平衡的 {..}/[..]；
空回應或解析失敗一律 raise JudgeError，交由 run_ragas 逐題 fail-open。
"""

from __future__ import annotations

import json
import logging
import os
import re

from app.services.llm import LLMUnavailableError, stream_completion
from app.services.llm_models import TASK_EVAL_JUDGE, resolve_model

logger = logging.getLogger(__name__)

# 旋鈕的 os.getenv 留在本檔；空字串視同未設，未設時查 LLM_PROVIDER 的預設表。兩張表這列都是
# claude-haiku-4-5：換 judge＝換量尺，要等校準（PR-26），不隨 LLM_PROVIDER 一起換。
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

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


class JudgeError(Exception):
    """評審回應為空或無法解析為 JSON。"""


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


async def _judge_once(prompt: str, *, system: str, model: str, timeout: float):
    parts: list[str] = []
    async for chunk in stream_completion(
        prompt, model=model, system=system, timeout=timeout, allow_web=False
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
) -> dict | list:
    """drain stream_completion 取全文 → robust 解析為 JSON。空/畸形 → JudgeError。

    **逾時是暫時性的，所以要重試**：evaluation 的每一題只要有一次 judge 呼叫失敗，
    整題就記成 error 而被排除在指標之外——n=8 的題集掉 3-5 題，剩下的平均值毫無意義
    （2026-07-29 就是這樣連續兩次跑出不可用的 baseline）。重試次數有界，仍失敗照樣拋，
    不會把真正的故障吞掉。
    """
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            return await _judge_once(
                prompt, system=system, model=model, timeout=timeout
            )
        except (JudgeError, LLMUnavailableError) as e:
            last = e
            if attempt < retries:
                logger.warning(
                    "judge 第 %d 次失敗（%s），重試：%s",
                    attempt + 1, type(e).__name__, str(e)[:100],
                )
    raise last  # type: ignore[misc]
