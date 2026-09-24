# report-mark 運作流程

券商研報從 `研報自動匯入/` 進來，經過抽文字 → Claude 多維標註 → 切塊嵌入 → pgvector 入庫，再由多個離線批次補齊摘要、標題、摘錄、訊號、簡報，最後由 web 服務提供語意檢索、RAG 問答、閱讀頁、觀點雷達與每日簡報。本檔描述每個階段的輸入、輸出與指令，以及 Web API 契約；架構不變量在 `docs/ARCHITECTURE.md`，抽取層細節在 `docs/EXTRACTION.md`。

## 全景流程圖

```
研報自動匯入/ (唯讀鏡像，NAS rsync)
   │
   ▼ ① scripts/extract_all.py             pdfplumber 版面層 / pypdf 回退 / python-docx
data/extracted/<file_hash>.json            per-hash 快取（text、blocks 索引、quality）
   │
   ▼ ② scripts/tag_all_cli.py             claude -p（Haiku）市場 / 商品類型 / 標的
data/tags/<file_hash>.json
   │
   ▼ ③ scripts/ingest_all.py              gate: is_research 且 market 非空
research.research_report ──── research.report_chunk（BGE-M3 dense + content_norm）
research.extraction_log（每個 hash 一列，含未入庫者）
   │
   ├─▶ ④ scripts/extract_takeaways.py     Sonnet → research.report_takeaway（閱讀頁）
   ├─▶ ⑤ scripts/extract_signals.py       Sonnet → research.report_signal（觀點雷達）
   ├─▶ ⑥ scripts/generate_summaries.py    Sonnet → research_report.summary
   ├─▶ ⑦ scripts/generate_titles.py       Sonnet → research_report.title
   └─▶ ⑧ scripts/generate_brief.py        Sonnet → research.report_brief（每日簡報）
   │
   ▼ web/server.py（:8097）
檢索 /api/search ── 問答 /api/ask ── 閱讀 /api/reading ── 雷達 /api/radar ── 簡報 /api/brief
```

生產路徑由 `report-mark-sync.timer` 每 3 小時觸發 `scripts/sync_new_reports.sh`，把上面 ① 到 ⑧ 串成一輪（見「生產同步鏈」）。全語料三支（`extract_all` → `tag_all_cli` → `ingest_all`）只在初次建庫或補歷史時跑。

## 責任分工

| 決定性（Python） | 語意（Claude） |
|---|---|
| 檔案雜湊、檔名解析、抽取、版面分析、品質評分 | 市場與標的標註（Haiku） |
| 切塊、嵌入、入庫、去重、斷點續跑 | 摘要、顯示標題（Sonnet） |
| 混合檢索、tier、選篇、rerank、脈絡組裝 | 問答生成、追問（Sonnet／Haiku） |
| 路由前檢詞表、overview 統計 | 五類路由分類（Haiku） |
| 引文錨定、訊號正規化與狀態判定、共識聚合、窗期 | 摘錄與訊號擷取、簡報撰寫 |
| 忠實度閘門（`is_numeric_claim`）、分數彙總 | 主張拆解與 grounding 評審（Haiku） |

批次以檔案 `file_hash` 為鍵、冪等可續跑；失敗寫 `data/*_failures.log`，不阻斷其他檔。

## 資料層

| 位置 | 內容 | 版控 |
|---|---|---|
| `研報自動匯入/` | 券商 PDF／docx 原檔（約 1.5 萬份），NAS rsync 鏡像，唯讀 | 否 |
| `data/extracted/` | 抽取快取 `<file_hash>.json`（`app/services/extraction/cache.py`） | 否 |
| `data/tags/` | 標註結果 `<file_hash>.json`，resume 依據；監控頁 `scandir` 熱點 | 否 |
| `data/metrics/` | 硬體用量取樣 JSONL | 否 |
| `data/.incidents/` | P5 事件狀態檔 | 否 |
| `data/*.log`、`data/.last_successful_sync`、`data/sync_round_state` | 執行期日誌、心跳、本輪狀態 | 否 |
| `research.*`（Postgres ＋ pgvector，容器 `report-mark-postgres`，host port 5436） | 7 張表，`db/schema.sql` | 是 |

