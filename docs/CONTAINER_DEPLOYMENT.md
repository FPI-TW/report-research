# 容器部署（映像＋docker compose）

給其他券商在自己環境自架的官方部署形態，也是廷豐測試環境之後的遷移目標（遷移本身另案進行）。單機或少數幾台 VM，一份應用映像跑四個角色，除了設定的 LLM 端點以外不需要任何外連。

現有的 systemd 主機部署（`make serve`、`deploy/systemd/`，程序見 `docs/production_resilience.md`）不受影響：容器的 schema 閘門、排程與設定都只在容器 entrypoint 裡。

## 檔案

| 檔案 | 內容 |
|---|---|
| `deploy/container/Dockerfile` | 多階段映像：前端 build（node）→ Python 相依（`uv sync --frozen --no-dev`，CPU-only torch，與 `pyproject.toml`／`uv.lock` 一致）→ build 時下載並封入模型權重 → 非 root 執行映像。context 是 repo 根 |
| `.dockerignore` | build context 的排除清單：`.env` 一切變體、`data/`、研報原檔、備份、`tests/`、相依與建置產物 |
| `deploy/container/entrypoint.py` | 角色分派、schema 閘門、migrate 流程、上傳 worker 迴圈 |
| `deploy/container/scheduler.py` | 排程工作清單與 systemd timer 的歸屬（下方對照表的真相來源） |
| `deploy/container/fetch_models.py` | build 階段下載權重，並以離線模式真的載入一次 |
| `deploy/compose/docker-compose.yml` | postgres、web、worker、scheduler、migrate（one-shot）、clamav（選配） |
| `deploy/compose/.env.example` | 設定範本（假值；標【必填】的沒填就拒絕啟動） |
| `deploy/demo/docker-compose.demo.yml` | 示範環境的 override：假 LLM、S3 測試伺服器（moto），並把 LLM 與連接器指過去（見「示範環境」） |
| `deploy/demo/docker-compose.real-llm.yml` | 疊在示範 override 之後，把 LLM 改回 `.env` 裡的真實設定 |
| `deploy/demo/fake_llm.py` | 示範與端到端測試用的假 LLM（OpenAI 相容、只用標準函式庫、回應決定性） |

Dockerfile 放在 `deploy/container/` 而不是 repo 根：repo 根的 `deploy/docker-compose.yml` 是廷豐測試環境的對外邊緣（nginx＋cloudflared），兩者無關；部署相關的真相來源都在 `deploy/` 底下。`.dockerignore` 必須在 build context 的根，所以在 repo 根。

## 系統需求

- Docker Engine 24 以上、Compose v2.24 以上（用到 `env_file` 的 `required` 與 bind 的 `create_host_path: false`）。BuildKit（Docker 23 起預設）。
- x86_64 或 arm64（supercronic 兩種架構的雜湊都釘在 Dockerfile）。
- 記憶體：web 常駐 BGE-M3 與 reranker；worker 入庫、scheduler 的 `backfill` 與 `retrieval-regression` 會各自再載一份嵌入模型。建議 16 GB 以上；開上傳（clamd 約 1.2–1.6 GB）再加 2 GB。實測數字見 `docs/CAPACITY.md`。
- 磁碟：映像本身數 GB（torch 與兩份模型權重；確切大小以 `docker image ls` 為準）；語料與向量在 `pgdata` volume，研報原檔與上傳在 `data` volume。
- 備份落點：一個與 Docker 資料不同磁碟（或網路儲存）的主機目錄。

## 建置映像

```bash
docker build -f deploy/container/Dockerfile -t report-mark:<版本> \
  --build-arg BUILD_COMMIT="$(git rev-parse HEAD)" \
  --build-arg BUILD_VERSION="$(git describe --tags --always)" \
  --build-arg BUILD_TREE_CLEAN="$(test -z "$(git status --porcelain)" && echo 1 || echo 0)" .
```

- 三個 `BUILD_*` 是版本對帳用（見「監控對接」的版本對帳）：映像不帶 `.git`，commit、版本 tag、建置時工作樹是否乾淨只能在 build 時傳進來，寫在映像的 `/app/build_info.json`。不給也能 build，管理後台只會顯示「建置時未提供」。

- build 需要網路（npm、PyPI、download.pytorch.org、Hugging Face、supercronic 與 uv 的發布檔）；**執行期不需要**。離線環境可在有網路的機器 build、`docker save`／`docker load` 搬進去，或推到內部 registry。
- 權重：嵌入用 `app/services/embed.py` 的 `MODEL_NAME`（BGE-M3），rerank 用 build arg `RERANK_MODEL`（預設與 `app/config.py` 相同）。改 rerank 模型要重 build，執行期的 `RERANK_MODEL` 也設同一個值。
- build 的最後一步以非 root 身分、`HF_HUB_OFFLINE=1` 真的載入兩個模型；少了任何權重檔，build 就在那裡失敗。
- 映像預設 `EXTRACTOR=pdfplumber`（版面層抽取，與 systemd 主機的 sync 相同）。程式本身的預設是舊路徑 `pypdf`，容器裡的連接器、上傳 worker 與回填都在這份映像裡跑，要改回舊路徑才在 `.env` 設 `EXTRACTOR=pypdf`。

## 角色

`docker compose` 以 `command` 指定角色（`entrypoint.py` 的第一個參數）：

