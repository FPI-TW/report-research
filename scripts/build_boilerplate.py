"""建立跨文件樣板段落字典：`data/extracted/<hash>.json` → `data/boilerplate/<source>.json`。

對應 `app/services/boilerplate.py`（規則與門檻都在那裡）。**唯讀語料、零 LLM、不碰 DB**，
只寫 `data/boilerplate/`。入庫端（ingest／sync／backfill）切塊前讀這些字典剔除樣板段落；
字典不存在就不剔除，所以這支可以任何時候重跑、也可以完全不跑。

用法：
    uv run python scripts/build_boilerplate.py            # 全語料重建
    uv run python scripts/build_boilerplate.py --dry-run  # 只印各 source 的樣板數與樣例
    uv run python scripts/build_boilerplate.py --show 5   # 每個 source 印 5 條樣例

什麼時候重跑：新券商上線、既有券商換版型、或 `make freshness` 看到 chunk 樣板命中率回升。
週期性重跑放在維運手冊，不進 sync 鏈——字典穩定，每三小時重算 16,000 篇是純浪費。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import boilerplate  # noqa: E402
from app.services.extraction import cache as extraction_cache  # noqa: E402
from app.services.filename import resolve_source  # noqa: E402


def _records():
    """快取紀錄；`source` 缺值（舊格式轉檔的紀錄有一萬多筆）時用與 sync／backfill 相同的
    `resolve_source` 補，否則它們全掉進 `_none` 桶，各券商的字典就少了大半樣本。"""
    for rec in extraction_cache.iter_records():
        if rec.get("_unreadable") or rec.get("scanned") or not rec.get("text"):
            continue
        if not rec.get("source"):
            rec = {**rec, "source": resolve_source(rec.get("file_name") or "", rec.get("text"))}
        yield rec


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out-dir", type=Path, default=boilerplate.DEFAULT_DIR)
    ap.add_argument("--min-docs", type=int, default=boilerplate.MIN_DOCS)
    ap.add_argument("--dry-run", action="store_true", help="不寫檔，只印統計")
    ap.add_argument("--show", type=int, default=0, help="每個 source 印幾條樣例")
    args = ap.parse_args()

    index = boilerplate.build_index(_records(), min_docs=args.min_docs)
    total_keys = 0
    for src in sorted(index, key=lambda s: -index[s]["n_docs"]):
        payload = index[src]
        n = len(payload["keys"])
        total_keys += n
        print(f"{src:16s} docs={payload['n_docs']:6d} threshold={payload['threshold']:3d} boilerplate_paras={n}")
        if args.show:
            for key, meta in sorted(payload["keys"].items(), key=lambda kv: -kv[1]["docs"])[: args.show]:
                print(f"    {meta['docs']:5d}x  {meta['sample']}")
    print(f"=== sources={len(index)} boilerplate_paras={total_keys}{'（dry-run，未寫檔）' if args.dry_run else ''} ===")
    if args.dry_run:
        return 0
    written = boilerplate.write_index(index, args.out_dir)
    print(f"寫出 {len(written)} 個字典 → {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
