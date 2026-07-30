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
| **檢索** | 即打即查、關鍵字黃底高亮、命中片段預覽、2-3 句中文摘要；可依市場／商品類型／標的／報告類型篩選，列表或表格檢視；**點任一筆導向閱讀頁 `/app/report/:file_hash`**（不再內嵌 PDF modal）|
| **問答（RAG）** | SSE 串流回答、行內 `[n]` 引用可點回原報告、對話歷史側欄、多輪續問、讚／倒讚回饋、離題閘門、語料總覽題（如「有哪些券商」）走分面統計 |
| **研報閱讀頁** | `/app/report/:file_hash`：一份研報的原文（PDF／文字雙檢視）＋重點摘錄（點擊跳到原文並高亮）＋標籤／摘要／訊號＋相似研報＋「就這篇提問」，收攏到一個可分享的網址 |
| **深度研報** | 深度檢索 → 逐節長文串流 → KPI/圖表 → Typst 渲染 PDF（WeasyPrint 為回退）→ 持久化（markdown 為真相來源，PDF 可重建）；生成跑在背景任務，斷線／重整不中止 |
| **觀點雷達** | `/app/radar`：`make signals` 由 Claude 依固定 JSON schema 擷取結構化訊號（評等／目標價／EPS／四維論點 → `research.report_signal`）→ `app/services/radar/` 做跨券商共識聚合 → 標的總覽（共識快照／四維論點／近期事件／單券商歷程），**讀取零 LLM 呼叫** |
| **監控頁** | `/app/monitor`：DB 筆數、摘要／重點摘錄／訊號覆蓋、背景程序狀態、速率與 ETA、忠實度查核卡片 |
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
        S["extract_signals.py<br/>Sonnet 結構化訊號"]
    end
    subgraph STORE["pgvector · research schema"]
        E[("research_report<br/>報告層 + 標籤")]
        F[("report_chunk<br/>vector 1024 · HNSW + trgm")]
        T[("report_signal<br/>評等 · 目標價 · 四維論點")]
    end
    subgraph SRV["服務層（FastAPI · web/routers/）"]
        G["檢索<br/>/api/search"]
        H["RAG 問答<br/>/api/ask · SSE"]
        I["深度研報<br/>/api/report · SSE → PDF"]
        J["觀點雷達<br/>/api/instrument/…/radar"]
    end
    A --> B --> C --> D
    D --> E
    D --> F
    E --> S --> T
    E --> G
    F --> G
    E --> H
    F --> H
    E --> I
    F --> I
    T --> J
