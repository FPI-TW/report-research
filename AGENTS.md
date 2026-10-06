# AGENTS.md

廷豐智能研報——券商研報平台：PDF/docx 抽字 → LLM（DeepSeek）標註 → BGE-M3 嵌入 pgvector → 語意檢索／RAG 問答／觀點雷達／每日簡報／閱讀頁。Repo 目錄是 `report-mark`，GitHub 是 `FPI-TW/report-research`。

本檔是給貢獻者與 AI 代理的**唯一**指引檔（repo 刻意不放 CLAUDE.md；Claude Code 2.1.277 起，工作目錄及其上層都沒有 CLAUDE.md 時會自動讀本檔）。這裡只列鐵律、改動連動與陷阱；模組地圖與「刻意」設計的完整出處在 `docs/ARCHITECTURE.md`，管線與 API 契約在 `docs/WORKFLOW.md`。

- 回覆使用者一律繁體中文；不加裝飾性 emoji。
- 動任何標記「刻意」的設計前，先讀該模組 docstring（索引在 `docs/ARCHITECTURE.md` §10）。

## 鐵律

- **分工**：Python 做所有決定性的事（解析、抽取、切塊、嵌入、儲存、檢索、錨定、聚合、窗期），LLM（DeepSeek）只做語意（標註、摘要、問答、訊號擷取）。每個管線階段以檔案 SHA256 `file_hash` 為鍵、可斷點續跑。
- **不另建檢索**：新功能重用 `hybrid_search`／`retrieval_pipeline`。
- **fail-open**：派生功能（rerank、忠實度、追問、摘錄、agentic 補查）一律降級，不阻斷主流程。
- **破壞性操作先問**：`make help` 列出的 `reset-db`／`clean-data`／`ingest-lowio` 除非使用者明確要求，否則不要跑；任何 TRUNCATE／DROP 前先問。
- **共用工作樹**：`git add <path>`，不用 `-A`／`.`（他人有 WIP）。這台機器的主 checkout 就是部署目錄。
- 市場代碼對齊 findb（`TW US HK CN FX WTX MACRO GLOBAL CRYPTO`），對照在 `app/services/tagging.py`、由 `tests/test_tagging.py` 逐字釘住，是跨 repo 契約；`make align` 零 LLM 重對。

## 指令

```bash
uv sync                              # Python 3.11+；torch 為 CPU-only
cp .env.example .env                 # 務必改掉 REPORT_MARK_SESSION_SECRET 的佔位值
uv run python scripts/create_admin.py --username <名稱>   # 套完 schema 後建第一位管理員（密碼互動輸入，不經 argv）
make setup                           # 相依 + pgvector 容器 + alembic upgrade head（空庫）
make schema CONFIRM=<host:port/db>   # 已有資料的庫做 migration：逐字確認目標（本機預設庫就是生產庫）
make schema-check                    # 嚴格 drift 比對（零＝0、有＝1）；既有庫導入見「改動對照表」
make schema-version                  # 只比 alembic 版本與程式 head（唯讀、不建暫存庫；0 一致／1 落後／2 超前或無法判斷／3 連不上）
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

make summaries / titles / takeaways / signals / brief   # 批次，都呼叫 LLM（預設 DeepSeek）、以 flock 互斥
make sync-once / db-backup / freshness / db-audit        # 維運
make llm-blocked                     # LLM 批次跳過名單（唯讀；research.llm_task_failure）
make boilerplate                     # 重建跨文件樣板字典 data/boilerplate/（入庫切塊前剔除；零 LLM）
make up-edge / down-edge / edge-logs / edge-reload       # 對外 nginx + cloudflared
uv run python scripts/extract_all.py                     # 全語料三支：只在初次建庫或補歷史
uv run python scripts/tag_all_cli.py --workers 8
uv run python scripts/ingest_all.py
```

## 測試與 CI

