"""分層抽樣 ~80 檔做原型驗證 → data/sample_manifest.json

6 桶：admin / 英文外資 / 週期報告 / docx / 個股 / 其他，各有配額。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.filename import FOREIGN_BROKERS, parse_filename  # noqa: E402

SOURCE_DIR = Path(__file__).resolve().parents[1] / "研報自動匯入"
OUT = Path(__file__).resolve().parents[1] / "data" / "sample_manifest.json"

QUOTAS = {
    "admin": 6,
    "en": 18,
    "periodic": 12,
    "docx": 6,
    "single_stock": 28,
    "other": 10,
}

PERIODIC_KW = ["雙週報", "週報", "月報", "策略"]
EN_NAME = re.compile(r"[A-Za-z]")


def bucket_of(path: Path) -> str:
    name = path.name
    meta = parse_filename(name)
    if meta.is_admin:
        return "admin"
    if path.suffix.lower() in (".docx", ".doc"):
        return "docx"
    if meta.source in FOREIGN_BROKERS or "webcast" in name.lower() or "flyer" in name.lower():
        return "en"
    if any(kw in name for kw in PERIODIC_KW):
        return "periodic"
    if meta.stock_code:
        return "single_stock"
    return "other"


def main() -> None:
    files = [p for p in SOURCE_DIR.iterdir() if p.is_file()]
    files.sort(key=lambda p: p.name)

    buckets: dict[str, list[Path]] = {k: [] for k in QUOTAS}
    for p in files:
        buckets[bucket_of(p)].append(p)

    selected: list[Path] = []
    seen: set[str] = set()
    for key, quota in QUOTAS.items():
        pool = buckets[key]
        if not pool:
            continue
        step = max(1, len(pool) // quota)  # 均勻抽樣避免偏倚
        picked = pool[::step][:quota]
        for p in picked:
            if p.name not in seen:
                selected.append(p)
                seen.add(p.name)

    manifest = [{"file_name": p.name, "file_path": str(p)} for p in selected]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    counts = {k: len(buckets[k]) for k in QUOTAS}
    print(f"corpus total: {len(files)}")
    print(f"bucket sizes: {counts}")
    print(f"selected: {len(manifest)} → {OUT}")


if __name__ == "__main__":
    main()
