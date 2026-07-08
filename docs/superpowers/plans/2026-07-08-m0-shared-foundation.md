# M0 共用地基 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 為研報/問答改善（M1–M9）立地基：型別化檢索 row、抽共用 `retrieve_context`、抽 `SentinelStreamParser`、集中設定、把選篇政策從 `build_context` 分離——全程對外行為零改變。

**Architecture:** 純重構。`store.py` 把裸 `Row` 包成 `ChunkRow`（NamedTuple，tuple 子型故位移相容）；下游改具名存取；`retrieval_pipeline.retrieve_context` 收斂 `embed→hybrid_search→build_context`（僅問答與研報兩路採用）；`build_context` 的選篇政策抽成純函式 `select_reports`；`ASK_*`/`REPORT_*` 收進 `app/config.py`。

**Tech Stack:** Python 3.11、FastAPI、SQLAlchemy async(`text()`)、pgvector、unittest-class 跑於 pytest（`uv run pytest`）。

## Global Constraints

- 繁體中文回覆；程式碼/識別字/路徑保留原文；**不加裝飾 emoji**。
- **核心不變式：M0 對外行為零改變**——同輸入必產同輸出（sources、脈絡字串、串流內容、SSE 事件、`qa_log`/`report_doc` 落地逐字節不變）。
- **`ChunkRow` 用 `NamedTuple`（非 dataclass）**：它是 tuple 子型，任何漏改的位移存取仍可運作＝零回歸。
- **範圍**：`retrieve_context` 只服務 `answer.py`（問答）與 `report.py`（研報）；檢索頁 `rank_reports` 路徑不改語意。`app/config.py` 只收 `os.getenv` 驅動的 `ASK_*`/`REPORT_*`；`retrieval.py:117` `BAND_WIDTH=0.05` 等非-env 字面量不動（band 收斂是 M6）。
- **不動**：`db/schema.sql`、前端、檢索頁分頁排序語意。
- 測試：`uv run pytest <path> -v`。分支 `refactor/m0-shared-foundation`（已建，已提交藍圖+backlog+spec）。stage 明確路徑（勿 `git add -A`，工作樹有他人 WIP），提交前 `git diff --staged --stat` 驗範圍。commit 訊息 Conventional Commits＋繁中 scope，結尾 `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`。
- **`ChunkRow` 16 欄逐一對齊 `store._meta_columns()`（15 欄）＋ 尾端 `distance`**：`chunk_id, report_id, file_name, market, source, summary, report_date, report_type, instrument_types, relates_stock, relates_futures, stock_targets, futures_targets, chunk_index, content, distance`。

---

### Task 1: `ChunkRow` 型別 + 在 `store.py` 邊界包裝（行為中性）

引入型別、把 store 的兩個檢索函式回傳包成 `ChunkRow`。因 `ChunkRow` 是 tuple 子型，**所有既有位移存取（`row[0]`/`row[-1]`/`row[_RID]`）繼續運作**，故本 task 不改任何消費端，全套測試須維持全綠。

**Files:**
- Create: `app/services/rows.py`
- Modify: `app/services/store.py`（`search_chunks_meta` 回傳 `store.py:279`、`search_chunks_lexical` 回傳 `store.py:371`）
- Test: `tests/test_rows.py`（新建）

**Interfaces:**
- Produces: `class ChunkRow(NamedTuple)`，16 欄（見 Global Constraints）。tuple 子型，支援位移與具名存取。

- [ ] **Step 1: 寫失敗測試**

建立 `tests/test_rows.py`：

```python
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services.rows import ChunkRow  # noqa: E402


class ChunkRowTests(unittest.TestCase):
    _VALS = (
        "c1", "r1", "f.pdf", "TW", "元大", "摘要",
        "2026-06-20", "個股", ["equity"], True, False, ["2330"], [],
        3, "本文內容", 0.12,
    )

    def test_named_and_positional_access_agree(self):
        row = ChunkRow._make(self._VALS)
        # 具名
        self.assertEqual(row.chunk_id, "c1")
        self.assertEqual(row.report_id, "r1")
        self.assertEqual(row.file_name, "f.pdf")
        self.assertEqual(row.market, "TW")
        self.assertEqual(row.report_date, "2026-06-20")
        self.assertEqual(row.content, "本文內容")
        self.assertEqual(row.distance, 0.12)
        # 位移（tuple 相容，向後相容既有消費端）
        self.assertEqual(row[0], "c1")      # chunk_id
        self.assertEqual(row[1], "r1")      # report_id
        self.assertEqual(row[6], "2026-06-20")  # report_date
        self.assertEqual(row[-1], 0.12)     # distance
        self.assertEqual(row[-2], "本文內容")  # content
        self.assertEqual(row[-3], 3)        # chunk_index
        self.assertEqual(len(row), 16)
        self.assertIsInstance(row, tuple)

    def test_from_sequence_preserves_order(self):
        # 模擬 SQLAlchemy Row（可迭代、依欄序）→ _make
        row = ChunkRow._make(list(self._VALS))
        self.assertEqual(tuple(row), self._VALS)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run pytest tests/test_rows.py -v`
Expected: FAIL（`app.services.rows` 不存在 → ImportError）

- [ ] **Step 3: 建立 `app/services/rows.py`**

```python
"""檢索結果的型別化 row。

`store.search_chunks_meta` / `search_chunks_lexical` 的 SELECT 欄位（見
`store._meta_columns`，15 欄）＋ 尾端 distance ＝ 16 欄。ChunkRow 為 NamedTuple
（tuple 子型），故既有位移存取（row[0]/row[-1]/row[-2] 等）與新的具名存取並存，
遷移期零回歸。
"""

from typing import NamedTuple


class ChunkRow(NamedTuple):
    chunk_id: str
    report_id: str
    file_name: str | None
    market: str | None
    source: str | None
    summary: str | None
    report_date: object  # date / datetime / str / None（沿用現況多型）
    report_type: str | None
    instrument_types: object
    relates_stock: object
    relates_futures: object
    stock_targets: object
    futures_targets: object
    chunk_index: int
    content: str
    distance: float
```

- [ ] **Step 4: 在 `store.py` 邊界包裝回傳**

`app/services/store.py` 頂部 import 區加：

```python
from app.services.rows import ChunkRow
```

把 `search_chunks_meta` 結尾（store.py:279）：

```python
    return rows.all()
```

改為：

```python
    return [ChunkRow._make(r) for r in rows.all()]
```

把 `search_chunks_lexical` 結尾（store.py:371）：

```python
    rows = await session.execute(text(sql), params)
    return rows.all()
```

改為：

```python
    rows = await session.execute(text(sql), params)
    return [ChunkRow._make(r) for r in rows.all()]
```

（`search_chunks_lexical` 的 `if not term_patterns: return []` 早退不變——空清單無需包裝。）

- [ ] **Step 5: 跑測試確認通過（含全套回歸，證明位移相容）**

Run: `uv run pytest tests/test_rows.py -v`
Expected: PASS

Run: `uv run pytest -q`
Expected: 全綠（消費端仍用位移存取、因 tuple 相容而不受影響）

