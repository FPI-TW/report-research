# report-mark 運作流程

券商研報從 `研報自動匯入/` 進來，經過抽文字 → LLM（`gpt-6-luna`）多維標註 → 切塊嵌入 → pgvector 入庫，再由多個離線批次補齊摘要、標題、摘錄、訊號、簡報，最後由 web 服務提供語意檢索、RAG 問答、閱讀頁、觀點雷達與每日簡報。本檔描述每個階段的輸入、輸出與指令，以及 Web API 契約；架構不變量在 `docs/ARCHITECTURE.md`，抽取層細節在 `docs/EXTRACTION.md`。

## 全景流程圖

```
研報自動匯入/ (唯讀鏡像，NAS rsync)
   │
   ▼ ① scripts/extract_all.py             pdfplumber 版面層 / pypdf 回退 / python-docx
data/extracted/<file_hash>.json            per-hash 快取（text、blocks 索引、quality）
   │
   ▼ ② scripts/tag_all_cli.py             LLM（gpt-6-luna）市場 / 商品類型 / 標的
data/tags/<file_hash>.json
   │
   ▼ ③ scripts/ingest_all.py              gate: is_research 且 market 非空
research.research_report ──── research.report_chunk（BGE-M3 dense + content_norm）
research.extraction_log（每個 hash 一列，含未入庫者）
   │
   ├─▶ ④ scripts/extract_takeaways.py     Flash → research.report_takeaway（閱讀頁）
   ├─▶ ⑤ scripts/extract_signals.py       Flash → research.report_signal（觀點雷達）
   ├─▶ ⑥ scripts/generate_summaries.py    Flash → research_report.summary
   ├─▶ ⑦ scripts/generate_titles.py       Flash → research_report.title
   └─▶ ⑧ scripts/generate_brief.py        Flash → research.report_brief（每日簡報）
   │
   ▼ web/server.py（:8097）
檢索 /api/search ── 問答 /api/ask ── 閱讀 /api/reading ── 雷達 /api/radar ── 簡報 /api/brief
```

部署主機上的增量由 `report-mark-sync.timer` 每 3 小時觸發 `scripts/sync_new_reports.sh`，把上面 ① 到 ⑧ 串成一輪（見「生產同步鏈」）；管理員上傳是第二個入口（見「研報上傳」）。全語料三支（`extract_all` → `tag_all_cli` → `ingest_all`）只在初次建庫或補歷史時跑。

## 責任分工

| 決定性（Python） | 語意（LLM，預設 `gpt-6-luna`） |
|---|---|
| 檔案雜湊、檔名解析、抽取、版面分析、品質評分 | 市場與標的標註 |
| 切塊、嵌入、入庫、去重、斷點續跑 | 摘要、顯示標題 |
| 混合檢索、tier、選篇、rerank、脈絡組裝 | 問答生成、追問 |
| 路由前檢詞表、overview 統計 | 五類路由分類 |
| 引文錨定、訊號正規化與狀態判定、共識聚合、窗期 | 摘錄與訊號擷取、簡報撰寫 |
| 忠實度閘門（`is_numeric_claim`）、分數彙總 | 主張拆解與 grounding 評審（judge 是 `gpt-6-luna`；分數類統計只計現行 judge，其他 judge 量的舊分數另計） |

批次以檔案 `file_hash` 為鍵、可重複執行；失敗寫 `data/*_failures.log`，不阻斷其他檔。能不能從上次進度接續依入口而定（`scripts/extract_all.py` 每次全量重抽，`scripts/ingest_all.py` 跳過已入庫的 hash），以各腳本的 docstring 為準。

## 資料層

| 位置 | 內容 | 版控 |
|---|---|---|
| `研報自動匯入/` | 券商 PDF／docx 原檔（約 1.5 萬份），唯讀鏡像：辦公室主機由 NAS rsync，EC2 由 R2 `inbox/` 拉回 | 否 |
| `data/extracted/` | 抽取快取 `<file_hash>.json`（`app/services/extraction/cache.py`） | 否 |
| `data/tags/` | 標註結果 `<file_hash>.json`，resume 依據；`/api/progress`（管線分頁）的 `scandir` 熱點 | 否 |
| `data/metrics/` | 硬體用量取樣 JSONL | 否 |
| `data/ops_spool/` | 監控 spool：`scripts/collect_resource_usage.py` 寫的主機／容器／服務觀測與批次執行紀錄 JSONL，以及 `scripts/incident_handler.sh`（P5）每次狀態轉換的事件紀錄 `incidents-*.jsonl` 與 journal 片段 `journal/*.log`；`scripts/load_observations.py` 匯入 DB 後刪舊日檔與片段（`OPS_SPOOL_DIR` 可覆寫） | 否 |
| `data/.incidents/` | P5 事件狀態檔 | 否 |
| `data/*.log`、`data/.last_successful_sync`、`data/sync_round_state`、`data/.sync_feed_state` | 執行期日誌、心跳、本輪狀態、來源段結果（掛載／rsync／inbox 推送；主機信標讀它） | 否 |
| `research.*`（Postgres ＋ pgvector；測試環境是容器 `report-mark-postgres`、host port 5436，正式環境是 RDS） | 由 Alembic migration 管理（`db/migrations/`），`db/schema.sql` 是凍結的 baseline | 是 |

`data/` 在 `.gitignore` 是逐項忽略而非整目錄；新增執行期檔案要同步加一行，因為 repo 禁止 `git add -A`。

## 逐階段說明

### ① 抽文字：`scripts/extract_all.py`

- 輸入 `研報自動匯入/`，輸出 `data/extracted/<file_hash>.json`。`--workers`（預設 16）。
- 走 `app/services/extract.py` 的 `extract_text(path)`：`EXTRACTOR=pdfplumber` 為版面層主軌，例外或字數低於 100 逐檔退回 pypdf；docx 走 python-docx。品質指標純程式計算，只標記不擋。
- 快取鍵含 `is_admin`、`stock_code`、`company_name`、`source`、`report_date`、`report_type`（檔名解析）與 `extractor`、`extraction_version`、`quality`、`blocks`。
- 批次不讀 repo 根 `.env`：手動跑要自己帶 `EXTRACTOR=pdfplumber`，否則靜默走程式預設 pypdf。細節見 `docs/EXTRACTION.md`。

### ② 多維標註：`scripts/tag_all_cli.py`（LLM）

- 輸入 `data/extracted/`，輸出 `data/tags/<file_hash>.json`；已有標籤檔即跳過。`--workers`（預設 8）、`--limit`、`--excerpt`（預設 10000 字）。模型 `TAG_MODEL`（未設查表，預設 `gpt-6-luna`）。
- 標籤 schema（`app/services/tagging.py`）：`market`（標準市場代碼）、`is_research`、`confidence`、`instrument_types`、`relates_stock`、`relates_futures`、`stock_targets`（四碼數字，最多 8 個）、`futures_targets`（小詞表）。Python 端 `parse_tags` 正規化：舊中文標籤經 `LEGACY_TO_FINDB` 對照，詞表外的值丟棄。送標註與訊號擷取的內文先經 `app/services/rating_index.py` 剔除封面的近期報告／評等異動索引表。
- 取 `scripts/_claude_lock.py` 的 flock，撞鎖 rc=75。
- 提高 `--workers` 前依 `.env.example` 算式重算 DB 連線數。

### ③ 嵌入入庫：`scripts/ingest_all.py`

- 輸入 `data/extracted/` ＋ `data/tags/`，輸出 `research.research_report` 與 `research.report_chunk`。閘：`is_research` 且 `market` 非空；`is_admin`、`scanned`、`not_research`、`extract_error` 各寫一列 `extraction_log`。`--limit`、`--batch-size`（預設 32）。
- 切塊 `app/services/chunk.py`（600 字元、80 重疊，段落邊界），嵌入 `app/services/embed.py`（BGE-M3，CPU），`store.upsert_report` 依 `file_hash` 先刪後插。
- 收尾 `ANALYZE research.report_chunk`（可能久於 60 秒，走 `relax_statement_timeout`）。
- `make ingest-lowio` 會關 `fsync`，SIGKILL 後不還原；處置 `make restore-durability`。除非使用者明講不要跑（走 `docker exec`，RDS 不適用）。

### ④ 重點摘錄：`scripts/extract_takeaways.py`（LLM，閱讀頁用）

- 輸入 `research_report.full_text`（經 `clean_extracted`，表格區塊用 `cache.strip_tables` 拿掉），輸出 `research.report_takeaway`。每篇 3–5 條 `{claim, quote}`，`quote` 逐字不轉繁、由 `reading/anchor.locate_quote` 錨回正典文字，`claim` 過 `to_traditional`。
- 每份報告單一交易內 DELETE ＋ 全量 INSERT（不是 upsert）；`text_sha256` 對 canonical 全文算，是錨點有效性的守門。
- `--since-days`（預設 90，濾 `report_date`）、`--hashes-file`（sync 鏈用）、`--workers`（2）、`--limit`、`--excerpt`（24000）、`--reextract`、`--retry-blocked`、`--dry-run`（也取鎖）。模型 `TAKEAWAY_MODEL`（預設 `gpt-6-luna`；要逐字引文，換模型前先量「任一方式錨定成功率」）。

