# 延遲與品質基準量測 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 取得四項目標路線圖第 0 步所需的三組基準數字（問答延遲分佈、檢索分段耗時、標的覆蓋率），並把品質 baseline 重跑到當前系統狀態。

**Architecture:** 兩段程式碼改動（一支唯讀量測腳本、一組走既有 `stats` 機制的分段計時）＋ 一項執行任務（重跑 `eval/` baseline）。全部零品質風險：量測腳本不寫任何表、不呼叫 LLM；分段計時只往既有的可選遙測 dict 多塞兩個鍵，不改任何回傳型別或排序邏輯。

**Tech Stack:** Python 3.11+、SQLAlchemy async、asyncpg、`uv`、pytest（unittest 風格）、ruff（`E,F,I`，line-length 120）

**Spec:** `docs/superpowers/specs/2026-08-17-four-goals-roadmap-design.md`（§4 現況：四項都沒有可信基準）

## Global Constraints

- **不得改動 repo 根的真實 `.env`。** 本機 repo root 就是部署目錄；任何「寫真檔再於 `finally` 還原」的測試都是拿生產換覆蓋率。要驗載入行為就餵 `tempfile`。
- **共用工作目錄**：其他人可能有未提交的 WIP。一律 `git add <明確路徑>`，禁用 `git add -A` / `git add .`。commit 前以 `git diff --staged --stat` 確認範圍。
- **Commit 訊息**：Conventional Commits ＋ 繁體中文 scope（如 `feat(measure): …`、`test(retrieval): …`）。
- **不加裝飾性 emoji**（程式碼、文件、commit、輸出皆然）。
- **DB 連線一律 `from app.services.db import SessionFactory`**，不得複製連線字串到新模組。
- **ruff 設定不得更動**：`E,F,I`、`line-length = 120`、`extend-ignore = ["E402"]`。批次／腳本在 import 前做 `sys.path.insert` 是本 repo 慣例，`E402` 已忽略。
- **不執行 `make reset-db` / `make clean-data`。**
- 本計畫**不含**股票代號搜尋與 rerank 進度事件——那兩項各自需要先寫 spec（見路線圖 §9 第 1 步）。

---

### Task 1: 基準量測腳本

一支唯讀報表腳本，一次取三組數字：`qa_log` 延遲分佈、`qa_log` 路由分佈、`research_report` 標的覆蓋率。前兩組是目標 1 的優先序依據，第三組是目標 3 的可行性前提（`stock_targets` 覆蓋率太低則「標的優先」那一路經常是空的）。

比照 `scripts/check_batch_freshness.py` 的形狀：純 SQL、零 LLM、零寫入，退出碼 `0` 完成／`2` DB 不可用（處置不同故與一般錯誤分流）。

**Files:**
- Create: `scripts/measure_baseline.py`
- Create: `tests/test_measure_baseline.py`

**Interfaces:**
- Consumes: `app.services.db.SessionFactory`
- Produces: `build_coverage(total, with_targets, with_stock_code, with_any) -> dict`（純函式，Task 無下游依賴，但測試需要它可獨立呼叫）

- [ ] **Step 1: 寫失敗的測試**

建立 `tests/test_measure_baseline.py`：

```python
"""基準量測腳本的純函式測試（不需要 DB）。

只測 build_coverage：SQL 本身由 DB 驗證，而百分比計算有一個真實的邊界——
空語料（新機器／重建中）會讓 total=0，除零會讓「量測腳本自己炸掉」，
那正是這類唯讀工具最不該有的故障型態。
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.measure_baseline import build_coverage  # noqa: E402


class BuildCoverageTests(unittest.TestCase):
    def test_empty_corpus_does_not_divide_by_zero(self):
        out = build_coverage(0, 0, 0, 0)
        self.assertEqual(out["total"], 0)
        self.assertEqual(out["pct_targets"], 0.0)
        self.assertEqual(out["pct_stock_code"], 0.0)
        self.assertEqual(out["pct_any"], 0.0)

    def test_percentages_rounded_to_two_places(self):
        out = build_coverage(1000, 250, 400, 500)
        self.assertEqual(out["pct_targets"], 25.0)
        self.assertEqual(out["pct_stock_code"], 40.0)
        self.assertEqual(out["pct_any"], 50.0)

    def test_repeating_decimal_is_rounded_not_truncated(self):
        # 1/3 = 33.333…％；round(…, 2) 應給 33.33 而非 33.0 或 33.34
        out = build_coverage(3, 1, 0, 1)
        self.assertEqual(out["pct_targets"], 33.33)

    def test_counts_are_passed_through_unchanged(self):
        out = build_coverage(10, 3, 4, 5)
        self.assertEqual(out["with_targets"], 3)
        self.assertEqual(out["with_stock_code"], 4)
        self.assertEqual(out["with_any"], 5)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑測試確認它失敗**

```bash
uv run pytest tests/test_measure_baseline.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.measure_baseline'`