- [ ] **Step 6: Commit**

```bash
git add app/services/rows.py app/services/store.py tests/test_rows.py
git commit -m "$(cat <<'EOF'
refactor(檢索): 引入 ChunkRow 型別並在 store 邊界包裝

store.search_chunks_meta/_lexical 回傳從裸 Row 包成 ChunkRow（NamedTuple、
16 欄對齊 _meta_columns＋distance）。tuple 子型故既有位移存取全相容，本次
不改消費端、行為零變化，為下一步具名遷移鋪路。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: 消費端改具名存取、刪除位移常數

把所有位移存取改為 `ChunkRow` 具名欄位。純機械替換、行為零變化。

**Files:**
- Modify: `app/services/answer.py`（刪 `:132` 常數；`build_context`@236-246）
- Modify: `app/services/retrieval.py`（`hybrid_search`@95-100；`rank_reports`@153,160）
- Modify: `web/server.py`（`meta_row` 拆解@598-601；passage rows@604,607）
- Test: 無新增（靠既有全套回歸；Task 3 再加 build_context parity）

**Interfaces:**
- Consumes: `ChunkRow`（Task 1）。

- [ ] **Step 1: 改 `retrieval.py::hybrid_search`（95-100）**

```python
        chunk_id = row[0]
        ...
        dense_sim = 1.0 - float(row[-1])
        nc = norm_for_match(row[-2])  # content
```

改為：

```python
        chunk_id = row.chunk_id
        ...
        dense_sim = 1.0 - float(row.distance)
        nc = norm_for_match(row.content)
```

- [ ] **Step 2: 改 `retrieval.py::rank_reports`（153, 160）**

```python
        rid = row[1]
        ...
                report_date=row[6],
```

改為：

```python
        rid = row.report_id
        ...
                report_date=row.report_date,
```

- [ ] **Step 3: 改 `web/server.py` `meta_row` 拆解（598-601）**

```python
        (
            _chunk_id, rid, fn, m, src, summary, rdate, rtype_, itypes, rstock, rfut,
            stargets, ftargets, _cidx, _content, _dist,
        ) = g.meta_row
```

改為：

```python
        mr = g.meta_row
        rid, fn, m, src, summary, rdate = (
            mr.report_id, mr.file_name, mr.market, mr.source, mr.summary, mr.report_date
        )
```

（`rtype_, itypes, rstock, rfut, stargets, ftargets, _chunk_id, _cidx, _content, _dist` 在 598-601 之後若未被使用則無需保留；若下方有引用，改為 `mr.<欄位>`。實作時 grep 該函式體確認：本區塊後續僅用到 `rid, fn, m, src, summary, rdate`。）

- [ ] **Step 4: 改 `web/server.py` passage rows（604, 607）**

```python
        for sc, prow in g.passages[:passages]:
            cleaned = clean_text(prow[-2])  # content = row[-2]
            if cleaned:
                ps.append(
                    Passage(score=sc, chunk_index=int(prow[-3]), content=cleaned)
                )  # chunk_index = row[-3]
```

改為：

```python
        for sc, prow in g.passages[:passages]:
            cleaned = clean_text(prow.content)
            if cleaned:
                ps.append(
                    Passage(score=sc, chunk_index=int(prow.chunk_index), content=cleaned)
                )
```

- [ ] **Step 5: 改 `answer.py::build_context`（236-246）並刪常數（132）**

刪除 `answer.py:131-132`：

```python
# hybrid_search 回傳 row 的欄位位置（見 store._meta_columns + distance；server.py:473 對應解包）
_RID, _FNAME, _MARKET, _RDATE, _CONTENT = 1, 2, 3, 6, 14
```

`build_context` 內（236-246）：

```python
        rid = row[_RID]
        content = clean_text(row[_CONTENT])
        ...
                "file_name": row[_FNAME],
                "market": row[_MARKET],
                "report_date": row[_RDATE],
```

改為：

```python
        rid = row.report_id
        content = clean_text(row.content)
        ...
                "file_name": row.file_name,
                "market": row.market,
                "report_date": row.report_date,
```

- [ ] **Step 6: grep 確認無殘留位移慣例**

Run: `rg -n "_RID|row\[_|meta_row\[|row\[-1\]|row\[-2\]|prow\[-" app/ web/`
Expected: 無輸出（或僅註解）。若 `retrieval.py::hybrid_search` 內 `row` 已全具名、`rank_reports` 已具名，且 server.py 兩處已改，則清空。

- [ ] **Step 7: 跑全套回歸**

Run: `uv run pytest -q`
Expected: 全綠（行為零變化）

- [ ] **Step 8: Commit**

```bash
git add app/services/answer.py app/services/retrieval.py web/server.py
git commit -m "$(cat <<'EOF'
refactor(檢索): 消費端改 ChunkRow 具名存取、刪位移常數

answer.build_context、retrieval.hybrid_search/rank_reports、web/server.py 的
meta_row 與 passage row 全改具名欄位；刪除 answer.py 的 _RID/_FNAME… 位移常數。
消滅 schema 增量演進下的位移錯位風險，行為零變化。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: 從 `build_context` 抽出 `select_reports` 純函式（含 parity golden 測試）

把「選篇政策」抽成純函式；`build_context` 只留格式化。先寫 golden master 鎖定 `build_context` 現有輸出，重構後須逐字不變。

**Files:**
- Modify: `app/services/answer.py`（`build_context`@210-345，抽出 `select_reports`）
- Test: `tests/test_select_reports.py`（新建）

**Interfaces:**
- Produces: `@dataclass SelectedReport{report_id: str, file_name, market, report_date, passages: list[str]}`；`select_reports(scored, *, max_reports, max_passages, max_chars, now, half_life_days, min_reports, relevance_floor, stale_age_days, max_stale) -> list[SelectedReport]`（已排序、已裁切、已定 kept passages、純函式）。
- Consumes: `ChunkRow`、既有 `_relevance_band`/`_recency_factor`/`_as_date`。

- [ ] **Step 1: 寫 golden master 測試（先鎖現況）**

建立 `tests/test_select_reports.py`。先寫「用固定 fixture 呼叫現有 `build_context`，把實際輸出當作 golden」——執行者於 Step 2 跑一次、把印出的實際 `(sources, context)` 貼進 `EXPECTED_*` 常數，確認對現有程式碼 PASS：

