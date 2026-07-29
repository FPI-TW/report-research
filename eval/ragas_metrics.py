"""Reference-free RAGAS 三指標（自實作，不引入 ragas/langchain）。

judge/embed 皆為注入 callable，故可用 fake 決定性單測、零真 LLM。prompt 與 parser
同檔共置（防漂移）。judge 契約：async judge(system, user) -> dict|list；embed 契約：
embed(text) -> list[float]（1024 維）。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import numpy as np

# faithfulness 的 claim 拆解／grounding 與其 prompt 已上升到生產層 app/services，
# 供研報/問答的 M8 查核與本評測共用（單一真相、防漂移）。本檔只保留評測專用的
# context_precision / answer_relevancy。
from app.services.faithfulness import (  # noqa: F401  (DECOMPOSE_SYS/GROUND_SYS 供既有測試 import)
    DECOMPOSE_SYS,
    GROUND_SYS,
    faithfulness,
)

# --- Prompt 常數（評測專用；一律要求 JSON-only 輸出）---

CTX_RELEVANCE_SYS = (
    "你是 RAG 檢索精準度評審。給定『問題』『回答』與一組候選片段，逐一判斷每個片段"
    "是否與回答此問題相關（relevant）——即該片段是否提供了回答問題所需的資訊。\n"
    '只輸出 JSON，格式：{"verdicts": [{"idx": 0, "relevant": true}, ...]}，'
    "idx 對應候選片段的 0-based 序號，不要任何其他文字。"
)

GENQ_SYS = (
    "你是 RAG 答案相關性評審。閱讀下列『回答』，反推它最可能在回答的 3 個問題"
    "（假設你沒看過原問題）。問題須具體、可獨立理解。\n"
    '只輸出 JSON，格式：{"questions": ["問題1", "問題2", "問題3"]}，不要任何其他文字。'
)


def _cosine(a, b) -> float:
    """兩向量餘弦相似度；任一為零向量 → 0.0。"""
    va = np.asarray(a, dtype=float)
    vb = np.asarray(b, dtype=float)
    na = float(np.linalg.norm(va))
    nb = float(np.linalg.norm(vb))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(np.dot(va, vb) / (na * nb))


def _average_precision(rel: list[int]) -> float:
    """RAGAS rank-weighted AP：sum_k(precision@k * rel_k) / total_relevant。無相關 → 0.0。"""
    total_relevant = sum(rel)
    if total_relevant == 0:
        return 0.0
    score = 0.0
    hits = 0
    for k, r in enumerate(rel, start=1):
        if r:
            hits += 1
            score += hits / k
    return score / total_relevant


async def context_precision(
    question: str, answer: str, contexts: list[str], *, judge
) -> float:
    """逐 context 判相關性 → rank-weighted AP。無 context → 0.0。"""
    if not contexts:
        return 0.0
    enumerated = "\n\n".join(f"[{i}]\n{c}" for i, c in enumerate(contexts))
    payload = f"問題：{question}\n\n回答：{answer}\n\n候選片段：\n{enumerated}"
    res = await judge(CTX_RELEVANCE_SYS, payload)
    verdicts = res.get("verdicts") if isinstance(res, dict) else None
    relmap: dict[int, int] = {}
    for v in verdicts or []:
        if isinstance(v, dict) and isinstance(v.get("idx"), int):
            relmap[v["idx"]] = 1 if v.get("relevant") is True else 0
    rel = [relmap.get(i, 0) for i in range(len(contexts))]
    return _average_precision(rel)


@dataclass(frozen=True)
class RelevancyDetail:
    """answer_relevancy 的分數與**中間產物**。

    baseline 先前只存最終分數，所以「AR 為什麼是 0.64」無法事後回答——只能重跑一次
    完整評測（judge + BGE-M3）才看得到反推出什麼問題。這個 dataclass 就是為了讓
    下一次診斷不必重跑：questions 與 sims 一併落進結果檔。
    """

    score: float
    questions: tuple[str, ...] = ()
    sims: tuple[float, ...] = ()


async def answer_relevancy_detailed(
    question: str, answer: str, *, judge, embed
) -> RelevancyDetail:
    """由回答反推 3 個問題 → 各與原問題的 embedding cosine 取平均，連中間產物一起回。"""
    out = await judge(GENQ_SYS, answer)
    gen = out.get("questions") if isinstance(out, dict) else None
    gen = [q for q in (gen or []) if isinstance(q, str) and q.strip()]
    if not gen:
        return RelevancyDetail(0.0)
    qv = await asyncio.to_thread(embed, question)
    sims = []
    for g in gen:
        gv = await asyncio.to_thread(embed, g)
        sims.append(_cosine(qv, gv))
    return RelevancyDetail(sum(sims) / len(sims), tuple(gen), tuple(sims))


async def answer_relevancy(question: str, answer: str, *, judge, embed) -> float:
    """只要分數的舊介面（既有測試與外部呼叫沿用）。"""
    return (
        await answer_relevancy_detailed(question, answer, judge=judge, embed=embed)
    ).score
