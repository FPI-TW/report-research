# CLAUDE.md

廷豐智能研報——券商研報平台：PDF/docx 抽字 → Claude 標註 → BGE-M3 嵌入 pgvector → 語意檢索／RAG 問答／觀點雷達／每日簡報／閱讀頁。深度研報生成已於 2026-09 整個移除（既有庫要手動跑 `db/drop_deep_report_tables.sql`）。Repo 目錄是 `report-mark`，GitHub 是 `FPI-TW/report-research`。

- **這不是上層目錄 CLAUDE.md 描述的 FinDB**：這裡沒有 Alembic、沒有 `app/api/`、沒有 `NORMALIZER_MAP`，那份文件的指引不適用。本 repo 只把市場代碼對齊 findb（`TW US HK CN FX WTX MACRO GLOBAL CRYPTO`，對照在 `app/services/tagging.py`，`make align` 零 LLM 重對）。
- 回覆使用者一律繁體中文；不加裝飾性 emoji。
- **分工鐵律**：Python 做所有決定性的事（解析、抽取、切塊、嵌入、儲存、檢索、錨定、聚合、窗期），Claude 只做語意（標註、摘要、問答、訊號擷取）。每個管線階段以檔案 SHA256 `file_hash` 為鍵、可斷點續跑。新功能沿用這個分工，並**重用 `hybrid_search`／`retrieval_pipeline`，不另建檢索**。
- 派生功能（rerank、忠實度、追問、摘錄、agentic 補查）一律 fail-open 降級，不阻斷主流程。

## 指令

```bash
uv sync                              # Python 3.11+；torch 為 CPU-only
make setup                           # 相依 + pgvector 容器 + 套 schema
make serve                           # :8097，無 --reload；Python 改動要重啟
make serve-dev                       # --reload + SKIP_WARMUP=1，只綁 127.0.0.1
make serve-preview                   # 免登入看版面（DEV_NO_AUTH=1），另開 8098
make build-web                       # 前端改動要跑這個才生效

uv run pytest -q                     # 缺 frontend/dist 會紅（不是 skip）
SKIP_SPA_TESTS=1 uv run pytest -q    # 不想先 build 前端時
uv run ruff check .                  # E,F,I；120 字元；刻意不跑 ruff format
cd frontend && npm test              # vitest
cd frontend && npm run typecheck     # tsc --noEmit
cd frontend && npm run lint          # eslint

make summaries / titles / takeaways / signals / brief   # 批次，都 spawn claude CLI、以 flock 互斥
make sync-once / db-backup / freshness / db-audit        # 維運
make llm-blocked                     # LLM 批次跳過名單（唯讀；research.llm_task_failure）
make boilerplate                     # 重建跨文件樣板字典 data/boilerplate/（入庫切塊前剔除；零 LLM）
make up-edge / down-edge / edge-logs / edge-reload       # 對外 nginx + cloudflared
uv run python scripts/extract_all.py                     # 全語料三支：只在初次建庫或補歷史
uv run python scripts/tag_all_cli.py --workers 8
uv run python scripts/ingest_all.py
```

`make help` 列出的 target 含破壞性與陷阱 target（`reset-db`／`clean-data`／`ingest-lowio`）；除非使用者明講，不要跑。任何 TRUNCATE／DROP 前先問。

## 測試與 CI

