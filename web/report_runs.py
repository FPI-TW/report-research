# web/report_runs.py
"""背景研報生成登錄表：把生成從 SSE 連線上解耦。

## 為什麼存在

`POST /api/report` 原本直接把 `generate_report(...)` 掛在 StreamingResponse 上。
使用者一重整／關分頁，uvicorn 就取消該 response task → 產生器在 yield 點收到
CancelledError → `report.py` 把 run 標成 cancelled。也就是說「重整後看不到生成中的
框」不只是前端沒畫，是**那份研報真的被殺掉了**——一件要跑 5–12 分鐘的事，重整一次
從頭來過，而畫面上沒有任何線索說明發生了什麼。

改法：生成跑在獨立的 `asyncio.Task`，事件進「重播緩衝 ＋ 每訂閱者一條佇列」；HTTP
只是訂閱端。斷線只是少一個訂閱者，任務照跑；重連（`GET /api/report-runs/{id}/stream`）
先收重播、再接上直播，於是重整、開新分頁、換裝置都看得到同一份進度。

## run_id 是本登錄表自己的 id，不是 report_run.id

`report_run.id` 由 `generate_report` 內部經 `_open_sectioned_run` 鑄造，而且只在
「逐節路徑開啟且 persist=True」時才存在——單次路徑與 eval 模式根本沒有。訂閱用的
handle 必須在生成開始前就拿得到，兩者生命週期也不同（DB 列長存、登錄項十分鐘後回收），
所以刻意不共用。要對帳時走 `report_run.conversation_id`／`qa_id`。

## 重播緩衝為什麼不存 token

`token` 在單次路徑是逐塊 markdown、逐節路徑是整節草稿，一份研報累計可達數百 KB，
全留在記憶體裡等一個可能永遠不會來的重連是純浪費。**現行前端完全忽略研報的 token
事件**（`askReducer.applyReport` 的 `case 'token': return t`），研報內文的真相是
`done` 帶的 report_id → PDF。故 token 照樣即時廣播給在線訂閱者（維持既有線路契約），
但不進重播緩衝；`section_draft` 同理只留 metadata、丟 markdown。

## 只在單一行程內有效

登錄表是行程內狀態（和 `_REPORT_SEMAPHORE` 一樣）。重啟即全滅——這正是
`report_writer.open_run` 需要「久無心跳的 in-flight run 可重試」那個條件的原因：
沒有它，一次 deploy 就讓那個冪等鍵永遠卡在「正在處理」。
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import time
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# 生成結束後仍可被重連取用的保留秒數：涵蓋「最後一節寫完 → 使用者切回分頁」的空窗。
# 過了就回收，之後該對話的完成研報改由對話歷史（report_doc）還原，不需要登錄項。
RUN_RETENTION_SECONDS = float(os.getenv("REPORT_RUN_RETENTION_SECONDS", "600"))

# 不進重播緩衝的事件種類（見模組 docstring）。
_VOLATILE = frozenset({"token"})
# 進緩衝但要先瘦身的事件：值＝保留的欄位。
_SLIM_KEEP = {
    "section_draft": ("position", "section_key", "heading"),
    "document_revision": ("revision_id", "revision"),
}
# `queued`（併發滿載、正在排隊）刻意**兩張表都不列**＝原樣進重播。
# 直覺會把它當「即時才有意義」丟進 _VOLATILE，但那樣做，在還沒輪到就重整的人重連後
# 會收到空重播——正是這個事件要消滅的「連上了卻什麼都沒有」。留著則自我修正：排隊
# 期間重連的人看到「排隊中」（仍是實情），已經開跑的 run 其重播裡 queued 後面必然接著
# status/outline，前端 reducer 收到任何後續事件就會清掉排隊狀態（見 askReducer）。
# 佇列終止標記。None／空 tuple 都可能是合法 payload，故用獨一物件。
_SENTINEL = object()


@dataclass
class _Run:
    run_id: str
    key: str
    question: str
    conversation_id: str | None
    qa_id: str | None
    started_at: float
    replay: list[tuple[str, object]] = field(default_factory=list)
    subscribers: set[asyncio.Queue] = field(default_factory=set)
    task: asyncio.Task | None = None
    finished_at: float | None = None

    @property
    def active(self) -> bool:
        return self.finished_at is None


_RUNS: dict[str, _Run] = {}
# 去重鍵 → run_id。只保留 active 的 run；完成即移除，讓同題重送能再跑一次。
_BY_KEY: dict[str, str] = {}
# A deleted conversation's UUID cannot legitimately be reused.  This in-process tombstone closes
# the gap between starting background work and the DB deletion transaction.  A failed deletion
# relinquishes its own provisional tombstone; a committed deletion keeps it until shutdown.
_DELETED_CONVERSATIONS: set[str] = set()
# Each deletion request owns a short-lived lease until its DB transaction has either committed or
# failed.  A tombstone stays live while *any* lease is pending.  A monotonically increasing token
# lets a rollback relinquish only itself, not a concurrent request's deletion guard.
_DELETION_GENERATIONS: dict[str, int] = {}
_PENDING_DELETION_LEASES: dict[str, set[int]] = {}
_COMPLETED_DELETIONS: set[str] = set()


@dataclass(frozen=True)
class ConversationDeletionLease:
    """Identity of one in-process conversation-deletion attempt.

    This is deliberately not a database lock.  It only closes the local gap while the delete
    transaction is in flight, and makes failure rollback safe when two HTTP requests overlap.
    """

    conversation_id: str
    generation: int | None
    cancelled_runs: int


def _dedup_key(
    question: str,
    conversation_id: str | None,
    template_id: str | None,
    locale: str | None,
) -> str:
    """同一份研報的識別。刻意與 `report_writer.synthesize_request_key` 分開：那把鍵是
    DB 層的冪等鍵（含 model、不含 template），這把只用來擋「同一使用者連按兩下／重整
    後又按一次生成」，維度取前端實際會變動的那幾個就夠。"""
    raw = "\x1f".join([question.strip(), conversation_id or "", template_id or "", locale or ""])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _slim(kind: str, payload: object) -> object:
    """重播緩衝用的瘦身版 payload（見模組 docstring：不留大段 markdown）。"""
    keep = _SLIM_KEEP.get(kind)
    if keep is None or not isinstance(payload, dict):
        return payload
    return {k: payload.get(k) for k in keep}


def _publish(run: _Run, kind: str, payload: object) -> None:
    """把一個事件同時寫進重播緩衝並廣播給在線訂閱者。

    **全程無 await**——`subscribe` 依賴這個不變式來避免「重播 ＋ 直播」重覆同一事件：
    它在同一個同步區段內先掛佇列、再快照 replay，兩步之間不可能被本函式插入。
    """
    if kind not in _VOLATILE:
        run.replay.append((kind, _slim(kind, payload)))
    for q in list(run.subscribers):
        q.put_nowait((kind, payload))


def _finish(run: _Run) -> None:
    run.finished_at = time.monotonic()
    for q in list(run.subscribers):
        q.put_nowait(_SENTINEL)
    if _BY_KEY.get(run.key) == run.run_id:
        _BY_KEY.pop(run.key, None)


def _gc() -> None:
    """回收保留期已過且無人訂閱的登錄項。"""
    now = time.monotonic()
    for run_id, run in list(_RUNS.items()):
        if (
            run.finished_at is not None
            and not run.subscribers
            and now - run.finished_at > RUN_RETENTION_SECONDS
        ):
            _RUNS.pop(run_id, None)


async def _pump(run: _Run, events: AsyncIterator[tuple[str, object]]) -> None:
    """把生成器的事件抽進登錄表。任何結局都必須走到 `_finish`，否則訂閱者永遠等不到
    終止標記——畫面就會停在「生成中」直到使用者放棄。"""
    try:
        async for kind, payload in events:
            _publish(run, kind, payload)
    except asyncio.CancelledError:
        _publish(run, "error", {"detail": "研報生成已取消"})
        raise
    except Exception:
        logger.exception("背景研報生成失敗 run=%s", run.run_id)
        _publish(run, "error", {"detail": "研報生成發生錯誤"})
    finally:
        _finish(run)


def active_run_for(
    *,
    question: str,
    conversation_id: str | None,
    template_id: str | None,
    locale: str | None,
) -> str | None:
    """這組參數目前是否已有進行中的 run（＝`start_or_attach` 會接回而非新開）。

    存在的理由只有一個：`/api/report` 的滿載 429 必須豁免「接回」這條路徑。接回不需要
    併發名額（生成早就在跑），把它一起擋掉就變成「重整一下，自己快好的研報反而被拒」。
    """
    key = _dedup_key(question, conversation_id, template_id, locale)
    run_id = _BY_KEY.get(key)
    if run_id is None:
        return None
    run = _RUNS.get(run_id)
    return run_id if run is not None and run.active else None


def conversation_deleted(conversation_id: str | None) -> bool:
    """Whether this process has begun deletion of a conversation's artifacts."""
    return conversation_id is not None and (
        conversation_id in _COMPLETED_DELETIONS
        or bool(_PENDING_DELETION_LEASES.get(conversation_id))
    )


