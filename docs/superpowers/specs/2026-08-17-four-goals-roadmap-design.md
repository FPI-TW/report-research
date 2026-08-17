# 四項目標總體路線圖設計規格

日期：2026-08-17
狀態：待核准
交付形式：規劃文件（本文件不含實作步驟；各項的實作計畫另行產出）

## 1. 本文件的範圍

四項目標放在同一份文件裡，是因為它們**彼此有依賴、且共用同一組約束**，分開規劃會在介面上打架。本文件負責四件事：界定每一項的範圍邊界、標出依賴關係、列出已量測與未量測的事實、給出建議順序。**每一項的細部設計與實作計畫另行產出**，不在本文件內。

四項目標：

| 編號 | 目標 | 工程量級 |
|---|---|---|
| 1 | 問答回答速度優化 | 天 |
| 2 | DB（連同 Web/API）上 AWS 雲端 | **週** |
| 3 | 搜尋支援股票代號 | 天 |
| 4 | Claude 問答改接 API | 天 |

## 2. 已確認的決策

以下六項在 2026-08-17 的規劃對話中確認，本文件其餘部分以此為前提。

| # | 決策 | 內容 |
|---|---|---|
| D1 | 推進方式 | 先總體路線圖，核准後再逐項寫 spec 與實作計畫 |
| D2 | 上雲範圍 | **DB 與 Web/API 一起上**，照已核准的 `docs/superpowers/specs/2026-08-14-aws-deployment-runbook-web-design.md` §6.3 + §6.4，RDS 與 ECS Fargate 同 VPC |
| D3 | API 化範圍 | **只改線上路徑**；七支批次腳本維持 `claude` CLI ＋ Max 訂閱，跑在 Private EC2 Worker |
| D4 | 代號搜尋行為 | **標的優先、全文次位**。不做代號 ↔ 名稱互查（不需要對照表） |
| D5 | GPU 驗證路徑 | **若要驗證 GPU**，租雲端跑 benchmark，不在本地裝顯卡、不使用內顯。D5 決定的是「怎麼驗」，「要不要驗」由 O1 的目標流量決定 |
| D6 | 品質定位 | **品質是約束，不是目標**。現行答案品質可接受，加速不得讓它變差 |

D6 是四項裡影響最深的一條，因為它把「加速手段」的優先序整個倒過來（見 §5.2）。

## 3. 未解決的決策

以下兩項刻意留空，因為現在沒有做決定所需的數字。**不要在取得數字之前選邊。**

### O1：GPU reranker 的去留

觸發條件是**目標流量**，而非 GPU 的速度。以 2026-07-29 實測的生產量（近 14 天 10 次 `/api/ask`，約 0.7 題／天）算：

| 方案 | 月成本量級 | 每題延遲 | 每題成本 |
|---|---|---|---|
| CPU（現有機器） | $0 邊際 | 34s | $0 |
| CPU ＋ ONNX int8 | $0 邊際 | 推估 10–15s（**未量測**） | $0 |
| SageMaker Async（T4，scale-to-zero） | ~$0.5 | **冷啟動 2–5 分鐘** | ~$0.03 |
| SageMaker Real-time（T4 常駐） | **~$500+** | 2–3s | **~$25** |

價格為 us-east-1 參考量級，ap-east-2 台北區只會更高，**實際數字需查詢後填入**。

三個推論：

1. Real-time 在 0.7 題／天下每題約 $25，付的是 24/7 待命費。
2. Async 的 scale-to-zero 意味著題與題之間隔了一整天，實例早被收掉——**每一題都是冷啟動，實際延遲比現行 CPU 的 34s 更差一個量級**。
3. 因此在現行流量下，SageMaker 兩種形態都劣於 CPU。

**GPU 只有在目標流量顯著放大（每天數十至數百題）時才重新成立**：流量密度會消滅冷啟動、並把 Real-time 的每題成本攤平。決策條件因此是「上雲後要開放給多少人」，這個問題目前沒有答案。

