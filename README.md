# 廷豐智能研報（tingfeng-research）

> 券商研究報告的**語意檢索 ＋ RAG 問答 ＋ 可下載深度研報**平台。
> 把 `研報自動匯入/` 內約 1.5 萬份券商研報，經「抽文字 → Claude 多維標註 → 切塊嵌入 → pgvector」沉澱為可被語意搜尋、可問答、可一鍵生成 PDF 深度研報的語料庫。

<p>
  <img alt="Python" src="https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white">
  <img alt="FastAPI" src="https://img.shields.io/badge/FastAPI-async-009688?logo=fastapi&logoColor=white">
  <img alt="pgvector" src="https://img.shields.io/badge/pgvector-pg16-336791?logo=postgresql&logoColor=white">
  <img alt="BGE-M3" src="https://img.shields.io/badge/Embedding-BGE--M3%201024d-FF6F00">
  <img alt="Claude" src="https://img.shields.io/badge/LLM-Claude%20CLI-AE7415">
  <img alt="Typst" src="https://img.shields.io/badge/PDF-Typst-444">
</p>

---

## 目錄

1. [這個專案在做什麼](#這個專案在做什麼)
2. [功能總覽](#功能總覽)
3. [系統架構](#系統架構)
4. [端到端資料流](#端到端資料流)
5. [技術棧](#技術棧)
6. [快速開始](#快速開始)
7. [專案結構](#專案結構)
8. [核心概念](#核心概念)
9. [資料庫 schema](#資料庫-schema)
10. [Web 介面與 API](#web-介面與-api)
11. [設定（環境變數）](#設定環境變數)
12. [開發與測試](#開發與測試)
13. [部署](#部署)
14. [Roadmap 現況](#roadmap-現況)
15. [延伸文件](#延伸文件)

---

## 這個專案在做什麼

券商每天產出大量研究報告（PDF／docx），散落、難搜尋、更難被「問答」。本專案把這些報告語料化，提供三種使用方式：

- **語意檢索**：用自然語言找報告，混合「向量相似度 ＋ 字面比對」並依相關度／新近度排序。
- **RAG 問答**：直接問問題，系統檢索相關報告、串流生成帶**編號引用**的答案，可點回原始 PDF，支援多輪對話。
- **深度研報**：當問答涵蓋足夠時，一鍵生成數千字、含 KPI 卡片與圖表的 **PDF 深度研報**，並持久化保存。

**核心設計理念**：**Python 負責確定性處理**（解析、抽文字、切塊、嵌入、入庫、檢索），**Claude 負責語意判斷**（讀報告內容標多維標籤、生成摘要、回答問題、撰寫研報）。兩端以檔案的 `file_hash`（SHA256）耦合，全管線可 **checkpoint-resume**（中斷後可續跑、不重做）。

> **與 findb 的關係**：報告的「市場標籤」對齊姊妹專案 [findb](../findb) 的 `instruments.market` 代碼（`TW / US / HK / CN / FX / WTX / MACRO / GLOBAL / CRYPTO`），方便日後跨庫整合。findb 是**獨立專案**、不在本 repo 內，本專案僅在市場代碼上與其對齊。
>
> 注意：本 repo 的上一層 `Project/CLAUDE.md` 描述的是另一個名為「FinDB」的資料管線專案，**與本專案無關**，請勿混淆。

---

## 功能總覽

| 能力 | 說明 |
|------|------|
| **檢索** | 即打即查、關鍵字黃底高亮、命中片段預覽、2-3 句中文摘要、內嵌原始 PDF；可依市場／商品類型／標的／報告類型篩選，列表（依月/市場分組）或表格檢視 |
| **問答（RAG）** | SSE 串流回答、行內 `[n]` 引用可點回原報告、對話歷史側欄、多輪續問、讚／倒讚回饋、離題閘門、語料總覽題（如「有哪些券商」）走分面統計 |
| **研報閱讀頁** | `/app/report/:file_hash`：一份研報的原文（PDF／文字雙檢視）＋重點摘錄（點擊跳到原文並高亮）＋標籤／摘要／訊號＋相似研報＋「就這篇提問」，收攏到一個可分享的網址 |
| **深度研報** | 深度檢索 → 逐節長文串流 → KPI/圖表 → Typst 渲染 PDF（WeasyPrint 為回退）→ 持久化（markdown 為真相來源，PDF 可重建） |
| **監控頁** | `/monitor`：DB 筆數、標註／嵌入／摘要進度、背景程序狀態、速率與 ETA |
| **對外存取** | Cloudflare Tunnel ＋ nginx 邊緣（無需開放入站埠）；App 內建共用帳密登入 |

---

## 系統架構

```mermaid
flowchart TB
    subgraph SRC["來源（唯讀）"]
        A["研報自動匯入/<br/>~1.5 萬 PDF · docx"]
    end
    subgraph PY["確定性處理（Python）"]
        B["①extract_all.py<br/>抽文字 + 檔名 metadata<br/>file_hash 去重"]
        D["③ingest_all.py<br/>chunk 600/80 + BGE-M3 1024維<br/>upsert"]
    end
    subgraph CL["語意標註（Claude CLI）"]
        C["②tag_all_cli.py<br/>Haiku 多維標籤"]
    end
    subgraph STORE["pgvector · research schema"]
        E[("research_report<br/>報告層 + 標籤")]
        F[("report_chunk<br/>vector 1024 · HNSW + trgm")]
    end
    subgraph SRV["服務層（FastAPI · web/server.py）"]
        G["檢索<br/>/api/search"]
        H["RAG 問答<br/>/api/ask · SSE"]
        I["深度研報<br/>/api/report · SSE → PDF"]
    end
    A --> B --> C --> D
    D --> E
    D --> F
    E --> G
    F --> G
    E --> H
    F --> H
    E --> I
    F --> I
```

整條管線可由 `scripts/resume_corpus.sh` 一鍵編排（標註＋導入並行、鎖檔防重入、可中斷續跑）。

---

## 端到端資料流

以一次「問答 → 生成深度研報」串起各服務模組與 SSE 事件：

```mermaid
sequenceDiagram
    autonumber
    participant U as 使用者
    participant API as FastAPI<br/>web/server.py
    participant ANS as answer.py
    participant RET as retrieval.py<br/>store.py
    participant EMB as embed.py<br/>BGE-M3
    participant DB as pgvector
    participant LLM as llm.py<br/>claude CLI
    participant REP as report.py
    participant PDF as typst_render.py<br/>Typst（pdf.py 為回退）

    U->>API: POST /api/ask（問題）
    API->>ANS: answer_question(q, k=8)
    ANS->>EMB: 查詢向量（快取）
    ANS->>RET: hybrid_search（dense + 字面）
    RET->>DB: HNSW + trgm 召回
    DB-->>ANS: 命中片段
    ANS-->>API: SSE event: sources
    ANS->>LLM: 串流生成（帶編號來源）
    LLM-->>API: SSE event: token …（含 [n] 引用）
    API-->>U: 串流回答 + 寫入 qa_log
    Note over U,API: 涵蓋足夠 → 提示「生成深度研報」
    U->>API: POST /api/report
    API->>REP: generate_report()
    REP->>RET: 深度檢索（k=30）
    REP->>LLM: 長文串流（KPI / 圖表 區塊）
    LLM-->>API: SSE: status / token …
    REP->>PDF: render_report_pdf(markdown)
    PDF-->>API: SSE: done（download_url）
    API-->>U: 下載 PDF（report_doc 持久化）
```

---

## 技術棧

| 層 | 選型 | 用途 |
|----|------|------|
| 語言 | Python 3.11+（開發 venv 為 3.13）| 全後端；以 `uv` 管理依賴與虛擬環境 |
| Web 框架 | FastAPI ＋ uvicorn | API 與 SSE 串流，port `8097` |
| 向量庫 | PostgreSQL 16 ＋ `pgvector`（容器 `report-mark-postgres`，host port `5436`）| `research` schema，HNSW（cosine）＋ pg_trgm（GIN）混合檢索 |
| 嵌入 | BGE-M3 dense 1024 維（`FlagEmbedding`，CPU、單例延遲載入）| 報告切塊與查詢向量化 |
| LLM | Claude CLI（headless `claude -p`，stream-json）| 多維標註（Haiku）、摘要／問答／研報（Sonnet）；`app/services/llm.py` 包裝串流與網搜事件 |
| PDF | **Typst**（主軌）＋ WeasyPrint（fail-open 回退）＋ `markdown` ＋ 純 stdlib SVG | 品牌化深度研報 PDF（Noto Sans CJK 字型、金色 `#AE7415`） |
| 前端 | React 19 ＋ TypeScript ＋ Vite | `frontend/`，**需 `npm run build` 產出 `dist`** |
| 部署 | systemd ＋ Docker（pgvector / nginx / cloudflared）| 詳見[部署](#部署) |

> torch 走 CPU-only index（`pyproject.toml` 的 `pytorch-cpu`），避免抓 CUDA 輪子。

---

## 快速開始

### 1）基礎建設（一次）

```bash
# 起 pgvector 容器（host port 5436）
docker run -d --name report-mark-postgres \
  -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=research \
  -p 5436:5432 -v report-mark-pgdata:/var/lib/postgresql/data pgvector/pgvector:pg16

# 安裝依賴 + 套用 schema（亦可用 `make setup` 一次完成 deps + db + schema）
uv sync
docker exec -i report-mark-postgres psql -U postgres -d research < db/schema.sql
```

### 2）全量生產管線

```bash
uv run python scripts/extract_all.py              # ① 並行抽全量文字（去重）→ data/extracted/all.jsonl
uv run python scripts/tag_all_cli.py --workers 8  # ② Claude(Haiku) 多維標註 → data/tags/*.json（需 claude CLI）
uv run python scripts/ingest_all.py               # ③ 串流切塊＋嵌入入庫（首次下載 BGE-M3 ~2-4GB）

# 或一鍵編排（標註＋導入並行、可中斷續跑）：
bash scripts/resume_corpus.sh
```

> 小規模驗證可改走**抽樣原型**：`make prep`（= `select_sample.py → extract_batch.py → make_worklist.py`）→ 在 Claude Code 以 Workflow 執行 `workflows/tag_reports.workflow.js` → `make ingest`。更多捷徑見 `make help`，逐階段細節見 [docs/WORKFLOW.md](docs/WORKFLOW.md)。

### 3）啟動查詢網頁

```bash
cp .env.example .env   # 編輯填入 REPORT_MARK_ACCESS_USERNAME / _PASSWORD / _SESSION_SECRET
make serve             # 載入 .env 並啟動 → http://localhost:8097
```

- **需登入**：未設帳密時 App 會 **fail-closed 拒絕啟動**。本機 `localhost` 可直接以 HTTP 登入；其他裝置請走受保護的 HTTPS 入口。
- `make serve` **無 `--reload`**：改了 Python 程式要**重啟**才生效；靜態 HTML/JS 即時生效（`_NoCacheStatic`）。

### 4）CLI 檢索（免登入）

```bash
uv run python scripts/search.py "AI 伺服器散熱需求"
uv run python scripts/search.py "利率與殖利率" --market MACRO
# 或：make search Q="AI 伺服器散熱需求" MARKET=TW
```

---

## 專案結構

```
report-mark/
├─ app/services/                  ← 核心服務（確定性處理 + Claude 語意）
│   filename.py    檔名解析（股票代碼/券商/日期）+ 內文發行機構指紋 + source_display
│   extract.py     PDF/docx 抽文字、掃描檔偵測、SHA256 file_hash
│   chunk.py       段落邊界分塊（600 字 / 80 重疊）
│   embed.py       BGE-M3 dense 1024 維（CPU，單例延遲載入，查詢向量 LRU 快取）
│   tagging.py     findb 市場代碼 + 商品類型/標的詞表 + 多維標註指令 + tag JSON 解析
│   store.py       file_hash 去重 upsert + 雙路（dense／字面）召回查詢
│   textnorm.py    顯示/儲存/比對三種正規化（NFKC、去空白、小寫、NUL 清理）
│   retrieval.py   混合檢索編排：dense＋字面 → 去重 → tier/band 融合排序
│   scope_router.py 問答五類範圍路由（離題／總覽／語料問答／時效／建議風險，fail-open）
│   overview.py    語料庫總覽問題偵測、facet 聚合與文字化（純 SQL，無 LLM）
│   answer.py      RAG 問答：檢索、來源編號、串流回答、新近度/相關度守門、qa_log
│   report.py      深度研報生成：深度檢索、長文串流、薄涵蓋上網 nudge、PDF 持久化
│   report_gate.py 問答後是否提示生成深度研報、建議標題（純規則）
│   reading/       研報閱讀頁：anchor.py（引文/chunk 錨回正典文字）、queries.py（純 SQL 取數）、schemas.py（API 契約，前端 zod 鏡像）
│   typst_render.py 中介模型 → Typst 原始碼 → PDF（**主軌**，模板見 app/templates/）
  pdf.py         Markdown → 品牌化 HTML/PDF（WeasyPrint，**fail-open 回退軌**）
  report_writer.py 逐節生成編排（大綱／逐節草稿／組裝，M7）
│   chart.py       ```chart / ```kpi JSON → 純 SVG 渲染（零依賴，支援負值）
│   llm.py         Claude CLI 串流包裝（stream-json、網搜事件、529 重試、逾時保護）
│   db.py          async SQLAlchemy 引擎（env REPORT_MARK_DB_URL）
│
├─ scripts/                       ← 管線與維運工具
│   # 全量生產
│   extract_all.py / tag_all_cli.py / ingest_all.py
│   resume_corpus.sh    編排：標註＋導入並行、鎖檔防重入、可續跑
│   ingest_lowio.sh     離線大量導入：暫關 Postgres durability 降 I/O（結束自動還原）
│   # 抽樣原型 / 工具
│   select_sample.py / extract_batch.py / make_worklist.py / run_ingest.py
│   normalize_chunks.py     內容正規化 → content_norm（供字面比對）+ ANALYZE
│   backfill_full_text.py   回填 full_text 欄
│   backfill_report_dates.py / backfill_report_sources.py   回填報告日期 / 發行來源
│   generate_summaries.py   為缺摘要的報告生成 2-3 句中文摘要（Sonnet，冪等可續）→ make summaries
│   extract_takeaways.py    閱讀頁重點摘錄：LLM 只出「論點＋逐字引文」、Python 確定性錨定（Sonnet，近 90 天，冪等可續）→ make takeaways
│   align_findb_markets.py  中文標籤 → findb 代碼（一次性、冪等）
│   search.py               CLI 語意檢索（可 --market 過濾）
│   eval_retrieval.py       離線 retrieval 評估（hit rate / 新近度）
│   analyze_qa_log.py       問答延遲、引用新近度與回饋分析
│   sync_new_reports.sh     NAS→本地增量同步 + 增量匯入（drvfs + rsync，三層去重）
│
├─ workflows/
│   tag_reports.workflow.js  Claude 分批 fan-out 市場標註
│
├─ web/
│   auth.py        共用帳密、HMAC 簽章 session cookie、登入限流、localhost HTTP 例外
│   env_loader.py  輕量 .env 載入器（make serve 啟動時讀 repo 根目錄）
│   server.py      FastAPI 組裝層：auth、檢索、問答、對話、深度研報、監控、SPA 服務
│   static/login.html   共用帳號登入頁（自包樣式）；其餘 vanilla 頁已退場，改由 frontend/ React SPA 服務 /app/*
│
├─ db/schema.sql    research schema：research_report + report_chunk + qa_log + report_doc
├─ docs/            WORKFLOW / ROADMAP / EXTERNAL_ACCESS / nas_scheduled_sync / qa_pdf_report / 向量搜索優化報告
├─ deploy/          對外邊緣（docker-compose: nginx + cloudflared）+ systemd（web / NAS sync timer）
├─ tests/           Python 測試（unittest 風格，pytest 執行）
├─ 研報自動匯入/     ← 唯讀來源：券商 PDF/docx（~1.5 萬，由 NAS 同步鏡入）
├─ Makefile         指令捷徑（make help）
└─ pyproject.toml   依賴（uv）+ CPU-only torch index
```

---

## 核心概念

### 1）多維標籤（Claude 標註 + 檔名/內文解析）

每篇報告由 **Claude 讀內文判定**多個維度，另由**檔名與內文指紋解析**補齊：

| 維度 | 來源 | 內容 |
|------|------|------|
| 市場 `market` | Claude | findb 代碼：`TW/US/HK/CN/FX/WTX/MACRO/GLOBAL/CRYPTO`（債券→`MACRO`、原物料→`GLOBAL`）|
| 商品類型 `instrument_types` | Claude | 股票/指數/期貨/選擇權/ETF/債券/外匯/原物料/加密（可多值）|
| 個股/期貨關聯 | Claude | `relates_stock` / `relates_futures` 布林 |
| 具體標的 | Claude | `stock_targets`（個股代碼）/ `futures_targets`（期貨品種詞表）|
| 研報判定 | Claude | `is_research`（行政/活動檔不進檢索）＋ `confidence` |
| 券商來源 `source` | 檔名 + 內文指紋 | 本土指紋（「X投顧」/自有 URL/著作權自稱）優先，再檔名，再外資 |
| 報告日期/類型 | 檔名（必要時 mtime 回填）| `report_date` / `report_type` |

對映規則在 `app/services/tagging.py`；既有資料若需重映射 findb 代碼用 `make align`（確定性、不重跑 Claude）。詳細詞表見 [docs/WORKFLOW.md](docs/WORKFLOW.md)。

### 2）混合檢索（retrieval.py）

dense（BGE-M3 cosine，HNSW）＋ 字面（pg_trgm，比對 `content_norm`）雙路召回 → 去重 → **tier/band 融合排序**：先依命中層級（片語＞全詞＞部分）分層，層內再以「相關度分帶（band）→ 新近度 → 融合分數」排序，兼顧精準與新近。

### 3）RAG 問答（answer.py）

查詢向量化 → `hybrid_search` → `build_context`（編號來源 + 清理片段）→ Claude 串流回答（行內 `[n]` 引用）→ 解析引用 → 寫 `qa_log`。內建：

- **新近度/相關度守門**：相關度下限 `ASK_RELEVANCE_FLOOR`、過舊軟截斷（`ASK_STALE_AGE_DAYS`），但 fail-open 不致濫殺。
- **範圍路由**（`scope_router.py`，五類；Haiku 並行隱藏延遲、fail-open）擋掉與研報無關的提問。
- **多輪對話**：以 `conversation_id` 分組（`COALESCE(conversation_id, id)` 相容舊列），追問會被壓縮改寫。
- **總覽路徑**（`overview.py`）：枚舉/聚合題（「有哪些券商」「報告分類」）改走全語料分面統計，避開 top-k 限制。

### 4）研報閱讀頁（app/services/reading/ + scripts/extract_takeaways.py）

`/app/report/:file_hash`：把一份研報的原文、語料已知的一切（標籤／摘要／重點摘錄／訊號／相似研報）與下一步動作（就這篇提問）收攏到一個可分享的網址。以 `file_hash` 為網址鍵——`report_id` 在重新 ingest 時會換新，分享連結會失效。

沿用全站分工：**Claude 只出語意、Python 負責定位**。`make takeaways` 讓 Sonnet 每篇回 3-5 條「論點 ＋ 一句逐字引文」（不給 offset），再由 `reading/anchor.py` 的 `locate_quote` 確定性錨回原文字元區間，寫入 `research.report_takeaway`。因此**閱讀頁讀取時零 LLM 呼叫**。

**正典文字＝`clean_extracted(full_text)`，不是 `full_text`**（`full_text` 存的是未清理的原始抽取文字，保留 PDF 抽字的 CJK 間空白，如「台 積 電」）。餵 LLM 的 excerpt、錨點基準、API 回傳的文字三者必須是同一個字串；`text_sha256` 就是這個不變量的守衛：讀取時比對「摘錄擷取當時的 sha」與「當前正典文字的 sha」，不符即把該條降級為不可跳，而不是跳到錯的地方。錨不到（`quote_start` 為 `NULL`）時條目照樣顯示，只是不給跳轉。

> 定位一律走 `reading/anchor.py`，不要自己 `full_text.find(...)`：`report_chunk.content` 因切塊 overlap 而**不是** `full_text` 的子字串，天真比對約 99% 無聲失敗，詳見 `anchor.py` 模組 docstring。

### 5）深度研報（report.py + pdf.py + chart.py）

深度檢索（`REPORT_DEEP_K`=30）→ Claude 長文串流（可輸出 ` ```kpi ` / ` ```chart ` 區塊）→ `typst_render.py` 將 markdown 渲染為品牌化 PDF（失敗才回退 `pdf.py`）（KPI 卡片、callout、引用徽章、純 SVG 圖表）→ 存入 `report_doc`（markdown 為真相來源，PDF 遺失可由 markdown 重建）。涵蓋不足時會 nudge 模型上網補充（`REPORT_THIN_COVERAGE`）。

---

## 資料庫 schema

`research` schema（`db/schema.sql`，冪等：`CREATE ... IF NOT EXISTS` ＋ `ALTER ... ADD COLUMN IF NOT EXISTS`）：

| 表 | 用途 | 關鍵欄位 / 索引 |
|----|------|------|
| `research_report` | 報告層，一檔一列（`file_hash` 去重） | `market`、`is_research`、`confidence`、`source`、`report_date`、`instrument_types[]`、`stock_targets[]`、`futures_targets[]`、`full_text`、`summary`；索引：`market`(btree)、`instrument_types/stock_targets/futures_targets`(GIN) |
| `report_chunk` | 切塊層，一塊一列 | `embedding vector(1024)`、`content`、`content_norm`(GENERATED)；索引：`embedding`(HNSW cosine)、`content_norm`(GIN trgm)；FK `ON DELETE CASCADE` |
| `qa_log` | 每次 `/api/ask` 一列（稽核/分析） | `question`、`answer`、`cited_report_ids[]`、`filters`、`latency_ms`、`thinking_ms`、`feedback`、`sources`、`ext_sources`、`conversation_id` |
| `report_doc` | 生成的深度研報（隨對話保存） | `qa_id`、`conversation_id`、`title`、`markdown`(真相來源)、`pdf_path`、`sources` |
| `report_takeaway` | 閱讀頁重點摘錄，一列＝一份研報 × 一條重點（`make takeaways` 產出，讀取零 LLM） | `report_id`(FK CASCADE)、`ordinal`、`claim`、`quote`、`quote_start`/`quote_end`、`anchor_method`(`exact`/`normalized`/`prefix`)、`text_sha256`、`extraction_version`、`extraction_status`(`pending`/`valid`/`partial`/`rejected`)；`UNIQUE(report_id, ordinal)` |

> `content_norm` 的 GENERATED 表達式（`lower(regexp_replace(normalize(content, NFKC), '\s+', '', 'g'))`）必須與 `app/services/textnorm.py` 的 `norm_for_match()` 一致。

---

## Web 介面與 API

**兩種模式**（頂部切換）：

- **檢索**：搜尋框在內容區上方，結果預設**列表**（依市場/報告類型/日期分組）或切**表格**；每筆顯示市場/商品類型/標的/券商/日期/相關度/摘要，搜尋時列出黃底高亮命中片段，點任一筆內嵌原始 PDF。
- **問答**：自然語言提問，SSE 串流回答附引用來源，側欄保留對話歷史（可重看/續問/刪除），涵蓋足夠時提示生成深度研報。

**認證**：全站以單一**共用帳號＋密碼**把關（`web/auth.py`）。未登入導向 `/login`；session 以 HMAC 簽章 cookie 維持 7 天（滑動到期）；登入失敗對單一 IP 限流。`/api/*` 未授權回 `401`。

### API 對照

| Method | Path | 用途 | 串流 |
|--------|------|------|:----:|
| GET | `/api/stats` | 總筆數、市場/商品/類型分面、帳號名（5s 快取） | |
| GET | `/api/markets` | 市場清單 | |
| GET | `/api/reports` | 瀏覽（無關鍵字，分頁，可篩選/排序；回 `file_hash`） | |
| GET | `/api/search` | 混合檢索（`q` 必填，每報告回 `passages` 片段與 `file_hash`） | |
| GET | `/api/reading/{file_hash}` | 閱讀頁骨架：meta ＋ 標籤 ＋ 摘要 ＋ 重點摘錄 ＋ 訊號（**不含全文**） | |
| GET | `/api/reading/{file_hash}/text` | 正典文字（＝`clean_extracted(full_text)`，所有 offset 以此為準）；帶 `?chunk=N` 一併回該段的字元區間供高亮 | |
| GET | `/api/reading/{file_hash}/similar` | 相似研報（向量近鄰，`limit` 預設 6、上限 20） | |
| POST | `/api/ask` | RAG 問答（預設 `k=8`，問題上限 2000 字，併發 ≤3） | SSE |
| POST | `/api/report` | 生成深度研報（併發由 `REPORT_SEMAPHORE`，預設 1 序列化） | SSE |
| GET | `/api/report-doc/{id}/pdf` | 下載生成的深度研報 PDF（缺檔即由 markdown 重建） | |
| GET | `/api/report/{id}/full` | 原始報告 metadata（供 modal） | |
| GET | `/api/report/{id}/file` | 原始報告檔（PDF inline / 其他 attachment） | |
| GET | `/api/conversations`、`/api/conversations/{id}` | 對話串清單 / 單串內容 | |
| DELETE/POST | `/api/conversations/{id}`、`.../delete` | 刪整串對話 | |
| GET | `/api/history`、DELETE/POST `/api/history/{qa_id}` | 問答歷史清單 / 刪單題 | |
| POST | `/api/feedback` | 對某次回答記讚/倒讚 | |
| GET | `/api/progress` | 監控快照（DB、標註/嵌入/摘要進度、背景程序） | |
| GET | `/`、`/monitor`、`/help`、`/login`(GET/POST)、`/logout`(POST) | 頁面與登入 | |

> SSE 事件：問答＝`sources → token… → done`；深度研報＝`status → sources → token… → done`。完整契約見 [docs/WORKFLOW.md](docs/WORKFLOW.md#web-api)。

---

## 設定（環境變數）

由 `make serve` 啟動時從 repo 根 `.env` 載入（`web/env_loader.py`，dotenv 風格、不做 shell 展開）。完整範本見 `.env.example`。

**存取控制 / DB**

| 變數 | 預設 | 說明 |
|------|------|------|
| `REPORT_MARK_ACCESS_USERNAME` | —（必填）| 登入帳號；未設則 fail-closed 拒啟 |
| `REPORT_MARK_ACCESS_PASSWORD` | —（必填）| 登入密碼；未設則 fail-closed 拒啟 |
| `REPORT_MARK_SESSION_SECRET` | 空（每次重啟換）| session 簽章金鑰；建議固定長隨機字串 |
| `REPORT_MARK_TRUSTED_PROXY_CIDRS` | loopback | 信任的反向代理 CIDR（走 Cloudflare Tunnel 外網時必填）|
| `REPORT_MARK_DB_URL` | `postgresql+asyncpg://postgres:postgres@localhost:5436/research` | DB 連線字串 |

**問答（`ASK_*`）** — 常用：`ASK_MAX_REPORTS`(15)、`ASK_MAX_PASSAGES`(4)、`ASK_MAX_CONTEXT_CHARS`(20000)、`ASK_RETRIEVAL_K`(15)、`ASK_DENSE_SCAN`(400)、`ASK_RELEVANCE_FLOOR`(0.62)、`ASK_STALE_AGE_DAYS`(180)、`ASK_MAX_STALE_REPORTS`(4)、`ASK_RECENCY_HALF_LIFE_DAYS`(90)、`ASK_INTENT_MODEL`(`claude-haiku-4-5`)。

**深度研報（`REPORT_*`）** — `REPORT_MODEL`(`claude-sonnet-5`)、`REPORT_DEEP_K`(30)、`REPORT_MAX_REPORTS`(25)、`REPORT_MAX_PASSAGES`(6)、`REPORT_MAX_CONTEXT_CHARS`(40000)、`REPORT_TIMEOUT`(600s)、`REPORT_THIN_COVERAGE`(8)、`REPORT_ENABLE_WEB`(1)、`REPORTS_DIR`(`data/reports`)、`REPORT_SEMAPHORE`(1)、`REPORT_MIN_CITED`(3)。

> 預設值集中在 **`app/config.py`** 的 `_load()`（frozen dataclass ＋ `os.getenv`），不必設定也能跑。各服務模組保留原常數名但改由 `get_settings()` 取值。
>
> 本表僅列常用鍵；`app/config.py` 另有約 60 個未在此列出的旋鈕（`REPORT_RENDERER`、`REPORT_SECTIONED_ENABLED`、`REPORT_DRAFT_BUDGET`、`ASK_RERANK_*`、`QA_AGENTIC_*`、`FAITHFULNESS_*`、`TRUSTED_DATA_ENABLED` 等），以該檔為準。

---

## 開發與測試

```bash
# Python 測試（unittest 風格 class，pytest 執行）
uv run pytest -q                         # 全部
uv run pytest tests/test_answer.py       # 單檔
uv run pytest -k retrieval               # 關鍵字

# 前端測試（React + TS + Vite）
cd frontend && npm test          # vitest
cd frontend && npm run typecheck # tsc --noEmit
```

- `tests/` 共 86 個檔，涵蓋 filename/extract/retrieval/answer/report/pdf/auth/store/overview/intent/summary 等；偏好以 mock 隔離 LLM、嵌入、檔案、DB 邊界。
- **慣例**：確定性邏輯放 Python，Claude CLI 只用於語意標註/摘要/問答/研報；前端在 `frontend/src/`（React ＋ TS ＋ CSS Modules）；新增旋鈕加在 `app/config.py`，`REPORT_MARK_*` 前綴**只**用於 auth/DB 那五個變數。Python 側未配置 ruff/black/mypy/pre-commit，**前端有 ESLint ＋ `tsc --noEmit`，且與 pytest 同列 CI 必要檢查**（風格約定見 [AGENTS.md](AGENTS.md)）。
- **提交**：採 Conventional Commits（常見繁中 scope，如 `feat(report): …`、`fix(report): …`）；提交前看近期訊息與 staged diff，勿用整句英文當訊息。
- **改後端要重啟、靜態檔即時生效**：`make serve` 無 `--reload`；靜態資源走 `_NoCacheStatic`（破快取、304 revalidation）。

---

## 部署

| 場景 | 做法 |
|------|------|
| 本機 | `make serve` → uvicorn `:8097`（啟動時 warm BGE-M3，免首查延遲）|
| 生產 Web | systemd `report-mark-web.service`（enabled、自動重啟）；改碼後 `sudo systemctl restart report-mark-web.service` |
| NAS 增量同步 | systemd `report-mark-sync.timer`（每 3 小時）→ `scripts/sync_new_reports.sh`（drvfs 唯讀掛載 → rsync delta → 增量 extract/tag/ingest）；手動測試 `make sync-once`。見 [docs/nas_scheduled_sync_deployment.md](docs/nas_scheduled_sync_deployment.md) |
| 對外存取 | Cloudflare Tunnel ＋ nginx 邊緣（`deploy/docker-compose.yml`，無入站埠）：`make up-edge` / `down-edge` / `edge-logs` / `edge-reload`，需 `deploy/.env` 的 `TUNNEL_TOKEN`。見 [docs/EXTERNAL_ACCESS.md](docs/EXTERNAL_ACCESS.md) |
| 深度研報 PDF | 需安裝 Noto Sans CJK 字型；`REPORT_TIMEOUT` 建議 ≥300s。見 [docs/qa_pdf_report_deployment.md](docs/qa_pdf_report_deployment.md) |

對外請求路徑：`Browser ──HTTPS──▶ Cloudflare edge ──tunnel──▶ nginx:80 ──▶ uvicorn:8097`。

---

## Roadmap 現況

`docs/ROADMAP.md` 規劃四階段。**目前（Phase 1）已上線**：RAG 問答（`/api/ask`）、`qa_log`、對話歷史（`/api/conversations`）、深度研報生成（`/api/report`）、PDF 渲染與 `report_doc`、前端問答模式（引用/歷史/讚倒讚/研報下載）。

**尚未實作**：每日簡報（`brief.py` / `brief.html`）、Phase 2（結構化訊號 ＋ findb 整合）、Phase 3（MCP / 對外 REST API）。詳見 [docs/ROADMAP.md](docs/ROADMAP.md)。

---

## 延伸文件

| 文件 | 內容 |
|------|------|
| [docs/WORKFLOW.md](docs/WORKFLOW.md) | **端到端權威說明**：流程圖、逐階段 I/O、完整標籤詞表、Web API 契約、擴展與排錯 |
| [docs/ROADMAP.md](docs/ROADMAP.md) | 四階段里程碑與現況 |
| [docs/EXTERNAL_ACCESS.md](docs/EXTERNAL_ACCESS.md) | Cloudflare Tunnel ＋ nginx 對外存取架構與維運 |
| [docs/nas_scheduled_sync_deployment.md](docs/nas_scheduled_sync_deployment.md) | NAS 定時增量同步（systemd timer）部署 |
| [docs/qa_pdf_report_deployment.md](docs/qa_pdf_report_deployment.md) | 深度研報 PDF（CJK 字型）部署 |
| [docs/production_resilience.md](docs/production_resilience.md) | 生產韌性：重啟策略、健康檢查、失敗告警、systemd unit 還原 |
| [docs/向量搜索優化報告.md](docs/向量搜索優化報告.md) | 向量檢索優化（混合檢索、HNSW 調校、CJK 正規化）|
| [AGENTS.md](AGENTS.md) | 貢獻者指南（結構、風格、測試、提交與安全慣例）|