- CI 四個 job 全為必要檢查（`.github/workflows/ci.yml`）：`前端測試（tsc + vitest）`（實際另跑 ESLint 與 vite build，並把 `frontend/dist` 傳給後端 job）、`後端測試（pytest）`（ruff＋pytest，SPA 測試對真 build 驗證）、`schema 契約（PostgreSQL）`（空庫 `alembic upgrade head`、單一 head、drift 自我一致、既有庫 stamp 演練，再以 `REPORT_MARK_REQUIRE_DB=1` 跑 DB 契約測試）、`secret 掃描（gitleaks）`。**required check 名稱＝job 的 `name`**，分支保護在 GitHub 設定不在 repo；改了 `name` 沒同步改設定，PR 會永遠等一個不回報的 check。
- async 測試一律 `unittest.IsolatedAsyncioTestCase`；**不用 pytest-asyncio**（未安裝、刻意不裝，`tests/test_dev_ergonomics.py` 守門）。
- 測試不連網、不載模型（CI 設 `HF_HUB_OFFLINE=1`）：LLM、嵌入、檔案系統一律用假物件。給函式加參數時同步改假物件簽章——過期的假物件拋 `TypeError` 會被外層 `except` 吞掉，程式靜默走另一條路。
- 例外是十支 DB 契約測試（`tests/test_schema_constraints.py`、`tests/test_content_norm_equivalence.py`、`tests/test_extraction_log_db.py`、`tests/test_conversations_db.py`、`tests/test_review_db.py`、`tests/test_accounts_db.py`、`tests/test_qa_isolation_db.py`、`tests/test_report_visibility_db.py`、`tests/test_ops_monitoring_db.py`、`tests/test_ops_rollup_db.py`）：連得上 DB 就真的連（**本機預設庫就是生產庫**，一律 rollback 不 commit），連不上就 skip；CI 設 `REPORT_MARK_REQUIRE_DB=1` 禁止 skip。
- 端點走 HTTP 層測（`TestClient`），不直接呼叫 handler 物件。router 檔的輔助函式一律放在所有 `@router.*` 裝飾器之上；夾在裝飾器與 handler 之間會讓端點回 422，直呼函式的測試看不到。
- **測試絕不可寫 repo 根的真實環境檔**：`finally` 擋得住例外、擋不住行程被殺，要驗載入行為餵 `tempfile`。`tests/conftest.py`（快照並還原 `.env`，變動即讓整個 session 失敗）與 `tests/test_env_loading.py` 是第二道防線，不是許可證。
- 任何預設寫進 repo 根 `data/` 的新狀態檔，要在 `tests/conftest.py` 以**賦值**（不是 `setdefault`）導向不存在的路徑或 `os.devnull`，比照 `LLM_BREAKER_FILE`、`LLM_USAGE_LOG`。回應隨查詢參數變的 TTL 快取走 `web/ttl_cache.py`（conftest 每題前後 `reset_all()`）；其他模組級快取要在 conftest 加每題前後的重設（比照 monitor 的快取），否則測試互相污染。
- `tests/conftest.py` 刻意強制 `LLM_PROVIDER=claude_cli`（測試不打付費 API）；要驗預設值的測試自己移除該鍵。
- 帳號：`tests/conftest.py` 整個 session 把 `web.deps.accounts` 換成記憶體假帳號庫（`tests/fake_accounts.py`，預設一位 tester／testpass **管理員**），既有測試照常 POST `/login`。要驗一般使用者、停用、跨使用者隔離的測試用 `fake_accounts.install()` 換上自己的一份；只需要「已登入」用 `session_cookies()`。conftest 也預設把問答的擁有權檢查（`deps.conversation_is_foreign`／`deps.qa_is_foreign`）stub 成「不是別人的」，要驗 404 的測試自行覆寫。改了 `app/services/accounts.py` 的語意要同步改假物件——`tests/test_accounts_db.py` 同一組情境兩邊各跑一次。
- 本機全綠不代表安全：抽取層 CJK 測試（`tests/test_extraction_layout.py` 的 CjkTests，用 weasyprint 渲染中文測試 PDF）缺字型時本機 skip，CI 以 `REPORT_MARK_REQUIRE_CJK=1` 封死。
- 授權守門 `tests/test_license_guard.py`：帶網路條款的 copyleft（AGPL／SSPL）一律紅，掃已安裝套件 metadata、`uv.lock` 名稱黑名單與 `frontend/package-lock.json`。紅了是換掉相依，不是加豁免。Dependabot 自動更新 PR 已停用，相依更新改由人工審查；更新 `torch` 時須確認 CPU-only wheel、FlagEmbedding／transformers 相容性並跑 eval，`@embedpdf/*` 必須整組升版並人眼驗證 PDF 選取與複製。
- 契約類測試（改了別處會紅，修程式或文件，不放寬 allowlist）：`tests/test_docs_contract.py`、`tests/test_schema_constraints.py`、`tests/test_schema_migrations.py`、`tests/test_schema_baseline.py`、`tests/test_schema_drift_check.py`、`tests/test_content_norm_equivalence.py`、`tests/test_sse_event_contract.py`、`tests/test_radar_contract.py`、`tests/test_deploy_units.py`、`tests/test_sync_timer_persistence.py`、`tests/test_env_loading.py`、`tests/test_llm_env_loading.py`、`tests/test_logging_setup.py`、`tests/test_dev_mode.py`、`tests/test_claude_lock.py`、`tests/test_claude_cli.py`、`tests/test_llm.py`、`tests/test_llm_models.py`、`tests/test_sql_index_hygiene.py`、`tests/test_secret_scan_config.py`、`tests/test_license_guard.py`、`tests/test_dev_ergonomics.py`、`tests/test_pre_split_guards.py`、`tests/test_spa_serving.py`、`tests/test_eval_question_contract.py`、`tests/test_db_backup.py`、`tests/test_authz.py`、`tests/test_accounts_db.py`、`tests/test_qa_isolation.py`、`tests/test_qa_isolation_db.py`、`tests/test_admin_client_generated.py`、`tests/test_visibility_guard.py`、`tests/test_report_visibility_db.py`。
- 評測（`eval/`）刻意不進 CI。改檢索或生成品質時前後各跑一次、用 `make eval-compare BASE=… CAND=…` 比，**退出碼是結論**：0 無劣化／1 劣化／2 不可比／3 有未分類指標（新指標要在 `METRIC_SPECS` 補方向）。門檻 F>0.9／CP>0.8／AR>0.55 是政策，不擅自改。最新基準線 `eval/baselines/baseline-2026-09-29-jdsflash-gdsflash.json`（DeepSeek judge，系譜 `deepseek-2026-09`，18 題×3 次，固定正式語料快照；快照 ID 見 README「開發與測試」）；舊的 `eval/baselines/baseline-2026-09-02.json` 是 haiku judge 系譜，拿新結果比一律回 2。正式 `eval/run_ragas.py` 帶 `--checkpoint-dir` 才能續跑。`eval/ragas_questions.json` 是凍結題集（`eval/question_contract.py` 守，`scripts/bench_load.py` 共用），改題集就失去與基準線的可比性。

## 改動對照表（改了 A 就要動 B）

