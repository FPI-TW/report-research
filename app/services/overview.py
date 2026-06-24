"""問答總覽路徑：枚舉/聚合題改走全語料分面統計。

detect_overview() 判定是否為枚舉/聚合題（純規則）；resolve_filters() 把中文條件
解析成 OverviewFilters；aggregate_facets() 對 research.research_report 跑分面聚合得
CorpusOverview；format_facts()/render_overview_text() 序列化。本模組不呼叫 LLM。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.filename import BROKER_MAP, SOURCE_DISPLAY, source_display
from app.services.tagging import INSTRUMENT_DISPLAY, MARKET_DISPLAY
from app.services.textnorm import norm_for_match

# 聚合/枚舉提示詞：命中任一即視為「總覽題」
_OVERVIEW_CUES = (
    "所有", "全部", "有哪些", "哪些", "列出", "清單", "列表", "多少篇",
    "幾篇", "幾份", "種類", "類型", "一覽", "統計", "都有什麼", "有什麼",
)
_OVERVIEW_CUES_NORM = tuple(norm_for_match(c) for c in _OVERVIEW_CUES)


def detect_overview(q: str) -> bool:
    """命中聚合提示詞即視為總覽題（純函式）。"""
    s = norm_for_match(q)
    return any(c in s for c in _OVERVIEW_CUES_NORM)


# 市場同義詞 → findb 代碼（明確列出常見口語；不重用 LEGACY_TO_FINDB 的「期貨→WTX」，
# 避免與商品類型 futures 衝突——「台指期」才歸 WTX 市場，「期貨」歸商品類型）
_MARKET_SYNONYMS: dict[str, str] = {
    "台股": "TW", "台灣": "TW", "美股": "US", "美國": "US",
    "港股": "HK", "香港": "HK", "陸股": "CN", "中國": "CN", "大陸": "CN", "a股": "CN",
    "外匯": "FX", "匯率": "FX", "台指期": "WTX", "台指": "WTX",
    "總經": "MACRO", "總體經濟": "MACRO", "宏觀": "MACRO",
    "全球": "GLOBAL", "海外": "GLOBAL",
    "加密": "CRYPTO", "加密貨幣": "CRYPTO", "虛擬貨幣": "CRYPTO",
}
_MARKET_SYNONYMS_NORM = {norm_for_match(k): v for k, v in _MARKET_SYNONYMS.items()}

_INSTRUMENT_SYNONYMS: dict[str, str] = {
    "期貨": "futures", "選擇權": "options", "etf": "etf", "債券": "bond", "債": "bond",
    "原物料": "commodity", "商品期貨": "commodity",
    "指數": "index", "個股": "equity", "股票": "equity",
}
_INSTRUMENT_SYNONYMS_NORM = {norm_for_match(k): v for k, v in _INSTRUMENT_SYNONYMS.items()}

# 券商查找：中文券商名（BROKER_MAP 含中文鍵）+ 顯示名（含外資中文名）+ 代碼本身。
# 跳過 BROKER_MAP 的英文短碼（MS/GS…），避免在中文查詢裡誤命中。
_SOURCE_LOOKUP: dict[str, str] = {}
for _k, _v in BROKER_MAP.items():
    if re.search(r"[一-鿿]", _k):
        _SOURCE_LOOKUP[norm_for_match(_k)] = _v
for _code, _disp in SOURCE_DISPLAY.items():
    _SOURCE_LOOKUP[norm_for_match(_disp)] = _code
    _SOURCE_LOOKUP[norm_for_match(_code)] = _code

# 解析個股中文名時，先剝除的非個股詞（提示詞 + 各維度表面詞 + 常見填充詞）
_NAME_STOPWORDS: tuple[str, ...] = (
    _OVERVIEW_CUES
    + tuple(_MARKET_SYNONYMS)
    + tuple(_INSTRUMENT_SYNONYMS)
    + tuple(k for k in _SOURCE_LOOKUP)
    + ("研報", "報告", "研究", "給我", "我", "想", "請", "幫我", "關於",
       "相關", "這", "那", "的", "新", "出過", "出", "有", "最近", "今年",
       "去年", "本週", "這週", "一覽")
)


@dataclass
class OverviewFilters:
    source: str | None = None
    market: str | None = None
    instrument_type: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    stock_code: str | None = None
    stock_name: str | None = None

    def any(self) -> bool:
        return any(
            v is not None
            for v in (
                self.source, self.market, self.instrument_type,
                self.date_from, self.date_to, self.stock_code, self.stock_name,
            )
        )

    def applied_labels(self) -> list[str]:
        """給答案顯示「已套用：券商=元大 / 市場=台股 / …」。"""
        out: list[str] = []
        if self.source:
            out.append(f"券商={source_display(self.source)}")
        if self.market:
            out.append(f"市場={MARKET_DISPLAY.get(self.market, self.market)}")
        if self.instrument_type:
            out.append(f"商品類型={INSTRUMENT_DISPLAY.get(self.instrument_type, self.instrument_type)}")
        if self.date_from or self.date_to:
            lo = self.date_from.isoformat() if self.date_from else "…"
            hi = self.date_to.isoformat() if self.date_to else "…"
            out.append(f"期間={lo}~{hi}")
        if self.stock_code:
            out.append(f"個股代碼={self.stock_code}")
        if self.stock_name:
            out.append(f"個股={self.stock_name}")
        return out


def _match_longest(s_norm: str, table: dict[str, str]) -> str | None:
    """在已正規化的查詢字串裡，比對 table（鍵為已正規化）最長命中的值。"""
    best_key = None
    for key in table:
        if key and key in s_norm and (best_key is None or len(key) > len(best_key)):
            best_key = key
    return table[best_key] if best_key is not None else None


def _resolve_dates(s_norm: str, today: date) -> tuple[date | None, date | None]:
    if any(k in s_norm for k in ("最近一週", "近一週", "本週", "這週", "近七天")):
        return today - timedelta(days=7), today
    if any(k in s_norm for k in ("最近一個月", "近一個月", "近一月", "近30天")):
        return today - timedelta(days=30), today
    if any(k in s_norm for k in ("最近三個月", "近三個月", "近一季", "這一季")):
        return today - timedelta(days=90), today
    if any(k in s_norm for k in ("最近半年", "近半年", "近六個月")):
        return today - timedelta(days=182), today
    if "今年" in s_norm:
        return date(today.year, 1, 1), today
    if "去年" in s_norm:
        return date(today.year - 1, 1, 1), date(today.year - 1, 12, 31)
    m = re.search(r"(20\d{2})年", s_norm)
    if m:
        y = int(m.group(1))
        return date(y, 1, 1), date(y, 12, 31)
    return None, None


def _extract_stock_name(q: str) -> str | None:
    """剝除各維度表面詞後，取最長的 CJK 殘餘段（3~8 字）當公司名候選。"""
    residual = q
    for w in sorted(_NAME_STOPWORDS, key=len, reverse=True):
        if re.search(r"[一-鿿]", w):  # 只剝中文停用詞，英文交給其他維度
            residual = residual.replace(w, " ")
    # v1 取精確優先於召回：殘餘段下限 3 字。2 字常見名詞（如「天氣」）若被誤判成
    # 公司過濾，比漏掉 2 字真實公司名更糟（後者會自然回退 RAG）。鴻海/台泥這類
    # 2 字股名是已知的 best-effort v1 限制。
    runs = re.findall(r"[一-鿿]{3,8}", residual)
    if not runs:
        return None
    return max(runs, key=len)


def resolve_filters(q: str, today: date) -> OverviewFilters:
    """中文條件 → 結構化過濾（確定性對應；解析不到的維度留空）。"""
    s = norm_for_match(q)
    date_from, date_to = _resolve_dates(s, today)
    # 4 位數字 = 個股代碼，但緊跟「年」的（如「2025年」）是年份字面，非代碼。
    m = re.search(r"(?<!\d)(\d{4})(?!\d)(?!年)", q)
    stock_code = m.group(1) if m else None
    f = OverviewFilters(
        source=_match_longest(s, _SOURCE_LOOKUP),
        market=_match_longest(s, _MARKET_SYNONYMS_NORM),
        instrument_type=_match_longest(s, _INSTRUMENT_SYNONYMS_NORM),
        date_from=date_from,
        date_to=date_to,
        stock_code=stock_code,
    )
    # 個股中文名：僅在沒有代碼/券商/市場/商品類型命中時才嘗試殘餘抽取（保守，避免誤抓）
    if not (stock_code or f.source or f.market or f.instrument_type):
        f.stock_name = _extract_stock_name(q)
    return f