- CI 四個 job 全為必要檢查（`.github/workflows/ci.yml`）：前端（ESLint＋tsc＋vite build＋vitest）、後端（ruff＋pytest）、schema 契約（pgvector 容器，套兩次驗冪等）、gitleaks。前端 job 把 `frontend/dist` 傳給後端 job，SPA 測試對真 build 驗證。**required check 名稱＝job 的中文 `name`**，分支保護在 GitHub 設定不在 repo；改了 `name` 沒同步改設定，PR 會永遠等一個不回報的 check。
- async 測試一律 `unittest.IsolatedAsyncioTestCase`；**不用 pytest-asyncio**（未安裝、刻意不裝）。
- 測試不連網、不載模型（CI 設 `HF_HUB_OFFLINE=1`）：LLM、嵌入、DB、檔案系統一律用假物件。給函式加參數時同步改假物件簽章——過期的假物件拋 `TypeError` 會被外層 `except` 吞掉，程式靜默走另一條路。
- 端點走 HTTP 層測，不直接呼叫 handler 物件。router 檔的輔助函式一律放在所有 `@router.*` 裝飾器之上；夾在裝飾器與 handler 之間會讓端點回 422，直呼函式的測試看不到。
- dataclass 新欄位放末尾並給預設。`rows.ChunkRow` 與 `store._meta_columns` 是位置對齊的：取欄位用 `ChunkRow._fields.index(...)`，不寫數字。
- **測試絕不可寫 repo 根的真實環境檔**：這台機器 repo root 就是部署目錄，`finally` 擋得住例外、擋不住行程被殺。要驗載入行為餵 `tempfile`。`tests/test_env_loading.py` 與 `tests/conftest.py` 是第二道防線，不是許可證。
- 本機全綠不代表安全：抽取層 CJK 測試（`tests/test_extraction_layout.py` 的 CjkTests，用 weasyprint 渲染中文測試 PDF）缺 CJK 字型時本機 skip，CI 以 `REPORT_MARK_REQUIRE_CJK=1` 封死。
- 加相依會過授權守門 `tests/test_license_guard.py`：帶網路條款的 copyleft（AGPL／SSPL）一律紅，掃已安裝套件的 metadata、`uv.lock` 名稱黑名單與 `frontend/package-lock.json`。紅了是換掉那個相依，不是加豁免。相依更新由 `.github/dependabot.yml` 每週分組開 PR，`torch` 與 `@embedpdf/*` 刻意排除（理由在該檔）。
- 評測（`eval/`）刻意不進 CI。改檢索或生成品質時前後各跑一次、用 `make eval-compare BASE=… CAND=…` 比，**退出碼是結論**：0 無劣化／1 劣化／2 不可比／3 有未分類指標（新指標要在 `METRIC_SPECS` 補方向）。門檻 F>0.9／CP>0.8／AR>0.55 是政策，不擅自改；最新基準線 `eval/baselines/baseline-2026-09-02.json`。

## 改動對照表（改了 A 就要動 B）

| 改了什麼 | 還要做什麼 |
|---|---|
| 任何 Python | `sudo systemctl restart report-mark-web.service` |
| 任何前端 | `make build-web`；`frontend/dist` 不存在時 SPA 回 503 |
| 改檔名、刪檔、加端點 | `tests/test_docs_contract.py` 會紅：**改文件，不放寬 allowlist**。端點路徑逐字寫進 README 與 `docs/WORKFLOW.md` 的 API 表（含參數名與 `:path`） |
| 新增後端 SSE 事件或欄位 | 加進 `tests/fixtures/sse_events.json`（兩側測試會指出另一側缺什麼）；`frontend/src/lib/askSchemas.ts` 的 parser／zod 要宣告（zod 預設 strip，未宣告鍵靜默丟掉；新欄位用 `optional()`） |
| `db/schema.sql` | 沒有 migration 工具，冪等只涵蓋 `ADD COLUMN IF NOT EXISTS`；**改 CHECK 約束在既有庫是 no-op**，要另寫給既有庫的 `ALTER`；刪表同理，要另給一支 DROP 腳本（先例 `db/drop_deep_report_tables.sql`）。`db/expected_constraints.txt` 紅了是這個意思，不是改清單；刻意改約束才用 `REPORT_MARK_WRITE_CONSTRAINTS=1 uv run pytest -q tests/test_schema_constraints.py` 重生 |
| `content_norm` 或 `textnorm.norm_for_match()` | 兩者必須逐字等價（`tests/test_content_norm_equivalence.py`） |
| 新旋鈕 | 放 `app/config.py`（frozen dataclass＋`os.getenv`，非 pydantic-settings）。既有散在各檔的 `os.getenv` **不要順手搬**；找旋鈕時 `grep -rn os.getenv app web`。`REPORT_MARK_*` 前綴只給 auth／DB；既有帶前綴的例外（`REPORT_MARK_RERANK_*`、`REPORT_MARK_MAX_TRACKED_FAIL_IPS`、`REPORT_MARK_ROOT`、`REPORT_MARK_ALERT_WEBHOOK`）是 live 的，不要改名 |
| `deploy/` 任何檔 | `sudo cp` 到 `/etc/systemd/system/` 再 `daemon-reload`；不要只改機器上的副本。`tests/test_deploy_units.py` 守 unit 檔 |
| 問答輸入框新增工具 | `frontend/src/features/ask/ComposerTools.tsx` 的 `useTools()` 陣列；已開啟的工具要在收合狀態外露 |
| 加 `--workers` 或提高併發閘 | 先照 `.env.example` 的算式重算 DB 連線數（每行程上限 `DB_POOL_SIZE`＋`DB_MAX_OVERFLOW`＝20） |
| 改 `zh_hant.py`、`faithfulness.is_numeric_claim`、`_SIMILAR_SQL` | 先讀該檔開頭的實測紀錄／docstring；參數都是量出來的 |

