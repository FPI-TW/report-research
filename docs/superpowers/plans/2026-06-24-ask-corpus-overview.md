# 問答總覽路徑 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 讓「給我所有元大的報告種類」這類枚舉/聚合題，改由全語料結構化分面統計作答，不再受限於 top-k 檢索抓到的 8 篇。

**Architecture:** 在 `answer_question` 既有 RAG 路徑之前插入「總覽分支」。純 Python 規則判定總覽題並解析條件（券商/市場/時間/個股/商品類型），確定性 SQL 對 `research.research_report` 做分面聚合，把算好的數字當「事實」交給 LLM 潤飾作答。任何判不到/解析不到/異常一律 fail-open 回退既有 RAG，離題閘門與既有行為零變動。

**Tech Stack:** Python 3.13、SQLAlchemy async（`text()` 原生 SQL）、claude CLI 串流（`stream_completion`）、`unittest`（既有測試框架，非 pytest fixture）。

## Global Constraints

- 繁體中文回答使用者；程式碼/識別字/路徑保持原文。
- 時間戳一律 UTC-aware：用 `datetime.now(timezone.utc)`（answer.py 既有用法），勿用 naive datetime。
- DB 存取一律 async；原生 SQL 包進 `text()`；`select()` 用於 ORM。
- 既有問答行為（RAG 路徑、離題閘門、事件序列）**不可改變**；總覽僅為新增分支，fail-open 回退。
- 不改 DB schema、不改 `web/server.py`、不改前端（重用既有 SSE 事件）。
- 測試用 `unittest.TestCase` / `unittest.IsolatedAsyncioTestCase`，比照 `tests/test_answer.py` 既有風格。
- 提交訊息用 Conventional Commits（`feat:`/`test:`…），繁中說明 what/why。
- 品質閘門：`uv run black app tests`、`uv run ruff check .`、`uv run mypy app`、`uv run pytest`。

---

## 檔案結構

- **Create `app/services/overview.py`** — 總覽路徑的純邏輯與 DB 聚合：`OverviewFilters`、`CorpusOverview` dataclass；`detect_overview`、`resolve_filters`、`aggregate_facets`、`format_facts`、`render_overview_text`、`OVERVIEW_SYSTEM_PROMPT`。不呼叫 LLM。
- **Modify `app/services/answer.py`** — 匯入 overview 元件；在 `answer_question` 插入總覽分支；新增 `_answer_overview()` 事件產生器（LLM 潤飾 + 模板退回 + SSE 事件）。
- **Create `tests/test_overview.py`** — `detect_overview`/`resolve_filters`/`format_facts`/`aggregate_facets`（fake session）/`answer_question` 總覽分支整合測試。

既有可重用對照表（直接 import，勿重造）：
- `app/services/filename.py`：`BROKER_MAP`（中文/英文→代碼）、`SOURCE_DISPLAY`（代碼→中文）、`source_display()`。
- `app/services/tagging.py`：`MARKET_DISPLAY`（代碼→中文）、`MARKETS`、`INSTRUMENT_DISPLAY`（代碼→中文）、`INSTRUMENT_TYPES`。
- `app/services/textnorm.py`：`norm_for_match()`（NFKC+小寫+去空白）。
- `app/services/answer.py`：`Source` dataclass、`_log_qa()`、`stream_completion`、`SEARCH_EVENT`、`DEFAULT_MODEL`。

---

### Task 1: `detect_overview()` — 總覽題判定（純函式）

**Files:**
- Create: `app/services/overview.py`
- Test: `tests/test_overview.py`

**Interfaces:**
- Produces: `detect_overview(q: str) -> bool`

- [ ] **Step 1: 建立測試檔與失敗測試**

`tests/test_overview.py`：
```python
import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.overview import detect_overview  # noqa: E402


class DetectOverviewTests(unittest.TestCase):
    def test_enumeration_cue_is_overview(self):
        self.assertTrue(detect_overview("給我所有元大的報告種類"))
        self.assertTrue(detect_overview("台股最近一週有哪些新研報"))
        self.assertTrue(detect_overview("元大總共有多少篇研報"))

    def test_specific_question_is_not_overview(self):
        self.assertFalse(detect_overview("台積電的投資評級如何"))
        self.assertFalse(detect_overview("元大怎麼看半導體景氣"))
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run python -m pytest tests/test_overview.py -q`
Expected: FAIL（`ModuleNotFoundError: app.services.overview` 或 `ImportError`）

