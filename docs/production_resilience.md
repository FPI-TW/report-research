# 維運手冊：重啟策略、健康探針、告警、備份還原與各元件的安裝停用

> **用語**：正式環境＝EC2（`research.tingfong.com`，DB 是 RDS）；測試環境＝辦公室主機（`research.kashionzarchive.com`，DB 是容器 `report-mark-postgres`），見 `AGENTS.md`「環境角色與資料安全」。資源名、catalog、unit、使用者名稱與環境變數值裡的 `staging`、`prod`、`production` 是歷史命名，不代表角色。各主機實際裝了哪些元件、版本與開關記在 `docs/DEPLOYMENT_STATUS.md`；本文只寫程序、退出碼契約與刻意設計的理由。
>
> **安裝指令的寫法**：沒有另外註明時是辦公室主機的寫法（`sudo cp`／`sudo install` 到 `/etc/systemd/system/`，repo 根是 `/home/kashionz/projects/report-mark`）。EC2 的 unit 一律改用 `sudo deploy/install_units.sh --user ubuntu --root /home/ubuntu/report-mark <unit…>`（安裝時代換使用者、HOME 與 repo 路徑；給 `.timer` 會連同名的 `.service` 一起裝；只複製與 `daemon-reload`，不 enable），其餘步驟相同。
>
> **代號**：P4＝應用層健康探針（`scripts/check_web_health.sh`）；P5＝事件處理器（`scripts/incident_handler.sh`）。

## 重啟策略、健康檢查與失敗告警

這一組防的是「假活著」：DB 沒起來時 web（`Restart=always`）與邊緣（nginx、cloudflared）都會自己回來，`systemctl is-active` 全綠，但每個需要登入的請求都回 503；而沒有免認證端點時，外部探測拿到的一律是導向 `/login` 的 302，分不出站台正常、DB 掛了還是路由不存在。

### DB 容器自動重啟

`Makefile` 的 `db` target 以 `--restart unless-stopped` 建立容器，並對既有容器補一行 `docker update`（`--restart` 只在 `run` 時生效）。換主機或重建容器後確認：

```bash
docker update --restart unless-stopped report-mark-postgres
docker inspect report-mark-postgres --format '{{.HostConfig.RestartPolicy.Name}}'   # 應為 unless-stopped
```

### 免認證健康檢查 `/healthz`

`web/routers/health.py`，列在 `web/server.py` 的 `_AUTH_ALLOWLIST`。

- 健康：`200 {"status":"ok"}`
- DB 不可用：**`503 {"status":"degraded"}`**（回 200 帶欄位的話，多數 uptime 監控預設仍判定為健康）

這是唯一免認證且對外可達的資料端點，所以**不洩漏任何資訊**（只有 `status` 一個鍵，錯誤細節只進日誌）、**探測結果快取 5 秒**（會被掃描器高頻打）、**探測包 3 秒逾時**（DB hang 住時回 degraded 而非耗住連線）。`/healthz/storage`、`/healthz/llm`、`/healthz/security`、`/healthz/feed` 只回答本機直連，各自的語意在該檔的模組 docstring。

驗證（會讓服務短暫降級，只在維護窗口做）：

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8097/healthz     # 200
docker stop report-mark-postgres
sleep 6                                                                     # 等快取過期
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8097/healthz     # 503
docker start report-mark-postgres
```

**本機 `/healthz` 綠不代表外網連得到**：對外那一段由邊緣探針負責，見 `docs/EXTERNAL_ACCESS.md`。

### 失敗告警 `OnFailure=`

web、sync 與各支日排程 unit 都宣告 `OnFailure=report-mark-alert@%n.service`，由 `deploy/systemd/report-mark-alert.sh` 處理。它不寄信（主機沒有 MTA），做的是留下耐久且可查的痕跡：

- journal 一行 `ERROR`（`journalctl -p err -t report-mark-alert`）
- `data/unit_failures.log` 追加一筆，**含失敗當下的 journal 尾巴**（事後才發現時 journald 可能已輪替掉）
- 設了 `REPORT_MARK_ALERT_WEBHOOK` 才送 webhook（值放 `/etc/report-mark/alert.env`，見「通知投遞與 secret 落點」）

這條路徑**沒有去重**：每 2 分鐘或每 5 分鐘跑一次的 unit 不接它（探針交給 P5），或以 `SuccessExitStatus` 放過「會自己好」的退出碼，否則同一個故障會變成一連串重複通知。

**關機期間 `OnFailure=` 送不出去**：systemd 拒絕把告警 unit 排進已含 stop job 的 transaction（journal 記 `Failed to enqueue OnFailure= job, ignoring`），不進 `data/unit_failures.log`、unit 也不會變紅。sync 另以 `data/sync_round_state` 補這個缺口（見「被關機砍掉的那一輪」）。

例行檢查：

```bash
tail -50 data/unit_failures.log
journalctl -p err -t report-mark-alert --since '-7 days' --no-pager
```

### `SuccessExitStatus=143`

uvicorn 收到 `SIGTERM` 後以 143（128+15）退出，是正常收場。web unit 宣告它，否則每次 `systemctl restart` 都在日誌留下 `Failed with result 'exit-code'`，並觸發一次假的 `OnFailure`。

## unit 的真相來源與安裝

`deploy/systemd/` 是 unit 的真相來源，不是機器上的 `/etc`。改 unit 先改 repo 再安裝，不要只改主機上的副本（`tests/test_deploy_units.py` 守 unit 檔）。

- **主機專屬值放 `/etc/default/report-mark-sync`**（`REPORT_MARK_ROOT`、`SYNC_PATH_EXTRA` 等，範例是 `deploy/systemd/report-mark-sync.env.example`），幾乎所有 unit 都以 `EnvironmentFile=-` 讀它。這個檔沒裝時 unit 會以 `${REPORT_MARK_ROOT:?}` 失敗——正解是補裝環境檔，不是把路徑寫死進主機上的 unit。新主機才從範例複製；已有環境檔的主機只補鍵（`sudoedit`），**不要用範例覆蓋**（會洗掉 R2、信標等主機設定）。
- **unit 裡不可用 `%h`**：系統層 unit 的 `%h` 一律解析為 service manager 的家目錄（`/root`），不受 `User=` 影響；`uv` 會去建 `/root/.cache/uv` 而以 EACCES 失敗。各 unit 的 `HOME` 寫字面值且必須一致（測試釘住）。
- **`SYNC_PATH_EXTRA` 只為了讓 unit 找到 `uv`**（在 `~/.local/bin`，不在 systemd 預設 PATH）；`deploy/` 不得出現 nvm 路徑（測試釘住）。
- **這個環境檔必須是使用者可讀的、而且不可在 shell 裡 `source`**：`scripts/db_backup.sh` 與掛載腳本會自己逐鍵讀它（手動 `make db-backup` 不經 systemd），值合法地可能含 `(` `)`。所以 secret 不放這裡（見「通知投遞與 secret 落點」「DeepSeek 金鑰落點與輪替」）。
- 其他安裝目的地：`*.env.example` 對應 `/etc/default/`；`*.sudoers` 以 `install -m 0440` 裝進 `/etc/sudoers.d/` 且目的檔名不帶副檔名（sudo 忽略含 `.` 的檔名）；`mount-nas-*` 裝到 `/usr/local/sbin/`；`report-mark-alert.sh` 就地執行。

### 安裝／更新

```bash
REPO=/home/kashionz/projects/report-mark
sudo cp "$REPO"/deploy/systemd/report-mark-web.service "$REPO"/deploy/systemd/report-mark-alert@.service \
        "$REPO"/deploy/systemd/report-mark-sync.service "$REPO"/deploy/systemd/report-mark-sync.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl restart report-mark-web.service
systemctl status report-mark-web.service --no-pager
```

其餘 unit 的安裝在各自的章節。驗證 `OnFailure` 接上：

```bash
systemd-analyze verify /etc/systemd/system/report-mark-web.service
systemctl show report-mark-web.service -p OnFailure --value        # 應非空
```

`make serve` 刻意沒有 `--reload`，部署的 web 也一樣：改了 Python 要重啟行程。sync 的 NAS 掛載、rsync 與排程細節見 `docs/nas_scheduled_sync_deployment.md`。

## 連線預算：調大併發之前先算連線數

`app/services/db.py` 的池參數與兩個 server-side 逾時全部顯式設定，走 `app/config.py` 的 `DB_*` 旋鈕（值與理由見該檔註解與 `.env.example`）。風險不在「SSE 串流期間持有 session」（`async with SessionFactory()` 區塊裡沒有 `yield` 或 LLM 串流；`retrieval_pipeline.py` 刻意短連線），而在**沒有 `statement_timeout` 時單一慢查詢可以無上限佔住一條連線**。

### 算式

```
worker 數 × (DB_POOL_SIZE + DB_MAX_OVERFLOW) + 同時在跑的批次腳本數 × 2
    <  max_connections − superuser_reserved_connections
```

- 右邊（辦公室主機）：`pgvector/pgvector:pg16` 官方映像，`Makefile` 的 `docker run` 沒帶 `postgresql.conf` 覆寫 ⇒ `max_connections=100`、`superuser_reserved_connections=3` ⇒ 一般角色可用 **97**。EC2 以 RDS 參數群組的值為準。
- 左邊：web 是**單 worker**（`report-mark-web.service` 的 `ExecStart` 沒有 `--workers`）⇒ `1 × (5+15) = 20`；批次腳本各自單執行緒、同時只開一個 session，每支以 2 條計。
- 上界取 20 的理由：有併發閘的路徑只有 `/api/ask`（`web/routers/ask.py` 的 `_ASK_GATE`＝3）；`/api/search`、雷達、閱讀頁、`/api/progress` 沒有併發閘，其餘連線留給它們的突發量。

**改任何一項併發都要重算**：加 `uvicorn --workers`、放寬 `_ASK_GATE`、新增長跑批次或加 `--workers`。這些是逐條數出來的上界，不是壓測結果（壓測紀錄見 `docs/CAPACITY.md`）。

### 逾時的兩個非對稱決定

- `DB_STATEMENT_TIMEOUT_MS` 預設 **60000**：雷達目錄的全表 unnest、overview 分面、閱讀頁 `similar` 拉高 `ef_search` 的 HNSW 掃描都天生偏慢，取太緊會把正常功能打掛。60 秒比任何已知查詢高一個數量級，仍把「無上限」變成有上限。
- `DB_IDLE_TX_TIMEOUT_MS` 預設 **0（關）**，刻意的：`scripts/sync_new_reports.py` 先 `report_exists()` 開了交易，接著才做 LLM 標註與 BGE-M3 嵌入（大檔可達數分鐘），中間沒有 commit。設了它，每 3 小時的同步會把研報靜默丟進 `data/sync_failures.log`。要開就**只在 web 的 `.env` 開**——批次不讀 repo 根的 `.env`（走 `/etc/default/report-mark-sync`）。

### 維運長查詢的豁免

匯入流程最後的 `ANALYZE research.report_chunk` 可能久於 60 秒，而它是整條匯入的最後一步——被 timeout 砍掉時資料都已 commit，症狀只有 planner 統計靜默過期。三個呼叫點（`scripts/ingest_all.py`、`scripts/run_ingest.py`、`scripts/sync_new_reports.py`）都先呼叫 `db.relax_statement_timeout(session)`，值取自 `DB_MAINTENANCE_STATEMENT_TIMEOUT_MS`（預設 0＝不限）；`tests/test_db_engine.py` 靜態守住每個 `ANALYZE` 前面都有它。

它用 `SET LOCAL` 而非 `SET`：連線是池化的，SQLAlchemy 預設的 `reset_on_return="rollback"` 不會還原 GUC，`SET` 的豁免會跟著連線流到下一個借用者。

## 備份與還原

本節適用 DB 在容器裡的辦公室主機。EC2 沒有應用層備份（只有 RDS 自動備份，見 `deploy/aws/README.md`）與稽核錨點；帳號刪除的 tombstone 在 EC2 寫本機磁碟（見「還原後重放帳號刪除」）。

### 為什麼只備這二十張表

`scripts/db_backup.sh` 的 `BACKUP_TABLES` 是不可重建的表，共二十張。`user_session` 刻意不備（遺失只是全員重新登入）；`api_client_usage` 不備（每日額度的計數器，遺失只是當天額度重新起算）；監控觀測（`service_observation*`、`job_execution`、`db_stat_snapshot`）不備（遺失只少了歷史）。

| 表 | 為什麼備 |
|---|---|
| `research.qa_log` | 每一次提問、當時的來源與證據帳本、使用者的讚／倒讚。**沒有任何來源可以重建** |
| `research.report_takeaway` | 閱讀頁重點摘錄（LLM 批次產物 + 確定性錨點） |
| `research.report_signal` | 觀點雷達訊號（LLM 批次產物） |
| `research.report_brief` | 每日簡報（LLM 批次產物，來源清單由 Python 記錄） |
| `research.review_state` | 待複核人工處理狀態、註記、驗證結果與處理人；無法從原始研報或問答重建 |
| `research.app_user` | 個別帳號與角色。遺失＝所有人要重新建帳、`qa_log.user_id` 與處理人全部對不回名字。**含 Argon2id 密碼雜湊，備份檔要當機密看待** |
| `research.admin_audit_log` | 管理操作稽核；事後追查「誰做的」的唯一來源。雜湊鏈＋只能新增，還原時用 `pg_restore --disable-triggers` |
| `research.user_scope` | 另外授予的權限（`qa_content.read`、`ops.operate`、`api_clients.manage`）與授予人；遺失＝特殊權限全部要重新授予，且說不出當初是誰給的 |
| `research.account_deletion` | 帳號刪除排程（提出人、執行時刻、取消紀錄）。遺失＝尚未執行的刪除排程消失、帳號停在停用狀態卻永遠不會被刪。已執行的刪除另有 DB 之外的 tombstone（見「還原後重放帳號刪除」） |
| `research.report_visibility` | 管理員隱藏的研報與發布狀態（以 `file_hash` 為鍵）。重建語料不會重建它：遺失＝被隱藏的研報全部回到檢索、問答與閱讀頁，未發布的上傳草稿全部變成已發布 |
| `research.incident`、`research.incident_event` | 事故歷史（事件投影）：spool 匯入後即刪、journald 有保留期，事後無從重建 |
| `research.report_upload` | 管理員上傳的研報（以 `file_hash` 連到語料）：上傳人、時間、掃描引擎與病毒名、處理失敗原因、退回原因。上傳檔不在 NAS 鏡像裡，重建語料不會重建它 |
| `research.user_quota` | 個人配額覆寫（每日上限、理由、調整人）。遺失＝所有人退回程式預設配額 |
| `research.feature_flag` | 功能旗標覆寫。**還原後要人工確認旗標狀態**：還原等於把旗標帶回備份當下 |
| `research.usage_daily` | 閱讀、原檔、搜尋、問答的每日主題計數（匿名、沒有 user_id、不記搜尋字串）。請求當下才有，事後無從重建 |
| `research.analytics_daily` | 每晚彙總的指標。來源 `qa_log` 會被使用者硬刪、`usage_daily` 只到主題層，事後無法重算 |
| `research.auth_event` | 登入與安全事件，保留至少 365 天。刪帳時刻意保留（只有 UUID、IP、UA，從不存帳號名稱） |
| `research.api_client` | 對外 API 用戶端：名稱、啟用狀態、key scopes、限流與每日額度、建立人。只存金鑰的 sha256 與 prefix（還原後既有金鑰照常可用）；遺失＝所有用戶端要重建並重新發金鑰 |
| `research.api_client_entitlement` | API 用戶端的授權範圍；遺失＝每個用戶端能看到哪些研報的設定全部消失 |

沒備的是語料層（`research_report`、`report_chunk`）：它確定重建得回來——研報原檔在 NAS／R2，`extract → tag → ingest` 全程可續；代價是 CPU 時間（BGE-M3 約 3 篇／分），不是資料消失。

**已知代價**：`report_takeaway` 與 `report_signal` 以 `report_id` FK 指向 `research_report`，而 `report_id` 是每次 ingest 重新產生的 uuid。**語料層若被整個重建，這兩張表的備份就對不回去**（其餘沒有 FK，可還原；但 `review_state` 中指向舊 `research_report.id` 的抽取複核紀錄會變成孤兒，須依重建後的 id 核對）。不要在還原時以 `--disable-triggers` 硬塞孤兒列。

### 怎麼跑

```bash
make db-backup                       # 手動跑一次
systemctl list-timers report-mark-backup.timer   # 排程：每日 03:30（Persistent=true）
```

輸出落在 NAS（`REPORT_MARK_BACKUP_DIR`）：

```
<REPORT_MARK_BACKUP_DIR>/daily/report-mark-critical-YYYYmmdd_HHMMSS.dump   # 保留 7 份（BACKUP_KEEP_DAILY）
<REPORT_MARK_BACKUP_DIR>/weekly/report-mark-critical-YYYY-Www.dump         # 保留 4 份（BACKUP_KEEP_WEEKLY）
```

幾個刻意的設計：

- **掛載不可用就失敗，絕不退回本地路徑。** 備份的價值在「跟 pgdata 不同一塊磁碟」；靜默寫本地會產出一份看起來成功、實際上跟 pgdata 一起死的備份，還會餵飽 `ingest-lowio` 的閘門。
- **落點必須是另一個 share，不是把研報那個 share 再掛一次 rw。** NAS 帳號對 `投資研究處` 只有讀取權：第二個掛載點即使是 `rw`，寫入仍得 `Permission denied`（EACCES，伺服器端 ACL 在擋）——與唯讀掛載的 `Read-only file system`（EROFS）是兩個不同的 errno，mount 旗標給不了伺服器不給的權限。`deploy/systemd/mount-nas-backup` 掛的 UNC 與落點都由 `/etc/default/report-mark-sync` 提供（`NAS_BACKUP_UNC`／`REPORT_MARK_BACKUP_MOUNT`／`REPORT_MARK_BACKUP_DIR`），搭配 `deploy/systemd/report-mark-backup.sudoers`；`tests/test_db_backup.py` 的 `MountHelperTests` 擋住改回唯讀那個 share。
- **範例檔裡的落點是暫存區**（`01.會議暫存(會後刪除)/…`），依命名就會被清掉，而備份內容不可重建。NAS 有不會被清的位置時只要改 `REPORT_MARK_BACKUP_DIR` 一個值，腳本與 unit 都不必動。
- **掛載腳本讀環境檔用逐鍵 `sed`，不用 `source`**：那個檔是給 systemd 的 `EnvironmentFile` 讀的，值合法地可能含 `(` `)`，`source` 會語法錯誤，更糟時值被當指令求值。
- **驗過才改名成 `*.dump`**：先寫 `.partial-*`，檢查檔頭魔數 `PGDMP` 與大小下限後才原子 `mv`。`.dump` 這個副檔名同時是保留策略與新鮮度閘門的判準。
- **`docker exec` 一律不加 `-t`**：配 TTY 會對 stdout 做行尾轉換，把二進位 dump 悄悄弄壞。
- **週備用 `cp` 不用 hardlink**：drvfs 的 hardlink 支援不可靠。
- **docker CLI 由 `scripts/_docker_bin.sh` 實際探 `docker info` 決定**：WSL 上 `/usr/bin/docker` 可能是連不到 daemon 的死連結，只看 binary 存在與否會讓備份在第一步中止，而 unit 從外面看起來仍是 enabled。
- **寫這個落點的 unit 都自己掛 NAS**：備份在 `scripts/db_backup.sh` 裡掛；刪帳執行、刪帳重放與稽核錨定以 `ExecStartPre` 跑 `scripts/ensure_backup_mount.sh`（同一個 sudoers 條目 `sudo -n mount-nas-backup`；主機沒裝掛載輔助程式時略過，掛載失敗不擋主程式，由主程式以 rc=2 告警），所以 WSL 重開後下一次觸發就會掛回來。要立刻手動掛時要在 systemd 的命名空間裡做（互動 shell 裡掛的，服務看不到）：`sudo systemd-run --wait --pipe /usr/local/sbin/mount-nas-backup`。

### 還原步驟（沒演練過的備份不算備份）

一律用 stdin 餵 `pg_restore`，不要 `docker cp`：備份檔在 WSL 的掛載點，`docker.exe` 看不到那個路徑；由 WSL 這側讀檔、管線送進容器才對得起來。

```bash
DUMP=/mnt/nas-backup/<落點>/daily/report-mark-critical-<時間>.dump

# 1) 先證明這份 dump 讀得回來（完全不動 DB）
#    **不可寫成 `pg_restore -l -`**：`-` 是 psql 的慣例，pg_restore 會把它當檔名而
#    以 `could not open input file "-"` 失敗。它在沒有檔名引數時就讀 stdin。
docker exec -i report-mark-postgres pg_restore -l < "$DUMP"

# 2) 還原到臨時 DB 驗過，再碰線上的庫
docker exec -i report-mark-postgres psql -U postgres -c 'CREATE DATABASE restore_check;'
docker exec -i report-mark-postgres psql -U postgres -d restore_check \
  -c 'CREATE SCHEMA IF NOT EXISTS research;'
docker exec -i report-mark-postgres pg_restore -U postgres -d restore_check \
  --no-owner --no-privileges < "$DUMP"
docker exec -i report-mark-postgres psql -U postgres -d restore_check \
  -c 'select count(*) from research.qa_log;' \
  -c 'select count(*) from research.report_brief;' \
  -c 'select count(*) from research.review_state;' \
  -c 'select count(*) from research.app_user;' \
  -c 'select count(*) from research.admin_audit_log;' \
  -c 'select count(*) from research.user_scope;' \
  -c 'select count(*) from research.account_deletion;'

# 預期輸出：**必定出現 5 個錯誤**，這是正常的，不是備份壞了——
#   ERROR: relation "research.research_report" does not exist          ×2
#     （report_takeaway / report_signal 的 report_id FK 指向未納入備份的語料層）
#   ERROR: function research.audit_append_only() does not exist         ×2
#   ERROR: function research.audit_chain_before_insert() does not exist ×1
#     （admin_audit_log 的雜湊鏈／只能新增觸發器用到的函式屬於 schema、不在逐表 dump 裡；
#      資料照樣完整還原，臨時 DB 只是少了觸發器。整組還原時 `make schema` 會先建回函式）
#   pg_restore: warning: errors ignored on restore: 5
# **pg_restore 的退出碼不能當判準**：有被忽略的錯誤時它可能是 0 也可能是 1（PG 16 的
# pg_restore 是 1），兩種都不代表還原乾不乾淨。要看的是 `errors ignored on restore:`
# 那一行的數字（臨時 DB 演練＝恰好 5，多於 5 就要查），再加上面的逐表筆數。

# 3) 確認筆數合理後才動線上的庫。單張表被誤刪／誤清時只還原那一張（例如 qa_log；review_state 亦可用同法）：
docker exec -i report-mark-postgres pg_restore -U postgres -d research \
  --no-owner --no-privileges -t qa_log < "$DUMP"
# 還原了 qa_log／app_user 之後一定要重放帳號刪除（見下方「還原後重放帳號刪除」）：
uv run python scripts/replay_deletions.py

# 4) 整組還原到空 DB（例如 pgdata 全滅、重建叢集之後）：
make schema                                    # 先把 schema 建回來（含 vector 擴充、索引、稽核觸發器）
docker exec -i report-mark-postgres pg_restore -U postgres -d research \
  --no-owner --no-privileges --data-only --disable-triggers < "$DUMP"
# --disable-triggers 讓稽核的只能新增／雜湊鏈觸發器不擋還原；還原後驗鏈：
uv run python scripts/audit_anchor.py --verify-only
# 再重放帳號刪除：備份之後才被刪除的使用者，資料會跟著備份回來（rc=1＝發現並已重新刪除，屬預期）
uv run python scripts/replay_deletions.py

# 5) 收尾：清掉步驟 2 的臨時 DB（**不可在 -d restore_check 連線上下這道指令**，
#    PostgreSQL 不允許 DROP 自己正連著的資料庫）
docker exec -i report-mark-postgres psql -U postgres -c 'DROP DATABASE restore_check;'
```

第 4 步用 `--data-only` 是因為 `make schema` 已經把表建好了（含 CHECK 與索引）；`--disable-triggers` 同時讓 `report_takeaway`／`report_signal` 的 FK 檢查在載入時讓開——**那只在 `research_report` 也還原到相同 `report_id` 時才有意義**，見上面的已知代價。還原了 `feature_flag` 之後，到管理後台「功能旗標」頁確認旗標狀態。

演練要定期重做，而且要做到步驟 2 的筆數比對：備份檔存在、unit 是 active、`pg_restore` 有輸出，都不能證明這份備份還原得回來。

### 還原後重放帳號刪除（tombstone）

帳號刪除（管理頁提出 → 24 小時撤銷窗口 → `report-mark-delete-accounts.timer` 每小時執行 `scripts/execute_deletions.py`）會刪掉該使用者的 `qa_log`、指向它們的 `review_state`、`user_scope`、`user_session`，`app_user` 只留 UUID。可是**備份是刪除前的樣子**：從任何一份比刪除還舊的備份還原 `qa_log` 或 `app_user`，那個人的問答就復活了，`account_deletion.executed_at` 也一起回到過去，DB 裡沒有任何東西記得他已被刪除（`qa_log.user_id` 刻意沒有 FK）。

所以執行刪除時先在 DB 之外寫一筆 tombstone：`$REPORT_MARK_BACKUP_DIR/account-tombstones.jsonl`（與備份同一個 NAS 落點；一行 `{"user_id", "executed_at"}`，不含帳號名稱）。落點不存在時整批不做（rc=2），不退回本機——與 pgdata 同一塊磁碟的 tombstone 會跟 DB 一起遺失或一起被還原。

`scripts/replay_deletions.py` 讀 tombstone，逐一檢查 DB 是否還有該使用者的內容、或 `app_user` 又變回可識別／可登入；有就在同一筆交易重新刪除（稽核 `user.delete_replayed`）並以 **rc=1** 結束（0＝全部乾淨、2＝無法執行：DB 不可用、落點不存在、tombstone 檔壞掉）。

- **每次還原（整組或單表）之後手動跑一次**（上面步驟 3、4 已列）。rc=1 在還原後是預期結果：代表它找到並清掉了復活的資料；再跑一次應該是 rc=0。只想看不想刪用 `--check-only`。
- 平時由 `report-mark-replay-deletions.timer` 每日 04:30 跑；平常日出現 rc=1（`OnFailure` 告警）表示有人在沒走這份流程的情況下還原過資料，要追查。
- tombstone 檔本身不要刪、不要手改；它和稽核錨點（`audit-anchors.jsonl`，`report-mark-audit-anchor.timer` 每日 04:15 由 `scripts/audit_anchor.py` 寫入並驗鏈）一樣是 DB 之外唯一的證據。
- **EC2（正式環境）**：沒有 NAS，`/etc/default/report-mark-sync` 的 `REPORT_MARK_BACKUP_DIR` 指向本機磁碟 `/var/lib/report-mark/tombstones`（`sudo install -d -o ubuntu -g ubuntu -m 0750`）。DB 在 RDS，從快照還原不會動到這顆磁碟，所以仍符合「不跟 DB 一起被還原」；EC2 整台遺失時 tombstone 跟著消失。安裝：`sudo deploy/install_units.sh --user ubuntu --root /home/ubuntu/report-mark report-mark-delete-accounts.timer report-mark-replay-deletions.timer`，再 `enable --now` 兩支 timer；從 RDS 快照還原後一樣要手動跑一次重放。

### `make ingest-lowio` 的硬閘

`scripts/ingest_lowio.sh` 會關掉 `fsync`／`full_page_writes`／`synchronous_commit`，崩潰即可能整個 pgdata 報廢，而這座 DB 裡有不可重建的表。所以它開頭會檢查備份目錄有沒有 24 小時內的 `*.dump`，沒有就 `exit 1`，且在碰 docker 之前就擋下。確定這座 DB 裡沒有不可重建資料（例如從零重建語料）時，用 `ALLOW_STALE_BACKUP=1 make ingest-lowio` 明示略過。被 SIGKILL 後 `fsync=off` 不會自己還原，處置是 `make restore-durability`（`make db-audit` 的 `durability_off` 會抓到）。

### 安裝

```bash
REPO=/home/kashionz/projects/report-mark
sudo install -m 0755 -o root -g root "$REPO"/deploy/systemd/mount-nas-backup /usr/local/sbin/
sudo install -m 0440 -o root -g root "$REPO"/deploy/systemd/report-mark-backup.sudoers \
  /etc/sudoers.d/report-mark-backup
