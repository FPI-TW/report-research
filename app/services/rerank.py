"""BGE cross-encoder 重排（CPU）。模型延遲載入並快取為單例（比照 embed.py）。

插在 hybrid_search 之後、build_context 之前（見 retrieval_pipeline）。同 tier 內重排：
rerank 分 sigmoid 正規化 [0,1] 覆蓋被重排候選的 fused、tier 保留；尾段維持原分數保 recall。
fail-open：載入/推論失敗回原融合序。

後端用 transformers 直跑 cross-encoder（AutoModelForSequenceClassification），
非 FlagEmbedding.FlagReranker——後者在 transformers>=5 呼叫已移除的
tokenizer.prepare_for_model 而 AttributeError（embed 的 BGEM3FlagModel 走另一路徑不受影響）。
transformers/torch 皆既有依賴，故仍零新增。
"""

from __future__ import annotations

import logging
import math
import threading

from app.config import get_settings

logger = logging.getLogger(__name__)

_model = None
_load_failed = False  # 熔斷：載入失敗一次後不再重試（否則每次請求都重載+再阻塞）
_lock = threading.Lock()


class _CrossEncoder:
    """bge-reranker-v2-m3 cross-encoder（CPU）。compute_score 介面比照 FlagReranker，
    使 rerank_scores 與其單測（注入 fake model）不受後端替換影響。"""

    def __init__(self, model_name: str, max_length: int = 512):
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        self._torch = torch
        self._max_length = max_length
        self._tokenizer = AutoTokenizer.from_pretrained(model_name)  # fast tokenizer
        self._model = AutoModelForSequenceClassification.from_pretrained(model_name)
        self._model.eval()

    def compute_score(self, pairs, normalize: bool = False) -> list[float]:
        """回每個 (query, passage) pair 的分數；normalize=True 走 sigmoid → [0,1]。恆回 list。"""
        torch = self._torch
        with torch.no_grad():
            inputs = self._tokenizer(
                list(pairs),
                padding=True,
                truncation=True,
                max_length=self._max_length,
                return_tensors="pt",
            )
            logits = self._model(**inputs, return_dict=True).logits.view(-1).float()
            if normalize:
                logits = torch.sigmoid(logits)
            return logits.tolist()


def _get_model():
    """回單例 cross-encoder；載入失敗回 None 並熔斷（不再重試），呼叫端 fail-open。"""
    global _model, _load_failed
    if _model is not None:
        return _model
    if _load_failed:
        return None  # 已知不可用：短路，不重載、不再阻塞
    with _lock:
        if _model is not None:
            return _model
        if _load_failed:
            return None
        try:
            _model = _CrossEncoder(get_settings().rerank_model)  # CPU
        except Exception:
            _load_failed = True
            logger.warning("rerank model load failed; disabling rerank", exc_info=True)
            return None
    return _model


def rerank_scores(query: str, passages: list[str]) -> list[float]:
    """回每個 passage 對 query 的相關度 ∈ [0,1]（sigmoid 正規化）。

    空 passages → []；模型不可用（載入失敗/已熔斷）→ [] → rerank_scored 以形狀不符 fail-open。
    compute_score 恆回 list；保留 float→list 防禦以相容注入 fake 的單測。
    """
    if not passages:
        return []
    model = _get_model()
    if model is None:
        return []
    raw = model.compute_score([(query, p) for p in passages], normalize=True)
    if isinstance(raw, (int, float)):
        raw = [raw]
    return [float(s) for s in raw]


def rerank_scored(question, scored, *, top_m, timer=None):
    """對 scored 前 top_m 個重排：rerank 分覆蓋 fused、tier/row 保留，head 依
    (tier, rerank 分) 皆降序重排——tier 硬性優先（字面命中永在語意之上），同 tier
    內取最相關者先入，故 select_reports 每報告前 max_passages 段落即重排後最相關者。
    尾段（top_m 之後）保留原 fused 分數與相對序，避免與 select_reports 的門檻尺度不相容。
    推論失敗/形狀不符（含模型不可用回 []）/非有限分數(NaN/inf)/退化 → 回原 scored（fail-open）。
    """
    if not scored or top_m <= 0:
        return scored
    try:
        head = scored[:top_m]
        tail = scored[top_m:]
        scores = rerank_scores(question, [row.content for (_t, _f, row) in head])
        if len(scores) != len(head) or not all(math.isfinite(s) for s in scores):
            return scored  # 形狀不符（含模型不可用）/NaN/inf：fail-open
        reranked = sorted(
            ((tier, scores[i], row) for i, (tier, _f, row) in enumerate(head)),
            key=lambda x: (x[0], x[1]),
            reverse=True,
        )
        if timer is not None:
            timer.mark("rerank")
        return reranked + tail
    except Exception:
        logger.warning("rerank failed, fall back to fused order", exc_info=True)
        return scored
