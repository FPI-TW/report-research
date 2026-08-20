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

### `in_flight` 是觀測的生命週期，不是健康結論

2026-08-20 有一次 **4 分 54 秒**的生產中斷（00:48:03–00:52:57，P2 換手方法錯誤所致）。
**P4 正確偵測到，P5 全程零告警。** 那次意外驗收出這一節要修的缺口。

```
P4  00:49:03 Starting ──────────────► 00:49:49 fail（exit 2）
                    P5 00:49:44 ▲ 落在執行窗內 → in_flight → web skip
P4  00:51:13 Starting ──────────────► 00:51:59 fail（exit 2）
                    P5 00:51:58 ▲ 落在執行窗內 → in_flight → web skip
```

根因是**探針失敗時執行窗暴增**。健康時整個 oneshot 約 30ms；失敗時走完
`HEALTH_RETRIES=3` × `HEALTH_TIMEOUT=5s` ＋ 2 × `HEALTH_RETRY_WAIT=15s` ＝ **45 秒**
（實測 46s），佔 P5 週期（~130s）的 **35%**。連續兩次撞上一點都不意外——
**timer 的 `RandomizedDelaySec` 只降低碰撞機率，不是正確性機制。**

#### 兩個實測否定的假設

| 假設 | 實測結果 |
|---|---|
| in-flight 時可從 `Result` 讀出上一輪結論 | **否**。systemd 在新一輪啟動時把 `Result` 重設為 `success`、`ExecMainStatus` 重設為 `0`，**即使上一輪是 `exit-code`**。照這個假設寫，會在中斷期間讀到「健康」 |
| 失敗的 unit 不被 GC，所以結論一直讀得到 | 只在**沒有新一輪啟動時**成立。新一輪一開始，`ExecMain*` 就歸零 |

所以「上一筆已完成的結論」只能由 P5 自己記住。

#### 觀測快取

`data/.incidents/probe_observation.state`（`boot_id`／`monotonic`／`status`／`result`）。
**刻意是獨立檔案，不放進 `web.state`**：後者在 RESOLVED 時會被 `rm -f`，等於**服務恢復
的那一刻把觀測快取一起抹掉**；而且 `monitor` 元件也要用這筆觀測判新鮮度，兩個消費者
共用一個生命週期遲早互相污染。

#### 語意

| 情況 | 判定 |
|---|---|
| 執行中 ＋ 快取 OK 且在信任窗內 | 沿用「上次健康」，不開事件；輸出仍標明 `current_probe=in_flight` |
| 執行中 ＋ 快取 **FAIL** | **開／維持 WEB_HEALTH FIRING**——絕不 `skip` 讓中斷消失 |
| 執行中 ＋ 快取超過**信任上限** | `monitor_blind` `observation_missed`——**漏讀，不是過期** |
| 執行中 ＋ 快取超過 `BLIND_CRITICAL` | `monitor_blind` `observation_stale`（CRITICAL） |
| 執行中超過 `PROBE_MAX_INFLIGHT` | `monitor_blind` `probe_stuck`——探針卡住，不是服務故障 |
| 執行中 ＋ 無任何快取 | 依 bootstrap 窗判 `bootstrap` 或 `monitor_blind` |
| 快取的 `boot_id` 與本次開機不同 | **一律丟棄**，不跨開機沿用 |

`in_flight` 期間**不解除**進行中的 monitor 事件——「正在跑」不等於「有一筆新的完成觀測」。

#### 信任上限是「有沒有漏讀」，不是「過期」

`INCIDENT_OBS_TRUST_SECONDS`（預設 **240s**）由 P4 節奏推導：P4 每 ~130s 完成一輪
（`OnUnitActiveSec=2min` ＋ `AccuracySec=10s`，實測 125–138s），若 P5 每輪都讀得到，
快取最多只會有「一個週期上界 140s ＋ 當前執行中 90s」＝ 230s 這麼舊。**超過就代表
P4 至少完成過一輪而 P5 沒讀到**，那筆結果是什麼並不知道。

這正是真實中斷的形狀：00:47:26 讀到 OK；P4 在 00:49:49 完成一筆 fail 而 P5 沒讀到；
00:51:58 再取樣時快取已 **272s**。沒有這個上限就會沿用那筆 OK 而繼續靜默。
**138s 時沿用 OK 是正確的**（那當下確實是最新的已完成觀測）；272s 時就不是了。

`INCIDENT_PROBE_MAX_INFLIGHT`（預設 **120s**）同樣由契約推導：契約最壞 45s、
unit 硬上限 `TimeoutStartSec=90s`，超過 90s 代表 systemd 應該已經砍掉它卻沒有。

#### 觀測快照必須是一次查詢（2026-08-20）

**每個屬性各發一次 `systemctl show` 會拼出不可能存在的組合。**

2026-08-20 出現三次假 `monitor_blind FIRING`（04:49／07:09／13:20，全為 monitor 元件、`notified=no`、約 2 分鐘內自行 RESOLVED）。同日 P4 執行 413 次、**最大間隔 142 秒、零次超過 240 秒**——探針從未真的斷過。

`last_completed_age` 的序列說明一切：

```
69 → 81 → 89 → 99 → 109 → 249 → 0 → 10
```

正常每輪只增約 10 秒（P4 與 P5 的相位漂移），卻在**單一取樣**跳 +140（約一個完整 P4 週期），下一輪立即回 0。而該輪 `current_probe=idle`——不是 #216 修的 in_flight 路徑。

成因：handler 原本對六個 service 屬性各發一次查詢。`ExecMainExitTimestampMonotonic` 與 `ActiveState` 之間相隔數毫秒，若剛好跨越 P4 的一次 invocation 邊界，就會得到 `ActiveState=inactive` ＋ `ExecMainExitTimestampMonotonic=0`——**該 unit 有 timer 引用（不會被 GC），idle 時必定有完成時戳，這個組合在單一一致快照中不可能出現**。P5 於是讀成「沒有完成觀測」→ 退回快取（已落後一個週期）→ age 越過 `OBS_TRUST_SECONDS` → FIRING。

隔離實驗（user-scope oneshot ＋ timer，804 次取樣）：

| 讀法 | 矛盾率 |
|---|---|
| 一次 `show -p A -p B` | **0 / 804 = 0.00%** |
| 兩次獨立 `show` | **3 / 804 = 0.37%** |