sudo cp "$REPO"/deploy/systemd/report-mark-backup.service \
        "$REPO"/deploy/systemd/report-mark-backup.timer \
        "$REPO"/deploy/systemd/report-mark-delete-accounts.service \
        "$REPO"/deploy/systemd/report-mark-delete-accounts.timer \
        "$REPO"/deploy/systemd/report-mark-replay-deletions.service \
        "$REPO"/deploy/systemd/report-mark-replay-deletions.timer \
        "$REPO"/deploy/systemd/report-mark-audit-anchor.service \
        "$REPO"/deploy/systemd/report-mark-audit-anchor.timer /etc/systemd/system/
# 環境檔要有 NAS_BACKUP_UNC、REPORT_MARK_BACKUP_MOUNT、REPORT_MARK_BACKUP_DIR（掛載腳本與這幾支 unit 都讀它；
# 少了掛載腳本會退回內建預設）。新主機從範例複製；已有環境檔時只補這幾個鍵，不要覆蓋整個檔。
sudoedit /etc/default/report-mark-sync
sudo systemctl daemon-reload
sudo systemctl enable --now report-mark-backup.timer
# 帳號刪除的執行、重放與稽核錨定都寫 NAS，與備份共用落點
sudo systemctl enable --now report-mark-delete-accounts.timer report-mark-replay-deletions.timer \
                            report-mark-audit-anchor.timer

# 換過落點時，舊掛載要先卸掉——mountpoint -q 會通過，然後在寫入探測才失敗
sudo umount /mnt/nas-backup 2>/dev/null || true

# 首跑與驗收
make db-backup                       # 或 sudo systemctl start report-mark-backup.service
ls -lh "$(sed -n 's/^REPORT_MARK_BACKUP_DIR=//p' /etc/default/report-mark-sync)/daily"
```

首次安裝或換落點後，照「還原步驟」的 1、2 步真的還原一次並比對筆數：檔頭魔數與大小檢查只擋得住頭尾壞掉。

## 應用層健康探針（`report-mark-health.timer`）

**`systemctl is-active` 不是應用健康的證據。** uvicorn 先跑 lifespan 再 bind，bind 失敗後行程仍會存活約 10 秒（背景載入 BGE-M3），配合 `Restart=always` 與 `RestartSec=3`，任何時間點去查 `is-active` 都有很高機率看到 `active`，而實際服務的請求數是 0。所以另有一支每 2 分鐘執行的探針 `scripts/check_web_health.sh`，由 `report-mark-health.timer` 觸發。

### 它檢查什麼（以及**不**檢查什麼）

它打 `http://127.0.0.1:8097/healthz`。**`/healthz` 只探 DB**（一次 `SELECT 1`，內部逾時 3 秒，結果快取 5 秒），不驗 BGE-M3 是否載入、reranker、NAS。它能證明兩件事：

1. **uvicorn 真的綁上了 :8097 並且會回應**（探針連得上）
2. **DB 可用**（回 200 而非 503）

R2 與 DeepSeek 帳號由探針另外檢查：退出碼 6 讀只回答本機直連的 `/healthz/storage`，退出碼 7／8 讀同樣只回答本機直連的 `/healthz/llm`。

用 `127.0.0.1` 而非 `localhost`：uvicorn 綁的是 `0.0.0.0`（**只有 IPv4**），而 `localhost` 在多數 glibc 設定下會先解析到 `::1`——那會讓探針自己製造假故障。

### 判定與退出碼

| 退出碼 | 意義 | unit 狀態 |
|---|---|---|
| `0` | 健康 | success |
| `1` | HTTP 探測失敗（非 200／連不上／逾時） | failed |
| `2` | web unit 不在 `active` | failed |
| `3` | 剛啟動的寬限期內 | success（unit 宣告 `SuccessExitStatus=3`） |
| `4` | 探針自己不能執行（缺 `curl`） | failed |
| `5` | **已退役、編號不重用**（原本檢查 claude CLI）。P5 收到 5 會落「未知退出碼」，代表主機上跑的是舊版探針 | — |
| `6` | `/healthz` 正常，但物件儲存（R2）連不上：`/healthz/storage` 回 503。原檔下載與 PDF 檢視會失敗，其餘功能正常 | failed |
| `7` | `/healthz` 正常，但 DeepSeek 的 CNY 餘額低於門檻：`/healthz/llm` 回 503 `low`。**尚未停擺**，要儲值。處置見「DeepSeek 帳號告警與 402／401 處置」 | failed |
| `8` | `/healthz` 正常，但 DeepSeek 帳號不可用、主答模型設定有誤或判斷不出來：`/healthz/llm` 回 `low` 以外的 503（`exhausted`／`auth_failed`／`unreachable`／`indeterminate`／`misconfigured`，本體讀不懂也算）。問答與批次 LLM 段停擺（`misconfigured` 只停問答主答）；檢索、閱讀、雷達正常 | failed |

`/healthz` 正常時 R2 與 LLM 兩項全查，一行 `reason=` 帶出全部原因（逗號分隔，例如 `storage_unreachable,llm_low`），退出碼取 **8 → 6 → 7** 最前面的那一個：

- **8 排第一**：它最重（問答與批次 LLM 段全停，CRITICAL），事件的嚴重度才等於同時成立的故障裡最重的那一個。
- **7 排最後**：「餘額低於門檻」會持續到儲值為止（可能好幾天），排前面的話這段期間 6 開不了事件。

被蓋住的那一項 reason 仍在 journal 那一行裡，前面那項修好後下一輪退出碼就換成它。web 只有一個 incident 元件：FIRING 期間退出碼換了不會另開事件；WARNING → CRITICAL（例如 7 → 8、6 → 8）會立刻送 ESCALATED，反方向（8 → 6／7）是降級、要等下一則提醒（最多 30 分鐘）才看到新的 exit。只認 HTTP 503：404（舊版 web 沒有這支端點）、連不上、200 都不開事件。`HEALTH_STORAGE_URL`、`HEALTH_LLM_URL` 設空字串可停用各自那一項。

**兩種「連不上」都算失敗**：uvicorn 沒綁上時，WSL mirrored networking 下連一個沒有 listener 的埠得到的是**逾時**（`curl rc=28`）而非拒絕（`rc=7`），因為 Windows 側是丟棄而非拒絕。

### 參數與其依據

| 參數 | 值 | 依據 |
|---|---|---|
| 觸發間隔 | 2 分鐘 | 最壞偵測延遲約 2 分 45 秒 |
| 單次逾時 | 5 秒（`HEALTH_TIMEOUT`） | `/healthz` 內部探測上限 3 秒，留 2 秒餘裕；`/healthz/llm` 內部最多等 4 秒 |
| 重試 | 3 次，間隔 15 秒（`HEALTH_RETRIES`、`HEALTH_RETRY_WAIT`） | 跨 30 秒，足以吸收 `systemctl restart` 的空窗 |
| 最壞耗時 | 55 秒 | 3 × 5 秒 ＋ 2 × 15 秒 ＝ 45 秒，第 3 次才成功時再加 `/healthz/storage`、`/healthz/llm` 各 5 秒；unit `TimeoutStartSec=90` |
| 啟動寬限 | 60 秒（`HEALTH_GRACE`） | bind 只要約 1 秒（模型暖機在背景，不阻塞 socket） |

**啟動寬限有三個條件，缺一不可**：unit 目前是 `active`、進入 active 未滿 60 秒、**且 `NRestarts == 0`**。第三個條件是關鍵——`Restart=always` ＋ `RestartSec=3` 會讓 `ActiveEnterTimestamp` 每 3 秒更新一次，只看時間戳的寬限在 crash loop 下恆為真，會永久抑制告警。systemd 在人為 `start`／`restart` 後把計數歸零、自動重啟時累加，所以 `0` 代表「這次是人啟動的」。

**`NRestarts == 0` 是探針的閘，不是健康的定義**：驗收腳本與例行檢查不可以寫「expected NRestarts=0」——一次事故之後它就永遠紅。看基準之後的 delta，不看絕對值；`data/unit_failures.log` 同理看基準之後的新增筆數，不要清檔。

### 兩條不可違反的設計

- **探針不相依 Python**：只用 `curl`／`systemctl`／coreutils，不用 `uv run`、不碰 `.venv`、不 import 任何 `app.*`。venv 損毀時相依它的探針會與被監控的服務一起死（`tests/test_web_health_probe.py` 靜態守這條）。其他 `scripts/check_*_health.sh` 與 `scripts/incident_handler.sh` 同理。
- **這支探針不通知任何人**：unit 刻意不宣告 `OnFailure=`。每 2 分鐘一次的探針接上沒有去重的告警鏈，同一次中斷會變成每小時約 30 則通知。去重、提醒節奏與恢復判定需要跨執行的狀態，那是 P5 的職責（下一節）。**不以降低探測頻率來掩蓋告警噪音**。

**部署目錄不放 `/mnt/c`（9p／drvfs）**：repo、`.venv`、`node_modules` 一律放 WSL 的 ext4。9p 對「寫入後立刻 rename」不保證即時可見，而 uv、npm 的原子安裝正是這個模式——venv 會停在半安裝狀態，所有相依它的 unit 同時失敗；另外 9p 的每次 `stat` 約 750 µs（ext4 約 1 µs），Python 啟動與 git 操作都是秒級成本。

**Python venv 不可搬移**：`.venv` 不能「先在 A 路徑建好、再改名到 B 路徑」。venv 內的 console script（`bin/uvicorn`…）第一行 shebang 寫的是建置當下的絕對直譯器路徑，改名不會更新它，症狀是 `Failed to spawn: uvicorn — No such file or directory`（指的是直譯器，不是 `uvicorn`）。要換 venv 就在最終路徑上建置，換裝後的驗收要包含「真的起得來」。

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

# 單次的退出碼與時間
systemctl show report-mark-health.service -p Result -p ExecMainStatus -p ExecMainExitTimestamp
```

### 停用

```bash
sudo systemctl disable --now report-mark-incident.timer   # 先停 P5：只停探針的話 P5 會回報 MONITOR_BLIND
sudo systemctl disable --now report-mark-health.timer
```

## 事件偵測與通知（`report-mark-incident.timer`）

健康探針刻意不通知任何人；`scripts/incident_handler.sh`（P5）才是決定「要不要打擾人」的地方。**沒有去重就等於沒有告警**：同一次事件在這裡只會產生 **1 則 FIRING ＋ 每 30 分鐘一則提醒 ＋ 1 則 RESOLVED**。

同一支腳本以不同的 `INCIDENT_*` 環境變數跑成多個實例，各看一支探針、各有自己的狀態目錄：web（本節）、邊緣（`docs/EXTERNAL_ACCESS.md`）、LineBot（`docs/LINEBOT_ALWAYS_ON.md`）、容器與主機（「事件投影與容器／主機探針」）、安全（「安全告警」）、來源主機（「來源主機信標」）。本節的規則對每個實例都成立。

### 它怎麼取得訊號

不是掛在探針的 `OnFailure` 上——`OnFailure` 只在失敗時觸發、`ExecStartPost` 只在成功時觸發，兩者都看不到完整的狀態轉換，而 `RESOLVED` 必須看得到成功。改為每 2 分鐘讀 systemd 為探針保留的執行結果：

| 欄位 | 用途 |
|---|---|
| `ExecMainStatus` | 探針的退出碼 |
| `ExecMainExitTimestampMonotonic` | **單調時鐘**，判斷「是否有新觀測」。用它而非牆鐘，因為 WSL 休眠喚醒與時區調整會讓牆鐘跳動 |
| `Result` | 附在通知訊息裡供人判讀 |

**systemd 不會永遠保存 `ExecMain*`**（探針 unit 是預設的 `CollectMode=inactive`）：

- 成功的 oneshot 在沒有其他 unit 引用時會被回收，`systemctl show` 的輸出與「從未執行」逐欄相同；失敗的 unit 不會被回收。
- timer `enabled`（即使被 `stop`）會持有引用、觀測保留；**`systemctl disable` 探針的 timer 會讓觀測當場消失**，P5 讀到的不是「失敗」而是空值。
- `systemctl show` 對**不存在**的 unit 也回 rc=0 加一整組預設值，只有 `LoadState` 分得出 unit 到底在不在。
- service 的各個屬性必須**一次 `systemctl show` 查完**：分開查會跨越探針的 invocation 邊界，拼出不可能同時成立的組合而誤報失明。重讀後仍矛盾時走信任窗分支（`INCIDENT_SNAPSHOT_RETRIES`）。

所以 **P5 的可觀測性前提是探針的 timer 保持 enabled**，而且每一輪都先驗證訊號源本身（timer 的 `is-enabled`／`is-active`／`LoadState` 與觀測新鮮度），再談服務健康。

### 兩類互相獨立的事件

| 類別 | 元件 | 來源 |
|---|---|---|
| **WEB_HEALTH** | `web` | 探針明確回報失敗的退出碼 |
| **MONITOR_BLIND** | `monitor` | timer 被停用／不在 active／unit 不存在／探針 unit 不存在／觀測消失或過期／systemctl 查詢失敗 |

各有自己的狀態檔。**「監控瞎了」不得覆蓋或解除進行中的 web 事件**，反之亦然：訊號不可信時 handler 完全不碰 `web.state`，只在輸出裡標記 `action=skip incident=FIRING`——「我看不見了」不是「已經好了」，把它當成恢復會在真正的中斷中途送出 `RESOLVED`。**訊號缺席永遠不得被當成健康。**

- **bootstrap 與失明的分界**：剛開機或剛 `enable` 時本來就還沒有觀測。以 **timer 進入 active 的時間**（`ActiveEnterTimestampMonotonic`）分辨，空窗上限 300 秒（`INCIDENT_BOOTSTRAP_SECONDS`；`OnBootSec` 120 ＋ `AccuracySec` 10 ＋ 探針 `TimeoutStartSec` 90 ＝ 220 秒，留餘裕）。
- **`MONITOR_BLIND` 的解除需要一筆真正新鮮的觀測**，不是只看 timer `is-active`。
- 探針的穩態間隔不是 120 秒：`OnUnitActiveSec` 從 service 進入 active 起算，加上 `AccuracySec=10s` 與探針自身耗時，實際約 125–140 秒。

| 距上一筆觀測 | 判定 |
|---|---|
| ≤ 420 秒 | 正常或單次抖動，不告警 |
| > 420 秒（`INCIDENT_STALE_SECONDS`） | **失明 WARNING**（約 3 個週期沒有新結果） |
| > 900 秒（`INCIDENT_BLIND_CRITICAL_SECONDS`） | **失明 CRITICAL**（約 7 個週期），升級 |

### 探針正在執行時（`in_flight`）

`in_flight` 是觀測的生命週期，不是健康結論。探針失敗時執行窗會從約 30ms 暴增到 45 秒以上，P5 連續幾輪剛好取樣在執行窗內並不罕見；而 systemd 在新一輪啟動時把 `Result`、`ExecMainStatus` 重設，in-flight 時讀不到上一輪的結論。所以「上一筆已完成的結論」由 P5 自己記在 `data/.incidents/probe_observation.state`（`boot_id`／`monotonic`／`status`／`result`；刻意是獨立檔案，不放進會在 RESOLVED 時被刪掉的 `web.state`）。

| 情況 | 判定 |
|---|---|
| 執行中 ＋ 快取 OK 且在信任窗內 | 沿用「上次健康」，不開事件；輸出仍標明 `current_probe=in_flight` |
| 執行中 ＋ 快取 **FAIL** | **開／維持 WEB_HEALTH FIRING**——絕不 `skip` 讓中斷消失 |
| 執行中 ＋ 快取超過**信任上限**（`INCIDENT_OBS_TRUST_SECONDS`，240 秒） | `monitor_blind` `observation_missed`——探針至少完成過一輪而 P5 沒讀到，那筆結果是什麼並不知道 |
| 執行中 ＋ 快取超過 `INCIDENT_BLIND_CRITICAL_SECONDS` | `monitor_blind` `observation_stale`（CRITICAL） |
| 執行中超過 `INCIDENT_PROBE_MAX_INFLIGHT`（120 秒） | `monitor_blind` `probe_stuck`——探針卡住，不是服務故障（unit 硬上限是 `TimeoutStartSec=90`） |
| 執行中 ＋ 無任何快取 | 依 bootstrap 窗判 `bootstrap` 或 `monitor_blind` |
| 快取的 `boot_id` 與本次開機不同 | **一律丟棄**，不跨開機沿用 |

`in_flight` 期間不解除進行中的 monitor 事件。**不要調大 `INCIDENT_OBS_TRUST_SECONDS` 來消除誤報**：240 秒是由探針節奏推導的（一個週期上界 140 秒＋執行中 90 秒），調大只會讓真正的漏讀晚 1–2 分鐘才被發現。

每一行輸出帶三個欄位，讓人一眼看出「現在在跑」與「上次結論」是兩件事；它們描述的是**本輪實際使用的那一筆觀測**，同一輪的 `web` 與 `monitor` 兩行必須一致（`tests/test_incident_handler.py` 釘住）：

```
current_probe=idle|in_flight
last_completed=ok|fail|tooling|none
last_completed_age=<秒>|-
```

### 狀態機與分級

```
healthy + 無事件   → no-op（只更新觀測游標）
failure + 無事件   → FIRING，立即通知一次
failure + FIRING   → 抑制；只有距上次通知 ≥ 30 分鐘才送提醒
healthy + FIRING   → RESOLVED，通知一次，移除狀態檔
```

web 這一組的分級（其他實例的退出碼語意以各探針檔頭為準；`INCIDENT_HOLD_EXIT_CODES` 列出的退出碼既不開也不關）：

| 探針退出碼 | 分級 | 語意 |
|---|---|---|
| `1` / `2` | **CRITICAL** | 使用者當下無法使用 |
| `4` | **WARNING** | 探針自己壞了＝「我不知道」，不是「壞了」 |
| `6` | **WARNING** | 服務降級：原檔下載與 PDF 檢視壞了，其餘正常 |
| `7` | **WARNING** | DeepSeek 餘額低於門檻（尚未停擺），請於 3 個工作天內儲值 |
| `8` | **CRITICAL** | LLM 帳號不可用或判斷不出來，問答與批次 LLM 段停擺；檢索、閱讀、雷達正常。由 7 轉來時立刻送 ESCALATED |
| 其他（含已退役的 `5`） | **WARNING** | 探針回報未知退出碼 |
| 探針超過 420 秒沒有新結果 | **WARNING** | **監控失明**——記在 `monitor` 元件，不是 `web` |
| 探針超過 900 秒沒有新結果 | **CRITICAL** | 失明持續，升級 |
| timer 被停用／不在 active／unit 不存在 | **CRITICAL** | 監控被關掉了——最不能只當 INFO 的一種 |
| `systemctl` 查詢失敗 | **WARNING** | 「我不知道」，不是「壞了」 |

升級（WARNING → CRITICAL）**立即通知，不等 30 分鐘的提醒週期**。刻意不做時間門檻的升級（例如「持續 10 分鐘才升 CRITICAL」）：探針本身已有 3 次重試跨 30 秒，再堆延遲會讓真中斷十幾分鐘才通知。

### 狀態檔

`data/.incidents/<component>.state`（其他實例在各自的 `data/.incidents-<名稱>/`），key=value 單行格式。**刻意不放 DB**：DB 不可用正是要告警的情境之一。狀態檔不進版控也不進備份，遺失只會讓下一次失敗重新開一個事件。

| 要求 | 做法 |
|---|---|
| 原子寫入 | 先寫 `.tmp.$$` 再 `mv -f` |
| 並發保護 | `flock -n`；撞到就跳過本輪 |
| 損毀復原 | 逐鍵解析而**不是 `source`**；每一個會進入算術展開的欄位都做數值驗證；`severity` 是封閉詞彙。欄位損毀時 fail-open 回退為新事件——寧可多送一則，不要靜默漏送 |
| 通知與狀態的順序 | **通知先送、狀態後寫**：崩潰在兩者之間只會造成重複通知，不會漏送。沒送達時不記「已通知」（見「投遞失敗語意」） |

三個不變量：

1. **觀測身分跨重開機仍正確。** `ExecMainExitTimestampMonotonic` 的 epoch 是「本次開機」，狀態檔一併記錄 `boot_id`；不同時 `last_obs_monotonic` 直接重置。
2. **已開啟的事件不得被任何早退路徑擱置。** 「探針從未執行」那條路徑只在沒有進行中事件時才早退；有事件時一律落到「監控失明」分支並照常發提醒。
3. **時鐘回跳不得讓提醒靜音。** `now - last_notified` 為負值時一律當成到期。判定「是否有新觀測」用單調時鐘，判定「提醒是否到期」用牆鐘，兩者不可混用。

### 通知投遞與 secret 落點

webhook URL 是 secret，放在自己的檔，**不放 `/etc/default/report-mark-sync`**（那個檔必須是使用者可讀的，見「unit 的真相來源與安裝」）：

```bash
sudo install -d -m 0700 -o root -g root /etc/report-mark
sudo install -m 0600 -o root -g root /dev/null /etc/report-mark/alert.env
sudo -e /etc/report-mark/alert.env        # 只寫一行 REPORT_MARK_ALERT_WEBHOOK=https://...
```

- 值必須是 `https://` 開頭的完整 URL，不加引號、不含空白。**寫完要驗證投遞**：unit 是否 active 與 `/healthz` 都看不出這個值對不對，只有 journal 的 `notified=yes`（或 `webhook 投遞失敗`／`不是合法 URL`）看得出來——檔裡留著安裝時的佔位字串，所有告警都不會送達，而且沒有任何東西會變紅。
- 各個 incident unit、`report-mark-alert@.service` 與上傳 worker 都以 `EnvironmentFile=-/etc/report-mark/alert.env` 讀它。**`-` 前綴是刻意的**：secret 尚未注入時 unit 不該啟動失敗，所以 unit 可以先部署、secret 後補。systemd 以 root 讀 `EnvironmentFile`、之後才降權，因此 0600 root:root 讀得到。
- **未設定時仍照常維護 incident 狀態**，只是不投遞——日後設定它的當下狀態是一致的，不會突然湧出一批補送。
- **別把 URL 寫進 repo、tracked `.env`、unit 檔、shell history、journal 或 PR**；也不進 argv（`curl ... "$URL"` 會出現在行程清單裡），兩支腳本都以 `-K -` 從 stdin 餵 curl。

#### 投遞契約

| 項目 | 值 |
|---|---|
| HTTP | `curl -sS -X POST`，`Content-Type: application/json` |
| connect / overall timeout | `INCIDENT_NOTIFY_CONNECT_TIMEOUT`(5s) / `INCIDENT_NOTIFY_MAX_TIME`(10s) |
| 成功條件 | **只有 2xx**。刻意不用 `-f`——它把 3xx 當成功，而未跟隨的重導向代表 POST 根本沒到目的地 |
| retry | **下一輪重試，直到送達為止**（見「投遞失敗語意」） |
| payload | `{"component","action","severity","reason","text"}`；前四個是封閉詞彙，供接收端路由，`text` 給人看。payload 必須跳脫（`summary` 含 systemctl 讀來的值） |
| body 上限 | `INCIDENT_NOTIFY_MAX_SUMMARY`(500 字元) 後截斷 |
| 設定值形狀 | 送之前先檢查：須以 `http://` 或 `https://` 開頭且不含空白。不合就不呼叫 curl，log 記「webhook 設定值不是合法 URL」（**不印值本身**），語意同投遞失敗 |
| 失敗原因 | log 帶 `HTTP <code> curl_rc=<n>`。`curl_rc` 常見值：3 URL 格式不合法、6 主機名解析不到、7 連線被拒、28 逾時、35／60 TLS 或憑證；`curl_rc=0` 代表對方回了非 2xx，看 HTTP code。curl 的 stderr 刻意不收（會帶主機名）。`report-mark-alert.sh` 同樣記 `curl_rc`，它用 `-f`，對方回 4xx／5xx 時是 22 |

#### 投遞失敗語意