### ⑤ 訊號擷取：`scripts/extract_signals.py`（LLM，觀點雷達用）

- 輸入 `full_text`，輸出 `research.report_signal`（一列＝研報 × 標的）。LLM 依固定 schema 擷取 `rating_raw`、目標價與 evidence、EPS 估計、四維論點（`outlook`、`catalyst`、`risk`、`valuation`）；Python 正規化評等（`buy`／`overweight`／`neutral`／`underweight`／`sell`／`unknown`）、幣別、判 `extraction_status`（`valid`／`partial`／`rejected`）。`thesis_dimensions[*].evidence` 逐字不轉繁。
- 只跑高覆蓋子集：`--min-brokers`（3）、`--min-reports`（5）、`--top-n`（50）；`--workers`（2）、`--limit`、`--excerpt`（16000）、`--reextract`、`--retry-blocked`、`--dry-run`。`EXTRACTION_VERSION = "sig-2026-11-04.v2"`；`REUSABLE_EXTRACTION_VERSIONS` 裡的舊版不算待擷取，bump 不會重打全語料，要重跑加 `--reextract`。
- 目標價閘門（`app/services/signal_extract.py`）：數字等於內文括號標記的現價（`(2308 TT，NT$1,355，增加持股)`）、讀到凱基策略表的現價欄、或與同券商同標的 ±45 天目標價中位數差 3 倍以上，就清空目標價並標 `partial`（原始值留在 `raw_payload`）；只清不補。既有列用 `scripts/repair_signal_values.py` 套同一套規則與評等表（零 LLM）。
- 空是常態：雷達 API 對未擷取的研報回 `pending_extraction`。

### ⑥ 摘要：`scripts/generate_summaries.py`（LLM）

- 補 `summary IS NULL` 的研報 2–3 句中文摘要（最多 400 字，過 `to_traditional`）。`--workers`（2）、`--limit`、`--excerpt`（12000）、`--hashes-file`。

### ⑦ 顯示標題：`scripts/generate_titles.py`（LLM）

- 補 `title IS NULL`：`title_source` 三值 `extracted`（原文標題）、`translated`（英文譯中）、`generated`（無標題時生成）；`title_original` 留原文。失敗維持 NULL，前端回退檔名。`--workers`（2）、`--limit`、`--excerpt`（3000）、`--hashes-file`。

### ⑧ 每日簡報：`scripts/generate_brief.py`（LLM，簡報頁用）

- 一天一列 `research.report_brief`；窗期是上一份的 `window_end` 到現在（沒有上一份取 24 小時，上限 `--max-lookback-days` 7），用 `created_at` 界定；`--force` 重寫已有的列時沿用該列存的窗期；補過去的日子時，終點是目標日結束（本地時間）與下一份的 `window_start` 取早的，窗期長度為零就不寫列。素材：窗期新入庫研報（prompt 最多 40 篇）與評等或目標價變動（與該券商前一次比，目標價變動門檻 1%）。
- `--date`、`--after-hour`（9，未到即 no-op）、`--force`、`--dry-run`。當日已有即 no-op 退出 0。鎖只包那一次 LLM 呼叫。來源清單由 Python 記錄。

### LLM 批次互斥：`scripts/_claude_lock.py`

呼叫 LLM 的批次（`tag_all_cli`、`sync_new_reports`、`generate_summaries`、`generate_titles`、`extract_takeaways`、`extract_signals`、`generate_brief`；權威清單是 `tests/test_claude_lock.py` 的 `LOCKED_SCRIPTS`）取 `data/.claude_cli.lock` 的 flock（檔名與「claude 鎖」的叫法是刻意沿用的舊名，鎖的是所有 LLM 批次）；除 `generate_brief` 只包那一次 LLM 呼叫外，都在 main 進入點取鎖。撞鎖 rc=75 是「不跑」不是「跑壞」，sync 殼不把它計入異常。互斥防的是重複計費、摘錄 DELETE+INSERT 互撞與 DB 連線數。`app/services/llm.py` 刻意不在鎖範圍內（`tests/test_claude_lock.py` 釘住）。從 worktree 跑批次不與主 checkout 互斥。

### 編排與離線工具

| 腳本 | 用途 |
|---|---|
| `scripts/resume_corpus.sh` | 全語料續跑：`tag_all_cli --workers 8` 與 `ingest_all` 並行，結束後再 ingest 一次收尾 |
| `scripts/backfill_extraction.py` | 抽取回填（零 LLM，`report-mark-backfill.timer` 01:00）：`extraction_version` 不是目前版本的研報重抽、重切、重嵌、重錨定摘錄；總結列印摘錄錨定率（`docs/EXTRACTION.md` §9） |
| `scripts/build_boilerplate.py`（`make boilerplate`） | 跨文件樣板段落字典 → `data/boilerplate/<source>.json`，入庫切塊前剔除（`docs/EXTRACTION.md` §12）；字典不在就不剔除 |
| `scripts/lost_anchors_to_delta.py` | 回填後摘錄錨點失效的研報 → hashes 檔，餵 `scripts/extract_takeaways.py --hashes-file … --reextract` |
| `scripts/backfill_traditional.py`、`scripts/backfill_report_dates.py`、`scripts/backfill_report_sources.py`、`scripts/backfill_full_text.py` | 一次性回填，預設試跑 |
| `scripts/align_findb_markets.py`（`make align`） | 舊中文市場標籤重映射為標準市場代碼（廷豐 profile 與 findb 對齊），冪等 |
| `scripts/profile_corpus.py`、`scripts/compare_extractors.py`、`scripts/review_extraction_golden.py` | 抽取層唯讀分析與 golden set 覆核 |
| `scripts/select_sample.py`、`scripts/extract_batch.py`、`scripts/make_worklist.py`、`scripts/run_ingest.py` | 抽樣原型路徑（`make prep`、`make ingest`） |
| `scripts/search.py`（`make search`） | CLI 檢索驗證 |
| `scripts/analyze_qa_log.py`、`scripts/measure_baseline.py`、`scripts/eval_faithfulness.py` | 唯讀分析 |

**不要再寫一支「清理 chunk 空白」的批次更新**：`clean_text` 與 `clean_extracted` 都會破壞段落換行，`make normalize` 就是因此移除的。`report_chunk.content` 要變，只能走回填路徑重切重嵌（`scripts/backfill_extraction.py`）。

### 問答對抗集：`eval/qa_adversarial.py`

`eval/qa_adversarial_cases.json` 固定五種合成研報情境：片段內惡意指令、偽造來源編號、跨日期衝突、數值單位及沒有證據的問題。每題自帶預期事實、禁語與局部引用規則，不讀取真實研報或資料庫。離線答案檔格式為 `{"answers":{"prompt_injection":"...", ...}}`，須包含所有題號；缺題或多題直接拒絕。

```bash
uv run python eval/qa_adversarial.py --answers /tmp/qa-answers.json --out /tmp/qa-adversarial-offline.json
uv run python eval/qa_adversarial.py --live --out /tmp/qa-adversarial-live.json
```

離線模式只檢查輸入答案，供假 LLM 或人工候選答案重播；`--live` 會預檢生成模型金鑰，使用與問答主答相同的 `SYSTEM_PROMPT`、`build_user_prompt`、`stream_completion` 和 token 上限，逐題把固定片段送給模型。正式問答會在串流與落庫前，把不存在的數字引用換成「無效引用」標記；`--live` 同樣處理後再評分，`summary` 代表使用者可見答案，`raw_summary`、逐題 `raw_answer` 與 `raw_failures` 保留模型原始輸出的缺陷。它刻意隔離檢索、路由、證據帳本與資料庫，所以結果只代表「已提供此片段時的生成行為」。沒有金鑰時預檢以 rc=2 結束，不產生正式模型結果。逐題答案與失敗原因、題集和離線答案檔的 SHA256 存在結果 JSON；所有規則通過為 rc=0，有失敗為 rc=1。檢查器能抓到指定錯誤數值、洩漏誘導字串、虛構來源及事實旁缺正確引用；正規表示式無法完整判斷語意、否定句或其他未列出的幻覺，正式結果仍須人工複核。這套分數與 `run_ragas` 的 F／CP／AR 量尺不同，勿送進 `eval-compare` 比較。

## 生產同步鏈：`scripts/sync_new_reports.sh`

兩個環境各跑一份，`report-mark-sync.timer` 每 3 小時（`OnCalendar=*-*-* 00/3:00:00`，`Persistent=false` 刻意不補跑）觸發，PID lock `data/.sync_new_reports.lock`。來源段不同：辦公室主機（測試環境）`SYNC_SOURCE=nas`（預設）從 NAS rsync，設了 `SYNC_INBOX_PUSH=1` 所以再把新檔推到 R2 `inbox/`；EC2（正式環境）`SYNC_SOURCE=r2-inbox` 從 `inbox/` 拉；第 4 段起相同。

