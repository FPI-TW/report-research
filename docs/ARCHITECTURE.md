# 架構：模組地圖與不變量

本檔是 `AGENTS.md` 架構不變量摘要的完整版：每個模組負責什麼、誰 import 誰、哪些設計是「刻意」的以及出處。它從程式碼讀出（2026-09-18，2026-09-30 依現況全面複核），是 `tests/test_docs_contract.py` 掃描的 living doc；改名或刪檔要同步改這裡。

## 1. 分工鐵律

Python 做所有決定性的事：解析、抽取、切塊、嵌入、儲存、檢索、錨定、聚合、窗期。LLM（DeepSeek）只做語意：標註、標題、摘要、摘錄、訊號擷取、每日簡報、問答（含意圖分類、改寫、子查詢分解）、追問、忠實度評審。唯一例外是網搜：`ASK_WEB_MODEL` 仍解析到 Claude，生產以 `ASK_ENABLE_WEB=0` 暫停。每個管線階段以檔案 SHA256 `file_hash` 為鍵、可斷點續跑。派生功能（rerank、忠實度、追問、摘錄、agentic 補查）一律 fail-open 降級，不阻斷主流程。

深度研報生成（含四張 `report_doc`／`report_run`／`report_section`／`report_rendition` 表與所有 `REPORT_*` 旋鈕）已於 2026-09 整個移除，本檔不再描述；既有庫要手動跑 `db/drop_deep_report_tables.sql`，`REPORT_FAITHFULNESS_MIN` 舊名仍可讀（見 §9）。

## 2. 模組地圖

### 2.1 `app/`

| 檔案 | 責任 |
|---|---|
| `app/config.py` | 集中讀環境變數為 frozen dataclass `Settings`（`get_settings()`）。驗證器對 typo 不靜默：`LOG_LEVEL`、`EXTRACTOR`、`OBJECT_STORAGE_MODE` 打錯會警告退回預設或拒絕啟動 |
| `app/logging_setup.py` | dictConfig 宣告 root logger；刻意不宣告 uvicorn 的三個 logger。只在 `web/server.py` 初始化，順序由 `tests/test_logging_setup.py` 釘住；批次腳本的 `logger.info` 無聲 |
| `app/request_context.py` | HTTP 請求的日誌關聯 id（`contextvars`；`create_task`／`to_thread` 自動沿用）。handler 上的 `RequestIdFilter` 把它蓋到每筆 record，日誌行以 `rid=` 呈現；批次與啟動期為 `-`。**不是** `qa_log.request_id`（那是前端冪等鍵） |

### 2.2 `app/services/` 檢索與問答

| 檔案 | 責任 |
|---|---|
| `retrieval.py` | `hybrid_search`（dense ＋ lexical 融合）、`rank_reports`（檢索頁分頁排序）、tier 常數 |
| `retrieval_pipeline.py` | 問答的**唯一檢索入口** `retrieve_context`：embed → `hybrid_search` → rerank（fail-open）→ `build_context` |
| `rerank.py` | BGE cross-encoder（`BAAI/bge-reranker-v2-m3`）；載入失敗熔斷、逾時或形狀不符回原序 |
| `embed.py` | BGE-M3 dense 1024 維單例；建構鎖與推論閘分開 |
| `rows.py` | `ChunkRow` NamedTuple，欄序與 `store._meta_columns` 逐欄對齊，取欄位用 `_fields.index()` |
| `store.py` | 入庫 upsert、檢索 SQL、`extraction_log`、`reanchor_takeaways` |
| `chunk.py` | 段落邊界切塊，600 字元、80 重疊 |
| `textnorm.py` | `clean_text`（折換行，檢索片段用）、`clean_extracted`（顯示用）、`norm_for_match`（與 DB 生成欄 `content_norm` 逐字等價） |
| `answer.py` | RAG 問答主編排、`select_reports`、`build_context`、`qa_log` 落庫、對話與版本管理 |
| `agentic_qa.py` | 多輪「評估 → 補查」迴圈；頂層只准 import 葉模組 |
| `scope_router.py` | 五類路由：`off_topic`、`overview`、`corpus_qa`、`time_sensitive`、`advice_risk`；確定性 `resolve_overview_route`（零 LLM）→ `precheck_route` 詞表 → LLM `classify_non_overview`（非 overview 四類）；續問改寫兼分類 `condense_and_route`；fail-open 落點 `corpus_qa` |
| `overview.py` | 枚舉／聚合題的全語料分面統計，零 LLM |
| `query_planner.py` | 子查詢分解（`qa` profile；profile 機制保留，研報 profile 已隨功能移除）；禁止 import `answer`／`retrieval_pipeline` |
| `faithfulness.py` | 主張拆解與 grounding；`is_numeric_claim` 是問答抽查的唯一閘門 |
| `evidence.py` | 證據帳本：引用存在不等於真實性 |
| `followups.py` | LLM 產至多三條追問，fail-open 回空 |
| `stream_sentinel.py` | `[EXT_SOURCES]` 跨 chunk 偵測狀態機 |
| `locale.py` | `zh-Hant`／`en`，未知一律回中文；預設 locale 不改動任何既有 prompt |
| `zh_hant.py` | 簡→繁（s2tw）；判別用 Big5 可編碼性，門檻「至少 2 字且密度 5%」缺一不可 |
| `trusted_market_data.py` | 受信任時效資料 provider 契約；任何失敗收斂為 `TrustedDataUnavailable` |
| `accounts.py` | 個別帳號、可撤銷 session、管理稽核的全部 SQL（`app_user`／`user_session`／`admin_audit_log`）。`resolve_session` 每個請求都查、刻意不快取（停用與撤銷要立即生效）；最後一位管理員、「不能鎖死自己」、最後一位 super admin、「只有 super admin 能管 super admin 與授予 scope」都在這一層（CLI 也受約束）；scope 詞彙（`effective_scopes`）、權限提升（`elevate_session`）與稽核雜湊鏈驗證（`verify_audit_chain`）也在這裡；稽核與變更同一筆交易。web 層只經 `web.deps.accounts` 呼叫，測試以 `tests/fake_accounts.py` 整組替換，兩者語意由 `tests/test_accounts_db.py` 的同一組情境對照 |
| `passwords.py` | Argon2id 雜湊／驗證／`needs_rehash`（argon2-cffi 預設參數，登入成功時就地升級）、密碼政策（10–256 字元、前後不可空白）、`burn_verify`（帳號不存在時也算一次，回應時間不洩漏帳號清單） |
| `llm.py` | `stream_completion` **依白名單分派**：`llm_models.is_http_model(model)` 走 `llm_http.astream_chat`（DeepSeek），其餘走 CLI；`allow_web` 配白名單 model 直接拋 `kind=config`（DeepSeek 網搜延後到 P9）。HTTP 路徑：只有 overloaded／network 且尚未吐字才重試（Retry-After 優先、上限 10 秒，否則 1.5／3 秒）；非預期例外一律包成 `LLMUnavailableError`（`kind`、`partial`），`CancelledError` 原樣上拋並經 `aclosing` 關閉回應；已吐字後內容審查拋 `partial=True`，`length`／read 逾時／斷線／總時限（`LLM_HTTP_TOTAL_TIMEOUT`，預設 600 秒，每個 await 各包 `timeout_at`）正常結束並寫 `meta["truncated_reason"]`；首字前伺服器 60 秒沉默（httpx read 逾時）歸 `timeout`、不重試。`timeout` 在兩條路徑經 `_with_heartbeat` 驅動時實際都是**首字期限**（CLI 的 `asyncio.timeout` 跨越 yield、綁在已結束的 Task 上；HTTP 刻意每個 await 各包 `timeout_at`、不跨 yield），只有單一 Task 收齊的呼叫端 CLI 才是總時限。**CLI 已於 2026-09-23 放棄、無可用後端**，現在只有 `ASK_WEB_MODEL`（網搜，暫停中）會解析到它，程式碼留到 PR-M 移除。CLI 路徑：以 `claude -p --setting-sources '' --output-format stream-json --verbose --include-partial-messages` spawn CLI，`cwd=/tmp`，prompt 走 stdin；開網搜加 `--tools WebSearch --allowedTools WebSearch`（`--tools` 把可用工具縮到只剩 WebSearch，`--allowedTools` 只管免核可），不開網搜加 `--tools ""` 不開任何工具（`--help` 寫明；批次的 `scripts/_claude_cli.py` 與 `scripts/generate_brief.py` 同樣；刻意不用 `--disallowedTools "*"`，萬用字元語意未記載）；三處一律加 `--strict-mcp-config` 且不帶 `--mcp-config`＝不載任何 MCP 伺服器（`--tools` 只管內建工具，`--setting-sources ''` 只擋設定檔來源）；工具旗標是可變長度選項，一律放 argv 最後，布林旗標放在它們之前；`system/init` 事件回報的工具集與預期不符時記 WARNING（`check_init_tools`，fail-open）；只對 API 錯誤重試，逾時對已串流文字 fail-open（實務上只發生在單一 Task 收齊的呼叫端） |
| `llm_models.py` | 模型名稱的單一真相來源：白名單 `HTTP_MODELS`／`is_http_model`、各 provider 預設表（`DEEPSEEK_DEFAULTS` 等）、`resolve_model(task)`（空字串視同未設）。葉模組，只 import 標準函式庫（`tests/test_llm_models.py` 釘住） |
| `llm_http.py` | DeepSeek Chat Completions 的 HTTP 客戶端（`astream_chat`、`complete_json`、`fetch_balance`）；`max_tokens` 由呼叫端逐點給；錯誤依狀態碼分類（`classify_status`）。葉模組（`tests/test_llm_http.py` 釘住） |
| `llm_health.py` | `/healthz/llm` 的判定：DeepSeek 餘額、金鑰、連線，回狀態字串、不回金額 |
| `llm_failures.py` | LLM 批次的內容型失敗紀錄（`research.llm_task_failure`）與跳過規則：`should_skip` 與 `skip_clause_sql` 必須等價；環境型失敗刻意不記 |
| `judge_schema.py` | judge 量尺：回應的 schema 驗證、讀分數的 `CURRENT_JUDGE_SQL`／`JUDGE_MODEL_SQL`／`is_current_judge`；`LEGACY_JUDGE_MODEL` 固定為 `claude-haiku-4-5`，不跟著生產預設改。刻意是葉模組 |
| `citation_filter.py` | 引用過濾：串流用 `CitationStreamFilter`，落庫與評測用 `filter_unknown_citations`／`count_unknown_citations`；不存在的 `[n]` 換成「（無效引用）」 |