**「事件發生了」與「通知送到了」是兩件事，狀態機分開記。** 通知節流的時鐘是 `last_notified`；把一則沒送達的通知寫進去，等於讓 30 分鐘的提醒週期從零開始計時，事故靜默到下一個提醒週期為止。所以四條路徑一律以「這則有沒有真的送達」為閘：

| 路徑 | 送達 | 沒送達 |
|---|---|---|
| FIRING | `last_notified=now`、`opened_sent=yes` | `last_notified=0`、`opened_sent=no`，下一輪**仍以 FIRING 重送** |
| ESCALATED | `severity=CRITICAL` | **留在 WARNING**，否則升級條件下一輪就不成立、「惡化了」永遠不再嘗試 |
| REMINDER | `last_notified=now` | 不前進 |
| RESOLVED | 刪狀態檔 | **保留事件**（`action=resolve_retry`），否則「已恢復」永遠送不出去 |

- 重試不會形成風暴：端點掛著時每一輪都失敗、實際送出 0 則；端點恢復後只送出一則。重試必須仍是 **FIRING** 而不是掉進提醒分支——否則操作者收到的第一則是「仍未恢復」。
- **`opened_sent` 缺值一律視為 `yes`**：沒有這個鍵的舊狀態檔當成 `no` 會多送一則 FIRING。
- **`NOTIFY_SENT` 與 `NOTIFY_OK` 不是同一件事**：前者＝這一輪有沒有送出（`notified` 欄位用它）；後者＝有沒有「該送而沒送到」的通知（狀態機的推進閘用它）。未設定 webhook 時 `NOTIFY_SENT=no` 但 `NOTIFY_OK=yes`；讓狀態機改看 `NOTIFY_SENT`，未設定 webhook 的部署會永遠關不掉事件（`test_without_webhook_the_incident_still_closes` 釘住）。

### 安裝

```bash
REPO=/home/kashionz/projects/report-mark
sudo cp "$REPO"/deploy/systemd/report-mark-incident.service \
        "$REPO"/deploy/systemd/report-mark-incident.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now report-mark-incident.timer
```

新主機先讓探針跑過一段時間、確認沒有誤報，再啟用本 unit 與 webhook。

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

## 批次停更偵測

### 為什麼 `OnFailure` 不夠

告警鏈只在 unit **進入 `failed`** 時觸發。而 `scripts/sync_new_reports.sh` 的下游各段（摘要、標題、摘錄、訊號、簡報、標題積壓）是**刻意的 best-effort**——`|| RC=$?` 之後只記一筆、不 `exit`：摘要失敗不該擋住下一輪匯入。代價是這幾段連續失敗永遠不會讓 unit 變紅；而停更的症狀是閱讀頁優雅降級、整區不進 DOM，不會有人回報。

所以停更靠**另一個獨立的偵測器**，量的是**結果**不是過程：不管是鎖撞了、LLM 帳號失效、timer 沒跑還是 NAS 沒掛上，只要派生資產不再前進就會紅。

### 怎麼跑

```bash
make freshness                                        # 手動跑一次
systemctl list-timers report-mark-freshness.timer     # 排程：每日 08:30（Persistent=true）
uv run python scripts/check_batch_freshness.py --json
```

退出碼：`0`＝PASS／`1`＝資產停更（FAIL）／`2`＝查不到（DB 不可用）／`3`＝**管線本身沒跑完（UPSTREAM_STALE）**。**四者刻意分流**：`1` 去看批次日誌、`2` 去看 DB 與 `/healthz`、`3` 去看 sync 殼與 LLM 批次鎖。`report-mark-freshness.service` 沒有宣告 `SuccessExitStatus`（測試釘住），四個非零碼都會觸發 `OnFailure`。判斷邏輯在 `app/services/batch_freshness.py`，管理後台「維運 → 資料健康」以同一組函式即時判讀。

### 資料新鮮度 ≠ 管線執行新鮮度

| | 症狀 | 偵測者 |
|---|---|---|
| 資料停更 | 批次在跑，但產不出東西（模型持續回空、擷取全被 rejected） | 四個 `max(created_at)`（`1`＝FAIL） |
| **管線停跑** | 管線根本沒有成功跑完，而 systemd 全程正常 | **管線心跳**（`3`＝UPSTREAM_STALE） |

後者看不見是兩個刻意設計疊出來的：`sync_new_reports.sh` 有一條 **rc=0 的早退路徑**（PID lock 被佔用時 `exit 0`，重入會壞事），下游各段又是 best-effort。整條管線可以連續數天沒有成功跑完，而 `systemctl is-active` 與 `OnFailure` 都沒有反應，症狀長得像「NAS 沒有新檔」。

### 管線心跳

`data/.last_successful_sync`，由 `scripts/sync_new_reports.sh` 在**完整成功**時原子寫入。內容只有 `ts`／`epoch`／`new_reports`／`pid`；`epoch` 才是機器讀的欄位。

| 情境 | 更新心跳？ |
|---|---|
| 完整成功 | **是** |
| **完整成功但 0 篇新研報** | **是** ← 見下 |
| 下游某段回 `rc=75`（LLM 批次鎖被別的批次佔用，`EX_TEMPFAIL`） | **是**——那是常態，不是異常 |
| PID lock 被佔用而跳過（`exit 0`） | 否 |
| 掛載／rsync／匯入失敗（`exit 1`） | 否 |
| 下游某段**異常**失敗（非 0 且非 75） | 否 |

- **0 篇新研報仍然更新**：心跳量的是「管線有沒有成功跑完」，不是「有沒有新資料」——週末與連假沒有新稿是常態，用後者當健康指標會製造日曆型假警報。
- 下游異常失敗不更新心跳，**但仍然不擋 sync、unit 仍然不變紅**；改變的只是持續的下游異常會變成管線執行新鮮度上的可見事實。
- 門檻預設 **9 小時**（`--pipeline-hours`），由排程回推：timer 每 3 小時、單輪最壞約 2.5 小時、長輪會讓下一次觸發撞 PID lock 而 `exit 0`；一次長輪＋一次撞鎖跳過＋一個週期餘裕＝3 個週期。
- 心跳缺席、格式損毀、**時間戳落在未來**（時鐘回跳或檔案被動過）一律算 UPSTREAM_STALE：把未來時間戳當成新鮮，會讓一個壞掉的時鐘永久抑制告警。
- **管線那一筆不經語料閘**，rc 的優先序是**上游 > 資產**：管線沒跑完時資產「停更」只是症狀。
- **不要為了讓狀態看起來綠而手動 `touch` 心跳**：它是唯一能分辨「停機」與「壞掉」的訊號。

### `rc=0` 不等於「該入庫的都入庫了」

匯入可以完全成功地什麼都沒收進來：標註的前置條件壞掉時，新研報全部記成 `skip_untagged`，importer 照常 `rc=0`，畫面與「NAS 真的沒有新檔」完全一樣。所以 `rc=0` 之後殼還要看計數：importer 原子寫出 `data/.sync_last_stats`（`key=value`），殼層逐鍵解析、只收非負整數。分界**不看名字，看「重跑會不會不一樣」**：

| 計數器 | 分類 | 理由 |
|---|---|---|
| `fail` | **異常** | 抽字或寫 DB 拋例外；環境修好重跑就會入庫 |
| `skip_untagged` | **異常** | 標註前置條件失敗；同上 |
| `skip_blocked` | **異常** | 行內標註被模型供應商的內容審查擋下；重跑不會變，要人處理（見「DeepSeek 批次的失敗處置」），殼另外點名 `make llm-blocked` |
| `skip_truncated` | **異常** | 行內標註輸出被截斷；重跑不會變（補救指令不撈），要人處理（同上） |
| `skip_admin` | 預期 | 標註成功且明確判定不該入庫 |
| `skip_non_research` | 預期 | 同上 |
| `skip_exists` | 預期 | 已在庫，冪等 |
| `skip_scanned` | 預期 | 掃描件抽不出文字，是**檔案本身的性質**，重跑也一樣。算成異常會讓心跳因為語料裡固定存在的掃描件而永遠不更新；代價是它不留路徑紀錄，屬已知限制 |
| `cache_fail` | 不算異常，殼層印 WARNING | 入庫 commit 之後寫抽取快取失敗：研報已在 DB、已記進 hashes，下游照常；重放補救對它無效（會 `skip_exists`）。持續出現多半是磁碟滿或 `data/extracted` 權限 |

有異常計數時不更新心跳，並記一筆 `sync_new_reports(ingest_abnormal)` 到 `data/unit_failures.log`。改殼的這一段時三件事不能錯：

- **殼層不可以 grep importer 那段給人看的 `=== sync summary ===`**：改一個字守門就靜默失效。
- **匯入前要先刪 `data/.sync_last_stats`**：importer 中途死掉會留下舊檔，讀到上一輪的 `abnormal=0` 等於守門不存在。
- **讀不到或格式壞掉一律保守視為異常**。

#### 補救走路徑，不走全庫掃描

`data/sync_failures.log` 的格式是 `絕對路徑<TAB>階段<TAB>原因`（階段為 `extract`／`tag`／`ingest`；內容審查擋下的標註是 `tag_blocked`、截斷的是 `tag_truncated`，預設都不撈）。`scripts/failures_to_delta.py` 把失敗記錄轉成合法的 `--delta` 輸入：只留真的存在於本地鏡像下的路徑，對不回檔案的行計數並印出。`--all-local` 要對鏡像裡每一個檔逐一抽字再查 DB，單篇失敗不要用它。

```bash
uv run python scripts/failures_to_delta.py --out data/sync_delta_recover.txt
uv run python scripts/sync_new_reports.py --delta data/sync_delta_recover.txt --dry-run   # 先確認 skip_exists=0
uv run python scripts/sync_new_reports.py --delta data/sync_delta_recover.txt
```

### 整批中止後的重放

**帳號／環境型中止（批次 rc=2）是整批一篇都沒做，而且不留逐篇失敗紀錄**：不寫 `data/sync_failures.log`、不進 `research.llm_task_failure`。所以 `failures_to_delta.py` 撈不到任何東西，`--all-local` 是 O(全部檔)——**這類中止兩者都不能用**。能重放的只有兩份「只有那一輪有」的檔，`scripts/sync_new_reports.sh` 在中止時保留它們：

| 中止在哪 | 保留什麼 | 殼印出什麼 |
|---|---|---|
| 匯入段 rc 不是 0 也不是 75 | 本輪 `data/sync_delta_<時間>.txt`（不刪；前幾輪同樣中止的也還在） | 依時間序（舊→新）列出**所有**保留的 delta，每份一條 `--delta … --hashes-out data/sync_hashes_retained_<同一時間>.txt` |
| 同上，且中止前已有研報入庫 | importer 寫的 `data/.sync_last_hashes.partial` 改名成 `data/sync_hashes_retained_<時間>_partial.txt` | 摘要、標題、摘錄各一條 `--hashes-file` 補跑指令 |
| 匯入段因 400 升級中止 | importer 寫的 `data/.sync_last_hashes.bad_request` 改名成 `data/sync_bad_request_<時間>.txt`（`file_hash<TAB>路徑`） | 逐行印出；重放本輪 delta 前先把這些路徑拿掉 |
| 下游任一段 rc=2 | 當輪 `data/.sync_last_hashes` 複製成 `data/sync_hashes_retained_<時間>.txt`（一輪一份） | 摘要、標題、摘錄各一條 `--hashes-file` 補跑指令 |

全部寫進當日 sync log，也落在 `data/unit_failures.log` 那筆紀錄的 log 尾巴裡。重放清單只列殼自己產生的 `sync_delta_<YYYYMMDD>_<HHMMSS>.txt`；`sync_delta_recover.txt` 這類手動檔不列。rc=75（批次鎖被別的批次佔用）不在此列，處置是 `--all-local`，補完刪掉本輪 delta。**rc=2 也可能是參數錯誤**（argparse 同樣以 2 退出，例如殼傳了批次不認得的旗標），動手前先看 sync log 確認中止原因。

**為什麼要 `_partial` 那份**：importer 逐篇各自 commit，但 hashes 清單只在最後寫出。中途整批中止時，已入庫的那幾篇不在任何 hashes 裡；重放同一份 delta 時它們又變成 `skip_exists`，重放寫出的 hashes 也沒有它們——摘要、標題、摘錄就永遠漏掉。importer 在中止時把「已 commit 的 hashes」寫到 `<hashes_out>.partial`（正常結束不寫；殼在匯入前先刪殘檔），殼改名保留。手動加 `--hashes-out X` 重放又中止時，partial 在 `X.partial`，要自己補跑。

**為什麼要 `--hashes-out`**：`sync_new_reports.py` 預設把入庫 hashes 寫到固定的 `data/.sync_last_hashes`，連續重放多份 delta 時每份都覆寫前一份，只有最後一份的研報會跑到下游。摘要與標題還有全表補的路徑，**摘錄沒有**，會靜默缺漏。`--hashes-out` 讓每份重放各寫一份，而且不動預設檔。

處置步驟（在主 checkout 執行；從 worktree 跑不與排程互斥）：

1. 排除中止原因。rc=2 的來源：DeepSeek 帳號層級錯誤（401 金鑰、402 餘額、模型不存在；中止訊息以 `API[auth]`／`API[quota]`／`API[config]` 開頭）、模型名不在白名單（訊息含「不在 DeepSeek 白名單」）、`LLM_PROVIDER` 退役值或拼錯、缺金鑰、斷路器，或 400 升級。**LLM 帳號型中止（例如餘額不足 402）的處置是儲值**，沒有別的後端可以改走。
2. 停排程，免得重放途中被下一輪搶鎖或覆寫：`sudo systemctl stop report-mark-sync.timer`。
3. 依殼印出的順序（舊→新）逐份重放 delta，**每重放一份就立刻用它自己的 `--hashes-out` 補跑三段**，再換下一份：

   ```bash
   uv run python scripts/sync_new_reports.py --delta data/sync_delta_<T>.txt --hashes-out data/sync_hashes_retained_<T>.txt
   uv run python scripts/generate_summaries.py --hashes-file data/sync_hashes_retained_<T>.txt
   uv run python scripts/generate_titles.py --hashes-file data/sync_hashes_retained_<T>.txt
   uv run python scripts/extract_takeaways.py --hashes-file data/sync_hashes_retained_<T>.txt
   ```

4. 下游中止保留的每一份 `data/sync_hashes_retained_<時間>.txt`，以及匯入中止保留的 `data/sync_hashes_retained_<時間>_partial.txt`，同樣依序補跑摘要、標題、摘錄（`--hashes-file`）。三段都只挑 `IS NULL`，重跑冪等，與第 3 步的先後無關。
5. 每份補完、確認 `make freshness` 與 sync log 無新錯誤後，刪掉用過的 delta 與 hashes 檔（都已 gitignore；delta 不刪的話，下次整批中止時會再被列進重放清單）。
6. 重新啟用排程：`sudo systemctl start report-mark-sync.timer`。

### sync timer 刻意不補跑

**`report-mark-sync.timer` 是 `Persistent=false`，而每日的 backup／freshness／audit 等是 `true`。那個非對稱是刻意的**（`tests/test_sync_timer_persistence.py` 釘住）。

`Persistent=true` 的補跑時機必然是「機器剛醒來」：NAS 掛載還在設定，而且那正是最可能立刻又被收掉的一刻。被砍在 rsync 中途會留下拿不回來的檔案——rsync 已把部分新檔落到本地，而 `--size-only` 讓下一輪認定它們已同步、**不再列進 delta**（delta 是匯入的唯一輸入）；檔案在本地、DB 裡沒有、下一輪不會自己補，得靠 `sync_new_reports.py --all-local`。0 bytes 的 `data/sync_delta_<時間>.txt` 是這個情況的指紋。

- 寫成 `false` 而不是省略那一行，是為了讓讀 unit 的人看得出這是想過的決定。
- 代價很小：每 3 小時的排程，錯過的那輪最多等到下一個整點；漏跑由心跳／freshness 報成 `UPSTREAM_STALE`（9 小時＝3 個週期）。**長時間停機後報 `UPSTREAM_STALE` 是預期行為**，下一個整點的排程跑完就恢復。
- 每日一次的 timer 相反：錯過一次就是整天沒有備份／沒有停更偵測／沒有耐久性稽核，而且那幾支短命、唯讀（或原子改名），被砍在中途不會留下半成品。

### 被關機砍掉的那一輪：`data/sync_round_state`

`Persistent=false` 只讓「開機瞬間補跑」不再發生；SIGTERM 仍可能在開工後任何一刻到來，而關機期間 `OnFailure=` 送不出去。這個缺口由**下一輪自己補記**：

| 時機 | 寫入 |
|---|---|
| 取得 PID lock 之後、開工之前 | `state=running` |
| 正常收場（EXIT trap） | `state=done`＋`rc` |
| 乾淨的非零退出 | `state=failed`＋`rc` |
| 收到 SIGTERM／INT／HUP | `state=signalled`＋`signal=TERM`＋`rc` |
| SIGKILL／斷電／WSL 被收掉 | **寫不出來**——狀態停在 `running`，那正是指紋 |

下一輪進入點讀到上一輪停在 `running` 或 `signalled`，就補一筆 `STAGE=sync_round(aborted)` 到 `data/unit_failures.log`（`/api/progress` 的 `unit_failures` 讀它），內容含殘留 delta 與 `--all-local` 復原指令。偵測延遲 ≤3 小時。`failed` 刻意不補記：那條路 unit 會進 failed、`OnFailure` 正常運作，補記等於同一次失敗記兩筆。

動 `scripts/sync_new_reports.sh` 那段之前先讀懂四個順序上的要求：

1. **整段必須在取得 PID lock 之後。** 「已有 sync 在跑就跳過」那條分支刻意在安裝 trap 之前 `exit`，否則被跳過的那次會把仍在跑的那輪的鎖與狀態一起清掉。
2. **補記必須早於本輪寫 `running`**，否則上一輪的狀態已被覆寫。
3. **終態一律由 EXIT trap 寫，訊號 trap 只記下訊號名再 `exit`。** 兩邊都寫會產生互相矛盾的紀錄；訊號 trap 不能省，否則 `signal=` 永遠是空的。
4. **`rc` 不明時整段 `RC=` 省略而不猜**（`web/routers/monitor.py` 的 `_UNIT_FAIL_RE` 對 `RC=` 是 optional）。

進入點另有 `systemctl is-system-running=stopping` 的前置判斷（正在關機就不開工）；它擋不住開工後才收到的 SIGTERM，認不出 systemd 時一律放行。這套機制只在殼有被執行時有效：「下一輪再也沒來」由管線心跳負責，兩者不可互相取代。狀態檔遺失只會讓下一輪少補記一筆。`tests/test_pipeline_heartbeat.py` 的 `RoundStateTests` 以真的 bash 與真的訊號走過四條路。

### 兩個刻意的預設

- **語料閘（`corpus`）抑制連鎖假警報。** 派生批次都只吃「本輪新入庫」的研報，沒有新研報時它們一行都不會產出——那是正確行為。所以語料自己在同窗期內沒前進時，派生資產的過期一律判 `suppressed`；少了這一層，一個連假就讓幾個資產同時亮紅。語料閘的母體刻意與批次一致（`full_text IS NOT NULL AND is_research IS NOT FALSE`）——用全表 `max()` 會被行政／活動檔拉新，抑制就失效了。`corpus` 本身的門檻預設 0：語料停更由 sync unit 的 `OnFailure` 覆蓋，而 NAS 供稿有連假空窗。
- **`signal` 預設門檻 0（不告警）。** 訊號只來自高覆蓋的少數研報，排程把積壓跑完之後 `max(created_at)` 就不再前進，與「這段時間沒有合格研報」無法區分——設任何門檻都會在積壓耗盡那天開始每日假警報。「訊號排程壞了」走另一條路：sync 殼對 `extract_signals` 非零退出會記進 `data/unit_failures.log`（測試釘住那個呼叫）。要開＝`--signal-days 14`。

### 排程健康：`unit_failures.log` 的讀取端

`data/unit_failures.log` 只有被讀才有用。`/api/progress` 回兩塊，由管理後台「維運 → 管線」分頁（`/app/admin/operations/pipeline`，需要 `ops.read`）的「排程與執行」卡呈現；失敗的即時通知靠 `OnFailure` 的 Slack 告警，這張卡是事後查「哪一輪、哪一段、退出碼多少」的地方。它只讀 web 行程，不經維運代理，代理或監控收集沒裝的主機上照常可看：

| 欄位 | 內容 |
|---|---|
| `sync` | `data/sync_run_<date>.log` 的最後一行 ＋ 完成判定 |
| `unit_failures` | `count_24h` / `count_7d` / `latest` / 最近 5 筆（unit、stage、rc） |

兩個判定上的坑，都有測試釘住：

- **完成判定看「最後一個標記行是 start 還是 done」**，不是「整檔有沒有 done」。log 是每日一檔、一天被多輪同步接續 append，看整檔等於第一輪跑完之後永遠顯示「已完成」。
- **異常條件是時間窗計數，不是累計未讀數。** 這個檔 append-only、沒有 logrotate、也沒有已讀游標，累計數當條件會永遠亮著。同理，待複核筆數常態非零，只列在頁首的中性「待人看」，不進燈號。

`/api/progress` 的快取分層與熱點（`data/tags/` 的 scandir）記在 `web/routers/monitor.py` 檔頭；管線分頁每 15 秒輪詢一次，分頁在背景時不輪詢。

### 安裝

```bash
REPO=/home/kashionz/projects/report-mark
sudo cp "$REPO"/deploy/systemd/report-mark-freshness.service \
        "$REPO"/deploy/systemd/report-mark-freshness.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now report-mark-freshness.timer

# 首跑與驗收（rc=1、rc=3 是合法結果，代表真的有東西停更）
sudo systemctl start report-mark-freshness.service
journalctl -u report-mark-freshness.service -n 30 --no-pager
systemctl show report-mark-freshness.service -p OnFailure --value   # 應非空
tail -20 data/unit_failures.log                                     # 停更時應多一筆
```

它沿用 `/etc/default/report-mark-sync`（`REPORT_MARK_ROOT` 與 `SYNC_PATH_EXTRA`）。

## 資料完整性稽核

### 與停更偵測的分工

停更偵測量的是「批次有沒有在**前進**」，這支量的是「已經產出的資料有沒有**互相矛盾**」。本 repo 的完整性保證幾乎全在「寫入端很小心」，而不在 DB 的約束裡：`qa_log.conversation_id` 與 `report_brief.report_ids` 刻意無 FK、`embedding` 可 NULL、`report_signal.market` 與 `research_report.market` 是兩份各自寫入的副本。代價是壞掉的方式全部是靜默的——重複 `chunk_index` 只讓閱讀頁跳到錯的位置、`market` 不一致仍會算出看起來合理的共識數字。

### 九條檢查

`scripts/db_audit.py` 的 `CHECKS` 有 **8 條 SQL 斷言**（各回一個違反列數，0＝通過），外加 1 條走 Python 判準的取樣比對：

| 級別 | 檢查 | 為什麼要 |
|---|---|---|
| error | `durability_off` | **整組裡唯一「不修會失去全部資料」的一條**，見下 |
| error | `null_embedding` | 那些 chunk 對語意檢索完全不存在（檢索端跳過並記數是降級，不是修復） |
| error | `duplicate_chunk_index` | 閱讀頁錨定跳錯位置，看起來只像「引文對不上」 |
| error | `signal_market_mismatch` | 雷達把訊號歸到錯的市場，數字仍然合理 |
| error | `is_research_null` | 未判定的研報會被 ingest 閘門與各批次靜默略過 |
| error | `upload_draft_mismatch` | 上傳的草稿狀態（`report_upload.state`）與可見性（`report_visibility.publication`）不一致：未審核的研報已經對所有人可見，或研報卡在不可見卻沒有可發布的上傳紀錄（退回後寬限期內、尚未清除的草稿不算：退回刻意不動 visibility，清除時才一起刪） |
| warn | `chunkless_report` | 有全文卻沒有任何 chunk＝檢索不到 |
| warn | `takeaway_sha_disagreement` | 同一報告的摘錄存了不同的 `text_sha256` |
| （取樣） | `norm_drift` | `content_norm` 是 GENERATED，驗「庫裡實際存的值」與 `norm_for_match()` 是否等價 |

`norm_drift` 與 `tests/test_content_norm_equivalence.py` 的分工：**測試驗「表達式定義與 Python 等價」，稽核驗「庫裡實際存的值等價」**——定義正確但既有列是舊定義算出來的，只有後者看得見。它取樣（`--norm-sample`，預設 500 列）而不全掃，因為這種漂移是全域性的。

**`durability_off` 是這支存在的最大理由。** `scripts/ingest_lowio.sh` 會 `ALTER SYSTEM SET fsync=off` 降 I/O，並以 `trap ... EXIT` 還原——**但 trap 擋不住 SIGKILL**（OOM killer、`kill -9`、WSL 整個被收掉），而 `ALTER SYSTEM` 寫的是 pgdata 裡的 `postgresql.auto.conf`，**重啟也不會恢復**。DB 於是無限期跑在 `fsync=off`：查詢完全正常、零症狀，但一次斷電就可能讓整個 pgdata 報廢。處置是 `make restore-durability`。

### 怎麼跑

```bash
make db-audit                                          # 手動跑一次
systemctl list-timers report-mark-audit.timer          # 排程：每日 08:45（Persistent=true）
uv run python scripts/db_audit.py --json
uv run python scripts/db_audit.py --skip norm_drift    # 跳過取樣那條（最慢）
```

退出碼：`0`＝乾淨／`1`＝有發現／`2`＝DB 不可用。**1 與 2 刻意分流**：混成同一個碼等於把「DB 掛了」誤導成「資料壞了」。

### 三個刻意的設計

- **只讀，一列都不改。** 修法幾乎都需要人決定（孤兒該刪還是補回連結？重複 chunk 刪哪一列？）。輸出給人看，處置由人下。
- **error / warn 都算失敗（同樣 rc=1）。** 分級只影響閱讀順序；「warn 不算失敗」會讓 warn 區永遠有東西、從此無人閱讀。
- **走 `db.relax_statement_timeout()`（`SET LOCAL`）。** 其中幾條是全表掃描，引擎層的 60 秒 `statement_timeout` 會把它們砍掉——被砍掉的稽核等於沒有稽核。

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