- [ ] **Step 3: 建立 overview.py 並實作 `detect_overview`**

`app/services/overview.py`：
```python
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
```

- [ ] **Step 4: 跑測試確認通過**

Run: `uv run python -m pytest tests/test_overview.py -q`
Expected: PASS（2 passed）

- [ ] **Step 5: Commit**

```bash
git add app/services/overview.py tests/test_overview.py
git commit -m "feat(overview): 新增 detect_overview 總覽題判定"
```

---

### Task 2: `resolve_filters()` — 中文條件解析成結構化過濾（純函式）

**Files:**
- Modify: `app/services/overview.py`
- Test: `tests/test_overview.py`

**Interfaces:**
- Consumes: `BROKER_MAP`, `SOURCE_DISPLAY`, `MARKET_DISPLAY`, `norm_for_match`
- Produces:
  - `@dataclass OverviewFilters` 欄位：`source: str|None`, `market: str|None`, `instrument_type: str|None`, `date_from: date|None`, `date_to: date|None`, `stock_code: str|None`, `stock_name: str|None`；方法 `any() -> bool`、`applied_labels() -> list[str]`
  - `resolve_filters(q: str, today: date) -> OverviewFilters`

- [ ] **Step 1: 寫失敗測試**

在 `tests/test_overview.py` 追加：
```python
from app.services.overview import OverviewFilters, resolve_filters  # noqa: E402


class ResolveFiltersTests(unittest.TestCase):
    TODAY = date(2026, 6, 24)

    def _r(self, q):
        return resolve_filters(q, self.TODAY)

    def test_broker_chinese_name(self):
        self.assertEqual(self._r("給我所有元大的研報").source, "yuanta")

    def test_broker_display_name_foreign(self):
        self.assertEqual(self._r("摩根士丹利有哪些研報").source, "morgan_stanley")

    def test_market_synonyms(self):
        self.assertEqual(self._r("台股有哪些新研報").market, "TW")
        self.assertEqual(self._r("美國市場研報清單").market, "US")

    def test_instrument_type(self):
        self.assertEqual(self._r("有哪些期貨研報").instrument_type, "futures")
        self.assertEqual(self._r("ETF 報告列表").instrument_type, "etf")

    def test_relative_dates(self):
        f = self._r("最近一週有哪些研報")
        self.assertEqual((f.date_from, f.date_to), (date(2026, 6, 17), self.TODAY))
        g = self._r("今年有哪些元大研報")
        self.assertEqual((g.date_from, g.date_to), (date(2026, 1, 1), self.TODAY))

    def test_year_literal(self):
        f = self._r("2025年有哪些台股研報")
        self.assertEqual((f.date_from, f.date_to), (date(2025, 1, 1), date(2025, 12, 31)))

    def test_stock_code(self):
        self.assertEqual(self._r("2330 有哪些研報").stock_code, "2330")

    def test_stock_name_residual(self):
        f = self._r("給我所有台積電的研報")
        self.assertEqual(f.stock_name, "台積電")

    def test_no_filter(self):
        self.assertFalse(self._r("列出所有天氣種類").any())

    def test_applied_labels(self):
        f = self._r("元大台股最近一週有哪些研報")
        labels = f.applied_labels()
        self.assertIn("券商=元大", labels)
        self.assertIn("市場=台股", labels)
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run python -m pytest tests/test_overview.py::ResolveFiltersTests -q`
Expected: FAIL（`ImportError: cannot import name 'OverviewFilters'`）

- [ ] **Step 3: 實作 `OverviewFilters` 與 `resolve_filters`**