```python
import sys
import unittest
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services.answer import build_context  # noqa: E402
from app.services.rows import ChunkRow  # noqa: E402


def _row(chunk_id, rid, fn, market, rdate, content, distance):
    return ChunkRow._make((
        chunk_id, rid, fn, market, "src", "sum", rdate, None,
        None, None, None, None, None, 0, content, distance,
    ))


# 固定「今天」讓新近度可重現
_NOW = datetime(2026, 7, 8, tzinfo=timezone.utc)


def _fixture():
    # 跨 tier、跨新近度、含長片段觸字數上限
    return [
        (2, 0.90, _row("c1", "rA", "A.pdf", "TW", "2026-07-01", "A 段一", 0.10)),
        (1, 0.70, _row("c2", "rB", "B.pdf", "US", "2026-01-01", "B 段一", 0.30)),
        (0, 0.65, _row("c3", "rC", "C.pdf", "TW", "2025-01-01", "C 段一（很舊）", 0.35)),
        (2, 0.88, _row("c1b", "rA", "A.pdf", "TW", "2026-07-01", "A 段二", 0.12)),
    ]


class BuildContextGoldenTests(unittest.TestCase):
    def test_golden_output_unchanged(self):
        sources, context = build_context(_fixture(), now=_NOW)
        got = ([asdict(s) for s in sources], context)
        # 執行者 Step 2：把實際 got 貼成 EXPECTED 後改為 assertEqual(got, EXPECTED)
        self.assertEqual(got, EXPECTED)


# 執行者於 Step 2 填入實際輸出（golden master）
EXPECTED = None


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑一次、擷取 golden，鎖定現況**

先臨時把 `test_golden_output_unchanged` 改成 `print(repr(got)); return`，跑：
Run: `uv run pytest tests/test_select_reports.py -v -s`
把印出的 `got` 值貼進 `EXPECTED = (...)`，還原 `assertEqual(got, EXPECTED)`，再跑：
Run: `uv run pytest tests/test_select_reports.py::BuildContextGoldenTests -v`
Expected: PASS（golden 已鎖定現有 `build_context` 行為）

- [ ] **Step 3: 加 `select_reports` 單元測試（先失敗）**

在 `tests/test_select_reports.py` `if __name__` 前加：

```python
class SelectReportsTests(unittest.TestCase):
    def test_returns_ordered_selected_reports(self):
        from app.services.answer import (
            select_reports, MAX_REPORTS, MAX_PASSAGES_PER_REPORT,
            MAX_CONTEXT_CHARS, RECENCY_HALF_LIFE_DAYS, ASK_MIN_REPORTS,
            ASK_RELEVANCE_FLOOR, ASK_STALE_AGE_DAYS, ASK_MAX_STALE_REPORTS,
        )
        sel = select_reports(
            _fixture(),
            max_reports=MAX_REPORTS, max_passages=MAX_PASSAGES_PER_REPORT,
            max_chars=MAX_CONTEXT_CHARS, now=_NOW.date(),
            half_life_days=RECENCY_HALF_LIFE_DAYS, min_reports=ASK_MIN_REPORTS,
            relevance_floor=ASK_RELEVANCE_FLOOR, stale_age_days=ASK_STALE_AGE_DAYS,
            max_stale=ASK_MAX_STALE_REPORTS,
        )
        rids = [s.report_id for s in sel]
        self.assertEqual(rids[0], "rA")                 # 最高 tier/分數在前
        self.assertTrue(all(s.passages for s in sel))   # 皆有 kept passage
        a = next(s for s in sel if s.report_id == "rA")
        self.assertEqual(a.passages, ["A 段一", "A 段二"])  # 同報告多段累積
```

- [ ] **Step 4: 跑測試確認失敗**

Run: `uv run pytest tests/test_select_reports.py::SelectReportsTests -v`
Expected: FAIL（`select_reports` 尚未存在 → ImportError）

- [ ] **Step 5: 抽出 `select_reports`，`build_context` 改呼叫它**

在 `answer.py` 加 `SelectedReport` dataclass（靠近 `build_context`；`from dataclasses import dataclass` 若未 import 則補）：

```python
@dataclass
class SelectedReport:
    report_id: str
    file_name: object
    market: object
    report_date: object
    passages: list  # list[str]，已依字數預算裁切
```

新增 `select_reports`（把現有 `build_context` 的 235-313 邏輯搬入；**邏輯逐行不變**）：

```python
def select_reports(
    scored,
    *,
    max_reports: int,
    max_passages: int,
    max_chars: int,
    now,
    half_life_days: float,
    min_reports: int,
    relevance_floor: float,
    stale_age_days: int,
    max_stale: int,
) -> list["SelectedReport"]:
    """選篇政策（純函式）：聚合→排序→過舊軟截斷→相關度下限→過舊配額→字數預算。

    排序鍵 (best_tier, 相關度 band, 新近度, best_fused, report_id) 由高到低。
    """
    now_date = now
    by_report: dict[str, dict] = {}
    order: list[str] = []
    for tier, fused, row in scored:
        rid = row.report_id
        content = clean_text(row.content)
        if not content:
            continue
        info = by_report.get(rid)
        if info is None:
            info = {
                "passages": [],
                "file_name": row.file_name,
                "market": row.market,
                "report_date": row.report_date,
                "best_tier": tier,
                "best_fused": fused,
            }
            by_report[rid] = info
            order.append(rid)
        else:
            if tier > info["best_tier"]:
                info["best_tier"] = tier
            if fused > info["best_fused"]:
                info["best_fused"] = fused
        if len(info["passages"]) < max_passages:
            info["passages"].append(content)

    reports = [(rid, by_report[rid]) for rid in order if by_report[rid]["passages"]]
    reports.sort(
        key=lambda it: (
            it[1]["best_tier"],
            _relevance_band(it[1]["best_fused"]),
            _recency_factor(it[1]["report_date"], now_date, half_life_days),
            it[1]["best_fused"],
            it[0],
        ),
        reverse=True,
    )

    factors = {
        rid: _recency_factor(info["report_date"], now_date, half_life_days)
        for rid, info in reports
    }
    fresh_count = sum(1 for f in factors.values() if f >= ASK_FRESH_FACTOR)
    cutoff_active = fresh_count >= ASK_MIN_FRESH_BEFORE_CUTOFF

    selected: list[SelectedReport] = []
    total = 0
    n = 0
    stale_used = 0
    for rid, info in reports:
        if n >= max_reports:
            break
        if cutoff_active and factors[rid] < ASK_STALE_FACTOR:
            continue
        rdate_d = _as_date(info["report_date"])
        is_stale = (
            rdate_d is not None and (now_date - rdate_d).days > stale_age_days
        )
        if n >= min_reports:
            if info["best_tier"] < 1 and info["best_fused"] < relevance_floor:
                continue
            if is_stale and stale_used >= max_stale:
                continue
        kept: list[str] = []
        for content in info["passages"]:
            if total + len(content) > max_chars:
                continue
            kept.append(content)
            total += len(content)
        if not kept:
            continue
        n += 1
        if is_stale:
            stale_used += 1
        selected.append(
            SelectedReport(
                report_id=rid,
                file_name=info["file_name"],
                market=info["market"],
                report_date=info["report_date"],
                passages=kept,
            )
        )
    return selected
