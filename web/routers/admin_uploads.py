"""研報上傳的收檔、查詢與審核（/api/admin/uploads*）。整組限管理員＋`reports.manage`。Admin v1.5 上傳管線 PR-4、PR-6。

本檔有收檔（POST）、清單、詳情（PR-4）與審核（PR-6：preview／file／publish／reject／unreject／retry）；
掃描、入庫與清除是 worker 的事（另一個 PR）。環境變數 `UPLOAD_ENABLED` 預設關閉：關閉時 POST 回 503
`uploads_disabled`，清單、詳情與審核照常可用（沒有資料就是空的）——審核端點不看旗標，關掉收檔時仍要能
處理已經進來的檔案。`UPLOAD_ENABLED` 是上限（ClamAV 與 worker 已安裝）；上限開著時管理員還能以功能旗標
`uploads.intake`（Admin v2，`app/services/feature_flags.py`）暫停收檔，同樣回 503 `uploads_disabled`。

收檔（`POST /api/admin/uploads?filename=&last_modified=`，raw body、`Content-Type: application/pdf`）：

1. 旗標（`UPLOAD_ENABLED` AND `uploads.intake`）→ Content-Type → 檔名清理（`upload_intake.sanitize_filename`）→
   `Content-Length` 超過上限 413。
2. 隔離區可用且剩餘空間足夠（`app/services/quarantine.py`），否則 503 `quarantine_unavailable`。
3. 配額快查（每人每日、全站處理中），超過 429——不必先收完 25 MB 才拒。
4. 串流寫進 `incoming/<upload_id>.part`（0600，邊寫邊算 SHA-256、邊計長，超過上限中止並刪檔 413）；
   檢查 PDF 字面（415）、fsync、rename 成 `<upload_id>.bin`。
5. 同一筆交易：重複檢查（409／422）、配額（429，鎖內精確）、INSERT `quarantined`、稽核 `upload.create`。
   任何一步失敗就 rollback **並刪掉隔離區的檔案**。成功回 202 與上傳紀錄。

審核（狀態閘門與交易細節在 `app/services/upload_review.py`）：

- `GET .../preview`、`GET .../file`：只限 draft／published（其餘 409 `upload_state_conflict`）。預覽回正典文字
  `clean_extracted(full_text)`、標籤、標題、摘要、摘錄，還沒產出的是 null／pending；report_id 讀取時以
  file_hash JOIN 取得。原檔只回語料的 `originals/` presign（帶 filename、TTL ≤ 1 小時，JSON `{url}`），
  **任何狀態都不讀隔離區**——隔離區與感染檔沒有任何端點能取回。
- `POST .../publish`、`.../reject`、`.../unreject`、`.../retry`：條件式 UPDATE 擋競態，狀態與稽核同一筆交易，
  影響列數不對就 rollback 回 409。

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
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, StringConstraints

from app.config import get_settings
from app.services import feature_flags, quarantine, uploads
from app.services.accounts import User
from app.services.object_storage import ObjectNotFound, ObjectStorageError, get_object_storage, original_object_key
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
from app.services.upload_review import REASON_MAX_CHARS, ReportMissingError, ReviewError, UploadPreview
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


class AdminUploadTags(BaseModel):
    market: str | None = None
    is_research: bool
    confidence: float | None = None
    source: str | None = None
    report_date: str | None = None
    report_type: str | None = None
    language: str | None = None
    stock_code: str | None = None
    company_name: str | None = None
    instrument_types: list[str]
    stock_targets: list[str]
    futures_targets: list[str]
    relates_stock: bool | None = None
    relates_futures: bool | None = None


class AdminUploadTakeaway(BaseModel):
    ordinal: int
    claim: str
    quote: str | None = None
    quote_start: int | None = None
    quote_end: int | None = None
    anchor_method: str | None = None


class AdminUploadPreview(BaseModel):
    upload: AdminUpload
    report_id: str
    file_name: str
    publication: Literal["draft", "published"]
    hidden: bool
    # 標題、摘要由批次另外產出：還沒跑時 null、*_state = pending。
    title: str | None = None
    title_original: str | None = None
    title_state: Literal["ready", "pending"]
    summary: str | None = None
    summary_state: Literal["ready", "pending"]
    tags: AdminUploadTags
    # 正典文字 clean_extracted(full_text)；超過上限截斷（text_chars、text_sha256 是完整正典文字的值）。
    text: str | None = None
    text_state: Literal["ready", "missing"]
    text_chars: int
    text_truncated: bool
    text_sha256: str | None = None
    takeaways_state: Literal["ready", "pending", "none"]
    takeaways: list[AdminUploadTakeaway]


class AdminUploadFile(BaseModel):
    url: str
    expires_in: int
    file_name: str


class AdminUploadRejectRequest(BaseModel):
    # 去頭尾空白後 1–500 字（與 DB CHECK 一致）；缺、空白、超長都是 422。
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=REASON_MAX_CHARS)]


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


def _review_error(exc: ReviewError | IntakeError, *, missing_status: int = 409) -> AppError:
    if isinstance(exc, IntakeError):
        return _intake_error(exc)
    status = 422 if exc.code == "invalid_reason" else 409
    if isinstance(exc, ReportMissingError):
        status = missing_status
    extra = {"state": exc.state}
    failure_kind = getattr(exc, "failure_kind", None)
    if failure_kind is not None:
        extra["failure_kind"] = failure_kind
    return AppError(status, exc.code, str(exc), extra=extra)


def _preview(p: UploadPreview) -> AdminUploadPreview:
    return AdminUploadPreview(
        upload=AdminUpload(**_item_fields(p.upload)), report_id=p.report_id, file_name=p.file_name,
        publication=p.publication, hidden=p.hidden, title=p.title, title_original=p.title_original,
        title_state="ready" if p.title else "pending", summary=p.summary,
        summary_state="ready" if p.summary else "pending",
        tags=AdminUploadTags(
            market=p.market, is_research=p.is_research, confidence=p.confidence, source=p.source,
            report_date=_iso(p.report_date), report_type=p.report_type, language=p.language,
            stock_code=p.stock_code, company_name=p.company_name, instrument_types=p.instrument_types,
            stock_targets=p.stock_targets, futures_targets=p.futures_targets, relates_stock=p.relates_stock,
            relates_futures=p.relates_futures,
        ),
        text=p.text, text_state="ready" if p.text else "missing", text_chars=p.text_chars,
        text_truncated=p.text_truncated, text_sha256=p.text_sha256, takeaways_state=p.takeaways_state,
        takeaways=[AdminUploadTakeaway(**vars(t)) for t in p.takeaways],
    )


def _file_unavailable(message: str = "這份上傳的原檔目前無法提供") -> AppError:
    return AppError(404, "upload_file_unavailable", message)


async def _presign_original(file_hash: str, file_name: str, object_key: str | None) -> str:
    """語料原檔（`originals/`）的短效 presign。物件鍵是 DB 資料、不可信：對 file_hash 與檔名重算正典鍵，
    再以 HEAD 的 sha256 metadata 驗證，才簽發（比照 `web/routers/report_file.py`）。沒有本機回退：
    local 模式或沒有物件鍵一律 404——這條路徑永遠不讀本機檔案，更不讀隔離區。"""
    storage = get_object_storage()
    if not storage.enabled or not object_key:
        raise _file_unavailable()
    try:
        canonical = original_object_key(file_hash, file_name)
    except ValueError as exc:
        raise AppError(503, "original_integrity_error", "原檔指標不一致") from exc
    if object_key != canonical:
        raise AppError(503, "original_integrity_error", "原檔指標不一致")
    try:
        head = await asyncio.to_thread(storage.head_object, object_key)
        meta = head.get("Metadata") if isinstance(head, dict) else None
        meta = meta if isinstance(meta, dict) else {}
        if (meta.get("sha256") or meta.get("SHA256")) != file_hash:
            raise AppError(503, "original_integrity_error", "原檔指標不一致")
        return await asyncio.to_thread(
            storage.presign_get, object_key, filename=file_name, inline=file_name.lower().endswith(".pdf"),
        )
    except ObjectNotFound as exc:
        raise _file_unavailable("物件儲存裡找不到這份原檔") from exc
    except ObjectStorageError as exc:
        raise AppError(503, "object_storage_unavailable", "物件儲存暫時無法使用") from exc


async def _transition(action: str, upload_id: str, actor: User, call) -> AdminUpload:
    """審核寫入的共用殼：一個 session、一筆交易；服務層拋錯就 rollback，成功才 commit。"""
    try:
        async with deps.SessionFactory() as session:
            try:
                row = await call(session)
                await session.commit()
            except BaseException:
                await session.rollback()
                raise
    except (ReviewError, IntakeError) as exc:
        raise _review_error(exc) from exc
    logger.info("管理操作 actor=%s action=%s target=%s state=%s", actor.username, action, upload_id, row.state)
    return AdminUpload(**_item_fields(row))


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
    if not await feature_flags.policy("uploads.intake", actor):
        # 功能旗標 uploads.intake（Admin v2）：環境變數上限開著（ClamAV 與 worker 已裝），管理員暫停收檔。
        raise AppError(503, "uploads_disabled", "收檔已由管理員暫停（功能開關 uploads.intake）")
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


@router.get("/api/admin/uploads/{upload_id}/preview", response_model=AdminUploadPreview, dependencies=[_REPORTS])
async def preview_upload(upload_id: str):
    """草稿或已發布上傳的預覽：正典文字、標籤、標題、摘要、摘錄（還沒產出的欄位 null／pending）。"""
    try:
        async with deps.SessionFactory() as session:
            preview = await deps.upload_review.get_preview(session, upload_id)
    except (ReviewError, IntakeError) as exc:
        raise _review_error(exc) from exc
    return _preview(preview)


@router.get("/api/admin/uploads/{upload_id}/file", response_model=AdminUploadFile, dependencies=[_REPORTS])
async def get_upload_file(upload_id: str, response: Response):
    """草稿或已發布上傳的原檔：語料 `originals/` 的短效 presign（JSON `{url}`）。隔離區與感染檔永不提供。"""
    try:
        async with deps.SessionFactory() as session:
            ref = await deps.upload_review.get_original(session, upload_id)
    except (ReviewError, IntakeError) as exc:
        raise _review_error(exc, missing_status=404) from exc
    url = await _presign_original(ref.file_hash, ref.file_name, ref.object_key)
    response.headers["Cache-Control"] = "no-store"
    return AdminUploadFile(url=url, expires_in=get_settings().r2_presign_ttl_seconds, file_name=ref.file_name)


@router.post("/api/admin/uploads/{upload_id}/publish", response_model=AdminUpload, dependencies=[_REPORTS])
async def publish_upload(upload_id: str, actor: User = Depends(authz.current_user)):
    """草稿 → 已發布：同一筆交易更新 visibility 與上傳紀錄、寫稽核 `upload.publish`。只有 draft 可以發布。"""
    return await _transition(
        "upload.publish", upload_id, actor,
        lambda session: deps.upload_review.publish(session, upload_id, actor_id=actor.id),
    )


@router.post("/api/admin/uploads/{upload_id}/reject", response_model=AdminUpload, dependencies=[_REPORTS])
async def reject_upload(upload_id: str, body: AdminUploadRejectRequest, actor: User = Depends(authz.current_user)):
    """退回（必填 `reason`）：draft／quarantined／clean／failed → rejected，寬限期後由 worker 清除；期間可撤銷。"""
    grace = get_settings().upload_reject_grace_hours
    return await _transition(
        "upload.reject", upload_id, actor,
        lambda session: deps.upload_review.reject(
            session, upload_id, reason=body.reason, actor_id=actor.id, grace_hours=grace,
        ),
    )


@router.post("/api/admin/uploads/{upload_id}/unreject", response_model=AdminUpload, dependencies=[_REPORTS])
async def unreject_upload(upload_id: str, actor: User = Depends(authz.current_user)):
    """撤銷退回（寬限期內、尚未清除）：回到由事實推導的退回前狀態，寫稽核 `upload.unreject`。
    回到處理中（quarantined／clean）時也受全站處理中上限（超過 429 `upload_quota_exceeded`）。"""
    max_in_flight = get_settings().upload_max_in_flight
    return await _transition(
        "upload.unreject", upload_id, actor,
        lambda session: deps.upload_review.unreject(
            session, upload_id, actor_id=actor.id, max_in_flight=max_in_flight,
        ),
    )


@router.post("/api/admin/uploads/{upload_id}/retry", response_model=AdminUpload, dependencies=[_REPORTS])
async def retry_upload(upload_id: str, actor: User = Depends(authz.current_user)):
    """可重試的失敗（tag_failed／ingest_error／extract_timeout）→ clean，等 worker 重新處理；寫稽核 `upload.retry`。
    也受全站處理中上限（超過 429 `upload_quota_exceeded`，與收檔同一套計數）。"""
    max_in_flight = get_settings().upload_max_in_flight
    return await _transition(
        "upload.retry", upload_id, actor,
        lambda session: deps.upload_review.retry(session, upload_id, actor_id=actor.id, max_in_flight=max_in_flight),
    )