| 改了什麼 | 還要做什麼 |
|---|---|
| 任何 Python | `sudo systemctl restart report-mark-web.service` |
| 任何前端 | `make build-web`；`frontend/dist` 不存在時 SPA 回 503 |
| 改檔名、刪檔、加端點 | `tests/test_docs_contract.py` 會紅：**改文件，不放寬 allowlist**。living docs 是本檔、`README.md`、`docs/WORKFLOW.md`、`docs/ARCHITECTURE.md`、`docs/EXTRACTION.md`（新增這類文件要加進該測試的 `LIVING_DOCS`）。端點路徑逐字寫進 README 的 API 表（`docs/WORKFLOW.md` 有契約細節時也寫），含參數名與 `:path` |
| 新增後端 SSE 事件 | 加進 `tests/fixtures/sse_events.json`（兩側測試會指出另一側缺什麼） |
| 新增 SSE 事件欄位 | 契約測試**抓不到**欄位漏宣告：自己在 `frontend/src/lib/askSchemas.ts` 用 `optional()` 宣告（zod 預設 strip，未宣告鍵靜默丟掉），並在 `askSchemas.test.ts` 補斷言 |
| schema 變更 | 一律寫新 revision：`uv run alembic revision --rev-id 00NN -m "..."`，SQL 放模組常數 `UPGRADE_SQL`（手寫、不用 autogenerate，`tests/test_schema_migrations.py` 守）；`db/schema.sql` 是**凍結的 baseline**（revision 0001，SHA-256 釘住），不再修改。平行分支各長一個 revision 會分岔成多個 head，整合時把 `down_revision` 改成線性。對已有資料的庫跑 migration 要 `CONFIRM=<host:port/db>`；worktree 開發把 `REPORT_MARK_DB_URL` 指向 devdb。改了約束要對**剛 `upgrade head` 的全新空庫**跑 `REPORT_MARK_WRITE_CONSTRAINTS=1 uv run pytest -q tests/test_schema_constraints.py` 重生 `db/expected_constraints.txt`（對既有庫重生只會得到舊約束）。刪表在 revision 裡寫 DROP |
| 既有庫導入 Alembic（尚未 stamp 的庫） | baseline 只接受空庫，`make schema` 對既有庫會拒絕。順序：`make schema-check`（`scripts/schema_baseline.py`，在同伺服器建暫存庫逐項比對系統目錄）必須零 drift——有 drift 先修（已知差異的修正在 `db/align_baseline_indexes.sql`）→ `make schema-stamp-baseline CONFIRM=… DUMP_DIR=…`（受保護的庫強制全庫 `pg_dump -Fc` preflight：空間、時限、PGDMP 檔頭、`pg_restore -l`）。DB 不在容器（staging 的 RDS）時加 `DUMP_CONTAINER=`，並以 `REPORT_MARK_PROTECTED_DB_TARGETS` 把它列為受保護 |
| 新增不可重建的表 | `scripts/db_backup.sh` 的 `BACKUP_TABLES`、`tests/test_db_backup.py` 的清單、README／`docs/ARCHITECTURE.md`／`docs/production_resilience.md`／本檔「資料層陷阱」的表數字樣一起改 |
| 新增管理端點 | 路徑放 `/api/admin/` 或 `/api/review/` 底下、router 層掛 `authz.require_admin`，每條再掛 `authz.require_scope(...)`（或 `require_super`；敏感操作加 `require_elevated`），`tests/test_authz.py` 結構性檢查每一條；`/api/admin/*` 的 pydantic model 改了要重跑 `uv run python scripts/gen_admin_client.py`（`tests/test_admin_client_generated.py` 比對 `frontend/src/lib/generated/adminApi.ts`）；要特定錯誤代碼用 `web.errors.AppError`；帳號規則寫在 `app/services/accounts.py` 而不是路由層（CLI 也要受約束）；會改資料的管理動作在同一筆交易寫 `admin_audit_log`，`detail` 不得含密碼或註記全文 |
| scope 詞彙 | `app/services/accounts.py` 的 `ADMIN_DEFAULT_SCOPES`／`GRANTABLE_SCOPES` 是唯一定義；可授予的那組同時是 `research.user_scope` 的 CHECK（寫 revision、重生 `db/expected_constraints.txt`）與 `web/routers/admin.py` 的 `GrantableScope`（`tests/test_admin_api.py` 釘住），改完重跑 `scripts/gen_admin_client.py` |
| `review_state` 詞彙（kind／status／verification） | `web/routers/review.py` 的 `Literal` 與 `frontend/src/lib/reviewSchemas.ts` 的 zod enum 逐字一致（**沒有測試守門**）；`verification` 的預設值 `untested` 改了要寫 revision（表刻意無 CHECK、無 FK）；部署順序 `make schema` → `make build-web` → 重啟 web |
| 新增或修改面向使用者、查 `research_report`／`report_chunk`／`report_signal`／`report_takeaway` 的 SQL | 帶 `app/services/visibility.py` 的 `visible_report_sql(別名)`（只有 report_id 時用 `visible_report_id_sql`），被管理員隱藏的研報才不會漏出來；`tests/test_visibility_guard.py` 以 AST 掃描檢索、閱讀、雷達、總覽、簡報、原檔各模組。批次與管理面不過濾（真的不需要時列進該測試的 `_EXEMPT` 並寫理由） |
| `content_norm` 或 `textnorm.norm_for_match()` | 兩者逐字等價（`tests/test_content_norm_equivalence.py`；已知 6 個分歧字元由該測試鎖住範圍，不可擴大） |
| 新旋鈕 | 放 `app/config.py`（frozen dataclass＋`os.getenv`，非 pydantic-settings）。既有散在各檔的讀取**不要順手搬**；找旋鈕要同時搜 `os.getenv` 與 `os.environ`（範圍 `app web scripts eval`；只搜前者會漏掉 `web/auth.py` 等處）。`REPORT_MARK_*` 前綴只給 auth／DB；既有帶前綴的例外（`REPORT_MARK_RERANK_*`、`REPORT_MARK_MAX_TRACKED_FAIL_IPS`、`REPORT_MARK_ROOT`、`REPORT_MARK_ALERT_WEBHOOK`、`REPORT_MARK_BACKUP_*`，測試用的 `REPORT_MARK_REQUIRE_*`、`REPORT_MARK_WRITE_CONSTRAINTS`）是 live 的，不要改名 |
| `ops_agent/` 或 `deploy/ops/services.*.toml` | 代理從 root 擁有的 `/opt/report-mark-ops/` 執行（docker 群組等同 root，不從 repo 跑）：重新 `install` 到那裡、`--check` 後重啟 `report-mark-ops-agent.service`（步驟見 `docs/production_resilience.md`「維運代理」）。代理只用標準庫、不 import `app.*`（`tests/test_ops_agent.py` 守）；新 action（P7 的 restart／run-now）要同時改 `ops_agent/protocol.py` 的 `KNOWN_ACTIONS`／`OPS` 與 `web/routers/admin_ops.py` 的 `Action` |
| `deploy/systemd/` 的 unit 與 `report-mark-web.service.d/` | `sudo cp` 到 `/etc/systemd/system/` 再 `daemon-reload`；`*.env.example` 對應 `/etc/default/`（`report-mark-llm` 必須 `install -m 0640 -o root -g kashionz`，`cp` 會讓金鑰全員可讀），`*.sudoers` 以 `install -m 0440` 裝進 `/etc/sudoers.d/` 且目的檔名不帶副檔名（sudo 忽略含 `.` 的檔名），`mount-nas-*` 裝到 `/usr/local/sbin/`，`report-mark-alert.sh` 就地執行。`deploy/docker-compose.yml`、`deploy/nginx.conf` 走 `make up-edge`／`edge-reload`。不要只改機器上的副本；`tests/test_deploy_units.py` 守 unit 檔 |
| DeepSeek 金鑰 | repo 根 `.env` 與 `/etc/default/report-mark-llm`（0640 root:kashionz，只有 sync unit 載入）逐字相同（核對：`uv run python -m scripts._llm_env .env /etc/default/report-mark-llm`）；改完重啟 web，不需 `daemon-reload`。輪替見 `docs/production_resilience.md` |
| 新的 LLM 批次或評測入口 | `sys.path.insert` 之後第一個專案 import 必須是 `scripts._llm_env`、緊接 `load_llm_env()`；`require_llm_key(...)` 在取鎖之前（`tests/test_llm_env_loading.py` 掃描入口檔釘住，只 import `answer`／`retrieval_pipeline` 等間接層的入口也算；不呼叫 LLM 的列在該檔 `NON_LLM_ENTRIES` 並逐一核對取用的名稱）；批次傳 `{任務: 模型}`（缺金鑰時說得出是哪個旋鈕），評測傳模型名清單。會呼叫 LLM 的批次還要加進 `tests/test_claude_lock.py` 的 `LOCKED_SCRIPTS` 並取鎖 |
| 新增批次 LLM 呼叫點（`run_claude`） | 帶 `max_tokens` 與 `meta={"task", "file_hash", "report_id"}`（簡報只需 `task`），值由 `tests/test_claude_cli.py` 的 `EXPECTED` 表逐點釘住，改值要同步改表並說明理由；重試迴圈加 `if res.text is None and not is_retryable(res): break`（`API[...]` 已在傳輸層處理，解析失敗才在腳本層重試），跳過名單的 reason 用 `failure_kind(res)` |
| 新增 `stream_completion` 呼叫點（線上與評測） | 帶 `max_tokens` 與 `task`（`tests/test_llm.py` 的 `EXPECTED` 表逐點釘住）；同步更新所有假物件簽章；model 只用白名單或 `claude-*` 正式名稱 |
| 改批次 prompt 或解析規則 | 跳過名單（`llm_task_failure`）只以 model 為鍵、不看 prompt：重跑要加 `--retry-blocked`，否則舊失敗繼續擋。`should_skip()` 與 `skip_clause_sql()` 必須等價（`tests/test_llm_failures.py`） |
| 讀忠實度分數 | 一律經 `app/services/judge_schema.py` 的 `CURRENT_JUDGE_SQL`／`JUDGE_MODEL_SQL`／`is_current_judge`，不自寫過濾；`LEGACY_JUDGE_MODEL` 永遠不跟著生產預設改 |
| 問答輸入框新增工具 | `frontend/src/features/ask/ComposerTools.tsx` 的 `useTools()` 陣列；已開啟的工具要在收合狀態外露。網搜暫停中由 `frontend/src/lib/useWebSearch.ts` 的 `WEB_SEARCH_PAUSED` 控制，不要刪 web 項；清單為空時 `ComposerTools` 回 null |
| 加 `--workers` 或提高併發閘 | 先照 `.env.example` 的算式重算 DB 連線數（每行程上限 `DB_POOL_SIZE`＋`DB_MAX_OVERFLOW`＝20） |
| 改 `zh_hant.py`、`faithfulness.is_numeric_claim`、`_SIMILAR_SQL`（`app/services/reading/queries.py`）、`ASK_RERANK_CANDIDATES` | 先讀實測紀錄（`zh_hant.py` 模組 docstring、`faithfulness.py` 的 `_NUMERIC_RE` 上方、`queries.py` 的 `_SIMILAR_SQL` 周邊註解、`docs/CAPACITY.md`）；參數都是量出來的 |

