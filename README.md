# 廷豐智能研報（report-mark）

券商研報平台：把 `研報自動匯入/` 的 PDF／docx 抽字、以 Claude 標註市場與標的、以 BGE-M3 嵌入 pgvector，提供語意檢索、RAG 問答、研報閱讀頁、券商觀點雷達與每日簡報。單機部署（WSL2 ＋ Docker Postgres），經 Cloudflare Tunnel 對外，共用帳密登入。GitHub 為 `FPI-TW/report-research`，目錄名沿用 `report-mark`。

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
| 問答 | `/app/ask` | RAG 串流問答，多輪對話，五類路由（離題、總覽、語料問答、時效、投資建議），每題可開網搜，追問建議 |
| 閱讀頁 | `/app/report/:hash` | 原檔 PDF 內嵌檢視（EmbedPDF）、重點摘錄、券商訊號卡、相似研報 |
| 觀點雷達 | `/app/radar` | 標的目錄、多券商評等與目標價共識、四維論點、券商時間軸，讀取零 LLM |
| 每日簡報 | `/app/brief` | 窗期內新研報與評等變動的日報 |
| 監控 | `/app/monitor` | 語料規模、批次覆蓋率、抽取品質、忠實度、管線與排程健康 |

## 系統架構

```
                    ┌──────────────── 離線批次（systemd timer / make）────────────────┐
研報自動匯入/ ──▶ extract_all ──▶ tag_all_cli(Haiku) ──▶ ingest_all ──▶ pgvector      │
                    │  data/extracted/  data/tags/          research_report            │
                    │                                       report_chunk (HNSW+trgm)   │
                    │  extract_takeaways / extract_signals / generate_summaries /       │
                    │  generate_titles / generate_brief（Sonnet，flock 互斥）            │
                    └──────────────────────────────────────────────────────────────────┘
                                                  │
     瀏覽器 ── Cloudflare Tunnel ── nginx ── uvicorn web/server.py（單 worker，:8097）
                                                  │
                     web/routers/* ── app/services/*（檢索、問答、閱讀、雷達、簡報）
                                                  │
                     claude -p（Sonnet／Haiku）    BGE-M3 ＋ bge-reranker（CPU 常駐）
                     R2（可選，研報原檔）
```

分工鐵律：Python 做所有決定性的事，Claude 只做語意。派生功能一律 fail-open。完整不變量見 `docs/ARCHITECTURE.md`。

技術棧：Python 3.11 ＋ uv、FastAPI ＋ uvicorn、SQLAlchemy async ＋ asyncpg、pgvector（HNSW cosine）＋ pg_trgm、FlagEmbedding（BGE-M3、bge-reranker-v2-m3，torch CPU-only）、pdfplumber ＋ pypdf ＋ python-docx、boto3（R2）、OpenCC（簡→繁）；前端 React 19 ＋ TypeScript ＋ Vite ＋ TanStack Query ＋ zod ＋ EmbedPDF；LLM 一律經 `claude` CLI（`claude -p`），不用 SDK。

## 快速開始

```bash
uv sync                       # Python 3.11+；torch 走 CPU-only index
make setup                    # 相依 + pgvector 容器（host port 5436）+ 套 db/schema.sql
cp .env.example .env          # 填 REPORT_MARK_ACCESS_USERNAME / _PASSWORD / _SESSION_SECRET，未設拒絕啟動

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

`claude` CLI 要在 PATH 上；systemd 環境靠 `deploy/systemd/report-mark-web.service.d/path.conf`。

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
db/schema.sql, db/expected_constraints.txt, db/drop_deep_report_tables.sql（既有庫手動執行）
deploy/                   systemd unit、nginx、docker-compose（部署真相來源）
eval/                     離線評測 harness 與基準線（刻意不進 CI）
tests/                    pytest（unittest 風格）＋ fixtures/sse_events.json
docs/                     WORKFLOW / ARCHITECTURE / EXTRACTION / 維運文件
研報自動匯入/               唯讀來源鏡像；data/ 為執行期產物（皆不入版控）
```