它與 freshness 一樣沿用 `/etc/default/report-mark-sync`。08:45 刻意排在 freshness（08:30）之後、錯開 15 分鐘（兩支都會對 `report_chunk` 跑全表掃描），也排在上班時段的開頭——產物是「一筆 unit 失敗紀錄」，要有人當天看到才有用。`Persistent=true` 對這支特別重要：最可能留下 `fsync=off` 的情境（機器被硬收掉）恰好就是它會被錯過的那一天。

## LLM（DeepSeek）：金鑰、批次失敗與帳號告警

LLM 的主要後端是 DeepSeek：402、401、DeepSeek 停機時，問答與 sync 的 LLM 段（行內標註、摘要、標題、摘錄、訊號、簡報）全部停擺，直到儲值、換金鑰或服務恢復；檢索、閱讀頁、雷達、既有簡報的讀取不受影響。402 等不及儲值時，可以**手動**切到 OpenCode Go（同一顆模型、不同的送達路徑；見「切換到 OpenCode Go」），沒有自動 failover。`LLM_PROVIDER` 預設 `deepseek`（未設、空值都是），另一個合法值是 `opencode_go`；`claude_cli`／`claude_only` 是退役值，行為見「刻意讓 LLM 停下來」。

### DeepSeek 金鑰落點與輪替

金鑰有**兩份、逐字相同**：

| 落點 | 讀者 | 權限 |
|---|---|---|
| repo 根 `.env` 的 `DEEPSEEK_API_KEY` | web（`web/server.py` 啟動時載入） | 同其他 web secret |
| `/etc/default/report-mark-llm` | `report-mark-sync.service` 與 `report-mark-upload.service`（`EnvironmentFile=-`，排在共用檔之後）；手動跑的 LLM 批次與評測由 `scripts/_llm_env.py` 自己讀 | 0640 root:kashionz（群組＝執行 sync 與手動批次的使用者；EC2 換成該主機的部署使用者） |

**只有白名單上的 unit 載入 llm 檔**（sync 與上傳 worker），其他 unit 都不呼叫 LLM——環境變數裡有金鑰的行程越少越好（`tests/test_deploy_units.py` 釘住白名單）。批次的模型旋鈕（`TAG_MODEL`、`SUMMARY_MODEL` 等）與 `LLM_PROVIDER` 也放這份檔，讓手動與排程用同一組設定；共用檔 `/etc/default/report-mark-sync` 不放任何 LLM 鍵（同一支測試釘住）。web 讀的是 `.env` 的同名鍵，兩份的模型旋鈕可以不同。

批次經 `scripts/_claude_cli.run_claude`（名稱是歷史值；`generate_brief` 也交給它）只呼叫 DeepSeek：模型名只接受白名單（`deepseek-flash`、`deepseek-v4-pro`、`deepseek-v4-flash`），其他名稱（含 `claude-*`）在預檢與呼叫層都以整批 **rc=2** 中止、不送出。換模型＝改這份檔的個別旋鈕，下一輪 sync 生效。遇到 401／402／模型不存在一律整批 **rc=2** 中止（不記跳過名單），處置見「DeepSeek 帳號告警與 402／401 處置」與「整批中止後的重放」。

安裝（範例檔檔頭有同樣的指令）：

```bash
sudo install -m 0640 -o root -g kashionz deploy/systemd/report-mark-llm.env.example /etc/default/report-mark-llm
sudoedit /etc/default/report-mark-llm      # 填 DEEPSEEK_API_KEY=；不要 echo／tee，也不要 source 這個檔
```

`scripts/_llm_env.py` 的行為：入口檔在第一個專案 import 之前載入這份檔（只補環境裡還不存在的鍵），並在取批次鎖之前預檢。下列情況一律 **rc=2** 並說出原因，sync 殼把 rc=2 當帳號／環境型中止告警：

- 有白名單模型卻沒金鑰（環境裡有空值要先 `unset DEEPSEEK_API_KEY`；PermissionError 要以有該群組權限的使用者執行）。這份檔不存在時也是這一條：批次不讀 repo 根 `.env`，拿不到金鑰。
- 有不在白名單的模型名（含 `claude-*`）。
- `LLM_PROVIDER` 非空卻不是 `deepseek`（退役值或拼錯），訊息印出原始值。
- 檔內有重複的鍵。**重複鍵特別危險**：systemd 取最後一行、手動批次取第一行，輪替時新舊兩行並存會讓兩條路徑用不同的金鑰。

通過時印 `fp=<金鑰 sha256 前 8 碼>`，不印金鑰本身。

輪替（不需要 `daemon-reload`，`EnvironmentFile` 每次啟動重讀）：

1. 在 DeepSeek 主控台發一把新金鑰（舊的先別撤）。
2. 改兩份檔的 `DEEPSEEK_API_KEY`：**直接改那一行**，不要新增一行。
3. 重啟 web：`sudo systemctl restart report-mark-web.service`。
4. 核對兩份指紋相同（只印雜湊前 8 碼，不印金鑰）。在 repo 根以讀得到 `/etc/default/report-mark-llm` 的使用者執行：
   ```bash
   uv run python -m scripts._llm_env .env /etc/default/report-mark-llm
   ```
   每份印一行 `fp=<前 8 碼>`，rc=0＝兩份都有值且一致，rc=1＝不一致、缺值、讀不到，或某份檔裡 `DEEPSEEK_API_KEY` 不只一行（重複時另印一行警告、列出每一行的指紋；刪掉多餘的行再核對一次，即使兩行相同也一樣回 rc=1）。**不要改用 `grep | cut | sha256sum`**：程式讀值時會去 `export `、去成對引號、strip（`web/env_loader._parse_line`），手算的雜湊在值帶引號、尾隨空白或 CRLF 時會把兩份其實相同的金鑰判成不同。
5. 確認 sync 目前沒在跑（`systemctl is-active report-mark-sync.service` 回 `inactive`），避免撤銷舊金鑰時打斷進行中的一輪。
6. 撤銷舊金鑰。

### DeepSeek 批次的失敗處置

批次的失敗依 kind 分三類處置；失敗原因一律寫成 `API[<kind>] <固定措辭>：<細節>`（單行、不含 TAB），落在各批次的 `*_failures.log`：

| 類別 | kind | 批次行為 |
|---|---|---|
| 帳號層級 | `auth`（401）、`quota`（402）、`config`（404、模型不存在；白名單外的名稱在送出前就歸這類） | 整批 **rc=2** 中止；不記跳過名單（402 的處置是儲值） |
| 暫時性 | `overloaded`（429、5xx）、`network` | 還沒吐字時傳輸層依 `Retry-After` 退避重試 ≤2 次（受總期限限制）；**已吐字（已計費）就不重試**，直接當單篇失敗；腳本層不再重試；計入斷路器 |
| 單篇 | `content_filter`、`truncated`、`empty`、`bad_request`、`timeout_streamed`、`timeout`、`other` | 這篇這輪只打 1 次；前五種記入 `research.llm_task_failure`（審查與 `truncated` 1 次就跳過，其餘連續 3 輪）；`timeout_streamed` 與 `timeout` 計入斷路器 |

「回應成功但解析失敗」（unparseable）不在上表：腳本層最多 3 次，連續 3 輪才跳過。摘要不接受純文字回應（沒有 JSON 就算解析失敗）。批次的 `timeout`（摘要／標題／摘錄／訊號 180 秒、標註 150 秒、簡報 300 秒）是**涵蓋傳輸層重試的總期限**，每收到一段位元組就檢查，伺服器送 keep-alive 或持續送不換行的位元組都延長不了它。沒吐字就到期是 `timeout`（計入斷路器、不記跳過名單）；**已吐字後才到期是 `timeout_streamed`（期限型截斷），可以重放**：計入斷路器，記入跳過名單但**連續 3 輪**才跳過，行內標註記 `skip_untagged`、階段 `tag`（`failures_to_delta.py` 預設會撈）。它和 `truncated` 刻意分開：`truncated` 只留給 `finish_reason=length`（同一輸入、同一上限重送結果不變，1 次就跳過）；期限型若也歸 `truncated`，DeepSeek 暫時變慢一次就會讓整批研報進跳過名單，而且斷路器不跳。合起來，一篇研報一輪最多 3 個已計費請求（解析失敗的腳本層重試），截斷、期限型截斷與審查只有 1 個。

**400 升級**：一般的 400（`bad_request`）算單篇失敗。**只有**同一個批次行程裡 ≥2 篇不同研報收到相同的 400 訊息（比對前正規化：小寫、數字換 `#`、長 hex／request id 換 `<id>`；上下文長度、輸入過長這類本質上是單篇輸入的訊息不參與升級），**而且本行程內還沒有任何一次 DeepSeek 呼叫成功過**，才判定是請求或設定壞了、升級成 `API[config]` 整批 **rc=2** 中止；中止前先把觸發的那幾篇以 `bad_request` 記入 `research.llm_task_failure`（完整 file_hash 印在 log 裡），計數直接記到 3（`SKIP_AFTER_ROUNDS`），**下一輪就跳過**，不會連續 3 輪整段 rc=2。**修好之後，這幾篇要對該批次加 `--retry-blocked`（或 DELETE 那幾列）才會再打**；`make llm-blocked` 列得出來。刻意沒有「第一個請求就 400 就升級」：那篇若排在最前面，每一輪都會中止整批、而中止不記跳過名單，它永遠不會被跳過。匯入段另把觸發研報寫成 `data/sync_bad_request_<時間>.txt`（`file_hash<TAB>路徑`），殼印出內容；**重放本輪 delta 之前先把這些路徑從 delta 拿掉**，否則會再撞一次。處置：看訊息判斷是程式（請求格式）還是設定問題，修好後照「整批中止後的重放」補跑。

**行內標註被內容審查擋下（`skip_blocked`）**：只列清單、交人工，沒有自動入庫路徑。這類研報不入庫、計入 `skip_blocked`（異常，擋心跳），記進 `research.llm_task_failure`（task=tag、reason=content_filter），並在 `data/sync_failures.log` 留一行階段為 `tag_blocked` 的紀錄（原因欄帶 `file_hash=`）。`failures_to_delta.py` 預設**不撈** `tag_blocked`：同一份輸入再送一次結果不會變。人工處理：

1. `make llm-blocked` 列出（task=`tag`、原因 `content_filter`；還沒入庫所以檔名欄是 `-`），用 file_hash 回查路徑：`grep <file_hash> data/sync_failures.log`。
2. 判斷要不要收。要收的話，照 `tag_all_cli.py` 的格式手寫 `data/tags/<file_hash>.json`（`market`、`is_research`、`confidence`、`instrument_types`、`relates_stock`、`relates_futures`、`stock_targets`、`futures_targets`）；匯入時 `load_tag` 讀得到就不會再呼叫 LLM。
3. 單篇重放：`uv run python scripts/failures_to_delta.py --stage tag_blocked --out data/sync_delta_blocked.txt`（或手寫只含那一行相對路徑的 delta），再 `uv run python scripts/sync_new_reports.py --delta data/sync_delta_blocked.txt --hashes-out data/sync_hashes_retained_blocked.txt`，接著用同一份 hashes 補跑摘要、標題、摘錄（`--hashes-file`）。那幾段若也被審查擋下，同樣記進跳過名單，不影響入庫。
4. 不收的話不用做什麼：下一輪 delta 不會再列出它（rsync `--size-only`），`llm_task_failure` 那一列留著作紀錄，要清就 DELETE。

**行內標註被截斷（`skip_truncated`）**：DeepSeek 回 `truncated`（`finish_reason=length`；`timeout_streamed` **不算**，它記 `skip_untagged`、可重放）的標註同上處置：不入庫、計入 `skip_truncated`（異常，擋心跳），記進 `research.llm_task_failure`（reason=truncated），`data/sync_failures.log` 的階段是 `tag_truncated`，`failures_to_delta.py` 預設**不撈**。處置：看是不是 `TAG_MAX_TOKENS`（`scripts/sync_new_reports.py`）不夠——調高後以 `--stage tag_truncated` 取出單篇重放；或照上面第 2 步手寫 tags。空回應與一般 400 **刻意仍記 `skip_untagged`**（階段 `tag`，補救指令會重送）：空回應多半是供應商端偶發；400 在送出時就被拒、不產生輸出，重送幾乎不花錢。

**簡報被內容審查擋下（或輸出被 `max_tokens` 截斷）**：該次跳過、不寫列，`generate_brief.py` 以 rc=1 收場（排程殼記進 `unit_failures.log`），原因寫進 `data/brief_failures.log`（`時間<TAB>簡報日期<TAB>原因<TAB>model`）與 sync log。**同一天、同一個 model 之後的輪次不再呼叫**（素材是上一次的超集，幾乎一定再擋），印「今日已被模型供應商的內容審查擋過，略過」、rc 仍是 1。隔天以新的窗期再試；換 model 或加 `--force` 會當天重打。`finish_reason=length` 的截斷（`API[truncated]`，撞到 `generate_brief.py` 的 `MAX_TOKENS`）同一套處置；期限型截斷（`API[timeout_streamed]`）與逾時**不算**，下一輪照打。

**用量記錄**：批次每次 LLM 呼叫在 `data/llm_usage.jsonl` 追加一行 JSON（`task`、`file_hash`、`report_id`、`backend`、`model_req`／`model_resp`、`prompt_sha256`、`tokens{hit,miss,completion,reasoning}`、`finish_reason`、`kind`、`attempts`、`ttft_ms`、`total_ms`；較舊的列可能是 `backend=cli`、`tokens` 為 null）。摘要、標題、標籤的產出模型靠它以 `file_hash` 回溯；費用真值看 DeepSeek 餘額差分，這份只拿來歸因。寫不進去不影響批次。檔案只增不減，要清就整份搬走。

**斷路器**：同一個批次行程裡最近 10 次 DeepSeek 呼叫有 ≥5 次逾時／過載／連線失敗，該段以 **rc=2** 中止，並寫 `data/.llm_breaker`。標記綁定 sync 輪次：`sync_new_reports.sh` 每輪 export `SYNC_ROUND_ID`，標記帶 `round=`；**同一輪**其餘會用到 DeepSeek 的段在預檢就 rc=2 拒跑（不看時間，一輪可超過 2.5 小時），**下一輪不受影響**（屆時若仍過載，斷路器會再跳一次）。手動執行（沒有輪次 id）或手動執行寫的標記，照 30 分鐘有效期。處置：看 DeepSeek 狀態頁與 sync log；恢復後 `rm data/.llm_breaker`（或等它失效），再依「整批中止後的重放」補跑。

### DeepSeek 帳號告警與 402／401 處置

**負責人**：儲值、DeepSeek 帳號持有、金鑰輪替都由 **Kashionz 一人**負責，不設代理人。帳戶以人民幣（**CNY**）儲值。

**儲值紀律**：告警門檻 **¥70**（約兩週用量，`LLM_BALANCE_FLOOR`）；月上限 **¥350**——每月儲值總額與儲值後的餘額都 ≤ ¥350。月上限是人的紀律，不是程式旋鈕，沒有任何東西會替你擋。

`/healthz/llm` 回 503 時問答就是停擺的（`low` 除外：那是還能用、要儲值）。

#### 刻意讓 LLM 停下來

**沒有**「一個旋鈕停掉全部 LLM」。`LLM_PROVIDER` 的退役值（`claude_cli`／`claude_only`）不是停止開關，而且效果依寫在哪份檔而不同：

| 退役值寫在哪 | web（問答） | 批次（sync） |
|---|---|---|
| repo 根 `.env` | 記 ERROR、當成 `deepseek`，**照常計費** | **照常計費**：批次不讀 `.env`，只讀 `/etc/default/report-mark-llm` |
| `/etc/default/report-mark-llm` | 不受影響（web 不讀這份檔），照常計費 | 預檢 **rc=2** 整批拒跑（sync 告警） |

要停，照對象分開做：

- **停問答主答**：repo 根 `.env` 設 `ASK_ANSWER_MODEL=off`（任何白名單外的名稱都行）並重啟 web。每題回「問答服務暫時無法使用（模型設定有誤）」、不送出主答請求；`/healthz/llm` 回 503 `misconfigured` → 探針退出碼 8 → **CRITICAL 事件（reason 是 `llm_misconfigured`）——刻意停用期間這是預期的**，恢復設定後送 RESOLVED。其餘 fail-open 的線上任務（首輪路由、續問改寫、查詢規劃、追問建議、忠實度抽查）各自解析模型、照常呼叫；要一個都不送，把它們的旋鈕（`ASK_INTENT_MODEL`、`ASK_CONDENSE_MODEL`、`QA_PLANNER_MODEL`、`ASK_FOLLOWUP_MODEL`、`FAITHFULNESS_MODEL`）也設成白名單外名稱（忠實度也可以 `ASK_FAITHFULNESS_ENABLED=0`），或直接把 `.env` 的 `DEEPSEEK_API_KEY=` 清空——所有線上呼叫在送出前就以 auth 失敗，`/healthz/llm` 回 503 `auth_failed`（同樣是退出碼 8、預期的）。
- **停批次**：`sudo systemctl stop report-mark-sync.timer`（要跨重開機再 `disable`）；排程批次裡會呼叫 LLM 的只有 sync 鏈，以及有裝的話上傳 worker（`report-mark-upload.timer`，同樣停掉）。停著期間 `make freshness` 會陸續報各段過期，這也是預期的。恢復時 `start`（或 `enable --now`）。

#### 訊號從哪裡來

| 訊號 | 管道 | 多快 |
|---|---|---|
| web 的 `/healthz/llm` 回 503 `low` | 探針退出碼 **7** → P5 的 `probe_exit_7`（**WARNING**，「DeepSeek 餘額低於門檻（尚未停擺），請於 3 個工作天內儲值」）→ Slack FIRING，每 30 分鐘提醒，恢復送 RESOLVED | 餘額查詢 ok 快取 600 秒，約 12 分鐘內 |
| web 的 `/healthz/llm` 回 503 `exhausted`／`auth_failed`／`unreachable`／`indeterminate`／`misconfigured` | 探針退出碼 **8** → `probe_exit_8`（**CRITICAL**，「LLM 帳號不可用（餘額用罄／認證失敗／連不上），問答與批次 LLM 段停擺；檢索、閱讀、雷達正常」）→ Slack FIRING；已在 7 的 FIRING 期間轉成 8 時立刻送 **ESCALATED**；8 → 7（儲值了但仍低於門檻）事件不關、降回 WARNING、不另通知；恢復送 RESOLVED | 問答碰到 402 的下一輪探針（約 2 分鐘）；否則同上 |
| 批次碰到 401／402 | 該段整批 rc=2 → sync unit failed → `OnFailure` → Slack | 當輪 |

`/healthz/llm` 只回 `{"llm": state}`，**不回任何金額**；金額與原因在 web 日誌的「healthz LLM 狀態」那一行（狀態改變時記一次）。判定細節（只讀 CNY 那一筆、缺 CNY 或出現非零 USD 視為判斷不出來、402 閂鎖、連續 2 次才算連不上）在 `app/services/llm_health.py` 的模組 docstring。

- **已知限制：web 重啟可能閃一次 RESOLVED。** 狀態只在 web 行程記憶體裡；重啟後第一次餘額查詢若剛好單次連不上，仍回 200 `unknown`（連續 2 次才算 unreachable），進行中的 LLM 事件會收到一則 RESOLVED，下一輪若仍連不上再 FIRING。402／401／餘額 ≤ 0 不受影響。刻意不改：把重啟後的第一次失敗當 503，每次重啟碰上單次抖動都會開假事件。
- **已知延遲**：批次行程的 402 web 看不到，要等 web 自己的餘額查詢快取到期（最長 600 秒）；那一側由 sync 的 `OnFailure` 即時告警。

```bash
curl -s http://127.0.0.1:8097/healthz/llm          # {"llm":"ok"}；只回答本機直連
journalctl -u report-mark-web.service --since "1 hour ago" | grep "healthz LLM 狀態"   # 原因與金額
journalctl -u report-mark-health.service -n 5 -o cat    # reason=llm_<state>（多項故障時逗號分隔）
```

#### 處置

P5 對退出碼 8 只有一則措辭（「LLM 帳號不可用…」），是哪一種看 journal 的 `reason=llm_<state>`。

| state | 意義 | 處置 |
|---|---|---|
| `low` | CNY 餘額 > 0 但低於 ¥70：還能用 | 依儲值紀律儲值（儲值後餘額 ≤ ¥350）。事件在下一次查詢（失敗快取 60 秒）回 ok 後 RESOLVED |
| `exhausted` | 402、`is_available=false` 或 CNY ≤ 0 | 儲值 → 確認 `/healthz/llm` 回 ok → 依下方步驟重放 |
| `auth_failed` | 401，或 web 線上任務走 DeepSeek 卻沒有金鑰 | 看是不是金鑰被撤、貼錯或兩份不一致：依「DeepSeek 金鑰落點與輪替」核對指紋，必要時輪替 |
| `unreachable` | 連續 2 次連不上 DeepSeek（網路、逾時、429／5xx） | 看對外網路與 DeepSeek 狀態頁；恢復後自動 RESOLVED |
| `indeterminate` | 判斷不出來：缺 CNY 那一筆、USD 出現非零餘額（幣別不符）、回應格式變了、端點設定錯 | 到 DeepSeek 後台看帳戶實際幣別與餘額。帳戶若真的改以美元計價，改 repo 根 `.env` 的 `LLM_BUDGET_CURRENCY` 並重訂門檻（重啟 web）；否則是 API 改版或 `DEEPSEEK_BASE_URL` 設錯 |
| `misconfigured` | 問答主答（`ASK_ANSWER_MODEL`）解析到白名單外的名稱（`claude-*`、打錯字）：每題回「設定有誤」。不是帳號問題，這時不查餘額 | 刻意停用就是預期的，不處置。否則改正 repo 根 `.env` 的 `ASK_ANSWER_MODEL`（或刪掉那一行用預設）並重啟 web；web 日誌的「healthz LLM 狀態」與啟動自檢都會說出是哪個值 |

402／401 之後的完整步驟：

1. 排除原因（儲值，或換金鑰並核對兩份指紋）。等不及儲值的 402，改照「切換到 OpenCode Go」切過去，再從第 2 步接著做。
2. 確認 `curl -s http://127.0.0.1:8097/healthz/llm` 回 `{"llm":"ok"}`。web 自己的請求收過 402 時，要等一次在那之後開始的成功餘額查詢才會解除（最多 60 秒）；急的話重啟 web 也會清掉。
3. 停排程：`sudo systemctl stop report-mark-sync.timer`。
4. 依「整批中止後的重放」逐份重放 sync 殼保留的 delta（`--delta … --hashes-out …`），每份接著用它自己的 hashes 補跑摘要、標題、摘錄（`--hashes-file`）；下游中止保留的 `data/sync_hashes_retained_<時間>.txt` 同樣補跑。**不能**用 `failures_to_delta.py` 或 `--all-local`（整批中止不留逐篇失敗紀錄）。
5. 缺的簡報日子補 `uv run python scripts/generate_brief.py --date YYYY-MM-DD`。
6. 重新啟用排程：`sudo systemctl start report-mark-sync.timer`。

### 切換到 OpenCode Go（402 時的手動備援）

`LLM_PROVIDER=opencode_go` 讓問答與批次的同一顆模型（`deepseek-flash`＝V4.1-Flash）改經 OpenCode Go 訂閱的 OpenAI 相容端點送出。**換的是送達路徑、不是模型**：任務旋鈕、白名單、預設表、judge 系譜都照舊寫 DeepSeek 名稱，送出時才翻成 Go 的模型 ID（`app/services/llm_models.py` 的 `GO_MODEL_IDS`）。實作的差異全在 `app/services/llm_http.py`（模組 docstring 的 provider 那一條）。

**風險與限制（切之前要接受的）**：

- 條款：OpenCode Go 是給 coding agent 用的個人訂閱（單人 workspace），官方要求流量「typical coding agent traffic」並監控濫用。拿它跑研報平台的批次與多人問答不符合這個前提，有被限制或停權的風險，所以只當短期備援，DeepSeek 儲值後就切回來。
- 資料：Go 上 DeepSeek 模型的零保留協議逐月續約，切之前到 OpenCode Go 文件的 Privacy 表確認還在有效期內；研報全文會多經過 OpenCode 這個中間商。
- 額度：每個模型每 5 小時最多用掉月額度的 20%、每週 50%、每月 100%。批次一次衝量容易撞到 5 小時上限。
- 監控：Go 沒有餘額端點，`/healthz/llm` 改打 `/models`，只能判斷端點與金鑰能不能用，`low` 不會出現。額度用完要等真實請求被拒才知道：批次整批 rc=2（`API[quota]`，提示「Go 額度用完」）；問答的請求被拒時 `/healthz/llm` 翻成 `exhausted`，要等之後有一次請求成功才解除（`app/services/llm_health.py` 的「OpenCode Go」一節）。Go 用量上限的錯誤格式沒有文件，`llm_http._GO_QUOTA_RE` 是依訊息文字的保守猜測：第一次真的撞到上限時，照 web 日誌或 `data/llm_usage.jsonl` 的 detail 修正。
- 費用歸因：`data/llm_usage.jsonl` 每列與 web 的 `llm_call` 日誌都有 `provider` 欄位；沒有這個欄位的舊列是 DeepSeek。

**前置（一次性）**：在 OpenCode console 訂閱 Go、建立 API key；workspace 的 Regions 要選 **Global**（DeepSeek 模型預設不開放）。

**切過去**（每台主機各做一次；正式環境 EC2 的環境檔路徑見 `deploy/aws/README.md`）：

1. 兩份環境檔各加兩行，**值逐字相同**：repo 根 `.env`（web）與 `/etc/default/report-mark-llm`（批次與上傳 worker；`sudoedit`，不要 echo／tee）。

   ```
   LLM_PROVIDER=opencode_go
   OPENCODE_GO_API_KEY=<Go 的金鑰>
   ```

   `DEEPSEEK_API_KEY` 不用刪，切回來時直接用。