生產實測 3 次 ÷ 約 660 個 P5 週期 ＝ 0.45%，同一量級。**六輪取樣中 `activating` 一次都沒被直接取樣到**，但矛盾組合仍然出現——那正是「第一次讀落在 activating、第二次讀落在完成後」的簽名。

**明確排除**：`Clock change detected` 不是原因。當日該訊息約 2,724 次（每分鐘數次），任何時間窗都會命中，沒有鑑別力。

##### 現在的契約

- **service 的六個屬性一次查完**（`systemctl show` 從 8 次降到 2 次），逐行切第一個 `=`、只認白名單鍵。**不用 `source`／`eval`**——值來自 systemd，`Result` 之類的欄位理論上可含任意字元。
- **一致性驗證只判「不可能同時成立」的組合**，不判健康：`inactive`／`failed` 時若 `start>0` 而 `exit=0`，或 `exit<start`，即為矛盾。**從未跑過（`start=0` 且 `exit=0`）是自洽的**，交給既有的 bootstrap 分類。
- **有界重讀**（`INCIDENT_SNAPSHOT_RETRIES`，預設 1 次、間隔 0.3s）。窗口只有毫秒級，一次重讀就足以跨過去。
- **重讀後仍矛盾 → 走既有的信任窗分支**（與 in_flight 共用同一段已測過的邏輯，**刻意不另開平行路徑**，複製只會讓兩邊漂移）：信任窗內沿用上一筆完成觀測、不開事件；超過 `OBS_TRUST_SECONDS` 仍報漏讀；超過 `BLIND_CRITICAL_SECONDS` 仍升級。
- **真 stale 完全不受影響**：快照自洽但確實沒有新完成觀測時，照樣 `monitor_blind`，且 `snapshot=stable` 讓人一眼看出這不是快照問題。
- **刻意不調大 `OBS_TRUST_SECONDS`**。那會讓真正的漏讀晚 1–2 分鐘才被發現，而且沒有解決任何事——問題從來不是門檻太緊，是讀數本身自相矛盾。

#### 結構化輸出

每一行多帶三個欄位，讓 operator 一眼看出「現在在跑」與「上次結論」是兩件事：

```
current_probe=idle|in_flight
last_completed=ok|fail|tooling|none
last_completed_age=<秒>|-
```

只印 `status=in_flight action=skip` 正是 2026-08-20 那次沒有人看得出問題的原因。

**這三個欄位描述「本輪實際使用的那一筆觀測」，不是「快取裡當時放著什麼」，而計算位置必須在 monitor 分派之前。**
兩者都是實測踩出來的：初版在載入快取當下就算好，於是 `signal=ok`（本輪讀到新鮮觀測）時欄位顯示的是**上一輪消費的那筆**，
年齡累積成「上一輪間隔 ＋ 那筆當時的年齡」——2026-08-20 實測印出 239s／255s，而快取其實完全同步、真實年齡只有 83.6s。
第二版把重算放進 web 分派，但 monitor 的狀態機**在那之前**就已經 emit，於是同一輪印出
`component=web last_completed_age=58` 與 `component=monitor last_completed_age=179` 兩個互相矛盾的數字。
判斷邏輯兩次都不受影響（`signal=ok` 用的一直是本輪的 `probe_mono`），但那個數字逼近 `OBS_TRUST_SECONDS=240` 的門檻值，
**會讓人誤判的欄位本身就是缺陷**，而「一半正確的輸出」比全錯更難察覺。`tests/test_incident_handler.py` 的
`test_both_component_lines_report_the_same_observation` 釘住兩行必須一致。


### 通知投遞與 secret 落點

偵測與狀態機從 2026-08-19 起就在生產運作，但 `REPORT_MARK_ALERT_WEBHOOK` 一直沒設，
所以事件只進 journal 與狀態檔、**沒有任何外部投遞**。去重、提醒節奏、恢復判定都在運作，
少的只是最後一哩。

#### secret 為什麼**不能**放進 `/etc/default/report-mark-sync`

那是本專案的 canonical 設定來源（7 個 unit 都以 `EnvironmentFile=-` 讀它），但它
**必須是使用者可讀的**：`scripts/db_backup.sh` 會自己逐鍵讀它，因為手動 `make db-backup`
完全不經過 systemd 的 `EnvironmentFile`——2026-07-30 就是因為兩條路徑用不同設定而寫錯
落點。把 webhook URL 放進去等於讓任何本機使用者讀得到；收緊權限則弄壞手動備份。
**兩者都不可接受。**

所以 secret 有自己的落點：

```bash
sudo install -d -m 0700 -o root -g root /etc/report-mark
sudo install -m 0600 -o root -g root /dev/null /etc/report-mark/alert.env
sudo -e /etc/report-mark/alert.env        # 只寫一行 REPORT_MARK_ALERT_WEBHOOK=...
sudo systemctl daemon-reload
```

`report-mark-incident.service` 與 `report-mark-alert@.service` 都以
`EnvironmentFile=-/etc/report-mark/alert.env` 讀它。**`-` 前綴是刻意的**：secret 尚未
注入時 unit 不該啟動失敗，所以 unit 可以先部署、secret 後補。systemd 以 PID 1（root）
讀 `EnvironmentFile`、之後才降權到 `User=kashionz`，因此 0600 root:root 讀得到。

**別把 URL 寫進 repo、tracked `.env`、unit 檔、shell history、journal 或 PR。**
有測試靜態守著前幾項。

#### 投遞契約

| 項目 | 值 |
|---|---|
| HTTP | `curl -sS -X POST`，`Content-Type: application/json` |
| connect / overall timeout | `INCIDENT_NOTIFY_CONNECT_TIMEOUT`(5s) / `INCIDENT_NOTIFY_MAX_TIME`(10s) |
| 成功條件 | **只有 2xx**。刻意不用 `-f`——它把 3xx 當成功，而未跟隨的重導向代表 POST 根本沒到目的地 |
| retry | **下一輪重試，直到送達為止**（見下方「投遞失敗語意」） |
| payload | `{"component","action","severity","reason","text"}`；前四個是封閉詞彙，供接收端路由，`text` 給人看 |
| body 上限 | `INCIDENT_NOTIFY_MAX_SUMMARY`(500 字元) 後截斷 |
| 投遞失敗 | **事件照記，「已通知」不記**。`notified=no`，且通知時鐘不前進 |

#### 投遞失敗語意

**「事件發生了」與「通知送到了」是兩件事，狀態機必須分開記。**

