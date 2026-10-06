# web/routers/review.py
"""待複核佇列與人工處理紀錄（零 LLM）。

系統已經偵測到、也存在庫裡，但先前**看不到是哪幾筆**的三種東西：

- `faithfulness`：問答忠實度抽查分數低於門檻（`qa_log.evaluation`）。監控頁只有 30 天內的
  **筆數**（`/api/progress` 的 `evaluation.qa.below_min`），要知道是哪一題只能開 psql。
- `feedback`：使用者按了倒讚的回答（`qa_log.feedback`）。先前唯一的消費端是離線的
  `scripts/analyze_qa_log.py`。
- `extraction`：抽取品質被標成 `needs_review` 的研報（只標記不擋，照樣入庫可檢索）。
  監控頁同樣只有全庫聚合。

三條品質迴路的共同缺口是「偵測到了但沒有人看得到個體」，所以收進同一支端點、同一種分頁
形狀（與雷達目錄一致：`total／limit／offset／has_more／next_offset／items`）。

處理狀態寫入獨立的 `review_state`，原始品質訊號保持不變。整組端點限管理員
（`authz.require_admin`）：佇列會列出所有使用者的提問原文。每次處理記下處理人
（`review_state.reviewer_user_id`，回應的 `reviewer`）並寫一列 `admin_audit_log`；
問答類另帶提問者（`asked_by`）。兩者在個別帳號上線前的舊資料都是 None——那時是
共用帳號，無從辨識是誰。

刻意的範圍：

- `faithfulness` 與 `feedback` 只看有效列（`active`、非中止），並限制在 `days` 天內：
  門檻與窗期沿用監控頁那張卡的定義（`FAITHFULNESS_MIN`、30 天），兩邊的數字才對得起來。
- `faithfulness` 只列**現行 judge** 量出來的低分（`app/services/judge_schema.py` 的
  `CURRENT_JUDGE_SQL`，與監控卡同一條規則；缺 `judge_model` 的舊列視為 claude-haiku-4-5）。
  每一列另帶 `judge_model`（沒有 evaluation 的倒讚列為 None）。
- `extraction` 沒有窗期：`needs_review` 是研報的現況，不是事件。

輔助函式一律放在 `@router` 裝飾器之上（夾在裝飾器與 handler 之間會讓端點回 422）。
"""
from __future__ import annotations

import logging
import time
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.config import get_settings
from app.services.accounts import User, record_audit
from app.services.filename import source_display
from app.services.judge_schema import CURRENT_JUDGE_SQL, JUDGE_MODEL_SQL
from app.services.store import review_reasons
from web import authz, deps

logger = logging.getLogger(__name__)

# 整組限管理員：待複核佇列會列出所有人的提問原文，一般使用者不該看得到別人問了什麼。
router = APIRouter(dependencies=[Depends(authz.require_admin), Depends(authz.require_scope("review.manage"))])

ReviewKind = Literal["faithfulness", "feedback", "extraction"]
ReviewStatus = Literal["open", "resolved", "dismissed"]
Verification = Literal["untested", "passed", "failed"]

_SETTINGS = get_settings()
_FAITHFULNESS_MIN = _SETTINGS.faithfulness_min
_JUDGE_MODEL = _SETTINGS.faithfulness_model

# jsonb 一律先以 jsonb_typeof 過濾再 cast：一列畸形的 evaluation 不該讓整支端點 500
# （與 web/routers/monitor.py 的同一段理由相同）。
_SCORE = (
    "CASE WHEN jsonb_typeof(evaluation->'faithfulness_score') = 'number' "
    "THEN (evaluation->>'faithfulness_score')::float END"
)
_QA_VALID = "active AND stopped IS NOT TRUE AND created_at > now() - make_interval(days => :days)"
# 該列 evaluation 的 judge；沒有 evaluation 就是 NULL（JUDGE_MODEL_SQL 本身會把 NULL 補成舊預設）。
_ROW_JUDGE = f"CASE WHEN evaluation IS NULL THEN NULL ELSE {JUDGE_MODEL_SQL} END"
# 帳號名稱用純量子查詢而不是 JOIN：app_user 也有 id／created_at，JOIN 進來會讓下面那些
# 沒加表名的欄位（id、created_at、feedback 的 ORDER BY）變成 ambiguous。
_REVIEWER = "(SELECT u.username FROM research.app_user u WHERE u.id = rv.reviewer_user_id)"
_ASKED_BY = "(SELECT u.username FROM research.app_user u WHERE u.id = qa_log.user_id)"


