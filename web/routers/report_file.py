# web/routers/report_file.py
"""舊 modal 的原始檔資料源：/api/report/{id}/full（metadata）與 /file（原始檔）。

從 web/server.py 拆出（第三步）。本組只讀「來源」研報的 metadata 與原始檔；
`/api/report/` 這個前綴過去也被已移除的生成功能使用（POST /api/report），
切分依共用碼（本組唯一私有符號 _fetch_report），不依 URL 前綴。

SessionFactory 走 web.deps；file_path 一律由 DB 依 id 取得，無路徑注入。
"""
import asyncio
import hashlib
import os

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import text

from app.services.filename import source_display
from app.services.object_storage import (
    ObjectNotFound,
    ObjectStorageError,
    get_object_storage,
    original_available,
    original_object_key,
)
from web import deps

router = APIRouter()


def _local_file_matches_sha256(path: str, expected_sha256: str | None) -> bool:
    """Hash a hybrid fallback incrementally; an original has no safe binary rebuild path."""
    if not isinstance(expected_sha256, str) or len(expected_sha256) != 64:
        return False
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return False
    return digest.hexdigest() == expected_sha256


def _require_report_id(report_id: str) -> None:
    """id 是 uuid 欄位：非法字串會讓驅動在編碼期拋例外變 500，先擋成與查無此篇相同的 404。

    在開 session 之前呼叫——非法 id 不該佔一條連線。
    """
    if not deps._valid_uuid(report_id):
        raise HTTPException(status_code=404, detail="report not found")


async def _fetch_report(session, report_id: str):
    row = (
        await session.execute(
            text(
                "SELECT file_name, market, source, report_date, report_type, "
                "file_path, full_text, summary, title, source_object_key, file_hash "
                "FROM research.research_report WHERE id = :id"
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
    _require_report_id(report_id)
    async with deps.SessionFactory() as session:
        row = await _fetch_report(session, report_id)
        fn, m, src, rdate, rtype, fpath, _full_text, summary, title = row[:9]
        object_key = row[9] if len(row) > 9 else None
    return {
        "report_id": report_id,
        "file_name": fn,
        # 報告內部標題（顯示用）；None＝尚未產生，前端回退 file_name
        "title": title,
        "market": m,
        "source": source_display(src),
        "summary": summary,
        "report_date": rdate.isoformat() if rdate else None,
        "report_type": rtype,
        "has_file": original_available(get_object_storage(), object_key, fpath),
    }


@router.get("/api/report/{report_id}/file")
async def report_file(report_id: str):
    """提供原始檔（PDF 內嵌、其他下載）。路徑由 DB 依 id 取得，無路徑注入。"""
    _require_report_id(report_id)
    async with deps.SessionFactory() as session:
        row = await _fetch_report(session, report_id)
    fpath, object_key = row[5], row[9] if len(row) > 9 else None
    file_hash = row[10] if len(row) > 10 else None
    storage = get_object_storage()
    hybrid_local_integrity_required = storage.mode == "hybrid" and not object_key
    if storage.enabled and object_key:
        if (
            not isinstance(file_hash, str)
            or len(file_hash) != 64
            or any(char not in "0123456789abcdef" for char in file_hash)
        ):
            raise HTTPException(status_code=503, detail="original object pointer integrity error")
        try:
            canonical_key = original_object_key(file_hash, row[0])
        except ValueError as exc:
            raise HTTPException(status_code=503, detail="original object pointer integrity error") from exc
        if object_key != canonical_key:
            # A DB object key is untrusted data.  Never mint a bearer URL for a different
            # report merely because that unrelated object exists in our private bucket.
            raise HTTPException(status_code=503, detail="original object pointer integrity error")
        try:
            # Explicit HEAD preserves the missing-vs-service distinction and makes the
            # integrity check above unambiguously precede every remote operation.
            metadata = await asyncio.to_thread(storage.head_object, object_key)
            raw_metadata = metadata.get("Metadata") if isinstance(metadata, dict) else None
            object_metadata = raw_metadata if isinstance(raw_metadata, dict) else {}
            metadata_sha = object_metadata.get("sha256") or object_metadata.get("SHA256")
            if not isinstance(metadata_sha, str) or metadata_sha != file_hash:
                raise HTTPException(status_code=503, detail="original object pointer integrity error")
            url = await asyncio.to_thread(
                storage.presign_get, object_key, filename=row[0], inline=str(row[0]).lower().endswith(".pdf"),
            )
            return RedirectResponse(url=url, status_code=302, headers={"Cache-Control": "no-store"})
        except ObjectNotFound:
            if storage.mode == "r2":
                raise HTTPException(status_code=404, detail="original file not found")
            hybrid_local_integrity_required = storage.mode == "hybrid"
        except ObjectStorageError as exc:
            raise HTTPException(status_code=503, detail="object storage unavailable") from exc
    elif storage.mode == "r2":
        raise HTTPException(status_code=404, detail="original file not found")
    if not fpath or not os.path.isfile(fpath):
        if hybrid_local_integrity_required:
            raise HTTPException(status_code=503, detail="original local fallback integrity error")
        raise HTTPException(status_code=404, detail="original file not found")
    if hybrid_local_integrity_required and not await asyncio.to_thread(_local_file_matches_sha256, fpath, file_hash):
        raise HTTPException(status_code=503, detail="original local fallback integrity error")
    name = os.path.basename(fpath)
    is_pdf = name.lower().endswith(".pdf")
    # PDF 用 inline 才能在 modal 的 iframe 內嵌渲染；其他（.docx）維持下載
    return FileResponse(
        fpath,
        media_type="application/pdf" if is_pdf else None,
        filename=name,
        content_disposition_type="inline" if is_pdf else "attachment",
    )