在取得答案之前，路線圖**保留 GPU 分支但不啟動**，並要求 reranker 抽成可替換介面（見 §5.3），使日後換 GPU 不需重寫。

### O2：SageMaker Async 是否適用

即使 O1 判定要上 GPU，Async 仍是待驗的。**Async 的設計用途不是線上問答**——AWS 的規格是單次最長 1 小時、payload 最大 1GB，目標情境是影片轉檔、大型文件批次推論那類可容忍延遲的工作。已核准規格 §6.6 自己也留了後路（「Async 未通過延遲 gate 時，評估 SageMaker Real-time」）。

驗證方式是 gate 而非並行（修正自初版規劃）：

```
第 1 步  RunPod 量 T4 / L4 純推論（<$2，一小時）
         ＋ 本地量 ONNX int8 CPU 作為對照組
         │
         ├─ GPU 未顯著優於 CPU 量化 ──→ 砍掉整個 SageMaker 分支
         │
         └─ GPU 快一個量級 ──────────→ 第 2 步：SageMaker Async 只量冷啟動與排隊
                                              ├─ 冷啟動可接受 → Async
                                              └─ 冷啟動太久   → Real-time，重算成本
```

**本地 ONNX int8 是這個決策的對照組，不是備案。** 沒有它，GPU 的數字沒有比較基準——「GPU 跑 3 秒」在「CPU 量化跑 10 秒且零成本」的對照下未必買得下來。

benchmark 的方法論要求：

- 餵**真實語料**（從 `qa_log` 撈近期真問題 ＋ 對應 `report_chunk`）。cross-encoder 成本由 tokenize 後長度決定，中文研報 chunk（600 字 ＋ 80 重疊）的 token 分佈與英文差異大，`max_length=512` 是否常態截斷也只有真實資料看得出來。
- 量 50 對（問答）與 120 對（研報）兩種候選數。
- 冷啟動獨立量測，不與熱機數字混報。

## 4. 現況：四項都沒有可信基準

這是路線圖的第 0 步，不是形式主義。四項裡有三項的優先序取決於數字，而那些數字目前只存在於程式碼註解裡。

### 4.1 延遲基準：分段量測不存在

已知的僅有註解記錄的點狀實測值：

| 段落 | 實測 | 出處 |
|---|---|---|
| cross-encoder rerank，50 對（20 核 CPU） | ~34s | `app/config.py:201-203` |
| cross-encoder rerank，120 對 | ~93s | 同上 |
| 檢索整段（含 rerank） | ~48s | `app/services/answer.py:2006` |
| 路由分類（與檢索並行，不疊加） | ~12s | 同上 |
| `claude` CLI 冷啟動 TTFT（每次重付 ~24K token 系統提示） | ~10s | `app/config.py:211-213` |

推估首 token 牆鐘 ≈ **55–60 秒**（檢索 48s ＋ CLI 冷啟動 10s）。**這是推估不是量測。**

缺口有二：

1. **`qa_log.thinking_ms` / `latency_ms` 的實際分佈從未統計過。** 可撈窗口約一個月——`logging_setup.py` 於 2026-07-29 修好之前，`qa_timing` 這條 log 完全沒有資料。
2. **`timer.mark("retrieve")` 把 embed → dense HNSW → lexical trgm → 去重融合整段當成一段**（`app/services/retrieval_pipeline.py:111-116`）。dense 與 lexical 各佔多少完全未知，而 `ASK_DENSE_SCAN=400` 的 HNSW 掃描與 57 萬列的 trgm GIN 是兩種完全不同的成本結構。

### 4.2 品質基準：已過期

`eval/` harness 存在且可用（`make eval-compare` 走 `scripts/eval_compare.py`，退出碼即結論：`0` 無劣化／`1` 有劣化／`2` 不可比／`3` 有未分類指標）。但：

