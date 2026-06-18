# 廷豐智能研報 — 研報市場標籤分類 + 向量檢索 + RAG 問答

把 `研報自動匯入/` 內的券商研究報告：**標上市場標籤 → 全文切塊嵌入 → 存入 pgvector → 語意檢索**。市場標籤對齊 [findb](../findb) 的市場代碼，標籤由 Claude 讀 PDF 判定。

> 完整的端到端運作流程、流程圖、逐階段 I/O、擴展與排錯，見 **[docs/WORKFLOW.md](docs/WORKFLOW.md)**。

## 運作流程一覽

同一套 Python 確定性處理 ＋ Claude 語意標註，依規模分兩條路徑：

**全量生產（現行主路徑）**

```
研報自動匯入/（約 1.5 萬 PDF/docx）
  └─① extract_all.py    並行抽文字＋檔名 metadata、依 file_hash 去重 → data/extracted/all.jsonl
  └─② tag_all_cli.py    Claude CLI(Haiku) 多執行緒讀文字標多維標籤  → data/tags/<file_hash>.json  [Claude]
  └─③ ingest_all.py     串流切塊＋BGE-M3 嵌入＋去重 upsert           → pgvector (research schema)
       └─ resume_corpus.sh  一鍵編排：標註＋導入並行、鎖檔防重入、可中斷續跑
       └─ ingest_lowio.sh   離線大量導入暫關 Postgres durability 降 I/O（結束自動還原）
       └─ web/server.py + static/index.html  查詢網頁   ／   search.py  CLI 檢索
```

**抽樣原型（小規模驗證用）**

```
select_sample.py（分層抽樣 ~80 檔）→ extract_batch.py → make_worklist.py
  → tag_reports.workflow.js（Claude Workflow fan-out）→ run_ingest.py
```

Python 負責確定性處理（解析/抽文字/分塊/嵌入/入庫/檢索），Claude 負責語意標註（讀報告判市場與商品類型/標的等多維標籤）。兩端以 `file_hash` 耦合，支援 checkpoint-resume。

## 快速開始

```bash
# 基礎建設（一次）
docker run -d --name report-mark-postgres \
  -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=research \
  -p 5436:5432 -v report-mark-pgdata:/var/lib/postgresql/data pgvector/pgvector:pg16
docker exec -i report-mark-postgres psql -U postgres -d research < db/schema.sql
uv sync

# 全量生產管線
uv run python scripts/extract_all.py             # 並行抽全量文字（去重）→ data/extracted/all.jsonl
uv run python scripts/tag_all_cli.py --workers 8 # Claude(Haiku) 標多維標籤 → data/tags/*.json（需 claude CLI）
uv run python scripts/ingest_all.py              # 串流切塊＋嵌入入庫（首次下載 BGE-M3 ~2-4GB）
# 或一鍵編排（標註＋導入並行、可中斷續跑）：
bash scripts/resume_corpus.sh

# 查詢網頁（需登入；首次先設共用帳密，未設則 fail-closed 拒啟）
cp .env.example .env          # 編輯填入 REPORT_MARK_ACCESS_USERNAME / _PASSWORD / _SESSION_SECRET
make serve                    # 載入 .env 並啟動 → http://localhost:8097
#   本機 localhost 可直接登入；其他裝置請走受保護的 HTTPS 入口
#   注意：make serve 無 --reload，改了程式碼要「重啟」才生效（靜態 HTML 即時生效）

# CLI 檢索（免登入）
uv run python scripts/search.py "AI 伺服器散熱需求"
uv run python scripts/search.py "利率與殖利率" --market MACRO
```

> 小規模驗證可改走抽樣原型：`select_sample.py → extract_batch.py → make_worklist.py →`（Claude Code 執行 `workflows/tag_reports.workflow.js`）`→ run_ingest.py`。更多捷徑見 `make help`，逐階段細節見 [docs/WORKFLOW.md](docs/WORKFLOW.md)。

## 市場標籤（對齊 findb）

findb `instruments.market` 代碼（大寫）：`TW`(台股) `US`(美股) `HK`(港股) `CN`(陸股) `FX`(外匯) `WTX`(台指期) `MACRO`(總經) `GLOBAL`(全球) `CRYPTO`(加密)。

findb 無「債券」「原物料」獨立市場 → 歸到最接近者：**債券→`MACRO`、原物料→`GLOBAL`**。對映在 `app/services/tagging.py`；既有資料以 `scripts/align_findb_markets.py` 確定性重映射（不需重跑 Claude）。

> 市場只是 Claude 標註的維度之一。每篇另標：**商品類型**（股票/指數/期貨/選擇權/ETF/債券/外匯/原物料/加密）、**個股／期貨關聯**（`relates_stock`／`relates_futures`）、**具體標的**（個股代碼、期貨品種），加上 `is_research` 與 `confidence`；而券商來源、報告日期、報告類型則由檔名解析（`filename.py`）。完整詞表見 [docs/WORKFLOW.md](docs/WORKFLOW.md)。

## 專案結構

