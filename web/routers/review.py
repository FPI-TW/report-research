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
（`authz.require_admin`＋`review.manage`）。每次處理記下處理人（`review_state.reviewer_user_id`，
回應的 `reviewer`）並寫一列 `admin_audit_log`；個別帳號上線前的舊資料是 None。

問答類**刻意去內容化**：佇列不回提問原文、回答與提問者帳號名，只回中繼資料（qa_id、時間、
分數、feedback、judge、處理狀態、處理人）與提問者代號 `asker_code`——以 session secret 做
HMAC 的短代號（`web.auth.pseudonym`），同一人同一代號、看不出是誰；NULL 擁有者（共用帳號
時期）為 None。佇列給的是「哪幾筆要看」，不是「誰問了什麼」。也不回 `conversation_id`：
對話串只有擁有者打得開，管理面不提供瀏覽某人整串歷史的入口。

需要看原文時走 `POST /api/review/qa/{qa_id}/access`（另要 `qa_content.read`，只授予品質審核／
指定 QA）：一次只回**這一筆**的 question 與 answer，而且只限**目前在佇列裡**的項目——低忠實度
（與佇列同一套條件：現行 judge、門檻、佇列預設窗期）或倒讚；其餘 qa_id 一律 404，不洩漏存在與否，
免得拿 id 掃某人的歷史。刻意用 POST：每次讀取都在同一筆交易寫一列 `admin_audit_log`
（`qa_content.read`，detail 只有 qa_id 與 kinds、絕不記內容），GET 的 safe／cache／prefetch
語意不適合有稽核副作用的讀取。稽核寫入或 commit 失敗就不回內容。

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

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.config import get_settings
from app.services.accounts import User, record_audit
from app.services.filename import source_display
from app.services.judge_schema import CURRENT_JUDGE_SQL, JUDGE_MODEL_SQL
from app.services.store import review_reasons
from web import auth, authz, deps

logger = logging.getLogger(__name__)

# 整組限管理員。佇列本身不含提問原文（見模組 docstring）；處理狀態仍是管理資料。
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
# 兩種問答 kind 的入列條件。佇列（_fetch）與逐筆讀取（read_qa_content）共用同一段字串，
# 「能讀的」才會恰好等於「佇列裡看得到的」。faithfulness 另需綁 :fmin 與 :judge_model。
_FAITHFULNESS_COND = f"({_SCORE}) < :fmin AND {CURRENT_JUDGE_SQL}"
_FEEDBACK_COND = "feedback = 'dislike'"
# 佇列的預設窗期，也是逐筆讀取的窗期（監控卡同值）。逐筆讀取刻意不收 days：讓呼叫端放寬到
# 一年，等於把可讀範圍放寬到一年。
_DEFAULT_DAYS = 30
# 該列 evaluation 的 judge；沒有 evaluation 就是 NULL（JUDGE_MODEL_SQL 本身會把 NULL 補成舊預設）。
_ROW_JUDGE = f"CASE WHEN evaluation IS NULL THEN NULL ELSE {JUDGE_MODEL_SQL} END"
# 帳號名稱用純量子查詢而不是 JOIN：app_user 也有 id／created_at，JOIN 進來會讓下面那些
# 沒加表名的欄位（id、created_at、feedback 的 ORDER BY）變成 ambiguous。
_REVIEWER = "(SELECT u.username FROM research.app_user u WHERE u.id = rv.reviewer_user_id)"
# 提問者代號的 namespace：改了代號會全部換掉（等同換 secret），不要隨手改。
_ASKER_NAMESPACE = "review.asker"


def _asker_code(user_id) -> str | None:
    """提問者 user id → 不可逆短代號；NULL（共用帳號時期、免登入開發模式）→ None。"""
    return auth.pseudonym(_ASKER_NAMESPACE, str(user_id)) if user_id is not None else None


class ReviewItem(BaseModel):
    """一筆待複核項目。依 `kind` 只有其中一組欄位有值，其餘為 None。"""

    # qa（faithfulness／feedback）：只有中繼資料，沒有提問原文（模組 docstring）
    qa_id: str | None = None
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
    # qa：提問者代號（不可逆，同一人同一代號）；None＝個別帳號上線前的共用歷史（或免登入開發模式寫入的列）。
    asker_code: str | None = None


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


QaReviewKind = Literal["faithfulness", "feedback"]