| 角色 | 做什麼 | 啟動前 |
|---|---|---|
| `web` | `uvicorn web.server:app`（:8097，單一行程） | 必填設定、schema 閘門 |
| `worker` | 上傳 worker 迴圈：每 `UPLOAD_WORKER_INTERVAL_SECONDS`（預設 300）秒跑一輪 `scripts/process_uploads.sh` | schema 閘門 |
| `scheduler` | supercronic 跑下方對照表的框架排程 | schema 閘門 |
| `migrate` | one-shot：備份 → 確認 → `alembic upgrade head` | — |
| `schema-version` | 只比一次版本並印出結論（唯讀；退出碼同 `make schema-version`） | — |
| `create-admin` | `scripts/create_admin.py`，其餘參數照傳 | schema 閘門 |
| 其他 | 原樣執行（例如 `bash`、`python scripts/llm_blocked.py`） | 不過閘門 |

所有角色都要求 `REPORT_MARK_DB_URL` 有值（容器不使用程式內建的預設連線）；web 另外要求 `REPORT_MARK_SESSION_SECRET` 至少 32 字元且不是範本佔位值。

### schema 閘門

web、worker、scheduler、create-admin 啟動時只**檢查** schema 版本（與 `make schema-version` 同一個判斷，`scripts/schema_baseline.py` 的 `check_version`），不做任何變更：

| DB 狀態 | 結果 |
|---|---|
| 一致 | 啟動 |
| 落後 | 拒起（退出碼 1），印出 `docker compose run --rm migrate` |
| 空庫、未接管、超前、不明確 | 拒起（退出碼 2），印出原因與下一步 |
| 連不上 | 在 `SCHEMA_GATE_WAIT_SECONDS`（預設 120）秒內重試，仍連不上退出碼 3 |

刻意不在啟動時自動 migrate：多個角色同時起來時誰先升級是競賽，而且升級前必須先有一份驗證過的全庫備份、必須由人逐字確認目標。

## 首次安裝

```bash
cd deploy/compose
cp .env.example .env && chmod 600 .env       # 填【必填】：POSTGRES_PASSWORD、REPORT_MARK_SESSION_SECRET、LLM 金鑰
sudo install -d -o 10001 -g 10001 -m 0750 /srv/report-mark-backups   # BACKUP_HOST_DIR 指向它
docker compose run --rm migrate              # 空庫：從 baseline 建到 head（免確認、不備份）
docker compose run --rm web create-admin --username <名稱>   # 第一位管理員（互動輸入密碼）
docker compose up -d
curl -fsS http://127.0.0.1:8097/readyz       # {"status":"ok"}
```

- `PROFILE_HOST_DIR` 指向 profile 目錄（profile.toml、`[sources]` 引用的發行機構名單檔、assets/、plugins/、eval/），唯讀掛到容器的 `/app/profile`（`PROFILE_DIR`）。
- 掛載的主機目錄刻意不讓 Docker 自動建立：路徑打錯時寧可起不來。`data` 與 `pgdata` 是 named volume，第一次掛上時擁有者就是容器的 UID 10001。
- `/readyz`（免登入）四項全過才 200：DB、嵌入模型（已載入或權重在映像裡）、profile、LLM 設定（不呼叫 LLM）。web 的 compose healthcheck 用它；`/healthz` 仍是只探 DB 的存活探測。

## 升級

```bash
docker compose pull                            # 或換 .env 的 REPORT_MARK_IMAGE 成新版映像
REPORT_MARK_MIGRATE_CONFIRM=<host:port/db> docker compose run --rm migrate
docker compose up -d
```

1. **換映像**。舊容器還在跑，不受影響。
2. **migrate**。DB 已在新映像的 head 時什麼都不做；落後時依序：檢查 `REPORT_MARK_MIGRATE_CONFIRM` 逐字等於 `host:port/db`（不符就在備份前拒絕，並印出正確的值）→ 全庫 `pg_dump -Fc` 到 `MIGRATE_BACKUP_DIR`（預設備份落點底下的 `migrate/`）並驗證（可用空間 ≥ 庫大小＋2 GiB、`MIGRATE_DUMP_TIMEOUT_MIN` 分鐘內完成、PGDMP 檔頭、`pg_restore -l` 讀得出 TABLE DATA；與 `make schema-stamp-baseline` 同一套 preflight）→ `alembic upgrade head`（`db/migrations/env.py` 的守門對同一個確認值再判一次）→ 讀回版本必須一致。任何一步失敗都不升級，備份留在原地。
3. **up -d**。新的 web／worker／scheduler 過閘門後啟動。

compose 只把 `.env` 與 shell 裡明確傳入的變數交給容器：確認值寫在指令前（如上）或 `.env` 都可以，升級完把 `.env` 裡那行刪掉。回退只回退映像、不 downgrade DB：新映像加的表與欄位留著，舊映像對「超前」的 DB 會拒起，要回退就得連同 DB 從 migrate 前的備份還原。

## 備份與還原

| 備份 | 何時 | 落點 | 內容 |
|---|---|---|---|
| 每日（scheduler 的 `backup`） | 03:30 | `$BACKUP_HOST_DIR/daily/`、`weekly/` | `scripts/db_backup.sh` 的不可重建表（清單在該檔的 `BACKUP_TABLES`），保留 7 日＋4 週 |
| migrate 前 | 每次升級 | `$BACKUP_HOST_DIR/migrate/` | 全庫（含語料與向量） |
| 稽核錨點、刪帳 tombstone | 每日／每小時 | `$BACKUP_HOST_DIR/` | `audit-anchors.jsonl`、`account-tombstones.jsonl` |