## 架構不變量

### 檢索與問答
- 混合檢索：dense（HNSW 餘弦）＋ lexical（`pg_trgm` over 生成欄 `content_norm`）融合，`hybrid_search` 只以 `(tier, fused)` 排序。之後的選篇分**兩條互不共用**：檢索頁走 `retrieval.rank_reports`（消費端 `web/routers/search.py`），問答走 `answer.select_reports`。調問答新近度改 `rank_reports` 沒有作用。
- `app/services/retrieval_pipeline.py` 的 `retrieve_context` 是問答的唯一檢索入口（embed → `hybrid_search` → rerank fail-open → `build_context`）。`answer.py` 自己不呼叫 `hybrid_search`；**要 patch 檢索請 patch `retrieval_pipeline`**。`scripts/eval_retrieval.py` 刻意直呼 `hybrid_search`，管線改動它量不到。
- **循環依賴是刻意的**：`retrieval_pipeline` 頂層 import `answer`；`answer`／`agentic_qa` 之間任何反向取用一律函式內 import。
- 首輪路由順序刻意：確定性 overview（`overview.py`，零 LLM）→ `precheck_route()` 詞表（命中 `time_sensitive` 完全不檢索）→ Haiku 五類分類（`scope_router.py`）與檢索並行、誰先到聽誰。五類與 `decided_by` 全寫進 `qa_log.filters`；fail-open 落點是 `CORPUS_QA`。
- 網搜每題由使用者決定：`web_on` ＝ 請求的 `web` AND `ASK_ENABLE_WEB`，下游只讀 `web_on`。系統提示與工具授權要一起切（`ask_system_prompt(web)`），逾時只在開網搜時放寬（`ASK_WEB_TIMEOUT`），`qa_log.filters.web` 含 False 也要寫，免責句由 Python 追加（`WEB_ANSWER_DISCLAIMER`），網搜來源不進 evidence ledger。
- 忠實度抽查在 `done` 後跑背景任務（`answer._spawn_background`），有自己的上限 `ASK_FAITHFULNESS_MAX_INFLIGHT`。`faithfulness.is_numeric_claim` 是問答抽查的唯一閘門，漏判是靜默的——寧可多抓不可漏抓。監控頁「待複核」門檻 `FAITHFULNESS_MIN`（0.9；讀不到時退回舊名 `REPORT_FAITHFULNESS_MIN`，生產環境檔可能還設著）。
- `app/services/llm.py` 以 `claude -p --setting-sources '' --output-format stream-json` spawn CLI，開網搜時加 `--allowedTools WebSearch`；只在 API 529 重試；逾時對已串流文字 fail-open。

### 閱讀頁、雷達、簡報（讀取零 LLM）
- 閱讀頁（`app/services/reading/`）：正典文字是 `clean_extracted(full_text)`，`text_sha256` 守不變量；錨點有效與否只在後端判（驗章＋`READING_TEXT_MAX_CHARS` 截斷）。PDF 選取走 `@embedpdf/plugin-selection`，`PagePointerProvider` 要在 `Rotate` 之內；複製走 `frontend/src/lib/clipboard.ts`（區網 HTTP 沒有 `navigator.clipboard`）。`/text` 端點、`anchor.py`、`quote_start`／`quote_end` 是刻意留的可逆性，不要清。
- 雷達（`app/services/radar/`）：`report_signal` 一列＝研報×標的，擷取只跑高覆蓋子集，**空是常態**（有研報未擷取回 200 `pending_extraction`）。清單 API 目標價只回方向、payload 不得含數值；帶 `stance` 時只能全量取回在 Python 分頁；共識預覽窗期恆 90 天。`STANCE_CONSTRUCTIVENESS`／`THESIS_DIMENSIONS` 由 `signal_extract.py` 定義、三處 import，是共用契約；`radar/schemas.py` 的 `Literal` 前端 zod 逐字鏡像。
- 簡報（`app/services/brief.py`）：窗期用 `created_at` 不是 `report_date`；變動要同時「這輪才擷取」且「報告夠新」；來源清單由 Python 記錄不從 markdown 反推。鎖只包那一次 CLI 呼叫。

