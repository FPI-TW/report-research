"""研報閱讀頁 HTTP 回應 pydantic（API 契約；前端 zod 需逐字鏡像）。

**狀態詞彙刻意與 DB 分離**：DB 的 extraction_status（pending/valid/partial/rejected）
記錄「批次做了什麼」；此處的 *_state 記錄「讀者拿到什麼」。兩者不可混用。

版面契約（設計稿 report/reading-c.html 拍板）：
- takeaways 空 → 前端整區不渲染
- signals_state == "none" → 前端**整區不進 DOM**（不是空框、不是骨架）。
  全語料僅 0.68% 有訊號，這是常態不是錯誤。
- text_state == "missing" → 前端不取 /text，文件區由 has_file/is_pdf 決定呈現，不是錯誤

**閱讀頁已無「文字檢視」這個使用者可選項**（2026-08-03 移除 [原文][文字] 分段控制）：
文件區一律內嵌 PDF，只有 has_file and is_pdf 為否時才落到正典文字後備；三者皆否時
給可下載的終態。本檔的欄位一個都沒動，但下面幾處敘述已據此改寫——
**契約沒變，變的是前端消費哪幾個欄位**。
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
    """一條重點摘錄。條目與逐字引文一律顯示；quote_start 為 None＝錨點無效。

    後端收回 offset 的三種情形（消費端只需看 quote_start 是否為 None，不必自行判斷）：
    錨不到、驗章不過（正典文字已漂移）、落在 /text 的截斷範圍之外。

    **前端目前不消費這三個 offset 欄位**（引文跳轉已於 2026-08-03 隨文字檢視移除），
    但欄位與收回規則刻意保留：批次照樣在寫，日後要恢復跳轉不必重跑 674 篇 Sonnet。
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
    """閱讀頁骨架。**不含全文** —— 絕大多數研報直接內嵌 PDF，用不到；全文另走 /text。"""

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
    # sha256(clean_extracted(full_text))。「摘錄與全文是否同源」的唯一驗章依據：
    # 與 /text 回傳的不符＝正典文字已漂移，後端據此收回該篇所有錨點。
    # 前端目前不消費此值（引文跳轉已移除），保留是為了可逆性與 db_audit 的同源稽核。
    text_sha256: Optional[str] = None

    takeaways: list[Takeaway] = []
    signals_state: SignalsState = "none"
    signals: list[Signal] = []


class ReadingText(BaseModel):
    """正典文字＝clean_extracted(full_text)。所有 offset 都以此字串為準。

    chunk_start/chunk_end：僅當請求帶 `?chunk=` 且該 chunk 錨定成功時有值。錨不到、
    chunk 不存在、或 offset 落在截斷範圍之外，一律為 None：**沒有命中位置不是錯誤**
    （不回 4xx）。**SPA 已不再帶這個參數**（命中定位隨文字檢視於 2026-08-03 移除），
    端點側刻意保留。

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