容器裡的每日備份直接連 DB（`DB_BACKUP_VIA=direct`，連線由 entrypoint 從 `REPORT_MARK_DB_URL` 換算成 libpq 環境變數，密碼不進 argv），其餘行為與主機版相同：落點必須是掛載點而且可寫，否則失敗、不退回本地路徑。語料層（`research_report`、`report_chunk`）不在每日備份裡，重建靠原檔重新入庫；要整庫保護就另外對 `pgdata` 做 volume 快照，或定期留一份 migrate 那種全庫備份。

還原（以全庫備份為例；不可重建表的單表還原與演練步驟見 `docs/production_resilience.md`「備份與還原」）：

```bash
docker compose stop web worker scheduler
docker compose exec -T postgres dropdb -U research research
docker compose exec -T postgres createdb -U research research
docker compose exec -T postgres pg_restore -U research -d research --no-owner < /srv/report-mark-backups/migrate/<檔名>.dump
docker compose run --rm web python scripts/replay_deletions.py   # 還原後一定要跑：已刪除帳號的問答不能復活
docker compose up -d
```

## systemd timer 對照表

`deploy/systemd/` 的每一支 timer 都有歸屬（`deploy/container/scheduler.py`，`tests/test_container_deploy.py` 對帳這張表、也逐支比對排程時間與 timer 的 OnCalendar）。框架排程在容器的 `TZ`（預設 Asia/Taipei）下解讀；預設關的工作以 `SCHEDULER_ENABLE` 開啟，預設開的以 `SCHEDULER_DISABLE` 關掉（逗號分隔的工作名，打錯名字 scheduler 拒絕啟動）。

| Timer | 歸屬 | 容器裡 |
|---|---|---|
| `report-mark-backfill.timer` | scheduler | `backfill`（預設開）：01:00，抽取版本回填，會載入嵌入模型 |
| `report-mark-analytics-rollup.timer` | scheduler | `analytics-rollup`（預設開）：02:20 |
| `report-mark-backup.timer` | scheduler | `backup`（預設開）：03:30，直接連 DB，落點是備份掛載 |
| `report-mark-audit-anchor.timer` | scheduler | `audit-anchor`（預設開）：04:15，錨點寫在備份掛載 |
| `report-mark-replay-deletions.timer` | scheduler | `replay-deletions`（預設開）：04:30 |
| `report-mark-security-retention.timer` | scheduler | `security-retention`（預設開）：04:45 |
| `report-mark-schema-check.timer` | scheduler | `schema-check`（預設開）：05:20；compose 的 postgres 帳號是 superuser，可做完整比對；外部 DB 沒有 CREATEDB 時設 `SCHEMA_CHECK_MODE=version` |
| `report-mark-audit.timer` | scheduler | `db-audit`（預設開）：08:45 |
| `report-mark-db-snapshot.timer` | scheduler | `db-snapshot`（預設開）：每小時 :40 |
| `report-mark-delete-accounts.timer` | scheduler | `delete-accounts`（預設開）：每小時整點，tombstone 寫在備份掛載 |
| `report-mark-retrieval-regression.timer` | scheduler | `retrieval-regression`（預設關）：07:40；要先對這個部署擷取一次基準（`python scripts/retrieval_regression.py capture`），會載入嵌入模型 |
| `report-mark-r2-reconcile.timer` | scheduler | `r2-reconcile`（預設關）：週一 07:00；只在物件儲存開著時有意義 |
| `report-mark-upload.timer` | worker | 常駐的 `worker` 服務，每 `UPLOAD_WORKER_INTERVAL_SECONDS` 秒一輪 |
| `report-mark-sync.timer` | 廷豐主機層 | 來源同步（NAS 掛載／R2 inbox）；框架的對應是下方框架專屬排程的 `connectors` |
| `report-mark-freshness.timer` | 廷豐主機層 | 判讀 sync 殼的心跳，隨 sync 一起 |
| `report-mark-beacon.timer` | 廷豐主機層 | 主機信標 |
| `report-mark-load-observations.timer` | 廷豐主機層 | 主機監控 spool 匯入 |
| `report-mark-rollup-observations.timer` | 廷豐主機層 | 主機監控觀測的保留與聚合 |
| `report-mark-health.timer` | 廷豐主機層 | web 探針（P5） |
| `report-mark-incident.timer` | 廷豐主機層 | 事件處理（P5／Slack） |
| `report-mark-container-health.timer` | 廷豐主機層 | 容器探針 |
| `report-mark-container-incident.timer` | 廷豐主機層 | 事件處理 |
| `report-mark-edge-health.timer` | 廷豐主機層 | 邊緣探針 |
| `report-mark-edge-incident.timer` | 廷豐主機層 | 事件處理 |
| `report-mark-feed-health.timer` | 廷豐主機層 | 來源主機信標探針 |
| `report-mark-feed-incident.timer` | 廷豐主機層 | 事件處理 |
| `report-mark-host-health.timer` | 廷豐主機層 | 主機資源探針 |
| `report-mark-host-incident.timer` | 廷豐主機層 | 事件處理 |
| `report-mark-linebot-health.timer` | 廷豐主機層 | LINE bot 探針 |
| `report-mark-linebot-incident.timer` | 廷豐主機層 | 事件處理 |
| `report-mark-security-health.timer` | 廷豐主機層 | 安全事件探針 |
| `report-mark-security-incident.timer` | 廷豐主機層 | 事件處理 |

### 框架專屬排程

