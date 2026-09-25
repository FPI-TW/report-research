"""Reference-free RAGAS 三指標（自實作，不引入 ragas/langchain）。

judge/embed 皆為注入 callable，故可用 fake 決定性單測、零真 LLM。prompt 與 parser
同檔共置（防漂移）。judge 契約：async judge(system, user) -> dict|list；embed 契約：
embed(text) -> list[float]（1024 維）。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass

import numpy as np

# faithfulness 的 claim 拆解／grounding 與其 prompt 已上升到生產層 app/services，
# 供研報/問答的 M8 查核與本評測共用（單一真相、防漂移）。本檔只保留評測專用的
# context_precision / answer_relevancy。
from app.services.faithfulness import (  # noqa: F401  (DECOMPOSE_SYS/GROUND_SYS 供既有測試 import)
    DECOMPOSE_SYS,
    GROUND_ITEM_FMT,
    GROUND_PAYLOAD_FMT,
    GROUND_SYS,
    faithfulness,
)
from app.services.faithfulness import JUDGE_MAX_TOKENS_BY_SYSTEM as _PROD_MAX_TOKENS
from app.services.judge_schema import (
    JudgeSchemaError,
    call_validated,
    parse_questions,
    parse_verdicts,
)

# --- Prompt 常數（評測專用；一律要求 JSON-only 輸出）---

CTX_RELEVANCE_SYS = (
    "你是 RAG 檢索精準度評審。給定『問題』『回答』與一組候選片段，逐一判斷每個片段"
    "是否與回答此問題相關（relevant）——即該片段是否提供了回答問題所需的資訊。\n"
    '只輸出 JSON，格式：{"verdicts": [{"idx": 1, "relevant": true}, ...]}，'
    "idx 為候選片段開頭方括號內的編號（從 1 起算，與回答中的引用編號相同），"
    "每個片段恰好一筆，不要任何其他文字。"
)

GENQ_SYS = (
    "你是 RAG 答案相關性評審。閱讀下列『回答』，反推它最可能在回答的 3 個問題"
    "（假設你沒看過原問題）。問題須具體、可獨立理解。\n"
    '只輸出 JSON，格式：{"questions": ["問題1", "問題2", "問題3"]}，不要任何其他文字。'
)

# context_precision 的 user payload 版型（理由同 faithfulness.GROUND_PAYLOAD_FMT）。
#
# **候選片段 1 起編號，並剝掉片段自帶的 `[n] ` 前綴**（審查 M12，schema v2）。v1 是外層
# `[i]`（0 起）套在已經帶 `[n] 報告：`（1 起，answer.build_context 的編號）的片段外面，
# 同一個片段身上有兩個差一的號碼，而回答裡的引用 `[n]` 用的是內層那個——judge 只要照
# 引用編號填 idx，就整體錯位一格，v1 不報錯、只靜默算出錯的 CP。選「統一」而不是只剝
# 內層：只剝內層仍留下 0 起的外層編號與回答的 1 起引用互相矛盾；統一成片段自己的號碼
# 之後，候選標籤、回答引用、idx 三者是同一個數字，judge 看哪一個填都對。
CP_ITEM_FMT = "[{n}] {c}"
_LEADING_CITE_RE = re.compile(r"^\[\d+\]\s*")
CP_PAYLOAD_FMT = "問題：{question}\n\n回答：{answer}\n\n候選片段：\n{contexts}"

# 算進 `judge_prompt_sha` 的全部模板：四支系統提示＋兩種 payload 版型。decompose 與
# GENQ 的 user 端就是答案原文，沒有版型。**新增或改動任何一支 judge 提示都要進這張表**，
# 否則雜湊不變、eval_compare 會把兩把尺當成同一把。順序與名稱也算進雜湊。
JUDGE_PROMPT_TEMPLATES: tuple[tuple[str, str], ...] = (
    ("decompose_sys", DECOMPOSE_SYS),
    ("ground_sys", GROUND_SYS),
    ("ground_item", GROUND_ITEM_FMT),
    ("ground_payload", GROUND_PAYLOAD_FMT),
    ("context_precision_sys", CTX_RELEVANCE_SYS),
    ("context_precision_item", CP_ITEM_FMT),
    ("context_precision_payload", CP_PAYLOAD_FMT),
    ("answer_relevancy_sys", GENQ_SYS),
)


# 各階段的輸出上限（HTTP judge；第二版計畫 §6.5）：拆解 8192、grounding 2048 沿用生產那兩支，
# CP 2048、反推問題 1024。judge 契約分不出階段，run_ragas 依系統提示查這張表傳給 `judge_json`。
# 不算進 judge_prompt_sha：它只決定會不會截斷（截斷另以 2 倍上限重試），不改變判分規則。
CP_MAX_TOKENS = 2048
GENQ_MAX_TOKENS = 1024
JUDGE_MAX_TOKENS_BY_SYSTEM: dict[str, int] = {
    **_PROD_MAX_TOKENS,
    CTX_RELEVANCE_SYS: CP_MAX_TOKENS,
    GENQ_SYS: GENQ_MAX_TOKENS,
}


def judge_prompt_sha() -> str:
    """judge 提示模板的 sha256（hex）。離線評測記進 summary 的 META 鍵 `judge_prompt_sha`。"""
    blob = json.dumps(JUDGE_PROMPT_TEMPLATES, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


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


def context_precision_payload(question: str, answer: str, contexts: list[str]) -> str:
    """CP 的 user payload：候選片段依序標 [1]..[n]，片段自帶的前導 [n] 剝掉（見 CP_ITEM_FMT）。"""
    enumerated = "\n\n".join(
        CP_ITEM_FMT.format(n=i, c=_LEADING_CITE_RE.sub("", c, count=1))
        for i, c in enumerate(contexts, start=1)
    )
    return CP_PAYLOAD_FMT.format(question=question, answer=answer, contexts=enumerated)


async def context_precision(
    question: str, answer: str, contexts: list[str], *, judge
) -> float:
    """逐 context 判相關性 → rank-weighted AP。無 context → 0.0。

    schema v2：idx 必須恰好是 1..len(contexts)，relevant 必須是 bool；不合格重試 1 次後拋
    JudgeSchemaError（run_ragas 記成該指標的 judge_errors）。
    """
    if not contexts:
        return 0.0
    payload = context_precision_payload(question, answer, contexts)
    parsed = await call_validated(
        judge, CTX_RELEVANCE_SYS, payload,
        lambda res: parse_verdicts(res, len(contexts), "relevant", first=1),
    )
    if parsed is None:
        raise JudgeSchemaError("judge 沒有回應")
    relmap = parsed[0]
    rel = [1 if relmap[i] else 0 for i in range(len(contexts))]
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
    """由回答反推 3 個問題 → 各與原問題的 embedding cosine 取平均，連中間產物一起回。

    schema v2：取不到任何問題是 schema 錯（重試 1 次後拋 JudgeSchemaError），不再記 0.0——
    「judge 沒答」與「答非所問」在 v1 是同一個分數。
    """
    gen = await call_validated(judge, GENQ_SYS, answer, parse_questions)
    if gen is None:
        raise JudgeSchemaError("judge 沒有回應")
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