- **baseline 停在 M4（RAGAS）／ M7（研報），M8–M10 全未重跑。** M8 忠實度、M9 Typst 渲染、M10 雙語之後系統已大幅改動。
- `context_precision` 0.679 vs 門檻 0.8，**自 M0 起未曾通過**。`answer_relevancy` 已於 PR #137 校準到 0.55 並通過。
- `baseline-2026-07-29` 是第一份乾淨量測（先前幾份有 judge 逾時掉題，數字虛高）。
- **PR #140 的量綱修正預期會再壓低 `context_precision`，該數字目前無人量過。**

後果：現在做任何加速改動後跑 `eval-compare`，比到的是「今天 vs 兩個月前一套已不存在的系統」。**D6 把品質定為硬約束，而硬約束需要可比的對照——所以重跑 baseline 是第 0 步，不是可選項。**

### 4.3 資料覆蓋率基準：目標 3 需要

`research_report.stock_targets` 的非空覆蓋率未知。它由標註批次抽取，覆蓋率太低會讓「標的優先」那一路經常是空的。一句 SQL 可得，列入第 0 步。

### 4.4 硬體現況（2026-08-17 實測）

| 項目 | 值 |
|---|---|
| CPU | Intel i7-14700，20 核 / 28 執行緒 |
| GPU | **Intel UHD Graphics 770（內顯）** — 無 NVIDIA、無 CUDA |
| RAM | 31.7 GB |
| WSL | Ubuntu-24.04（生產所在） |
| torch | `pyproject.toml:41-47` 釘死 CPU-only wheel index |

CPU 核數與 `app/config.py:201` 註解裡的「20 核 CPU」一致，確認註解中的實測值來自本機。

**內顯不構成 GPU 路徑**：UHD 770 為 32 EU Xe-LP，峰值 FP32 約 0.7–0.8 TFLOPS 且與 CPU 共用系統記憶體頻寬；i7-14700 的 20 核 AVX2 約 1–1.5 TFLOPS。把 bge-reranker-v2-m3（568M 參數 XLM-RoBERTa-large）搬上內顯預期是**退步**。OpenVINO / IPEX 能否跑起來與此結論無關。

## 5. 目標 1：問答加速

### 5.1 現況拆解

首 token 前的牆鐘由兩塊主導：**cross-encoder rerank 約 34 秒**、**`claude` CLI spawn 約 10 秒**。其餘（embed 有 LRU 快取、路由與檢索並行）不是瓶頸。

一個獨立於延遲的體感問題：**rerank 那 34 秒完全不送任何 SSE 事件**。使用者看到「找到 N 篇」之後長時間靜止，與伺服器卡死無從分辨。

### 5.2 手段與品質風險（D6 使這張表倒過來讀）

| 手段 | 省時 | 品質風險 |
|---|---|---|
| GPU（同模型同參數） | −30s | **零**。純硬體加速，fp32 下數值一致 |
| rerank 期間補進度事件 | 0（僅體感） | **零** |
| CLI → API（目標 4） | −10s | **中**。CLI `-p` headless 與 API messages 不等價 |
| ONNX Runtime ＋ int8 量化 | −8~12s（推估） | **中**。分數數值漂移 → 排序可能改變 |
| rerank 候選 50 → 25 | −17s | **高**。直接砍掉 reranker 可見的候選 |
| `max_length` 512 → 256 | −17s | **高**。直接截斷 passage 後半段 |

**D6 的直接後果**：零成本的手段全都要付品質，零品質風險的手段（GPU）成本高昂。這個張力沒有繞過的辦法，只能靠 `eval-compare` 對每個手段逐一定價——**掉多少品質、換多少秒、值不值得**。

`ASK_RELEVANCE_FLOOR=0.62` 的爆炸半徑比表面小：它比的是 rerank **之前**的 fused 快照（`app/services/retrieval_pipeline.py:119-128`），rerank 分只影響排序不影響閘門。這降低了量化造成數值漂移的風險，但不消除它。

