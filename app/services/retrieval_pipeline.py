"""問答/研報共用的檢索封裝：embed → hybrid_search → build_context。

僅此 helper 收斂三步序列；檢索頁分頁（rank_reports）不使用。M2 的 reranker
將插在 hybrid_search 之後、build_context 之前的此處。
"""

import asyncio

from app.services.answer import Source, build_context
from app.services.db import SessionFactory
from app.services.embed import embed_query_cached
from app.services.retrieval import hybrid_search


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
) -> tuple[list[Source], str]:
    """回 (sources, context)。timer 給定時記 embed/retrieve 兩段耗時（保留 qa_timing）。"""
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
    return build_context(
        scored,
        max_reports=max_reports,
        max_passages=max_passages,
        max_chars=max_chars,
        now=now,
    )
