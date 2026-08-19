# 生產韌性：重啟策略、健康檢查、失敗告警、unit 還原

本文對應 2026-07-28 的 P3 工作。要解決的是**一個偵測不到的複合故障**，不是四件無關的雜事。

## 故障是怎麼串起來的

1. `report-mark-postgres` 容器的重啟策略是 `no`，而 `deploy-nginx-1`、`deploy-cloudflared-1`（`deploy/docker-compose.yml`）與 `report-mark-web.service`（`Restart=always`）**都會自己回來**。
2. 於是重開機後只有 DB 不回來。
3. 而登入路徑**完全不碰 DB**（`web/auth.py` 無任何 DB 相依）→ 站台開得起來、登入還會成功、每一個查詢 500。這是最糟的故障型態：**假活著**。
4. 在此之前沒有任何探測能分辨——`/healthz` 與 `/zzz-nonexistent` 都回 302（被 auth middleware 導向 `/login`）。`/api/stats` 在 auth 之後且經 TTL 快取，代替不了。
5. 也沒有告警：三個 systemd unit 都沒有 `OnFailure=`。`data/sync_delta_20260706_150148.txt` 這個孤兒檔就是證據——同步異常結束，**三週沒人知道**。

## 四項改動

### 1. DB 容器自動重啟

`Makefile` 的 `db` target 已加 `--restart unless-stopped`，並對既有容器補一行 `docker update`（`--restart` 只在 `run` 時生效，`docker update` 免重建容器）。

既有環境補套用：

```bash
docker update --restart unless-stopped report-mark-postgres
docker inspect report-mark-postgres --format '{{.HostConfig.RestartPolicy.Name}}'   # 應為 unless-stopped
```

### 2. 免認證健康檢查 `/healthz`

`web/routers/health.py`，並列入 `web/server.py` 的 `_AUTH_ALLOWLIST`。

- 健康：`200 {"status":"ok"}`
- DB 不可用：**`503 {"status":"degraded"}`**（回 200 帶欄位的話，多數 uptime 監控預設仍判定為健康）

設計上的取捨（這是唯一免認證且對外可達的資料端點）：**不洩漏任何資訊**（只有 `status` 一個欄位，錯誤細節只進日誌）、**探測結果快取 5 秒**（免認證端點會被掃描器高頻打）、**探測包 3 秒逾時**（DB hang 住時回 degraded 而非耗住連線）。

