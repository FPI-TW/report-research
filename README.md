# 廷豐研報 — 研報市場標籤分類 + 向量檢索

把 `研報自動匯入/` 內的券商研究報告：**標上市場標籤 → 全文切塊嵌入 → 存入 pgvector → 語意檢索**。市場標籤對齊 [findb](../findb) 的市場代碼，標籤由 Claude 讀 PDF 判定。

> 完整的端到端運作流程、流程圖、逐階段 I/O、擴展與排錯，見 **[docs/WORKFLOW.md](docs/WORKFLOW.md)**。

## 運作流程一覽

```
研報自動匯入/ (15,866 PDF/docx)
  └─① select_sample.py      分層抽樣 ~80 檔        → data/sample_manifest.json
  └─② extract_batch.py      抽文字 + 檔名 metadata → data/extracted/sample.jsonl
  └─③ make_worklist.py      過濾 + 分批            → data/worklist_batch0-4.json
  └─④ tag_reports.workflow  Claude 讀 PDF 標市場   → data/tags/<file_hash>.json   [Claude]
  └─⑤ run_ingest.py         切塊 + BGE-M3 嵌入     → pgvector (research schema)
       └─ web/server.py + index.html  iOS 風格查詢網頁
       └─ search.py                   CLI 檢索
```

Python 負責確定性處理（解析/抽文字/分塊/嵌入/入庫/檢索），Claude 負責語意標註（讀 PDF 判市場）。兩端以 `file_hash` 耦合，支援 checkpoint-resume。

## 快速開始

```bash
# 基礎建設（一次）
docker run -d --name report-mark-postgres \
  -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=research \
  -p 5436:5432 -v report-mark-pgdata:/var/lib/postgresql/data pgvector/pgvector:pg16
docker exec -i report-mark-postgres psql -U postgres -d research < db/schema.sql
uv sync

# 管線
uv run python scripts/select_sample.py
uv run python scripts/extract_batch.py
uv run python scripts/make_worklist.py
#  ④ Claude 標註：由 Claude Code 執行 workflows/tag_reports.workflow.js（Workflow 工具）
uv run python scripts/run_ingest.py          # 首次會下載 BGE-M3 ~2-4GB

# 查詢
uv run uvicorn web.server:app --host 0.0.0.0 --port 8097   # → http://localhost:8097
uv run python scripts/search.py "AI 伺服器散熱需求"
uv run python scripts/search.py "利率與殖利率" --market MACRO
```

## 市場標籤（對齊 findb）

findb `instruments.market` 代碼（大寫）：`TW`(台股) `US`(美股) `HK`(港股) `CN`(陸股) `FX`(外匯) `WTX`(台指期) `MACRO`(總經) `GLOBAL`(全球) `CRYPTO`(加密)。

findb 無「債券」「原物料」獨立市場 → 歸到最接近者：**債券→`MACRO`、原物料→`GLOBAL`**。對映在 `app/services/tagging.py`；既有資料以 `scripts/align_findb_markets.py` 確定性重映射（不需重跑 Claude）。

## 專案結構

```
research/                          ← 資料庫 schema：research_report + report_chunk（HNSW cosine）
db/schema.sql

app/services/
  filename.py    檔名解析（抽樣分桶 + 股票代碼/券商/日期，不決定市場）+ source_display
  extract.py     PDF/docx 抽文字、掃描檔偵測、SHA256 file_hash
  chunk.py       段落邊界分塊（600 字 / 80 重疊）
  embed.py       BGE-M3 dense 1024 維（CPU，單例延遲載入）
  tagging.py     findb 市場代碼 + Claude 標註指令 + tag 解析（MARKETS/MARKET_DISPLAY/LEGACY_TO_FINDB）
  store.py       file_hash 去重 upsert + cosine 分組檢索
  db.py          async SQLAlchemy 引擎（env REPORT_MARK_DB_URL）

scripts/
  select_sample.py        分層抽樣
  extract_batch.py        批次抽文字 + metadata
  make_worklist.py        過濾 + 分批（resume-aware）
  run_ingest.py           切塊 → 嵌入 → upsert pgvector
  align_findb_markets.py  中文標籤 → findb 代碼（一次性、冪等）
  search.py               CLI 語意檢索（可 --market 過濾）

workflows/
  tag_reports.workflow.js Claude 分批 fan-out 市場標註

web/
  server.py               FastAPI（BGE-M3 常駐）/api/stats /api/markets /api/search
  static/index.html       iOS 風格查詢介面（雙欄、卡片網格、高亮、即打即查）
```

## 查詢網頁

iOS 風格、雙欄側邊版面（手機收單欄）。結果**依報告分組**：每篇顯示市場標籤、券商來源、報告日期、命中片段數、相關度 %，查詢關鍵字高亮、可展開更多片段。左側市場 chips 可過濾。

API：`/api/stats`、`/api/markets`、`/api/search?q=&market=&k=&passages=`（詳見 [docs/WORKFLOW.md](docs/WORKFLOW.md#web-api)）。

## 環境

| 項目 | 值 |
|------|-----|
| Python | 3.11+（uv）|
| 向量 DB | `pgvector/pgvector:pg16`，容器 `report-mark-postgres`，host port 5436 |
| 嵌入 | BGE-M3 dense 1024 維（CPU）|
| Web | uvicorn，port 8097 |