沒有對應 systemd timer 的工作（廷豐主機上同樣的事由主機層的 sync 殼做）。開關與上表相同（`SCHEDULER_DISABLE`／`SCHEDULER_ENABLE`）。

| 工作 | 排程 | 說明 |
|---|---|---|
| `connectors` | `*/15 * * * *` | 預設開。`scripts/connector_sync.py`：profile `[connectors].enabled` 的連接器到了輪詢時間（各自的 `poll_minutes`，預設 180 分鐘）才跑一輪「落地新檔 → 入庫 → 摘要／標題／摘錄（`--hashes-file`）→ 訊號（`--limit`）→ 簡報 → 標題積壓（`--limit`）」，步驟與參數同 sync 鏈第 4 段起，差異只有分層回填（摘要、標題、摘錄只收本輪第二層的新研報，訊號與標題積壓帶 `--min-report-date`，見 `docs/WORKFLOW.md`「首次導入」）；沒有啟用的連接器、或都沒到期就是 no-op。設定、側檔格式與落地資料夾見 `docs/WORKFLOW.md`「資料來源連接器」 |
| `backfill-tiers` | `50 * * * *` | 預設開，但 profile 的 `[backfill].background_enabled` 預設 `false`＝no-op（開關在 profile，不在 `SCHEDULER_ENABLE`）。`scripts/backfill_tiers.py --tier 3 --scheduled`：首次導入的第三層背景補齊（`report_date` 早於 `[backfill].recent_months` 個月、或為 NULL 的研報，補摘要、標題、摘錄、訊號），每輪最多 `max_reports_per_round` 份、每個台北日最多 `daily_token_limit` token，到上限就停、下一輪再續；以 `nice` 降低優先權，各批次照舊取 LLM 批次鎖（撞鎖就停）。帳在 `data/backfill/state.json`。步驟見 `docs/WORKFLOW.md`「首次導入」 |

連接器的資料夾要讓 web 與 scheduler 在**同一個路徑**看得到：入庫後 `research_report.file_path` 指向落地的檔案，`OBJECT_STORAGE_MODE=local` 時 web 從那裡交原檔。`s3`／`plugin` 的預設落地資料夾在 `data/connectors/<名稱>/`（`data` volume，已共用）；`folder` 監看的是部署方自己掛上的目錄（SMB／NFS 由主機掛載，框架不做 mount），以 compose 的 override 檔（與 docker-compose.yml 同目錄的 docker-compose.override.yml）把它以唯讀 bind 掛進 web 與 scheduler，`profile.toml` 的 `path` 寫容器裡的路徑：

```yaml
services:
  web:
    volumes:
      - {type: bind, source: /srv/research-inbox, target: /srv/research-inbox, read_only: true, bind: {create_host_path: false}}
  scheduler:
    volumes:
      - {type: bind, source: /srv/research-inbox, target: /srv/research-inbox, read_only: true, bind: {create_host_path: false}}
```

`s3` 連接器的憑證（`<env>_BUCKET`、`<env>_ACCESS_KEY_ID`、`<env>_SECRET_ACCESS_KEY`，選填 `<env>_ENDPOINT`、`<env>_REGION`；`<env>` 是 profile 裡的 `env`）寫進 `deploy/compose/.env`，scheduler 讀得到即可。

常駐服務：`report-mark-web.service` → `web`；維運代理（`report-mark-ops-agent*.service`）、`report-mark-metrics.service`、`report-mark-alert@.service` 屬廷豐主機層。容器裡的工作失敗只寫進容器日誌（`docker compose logs scheduler`），沒有 `OnFailure` 告警。

### 互斥

各工作沿用 repo 既有的鎖：LLM 批次的 `data/.claude_cli.lock`（`scripts/_claude_lock.py`）、上傳 worker 的整輪鎖等，都是 `data/` 底下的 flock。web、worker、scheduler 掛同一個 `data` volume，flock 是核心層級、以檔案為準，跨容器照樣互斥（撞鎖 rc=75 是「不跑」不是「跑壞」）。supercronic 預設不讓同一個工作重疊。不要把 `data` 拆成各角色各自一份 volume，也不要把它放在不支援 flock 的網路檔案系統上。

## 零外連清單

執行期唯一預設的外連是設定的 LLM 端點。映像內：`HF_HUB_OFFLINE=1`、`TRANSFORMERS_OFFLINE=1`（權重在映像裡）、`UV_OFFLINE=1`／`UV_NO_SYNC=1`（殼腳本裡的 `uv run` 直接用映像內的環境，不解析也不下載相依）。驗證方式：把 web 接到沒有對外路由的網路（compose 的 `internal: true` 網路，或 `docker run --network none`），`/readyz` 照樣是 200。

