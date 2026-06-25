"""回填／校正 research_report.source。

來源券商原本只由 parse_filename 檔名解析產生，而大宗格式檔名不帶券商 token
（daily story_/Takeaway_/速報/國際金融市場焦點/投資早報…）→ 約 67% 為 NULL；且檔名解析
會把「標的公司」誤當券商（如「2882國泰金…-報告.pdf」實為元富投顧發行）。本腳本以
filename.resolve_source（本土發行機構內文指紋 → 檔名 token → 外資內文指紋）統一回填／校正。

兩種模式（皆冪等；只依 file_name + full_text 重算，與既有值無關）：
  預設（補洞）  ：只填 source 為 NULL/空者，不動既有標籤。
  --reconcile  ：對全部列重算，發行機構內文指紋與既有標籤衝突時「校正」既有值
                 （修檔名 subject-company 誤判，以及補洞階段早期版本誤標的彙整型研報）。
                 永不把既有標籤清成 NULL（重算不出來時保留原值）。

用法：
  uv run python scripts/backfill_report_sources.py --dry-run               # 補洞，只統計
  uv run python scripts/backfill_report_sources.py                          # 補洞，實寫
  uv run python scripts/backfill_report_sources.py --reconcile --dry-run    # 校正，只統計
  uv run python scripts/backfill_report_sources.py --reconcile              # 校正，實寫
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from app.services.db import SessionFactory  # noqa: E402
from app.services.filename import CJK_SIG_WINDOW, resolve_source  # noqa: E402


async def _update(session, rid: str, src: str, *, override: bool) -> int:
    """寫回單列。override=False 只在仍為 NULL/空時更新；True 則覆蓋（皆冪等）。"""
    guard = "" if override else " AND (source IS NULL OR source = '')"
    res = await session.execute(
        text(f"UPDATE research.research_report SET source = :s WHERE id = :id{guard}"),
        {"s": src, "id": rid},
    )
    return res.rowcount or 0


async def run(reconcile: bool, dry_run: bool, batch: int) -> None:
    where = "" if reconcile else "WHERE (source IS NULL OR source = '')"
    filled = 0  # NULL→value
    changed = 0  # value→不同 value（僅 reconcile）
    pairs: collections.Counter = collections.Counter()
    src_counter: collections.Counter = collections.Counter()
    samples: list[str] = []
    last_id = ""
    async with SessionFactory() as session:
        while True:
            rows = (
                await session.execute(
                    text(
                        "SELECT id::text, file_name, coalesce(source, ''), "
                        "left(full_text, :win) "
                        "FROM research.research_report "
                        f"{where}{' AND' if where else 'WHERE'} id::text > :last "
                        "ORDER BY id LIMIT :lim"
                    ),
                    {"win": CJK_SIG_WINDOW, "last": last_id, "lim": batch},
                )
            ).all()
            if not rows:
                break
            last_id = rows[-1][0]
            for rid, fn, cur, ft in rows:
                new = resolve_source(fn, ft)
                if not new or new == cur:
                    continue
                is_fill = cur == ""
                if not is_fill and not reconcile:
                    continue  # 補洞模式不覆蓋既有
                if not dry_run:
                    await _update(session, rid, new, override=reconcile)
                if is_fill:
                    filled += 1
                else:
                    changed += 1
                    pairs[(cur, new)] += 1
                src_counter[new] += 1
                if len(samples) < 12:
                    tag = "fill" if is_fill else f"{cur}→{new}"
                    samples.append(f"{new:14} [{tag}]  {fn[:46]}")
            if not dry_run:
                await session.commit()
    tail = " (dry-run)" if dry_run else ""
    print(f"[{'reconcile' if reconcile else 'backfill'}] filled={filled} changed={changed}{tail}")
    print("  by resolved source:", dict(src_counter.most_common()))
    if pairs:
        print("  corrections (old→new):")
        for (a, b), c in pairs.most_common():
            print(f"      {c:4d}  {a} -> {b}")
    for s in samples:
        print(f"    e.g. {s}")


async def remaining_null() -> int:
    async with SessionFactory() as session:
        return (
            await session.execute(
                text(
                    "SELECT count(*) FROM research.research_report "
                    "WHERE source IS NULL OR source = ''"
                )
            )
        ).scalar() or 0


async def main() -> None:
    ap = argparse.ArgumentParser(description="回填／校正 research_report.source")
    ap.add_argument("--dry-run", action="store_true", help="只統計，不寫入")
    ap.add_argument(
        "--reconcile",
        action="store_true",
        help="全列重算並校正衝突的既有標籤（預設只補 NULL）",
    )
    ap.add_argument("--batch", type=int, default=1000, help="每批列數")
    args = ap.parse_args()

    before = await remaining_null()
    print(f"NULL source before: {before}")
    await run(args.reconcile, args.dry_run, args.batch)
    after = await remaining_null()
    print(f"NULL source after : {after}  (filled {before - after})")


if __name__ == "__main__":
    asyncio.run(main())