在 `app/services/overview.py`（`detect_overview` 之後）追加：
```python
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
                self.date_from, self.stock_code, self.stock_name,
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
    """剝除各維度表面詞後，取最長的 CJK 殘餘段（2~8 字）當公司名候選。"""
    residual = q
    for w in sorted(_NAME_STOPWORDS, key=len, reverse=True):
        if re.search(r"[一-鿿]", w):  # 只剝中文停用詞，英文交給其他維度
            residual = residual.replace(w, " ")
    runs = re.findall(r"[一-鿿]{2,8}", residual)
    if not runs:
        return None
    return max(runs, key=len)


def resolve_filters(q: str, today: date) -> OverviewFilters:
    """中文條件 → 結構化過濾（確定性對應；解析不到的維度留空）。"""
    s = norm_for_match(q)
    date_from, date_to = _resolve_dates(s, today)
    m = re.search(r"(?<!\d)(\d{4})(?!\d)", q)
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
```

- [ ] **Step 4: 跑測試確認通過**

Run: `uv run python -m pytest tests/test_overview.py::ResolveFiltersTests -q`
Expected: PASS（11 passed）。若 `test_stock_name_residual` 失敗，檢查 `_NAME_STOPWORDS` 是否漏掉某個表面詞導致殘餘多段。

- [ ] **Step 5: Commit**

```bash
git add app/services/overview.py tests/test_overview.py
git commit -m "feat(overview): resolve_filters 解析券商/市場/時間/個股/商品類型條件"
```

---

### Task 3: `aggregate_facets()` — 全語料分面聚合（DB）

**Files:**
- Modify: `app/services/overview.py`
- Test: `tests/test_overview.py`

**Interfaces:**
- Consumes: `OverviewFilters`、`AsyncSession`
- Produces:
  - `@dataclass CorpusOverview` 欄位：`total: int`, `date_min: date|None`, `date_max: date|None`, `by_market: list[tuple[str,int]]`, `by_instrument: list[tuple[str,int]]`, `by_source: list[tuple[str,int]]`, `by_report_type: list[tuple[str,int]]`, `top_stocks: list[tuple[str,int]]`, `samples: list[tuple[str,str,str|None,date|None]]`（report_id, file_name, market, report_date）, `filters: OverviewFilters`
  - `aggregate_facets(session: AsyncSession, f: OverviewFilters, *, sample_k: int = 5) -> CorpusOverview`

- [ ] **Step 1: 寫失敗測試（fake session 回傳排隊好的結果）**

在 `tests/test_overview.py` 追加：
```python
from app.services.overview import CorpusOverview, aggregate_facets  # noqa: E402


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return list(self._rows)


class _QueuedSession:
    """依序回傳預先排好的 _FakeResult，模擬 aggregate_facets 的多次 execute。"""

    def __init__(self, results):
        self._results = list(results)
        self.executed = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, *a, **k):
        res = self._results[self.executed]
        self.executed += 1
        return res


class AggregateFacetsTests(unittest.IsolatedAsyncioTestCase):
    async def test_packs_rows_into_overview(self):
        results = [
            _FakeResult([(734, date(2021, 3, 1), date(2026, 6, 20))]),  # totals
            _FakeResult([("TW", 700), ("US", 20), ("MACRO", 14)]),      # by_market
            _FakeResult([("equity", 690), ("index", 300)]),             # by_instrument
            _FakeResult([("yuanta", 734)]),                             # by_source
            _FakeResult([("(未標註)", 732), ("速報", 1), ("策略", 1)]),  # by_report_type
            _FakeResult([("2330", 120), ("2317", 80)]),                # top_stocks
            _FakeResult([("rid1", "元大-台積電.pdf", "TW", date(2026, 6, 20))]),  # samples
        ]
        f = OverviewFilters(source="yuanta")
        ov = await aggregate_facets(_QueuedSession(results), f)
        self.assertEqual(ov.total, 734)
        self.assertEqual(ov.date_max, date(2026, 6, 20))
        self.assertEqual(ov.by_market[0], ("TW", 700))
        self.assertEqual(ov.by_report_type[0], ("(未標註)", 732))
        self.assertEqual(ov.samples[0][1], "元大-台積電.pdf")
        self.assertIs(ov.filters, f)
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run python -m pytest tests/test_overview.py::AggregateFacetsTests -q`
Expected: FAIL（`ImportError: cannot import name 'CorpusOverview'`）

- [ ] **Step 3: 實作 `CorpusOverview`、`_build_where`、`aggregate_facets`**

