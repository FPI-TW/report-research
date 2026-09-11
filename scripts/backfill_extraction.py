"""既有語料的抽取回填（E1d）：把 `extraction_version` 不是目標版本的研報，用目標抽取器重抽、
重切、重嵌，**原地**更新（report_id 不變），並把既有摘錄的錨點對新正典文字重算。

E1 共識（docs/EXTRACTION_REDESIGN.md §9、2026-09-02 與使用者逐題確認）：
- 深夜分批、不停服：`report-mark-backfill.timer` 01:00 起跑，`--max-minutes 240` 到點收手。
- 依 `report_date` 由新到舊（問答最常引用的先修好），不限篇數、限時間（長報告的 chunk
  數差十倍，篇數限不住時間）。
- 每篇一個交易：被殺掉最多損失當時那一篇，下一晚重做它。
- 摘錄不重跑 LLM：`store.reanchor_takeaways` 重算 `quote_start`／`quote_end`／`text_sha256`。
- 新抽取器抽不出字（拋例外／低於 MIN_TEXT_CHARS 且 pypdf 也不行）：**保留舊全文與 chunk**，
  只更新版本與旗標（`needs_review=true`），讓它不再被排進回填。
- 零 LLM，不取 claude 鎖；吃的是 BGE-M3 的 CPU。

「還有沒有要回填的」由 `extraction_version` 決定，不靠狀態檔；全部跑完就是 no-op、exit 0。

用法：
    uv run python scripts/backfill_extraction.py --dry-run             # 只印會回填哪些
    uv run python scripts/backfill_extraction.py --limit 5             # 手動試 5 篇
    uv run python scripts/backfill_extraction.py --max-minutes 240     # timer 用

退出碼：0 完成或 no-op／1 有單篇失敗（已記 data/backfill_failures.log）／2 DB 或模型不可用。
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import sys
import time
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text as sql_text  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.services.chunk import chunk_text  # noqa: E402
from app.services.db import SessionFactory  # noqa: E402
from app.services.extract import extract_text, file_sha256  # noqa: E402
from app.services.extraction import EXTRACTION_VERSION, cache  # noqa: E402
from app.services.filename import parse_filename  # noqa: E402
from app.services.object_storage import (  # noqa: E402
    ObjectNotFound,
    get_object_storage,
    original_object_key,
    verified_file_snapshot,
)
from app.services.store import (  # noqa: E402
    ExtractionLogRow,
    mark_report_extraction,
    needs_review,
    reanchor_takeaways,
    replace_report_extraction,
    upsert_extraction_log,
)
from app.services.textnorm import clean_extracted  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "研報自動匯入"
FAIL_LOG = ROOT / "data" / "backfill_failures.log"

# 候選：版本不是目標者，新的先。report_date 為 NULL 者排最後（NULLS LAST），不是排除——
# 它們也要回填，只是優先序最低。
CANDIDATES_SQL = """
SELECT r.id::text, r.file_hash, r.file_name, r.file_path, r.source_object_key, r.report_date
FROM research.research_report r
WHERE r.extraction_version IS DISTINCT FROM :target
ORDER BY r.report_date DESC NULLS LAST, r.id
LIMIT :limit
"""
COUNT_SQL = "SELECT count(*) FROM research.research_report r WHERE r.extraction_version IS DISTINCT FROM :target"


def resolve_path(file_path: str, file_name: str) -> Path | None:
    """DB 存的 file_path 可能是舊掛載點（實測有 /mnt/c/... 的歷史值）；找不到就退回鏡像目錄。"""
    p = Path(file_path)
    if p.exists():
        return p
    alt = SRC / file_name
    if alt.exists():
        return alt
    return None


class Budget:
    def __init__(self, max_minutes: float | None):
        self.deadline = (time.monotonic() + max_minutes * 60) if max_minutes else None

    def exhausted(self) -> bool:
        return self.deadline is not None and time.monotonic() >= self.deadline


def _log_failure(file_name: str, stage: str, reason: str) -> None:
    FAIL_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(FAIL_LOG, "a", encoding="utf-8") as fl:
        fl.write(f"{file_name}\t{stage}\t{reason}\n")


async def backfill_one(session, row, extractor: str, target: str, review_min: float, batch_size: int) -> str:
    """回傳結果類別：replaced／kept_previous／missing_file。例外往外拋，由呼叫端記錄。"""
    rid, file_hash, file_name, file_path, source_object_key, _report_date = row
    storage = get_object_storage()
    path: Path | None = None
    temp_dir: TemporaryDirectory | None = None
    # hybrid deliberately probes R2 first; only a confirmed missing key may fall back to local.
    # r2 mode never resolves ``file_path`` or the mirror directory.
    if storage.enabled and source_object_key:
        # Parser APIs accept only real paths.  Never treat an object key as one: materialize
        # into an owned TemporaryDirectory and remove it after this one report.
        temp_dir = TemporaryDirectory(prefix="report-mark-backfill-")
        path = Path(temp_dir.name) / file_name
        try:
            data = await asyncio.to_thread(storage.download_bytes, source_object_key)
            await asyncio.to_thread(path.write_bytes, data)
            if hashlib.sha256(data).hexdigest() != file_hash:
                raise ValueError("R2 source content SHA-256 does not match DB file_hash")
        except ObjectNotFound:
            temp_dir.cleanup()
            temp_dir = None
            path = None
            if storage.mode == "r2":
                return "missing_file"
        except Exception:
            temp_dir.cleanup()
            raise

    if path is None:
        if storage.mode == "r2":
            return "missing_file"
        path = resolve_path(file_path, file_name)
        if path is None:
            return "missing_file"

    try:
        # NAS/local paths are mutable.  Copy once before either upload or parsing so a
        # replacement cannot make R2 bytes and extracted text disagree.  R2 downloads are
        # already owned temp files, but taking the same snapshot keeps one invariant for both.
        with verified_file_snapshot(path, file_hash) as (snapshot, _digest):
            return await _backfill_path(
                session, rid, file_hash, file_name, snapshot, extractor, target, review_min, batch_size,
                expected_source_object_key=source_object_key,
            )
    finally:
        # The parser, embedder, DB update, cache write, and even logging may throw.  This
        # ownership boundary is deliberately outside all of them so R2 bytes never leak.
        if temp_dir:
            temp_dir.cleanup()


async def _backfill_path(
    session, rid, file_hash: str, file_name: str, path: Path, extractor: str,
    target: str, review_min: float, batch_size: int, *, expected_source_object_key: str | None = None,
) -> str:
    """Backfill one concrete local path; caller owns any temporary directory."""

    storage = get_object_storage()
    if storage.enabled:
        # A successful upload precedes the transaction that records its key.  If that
        # transaction later rolls back, the object is an explicitly reconcilable orphan.
        if file_sha256(path) != file_hash:
            raise ValueError("source content SHA-256 does not match DB file_hash; refusing R2 upload")
        source_object_key = original_object_key(file_hash, file_name)
        await asyncio.to_thread(storage.upload_file, path, source_object_key, expected_sha256=file_hash)
        result = await session.execute(
            sql_text(
                "UPDATE research.research_report SET source_object_key = :key "
                "WHERE id = :id AND source_object_key IS NOT DISTINCT FROM :expected_old"
            ),
            {"id": rid, "key": source_object_key, "expected_old": expected_source_object_key},
        )
        if getattr(result, "rowcount", 1) == 0:
            raise RuntimeError("source_object_key update lost race; uploaded object left for reconciliation")

    res = extract_text(path, extractor=extractor)
    meta = parse_filename(path.name)
    q = dict(res.quality or {})

    if res.error or res.scanned:
        q.update({"backfill": "kept_previous_text", "backfill_reason": res.error or "below_min_chars"})
        await mark_report_extraction(
            session, rid, {"extractor": res.extractor, "extraction_version": target, "quality_flags": q}
        )
        await upsert_extraction_log(
            session,
            ExtractionLogRow(
                file_hash, path.name, res.extractor, target, "ingested", page_count=res.page_count,
                pages_failed=list(res.pages_failed) or None, char_count=res.char_count,
                quality_score=q.get("quality_score"), quality_flags=q,
            ),
        )
        await session.commit()
        return "kept_previous"

    raw = res.text.replace("\x00", "")
    canonical = clean_extracted(raw)
    chunks = chunk_text(canonical)
    if not chunks:
        q.update({"backfill": "kept_previous_text", "backfill_reason": "no_chunks"})
        await mark_report_extraction(
            session, rid, {"extractor": res.extractor, "extraction_version": target, "quality_flags": q}
        )
        await session.commit()
        return "kept_previous"

    from app.services.embed import embed_texts

    embeddings = embed_texts(chunks, batch_size=batch_size)
    fields = {
        "extractor": res.extractor,
        "extraction_version": res.extraction_version,
        "quality_score": q.get("quality_score"),
        "quality_flags": q,
        "page_count": res.page_count,
        "pages_failed": list(res.pages_failed),
        "needs_review": needs_review(q.get("quality_score"), res.pages_failed, review_min),
    }
    await replace_report_extraction(
        session, rid, full_text=raw, language=res.language, chunks=chunks, embeddings=embeddings, fields=fields
    )
    n_tk, n_anchored = await reanchor_takeaways(session, rid, canonical)
    await upsert_extraction_log(
        session,
        ExtractionLogRow(
            file_hash, path.name, res.extractor, res.extraction_version, "ingested", page_count=res.page_count,
            pages_failed=list(res.pages_failed) or None, char_count=res.char_count,
            quality_score=q.get("quality_score"), quality_flags=q,
        ),
    )
    await session.commit()
    # 快取與 DB 同步：之後的 tag／ingest／takeaways 讀到的就是這一版。
    rec = cache.record_from_result(res, path, meta, meta.source)
    cache.write_record(rec)
    print(
        f"  [{res.extractor}] {path.name[:52]} chunks={len(chunks)} takeaways={n_anchored}/{n_tk}"
        + (f" pages_failed={list(res.pages_failed)}" if res.pages_failed else ""),
        flush=True,
    )
    return "replaced"


async def run(args) -> int:
    settings = get_settings()
    extractor = args.extractor  # 回填只支援版面層：pypdf 沒有可追溯的版本，沒有「回填到 pypdf」這回事
    target = EXTRACTION_VERSION
    budget = Budget(args.max_minutes)
    stats = {"replaced": 0, "kept_previous": 0, "missing_file": 0, "failed": 0}
    t0 = time.time()
    try:
        async with SessionFactory() as session:
            total = (await session.execute(sql_text(COUNT_SQL), {"target": target})).scalar() or 0
            rows = (
                await session.execute(sql_text(CANDIDATES_SQL), {"target": target, "limit": args.limit or 10**9})
            ).all()
    except Exception as exc:  # noqa: BLE001
        print(f"DB 不可用：{exc!r}", file=sys.stderr)
        return 2
    print(f"=== backfill_extraction → {target}  待回填 {total} 篇，本輪最多 {len(rows)} 篇"
          f"{'，dry-run' if args.dry_run else ''}{f'，上限 {args.max_minutes} 分鐘' if args.max_minutes else ''} ===",
          flush=True)
    if not rows:
        print("沒有要回填的研報（全部已是目標版本）。", flush=True)
        return 0
    if args.dry_run:
        for _rid, _h, name, _p, _key, rd in rows[:50]:
            print(f"  {rd} {name[:60]}")
        if len(rows) > 50:
            print(f"  … 共 {len(rows)} 篇")
        return 0

    async with SessionFactory() as session:
        for row in rows:
            if budget.exhausted():
                print(f"時間到（{args.max_minutes} 分鐘），本輪收手；剩下的下一輪再做。", flush=True)
                break
            try:
                kind = await backfill_one(
                    session, row, extractor, target, settings.extraction_review_min, args.batch_size
                )
                stats[kind] += 1
                if kind == "missing_file":
                    _log_failure(row[2], "locate", "檔案不在 file_path 也不在鏡像目錄")
            except Exception as exc:  # noqa: BLE001 — 單篇失敗不中斷，下一晚重做
                stats["failed"] += 1
                await session.rollback()
                _log_failure(row[2], "backfill", repr(exc))
                print(f"  FAIL {row[2][:60]}: {exc!r}", flush=True)

    elapsed = time.time() - t0
    remaining = max(0, total - stats["replaced"] - stats["kept_previous"])
    print(f"=== 本輪：換文 {stats['replaced']}｜保留舊文 {stats['kept_previous']}｜找不到檔 {stats['missing_file']}"
          f"｜失敗 {stats['failed']}｜{elapsed / 60:.1f} 分鐘｜估計尚餘 {remaining} 篇 ===", flush=True)
    return 1 if stats["failed"] else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--extractor", choices=("pdfplumber",), default="pdfplumber",
                    help="回填只支援 pdfplumber 版面層（刻意不讀環境檔的 EXTRACTOR）")
    ap.add_argument("--max-minutes", type=float, default=None, help="到點收手（timer 用 240）")
    ap.add_argument("--limit", type=int, default=None, help="本輪最多幾篇")
    ap.add_argument("--batch-size", type=int, default=8, help="BGE-M3 批次大小")
    ap.add_argument("--dry-run", action="store_true", help="只印候選，不寫任何東西")
    args = ap.parse_args()
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] backfill_extraction 開始", flush=True)
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
