# report-mark 運作流程

研報市場標籤分類 + 向量檢索系統的端到端運作說明。

系統把 `研報自動匯入/` 內的券商研究報告，經過 **抽文字 → Claude 多維標註 → 切塊嵌入 → pgvector 入庫 → 語意檢索**，產出可依 **市場／商品類型／標的／報告類型** 過濾並排序的語意搜尋服務。市場標籤對齊 [findb](../../findb) 的市場代碼。

> 有兩條路徑共用同一套處理：**全量生產**（現行主路徑，`extract_all → tag_all_cli → ingest_all`，可一鍵編排續跑）與 **抽樣原型**（小規模驗證，`select_sample → extract_batch → make_worklist → tag_reports.workflow → run_ingest`）。

---

## 全景流程圖（全量生產）

```mermaid
flowchart TD
    SRC["研報自動匯入/<br/>約 1.5 萬 PDF/docx"]

    subgraph PY1["① 抽文字 (Python, 並行)"]
      EX["extract_all.py<br/>filename.py + extract.py<br/>file_hash 去重"]
    end
    subgraph CL["② 多維標註 (Claude)"]
      TG["tag_all_cli.py<br/>Claude CLI(Haiku) 多執行緒"]
    end
    subgraph PY3["③ 嵌入入庫 (Python, 串流)"]
      IN["ingest_all.py<br/>chunk.py + embed.py + store.py"]
    end

    DB[("pgvector / PostgreSQL 16<br/>schema research<br/>research_report + report_chunk")]

    SRC --> EX
    EX -->|"data/extracted/all.jsonl"| TG
    EX -.->|"extracted text"| IN
    TG -->|"data/tags/&lt;file_hash&gt;.json<br/>(市場/商品類型/標的…)"| IN
    IN -->|"BGE-M3 1024d + HNSW + trgm"| DB

    ORC["resume_corpus.sh<br/>標註＋導入並行、可續跑"] -.-> TG
    ORC -.-> IN

    DB --> WEB["web/server.py (FastAPI)<br/>BGE-M3 常駐 + 多維篩選/排序"]
    DB --> CLI["search.py (CLI)"]
    WEB --> UI["web/static/index.html<br/>查詢介面"]
```

**耦合鍵**：`file_hash`（SHA256）貫穿所有階段，讓 Python（確定性處理）與 Claude（語意標註）兩端解耦，並支援 checkpoint-resume。

> **抽樣原型路徑**（小規模驗證）：`select_sample.py（分層抽樣 ~80 檔）→ extract_batch.py → make_worklist.py → tag_reports.workflow.js（Claude Workflow 5-agent fan-out）→ run_ingest.py`，產物為 `data/extracted/sample.jsonl` 與 `data/worklist_batch*.json`。邏輯與全量路徑相同，只是規模較小。

---

## 責任分工

| 由誰負責 | 工作 |
|----------|------|
| **Python**（確定性、可重現） | 檔名解析、抽文字、掃描檔偵測、分塊、BGE-M3 嵌入、去重入庫、檢索 |
| **Claude**（語意理解） | 讀報告文字判定多維標籤：主要市場（findb 代碼）、is_research、confidence、商品類型、個股/期貨關聯、具體標的 |

> 券商來源、報告日期、報告類型、股票代碼等 metadata 由**檔名解析**（`filename.py`）取得，不經 Claude。

---

## 資料層

| 層 | 位置 | 內容 |
|----|------|------|
| 來源 | `研報自動匯入/` | 原始 PDF/docx（唯讀，不更動）|
| 中繼產物 | `data/` | 抽出文字 JSONL（`all.jsonl`／`sample.jsonl`）、工作清單、tag JSON、執行 log |
| Canonical | `research.research_report` | 每篇一列：市場/商品類型/標的等標籤 ＋ 檔名 metadata ＋ `full_text` |
| 向量 | `research.report_chunk` | 全文切塊 ＋ `vector(1024)`（HNSW cosine）＋ `content_norm`（pg_trgm 字面比對）|

**`research.research_report` 欄位**：`id`、`file_hash`(唯一)、`file_name`/`file_path`、`market`、`is_research`、`confidence`、`stock_code`、`company_name`、`source`、`report_date`、`report_type`、`language`、`instrument_types[]`、`relates_stock`、`relates_futures`、`stock_targets[]`、`futures_targets[]`、`full_text`、`created_at`。