在 `app/services/overview.py` 追加：
```python
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
    top_stocks = [(str(k), int(n)) for k, n in top_stocks_rows]

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
```

注意：`grouped()` 內層用同一份 `params`，且 `by_instrument`/`top_stocks` 的 `unnest` 與 WHERE 都掛在別名 `r` 上，故 `_build_where` 的 `r.` 限定不可省。fake session 測試只驗證「結果列 → dataclass 欄位」打包正確；真實 SQL 由 Task 6 端到端驗證。

- [ ] **Step 4: 跑測試確認通過**

Run: `uv run python -m pytest tests/test_overview.py::AggregateFacetsTests -q`
Expected: PASS（1 passed）

- [ ] **Step 5: Commit**

```bash
git add app/services/overview.py tests/test_overview.py
git commit -m "feat(overview): aggregate_facets 全語料分面聚合查詢"
```

---

### Task 4: `format_facts()` / `render_overview_text()` / `OVERVIEW_SYSTEM_PROMPT`（純函式）

**Files:**
- Modify: `app/services/overview.py`
- Test: `tests/test_overview.py`

**Interfaces:**
- Consumes: `CorpusOverview`、`MARKET_DISPLAY`、`INSTRUMENT_DISPLAY`、`source_display`
- Produces:
  - `format_facts(ov: CorpusOverview) -> str`（給 LLM 的事實區塊）
  - `render_overview_text(ov: CorpusOverview) -> str`（LLM 失敗時的確定性模板答案，含 `[n]` 樣本）
  - `OVERVIEW_SYSTEM_PROMPT: str`

- [ ] **Step 1: 寫失敗測試**

在 `tests/test_overview.py` 追加：
```python
from app.services.overview import (  # noqa: E402
    format_facts,
    render_overview_text,
)


def _sample_overview():
    return CorpusOverview(
        total=734,
        date_min=date(2021, 3, 1),
        date_max=date(2026, 6, 20),
        by_market=[("TW", 700), ("US", 20)],
        by_instrument=[("equity", 690), ("index", 300)],
        by_source=[],
        by_report_type=[("(未標註)", 732), ("速報", 1)],
        top_stocks=[("2330", 120)],
        samples=[("rid1", "元大-台積電.pdf", "TW", date(2026, 6, 20))],
        filters=OverviewFilters(source="yuanta"),
    )


class FormatFactsTests(unittest.TestCase):
    def test_facts_contain_numbers_and_labels(self):
        txt = format_facts(_sample_overview())
        self.assertIn("734", txt)
        self.assertIn("台股", txt)        # 市場代碼轉中文
        self.assertIn("(未標註)", txt)     # 稀疏 report_type 桶
        self.assertIn("元大", txt)         # 已套用條件顯示

    def test_render_text_has_total_and_sample_citation(self):
        txt = render_overview_text(_sample_overview())
        self.assertIn("734", txt)
        self.assertIn("[1]", txt)          # 樣本帶編號供點閱

    def test_zero_total_handled(self):
        ov = CorpusOverview(total=0, date_min=None, date_max=None,
                            filters=OverviewFilters(source="yuanta"))
        self.assertIn("找不到", render_overview_text(ov))
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run python -m pytest tests/test_overview.py::FormatFactsTests -q`
Expected: FAIL（`ImportError: cannot import name 'format_facts'`）

- [ ] **Step 3: 實作三者**

在 `app/services/overview.py` 追加：
```python
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


def render_overview_text(ov: CorpusOverview) -> str:
    """LLM 潤飾失敗時的確定性模板答案（保證有答案、不阻斷）。"""
    f = ov.filters or OverviewFilters()
    cond = "、".join(f.applied_labels()) or "全語料"
    if ov.total == 0:
        return f"在研報語料中找不到符合條件（{cond}）的研報。"
    parts = [f"符合條件（{cond}）的研報共 {ov.total} 篇。"]
    if ov.date_min or ov.date_max:
        lo = ov.date_min.isoformat() if ov.date_min else "?"
        hi = ov.date_max.isoformat() if ov.date_max else "?"
        parts.append(f"日期範圍 {lo} ~ {hi}。")
    if ov.by_market:
        parts.append("按市場：" + _fmt_pairs(ov.by_market, MARKET_DISPLAY) + "。")
    if ov.by_instrument:
        parts.append("按商品類型：" + _fmt_pairs(ov.by_instrument, INSTRUMENT_DISPLAY) + "。")
    untagged = dict(ov.by_report_type).get("(未標註)", 0)
    if untagged and untagged >= ov.total * 0.5:
        parts.append("（多數研報未標註『報告種類』欄位，故改以市場/商品類型維度呈現。）")
    if ov.samples:
        parts.append("最新樣本：")
        for i, (_rid, fn, _mk, rd) in enumerate(ov.samples, 1):
            ds = rd.isoformat() if hasattr(rd, "isoformat") else (rd or "")
            parts.append(f"[{i}] {fn}{f'（{ds}）' if ds else ''}")
    return "\n".join(parts)
```