`data/` 在 `.gitignore` 是逐項忽略而非整目錄；新增執行期檔案要同步加一行，因為 repo 禁止 `git add -A`。

深度研報生成（`report_doc`／`report_run`／`report_section`／`report_rendition` 四張表、`data/reports/`、bucket 的 `generated/` 前綴）已於 2026-09 移除。`db/schema.sql` 只 `CREATE IF NOT EXISTS`，對既有庫是 no-op，四張表要由人手動執行 `docker exec -i report-mark-postgres psql -U postgres -d research < db/drop_deep_report_tables.sql`（依相依順序 `DROP TABLE IF EXISTS`；執行前確認 `make db-audit` 全綠，備份從未涵蓋這四張）。在生產庫執行 DROP 之前，`tests/test_schema_constraints.py` 對著生產庫跑會因多出 `report_run`／`report_section` 的兩條 CHECK 而紅，這是預期的，DROP 後自然轉綠，不要為此重生 `db/expected_constraints.txt`。bucket 裡舊的 `generated/` 生成 PDF 不再算 orphan、由人手動清；環境檔裡的 `REPORT_FAITHFULNESS_MIN` 舊名仍可讀（新名 `FAITHFULNESS_MIN`）。

## 逐階段說明

### ① 抽文字：`scripts/extract_all.py`

- 輸入 `研報自動匯入/`，輸出 `data/extracted/<file_hash>.json`。`--workers`（預設 16）。
- 走 `app/services/extract.py` 的 `extract_text(path)`：`EXTRACTOR=pdfplumber` 為版面層主軌，例外或字數低於 100 逐檔退回 pypdf；docx 走 python-docx。品質指標純程式計算，只標記不擋。
- 快取鍵含 `is_admin`、`stock_code`、`company_name`、`source`、`report_date`、`report_type`（檔名解析）與 `extractor`、`extraction_version`、`quality`、`blocks`。
- 細節與診斷紀錄見 `docs/EXTRACTION.md`。

### ② 多維標註：`scripts/tag_all_cli.py`（Claude CLI）

- 輸入 `data/extracted/`，輸出 `data/tags/<file_hash>.json`；已有標籤檔即跳過。`--workers`（預設 8）、`--limit`、`--excerpt`（預設 10000 字）。模型 `claude-haiku-4-5`。
- 標籤 schema（`app/services/tagging.py`）：`market`（findb 代碼）、`is_research`、`confidence`、`instrument_types`、`relates_stock`、`relates_futures`、`stock_targets`（四碼數字，最多 8 個）、`futures_targets`（小詞表）。Python 端 `parse_tags` 正規化：舊中文標籤經 `LEGACY_TO_FINDB` 對照，詞表外的值丟棄。
- 取 `scripts/_claude_lock.py` 的 flock，撞鎖 rc=75。
- 提高 `--workers` 前依 `.env.example` 算式重算 DB 連線數。

### ③ 嵌入入庫：`scripts/ingest_all.py`

- 輸入 `data/extracted/` ＋ `data/tags/`，輸出 `research.research_report` 與 `research.report_chunk`。閘：`is_research` 且 `market` 非空；`is_admin`、`scanned`、`not_research`、`extract_error` 各寫一列 `extraction_log`。`--limit`、`--batch-size`（預設 32）。
- 切塊 `app/services/chunk.py`（600 字元、80 重疊，段落邊界），嵌入 `app/services/embed.py`（BGE-M3，CPU），`store.upsert_report` 依 `file_hash` 先刪後插。
- 收尾 `ANALYZE research.report_chunk`（可能久於 60 秒，走 `relax_statement_timeout`）。
- `make ingest-lowio` 會關 `fsync`，SIGKILL 後不還原；處置 `make restore-durability`。除非使用者明講不要跑。

