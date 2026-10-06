"""研報上傳的收檔與查詢（`research.report_upload`，revision 0008；Admin v1.5 上傳管線）。

`web/routers/admin_uploads.py` 呼叫：檔名清理是純函式（直接 import），DB 的部分一律經
`deps.upload_intake`（測試的替換點）。隔離區的檔案寫入在 `app/services/quarantine.py`；狀態詞彙在
`app/services/uploads.py`。本模組不做掃描、入庫與審核（worker 與審核 API 各自的 PR）。

收檔的 DB 那一步（`create_upload`）在**呼叫端的同一筆交易**裡依序做：

1. `pg_advisory_xact_lock`：把收檔的「檢查 → INSERT」排成一列，配額才是精確的（兩個請求同時
   在第 29 份時不會都過）。鎖只包這一小段 SQL，交易一 commit 就放。
2. 重複檢查：語料已有同 hash → `DuplicateInCorpusError`（409，帶它目前是隱藏／草稿／已發布）；
   已有進行中的上傳 → `UploadInProgressError`（409）；曾判感染 → `KnownInfectedError`（422）。
3. 配額：每人每日（台北時間的日曆日，所有狀態都算）、全站處理中（`uploads.IN_FLIGHT_STATES`）。
4. INSERT（state `quarantined`）。同一個 file_hash 的進行中上傳由 partial unique index
   `idx_report_upload_active_hash` 擋：上面的檢查之外，任何別的路徑（例如日後審核 API 的 retry
   把 failed 轉回 clean）與收檔撞上時，INSERT 的 unique violation 一樣轉成 `UploadInProgressError`。
5. 稽核 `upload.create`（detail 只放 upload_id、檔名、大小、狀態）。呼叫端負責 commit；任何一步失敗
   呼叫端 rollback 並刪掉隔離區的檔案——稽核寫不進去，上傳紀錄也不留。

管理面不過濾可見性（這裡查 `research_report` 是為了回報重複與詳情裡的抽取品質，不是使用者讀取路徑）。
bind 參數一律 `CAST(:x AS ...)`。
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import uploads

FILENAME_MAX_CHARS = 255
PDF_SUFFIX = ".pdf"
TZ_NAME = "Asia/Taipei"  # 每日配額的「一天」
# pg_advisory_xact_lock 的鍵：固定常數（"uploadin" 的 ASCII），全庫唯一即可。
INTAKE_LOCK_KEY = int.from_bytes(b"uploadin", "big")
ACTIVE_INDEX = "idx_report_upload_active_hash"

# 從檔名剔除的字元類別：控制字元、格式字元（含 RTL 覆寫 U+202E 這種能把 "fdp.exe" 顯示成 "exe.pdf" 的）、
# 行與段落分隔符。
_STRIP_CATEGORIES = frozenset({"Cc", "Cf", "Zl", "Zp"})
_PATH_SEP = re.compile(r"[\\/]")


# ── 錯誤 ────────────────────────────────────────────────────────────────


class IntakeError(Exception):
    """收檔與查詢的可預期錯誤；訊息是給管理員看的中文。"""


class InvalidFilenameError(IntakeError):
    pass


class NotPdfFilenameError(IntakeError):
    pass


class UploadNotFoundError(IntakeError):
    pass


class DuplicateInCorpusError(IntakeError):
    def __init__(self, file_hash: str, status: str, upload_id: Optional[str] = None):
        label = {"hidden": "已隱藏", "draft": "草稿", "published": "已發布"}.get(status, status)
        super().__init__(f"語料裡已有這份檔案（{label}），不必重複上傳")
        self.file_hash = file_hash
        self.status = status  # hidden／draft／published
        self.upload_id = upload_id


class UploadInProgressError(IntakeError):
    def __init__(self, file_hash: str, upload_id: Optional[str] = None, state: Optional[str] = None):
        super().__init__("同一份檔案已有進行中的上傳")
        self.file_hash = file_hash
        self.upload_id = upload_id
        self.state = state


class KnownInfectedError(IntakeError):
    def __init__(self, file_hash: str):
        super().__init__("這份檔案先前已被判定含有惡意程式，拒絕上傳")
        self.file_hash = file_hash


class QuotaExceededError(IntakeError):
    def __init__(self, scope: str, limit: int, used: int):
        if scope == "daily":
            message = f"今天（台北時間）已上傳 {used} 份，已達每人每日上限 {limit} 份"
        else:
            message = f"全站處理中的上傳已有 {used} 份，已達上限 {limit} 份，請稍後再試"
        super().__init__(message)
        self.scope = scope  # daily／in_flight
        self.limit = limit
        self.used = used


# ── 檔名 ────────────────────────────────────────────────────────────────


def sanitize_filename(raw: str) -> str:
    """瀏覽器送來的檔名 → 存進 DB 的原始檔名（`parse_filename` 靠它推券商與日期，所以盡量保留原樣）。

    去掉路徑成分（`/` 與 `\\` 之前的全部）、控制與格式字元，NFC 正規化，副檔名強制小寫 `.pdf`，
    總長不超過 255 字元（太長時截主檔名、保留副檔名）。副檔名不是 .pdf 拋 `NotPdfFilenameError`；
    清完沒有主檔名拋 `InvalidFilenameError`。
    """
    name = unicodedata.normalize("NFC", raw or "")
    name = _PATH_SEP.split(name)[-1]
    name = "".join(ch for ch in name if unicodedata.category(ch) not in _STRIP_CATEGORIES)
    name = unicodedata.normalize("NFC", name).strip()
    if not name.lower().endswith(PDF_SUFFIX):
        raise NotPdfFilenameError("只接受副檔名為 .pdf 的檔案")
    stem = name[: -len(PDF_SUFFIX)].rstrip()
    if not stem.strip(" ."):
        raise InvalidFilenameError("檔名不合法")
    stem = unicodedata.normalize("NFC", stem[: FILENAME_MAX_CHARS - len(PDF_SUFFIX)]).rstrip()
    return stem + PDF_SUFFIX


def valid_upload_id(value) -> bool:
    try:
        uuid.UUID(str(value))
        return True
    except (ValueError, TypeError, AttributeError):
        return False


# ── 資料列 ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class UploadRow:
    upload_id: str
    file_hash: str
    original_name: str
    size_bytes: int
    client_mtime: Optional[datetime]
    uploaded_by: Optional[str]  # 帳號名；開發模式（NULL）或帳號查不到時 None
    uploaded_at: datetime
    state: str
    state_changed_at: datetime
    scan_attempts: int
    scan_engine: Optional[str]
    scan_signature: Optional[str]
    scanned_at: Optional[datetime]
    scan_last_error: Optional[str]
    process_attempts: int
    failure_kind: Optional[str]
    failure_detail: Optional[str]
    processed_at: Optional[datetime]
    decided_by: Optional[str]  # 帳號名
    decided_at: Optional[datetime]
    decision_reason: Optional[str]
    purge_after: Optional[datetime]
    purged_at: Optional[datetime]


@dataclass(frozen=True)
class UploadReport:
    """詳情頁用：語料裡同 file_hash 的研報（worker 入庫後才有）。"""

    report_id: str
    title: Optional[str]
    publication: str
    hidden: bool
    extractor: Optional[str]
    extraction_version: Optional[str]
    quality_score: Optional[float]
    page_count: Optional[int]
    pages_failed: Optional[list[int]]
    needs_review: bool
    created_at: Optional[datetime]


@dataclass(frozen=True)
class ScannerSummary:
    """掃描器狀態（由 DB 推導；web 不連 clamd）。"""

    pending: int  # quarantined：等掃描
    scanning: int
    oldest_pending_at: Optional[datetime]
    oldest_pending_seconds: Optional[int]
    last_error: Optional[str]  # 等待中的列裡最近一次的 scan_last_error（clamd 掛掉、病毒碼過舊…）
    last_error_at: Optional[datetime]


_COLS = (
    "u.id::text, u.file_hash, u.original_name, u.size_bytes, u.client_mtime, "
    "(SELECT a.username FROM research.app_user a WHERE a.id = u.uploaded_by), u.uploaded_at, "
    "u.state, u.state_changed_at, u.scan_attempts, u.scan_engine, u.scan_signature, u.scanned_at, "
    "u.scan_last_error, u.process_attempts, u.failure_kind, u.failure_detail, u.processed_at, "
    "(SELECT a.username FROM research.app_user a WHERE a.id = u.decided_by), u.decided_at, "
    "u.decision_reason, u.purge_after, u.purged_at"
)


def _row(r) -> UploadRow:
    return UploadRow(*r)


# ── 收檔 ────────────────────────────────────────────────────────────────


async def check_quota(
    session: AsyncSession, *, actor_id: Optional[str], daily_quota: int, max_in_flight: int,
) -> None:
    """超過配額拋 `QuotaExceededError`。收檔前先快查一次（不必收完整個檔才拒），INSERT 前在鎖內再查一次。"""
    row = (
        await session.execute(
            text(
                "SELECT "
                "count(*) FILTER (WHERE uploaded_by IS NOT DISTINCT FROM CAST(:uid AS uuid) "
                "  AND uploaded_at >= (date_trunc('day', now() AT TIME ZONE :tz) AT TIME ZONE :tz)), "
                "count(*) FILTER (WHERE state = ANY(CAST(:in_flight AS text[]))) "
                "FROM research.report_upload"
            ),
            {"uid": actor_id, "tz": TZ_NAME, "in_flight": list(uploads.IN_FLIGHT_STATES)},
        )
    ).one()
    today, in_flight = int(row[0]), int(row[1])
    if today >= daily_quota:
        raise QuotaExceededError("daily", daily_quota, today)
    if in_flight >= max_in_flight:
        raise QuotaExceededError("in_flight", max_in_flight, in_flight)


async def find_conflict(session: AsyncSession, file_hash: str) -> None:
    """同一個 file_hash 不能再收：語料已有（409）、進行中（409）、曾判感染（422）。依序檢查、拋第一個。"""
    active = (
        await session.execute(
            text(
                "SELECT id::text, state FROM research.report_upload "
                "WHERE file_hash = :h AND state = ANY(CAST(:active AS text[])) ORDER BY uploaded_at DESC LIMIT 1"
            ),
            {"h": file_hash, "active": list(uploads.ACTIVE_STATES)},
        )
    ).first()
    corpus = (
        await session.execute(
            text(
                "SELECT COALESCE(v.hidden, false), COALESCE(v.publication, 'published') "
                "FROM research.research_report r "
                "LEFT JOIN research.report_visibility v ON v.file_hash = r.file_hash "
                "WHERE r.file_hash = :h LIMIT 1"
            ),
            {"h": file_hash},
        )
    ).first()
    if corpus is not None:
        status = "draft" if corpus[1] != "published" else ("hidden" if corpus[0] else "published")
        raise DuplicateInCorpusError(file_hash, status, active[0] if active else None)
    if active is not None:
        raise UploadInProgressError(file_hash, active[0], active[1])
    infected = (
        await session.execute(
            text("SELECT 1 FROM research.report_upload WHERE file_hash = :h AND state = :s LIMIT 1"),
            {"h": file_hash, "s": uploads.STATE_INFECTED},
        )
    ).first()
    if infected is not None:
        raise KnownInfectedError(file_hash)


async def create_upload(
    session: AsyncSession,
    *,
    upload_id: str,
    file_hash: str,
    original_name: str,
    size_bytes: int,
    client_mtime: Optional[datetime],
    actor_id: Optional[str],
    daily_quota: int,
    max_in_flight: int,
) -> UploadRow:
    """檢查重複與配額、INSERT 一列 `quarantined`、寫稽核——全在呼叫端的交易裡。呼叫端負責 commit／rollback。"""
    # 函式內 import：與 visibility.py 同理，不讓這個模組在 import 期拉起帳號模組。
    from app.services.accounts import record_audit

    await session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": INTAKE_LOCK_KEY})
    await find_conflict(session, file_hash)
    await check_quota(session, actor_id=actor_id, daily_quota=daily_quota, max_in_flight=max_in_flight)
    try:
        row = (
            await session.execute(
                text(
                    "WITH ins AS ("
                    "  INSERT INTO research.report_upload "
                    "    (id, file_hash, original_name, size_bytes, client_mtime, uploaded_by, state) "
                    "  VALUES (CAST(:id AS uuid), :h, :name, :size, :mtime, CAST(:uid AS uuid), :state) "
                    "  RETURNING *"
                    f") SELECT {_COLS} FROM ins u"
                ),
                {
                    "id": upload_id, "h": file_hash, "name": original_name, "size": size_bytes,
                    "mtime": client_mtime, "uid": actor_id, "state": uploads.STATE_QUARANTINED,
                },
            )
        ).one()
    except IntegrityError as exc:
        if ACTIVE_INDEX in str(exc.orig):
            raise UploadInProgressError(file_hash) from exc
        raise
    await record_audit(
        session, actor_id=actor_id, action="upload.create", target_type="upload", target_id=upload_id,
        detail={
            "upload_id": upload_id, "file_name": original_name, "size_bytes": size_bytes,
            "state": uploads.STATE_QUARANTINED,
        },
    )
    return _row(row)


# ── 查詢 ────────────────────────────────────────────────────────────────


async def list_uploads(
    session: AsyncSession, *, state: Optional[str] = None, limit: int = 50, offset: int = 0,
) -> tuple[int, list[UploadRow]]:
    """上傳紀錄清單：可依狀態篩選；上傳新→舊，id 當決勝鍵讓翻頁穩定。"""
    params: dict = {}
    where = ""
    if state is not None:
        if state not in uploads.STATES:
            raise ValueError(f"list_uploads：不合法的 state {state!r}")
        where = "WHERE u.state = :state"
        params["state"] = state
    total = (
        await session.execute(text(f"SELECT count(*) FROM research.report_upload u {where}"), params)
    ).scalar_one()
    rows = (
        await session.execute(
            text(
                f"SELECT {_COLS} FROM research.report_upload u {where} "
                "ORDER BY u.uploaded_at DESC, u.id LIMIT :limit OFFSET :offset"
            ),
            {**params, "limit": limit, "offset": offset},
        )
    ).all()
    return int(total), [_row(r) for r in rows]


async def get_upload(session: AsyncSession, upload_id: str) -> UploadRow:
    if not valid_upload_id(upload_id):
        raise UploadNotFoundError("上傳紀錄不存在")
    row = (
        await session.execute(
            text(f"SELECT {_COLS} FROM research.report_upload u WHERE u.id = CAST(:id AS uuid)"),
            {"id": upload_id},
        )
    ).first()
    if row is None:
        raise UploadNotFoundError("上傳紀錄不存在")
    return _row(row)


async def get_upload_report(session: AsyncSession, file_hash: str) -> Optional[UploadReport]:
    """語料裡同 file_hash 的研報與抽取品質；還沒入庫（或已清除）回 None。管理面：不套可見性過濾。"""
    row = (
        await session.execute(
            text(
                "SELECT r.id::text, r.title, COALESCE(v.publication, 'published'), COALESCE(v.hidden, false), "
                "r.extractor, r.extraction_version, r.quality_score, r.page_count, r.pages_failed, "
                "r.needs_review, r.created_at "
                "FROM research.research_report r "
                "LEFT JOIN research.report_visibility v ON v.file_hash = r.file_hash "
                "WHERE r.file_hash = :h LIMIT 1"
            ),
            {"h": file_hash},
        )
    ).first()
    if row is None:
        return None
    return UploadReport(
        report_id=row[0], title=row[1], publication=row[2], hidden=bool(row[3]), extractor=row[4],
        extraction_version=row[5], quality_score=float(row[6]) if row[6] is not None else None,
        page_count=row[7], pages_failed=list(row[8]) if row[8] is not None else None,
        needs_review=bool(row[9]), created_at=row[10],
    )


async def scanner_summary(session: AsyncSession) -> ScannerSummary:
    """待掃件數、掃描中件數、最舊的等待、等待中的列最近一次掃描錯誤。全部由 DB 推導。"""
    counts = (
        await session.execute(
            text(
                "SELECT count(*) FILTER (WHERE state = :q), count(*) FILTER (WHERE state = :s), "
                "min(uploaded_at) FILTER (WHERE state = :q), "
                "EXTRACT(EPOCH FROM now() - min(uploaded_at) FILTER (WHERE state = :q))::bigint "
                "FROM research.report_upload WHERE state IN (:q, :s)"
            ),
            {"q": uploads.STATE_QUARANTINED, "s": uploads.STATE_SCANNING},
        )
    ).one()
    err = (
        await session.execute(
            text(
                "SELECT scan_last_error, state_changed_at FROM research.report_upload "
                "WHERE state = :q AND scan_last_error IS NOT NULL ORDER BY state_changed_at DESC LIMIT 1"
            ),
            {"q": uploads.STATE_QUARANTINED},
        )
    ).first()
    return ScannerSummary(
        pending=int(counts[0]), scanning=int(counts[1]), oldest_pending_at=counts[2],
        oldest_pending_seconds=int(counts[3]) if counts[3] is not None else None,
        last_error=err[0] if err else None, last_error_at=err[1] if err else None,
    )