async def cancel_conversation(conversation_id: str) -> int:
    """Tombstone and stop all active runs for a conversation before its DB rows are deleted.

    Awaiting task termination is essential: it ensures a run cannot reach a later persistence
    await after the deletion transaction has removed its existing artifacts.  A blocked worker
    thread may still finish an in-flight upload, but it has no DB pointer and reconciliation can
    safely report that exact generated-prefix orphan.
    """
    lease = await begin_conversation_deletion(conversation_id)
    return lease.cancelled_runs


async def begin_conversation_deletion(conversation_id: str) -> ConversationDeletionLease:
    """Tombstone a conversation and await active report runs before DB deletion.

    Call :func:`confirm_conversation_deletion` only after the DB deletion transaction commits;
    call :func:`rollback_conversation_deletion` for every other exit.  Cancellation while waiting
    for a run is itself a failed deletion attempt and is rolled back here before it propagates.
    """
    if conversation_id in _COMPLETED_DELETIONS:
        return ConversationDeletionLease(conversation_id, None, 0)

    generation = _DELETION_GENERATIONS.get(conversation_id, 0) + 1
    _DELETION_GENERATIONS[conversation_id] = generation
    _PENDING_DELETION_LEASES.setdefault(conversation_id, set()).add(generation)
    _DELETED_CONVERSATIONS.add(conversation_id)
    lease = ConversationDeletionLease(conversation_id, generation, 0)
    tasks = [
        run.task for run in _RUNS.values()
        if run.active and run.conversation_id == conversation_id and run.task is not None
    ]
    for task in tasks:
        task.cancel()
    try:
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                # A cancelled child is expected.  A cancellation delivered to *this* delete
                # request is not: propagate it so the caller releases the provisional lease.
                if asyncio.current_task() is not None and asyncio.current_task().cancelling():
                    raise
                pass
            except Exception:
                logger.warning("刪除對話串時背景研報收尾失敗", exc_info=True)
    except BaseException:
        rollback_conversation_deletion(lease)
        raise
    return ConversationDeletionLease(conversation_id, generation, len(tasks))