### 2.3 `app/services/` 閱讀頁、雷達、簡報、標註與基礎設施（讀取路徑零 LLM）

| 目錄／檔案 | 責任 |
|---|---|
| `reading/anchor.py`、`reading/queries.py`、`reading/schemas.py` | 閱讀頁：正典文字、引文錨定、相似研報 |
| `radar/compute.py`、`radar/queries.py`、`radar/scale.py`、`radar/schemas.py`、`radar/types.py` | 觀點雷達：純 SQL 取訊號 ＋ Python 決定性聚合 |
| `signal_extract.py` | 訊號擷取 prompt 與正規化（LLM 只擷取數值與論點證據，Python 判評等方向、幣別、狀態） |
| `brief.py` | 每日簡報素材、prompt、落庫 |
| `tagging.py` | 市場代碼（對齊 findb）、商品類型、期貨標的詞表、標註 prompt |
| `filename.py` | 檔名解析：券商代碼、日期、行政文件判定 |
| `db.py` | async engine、`SessionFactory`、`relax_statement_timeout`、pgvector 版本守門 |
| `object_storage.py` | R2 物件儲存（`local`／`hybrid`／`r2`），全同步方法，FastAPI 端要 `asyncio.to_thread` |
| `extract.py`、`extraction/` | 抽取層，見 `docs/EXTRACTION.md` |
| `boilerplate.py` | 跨文件樣板字典（由 `make boilerplate` 建）與 `strip_boilerplate`：入庫切塊前剔除，`full_text` 不動；全部 fail-open（見 `docs/EXTRACTION.md`） |

### 2.4 `web/`

| 檔案 | 責任 |
|---|---|
| `web/server.py` | 組合層：載環境檔、初始化 logging、auth middleware、lifespan、掛 router |
| `web/routers/` | 13 支 router：`ask`、`search`、`qa_history`、`monitor`、`radar`、`reading`、`report_file`（研報原檔 `/full`／`/file`）、`health`、`auth_pages`（登入、登出、`/api/me`）、`spa`、`brief`、`review`（忠實度低分／倒讚／抽取 `needs_review` 的個體清單與人工處理紀錄，限管理員）、`admin`（帳號管理與稽核，限管理員）。全部 `APIRouter()` 不帶 prefix（`tests/test_docs_contract.py` 靠這個抓完整路徑） |
| `web/deps.py` | 跨 router 共用符號與測試 patch 的單一位置；`_sse`、心跳 |
| `web/auth.py` | session cookie 的簽章與驗證（只帶 session id）、失敗追蹤、可信代理。帳號與 session 狀態不在這裡，在 `app/services/accounts.py` |
| `web/authz.py` | `current_user`／`require_admin`／`require_scope`／`require_super`／`require_elevated`（FastAPI dependency）：唯一的授權判斷點；前端 route guard 只是顯示層 |
| `web/csrf.py` | 會改變狀態的請求只收本站來的（`Origin` 等於 `Host`；沒有 `Origin` 看 `Sec-Fetch-Site`）；middleware 在認證之外 |
| `web/errors.py` | 統一錯誤格式 `{detail, code, request_id}`：`AppError`、exception handler、middleware 用的 `error_response` |
| `web/concurrency.py` | `ConcurrencyGate`（刻意不支援 `async with`）、單 worker 偵測 |
| `web/request_log.py` | 純 ASGI middleware（最外層）：設關聯 id、回應帶 `X-Request-Id`（上游給的只在形狀安全時沿用）、`/api/*` 每請求記一行 `status`／`elapsed_ms`；`/healthz`、`/api/progress` 正常時不記，變慢或 5xx 照記。不記 query string |
| `web/ttl_cache.py` | 有上限、依 key 分格的單行程 TTL 快取；`reset_all()` 由 `tests/conftest.py` 每題清空。目前用在雷達目錄回應 |
| `web/dev_mode.py` | `DEV_NO_AUTH` 三條件放行 |
| `web/env_loader.py` | 讀 repo 根 `.env`，不做 shell 展開 |

### 2.5 `frontend/src/`

