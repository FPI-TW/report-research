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
import re

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
    if v == "OUT":
        return False
    if v == "IN":
        return True
    tokens = re.findall(r"[A-Z]+", v)
    has_out = "OUT" in tokens
    has_in = "IN" in tokens
    # 寬鬆兜底：只有明確的獨立 OUT token、且沒有 IN token 才判離題
    if has_out and not has_in:
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


CONDENSE_MODEL = os.getenv("ASK_CONDENSE_MODEL", INTENT_MODEL)
CONDENSE_TIMEOUT = float(os.getenv("ASK_CONDENSE_TIMEOUT", "20"))

CONDENSE_SYSTEM_PROMPT = (
    "你是「廷豐研報」投資問答系統的前置處理器。根據『先前對話』，把使用者的"
    "『追問』改寫成一個語意完整、可獨立檢索的問題：補齊代名詞與省略的主語"
    "（例如把「它」「那檔」「上述」還原為具體公司／標的／主題）。同時判斷"
    "改寫後的問題是否屬於『可由投資研究報告回答的金融／市場／個股／總經／期貨提問』。\n"
    "嚴格只輸出兩行，不要任何其他文字或標點說明：\n"
    "QUERY: <改寫後可獨立檢索的完整問題>\n"
    "INTENT: IN 或 OUT"
)


def parse_condense(text: str) -> tuple[str | None, bool]:
    """解析改寫器輸出 → (standalone_query 或 None, in_domain)。

    取 `QUERY:` 行為改寫後查詢（空則 None，由呼叫端退回原問題）；
    `INTENT:` 行交 parse_intent 判定（缺此行 → fail-open True）。
    """
    query: str | None = None
    in_domain = True
    for line in text.splitlines():
        s = line.strip()
        upper = s.upper()
        if upper.startswith("QUERY:"):
            query = s[len("QUERY:"):].strip() or None
        elif upper.startswith("INTENT:"):
            in_domain = parse_intent(s[len("INTENT:"):])
    return query, in_domain


async def condense_and_classify(
    history_text: str,
    question: str,
    *,
    model: str = CONDENSE_MODEL,
    timeout: float = CONDENSE_TIMEOUT,
) -> tuple[str, bool]:
    """一次 Haiku 呼叫：把追問改寫成獨立查詢並判定意圖 → (standalone_query, in_domain)。

    任何錯誤／逾時／空回應／解析不到查詢 → fail-open，回 (原始 question, True)。
    """
    prompt = f"先前對話：\n{history_text}\n\n追問：{question}"
    try:
        parts: list[str] = []
        async for chunk in stream_completion(
            prompt, model=model, system=CONDENSE_SYSTEM_PROMPT, timeout=timeout
        ):
            parts.append(chunk)
        query, in_domain = parse_condense("".join(parts))
        return (query or question, in_domain)
    except Exception:
        return (question, True)
