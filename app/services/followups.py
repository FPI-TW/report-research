"""追問建議：答完後由 Haiku 產 ≤3 條同語料範疇的財經追問（fail-open）。"""
from __future__ import annotations

import json
import logging
import os
import re

from app.services.llm import stream_completion
from app.services.locale import DEFAULT_LOCALE

logger = logging.getLogger(__name__)

FOLLOWUP_MODEL = os.getenv("ASK_FOLLOWUP_MODEL", "claude-haiku-4-5-20251001")
FOLLOWUP_TIMEOUT = float(os.getenv("ASK_FOLLOWUP_TIMEOUT", "15"))

_SYSTEM = (
    "你是券商研報問答助理。根據使用者的問題與你剛給的回答，"
    "提出恰好 3 條使用者可能接著想問、且能用券商研報回答的財經追問。"
    "每條為完整、具體、可獨立檢索的問題（避免代名詞）。"
    "只輸出一個 JSON 字串陣列，例如 [\"問題一\", \"問題二\", \"問題三\"]，不要其他文字。"
)

# M10 遺漏：英文模式下主答案、婉拒、總覽模板都已英文化，唯獨這三顆追問 chip 仍是中文，
# 而且會經 _update_followups 落 qa_log、一直跟著歷史重播。
#
# 用**整份英文變體**而非「中文底稿＋尾部附加覆寫」：後者在長篇生成上實測會機率性
# 失守（2026-07 深度研報逐節生成時 8 節中 1 節整節漂回中文；該功能已移除，教訓留著）。
_SYSTEM_EN = (
    "You are a broker-research Q&A assistant. Based on the user's question and the "
    "answer you just gave, propose exactly 3 finance follow-up questions the user is "
    "likely to ask next and that can be answered from broker research reports. "
    "Each must be a complete, specific, independently searchable question (avoid "
    "pronouns). Write them in English, but keep company names, tickers, and other "
    "proper nouns in their original language. "
    'Output only a JSON array of strings, e.g. ["Question one", "Question two", '
    '"Question three"], with no other text.'
)


def _system_for(locale: str) -> str:
    """非 en 一律中文（fail-open，對齊 app.services.locale 的慣例）。"""
    return _SYSTEM_EN if locale == "en" else _SYSTEM


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
    question: str,
    answer: str,
    *,
    model: str = FOLLOWUP_MODEL,
    timeout: float = FOLLOWUP_TIMEOUT,
    locale: str = DEFAULT_LOCALE,
) -> list[str]:
    """回 ≤3 條追問；任何失敗/逾時回 []（fail-open，不擋主答題）。輸出語言隨 locale。"""
    if locale == "en":
        prompt = (
            f"Question: {question}\n\nAnswer: {answer}\n\n"
            "Output the JSON array per the rules."
        )
    else:
        prompt = f"問題：{question}\n\n回答：{answer}\n\n請依規則輸出 JSON 陣列。"
    parts: list[str] = []
    try:
        async for chunk in stream_completion(
            prompt, model=model, system=_system_for(locale),
            allow_web=False, timeout=timeout,
        ):
            parts.append(chunk)
    except Exception:
        logger.info("generate_followups failed; returning []", exc_info=True)
        return []
    return _parse_array("".join(parts))[:3]
