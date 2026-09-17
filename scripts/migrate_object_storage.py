"""Upload legacy local report/PDF artifacts to private R2 and persist missing object keys.

This is deliberately separate from ``reconcile_object_storage.py``: it mutates only one
nullable key field after a successful upload, never deletes or re-ingests anything.  Re-running
is safe because rows that already have a key are skipped.  An upload followed by a DB failure is
reported as a reconcilable orphan; it is never deleted automatically.
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
    generated_object_key_for_sha,
    get_object_storage,
    original_object_key,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="將 legacy local artifacts 安全遷移到私有 R2")
    parser.add_argument("--dry-run", action="store_true", help="驗證並列出計畫，不 upload 或更新 DB")
    parser.add_argument("--kind", choices=("originals", "generated", "all"), default="all")
    parser.add_argument("--limit", type=int, default=0, help="最多處理幾筆 DB artifacts（所有 kind 合計；0 不限）")
    parser.add_argument("--concurrency", type=int, default=4, help="hash/upload 併發數")
    return parser.parse_args()


async def _rows(kind: str, limit: int) -> list[dict]:
    """Read only un-migrated rows; limit is global across originals/base/renditions."""
    rows: list[dict] = []

    def limited_sql(base: str) -> tuple[str, dict]:
        remaining = limit - len(rows)
        if limit and remaining <= 0:
            return "", {}
        return (base + " LIMIT :limit", {"limit": remaining}) if limit else (base, {})

    async with SessionFactory() as session:
        if kind in {"originals", "all"}:
            sql, params = limited_sql(
                "SELECT id::text, file_hash, file_name, file_path FROM research.research_report "
                "WHERE source_object_key IS NULL ORDER BY id"
            )
            if sql:
                result = await session.execute(text(sql), params)
                rows.extend(
                    {"kind": "original", "id": row[0], "hash": row[1], "name": row[2], "path": row[3]}
                    for row in result
                )
        if kind in {"generated", "all"}:
            sql, params = limited_sql(
                "SELECT id::text, pdf_path FROM research.report_doc WHERE pdf_object_key IS NULL ORDER BY id"
            )
            if sql:
                result = await session.execute(text(sql), params)
                rows.extend({"kind": "base", "id": row[0], "path": row[1]} for row in result)
            sql, params = limited_sql(
                "SELECT id::text, report_id::text, pdf_path FROM research.report_rendition "
                "WHERE pdf_object_key IS NULL ORDER BY id"
            )
            if sql:
                result = await session.execute(text(sql), params)
                rows.extend(
                    {"kind": "rendition", "id": row[0], "report_id": row[1], "path": row[2]} for row in result
                )
    return rows


def _file_sha256(path: str) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verified_key(row: dict) -> tuple[str | None, str | None, str | None]:
    """Return ``(key, problem, digest)`` after hashing local content; no R2/DB side effects."""
    path = row.get("path")
    if not path or not Path(path).is_file():
        return None, "MISSING_LOCAL", None
    digest = _file_sha256(path)
    if row["kind"] == "original":
        if digest != row["hash"]:
            return None, f"HASH_MISMATCH expected={row['hash']} actual={digest}", digest
        return original_object_key(row["hash"], row["name"]), None, digest
    report_id = row.get("report_id") or row["id"]
    rendition_id = row["id"] if row["kind"] == "rendition" else None
    return generated_object_key_for_sha(report_id, digest, rendition_id), None, digest


async def _update_key(row: dict, key: str) -> bool:
    """Set only an as-yet-null key.  Each task owns its own session/transaction."""
    if row["kind"] == "original":
        sql = (
            "UPDATE research.research_report SET source_object_key = :key "
            "WHERE id = :id AND source_object_key IS NULL"
        )
    elif row["kind"] == "base":
        sql = "UPDATE research.report_doc SET pdf_object_key = :key WHERE id = :id AND pdf_object_key IS NULL"
    else:
        sql = "UPDATE research.report_rendition SET pdf_object_key = :key WHERE id = :id AND pdf_object_key IS NULL"
    async with SessionFactory() as session:
        result = await session.execute(text(sql), {"id": row["id"], "key": key})
        await session.commit()
    # SQLAlchemy exposes rowcount for UPDATE; tolerate minimal test fakes that do not.
    return getattr(result, "rowcount", 1) != 0


async def run(args: argparse.Namespace) -> int:
    storage = get_object_storage()
    if not storage.enabled:
        print("OBJECT_STORAGE_MODE=local：遷移需要 hybrid 或 r2（未做任何動作）")
        return 1
    rows = await _rows(args.kind, args.limit)
    stats = {"planned": 0, "migrated": 0, "skipped": 0, "missing": 0, "hash_mismatch": 0, "errors": 0, "orphans": 0}
    sem = asyncio.Semaphore(max(1, args.concurrency))

    async def migrate(row: dict) -> None:
        # Hashing can be as expensive as the upload (and dry-run deliberately hashes too), so
        # one semaphore covers the complete per-row local/R2 work.  Do not move verification
        # outside this scope: that would let --dry-run saturate disks despite --concurrency.
        async with sem:
            key, problem, digest = await asyncio.to_thread(_verified_key, row)
            if problem:
                if problem == "MISSING_LOCAL":
                    stats["missing"] += 1
                else:
                    stats["hash_mismatch"] += 1
                print(f"{problem} {row['kind']} id={row['id']} path={row.get('path')}")
                return
            assert key is not None and digest is not None
            stats["planned"] += 1
            if args.dry_run:
                print(f"PLAN {row['kind']} id={row['id']} key={key}")
                return
            try:
                # upload_file hashes again for metadata while streaming the upload, and happens
                # before the DB commit by design.  A later DB failure is an explicit orphan.
                await asyncio.to_thread(storage.upload_file, row["path"], key, expected_sha256=digest)
            except Exception as exc:  # noqa: BLE001 - a vanished/read-failed local file must not abort the batch
                stats["errors"] += 1
                print(f"ERROR upload {row['kind']} id={row['id']} key={key}: {exc}")
                return
            try:
                updated = await _update_key(row, key)
            except Exception as exc:  # noqa: BLE001 - DB failures must preserve orphan evidence
                stats["errors"] += 1
                stats["orphans"] += 1
                print(f"ORPHAN {key} upload_succeeded_db_update_failed={exc!r}")
                return
        if updated:
            stats["migrated"] += 1
            print(f"MIGRATED {row['kind']} id={row['id']} key={key}")
        else:
            # A concurrent worker may have persisted the key after this scan.  The uploaded
            # object uses the same content-addressed key, so this is safe and rerunnable.
            stats["skipped"] += 1
            print(f"SKIP_ALREADY_MIGRATED {row['kind']} id={row['id']} key={key}")

    await asyncio.gather(*(migrate(row) for row in rows))
    print("summary " + " ".join(f"{key}={value}" for key, value in stats.items()) + f" dry_run={args.dry_run}")
    return 1 if stats["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run(parse_args())))
