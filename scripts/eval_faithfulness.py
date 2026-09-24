"""M8 忠實度查核結果（qa_log.evaluation）的讀取路徑。

M8 從上線起就只寫不看：這個 jsonb 欄位在 `web/`、`scripts/`、`frontend/src/` 各有
**0 個消費端**。實測（2026-07-28 生產）近 30 天問答查核 3 筆中有 1 筆 degraded、
平均 0.563——沒有任何地方會顯示出來。

監控頁的卡片回答「還在跑嗎、有沒有壞」；這支腳本回答「壞在哪一條主張」：

    uv run python scripts/eval_faithfulness.py                 # 近 30 天彙總 + 最差 10 筆
    uv run python scripts/eval_faithfulness.py --days 90 --limit 20
    uv run python scripts/eval_faithfulness.py --json          # 機器可讀
    uv run python scripts/eval_faithfulness.py --claims <id>   # 逐條主張下鑽

指標語意（與 app/services/faithfulness.py 的 to_evaluation 對齊）：

  faithfulness_score   有語料支持的主張 / 全部主張
  numeric_support_rate 同上，但只算含金融數字的主張（金額、%、倍數…）
  citation_coverage    問答端一律 None（欄位保留在 to_evaluation 的形狀裡）
  degraded=True        judge 異常時的 fail-open：**該筆實際上沒被查核**，
                       分數欄位皆 None。degraded 不是「分數低」，是「沒量到」，
                       兩者混在一起看會把故障讀成品質問題。

**只計現行 judge**（`FAITHFULNESS_MODEL`，可用 --judge 指定別的）：與監控卡、待複核佇列
共用 `app/services/judge_schema.py` 的同一條規則，缺 `judge_model` 的舊列視為
claude-haiku-4-5。其他 judge 的筆數列在 `other_judge_checked` 與 `by_judge`，不混進分數。

唯讀，不寫任何資料。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.services.db import SessionFactory  # noqa: E402
from app.services.judge_schema import is_current_judge, judge_model_of  # noqa: E402

# (表, 識別欄位)。以 dict 保留是為了 summarize／_fetch 對來源一視同仁；
# 研報 PDF 那一半的來源表已隨功能移除（2026-09）。
_SOURCES = {
    "qa": ("research.qa_log", "question"),
}


def _num(v) -> float | None:
    """只接受真的是數字的值。畸形一筆不該讓整份統計爆掉或被靜默當 0。"""
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _current_judge(judge_model: str | None) -> str:
    return judge_model or get_settings().faithfulness_model


def summarize(rows: list[dict], min_score: float, *, judge_model: str | None = None) -> dict:
    """把原始列彙整成指標（純函式，便於單元測試）。

    `rows` 每筆需有 `evaluation`（dict 或 None）。**分數統計只納入非 degraded 的筆數**：
    degraded 代表沒量到，把它的 None 當 0 會憑空拉低平均，當成「有查核」則會高估覆蓋。
    **也只納入 judge_model 量的列**（未給＝現行 FAITHFULNESS_MODEL）；其他 judge 的
    筆數另計 other_judge_checked，逐 judge 的筆數在 by_judge。
    """
    judge = _current_judge(judge_model)
    total = len(rows)
    all_checked = [r for r in rows if isinstance(r.get("evaluation"), dict)]
    checked = [r for r in all_checked if is_current_judge(r["evaluation"], judge)]
    by_judge: dict[str, int] = {}
    for r in all_checked:
        name = judge_model_of(r["evaluation"])
        by_judge[name] = by_judge.get(name, 0) + 1
    degraded = [r for r in checked if r["evaluation"].get("degraded") is True]
    scored = [
        (r, s)
        for r in checked
        if (s := _num(r["evaluation"].get("faithfulness_score"))) is not None
    ]
    scores = [s for _, s in scored]
    numeric = [
        n
        for r in checked
        if (n := _num(r["evaluation"].get("numeric_support_rate"))) is not None
    ]
    return {
        "judge_model": judge,
        "total": total,
        "checked": len(checked),
        "other_judge_checked": len(all_checked) - len(checked),
        "by_judge": dict(sorted(by_judge.items())),
        "degraded": len(degraded),
        "scored": len(scores),
        "below_min": sum(1 for s in scores if s < min_score),
        "avg_score": round(statistics.fmean(scores), 4) if scores else None,
        "median_score": round(statistics.median(scores), 4) if scores else None,
        "min_score_seen": round(min(scores), 4) if scores else None,
        "avg_numeric_support": round(statistics.fmean(numeric), 4) if numeric else None,
        "latest_checked": max(
            (str(r["created_at"])[:19] for r in checked), default=None
        ),
    }


def worst(rows: list[dict], limit: int, *, judge_model: str | None = None) -> list[dict]:
    """分數最低的前 N 筆。degraded 排除——它沒有分數，不是「最差」而是「沒量」。
    只看 judge_model（未給＝現行 judge）量的列，理由同 summarize。"""
    judge = _current_judge(judge_model)
    scored = [
        (s, r)
        for r in rows
        if isinstance(r.get("evaluation"), dict)
        and is_current_judge(r["evaluation"], judge)
        and (s := _num(r["evaluation"].get("faithfulness_score"))) is not None
    ]
    scored.sort(key=lambda t: t[0])
    out = []
    for s, r in scored[:limit]:
        claims = r["evaluation"].get("claims") or []
        bad = [c for c in claims if isinstance(c, dict) and c.get("verdict") != "supported"]
        out.append({
            "id": str(r["id"]),
            "created_at": str(r["created_at"])[:19],
            "score": round(s, 4),
            "numeric_support_rate": _num(r["evaluation"].get("numeric_support_rate")),
            "claims": len(claims),
            "unsupported": len(bad),
            "question": (r.get("question") or "")[:60],
        })
    return out


async def _fetch(kind: str, days: int) -> list[dict]:
    table, qcol = _SOURCES[kind]
    async with SessionFactory() as session:
        res = await session.execute(
            text(
                f"SELECT id, created_at, {qcol} AS question, evaluation FROM {table} "
                "WHERE created_at > now() - make_interval(days => :d) "
                "ORDER BY created_at DESC"
            ),
            {"d": days},
        )
        return [dict(r._mapping) for r in res.all()]


async def _fetch_claims(item_id: str) -> tuple[str, dict] | None:
    """逐條主張下鑽：id 可能落在任一張表，兩張都找。"""
    async with SessionFactory() as session:
        for kind, (table, qcol) in _SOURCES.items():
            res = await session.execute(
                text(
                    f"SELECT {qcol} AS question, evaluation FROM {table} "
                    "WHERE id::text = :i"
                ),
                {"i": item_id},
            )
            row = res.first()
            if row is not None:
                return kind, {"question": row[0], "evaluation": row[1]}
    return None


def _print_claims(kind: str, data: dict) -> None:
    ev = data.get("evaluation")
    if not isinstance(ev, dict):
        print(f"[{kind}] 這筆沒有 evaluation（未查核）")
        return
    print(f"[{kind}] {(data.get('question') or '')[:80]}")
    print(f"  faithfulness={ev.get('faithfulness_score')} "
          f"numeric={ev.get('numeric_support_rate')} degraded={ev.get('degraded')} "
          f"judge={judge_model_of(ev)}")
    if ev.get("degraded"):
        reason = ev.get("degraded_reason")
        print("  ※ degraded＝judge 異常 fail-open，這筆實際未被查核"
              + (f"（{reason}）" if reason else ""))
    for i, c in enumerate(ev.get("claims") or [], 1):
        if not isinstance(c, dict):
            continue
        mark = "OK " if c.get("verdict") == "supported" else "!! "
        num = "[數值]" if c.get("is_numeric") else "      "
        print(f"  {mark}{num} {i:>2}. {(c.get('text') or '')[:90]}")


def _print_report(agg: dict, worst_rows: dict, min_score: float, days: int) -> None:
    print(f"M8 忠實度查核（近 {days} 天，門檻 {min_score}）")
    for kind, label in (("qa", "問答"),):
        a = agg[kind]
        print(f"\n[{label}] 判定尺 {a['judge_model']}　總數 {a['total']}　已查核 {a['checked']}　"
              f"fail-open {a['degraded']}　有分數 {a['scored']}")
        if a["other_judge_checked"]:
            others = "、".join(f"{k} {v}" for k, v in a["by_judge"].items() if k != a["judge_model"])
            print(f"  另有 {a['other_judge_checked']} 筆其他判定尺的結果未計入（{others}）")
        if a["scored"]:
            print(f"  平均 {a['avg_score']}　中位 {a['median_score']}　"
                  f"最低 {a['min_score_seen']}　數值支持率 {a['avg_numeric_support']}")
            print(f"  低於門檻（待複核）：{a['below_min']} 筆")
        print(f"  最後查核：{a['latest_checked'] or '—'}")
        rows = worst_rows[kind]
        if rows:
            print(f"  最低分 {len(rows)} 筆：")
            for w in rows:
                print(f"    {w['score']:.3f}  {w['created_at']}  "
                      f"未支持 {w['unsupported']}/{w['claims']}  "
                      f"{w['id'][:8]}  {w['question']}")


async def main(args) -> None:
    min_score = args.min if args.min is not None else get_settings().faithfulness_min

    if args.claims:
        found = await _fetch_claims(args.claims)
        if found is None:
            print(f"找不到 id={args.claims}（qa_log 無此列）")
            raise SystemExit(1)
        _print_claims(*found)
        return

    agg, worst_rows = {}, {}
    for kind in _SOURCES:
        rows = await _fetch(kind, args.days)
        agg[kind] = summarize(rows, min_score, judge_model=args.judge)
        worst_rows[kind] = worst(rows, args.limit, judge_model=args.judge)

    if args.json:
        print(json.dumps(
            {"days": args.days, "min_score": min_score,
             "summary": agg, "worst": worst_rows},
            ensure_ascii=False, indent=2,
        ))
    else:
        _print_report(agg, worst_rows, min_score, args.days)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="M8 忠實度查核結果彙總（唯讀）")
    ap.add_argument("--days", type=int, default=30, help="回看天數（預設 30）")
    ap.add_argument("--limit", type=int, default=10, help="最低分列出筆數（預設 10）")
    ap.add_argument("--min", type=float, default=None,
                    help="待複核門檻（預設取 FAITHFULNESS_MIN）")
    ap.add_argument("--claims", metavar="ID", help="逐條主張下鑽（qa_log 的 id）")
    ap.add_argument("--judge", default=None,
                    help="只統計這個 judge 量的列（預設取 FAITHFULNESS_MODEL；缺鍵的舊列視為 claude-haiku-4-5）")
    ap.add_argument("--json", action="store_true", help="輸出 JSON")
    asyncio.run(main(ap.parse_args()))