### 5.3 reranker 介面抽象（O1 的先決條件）

無論 O1 最終走哪條，reranker 都應抽成可替換後端（現行 CPU transformers、ONNX、遠端 HTTP endpoint 三種實作共用同一介面）。理由是 O1 現在無法決定，而**介面抽象讓決定可以延後而不付重寫代價**。

必須保留的既有契約（`app/services/rerank.py:124-157` docstring 已明載）：

- **回傳物件同一性**：所有 fail-open 路徑回傳「輸入的同一 list 物件」，成功路徑回傳新 list。`retrieval_pipeline._rerank_stage` 以 `is` 判定 rerank 是否實際套用，那是多查詢降級的唯一依據。守護測試在 `tests/test_rerank.py`。
- **fail-open 語意**：載入失敗熔斷、推論失敗、形狀不符、NaN/inf、deadline 中止，一律回原序。
- **deadline 在批次邊界生效**（`_BATCH_SIZE = 16`），使被放棄的背景工作能收手、釋放 semaphore。

### 5.4 明確不做

- 不在本地裝顯卡（D5）。
- 不使用 Intel 內顯（§4.4）。
- **不在未取得 §4.2 的 baseline 之前調整任何品質敏感旋鈕。**

## 6. 目標 2：整套上雲

架構已核准（`2026-08-14-aws-deployment-runbook-web-design.md` §6.2–§6.8），**本目標要規劃的是遷移程序，不是架構選型**。

### 6.1 會讓 App 起不來或靜默走錯的四項

| # | 風險 | 出處 | 性質 |
|---|---|---|---|
| R1 | **RDS 的 pgvector 必須 ≥ 0.8**。檢索與閱讀頁用 `SET LOCAL hnsw.iterative_scan`，該 GUC 在更舊版本不存在 | `app/services/db.py:76,94-130` | **fail-closed**，版本不對 App 直接起不來。**第一個要驗的前置條件** |
| R2 | **`REPORT_MARK_DB_URL` 目前未設**，走 `db.py` 的 `localhost:5436` fallback | `app/services/db.py:23-26` | 地端安全，上雲後變陷阱：忘了設就靜默連向不存在的本機庫 |
| R3 | **`_ASK_GATE` 的 per-process 語意在 ECS 上失效**。`assert_single_worker()` 檢查的是單行程內的 worker 數；N 個 Fargate task 是 N 個行程，各自通過檢查、各自持有容量 3 的閘 → 實際併發 3N | `web/routers/ask.py:52`、`web/server.py` | **靜默**。RDS Proxy 解得了連線數，解不了併發閘 |
| R4 | **`scripts/ingest_lowio.sh` 在 RDS 上是死的**——`ALTER SYSTEM` 被 RDS 禁用。連帶 `scripts/db_audit.py` 的耐久性斷言在 RDS 上永遠不觸發 | `CLAUDE.md` 的 lowio 段落 | 留著不改會讓人誤以為那條稽核仍在守 |

R3 連帶影響 DB 連線數：`DB_POOL_SIZE=5 + DB_MAX_OVERFLOW=15 = 20` 是 **per-process**（`app/config.py:296-312`），N 個 task 就是 20N。`.env.example` 有現成算式，遷移時必須重算。

### 6.2 其餘六項

| # | 項目 | 說明 |
|---|---|---|
| R5 | 576,305 vector 的 HNSW 重建 | 建議 `pg_dump` 不含索引 → restore → `CREATE INDEX CONCURRENTLY`；重建期需調 `maintenance_work_mem`，完成後調回 |
| R6 | `content_norm` GENERATED 欄位 | restore 時 57 萬列全部重算 `normalize(NFKC) + regexp_replace`，需納入時間估算 |
| R7 | 導入來源搬遷 | `研報自動匯入/` NAS 掛載 → S3（已核准規格 §6.5）。NAS 在公司內網，需規劃 NAS → S3 同步 |
| R8 | 模型檔進容器 | BGE-M3 ＋ reranker 合計 2–4GB。打進 image（image 巨大、部署慢）vs 掛 EFS（冷啟動慢），需決策 |
| R9 | 備份策略 | RDS 有 PITR，現行 `make db-backup` 七張表 → NAS。**建議保留 NAS 那條**——跨雲是真正獨立的故障域 |
| R10 | 回退路徑 | 地端 WSL 保留多久、切換後資料如何對回 |