### 抽取與入庫
- 全語料三支：`scripts/extract_all.py` → `data/extracted/<hash>.json`（per-hash 快取，`app/services/extraction/cache.py`）→ `scripts/tag_all_cli.py` → `data/tags/<hash>.json` → `scripts/ingest_all.py`（gate on `is_research`＋`market`）。生產入庫走 `scripts/sync_new_reports.sh`。
- E1 抽取層：`extract_text` 門面、pdfplumber 版面層（`app/services/extraction/layout.py`），品質只標記不擋；`extraction_log` 每個進過管線的 `file_hash` 一列，`stopped_at` 詞彙與 `store.STOPPED_AT` 逐字對齊。**刻意不用 PyMuPDF**（AGPL，本站對外服務）。
- R2 物件儲存（`app/services/object_storage.py`）：`OBJECT_STORAGE_MODE` local（預設，既有行為不變）／hybrid／r2；非 local 缺任一 `R2_*` 啟動即 fail-closed，憑證在 repo root `.env` 與 `/etc/default/report-mark-sync` 各一份、逐字相同。遷移與對帳走 `scripts/migrate_object_storage.py`、`scripts/reconcile_object_storage.py`（順序見 `docs/WORKFLOW.md`），`file_path` 仍指舊掛載點時先跑 `scripts/repoint_file_paths.py`。**瀏覽器端兩個前提**：bucket 要設 CORS（PDF 檢視器是 `fetch` 跟 302 到 presigned URL，沒設就整頁靜默失敗、伺服器零錯誤）；presign 一律帶 `filename`（key 是 hash，跨來源後 `<a download>` 失效）。`r2` 模式下 `has_file` 只認 key（`original_available`），缺 key 即 503 不回退，所以切換前對帳要全零；切換後由 `report-mark-r2-reconcile.timer` 每週對帳接告警鏈；`/healthz` 不探 R2（回應只有 `status` 一個鍵是釘死的），bucket／憑證失效由只回答本機直連的 `/healthz/storage` 加探針退出碼 6 偵測。

## 資料層陷阱
- `research_report.full_text` 是未清理的原始抽取（帶 CJK 字間空白）；顯示一律 `clean_extracted(full_text)`，不是 `clean_text`（後者折掉換行，只適合檢索片段）。
- `report_chunk.content` 不是 `full_text` 的子字串（overlap merge），錨定一律經 `app/services/reading/anchor.py`。**不要寫批次更新 `report_chunk.content`**（`clean_text` 與 `clean_extracted` 都會破壞段落換行），要動只有重跑 `ingest_all.py`。
- 簡體字守門在 `app/services/zh_hant.py`：四個寫入點過 `to_traditional()`；串流路徑刻意不中途轉，問答在 `done` 帶只在有變動時出現的 `answer` 欄位收斂（`askSchemas.ts`＋`askReducer.ts` 都要接）。**逐字引文（`report_takeaway.quote`、`thesis_dimensions[*].evidence`）一律不轉**——它是錨定基準與 PDFium 搜尋關鍵字。
- 顯示名稱走 `title`，缺值回退 `file_name`（`frontend/src/lib/displayTitle.ts`）；title 漸進補齊，NULL 是常態。
- DB 一律 `from app.services.db import SessionFactory`，不複製預設連線字串（鍵是 `REPORT_MARK_DB_URL`）。長查詢用 `db.relax_statement_timeout()`（`SET LOCAL`）。`DB_IDLE_TX_TIMEOUT_MS` 預設 0 是刻意的：sync 在交易內 spawn CLI 與嵌入。
- logging 只在 `web/server.py` 初始化（`app/logging_setup.py`，順序契約由 `tests/test_logging_setup.py` 釘住）；批次腳本的 `logger.info` 無聲。
- 備份只涵蓋四張不可重建的表（`qa_log`、`report_takeaway`、`report_signal`、`report_brief`）→ NAS；語料層刻意不備。