驗證：

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8097/healthz     # 200
docker stop report-mark-postgres
sleep 6                                                                     # 等快取過期
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8097/healthz     # 503
docker start report-mark-postgres
```

### 3. 失敗告警 `OnFailure=`

`report-mark-web.service` 與 `report-mark-sync.service` 都加了 `OnFailure=report-mark-alert@%n.service`，由 `deploy/systemd/report-mark-alert.sh` 處理。

**它不寄信**：本機沒有 MTA、也沒有既有通知管道，硬寫「寄信」只會產出靜默失敗的假告警。它做的是留下耐久且可查的痕跡：

- journal 一行 `ERROR`（`journalctl -p err -t report-mark-alert`）
- `data/unit_failures.log` 追加一筆，**含失敗當下的 journal 尾巴**——三週後才發現時你要的正是當時的上下文，而 journald 可能已輪替掉
- 設了 `REPORT_MARK_ALERT_WEBHOOK` 才送 webhook（opt-in，URL 不進 repo）

例行檢查：

```bash
tail -50 data/unit_failures.log
journalctl -p err -t report-mark-alert --since '-7 days' --no-pager
```

### 4. `SuccessExitStatus=143`（附帶修正）

uvicorn 收到 `SIGTERM` 後以 143（128+15）退出，這是**正常收場**。未宣告的話每次 `systemctl restart` 都會在日誌留下 `Failed with result 'exit-code'`——維運查問題時被假錯誤帶偏，真正的失敗也被雜訊淹沒。

## unit 還原（重點）

在此之前 **`deploy/systemd/` 裡沒有 web unit**：生產的 web 服務定義只存在於主機上，repo 還原不了。現已補齊：

| 檔案 | 用途 |
|---|---|
| `report-mark-web.service` | web 服務（與主機版逐字對齊，另加 `OnFailure` / `SuccessExitStatus`） |
| `report-mark-web.service.d/path.conf` | PATH drop-in。**`claude` CLI 在 nvm 的 node bin，不在 systemd 預設 PATH**；少了它 `/api/ask` 會以 `FileNotFoundError: 'claude'` 失敗 |
| `report-mark-sync.service` / `.timer` | NAS 增量同步 |
| `report-mark-alert@.service` / `report-mark-alert.sh` | 失敗告警 |
| `report-mark-sync.env.example` | → `/etc/default/report-mark-sync` |

### sync unit 的漂移（已知歷史問題）

repo 版 `report-mark-sync.service` 刻意把主機專屬值外移到 `/etc/default/report-mark-sync`（`REPORT_MARK_ROOT` 等），**這是正確的設計**。但該 env 檔從未被安裝，於是有人改去在主機 unit 裡寫死路徑 → repo 與主機分岔，照 repo 安裝反而會失敗（`${REPORT_MARK_ROOT:?}` 未設）。

正解是**補裝 env 檔**，而不是把 repo 改成寫死版：

```bash
sudo cp deploy/systemd/report-mark-sync.env.example /etc/default/report-mark-sync
sudo "$EDITOR" /etc/default/report-mark-sync        # 確認 REPORT_MARK_ROOT 等值
```

### sync unit 的第二次漂移：`HOME=%h`（2026-07-28，停擺 24 小時）

前一節的「補裝 env 檔」在 2026-07-28 13:15 執行後，反而引爆了另一個**一直躺在 repo 版裡**的缺陷：

- `Environment=HOME=%h`。**系統層 unit 的 `%h` 一律解析為 service manager 的家目錄（`/root`），不受 `User=` 影響**——這是 systemd 的既定語意。`uv` 隨即去建 `/root/.cache/uv`，EACCES，整支 `exit 2`。
- `SYNC_PATH_EXTRA` 指向 `.nvm/versions/node/current/bin`，而 **nvm 沒有 `current` 這個符號連結**（那是 n / nodenv 的慣例）。這段 PATH 整個落空：`uv` 仍找得到（在 `~/.local/bin`），`claude` 找不到。

後果比看起來嚴重：rsync 那一段照常成功、新檔已落到本地，**只有匯入沒發生**——症狀是「這幾天怎麼沒有新研報」，不是「服務壞了」。自 07-28 15:00 起連續 10 輪 100% 失敗，約 24 小時無人察覺（`OnFailure` 有寫進 `data/unit_failures.log`，但那個檔沒有任何程式消費端，webhook 也未設）。

而且**日誌完全看不出敗在哪一步**：`scripts/sync_new_reports.sh` 開頭是 `set -euo pipefail`，裸呼叫失敗會就地中止，緊接其後的 `RC=$?` / `log "匯入結束 rc=..."` 根本執行不到——日誌永遠停在「增量匯入 delta…」。現已改為 `|| RC=$?` 形式。

三處都已修正並由 `tests/test_deploy_units.py` 機械化守門（`%h` 禁用、三支 unit 的 `HOME` 必須一致、web drop-in 與 sync env 的 nvm 路徑必須逐字相同）。**升級 nvm node 版本時，`report-mark-web.service.d/path.conf` 與 `report-mark-sync.env.example` 兩處要一起改**，該測試會擋住漏改。

補跑破口期間漏掉的匯入（rsync 已落地、delta 檔留在 `data/`）：

```bash
uv run python scripts/sync_new_reports.py --delta data/sync_delta_20260729_120004.txt
```

### 安裝／更新

```bash
REPO=/home/kashionz/projects/report-mark
sudo cp "$REPO"/deploy/systemd/report-mark-*.service "$REPO"/deploy/systemd/report-mark-*.timer /etc/systemd/system/
sudo mkdir -p /etc/systemd/system/report-mark-web.service.d
sudo cp "$REPO"/deploy/systemd/report-mark-web.service.d/path.conf /etc/systemd/system/report-mark-web.service.d/
sudo systemctl daemon-reload
sudo systemctl restart report-mark-web.service
systemctl status report-mark-web.service --no-pager
```

驗證 `OnFailure` 真的接上（刻意觸發一次失敗）：

```bash
systemd-analyze verify /etc/systemd/system/report-mark-web.service
systemctl show report-mark-web.service -p OnFailure --value        # 應非空
```

## 連線預算：調大併發之前先算連線數

`app/services/db.py` 過去只設 `pool_pre_ping=True`，池上界與查詢逾時**兩者都沒有**。現在池的四個參數與兩個 server-side 逾時全部顯式設定，走 `app/config.py` 的 `DB_*` 旋鈕（值與理由見該檔註解與 `.env.example`）。

**先講清楚因果，因為架構檢視報告在這裡寫錯過一次**：真正的風險**不是**「SSE 長串流期間持有 session、3-5 個併發使用者就打滿池」。2026-07-29 以 AST 掃過全部 44 個 `async with SessionFactory()` 區塊，**沒有一個含 `yield`，也沒有一個含 LLM 串流呼叫**；`retrieval_pipeline.py` 更是刻意短連線（rerank 與 LLM 都發生在區塊之外）。真正的風險是**沒有 `statement_timeout` ⇒ 單一慢查詢可以無上限佔住一條連線**，配合雷達目錄與 overview 分面在現規模下的全表掃描才會把池吃乾。

### 算式

```
worker 數 × (DB_POOL_SIZE + DB_MAX_OVERFLOW) + 同時在跑的批次腳本數 × 2
    <  max_connections − superuser_reserved_connections
