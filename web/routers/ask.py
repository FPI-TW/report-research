# web/routers/ask.py
"""RAG 問答 API：/api/ask（SSE 串流回答）與 /api/ask/stop（保存中斷的部分答案）。

從 web/server.py 拆出（第三步）。

_ASK_GATE 是模組級狀態，限制同時提問數（每次提問 spawn 一個 claude CLI
子程序）。它定義在本模組、由本模組的 handler 使用；server.py 以
`from web.routers import ask` 單一路徑匯入，故全程只有一個閘門實例——
若被兩條不同 import 路徑載入會分裂成兩個、併發上限失效。**它同時是 per-process
的**：多 worker 下上限會直接翻倍，故啟動時有 fail-closed 守門，見 web/concurrency.py。

answer_question、log_stopped_qa、_valid_uuid、_sse、_with_heartbeat 走 web.deps
（測試 patch web.deps.X 即涵蓋）。
"""
import logging
import os

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.services.llm import LLMUnavailableError
from web import deps
from web.concurrency import ConcurrencyGate

logger = logging.getLogger(__name__)

router = APIRouter()


ASK_QUESTION_MAX_CHARS = 2000


class AskRequest(BaseModel):
    question: str = Field(..., max_length=ASK_QUESTION_MAX_CHARS)
    conversation_id: str | None = None
    market: str | None = None
    instrument_type: str | None = None
    relates_stock: bool | None = None
    relates_futures: bool | None = None
    report_type: str | None = None
    k: int = 8
    regenerate_of: str | None = None
    edit_of: str | None = None
    request_id: str | None = None
    locale: str | None = None  # M10：輸出語言（zh-Hant/en）；未帶/未知 → 預設中文（fail-open）
    # M11：本輪是否開放網路搜尋補充。**預設 False**——未帶欄位的舊前端行為一字不變。
    # 伺服器端另有總閘 ASK_ENABLE_WEB（app/config.py），關掉時這裡送 true 也不生效。
    web: bool = False


# 每次提問會 spawn 一個 claude CLI 子程序（CPU-bound 機器），限制同時數避免區網多人同問雪崩。
# ASK_MAX_QUEUE 是排隊人數上限（超過即 429，不是排到天荒地老）。預設 20 刻意寬鬆：
# 上限 3、單題約 60–90s，排到第 21 位表示已是堆積而非尖峰，那時讓人帶著 Retry-After
# 早點知道，好過在一條開好的 SSE 上等十分鐘。設 0 可退回舊行為（無限排隊）。
_ASK_GATE = ConcurrencyGate(3, name="ask", max_queue=int(os.getenv("ASK_MAX_QUEUE", "20")))

ASK_ERROR_DETAIL = "問答服務發生錯誤"
# 依 LLMUnavailableError.kind 給使用者看的訊息（kind 只有 HTTP 路徑會填；CLI 只有認證失效填 auth，其餘落到預設）。
# 內容審查：同一題換個問法多半就過，要讓使用者知道「可以自己處理」，而不是以為站台壞了。
# 帳號與設定層級：每一題都會失敗、使用者無能為力，直接說「暫時無法使用」，免得反覆重試；
# 啟動自檢與 `qa_log.filters.llm_error` 會留下可查的紀錄。**不承諾「已通知管理者」**：quota／auth
# 雖然會經 `/healthz/llm` → 探針退出碼 8 告警，但那要探針下一輪（約 2 分鐘）、而且只在 Slack 投遞正常
# 時才成立；config 沒有依 kind 的告警。說了等於讓使用者以為有人在處理。
# config 與帳號分開措辭：模型名打錯、`ASK_WEB_MODEL` 誤設成 DeepSeek 都會落到 config，那不是帳號問題。
_ACCOUNT_ERROR_DETAIL = "問答服務暫時無法使用（模型服務帳號異常），請稍後再試或聯絡管理者"
_LLM_ERROR_DETAILS = {
    "content_filter": "此題觸發模型供應商的內容審查，可換個問法",
    "quota": _ACCOUNT_ERROR_DETAIL,
    "auth": _ACCOUNT_ERROR_DETAIL,
    "config": "問答服務暫時無法使用（模型設定有誤），請聯絡管理者",
}


def _llm_error_detail(exc: LLMUnavailableError) -> str:
    """LLM 不可用時 SSE error 的 detail（放在所有 @router 之上，理由見 CLAUDE.md）。"""
    return _LLM_ERROR_DETAILS.get(getattr(exc, "kind", None) or "", ASK_ERROR_DETAIL)