## Web 介面與 API

所有路徑除 `/login`、`/healthz`、`/app/assets/` 外都要登入；`/api/` 未登入回 401，其餘 302 到 `/login`。SSE 端點每 20 秒送一行心跳註解。

| 方法 | 路徑 | 參數 | 回應 | 備註 |
|---|---|---|---|---|
| GET | `/healthz` | — | `{"status":"ok"}`；DB 不可用回 503 `{"status":"degraded"}` | 免登入；只探 DB（`SELECT 1`，3 秒逾時）；結果快取 5 秒 |
| GET | `/healthz/storage` | — | `{"storage":"disabled"\|"unknown"\|"ok"\|"degraded"}`；degraded 回 503 | **只回答本機直連**（對端 loopback、無代理 header、Host 為本機），其餘 404；給 `scripts/check_web_health.sh` 用（退出碼 6） |
| GET | `/healthz/llm` | — | `{"llm":"disabled"\|"unknown"\|"ok"\|"low"\|"exhausted"\|"auth_failed"\|"unreachable"\|"indeterminate"}`；後五種回 503，線上任務沒有用到 DeepSeek 時改回 200 並加 `_unused` 後綴。**不回任何金額** | **只回答本機直連**，其餘 404；查 DeepSeek `GET /user/balance`（只看 `LLM_BUDGET_CURRENCY` 那一筆，低於 `LLM_BALANCE_FLOOR` 為 low），ok 快取 600 秒、其餘 60 秒、每次最多等 4 秒；給 `scripts/check_web_health.sh` 用（退出碼 7）。判定細節見 `app/services/llm_health.py` |
| GET／POST | `/login`、POST `/logout` | form `username`、`password`、`next` | 302／303 | 登入頁免登入；失敗回 `/login?error=1|locked|insecure` |
| GET | `/`、`/monitor`、`/help` | — | 302 到 `/app/search`、`/app/monitor`、`/app/help` | 舊入口相容 |
| GET | `/app`、`/app/{spa_path:path}` | — | SPA `index.html`（no-cache） | `frontend/dist` 不存在回 503；`/app/assets/` 免登入且 immutable 快取 |
| GET | `/api/stats` | — | `total_reports`、`total_chunks`、`markets`、`instrument_types`、`report_types`、`username` | 與 `/api/progress` 共用 15 秒 DB 快取 |
| GET | `/api/progress` | — | `db`、`summary`、`takeaway`、`signal`、`evaluation`、`extraction`、`tagging`、`ingest`、`pipelines`、`orchestrator`、`sync`、`unit_failures` | 監控頁輪詢；`extraction_log` 缺表時 `extraction` 為 null。`evaluation.qa` 的 `total`／`checked`／`latest` 計所有 judge（覆蓋率語意，換 judge 不會驟降）；分數類 `judge_checked`／`degraded`／`below_min`／`avg_score`／`avg_n`（平均的樣本數，不含 degraded）只計現行 judge（`FAITHFULNESS_MODEL`），另帶 `judge_model`、`judge_since`（窗期內現行 judge 最早一筆的日期）、`other_judge_checked`（其他 judge 的筆數） |
| GET | `/api/markets` | — | `{"markets": [...]}` | 市場代碼清單 |
| GET | `/api/reports` | `market`、`instrument_type`、`relates_stock`、`relates_futures`、`report_type`、`sort`（`date_desc`）、`limit`（1–100，50）、`offset` | `{total, offset, items[]}` | 瀏覽（無查詢詞） |
| GET | `/api/search` | `q`（1–500 必填）、同上篩選、`sort`（`relevance`）、`limit`、`offset`、`passages`（1–6，3） | `{query, market, total, market_facets, lexical_truncated, results[]}` | 混合檢索 ＋ `rank_reports` |
| POST | `/api/ask` | JSON `question`（≤2000）、`conversation_id`、篩選欄位、`k`（1–20，8）、`regenerate_of`、`edit_of`、`request_id`、`locale`、`web` | SSE：`queued`→`status`→`sources`→`ext_sources`→`token`…→`followups`→`done`；婉拒走 `notice` | 併發上限 3、佇列 20（滿載 429 ＋ `Retry-After: 30`） |
| POST | `/api/ask/stop` | JSON `question`、`conversation_id`、`partial_answer`、`sources`、`ext_sources`、`stages`、`regenerate_of`、`edit_of`、`request_id` | `{"qa_id"}` | 中止時把部分答案落 `qa_log` |
| POST | `/api/feedback` | JSON `qa_id`、`value`（`like`／`dislike`／`none`） | `{"ok"}` | |
| GET | `/api/history` | `limit`（1–200，50） | 最近問答列 | 排除離題婉拒 |
| DELETE | `/api/history/{qa_id}`；POST `/api/history/{qa_id}/delete` | — | `{"ok"}` | POST 是相容 alias |
| GET | `/api/qa/{root_qa_id}/versions` | — | 同題所有版本 | 重新生成／編輯後的版本鏈 |
| GET | `/api/conversations` | `limit`（1–200）、`offset`、`q`（≤200 字） | 對話串清單（裸陣列，無 total） | `q` 比對整串的有效提問（不只標題），`%`／`_` 為字面字元；前端以「回來的筆數等於 limit」判斷有無下一頁 |
| GET | `/api/conversations/{conversation_id}` | — | 該對話全部輪次（舊→新） | |
| DELETE | `/api/conversations/{conversation_id}`；POST `/api/conversations/{conversation_id}/delete` | — | `{"ok"}` | 以 `COALESCE(conversation_id, id)` 整批刪 `qa_log` |
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
| GET | `/api/review/queue` | `kind`（`faithfulness`／`feedback`／`extraction`，必填）、`limit`（1–100，20）、`offset`、`days`（1–365，30） | `{kind, total, limit, offset, has_more, next_offset, min_score, items}` | 待複核佇列，唯讀零 LLM：忠實度低於 `FAITHFULNESS_MIN`（只列現行 judge 量的）、倒讚、抽取 `needs_review`。`days` 只作用於前兩種；門檻、窗期與 judge 過濾和監控頁的忠實度卡同一套定義。問答項目帶 `judge_model`（沒有 evaluation 時為 null） |

