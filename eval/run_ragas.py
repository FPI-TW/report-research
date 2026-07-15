"""M1 eval 編排：讀題集 → 逐題 retrieve→generate→三指標 → aggregate → 寫基準線。

生成端刻意用 build_user_prompt + stream_completion（非 answer_question）以隔離檢索與
生成、避開 qa_log 寫入/overview/off-topic。有界併發（claude CLI spawn 吃 IO）、逐題
fail-open（任一階段異常記 error、不計均值、不中斷批次）。

M5 起：每 case 記 latency_ms（檢索＋生成牆鐘，排除 judge）；--agentic 走 agentic
迴圈評測（plan 並行→run_agentic 合併，記 rounds/subqueries_run/skipped 歸因欄位，
補查全部排同一個 rerank semaphore，故一律強制 concurrency=1）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.agentic_qa import run_agentic  # noqa: E402
from app.services.answer import (  # noqa: E402
    ASK_DENSE_SCAN,
    ASK_RERANK_TIMEOUT,
    MAX_CONTEXT_CHARS,
    MAX_PASSAGES_PER_REPORT,
    MAX_REPORTS,
    RETRIEVAL_K,
    SYSTEM_PROMPT,
    build_user_prompt,
)
from app.services.embed import embed_query_cached  # noqa: E402
from app.services.llm import DEFAULT_MODEL, SEARCH_EVENT, stream_completion  # noqa: E402
from app.services.query_planner import plan_queries  # noqa: E402
from app.services.retrieval_pipeline import retrieve_context  # noqa: E402
from app.services.scope_router import CORPUS_QA, POLICY_FOR_SCOPE, RouteDecision  # noqa: E402
from eval.judge import DEFAULT_JUDGE_MODEL, judge_json  # noqa: E402
from eval.ragas_metrics import answer_relevancy, context_precision, faithfulness  # noqa: E402

RETRIEVAL_PARAMS = {
    "k": RETRIEVAL_K,
    "dense_scan": ASK_DENSE_SCAN,
    "max_reports": MAX_REPORTS,
    "max_passages": MAX_PASSAGES_PER_REPORT,
    "max_chars": MAX_CONTEXT_CHARS,
}

_CTX_SPLIT_RE = re.compile(r"(?=^\[\d+\] )", re.MULTILINE)

# 題集已全標 corpus_qa（凍結題集），--agentic 固定注入此路由決策（設計 §7.2）。
_CORPUS_QA_DECISION = RouteDecision(
    scope=CORPUS_QA, tool_policy=POLICY_FOR_SCOPE[CORPUS_QA]
)

FAITHFULNESS_MIN = 0.9
CONTEXT_PRECISION_MIN = 0.8
ANSWER_RELEVANCY_MIN = 0.85


def split_contexts(context: str) -> list[str]:
    """把 build_context 的編號脈絡字串以 [n] 表頭切成每篇一塊。空 → []。"""
    if not context.strip():
        return []
    return [p.strip() for p in _CTX_SPLIT_RE.split(context) if p.strip()]


async def _generate_answer(question: str, context: str) -> str:
    """以 build_user_prompt + stream_completion 生成答案（跳過 SEARCH_EVENT 控制標記）。"""
    prompt = build_user_prompt(question, context)
    parts: list[str] = []
    async for chunk in stream_completion(prompt, system=SYSTEM_PROMPT, model=DEFAULT_MODEL):
        if chunk == SEARCH_EVENT:
            continue
        parts.append(chunk)
    return "".join(parts)


async def eval_question(
    q: dict, *, judge, embed, retrieval_params: dict, agentic: bool = False
) -> dict:
    """單題：retrieve→generate→三指標。任一階段異常 → {..., "error": str}（fail-open）。

    latency_ms 量測「檢索＋生成」牆鐘（agentic 時含規劃/評估/補查；排除 judge）。
    agentic=True 走 M5 流程：plan_queries（profile="qa"）與第一輪 retrieve_context
    並行 → run_agentic（忽略 stage 事件、取 outcome 的合併結果）→ 以合併 context
    生成，並記 rounds/subqueries_run/skipped 歸因欄位（補查是否被跳過可歸因）。
    """
    base = {"id": q.get("id"), "question": q.get("question")}
    try:
        question = q["question"]
        filters = q.get("filters") or {}
        t0 = time.monotonic()
        outcome = None
        if agentic:
            plan_task = asyncio.create_task(plan_queries(question, profile="qa"))
            try:
                sources, context = await retrieve_context(
                    question, filters=filters, **retrieval_params
                )
                plan = await plan_task  # plan_queries 永不 raise（失敗回 degraded 計畫）
            except BaseException:
                plan_task.cancel()
                raise
            async for kind, payload in run_agentic(
                question,
                plan=plan,
                decision=_CORPUS_QA_DECISION,
                first=(sources, context),
                filters=filters,
                # run_agentic 的補查 max_reports 由 qa_subquery_max_reports 決定，
                # 此處不傳 max_reports；rerank_timeout 沿用問答路徑常數。
                retrieval_params={
                    "k": retrieval_params.get("k", RETRIEVAL_K),
                    "dense_scan": retrieval_params.get("dense_scan", ASK_DENSE_SCAN),
                    "max_passages": retrieval_params.get(
                        "max_passages", MAX_PASSAGES_PER_REPORT
                    ),
                    "max_chars": retrieval_params.get("max_chars", MAX_CONTEXT_CHARS),
                    "rerank_top_m": retrieval_params.get("rerank_top_m", 0),
                    "rerank_timeout": ASK_RERANK_TIMEOUT,
                },
            ):
                if kind == "outcome":
                    outcome = payload
            if outcome is not None:
                sources, context = outcome.sources, outcome.context
        else:
            sources, context = await retrieve_context(
                question, filters=filters, **retrieval_params
            )
        contexts = split_contexts(context)
        answer = await _generate_answer(question, context)
        latency_ms = int((time.monotonic() - t0) * 1000)
        f = await faithfulness(answer, contexts, judge=judge)
        cp = await context_precision(question, answer, contexts, judge=judge)
        ar = await answer_relevancy(question, answer, judge=judge, embed=embed)
        result = {
            **base,
            "faithfulness": f,
            "context_precision": cp,
            "answer_relevancy": ar,
            "n_contexts": len(contexts),
            "latency_ms": latency_ms,
        }
        if outcome is not None:
            result["rounds"] = outcome.rounds
            result["subqueries_run"] = len(outcome.subqueries_run)
            result["skipped"] = outcome.skipped
        return result
    except Exception as e:  # noqa: BLE001 — 離線批次逐題 fail-open，不讓單題炸掉整批
        return {**base, "error": f"{type(e).__name__}: {e}"}


def _mean_of(per_q: list[dict], key: str) -> float | None:
    vals = [c[key] for c in per_q if "error" not in c and c.get(key) is not None]
    return sum(vals) / len(vals) if vals else None


def _percentile(vals: list, pct: float):
    """最近秩（nearest-rank）百分位：取 sorted[ceil(pct/100*n)-1]。呼叫端保證非空。"""
    ordered = sorted(vals)
    idx = max(0, math.ceil(pct / 100 * len(ordered)) - 1)
    return ordered[idx]


def aggregate(per_q: list[dict]) -> dict:
    """各指標均值（略過 error 與 None）、延遲分佈（mean/p50/p95，只算無 error 且有
    latency_ms 的 case——舊報表 case 無此欄）、計數、門檻旗標。純函式。"""
    f = _mean_of(per_q, "faithfulness")
    cp = _mean_of(per_q, "context_precision")
    ar = _mean_of(per_q, "answer_relevancy")
    n_errors = sum(1 for c in per_q if "error" in c)
    n_no_context = sum(
        1 for c in per_q if "error" not in c and c.get("faithfulness") is None
    )
    thresholds_pass = (
        f is not None
        and cp is not None
        and ar is not None
        and f > FAITHFULNESS_MIN
        and cp > CONTEXT_PRECISION_MIN
        and ar > ANSWER_RELEVANCY_MIN
    )
    latencies = [
        c["latency_ms"]
        for c in per_q
        if "error" not in c and c.get("latency_ms") is not None
    ]
    return {
        "faithfulness": f,
        "context_precision": cp,
        "answer_relevancy": ar,
        "latency_ms_mean": sum(latencies) / len(latencies) if latencies else None,
        "latency_ms_p50": _percentile(latencies, 50) if latencies else None,
        "latency_ms_p95": _percentile(latencies, 95) if latencies else None,
        "n": len(per_q),
        "n_errors": n_errors,
        "n_no_context": n_no_context,
        "thresholds_pass": thresholds_pass,
    }


async def run(
    dataset_path,
    *,
    out_path,
    judge_model: str = DEFAULT_JUDGE_MODEL,
    limit: int | None = None,
    concurrency: int = 3,
    rerank_top_m: int = 0,
    scope: str | None = None,
    agentic: bool = False,
) -> dict:
    """讀題集 → 有界併發 eval_question → aggregate → 寫報表（{summary, cases}）。

    rerank_top_m>0 時檢索走 M2 cross-encoder 重排（rerank-on 基準線），=0 為 rerank-off。
    scope 指定時只跑該 scope 的題目（未標 scope 的題目視為 corpus_qa）。
    agentic=True 一律強制 concurrency=1：補查全部排同一個 rerank semaphore
    （workers=1、逾時含排隊），併發會使 deadline 非決定性跳過補查、CP 混入隨機性。
    """
    dataset = json.loads(Path(dataset_path).read_text(encoding="utf-8"))
    questions = dataset.get("questions", [])
    if scope:
        questions = [q for q in questions if q.get("scope", "corpus_qa") == scope]
    if limit is not None:
        questions = questions[:limit]

    if agentic:
        concurrency = 1

    retrieval_params = {**RETRIEVAL_PARAMS, "rerank_top_m": rerank_top_m}

    async def _judge(system: str, user: str):
        return await judge_json(user, system=system, model=judge_model)

    sem = asyncio.Semaphore(concurrency)

    async def _one(q: dict) -> dict:
        async with sem:  # 限制同時 spawn 的 claude CLI 數，避開 IO 風暴
            return await eval_question(
                q,
                judge=_judge,
                embed=embed_query_cached,
                retrieval_params=retrieval_params,
                agentic=agentic,
            )

    cases = await asyncio.gather(*[_one(q) for q in questions])
    summary = aggregate(list(cases))
    report = {"summary": summary, "cases": list(cases)}
    if out_path is not None:
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    return report


def _print_summary(report: dict) -> None:
    s = report["summary"]

    def fmt(v):
        return f"{v:.3f}" if isinstance(v, float) else "n/a"

    print("=== M1 RAGAS 基準線 ===")
    print(f"Faithfulness      : {fmt(s['faithfulness'])}  (門檻 > {FAITHFULNESS_MIN})")
    print(f"Context Precision : {fmt(s['context_precision'])}  (門檻 > {CONTEXT_PRECISION_MIN})")
    print(f"Answer Relevancy  : {fmt(s['answer_relevancy'])}  (門檻 > {ANSWER_RELEVANCY_MIN})")
    if s.get("latency_ms_mean") is not None:
        print(
            f"latency_ms        : mean={s['latency_ms_mean']:.0f}  "
            f"p50={s['latency_ms_p50']:.0f}  p95={s['latency_ms_p95']:.0f}"
        )
    print(f"n={s['n']}  errors={s['n_errors']}  no_context={s['n_no_context']}")
    print(f"thresholds_pass   : {s['thresholds_pass']}")


def _main() -> None:
    parser = argparse.ArgumentParser(description="跑 M1 RAGAS reference-free 評測")
    parser.add_argument("--dataset", default="eval/ragas_questions.json")
    parser.add_argument("--out", default="eval/baselines/baseline-m0.json")
    parser.add_argument("--judge-model", default=DEFAULT_JUDGE_MODEL)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument(
        "--rerank-top-m",
        type=int,
        default=0,
        help="檢索重排候選上限（>0 走 M2 cross-encoder 重排，0=off）",
    )
    parser.add_argument("--scope", default=None,
                        help="只跑指定 scope 的題目（如 corpus_qa）；未標 scope 的題目視為 corpus_qa")
    parser.add_argument("--agentic", action="store_true",
                        help="走 M5 agentic 迴圈評測（強制 concurrency=1）")
    parser.add_argument("--json", action="store_true", help="改輸出完整 JSON 到 stdout")
    args = parser.parse_args()

    report = asyncio.run(
        run(
            args.dataset,
            out_path=args.out,
            judge_model=args.judge_model,
            limit=args.limit,
            concurrency=args.concurrency,
            rerank_top_m=args.rerank_top_m,
            scope=args.scope,
            agentic=args.agentic,
        )
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_summary(report)
        print(f"\nwrote report -> {args.out}")


if __name__ == "__main__":
    _main()