```

把 `build_context`（210-345）主體改為：呼叫 `select_reports` 取得 `selected`，再只做格式化：

```python
def build_context(
    scored: list[tuple[int, float, tuple]],
    *,
    max_reports: int = MAX_REPORTS,
    max_passages: int = MAX_PASSAGES_PER_REPORT,
    max_chars: int = MAX_CONTEXT_CHARS,
    now: datetime | None = None,
    half_life_days: float = RECENCY_HALF_LIFE_DAYS,
    min_reports: int = ASK_MIN_REPORTS,
    relevance_floor: float = ASK_RELEVANCE_FLOOR,
    stale_age_days: int = ASK_STALE_AGE_DAYS,
    max_stale: int = ASK_MAX_STALE_REPORTS,
) -> tuple[list[Source], str]:
    """把檢索結果整理成『來源清單 + 帶編號的脈絡文字』（選篇政策見 select_reports）。"""
    now_date = (now or datetime.now(timezone.utc)).date()
    selected = select_reports(
        scored,
        max_reports=max_reports, max_passages=max_passages, max_chars=max_chars,
        now=now_date, half_life_days=half_life_days, min_reports=min_reports,
        relevance_floor=relevance_floor, stale_age_days=stale_age_days,
        max_stale=max_stale,
    )

    sources: list[Source] = []
    blocks: list[str] = []
    for i, sr in enumerate(selected, start=1):
        rdate = sr.report_date
        rdate_s = rdate.isoformat() if hasattr(rdate, "isoformat") else (rdate or None)
        sources.append(
            Source(
                n=i,
                report_id=sr.report_id,
                file_name=sr.file_name,
                market=sr.market,
                report_date=rdate_s,
            )
        )
        head = f"[{i}] 報告：{sr.file_name}"
        bits = []
        if sr.market:
            bits.append(f"市場 {sr.market}")
        if rdate_s:
            bits.append(f"日期 {rdate_s}")
        if bits:
            head += "（" + "，".join(bits) + "）"
        blocks.append(head + "\n" + "\n".join(sr.passages))

    latest_n, latest_d = None, None
    for s in sources:
        d = _as_date(s.report_date)
        if d is not None and (latest_d is None or d > latest_d):
            latest_n, latest_d = s.n, d
    if latest_n is not None:
        for s in sources:
            s.is_latest = s.n == latest_n

    return sources, "\n\n".join(blocks)
```

> 注意：原 `build_context` 用 `n`（1-based）作編號；此處 `enumerate(selected, start=1)` 等價。`is_latest` 邏輯與 head 組法逐字沿用。

- [ ] **Step 6: 跑測試確認全綠（golden 未變、單元測試通過）**

Run: `uv run pytest tests/test_select_reports.py -v`
Expected: PASS（`BuildContextGoldenTests` golden 逐字不變＝重構正確；`SelectReportsTests` 通過）

Run: `uv run pytest -q`
Expected: 全綠

- [ ] **Step 7: Commit**

```bash
git add app/services/answer.py tests/test_select_reports.py
git commit -m "$(cat <<'EOF'
refactor(檢索): 從 build_context 抽出 select_reports 選篇政策純函式

聚合/排序/過舊軟截斷/相關度下限/過舊配額/字數預算搬進純函式 select_reports；
build_context 只留 [n] 編號、Source 建構、blocks 組裝與 is_latest 徽章。新增
golden master 測試鎖定 build_context 輸出逐字不變，為 M6 MMR 留插入點。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: `retrieve_context` 共用 helper + 研報路徑採用

**Files:**
- Create: `app/services/retrieval_pipeline.py`
- Modify: `app/services/report.py`（`generate_report`@208-218）
- Test: `tests/test_retrieval_pipeline.py`（新建）

**Interfaces:**
- Produces: `async def retrieve_context(question, *, k, dense_scan, max_reports, max_passages, max_chars, filters=None, now=None, timer=None) -> tuple[list[Source], str]`。內部：`embed_query_cached`（`asyncio.to_thread`）→ `async with SessionFactory(): hybrid_search` → `build_context`。若給 `timer`，在 embed 後 `timer.mark("embed")`、檢索後 `timer.mark("retrieve")`（保留 `qa_timing` 粒度）。
- Consumes: `answer.build_context`、`retrieval.hybrid_search`、`embed.embed_query_cached`、`store.SessionFactory`。

- [ ] **Step 1: 寫失敗測試（以 mock 驗證串接與 timer）**

建立 `tests/test_retrieval_pipeline.py`：

```python
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


class RetrieveContextTests(unittest.IsolatedAsyncioTestCase):
    async def test_wires_embed_search_buildcontext_and_marks_timer(self):
        import app.services.retrieval_pipeline as rp

        calls = []

        class _Session:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False

        class _Timer:
            def mark(self, name): calls.append(("mark", name))

        async def _fake_hybrid(session, q, vec, **kw):
            calls.append(("hybrid", q, kw.get("k"), kw.get("dense_scan")))
            return [("scored")]

        def _fake_build(scored, **kw):
            calls.append(("build", tuple(scored), kw.get("max_reports")))
            return (["S"], "CTX")

        with mock.patch.object(rp, "embed_query_cached", lambda q: [0.1]), \
             mock.patch.object(rp, "SessionFactory", lambda: _Session()), \
             mock.patch.object(rp, "hybrid_search", _fake_hybrid), \
             mock.patch.object(rp, "build_context", _fake_build):
            t = _Timer()
            sources, ctx = await rp.retrieve_context(
                "台積電", k=30, dense_scan=400, max_reports=25,
                max_passages=6, max_chars=40000, filters={"market": "TW"}, timer=t,
            )

        self.assertEqual((sources, ctx), (["S"], "CTX"))
        self.assertIn(("hybrid", "台積電", 30, 400), calls)
        self.assertIn(("build", ("scored",), 25), calls)
        self.assertIn(("mark", "embed"), calls)
        self.assertIn(("mark", "retrieve"), calls)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run pytest tests/test_retrieval_pipeline.py -v`
Expected: FAIL（模組不存在）

- [ ] **Step 3: 建立 `app/services/retrieval_pipeline.py`**

```python
"""問答/研報共用的檢索封裝：embed → hybrid_search → build_context。

僅此 helper 收斂三步序列；檢索頁分頁（rank_reports）不使用。M2 的 reranker
將插在 hybrid_search 之後、build_context 之前的此處。
"""

import asyncio

from app.services.answer import Source, build_context
from app.services.embed import embed_query_cached
from app.services.retrieval import hybrid_search
from app.services.store import SessionFactory


async def retrieve_context(
    question: str,
    *,
    k: int,
    dense_scan: int,
    max_reports: int,
    max_passages: int,
    max_chars: int,
    filters: dict | None = None,
    now=None,
    timer=None,
) -> tuple[list[Source], str]:
    """回 (sources, context)。timer 給定時記 embed/retrieve 兩段耗時（保留 qa_timing）。"""
    filters = filters or {}
    qvec = await asyncio.to_thread(embed_query_cached, question)
    if timer is not None:
        timer.mark("embed")
    async with SessionFactory() as session:  # 短連線：檢索完即釋放
        scored = await hybrid_search(
            session, question, qvec, k=k, dense_scan=dense_scan, **filters
        )
    if timer is not None:
        timer.mark("retrieve")
    return build_context(
        scored,
        max_reports=max_reports,
        max_passages=max_passages,
        max_chars=max_chars,
        now=now,
    )
```