class ReviewItem(BaseModel):
    """一筆待複核項目。依 `kind` 只有其中一組欄位有值，其餘為 None。"""

    # qa（faithfulness／feedback）
    qa_id: str | None = None
    conversation_id: str | None = None
    question: str | None = None
    created_at: str | None = None
    faithfulness_score: float | None = None
    feedback: str | None = None
    # extraction
    report_id: str | None = None
    file_hash: str | None = None
    file_name: str | None = None
    title: str | None = None
    source: str | None = None
    report_date: str | None = None
    quality_score: float | None = None
    quality_flags: dict | None = None
    pages_failed: list[int] | None = None
    # qa：這筆 evaluation 是哪個 judge 量的（缺鍵的舊列＝claude-haiku-4-5；沒有 evaluation＝None）
    judge_model: str | None = None
    # extraction：為什麼被標成 needs_review（`store.REVIEW_REASONS` 的封閉詞彙，可多個）。以**現行門檻**
    # 重算：入庫之後調過門檻的話，可能與當初被標記的原因不同，甚至是空的——那代表這一篇
    # 以現在的標準已經不必看了，重跑該篇回填就會解除標記。
    review_reasons: list[str] | None = None
    review_status: ReviewStatus = "open"
    review_note: str = ""
    verification: Verification = "untested"
    reviewed_at: str | None = None
    # 最後一次處理的人；None＝沒人處理過，或是個別帳號上線前（共用帳號時期）處理的。
    reviewer: str | None = None
    # qa：提問者帳號；None＝個別帳號上線前的共用歷史（或免登入開發模式寫入的列）。
    asked_by: str | None = None


class ReviewQueueResponse(BaseModel):
    kind: ReviewKind
    total: int
    limit: int
    offset: int
    has_more: bool
    next_offset: int | None
    # faithfulness 的門檻；其他 kind 為 None。讓畫面說得出「低於多少」而不必另外查設定。
    min_score: float | None = None
    items: list[ReviewItem]


class ReviewUpdate(BaseModel):
    status: ReviewStatus
    note: str = Field(default="", max_length=1000)
    verification: Verification = "untested"


class ReviewStateResponse(ReviewUpdate):
    kind: ReviewKind
    subject_id: UUID
    updated_at: str
    reviewer: str | None = None


def _iso(v) -> str | None:
    return v.isoformat() if hasattr(v, "isoformat") else (str(v) if v is not None else None)


def _qa_item(row) -> ReviewItem:
    (qa_id, conv_id, question, created_at, score, feedback, judge_model, status, note, verification,
     reviewed_at, reviewer, asked_by) = row
    return ReviewItem(
        qa_id=str(qa_id), conversation_id=str(conv_id), question=question,
        created_at=_iso(created_at),
        faithfulness_score=float(score) if score is not None else None,
        feedback=feedback,
        judge_model=judge_model,
        review_status=status or "open", review_note=note or "",
        verification=verification or "untested", reviewed_at=_iso(reviewed_at),
        reviewer=reviewer, asked_by=asked_by,
    )


def _extraction_item(row) -> ReviewItem:
    (rid, fhash, fname, title, src, rdate, qscore, qflags, pfailed, status, note, verification,
     reviewed_at, reviewer) = row
    flags = qflags if isinstance(qflags, dict) else None
    reasons = review_reasons(
        float(qscore) if qscore is not None else None, list(pfailed) if pfailed else None,
        _SETTINGS.extraction_review_min, flags,
        min_coverage=_SETTINGS.extraction_review_min_coverage,
        max_garbled=_SETTINGS.extraction_review_max_garbled,
    )
    return ReviewItem(
        review_reasons=reasons,
        report_id=str(rid), file_hash=fhash, file_name=fname, title=title,
        source=source_display(src), report_date=_iso(rdate),
        quality_score=float(qscore) if qscore is not None else None,
        quality_flags=flags,
        pages_failed=list(pfailed) if pfailed else None,
        review_status=status or "open", review_note=note or "",
        verification=verification or "untested", reviewed_at=_iso(reviewed_at),
        reviewer=reviewer,
    )