### 6.3 依賴

目標 2 **依賴目標 4**：ECS Fargate 上跑不了 Max 訂閱登入的 `claude` CLI，這正是已核准規格把導入路徑隔離成 Private EC2 Worker 的原因。線上路徑若不先 API 化，Fargate 就沒有可用的 LLM 後端。

## 7. 目標 3：股票代號搜尋

### 7.1 關鍵範圍界定

**只動檢索頁的選篇路徑，完全不碰問答的 tier 契約。**

`app/services/retrieval.py` 的 `rank_reports`（檢索頁，唯一生產消費端是 `web/routers/search.py`）與 `app/services/answer.py` 的 `select_reports`（問答／研報）是兩條互不共用的選篇。而 `TIER_SEMANTIC` / `TIER_ALL_TERMS` / `TIER_PHRASE` 是**問答與研報共用的契約**（`select_reports` 以 `TIER_ALL_TERMS 以上一律放行` 消費它）。

因此標的優先排序加在 `rank_reports` 那一層。**若改採新增 `TIER_INSTRUMENT = 3` 的做法，會同時改變問答的選篇行為**——那是本目標範圍外的副作用。

### 7.2 設計三段

1. **代號偵測**：台股 4 位數字。`app/services/overview.py:215` 已有一條含「排除 `2025年`」的 regex，抽成共用而非另寫一份。
2. **標的路召回**：`WHERE stock_code = :code OR :code = ANY(stock_targets)`。兩個索引均為現成（`db/schema.sql` 的 `idx_rr_stock_code` B-tree、`idx_rr_stock_targets` GIN）。`research.report_signal.instrument_code` 為可選的第三來源。
3. **融合與標示**：標的命中排前、內文提及排後，UI 標示區別。

### 7.3 必要的 fail-open

代號路無結果時**靜默退回純全文**。「2330」也可能是價格或年份，誤判成代號而讓一般搜尋退化是真實風險。

### 7.4 前端契約

`web/routers/search.py` 的 `ReportResult` 新增欄位時，**必須同步補 `frontend/src/lib/schemas.ts:51` 的 `reportResultSchema`**（`searchResponseSchema` 由它組成）。zod 物件預設 `strip`，未宣告的鍵不報錯、直接安靜丟掉——本 repo 已有 `/api/progress` 的 `takeaway`/`signal` 覆蓋率踩過同一個坑。新欄位用 `optional()`，讓滾動部署不會整頁 parse 失敗。

### 7.5 明確不做

- 不做代號 ↔ 名稱互查（D4）。雷達的 `instrument_name` 只涵蓋約 16.4% 研報，findb 整合未實作，建對照表會讓範圍膨脹到需要拆兩期。

## 8. 目標 4：線上路徑改接 API

### 8.1 核心設計決定

**`llm.stream_completion` 的介面一個字都不改**，只換內部實作。所有呼叫端零改動。

### 8.2 呼叫點盤點（10 個生產呼叫點，非 7 個）

線上路徑（本目標範圍內，改走 API）：

| # | 檔案 | 用途 |
|---|---|---|
| 1 | `app/services/answer.py` | 主 RAG ＋ overview |
| 2 | `app/services/report.py` | 研報單次組裝 |
| 3 | `app/services/report_writer.py` | 研報逐節 |
| 4 | `app/services/scope_router.py` | 四類分類 ＋ `condense_and_route`（**注意：`config.py` 註解寫的 `intent.py` 不存在，那些函式在本檔**） |
| 5 | `app/services/query_planner.py` | M5/M6 子查詢規劃 |
| 6 | `app/services/agentic_qa.py` | M5 多輪補查 |
| 7 | `app/services/faithfulness.py` | M8c 忠實度抽查 |
| 8 | `app/services/followups.py` | 追問建議 |

