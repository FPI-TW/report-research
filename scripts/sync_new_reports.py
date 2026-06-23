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
import sys
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
SRC_LOCAL = ROOT / "研報自動匯入"
TAGS_DIR = ROOT / "data" / "tags"
ALL_JSONL = ROOT / "data" / "extracted" / "all.jsonl"
FAIL_LOG = ROOT / "data" / "sync_failures.log"
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
        if name in seen:
            continue
        seen.add(name)
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
