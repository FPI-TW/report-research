"""增量同步匯入：吃 rsync delta 檔清單 → 逐檔 extract→tag→ingest。

僅處理「本次新傳入」的檔（或 --all-local 全本地對 DB 補漏），以 file_hash
對 DB 去重；單檔失敗不中斷，記 data/sync_failures.log。
重型相依（embed/store/db…）延遲到 main() 內 import，讓純函式可被輕量測試。

用法：
  uv run python scripts/sync_new_reports.py --delta data/sync_delta.txt
  uv run python scripts/sync_new_reports.py --all-local
  uv run python scripts/sync_new_reports.py --delta data/sync_delta.txt --dry-run
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from datetime import date
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SRC_LOCAL = ROOT / "研報自動匯入"
TAGS_DIR = ROOT / "data" / "tags"
ALL_JSONL = ROOT / "data" / "extracted" / "all.jsonl"
FAIL_LOG = ROOT / "data" / "sync_failures.log"
INGESTED_HASHES_FILE = ROOT / "data" / ".sync_last_hashes"
EXTS = {".pdf", ".docx", ".doc"}


def parse_rsync_delta(
    lines: Iterable[str], dst_root: Path, exts: set[str] = EXTS
) -> list[Path]:
    """rsync --out-format='%n' 輸出 → 本地絕對路徑清單。

    略過空行與目錄列（'/' 結尾）；只留副檔名在 exts 內者；去重保序。
    """
    out: list[Path] = []
    seen: set[str] = set()
    for raw in lines:
        name = raw.strip()
        if not name or name.endswith("/"):
            continue
        if Path(name).suffix.lower() not in exts:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(dst_root / name)
    return out


def skip_before_tag(is_admin: bool, scanned: bool, exists: bool) -> str | None:
    """標註前的便宜過濾：行政檔／掃描空檔／已入庫 → skip 原因；否則 None。"""
    if is_admin:
        return "skip_admin"
    if scanned:
        return "skip_scanned"
    if exists:
        return "skip_exists"
    return None


def skip_after_tag(tag) -> str | None:
    """標註後過濾：無 tag → skip_untagged；非研究/無市場 → skip_non_research；否則 None。"""
    if tag is None:
        return "skip_untagged"
    if not tag.is_research or not tag.market:
        return "skip_non_research"
    return None


def _tag_via_cli(
    file_name: str,
    text: str,
    excerpt: int = 10000,
    model: str = "claude-haiku-4-5",
    timeout: int = 150,
):
    """用 claude CLI(Haiku)標註單篇；沿用 tag_all_cli 慣例（剝 NUL、cwd=/tmp）。"""
    import subprocess

    from app.services.tagging import TAG_INSTRUCTION, parse_tags

    body = (text or "")[:excerpt]
    prompt = (
        f"{TAG_INSTRUCTION}\n\n檔名：{file_name}\n"
        f"報告內文（前 {excerpt} 字摘錄）：\n{body}\n\n"
        f"請依上述規則只輸出單一 JSON 物件。"
    ).replace("\x00", "")
    try:
        r = subprocess.run(
            ["claude", "-p", prompt, "--model", model],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd="/tmp",
        )
    except Exception:
        return None
    return parse_tags(r.stdout) if r.returncode == 0 else None


def _persist_tag(file_hash: str, tag) -> None:
    """tag → data/tags/<hash>.json（與 tag_all_cli 同格式，原子寫入）。"""
    import json

    TAGS_DIR.mkdir(parents=True, exist_ok=True)
    obj = {
        "market": tag.market,
        "is_research": tag.is_research,
        "confidence": tag.confidence,
        "instrument_types": tag.instrument_types or [],
        "relates_stock": bool(tag.relates_stock),
        "relates_futures": bool(tag.relates_futures),
        "stock_targets": tag.stock_targets or [],
        "futures_targets": tag.futures_targets or [],
    }
    out = TAGS_DIR / f"{file_hash}.json"
    tmp = out.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    tmp.rename(out)


def _append_all_jsonl(rec: dict) -> None:
    """把成功匯入的紀錄 append 進 all.jsonl，維持與批次工具一致。"""
    import json

    ALL_JSONL.parent.mkdir(parents=True, exist_ok=True)
    with open(ALL_JSONL, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def write_ingested_hashes(path: Path, hashes: list[str]) -> None:
    """把本輪成功入庫的 file_hash 清單原子寫入標記檔（每行一個，每輪覆寫）。

    供殼層 gate 摘要步驟，並讓摘要只針對本輪新研報、不掃歷史 NULL 積壓。
    空清單寫成 0-byte 檔，殼層 `[ -s file ]` 會視為「無新研報」而跳過。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f"{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("\n".join(hashes))
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def _iter_targets(args) -> list[Path]:
    """依參數取得待處理檔清單：--all-local 掃整個本地夾；否則解析 delta 檔。"""
    if args.all_local:
        return sorted(p for p in SRC_LOCAL.rglob("*") if p.suffix.lower() in EXTS)
    if not args.delta:
        return []
    lines = Path(args.delta).read_text(encoding="utf-8").splitlines()
    return parse_rsync_delta(lines, SRC_LOCAL)


def fallback_report_date_from_mtime(
    report_date: date | None,
    path: Path,
    *,
    created_at: date | None,
) -> date | None:
    """report_date 缺值時以 mtime 補；live sync 無可靠 copy-time 參照時直接信任 mtime。"""
    if report_date is not None:
        return report_date
    try:
        mtime = date.fromtimestamp(path.stat().st_mtime)
    except OSError:
        return None

    from app.services.filename import mtime_report_date

    return mtime_report_date(mtime, created_at)


