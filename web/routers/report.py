# web/routers/report.py
"""深度研報 API：/api/report（SSE 串流生成）與 /api/report-doc/{id}/pdf（下載）。

從 web/server.py 拆出（第三步之八）。與舊 modal 的 /api/report/{id}/full、/file
（web/routers/report_file.py）同前綴但不同組——本組產生/下載「生成的」研報，那組
只讀「來源」研報。

_REPORT_SEMAPHORE 是模組級狀態，預設序列化研報生成（單機重負載保護）。定義於本
模組、由本模組 handler 使用；server.py 以單一 `from web.routers import report` 匯入，
故只有一個 semaphore 實例。

render_report_pdf 刻意由 app.services.report（分派層，依 REPORT_RENDERER 選
typst/weasyprint 並在失敗時回退）匯入，而非 app.services.pdf.render_report_pdf
（只會是 WeasyPrint）。test_render_dispatch 以 identity 檢查鎖死這條契約。
generate_report/fetch_report_doc/write_report_pdf 同樣直接匯入；_valid_uuid/_sse/
_with_heartbeat 走 web.deps。
"""
import asyncio
import hashlib
import logging
import os

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from app.services.report import (
    REPORT_RENDERER,
    create_rendition,
    fetch_current_rendition_pdf,
    fetch_report_doc,
    generate_report,
    render_report_pdf,
    set_current_rendition,
    write_report_pdf,
)
from web import deps

logger = logging.getLogger(__name__)

router = APIRouter()


# 研報生成比問答重很多（長輸出 + PDF 排版），預設序列化避免區網多人同時生成拖垮機器。
_REPORT_SEMAPHORE = asyncio.Semaphore(int(os.getenv("REPORT_SEMAPHORE", "1")))


class ReportRequest(BaseModel):
    question: str
    conversation_id: str | None = None
    qa_id: str | None = None
    template_id: str | None = None  # M9b：選渲染模板；未知/未帶 → 預設（fail-safe）
    locale: str | None = None  # M10：輸出語言（zh-Hant/en）；未帶/未知 → 預設中文（fail-open）


@router.post("/api/report")
async def report(req: ReportRequest):
    """深度研報生成：深度檢索 → 串流撰寫 → 渲染 PDF。回 text/event-stream。

    事件序：status(retrieving/writing/rendering) → sources → token… → done{download_url}。
    """
    question = (req.question or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="question 不可為空")
    if req.qa_id is not None and not deps._valid_uuid(req.qa_id):
        raise HTTPException(status_code=400, detail="qa_id 格式不正確")
    if req.conversation_id is not None and not deps._valid_uuid(req.conversation_id):
        raise HTTPException(status_code=400, detail="conversation_id 格式不正確")

    async def gen():
        async with _REPORT_SEMAPHORE:
            try:
                async for event, payload in generate_report(
                    question, filters={},
                    conversation_id=req.conversation_id, qa_id=req.qa_id,
                    template_id=req.template_id, locale=req.locale,
                ):
                    yield deps._sse(event, payload)
            except Exception:
                logger.exception("report failed")
                yield deps._sse("error", {"detail": "研報生成發生錯誤"})

    return StreamingResponse(
        deps._with_heartbeat(gen()),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/api/report-templates")
async def report_templates():
    """可選研報渲染模板清單（M9b registry）；前端模板選擇器資料源。"""
    from app.templates import manifest

    return {
        "templates": [
            {
                "id": t.id, "name": t.name, "description": t.description,
                "is_default": t.is_default, "thumbnail": t.thumbnail,
            }
            for t in manifest.list_templates()
        ]
    }


class RerenderRequest(BaseModel):
    template_id: str | None = None  # 未知/未帶 → 預設（fail-safe）


@router.post("/api/report-doc/{report_id}/rerender")
async def report_doc_rerender(report_id: str, req: RerenderRequest):
    """換皮重出（M9b）：用既有 markdown 以另一模板產新 rendition，成功後原子切換目前
    版本——**零 LLM、零重新生成**。失敗保留上一個可下載 PDF（回 500，不動指標）。
    """
    if not deps._valid_uuid(report_id):
        raise HTTPException(status_code=404, detail="report not found")
    doc = await fetch_report_doc(report_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="report not found")
    try:
        pdf_bytes = await asyncio.to_thread(
            render_report_pdf, doc["markdown"], title=doc["title"],
            meta={"date": doc.get("date") or "", "question": doc.get("question")},
            template_id=req.template_id,
        )
    except Exception:
        # 渲染分派層本身 fail-open 回退 weasyprint；仍拋代表兩軌皆炸 → 保留上一版
        logger.exception("換皮重出渲染失敗，保留上一個 rendition")
        raise HTTPException(status_code=500, detail="重新渲染失敗，已保留上一個版本")
    content_hash = hashlib.sha256(doc["markdown"].encode("utf-8")).hexdigest()
    # 先建 rendition_id 再落地（用其短碼當檔名後綴，不覆蓋歷史 PDF），最後原子切換指標
    rendition_id = await create_rendition(
        report_id, renderer=REPORT_RENDERER,
        template_id=(req.template_id if REPORT_RENDERER == "typst" else None),
        content_hash=content_hash,
        pdf_path=await asyncio.to_thread(
            write_report_pdf, report_id, pdf_bytes,
            suffix=f"-{hashlib.sha256((report_id + content_hash + (req.template_id or '')).encode()).hexdigest()[:8]}",
        ),
    )
    await set_current_rendition(report_id, rendition_id)
    return {"rendition_id": rendition_id, "template_id": req.template_id}


@router.get("/api/report-doc/{report_id}/pdf")
async def report_doc_pdf(report_id: str):
    """下載目前渲染版本的研報 PDF；無 rendition 指標→回退原始 pdf_path，仍不存在→即時重建。"""
    if not deps._valid_uuid(report_id):
        raise HTTPException(status_code=404, detail="report not found")
    # M9b：優先服務目前 rendition（換皮重出後）；NULL 指標或舊列 → 回退 report_doc.pdf_path
    path = await fetch_current_rendition_pdf(report_id)
    doc = None
    if not path or not os.path.isfile(path):
        doc = await fetch_report_doc(report_id)
        if doc is None:
            raise HTTPException(status_code=404, detail="report not found")
        path = doc.get("pdf_path")
    if not path or not os.path.isfile(path):
        pdf_bytes = await asyncio.to_thread(
            render_report_pdf, doc["markdown"], title=doc["title"],
            meta={"date": doc.get("date") or "", "question": doc.get("question")},
        )
        path = await asyncio.to_thread(write_report_pdf, report_id, pdf_bytes)
    return FileResponse(
        path, media_type="application/pdf",
        filename=f"report-{report_id[:8]}.pdf",
        headers={"Cache-Control": "no-cache"},
    )