```
research/                          ← 資料庫 schema：research_report + report_chunk（HNSW cosine）
db/schema.sql

app/services/
  filename.py    檔名解析（抽樣分桶 + 股票代碼/券商/日期，不決定市場）+ source_display
  extract.py     PDF/docx 抽文字、掃描檔偵測、SHA256 file_hash
  chunk.py       段落邊界分塊（600 字 / 80 重疊）
  embed.py       BGE-M3 dense 1024 維（CPU，單例延遲載入）
  tagging.py     findb 市場代碼 + 商品類型/標的詞表 + 多維標註指令 + tag JSON 解析（市場/商品類型/關聯/標的）
  store.py       file_hash 去重 upsert + 雙路（dense／字面）召回查詢
  textnorm.py    查詢/內容正規化（NFKC、去空白、小寫）供字面比對
  retrieval.py   混合檢索編排：dense＋字面召回 → 去重 → tier 融合排序
  db.py          async SQLAlchemy 引擎（env REPORT_MARK_DB_URL）

scripts/  ── 全量生產
  extract_all.py          並行抽全量文字 + metadata、file_hash 去重
  tag_all_cli.py          Claude CLI(Haiku) 多執行緒多維標註（失敗記 tag_failures.log）
  ingest_all.py           串流切塊 → 嵌入 → upsert（失敗記 ingest_failures.log）
  ingest_lowio.sh         離線大量導入：暫關 durability 降 I/O（結束自動還原）
  resume_corpus.sh        編排：標註＋導入並行、鎖檔防重入、可續跑
scripts/  ── 抽樣原型 / 工具
  select_sample.py        分層抽樣 ~80 檔
  extract_batch.py        抽樣批次抽文字 + metadata
  make_worklist.py        過濾 + 分批（resume-aware）
  run_ingest.py           抽樣切塊 → 嵌入 → upsert
  normalize_chunks.py     內容正規化 → content_norm（供字面比對）
  backfill_full_text.py   回填 full_text 欄
  generate_summaries.py   為缺摘要的報告生成 2-3 句中文摘要（Sonnet，補 summary IS NULL，冪等可續傳）→ make summaries
  align_findb_markets.py  中文標籤 → findb 代碼（一次性、冪等）
  search.py               CLI 語意檢索（可 --market 過濾）

workflows/
  tag_reports.workflow.js Claude 分批 fan-out 市場標註

web/
  server.py               FastAPI（BGE-M3 常駐）/api/stats /api/markets /api/search /api/reports /api/report/{id} /api/ask /api/history
  static/index.html       查詢介面（雙欄、檢索＋問答、列表/表格檢視、高亮、即打即查）
```

## 查詢網頁

> **需登入**：全站以單一**共用帳號＋密碼**把關（由環境變數設定，見[快速開始](#快速開始)）。未登入自動導向 `/login`，右上可登出；session 以簽章 cookie 維持 7 天（滑動到期）。外網部署見 [docs/EXTERNAL_ACCESS.md](docs/EXTERNAL_ACCESS.md)。

雙欄側邊版面（手機收單欄），頂部有**檢索／問答**切換。

**檢索**：搜尋框在內容區上方，結果預設以**列表**（依市場／報告類型／日期(月)分組，右上可切「分組依據」）呈現，也可切**表格**（右上角圖示切換）。每筆顯示市場標籤、商品類型、標的、券商來源、報告日期、命中片段數、相關度 %，以及**2-3 句中文摘要**（列表完整顯示；表格於名稱 hover 顯示）讓你不必開全文就能掌握大意；**搜尋時**列表還會列出最相關的**命中片段**並將關鍵字**黃底高亮**（可展開更多片段）；點任一筆即**內嵌原始 PDF**（彈窗頂部亦顯示摘要）。摘要由 `make summaries` 離線生成。

**問答**：以自然語言提問，RAG 檢索＋串流回答、附**引用來源**（可點開原始報告），側欄保留**歷史問答**（可重看／刪除）。

左側可篩選**市場 / 商品類型 / 標的（個股·期貨）/ 報告類型**（皆單選），並切換**排序**：搜尋＝相關度（預設）/ 日期新→舊 / 日期舊→新；瀏覽＝日期新→舊（預設）/ 日期舊→新。

API：`/api/stats`、`/api/markets`、`/api/search`、`/api/reports`（瀏覽）、`/api/report/{id}/full`、`/api/report/{id}/file`、`/api/ask`（RAG 問答，SSE 串流）、`/api/history`（問答歷史；`DELETE /api/history/{qa_id}` 刪除單筆）、`/api/feedback`（讚／倒讚）（詳見 [docs/WORKFLOW.md](docs/WORKFLOW.md#web-api)）。

## 環境

| 項目 | 值 |
|------|-----|
| Python | 3.11+（uv）|
| 向量 DB | `pgvector/pgvector:pg16`，容器 `report-mark-postgres`，host port 5436 |
| 嵌入 | BGE-M3 dense 1024 維（CPU）|
| Web | uvicorn，port 8097 |
| 登入 | 共用帳密，env `REPORT_MARK_ACCESS_USERNAME`／`_PASSWORD`（未設則 fail-closed 拒啟）＋ `REPORT_MARK_SESSION_SECRET`；`make serve` 載入 repo 根 `.env` |
