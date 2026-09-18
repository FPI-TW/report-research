"""全量導入：串流 per-hash 抽取快取 + tags → chunk → BGE-M3 嵌入 → upsert pgvector。

與 run_ingest.py 的差異：
- 逐檔串流 data/extracted/<hash>.json（不整批載入記憶體；E1c）
- 啟動時一次撈出 DB 既有 file_hash 作續傳集合（不必逐筆查 exists）
- 單筆失敗記錄到 data/ingest_failures.log 後繼續，不中斷長跑
- 每 25 篇輸出速率與 ETA
用法：uv run python scripts/ingest_all.py [--limit N] [--batch-size 32]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text as sql_text  # noqa: E402

from app.services.boilerplate import strip_boilerplate  # noqa: E402
from app.services.chunk import chunk_text  # noqa: E402
from app.services.db import SessionFactory, relax_statement_timeout  # noqa: E402
from app.services.embed import embed_texts  # noqa: E402
from app.services.extract import file_sha256  # noqa: E402
from app.services.object_storage import get_object_storage, original_object_key  # noqa: E402
from app.services.store import (  # noqa: E402
    ExtractionLogRow,
    ReportRow,
    needs_review,
    upsert_extraction_log,
    upsert_report,
)
from app.services.tagging import load_tag  # noqa: E402
from app.services.textnorm import clean_extracted  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
from app.services.extraction import cache  # noqa: E402


def _log_row(rec: dict, stopped_at: str) -> ExtractionLogRow:
    """快取紀錄 → extraction_log 列。舊格式（E1c 之前）沒有抽取版本欄位，誠實記 legacy。"""
    q = rec.get("quality") or {}
    return ExtractionLogRow(
        file_hash=rec["file_hash"],
        file_name=rec["file_name"],
        extractor=rec.get("extractor") or "pypdf",
        extraction_version=rec.get("extraction_version") or "pypdf-legacy",
        stopped_at=stopped_at,
        page_count=rec.get("page_count"),
        pages_failed=rec.get("pages_failed") or None,
        char_count=rec.get("char_count"),
        quality_score=q.get("quality_score"),
        quality_flags=q,
    )
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
    storage = get_object_storage()
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

    from app.config import get_settings

    review_min = get_settings().extraction_review_min
    async with SessionFactory() as session:
        for rec in cache.iter_records():
            if rec.get("_unreadable"):
                stats["fail"] += 1
                continue
            if rec.get("_failed"):
                # 抽取階段就炸掉的紀錄沒有 file_hash，進不了以 hash 為鍵的 extraction_log；
                # 它們留在 all.jsonl 的 _failed 列與 extract_all 的統計裡。
                continue
            # 每一道閘都寫 extraction_log（§4.2 目標 #1：落點不能只存在於 if 分支裡）。
            # 寫入獨立 commit：閘門紀錄不該因為後面的 upsert 失敗而一起回滾。
            if rec.get("is_admin"):
                stats["skip_admin"] += 1
                await upsert_extraction_log(session, _log_row(rec, "skip_admin"))
                await session.commit()
                continue
            if rec.get("scanned"):
                stats["skip_scanned"] += 1
                await upsert_extraction_log(session, _log_row(rec, "scanned"))
                await session.commit()
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
                await upsert_extraction_log(session, _log_row(rec, "not_research"))
                await session.commit()
                continue

            try:
                # full_text 走原始文字（不經 clean_extracted），需單獨剝除 NUL，
                # 否則含 \x00 的 PDF 會在 upsert 時拋 UTF8 編碼錯誤而永久失敗。
                raw_text = (rec.get("text") or "").replace("\x00", "")
                # 樣板段落只從要切塊的文字拿掉，full_text 不動（app/services/boilerplate.py）。
                chunks = chunk_text(strip_boilerplate(clean_extracted(raw_text), rec.get("source"))[0])
                if not chunks:
                    stats["skip_scanned"] += 1
                    await upsert_extraction_log(session, _log_row(rec, "scanned"))
                    await session.commit()
                    continue
                embeddings = embed_texts(chunks, batch_size=batch_size)
                source_object_key = None
                source_path = Path(rec["file_path"])
                if storage.enabled:
                    # Cached extraction is local-only; the original object is uploaded before
                    # the report row commits so r2 mode never publishes a dangling key.
                    if file_sha256(source_path) != h:
                        raise ValueError("source SHA-256 differs from cached extraction record")
                    source_object_key = original_object_key(h, rec["file_name"])
                    await asyncio.to_thread(storage.upload_file, source_path, source_object_key, expected_sha256=h)
                q = rec.get("quality") or {}
                report = ReportRow(
                    file_hash=h,
                    file_name=rec["file_name"],
                    file_path=rec["file_path"],
                    market=tag.market,
                    is_research=tag.is_research,
                    confidence=tag.confidence,
                    source_object_key=source_object_key,
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
                    extractor=rec.get("extractor") or "pypdf",
                    extraction_version=rec.get("extraction_version") or "pypdf-legacy",
                    quality_score=q.get("quality_score"),
                    quality_flags=q or None,
                    page_count=rec.get("page_count"),
                    pages_failed=rec.get("pages_failed") or None,
                    needs_review=needs_review(
                        q.get("quality_score"), rec.get("pages_failed"), review_min, q,
                        min_coverage=get_settings().extraction_review_min_coverage,
                        max_garbled=get_settings().extraction_review_max_garbled,
                    ),
                )
                await upsert_report(session, report, chunks, embeddings)
                await upsert_extraction_log(session, _log_row(rec, "ingested"))
                await session.commit()
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
            # research_report 也一起刷（毫秒級）。autoanalyze 是開著的，所以缺這句不會
            # 讓統計長期失真；會失真的是「剛大批 ingest 完就立刻查詢」那個短窗——
            # autoanalyze 還沒被觸發／跑完，雷達與 overview 就已經在用舊的列數與選擇度估算。
            await session.execute(sql_text("ANALYZE research.research_report"))
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
