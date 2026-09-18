"""跨文件重複段落（樣板）的字典與剔除（v4，docs/EXTRACTION.md §4）。

## 這支模組在修什麼

頁首頁尾偵測（`extraction/layout.py`）只看 10% 版面帶加跨頁重複，抓得到「每頁都印的
券商名」，抓不到每篇報告尾端整段的據點地址、評等定義、免責聲明、分析師聲明——它們
不在版面帶裡、在同一份 PDF 內也只出現一次，卻在同一家券商的每一篇都一模一樣。v3 全庫
605,485 個 chunk 有 12,110 個命中嚴格的免責樣式（寬鬆樣式 11%），檢索時它們對「風險」
「投資建議」這類詞每篇都命中。

版面層看不到語料，所以樣板要由語料決定：`scripts/build_boilerplate.py` 掃 `data/extracted/`
（per-hash 快取），把每個段落正規化後取 hash，同一 `source` 下出現在夠多篇文件、且橫跨
夠多不同標的的段落就是樣板，寫成 `data/boilerplate/<source>.json`。入庫（ingest／sync／
backfill）在切塊前用 `strip_boilerplate` 把這些段落從**要切塊的文字**拿掉；`full_text`
不動——閱讀頁與錨定基準都建立在完整正典文字上，樣板只是不進 chunk、不進嵌入。

## 為什麼是「跨標的」而不只是「跨文件」

同一檔股票的公司簡介（「台積電為全球最大晶圓代工廠…」）會在覆蓋它的每一篇報告重複，
出現次數輕易超過門檻，但它是內容不是樣板。要求段落橫跨至少 `MIN_DISTINCT_CODES` 個不同
`stock_code`（或出現在夠多沒有 stock_code 的總經／策略報告裡）就擋得住這一類。

## 失效模式全部 fail-open

字典不存在、壞掉、或 source 沒有字典 → 不剔除；剔除後只剩不到 `MIN_KEEP_FRAC` 的文字 →
退回原文（否則一篇全是樣板的檔會被當成掃描檔跳過）。切塊只會比沒有字典時**多**保留，
不會少。
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
from collections import defaultdict
from functools import lru_cache
from pathlib import Path
from typing import Iterable

from app.services.textnorm import clean_extracted, norm_for_match

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DIR = ROOT / "data" / "boilerplate"
RECORD_VERSION = 1

# 段落正規化後至少要這麼長才參與統計：短句（「資料來源：凱基」）本來就處處重複，
# 剔掉沒有意義也沒有害處，但會把字典撐大。
MIN_CHARS = 30
# 樣板門檻：同一 source 至少這麼多篇、或該 source 文件數的這個比例（取大者）。
MIN_DOCS = 8
MIN_DOC_FRAC = 0.005
# 跨標的門檻（見檔頭）。沒有 stock_code 的文件（總經、策略、日報）另計。
MIN_DISTINCT_CODES = 3
# 剔除後至少要剩原文的這個比例，否則退回原文。
MIN_KEEP_FRAC = 0.2

_RE_PARA = re.compile(r"\n\s*\n")


def source_bucket(source: str | None) -> str:
    return (source or "_none").strip().lower() or "_none"


def paragraph_key(para: str) -> str | None:
    """段落 → 16 字元 hash；太短回 None。正規化與 `content_norm` 同源（NFKC、小寫、去空白）。"""
    norm = norm_for_match(para)
    if len(norm) < MIN_CHARS:
        return None
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()[:16]


def build_index(records: Iterable[dict], *, min_docs: int = MIN_DOCS, min_doc_frac: float = MIN_DOC_FRAC,
                min_distinct_codes: int = MIN_DISTINCT_CODES) -> dict[str, dict]:
    """快取紀錄 → {source: 字典}。純計數、零 LLM，決定性。

    每筆紀錄需要 `text`、`source`、`stock_code`（可 None）。同一篇裡重複的段落只算一次。"""
    n_docs: dict[str, int] = defaultdict(int)
    hits: dict[str, dict[str, dict]] = defaultdict(dict)  # source → key → {docs, codes, nocode, sample}
    for rec in records:
        text = rec.get("text") or ""
        if not text.strip():
            continue
        src = source_bucket(rec.get("source"))
        code = (rec.get("stock_code") or "").strip() or None
        n_docs[src] += 1
        seen: set[str] = set()
        for para in _RE_PARA.split(clean_extracted(text)):
            key = paragraph_key(para)
            if key is None or key in seen:
                continue
            seen.add(key)
            slot = hits[src].get(key)
            if slot is None:
                slot = hits[src][key] = {"docs": 0, "codes": set(), "nocode": 0, "sample": para.strip()[:80]}
            slot["docs"] += 1
            if code:
                slot["codes"].add(code)
            else:
                slot["nocode"] += 1

    out: dict[str, dict] = {}
    for src, total in n_docs.items():
        threshold = max(min_docs, math.ceil(total * min_doc_frac))
        keys = {}
        for key, slot in hits[src].items():
            if slot["docs"] < threshold:
                continue
            if len(slot["codes"]) >= min_distinct_codes or slot["nocode"] >= threshold:
                keys[key] = {"docs": slot["docs"], "sample": slot["sample"]}
        out[src] = {"version": RECORD_VERSION, "source": src, "n_docs": total, "threshold": threshold, "keys": keys}
    return out


def write_index(index: dict[str, dict], out_dir: Path = DEFAULT_DIR) -> list[Path]:
    """一個 source 一個檔，原子寫入（先 .tmp 再 replace）。回寫出的路徑。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for src, payload in sorted(index.items()):
        path = out_dir / f"{src}.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=1), encoding="utf-8")
        os.replace(tmp, path)
        written.append(path)
    load_keys.cache_clear()
    return written


@lru_cache(maxsize=64)
def load_keys(source: str | None, out_dir: str = str(DEFAULT_DIR)) -> frozenset[str]:
    """讀某 source 的樣板 key 集合；沒有字典或壞檔 → 空集合（fail-open）。"""
    path = Path(out_dir) / f"{source_bucket(source)}.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        keys = payload.get("keys") or {}
        return frozenset(keys.keys() if isinstance(keys, dict) else keys)
    except FileNotFoundError:
        return frozenset()
    except Exception as exc:  # noqa: BLE001 — 壞檔不可以擋入庫
        logger.warning("boilerplate index unreadable for %s: %s", source, exc)
        return frozenset()


def strip_boilerplate(canonical: str, source: str | None, *, keys: frozenset[str] | None = None) -> tuple[str, int]:
    """把樣板段落從要切塊的正典文字拿掉。回 (文字, 剔除段落數)。

    `canonical` 必須是 `clean_extracted` 之後的字串（與 `paragraph_key` 建字典時同源）。
    剔除後剩不到 `MIN_KEEP_FRAC` 就退回原文。"""
    if not canonical:
        return canonical, 0
    keyset = load_keys(source) if keys is None else keys
    if not keyset:
        return canonical, 0
    kept: list[str] = []
    dropped = 0
    for para in _RE_PARA.split(canonical):
        if not para.strip():
            continue
        if paragraph_key(para) in keyset:
            dropped += 1
            continue
        kept.append(para)
    if not dropped:
        return canonical, 0
    text = "\n\n".join(kept)
    if len(text) < MIN_KEEP_FRAC * len(canonical):
        return canonical, 0
    return text, dropped
