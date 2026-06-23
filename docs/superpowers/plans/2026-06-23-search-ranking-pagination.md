# 檢索結果排序與分頁優化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 讓關鍵字檢索移除結果硬上限（改載入更多分頁），並把預設排序改為報告層級「相關度分層內最新優先」。

**Architecture:** 後端新增純函式 `rank_reports`（分組成報告 + 依模式排序，可單元測試）；召回深度與「每報告取最佳 chunk」改成 `hybrid_search` / `search_chunks_lexical` 的**選用參數**（預設值＝現況，問答路徑零變動）；`/api/search` 改 `limit`/`offset` 分頁並回 `total`；前端 `run(append)` + 解除 `appendLoadMore` 僅 browse 限制 + load-more 依 mode 分派。

**Tech Stack:** Python 3.13 / FastAPI / SQLAlchemy async / pgvector(HNSW) / pg_trgm；前端原生 ESM（零工具鏈）；測試 `unittest`（後端）、`*.test.mjs`（前端）、Playwright（端到端）。

## Global Constraints

- 問答模式（`app/services/answer.py`、`/api/ask`）行為**不得改變**：所有召回相關變更必須是選用參數，預設值需重現現況；以「`uv run pytest tests/test_answer.py` 全綠」為回歸閘門。
- 瀏覽模式（`/api/reports`、`loadBrowse`）不動。
- 時間一律 UTC-aware；DB 存取 async；SQL 包 `text()`；設定走 `app.config`／env，勿硬編碼。
- 前端零工具鏈、原生 ESM、`html``/`raw()` 防 XSS 慣例不變；UI 標籤不加 emoji。
- band 寬度（相近相關度判定）= **0.05**（已與使用者拍板）。
- 共用工作目錄有他人未提交 WIP：**只 `git add` 本計畫明確列出的檔案**，提交前 `git status --short` 確認範圍，嚴禁 `git add -A`/`.`。
- 提交訊息走 Conventional Commits，結尾加 `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`。
- 回應使用者一律繁體中文。

---

## File Structure

- `app/services/retrieval.py`（改）：新增常數 `BAND_WIDTH`、`DENSE_SCAN_SEARCH`、`LEX_LIMIT_SEARCH`、`LEX_CAP_SEARCH`；新增 `RankedReport` dataclass 與 `rank_reports()` 純函式；`hybrid_search()` 加選用召回參數。
- `app/services/store.py`（改）：抽出 `_lexical_sql()` 純 SQL 組裝器；`search_chunks_lexical()` 加 `per_report` 參數。
- `web/server.py`（改）：`SearchResponse` 加 `total`；`/api/search` 改 `limit`/`offset` + `rank_reports` + 切片 + 全域 rank。
- `web/static/app/state.js`（改）：新增 `SEARCH_PAGE`。
- `web/static/app/api.js`（改）：`run(append=false)` 支援分頁追加。
- `web/static/app/render.js`（改）：`render()` 文案改 `total`、`appendLoadMore()` 解除僅 browse、load-more 依 mode 分派。
- `tests/test_retrieval_rank.py`（新）：`rank_reports` 排序/分組單元測試。
- `tests/test_store_sql.py`（新）：`_lexical_sql` per_report SQL 組裝測試。

---

## Task 1: `rank_reports` 純函式（報告層級排序）

**Files:**
- Modify: `app/services/retrieval.py`（檔尾新增常數、dataclass、函式）
- Test: `tests/test_retrieval_rank.py`（新）

**Interfaces:**
- Consumes: `hybrid_search` 回傳的 `scored: list[tuple[int, float, tuple]]`（`(tier, fused, row)`，已依 `(tier, fused)` 由高到低排序；`row[1]`=report_id、`row[6]`=report_date）。
- Produces:
  - `BAND_WIDTH: float = 0.05`
  - `@dataclass RankedReport`：`report_id: str`、`tier: int`、`best_score: float`、`report_date`（`datetime.date | None`）、`meta_row: tuple`、`passages: list[tuple[float, tuple]]`、`match_count: int`
  - `rank_reports(scored, *, sort: str = "relevance") -> list[RankedReport]`

- [ ] **Step 1: 寫失敗測試**

建立 `tests/test_retrieval_rank.py`：

```python
# tests/test_retrieval_rank.py
import sys
import unittest
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services.retrieval import rank_reports  # noqa: E402


