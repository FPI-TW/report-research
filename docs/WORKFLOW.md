# report-mark 運作流程

研報市場標籤分類 + 向量檢索系統的端到端運作說明。

系統把 `研報自動匯入/` 內的券商研究報告，經過 **抽樣 → 抽文字 → Claude 市場標註 → 切塊嵌入 → pgvector 入庫 → 語意檢索**，產出可依市場過濾的語意搜尋服務。市場標籤對齊 [findb](../../findb) 的市場代碼。

---

## 全景流程圖

```mermaid
flowchart TD
    SRC["研報自動匯入/<br/>15,866 PDF/docx"]

    subgraph PY1["① 抽樣 (Python)"]
      SS["select_sample.py<br/>分層抽樣 6 桶"]
    end
    subgraph PY2["② 抽文字 (Python)"]
      EX["extract_batch.py<br/>filename.py + extract.py"]
    end
    subgraph PY3["③ 工作清單 (Python)"]
      WL["make_worklist.py<br/>過濾行政/掃描 + 分批"]
    end
    subgraph CL["④ 市場標註 (Claude)"]
      TG["tag_reports.workflow.js<br/>5 agent fan-out 讀 PDF"]
    end
    subgraph PY4["⑤ 嵌入入庫 (Python)"]
      IN["run_ingest.py<br/>chunk.py + embed.py + store.py"]
    end

    DB[("pgvector / PostgreSQL 16<br/>schema research<br/>research_report + report_chunk")]

    SRC --> SS
    SS -->|"data/sample_manifest.json (80)"| EX
    EX -->|"data/extracted/sample.jsonl"| WL
    WL -->|"data/worklist_batch0-4.json"| TG
    TG -->|"data/tags/&lt;file_hash&gt;.json<br/>(findb 市場代碼)"| IN
    EX -.->|"extracted text"| IN
    IN -->|"BGE-M3 1024d + HNSW"| DB

    DB --> WEB["web/server.py (FastAPI)<br/>BGE-M3 常駐 + /api/search"]
    DB --> CLI["search.py (CLI)"]
    WEB --> UI["web/static/index.html<br/>iOS 風格查詢介面"]

    ALN["align_findb_markets.py<br/>(一次性重映射)"] -.->|"中文標籤→findb 代碼"| DB
```

**耦合鍵**：`file_hash`（SHA256）貫穿所有階段，讓 Python（確定性處理）與 Claude（語意標註）兩端解耦，並支援 checkpoint-resume。

---

## 責任分工

| 由誰負責 | 工作 |
|----------|------|
| **Python**（確定性、可重現） | 檔名解析、抽文字、掃描檔偵測、分塊、BGE-M3 嵌入、去重入庫、檢索 |
| **Claude**（語意理解） | 讀 PDF 內容判定「主要市場」，輸出 findb 市場代碼 |

---

## 資料層

| 層 | 位置 | 內容 |
|----|------|------|
| 來源 | `研報自動匯入/` | 原始 PDF/docx（唯讀，不更動）|
| 中繼產物 | `data/` | 抽樣清單、抽出文字 JSONL、工作清單、tag JSON |
| Canonical | `research.research_report` | 每篇一列：市場代碼、is_research、信心、檔名 metadata |
| 向量 | `research.report_chunk` | 全文切塊 + `vector(1024)`，HNSW cosine 索引 |

DB schema 定義於 [`db/schema.sql`](../db/schema.sql)。

---

## 逐階段說明

### ① 抽樣 — `scripts/select_sample.py`
- **輸入**：`研報自動匯入/` 全部檔案
- **做什麼**：用 `filename.py` 把語料分 6 桶（行政 / 英文外資 / 週期報告 / docx / 個股 / 其他），各桶均勻抽樣湊 ~80 檔做原型
- **輸出**：`data/sample_manifest.json`

### ② 抽文字 — `scripts/extract_batch.py`
- **輸入**：`data/sample_manifest.json`
- **做什麼**：對每檔
  - `extract.py`：PDF（pypdf）/docx（python-docx）抽純文字、算 SHA256 `file_hash`、偵測掃描檔（可抽文字 < 100 字 → `scanned=true`）、判語言
  - `filename.py`：解析股票代碼、券商來源、報告日期、報告類型、是否行政檔
- **輸出**：`data/extracted/sample.jsonl`（每行一筆，含 text + metadata）

### ③ 工作清單 — `scripts/make_worklist.py`
- **輸入**：`data/extracted/sample.jsonl`
- **做什麼**：過濾掉檔名可判定的行政檔與掃描/空白檔；已存在 `data/tags/<hash>.json` 者跳過（resume）；其餘分成每批 15 檔
- **輸出**：`data/worklist_sample.json` + `data/worklist_batch0-4.json`

### ④ 市場標註 — `workflows/tag_reports.workflow.js`（Claude Workflow）
- **輸入**：`data/worklist_batch*.json`
- **做什麼**：5 個 agent 平行（fan-out），每個 agent 讀一個批次檔，逐檔用 Read 開 PDF 判定主要市場，輸出 **findb 市場代碼**，用 Write 寫 `data/tags/<file_hash>.json`
  - 標註規則（含 findb 代碼定義、債券→MACRO / 原物料→GLOBAL）內嵌在 workflow 指令
  - 由 Claude Code 以 Workflow 工具執行（非 `uv run`）
- **輸出**：`data/tags/<file_hash>.json`，內容 `{"market":"<findb 代碼>","is_research":bool,"confidence":0-1}`