**`research.report_chunk` 欄位**：`id`、`report_id`(FK)、`chunk_index`、`content`、`embedding vector(1024)`、`content_norm`（`GENERATED STORED`：NFKC→去空白→小寫，對齊 `textnorm.norm_for_match()`）。

**索引**：`report_chunk.embedding` HNSW(cosine)、`content_norm` GIN(trgm)；`research_report` 的 `market` btree、`instrument_types`/`stock_targets`/`futures_targets` GIN。DB schema 定義於 [`db/schema.sql`](../db/schema.sql)（DDL 皆 `IF NOT EXISTS`，可冪等套用於既有庫）。

---

## 逐階段說明（全量生產）

### ① 抽文字 — `scripts/extract_all.py`
- **輸入**：`研報自動匯入/` 全部檔案（遞迴掃 `.pdf`/`.docx`/`.doc`）
- **做什麼**：`ProcessPoolExecutor`（預設 `--workers 16`）並行對每檔抽純文字，依 `file_hash` 去重（保留首見）；單檔失敗不阻斷整批；統計 unique / 重複 / 行政檔 / 掃描空白 / 失敗 / 候選報告數
  - `extract.py`：PDF（pypdf）/docx（python-docx）抽文字、算 SHA256 `file_hash`、偵測掃描檔（可抽文字 < 100 字 → `scanned=true`）、判語言
  - `filename.py`：解析股票代碼、券商來源、報告日期、報告類型、是否行政檔
- **輸出**：`data/extracted/all.jsonl`（每行一筆，含 text + metadata）
- **指令**：`uv run python scripts/extract_all.py [--workers N]`

### ② 多維標註 — `scripts/tag_all_cli.py`（Claude CLI）
- **輸入**：`data/extracted/all.jsonl`（候選＝排除 `_failed`／`is_admin`／`scanned`）
- **做什麼**：`ThreadPoolExecutor`（預設 `--workers 8`）；每個 worker 以 subprocess 呼叫 `claude -p <prompt> --model claude-haiku-4-5`（headless、`cwd=/tmp` 避免載入專案 CLAUDE.md），prompt＝`TAG_INSTRUCTION` ＋ 檔名 ＋ 報告文字前 `--excerpt`（預設 10000）字；解析 JSON、正規化各欄、原子寫檔（`.tmp` → rename）
  - 失敗最多重試 2 次；仍失敗則記 `data/tag_failures.log`
  - 已有有效 tag 者跳過（冪等、可續跑）
- **輸出**：`data/tags/<file_hash>.json`（多維標籤，schema 見下方）
- **指令**：`uv run python scripts/tag_all_cli.py [--workers 8] [--limit N] [--excerpt 10000]`
- **替代（原型）**：`workflows/tag_reports.workflow.js` — 由 Claude Code 以 Workflow 工具執行，5 個 agent 各讀一個 `worklist_batch*.json`、用 Read 開 PDF、Write 寫 tag；標註規則與 CLI 相同。

### ③ 嵌入入庫 — `scripts/ingest_all.py`
- **輸入**：`data/extracted/all.jsonl` ＋ `data/tags/*.json`
- **過濾串**（各自計數）：`is_admin` → `scanned` → 已在 DB（啟動時一次撈出既有 `file_hash` 集合做 O(1) 比對）→ 無 tag → `is_research=false` 或無 market
- **做什麼**：串流逐行讀 `all.jsonl`（不整檔載入記憶體）；合格檔以 `chunk.py` 切塊（600 字 / 80 重疊、段落邊界）→ `embed.py` BGE-M3 批次嵌入（1024 維、`--batch-size` 預設 32）→ `store.py` 依 `file_hash` 去重 upsert（先刪後插）寫入 pgvector；每 25 篇印速率/ETA；結束 `ANALYZE report_chunk`
  - 單檔錯誤記 `data/ingest_failures.log` 後 rollback 續跑（長跑韌性）；無 `--force`，重灌需先刪列
- **輸出**：`research.research_report` ＋ `research.report_chunk`
- **指令**：`uv run python scripts/ingest_all.py [--limit N] [--batch-size 32]`