2. 核對兩份一致：`uv run python -m scripts._llm_env .env /etc/default/report-mark-llm`。它會逐份印出兩個金鑰的指紋與 `LLM_PROVIDER`；兩份的 provider 不同（web 走 Go、批次走 DeepSeek，或反過來）一樣回 rc=1。
3. 重啟 web：`sudo systemctl restart report-mark-web.service`。啟動日誌那行「LLM 模型：provider=opencode_go」就是生效了。
4. 確認 `curl -s http://127.0.0.1:8097/healthz/llm` 回 `{"llm":"ok"}`，問一題確認問答正常。
5. 照「402／401 之後的完整步驟」的第 3 步起，重放停擺期間保留的 delta。批次每輪重新讀環境檔，不需要另外重啟。

**切回 DeepSeek**（儲值後）：兩份環境檔的 `LLM_PROVIDER` 改回 `deepseek`（或刪掉那一行），`OPENCODE_GO_API_KEY` 可以留著；再核對（第 2 步）、重啟 web、確認 `/healthz/llm` 回 ok。切回之後的請求照常按量計費。

### 忠實度 judge

線上忠實度 judge（`FAITHFULNESS_MODEL`）與離線評測 judge（`EVAL_JUDGE_MODEL`）預設都是 `deepseek-flash`，與先前的 haiku 分數是兩把不同的尺（系譜 `deepseek-2026-09`）；門檻 `FAITHFULNESS_MIN` 0.9 與評測的 F>0.9／CP>0.8／AR>0.55 數值不變。讀分數一律經 `app/services/judge_schema.py`，舊尺的分數只計入已查核數、不進 fail-open／低於門檻／平均，待複核佇列也只列現行尺的低分。

確認抽查有在跑：問一題含數字的問題，等背景抽查跑完（數十秒內）後核對（辦公室主機的寫法）：

```bash
docker exec -i report-mark-postgres psql -U postgres -d research -c "SELECT created_at, evaluation->>'judge_model', evaluation->>'judge_fingerprint',
  evaluation->>'degraded', evaluation->>'degraded_reason', evaluation->>'elapsed_ms'
  FROM research.qa_log WHERE evaluation IS NOT NULL ORDER BY created_at DESC LIMIT 3"
```

`judge_model` 應為 `deepseek-flash`、`judge_fingerprint` 有值、`degraded` 為 false；web 日誌的 `llm_call task=faithfulness` 行帶 `fp=`。repo 根 `.env` 不該有 `FAITHFULNESS_MODEL=claude-…`（白名單外的 judge 不送出，每次抽查記一筆 degraded(account)），`ASK_FAITHFULNESS_TIMEOUT` 不設時是 90 秒。

**`degraded_reason` 的意義**：`content_risk`（供應商內容審查拒答，不重試）、`account`（401／402／404：金鑰、餘額、模型設定；告警走 `/healthz/llm`，這裡只是抽查沒量到）、`truncated`（`finish_reason=length`；剩餘期限夠時已用 2 倍上限重試過）、`timeout`（每次 judge 呼叫的總期限 `ASK_FAITHFULNESS_TIMEOUT` 到了，或伺服器 60 秒沒回任何位元組）、`error`（adapter 漏出的意外例外，看 web 日誌的 traceback）。每個階段最多 3 個請求，一次抽查最多 6 個。

**judge 有系統性問題時**：沒有別的後端可換。暫停抽查（repo 根 `.env` 設 `ASK_FAITHFULNESS_ENABLED=0` 並重啟 web，或在管理後台以功能旗標 `qa.faithfulness` 覆寫關閉），或把 `FAITHFULNESS_MODEL` 換成 `deepseek-v4-pro`——那又是另一把尺，監控卡會把 flash 的分數歸「其他 judge」。

**描述性校準**（不是閘門，結果只給人看）：`uv run python scripts/judge_agreement.py --dry-run` 先看取樣數與估價，再正式跑。它拿 `qa_log` 裡歷史的 haiku 判定當參考，印 κ、平均偏移、門檻翻轉率；脈絡是重建的，差異同時來自 judge 與脈絡。離峰跑（會在本行程載入 BGE-M3）；預設值與取樣規則見該腳本的 docstring。

## oneshot 的手動驗證：`Result=success` 不是證據

`systemctl start <oneshot>` 之後看到 `Result=success`、`ExecMainStatus=0` 不能證明它這次真的跑了：

1. 這兩個值既是**上一次**執行留下的值，也是**從未執行過**的 unit 的預設值——兩者無法區分。
2. oneshot 在沒有其他 unit 引用時會被 systemd 回收（`CollectMode=inactive`），回收後 `systemctl show` 讀到的是重新載入的乾淨狀態。

所以判準只能是「相對於一個**事前基線**的前進」：

```bash
BASE=$(bash scripts/verify_oneshot_ran.sh baseline report-mark-freshness.service)
sudo systemctl start report-mark-freshness.service
bash scripts/verify_oneshot_ran.sh verify report-mark-freshness.service "$BASE"
```

rc `0`＝`EXECUTION_PROVEN`／`1`＝**`EXECUTION_NOT_PROVEN`**／`2`＝用法或 token 格式錯誤。必要條件是 `ExecMainStartTimestampMonotonic` 前進，再加至少一項佐證（journal 行數成長或 `ExecMainExitTimestampMonotonic` 前進）；journal 成長單獨不足（它可能因為 timer 的訊息而長）。下文各節的「首跑與驗收」都以這支為準。

## 監控 spool 與匯入（report-mark-load-observations）

管理頁的排程工作與主機資源讀 DB 的 `research.service_observation`／`research.job_execution`，
但**觀測的第一落點不是 DB**：DB 掛掉的那段時間正是最需要觀測的時候。所以分兩段——

- `report-mark-metrics.service` 的收集器（`scripts/collect_resource_usage.py`，系統 python、不需 venv）每 60 秒
  把 Host（CPU、記憶體、磁碟、I/O PSI、load）、Service Catalog（`deploy/ops/services.prod.toml`）列的容器與
  systemd unit 狀態、有 timer 的 oneshot 每次執行寫進本機 spool `data/ops_spool/`（格式在收集器檔頭）。它不連 DB。
- `report-mark-load-observations.timer` 每 5 分鐘以 `scripts/load_observations.py` 冪等匯入（自然鍵去重，進度只在
  commit 後前進）。DB 不可用 rc=2、spool 原封不動，恢復後下一輪補匯入；匯入完的舊日檔才刪。

批次「跑過」的判準與 `scripts/verify_oneshot_ran.sh` 相同（要有 InvocationID 與 ExecMainStartTimestamp，`Result=success`
不是證據）；沒看到結束、同 unit 已有更晚開始的那一次記成 `lost`（結果不明，不捏造結束時間）。兩張表遺失只少了歷史，
不影響服務與告警，刻意不備份。這一段**不碰 P5**：告警照舊只有 `scripts/incident_handler.sh`，不經 DB。

### 安裝（人工，需 sudo；只在要啟用時做）

```bash
# 前提：schema 已在 head（make schema-version 回 0）；收集器 report-mark-metrics.service 已安裝並在跑
#       （安裝見 docs/CAPACITY.md；spool data/ops_spool/ 由它寫）
# 1) 匯入器
sudo install -m 0644 deploy/systemd/report-mark-load-observations.service deploy/systemd/report-mark-load-observations.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now report-mark-load-observations.timer
# 2) 維運代理的 catalog 要列 load-observations：照「維運代理」的步驟 3 重新安裝 catalog、--check，再重啟代理
```

驗收：`ls data/ops_spool/` 一分鐘內出現 `observations-<今天>.jsonl`；`uv run python scripts/load_observations.py --dry-run`
印出待匯入筆數；timer 跑過一輪後 `systemctl show report-mark-load-observations -p Result,ExecMainStatus`，並以管理員
打 `/api/admin/observations?scope=host` 與 `/api/admin/jobs`。沒裝收集器（`report-mark-metrics`）的主機
不會產生 spool，匯入器每輪只是「沒有東西可匯入」。

管理後台以維運代理的服務清單有沒有 `load-observations` 判斷這台有沒有監控匯入：沒有時「主機」「排程工作」「事件」三頁
不列在導覽裡，也不查事件（`frontend/src/features/admin/adminCapabilities.ts`）。

### 停用

```bash
sudo systemctl disable --now report-mark-load-observations.timer   # 管理頁的監控資料停在最後一次匯入
```

收集器仍會寫 spool（保留期安全網 14 天，`--spool-retention-days`）；要連 spool 都停，在 metrics unit 的 ExecStart
加 `--observe-interval 0` 後重啟。

## 事件投影與容器／主機探針

管理頁的「事件」讀 DB 的 `research.incident`／`research.incident_event`，但**它們只是 projection**：
事件的去重、提醒、FIRING／RESOLVED 判定與 Slack 投遞仍然只有 `scripts/incident_handler.sh`（P5），它不碰 DB、不依賴 web。P5 是主要的 incident state machine／狀態型告警路徑，但不是唯一的 Slack 發送者：unit failure（`report-mark-alert@`，OnFailure）與上傳 worker 的感染通知另有路徑；安全告警與來源主機信標各是一個 P5 實例，不另設直接的 Slack sender。

- P5 每次已落地的狀態轉換（FIRING、REMINDER、ESCALATED、RESOLVED）另寫一行本機 spool
  `data/ops_spool/incidents-YYYYMMDD.jsonl`；FIRING 附事件前 10 分鐘、RESOLVED 附開場後 10 分鐘的 journal 片段
  （`data/ops_spool/journal/*.log`，預設 64 KiB、硬上限 256 KiB；完整 log 仍以 journald 為準）。寫入在背景、輸出丟棄、
  失敗吞掉：spool 壞掉只少那一筆，告警行為與退出碼不變（`tests/test_incident_handler.py` 的 SpoolFailureEquivalenceTests）。
- `report-mark-load-observations.timer` 順便把它們冪等匯入（event_id 去重；片段遮祕密後入庫、commit 後才刪片段檔）。
  DB 掛掉期間的事件恢復後補匯入。沒收到 RESOLVED、同元件已有更晚的事件時記成 `lost`（結束時間不明）。
- 兩張表是事故歷史（spool 匯入後即刪、journald 有保留期），列入備份。

容器與主機兩支探針各接一個 P5 實例（只換 `INCIDENT_*`）：

| 探針 | 看什麼 | tier → 去抖 | 退出碼 |
|---|---|---|---|
| `scripts/check_container_health.sh` | catalog 的 PostgreSQL、nginx、cloudflared、ClamAV 是否 running（不是 unhealthy） | 前三者在 catalog 是 critical：單次執行內重試一次（間隔 15 秒）後確認就回 1；ClamAV 是 supporting（連續 3 輪才回 9，見「ClamAV（上傳掃描）」） | 0／1 CRITICAL／3 確認期／4 判不出來／9 WARNING |
| `scripts/check_host_health.sh` | 磁碟使用率 ≥ 90%（important，連續 2 輪）、MemAvailable < 5%、memory／io PSI full avg60 ≥ 10／30（supporting，連續 3 輪） | 門檻與理由在探針檔頭（保守初值） | 0／3／4／9 |

**去抖做在探針、不在 P5**：探針記每個檢查項目的連續失敗次數（`data/.health-streaks/`），還在確認期回 3，
P5 那兩組設 `INCIDENT_HOLD_EXIT_CODES=3`（不開也不關）。判不出來（docker 連不上、/proc 讀不到）連續 2 輪才回 4；
狀態目錄寫不進去時每筆失敗都算確認（寧可多吵也不靜默）。門檻與確認次數可在 `/etc/default/report-mark-sync`
覆寫（`HOST_DISK_MAX_PCT`、`HOST_MEM_MIN_AVAIL_PCT`、`HOST_PSI_MEMORY_FULL_MAX`、`HOST_PSI_IO_FULL_MAX`、
`HEALTH_CONFIRM_IMPORTANT`／`_SUPPORTING`／`_TOOLING`、`CONTAINER_HEALTH_TARGETS`）。

### 安裝（人工，需 sudo；只在要啟用時做）

```bash
# 前提：schema 已在 head、web 那組 P5（report-mark-incident）與監控匯入（report-mark-load-observations）已安裝
# 1) 容器與主機的探針與 P5 實例
sudo install -m 0644 deploy/systemd/report-mark-container-health.{service,timer} \
    deploy/systemd/report-mark-container-incident.{service,timer} \
    deploy/systemd/report-mark-host-health.{service,timer} \
    deploy/systemd/report-mark-host-incident.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now report-mark-container-health.timer report-mark-host-health.timer
sudo systemctl enable --now report-mark-container-incident.timer report-mark-host-incident.timer
# 2) 維運代理的 catalog 要列這四項：照「維運代理」的步驟 3 重新安裝 catalog、--check，再重啟代理
```

P5 與探針都以 `kashionz` 執行；journal 片段要讀系統 unit 的 journal，`kashionz` 必須在 `adm` 或
`systemd-journal` 群組（`id kashionz` 確認；不在時片段是空的，事件照常）。

驗收：`sudo systemctl start report-mark-container-health.service report-mark-host-health.service` 後
`systemctl show report-mark-container-health -p ExecMainStatus` 應為 0（3＝確認期）、`journalctl -u report-mark-container-health -n 1`
看得到 `status=ok`；P5 實例跑過一輪後 `journalctl -u report-mark-container-incident -n 2` 是 `action=noop`。下一次真的
有事件時 `ls data/ops_spool/incidents-*.jsonl` 出現一行、loader 跑過後管理頁「維運 → 事件」看得到。

### 停用

```bash
sudo systemctl disable --now report-mark-container-incident.timer report-mark-host-incident.timer
sudo systemctl disable --now report-mark-container-health.timer report-mark-host-health.timer
rm -rf data/.incidents-container data/.incidents-host data/.health-streaks   # 可選
```

停掉探針 timer 而不停 P5 實例，P5 會照設計回報 MONITOR_BLIND——要停就兩個一起停。事件投影本身沒有開關：
不想要 spool 時把 P5 unit 的 `INCIDENT_SPOOL_DIR` 設成不可寫的路徑（例如 `/dev/null`），告警不受影響。

## 監控觀測的保留與聚合（report-mark-rollup-observations）

監控歷史保留 90 天、越舊越粗（理由在 `db/migrations/versions/0007_observation_rollup.py` 的註解）：0–24 小時是原始觀測
`research.service_observation`、24 小時–7 天是 5 分鐘桶 `service_observation_5m`、7–90 天是 1 小時桶
`service_observation_1h`，90 天以前連同 `job_execution` 刪除。`report-mark-rollup-observations.timer` 每小時 :20
跑 `scripts/rollup_observations.py`（SQL 在 `app/services/ops_rollup.py`）：

- 每一片（1 小時）以**同一句 SQL** 先刪來源、再把被刪的列聚合後 upsert 進較粗的表，寫入失敗整句回滾、來源原封不動，
  寫入後再核對筆數，不符就 rollback 並 rc=1。重跑同一區間是 no-op（冪等）；遲到的舊觀測合併進既有的桶。
- 每片、每批保留期刪除（每批最多 5000 列）各自一個交易並放寬 statement_timeout（`SET LOCAL`），不會長時間鎖表。
- PostgreSQL advisory lock 防止兩份同時跑（rc=75＝另一份在跑）；DB 不可用 rc=2。unit 接 `OnFailure` 告警，但以
  `SuccessExitStatus=2 75` 放過這兩種（DB 掛掉已由 web 探針經 P5 去重告警；撞鎖是「不跑」不是「跑壞」），
  只有 rc=1（筆數核對不符、SQL 錯誤）會告警。
- **incident／incident_event 不在範圍內**（不可重建的事故歷史，列入備份）。
- 管理頁 `/api/admin/observations` 的範圍上限放寬到 90 天，依 `since` 自動選粒度（24 小時內逐筆、7 天內 5 分鐘、
  更早 1 小時），回應的 `resolution` 標示實際用的。聚合表與原始表一樣不備份。

### 安裝（人工，需 sudo；只在要啟用時做）

```bash
# 前提：schema 已在 head（make schema-version 回 0）
# 1) 先看第一輪要處理多少（唯讀）
uv run python scripts/rollup_observations.py --dry-run
# 2) 批次
sudo install -m 0644 deploy/systemd/report-mark-rollup-observations.service deploy/systemd/report-mark-rollup-observations.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now report-mark-rollup-observations.timer
# 3) 維運代理的 catalog 要列 rollup-observations：照「維運代理」的步驟 3 重新安裝 catalog、--check，再重啟代理
```

驗收：`sudo systemctl start report-mark-rollup-observations.service` 後 `journalctl -u report-mark-rollup-observations -n 3`
看得到「完成 raw→5m slices=…」；`scripts/verify_oneshot_ran.sh` 確認跑過。原始觀測若已累積超過 24 小時，之後
`SELECT min(observed_at) FROM research.service_observation` 應在 25 小時內。積壓很大（`--dry-run` 顯示數百片以上）時
第一輪只搬 500 片、輸出「尚有積壓」，之後每小時繼續。

### 停用

```bash
sudo systemctl disable --now report-mark-rollup-observations.timer   # 原始觀測從此不再聚合、也不再刪除
```

停用後原始表每天約增加 29 萬列；聚合表裡已有的資料不受影響，查詢端照常聯集。

## DB 統計快照與慢查詢（report-mark-db-snapshot、pg_stat_statements）

管理後台「維運 → 資料庫」（`/api/admin/db/*`，`ops.read`）有三塊，前提各不相同：

- **即時快照**（`/api/admin/db/overview`）：只查系統目錄、`SET LOCAL` 收緊 statement_timeout（5 秒）與 lock_timeout
  （1 秒），不需要任何安裝。帳號權限較窄時（RDS 一般帳號、沒有 `pg_monitor`／`pg_read_all_stats`）該段只顯示
  「權限不足」，別人的 session 計入「看不到狀態」，其餘照常。
- **趨勢**（`/api/admin/db/trends`）：要裝 `report-mark-db-snapshot.timer`，沒裝時趨勢圖是空的。
- **慢查詢**（`/api/admin/db/slow-queries`）：要先啟用 `pg_stat_statements`，沒啟用時頁面顯示原因
  （未建立擴充／未預載／權限不足）。**程式與 migration 都不會建立擴充或改設定**（啟用是獨立的維護步驟，見下）。

### db-snapshot（每小時 :40）

`scripts/db_snapshot.py`（SQL 在 `app/services/db_insights.py`）每輪：寫一列 `research.db_stat_snapshot`
（granularity=hour、taken_at＝整點；同一小時已有就不寫）→ 把已結束、還有逐時列卻沒有每日列的日子（台北時間）
各彙總成一列 granularity=day（所以每天第一次執行會補前一天，漏跑的日子也會補；重跑是 no-op）→ 刪除逐時超過
30 天、每日超過 400 天的列（`DB_SNAPSHOT_HOURLY_RETENTION_DAYS`／`DB_SNAPSHOT_DAILY_RETENTION_DAYS`）。這只是監控統計，**不是 DB dump、不備份**；量很小（逐時 720 列、每日 400 列，每列數 KB）。

- 這支要連 DB（監控收集器刻意不連）：量的就是 DB 本身，DB 掛掉時本來就量不到；DB 故障告警仍是 `/healthz`＋P5。
- 退出碼：0 正常、1 其他失敗（整輪 rollback，走 `OnFailure` 告警）、2 DB 不可用（`SuccessExitStatus=2` 放行，
  理由同 rollup-observations：P5 已去重告警，`report-mark-alert@` 沒有去重）。系統目錄某段權限不足或逾時不算
  失敗：記在該列 `stats.errors`，趨勢上是空點。
- 排程錯開 sync（每 3 小時整點）與 :20 的監控聚合；不 import 檢索、嵌入、LLM 模組（`MemoryMax=512M`，
  `tests/test_db_snapshot.py` 守門）。

安裝（人工，需 sudo；只在要啟用時做；schema 須已在 head）：

```bash
# 1) 先看這一輪會寫什麼（唯讀；各段權限不足會出現在 errors）
uv run python scripts/db_snapshot.py --dry-run
# 2) unit（非辦公室主機改用 deploy/install_units.sh）
sudo install -m 0644 deploy/systemd/report-mark-db-snapshot.service deploy/systemd/report-mark-db-snapshot.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now report-mark-db-snapshot.timer
```

驗收：`sudo systemctl start report-mark-db-snapshot.service` 後 `journalctl -u report-mark-db-snapshot -n 3` 看得到
「完成 逐時快照已寫入…」；`scripts/verify_oneshot_ran.sh` 確認跑過；隔天 `SELECT granularity, count(*) FROM
research.db_stat_snapshot GROUP BY 1` 有一列 day。辦公室主機的 ops catalog（`deploy/ops/services.prod.toml`）已列
`db-snapshot`（含「立即執行」，理由寫在 catalog 註解）：裝好 unit 後照「維運代理」的步驟 3、4 重新安裝 catalog 與
polkit、`--check`，再重啟代理。

停用：

```bash
sudo systemctl disable --now report-mark-db-snapshot.timer   # 趨勢從此不再更新；既有的列留著（不會再被保留期清掉）
```

要連資料一起清：`DELETE FROM research.db_stat_snapshot;`（只是監控統計，刪了只是趨勢圖變空）。

### 啟用 pg_stat_statements（獨立維護步驟；部署流程不會自動做）

`pg_stat_statements` 要在 `shared_preload_libraries` 預載才能用，而改這個參數一定要重啟 PostgreSQL。兩個環境都已啟用；
下面的步驟留給重建容器、換主機或回退時用。兩個環境的前提不同：

- **測試環境（辦公室主機，`report-mark-postgres` 容器，pg16）**：預載寫在 data volume 的 `postgresql.auto.conf`，容器重建
  也還在。全新的 volume 要照下面做，需要短暫停機（重啟容器約數秒，期間 web 回 503、探針可能經 P5 開事件；挑維護窗口、事先告知）：

  ```bash
  # 1) 寫進 data volume 的 postgresql.auto.conf（容器重建也還在）
  docker exec report-mark-postgres psql -U postgres -c "ALTER SYSTEM SET shared_preload_libraries = 'pg_stat_statements'"
  # 2) 重啟容器（短暫停機）
  docker restart report-mark-postgres
  docker exec report-mark-postgres psql -U postgres -Atc "SHOW shared_preload_libraries"   # 應為 pg_stat_statements
  # 3) 在 research 庫建立擴充（只有這個庫的頁面會用到）
  docker exec report-mark-postgres psql -U postgres -d research -c "CREATE EXTENSION IF NOT EXISTS pg_stat_statements"
  ```

  若 `shared_preload_libraries` 原本已有其他值，`ALTER SYSTEM` 要把舊值一起列上（逗號分隔），否則會被蓋掉。
- **正式環境（EC2＋RDS；AWS 資源名稱沿用 staging 這個歷史命名）**：參數群組
  `report-research-staging-dbparametergroup-…` 已含 `pg_stat_statements,pg_tle`（static、in-sync），**不需要
  reboot**；只要以 master 帳號（`rds_superuser`）在 app 用的庫執行一次
  `CREATE EXTENSION IF NOT EXISTS pg_stat_statements;`。app 帳號若沒有 `pg_read_all_stats`，頁面看得到
  統計但別人的語句文字會顯示「權限不足、已隱藏」；要看全部再以 master `GRANT pg_read_all_stats TO <app 帳號>`
  （是否授予由使用者決定；目前未授予）。

驗收：頁面「慢查詢」不再顯示原因、出現依總執行時間排序的語句。統計自 `pg_stat_statements_reset()` 或重啟起累計。
回退：`DROP EXTENSION pg_stat_statements;`（辦公室主機若要連預載一起拿掉：`ALTER SYSTEM RESET shared_preload_libraries`
＋重啟容器）。

**隱私**：pg_stat_statements 會把常數正規化成 `$1`，但工具語句可能保留字面值；頁面只開給 `ops.read`、查詢文字
壓空白後截斷 200 字。

## schema 與版本 drift 每日檢查（report-mark-schema-check）

`report-mark-schema-check.timer` 每日 05:20 跑 `scripts/schema_baseline.py scheduled`，對目標庫唯讀：

1. **版本 drift**：DB 的 `public.alembic_version` 對部署程式的 alembic head。完整比對以「DB 自己宣稱的
   revision」為基準，所以「換了程式、忘了 `make schema`」的庫在完整比對裡是零 drift——只有這一步抓得到。
   手動跑：`make schema-version`（＝`check --expect-head`，不建暫存庫、不需要 CREATEDB）。
2. **schema drift**：版本可對應時（一致或落後），以 DB 的 revision 在**同一台伺服器**建唯一的暫存庫
   `schema_ref_*`、套 baseline＋到該 revision 為止的 SQL、逐項比對系統目錄，比完一定刪掉並確認刪掉了
   ——與 `make schema-check` 同一段程式，保證不變（清理失敗＝整次作廢、印手動清理指令）。
   extension 自己建的物件不比；目標多裝的維運用 extension（`scripts/schema_baseline.py` 的
   `OPERATIONAL_EXTENSIONS`，目前只有 `pg_stat_statements`）只在輸出列為資訊、不算 drift，因為它由獨立的
   維護步驟啟用、不在 migration 裡。其他 extension 多裝或缺少照樣算 drift。

退出碼與告警（unit 以 `OnFailure=report-mark-alert@%n.service` 加 `SuccessExitStatus=3` 表達）：

| 退出碼 | 意思 | 告警 |
|---|---|---|
| 0 | 版本一致且零 drift | 否 |
| 1 | schema drift，或 DB 落後（尚未套用的 revision 列在訊息與狀態檔） | 是 |
| 2 | DB 超前（revision 不在程式的鏈上）、未接管、不明確、無法比對、**暫存庫清理失敗**、帳密錯或沒有 CREATEDB | 是 |
| 3 | 目標 DB 連不上（只在第一次接觸目標時判定：連線被拒、逾時、DNS、伺服器正在啟動／關閉） | 否 |

3 不告警的理由與 rollup-observations 的 rc=2 相同：DB 掛掉已由 web 探針經 P5 帶去重地告警，
`report-mark-alert@` 沒有去重，再叫一次只是重複通知；那一天的檢查就此跳過，狀態檔記 `db_unavailable`。
比到一半才斷線則是清理失敗或無法比對（2），照樣告警——受檢的 DB 伺服器上可能留了一個暫存庫。

