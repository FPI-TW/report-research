# web/routers/brief.py
"""每日簡報 API（/api/brief/*）：三條路由都是純讀取、零 LLM。

簡報由 `scripts/generate_brief.py` 離線批次產生並落 `research.report_brief`
（對齊觀點雷達之於 report_signal、閱讀頁摘錄之於 report_takeaway 的分工）。

**空狀態不是錯誤**：批次還沒跑過、或今天還沒到產生時間時 `/latest` 回 200 加
`status="pending"`，不是 404——404 會被前端當成壞掉，而「還沒產生」是每天早上
都會出現的正常狀態。指定日期查不到才是真的 404。

服務函式走 web.deps（測試 patch web.deps.X 即涵蓋）。
"""
import logging
from datetime import date as date_cls
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.services.filename import source_display
from web import deps

logger = logging.getLogger(__name__)

router = APIRouter()

# 前端日期切換清單的長度上限。簡報一天一份，30 天足夠回顧且不必分頁。
MAX_AVAILABLE_DATES = 30


class BriefReportRef(BaseModel):
    """簡報的來源研報之一。file_hash 是閱讀頁網址鍵（report_id 重新 ingest 會換）。"""

    report_id: str
    file_hash: str
    file_name: str
    title: Optional[str] = None
    market: Optional[str] = None
    source: Optional[str] = None
    source_display: Optional[str] = None
    report_date: Optional[str] = None


class BriefPayload(BaseModel):
    brief_date: str
    window_start: str
    window_end: str
    markdown: str
    # report_count 是窗期內的**實際**篇數，可能大於 reports 的長度（prompt 有上限）。
    # 兩者不同時前端要說得出「另有 N 篇未列出」，不要靜默只顯示一部分。
    report_count: int
    signal_count: int
    reports: list[BriefReportRef]
    created_at: str


class BriefEnvelope(BaseModel):
    status: Literal["ready", "pending"]
    brief: Optional[BriefPayload] = None
    available_dates: list[str] = []


def _ref(report) -> BriefReportRef:
    return BriefReportRef(
        report_id=report.report_id,
        file_hash=report.file_hash,
        file_name=report.file_name,
        title=report.title,
        market=report.market,
        source=report.source,
        source_display=source_display(report.source) if report.source else None,
        report_date=report.report_date.isoformat() if report.report_date else None,
    )


async def _payload(session, row) -> BriefPayload:
    """把 BriefRow 補上來源研報後轉成回應。

    查不到的 report_id 會直接消失（`report_ids` 刻意無 FK，語料重建後舊 id 會失效）
    —— 簡報本文仍然有效，只是少幾個連結，故不視為錯誤。
    """
    reports = await deps.fetch_brief_reports(session, row.report_ids)
    return BriefPayload(
        brief_date=row.brief_date.isoformat(),
        window_start=row.window_start.isoformat(),
        window_end=row.window_end.isoformat(),
        markdown=row.markdown,
        report_count=row.report_count,
        signal_count=row.signal_count,
        reports=[_ref(r) for r in reports],
        created_at=row.created_at.isoformat(),
    )


# ── 以下為路由。新增輔助函式一律放在這一行之上 ──────────────────────
# （夾在裝飾器與 handler 之間會讓裝飾器套到輔助函式，端點對正常請求回 422；
#   2026-07-28 實際事故，直接呼叫函式物件的測試看不到。）


@router.get("/api/brief/latest", response_model=BriefEnvelope)
async def brief_latest():
    async with deps.SessionFactory() as session:
        row = await deps.fetch_latest_brief(session)
        dates = await deps.fetch_brief_dates(session, MAX_AVAILABLE_DATES)
        if row is None:
            return BriefEnvelope(status="pending", brief=None, available_dates=[])
        return BriefEnvelope(
            status="ready",
            brief=await _payload(session, row),
            available_dates=[d.isoformat() for d in dates],
        )


@router.get("/api/brief/dates")
async def brief_dates(limit: int = Query(MAX_AVAILABLE_DATES, ge=1, le=120)):
    async with deps.SessionFactory() as session:
        dates = await deps.fetch_brief_dates(session, limit)
    return {"dates": [d.isoformat() for d in dates]}


# 這條必須排在 /latest 與 /dates 之後：FastAPI 依註冊順序比對，先註冊參數路由會把
# 那兩個字面路徑一起吃掉（date 解析失敗 → 422，而不是走到該走的 handler）。
@router.get("/api/brief/{brief_date}", response_model=BriefEnvelope)
async def brief_by_date(brief_date: date_cls):
    async with deps.SessionFactory() as session:
        row = await deps.fetch_brief_by_date(session, brief_date)
        if row is None:
            raise HTTPException(status_code=404, detail="該日期沒有簡報")
        dates = await deps.fetch_brief_dates(session, MAX_AVAILABLE_DATES)
        return BriefEnvelope(
            status="ready",
            brief=await _payload(session, row),
            available_dates=[d.isoformat() for d in dates],
        )
