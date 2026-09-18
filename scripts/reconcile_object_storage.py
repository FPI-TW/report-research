"""Non-destructive R2 inventory/reconciliation for report originals.

Examples:
    uv run python scripts/reconcile_object_storage.py --dry-run --kind all
    uv run python scripts/reconcile_object_storage.py --kind originals --limit 100 --concurrency 8

It never deletes remote objects.  "Orphan" means an object under the owned prefix has no
corresponding DB key; investigate or archive it manually after a review.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from app.services.db import SessionFactory  # noqa: E402
from app.services.object_storage import (  # noqa: E402
    ObjectNotFound,
    ObjectStorageError,
    get_object_storage,
    original_object_key,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="比對私有 R2 與 report-mark DB（不刪除）")
    parser.add_argument("--dry-run", action="store_true", help="僅列出計畫／差異，不寫 DB 或 R2")
    # 生成 PDF 那一半移除後只剩 originals；仍收 "all" 讓 report-mark-r2-reconcile.service 的命令列不必改。
    parser.add_argument("--kind", choices=("originals", "all"), default="all")
    parser.add_argument("--limit", type=int, default=0, help="最多檢查幾筆 DB 產物（0 不限）")
    parser.add_argument("--concurrency", type=int, default=4, help="R2 查詢併發數")
    return parser.parse_args()


async def _rows(kind: str, limit: int) -> list[dict]:
    rows: list[dict] = []
    async with SessionFactory() as session:
        sql = (
            "SELECT id::text, file_hash, file_name, file_path, source_object_key "
            "FROM research.research_report ORDER BY id"
        )
        params: dict = {}
        if limit:
            sql += " LIMIT :limit"
            params = {"limit": limit}
        result = await session.execute(text(sql), params)
        rows.extend(
            {"kind": "original", "id": r[0], "hash": r[1], "name": r[2], "path": r[3], "key": r[4]}
            for r in result
        )
    return rows


def _local_hash(path: str | None) -> tuple[int, str] | None:
    if not path or not Path(path).is_file():
        return None
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return Path(path).stat().st_size, digest.hexdigest()


def _expected_sha(row: dict, key: str) -> str | None:
    """Originals use the DB SHA (the key itself is derived from it)."""
    return row["hash"]


async def run(args: argparse.Namespace) -> int:
    storage = get_object_storage()
    if not storage.enabled:
        print("OBJECT_STORAGE_MODE=local：沒有 R2 可對帳（未做任何動作）")
        return 0
    rows = await _rows(args.kind, args.limit)
    expected: set[str] = set()
    stats = {
        "checked": 0, "missing": 0, "unkeyed": 0, "size_mismatch": 0,
        "sha_mismatch": 0, "sha_metadata_missing": 0, "key_mismatch": 0, "errors": 0, "orphans": 0,
    }
    sem = asyncio.Semaphore(max(1, args.concurrency))

    async def inspect(row: dict) -> None:
        key = row.get("key")
        if not key:
            stats["unkeyed"] += 1
            # A canonical original may already exist after an upload-before-DB failure, but
            # the absent DB pointer is itself non-ready state.  Do not infer it into expected:
            # inventory should still report that object as a recoverable orphan evidence.
            canonical = f" canonical={original_object_key(row['hash'], row['name'])}"
            print(f"UNKEYED {row['kind']} id={row['id']}{canonical}")
            return
        canonical = original_object_key(row["hash"], row["name"])
        if key != canonical:
            # Do not contact R2 for a DB pointer that is not the canonical key.  This also
            # keeps a bad pointer out of the expected inventory set below.
            stats["key_mismatch"] += 1
            print(f"KEY_MISMATCH {key} expected={canonical} db_id={row['id']}")
            return
        async with sem:
            try:
                metadata = await asyncio.to_thread(storage.head_object, key)
            except ObjectNotFound:
                stats["checked"] += 1
                stats["missing"] += 1
                print(f"MISSING {key} db_id={row['id']}")
                return
            except ObjectStorageError as exc:
                stats["errors"] += 1
                print(f"ERROR {key}: {exc}")
                return
            try:
                # Keep the potentially long streamed read under the requested R2 concurrency
                # bound too, rather than allowing every row to download at once after HEAD.
                remote_sha = await asyncio.to_thread(storage.sha256, key)
            except ObjectNotFound:
                # A concurrent lifecycle rule may remove an object between HEAD and GET.
                # It is still a confirmed missing object, not a service failure.
                stats["checked"] += 1
                stats["missing"] += 1
                print(f"MISSING {key} db_id={row['id']}")
                return
            except ObjectStorageError as exc:
                stats["errors"] += 1
                print(f"ERROR {key} hash: {exc}")
                return
        stats["checked"] += 1
        local = _local_hash(row.get("path"))
        remote_size = metadata.get("ContentLength")
        if local and remote_size is not None and int(remote_size) != local[0]:
            stats["size_mismatch"] += 1
            print(f"SIZE_MISMATCH {key} local={local[0]} remote={remote_size}")
        expected_sha = _expected_sha(row, key)
        metadata = metadata.get("Metadata") or {}
        metadata_sha = metadata.get("sha256") or metadata.get("SHA256")
        if not metadata_sha:
            # Legacy uploads without the sha256 metadata cannot prove their digest from
            # metadata alone.  Report this explicitly rather than silently treating as PASS.
            stats["sha_metadata_missing"] += 1
            print(f"SHA_METADATA_MISSING {key}")
        elif metadata_sha != remote_sha:
            stats["sha_mismatch"] += 1
            print(f"SHA_MISMATCH {key} metadata={metadata_sha} remote={remote_sha}")
        expected.add(key)
        if expected_sha and not remote_sha.startswith(expected_sha):
            stats["sha_mismatch"] += 1
            print(f"SHA_MISMATCH {key} expected={expected_sha} remote={remote_sha}")
        if local and remote_sha != local[1]:
            stats["sha_mismatch"] += 1
            print(f"SHA_MISMATCH {key} local={local[1]} remote={remote_sha}")

    await asyncio.gather(*(inspect(row) for row in rows))
    # Orphan classification is only sound when every DB row in the selected kind was read.
    # With a limit, a remote key may belong to an unselected valid row, so never call it orphan.
    if args.limit:
        print("ORPHAN_SCAN_SKIPPED --limit only checks selected DB rows")
        print("summary " + " ".join(f"{k}={v}" for k, v in stats.items()) + f" dry_run={args.dry_run}")
        return 1 if stats["errors"] or stats["unkeyed"] or stats["key_mismatch"] else 0

    # Inventory only the owned originals/ prefix.  It is deliberately reporting-only.
    # 生成 PDF 功能移除後 generated/ 前綴不再有 DB 對應列；bucket 裡若還有舊的生成 PDF，
    # 由人依 docs/WORKFLOW.md 的說明手動清理，這裡不把它們算成 orphan。
    try:
        remote_keys: set[str] = set(await asyncio.to_thread(storage.list_keys, "originals/"))
        for key in sorted(remote_keys - expected):
            stats["orphans"] += 1
            print(f"ORPHAN {key}")
    except ObjectStorageError as exc:
        stats["errors"] += 1
        print(f"ERROR inventory: {exc}")
    print("summary " + " ".join(f"{k}={v}" for k, v in stats.items()) + f" dry_run={args.dry_run}")
    return 1 if stats["errors"] or stats["unkeyed"] or stats["key_mismatch"] else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run(parse_args())))
