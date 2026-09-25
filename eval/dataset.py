"""題集生成器：挖 research.qa_log → 去重/濾/白名單/市場多樣抽樣 → 版本化 JSON。

生成器語義：偶爾 refresh、人工過目後 commit＝穩定 baseline 輸入。純函式 select_questions
不取時間；generated_at 由 CLI 以現在時間戳入。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from app.services.answer import (  # noqa: E402
    NO_CONTEXT_MESSAGE,
    OFF_TOPIC_MESSAGES,
    TIME_SENSITIVE_MESSAGES,
)
from app.services.db import SessionFactory  # noqa: E402
from app.services.textnorm import norm_for_match  # noqa: E402

MIN_QUESTION_LEN = 6

# 只保留 hybrid_search 接受的檢索相關 filter 鍵；qa_log 的 null 值與 path 等雜鍵剝除
_RETRIEVAL_FILTER_KEYS = (
    "market",
    "instrument_type",
    "relates_stock",
    "relates_futures",
    "report_type",
)


def _clean_filters(filters: dict) -> dict:
    """只留白名單鍵、丟棄 None 值 → 可安全展開給 hybrid_search 的 dict。"""
    out: dict = {}
    for key in _RETRIEVAL_FILTER_KEYS:
        v = filters.get(key)
        if v is not None:
            out[key] = v
    return out


def select_questions(rows: list[dict], *, per_market_cap: int, target: int) -> list[dict]:
    """純函式：濾 off-topic/no-context/overview/過短、去重（正規化）、市場多樣抽樣。

    rows: [{"question","answer","filters"}]（filters 為 dict）。回 [{"id","question","filters"}]。
    """
    seen: set[str] = set()
    per_market: "OrderedDict[str, list[dict]]" = OrderedDict()
    for r in rows:
        q = (r.get("question") or "").strip()
        a = (r.get("answer") or "").strip()
        filters = r.get("filters") or {}
        if not q or len(q) < MIN_QUESTION_LEN:
            continue
        if a in (*OFF_TOPIC_MESSAGES, NO_CONTEXT_MESSAGE, *TIME_SENSITIVE_MESSAGES):
            continue
        # overview 走純 SQL、time_sensitive 走 adapter/婉拒——皆非 corpus RAG，
        # 不進題集（時效成功答案是模板文字，不會被上面的固定文案比對攔下）
        if filters.get("path") in ("overview", "time_sensitive"):
            continue
        key = norm_for_match(q)
        if key in seen:
            continue
        seen.add(key)
        cleaned = _clean_filters(filters)
        market = cleaned.get("market") or "_general"
        bucket = per_market.setdefault(market, [])
        if len(bucket) < per_market_cap:
            bucket.append({"question": q, "filters": cleaned})

    # round-robin 攤平各市場、湊到 target（多樣優先）
    result: list[dict] = []
    lists = list(per_market.values())
    while len(result) < target and any(lists):
        progressed = False
        for lst in lists:
            if lst:
                result.append(lst.pop(0))
                progressed = True
                if len(result) >= target:
                    break
        if not progressed:
            break

    return [
        {"id": f"q{n:03d}", "question": it["question"], "filters": it["filters"]}
        for n, it in enumerate(result, start=1)
    ]


async def build_dataset(*, target: int, per_market_cap: int) -> dict:
    """讀 research.qa_log（最近 2000 列）→ select_questions → 版本化結構（generated_at 待 CLI 覆寫）。"""
    async with SessionFactory() as session:
        res = await session.execute(
            text(
                "SELECT question, answer, filters FROM research.qa_log "
                "WHERE answer IS NOT NULL "
                "ORDER BY created_at DESC LIMIT 2000"
            )
        )
        rows = [
            {"question": row[0], "answer": row[1], "filters": row[2] or {}}
            for row in res
        ]
    questions = select_questions(rows, per_market_cap=per_market_cap, target=target)
    return {
        "version": 1,
        "generated_at": None,
        "count": len(questions),
        "questions": questions,
    }


def _main() -> None:
    parser = argparse.ArgumentParser(description="挖 qa_log 產出版本化 eval 題集")
    parser.add_argument("--out", default="eval/ragas_questions.json")
    parser.add_argument("--target", type=int, default=30)
    parser.add_argument("--per-market-cap", type=int, default=8)
    args = parser.parse_args()

    dataset = asyncio.run(
        build_dataset(target=args.target, per_market_cap=args.per_market_cap)
    )
    dataset["generated_at"] = datetime.now(timezone.utc).isoformat()
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(dataset, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote {dataset['count']} questions -> {out_path}")


if __name__ == "__main__":
    _main()
