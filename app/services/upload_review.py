"""研報上傳的審核（`research.report_upload` ＋ `research.report_visibility`，revision 0008；Admin v1.5 上傳管線 PR-6）。

`web/routers/admin_uploads.py` 經 `deps.upload_review` 呼叫（測試的替換點）。收檔與查詢在
`app/services/upload_intake.py`，狀態詞彙在 `app/services/uploads.py`；本模組不做掃描、入庫與清除
（清除是 worker 的事，這裡只提供它要用的 SQL 守門 `corpus_purgeable_sql`／`list_purgeable`）。

狀態轉移（寫入函式都在**呼叫端的同一筆交易**裡完成狀態更新與稽核，呼叫端負責 commit；任何一步拋錯
呼叫端 rollback——稽核寫不進去，狀態也不變）：

- `publish`：只有 `draft`。條件式 UPDATE `report_upload ... WHERE state = 'draft'`，再條件式 UPDATE
  `report_visibility ... WHERE publication = 'draft' AND published_at IS NULL`（且語料真的有這份研報），
  兩者任一影響 0 列就拋錯讓呼叫端 rollback（409）。兩個交易同時發布同一筆：第二個在 report_upload 的
  列鎖上等，第一個 commit 後 `state = 'draft'` 不再成立 → 0 列 → 409。稽核 `upload.publish`。
- `reject`：`uploads.REJECTABLE_STATES`（draft、quarantined、clean、failed）；scanning／processing 是 worker
  正握著 → `UploadBusyError`；published 只能隱藏 → `UploadPublishedError`。草稿另加守門：語料裡同 hash 的
  研報必須仍是「草稿且從未發布」，否則日後清除會刪到已發布的研報。退回只改 report_upload（state、原因、
  決策人與時刻、`purge_after = now() + 寬限期`），**草稿的 visibility 列與研報原封不動**（仍不可見）。
  稽核 `upload.reject`：detail 不放原因全文，只放長度。
- `unreject`（設計決策 13）：只限 `rejected`、`purged_at IS NULL`、`now() < purge_after`。回到哪個狀態沒有
  欄位記，由事實推導（`restore_state_after_unreject`，純函式）。推導出 quarantined／clean 時也受全站處理中上限
  （與 retry 同一把 advisory lock、同一套計數）。稽核 `upload.unreject`。
- `retry`：只限 `failed` 且 `failure_kind` 屬 `uploads.RETRYABLE_FAILURE_KINDS` → `clean`；`process_attempts`
  不歸零、清 `failure_kind`／`failure_detail`。也受全站處理中上限（超過拋 `QuotaExceededError`，與收檔同一套
  計數與同一把 advisory lock）。稽核 `upload.retry`。

回到進行中狀態（unreject 推導出 draft／quarantined／clean、retry 轉 clean）會撞 partial unique index
`idx_report_upload_active_hash`：寬限期內有人重新上傳了同一份檔（只可能在語料還沒有它時），INSERT 端
的保證反過來保護這裡，unique violation 轉成 `ActiveUploadConflictError`（409）。

管理面**不套可見性過濾**（預覽與原檔要看的正是草稿）；這裡查語料表不是使用者讀取路徑。
bind 參數一律 `CAST(:x AS ...)`。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import uploads
from app.services.textnorm import clean_extracted
from app.services.upload_intake import (
    ACTIVE_INDEX,
    INTAKE_LOCK_KEY,
    QuotaExceededError,
    UploadNotFoundError,
    UploadRow,
    get_upload,
    valid_upload_id,
)

# 與 DB CHECK `char_length(decision_reason) <= 500` 一致（Python 的 len 也是數 code point）。
REASON_MAX_CHARS = 500
# 預覽回傳的正典文字上限（與閱讀頁 /text 的 READING_TEXT_MAX_CHARS 同值）；超過就截斷並標 truncated。
PREVIEW_TEXT_MAX_CHARS = 400_000

# 只有入庫完成（語料裡有研報）的狀態才有可預覽的內容與原檔。
PREVIEWABLE_STATES: tuple[str, ...] = (uploads.STATE_DRAFT, uploads.STATE_PUBLISHED)
# worker 正握著的狀態：不能退回（它下一步會覆寫狀態）。
BUSY_STATES: tuple[str, ...] = (uploads.STATE_SCANNING, uploads.STATE_PROCESSING)
# 不算失敗的 failure_kind（延後用，留在 clean）；unreject 推導時不據此判 failed。
DEFERRAL_FAILURE_KINDS: tuple[str, ...] = (uploads.FAILURE_LLM_BREAKER,)
# 摘錄可展示的 extraction_status（與閱讀頁 reading/queries.py 的 VALID_STATUSES 一致）。
_TAKEAWAY_VALID = ("valid", "partial")
_TAKEAWAY_DONE = ("valid", "partial", "rejected")
_HASH_EXPR = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?$")


# ── 錯誤 ────────────────────────────────────────────────────────────────


class ReviewError(Exception):
    """審核操作的可預期錯誤；訊息是給管理員看的中文。`code` 是 API 回應的穩定錯誤碼。"""

    code = "upload_state_conflict"

    def __init__(self, message: str, *, state: Optional[str] = None):
        super().__init__(message)
        self.state = state


class InvalidReasonError(ReviewError):
    code = "invalid_reason"


class UploadStateConflictError(ReviewError):
    code = "upload_state_conflict"


class UploadBusyError(ReviewError):
    code = "upload_busy"


class UploadPublishedError(ReviewError):
    code = "upload_published_use_hide"


class RejectExpiredError(ReviewError):
    code = "upload_reject_expired"


class NotRetryableError(ReviewError):
    code = "upload_not_retryable"

    def __init__(self, message: str, *, state: Optional[str] = None, failure_kind: Optional[str] = None):
        super().__init__(message, state=state)
        self.failure_kind = failure_kind


class ActiveUploadConflictError(ReviewError):
    code = "upload_active_conflict"


class ReportMissingError(ReviewError):
    """狀態是 draft／published，語料裡卻沒有同 hash 的研報（不一致；正常流程不會發生）。"""

    code = "upload_report_missing"


# ── 純函式 ──────────────────────────────────────────────────────────────


def normalize_reason(raw: Optional[str]) -> str:
    """退回原因：去頭尾空白後非空、不超過 500 字（與 DB CHECK 一致）。不合格拋 `InvalidReasonError`。"""
    note = (raw or "").strip()
    if not note:
        raise InvalidReasonError("退回必須填寫原因")
    if len(note) > REASON_MAX_CHARS:
        raise InvalidReasonError(f"原因最多 {REASON_MAX_CHARS} 字")
    return note


def restore_state_after_unreject(*, corpus_draft: bool, failure_kind: Optional[str], scanned: bool) -> str:
    """撤銷退回時要回到的狀態。report_upload 沒有欄位記「退回前的狀態」，依事實推導：

    1. 語料有這個 hash、且它的 visibility 是「草稿且從未發布」→ `draft`（只有 draft 會有語料）。
    2. `failure_kind` 是真的失敗（不是 `llm_breaker` 這種延後標記）→ `failed`。
    3. 掃描過（`scanned_at` 非空）→ `clean`（含被斷路器延後、帶 `llm_breaker` 的 clean）。
    4. 其餘 → `quarantined`（還沒掃描）。

    推導結果不一定與退回前逐字相同（例如退回前是 draft、寬限期內語料被別的流程刪掉，會回到 clean
    讓 worker 重新處理），但一定是一個 worker 能從那裡繼續往前走的狀態。
    """
    if corpus_draft:
        return uploads.STATE_DRAFT
    if failure_kind and failure_kind not in DEFERRAL_FAILURE_KINDS:
        return uploads.STATE_FAILED
    if scanned:
        return uploads.STATE_CLEAN
    return uploads.STATE_QUARANTINED


def _draft_marker_sql(hash_expr: str, alias: str) -> str:
    """「`hash_expr` 在 visibility 是草稿且從未發布」的 EXISTS 片段。"""
    return (
        f"EXISTS (SELECT 1 FROM research.report_visibility {alias} WHERE {alias}.file_hash = {hash_expr} "
        f"AND {alias}.publication = 'draft' AND {alias}.published_at IS NULL)"
    )


def corpus_never_published_sql(hash_expr: str) -> str:
    """「這個 hash 在語料裡沒有任何已發布的痕跡」的布林運算式（`hash_expr` 是 SQL 欄位運算式）。

    成立的條件：語料裡要嘛沒有這份研報、要嘛它的 visibility 是「草稿且 `published_at IS NULL`」；而且
    visibility 沒有任何一列是 `published` 或曾被發布過（NAS 進來的研報沒有 visibility 列＝已發布，
    被 `research_report` 那一支擋下）。退回草稿與清除都以它守門：已發布過的研報永遠只能隱藏。
    """
    if not _HASH_EXPR.match(hash_expr):
        raise ValueError(f"corpus_never_published_sql：不合法的欄位運算式 {hash_expr!r}")
    return (
        "(NOT EXISTS (SELECT 1 FROM research.research_report gr "
        f"WHERE gr.file_hash = {hash_expr} AND NOT {_draft_marker_sql('gr.file_hash', 'gv')}) "
        "AND NOT EXISTS (SELECT 1 FROM research.report_visibility gv2 "
        f"WHERE gv2.file_hash = {hash_expr} AND (gv2.publication <> 'draft' OR gv2.published_at IS NOT NULL)))"
    )


def corpus_purgeable_sql(hash_expr: str) -> str:
    """清除被退回的上傳時，「可以連語料一起刪」的守門（給上傳 worker 的清除步驟用）。

    = `corpus_never_published_sql`，再加上：同一個 hash 沒有別的上傳處於進行中或已發布（寬限期滿前
    有人重新上傳、已入庫成新的草稿時，語料屬於那一筆，不能跟著舊的退回件被刪）。
    不成立時清除只能刪這筆上傳自己的隔離區檔案，語料、visibility 列、快取、標籤、R2 物件一律不碰。
    """
    keep = list(uploads.ACTIVE_STATES) + [uploads.STATE_PUBLISHED]
    states = ", ".join(f"'{s}'" for s in keep)
    return (
        f"({corpus_never_published_sql(hash_expr)} AND NOT EXISTS (SELECT 1 FROM research.report_upload gu "
        f"WHERE gu.file_hash = {hash_expr} AND gu.state IN ({states})))"
    )


def _iso(v) -> Optional[str]:
    return v.isoformat() if hasattr(v, "isoformat") else (str(v) if v is not None else None)


# ── 預覽 ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PreviewTakeaway:
    ordinal: int
    claim: str
    quote: Optional[str]
    quote_start: Optional[int]  # 驗章不過或落在截斷範圍外時 None（與閱讀頁同一條規則）
    quote_end: Optional[int]
    anchor_method: Optional[str]


@dataclass(frozen=True)
class UploadPreview:
    upload: UploadRow
    report_id: str  # 讀取時以 file_hash JOIN 取得，不存
    file_name: str
    publication: str
    hidden: bool
    title: Optional[str]
    title_original: Optional[str]
    summary: Optional[str]
    market: Optional[str]
    is_research: bool
    confidence: Optional[float]
    source: Optional[str]
    report_date: Optional[date]
    report_type: Optional[str]
    language: Optional[str]
    stock_code: Optional[str]
    company_name: Optional[str]
    instrument_types: list[str]
    stock_targets: list[str]
    futures_targets: list[str]
    relates_stock: Optional[bool]
    relates_futures: Optional[bool]
    text: Optional[str]  # clean_extracted(full_text)，最多 PREVIEW_TEXT_MAX_CHARS 字；沒有全文時 None
    text_chars: int  # 完整正典文字的字數
    text_truncated: bool
    text_sha256: Optional[str]  # 完整正典文字的 sha256（與 report_takeaway.text_sha256 同算法）
    takeaways_state: str  # ready（有可展示的）／pending（還沒擷取）／none（擷取過但沒有可展示的）
    takeaways: list[PreviewTakeaway]


@dataclass(frozen=True)
class OriginalRef:
    """原檔 presign 需要的事實：物件鍵由呼叫端對 file_hash 與檔名重新驗證，不信任 DB 值。"""

    upload_id: str
    file_hash: str
    file_name: str
    object_key: Optional[str]


_REPORT_SQL = (
    "SELECT r.id::text, r.file_name, COALESCE(v.publication, 'published'), COALESCE(v.hidden, false), "
    "r.title, r.title_original, r.summary, r.market, r.is_research, r.confidence, r.source, r.report_date, "
    "r.report_type, r.language, r.stock_code, r.company_name, r.instrument_types, r.stock_targets, "
    "r.futures_targets, r.relates_stock, r.relates_futures, r.full_text, r.source_object_key "
    "FROM research.research_report r "
    "LEFT JOIN research.report_visibility v ON v.file_hash = r.file_hash "
    "WHERE r.file_hash = :h"
)


async def _previewable(session: AsyncSession, upload_id: str) -> UploadRow:
    row = await get_upload(session, upload_id)
    if row.state not in PREVIEWABLE_STATES:
        raise UploadStateConflictError("只有草稿或已發布的上傳可以預覽與取原檔", state=row.state)
    return row


def _takeaways(rows, text_sha256: Optional[str], visible_chars: int) -> tuple[str, list[PreviewTakeaway]]:
    statuses = {r[6] for r in rows}
    shown = [r for r in rows if r[6] in _TAKEAWAY_VALID]
    if shown:
        state = "ready"
    elif statuses and statuses <= set(_TAKEAWAY_DONE):
        state = "none"
    else:
        state = "pending"
    out = []
    for ordinal, claim, quote, qs, qe, method, _status, sha in shown:
        drop = text_sha256 is None or sha != text_sha256 or qe is None or qe > visible_chars
        out.append(PreviewTakeaway(
            ordinal=ordinal, claim=claim, quote=quote, quote_start=None if drop else qs,
            quote_end=None if drop else qe, anchor_method=None if drop else method,
        ))
    return state, out


async def get_preview(session: AsyncSession, upload_id: str) -> UploadPreview:
    """草稿或已發布上傳的預覽：正典文字、標籤、標題、摘要、摘錄。還沒產出的欄位是 None／pending。"""
    upload = await _previewable(session, upload_id)
    r = (await session.execute(text(_REPORT_SQL), {"h": upload.file_hash})).first()
    if r is None:
        raise ReportMissingError("語料中找不到這份上傳的研報", state=upload.state)
    canonical = clean_extracted(r[21]) if r[21] else ""
    sha = hashlib.sha256(canonical.encode("utf-8")).hexdigest() if canonical else None
    visible = min(len(canonical), PREVIEW_TEXT_MAX_CHARS)
    rows = (
        await session.execute(
            text(
                "SELECT ordinal, claim, quote, quote_start, quote_end, anchor_method, extraction_status, "
                "text_sha256 FROM research.report_takeaway WHERE report_id = CAST(:rid AS uuid) ORDER BY ordinal"
            ),
            {"rid": r[0]},
        )
    ).all()
    state, takeaways = _takeaways(rows, sha, visible)
    return UploadPreview(
        upload=upload, report_id=r[0], file_name=r[1], publication=r[2], hidden=bool(r[3]), title=r[4],
        title_original=r[5], summary=r[6], market=r[7], is_research=bool(r[8]),
        confidence=float(r[9]) if r[9] is not None else None, source=r[10], report_date=r[11],
        report_type=r[12], language=r[13], stock_code=r[14], company_name=r[15],
        instrument_types=list(r[16] or []), stock_targets=list(r[17] or []), futures_targets=list(r[18] or []),
        relates_stock=r[19], relates_futures=r[20], text=canonical[:visible] if canonical else None,
        text_chars=len(canonical), text_truncated=len(canonical) > visible, text_sha256=sha,
        takeaways_state=state, takeaways=takeaways,
    )


async def get_original(session: AsyncSession, upload_id: str) -> OriginalRef:
    """草稿或已發布上傳的原檔指標（語料的 `source_object_key`）。**永遠不看隔離區**：隔離區的檔案（含感染檔）
    沒有任何端點能取回；這裡只回語料裡、已經過掃描與入庫的那份。"""
    upload = await _previewable(session, upload_id)
    r = (await session.execute(text(_REPORT_SQL), {"h": upload.file_hash})).first()
    if r is None:
        raise ReportMissingError("語料中找不到這份上傳的研報", state=upload.state)
    return OriginalRef(upload_id=upload.upload_id, file_hash=upload.file_hash, file_name=r[1], object_key=r[22])


# ── 狀態轉移 ────────────────────────────────────────────────────────────


async def _current(session: AsyncSession, upload_id: str):
    """(state, failure_kind)；不存在拋 UploadNotFoundError。條件式 UPDATE 影響 0 列後用來決定錯誤。"""
    row = (
        await session.execute(
            text("SELECT state, failure_kind FROM research.report_upload WHERE id = CAST(:id AS uuid)"),
            {"id": upload_id},
        )
    ).first()
    if row is None:
        raise UploadNotFoundError("上傳紀錄不存在")
    return row[0], row[1]


def _require_id(upload_id: str) -> None:
    if not valid_upload_id(upload_id):
        raise UploadNotFoundError("上傳紀錄不存在")


def _active_conflict(exc: IntegrityError) -> bool:
    return ACTIVE_INDEX in str(getattr(exc, "orig", exc))


async def publish(session: AsyncSession, upload_id: str, *, actor_id: Optional[str]) -> UploadRow:
    """draft → published，並把語料的草稿標記改成已發布、寫稽核——全在呼叫端的交易裡（呼叫端 commit）。"""
    from app.services.accounts import record_audit

    _require_id(upload_id)
    up = (
        await session.execute(
            text(
                "UPDATE research.report_upload SET state = :published, decided_by = CAST(:actor AS uuid), "
                "decided_at = now(), state_changed_at = now() "
                "WHERE id = CAST(:id AS uuid) AND state = :draft "
                "RETURNING file_hash, original_name, uploaded_by::text"
            ),
            {"id": upload_id, "actor": actor_id, "published": uploads.STATE_PUBLISHED, "draft": uploads.STATE_DRAFT},
        )
    ).first()
    if up is None:
        state, _ = await _current(session, upload_id)
        if state == uploads.STATE_PUBLISHED:
            raise UploadStateConflictError("這份上傳已經發布", state=state)
        raise UploadStateConflictError("只有草稿可以發布", state=state)
    file_hash, file_name, uploaded_by = up
    vis = (
        await session.execute(
            text(
                "UPDATE research.report_visibility v SET publication = 'published', published_at = now(), "
                "published_by = CAST(:actor AS uuid) "
                "WHERE v.file_hash = :h AND v.publication = 'draft' AND v.published_at IS NULL "
                "AND EXISTS (SELECT 1 FROM research.research_report r WHERE r.file_hash = v.file_hash) "
                "RETURNING v.hidden"
            ),
            {"h": file_hash, "actor": actor_id},
        )
    ).first()
    if vis is None:  # 呼叫端 rollback：上面的 report_upload 更新一併撤銷
        raise UploadStateConflictError("語料中這份研報的草稿標記不一致，無法發布", state=uploads.STATE_DRAFT)
    await record_audit(
        session, actor_id=actor_id, action="upload.publish", target_type="upload", target_id=upload_id,
        detail={
            "upload_id": upload_id, "file_hash": file_hash, "file_name": file_name,
            "uploaded_by": uploaded_by, "published_by": actor_id,
            "self_published": uploaded_by is not None and uploaded_by == actor_id,
            "previous_state": uploads.STATE_DRAFT, "state": uploads.STATE_PUBLISHED, "hidden": bool(vis[0]),
        },
    )
    return await get_upload(session, upload_id)


async def reject(
    session: AsyncSession, upload_id: str, *, reason: Optional[str], actor_id: Optional[str], grace_hours: int,
) -> UploadRow:
    """退回（必填原因）：state → rejected、`purge_after = now() + grace_hours`、寫稽核。呼叫端 commit。

    語料、草稿的 visibility 列與檔案都不動（寬限期滿由 worker 清除）；寬限期內可 `unreject`。
    """
    from app.services.accounts import record_audit

    note = normalize_reason(reason)
    _require_id(upload_id)
    if grace_hours < 1:
        raise ValueError("grace_hours 必須 ≥ 1")
    row = (
        await session.execute(
            text(
                "WITH prev AS (SELECT id, state FROM research.report_upload WHERE id = CAST(:id AS uuid) FOR UPDATE) "
                "UPDATE research.report_upload u SET state = :rejected, decision_reason = :reason, "
                "decided_by = CAST(:actor AS uuid), decided_at = now(), state_changed_at = now(), "
                "purge_after = now() + make_interval(hours => :grace) "
                "FROM prev WHERE u.id = prev.id AND u.state = ANY(CAST(:rejectable AS text[])) "
                f"AND (u.state <> :draft OR {corpus_never_published_sql('u.file_hash')}) "
                "RETURNING prev.state, u.file_hash, u.original_name, u.purge_after"
            ),
            {
                "id": upload_id, "rejected": uploads.STATE_REJECTED, "reason": note, "actor": actor_id,
                "grace": int(grace_hours), "rejectable": list(uploads.REJECTABLE_STATES),
                "draft": uploads.STATE_DRAFT,
            },
        )
    ).first()
    if row is None:
        state, _ = await _current(session, upload_id)
        if state in BUSY_STATES:
            raise UploadBusyError("這份上傳正在掃描或處理中，請稍後再退回", state=state)
        if state == uploads.STATE_PUBLISHED:
            raise UploadPublishedError("已發布的研報不能退回，請改用研報管理的「隱藏」", state=state)
        if state == uploads.STATE_DRAFT:
            raise UploadStateConflictError("語料中這份研報已不是未發布的草稿，不能退回", state=state)
        raise UploadStateConflictError("這個狀態的上傳不能退回", state=state)
    previous, file_hash, file_name, purge_after = row
    await record_audit(
        session, actor_id=actor_id, action="upload.reject", target_type="upload", target_id=upload_id,
        detail={
            "upload_id": upload_id, "file_hash": file_hash, "file_name": file_name,
            "previous_state": previous, "state": uploads.STATE_REJECTED,
            "has_reason": True, "reason_chars": len(note), "purge_after": _iso(purge_after),
        },
    )
    return await get_upload(session, upload_id)


async def unreject(session: AsyncSession, upload_id: str, *, actor_id: Optional[str], max_in_flight: int) -> UploadRow:
    """寬限期內撤銷退回：回到由事實推導的狀態（`restore_state_after_unreject`），清掉原因、決策與清除時刻。

    推導出的狀態是處理中（quarantined／clean）時，與 `retry` 一樣受全站處理中上限：同一把 advisory lock、
    同一套計數，超過拋 `QuotaExceededError`（429，呼叫端 rollback）。回到 draft／failed 不佔產能，不檢查。
    """
    from app.services.accounts import record_audit

    _require_id(upload_id)
    await session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": INTAKE_LOCK_KEY})
    cur = (
        await session.execute(
            text(
                "SELECT u.state, u.purged_at IS NOT NULL, COALESCE(u.purge_after > now(), false), u.failure_kind, "
                "u.scanned_at IS NOT NULL, u.file_hash, u.original_name, u.purge_after, "
                "EXISTS (SELECT 1 FROM research.research_report r WHERE r.file_hash = u.file_hash "
                f"AND {_draft_marker_sql('r.file_hash', 'dv')}) "
                "FROM research.report_upload u WHERE u.id = CAST(:id AS uuid) FOR UPDATE"
            ),
            {"id": upload_id},
        )
    ).first()
    if cur is None:
        raise UploadNotFoundError("上傳紀錄不存在")
    state, purged, in_grace, failure_kind, scanned, file_hash, file_name, purge_after, corpus_draft = cur
    if state != uploads.STATE_REJECTED:
        raise UploadStateConflictError("只有已退回的上傳可以撤銷退回", state=state)
    if purged or not in_grace:
        raise RejectExpiredError("已超過退回的寬限期（或已清除），不能撤銷", state=state)
    target = restore_state_after_unreject(corpus_draft=bool(corpus_draft), failure_kind=failure_kind,
                                          scanned=bool(scanned))
    try:
        async with session.begin_nested():
            done = (
                await session.execute(
                    text(
                        "UPDATE research.report_upload SET state = :target, decision_reason = NULL, "
                        "decided_by = NULL, decided_at = NULL, purge_after = NULL, state_changed_at = now() "
                        "WHERE id = CAST(:id AS uuid) AND state = :rejected AND purged_at IS NULL "
                        "AND purge_after > now() RETURNING id"
                    ),
                    {"id": upload_id, "target": target, "rejected": uploads.STATE_REJECTED},
                )
            ).first()
    except IntegrityError as exc:
        if _active_conflict(exc):
            raise ActiveUploadConflictError(
                "同一份檔案已有另一筆進行中的上傳，不能撤銷退回", state=state,
            ) from exc
        raise
    if done is None:  # 上面已 FOR UPDATE 鎖住並檢查過，理論上不會到這裡
        raise RejectExpiredError("已超過退回的寬限期（或已清除），不能撤銷", state=state)
    if target in uploads.IN_FLIGHT_STATES:
        await _check_in_flight(session, max_in_flight)
    await record_audit(
        session, actor_id=actor_id, action="upload.unreject", target_type="upload", target_id=upload_id,
        detail={
            "upload_id": upload_id, "file_hash": file_hash, "file_name": file_name,
            "previous_state": uploads.STATE_REJECTED, "state": target, "purge_after_was": _iso(purge_after),
        },
    )
    return await get_upload(session, upload_id)


async def _check_in_flight(session: AsyncSession, max_in_flight: int) -> None:
    """剛轉回處理中狀態之後呼叫（呼叫端已持 `INTAKE_LOCK_KEY`）：全站處理中超過上限就拋 `QuotaExceededError`。
    計數含剛轉回的這一筆，所以 `used` 回報的是轉之前的件數（與收檔「已有 N 份」同一個意思）。"""
    in_flight = int((await session.execute(
        text("SELECT count(*) FROM research.report_upload WHERE state = ANY(CAST(:s AS text[]))"),
        {"s": list(uploads.IN_FLIGHT_STATES)},
    )).scalar_one())
    if in_flight > max_in_flight:
        raise QuotaExceededError("in_flight", max_in_flight, in_flight - 1)


async def retry(session: AsyncSession, upload_id: str, *, actor_id: Optional[str], max_in_flight: int) -> UploadRow:
    """failed（可重試類）→ clean：清 failure_kind／failure_detail，process_attempts 保留。寫稽核，呼叫端 commit。

    重試也受全站處理中上限（`UPLOAD_MAX_IN_FLIGHT`，與收檔同一套計數 `uploads.IN_FLIGHT_STATES`）：轉回 clean 之後
    處理中件數超過上限就拋 `QuotaExceededError`（429 `upload_quota_exceeded`，呼叫端 rollback）。與收檔共用同一把
    `pg_advisory_xact_lock`，兩邊同時在第 50 份時不會都過。
    """
    from app.services.accounts import record_audit

    _require_id(upload_id)
    await session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": INTAKE_LOCK_KEY})
    try:
        async with session.begin_nested():
            row = (
                await session.execute(
                    text(
                        "WITH prev AS (SELECT id, failure_kind FROM research.report_upload "
                        "  WHERE id = CAST(:id AS uuid) FOR UPDATE) "
                        "UPDATE research.report_upload u SET state = :clean, failure_kind = NULL, "
                        "failure_detail = NULL, state_changed_at = now() "
                        "FROM prev WHERE u.id = prev.id AND u.state = :failed "
                        "AND u.failure_kind = ANY(CAST(:retryable AS text[])) "
                        "RETURNING prev.failure_kind, u.file_hash, u.original_name, u.process_attempts"
                    ),
                    {
                        "id": upload_id, "clean": uploads.STATE_CLEAN, "failed": uploads.STATE_FAILED,
                        "retryable": list(uploads.RETRYABLE_FAILURE_KINDS),
                    },
                )
            ).first()
    except IntegrityError as exc:
        if _active_conflict(exc):
            raise ActiveUploadConflictError(
                "同一份檔案已有另一筆進行中的上傳，不能重試", state=uploads.STATE_FAILED,
            ) from exc
        raise
    if row is None:
        state, failure_kind = await _current(session, upload_id)
        if state != uploads.STATE_FAILED:
            raise UploadStateConflictError("只有處理失敗的上傳可以重試", state=state)
        raise NotRetryableError("這類失敗重跑也不會改變結果，不能重試", state=state, failure_kind=failure_kind)
    previous_kind, file_hash, file_name, attempts = row
    await _check_in_flight(session, max_in_flight)
    await record_audit(
        session, actor_id=actor_id, action="upload.retry", target_type="upload", target_id=upload_id,
        detail={
            "upload_id": upload_id, "file_hash": file_hash, "file_name": file_name,
            "previous_state": uploads.STATE_FAILED, "state": uploads.STATE_CLEAN,
            "failure_kind": previous_kind, "process_attempts": attempts,
        },
    )
    return await get_upload(session, upload_id)


# ── 清除的守門（給上傳 worker；本模組不刪任何東西）────────────────────────


@dataclass(frozen=True)
class PurgeCandidate:
    upload_id: str
    file_hash: str
    purge_after: datetime
    # True＝可以連語料一起刪（研報、visibility 列、抽取快取、標籤、本機乾淨檔、R2 原檔、extraction_log）；
    # False＝只能刪這筆上傳自己的隔離區檔案（語料已發布過，或屬於另一筆進行中／已發布的上傳）。
    corpus_purgeable: bool


async def list_purgeable(session: AsyncSession, *, limit: int = 50) -> list[PurgeCandidate]:
    """寬限期已過、尚未清除的退回件（最早到期的先），附 `corpus_purgeable` 守門結果。唯讀、不鎖列。

    worker 實際清除時要在**自己的交易**裡以條件式 UPDATE 認領（`WHERE state = 'rejected' AND purged_at IS NULL
    AND purge_after <= now()`），並在同一個交易的 DELETE 上再帶一次 `corpus_purgeable_sql`——這裡的結果只是
    候選清單，不是許可。
    """
    rows = (
        await session.execute(
            text(
                f"SELECT u.id::text, u.file_hash, u.purge_after, {corpus_purgeable_sql('u.file_hash')} "
                "FROM research.report_upload u "
                "WHERE u.state = :rejected AND u.purged_at IS NULL AND u.purge_after <= now() "
                "ORDER BY u.purge_after, u.id LIMIT :limit"
            ),
            {"rejected": uploads.STATE_REJECTED, "limit": limit},
        )
    ).all()
    return [PurgeCandidate(upload_id=r[0], file_hash=r[1], purge_after=r[2], corpus_purgeable=bool(r[3])) for r in rows]
