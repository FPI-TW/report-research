"""全量平行萃取 研報自動匯入/ → data/extracted/all.jsonl

- 平行(多進程)抽全文 + 檔名 metadata + file_hash
- 依 file_hash 去重(保留首個出現)
- 輸出去重後的唯一檔；並回報 total / unique / admin / scanned / 候選研報數
用法：uv run python scripts/extract_all.py [--workers N]
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.extract import extract_text  # noqa: E402
from app.services.filename import parse_filename  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "研報自動匯入"
OUT = ROOT / "data" / "extracted" / "all.jsonl"
EXTS = {".pdf", ".docx", ".doc"}


def _work(path_str: str) -> dict | None:
    path = Path(path_str)
    try:
        meta = parse_filename(path.name)
        res = extract_text(path)
        return {
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
            "report_date": meta.report_date.isoformat() if meta.report_date else None,
            "report_type": meta.report_type,
        }
    except Exception as e:  # 單檔壞掉不影響整批
        return {"file_path": str(path), "error": str(e)[:120], "_failed": True}


def main(workers: int) -> None:
    files = sorted(p for p in SRC.rglob("*") if p.suffix.lower() in EXTS)
    total = len(files)
    OUT.parent.mkdir(parents=True, exist_ok=True)

    seen: set[str] = set()
    n_written = n_dupe = n_admin = n_scanned = n_failed = 0
    n_candidate = 0  # 唯一 且 非admin 且 非scanned

    with open(OUT, "w", encoding="utf-8") as f, ProcessPoolExecutor(
        max_workers=workers
    ) as ex:
        for i, rec in enumerate(ex.map(_work, [str(p) for p in files], chunksize=16), 1):
            if rec is None or rec.get("_failed"):
                n_failed += 1
            else:
                h = rec["file_hash"]
                if h in seen:
                    n_dupe += 1
                else:
                    seen.add(h)
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    n_written += 1
                    if rec["is_admin"]:
                        n_admin += 1
                    if rec["scanned"]:
                        n_scanned += 1
                    if not rec["is_admin"] and not rec["scanned"]:
                        n_candidate += 1
            if i % 1000 == 0:
                print(f"  ...{i}/{total} (unique={n_written}, dupe={n_dupe})", flush=True)

    print("\n=== extract_all summary ===")
    print(f"  total files      : {total}")
    print(f"  unique (by hash) : {n_written}")
    print(f"  duplicates       : {n_dupe}")
    print(f"  admin(by name)   : {n_admin}")
    print(f"  scanned/empty    : {n_scanned}")
    print(f"  failed           : {n_failed}")
    print(f"  >>> 候選研報(唯一 且 非admin 且 非scanned): {n_candidate}")
    print(f"  output: {OUT}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=16)
    args = ap.parse_args()
    main(args.workers)
