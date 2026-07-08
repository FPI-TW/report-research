# M0 共用地基 — 設計 spec

> 里程碑 M0（`docs/IMPLEMENTATION_PLAN.md`）。藍圖：`docs/REPORT_GEN_REDESIGN.md` §3 Phase 0 ＋ `docs/QA_REDESIGN.md` §8。

## Goal

把後續所有里程碑（rerank、agentic、逐節生成、忠實度、Typst）要依賴的「地基」先立起來，讓檢索/生成路徑**可測、可安全改動**：型別化檢索 row、抽共用檢索 helper、抽串流狀態機、集中設定、把選篇政策從格式化中分離。

## 核心不變式（Hard Invariant）

**M0 對外行為零改變。** 同一輸入必產同一輸出——問答/研報的 `sources`、脈絡文字、串流內容、SSE 事件、`qa_log`/`report_doc` 落地全部逐字節不變。M0 是純重構，任何「順手改善行為」都不屬於 M0。

## 範圍決策（已與使用者確認）

1. **`retrieve_context` 只服務 RAG 問答（`answer.py`）與深度研報（`report.py`）兩路。** 檢索頁/分頁的 `retrieval.rank_reports` 路徑**完全不動**（其排序/分頁語意不同，強行共用會引入行為差異）。
2. **`app/config.py` 只收 env 驅動常數。** 目前 `os.getenv` 的 `ASK_*`/`REPORT_*` 收進 dataclass；`retrieval.py` 的非-env 字面量（`BAND_WIDTH=0.05`、`DENSE_SCAN_SEARCH=600` 等）**不動**——band 定義收斂是 M6 的工作。

## 非目標（Out of Scope）

- 不收斂 `retrieval.py:117` `BAND_WIDTH=0.05` 與 `answer.py:76` `RELEVANCE_BAND=0.10` 的 band 漂移（→ M6）。
- 不接 reranker（→ M2）、不加 MMR（→ M6）、不改 intent/agentic（→ M4/M5）。
- 不動檢索頁分頁路徑、不動 `db/schema.sql`、不動前端。

---

## 交付單元

### Unit 1 — `app/services/rows.py`：型別化檢索 row

**介面**
```python
class ChunkRow(NamedTuple):
    chunk_id: str
    report_id: str
    file_name: str | None
    market: str | None
    source: str | None
    summary: str | None
    report_date: object          # date / datetime / str / None（沿用現況多型）
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
- 欄序**逐一對齊** `store._meta_columns()`（store.py:228，15 欄）＋ 尾端 `distance`＝16 欄。
- **型別選擇：`NamedTuple`（非 dataclass）**。理由：`NamedTuple` 本身是 tuple，任何漏改的位移存取（`row[0]`、`row[-1]`、`row[-2]`）仍正常運作＝零回歸風險，同時提供具名欄位。

**落點與消費端**
- 建構點（store.py 邊界）：`search_chunks_meta`（store.py:279 `return rows.all()`）、`search_chunks_lexical`（store.py:371）改為包成 `ChunkRow`（`ChunkRow._make(r)`；欄序一致故可直接 `_make`）。
- 改具名存取：
  - `answer.py`：刪除 `:132` 的 `_RID,_FNAME,_MARKET,_RDATE,_CONTENT = 1,2,3,6,14`；`build_context`@236-246 改 `.report_id/.content/.file_name/.market/.report_date`。
  - `retrieval.py`：`hybrid_search` 內 `row[0]`(@95)、`row[-1]`(@99)、`row[-2]`(@100) 與 `rank_reports` 內 `row[1]`(@153)、`row[6]`(@160) 改具名。
  - `web/server.py`：~601 的 `meta_row` 16-tuple 位移拆解、~473 對應處改具名。

> 相容性：`ChunkRow` 是 tuple 子型，跨路去重（以 `row[0]`/`.chunk_id`）、排序、切片皆不變。

### Unit 2 — `app/services/retrieval_pipeline.py`：共用檢索 helper

**介面**
```python
async def retrieve_context(
    session, question, *, k, max_reports, max_passages, max_chars,
    dense_scan, filters=None, now=None, half_life_days=..., <選篇政策參數...>,
) -> tuple[list[Source], str]:
    """embed_query_cached → hybrid_search → build_context 的共用封裝。"""
```
- 取代 `answer.py`（:766 附近）與 `report.py`（:208-218）各自手寫的三步序列。
- 兩路各帶自己的既有參數值（問答 `ASK_*`、研報 `REPORT_*`），**輸出與現況逐位元組相同**。
- 只此兩處呼叫；檢索頁不接。
- **邊界（重要）**：`retrieve_context` **只**封裝 `embed_query_cached → hybrid_search → build_context` 三步。**不**納入 intent 併發分類、off-topic/no-context 短路、串流、`_log_qa`、`SEARCH_EVENT` 處理——那些留在 `answer_question`/`generate_report`。helper 是「拿到 (sources, context)」為止。

### Unit 3 — `app/services/stream_sentinel.py`：`SentinelStreamParser`

**介面**
```python
class SentinelStreamParser:
    def __init__(self, sentinel: str = "[EXT_SOURCES]"): ...
    def feed(self, chunk: str) -> str:   # 回可安全輸出的部分（保留可能跨界的尾段）
    def flush(self) -> str:              # 串流結束時吐出殘餘
    @property
    def sentinel_found(self) -> bool: ...