- [ ] **Step 3: 寫腳本**

建立 `scripts/measure_baseline.py`：

```python
"""延遲／路由／標的覆蓋率的基準量測（唯讀，零 LLM、零寫入）。

存在理由：2026-08-17 的四項目標路線圖把「取得基準」列為第 0 步——四項裡有三項的
優先序取決於數字，而那些數字目前只存在於程式碼註解裡（`app/config.py:201` 的
rerank 50 對 ~34s、`app/services/answer.py:2006` 的檢索 ~48s）。註解是點狀實測，
不是分佈；沒有分佈就無法判斷「改善了 10 秒」對 p95 有沒有意義。

三份報表：
  1. qa_log 的 latency_ms / thinking_ms 分佈（p50/p95/p99/max）
  2. qa_log 的路由分佈（filters->>'path' × filters->>'decided_by'）
     ——`fail_open` 的佔比即分類器失敗率，那個數字現在完全不可觀測。
  3. research_report 的 stock_targets / stock_code 覆蓋率（目標 3 的可行性前提）

刻意不做：
  - 不寫任何表、不呼叫 LLM、不載入 embedding 模型。
  - 不設門檻、不告警。這是一次性量測工具，不是偵測器——停更偵測是
    `scripts/check_batch_freshness.py` 的職責，兩者不合流。

用法：
  uv run python scripts/measure_baseline.py
  uv run python scripts/measure_baseline.py --days 60
  uv run python scripts/measure_baseline.py --json

退出碼：0＝完成；2＝DB 不可用（與 check_batch_freshness.py 同慣例，處置不同故分流）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

EXIT_OK = 0
EXIT_UNKNOWN = 2

# percentile_disc 是 ordered-set aggregate，會略過 NULL 輸入列——thinking_ms 在舊列
# 上可能是 NULL（該欄是後來補的），故延遲與思考時間各自的母體大小不同，兩者都要報。
LATENCY_SQL = """
SELECT
  count(*)                                                    AS n,
  count(latency_ms)                                           AS n_latency,
  count(thinking_ms)                                          AS n_thinking,
  percentile_disc(0.5)  WITHIN GROUP (ORDER BY latency_ms)    AS latency_p50,
  percentile_disc(0.95) WITHIN GROUP (ORDER BY latency_ms)    AS latency_p95,
  percentile_disc(0.99) WITHIN GROUP (ORDER BY latency_ms)    AS latency_p99,
  max(latency_ms)                                             AS latency_max,
  percentile_disc(0.5)  WITHIN GROUP (ORDER BY thinking_ms)   AS thinking_p50,
  percentile_disc(0.95) WITHIN GROUP (ORDER BY thinking_ms)   AS thinking_p95,
  percentile_disc(0.99) WITHIN GROUP (ORDER BY thinking_ms)   AS thinking_p99,
  max(thinking_ms)                                            AS thinking_max
FROM research.qa_log
WHERE created_at >= now() - (interval '1 day' * :days)
"""

# path / decided_by 皆為 M4 之後才寫入，舊列沒有這兩個鍵 → COALESCE 成 '(none)'，
# 讓「有多少列根本沒有路由遙測」自己現形，而不是被靜默排除在分母之外。
ROUTE_SQL = """
SELECT
  COALESCE(filters->>'path', '(none)')        AS path,
  COALESCE(filters->>'decided_by', '(none)')  AS decided_by,
  count(*)                                    AS n
FROM research.qa_log
WHERE created_at >= now() - (interval '1 day' * :days)
GROUP BY 1, 2
ORDER BY 3 DESC, 1, 2
"""

# 母體用 is_research（該欄已收斂為 NOT NULL DEFAULT true），與檢索母體一致。
# stock_code 是純量欄位、stock_targets 是陣列，兩者互不覆蓋——目標 3 的召回路徑
# 要同時吃這兩欄，故分開報並另報聯集。
COVERAGE_SQL = """
SELECT
  count(*)                                                              AS total,
  count(*) FILTER (
    WHERE stock_targets IS NOT NULL AND cardinality(stock_targets) > 0
  )                                                                     AS with_targets,
  count(*) FILTER (
    WHERE stock_code IS NOT NULL AND stock_code <> ''
  )                                                                     AS with_stock_code,
  count(*) FILTER (
    WHERE (stock_targets IS NOT NULL AND cardinality(stock_targets) > 0)
       OR (stock_code IS NOT NULL AND stock_code <> '')
  )                                                                     AS with_any
FROM research.research_report
WHERE is_research
"""


def build_coverage(
    total: int, with_targets: int, with_stock_code: int, with_any: int
) -> dict:
    """覆蓋率百分比（純函式）。

    total=0 時一律回 0.0——空語料是新機器／重建中的正常狀態，讓量測腳本除零炸掉
    等於「拿來診斷的工具自己成為故障源」。
    """
    def pct(n: int) -> float:
        return round(100.0 * n / total, 2) if total else 0.0

    return {
        "total": total,
        "with_targets": with_targets,
        "with_stock_code": with_stock_code,
        "with_any": with_any,
        "pct_targets": pct(with_targets),
        "pct_stock_code": pct(with_stock_code),
        "pct_any": pct(with_any),
    }


async def collect(days: int) -> dict:
    """跑三支查詢並組成報表 dict。DB 不可用時例外原樣上拋，由 main 轉成 rc=2。"""
    from app.services.db import SessionFactory

    async with SessionFactory() as session:
        lat = (await session.execute(text(LATENCY_SQL), {"days": days})).mappings().first()
        routes = (await session.execute(text(ROUTE_SQL), {"days": days})).mappings().all()
        cov = (await session.execute(text(COVERAGE_SQL))).mappings().first()

    return {
        "window_days": days,
        "latency": dict(lat) if lat is not None else {},
        "routes": [dict(r) for r in routes],
        "coverage": build_coverage(
            int(cov["total"] or 0),
            int(cov["with_targets"] or 0),
            int(cov["with_stock_code"] or 0),
            int(cov["with_any"] or 0),
        ),
    }


def render(report: dict) -> str:
    """報表 → 人可讀文字（純函式）。"""
    lat = report["latency"]
    lines = [
        f"== 問答延遲（近 {report['window_days']} 天）==",
        f"  樣本數 n={lat.get('n')} "
        f"(latency 非空 {lat.get('n_latency')}／thinking 非空 {lat.get('n_thinking')})",
        f"  latency_ms   p50={lat.get('latency_p50')} p95={lat.get('latency_p95')} "
        f"p99={lat.get('latency_p99')} max={lat.get('latency_max')}",
        f"  thinking_ms  p50={lat.get('thinking_p50')} p95={lat.get('thinking_p95')} "
        f"p99={lat.get('thinking_p99')} max={lat.get('thinking_max')}",
        "",
        "== 路由分佈 ==",
    ]
    if not report["routes"]:
        lines.append("  （窗期內無 qa_log 列）")
    for r in report["routes"]:
        lines.append(f"  {r['path']:<16} decided_by={r['decided_by']:<12} n={r['n']}")

    c = report["coverage"]
    lines += [
        "",
        "== 標的覆蓋率（is_research 母體）==",
        f"  總篇數 {c['total']}",
        f"  stock_targets 非空  {c['with_targets']} ({c['pct_targets']}%)",
        f"  stock_code    非空  {c['with_stock_code']} ({c['pct_stock_code']}%)",
        f"  兩者聯集            {c['with_any']} ({c['pct_any']}%)",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="延遲／路由／標的覆蓋率基準量測（唯讀）")
    parser.add_argument("--days", type=int, default=30, help="qa_log 回溯窗期（天）")
    parser.add_argument("--json", action="store_true", help="輸出 JSON 而非文字")
    args = parser.parse_args()

    try:
        report = asyncio.run(collect(args.days))
    except Exception as exc:  # DB 不可用與查詢失敗都走這裡；訊息原樣印出供診斷
        print(f"量測失敗（DB 不可用？）：{exc}", file=sys.stderr)
        return EXIT_UNKNOWN

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        print(render(report))
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: 跑測試確認通過**

```bash
uv run pytest tests/test_measure_baseline.py -v
```

Expected: 4 passed

- [ ] **Step 5: 跑 lint**

```bash
uv run ruff check scripts/measure_baseline.py tests/test_measure_baseline.py
```

Expected: `All checks passed!`

- [ ] **Step 6: 對真實 DB 跑一次**

需要 DB 在跑（WSL 內的 `report-mark-postgres` 容器）。

```bash
uv run python scripts/measure_baseline.py
```

Expected: 印出三段報表，退出碼 0。**把輸出貼進 commit 訊息或另存**——這份數字就是路線圖 §4.1／§4.3 的交付物。

DB 沒起來時應印出「量測失敗（DB 不可用？）」並回 rc=2，**不是 traceback**。以此確認錯誤路徑：

```bash
echo $?
```

- [ ] **Step 7: Commit**

```bash
git add scripts/measure_baseline.py tests/test_measure_baseline.py
git diff --staged --stat
git commit -m "feat(measure): 新增延遲／路由／標的覆蓋率的唯讀基準量測腳本"
```

---

### Task 2: 檢索分段計時（dense 與 lexical 分開量）

`timer.mark("retrieve")` 目前把 embed → dense HNSW → lexical trgm → 去重融合整段當成一段（`app/services/retrieval_pipeline.py:111-116`），所以 dense 與 lexical 各佔多少完全未知。這兩者的成本結構完全不同（`ASK_DENSE_SCAN=400` 的 HNSW 掃描 vs 57 萬列的 trgm GIN），不拆開就無法決定該優化哪一邊。

**走既有的 `stats` 機制**：`hybrid_search` 已經接受一個可選的 `stats: dict | None` 並往裡面填遙測（`lex_hits`／`lex_cap`／`lex_truncated`／`null_embedding_skipped`）。多塞兩個鍵不改回傳型別、不動任何 fake 的簽章——那個設計正是為此存在的（見 `hybrid_search` docstring 對「不多回一個值」的說明）。

**Files:**
- Modify: `app/services/retrieval.py:98-131`（`hybrid_search` 內的兩次查詢與 stats 填寫）
- Modify: `app/services/answer.py:2248-2258`（`qa_timing` log 行）
- Test: `tests/test_retrieval_rank.py`（沿用 `HybridSearchLexStatsTests._run` 的 fake 模式）
- Test: `tests/test_answer.py`（沿用 `AskLexTelemetryTests._ask_with_lex_stats` 的模式）

**Interfaces:**
- Consumes: 既有的 `hybrid_search(session, q, query_embedding, *, stats=None, ...)` 契約
- Produces: `stats` dict 新增兩個鍵 —— `dense_ms: int`、`lex_ms: int`（毫秒整數；字面路未執行時 `lex_ms` 為 `0`）

- [ ] **Step 1: 寫失敗的測試（retrieval 層）**

在 `tests/test_retrieval_rank.py` 的 `HybridSearchLexStatsTests` 之後新增：

```python
class HybridSearchTimingStatsTests(unittest.IsolatedAsyncioTestCase):
    """dense 與 lexical 必須分開計時。

    合在一起量的後果不是資訊少一點，是**優化方向可能整個押錯邊**：HNSW 掃描
    與 57 萬列的 trgm GIN 成本結構完全不同，而 `timer.mark("retrieve")` 把兩者
    連同 embed 與融合一起塞進同一個數字。
    """

    @staticmethod
    async def _run(*, query="台積電", stats=None):
        from app.services import retrieval as ret

        async def fake_dense(*a, **k):
            return []

        async def fake_lex(*a, **k):
            return [], 0

        orig = (ret.search_chunks_meta, ret.search_chunks_lexical)
        ret.search_chunks_meta = fake_dense
        ret.search_chunks_lexical = fake_lex
        try:
            await ret.hybrid_search(object(), query, [0.0], stats=stats)
        finally:
            ret.search_chunks_meta, ret.search_chunks_lexical = orig
        return stats

    async def test_both_segments_recorded_as_ints(self):
        stats = await self._run(stats={})
        self.assertIsInstance(stats["dense_ms"], int)
        self.assertIsInstance(stats["lex_ms"], int)
        self.assertGreaterEqual(stats["dense_ms"], 0)
        self.assertGreaterEqual(stats["lex_ms"], 0)

    async def test_lex_ms_is_zero_when_query_yields_no_terms(self):
        # 純標點：norm_for_match 後不含任何 [a-z0-9] 或 CJK 段 → terms 為空
        # → 字面路整段不執行。此時 lex_ms 必須是 0 而非 None，否則 log 會印出
        # 「lex_ms=None」而讀者無從分辨「沒跑」與「遙測壞了」。
        stats = await self._run(query="！！！", stats={})
        self.assertEqual(stats["lex_ms"], 0)

    async def test_stats_none_is_still_accepted(self):
        # stats 是可選的；不傳不得拋例外（四個生產呼叫端有兩個不傳）
        self.assertIsNone(await self._run(stats=None))
