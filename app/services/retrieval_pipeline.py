"""問答/研報共用的檢索封裝：embed → hybrid_search → build_context。

僅此 helper 收斂三步序列；檢索頁分頁（rank_reports）不使用。M2 的 reranker
將插在 hybrid_search 之後、build_context 之前的此處。
"""

import asyncio
import logging
import os
import time

from app.services.answer import Source, build_context
from app.services.db import SessionFactory
from app.services.embed import embed_query_cached
from app.services.retrieval import hybrid_search
from app.services.rerank import rerank_scored

logger = logging.getLogger(__name__)
_RERANK_WORKERS = max(1, int(os.getenv("REPORT_MARK_RERANK_WORKERS", "1")))
# 呼叫端未給 rerank_timeout 時的後備值（answer/report 各自帶 Settings 的 per-path 逾時）。
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
) -> tuple[list[Source], str]:
    """回 (sources, context)。timer 給定時記 embed/retrieve/rerank 各段耗時。
    rerank_top_m>0 時在檢索後、選篇前插入 cross-encoder 重排（fail-open）。
    rerank_timeout 未給時退回模組後備值；逾時預算含排隊等待 semaphore 的時間。"""
    filters = filters or {}
    qvec = await asyncio.to_thread(embed_query_cached, question)
    if timer is not None:
        timer.mark("embed")
    async with SessionFactory() as session:  # 短連線：檢索完即釋放
        scored = await hybrid_search(
            session, question, qvec, k=k, dense_scan=dense_scan, **filters
        )
    if timer is not None:
        timer.mark("retrieve")
    if rerank_top_m > 0:
        # CPU-bound cross-encoder：比照 embed_query_cached 卸載到執行緒，避免同步
        # 推論（ask 50 / report 120 對候選）阻塞單一 asyncio event loop 凍結全站併發。
        timeout = rerank_timeout if rerank_timeout is not None else _RERANK_TIMEOUT
        task = asyncio.create_task(
            _run_rerank(
                question, scored, top_m=rerank_top_m, timer=timer,
                deadline=time.monotonic() + timeout,
            )
        )
        try:
            scored = await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
        except TimeoutError:
            logger.warning("rerank timed out; using fused ranking")
            task.add_done_callback(_consume_rerank_result)
        except asyncio.CancelledError:
            task.add_done_callback(_consume_rerank_result)
            raise
    return build_context(
        scored,
        max_reports=max_reports,
        max_passages=max_passages,
        max_chars=max_chars,
        now=now,
    )