```

- 右邊：DB 跑 `pgvector/pgvector:pg16` 官方映像，`Makefile` 的 `docker run` 沒帶任何 `postgresql.conf` 覆寫 ⇒ `max_connections=100`、`superuser_reserved_connections=3` ⇒ 一般角色可用 **97**。
- 左邊現況：web 是**單 worker**（`report-mark-web.service` 的 `ExecStart` 沒有 `--workers`）⇒ `1 × (5+15) = 20`；同步鏈三支批次腳本各自單執行緒、同時只開一個 session ⇒ `3 × 2 = 6`。合計 **26**。
- 為何上界取 20 而不是沿用 SQLAlchemy 預設的 15：有併發閘的路徑只有 `/api/ask`(3) 與研報（1 個 run × `REPORT_FANOUT_CONCURRENCY`=3 ＋ 1 條記帳）＝ 7；`/api/search`、雷達、閱讀頁、監控頁**完全沒有併發閘**，剩下 13 條是留給它們的突發量。

**改任何一項併發都要重算這條式子**：加 `uvicorn --workers`、提高 `REPORT_SEMAPHORE`、放寬 `web/routers/ask.py` 裡寫死的 `_ASK_GATE`(3)、或新增一支長跑批次腳本。

> 這裡沒有壓測數據。上面的數字是逐條數出來的上界，不是實測——要留下「幾個併發使用者會打滿」這種數字之前，得真的壓一次（例如 15 個並行 `/api/search` 觀察 `pg_stat_activity`）。

### 逾時的兩個非對稱決定

- `DB_STATEMENT_TIMEOUT_MS` 預設 **60000**，刻意不取架構檢視建議的 15000：雷達目錄的兩次全表 unnest、overview 一題掃 7 次同一母體、閱讀頁 `similar` 拉高 `ef_search` 的 HNSW 掃描都天生偏慢，15s 有把正常功能打掛的實質風險。60s 比任何已知查詢高一個數量級，仍舊把「無上限」變成有上限。
- `DB_IDLE_TX_TIMEOUT_MS` 預設 **0（關）**，這是刻意的：`scripts/sync_new_reports.py` 先 `report_exists()` 開了交易，接著才 spawn `claude` CLI 標註（硬逾時 150s）與跑 BGE-M3 嵌入（大檔可達數分鐘），中間完全沒有 commit。設了它＝每 3 小時一次的生產同步會把報告靜默丟進 `data/sync_failures.log`。要開就**只在 web 的 `.env` 開**——批次腳本不讀 repo 根的 `.env`（sync unit 走 `/etc/default/report-mark-sync`），這個切分是天然的。

### 維運長查詢的豁免

匯入流程最後那句 `ANALYZE research.report_chunk` 要在 70 萬列 × `vector(1024)` 上抽樣，可能久於 60s，而它是整條匯入的**最後一步**——被 timeout 砍掉時前面的資料都已 commit，症狀只有 planner 統計靜默過期。三個呼叫點（`scripts/ingest_all.py`、`scripts/run_ingest.py`、`scripts/sync_new_reports.py`）因此都先呼叫 `db.relax_statement_timeout(session)`，值取自 `DB_MAINTENANCE_STATEMENT_TIMEOUT_MS`（預設 0＝不限）。

它用的是 `SET LOCAL` 而非 `SET`：連線是池化的，而 SQLAlchemy 預設的 `reset_on_return="rollback"` **不會**還原 GUC——`SET` 的豁免會跟著連線流到下一個借用者身上，等於把線上查詢的上界一起拆了。`SET LOCAL` 只活到本交易結束。

`tests/test_db_engine.py` 靜態守住「每個 `ANALYZE research.report_chunk` 前面都有 `relax_statement_timeout()`」——漏掉一處的症狀是統計靜默過期，沒有例外也沒有日誌，只有檢索計畫慢慢變差，行為測試照不到。
## 備份與還原

在此之前這個 DB **完全沒有備份**——`pg_dump` / `pgbackrest` / `pg_basebackup` 在 Makefile、`scripts/`、`deploy/`、`docs/`、systemd、crontab 全部零命中，唯一的副本是 docker named volume `report-mark-pgdata`。而 `docs/qa_pdf_report_deployment.md` 早在深度研報上線時就寫著「DB 的 `report_doc` 表需納入備份」，一直沒有人做。

### 為什麼只備七張表

| 表 | 為什麼備 |
|---|---|
| `research.qa_log` | 每一次提問、當時的來源與證據帳本、使用者的讚／倒讚。**沒有任何來源可以重建** |
| `research.report_doc` | 深度研報的 `markdown`（schema 註解明寫「真相來源」，PDF 由它重建） |
| `research.report_rendition` | 換皮重出的不可變渲染史 |
| `research.report_takeaway` | 閱讀頁重點摘錄（Sonnet 批次產物 + 確定性錨點） |
| `research.report_signal` | 觀點雷達訊號（Sonnet 批次產物） |
| `research.report_run` / `report_section` | 逐節生成的流程史 |

沒備的是語料層（`research_report`、`report_chunk`）。理由不是「不重要」，是**它確定重建得回來**：研報原檔在 NAS、`extract → tag → ingest` 全程 checkpoint 可續。代價是 CPU 時間（BGE-M3 約 3 篇／分，全語料數十小時），不是資料消失。而這七張表的體積相對很小，備起來幾乎沒有成本。

**這個取捨有一個已知代價，先寫在這裡免得還原那天才發現**：`report_takeaway` 與 `report_signal` 以 `report_id` FK 指向 `research_report`，而 `report_id` 是每次 ingest 重新產生的 uuid。**語料層若被整個重建，這兩張表的備份就對不回去了**（另五張沒有 FK，任何情況都還原得乾淨）。若之後判定摘錄／訊號值得那個代價，正解是把 `research_report` 一起納入備份（`report_chunk` 仍不必——向量重算得回來），而不是在還原時 `--disable-triggers` 硬塞孤兒列。

### 怎麼跑

```bash
make db-backup                       # 手動跑一次
systemctl list-timers report-mark-backup.timer   # 排程：每日 03:30（Persistent=true）
```

輸出落在 NAS：

```
/mnt/nas-backup/report-mark-db/daily/report-mark-critical-YYYYmmdd_HHMMSS.dump   # 保留 7 份
/mnt/nas-backup/report-mark-db/weekly/report-mark-critical-YYYY-Www.dump         # 保留 4 份
```

幾個刻意的設計：

- **掛載不可用就失敗，絕不退回本地路徑。** 備份的全部價值在「跟 pgdata 不同一塊磁碟」；靜默寫本地會產出一份看起來成功、實際上跟 pgdata 一起死的備份，還會餵飽下面那個 `ingest-lowio` 閘門——把安全網變成假象比沒有安全網更糟。
- **落點必須是另一個 share，不是「同一個 share 再掛一次 rw」。** 這一點 2026-07-30 用實測否證過一次，證據留在這裡免得有人再走一遍：

  | 掛載點 | `/proc/mounts` 旗標 | `touch` 的錯誤 | 誰在擋 |
  |---|---|---|---|
  | `/mnt/nas-research` | `ro` | `Read-only file system`（EROFS） | Linux 的 mount 旗標 |
  | `/mnt/nas-backup`（曾指向同一 share） | **`rw`** | `Permission denied`（EACCES） | **伺服器端 ACL** |

  兩個 errno 不同正是判定依據：第二個掛載確認是 `rw`，所以不是旗標問題——那組 NAS 帳號對 `投資研究處` 就只有讀取權，而 Linux 端的 mount 旗標給不了伺服器不給的權限。**加 rw 旗標救不了 ACL。** 所以 `deploy/systemd/mount-nas-backup` 掛的是另一個 share，UNC 與落點都由 `/etc/default/report-mark-sync` 提供（`NAS_BACKUP_UNC` / `REPORT_MARK_BACKUP_DIR`），搭配 `deploy/systemd/report-mark-backup.sudoers`。`tests/test_db_backup.py` 的 `MountHelperTests` 會擋住改回唯讀那個 share。

  > **目前的落點是臨時的**：`公用資料夾/01.會議暫存(會後刪除)/Jacky/`。那個資料夾依命名就是會後清掉的暫存區，而備份內容（`qa_log`、`report_doc.markdown`）不可重建。等 NAS 開好不會被清的位置，只要改 `/etc/default/report-mark-sync` 的 `REPORT_MARK_BACKUP_DIR` 一個值，腳本與 unit 都不必動。

- **掛載腳本讀環境檔用逐鍵 `sed`，不用 `source`。** 那個檔是給 systemd 的 `EnvironmentFile` 讀的，systemd **不做 shell 解析**，所以值合法地可能含 `(` `)`——實際落點就是一例。實測 `bash -c '. /etc/default/report-mark-sync'` 直接 `syntax error near unexpected token '('`。對一個「給 systemd 讀的檔」下 `source` 是安靜的地雷，更糟的情況是值被當指令求值。
- **驗過才改名成 `*.dump`。** 先寫 `.partial-*`，檢查檔頭魔數 `PGDMP` 與大小下限後才原子 `mv`。備份最惡劣的失敗型態是「檔案在、內容不能用」，而 `.dump` 這個副檔名同時是保留策略與新鮮度閘門的判準。
- **`docker exec` 一律不加 `-t`。** 配 TTY 會對 stdout 做行尾轉換，把二進位 dump 悄悄弄壞——檔案照樣產出、大小也合理，直到還原那天才發現。
- **週備用 `cp` 不用 hardlink。** drvfs 的 hardlink 支援不可靠，而「以為連結還在、其實日備輪替時一起砍掉了」是完全靜默的資料消失。

### 還原步驟（沒演練過的備份不算備份）

一律用 stdin 餵 `pg_restore`，不要 `docker cp`：備份檔在 `/mnt/nas-backup`，那是 WSL 的掛載點，`docker.exe` 看不到這個路徑；由 WSL 這側讀檔、管線送進容器才對得起來。

```bash
DUMP=/mnt/nas-backup/report-mark-db/daily/report-mark-critical-20260730_033000.dump