## 架構不變量（摘要；完整版見 `docs/ARCHITECTURE.md`）

### 檢索與問答（§3–§5）
- 混合檢索：dense（HNSW 餘弦）＋ lexical（`pg_trgm` over 生成欄 `content_norm`），`hybrid_search` 只以 `(tier, fused)` 排序。選篇分**兩條互不共用**：檢索頁走 `retrieval.rank_reports`，問答走 `answer.select_reports`——調問答新近度改 `rank_reports` 沒有作用。
- `retrieval_pipeline.retrieve_context` 是問答的唯一檢索入口；`answer.py` 自己不呼叫 `hybrid_search`，**要 patch 檢索請 patch `retrieval_pipeline`**。`scripts/eval_retrieval.py` 刻意直呼 `hybrid_search`，管線改動它量不到。
- **循環依賴是刻意的**：`retrieval_pipeline` 頂層 import `answer`；`answer`／`agentic_qa` 之間任何反向取用一律函式內 import。
- 首輪路由順序刻意：確定性 overview 判定（`scope_router.resolve_overview_route`，零 LLM）→ `precheck_route()` 詞表（命中 `time_sensitive` 完全不檢索）→ LLM 四類分類（`classify_non_overview`）與檢索並行、誰先到聽誰。五類與 `decided_by` 全寫進 `qa_log.filters`；fail-open 落點是 `CORPUS_QA`。
- 網搜 `web_on` ＝ 請求的 `web` AND `ASK_ENABLE_WEB`，下游只讀 `web_on`；`qa_log.filters.web` 含 False 也要寫。系統提示與工具授權要一起切（`ask_system_prompt(web)`）。
- 引用過濾（`app/services/citation_filter.py`）：主答串流經 `CitationStreamFilter`、落庫與評測經 `filter_unknown_citations`，不存在的 `[n]` 換成「（無效引用）」、計數寫 `filters.invalid_citation_count`；改串流或落庫路徑不可繞過。
- 忠實度抽查在 `done` 後跑背景任務，上限 `ASK_FAITHFULNESS_MAX_INFLIGHT`。`faithfulness.is_numeric_claim` 是唯一閘門，漏判是靜默的——寧可多抓不可漏抓。低分門檻 `FAITHFULNESS_MIN`（0.9；讀不到時退回舊名 `REPORT_FAITHFULNESS_MIN`）由監控卡、待複核佇列與 `scripts/eval_faithfulness.py` 共用；`eval/run_ragas.py` 的 F>0.9 是獨立常數。