1. 補記上一輪異常收場；系統關機中或 lock 被佔用退出 0；找不到 `uv` 退出 1。
2. drvfs 唯讀掛載 NAS 上的研報資料夾 `投資研究處\02.研究資源\研報自動匯入`（`/usr/local/sbin/mount-nas-research`，sudoers 免密碼；share 根目錄的 ACL 讀不到，所以只掛子資料夾）；失敗、或掛到的是整個 share（掛載點底下有 `02.研究資源`）時退出 1。
3. `rsync -rt --size-only` 到 `研報自動匯入/`，delta 寫 `data/sync_delta_<時間>.txt`。`SYNC_INBOX_PUSH=1` 時接著以 `scripts/r2_inbox.py push` 把本輪新檔連同相對路徑與 mtime 推到 R2 的 `inbox/`（best-effort，失敗不擋匯入）。推送失敗會保留 `data/inbox_delta_retained_<時間>.txt`，log 與告警紀錄提供 `push --delta` 補傳指令；補傳成功後刪掉該份 delta。
   第 2、3 段的結論每輪寫進 `data/.sync_feed_state`（`result`＝`ok`／`mount_failed`／`rsync_failed`，`push`＝`off`／`none`／`ok`／`failed`；掛載或 rsync 失敗而中止的那一輪也寫，PID lock 早退不寫）。`report-mark-beacon.timer` 每 10 分鐘以 `scripts/publish_host_beacon.py` 把它連同服務狀態發布到 R2 的 `beacons/<HOST_BEACON_NAME>.json`，收檔那一端（EC2）的 `/healthz/feed` 讀來判斷「來源主機失聯／來源段壞了」並告警——那一端自己看不出 inbox 沒有新檔是因為沒有新稿還是來源斷了（`app/services/host_beacon.py`；處置見 `docs/production_resilience.md`「來源主機信標」）。
   碰不到 NAS 的部署（EC2，正式環境）設 `SYNC_SOURCE=r2-inbox`：第 2、3 段改成 `scripts/r2_inbox.py pull`，把本地鏡像沒有或大小不同的 inbox 物件拉回 `研報自動匯入/`（保留 mtime）並寫同格式的 delta，第 4 段起完全相同。部分失敗仍匯入已落地的檔，失敗檔下一輪重拉；拉取／推送有失敗時均記告警並抑制完整成功心跳。`originals/` 不能拿來代替 inbox：那裡只有 SHA256 與 sha256 metadata，沒有檔名與 mtime。
4. `scripts/sync_new_reports.py --delta <delta>`（`nice -n 19 ionice -c3`）：逐檔 extract → tag → ingest（單篇流程在 `scripts/_ingest_core.py` 的 `ingest_one`，sync 依它回的結果計數、寫失敗紀錄與跳過名單），每道閘寫 `extraction_log`；失敗記 `data/sync_failures.log`，成功 hash 寫 `data/.sync_last_hashes`，計數寫 `data/.sync_last_stats`。rc=0 不等於成功：`ABNORMAL>0` 時本輪不算完整，補救走 `scripts/failures_to_delta.py --out data/sync_delta_recover.txt` 再餵 `--delta`，**不要 `--all-local`**（那是 O(全部檔)）。匯入 rc 不是 0 也不是 75 時 delta 保留在 `data/`，殼依時間序列出所有保留的 delta 與 `--delta … --hashes-out data/sync_hashes_retained_<時間>.txt` 重放指令（`--hashes-out` 讓每份重放的 hashes 各寫一份，不覆寫 `data/.sync_last_hashes`；只列殼產生的 `sync_delta_<YYYYMMDD>_<HHMMSS>.txt`）。中途中止前已入庫的篇，importer 寫到 `<hashes_out>.partial`，殼改名保留成 `data/sync_hashes_retained_<時間>_partial.txt` 並印三段補跑指令（重放時它們會變 `skip_exists`，不會進重放的 hashes）。
5. 以 `--hashes-file data/.sync_last_hashes` 依序跑摘要、標題、摘錄。**不可改成 `--since-days`**：它濾的是 `report_date`，會漏掉大半新入庫的研報。
6. 訊號 `--limit ${SYNC_SIGNAL_LIMIT:-100}`，排跨全語料積壓，`--limit` 是安全機制不是效能旋鈕（不限量會長時間佔住批次鎖，擋住之後每一輪 sync）。
7. 簡報（無參數；沒有自己的 timer 是刻意的，要讀當輪剛擷取的評等變動）。
8. 標題積壓 `--limit ${SYNC_TITLE_BACKLOG_LIMIT:-60}`，補一年以上舊檔（永遠不會進 `--hashes-file`）。

第 5 到 8 段 best-effort：失敗只記 `data/unit_failures.log`，rc=75 不計入異常；任一段 rc=2（帳號／環境型中止）時，當輪 `data/.sync_last_hashes` 複製保留成 `data/sync_hashes_retained_<時間>.txt`，並印出摘要、標題、摘錄各自的 `--hashes-file` 補跑指令。整批中止的重放步驟見 `docs/production_resilience.md`「整批中止後的重放」，**不能**用 `failures_to_delta.py` 或 `--all-local` 補救（整批中止不留逐篇失敗紀錄）。摘要、標題、摘錄、訊號遇到「LLM 有回應但不能用」的研報會記入 `research.llm_task_failure`：同一 model 下審查擋下或截斷 1 次、其他原因連續 3 輪就不再重打，成功即刪列；`make llm-blocked` 唯讀列出（`--all` 連累計中的也列），要重試就對該批次加 `--retry-blocked` 或 DELETE 那一列；跳過鍵只看 model、不看 prompt 或 `EXTRACTION_VERSION`，**改 prompt 後要加 `--retry-blocked`**（摘錄與訊號的 `--reextract` 隱含它）。第 8 段標題積壓以 `--exclude-hashes-file data/.sync_last_hashes` 排掉本輪第 5 段剛打過的新研報，免得同一篇一輪打兩次、失敗記兩次。逾時這類環境型失敗不記。審查擋下、截斷、空回應、400、已吐字後逾時（`timeout_streamed`，期限型截斷：連續 3 輪才跳過、計入斷路器、可重放）也照原因記入（`API[...]` 錯誤不在腳本層重試）；行內標註被審查擋下的研報不入庫、計 `skip_blocked`（異常）、被 `max_tokens` 截斷的計 `skip_truncated`（異常），兩者 `failures_to_delta.py` 預設都不撈（期限型截斷記 `skip_untagged`、會撈），由人處置（`docs/production_resilience.md`「LLM 批次的失敗處置」）。批次斷路器的標記綁定 sync 輪次（殼每輪 export `SYNC_ROUND_ID`）：只擋同一輪後面呼叫 LLM 的段。心跳 `data/.last_successful_sync` 只在完整成功時更新，`scripts/check_batch_freshness.py` 據此判管線停跑。環境檔 `/etc/default/report-mark-sync`（範本 `deploy/systemd/report-mark-sync.env.example`）：`REPORT_MARK_ROOT`、`SYNC_PATH_EXTRA`（讓 unit 找得到 `uv`）、`EXTRACTOR`、備份與 R2 變數、`SYNC_SOURCE`／`SYNC_INBOX_PUSH`；DB 不在本機時另設 `REPORT_MARK_DB_URL` 與 `PGSSLROOTCERT`（批次不讀 repo 根 `.env`，漏設會靜默連回 `localhost:5436`）。

## 資料來源連接器（框架的導入路徑）：`scripts/ingest_connectors.py`

給其他券商「快速導入」：在 profile 的 `[connectors]` 接上自己的研報來源，之後的去重、抽取、標註、入庫與下游批次全部共用。連接器只負責**把新檔落地到本機資料夾、附上 metadata 側檔**（`app/services/connectors/`）；之後逐篇交給 `scripts/_ingest_core.py` 的 `ingest_one`（與 sync、上傳 worker 同一份），以 `file_hash` 去重。廷豐的來源仍由上面的 sync 殼執行，廷豐 profile 只宣告對應（`enabled = []`），sync 鏈第 4 段起不變。

| 類型 | 從哪裡來 | 落地 | 非祕密參數（`[connectors.sources.<名稱>]`） | 祕密 |
|---|---|---|---|---|
| `folder` | 本機資料夾（券商自己的 SMB／NFS 由主機或 compose volume 掛上，框架不做 mount） | 原地（`path`） | `path` | — |
| `s3` | S3 相容 bucket 的 prefix（R2、MinIO、AWS S3）；廷豐的 R2 inbox 是它的一個實例（`scripts/r2_inbox.py pull` 共用 `app/services/connectors/s3.py` 的 `pull`） | `landing`（預設 `data/connectors/<名稱>/`）；只拉研報檔、同名 JSON 與根目錄的 `_metadata.csv`，本地沒有或大小不同才拉，保留物件 metadata 的 mtime | `prefix`（以 `/` 結尾）、`env`（環境變數前綴）、`landing` | `<env>_BUCKET`、`<env>_ACCESS_KEY_ID`、`<env>_SECRET_ACCESS_KEY`，選填 `<env>_ENDPOINT`、`<env>_REGION`（預設 `auto`） |
| `plugin` | profile 的 `plugins/<plugin>.py`（只載入 profile 以名稱明確引用的那一個檔，不掃描目錄） | `landing`；plugin 的 `fetch(landing, params) -> FetchReport` 把新檔與側檔寫進去 | `plugin`、`landing`、`[...params]`（字串、數字、布林） | plugin 自己讀環境變數 |