> 循環 import 注意：`retrieval_pipeline` import 自 `answer`（`Source`/`build_context`）。`answer.py` 目前不 import `retrieval_pipeline`；Task 5 讓 `answer.py` import 它時，須確認無循環（`answer` 不可在模組頂層 import `retrieval_pipeline`——見 Task 5 Step 說明，於函式內或維持單向）。

- [ ] **Step 4: 跑測試確認通過**

Run: `uv run pytest tests/test_retrieval_pipeline.py -v`
Expected: PASS

- [ ] **Step 5: `report.py::generate_report` 採用 helper（208-218）**

`report.py` 頂部 import 區，把 `from app.services.retrieval import hybrid_search`（report.py:30）改為（或並存）加入：

```python
from app.services.retrieval_pipeline import retrieve_context
```

把 208-218：

```python
    qvec = await asyncio.to_thread(embed_query_cached, question)
    async with SessionFactory() as session:
        scored = await hybrid_search(
            session, question, qvec, k=REPORT_DEEP_K, dense_scan=ASK_DENSE_SCAN, **filters
        )
    sources, context = build_context(
        scored,
        max_reports=REPORT_MAX_REPORTS,
        max_passages=REPORT_MAX_PASSAGES,
        max_chars=REPORT_MAX_CONTEXT_CHARS,
    )
```

改為：

```python
    sources, context = await retrieve_context(
        question,
        k=REPORT_DEEP_K,
        dense_scan=ASK_DENSE_SCAN,
        max_reports=REPORT_MAX_REPORTS,
        max_passages=REPORT_MAX_PASSAGES,
        max_chars=REPORT_MAX_CONTEXT_CHARS,
        filters=filters,
    )
```

（若 `embed_query_cached`/`hybrid_search`/`build_context`/`SessionFactory` 在 report.py 其他地方仍有用則保留 import，否則移除未使用 import。）

- [ ] **Step 6: 跑回歸**

Run: `uv run pytest tests/test_report.py tests/test_answer_report_wiring.py -q`
Expected: 全綠

Run: `uv run pytest -q`
Expected: 全綠

- [ ] **Step 7: Commit**

```bash
git add app/services/retrieval_pipeline.py app/services/report.py tests/test_retrieval_pipeline.py
git commit -m "$(cat <<'EOF'
refactor(檢索): 抽共用 retrieve_context 並讓研報路徑採用

新增 retrieval_pipeline.retrieve_context（embed→hybrid_search→build_context，
自開短連線、可選 timer 保留耗時粒度）。generate_report 改呼叫它，消除三步序列
重複；此處即 M2 reranker 的插入點。行為零變化。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: 問答路徑採用 `retrieve_context`（保留 intent 併發與 timer）

問答 RAG 路徑的 embed+檢索與 build_context 被 intent 閘門隔開；改用 helper 時須保留：(a) 首輪 `classify_intent` 並發、(b) `timer.mark("embed"/"retrieve")`、(c) 離題短路仍 yield 空 sources。build_context 對離題題會被計算後丟棄（輸出逐字不變、僅極少額外計算）。

**Files:**
- Modify: `app/services/answer.py`（RAG 區塊 764-809）
- Test: 靠既有 `tests/test_answer.py` 全套回歸 + Task 3 golden。

**Interfaces:**
- Consumes: `retrieval_pipeline.retrieve_context`。

- [ ] **Step 1: 於 `answer.py` 函式內 import helper（避免頂層循環 import）**

因 `retrieval_pipeline` 於頂層 import `answer`，`answer` 不可於頂層 import `retrieval_pipeline`。在 `answer_question` 內、RAG 區塊前加**函式內 import**：

```python
    from app.services.retrieval_pipeline import retrieve_context
```

- [ ] **Step 2: 改多輪分支（764-771）**

```python
    if turns:
        qvec = await asyncio.to_thread(embed_query_cached, standalone_query)
        timer.mark("embed")
        async with SessionFactory() as session:  # 短連線：檢索完即釋放
            scored = await hybrid_search(
                session, standalone_query, qvec, k=k, dense_scan=ASK_DENSE_SCAN, **filters
            )
        timer.mark("retrieve")
    else:
```

改為：

```python
    if turns:
        sources, context = await retrieve_context(
            standalone_query, k=k, dense_scan=ASK_DENSE_SCAN,
            max_reports=MAX_REPORTS, max_passages=MAX_PASSAGES_PER_REPORT,
            max_chars=MAX_CONTEXT_CHARS, filters=filters, timer=timer,
        )
    else:
```

- [ ] **Step 3: 改首輪分支（772-786），保留 intent 並發**

```python
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
            timer.mark("intent_wait")
        except BaseException:
            intent_task.cancel()
            raise
```

改為：

```python
    else:
        intent_task = asyncio.create_task(classify_intent(question))
        try:
            sources, context = await retrieve_context(
                question, k=k, dense_scan=ASK_DENSE_SCAN,
                max_reports=MAX_REPORTS, max_passages=MAX_PASSAGES_PER_REPORT,
                max_chars=MAX_CONTEXT_CHARS, filters=filters, timer=timer,
            )
            in_domain = await intent_task
            timer.mark("intent_wait")
        except BaseException:
            intent_task.cancel()
            raise
```

- [ ] **Step 4: 改離題後、原 build_context 呼叫（809）**

原 809：

```python
    sources, context = build_context(scored)
    yield ("sources", [asdict(s) for s in sources])
```

因 `sources, context` 已由 `retrieve_context` 產生（Step 2/3），刪除此處 `build_context(scored)` 呼叫，只留 yield：

```python
    yield ("sources", [asdict(s) for s in sources])
```

（離題分支 788-807 不變：仍 `yield ("sources", [])`、丟棄已算的 `sources`。多輪分支的 `in_domain` 仍來自稍早的 `condense_and_classify`，不受影響。）

- [ ] **Step 5: 檢查殘留未使用 import/變數**

Run: `rg -n "build_context|hybrid_search|embed_query_cached|SessionFactory" app/services/answer.py`
確認：`build_context` 定義仍在（供 report 及測試用），但 `answer_question` 內不再直接呼叫 `hybrid_search`/`embed_query_cached`/`SessionFactory`（若這些 import 僅供該處使用則保留——`build_context` 及其他函式可能仍用；不移除以免誤傷）。

- [ ] **Step 6: 跑全套回歸**

Run: `uv run pytest tests/test_answer.py tests/test_select_reports.py -v`
Expected: 全綠

Run: `uv run pytest -q`
Expected: 全綠

- [ ] **Step 7: Commit**

```bash
git add app/services/answer.py
git commit -m "$(cat <<'EOF'
refactor(問答): answer_question RAG 路徑改用 retrieve_context

問答首輪/多輪皆改呼叫共用 retrieve_context（傳 timer 保留 embed/retrieve 耗時
粒度、離題短路仍 yield 空 sources）。首輪 classify_intent 並發不變。問答與研報
自此共用同一檢索封裝（M2 reranker 單點插入即惠及兩路）。行為零變化。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: 抽 `SentinelStreamParser` 串流狀態機

把 `answer.py:840-885` 的 EXT_SENTINEL 逐 chunk buffering 抽成可單測的類別。