## Web 與 auth
- `web/server.py` 只是組合層；路由在 `web/routers/`，共用符號經 `web/deps.py`（測試 patch 的單一位置）。
- Auth deny-by-default、fail-closed（缺 `REPORT_MARK_ACCESS_USERNAME`／`_PASSWORD` 不啟動）。免登入白名單 `/login`、`/healthz`、前綴 `/app/assets/`。外部存取需 `REPORT_MARK_EDGE_SECRET` 或 `REPORT_MARK_TRUSTED_PROXY_CIDRS` 任一（刻意 OR，祕密要 repo root 與 `deploy/` 兩份環境檔逐字相同）。Session 7 天滑動、30 天上限；改密碼、`REPORT_MARK_SESSION_SECRET`、`REPORT_MARK_SESSION_EPOCH` 都會全員登出，是預期行為。
- `DEV_NO_AUTH=1` 三條件同時成立才放行（旗標在環境檔載入前已在 `os.environ`、對端 loopback、無代理 header），`web/server.py` 的 import 順序由 `tests/test_dev_mode.py` 釘住。`SKIP_WARMUP` 同樣走 `os.environ` 且判 `== "1"`。兩者都不要寫進環境檔。
- 併發閘只剩一個：`/api/ask` 上限 3 寫死在 `web/routers/ask.py` 的 `_ASK_GATE`（無環境變數；佇列 `ASK_MAX_QUEUE`），是 `web/concurrency.py` 的 `ConcurrencyGate`（刻意不支援 `async with`）。問答忠實度抽查背景任務的上限 `ASK_FAITHFULNESS_MAX_INFLIGHT` 同樣是行程內狀態。上限 per-process，lifespan 擋多 worker。
- `claude` CLI 要在 PATH 上；systemd 靠 `deploy/systemd/report-mark-web.service.d/path.conf`。`llm.py` 刻意不在批次 flock 範圍內（有靜態測試釘住）。

## 生產維運
- 真相來源在 `deploy/`，不是機器上的 `/etc`。sync 鏈（每 3h）：rsync → 增量匯入 → 摘要 → 標題 → 摘錄 → 訊號（限量）→ 簡報 → 標題積壓（限量）。摘要／標題／摘錄吃 `--hashes-file`，**不可改成 `--since-days`**（濾的是 `report_date`，會漏掉近九成）；後三段的 `--limit` 是安全機制不是效能旋鈕。補救走 `scripts/failures_to_delta.py`，不要 `--all-local`。
- 會 spawn `claude -p` 的批次在 main 進入點取 `scripts/_claude_lock.py` 的 flock，撞鎖 rc=75 是「不跑」不是「跑壞」。從 worktree 跑批次不與主 checkout 互斥。
- 健康判定打 `/healthz`（只探 DB），不看 `systemctl is-active`；oneshot 是否跑過用 `scripts/verify_oneshot_ran.sh`，不看 `Result=success`。監控兩層：`scripts/check_web_health.sh` 只回報事實（刻意不用 `uv run`、不 import `app.*`），`scripts/incident_handler.sh` 做去重與 RESOLVED。
- `make freshness` rc 0／1／2／3 分流；`signal` 門檻 0 與語料閘是刻意預設。`make db-audit` 只讀不修，warn 也算失敗。
- `研報自動匯入/` 唯讀。`make ingest-lowio` 會 `fsync=off` 且 SIGKILL 後不還原；處置 `make restore-durability`。
- 夜間回填 `report-mark-backfill.timer`（E1d）跑完後由人手動 disable。

## 慣例
- Commit：Conventional Commits＋繁中 scope（`feat(報告): ...`、`docs(維運): ...`）。`git add <path>`，不用 `-A`／`.`（共用工作樹，他人有 WIP）。
- Python：ruff `E,F,I`、120 字元、`E402` 關（import 前 `sys.path.insert`／載環境檔是刻意的）；**不跑 `ruff format`**、無 black／mypy。
- 前端：React 19＋TypeScript、CSS Modules、TanStack Query、zod 在 API 邊界；`_` 前綴＝刻意不用；`src/components/animate-ui/` 是第三方匯入，不套 lint。
- 動任何標記「刻意」的設計前先讀該模組 docstring。

## Where to look
- `README.md`：開發總覽、完整 API 表、環境變數、部署。
- `docs/WORKFLOW.md`：端到端管線、階段 I/O、標籤詞彙、Web API 契約、R2 遷移順序。
- `AGENTS.md`：貢獻者慣例（結構、風格、測試、commit、安全）。
- `docs/production_resilience.md`、`docs/LINEBOT_ALWAYS_ON.md`、`docs/CAPACITY.md`、`docs/EXTERNAL_ACCESS.md`、`docs/incidents/`：維運、監控、容量、外部存取、事故。
- `docs/ARCHITECTURE.md`：模組地圖與不變量的完整版（本檔只列鐵律，細節與「刻意」設計的出處都在那裡）。
- `docs/EXTRACTION.md`：抽取層現況（抽取器選型與授權、文件模型、品質指標、快取、回填）。
- 歷史規劃、架構檢視快照、版面診斷與設計稿已於 2026-09-18 移出 repo，需要時看 git 歷史（原檔名 docs/EXTRACTION_REDESIGN.md、docs/ARCHITECTURE_REVIEW_2026-07.md 與其 _VERIFY、docs/REPORT_LAYOUT_FIXES.md、docs/design/；刻意不加反引號，免得觸發文件契約測試的路徑檢查）。