**狀態檔** `data/schema_check.json`（`SCHEMA_CHECK_STATUS_FILE` 可改；原子寫入，寫不進去只警告、不改
退出碼——告警走退出碼，這份是給管理頁「資料健康」讀的投影）。format 1 的鍵：`format`、`checked_at`
（含時區）、`duration_s`、`mode`（`full`／`version`）、`target`（`host:port/db`，不含帳密）、`exit_code`、
`alert`（1、2 為 true）、`problems`（`db_unavailable`、`version_behind`、`version_ahead`、`version_unversioned`、
`version_ambiguous`、`schema_drift`、`reference_cleanup_failed`、`check_error` 的子集）、`message`、
`version`（`status`、`expected_head`、`db_revision`、`pending`）、`drift`（`status` 為
`ok`／`drift`／`error`／`cleanup_failed`／`skipped`，以及 `revision`、`reference`、`drift_count`、
`categories`、`column_order_differs`、`message`）。完整定義在該腳本的 `build_status_payload`。

### 正式環境（EC2 的 RDS）

EC2 的 RDS app 帳號沒有 CREATEDB，完整比對每天都會以 2 告警。兩種做法擇一：

- **只比版本**（建議的起點）：`/etc/default/report-mark-sync` 加 `SCHEMA_CHECK_MODE=version`。版本 drift
  照樣每日偵測；schema drift 在每次部署前人工以下一種方式跑一次。
- **完整比對、基準改用 master 帳號建**：另建 `/etc/default/report-mark-schema-check`（root 擁有、0600；unit 以
  `EnvironmentFile=-` 選用載入，systemd 以 root 讀，行程照樣拿得到），內容兩行：
  `SCHEMA_CHECK_REFERENCE_URL_ENV=SCHEMA_CHECK_REFERENCE_URL` 與
  `SCHEMA_CHECK_REFERENCE_URL=postgresql+asyncpg://<master 帳號>:<密碼>@<RDS 端點>:5432/postgres?ssl=verify-full`。
  暫存庫建在同一個 RDS（版本相同才能逐字比），目標的目錄仍以 app 帳號讀。帳密只放這個檔，repo 與
  `report-mark-sync` 環境檔都不放；master 帳號權限大，評估過再用。手動跑同一件事：
  `SCHEMA_CHECK_REFERENCE_URL=… uv run python scripts/schema_baseline.py check --reference-url-env SCHEMA_CHECK_REFERENCE_URL`。

### 安裝（人工，需 sudo；只在要啟用時做）

```bash
# 0) 先手動跑一次，確認現況（辦公室主機：版本應一致、零 drift；不一致就先處理，否則一啟用就告警）
make schema-version
make schema-check
# 1) 辦公室主機
sudo install -m 0644 deploy/systemd/report-mark-schema-check.service deploy/systemd/report-mark-schema-check.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now report-mark-schema-check.timer
# 1') EC2（正式環境）：先在 /etc/default/report-mark-sync 加 SCHEMA_CHECK_MODE=version（或照上一節放 reference 檔）
sudo deploy/install_units.sh --user <使用者> --root <repo 根> report-mark-schema-check.timer
sudo systemctl enable --now report-mark-schema-check.timer
# 2) 維運代理的 catalog 要列 schema-check：照「維運代理」的步驟 3 重新安裝 catalog、--check，再重啟代理
```

驗收：`sudo systemctl start report-mark-schema-check.service` 後 `systemctl show report-mark-schema-check -p ExecMainStatus`
為 0、`journalctl -u report-mark-schema-check -n 5` 看得到「版本一致」與「結論：零 drift」、`data/schema_check.json`
的 `exit_code` 為 0；`scripts/verify_oneshot_ran.sh` 確認跑過。之後在伺服器上不應留下任何 `schema_ref_*` 庫
（`docker exec report-mark-postgres psql -U postgres -Atc "SELECT datname FROM pg_database WHERE datname LIKE 'schema_ref_%'"`
應為空）。DB 的 revision 落後於程式時，啟用當天就會以「DB 落後」告警——先照部署順序套 schema。

### 停用

```bash
sudo systemctl disable --now report-mark-schema-check.timer   # 狀態檔停在最後一次的結果（看 checked_at）
```

## 檢索回歸每日檢查（report-mark-retrieval-regression）

`report-mark-retrieval-regression.timer` 每日 07:40 跑 `scripts/retrieval_regression.py check`（零 LLM）：凍結題集
`eval/ragas_questions.json` 的 18 題各跑一次 `hybrid_search`（參數同問答：`dense_scan`＝`ASK_DENSE_SCAN`），與一次性
擷取的基準 `data/retrieval_regression/baseline.json` 比研報層級的召回，結果寫 `data/health/retrieval_regression.json`，
管理後台「維運 → 資料健康」最下面的「檢索回歸」卡片讀它（`GET /api/admin/retrieval-regression`，web 不跑檢索）。
量哪一層、為什麼不用 `retrieve_context`、指標與門檻的理由在 `app/services/retrieval_regression.py` 的模組 docstring。

**不算劣化的三種變化**（分開計數、顯示在卡片上）：

- 基準時點之後才入庫的研報（`created_at` 晚於基準的語料截點）先從完整候選清單排除再取 top-k——語料每 3 小時在長，
  新研報擠進前段不是變差。基準裡本來就有的研報即使重新入庫（`created_at` 被重設）也不算新。
- 基準研報被管理員隱藏（`report_visibility`）或已不在語料裡：從分母拿掉。
- 字面路候選被 cap 截斷（`lex_truncated`）的題目，前後兩次跑的 top-k 本來就可能不同（`store._lexical_sql` 刻意不在
  cap 前排序）；這不排除，而是由「允許少數題崩掉」吸收，並在卡片上標出來。

退出碼與告警（unit 以 `OnFailure=report-mark-alert@%n.service` 加 `SuccessExitStatus=2` 表達）：

| 退出碼 | 意思 | 告警 |
|---|---|---|
| 0 | 沒有劣化 | 否 |
| 1 | 劣化：平均研報召回 < `RETRIEVAL_REGRESSION_MIN_MEAN_RECALL`（0.8），或研報召回 < `RETRIEVAL_REGRESSION_MIN_QUESTION_RECALL`（0.5）的題數 > `RETRIEVAL_REGRESSION_MAX_DEGRADED_QUESTIONS`（2） | 是 |
| 2 | 這次略過：DB 連不上、可用記憶體 < `RETRIEVAL_REGRESSION_MIN_AVAILABLE_GIB`（4 GiB）、sync 正在跑（`data/.sync_new_reports.lock` 的行程還活著） | 否 |
| 3 | 要人處理：還沒有基準、基準檔壞了、題集或嵌入模型與基準不同、可比較的題目不到一半（基準研報大半已下架或隱藏）、嵌入模型載不起來、非連線類的 DB 錯誤、未預期的例外 | 是 |

2 不告警的理由與 rollup-observations 的 rc=2 相同：DB 掛掉已由 web 探針經 P5 帶去重地告警，記憶體與 sync 是會自己好的
狀況。略過那次不蓋掉上一次的比對；連續略過超過 48 小時，卡片把結果標成過期（至少「注意」）。

**資源與互斥**：會載 BGE-M3（行程約 2–3 GB）。不取 `scripts/_claude_lock.py` 的鎖——它不呼叫 LLM，取了反而讓同時段的
LLM 批次（含 sync 輪內的摘要／標題／摘錄）以 rc=75 跳過；記憶體競爭改由排程避開 sync 與夜間回填、sync PID 檔守門、
`MemAvailable` 守門與 `MemoryMax=4G` 兜底（超過就在這個 unit 裡被 OOM，SIGKILL 會走告警），CPU 以 `Nice=15`、
`IOSchedulingClass=idle`、`EMBED_TORCH_THREADS=2` 讓路。量級（約 1.5 萬篇的語料）：冷載模型＋18 題約 4 分鐘（模型已載入時
比對約 50 秒）、行程峰值 RSS 約 2 GiB。字面路被截斷的題目（命中片段數超過 cap）在相隔幾分鐘的兩次執行之間，研報召回可能
從 1.00 掉到 0.40——這就是允許少數題崩掉的原因。

**評測刻意不進 CI**：這支要真語料與真模型；CI 只跑假嵌入、假 DB 的單元測試（`tests/test_retrieval_regression.py`）。

### 安裝（人工，需 sudo；只在要啟用時做）

```bash
# 0) 先擷取基準（一次性；在 repo 根、用部署機的 DB）。會載 BGE-M3，挑 sync 沒在跑、可用記憶體 ≥ 4 GiB 的時段
EMBED_TORCH_THREADS=2 uv run python scripts/retrieval_regression.py capture
#    再手動比一次，確認是 0、研報召回接近 1（字面路截斷的題目可能不是 1.00，卡片會標出來）
EMBED_TORCH_THREADS=2 uv run python scripts/retrieval_regression.py check
# 1) 辦公室主機
sudo install -m 0644 deploy/systemd/report-mark-retrieval-regression.service deploy/systemd/report-mark-retrieval-regression.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now report-mark-retrieval-regression.timer
# 2) 辦公室主機的 catalog 列有 retrieval-regression（唯讀，不給 run）：照「維運代理」的步驟 3 重新安裝 catalog、
#    --check，再重啟代理
# 1') EC2（正式環境）：語料不同，要在 EC2 上自己擷取基準；unit 用 install_units.sh 代換使用者與路徑
sudo deploy/install_units.sh --user <使用者> --root <repo 根> report-mark-retrieval-regression.timer
sudo systemctl enable --now report-mark-retrieval-regression.timer
```

驗收：`sudo systemctl start report-mark-retrieval-regression.service` 後 `systemctl show report-mark-retrieval-regression -p ExecMainStatus`
為 0、`journalctl -u report-mark-retrieval-regression -n 30` 看得到逐題表格與「沒有劣化」、`data/health/retrieval_regression.json`
的 `outcome` 為 `ok`，管理頁卡片顯示「正常」；`scripts/verify_oneshot_ran.sh` 確認跑過。沒有先擷取基準就啟用，第一次會以
rc=3（`no_baseline`）告警。

### 重新擷取基準

基準是一把尺，**只在刻意改變了「什麼算正確結果」時才換**：調整檢索參數（`ASK_DENSE_SCAN`——卡片會顯示 `params_changed`
提醒——、融合權重、索引）並確認變好之後；題集或嵌入模型改了（rc=3 `incomparable`）；基準太舊、可比較的題目不到一半
（rc=3）。劣化告警時**不要**直接重新擷取——那等於把劣化收編成新基準；先看卡片上流失的研報與 journal 找原因。

```bash
EMBED_TORCH_THREADS=2 uv run python scripts/retrieval_regression.py capture --force   # 覆寫；舊基準需要時先自己備份
```

`capture --as-of <ISO 時間>` 是驗證用：假裝基準是在那個時點擷取的（之後入庫的研報不進基準），用來檢查新研報排除是否有效，
不要用它建正式基準。

### 停用

```bash
sudo systemctl disable --now report-mark-retrieval-regression.timer   # 結果檔停在最後一次（48 小時後卡片標成過期）
```

## 維運代理（report-mark-ops-agent）

管理後台的維運狀態（`/api/admin/ops/*`）不是 web 自己去跑 systemctl／journalctl／docker，而是經
Unix socket 問一支單機代理（`ops_agent/`，只用標準庫、系統的 `/usr/bin/python3`）。代理只認
Service Catalog（`deploy/ops/services.prod.toml`；EC2（正式環境）`deploy/ops/services.staging.toml`、開發環境
`deploy/ops/services.dev.toml`）列出的服務與
action：唯讀的 `status`、`logs`，以及寫入類的 `restart`（只有 Web）與 `run`（既有 oneshot 立即執行一次：
sync、backup、freshness、audit、r2-reconcile、upload、db-snapshot、analytics-rollup）。
沒有任意 shell、任意 unit、stop、timer enable/disable；
PostgreSQL、nginx、cloudflared 永遠唯讀（catalog 載入期與代理執行前兩層都擋，PG 的 restart 尤其禁止）。
P4／P5 的探針與事件處理不經過它，代理停掉只讓管理頁的維運區塊回 503 `ops_agent_unavailable`，web 其他功能照常。

**寫入類的規則**（細節在 `ops_agent/actions.py`）：
- Web 端要管理員＋另外授予的 `ops.operate`＋10 分鐘內重新驗證過密碼（elevated），每次（成功或被拒）寫
  `admin_audit_log`；代理的 journal 另記一行（含 web 傳來的使用者名稱）。
- **execution group 互斥、不排隊**：同 group 任一 unit 正在跑（activating／active／reloading／deactivating，
  或有排隊中的 job）就回 409 `already_running`；restart 的對象本身只看轉換中狀態（Web 平常就是 active）。
- sync 會呼叫 LLM：另以非阻塞 flock 試探 `data/.claude_cli.lock`（`scripts/_claude_lock.py`），並比照 sync
  自己的 `kill -0` 檢查 `data/.sync_new_reports.lock`——手動在主機跑的批次不經 systemd、也不經 Web，只看
  unit 狀態會漏掉它們。鎖檔目錄看不到時回 503 `ops_lock_unavailable`（無法確認就不執行）。試探只佔
  微秒級的窗口；剛好撞上時批次會 rc=75（「不跑」不是「跑壞」，下一輪補上）。
- run 是 `systemctl start --no-block`，等同 timer 觸發（同一份 unit、同一份環境檔、`--hashes-file` 等邏輯不變），
  **不接受任何參數**。restart 是代理先回 202、`restart_delay`（1.5 秒）後才 `systemctl restart --no-block`
  ——重啟 Web 會中斷這次請求本身。前端輪詢 `/api/admin/ops/services/{name}`，`systemd.invocation_id`
  換成新值（並回到 active）就是新的一輪起來了。Web 重啟期間 BGE-M3 要重新載入，這段時間整站不可用。
- **授權靠 polkit，不靠 sudo**：代理的 unit 是 `NoNewPrivileges=yes`、沒有 capability，setuid 的 sudo 在
  裡面無法提權。systemd 以 polkit 的 `org.freedesktop.systemd1.manage-units` 檢查呼叫端並附上 unit 與
  verb，`deploy/polkit/10-report-mark-ops.rules` 把 `report-mark-ops` 限定在 Web 的 restart 與八個 oneshot
  （sync、backup、freshness、audit、r2-reconcile、upload、db-snapshot、analytics-rollup）的 start，
  `report-mark-ops-dev` 限定在 `report-mark-dev-web.service` 的 restart 與
  `report-mark-dev-smoke.service` 的 start，`report-mark-ops-staging`（EC2）限定在 Web 的 restart 與
  freshness、audit、db-snapshot、analytics-rollup 的 start；這三個使用者的其他 systemd 動作一律 NO。需要 polkit ≥ 0.106
  （JS 規則；Ubuntu 24.04 是 124）。

**威脅模型**：代理的使用者在 `docker` 群組（讀容器狀態與日誌需要 docker socket），而 docker 群組等同
root。所以程式碼與 catalog 一律裝在 root 擁有、其他人不可寫的 `/opt/report-mark-ops/`，**不從 repo 執行**
（repo 屬於 web 的使用者 kashionz；從那裡執行等於 web 被攻破就能改代理、拿到 root）。socket 只給
`report-mark-ops` 群組（0660，目錄 0750），代理再以 `SO_PEERCRED` 比對 catalog 的 `allowed_users`。

### 安裝（人工，需 sudo；只在要啟用時做）

```bash
# 1) 專用使用者：讀 journal 用 systemd-journal、讀容器用 docker（見上面的威脅模型）
sudo useradd --system --user-group --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin report-mark-ops
sudo usermod -aG systemd-journal,docker report-mark-ops
# 2) web 的使用者加入 socket 群組（下一步之後要重啟 web 才拿得到新群組）
sudo usermod -aG report-mark-ops kashionz
# 3) 程式碼與 catalog 裝到 root 擁有的目錄（在部署 checkout 的 repo 根執行；每次更新 ops_agent/ 或 catalog 都重做）
sudo install -d -o root -g root -m 0755 /opt/report-mark-ops /opt/report-mark-ops/ops_agent
sudo install -o root -g root -m 0644 ops_agent/*.py /opt/report-mark-ops/ops_agent/
sudo install -o root -g root -m 0644 deploy/ops/services.prod.toml /opt/report-mark-ops/
(cd /opt/report-mark-ops && /usr/bin/python3 -B -E -s -m ops_agent --catalog services.prod.toml --check)
# 4) 寫入類的授權（restart／run）：polkit 規則，root 擁有、0644。polkitd 會自動重新載入 rules.d。
#    只要唯讀時可以不裝：代理對 restart／run 會回 command_failed（systemd 拒絕，Access denied）。
sudo install -o root -g root -m 0644 deploy/polkit/10-report-mark-ops.rules /etc/polkit-1/rules.d/
# 5) unit（不經 install_units.sh）。unit 以 BindReadOnlyPaths 唯讀綁進部署目錄的 data/（鎖檔），
#    部署目錄不是 /home/kashionz/projects/report-mark 時，unit 與 catalog 的 flock_files／pid_files 一起改。
sudo install -m 0644 deploy/systemd/report-mark-ops-agent.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now report-mark-ops-agent.service
sudo systemctl restart report-mark-web.service   # 讓 web 拿到 report-mark-ops 群組
# 6) 要用寫入類的管理員：super admin 在管理頁授予 ops.operate（只有另外授予的人能 restart／run）
```

寫入類驗收（不重啟任何東西）：sync 正在跑時對它「立即執行」應回 409 `already_running`；對 `audit`
（唯讀、零 LLM）執行一次後 `systemctl show report-mark-audit -p InvocationID` 換成新值；polkit 規則沒裝或
沒生效時回 502 `ops_command_failed`（訊息含 `Access denied`）。Web 的 restart 只在真的要重啟時才按。

驗收：`systemctl show report-mark-ops-agent -p ActiveState,SubState,NRestarts`、`ls -l /run/report-mark-ops/`
（`srw-rw---- report-mark-ops report-mark-ops agent.sock`），再以管理員登入打 `/api/admin/ops/services`。
503 的 `detail` 會說是哪一種：找不到 socket＝代理沒起來、沒有權限＝web 還沒拿到群組（重啟 web）、
`這個使用者不能連線`＝catalog 的 `allowed_users` 沒有 web 的使用者。

開發環境同一支程式：使用者 `report-mark-ops-dev`（同樣的群組設定，開發用 web 的使用者加入
`report-mark-ops-dev`）、catalog `services.dev.toml`、unit `report-mark-ops-agent-dev.service`、socket
`/run/report-mark-ops-dev/agent.sock`；開發用 web 設 `OPS_AGENT_ENVIRONMENT=development`。代理以 dev socket
載入 prod catalog（或反之）會拒絕啟動，dev catalog 也只能列 `report-mark-dev-*` 與 `report-mark-dev*` 容器。
寫入類在開發環境同一套規則：`dev-web` 可 restart、`dev-smoke`（`deploy/systemd/report-mark-dev-smoke.service`，
只 sleep 20 秒的 oneshot，裝到 `/etc/systemd/system/` 後 `daemon-reload` 即可，不必 enable）可 run，用來驗
run、409 與輪詢而不碰任何真東西；polkit 規則同一個檔已涵蓋 `report-mark-ops-dev`。
本機冒煙（不必安裝）：`python3 -m ops_agent --catalog deploy/ops/services.dev.toml --socket /tmp/<短路徑>/a.sock`
（只有 development 允許覆寫 socket；AF_UNIX 路徑上限約 107 字元）。

**EC2（正式環境；名稱裡的 staging 是歷史命名）**（重裝或更新時照下面做，
`deploy/ops/services.staging.toml` 開頭有 EC2 與辦公室主機的差異）：
使用者 `report-mark-ops-staging`、catalog `services.staging.toml`、unit `report-mark-ops-agent-staging.service`、
socket `/run/report-mark-ops-staging/agent.sock`；EC2 的 web 設 `OPS_AGENT_ENVIRONMENT=staging`（值是歷史命名）（repo 根 `.env`）。
EC2 沒有容器（RDS、apt 的 nginx），所以這個使用者**不加 docker 群組**、unit 也不給。權限比辦公室主機那組窄：
restart 只有 web，run 只有 freshness、audit、db-snapshot、analytics-rollup；sync（共用 DeepSeek 金鑰）與 r2-reconcile（共用 R2 bucket）唯讀。
polkit 規則同一個檔（`report-mark-ops-staging` 那組）；EC2 上不建 `report-mark-ops`，辦公室主機那組在那裡不會命中。
catalog 以 uid 寫 `allowed_uids = [1000]`（ubuntu），先以 `id -u ubuntu` 核對。

```bash
# 在 EC2 的部署 checkout（/home/ubuntu/report-mark）repo 根執行
sudo useradd --system --user-group --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin report-mark-ops-staging
sudo usermod -aG systemd-journal report-mark-ops-staging      # 不加 docker
sudo usermod -aG report-mark-ops-staging ubuntu
sudo install -d -o root -g root -m 0755 /opt/report-mark-ops /opt/report-mark-ops/ops_agent
sudo install -o root -g root -m 0644 ops_agent/*.py /opt/report-mark-ops/ops_agent/
sudo install -o root -g root -m 0644 deploy/ops/services.staging.toml /opt/report-mark-ops/
(cd /opt/report-mark-ops && /usr/bin/python3 -B -E -s -m ops_agent --catalog services.staging.toml --check)
sudo install -o root -g root -m 0644 deploy/polkit/10-report-mark-ops.rules /etc/polkit-1/rules.d/
sudo deploy/install_units.sh --user ubuntu --root /home/ubuntu/report-mark report-mark-ops-agent-staging.service
sudo systemctl enable --now report-mark-ops-agent-staging.service
sudo systemctl restart report-mark-web.service   # .env 設好 OPS_AGENT_ENVIRONMENT=staging，並讓 web 拿到群組
```

EC2 的 catalog 只列已安裝的 unit；之後在 EC2 裝了新的 unit（例如 host-health），
要把它加進 `services.staging.toml`（含 `depends_on`；給 run 的同步加進 polkit 的
`report-mark-ops-staging`）並重做 install＋`--check`＋重啟代理。

**依賴圖**：catalog 的 `depends_on` 與 `[[externals]]`（格式見 `ops_agent/catalog.py`）是服務依賴關係的唯一真相來源，
`--check` 會擋下指向不存在的節點、依賴自己與環。改了依賴（或新增服務時忘了寫）都照步驟 3 重新安裝 catalog、
`--check`、重啟代理；管理頁的依賴圖（`GET /api/admin/ops/dependencies`）在代理重啟後才看得到新的關係。

### 停用

```bash
sudo systemctl disable --now report-mark-ops-agent.service   # 管理頁的維運區塊改回 503，其他不受影響（EC2 是 report-mark-ops-agent-staging.service）
```

## ClamAV（上傳掃描）

上傳管線用常駐的 clamd 容器掃描上傳的 PDF：`deploy/clamav/docker-compose.yml`（獨立 compose 專案
`report-mark-clamav`、容器 `report-mark-clamav`、官方映像 `clamav/clamav:1.4`）與 `deploy/clamav/conf/` 的
`clamd.conf`／`freshclam.conf`。只綁 `127.0.0.1:3310`、不加入 edge 網路、病毒碼放 named volume、
`restart: unless-stopped`、`mem_limit: 2g`。客戶端是 `app/services/clamd.py`（標準庫 socket），web 不直接連它。

**這是安全閘門，fail-closed**（不適用派生功能的 fail-open）：clamd 連不上、逾時、回應看不懂、病毒碼超過
`CLAMD_SIGNATURE_MAX_AGE_HOURS`（預設 72 小時）或日期判斷不出來，一律是**暫時性**錯誤——上傳留在隔離區、
下一輪再試，**永不放行**；超過串流上限（`StreamMaxLength 30M`／`CLAMD_STREAM_MAX_BYTES`）或 clamd 對內容回
其他 ERROR 是**決定性**錯誤（worker 重試 3 次後轉 blocked）。唯一放行的是 clamd 明確回 `stream: OK`。

`clamd.conf` 刻意改掉的預設（每一條在檔案裡都有註解）：`StreamMaxLength 30M`（≥ 25 MB 上傳上限）、
`AlertExceedsMax yes`（預設超過 MaxFileSize／MaxScanSize 只掃一部分就回 OK）、`AlertEncrypted yes`（預設把打不開的
加密 PDF 當乾淨）、`ConcurrentDatabaseReload no`（預設重載時記憶體翻倍到約 3 GB，會撞上 2g 上限）。
設定是整個 `/etc/clamav` 目錄唯讀掛進去（取代映像內建設定），刻意不掛單檔：Docker Desktop 重啟後單檔
bind mount 可能掛不上（`docs/EXTERNAL_ACCESS.md` 的 nginx 事故）。記憶體預估與實測見 `docs/CAPACITY.md`
「常駐元件：ClamAV」。

### 安裝（人工；只在要啟用上傳時做）

```bash
# 0) 先看記憶體：常駐約 1.2–1.6 GB。可用記憶體常態低於 3 GiB 時先不要起
free -g
# 1) 在部署 checkout 的 repo 根啟動（首次要下載約 300 MB 病毒碼，數分鐘；health 有 6 分鐘 start-period）
make up-clamav
docker inspect -f '{{.State.Status}} {{.State.Health.Status}}' report-mark-clamav   # 等到 running healthy
# 2) 冒煙：EICAR 要 FOUND、正常 PDF 要 OK（走上傳 worker 同一條 scan()；病毒碼過舊也算失敗）
make clamav-smoke                                   # 內建最小 PDF
make clamav-smoke SMOKE_PDF=/path/to/真實研報.pdf    # 再用一份真的研報驗一次
# 3) 維運代理的 catalog 要列 clamav：照「維運代理」的步驟 3 重新安裝 catalog、--check，再重啟代理
# 4) 容器探針不必另外設定：預設目標清單（scripts/check_container_health.sh）已含 clamav
```

`make clamav-smoke` 的退出碼：0 通過／1 結果不符（EICAR 沒被攔、PDF 沒通過、病毒碼過舊）／2 clamd 連不上。
EICAR 字串在執行期拼出來、只存在記憶體，不會在磁碟上留下會被防毒隔離的檔案。