```

- [ ] **Step 2: 跑測試確認它失敗**

```bash
uv run pytest tests/test_retrieval_rank.py::HybridSearchTimingStatsTests -v
```

Expected: FAIL — `KeyError: 'dense_ms'`

- [ ] **Step 3: 實作分段計時**

在 `app/services/retrieval.py` 頂端的 import 區加入 `import time`（放在 `import re` 之後，維持 isort 順序）。

把 `hybrid_search` 內兩次查詢改成：

```python
    scan = dense_scan if dense_scan is not None else max(DENSE_SCAN_MIN, k * 8)
    _t = time.monotonic()
    dense_rows = await search_chunks_meta(session, query_embedding, scan=scan, **filters)
    dense_ms = int((time.monotonic() - _t) * 1000)
    lex_rows = []
    lex_hits = 0
    # 0 而非 None：字面路未執行時 log 會印 `lex_ms=0`，與「遙測沒填」可區分。
    lex_ms = 0
    cap = lex_cap if lex_cap is not None else LEX_CAP
    if terms:
        patterns = ["%" + t.translate(_LIKE_ESC) + "%" for t in terms]
        lex_row_limit = None if lex_unlimited else (
            lex_limit if lex_limit is not None else LEX_LIMIT
        )
        _t = time.monotonic()
        lex_rows, lex_hits = await search_chunks_lexical(
            session,
            query_embedding,
            patterns,
            limit=lex_row_limit,
            cap=cap,
            per_report=lex_per_report,
            **filters,
        )
        lex_ms = int((time.monotonic() - _t) * 1000)
    if stats is not None:
        stats["lex_hits"] = lex_hits
        stats["lex_cap"] = cap
        stats["dense_ms"] = dense_ms
        stats["lex_ms"] = lex_ms
        # >= 而非 ==：cap 是 SQL LIMIT，理論上不會超過，但用 >= 讓「cap 改小後拿到舊
        # 計數」這類意外落在保守側（寧可誤報截斷，不可漏報）。
        stats["lex_truncated"] = bool(terms) and lex_hits >= cap