- [ ] **Step 4: 跑測試確認通過**

Run: `uv run python -m pytest tests/test_overview.py::FormatFactsTests -q`
Expected: PASS（3 passed）

- [ ] **Step 5: Commit**

```bash
git add app/services/overview.py tests/test_overview.py
git commit -m "feat(overview): format_facts/render_overview_text 與總覽 system prompt"
```

---

### Task 5: 接線 `answer_question` — 總覽分支 + `_answer_overview` 事件產生器

**Files:**
- Modify: `app/services/answer.py:578-628`（`answer_question` 開頭的歷史/檢索區塊）、新增 `_answer_overview()`、import 區塊
- Test: `tests/test_overview.py`

**Interfaces:**
- Consumes: `detect_overview`, `resolve_filters`, `aggregate_facets`, `format_facts`, `render_overview_text`, `OVERVIEW_SYSTEM_PROMPT`, `CorpusOverview`（皆來自 `app.services.overview`）；既有 `Source`, `_log_qa`, `stream_completion`, `SEARCH_EVENT`, `SessionFactory`
- Produces: `answer_question` 在偵測到總覽題且解析到 ≥1 條件時，改 yield 總覽事件序列（`sources`/`status`/`token`/`done`），不呼叫 `hybrid_search`

- [ ] **Step 1: 寫整合失敗測試（monkeypatch overview 元件與串流）**

在 `tests/test_overview.py` 追加：
```python
import asyncio  # noqa: E402

from app.services import answer as ans  # noqa: E402


class AnswerQuestionOverviewBranchTests(unittest.TestCase):
    def _drive(self, question):
        async def run():
            events = []
            async for ev in ans.answer_question(question):
                events.append(ev)
            return events

        return asyncio.run(run())

    def _patch_common(self):
        called = {"hybrid": 0}

        async def fake_hybrid(*a, **k):
            called["hybrid"] += 1
            return []

        async def fake_agg(session, f, **k):
            return CorpusOverview(
                total=734, date_min=date(2021, 3, 1), date_max=date(2026, 6, 20),
                by_market=[("TW", 700)], by_instrument=[("equity", 690)],
                by_source=[], by_report_type=[("(未標註)", 732)],
                top_stocks=[("2330", 120)],
                samples=[("rid1", "元大-台積電.pdf", "TW", date(2026, 6, 20))],
                filters=OverviewFilters(source="yuanta"),
            )

        async def fake_stream(*a, **k):
            yield "元大"
            yield "共有 734 篇研報。[1]"

        async def fake_log(*a, **k):
            return "qa-id"

        ans.hybrid_search = fake_hybrid
        ans.aggregate_facets = fake_agg
        ans.stream_completion = fake_stream
        ans._log_qa = fake_log
        ans.SessionFactory = lambda: _QueuedSession([])
        return called

    def test_overview_question_takes_overview_path(self):
        orig = (ans.hybrid_search, ans.aggregate_facets, ans.stream_completion,
                ans._log_qa, ans.SessionFactory)
        try:
            called = self._patch_common()
            events = self._drive("給我所有元大的報告種類")
            kinds = [e[0] for e in events]
            self.assertIn("sources", kinds)
            self.assertIn("token", kinds)
            self.assertEqual(kinds[-1], "done")
            self.assertEqual(called["hybrid"], 0)  # 沒走 RAG 檢索
            text_joined = "".join(p for k, p in events if k == "token" and isinstance(p, str))
            self.assertIn("734", text_joined)
        finally:
            (ans.hybrid_search, ans.aggregate_facets, ans.stream_completion,
             ans._log_qa, ans.SessionFactory) = orig
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run python -m pytest tests/test_overview.py::AnswerQuestionOverviewBranchTests -q`
Expected: FAIL（`AttributeError: module 'app.services.answer' has no attribute 'aggregate_facets'`，因尚未 import）