共用參數：`kind`、`poll_minutes`（兩次輪詢的最短間隔，預設 180、5..10080）、`settle_seconds`（mtime 距今未滿就先不收，預設 60）。`[connectors].enabled` 是要輪詢的連接器（順序＝輪詢順序），空＝不輪詢。名稱、類型、必填參數、`prefix` 形狀、plugin 檔存在與否都在啟動時嚴格驗證（`app/profile.py`）；s3 缺環境變數、plugin 載入失敗（語法錯、import 錯、缺 `fetch`）在那一輪報成 `connector_error`，該連接器不跑，其他照跑。

```toml
[connectors]
enabled = ["inbox", "drop"]

[connectors.sources.inbox]
kind = "s3"
prefix = "research/"
env = "CONNECTOR_INBOX"      # CONNECTOR_INBOX_BUCKET、CONNECTOR_INBOX_ACCESS_KEY_ID…
poll_minutes = 60

[connectors.sources.drop]
kind = "folder"
path = "/srv/research-inbox"  # 主機或 compose 掛上的目錄；web 也要在同一個路徑看得到
```

**一輪**（`scripts/ingest_connectors.py`，LLM 入口：取批次鎖、缺金鑰在取鎖前就 rc=2）：每個到期的連接器 `fetch()` → `base.scan()` 列出落地資料夾裡的研報檔（`.pdf`／`.docx`／`.doc`，略過 `.` 開頭與 `.part`／`.tmp`）並配上側檔、算指紋（大小、mtime、側檔內容）→ 處理紀錄裡指紋相同的略過 → 其餘逐篇 `ingest_one(metadata=…, connector=<名稱>)`。入庫後 `research_report.file_path` 指向落地的檔案，所以落地資料夾要長期保留（`OBJECT_STORAGE_MODE=local` 時 web 從那裡交原檔）。

**重跑不重複入庫**：處理紀錄 `<CONNECTOR_DATA_DIR>/state/<名稱>.json`（`CONNECTOR_DATA_DIR` 預設 `data/connectors/`）只記「重跑不會不一樣」的結果——`ingested`、`skip_exists`、`skip_admin`、`skip_scanned`、`skip_non_research`、`skip_blocked`、`skip_truncated` 與側檔不合格；`fail`、`skip_untagged` 不記，下一輪重試。檔案或側檔改了指紋就變，會重送一次。紀錄遺失時最壞是重抽一次字、全部 `skip_exists`（`ingest_one` 以 file_hash 判斷已入庫），不會重複入庫；紀錄壞掉（不是合法 JSON）不會被當成空的，該連接器停下、記一筆 `connector_error` 等人處理。本輪入庫的 hashes 寫到 `<CONNECTOR_DATA_DIR>/last_hashes`（或 `--hashes-out`），計數寫 `last_stats`（`abnormal`＝`fail`＋`skip_untagged`＋`skip_blocked`＋`skip_truncated`＋`sidecar_invalid`＋`fetch_fail`＋`connector_error`），失敗清單 `failures.log`（`連接器<TAB>相對路徑<TAB>階段<TAB>原因`）；中途整批中止時已入庫的篇寫到 `<hashes_out>.partial`（同 sync）。

**排程**（`scripts/connector_sync.py`，容器 scheduler 的 `connectors` 工作每 15 分鐘叫一次，見 `docs/CONTAINER_DEPLOYMENT.md`）：沒有到期的連接器就整輪不跑；到期時跑 `ingest_connectors.py`，再照抄 sync 鏈第 5 到 8 段——`generate_summaries`／`generate_titles`／`extract_takeaways` 帶 `--hashes-file`（本輪有新研報才跑）、`extract_signals --limit ${SYNC_SIGNAL_LIMIT:-100}`、`generate_brief`、`generate_titles --limit ${SYNC_TITLE_BACKLOG_LIMIT:-60}`（`tests/test_connector_sync.py` 對帳殼裡的步驟並釘住 `--limit` 永遠在）。下游 best-effort；匯入撞鎖（rc=75）時本輪結束，落地的檔案沒記進處理紀錄、下一輪自然再送；下游 rc=2 時本輪 hashes 保留成 `hashes_retained_<時間>.txt` 並印補跑指令。

### metadata 側檔

每份研報可附同名 JSON（例如 20261020_報告.pdf 旁邊放 20261020_報告.json），或在落地資料夾根目錄放一份 `_metadata.csv`（第一欄 `file` 是相對路徑，其餘欄名同下表，陣列以 `;` 分隔，空格子＝沒提供）。所有欄位選填、未知欄位一律拒收：

| 欄位 | 格式 | 寫到哪裡 | 與 LLM 產出的關係 |
|---|---|---|---|
| `title` | 字串 ≤200 | `research_report.title`（`title_source='metadata'`） | 標題批次只挑 `title IS NULL`，不再為它呼叫 LLM |
| `report_date` | `YYYY-MM-DD` | `report_date` | 優先於檔名與 mtime |
| `source` | 發行機構代碼（小寫英數、`_`、`-`） | `source` | 優先於內文指紋與檔名 |
| `market` | 標準市場代碼（`TW US HK CN FX WTX MACRO GLOBAL CRYPTO`） | `market` | 覆寫標註；給了就視為研究報告 |
| `stock_targets` | 字串陣列（可空） | `stock_targets`、`relates_stock` | 覆寫標註；與 `market` 都給了就**不呼叫標註 LLM** |
| `instrument_types` | `equity index futures options etf bond fx commodity crypto` 的子集 | `instrument_types` | 覆寫標註 |
| `futures_targets` | `台指期 小型台指 電子期 金融期 個股期貨 其他` 的子集 | `futures_targets`、`relates_futures` | 覆寫標註 |
| `analysts` | 字串陣列 | 只存 `research.report_metadata` | — |
| `rating` | 字串（原文） | 只存 `research.report_metadata` | **不覆寫 `report_signal`** |
| `target_price`、`target_currency` | 正數；三碼大寫幣別（要先有 `target_price`） | 只存 `research.report_metadata` | **不覆寫 `report_signal`** |

```json
{"title": "台積電：先進封裝上修", "report_date": "2026-11-01", "source": "acme", "market": "TW",
 "stock_targets": ["2330"], "analysts": ["王小明"], "rating": "買進", "target_price": 1350, "target_currency": "TWD"}
```

- **嚴格驗證**（`app/services/connectors/sidecar.py`）：JSON 語法錯、型別錯、市場不是標準代碼、日期錯、同名研報檔不只一份（a.pdf 與 a.docx 共用 a.json）、同一份檔同時有 JSON 與 CSV 列、CSV 清單任一列不合格（整份不採信）——一律不入庫，寫進失敗清單並計成 `sidecar_invalid`（異常）；不退回「當作沒有側檔」。改好側檔後指紋改變，下一輪自動重送。
- **保存**：驗證後的欄位（只含有給的鍵）在 `upsert_report` 之前、同一筆交易寫進 `research.report_metadata`（以 `file_hash` 為鍵、刻意無 FK，同 `report_visibility`；不可重建、在備份清單內）。側檔是人工提供的文字，**原樣保存，不經 `zh_hant.to_traditional()`**。
- **訊號**：摘要、摘錄、`report_signal` 照常由 LLM 產出。雷達的評等與目標價需要逐字證據與錨定（`target_price_evidence`、`thesis_dimensions[*].evidence`），側檔的 `rating`／`target_price` 沒有證據，所以刻意不寫進 `report_signal`，只保存供查證；`extract_signals` 的 `--limit` 照舊。
- **只在第一次入庫時套用**：已在庫的研報（`skip_exists`）不因側檔改變而改寫。全語料重建（`scripts/ingest_all.py`）不讀側檔（標註快取 `data/tags/` 只放 LLM 的結果）。

## 研報上傳：收檔、worker 與審核（`UPLOAD_ENABLED` 預設關閉）

管理員從管理後台上傳 PDF 是 NAS 同步之外的第二個入口，分三段：**收檔**（web）→ **上傳 worker**（掃毒、入庫成草稿、清除）→ **審核 API**（發布、退回、重試）。`UPLOAD_ENABLED` 預設 0（`POST /api/admin/uploads` 回 503 `uploads_disabled`），兩個環境目前都沒開。開之前該主機要先裝好 worker 的 unit（`report-mark-upload.service`／`.timer`）與 ClamAV 容器並走完上線步驟（`docs/production_resilience.md`「上傳 worker」；正式環境兩者都沒裝，各主機現況見 `docs/DEPLOYMENT_STATUS.md`）。`UPLOAD_ENABLED` 是功能旗標 `uploads.intake` 的上限：開了之後管理員還能在「功能旗標」頁以覆寫暫停收檔（同樣回 503 `uploads_disabled`）。

`POST /api/admin/uploads?filename=&last_modified=`（`web/routers/admin_uploads.py`，管理員＋`reports.manage`）：