| 整合 | 預設 | 開法 | 會連到哪裡 |
|---|---|---|---|
| LLM | 開（必填） | `LLM_PROVIDER`＋對應金鑰；端點可指到內部閘道或自架的相容端點（`openai_compatible`，旋鈕見 repo 根 `.env.example`） | 設定的端點 |
| 物件儲存 | 關（`OBJECT_STORAGE_MODE=local`，原檔在 `data` volume） | `OBJECT_STORAGE_MODE=hybrid`／`r2`＋`R2_*` | S3 相容端點 |
| 網搜 | 關（`ASK_ENABLE_WEB=0`；沒有後端，保持關） | — | — |
| 告警 webhook | 關（`REPORT_MARK_ALERT_WEBHOOK` 空） | 設 webhook URL | 該 URL |
| 通用 webhook 告警 | 關（`ALERT_WEBHOOK_URL` 空） | 設 webhook URL（見「監控對接」） | 該 URL |
| 上傳的病毒碼更新 | 只在起了 clamav profile 時 | `docker compose --profile clamav up -d` | freshclam 連 ClamAV 的病毒碼伺服器 |
| 來源主機信標 | 關（`HOST_BEACON_WATCH` 空） | 廷豐主機層，框架不用 | 物件儲存 |
| S3 相容來源（連接器） | 關（profile 沒有啟用 `kind = "s3"` 的連接器） | profile `[connectors]` 啟用＋`<env>_*` 憑證 | 設定的 bucket 端點 |
| plugin 連接器 | 關（profile 沒有啟用 `kind = "plugin"` 的連接器） | profile 的 `plugins/<名稱>.py` | 由 plugin 決定（部署方自己的程式） |

上傳要掃毒（fail-closed：clamd 不可用或病毒碼超過 72 小時，上傳停在隔離區不放行）。不能讓 freshclam 外連的環境，把 `deploy/clamav/conf/freshclam.conf` 的 `DatabaseMirror` 指到內部鏡像，或在 compose 的 clamav 服務設 `CLAMAV_NO_FRESHCLAMD=true` 並自行定期把病毒碼放進 `clamav-db` volume。

## 對外入口

web 預設只發佈在 `127.0.0.1:8097`（`WEB_PUBLISH`）。對外上傳 API（`POST /external/v1/uploads`）的 body 最大到 `UPLOAD_MAX_BYTES`（預設 25 MiB），代理的 body 上限要比它寬（`deploy/nginx.conf` 給 `/external/` 30m），否則超限的請求拿到代理的 HTML 錯誤頁而不是 413 JSON。對外一律經部署方自備的 TLS 反向代理：非 localhost 的明文 HTTP 不會建立登入 session。代理不在同一台主機的 loopback 時，App 看到的來源是 Docker 網路的閘道位址，要把它列進 `REPORT_MARK_TRUSTED_PROXY_CIDRS`，或改用共享祕密 `REPORT_MARK_EDGE_SECRET`（代理注入 `X-Edge-Secret`），規則見 `docs/EXTERNAL_ACCESS.md`。對外 API（`/external/v1/*`）要讓代理透傳 `Authorization`。

## 監控對接

框架只提供**應用層訊號**，接到部署方自己的監控（Prometheus＋Grafana、Zabbix、ELK 等）：`/metrics`、結構化 JSON 日誌、`/readyz`／`/healthz`、版本對帳與選配的 webhook 告警。廷豐的維運代理、主機信標、事件處理、探針與 Slack 屬廷豐主機層，框架不需要也不依賴它們。

### `GET /metrics`

Prometheus 文字格式，不登入。實作與約束在 `web/metrics.py` 的模組 docstring。

**存取控制**：不通過一律 404（與不存在的路徑無從分辨）。

- 對端位址必須落在 `METRICS_ALLOWED_CIDRS`（逗號分隔 CIDR；沒設＝只收 loopback）。全部寫錯時是誰都不收，不是全開。
- 帶任何代理 header（`X-Forwarded-For`、`X-Real-IP`、`Forwarded`、`CF-Connecting-IP`、`X-Forwarded-Proto`、`X-Forwarded-Host`、`X-Edge-Secret`）的請求一律不收：經反向代理進來的就是外部請求。部署方的反向代理要照常帶 `X-Forwarded-For`，或直接在代理上擋掉 `/metrics`。
- `METRICS_ENABLED=0` 整個關掉。
- compose 部署：讓 Prometheus 加入同一個 compose 網路，直接抓 `web:8097/metrics`，再把那個網路的網段（`docker network inspect` 看得到）設進 `METRICS_ALLOWED_CIDRS`。不要為了 `/metrics` 把 web 對外發佈到 loopback 以外的位址。

**多行程**：計數器是 web 行程內的狀態。web 只會有一個 worker（多 worker 在啟動時就拒起，`web/concurrency.py`），所以數字是正確的；web 重啟時計數器歸零（`rate()`／`increase()` 本來就處理）。worker、scheduler 與批次是別的行程：它們的 LLM 呼叫不在 LLM 指標內，批次健康改看從 DB 算的新鮮度指標與 webhook 告警。

**標籤**：`route` 是路由模板（例如 `/api/reading/{file_hash}`），比對不到的是 `<unmatched>`；沒有查詢字串、路徑參數、帳號、IP 或問答內容。

