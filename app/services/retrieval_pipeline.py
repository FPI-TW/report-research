"""問答的檢索封裝：embed → hybrid_search → build_context。

僅此 helper 收斂三步序列；檢索頁分頁（rank_reports）不使用。M2 的 reranker
插在 hybrid_search 之後、build_context 之前。
"""

import asyncio
import logging
import os
import sys
import time

from app.services.answer import Source, build_context, selection_order
from app.services.db import SessionFactory
from app.services.embed import embed_query_cached
from app.services.rerank import rerank_scored
from app.services.retrieval import hybrid_search
from app.services.textnorm import clean_text

logger = logging.getLogger(__name__)
# 名額對齊 _ASK_GATE 的容量 3：只有 1 個名額時，同時提問的第 2、3 人各自要多等一輪
# 完整 rerank（prod 實測單輪 40s），而那段等待不在任何逾時預算內、只表現為「更慢」。
# 注意這裡沒有連帶限制 torch 的執行緒數，3 輪並行在核心數不足的機器上會互相搶。
_RERANK_WORKERS = max(1, int(os.getenv("REPORT_MARK_RERANK_WORKERS", "3")))
# 呼叫端未給 rerank_timeout 時的後備值（answer 帶 Settings 的 per-path 逾時）。
_RERANK_TIMEOUT = float(os.getenv("REPORT_MARK_RERANK_TIMEOUT", "30"))
_rerank_semaphore = asyncio.Semaphore(_RERANK_WORKERS)


async def _run_rerank(question, scored, *, top_m, timer, deadline, stats=None):
    """執行緒工作由此 task 持有 semaphore；呼叫端取消不會釋放仍在跑的 CPU 工作，
    但 deadline 使被放棄的工作在批次邊界提早收手（釋放 CPU 與 semaphore）。"""
    queued_at = time.monotonic()
    async with _rerank_semaphore:
        started_at = time.monotonic()
        if stats is not None:
            stats["rerank_queue_ms"] = round((started_at - queued_at) * 1000, 1)
        result = await asyncio.to_thread(
            rerank_scored, question, scored, top_m=top_m, timer=timer,
            deadline=deadline,
        )
        if stats is not None:
            stats["rerank_compute_ms"] = round((time.monotonic() - started_at) * 1000, 1)
        return result


def _consume_rerank_result(task: asyncio.Task) -> None:
    """讀取背景重排結果，避免逾時或取消後的例外成為未取用 task exception。"""
    try:
        task.result()
    except asyncio.CancelledError:
        pass
    except Exception:
        logger.debug("background rerank failed", exc_info=True)


async def _rerank_stage(
    question, scored, *, top_m, timeout, timer, stats=None
) -> tuple[list, bool]:
    """單次 rerank 段（fail-open）：回 (scored, applied)。

    applied=False ＝ rerank 未實際套用——(a) asyncio.wait_for 逾時；或
    (b) rerank_scored 回傳「與輸入同一 list 物件」（其 fail-open 契約，見
    rerank.rerank_scored docstring）。retrieve_context 據此決定是否傳 gate_scores。
    逾時預算含排隊等待 semaphore。
    """
    task = asyncio.create_task(
        _run_rerank(
            question, scored, top_m=top_m, timer=timer,
            deadline=time.monotonic() + timeout,
            stats=stats,
        )
    )
    if stats is not None:
        stats["rerank_timed_out"] = False
    try:
        reranked = await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
    except TimeoutError:
        if stats is not None:
            stats["rerank_timed_out"] = True
            stats["rerank_applied"] = False
        logger.warning("rerank timed out; using fused ranking")
        task.add_done_callback(_consume_rerank_result)
        return scored, False
    except asyncio.CancelledError:
        task.add_done_callback(_consume_rerank_result)
        raise
    applied = reranked is not scored
    if stats is not None:
        stats["rerank_applied"] = applied
    return reranked, applied