### ④ 重點摘錄：`scripts/extract_takeaways.py`（Claude CLI，閱讀頁用）

- 輸入 `research_report.full_text`（經 `clean_extracted`，表格區塊用 `cache.strip_tables` 拿掉），輸出 `research.report_takeaway`。每篇 3–5 條 `{claim, quote}`，`quote` 逐字不轉繁、由 `reading/anchor.locate_quote` 錨回正典文字，`claim` 過 `to_traditional`。
- 每份報告單一交易內 DELETE ＋ 全量 INSERT（不是 upsert）；`text_sha256` 對 canonical 全文算，是錨點有效性的守門。
- `--since-days`（預設 90，濾 `report_date`）、`--hashes-file`（生產用）、`--workers`（2）、`--limit`、`--excerpt`（24000）、`--reextract`、`--dry-run`（也取鎖）。模型 `claude-sonnet-5`。

### ⑤ 訊號擷取：`scripts/extract_signals.py`（Claude CLI，觀點雷達用）

- 輸入 `full_text`，輸出 `research.report_signal`（一列＝研報 × 標的）。LLM 依固定 schema 擷取 `rating_raw`、目標價與 evidence、EPS 估計、四維論點（`outlook`、`catalyst`、`risk`、`valuation`）；Python 正規化評等（`buy`／`overweight`／`neutral`／`underweight`／`sell`／`unknown`）、幣別、判 `extraction_status`（`valid`／`partial`／`rejected`）。`thesis_dimensions[*].evidence` 逐字不轉繁。
- 只跑高覆蓋子集：`--min-brokers`（3）、`--min-reports`（5）、`--top-n`（50）；`--workers`（2）、`--limit`、`--excerpt`（16000）、`--reextract`、`--dry-run`。`EXTRACTION_VERSION = "sig-2026-07-15.v1"`。
- 空是常態：雷達 API 對未擷取的研報回 `pending_extraction`。

### ⑥ 摘要：`scripts/generate_summaries.py`（Claude CLI）

- 補 `summary IS NULL` 的研報 2–3 句中文摘要（最多 400 字，過 `to_traditional`）。`--workers`（2）、`--limit`、`--excerpt`（12000）、`--hashes-file`。

### ⑦ 顯示標題：`scripts/generate_titles.py`（Claude CLI）

- 補 `title IS NULL`：`title_source` 三值 `extracted`（原文標題）、`translated`（英文譯中）、`generated`（無標題時生成）；`title_original` 留原文。失敗維持 NULL，前端回退檔名。`--workers`（2）、`--limit`、`--excerpt`（3000）、`--hashes-file`。

### ⑧ 每日簡報：`scripts/generate_brief.py`（Claude CLI，簡報頁用）

- 一天一列 `research.report_brief`；窗期是上一份的 `window_end` 到現在（沒有上一份取 24 小時，上限 `--max-lookback-days` 7），用 `created_at` 界定。素材：窗期新入庫研報（prompt 最多 40 篇）與評等或目標價變動（與該券商前一次比，目標價變動門檻 1%）。
- `--date`、`--after-hour`（9，未到即 no-op）、`--force`、`--dry-run`。當日已有即 no-op 退出 0。鎖只包那一次 CLI 呼叫。來源清單由 Python 記錄。

### claude CLI 批次互斥：`scripts/_claude_lock.py`

會 spawn `claude -p` 的批次（`tag_all_cli`、`sync_new_reports`、`generate_summaries`、`generate_titles`、`extract_takeaways`、`extract_signals`、`generate_brief`）在 main 進入點取 `data/.claude_cli.lock` 的 flock；撞鎖 rc=75 是「不跑」不是「跑壞」，sync 殼不把它計入異常。併發搶 CLI 會讓擷取被大量誤標 `rejected`。`app/services/llm.py` 刻意不在鎖範圍內（`tests/test_claude_lock.py` 釘住）。從 worktree 跑批次不與主 checkout 互斥。

