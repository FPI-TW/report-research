"""一次性回填 research_report.full_text（從 extracted jsonl 的原始 text）。

不重新分塊/嵌入，只 UPDATE 既有列的 full_text 欄；依 file_hash 對應。
用法：uv run python scripts/backfill_full_text.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from app.services.db import SessionFactory  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
EXTRACTED = ROOT / "data" / "extracted" / "sample.jsonl"


async def main() -> None:
    records = [
        json.loads(line)
        for line in EXTRACTED.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    updated = 0
    skipped = 0
    async with SessionFactory() as session:
        for rec in records:
            body = rec.get("text")
            if not body:
                skipped += 1
                continue
            res = await session.execute(
                text(
                    "UPDATE research.research_report "
                    "SET full_text = :t WHERE file_hash = :h"
                ),
                {"t": body, "h": rec["file_hash"]},
            )
            if res.rowcount:
                updated += res.rowcount
            else:
                skipped += 1
        await session.commit()
    print(f"backfilled full_text: updated={updated} skipped={skipped}")


if __name__ == "__main__":
    asyncio.run(main())