### LLM（§2.2 `llm.py` 列、§9）
- `LLM_PROVIDER` 預設 `deepseek`（未設、空值都是；未知值在 web 退回 deepseek 並記 ERROR，在批次預檢 rc=2 並印原始值——拼錯不能變成照常計費）。模型一律經 `llm_models.resolve_model`；兩個 judge 是 `deepseek-flash`。
- `stream_completion` 依白名單分派：`llm_models.HTTP_MODELS` 走 `llm_http`，其餘走 CLI（只剩網搜）。未吐字失敗拋 `LLMUnavailableError(kind=…)`，`/api/ask` 依 kind 回訊息（對照在 `web/routers/ask.py` 的 `_LLM_ERROR_DETAILS`，都不承諾已通知）；已吐字後截斷保留已吐的字、由 Python 附註並寫 `filters.llm_truncated`。內容審查與 402 **不改走 Claude**。
- CLI 旗標、逾時語意、重試條件的細節只記在 `docs/ARCHITECTURE.md`，改 `llm.py` 前先讀。

### 閱讀頁、雷達、簡報（讀取路徑零 LLM；§6）
- 閱讀頁：正典文字是 `clean_extracted(full_text)`，`text_sha256` 守不變量；錨點有效與否只在後端判。`PagePointerProvider` 要在 `Rotate` 之內（`PdfViewer.tsx`）；複製走 `frontend/src/lib/clipboard.ts`（區網 HTTP 沒有 `navigator.clipboard`）。`/text` 端點、`anchor.py`、`quote_start`／`quote_end` 是刻意留的可逆性，不要清。
- 雷達：`report_signal` 一列＝研報×標的，**空是常態**（有研報未擷取回 200 `pending_extraction`）。清單 payload 不得含目標價數值；`signal_extract.py` 的 `THESIS_DIMENSIONS`／`STANCE_CONSTRUCTIVENESS` 是共用契約；`radar/schemas.py` 的 `Literal` 由前端 zod 逐字鏡像。
- 簡報：窗期用 `created_at` 不是 `report_date`；評等變動要同時「這輪才擷取」且「報告夠新」（`report_date` 在 `SIGNAL_MAX_REPORT_AGE_DAYS` 內、NULL 排除）；來源清單由 Python 記錄不從 markdown 反推；鎖只包那一次 LLM 呼叫（不在進入點取）。

### 抽取、入庫與物件儲存（`docs/EXTRACTION.md`、`docs/WORKFLOW.md`）
- 全語料三支：`scripts/extract_all.py` → `scripts/tag_all_cli.py` → `scripts/ingest_all.py`（gate on `is_research`＋`market`），各階段 per-hash 快取；生產入庫走 `scripts/sync_new_reports.sh`。
- `EXTRACTOR` 程式預設 `pypdf`，生產的 pdfplumber 版面層（`app/services/extraction/layout.py`）是 sync 環境檔設的；批次不讀 repo 根 `.env`，**手動跑 `extract_all.py` 要自己帶 `EXTRACTOR=pdfplumber`**，否則靜默走 pypdf。品質只標記不擋；`extraction_log` 的 `stopped_at` 詞彙與 `store.STOPPED_AT` 逐字對齊。**刻意不用 PyMuPDF**（AGPL，本站對外服務）。
- 物件儲存生產已是 `OBJECT_STORAGE_MODE=r2`：缺 key 即 503、不回退本機；非 local 缺任一 `R2_*` 啟動即 fail-closed，憑證在 repo 根 `.env` 與 `/etc/default/report-mark-sync` 逐字相同。bucket 必須設 CORS（沒設則 PDF 檢視器整頁靜默失敗、伺服器零錯誤）；presign 一律帶 `filename`、有效期不超過一小時。遷移與對帳順序見 `docs/WORKFLOW.md`。

## 資料層陷阱（§8）