### 編排與離線工具

| 腳本 | 用途 |
|---|---|
| `scripts/resume_corpus.sh` | 全語料續跑：`tag_all_cli --workers 8` 與 `ingest_all` 並行，結束後再 ingest 一次收尾 |
| `scripts/backfill_extraction.py` | 抽取回填（零 LLM，`report-mark-backfill.timer` 01:00）：`extraction_version` 不是目前版本的研報重抽、重切、重嵌、重錨定摘錄；總結列印摘錄錨定率 |
| `scripts/build_boilerplate.py`（`make boilerplate`） | 跨文件樣板段落字典 → `data/boilerplate/<source>.json`，入庫切塊前剔除（`docs/EXTRACTION.md` §12）；字典不在就不剔除 |
| `scripts/lost_anchors_to_delta.py` | 回填後摘錄錨點失效的研報 → hashes 檔，餵 `scripts/extract_takeaways.py --hashes-file … --reextract` |
| `scripts/migrate_extraction_cache.py` | `all.jsonl` → per-hash 快取（一次性） |
| `scripts/backfill_traditional.py`、`scripts/backfill_report_dates.py`、`scripts/backfill_report_sources.py`、`scripts/backfill_full_text.py` | 一次性回填，預設試跑 |
| `scripts/align_findb_markets.py`（`make align`） | 舊中文市場標籤重映射為 findb 代碼，冪等 |
| `scripts/profile_corpus.py`、`scripts/compare_extractors.py`、`scripts/review_extraction_golden.py` | 抽取層唯讀分析與 golden set 覆核 |
| `scripts/select_sample.py`、`scripts/extract_batch.py`、`scripts/make_worklist.py`、`scripts/run_ingest.py` | 抽樣原型路徑（`make prep`、`make ingest`） |
| `scripts/search.py`（`make search`） | CLI 檢索驗證 |
| `scripts/analyze_qa_log.py`、`scripts/measure_baseline.py`、`scripts/eval_faithfulness.py` | 唯讀分析 |

**不要再寫一支「清理 chunk 空白」的批次更新**：`clean_text` 與 `clean_extracted` 都會破壞段落換行，2026-07-29 已連同 `make normalize` 一併移除。

## 生產同步鏈：`scripts/sync_new_reports.sh`

`report-mark-sync.timer` 每 3 小時（`OnCalendar=*-*-* 00/3:00:00`，`Persistent=false` 刻意不補跑）觸發，PID lock `data/.sync_new_reports.lock`。

1. 補記上一輪異常收場；系統關機中或 lock 被佔用退出 0；找不到 `uv` 退出 1。
2. drvfs 唯讀掛載 NAS（`/usr/local/sbin/mount-nas-research`，sudoers 免密碼）；失敗退出 1。
3. `rsync -rt --size-only` 到 `研報自動匯入/`，delta 寫 `data/sync_delta_<時間>.txt`。
4. `scripts/sync_new_reports.py --delta <delta>`（`nice -n 19 ionice -c3`）：逐檔 extract → tag → ingest，每道閘寫 `extraction_log`；失敗記 `data/sync_failures.log`，成功 hash 寫 `data/.sync_last_hashes`，計數寫 `data/.sync_last_stats`。rc=0 不等於成功：`ABNORMAL>0` 時本輪不算完整，補救走 `scripts/failures_to_delta.py --out data/sync_delta_recover.txt` 再餵 `--delta`，**不要 `--all-local`**（那是 O(全部檔)）。匯入 rc 不是 0 也不是 75 時 delta 保留在 `data/`，殼依時間序列出所有保留的 delta 與 `--delta … --hashes-out data/sync_hashes_retained_<時間>.txt` 重放指令（`--hashes-out` 讓每份重放的 hashes 各寫一份，不覆寫 `data/.sync_last_hashes`；只列殼產生的 `sync_delta_<YYYYMMDD>_<HHMMSS>.txt`）。中途中止前已入庫的篇，importer 寫到 `<hashes_out>.partial`，殼改名保留成 `data/sync_hashes_retained_<時間>_partial.txt` 並印三段補跑指令（重放時它們會變 `skip_exists`，不會進重放的 hashes）。
5. 以 `--hashes-file data/.sync_last_hashes` 依序跑摘要、標題、摘錄。**不可改成 `--since-days`**：它濾的是 `report_date`，會漏掉近九成。
6. 訊號 `--limit ${SYNC_SIGNAL_LIMIT:-100}`，排跨全語料積壓，`--limit` 是安全機制不是效能旋鈕（不限量會佔住 CLI 鎖 80 小時以上）。
7. 簡報（無參數；沒有自己的 timer 是刻意的，要讀當輪剛擷取的評等變動）。
8. 標題積壓 `--limit ${SYNC_TITLE_BACKLOG_LIMIT:-60}`，補一年以上舊檔（永遠不會進 `--hashes-file`）。