1. 請求是 raw body（`Content-Type: application/pdf`），不是 multipart：一次串流裡同時限長、算 hash、寫進隔離區，不另外暫存。`filename` 是原始檔名，`last_modified` 是瀏覽器 `File.lastModified`（毫秒），存成 `client_mtime`（之後 worker 用它當 `report_date` 的回退）。
2. 依序擋：旗標（503）→ Content-Type（415）→ 檔名清理（`app/services/upload_intake.py` 的 `sanitize_filename`：去路徑成分、控制與格式字元，NFC，強制小寫 `.pdf`，≤255 字元；副檔名不對 415、清完沒主檔名 400 `invalid_filename`）→ `Content-Length` 超過 `UPLOAD_MAX_BYTES`（413）→ 隔離區可用且剩餘空間扣掉這次大小後不低於 `UPLOAD_MIN_FREE_MB`（503 `quarantine_unavailable`）→ 配額快查（429）。
3. 落地（`app/services/quarantine.py`）：以 `O_EXCL`／`O_NOFOLLOW` 寫 `<隔離區>/incoming/<upload_id>.part`（目錄 0700、檔案 0600），邊寫邊算 SHA-256、邊計長，超過上限中止並刪 `.part`（413）；前 1024 bytes 要有 `%PDF-1.`／`%PDF-2.`、最後 1024 bytes 要有 `%%EOF`（415）；fsync 後 rename 成 `<隔離區>/<upload_id>.bin`。檔名只有 upload_id，不用使用者檔名、不帶 `.pdf`。隔離區預設 `data/quarantine/`（`UPLOAD_QUARANTINE_DIR`）。**web 不解析 PDF 內容、不連 clamd**：主動內容與加密的檢查、掃毒都是 worker 的事。
4. 同一筆交易（`upload_intake.create_upload`，先取 `pg_advisory_xact_lock` 讓配額精確）：語料已有同 hash → 409 `upload_duplicate`（帶 `file_hash` 與它是 `hidden`／`draft`／`published`）；已有進行中的上傳 → 409 `upload_duplicate`（`existing: upload`；並發時靠 partial unique index `idx_report_upload_active_hash` 擋，撞 index 也轉成 409）；曾判感染 → 422 `upload_known_infected`；每人每日（台北時間日曆日，`UPLOAD_DAILY_QUOTA`）或全站處理中（quarantined／scanning／clean／processing，`UPLOAD_MAX_IN_FLIGHT`）超過 → 429 `upload_quota_exceeded`；INSERT `report_upload`（state `quarantined`）並寫稽核 `upload.create`（detail 只有 upload_id、檔名、大小、狀態）。任何一步失敗就 rollback 並刪掉 `.bin`；成功回 202 與上傳紀錄。

`GET /api/admin/uploads?state=&limit=&offset=` 列上傳紀錄，附 `scanner` 摘要（待掃件數、掃描中件數、最舊的等待、等待中的列最近一次 `scan_last_error`），全部由 DB 推導，web 不連 clamd。`GET /api/admin/uploads/{upload_id}` 是單筆詳情，另帶語料裡同 hash 的研報與抽取品質（還沒入庫時 null）。

辦公室主機對外經容器 nginx 時走 `deploy/nginx.conf` 的 `location = /api/admin/uploads`（`client_max_body_size 30m`、`proxy_request_buffering on`、讀寫逾時 120 秒）；其他路徑維持全域的 1m。改了 nginx 設定要 `make edge-reload` 才生效。

### 上傳 worker（`scripts/process_uploads.sh`／`.py`＋`app/services/upload_worker.py`）

oneshot 批次，timer 每 5 分鐘（`Persistent=false`），ops 另有「立即執行」（服務名 `upload`）。殼以 flock 取整輪鎖 `data/.upload_worker.lock`（`UPLOAD_WORKER_LOCK_FILE`；忙就 rc 0），fd 經 `UPLOAD_WORKER_LOCK_FD` 交給子命令，子命令對同一個 fd 再 flock 一次證明自己在鎖底下（手動直接跑子命令時自己取鎖）。所以每個子命令開頭看到的 `scanning`／`processing` 一定是上一輪被殺的殘留：一律退回 `quarantined`／`clean`；處理中被中止達 3 次的轉 `failed`（`ingest_error`，可重試），免得同一份檔每 5 分鐘把 worker 打掛一次。

```
quarantined ─認領─▶ scanning ─OK─▶ clean ─認領（持 claude 鎖）─▶ processing ─pre_upsert─▶ draft
scanning ─FOUND（病毒碼）─▶ infected（檔案搬進 infected/<id>.bin、0400；30 天後刪檔，DB 永久保留）
scanning ─FOUND（Heuristics.* 規則）─▶ blocked（scan_heuristic；簽章照記，不算感染）
scanning ─SHA 不符或檔案不見─▶ blocked（hash_mismatch）
scanning ─決定性錯誤第 3 次─▶ blocked；未滿 3 次與一切暫時性錯誤 ─▶ quarantined（scan_attempts+1，永不放行）
processing ─▶ failed（failure_kind）／duplicate（語料已有同 hash）
processing ─斷路器─▶ clean（failure_kind=llm_breaker：延後）
```

1. **scan**（零 LLM、不取 claude 鎖）：條件式認領 `quarantined → scanning` → 重算 SHA-256 與 DB 比對 → `app/services/clamd.py` 的 `scan()`（送出的內容邊讀邊再算一次 SHA）。OK → `clean`（`scan_engine`、`scanned_at`）。FOUND（病毒碼）→ 檔案**先**搬到 `<隔離區>/infected/<upload_id>.bin` 並 chmod 0400，再寫 `infected`、`scan_signature`、`purge_after = now() + 30 天`、稽核 `upload.infected`（actor NULL），journal 一行 ERROR（systemd 底下以 `<3>` 優先序），有設 `REPORT_MARK_ALERT_WEBHOOK` 另送 webhook（URL 經 stdin 給 curl，不進 argv）。FOUND 但簽章是 `Heuristics.*`（clamd 的規則攔截：加密、超過掃描上限）→ `blocked`、`failure_kind=scan_heuristic`、`scan_signature` 照記，不判感染（同一份檔重傳不會被 422 擋、不發感染通知）。暫時性錯誤（連不上、逾時、病毒碼過舊或判斷不出來）→ 退回 `quarantined`、`scan_attempts+1`、`scan_last_error`，本輪停止掃描（其餘檔一定得到同一個答案）。決定性錯誤（clamd 對內容回 ERROR、超限）在 `scan_last_error` 記 `[決定性 n/3]`，第 3 次轉 `blocked`。`blocked` 也寫稽核 `upload.blocked` 並設 30 天的證據保留期。
2. **ingest**：沒有 `clean` 就結束，不碰 LLM。依序：`scripts/backfill_extraction.py` 正在跑（`/proc` 的命令列或 `report-mark-backfill.service` 是 activating）→ 本輪只掃描、不入庫（記憶體：backfill 也載 BGE-M3、不取 claude 鎖）；批次斷路器有效 → 所有 `clean` 標 `failure_kind=llm_breaker`（延後、不算失敗）；`require_llm_key` → 取 claude 鎖（撞鎖 rc 75，乾淨檔維持 `clean`）。逐筆：條件式認領 `clean → processing`（`process_attempts+1`）→ 語料已有同 hash 轉 `duplicate`（不碰 visibility，刪掉隔離區的檔）→ 入庫前檢查在**子行程**跑（`app/services/pdf_preflight.py`：`RLIMIT_AS`＝`UPLOAD_PREFLIGHT_MEMORY_MB`、逾時 300 秒、環境白名單；加密 → `encrypted`、超過 300 頁 → `too_many_pages`、/JavaScript、/Launch、/EmbeddedFile、/XFA、/RichMedia → `active_content`（只有 /OpenAction 本身放行）、看不懂 → `extract_error`、逾時 → `extract_timeout`；通過後再以入庫用的抽取器試抽一次）→ 搬正到 `data/uploads/clean/<hash>/<原始檔名>`（`UPLOAD_CLEAN_DIR`；搬前搬後各驗一次 SHA，以 `client_mtime` 設回 mtime 供 `report_date` 回退；保留原始檔名是因為入庫核心以檔名推券商、日期、行政文件與顯示名稱）→ `scripts/_ingest_core.py` 的 `ingest_one`，`pre_upsert` 在 `upsert_report` 之前、同一個交易寫 visibility 草稿列（`publication='draft'`）與 `report_upload.state='draft'`，由 upsert 那次 commit 一起落庫，所以研報從來沒有可見空窗、`upload_draft_mismatch` 也不會在中間成立。`Outcome` 對應 `failed`：`admin_file`、`scanned`、`not_research`、`tag_failed`（可重試）、`tag_blocked`、`tag_truncated`、`extract_error`、`ingest_error`（可重試）；被擋與截斷的標註照 sync 記 `research.llm_task_failure`。斷路器在途中跳脫時，當筆退回 `clean`、其餘 `clean` 一起標延後；401／402、400 升級這類 LLM 環境錯誤當筆退回 `clean`、rc 2。`--hashes-out data/.upload_last_hashes` 一定會寫（空清單是 0-byte，中止前已 commit 的也在）。
3. **下游**：本輪有新草稿時，殼依序跑 `generate_summaries`、`generate_titles`、`extract_takeaways` 加 `--hashes-file`（各自取 claude 鎖，不巢狀），讓預覽看得到標題、摘要與摘錄。best-effort：任一段非零時把 hashes 複製成 `data/upload_hashes_retained_<時間>.txt` 並印出補跑指令。
4. **cleanup**：寬限期已過的退回件照 `upload_review` 的約定清除——條件式認領（設 `purged_at`）、每一句 DELETE 都再帶一次 `corpus_purgeable_sql`、刪語料時持 claude 鎖（與 sync 的入庫互斥；拿不到就留到下一輪）。守門成立時同一個交易刪 `research_report`（CASCADE 帶走 chunk、摘錄、訊號）、visibility 列、`extraction_log`，commit **之後**刪 `data/tags/<hash>.json`、`data/extracted/<hash>.json`、`data/uploads/clean/<hash>/`、R2 `originals/` 物件（鍵由 hash 與副檔名重推，不信 DB）與隔離區的檔，稽核 `upload.purge`（`corpus_purged`）；守門不成立時只刪隔離區的檔。感染與攔截證據過了 `purge_after` 刪檔、設 `purged_at`、稽核 `upload.evidence_purged`（DB 那一列永久保留）。隔離區（含 `incoming/`、`infected/`）裡沒有對應 DB 列、超過 1 小時的檔案刪除。

