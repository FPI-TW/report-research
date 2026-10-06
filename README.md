# 廷豐智能研報（report-mark）

券商研報平台：把 `研報自動匯入/` 的 PDF／docx 抽字、以 LLM（DeepSeek）標註市場與標的、以 BGE-M3 嵌入 pgvector，提供語意檢索、RAG 問答、研報閱讀頁、券商觀點雷達與每日簡報。單機部署（WSL2 ＋ Docker Postgres），經 Cloudflare Tunnel 對外，個別帳號登入（管理員／一般使用者兩種角色）。GitHub 為 `FPI-TW/report-research`，目錄名沿用 `report-mark`。

## 目錄

- [功能總覽](#功能總覽)
- [系統架構](#系統架構)
- [快速開始](#快速開始)
- [專案結構](#專案結構)
- [Web 介面與 API](#web-介面與-api)
- [設定（環境變數）](#設定環境變數)
- [開發與測試](#開發與測試)
- [部署與維運](#部署與維運)
- [延伸文件](#延伸文件)

## 功能總覽

| 頁面 | 路徑 | 做什麼 |
|---|---|---|
| 檢索 | `/app/search` | 混合檢索（dense ＋ lexical），市場、商品類型、報告類型篩選，卡片／表格／Bento 三種檢視 |
| 問答 | `/app/ask` | RAG 串流問答，多輪對話，五類路由（離題、總覽、語料問答、時效、投資建議），追問建議；網搜**暫停中**（見下） |
| 閱讀頁 | `/app/report/:hash` | 原檔 PDF 內嵌檢視（EmbedPDF）、重點摘錄、券商訊號卡、相似研報 |
| 觀點雷達 | `/app/radar` | 標的目錄、多券商評等與目標價共識、四維論點、券商時間軸，讀取零 LLM |
| 每日簡報 | `/app/brief` | 窗期內新研報與評等變動的日報 |
| 監控 | `/app/monitor` | 語料規模、批次覆蓋率、抽取品質、忠實度、管線與排程健康 |

## 系統架構

```
                    ┌──────────────── 離線批次（systemd timer / make）────────────────┐
研報自動匯入/ ──▶ extract_all ──▶ tag_all_cli(Flash) ──▶ ingest_all ──▶ pgvector      │
                    │  data/extracted/  data/tags/          research_report            │
                    │                                       report_chunk (HNSW+trgm)   │
                    │  extract_takeaways / extract_signals / generate_summaries /       │
                    │  generate_titles / generate_brief（Flash，flock 互斥）             │
                    └──────────────────────────────────────────────────────────────────┘
                                                  │
     瀏覽器 ── Cloudflare Tunnel ── nginx ── uvicorn web/server.py（單 worker，:8097）
                                                  │
                     web/routers/* ── app/services/*（檢索、問答、閱讀、雷達、簡報）
                                                  │
                     DeepSeek API（flash）         BGE-M3 ＋ bge-reranker（CPU 常駐）
                     R2（可選，研報原檔）
```

分工鐵律：Python 做所有決定性的事，LLM（DeepSeek）只做語意。派生功能一律 fail-open。完整不變量見 `docs/ARCHITECTURE.md`。

技術棧：Python 3.11 ＋ uv、FastAPI ＋ uvicorn、SQLAlchemy async ＋ asyncpg、pgvector（HNSW cosine）＋ pg_trgm、FlagEmbedding（BGE-M3、bge-reranker-v2-m3，torch CPU-only）、pdfplumber ＋ pypdf ＋ python-docx、boto3（R2）、OpenCC（簡→繁）；前端 React 19 ＋ TypeScript ＋ Vite ＋ TanStack Query ＋ zod ＋ EmbedPDF；LLM 預設走 DeepSeek 官方 API（`httpx` 直連，`app/services/llm_http.py`），不用 SDK；網搜仍解析到 `claude` CLI（`claude -p`），而 CLI 已於 2026-09-23 放棄，所以網搜**暫停中**：生產以 `ASK_ENABLE_WEB=0` 關閉，前端不列網搜開關、請求一律送 `web=false`（`frontend/src/lib/useWebSearch.ts` 的 `WEB_SEARCH_PAUSED`）。恢復條件是 DeepSeek 版網搜（Tavily 工具迴圈）完成，屆時移除 `ASK_ENABLE_WEB=0`、把該常數改回 false。

## 快速開始

```bash
uv sync                       # Python 3.11+；torch 走 CPU-only index
make setup                    # 相依 + pgvector 容器（host port 5436）+ alembic upgrade head
cp .env.example .env          # 填 REPORT_MARK_SESSION_SECRET（未設則每次重啟全員登出）
uv run python scripts/create_admin.py --username <名稱>   # 第一位管理員（套完 schema 之後）

# 全語料三支（初次建庫；生產增量走 sync 鏈）
uv run python scripts/extract_all.py
uv run python scripts/tag_all_cli.py --workers 8
uv run python scripts/ingest_all.py

make build-web                # 前端 → frontend/dist；缺它 SPA 回 503
make serve                    # http://localhost:8097，模型常駐，Python 改動要重啟
make serve-dev                # --reload + SKIP_WARMUP=1，只綁 127.0.0.1
make serve-preview            # DEV_NO_AUTH=1 免登入看版面，另開 8098
make search Q="AI 伺服器散熱" MARKET=TW   # CLI 檢索
```

DeepSeek 金鑰 `DEEPSEEK_API_KEY` 放 repo 根 `.env`（web）與 `/etc/default/report-mark-llm`（批次），兩份逐字相同。`claude` CLI 的 PATH drop-in `deploy/systemd/report-mark-web.service.d/path.conf` 留到 PR-M 移除。

## 專案結構

```
app/
  config.py               環境變數 → frozen Settings（新旋鈕放這裡）
  logging_setup.py        只在 web/server.py 初始化
  services/
    extract.py, extraction/   抽取門面、版面層、品質、per-hash 快取（docs/EXTRACTION.md）
    filename.py, tagging.py   檔名解析、標籤詞表（市場代碼對齊 findb）
    chunk.py, embed.py, store.py, rows.py, textnorm.py, db.py
    retrieval.py, retrieval_pipeline.py, rerank.py      混合檢索唯一入口
    answer.py, agentic_qa.py, scope_router.py, overview.py, query_planner.py,
    faithfulness.py, evidence.py, followups.py, llm.py, zh_hant.py, locale.py
    reading/, radar/, signal_extract.py, brief.py       讀取零 LLM 的功能
    object_storage.py     R2（local / hybrid / r2）
web/
  server.py               組合層：env → logging → auth middleware → lifespan → routers
  routers/                11 支 APIRouter，不帶 prefix
  deps.py, auth.py, concurrency.py, dev_mode.py, env_loader.py
frontend/src/
  features/{search,ask,monitor,radar,report,brief,help}
  lib/                    API 邊界：zod schema、readSSE、askReducer、hooks
  components/{shell,primitives,animate-ui}
scripts/                  批次與維運（50 支），會 spawn claude 的取 _claude_lock.py
db/schema.sql（凍結的 baseline）、db/migrations/（Alembic revision）、db/expected_constraints.txt、
                          db/align_baseline_indexes.sql 與 db/drop_deep_report_tables.sql（既有庫手動執行）
deploy/                   systemd unit、nginx、docker-compose（部署真相來源）
                          deploy/ops/：維運代理的 Service Catalog（prod／dev）
ops_agent/                維運代理（標準庫、不 import app.*；部署時複製到 /opt/report-mark-ops）
eval/                     離線評測 harness 與基準線（刻意不進 CI）
tests/                    pytest（unittest 風格）＋ fixtures/sse_events.json
docs/                     WORKFLOW / ARCHITECTURE / EXTRACTION / 維運文件
研報自動匯入/               唯讀來源鏡像；data/ 為執行期產物（皆不入版控）
```

## Web 介面與 API

所有路徑除 `/login`、`/healthz`、`/app/assets/` 外都要登入；`/api/` 未登入回 401，其餘 302 到 `/login`。登入是個別帳號（`research.app_user`），每個請求都查 DB 的 session 狀態：帳號停用、強制登出、重設密碼都在下一個請求生效；帳號服務（DB）不可用時回 503 而不是導回登入頁。`/api/review/*` 與 `/api/admin/*` 限管理員（一般使用者回 403），每條另要求一個 scope（管理員預設有 `admin`、`accounts.manage`、`audit.read`、`review.manage`、`ops.read`、`reports.manage`；`qa_content.read`、`ops.operate` 要另外授予；super admin 全部都有）。會改變狀態的請求（POST／PUT／PATCH／DELETE）帶了 `Origin` 就必須與 `Host` 相同，跨站一律 403 `csrf_rejected`。JSON 錯誤一律是 `{detail, code, request_id}`：`detail` 維持原意（多半是中文訊息，422 是欄位錯誤清單），`code` 是穩定字串，`request_id` 對得上 journal 那一行。SSE 端點每 20 秒送一行心跳註解。

| 方法 | 路徑 | 參數 | 回應 | 備註 |
|---|---|---|---|---|
| GET | `/healthz` | — | `{"status":"ok"}`；DB 不可用回 503 `{"status":"degraded"}` | 免登入；只探 DB（`SELECT 1`，3 秒逾時）；結果快取 5 秒 |
| GET | `/healthz/storage` | — | `{"storage":"disabled"\|"unknown"\|"ok"\|"degraded"}`；degraded 回 503 | **只回答本機直連**（對端 loopback、無代理 header、Host 為本機），其餘 404；給 `scripts/check_web_health.sh` 用（退出碼 6） |
| GET | `/healthz/llm` | — | `{"llm":"disabled"\|"unknown"\|"ok"\|"low"\|"exhausted"\|"auth_failed"\|"unreachable"\|"indeterminate"}`；後五種回 503，問答主答（`ASK_ANSWER_MODEL`）沒有用到 DeepSeek 時改回 200 並加 `_unused` 後綴。**不回任何金額** | **只回答本機直連**，其餘 404；查 DeepSeek `GET /user/balance`（只看 `LLM_BUDGET_CURRENCY` 那一筆，低於 `LLM_BALANCE_FLOOR` 為 low），ok 快取 600 秒、其餘 60 秒、每次最多等 4 秒；給 `scripts/check_web_health.sh` 用（`low` 為退出碼 7、其餘 503 為 8）。判定細節見 `app/services/llm_health.py` |
| GET／POST | `/login`、POST `/logout` | form `username`、`password`、`next`；第二步 form `step=totp`、`code`、`next` | 302／303 | 登入頁免登入；帳號不分大小寫。失敗回 `/login?error=1|locked|insecure|disabled|unavailable`（`disabled` 只在密碼正確時出現）。帳號開了兩步驟驗證時，密碼正確只發 5 分鐘、`Path=/login` 的簽章暫時憑證 `tf_mfa` 並導向 `/login?step=totp`，第二步驗證碼正確才發 session；錯誤回 `?step=totp&error=totp`（計入每 IP 失敗限流）、暫時憑證缺漏或逾時回 `?error=expired`。暫時憑證不可重放。登出只撤銷這一個 session |
| GET | `/api/me` | — | `{id, username, role, is_super, scopes, elevated_until, totp_enabled}` | 目前登入身分；`role` 為 `admin`／`user`，`scopes` 是實際生效的 scope，`elevated_until` 是本 session 權限提升的到期時刻（未提升為 null）。免登入開發模式回 `id: null`、`username: "dev"`、super admin 全部 scope |
| GET | `/api/status` | — | `{status, message}`；`status` 為 `ok`／`degraded`／`unknown`，`message` 是一句中文 | 任何登入的使用者（主平台帳號選單的小燈號）。刻意不含服務清單、主機、環境或錯誤細節（那些在 `/api/admin/ops/*`）。DB 探測（與 `/healthz` 共用 5 秒快取）失敗 → `degraded`；再問維運代理（逾時 3 秒、結論快取 30 秒），只看 critical 層：任一 `failed`／`not_found`／`idle` → `degraded`，否則有 `unknown` → `unknown`，其餘 `ok`；代理不可用 fail-open 回 200 `unknown`。一律 200 |
| POST | `/api/me/elevate` | JSON `password`、`code`（開了兩步驟驗證時必填） | `{elevated_until}` | 任何登入的使用者。與 `/api/admin/elevate` 同一套實作：10 分鐘、綁本 session；密碼正確但缺驗證碼 403 `totp_required`（不計失敗），密碼或驗證碼錯 403 `bad_password`，共用每 IP 失敗限流 |
| GET | `/api/me/totp` | — | `{enabled, pending}` | 自己的兩步驟驗證狀態；`pending`＝已產生 secret、尚未確認 |
| POST | `/api/me/totp/setup` | — | `{secret, otpauth_uri}` | 產生新的 secret（尚未啟用，secret 只回這一次）；已啟用 409 `totp_state`（換裝置請先關閉） |
| POST | `/api/me/totp/confirm` | JSON `code` | `{enabled, pending}` | 輸入驗證器顯示的第一個碼才啟用（RFC 6238，30 秒、6 位、前後各一步）；錯 400 `bad_totp` |
| POST | `/api/me/totp/disable` | — | `{enabled, pending}` | 關閉自己的兩步驟驗證；需近 10 分鐘內重新驗證過（403 `elevation_required`）。寫稽核 |
| GET | `/`、`/monitor`、`/help` | — | 302 到 `/app/search`、`/app/monitor`、`/app/help` | 舊入口相容 |
| GET | `/app`、`/app/{spa_path:path}` | — | SPA `index.html`（no-cache） | `frontend/dist` 不存在回 503；`/app/assets/` 免登入且 immutable 快取 |
| GET | `/api/stats` | — | `total_reports`、`total_chunks`、`markets`、`instrument_types`、`report_types`、`username` | 與 `/api/progress` 共用 15 秒 DB 快取；`username` 是目前登入者 |
| GET | `/api/progress` | — | `db`、`summary`、`takeaway`、`signal`、`evaluation`、`extraction`、`tagging`、`ingest`、`pipelines`、`orchestrator`、`sync`、`unit_failures` | 監控頁輪詢；`extraction_log` 缺表時 `extraction` 為 null。`evaluation.qa` 的 `total`／`checked`／`latest` 計所有 judge（覆蓋率語意，換 judge 不會驟降）；分數類 `judge_checked`／`degraded`／`below_min`／`avg_score`／`avg_n`（平均的樣本數，不含 degraded）只計現行 judge（`FAITHFULNESS_MODEL`），另帶 `judge_model`、`judge_since`（窗期內現行 judge 最早一筆的日期）、`other_judge_checked`（其他 judge 的筆數） |
| GET | `/api/markets` | — | `{"markets": [...]}` | 市場代碼清單 |
| GET | `/api/reports` | `market`、`instrument_type`、`relates_stock`、`relates_futures`、`report_type`、`sort`（`date_desc`）、`limit`（1–100，50）、`offset` | `{total, offset, items[]}` | 瀏覽（無查詢詞） |
| GET | `/api/search` | `q`（1–500 必填）、同上篩選、`sort`（`relevance`）、`limit`、`offset`、`passages`（1–6，3） | `{query, market, total, market_facets, lexical_truncated, results[]}` | 混合檢索 ＋ `rank_reports` |
| POST | `/api/ask` | JSON `question`（≤2000）、`conversation_id`、篩選欄位、`k`（1–20，8）、`regenerate_of`、`edit_of`、`request_id`、`locale`、`web` | SSE：`queued`→`status`→`sources`→`ext_sources`→`token`…→`followups`→`done`；婉拒走 `notice` | 併發上限 3、佇列 20（滿載 429 ＋ `Retry-After: 30`）。寫入的擁有者是目前登入者；帶別人的 `conversation_id`／`regenerate_of`／`edit_of`（含共用歷史）在開始串流前回 404，擁有權檢查失敗回 503，`conversation_id` 格式錯誤回 400；找不到的參照照舊當新題／新串 |
| POST | `/api/ask/stop` | JSON `question`、`conversation_id`、`partial_answer`、`sources`、`ext_sources`、`stages`、`regenerate_of`、`edit_of`、`request_id` | `{"qa_id"}` | 中止時把部分答案落 `qa_log`（擁有者為目前登入者）。參照規則同 `/api/ask`：別人的 `conversation_id`／`regenerate_of`／`edit_of` 回 404、擁有權檢查失敗回 503、格式錯誤 400 |
| POST | `/api/feedback` | JSON `qa_id`、`value`（`like`／`dislike`／`none`） | `{"ok"}` | 只能評自己的問答；別人的或不存在的回 `{"ok": false}`（兩者看不出差別） |
| GET | `/api/history` | `limit`（1–200，50） | 最近問答列 | 只回自己的（個別帳號上線前的共用歷史不顯示）；排除離題婉拒 |
| DELETE | `/api/history/{qa_id}`；POST `/api/history/{qa_id}/delete` | — | `{"ok"}` | POST 是相容 alias；別人的或不存在的回 `{"ok": false}` |
| GET | `/api/qa/{root_qa_id}/versions` | — | 同題所有版本 | 重新生成／編輯後的版本鏈；看不到任何一列（不存在或別人的）回 404 |
| GET | `/api/conversations` | `limit`（1–200）、`offset`、`q`（≤200 字） | 對話串清單（裸陣列，無 total） | `q` 比對整串的有效提問（不只標題），`%`／`_` 為字面字元；前端以「回來的筆數等於 limit」判斷有無下一頁；只列自己的對話串 |
| GET | `/api/conversations/{conversation_id}` | — | 該對話全部輪次（舊→新） | 看不到任何一列（不存在或別人的）回 404；前端遇 404 改當新對話 |
| DELETE | `/api/conversations/{conversation_id}`；POST `/api/conversations/{conversation_id}/delete` | — | `{"ok"}` | 以 `COALESCE(conversation_id, id)` 整批刪自己的 `qa_log`；別人的或不存在的回 `{"ok": false}` |
| GET | `/api/report/{report_id}/full` | — | `report_id`、`file_name`、`title`、`market`、`source`、`summary`、`report_date`、`report_type`、`has_file` | 研報原檔詳情（`web/routers/report_file.py`，與已移除的深度研報無關） |
| GET | `/api/report/{report_id}/file` | — | 原檔（PDF inline）或 302 到 presigned URL | `r2` 模式缺 key 回 503 |
| GET | `/api/reading/{file_hash}` | — | `ReadingDoc`（metadata ＋ 重點摘錄 ＋ 訊號，不含全文） | `file_hash` 須 64 hex |
| GET | `/api/reading/{file_hash}/text` | `chunk`（選填） | `ReadingText`（正典文字，超過 `READING_TEXT_MAX_CHARS` 截斷並標 `truncated`） | `text_sha256` 一律對完整文字算 |
| GET | `/api/reading/{file_hash}/similar` | `limit`（1–20，6） | 相似研報清單 | dense 近鄰 |
| GET | `/api/radar/instruments` | `market`、`q`、`limit`（1–100）、`offset`、`sort`（`latest`／`reports`／`brokers`／`code`）、`stance`、`with_consensus` | `{total, limit, offset, has_more, next_offset, items, facets, latest_report_date}` | 帶 `stance` 時 Python 端分頁；目標價只回方向 |
| GET | `/api/instrument/{code:path}/radar` | `market`（必填）、`window`（`30`／`90`／`180`／`all`，預設 `90`） | 共識、四維論點、近期事件、券商清單 | 未擷取回 200 `pending_extraction` |
| GET | `/api/instrument/{code:path}/radar/events` | `market`、`window`、`limit`（1–50）、`offset` | 事件列 | |
| GET | `/api/instrument/{code:path}/radar/brokers/{broker:path}` | `market`、`window` | 單一券商對該標的的歷史 | |
| GET | `/api/brief/latest` | — | `{status: ready|pending, brief, available_dates}` | 無簡報回 200 `pending` 不是 404 |
| GET | `/api/brief/dates` | `limit`（1–120，30） | `{"dates": [...]}` | |
| GET | `/api/brief/{brief_date}` | — | 同 latest | 該日無簡報 404 |
| GET | `/api/review/queue` | `kind`（`faithfulness`／`feedback`／`extraction`，必填）、`status`（`open`／`resolved`／`dismissed`／`all`，預設 `open`）、`limit`（1–100，20）、`offset`、`days`（1–365，30） | `{kind, total, limit, offset, has_more, next_offset, min_score, items}` | 限管理員。忠實度低分（只列現行 judge）、倒讚與抽取 `needs_review` 的佇列；`days` 只作用於前兩種。每筆帶 `review_status`、`review_note`、`verification`、`reviewed_at`、`reviewer`（最後處理人帳號，舊資料為 null）；未處理的列視為 `open`。問答項目只有中繼資料（`qa_id`、`created_at`、`faithfulness_score`、`feedback`、`judge_model`）與 `asker_code`（不可逆的提問者代號：以 session secret 做 HMAC，同一人同一代號、看不出是誰；共用歷史為 null），刻意不回提問原文、回答、帳號名稱與 `conversation_id`；抽取另帶以現行門檻重算的 `review_reasons`。原始品質訊號不因處理狀態改變 |
| PUT | `/api/review/{kind}/{subject_id}` | `kind` 同上；`subject_id` 為 UUID；JSON `status`（必填：`open`／`resolved`／`dismissed`）、`note`（最多 1000 字）、`verification`（`untested`／`passed`／`failed`） | `{kind, subject_id, status, note, verification, updated_at, reviewer}` | 記錄人工處理結果；送 `open` 可重新打開。限管理員。驗證結果由人填寫，不會重跑評測或抽取；記下處理人（`reviewer_user_id`）並寫一列 `admin_audit_log`（`review.update`，不含註記全文）。不存在的項目回 404 |
| POST | `/api/review/qa/{qa_id}/access` | `qa_id` 為 UUID；無 body | `{qa_id, kinds, created_at, question, answer}` | 讀**一筆**待複核問答的原文（不含對話串其他輪）。另要 `qa_content.read`（403 `missing_scope`）。只限此刻在佇列裡的項目：現行 judge 低於門檻或倒讚、`active` 且未中止、近 30 天（窗期固定，不收 `days`）；其餘一律 404、不分是否存在。`kinds` 是它在哪幾種佇列裡。每次讀取在同一筆交易寫一列 `admin_audit_log`（`qa_content.read`，detail 只有 `qa_id` 與 `kinds`），稽核寫不進去就不回內容；回應 `Cache-Control: no-store` |
| GET | `/api/admin/users` | — | `{items: [{id, username, role, enabled, created_at, updated_at, password_changed_at, last_login_at, last_seen_at, active_sessions, is_super, scopes}]}` | `accounts.manage`。不含任何密碼衍生值；`scopes` 只列另外授予的 |
| POST | `/api/admin/users` | JSON `username`（2–64 字元，文字、數字與 `. _ @ -`，不分大小寫唯一）、`password`（10–256 字元、前後不可空白）、`role`（`admin`／`user`，預設 `user`） | 201 帳號一列 | 限管理員。400 輸入不合法、409 帳號已存在 |
| PATCH | `/api/admin/users/{user_id}` | JSON `role`、`enabled`（至少一個） | 帳號一列 | 限管理員。停用時撤銷該帳號所有 session；409：最後一位啟用中的管理員不能停用或降級、不能停用自己或拿掉自己的管理員權限；404 帳號不存在 |
| POST | `/api/admin/users/{user_id}/password` | JSON `password` | 帳號一列 | 限管理員。重設密碼並撤銷該帳號所有 session |
| POST | `/api/admin/users/{user_id}/logout` | — | `{revoked}` | 限管理員。強制登出（撤銷所有 session，帳號仍可重新登入） |
| PUT | `/api/admin/users/{user_id}/privileges` | JSON `is_super`、`scopes`（`qa_content.read`／`ops.operate` 的完整清單，取代；至少一個欄位） | 帳號一列 | super admin＋近 10 分鐘內重新驗證過（403 `super_required`／`elevation_required`）。只授予給啟用中的管理員（400 `invalid_input`）；409 `self_lockout` 不能拿掉自己的 super、`last_super` 至少保留一位 super admin |
| POST | `/api/admin/elevate` | JSON `password`、`code`（開了兩步驟驗證時必填） | `{elevated_until}` | 任何管理員。重新驗證密碼（＋驗證碼），取得綁在本 session 的 10 分鐘權限提升；缺驗證碼 403 `totp_required`，密碼或驗證碼錯 403 `bad_password`，與登入共用每 IP 失敗限流（429 `rate_limited`）。成功與失敗都寫稽核 |
| POST | `/api/admin/users/{user_id}/deletion` | — | `{id, user_id, username, requested_by, requested_by_username, requested_at, execute_after, cancelled_at, executed_at, status}` | `accounts.manage`＋近 10 分鐘內重新驗證過。立即停用並撤銷所有 session，排程 24 小時後由 `scripts/execute_deletions.py` 執行；409 `self_lockout`／`last_admin`／`last_super`／`deletion_pending`，403 `super_required`（對象是 super admin），404 |
| POST | `/api/admin/users/{user_id}/deletion/cancel` | — | 刪除排程一筆 | `accounts.manage`。撤銷窗口內取消並還原提出前的啟用狀態（被撤銷的 session 不會復活）；404 `no_pending_deletion`，已到執行時刻 409 `deletion_window_closed` |
| GET | `/api/admin/deletions` | `status`（`pending`／`all`，預設 `pending`） | `{items: [刪除排程…]}` | `accounts.manage`。新的在前；執行後 `username` 是 `deleted-<uuid>` |
| POST | `/api/admin/users/{user_id}/totp/reset` | — | 帳號一列 | `accounts.manage`＋已提升。替遺失驗證器的人關閉兩步驟驗證；對象是 super admin 時只有 super admin 能做 |
| GET | `/api/admin/audit/verify` | — | `{ok, total, head_id, head_hash, broken_ids}` | `audit.read`。逐列重算稽核雜湊鏈；`broken_ids` 最多 20 筆 |
| GET | `/api/admin/audit` | `limit`（1–200，50）、`offset` | `{total, limit, offset, has_more, next_offset, items: [{id, actor_user_id, actor_username, action, target_type, target_id, detail, created_at}]}` | 限管理員。新的在前；`actor_user_id` 為 null 表示 CLI（`scripts/create_admin.py`）。`action`：`user.create`、`user.set_role`、`user.enable`、`user.disable`、`user.reset_password`、`user.force_logout`、`user.set_privileges`、`user.totp_enable`、`user.totp_disable`、`user.totp_reset`、`user.delete_requested`、`user.delete_cancelled`、`user.delete_executed`、`user.delete_replayed`、`session.elevate`、`session.elevate_failed`、`review.update`、`report.hide`、`report.restore`、`data.export`（刪除相關的 `detail` 只記數量，不記帳號名稱；`data.export` 的 `detail` 是 `{kind, format, filters, row_count, truncated}`） |
| GET | `/api/admin/reports` | `q`（標題／檔名／券商關鍵字，`%`、`_` 視為字面）、`hidden`（`true`／`false`，省略＝全部）、`publication`（`draft`／`published`，省略＝全部）、`limit`（1–200，50）、`offset` | `{total, limit, offset, has_more, next_offset, items: [{report_id, file_hash, file_name, title, source, market, report_date, created_at, hidden, hidden_reason, visibility_updated_by, visibility_updated_at, publication}]}` | `reports.manage`。入庫新→舊；含非研究檔與未發布的上傳草稿（`publication=draft`；沒有可見性列的研報一律 `published`） |
| PUT | `/api/admin/reports/{file_hash}/visibility` | JSON `hidden`（必填）、`reason`（隱藏時必填、最多 500 字；恢復可省略） | `{file_hash, hidden, reason, updated_by, updated_at}` | `reports.manage`。以 `file_hash` 為鍵（重新入庫換 report_id 仍有效）；被隱藏（或尚未發布的上傳草稿）的研報從檢索、問答、閱讀頁（404）、雷達、總覽、簡報與原檔（404）全部排除，批次照常處理、恢復即生效。與稽核（`report.hide`／`report.restore`，detail 不含原因全文）同一筆交易；400 `invalid_input` 原因不合法、404 研報不存在、409 `report_is_draft` 尚未發布的上傳草稿（隱藏與恢復都拒絕，恢復不會順手發布） |
| GET | `/api/admin/ops/services` | — | `{environment, host, checked_at, items: [{name, kind, tier, target, timer, actions, group, description, summary, error, systemd, container, timer_state}]}` | `ops.read`。唯讀，經維運代理（`ops_agent/`，Unix socket）查 Service Catalog（`deploy/ops/services.prod.toml`）的全部服務；`summary` 是 `running`／`idle`／`failed`／`transitioning`／`not_found`／`unknown`，判讀看 `systemd`（`systemctl show` 的原始屬性與時間戳）或 `container`（`docker inspect` 的 State）。代理不可用回 503 `ops_agent_unavailable`（其他功能不受影響） |
| GET | `/api/admin/ops/services/{name}` | `name`（catalog 名稱，小寫英數與 `-`） | 一個服務的同上欄位＋`checked_at` | `ops.read`。不在 catalog 回 404 `ops_service_not_found` |
| GET | `/api/admin/ops/services/{name}/logs` | `since`（`15m`／`2h`／`1d` 或帶時區的 ISO 8601，最多 7 天，預設 `1h`）、`lines`（1–1000，200） | `{name, kind, tier, target, since, lines, truncated, entries, checked_at}` | `ops.read`。systemd 走 `journalctl -o short-iso`、容器走 `docker logs --timestamps`；回應超過上限時從舊的那端截掉（`truncated`），形似祕密的片段遮成 `<redacted>`。400 `invalid_params`、504 `ops_timeout` |
| GET | `/api/admin/ops/dependencies` | — | `{environment, host, checked_at, nodes: [{name, kind, tier, target, description, summary, health, health_reason, probe, observed_at, depends_on, dependents, layer, affected, impacted_by}], edges: [{dependent, dependency, broken}], down, root_causes, affected}` | `ops.read`。服務依賴圖：依賴關係只來自 Service Catalog（`deploy/ops/services.*.toml` 的 `depends_on` 與 `[[externals]]`，代理 `--check` 驗證引用存在、不自我依賴、無環），經維運代理一次 `list` 取得。`kind` 多了 `external`（R2、DeepSeek、NAS、Slack、對外入口等，代理不查也不動）；`health` 是 `ok`／`degraded`／`down`／`unknown`：常駐服務停著或失敗、unit 不存在是 `down`，排程 oneshot 待命是 `ok`；外部依賴看 catalog 指定探針最後一次的退出碼，沒有探針、退出碼不在對照表（可能被優先序更高的狀況蓋住）、探針 30 分鐘沒有新結果一律 `unknown`。只有 `down` 往下游傳播：`affected`／`impacted_by` 是（間接）依賴到 down 節點的下游，`root_causes` 是上游沒有 down 的 down 節點；`layer` 0＝不依賴任何節點。代理不可用 503 `ops_agent_unavailable` |
| POST | `/api/admin/ops/services/{name}/restart` | `name`；無 body、無 query | 202 `{name, kind, tier, target, group, action, state: "scheduled", previous_invocation_id, previous_active_enter_at, previous_exec_main_start_at, execute_after_ms, accepted_at, checked_at}` | `ops.operate`（另外授予）＋elevated（10 分鐘內重新驗證密碼，否則 403 `elevation_required`）。v1 只有 `web`；代理先回應、`execute_after_ms` 後才 `systemctl restart --no-block`，前端輪詢 `GET /api/admin/ops/services/{name}` 直到 `systemd.invocation_id` 換掉。PostgreSQL／nginx／cloudflared 一律 403 `action_not_allowed`；同 execution group 轉換中 409 `already_running`（不排隊）；不在 catalog 404 `ops_service_not_found`；代理不可用 503 `ops_agent_unavailable`。成功與被拒都寫稽核 `ops.restart` |
| POST | `/api/admin/ops/services/{name}/run` | `name`；無 body、無 query | 202，同上但 `action: "run"`、`state: "queued"` | 同上的權限與錯誤碼。只限 catalog 白名單的 oneshot（`sync`、`backup`、`freshness`、`audit`、`r2-reconcile`），`systemctl start --no-block`，等同 timer 觸發、不收參數。同 group 有 unit 在跑、或（sync）LLM 批次 flock／sync PID 檔被持有 → 409 `already_running`；鎖檔看不到 503 `ops_lock_unavailable`。稽核 `ops.run` |
| GET | `/api/admin/jobs` | `service`（catalog 名稱）、`unit`、`state`（`running`／`finished`／`lost`）、`result`（systemd 的 Result，如 `success`、`exit-code`）、`since`／`until`（ISO 8601，沒時區當 UTC；預設最近 7 天，範圍最多 90 天）、`limit`（1–200，50）、`offset`（0–10000） | `{since, until, total, limit, offset, has_more, next_offset, items: [{host, unit, service, invocation_id, state, started_at, finished_at, duration_seconds, result, exit_status, exec_main_code, last_seen_at}]}` | `ops.read`。有 timer 的 oneshot 每次執行（依開始時間新→舊），來自 DB 投影 `job_execution`（收集器寫 spool、`scripts/load_observations.py` 每 5 分鐘匯入，最多晚幾分鐘）。「跑過」比照 `scripts/verify_oneshot_ran.sh`；`lost`＝沒觀測到結束、之後已有新一輪（結果不明）。範圍顛倒或過長 400 `invalid_params` |
| GET | `/api/admin/observations` | `scope`（`host`／`container`／`service`）、`subject`（`host`、`fs:<路徑>` 或 catalog 名稱）、`metric`（如 `cpu_pct`、`mem_used_pct`、`psi_io_some_avg60`、`active_state`）、`since`／`until`（預設最近 1 小時，範圍最多 90 天＝保留期）、`limit`（1–5000，500） | `{since, until, limit, truncated, resolution, items: [{observed_at, host, scope, subject, metric, value, state, detail, sample_count, value_min, value_max, value_last, first_state, state_changes}]}` | `ops.read`。觀測時間序列（新→舊），來自 DB 投影 `service_observation`；粒度依 `since` 距今自動選、`resolution` 標示實際用的：24 小時內 `raw`（逐筆）、7 天內 `5m`、更早 `1h`（與保留期分段一致，revision 0007），聚合的桶 `observed_at` 是桶起點、`value` 是平均、`state` 是最後的狀態，`sample_count` 等欄位只在聚合時有值；數值型指標在 `value`、狀態型（容器 `status`／`health`、unit `active_state`）在 `state`，附帶屬性只掛在狀態列的 `detail`。超過 `limit` 時 `truncated=true`。不是告警的真相來源（告警只有 P5） |
| GET | `/api/admin/incidents` | `status`（`firing`／`resolved`／`lost`）、`component`（P5 的元件名，如 `web`、`monitor`、`edge`、`container`、`host_monitor`）、`since`／`until`（ISO 8601，沒時區當 UTC；預設最近 30 天，範圍最多 366 天）、`limit`（1–200，50）、`offset`（0–10000） | `{since, until, total, limit, offset, has_more, next_offset, items: [{incident_id, host, component, kind, probe_unit, status, severity, reason, summary, opened_at, last_event_at, resolved_at, duration_seconds, event_count}]}` | `ops.read`。P5（`scripts/incident_handler.sh`）事件的 DB 投影 `incident`（依開場時間新→舊；與區間重疊：firing 一律算、resolved 看恢復時間、lost 看最後一則轉換）。`lost`＝沒收到 RESOLVED、同元件已有更晚的事件（結束時間不明）。**不是告警的真相來源**：P5 寫 spool、loader 每 5 分鐘匯入，spool 寫入失敗的那筆會缺；即時告警只看 Slack 與 P5 狀態檔。範圍顛倒或過長 400 `invalid_params` |
| GET | `/api/admin/incidents/{incident_id}` | `incident_id`（`<host>:<component>:<first_seen epoch>`） | `{…清單欄位, events: [{event_id, occurred_at, action, severity, reason, status, summary, notified, journal_excerpt, journal_truncated, journal_since, journal_until, journal_units}], events_truncated}` | `ops.read`。事件的每一則狀態轉換（`FIRING`／`REMINDER`／`ESCALATED`／`RESOLVED`，舊→新，最多 1000 則）；FIRING 帶事件前約 10 分鐘、RESOLVED 帶開場後約 10 分鐘的 journal 片段（P5 當下擷取，有大小上限、已遮祕密；完整 log 以 journald 為準）。不存在 404 `not_found`；id 格式不合 422 |
| GET | `/api/admin/data-health` | — | `{generated_at, overall, freshness: {status, exit_code, error, findings: [{asset, label, state, latest, age_days, threshold_days, detail}]}, db_audit: {status, available, unavailable_reason, finished_at, age_hours, stale, exit_code, error, skipped, findings: [{key, label, severity, count, detail}]}, r2_reconcile: {status, available, unavailable_reason, finished_at, age_hours, stale, exit_code, mode, dry_run, limit, orphan_scan, stats, issues: [{type, ref}], issues_total}}` | `ops.read`。唯讀，整份快取 60 秒。`status`／`overall` 是 `ok`／`warn`／`fail`／`unknown`（overall 取最嚴重的）。批次新鮮度即時判讀（與 `scripts/check_batch_freshness.py` 同一組函式與門檻，`app/services/batch_freshness.py`；DB 查不到時只回管線心跳那筆、`unknown`）；資料完整性稽核與 R2 對帳**不在 web 裡跑**，讀 `scripts/db_audit.py`、`scripts/reconcile_object_storage.py` 最後一次寫的結果檔（`data/health/*.json`，`app/services/data_health.py`）。稽核「warn 也算失敗」；對帳的連不上／缺 key／key 不符是 `fail`，缺檔、大小或 sha 不符、orphan 是 `warn`。結果檔比排程週期舊（稽核 48 小時、對帳 9 天）時 `stale=true`、狀態至少 `warn`；還沒有結果檔 `available=false`（`unavailable_reason: missing`），不是錯誤 |
| GET | `/api/admin/llm-usage` | `since`／`until`（ISO 8601，沒時區當 UTC；預設最近 30 天，範圍最多 366 天） | `{since, until, timezone, source: {exists, size_bytes, scanned_bytes, truncated, lines_scanned, lines_invalid, lines_in_range, earliest_ts, latest_ts}, totals, by_day: [{day, …}], by_task: [{task, …}], by_model: [{model, …}], rows: [{day, task, model, …}], rows_truncated, cost_available}`；每組 token 欄位是 `calls, failures, prompt_hit_tokens, prompt_miss_tokens, completion_tokens, reasoning_tokens, calls_without_tokens, total_ms, cost` | `ops.read`。彙總 `data/llm_usage.jsonl`（**批次**的 LLM 呼叫；線上問答不寫這份檔），日期以台北時間切日、模型取 `model_req`。只回彙總：不含 prompt 雜湊、`file_hash`、`report_id`。只讀檔尾 16 MiB、最多 10 萬行（超過 `truncated=true`，涵蓋範圍看 `earliest_ts`），任務／模型最多 200 個相異值（其餘併成「(其他)」）、明細最多 5000 列。檔案不存在回全零。寫入端不記費用，`cost` 只在行內帶數值 `cost` 時加總（否則 null）。範圍顛倒或過長 400 `invalid_params` |
| GET | `/api/admin/export/audit.csv` | `limit`（1–10000，10000） | 回 `text/csv`（UTF-8 BOM，檔名 `report-mark-<種類>-<YYYYMMDD>.csv`，標頭 `X-Export-Rows`、`X-Export-Truncated`）：`id, created_at, actor_user_id, actor_username, action, target_type, target_id, detail`（detail 為 JSON） | `audit.read`。新→舊，超過 `limit` 只取最新的（`X-Export-Truncated: true`）。每次匯出先寫一筆稽核 `data.export`（種類、篩選條件、筆數、是否達上限；不含內容），寫不進去 503 `export_audit_failed` 不匯出；字串儲存格以 `=`、`+`、`-`、`@` 開頭時加 `'` 防公式注入 |
| GET | `/api/admin/export/users.csv` | `limit`（1–10000，10000） | 回 `text/csv`（UTF-8 BOM，檔名 `report-mark-<種類>-<YYYYMMDD>.csv`，標頭 `X-Export-Rows`、`X-Export-Truncated`）：`id, username, role, enabled, is_super, scopes, totp_enabled, created_at, updated_at, last_login_at, last_seen_at, active_sessions, deletion_execute_after` | `accounts.manage`。不含已刪除帳號；欄位是白名單，**不含任何密碼或 TOTP 衍生值**（連 `password_changed_at` 也不列）；`scopes` 只列另外授予的，以 `;` 分隔。每次匯出先寫一筆稽核 `data.export`（種類、篩選條件、筆數、是否達上限；不含內容），寫不進去 503 `export_audit_failed` 不匯出；字串儲存格以 `=`、`+`、`-`、`@` 開頭時加 `'` 防公式注入 |
| GET | `/api/admin/export/reports.csv` | `q`、`hidden`、`publication`（同 `/api/admin/reports`）、`limit`（1–10000，10000） | 回 `text/csv`（UTF-8 BOM，檔名 `report-mark-<種類>-<YYYYMMDD>.csv`，標頭 `X-Export-Rows`、`X-Export-Truncated`）：`file_hash, report_id, title, file_name, source, market, report_date, created_at, hidden, hidden_reason, visibility_updated_by, visibility_updated_at, publication` | `reports.manage`。入庫新→舊；含隱藏原因。每次匯出先寫一筆稽核 `data.export`（種類、篩選條件、筆數、是否達上限；不含內容），寫不進去 503 `export_audit_failed` 不匯出；字串儲存格以 `=`、`+`、`-`、`@` 開頭時加 `'` 防公式注入 |
| GET | `/api/admin/export/incidents.csv` | `status`、`component`、`since`／`until`（同 `/api/admin/incidents`：預設最近 30 天、最多 366 天）、`limit`（1–10000，10000） | 回 `text/csv`（UTF-8 BOM，檔名 `report-mark-<種類>-<YYYYMMDD>.csv`，標頭 `X-Export-Rows`、`X-Export-Truncated`）：`incident_id, host, component, kind, probe_unit, status, severity, reason, summary, opened_at, last_event_at, resolved_at, duration_seconds, event_count` | `ops.read`。依開場時間新→舊；範圍顛倒或過長 400 `invalid_params`。每次匯出先寫一筆稽核 `data.export`（種類、篩選條件、筆數、是否達上限；不含內容），寫不進去 503 `export_audit_failed` 不匯出；字串儲存格以 `=`、`+`、`-`、`@` 開頭時加 `'` 防公式注入 |
| GET | `/api/admin/export/jobs.csv` | `service`、`unit`、`state`、`result`、`since`／`until`（同 `/api/admin/jobs`：預設最近 7 天、最多 90 天）、`limit`（1–10000，10000） | 回 `text/csv`（UTF-8 BOM，檔名 `report-mark-<種類>-<YYYYMMDD>.csv`，標頭 `X-Export-Rows`、`X-Export-Truncated`）：`host, unit, service, invocation_id, state, started_at, finished_at, duration_seconds, result, exit_status, exec_main_code, last_seen_at` | `ops.read`。依開始時間新→舊；範圍顛倒或過長 400 `invalid_params`。每次匯出先寫一筆稽核 `data.export`（種類、篩選條件、筆數、是否達上限；不含內容），寫不進去 503 `export_audit_failed` 不匯出；字串儲存格以 `=`、`+`、`-`、`@` 開頭時加 `'` 防公式注入。問答原文刻意沒有匯出端點 |

SSE 事件欄位見 `docs/WORKFLOW.md` 的 Web API 契約；單一真相 `tests/fixtures/sse_events.json`。

## 設定（環境變數）

repo 根 `.env`（範本 `.env.example`）由 `web/env_loader.py` 讀取，不做 shell 展開；批次腳本不讀它，生產批次的環境在 `/etc/default/report-mark-sync`。

| 變數 | 預設 | 說明 |
|---|---|---|
| `REPORT_MARK_BENCH_USERNAME`、`REPORT_MARK_BENCH_PASSWORD` | 無 | 只給 `scripts/bench_load.py` 登入用（建議開一個壓測專用的一般帳號）。帳號本身在 `research.app_user`，由 `scripts/create_admin.py` 或管理頁建立；舊的 `REPORT_MARK_ACCESS_USERNAME`／`_PASSWORD` 已不再讀取，還留著的話啟動時記 warning |
| `REPORT_MARK_SESSION_SECRET` | 隨機 | HMAC 金鑰；未設則每次重啟登出所有人 |
| `REPORT_MARK_SESSION_EPOCH` | 空 | 改任何新值即全員登出 |
| `REPORT_MARK_TRUSTED_PROXY_CIDRS` | `127.0.0.1/32,::1/128` | 可信代理網段；走 Cloudflare Tunnel 時必填（WSL 的 docker 網段） |
| `REPORT_MARK_EDGE_SECRET` | 空（停用） | 邊緣共享祕密，與 `deploy/.env` 的 `EDGE_SECRET` 逐字相同；與 CIDR 是 OR |
| `REPORT_MARK_DB_URL` | `postgresql+asyncpg://postgres:postgres@localhost:5436/research` | 唯一留在 `app/services/db.py` 的 getenv |
| `LOG_LEVEL` | `INFO` | 調到 WARNING 會失去登入成功稽核、`qa_timing`、`/api/*` 請求耗時與檢索遙測。每行帶 `rid=`，與回應的 `X-Request-Id` 相同 |
| `DB_POOL_SIZE`、`DB_MAX_OVERFLOW`、`DB_POOL_TIMEOUT`、`DB_POOL_RECYCLE` | 5、15、10、1800 | per-process 上限 20；改併發前依 `.env.example` 算式重算 |
| `DB_STATEMENT_TIMEOUT_MS`、`DB_IDLE_TX_TIMEOUT_MS`、`DB_MAINTENANCE_STATEMENT_TIMEOUT_MS` | 60000、0、0 | idle 預設 0 是刻意的（sync 在交易內 spawn CLI）；維運長查詢走 `relax_statement_timeout()` |
| `EMBED_MAX_CONCURRENCY`、`EMBED_TORCH_THREADS` | 1、0 | 嵌入序列化；`/api/search`、雷達、閱讀頁沒有併發閘 |
| `LLM_HTTP_TOTAL_TIMEOUT` | `600` | DeepSeek 串流的牆鐘總時限（秒）；吐字後到期＝截斷並附註，CLI 路徑不讀 |
| `LLM_BUDGET_CURRENCY`、`LLM_BALANCE_FLOOR` | `CNY`、`70` | 只有 web 讀（`/healthz/llm`，設在 repo 根 `.env`）：只看餘額裡這個幣別那一筆，低於門檻回 503 → 探針退出碼 7（用罄、認證失敗等停擺為 8）。月上限 ¥350 是儲值紀律，不是旋鈕（`docs/production_resilience.md`） |
| `LLM_PROVIDER`、各任務 `*_MODEL`（`ASK_ANSWER_MODEL`、`ASK_WEB_MODEL`、`TAG_MODEL`、`SUMMARY_MODEL` 等 14 個） | `deepseek`、查表 | 任務旋鈕非空就用，否則查 provider 的預設表（`app/services/llm_models.py`）；`deepseek` 表除網搜外都是 `deepseek-flash`（兩個 judge 自 2026-09 起也是）；未設、空值都當成 `deepseek`，未知值線上當成 `deepseek`（記 ERROR）、批次與評測預檢 rc=2 並印原始值。`claude_cli`（遷移前的表）與 `claude_only`（遷移期的回退值）仍是合法值，但 claude CLI 已於 2026-09-23 放棄，設了等於 LLM 全部停擺。清單與語意見 `.env.example` |
| `ASK_*`、`QA_*` | 見 `docs/ARCHITECTURE.md` 設定旋鈕 | 問答脈絡、選篇、路由模型、網搜（`ASK_ENABLE_WEB`、`ASK_WEB_TIMEOUT`）、agentic 補查 |
| `ASK_RERANK_*`、`RERANK_MODEL` | 開、16 候選 | rerank fail-open；50 候選在 2026-09-29 的線上壓測易逾時 |
| `ASK_FAITHFULNESS_*`、`FAITHFULNESS_MIN`、`FAITHFULNESS_MODEL`、`FAITHFULNESS_TIMEOUT` | 開、0.9、`deepseek-flash`（`claude_cli` 表是 `claude-haiku-4-5`）、`ASK_FAITHFULNESS_TIMEOUT` 依 judge（DeepSeek 90、Claude CLI 240） | 問答忠實度抽查；關掉或壞掉都不會有錯誤訊息，只標 `degraded`（`evaluation.degraded_reason` 說原因）。`FAITHFULNESS_MIN` 讀不到時退回舊名 `REPORT_FAITHFULNESS_MIN`。`FAITHFULNESS_MODEL` 不再沿用 `ASK_INTENT_MODEL`；換掉等於換尺，監控卡、待複核與 `scripts/eval_faithfulness.py` 只計現行 judge（缺 `judge_model` 的舊列視為 `claude-haiku-4-5`，自 judge 切成 DeepSeek 起歸「其他 judge」）。`ASK_FAITHFULNESS_TIMEOUT` 是每次 judge 呼叫的總期限；未設時依 judge 決定（DeepSeek 90，依探測延遲訂；Claude CLI 240；計算在 `app/config.py`），設了就照設的值 |
| `EXTRACTOR`、`EXTRACTION_REVIEW_MIN`、`EXTRACTION_REVIEW_MIN_COVERAGE`、`EXTRACTION_REVIEW_MAX_GARBLED` | `pypdf`、0.6、0.30、0.02 | 抽取器（生產 sync 環境檔設 `pdfplumber`）與 `needs_review` 三道門檻（只標記不擋，`docs/EXTRACTION.md` §5） |
| `OBJECT_STORAGE_MODE`、`R2_ENDPOINT_URL`、`R2_BUCKET`、`R2_ACCESS_KEY_ID`、`R2_SECRET_ACCESS_KEY`、`R2_PRESIGN_TTL_SECONDS` | `local` | 非 local 缺任一 fail-closed；TTL 上限 3600 |
| `ASK_MAX_QUEUE`、`SSE_HEARTBEAT_INTERVAL` | 20、20 | web 層旋鈕 |
| `RADAR_CATALOG_CACHE_TTL` | 60 | 雷達目錄回應快取秒數；0 停用 |
| `OPS_AGENT_ENVIRONMENT`、`OPS_AGENT_SOCKET`、`OPS_AGENT_TIMEOUT` | `production`、依環境（`/run/report-mark-ops/agent.sock`，staging 是 `/run/report-mark-ops-staging/agent.sock`、development 是 `/run/report-mark-ops-dev/agent.sock`）、20 | 維運代理的 client（`web/ops_client.py`）。環境是請求裡宣告的、代理會比對；拼錯的環境值＝停用（維運端點回 503），不退回 production |
| `SKIP_WARMUP`、`DEV_NO_AUTH` | — | 只從 `os.environ` 讀且判 `== "1"`，不要寫進環境檔 |

新旋鈕放 `app/config.py`（frozen dataclass ＋ `os.getenv`）。`REPORT_MARK_*` 前綴只給 auth／DB；既有帶前綴的例外（`REPORT_MARK_RERANK_*`、`REPORT_MARK_MAX_TRACKED_FAIL_IPS`、`REPORT_MARK_ROOT`、`REPORT_MARK_ALERT_WEBHOOK`）是 live 的，不要改名。

## 開發與測試

```bash
uv run pytest -q                       # 缺 frontend/dist 會紅（不是 skip）
SKIP_SPA_TESTS=1 uv run pytest -q      # 不想先 build 前端時
uv run ruff check .                    # E,F,I；120 字元；刻意不跑 ruff format
cd frontend && npm test                # vitest
cd frontend && npm run typecheck       # tsc --noEmit
cd frontend && npm run lint            # eslint

python3 eval/question_contract.py eval/ragas_questions.json  # 零 LLM：驗題集契約並印 sha256
uv run python eval/run_ragas.py --concurrency 1   # 探索性問答評測，預設寫 eval/candidate-ragas.json；會呼叫付費 API
uv run python eval/run_ragas.py --generator-model deepseek-flash --repeat 3 --dump-io data/eval_frozen/example
make eval-compare BASE=eval/baselines/example.json CAND=eval/candidate-ragas.json
uv run python scripts/judge_agreement.py --dry-run   # judge 描述性校準（歷史 haiku 判定當參考、只描述；正式跑會呼叫付費 API）
uv run python eval/observe_switch.py --switch-at <切換時點> --dry-run   # DeepSeek 切換後批次產出觀測（零 LLM、唯讀；去掉 --dry-run 出報告）
```

- CI 四個 job 全為必要檢查（`.github/workflows/ci.yml`）：前端測試（tsc ＋ vitest）、後端測試（pytest）、schema 契約（PostgreSQL）、secret 掃描（gitleaks）。前端 job 把 `frontend/dist` 傳給後端 job，SPA 測試對真 build 驗證；後端設 `HF_HUB_OFFLINE=1`、安裝 CJK 字型並設 `REPORT_MARK_REQUIRE_CJK=1`（`tests/test_extraction_layout.py` 的 CjkTests 用 weasyprint 渲染中文測試 PDF，不准退回 skip）；schema job 對空庫跑 `alembic upgrade head`（含單一 head、drift checker 自我一致、既有庫 stamp 演練）並對帳 `db/expected_constraints.txt`。required check 名稱等於 job 的中文 `name`，改了要同步 GitHub 分支保護。
- 測試不連網、不載模型：LLM、嵌入、DB、檔案系統一律用假物件。async 測試用 `unittest.IsolatedAsyncioTestCase`，不用 pytest-asyncio。端點走 HTTP 層測。
- 測試絕不可寫 repo 根的真實環境檔（`tests/conftest.py` 會還原並 fail）。
- 評測 `eval/` 刻意不進 CI（會呼叫付費 API；CI 不連網）。`make eval-compare` 退出碼是結論：0 無劣化、1 劣化、2 不可比、3 有未分類指標。門檻 F>0.9／CP>0.8／AR>0.55 是政策；最新基準線 `eval/baselines/baseline-2026-09-29-jdsflash-gdsflash.json`（DeepSeek judge）。
- **DeepSeek 切換後觀測**（`eval/observe_switch.py`，零 LLM、唯讀）：切換後第 7 天、第 14 天各跑一次，比切換前 30 天的 Claude 產出與切換後的 DeepSeek 產出。主指標是摘錄的任一方式錨定成功率（差值 CI 下界 ≥ −5pp）；摘錄產出率、訊號非 rejected 率、標題／摘要填補率差值 CI 下界 ≥ −2pp；標註 `skip_non_research`／market=None 比例差值 CI 上界 ≤ 0。另以用量紀錄零 LLM 判 content_filter 比例（Wilson 上界 ≤ 1%）、截斷／401／402 為 0 與斷路器標記。Claude 群排除 CLI 失效到切換的事故空窗（`--claude-until`），缺值率只把該批次自己模型的產出算已填；摘錄錨定排除擷取後被回填的研報，全庫回填期間不要跑。**總表全部通過不等於批次 A 觀測完成**（幻覺率、花費金額等見報告「未涵蓋項目」）。只輸出判讀、不做任何切換；劣化時只能修 prompt 或換 `deepseek-v4-pro`。從 worktree 跑時用 `--usage-log`／`--tags-dir`／`--breaker-file` 指到部署目錄。細節見 `docs/WORKFLOW.md`「DeepSeek 切換後觀測」。
- **judge 自 2026-09 起是 DeepSeek（`deepseek-flash`，量尺系譜 `deepseek-2026-09`）。** 舊 Claude haiku 結果不能相比（回 2；門檻數值不變）。`eval/ragas_questions.json` v2 有 18 題 corpus QA；2026-09-29 從正式庫凍結 15,255 份研報／616,769 個 chunk（快照 ID `sha256:969ba5277b99fb57d05262d19ceeb9fffe813c2f2ac7e0cc9ab6c87a7768373d`），在隔離 PostgreSQL 逐題確認 18/18 題有來源。基準線每題重跑 3 次，F=0.967、CP=0.873、AR=0.691，無錯誤、無缺來源；同快照獨立候選比較退出碼 0。這組評測設定是 `rerank_top_m=0`、`agentic=false`，只量固定脈絡下的答案品質；線上含重排與補查的整體延遲要另用 `scripts/bench_load.py` 走 HTTP 量。快照原檔和含研報片段的檢查點留在未版控資料目錄，版控只保留摘要分數與設定。

  ```bash
  # 兩次執行使用同一個正式語料快照；SNAPSHOT_ID 填實際備份 ID 或內容雜湊。
  SNAPSHOT_ID=actual-immutable-corpus-snapshot-id
  BASELINE=eval/baselines/baseline-YYYY-MM-DD-jdsflash-gdsflash.json
  uv run python eval/run_ragas.py --dataset eval/ragas_questions.json --corpus-id "$SNAPSHOT_ID" \
    --generator-model deepseek-flash --judge-model deepseek-flash --concurrency 1 --repeat 3 \
    --complete-only --checkpoint-dir data/eval_frozen/deepseek-baseline-checkpoints --out "$BASELINE"
  uv run python eval/run_ragas.py --dataset eval/ragas_questions.json --corpus-id "$SNAPSHOT_ID" \
    --generator-model deepseek-flash --judge-model deepseek-flash --concurrency 1 --repeat 3 \
    --match-baseline "$BASELINE" --checkpoint-dir data/eval_frozen/deepseek-candidate-checkpoints \
    --out eval/candidate-ragas.json
  make eval-compare BASE="$BASELINE" CAND=eval/candidate-ragas.json
  ```

  `--complete-only` 要求所有題目都有來源、生成成功，且 F／CP／AR 三項 judge 分數全數有效；否則不寫結果。正式基準流程也拒絕覆寫既有輸出。`--checkpoint-dir` 逐題逐次原子保存付費結果；同一輸出路徑、commit、語料快照與設定重跑時自動續跑，設定漂移則在付費前停止；基準與候選必須用不同目錄。檢查點含研報片段與答案，只放在未版控的 `data/eval_frozen/`。`--match-baseline` 在付費呼叫前比對題集 SHA256、語料快照 ID、模型、judge prompt／schema、檢索設定、重跑次數與併發，執行後再比對 API 回報的 judge model／system fingerprint；缺少既有設定也拒絕。若 API 未提供 fingerprint，結果只記空值，需人工確認服務端模型版本。
- `run_ragas` 的結果檔記錄量尺：summary 的 `judge_model`、`judge_prompt_sha`、`judge_schema_version` 是 META 鍵，兩份不同、或**只有一邊有記錄**，`eval-compare` 一律回 2。舊的 `eval/baselines/baseline-2026-09-02.json` 是記錄量尺之前的 Claude judge 結果，拿新結果跟它比一律回 2；新結果應與上面的 DeepSeek 基準線比較。生成端、各任務 model、commit、題集 sha256 記在 `config`（只印差異，不判定）。judge 出錯只讓該指標記 None（`n_judge_errors` 計數，只列出、不判方向），但 summary 另記三個 judge 指標各自入均值的題數 `n_effective_<指標>` 與題目集合雜湊 `judged_ids_sha`：兩邊的題目集合不同（例如各錯一題但題目不同）`eval-compare` 回 2，處置是補跑到兩邊相同題目，或直接跑 `uv run python scripts/eval_compare.py … --common-only` 只在兩邊都有值的題目上重取平均（門檻旗標在此模式下不判定）。`n_truncated` 取自 `stream_completion` 回報的逾時截斷（成功那次嘗試撞到逾時、已吐的字被砍掉；529 重試的時間不算）；輸出長度上限造成的截斷 CLI 看不到，PR-11 接 HTTP 後改用 `finish_reason`。`--repeat` 每題每指標跨次取平均，規則寫在 `eval/run_ragas.py` 的模組 docstring。
- judge 回應以 schema v2 嚴格驗證（`app/services/judge_schema.py`：`statements` 必須是字串陣列、`idx` 必須恰好覆蓋全部條目且不收布林、判定值必須是布林、AR 取不到問題算錯），不合格重試 1 次；離線仍不合格記該指標 None，生產記 `degraded_reason=schema`，唯獨生產 grounding 缺 idx 仍計 unsupported 並記 WARNING（條數記進 `evaluation.n_missing_verdicts`；一條都沒判算 schema 錯）。CP 候選片段改為 1 起編號、與脈絡的 `[n]` 和答案引用一致。
- 改動對照表（改了 A 要動 B）與契約類測試清單在 `AGENTS.md`。

## 部署與維運

真相來源在 `deploy/`，不是機器上的 `/etc`；改了 unit 要 `sudo cp` 到 `/etc/systemd/system/` 再 `daemon-reload`。

Schema 由 Alembic 管理：`make schema` 跑 `alembic upgrade head`（連 `REPORT_MARK_DB_URL`；已有資料的庫要 `CONFIRM=<host:port/db>` 逐字確認目標）。`db/schema.sql` 是凍結的 baseline（revision 0001），只接受空庫；之後的變更寫在 `db/migrations/versions/`。尚未接管的既有庫先 `make schema-check`（`scripts/schema_baseline.py`，嚴格比對系統目錄，零 drift 才算通過），再 `make schema-stamp-baseline CONFIRM=… DUMP_DIR=…`（受保護的庫強制先做全庫 `pg_dump -Fc` 並驗證可讀）；已知的索引差異以 `db/align_baseline_indexes.sql` 修正。以下幾段的 `make schema` 是導入 Alembic 前的歷史部署步驟。部署待複核處理前須先跑 `make schema` 建 `review_state`，再啟動新 API 與備份。深度研報生成已於 2026-09 移除，既有庫要由人手動執行 `docker exec -i report-mark-postgres psql -U postgres -d research < db/drop_deep_report_tables.sql` 清掉 `report_doc`／`report_run`／`report_section`／`report_rendition` 四張表（執行前確認 `make db-audit` 全綠；備份從未涵蓋這四張，不必先備）。同時：R2 bucket 裡舊的 `generated/` 生成 PDF 不再由對帳工具管，可手動清理；環境檔裡的 `REPORT_FAITHFULNESS_MIN` 舊名仍可讀，新名是 `FAITHFULNESS_MIN`。

本次問答與待複核整合上線時，先確認最近的 NAS 備份能由 `pg_restore -l` 讀取；更新部署 checkout 後依序跑 `make schema`、`make build-web`，再重啟 `report-mark-web.service`。驗收 `/healthz`、帶登入的 `/api/review/queue?kind=extraction` 與一筆 `/api/ask` 串流後，執行 `make db-backup`，確認新備份清單含 `review_state` 等表。Schema 是新增表，若需回退應回退程式版本並保留表與人工複核資料；不要用 DROP 當回退步驟。

**個別帳號上線（取代共用帳密）**的順序不可對調，否則沒有人登得進去：(1) `make schema`（建 `app_user`、`user_session`、`admin_audit_log`，`qa_log` 加 `user_id`、`review_state` 加 `reviewer_user_id`）；(2) 建第一位管理員——`uv run python scripts/create_admin.py --from-env`（把 `.env` 裡的舊共用帳密轉成管理員；舊密碼不足 10 字元時改用 `--username <名稱>` 互動設定）；(3) `make build-web` 後重啟 web；(4) 登入後在「管理 → 帳號」為每位同事建帳號（LINE bot 只下載研報到 NAS、不呼叫平台 API，不受影響）；(5) 刪掉環境檔裡的 `REPORT_MARK_ACCESS_USERNAME`／`_PASSWORD`。上線當下所有人會被登出一次（舊 cookie 是 v2 格式，明確拒收）。既有問答歷史的擁有者是 NULL（無從判斷是誰問的），一般介面看不到；管理員仍可經待複核佇列看到其中的低分與倒讚。忘記密碼或管理員全被停用時的救援：`scripts/create_admin.py --username <名稱> --reset-password`。`app_user` 進了備份（含 Argon2id 雜湊），NAS 上的備份檔要當機密看待。

| Unit | 排程 | 做什麼 |
|---|---|---|
| `report-mark-web.service` | 常駐 | `uv run uvicorn web.server:app --port 8097`，`Restart=always`，PATH drop-in 給 `claude` |
| `report-mark-sync.timer` | 每 3 小時 | rsync → 增量匯入 → 摘要 → 標題 → 摘錄 → 訊號（限量）→ 簡報 → 標題積壓（限量） |
| `report-mark-backup.timer` | 03:30 | `scripts/db_backup.sh`：十三張不可重建的表（`qa_log`、`report_takeaway`、`report_signal`、`report_brief`、`review_state`、`app_user`、`admin_audit_log`、`user_scope`、`account_deletion`、`report_visibility`、`incident`、`incident_event`、`report_upload`）`pg_dump -Fc` → NAS，保留 7 日 ＋ 4 週；掛載不可寫刻意失敗不寫本地 |
| `report-mark-freshness.timer` | 08:30 | `make freshness`，rc 0／1／2／3（新鮮／資產停更／DB 查不到／管線停跑） |
| `report-mark-audit.timer` | 08:45 | `make db-audit`，唯讀，warn 也算失敗 |
| `report-mark-audit-anchor.timer` | 04:15 | `scripts/audit_anchor.py`：驗稽核雜湊鏈、比對先前所有錨點、把鏈頭追加到 `$REPORT_MARK_BACKUP_DIR/audit-anchors.jsonl`（NAS）；鏈斷或與錨點不符 rc=1 告警 |
| `report-mark-delete-accounts.timer` | 每小時 | `scripts/execute_deletions.py`：執行到期（提出後 24 小時）的帳號刪除排程；先把 `{user_id, executed_at}` 追加到 `$REPORT_MARK_BACKUP_DIR/account-tombstones.jsonl`（NAS，落點不存在 rc=2 不退回本機），再同一筆交易刪該使用者的 `qa_log`、相關 `review_state`、`user_scope`、`user_session` 並清掉 `app_user` 的可識別資料 |
| `report-mark-replay-deletions.timer` | 04:30 | `scripts/replay_deletions.py`：依 tombstone 檢查已刪除帳號的資料沒有因還原舊備份而復活，有就重新刪除並 rc=1 告警；**每次還原後也要手動跑**（`docs/production_resilience.md`） |
| `report-mark-health.timer`、`report-mark-incident.timer` | 每 2 分鐘 | P4 探針 `scripts/check_web_health.sh`（只回報事實）與 P5 `scripts/incident_handler.sh`（去重、30 分鐘提醒、RESOLVED），webhook opt-in |
| `report-mark-linebot-health.timer`、`report-mark-linebot-incident.timer` | 每 2 分鐘 | LineBot 側同一套 |
| `report-mark-edge-health.timer`、`report-mark-edge-incident.timer` | 每 2 分鐘 | 對外邊緣同一套：`scripts/check_edge_health.sh` 打對外網址的 `/healthz`（`EDGE_HEALTH_URL`，在 `/etc/default/report-mark-sync`），本機 origin 健康而對外失敗才算邊緣故障 |
| `report-mark-container-health.timer`、`report-mark-container-incident.timer`、`report-mark-host-health.timer`、`report-mark-host-incident.timer` | 每 2 分鐘 | 容器與主機同一套：`scripts/check_container_health.sh`（catalog 的 PostgreSQL／nginx／cloudflared 是否在跑）、`scripts/check_host_health.sh`（磁碟、可用記憶體、PSI）。依 tier 去抖在探針裡做（3＝確認期，P5 那兩組 hold），細節見 `docs/production_resilience.md`「事件投影與容器／主機探針」 |
| `report-mark-backfill.timer` | 01:00 | E1d 抽取回填，跑完手動 disable |
| `report-mark-r2-reconcile.timer` | 週一 07:00 | R2 對帳（唯讀） |
| `report-mark-metrics.service` | 常駐 | 硬體用量取樣 → `data/metrics/`（`make metrics`）；另每 60 秒把主機、catalog 列的容器與 unit 狀態、批次執行寫進監控 spool `data/ops_spool/`（不連 DB） |
| `report-mark-load-observations.timer` | 每 5 分鐘 | `scripts/load_observations.py`：監控 spool 冪等匯入 `service_observation`／`job_execution`（管理頁的排程工作與主機資源讀這兩張表）與 P5 的事件紀錄 `incident`／`incident_event`（含 journal 片段）；DB 不可用 rc=2、spool 保留待下一輪補匯入，刻意不接告警 |
| `report-mark-rollup-observations.timer` | 每小時 :20 | `scripts/rollup_observations.py`：監控觀測保留 90 天、越舊越粗——24 小時前的原始觀測聚合成 5 分鐘桶（`service_observation_5m`）、7 天前的再聚合成 1 小時桶（`service_observation_1h`），90 天以前的觀測與 `job_execution` 刪除；每片同一句 SQL 先刪後寫、失敗整句回滾（冪等），advisory lock 防重疊；rc=1（核對不符／SQL 錯誤）才告警，DB 不可用（rc=2，P5 已告警）與撞鎖（rc=75）不告警。incident 不在範圍內 |
| `report-mark-schema-check.timer` | 05:20 | `scripts/schema_baseline.py scheduled`：先比 DB 的 `alembic_version` 與程式的 head（版本 drift），再以 DB 的 revision 在同伺服器建刪暫存庫做完整 schema drift 比對，結果寫 `data/schema_check.json`；drift、落後（rc=1）與超前、無法比對、暫存庫清理失敗（rc=2）告警，DB 連不上（rc=3，P5 已告警）不告警。staging（沒有 CREATEDB）設 `SCHEMA_CHECK_MODE=version` 只比版本，見 `docs/production_resilience.md` |
| `report-mark-ops-agent.service` | 常駐 | 維運代理（唯讀）：`/api/admin/ops/*` 經 `/run/report-mark-ops/agent.sock` 查 `deploy/ops/services.prod.toml` 列出的服務狀態與日誌。專用使用者、程式碼裝在 `/opt/report-mark-ops/`，安裝步驟與威脅模型見 `docs/production_resilience.md`「維運代理」；開發環境是 `report-mark-ops-agent-dev.service`、EC2 staging 是 `report-mark-ops-agent-staging.service`（`deploy/ops/services.staging.toml`） |
| `report-mark-alert@.service` | `OnFailure` 觸發 | journal ＋ `data/unit_failures.log` ＋ webhook |

非辦公室主機（EC2 staging：RDS PostgreSQL、共用 production 的 R2 bucket）：

- unit 用 `sudo deploy/install_units.sh --user <使用者> --root <repo 根> <unit…>` 安裝：repo 裡的 unit 維持辦公室主機的字面值，這支在安裝時代換使用者、HOME 與 repo 路徑，只複製與 `daemon-reload`，不 enable。只適用辦公室的不裝：`report-mark-backup`（NAS）、`report-mark-linebot-*`、`report-mark-metrics`（docker cgroup）、`report-mark-backfill`。
- DB：`REPORT_MARK_DB_URL=postgresql+asyncpg://…@<RDS 端點>:5432/research?ssl=verify-full`，並設 `PGSSLROOTCERT=<RDS CA bundle>`（asyncpg 從環境讀；URL 裡寫 `sslmode=`／`sslrootcert=` 會讓 asyncpg 拋 `TypeError`）。repo 根 `.env` 與 `/etc/default/report-mark-sync` 兩處都要設。`make schema`、`make db-backup`、`make ingest-lowio` 走 `docker exec`，在 RDS 上不適用（RDS 的耐久性參數只能改 parameter group）。
- 新研報：辦公室主機的 `/etc/default/report-mark-sync` 設 `SYNC_INBOX_PUSH=1`，staging 設 `SYNC_SOURCE=r2-inbox`（見 `docs/WORKFLOW.md`「生產同步鏈」）。兩邊入庫同一份檔得到同一個 `originals/` key（create-only、SHA 驗證），不會互相覆寫。
- 對外：`research.tingfong.com` 是 Cloudflare 橘雲 A record 指向 EC2 的 Elastic IP，不走 Tunnel。EC2 上以 apt 的 nginx 載入 `deploy/nginx-origin.conf`（裝到 `/etc/nginx/sites-available/report-mark`），以 Cloudflare Origin CA 憑證（`/etc/ssl/report-mark/`，不進 repo）聽 443、反代 `127.0.0.1:8097`；App 靠對端 127.0.0.1 信任它（`REPORT_MARK_TRUSTED_PROXY_CIDRS` 預設值），所以 `proxy_pass` 不能改成其他位址。Security Group 只放行 Cloudflare IPv4 範圍的 443（由 CloudFormation stack `report-research-staging` 管理）。三個前提缺一不可：橘雲不能關（Origin CA 只有 Cloudflare 信任）、Cloudflare SSL/TLS 模式 Full (strict)、Cloudflare IP 範圍變動時 nginx 的 `set_real_ip_from` 與 SG 的 prefix list 一起改。

對外邊緣：`make up-edge`／`down-edge`／`edge-logs`／`edge-reload`（`deploy/docker-compose.yml`：nginx 限流 10r/s、靜態資產豁免；cloudflared 隧道）。健康判定打 `/healthz`，不看 `systemctl is-active`；oneshot 是否跑過用 `scripts/verify_oneshot_ran.sh`。`make help` 列出的破壞性 target（`reset-db`、`clean-data`、`ingest-lowio`）除非明講不要跑。

LLM 批次的跳過名單：`make llm-blocked` 唯讀列出 `research.llm_task_failure` 判定跳過的研報（零 LLM；要連累計中未達門檻的也列，直接跑 `uv run python scripts/llm_blocked.py --all`）。要重打就對該批次加 `--retry-blocked`；跳過鍵只看 model、不看 prompt，**改 prompt 後也要加**（摘錄與訊號的 `--reextract` 隱含它）。部署這張表要先 `make schema`。

## 延伸文件

| 文件 | 內容 |
|---|---|
| `AGENTS.md` | 給貢獻者與 AI 代理的唯一指引：鐵律、指令、測試、改動對照表、架構不變量摘要、陷阱、過渡中狀態、慣例 |
| `docs/ARCHITECTURE.md` | 模組地圖、import 方向、檢索／問答／讀取功能的不變量、Web 層、資料層、設定旋鈕 |
| `docs/WORKFLOW.md` | 端到端管線、逐階段 I/O 與參數、生產同步鏈、標籤詞彙、SSE 契約、R2 遷移順序、排錯 |
| `docs/EXTRACTION.md` | 抽取層現況：選型與授權、文件模型、回退、品質指標、快取、`extraction_log`、golden set、回填 |
| `docs/production_resilience.md` | 生產韌性：重啟策略、健康探針、告警鏈、備份與還原（含 `pg_restore` 演練） |
| `docs/EXTERNAL_ACCESS.md` | Cloudflare Tunnel ＋ nginx 對外存取 |
| `docs/nas_scheduled_sync_deployment.md` | NAS 定時同步的掛載、sudoers、timer 安裝 |
| `docs/LINEBOT_ALWAYS_ON.md` | LineBot 常駐與監控 |
| `docs/CAPACITY.md` | 硬體用量量測與上雲選型 |
| [AWS staging 基礎設施](deploy/aws/README.md) | CloudFormation 管理的 EC2／RDS staging、change set 與驗收流程 |
| `docs/incidents/` 與 `docs/benchmarks/` | 事故報告與基準量測 |