第 5 到 8 段 best-effort：失敗只記 `data/unit_failures.log`，rc=75 不計入異常；任一段 rc=2（帳號／環境型中止）時，當輪 `data/.sync_last_hashes` 複製保留成 `data/sync_hashes_retained_<時間>.txt`，並印出摘要、標題、摘錄各自的 `--hashes-file` 補跑指令。整批中止的重放步驟見 `docs/production_resilience.md`「整批中止後的重放」，**不能**用 `failures_to_delta.py` 或 `--all-local` 補救（整批中止不留逐篇失敗紀錄）。摘要、標題、摘錄、訊號遇到「LLM 有回應但不能用」的研報會記入 `research.llm_task_failure`：同一 model 下審查擋下或截斷 1 次、其他原因連續 3 輪就不再重打，成功即刪列；`make llm-blocked` 唯讀列出（`--all` 連累計中的也列），要重試就對該批次加 `--retry-blocked` 或 DELETE 那一列；跳過鍵只看 model、不看 prompt 或 `EXTRACTION_VERSION`，**改 prompt 後要加 `--retry-blocked`**（摘錄與訊號的 `--reextract` 隱含它）。第 8 段標題積壓以 `--exclude-hashes-file data/.sync_last_hashes` 排掉本輪 4b 剛打過的新研報，免得同一篇一輪打兩次、失敗記兩次。逾時、CLI 非零退出這類環境型失敗不記。心跳 `data/.last_successful_sync` 只在完整成功時更新，`scripts/check_batch_freshness.py` 據此判管線停跑。環境檔 `/etc/default/report-mark-sync`（範本 `deploy/systemd/report-mark-sync.env.example`）：`REPORT_MARK_ROOT`、`SYNC_PATH_EXTRA`（nvm 沒有 `current` 連結，寫錯會讓 claude 找不到而無聲漏跑）、`EXTRACTOR`、備份與 R2 變數。

## 標籤維度（對齊 findb）

| 維度 | 值（逐字） |
|---|---|
| `market` | `TW`、`US`、`HK`、`CN`、`FX`、`WTX`、`MACRO`、`GLOBAL`、`CRYPTO`（`tests/test_tagging.py` 逐字釘住，是與 findb 的跨 repo 契約） |
| `MARKET_DISPLAY` | 台股、美股、港股、陸股、外匯、台指期、總經、全球、加密 |
| `LEGACY_TO_FINDB` | 台股→TW、美股→US、港股→HK、陸股→CN、外匯→FX、期貨→WTX、總體經濟→MACRO、多元配置→GLOBAL、債券→MACRO、原物料→GLOBAL |
| `instrument_types` | `equity`、`index`、`futures`、`options`、`etf`、`bond`、`fx`、`commodity`、`crypto` |
| `futures_targets` | 台指期、小型台指、電子期、金融期、個股期貨、其他 |
| `stock_targets` | 四碼數字，最多 8 個，去重保序 |
| `source`（券商） | `filename.BROKER_MAP` 由檔名代碼正規化（如 `kgi`、`masterlink`、`sinopac`、`yuanta`、`morgan_stanley`），`SOURCE_DISPLAY` 給顯示名；缺檔名代碼時由 `extract_source_from_text` 從內文補 |
| `report_type` | 檔名解析 |