- [ ] **Step 3: 在 answer.py import overview 元件**

`app/services/answer.py` 既有 import 區（約 line 27-30，`from app.services.intent import ...` 附近）後追加：
```python
from app.services.overview import (
    CorpusOverview,
    OVERVIEW_SYSTEM_PROMPT,
    aggregate_facets,
    detect_overview,
    format_facts,
    render_overview_text,
    resolve_filters,
)
```

- [ ] **Step 4: 重構 `answer_question` 歷史/檢索區塊插入總覽分支**

把 `app/services/answer.py` 現有區塊（line ~598-628，從 `turns = await load_recent_turns...` 到第一個 `except BaseException:` 區塊結束）替換為：
```python
    # 僅「續問」才載歷史；首輪無歷史，維持並行意圖判定
    turns = await load_recent_turns(conv_id) if conversation_id else []
    history_block = build_history_block(turns)

    # 多輪需先 condense 取得獨立查詢；首輪直接用原問題（意圖判定仍延後並行）
    if turns:
        standalone_query, in_domain = await condense_and_classify(
            history_block, question
        )
        timer.mark("condense")
    else:
        standalone_query, in_domain = question, None

    # 總覽分支：枚舉/聚合題改走全語料分面統計（純規則判定，零 LLM、零向量檢索）。
    # 需解析到 ≥1 金融條件才改道——此門檻本身即離題保護，否則回退既有 RAG。
    # fail-open：聚合在第一個 yield 之前拋例外（produced 仍為 False）則落回 RAG。
    ov_filters = resolve_filters(
        standalone_query, datetime.now(timezone.utc).date()
    )
    if detect_overview(standalone_query) and ov_filters.any():
        try:
            produced = False
            async for ev in _answer_overview(
                question,
                ov_filters,
                filters,
                conv_id=conv_id,
                model=model,
                started=started,
            ):
                produced = True
                yield ev
            if produced:
                return
        except Exception:
            logger.exception("overview path failed; falling back to RAG")
            # 落到下方 RAG 路徑（不 return）

    # 既有 RAG 路徑
    if turns:
        qvec = await asyncio.to_thread(embed_query_cached, standalone_query)
        timer.mark("embed")
        async with SessionFactory() as session:  # 短連線：檢索完即釋放
            scored = await hybrid_search(
                session, standalone_query, qvec, k=k, dense_scan=ASK_DENSE_SCAN, **filters
            )
        timer.mark("retrieve")
    else:
        intent_task = asyncio.create_task(classify_intent(question))
        try:
            qvec = await asyncio.to_thread(embed_query_cached, question)
            timer.mark("embed")
            async with SessionFactory() as session:
                scored = await hybrid_search(
                    session, question, qvec, k=k, dense_scan=ASK_DENSE_SCAN, **filters
                )
            timer.mark("retrieve")
            in_domain = await intent_task
            timer.mark("intent_wait")  # 與 embed/retrieve 並行，故為等待耗時、非序列
        except BaseException:
            intent_task.cancel()
            raise
```

- [ ] **Step 5: 新增 `_answer_overview()` 產生器**

