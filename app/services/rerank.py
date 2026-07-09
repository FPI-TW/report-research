"""BGE cross-encoder 重排（CPU）。模型延遲載入並快取為單例（比照 embed.py）。

插在 hybrid_search 之後、build_context 之前（見 retrieval_pipeline）。同 tier 內重排：
rerank 分 sigmoid 正規化 [0,1] 覆蓋被重排候選的 fused、tier 保留；尾段壓縮保 recall。
fail-open：載入/推論失敗回原融合序。
"""

from __future__ import annotations

import logging
import threading

from app.config import get_settings

logger = logging.getLogger(__name__)

_model = None
_lock = threading.Lock()


def _get_model():
    global _model
    if _model is None:
        with _lock:
            if _model is None:
                from FlagEmbedding import FlagReranker

                _model = FlagReranker(get_settings().rerank_model, use_fp16=False)  # CPU
    return _model


def rerank_scores(query: str, passages: list[str]) -> list[float]:
    """回每個 passage 對 query 的相關度 ∈ [0,1]（sigmoid 正規化）。空 → []。

    FlagReranker.compute_score 單一 pair 回 float、多 pair 回 list，統一成 list[float]。
    """
    if not passages:
        return []
    model = _get_model()
    raw = model.compute_score([(query, p) for p in passages], normalize=True)
    if isinstance(raw, (int, float)):
        raw = [raw]
    return [float(s) for s in raw]


def rerank_scored(question, scored, *, top_m, timer=None):
    """對 scored 前 top_m 個（依現行序）重排：rerank 分覆蓋 fused、tier/row 保留；
    尾段壓縮到嚴格低於最低重排分並保相對序（守 recall、同 tier 內不反超）。
    任何例外/退化/形狀不符 → 回原 scored（fail-open）。
    """
    if not scored or top_m <= 0:
        return scored
    try:
        head = scored[:top_m]
        tail = scored[top_m:]
        scores = rerank_scores(question, [row.content for (_t, _f, row) in head])
        if len(scores) != len(head):
            return scored  # 形狀不符：fail-open
        reranked = [(tier, scores[i], row) for i, (tier, _f, row) in enumerate(head)]
        if tail:
            min_rr = min(scores)
            ceiling = min_rr * 0.99  # 嚴格低於最低重排分
            tail_fused = [f for (_t, f, _r) in tail]
            lo, hi = min(tail_fused), max(tail_fused)
            span = hi - lo
            tail = [
                (tier, (ceiling if span == 0 else ceiling * (f - lo) / span), row)
                for (tier, f, row) in tail
            ]
        if timer is not None:
            timer.mark("rerank")
        return reranked + tail
    except Exception:
        logger.warning("rerank failed, fall back to fused order", exc_info=True)
        return scored