退出碼（分界是會不會自己好）：0 做完了（含另一輪在跑、backfill 在跑、斷路器延後）；1 自己壞了（OnFailure 告警）；2 DB 連不上（這輪不跑，web 探針告警）；3 LLM 設定或帳號錯誤（缺金鑰、環境檔讀不到、`LLM_PROVIDER` 拼錯、未知模型、401／402／400 升級；不會自己好，OnFailure 告警，只在有 `clean` 待處理時出現）；75 入庫段撞 claude 鎖。unit 把 2 與 75 列為成功。隔離區與乾淨檔目錄的旋鈕 web（repo 根 `.env`）與 worker（`/etc/default/report-mark-sync`）要設成一樣，或兩邊都留預設。

### 審核（`web/routers/admin_uploads.py`＋`app/services/upload_review.py`）

審核端點同樣是管理員＋`reports.manage`，**不看 `UPLOAD_ENABLED`**（關掉收檔時仍要能處理已經進來的檔案）。處理的是 worker 產出的 `draft`／`failed`。狀態轉移（Python 決定）：

```
draft ─publish─▶ published（之後只能在研報管理「隱藏」，不能退回）
draft、quarantined、clean、failed ─reject（必填原因）─▶ rejected ─寬限期內 unreject─▶ 推導出的退回前狀態
                                                          └─寬限期滿─▶ 由上傳 worker 清除（purged_at）
failed（tag_failed／ingest_error／extract_timeout）─retry─▶ clean（等 worker 下一輪）
```

1. **預覽與原檔**（`GET .../preview`、`GET .../file`）：只限 `draft`／`published`（其餘 409 `upload_state_conflict`）。預覽回正典文字 `clean_extracted(full_text)`（上限 400,000 字）、標籤、標題、摘要、摘錄；標題、摘要、摘錄由批次另外產出，還沒跑時是 null／`pending`。`report_id` 讀取時以 file_hash JOIN 取得，`report_upload` 不存它。原檔只回語料 `originals/` 的 presign（JSON `{url, expires_in, file_name}`，帶 `filename`、TTL ≤ 1 小時，物件鍵對 file_hash 與檔名重算並比對 HEAD 的 sha256）；沒有本機回退，**任何狀態都不讀隔離區**，所以 local 模式沒有原檔預覽（404 `upload_file_unavailable`）。管理面不套可見性過濾。
2. **發布**（`POST .../publish`）：只有 `draft`。同一筆交易依序做條件式 UPDATE `report_upload`（`WHERE state='draft'`）→ `report_visibility` 設 `publication='published'`、`published_at`、`published_by`（`WHERE publication='draft' AND published_at IS NULL`，且語料真的有這份研報）→ 稽核 `upload.publish`（detail：upload_id、file_hash、檔名、`uploaded_by`、`published_by`、`self_published`、`previous_state`、`state`、`hidden`）。任一步影響 0 列就 rollback 回 409。兩個交易同時發布：第二個在 `report_upload` 的列鎖上等，第一個 commit 後條件不成立 → 409。發布不要求權限提升、不禁止上傳者自己發布，稽核記兩者。chunk 早已嵌入，發布當下就能被檢索。晚發布的研報不會補進每日簡報（簡報窗期用 `created_at`）。
3. **退回**（`POST .../reject`，body `{reason}`，去頭尾空白後 1–500 字）：可退回 `draft`／`quarantined`／`clean`／`failed`；`scanning`／`processing` 是 worker 正握著 → 409 `upload_busy`；`published` → 409 `upload_published_use_hide`。草稿另有守門：語料裡同 hash 的研報必須仍是「草稿且 `published_at IS NULL`」（NAS 送來的沒有 visibility 列＝已發布），否則 409，免得日後清除刪到已發布的研報。同一筆交易寫 `rejected`、原因、決策人與時刻、`purge_after = now() + UPLOAD_REJECT_GRACE_HOURS`（預設 24）與稽核 `upload.reject`（detail 只有 `reason_chars`，不含原因全文）。**語料、草稿的 visibility 列與檔案都不動**：退回後的草稿仍不可見。
4. **撤銷退回**（`POST .../unreject`）：只限 `rejected`、`purged_at IS NULL`、`now() < purge_after`（過期 409 `upload_reject_expired`）。`report_upload` 沒有欄位記退回前的狀態，依事實推導（`upload_review.restore_state_after_unreject`）：語料有這份研報且是從未發布的草稿 → `draft`；`failure_kind` 是失敗（`llm_breaker` 這種延後標記不算）→ `failed`；`scanned_at` 有值 → `clean`；其餘 → `quarantined`。清掉原因、決策人與時刻、`purge_after`，稽核 `upload.unreject`。推導出 `clean`／`quarantined` 時與重試一樣受全站處理中上限（同一把 advisory lock、同一套計數，超過 429 `upload_quota_exceeded`）；回到 `draft`／`failed` 不佔產能，不檢查。寬限期內若已有同 hash 的另一筆進行中上傳（只可能在語料還沒有它時），撞 partial unique index → 409 `upload_active_conflict`。
5. **重試**（`POST .../retry`）：只限 `failed` 且 `failure_kind` 是 `tag_failed`／`ingest_error`／`extract_timeout`（其他 409 `upload_not_retryable`）。轉回 `clean`，清 `failure_kind`／`failure_detail`，`process_attempts` 不歸零，稽核 `upload.retry`；同樣可能撞 partial unique index（409 `upload_active_conflict`）。重試也受全站處理中上限 `UPLOAD_MAX_IN_FLIGHT`（與收檔同一套計數 `uploads.IN_FLIGHT_STATES`、同一把 advisory lock），轉回 `clean` 後超過上限就 429 `upload_quota_exceeded`。

給上傳 worker 清除步驟的介面（本 API 不刪任何東西；worker 的用法見上一節第 4 點）：`upload_review.list_purgeable` 列出寬限期已過、`purged_at IS NULL` 的退回件與 `corpus_purgeable` 旗標；`corpus_purgeable_sql(欄位)` 是同一個守門的 SQL 片段——語料從未發布過（沒有研報，或研報的 visibility 是草稿且 `published_at IS NULL`；visibility 沒有任何一列已發布或曾被發布），且同 hash 沒有別的上傳處於進行中或已發布。不成立時只能刪那筆上傳自己的隔離區檔案。

## 標籤維度（標準市場代碼）

| 維度 | 值（逐字） |
|---|---|
| `market` | `TW`、`US`、`HK`、`CN`、`FX`、`WTX`、`MACRO`、`GLOBAL`、`CRYPTO`（`tests/test_tagging.py` 逐字釘住的標準市場代碼；與 findb 對齊是廷豐 profile 的跨 repo 契約。profile 只能選使用者端的子集與改顯示名稱，見 `app/profile.py`） |
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

請求 JSON：`question`（≤2000）、`conversation_id`、`market`、`instrument_type`、`relates_stock`、`relates_futures`、`report_type`、`k`（夾到 1–20）、`regenerate_of`、`edit_of`、`request_id`（冪等鍵，`qa_log.request_id` UNIQUE）、`locale`（`zh-Hant`／`en`）、`web`（每題決定；網搜沒有後端，前端一律送 `false`）。

事件序：`queued`（排隊時，`scope`、`position`、`capacity`）→ `status`（`stage`、`thinking_ms`）→ `sources`（`n`、`report_id`、`file_name`、`title`、`market`、`report_date`、`is_latest`）→ `ext_sources`（網搜或受信任資料，`title`、`url`）→ `token`… → `followups` → `done`。婉拒（離題、時效、建議風險）走 `notice` 再 `done{notice_kind}`。傳輸層錯誤 `error{detail}`。心跳每 20 秒一行 SSE 註解。

`POST /api/ask/stop` 把使用者中止時的部分答案與 `stages` 落 `qa_log`（`stopped=true`），回 `qa_id`。