**容器探針預設就會找 `report-mark-clamav`**：裝了容器探針而還沒 `make up-clamav` 的主機，它是 `missing`，
連續 3 輪（約 6 分鐘）後開 WARNING 事件。暫時不啟用 ClamAV 時，在
`/etc/default/report-mark-sync` 設 `CONTAINER_HEALTH_TARGETS`（把預設值裡的 `clamav=…` 拿掉）。

### 病毒碼更新

容器內的 freshclam 每天檢查 `FRESHCLAM_CHECKS` 次（compose 設 4，每 6 小時；映像 entrypoint 的預設其實是 1），
有新病毒碼才下載並通知 clamd 重載。`ConcurrentDatabaseReload no` 下重載期間約 30–60 秒新的掃描要等（連線被接受
但晾著），客戶端逾時（`CLAMD_TIMEOUT`，預設 120 秒）蓋得過；萬一逾時也只是暫時性錯誤。

```bash
docker exec report-mark-clamav sh -c 'echo VERSION | nc localhost 3310'   # ClamAV 1.4.x/<病毒碼版本>/<日期，UTC>
docker logs --since 24h report-mark-clamav 2>&1 | grep -iE 'freshclam|database|error'
docker exec report-mark-clamav freshclam --stdout   # 手動更新一次（freshclam 自己會限制頻率；不要寫成迴圈）
```

病毒碼超過 72 小時沒更新時，上傳 worker 會以 `signatures_stale` 拒掃（上傳停在隔離區）。常見原因：主機連不到
`database.clamav.net`（被限流時 freshclam 會在日誌說 `cool-down`，等它過）、volume 空間不足。容器內的時區必須是
UTC（compose 寫死 `TZ: Etc/UTC`）：`VERSION` 的日期是容器本地時間，被改成 UTC+8 會讓日期跑到未來，客戶端判成
`signatures_unknown`、同樣不放行。

### clamd 掛掉時

| 狀況 | 看得到什麼 | 上傳的行為 | 處置 |
|---|---|---|---|
| 容器停了（Exited） | 容器探針 `clamav:exited`，3 輪後 WARNING；管理頁維運的 clamav 為 exited | 掃描回 `connection_refused`（暫時性），檔案留在隔離區、不放行 | `docker logs --tail 100 report-mark-clamav` 看原因，`make up-clamav` |
| 容器 running 但 unhealthy | 探針 `clamav:unhealthy`。clamd 在容器裡是背景行程，被 OOM 或崩潰時容器不會退出，`restart: unless-stopped` 也不處理 unhealthy | 同上 | `docker inspect -f '{{.State.OOMKilled}}' report-mark-clamav`；`docker restart report-mark-clamav`（重新載入病毒碼約一分鐘）。反覆 OOM 見 `docs/CAPACITY.md` |
| 病毒碼過舊 | worker 記 `signatures_stale` | 不掃、留在隔離區 | 見上一節 |
| 重載中、逾時 | worker 記 `timeout` | 留在隔離區，下一輪自然重試 | 不必處置；持續發生才看 `docker stats report-mark-clamav` |

**Docker Desktop 重啟後可能停在 Exited**：nginx 發生過（`docs/EXTERNAL_ACCESS.md`），
`restart: unless-stopped` 不會重試啟動失敗。ClamAV 的設定刻意掛目錄而不是單檔以避開那個成因，但 Docker Desktop
重啟後仍要確認一次：`docker ps -a --filter name=report-mark-clamav`；是 Exited 就 `make up-clamav`
（把既有容器再啟動）。還是起不來時重建：`docker compose -f deploy/clamav/docker-compose.yml up -d --force-recreate`
（病毒碼在 volume 裡，不必重抓）。

### 停用

```bash
make down-clamav   # 停掉並移除容器；病毒碼 volume 保留（刻意不帶 -v）
```

停掉之後上傳只會停在隔離區、不放行，其他功能不受影響。長期停用時，在 `/etc/default/report-mark-sync` 設
`CONTAINER_HEALTH_TARGETS`（不含 clamav）避免探針持續告警；要連病毒碼一起清掉：
`docker volume rm report-mark-clamav_clamav-db`（下次啟動要重新下載約 300 MB）。

## 上傳 worker

`report-mark-upload.service`＋`.timer`（`scripts/process_uploads.sh`）：每 5 分鐘一輪，掃毒（ClamAV）→ 掃描通過的
上傳入庫成草稿 → 本輪新草稿跑摘要、標題、摘錄 → 清除過寬限期的退回件、過保留期的感染證據、隔離區孤兒檔。
流程與狀態機見 `docs/WORKFLOW.md`「上傳 worker」。各主機是否已安裝見 `docs/DEPLOYMENT_STATUS.md`，
安裝前先核對目標主機的 unit、timer 與 `UPLOAD_ENABLED`。下面的安裝要另外取得同意，
站序為 devdb 演練 → 測試環境（辦公室主機）→ 正式環境（EC2）；沒有 ClamAV 的主機維持 `UPLOAD_ENABLED=0`、不裝上傳 worker。

互斥與記憶體：

- 整輪鎖 `data/.upload_worker.lock`（flock，殼取、子命令驗證）：同時只有一輪；手動跑殼或子命令也取同一把。
- 入庫段取 claude 鎖（`data/.claude_cli.lock`），與 sync、手動 LLM 批次互斥——主機上最多「web 一份＋一支批次一份」
  BGE-M3。撞鎖 rc=75：乾淨檔留在 `clean`，掃描與清除照做。
- `scripts/backfill_extraction.py` 正在跑（它也載 BGE-M3、不取 claude 鎖）時只掃描、不入庫。
- unit：`MemoryMax=4G`、`Nice=19`、`IOSchedulingClass=idle`、`EMBED_TORCH_THREADS=4`。不可信 PDF 的檢查與試抽字
  在子行程（`RLIMIT_AS`＝`UPLOAD_PREFLIGHT_MEMORY_MB`，預設 2048；逾時 300 秒；頁數上限 300）。已知風險：
  入庫核心會在主行程再抽一次字，pdfplumber 抽 300 頁的密集文字約 1.7 GB，加上已載入的 BGE-M3 會逼近 4G——
  撞到時 cgroup OOM 砍掉這一輪，殘留回收把它退回 `clean`，同一份檔被中止 3 次就轉 `failed`（不會無限重試）。
  記憶體預估見 `docs/CAPACITY.md`「批次元件：上傳 worker」。

退出碼與告警（unit 以 `OnFailure=report-mark-alert@%n.service` 加 `SuccessExitStatus=2 75` 表達；分界是「會不會自己好」：
會自己好的不告警——timer 每 5 分鐘、`report-mark-alert@` 沒有去重，算成失敗就是每 5 分鐘一則重複通知）：

| 退出碼 | 意思 | 告警 |
|---|---|---|
| 0 | 做完了。另一輪 worker 還在跑、backfill 正在跑（只掃描）、批次斷路器有效（`clean` 標 `llm_breaker` 延後）也是 0 | 否 |
| 1 | 自己壞了：例外、SQL 錯誤、隔離區或乾淨檔目錄的權限 | 是 |
| 2 | 這輪不跑、會自己好：DB 連不上（web 探針經 P5 帶去重告警）。乾淨檔維持 `clean` | 否 |
| 3 | LLM 設定或帳號錯誤、不會自己好：缺金鑰、`/etc/default/report-mark-llm` 讀不到（不存在／權限）、`LLM_PROVIDER` 拼錯、未知模型名、環境檔有重複的鍵（`require_llm_key` 拒跑），以及入庫途中的 401／402／400 升級。乾淨檔維持 `clean` | 是 |
| 75 | 入庫段撞 claude 鎖（sync 或手動 LLM 批次在跑），乾淨檔留到下一輪；掃描與清除照做 | 否 |

3 只在真的有上傳在等（有 `clean`）時才會出現，所以沒有上傳時不會每 5 分鐘告警；有上傳在等而設定沒修好時，
每一輪都會再告警一次（刻意：上傳卡住而沒有人知道比較糟）。處置看 `journalctl -u report-mark-upload` 裡 `[llm-env]`
那一行（缺金鑰時它會說是檔案不存在、權限，還是檔裡沒填）。clamd 由容器探針告警（掃不到只會留在隔離區，不影響
退出碼）。偵測到感染時 worker 自己留痕：稽核 `upload.infected`（actor NULL）、journal 一行 err 優先序（`journalctl -p err -u report-mark-upload`）、
有設 `REPORT_MARK_ALERT_WEBHOOK` 另送 webhook（unit 載入 `/etc/report-mark/alert.env`，URL 經 stdin 給 curl）。

### 安裝（人工；只在要啟用上傳時做，需 sudo）

前提：schema 已在 head（`make schema-version` 回 0）、ClamAV 已照上一節安裝且 `make clamav-smoke` 通過、
`/etc/default/report-mark-llm` 已安裝（見「DeepSeek 金鑰落點與輪替」；白名單是 sync 與這支）。

```bash
# 0) 記憶體：入庫時會多一份 BGE-M3（與 sync 互斥）。可用記憶體常態偏低時先不要開
free -g
# 1) 隔離區與乾淨檔目錄的旋鈕：web（repo 根 .env）與 worker（/etc/default/report-mark-sync）要一樣，或兩邊都留預設
#    （UPLOAD_QUARANTINE_DIR、UPLOAD_CLEAN_DIR；兩者要在同一個檔案系統，搬正才是原子的 os.replace）
# 2) unit（在部署 checkout 的 repo 根）
sudo cp deploy/systemd/report-mark-upload.service deploy/systemd/report-mark-upload.timer /etc/systemd/system/
sudo systemctl daemon-reload
# 3) 先手動跑一輪（旗標還關著：隔離區沒有東西，應該幾秒就結束、rc=0）。是否真的跑過看 invocation，不看 Result=success
BASE=$(bash scripts/verify_oneshot_ran.sh baseline report-mark-upload.service)
sudo systemctl start report-mark-upload.service
bash scripts/verify_oneshot_ran.sh verify report-mark-upload.service "$BASE"
journalctl -u report-mark-upload -n 50 --no-pager
# 4) 維運代理的 catalog 要列 upload（含 run）、polkit 要有它的 start：照「維運代理」的步驟 3、4 重新安裝 catalog 與規則，
#    --check 後重啟 report-mark-ops-agent.service
# 5) 開排程
sudo systemctl enable --now report-mark-upload.timer
# 6) 經同意後才開收檔：repo 根 .env 設 UPLOAD_ENABLED=1，重啟 web
```

驗收：上傳一份真的研報 → 5 分鐘內管理頁看到 `clean` → `draft`（標題、摘要、摘錄稍後補上）；
`make db-audit` 的 `upload_draft_mismatch` 為 0；草稿在一般使用者的檢索、問答、閱讀頁都看不到，發布後才看得到。

### 日常處置

| 狀況 | 看得到什麼 | 處置 |
|---|---|---|
| 一直停在 `quarantined`，`scan_last_error` 是 `connection_refused`／`timeout`／`signatures_stale` | 管理頁掃描器橫幅；容器探針的 clamav 事件 | 見上一節「clamd 掛掉時」；修好後下一輪自動掃 |
| 一直停在 `clean`，`failure_kind=llm_breaker` | journal「LLM 斷路器有效」 | DeepSeek 恢復、斷路器標記過期（30 分鐘）後自動處理；確認恢復可刪 `data/.llm_breaker` |
| 一直停在 `clean`、沒有 failure_kind | journal「claude 鎖被其他 LLM 批次佔用」（rc=75）或「抽取回填正在跑」 | 等對方結束；下一輪自動處理 |
| 一直停在 `clean`，unit 每輪 rc=3 告警 | journal「LLM 設定錯誤」與上方 `[llm-env]` 那一行 | 照 `[llm-env]` 的提示修環境檔或金鑰（見「DeepSeek 金鑰落點與輪替」）；修好後下一輪自動處理 |
| `blocked`、`failure_kind=scan_heuristic` | 稽核 `upload.blocked`；`scan_signature` 是 `Heuristics.*`（例如加密、超過掃描上限） | 規則攔截不是病毒，同一份檔可以在處理掉原因後重新上傳（不會被 422 擋）；證據 30 天後自動刪 |
| `failed`（`tag_failed`／`ingest_error`／`extract_timeout`） | 管理頁失敗頁籤 | 排除原因後在管理頁「重試」（受全站處理中上限） |
| `infected` | 稽核 `upload.infected`、journal err、webhook | 檔案在 `<隔離區>/infected/<upload_id>.bin`（0400），web 沒有任何端點取得回；確認上傳者與來源。30 天後 worker 自動刪檔（`upload.evidence_purged`），DB 那一列永久保留 |
| 下游沒跑完（摘要、標題、摘錄缺） | journal 印出 `data/upload_hashes_retained_<時間>.txt` 與補跑指令 | 照印出的指令以 `--hashes-file` 補跑（三段都冪等） |

### 停用

```bash
# 1) 先停收檔：repo 根 .env 設 UPLOAD_ENABLED=0，重啟 web（之後不會有新檔進隔離區）
# 2) 停排程（已在跑的那一輪會跑完）
sudo systemctl disable --now report-mark-upload.timer
```

停掉之後：隔離區的檔案與 `report_upload` 都保留，已入庫的草稿維持不可見、仍可在管理頁發布或退回（退回後的清除要
worker 跑才會發生）。要恢復就重新 `enable --now` timer。長期移除時另把 catalog 的 upload 與 polkit 的那一行拿掉
重新安裝（「維運代理」步驟 3、4）。

## 安全維運（登入事件、安全告警、保留期清除）

登入、登出與權限提升的每種結果寫進 `research.auth_event`：成功、密碼錯、帳號不存在、停用、TOTP 錯、
第二步暫時憑證失效、被每 IP 限流擋下、非 HTTPS 遭拒、權限提升成功／失敗。設計重點：

- **不存帳號名稱**：帳號存在時只有 UUID，帳號不存在時連 UUID 都沒有（帳號欄常被誤填成密碼）。管理後台顯示的名稱是讀取時
  以 UUID 對帳號現值 join 的，已刪除帳號不顯示。IP、UA 只給 `audit.read` 的管理員看。
- **記錄失敗不影響登入**（`security_ops.record_event` 吞掉例外、3 秒時限）。
- **被攻擊時不放大成 DB 寫入**：被限流、非 HTTPS 遭拒、沒有有效暫時憑證的第二步只在 web 記憶體每 IP 計數，至少 60 秒才寫一列
  彙總（`count`），由下一個登入請求或下一次 `/healthz/security` 落庫；web 重啟時尚未落庫的計數會遺失。
- **不鎖帳號、不封 IP**：`web/auth.py` 的每 IP 失敗限流（5 次／300 秒）原封不動；要擋 IP 走下面的 Cloudflare WAF。
- 保留至少 365 天、納入每日備份；刪帳時刻意保留（只有 UUID、IP、UA）。

### 安全告警（`report-mark-security-health`／`report-mark-security-incident`）

判斷在 web（`/healthz/security`，只回答本機直連、只回 `{"security": state}`），門檻在 `app/config.py`、可在 repo 根 `.env` 覆寫：

| 條件 | 旋鈕（預設） | state | 探針退出碼 → P5 |
|---|---|---|---|
| 稽核雜湊鏈驗證失敗（結果快取 `AUDIT_VERIFY_CACHE_SECONDS`＝3600） | — | `audit_chain_broken` | 1 → CRITICAL |
| `SECURITY_WINDOW_MINUTES`（15）分鐘內權限提升失敗 | `SECURITY_ELEVATE_FAILURE_THRESHOLD`（3） | `elevate_failures` | 2 → CRITICAL |
| 同一帳號「上次登入成功之後」連續失敗（密碼錯、停用、TOTP 錯） | `SECURITY_ACCOUNT_FAILURE_THRESHOLD`（5） | `account_failures` | 2 → CRITICAL |
| 視窗內全站登入失敗（含被限流擋下的請求數） | `SECURITY_LOGIN_FAILURE_THRESHOLD`（20） | `login_failures` | 2 → CRITICAL |
| DB 查不到、逾時、web 連不上、端點不存在 | — | `unknown`（或沒有回應） | 3 → hold（不開也不關） |
| 探針缺 curl | — | — | 4 → WARNING |

多項同時成立回表中最前面那一個。P5 的通知只說「探針回報失敗（exit=1 或 2）」；是哪一條
在探針 unit 的 journal（`journalctl -u report-mark-security-health -n 5` 的 `reason=security_<state>`）與 web 日誌的「安全告警」一行
（只有計數與門檻），細節在管理後台「安全」頁。Slack 只會收到 FIRING／ESCALATED／REMINDER／RESOLVED，不為每筆登入事件發通知；
「有人被授予 super」這類一次性事件只在安全頁的高風險時間線上。

**處置**：

- `login_failures`／`account_failures`：到「安全」頁看可疑 IP 與登入事件。外部單一來源的暴力嘗試 → 照下面的 Cloudflare WAF 手冊封鎖；
  同事自己忘了密碼 → 協助重設（不需要做什麼，失敗停了 15 分鐘後自動 RESOLVED）。帳號被針對時考慮請本人改密碼並開 TOTP。
- `elevate_failures`：已登入的 session 在猜密碼（cookie 外流的典型跡象）。在「安全」頁找出那個 session 撤銷、請本人改密碼。
- `audit_chain_broken`：疑似有人改了 `admin_audit_log`。**不要**重算或修補；先保全（`pg_dump -t research.admin_audit_log`），
  `uv run python scripts/audit_anchor.py --verify-only` 看對不上的 id，對照 NAS 上的 `audit-anchors.jsonl` 與備份，照事故處理。

門檻可依實際分布調（改 `.env` 後重啟 web）。

#### 安裝（人工，需 sudo；只在要啟用時做）

前提：schema 已在 head；該主機的 P5（`report-mark-incident`）已運作、`/etc/report-mark/alert.env` 有 webhook；
`curl -s http://127.0.0.1:8097/healthz/security` 回 `{"security":"ok"}` 或 `unknown`。

```bash
sudo install -m 0644 deploy/systemd/report-mark-security-health.{service,timer} \
    deploy/systemd/report-mark-security-incident.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now report-mark-security-health.timer report-mark-security-incident.timer
```

驗收：`sudo systemctl start report-mark-security-health.service` 後 `systemctl show report-mark-security-health -p ExecMainStatus`
為 0（3＝判不出來，看 web 日誌）、`journalctl -u report-mark-security-health -n 1` 有 `status=ok`；P5 實例跑過一輪後
`journalctl -u report-mark-security-incident -n 2` 是 `action=noop`。狀態目錄是 `data/.incidents-security/`（已在 .gitignore）。
辦公室主機的 ops catalog 已列 `security-health`、`security-incident`（唯讀，不給「立即執行」：探針與 P5 狀態機每 2 分鐘就跑），
裝好後照「維運代理」的步驟 3 重新安裝 catalog、`--check`，再重啟代理。

#### 停用

```bash
sudo systemctl disable --now report-mark-security-incident.timer report-mark-security-health.timer
rm -rf data/.incidents-security   # 可選
```

要停就兩個一起停：只停探針 timer 時 P5 實例會照設計回報 MONITOR_BLIND。

### 保留期清除（`report-mark-security-retention`）

每日 04:45（備份 03:30、稽核錨定 04:15 之後）跑 `scripts/security_retention.py`：刪除超過 `AUTH_EVENT_RETENTION_DAYS`
的 `auth_event`（**下限 365**，設定與清除函式各夾一次）與結束（撤銷或絕對到期）超過 `SESSION_EXPIRED_RETENTION_DAYS`（90）的
`user_session`；仍有效的 session 永遠不刪。分批 commit。DB 不可用 rc=2 → OnFailure 告警。第一次執行時 `user_session` 可能一次刪掉
較多列（它從未清理過），屬預期。

```bash
# 安裝
sudo install -m 0644 deploy/systemd/report-mark-security-retention.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now report-mark-security-retention.timer
BASE=$(bash scripts/verify_oneshot_ran.sh baseline report-mark-security-retention.service)
sudo systemctl start report-mark-security-retention.service
bash scripts/verify_oneshot_ran.sh verify report-mark-security-retention.service "$BASE"
# 辦公室主機的 ops catalog 已列 security-retention（唯讀：會刪資料，不給「立即執行」）；照「維運代理」的步驟 3 重新安裝
# 停用
sudo systemctl disable --now report-mark-security-retention.timer
```

稽核錨定（`report-mark-audit-anchor`，安裝見「備份與還原」）每次正式執行後寫一份 `data/health/audit_anchor.json`
（給安全頁的「最後錨定」；寫不進去不改退出碼）。安全頁超過 48 小時沒有新結果會顯示「過期」；沒裝錨定的主機顯示沒有結果。

### Cloudflare WAF 自訂規則（封鎖可疑 IP 的操作手冊）

本站刻意不做 app 層 IP 封鎖（與 Cloudflare、nginx `limit_req` 重疊，而且封錯會把同事擋在門外）。安全頁的「可疑 IP」只彙整；
要擋人在 Cloudflare 做。nginx 以 `CF-Connecting-IP` 還原真實 IP，所以安全頁顯示的 IP 就是 Cloudflare 看到的來源 IP。

1. 在安全頁確認：失敗與被限流的次數、涉及幾個帳號、有沒有成功過（`成功` 不是 0 時先當成帳號可能已被入侵處理：撤銷該帳號的
   session、重設密碼）。**確認不是公司或同事的出口 IP**（辦公室、VPN、行動網路）。
2. Cloudflare 儀表板 → 該網域 → **Security → WAF → Custom rules → Create rule**：
   - Rule name：`block-suspicious-<IP>-<日期>`（之後才找得到、刪得掉）。
   - Expression（Edit expression）：單一 IP `(ip.src eq 203.0.113.9)`；多個 `(ip.src in {203.0.113.9 198.51.100.0/24})`。
     只想擋登入可再加 `and http.request.uri.path eq "/login"`。
   - Action：外部陌生來源用 **Block**；不確定是不是自己人時用 **Managed Challenge**（真人過得去、腳本過不去）。
   - Deploy。
3. 驗證：Cloudflare **Security → Events** 看到該規則的命中；本站安全頁該 IP 的事件不再增加；`/healthz/security` 在視窗（15 分鐘）
   過後回 `ok`，P5 送 RESOLVED。
4. 記錄：在事件紀錄寫下 IP、理由、規則名稱與預計移除日期。規則**不會自己過期**——兩週後回頭檢查，
   沒有再命中就刪掉（Custom rules → 該規則 → Delete），免得清單越積越多、哪天擋到換了 IP 的同事。

持續性的撞庫（大量不同 IP、低頻）不適合逐條封：改在 Cloudflare 對 `/login` 的 POST 加 **Rate limiting rule**（例如同一 IP
10 分鐘 20 次 → Managed Challenge），並請同事開 TOTP。區網直連（不經 Cloudflare）不受這些規則影響，本來就被視為可信。

## 使用分析每晚彙總（report-mark-analytics-rollup）

管理後台「使用分析」（`/api/admin/analytics/*`）最近 `ANALYTICS_LIVE_WINDOW_DAYS`（90）天即時查 `qa_log`／`usage_counter`，
更早的日子**只讀** `research.analytics_daily`。`qa_log` 會被使用者硬刪、`usage_counter` 只留 400 天，
所以每一天都必須在離開即時窗期前彙總過，否則那天在長期趨勢上永遠是「沒有資料」（`has_data=false`）。

`report-mark-analytics-rollup.timer` 每天 02:20 跑 `scripts/analytics_rollup.py`（純 SQL、零 LLM、不載嵌入模型）：
覆寫昨天，並補齊窗期內還沒有彙總標記（`rollup.computed`）的日子——機器關機錯過幾晚也追得上。一天一個交易、先刪後寫，
重跑同一天結果相同；同一天的並行執行以 advisory lock 排隊。`analytics_daily` 沒有 user_id、刪帳後保留，
列入備份。退出碼：0 正常、1 計算／寫入失敗或參數錯誤（告警）、2 DB 不可用（`SuccessExitStatus=2`，DB 故障由 P5 告警）。

### 安裝（人工，需 sudo；只在要啟用時做）

```bash
# 前提：schema 已在 head（make schema-version 回 0）
# 1) 第一次啟用：先試算再補滿窗期（已彙總的日子不會被覆寫）
uv run python scripts/analytics_rollup.py --dry-run
uv run python scripts/analytics_rollup.py
# 2) 排程
sudo install -m 0644 deploy/systemd/report-mark-analytics-rollup.service deploy/systemd/report-mark-analytics-rollup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now report-mark-analytics-rollup.timer
```

驗收：`systemctl list-timers report-mark-analytics-rollup.timer` 有下一次觸發；跑過一輪後
`systemctl show report-mark-analytics-rollup -p Result,ExecMainStatus`（判準見「oneshot 的手動驗證」），並以管理員打
`/api/admin/analytics/overview?since=<91 天前>`，`range.spans` 出現 `rollup` 段、那段的 `daily[].has_data` 為 true。
辦公室主機的 ops catalog（`deploy/ops/services.prod.toml`）已列 `analytics-rollup`（含「立即執行」：run 不收參數，
只會是預設模式，不會觸發 `--force` 的重算）：裝好 unit 後照「維運代理」的步驟 3、4 重新安裝 catalog 與 polkit、
`--check`，再重啟代理。

**回填**：`--backfill N` 從 `qa_log` 補最近 N 天裡缺的日子，**預設不覆寫已彙總的日子**——重算會讓刪帳或使用者刪歷史之前
已保留的匿名彙總縮水。確定要重算（例如修了指標的 bug）才加 `--force`，或以 `--day YYYY-MM-DD` 重算單一天。

### 停用

```bash
sudo systemctl disable --now report-mark-analytics-rollup.timer
```

停掉之後最近 90 天照常即時顯示；之後離開窗期的日子沒有彙總，長期趨勢在那段顯示「沒有資料」。已寫入的 `analytics_daily`
保留。恢復時重新 `enable --now`，並手動跑一次 `scripts/analytics_rollup.py --backfill 90` 補回還在窗期內的缺口
（已離開窗期、`qa_log` 仍在的日子也能以更大的 N 補，但被硬刪的問答補不回來）。

## 來源主機信標（辦公室主機的 NAS → R2 inbox，由 EC2 監看）