## Web API 契約

完整端點表在 `README.md`；本節寫串流與契約細節。`tests/test_docs_contract.py` 要求每個 router 路徑逐字出現在 README 或本檔。

### 問答 `POST /api/ask`（SSE）

請求 JSON：`question`（≤2000）、`conversation_id`、`market`、`instrument_type`、`relates_stock`、`relates_futures`、`report_type`、`k`（夾到 1–20）、`regenerate_of`、`edit_of`、`request_id`（冪等鍵，`qa_log.request_id` UNIQUE）、`locale`（`zh-Hant`／`en`）、`web`（每題決定）。

事件序：`queued`（排隊時，`scope`、`position`、`capacity`）→ `status`（`stage`、`thinking_ms`）→ `sources`（`n`、`report_id`、`file_name`、`title`、`market`、`report_date`、`is_latest`）→ `ext_sources`（網搜或受信任資料，`title`、`url`）→ `token`… → `followups` → `done`。婉拒（離題、時效、建議風險）走 `notice` 再 `done{notice_kind}`。傳輸層錯誤 `error{detail}`。心跳每 20 秒一行 SSE 註解。

`POST /api/ask/stop` 把使用者中止時的部分答案與 `stages` 落 `qa_log`（`stopped=true`），回 `qa_id`。

### 契約守門

- `tests/fixtures/sse_events.json` 是後端與前端共吃的單一真相（`tests/test_sse_event_contract.py`、`frontend/src/lib/sseEventContract.test.ts`），現在只剩 `ask` 一組事件。新事件或欄位：fixture 與 `frontend/src/lib/askSchemas.ts` 的 zod（預設 strip，未宣告鍵靜默丟掉；新欄位用 `optional()`）兩處都要動。
- 雷達 `app/services/radar/schemas.py` 的 `Literal` 與 `frontend/src/lib/radarSchemas.ts` 逐字鏡像；閱讀頁 `app/services/reading/schemas.py` 與 `frontend/src/lib/readingSchemas.ts` 同理；`web/routers/brief.py` 的 pydantic 與 `frontend/src/lib/briefSchemas.ts` 同理。
- `/api/progress` 新增鍵要同步改 `frontend/src/features/monitor/progressSchema.ts`。
- 忠實度分數的讀取端（`/api/progress` 的 `evaluation.qa`、`/api/review/queue?kind=faithfulness`、`scripts/eval_faithfulness.py`）只計現行 judge，共用 `app/services/judge_schema.py` 的過濾；`qa_log.evaluation` 帶 `judge_model`、`judge_schema_version`、`degraded_reason`、`elapsed_ms`，缺 `judge_model` 的舊列視為 `claude-haiku-4-5`。

## 私有 R2 遷移順序

`OBJECT_STORAGE_MODE`：`local`（預設，既有行為）、`hybrid`（R2 優先讀、local 回退）、`r2`（只認 object key，缺 key 即 503 不回退）。物件只有研報原檔（`originals/` 前綴）。非 local 缺任一 `R2_*` 啟動即 fail-closed；憑證在 repo 根 `.env` 與 `/etc/default/report-mark-sync` 各一份、逐字相同。