class QaContentResponse(BaseModel):
    """逐筆讀取的一筆問答原文。只有這一筆，不含對話串的其他輪。"""

    qa_id: str
    # 這一筆目前因為哪些原因在佇列裡（可能兩者皆是）。
    kinds: list[QaReviewKind]
    created_at: str | None = None
    question: str
    answer: str | None = None


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
    (qa_id, created_at, score, feedback, judge_model, status, note, verification,
     reviewed_at, reviewer, asker_id) = row
    return ReviewItem(
        qa_id=str(qa_id),
        created_at=_iso(created_at),
        faithfulness_score=float(score) if score is not None else None,
        feedback=feedback,
        judge_model=judge_model,
        review_status=status or "open", review_note=note or "",
        verification=verification or "untested", reviewed_at=_iso(reviewed_at),
        reviewer=reviewer, asker_code=_asker_code(asker_id),
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
        where = f"{_QA_VALID} AND {_FAITHFULNESS_COND}"
        order = f"({_SCORE}) ASC, created_at DESC, id"
        params = {"days": days, "fmin": _FAITHFULNESS_MIN, "judge_model": _JUDGE_MODEL}
    else:  # feedback
        where = f"{_QA_VALID} AND {_FEEDBACK_COND}"
        order = "created_at DESC, id"
        params = {"days": days}
    join = f"LEFT JOIN research.review_state rv ON rv.kind = '{kind}' AND rv.subject_id = qa_log.id"
    total = (await session.execute(
        text(f"SELECT count(*) FROM research.qa_log {join} WHERE {where}{state_filter}"),
        {**params, **state_params},
    )).scalar_one()
    rows = (await session.execute(
        text(
            # 刻意不選 question／answer：佇列只回中繼資料。
            f"SELECT id, created_at, ({_SCORE}), feedback, "
            f"({_ROW_JUDGE}), {state_cols}, qa_log.user_id "
            f"FROM research.qa_log {join} WHERE {where}{state_filter} "
            f"ORDER BY {order} LIMIT :limit OFFSET :offset"
        ),
        {**params, **page, **state_params},
    )).all()
    return int(total), [_qa_item(tuple(r)) for r in rows]


# 逐筆讀取：id 相符、而且此刻落在任一種問答佇列裡。兩個旗標說明是因為哪一種（NULL 分數→不是低分）。
_QA_ACCESS_SQL = (
    "SELECT question, answer, created_at, "
    f"COALESCE(({_FAITHFULNESS_COND}), false), COALESCE(({_FEEDBACK_COND}), false) "
    "FROM research.qa_log "
    f"WHERE id = :id AND {_QA_VALID} AND (({_FAITHFULNESS_COND}) OR ({_FEEDBACK_COND}))"
)


async def _qa_content_in_queue(session, qa_id: UUID):
    """回 (question, answer, created_at, kinds)；不在佇列（含不存在）回 None。"""
    row = (await session.execute(text(_QA_ACCESS_SQL), {
        "id": qa_id, "days": _DEFAULT_DAYS, "fmin": _FAITHFULNESS_MIN, "judge_model": _JUDGE_MODEL,
    })).first()
    if row is None:
        return None
    question, answer, created_at, low, disliked = tuple(row)
    kinds = [k for k, hit in (("faithfulness", low), ("feedback", disliked)) if hit]
    return question, answer, created_at, kinds


@router.get("/api/review/queue", response_model=ReviewQueueResponse)
async def review_queue(
    kind: ReviewKind = Query(...),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    days: int = Query(_DEFAULT_DAYS, ge=1, le=365),
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


@router.post(
    "/api/review/qa/{qa_id}/access", response_model=QaContentResponse,
    dependencies=[Depends(authz.require_scope("qa_content.read"))],
)
async def read_qa_content(qa_id: UUID, response: Response, user: User = Depends(authz.current_user)):
    """讀一筆在佇列裡的問答原文（question＋answer），並在同一筆交易寫稽核。

    不在佇列（不存在、窗期外、分數已不低、不是倒讚、已停用或中止）一律 404，訊息相同。
    稽核 detail 只有 qa_id 與 kinds；內容一個字都不進稽核，也不進日誌。
    """
    async with deps.SessionFactory() as session:
        found = await _qa_content_in_queue(session, qa_id)
        if found is None:
            raise HTTPException(status_code=404, detail="待複核項目不存在")
        question, answer, created_at, kinds = found
        await record_audit(
            session, actor_id=user.id, action="qa_content.read", target_type="qa",
            target_id=str(qa_id), detail={"qa_id": str(qa_id), "kinds": kinds},
        )
        # commit 成功才回內容：稽核沒落庫就等於沒讀過。
        await session.commit()
    logger.info("review qa content read qa_id=%s kinds=%s", qa_id, ",".join(kinds))
    response.headers["Cache-Control"] = "no-store"
    return QaContentResponse(
        qa_id=str(qa_id), kinds=kinds, created_at=_iso(created_at), question=question or "", answer=answer,
    )