| 指標 | 型別 | 標籤 | 意義 |
|---|---|---|---|
| `report_mark_http_requests_total` | counter | `method`、`route`、`status` | HTTP 請求數（含認證擋下的 401／302 與 500） |
| `report_mark_http_request_duration_seconds` | histogram | `method`、`route` | 請求耗時；SSE（問答）是整條串流的長度 |
| `report_mark_http_requests_in_progress` | gauge | — | 處理中的請求（含開著的 SSE） |
| `report_mark_qa_requests_total` | counter | `outcome` | 問答結局：`ok`、`llm_error`、`error`、`cancelled`（使用者中途停止或斷線）、`queue_full`（排隊滿回 429） |
| `report_mark_qa_llm_errors_total` | counter | `kind` | 問答因 LLM 不可用而失敗的類別（`auth`、`quota`、`config`、`content_filter`、`timeout`、`overloaded`、`network`…） |
| `report_mark_llm_calls_total` | counter | `task`、`provider`、`result` | web 行程的 LLM 呼叫數（`result`＝`ok`／`error`） |
| `report_mark_llm_call_failures_total` | counter | `task`、`provider`、`kind` | LLM 呼叫失敗的類別 |
| `report_mark_llm_call_duration_seconds` | histogram | `task`、`provider` | LLM 呼叫耗時（含傳輸層重試） |
| `report_mark_llm_tokens_total` | counter | `task`、`provider`、`type` | token 用量：`prompt_cached`、`prompt_uncached`、`completion`、`reasoning`（`reasoning` 含在 `completion` 內） |
| `report_mark_llm_breaker_active` | gauge | — | LLM 批次斷路器標記在有效期內（1）：批次會拒跑 |
| `report_mark_batch_freshness_age_seconds` | gauge | `asset` | 批次資產最新產出距今（NaN＝從未產出）；`asset`：`pipeline`、`corpus`、`summary`、`takeaway`、`signal` |
| `report_mark_batch_freshness_threshold_seconds` | gauge | `asset` | 停更門檻（0＝不告警） |
| `report_mark_batch_freshness_stale` | gauge | `asset` | 停更（1）；判定與 `scripts/check_batch_freshness.py`、管理後台資料健康頁相同（含語料閘的抑制） |
| `report_mark_upload_queue` | gauge | `state` | 研報上傳依狀態的筆數（狀態詞彙見 `app/services/uploads.py`） |
| `report_mark_upload_oldest_in_flight_age_seconds` | gauge | — | 最舊一筆處理中（隔離、掃描中、待入庫、處理中）的上傳已等待的秒數 |
| `report_mark_signal_source_up` | gauge | `source` | 上面兩組從 DB 算的值這次查不查得到（`freshness`、`uploads`）；查不到時該組指標清空，不留舊值 |
| `report_mark_db_pool_size`、`report_mark_db_pool_max_overflow`、`report_mark_db_pool_checked_out`、`report_mark_db_pool_checked_in` | gauge | — | DB 連線池（`DB_POOL_SIZE`＋`DB_MAX_OVERFLOW` 是上限） |
| `report_mark_gate_in_use`、`report_mark_gate_capacity`、`report_mark_gate_waiting` | gauge | `gate` | 行程內併發閘（問答 `ask` 等）的使用中、上限與排隊 |
| `report_mark_build_info` | gauge | `commit`、`version`、`tree_clean`、`source` | 恆為 1；版本對帳看標籤 |
| `process_*`、`python_*` | — | — | prometheus_client 內建的行程指標（記憶體、CPU、檔案描述元、GC） |

`pipeline` 是 sync 殼的管線心跳（廷豐主機層）；容器部署沒有它，`report_mark_batch_freshness_stale{asset="pipeline"}` 會一直是 1，告警規則不要用它。從 DB 算的值快取 15 秒、每次查詢 5 秒逾時，抓取間隔建議 30 秒以上。

**建議告警規則**（Prometheus；門檻依部署調）：

```yaml
groups:
  - name: report-mark
    rules:
      - alert: ReportMarkDown
        expr: up{job="report-mark"} == 0
        for: 5m
      - alert: ReportMarkHigh5xx
        expr: sum(rate(report_mark_http_requests_total{status=~"5.."}[5m])) / sum(rate(report_mark_http_requests_total[5m])) > 0.05
        for: 10m
      - alert: ReportMarkQaLlmAccount
        # 帳號層級（金鑰、額度、設定）：每一題都會失敗，使用者無能為力
        expr: increase(report_mark_qa_llm_errors_total{kind=~"auth|quota|config"}[10m]) > 0
      - alert: ReportMarkQaFailing
        expr: sum(increase(report_mark_qa_requests_total{outcome=~"llm_error|error"}[30m])) / sum(increase(report_mark_qa_requests_total[30m])) > 0.2
        for: 15m
      - alert: ReportMarkQaQueueFull
        expr: increase(report_mark_qa_requests_total{outcome="queue_full"}[15m]) > 0
      - alert: ReportMarkLlmBreaker
        expr: report_mark_llm_breaker_active == 1
      - alert: ReportMarkBatchStale
        expr: report_mark_batch_freshness_stale{asset=~"summary|takeaway"} == 1
        for: 1h
      - alert: ReportMarkUploadBacklog
        expr: report_mark_upload_oldest_in_flight_age_seconds > 7200
        for: 15m
      - alert: ReportMarkSignalSourceDown
        expr: report_mark_signal_source_up == 0
        for: 15m
      - alert: ReportMarkDbPoolSaturated
        expr: report_mark_db_pool_checked_out >= report_mark_db_pool_size + report_mark_db_pool_max_overflow
        for: 5m
```

存活與就緒另有 `/healthz`（只探 DB）與 `/readyz`（DB、模型、profile、LLM 設定），回應都只有 `status` 一鍵。

### 結構化日誌

`LOG_FORMAT=json`（映像的預設；systemd 主機沒設，維持原本的一行文字）時，web 每行一個 JSON 物件寫到 stdout，容器日誌（`docker compose logs`、json-file driver）直接收：

| 欄位 | 內容 |
|---|---|
| `ts` | UTC、ISO-8601、毫秒（`2026-11-10T03:00:00.123Z`） |
| `level` | `DEBUG`／`INFO`／`WARNING`／`ERROR`／`CRITICAL`（門檻 `LOG_LEVEL`） |
| `logger` | logger 名稱（`web.request_log`、`app.services.llm_http`…） |
| `request_id` | 這一行屬於哪個 HTTP 請求（回應 header `X-Request-Id`、錯誤回應的 `request_id` 是同一個值）；批次與啟動期為 `-` |
| `message` | 訊息 |
| `exc_type`、`exc` | 只在有例外時：例外型別與 traceback |

