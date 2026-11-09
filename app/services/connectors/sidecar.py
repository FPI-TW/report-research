"""metadata 側檔：連接器帶進來的檔案可附一份同名 JSON（或資料夾裡的 CSV 清單），提供已知的欄位。

## 格式

每份研報 `<主檔名>.<副檔名>` 旁邊放 `<主檔名>.json`（例如 `20261020_報告.pdf` ↔ `20261020_報告.json`），
內容是一個 JSON 物件，所有欄位都是選填：

| 欄位 | 型別 | 對應 |
|---|---|---|
| `title` | 字串（≤200 字） | `research_report.title`（`title_source='metadata'`，標題批次就不再為它呼叫 LLM） |
| `report_date` | `YYYY-MM-DD` | `research_report.report_date`（優先於檔名與 mtime） |
| `source` | 發行機構代碼（小寫英數、`_`、`-`） | `research_report.source`（優先於內文指紋與檔名） |
| `analysts` | 字串陣列 | 只保存在 `research.report_metadata` |
| `market` | 標準市場代碼（`tagging.MARKETS`） | `research_report.market`；給了就視為研究報告 |
| `instrument_types` | `tagging.INSTRUMENT_TYPES` 的子集 | `research_report.instrument_types` |
| `stock_targets` | 字串陣列（可為空陣列） | `research_report.stock_targets` |
| `futures_targets` | `tagging.FUTURES_TARGETS` 的子集 | `research_report.futures_targets` |
| `rating` | 字串（原文） | 只保存在 `research.report_metadata` |
| `target_price` | 正數 | 只保存在 `research.report_metadata` |
| `target_currency` | 三碼大寫幣別（給了 `target_price` 才可給） | 只保存在 `research.report_metadata` |

資料夾根目錄（S3 是 prefix 底下）也可以放一份 `_metadata.csv` 清單：第一欄 `file` 是相對於根目錄的路徑
（`/` 分隔），其餘欄名同上表；陣列欄位以 `;` 分隔，空格子＝沒提供。同一份檔案同時有 JSON 與 CSV 列時
視為錯誤（不猜哪一份為準）。

## 驗證（fail-closed）

未知欄位、型別錯、市場不是標準代碼、日期格式錯、JSON 語法錯、不是物件——一律拋 `SidecarError`，
呼叫端把這份檔案記進失敗清單、**不入庫**（不退回「當作沒有側檔」）：提供方寫了 metadata 卻被靜默忽略，
比沒寫更糟。側檔是人工提供的文字，原樣保存，不經 `zh_hant.to_traditional()`（那只用在 LLM 產出）。

## 與 LLM 產出的關係

- 標註（`tag`）：側檔同時給了 `market` 與 `stock_targets` 就不呼叫標註 LLM（`skips_tagging()`）；
  否則照常標註，再以側檔有給的欄位逐欄覆寫標註結果（`apply_to_tag()`）。
- 標題：側檔給了 `title` 就直接寫入，標題批次只挑 `title IS NULL` 的研報，所以不會再呼叫 LLM。
- 摘要、摘錄、訊號（`report_signal`）照常由 LLM 產出：訊號需要逐字證據與錨定，側檔的 `rating`、
  `target_price` 沒有證據，刻意不覆寫 `report_signal`，只保存在 `research.report_metadata` 供查證與後續使用。
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from app.services.tagging import FUTURES_TARGETS, INSTRUMENT_TYPES, MARKETS, MarketTag

MANIFEST_NAME = "_metadata.csv"
SIDECAR_SUFFIX = ".json"
# 側檔與清單的大小上限：metadata 是幾百位元組的東西，超過就是放錯檔。
MAX_SIDECAR_BYTES = 64 * 1024
MAX_MANIFEST_BYTES = 8 * 1024 * 1024

FIELDS = (
    "title", "report_date", "source", "analysts", "market", "instrument_types", "stock_targets",
    "futures_targets", "rating", "target_price", "target_currency",
)
LIST_FIELDS = frozenset({"analysts", "instrument_types", "stock_targets", "futures_targets"})

_SOURCE = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
_CURRENCY = re.compile(r"[A-Z]{3}")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


class SidecarError(ValueError):
    """側檔不合格。訊息帶檔名與欄位，進失敗清單給人看。"""


@dataclass(frozen=True)
class Metadata:
    """驗證過的側檔。`provided` 是側檔實際給了的欄位名（空陣列也算給了）。"""

    title: str | None = None
    report_date: date | None = None
    source: str | None = None
    analysts: tuple[str, ...] = ()
    market: str | None = None
    instrument_types: tuple[str, ...] = ()
    stock_targets: tuple[str, ...] = ()
    futures_targets: tuple[str, ...] = ()
    rating: str | None = None
    target_price: float | None = None
    target_currency: str | None = None
    provided: frozenset[str] = field(default_factory=frozenset)
    origin: str = ""  # 側檔來源（相對路徑或 `_metadata.csv:<行號>`），寫進 report_metadata 供追查

    def has(self, name: str) -> bool:
        return name in self.provided

    def skips_tagging(self) -> bool:
        """側檔同時給了 market 與 stock_targets：標註 LLM 能給的關鍵欄位都已知，不必呼叫。"""
        return self.has("market") and self.has("stock_targets")

    def to_json(self) -> dict[str, Any]:
        """只含側檔給了的欄位（`research.report_metadata.metadata` 的內容）。"""
        out: dict[str, Any] = {}
        for name in FIELDS:
            if name not in self.provided:
                continue
            value = getattr(self, name)
            if isinstance(value, date):
                value = value.isoformat()
            elif isinstance(value, tuple):
                value = list(value)
            out[name] = value
        return out


def _err(origin: str, key: str, reason: str) -> SidecarError:
    where = f"{origin}：{key}" if key else origin
    return SidecarError(f"metadata 側檔不合格：{where}：{reason}")


def _string(origin: str, key: str, value: Any, *, max_len: int) -> str:
    if not isinstance(value, str):
        raise _err(origin, key, f"必須是字串，目前是 {type(value).__name__}")
    cleaned = value.strip()
    if not cleaned:
        raise _err(origin, key, "不可為空字串（沒有就不要給這個欄位）")
    if len(cleaned) > max_len:
        raise _err(origin, key, f"最多 {max_len} 個字元")
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in cleaned):
        raise _err(origin, key, "不可含控制字元")
    return cleaned


def _string_list(origin: str, key: str, value: Any, *, max_items: int, max_len: int,
                 vocab: list[str] | None = None) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise _err(origin, key, f"必須是字串陣列，目前是 {type(value).__name__}")
    if len(value) > max_items:
        raise _err(origin, key, f"最多 {max_items} 項")
    out: list[str] = []
    for i, item in enumerate(value):
        s = _string(origin, f"{key}[{i}]", item, max_len=max_len)
        if vocab is not None and s not in vocab:
            raise _err(origin, f"{key}[{i}]", f"不在詞表內：{s!r}（可用：{'、'.join(vocab)}）")
        if s not in out:
            out.append(s)
    return tuple(out)


def validate(data: Any, origin: str) -> Metadata:
    """JSON 物件 → `Metadata`；任何不合格拋 `SidecarError`。"""
    if not isinstance(data, dict):
        raise _err(origin, "", "必須是 JSON 物件")
    unknown = sorted(k for k in data if k not in FIELDS)
    if unknown:
        raise _err(origin, unknown[0], f"未知的欄位（可用：{'、'.join(FIELDS)}）")
    values: dict[str, Any] = {}
    if "title" in data:
        values["title"] = _string(origin, "title", data["title"], max_len=200)
    if "report_date" in data:
        raw = data["report_date"]
        if not isinstance(raw, str) or not _DATE.fullmatch(raw):
            raise _err(origin, "report_date", "必須是 YYYY-MM-DD 字串")
        try:
            values["report_date"] = date.fromisoformat(raw)
        except ValueError as exc:
            raise _err(origin, "report_date", f"不是有效的日期：{raw!r}") from exc
    if "source" in data:
        source = _string(origin, "source", data["source"], max_len=64)
        if not _SOURCE.fullmatch(source):
            raise _err(origin, "source", f"發行機構代碼只能是小寫英數、底線、連字號：{source!r}")
        values["source"] = source
    if "analysts" in data:
        values["analysts"] = _string_list(origin, "analysts", data["analysts"], max_items=20, max_len=60)
    if "market" in data:
        market = data["market"]
        if not isinstance(market, str) or market not in MARKETS:
            raise _err(origin, "market", f"不是標準市場代碼：{market!r}（可用：{' '.join(MARKETS)}）")
        values["market"] = market
    if "instrument_types" in data:
        values["instrument_types"] = _string_list(
            origin, "instrument_types", data["instrument_types"], max_items=len(INSTRUMENT_TYPES), max_len=20,
            vocab=INSTRUMENT_TYPES)
    if "stock_targets" in data:
        values["stock_targets"] = _string_list(origin, "stock_targets", data["stock_targets"], max_items=50,
                                               max_len=20)
    if "futures_targets" in data:
        values["futures_targets"] = _string_list(
            origin, "futures_targets", data["futures_targets"], max_items=len(FUTURES_TARGETS), max_len=20,
            vocab=FUTURES_TARGETS)
    if "rating" in data:
        values["rating"] = _string(origin, "rating", data["rating"], max_len=40)
    if "target_price" in data:
        price = data["target_price"]
        # bool 是 int 的子類別：true 不是目標價。
        if isinstance(price, bool) or not isinstance(price, (int, float)) or not price > 0 or price >= 1e12:
            raise _err(origin, "target_price", f"必須是正數，目前是 {price!r}")
        values["target_price"] = float(price)
    if "target_currency" in data:
        if "target_price" not in data:
            raise _err(origin, "target_currency", "沒有 target_price 就不能給幣別")
        currency = data["target_currency"]
        if not isinstance(currency, str) or not _CURRENCY.fullmatch(currency):
            raise _err(origin, "target_currency", f"必須是三碼大寫幣別（例如 TWD、USD）：{currency!r}")
        values["target_currency"] = currency
    return Metadata(**values, provided=frozenset(data), origin=origin)


def parse_json_bytes(raw: bytes, origin: str) -> Metadata:
    if len(raw) > MAX_SIDECAR_BYTES:
        raise _err(origin, "", f"超過 {MAX_SIDECAR_BYTES // 1024} KiB")
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _err(origin, "", f"不是合法的 UTF-8 JSON：{exc}") from exc
    return validate(data, origin)


def _manifest_value(name: str, raw: str, origin: str, key: str) -> Any:
    if name in LIST_FIELDS:
        return [s.strip() for s in raw.split(";") if s.strip()]
    if name == "target_price":
        try:
            return float(raw)
        except ValueError as exc:
            raise _err(origin, key, f"不是數字：{raw!r}") from exc
    return raw


@dataclass(frozen=True)
class Manifest:
    """`_metadata.csv` 解析後：相對路徑 → Metadata；`digest` 是清單內容的雜湊（狀態比對用）。"""

    rows: Mapping[str, Metadata]
    digest: str


def parse_manifest(raw: bytes, origin: str = MANIFEST_NAME) -> Manifest:
    """CSV 清單 → Manifest。整份清單任何一列不合格就整份拋錯：清單是一次交付，不做部分採信。"""
    if len(raw) > MAX_MANIFEST_BYTES:
        raise _err(origin, "", f"超過 {MAX_MANIFEST_BYTES // (1024 * 1024)} MiB")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise _err(origin, "", f"不是 UTF-8：{exc}") from exc
    reader = csv.DictReader(io.StringIO(text))
    header = reader.fieldnames or []
    if not header or header[0] != "file":
        raise _err(origin, "", "第一欄必須是 file")
    unknown = [h for h in header[1:] if h not in FIELDS]
    if unknown:
        raise _err(origin, unknown[0], f"未知的欄位（可用：{'、'.join(FIELDS)}）")
    if len(set(header)) != len(header):
        raise _err(origin, "", "欄名重複")
    rows: dict[str, Metadata] = {}
    for lineno, row in enumerate(reader, start=2):
        where = f"{origin}:{lineno}"
        if None in row:
            raise _err(where, "", "欄位數比表頭多")
        rel = safe_relative((row.get("file") or "").strip())
        if rel is None:
            raise _err(where, "file", "必須是根目錄底下的相對路徑（不可含 .. 或以 / 開頭）")
        if rel in rows:
            raise _err(where, "file", f"同一份檔案列了兩次：{rel}")
        data = {k: _manifest_value(k, v.strip(), where, k) for k, v in row.items()
                if k != "file" and v is not None and v.strip()}
        rows[rel] = validate(data, where)
    return Manifest(rows=rows, digest=hashlib.sha256(raw).hexdigest())


def safe_relative(name: str) -> str | None:
    """相對路徑檢查：絕對路徑、`..`、空段一律拒收（key 與清單內容都不是可信輸入）。"""
    p = PurePosixPath(name)
    if not name or p.is_absolute() or any(part in ("", ".", "..") for part in p.parts):
        return None
    return str(p)


def sidecar_path(document: Path) -> Path:
    return document.with_suffix(SIDECAR_SUFFIX)


def apply_to_tag(tag: MarketTag | None, meta: Metadata) -> MarketTag | None:
    """以側檔有給的欄位覆寫標註結果。

    - 側檔給了 `market`：視為研究報告（提供方明確分類），即使 LLM 判非研究或標註失敗也以側檔為準——
      但只有在 `skips_tagging()`（另給了 `stock_targets`）時才會在沒有 LLM 標註的情況下成立。
    - 沒有標註結果（LLM 失敗）而側檔不足以取代時回 None，照常走標註失敗的路徑。
    """
    if tag is None:
        if not meta.skips_tagging():
            return None
        tag = MarketTag(market=None, is_research=True, confidence=1.0)
    stock = list(meta.stock_targets) if meta.has("stock_targets") else list(tag.stock_targets or [])
    futures = list(meta.futures_targets) if meta.has("futures_targets") else list(tag.futures_targets or [])
    return MarketTag(
        market=meta.market if meta.has("market") else tag.market,
        is_research=True if meta.has("market") else tag.is_research,
        confidence=1.0 if meta.skips_tagging() else tag.confidence,
        instrument_types=(list(meta.instrument_types) if meta.has("instrument_types")
                          else list(tag.instrument_types or [])),
        relates_stock=bool(stock) if meta.has("stock_targets") else tag.relates_stock,
        relates_futures=bool(futures) if meta.has("futures_targets") else tag.relates_futures,
        stock_targets=stock,
        futures_targets=futures,
    )