async def _fetch(session, kind: str, *, limit: int, offset: int, days: int, status: str = "open"):
    """回 (total, items)。每個 kind 兩條查詢：count 與當頁。"""
    page = {"limit": limit, "offset": offset}
    state_filter = "" if status == "all" else " AND COALESCE(rv.status, 'open') = :review_status"
    state_params = {} if status == "all" else {"review_status": status}
    state_cols = f"rv.status, rv.note, rv.verification, rv.updated_at, {_REVIEWER}"
    if kind == "extraction":
        total = (await session.execute(
            text("SELECT count(*) FROM research.research_report rr "
                 "LEFT JOIN research.review_state rv ON rv.kind = 'extraction' AND rv.subject_id = rr.id "
                 f"WHERE rr.needs_review{state_filter}"), state_params,
        )).scalar_one()
        rows = (await session.execute(
            text(
                "SELECT rr.id, rr.file_hash, rr.file_name, rr.title, rr.source, rr.report_date, "
                f"rr.quality_score, rr.quality_flags, rr.pages_failed, {state_cols} "
                "FROM research.research_report rr "
                "LEFT JOIN research.review_state rv ON rv.kind = 'extraction' AND rv.subject_id = rr.id "
                f"WHERE rr.needs_review{state_filter} "
                # 分數低的在前（NULL＝算不出分數，排最後）；id 當決勝鍵讓翻頁穩定。
                "ORDER BY rr.quality_score ASC NULLS LAST, rr.id LIMIT :limit OFFSET :offset"
            ),
            {**page, **state_params},
        )).all()
        return int(total), [_extraction_item(tuple(r)) for r in rows]

    if kind == "faithfulness":
        where = f"{_QA_VALID} AND ({_SCORE}) < :fmin AND {CURRENT_JUDGE_SQL}"
        order = f"({_SCORE}) ASC, created_at DESC, id"
        params = {"days": days, "fmin": _FAITHFULNESS_MIN, "judge_model": _JUDGE_MODEL}
    else:  # feedback
        where = f"{_QA_VALID} AND feedback = 'dislike'"
        order = "created_at DESC, id"
        params = {"days": days}
    join = f"LEFT JOIN research.review_state rv ON rv.kind = '{kind}' AND rv.subject_id = qa_log.id"
    total = (await session.execute(
        text(f"SELECT count(*) FROM research.qa_log {join} WHERE {where}{state_filter}"),
        {**params, **state_params},
    )).scalar_one()
    rows = (await session.execute(
        text(
            f"SELECT id, COALESCE(conversation_id, id), question, created_at, ({_SCORE}), feedback, "
            f"({_ROW_JUDGE}), {state_cols}, {_ASKED_BY} "
            f"FROM research.qa_log {join} WHERE {where}{state_filter} "
            f"ORDER BY {order} LIMIT :limit OFFSET :offset"
        ),
        {**params, **page, **state_params},
    )).all()
    return int(total), [_qa_item(tuple(r)) for r in rows]


@router.get("/api/review/queue", response_model=ReviewQueueResponse)
async def review_queue(
    kind: ReviewKind = Query(...),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    days: int = Query(30, ge=1, le=365),
    status: Literal["open", "resolved", "dismissed", "all"] = Query("open"),
):
    """待複核佇列的一頁。`days` 只作用於 faithfulness／feedback。"""
    t0 = time.monotonic()
    async with deps.SessionFactory() as session:
        total, items = await _fetch(session, kind, limit=limit, offset=offset, days=days, status=status)
    next_offset = offset + len(items)
    has_more = next_offset < total
    logger.info(
        "review queue kind=%s total=%d offset=%d limit=%d elapsed_ms=%.1f",
        kind, total, offset, limit, (time.monotonic() - t0) * 1000,
    )
    return ReviewQueueResponse(
        kind=kind, total=total, limit=limit, offset=offset, has_more=has_more,
        next_offset=next_offset if has_more else None,
        min_score=_FAITHFULNESS_MIN if kind == "faithfulness" else None,
        items=items,
    )


@router.put("/api/review/{kind}/{subject_id}", response_model=ReviewStateResponse)
async def update_review(
    kind: ReviewKind, subject_id: UUID, body: ReviewUpdate, user: User = Depends(authz.current_user),
):
    """記錄人工處理結果；verification 是人工確認，不會偷偷重跑評測或抽取。

    處理人與稽核和處理狀態同一筆交易寫入。稽核只記狀態與驗證結果，不記註記全文
    （註記本身就在 review_state，稽核留的是「誰在何時改成什麼」）。
    """
    table = "research.research_report" if kind == "extraction" else "research.qa_log"
    note = body.note.strip()
    async with deps.SessionFactory() as session:
        exists = (await session.execute(
            text(f"SELECT EXISTS (SELECT 1 FROM {table} WHERE id = :id)"), {"id": subject_id},
        )).scalar_one()
        if not exists:
            raise HTTPException(status_code=404, detail="待複核項目不存在")
        row = (await session.execute(text(
            "INSERT INTO research.review_state (kind, subject_id, status, note, verification, reviewer_user_id) "
            "VALUES (:kind, :id, :status, :note, :verification, :reviewer) "
            "ON CONFLICT (kind, subject_id) DO UPDATE SET status = EXCLUDED.status, "
            "note = EXCLUDED.note, verification = EXCLUDED.verification, "
            "reviewer_user_id = EXCLUDED.reviewer_user_id, updated_at = now() "
            "RETURNING updated_at"
        ), {
            "kind": kind, "id": subject_id, "status": body.status,
            "note": note, "verification": body.verification, "reviewer": user.id,
        })).scalar_one()
        await record_audit(
            session, actor_id=user.id, action="review.update", target_type="review",
            target_id=str(subject_id),
            detail={"kind": kind, "status": body.status, "verification": body.verification,
                    "has_note": bool(note)},
        )
        await session.commit()
    return ReviewStateResponse(
        kind=kind, subject_id=subject_id, status=body.status,
        note=note, verification=body.verification, updated_at=_iso(row),
        reviewer=user.username,
    )
