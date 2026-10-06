"""管理後台的研報治理（/api/admin/reports*）：查研報、隱藏／恢復。整組限管理員＋`reports.manage`。

v1 只做 review＋hide／restore（草稿／發布、metadata 覆寫、上傳不在這裡）。規則與 SQL 在
`app/services/visibility.py`（`list_reports`／`set_visibility`），這裡只做 HTTP 轉換；呼叫一律經
`deps.report_visibility`（測試的替換點）。

- 隱藏以 `file_hash` 為鍵：重新入庫換了 report_id 也不會失效。
- 隱藏與恢復都在**同一筆交易**寫 `admin_audit_log`（`report.hide`／`report.restore`），detail 不含
  原因全文與研報內文。
- 被隱藏的研報對所有使用者路徑（含管理員自己走一般頁面時）都像不存在：閱讀頁與原檔 presign 回 404。
- 草稿（上傳後尚未發布，`publication='draft'`）同樣不可見；對草稿隱藏或恢復回 409 `report_is_draft`
  （恢復不可順手發布）。

輔助函式一律放在 `@router` 裝飾器之上（夾在裝飾器與 handler 之間會讓端點回 422）。
"""
from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.services.accounts import User
from app.services.visibility import InvalidReasonError, ReportIsDraftError, ReportNotFoundError, VisibilityError
from web import authz, deps
from web.errors import AppError

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(authz.require_admin)])

_REPORTS = Depends(authz.require_scope("reports.manage"))


class AdminReportItem(BaseModel):
    report_id: str
    file_hash: str
    file_name: str
    title: str | None = None
    source: str | None = None
    market: str | None = None
    report_date: str | None = None
    created_at: str | None = None
    hidden: bool = False
    hidden_reason: str | None = None
    visibility_updated_by: str | None = None
    visibility_updated_at: str | None = None
    # 發布狀態：上傳後尚未發布的草稿是 draft（對所有使用者路徑不可見）；sync 進來的研報一律 published。
    publication: Literal["draft", "published"] = "published"


class AdminReportListResponse(BaseModel):
    total: int
    limit: int
    offset: int
    has_more: bool
    next_offset: int | None
    items: list[AdminReportItem]


class ReportVisibilityRequest(BaseModel):
    hidden: bool
    # 長度上限只為擋巨大 payload；實際規則（隱藏必填、最多 500 字）由服務層判，回 400 與中文原因。
    reason: str | None = Field(None, max_length=2000)


class ReportVisibilityResponse(BaseModel):
    file_hash: str
    hidden: bool
    reason: str | None = None
    updated_by: str | None = None
    updated_at: str | None = None


# ── 輔助函式一律放在所有 @router.* 裝飾器之上 ──────────────────────────────


def _iso(v) -> str | None:
    return v.isoformat() if hasattr(v, "isoformat") else (str(v) if v is not None else None)


def _http_error(exc: VisibilityError) -> AppError:
    if isinstance(exc, ReportNotFoundError):
        return AppError(404, "not_found", str(exc))
    if isinstance(exc, InvalidReasonError):
        return AppError(400, "invalid_input", str(exc))
    if isinstance(exc, ReportIsDraftError):
        return AppError(409, "report_is_draft", str(exc))
    return AppError(400, "bad_request", str(exc))


@router.get("/api/admin/reports", response_model=AdminReportListResponse, dependencies=[_REPORTS])
async def list_reports(
    q: str | None = Query(None, max_length=200),
    hidden: bool | None = Query(None),
    publication: Literal["draft", "published"] | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """依標題／檔名／券商關鍵字（`q`）、是否隱藏（`hidden`）與發布狀態（`publication`）查研報；入庫新→舊。"""
    async with deps.SessionFactory() as session:
        total, rows = await deps.report_visibility.list_reports(
            session, q=q, hidden=hidden, publication=publication, limit=limit, offset=offset,
        )
    next_offset = offset + len(rows)
    has_more = next_offset < total
    return AdminReportListResponse(
        total=total, limit=limit, offset=offset, has_more=has_more,
        next_offset=next_offset if has_more else None,
        items=[
            AdminReportItem(
                report_id=r.report_id, file_hash=r.file_hash, file_name=r.file_name, title=r.title,
                source=r.source, market=r.market, report_date=_iso(r.report_date), created_at=_iso(r.created_at),
                hidden=r.hidden, hidden_reason=r.reason, visibility_updated_by=r.updated_by,
                visibility_updated_at=_iso(r.updated_at), publication=r.publication,
            )
            for r in rows
        ],
    )


@router.put(
    "/api/admin/reports/{file_hash}/visibility", response_model=ReportVisibilityResponse, dependencies=[_REPORTS],
)
async def set_report_visibility(
    file_hash: str, body: ReportVisibilityRequest, actor: User = Depends(authz.current_user),
):
    """隱藏（`hidden=true`，必填 `reason`）或恢復（`hidden=false`）一份研報；稽核同交易寫入。

    草稿（尚未發布的上傳研報）兩者都回 409 `report_is_draft`：發布與退回只走上傳審核。
    """
    async with deps.SessionFactory() as session:
        try:
            state = await deps.report_visibility.set_visibility(
                session, file_hash, hidden=body.hidden, reason=body.reason, actor_id=actor.id,
            )
        except VisibilityError as exc:
            await session.rollback()
            raise _http_error(exc) from exc
        await session.commit()
    logger.info(
        "管理操作 actor=%s action=%s target=%s", actor.username,
        "report.hide" if state.hidden else "report.restore", file_hash,
    )
    return ReportVisibilityResponse(
        file_hash=state.file_hash, hidden=state.hidden, reason=state.reason,
        updated_by=state.updated_by, updated_at=_iso(state.updated_at),
    )
