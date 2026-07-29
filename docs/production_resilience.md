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
REPO=/mnt/c/Users/User/Desktop/Project/report-mark
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
- **既有的 `/mnt/nas-research` 是 `-o ro`**（研報來源刻意唯讀），寫不進去。所以另有一支 rw 掛載 `deploy/systemd/mount-nas-backup` → `/mnt/nas-backup`（同一個 share、不同選項、不同掛載點），搭配 `deploy/systemd/report-mark-backup.sudoers`。
- **驗過才改名成 `*.dump`。** 先寫 `.partial-*`，檢查檔頭魔數 `PGDMP` 與大小下限後才原子 `mv`。備份最惡劣的失敗型態是「檔案在、內容不能用」，而 `.dump` 這個副檔名同時是保留策略與新鮮度閘門的判準。
- **`docker exec` 一律不加 `-t`。** 配 TTY 會對 stdout 做行尾轉換，把二進位 dump 悄悄弄壞——檔案照樣產出、大小也合理，直到還原那天才發現。
- **週備用 `cp` 不用 hardlink。** drvfs 的 hardlink 支援不可靠，而「以為連結還在、其實日備輪替時一起砍掉了」是完全靜默的資料消失。

### 還原步驟（沒演練過的備份不算備份）

一律用 stdin 餵 `pg_restore`，不要 `docker cp`：備份檔在 `/mnt/nas-backup`，那是 WSL 的掛載點，`docker.exe` 看不到這個路徑；由 WSL 這側讀檔、管線送進容器才對得起來。

```bash
DUMP=/mnt/nas-backup/report-mark-db/daily/report-mark-critical-20260730_033000.dump

# 1) 先證明這份 dump 讀得回來（完全不動 DB）
docker exec -i report-mark-postgres pg_restore -l - < "$DUMP"

# 2) 還原到臨時 DB 驗過，再碰生產
docker exec -i report-mark-postgres psql -U postgres -c 'CREATE DATABASE restore_check;'
docker exec -i report-mark-postgres psql -U postgres -d restore_check \
  -c 'CREATE SCHEMA IF NOT EXISTS research;'
docker exec -i report-mark-postgres pg_restore -U postgres -d restore_check \
  --no-owner --no-privileges < "$DUMP"
docker exec -i report-mark-postgres psql -U postgres -d restore_check \
  -c 'select count(*) from research.qa_log;' \
  -c 'select count(*) from research.report_doc;'

# 3) 確認筆數合理後才動生產。單張表被誤刪／誤清時只還原那一張：
docker exec -i report-mark-postgres pg_restore -U postgres -d research \
  --no-owner --no-privileges -t qa_log < "$DUMP"

# 4) 整組還原到空 DB（例如 pgdata 全滅、重建叢集之後）：
make schema                                    # 先把 schema 建回來（含 vector 擴充與索引）
docker exec -i report-mark-postgres pg_restore -U postgres -d research \
  --no-owner --no-privileges --data-only --disable-triggers < "$DUMP"
docker exec -i report-mark-postgres psql -U postgres -d research -c 'DROP DATABASE restore_check;'
```

第 4 步用 `--data-only` 是因為 `make schema` 已經把表建好了（含 CHECK 與索引）；`--disable-triggers` 讓 `report_takeaway` / `report_signal` 的 FK 檢查在載入時先讓開——**但那只在 `research_report` 也還原到相同 `report_id` 時才有意義**，見上面的已知代價。

`pg_restore` 的退出碼要看：非 0 就是沒還原完，**不要因為「有些表看起來有資料」就當成功**。

### `make ingest-lowio` 現在有硬閘

`scripts/ingest_lowio.sh` 會關掉 `fsync` / `full_page_writes` / `synchronous_commit`，崩潰即可能整個 pgdata 報廢。它檔頭原本的安全論證是「本 DB 為衍生、可由原始研報重建」——**在上面那七張表存在之後，這句話已經不成立**。現在它開頭會檢查備份目錄有沒有 24 小時內的 `*.dump`，沒有就 `exit 1`，且在碰 docker 之前就擋下。

確定這座 DB 裡沒有不可重建資料（例如正在從零重建語料）時，用 `ALLOW_STALE_BACKUP=1 make ingest-lowio` 明示略過。

### 安裝

```bash
REPO=/mnt/c/Users/User/Desktop/Project/report-mark
sudo install -m 0755 -o root -g root "$REPO"/deploy/systemd/mount-nas-backup /usr/local/sbin/
sudo install -m 0440 -o root -g root "$REPO"/deploy/systemd/report-mark-backup.sudoers \
  /etc/sudoers.d/report-mark-backup
sudo cp "$REPO"/deploy/systemd/report-mark-backup.service \
        "$REPO"/deploy/systemd/report-mark-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now report-mark-backup.timer

# 首跑與驗收
sudo systemctl start report-mark-backup.service
journalctl -u report-mark-backup.service -n 40 --no-pager
ls -lh /mnt/nas-backup/report-mark-db/daily
```

`/etc/default/report-mark-sync` 是共用的環境檔（備份 unit 也讀它），備份專屬旋鈕的範例在 `deploy/systemd/report-mark-sync.env.example`。

**待驗（本輪未在主機上執行）**：NAS 帳號對 `\\192.168.1.100\投資研究處` 是否有寫入權、同一個 share 以 drvfs 掛在第二個掛載點是否如預期，都要等實際安裝那次才知道。備份腳本的落點檢查、原子改名、檔頭驗證與輪替已用假 `docker` 二進位在沙箱驗過。

## 這一輪刻意沒做

- **外部 uptime 監控**：`/healthz` 是給它用的介面，但要接哪一家（UptimeRobot／自架）是部署決策，不該由一次程式碼改動偷渡。
- **把 unit 失敗顯示在監控頁**：`data/unit_failures.log` 已是可讀來源，但要不要進 UI 屬產品決策。
- **告警投遞管道**：webhook 已留 opt-in 掛勾，設不設由部署端決定。
- **語料層（`research_report` / `report_chunk`）納入備份**：見上面的取捨與已知代價，那是一個容量決定（`full_text` 是全語料原文），不該夾帶在第一版備份裡。
- **異地／離線副本與加密**：NAS 已經比 pgdata 好一個數量級，但 NAS 本身壞掉仍是單點。
- **自動還原演練**：真正的驗收是定期把 dump 還原到臨時 DB 比對筆數。這需要排程與判準，先把還原步驟寫成可照抄的指令。