SSE 事件欄位見 `docs/WORKFLOW.md` 的 Web API 契約；單一真相 `tests/fixtures/sse_events.json`。

## 設定（環境變數）

repo 根 `.env`（範本 `.env.example`）由 `web/env_loader.py` 讀取，不做 shell 展開；批次腳本不讀它，生產批次的環境在 `/etc/default/report-mark-sync`。

| 變數 | 預設 | 說明 |
|---|---|---|
| `REPORT_MARK_ACCESS_USERNAME`、`REPORT_MARK_ACCESS_PASSWORD` | 無 | 共用帳密；未設拒絕啟動（fail-closed）。換密碼全員登出 |
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
| `LLM_BUDGET_CURRENCY`、`LLM_BALANCE_FLOOR` | `CNY`、`70` | 只有 web 讀（`/healthz/llm`，設在 repo 根 `.env`）：只看餘額裡這個幣別那一筆，低於門檻回 503 → 探針退出碼 7。月上限 ¥350 是儲值紀律，不是旋鈕（`docs/production_resilience.md`） |
| `LLM_PROVIDER`、各任務 `*_MODEL`（`ASK_ANSWER_MODEL`、`ASK_WEB_MODEL`、`TAG_MODEL`、`SUMMARY_MODEL` 等 14 個） | `claude_cli`、查表 | 任務旋鈕非空就用，否則查 provider 的預設表（`app/services/llm_models.py`）；`claude_cli` 與遷移前逐字相同；`claude_only` 是遷移期的回退值，claude CLI 已於 2026-09-23 放棄，設了等於 LLM 全部停擺。清單與語意見 `.env.example` |
| `ASK_*`、`QA_*` | 見 `docs/ARCHITECTURE.md` 設定旋鈕 | 問答脈絡、選篇、路由模型、網搜（`ASK_ENABLE_WEB`、`ASK_WEB_TIMEOUT`）、agentic 補查 |
| `ASK_RERANK_*`、`RERANK_MODEL` | 開、50 候選 | rerank fail-open |
| `ASK_FAITHFULNESS_*`、`FAITHFULNESS_MIN`、`FAITHFULNESS_MODEL`、`FAITHFULNESS_TIMEOUT` | 開、0.9、`claude-haiku-4-5` | 問答忠實度抽查；關掉或壞掉都不會有錯誤訊息，只標 `degraded`（`evaluation.degraded_reason` 說原因）。`FAITHFULNESS_MIN` 讀不到時退回舊名 `REPORT_FAITHFULNESS_MIN`。`FAITHFULNESS_MODEL` 不再沿用 `ASK_INTENT_MODEL`；換掉等於換尺，監控卡、待複核與 `scripts/eval_faithfulness.py` 只計現行 judge（缺 `judge_model` 的舊列視為 `claude-haiku-4-5`） |
| `EXTRACTOR`、`EXTRACTION_REVIEW_MIN`、`EXTRACTION_REVIEW_MIN_COVERAGE`、`EXTRACTION_REVIEW_MAX_GARBLED` | `pypdf`、0.6、0.30、0.02 | 抽取器（生產 sync 環境檔設 `pdfplumber`）與 `needs_review` 三道門檻（只標記不擋，`docs/EXTRACTION.md` §5） |
| `OBJECT_STORAGE_MODE`、`R2_ENDPOINT_URL`、`R2_BUCKET`、`R2_ACCESS_KEY_ID`、`R2_SECRET_ACCESS_KEY`、`R2_PRESIGN_TTL_SECONDS` | `local` | 非 local 缺任一 fail-closed；TTL 上限 3600 |
| `ASK_MAX_QUEUE`、`SSE_HEARTBEAT_INTERVAL` | 20、20 | web 層旋鈕 |
| `RADAR_CATALOG_CACHE_TTL` | 60 | 雷達目錄回應快取秒數；0 停用 |
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

