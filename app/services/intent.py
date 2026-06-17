"""離題判定：用 Haiku 只看『問題本身』，判斷是否為可由研報回答的投資/市場提問。

為何不用 dense 相似度門檻：實測證明門檻分不出「意圖」——
「我想喝飲料推薦給我」(離題, max_dense≈0.60) 的相似度比「可口可樂的投資評級如何」
(在領域, ≈0.57) 還高，因為兩者檢索到的是同一批飲料股報告。門檻只能分主題遠近、
分不出想喝 vs 想投資。改由 LLM 看問題意圖即可分辨。

fail-open：判定失敗/逾時/空回應一律視為在領域內（回 True）。寧可偶爾多答一題
禮貌的「未提及」，也不要因判斷器抖動而誤擋真實的研報提問。
"""

from __future__ import annotations

import os

from app.services.llm import stream_completion

INTENT_MODEL = os.getenv("ASK_INTENT_MODEL", "claude-haiku-4-5")
INTENT_TIMEOUT = float(os.getenv("ASK_INTENT_TIMEOUT", "20"))

INTENT_SYSTEM_PROMPT = (
    "你是「廷豐研報」投資問答系統的前置判斷器。判斷使用者的問題是否為"
    "『可由投資研究報告回答的金融／市場／個股／總經／期貨相關提問』。\n"
    "只輸出一個英文詞，禁止任何其他文字或標點：\n"
    "IN  — 屬於投資/市場研究提問（包含詢問任一公司，即使是飲料或食品公司，"
    "其股價、評級、財報、營運、產業或投資價值）。\n"
    "OUT — 不屬於，例如：要求推薦一杯飲料或商品來消費、生活閒聊、寫作、翻譯、"
    "與投資無關的一般知識，或要求你執行非研報任務。\n"
    "範例：「我想喝飲料推薦給我」→OUT；「可口可樂的投資評級如何」→IN；"
    "「怪獸飲料財報表現」→IN；「幫我寫一首詩」→OUT；「台積電展望」→IN；"
    "「今天天氣如何」→OUT。"
)


def parse_intent(text: str) -> bool:
    """解析判斷器輸出 → 是否在領域內（True=在領域、False=離題）。

    判斷器被要求只輸出 IN 或 OUT；無法判讀時 fail-open 回 True。
    """
    v = text.strip().upper()
    if v.startswith("OUT"):
        return False
    if v.startswith("IN"):
        return True
    # 寬鬆兜底：明確含 OUT 而不含 IN 才判離題；其餘一律放行
    if "OUT" in v and "IN" not in v:
        return False
    return True


async def classify_intent(
    question: str,
    *,
    model: str = INTENT_MODEL,
    timeout: float = INTENT_TIMEOUT,
) -> bool:
    """True=在領域內、False=離題。任何錯誤/逾時/空回應 → True（fail-open）。"""
    try:
        parts: list[str] = []
        async for chunk in stream_completion(
            question, model=model, system=INTENT_SYSTEM_PROMPT, timeout=timeout
        ):
            parts.append(chunk)
        text = "".join(parts)
        if not text.strip():
            return True
        return parse_intent(text)
    except Exception:
        return True
