"""分析 research.qa_log 真實問答流量：離題率、延遲分位、feedback、引用研報新舊度。

重點是直接量「引用太老舊」：把 cited_report_ids 展開後 JOIN research_report.report_date，
算被引用報告的年齡中位數與過舊比例——配合 scripts/eval_retrieval.py 的離線指標，
線上/離線雙重追蹤新近度是否改善。

用法：
  uv run python scripts/analyze_qa_log.py --days 30
  uv run python scripts/analyze_qa_log.py --days 30 --market TW --json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from app.services.answer import NO_CONTEXT_MESSAGE, OFF_TOPIC_MESSAGES  # noqa: E402
from app.services.db import SessionFactory  # noqa: E402

OFFTOPIC_ANSWERS = set(OFF_TOPIC_MESSAGES) | {NO_CONTEXT_MESSAGE}


def percentiles(values: list[float], ps: list[int]) -> dict:
    """nearest-rank 百分位：idx = ceil(p/100 * n) - 1。空 → 各為 None。"""
    if not values:
        return {p: None for p in ps}
    s = sorted(values)
    out = {}
    for p in ps:
        idx = max(0, math.ceil(p / 100 * len(s)) - 1)
        out[p] = s[idx]
    return out


def bucketize(ages: list[int], thresholds: list[int]) -> dict:
    """每個門檻：嚴格大於該門檻的個數。"""
    return {t: sum(1 for a in ages if a > t) for t in thresholds}


def pct(part: float, whole: float) -> float:
    """part/whole；whole=0 → 0.0。"""
    return (part / whole) if whole else 0.0


def summarize(rows: list[dict], cited_ages: list[int], offtopic_answers: set) -> dict:
    """把原始列 + 引用年齡彙整成指標 dict（純函式，便於單元測試）。"""
    n = len(rows)
    offtopic = sum(1 for r in rows if r.get("answer") in offtopic_answers)
    fb = {"like": 0, "dislike": 0, "none": 0}
    for r in rows:
        f = r.get("feedback")
        fb["like" if f == "like" else "dislike" if f == "dislike" else "none"] += 1

    lat = percentiles(
        [r["latency_ms"] for r in rows if r.get("latency_ms") is not None], [50, 90, 99]
    )
    think = percentiles(
        [r["thinking_ms"] for r in rows if r.get("thinking_ms") is not None],
        [50, 90, 99],
    )
    return {
        "n": n,
        "offtopic": offtopic,
        "offtopic_rate": pct(offtopic, n),
        "feedback": fb,
        "latency_ms": lat,
        "thinking_ms": think,
        "n_cited": len(cited_ages),
        "cited_age": {
            "median": statistics.median(cited_ages) if cited_ages else None,
            "p90": percentiles(cited_ages, [90])[90],
            "pct_over_365": pct(bucketize(cited_ages, [365])[365], len(cited_ages)),
            "pct_over_540": pct(bucketize(cited_ages, [540])[540], len(cited_ages)),
        },
    }


async def fetch_rows(session, days: int, market: str | None) -> list[dict]:
    sql = """
        SELECT latency_ms, thinking_ms, feedback, answer
        FROM research.qa_log
        WHERE created_at >= now() - make_interval(days => :days)
    """
    params: dict = {"days": days}
    if market:
        sql += " AND filters->>'market' = :market"
        params["market"] = market
    res = await session.execute(text(sql), params)
    return [
        {"latency_ms": r[0], "thinking_ms": r[1], "feedback": r[2], "answer": r[3]}
        for r in res.all()
    ]


async def fetch_cited_ages(session, days: int, market: str | None) -> list[int]:
    sql = """
        SELECT (q.created_at::date - r.report_date) AS age_days
        FROM research.qa_log q
        CROSS JOIN LATERAL unnest(q.cited_report_ids) AS cid(rid)
        JOIN research.research_report r ON r.id = cid.rid
        WHERE q.created_at >= now() - make_interval(days => :days)
          AND r.report_date IS NOT NULL
    """
    params: dict = {"days": days}
    if market:
        sql += " AND q.filters->>'market' = :market"
        params["market"] = market
    res = await session.execute(text(sql), params)
    return [int(r[0]) for r in res.all() if r[0] is not None]


def _p(v) -> str:
    return str(v) if v is not None else "—"


def fmt_report(s: dict, days: int) -> str:
    lat, think, age = s["latency_ms"], s["thinking_ms"], s["cited_age"]
    fb = s["feedback"]
    lines = [
        f"=== qa_log analysis (last {days} days, n={s['n']}) ===",
        "",
        "[Volume & off-topic]",
        f"  total ............. {s['n']}",
        f"  off-topic ......... {s['offtopic']}  ({s['offtopic_rate'] * 100:.1f}%)",
        "",
        "[Latency]              p50      p90      p99",
        f"  latency_ms ...... {_p(lat[50]):>7}  {_p(lat[90]):>7}  {_p(lat[99]):>7}",
        f"  thinking_ms ..... {_p(think[50]):>7}  {_p(think[90]):>7}  {_p(think[99]):>7}",
        "",
        "[Feedback]",
        f"  like {fb['like']}   dislike {fb['dislike']}   none {fb['none']}",
        "",
        "[Cited report recency]  (直接對應「引用太老舊」)",
        f"  n cited ........... {s['n_cited']}",
        f"  median age ........ {_p(age['median'])}d",
        f"  p90 age ........... {_p(age['p90'])}d",
        f"  cited > 365d ...... {age['pct_over_365'] * 100:.0f}%",
        f"  cited > 540d ...... {age['pct_over_540'] * 100:.0f}%",
    ]
    return "\n".join(lines)


async def main(days: int, market: str | None, json_out: bool) -> None:
    async with SessionFactory() as session:
        rows = await fetch_rows(session, days, market)
        cited_ages = await fetch_cited_ages(session, days, market)
    s = summarize(rows, cited_ages, OFFTOPIC_ANSWERS)
    if json_out:
        print(json.dumps(s, ensure_ascii=False))
    else:
        print(fmt_report(s, days))


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="分析 qa_log 問答流量")
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--market", default=None, help="只看特定市場（filters.market）")
    p.add_argument("--json", action="store_true")
    return p.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args()
    asyncio.run(main(args.days, args.market, args.json))
