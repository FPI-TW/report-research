"""問答總覽路徑：枚舉/聚合題改走全語料分面統計。

detect_overview() 判定是否為枚舉/聚合題（純規則）；resolve_filters() 把中文條件
解析成 OverviewFilters；aggregate_facets() 對 research.research_report 跑分面聚合得
CorpusOverview；format_facts()/render_overview_text() 序列化。本模組不呼叫 LLM。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
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

# 維度表面名詞（指「某個維度」本身而非其值，如「市場/商品/分類」）與狀態填充詞。
# 必須剝除，否則「目前市場」「市場分類」這類泛化片段會殘留成 ≥3 字 CJK 段，被
# _extract_stock_name 誤判為公司名 → 套成空的 company_name 過濾 → 枚舉題回「找不到」。
_DIMENSION_NOUNS: tuple[str, ...] = (
    "市場", "商品", "券商", "來源", "標的", "分類", "類別", "面向", "維度",
    "目前", "現在", "現況", "概況", "近期", "整體", "全部的",
)

# 解析個股中文名時，先剝除的非個股詞（提示詞 + 各維度表面詞 + 常見填充詞）
_NAME_STOPWORDS: tuple[str, ...] = (
    _OVERVIEW_CUES
    + tuple(_MARKET_SYNONYMS)
    + tuple(_INSTRUMENT_SYNONYMS)
    + tuple(k for k in _SOURCE_LOOKUP)
    + _DIMENSION_NOUNS
    + ("研報", "報告", "研究", "給我", "我", "想", "請", "幫我", "關於",
       "相關", "這", "那", "的", "最新", "出過", "出", "有", "最近", "今年",
       "去年", "本週", "這週", "一覽")
)


@dataclass
class OverviewFilters:
    source: str | None = None
    market: str | None = None
    instrument_type: str | None = None
    report_type: str | None = None
    relates_stock: bool | None = None
    relates_futures: bool | None = None
    date_from: date | None = None
    date_to: date | None = None
    stock_code: str | None = None
    stock_name: str | None = None

    def any(self) -> bool:
        return any(
            v is not None
            for v in (
                self.source, self.market, self.instrument_type,
                self.report_type, self.relates_stock, self.relates_futures,
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
        if self.report_type:
            out.append(f"報告種類={self.report_type}")
        if self.relates_stock:
            out.append("個股相關=true")
        if self.relates_futures:
            out.append("期貨相關=true")
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


def merge_request_filters(base: OverviewFilters, filters: dict) -> OverviewFilters:
    """把 ask request 的顯式 filters 套進 overview 條件。

    與主 RAG 路徑一致，呼叫端已給的 request filter 視為最終 scope；
    query 解析出的 overview filters 僅補足未顯式指定的維度。
    """
    return replace(
        base,
        market=filters.get("market") or base.market,
        instrument_type=filters.get("instrument_type") or base.instrument_type,
        report_type=filters.get("report_type") or base.report_type,
        relates_stock=filters.get("relates_stock")
        if filters.get("relates_stock") is not None
        else base.relates_stock,
        relates_futures=filters.get("relates_futures")
        if filters.get("relates_futures") is not None
        else base.relates_futures,
    )


def resolve_filters(q: str, today: date) -> OverviewFilters:
    """中文條件 → 結構化過濾（確定性對應；解析不到的維度留空）。"""
    s = norm_for_match(q)
    date_from, date_to = _resolve_dates(s, today)
    # 4 位數字 = 個股代碼，但緊跟「年」的（如「2025年」）是年份字面，非代碼。
    m = re.search(r"(?<!\d)(\d{4})(?!\d)(?!\s*年)", q)
    stock_code = m.group(1) if m else None
    f = OverviewFilters(
        source=_match_longest(s, _SOURCE_LOOKUP),
        market=_match_longest(s, _MARKET_SYNONYMS_NORM),
        instrument_type=_match_longest(s, _INSTRUMENT_SYNONYMS_NORM),
        date_from=date_from,
        date_to=date_to,
        stock_code=stock_code,
    )
    # 個股中文名需允許與券商/市場/商品類型並存，否則多條件查詢會丟失公司過濾。
    # 僅在已有明確 stock_code 時跳過，避免同時落兩個互斥個股條件。
    if not stock_code:
        f.stock_name = _extract_stock_name(q)
    return f


@dataclass
class CorpusOverview:
    total: int
    date_min: date | None
    date_max: date | None
    by_market: list[tuple[str, int]] = field(default_factory=list)
    by_instrument: list[tuple[str, int]] = field(default_factory=list)
    by_source: list[tuple[str, int]] = field(default_factory=list)
    by_report_type: list[tuple[str, int]] = field(default_factory=list)
    top_stocks: list[tuple[str, int]] = field(default_factory=list)
    samples: list[tuple[str, str, str | None, object]] = field(default_factory=list)
    filters: OverviewFilters | None = None


def _build_where(f: OverviewFilters) -> tuple[str, dict]:
    """OverviewFilters → (WHERE 片段, params)，欄位皆以別名 r 限定。"""
    conds = ["r.is_research = true"]
    params: dict = {}
    if f.source:
        conds.append("r.source = :source")
        params["source"] = f.source
    if f.market:
        conds.append("r.market = :market")
        params["market"] = f.market
    if f.instrument_type:
        conds.append("r.instrument_types @> ARRAY[:it]::text[]")
        params["it"] = f.instrument_type
    if f.report_type:
        conds.append("r.report_type = :report_type")
        params["report_type"] = f.report_type
    if f.relates_stock:
        conds.append("r.relates_stock = true")
    if f.relates_futures:
        conds.append("r.relates_futures = true")
    if f.date_from:
        conds.append("r.report_date >= :date_from")
        params["date_from"] = f.date_from
    if f.date_to:
        conds.append("r.report_date <= :date_to")
        params["date_to"] = f.date_to
    if f.stock_code:
        conds.append("(r.stock_code = :sc OR :sc = ANY(r.stock_targets))")
        params["sc"] = f.stock_code
    if f.stock_name:
        conds.append("r.company_name ILIKE :sname")
        params["sname"] = f"%{f.stock_name}%"
    return " AND ".join(conds), params


async def aggregate_facets(
    session: AsyncSession, f: OverviewFilters, *, sample_k: int = 5
) -> CorpusOverview:
    """對 research.research_report 跑分面聚合（WHERE = is_research + 解析到的條件）。"""
    where, params = _build_where(f)
    base = f"FROM research.research_report r WHERE {where}"

    totals = (
        await session.execute(
            text(f"SELECT count(*), min(r.report_date), max(r.report_date) {base}"),
            params,
        )
    ).first()
    total = int(totals[0]) if totals else 0

    async def grouped(expr: str, extra_from: str = "") -> list[tuple[str, int]]:
        rows = (
            await session.execute(
                text(
                    f"SELECT {expr} AS k, count(*) AS n "
                    f"FROM research.research_report r{extra_from} WHERE {where} "
                    f"GROUP BY k ORDER BY n DESC, k"
                ),
                params,
            )
        ).all()
        return [(str(k), int(n)) for k, n in rows if k is not None]

    by_market = await grouped("r.market")
    by_instrument = await grouped("it", extra_from=", unnest(r.instrument_types) it")
    by_source = [] if f.source else await grouped("r.source")
    by_report_type = await grouped("COALESCE(NULLIF(r.report_type, ''), '(未標註)')")
    top_stocks_rows = (
        await session.execute(
            text(
                "SELECT st AS k, count(*) AS n "
                "FROM research.research_report r, unnest(r.stock_targets) st "
                f"WHERE {where} GROUP BY st ORDER BY n DESC, st LIMIT 10"
            ),
            params,
        )
    ).all()
    top_stocks = [(str(k), int(n)) for k, n in top_stocks_rows if k is not None]

    sample_rows = (
        await session.execute(
            text(
                "SELECT r.id::text, r.file_name, r.market, r.report_date "
                f"{base} ORDER BY r.report_date DESC NULLS LAST LIMIT :k"
            ),
            {**params, "k": sample_k},
        )
    ).all()
    samples = [(rid, fn, mk, rd) for rid, fn, mk, rd in sample_rows]

    return CorpusOverview(
        total=total,
        date_min=totals[1] if totals else None,
        date_max=totals[2] if totals else None,
        by_market=by_market,
        by_instrument=by_instrument,
        by_source=by_source,
        by_report_type=by_report_type,
        top_stocks=top_stocks,
        samples=samples,
        filters=f,
    )


OVERVIEW_SYSTEM_PROMPT = (
    "你是「廷豐研報」的研究問答助理，正在回答一個『語料總覽/統計』問題。\n"
    "下方『分面統計』是系統對符合條件的全部研報，由資料庫精確算出的數字。請遵守：\n"
    "1. 只能引用『分面統計』提供的數字與清單，嚴禁自行臆測或捏造任何數量。\n"
    "2. 一律用繁體中文、條理清楚作答；針對使用者的問法（例如問『種類』）回應。\n"
    "3. 若使用者問的是『報告種類』而統計顯示多數為『(未標註)』，請誠實說明此欄位"
    "多數研報未標註，並改用『市場/商品類型/個股』等實際有標到的維度說明語料涵蓋範圍。\n"
    "4. 結尾可提示使用者可至檢索頁套用相同條件查看全部研報。\n"
    "5. 樣本研報已附編號，可在對應句末標註 [1]、[2]。"
)


def _fmt_pairs(pairs: list[tuple[str, int]], label_map: dict[str, str] | None) -> str:
    parts = []
    for k, n in pairs:
        name = label_map.get(k, k) if label_map else k
        parts.append(f"{name} {n}")
    return "、".join(parts) if parts else "（無）"


def format_facts(ov: CorpusOverview) -> str:
    """把 CorpusOverview 序列化成給 LLM 的『分面統計』事實區塊。"""
    f = ov.filters or OverviewFilters()
    lines = ["【分面統計】"]
    cond = "、".join(f.applied_labels()) or "（全語料）"
    lines.append(f"已套用條件：{cond}")
    lines.append(f"總篇數：{ov.total}")
    if ov.date_min or ov.date_max:
        lo = ov.date_min.isoformat() if ov.date_min else "?"
        hi = ov.date_max.isoformat() if ov.date_max else "?"
        lines.append(f"日期範圍：{lo} ~ {hi}")
    lines.append(f"按市場：{_fmt_pairs(ov.by_market, MARKET_DISPLAY)}")
    lines.append(f"按商品類型：{_fmt_pairs(ov.by_instrument, INSTRUMENT_DISPLAY)}")
    if ov.by_source:
        lines.append(
            "按券商：" + _fmt_pairs(
                [(source_display(k) or k, n) for k, n in ov.by_source], None
            )
        )
    lines.append(f"按報告種類：{_fmt_pairs(ov.by_report_type, None)}")
    if ov.top_stocks:
        lines.append(f"熱門個股標的：{_fmt_pairs(ov.top_stocks, None)}")
    if ov.samples:
        lines.append("最新樣本研報：")
        for i, (_rid, fn, _mk, rd) in enumerate(ov.samples, 1):
            ds = rd.isoformat() if hasattr(rd, "isoformat") else (rd or "")
            lines.append(f"[{i}] {fn}{f'（{ds}）' if ds else ''}")
    return "\n".join(lines)


def render_overview_text(ov: CorpusOverview, locale: str = "zh-Hant") -> str:
    """LLM 潤飾失敗時的確定性模板答案（保證有答案、不阻斷）。

    輸出隨 locale 切換（M10）；分面維度標籤（市場/商品類型）維持原文——證據不翻譯。
    非 en 一律回中文（fail-open）。
    """
    from app.services.locale import is_english

    en = is_english(locale)
    f = ov.filters or OverviewFilters()
    cond = "、".join(f.applied_labels()) or ("all reports" if en else "全語料")
    if ov.total == 0:
        return (
            f"No reports matching the criteria ({cond}) were found in the corpus."
            if en else f"在研報語料中找不到符合條件（{cond}）的研報。"
        )
    parts = [
        f"A total of {ov.total} reports match the criteria ({cond})."
        if en else f"符合條件（{cond}）的研報共 {ov.total} 篇。"
    ]
    if ov.date_min or ov.date_max:
        lo = ov.date_min.isoformat() if ov.date_min else "?"
        hi = ov.date_max.isoformat() if ov.date_max else "?"
        parts.append(f"Date range {lo} ~ {hi}." if en else f"日期範圍 {lo} ~ {hi}。")
    if ov.by_market:
        pairs = _fmt_pairs(ov.by_market, MARKET_DISPLAY)
        parts.append((f"By market: {pairs}." if en else f"按市場：{pairs}。"))
    if ov.by_instrument:
        pairs = _fmt_pairs(ov.by_instrument, INSTRUMENT_DISPLAY)
        parts.append((f"By instrument type: {pairs}." if en else f"按商品類型：{pairs}。"))
    untagged = dict(ov.by_report_type).get("(未標註)", 0)
    if untagged and untagged >= ov.total * 0.5:
        parts.append(
            "(Most reports have no 'report type' label, so market / instrument-type "
            "dimensions are shown instead.)"
            if en else "（多數研報未標註『報告種類』欄位，故改以市場/商品類型維度呈現。）"
        )
    if ov.samples:
        parts.append("Latest samples:" if en else "最新樣本：")
        for i, (_rid, fn, _mk, rd) in enumerate(ov.samples, 1):
            ds = rd.isoformat() if hasattr(rd, "isoformat") else (rd or "")
            parts.append(f"[{i}] {fn}{f'（{ds}）' if ds else ''}")
    return "\n".join(parts)