通知節流的時鐘是 `last_notified`。把一則根本沒送達的通知寫進去，等於讓 30 分鐘的提醒
週期從零開始計時——事故於是靜默到下一個提醒週期為止，而 journal 看起來一切正常
（`action=firing` 確實出現過）。所以四條路徑一律以「這則有沒有真的送達」為閘：

| 路徑 | 送達 | 沒送達 |
|---|---|---|
| FIRING | `last_notified=now`、`opened_sent=yes` | `last_notified=0`、`opened_sent=no`，下一輪**仍以 FIRING 重送** |
| ESCALATED | `severity=CRITICAL` | **留在 WARNING**，否則升級條件下一輪就不成立、「惡化了」永遠不再嘗試 |
| REMINDER | `last_notified=now` | 不前進 |
| RESOLVED | 刪狀態檔 | **保留事件**（`action=resolve_retry`），否則「已恢復」永遠送不出去，而操作者最後看到的是 FIRING |

重試不會形成風暴：端點掛著時每一輪都失敗、實際送出 0 則；端點恢復後只送出一則，
之後時鐘前進、去重照常生效。重試必須仍是 **FIRING** 而不是掉進提醒分支——否則操作者
收到的第一則是「仍未恢復」，而他從沒收到過「開始了」。

**`opened_sent` 缺值一律視為 `yes`**：升級前寫下的狀態檔沒有這個鍵，當成 `no` 會讓既有
的進行中事件在部署當下多送一則 FIRING。

**兩個變數不是同一件事。** `NOTIFY_SENT` ＝這一輪有沒有送出（`emit` 的 `notified` 欄位用它）；
`NOTIFY_OK` ＝有沒有「該送而沒送到」的通知（狀態機的推進閘用它）。**未設定 webhook 時
`NOTIFY_SENT=no` 但 `NOTIFY_OK=yes`**——沒有東西要送，就沒有東西沒送到。若讓狀態機改看
`NOTIFY_SENT`，未設定 webhook 的部署（＝目前生產）會永遠關不掉事件，**偵測功能被通知
功能反噬**。`test_without_webhook_the_incident_still_closes` 釘住這條。

兩個容易寫錯的地方：