正式環境（EC2）碰不到 NAS，新研報全靠辦公室主機的 sync 每輪 rsync 之後推到 R2 的 `inbox/`。那台停機、WSL 被收掉、sync 的
timer 停了、NAS 掛不上、推送失敗——EC2 看到的都只是「inbox 沒有新檔」：自己的 sync 每 3 小時照樣 rc=0、心跳照樣更新、
後台全綠，與「這幾天沒有新稿」無從分辨；而辦公室主機整台停掉時，它自己的告警一則也送不出來。

做法是來源那台主動發布、收檔那台判讀（`app/services/host_beacon.py`，不另開網路通道——兩台本來就共用同一個 bucket）：

| 主機 | 元件 | 做什麼 |
|---|---|---|
| 辦公室主機（來源） | `scripts/sync_new_reports.sh` | 每輪把來源段的結論寫進 `data/.sync_feed_state`（`result`＝`ok`／`mount_failed`／`rsync_failed`，`push`＝`off`／`none`／`ok`／`failed`）。只記掛載、rsync、推送；匯入與下游的失敗由這台自己的心跳與 freshness 負責 |
| 辦公室主機（來源） | `report-mark-beacon.timer`（每 10 分鐘） | `scripts/publish_host_beacon.py` 把上面那個檔、本輪狀態、心跳、還沒補傳的 `data/inbox_delta_retained_*.txt` 數量，加上維運代理回報的服務清單，覆寫到 R2 的 `beacons/<HOST_BEACON_NAME>.json` |
| EC2（收檔） | web 的 `/healthz/feed` | 讀 `HOST_BEACON_WATCH` 指定的信標並判讀；信標的年齡以 R2 的 `LastModified` 計（不信任發布端的時鐘） |
| EC2（收檔） | `report-mark-feed-health`／`report-mark-feed-incident` | 探針把 state 轉成退出碼，P5 做去重並送 Slack；提醒間隔 3 小時 |
| EC2（收檔） | 管理後台「維運 → 來源主機」、依賴圖的 `office-feed` | 看是哪幾條成立、來源段最近一輪的結果與那台的服務清單（`GET /api/admin/ops/remote-hosts`） |

### 狀態與處置

| state | 意思 | 探針退出碼 → P5 | 先看哪裡 |
|---|---|---|---|
| `stale` | 信標超過 `HOST_BEACON_STALE_MINUTES`（45）分鐘沒更新：辦公室主機失聯 | 1 → CRITICAL | 那台是否開機、WSL 是否在跑（`wsl -l -v`）、`systemctl list-timers report-mark-beacon.timer`、`journalctl -u report-mark-beacon -n 20`（R2 寫不進去也是這個狀態） |
| `missing` | bucket 裡沒有這個信標 | 1 → CRITICAL | 辦公室主機的 `HOST_BEACON_NAME` 與 EC2 的 `HOST_BEACON_WATCH` 是否同名；`report-mark-beacon.timer` 有沒有裝、有沒有跑過 |
| `invalid` | 讀得到但看不懂（版本不符、格式壞掉） | 2 → CRITICAL | 兩台的程式版本是否差太多（信標格式 `v` 不同）；先升級收檔那台 |
| `timer_inactive` | `report-mark-sync.timer` 不在 active | 2 → CRITICAL | 辦公室主機 `sudo systemctl enable --now report-mark-sync.timer` |
| `sync_stalled` | 來源段超過 `HOST_BEACON_FEED_STALE_HOURS`（7）小時沒有新結果（sync 每 3 小時一輪） | 2 → CRITICAL | `data/sync_run_<日期>.log`：是不是 PID lock 一直被佔用（「已有 sync 在跑」）、unit 起不來、或 `systemctl is-system-running` 卡在 stopping |
| `source_failed` | 最近一輪 NAS 掛載或 rsync 失敗 | 2 → CRITICAL | `data/sync_run_<日期>.log` 的「掛載失敗」或「rsync rc=」；NAS 是否在線、Windows 的網路磁碟憑證、`sudo -n /usr/local/sbin/mount-nas-research` |
| `push_disabled` | 來源是 NAS 卻沒開 `SYNC_INBOX_PUSH` | 2 → CRITICAL | `/etc/default/report-mark-sync` 補回 `SYNC_INBOX_PUSH=1`（下一輪生效；這段期間落地的檔不會自己補推，見下） |
| `push_failed` | 最近一輪推送失敗，或還有沒補傳的 delta | 2 → CRITICAL | 照 `data/sync_run_<日期>.log` 印出的指令補傳：`uv run python scripts/r2_inbox.py push --delta data/inbox_delta_retained_<時間>.txt`，**成功後刪掉那份 delta**（檔還在就會一直是 `push_failed`） |
| `round_aborted` | 最近一輪被中止（SIGTERM、SIGKILL、斷電） | 2 → CRITICAL | rsync 可能已把部分新檔落到本地，而 `--size-only` 讓下一輪 delta 不再列出它們：照 `data/unit_failures.log` 的 `sync_round(aborted)` 那筆重放 delta（或 `--all-local`），再以同一份 delta 補推 inbox。下一輪正常跑完後這個狀態自己消失，但漏掉的檔不會自己回來 |
| `unknown` | R2 讀不到或逾時、web 連不上、端點不存在 | 3 → hold（不開也不關） | R2 與 web 各有自己的事件（`report-mark-incident` 的 exit 6、1） |
| `disabled` | web 沒設 `HOST_BEACON_WATCH` | 4 → WARNING | 補設定並重啟 web；這台不需要監看就移除這組 unit |

多項同時成立時 state 是表中最前面那一個（`stale` 成立時不再往下判：信標裡都是舊聞）。P5 的通知只說「探針回報失敗（exit=1 或 2）」；
是哪一條在探針 unit 的 journal（`journalctl -u report-mark-feed-health -n 5` 的 `reason=feed_<state>`）、web 日誌的「來源主機信標告警」
一行，全部成立的項目與來源段的細節在「維運 → 來源主機」。恢復是自動的：條件消失後下一輪探針回 0，P5 送 RESOLVED。

`push_disabled`、`push_failed`、`round_aborted` 期間落地在辦公室主機的檔不會自己補推到 inbox。補推以 delta 為單位：
`uv run python scripts/r2_inbox.py push --delta <那一輪的 delta>`；沒有 delta 可用時，以檔名清單自己寫一份（每行一個相對於
`研報自動匯入/` 的路徑）。EC2 下一輪 sync 會把 inbox 裡本地沒有的檔拉回去。

**量不到的**：信標只證明「來源段有在跑、有結論」。NAS 上游（LINE bot 沒把檔放進 NAS）不在這裡，那是辦公室主機
`linebot-health` 的事；EC2 自己拉取失敗（`r2_inbox(pull)`）是 EC2 的 `data/unit_failures.log` 與心跳。

### 安裝（人工，需 sudo；先辦公室主機、後 EC2）

順序有意義：信標要先存在，EC2 的探針第一輪才不會以 `missing` 開一個事件；sync 也要跑過一輪，`data/.sync_feed_state`
才會有（否則信標發布後 7 小時會判成 `sync_stalled`）。

```bash
# ── 1) 辦公室主機（部署目錄 repo 根）
#    a. 在 /etc/default/report-mark-sync 加一行（名稱兩台要一致；小寫英數與 -）：
#         HOST_BEACON_NAME=office
#    b. 先試跑，確認內容合理（--dry-run 不寫 R2；手動跑不會載入環境檔，所以把要回報的設定帶在命令列）：
HOST_BEACON_NAME=office SYNC_INBOX_PUSH=1 uv run python scripts/publish_host_beacon.py --dry-run
#    c. 安裝並啟用
sudo install -m 0644 deploy/systemd/report-mark-beacon.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now report-mark-beacon.timer
sudo systemctl start report-mark-beacon.service && journalctl -u report-mark-beacon -n 3 --no-pager   # 「已發布 beacons/office.json」
#    d. 維運代理的 catalog 要列 beacon（唯讀）：照「維運代理」的步驟 3 重新安裝 catalog、--check，再重啟代理。
#    e. 等下一輪 sync（每 3 小時整點）跑過，或確認 data/.sync_feed_state 已存在。

# ── 2) EC2（部署 checkout repo 根）
#    a. repo 根 .env 加一行並重啟 web：
#         HOST_BEACON_WATCH=office
curl -s http://127.0.0.1:8097/healthz/feed          # {"feed":"ok"}；disabled＝.env 沒生效
#    b. 安裝探針與事件（代換使用者與路徑），兩組一起開
sudo deploy/install_units.sh --user ubuntu --root /home/ubuntu/report-mark \
    report-mark-feed-health.service report-mark-feed-health.timer \
    report-mark-feed-incident.service report-mark-feed-incident.timer
sudo systemctl enable --now report-mark-feed-health.timer report-mark-feed-incident.timer
#    c. 維運代理的 catalog 要列 feed-health、feed-incident 與外部依賴 office-feed：照「維運代理」的 EC2 一節重新安裝
#       services.staging.toml、--check，再重啟 report-mark-ops-agent-staging.service。
```

驗收：EC2 上 `sudo systemctl start report-mark-feed-health.service` 後 `systemctl show report-mark-feed-health -p ExecMainStatus`
為 0、`journalctl -u report-mark-feed-health -n 1` 有 `status=ok`；P5 實例跑過一輪後 `journalctl -u report-mark-feed-incident -n 2`
是 `action=noop`；管理後台「維運 → 來源主機」看得到辦公室主機的服務清單與來源段結果，依賴圖上 `office-feed` 是正常。
要演練告警：在辦公室主機 `sudo systemctl stop report-mark-beacon.timer`，45 分鐘後 EC2 應收到 `feed` 的 FIRING（`reason` 是
`probe_exit_1`），重新 `start` 後下一班發布、下一輪探針就 RESOLVED。狀態目錄是 `data/.incidents-feed/`。

前提同安全告警那組：EC2 的 P5 要已運作、`/etc/report-mark/alert.env` 有 webhook。EC2 沒有監控收集與事件投影，這組事件只送
Slack、不進後台的「事件」分頁。

### 停用

```bash
# EC2：先停事件再停探針（只留 P5 在跑會回報 MONITOR_BLIND）
sudo systemctl disable --now report-mark-feed-incident.timer report-mark-feed-health.timer
# 辦公室主機
sudo systemctl disable --now report-mark-beacon.timer
```

只停辦公室主機那一支而 EC2 的探針還開著，45 分鐘後就是一則 `stale` 告警——那是設計，不是誤報。信標物件（`beacons/`）不必清，
也不要對 `beacons/` 設 R2 生命週期規則（inbox 的規則只套 `inbox/`）。

## 功能開關與啟用矩陣

功能旗標（`app/services/feature_flags.py`、管理後台「功能旗標」頁、`/api/admin/flags*`）讓管理員在**環境變數允許的
範圍內**暫停或限定派生功能，不必改環境檔、不必重啟。語意：

- **實際值＝環境變數上限 AND DB 覆寫**。環境變數代表「能力已安裝／允許」；`research.feature_flag` 只存覆寫，沒有
  覆寫＝registry 預設。覆寫只能在上限之下關閉或限定給角色（`admin`／`user`）與指定帳號，上限關時一律關。
- **DB 讀不到時退回 registry 預設**：派生功能（agentic、忠實度、rerank、可信資料、收檔）預設開＝回到只看
  環境變數的行為；`quota.enforce` 預設關＝回到影子模式；`ask.web_search` 預設關＝不開放網搜（網搜沒有後端）。所以**安全閘門一律不是旗標**：`ADMIN_MFA_REQUIRED`、
  `DEV_NO_AUTH`、`REPORT_MARK_*`、模型與 provider、`OBJECT_STORAGE_MODE`／`R2_*`、併發閘與連線池、`UPLOAD_MAX_BYTES`、
  ClamAV、`SYNC_*_LIMIT`、`EXTRACTOR` 都只在環境檔決定。
- 快取 5 秒（`FEATURE_FLAG_CACHE_SECONDS`），同一行程的寫入立即失效；web 只有一個 worker，所以等於立即生效。前端
  `/api/features` 另有 60 秒的 staleTime（視窗回焦點時重抓）。
- 旗標只在 web 的請求路徑上判斷；sync、上傳 worker、`eval/`、`scripts/judge_agreement.py` 不讀旗標。
- 寫入（單一旗標、匯入）要 `ops.operate`＋重新驗證，每個實際變更同交易寫稽核 `flag.update`（操作紀錄頁看得到）。
  `feature_flag` 在備份清單內：**還原後要人工確認旗標狀態**（見「備份與還原」）。

### 啟用矩陣

測試環境＝辦公室主機、正式環境＝EC2（`research.tingfong.com`，見 AGENTS.md「環境角色」）。「上限」是兩台主機各自的
repo 根 `.env`；「覆寫」是各自 DB 裡的 `feature_flag`。

| 旗標 | 上限（環境變數，程式預設） | 測試環境建議 | 正式環境建議 | 何時調整 |
|---|---|---|---|---|
| `ask.web_search` | `ASK_ENABLE_WEB`（0；registry 預設也是**關**） | 上限 0；不設覆寫 | 上限 0；不設覆寫 | 網搜沒有後端：上限開了只會讓時效題一律婉拒、開了網搜的主答失敗（啟動自檢記 ERROR）。要上限開 AND 覆寫開，問答輸入框才出現網搜開關。後端完成後：測試環境上限設 1、覆寫限定 `admin` 試用 → 正式環境上限設 1、覆寫開啟（需要時同樣先限定） |
| `uploads.intake` | `UPLOAD_ENABLED`（0） | 上限 0，直到該主機裝好 ClamAV 與上傳 worker 並經同意（本檔「ClamAV」「上傳 worker」） | 同左 | 上限 1 之後，維護或掃毒有狀況時用覆寫「全站關閉」暫停收檔（503 `uploads_disabled`）；審核端點不受影響 |
| `qa.agentic` | `QA_AGENTIC_ENABLED`（1） | 上限 1；不設覆寫 | 上限 1；不設覆寫 | DeepSeek 延遲高或費用異常時覆寫「全站關閉」降級（只用第一輪檢索作答） |
| `qa.faithfulness` | `ASK_FAITHFULNESS_ENABLED`（1） | 上限 1；不設覆寫 | 上限 1；不設覆寫 | judge 異常或要省費用時覆寫關閉；使用者看不到差別（抽查在 done 之後、不上 UI） |
| `ask.rerank` | `ASK_RERANK_ENABLED`（1） | 上限 1；不設覆寫 | 上限 1；不設覆寫 | CPU 吃緊、rerank 頻繁逾時時覆寫關閉。注意：關覆寫**不會**釋放 reranker 模型的記憶體（啟動時依上限預熱），要省記憶體得改上限並重啟 |
| `trusted_data` | `TRUSTED_DATA_ENABLED`（1） | 上限 1；不設覆寫 | 上限 1；不設覆寫 | 受信任資料來源出問題時覆寫關閉；時效題回到既有的安全婉拒 |
| `quota.enforce` | `QUOTA_ENFORCE`（0） | 上限 0（影子模式） | 上限 0（影子模式） | 依影子模式觀察到的 P50／P95 決定額度後：先在測試環境上限設 1、覆寫限定測試帳號驗證 429，再到正式環境上限設 1、覆寫「全站開啟」 |

新主機或調整上限時，核對該主機 `.env` 的這七個變數與上表一致；各主機目前的值記在 `docs/DEPLOYMENT_STATUS.md`。

### 匯出匯入（測試環境 → 正式環境）

只做 API 與頁面，不自動同步。匯出檔只含覆寫（不含環境變數上限），使用者以**帳號名稱**表示（兩台主機的帳號 UUID
不同），已刪帳的不匯出。

1. 測試環境的「功能旗標」頁按「匯出設定（JSON）」（或 `GET /api/admin/flags/export`，`ops.read`），得到
   `report-mark-flags-<環境>-<日期>.json`。檔案列出每個登記旗標；`override: null`＝沒有覆寫。
2. 只想搬部分旗標時，從檔案的 `flags` 刪掉不搬的項目：**沒列出的旗標匯入時不會被動到**；列出且 `override: null`
   的會把目標的覆寫刪掉（回到預設）。
3. 正式環境先「匯出設定」留一份底（回滾用），並確認上限（`.env`）已照矩陣設定——上限關的旗標匯入了也不會生效。
4. 正式環境「匯入檔案並預覽」：後端以 dry-run 回每個旗標的動作（新增／更新／刪除覆寫／不變）與前後值，以及錯誤：
   `unknown_key`（這個環境的程式沒有那個旗標）、`unknown_user`（找不到那個帳號名稱）、`duplicate_key`、
   `invalid_input`。有任何錯誤就不能套用；旗標清單版本（`registry_version`）不同只提示，代表兩邊程式版本可能不一致。
5. 確認差異後按「套用」（`ops.operate`＋重新驗證）。後端在同一筆交易重算差異，有錯誤整份不寫（422
   `flag_import_invalid`）；成功時每個變更各寫一筆 `flag.update`（detail 的 `via` 是 `import`）。
6. 驗收：「功能旗標」頁的實際值、操作紀錄頁的 `flag.update`；要看某位使用者的實際值，請他打開問答頁（或
   `GET /api/features`，只回本人的值）。

回滾：在正式環境匯入第 3 步留底的檔案（同樣先預覽）。單一旗標要回到預設，在頁面按「恢復預設」
（`DELETE /api/admin/flags/{key}`）。

## 部署順序

把含 schema 變更與管理後台排程元件的版本放上任何一台主機（新主機、重裝，或之後帶新 revision 的版本），都照這個順序。
這裡只排順序與前提，每一步的指令、驗收與停用都在各自的章節，不在這裡重複。

**前提**：先確認目標主機（`AGENTS.md`「環境角色與資料安全」：EC2 是正式環境、辦公室主機是測試環境）。站序是 devdb 演練 →
測試環境 → 正式環境，每站先備份。

1. **先套 schema、再換程式。** migration 只新增表與欄位時，舊程式在新 schema 的庫上照常可跑，新程式在舊 schema 的庫上會壞。
   - 備份：測試環境照「備份與還原」先跑一次 `make db-backup`；正式環境先做 RDS 手動快照。
   - 部署 checkout 先更新到新版（migration 檔在新版裡），**web 先不要重啟**：執行中的仍是舊程式。
   - 套之前 `make schema-version` 應回 1（落後），而且零 drift：辦公室主機跑 `make schema-check`；EC2 的 app 帳號沒有
     CREATEDB，照「schema 與版本 drift 每日檢查」的「正式環境（EC2 的 RDS）」一節以 master 帳號建基準。
   - 套 migration（逐字確認目標）：DB 在容器的辦公室主機 `make schema CONFIRM=<host:port/db>`；EC2 的 RDS 用
     `REPORT_MARK_MIGRATE_CONFIRM=<host:port/db> uv run alembic upgrade head`（`make schema` 會先啟動 Docker 的 PostgreSQL，
     不適用 RDS）。套完 `make schema-version` 回 0、再做一次零 drift 比對。
   - 每日 schema 檢查（`report-mark-schema-check`）已啟用的主機，套 schema 與換程式要在同一個維護窗口內做完，
     否則當天的檢查會以版本不一致告警。
2. **換程式**：`make build-web`，緊接著重啟 web（部署目錄的 `frontend/dist` 不存在時 SPA 回 503）。驗收：
   `curl -s http://127.0.0.1:8097/healthz` 回 `{"status":"ok"}`、`/healthz/security` 回 `{"security":"ok"}` 或 `unknown`。
   備份清單的變動由部署目錄裡的 `scripts/db_backup.sh` 直接生效，備份 unit 不用重裝。
3. **安裝或更新 unit**（辦公室主機直接 `install` 到 `/etc/systemd/system/`；EC2 用 `deploy/install_units.sh --user ubuntu
   --root /home/ubuntu/report-mark` 代換使用者與路徑）。repo 裡的 unit 檔有改就重裝那一支。新主機上管理後台元件的順序：
   1. `report-mark-db-snapshot`：先 `--dry-run` 看權限不足的段落，見「DB 統計快照與慢查詢」。
   2. `report-mark-analytics-rollup`：第一次先試算、再補滿 90 天窗期，然後才開排程，見「使用分析每晚彙總」。
   3. `report-mark-security-health` 與 `report-mark-security-incident`：兩個一起裝、一起開（只有 P5 實例在跑、探針沒開時，
      它會照設計回報 MONITOR_BLIND），見「安全維運」的安全告警一節。前提是該主機的 P5（`report-mark-incident`）已經在運作、
      `/etc/report-mark/alert.env` 有 webhook。
   4. `report-mark-security-retention`：排在備份與稽核錨定之後，見「安全維運」的保留期清除一節。
4. **ops catalog 與 polkit 重新安裝、重啟維運代理**（「維運代理」的步驟 3、4；EC2 照該節的 EC2 一段，重啟
   `report-mark-ops-agent-staging.service`）。`ops_agent/`、catalog 或 polkit 規則有任何更新都要重做；catalog 只列該主機
   已安裝的 unit。還沒裝代理的主機，第一次安裝時直接用新版 catalog。
5. **確認開關維持預設**（該主機的 repo 根 `.env`）：
   - `ADMIN_MFA_REQUIRED` 不設或設 0：預設關，TOTP 依個人設定開關。這是環境變數，不是 DB 旗標；要開啟，先確認每位管理員
     都已設定 TOTP。
   - `QUOTA_ENFORCE` 不設或設 0：配額影子模式，只計數、記錄「本來會擋」，不回 429。
   - 功能旗標：`feature_flag` 沒有任何覆寫時，`ask.web_search` 關、其餘派生功能開，實際值仍受環境變數上限限制。
     七個上限變數照「功能開關與啟用矩陣」核對；部署時不設任何覆寫。
6. **`pg_stat_statements` 不在這個順序裡**：它是獨立的維護步驟，見「DB 統計快照與慢查詢」的「啟用 pg_stat_statements」
   （重建或換主機時用）。沒做時「慢查詢」區塊只顯示原因，其他頁面不受影響。

### v2 功能的啟用矩陣（測試環境與正式環境）

測試環境＝辦公室主機（`research.kashionzarchive.com`），正式環境＝EC2（`research.tingfong.com`）。結構上的差異：EC2 的 DB 是
RDS（沒有容器、app 帳號權限較窄）、碰不到 NAS（沒有應用層備份與稽核錨定；tombstone 改寫本機磁碟）、不以容器跑任何東西。EC2 沒裝
哪些 unit 與連帶的後果列在 `README.md`「非辦公室主機（EC2）」，查證日期與現況在 `docs/DEPLOYMENT_STATUS.md`；功能旗標各項的
上限與覆寫建議另見「功能開關與啟用矩陣」。

| 項目 | 怎麼啟用 | 測試環境（辦公室主機） | 正式環境（EC2） | 前提與差異 |
|---|---|---|---|---|
| schema migration | 依 DB 類型執行 | `make schema CONFIRM=<host:port/db>` | `REPORT_MARK_MIGRATE_CONFIRM=<host:port/db> uv run alembic upgrade head`（RDS） | 一律先於換程式 |
| 使用量收集（`usage_daily`、`usage_counter`） | 隨 web | 開 | 開 | 不必安裝；只記次數與主題彙總，不記搜尋字串 |
| 使用分析頁＋`report-mark-analytics-rollup` | 安裝 unit | 安裝 | 安裝 | 沒裝時最近 90 天照常即時顯示，更早的日子是「沒有資料」 |
| 安全頁（登入事件、session、高風險時間線） | 隨 web | 開 | 開 | 「最後錨定」要有 `report-mark-audit-anchor`（寫 NAS）：EC2 沒有，顯示沒有結果 |
| `report-mark-security-health`＋`report-mark-security-incident` | 安裝 unit | 安裝 | 安裝 | 要 P5 已運作、`alert.env` 有 webhook；EC2 沒有事件投影，事件只送 Slack |
| `report-mark-security-retention` | 安裝 unit | 安裝 | 安裝 | EC2 沒有應用層備份：`auth_event` 只靠 RDS 自動備份 |
| 配額（`QUOTA_ENFORCE`） | 環境變數 | 不設（影子模式） | 不設（影子模式） | 決定額度後先在測試環境驗 429 |
| 管理員 TOTP 強制（`ADMIN_MFA_REQUIRED`） | 環境變數 | 不設（關） | 不設（關） | 依個人設定開關；開啟前先確認每位管理員都已設定 TOTP |
| 功能旗標頁與 `/api/features` | 隨 web | 開，不設覆寫 | 開，不設覆寫 | 各旗標的上限見「功能開關與啟用矩陣」 |
| DB 即時快照（`/api/admin/db/overview`） | 隨 web | 開 | 開 | RDS 帳號沒有 `pg_monitor`／`pg_read_all_stats` 時，相關段落顯示「權限不足」 |
| `report-mark-db-snapshot`（DB 趨勢） | 安裝 unit | 安裝 | 安裝 | 權限不足的段落記在 `stats.errors`、趨勢上是空點 |
| 慢查詢（`pg_stat_statements`） | 獨立維護步驟 | 預載＋重啟容器 | master 帳號 `CREATE EXTENSION`（參數群組已預載） | 不綁版本部署；沒做時頁面只顯示原因 |
| 主機、排程工作、事件三頁（`service_observation`、`job_execution`、`incident`） | 安裝監控收集與匯入（`report-mark-metrics`、`report-mark-load-observations`） | 安裝 | 不裝 | 收集器讀 docker 的 cgroup；維運代理的服務清單沒有 `load-observations` 時，管理後台不列這三頁 |
| 研報上傳（`UPLOAD_ENABLED`） | ClamAV＋上傳 worker＋環境變數 | 安裝；收檔（設 1）經同意才開 | 維持 0 | EC2 沒有 ClamAV 與上傳 worker |
| 帳號刪除的執行與重放 | 安裝 unit＋`REPORT_MARK_BACKUP_DIR` | 安裝（tombstone 寫 NAS） | 安裝（tombstone 寫本機 `/var/lib/report-mark/tombstones`） | EC2 的 DB 在 RDS，快照還原不會動到本機磁碟；EC2 整台遺失時 tombstone 跟著消失 |
| ops catalog 與 polkit | 重新安裝 catalog 與規則 | `services.prod.toml` | `services.staging.toml` | 維運代理要已安裝；「立即執行」在 EC2 只給 freshness、audit、db-snapshot、analytics-rollup |