**擁有者**：兩支端點寫入的 `qa_log.user_id` 是目前登入者（免登入開發模式為 NULL）。NULL 是個別帳號上線前的共用歷史，一般介面不可見。隔離有兩道：路由在開始串流前以 `deps.conversation_is_foreign`／`deps.qa_is_foreign` 檢查參照（列存在但不是你的＝404；查詢失敗＝503，不 fail-open；不存在照舊當新題／新串，因為 `_log_qa` 是 best-effort，寫入失敗後的續問不能被擋）；服務層每條 qa_log SQL 帶 `user_id IS NOT DISTINCT FROM :uid`（續問歷史、舊列 meta、版本數、停用／截斷 UPDATE 都只認自己的列），`request_id` 的收斂也只限同一擁有者（撞到別人的 `request_id` 寫入失敗）。`tests/test_qa_isolation.py` 以 AST 守門要求每個 qa_log 函式呼叫都帶 `user_id=`。

### 健康端點 `GET /healthz/llm`（只回答本機直連）

回 `{"llm": state}`；只給本機探針 `scripts/check_web_health.sh` 用（503 一律退出碼 8），經邊緣一律 404。打目前 `LLM_PROVIDER` 的模型清單端點（不計費；OpenAI 族 `GET /models`、Azure `GET /openai/models?api-version=…`、Anthropic `GET /v1/models`）：各家都沒有餘額 API，所以只判斷端點與金鑰，沒有「快用完」的預警（預算告警在各供應商後台設定）。

| state | HTTP | 條件 |
|---|---|---|
| `unknown` | 200 | 還沒有完成過任何一次探測（或第一次連不上） |
| `ok` | 200 | 模型清單端點回 200，或 403 但只缺 Models 讀取權限（`api.model.read`） |
| `exhausted` | 503 | 探測回額度用完，或本行程的真實請求碰過額度用完且之後還沒有成功的真實請求 |
| `auth_failed` | 503 | 探測回 401 或其他 403，或沒有目前 provider 的金鑰 |
| `unreachable` | 503 | 連續 2 次連不上（網路、逾時、429／5xx） |
| `indeterminate` | 503 | 端點設定錯（404 等）、其他未預期的回應 |
| `misconfigured` | 503 | 問答主答（`ASK_ANSWER_MODEL`）解析到白名單外的名稱，或目前 `LLM_PROVIDER` 的端點設定有誤（`llm_models.config_problems`） |

`misconfigured` 時不探端點（每題都回設定有誤；只看主答，其他線上任務都 fail-open，不算）。ok 快取 600 秒、其餘 60 秒，每次最多等 4 秒。

### 對外 API（`/external/v1/*`）

四條端點：`GET /external/v1/search`（需 `search` scope）、`GET /external/v1/reports/{report_id}/file-url`（需 `report.file` scope）、`POST /external/v1/uploads` 與 `GET /external/v1/uploads/{upload_id}`（需 `report.upload` scope），參數與回應欄位見 README 的 API 表。路由在 `web/routers/external.py`，認證在 `web/external_auth.py`，用戶端規則在 `app/services/api_clients.py`、授權範圍在 `app/services/entitlement.py`，原檔連結與站內 `/api/report/{report_id}/file` 共用 `app/services/original_file_url.py`。交給 API 用戶端的說明是 `docs/EXTERNAL_API.md`（只寫用戶端需要知道的事），後台「API 用戶端」頁的「說明文件」分頁照它渲染（`{BASE_URL}` 代入當下站台）、「試打」分頁的端點與參數表單也從它解析；它與路由、參數、scope、錯誤碼、回應欄位的一致性由 `tests/test_external_api_doc.py` 對帳。

**認證**：`Authorization: Bearer <api_key>`，一個 API 用戶端一把金鑰（`rmk_<8 hex>_<43 字元>`；DB 只存 sha256，原始金鑰只在建立／輪替時顯示一次）。`web/server.py` 的 session middleware 對 `/external/` 前綴整個放行（不查、不發 session cookie，免登入開發模式也不作用），認證完全由路由 dependency 負責；站內 `/api/*` 一律不認 Bearer。每個請求都查 DB（不快取），停用與輪替下一個請求就生效。檢查順序：解析標頭 → 查金鑰 → scope → 每分鐘限流 → 每日額度；未認證的請求拿到的是 401，不是參數驗證的 422。日誌只記 client id 與 `key_prefix`，不記原始金鑰。

**錯誤碼**（格式同站內 `{detail, code, request_id}`）：

| HTTP | code | 條件 |
|---|---|---|
| 401 | `api_key_missing` | 沒有 `Authorization` 標頭（帶 `WWW-Authenticate: Bearer`） |
| 401 | `api_key_invalid` | 不是 `Bearer <key>` 形式、格式不符、查無、已停用、已輪替掉的舊金鑰——刻意同一個回應 |
| 403 | `api_scope_missing` | 金鑰沒有該端點要的 scope |
| 429 | `api_rate_limited` | 超過每分鐘上限（`Retry-After`：補到一個名額的秒數） |
| 429 | `api_quota_exceeded` | 超過每日額度（`Retry-After`：到台北隔日 0 點的秒數） |
| 404 | `report_not_found` | file-url：查無、被隱藏、草稿、不在授權範圍、`report_id` 不是 UUID——刻意同一個回應 |
| 404 | `original_not_found` | file-url：研報沒有原檔物件，或站台不是物件儲存模式（local 不產生對外 URL） |
| 503 | `original_unavailable` | file-url：物件指標或 sha256 對不上、物件儲存無法使用 |
| 503 | `api_auth_unavailable` | 查金鑰或計數額度時 DB 不可用 |
| 503 | `api_client_misconfigured` | 用戶端的授權範圍無效（只可能是直接改 DB） |
| 422 | `validation_error` | 已認證但參數超出範圍（例如 `limit` > 20、未知的 `sort`） |

上傳端點另有一組錯誤碼（`upload_unavailable`、`upload_not_pdf`、`upload_too_large`、`upload_duplicate`、`upload_rejected`、`upload_quota_exceeded`、`upload_busy`、`upload_not_found`、`invalid_filename`、`invalid_request`），完整的表在 `docs/EXTERNAL_API.md`。

**上傳**：`POST /external/v1/uploads` 的檔案進上一節的上傳流程（隔離區 → 預檢與 ClamAV → worker 入庫成草稿 → 管理員發布），不直接入庫；收檔步驟與 `POST /api/admin/uploads` 相同，差別是上傳者記成 API 用戶端（`report_upload.api_client_id`，`uploaded_by` 為 NULL；管理後台的上傳清單顯示「API：<用戶端名稱>」）、每日份數依用戶端計（`UPLOAD_DAILY_QUOTA`，帳號的個人覆寫不適用）、重複的檔案一律 409 `upload_duplicate`（不說它在語料裡是已發布、草稿還是隱藏）。`GET /external/v1/uploads/{upload_id}` 只查得到同一個用戶端收進來的上傳，狀態收斂成 `processing`／`pending_review`／`published`／`rejected`（`web/routers/external.py` 的 `EXTERNAL_UPLOAD_STATUS`），不回掃描特徵、失敗細節與決策原因。對外上傳不收 metadata 側檔（側檔只走輪詢型連接器）。

**限流與額度**：每分鐘上限是每個用戶端一個 token bucket（容量＝`rate_limit_per_min`，每秒補 1/60），狀態在 web 行程記憶體、重啟即滿；管理員調低上限下一個請求就生效。被限流擋下的請求不碰 DB、不計入每日額度。每日額度（`daily_quota`）以**台北時間的日曆日**計，台北 0 點（UTC 16:00）重置；通過限流的請求不論結果都計入當日計數（含額度用完後被拒的那幾次）。

**授權範圍（entitlement）**：allowlist、fail-closed。`market` 必填；`source`／`report_type`／`instrument_type` 沒設定＝不限；維度內 OR、維度間 AND；研報在有設定的維度為 NULL 一律不符。用戶端看得到的研報＝可見（未隱藏、已發布）**且**在授權範圍內，兩個條件都在 SQL 裡（dense 與字面兩路都下推，不在 Python 事後過濾）。搜尋請求帶的 `market`／`source`／`report_type`／`instrument_type` 各是一個值，只能把該維度縮小成那一個值、且該值必須在授權清單內（`instrument_type` 也一樣，不靠陣列重疊放寬）；任一維度交集為空直接回空結果，不打檢索。file-url 不信任先前的搜尋結果，每次重新以可見性＋授權範圍查那一篇。

**原檔 URL 有效期**：presigned GET，有效期 `EXTERNAL_FILE_URL_TTL_SECONDS`（預設 600 秒，60..3600），`expires_at`／`file_url_expires_at` 是簽發時刻＋有效期（ISO 8601 UTC，秒精度）。簽出前一律檢查 object key 逐字等於 `originals/<hash 前兩碼>/<file_hash><副檔名>`；file-url 端點另以 HEAD 驗物件 metadata 的 sha256，搜尋結果附的 `file_url` 不逐筆 HEAD，產生失敗的那筆給 null、不讓整個搜尋失敗。連結是持有即可下載的憑證，交出去就收不回，回應帶 `Cache-Control: no-store`。

### 契約守門