uv run python eval/run_ragas.py --concurrency 1   # 問答評測（會 spawn claude），預設寫 eval/candidate-ragas.json
uv run python eval/run_ragas.py --generator-model <model> --repeat 3 --dump-io data/eval_frozen/<名稱>
make eval-compare BASE=<同一版 run_ragas 產出的基準線.json> CAND=eval/candidate-ragas.json
```

- CI 四個 job 全為必要檢查（`.github/workflows/ci.yml`）：前端測試（tsc ＋ vitest）、後端測試（pytest）、schema 契約（PostgreSQL）、secret 掃描（gitleaks）。前端 job 把 `frontend/dist` 傳給後端 job，SPA 測試對真 build 驗證；後端設 `HF_HUB_OFFLINE=1`、安裝 CJK 字型並設 `REPORT_MARK_REQUIRE_CJK=1`（`tests/test_extraction_layout.py` 的 CjkTests 用 weasyprint 渲染中文測試 PDF，不准退回 skip）；schema job 套 `db/schema.sql` 兩次驗冪等並對帳 `db/expected_constraints.txt`。required check 名稱等於 job 的中文 `name`，改了要同步 GitHub 分支保護。
- 測試不連網、不載模型：LLM、嵌入、DB、檔案系統一律用假物件。async 測試用 `unittest.IsolatedAsyncioTestCase`，不用 pytest-asyncio。端點走 HTTP 層測。
- 測試絕不可寫 repo 根的真實環境檔（`tests/conftest.py` 會還原並 fail）。
- 評測 `eval/` 刻意不進 CI（會與 sync timer 搶 `claude` CLI）。`make eval-compare` 退出碼是結論：0 無劣化、1 劣化、2 不可比、3 有未分類指標。門檻 F>0.9／CP>0.8／AR>0.55 是政策；最新基準線 `eval/baselines/baseline-2026-09-02.json`。
- `run_ragas` 的結果檔記錄量尺：summary 的 `judge_model`、`judge_prompt_sha`、`judge_schema_version` 是 META 鍵，兩份不同、或**只有一邊有記錄**，`eval-compare` 一律回 2。上面那份最新基準線是在記錄量尺之前產出的，所以**現在拿新結果跟它比一律回 2**，直到用新版重跑出新的基準線為止；要比就兩邊都用同一版重跑。生成端、各任務 model、commit、題集 sha256 記在 `config`（只印差異，不判定）。judge 出錯只讓該指標記 None（`n_judge_errors` 計數，只列出、不判方向），但 summary 另記三個 judge 指標各自入均值的題數 `n_effective_<指標>` 與題目集合雜湊 `judged_ids_sha`：兩邊的題目集合不同（例如各錯一題但題目不同）`eval-compare` 回 2，處置是補跑到兩邊相同題目，或直接跑 `uv run python scripts/eval_compare.py … --common-only` 只在兩邊都有值的題目上重取平均（門檻旗標在此模式下不判定）。`n_truncated` 取自 `stream_completion` 回報的逾時截斷（成功那次嘗試撞到逾時、已吐的字被砍掉；529 重試的時間不算）；輸出長度上限造成的截斷 CLI 看不到，PR-11 接 HTTP 後改用 `finish_reason`。`--repeat` 每題每指標跨次取平均，規則寫在 `eval/run_ragas.py` 的模組 docstring。
- judge 回應以 schema v2 嚴格驗證（`app/services/judge_schema.py`：`statements` 必須是字串陣列、`idx` 必須恰好覆蓋全部條目且不收布林、判定值必須是布林、AR 取不到問題算錯），不合格重試 1 次；離線仍不合格記該指標 None，生產記 `degraded_reason=schema`，唯獨生產 grounding 缺 idx 仍計 unsupported 並記 WARNING（條數記進 `evaluation.n_missing_verdicts`；一條都沒判算 schema 錯）。CP 候選片段改為 1 起編號、與脈絡的 `[n]` 和答案引用一致。
- 改動對照表（改了 A 要動 B）在 `CLAUDE.md`；契約類測試清單在 `AGENTS.md`。

## 部署與維運

真相來源在 `deploy/`，不是機器上的 `/etc`；改了 unit 要 `sudo cp` 到 `/etc/systemd/system/` 再 `daemon-reload`。

Schema 由 `make schema` 套 `db/schema.sql`（只 `CREATE IF NOT EXISTS`，冪等），沒有 migration 工具，刪表要另給腳本。深度研報生成已於 2026-09 移除，既有庫要由人手動執行 `docker exec -i report-mark-postgres psql -U postgres -d research < db/drop_deep_report_tables.sql` 清掉 `report_doc`／`report_run`／`report_section`／`report_rendition` 四張表（執行前確認 `make db-audit` 全綠；備份從未涵蓋這四張，不必先備）。同時：R2 bucket 裡舊的 `generated/` 生成 PDF 不再由對帳工具管，可手動清理；環境檔裡的 `REPORT_FAITHFULNESS_MIN` 舊名仍可讀，新名是 `FAITHFULNESS_MIN`。

| Unit | 排程 | 做什麼 |
|---|---|---|
| `report-mark-web.service` | 常駐 | `uv run uvicorn web.server:app --port 8097`，`Restart=always`，PATH drop-in 給 `claude` |
| `report-mark-sync.timer` | 每 3 小時 | rsync → 增量匯入 → 摘要 → 標題 → 摘錄 → 訊號（限量）→ 簡報 → 標題積壓（限量） |
| `report-mark-backup.timer` | 03:30 | `scripts/db_backup.sh`：四張不可重建的表（`qa_log`、`report_takeaway`、`report_signal`、`report_brief`）`pg_dump -Fc` → NAS，保留 7 日 ＋ 4 週；掛載不可寫刻意失敗不寫本地 |
| `report-mark-freshness.timer` | 08:30 | `make freshness`，rc 0／1／2／3（新鮮／資產停更／DB 查不到／管線停跑） |
| `report-mark-audit.timer` | 08:45 | `make db-audit`，唯讀，warn 也算失敗 |
| `report-mark-health.timer`、`report-mark-incident.timer` | 每 2 分鐘 | P4 探針 `scripts/check_web_health.sh`（只回報事實）與 P5 `scripts/incident_handler.sh`（去重、30 分鐘提醒、RESOLVED），webhook opt-in |
| `report-mark-linebot-health.timer`、`report-mark-linebot-incident.timer` | 每 2 分鐘 | LineBot 側同一套 |
| `report-mark-backfill.timer` | 01:00 | E1d 抽取回填，跑完手動 disable |
| `report-mark-r2-reconcile.timer` | 週一 07:00 | R2 對帳（唯讀） |
| `report-mark-metrics.service` | 常駐 | 硬體用量取樣 → `data/metrics/`（`make metrics`） |
| `report-mark-alert@.service` | `OnFailure` 觸發 | journal ＋ `data/unit_failures.log` ＋ webhook |

對外邊緣：`make up-edge`／`down-edge`／`edge-logs`／`edge-reload`（`deploy/docker-compose.yml`：nginx 限流 10r/s、靜態資產豁免；cloudflared 隧道）。健康判定打 `/healthz`，不看 `systemctl is-active`；oneshot 是否跑過用 `scripts/verify_oneshot_ran.sh`。`make help` 列出的破壞性 target（`reset-db`、`clean-data`、`ingest-lowio`）除非明講不要跑。

LLM 批次的跳過名單：`make llm-blocked` 唯讀列出 `research.llm_task_failure` 判定跳過的研報（零 LLM；要連累計中未達門檻的也列，直接跑 `uv run python scripts/llm_blocked.py --all`）。要重打就對該批次加 `--retry-blocked`；跳過鍵只看 model、不看 prompt，**改 prompt 後也要加**（摘錄與訊號的 `--reextract` 隱含它）。部署這張表要先 `make schema`。

## 延伸文件

| 文件 | 內容 |
|---|---|
| `CLAUDE.md` | 給 AI 助理與貢獻者的鐵律、改動對照表、架構不變量摘要 |
| `AGENTS.md` | 貢獻者慣例：結構、風格、測試、commit、安全 |
| `docs/ARCHITECTURE.md` | 模組地圖、import 方向、檢索／問答／讀取功能的不變量、Web 層、資料層、設定旋鈕 |
| `docs/WORKFLOW.md` | 端到端管線、逐階段 I/O 與參數、生產同步鏈、標籤詞彙、SSE 契約、R2 遷移順序、排錯 |
| `docs/EXTRACTION.md` | 抽取層現況：選型與授權、文件模型、回退、品質指標、快取、`extraction_log`、golden set、回填 |
| `docs/production_resilience.md` | 生產韌性：重啟策略、健康探針、告警鏈、備份與還原（含 `pg_restore` 演練） |
| `docs/EXTERNAL_ACCESS.md` | Cloudflare Tunnel ＋ nginx 對外存取 |
| `docs/nas_scheduled_sync_deployment.md` | NAS 定時同步的掛載、sudoers、timer 安裝 |
| `docs/LINEBOT_ALWAYS_ON.md` | LineBot 常駐與監控 |
| `docs/CAPACITY.md` | 硬體用量量測與上雲選型 |
| `docs/incidents/` 與 `docs/benchmarks/` | 事故報告與基準量測 |
