# report-mark 運作流程

研報市場標籤分類、向量檢索、RAG 問答與深度研報生成系統的端到端運作說明。

系統把 `研報自動匯入/` 內的券商研究報告，經過 **抽文字 → Claude 多維標註 → 切塊嵌入 → pgvector 入庫 → 語意檢索 / RAG 問答 / 深度研報 PDF**，產出可依 **市場／商品類型／標的／報告類型** 過濾並排序的研究助理服務。市場標籤對齊 [findb](../../findb) 的市場代碼。

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

    DB[("pgvector / PostgreSQL 16<br/>schema research<br/>research_report + report_chunk<br/>qa_log + report_doc<br/>report_signal + report_takeaway<br/>report_run/report_section + report_rendition")]

    SRC --> EX
    EX -->|"data/extracted/all.jsonl"| TG
    EX -.->|"extracted text"| IN
    TG -->|"data/tags/&lt;file_hash&gt;.json<br/>(市場/商品類型/標的…)"| IN
    IN -->|"BGE-M3 1024d + HNSW + trgm"| DB

    ORC["resume_corpus.sh<br/>標註＋導入並行、可續跑"] -.-> TG
    ORC -.-> IN

    DB --> WEB["web/server.py (FastAPI)<br/>BGE-M3 常駐 + auth + API composition"]
    DB --> CLI["search.py (CLI)"]
    WEB --> UI["frontend/dist（React SPA）<br/>檢索 / 問答 / 觀點雷達 / 閱讀頁 / 監控 / 說明"]
    WEB --> QA["answer.py<br/>RAG 問答 + qa_log"]
    WEB --> RP["report.py + report_writer.py<br/>逐節生成（M7）+ report_run/report_section<br/>typst_render.py 主軌（pdf.py 回退）→ report_doc"]