React 19 ＋ TypeScript ＋ Vite，`basename` 為 `/app`。`features/` 依頁面分：`search`、`ask`、`monitor`、`radar`、`report`（閱讀頁，含 `report/pdf` 的 EmbedPDF 檢視器）、`brief`、`help`、`admin`（管理後台：帳號管理、待複核、操作紀錄，`/app/admin/*`）。`lib/` 放 API 邊界（zod schema、SSE 讀取、reducer、hooks）。`components/shell/`（外框、側欄、行動版分頁列）與 `components/primitives/`（Modal、Popover 等）是自製元件；`components/animate-ui/` 及其依賴的 `hooks/use-controlled-state.tsx`、`hooks/use-is-in-view.tsx`、`lib/get-strict-context.tsx` 是第三方匯入，不套 lint。管理後台與研報平台**分開**：`App.tsx` 有兩組 layout route——研報平台走 `components/shell/AppShell.tsx`（側欄、歷史對話），`/admin/*` 走 `components/shell/AdminShell.tsx`（自己的頂列與管理導覽、「回到研報平台」、登出；外殼本身 lazy 載入，一般使用者不會下載）。主平台的導覽刻意沒有管理入口，只在帳號選單（`AccountMenu`）對管理員露出「管理後台」。`RequireAdmin` 包在外殼與各管理頁上，非管理員連管理導覽都不畫——這些都只是顯示層，授權一律由後端 `web/authz.py` 判。待複核佇列的 UI 在 `features/admin/ReviewQueuePanel.tsx`（`/app/admin/reviews`；監控頁對一般使用者開放，所以不放那裡）。路由表在 `frontend/src/App.tsx`。

## 3. import 方向

唯一一組刻意的循環：`retrieval_pipeline.py` **頂層** import `answer`（取 `Source` 與 `build_context`）；反方向（`answer` → `retrieval_pipeline`、`answer` → `agentic_qa`、`agentic_qa` → `retrieval_pipeline`）一律函式內 import。`query_planner` 與 `agentic_qa` 的 docstring 有明文 import 約束。

其他函式內 import 是載入成本考量而非循環：`extract.py` 延遲 import pdfplumber 鏈、`object_storage.py` 只在啟用 R2 時 import boto3、`extraction/__init__.py` 刻意不 import 子模組。

## 4. 檢索

- `hybrid_search(session, q, query_embedding, *, k, 篩選…, dense_scan, lex_limit, lex_cap, …)` 回 `[(tier, fused, row)]`，**只以 `(tier, fused)` 排序**。tier 是硬保證：`TIER_PHRASE=2`（整串命中，bonus 0.25）＞ `TIER_ALL_TERMS=1`（至少兩詞且全部命中，0.15）＞ `TIER_SEMANTIC=0`（部分命中，0.05 × 覆蓋率）。`fused = min(0.999, dense_sim + bonus)`。
- lexical 走 `pg_trgm` over 生成欄 `content_norm`；純中文查詢 lexical 空命中時以 `cjk_affix_candidates` ＋ `store.pick_title_lead_term` 重探；挑中後 `phrase`／`terms` 一併換成該詞算 tier（只補召回不補排序等於沒補）。`k` 只在沒給 `dense_scan` 時決定掃描量（`max(120, k×8)`）；問答一律傳 `ASK_DENSE_SCAN`，所以請求的 `k` 與 `ASK_RETRIEVAL_K` 不影響問答的候選數。`distance` 為 NULL 的列跳過並計數，不拋例外。
- 選篇分**兩條互不共用**：檢索頁走 `retrieval.rank_reports`（`relevance`／`date_desc`／`date_asc`，相關度以 `BAND_WIDTH=0.05` 分帶再看日期），消費端 `web/routers/search.py`；問答走 `answer.select_reports`（聚合 → 排序 → 過舊軟截斷 → 相關度下限 → 過舊配額 → 字數預算；相關度下限與過舊配額在湊滿 `ASK_MIN_REPORTS` 篇之後才生效，比的是 rerank 前的 `best_gate`，tier ≥ `TIER_ALL_TERMS` 一律放行。MMR 分支 `_mmr_pick` 目前沒有呼叫端傳 `mmr_lambda`，原消費端已隨深度研報移除，生產恆走一般迴圈）。調問答新近度改 `rank_reports` 沒有作用。
- `retrieval_pipeline.retrieve_context`：短連線做檢索，rerank 前拍 `gate_scores` 快照；rerank 未實際套用時 `gate_scores` 不傳。多子查詢扇出（`retrieve_context_multi`／`merge_scored`）已隨深度研報移除，agentic 補查（M5）逐子查詢各自呼叫 `retrieve_context`。
- rerank 的 fail-open 契約：所有 fail-open 路徑回傳**輸入的同一 list 物件**，成功路徑回新 list；`_rerank_stage` 以 `reranked is not scored` 判 `applied`。逾時用 `asyncio.shield`，結果由 callback 消費。只重排前 `top_m` 筆，以 sigmoid 分覆蓋 fused、tier 保留、尾段保留 fused（所以要拍 `gate_scores` 快照）；並行名額 `REPORT_MARK_RERANK_WORKERS`（3），逾時預算含排隊時間，被放棄的工作靠 deadline 在每 16 筆的批次邊界收手。冷載入實測 44–52 秒，所以 lifespan 暖機。
- `scripts/eval_retrieval.py` 刻意直呼 `hybrid_search`，管線改動它量不到。

## 5. 問答

首輪路由順序（`answer.answer_question`）：

1. `resolve_locale` fail-open 到 zh-Hant；`web_on = bool(web) and ASK_ENABLE_WEB`，**下游只讀 `web_on`**。
2. 續問：`load_recent_turns` ＋ `condense_and_route`（原問題先過前檢，命中即不呼叫 LLM；否則一次 LLM 呼叫改寫兼分類，改寫後依序以 overview → 前檢 → LLM ROUTE → `fail_open` 重判）。首輪：`resolve_overview_route`（確定性，零 LLM 零向量）。
3. `overview` → `_answer_overview`；例外且尚未 yield 則回退 RAG。
4. 首輪 `precheck_route` 詞表：命中 `time_sensitive` **完全不檢索**；命中 `advice_risk` 不短路，只省掉分類器那次 LLM 呼叫；兩類同時命中時 `advice_risk` 優先。
5. 未定案時 `classify_non_overview`（LLM 分非 overview 的四類）與檢索並行、`FIRST_COMPLETED`、誰先到聽誰；路由先回且是 `off_topic`／`time_sensitive` 就取消檢索。
6. `plan_queries` 與第一輪檢索並行建立（首輪一律、續問僅 `corpus_qa`／`advice_risk`）；`run_agentic`（M5，`QA_AGENTIC_ENABLED`）在計畫 degraded 或單一非時效子查詢時走快速路徑，否則依序補查。檢索總次數含第一輪不超過 `QA_PLANNER_MAX_SUBQUERIES`，整段受 `QA_AGENTIC_TIMEOUT` 約束、在文字層合併；逸出例外沿用第一輪結果。
7. 開網搜那一輪主答改用 `ASK_WEB_MODEL`（`task="ask_web"`）；兩張預設表都是 `claude-sonnet-5`、走 CLI，而 CLI 已放棄，這就是網搜暫停的原因。
8. 系統提示：`ask_system_prompt(web_on)` ＋ `advice_risk` 時的 `RESEARCH_ONLY_POLICY` ＋ `output_directive(locale)`。