```

同時更新 `hybrid_search` 的 docstring，把 `stats` 那段改成：

```
    `stats` 給定時填入字面路的召回遙測（`lex_hits`／`lex_cap`／`lex_truncated`）與
    兩路的分段耗時（`dense_ms`／`lex_ms`，毫秒；字面路未執行時 `lex_ms` 為 0）。
```

- [ ] **Step 4: 跑測試確認通過**

```bash
uv run pytest tests/test_retrieval_rank.py -v
```

Expected: 全部 passed（含既有的 `HybridSearchLexStatsTests`、`HybridSearchConfigTests`）

- [ ] **Step 5: 寫 qa_timing 的失敗測試**

`tests/test_answer.py` 的 `AskLexTelemetryTests._ask_with_lex_stats` 目前寫死三個鍵。改成可帶入分段耗時，**保留現有預設值讓兩條既有測試不動**：

把方法簽章與 stats 更新那段改成：

```python
    async def _ask_with_lex_stats(
        self, *, lex_hits, cap, truncated, dense_ms=12, lex_ms=34
    ):
```

```python
        async def recording_search(session, q, qvec, *, stats=None, **k):
            captured["stats_obj"] = stats
            if stats is not None:
                stats.update(
                    {
                        "lex_hits": lex_hits,
                        "lex_cap": cap,
                        "lex_truncated": truncated,
                        "dense_ms": dense_ms,
                        "lex_ms": lex_ms,
                    }
                )
            return [(0, 0.80, make_row("r1", "x.pdf", "TW", "內容。", date(2026, 6, 1)))]
