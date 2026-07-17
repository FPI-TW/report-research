"""離線檢索評估：對固定查詢集量測檢索/排序的「命中率、精準度、引用新近度」。

重用既有 hybrid_search + build_context（不複製檢索邏輯），跑到 build_context 為止、
不呼叫 LLM——因此快、確定性高、可反覆跑。標註集以「關鍵字 + 日期條件」描述期望
（非寫死 report_id），對語料重 ingest（UUID 會變）穩健。

核心用途：調整新近度旋鈕（ASK_RECENCY_*）前後各跑一次，逐欄比較
hit_rate / mean_P@k / median_cited_age / pct_over_max_age，避免盲調。

用法：
  uv run python scripts/eval_retrieval.py --queryset eval/queryset.json
  uv run python scripts/eval_retrieval.py --queryset eval/queryset.json --json > eval/before.json
  ASK_RECENCY_HALF_LIFE_DAYS=90 uv run python scripts/eval_retrieval.py \
      --queryset eval/queryset.json --json > eval/after.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import answer as _answer  # noqa: E402
from app.services.answer import _as_date, build_context  # noqa: E402
from app.services.db import SessionFactory  # noqa: E402
from app.services.embed import embed_query_cached  # noqa: E402
from app.services.retrieval import hybrid_search  # noqa: E402
from app.services.rows import ChunkRow  # noqa: E402
from app.services.textnorm import norm_for_match  # noqa: E402

# 由 ChunkRow 推導欄位位置，不要寫死數字：ChunkRow 是 NamedTuple，任何在中段插入
# 欄位（如 2026-07 為閱讀頁加的 file_hash）都會讓寫死的索引指到別的欄，而且是
# **無聲的** —— row[14] 從 content 變成 chunk_index 不會拋例外，只會讓評估分數
# 悄悄變成垃圾。曾實際發生：本檔原為 `_RID, _CONTENT = 1, 14`。
_RID = ChunkRow._fields.index("report_id")
_CONTENT = ChunkRow._fields.index("content")


def load_queryset(path: str | Path) -> dict:
    """讀標註集 JSON。結構：{as_of, cases:[{id, query, filters?, expect{...}}]}。"""
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _report_blob(source, rows) -> str:
    """某來源報告的可比對文字 = 檔名 + 該報告在 rows 中的所有片段內容（已正規化）。

    rows 為 hybrid_search row（非 (tier,fused,row) 三元組）；呼叫端先解包。
    """
    parts = [source.file_name or ""]
    for row in rows:
        if row[_RID] == source.report_id and row[_CONTENT]:
            parts.append(str(row[_CONTENT]))
    return norm_for_match(" ".join(parts))


def source_matches(source, rows, keywords: list[str]) -> bool:
    """來源報告（檔名+片段）是否命中任一關鍵字（正規化後 substring）。無關鍵字→False。"""
    if not keywords:
        return False
    blob = _report_blob(source, rows)
    return any(norm_for_match(kw) in blob for kw in keywords)


def source_age_days(source, as_of: date) -> int | None:
    """來源報告日期距 as_of 的天數；無日期→None。"""
    d = _as_date(source.report_date)
    if d is None:
        return None
    return (as_of - d).days


def case_metrics(sources, rows, expect: dict, as_of: date) -> dict:
    """單一查詢案的指標：命中、precision@k、新近度。

    rows 為 hybrid_search row（非 (tier,fused,row) 三元組）；呼叫端先解包。
    """
    keywords = expect.get("any_keywords") or []
    min_hits = int(expect.get("min_hits", 1))
    matched = [source_matches(s, rows, keywords) for s in sources]
    matched_count = sum(matched)
    if keywords:
        hit = matched_count >= min_hits
    else:
        hit = len(sources) >= min_hits
    p_at_k = (matched_count / len(sources)) if sources else 0.0

    ages = [
        a for s in sources if (a := source_age_days(s, as_of)) is not None
    ]
    recency = expect.get("recency") or {}
    max_age = recency.get("max_age_days")
    if max_age is None:
        recency_pass = None
    else:
        recency_pass = any(a <= max_age for a in ages) if ages else False

    return {
        "hit": hit,
        "p_at_k": p_at_k,
        "matched_count": matched_count,
        "n_sources": len(sources),
        "ages": ages,
        "newest_age": min(ages) if ages else None,
        "median_age": statistics.median(ages) if ages else None,
        "recency_pass": recency_pass,
    }


def aggregate(per_case: list[dict], max_age_days: int) -> dict:
    """彙整所有案：hit_rate / mean_P@k / 引用年齡中位數 / 過舊比例 / recency 通過率。"""
    n = len(per_case)
    all_ages = [a for c in per_case for a in c.get("ages", [])]
    rec_flags = [
        c["recency_pass"] for c in per_case if c.get("recency_pass") is not None
    ]
    over = [a for a in all_ages if a > max_age_days]
    return {
        "n_cases": n,
        "hit_rate": (sum(1 for c in per_case if c["hit"]) / n) if n else 0.0,
        "mean_p_at_k": (sum(c["p_at_k"] for c in per_case) / n) if n else 0.0,
        "recency_pass_rate": (sum(rec_flags) / len(rec_flags)) if rec_flags else None,
        "median_cited_age": statistics.median(all_ages) if all_ages else None,
        "pct_over_max_age": (len(over) / len(all_ages)) if all_ages else None,
        "n_cited": len(all_ages),
        "max_age_days": max_age_days,
    }


def _fmt_age(v) -> str:
    return f"{v}d" if v is not None else "—"


def fmt_table(per_case: list[dict], agg: dict) -> str:
    """純文字表：每案一列 + SUMMARY + 當下生效的新近度旋鈕值。"""
    lines = [
        f"{'case':<34}{'hit':>4}{'P@k':>7}{'med_age':>9}{'newest':>9}",
        "-" * 63,
    ]
    for c in per_case:
        lines.append(
            f"{c['id'][:33]:<34}"
            f"{('✓' if c['hit'] else '✗'):>4}"
            f"{c['p_at_k']:>7.2f}"
            f"{_fmt_age(c['median_age']):>9}"
            f"{_fmt_age(c['newest_age']):>9}"
        )
    lines.append("-" * 63)
    pct = agg["pct_over_max_age"]
    pct_str = f"{pct * 100:.0f}%" if pct is not None else "—"
    lines.append(
        f"SUMMARY  hit_rate={agg['hit_rate']:.2f}  "
        f"mean_P@k={agg['mean_p_at_k']:.2f}  "
        f"median_cited_age={_fmt_age(agg['median_cited_age'])}  "
        f"pct_over_{agg['max_age_days']}d={pct_str}"
    )
    lines.append(
        f"knobs: HALF_LIFE_DAYS={_answer.RECENCY_HALF_LIFE_DAYS} "
        f"RELEVANCE_BAND={getattr(_answer, 'RELEVANCE_BAND', '—')} "
        f"BAND_EPS={getattr(_answer, 'BAND_EPS', '—')} "
        f"n_cited={agg['n_cited']}"
    )
    return "\n".join(lines)


async def run_case(case: dict, k: int, as_of: date) -> dict:
    """跑單一案：embed → hybrid_search → build_context → 算指標。"""
    query = case["query"]
    filters = case.get("filters") or {}
    qvec = await asyncio.to_thread(embed_query_cached, query)
    # 鏡像問答路徑：顯式傳 ASK_DENSE_SCAN，使 before/after 與真實 ask 行為一致
    dense_scan = getattr(_answer, "ASK_DENSE_SCAN", None)
    async with SessionFactory() as session:
        scored = await hybrid_search(
            session, query, qvec, k=k, dense_scan=dense_scan, **filters
        )
    sources, _context = build_context(scored)
    rows = [r for (_tier, _fused, r) in scored]  # 解包成 bare row 供關鍵字比對
    m = case_metrics(sources, rows, case.get("expect") or {}, as_of)
    m["id"] = case.get("id", query[:20])
    return m


async def main(
    queryset_path: str, k: int, as_of_override: str | None, max_age_days: int,
    json_out: bool, only_case: str | None,
) -> None:
    qs = load_queryset(queryset_path)
    as_of_str = as_of_override or qs.get("as_of")
    as_of = _as_date(as_of_str) or date.today()
    cases = qs.get("cases", [])
    if only_case:
        cases = [c for c in cases if c.get("id") == only_case]

    per_case = []
    for case in cases:
        per_case.append(await run_case(case, k, as_of))
    agg = aggregate(per_case, max_age_days)

    if json_out:
        print(json.dumps({"summary": agg, "cases": per_case}, ensure_ascii=False))
    else:
        print(fmt_table(per_case, agg))


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="離線檢索評估")
    p.add_argument("--queryset", default="eval/queryset.json")
    p.add_argument("--k", type=int, default=_DEFAULT_K)
    p.add_argument("--as-of", default=None, help="覆寫 queryset 的基準日 YYYY-MM-DD")
    p.add_argument("--max-age-days", type=int, default=365, help="過舊門檻（彙整用）")
    p.add_argument("--json", action="store_true", help="輸出機器可讀摘要")
    p.add_argument("--case", default=None, help="只跑指定 id 的案（debug）")
    return p.parse_args(argv)


# RETRIEVAL_K 預設：優先用 answer 的常數，否則退回 8
_DEFAULT_K = getattr(_answer, "RETRIEVAL_K", 8)


if __name__ == "__main__":
    args = _parse_args()
    asyncio.run(
        main(
            args.queryset, args.k, args.as_of, args.max_age_days, args.json, args.case
        )
    )