五類與 `decided_by`（`precheck`／`overview`／`llm`／`fail_open`／`unknown`）全寫進 `qa_log.filters`；`filters.web` 含 False 也要寫（例外：`/api/ask/stop` 的停止列 `filters` 為 `{}`）。有呼叫模型的輪次寫 `filters.llm_model`（實際送出的模型名；總覽退回模板時不寫）；已吐字後被截斷（內容審查、`length`、read 逾時、斷線、HTTP 總時限；CLI 單一 Task 逾時記 `timeout`）時保留已送出的文字、由 Python 追加一行中斷註記（`answer.truncation_note`，時效網搜排在免責句之前），並寫 `filters.llm_truncated`（`content_filter`／`length`／`read_timeout`／`network`／`total_timeout`，CLI 為 `timeout`）。未吐字的失敗：主路徑落 `answer=NULL`＋`filters.llm_error` 的 `active=false` 遙測列（刻意不停用舊版、不截斷後續）；時效網搜退回婉拒文案（落婉拒文字＋`llm_error`）；總覽退回模板、不寫 `llm_error`。`llm_error` 取 `LLMUnavailableError.kind`，CLI 只分 overloaded／other（認證失效填 `auth`）。網搜的逾時只在 `web_on` 時放寬（`ASK_WEB_TIMEOUT`；經 `_with_heartbeat` 驅動時它實際是「第一個輸出（文字或網搜標記）」的期限，之後不設牆鐘上限），免責句由 Python 追加（`WEB_ANSWER_DISCLAIMER`），網搜來源不進 evidence ledger，`drop_abandoned_draft` 只在 `web_on` 時套用。

事件序（services 層 yield 的 kind）：`status`（stage：`understanding`、`retrieved`、`generating`、`reading`、`searching_web`、`evaluating`）、`sources`、`token`、`notice`、`ext_sources`、`followups`、`done`（`cited`、`qa_id`、`conversation_id`、`thinking_ms`、`root_qa_id`、`version_count`，無脈絡路徑只有前四欄；整串收尾改動了內容時（簡→繁、開網搜時的 `drop_abandoned_draft`）多帶 `answer`；婉拒版帶 `notice_kind` 且無 `qa_id`）。web 層補 `queued` 與 `error`。契約在 `tests/fixtures/sse_events.json`。

引用過濾（`app/services/citation_filter.py`）：主答串流先經 `SentinelStreamParser` 再經 `CitationStreamFilter`（跨 chunk 暫扣 `[數字` 尾段），不存在的 `[n]` 換成「（無效引用）」；落庫前以 `count_unknown_citations` 計數寫 `filters.invalid_citation_count`，再以 `filter_unknown_citations` 過濾。`eval/run_ragas.py`、`eval/qa_adversarial.py` 套同一個過濾，評測才量得到使用者實際看到的答案。

忠實度抽查在 `done` 後以 `answer._spawn_background` 跑，只抽 RAG 主路徑（總覽、時效、婉拒、無脈絡都不抽），比對的是 agentic 合併後的 `context`：四閘 `ASK_FAITHFULNESS_ENABLED`、有 `qa_id`、`is_numeric_claim(body)`、抽樣率；in-flight 超過 `ASK_FAITHFULNESS_MAX_INFLIGHT` 就跳過不排隊。結果落 `qa_log.evaluation`，judge 異常標 `degraded` 不加分數（原因記 `degraded_reason`）。judge 依 model 分派：白名單走 `llm_http.complete_json`（非串流、`json_object`、`temperature=0`、thinking 關、`user_id` 生產 `web-faithfulness`／離線 `eval-judge`，各階段 `max_tokens` 拆解 8192、grounding 2048、CP 2048、反推問題 1024），其餘走 CLI。HTTP 路徑的重試只有一層預算：每個階段（一次 `call_validated`）最多 3 個請求（`judge_schema.HTTP_STAGE_MAX_REQUESTS`，adapter 的截斷 2 倍重試／空回應／暫時性重試與 schema 重試共用、不相乘，`judge_json` 的重試在 HTTP 路徑不跑），所以生產一次抽查最壞 6 個請求。審查記 `degraded_reason=content_risk`、401／402／404 記 `account`，都不重試；離線遇到帳號錯誤（judge 或生成端的 auth／quota／config）整批中止（rc=2、不寫結果檔）。`evaluation` 另記 API 回報的 `judge_model_resp`、`judge_fingerprint`（`system_fingerprint`）、`judge_requests`、`usage`。CLI 路徑的最壞次數（6 次 spawn／4 次 judge 呼叫）與細節在 `app/services/faithfulness.py` 模組 docstring。judge 的描述性校準工具是 `scripts/judge_agreement.py`（歷史 haiku 判定當參考、只描述不判定；只取 2026-09-03 起的抽查，haiku 分數排除 `no_source` 重算）。

簡繁守門 `zh_hant.to_traditional` 的寫入點：四支批次（摘要、標題、摘錄、簡報）、`answer.py` 三處（overview 回答、網搜答案、主答案收斂）、`signal_extract.py` 一處（訊號 summary）。串流路徑刻意不中途轉。**逐字引文不轉**：`report_takeaway.quote`、`thesis_dimensions[*].evidence`、`full_text`、`report_chunk.content`。

## 6. 閱讀頁、雷達、簡報

- 閱讀頁：正典文字是 `clean_extracted(full_text)`，`text_sha256` 守不變量；餵給 LLM 的文字、錨點基準、API 回傳三者同源。`reading/anchor.py` 的 `locate_quote`／`locate_chunk`：天真的 `full_text.find(chunk)` 有 99% 機率無聲失敗，正規化並丟開頭 `CHUNK_OVERLAP` 字才到 100%。錨點有效與否只在後端判（驗章 ＋ `READING_TEXT_MAX_CHARS` 截斷）。狀態詞彙刻意與 DB 分離：`text_state`（`ok`／`missing`）、`signals_state`（`available`／`none`，`none` 時前端整區不進 DOM）。`/text` 端點、`anchor.py`、`quote_start`／`quote_end` 是刻意留的可逆性。PDF 選取走 `@embedpdf/plugin-selection`，複製走 `frontend/src/lib/clipboard.ts`（區網 HTTP 沒有 `navigator.clipboard`）。
- 雷達：`report_signal` 一列＝研報 × 標的，擷取只跑高覆蓋子集，空是常態（`coverage_state`：`ok`／`partial`／`pending_extraction`／`window_empty`）；標的完全無研報回 404，有研報未擷取回 200 `pending_extraction`。`/api/radar/instruments` 以 `web/ttl_cache.py` 快取整份回應（256 格，`RADAR_CATALOG_CACHE_TTL`）。共識一律取每家券商窗期內最新有效訊號（`valid`、`partial`），不累積同券商舊報告；共識預覽窗期恆 90 天；`Window` 為 `30`／`90`／`180`／`all`。清單 API 目標價只回方向，payload 不含數值；帶 `stance` 時全量取回在 Python 分頁。`STANCE_CONSTRUCTIVENESS`（`outlook`／`catalyst`：positive 1、negative −1；`valuation`：attractive 1、stretched −1；`risk`：easing 1、rising −1）與 `THESIS_DIMENSIONS` 由 `signal_extract.py` 定義、三處 import；`radar/schemas.py` 的 `Literal` 前端 zod 逐字鏡像；`CatalogSort` 值與 `queries.CATALOG_SORTS` 鍵逐字相同。
- 簡報：窗期用 `research_report.created_at` 不是 `report_date`（實測近 10 天入庫 90 篇有 79 篇 `report_date` 超過一天前）；窗期是上一份的 `window_end` 到現在（上限 `--max-lookback-days` 7 天，停擺超過就有縫；沒有上一份取 24 小時）；來源清單由 Python 記錄不從 markdown 反推；評等變動是與該券商前一次訊號比（全表 `lag()` 後才過濾窗期），且要同時「這輪才擷取」與「報告夠新」（`report_date` 在 `SIGNAL_MAX_REPORT_AGE_DAYS` 內，NULL 一律排除）。`report_brief` 一天一列：`UNIQUE(brief_date)` 擋重複，寫入是 upsert，只有 `--force` 會重寫當日；本地 9 時前、當日已有列、窗期內無研報也無變動（刻意不寫列，稍晚還補得回）、當日同 model 被審查擋過或截斷（`blocked_today`）都不呼叫 LLM。`/api/brief/latest` 沒有簡報時回 200 `status=pending`，指定日期查無才 404。鎖只包那一次 LLM 呼叫。