async def _run(args) -> None:
    import time

    from sqlalchemy import text as sql_text

    from app.services.chunk import chunk_text
    from app.services.db import SessionFactory
    from app.services.embed import embed_texts

    from app.services.extract import extract_text
    from app.services.filename import parse_filename
    from app.services.store import ReportRow, report_exists, upsert_report
    from app.services.tagging import load_tag
    from app.services.textnorm import clean_extracted

    targets = _iter_targets(args)
    if args.limit:
        targets = targets[: args.limit]
    print(f"待處理檔：{len(targets)}（dry_run={args.dry_run}）", flush=True)

    stats = {
        k: 0
        for k in (
            "ingested",
            "chunks",
            "skip_admin",
            "skip_scanned",
            "skip_exists",
            "skip_untagged",
            "skip_non_research",
            "fail",
        )
    }
    ingested_hashes: list[str] = []
    t0 = time.time()

    async with SessionFactory() as session:
        for path in targets:
            if not path.exists():
                continue
            try:
                res = extract_text(path)
            except Exception as e:  # noqa: BLE001 — 長跑不因單檔中斷
                stats["fail"] += 1
                with open(FAIL_LOG, "a", encoding="utf-8") as fl:
                    fl.write(f"{path}\textract\t{e!r}\n")
                continue

            meta = parse_filename(path.name)
            # live sync 沒有可信的「複製發生時間」參照；若 rsync 已保留 NAS 原始 mtime，
            # 這裡應直接信任 mtime，避免今天/近兩天的新報告再度被留成 NULL。
            report_date = fallback_report_date_from_mtime(
                meta.report_date, path, created_at=None
            )
            exists = await report_exists(session, res.file_hash)
            reason = skip_before_tag(meta.is_admin, res.scanned, exists)
            if reason:
                stats[reason] += 1
                continue

            if args.dry_run:
                stats["ingested"] += 1
                print(f"  [DRY] would ingest: {path.name[:60]}", flush=True)
                continue

            tag = load_tag(TAGS_DIR, res.file_hash) or _tag_via_cli(path.name, res.text)
            if tag is not None:
                _persist_tag(res.file_hash, tag)
            reason = skip_after_tag(tag)
            if reason:
                stats[reason] += 1
                continue

            try:
                raw_text = (res.text or "").replace("\x00", "")
                chunks = chunk_text(clean_extracted(raw_text))
                if not chunks:
                    stats["skip_scanned"] += 1
                    continue
                embeddings = embed_texts(chunks, batch_size=args.batch_size)
                report = ReportRow(
                    file_hash=res.file_hash,
                    file_name=path.name,
                    file_path=str(path),
                    market=tag.market,
                    is_research=tag.is_research,
                    confidence=tag.confidence,
                    stock_code=meta.stock_code,
                    company_name=meta.company_name,
                    source=meta.source,
                    report_date=report_date,
                    report_type=meta.report_type,
                    language=res.language,
                    instrument_types=tag.instrument_types,
                    relates_stock=tag.relates_stock,
                    relates_futures=tag.relates_futures,
                    stock_targets=tag.stock_targets,
                    futures_targets=tag.futures_targets,
                    full_text=raw_text,
                )
                await upsert_report(session, report, chunks, embeddings)
                _append_all_jsonl(
                    {
                        "file_hash": res.file_hash,
                        "file_name": path.name,
                        "file_path": str(path),
                        "text": res.text,
                        "char_count": res.char_count,
                        "scanned": res.scanned,
                        "language": res.language,
                        "is_admin": meta.is_admin,
                        "stock_code": meta.stock_code,
                        "company_name": meta.company_name,
                        "source": meta.source,
                        "report_date": (
                            report_date.isoformat() if report_date else None
                        ),
                        "report_type": meta.report_type,
                    }
                )
            except Exception as e:  # noqa: BLE001
                stats["fail"] += 1
                await session.rollback()
                with open(FAIL_LOG, "a", encoding="utf-8") as fl:
                    fl.write(f"{res.file_hash}\t{path.name}\t{e!r}\n")
                continue

            stats["ingested"] += 1
            stats["chunks"] += len(chunks)
            ingested_hashes.append(res.file_hash)
            print(f"  [{tag.market}] {path.name[:55]} ({len(chunks)} chunks)", flush=True)

        if stats["ingested"] and not args.dry_run:
            await session.execute(sql_text("ANALYZE research.report_chunk"))
            await session.commit()

    if not args.dry_run:
        write_ingested_hashes(INGESTED_HASHES_FILE, ingested_hashes)

    print("\n=== sync summary ===", flush=True)
    for k, v in stats.items():
        print(f"  {k}: {v}", flush=True)
    print(f"  elapsed: {time.time() - t0:.0f}s", flush=True)


def main() -> None:
    import asyncio

    ap = argparse.ArgumentParser()
    ap.add_argument("--delta", help="rsync 傳輸清單檔（本次新傳）")
    ap.add_argument(
        "--all-local",
        action="store_true",
        help="改掃整個本地 研報自動匯入/ 對 DB 補漏",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="只印將匯入清單，不呼叫 claude、不寫 DB",
    )
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--batch-size", type=int, default=32)
    args = ap.parse_args()
    if not args.delta and not args.all_local:
        ap.error("需指定 --delta <file> 或 --all-local")
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
