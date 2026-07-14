"""M1b 研報 eval 編排：讀研報題集 → 逐題 generate_report(persist=False) → 結構化指標
→ aggregate → 寫基準線。

與 M1 問答 runner（run_ragas.py）分離：本 runner 走 generate_report 本尊（深檢索 +
研報 prompt + 長輸出），指標全部確定性（eval/report_metrics.py），不用 LLM 評審。
序列執行（concurrency=1）：研報為長 LLM 工作，claude CLI 多併發實證會觸發限流
（M4 Task 7 教訓），prod 端 REPORT_SEMAPHORE 本就序列化。逐題 fail-open。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import bindparam, text  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.services.report import generate_report  # noqa: E402
from eval.report_metrics import (  # noqa: E402
    RULESET_VERSION,
    aggregate_cases,
    citation_metrics,
    date_diversity,
    external_labeling,
    facet_coverage,
    no_data_handled,
    section_coverage,
    source_diversity,
)

DEFAULT_QUESTION_TIMEOUT = 900.0  # 外層護欄；generate_report 內部另有 REPORT_TIMEOUT=600


async def fetch_brokers(report_ids: list[str]):
    """查 research_report.source（券商）供來源多樣性；任何 DB 錯誤 → None（未知）。"""
    if not report_ids:
        return []
    try:
        from app.services.db import SessionFactory

        async with SessionFactory() as session:
            stmt = text(
                "SELECT source FROM research.research_report WHERE id IN :ids"
            ).bindparams(bindparam("ids", expanding=True))
            res = await session.execute(stmt, {"ids": report_ids})
            return [row[0] for row in res]
    except Exception:  # noqa: BLE001 — 離線批次 fail-open：券商多樣性記未知
        return None


async def eval_question(
    q: dict,
    *,
    gen=generate_report,
    broker_lookup=fetch_brokers,
    question_timeout: float = DEFAULT_QUESTION_TIMEOUT,
) -> dict:
    """單題：消費 generate_report(persist=False) 事件流 → 結構化指標。

    任一階段異常/逾時 → {..., "error": str}（fail-open，不中斷批次）。
    """
    topic = q.get("topic") or q.get("question") or ""
    no_data = bool(q.get("no_data"))
    base = {"id": q.get("id"), "topic": topic, "market": q.get("market"),
            "no_data": no_data}

    sources: list[dict] = []
    stages: list[str] = []
    markdown: str | None = None
    context: str | None = None
    # 兩種失敗分開記（審查 M1b-1）：report_error＝generate_report 的結構化
    # error 事件（研報婉拒，no_data 題的安全形態）；run_error＝runner 例外/逾時
    # （基礎設施失敗，計入 n_errors）。
    report_error: str | None = None
    run_error: str | None = None

    async def _consume() -> None:
        nonlocal markdown, context, report_error
        async for kind, payload in gen(
            topic, filters=q.get("filters") or {}, persist=False
        ):
            if kind == "status":
                stages.append(payload.get("stage"))
            elif kind == "sources":
                sources[:] = list(payload)
            elif kind == "error":
                report_error = (
                    payload.get("detail") if isinstance(payload, dict)
                    else str(payload)
                )
            elif kind == "done":
                markdown = payload.get("markdown")
                context = payload.get("context")

    try:
        await asyncio.wait_for(_consume(), timeout=question_timeout)
    except TimeoutError:
        run_error = f"timeout: exceeded {question_timeout}s"
    except Exception as e:  # noqa: BLE001 — 逐題 fail-open
        run_error = f"{type(e).__name__}: {e}"

    if run_error is not None:
        # runner 失敗＝結果未知：不算 no_data_handled 分母，計入 n_errors
        return {**base, "error": run_error, "stages": stages,
                "n_sources": len(sources)}

    handled = (
        no_data_handled(error=report_error, n_sources=len(sources),
                        markdown=markdown)
        if no_data
        else None
    )
    if report_error is not None:
        return {**base, "report_error": report_error, "stages": stages,
                "n_sources": len(sources), "no_data_handled": handled}

    brokers = await broker_lookup([s.get("report_id") for s in sources
                                   if s.get("report_id")])
    cm = citation_metrics(markdown or "", len(sources))
    return {
        **base,
        "n_sources": len(sources),
        "stages": stages,
        "facet_coverage": facet_coverage(q.get("expected_facets"), context or ""),
        "source_diversity": source_diversity(sources, brokers=brokers),
        "date_diversity": date_diversity(sources),
        "section_coverage": section_coverage(markdown or ""),
        **cm,
        "external_labeling": external_labeling(markdown or ""),
        "no_data_handled": handled,
        "context_chars": len(context or ""),
        "markdown": markdown,  # 供人工抽樣與跨基準線稽核（脈絡不落地，僅記長度）
    }


def _config_snapshot(dataset: dict) -> dict:
    s = get_settings()
    return {
        "dataset_version": dataset.get("version"),
        "ruleset_version": RULESET_VERSION,
        "report_model": s.report_model,
        "report_enable_web": s.report_enable_web,
        "report_deep_k": s.report_deep_k,
        "report_max_reports": s.report_max_reports,
        "report_max_passages": s.report_max_passages,
        "report_max_context_chars": s.report_max_context_chars,
        "report_rerank_enabled": s.report_rerank_enabled,
        "report_rerank_candidates": s.report_rerank_candidates,
        "report_thin_coverage": s.report_thin_coverage,
    }


async def run(
    dataset_path,
    *,
    out_path,
    gen=generate_report,
    broker_lookup=fetch_brokers,
    question_timeout: float = DEFAULT_QUESTION_TIMEOUT,
    limit: int | None = None,
) -> dict:
    """讀題集 → 逐題（序列）→ aggregate → 寫 {summary, cases, config}。"""
    dataset = json.loads(Path(dataset_path).read_text(encoding="utf-8"))
    questions = dataset.get("questions", [])
    if limit is not None:
        questions = questions[:limit]

    cases: list[dict] = []
    for i, q in enumerate(questions, start=1):
        print(f"[{i}/{len(questions)}] {q.get('id')} {q.get('topic', '')[:30]}...",
              flush=True)
        case = await eval_question(
            q, gen=gen, broker_lookup=broker_lookup,
            question_timeout=question_timeout,
        )
        if case.get("error"):
            print(f"  -> error: {case['error'][:120]}", flush=True)
        cases.append(case)

    summary = aggregate_cases(cases)
    report = {"summary": summary, "config": _config_snapshot(dataset), "cases": cases}
    if out_path is not None:
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    return report


def _fmt(v) -> str:
    return f"{v:.3f}" if isinstance(v, float) else ("n/a" if v is None else str(v))


def _print_summary(report: dict) -> None:
    s = report["summary"]
    print("=== M1b 研報評測基準線 ===")
    for key in (
        "facet_coverage", "section_coverage", "citation_validity",
        "source_citation_rate", "external_labeling", "no_data_handled",
        "n_reports", "n_brokers", "n_markets", "date_span_days", "n_months",
    ):
        m = s[key]
        print(f"{key:22s}: {_fmt(m['mean'])}  (n_valid={m['n_valid']})")
    print(f"n={s['n']}  errors={s['n_errors']}  declined={s['n_report_declined']}  "
          f"no_data={s['n_no_data']}  sufficient_n={s['sufficient_n']}")


def _main() -> None:
    parser = argparse.ArgumentParser(description="跑 M1b 研報專用結構化評測")
    parser.add_argument("--dataset", default="eval/report_questions.json")
    parser.add_argument("--out", default="eval/baselines/report-m1b.json")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--question-timeout", type=float,
                        default=DEFAULT_QUESTION_TIMEOUT)
    parser.add_argument("--json", action="store_true", help="輸出完整 JSON 到 stdout")
    args = parser.parse_args()

    report = asyncio.run(
        run(
            args.dataset,
            out_path=args.out,
            question_timeout=args.question_timeout,
            limit=args.limit,
        )
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_summary(report)
        print(f"\nwrote report -> {args.out}")


if __name__ == "__main__":
    _main()