### 編排與離線優化
- **`scripts/resume_corpus.sh`**：一鍵編排——鎖檔（`data/.resume_corpus.lock` + PID 檢查）防重入，並行起 `tag_all_cli.py` 與 `ingest_all.py`，待首輪導入消化 backlog → 等標註全數完成 → 補跑 catch-up 導入；各階段時間戳記寫 `data/resume_orchestrator_*.log`。`bash scripts/resume_corpus.sh`
- **`scripts/ingest_lowio.sh`（或 `make ingest-lowio`）**：`ingest_all.py` 的包裝，**離線大量導入**時以 `ALTER SYSTEM` 暫關 Postgres durability（`fsync`/`full_page_writes`/`synchronous_commit`）降磁碟 I/O，並用 `trap` 確保正常/錯誤/Ctrl-C 都會還原。⚠️ **僅限 DB 未對外服務時使用**（關 fsync 期間若主機/DB 崩潰，research 庫不可復原，但可由原始報告重新導入）。中斷未還原時用 `make restore-durability` 重設。

### 工具（一次性 / 維運）
- **`scripts/normalize_chunks.py`**：清理 `report_chunk.content`（去 CJK 間隙、收合空白/換行），觸發 `content_norm` 生成欄重算（供字面比對），冪等、結束 `ANALYZE`；不重嵌入、不重切塊。
- **`scripts/backfill_full_text.py`**：由 `sample.jsonl` 回填 `research_report.full_text`（欄位後加時補；僅抽樣路徑）。
- **`scripts/align_findb_markets.py`**：把既有中文市場標籤確定性重映射為 findb 代碼（同改 `data/tags/*.json` 與 DB），冪等、不需重跑 Claude。

