"""一次性清理 report_chunk.content（CJK 間空白等 PDF 排版雜訊）+ ANALYZE。

content 更新後 content_norm（generated column）自動重算。不重新嵌入。
冪等：clean_text 已清理過的內容不會再變，重跑為 0 筆更新。
用法：uv run python scripts/normalize_chunks.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from app.services.db import SessionFactory  # noqa: E402
from app.services.textnorm import clean_text  # noqa: E402

BATCH_COMMIT = 500


async def main() -> None:
    updated = 0
    unchanged = 0
    async with SessionFactory() as session:
        rows = (
            await session.execute(
                text("SELECT id, content FROM research.report_chunk")
            )
        ).all()
        for cid, content in rows:
            cleaned = clean_text(content)
            if cleaned == content:
                unchanged += 1
                continue
            await session.execute(
                text("UPDATE research.report_chunk SET content = :c WHERE id = :id"),
                {"c": cleaned, "id": cid},
            )
            updated += 1
            if updated % BATCH_COMMIT == 0:
                await session.commit()
                print(f"  updated={updated} ...", flush=True)
        await session.commit()
        await session.execute(text("ANALYZE research.report_chunk"))
        await session.commit()
    print(f"normalize chunks: updated={updated} unchanged={unchanged}")


if __name__ == "__main__":
    asyncio.run(main())
