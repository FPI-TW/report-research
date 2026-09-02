"""語料 profiling（唯讀）：新抽取器眼中的語料長什麼樣。

對應 docs/EXTRACTION_REDESIGN.md §9 的第 1 步。**不寫任何生產路徑**：不碰
`app/services/extract.py`、不碰 DB（只讀）、不碰 `data/extracted/`。

用法：
    uv run python scripts/profile_corpus.py --sample-per-source 40    # 抽樣
    uv run python scripts/profile_corpus.py --workers 16              # 全語料
    uv run python scripts/profile_corpus.py --no-render               # 省掉 layout_coverage

## 三個刻意的設計

1. **檔案清單以本地鏡像為權威，不是 DB 的 `file_path`。** 實測 15,089 列裡
   有 14,986 列的 `file_path` 指向 `/mnt/c/...`——那是 repo 還在 Windows 側
   時留下的舊路徑，指向的副本已經比鏡像少 136 檔，而且落在 WSL 9p（2026-08-18
   那次 4h50m 中斷的根因檔案系統）。DB 只用來**左連**補 metadata。
2. **輸出 JSONL 且可續傳。** 全語料一輪 20 分鐘級，中途被打斷不該從頭來。
   續傳鍵是 `file_hash`，不是路徑——同內容不同檔名的重複檔（實測 122 組）
   本來就該只算一次。
3. **`in_db` 是一等欄位。** 鏡像有 16,850 檔而 `research_report` 只有 15,089
   列，中間蒸發的 1,466 筆正是診斷 #4b 在講的事。這支腳本讓那個差額**逐檔
   可查**，而不是只有一個總數。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.extraction import EXTRACTION_VERSION, EXTRACTOR_NAME  # noqa: E402
from app.services.extraction.layout import extract_document  # noqa: E402
from app.services.extraction.quality import measure  # noqa: E402
from app.services.filename import parse_filename  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "研報自動匯入"
OUT_DIR = ROOT / "data" / "extraction"
EXTS = {".pdf", ".docx", ".doc"}

# pdfminer 對缺 FontBBox 的字型會對每一頁噴 warning。那是研報 PDF 的常態，
# 不是我們要看的訊號；讓它淹掉真正的警告比沒有警告更糟。
logging.getLogger("pdfminer").setLevel(logging.ERROR)
logging.getLogger("pdfplumber").setLevel(logging.ERROR)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _work(args: tuple[str, bool]) -> dict:
    path_str, render = args
    path = Path(path_str)
    rec: dict = {"file_name": path.name, "file_path": path_str}
    try:
        rec["file_hash"] = _sha256(path)
        meta = parse_filename(path.name)
        rec["source"] = meta.source
        rec["report_date"] = meta.report_date.isoformat() if meta.report_date else None
        rec["is_admin"] = meta.is_admin
    except Exception as exc:
        rec["error"] = f"meta: {exc}"[:200]
        return rec

    if path.suffix.lower() != ".pdf":
        # docx 走 python-docx，沒有版面座標；profiling 對它只有字數有意義。
        rec["skipped"] = "not_pdf"
        return rec

    try:
        doc = extract_document(path)
        if doc.error:
            rec["error"] = f"open: {doc.error}"[:200]
            return rec
        renderer = None
        if render:
            import pypdfium2 as pdfium

            renderer = pdfium.PdfDocument(str(path))
        q = measure(doc, renderer)
        rec.update(q.as_flags())
        rec["block_types"] = dict(Counter(b.type for b in doc.blocks()))
        rec["extractor"] = EXTRACTOR_NAME
        rec["extraction_version"] = EXTRACTION_VERSION
    except Exception as exc:  # 單檔壞掉不影響整批
        rec["error"] = f"profile: {type(exc).__name__}: {exc}"[:200]
    return rec


def _db_meta() -> dict[str, dict]:
    """file_hash → DB metadata。DB 不可用時回空 dict（profiling 照跑）。"""
    try:
        import asyncio

        from sqlalchemy import text

        from app.services.db import SessionFactory

        async def go() -> dict[str, dict]:
            async with SessionFactory() as s:
                rows = (
                    await s.execute(
                        text(
                            "SELECT file_hash, source, market, is_research, "
                            "length(full_text) AS pypdf_chars FROM research.research_report"
                        )
                    )
                ).all()
            return {
                r.file_hash: {
                    "db_source": r.source,
                    "db_market": r.market,
                    "db_is_research": r.is_research,
                    "pypdf_chars": r.pypdf_chars,
                }
                for r in rows
            }

        return asyncio.run(go())
    except Exception as exc:
        print(f"[warn] DB 不可用，in_db 欄位會全部是 false：{exc}", file=sys.stderr)
        return {}


def _pick(files: list[Path], per_source: int | None, limit: int | None) -> list[Path]:
    """分層抽樣。`parse_filename` 不做 I/O，所以可以在昂貴工作之前先分層。"""
    if per_source:
        by: dict[str, list[Path]] = defaultdict(list)
        for p in files:
            try:
                src = parse_filename(p.name).source or "unknown"
            except Exception:
                src = "unknown"
            by[src].append(p)
        files = [p for src in sorted(by) for p in sorted(by[src])[:per_source]]
    return files[:limit] if limit else files


def main() -> int:
    ap = argparse.ArgumentParser(description="語料 profiling（唯讀）")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 4) - 4))
    ap.add_argument("--sample-per-source", type=int, default=None, help="每券商最多幾份")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--no-render", action="store_true", help="不算 layout_coverage（省 render）")
    ap.add_argument("--out", default=str(OUT_DIR / "profile.jsonl"))
    ap.add_argument("--fresh", action="store_true", help="忽略既有輸出，從頭跑")
    args = ap.parse_args()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    files = _pick(
        sorted(p for p in SRC.rglob("*") if p.suffix.lower() in EXTS),
        args.sample_per_source,
        args.limit,
    )

    done: set[str] = set()
    if out.exists() and not args.fresh:
        for line in out.open(encoding="utf-8"):
            try:
                done.add(json.loads(line)["file_hash"])
            except Exception:
                continue
        print(f"續傳：既有 {len(done)} 筆")

    mode = "w" if (args.fresh or not out.exists()) else "a"
    render = not args.no_render
    n = 0
    with out.open(mode, encoding="utf-8") as f, ProcessPoolExecutor(args.workers) as ex:
        payload = [(str(p), render) for p in files]
        for rec in ex.map(_work, payload, chunksize=4):
            if rec.get("file_hash") in done:
                continue
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            done.add(rec.get("file_hash", ""))
            n += 1
            if n % 200 == 0:
                print(f"  ...{n}/{len(files)}", flush=True)

    # 左連 DB，寫出彙總
    meta = _db_meta()
    recs = [json.loads(line) for line in out.open(encoding="utf-8")]
    for r in recs:
        h = r.get("file_hash")
        r["in_db"] = bool(h and h in meta)
        if h in meta:
            r.update(meta[h])

    summary = _summarise(recs)
    sp = out.with_name(out.stem + "_summary.json")
    sp.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=== profile_corpus summary ===")
    for k, v in summary.items():
        if not isinstance(v, dict):
            print(f"  {k:26} {v}")
    print(f"  逐檔輸出: {out}")
    print(f"  彙總    : {sp}")
    return 0


def _summarise(recs: list[dict]) -> dict:
    ok = [r for r in recs if "quality_score" in r]
    cols = Counter(r.get("max_columns", 0) for r in ok)
    scores = sorted(r["quality_score"] for r in ok)
    cov = sorted(r["layout_coverage"] for r in ok if r.get("layout_coverage") is not None)

    def q(v: list[float], p: float) -> float | None:
        return round(v[int(len(v) * p)], 4) if v else None

    return {
        "files_seen": len(recs),
        "profiled_ok": len(ok),
        "errors": sum(1 for r in recs if r.get("error")),
        "skipped_not_pdf": sum(1 for r in recs if r.get("skipped") == "not_pdf"),
        "not_in_db": sum(1 for r in recs if not r.get("in_db")),
        "is_admin": sum(1 for r in recs if r.get("is_admin")),
        "pages_failed_files": sum(1 for r in ok if r.get("pages_failed")),
        "multi_column_files": sum(1 for r in ok if r.get("max_columns", 1) > 1),
        "quality_score_p05": q(scores, 0.05),
        "quality_score_p50": q(scores, 0.50),
        "quality_score_p95": q(scores, 0.95),
        "layout_coverage_p05": q(cov, 0.05),
        "layout_coverage_p50": q(cov, 0.50),
        "layout_coverage_p95": q(cov, 0.95),
        "max_columns_dist": dict(sorted(cols.items())),
        "by_source": dict(Counter(r.get("source") or "unknown" for r in recs).most_common()),
    }


if __name__ == "__main__":
    raise SystemExit(main())
