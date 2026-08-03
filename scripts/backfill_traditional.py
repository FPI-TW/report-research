"""把既有 LLM 產出的顯示文字裡的簡體字回填成繁體（台灣標準字形）。

寫入端已由 app/services/zh_hant.to_traditional 守住（generate_titles /
generate_summaries / extract_takeaways / signal_extract 四處），這支負責清掉
守門上線之前就寫進去的存量。判定與轉換規則完全共用同一個模組，所以這支跑完
之後再跑，命中數必為 0（冪等）。

涵蓋四個欄位，全部是 LLM 自己的轉述文字：
  research_report.title / research_report.summary
  report_takeaway.claim
  report_signal.thesis_dimensions[*].summary

**刻意不涵蓋**（動了會壞掉，別順手加）：
  report_takeaway.quote、report_signal.thesis_dimensions[*].evidence
      逐字引文／原句。改一個字就不再是逐字引文——這個理由與任何功能無關。
      前者另外還是 reading/anchor.locate_quote 的錨定基準，轉了就錨不回
      canonical text，而且錨不到不會報錯，只會讓 quote_start/quote_end 靜默留空。
  research_report.title_original
      原文（英文或其他語言），保留原樣正是它存在的理由。
  research_report.full_text、report_chunk.content
      語料本身，不是 LLM 產出。全語料有 63 篇研報原文就是簡體，那是來源事實。
      chunk 另有獨立理由碰不得（見 CLAUDE.md 對 make normalize 死法的記載）。

用法：
    uv run python scripts/backfill_traditional.py            # 唯讀，只列出會改什麼
    uv run python scripts/backfill_traditional.py --apply    # 實際寫入
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from app.services.db import SessionFactory  # noqa: E402
from app.services.zh_hant import to_traditional  # noqa: E402

# (標籤, SELECT, UPDATE)：SELECT 回 (key, value)，UPDATE 吃 :key/:value
PLAIN_TARGETS = [
    (
        "research_report.title",
        "SELECT id::text, title FROM research.research_report WHERE title IS NOT NULL",
        "UPDATE research.research_report SET title = :value WHERE id = CAST(:key AS uuid)",
    ),
    (
        "research_report.summary",
        "SELECT id::text, summary FROM research.research_report WHERE summary IS NOT NULL",
        "UPDATE research.research_report SET summary = :value WHERE id = CAST(:key AS uuid)",
    ),
    (
        "report_takeaway.claim",
        "SELECT id::text, claim FROM research.report_takeaway WHERE claim IS NOT NULL",
        "UPDATE research.report_takeaway SET claim = :value WHERE id = CAST(:key AS uuid)",
    ),
]

SIGNAL_SELECT = (
    "SELECT id::text, thesis_dimensions FROM research.report_signal "
    "WHERE thesis_dimensions IS NOT NULL"
)
SIGNAL_UPDATE = (
    "UPDATE research.report_signal SET thesis_dimensions = CAST(:value AS jsonb) "
    "WHERE id = CAST(:key AS uuid)"
)


def _preview(before: str, after: str, width: int = 78) -> str:
    return f"      舊 {before[:width]}\n      新 {after[:width]}"


async def backfill_plain(label: str, select_sql: str, update_sql: str, apply: bool) -> int:
    async with SessionFactory() as session:
        rows = (await session.execute(text(select_sql))).all()
    changes = [(k, v, to_traditional(v)) for k, v in rows if v]
    changes = [(k, v, c) for k, v, c in changes if c != v]

    print(f"\n{label}: {len(changes)} / {len(rows)} 列需回填")
    for _, before, after in changes:
        print(_preview(before, after))
    if changes and apply:
        async with SessionFactory() as session:
            for key, _, after in changes:
                await session.execute(text(update_sql), {"key": key, "value": after})
            await session.commit()
        print(f"  → 已寫入 {len(changes)} 列")
    return len(changes)


async def backfill_signals(apply: bool) -> int:
    """雷達四維論點：只改每維的 summary，evidence 與 stance 原樣搬回。"""
    async with SessionFactory() as session:
        rows = (await session.execute(text(SIGNAL_SELECT))).all()

    changes: list[tuple[str, dict, list[tuple[str, str]]]] = []
    for rid, raw in rows:
        obj = raw if isinstance(raw, dict) else json.loads(raw or "{}")
        if not isinstance(obj, dict):
            continue
        updated, diffs = dict(obj), []
        for dim, cell in obj.items():
            if not isinstance(cell, dict) or not isinstance(cell.get("summary"), str):
                continue
            after = to_traditional(cell["summary"])
            if after != cell["summary"]:
                updated[dim] = {**cell, "summary": after}
                diffs.append((cell["summary"], after))
        if diffs:
            changes.append((rid, updated, diffs))

    n_dims = sum(len(d) for _, _, d in changes)
    print(f"\nreport_signal.thesis_dimensions[*].summary: {n_dims} 個維度字串"
          f"（{len(changes)} / {len(rows)} 列）需回填")
    for _, _, diffs in changes:
        for before, after in diffs:
            print(_preview(before, after))
    if changes and apply:
        async with SessionFactory() as session:
            for rid, updated, _ in changes:
                await session.execute(
                    text(SIGNAL_UPDATE),
                    {"key": rid, "value": json.dumps(updated, ensure_ascii=False)},
                )
            await session.commit()
        print(f"  → 已寫入 {len(changes)} 列")
    return n_dims


async def main(apply: bool) -> None:
    print("模式：" + ("寫入（--apply）" if apply else "唯讀試跑（加 --apply 才會寫）"))
    total = 0
    for label, select_sql, update_sql in PLAIN_TARGETS:
        total += await backfill_plain(label, select_sql, update_sql, apply)
    total += await backfill_signals(apply)
    print(f"\n合計 {total} 處" + ("已回填。" if apply else "待回填。"))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="實際寫入（預設只試跑）")
    asyncio.run(main(ap.parse_args().apply))
