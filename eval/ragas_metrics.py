"""Reference-free RAGAS 三指標（自實作，不引入 ragas/langchain）。

judge/embed 皆為注入 callable，故可用 fake 決定性單測、零真 LLM。prompt 與 parser
同檔共置（防漂移）。judge 契約：async judge(system, user) -> dict|list；embed 契約：
embed(text) -> list[float]（1024 維）。
"""

from __future__ import annotations

import asyncio

import numpy as np

# --- Prompt 常數（就地共置；一律要求 JSON-only 輸出）---

DECOMPOSE_SYS = (
    "你是 RAG 評測助手。把下列『回答』拆解成一組獨立、原子的事實主張（statement）。"
    "每個主張須可獨立判斷真偽，不含連接詞堆疊。若回答只是『找不到資料』之類、"
    "未提出任何事實主張，回空陣列。\n"
    '只輸出 JSON，格式：{"statements": ["主張1", "主張2", ...]}，不要任何其他文字。'
)

GROUND_SYS = (
    "你是 RAG 忠實度評審。給定『參考片段』與一組『主張』，逐一判斷每個主張是否"
    "能由參考片段直接佐證支持（supported）。只依片段內容判斷，不用外部知識；片段沒說到、"
    "或與片段矛盾，一律 supported=false。\n"
    '只輸出 JSON，格式：{"verdicts": [{"idx": 0, "supported": true}, ...]}，'
    "idx 對應主張的 0-based 序號，不要任何其他文字。"
)

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


async def faithfulness(answer: str, contexts: list[str], *, judge) -> float | None:
    """拆解回答為主張 → 逐條佐證於 contexts。分數 = supported/total；total==0 → None。"""
    dec = await judge(DECOMPOSE_SYS, answer)
    statements = dec.get("statements") if isinstance(dec, dict) else None
    statements = [s for s in (statements or []) if isinstance(s, str) and s.strip()]
    if not statements:
        return None  # 無事實主張（如「找不到資料」）：自均值排除，不以空洞值灌水
    joined_ctx = "\n\n".join(contexts)
    enumerated = "\n".join(f"{i}. {s}" for i, s in enumerate(statements))
    payload = f"參考片段：\n{joined_ctx}\n\n主張：\n{enumerated}"
    res = await judge(GROUND_SYS, payload)
    verdicts = res.get("verdicts") if isinstance(res, dict) else None
    supmap: dict[int, bool] = {}
    for v in verdicts or []:
        if isinstance(v, dict) and isinstance(v.get("idx"), int):
            idx = v["idx"]
            if 0 <= idx < len(statements):
                supmap[idx] = v.get("supported") is True
    supported = sum(1 for ok in supmap.values() if ok)
    return supported / len(statements)


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


async def answer_relevancy(question: str, answer: str, *, judge, embed) -> float:
    """由回答反推 3 個問題 → 各與原問題的 embedding cosine 取平均。無反推問題 → 0.0。"""
    out = await judge(GENQ_SYS, answer)
    gen = out.get("questions") if isinstance(out, dict) else None
    gen = [q for q in (gen or []) if isinstance(q, str) and q.strip()]
    if not gen:
        return 0.0
    qv = await asyncio.to_thread(embed, question)
    sims = []
    for g in gen:
        gv = await asyncio.to_thread(embed, g)
        sims.append(_cosine(qv, gv))
    return sum(sims) / len(sims)
