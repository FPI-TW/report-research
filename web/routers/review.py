# web/routers/review.py
"""待複核佇列：`GET /api/review/queue`（唯讀、零 LLM）。

系統已經偵測到、也存在庫裡，但先前**看不到是哪幾筆**的三種東西：

- `faithfulness`：問答忠實度抽查分數低於門檻（`qa_log.evaluation`）。監控頁只有 30 天內的
  **筆數**（`/api/progress` 的 `evaluation.qa.below_min`），要知道是哪一題只能開 psql。
- `feedback`：使用者按了倒讚的回答（`qa_log.feedback`）。先前唯一的消費端是離線的
  `scripts/analyze_qa_log.py`。
- `extraction`：抽取品質被標成 `needs_review` 的研報（只標記不擋，照樣入庫可檢索）。
  監控頁同樣只有全庫聚合。

三條品質迴路的共同缺口是「偵測到了但沒有人看得到個體」，所以收進同一支端點、同一種分頁
形狀（與雷達目錄一致：`total／limit／offset／has_more／next_offset／items`）。

刻意的範圍：

- **只讀**。這裡不提供「標記為已處理」——那需要新的狀態欄位與誰處理的歸因，而本站是
  共用帳號；先讓東西看得見。低分的回答處理方式是重問或回報，抽取問題是重跑該篇回填。
- `faithfulness` 與 `feedback` 只看有效列（`active`、非中止），並限制在 `days` 天內：
  門檻與窗期沿用監控頁那張卡的定義（`FAITHFULNESS_MIN`、30 天），兩邊的數字才對得起來。
- `extraction` 沒有窗期：`needs_review` 是研報的現況，不是事件。

輔助函式一律放在 `@router` 裝飾器之上（夾在裝飾器與 handler 之間會讓端點回 422）。
"""
from __future__ import annotations

import logging
import time
from typing import Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import text

from app.config import get_settings
from app.services.filename import source_display
from web import deps

logger = logging.getLogger(__name__)

router = APIRouter()

ReviewKind = Literal["faithfulness", "feedback", "extraction"]

_FAITHFULNESS_MIN = get_settings().faithfulness_min

# jsonb 一律先以 jsonb_typeof 過濾再 cast：一列畸形的 evaluation 不該讓整支端點 500
# （與 web/routers/monitor.py 的同一段理由相同）。
_SCORE = (
    "CASE WHEN jsonb_typeof(evaluation->'faithfulness_score') = 'number' "
    "THEN (evaluation->>'faithfulness_score')::float END"
)
_QA_VALID = "active AND stopped IS NOT TRUE AND created_at > now() - make_interval(days => :days)"


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


def _iso(v) -> str | None:
    return v.isoformat() if hasattr(v, "isoformat") else (str(v) if v is not None else None)


def _qa_item(row) -> ReviewItem:
    qa_id, conv_id, question, created_at, score, feedback = row
    return ReviewItem(
        qa_id=str(qa_id), conversation_id=str(conv_id), question=question,
        created_at=_iso(created_at),
        faithfulness_score=float(score) if score is not None else None,
        feedback=feedback,
    )


def _extraction_item(row) -> ReviewItem:
    rid, fhash, fname, title, src, rdate, qscore, qflags, pfailed = row
    return ReviewItem(
        report_id=str(rid), file_hash=fhash, file_name=fname, title=title,
        source=source_display(src), report_date=_iso(rdate),
        quality_score=float(qscore) if qscore is not None else None,
        quality_flags=qflags if isinstance(qflags, dict) else None,
        pages_failed=list(pfailed) if pfailed else None,
    )


async def _fetch(session, kind: str, *, limit: int, offset: int, days: int):
    """回 (total, items)。每個 kind 兩條查詢：count 與當頁。"""
    page = {"limit": limit, "offset": offset}
    if kind == "extraction":
        total = (await session.execute(
            text("SELECT count(*) FROM research.research_report WHERE needs_review")
        )).scalar_one()
        rows = (await session.execute(
            text(
                "SELECT id, file_hash, file_name, title, source, report_date, "
                "quality_score, quality_flags, pages_failed "
                "FROM research.research_report WHERE needs_review "
                # 分數低的在前（NULL＝算不出分數，排最後）；id 當決勝鍵讓翻頁穩定。
                "ORDER BY quality_score ASC NULLS LAST, id LIMIT :limit OFFSET :offset"
            ),
            page,
        )).all()
        return int(total), [_extraction_item(tuple(r)) for r in rows]

    if kind == "faithfulness":
        where = f"{_QA_VALID} AND ({_SCORE}) < :fmin"
        order = f"({_SCORE}) ASC, created_at DESC, id"
        params = {"days": days, "fmin": _FAITHFULNESS_MIN}
    else:  # feedback
        where = f"{_QA_VALID} AND feedback = 'dislike'"
        order = "created_at DESC, id"
        params = {"days": days}
    total = (await session.execute(
        text(f"SELECT count(*) FROM research.qa_log WHERE {where}"), params
    )).scalar_one()
    rows = (await session.execute(
        text(
            f"SELECT id, COALESCE(conversation_id, id), question, created_at, ({_SCORE}), feedback "
            f"FROM research.qa_log WHERE {where} ORDER BY {order} LIMIT :limit OFFSET :offset"
        ),
        {**params, **page},
    )).all()
    return int(total), [_qa_item(tuple(r)) for r in rows]


@router.get("/api/review/queue", response_model=ReviewQueueResponse)
async def review_queue(
    kind: ReviewKind = Query(...),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    days: int = Query(30, ge=1, le=365),
):
    """待複核佇列的一頁。`days` 只作用於 faithfulness／feedback。"""
    t0 = time.monotonic()
    async with deps.SessionFactory() as session:
        total, items = await _fetch(session, kind, limit=limit, offset=offset, days=days)
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