0. `file_path` 若仍指向舊掛載點，先跑 `scripts/repoint_file_paths.py --old-prefix <舊> --mirror-root <ext4 鏡像>`（不帶 `--apply` 只列計畫；只在目標檔存在且大小相同時才改，舊檔 stat 不到用 `--verify-hash`）。
1. 維持 `local`，確認 legacy 路徑可讀。
2. 設好憑證，以 `hybrid` 跑 `scripts/migrate_object_storage.py --dry-run --kind all`，確認計畫再去掉 `--dry-run`。只補缺 key 的 originals（`--kind` 仍收 `originals`／`all`，`all` 等同 originals，讓 `report-mark-r2-reconcile.service` 的命令列不必改），上傳成功後才更新 DB key；不刪除、不重嵌、不重匯入；可重跑。
3. 跑 `scripts/reconcile_object_storage.py --dry-run --kind all` 處理 missing／SHA／orphan，全零才切 `r2`。orphan 掃描只看 `originals/` 前綴；bucket 裡舊的 `generated/` 生成 PDF 不算 orphan，由人手動清。
4. 切換後 `report-mark-r2-reconcile.timer` 每週一 07:00 對帳接告警鏈；`/healthz` 不探 R2，bucket／憑證層級的失效由健康探針經 `/healthz/storage` 偵測（`scripts/check_web_health.sh` 退出碼 6，約 5 分鐘內），個別物件缺漏仍靠對帳。

瀏覽器端兩個前提：bucket 要設 CORS（只開 GET／HEAD），沒設就閱讀頁 PDF 檢視器整頁靜默失敗、伺服器零錯誤；presign 一律帶 `filename`（key 是 hash，跨來源 302 後 `<a download>` 失效）。`--limit` 在兩個工具中都會限制 orphan 判定（沒讀完全部 DB 列就不判 orphan）。

## 完整重跑指令

```bash
# 基礎建設（一次）
make setup                                   # uv sync + pgvector 容器 + 套 schema
cp .env.example .env                         # 填 REPORT_MARK_ACCESS_USERNAME/PASSWORD/SESSION_SECRET

# 全語料三支（初次建庫或補歷史）
uv run python scripts/extract_all.py
uv run python scripts/tag_all_cli.py --workers 8
uv run python scripts/ingest_all.py
#   或一鍵續跑：bash scripts/resume_corpus.sh

# 派生批次（各自冪等，不可併發）
make takeaways
make signals
make summaries
make titles
make brief

# 服務
make build-web                               # 前端改動後
make serve                                   # :8097

# 生產一輪同步（手動）
make sync-once
```

## 排錯

| 症狀 | 看哪裡 |
|---|---|
| 問答無聲中斷、`FileNotFoundError: 'claude'` | web unit 的 PATH drop-in `deploy/systemd/report-mark-web.service.d/path.conf`；`scripts/check_web_health.sh` rc=5 就是這個 |
| 原檔下載／PDF 檢視全數 503，其餘正常 | R2 bucket 或憑證（repo root `.env` 與 `/etc/default/report-mark-sync` 兩份要逐字相同）；`scripts/check_web_health.sh` rc=6 就是這個，細節在 web 日誌的「healthz 物件儲存探測失敗」 |
| 摘要、摘錄無聲漏跑 | `/etc/default/report-mark-sync` 的 `SYNC_PATH_EXTRA` 是否指到實際 node 版本目錄 |
| 批次 rc=75 | 撞 `claude` 鎖，不是錯誤；`make freshness` 判是否停更 |
| 每個查詢 500 但登入正常 | DB 沒起來（假活著），打 `/healthz` 不看 `systemctl is-active` |
| `/api/progress` 回 422 | router 檔輔助函式夾在裝飾器與 handler 之間 |
| SPA 回 503 | `frontend/dist` 不存在，`make build-web` |
| 閱讀頁 PDF 整頁空白、伺服器零錯誤 | R2 bucket CORS |
| 訊號大量 `rejected` | 批次併發搶 CLI，不是資料壞 |
| `make db-audit` 紅 | `fsync=off` 殘留（`make restore-durability`）、孤兒列、`content_norm` 漂移 |
| 評測退出碼 3 | 新指標未在 `scripts/eval_compare.py` 的 `METRIC_SPECS` 補方向 |
| 評測退出碼 2，訊息是「量尺只有 … 有記錄」 | 一邊是記錄量尺之前的舊結果檔（例如 `eval/baselines/baseline-2026-09-02.json`）；兩邊都用同一版 `eval/run_ragas.py` 重跑，不是放寬比較器 |
