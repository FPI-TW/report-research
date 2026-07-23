# web/routers/ask.py
"""RAG 問答 API：/api/ask（SSE 串流回答）與 /api/ask/stop（保存中斷的部分答案）。

從 web/server.py 拆出（第三步）。

_ASK_SEMAPHORE 是模組級狀態，限制同時提問數（每次提問 spawn 一個 claude CLI
子程序）。它定義在本模組、由本模組的 handler 使用；server.py 以
`from web.routers import ask` 單一路徑匯入，故全程只有一個 semaphore 實例——
若被兩條不同 import 路徑載入會分裂成兩個、併發上限失效。

answer_question、log_stopped_qa、_valid_uuid、_sse、_with_heartbeat 走 web.deps
（測試 patch web.deps.X 即涵蓋）。
"""
import asyncio
import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from web import deps

logger = logging.getLogger(__name__)

router = APIRouter()


ASK_QUESTION_MAX_CHARS = 2000


class AskRequest(BaseModel):
    question: str = Field(..., max_length=ASK_QUESTION_MAX_CHARS)
    conversation_id: str | None = None
    market: str | None = None
    instrument_type: str | None = None
    relates_stock: bool | None = None
    relates_futures: bool | None = None
    report_type: str | None = None
    k: int = 8
    regenerate_of: str | None = None
    edit_of: str | None = None
    request_id: str | None = None
    locale: str | None = None  # M10：輸出語言（zh-Hant/en）；未帶/未知 → 預設中文（fail-open）


# 每次提問會 spawn 一個 claude CLI 子程序（CPU-bound 機器），限制同時數避免區網多人同問雪崩。
_ASK_SEMAPHORE = asyncio.Semaphore(3)


@router.post("/api/ask")
async def ask(req: AskRequest):
    """RAG 問答：檢索 → 串流回答（帶 [n] 行內引用）。回 text/event-stream。

    事件序：sources（引用清單）→ 多筆 token（文字片段）→ done（實際引用的報告 id）。
    """
    question = (req.question or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="question 不可為空")
    mkt = req.market if req.market and req.market != "全部" else None
    instr = (
        req.instrument_type
        if req.instrument_type and req.instrument_type != "全部"
        else None
    )
    rtype = req.report_type if req.report_type and req.report_type != "全部" else None
    filters = dict(
        market=mkt,
        instrument_type=instr,
        relates_stock=req.relates_stock or None,
        relates_futures=req.relates_futures or None,
        report_type=rtype,
    )
    k = max(1, min(req.k, 20))
    if req.regenerate_of is not None and not deps._valid_uuid(req.regenerate_of):
        raise HTTPException(status_code=400, detail="regenerate_of 格式不正確")
    if req.edit_of is not None and not deps._valid_uuid(req.edit_of):
        raise HTTPException(status_code=400, detail="edit_of 格式不正確")
    if req.request_id is not None and not deps._valid_uuid(req.request_id):
        raise HTTPException(status_code=400, detail="request_id 格式不正確")

    async def gen():
        async with _ASK_SEMAPHORE:
            try:
                async for event, payload in deps.answer_question(
                    question,
                    k=k,
                    filters=filters,
                    conversation_id=req.conversation_id,
                    regenerate_of=req.regenerate_of,
                    edit_of=req.edit_of,
                    request_id=req.request_id,
                    locale=req.locale,
                ):
                    yield deps._sse(event, payload)
            except Exception:
                logger.exception("ask failed")
                yield deps._sse("error", {"detail": "問答服務發生錯誤"})

    return StreamingResponse(
        deps._with_heartbeat(gen()),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


class StopRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=ASK_QUESTION_MAX_CHARS)
    conversation_id: str | None = None
    partial_answer: str = Field(default="", max_length=20_000)
    sources: list[dict] | None = Field(default=None, max_length=100)
    ext_sources: list[dict] | None = Field(default=None, max_length=50)
    stages: list[str] | None = Field(default=None, max_length=10)
    regenerate_of: str | None = None
    request_id: str | None = None


@router.post("/api/ask/stop")
async def ask_stop(req: StopRequest):
    """使用者中斷串流時保存部分答案（stopped=true）。回 {qa_id}。"""
    if req.regenerate_of is not None and not deps._valid_uuid(req.regenerate_of):
        raise HTTPException(status_code=400, detail="regenerate_of 格式不正確")
    if req.conversation_id is not None and not deps._valid_uuid(req.conversation_id):
        raise HTTPException(status_code=400, detail="conversation_id 格式不正確")
    if req.request_id is not None and not deps._valid_uuid(req.request_id):
        raise HTTPException(status_code=400, detail="request_id 格式不正確")
    qa_id = await deps.log_stopped_qa(
        (req.question or "").strip(),
        req.partial_answer or "",
        conversation_id=req.conversation_id,
        sources=req.sources,
        ext_sources=req.ext_sources,
        stages=req.stages,
        regenerate_of=req.regenerate_of,
        request_id=req.request_id,
    )
    if qa_id is None:
        raise HTTPException(status_code=503, detail="停止的回答暫時無法保存")
    return {"qa_id": qa_id}