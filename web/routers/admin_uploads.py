"""研報上傳的收檔與查詢（/api/admin/uploads*）。整組限管理員＋`reports.manage`。Admin v1.5 上傳管線 PR-4。

本檔只有收檔（POST）、清單與詳情；掃描、入庫（worker）與審核（preview／file／publish／reject／retry）
在後續的 PR。功能旗標 `UPLOAD_ENABLED` 預設關閉：關閉時 POST 回 503 `uploads_disabled`，清單與詳情照常
可讀（沒有資料就是空的）。

收檔（`POST /api/admin/uploads?filename=&last_modified=`，raw body、`Content-Type: application/pdf`）：

1. 旗標 → Content-Type → 檔名清理（`upload_intake.sanitize_filename`）→ `Content-Length` 超過上限 413。
2. 隔離區可用且剩餘空間足夠（`app/services/quarantine.py`），否則 503 `quarantine_unavailable`。
3. 配額快查（每人每日、全站處理中），超過 429——不必先收完 25 MB 才拒。
4. 串流寫進 `incoming/<upload_id>.part`（0600，邊寫邊算 SHA-256、邊計長，超過上限中止並刪檔 413）；
   檢查 PDF 字面（415）、fsync、rename 成 `<upload_id>.bin`。
5. 同一筆交易：重複檢查（409／422）、配額（429，鎖內精確）、INSERT `quarantined`、稽核 `upload.create`。
   任何一步失敗就 rollback **並刪掉隔離區的檔案**。成功回 202 與上傳紀錄。

web **不解析 PDF 內容**（主動內容與加密由 worker 在子行程裡查）、**不連 clamd**；清單的 `scanner`
摘要由 DB 推導。CSRF：`web/server.py` 的 `reject_cross_site` 在讀 body 之前就擋掉跨站 POST；另外要求
`Content-Type: application/pdf`，瀏覽器的跨站表單送不出這種 Content-Type（不經 CORS preflight 不行）。

輔助函式一律放在 `@router` 裝飾器之上（夾在裝飾器與 handler 之間會讓端點回 422）。
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from app.config import get_settings
from app.services import quarantine, uploads
from app.services.accounts import User
from app.services.upload_intake import (
    DuplicateInCorpusError,
    IntakeError,
    InvalidFilenameError,
    KnownInfectedError,
    NotPdfFilenameError,
    QuotaExceededError,
    ScannerSummary,
    UploadInProgressError,
    UploadNotFoundError,
    UploadReport,
    UploadRow,
    sanitize_filename,
)
from web import authz, deps
from web.errors import AppError

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(authz.require_admin)])

_REPORTS = Depends(authz.require_scope("reports.manage"))

UploadState = Literal[uploads.STATES]  # type: ignore[valid-type]
_MIB = 1024 * 1024
# File.lastModified（毫秒）的上限：datetime 能表示的最後一刻（9999-12-31）。
_MAX_MTIME_MS = 253402300799999
_PDF_MEDIA_TYPE = "application/pdf"
# raw body 端點沒有 pydantic 請求模型：在 OpenAPI 裡宣告 body 是 application/pdf 的二進位，
# scripts/gen_admin_client.py 據此產生上傳網址與回應 schema（不產生 JSON 呼叫函式）。
_PDF_BODY = {
    "requestBody": {
        "required": True,
        "content": {_PDF_MEDIA_TYPE: {"schema": {"type": "string", "format": "binary"}}},
    },
}


class AdminUpload(BaseModel):
    upload_id: str
    file_hash: str
    original_name: str
    size_bytes: int
    client_mtime: str | None = None
    uploaded_by: str | None = None
    uploaded_at: str
    state: UploadState
    state_changed_at: str
    scan_attempts: int = 0
    scan_engine: str | None = None
    scan_signature: str | None = None
    scanned_at: str | None = None
    scan_last_error: str | None = None
    process_attempts: int = 0
    failure_kind: str | None = None
    failure_detail: str | None = None
    processed_at: str | None = None
    decided_by: str | None = None
    decided_at: str | None = None
    decision_reason: str | None = None
    purge_after: str | None = None
    purged_at: str | None = None


class AdminUploadScanner(BaseModel):
    pending: int
    scanning: int
    oldest_pending_at: str | None = None
    oldest_pending_seconds: int | None = None
    last_error: str | None = None
    last_error_at: str | None = None


class AdminUploadListResponse(BaseModel):
    total: int
    limit: int
    offset: int
    has_more: bool
    next_offset: int | None
    items: list[AdminUpload]
    scanner: AdminUploadScanner


class AdminUploadReport(BaseModel):
    report_id: str
    title: str | None = None
    publication: Literal["draft", "published"]
    hidden: bool
    extractor: str | None = None
    extraction_version: str | None = None
    quality_score: float | None = None
    page_count: int | None = None
    pages_failed: list[int] | None = None
    needs_review: bool = False
    created_at: str | None = None


class AdminUploadDetail(AdminUpload):
    # 語料裡同 file_hash 的研報（worker 入庫後才有；還在隔離區或已清除時 null）。
    report: AdminUploadReport | None = None


# ── 輔助函式一律放在所有 @router.* 裝飾器之上 ──────────────────────────────


def _iso(v) -> str | None:
    return v.isoformat() if hasattr(v, "isoformat") else (str(v) if v is not None else None)


def _item_fields(r: UploadRow) -> dict:
    return {
        "upload_id": r.upload_id, "file_hash": r.file_hash, "original_name": r.original_name,
        "size_bytes": r.size_bytes, "client_mtime": _iso(r.client_mtime), "uploaded_by": r.uploaded_by,
        "uploaded_at": _iso(r.uploaded_at), "state": r.state, "state_changed_at": _iso(r.state_changed_at),
        "scan_attempts": r.scan_attempts, "scan_engine": r.scan_engine, "scan_signature": r.scan_signature,
        "scanned_at": _iso(r.scanned_at), "scan_last_error": r.scan_last_error,
        "process_attempts": r.process_attempts, "failure_kind": r.failure_kind, "failure_detail": r.failure_detail,
        "processed_at": _iso(r.processed_at), "decided_by": r.decided_by, "decided_at": _iso(r.decided_at),
        "decision_reason": r.decision_reason, "purge_after": _iso(r.purge_after), "purged_at": _iso(r.purged_at),
    }


def _report(r: UploadReport | None) -> AdminUploadReport | None:
    if r is None:
        return None
    return AdminUploadReport(
        report_id=r.report_id, title=r.title, publication=r.publication, hidden=r.hidden, extractor=r.extractor,
        extraction_version=r.extraction_version, quality_score=r.quality_score, page_count=r.page_count,
        pages_failed=r.pages_failed, needs_review=r.needs_review, created_at=_iso(r.created_at),
    )


def _scanner(s: ScannerSummary) -> AdminUploadScanner:
    return AdminUploadScanner(
        pending=s.pending, scanning=s.scanning, oldest_pending_at=_iso(s.oldest_pending_at),
        oldest_pending_seconds=s.oldest_pending_seconds, last_error=s.last_error, last_error_at=_iso(s.last_error_at),
    )


def _intake_error(exc: IntakeError) -> AppError:
    if isinstance(exc, DuplicateInCorpusError):
        return AppError(409, "upload_duplicate", str(exc), extra={
            "file_hash": exc.file_hash, "existing": "corpus", "status": exc.status, "upload_id": exc.upload_id,
        })
    if isinstance(exc, UploadInProgressError):
        return AppError(409, "upload_duplicate", str(exc), extra={
            "file_hash": exc.file_hash, "existing": "upload", "status": exc.state, "upload_id": exc.upload_id,
        })
    if isinstance(exc, KnownInfectedError):
        return AppError(422, "upload_known_infected", str(exc), extra={"file_hash": exc.file_hash})
    if isinstance(exc, QuotaExceededError):
        return AppError(429, "upload_quota_exceeded", str(exc), extra={
            "quota": exc.scope, "limit": exc.limit, "used": exc.used,
        })
    if isinstance(exc, NotPdfFilenameError):
        return AppError(415, "upload_not_pdf", str(exc))
    if isinstance(exc, InvalidFilenameError):
        return AppError(400, "invalid_filename", str(exc))
    if isinstance(exc, UploadNotFoundError):
        return AppError(404, "not_found", str(exc))
    return AppError(400, "bad_request", str(exc))


def _too_large(max_bytes: int) -> AppError:
    return AppError(413, "upload_too_large", f"檔案超過上限 {max_bytes / _MIB:g} MB", extra={"max_bytes": max_bytes})


def _quarantine_unavailable(exc: quarantine.QuarantineError) -> AppError:
    return AppError(503, "quarantine_unavailable", str(exc))


def _is_pdf_content_type(value: str | None) -> bool:
    return (value or "").split(";", 1)[0].strip().lower() == _PDF_MEDIA_TYPE


def _declared_length(request: Request) -> int | None:
    raw = request.headers.get("content-length")
    if raw is None:
        return None  # chunked：串流時邊寫邊計
    raw = raw.strip()
    if not raw.isdigit():
        raise AppError(400, "bad_request", "Content-Length 不合法")
    return int(raw)


def _client_mtime(ms: int | None) -> datetime | None:
    return None if ms is None else datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


async def _receive(request: Request, writer: quarantine.QuarantineWriter, max_bytes: int) -> quarantine.StoredFile:
    """把 body 串流進隔離區，回落地後的檔案。任何失敗（含用戶端斷線、取消）都刪掉 `.part`／`.bin`。"""
    try:
        writer.open()
        async for chunk in request.stream():
            writer.write(chunk)
        return await asyncio.to_thread(writer.finalize)
    except quarantine.UploadTooLarge as exc:
        writer.discard()
        raise _too_large(max_bytes) from exc
    except quarantine.NotPdf as exc:
        writer.discard()
        raise AppError(415, "upload_not_pdf", str(exc)) from exc
    except quarantine.QuarantineError as exc:
        writer.discard()
        raise _quarantine_unavailable(exc) from exc
    except BaseException:
        writer.discard()
        raise


@router.post(
    "/api/admin/uploads", status_code=202, response_model=AdminUpload, dependencies=[_REPORTS],
    openapi_extra=_PDF_BODY,
)
async def create_upload(
    request: Request,
    filename: str = Query(..., min_length=1, max_length=1024),
    last_modified: int | None = Query(None, ge=0, le=_MAX_MTIME_MS),
    actor: User = Depends(authz.current_user),
):
    """收一份 PDF 進隔離區（raw body、`Content-Type: application/pdf`）；回 202 與 `quarantined` 的上傳紀錄。

    `filename` 是原始檔名（清理後只存 DB），`last_modified` 是瀏覽器的 `File.lastModified`（毫秒）。
    """
    s = get_settings()
    if not s.upload_enabled:
        raise AppError(503, "uploads_disabled", "上傳功能尚未開放")
    if not _is_pdf_content_type(request.headers.get("content-type")):
        raise AppError(415, "upload_not_pdf", "請以 Content-Type: application/pdf 上傳 PDF 檔案內容")
    try:
        name = sanitize_filename(filename)
    except IntakeError as exc:
        raise _intake_error(exc) from exc
    declared = _declared_length(request)
    if declared is not None and declared > s.upload_max_bytes:
        raise _too_large(s.upload_max_bytes)
    if declared == 0:
        raise AppError(415, "upload_not_pdf", "檔案是空的")

    root = quarantine.quarantine_dir(s)
    try:
        quarantine.ensure_dirs(root)
        quarantine.check_free_space(
            root, needed_bytes=declared if declared is not None else s.upload_max_bytes,
            min_free_bytes=s.upload_min_free_mb * _MIB,
        )
    except quarantine.QuarantineError as exc:
        logger.error("隔離區不可用 dir=%s：%s", root, exc)
        raise _quarantine_unavailable(exc) from exc

    try:
        async with deps.SessionFactory() as session:
            await deps.upload_intake.check_quota(
                session, actor_id=actor.id, daily_quota=s.upload_daily_quota, max_in_flight=s.upload_max_in_flight,
            )
    except IntakeError as exc:
        raise _intake_error(exc) from exc
    except Exception as exc:
        logger.exception("上傳配額查詢失敗")
        raise HTTPException(503, "上傳服務暫時無法使用，請稍後再試") from exc

    upload_id = str(uuid.uuid4())
    writer = quarantine.QuarantineWriter(root, upload_id, max_bytes=s.upload_max_bytes)
    stored = await _receive(request, writer, s.upload_max_bytes)

    try:
        async with deps.SessionFactory() as session:
            try:
                row = await deps.upload_intake.create_upload(
                    session, upload_id=upload_id, file_hash=stored.sha256, original_name=name,
                    size_bytes=stored.size_bytes, client_mtime=_client_mtime(last_modified), actor_id=actor.id,
                    daily_quota=s.upload_daily_quota, max_in_flight=s.upload_max_in_flight,
                )
                await session.commit()
            except BaseException:
                await session.rollback()
                raise
    except IntakeError as exc:
        writer.discard()
        raise _intake_error(exc) from exc
    except Exception as exc:
        writer.discard()
        logger.exception("上傳紀錄寫入失敗，已刪除隔離區檔案 upload_id=%s", upload_id)
        raise HTTPException(503, "上傳紀錄寫入失敗，檔案未保留，請稍後再試") from exc
    except BaseException:
        writer.discard()
        raise
    logger.info(
        "管理操作 actor=%s action=upload.create target=%s size=%d", actor.username, upload_id, stored.size_bytes,
    )
    return AdminUpload(**_item_fields(row))


@router.get("/api/admin/uploads", response_model=AdminUploadListResponse, dependencies=[_REPORTS])
async def list_uploads(
    state: UploadState | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """上傳紀錄清單（上傳新→舊，可依 `state` 篩選），附由 DB 推導的掃描器摘要 `scanner`。"""
    async with deps.SessionFactory() as session:
        total, rows = await deps.upload_intake.list_uploads(session, state=state, limit=limit, offset=offset)
        scanner = await deps.upload_intake.scanner_summary(session)
    next_offset = offset + len(rows)
    has_more = next_offset < total
    return AdminUploadListResponse(
        total=total, limit=limit, offset=offset, has_more=has_more,
        next_offset=next_offset if has_more else None,
        items=[AdminUpload(**_item_fields(r)) for r in rows], scanner=_scanner(scanner),
    )


@router.get("/api/admin/uploads/{upload_id}", response_model=AdminUploadDetail, dependencies=[_REPORTS])
async def get_upload(upload_id: str):
    """一筆上傳紀錄的詳情：掃描結果、失敗原因與決策，以及入庫後的研報與抽取品質（`report`）。"""
    async with deps.SessionFactory() as session:
        try:
            row = await deps.upload_intake.get_upload(session, upload_id)
        except IntakeError as exc:
            raise _intake_error(exc) from exc
        report = await deps.upload_intake.get_upload_report(session, row.file_hash)
    return AdminUploadDetail(**_item_fields(row), report=_report(report))