```

整條管線可由 `scripts/resume_corpus.sh` 一鍵編排（標註＋導入並行、鎖檔防重入、可中斷續跑）。

---

## 端到端資料流

以一次「問答 → 生成深度研報」串起各服務模組與 SSE 事件：

```mermaid
sequenceDiagram
    autonumber
    participant U as 使用者
    participant API as FastAPI<br/>web/routers/
    participant ANS as answer.py
    participant RET as retrieval_pipeline.py<br/>retrieval.py · store.py
    participant EMB as embed.py<br/>BGE-M3
    participant DB as pgvector
    participant LLM as llm.py<br/>claude CLI
    participant REP as report.py<br/>report_writer.py
    participant PDF as typst_render.py<br/>Typst（pdf.py 為回退）

    U->>API: POST /api/ask（問題）
    API->>ANS: answer_question(q, k=8)
    ANS-->>API: SSE event: status（understanding）
    ANS->>EMB: 查詢向量（快取）
    ANS->>RET: hybrid_search（dense + 字面）→ rerank
    RET->>DB: HNSW + trgm 召回
    DB-->>ANS: 命中片段
    ANS-->>API: SSE event: sources
    ANS->>LLM: 串流生成（帶編號來源）
    LLM-->>API: SSE event: token …（含 [n] 引用）
    API-->>U: 串流回答 + 寫入 qa_log
    Note over U,API: 涵蓋足夠 → 提示「生成深度研報」
    U->>API: POST /api/report
    API-->>U: SSE: run（run_id；生成跑在背景任務，斷線不中止）
    API->>REP: generate_report()
    REP->>RET: 多查詢深度檢索（k=30，fan-out + MMR）
    REP->>LLM: 大綱 → 逐節草稿（可含 KPI / 圖表 區塊）
    LLM-->>API: SSE: status / outline / token / section_draft …
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
cd frontend && npm ci && npm run build && cd ..   # 產出 frontend/dist（SPA 由它提供）
make serve             # 載入 .env 並啟動 → http://localhost:8097
```

- **需登入**：未設帳密時 App 會 **fail-closed 拒絕啟動**。本機 `localhost` 可直接以 HTTP 登入；其他裝置請走受保護的 HTTPS 入口。
- **沒有 `frontend/dist` 就沒有頁面**：`/app/*` 直接回 **503**（`dist` 不在版控內），根路徑 `/` 只是 302 導向 `/app/search`。
- `make serve` **無 `--reload`**：改了 Python 程式要**重啟**才生效。**前端改動要 `cd frontend && npm run build`**：SPA 由 `frontend/dist` 提供，資產走 `_ImmutableStatic`（`Cache-Control: immutable`，一年）；`_NoCacheStatic` 現在只剩 `web/static/login.html` 走（即時生效）。

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
├─ app/
│   config.py      集中設定（frozen dataclass ＋ os.getenv，約 80 個旋鈕鍵）
│   templates/     Typst 模板：manifest.py registry ＋ ib-classic / broker-modern / privatebank-dark
│
├─ app/services/                  ← 核心服務（確定性處理 + Claude 語意）
│   filename.py    檔名解析（股票代碼/券商/日期）+ 內文發行機構指紋 + source_display
│   extract.py     PDF/docx 抽文字、掃描檔偵測、SHA256 file_hash
│   chunk.py       段落邊界分塊（600 字 / 80 重疊）
│   embed.py       BGE-M3 dense 1024 維（CPU，單例延遲載入，查詢向量 LRU 快取）
│   tagging.py     findb 市場代碼 + 商品類型/標的詞表 + 多維標註指令 + tag JSON 解析
│   store.py       file_hash 去重 upsert + 雙路（dense／字面）召回查詢
│   textnorm.py    顯示/儲存/比對三種正規化（NFKC、去空白、小寫、NUL 清理）
│   retrieval.py   混合檢索編排：dense＋字面 → 去重 → tier/band 融合排序
│   retrieval_pipeline.py 問答／研報的檢索單一入口：embed → hybrid_search → rerank → build_context；研報另走多查詢 fan-out ＋ MMR（M6）
│   rerank.py      cross-encoder 重排（M2；semaphore ＋ deadline，逾時 fail-open）
│   scope_router.py 問答五類範圍路由（離題／總覽／語料問答／時效／建議風險，fail-open）
│   overview.py    語料庫總覽問題偵測、facet 聚合與文字化（純 SQL，無 LLM）
│   answer.py      RAG 問答：檢索、來源編號、串流回答、新近度/相關度守門、qa_log
│   agentic_qa.py / query_planner.py  多輪補查編排與子查詢規劃（M5）
│   evidence.py    證據帳本（M4b）；trusted_market_data.py 受信任時效資料（M4a）
│   faithfulness.py 忠實度查核（M8）；followups.py 追問建議；locale.py 輸出語言（M10）
│   report.py      深度研報生成編排：深度檢索、串流、薄涵蓋上網 nudge、渲染分派、持久化
│   report_writer.py 逐節生成編排（大綱／逐節檢索與草稿／組裝，M7）
│   report_gate.py 問答後是否提示生成深度研報、建議標題（純規則）
│   reading/       研報閱讀頁：anchor.py（引文/chunk 錨回正典文字）、queries.py（純 SQL 取數）、schemas.py（API 契約，前端 zod 鏡像）
│   signal_extract.py 觀點雷達訊號擷取：固定 JSON schema prompt + Python 正規化/驗證（純函式）
│   radar/         觀點雷達服務層：types / queries / scale / compute / schemas（純 SQL ＋ 決定性聚合，讀取零 LLM）
│   typst_render.py 中介模型 → Typst 原始碼 → PDF（**主軌**，模板見 app/templates/）
│   pdf.py         Markdown → 品牌化 HTML/PDF（WeasyPrint，**fail-open 回退軌**；文字正規化與免責亦由此供兩軌共用）
│   chart.py       chart / kpi 圍欄 JSON → 純 SVG 渲染（零依賴，支援負值）
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
│   backfill_full_text.py   回填 full_text 欄
│   backfill_report_dates.py / backfill_report_sources.py   回填報告日期 / 發行來源
│   generate_summaries.py   為缺摘要的報告生成 2-3 句中文摘要（Sonnet，冪等可續）→ make summaries
│   extract_takeaways.py    閱讀頁重點摘錄：LLM 只出「論點＋逐字引文」、Python 確定性錨定（Sonnet，近 90 天，冪等可續）→ make takeaways
│   extract_signals.py      觀點雷達訊號擷取（Sonnet，高覆蓋子集先行，冪等可續）→ make signals
│   generate_titles.py      抽報告內部標題取代檔名顯示；英文標題譯中文、無標題則自擬（Sonnet，冪等可續）→ make titles
│   align_findb_markets.py  中文標籤 → findb 代碼（一次性、冪等）
│   search.py               CLI 語意檢索（可 --market 過濾）
│   eval_retrieval.py       離線 retrieval 評估（hit rate / 新近度）
│   eval_compare.py         比較兩份評測結果 JSON，劣化即非零退出（三種形狀通吃）→ make eval-compare
│   analyze_qa_log.py       問答延遲、引用新近度與回饋分析
│   eval_faithfulness.py    M8 查核結果彙總（唯讀）；--claims <id> 逐條主張下鑽
│   check_batch_freshness.py  批次停更偵測：純 SQL 比最新產出日 vs 門檻（0 新鮮／1 停更／2 查不到）→ make freshness
│   sync_new_reports.sh     NAS→本地增量同步 + 增量匯入（drvfs + rsync，三層去重）
│
├─ workflows/
│   tag_reports.workflow.js  Claude 分批 fan-out 市場標註
│
├─ web/
│   server.py      FastAPI 組合層：lifespan 暖機、auth middleware（deny-by-default）、掛載各 router
│   routers/       11 個 APIRouter：ask / report / search / qa_history / monitor / radar / reading / report_file / health / auth_pages / spa
│   deps.py        跨組共用符號的單一存取點（測試 patch 就 patch 這裡）
│   report_runs.py 研報背景執行登錄表（行程內狀態；重播＋直播、取消）
│   auth.py        共用帳密、HMAC 簽章 session cookie、登入限流、localhost HTTP 例外
│   env_loader.py  輕量 .env 載入器（make serve 啟動時讀 repo 根目錄）
│   static/login.html   共用帳號登入頁（自包樣式）；其餘 vanilla 頁已退場，改由 frontend/ React SPA 服務 /app/*
│
├─ frontend/        React 19 ＋ TypeScript ＋ Vite SPA（features: search / ask / monitor / radar / report / help）
│                   **部署要 `npm run build` 產出 frontend/dist；dist 不在版控內**
├─ eval/            離線評測 harness：run_ragas.py（M1）、run_report_eval.py（M1b）、凍結題集、baselines/
├─ db/schema.sql    research schema：research_report + report_chunk + qa_log + report_doc
│                   + report_signal + report_run/report_section + report_rendition + report_takeaway
├─ docs/            WORKFLOW / ROADMAP / EXTERNAL_ACCESS / production_resilience / nas_scheduled_sync / qa_pdf_report / 向量搜索優化報告
├─ deploy/          生產部署真相來源：systemd（web / NAS sync / alert ＋ PATH drop-in）、nginx.conf、docker-compose（nginx + cloudflared）
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

查詢向量化 → `hybrid_search` → rerank → `build_context`（編號來源 + 清理片段）→ Claude 串流回答（行內 `[n]` 引用）→ 解析引用 → 寫 `qa_log`。**這段檢索編排住在 `retrieval_pipeline.py`**（問答與研報共用的唯一入口），`answer.py` 自己不呼叫 `hybrid_search`；但 `build_context` / `select_reports` 仍定義在 `answer.py`。內建：

- **新近度/相關度守門**：相關度下限 `ASK_RELEVANCE_FLOOR`、過舊軟截斷（`ASK_STALE_AGE_DAYS`），但 fail-open 不致濫殺。
- **範圍路由**（`scope_router.py`，五類；Haiku 並行隱藏延遲、fail-open）擋掉與研報無關的提問。
- **多輪對話**：以 `conversation_id` 分組（`COALESCE(conversation_id, id)` 相容舊列），追問會被壓縮改寫。
- **總覽路徑**（`overview.py`）：枚舉/聚合題（「有哪些券商」「報告分類」）改走全語料分面統計，避開 top-k 限制。

### 4）研報閱讀頁（app/services/reading/ + scripts/extract_takeaways.py）

`/app/report/:file_hash`：把一份研報的原文、語料已知的一切（標籤／摘要／重點摘錄／訊號／相似研報）與下一步動作（就這篇提問）收攏到一個可分享的網址。以 `file_hash` 為網址鍵——`report_id` 在重新 ingest 時會換新，分享連結會失效。

沿用全站分工：**Claude 只出語意、Python 負責定位**。`make takeaways` 讓 Sonnet 每篇回 3-5 條「論點 ＋ 一句逐字引文」（不給 offset），再由 `reading/anchor.py` 的 `locate_quote` 確定性錨回原文字元區間，寫入 `research.report_takeaway`。因此**閱讀頁讀取時零 LLM 呼叫**。

**正典文字＝`clean_extracted(full_text)`，不是 `full_text`**（`full_text` 存的是未清理的原始抽取文字，保留 PDF 抽字的 CJK 間空白，如「台 積 電」）。餵 LLM 的 excerpt、錨點基準、API 回傳的文字三者必須是同一個字串；`text_sha256` 就是這個不變量的守衛：讀取時比對「摘錄擷取當時的 sha」與「當前正典文字的 sha」，不符即把該條降級為不可跳，而不是跳到錯的地方。錨不到（`quote_start` 為 `NULL`）時條目照樣顯示，只是不給跳轉。

> 定位一律走 `reading/anchor.py`，不要自己 `full_text.find(...)`：`report_chunk.content` 因切塊 overlap 而**不是** `full_text` 的子字串，天真比對約 99% 無聲失敗，詳見 `anchor.py` 模組 docstring。

### 5）深度研報（report.py → report_writer.py → typst_render.py / pdf.py → chart.py）

深度檢索（`REPORT_DEEP_K`=30，M6 多查詢 fan-out ＋ MMR）→ **逐節生成**（`REPORT_SECTIONED_ENABLED` 預設開，`report_writer.py`：大綱 → 逐節檢索與草稿 → 逐節 grounding 與低分節修正一輪 → 單次組裝，狀態機落 `report_run`/`report_section`；`REPORT_DRAFT_BUDGET` 在逾時逼近時只砍動態分析子節、保住骨架，仍交付）→ Claude 可輸出 ` ```kpi ` / ` ```chart ` 區塊 → `typst_render.py` 將 markdown 渲染為品牌化 PDF（**主軌**；失敗才 fail-open 回退 `pdf.py` 的 WeasyPrint）→ 存入 `report_doc`（markdown 為真相來源，PDF 遺失可由 markdown 重建）。涵蓋不足時會 nudge 模型上網補充（`REPORT_THIN_COVERAGE`）。

- **模板由 registry 管理**（`app/templates/manifest.py`：ib-classic／broker-modern／privatebank-dark）；換模板重出走 `POST /api/report-doc/{report_id}/rerender`，**零 LLM、零重新生成**，產物落不可變表 `report_rendition`。
- **生成跑在背景任務**（`web/report_runs.py`）：`POST /api/report` 只是訂閱端，斷線／重整不會中止生成；重連走 `GET /api/report-runs/{run_id}/stream`（先重播、再接直播），主動中止走 `.../cancel`。登錄表是**行程內**狀態，重啟即全滅。

### 6）觀點雷達（signal_extract.py + app/services/radar/ + scripts/extract_signals.py）

`/app/radar`：把「同一個標的、不同券商、不同時間」的觀點攤成一張可比較的表。同樣是**Claude 只出語意、Python 負責計算**——`make signals` 讓 Sonnet 依固定 JSON schema 擷取 `rating_normalized`（正規化五級評等）／`target_price`（**保幣別、不換算**）／`eps_estimates`／`thesis_dimensions`（outlook・catalyst・risk・valuation 四維），寫入 `research.report_signal`（一列＝一份研報 × 一個標的，`UNIQUE(report_id, market, instrument_code)`、FK CASCADE）；跨券商共識、四分位、跨期變動全由 `app/services/radar/`（types → queries → scale → compute → schemas）決定性計算，**讀取雷達零 LLM 呼叫**。

擷取刻意只跑高覆蓋子集（`--min-brokers 3` / `--min-reports 5` / `--top-n 50`），所以**「還沒有訊號」是常態不是錯誤**：有研報但尚未擷取時雷達回 200 `pending_extraction` 空狀態，完全查無研報才 404。

> `extract_signals.py`、`extract_takeaways.py`、`generate_summaries.py`、`generate_titles.py`、`tag_all_cli.py`、`sync_new_reports.py` 都 spawn `claude` CLI，**不可併發**——互搶會讓擷取被大量誤標 `rejected`（不是資料壞、也不是模型壞）。互斥由 `scripts/_claude_lock.py` 的 `flock` 跨進程鎖強制：撞車時後啟動者印出持有者（腳本名／pid／起始時間）後以 `rc=75` 結束，**不會產出壞資料**。鎖綁在檔案描述子上，持有者行程無論怎麼死（含 SIGKILL）都會自動釋放，不需要手動清鎖檔。緊急繞過＝`CLAUDE_LOCK_DISABLE=1`（會印警告）。

---

## 資料庫 schema

`research` schema（`db/schema.sql`，冪等：`CREATE ... IF NOT EXISTS` ＋ `ALTER ... ADD COLUMN IF NOT EXISTS`）：

| 表 | 用途 | 關鍵欄位 / 索引 |
|----|------|------|
| `research_report` | 報告層，一檔一列（`file_hash` 去重） | `market`、`is_research`、`confidence`、`source`、`report_date`、`instrument_types[]`、`stock_targets[]`、`futures_targets[]`、`full_text`、`summary`、`title`/`title_original`/`title_source`（顯示標題，取代檔名）；索引：`market`(btree)、`instrument_types/stock_targets/futures_targets`(GIN) |
| `report_chunk` | 切塊層，一塊一列 | `embedding vector(1024)`、`content`、`content_norm`(GENERATED)；索引：`embedding`(HNSW cosine)、`content_norm`(GIN trgm)；FK `ON DELETE CASCADE` |
| `qa_log` | 每次 `/api/ask` 一列（稽核/分析） | `question`、`answer`、`cited_report_ids[]`、`filters`、`latency_ms`、`thinking_ms`、`feedback`、`sources`、`ext_sources`、`conversation_id` |
| `report_doc` | 生成的深度研報（隨對話保存） | `qa_id`、`conversation_id`、`title`、`markdown`(真相來源)、`pdf_path`、`sources` |
| `report_takeaway` | 閱讀頁重點摘錄，一列＝一份研報 × 一條重點（`make takeaways` 產出，讀取零 LLM） | `report_id`(FK CASCADE)、`ordinal`、`claim`、`quote`、`quote_start`/`quote_end`、`anchor_method`(`exact`/`normalized`/`prefix`)、`text_sha256`、`extraction_version`、`extraction_status`(`pending`/`valid`/`partial`/`rejected`)；`UNIQUE(report_id, ordinal)` |
| `report_signal` | 觀點雷達訊號，一列＝一份研報對一個標的的結構化觀點（`make signals` 產出，讀取零 LLM） | `report_id`(FK CASCADE)、`market`/`instrument_code`/`broker`/`report_date`、`rating_raw`＋`rating_normalized`(五級＋`unknown`)、`target_price`(numeric，保幣別)、`eps_estimates`(jsonb)、`thesis_dimensions`(jsonb 四維)、`extraction_status`；`UNIQUE(report_id, market, instrument_code)` |
| `report_run` / `report_section` | M7 逐節生成狀態機：一次生成一列 run、每節一列 section | run：`request_key`(冪等鍵，UNIQUE)、`status`(`queued`…`completed`/`failed`/`cancelled`)、`outline`、`checkpoint`、`updated_at`(心跳)；section：`position`、`draft_markdown`/`final_markdown`、`evidence_ids[]`；`run_id` FK CASCADE |
| `report_rendition` | M9b **不可變**渲染產物：同一份 markdown × 渲染器 × 模板各一列，換模板重出走這裡（零 LLM） | `report_id`、`renderer`、`template_id`、`content_hash`(markdown sha256)、`pdf_path`；`report_doc.current_rendition_id` 為原子切換的指標 |

> `content_norm` 的 GENERATED 表達式（`lower(regexp_replace(normalize(content, NFKC), '\s+', '', 'g'))`）必須與 `app/services/textnorm.py` 的 `norm_for_match()` 一致。

---

## Web 介面與 API

**導覽是左側導覽軌**（可收合成 mini，手機改底部 tab bar），四個入口——**檢索／問答／觀點／監控**；閱讀頁與說明頁不進導覽列。

- **檢索**：搜尋列＋市場 chip 列＋已選條件 chips 固定在上方；**無查詢也無篩選的落地態**顯示 Bento 牆（語料市場組成色譜、最新一批研報），此時工具列不渲染，一旦輸入查詢或套上篩選才換成工具列（排序／更多篩選／檢視切換）＋結果區。結果檢視只有兩種（**列表**＝高密度單列、**表格**）；依日期(月)分組只出現在「查看全部／已篩選瀏覽」，搜尋結果不分組。列上顯示市場代碼／標的／券商／日期／相關度，搜尋時列出黃底高亮命中片段，**點任一筆＝導向閱讀頁 `/app/report/:file_hash`**（帶 `?chunk=N` 跳到命中段），不是內嵌 PDF modal。
- **問答**：自然語言提問，SSE 串流回答附可點引用來源，側欄保留對話歷史（可重看／續問／刪除），涵蓋足夠時提示生成深度研報。

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
| POST | `/api/ask` | RAG 問答（預設 `k=8`，問題上限 2000 字，併發 ≤3；滿載先送 `queued` 事件，排隊逾 `ASK_MAX_QUEUE` 回 429＋`Retry-After`） | SSE |
| POST | `/api/ask/stop` | 使用者中斷串流時保存部分答案（`stopped=true`），回 `{qa_id}` | |
| GET | `/api/qa/{root_qa_id}/versions` | 重生／編輯的版本鏈（**含已標 inactive 的舊版**，歷史 pager 要回看的正是它們） | |
| POST | `/api/report` | 生成深度研報（**跑在背景任務**，斷線不中止；併發由 `REPORT_SEMAPHORE`，預設 1 序列化；滿載先送 `queued` 事件，排隊逾 `REPORT_MAX_QUEUE` 回 429＋`Retry-After`，**接回既有 run 豁免**） | SSE |
| GET | `/api/report-runs?conversation_id=` | 該對話仍在背景生成的研報（前端載入時據此接回進度） | |
| GET | `/api/report-runs/{run_id}/stream` | 重連背景 run：先重播已發生的事件（**不含 `token`**，且 `section_draft` 只保留 position/section_key/heading）、再接直播（未知 `run_id` 回 404） | SSE |
| POST | `/api/report-runs/{run_id}/cancel` | 主動中止背景生成（關分頁不等於取消） | |
| GET | `/api/report-templates` | 可選 PDF 模板清單（M9b registry：ib-classic／broker-modern／privatebank-dark） | |
| POST | `/api/report-doc/{report_id}/rerender` | 換模板重出 PDF（**零 LLM**，產物落不可變表 `report_rendition`；失敗保留上一版） | |
| GET | `/api/report-doc/{report_id}/pdf` | 下載生成的深度研報 PDF（缺檔即由 markdown 重建） | |
| GET | `/api/report/{report_id}/full` | 原始報告 metadata（供 modal） | |
| GET | `/api/report/{report_id}/file` | 原始報告檔（PDF inline / 其他 attachment） | |
| GET | `/api/radar/instruments` | 觀點雷達標的清單（可搜尋／分頁；預設附每檔精簡共識預覽） | |
| GET | `/api/instrument/{code:path}/radar` | 跨券商共識總覽（**`market` 為必帶 query**；讀取不呼叫 LLM） | |
| GET | `/api/instrument/{code:path}/radar/events` | 近期事件（穩定 offset 分頁；**`market` 必帶**） | |
| GET | `/api/instrument/{code:path}/radar/brokers/{broker:path}` | 單券商歷程（展開券商列才請求；**`market` 必帶**） | |
| GET | `/api/conversations`、`/api/conversations/{conversation_id}` | 對話串清單 / 單串內容 | |
| DELETE/POST | `/api/conversations/{conversation_id}`、`/api/conversations/{conversation_id}/delete` | 刪整串對話（POST alias 供 DELETE 不穩的邊緣環境回退） | |
| GET | `/api/history`、DELETE `/api/history/{qa_id}`、POST `/api/history/{qa_id}/delete` | 問答歷史清單 / 刪單題（POST 為相容 alias） | |
| POST | `/api/feedback` | 對某次回答記讚/倒讚 | |
| GET | `/api/progress` | 監控快照（DB 筆數、摘要／重點摘錄／訊號覆蓋、背景程序、忠實度查核，外加 `sync`＝每 3 小時排程同步的最新狀態、`unit_failures`＝`OnFailure` 告警的近期計數） | |
| GET | `/healthz` | **唯一免認證的 API 端點**：DB 探測，正常 200 `{"status":"ok"}`、DB 不可用 503 `{"status":"degraded"}`（結果快取 5 秒），供外部監控分辨「站台活著但 DB 掛了」 | |
| GET/POST | `/login`、POST `/logout` | 登入頁與登入／登出 | |
| GET | `/`、`/monitor`、`/help` | **302 導向** `/app/search`、`/app/monitor`、`/app/help`（舊 vanilla 頁已退場） | |
| GET | `/app`、`/app/{spa_path:path}` | SPA shell（所有深連結回同一份 `index.html`；`frontend/dist` 不存在時回 **503**） | |

> 認證是 deny-by-default middleware，白名單只有 `/login` 與 `/healthz`，另加前綴 `/app/assets/`。
>
> SSE 事件：
> - **問答**＝`status`(understanding，**首事件**) → `sources` → `status`(retrieved) → `status`(generating／reading／searching_web) → `token`… → `ext_sources` → `done` → `followups`。婉拒路徑（離題／時效且 registry 為空）在 `understanding` 之後改走 `sources`(空) → `notice` → `done`（沒有 `token`）；時效題若命中受信任資料則走 `sources`(空) → `status`(generating) → `token` → `ext_sources` → `done`。
> - **深度研報**＝`run`（**首事件**，帶 `run_id`／`elapsed_ms`，供重連取得 handle 與算 ETA）→ `status`(retrieving) → `sources` → `status`(outlining) → `outline` → `status`(writing) → (`token`｜`section_draft`｜`section_skipped`)×N → `status`(verifying) → `document_revision` → `status`(rendering) → `done`（帶 `download_url`）；任何階段失敗改送 `error`。
>
> **後端新增事件時務必同步加前端 parser 分支**：`parseReportEvent`／`parseAskEvent` 對未知 event 一律回 `null` 靜默丟棄，症狀只會是「進度條停在某個百分比不動」、沒有任何錯誤訊息。完整契約見 [docs/WORKFLOW.md](docs/WORKFLOW.md#web-api)。

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

**DB 連線池與查詢逾時（`DB_*`）** — `DB_POOL_SIZE`(5)、`DB_MAX_OVERFLOW`(15)、`DB_POOL_TIMEOUT`(10s)、`DB_POOL_RECYCLE`(1800s)、`DB_STATEMENT_TIMEOUT_MS`(60000)、`DB_IDLE_TX_TIMEOUT_MS`(0＝關)、`DB_MAINTENANCE_STATEMENT_TIMEOUT_MS`(0＝不限)。

> **連線池是 per-process**（一個 uvicorn worker 一個池，每支批次腳本各自一個池）。**調大併發之前先算連線數**：`worker 數 × (DB_POOL_SIZE + DB_MAX_OVERFLOW) + 同時在跑的批次腳本數 × 2` 必須小於 DB 的 `max_connections` 扣掉 `superuser_reserved_connections`（官方 `pgvector/pgvector:pg16` 映像未覆寫 conf ⇒ 100 − 3 = 97；現況 1 × 20 + 3 × 2 = 26）。要加 `uvicorn --workers`、提高 `REPORT_SEMAPHORE` 或放寬 `web/routers/ask.py` 寫死的 `_ASK_SEMAPHORE`(3) 之前，重算這條式子——算式與逐項理由見 [.env.example](.env.example) 與 `app/config.py` 的 `DB_*` 區段。
>
> `DB_STATEMENT_TIMEOUT_MS` 是這組裡唯一真的在擋事情的：沒有它，**單一失控查詢可以無上限佔住一條連線**（雷達目錄與 overview 分面在現規模下都是全表掃描）——這才是連線耗盡的成因，不是「併發使用者太多」。`DB_IDLE_TX_TIMEOUT_MS` **預設關是刻意的**：`scripts/sync_new_reports.py` 會在交易開著時 spawn `claude` CLI 與跑嵌入，開了它等於讓每 3 小時一次的生產同步靜默丟報告；要開就只在 web 的 `.env` 開（批次腳本不讀 repo 根的 `.env`）。

**問答（`ASK_*`）** — 常用：`ASK_MAX_REPORTS`(15)、`ASK_MAX_PASSAGES`(4)、`ASK_MAX_CONTEXT_CHARS`(20000)、`ASK_RETRIEVAL_K`(15)、`ASK_DENSE_SCAN`(400)、`ASK_RELEVANCE_FLOOR`(0.62)、`ASK_STALE_AGE_DAYS`(180)、`ASK_MAX_STALE_REPORTS`(4)、`ASK_RECENCY_HALF_LIFE_DAYS`(90)、`ASK_INTENT_MODEL`(`claude-haiku-4-5`)、`ASK_MAX_QUEUE`(20，排隊上限；0＝不限)。

**深度研報（`REPORT_*`）** — `REPORT_MODEL`(`claude-sonnet-5`)、`REPORT_DEEP_K`(30)、`REPORT_MAX_REPORTS`(25)、`REPORT_MAX_PASSAGES`(6)、`REPORT_MAX_CONTEXT_CHARS`(40000)、`REPORT_TIMEOUT`(600s)、`REPORT_THIN_COVERAGE`(8)、`REPORT_ENABLE_WEB`(1)、`REPORTS_DIR`(`data/reports`)、`REPORT_SEMAPHORE`(1)、`REPORT_MAX_QUEUE`(5)、`REPORT_MIN_CITED`(3)。

**忠實度查核（M8）** — `REPORT_FAITHFULNESS_ENABLED`(1)、`ASK_FAITHFULNESS_ENABLED`(1)、`REPORT_FAITHFULNESS_MIN`(0.9)、`ASK_FAITHFULNESS_SAMPLE_RATE`(1.0)、`FAITHFULNESS_MODEL`(未設時沿用 `ASK_INTENT_MODEL`)、`FAITHFULNESS_TIMEOUT`(60s)。

> 查核結果寫入 `qa_log.evaluation` / `report_doc.evaluation`（jsonb：`faithfulness_score`、`numeric_support_rate`、`citation_coverage`、逐條 `claims`）。
> **全程 fail-open**：judge 異常或逾時會把該筆標 `degraded=true`、分數留 `None`，主流程不受影響——也就是說**關掉或壞掉都不會有錯誤訊息**，只會讓 evaluation 停止累積。
> 讀取路徑有兩條：監控頁 `/app/monitor` 的「忠實度查核」卡片（fail-open 計數與最後查核日期即為此而設），以及 `uv run python scripts/eval_faithfulness.py`（`--claims <id>` 可逐條主張下鑽）。

> 預設值集中在 **`app/config.py`** 的 `_load()`（frozen dataclass ＋ `os.getenv`），不必設定也能跑。各服務模組保留原常數名但改由 `get_settings()` 取值。
>
> 本表僅列常用鍵；`app/config.py` 共約 80 個旋鈕鍵，未列出的還有 `REPORT_RENDERER`(`typst`)、`REPORT_SECTIONED_ENABLED`(1)、`REPORT_DRAFT_BUDGET`(900s)、`REPORT_SECTION_*`、`REPORT_RUN_STALE_SECONDS`(1800s)、`ASK_RERANK_*`、`REPORT_MMR_*`、`QA_AGENTIC_*`、`TRUSTED_DATA_ENABLED` 等，以該檔為準。
>
> **集中化還沒做完，找旋鈕時別只翻 `app/config.py`**：`SSE_HEARTBEAT_INTERVAL`（`web/deps.py`）、`REPORT_SEMAPHORE`／`REPORT_MAX_QUEUE`（`web/routers/report.py` 的 `_REPORT_GATE`，由背景任務持有）、`ASK_MAX_QUEUE`（`web/routers/ask.py` 的 `_ASK_GATE`）、`REPORT_RUN_RETENTION_SECONDS`（`web/report_runs.py`）、`ASK_FOLLOWUP_MODEL`／`ASK_FOLLOWUP_TIMEOUT`（`app/services/followups.py`）、`REPORT_MARK_RERANK_WORKERS`／`REPORT_MARK_RERANK_TIMEOUT`（`app/services/retrieval_pipeline.py`）、`REPORT_MARK_DB_URL`（`app/services/db.py`）、`REPORT_MARK_MAX_TRACKED_FAIL_IPS`（`web/auth.py`）、`EVAL_JUDGE_MODEL`（`eval/judge.py`）仍是就地 `os.getenv`。另外 `/api/ask` 的併發上限 3 仍是**寫死**在 `web/routers/ask.py` 的 `_ASK_GATE`（沒有對應環境變數；可調的只有排隊上限 `ASK_MAX_QUEUE`）。
>
> **兩個上限都是 per-process**：`web/server.py` 啟動時以 `web/concurrency.py` 的 `assert_single_worker()` fail-closed 擋下多 worker（讀 `--workers` / `WEB_CONCURRENCY` / gunicorn `-w`；讀不到就放行並在啟動日誌印出有效上限）。要提高吞吐不能靠加 worker。

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
cd frontend && npm run lint      # ESLint（已在 CI 內）
make build-web                   # 產出 frontend/dist；CI 也會 build 並傳給後端 job

# lint（已在 CI 內；ruff format 刻意不做，見 pyproject.toml 註解）
uv run ruff check .              # E,F,I；line-length 120

# 離線評測 harness（改動檢索或生成品質時用它量測，不要另建一套）
uv run python eval/run_ragas.py        # M1：檢索／問答 RAGAS（凍結題集 + baselines/）
uv run python eval/run_report_eval.py  # M1b：研報結構化指標
uv run python scripts/eval_retrieval.py --queryset eval/queryset.json --json > eval/after.json

# 比較兩份結果（劣化超過容忍值即非零退出）——不要用人眼比表
# 兩份必須是同一套評測的產物；跨套（RAGAS vs 檢索）會被形狀指紋擋下
uv run python scripts/eval_compare.py --baseline eval/before.json --candidate eval/after.json
make eval-compare BASE=eval/baselines/baseline-2026-07-29.json CAND=eval/candidate-ragas.json TOL=0.03
```

**評測是手動工具，刻意不在 CI 內**：跑一輪 RAGAS 會 spawn `claude` CLI，與每 3 小時的 `report-mark-sync.timer` 搶同一個 CLI（`scripts/_claude_lock.py` 那把 flock **刻意不含 `llm.py`**，而 eval 走的正是 `llm.py`）。用法是「改動前後各跑一次，再用 `eval-compare` 比」。跑 RAGAS 請帶 `--concurrency 1`：預設的 3 會讓多個 `claude -p` 互搶而自我汙染（實測 8 題掉 3 題、延遲 213-222s vs 單執行緒 23-37s）。

`eval_compare.py` 的退出碼即結論：`0` 無劣化／`1` 有劣化／`2` **不可比**（樣本數、`ruleset_version`、`max_age_days` 不同，或結果自稱 `sufficient_n=false`）／`3` 有未分類指標。它只讀 `summary`、一個指標都不重算，方向表是白名單——**新增評測指標時要在 `METRIC_SPECS` 補一筆方向**，否則會被報成「未分類」（那是刻意的：默默把未知指標當成「越大越好」就是製造假綠）。三個絕對門檻（`FAITHFULNESS_MIN` / `CONTEXT_PRECISION_MIN` / `ANSWER_RELEVANCY_MIN`）是政策決定，比較器不碰、只比相對 baseline 的變化。

- `tests/` 放 Python 測試（`test_*.py`），涵蓋 filename/extract/retrieval/answer/report/report_writer/pdf/typst/auth/store/overview/scope_router/radar/reading/faithfulness 等；前端測試與元件同置，為 `frontend/src/` 下的 `*.test.ts(x)`。偏好以 mock 隔離 LLM、嵌入、檔案、DB 邊界。
- **CI**（`.github/workflows/ci.yml`）有**三個 job**：前端測試（ESLint + tsc + **vite build** + vitest）、後端測試（**ruff** + pytest）、**schema 契約**（`pgvector/pgvector:pg16` service container，套 `db/schema.sql` 兩次驗冪等 ＋ `content_norm` 等價性 ＋ CHECK 約束清單對帳）。前端 job 把 `frontend/dist` 當 artifact 傳給後端 job，`tests/test_spa_serving.py` 因此對**真 build 產物**驗證（缺 dist 是**紅**不是 skip；本機要跳過設 `SKIP_SPA_TESTS=1`）。**三個 job 皆為必要檢查**（2026-07-29 起；required check 名稱是 job 的中文 `name`，改名等於讓分支保護指向一個永不回報的 check，**改 `name` 就要同步改 GitHub 分支保護設定**）。main 有分支保護（strict ＋ enforce_admins）。**本機只跑 pytest 會在前端 job 上翻車。**
- **慣例**：確定性邏輯放 Python，Claude CLI 只用於語意標註/摘要/訊號/問答/研報；前端在 `frontend/src/`（React ＋ TS ＋ CSS Modules）；新增旋鈕加在 `app/config.py`。`REPORT_MARK_*` 前綴的**規則**是只給 `.env.example` 那五個 auth/DB 變數、新旋鈕一律不加前綴——但程式碼內另有幾個歷史遺留的同前綴鍵（如 `REPORT_MARK_RERANK_WORKERS`／`REPORT_MARK_RERANK_TIMEOUT`），**是 live 的，別當成命名錯誤改掉**。Python 側有 **ruff**（`E,F,I`、line-length 120，在 CI 內），**沒有** black/mypy/pre-commit，且 `ruff format` 是刻意不做的（會重排 60/90 個檔、洗掉 blame）——其餘風格約定見 [AGENTS.md](AGENTS.md)。
- **提交**：採 Conventional Commits（常見繁中 scope，如 `feat(report): …`、`fix(report): …`）；提交前看近期訊息與 staged diff，勿用整句英文當訊息。
- **改後端要重啟、改前端要 build**：`make serve` 無 `--reload`；SPA 由 `frontend/dist` 提供，前端改動須 `cd frontend && npm run build`（`/app/assets/*` 走 `_ImmutableStatic` 長快取）。`_NoCacheStatic` 只剩 `web/static/login.html` 走。

---

## 部署

| 場景 | 做法 |
|------|------|
| 本機 | `make serve` → uvicorn `:8097`（啟動時 warm BGE-M3，免首查延遲）|
| 生產 Web | systemd `report-mark-web.service`（enabled、自動重啟）；改碼後 `sudo systemctl restart report-mark-web.service` |
| NAS 增量同步 | systemd `report-mark-sync.timer`（每 3 小時）→ `scripts/sync_new_reports.sh`（drvfs 唯讀掛載 → rsync delta → 增量 extract/tag/ingest → 依序補摘要 → 補重點摘錄，後兩段吃本輪 `--hashes-file`）；手動測試 `make sync-once`。**這條鏈才是生產實際的入庫路徑**，全量三支腳本只在初次建庫或補跑歷史時用。見 [docs/nas_scheduled_sync_deployment.md](docs/nas_scheduled_sync_deployment.md) |
| 對外存取 | Cloudflare Tunnel ＋ nginx 邊緣（`deploy/docker-compose.yml`，無入站埠）：`make up-edge` / `down-edge` / `edge-logs` / `edge-reload`，需 `deploy/.env` 的 `TUNNEL_TOKEN`。見 [docs/EXTERNAL_ACCESS.md](docs/EXTERNAL_ACCESS.md) |
| 深度研報 PDF | 需安裝 Noto Sans CJK 字型；`REPORT_TIMEOUT` 建議 ≥300s。見 [docs/qa_pdf_report_deployment.md](docs/qa_pdf_report_deployment.md) |
| DB 備份 | systemd `report-mark-backup.timer`（每日 03:30）→ `scripts/db_backup.sh`：`pg_dump -Fc` **只備重建不回來的七張表**（`qa_log` / `report_doc` / `report_rendition` / `report_takeaway` / `report_signal` / `report_run` / `report_section`）到 NAS，保留 7 日 ＋ 4 週；手動跑一次 `make db-backup`。語料層刻意不備（重跑管線可還原）。**還原步驟與已知限制見 [docs/production_resilience.md](docs/production_resilience.md)** |
| 批次停更偵測 | systemd `report-mark-freshness.timer`（每日 08:30）→ `scripts/check_batch_freshness.py`（純 SQL、零 LLM）：查摘要／重點摘錄／觀點訊號的最新產出日，超過門檻即非零退出 → 走既有 `OnFailure` 告警鏈；手動跑一次 `make freshness`。**存在理由**：sync 殼把摘要／標題／摘錄設成 best-effort（失敗只記 log、不讓 unit 變紅），所以停更**不會**觸發 `OnFailure`——2026-07 實測 takeaway 停更 8 天、signal 停更 12 天都是事後才發現 |

對外請求路徑：`Browser ──HTTPS──▶ Cloudflare edge ──tunnel──▶ nginx:80 ──▶ uvicorn:8097`。

> **systemd unit 與 nginx 設定的真相來源在 `deploy/`，不是機器上的 `/etc`**：改完要 `sudo cp` 過去再 `daemon-reload`，直接在主機上改會讓 repo 與主機分岔。其中 `deploy/systemd/report-mark-web.service.d/path.conf` 是必要的——`claude` CLI 裝在 nvm 的 node bin、不在 systemd 預設 PATH，少了它 `/api/ask` 會以 `FileNotFoundError: 'claude'` 失敗（前端只顯示「問答服務發生錯誤」）。外部監控請打免認證的 `/healthz`（見上方 API 表）。細節見 [docs/production_resilience.md](docs/production_resilience.md)。

---

## Roadmap 現況

`docs/ROADMAP.md` 已於 2026-07-28 全面重寫為 **M0–M10 里程碑**敘事（舊的 Phase 0–3 編號與實際里程碑衝突，已作廢）。

**已上線**：語料管線與混合檢索、設定集中化（M0）、eval harness 與凍結題集（M1／M1b）、cross-encoder rerank（M2）、問答 UX（M3）、五類範圍路由（M4）、受信任時效資料（M4a）、證據帳本（M4b）、agentic 多輪補查（M5）、研報多查詢檢索＋MMR（M6）、逐節生成（M7）、忠實度查核（M8）、Typst 渲染主軌（M9a）、模板 registry 與零 LLM 換皮重出（M9b）、雙語輸出（M10）；另有**觀點雷達**（`report_signal` ＋ `/app/radar`）、研報閱讀頁、React SPA、`server.py` 拆 router、CI 與分支保護、生產韌性與 NAS 定時同步。

**尚未實作**：findb 整合（唯讀 Serve API 取行情／名稱）、每日簡報（`brief.py` ＋ 前端頁）、MCP server、對外 REST `/api/v1/*`（目前全站只有一組共用帳密的 session cookie，無 API key 機制）。**每項在 ROADMAP 都附「前置／阻礙」欄，動手前先看那一欄**。詳見 [docs/ROADMAP.md](docs/ROADMAP.md)。

---

## 延伸文件

| 文件 | 內容 |
|------|------|
| [docs/WORKFLOW.md](docs/WORKFLOW.md) | **端到端權威說明**：流程圖、逐階段 I/O、完整標籤詞表、Web API 契約、擴展與排錯 |
| [docs/ROADMAP.md](docs/ROADMAP.md) | 里程碑（M0–M10）與現況；未實作項附「前置／阻礙」 |
| [docs/EXTERNAL_ACCESS.md](docs/EXTERNAL_ACCESS.md) | Cloudflare Tunnel ＋ nginx 對外存取架構與維運 |
| [docs/nas_scheduled_sync_deployment.md](docs/nas_scheduled_sync_deployment.md) | NAS 定時增量同步（systemd timer）部署 |
| [docs/qa_pdf_report_deployment.md](docs/qa_pdf_report_deployment.md) | 深度研報 PDF（CJK 字型）部署 |
| [docs/production_resilience.md](docs/production_resilience.md) | 生產韌性：重啟策略、健康檢查、失敗告警、systemd unit 還原 |
| [docs/向量搜索優化報告.md](docs/向量搜索優化報告.md) | 向量檢索優化（混合檢索、HNSW 調校、CJK 正規化）|
| [AGENTS.md](AGENTS.md) | 貢獻者指南（結構、風格、測試、提交與安全慣例）|