def confirm_conversation_deletion(lease: ConversationDeletionLease) -> None:
    """Retain a tombstone after the caller has confirmed its DB delete committed."""
    _COMPLETED_DELETIONS.add(lease.conversation_id)
    _PENDING_DELETION_LEASES.pop(lease.conversation_id, None)
    _DELETED_CONVERSATIONS.add(lease.conversation_id)


def rollback_conversation_deletion(lease: ConversationDeletionLease) -> None:
    """Release only this failed deletion attempt without reviving another request."""
    if lease.generation is None or lease.conversation_id in _COMPLETED_DELETIONS:
        return
    pending = _PENDING_DELETION_LEASES.get(lease.conversation_id)
    if pending is None:
        return
    pending.discard(lease.generation)
    if pending:
        return
    _PENDING_DELETION_LEASES.pop(lease.conversation_id, None)
    _DELETED_CONVERSATIONS.discard(lease.conversation_id)


def start_or_attach(
    *,
    question: str,
    conversation_id: str | None,
    qa_id: str | None,
    template_id: str | None,
    locale: str | None,
    make_events: Callable[[], AsyncIterator[tuple[str, object]]],
) -> tuple[str, bool]:
    """開一個背景生成，或回傳同題目前仍在跑的那個。回 ``(run_id, is_new)``。

    去重是必要的而非最佳化：重整後前端會先問 `/api/report-runs` 再自動接回，但使用者
    也可能直接再按一次「生成研報」。沒有去重就會有兩條任務寫同一個冪等鍵，其中一條
    必定撞上 `open_run` 的「相同研報請求正在處理」而回錯——使用者看到的是「重試也失敗」。
    """
    _gc()
    if conversation_deleted(conversation_id):
        raise ValueError("conversation has been deleted")
    key = _dedup_key(question, conversation_id, template_id, locale)
    existing_id = _BY_KEY.get(key)
    if existing_id:
        existing = _RUNS.get(existing_id)
        if existing is not None and existing.active:
            return existing_id, False
        _BY_KEY.pop(key, None)

    run_id = str(uuid.uuid4())
    run = _Run(
        run_id=run_id, key=key, question=question,
        conversation_id=conversation_id, qa_id=qa_id,
        started_at=time.monotonic(),
    )
    _RUNS[run_id] = run
    _BY_KEY[key] = run_id
    # 必須持有 Task 參照（run.task）：只留在區域變數會被 GC 回收而任務中途消失。
    run.task = asyncio.create_task(_pump(run, make_events()), name=f"report-run-{run_id[:8]}")
    return run_id, True


