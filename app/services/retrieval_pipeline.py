"""問答/研報共用的檢索封裝：embed → hybrid_search → build_context。

僅此 helper 收斂三步序列；檢索頁分頁（rank_reports）不使用。M2 的 reranker
將插在 hybrid_search 之後、build_context 之前的此處。
"""

import asyncio

from app.services.answer import Source, build_context
from app.services.db import SessionFactory
from app.services.embed import embed_query_cached
from app.services.retrieval import hybrid_search
from app.services.rerank import rerank_scored


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
) -> tuple[list[Source], str]:
    """回 (sources, context)。timer 給定時記 embed/retrieve/rerank 各段耗時。
    rerank_top_m>0 時在檢索後、選篇前插入 cross-encoder 重排（fail-open）。"""
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
        scored = await asyncio.to_thread(
            rerank_scored, question, scored, top_m=rerank_top_m, timer=timer
        )
    return build_context(
        scored,
        max_reports=max_reports,
        max_passages=max_passages,
        max_chars=max_chars,
        now=now,
    )