**Files:**
- Create: `app/services/stream_sentinel.py`
- Modify: `app/services/answer.py`（串流迴圈 839-885）
- Test: `tests/test_stream_sentinel.py`（新建）

**Interfaces:**
- Produces: `class SentinelStreamParser`；`__init__(self, sentinel="[EXT_SOURCES]")`；`feed(chunk: str) -> str`（回可安全輸出的片段，保留可能跨界的尾段）；`flush() -> str`（結束時吐殘餘）；property `sentinel_found: bool`。命中 sentinel 後 `feed` 一律回 `""`。

- [ ] **Step 1: 寫失敗測試**

建立 `tests/test_stream_sentinel.py`：

```python
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services.stream_sentinel import SentinelStreamParser  # noqa: E402


class SentinelStreamParserTests(unittest.TestCase):
    def _drive(self, chunks, sentinel="[EXT_SOURCES]"):
        p = SentinelStreamParser(sentinel)
        emitted = "".join(p.feed(c) for c in chunks)
        emitted += p.flush()
        return emitted, p.sentinel_found

    def test_no_sentinel_emits_all(self):
        emitted, found = self._drive(["台積電", "展望", "良好"])
        self.assertEqual(emitted, "台積電展望良好")
        self.assertFalse(found)

    def test_sentinel_stops_emission(self):
        emitted, found = self._drive(["答案本文", "[EXT_SOURCES]", "- 標題 | http://x"])
        self.assertEqual(emitted, "答案本文")
        self.assertTrue(found)

    def test_sentinel_split_across_chunks(self):
        emitted, found = self._drive(["前文[EXT_", "SOURCES]尾"])
        self.assertEqual(emitted, "前文")
        self.assertTrue(found)

    def test_sentinel_at_very_end(self):
        emitted, found = self._drive(["內容", "[EXT_SOURCES]"])
        self.assertEqual(emitted, "內容")
        self.assertTrue(found)

    def test_tail_shorter_than_sentinel_held_then_flushed(self):
        # 未達 sentinel 長度的尾段先保留、無 sentinel 時最終 flush 補回
        emitted, found = self._drive(["abc"])
        self.assertEqual(emitted, "abc")
        self.assertFalse(found)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run pytest tests/test_stream_sentinel.py -v`
Expected: FAIL（模組不存在）

- [ ] **Step 3: 建立 `app/services/stream_sentinel.py`**

邏輯逐字對應 `answer.py:840-885`（`hold` 尾段保留、`find` 命中即停、末端 flush）：

```python
"""串流中偵測 EXT_SENTINEL 的增量狀態機。

自 answer.py 的逐 chunk buffering 抽出：保留最後 len(sentinel) 個字元以偵測
跨 chunk 邊界的 sentinel；命中後停止輸出後續（外部來源區塊改由 raw 全文的
split_external_sources 處理）。與 split_external_sources 語意一致、並存。
"""


class SentinelStreamParser:
    def __init__(self, sentinel: str = "[EXT_SOURCES]"):
        self._sentinel = sentinel
        self._hold = len(sentinel)
        self._buf = ""
        self._found = False

    @property
    def sentinel_found(self) -> bool:
        return self._found

    def feed(self, chunk: str) -> str:
        """吃一段 chunk，回可安全輸出的片段（保留可能跨界的尾段）。"""
        if self._found:
            return ""
        self._buf += chunk
        idx = self._buf.find(self._sentinel)
        if idx != -1:
            out = self._buf[:idx]
            self._found = True
            self._buf = ""
            return out
        if len(self._buf) > self._hold:
            out = self._buf[: -self._hold]
            self._buf = self._buf[-self._hold :]
            return out
        return ""

    def flush(self) -> str:
        """串流結束：無 sentinel 時吐出殘餘尾段。"""
        if self._found:
            return ""
        out = self._buf
        self._buf = ""
        return out
```

- [ ] **Step 4: 跑測試確認通過**

Run: `uv run pytest tests/test_stream_sentinel.py -v`
Expected: PASS（5 項）

- [ ] **Step 5: `answer.py` 串流迴圈改用 parser（839-885）**

`answer.py` 頂部 import 區加：

```python
from app.services.stream_sentinel import SentinelStreamParser
```

把 840-843 的初始化：

```python
    raw_parts: list[str] = []
    buf = ""
    hold = len(EXT_SENTINEL)
    sentinel_found = False
    searching_sent = False
```

改為：

```python
    raw_parts: list[str] = []
    parser = SentinelStreamParser(EXT_SENTINEL)
    searching_sent = False
```

把串流迴圈 868-885：

```python
        raw_parts.append(chunk)
        if sentinel_found:
            continue
        buf += chunk
        idx = buf.find(EXT_SENTINEL)
        if idx != -1:
            if buf[:idx]:
                for ev in _emit_token(buf[:idx]):
                    yield ev
            sentinel_found = True
            buf = ""
        elif len(buf) > hold:
            for ev in _emit_token(buf[:-hold]):
                yield ev
            buf = buf[-hold:]
    if not sentinel_found and buf:
        for ev in _emit_token(buf):
            yield ev
```

改為：

```python
        raw_parts.append(chunk)
        emit = parser.feed(chunk)
        if emit:
            for ev in _emit_token(emit):
                yield ev
    tail = parser.flush()
    if tail:
        for ev in _emit_token(tail):
            yield ev
```

> 語意等價：原「命中後 `continue`」＝ parser 命中後 `feed` 回 `""`；原尾段保留＝ parser 內 `_hold`；原末端 flush＝ `parser.flush()`。`_emit_token` 對空字串不呼叫（`if emit:`/`if tail:` 守門），與原 `if buf[:idx]:`/`if ... and buf:` 一致。

- [ ] **Step 6: 跑回歸**

Run: `uv run pytest tests/test_answer.py tests/test_stream_sentinel.py -v`
Expected: 全綠

Run: `uv run pytest -q`
Expected: 全綠

- [ ] **Step 7: Commit**

```bash
git add app/services/stream_sentinel.py app/services/answer.py tests/test_stream_sentinel.py
git commit -m "$(cat <<'EOF'
refactor(問答): 抽 SentinelStreamParser 串流狀態機

把 answer.py 的 EXT_SENTINEL 逐 chunk buffering（跨 chunk 尾段保留、命中即停、
末端 flush）抽成 stream_sentinel.SentinelStreamParser，補跨邊界/結尾/無 sentinel
單元測試。answer_question 串流迴圈改用 parser，行為零變化。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: `app/config.py` 集中 `ASK_*`/`REPORT_*` 設定

把散在四個模組的 `os.getenv` 收進一個 frozen dataclass、載入一次；各模組保留原常數名但改由 `get_settings()` 取值（下游引用零改動）。去除 `ASK_DENSE_SCAN` 重複。

**Files:**
- Create: `app/config.py`
- Modify: `app/services/answer.py`（45-101 各常數 RHS）、`app/services/report.py`（34-50）、`app/services/intent.py`（19-20, 84-85）、`app/services/report_gate.py`（12, 23）
- Test: `tests/test_config.py`（新建）

**Interfaces:**
- Produces: `@dataclass(frozen=True) class Settings`（欄位見下）；`get_settings() -> Settings`（模組級快取，載入一次）。

- [ ] **Step 1: 寫失敗測試（鎖定每欄預設＝現況字面值）**

建立 `tests/test_config.py`：

```python
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.config import get_settings  # noqa: E402


