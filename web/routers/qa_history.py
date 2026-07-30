# web/routers/qa_history.py
"""問答歷史 / 回饋 / 對話串 API：/api/feedback、/api/history、/api/qa/{id}/versions、
/api/conversations*。

從 web/server.py 拆出（第三步）。全為唯讀查詢或小寫入，無 SSE、無 semaphore。

delete_qa、list_qa_versions、_valid_uuid、SessionFactory 走 web.deps（測試 patch
web.deps.X 即涵蓋）。其餘服務函式（record_feedback、history_item、對話串 CRUD、
OFF_TOPIC_MESSAGES）只有這組用，由 app.services.answer 直接匯入。
"""
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import bindparam, text

from app.services.answer import (
    OFF_TOPIC_MESSAGES,
    delete_conversation,
    deleted_pdf_paths,
    get_conversation,
    history_item,
    list_conversations,
    record_feedback,
)
from web import deps

logger = logging.getLogger(__name__)

router = APIRouter()


class FeedbackRequest(BaseModel):
    qa_id: str
    value: str  # 'like' | 'dislike'


# ── 輔助函式一律放在所有 @router.* 裝飾器之上 ────────────────────────────────
# 夾在裝飾器與 handler 之間會讓裝飾器套到輔助函式，端點對正常請求回 422
# （2026-07-28 實際事故）。直接呼叫函式物件的測試看不到，只有 HTTP 層測試會抓到。


async def _delete_conversation_and_files(conversation_id: str) -> bool:
    """刪對話串（含研報衍生物）並清掉磁碟上的 PDF。兩個刪除端點共用。

    **順序是刻意的**：先查路徑（列刪掉就查不到了）→ 刪 DB → 刪檔。
    檔案刪不掉只 log 不影響回傳——DB 已提交而檔案殘留是可容忍的（`make db-audit`
    看得到）；反過來檔案刪了 DB 沒刪，就是下載端點永久 500。
    """
    paths = await deleted_pdf_paths(conversation_id)
    ok = await delete_conversation(conversation_id)
    if not ok:
        return False
    for p in paths:
        try:
            Path(p).unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("刪除對話串的 PDF 失敗（DB 已刪，檔案殘留）path=%s %s", p, exc)
    return True


@router.post("/api/feedback")
async def feedback(req: FeedbackRequest):
    """記錄使用者對某次回答的讚/倒讚（qa_id 來自 /api/ask 的 done 事件）。"""
    if req.value not in ("like", "dislike"):
        raise HTTPException(status_code=400, detail="value 必須是 like 或 dislike")
    ok = await record_feedback(req.qa_id, req.value)
    return {"ok": ok}


@router.get("/api/history")
async def history(limit: int = Query(50, ge=1, le=200)):
    """最近的問答歷史（排除離題拒答）；唯讀，供前端「歷史」抽層。"""
    async with deps.SessionFactory() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT id, question, answer, created_at, feedback, sources, ext_sources, thinking_ms "
                    "FROM research.qa_log "
                    "WHERE COALESCE(answer NOT IN :offtopics, TRUE) "
                    "AND active AND stopped IS NOT TRUE "
                    "ORDER BY created_at DESC LIMIT :limit"
                ).bindparams(bindparam("offtopics", expanding=True)),
                {"offtopics": list(OFF_TOPIC_MESSAGES), "limit": limit},
            )
        ).all()
    return [history_item(tuple(r)) for r in rows]


@router.delete("/api/history/{qa_id}")
async def delete_history(qa_id: str):
    """刪除單筆問答歷史（使用者清除側欄某一列）。回 {"ok": bool}。"""
    ok = await deps.delete_qa(qa_id)
    return {"ok": ok}


@router.post("/api/history/{qa_id}/delete")
async def delete_history_post(qa_id: str):
    """相容性刪除路由。

    某些外部代理/邊緣環境對 DELETE 支援不穩時，前端可回退到 POST alias。
    """
    ok = await deps.delete_qa(qa_id)
    return {"ok": ok}


@router.get("/api/qa/{root_qa_id}/versions")
async def qa_versions(root_qa_id: str):
    """某問題群組全部版本（供歷史 pager 回看）。"""
    if not deps._valid_uuid(root_qa_id):
        raise HTTPException(status_code=404, detail="not found")
    return await deps.list_qa_versions(root_qa_id)


@router.get("/api/conversations")
async def conversations(limit: int = Query(50, ge=1, le=200)):
    """對話串清單（首題非離題者）；唯讀，供側欄。"""
    return await list_conversations(limit)


@router.get("/api/conversations/{conversation_id}")
async def conversation_detail(conversation_id: str):
    """單一對話全部輪次（由舊到新），供重開重現與續問。"""
    return await get_conversation(conversation_id)


@router.delete("/api/conversations/{conversation_id}")
async def conversation_delete(conversation_id: str):
    """刪整個對話串（含研報衍生物與磁碟 PDF）。回 {"ok": bool}。"""
    ok = await _delete_conversation_and_files(conversation_id)
    return {"ok": ok}


@router.post("/api/conversations/{conversation_id}/delete")
async def conversation_delete_post(conversation_id: str):
    """相容性刪除路由（某些代理/邊緣對 DELETE 不穩時前端回退）。"""
    ok = await _delete_conversation_and_files(conversation_id)
    return {"ok": ok}