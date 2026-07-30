"""讀 extracted JSONL + Claude tag JSON → chunk → BGE-M3 嵌入 → upsert pgvector。

過濾串：行政 → 掃描/空 → 無 tag → is_research=false → 已在 DB（除非 --force）。
用法：uv run python scripts/run_ingest.py [--force]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text as sql_text  # noqa: E402

from app.services.chunk import chunk_text  # noqa: E402
from app.services.db import SessionFactory, relax_statement_timeout  # noqa: E402
from app.services.embed import embed_texts  # noqa: E402
from app.services.store import ReportRow, report_exists, upsert_report  # noqa: E402
from app.services.tagging import load_tag  # noqa: E402
from app.services.textnorm import clean_extracted  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
EXTRACTED = ROOT / "data" / "extracted" / "sample.jsonl"
TAGS_DIR = ROOT / "data" / "tags"


def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


async def main(force: bool) -> None:
    records = [
        json.loads(line)
        for line in EXTRACTED.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    stats = {
        "ingested": 0,
        "skip_admin": 0,
        "skip_scanned": 0,
        "skip_untagged": 0,
        "skip_non_research": 0,
        "skip_exists": 0,
        "total_chunks": 0,
    }

    async with SessionFactory() as session:
        for rec in records:
            name = rec["file_name"]
            if rec["is_admin"]:
                stats["skip_admin"] += 1
                continue
            if rec["scanned"]:
                stats["skip_scanned"] += 1
                continue

            tag = load_tag(TAGS_DIR, rec["file_hash"])
            if tag is None:
                stats["skip_untagged"] += 1
                print(f"  skip(untagged) {name[:45]}")
                continue
            if not tag.is_research or not tag.market:
                stats["skip_non_research"] += 1
                continue
            if not force and await report_exists(session, rec["file_hash"]):
                stats["skip_exists"] += 1
                continue

            chunks = chunk_text(clean_extracted(rec["text"]))
            if not chunks:
                stats["skip_scanned"] += 1
                continue
            embeddings = embed_texts(chunks, batch_size=8)

            report = ReportRow(
                file_hash=rec["file_hash"],
                file_name=name,
                file_path=rec["file_path"],
                market=tag.market,
                is_research=tag.is_research,
                confidence=tag.confidence,
                stock_code=rec.get("stock_code"),
                company_name=rec.get("company_name"),
                source=rec.get("source"),
                report_date=_parse_date(rec.get("report_date")),
                report_type=rec.get("report_type"),
                language=rec.get("language"),
                instrument_types=tag.instrument_types,
                relates_stock=tag.relates_stock,
                relates_futures=tag.relates_futures,
                stock_targets=tag.stock_targets,
                futures_targets=tag.futures_targets,
                full_text=rec.get("text"),
            )
            await upsert_report(session, report, chunks, embeddings)
            stats["ingested"] += 1
            stats["total_chunks"] += len(chunks)
            print(f"  [{tag.market}] {name[:45]} ({len(chunks)} chunks)")

        if stats["ingested"]:
            # 批量導入後刷新統計，讓 planner 掌握新資料分佈。
            # ANALYZE 可能久於引擎層的 statement_timeout，且是最後一步——被砍掉
            # 只會靜默留下過期統計。先就本交易放寬上界（見 db.relax_statement_timeout）。
            await relax_statement_timeout(session)
            await session.execute(sql_text("ANALYZE research.report_chunk"))
            # research_report 也一起刷（毫秒級）。autoanalyze 是開著的，缺這句不會讓統計
            # 長期失真；會失真的是「剛大批 ingest 完就立刻查詢」那個短窗。
            await session.execute(sql_text("ANALYZE research.research_report"))
            await session.commit()

    print("\n=== ingest summary ===")
    for k, v in stats.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="重新處理已入庫檔")
    args = ap.parse_args()
    asyncio.run(main(args.force))