class SettingsDefaultsTests(unittest.TestCase):
    def test_defaults_match_prior_literals(self):
        s = get_settings()
        # ASK_*
        self.assertEqual(s.ask_max_reports, 15)
        self.assertEqual(s.ask_max_passages, 4)
        self.assertEqual(s.ask_max_context_chars, 20000)
        self.assertEqual(s.ask_retrieval_k, 15)
        self.assertEqual(s.ask_dense_scan, 400)
        self.assertEqual(s.ask_recency_weight, 0.06)
        self.assertEqual(s.ask_recency_half_life_days, 90)
        self.assertEqual(s.ask_relevance_band, 0.10)
        self.assertEqual(s.ask_band_eps, 0.03)
        self.assertEqual(s.ask_fresh_factor, 0.5)
        self.assertEqual(s.ask_stale_factor, 0.1)
        self.assertEqual(s.ask_min_fresh_before_cutoff, 2)
        self.assertEqual(s.ask_relevance_floor, 0.62)
        self.assertEqual(s.ask_min_reports, 3)
        self.assertEqual(s.ask_stale_age_days, 180)
        self.assertEqual(s.ask_max_stale_reports, 4)
        self.assertTrue(s.ask_enable_web)
        # intent
        self.assertEqual(s.ask_intent_model, "claude-haiku-4-5")
        # REPORT_*
        self.assertEqual(s.report_model, "claude-sonnet-4-6")
        self.assertEqual(s.report_deep_k, 30)
        self.assertEqual(s.report_max_reports, 25)
        self.assertEqual(s.report_max_passages, 6)
        self.assertEqual(s.report_max_context_chars, 40000)
        self.assertEqual(s.report_timeout, 600.0)
        self.assertEqual(s.report_thin_coverage, 8)
        self.assertTrue(s.report_enable_web)

    def test_singleton(self):
        self.assertIs(get_settings(), get_settings())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL（`app.config` 不存在）

- [ ] **Step 3: 建立 `app/config.py`**

> 收錄目前為 `os.getenv` 驅動的 `ASK_*`/`REPORT_*`（含 intent、report_gate）。每欄 name/default 與現況逐字相同。`REPORTS_DIR`、`ASK_INTENT_TIMEOUT`、`ASK_CONDENSE_MODEL/TIMEOUT`、`REPORT_MIN_CITED`、`REPORT_LONG_ANSWER_CHARS`、`ASK_ENABLE_WEB` 等一併納入。

```python
"""集中設定：一次讀取 ASK_*/REPORT_* 環境變數。

各服務模組保留原常數名，改由 get_settings() 取值，避免同一 env 在多檔重複讀取
（如 ASK_DENSE_SCAN 原本 answer.py 與 report.py 各定義一次）。retrieval.py 的
非-env 字面量（BAND_WIDTH 等）不在此收斂範圍（見 M6）。
"""

import os
from dataclasses import dataclass


def _flag(name: str, default: str) -> bool:
    return os.getenv(name, default) not in ("0", "false", "False", "")


@dataclass(frozen=True)
class Settings:
    # ASK_*（answer.py）
    ask_max_reports: int
    ask_max_passages: int
    ask_max_context_chars: int
    ask_retrieval_k: int
    ask_dense_scan: int
    ask_recency_weight: float
    ask_recency_half_life_days: float
    ask_relevance_band: float
    ask_band_eps: float
    ask_fresh_factor: float
    ask_stale_factor: float
    ask_min_fresh_before_cutoff: int
    ask_relevance_floor: float
    ask_min_reports: int
    ask_stale_age_days: int
    ask_max_stale_reports: int
    ask_enable_web: bool
    # intent.py
    ask_intent_model: str
    ask_intent_timeout: float
    ask_condense_model: str
    ask_condense_timeout: float
    # report.py
    report_model: str
    report_deep_k: int
    report_max_reports: int
    report_max_passages: int
    report_max_context_chars: int
    report_timeout: float
    reports_dir: str
    report_enable_web: bool
    report_thin_coverage: int
    # report_gate.py
    report_min_cited: int
    report_long_answer_chars: int


def _load() -> Settings:
    intent_model = os.getenv("ASK_INTENT_MODEL", "claude-haiku-4-5")
    return Settings(
        ask_max_reports=int(os.getenv("ASK_MAX_REPORTS", "15")),
        ask_max_passages=int(os.getenv("ASK_MAX_PASSAGES", "4")),
        ask_max_context_chars=int(os.getenv("ASK_MAX_CONTEXT_CHARS", "20000")),
        ask_retrieval_k=int(os.getenv("ASK_RETRIEVAL_K", "15")),
        ask_dense_scan=int(os.getenv("ASK_DENSE_SCAN", "400")),
        ask_recency_weight=float(os.getenv("ASK_RECENCY_WEIGHT", "0.06")),
        ask_recency_half_life_days=float(os.getenv("ASK_RECENCY_HALF_LIFE_DAYS", "90")),
        ask_relevance_band=float(os.getenv("ASK_RELEVANCE_BAND", "0.10")),
        ask_band_eps=float(os.getenv("ASK_BAND_EPS", "0.03")),
        ask_fresh_factor=float(os.getenv("ASK_FRESH_FACTOR", "0.5")),
        ask_stale_factor=float(os.getenv("ASK_STALE_FACTOR", "0.1")),
        ask_min_fresh_before_cutoff=int(os.getenv("ASK_MIN_FRESH_BEFORE_CUTOFF", "2")),
        ask_relevance_floor=float(os.getenv("ASK_RELEVANCE_FLOOR", "0.62")),
        ask_min_reports=int(os.getenv("ASK_MIN_REPORTS", "3")),
        ask_stale_age_days=int(os.getenv("ASK_STALE_AGE_DAYS", "180")),
        ask_max_stale_reports=int(os.getenv("ASK_MAX_STALE_REPORTS", "4")),
        ask_enable_web=_flag("ASK_ENABLE_WEB", "1"),
        ask_intent_model=intent_model,
        ask_intent_timeout=float(os.getenv("ASK_INTENT_TIMEOUT", "8")),
        ask_condense_model=os.getenv("ASK_CONDENSE_MODEL", intent_model),
        ask_condense_timeout=float(os.getenv("ASK_CONDENSE_TIMEOUT", "8")),
        report_model=os.getenv("REPORT_MODEL", "claude-sonnet-4-6"),
        report_deep_k=int(os.getenv("REPORT_DEEP_K", "30")),
        report_max_reports=int(os.getenv("REPORT_MAX_REPORTS", "25")),
        report_max_passages=int(os.getenv("REPORT_MAX_PASSAGES", "6")),
        report_max_context_chars=int(os.getenv("REPORT_MAX_CONTEXT_CHARS", "40000")),
        report_timeout=float(os.getenv("REPORT_TIMEOUT", "600")),
        reports_dir=os.getenv("REPORTS_DIR", "data/reports"),
        report_enable_web=_flag("REPORT_ENABLE_WEB", "1"),
        report_thin_coverage=int(os.getenv("REPORT_THIN_COVERAGE", "8")),
        report_min_cited=int(os.getenv("REPORT_MIN_CITED", "1")),
        report_long_answer_chars=int(os.getenv("REPORT_LONG_ANSWER_CHARS", "300")),
    )


_SETTINGS: Settings | None = None


def get_settings() -> Settings:
    global _SETTINGS
    if _SETTINGS is None:
        _SETTINGS = _load()
    return _SETTINGS
```