評測 harness（**範圍需明確決策，見 §8.4**）：

| # | 檔案 | 用途 |
|---|---|---|
| 9 | `eval/judge.py` | LLM judge |
| 10 | `eval/run_ragas.py` | RAGAS 評測 |

批次腳本（D3，維持 CLI）：`tag_all_cli.py`、`generate_summaries.py`、`generate_titles.py`、`extract_takeaways.py`、`extract_signals.py`、`sync_new_reports.py`、`generate_brief.py`。這七支不經 `llm.py`，各自 spawn CLI 並受 `scripts/_claude_lock.py` 的 flock 互斥——**該互斥範圍不變，`llm.py` 刻意不在鎖內（有靜態測試反向釘死），API 化不違反它**。

### 8.3 四個容易漏的地方

1. **WebSearch 的事件形狀變了。** CLI 走 `--allowedTools WebSearch`（`app/services/llm.py:167-168`）＋ `content_block_start` 內 `tool_use name=WebSearch` 偵測（`llm.py:131-150`）；API 側是 server tool，`SEARCH_EVENT` 控制標記的觸發點要重寫。`ASK_ENABLE_WEB` 與 `REPORT_ENABLE_WEB` **預設皆為 1**，這是生產預設路徑而非邊角。
2. **CLI 專屬的錯誤 heuristic 不能直接刪。** `looks_like_api_error` / `result_line_info` / `extract_assistant_text` 存在是因為 CLI 把 API 錯誤當回答文字輸出（`llm.py:74-128`）。API 有真的 exception type，但重試語意（只對 529 重試、timeout 快速失敗）要逐一對照過去，且既有測試依賴這些函式。
3. **模型別名 ≠ API model ID。** `claude-haiku-4-5` 在 API 側是 `claude-haiku-4-5-20251001`。
4. **CLI 專屬的隔離措施 API 化後全部不需要**：`--setting-sources ''`、`cwd="/tmp"`、`--system-prompt` 取代預設提示、`_STDOUT_LINE_LIMIT = 16MB`。移除時要確認沒有其他行為依賴它們。

### 8.4 品質對比的陷阱（D6 使這一條變關鍵）

`eval/judge.py` 與 `eval/run_ragas.py` **也呼叫 `stream_completion`**。若在改 API 前後各跑一次 baseline，第二次的 judge 也跟著變成 API——**同時改變了「受測系統」與「量尺」**。

危險在於 `eval-compare` **不會**回報 `2`（不可比）：樣本數與 ruleset 都沒變，它會給出 `0` 或 `1`，而那個結論是錯的。

**因此對比期間 judge 的 LLM 後端必須釘死。** 具體做法（實作計畫決定選哪個）：

- 讓 judge 的後端獨立於受測路徑，以明確旗標指定；或
- 分兩步：先只改 §8.2 的 1–8，judge 保持 CLI，量完之後再考慮 9–10。

### 8.5 新能力：成本可觀測性

API 回 usage（input / output / cache tokens），應落 `qa_log`。現況完全沒有成本紀錄，而上雲後那會是唯一能回答「一題多少錢」的地方。這是新增能力而非遷移，可視為獨立小項。

### 8.6 品質驗證要求（D6）

CLI 的 `-p` headless 與 API messages 不等價（CLI 有自身的 system prompt 疊加與 tool loop），**答案會變**。必須用 `eval/` 前後各跑一次並以 `make eval-compare` 判定，不得憑感覺宣稱等價。前提是 §4.2 的 baseline 已重跑。

### 8.7 前置條件

