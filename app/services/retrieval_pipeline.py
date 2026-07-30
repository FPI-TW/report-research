"""問答/研報共用的檢索封裝：embed → hybrid_search → build_context。

僅此 helper 收斂三步序列；檢索頁分頁（rank_reports）不使用。M2 的 reranker
插在 hybrid_search 之後、build_context 之前。M6 增設研報用多查詢 fan-out
（retrieve_context_multi）：逐子查詢檢索 → chunk 級合併去重 → 合併後單次
rerank（query=原題）→ build_context；retrieve_context 簽名與行為不變。
"""

import asyncio
import logging
import os
import time
from typing import Sequence

from app.config import get_settings
from app.services.answer import Source, build_context
from app.services.db import SessionFactory
from app.services.embed import embed_query_cached
from app.services.rerank import rerank_scored
from app.services.retrieval import classify_match, extract_terms, hybrid_search
from app.services.rows import ChunkRow
from app.services.store import fetch_chunk_embeddings
from app.services.textnorm import clean_text

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
    lex_truncated），由呼叫端決定要不要記錄——本函式不 log，避免同一份資訊在管線裡
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


def merge_scored(
    results: list[list[tuple[int, float, ChunkRow]]], *, cap: int
) -> list[tuple[int, float, ChunkRow]]:
    """多條子查詢結果 chunk 級合併（純函式）：同 chunk 被多條子查詢命中時取
    (tier, fused) 最大者，依 (tier, fused) 降序排序後截斷至 cap（<=0＝不截斷）。

    各子查詢的 tier 是「相對該子查詢字面」的分層，異質但仍是字面命中某面向的
    合理代理；head 隨後由 rerank 以原始主題重打分。
    """
    best: dict[str, tuple[int, float, ChunkRow]] = {}
    for scored in results:
        for tier, fused, row in scored:
            cur = best.get(row.chunk_id)
            if cur is None or (tier, fused) > (cur[0], cur[1]):
                best[row.chunk_id] = (tier, fused, row)
    merged = sorted(best.values(), key=lambda s: (s[0], s[1]), reverse=True)
    if cap > 0:
        return merged[:cap]
    return merged


def _retier_to_question(question, merged):
    """以原始主題重算合併候選的 tier（純函式，可卸載到執行緒）。

    各子查詢的 hybrid_search tier 只相對「該子查詢字面」；離題子查詢的字面命中
    會冒充 tier 2 霸佔 select_reports 頂部並繞過 relevance_floor（tier>=TIER_ALL_TERMS
    免閘），而 rerank 只在同 tier 內重排（保留 tier）無法跨 tier 壓垃圾。改以原題的
    phrase/all-terms 判定（與 hybrid_search 共用 classify_match）重算 tier：切題命中
    保留、離題命中降為 TIER_SEMANTIC。fused 不變（仍是 max-over-subqueries 快照）；
    重算後依 (tier, fused) 降序重排。
    """
    phrase, terms = extract_terms(question)
    retiered = [
        (classify_match(phrase, terms, row.content)[0], fused, row)
        for (_tier, fused, row) in merged
    ]
    retiered.sort(key=lambda s: (s[0], s[1]), reverse=True)
    return retiered