- **payload 必須跳脫**。`summary` 含 systemctl 讀來的值（`Result`、`ActiveState`…），
  那是外部輸入；未跳脫的 `"` 或 `\` 會產出格式錯誤的 JSON，接收端回 400，於是
  「通知送不出去」的真正原因會偽裝成「webhook 壞了」。
- **URL 不進 argv**。`curl ... "$URL"` 會讓 secret 出現在行程清單裡，任何本機使用者
  `ps` 就看得到。改用 `-K -` 從 stdin 餵 curl 設定檔。`report-mark-alert.sh` 同樣處理過。

### oneshot 的手動驗證：`Result=success` 不是證據

2026-08-19 部署 P1 時出現過一次假通過。`systemctl start report-mark-freshness.service`
的終端輸出看起來完全成功——`Result=success`、`ExecMainStatus=0`，還印出一份 freshness
報告。但 journal 裡該 unit 當日只有 9 行、全屬 08:31 那次自然觸發，
`ExecMainStartTimestamp` 也是 08:31。**unit 根本沒有執行。**

兩件事疊出來的：

1. `Result=success` 與 `ExecMainStatus=0` 既是**上一次**執行留下的值，也是**從未執行過**
   的 unit 的預設值——兩者無法區分。
2. oneshot 在沒有其他 unit 引用時會被 systemd 回收（`CollectMode=inactive`），
   回收後 `systemctl show` 讀到的是重新載入的乾淨狀態，屬性一律為空。

所以判準只能是「相對於一個**事前基線**的前進」：

```bash
BASE=$(bash scripts/verify_oneshot_ran.sh baseline report-mark-freshness.service)
sudo systemctl start report-mark-freshness.service
bash scripts/verify_oneshot_ran.sh verify report-mark-freshness.service "$BASE"
```

rc `0`＝`EXECUTION_PROVEN`／`1`＝**`EXECUTION_NOT_PROVEN`**／`2`＝用法或 token 格式錯誤。
必要條件是 `ExecMainStartTimestampMonotonic` 前進，再加至少一項佐證（journal 行數成長
或 `ExecMainExitTimestampMonotonic` 前進）。

三個實測逼出來的細節：

- **exit 必須與 exit 比**。初版 token 少了 `exit_mono`，於是拿現在的 exit 去比基線的
  start——而 exit 本來就晚於 start，那個佐證欄位**恆為 yes**。永遠成立的證據等於沒有證據。
- **格式錯誤的 token 必須是 rc=2，不能是判定**。初版把非數字欄位一律歸 0，於是隨便一個
  字串當 token 就讓所有現值看起來「都前進了」→ 假的 `EXECUTION_PROVEN`。
  **一個會在輸入壞掉時回報成功的驗證器，比沒有驗證器更危險。**
- journal 成長**單獨不足**——它可能因為 timer 的訊息而長。啟動時間戳前進才是必要條件。

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

退出碼：`0`＝PASS／`1`＝資產停更（FAIL）／`2`＝查不到（DB 不可用）／`3`＝**管線本身沒跑完（UPSTREAM_STALE）**。**四者刻意分流**：`1` 去看批次日誌、`2` 去看 DB 與 `/healthz`、`3` 去看 sync 殼與 claude 鎖。混成同一個碼等於把「DB 掛了」誤導成「批次壞了」。

### 資料新鮮度 ≠ 管線執行新鮮度

上一節量的是**結果**——四個 `max(created_at)` 有沒有前進。但那組數字回答不了一個問題：**管線最近一次「完整成功跑完」是什麼時候？**

兩件事會分開壞：

| | 症狀 | 偵測者 |
|---|---|---|
| 資料停更 | 批次在跑，但產不出東西（模型持續回空、擷取全被 rejected） | 四個 `max(created_at)`（`1`＝FAIL） |
| **管線停跑** | 管線根本沒有成功跑完，而 systemd 全程正常 | **管線心跳**（`3`＝UPSTREAM_STALE） |

後者為什麼看不見，是兩個刻意設計疊出來的：

1. `sync_new_reports.sh` 有一條 **rc=0 的早退路徑**——PID lock 被佔用時 `exit 0`（那是對的，重入會壞事）。
2. 下游六段是 **best-effort**，失敗只 `log` 不 `exit`（那也是對的）。

疊起來的後果：整條管線可以連續數天完全沒有成功跑完，而 `systemctl is-active` 與 `OnFailure` 都不會有任何反應。**2026-08-12 的事故正是這個形狀**——所有 `claude` 批次連續 4 天 100% 失敗、入庫歸零、unit 全綠，而症狀長得像「NAS 沒有新檔」。

### 管線心跳

`data/.last_successful_sync`，由 `scripts/sync_new_reports.sh` 在**完整成功**時原子寫入（暫存 → `sync -f` → `mv`）。內容只有 `ts`／`epoch`／`new_reports`／`pid`，**不含任何 secret**；`epoch` 才是機器讀的欄位（`ts` 只給人看，不當備援——多一個格式解析就多一個失效面）。

| 情境 | 更新心跳？ |
|---|---|
| 完整成功 | **是** |
| **完整成功但 0 篇新研報** | **是** ← 見下 |
| 下游某段回 `rc=75`（claude CLI 被別的批次佔用，`EX_TEMPFAIL`） | **是**——那是常態，不是異常 |
| PID lock 被佔用而跳過（`exit 0`） | 否 |
| 掛載／rsync／匯入失敗（`exit 1`） | 否 |
| 下游某段**異常**失敗（非 0 且非 75） | 否 |

**0 篇新研報仍然更新，是這個設計的關鍵。** 心跳量的是「管線有沒有成功跑完」，不是「有沒有新資料」——週末與連假沒有新稿是常態，用後者當健康指標會製造日曆型假警報。這也是它與上一節的分工：資料面的停更留給那四個 `max(created_at)`。

下游異常失敗不更新心跳，**但仍然不擋 sync、unit 仍然不變紅**——best-effort 的語意一個字都沒有改。改變的只有一件事：持續的下游異常從「躺在 `data/unit_failures.log` 裡等人去看」變成「管線執行新鮮度上的可見事實」。

門檻預設 **9 小時**（`--pipeline-hours`），由排程回推而**不是硬編 12 小時**：

- timer 是 `OnCalendar=*-*-* 00/3:00:00`＝每 3 小時
- 單輪最壞約 2.5 小時（訊號擷取 100 份實測約 47 分、最壞約 113 分，加上 rsync／匯入／摘要／標題／摘錄）
- 長輪會讓下一次觸發撞 PID lock 而 `exit 0`（不更新心跳）

一次長輪 ＋ 一次撞鎖跳過 ＋ 一個週期餘裕 ＝ 3 個週期 ＝ 9 小時。2026-08-16..19 生產實測間隔多為 3.0h，另有 10.57h／16.26h 兩個缺口——**那兩個是 08-18 的真實中斷，應該被報出來，不是要被門檻容忍掉**。

心跳缺席、格式損毀、**以及時間戳落在未來**（時鐘回跳或檔案被動過）一律算 UPSTREAM_STALE。未來時間戳特別要擋：把它當成新鮮會讓一個壞掉的時鐘**永久**抑制告警，而抑制是這裡最危險的失效方向。

**管線那一筆不經語料閘。** 語料閘的用意是「沒有新稿時別怪派生批次」，但管線有沒有跑完與有沒有新稿無關；放進閘內會製造致命抑制——連假期間管線整個停掉會被讀成「本來就沒事做」。同理，rc 的優先序是**上游 > 資產**：管線沒跑完時資產「停更」只是症狀，先報症狀會讓人去查錯的地方。

新增 `3` 之前已確認 `report-mark-freshness.service` **沒有宣告 `SuccessExitStatus`**（有測試釘住）——否則真正的 UPSTREAM_STALE 會變成 systemd 眼中的成功，而那個 unit 存在的唯一理由就是觸發 `OnFailure`。

`report-mark-freshness.service` 宣告 `OnFailure=report-mark-alert@%n.service`，**直接復用既有告警鏈、零新管道**（`report-mark-alert.sh` 恆容錯、恆 `exit 0`，被多一個 unit 呼叫是安全的）。

### `rc=0` 不等於「該入庫的都入庫了」（2026-08-20）

**匯入可以完全成功地什麼都沒收進來。** claude CLI 自我更新到缺 native artifact 的版本那次，7 篇新研報全部記成 `skip_untagged`，importer 照常 `rc=0`，排程殼只印一行「本次無新研報入庫」——**與「NAS 真的沒有新檔」在畫面上完全一樣**。

那一輪之所以沒有錯誤更新心跳，**純粹因為後面的每日簡報剛好 `rc=1`**。若當時不在簡報的執行時段、或其他下游段都正常，整輪會被判成完整成功、心跳前進、freshness `PASS`，7 篇靜默消失。同一形狀見 2026-08-12（33 檔、四天後才發現）。

#### 兩層修正

**第一層：環境失敗的述詞從「型別」改成「errno」。** `scripts/_claude_cli.py` 原本只接 `FileNotFoundError`（ENOENT）就拋 `CliNotFoundError` 中止整批——那個機制**本來就存在**，而且註解精準預言了這個失效模式。它漏接的原因是述詞太窄：這次拋的是 `OSError [Errno 8] ENOEXEC`（檔案在，但不是可執行格式），不是 `FileNotFoundError`。現在改判 `errno ∈ {ENOENT, ENOEXEC, EACCES, EPERM, EISDIR}`。**刻意不寬泛接 `OSError`**：ENOMEM／ENFILE 那類是暫時性資源壓力，中止整批會讓一次尖峰變成一輪完全沒跑。

**第二層：`rc=0` 之後仍要看計數。** importer 原子寫出 `data/.sync_last_stats`（`key=value`），殼層逐鍵解析、只收非負整數。分界**不看名字，看「重跑會不會不一樣」**：

| 計數器 | 分類 | 理由 |
|---|---|---|
| `fail` | **異常** | 抽字或寫 DB 拋例外；環境修好重跑就會入庫 |
| `skip_untagged` | **異常** | 標註前置條件失敗；同上 |
| `skip_admin` | 預期 | 標註成功且明確判定不該入庫 |
| `skip_non_research` | 預期 | 同上 |
| `skip_exists` | 預期 | 已在庫，冪等 |
| `skip_scanned` | 預期 | 掃描件抽不出文字，是**檔案本身的性質**，重跑一萬次也一樣 |

`skip_scanned` 那條是刻意的取捨：算成異常會讓心跳因為語料裡固定存在的掃描件而**永遠**不更新，而永遠紅的告警兩週內就會被當背景噪音（本 repo 已有兩次前例）。代價是它不留路徑紀錄，屬已知限制。

#### 三個容易寫錯的地方

- **殼層不可以 grep importer 那段給人看的 `=== sync summary ===`。** 那是人類文案，改一個字守門就靜默失效，而症狀是「心跳照常更新」——與沒有守門完全一樣。
- **匯入前要先刪 `data/.sync_last_stats`。** importer 中途死掉會留下舊檔，讀到上一輪的 `abnormal=0` 等於守門不存在。
- **讀不到或格式壞掉一律保守視為異常。** 這個判斷的唯一用途就是擋心跳，讀不到就當沒問題，等於在最需要它的時候把它關掉。

#### 補救走路徑，不走全庫掃描

三個 `FAIL_LOG` 寫入點的格式現在一致：`絕對路徑<TAB>階段<TAB>原因`（階段為 `extract`／`tag`／`ingest`）。**原本第三處寫的是 `file_hash<TAB>檔名<TAB>原因`**——欄位數相同但語意不同，於是那一類漏收拿不到路徑、無法精準補回。

`scripts/failures_to_delta.py` 把失敗記錄轉成合法的 `--delta` 輸入：只留「真的存在於本地鏡像下」的路徑，對不回檔案的行**計數並印出**而不是靜默丟棄（靜默丟棄會讓「補完了」與「有一半根本沒被看到」長得一樣）。2026-08-20 實測：7 篇、160 chunks、`fail=0`，而 `--all-local` 要對鏡像裡 16,736 個檔逐一抽字再查 DB。

```bash
uv run python scripts/failures_to_delta.py --out data/sync_delta_recover.txt
uv run python scripts/sync_new_reports.py --delta data/sync_delta_recover.txt --dry-run   # 先確認 skip_exists=0
uv run python scripts/sync_new_reports.py --delta data/sync_delta_recover.txt
```

**「NAS 真的沒有新檔」仍是完整成功。** 週末與連假沒有新稿是常態，用「有沒有新資料」當健康指標會製造日曆型假警報——那條設計沒有變，測試釘住。

### sync timer 刻意不補跑（2026-08-20）

**`report-mark-sync.timer` 是 `Persistent=false`，而同目錄的 backup／freshness／audit 三支是 `true`。那個非對稱是刻意的。**

`Persistent=true` 的補跑時機必然是「機器剛醒來」，而那正是最可能立刻又被收掉的一刻。2026-08-17 的生產 journal 是完整的一次：

```
16:34:25  boot
16:34:26  Started report-mark-sync.timer
16:34:28  Starting report-mark-sync.service     ← 補跑，開機後 3 秒
16:34:28  sudo mount-nas-research               ← 掛載此時才在設定
16:34:53  Stopped report-mark-sync.service      ← 存活 25 秒
16:34:53  Failed to enqueue OnFailure= job, ignoring:
          Transaction for report-mark-alert@... is destructive