**一把 `ANTHROPIC_API_KEY`，且與 Claude Max 20x 訂閱是兩套獨立計費**——Max 訂閱不含 API 額度。使用者已確認可另外建立。新旋鈕加在 `app/config.py`，不在服務模組寫 `os.getenv`。

## 9. 依賴關係與建議順序

```
目標 3（股票代號搜尋）── 完全獨立，可隨時做
                          │
目標 4（線上改 API）──────┼── 目標 1 的加速項之一
     │                    │
     ├── 目標 1（加速）── 品質敏感項需 baseline 才能定價
     │
     └── 目標 2（整套上雲）── 依賴目標 4（Fargate 跑不了 CLI）
```

建議順序：

```
0.  基準量測（不動程式碼，可並行）
    ├─ 重跑品質 baseline（RAGAS ＋ 研報）      ← D6 使這條成為所有後續的前提
    ├─ 撈 qa_log 的 thinking_ms / latency_ms 分佈
    └─ 量 stock_targets 覆蓋率（目標 3 用）
    │
1.  零品質風險的先做
    ├─ rerank 期間補進度事件（體感）
    └─ 目標 3 股票代號搜尋（只動檢索頁）
    │
2.  逐項用 eval-compare 定價（每項獨立量：省幾秒 / 掉幾分）
    ├─ reranker 介面抽象 ＋ ONNX int8
    ├─ rerank 候選數調整
    └─ 目標 4 線上改 API      ← 同時是上雲前提，不純是加速
    │
3.  目標 2 整套上雲
    （GPU 分支去留由 O1 的目標流量與第 2 步的定價結果共同決定）
```

第 0 步與第 1 步之間唯一的耦合：**第 1 步的兩項都是零品質風險，所以不必等 baseline**。第 2 步的每一項都必須等。

## 10. 跨項風險

| # | 風險 | 影響範圍 |
|---|---|---|
| C1 | **品質 baseline 若不先重跑，第 2 步的每一項都無法定價**，`eval-compare` 會回 `2`（不可比） | 目標 1、4 |
| C2 | **judge 與受測系統同時切換 API 會產生「看起來有效、實際無意義」的對比**，且不會被 `2` 攔下 | 目標 4 → 目標 1 |
| C3 | R3（`_ASK_GATE` per-process 語意）在 ECS 上靜默失效，而併發上限同時是 DB 連線與 rerank semaphore 的上游 | 目標 2 → 目標 1 |
| C4 | `context_precision` 自 M0 未達門檻（0.679 vs 0.8），PR #140 預期再壓低且未量。**D6 說品質是約束不是目標，所以本路線圖不處理它**，但重跑 baseline 時會看到這個數字——**那不是本次改動造成的** | 全部 |
| C5 | 共用工作目錄：本 tree 可能有他人未提交的 WIP。一律 `git add <明確路徑>`，禁用 `git add -A`／`.` | 全部 |

## 11. 驗收準則

本路線圖的交付物是「四份可獨立執行的實作計畫 ＋ 一組已量測的基準數字」，不是程式碼。判定完成的條件：

1. §4 的三組基準數字全部取得並記錄。
2. O1、O2 兩項未解決決策，各自取得決策所需的數字（目標流量、GPU benchmark），或明確記錄為「延後至觸發條件成立」。
3. 四項各有一份 spec 與實作計畫。
4. 每一項的實作計畫都標明其品質驗證方式（D6 要求）。

## 12. 明確不做的事項

- 不在本文件內寫任何實作步驟。
- 不在取得 §4.2 baseline 之前調整品質敏感旋鈕。
- 不做代號 ↔ 名稱對照表（D4）。
- 不把批次腳本改接 API（D3）。
- 不在本地安裝顯卡、不使用 Intel 內顯（D5、§4.4）。
- 不處理 `context_precision` 未達門檻的問題（D6：品質是約束不是目標）。
- 不更動 `eval/` 的門檻值——那是政策決定。
