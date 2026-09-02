"""抽取快取：`data/extracted/<file_hash>.json`，一檔一筆（E1c，docs/EXTRACTION_REDESIGN.md §4.2）。

取代 422MB append-only 的 `data/extracted/all.jsonl`。那個檔有兩個讀取端、兩個寫入端，
無去重無版本：同一份 PDF 用新抽取器重抽一次，只能再 append 一行，讀取端拿到哪一行看
運氣。per-hash 之後「同一檔重抽」＝覆寫同一個檔案，`extraction_version` 在檔內。
對齊 `data/tags/<hash>.json` 的既有慣例。

## 紀錄格式（version 1）

舊 `all.jsonl` 的每個鍵都保留（`text`／`char_count`／`scanned`／`language`／`is_admin`／
`stock_code`／`company_name`／`source`／`report_date`／`report_type`／`file_name`／`file_path`），
讀取端不必分新舊；另加 E1a 的抽取欄位：

    extractor, extraction_version, page_count, pages_failed, quality, blocks

`blocks` 是 `[[type, page_no, start, end], …]`——序列化文字裡每個 Block 的字元區間，
**只在 pdfplumber 路徑有值**；舊紀錄轉檔後為空、`extractor` 記 `pypdf`、
`extraction_version` 記 `pypdf-legacy`（誠實：當年的 pypdf 版本沒記）。

## 寫入是原子的

先寫 `<hash>.json.tmp` 再 `os.replace`。sync 每 3 小時寫、tag／ingest 隨時可能讀，
半寫的檔案會讓讀取端 JSONDecodeError 而把那一筆當成不存在——那正是 §1.1 那種
「消失得沒有聲音」的落點。`os.replace` 在同一檔案系統上是原子的。
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
CACHE_DIR = ROOT / "data" / "extracted"
RECORD_VERSION = 1
LEGACY_VERSION = "pypdf-legacy"


def cache_path(file_hash: str, cache_dir: Path = CACHE_DIR) -> Path:
    if not file_hash or "/" in file_hash or file_hash.startswith("."):
        raise ValueError(f"file_hash 不合法：{file_hash!r}")
    return cache_dir / f"{file_hash}.json"


def record_from_result(res: Any, path: Path, meta: Any, source: str | None) -> dict[str, Any]:
    """`extract.ExtractResult` ＋ 檔名 metadata → 快取紀錄。四個寫入端只有這一種組法。"""
    return {
        "version": RECORD_VERSION,
        "file_hash": res.file_hash,
        "file_name": path.name,
        "file_path": str(path),
        "text": res.text,
        "char_count": res.char_count,
        "scanned": res.scanned,
        "language": res.language,
        "error": res.error,
        "is_admin": bool(getattr(meta, "is_admin", False)),
        "stock_code": getattr(meta, "stock_code", None),
        "company_name": getattr(meta, "company_name", None),
        "source": source,
        "report_date": (
            meta.report_date.isoformat() if getattr(meta, "report_date", None) else None
        ),
        "report_type": getattr(meta, "report_type", None),
        "extractor": res.extractor,
        "extraction_version": res.extraction_version,
        "page_count": res.page_count,
        "pages_failed": list(res.pages_failed),
        "quality": dict(res.quality or {}),
        "blocks": [list(b) for b in res.blocks],
    }


def record_from_legacy(rec: dict[str, Any]) -> dict[str, Any]:
    """舊 `all.jsonl` 的一行 → version 1 紀錄。缺的抽取欄位填 legacy，不猜。"""
    out = dict(rec)
    out.setdefault("version", RECORD_VERSION)
    out.setdefault("error", None)
    out.setdefault("extractor", "pypdf")
    out.setdefault("extraction_version", LEGACY_VERSION)
    out.setdefault("page_count", None)
    out.setdefault("pages_failed", [])
    out.setdefault("quality", {})
    out.setdefault("blocks", [])
    return out


def write_record(rec: dict[str, Any], cache_dir: Path = CACHE_DIR) -> Path:
    """原子寫入；同 hash 覆寫。回傳落點。"""
    cache_dir.mkdir(parents=True, exist_ok=True)
    final = cache_path(rec["file_hash"], cache_dir)
    tmp = final.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, final)
    return final


def read_record(file_hash: str, cache_dir: Path = CACHE_DIR) -> dict[str, Any] | None:
    p = cache_path(file_hash, cache_dir)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def iter_records(cache_dir: Path = CACHE_DIR) -> Iterator[dict[str, Any]]:
    """逐檔串流（不整批載入記憶體），**依檔名排序**——讀取端的處理順序才是決定性的。
    壞掉的檔（半寫、非 JSON）跳過並留給呼叫端計數：`rec['_unreadable']`。"""
    if not cache_dir.exists():
        return
    for p in sorted(cache_dir.glob("*.json")):
        if p.name.startswith("."):
            continue
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            yield {"_unreadable": True, "file_path": str(p)}
            continue
        if not isinstance(rec, dict) or "file_hash" not in rec:
            yield {"_unreadable": True, "file_path": str(p)}
            continue
        yield rec


def count_records(cache_dir: Path = CACHE_DIR) -> int:
    return sum(1 for _ in cache_dir.glob("*.json")) if cache_dir.exists() else 0


# ── 給摘錄批次用：把表格從餵給 LLM 的文字裡拿掉 ────────────────────────────


def strip_tables(text: str, blocks: list[list[Any]] | list[tuple[Any, ...]], expected_len: int | None = None) -> str:
    """依 Block 索引把 `type == 'table'` 的區間從文字裡拿掉（§4.2「逐字引文的防護」）。

    表格列被當成摘錄引文時，前端的關鍵字階梯沒有一階會剝掉 `|`——模型看不到表格列，
    就不可能引用它。**索引是對序列化字串算的**：`expected_len` 給進來且與 `text` 長度
    不符（例如入庫時剝過 NUL），代表座標系已經不同，原樣回傳、不硬切。"""
    if not blocks:
        return text
    if expected_len is not None and expected_len != len(text):
        return text
    spans = sorted((int(b[2]), int(b[3])) for b in blocks if b and b[0] == "table")
    if not spans:
        return text
    out: list[str] = []
    pos = 0
    for start, end in spans:
        if start < pos or end > len(text) or start >= end:
            return text  # 索引與文字對不上：不猜，整段原樣回傳
        out.append(text[pos:start])
        pos = end
    out.append(text[pos:])
    return "".join(out)
