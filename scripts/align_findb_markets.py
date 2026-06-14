"""把現有市場標籤（舊中文）重映射為 findb 市場代碼。

- 改寫 data/tags/*.json 的 market 欄位（保持與 DB 一致、resume 安全）
- 更新 DB research.research_report.market

對映規則見 app/services/tagging.LEGACY_TO_FINDB（債券→MACRO、原物料→GLOBAL）。
冪等：已是 findb 代碼者不變。
用法：uv run python scripts/align_findb_markets.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from app.services.db import SessionFactory  # noqa: E402
from app.services.tagging import normalize_market  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
TAGS_DIR = ROOT / "data" / "tags"


def remap_tag_files() -> Counter:
    counts: Counter = Counter()
    for p in sorted(TAGS_DIR.glob("*.json")):
        try:
            obj = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            counts["parse_error"] += 1
            continue
        old = obj.get("market")
        new = normalize_market(old)
        if new != old:
            obj["market"] = new
            p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
            counts["rewritten"] += 1
        counts[f"→{new}"] += 1
    return counts


async def remap_db() -> Counter:
    counts: Counter = Counter()
    async with SessionFactory() as session:
        rows = (
            await session.execute(
                text("SELECT id::text, market FROM research.research_report")
            )
        ).all()
        for rid, market in rows:
            new = normalize_market(market)
            if new != market:
                await session.execute(
                    text(
                        "UPDATE research.research_report SET market = :m WHERE id = :id"
                    ),
                    {"m": new, "id": rid},
                )
                counts["updated"] += 1
            counts[f"→{new}"] += 1
        await session.commit()
    return counts


async def main() -> None:
    tag_counts = remap_tag_files()
    db_counts = await remap_db()
    print("=== tag files ===")
    for k, v in sorted(tag_counts.items()):
        print(f"  {k}: {v}")
    print("=== db rows ===")
    for k, v in sorted(db_counts.items()):
        print(f"  {k}: {v}")


if __name__ == "__main__":
    asyncio.run(main())