# 1) 先證明這份 dump 讀得回來（完全不動 DB）
#    **不可寫成 `pg_restore -l -`**：`-` 是 psql 的慣例，pg_restore 會把它當檔名而
#    以 `could not open input file "-"` 失敗。它在沒有檔名引數時就讀 stdin。
docker exec -i report-mark-postgres pg_restore -l < "$DUMP"

# 2) 還原到臨時 DB 驗過，再碰生產
docker exec -i report-mark-postgres psql -U postgres -c 'CREATE DATABASE restore_check;'
docker exec -i report-mark-postgres psql -U postgres -d restore_check \
  -c 'CREATE SCHEMA IF NOT EXISTS research;'
docker exec -i report-mark-postgres pg_restore -U postgres -d restore_check \
  --no-owner --no-privileges < "$DUMP"
docker exec -i report-mark-postgres psql -U postgres -d restore_check \
  -c 'select count(*) from research.qa_log;' \
  -c 'select count(*) from research.report_doc;'

# 預期輸出：**必定出現 2 個 FK 錯誤**，這是正常的，不是備份壞了——
#   ERROR: relation "research.research_report" does not exist
#     （report_takeaway / report_signal 的 report_id FK 指向未納入備份的語料層）
#   pg_restore: warning: errors ignored on restore: 2
# **而 pg_restore 的退出碼仍然是 0。** 所以「rc=0 就是還原乾淨」是錯的判準：
# 要看的是 `errors ignored on restore:` 那一行的數字（臨時 DB 演練＝恰好 2，
# 多於 2 就要查）。2026-07-30 的演練就是這樣量出來的。

# 3) 確認筆數合理後才動生產。單張表被誤刪／誤清時只還原那一張：
docker exec -i report-mark-postgres pg_restore -U postgres -d research \
  --no-owner --no-privileges -t qa_log < "$DUMP"

# 4) 整組還原到空 DB（例如 pgdata 全滅、重建叢集之後）：
make schema                                    # 先把 schema 建回來（含 vector 擴充與索引）
docker exec -i report-mark-postgres pg_restore -U postgres -d research \
  --no-owner --no-privileges --data-only --disable-triggers < "$DUMP"

# 5) 收尾：清掉步驟 2 的臨時 DB（**不可在 -d restore_check 連線上下這道指令**，
#    PostgreSQL 不允許 DROP 自己正連著的資料庫）
docker exec -i report-mark-postgres psql -U postgres -c 'DROP DATABASE restore_check;'
```

第 4 步用 `--data-only` 是因為 `make schema` 已經把表建好了（含 CHECK 與索引）；`--disable-triggers` 讓 `report_takeaway` / `report_signal` 的 FK 檢查在載入時先讓開——**但那只在 `research_report` 也還原到相同 `report_id` 時才有意義**，見上面的已知代價。

`pg_restore` 的退出碼要看：非 0 就是沒還原完，**不要因為「有些表看起來有資料」就當成功**。
但反過來**不成立**——見步驟 2 的註解：臨時 DB 還原必定有 2 個 FK 錯誤而 rc 仍是 0。

### 演練紀錄

| 日期 | 做了什麼 | 結果 |
|---|---|---|
| 2026-07-30 | 手動 `make db-backup` → 依上述步驟 1-2 還原到 `restore_check` → 七張表逐表比列數 → 三張大表比內容 md5（含 `report_doc.markdown`）→ 清除臨時 DB | **通過**。七張表列數與內容雜湊全數相符（qa_log 79／report_doc 14／report_rendition 1／report_run 5／report_section 40／report_signal 398／report_takeaway 3094）。生產庫未受影響 |

那次演練抓出三個缺陷。文件這兩條（`pg_restore -l -` 不成立、「rc=0 即乾淨」是錯判準）
在此修正；第三條是**排程其實跑不起來**——`docker` 偵測只看 binary 存在與否，而本機
`/usr/bin/docker` 是 WSL integration 留下的死連結（`docker info` rc=1），備份在第一步
就中止，而 unit 已 enabled/active，從外面看起來卻像備份有在跑。那條已由 PR #151
（`scripts/_docker_bin.sh` 的 `detect_docker_bin`）修掉。

**演練值得定期重做**：三個缺陷沒有一個是讀程式碼看得出來的——備份檔存在、unit 是
active、`pg_restore` 退出碼是 0，每一個訊號都指向「沒問題」。

### `make ingest-lowio` 現在有硬閘

`scripts/ingest_lowio.sh` 會關掉 `fsync` / `full_page_writes` / `synchronous_commit`，崩潰即可能整個 pgdata 報廢。它檔頭原本的安全論證是「本 DB 為衍生、可由原始研報重建」——**在上面那七張表存在之後，這句話已經不成立**。現在它開頭會檢查備份目錄有沒有 24 小時內的 `*.dump`，沒有就 `exit 1`，且在碰 docker 之前就擋下。

確定這座 DB 裡沒有不可重建資料（例如正在從零重建語料）時，用 `ALLOW_STALE_BACKUP=1 make ingest-lowio` 明示略過。

### 安裝

```bash
REPO=/home/kashionz/projects/report-mark
sudo install -m 0755 -o root -g root "$REPO"/deploy/systemd/mount-nas-backup /usr/local/sbin/
sudo install -m 0440 -o root -g root "$REPO"/deploy/systemd/report-mark-backup.sudoers \
  /etc/sudoers.d/report-mark-backup