## 7. Web 層

- `web/server.py` import 順序有三道守門：`dev_mode` 必須在 `load_env_file` 之前（`tests/test_dev_mode.py`）；`configure_logging` 必須在讀 `.env` 之後、任何 `app.services.*` 之前（`tests/test_logging_setup.py`）；`tests/test_env_loading.py` 守 loader 接線。
- Auth deny-by-default、fail-closed：個別帳號（`research.app_user`，Argon2id），舊的 `REPORT_MARK_ACCESS_USERNAME`／`_PASSWORD` 已不讀取（還在環境裡時 lifespan 記 warning；沒有任何啟用中的管理員時記 ERROR、照樣啟動）。`require_login` middleware 先驗 cookie 簽章，再以 `deps.accounts.resolve_session` 查 DB（未撤銷、未過 `expires_at`、帳號啟用），通過才把 `accounts.User` 放進 `request.state.user`；查 DB 失敗回 503（不導回登入頁），簽章有效但 DB 不認的 cookie 會被清掉。授權在 `web/authz.py`：`/api/review/*`、`/api/admin/*` 以 router 層 `require_admin` 整組限管理員，每條路由再宣告 scope（`require_scope`／`require_super`；`tests/test_authz.py` 結構性檢查每條路由）。scope：一般使用者沒有；管理員有 `ADMIN_DEFAULT_SCOPES`，加上 `research.user_scope` 裡另外授予的；super admin 全部都有。調整權限要 super admin 加上近 10 分鐘內在同一個 session 重新驗證過密碼（`POST /api/admin/elevate` → `user_session.elevated_until`）。會改變狀態的請求先經 `web/csrf.py`（跨站 403，排在認證之外）。JSON 錯誤由 `web/errors.py` 統一成 `{detail, code, request_id}`。`/api/admin/*` 的前端 client 由 `scripts/gen_admin_client.py` 從 OpenAPI 產生（`frontend/src/lib/generated/adminApi.ts`）。免登入開發模式的身分是 `accounts.DEV_USER`（管理員、id=None，寫入的問答擁有者為 NULL）。免登入只有 `/login`、`/healthz`、`/app/assets` 與前綴 `/app/assets/`（`/static` 需登入）；POST `/login` 只在 HTTPS（含可信代理的 `X-Forwarded-Proto: https`）或本機時受理，否則 303 `?error=insecure`；`/healthz/storage`、`/healthz/llm` 也在白名單，但只回答本機直連（其餘 404）。`/api/` 未登入回 401 JSON，其餘 302。Session cookie `tf_session`：HMAC token v3 `<ver>.<sid>.<iat>.<exp>.<sig>`（`sid` 是 `user_session.id`，只收正規 UUID 字面），簽章訊息含 `REPORT_MARK_SESSION_EPOCH`，7 天滑動、30 天絕對上限（DB 端 `expires_at` 同值）；v1／v2 舊 cookie 明確拒收並記一筆。撤銷單一 session（登出）、單一帳號（停用、強制登出、重設密碼）在 DB、下一個請求生效；`REPORT_MARK_SESSION_SECRET`、`REPORT_MARK_SESSION_EPOCH` 是全員登出。登入成功記帳號名稱，失敗只記「帳號是否存在」；停用帳號只在密碼正確時回 `?error=disabled`。登入失敗 5 次／300 秒鎖 IP（記憶體內）。外部存取 `from_trusted_proxy = edge_secret_ok OR is_trusted_proxy`，刻意 OR（nginx 與 app 滾動切換窗口）；祕密要 repo 根 `.env` 與 `deploy/.env` 逐字相同。
- `DEV_NO_AUTH=1` 三條件同時成立才放行：旗標在環境檔載入前已在 `os.environ`、對端與 URL hostname 皆 loopback、無任何代理 header；放行時刻意不發 cookie。`SKIP_WARMUP` 在 lifespan 讀 `os.environ`、判 `== "1"`，晚於載入 `.env`，所以寫進 `.env` 會生效（沒有快照保護）。兩者都不要寫進環境檔。
- lifespan：`assert_single_worker`（偵測到多 worker 拒絕啟動，偵測不到放行）→ `dev_mode.log_banner()` 與併發設定日誌 → `assert_pgvector_version`（低於 0.8 fail-closed，連不上 DB 放行交給 `/healthz`）→ LLM 自檢（`_check_llm_models`：WARNING 記 provider 與各任務解析結果；缺 `DEEPSEEK_API_KEY`、未知模型名、網搜解析到白名單模型記 ERROR；解析到 Claude 的任務才探 `llm.claude_cli_path()`——DeepSeek 表的網搜仍是 Claude，所以 PR-M 合併前每次啟動都會探；不擋啟動：讀取類功能不需要 LLM）→ 背景暖機（embed 再 rerank，**必須依序**，兩執行緒同時首次 import transformers 會競態）。關機時取消暖機並 `llm_http.aclose()`。
- 併發閘只剩一個：`/api/ask` 容量 3 寫死在 `web/routers/ask.py` 的 `_ASK_GATE`：超過容量先排隊（SSE 先送 `queued` 事件），排隊人數達 `ASK_MAX_QUEUE`（預設 20，0＝不限）才在送出 200 之前回 429 ＋ `Retry-After: 30`（SSE 開始後改不了狀態碼）。問答忠實度抽查背景任務的上限 `ASK_FAITHFULNESS_MAX_INFLIGHT` 同樣是行程內狀態。都是 per-process，lifespan 擋多 worker。
- SSE：`deps._with_heartbeat` 每 `SSE_HEARTBEAT_INTERVAL`（20 秒）插註解行，因 nginx `proxy_read_timeout` 60 秒；前端 `readSSE.parseFrame` 對無 `data:` 的框回 null。
- `/healthz` 只探 DB（`SELECT 1`，3 秒逾時，結果快取 5 秒），503 而非 200 加 degraded 欄位。存在理由：讓外部監控分辨「DB 掛了」與「站台正常」（個別帳號上線後登入與每個請求的 session 查驗都要 DB，DB 掛掉時其餘路徑一律 503 或登入失敗）。它必須同時在路由與白名單，只掛路由等於永遠 302。回應只有 `status` 一個鍵是釘死的不變量。消費端有兩個：本機 `scripts/check_web_health.sh`，以及從本機打對外網址、再以本機 origin 做失敗歸因的 `scripts/check_edge_health.sh`（細節見 `docs/EXTERNAL_ACCESS.md`）。
- `/healthz/storage` 回報物件儲存可達性（`disabled`／`unknown`／`ok`／`degraded`，只有 degraded 回 503）。刻意不併進 `/healthz`：R2 掛掉時其餘功能都活著，對外監控不該因此判站台死亡。它在 auth 白名單裡，但 handler 只回答本機直連（`dev_mode.is_direct_loopback`），經邊緣一律 404。探測是 `ObjectStorage.ping()`（`list_objects_v2` `MaxKeys=1`），成功快取 5 分鐘、失敗 60 秒、連續兩次失敗才翻 degraded。消費端是 `scripts/check_web_health.sh` 退出碼 6。
- `/healthz/llm` 回報 DeepSeek 帳號（`app/services/llm_health.py`）：同樣在白名單、只回答本機直連、回應只有 `llm` 一鍵且不含金額。查 `GET /user/balance`，只讀 `LLM_BUDGET_CURRENCY`（CNY）那一筆、依 `currency` 取值不靠索引；低於 `LLM_BALANCE_FLOOR`（70）為 low，缺該幣別或其他幣別非零為 indeterminate。本行程真實請求的 402（`llm_http.last_quota_at`）立刻翻 exhausted，直到一次之後開始的成功查詢。只有問答主答（`ASK_ANSWER` 任務）解析到 DeepSeek 時才回 503（審查 M15；其他線上任務都 fail-open，不算），否則加 `_unused` 回 200。消費端是探針：`low` 為退出碼 7（WARNING），其餘 503 為 8（CRITICAL，停擺或判斷不出來）；探針三項 L3 全查、一行帶出全部 reason，退出碼取 8 → 5 → 6 → 7 最前面的（8 是最重的故障；7 可能持續到儲值，不能遮蔽 5、6）。狀態只在行程記憶體裡，web 重啟時可能閃一次 RESOLVED（`llm_health` docstring）。
- `web/routers/monitor.py`：`/api/stats` 與 `/api/progress` 共用 15 秒 DB 快取（同模組是刻意的，拆開就分裂成兩份）；runtime 區塊 10 秒；`data/tags/` 檔數 60 秒（實測 15,852 檔冷 412 ms，是真正的熱點）。router 檔的輔助函式一律放在所有 `@router.*` 之上，夾在中間會讓端點回 422，只有 HTTP 層測試抓得到。
- SPA：`/app/assets` 的 Mount 必須贏過 `/app/{spa_path:path}`（`tests/test_pre_split_guards.py`）；`frontend/dist` 不存在回 503；`tests/test_spa_serving.py` 對真 build 驗證，缺 dist 是紅不是 skip（`SKIP_SPA_TESTS=1` 才跳過）。Mount 只在啟動時 `frontend/dist` 底下的 assets 目錄已存在才掛；先起 web 再首次 `make build-web`，資產請求會落進 catch-all 拿到 index.html，要重啟。
- 待複核（`web/routers/review.py`，`/api/review/queue`、`/api/review/{kind}/{subject_id}`，整組限管理員）：佇列每次從原始品質條件重算，再 LEFT JOIN `review_state`（沒有列一律視為 `open`）。PUT 只檢查主體還在不在 `qa_log`／`research_report`，不檢查它是否仍在佇列裡；寫入是 upsert，`note` 會 strip，記下處理人並在同一筆交易寫 `admin_audit_log`（`review.update`，不含註記全文）；每筆帶 `reviewer` 與問答類的 `asked_by`，以純量子查詢取帳號名（JOIN `app_user` 會讓既有未加表名的欄位 ambiguous）。`review_state` 不設 FK，所以有兩種孤兒：刪問答歷史或對話串是對 `qa_log` 硬 DELETE；重跑入庫（`upsert_report`）先 DELETE 再以新 uuid 重建，舊的抽取複核狀態留成孤兒、該篇重新以 `open` 出現（E1d 回填走 `replace_report_extraction` 原地更新，id 不變、狀態保留）。
- 問答歷史（`web/routers/qa_history.py`）：刪除是硬刪，查無或 DB 異常回 200 `{"ok": false}` 不是 404；DELETE 另有 POST 的 `/delete` 別名（邊緣對 DELETE 不穩時用）；`/api/conversations` 回裸陣列、不帶 total，前端以筆數等於 `limit` 判斷有沒有下一頁；回饋送 `none` 寫成 NULL。

