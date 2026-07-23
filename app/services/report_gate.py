"""判斷一題問答是否值得『出一份深度研報』，並給研報標題草稿。純規則、零 LLM。

在 answer_question 答案完成後計算（已知實際引用篇數與答案文字），結果隨 done 事件
回前端決定是否顯示『要不要出研報』建議卡。保守傾向：寧可少問，不要每題都問。
"""

from __future__ import annotations

from app.config import get_settings

_S = get_settings()
# 引用篇數門檻：實際引用少於此數，視為素材不足，不建議出研報。
REPORT_MIN_CITED = _S.report_min_cited

# 分析意圖關鍵詞：帶這些字代表使用者要的是分析/整理，而非單一即時事實。
_ANALYSIS_HINTS = (
    "分析", "比較", "展望", "趨勢", "影響", "前景", "評估", "總結",
    "整理", "報告", "深入", "全面", "綜合", "概況", "回顧", "預測", "策略",
)
# 純即時事實/報價題：即使引用足夠也不值得出研報。
_TRIVIAL_HINTS = ("股價", "報價", "收盤", "開盤", "幾元", "多少錢")

# 答案夠長也視為有分析深度（無顯式關鍵詞時的後備門檻）。
_LONG_ANSWER_CHARS = _S.report_long_answer_chars


def suggested_title(question: str, locale: str = "zh-Hant") -> str:
    """由問題組出研報標題草稿（輸出隨 locale；非 en 一律中文）。"""
    q = (question or "").strip().rstrip("?？。.!！").strip()
    if locale == "en":
        return f"{q} — Deep Research Report" if q else "Research Report"
    if not q:
        return "研報"
    return f"{q} 深度研報"


def should_offer_report(
    question: str, cited: list, answer: str
) -> tuple[bool, str | None]:
    """回 (offer, suggested_title)。cited 為實際引用的報告 id 清單。"""
    q = question or ""
    ans = answer or ""
    if len(cited) < REPORT_MIN_CITED:
        return (False, None)
    if any(h in q for h in _TRIVIAL_HINTS):
        return (False, None)
    has_intent = any(h in q for h in _ANALYSIS_HINTS) or len(ans) >= _LONG_ANSWER_CHARS
    if not has_intent:
        return (False, None)
    return (True, suggested_title(question))