- `research_report.full_text` 是未清理的原始抽取（帶 CJK 字間空白）；顯示一律 `clean_extracted(full_text)`，不是 `clean_text`（後者折掉換行，只適合檢索片段）。
- `report_chunk.content` 不是 `full_text` 的子字串（overlap merge），錨定一律經 `app/services/reading/anchor.py`。**不要寫批次更新 `report_chunk.content`**，要動只有重跑 `ingest_all.py`（`make normalize` 那種就地更新的死法記在 `docs/WORKFLOW.md`）。
- 簡體字守門 `zh_hant.to_traditional()`：LLM 產出的顯示文字落庫前一律轉（四支批次、訊號 summary、問答三條路徑）；串流路徑刻意不中途轉，問答在 `done` 帶只在有變動時出現的 `answer` 欄位收斂（`askSchemas.ts`＋`askReducer.ts` 都要接）。**逐字引文（`report_takeaway.quote`、`thesis_dimensions[*].evidence`）一律不轉**——它是錨定基準與 PDFium 搜尋關鍵字。
- 研報隱藏／恢復（`research.report_visibility`，revision 0004）**以 `file_hash` 為鍵、刻意不設 FK**：`upsert_report` 先刪後插換新 report_id，旗標掛在 report_id 上會靜默消失。被隱藏的研報對所有使用者路徑等於不存在（閱讀頁與原檔 404），批次照常處理、恢復即生效，管理面（待複核、監控）不過濾。
- 顯示名稱走 `title`，缺值回退 `file_name`（`frontend/src/lib/displayTitle.ts`）；NULL 是常態。
- dataclass 新欄位放末尾並給預設。但 `rows.ChunkRow` 是與 `store._meta_columns` 位置對齊的 NamedTuple：新欄位**插中段、絕不 append**，取欄位用 `ChunkRow._fields.index(...)`，不寫數字。
- DB 一律 `from app.services.db import SessionFactory`，不複製預設連線字串（鍵是 `REPORT_MARK_DB_URL`）。長查詢用 `db.relax_statement_timeout()`（`SET LOCAL`）。`DB_IDLE_TX_TIMEOUT_MS` 預設 0 是刻意的：sync 在交易內做 LLM 標註與嵌入。SQL bind 參數轉型寫 `CAST(:x AS text[])`，不可寫 `:x::text[]`（`::` 緊貼參數名會讓 `text()` 綁錯參數）；標的過濾寫 containment（`@>`）才吃得到 GIN（`tests/test_sql_index_hygiene.py`）。
- logging 只在 `web/server.py` 初始化（`app/logging_setup.py`，順序由 `tests/test_logging_setup.py` 釘住）；批次腳本的 `logger.info` 無聲。
- 備份涵蓋十二張不可重建的表（`qa_log`、`report_takeaway`、`report_signal`、`report_brief`、`review_state`、`app_user`、`admin_audit_log`、`user_scope`、`account_deletion`、`report_visibility`、`incident`、`incident_event`）→ NAS；語料層與 `user_session` 刻意不備。`app_user` 含密碼雜湊，備份檔當機密看待。
- 問答紀錄每列有擁有者 `qa_log.user_id`：所有面向使用者的讀寫（歷史、對話串、版本、刪除、回饋、續問與重生的舊列）一律帶 user 條件，不靠前端隔離；NULL＝個別帳號上線前的共用歷史，一般介面看不到。管理面（待複核、監控聚合、離線分析）刻意看全部列，但待複核佇列不回問答原文與提問者帳號（只有不可逆代號 `asker_code`），原文只能經 `POST /api/review/qa/{qa_id}/access` 逐筆讀（`qa_content.read`、只限佇列內、每次同交易寫稽核）；`scripts/analyze_qa_log.py` 等主機 CLI 直讀 DB 屬 break-glass，不經此權限也不留稽核。兩道隔離：路由在串流前檢查參照擁有權（別人的＝404、查詢失敗＝503），服務層 SQL 帶 `user_id IS NOT DISTINCT FROM :uid`；`app/services/answer.py` 的 qa_log 讀寫函式 `user_id` 是必填關鍵字參數，`tests/test_qa_isolation.py` 以 AST 守門每個呼叫點。

## Web、auth 與安全（§7）

- `web/server.py` 是組合層（加上 auth middleware 與白名單）；路由在 `web/routers/`（無 `APIRouter(prefix=...)`），共用符號經 `web/deps.py`（測試 patch 的單一位置）。
- Auth deny-by-default、fail-closed：個別帳號（`research.app_user`，Argon2id；刪除＝提出即停用、24 小時後清掉內容與可識別資料，列保留），middleware 驗完 cookie 簽章後**每個請求查 DB**（`deps.accounts.resolve_session`，刻意不快取：停用、強制登出、重設密碼、降級都要下一個請求就生效），身分放 `request.state.user`；查 DB 失敗回 503 不導回登入頁。授權只在後端 `web/authz.py`（`current_user`／`require_admin`／`require_scope`／`require_super`／`require_elevated`）：`/api/review/*`、`/api/admin/*` 整組限管理員且每條宣告 scope；super admin（`app_user.is_super`）才能授予 scope 與 super、才能管理其他 super admin；權限提升是 `user_session.elevated_until`（10 分鐘、綁 session）。前端的管理後台是獨立外殼（`AdminShell`，`/app/admin/*`），主平台只在帳號選單留入口；`RequireAdmin` 與入口顯示都只是顯示層。舊的 `REPORT_MARK_ACCESS_USERNAME`／`_PASSWORD` 已不讀取；救援入口是 `scripts/create_admin.py --reset-password`。`REPORT_MARK_SESSION_SECRET` 未設會用隨機值（每次重啟全員登出），生產必須設。免登入白名單 `/login`、`/healthz`、前綴 `/app/assets/`；`/healthz/storage`、`/healthz/llm` 也在白名單但只回答本機直連（其餘 404）。
- 外部存取需 `REPORT_MARK_EDGE_SECRET` 或 `REPORT_MARK_TRUSTED_PROXY_CIDRS` 任一（刻意 OR；祕密在 repo 根 `.env` 與 `deploy/.env` 逐字相同，後者鍵名不帶前綴），見 `docs/EXTERNAL_ACCESS.md`。Session 是 HMAC cookie（v3，只帶 `user_session.id`），7 天滑動、30 天上限；`REPORT_MARK_SESSION_SECRET`、`REPORT_MARK_SESSION_EPOCH` 會全員登出，是預期行為；單一帳號的撤銷走管理頁。
- `DEV_NO_AUTH=1` 三條件同時成立才放行（旗標在環境檔載入前已在 `os.environ`、對端與 URL hostname 皆 loopback、無代理 header），import 順序由 `tests/test_dev_mode.py` 釘住。`SKIP_WARMUP` 在 lifespan 才讀、判 `== "1"`，寫進 `.env` **會生效**。兩者都不要寫進環境檔。
- 併發閘只剩一個：`/api/ask` 上限 3 寫死在 `web/routers/ask.py` 的 `_ASK_GATE`（佇列 `ASK_MAX_QUEUE`），是 `web/concurrency.py` 的 `ConcurrencyGate`（刻意不支援 `async with`）。上限 per-process，lifespan 擋多 worker。
- CSRF：`web/csrf.py`，會改變狀態的請求帶 `Origin` 就必須等於 `Host`（沒有 `Origin` 時看 `Sec-Fetch-Site`；兩者都沒有＝非瀏覽器，放行），刻意沒有允許清單旋鈕。錯誤格式：`web/errors.py`，JSON 錯誤一律 `{detail, code, request_id}`，`detail` 意義不變。
- 稽核紀錄 `research.admin_audit_log` 只能新增（觸發器擋 UPDATE／DELETE／TRUNCATE，revision 0002），每列 `row_hash` 串成雜湊鏈（內容定義在 DB 函式 `research.audit_row_hash`）；`scripts/audit_anchor.py` 每日把鏈頭寫到 NAS 並比對先前錨點。還原用 `pg_restore --disable-triggers`；臨時 DB 還原演練的預期錯誤數是 5（見 `docs/production_resilience.md`）。
- 兩步驟驗證（TOTP，`app/services/totp.py`）每人自助開關（`/api/me/totp*`）；開了的帳號登入要第二步（同一個 POST `/login`，簽章暫時憑證綁 `accounts.mfa_fingerprint`、不可重放），權限提升也要驗證碼。帳號刪除的執行與重放都寫 DB 之外的 tombstone（`$REPORT_MARK_BACKUP_DIR/account-tombstones.jsonl`）：`qa_log.user_id` 刻意沒有 FK，刪除的正確性靠 `accounts._purge_user` 的單一交易；**從備份還原 `qa_log`／`app_user` 之後一定要跑 `scripts/replay_deletions.py`**，否則已刪除使用者的問答會復活。
- 祕密不進 argv（webhook URL 經 stdin 餵給 curl）；gitleaks 掃全歷史，`.gitleaks.toml` 由 `tests/test_secret_scan_config.py` 守。

