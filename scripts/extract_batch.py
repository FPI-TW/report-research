"""對抽樣檔抽全文 + 檔名 metadata → data/extracted/sample.jsonl

每行一筆：file_hash, file_name, file_path, text, char_count, scanned,
language, is_admin, stock_code, company_name, source, report_date, report_type
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.extract import extract_text  # noqa: E402
from app.services.filename import parse_filename  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "data" / "sample_manifest.json"
OUT = ROOT / "data" / "extracted" / "sample.jsonl"


def main() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    OUT.parent.mkdir(parents=True, exist_ok=True)

    n_scanned = n_admin = 0
    with open(OUT, "w", encoding="utf-8") as f:
        for i, item in enumerate(manifest, 1):
            path = Path(item["file_path"])
            meta = parse_filename(path.name)
            res = extract_text(path)
            record = {
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
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            n_scanned += res.scanned
            n_admin += meta.is_admin
            flag = "SCANNED" if res.scanned else f"{res.char_count}c"
            print(f"[{i}/{len(manifest)}] {flag:>10} {path.name[:50]}")

    print(f"\nextracted {len(manifest)} → {OUT}")
    print(f"scanned/empty: {n_scanned} | admin(by filename): {n_admin}")


if __name__ == "__main__":
    main()
