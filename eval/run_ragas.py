"""M1 eval 編排：讀題集 → 逐題 retrieve→generate→三指標 → aggregate → 寫基準線。

生成端刻意用 build_user_prompt + stream_completion（非 answer_question）以隔離檢索與
生成、避開 qa_log 寫入/overview/off-topic。有界併發（claude CLI spawn 吃 IO）、逐題
fail-open（任一階段異常記 error、不計均值、不中斷批次）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.answer import (  # noqa: E402
    ASK_DENSE_SCAN,
    MAX_CONTEXT_CHARS,
    MAX_PASSAGES_PER_REPORT,
    MAX_REPORTS,
    RETRIEVAL_K,
    SYSTEM_PROMPT,
    build_user_prompt,
)
from app.services.embed import embed_query_cached  # noqa: E402
from app.services.llm import DEFAULT_MODEL, SEARCH_EVENT, stream_completion  # noqa: E402
from app.services.retrieval_pipeline import retrieve_context  # noqa: E402
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


async def eval_question(q: dict, *, judge, embed, retrieval_params: dict) -> dict:
    """單題：retrieve→generate→三指標。任一階段異常 → {..., "error": str}（fail-open）。"""
    base = {"id": q.get("id"), "question": q.get("question")}
    try:
        sources, context = await retrieve_context(
            q["question"], filters=q.get("filters") or {}, **retrieval_params
        )
        contexts = split_contexts(context)
        answer = await _generate_answer(q["question"], context)
        f = await faithfulness(answer, contexts, judge=judge)
        cp = await context_precision(q["question"], answer, contexts, judge=judge)
        ar = await answer_relevancy(q["question"], answer, judge=judge, embed=embed)
        return {
            **base,
            "faithfulness": f,
            "context_precision": cp,
            "answer_relevancy": ar,
            "n_contexts": len(contexts),
        }
    except Exception as e:  # noqa: BLE001 — 離線批次逐題 fail-open，不讓單題炸掉整批
        return {**base, "error": f"{type(e).__name__}: {e}"}


def _mean_of(per_q: list[dict], key: str) -> float | None:
    vals = [c[key] for c in per_q if "error" not in c and c.get(key) is not None]
    return sum(vals) / len(vals) if vals else None


def aggregate(per_q: list[dict]) -> dict:
    """各指標均值（略過 error 與 None）、計數（n/n_errors/n_no_context）、門檻旗標。純函式。"""
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
    return {
        "faithfulness": f,
        "context_precision": cp,
        "answer_relevancy": ar,
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
) -> dict:
    """讀題集 → 有界併發 eval_question → aggregate → 寫報表（{summary, cases}）。

    rerank_top_m>0 時檢索走 M2 cross-encoder 重排（rerank-on 基準線），=0 為 rerank-off。
    scope 指定時只跑該 scope 的題目（未標 scope 的題目視為 corpus_qa）。
    """
    dataset = json.loads(Path(dataset_path).read_text(encoding="utf-8"))
    questions = dataset.get("questions", [])
    if scope:
        questions = [q for q in questions if q.get("scope", "corpus_qa") == scope]
    if limit is not None:
        questions = questions[:limit]

    retrieval_params = {**RETRIEVAL_PARAMS, "rerank_top_m": rerank_top_m}

    async def _judge(system: str, user: str):
        return await judge_json(user, system=system, model=judge_model)

    sem = asyncio.Semaphore(concurrency)

    async def _one(q: dict) -> dict:
        async with sem:  # 限制同時 spawn 的 claude CLI 數，避開 IO 風暴
            return await eval_question(
                q, judge=_judge, embed=embed_query_cached, retrieval_params=retrieval_params
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
        )
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_summary(report)
        print(f"\nwrote report -> {args.out}")


if __name__ == "__main__":
    _main()