## 生產維運

- 真相來源在 `deploy/`，不是機器上的 `/etc`。sync 鏈（每 3h）：rsync → 增量匯入 → 摘要 → 標題 → 摘錄 → 訊號（限量）→ 簡報 → 標題積壓（限量）。摘要／標題／摘錄吃 `--hashes-file`，**不可改成 `--since-days`**（濾的是 `report_date`，會漏掉近九成）；訊號與標題積壓的 `--limit`（`SYNC_SIGNAL_LIMIT`、`SYNC_TITLE_BACKLOG_LIMIT`）是安全機制不是效能旋鈕。
- 補救：單篇失敗用 `scripts/failures_to_delta.py` 轉 delta 重放，不要 `--all-local`；只有匯入撞鎖（rc=75）或上一輪被砍且 delta 不可靠時才用 `--all-local`。整段中止時照 log 印出的指令重放：匯入段（rc 非 0／75）重放保留的 delta，下游段 rc=2 以保留的 hashes 檔跑 `--hashes-file`；這兩種都不可用 `failures_to_delta.py`／`--all-local`（細節見 `docs/WORKFLOW.md`）。
- LLM 批次（清單見 `tests/test_claude_lock.py` 的 `LOCKED_SCRIPTS`）以 `scripts/_claude_lock.py` 的 flock 互斥——名稱是 CLI 時代的遺留，現在防的是重複計費、摘錄覆寫互撞與 DB 連線數。除簡報外都在 main 進入點取鎖；撞鎖 rc=75 是「不跑」不是「跑壞」。從 worktree 跑批次不與主 checkout 互斥。`llm.py` 刻意不在 flock 範圍內（`tests/test_claude_lock.py` 釘住）。
- 批次斷路器 `data/.llm_breaker` 觸發後，手動跑的批次 30 分鐘內 rc=2 拒跑；sync 輪內只擋同一輪後段（標記綁 `SYNC_ROUND_ID`、跨輪放行），且只擋會用到 DeepSeek 的段；`data/llm_usage.jsonl` 是費用歸因依據。
- 健康判定打 `/healthz`（只探 DB，回應只有 `status` 一鍵是釘死的），不看 `systemctl is-active`；oneshot 是否跑過用 `scripts/verify_oneshot_ran.sh`，不看 `Result=success`。
- 監控兩層：探針只回報事實（刻意不用 `uv run`、不 import `app.*`），`scripts/incident_handler.sh` 做去重與 RESOLVED。五組元件：web（`scripts/check_web_health.sh`）、LINE bot（`scripts/check_linebot_health.sh`）、對外邊緣（`scripts/check_edge_health.sh`，從本機打對外網址；1＝邊緣故障、3＝origin 自己壞了、4＝判不出來）、容器與主機（`scripts/check_container_health.sh`、`scripts/check_host_health.sh`；依 tier 去抖在探針裡做，3＝確認期、9＝important／supporting 確認失敗）；web 與 LINE bot 兩組不設 `INCIDENT_HOLD_EXIT_CODES`（它們的 3 是健康），其餘三組設 3。P5 每次狀態轉換另寫監控 spool（事件投影，失敗不影響告警）。**本機 `/healthz` 綠不代表外網連得到**；Docker Desktop 重啟後 nginx 可能停在 Exited，處置 `make edge-reload`。
- web 探針退出碼：R2 由 `/healthz/storage` 偵測為 6；DeepSeek 帳號由 `/healthz/llm`（只回 `llm` 一鍵、不含金額）偵測，CNY 餘額低於 `LLM_BALANCE_FLOOR` 為 7（WARNING），用罄／401／連不上／判斷不出來為 8（CRITICAL）；全查、一行帶全部 reason，退出碼取 8 → 5 → 6 → 7 最前面的。402 的處置是儲值，絕不改走 Claude。細節見 `docs/production_resilience.md`。
- `make freshness` rc 0／1／2／3 分流；`signal` 門檻 0 與語料閘是刻意預設。`make db-audit` 只讀不修，warn 也算失敗。
- `研報自動匯入/` 唯讀。`make ingest-lowio` 會 `fsync=off` 且 SIGKILL 後不還原；處置 `make restore-durability`。

