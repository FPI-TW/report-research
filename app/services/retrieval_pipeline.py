"""問答的檢索封裝：embed → hybrid_search → build_context。

僅此 helper 收斂三步序列；檢索頁分頁（rank_reports）不使用。M2 的 reranker
插在 hybrid_search 之後、build_context 之前。
"""

import asyncio
import logging
import os
import time

from app.services.answer import Source, build_context
from app.services.db import SessionFactory
from app.services.embed import embed_query_cached
from app.services.rerank import rerank_scored
from app.services.retrieval import hybrid_search

logger = logging.getLogger(__name__)
# 名額對齊 _ASK_GATE 的容量 3：只有 1 個名額時，同時提問的第 2、3 人各自要多等一輪
# 完整 rerank（prod 實測單輪 40s），而那段等待不在任何逾時預算內、只表現為「更慢」。
# 注意這裡沒有連帶限制 torch 的執行緒數，3 輪並行在核心數不足的機器上會互相搶。
_RERANK_WORKERS = max(1, int(os.getenv("REPORT_MARK_RERANK_WORKERS", "3")))
# 呼叫端未給 rerank_timeout 時的後備值（answer 帶 Settings 的 per-path 逾時）。
_RERANK_TIMEOUT = float(os.getenv("REPORT_MARK_RERANK_TIMEOUT", "30"))
_rerank_semaphore = asyncio.Semaphore(_RERANK_WORKERS)


async def _run_rerank(question, scored, *, top_m, timer, deadline):
    """執行緒工作由此 task 持有 semaphore；呼叫端取消不會釋放仍在跑的 CPU 工作，
    但 deadline 使被放棄的工作在批次邊界提早收手（釋放 CPU 與 semaphore）。"""
    async with _rerank_semaphore:
        return await asyncio.to_thread(
            rerank_scored, question, scored, top_m=top_m, timer=timer,
            deadline=deadline,
        )


def _consume_rerank_result(task: asyncio.Task) -> None:
    """讀取背景重排結果，避免逾時或取消後的例外成為未取用 task exception。"""
    try:
        task.result()
    except asyncio.CancelledError:
        pass
    except Exception:
        logger.debug("background rerank failed", exc_info=True)


async def _rerank_stage(
    question, scored, *, top_m, timeout, timer
) -> tuple[list, bool]:
    """單次 rerank 段（fail-open）：回 (scored, applied)。

    applied=False ＝ rerank 未實際套用——(a) asyncio.wait_for 逾時；或
    (b) rerank_scored 回傳「與輸入同一 list 物件」（其 fail-open 契約，見
    rerank.rerank_scored docstring）。retrieve_context 忽略 applied；
    retrieve_context_multi 據此決定多查詢降級。逾時預算含排隊等待 semaphore。
    """
    task = asyncio.create_task(
        _run_rerank(
            question, scored, top_m=top_m, timer=timer,
            deadline=time.monotonic() + timeout,
        )
    )
    try:
        reranked = await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
    except TimeoutError:
        logger.warning("rerank timed out; using fused ranking")
        task.add_done_callback(_consume_rerank_result)
        return scored, False
    except asyncio.CancelledError:
        task.add_done_callback(_consume_rerank_result)
        raise
    return reranked, reranked is not scored


async def retrieve_context(
    question: str,
    *,
    k: int,
    dense_scan: int,
    max_reports: int,
    max_passages: int,
    max_chars: int,
    filters: dict | None = None,
    now=None,
    timer=None,
    rerank_top_m: int = 0,
    rerank_timeout: float | None = None,
    stats: dict | None = None,
) -> tuple[list[Source], str]:
    """回 (sources, context)。timer 給定時記 embed/retrieve/rerank 各段耗時。
    rerank_top_m>0 時在檢索後、選篇前插入 cross-encoder 重排（fail-open），並把
    重排前的 fused 快照當 gate_scores 傳給 build_context——重排會覆寫分數尺度，
    而選篇的 relevance_floor 是以 fused 校準的（詳見下方註解）。
    rerank_timeout 未給時退回模組後備值；逾時預算含排隊等待 semaphore 的時間。

    `stats` 給定時原樣轉給 hybrid_search 填寫字面路召回遙測（lex_hits／lex_cap／
    lex_truncated）與兩路的分段耗時（dense_ms／lex_ms，毫秒；字面路未執行時
    lex_ms 為 0），由呼叫端決定要不要記錄——本函式不 log，避免同一份資訊在管線裡
    出現兩次而對不上。"""
    filters = filters or {}
    qvec = await asyncio.to_thread(embed_query_cached, question)
    if timer is not None:
        timer.mark("embed")
    async with SessionFactory() as session:  # 短連線：檢索完即釋放
        scored = await hybrid_search(
            session, question, qvec, k=k, dense_scan=dense_scan, stats=stats, **filters
        )
    if timer is not None:
        timer.mark("retrieve")
    gate_scores: dict[str, float] | None = None
    if rerank_top_m > 0:
        # gate 快照：rerank 前的 fused（select_reports 的 relevance_floor 以此尺度校準）。
        # 不留快照就是量綱錯配——rerank_scored 會把 head 的第 2 欄由 fused 換成
        # cross-encoder 的 sigmoid [0,1] 分（tail 保留 fused），而 ASK_RELEVANCE_FLOOR
        # =0.62 是 rerank 進來之前、以 fused 尺度校準的門檻（見 answer.select_reports）。
        # 兩者相比會誤剔 tier 0 的高相關候選：baseline-m0（rerank off）的 n_contexts
        # 為 15/15/3/4/15/15/3/15，baseline-m2（rerank on）同題掉到 9/6/3/3/-/6/3/7。
        # retrieve_context_multi 早在 M6 就傳了快照，這條單查詢路徑被漏掉；而它才是
        # 預設生產路徑（ASK_RERANK_ENABLED 預設 1），並且研報的逐節檢索在
        # plan_queries fail-open 回單一查詢時也會落到這裡。
        gate_scores = {row.chunk_id: fused for (_tier, fused, row) in scored}
        # CPU-bound cross-encoder：比照 embed_query_cached 卸載到執行緒，避免同步
        # 推論（ask 50 / report 120 對候選）阻塞單一 asyncio event loop 凍結全站併發。
        timeout = rerank_timeout if rerank_timeout is not None else _RERANK_TIMEOUT
        scored, applied = await _rerank_stage(
            question, scored, top_m=rerank_top_m, timeout=timeout, timer=timer
        )
        if not applied:
            # 未套用＝fused 未被覆蓋，gate 直接比 fused 等價；傳 None 讓「有沒有
            # 發生覆寫」在呼叫端可讀，與 retrieve_context_multi 的降級處理一致。
            gate_scores = None
    return build_context(
        scored,
        max_reports=max_reports,
        max_passages=max_passages,
        max_chars=max_chars,
        now=now,
        gate_scores=gate_scores,
    )
