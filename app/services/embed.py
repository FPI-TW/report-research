"""BGE-M3 dense 嵌入（1024 維，CPU）。模型延遲載入並快取為單例。

**`_build_lock` 只保護「建構」，`_encode_gate` 才保護「推論」——兩者不可混用。**
先前只有前者，於是：每個 web 呼叫端都經 `asyncio.to_thread` 丟進預設執行緒池
（`min(32, cpu+4)` 條），而 `/api/search`／雷達／閱讀頁**完全沒有併發閘**，所以
同時進 `model.encode` 的執行緒數沒有上界。CPU-only 推論下 torch 自己還會再開
intra-op 執行緒，兩層相乘就是嚴重超額訂閱——症狀不是錯誤，是**每一條都變慢**，
而且量測起來像「BGE-M3 本來就慢」。

`EMBED_MAX_CONCURRENCY` 預設 1（序列化）。CPU-bound 工作序列化不損總吞吐（反而因
為少了搶核而變快），代價只是併發請求的尾延遲，而那本來就被超額訂閱吃掉了。
"""

from __future__ import annotations

import logging
import os
import threading
from functools import lru_cache

from app.config import get_settings

logger = logging.getLogger(__name__)

_model = None
_build_lock = threading.Lock()

MODEL_NAME = "BAAI/bge-m3"
EMBED_DIM = 1024


def _make_encode_gate() -> threading.Semaphore:
    """推論閘。容量取自設定，`<= 0` 視為 1——0 的語意會是「誰都不准跑」。"""
    n = int(get_settings().embed_max_concurrency)
    return threading.Semaphore(max(1, n))


_encode_gate = _make_encode_gate()


def _get_model():
    global _model
    if _model is None:
        with _build_lock:
            if _model is None:
                # 必須在匯入 FlagEmbedding（連帶 tokenizers）**之前**設：
                # HF tokenizers 只在首次匯入時讀這個變數，之後改沒有作用。
                # 關掉它是因為上層已經在多執行緒裡跑，再開一層 rust 執行緒只是搶核
                # （也順帶消掉 fork 之後那個 "The current process just got forked" 警告）。
                os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
                from FlagEmbedding import BGEM3FlagModel

                threads = int(get_settings().embed_torch_threads)
                if threads > 0:
                    import torch

                    torch.set_num_threads(threads)
                    logger.info("torch.set_num_threads(%s)", threads)
                _model = BGEM3FlagModel(MODEL_NAME, use_fp16=False)
    return _model


def embed_texts(texts: list[str], batch_size: int = 8) -> list[list[float]]:
    """回傳每段文字的 1024 維 dense 向量。

    `_get_model()` 刻意在閘**之外**：首次載入要數十秒（含下載 2-4GB），把它圈進閘裡
    等於讓第一個請求把後面全部擋住那麼久。建構自己有 `_build_lock` 保護。
    """
    if not texts:
        return []
    model = _get_model()
    with _encode_gate:
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