> **執行者：先讀 `intent.py:19-20,84-85` 與 `report_gate.py:12,23` 的實際 env 名稱與預設值**，把上面 `ask_intent_timeout`/`ask_condense_*`/`report_min_cited`/`report_long_answer_chars` 的 default 校準為與現況逐字相同（本 plan 的 `8`/`1`/`300` 為依語意推估，若與檔案不符以檔案為準並同步更新 `tests/test_config.py`）。

- [ ] **Step 4: 跑 config 測試（先綠）**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS（若 intent/report_gate 預設經 Step 3 校準）

- [ ] **Step 5: 各模組改由 get_settings() 取值（保留常數名）**

`answer.py` 45-101，把每個 `X = os.getenv(...)` 的 RHS 改為 `get_settings().<field>`，常數名不動。頂部加 `from app.config import get_settings`，並在常數區前 `_S = get_settings()`。例（全部比照）：

```python
_S = get_settings()
MAX_REPORTS = _S.ask_max_reports
MAX_PASSAGES_PER_REPORT = _S.ask_max_passages
MAX_CONTEXT_CHARS = _S.ask_max_context_chars
RETRIEVAL_K = _S.ask_retrieval_k
ASK_DENSE_SCAN = _S.ask_dense_scan
RECENCY_WEIGHT = _S.ask_recency_weight
RECENCY_HALF_LIFE_DAYS = _S.ask_recency_half_life_days
RELEVANCE_BAND = _S.ask_relevance_band
BAND_EPS = _S.ask_band_eps
ASK_FRESH_FACTOR = _S.ask_fresh_factor
ASK_STALE_FACTOR = _S.ask_stale_factor
ASK_MIN_FRESH_BEFORE_CUTOFF = _S.ask_min_fresh_before_cutoff
ASK_RELEVANCE_FLOOR = _S.ask_relevance_floor
ASK_MIN_REPORTS = _S.ask_min_reports
ASK_STALE_AGE_DAYS = _S.ask_stale_age_days
ASK_MAX_STALE_REPORTS = _S.ask_max_stale_reports
ASK_ENABLE_WEB = _S.ask_enable_web
```

（`MAX_HISTORY_TURNS`/`MAX_HISTORY_ANSWER_CHARS` 非 env、保留原樣；`SYSTEM_PROMPT` 等不動。）

`report.py` 34-50 同法：

```python
_S = get_settings()
REPORT_MODEL = _S.report_model
REPORT_DEEP_K = _S.report_deep_k
REPORT_MAX_REPORTS = _S.report_max_reports
REPORT_MAX_PASSAGES = _S.report_max_passages
REPORT_MAX_CONTEXT_CHARS = _S.report_max_context_chars
REPORT_TIMEOUT = _S.report_timeout
REPORTS_DIR = _S.reports_dir
ASK_DENSE_SCAN = _S.ask_dense_scan
REPORT_ENABLE_WEB = _S.report_enable_web
REPORT_THIN_COVERAGE = _S.report_thin_coverage
```

`intent.py`（19-20, 84-85）與 `report_gate.py`（12, 23）：把對應 `os.getenv` 常數改為 `get_settings().<field>`，常數名不動。

- [ ] **Step 6: 跑全套回歸**

Run: `uv run pytest -q`
Expected: 全綠（常數值未變、僅來源改為 config）

- [ ] **Step 7: grep 確認 ASK_DENSE_SCAN 不再各自 os.getenv**

Run: `rg -n 'os.getenv\("ASK_DENSE_SCAN"' app/`
Expected: 無輸出（僅 `app/config.py` 有該 getenv）

- [ ] **Step 8: Commit**

```bash
git add app/config.py app/services/answer.py app/services/report.py app/services/intent.py app/services/report_gate.py tests/test_config.py
git commit -m "$(cat <<'EOF'
refactor(config): 集中 ASK_*/REPORT_* 至 app/config.py

新增 Settings frozen dataclass ＋ get_settings()（載入一次）；answer/report/
intent/report_gate 保留原常數名、改由 config 取值，消除 ASK_DENSE_SCAN 在兩檔
重複讀取。新增預設值測試鎖定每欄＝現況字面值。行為零變化。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## 全套驗證（所有 task 後）

```bash
uv run pytest -q                               # 全套回歸全綠
rg -n "_RID|row\[_|meta_row\[|prow\[-|row\[-1\]|row\[-2\]" app/ web/   # 無殘留位移慣例
rg -n 'os.getenv\("ASK_DENSE_SCAN"' app/       # 僅 app/config.py
```

行為 parity（人工佐證，可選）：對同一問題跑重構前後的 `/api/ask`、`/api/report`，比對 `sources`、答案本文、SSE 事件序一致。部署：重啟 `report-mark-web.service`（無 schema 變更）。

## Self-Review

- **Spec 覆蓋**：Unit 1 ChunkRow→Task 1/2；Unit 2 retrieve_context→Task 4/5；Unit 3 SentinelStreamParser→Task 6；Unit 4 config→Task 7；Unit 5 select_reports→Task 3。全單元皆有對應 task。
- **不變式**：`ChunkRow` NamedTuple tuple 相容（Task 1 測試 `isinstance(row, tuple)` 與位移/具名一致）；build_context golden master（Task 3）鎖定選篇+格式化輸出逐字不變；retrieve_context 保留 timer 粒度與 intent 併發（Task 4/5）；SentinelStreamParser 語意等價（Task 6 五案）；config 預設＝現況（Task 7 斷言）。
- **型別一致**：`ChunkRow` 欄名（chunk_id/report_id/file_name/market/report_date/content/distance/chunk_index）於 Task 1 定義，Task 2/3 消費一致；`SelectedReport`（Task 3）欄位於 build_context 消費一致；`Settings` 欄名（Task 7）於 test_config 與各模組一致。
- **循環 import**：`retrieval_pipeline` 頂層 import `answer`；`answer` 改為**函式內** import `retrieval_pipeline`（Task 5 Step 1）避免循環。
- **待執行者校準**：Task 7 Step 3 的 intent/report_gate env 預設值須以檔案實際值為準（`ask_intent_timeout`/`ask_condense_*`/`report_min_cited`/`report_long_answer_chars`）。

## Execution Handoff

見主流程：本 plan 交由 superpowers:subagent-driven-development 逐 task 執行（每 task 一個 fresh subagent + 兩階段審查）。