async def retrieve_context_multi(
    question: str,
    queries: Sequence[str],
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
    subquery_dense_scan: int | None = None,
    fanout_concurrency: int | None = None,
    total_candidates: int | None = None,
    mmr_lambda: float | None = None,
    mmr_max_per_source: int | None = None,
    mmr_max_per_month: int | None = None,
) -> tuple[list[Source], str]:
    """研報用多查詢檢索：逐條 hybrid_search（受控併發）→ merge_scored →
    合併後單次 rerank（query=原題）→ build_context（gate 快照＋MMR kwargs）。

    queries 首項應為原題（plan_queries 保證）；len<=1＝單查詢等價路徑。原題
    恆用呼叫端 dense_scan、子查詢用 subquery_dense_scan。失敗語意：個別子查詢
    失敗 log＋略過、全部失敗 re-raise 最後例外；CancelledError 一律穿透（不得
    以部分結果續跑）。多查詢且 rerank 未實際套用時（唯一以原題對子查詢召回
    重打分的防線失效）整批降級回原題單查詢結果、gate_scores 不傳。None 的
    旋鈕由 settings 補預設值（研報是唯一呼叫端，預設集中於此）。
    """
    settings = get_settings()
    filters = filters or {}
    qlist = list(queries) or [question]
    sub_scan = (
        subquery_dense_scan
        if subquery_dense_scan is not None
        else settings.report_subquery_dense_scan
    )
    concurrency = (
        fanout_concurrency
        if fanout_concurrency is not None
        else settings.report_fanout_concurrency
    )
    cap = (
        total_candidates
        if total_candidates is not None
        else settings.report_total_candidates
    )
    if mmr_lambda is None:
        mmr_lambda = settings.report_mmr_lambda if settings.report_mmr_enabled else 0.0
    if mmr_max_per_source is None:
        mmr_max_per_source = settings.report_mmr_max_per_source
    if mmr_max_per_month is None:
        mmr_max_per_month = settings.report_mmr_max_per_month

    # embed 序列化：BGE-M3 單例模型不做執行緒併發；短查詢便宜且有 LRU 快取
    vecs = [await asyncio.to_thread(embed_query_cached, q) for q in qlist]
    if timer is not None:
        timer.mark("embed")

    sem = asyncio.Semaphore(max(1, concurrency))
    results: list[list[tuple[int, float, ChunkRow]] | None] = [None] * len(qlist)
    errors: list[Exception] = []

    async def _search_one(idx: int, q_text: str, vec) -> None:
        # 原題（首條）恆用呼叫端 dense_scan（＝現行單查詢行為，也讓降級路徑
        # 真正等於現行掃描深度）；子查詢用較淺的 sub_scan 控總成本。
        per_scan = dense_scan if idx == 0 else sub_scan
        try:
            async with sem:
                async with SessionFactory() as session:  # session 不可跨 task 共用
                    results[idx] = await hybrid_search(
                        session, q_text, vec, k=k, dense_scan=per_scan, **filters
                    )
        except Exception as exc:  # CancelledError 是 BaseException：刻意穿透
            logger.warning(
                "subquery retrieval failed; skipped: %s", q_text, exc_info=True
            )
            errors.append(exc)

    await asyncio.gather(
        *(_search_one(i, q, v) for i, (q, v) in enumerate(zip(qlist, vecs)))
    )
    ok = [r for r in results if r is not None]
    if not ok:
        raise errors[-1]  # 全部失敗＝系統性故障，不吞（等同現行檢索失敗傳播）

    merged = merge_scored(ok, cap=cap)
    if len(qlist) > 1:
        # 合併後以原始主題重算 tier（純 CPU，卸載到執行緒避免凍結 event loop，
        # 比照 rerank/embed；_retier_to_question docstring 說明其防線角色）。
        merged = await asyncio.to_thread(_retier_to_question, question, merged)
    if timer is not None:
        timer.mark("retrieve")

    # gate 快照：rerank 前的 fused（select_reports 的 relevance_floor 以此尺度校準）
    gate_scores: dict[str, float] | None = {
        row.chunk_id: fused for (_tier, fused, row) in merged
    }

    scored: list = merged
    applied = False
    if rerank_top_m > 0:
        timeout = rerank_timeout if rerank_timeout is not None else _RERANK_TIMEOUT
        scored, applied = await _rerank_stage(
            question, scored, top_m=rerank_top_m, timeout=timeout, timer=timer
        )
    if len(qlist) > 1 and not applied:
        # 離題子查詢防線：rerank（唯一以原題重打分、把同 tier 內垃圾壓到尾段者）
        # 未實際套用時——無論逾時、fail-open 回傳同一物件、或 rerank 旗標關閉
        # （rerank_top_m<=0，緊急退場 REPORT_RERANK_ENABLED=0）——合併序只反映
        # 「對各子查詢字面」的 (tier, fused)，離題子查詢的字面命中會排最前。整批
        # 降級回原題單查詢結果（＝現行 retrieve_context fail-open 後行為）。
        primary = results[0]
        if primary is not None:
            logger.warning(
                "multi-query rerank not applied; degrading to primary-query results"
            )
            scored = primary
        else:
            # 原題那條檢索也失敗時無可退，保留合併序（gate 一樣不傳：
            # rerank 未套用，各 chunk fused 未被覆蓋，floor 直接比 fused 等價）
            logger.warning(
                "multi-query rerank not applied and primary query failed; "
                "keeping merged order"
            )
        gate_scores = None

    chunk_embeddings: dict[str, list[float]] = {}
    if mmr_lambda > 0:
        # 每 report 代表 chunk＝最終順序首個 clean_text 非空者——與 select_reports
        # 聚合的 best_chunk_id 規則逐字對齊；兩端取不同 chunk 會使該報告的
        # embedding 缺鍵、冗餘懲罰靜默失效。
        rep_ids: list[str] = []
        seen_reports: set[str] = set()
        for _tier, _fused, row in scored:
            rid = row.report_id
            if rid in seen_reports or not clean_text(row.content):
                continue
            seen_reports.add(rid)
            rep_ids.append(row.chunk_id)
        if rep_ids:
            try:
                async with SessionFactory() as session:
                    chunk_embeddings = await fetch_chunk_embeddings(session, rep_ids)
            except Exception:
                logger.warning(
                    "fetch_chunk_embeddings failed; MMR degraded", exc_info=True
                )
                chunk_embeddings = {}

    # MMR cosine 為純 Python O(候選×已選×1024) CPU：卸載到執行緒，避免在單一
    # asyncio event loop 同步跑而凍結全站併發（比照 rerank/embed；M2 教訓）。
    return await asyncio.to_thread(
        build_context,
        scored,
        max_reports=max_reports,
        max_passages=max_passages,
        max_chars=max_chars,
        now=now,
        gate_scores=gate_scores,
        mmr_lambda=mmr_lambda,
        chunk_embeddings=chunk_embeddings,
        mmr_max_per_source=mmr_max_per_source,
        mmr_max_per_month=mmr_max_per_month,
    )