在 `app/services/answer.py` 的 `answer_question` 定義**之前**（或之後皆可，需在模組層）新增：
```python
async def _answer_overview(
    question: str,
    ov_filters,
    filters: dict,
    *,
    conv_id: str,
    model: str,
    started: float,
) -> AsyncIterator[tuple[str, object]]:
    """總覽路徑：分面聚合 → LLM 用算好的數字潤飾 → 失敗退回模板。事件序列同主路徑。"""
    async with SessionFactory() as session:  # 短連線：聚合完即釋放
        overview = await aggregate_facets(session, ov_filters)

    if overview.total == 0:
        msg = render_overview_text(overview)  # 「找不到…」
        yield ("sources", [])
        thinking_ms = int((time.monotonic() - started) * 1000)
        yield ("status", {"stage": "generating", "thinking_ms": thinking_ms})
        yield ("token", msg)
        qa_id = await _log_qa(
            question, msg, [], dict(filters, path="overview"), thinking_ms, [], [],
            conversation_id=conv_id, thinking_ms=thinking_ms,
        )
        yield ("done", {"cited": [], "qa_id": qa_id,
                        "conversation_id": conv_id, "thinking_ms": thinking_ms})
        return

    sources = [
        Source(n=i, report_id=rid, file_name=fn, market=mk,
               report_date=rd.isoformat() if hasattr(rd, "isoformat") else rd)
        for i, (rid, fn, mk, rd) in enumerate(overview.samples, 1)
    ]
    yield ("sources", [asdict(s) for s in sources])
    yield ("status", {"stage": "retrieved", "count": overview.total})

    facts = format_facts(overview)
    user_prompt = f"{facts}\n\n問題：{question}\n\n請依規則作答。"
    thinking_ms = int((time.monotonic() - started) * 1000)
    yield ("status", {"stage": "generating", "thinking_ms": thinking_ms})

    raw_parts: list[str] = []
    try:
        async for chunk in stream_completion(
            user_prompt, model=model, system=OVERVIEW_SYSTEM_PROMPT, allow_web=False
        ):
            if chunk == SEARCH_EVENT:
                continue
            raw_parts.append(chunk)
            yield ("token", chunk)
    except Exception:
        raw_parts = []  # 串流異常 → 退回模板

    body = "".join(raw_parts).strip()
    if not body:
        body = render_overview_text(overview)
        yield ("token", body)

    cited = cited_report_ids(body, sources)
    qa_id = await _log_qa(
        question, body, cited, dict(filters, path="overview"),
        int((time.monotonic() - started) * 1000),
        [asdict(s) for s in sources], [],
        conversation_id=conv_id, thinking_ms=thinking_ms,
    )
    yield ("done", {"cited": cited, "qa_id": qa_id,
                    "conversation_id": conv_id, "thinking_ms": thinking_ms})
```

注意：`_answer_overview` 用到的 `Source`/`asdict`/`cited_report_ids`/`_log_qa`/`time`/`stream_completion`/`SEARCH_EVENT`/`SessionFactory` 皆為 answer.py 既有模組層名稱，無需新增 import（除 Task 5 Step 3 的 overview import）。

- [ ] **Step 6: 跑整合測試與既有迴歸測試確認通過**

Run: `uv run python -m pytest tests/test_overview.py tests/test_answer.py tests/test_intent.py -q`
Expected: PASS（全綠；既有 `test_answer`/`test_intent` 不受影響）

- [ ] **Step 7: Commit**

```bash
git add app/services/answer.py tests/test_overview.py
git commit -m "feat(ask): answer_question 接入總覽分支與 _answer_overview"
```

---

### Task 6: 品質閘門 + 端到端驗證

**Files:**
- 無新增；全量檢查與真實 DB 抽查

- [ ] **Step 1: 格式化與靜態檢查**

Run:
```bash
uv run black app tests
uv run ruff check .
uv run mypy app
```
Expected: black 無變更或自動修正後乾淨；ruff 0 error；mypy 0 error（若 mypy 對 `text()` 回傳型別報 union-attr，於 `aggregate_facets` 內以區域變數標註或 `# type: ignore[union-attr]` 處理，並於 commit 說明）。

- [ ] **Step 2: 全測試套件**

Run: `uv run python -m pytest -q`
Expected: 全綠（含新 `tests/test_overview.py`，既有測試無回歸）。

- [ ] **Step 3: 真實 DB 端到端抽查 `aggregate_facets`**

確認 DB 容器在跑（`docker.exe ps | grep report-mark-postgres`），執行一次性腳本驗證真實 SQL（非 fake session）：
```bash
uv run python -c "
import asyncio
from datetime import date
from app.services.db import SessionFactory
from app.services.overview import resolve_filters, aggregate_facets, format_facts

async def main():
    f = resolve_filters('給我所有元大的報告種類', date.today())
    print('filters:', f)
    async with SessionFactory() as s:
        ov = await aggregate_facets(s, f)
    print(format_facts(ov))

asyncio.run(main())
"
```
Expected: `filters` 顯示 `source='yuanta'`；總篇數約 734；按報告種類顯示「(未標註) 約 732、…」；按市場/商品類型有分佈；最新樣本 5 筆。**若 total 與 §背景表的 734 不符，先停下檢查 `_build_where` 是否多掛了條件。**