### 檢索 — 兩種介面
- **CLI** `scripts/search.py`：嵌入查詢 → cosine top-k，可加 `--market <findb 代碼>` 過濾
- **網頁** `web/server.py` + `web/static/index.html`：FastAPI 後端在啟動時把 BGE-M3 常駐記憶體，前端查詢介面支援多維篩選與排序（見 [Web API](#web-api)）。**全站需登入**（共用帳密，env 設定；未登入導向 `/login`，可登出）——認證細節見 `web/auth.py` 與 [docs/EXTERNAL_ACCESS.md](EXTERNAL_ACCESS.md)

---

## 標籤維度（對齊 findb）

Claude 每篇輸出 8 個語意欄位（`app/services/tagging.py` 的 `MarketTag`）：

**① 市場 `market`** — findb `instruments.market`（大寫）；非研究報告為 `null`：

| 代碼 | 顯示 | 涵蓋 |
|------|------|------|
| `TW` | 台股 | 台灣個股/產業 |
| `US` | 美股 | 美國個股/市場（英文外資、webcast/flyer）|
| `HK` | 港股 | 香港掛牌 |
| `CN` | 陸股 | 中國 A 股 |
| `FX` | 外匯 | 匯率/FX |
| `WTX` | 台指期 | 期貨/選擇權/部位限制 |
| `MACRO` | 總經 | 總經/利率；**債券/固定收益歸此** |
| `GLOBAL` | 全球 | 跨市場配置/全球策略；**原物料/商品歸此** |
| `CRYPTO` | 加密 | 加密貨幣 |

findb 無「債券」「原物料」獨立市場 → 歸最接近者（債券→`MACRO`、原物料→`GLOBAL`）。對映在 `tagging.py`（`MARKETS` / `MARKET_DISPLAY` / `LEGACY_TO_FINDB` / `normalize_market()`）；前端 `MARKET_META` 把代碼顯示為中文 + 配色。

**② `is_research`**（bool，有 market 時預設 true）／ **③ `confidence`**（0–1）：非研究檔（人事異動、行事曆、組織架構、部位限制公告、webcast flyer、帳單等）標 `is_research=false`，不進向量檢索。

**④ 商品類型 `instrument_types[]`** — 多選，詞表：

| 代碼 | equity | index | futures | options | etf | bond | fx | commodity | crypto |
|------|--------|-------|---------|---------|-----|------|----|-----------|--------|
| 顯示 | 股票 | 指數 | 期貨 | 選擇權 | ETF | 債券 | 外匯 | 原物料 | 加密 |

**⑤ `relates_stock`**（bool）／ **⑥ `relates_futures`**（bool）：是否對「選股/個股交易」「期貨/指數部位」有參考價值。

**⑦ 個股標的 `stock_targets[]`**：報告明確聚焦/評等的四位股票代碼，至多 8 檔。
**⑧ 期貨標的 `futures_targets[]`**：詞表 `台指期 / 小型台指 / 電子期 / 金融期 / 個股期貨 / 其他`。

> `report_type`（速報/週報/策略…）、`source`（券商）、`report_date`、`stock_code`、`language` 由 `filename.py` 從檔名解析，非 Claude 標註。

---

## Web API

| 端點 | 說明 |
|------|------|
| `GET /api/stats` | 總篇數、總片段數，各市場代碼／商品類型／報告類型的篇數 |
| `GET /api/markets` | findb 市場代碼清單 |
| `GET /api/search` | 語意檢索並**依報告分組**。參數：`q`（必填）、`market`、`instrument_type`、`relates_stock`、`relates_futures`、`report_type`、`sort`（`relevance` 預設／`date_desc`／`date_asc`）、`k`、`passages`。每篇回傳 best_score、命中片段數、券商/日期/類型/標的 metadata、清理後（去除 PDF 雜亂排版）的片段 |
| `GET /api/reports` | 無關鍵字瀏覽：依 `sort`（`date_desc` 預設／`date_asc`）列出，支援與 search 相同的篩選參數 ＋ `limit`/`offset` 分頁 |
| `GET /api/report/{id}/full` | 單篇 metadata 與原始檔狀態（供前端完整報告 modal）|
| `GET /api/report/{id}/file` | 回傳原始檔（PDF 以 inline 內嵌、其他下載）|
| `POST /api/ask` | RAG 問答：SSE 串流回答，行內 `[n]` 引用對應來源報告；寫入 `qa_log`。以整個語料庫為範圍 |
| `GET /api/history` | 最近的問答歷史（供側欄歷史清單）。`DELETE /api/history/{qa_id}` 刪除單筆 |
| `POST /api/feedback` | 記錄使用者對某次回答的讚／倒讚（`qa_id` + `value`）|
| `GET /` | 單頁前端（檢索／問答兩種模式）|
| `GET`/`POST /login` | 登入頁與登入提交（共用帳密；**唯一免登入端點**）|
| `POST /logout` | 清除 session cookie 並導回 `/login` |

> **認證**：除 `/login` 外所有端點皆需登入（deny-by-default 中介層）。未帶有效 session cookie 時 `/api/*` 回 **401**、其餘導向 **`/login`**；`/static/*` 也受保護。憑證為單一共用帳密（env `REPORT_MARK_ACCESS_USERNAME`/`_PASSWORD`，fail-closed），cookie 以 `REPORT_MARK_SESSION_SECRET` 簽章、7 天滑動到期，並對登入失敗做每 IP 限流。

前端特性：雙欄側邊版面（手機收單欄）、頂部**檢索／問答**模式切換。檢索結果預設**列表**（依市場／報告類型／日期(月)分組，右上可切「分組依據」），可切**表格**（右上角圖示）；市場／商品類型／標的／報告類型篩選與排序、同篇研報合併、點任一筆「內嵌完整報告 PDF」、即打即查（debounce 450ms）、骨架載入。問答模式：RAG 串流回答＋可點引用來源、側欄歷史問答（可重看／刪除）。

---

## 完整重跑指令

```bash
# 0) 基礎建設（一次）
docker run -d --name report-mark-postgres \
  -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=research \
  -p 5436:5432 -v report-mark-pgdata:/var/lib/postgresql/data pgvector/pgvector:pg16
docker exec -i report-mark-postgres psql -U postgres -d research < db/schema.sql
uv sync
#（或用捷徑：make db && make schema && make deps；其餘見 make help）

# 全量生產
uv run python scripts/extract_all.py             # 並行抽全量文字（去重）→ data/extracted/all.jsonl
uv run python scripts/tag_all_cli.py --workers 8 # Claude(Haiku) 標多維標籤（需 claude CLI）→ data/tags/*.json
uv run python scripts/ingest_all.py              # 串流切塊＋嵌入入庫（首次下載 BGE-M3 ~2-4GB）
#   ↑ 三步可改一鍵編排（標註＋導入並行、可中斷續跑）：
bash scripts/resume_corpus.sh
#   離線大量導入降 I/O（DB 未對外服務時）：make ingest-lowio

# 啟動查詢網頁（需登入；首次先 cp .env.example .env 填帳密，未設則 fail-closed 拒啟）
make serve   # 載入 repo 根 .env 啟動 → http://localhost:8097（本機 localhost 可直接登入；無 --reload，改碼後須重啟才生效）

# CLI 檢索
uv run python scripts/search.py "AI 伺服器散熱需求"
uv run python scripts/search.py "利率與殖利率" --market MACRO
```

> **抽樣原型**（小規模）：`uv run python scripts/select_sample.py` → `extract_batch.py` → `make_worklist.py` →（Claude Code 執行 `workflows/tag_reports.workflow.js`）→ `run_ingest.py`。

---

## 環境與基礎建設

| 項目 | 值 |
|------|-----|
| Python | 3.11+（uv 管理）|
| 向量 DB | `pgvector/pgvector:pg16`，容器 `report-mark-postgres`，host port **5436** |
| DB 連線 | env `REPORT_MARK_DB_URL`（預設 `postgresql+asyncpg://postgres:postgres@localhost:5436/research`）|
| 嵌入模型 | BGE-M3 dense，1024 維，CPU（首次 lazy-load 下載 ~2-4GB）|
| 標註 | `tag_all_cli.py` 需 `claude` CLI（`--model claude-haiku-4-5`，預設 8 worker 執行緒）|
| 抽文字 | `extract_all.py` 用 multiprocessing（預設 16 worker）|
| Web 服務 | uvicorn，port **8097**（`make serve`，無 `--reload`，改碼後須重啟）|
| 登入 | 共用帳密 env `REPORT_MARK_ACCESS_USERNAME`／`_PASSWORD`（fail-closed）＋簽章金鑰 `REPORT_MARK_SESSION_SECRET`；由 `make serve` 載入 repo 根 `.env`（已 gitignore）。本機 `localhost` 可直連，其他裝置請走 HTTPS 入口 |

> 此機 `docker compose` 子指令不可用，故用 `docker run` 起單一容器。背景編排（`resume_corpus.sh`）以 setsid/nohup 方式長跑。

---

## 全量生產現狀

全量語料（`研報自動匯入/`，約 1.5 萬份）已是**現行生產路徑**，非未來規劃：

- `extract_all.py → tag_all_cli.py → ingest_all.py` 為主路徑；`resume_corpus.sh` 提供鎖檔防重入、標註＋導入並行、可中斷續跑的編排。
- `file_hash` 去重與「已標/已導入」檢查讓全流程可隨時中斷續跑。
- **掃描型 PDF 無 OCR**：`extract_all.py` 以可抽文字 < 100 字判 `scanned=true`，導入階段以 `skip_scanned` 計數略過——刻意排除，不提供 OCR。
- 標註/導入失敗各自記 `data/tag_failures.log`、`data/ingest_failures.log` 供事後排查。

---

## 排錯

| 症狀 | 檢查 |
|------|------|
| 查詢很慢 / 第一次卡住 | BGE-M3 首次下載 ~2-4GB；server 啟動時已暖機，看 uvicorn log |
| 改了程式/前端卻沒生效 | `make serve` 無 `--reload`：靜態 HTML 即時生效，但路由/中介層在**啟動時**載入 → 須**重啟** `make serve` 才載入新碼（常見誤判：看到新 UI 卻打到舊路由）|
| App 啟動即報錯退出 | 未設 `REPORT_MARK_ACCESS_USERNAME`／`_PASSWORD`（fail-closed）→ 補進 `.env` 再 `make serve` |
| 一直回登入頁 / 登出按 404 | 多半是舊程序還在跑（未重啟，見上）；或 `REPORT_MARK_SESSION_SECRET` 每次重啟變動（請在 `.env` 固定一組）|
| `/api/stats` 連不上 | 容器是否運行 `docker ps`；port 5436 是否被佔 |
| 入庫筆數偏少 | 看 `ingest_all.py` summary 的各類 skip（admin/scanned/untagged/non_research/exists）|
| 標註有缺漏 | 抽查 `data/tags/<hash>.json`；看 `data/tag_failures.log`；`tag_all_cli.py` 可直接重跑（冪等續標）|
| 導入有失敗 | 看 `data/ingest_failures.log`；修正後重跑 `ingest_all.py`（已導入者自動略過）|
| 啟動服務後磁碟吃滿 | 全量導入 I/O 重；離線時改 `make ingest-lowio`，並確認 Defender 已排除 vhdx |
| 市場顯示為代碼非中文 | 前端 `MARKET_META` 是否含該代碼；DB 是否已跑 `align_findb_markets.py` |
| 標註結果不對 | 抽查 `data/tags/<hash>.json`；市場代碼須在 `MARKETS` 內 |
