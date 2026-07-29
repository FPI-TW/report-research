"""全量導入：串流 all.jsonl + tags → chunk → BGE-M3 嵌入 → upsert pgvector。

與 run_ingest.py 的差異：
- 讀 all.jsonl 且逐行串流（375MB，不整檔載入記憶體）
- 啟動時一次撈出 DB 既有 file_hash 作續傳集合（不必逐筆查 exists）
- 單筆失敗記錄到 data/ingest_failures.log 後繼續，不中斷長跑
- 每 25 篇輸出速率與 ETA
用法：uv run python scripts/ingest_all.py [--limit N] [--batch-size 32]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text as sql_text  # noqa: E402

from app.services.chunk import chunk_text  # noqa: E402
from app.services.db import SessionFactory, relax_statement_timeout  # noqa: E402
from app.services.embed import embed_texts  # noqa: E402
from app.services.store import ReportRow, upsert_report  # noqa: E402
from app.services.tagging import load_tag  # noqa: E402
from app.services.textnorm import clean_extracted  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
ALL = ROOT / "data" / "extracted" / "all.jsonl"
TAGS_DIR = ROOT / "data" / "tags"
FAIL_LOG = ROOT / "data" / "ingest_failures.log"


def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


async def main(limit: int | None, batch_size: int) -> None:
    async with SessionFactory() as session:
        rows = await session.execute(
            sql_text("SELECT file_hash FROM research.research_report")
        )
        existing = {r[0] for r in rows}
    print(f"DB 既有報告: {len(existing)}", flush=True)

    stats = {
        "ingested": 0,
        "chunks": 0,
        "skip_admin": 0,
        "skip_scanned": 0,
        "skip_untagged": 0,
        "skip_non_research": 0,
        "skip_exists": 0,
        "fail": 0,
    }
    t0 = time.time()

    async with SessionFactory() as session:
        with open(ALL, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("_failed"):
                    continue
                if rec.get("is_admin"):
                    stats["skip_admin"] += 1
                    continue
                if rec.get("scanned"):
                    stats["skip_scanned"] += 1
                    continue
                h = rec["file_hash"]
                if h in existing:
                    stats["skip_exists"] += 1
                    continue
                tag = load_tag(TAGS_DIR, h)
                if tag is None:
                    stats["skip_untagged"] += 1
                    continue
                if not tag.is_research or not tag.market:
                    stats["skip_non_research"] += 1
                    continue

                try:
                    # full_text 走原始文字（不經 clean_extracted），需單獨剝除 NUL，
                    # 否則含 \x00 的 PDF 會在 upsert 時拋 UTF8 編碼錯誤而永久失敗。
                    raw_text = (rec.get("text") or "").replace("\x00", "")
                    chunks = chunk_text(clean_extracted(raw_text))
                    if not chunks:
                        stats["skip_scanned"] += 1
                        continue
                    embeddings = embed_texts(chunks, batch_size=batch_size)
                    report = ReportRow(
                        file_hash=h,
                        file_name=rec["file_name"],
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
                        full_text=raw_text,
                    )
                    await upsert_report(session, report, chunks, embeddings)
                except Exception as e:  # noqa: BLE001 — 長跑不因單筆中斷
                    stats["fail"] += 1
                    await session.rollback()
                    with open(FAIL_LOG, "a", encoding="utf-8") as fl:
                        fl.write(f"{h}\t{rec.get('file_name')}\t{e!r}\n")
                    continue

                existing.add(h)
                stats["ingested"] += 1
                stats["chunks"] += len(chunks)
                if stats["ingested"] % 25 == 0:
                    elapsed = time.time() - t0
                    rate = stats["chunks"] / elapsed if elapsed else 0.0
                    print(
                        f"ingested={stats['ingested']} chunks={stats['chunks']} "
                        f"fail={stats['fail']} rate={rate:.1f}c/s "
                        f"avg={stats['chunks'] / stats['ingested']:.0f}c/篇",
                        flush=True,
                    )
                if limit and stats["ingested"] >= limit:
                    break

        if stats["ingested"]:
            # 批量導入後刷新統計，讓 planner 掌握新資料分佈。
            # ANALYZE 在 70 萬列 × vector(1024) 上抽樣，可能久於引擎層的
            # statement_timeout；而它是整條匯入的最後一步，被砍掉只會靜默留下
            # 過期統計。先就本交易放寬上界（SET LOCAL，commit 後自動還原）。
            await relax_statement_timeout(session)
            await session.execute(sql_text("ANALYZE research.report_chunk"))
            await session.commit()

    print("\n=== ingest_all summary ===", flush=True)
    for k, v in stats.items():
        print(f"  {k}: {v}", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None, help="最多導入 N 篇後停止")
    ap.add_argument("--batch-size", type=int, default=32, help="嵌入批次大小")
    args = ap.parse_args()
    asyncio.run(main(args.limit, args.batch_size))