def row(report_id, report_date=None):
    """造一列符合 hybrid_search 回傳結構的 row（rank_reports 只讀 [1] 與 [6]）。"""
    r = [None] * 16
    r[1] = report_id
    r[6] = report_date
    return tuple(r)


def scored(*items):
    """items: (report_id, tier, fused, date) → [(tier, fused, row), ...]。"""
    return [(tier, fused, row(rid, d)) for (rid, tier, fused, d) in items]


class RankReportsTests(unittest.TestCase):
    def ids(self, ranked):
        return [g.report_id for g in ranked]

    def test_group_dedup_keeps_best_first_chunk(self):
        # 同報告多 chunk：tier/best_score 取首見（最佳），match_count 累計
        s = scored(
            ("A", 2, 0.90, date(2024, 1, 1)),
            ("A", 0, 0.40, date(2024, 1, 1)),
        )
        ranked = rank_reports(s, sort="relevance")
        self.assertEqual(len(ranked), 1)
        self.assertEqual(ranked[0].tier, 2)
        self.assertEqual(ranked[0].best_score, 0.90)
        self.assertEqual(ranked[0].match_count, 2)
        self.assertEqual(len(ranked[0].passages), 2)

    def test_higher_tier_wins_even_if_older(self):
        s = scored(
            ("OLD2", 2, 0.50, date(2020, 1, 1)),
            ("NEW0", 0, 0.95, date(2024, 1, 1)),
        )
        self.assertEqual(self.ids(rank_reports(s, sort="relevance")), ["OLD2", "NEW0"])

    def test_higher_band_wins_within_tier_even_if_older(self):
        # 0.80 vs 0.60：band 16 vs 12 → 高 band 在前，不看日期
        s = scored(
            ("HIGH", 0, 0.80, date(2020, 1, 1)),
            ("LOW", 0, 0.60, date(2024, 1, 1)),
        )
        self.assertEqual(self.ids(rank_reports(s, sort="relevance")), ["HIGH", "LOW"])

    def test_within_same_band_newer_wins(self):
        # 0.71 與 0.73 同 band(14, 0.70~0.7499) → 日期新者在前
        s = scored(
            ("OLD", 0, 0.73, date(2021, 1, 1)),
            ("NEW", 0, 0.71, date(2024, 1, 1)),
        )
        self.assertEqual(self.ids(rank_reports(s, sort="relevance")), ["NEW", "OLD"])

    def test_none_date_sorts_last_within_band(self):
        s = scored(
            ("HASDATE", 0, 0.72, date(2021, 1, 1)),
            ("NODATE", 0, 0.72, None),
        )
        self.assertEqual(self.ids(rank_reports(s, sort="relevance")), ["HASDATE", "NODATE"])

    def test_date_desc_ignores_relevance(self):
        s = scored(
            ("OLDREL", 2, 0.99, date(2020, 1, 1)),
            ("NEW", 0, 0.30, date(2024, 1, 1)),
        )
        self.assertEqual(self.ids(rank_reports(s, sort="date_desc")), ["NEW", "OLDREL"])

    def test_date_asc_oldest_first_none_last(self):
        s = scored(
            ("MID", 0, 0.5, date(2022, 1, 1)),
            ("OLD", 0, 0.5, date(2020, 1, 1)),
            ("NODATE", 0, 0.5, None),
        )
        self.assertEqual(self.ids(rank_reports(s, sort="date_asc")), ["OLD", "MID", "NODATE"])

    def test_band_boundary_multiple_of_width(self):
        # 0.10 與 0.05 必須落在不同 band（浮點邊界不可黏在一起）
        s = scored(
            ("B2", 0, 0.10, date(2020, 1, 1)),
            ("B1", 0, 0.05, date(2024, 1, 1)),
        )
        self.assertEqual(self.ids(rank_reports(s, sort="relevance")), ["B2", "B1"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run python -m pytest tests/test_retrieval_rank.py -q`
Expected: FAIL — `ImportError: cannot import name 'rank_reports'`

- [ ] **Step 3: 實作最小程式碼**

在 `app/services/retrieval.py` 檔尾（`hybrid_search` 之後）新增：

```python
from dataclasses import dataclass, field

BAND_WIDTH = 0.05
# 檢索分頁專用召回深度（與問答路徑的 k*8 脫鉤；見 hybrid_search 選用參數）
DENSE_SCAN_SEARCH = 600
LEX_LIMIT_SEARCH = 1000
LEX_CAP_SEARCH = 8000


@dataclass
class RankedReport:
    """一篇報告的聚合結果：代表性 tier/分數取最佳 chunk，passages 依 chunk 順序累積。"""

    report_id: str
    tier: int
    best_score: float
    report_date: object  # datetime.date | None
    meta_row: tuple
    passages: list = field(default_factory=list)  # list[tuple[float, tuple]]
    match_count: int = 0


def _date_ordinal(d) -> int:
    """日期 → 序數；None → 0（在反向排序中最小，故殿後）。"""
    return d.toordinal() if d is not None else 0


def rank_reports(scored, *, sort: str = "relevance") -> list["RankedReport"]:
    """把 (tier, fused, row) chunk 清單分組成報告並排序。

    scored 已依 (tier, fused) 由高到低排序，故每篇首見 chunk 即其最佳 tier/分數。
    - relevance：(tier, band, 日期, fused) 由高到低——相關度分層內最新優先（band=0.05）。
    - date_desc：全召回報告依日期新→舊（None 殿後）。
    - date_asc：全召回報告依日期舊→新（None 殿後）。
    report_id 作為最終 tiebreak，確保分頁切片穩定、不跨頁重複。
    """
    groups: dict[str, RankedReport] = {}
    for tier, fused, row in scored:
        rid = row[1]
        g = groups.get(rid)
        if g is None:
            g = RankedReport(
                report_id=rid,
                tier=tier,
                best_score=fused,
                report_date=row[6],
                meta_row=row,
            )
            groups[rid] = g
        g.match_count += 1
        g.passages.append((fused, row))

    reports = list(groups.values())
    if sort == "date_desc":
        reports.sort(
            key=lambda g: (_date_ordinal(g.report_date), g.best_score, g.report_id),
            reverse=True,
        )
    elif sort == "date_asc":
        reports.sort(
            key=lambda g: (
                g.report_date is None,  # False(0) 在前、None(True=1) 殿後
                _date_ordinal(g.report_date),
                -g.best_score,
                g.report_id,
            )
        )
    else:  # relevance：相關度分層內最新優先
        reports.sort(
            key=lambda g: (
                g.tier,
                int(g.best_score / BAND_WIDTH + 1e-9),  # +eps 避開浮點邊界誤判
                _date_ordinal(g.report_date),
                g.best_score,
                g.report_id,
            ),
            reverse=True,
        )
    return reports
```

> 注意：`from dataclasses import ...` 放檔案最上方的 import 區（與既有 import 並列），不要留在函式中段。

- [ ] **Step 4: 跑測試確認通過**

Run: `uv run python -m pytest tests/test_retrieval_rank.py -q`
Expected: PASS（8 passed）

- [ ] **Step 5: 提交**

```bash
git add app/services/retrieval.py tests/test_retrieval_rank.py
git status --short   # 確認只有這兩檔
git commit -m "$(cat <<'EOF'
feat(search): 新增 rank_reports 報告層級排序（相關度分層內最新優先）

分組成報告後依 (tier, band=0.05, 日期, fused) 排序；date_desc/date_asc 對
全召回集合排序。純函式、可單元測試，尚未接上端點。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: lexical 每報告最佳 chunk（選用 `per_report`）+ 召回 SQL 組裝器

**Files:**
- Modify: `app/services/store.py`（抽出 `_lexical_sql`；`search_chunks_lexical` 加 `per_report`）
- Test: `tests/test_store_sql.py`（新）

**Interfaces:**
- Produces:
  - `_lexical_sql(num_patterns: int, extra_conds: list[str], per_report: bool) -> str`
  - `search_chunks_lexical(..., *, per_report: bool = False, market=None, ...)`（新增 keyword-only `per_report`，預設 `False`＝現況）

- [ ] **Step 1: 寫失敗測試**

建立 `tests/test_store_sql.py`：

```python
# tests/test_store_sql.py
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services.store import _lexical_sql  # noqa: E402


class LexicalSqlTests(unittest.TestCase):
    def test_default_has_no_distinct_on(self):
        sql = _lexical_sql(2, ["r.market = :market"], per_report=False)
        self.assertNotIn("DISTINCT ON", sql)
        self.assertIn("c.content_norm LIKE :t0", sql)
        self.assertIn("c.content_norm LIKE :t1", sql)
        self.assertIn("r.market = :market", sql)
        self.assertIn("LIMIT :cap", sql)
        self.assertIn("LIMIT :limit", sql)

    def test_per_report_uses_distinct_on(self):
        sql = _lexical_sql(1, [], per_report=True)
        self.assertIn("DISTINCT ON (c.report_id)", sql)
        # DISTINCT ON 需以 report_id 起首排序，再依向量距離取最近 chunk
        self.assertIn("ORDER BY c.report_id", sql)

    def test_no_extra_conds_still_has_pattern_cond(self):
        sql = _lexical_sql(1, [], per_report=False)
        self.assertIn("c.content_norm LIKE :t0", sql)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run python -m pytest tests/test_store_sql.py -q`
Expected: FAIL — `ImportError: cannot import name '_lexical_sql'`

- [ ] **Step 3: 實作最小程式碼**

在 `app/services/store.py` 中，於 `search_chunks_lexical` 之前新增組裝器：

```python
def _lexical_sql(num_patterns: int, extra_conds: list[str], per_report: bool) -> str:
    """組 lexical 召回 SQL。per_report=True 時每報告只取最近距離 chunk（DISTINCT ON）。

    per_report=False 時結構與原查詢完全一致（問答路徑沿用，不可變更語意）。
    """
    conds = [f"c.content_norm LIKE :t{i}" for i in range(num_patterns)] + extra_conds
    where = " AND ".join(conds)
    if per_report:
        cte_select = (
            "SELECT DISTINCT ON (c.report_id) "
            "c.id, c.report_id, c.chunk_index, c.content, c.embedding"
        )
        cte_order = "ORDER BY c.report_id, c.embedding <=> CAST(:q AS vector)"
    else:
        cte_select = "SELECT c.id, c.report_id, c.chunk_index, c.content, c.embedding"
        cte_order = ""
    return f"""
        WITH lex AS MATERIALIZED (
            {cte_select}
            FROM research.report_chunk c
            JOIN research.research_report r ON r.id = c.report_id
            WHERE {where}
            {cte_order}
            LIMIT :cap
        )
        SELECT {_meta_columns("l")},
               l.embedding <=> CAST(:q AS vector) AS distance
        FROM lex l
        JOIN research.research_report r ON r.id = l.report_id
        ORDER BY distance
        LIMIT :limit
    """
```

然後把 `search_chunks_lexical` 改成使用組裝器，並加 `per_report` 參數。將原本的：

```python
async def search_chunks_lexical(
    session: AsyncSession,
    query_embedding: list[float],
    term_patterns: list[str],
    limit: int = 200,
    cap: int = 2000,
    *,
    market: Optional[str] = None,
    instrument_type: Optional[str] = None,
    relates_stock: Optional[bool] = None,
    relates_futures: Optional[bool] = None,
    report_type: Optional[str] = None,
):
```

改為（新增 `per_report`）：

```python
async def search_chunks_lexical(
    session: AsyncSession,
    query_embedding: list[float],
    term_patterns: list[str],
    limit: int = 200,
    cap: int = 2000,
    *,
    per_report: bool = False,
    market: Optional[str] = None,
    instrument_type: Optional[str] = None,
    relates_stock: Optional[bool] = None,
    relates_futures: Optional[bool] = None,
    report_type: Optional[str] = None,
):
```

並把函式體內「組 conds + 內嵌 sql 字串」的段落（從 `conds = [...]` 到 `sql = f"""..."""`）替換為：

```python
    if not term_patterns:
        return []
    params: dict = {"q": _vec_literal(query_embedding), "limit": limit, "cap": cap}
    for i, pat in enumerate(term_patterns):
        params[f"t{i}"] = pat
    extra = _meta_filters(
        params, market, instrument_type, relates_stock, relates_futures, report_type
    )
    sql = _lexical_sql(len(term_patterns), extra, per_report)
    rows = await session.execute(text(sql), params)
    return rows.all()
```

> `if not term_patterns: return []` 維持在函式最前（原本就有）；上面整段取代其後的 params/conds/sql 組裝。確認最終函式只組一次 params 與一次 sql。

- [ ] **Step 4: 跑測試確認通過 + 問答回歸**

Run: `uv run python -m pytest tests/test_store_sql.py tests/test_answer.py -q`
Expected: PASS（store SQL 測試全綠；`test_answer.py` 全綠＝問答路徑未受影響）

- [ ] **Step 5: 提交**

```bash
git add app/services/store.py tests/test_store_sql.py
git status --short
git commit -m "$(cat <<'EOF'
feat(search): lexical 召回加 per_report 選項（每報告取最佳 chunk）

抽出 _lexical_sql 組裝器；per_report=True 以 DISTINCT ON 每篇只回最近 chunk，
讓精確命中報告幾乎全數入列。預設 False＝現況，問答路徑零變動。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: `hybrid_search` 選用召回參數 + `/api/search` 分頁端點

**Files:**
- Modify: `app/services/retrieval.py`（`hybrid_search` 加選用參數）
- Modify: `web/server.py`（`SearchResponse` 加 `total`；`/api/search` 改寫）

**Interfaces:**
- Consumes: `rank_reports`、`RankedReport`、`DENSE_SCAN_SEARCH`、`LEX_LIMIT_SEARCH`、`LEX_CAP_SEARCH`（Task 1）；`search_chunks_lexical(per_report=...)`（Task 2）。
- Produces:
  - `hybrid_search(..., *, k=10, ..., dense_scan: int | None = None, lex_limit: int | None = None, lex_cap: int | None = None, lex_per_report: bool = False)`（皆預設 None/False＝現況）
  - `SearchResponse.total: int`
  - `GET /api/search?q=&limit=&offset=&passages=&sort=&<filters>` → `{query, market, total, results}`

- [ ] **Step 1: 改 `hybrid_search` 加選用參數**

在 `app/services/retrieval.py` 的 `hybrid_search`：

把簽名中的關鍵字參數區（`k`）改為帶預設，並在 filters 之後加四個選用參數：

```python
async def hybrid_search(
    session: AsyncSession,
    q: str,
    query_embedding: list[float],
    *,
    k: int = 10,
    market: Optional[str] = None,
    instrument_type: Optional[str] = None,
    relates_stock: Optional[bool] = None,
    relates_futures: Optional[bool] = None,
    report_type: Optional[str] = None,
    dense_scan: Optional[int] = None,
    lex_limit: Optional[int] = None,
    lex_cap: Optional[int] = None,
    lex_per_report: bool = False,
) -> list[tuple[int, float, tuple]]:
```

把函式體的 dense/lexical 召回兩段改為（其餘不變）：

```python
    scan = dense_scan if dense_scan is not None else max(DENSE_SCAN_MIN, k * 8)
    dense_rows = await search_chunks_meta(session, query_embedding, scan=scan, **filters)
    lex_rows = []
    if terms:
        patterns = ["%" + t.translate(_LIKE_ESC) + "%" for t in terms]
        lex_rows = await search_chunks_lexical(
            session,
            query_embedding,
            patterns,
            limit=lex_limit if lex_limit is not None else LEX_LIMIT,
            cap=lex_cap if lex_cap is not None else LEX_CAP,
            per_report=lex_per_report,
            **filters,
        )
```

> 預設路徑（問答）不傳新參數 → `scan=max(120, k*8)`、`limit=200`、`cap=2000`、`per_report=False`，與現況完全一致。

- [ ] **Step 2: 跑問答回歸確認 hybrid_search 未變**

Run: `uv run python -m pytest tests/test_answer.py -q`
Expected: PASS（簽名改動向後相容，問答路徑全綠）

- [ ] **Step 3: 改 `SearchResponse` 加 `total`**

在 `web/server.py` 的 `SearchResponse`：

```python
class SearchResponse(BaseModel):
    query: str
    market: str | None
    total: int
    results: list[ReportResult]
```

- [ ] **Step 4: 改 import**

在 `web/server.py` 既有 `from app.services.retrieval import hybrid_search` 改為：

```python
from app.services.retrieval import (  # noqa: E402
    DENSE_SCAN_SEARCH,
    LEX_CAP_SEARCH,
    LEX_LIMIT_SEARCH,
    hybrid_search,
    rank_reports,
)
```

- [ ] **Step 5: 改寫 `/api/search` 端點**

把整個 `@app.get("/api/search", ...)` 函式（含簽名與函式體）替換為：

```python
@app.get("/api/search", response_model=SearchResponse)
async def search(
    q: str = Query(..., min_length=1),
    market: str | None = Query(None),
    instrument_type: str | None = Query(None),
    relates_stock: bool | None = Query(None),
    relates_futures: bool | None = Query(None),
    report_type: str | None = Query(None),
    sort: str = Query("relevance"),  # relevance | date_desc | date_asc
    limit: int = Query(50, ge=1, le=100),  # 回傳的「報告」頁大小
    offset: int = Query(0, ge=0),
    passages: int = Query(3, ge=1, le=6),  # 每篇保留的命中片段數
):
    mkt = market if market and market != "全部" else None
    instr = instrument_type if instrument_type and instrument_type != "全部" else None
    rtype = report_type if report_type and report_type != "全部" else None
    qvec = await asyncio.to_thread(embed_query_cached, q)
    async with SessionFactory() as session:
        scored = await hybrid_search(
            session,
            q,
            qvec,
            market=mkt,
            instrument_type=instr,
            relates_stock=relates_stock or None,
            relates_futures=relates_futures or None,
            report_type=rtype,
            dense_scan=DENSE_SCAN_SEARCH,
            lex_limit=LEX_LIMIT_SEARCH,
            lex_cap=LEX_CAP_SEARCH,
            lex_per_report=True,
        )

    # 分組成「全部」召回報告 → 依 sort 排序 → 取 total → 切當頁
    ranked = rank_reports(scored, sort=sort)
    total = len(ranked)
    page = ranked[offset : offset + limit]

    results: list[ReportResult] = []
    for i, g in enumerate(page, start=offset + 1):  # 全域 rank，跨頁不重號
        (
            _chunk_id, rid, fn, m, src, summary, rdate, rtype_, itypes, rstock, rfut,
            stargets, ftargets, _cidx, _content, _dist,
        ) = g.meta_row
        ps: list[Passage] = []
        for sc, prow in g.passages[:passages]:
            cleaned = clean_text(prow[-2])  # content = row[-2]
            if cleaned:
                ps.append(
                    Passage(score=sc, chunk_index=int(prow[-3]), content=cleaned)
                )  # chunk_index = row[-3]
        results.append(
            ReportResult(
                rank=i,
                report_id=rid,
                file_name=fn,
                market=m,
                source=source_display(src),
                summary=summary,
                report_date=rdate.isoformat() if rdate else None,
                report_type=rtype_,
                instrument_types=list(itypes) if itypes else None,
                relates_stock=rstock,
                relates_futures=rfut,
                stock_targets=list(stargets) if stargets else None,
                futures_targets=list(ftargets) if ftargets else None,
                best_score=g.best_score,
                match_count=g.match_count,
                passages=ps,
            )
        )
    return SearchResponse(query=q, market=mkt, total=total, results=results)
```

> row 位置：`content = row[-2]`、`distance = row[-1]`、`chunk_index = row[-3]`（見 `store._meta_columns`：…, chunk_index(13), content(14), distance(15)）。

- [ ] **Step 6: 靜態檢查 + 全後端測試**

Run:
```bash
uv run ruff check app/services/retrieval.py web/server.py
uv run python -m pytest tests/ -q
```
Expected: ruff 無錯；pytest 全綠（既有測試 + Task 1/2 新測試）。

- [ ] **Step 7: 提交**

```bash
git add app/services/retrieval.py web/server.py
git status --short
git commit -m "$(cat <<'EOF'
feat(search): /api/search 改分頁（limit/offset/total）並接上 rank_reports

hybrid_search 加選用深召回參數（dense_scan/lex_*，預設＝現況）；檢索端點以
DENSE_SCAN_SEARCH + lex per_report 深召回，分組排序後回 total 與當頁切片，
rank 改全域編號。問答路徑不受影響。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: 前端搜尋分頁（`run(append)` + load-more 接線）

**Files:**
- Modify: `web/static/app/state.js`（加 `SEARCH_PAGE`）
- Modify: `web/static/app/api.js`（`run(append=false)`）
- Modify: `web/static/app/render.js`（`render()` 文案、`appendLoadMore()`、load-more 分派）

**Interfaces:**
- Consumes: `/api/search` 回應 `{query, market, total, results}`（Task 3）；既有 `state.{rows,total,offset,mode,searchReq}`、`render.js` 的 `paintResults/restoreLoadMore`（api.js 已 import）。
- Produces: `run(append = false)`（append=true 為載入更多）；`appendLoadMore` 於 search 模式亦顯示。

- [ ] **Step 1: `state.js` 新增頁大小常數**

在 `web/static/app/state.js` 既有 `export const BROWSE_PAGE = 50;` 之後新增：

```javascript
// ── 搜尋模式：每頁報告數（與瀏覽分頁同步、各自獨立）──
export const SEARCH_PAGE = 50;
```

- [ ] **Step 2: 改 `api.js` 的 `run`**

`web/static/app/api.js` 頂部 import 補上 `SEARCH_PAGE`：

```javascript
import { state, BROWSE_PAGE, SEARCH_PAGE } from "/static/app/state.js";
```

把整個 `export async function run() { ... }` 替換為：

```javascript
export async function run(append = false) {
  const q = $("#q").value.trim();
  if (!q) return;
  state.lastQuery = q;
  const my = ++state.searchReq;
  if (!append) { state.offset = 0; skeleton(); buildSortChips("search"); syncURL(); }
  try {
    const off = append ? state.offset : 0;
    let url = `/api/search?q=${encodeURIComponent(q)}&limit=${SEARCH_PAGE}&offset=${off}&passages=4`;
    if (state.market !== "全部") url += `&market=${encodeURIComponent(state.market)}`;
    if (state.instrument !== "全部") url += `&instrument_type=${encodeURIComponent(state.instrument)}`;
    if (state.relStock) url += "&relates_stock=true";
    if (state.relFutures) url += "&relates_futures=true";
    if (state.type !== "全部") url += `&report_type=${encodeURIComponent(state.type)}`;
    url += `&sort=${state.sort}`;
    const data = await fetchJSON(url);
    // 最新的 search 才套用；若查詢已被清空，代表意圖切回瀏覽 → 放棄這次結果
    if (my !== state.searchReq || !$("#q").value.trim()) { if (append) restoreLoadMore(); return; }
    if (append) {
      state.rows = state.rows.concat(data.results || []);
      state.total = data.total || 0;
      state.offset = state.rows.length;
      paintResults(false);   // 不重播進場動畫
    } else {
      render(data);          // 首頁：render 內設定 rows/total/offset/meta 並繪製
    }
  } catch (e) {
    if (my !== state.searchReq || !$("#q").value.trim()) return;
    if (append) { restoreLoadMore("載入更多（載入失敗，點擊重試）"); return; }
    $("#results").className = "";
    $("#results").removeAttribute("aria-busy");
    $("#results").innerHTML = html`<div class="state"><div class="big" aria-hidden="true">⚠️</div>
      <div class="msg">查詢逾時或失敗，請稍後再試</div>
      <div class="examples"><button class="ex" id="retrySearch" type="button">重試</button></div></div>`;
    const rb = $("#retrySearch"); if (rb) rb.onclick = () => run();
  }
}
```

> `api.js` 已 import `html`（來自 utils.js），重試訊息沿用。確認頂部 import 仍含 `skeleton, render, paintResults, restoreLoadMore`。

- [ ] **Step 3: 改 `render.js` 的 `render()` 文案與 rows/total/offset**

`web/static/app/render.js` 的 `render(data)` 開頭，把：

```javascript
  state.rows = data.results || [];
  state.mode = "search";
  state.terms = buildTerms(data.query);
  state.tableSort = { key: null, dir: "asc" };
  $("#meta").classList.add("show");
  const mkt = data.market ? " · " + mLabel(data.market) : "";
  const capped = state.rows.length >= 12;   // k=12，達上限代表只取最相關的前幾篇
  $("#meta").textContent = `「${data.query}」${mkt} — 最相關的 ${state.rows.length} 篇研報`
    + (capped ? "（已達顯示上限，可加關鍵字縮小範圍）" : "");
  if (!state.rows.length) {
```

替換為：

```javascript
  state.rows = data.results || [];
  state.mode = "search";
  state.terms = buildTerms(data.query);
  state.tableSort = { key: null, dir: "asc" };
  state.total = data.total || 0;
  state.offset = state.rows.length;
  $("#meta").classList.add("show");
  const mkt = data.market ? " · " + mLabel(data.market) : "";
  $("#meta").textContent = `「${data.query}」${mkt} — 找到 ${state.total} 篇研報`;
  if (!state.total) {
```

> 其餘空狀態分支（`#resultsBar`、`emptyClear`、`emptyBrowse`）與結尾 `paintResults(true)` 保持不變。

- [ ] **Step 4: 改 `appendLoadMore()` 解除僅 browse 限制**

`web/static/app/render.js` 的 `appendLoadMore`，把第一行：

```javascript
  if (state.mode !== "browse" || state.offset >= state.total) return;
```

改為：

```javascript
  if (state.offset >= state.total) return;   // search/browse 皆可載入更多
```

- [ ] **Step 5: 改 load-more 點擊依 mode 分派**

`web/static/app/render.js` 的 `bindResultEvents()` 內，把：

```javascript
  const lm = $("#loadMore");
  if (lm) lm.onclick = e => {
    e.target.disabled = true;
    e.target.textContent = "載入中…";   // 防連點重複請求 + 即時回饋
    loadBrowse(true);
  };
```

改為：

```javascript
  const lm = $("#loadMore");
  if (lm) lm.onclick = e => {
    e.target.disabled = true;
    e.target.textContent = "載入中…";   // 防連點重複請求 + 即時回饋
    state.mode === "search" ? run(true) : loadBrowse(true);
  };
```

> `render.js` 已 `import { run, loadBrowse } from "/static/app/api.js";`，無需新增 import。

- [ ] **Step 6: 前端語法自檢**

Run: `node --check web/static/app/api.js && node --check web/static/app/render.js && node --check web/static/app/state.js`
Expected: 無輸出（語法正確）

- [ ] **Step 7: 提交**

```bash
git add web/static/app/state.js web/static/app/api.js web/static/app/render.js
git status --short
git commit -m "$(cat <<'EOF'
feat(search): 檢索結果支援載入更多分頁

run(append) 支援逐批追加；appendLoadMore 解除僅 browse 限制；load-more
依 state.mode 分派 search/browse；首屏文案改「找到 N 篇」。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: 端到端驗證（Playwright，需登入）

**Files:** 無（驗證用，腳本寫到 `$CLAUDE_JOB_DIR/tmp` 或 `/tmp`，不入庫）

**前置：** 確認服務在跑（本機 `make up-server` 或 LAN `:8097`）。`/static` 與 `/api` 在登入後才可用，須先登入（共用帳密見 env / login 頁）。

- [ ] **Step 1: 啟服務（若未啟）**

Run: 依專案慣例啟動 web（如 `make serve` 或既有 setsid nohup uvicorn）。確認 `GET /api/stats` 通。

- [ ] **Step 2: 跑驗證腳本**

用 playwright-skill 或 MCP 瀏覽器，腳本流程：
1. 開 `/login` → 以共用帳密登入 → 應導向首頁。
2. 進檢索頁，於 `#q` 輸入熱門關鍵字（如「台積電」），Enter。
3. 斷言 `#meta` 顯示「找到 N 篇研報」，且 N 明顯 > 12（驗證上限已解除）。
4. 斷言結果區底部出現「載入更多（還有 …）」鈕（`#loadMore`）。
5. 點「載入更多」→ 斷言結果列數增加、`#loadMore` 文案更新或在末頁消失。
6. 切「日期新→舊」排序 chip → 斷言首屏第一筆日期 ≥ 第二筆（全召回日期排序，非只前 12）。
7. console 無 error。

- [ ] **Step 3: 記錄結果**

截圖存證；若任何斷言失敗，回報並進系統化除錯（`superpowers:systematic-debugging`），勿宣稱完成。

---

## Self-Review（計畫對照 spec）

- **排序語意（spec §1）** → Task 1 `rank_reports`（tier→band0.05→date→fused；三模式語意）。✅
- **召回擴大（spec §2）** → Task 2（lexical per_report DISTINCT ON）+ Task 3（dense_scan=600、lex_limit/cap 拉高，選用參數不動問答）。✅
- **API 分頁（spec §3）** → Task 3（limit/offset/total、切片、全域 rank、SearchResponse.total）。✅
- **前端（spec §4）** → Task 4（run(append)、appendLoadMore 解除、load-more 分派、文案）。✅
- **不變項（spec §5）** → Global Constraints + Task 2/3 選用參數預設＝現況 + test_answer.py 回歸閘門。✅
- **測試計畫（spec）** → Task 1/2 單元測試 + Task 3 全後端回歸 + Task 5 Playwright 端到端。✅
- **Placeholder scan**：無 TBD/TODO；每段含實際程式碼與指令。✅
- **Type 一致**：`rank_reports`/`RankedReport`/`_lexical_sql`/`hybrid_search` 參數與 `SearchResponse.total` 跨任務命名一致；row 位置 `[-2]/[-3]/[1]/[6]` 一致。✅