```

然後在該類別末尾（`test_untruncated_run_logs_false_not_missing` 之後）新增：

```python
    async def test_retrieval_segments_land_in_qa_timing(self):
        """dense/lex 分段耗時必須進 qa_timing——只填進 stats 而不 log 等於沒量。"""
        _captured, line = await self._ask_with_lex_stats(
            lex_hits=37, cap=2000, truncated=False, dense_ms=180, lex_ms=1420
        )
        self.assertIn("dense_ms=180", line)
        self.assertIn("lex_ms=1420", line)
```

- [ ] **Step 6: 跑測試確認它失敗**

```bash
uv run pytest tests/test_answer.py::AskLexTelemetryTests -v
```

Expected: `test_retrieval_segments_land_in_qa_timing` FAIL（log 行不含 `dense_ms=`）；另外兩條仍 PASS

- [ ] **Step 7: 把兩個欄位加進 qa_timing log**

修改 `app/services/answer.py:2248-2258`：

```python
    logger.info(
        "qa_timing id=%s %s total_ms=%s thinking_ms=%s lex_hits=%s lex_cap=%s"
        " lex_truncated=%s dense_ms=%s lex_ms=%s",
        qa_id,
        timer.stage_str(),
        timer.total_ms(),
        thinking_ms,
        retrieval_stats.get("lex_hits"),
        retrieval_stats.get("lex_cap"),
        retrieval_stats.get("lex_truncated"),
        retrieval_stats.get("dense_ms"),
        retrieval_stats.get("lex_ms"),
    )