### ⑤ 嵌入入庫 — `scripts/run_ingest.py`
- **輸入**：`data/extracted/sample.jsonl` + `data/tags/*.json`
- **過濾串**：行政檔 → 掃描/空 → 無 tag → `is_research=false` → 已在 DB（除非 `--force`）
- **做什麼**：合格檔以 `chunk.py` 切塊（600 字 / 80 重疊）→ `embed.py` 用 BGE-M3 批次嵌入（1024 維）→ `store.py` 依 `file_hash` 去重 upsert（先刪後插）寫入 pgvector
- **輸出**：`research.research_report` + `research.report_chunk`

### （一次性）市場代碼對齊 — `scripts/align_findb_markets.py`
把既有的中文市場標籤確定性重映射為 findb 代碼（同時改 `data/tags/*.json` 與 DB），冪等、不需重跑 Claude。對映規則見 [市場標籤](#市場標籤對齊-findb)。

### 檢索 — 兩種介面
- **CLI** `scripts/search.py`：嵌入查詢 → cosine top-k，可加 `--market <findb 代碼>` 過濾
- **網頁** `web/server.py` + `web/static/index.html`：FastAPI 後端在啟動時把 BGE-M3 常駐記憶體，前端 iOS 風格查詢介面

---

## 市場標籤（對齊 findb）

市場代碼採用 findb `instruments.market`（大寫）：

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

findb 無「債券」「原物料」獨立市場 → 歸到最接近者（債券→`MACRO`、原物料→`GLOBAL`）。對映在 `app/services/tagging.py`（`MARKETS` / `MARKET_DISPLAY` / `LEGACY_TO_FINDB` / `normalize_market()`）；前端 `MARKET_META` 把代碼顯示為中文 + 配色。

---

## Web API

| 端點 | 說明 |
|------|------|
| `GET /api/stats` | 總篇數、總片段數、各市場代碼篇數 |
| `GET /api/markets` | findb 市場代碼清單 |
| `GET /api/search?q=&market=&k=&passages=` | 語意檢索，結果**依報告分組**：每篇回傳 best_score、命中片段數、券商/日期/類型 metadata、清理後（去除 PDF 雜亂排版）的片段 |
| `GET /` | iOS 風格單頁前端 |

前端特性：雙欄側邊版面（手機收單欄）、自適應卡片網格、同篇研報合併、查詢關鍵字高亮、可展開片段、即打即查（debounce 450ms）、骨架載入。

---

## 完整重跑指令

```bash
# 0) 基礎建設（一次）
docker run -d --name report-mark-postgres \
  -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=research \
  -p 5436:5432 -v report-mark-pgdata:/var/lib/postgresql/data pgvector/pgvector:pg16
docker exec -i report-mark-postgres psql -U postgres -d research < db/schema.sql
uv sync

# 1-3) 抽樣 → 抽文字 → 工作清單
uv run python scripts/select_sample.py
uv run python scripts/extract_batch.py
uv run python scripts/make_worklist.py

# 4) Claude 市場標註：由 Claude Code 執行 workflows/tag_reports.workflow.js
#    （Workflow 工具，非 uv run；批次檔硬編在腳本內）

# 5) 嵌入入庫（首次會下載 BGE-M3 ~2-4GB）
uv run python scripts/run_ingest.py

# 6) 啟動查詢網頁
uv run uvicorn web.server:app --host 0.0.0.0 --port 8097
#    → http://localhost:8097

# CLI 檢索
uv run python scripts/search.py "AI 伺服器散熱需求"
uv run python scripts/search.py "利率與殖利率" --market MACRO
```

---

## 環境與基礎建設

| 項目 | 值 |
|------|-----|
| Python | 3.11+（uv 管理）|
| 向量 DB | `pgvector/pgvector:pg16`，容器 `report-mark-postgres`，host port **5436** |
| DB 連線 | env `REPORT_MARK_DB_URL`（預設 `postgresql+asyncpg://postgres:postgres@localhost:5436/research`）|
| 嵌入模型 | BGE-M3 dense，1024 維，CPU |
| Web 服務 | uvicorn，port **8097** |

> 此機 `docker compose` 子指令不可用，故用 `docker run` 起單一容器。

---

## 擴展到全量（15,866 檔）

原型驗證後若要跑全量：
- **抽樣階段省略**，直接對全語料抽文字
- **標註與嵌入成本約數十倍**：Claude 標註改全量分批 + checkpoint 背景跑；CPU 嵌入耗時大幅上升（可考慮 GPU 或分機）
- `file_hash` 去重與 resume 機制已就緒，可中斷續跑
- **掃描型 PDF 無 OCR** → 目前直接跳過，全量時若占比高需評估 OCR

---

## 排錯

| 症狀 | 檢查 |
|------|------|
| 查詢很慢 / 第一次卡住 | BGE-M3 首次下載 ~2-4GB；server 啟動時已暖機，看 uvicorn log |
| `/api/stats` 連不上 | 容器是否運行 `docker ps`；port 5436 是否被佔 |
| 入庫筆數偏少 | 看 `run_ingest.py` summary 的各類 skip（admin/scanned/untagged/non_research）|
| 市場顯示為代碼非中文 | 前端 `MARKET_META` 是否含該代碼；DB 是否已跑 `align_findb_markets.py` |
| 標註結果不對 | 抽查 `data/tags/<hash>.json`；市場代碼須在 `MARKETS` 內 |
