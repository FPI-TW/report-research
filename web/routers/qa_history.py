# web/routers/qa_history.py
"""問答歷史 / 回饋 / 對話串 API：/api/feedback、/api/history、/api/qa/{id}/versions、
/api/conversations*。

從 web/server.py 拆出（第三步）。全為唯讀查詢或小寫入，無 SSE、無 semaphore。

delete_qa、list_qa_versions、_valid_uuid、SessionFactory 走 web.deps（測試 patch
web.deps.X 即涵蓋）。其餘服務函式（record_feedback、history_item、對話串 CRUD、
OFF_TOPIC_MESSAGES）只有這組用，由 app.services.answer 直接匯入。
"""
import asyncio
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import bindparam, text

from app.services.answer import (
    OFF_TOPIC_MESSAGES,
    DeletedGeneratedObject,
    delete_conversation,
    deleted_pdf_object_keys,
    deleted_pdf_paths,
    get_conversation,
    history_item,
    list_conversations,
    record_feedback,
)
from app.services.object_storage import (
    ObjectNotFound,
    ObjectStorageError,
    generated_key_has_expected_owner,
    generated_object_key_for_sha,
    get_object_storage,
)
from web import deps, report_runs

logger = logging.getLogger(__name__)

router = APIRouter()


class FeedbackRequest(BaseModel):
    qa_id: str
    value: str  # 'like' | 'dislike' | 'none'（none＝取消，寫入 NULL）


class ReportOfferRequest(BaseModel):
    action: str  # 'decline'（收合邀請）| 'restore'（還原邀請）


# ── 輔助函式一律放在所有 @router.* 裝飾器之上 ────────────────────────────────
# 夾在裝飾器與 handler 之間會讓裝飾器套到輔助函式，端點對正常請求回 422
# （2026-07-28 實際事故）。直接呼叫函式物件的測試看不到，只有 HTTP 層測試會抓到。


async def _delete_conversation_and_files(conversation_id: str) -> bool:
    """刪對話串（含研報衍生物）並清掉磁碟上的 PDF。兩個刪除端點共用。

    **順序是刻意的**：先查路徑（列刪掉就查不到了）→ 刪 DB → 刪檔。
    檔案刪不掉只 log 不影響回傳——DB 已提交而檔案殘留是可容忍的（`make db-audit`
    看得到）；反過來檔案刪了 DB 沒刪，就是下載端點永久 500。
    """
    # Tombstone/cancel first and wait for background tasks: otherwise an SSE-decoupled run can
    # finish after these snapshots and recreate a report_doc or object pointer we just deleted.
    # The tombstone is provisional until delete_conversation has committed.  Every failed path,
    # including request cancellation, releases only this request's lease.
    lease = await report_runs.begin_conversation_deletion(conversation_id)
    try:
        paths = await deleted_pdf_paths(conversation_id)
        object_keys = await deleted_pdf_object_keys(conversation_id)
        ok = await delete_conversation(conversation_id)
        if not ok:
            report_runs.rollback_conversation_deletion(lease)
            return False
    except BaseException:
        report_runs.rollback_conversation_deletion(lease)
        raise

    # Post-commit cleanup is intentionally outside the rollback scope: DB absence is now the
    # authoritative state, and lingering local/R2 files are reconcilable orphans rather than a
    # reason to let a background run recreate the conversation.
    report_runs.confirm_conversation_deletion(lease)
    for p in paths:
        try:
            Path(p).unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("刪除對話串的 PDF 失敗（DB 已刪，檔案殘留）path=%s %s", p, exc)
    storage = get_object_storage()
    if storage.enabled:
        for object_ref in object_keys:
            await _delete_validated_generated_object(storage, object_ref)
    return True


async def _delete_validated_generated_object(storage, object_ref: DeletedGeneratedObject) -> None:
    """Delete one post-commit PDF only after validating its immutable DB ownership snapshot.

    The DB row is intentionally already gone here.  Anything that cannot be proved to be this
    exact report/rendition object is left in R2 as a reconcilable orphan rather than risking a
    cross-report deletion from a corrupt pointer.
    """
    if object_ref.kind == "base" and object_ref.rendition_id is None:
        rendition_id = None
    elif object_ref.kind == "rendition" and object_ref.rendition_id:
        rendition_id = object_ref.rendition_id
    else:
        logger.warning("略過不完整的 R2 PDF 清理身分 kind=%s key=%s", object_ref.kind, object_ref.key)
        return
    if not generated_key_has_expected_owner(object_ref.key, object_ref.report_id, rendition_id):
        logger.warning("略過不屬於對話研報的 R2 PDF key=%s report_id=%s", object_ref.key, object_ref.report_id)
        return
    try:
        metadata = await asyncio.to_thread(storage.head_object, object_ref.key)
    except ObjectNotFound:
        # The DB is gone and the exact owned key is already absent: cleanup is complete.
        return
    except ObjectStorageError as exc:
        logger.warning("驗證對話串 R2 PDF 失敗（DB 已刪，物件殘留）key=%s %s", object_ref.key, exc)
        return
    object_metadata = metadata.get("Metadata") if isinstance(metadata, dict) else None
    full_sha = (object_metadata or {}).get("sha256") or (object_metadata or {}).get("SHA256")
    try:
        canonical_key = generated_object_key_for_sha(object_ref.report_id, full_sha or "", rendition_id)
    except ValueError:
        logger.warning("略過缺少或無效 SHA metadata 的 R2 PDF key=%s", object_ref.key)
        return
    if object_ref.key != canonical_key:
        logger.warning("略過 R2 PDF key/SHA 不符 key=%s expected=%s", object_ref.key, canonical_key)
        return
    try:
        # DB is already committed.  Do not retry/delete broadly here; reconciliation reports
        # a failed one-key cleanup as an orphan for an operator to inspect.
        await asyncio.to_thread(storage.delete, object_ref.key)
    except ObjectStorageError as exc:
        logger.warning("刪除對話串的 R2 PDF 失敗（DB 已刪，物件殘留）key=%s %s", object_ref.key, exc)


@router.post("/api/feedback")
async def feedback(req: FeedbackRequest):
    """記錄使用者對某次回答的讚/倒讚（qa_id 來自 /api/ask 的 done 事件）。

    'none' ＝取消（再點一次已亮起的那顆），由 record_feedback 寫成 NULL。
    """
    if req.value not in ("like", "dislike", "none"):
        raise HTTPException(status_code=400, detail="value 必須是 like、dislike 或 none")
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


@router.post("/api/qa/{qa_id}/report-offer")
async def qa_report_offer(qa_id: str, req: ReportOfferRequest):
    """研報邀請的收合／還原（decline/restore）。回 {"ok": bool}。

    邀請本身由 get_conversation 讀取時以 gate 重算（重整後不再消失）；這支只
    負責婉拒旗標，讓「暫時不用」跨重整持久，且隨時可還原——收合不是刪除。
    """
    if not deps._valid_uuid(qa_id):
        raise HTTPException(status_code=404, detail="not found")
    if req.action not in ("decline", "restore"):
        raise HTTPException(status_code=400, detail="action 必須是 decline 或 restore")
    ok = await deps.set_report_offer_declined(qa_id, req.action == "decline")
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
