"""追問建議：答完後由 Haiku 產 ≤3 條同語料範疇的財經追問（fail-open）。"""
from __future__ import annotations

import json
import logging
import os
import re

from app.services.llm import stream_completion

logger = logging.getLogger(__name__)

FOLLOWUP_MODEL = os.getenv("ASK_FOLLOWUP_MODEL", "claude-haiku-4-5-20251001")

_SYSTEM = (
    "你是券商研報問答助理。根據使用者的問題與你剛給的回答，"
    "提出恰好 3 條使用者可能接著想問、且能用券商研報回答的財經追問。"
    "每條為完整、具體、可獨立檢索的問題（避免代名詞）。"
    "只輸出一個 JSON 字串陣列，例如 [\"問題一\", \"問題二\", \"問題三\"]，不要其他文字。"
)


def _parse_array(text_out: str) -> list[str]:
    """從模型輸出擷取 JSON 字串陣列；偏好第一個 [...] 區段，健壯防雜訊。"""
    m = re.search(r"\[.*\]", text_out, re.DOTALL)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    return [s.strip() for s in data if isinstance(s, str) and s.strip()]


async def generate_followups(
    question: str, answer: str, *, model: str = FOLLOWUP_MODEL
) -> list[str]:
    """回 ≤3 條追問；任何失敗/逾時回 []（fail-open，不擋主答題）。"""
    prompt = f"問題：{question}\n\n回答：{answer}\n\n請依規則輸出 JSON 陣列。"
    parts: list[str] = []
    try:
        async for chunk in stream_completion(
            prompt, model=model, system=_SYSTEM, allow_web=False
        ):
            parts.append(chunk)
    except Exception:
        logger.info("generate_followups failed; returning []", exc_info=True)
        return []
    return _parse_array("".join(parts))[:3]