## 8. 資料層

schema 名 `research`，由 Alembic 管理（`alembic.ini`、`db/migrations/`；守門與 baseline 規則在 `app/services/schema_migrations.py`）。`db/schema.sql` 是凍結的 baseline（revision 0001，SHA-256 釘住），只接受空庫；之後的變更一律是手寫 SQL 的 revision。對已有資料的庫做變更要逐字確認目標（`REPORT_MARK_MIGRATE_CONFIRM`），因為本機預設庫就是生產庫。既有庫導入時先以 `scripts/schema_baseline.py` 證明零 drift 才 stamp——`alembic stamp` 本身不檢查庫長什麼樣。導入前的歷史：`CREATE ... IF NOT EXISTS` 對既有庫刪不掉東西、也改不了同名物件的定義，生產因此留著三項索引差異（`db/align_baseline_indexes.sql`）。`db/expected_constraints.txt` 由 `tests/test_schema_constraints.py` 對帳。刪表同理：深度研報的四張表已從 `db/schema.sql` 拿掉，既有庫要手動跑 `db/drop_deep_report_tables.sql`（依相依順序 `DROP TABLE IF EXISTS`）；DROP 之前對生產庫跑約束測試會多出 `report_run`／`report_section` 的兩條 CHECK 而紅，是預期的。

| 表 | 用途 | 關係 |
|---|---|---|
| `research_report` | 研報主檔：檔案、標籤、`full_text`、`summary`、顯示標題三欄（`title`／`title_original`／`title_source`）、抽取七欄、`source_object_key` | `file_hash` UNIQUE；`is_research` NOT NULL DEFAULT true |
| `report_chunk` | 切塊、`embedding vector(1024)`（HNSW cosine）、生成欄 `content_norm`（GIN trgm） | FK → `research_report` CASCADE |
| `qa_log` | 問答紀錄：`filters`、`sources`、`ext_sources`、`stages`、`followups`、`evidence_manifest`、`evaluation`、版本鏈 `root_qa_id`、`request_id` UNIQUE、擁有者 `user_id` | `conversation_id` 軟連結；`user_id` → `app_user.id` 刻意無 FK（備份表不被還原順序綁住），NULL＝個別帳號上線前的共用歷史，一般介面看不到 |
| `report_signal` | 雷達訊號（研報 × 標的） | FK → `research_report` CASCADE；UNIQUE(report_id, market, instrument_code) |
| `report_takeaway` | 閱讀頁重點摘錄與錨點 | FK → `research_report` CASCADE；UNIQUE(report_id, ordinal) |
| `report_brief` | 每日簡報 | UNIQUE(brief_date)；`report_ids uuid[]` 刻意無 FK，讀取端容忍孤兒 |
| `extraction_log` | 每個進過管線的 `file_hash` 一列 | PK `file_hash`；CHECK `stopped_at` 與 `store.STOPPED_AT` 逐字對齊；無 FK |
| `llm_task_failure` | LLM 批次的內容型失敗（跳過名單）：解析不了、審查擋下、截斷、空回應、其他 400／422、已吐字後逾時（`timeout_streamed`，連續 3 輪才跳過）；成功即刪列，規則在 `app/services/llm_failures.py` | PK(file_hash, task)；刻意無 CHECK、不備份 |
| `review_state` | 人工複核的狀態、註記、驗證結果、更新時間、處理人 `reviewer_user_id`；未建列視為 `open` | PK(kind, subject_id)；subject_id 指向 `qa_log.id` 或 `research_report.id`，不設跨表 FK；`reviewer_user_id` 同樣無 FK，NULL＝共用帳號時期 |
| `app_user` | 個別帳號：`username`（`lower()` 唯一）、Argon2id `password_hash`、`role`（CHECK `admin`／`user`）、`enabled`、登入與改密碼時間 | 只停用不刪除（歷史擁有者、處理人、稽核都指向它） |
| `user_session` | 可撤銷 session：`revoked_at`、絕對上限 `expires_at`、`last_seen_at`（超過 5 分鐘才回寫）、`ip`、`user_agent` | FK → `app_user` CASCADE；刻意不備份 |
| `admin_audit_log` | 管理操作稽核：`actor_user_id`（NULL＝CLI）、`action`、`target_type`、`target_id`、`detail` jsonb；不含任何密碼衍生值 | 與變更同交易寫入；無 FK |