- [ ] **Step 4: 真實服務端到端抽查（可選，需服務在跑）**

若 web 服務在跑（systemd `report-mark-web.service`），用 curl 對 `/api/ask` 送「給我所有元大的報告種類」，確認回傳的 SSE `sources` 為樣本、答案文字含總篇數而非僅 8 篇內容。若服務未跑則略過，以 Step 3 為準。

- [ ] **Step 5: 最終 commit（若 Step 1 有格式調整）**

```bash
git add -p app tests
git commit -m "style(overview): black/ruff/mypy 清理"
```

---

## Self-Review

**1. Spec coverage：**
- 設計 §1 流程（總覽分支插在 RAG 前、跳過 embed/檢索/離題）→ Task 5 Step 4。✅
- §2(a) `detect_overview` → Task 1；§2(b) `resolve_filters` 全維度 → Task 2；§2(c) `aggregate_facets`（含 report_type「(未標註)」桶、top 個股、最新樣本）→ Task 3。✅
- §3 答案生成（SQL 數字 + LLM 潤飾 + 樣本 sources + 純文字 deep-link 提示）→ Task 4（prompt/facts/模板）+ Task 5（`_answer_overview` 串流與 SSE）。✅
- §4 錯誤處理（非總覽/無 filter 回退、total==0 誠實、LLM 失敗退模板、SQL 異常 fail-open）→ Task 5（total==0 與串流 try/except）；「SQL 異常 fail-open」：`aggregate_facets` 例外會往上拋出 `answer_question`，由其呼叫端（web SSE 層）既有錯誤處理接手——**補強**：在 Task 5 的總覽分支用 try 包 `_answer_overview` 並於例外時 `pass` 落回下方 RAG？否——分支已 `return`。實作時將 `ov_filters.any()` 分支整段包進 `try/except Exception: pass` 後**不 return**，讓流程自然落到 RAG。修正見下方「型別/一致性」修補。
- §5 測試矩陣 → Task 1-5 各自 TDD + Task 6 全量。✅
- §不在範圍（名稱→代碼字典、report_type 回補、可點 deep-link、跨券商比較）→ 計畫未納入。✅

**2. Placeholder scan：** 無 TBD/TODO；每步含完整程式碼與指令。✅

**3. 型別/一致性修補（fail-open 落回 RAG）：** Task 5 Step 4 的總覽分支應確保「聚合/潤飾過程拋例外時回退 RAG」。將分支改為：
```python
    if detect_overview(standalone_query) and ov_filters.any():
        try:
            produced = False
            async for ev in _answer_overview(
                question, ov_filters, filters,
                conv_id=conv_id, model=model, started=started,
            ):
                produced = True
                yield ev
            if produced:
                return
        except Exception:
            logger.exception("overview path failed; falling back to RAG")
            # 落到下方 RAG 路徑（不 return）
```
注意：一旦已 yield 過事件再拋例外無法乾淨回退，故 `_answer_overview` 內部對 `aggregate_facets` 之後的 LLM 串流已自帶 try/except 退模板；此外層 try 主要保護「`aggregate_facets` 本身拋例外（尚未 yield 任何事件）」的情況——此時 `produced` 為 False，安全落回 RAG。實作 `_answer_overview` 時，**確保 `aggregate_facets` 呼叫在第一個 `yield` 之前**（目前設計即如此），fail-open 才成立。

此修補不影響其他 Task 的型別簽名（`_answer_overview` 簽名不變）。

**簽名一致性檢查：** `aggregate_facets(session, f, *, sample_k=5)`、`resolve_filters(q, today)`、`detect_overview(q)`、`format_facts(ov)`、`render_overview_text(ov)`、`_answer_overview(question, ov_filters, filters, *, conv_id, model, started)` 在 Task 2-5 各處引用一致。✅