def rerank_head_first(scored, *, top_m, max_passages, now=None):
    """把「選篇政策會接納的研報」的 chunk 排到最前面，回 (scored, head 筆數)；head＝其中融合序前 top_m 個。

    rerank 只重排前 top_m 個 chunk（問答 16）。舊做法直接取融合序的前 top_m 個，2026-11-10 在固定
    語料快照（devdb-2026-10-28）上對凍結題集 18 題逐題拆解：那 16 個 chunk 多半屬於一兩年前的研報，
    選篇時被過舊軟截斷整批剔除；入選的約 234 個脈絡只有 31 個被重排過，其餘在同一個 fused band 裡
    只照新近度排，cross-encoder 幾乎不影響脈絡。例：「台積電最新的營運展望」head 16 個 chunk 有 15 個
    是 2024–2025 年，入選 15 篇只有 1 篇被重排。

    接納與否直接問選篇政策（`answer.selection_order`，篇數與字數不設限：只要知道哪些研報不會被
    過舊軟截斷、相關度下限或過舊配額永久剔除），不另寫一份判準。重排配對數不變（延遲不變）；
    其餘 chunk 相對序不變，報告內段落順序與代表 chunk 也不變，所以不帶 rerank_scores 時
    build_context 的結果與原 scored 逐字相同。

    不需要挪動時回傳輸入的同一個 list 物件；任何例外＝回 (原 scored, min(top_m, len))，即舊行為
    （fail-open）。
    """
    try:
        admitted = set(selection_order(
            scored, max_reports=len(scored), max_passages=max_passages, max_chars=sys.maxsize, now=now,
        ))
        head_idx = [
            i for i, (_t, _f, row) in enumerate(scored)
            if row.report_id in admitted and clean_text(row.content)
        ][:top_m]
        if head_idx == list(range(len(head_idx))):
            return scored, len(head_idx)  # 已在最前面：回同一物件（逾時降級時 build_context 收到原 scored）
        taken = set(head_idx)
        arranged = [scored[i] for i in head_idx] + [s for i, s in enumerate(scored) if i not in taken]
        return arranged, len(head_idx)
    except Exception:
        logger.warning("rerank_head_first failed; rerank uses fused head", exc_info=True)
        return scored, min(top_m, len(scored))


async def ask_rerank_top_m(configured: int, user_id: str | None = None) -> int:
    """問答路徑這一題的 rerank 候選數：`configured`（`answer.ASK_RERANK_TOP_M`，環境變數 `ASK_RERANK_ENABLED`
    關時為 0＝上限關）AND 功能旗標 `ask.rerank`（Admin v2 的降級開關；DB 沒有覆寫或讀不到＝開，行為不變）。

    只給問答路徑用：`retrieve_context` 本身不讀旗標——評測（`eval/run_ragas.py`）
    以 `rerank_top_m` 明確指定 rerank 開關，線上降級不可以靜默改掉它的設定（批次不讀 web 的旗標，設計 §1.4）。
    """
    if configured <= 0:
        return 0
    from app.services import feature_flags  # 函式內 import：與 answer 之間的取用一律如此（循環依賴是刻意的）

    return configured if await feature_flags.policy("ask.rerank", user_id) else 0


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
    而選篇的 relevance_floor 是以 fused 校準的（詳見下方註解）。重排的對象是選篇會接納的研報裡
    融合序前 rerank_top_m 個 chunk（rerank_head_first），重排成功時另把分數當 rerank_scores
    傳給選篇排序。
    rerank_timeout 未給時退回模組後備值；逾時預算含排隊等待 semaphore 的時間。

    `stats` 給定時原樣轉給 hybrid_search 填寫字面路召回遙測（lex_hits／lex_cap／
    lex_truncated）與兩路的分段耗時（dense_ms／lex_ms，毫秒；字面路未執行時
    lex_ms 為 0），另記 rerank_queue_ms、rerank_compute_ms、rerank_applied、
    rerank_timed_out。compute 包含 asyncio.to_thread 排程等待；逾時後背景工作仍
    可能補寫 compute。由呼叫端決定要不要記錄——本函式不 log，避免資訊重複。"""
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
    rerank_scores: dict[str, float] | None = None
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
        # head 只從選篇會接納的研報取（見 rerank_head_first）；gate 快照在挪動前取，內容相同。
        scored, head_n = rerank_head_first(
            scored, top_m=rerank_top_m, max_passages=max_passages, now=now,
        )
        scored, applied = await _rerank_stage(
            question, scored, top_m=head_n, timeout=timeout, timer=timer, stats=stats,
        )
        if not applied:
            # 未套用＝fused 未被覆蓋，gate 直接比 fused 等價；傳 None 讓「有沒有
            # 發生覆寫」在呼叫端可讀，與 retrieve_context_multi 的降級處理一致。
            gate_scores = None
        else:
            # 重排分另傳給選篇排序（select_reports 的 rerank_scores）：重排分是 sigmoid、
            # 尾段是 fused，不分開的話尾段會整批排到 head 前面。取不到＝原排序（fail-open）。
            try:
                rerank_scores = {row.chunk_id: s for (_t, s, row) in scored[:head_n]}
            except Exception:
                logger.warning("rerank head scores unavailable; selection keeps fused ordering", exc_info=True)
    return build_context(
        scored,
        max_reports=max_reports,
        max_passages=max_passages,
        max_chars=max_chars,
        now=now,
        gate_scores=gate_scores,
        rerank_scores=rerank_scores,
    )