- `tests/fixtures/sse_events.json` 是後端與前端共吃的單一真相（`tests/test_sse_event_contract.py`、`frontend/src/lib/sseEventContract.test.ts`），只有 `ask` 一組事件。新事件或欄位：fixture 與 `frontend/src/lib/askSchemas.ts` 的 zod（預設 strip，未宣告鍵靜默丟掉；新欄位用 `optional()`）兩處都要動。
- 雷達 `app/services/radar/schemas.py` 的 `Literal` 與 `frontend/src/lib/radarSchemas.ts` 逐字鏡像；閱讀頁 `app/services/reading/schemas.py` 與 `frontend/src/lib/readingSchemas.ts` 同理；`web/routers/brief.py` 的 pydantic 與 `frontend/src/lib/briefSchemas.ts` 同理。
- `/api/progress` 新增鍵要同步改 `frontend/src/lib/progressSchema.ts`；`/api/review/judge-scale`（`evaluation.qa` 的判定尺子集）的鍵對應 `frontend/src/lib/reviewSchemas.ts` 的 `judgeScaleSchema`。兩者與 `/api/stats` 共用 `web/stats_snapshot.py` 的同一份 DB 快照，判定尺不另寫查詢。
- 忠實度分數的讀取端（`/api/progress` 的 `evaluation.qa`、`/api/review/queue?kind=faithfulness`、`scripts/eval_faithfulness.py`）的分數類統計只計現行 judge（管線分頁的已查核數 `checked` 例外：它是覆蓋率語意，計所有 judge），共用 `app/services/judge_schema.py` 的過濾；`qa_log.evaluation` 帶 `judge_model`、`judge_schema_version`、`degraded_reason`（`unavailable`／`timeout`／`truncated`／`empty`／`parse`／`schema`／`content_risk`／`account`／`error`，詞彙在 `app/services/faithfulness.py`）、`elapsed_ms`、`n_missing_verdicts`（grounding 漏判而計為 unsupported 的條數），以及 API 回報的 `judge_model_resp`、`judge_fingerprint`、`judge_requests`、`usage`；缺 `judge_model` 的舊列視為 `claude-haiku-4-5`。judge 回應走 schema v2 嚴格驗證（同一模組：`statements` 必須是字串陣列、`idx` 恰好覆蓋全部條目、判定值必須是布林），不合格重試 1 次後線上記 `degraded_reason=schema`、離線記該指標 None；線上 grounding 缺 idx 仍計 unsupported，但一條都沒判（`{"verdicts": []}`）算 schema 錯。

## 物件儲存（R2）

兩個環境都是 `OBJECT_STORAGE_MODE=r2`、共用同一個私有 bucket。模式語意：`local`（程式預設，只讀本機檔）、`hybrid`（R2 優先讀、local 回退）、`r2`（只認 object key，缺 key 即 503 不回退）。非 local 缺任一 `R2_*` 啟動即 fail-closed；憑證在 repo 根 `.env`（web）與 `/etc/default/report-mark-sync`（批次）各一份、逐字相同。

bucket 裡的前綴：

| 前綴 | 內容 | 寫入 |
|---|---|---|
| `originals/` | 研報原檔，key 是 `originals/<hash 前兩碼>/<file_hash><副檔名>`，metadata 帶 sha256 | 入庫時 create-only、SHA 驗證；兩個環境入庫同一份檔得到同一個 key，不會互相覆寫 |
| `inbox/` | 辦公室主機推給 EC2 的新檔（相對路徑＋mtime），可覆寫 | `scripts/r2_inbox.py push`；`originals/` 不能代替它（沒有檔名與 mtime） |
| `beacons/` | 主機信標 `beacons/<名稱>.json`，每次發布整份取代 | `scripts/publish_host_beacon.py` |

監控分兩層：`/healthz` 不探 R2，bucket／憑證層級的失效由健康探針經 `/healthz/storage` 偵測（`scripts/check_web_health.sh` 退出碼 6，約 5 分鐘內）；個別物件缺漏靠 `report-mark-r2-reconcile.timer`（每週一 07:00，`scripts/reconcile_object_storage.py`，唯讀）對帳並接告警鏈。

對帳結果的判讀：`unkeyed`、`key_mismatch`、`errors` 讓退出碼為 1 並告警，其餘只計數（管理後台「資料健康」列為 warn）。orphan 掃描只看 `originals/` 前綴；`--limit` 會讓 orphan 判定略過（沒讀完全部 DB 列就不判 orphan）。

- **`unkeyed`**（研報列沒有 `source_object_key`）：以 `scripts/migrate_object_storage.py` 補傳（先 `--dry-run`；只補缺 key 的 originals，上傳成功後才更新 DB key，不刪除、不重嵌、不重匯入，可重跑）。該腳本不讀 repo 根 `.env`，手動跑要自己帶 `OBJECT_STORAGE_MODE` 與 `R2_*`（與 sync 的環境檔相同的值），否則以 local 模式直接結束。本機檔的 SHA-256 與 `file_hash` 不同時它回報 `HASH_MISMATCH`、不上傳：鏡像目錄的檔案已被同檔名的新版取代，這一列的原檔無法從本機取得。
- **`LOCAL_REPLACED`**（只印出、不計數）：券商以同檔名重發研報，新檔另成一篇研報（新 hash、自己的 key）。那個路徑上已不是這一列的內容，所以不拿它比對；R2 物件仍以 DB 的 `file_hash` 驗證。
- **orphan**：指「這次連的 DB」沒有對應列。bucket 是兩個環境共用的，它可能是只有對方入庫的研報——兩邊各自同步，券商換版的時間差會讓兩邊入到不同版本。封存或刪除前要查過每一個寫這個 bucket 的 DB。

瀏覽器端兩個前提：bucket 要設 CORS（只開 GET／HEAD；規則檔 `deploy/r2-cors-research.json`），沒設就閱讀頁 PDF 檢視器整頁靜默失敗、伺服器零錯誤；presign 一律帶 `filename`（key 是 hash，跨來源 302 後 `<a download>` 失效）。

## 完整重跑指令

新 checkout 的建置（相依、容器、schema、`.env`、第一位管理員）見 `README.md`「快速開始」。

```bash
# 全語料三支（初次建庫或補歷史；手動跑 extract_all 要帶 EXTRACTOR=pdfplumber）
EXTRACTOR=pdfplumber uv run python scripts/extract_all.py
uv run python scripts/tag_all_cli.py --workers 8
uv run python scripts/ingest_all.py
#   或一鍵續跑：bash scripts/resume_corpus.sh

# 派生批次（各自冪等，以 flock 互斥、不可併發）
make takeaways
make signals
make summaries
make titles
make brief

# 手動跑一輪同步（部署主機）：經 unit 才會載入 /etc/default/report-mark-sync
sudo systemctl start report-mark-sync.service
# make sync-once 直接跑殼、不載入那個環境檔（EXTRACTOR、SYNC_INBOX_PUSH、R2_* 都退回程式預設），不要拿來代替上面那一行
```

## 排錯

| 症狀 | 看哪裡 |
|---|---|
| 問答每題回「帳號異常」／「設定有誤」 | `/healthz/llm`（`scripts/check_web_health.sh` rc=8）與 web 日誌的啟動自檢（金鑰、白名單外的模型名、`ASK_ENABLE_WEB`）；處置見 `docs/production_resilience.md` |
| 原檔下載／PDF 檢視全數 503，其餘正常 | R2 bucket 或憑證（repo root `.env` 與 `/etc/default/report-mark-sync` 兩份要逐字相同）；`scripts/check_web_health.sh` rc=6 就是這個，細節在 web 日誌的「healthz 物件儲存探測失敗」 |
| 摘要、摘錄無聲漏跑 | `make llm-blocked`（跳過名單）與各批次 `*_failures.log`；LLM 帳號狀態看 `scripts/check_web_health.sh` rc=8 |
| 批次 rc=75 | 撞 LLM 批次鎖，不是錯誤；`make freshness` 判是否停更 |
| 每個查詢 500 但登入正常 | DB 沒起來（假活著），打 `/healthz` 不看 `systemctl is-active` |
| `/api/progress` 回 422 | router 檔輔助函式夾在裝飾器與 handler 之間 |
| SPA 回 503 | `frontend/dist` 不存在，`make build-web` |
| 閱讀頁 PDF 整頁空白、伺服器零錯誤 | R2 bucket CORS |
| 訊號大量 `rejected` | `data/signal_failures.log` 每列的原因：`API[<kind>]` 是呼叫失敗（逾時、過載多半是供應商端暫時的，下一輪會重打），「回應不是合法 JSON」之類是有回應但不能用（連續 3 輪進跳過名單，`make llm-blocked`）；不是資料壞 |
| `make db-audit` 紅 | `fsync=off` 殘留（`make restore-durability`）、孤兒列、`content_norm` 漂移 |
| 評測退出碼 3 | 新指標未在 `scripts/eval_compare.py` 的 `METRIC_SPECS` 補方向 |
| 評測退出碼 2，訊息是「量尺只有 … 有記錄」 | 一邊是記錄量尺之前的舊結果檔（例如 `eval/baselines/baseline-2026-09-02.json`）；兩邊都用同一版 `eval/run_ragas.py` 重跑，不是放寬比較器 |
| 正式環境沒有新研報但 sync rc=0 | 來源在辦公室主機：看 `/healthz/feed`（`scripts/check_feed_health.sh`）與管理後台「維運 → 來源主機」；處置見 `docs/production_resilience.md`「來源主機信標」 |
