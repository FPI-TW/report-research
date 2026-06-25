"""一次性回填 research_report.report_date（既有列僅 52% 有日期，致問答新近度排序失效）。

兩階段、冪等、只補 NULL（不覆蓋既有日期）：
  1) 檔名：擴充後的 parse_filename（年份明確的 YYYY_MM_DD / MMDDYYYY / 唯一 6 位數）。
  2) 檔案 mtime：剩餘 NULL 者改用 file_path 的修改時間（實測與真實出版日中位數僅差 1 天、
     近 100% 覆蓋）；防呆見 mtime_report_date——mtime 太接近 created_at（批次複製時間）→ 留 NULL。

根因見：filename._parse_date 舊版只認 YYYYMMDD → report_date 多為 NULL → _recency_factor=0
→ build_context 偏好最新/過舊截斷全失效。本腳本修存量，filename 修正修未來匯入。

用法：
  uv run python scripts/backfill_report_dates.py --dry-run   # 只統計，不寫入
  uv run python scripts/backfill_report_dates.py             # 實際回填
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from app.services.db import SessionFactory  # noqa: E402
from app.services.filename import mtime_report_date, parse_filename  # noqa: E402


def _as_date(value) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return None


def _file_mtime_date(file_path: str | None) -> date | None:
    """讀 file_path 的 mtime（轉本地日期）；檔不存在或無路徑 → None。"""
    if not file_path or not os.path.exists(file_path):
        return None
    try:
        return date.fromtimestamp(os.path.getmtime(file_path))
    except OSError:
        return None


async def _apply(session, updates: list[tuple[str, date]], dry_run: bool) -> int:
    """把 (id, date) 寫回（只在仍為 NULL 時更新，冪等）；dry-run 不寫。回實際更新筆數。"""
    if dry_run or not updates:
        return len(updates)
    done = 0
    for rid, d in updates:
        res = await session.execute(
            text(
                "UPDATE research.research_report SET report_date = :d "
                "WHERE id = :id AND report_date IS NULL"
            ),
            {"d": d, "id": rid},
        )
        done += res.rowcount or 0
    await session.commit()
    return done


async def pass_filename(dry_run: bool) -> int:
    """階段 1：用擴充後 parse_filename 補檔名含年份者。"""
    async with SessionFactory() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT id::text, file_name FROM research.research_report "
                    "WHERE report_date IS NULL ORDER BY id"
                )
            )
        ).all()
        updates = [
            (rid, d)
            for rid, fn in rows
            if (d := parse_filename(fn).report_date) is not None
        ]
        applied = await _apply(session, updates, dry_run)
    print(
        f"[pass 1 filename] scanned={len(rows)} resolved={len(updates)} "
        f"applied={applied}{' (dry-run)' if dry_run else ''}"
    )
    return len(updates)


async def pass_mtime(dry_run: bool, batch: int) -> int:
    """階段 2：剩餘 NULL 者用 file_path 的 mtime 補（防呆排除批次複製時間）。"""
    resolved = skipped_copy = no_file = 0
    last_id: str | None = None
    samples: list[str] = []
    async with SessionFactory() as session:
        while True:
            rows = (
                await session.execute(
                    text(
                        "SELECT id::text, file_name, file_path, created_at "
                        "FROM research.research_report "
                        "WHERE report_date IS NULL "
                        "AND (:last IS NULL OR id > CAST(:last AS uuid)) "
                        "ORDER BY id LIMIT :lim"
                    ),
                    {"last": last_id, "lim": batch},
                )
            ).all()
            if not rows:
                break
            last_id = rows[-1][0]
            updates: list[tuple[str, date]] = []
            for rid, fn, fp, created in rows:
                mt = _file_mtime_date(fp)
                if mt is None:
                    no_file += 1
                    continue
                d = mtime_report_date(mt, _as_date(created))
                if d is None:
                    skipped_copy += 1
                    continue
                updates.append((rid, d))
                if len(samples) < 8:
                    samples.append(f"{d}  {fn[:48]}")
            resolved += len(updates)
            await _apply(session, updates, dry_run)
    print(
        f"[pass 2 mtime   ] resolved={resolved} skipped_copy_date={skipped_copy} "
        f"file_missing={no_file}{' (dry-run)' if dry_run else ''}"
    )
    for s in samples:
        print(f"    e.g. {s}")
    return resolved


async def remaining_null() -> int:
    async with SessionFactory() as session:
        return (
            await session.execute(
                text(
                    "SELECT count(*) FROM research.research_report "
                    "WHERE report_date IS NULL"
                )
            )
        ).scalar() or 0


async def main() -> None:
    ap = argparse.ArgumentParser(description="回填 research_report.report_date")
    ap.add_argument("--dry-run", action="store_true", help="只統計，不寫入")
    ap.add_argument("--batch", type=int, default=1000, help="mtime 階段每批列數")
    args = ap.parse_args()

    before = await remaining_null()
    print(f"NULL report_date before: {before}")
    await pass_filename(args.dry_run)
    await pass_mtime(args.dry_run, args.batch)
    after = await remaining_null()
    print(f"NULL report_date after : {after}  (recovered {before - after})")


if __name__ == "__main__":
    asyncio.run(main())