## 過渡中狀態（2026-09-30 核對；狀態一變就改這節）

- **Admin v1 已在 main、生產尚未部署（2026-10-06）**：程式碼含 revision 0002～0007、ops agent、稽核錨定、刪帳、監控收集與聚合、容器／主機探針等，但部署目錄仍是舊版、生產庫仍停在 0001（只 stamp 過 baseline），對應的 systemd unit、polkit 規則與 ops agent 都沒有安裝。上線要另外取得同意，順序：devdb 演練 → staging → 生產，每站先備份並確認 `make schema-check` 零 drift；`make schema CONFIRM=…` 套到 head → 依 `docs/production_resilience.md` 安裝新 unit、polkit 與 ops agent → 重新安裝 `report-mark-incident.service` → 重啟 `report-mark-metrics` 與 web → `make build-web`。**舊程式在 0007 的庫上照常可跑（都是新增表與欄位），新程式在 0001 的庫上會壞**——一律先套 schema 再換程式。部署完成後改寫這一條。
- **Claude CLI 退場（PR-M，分支 `feat/deepseek-remove-cli` 未合併）**：claude CLI 已於 2026-09-23 放棄，現在只剩網搜解析到 Claude。`claude_cli`／`claude_only` 仍是合法 provider 值但已無可用後端；web unit 的 `deploy/systemd/report-mark-web.service.d/path.conf` 還在；探針退出碼 5（claude 依賴）已由 health unit 的空 `HEALTH_DEP_DROPIN=` 停用。PR-M 合併後這些一起移除，屆時同步改本檔、`tests/conftest.py` 的 provider 說明與上面「model 只用白名單或 `claude-*`」那句。
- **網搜暫停中**：生產 `ASK_ENABLE_WEB=0`，前端 `WEB_SEARCH_PAUSED` 隱藏開關、請求一律送 `web=false`；DeepSeek 版網搜完成後兩處一起恢復。
- **夜間回填** `report-mark-backfill.timer`（E1d）仍 enabled，跑完（journal 的「估計尚餘」歸零）後由人手動 disable。
- **深度研報已移除**（2026-09）：既有庫要手動跑 `db/drop_deep_report_tables.sql`。
- **個別帳號取代共用帳密（2026-10 開發中，尚未部署）**：部署順序 `make schema` → `scripts/create_admin.py --from-env`（或 `--username`）→ `make build-web` → 重啟 web → 在管理頁為每位同事建帳號（LINE bot 只從 LINE 群組下載研報到 NAS、不呼叫平台 API，不需要帳號） → 刪掉環境檔的 `REPORT_MARK_ACCESS_USERNAME`／`_PASSWORD`（還在時啟動記 warning）。生產庫套 schema 前，本機 `tests/test_schema_constraints.py` 對生產庫對帳會紅（多了 `app_user` 的 CHECK）。部署完成後改寫這一條。

## 慣例

- Commit：Conventional Commits＋繁中 scope（`feat(問答): ...`、`fix(抽取): ...`、`docs(維運): ...`），標題約 70 字內，本文寫 what 與 why。commit 前跑 ruff 與相關測試，不繞過 hook。
- Python：ruff `E,F,I`、120 字元、`E402` 關（import 前 `sys.path.insert`／載環境檔是刻意的）；**不跑 `ruff format`**、無 black／mypy。
- 前端：React 19＋TypeScript＋Vite、CSS Modules、TanStack Query、zod 在 API 邊界；`_` 前綴＝刻意不用；`src/components/animate-ui/` 是第三方匯入，不套 lint。

## 專案結構與文件地圖

- `app/services/`：抽取（`extract.py`、`extraction/`）、標註、切塊、嵌入、混合檢索（`retrieval.py`、`retrieval_pipeline.py`）、RAG 問答（`answer.py`），以及 `reading/`、`radar/`、`brief.py`。`app/config.py` 是新旋鈕唯一去處。
- `web/`：`server.py` 組合層、`routers/`、`deps.py`。`frontend/`：`src/features/*` 每頁一個、`src/lib/*` 是 API 邊界（zod、SSE、reducer）。
- `scripts/`：批次與維運；`deploy/`：systemd、nginx、docker-compose 的真相來源；`db/`：`schema.sql`（凍結的 baseline）、`migrations/`（Alembic，設定在 repo 根 `alembic.ini`）與約束 golden；`eval/`：離線評測與基準線（不進 CI）。`研報自動匯入/`（唯讀鏡像）與 `data/`（執行期產物）不進版控。
- 文件：`README.md`（開發總覽、完整 API 表、環境變數、部署）、`docs/ARCHITECTURE.md`（模組地圖與不變量完整版）、`docs/WORKFLOW.md`（端到端管線、階段 I/O、標籤詞彙、API 契約、R2 遷移順序）、`docs/EXTRACTION.md`（抽取層）；維運類 `docs/production_resilience.md`、`docs/LINEBOT_ALWAYS_ON.md`、`docs/CAPACITY.md`、`docs/EXTERNAL_ACCESS.md`、`docs/nas_scheduled_sync_deployment.md`、`docs/incidents/`、`docs/benchmarks/`。
- 文件只描述現況；歷史規劃、架構檢視快照、版面診斷與設計稿已於 2026-09-18 移出 repo，需要時看 git 歷史（原檔名 docs/EXTRACTION_REDESIGN.md、docs/ARCHITECTURE_REVIEW_2026-07.md 與其 _VERIFY、docs/REPORT_LAYOUT_FIXES.md、docs/design/；刻意不加反引號，免得觸發文件契約測試的路徑檢查）。
