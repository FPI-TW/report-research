# web/routers/report.py
"""深度研報 API：/api/report（SSE 串流生成）、/api/report-runs/*（背景 run 的探詢與
重連）與 /api/report-doc/{id}/pdf（下載）。

從 web/server.py 拆出（第三步之八）。與舊 modal 的 /api/report/{id}/full、/file
（web/routers/report_file.py）同前綴但不同組——本組產生/下載「生成的」研報，那組
只讀「來源」研報。**重連組刻意用 /api/report-runs 而非 /api/report/... 第三個前綴**：
同一個前綴底下已經有兩組語意不同的路由，再塞一組只會讓下一個人猜錯。

生成本身跑在背景（web/report_runs.py），HTTP 只是訂閱端——重整/關分頁不再殺掉生成。
_REPORT_SEMAPHORE 是模組級狀態，預設序列化研報生成（單機重負載保護）。定義於本
模組、由本模組 handler 使用；server.py 以單一 `from web.routers import report` 匯入，
故只有一個 semaphore 實例。**它現在由背景任務持有，不再由 request handler 持有**：
掛在 handler 上的話，斷線就會釋放 semaphore，而背景任務仍在跑 → 序列化保護落空。

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
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException, Query
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
from web import deps, report_runs

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


def _sse_response(gen) -> StreamingResponse:
    return StreamingResponse(
        deps._with_heartbeat(gen),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _tail(run_id: str) -> AsyncIterator[str]:
    """把一個背景 run 的事件轉成 SSE。

    首事件恆為 `run{run_id, elapsed_ms}`：前端據此記住 handle（重整後可再接回）並回推
    起始時刻算已耗時／預估剩餘。放在最前面是為了讓它在**任何**生成事件之前抵達——
    重連時 replay 會先湧出一批舊事件，夾在中間就等於沒有。
    """
    yield deps._sse("run", {"run_id": run_id, "elapsed_ms": report_runs.elapsed_ms(run_id)})
    async for event, payload in report_runs.subscribe(run_id):
        yield deps._sse(event, payload)


@router.post("/api/report")
async def report(req: ReportRequest):
    """深度研報生成：深度檢索 → 串流撰寫 → 渲染 PDF。回 text/event-stream。

    事件序：run → status(retrieving/outlining/writing/verifying/rendering) → outline →
    sources → token/section_draft/section_skipped… → done{download_url}。

    生成跑在背景任務，本回應只是訂閱端：**中途斷線不會中止生成**，重連走
    GET /api/report-runs/{run_id}/stream。同題重按只會接回既有 run，不會跑兩份。
    """
    question = (req.question or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="question 不可為空")
    if req.qa_id is not None and not deps._valid_uuid(req.qa_id):
        raise HTTPException(status_code=400, detail="qa_id 格式不正確")
    if req.conversation_id is not None and not deps._valid_uuid(req.conversation_id):
        raise HTTPException(status_code=400, detail="conversation_id 格式不正確")

    def _make_events() -> AsyncIterator[tuple[str, object]]:
        # semaphore 在背景任務內持有（見模組 docstring）：掛在 handler 上的話，使用者
        # 一斷線就把重負載保護一起放掉了。
        async def gen() -> AsyncIterator[tuple[str, object]]:
            async with _REPORT_SEMAPHORE:
                try:
                    async for event, payload in generate_report(
                        question, filters={},
                        conversation_id=req.conversation_id, qa_id=req.qa_id,
                        template_id=req.template_id, locale=req.locale,
                    ):
                        yield (event, payload)
                except asyncio.CancelledError:
                    raise  # 取消不是錯誤：交給 _pump 發「已取消」並收尾
                except Exception:
                    logger.exception("report failed")
                    yield ("error", {"detail": "研報生成發生錯誤"})

        return gen()

    run_id, _is_new = report_runs.start_or_attach(
        question=question, conversation_id=req.conversation_id, qa_id=req.qa_id,
        template_id=req.template_id, locale=req.locale, make_events=_make_events,
    )
    return _sse_response(_tail(run_id))


@router.get("/api/report-runs")
async def report_runs_active(conversation_id: str = Query(...)):
    """某對話目前仍在背景生成的研報。前端載入對話時據此自動接回進度框。"""
    if not deps._valid_uuid(conversation_id):
        raise HTTPException(status_code=400, detail="conversation_id 格式不正確")
    return {"runs": report_runs.find_active(conversation_id)}


@router.get("/api/report-runs/{run_id}/stream")
async def report_run_stream(run_id: str):
    """重連一個背景 run：先重播已發生的事件，再接上直播。

    未知 run_id 回 404 而非空串流——行程重啟後前端手上的 id 已無意義，明確的 404 讓它
    退回「重新生成」，空串流只會讓進度框永遠停在 0%。
    """
    if not deps._valid_uuid(run_id) or not report_runs.exists(run_id):
        raise HTTPException(status_code=404, detail="run not found")
    return _sse_response(_tail(run_id))


@router.post("/api/report-runs/{run_id}/cancel")
async def report_run_cancel(run_id: str):
    """使用者主動中止生成。背景執行後這是唯一的停止手段——關掉分頁不再等於取消。"""
    if not deps._valid_uuid(run_id):
        raise HTTPException(status_code=404, detail="run not found")
    return {"cancelled": report_runs.cancel(run_id)}


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
    # locale 一律沿用**產出當時**的值：換皮只換版型，不該改變輸出語言。
    # 不沿用的話英文研報換皮後會變成「英文內文 + 中文封面/頁首/免責」——而免責聲明
    # 正是可轉寄 PDF 上最不該漂移的東西。歷史列為 NULL → fail-open 到 zh-Hant，
    # 恰好就是那些列產出時的實際行為。
    doc_locale = doc.get("locale")
    # template_id 未帶＝維持原模板（不是「換成預設模板」）。
    target_template = req.template_id or doc.get("template_id")
    try:
        pdf_bytes = await asyncio.to_thread(
            render_report_pdf, doc["markdown"], title=doc["title"],
            meta={"date": doc.get("date") or "", "question": doc.get("question")},
            template_id=target_template, locale=doc_locale,
        )
    except Exception:
        # 渲染分派層本身 fail-open 回退 weasyprint；仍拋代表兩軌皆炸 → 保留上一版
        logger.exception("換皮重出渲染失敗，保留上一個 rendition")
        raise HTTPException(status_code=500, detail="重新渲染失敗，已保留上一個版本")
    content_hash = hashlib.sha256(doc["markdown"].encode("utf-8")).hexdigest()
    # 先建 rendition_id 再落地（用其短碼當檔名後綴，不覆蓋歷史 PDF），最後原子切換指標
    rendition_id = await create_rendition(
        report_id, renderer=REPORT_RENDERER,
        template_id=(target_template if REPORT_RENDERER == "typst" else None),
        content_hash=content_hash,
        pdf_path=await asyncio.to_thread(
            write_report_pdf, report_id, pdf_bytes,
            suffix=f"-{hashlib.sha256((report_id + content_hash + (target_template or '')).encode()).hexdigest()[:8]}",
        ),
    )
    await set_current_rendition(report_id, rendition_id)
    return {"rendition_id": rendition_id, "template_id": target_template}


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
        # 重建也必須沿用產出當時的 locale/template_id（docs/qa_pdf_report_deployment.md
        # 正是以「PDF 可重建」為由主張 REPORTS_DIR 不需備份——重建出不一樣的東西，
        # 那個主張就不成立了）。
        pdf_bytes = await asyncio.to_thread(
            render_report_pdf, doc["markdown"], title=doc["title"],
            meta={"date": doc.get("date") or "", "question": doc.get("question")},
            template_id=doc.get("template_id"), locale=doc.get("locale"),
        )
        path = await asyncio.to_thread(write_report_pdf, report_id, pdf_bytes)
    return FileResponse(
        path, media_type="application/pdf",
        filename=f"report-{report_id[:8]}.pdf",
        headers={"Cache-Control": "no-cache"},
    )