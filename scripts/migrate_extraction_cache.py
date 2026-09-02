"""把 `data/extracted/all.jsonl` 轉成 per-hash 快取 `data/extracted/<hash>.json`（E1c，一次性）。

用法：
    uv run python scripts/migrate_extraction_cache.py            # 唯讀試跑：只印統計，不寫任何檔
    uv run python scripts/migrate_extraction_cache.py --apply    # 真的寫；完成後把 all.jsonl 改名 all.jsonl.bak

規則（E1 共識第 5、9 條）：
- 同一個 `file_hash` 出現多行時**取最後一行**（sync 每 3 小時 append，後寫的比較新）；
  重複筆數會印出來。
- 已存在的 `<hash>.json` **預設不覆寫**（`--force` 才覆寫）：轉檔後 sync 已經寫新格式，
  再跑一次不該用舊資料蓋掉新抽取結果。冪等。
- `_failed` 行（抽取階段就炸、沒有 file_hash）跳過並計數。
- 舊檔**改名不刪**：`all.jsonl` → `all.jsonl.bak`。四個端點都只讀新格式，改名讓任何還在
  讀舊路徑的程式當場炸掉，而不是安靜地讀一個沒人寫的檔。回填跑完、核對
  `extraction_log` 列數等於 unique hash 數之後，由人手動刪。

退出碼：0 完成／1 有無法解析的行（仍會轉其餘）／2 找不到 all.jsonl。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.extraction import cache  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SRC = ROOT / "data" / "extracted" / "all.jsonl"


def migrate(src: Path, cache_dir: Path, apply: bool, force: bool) -> dict[str, int]:
    stats = {"lines": 0, "bad_json": 0, "failed_rows": 0, "unique": 0, "dupes": 0,
             "written": 0, "kept_existing": 0}
    latest: dict[str, dict] = {}
    with open(src, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            stats["lines"] += 1
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                stats["bad_json"] += 1
                continue
            if rec.get("_failed") or not rec.get("file_hash"):
                stats["failed_rows"] += 1
                continue
            if rec["file_hash"] in latest:
                stats["dupes"] += 1
            latest[rec["file_hash"]] = rec  # 後寫覆蓋前寫＝取最後一行
    stats["unique"] = len(latest)
    for h, rec in latest.items():
        target = cache.cache_path(h, cache_dir)
        if target.exists() and not force:
            stats["kept_existing"] += 1
            continue
        if apply:
            cache.write_record(cache.record_from_legacy(rec), cache_dir)
        stats["written"] += 1
    return stats


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", type=Path, default=DEFAULT_SRC)
    ap.add_argument("--cache-dir", type=Path, default=cache.CACHE_DIR)
    ap.add_argument("--apply", action="store_true", help="真的寫檔並把來源改名 .bak；預設唯讀試跑")
    ap.add_argument("--force", action="store_true", help="已存在的 <hash>.json 也覆寫（預設保留）")
    args = ap.parse_args()

    if not args.src.exists():
        print(f"找不到 {args.src}", file=sys.stderr)
        return 2
    stats = migrate(args.src, args.cache_dir, args.apply, args.force)
    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"=== migrate_extraction_cache [{mode}] ===")
    for k, v in stats.items():
        print(f"  {k:<14} {v}")
    if args.apply:
        bak = args.src.with_name(args.src.name + ".bak")
        args.src.rename(bak)
        print(f"  來源已改名 → {bak}")
    else:
        print("  （唯讀試跑：沒有寫任何檔；加 --apply 才會寫）")
    return 1 if stats["bad_json"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