```

同一形狀 2026-08-18 08:50 再發生一次。兩件事同時成立，而且**互相掩蓋**：

1. **關機期間 `OnFailure=` 完全無聲。** systemd 拒絕把 alert unit 排進已含 stop job 的 transaction，訊息是 `Failed to enqueue OnFailure= job, ignoring`——**那不是錯誤**，不進 `data/unit_failures.log`、不會讓 unit 變紅。本機 journal 有 5 次以上，且不限 sync：`report-mark-web.service`、`report-mark-audit.service` 都中過。**這條與 timer 設定無關，是 systemd 的關機語意**——任何 unit 在關機期間失敗，它的 `OnFailure` 都送不出去。
2. **被砍在 rsync 中途會留下拿不回來的檔案。** rsync 已把部分新檔落到本地，而 `--size-only` 讓下一輪認定它們已同步、**不再列進 delta**——而 delta 是匯入的唯一輸入。檔案在本地、DB 裡沒有、下一輪不會自己補，得靠 `sync_new_reports.py --all-local`。`data/sync_delta_20260817_163428.txt` 與 `data/sync_delta_20260818_085003.txt` 都是 **0 bytes**，正是這個指紋。

隔離實驗（user-scope systemd，未動生產、未關機）確認語意本身：回溯 stamp 檔模擬「關機兩天」後啟動 timer——`Persistent=true` **在同一秒觸發**，`Persistent=false` 與**完全移除該行**都不觸發、只等下一個排程。**寫成 `false` 而不是省略**，是為了讓讀 unit 的人看得出這是想過的決定。

**代價很小，而且不再是無聲的。** 這是每 3 小時的排程，錯過的那輪最多等到下一個整點；漏跑由 P1 心跳／freshness 報成 `UPSTREAM_STALE`（門檻 9 小時＝3 個週期）。

#### 長時間停機後 `UPSTREAM_STALE` 是預期行為

機器關機 12 小時 → 開機後**不**立刻補跑 → 下一個整點的正常排程跑完 → 心跳前進 → 恢復 `PASS`。中間那段報 `UPSTREAM_STALE` 是正確的，因為管線**確實**沒跑。

**不要為了讓狀態看起來綠而手動 `touch` 心跳。** 心跳是「管線完整跑完」的證據，手動蓋掉它就是把唯一能分辨「停機」與「壞掉」的訊號抹平——而那正是 2026-08-12 那次「所有批次連續 4 天失敗而 unit 全綠」能藏四天的原因。

**非對稱的理由**：每日一次的 timer 錯過一次就是整天沒有備份／沒有停更偵測／沒有耐久性稽核，而那一天無法由下一次涵蓋；且那三支都短命、唯讀（或原子改名），被砍在中途不會留下半成品。sync 兩個條件都不成立。由 `tests/test_sync_timer_persistence.py` 釘住。

### 被關機砍掉的那一輪：`data/sync_round_state`（2026-08-21）

上一節說明了「關機期間 `OnFailure=` 結構性地送不出去」。**那條缺口本身沒有被上一節補掉**——`Persistent=false` 只讓「開機瞬間補跑」不再發生，但 SIGTERM 可以在開工後任何一刻到來（實測那兩次都是開工約 25 秒後才被砍），而那一輪一樣完全無聲。

心跳看得到的是「久沒有完整成功」，看不到的是「那一輪被砍在 rsync 中途、新檔已落地而 `--size-only` 讓下一輪不再列出它們」——後者需要人去跑 `--all-local`，而沒有任何訊號會提醒他。

機制不靠 `OnFailure`，改由**下一輪自己補記**：

| 時機 | 寫入 |
|---|---|
| 取得 PID lock 之後、開工之前 | `state=running` |
| 正常收場（EXIT trap） | `state=done`＋`rc` |
| 乾淨的非零退出 | `state=failed`＋`rc` |
| 收到 SIGTERM／INT／HUP | `state=signalled`＋`signal=TERM`＋`rc` |
| SIGKILL／斷電／WSL 被收掉 | **寫不出來**——狀態停在 `running`，那正是指紋 |

下一輪進入點讀到上一輪停在 `running` 或 `signalled`，就補一筆 `STAGE=sync_round(aborted)` 到 `data/unit_failures.log`（**既有落點**，`/api/progress` 的 `unit_failures` 已經在讀它，零新管道、零前端改動），內容含殘留 delta 與 `--all-local` 復原指令。偵測延遲 ≤3 小時（下一輪排程）。

**`failed` 刻意不補記**：那條路 unit 會進 failed、`OnFailure` 正常運作，補記它等於同一次失敗記兩筆。

四個順序上的要求，動 `scripts/sync_new_reports.sh` 那段之前先讀懂：

1. **整段必須在取得 PID lock 之後。** 「已有 sync 在跑就跳過」那條分支刻意在安裝 trap 之前 `exit`，否則被跳過的那次會把仍在跑的那輪的鎖與狀態一起清掉——症狀是憑空多出一筆 aborted。
2. **補記必須早於本輪寫 `running`**，否則上一輪的狀態已被覆寫、永遠讀不到。
3. **終態一律由 EXIT trap 寫，訊號 trap 只記下訊號名再 `exit`。** 兩邊都寫會產生互相矛盾的紀錄。訊號 trap 不能省：沒有它，bash 走預設處置，`signal=` 永遠是空的，事後分不出「被砍」與「連 trap 都沒跑到」。
4. **`rc` 不明時整段 `RC=` 省略而不猜。** 被 SIGKILL 收掉的那一輪本來就沒有退出碼，填一個數字只會讓讀的人以為那是真的（`web/routers/monitor.py` 的 `_UNIT_FAIL_RE` 對 `RC=` 本來就是 optional）。

另在進入點加了 `systemctl is-system-running=stopping` 的前置判斷。**它是補集不是替代品**：擋得住「觸發點恰好落在關機 transaction 裡」，擋不住開工後才收到 SIGTERM。認不出 systemd（手動執行、容器）時一律放行。

**已知限制——它只覆蓋一半。** 這支殼沒被執行時一個字也寫不出來，所以「下一輪再也沒來」由管線心跳與 `check_batch_freshness.py` 的 `assess_pipeline`（`--pipeline-hours`）負責。兩者刻意分開，不可互相取代。

狀態檔是純執行期狀態（`.gitignore` 逐項忽略），遺失只會讓下一輪少補記一筆，不壞資料。四條路（done／failed／signalled／running）由 `tests/test_pipeline_heartbeat.py` 的 `RoundStateTests` 以**真的 bash 與真的訊號**走過，並比對 `_UNIT_FAIL_RE` 認不認得補記的標頭。

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

#### `NRestarts == 0` 是探針的閘，不是健康的定義

**兩者常被混為一談，而混淆的方向是「把正常誤判成異常」。** 探針用它區分「人為
啟動」與「crash loop」——systemd 在人為 `start`／`restart` 後把計數歸零、自動重啟時
累加，所以 `0` 代表「這次是人啟動的，給它 60 秒暖機」。

但**驗收腳本與 runbook 不可以把 `NRestarts == 0` 當成健康的判準**。2026-08-20 的 P2
換裝在 00:48:03–00:52:57 造成 4m54s 中斷（venv console script 的 shebang 指向改名前的
絕對路徑），計數因此停在：

```
baseline_nrestarts=84        # 基準時刻 2026-08-20 00:52:57（ActiveEnterTimestamp）
```

**此後一律看 delta，不看絕對值。** 寫「expected NRestarts=0」的檢查會從那一刻起
永遠紅，於是被關掉或被忽略——那正是告警失效最常見的死法。

**已知未驗證的一點**：上述「人為重啟歸零」來自 `check_web_health.sh` 的既有註解與
systemd 行為，本輪**沒有實證**（實證需要重啟生產 web，代價不對等）。所以下次人為
重啟後要重新記錄基準值，不要假設它仍是 84。同理，`data/unit_failures.log` 在該次
事故新增約 84 筆，**那是事故證據，不要清檔**；後續一律看「基準之後的新增筆數」。

### 探針刻意不相依 Python

`scripts/check_web_health.sh` 只用 `curl`／`systemctl`／coreutils，**不用 `uv run`、
不碰 `.venv`、不 import 任何 `app.*`**。2026-08-18 的根因正是 `/mnt/c`（9p）上的
venv 損毀，若探針相依 Python 環境，它會與被監控的服務一起死——那時最需要它，
而它不在。`tests/test_web_health_probe.py` 靜態守這條。

### Python venv 不可搬移——2026-08-20 用 4m54s 學到的

**`.venv` 不能「先在 A 路徑建好、再改名到 B 路徑」。** venv 內的 console script
（`bin/uvicorn`、`bin/pytest`…）第一行 shebang 寫的是**建置當下的絕對直譯器路徑**，
改名不會更新它。P2 的 CPU-only torch 換裝就是這樣做的，結果是：

```
Failed to spawn: uvicorn — No such file or directory
```

**那句話指的是直譯器，不是 `uvicorn`**——最容易把人帶往完全錯誤的方向（去查
uvicorn 有沒有裝）。中斷 4m54s，web 累積 84 次自動重啟。

正確做法只有兩條：**在最終的正式路徑上建置**，或使用能驗證的部署機制（symlink
切換、`--relocatable`、容器）。無論哪條，換裝後的驗收都必須包含「真的起得來」，
而不只是「檔案在」。

#### 目前的 venv 資產

| 路徑 | 狀態 | 說明 |
|---|---|---|
| `.venv` | **ACTIVE／唯一權威** | torch 2.12.1+cpu，`cuda_available=False`，nvidia／triton 套件 0 |
| `.venv.badpath` | BROKEN | 換裝失敗的搬移產物，console script shebang 指向舊絕對路徑，**不可用** |
| `.venv.pre-cpu` | **CONTAMINATED** | 混合快照（CPU torch ＋ CUDA/nvidia 殘留），**不是有效的回滾點** |

**要真的回滾到 CUDA，唯一正確路徑是**：checkout P2 之前的 `pyproject.toml`／`uv.lock`
→ 在正式路徑重新建置 → 驗證 → 受控切換。**不要把 `.venv.pre-cpu` 當回滾用**，
它裝的已經是 CPU torch。

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

---

## 事件偵測與通知（`report-mark-incident.timer`）

健康探針（上一節）**刻意不通知任何人**。這一節的 unit 才是決定「要不要打擾人」的地方。

存在理由：2026-08-18 的中斷期間，告警機制**確實運作了**——`data/unit_failures.log`
累積了 854 筆完整紀錄——但那個檔案沒有任何消費端，中斷是在一次無關的遷移工作中被
順帶發現的。那 854 筆同時說明另一件事：**沒有去重就等於沒有告警**。

同一次事件在這裡只會產生：**1 則 FIRING ＋ 每 30 分鐘一則提醒 ＋ 1 則 RESOLVED**。
以那次 4 小時 50 分的中斷換算＝ **11 則**，而不是 145 則。

### 它怎麼取得訊號

不是掛在探針的 `OnFailure` 上——`OnFailure` 只在失敗時觸發、`ExecStartPost` 只在
成功時觸發，兩者都看不到完整的狀態轉換，而 `RESOLVED` 必須看得到成功。

改為每 2 分鐘讀 systemd 為探針保留的執行結果：

| 欄位 | 用途 |
|---|---|
| `ExecMainStatus` | 探針的退出碼（0/3 健康、1/2 服務故障、4 探針自身錯誤） |
| `ExecMainExitTimestampMonotonic` | **單調時鐘**，判斷「是否有新觀測」。用它而非牆鐘，因為 WSL 休眠喚醒與時區調整會讓牆鐘跳動 |
| `Result` | 附在通知訊息裡供人判讀 |

### systemd **不會**永遠保存 `ExecMain*`

這是本節最容易被誤解的一件事，而誤解的代價是整套告警靜默失效。

`report-mark-health.service` 用預設的 `CollectMode=inactive`。2026-08-19 部署當天
以隔離的使用者層 unit 做了七組實測，結論是：

| 情境 | `ExecMainExitTimestampMonotonic` | `ActiveState` |
|---|---|---|
| 從未執行、無 unit 引用 | **0** | `inactive` |
| **成功**執行、無 unit 引用 | **0** ← 與上一列**逐欄完全相同** | `inactive` |
| **失敗**執行、無 unit 引用 | 保留 | `failed` |
| timer `enabled` ＋ `active` | 保留 | `inactive` |
| timer `enabled` 但被 `stop` | 保留（timer unit 仍載入著、仍持有引用） | `inactive` |
| **timer `disable`** | **0 ← 觀測當場消失** | `inactive` |

也就是說：**一次成功的探測與「探針從未執行」在 `systemctl show` 的輸出上無法區分**，
而 `systemctl disable report-mark-health.timer` 會立刻讓觀測消失。

兩個推論：

- **P5 的可觀測性前提是那個 timer 保持 enabled。** 有人停用它，P5 讀到的不是「失敗」
  而是**空值**——沒有任何錯誤訊息。
- 反過來，**失敗永遠看得見**（失敗的 unit 不會被回收），所以真正的 web 故障不會因此漏掉；
  會漏掉的是「從此不再有任何新觀測」。

另外，`systemctl show` 對**不存在**的 unit 也回 rc=0 加一整組預設值——「查詢成功」
什麼都不保證，只有 `LoadState` 分得出 unit 到底在不在。

### 監控自己的監控

因此每一輪都先驗證訊號源本身，再談服務健康：

```
systemctl is-enabled report-mark-health.timer     # enabled / disabled / not-found
systemctl is-active  report-mark-health.timer     # active / inactive
systemctl show       report-mark-health.timer -p LoadState -p ActiveEnterTimestampMonotonic
systemctl show       report-mark-health.service -p LoadState -p Result \
                     -p ExecMainStatus -p ExecMainExitTimestampMonotonic
