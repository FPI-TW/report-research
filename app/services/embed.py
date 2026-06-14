"""BGE-M3 dense 嵌入（1024 維，CPU）。模型延遲載入並快取為單例。"""

from __future__ import annotations

import threading
from functools import lru_cache

_model = None
_lock = threading.Lock()

MODEL_NAME = "BAAI/bge-m3"
EMBED_DIM = 1024


def _get_model():
    global _model
    if _model is None:
        with _lock:
            if _model is None:
                from FlagEmbedding import BGEM3FlagModel

                _model = BGEM3FlagModel(MODEL_NAME, use_fp16=False)
    return _model


def embed_texts(texts: list[str], batch_size: int = 8) -> list[list[float]]:
    """回傳每段文字的 1024 維 dense 向量。"""
    if not texts:
        return []
    model = _get_model()
    out = model.encode(
        texts,
        batch_size=batch_size,
        max_length=1024,
        return_dense=True,
        return_sparse=False,
        return_colbert_vecs=False,
    )
    return [v.tolist() for v in out["dense_vecs"]]


def embed_query(text: str) -> list[float]:
    return embed_texts([text])[0]


@lru_cache(maxsize=256)
def _embed_query_cached(text: str) -> tuple[float, ...]:
    return tuple(embed_texts([text])[0])


def embed_query_cached(text: str) -> list[float]:
    """同 embed_query，但以 LRU 快取重複查詢（CPU 嵌入 ~100-300ms → ~0ms）。"""
    return list(_embed_query_cached(text.strip()))
