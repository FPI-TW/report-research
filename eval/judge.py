"""評審 LLM 原語：drain claude CLI 串流 + robust JSON 解析。

沿用 intent.py 的 drain 慣例（parts=[] async for ... "".join(parts)）。judge 不上網
（allow_web=False）。解析容忍 ```json 圍欄、前後散文，取第一個平衡的 {..}/[..]；
空回應或解析失敗一律 raise JudgeError，交由 run_ragas 逐題 fail-open。
"""

from __future__ import annotations

import json
import os
import re

from app.services.llm import stream_completion

DEFAULT_JUDGE_MODEL = os.getenv("EVAL_JUDGE_MODEL", "claude-haiku-4-5")

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
    starts = [i for i in (s.find("{"), s.find("[")) if i != -1]
    if not starts:
        raise JudgeError(f"no JSON found in judge output: {raw[:120]!r}")
    start = min(starts)
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


async def judge_json(
    prompt: str,
    *,
    system: str,
    model: str = DEFAULT_JUDGE_MODEL,
    timeout: float = 60.0,
) -> dict | list:
    """drain stream_completion 取全文 → robust 解析為 JSON。空/畸形 → JudgeError。"""
    parts: list[str] = []
    async for chunk in stream_completion(
        prompt, model=model, system=system, timeout=timeout, allow_web=False
    ):
        parts.append(chunk)
    text = "".join(parts)
    if not text.strip():
        raise JudgeError("empty judge response")
    return _loads_robust(text)
