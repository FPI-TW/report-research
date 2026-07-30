"""從 extracted JSONL 過濾出可標註檔，產 worklist 並分批。

過濾：去掉檔名即可判定的行政檔、掃描/空白檔。
resume：已存在 data/tags/<hash>.json 者略過（不放入 worklist）。
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXTRACTED = ROOT / "data" / "extracted" / "sample.jsonl"
TAGS_DIR = ROOT / "data" / "tags"
OUT = ROOT / "data" / "worklist_sample.json"
BATCH_SIZE = 15


def main() -> None:
    TAGS_DIR.mkdir(parents=True, exist_ok=True)
    items: list[dict] = []
    skipped_admin = skipped_scanned = skipped_done = 0

    with open(EXTRACTED, encoding="utf-8") as f:
        for line in f:
            rec = json.loads(line)
            if rec["is_admin"]:
                skipped_admin += 1
                continue
            if rec["scanned"]:
                skipped_scanned += 1
                continue
            if (TAGS_DIR / f"{rec['file_hash']}.json").exists():
                skipped_done += 1
                continue
            items.append(
                {
                    "file_hash": rec["file_hash"],
                    "file_path": rec["file_path"],
                    "file_name": rec["file_name"],
                }
            )

    OUT.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")

    # 分批檔（供 workflow / 平行標註）
    for b in range(0, len(items), BATCH_SIZE):
        batch = items[b : b + BATCH_SIZE]
        bp = ROOT / "data" / f"worklist_batch{b // BATCH_SIZE}.json"
        bp.write_text(json.dumps(batch, ensure_ascii=False, indent=2), encoding="utf-8")

    n_batches = (len(items) + BATCH_SIZE - 1) // BATCH_SIZE
    print(f"worklist: {len(items)} files → {OUT} ({n_batches} batches of {BATCH_SIZE})")
    print(
        f"skipped — admin: {skipped_admin}, scanned: {skipped_scanned}, "
        f"already-tagged: {skipped_done}"
    )


if __name__ == "__main__":
    main()