```
- 抽自 `answer.py:840-885` 的逐 chunk buffering：`hold = len(sentinel)` 尾段保留、`buf.find(sentinel)` 命中即停止輸出後續、末端 flush。
- `answer.py` 串流迴圈改用此類別；`raw_parts`（供 `split_external_sources`@106-126 於串流後切外部來源）維持不變。
- 與 `split_external_sources` 語意一致、並存（一個處理串流增量、一個處理完成全文）。

### Unit 4 — `app/config.py`：集中設定

**介面**
```python
@dataclass(frozen=True)
class Settings:
    # ASK_*（answer.py:45-101 / intent.py / report_gate.py）
    ask_max_reports: int; ask_max_passages: int; ask_max_context_chars: int
    ask_retrieval_k: int; ask_dense_scan: int; ask_recency_weight: float; ...
    # REPORT_*（report.py:34-50）
    report_model: str; report_deep_k: int; report_timeout: float; ...

def get_settings() -> Settings: ...   # 載入一次
```
- 收斂目前散在 `answer.py`/`report.py`/`intent.py`/`report_gate.py` 的 `os.getenv("ASK_*"|"REPORT_*", default)`；**去除 `ASK_DENSE_SCAN` 在 answer.py:51＋report.py:46 的重複定義**。
- **零行為改變關鍵**：每欄 `os.getenv(name, default)` 的 name 與 default 與現況逐字相同。
- `retrieval.py` 的非-env 字面量不納入（範圍決策 2）。

### Unit 5 — `select_reports(...)`：抽出選篇政策純函式

**介面**
```python
def select_reports(scored, *, max_reports, max_passages, max_chars, now,
                   half_life_days, min_reports, relevance_floor,
                   stale_age_days, max_stale) -> list[SelectedReport]:
    """回已排序、已裁切、已定 kept passages 的報告清單（純函式、無 I/O）。"""
```
- 從 `build_context`（answer.py:210-345）切出 @235-313：per-report 聚合、排序鍵 `(best_tier, _relevance_band, _recency_factor, best_fused, report_id)`、過舊軟截斷（`cutoff_active`）、相關度下限（tier 感知）、過舊配額、字數預算修剪。
- `build_context` 只留格式化：`[n]` 連號、`Source` 建構、blocks 組裝、`is_latest` 徽章（@316-343）。
- 為 M6 的 MMR 留插入點（在排序後、字數修剪前）。

---

## 資料流（重構後，行為不變）

```
問答  answer_question → retrieve_context(ASK_*) ┐
                                                 ├→ embed_query_cached → hybrid_search(→ list[(tier,fused,ChunkRow)])
研報  generate_report → retrieve_context(REPORT_*)┘        → build_context = select_reports(政策) + 格式化 → (sources, context)

串流  stream_completion 增量 → SentinelStreamParser.feed()/flush() → token 事件；raw_parts → split_external_sources
設定  所有 ASK_*/REPORT_* ← get_settings()（載入一次）
```

## 測試策略

- **parity golden 測試（最關鍵）**：對固定 `scored` fixture（多篇、跨 tier、含 fresh/stale、觸 `max_chars` 邊界）鎖定 `build_context` 的 `(sources, context)` 逐字輸出；重構前後一致。放 `tests/test_answer.py` 或新 `tests/test_retrieval_pipeline.py`。
- **`SentinelStreamParser` 單元測試**：sentinel 跨 chunk 邊界、被拆兩半、無 sentinel、sentinel 在結尾、sentinel 前後皆有文字。
- **`select_reports` 單元測試**：min_reports 保底、相關度下限、過舊配額、過舊軟截斷、字數預算修剪各一案。
- **config 預設值測試**：斷言 `get_settings()` 每欄預設＝重構前字面值。
- **全套回歸**：`uv run pytest -q` 全綠，重點 `test_answer.py`、`test_report.py`、`test_report_gate.py`、`test_answer_report_wiring.py`。
- **grep 淨空**：無殘留 `_RID`、`row[_`、`meta_row[`、`row[-1]`/`row[-2]`。

## 提交策略

分支 `refactor/m0-shared-foundation`（自 `origin/main`）。5 個邏輯 commit，各自可建、測綠，只 stage 明確路徑：
1. `refactor(檢索): 型別化檢索 row（ChunkRow）` — rows.py ＋ store.py 邊界 ＋ 消費端（answer/retrieval/server）
2. `refactor(檢索): 抽共用 retrieve_context pipeline` — retrieval_pipeline.py ＋ 接 answer/report
3. `refactor(問答): 抽 SentinelStreamParser 串流狀態機` — stream_sentinel.py ＋ 單元測試
4. `refactor(config): 集中 ASK_*/REPORT_* 至 app/config.py`
5. `refactor(檢索): 抽 select_reports 選篇政策純函式`

PR/push 待使用者點頭。

## 部署備註

純重構、無 schema 變更。上線需重啟 `report-mark-web.service`（`make serve` 無 --reload）。
