#!/usr/bin/env python3
"""`data/sync_failures.log` → 可餵給 `sync_new_reports.py --delta` 的清單。

**為什麼不用 `--all-local`。** 那個模式對本地鏡像的**每一個檔**先抽字再查 DB，
是 O(全部檔)；2026-08-20 那次只有 7 篇要補，鏡像裡有 16,736 個檔。失敗記錄裡本來
就有逐筆路徑，直接把它轉成 delta 就好——實測 7 篇、160 chunks、fail=0。

**只讀，不寫 DB、不呼叫任何 LLM。** 產出是一個文字檔，要不要拿它去匯入是人的決定。

用法：
  uv run python scripts/failures_to_delta.py --out data/sync_delta_recover.txt
  uv run python scripts/failures_to_delta.py --stage tag --out /tmp/only_tag.txt

格式契約：`sync_failures.log` 每行是 `絕對路徑<TAB>階段<TAB>原因`，三個寫入點
（extract／tag／ingest）一致。**歷史行可能不是這個格式**——2026-08-20 之前 ingest
那一處寫的是 `file_hash<TAB>檔名<TAB>原因`，第 0 欄根本不是路徑。那些行對不回檔案，
會被計進 `unmappable` 並印出來，**不是靜默丟棄**：靜默丟棄會讓「補完了」與
「有一半根本沒被看到」長得一樣。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FAIL_LOG = ROOT / "data" / "sync_failures.log"
SRC_LOCAL = ROOT / "研報自動匯入"
EXTS = {".pdf", ".docx", ".doc"}


def parse_failures(
    lines, mirror_root: Path, stages: set[str] | None = None, exts: set[str] = EXTS
) -> tuple[list[str], list[str]]:
    """→ (可補救的相對路徑, 對不回檔案的原始行)。順序保留、去重。

    判定刻意以「檔案真的存在於鏡像下」為準，而不是信任欄位長相：格式在歷史上變過，
    而且這個清單的下一步是拿去匯入，寧可漏掉一行也不要送出一個不存在的路徑。
    """
    ok: list[str] = []
    seen: set[str] = set()
    bad: list[str] = []
    for raw in lines:
        line = raw.rstrip("\n")
        if not line.strip():
            continue
        parts = line.split("\t", 2)
        candidate = parts[0]
        stage = parts[1] if len(parts) > 1 else ""
        if stages is not None and stage not in stages:
            continue
        p = Path(candidate)
        try:
            rel = p.resolve().relative_to(mirror_root.resolve())
        except (ValueError, OSError):
            bad.append(line)
            continue
        if p.suffix.lower() not in exts or not p.is_file():
            bad.append(line)
            continue
        key = str(rel)
        if key not in seen:
            seen.add(key)
            ok.append(key)
    return ok, bad


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", default=str(FAIL_LOG), help="來源失敗記錄")
    ap.add_argument("--out", required=True, help="輸出的 delta 檔")
    ap.add_argument(
        "--stage",
        default=None,
        help="只取這些階段（逗號分隔，例如 tag,extract,ingest）；預設全取",
    )
    ap.add_argument("--mirror", default=str(SRC_LOCAL), help="本地鏡像根目錄")
    args = ap.parse_args()

    log = Path(args.log)
    if not log.is_file():
        print(f"找不到失敗記錄：{log}", file=sys.stderr)
        raise SystemExit(2)
    stages = set(s.strip() for s in args.stage.split(",")) if args.stage else None

    lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
    ok, bad = parse_failures(lines, Path(args.mirror), stages)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(("\n".join(ok) + "\n") if ok else "", encoding="utf-8")

    print(f"可補救：{len(ok)} 筆 → {out}")
    if bad:
        print(f"對不回檔案：{len(bad)} 筆（不是路徑、副檔名不符、或檔案已不在）")
        for b in bad[:10]:
            print(f"  {b[:120]}")
        if len(bad) > 10:
            print(f"  …另有 {len(bad) - 10} 筆")
    if not ok:
        print("沒有可補救的項目——不要拿空 delta 去跑匯入，那只會是一次無效的全流程。")
        raise SystemExit(1)
    print("下一步（先 dry-run 確認 skip_exists=0）：")
    print(f"  uv run python scripts/sync_new_reports.py --delta {out} --dry-run")


if __name__ == "__main__":
    main()