待複核 API 以原始品質條件查詢，再以 `review_state` 篩選 `open`／`resolved`／`dismissed`／`all`；寫入狀態不改 `qa_log` 或 `research_report` 的品質訊號。人工驗證欄位只記錄人工結果，不會重跑評測或抽取。

備份涵蓋八張不可重建的表（`qa_log`、`report_takeaway`、`report_signal`、`report_brief`、`review_state`、`app_user`、`admin_audit_log`、`user_scope`）→ NAS；語料層與 `user_session` 刻意不備。

資料陷阱：
- `full_text` 是未清理原始抽取（帶 CJK 字間空白），顯示一律 `clean_extracted`，不是 `clean_text`。
- `report_chunk.content` 不是 `full_text` 子字串，錨定一律經 `reading/anchor.py`。**不要寫批次更新 `report_chunk.content`**，要動只有重跑 `scripts/ingest_all.py`。
- 顯示名稱走 `title`，缺值回退 `file_name`（`frontend/src/lib/displayTitle.ts`）；NULL 是常態。
- DB 一律 `from app.services.db import SessionFactory`，唯一留在 `db.py` 的環境變數讀取是 `REPORT_MARK_DB_URL`（`os.environ.get`）。長查詢用 `relax_statement_timeout()`（`SET LOCAL`，因為池化連線的 `SET` 會流到下一個借用者）。`DB_IDLE_TX_TIMEOUT_MS` 預設 0 是刻意的：sync 在交易內做 LLM 標註（預設 DeepSeek HTTP）與嵌入，中間不 commit。
- SQL bind 參數轉型寫 `CAST(:x AS text[])`，不可寫 `:x::text[]`。標的過濾寫 containment（`@>`）才吃得到 GIN（`tests/test_sql_index_hygiene.py`）。

## 9. 設定旋鈕

`app/config.py`（frozen dataclass ＋ `os.getenv`，非 pydantic-settings）分組：

| 組 | 旋鈕（預設） |
|---|---|
| 問答脈絡 | `ASK_MAX_REPORTS`（15）、`ASK_MAX_PASSAGES`（4）、`ASK_MAX_CONTEXT_CHARS`（20000）、`ASK_RETRIEVAL_K`（15）、`ASK_DENSE_SCAN`（400） |
| 問答選篇 | `ASK_RECENCY_WEIGHT`（0.06）、`ASK_RECENCY_HALF_LIFE_DAYS`（90）、`ASK_RELEVANCE_BAND`（0.10）、`ASK_BAND_EPS`（0.03）、`ASK_FRESH_FACTOR`（0.5）、`ASK_STALE_FACTOR`（0.1）、`ASK_MIN_FRESH_BEFORE_CUTOFF`（2）、`ASK_RELEVANCE_FLOOR`（0.62）、`ASK_MIN_REPORTS`（3）、`ASK_STALE_AGE_DAYS`（180）、`ASK_MAX_STALE_REPORTS`（4） |
| LLM 模型 | `LLM_PROVIDER`（`deepseek`；另有 `claude_cli`、遷移期回退 `claude_only`）與 14 個任務旋鈕 `*_MODEL`，一律經 `app/services/llm_models.py` 的 `resolve_model`：非空的任務旋鈕優先，否則查該 provider 的預設表；空字串視同未設，`LLM_PROVIDER` 未設、空值都當成 `deepseek`；未知值在線上也當成 `deepseek`（記 ERROR），批次與評測的預檢（`scripts/_llm_env.py`）則印原始值 rc=2——`claude_cli` 是讓 LLM 停下來的開關，拼錯不能變成照常計費（所以 `/etc/default/report-mark-llm` 缺檔時批次解析到 DeepSeek、因缺金鑰預檢 rc=2，不會退回 CLI）。DeepSeek 表裡只有網搜刻意仍是 Claude，所以網搜暫停中（生產 `ASK_ENABLE_WEB=0`；前端 `WEB_SEARCH_PAUSED` 隱藏開關、請求一律送 `web=false`，DeepSeek 版網搜完成後恢復）；兩個 judge 自 PR-26/27 起是 `deepseek-flash`（新量尺系譜，門檻數值不變）。`claude_cli` 表與遷移前各呼叫點逐字相同（`tests/test_llm_models.py`；測試以 conftest 強制 `claude_cli` 跑，不打付費 API）。`claude_only` 忽略旋鈕裡的 DeepSeek 白名單名稱。claude CLI 已於 2026-09-23 放棄，`claude_cli`／`claude_only` 仍是合法值但已無可用後端（PR-M 決定去留）。白名單 `HTTP_MODELS` 住在這個只依賴標準函式庫的葉模組（`app/config.py` 與 `llm_http` 都 import 它，避免循環）。啟動自檢依解析結果檢查 claude CLI 路徑、`DEEPSEEK_API_KEY` 有無值、未知模型名，不擋啟動 |
| 問答模型與網搜 | `ASK_ANSWER_MODEL`（查表，`deepseek-flash`；總覽、主答、評測生成）、`ASK_WEB_MODEL`（查表，DeepSeek 表刻意仍是 `claude-sonnet-5`，網搜暫停中）、`ASK_INTENT_MODEL`（查表，預設 `deepseek-flash`）、`ASK_INTENT_TIMEOUT`（20）、`ASK_CONDENSE_MODEL`（查表，不再跟隨 intent）、`ASK_CONDENSE_TIMEOUT`（20）、`ASK_ENABLE_WEB`（1）、`ASK_WEB_TIMEOUT`（240） |
| M5／M6 規劃 | `QA_PLANNER_MODEL`（查表，不再跟隨 intent）、`QA_PLANNER_TIMEOUT`（45；依 CLI 時代冷啟動 ttft 約 10 秒量出，DeepSeek 下未重量）、`QA_PLANNER_MAX_SUBQUERIES`（3）、`QA_MAX_ROUNDS`（2）、`QA_AGENTIC_ENABLED`（1）、`QA_AGENTIC_TIMEOUT`（90）、`QA_SUBQUERY_MAX_REPORTS`（5） |
| rerank | `ASK_RERANK_ENABLED`（1）、`ASK_RERANK_CANDIDATES`（16；2026-09-29 凍結快照實測 50 候選三併發易逾時，見 `docs/CAPACITY.md`）、`ASK_RERANK_TIMEOUT`（60）、`RERANK_MODEL`（`BAAI/bge-reranker-v2-m3`） |
| DeepSeek 傳輸與餘額 | `LLM_HTTP_TOTAL_TIMEOUT`（600，串流牆鐘總時限；非正數退回預設）、`LLM_BUDGET_CURRENCY`（CNY）、`LLM_BALANCE_FLOOR`（70；只有 `/healthz/llm` 讀，低於門檻為 low → 探針退出碼 7） |
| 忠實度 M8 | `ASK_FAITHFULNESS_ENABLED`（1）、`FAITHFULNESS_MIN`（0.9；讀不到時退回舊名 `REPORT_FAITHFULNESS_MIN`，讀者是監控頁 `_FAITHFULNESS_MIN`、待複核佇列 `web/routers/review.py`、`scripts/eval_faithfulness.py` 與 `scripts/judge_agreement.py`）、`ASK_FAITHFULNESS_SAMPLE_RATE`（1.0）、`FAITHFULNESS_MODEL`（deepseek 表 `deepseek-flash`、`claude_cli` 表 `claude-haiku-4-5`，刻意不沿用 `ASK_INTENT_MODEL`：換路由模型不得靜默換尺；讀分數三處只計現行 judge，見 `app/services/judge_schema.py`）、`FAITHFULNESS_TIMEOUT`（60，目前沒有呼叫端）、`ASK_FAITHFULNESS_TIMEOUT`（每次 judge 呼叫的總期限；未設時依 judge 走哪條路：DeepSeek 90，依探測延遲以 max(ceil(3×p99), 60) 估算；Claude CLI 240）、`ASK_FAITHFULNESS_MAX_INFLIGHT`（2） |
| 抽取與儲存 | `EXTRACTOR`（pypdf）、`EXTRACTION_REVIEW_MIN`（0.6）、`EXTRACTION_REVIEW_MIN_COVERAGE`（0.30）、`EXTRACTION_REVIEW_MAX_GARBLED`（0.02）、`OBJECT_STORAGE_MODE`（local）、`R2_ENDPOINT_URL`、`R2_BUCKET`、`R2_ACCESS_KEY_ID`、`R2_SECRET_ACCESS_KEY`、`R2_PRESIGN_TTL_SECONDS`（3600，上限一小時） |
| 雷達 | `RADAR_CATALOG_CACHE_TTL`（60 秒；0 停用）：`/api/radar/instruments` 整份回應依查詢參數快取，`report_signal` 每 3 小時才更新 |
| DB 與嵌入 | `LOG_LEVEL`（INFO）、`DB_POOL_SIZE`（5）、`DB_MAX_OVERFLOW`（15）、`DB_POOL_TIMEOUT`（10）、`DB_POOL_RECYCLE`（1800）、`DB_STATEMENT_TIMEOUT_MS`（60000）、`DB_IDLE_TX_TIMEOUT_MS`（0）、`DB_MAINTENANCE_STATEMENT_TIMEOUT_MS`（0）、`EMBED_MAX_CONCURRENCY`（1）、`EMBED_TORCH_THREADS`（0）、`TRUSTED_DATA_ENABLED`（1） |