sudo cp "$REPO"/deploy/systemd/report-mark-backup.service \
        "$REPO"/deploy/systemd/report-mark-backup.timer /etc/systemd/system/
# 環境檔要一起更新——NAS_BACKUP_UNC 與 REPORT_MARK_BACKUP_DIR 都在裡面，
# 掛載腳本與備份 unit 都讀它。少了這步，掛載腳本會退回內建預設。
sudo cp "$REPO"/deploy/systemd/report-mark-sync.env.example /etc/default/report-mark-sync
sudo systemctl daemon-reload
sudo systemctl enable --now report-mark-backup.timer

# 換過落點時，舊掛載要先卸掉——mountpoint -q 會通過，然後在寫入探測才失敗
sudo umount /mnt/nas-backup 2>/dev/null || true

# 首跑與驗收
make db-backup                       # 或 sudo systemctl start report-mark-backup.service
ls -lh "$(sed -n 's/^REPORT_MARK_BACKUP_DIR=//p' /etc/default/report-mark-sync)/daily"
```

`/etc/default/report-mark-sync` 是共用的環境檔（備份 unit 與掛載腳本都讀它），備份專屬旋鈕的範例在 `deploy/systemd/report-mark-sync.env.example`。

**已驗（2026-07-30）**：`投資研究處` 的寫入權——**沒有**，見上面的 errno 對照表；掛載腳本、sudoers、`rw` 掛載本身都正常。落點已改為 `公用資料夾`。

**仍待驗**：新 share 的寫入權（首跑就知道）、以及 `docker.exe` 透過 WSL interop 把二進位 dump 送回 WSL 檔案是否位元完整——檔頭魔數 + 大小檢查只擋得住頭尾壞掉，**首次安裝時應該真的做一次上面那節的還原比對**。備份腳本的落點檢查、原子改名、檔頭驗證與輪替已用假 `docker` 二進位在沙箱驗過。

## 批次停更偵測（2026-07-30）

### 為什麼 `OnFailure` 不夠

上面第 3 項的告警鏈只在 unit **進入 `failed`** 時觸發。而 `scripts/sync_new_reports.sh` 的摘要／標題／摘錄三段是**刻意的 best-effort**——`|| RC=$?` 之後只 `log` 一行、不 `exit`。那個設計本身是對的：摘要失敗不該擋住下一輪匯入。代價是這三段連續失敗永遠不會讓 unit 變紅，於是 **`OnFailure` 一次都不會觸發**。

實測後果：2026-07 量到 `report_takeaway` 停更 8 天、`report_signal` 停更 12 天，而覆蓋率量測是事故**之後**才補的。停更的症狀是閱讀頁優雅降級、整區不進 DOM——「最新研報靜默少一個功能」，不會有人回報。

所以停更必須靠**另一個獨立的偵測器**，不能靠 `OnFailure`；而它量的是**結果**不是過程：不管是鎖撞了、`claude` 不在 PATH、timer 沒跑還是 NAS 沒掛上，只要派生資產不再前進就會紅。

### 怎麼跑

```bash
make freshness                                        # 手動跑一次
systemctl list-timers report-mark-freshness.timer     # 排程：每日 08:30（Persistent=true）
uv run python scripts/check_batch_freshness.py --json # 供後續接監控
```

退出碼：`0`＝新鮮／`1`＝停更／`2`＝查不到（DB 不可用）。**1 與 2 刻意分流**：前者去看批次日誌，後者去看 DB 與 `/healthz`，混成同一個碼等於把「DB 掛了」誤導成「批次壞了」。

`report-mark-freshness.service` 宣告 `OnFailure=report-mark-alert@%n.service`，**直接復用既有告警鏈、零新管道**（`report-mark-alert.sh` 恆容錯、恆 `exit 0`，被多一個 unit 呼叫是安全的）。

### 兩個刻意的預設

- **語料閘（`corpus`）抑制連鎖假警報。** 三支批次都只吃「本輪新入庫」的研報，沒有新研報時它們一行都不會產出——那是正確行為。所以語料自己在同窗期內沒前進時，派生資產的過期一律判 `suppressed`。少了這一層，一個連假就讓三個資產同時亮紅，而「天天假警報」的下一步就是沒人看告警。語料閘的母體刻意與批次一致（`full_text IS NOT NULL AND is_research IS NOT FALSE`）——用全表 `max()` 會被行政／活動檔拉新，抑制就失效了。
- **`signal` 預設門檻 0（不告警）。** `research.report_signal` **沒有任何排程產生者**：sync 殼只跑摘要、標題、摘錄，訊號擷取只有手動 `make signals`。2026-07-30 實測 `signal` 的最新產出是 07-16、已 **13.95 天**——若照直覺設 14 天，這支上線當天（一小時內）就會開始每天亮紅，而根因不是壞掉、是沒人跑。永遠紅的告警兩週內就會被當背景噪音。要開＝`--signal-days 14`。

`corpus` 本身預設也是 0：語料停更已經由 sync unit 的 `OnFailure` 覆蓋（匯入段失敗會 `exit 1` → unit 變紅），而 NAS 供稿有連假空窗，硬設門檻會變成日曆的假警報。

### 監控頁：`unit_failures.log` 終於有讀取端

2026-07-28 那次 24 小時停擺，`OnFailure` **確實**把 10 筆告警寫進了 `data/unit_failures.log`。機制當天就抓到真故障——但那個檔**零程式消費端**、webhook 也沒設，所以整整一天沒有人知道。

`/api/progress` 現在多回兩塊，`/app/monitor` 的「排程健康」卡呈現：

| 欄位 | 內容 |
|---|---|
| `sync` | `data/sync_run_<date>.log` 的最後一行 ＋ 完成判定。**先前 runtime 區塊只認 `tag_run_*`／`ingest_run_*`**，而那兩支全量腳本只在初次建庫時跑 ⇒ 生產實際的入庫路徑零可見度（現況是 runtime 區塊全 null ＋ 三個布林） |
| `unit_failures` | `count_24h` / `count_7d` / `latest` / 最近 5 筆（unit、stage、rc） |

兩個判定上的坑，都寫成測試釘住了：

- **完成判定看「最後一個標記行是 start 還是 done」**，不是「整檔有沒有 done」。log 是每日一檔、一天被 8 輪同步接續 append，看整檔等於第一輪跑完之後永遠顯示「已完成」。用「最後一個標記」而非「最後一個 start 之後有無 done」是為了對 tail 截斷免疫。
- **紅點條件是時間窗計數，不是累計未讀數。** 這個檔 append-only、沒有 logrotate、也沒有已讀游標；累計數當條件＝上線第一天就永遠亮著。

### 監控頁負載（順手降三分之二）

改動前是「每 5 秒 9 條 DB 查詢 ＋ 一次 `data/tags/` 的 scandir」。量級要講對：這稱不上「持續背景負載」（約 0.8% 一顆核心），而且 TanStack Query 預設在視窗失焦時會停 interval，所以成立條件是**監控頁開著且在前景**。

真正的熱點不是架構檢視歸咎的 `_proc_alive`。本機實測：

| 項目 | 每次成本 |
|---|---|
| `_count_tag_files`（`data/tags/` scandir，15,852 檔） | 冷 412 ms／熱 117 ms |
| 三次 `_proc_alive`（掃 133 個 `/proc/*/cmdline`） | 合計 2.4 ms |

差 50 倍，所以 `_proc_alive` **刻意不做任何優化**。三層 TTL 快取各有理由：`_DB_STATS_CACHE` 5→15 秒（DB 負載降為三分之一）、`_RUNTIME_CACHE` 10 秒（原本每次輪詢都重掃，把 DB 那層拉長只解一半）、`_TAG_COUNT_CACHE` 60 秒（粒度退化只影響「全量標註進行中」，而那條管線只在初次建庫時跑）。

### 安裝

```bash
REPO=/home/kashionz/projects/report-mark
sudo cp "$REPO"/deploy/systemd/report-mark-freshness.service \
        "$REPO"/deploy/systemd/report-mark-freshness.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now report-mark-freshness.timer

# 首跑與驗收（rc=1 是合法結果，代表真的有東西停更）
sudo systemctl start report-mark-freshness.service
journalctl -u report-mark-freshness.service -n 30 --no-pager
systemctl show report-mark-freshness.service -p OnFailure --value   # 應非空
tail -20 data/unit_failures.log                                     # 停更時應多一筆
```

它沿用 `/etc/default/report-mark-sync`（`REPORT_MARK_ROOT` 與 `SYNC_PATH_EXTRA` 都在裡面）。`SYNC_PATH_EXTRA` 在這支只是為了找到 **`uv`**（在 `~/.local/bin`）——它零 LLM，所以 nvm 那段 PATH 漂掉不影響它。

**待驗（本輪未在主機上執行）**：timer 是否真的每日觸發、`OnFailure` 是否真的把停更寫進 `data/unit_failures.log`。腳本本身的三條退出路徑（新鮮／停更／DB 不可用）已對生產 DB 以唯讀查詢實測過。

## 資料完整性稽核（2026-07-30）

### 與停更偵測的分工

上一節那支量的是「批次有沒有在**前進**」，這支量的是「已經產出的資料有沒有**互相矛盾**」。兩個不同的問題，同一種失效型態——**沒有人會回報**。

理由是本 repo 的完整性保證幾乎全在「寫入端很小心」，而不在 DB 的約束裡：三張研報衍生表刻意無 FK、`embedding` 可 NULL、`report_signal.market` 與 `research_report.market` 是兩份各自寫入的副本。這些設計都有理由，代價是壞掉的方式全部是靜默的——孤兒列沒有任何讀取路徑會碰到、重複 `chunk_index` 只讓閱讀頁跳到錯的位置、`market` 不一致仍會算出看起來合理的共識數字。

### 十一條檢查

`scripts/db_audit.py` 的 `CHECKS` 有 **10 條 SQL 斷言**（各回一個違反列數，0＝通過），外加 1 條走 Python 判準的取樣比對：

| 級別 | 檢查 | 為什麼要 |
|---|---|---|
| error | `durability_off` | **整組裡唯一「不修會失去全部資料」的一條**，見下 |
| error | `null_embedding` | 那些 chunk 對語意檢索完全不存在（檢索端跳過並記數是降級，不是修復） |
| error | `duplicate_chunk_index` | 閱讀頁錨定跳錯位置，看起來只像「引文對不上」 |
| error | `signal_market_mismatch` | 雷達把訊號歸到錯的市場，數字仍然合理 |
| error | `is_research_null` | 未判定的研報會被 ingest 閘門與各批次靜默略過 |
| warn | `chunkless_report` | 有全文卻沒有任何 chunk＝檢索不到 |
| warn | `orphan_report_doc` / `orphan_report_run` / `orphan_report_rendition` | 對話串或母表已刪的殘留 |
| warn | `takeaway_sha_disagreement` | 同一報告的摘錄存了不同的 `text_sha256` |
| （取樣） | `norm_drift` | `content_norm` 是 GENERATED，驗「庫裡實際存的值」與 `norm_for_match()` 是否等價 |

`norm_drift` 與 `tests/test_content_norm_equivalence.py` 的分工要分清楚：**測試驗「表達式定義與 Python 等價」，稽核驗「庫裡實際存的值等價」**——定義正確但既有列是舊定義算出來的，只有後者看得見。它取樣（`--norm-sample`，預設 500 列）而不全掃，因為這種漂移是全域性的。

**`durability_off` 是這支存在的最大理由。** `scripts/ingest_lowio.sh` 會 `ALTER SYSTEM SET fsync=off` 降 I/O，並以 `trap ... EXIT` 還原——**但 trap 擋不住 SIGKILL**（OOM killer、`kill -9`、WSL 整個被收掉），而 `ALTER SYSTEM` 寫的是 pgdata 裡的 `postgresql.auto.conf`，**重啟也不會恢復**。DB 於是無限期跑在 `fsync=off`：查詢完全正常、零症狀，但一次斷電就可能讓整個 pgdata 報廢。處置是 `make restore-durability`。

### 怎麼跑

```bash
make db-audit                                          # 手動跑一次
systemctl list-timers report-mark-audit.timer          # 排程：每日 08:45（Persistent=true）
uv run python scripts/db_audit.py --json               # 供後續接監控
uv run python scripts/db_audit.py --skip norm_drift    # 跳過取樣那條（最慢）
```

退出碼：`0`＝乾淨／`1`＝有發現／`2`＝DB 不可用。**1 與 2 刻意分流**，理由與上一節相同：混成同一個碼等於把「DB 掛了」誤導成「資料壞了」。

### 三個刻意的設計

- **只讀，一列都不改。** 稽核器自己去修等於在無人監督下改生產資料，而修法幾乎都需要人決定（孤兒該刪還是補回連結？重複 chunk 刪哪一列？）。輸出給人看，處置由人下。
- **error / warn 都算失敗（同樣 rc=1）。** 分級只影響閱讀順序。「warn 不算失敗」會在三個月內讓 warn 區永遠有東西、從此無人閱讀。
- **走 `db.relax_statement_timeout()`（`SET LOCAL`）。** 其中幾條是 57 萬列全表掃描，引擎層的 60s `statement_timeout` 會把它們砍掉——**被砍掉的稽核等於沒有稽核**。用 `SET LOCAL` 而非 `SET`，豁免不會跟著池化連線漏給下一個借用者。

### 安裝

```bash
REPO=/home/kashionz/projects/report-mark
sudo cp "$REPO"/deploy/systemd/report-mark-audit.service \
        "$REPO"/deploy/systemd/report-mark-audit.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now report-mark-audit.timer

# 首跑與驗收（rc=1 是合法結果，代表真的稽核出東西）
sudo systemctl start report-mark-audit.service
journalctl -u report-mark-audit.service -n 40 --no-pager
systemctl show report-mark-audit.service -p OnFailure --value   # 應非空
systemctl list-timers report-mark-audit.timer                   # 應排在每日 08:45
```

它與 freshness 一樣沿用 `/etc/default/report-mark-sync`（`REPORT_MARK_ROOT` 與 `SYNC_PATH_EXTRA` 都在裡面），`SYNC_PATH_EXTRA` 在這支同樣只是為了找到 `uv`。

**08:45 是刻意排在 freshness（08:30）之後、錯開 15 分鐘**：兩支都會對 `report_chunk` 跑全表掃描，同時跑只是互相搶 I/O。同樣排在上班時段的開頭——這支的產物是「一筆 unit 失敗紀錄」，要有人當天看到它才有用。

**`Persistent=true` 對這支特別重要**：最可能留下 `fsync=off` 的情境（機器被硬收掉）恰好就是它會被錯過的那一天。

**待驗（2026-07-31 確認：這組 unit 至今未安裝）**——`/etc/systemd/system/` 裡只有 web／sync／backup／freshness／alert 五組，**每日稽核一次都沒跑過**，包含耐久性那條。腳本本身可用 `make db-audit` 手動驗證。

## 這一輪刻意沒做

- **外部 uptime 監控**：`/healthz` 是給它用的介面，但要接哪一家（UptimeRobot／自架）是部署決策，不該由一次程式碼改動偷渡。
- ~~**把 unit 失敗顯示在監控頁**~~ → **已做**（2026-07-30），見下節「批次停更偵測」。
- **告警投遞管道**：webhook 已留 opt-in 掛勾，設不設由部署端決定。
- **語料層（`research_report` / `report_chunk`）納入備份**：見上面的取捨與已知代價，那是一個容量決定（`full_text` 是全語料原文），不該夾帶在第一版備份裡。
- **異地／離線副本與加密**：NAS 已經比 pgdata 好一個數量級，但 NAS 本身壞掉仍是單點。
- **自動還原演練**：真正的驗收是定期把 dump 還原到臨時 DB 比對筆數。這需要排程與判準，先把還原步驟寫成可照抄的指令。

---

## 應用層健康探針（`report-mark-health.timer`）

**`systemctl is-active` 不是應用健康的證據。** 2026-08-18 的中斷持續 4 小時 50 分，
期間 `systemctl is-active report-mark-web.service` 全程顯示 `active`，而 journal 的
access log 在恢復前是 **0 筆**。原因是 uvicorn 先跑 lifespan 再 bind，bind 失敗後
行程仍會存活約 10 秒（背景載入 BGE-M3），配合 `Restart=always` 與 `RestartSec=3`，
任何時間點去查 `is-active` 都有很高機率看到 `active`。事故全記錄見
`docs/incidents/`（待整合）。

因此另有一支每 2 分鐘執行的探針：`scripts/check_web_health.sh`，
由 `report-mark-health.timer` 觸發。

### 它檢查什麼（以及**不**檢查什麼）

它打 `http://127.0.0.1:8097/healthz`，也就是 `web/routers/health.py` 的端點。

**`/healthz` 目前只探 DB**（一次 `SELECT 1`，內部逾時 3 秒，結果快取 5 秒）。
它**不**代表「所有相依都健康」——不驗 BGE-M3 是否載入、不驗 reranker、不驗 NAS、
不驗 `claude` CLI。它能證明的是兩件事，而那兩件正好涵蓋 2026-08-18 的失效型態：

1. **uvicorn 真的綁上了 :8097 並且會回應**（探針連得上）
2. **DB 可用**（回 200 而非 503）

用 `127.0.0.1` 而非 `localhost`：uvicorn 綁的是 `0.0.0.0`（**只有 IPv4**），而
`localhost` 在多數 glibc 設定下會先解析到 `::1`——那會讓探針自己製造假故障。

### 判定與退出碼

| 退出碼 | 意義 | unit 狀態 |
|---|---|---|
| `0` | 健康 | success |
| `1` | HTTP 探測失敗（非 200／連不上／逾時） | **failed → `OnFailure`** |
| `2` | web unit 不在 `active` | **failed → `OnFailure`** |
| `3` | 剛啟動的寬限期內 | success（unit 宣告 `SuccessExitStatus=3`） |
| `4` | 探針自己不能執行（缺 `curl`） | **failed → `OnFailure`** |

**兩種「連不上」都算失敗**：2026-08-18 的失效型態是 uvicorn 根本沒綁上（連不上），
不是回 503。實測本機在 WSL mirrored networking 下，連一個沒有 listener 的埠得到的是
**逾時**（`curl rc=28`）而非拒絕（`rc=7`），因為 Windows 側是丟棄而非拒絕。

### 參數與其依據

| 參數 | 值 | 依據 |
|---|---|---|
| 觸發間隔 | 2 分鐘 | 對比中斷 4h50m，最壞偵測延遲降到約 2 分 45 秒 |
| 單次逾時 | 5 秒 | `/healthz` 內部探測上限 3 秒，留 2 秒餘裕 |
| 重試 | 3 次，間隔 15 秒 | 跨 30 秒，足以吸收 `systemctl restart` 的空窗（`RestartSec=3` ＋ 實測 bind 僅需 1 秒） |
| 啟動寬限 | 60 秒 | **實測 bind 只要 1 秒**（模型暖機是背景進行，不阻塞 socket），不需要更長 |

**啟動寬限有三個條件，缺一不可**：unit 目前是 `active`、進入 active 未滿 60 秒、
**且 `NRestarts == 0`**。第三個條件是關鍵——`Restart=always` ＋ `RestartSec=3` 會讓
`ActiveEnterTimestamp` 每 3 秒更新一次，只看時間戳的寬限在 crash loop 下**恆為真**，
會永久抑制告警（2026-08-18 累積 550 次重啟，正是這個形狀）。

### 探針刻意不相依 Python

`scripts/check_web_health.sh` 只用 `curl`／`systemctl`／coreutils，**不用 `uv run`、
不碰 `.venv`、不 import 任何 `app.*`**。2026-08-18 的根因正是 `/mnt/c`（9p）上的
venv 損毀，若探針相依 Python 環境，它會與被監控的服務一起死——那時最需要它，
而它不在。`tests/test_web_health_probe.py` 靜態守這條。

### 通知行為：**這支探針不會通知任何人**

本 unit **刻意不宣告 `OnFailure=`**，與同目錄其他五支不同。

理由不是它不重要，而是**通知節奏對不上**：那五支是日排程或每 3 小時，`OnFailure`
觸發一次＝一次批次失敗；本探針每 2 分鐘一次，同一次中斷會觸發約 30 次／小時。
而 `report-mark-alert.sh` 的 webhook 只由**單一全域變數** `REPORT_MARK_ALERT_WEBHOOK`
控制，`report-mark-alert@.service` 又是從 `/etc/default/report-mark-sync` 讀它——
**只要有人為了其他 unit 設定那個變數，本探針就會在無人察覺的情況下變成每 2 分鐘
一則通知**。那會讓它從「健康偵測」暗中變成「健康偵測 ＋ 不受控通知」。

通知的去重、提醒節奏與恢復判定都需要跨執行的狀態，那是 P5 的職責。
**這裡把邊界交給架構而不是設定紀律**：不接告警鏈，這支探針就結構上不可能通知，
不必依賴任何人記得「別設那個變數」。

失敗仍然完全可觀測：

| 訊號 | 取得方式 |
|---|---|
| unit 停在 failed | `systemctl is-failed report-mark-health.service` |
| 退出碼與時間 | `systemctl show report-mark-health.service -p Result -p ExecMainStatus -p ExecMainExitTimestamp` |
| 本次結果 | `journalctl -u report-mark-health.service -n 1` 的 key=value 單行 ＋ stderr 歸因訊息 |

P5 上線時再由它自己掛上帶去重的處理器（例如 `OnFailure=report-mark-incident@%n`），
屆時通知行為由 P5 完整擁有，而不是散在兩處。

**不以降低探測頻率來掩蓋告警噪音**——頻率正是這支探針的全部價值。

### 安裝

```bash
REPO=/home/kashionz/projects/report-mark
sudo cp "$REPO"/deploy/systemd/report-mark-health.service \
        "$REPO"/deploy/systemd/report-mark-health.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now report-mark-health.timer
```

### 觀察

```bash
# 每次執行的結果（一行一筆 key=value）
journalctl -u report-mark-health.service --since "24 hours ago" --no-pager | grep 'probe=local_http'

# 統計 ok / fail / grace
journalctl -u report-mark-health.service --since "24 hours ago" --no-pager -o cat \
  | grep -oE 'status=[a-z]+' | sort | uniq -c

# 誤報有沒有污染 unit_failures.log（無真實故障時應為 0）
grep -c 'UNIT=report-mark-health' data/unit_failures.log
```

### 停用

```bash
sudo systemctl disable --now report-mark-health.timer
```

零 application 變更、零資料變更，停用即完全回到沒有探針的狀態。

