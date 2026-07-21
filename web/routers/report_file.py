# web/routers/report_file.py
"""舊 modal 的原始檔資料源：/api/report/{id}/full（metadata）與 /file（原始檔）。

從 web/server.py 拆出（第三步）。與深度研報組（POST /api/report、
/api/report-doc/{id}/pdf）**同前綴但不同組**——那組會產生/下載「生成的」研報，
本組只讀「來源」研報的 metadata 與原始檔。切分依共用碼（本組唯一私有符號
_fetch_report），不依 URL 前綴。

SessionFactory 走 web.deps；file_path 一律由 DB 依 id 取得，無路徑注入。
"""
import os

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import text

from app.services.filename import source_display
from web import deps

router = APIRouter()


async def _fetch_report(session, report_id: str):
    row = (
        await session.execute(
            text(
                "SELECT file_name, market, source, report_date, report_type, "
                "file_path, full_text, summary FROM research.research_report WHERE id = :id"
            ),
            {"id": report_id},
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="report not found")
    return row


@router.get("/api/report/{report_id}/full")
async def report_full(report_id: str):
    """回傳單篇報告的 metadata 與原始檔狀態（供前端 modal 內嵌 PDF）。"""
    async with deps.SessionFactory() as session:
        fn, m, src, rdate, rtype, fpath, _, summary = await _fetch_report(
            session, report_id
        )
    return {
        "report_id": report_id,
        "file_name": fn,
        "market": m,
        "source": source_display(src),
        "summary": summary,
        "report_date": rdate.isoformat() if rdate else None,
        "report_type": rtype,
        "has_file": bool(fpath) and os.path.isfile(fpath),
    }


@router.get("/api/report/{report_id}/file")
async def report_file(report_id: str):
    """提供原始檔（PDF 內嵌、其他下載）。路徑由 DB 依 id 取得，無路徑注入。"""
    async with deps.SessionFactory() as session:
        row = await _fetch_report(session, report_id)
    fpath = row[5]
    if not fpath or not os.path.isfile(fpath):
        raise HTTPException(status_code=404, detail="original file not found")
    name = os.path.basename(fpath)
    is_pdf = name.lower().endswith(".pdf")
    # PDF 用 inline 才能在 modal 的 iframe 內嵌渲染；其他（.docx）維持下載
    return FileResponse(
        fpath,
        media_type="application/pdf" if is_pdf else None,
        filename=name,
        content_disposition_type="inline" if is_pdf else "attachment",
    )