只有這幾個欄位（白名單）。遮罩：名稱像祕密的環境變數之值、DB 密碼、URL 裡的帳密、`Bearer` token、URL 的查詢字串、SQL 錯誤訊息的 bind 參數一律換成 `[redacted]`；uvicorn 的 access log 去掉查詢字串與對端位址。問答原文與查詢字串本來就不進日誌。每個 `/api/*` 請求另有一行 `http <method> <path> status=… elapsed_ms=…`（`web.request_log`）。worker 與 scheduler 跑的殼腳本與批次照舊輸出文字，排程工作結束時另有一行 `[run_job] job=… rc=… elapsed_s=…`。

### 版本對帳

`GET /api/admin/diagnostics` 的 `versions.build`（管理後台「營運 → 診斷」的「版本」卡）與 `report_mark_build_info` 回報建置 commit、版本 tag、建置時工作樹是否乾淨與來源：映像是 build 時以 build arg 寫入（見「建置映像」）；systemd 主機是部署時 `make build-info` 從 git 讀出寫進 repo 根的 build_info.json，檔案與正在跑的 commit 不同時作廢（只回 `.git` 的 commit，`stale_file=true`）。唯讀，不擋任何操作。

### 通用 webhook 告警（選配，預設關）

設 `ALERT_WEBHOOK_URL` 才會送（URL 只經環境變數，不進 argv、不進日誌）。給沒有 Alertmanager、只想出事時往聊天室或工單系統丟一則的部署；與廷豐既有的 `REPORT_MARK_ALERT_WEBHOOK` 並存、互不取代。

| 事件 | 何時送 |
|---|---|
| `batch_failed` | scheduler 的工作非零退出（撞鎖的 75 不算） |
| `llm_breaker_tripped` | LLM 批次斷路器跳脫 |
| `upload_backlog` | 最舊一筆處理中的上傳超過 `ALERT_UPLOAD_BACKLOG_MINUTES`（預設 120；0＝不檢查），web 每 `ALERT_CHECK_INTERVAL_SECONDS`（預設 300）秒檢查一次 |

`POST` JSON：`source`、`event`、`severity`、`summary`、`text`、`details`、`host`、`at`。`text` 是一行可讀的摘要，Slack、Teams、Mattermost、Google Chat 的 incoming webhook 都直接認得；`details` 只有計數、工作名、退出碼與時間，不帶日誌內容或使用者資料。同一個事件（同一個工作）在 `ALERT_WEBHOOK_COOLDOWN_MINUTES`（預設 60）內只送一次；只送「出事」，恢復與趨勢看 `/metrics`。送不出去不影響批次與 web。

## 示範環境

在一台機器上把虛構的示範券商（`profiles/demo`）跑起來給客戶看：同一份映像、同一份 compose，多疊一個 override（`deploy/demo/docker-compose.demo.yml`），多兩個服務——假 LLM（`deploy/demo/fake_llm.py`）與 S3 測試伺服器（moto，Apache-2.0；刻意不用 MinIO：AGPL，與授權守門衝突）。預設完全離線、零費用。示範 profile 與廷豐的差異：機構與產品名（虛構）、自製 logo、青綠強調色、市場只開台股／美股／總體經濟、研報由 S3 bucket＋metadata 側檔送進來、背景補齊開著且上限小。

| 元件 | 示範環境 | 換成真的 |
|---|---|---|
| 語料 | `scripts/demo_corpus.py` 以固定種子產生約 30 份虛構 PDF（頁首頁尾、雙欄、表格）與側檔，其中幾份側檔故意寫錯；內容、機構、分析師與數字都是虛構的，頁首頁尾與文末都有標示 | 券商自己的 bucket（profile 的 `[connectors]`） |
| LLM | 假 LLM：一個任務一個模型名（`fake_llm.MODELS`），回應格式正確、內容標「示範產出」；問答的回答刻意夾一個不存在的來源編號，畫面上會看到「（無效引用）」 | 再疊 `deploy/demo/docker-compose.real-llm.yml`，LLM 設定寫在 `.env` |
| S3 | moto，資料只在記憶體（容器重建就清空） | 真的 S3 相容端點（`CONNECTOR_DEMO_*`） |

```bash
uv sync                                        # 主機上產生語料要 weasyprint（dev 相依）與 fonts-noto-cjk
uv run python scripts/demo_corpus.py --out /srv/demo-corpus     # 30 份約 3 分鐘
cd deploy/compose
cp .env.example .env && chmod 600 .env         # 填 POSTGRES_PASSWORD、REPORT_MARK_SESSION_SECRET；PROFILE_HOST_DIR=../../profiles/demo
                                               # LLM 那段留空即可（demo override 會指到假 LLM）
alias dc='docker compose -f docker-compose.yml -f ../demo/docker-compose.demo.yml'
dc up -d postgres s3 fake-llm
dc run --rm migrate
dc run --rm web create-admin --username demo
AWS_ACCESS_KEY_ID=moto AWS_SECRET_ACCESS_KEY=moto aws --endpoint-url http://127.0.0.1:5000 s3 mb s3://demo-research
AWS_ACCESS_KEY_ID=moto AWS_SECRET_ACCESS_KEY=moto aws --endpoint-url http://127.0.0.1:5000 s3 sync /srv/demo-corpus/corpus s3://demo-research/research/
dc run --rm scheduler python scripts/connector_sync.py --force   # 不等排程（每 15 分鐘）
dc up -d                                       # web、worker、scheduler；瀏覽 http://127.0.0.1:8097
```