```

結果分成兩類**互相獨立**的事件，各有自己的狀態檔：

| 類別 | 元件 | 來源 |
|---|---|---|
| **WEB_HEALTH** | `web` | 探針明確回報 exit 1／2／4 |
| **MONITOR_BLIND** | `monitor` | timer 被停用／不在 active／unit 不存在／探針 unit 不存在／觀測消失或過期／systemctl 查詢失敗 |

**「監控瞎了」不得覆蓋或解除進行中的 web 事件**，反之亦然。訊號不可信時，handler
完全不碰 `web.state`，只在輸出裡標記 `action=skip incident=FIRING`——因為
「我看不見了」不是「已經好了」，把它當成恢復會在真正的中斷中途送出 `RESOLVED`，
那比完全不告警更糟：它會讓人停止調查。

**訊號缺席永遠不得被當成健康。** 這是本節的硬不變量，也是初版最危險的缺陷：
初版在「沒有觀測且無進行中事件」時直接 `emit noop probe_never_ran` 並早退，
而那條路徑同時涵蓋「還沒跑第一輪」與「有人把 timer 停了」。

### bootstrap 與失明的分界

沒有觀測不一定是壞事——剛開機或剛 `enable` 時本來就還沒有。兩者用 **timer 進入
active 的時間**分辨（`ActiveEnterTimestampMonotonic`），而不是用開機時間，這樣
「剛開機」與「剛重新啟用」都涵蓋得到。

空窗上限 **300 秒**由 P4 的參數回推：`OnBootSec`(120) ＋ `AccuracySec`(10) ＋
探針最壞耗時 `TimeoutStartSec`(90) ＝ 220 秒，取 300 秒＝36% 餘裕。
**沒有沿用初版的 600 秒**——那個值是在還沒有任何實測分布時訂的。

### 觸發間隔的實測分布

**穩態間隔不是 120 秒。** `OnUnitActiveSec` 從 service 進入 active 起算，加上
`AccuracySec=10s` 的抖動與探針自身耗時。T0（2026-08-19 11:11:39）之後連續 9 個
間隔實測為 125／132／136／134／135／135／126／126／134 秒——min 125、max 136、
mean 131.4。把「正常」定成 120 會讓每一輪都看起來遲到。

| 間隔 | 判定 | 處置 |
|---|---|---|
| ≤ 150s | 正常 | 實測 max 136 ＋ 約 10% 餘裕 |
| 150–420s | 容忍 | 單次抖動或系統負載，尚不告警 |
| > 420s | **失明 WARNING** | ≈3 個週期沒有新結果，不再是抖動 |
| > 900s | **失明 CRITICAL** | ≈7 個週期，升級 |

升級（WARNING → CRITICAL）**立即通知，不等 30 分鐘的提醒週期**——否則
「暫時看不見」惡化成「確定被停掉」會被去重機制吞掉最多半小時。

### 恢復語意

`MONITOR_BLIND` 的解除**需要一筆真正新鮮的觀測**，不是只看 `systemctl is-active`。
timer 可以是 active 卻還沒產出任何東西（剛 enable、或 GC 之後的空窗），那個瞬間
我們仍然看不見任何東西。只看 `is-active` 就宣告恢復＝把失明當成健康。

### 狀態機

```
healthy + 無事件   → no-op（只更新觀測游標）
failure + 無事件   → FIRING，立即通知一次
failure + FIRING   → 抑制；只有距上次通知 ≥ 30 分鐘才送提醒
healthy + FIRING   → RESOLVED，通知一次，移除狀態檔
```

### 分級

| 探針退出碼 | 分級 | 語意 |
|---|---|---|
| `1` / `2` | **CRITICAL** | 使用者當下無法使用 |
| `4` | **WARNING** | 探針自己壞了＝「我不知道」，不是「壞了」 |
| 探針超過 420 秒沒有新結果 | **WARNING** | **監控失明**——記在 `monitor` 元件，不是 `web` |
| 探針超過 900 秒沒有新結果 | **CRITICAL** | 失明持續，升級（立即通知，不等提醒週期） |
| timer 被停用／不在 active／unit 不存在 | **CRITICAL** | 監控被關掉了——**這是最不能只當 INFO 的一種** |
| `systemctl` 查詢失敗 | **WARNING** | 「我不知道」，不是「壞了」 |

刻意不做時間門檻的升級（例如「持續 10 分鐘才升 CRITICAL」）：探針本身已有 3 次
重試跨 30 秒，再堆延遲會讓真中斷十幾分鐘才通知。

### 狀態檔

`data/.incidents/<component>.state`，key=value 單行格式（shell 可解析，不需 `jq`）。

**刻意不放 DB**：DB 不可用正是要告警的情境之一，把事件狀態放進去等於在最需要
告警時失去告警。

四個正確性要求：

| 要求 | 做法 |
|---|---|
| 原子寫入 | 先寫 `.tmp.$$` 再 `mv -f`（同一檔案系統的 rename 是原子的） |
| 並發保護 | `flock -n`；撞到就跳過本輪（狀態機的讀-改-寫不是原子的，兩份同時跑會讓「是否已通知」互相覆蓋） |
| 損毀復原 | 逐鍵解析而**不是 `source`**（後者等於任意程式碼執行）；**每一個會進入算術展開的欄位都做數值驗證**；`severity` 是封閉詞彙，值不在其中就丟棄。欄位損毀時 fail-open 回退為新事件——寧可多送一則，不要靜默漏送 |
| 投遞失敗 | 狀態照常推進，只記錄未送達。**通知先送、狀態後寫**：崩潰在兩者之間只會造成重複通知（下一輪重開事件），不會漏送。反過來（先寫狀態再送）會製造「已標記為已通知但其實沒送出」的窗口，那是靜默漏報 |

### 三個不變量（都由反轉實驗驗過）

**1. 觀測身分跨重開機仍正確。** `ExecMainExitTimestampMonotonic` 的 epoch 是「本次開機」，
狀態檔會一併記錄 `boot_id`；`boot_id` 不同時 `last_obs_monotonic` 直接重置，
避免上一個 boot 的值與新 boot 的值相比而被誤判成「同一次觀測」。

**2. 已開啟的事件不得被任何早退路徑擱置。** 初版在「探針從未執行」（daemon-reload 後
屬性被清空、重開機後 P4 還沒跑第一輪、或 P4 的 timer 被停用）時直接早退，
於是進行中的 FIRING 會永遠卡住——不再有提醒、也不會 RESOLVED。現在那條路徑只在
**沒有進行中事件**時才早退，有事件時一律落到「監控失明」的 stale 分支並照常發提醒。

**3. 時鐘回跳不得讓提醒靜音。** WSL 休眠喚醒或 NTP 校正會讓牆鐘倒退，
`now - last_notified` 變成負數而永遠小於門檻 ⇒ 提醒永遠不觸發。負值一律當成到期
（fail-open）。**判定「是否有新觀測」用單調時鐘，判定「提醒是否到期」用牆鐘**——
兩者角色不同，不可混用。

狀態檔**不進版控也不進備份**（`.gitignore` 有 `data/.incidents/`）：純執行期狀態，
遺失只會讓下一次失敗重新開一個事件，不影響正確性。

### 通知投遞

沿用既有的 `REPORT_MARK_ALERT_WEBHOOK`（opt-in，URL 不進 repo，寫在
`/etc/default/report-mark-sync`）。**未設定時仍照常維護 incident 狀態**，只是不投遞
——這樣日後設定它的當下狀態是一致的，不會突然湧出一批補送。

### 安裝

```bash
REPO=/home/kashionz/projects/report-mark
sudo cp "$REPO"/deploy/systemd/report-mark-incident.service \
        "$REPO"/deploy/systemd/report-mark-incident.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now report-mark-incident.timer
```

**部署順序**：探針（P4）先上並觀察 24 小時零誤報，才啟用本 unit。
沒有那一輪觀察，第一週的誤報會決定這套告警之後有沒有人理它。

### 觀察

```bash
journalctl -u report-mark-incident.service --since "24 hours ago" --no-pager | grep 'handler=incident'
# action 的分布：noop 應佔絕大多數
journalctl -u report-mark-incident.service --since "24 hours ago" --no-pager -o cat \
  | grep -oE 'action=[a-z]+' | sort | uniq -c
cat data/.incidents/web.state 2>/dev/null || echo "(無進行中事件)"
```

### 停用

```bash
sudo systemctl disable --now report-mark-incident.timer
rm -f data/.incidents/*.state          # 可選：清掉殘留的事件狀態
```

