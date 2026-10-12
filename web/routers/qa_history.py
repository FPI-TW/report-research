# web/routers/qa_history.py
"""問答歷史 / 回饋 / 對話串 API：/api/feedback、/api/history、/api/qa/{id}/versions、
/api/conversations*。

從 web/server.py 拆出（第三步）。全為唯讀查詢或小寫入，無 SSE、無 semaphore。

delete_qa、list_qa_versions、_valid_uuid、SessionFactory 走 web.deps（測試 patch
web.deps.X 即涵蓋）。其餘服務函式（record_feedback、history_item、對話串 CRUD、
OFF_TOPIC_MESSAGES）只有這組用，由 app.services.answer 直接匯入。

**每人資料隔離**：每支端點都取目前使用者（`authz.current_user`），把 `user.id` 傳進
SQL 條件——隔離在後端，不靠前端。別人的資料與不存在的資料回一樣的東西：
清單裡看不到；詳情與版本回 404；回饋與刪除回 `{"ok": false}`。個別帳號上線前的
共用歷史（`user_id` 為 NULL）一般帳號一律看不到。
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import bindparam, text

from app.services.accounts import User
from app.services.answer import (
    OFF_TOPIC_MESSAGES,
    delete_conversation,
    get_conversation,
    history_item,
    list_conversations,
    record_feedback,
)
from web import authz, deps

logger = logging.getLogger(__name__)

router = APIRouter()


class FeedbackRequest(BaseModel):
    qa_id: str
    value: str  # 'like' | 'dislike' | 'none'（none＝取消，寫入 NULL）


# ── 輔助函式一律放在所有 @router.* 裝飾器之上 ────────────────────────────────
# 夾在裝飾器與 handler 之間會讓裝飾器套到輔助函式，端點對正常請求回 422
# （2026-07-28 實際事故）。直接呼叫函式物件的測試看不到，只有 HTTP 層測試會抓到。


async def _delete_conversation_and_files(conversation_id: str, user: User) -> bool:
    """刪對話串中自己的列。兩個刪除端點共用；DB 異常由 delete_conversation 吞成 False。

    非法 id 直接回 False：它進的是 uuid 欄位的 WHERE，驅動會在編碼期拋例外。
    """
    if not deps._valid_uuid(conversation_id):
        return False
    return await delete_conversation(conversation_id, user_id=user.id)


async def _delete_one(qa_id: str, user: User) -> bool:
    """刪自己的一筆問答（兩條刪除路由共用）。別人的、不存在的、非法 id 都是 False。"""
    if not deps._valid_uuid(qa_id):
        return False
    return await deps.delete_qa(qa_id, user_id=user.id)


@router.post("/api/feedback")
async def feedback(req: FeedbackRequest, user: User = Depends(authz.current_user)):
    """記錄使用者對某次回答的讚/倒讚（qa_id 來自 /api/ask 的 done 事件）。

    'none' ＝取消（再點一次已亮起的那顆），由 record_feedback 寫成 NULL。
    """
    if req.value not in ("like", "dislike", "none"):
        raise HTTPException(status_code=400, detail="value 必須是 like、dislike 或 none")
    if not deps._valid_uuid(req.qa_id):
        return {"ok": False}
    ok = await record_feedback(req.qa_id, req.value, user_id=user.id)
    return {"ok": ok}


@router.get("/api/history")
async def history(limit: int = Query(50, ge=1, le=200), user: User = Depends(authz.current_user)):
    """自己最近的問答歷史（排除離題拒答）；唯讀，供前端「歷史」抽層。"""
    async with deps.SessionFactory() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT id, question, answer, created_at, feedback, sources, ext_sources, thinking_ms "
                    "FROM research.qa_log "
                    "WHERE COALESCE(answer NOT IN :offtopics, TRUE) "
                    "AND active AND stopped IS NOT TRUE "
                    "AND user_id IS NOT DISTINCT FROM :uid "
                    "ORDER BY created_at DESC LIMIT :limit"
                ).bindparams(bindparam("offtopics", expanding=True)),
                {"offtopics": list(OFF_TOPIC_MESSAGES), "limit": limit, "uid": user.id},
            )
        ).all()
    return [history_item(tuple(r)) for r in rows]


@router.delete("/api/history/{qa_id}")
async def delete_history(qa_id: str, user: User = Depends(authz.current_user)):
    """刪除自己的單筆問答歷史（使用者清除側欄某一列）。回 {"ok": bool}。"""
    return {"ok": await _delete_one(qa_id, user)}


@router.post("/api/history/{qa_id}/delete")
async def delete_history_post(qa_id: str, user: User = Depends(authz.current_user)):
    """相容性刪除路由。

    某些外部代理/邊緣環境對 DELETE 支援不穩時，前端可回退到 POST alias。
    """
    return {"ok": await _delete_one(qa_id, user)}


@router.get("/api/qa/{root_qa_id}/versions")
async def qa_versions(root_qa_id: str, user: User = Depends(authz.current_user)):
    """某問題群組中自己的全部版本（供歷史 pager 回看）。一個都看不到 → 404。"""
    if not deps._valid_uuid(root_qa_id):
        raise HTTPException(status_code=404, detail="not found")
    versions = await deps.list_qa_versions(root_qa_id, user_id=user.id)
    if not versions:
        raise HTTPException(status_code=404, detail="not found")
    return versions


@router.get("/api/conversations")
async def conversations(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    q: str | None = Query(None, max_length=200),
    user: User = Depends(authz.current_user),
):
    """自己的對話串清單（首題非離題者）；唯讀，供側欄。

    `q` 搜尋整串的提問、`offset` 翻頁。回應維持裸陣列（沒有 total）：前端以
    「回來的筆數等於 limit」判斷還有沒有下一頁，舊 bundle 也照樣解析得了。
    """
    return await list_conversations(limit, offset, q, user_id=user.id)


@router.get("/api/conversations/{conversation_id}")
async def conversation_detail(conversation_id: str, user: User = Depends(authz.current_user)):
    """單一對話中自己的全部輪次（由舊到新），供重開重現與續問。

    conversation_id 進的是 uuid 欄位的 WHERE；非法字串會讓驅動在編碼期拋例外變 500，
    所以比照 qa_versions 先擋成 404。一輪都看不到（不存在或是別人的）也是 404。
    """
    if not deps._valid_uuid(conversation_id):
        raise HTTPException(status_code=404, detail="not found")
    turns = await get_conversation(conversation_id, user_id=user.id)
    if not turns:
        raise HTTPException(status_code=404, detail="not found")
    return turns


@router.delete("/api/conversations/{conversation_id}")
async def conversation_delete(conversation_id: str, user: User = Depends(authz.current_user)):
    """刪整個對話串（只刪自己的列）。回 {"ok": bool}。"""
    return {"ok": await _delete_conversation_and_files(conversation_id, user)}


@router.post("/api/conversations/{conversation_id}/delete")
async def conversation_delete_post(conversation_id: str, user: User = Depends(authz.current_user)):
    """相容性刪除路由（某些代理/邊緣對 DELETE 不穩時前端回退）。"""
    return {"ok": await _delete_conversation_and_files(conversation_id, user)}