```

**耦合鍵**：`file_hash`（SHA256）貫穿所有階段，讓 Python（確定性處理）與 Claude（語意標註）兩端解耦，並支援 checkpoint-resume。

> **抽樣原型路徑**（小規模驗證）：`select_sample.py（分層抽樣 ~80 檔）→ extract_batch.py → make_worklist.py → tag_reports.workflow.js（Claude Workflow 5-agent fan-out）→ run_ingest.py`，產物為 `data/extracted/sample.jsonl` 與 `data/worklist_batch*.json`。邏輯與全量路徑相同，只是規模較小。

---

## 責任分工

| 由誰負責 | 工作 |
|----------|------|
| **Python**（確定性、可重現） | 檔名解析、抽文字、掃描檔偵測、分塊、BGE-M3 嵌入、去重入庫、檢索、**引文/chunk 錨回原文字元區間**（`reading/anchor.py`）|
| **Claude**（語意理解） | 讀報告文字判定多維標籤：主要市場（findb 代碼）、is_research、confidence、商品類型、個股/期貨關聯、具體標的；**閱讀頁重點摘錄的「論點＋逐字引文」（不給 offset）**；**觀點雷達訊號的評等／目標價／EPS 預估／四維論點（只依固定 schema 擷取單篇事實，跨券商共識與跨期變動不由它算）**|

> 券商來源、報告日期、報告類型、股票代碼等 metadata 由**檔名解析**（`filename.py`）取得，不經 Claude。

---

## 資料層

| 層 | 位置 | 內容 |
|----|------|------|
| 來源 | `研報自動匯入/` | 原始 PDF/docx（唯讀，不更動）|
| 中繼產物 | `data/` | 抽出文字 JSONL（`all.jsonl`／`sample.jsonl`）、工作清單、tag JSON、執行 log |
| Canonical | `research.research_report` | 每篇一列：市場/商品類型/標的等標籤 ＋ 檔名 metadata ＋ `full_text` ＋ `summary` |
| 向量 | `research.report_chunk` | 全文切塊 ＋ `vector(1024)`（HNSW cosine）＋ `content_norm`（pg_trgm 字面比對）|
| 問答紀錄 | `research.qa_log` | 每輪 Q&A 的 question/answer、來源、外部參考、conversation、回饋與延遲 |
| 生成研報 | `research.report_doc` | 深度研報 Markdown 真相來源、PDF 路徑、來源清單與對話/問答關聯 |
| 重點摘錄 | `research.report_takeaway` | 閱讀頁每篇 3-5 條「論點 ＋ 逐字引文」＋ 錨定出的原文字元區間；離線批次產生，讀取零 LLM |
| 觀點訊號 | `research.report_signal` | 觀點雷達：一列＝「一份研報 × 一個標的」的不可覆寫歷史快照（評等／目標價／EPS 預估／四維論點）；離線批次產生，讀取零 LLM |
| 生成狀態機 | `research.report_run` / `report_section` | M7 逐節生成：一列 run＝一次生成請求的完整生命週期；一列 section＝大綱中的一節 |
| 渲染產物 | `research.report_rendition` | M9b 不可變 rendition：同一份 markdown 換模板重出各存一列，不覆蓋歷史 PDF |

**`research.research_report` 欄位**：`id`、`file_hash`(唯一)、`file_name`/`file_path`、`market`、`is_research`、`confidence`、`stock_code`、`company_name`、`source`、`report_date`、`report_type`、`language`、`instrument_types[]`、`relates_stock`、`relates_futures`、`stock_targets[]`、`futures_targets[]`、`full_text`、`summary`、`title`／`title_original`／`title_source`（顯示標題三欄，見 ⑦）、`created_at`。

**`research.report_chunk` 欄位**：`id`、`report_id`(FK)、`chunk_index`、`content`、`embedding vector(1024)`、`content_norm`（`GENERATED STORED`：NFKC→去空白→小寫，對齊 `textnorm.norm_for_match()`）。

**互動表**：`qa_log` 以 `COALESCE(conversation_id, id)` 分組支援舊單題與新對話串；`report_doc` 用 `markdown` 作真相來源，PDF 檔遺失時可由 Markdown 即時重建。

**`research.report_takeaway` 欄位**：`id`、`report_id`(FK，`ON DELETE CASCADE`)、`ordinal`(1..N 顯示順序)、`claim`(論點，LLM)、`quote`(逐字引文，LLM)、`quote_start`/`quote_end`(Python 錨定結果；`NULL`＝錨不到，條目仍照常顯示。前端引文跳轉已於 2026-08-03 移除，這兩欄目前無讀取路徑，但批次仍在寫——那是刻意留的可逆性)、`anchor_method`(`exact`/`normalized`/`prefix`)、`text_sha256`、`extraction_version`、`extraction_status`(`pending`/`valid`/`partial`/`rejected`)、`raw_payload`、`error_detail`、`created_at`；`UNIQUE(report_id, ordinal)`。CASCADE 是刻意的：同 `file_hash` 重新 ingest（`store.upsert_report` 先刪後插）會連帶清除摘錄，下次批次偵測缺列自動補擷取＝要的冪等行為。

**`research.report_signal` 欄位**：`id`、`report_id`(FK，`ON DELETE CASCADE`)、`market`、`instrument_code`(來自 `stock_targets`)、`broker`(來自 `source`)、`report_date`、`rating_raw` ＋ `rating_normalized`(五級 `buy`/`overweight`/`neutral`/`underweight`/`sell`，無法映射為 `unknown`)、`target_price`(`numeric(18,4)`) ＋ `target_currency`/`target_horizon`/`target_price_evidence`、`eps_estimates`(jsonb 陣列)、`thesis_dimensions`(jsonb，`outlook`/`catalyst`/`risk`/`valuation` 各 `{stance, summary, evidence}`)、`extraction_version`、`extraction_status`(`pending`/`valid`/`partial`/`rejected`)、`raw_payload`、`error_detail`、`created_at`；`UNIQUE(report_id, market, instrument_code)`。**目標價保幣別、不換算、不混入跨券商中位數**；CASCADE 同摘錄，重新 ingest 即連帶清除、下次批次自動補擷。

**`research.report_run` / `report_section`**（M7 逐節生成狀態機）：run 有冪等鍵 `request_key`(UNIQUE)、`status`(`queued`/`retrieving`/`outlining`/`drafting`/`verifying`/`rendering`/`completed`/`failed`/`cancelled`)、`outline`、`checkpoint`(最後一致可續跑點) 與 `input_config` 快照；section 有 `position`(組裝序＝`[n]` 首見序)、`section_key`/`heading`、`draft_markdown`（對應 SSE `section_draft`，可覆寫）與 `final_markdown` 分離、`evidence_ids[]`。**`report_run` 對 `research_report` 刻意無 FK**——生成流程史不是語料衍生物，語料 upsert 先刪後插不該連帶清除它。

**`research.report_rendition` 欄位**（M9b）：`id`、`report_id`(反向連結 `report_doc`，plain uuid 非 FK)、`renderer`(`typst`/`weasyprint`)、`template_id`、`content_hash`(markdown 的 sha256——換皮不重生內容，故同 hash)、`pdf_path`、`status`、`created_at`。每列 `pdf_path` 各異，**不覆蓋歷史 PDF**；重出成功後才原子切換 `report_doc.current_rendition_id`。

**索引**：`report_chunk.embedding` HNSW(cosine)、`content_norm` GIN(trgm)；`research_report` 的 `market` btree、`instrument_types`/`stock_targets`/`futures_targets` GIN；`qa_log` 依建立時間與對話分組索引（另有 `request_id` 的部分唯一索引）；`report_doc` 依 `qa_id` 與 `conversation_id` 索引；`report_signal` 依 `(market, instrument_code, report_date DESC)`、`(market, instrument_code, broker, report_date DESC)`、`report_id` 與 `extraction_status` 索引；`report_section.evidence_ids` GIN。DB schema 定義於 [`db/schema.sql`](../db/schema.sql)（DDL 皆 `IF NOT EXISTS`，可冪等套用於既有庫）。

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

### ④ 重點摘錄擷取 — `scripts/extract_takeaways.py`（Claude CLI，閱讀頁用）
- **輸入**：DB 內近 `--since-days`（預設 90）天、有全文的研究報告。餵給 LLM 的是 **`clean_extracted(full_text)` 的前 `--excerpt`（預設 24000）字**，不是 `full_text` 本身（見下方不變量）
- **做什麼**：asyncio ＋ Semaphore（`--workers` 預設 2）逐報告 spawn `claude -p`（Sonnet），依固定 schema 擷取 3-5 條 `{claim, quote}`；**LLM 只出語意、不給 offset**，Python 端以 `app/services/reading/anchor.py` 的 `locate_quote` 把引文確定性錨回正典文字；每份報告在單一 transaction 內 DELETE ＋ 全量 INSERT（非 upsert）
  - **checkpoint-resume 條件**：該報告已有列、且 `extraction_version` 與 `text_sha256` **皆相符**、且狀態 ∈ (`valid`, `partial`) → 跳過。任一不符即重擷（全文變了、擷取版本升級了，舊 offset 就不可信）
  - 單筆失敗只寫 `data/takeaway_failures.log`，不中斷、不影響檢索/問答
- **輸出**：`research.report_takeaway`（閱讀頁 `/api/reading/{file_hash}` 讀取時零 LLM）
- **指令**：`make takeaways`＝`uv run python scripts/extract_takeaways.py`；旗標 `[--since-days 90] [--hashes-file PATH] [--workers 2] [--limit N] [--excerpt 24000] [--model M] [--reextract] [--dry-run]`
- **成本**：預設 90 天約 549 篇、約 2-3 小時；全語料 14,575 篇要跑十天以上，故預設不跑全量
- ⚠️ **接排程一律用 `--hashes-file`（它會忽略 `--since-days`），不要用 `--since-days 1`**：後者濾的是 `report_date`（研報自己標的日期）而非入庫時間，而 NAS 匯入的研報日期常比入庫日早——實測近 10 天入庫的 90 篇裡有 79 篇（88%）`report_date` 超過一天前，用天數接排程會**靜默**漏掉近九成新研報。`scripts/sync_new_reports.sh` 走的就是 `--hashes-file data/.sync_last_hashes`（本輪新入庫的 `file_hash` 清單）。
- ⚠️ **不可與其他 `claude` CLI 批次同時跑**：併發搶 `claude` CLI 會讓擷取大量被誤判 `rejected`（真因不是資料壞、也不是模型壞，是搶資源）。現由 `scripts/_claude_lock.py` 的跨進程鎖強制，見下方〈claude CLI 批次互斥〉。

> **不可妥協的不變量：正典文字＝`clean_extracted(full_text)`**。`research_report.full_text` 存的是**未清理**的原始抽取文字（`ingest_all.py` 寫 `full_text=raw_text`，但 chunk 走 `chunk_text(clean_extracted(raw_text))`），保留 PDF 抽字的 CJK 間空白（「台 積 電」）。**餵 LLM 的 excerpt、錨點基準、API 回傳的文字三者必須同源**，`text_sha256` 是這個不變量的守衛（讀取時比對「擷取當時的 sha」vs「當前正典文字的 sha」，不符即把該條的錨點收回）。拿 `full_text` 當基準會讓所有 offset 全錯，而且**測試抓不到**——引文照樣「錨得到」，只是錨在錯的座標系。同理，`report_chunk.content` 因切塊 overlap 而不是 `full_text` 的子字串（天真的 `full_text.find(chunk.content)` 約 99% 無聲失敗），定位一律走 `anchor.py`。

### ⑤ 訊號擷取 — `scripts/extract_signals.py`（Claude CLI，觀點雷達用）
- **輸入**：DB 內 `stock_targets` 命中「涵蓋子集」的研究報告——子集門檻 `--min-brokers`（預設 3）／`--min-reports`（預設 5）／`--top-n`（預設 50，依覆蓋度取前 N 檔標的）。每份報告的 `requested_codes` ＝該報告 `stock_targets` ∩ 該市場子集；餵給 LLM 的是 `full_text` 前 `--excerpt`（預設 16000）字（訊號擷取只取數值與論點，不做字元錨定，故不需要 ④ 那條正典文字不變量）
- **做什麼**：asyncio ＋ Semaphore（`--workers` 預設 2）逐報告 spawn `claude -p`（預設 Sonnet），依固定 schema 擷取評等／目標價／EPS 預估／四維論點，Python 端正規化（五級評等映射、目標價保幣別）後 upsert；**一列＝「一份研報 × 一個標的」的不可覆寫歷史快照**。**checkpoint-resume 條件**：該報告的所有 requested 標的皆已有列、狀態 ∈ (`valid`, `partial`) 且 `extraction_version` 相符 → 跳過（`--reextract` 強制重跑）
- **輸出**：`research.report_signal`（讀雷達時零 LLM——跨券商共識、四分位與跨期變動全由 `app/services/radar/` 決定性計算）
- **指令**：`make signals`＝`uv run python scripts/extract_signals.py`；旗標 `[--min-brokers 3] [--min-reports 5] [--top-n 50] [--workers 2] [--limit N] [--excerpt 16000] [--model M] [--reextract] [--dry-run]`
- **刻意只跑高覆蓋子集**：全語料僅約 0.68%（99 篇）有訊號。**「沒有訊號」是常態不是錯誤**——雷達對「有研報但尚未擷取」回 200 的 `pending_extraction` 空狀態（完全查無研報才 404），閱讀頁則整區不進 DOM。
- **已納入排程（每 3 小時，每輪限量）**：`scripts/sync_new_reports.sh` 最後一段跑 `--limit ${SYNC_SIGNAL_LIMIT:-15}`，且**刻意不綁本輪新檔**——它排的是跨全語料的積壓（2026-07-30 實測待擷取 5047 份），綁新檔的話沒有新研報進來就完全不動，雷達正是這樣從 2026-07-16 起靜止兩週。**`--limit` 是安全機制不是調校旋鈕**：不設上限＝連續佔住 claude 鎖八十小時以上，期間每輪匯入撞鎖 rc=75，而匯入撞鎖的那批研報會從 delta 消失（`rsync --size-only` 下輪不再列出），得靠 `--all-local` 手動補。擷取順序是 `report_date DESC`，故限量取的一定是最新那幾份：雷達保持最新，歷史積壓在背景慢慢排。
- ⚠️ **不可與其他 `claude` CLI 批次同時跑**（同一個搶 CLI 的坑，見 ④；互斥由 `scripts/_claude_lock.py` 強制）

### ⑥ 摘要生成 — `scripts/generate_summaries.py`（Claude CLI）
- **輸入**：`research_report` 中 `summary IS NULL`、有全文、`is_research IS NOT FALSE` 的列；帶 `--hashes-file` 時只補該清單列出的 `file_hash`（定時同步走這條，不掃歷史 NULL 積壓）
- **做什麼**：asyncio ＋ Semaphore（`--workers` 預設 2）逐報告 spawn `claude -p`（Sonnet），依 `full_text` 前 `--excerpt`（預設 12000）字產 2-3 句、約 100-150 字的中文摘要；只補 NULL 故冪等可續傳
- **輸出**：`research.research_report.summary`（檢索結果卡片與閱讀頁直接顯示）
- **指令**：`make summaries`＝`uv run python scripts/generate_summaries.py`；旗標 `[--workers 2] [--limit N] [--excerpt 12000] [--hashes-file PATH]`
- ⚠️ 同樣受 `claude` CLI 併發之限：`scripts/sync_new_reports.sh` 在增量匯入後會**自動依序**跑本階段與 ④（見下方〈claude CLI 批次互斥〉）

### claude CLI 批次互斥（`scripts/_claude_lock.py`）

`claude` CLI 是跨進程共用資源。會 spawn 它的批次——`tag_all_cli.py`（②標註）、`sync_new_reports.py`（增量匯入時的行內標註）、`generate_summaries.py`（⑥）、`generate_titles.py`（⑦）、`extract_takeaways.py`（④）、`extract_signals.py`（⑤）——併發互搶的症狀不是「壞掉」而是**擷取被大量誤標 `rejected`**：資料沒壞、模型也沒壞，只是 CLI 被搶。

規約以前只寫在註解與文件裡，但 `report-mark-sync.timer` 每 3 小時會自動跑「增量匯入 → 摘要 → 標題 → 摘錄 → 訊號」，文件攔不住排程。現在改由鎖強制：

- **機制**：`fcntl.flock(LOCK_EX | LOCK_NB)` 於鎖檔 `data/.claude_cli.lock`；各批次在 `main` 進入點取一次（**不在 per-report 迴圈內**）。
- **撞車行為**：後啟動者印出持有者（腳本名／pid／起始時間）並以 **`rc=75`**（`sysexits.h` 的 `EX_TEMPFAIL`）結束——刻意與「批次自己壞了」分開，讓排程殼能分別處置。
- **為什麼是 flock 而不是 PID 檔**：flock 綁在開啟檔案描述子上，持有者行程**無論怎麼死（含 SIGKILL）都會自動釋放**，不留陳舊鎖；PID 檔則會在強殺後殘留，且 PID 被回收時 `kill -0` 還會誤判為存活。代價是只在單機有效——這些批次本來就只跑一台。
- **排程側的可見化**：`scripts/sync_new_reports.sh` 的摘要／摘錄兩段是 best-effort（失敗不擋 sync、unit 不會變紅），所以三個階段的非零退出都會補記一筆到 `data/unit_failures.log`（`OnFailure` 告警既有的落點）。匯入段若因鎖而未執行，還會印出復原指令——**rsync 已把新檔落到本地，下一輪 delta 不會再列出它們**，得用 `scripts/sync_new_reports.py --all-local` 補漏。
- **逃生口**：`CLAUDE_LOCK_DISABLE=1` 完全繞過（會在 stderr 印警告）。刻意不放進 `.env.example`——批次是 `uv run python scripts/...` 直接跑、不載入 `.env`。
- **`app/services/llm.py` 不在此鎖範圍內**，且不可加入：它是 `/api/ask` 與研報生成的同一個 spawn 點，納入鎖等於讓一輪數小時的 `tag_all_cli` 把線上問答鎖死。守門在 `tests/test_claude_lock.py`。

### ⑦ 顯示標題產生 — `scripts/generate_titles.py`（Claude CLI）
- **為什麼**：`file_name` 多是券商流水號（624726992507895929_260728_gs_umt.pdf），列在卡片、來源與閱讀頁頁首上讀者看不懂。報告的真正標題印在首頁內文裡，本階段把它抽出來
- **輸入**：`research_report` 中 `title IS NULL`、有全文、`is_research IS NOT FALSE` 的列（帶 `--hashes-file` 時只補該清單）。餵給 LLM 的是 **`clean_extracted(full_text)` 的前 `--excerpt`（預設 3000）字**——標題在首頁，故摘錄遠比 ④⑥ 短；不清理則「台 積 電」會讓模型讀錯詞。候選依 `report_date DESC` 排序：跑不完全語料時先讓最近的報告有標題
- **做什麼**：asyncio ＋ Semaphore（`--workers` 預設 2）逐報告 spawn `claude -p`（Sonnet），一次呼叫涵蓋三種情形並記在 `title_source`：
  - `extracted`：內文標題已是中文 → 原樣保留
  - `translated`：英文/其他語言標題 → 譯為繁體中文，原文存 `title_original`
  - `generated`：內文根本沒有標題（掃描件、純表格日報）→ 依重點自擬一句話標題
- **失敗即留 NULL**：抽字損毀/亂碼時提示詞要求模型回 `null`（不要猜），`parse_title()` 也**刻意沒有純文字 fallback**——模型不照格式輸出時多半是把整段內文吐回來，寧可讓前端回退檔名，也不要顯示一段錯的標題。失敗記 `data/title_failures.log`
- **輸出**：`research.research_report.title`（讀取時零 LLM；所有呈現層一律「有標題顯示標題、缺標題回退檔名」）
- **「一律繁體中文」是 prompt 的機率性保證，確定性收尾在 `app/services/zh_hant.py`**：2026-07-31 的台股頭條就是模型把英文標題翻成了整句簡體（`title_source=translated`）。④⑤⑥⑦ 四支批次的 LLM 轉述文字都過這一關（`title`／`summary`／`claim`／thesis `summary`），**逐字引文與原句刻意不過**（理由見下方工具段）。線上的兩條串流路徑同樣有接：問答（`app/services/answer.py` 的主 RAG 與 overview 收尾，畫面靠 `done` 的加法欄位 `answer` 校正）與深度研報（`app/services/report.py` 的**單次與逐節兩個組裝點**，前端不渲染研報草稿故不需要校正事件）。存量由 `scripts/backfill_traditional.py` 清。比照本 repo 對研報版面的一貫作法：用 Python 收尾，不去改四份平行的 prompt 副本
- **指令**：`make titles`＝`uv run python scripts/generate_titles.py`；旗標 `[--workers 2] [--limit N] [--excerpt 3000] [--hashes-file PATH]`
- ⚠️ 同樣受 `claude` CLI 併發之限（互斥由 `scripts/_claude_lock.py` 強制，見下方）：`scripts/sync_new_reports.sh` 在增量匯入後會**自動依序**跑本階段（只補本輪新研報）

### 編排與離線優化
- **`scripts/resume_corpus.sh`**：一鍵編排——鎖檔（`data/.resume_corpus.lock` + PID 檢查）防重入，並行起 `tag_all_cli.py` 與 `ingest_all.py`，待首輪導入消化 backlog → 等標註全數完成 → 補跑 catch-up 導入；各階段時間戳記寫 `data/resume_orchestrator_*.log`。`bash scripts/resume_corpus.sh`
- **`scripts/ingest_lowio.sh`（或 `make ingest-lowio`）**：`ingest_all.py` 的包裝，**離線大量導入**時以 `ALTER SYSTEM` 暫關 Postgres durability（`fsync`/`full_page_writes`/`synchronous_commit`）降磁碟 I/O，並用 `trap` 確保正常/錯誤/Ctrl-C 都會還原。⚠️ **僅限 DB 未對外服務時使用**（關 fsync 期間若主機/DB 崩潰，research 庫不可復原，但可由原始報告重新導入）。中斷未還原時用 `make restore-durability` 重設。

### 工具（一次性 / 維運）
- **不要再寫一支「清理 chunk 空白」的批次更新——那正是已刪除的 `make normalize` 的死法。** 2026-07-29 連同 scripts/normalize_chunks.py 一併移除（**該檔已不存在**）。它自稱「冪等、重跑 0 筆更新」，**那是錯的**：腳本用 `clean_text`，而 chunk 是 `chunk_text(clean_extracted(...))` 產生的——`clean_text` 把換行折成空格，而 chunk 內的段落正是用**單一換行**接起來的。兩組獨立樣本實測 **95.95%／98.66%** 的 chunk 會被改動（換行數歸零），而 `norm_for_match` 前後不同者 **0**＝`content_norm` 一個字都不會變（該 GENERATED 表達式本來就移除所有空白）：**純破壞、零收益**，外加表與 HNSW 索引雙倍膨脹。**改用 `clean_extracted` 也不行**（`_RE_CJK_GAP` 同樣吃掉段落間那個換行，實測仍破壞 51%）。要改 chunk 內容只有重跑 `ingest_all.py` 一條路。逐步推導見 [`docs/ARCHITECTURE_REVIEW_2026-07.md`](ARCHITECTURE_REVIEW_2026-07.md) 的 P0 第 1 項與 [`ARCHITECTURE_REVIEW_2026-07_VERIFY.md`](ARCHITECTURE_REVIEW_2026-07_VERIFY.md)。
- **`scripts/backfill_full_text.py`**：由 `sample.jsonl` 回填 `research_report.full_text`（欄位後加時補；僅抽樣路徑）。
- **`scripts/align_findb_markets.py`**：把既有中文市場標籤確定性重映射為 findb 代碼（同改 `data/tags/*.json` 與 DB），冪等、不需重跑 Claude。
- **`scripts/backfill_traditional.py`**：回填 LLM 產出顯示文字裡的簡體字（`title`／`summary`／`report_takeaway.claim`／`report_signal.thesis_dimensions[*].summary` 四欄）。判定與轉換共用 `app/services/zh_hant.py`，與四支批次的寫入端同一份規則，所以跑完再跑必為 0 筆（冪等）。預設唯讀試跑，`--apply` 才寫入。
  - **`quote`／`evidence` 刻意不在涵蓋範圍**：那是逐字引文與原句。**第一個理由與任何功能無關**——改一個字它就不再是逐字引文，「原文就是這麼寫的」正是它存在的全部意義。`quote` 另外還是 `app/services/reading/anchor.py` 的錨定基準，全語料有 63 篇研報原文本身就是簡體，把引文轉繁體會讓它在正典文字裡再也找不到，而錨不到不會報錯、只會讓 `quote_start`／`quote_end` 靜默留空。**前端引文跳轉已於 2026-08-03 隨文字檢視移除，這條規則一個字都不能鬆**（跳轉沒了只是讓第二個理由暫時看不見）。`full_text`／`report_chunk.content` 同理更不能動（後者另有 `make normalize` 的死法為鑑）。
- **`scripts/check_batch_freshness.py`（或 `make freshness`）**：偵測派生資產是否**停更**。純 SQL 一次查四個 `max(created_at)`（語料／摘要／摘錄／訊號），零 LLM、零寫入；退出碼 `0`＝新鮮、`1`＝停更、`2`＝查不到（DB 不可用，處置不同故刻意分流）。平時由 `report-mark-freshness.timer` 每日 08:30 觸發，非零退出經 `OnFailure=report-mark-alert@%n.service` 走既有告警鏈。
  - **為什麼不能靠 `OnFailure` 就好**：④⑥⑦ 三段掛在 sync 殼的 `|| RC=$?` 之後，是刻意的 best-effort（摘要失敗不該擋住下一輪匯入），所以連續失敗**永遠不會**讓 unit 進 `failed` ⇒ `OnFailure` 一次都不觸發。2026-07 實測 takeaway 停更 8 天、signal 停更 12 天。
  - **兩個刻意的預設**：(a) 有一層**語料閘**——三支批次都只吃「本輪新入庫」的研報，語料自己在同窗期內沒前進時派生資產的過期一律判 `suppressed`（少了它，一個連假就讓三個資產同時亮紅）；(b) `signal` 預設門檻 **0＝不告警**——**理由不是「沒有排程產生者」**（`extract_signals.py` 自 PR #150 起就在 sync 殼裡，每輪 `--limit 15`），而是**它的停更在原理上無法與正常區分**：訊號只來自高覆蓋子集，排程把積壓跑完之後 `max(created_at)` 本來就不再前進，那與「這段時間沒有合格研報」完全一樣。設任何門檻都會在積壓耗盡當天開始每日假警報。**訊號靠的是失敗記錄而非新鮮度**——sync 殼對 `extract_signals` 非零退出會 `record_unit_failure`，`/api/progress` 的 `unit_failures` 讀它（`tests/test_batch_freshness.py` 釘住那個呼叫，拿掉訊號就完全沒有偵測）。真要開＝`--signal-days 14`，但先想清楚積壓耗盡後怎麼辦。

### 檢索、問答與深度研報
- **CLI** `scripts/search.py`：嵌入查詢 → cosine top-k，可加 `--market <findb 代碼>` 過濾
- **Web 檢索** `web/server.py`（組合層）+ `web/routers/*.py` + `frontend/`（React SPA）：FastAPI 啟動時背景暖機 BGE-M3 與 reranker（**必須依序，不可並行**：兩執行緒同時首次 import 會競態出 `ImportError: cannot import name is_torch_npu_available`），`/api/search` 走 dense + pg_trgm 字面召回、報告層聚合、tier/band/日期排序；`/api/reports` 提供無關鍵字瀏覽與分頁。
- **RAG 問答** `app/services/answer.py`：`retrieval_pipeline.retrieve_context`（＝`embed_query_cached → hybrid_search → rerank → build_context`）→ `stream_completion` → `qa_log`。**`answer.py` 自己不呼叫 `hybrid_search`**（頂層那個 import 已無呼叫點），檢索編排住在 `app/services/retrieval_pipeline.py`；`build_context`／`select_reports` 仍定義在 `answer.py`。來源以 `[n]` 編號，支援多輪對話、語料總覽問題、離題拒答、外部網搜來源、追問建議、讚倒讚與對話刪除。
- **深度研報** `app/services/report.py` + `report_writer.py`（逐節，預設開）+ `typst_render.py`（主軌）/ `pdf.py`（回退）：`/api/report` 以較深召回與較大 context 生成 Markdown 研報，必要時主動網搜補覆蓋，渲染成品牌化 PDF，並把 Markdown/PDF/source 寫入 `report_doc`。
- **觀點雷達** `app/services/radar/`：`/app/radar` 以 ⑤ 落庫的 `report_signal` 為底，做「單一標的跨券商」的共識快照、四維論點、近期事件時間軸與單券商歷程。`types.py`（值型別）→ `queries.py`（純 SQL）→ `scale.py`（純函式）→ `compute.py`（聚合）→ `schemas.py`（HTTP 契約，前端 zod 逐字鏡像其 `Literal` enum）；**四分位、共識與跨期變動全是 Python 決定性計算，讀取零 LLM**。共識一律取「每家券商窗期內最新一筆有效訊號」，不把同券商舊報告累加。`STANCE_CONSTRUCTIVENESS`／`THESIS_DIMENSIONS` 是 `signal_extract.py` 與 `radar/scale.py` 的**共用契約**（radar 端 import，一處為準）。
- **研報閱讀頁** `app/services/reading/`：`/app/report/:file_hash` 把一份研報的原文、語料已知的一切與下一步動作收攏到一個可分享的網址（以 `file_hash` 為鍵——`report_id` 重新 ingest 會換新，分享連結會失效）。`anchor.py` 負責錨定、`queries.py` 純 SQL 取數、`schemas.py` 為凍結的 API 契約（前端 zod 逐字鏡像）；摘錄早在 ④ 已落 DB，**讀取時零 LLM**。依資料現況優雅降級：無摘錄→整區不渲染；無訊號→整區**不進 DOM**（99.3% 的報告如此，是常態不是錯誤）；**內嵌不了原始檔（非 PDF 或檔案不存在）→ 落到正典文字後備**，那是資料現實決定的、不是使用者可選的檢視（`[原文][文字]` 切換已於 2026-08-03 移除）。

**全站需登入**（共用帳密，env 設定；未登入導向 `/login`，可登出）——認證細節見 `web/auth.py` 與 [docs/EXTERNAL_ACCESS.md](EXTERNAL_ACCESS.md)。

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
| `GET /api/progress` | 供 `/app/monitor` 使用的 ingestion、tagging、summary、DB 與背景程序進度，外加**派生資產新鮮度**（`takeaway`／`signal` 各回近 30 天窗口的 done/total/remaining/pct ＋ 全表最新產出日 `latest`）與 **M8 忠實度查核健康度**（`evaluation`）。**全表覆蓋率不能當訊號**（摘錄與訊號都刻意只跑子集），要看的是近期窗口與 `latest` 有沒有前進。另回**排程可見度**兩塊：`sync`（`data/sync_run_*.log` 的最後一行＋完成判定——生產實際的入庫路徑，先前 runtime 區塊只認全量腳本的 log 而完全看不到它）與 `unit_failures`（`data/unit_failures.log` 的 `count_24h`／`count_7d`／`latest`／最近 5 筆；**時間窗計數而非累計未讀數**，累計數會讓紅點永遠亮著）|
| `GET /api/markets` | findb 市場代碼清單 |
| `GET /api/search` | 語意檢索並**依報告分組**。參數：`q`（必填）、`market`、`instrument_type`、`relates_stock`、`relates_futures`、`report_type`、`sort`（`relevance` 預設／`date_desc`／`date_asc`）、`limit`、`offset`、`passages`。每篇回傳 best_score、命中片段數、券商/日期/類型/標的 metadata、摘要與清理後片段，以及 `file_hash`（閱讀頁 `/app/report/:file_hash` 的連結鍵）。信封另有 `lexical_truncated`：字面路候選是否已被 `LEX_CAP_SEARCH`(8000) 截斷。**截斷時結果本身就不穩定**——`store._lexical_sql` 的 `LIMIT :cap` 沒有 ORDER BY，取到哪 cap 列由 heap 物理順序決定（`synchronize_seqscans` 預設 on ⇒ 併發 seq scan 從任意 block 起掃），同一查詢在不同時刻可能回不同結果。這個旗標存在的目的是**先量出發生率**，不是要照著加排序鍵（加了會逼掃完全部命中列，是淨損失，見該函式 docstring）|
| `GET /api/reports` | 無關鍵字瀏覽：依 `sort`（`date_desc` 預設／`date_asc`）列出，支援與 search 相同的篩選參數 ＋ `limit`/`offset` 分頁；同樣回 `file_hash` |
| `GET /api/report/{report_id}/full` | 單篇 metadata 與原始檔狀態（`has_file`），供詳情 modal——**現在只有問答頁的引用來源與雷達頁的報告連結會開它**，檢索結果改導向閱讀頁 |
| `GET /api/report/{report_id}/file` | 回傳原始檔（PDF 以 inline 內嵌、其他下載）|
| `GET /api/reading/{file_hash}` | 閱讀頁骨架：meta ＋ 標籤 ＋ 摘要 ＋ 重點摘錄 ＋ 訊號。**不含全文**（絕大多數研報直接內嵌 PDF，文字另取）。`file_hash` 格式不符直接 422、查無報告 404。摘錄的 `quote_start`/`quote_end` 在三種情形由**後端**收回為 `null`：錨不到、驗章不過（正典文字已漂移）、落在 `/text` 的截斷範圍之外 —— **錨點有效與否只該有一個真相來源**，消費端不自行判斷截斷。（前端引文跳轉已於 2026-08-03 移除，這三欄目前無讀取路徑；規則與批次寫入都刻意保留）|
| `GET /api/reading/{file_hash}/text` | 正典文字（＝`clean_extracted(full_text)`），所有 offset 以此為準。超過 40 萬字只回前綴並標 `truncated`，但 `text_sha256`/`text_chars` 一律是**完整**正典文字的值（回截斷版的 sha 會讓前端驗章全滅）。`?chunk=N`＝檢索命中的 `chunk_index`，一併回該段字元區間；錨不到、或錨點落在截斷範圍之外，則為 `None` 且仍回 200（**沒有命中位置不是錯誤**）。**SPA 已不再帶這個參數**（前端命中定位隨文字檢視於 2026-08-03 移除），端點側刻意保留 |
| `GET /api/reading/{file_hash}/similar` | 相似研報（全篇均勻取樣 probe ＋ 廣度加權的向量近鄰）；`limit` 預設 6、上限 20 |
| `GET /api/radar/instruments` | 觀點雷達「選標的」目錄：有可展示訊號的標的清單。參數 `market`／`q`／`limit`（預設 50、上限 100）／`offset`／`with_consensus`（預設 true，當頁每檔附精簡共識預覽，以單次批次查詢算完避免 N+1）|
| `GET /api/instrument/{code:path}/radar` | 跨券商總覽：共識快照 ＋ 四維論點 ＋ 近期事件 ＋ 券商清單。`market` **必填**（findb 代碼）、`window` ∈ `30`/`90`/`180`/`all`（預設 `90`）。**讀取零 LLM**——差異全由 `app/services/radar/` 決定性計算。完全查無研報 → 404；有研報但尚未擷取訊號 → 200 的 `pending_extraction` 空狀態（**沒有訊號不是錯誤**）|
| `GET /api/instrument/{code:path}/radar/events` | 與總覽同源的完整近期事件，提供穩定 offset 分頁（`limit` 預設 12、上限 50）；`market` 必帶 |
| `GET /api/instrument/{code:path}/radar/brokers/{broker:path}` | 單券商歷程（展開券商列才延遲載入）：全歷程快照 ＋ 相鄰差異；`market` 必帶。**路徑上的 `:path` 是 Starlette 轉換器、不是排版**——它會連 `/` 一起吃進參數，抄路徑時別把它去掉（長度上限由 handler 自行檢查，超過即 422）|
| `POST /api/ask` | RAG 問答：SSE 串流 `status` / `sources` / `token` / `notice` / `ext_sources` / `done` / `followups` / `error`，行內 `[n]` 引用對應來源報告；支援 `conversation_id`、`locale`（`zh-Hant`／`en`，未帶或不認得 fail-open 回中文）與篩選，寫入 `qa_log`。**`ext_sources`（外部網搜來源）在 `done` 之前、`followups`（追問建議）在 `done` 之後**——事件序不止於 `done`。答案本文與外部來源**共用同一條 LLM 串流**，靠 `app/services/stream_sentinel.py` 的 `SentinelStreamParser` 在串流中以哨符（`EXT_SENTINEL`）即時切開：命中之後就不再吐 `token`，來源區塊改由完整原文另行切分。動 `ext_sources` 的格式前先讀該模組——直接改 prompt 會讓哨符對不上，整段外部來源就會當成答案正文串給使用者 |
| `POST /api/ask/stop` | 使用者中斷串流時保存部分答案（`stopped=true`），回 `{qa_id}`。**與串流完成共用前端 `request_id`**（`qa_log.request_id` 上有部分唯一索引），避免網路競態寫出兩筆同一輪問答。帶 `regenerate_of`（重生途中停止）會同交易停用舊版列並接回版本鏈；帶 `edit_of`（編輯重問途中停止）會比照完成路徑截斷被編輯輪之後的輪次——兩者少了會在重整後出現重複輪／舊輪復活 |
| `POST /api/report` | 深度研報生成：SSE 串流 `run`（背景 run handle，**恆為首事件**）→ `status`（retrieving）→ `sources` → `status`（outlining）→ `outline`（章節清單＝進度分母）→ `status`（writing，必要時穿插 searching_web）→ `token`/`section_draft`/`section_skipped` → `status`（verifying）→ `document_revision` → `status`（rendering）→ `done`；任何階段失敗改送 `error`。**`sources` 在 `outline` 之前**——事件序以 `app/services/report.py` 的模組 docstring 與實際 yield 序為準，別照 `web/routers/report.py` handler 的 docstring（該處把這兩者寫反）。**生成跑在背景任務，斷線不中止**。請求 body 另吃 `template_id`（M9b 渲染模板）與 `locale`（M10 輸出語言 `zh-Hant`／`en`）——兩者未帶或不認得都 fail-open 回預設，不會擋生成 |
| `GET /api/report-runs?conversation_id=` | 該對話仍在背景生成的研報；前端載入對話時據此把進度框接回（重整／開新分頁都看得到） |
| `GET /api/report-runs/{run_id}/stream` | 重連背景 run：先重播已發生的事件（不含 token），再接上直播 |
| `POST /api/report-runs/{run_id}/cancel` | 主動中止背景生成 |
| `GET /api/report-templates` | 可選研報渲染模板清單（M9b registry：ib-classic／broker-modern／privatebank-dark），每項回 `id`／`name`／`description`／`is_default`／`thumbnail`。**`thumbnail` 目前三款皆為 `null`**——前端版型選擇器的縮圖是用 CSS 畫出各模板的版面骨架（零圖檔、零請求），不是後端供圖，別去補圖片資產 |
| `POST /api/report-doc/{report_id}/rerender` | 換皮重出（M9b）：用既有 markdown 以另一模板產新 rendition，成功後原子切換 `report_doc.current_rendition_id`——**零 LLM、零重新生成**。body 的 `template_id` 未帶＝維持原模板（不是換成預設）；`locale` 不可指定，一律沿用產出當時的值（否則英文研報會變成「英文內文 ＋ 中文封面／免責」）。渲染兩軌皆炸才回 500，並保留上一個可下載 PDF |
| `GET /api/report-doc/{report_id}/pdf` | 下載研報 PDF：優先服務目前 rendition（換皮重出後），無指標或檔案不在則回退 `report_doc.pdf_path`；仍缺就由 persisted Markdown 即時重建（**沿用產出當時的 `locale`/`template_id`**，否則重建出來的不是同一份東西）|
| `GET /api/history` | 最近的問答歷史（舊單題清單）。`DELETE /api/history/{qa_id}`（或相容 alias `POST /api/history/{qa_id}/delete`）刪除單筆 |
| `GET /api/qa/{root_qa_id}/versions` | 某問題群組的**全部**版本（重生版本鏈：以 `COALESCE(root_qa_id, id)` 分組、由舊到新）。**刻意含已標 `active=false` 的舊版**——歷史 pager 要回看的正是它們；濾 `active` 的是歷史／續問清單，不是這條。DB 出錯 fail-open 回 `[]` |
| `POST /api/qa/{qa_id}/report-offer` | 研報邀請的收合／還原（body `{"action": "decline"\|"restore"}`，寫 `qa_log.filters` 的 additive 鍵 `report_offer_declined`）。邀請本身**不落庫**：`get_conversation` 讀取時以 `report_gate.should_offer_report` 逐列重算（零 LLM、歷史舊列自動涵蓋），重新整理後邀請卡不再消失；「暫時不用」收合成小入口而非刪除，隨時可還原 |
| `GET /api/conversations` | 對話串清單；`GET /api/conversations/{conversation_id}` 取回全部輪次；`DELETE /api/conversations/{conversation_id}`（或相容 alias `POST /api/conversations/{conversation_id}/delete`）刪除整串 |
| `POST /api/feedback` | 記錄使用者對某次回答的讚／倒讚（`qa_id` + `value`，三值列舉 `like`／`dislike`／**`none`**）。`none` ＝再點一次已亮起的那顆＝取消，寫入的是 **SQL NULL** 而非字面值——讀取端（`/api/history`、`/api/qa/{root_qa_id}/versions` 與前端 zod）認的是 `like\|dislike\|null`。取消刻意走第三個列舉值而非「可為 null 的欄位」：後者讓「客戶端漏送欄位」與「明確取消」無法區分 |
| `GET /app`、`GET /app/{spa_path:path}` | SPA shell：所有深連結都回同一份 `frontend/dist/index.html`，交給 client 端路由。SPA 路由為 `/app/search`、`/app/ask`、`/app/radar`、`/app/monitor`、`/app/help`、`/app/report/:file_hash`（basename `/app`）。**`frontend/dist/index.html` 不存在時直接 503**——前端改動要先 `cd frontend && npm run build` |
| `GET /`、`GET /monitor`、`GET /help` | 舊 vanilla 頁已退場，三條都只是 **302 相容導向**（分別到 `/app/search`、`/app/monitor`、`/app/help`），不再自己服務任何頁面 |
| `GET`/`POST /login` | 登入頁與登入提交（共用帳密）|
| `GET /healthz` | **免認證**存活探測：健康 200、DB 不可用 **503**（見 `web/routers/health.py`）|
| `POST /logout` | 清除 session cookie 並導回 `/login` |

> **認證**：deny-by-default 中介層。**免登入的只有 `/login`、`/healthz` 與前綴 `/app/assets/`**（`web/server.py` 的 `_AUTH_ALLOWLIST` / `_AUTH_PREFIX_ALLOWLIST`）。`/healthz` 刻意免認證——登入路徑完全不碰 DB，DB 掛掉時仍能登入，沒有這個豁免就沒有任何探測能分辨。未帶有效 session cookie 時 `/api/*` 回 **401**、其餘導向 **`/login`**；`/static/*` 也受保護。憑證為單一共用帳密（env `REPORT_MARK_ACCESS_USERNAME`/`_PASSWORD`，fail-closed），cookie 以 `REPORT_MARK_SESSION_SECRET` 簽章、7 天滑動到期（**另有 30 天絕對上限**，續期只推遲 `exp` 不重置 `iat`），並對登入失敗做每 IP 限流。token 格式為 `<ver>.<iat>.<exp>.<sig>`，簽章訊息含帳密指紋與 `REPORT_MARK_SESSION_EPOCH` ⇒ **換密碼或 bump epoch 即全員登出**；舊版格式一律拒絕。登入的成功（INFO）／失敗・鎖定・非 HTTPS 遭拒（WARNING）都會寫進 journald，且不記密碼。

前端特性：左側導覽軌（可收合成 mini，手機改底部 tab bar）四個入口——**檢索／問答／觀點／監控**；閱讀頁與說明頁不進導覽列。檢索頁固定有搜尋列 ＋ 市場 chip 列 ＋ 已選條件 chips；**無查詢也無篩選的落地態**顯示 Bento 牆（全語料市場組成色譜、最新一批研報等磚塊），此時工具列不渲染——一旦輸入查詢或套上篩選才換成工具列（排序選單／更多篩選 popover／檢視切換）＋結果區，結果區頂端改以 HitBar 顯示**命中集合**的市場組成色譜。結果檢視只有兩種（`ViewSwitch`：**列表**＝高密度單列、**表格**）；依日期(月)分組只出現在「查看全部／已篩選瀏覽」的完整清單，搜尋結果不分組。同篇研報合併、搜尋時列表顯示命中片段＋關鍵字高亮、即打即查（debounce 350ms，Enter 立即送出）、骨架載入；**點任一筆＝導向閱讀頁 `/app/report/:file_hash`**（乾淨網址，不帶 query），不是內嵌 PDF modal。問答模式：RAG 串流回答＋可點引用來源、處理過程面板、側欄對話歷史（可重看／續問／刪除）、外部參考、追問建議、讚倒讚、複製答案，以及深度研報生成卡片（版型選擇器＋逐節進度＋換皮重出＋PDF 下載）。

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
| 登入 | 共用帳密 env `REPORT_MARK_ACCESS_USERNAME`／`_PASSWORD`（fail-closed）＋簽章金鑰 `REPORT_MARK_SESSION_SECRET`；由 `make serve` 載入 repo 根 `.env`（已 gitignore）。本機 `localhost` 可直連，其他裝置請走 HTTPS 入口。全員登出＝`REPORT_MARK_SESSION_EPOCH` 換值或改密碼後重啟 |

> DB 以 `docker run` 起單一容器（`make db`，含 `--restart unless-stopped`）；對外邊緣層（nginx + cloudflared）則用 `docker compose`（`make up-edge`，見 `deploy/docker-compose.yml`）。背景編排（`resume_corpus.sh`）以 setsid/nohup 方式長跑。

---

## 全量生產現狀

全量語料（`研報自動匯入/`，約 1.5 萬份）已是**現行生產路徑**，非未來規劃：

- `extract_all.py → tag_all_cli.py → ingest_all.py` 為主路徑；`resume_corpus.sh` 提供鎖檔防重入、標註＋導入並行、可中斷續跑的編排。
- `file_hash` 去重與「已標/已導入」檢查讓全流程可隨時中斷續跑。
- **掃描型 PDF 無 OCR**：`extract_all.py` 以可抽文字 < 100 字判 `scanned=true`，導入階段以 `skip_scanned` 計數略過——刻意排除，不提供 OCR。
- 標註/導入失敗各自記 `data/tag_failures.log`、`data/ingest_failures.log` 供事後排查。

**資料現況**（實測 2026-07-17）——UI 要據此**優雅降級**，缺欄是常態不是錯誤：

| 項目 | 覆蓋 |
|------|------|
| 語料規模 | 14,575 篇（近 90 天 549 篇）|
| 檔案類型 | PDF 99.8% |
| `full_text` | 100%（平均 18,220 字、最大 487,187）|
| `summary` | 70% |
| `source` | 97.8% |
| `report_type` | **19.5%** |
| 結構化訊號（`report_signal`）| **0.68%（99 篇）**|

> 例：閱讀頁在訊號缺席時整個觀點區**不進 DOM**——99.3% 的報告都沒有訊號，那是常態；渲染空框或骨架只會讓讀者以為壞了。同理 `report_type` 僅約兩成有值，任何以它為主軸的分組/篩選都要能承受大量 null。

---

## 排錯

| 症狀 | 檢查 |
|------|------|
| 查詢很慢 / 第一次卡住 | BGE-M3 首次下載 ~2-4GB；server 啟動時已暖機，看 uvicorn log |
| 改了程式/前端卻沒生效 | `make serve` 無 `--reload`：`web/static/`（現在只剩 `login.html`）即時生效，但路由/中介層在**啟動時**載入 → 須**重啟** `make serve` 才載入新碼（常見誤判：看到新 UI 卻打到舊路由）。**SPA 改動另需 `cd frontend && npm run build`**——頁面由 `frontend/dist` 服務，不重建看到的永遠是舊版 |
| App 啟動即報錯退出 | 未設 `REPORT_MARK_ACCESS_USERNAME`／`_PASSWORD`（fail-closed）→ 補進 `.env` 再 `make serve` |
| 一直回登入頁 / 登出按 404 | 多半是舊程序還在跑（未重啟，見上）；或 `REPORT_MARK_SESSION_SECRET` 每次重啟變動（請在 `.env` 固定一組）|
| `/api/stats` 連不上 | 容器是否運行 `docker ps`；port 5436 是否被佔 |
| 入庫筆數偏少 | 看 `ingest_all.py` summary 的各類 skip（admin/scanned/untagged/non_research/exists）|
| 標註有缺漏 | 抽查 `data/tags/<hash>.json`；看 `data/tag_failures.log`；`tag_all_cli.py` 可直接重跑（冪等續標）|
| 導入有失敗 | 看 `data/ingest_failures.log`；修正後重跑 `ingest_all.py`（已導入者自動略過）|
| 啟動服務後磁碟吃滿 | 全量導入 I/O 重；離線時改 `make ingest-lowio`，並確認 Defender 已排除 vhdx |
| 市場顯示為代碼非中文 | 前端 `MARKET_META` 是否含該代碼；DB 是否已跑 `align_findb_markets.py` |
| 標註結果不對 | 抽查 `data/tags/<hash>.json`；市場代碼須在 `MARKETS` 內 |
