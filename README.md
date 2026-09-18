# 廷豐智能研報（report-mark）

券商研報平台：把 `研報自動匯入/` 的 PDF／docx 抽字、以 Claude 標註市場與標的、以 BGE-M3 嵌入 pgvector，提供語意檢索、RAG 問答、可下載的深度研報 PDF、研報閱讀頁、券商觀點雷達與每日簡報。單機部署（WSL2 ＋ Docker Postgres），經 Cloudflare Tunnel 對外，共用帳密登入。GitHub 為 `FPI-TW/report-research`，目錄名沿用 `report-mark`。

## 目錄

- [功能總覽](#功能總覽)
- [系統架構](#系統架構)
- [快速開始](#快速開始)
- [專案結構](#專案結構)
- [Web 介面與 API](#web-介面與-api)
- [設定（環境變數）](#設定環境變數)
- [開發與測試](#開發與測試)
- [部署與維運](#部署與維運)
- [尚未實作與暫不納入](#尚未實作與暫不納入)
- [延伸文件](#延伸文件)

## 功能總覽

| 頁面 | 路徑 | 做什麼 |
|---|---|---|
| 檢索 | `/app/search` | 混合檢索（dense ＋ lexical），市場、商品類型、報告類型篩選，卡片／表格／Bento 三種檢視 |
| 問答 | `/app/ask` | RAG 串流問答，多輪對話，五類路由（離題、總覽、語料問答、時效、投資建議），每題可開網搜，追問建議，答完可一鍵出深度研報 |
| 深度研報 | 問答頁內 | 大綱 → 逐節檢索草稿 → grounding 修正 → Typst 排版 PDF，三款模板可換皮重排，背景生成可重連 |
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
                     web/routers/* ── app/services/*（檢索、問答、研報、閱讀、雷達、簡報）
                                                  │
                     claude -p（Sonnet／Haiku）    BGE-M3 ＋ bge-reranker（CPU 常駐）
                     Typst ／ WeasyPrint            R2（可選）／ data/reports
```

分工鐵律：Python 做所有決定性的事，Claude 只做語意。派生功能一律 fail-open。完整不變量見 `docs/ARCHITECTURE.md`。

技術棧：Python 3.11 ＋ uv、FastAPI ＋ uvicorn、SQLAlchemy async ＋ asyncpg、pgvector（HNSW cosine）＋ pg_trgm、FlagEmbedding（BGE-M3、bge-reranker-v2-m3，torch CPU-only）、pdfplumber ＋ pypdf ＋ python-docx、Typst ＋ pandoc、WeasyPrint、boto3（R2）、OpenCC（簡→繁）；前端 React 19 ＋ TypeScript ＋ Vite ＋ TanStack Query ＋ zod ＋ EmbedPDF；LLM 一律經 `claude` CLI（`claude -p`），不用 SDK。

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
  templates/              Typst 模板 ×3 + manifest.py registry
  services/
    extract.py, extraction/   抽取門面、版面層、品質、per-hash 快取（docs/EXTRACTION.md）
    filename.py, tagging.py   檔名解析、標籤詞表（市場代碼對齊 findb）
    chunk.py, embed.py, store.py, rows.py, textnorm.py, db.py
    retrieval.py, retrieval_pipeline.py, rerank.py      混合檢索唯一入口
    answer.py, agentic_qa.py, scope_router.py, overview.py, query_planner.py,
    faithfulness.py, evidence.py, followups.py, report_gate.py, llm.py, zh_hant.py, locale.py
    report.py, report_writer.py, typst_render.py, pdf.py, chart.py   深度研報
    reading/, radar/, signal_extract.py, brief.py       讀取零 LLM 的功能
    object_storage.py     R2（local / hybrid / r2）
web/
  server.py               組合層：env → logging → auth middleware → lifespan → routers
  routers/                12 支 APIRouter，不帶 prefix
  deps.py, report_runs.py, auth.py, concurrency.py, dev_mode.py, env_loader.py
frontend/src/
  features/{search,ask,monitor,radar,report,brief,help}
  lib/                    API 邊界：zod schema、readSSE、askReducer、hooks
  components/{shell,primitives,animate-ui}
scripts/                  批次與維運（50 支），會 spawn claude 的取 _claude_lock.py
db/schema.sql, db/expected_constraints.txt
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
| GET／POST | `/login`、POST `/logout` | form `username`、`password`、`next` | 302／303 | 登入頁免登入；失敗回 `/login?error=1|locked|insecure` |
| GET | `/`、`/monitor`、`/help` | — | 302 到 `/app/search`、`/app/monitor`、`/app/help` | 舊入口相容 |
| GET | `/app`、`/app/{spa_path:path}` | — | SPA `index.html`（no-cache） | `frontend/dist` 不存在回 503；`/app/assets/` 免登入且 immutable 快取 |
| GET | `/api/stats` | — | `total_reports`、`total_chunks`、`markets`、`instrument_types`、`report_types`、`username` | 與 `/api/progress` 共用 15 秒 DB 快取 |
| GET | `/api/progress` | — | `db`、`summary`、`takeaway`、`signal`、`evaluation`、`extraction`、`tagging`、`ingest`、`pipelines`、`orchestrator`、`sync`、`unit_failures` | 監控頁輪詢；`extraction_log` 缺表時 `extraction` 為 null |
| GET | `/api/markets` | — | `{"markets": [...]}` | 市場代碼清單 |
| GET | `/api/reports` | `market`、`instrument_type`、`relates_stock`、`relates_futures`、`report_type`、`sort`（`date_desc`）、`limit`（1–100，50）、`offset` | `{total, offset, items[]}` | 瀏覽（無查詢詞） |
| GET | `/api/search` | `q`（1–500 必填）、同上篩選、`sort`（`relevance`）、`limit`、`offset`、`passages`（1–6，3） | `{query, market, total, market_facets, lexical_truncated, results[]}` | 混合檢索 ＋ `rank_reports` |
| POST | `/api/ask` | JSON `question`（≤2000）、`conversation_id`、篩選欄位、`k`（1–20，8）、`regenerate_of`、`edit_of`、`request_id`、`locale`、`web` | SSE：`queued`→`status`→`sources`→`ext_sources`→`token`…→`followups`→`done`；婉拒走 `notice` | 併發上限 3、佇列 20（滿載 429 ＋ `Retry-After: 30`） |
| POST | `/api/ask/stop` | JSON `question`、`conversation_id`、`partial_answer`、`sources`、`ext_sources`、`stages`、`regenerate_of`、`edit_of`、`request_id` | `{"qa_id"}` | 中止時把部分答案落 `qa_log` |
| POST | `/api/report` | JSON `question`、`conversation_id`、`qa_id`、`template_id`、`locale` | SSE：`run`→`queued`→`status`→`outline`→`sources`→`section_draft`／`section_skipped`／`token`…→`document_revision`→`done{download_url}` | 訂閱端；生成由背景任務持有 `REPORT_SEMAPHORE`（1，佇列 5；滿載 429 ＋ `Retry-After: 120`），接回既有 run 不佔名額 |
| GET | `/api/report-runs` | `conversation_id`（必填） | `{"runs":[{run_id, qa_id, conversation_id, question, elapsed_ms}]}` | 行程內仍活著的 run |
| GET | `/api/report-runs/{run_id}/stream` | — | SSE（先重播緩衝再直播） | `token` 不重播；`section_draft` 重播無 `markdown` |
| POST | `/api/report-runs/{run_id}/cancel` | — | `{"cancelled": bool}` | 唯一取消手段 |
| GET | `/api/report-templates` | — | `{"templates":[{id, name, description, is_default, thumbnail}]}` | `app/templates/manifest.py` |
| POST | `/api/report-doc/{report_id}/rerender` | JSON `template_id` | `{"rendition_id", "template_id"}` | 換模板重排；失敗保留上一版 |
| GET | `/api/report-doc/{report_id}/pdf` | — | PDF 檔或 302 到 presigned URL | 404／409（尚未完成）／503（物件不存在） |
| POST | `/api/feedback` | JSON `qa_id`、`value`（`like`／`dislike`／`none`） | `{"ok"}` | |
| GET | `/api/history` | `limit`（1–200，50） | 最近問答列 | 排除離題婉拒 |
| DELETE | `/api/history/{qa_id}`；POST `/api/history/{qa_id}/delete` | — | `{"ok"}` | POST 是相容 alias |
| POST | `/api/qa/{qa_id}/report-offer` | JSON `action`（`decline`／`restore`） | `{"ok"}` | 研報建議卡的收合狀態 |
| GET | `/api/qa/{root_qa_id}/versions` | — | 同題所有版本 | 重新生成／編輯後的版本鏈 |
| GET | `/api/conversations` | `limit`（1–200） | 對話串清單 | |
| GET | `/api/conversations/{conversation_id}` | — | 該對話全部輪次（舊→新） | |
| DELETE | `/api/conversations/{conversation_id}`；POST `/api/conversations/{conversation_id}/delete` | — | `{"ok"}` | 連同研報衍生物與磁碟 PDF；進行中 run 先取消 |
| GET | `/api/report/{report_id}/full` | — | `report_id`、`file_name`、`title`、`market`、`source`、`summary`、`report_date`、`report_type`、`has_file` | 研報詳情 |
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

SSE 事件欄位與重播規則見 `docs/WORKFLOW.md` 的 Web API 契約；單一真相 `tests/fixtures/sse_events.json`。

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
| `LOG_LEVEL` | `INFO` | 調到 WARNING 會失去登入成功稽核與 `qa_timing` 遙測 |
| `DB_POOL_SIZE`、`DB_MAX_OVERFLOW`、`DB_POOL_TIMEOUT`、`DB_POOL_RECYCLE` | 5、15、10、1800 | per-process 上限 20；改併發前依 `.env.example` 算式重算 |
| `DB_STATEMENT_TIMEOUT_MS`、`DB_IDLE_TX_TIMEOUT_MS`、`DB_MAINTENANCE_STATEMENT_TIMEOUT_MS` | 60000、0、0 | idle 預設 0 是刻意的（sync 在交易內 spawn CLI）；維運長查詢走 `relax_statement_timeout()` |
| `EMBED_MAX_CONCURRENCY`、`EMBED_TORCH_THREADS` | 1、0 | 嵌入序列化；`/api/search`、雷達、閱讀頁沒有併發閘 |
| `ASK_*`、`QA_*` | 見 `docs/ARCHITECTURE.md` 設定旋鈕 | 問答脈絡、選篇、路由模型、網搜（`ASK_ENABLE_WEB`、`ASK_WEB_TIMEOUT`）、agentic 補查 |
| `REPORT_*` | 同上 | 研報模型、配額、逐節預算、`REPORT_RENDERER`（`typst`，出事設 `weasyprint` 全域回退）、`REPORT_SEMAPHORE`、`REPORT_MAX_QUEUE` |
| `ASK_RERANK_*`、`REPORT_RERANK_*`、`RERANK_MODEL` | 開、50／120 候選 | rerank fail-open |
| `*_FAITHFULNESS_*`、`FAITHFULNESS_MODEL`、`FAITHFULNESS_TIMEOUT` | 開 | 忠實度抽查；關掉或壞掉都不會有錯誤訊息，只標 `degraded` |
| `EXTRACTOR`、`EXTRACTION_REVIEW_MIN` | `pypdf`、0.6 | 抽取器；生產 sync 環境檔設 `pdfplumber` |
| `OBJECT_STORAGE_MODE`、`R2_ENDPOINT_URL`、`R2_BUCKET`、`R2_ACCESS_KEY_ID`、`R2_SECRET_ACCESS_KEY`、`R2_PRESIGN_TTL_SECONDS` | `local` | 非 local 缺任一 fail-closed；TTL 上限 3600 |
| `ASK_MAX_QUEUE`、`SSE_HEARTBEAT_INTERVAL`、`REPORT_RUN_RETENTION_SECONDS` | 20、20、600 | web 層旋鈕 |
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

uv run python eval/run_ragas.py --out eval/baselines/candidate.json   # 問答評測（會 spawn claude）
make eval-compare BASE=eval/baselines/baseline-2026-09-02.json CAND=eval/baselines/candidate.json
```

- CI 四個 job 全為必要檢查（`.github/workflows/ci.yml`）：前端測試（tsc ＋ vitest）、後端測試（pytest）、schema 契約（PostgreSQL）、secret 掃描（gitleaks）。前端 job 把 `frontend/dist` 傳給後端 job，SPA 測試對真 build 驗證；後端設 `HF_HUB_OFFLINE=1` 與 `REPORT_MARK_REQUIRE_CJK=1`（研報 PDF 內容測試不准退回 skip）；schema job 套 `db/schema.sql` 兩次驗冪等並對帳 `db/expected_constraints.txt`。required check 名稱等於 job 的中文 `name`，改了要同步 GitHub 分支保護。
- 測試不連網、不載模型：LLM、嵌入、DB、檔案系統一律用假物件。async 測試用 `unittest.IsolatedAsyncioTestCase`，不用 pytest-asyncio。端點走 HTTP 層測。
- 測試絕不可寫 repo 根的真實環境檔（`tests/conftest.py` 會還原並 fail）。
- 評測 `eval/` 刻意不進 CI（會與 sync timer 搶 `claude` CLI）。`make eval-compare` 退出碼是結論：0 無劣化、1 劣化、2 不可比、3 有未分類指標。門檻 F>0.9／CP>0.8／AR>0.55 是政策；最新基準線 `eval/baselines/baseline-2026-09-02.json`。
- 改動對照表（改了 A 要動 B）在 `CLAUDE.md`；契約類測試清單在 `AGENTS.md`。

## 部署與維運

真相來源在 `deploy/`，不是機器上的 `/etc`；改了 unit 要 `sudo cp` 到 `/etc/systemd/system/` 再 `daemon-reload`。

| Unit | 排程 | 做什麼 |
|---|---|---|
| `report-mark-web.service` | 常駐 | `uv run uvicorn web.server:app --port 8097`，`Restart=always`，PATH drop-in 給 `claude` |
| `report-mark-sync.timer` | 每 3 小時 | rsync → 增量匯入 → 摘要 → 標題 → 摘錄 → 訊號（限量）→ 簡報 → 標題積壓（限量） |
| `report-mark-backup.timer` | 03:30 | `scripts/db_backup.sh`：七張不可重建的表 `pg_dump -Fc` → NAS，保留 7 日 ＋ 4 週；掛載不可寫刻意失敗不寫本地 |
| `report-mark-freshness.timer` | 08:30 | `make freshness`，rc 0／1／2／3（新鮮／資產停更／DB 查不到／管線停跑） |
| `report-mark-audit.timer` | 08:45 | `make db-audit`，唯讀，warn 也算失敗 |
| `report-mark-health.timer`、`report-mark-incident.timer` | 每 2 分鐘 | P4 探針 `scripts/check_web_health.sh`（只回報事實）與 P5 `scripts/incident_handler.sh`（去重、30 分鐘提醒、RESOLVED），webhook opt-in |
| `report-mark-linebot-health.timer`、`report-mark-linebot-incident.timer` | 每 2 分鐘 | LineBot 側同一套 |
| `report-mark-backfill.timer` | 01:00 | E1d 抽取回填，跑完手動 disable |
| `report-mark-r2-reconcile.timer` | 週一 07:00 | R2 對帳（唯讀） |
| `report-mark-metrics.service` | 常駐 | 硬體用量取樣 → `data/metrics/`（`make metrics`） |
| `report-mark-alert@.service` | `OnFailure` 觸發 | journal ＋ `data/unit_failures.log` ＋ webhook |

對外邊緣：`make up-edge`／`down-edge`／`edge-logs`／`edge-reload`（`deploy/docker-compose.yml`：nginx 限流 10r/s、靜態資產豁免；cloudflared 隧道）。健康判定打 `/healthz`，不看 `systemctl is-active`；oneshot 是否跑過用 `scripts/verify_oneshot_ran.sh`。`make help` 列出的破壞性 target（`reset-db`、`clean-data`、`ingest-lowio`）除非明講不要跑。

## 尚未實作與暫不納入

| 項目 | 說明 | 前置或阻礙 |
|---|---|---|
| findb 整合 | 唯讀 Serve API 取行情與名稱，讓雷達能算相對收盤的 upside、時效題能引真實數字 | findb 服務要可連，憑證與網路路徑屬跨專案部署問題；本 repo 只有 `scripts/align_findb_markets.py` 做離線代碼映射，`app/services/trusted_market_data.py` 的 provider 契約已備好 |
| MCP server | 把檢索、問答、雷達包成 agent 可消費的工具 | `hybrid_search` 需要已算好的 query embedding，BGE-M3 是 2–4 GB 的行內 CPU 單例；stdio server 每次 spawn 都要重載模型，常駐 HTTP 則需先做金鑰認證 |
| 對外 REST 與 API key | 機器可用的認證與 per-key 配額 | 全站只有一組共用帳密的 session cookie；昂貴端點僅靠 semaphore 擋；尚無外部消費者 |
| PDF 內文無障礙 text layer | 讓螢幕閱讀器讀得到研報內文 | `@embedpdf/plugin-selection` 解決的是選取與複製，DOM 裡沒有文字節點；EmbedPDF 沒有 text layer 外掛 |
| E4 欄位擷取「定位 → 局部擷取 → 錨回驗證」 | 訊號漏抽主因是「關鍵詞在視窗內卻沒抽到」而非截斷 | 嚴格錨回會讓 `rejected` 跳升且分不出模型正規化與幻覺，需先設計 `partial` 分級（`docs/EXTRACTION.md` §10） |
| 表格感知切塊 | `chunk_text` 純字元切法會切碎表格 | 依賴文件模型的 `blocks` 索引，已有基礎 |

暫不納入：掃描檔 OCR（`stopped_at = scanned` 已可查可回收，但無 OCR 分支）、GPU 加速 ingest、多帳號系統、使用者端通知或訂閱（維運告警鏈是另一回事，已上線）。

## 延伸文件

| 文件 | 內容 |
|---|---|
| `CLAUDE.md` | 給 AI 助理與貢獻者的鐵律、改動對照表、架構不變量摘要 |
| `AGENTS.md` | 貢獻者慣例：結構、風格、測試、commit、安全 |
| `docs/ARCHITECTURE.md` | 模組地圖、import 方向、檢索／問答／研報／讀取功能的不變量、Web 層、資料層、設定旋鈕 |
| `docs/WORKFLOW.md` | 端到端管線、逐階段 I/O 與參數、生產同步鏈、標籤詞彙、SSE 契約、R2 遷移順序、排錯 |
| `docs/EXTRACTION.md` | 抽取層現況：選型與授權、文件模型、回退、品質指標、快取、`extraction_log`、golden set、回填 |
| `docs/production_resilience.md` | 生產韌性：重啟策略、健康探針、告警鏈、備份與還原（含 `pg_restore` 演練） |
| `docs/EXTERNAL_ACCESS.md` | Cloudflare Tunnel ＋ nginx 對外存取 |
| `docs/nas_scheduled_sync_deployment.md` | NAS 定時同步的掛載、sudoers、timer 安裝 |
| `docs/LINEBOT_ALWAYS_ON.md` | LineBot 常駐與監控 |
| `docs/CAPACITY.md` | 硬體用量量測與上雲選型 |
| `docs/incidents/` 與 `docs/benchmarks/` | 事故報告與基準量測 |