```

- [ ] **Step 8: 跑測試確認通過**

```bash
uv run pytest tests/test_answer.py::AskLexTelemetryTests tests/test_retrieval_rank.py -v
```

Expected: 全部 passed

- [ ] **Step 9: 跑完整後端測試 ＋ lint**

```bash
SKIP_SPA_TESTS=1 uv run pytest -q
```

Expected: 全部 passed。（`SKIP_SPA_TESTS=1` 是因為本機沒有 `frontend/dist` 時 `tests/test_spa_serving.py` 會紅——本任務不動前端。）

```bash
uv run ruff check .
```

Expected: `All checks passed!`

- [ ] **Step 10: Commit**

```bash
git add app/services/retrieval.py app/services/answer.py tests/test_retrieval_rank.py tests/test_answer.py
git diff --staged --stat
git commit -m "feat(retrieval): dense 與 lexical 分開計時並進 qa_timing，檢索段不再是單一黑箱"
```

---

### Task 3: 重跑品質 baseline

路線圖 §4.2：`eval/` 的 baseline 停在 M4（RAGAS）／M7（研報），M8–M10 全未重跑。**D6 把品質定為硬約束，而硬約束需要可比的對照**——沒有當前 baseline，後續每一個加速改動跑 `eval-compare` 都會比到一套已不存在的系統。

這是執行任務，不是程式碼任務：不改任何檔案，只跑既有 harness 並存檔。

**Files:**
- Create: `eval/baselines/baseline-2026-08-17.json`（由工具產出，不手寫）

**Interfaces:**
- Consumes: `eval/run_ragas.py`、`scripts/eval_compare.py`（皆為既有工具）
- Produces: 一份可作為後續 `eval-compare` BASE 的結果檔

- [ ] **Step 1: 確認前置條件**

三項都要成立才開跑：

```bash
which claude
```
Expected: 有輸出。`eval` 走 `app/services/llm.py`，需要 `claude` CLI 在 PATH。

```bash
uv run python scripts/check_batch_freshness.py
```
用來確認 DB 可用（rc 2＝DB 不可用就先修那個）。

**確認 `report-mark-sync.timer` 不會在跑的期間觸發。** `eval` 走 `llm.py`，而 `scripts/_claude_lock.py` 的 flock **刻意不含 `llm.py`**——所以 eval 與同步鏈的批次會**互搶同一個 `claude` CLI 而沒有任何鎖擋著**。這正是 2026-07-29 那份 baseline 掉題的成因類型。同步排程每 3 小時一次：

```bash
systemctl list-timers report-mark-sync.timer
```

若下一次觸發在一小時內，先等它跑完再開始。

- [ ] **Step 2: 跑 RAGAS baseline**

**`--concurrency 1` 不可省。** `run_ragas.py` 的預設是 3，而 `baseline-2026-07-29` 的 `notes` 記著：預設值會讓多個 `claude -p` 互搶，實測 8 題掉 3 題、延遲 213–222s（單執行緒僅 23–37s）。不帶這個旗標就會產出一份髒基準，而髒基準比沒有基準更糟——它看起來可以拿來比。

```bash
uv run python eval/run_ragas.py --concurrency 1 --out eval/baselines/baseline-2026-08-17.json
```

預期耗時：8 題 × 每題 23–37s ＋ judge，約 10–20 分鐘。judge 逾時預設已是 180s（`eval/judge.py:31`），不需另外調。

- [ ] **Step 3: 驗證這份 baseline 是乾淨的**

```bash
grep -o '"n_errors": [0-9]*' eval/baselines/baseline-2026-08-17.json
grep -o '"n": [0-9]*' eval/baselines/baseline-2026-08-17.json | head -1
```

Expected: `n_errors: 0`、`n: 8`。

**`n_errors` 不是 0 就重跑，不要接受。** 掉題會讓 summary 的平均值虛高（先前有兩份 baseline 分別掉 3 題與 5 題，數字都不可用）。

- [ ] **Step 4: 與舊 baseline 對比（結果是資訊，不是驗收）**

```bash
make eval-compare BASE=eval/baselines/baseline-2026-07-29.json CAND=eval/baselines/baseline-2026-08-17.json
```

**這一步的非零退出碼不代表任務失敗。** 退出碼的意義是：`0` 無劣化／`1` 有劣化／`2` 不可比／`3` 有未分類指標。M8–M10 之後系統已大幅改動，出現 `1` 是預期內的資訊，**要記錄下來而不是修掉**。

特別注意 `context_precision`：它自 M0 起就未達 0.8 門檻（`baseline-2026-07-29` 是 0.679），且 PR #140 的量綱修正**預期會再壓低它**。若這次量到更低的 CP，那**不是本次改動造成的**——路線圖 §10 的 C4 已記錄此事，D6 明定品質是約束不是目標，故本計畫不處理它。

- [ ] **Step 5: 補上 notes 並存檔**

`run_ragas.py` 產出的 JSON 尾端有 `notes` 欄位。手動補上這次的執行條件，比照 `baseline-2026-07-29` 的格式，至少要記：日期、`n`、`n_errors`、`--concurrency 1`、與舊 baseline 的 `eval-compare` 退出碼與逐指標差異、以及「M8–M10 之後的第一份量測」這件事。

**沒有 notes 的 baseline 在三個月後等於沒有 baseline**——`baseline-2026-07-29` 的 `notes` 正是 Step 2 那個 `--concurrency 1` 陷阱的唯一記載處。

- [ ] **Step 6: Commit**

```bash
git add eval/baselines/baseline-2026-08-17.json
git diff --staged --stat
git commit -m "test(eval): 重跑 RAGAS 基準至 M10 後的當前狀態，供後續加速改動對比"
```

---

## 完成後的交付物

| 路線圖項目 | 由哪個 Task 交付 |
|---|---|
| §4.1 延遲分佈（`qa_log` p50/p95/p99） | Task 1 Step 6 的輸出 |
| §4.1 檢索分段（dense vs lexical） | Task 2 — 需在真實流量下累積數天 `qa_timing` 後才讀得到分佈 |
| §4.2 品質 baseline | Task 3 的 `baseline-2026-08-17.json` |
| §4.3 `stock_targets` 覆蓋率 | Task 1 Step 6 的輸出 |

**Task 2 的數字不是跑完就有的**：它只是把量測點裝上去，實際分佈要等真實請求累積。以現行流量（約 0.7 題／天）計，需要數週才會有統計意義——這一點會直接影響「目標 1 的檢索優化該不該等」，取得初步樣本後應回頭修訂路線圖 §9 的順序。

## 明確不在本計畫範圍

- 股票代號搜尋（目標 3）——需先寫 spec。
- rerank 期間的 SSE 進度事件——需先寫 spec（涉及 `answer.py` 兩個等待點、SSE 事件白名單、`askSchemas.ts` parser、`askReducer`、UI 元件，是全端改動而非小改）。
- 任何品質敏感旋鈕的調整（rerank 候選數、`max_length`、ONNX 量化）——必須等 Task 3 的 baseline。
- `context_precision` 未達門檻的問題（路線圖 §12）。
