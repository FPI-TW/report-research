"""研報閱讀頁 HTTP 回應 pydantic（API 契約；前端 zod 需逐字鏡像）。

**狀態詞彙刻意與 DB 分離**：DB 的 extraction_status（pending/valid/partial/rejected）
記錄「批次做了什麼」；此處的 *_state 記錄「讀者拿到什麼」。兩者不可混用。

版面契約（設計稿 report/reading-c.html 拍板）：
- takeaways 空 → 前端整區不渲染
- signals_state == "none" → 前端**整區不進 DOM**（不是空框、不是骨架）。
  全語料僅 0.68% 有訊號，這是常態不是錯誤。
- text_state == "missing" → 只給 PDF，不是錯誤
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel

# 讀者視角的狀態（非 DB 狀態）
TextState = Literal["ok", "missing"]
SignalsState = Literal["available", "none"]
AnchorMethod = Literal["exact", "normalized", "prefix"]
RatingNorm = Literal["buy", "overweight", "neutral", "underweight", "sell", "unknown"]


class Takeaway(BaseModel):
    """一條重點摘錄。quote_start 為 None＝不可跳，前端顯示條目但不給跳轉。

    後端收回 offset 的三種情形（前端只需看 quote_start 是否為 None，不必自行判斷）：
    錨不到、驗章不過（正典文字已漂移）、落在 /text 的截斷範圍之外。
    """

    ordinal: int
    claim: str
    quote: Optional[str] = None
    quote_start: Optional[int] = None
    quote_end: Optional[int] = None
    anchor_method: Optional[AnchorMethod] = None


class ThesisDim(BaseModel):
    key: Literal["outlook", "catalyst", "risk", "valuation"]
    stance: Optional[str] = None
    summary: Optional[str] = None
    evidence: Optional[str] = None


class EpsEstimate(BaseModel):
    fiscal_year: Optional[str] = None
    period: Optional[str] = None
    currency: Optional[str] = None
    unit: Optional[str] = None
    value: Optional[float] = None


class Signal(BaseModel):
    """一份研報對一個標的的結構化訊號。全語料僅 0.68% 的報告有。"""

    instrument_code: str
    market: str
    broker: Optional[str] = None
    broker_display: Optional[str] = None
    rating_raw: Optional[str] = None
    rating_normalized: RatingNorm = "unknown"
    target_price: Optional[float] = None
    target_currency: Optional[str] = None
    target_horizon: Optional[str] = None
    eps_estimates: list[EpsEstimate] = []
    thesis: list[ThesisDim] = []


class ReadingDoc(BaseModel):
    """閱讀頁骨架。**不含全文** —— PDF 是預設檢視，用不到；全文另走 /text。"""

    report_id: str
    file_hash: str
    file_name: str
    # 報告內部標題（頁首顯示用）。None＝尚未產生，前端回退 file_name——批次是漸進補的，
    # 任何時點都有一部分報告沒有標題，這是常態不是錯誤。
    title: Optional[str] = None
    market: Optional[str] = None
    source: Optional[str] = None
    source_display: Optional[str] = None
    report_date: Optional[str] = None
    report_type: Optional[str] = None
    summary: Optional[str] = None
    instrument_types: list[str] = []
    stock_targets: list[str] = []
    futures_targets: list[str] = []
    has_file: bool = False
    is_pdf: bool = False

    text_state: TextState = "missing"
    text_chars: int = 0
    # sha256(clean_extracted(full_text))。前端只在此值與 /text 回傳的相符時才啟用
    # 引文跳轉；不符＝摘錄照常顯示但不可跳（見模組 docstring 的不變量）。
    text_sha256: Optional[str] = None

    takeaways: list[Takeaway] = []
    signals_state: SignalsState = "none"
    signals: list[Signal] = []


class ReadingText(BaseModel):
    """正典文字＝clean_extracted(full_text)。所有 offset 都以此字串為準。

    chunk_start/chunk_end：僅當請求帶 `?chunk=` 且該 chunk 錨定成功時有值 —— 供前端
    標出「你從檢索命中點進來的那一段」。錨不到、chunk 不存在、或 offset 落在截斷範圍
    之外，一律為 None：**前端不高亮，但頁面照常**（不是錯誤，不回 4xx）。

    與 takeaway 的 quote_start/quote_end 不同，這兩個值不需驗章：它們與同一回應的
    text 出自同一份正典文字，必然同源。
    """

    file_hash: str
    text: str
    text_sha256: str
    text_chars: int
    truncated: bool = False
    chunk_start: Optional[int] = None
    chunk_end: Optional[int] = None


class SimilarReport(BaseModel):
    file_hash: str
    file_name: str
    title: Optional[str] = None  # 同 ReadingDoc.title
    market: Optional[str] = None
    source: Optional[str] = None
    source_display: Optional[str] = None
    report_date: Optional[str] = None
    summary: Optional[str] = None
    # 「9/12 段相符」的兩個數字（設計稿拍板的說法）
    matched_probes: int
    total_probes: int


class SimilarResponse(BaseModel):
    file_hash: str
    items: list[SimilarReport] = []