既有散在各檔的環境變數讀取 **不要順手搬**（`grep -rnE "os\.(getenv|environ)" app web scripts eval`；只搜 `os.getenv` 會漏掉 `web/auth.py`、`web/dev_mode.py`、`web/server.py`、`db.py` 的 `os.environ`）：`REPORT_MARK_DB_URL`、`DEEPSEEK_API_KEY`／`DEEPSEEK_BASE_URL`／`DEEPSEEK_CONNECT_TIMEOUT`（10；`llm_http.py` 刻意在呼叫時讀：批次不讀 repo 根 `.env`，Settings 是 import 期快取的單例）、`ASK_FOLLOWUP_MODEL`（`followups.py`；與 `eval/judge.py` 的 `EVAL_JUDGE_MODEL` 一樣只把讀到的值交給 `resolve_model(override=…)`）、`ASK_FOLLOWUP_TIMEOUT`（15）、`REPORT_MARK_RERANK_WORKERS`（3）、`REPORT_MARK_RERANK_TIMEOUT`（30）、`ASK_MAX_QUEUE`（20）、`SSE_HEARTBEAT_INTERVAL`（20）、`SKIP_WARMUP`、`DEV_NO_AUTH`、`REPORT_MARK_MAX_TRACKED_FAIL_IPS`（4096）與 auth 那組 `REPORT_MARK_*`。`REPORT_MARK_*` 前綴只給 auth／DB，帶前綴的例外是 live 的不要改名。

DB 連線數算式（`.env.example`）：`worker 數 × (DB_POOL_SIZE + DB_MAX_OVERFLOW) + 批次腳本數 × 2` 要小於 97；現況 1 × 20 + 3 × 2 = 26；有併發閘的路徑只有 `/api/ask`(3)，其餘 17 條留給無閘路徑。加 `--workers`、放寬 `_ASK_GATE` 之前要重算。

## 10. 「刻意」設計索引

動任何標記「刻意」的設計前先讀該模組 docstring。

| 設計 | 出處 |
|---|---|
| 不用 pytest-asyncio、不跑 ruff format、不加 black／mypy | `pyproject.toml`、`tests/test_dev_ergonomics.py` |
| 測試不得寫 repo 根 `.env` | `tests/conftest.py`、`tests/test_env_loading.py` |
| `llm.py` 不取批次 flock | `tests/test_claude_lock.py` |
| `rows.ChunkRow` 新欄位插中段不 append | `app/services/rows.py` |
| `ASK_*` 逾時、`DB_*` 逾時的數字 | `app/config.py` 逐條註解 |
| `zh_hant.py` 的判別法與門檻、`faithfulness.is_numeric_claim`、`reading/queries.py` 的 `_SIMILAR_SQL`、`ASK_RERANK_CANDIDATES` | `zh_hant.py` 模組 docstring、`faithfulness.py` 的 `_NUMERIC_RE` 上方、`reading/queries.py` 的 `_SIMILAR_SQL` 周邊註解、`docs/CAPACITY.md` |
| 抽取層不用 PyMuPDF、不用 LLM 評分、快取不存 bbox | `docs/EXTRACTION.md` |
| session 每個請求查 DB、不快取；帳號只停用不刪除；稽核與變更同交易 | `app/services/accounts.py` 模組 docstring |
| 問答紀錄的舊共用歷史（`user_id` NULL）對一般使用者隱藏 | `db/schema.sql` 的 `qa_log.user_id` 註解 |
| 簡報窗期用 `created_at`、沒有自己的 timer | `app/services/brief.py`、`scripts/sync_new_reports.sh` |
| sync 鏈用 `--hashes-file` 不用 `--since-days`、訊號與標題積壓的 `--limit`（`SYNC_SIGNAL_LIMIT`、`SYNC_TITLE_BACKLOG_LIMIT`）是安全機制 | `scripts/sync_new_reports.sh` |
| `report-mark-sync.timer` 的 `Persistent=false` | `tests/test_sync_timer_persistence.py` |
| `report-mark-health.timer` 不設 `Persistent`、健康探針不用 uv | `deploy/systemd/report-mark-health.timer`、`scripts/check_web_health.sh` |
| `make edge-reload` 是 force-recreate 不是 restart | `Makefile` |
| 評測不進 CI、門檻是政策 | `Makefile`、`eval/run_ragas.py` |