async def subscribe(run_id: str) -> AsyncIterator[tuple[str, object]]:
    """訂閱某個 run：先重播已發生的事件，再接上直播，直到生成結束。

    未知 run_id 直接結束（不拋）——例如行程重啟後前端還拿著舊 id 來接。
    """
    run = _RUNS.get(run_id)
    if run is None:
        return
    q: asyncio.Queue = asyncio.Queue()
    # ── 以下三行之間不可插入 await ──────────────────────────────────────────
    # 先掛佇列、再快照 replay：反過來會漏掉「快照完成到掛上佇列之間」發生的事件。
    # 而 `_publish` 全程同步（見該函式註解），故快照之後才發生的事件必然只走佇列、
    # 不在 backlog 裡，兩邊不會重覆。
    run.subscribers.add(q)
    backlog = list(run.replay)
    finished = run.finished_at is not None
    # ──────────────────────────────────────────────────────────────────────
    try:
        for ev in backlog:
            yield ev
        if finished:
            return
        while True:
            item = await q.get()
            if item is _SENTINEL:
                return
            yield item
    finally:
        run.subscribers.discard(q)


def exists(run_id: str) -> bool:
    """登錄表裡還有這個 run 嗎（含保留期內已完成的）。重連端點用它分辨 404。"""
    return run_id in _RUNS


def elapsed_ms(run_id: str) -> int:
    """這個 run 已經跑了多久。前端拿它回推起始時刻（`Date.now() - elapsed_ms`），
    避免直接送伺服器時間戳而受兩端時鐘偏差影響。"""
    run = _RUNS.get(run_id)
    if run is None:
        return 0
    end = run.finished_at if run.finished_at is not None else time.monotonic()
    return int((end - run.started_at) * 1000)


def _snapshot(run: _Run) -> dict:
    return {
        "run_id": run.run_id,
        "qa_id": run.qa_id,
        "conversation_id": run.conversation_id,
        "question": run.question,
        "elapsed_ms": elapsed_ms(run.run_id),
    }


def find_active(conversation_id: str) -> list[dict]:
    """某對話目前仍在生成的 run。前端載入對話時據此自動接回。

    只回未完成的：已完成的研報由對話歷史（`report_doc` → `turnFromHistory`）還原，
    不需要也不該從這裡拿——登錄項十分鐘後就回收了，兩條來源會給出不一致的答案。
    """
    _gc()
    return [
        _snapshot(run)
        for run in _RUNS.values()
        if run.active and run.conversation_id == conversation_id
    ]


def cancel(run_id: str) -> bool:
    """使用者主動取消。回傳是否真的取消了一個進行中的 run。"""
    run = _RUNS.get(run_id)
    if run is None or not run.active or run.task is None:
        return False
    run.task.cancel()
    return True


async def shutdown() -> None:
    """行程收工：取消所有進行中的生成並等它們收尾。

    等待不是禮貌而是必要——`report.py` 的 `except CancelledError` 要把 run 標成
    cancelled，不等就會留下一筆 in-flight 的 `report_run`，接下來 30 分鐘內同一
    冪等鍵都會被擋（見 `open_run` 的 stale 條件）。
    """
    tasks = [run.task for run in _RUNS.values() if run.active and run.task is not None]
    for task in tasks:
        task.cancel()
    for task in tasks:
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.warning("研報背景任務收尾時拋錯", exc_info=True)
    _RUNS.clear()
    _BY_KEY.clear()
    _DELETED_CONVERSATIONS.clear()
    _DELETION_GENERATIONS.clear()
    _PENDING_DELETION_LEASES.clear()
    _COMPLETED_DELETIONS.clear()
