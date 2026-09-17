#!/usr/bin/env python3
"""把 ``research_report.file_path`` 從舊掛載點重指到現在的鏡像目錄（唯讀試跑，``--apply`` 才寫）。

為什麼需要這支：15,196 列裡有 14,976 列的 ``file_path`` 仍指向
``/mnt/c/Users/.../研報自動匯入``——那是 Windows 側的 9p 掛載，也是 2026-08-18 事故的
根因檔案系統。R2 遷移、夜間回填、閱讀頁的 ``has_file`` 與 hybrid 的本機回退全都照
``file_path`` 讀檔：留著它等於 15 GB 走 9p、外加 Windows 側沒掛好那天全部靜默找不到檔。

判定刻意保守：只在「目標檔存在且大小與舊檔相同」時才改。hash 驗證交給
``migrate_object_storage.py``——它本來就會逐檔 sha256 並把不符者列成 ``HASH_MISMATCH``，
這裡再算一次只是把 15 GB 多讀一遍。舊檔 stat 不到（9p 沒掛）時用 ``--verify-hash`` 改以
``file_hash`` 驗目標檔，代價是讀完整檔。

只改 ``file_path`` 一個欄位、只改前綴命中的列、每列各自一個 UPDATE；不動 ``file_name``、
``source_object_key`` 或任何衍生表。可安全重跑：已指向鏡像的列不會再被列出。
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from app.services.db import SessionFactory  # noqa: E402

MIRROR_DIRNAME = "研報自動匯入"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="把 research_report.file_path 重指到 ext4 鏡像")
    parser.add_argument("--old-prefix", required=True,
                        help="要取代的舊前綴，例如 /mnt/c/Users/User/Desktop/Project/report-mark/研報自動匯入")
    parser.add_argument("--mirror-root", required=True,
                        help="現在的鏡像目錄，例如 /home/kashionz/projects/report-mark/研報自動匯入")
    parser.add_argument("--apply", action="store_true", help="真的寫 DB；不帶＝只列計畫")
    parser.add_argument("--verify-hash", action="store_true",
                        help="以 file_hash 驗目標檔（舊檔 stat 不到時用；讀完整檔）")
    parser.add_argument("--limit", type=int, default=0, help="最多處理幾列（0 不限）")
    return parser.parse_args(argv)


@dataclass(frozen=True)
class Plan:
    report_id: str
    old_path: str
    new_path: str
    verdict: str  # REPOINT / MISSING_TARGET / SIZE_MISMATCH / OLD_UNREADABLE / HASH_MISMATCH


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def plan_row(report_id: str, file_hash: str, old_path: str, *, old_prefix: str, mirror_root: str,
             verify_hash: bool) -> Plan:
    """純函式：決定這一列要不要改、改成什麼。不碰 DB。"""
    relative = old_path[len(old_prefix):].lstrip("/")
    new_path = os.path.join(mirror_root, relative)
    target = Path(new_path)
    if not target.is_file():
        return Plan(report_id, old_path, new_path, "MISSING_TARGET")
    if verify_hash:
        return Plan(report_id, old_path, new_path, "REPOINT" if _sha256(target) == file_hash else "HASH_MISMATCH")
    try:
        old_size = os.path.getsize(old_path)
    except OSError:
        return Plan(report_id, old_path, new_path, "OLD_UNREADABLE")
    if old_size != target.stat().st_size:
        return Plan(report_id, old_path, new_path, "SIZE_MISMATCH")
    return Plan(report_id, old_path, new_path, "REPOINT")


def normalize_prefix(prefix: str) -> str:
    """去掉尾端斜線，讓 ``prefix/`` 與 ``prefix`` 兩種寫法等價；LIKE 比對只看前綴。"""
    return prefix.rstrip("/")


async def _rows(old_prefix: str, limit: int) -> list[tuple[str, str, str]]:
    sql = (
        "SELECT id::text, file_hash, file_path FROM research.research_report "
        "WHERE file_path LIKE :prefix ORDER BY id"
    )
    params: dict = {"prefix": old_prefix + "/%"}
    if limit:
        sql += " LIMIT :limit"
        params["limit"] = limit
    async with SessionFactory() as session:
        result = await session.execute(text(sql), params)
        return [(row[0], row[1], row[2]) for row in result]


async def _apply(plan: Plan) -> bool:
    # WHERE 帶舊路徑：同一列若已被別人改過，這裡就是 0 列、不會覆蓋。
    async with SessionFactory() as session:
        result = await session.execute(
            text("UPDATE research.research_report SET file_path = :new WHERE id = :id AND file_path = :old"),
            {"new": plan.new_path, "id": plan.report_id, "old": plan.old_path},
        )
        await session.commit()
    return getattr(result, "rowcount", 1) != 0


async def run(args: argparse.Namespace) -> int:
    old_prefix = normalize_prefix(args.old_prefix)
    mirror_root = normalize_prefix(args.mirror_root)
    if not Path(mirror_root).is_dir():
        print(f"鏡像目錄不存在：{mirror_root}")
        return 2
    rows = await _rows(old_prefix, args.limit)
    stats: dict[str, int] = {}
    for report_id, file_hash, old_path in rows:
        plan = await asyncio.to_thread(
            plan_row, report_id, file_hash, old_path,
            old_prefix=old_prefix, mirror_root=mirror_root, verify_hash=args.verify_hash,
        )
        verdict = plan.verdict
        if verdict == "REPOINT" and args.apply:
            verdict = "REPOINTED" if await _apply(plan) else "SKIP_CHANGED_MEANWHILE"
        stats[verdict] = stats.get(verdict, 0) + 1
        if verdict not in {"REPOINT", "REPOINTED"}:
            print(f"{verdict} id={plan.report_id} old={plan.old_path} new={plan.new_path}")
    print(
        f"summary rows={len(rows)} " + " ".join(f"{k}={v}" for k, v in sorted(stats.items()))
        + f" apply={args.apply} verify_hash={args.verify_hash}"
    )
    problems = sum(v for k, v in stats.items() if k not in {"REPOINT", "REPOINTED"})
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run(parse_args())))