@router.post("/api/ask")
async def ask(req: AskRequest):
    """RAG 問答：檢索 → 串流回答（帶 [n] 行內引用）。回 text/event-stream。

    事件序：（滿載時先 queued）→ sources（引用清單）→ 多筆 token（文字片段）→
    done（實際引用的報告 id）。web=true 時另可能出現 status.searching_web 與
    ext_sources（外部來源清單）。
    """
    question = (req.question or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="question 不可為空")
    mkt = req.market if req.market and req.market != "全部" else None
    instr = (
        req.instrument_type
        if req.instrument_type and req.instrument_type != "全部"
        else None
    )
    rtype = req.report_type if req.report_type and req.report_type != "全部" else None
    filters = dict(
        market=mkt,
        instrument_type=instr,
        relates_stock=req.relates_stock or None,
        relates_futures=req.relates_futures or None,
        report_type=rtype,
    )
    k = max(1, min(req.k, 20))
    if req.regenerate_of is not None and not deps._valid_uuid(req.regenerate_of):
        raise HTTPException(status_code=400, detail="regenerate_of 格式不正確")
    if req.edit_of is not None and not deps._valid_uuid(req.edit_of):
        raise HTTPException(status_code=400, detail="edit_of 格式不正確")
    if req.request_id is not None and not deps._valid_uuid(req.request_id):
        raise HTTPException(status_code=400, detail="request_id 格式不正確")
    # 唯一能回 429 的位置：SSE 一旦送出 200 就改不了 status code（見 ConcurrencyGate.queue_full）。
    if _ASK_GATE.queue_full():
        raise HTTPException(
            status_code=429,
            detail="問答排隊人數已滿，請稍後再試",
            headers={"Retry-After": "30"},
        )

    async def gen():
        # 排隊要先說。這段跑在回應開始串流之後，若直接 await 到取得名額，使用者看到的
        # 是「連線建立但永遠沒有 token」——與伺服器卡死完全無從分辨。
        if _ASK_GATE.would_queue():
            yield deps._sse("queued", _ASK_GATE.queue_event())
        await _ASK_GATE.acquire()
        try:
            try:
                async for event, payload in deps.answer_question(
                    question,
                    k=k,
                    filters=filters,
                    conversation_id=req.conversation_id,
                    regenerate_of=req.regenerate_of,
                    edit_of=req.edit_of,
                    request_id=req.request_id,
                    locale=req.locale,
                    web=req.web,
                ):
                    yield deps._sse(event, payload)
            except LLMUnavailableError as exc:
                # answer_question 已把失敗那輪落庫（filters.llm_error），這裡只負責說人話。
                logger.exception("ask failed: LLM 不可用 kind=%s", exc.kind)
                yield deps._sse("error", {"detail": _llm_error_detail(exc)})
            except Exception:
                logger.exception("ask failed")
                yield deps._sse("error", {"detail": ASK_ERROR_DETAIL})
        finally:
            _ASK_GATE.release()

    return StreamingResponse(
        deps._with_heartbeat(gen()),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


class StopRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=ASK_QUESTION_MAX_CHARS)
    conversation_id: str | None = None
    partial_answer: str = Field(default="", max_length=20_000)
    sources: list[dict] | None = Field(default=None, max_length=100)
    ext_sources: list[dict] | None = Field(default=None, max_length=50)
    stages: list[str] | None = Field(default=None, max_length=10)
    regenerate_of: str | None = None
    # 編輯重問途中停止：前端在送出當下已把編輯點之後的輪次從畫面截掉，停止列
    # 落庫時後端要做同一件事（停用舊輪次），否則重整後被編輯掉的對話整段復活。
    edit_of: str | None = None
    request_id: str | None = None


@router.post("/api/ask/stop")
async def ask_stop(req: StopRequest):
    """使用者中斷串流時保存部分答案（stopped=true）。回 {qa_id}。"""
    if req.regenerate_of is not None and not deps._valid_uuid(req.regenerate_of):
        raise HTTPException(status_code=400, detail="regenerate_of 格式不正確")
    if req.edit_of is not None and not deps._valid_uuid(req.edit_of):
        raise HTTPException(status_code=400, detail="edit_of 格式不正確")
    if req.conversation_id is not None and not deps._valid_uuid(req.conversation_id):
        raise HTTPException(status_code=400, detail="conversation_id 格式不正確")
    if req.request_id is not None and not deps._valid_uuid(req.request_id):
        raise HTTPException(status_code=400, detail="request_id 格式不正確")
    qa_id = await deps.log_stopped_qa(
        (req.question or "").strip(),
        req.partial_answer or "",
        conversation_id=req.conversation_id,
        sources=req.sources,
        ext_sources=req.ext_sources,
        stages=req.stages,
        regenerate_of=req.regenerate_of,
        edit_of=req.edit_of,
        request_id=req.request_id,
    )
    if qa_id is None:
        raise HTTPException(status_code=503, detail="停止的回答暫時無法保存")
    return {"qa_id": qa_id}