- 沒有 aws cli 時，`uv run python scripts/e2e_compose.py --keep` 會把同樣的流程（含上傳）跑完並留著 project，印出拆除指令；它的 web 埠是隨機的，看 log 第一行。
- 要給客戶看真實的問答品質：`.env` 寫好真的 LLM 設定（`LLM_PROVIDER`＋該 provider 的金鑰、端點與模型宣告，見 repo 根 `.env.example`），`dc` 再加 `-f ../demo/docker-compose.real-llm.yml`。標註、摘要等批次照樣會打真的 LLM、照樣計費。
- 背景補齊（`backfill-tiers`）每小時 :50 會補第三層（13～18 個月前的那幾份）的摘要、標題與摘錄；示範 profile 的上限是每輪 10 份、每日 30 萬 token。
- 收掉：`dc down -v`（連 DB 與 `data` volume 一起刪）。

### 端到端測試（`scripts/e2e_compose.py`）

上面整套流程的自動化版本，CI 由獨立的 `.github/workflows/e2e.yml` 跑（**不是**必要檢查；只在會改變映像或端到端行為的檔案變動時觸發，也可以手動觸發）。每次用獨立的 compose project（`-p e2e-<亂數>`）、隨機埠、暫存目錄當 project directory 與備份落點，不碰其他 compose project；跑完一律 `down -v`。

```bash
uv run python scripts/e2e_compose.py --build      # build 映像（重用 BuildKit 快取）再跑；約 15 分鐘（不含 build）
uv run python scripts/e2e_compose.py --image report-mark:e2e --corpus /srv/demo-corpus   # 映像與語料都已備好
```

驗證項目：空庫 `migrate`、`create-admin --password-stdin`、`connector_sync.py --force` 的入庫份數、側檔欄位（`title`／`source`／`market`）生效、`research.report_metadata`、錯誤側檔進失敗清單與 `last_stats` 的 `sidecar_invalid`、第二層的摘要／錨定的摘錄／訊號、`backfill_tiers.py --tier 3` 補上第三層、`/readyz`、`/api/branding` 是示範品牌、登入、`/api/markets` 只有子集、`/api/search` 有結果、未啟用的市場回 422、`/api/ask` 的 SSE 走完且引用只落在來源編號內（無效編號在串流中被替換）、閱讀頁 `/text`、容器內 loopback 的 `/metrics`。退出碼 0 全過、1 有失敗、2 環境不符。

- web 預設關 rerank（`ASK_RERANK_ENABLED=0`，省約 2 GB 記憶體；`--rerank` 打開）。峰值約 4 GB（入庫時一份嵌入模型、web 一份，不同時）。
- 真實 LLM 版本：`--real-llm`，設定取自 `E2E_LLM_PROVIDER`（預設 `openai`）、`E2E_LLM_API_KEY`、`E2E_LLM_MODEL`、`E2E_LLM_BASE_URL`（選填），只寫進暫存目錄的 `.env`（0600）。workflow 的 `workflow_dispatch` 勾 `real_llm` 才跑，repo 沒設 `E2E_LLM_API_KEY`／`E2E_LLM_MODEL` secret 就跳過。真實模型的回答不是決定性的，所以不檢查無效引用標記。
- CI 的映像層快取在 GitHub Actions cache（`mode=min`）；未命中時 build 要下載約 4.5 GB 權重。repo 的快取額度（10 GB）被擠掉時只是變慢，不會失敗。

## 從主機部署遷入

既有的主機部署（DB 已被 alembic 接管）遷到 compose 時，把整庫 `pg_dump -Fc` 還原進 compose 的 postgres，再 `docker compose run --rm migrate`（版本一致就什麼都不做）。還沒被 alembic 接管的既有庫，migrate 會拒絕：先在原主機走 `make schema-check`（必須零 drift）＋`make schema-stamp-baseline`。研報原檔與上傳搬進 `data` volume 時，`research_report.file_path` 的路徑要對得上容器裡的位置。

## 疑難排解

| 症狀 | 原因與處置 |
|---|---|
| 容器啟動就退出，日誌有「拒絕啟動」 | 必填設定或 schema 閘門；照訊息印出的指令做 |
| `docker compose up` 報 `BACKUP_HOST_DIR`／`PROFILE_HOST_DIR` 不存在 | 主機目錄要先建（刻意不自動建立） |
| 每日備份失敗「不可寫」 | 備份目錄的擁有者不是 UID 10001 |
| `/readyz` 503、`/healthz` 200 | 看 web 日誌的「readyz 未就緒：…」：`embedding`（映像沒有權重，或 `RERANK_MODEL`／嵌入模型名與 build 時不同）、`profile`（profile 驗證失敗）、`llm`（金鑰空、模型名不在白名單、`LLM_PROVIDER` 拼錯） |
| `/metrics` 回 404 | 對端不在 `METRICS_ALLOWED_CIDRS`（預設只有 loopback），或請求經過反向代理（帶了 `X-Forwarded-For` 等 header）；見「監控對接」 |
| `scheduler` 拒絕啟動「不認得的工作名」 | `SCHEDULER_DISABLE`／`SCHEDULER_ENABLE` 拼錯；可用的名字在 `deploy/container/scheduler.py` |
