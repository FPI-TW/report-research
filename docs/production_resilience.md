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

**改任何一項併發都要重算這條式子**：加 `uvicorn --workers`、提高 `REPORT_SEMAPHORE`、放寬 `web/routers/ask.py` 裡寫死的 `_ASK_SEMAPHORE`(3)、或新增一支長跑批次腳本。

> 這裡沒有壓測數據。上面的數字是逐條數出來的上界，不是實測——要留下「幾個併發使用者會打滿」這種數字之前，得真的壓一次（例如 15 個並行 `/api/search` 觀察 `pg_stat_activity`）。

### 逾時的兩個非對稱決定

- `DB_STATEMENT_TIMEOUT_MS` 預設 **60000**，刻意不取架構檢視建議的 15000：雷達目錄的兩次全表 unnest、overview 一題掃 7 次同一母體、閱讀頁 `similar` 拉高 `ef_search` 的 HNSW 掃描都天生偏慢，15s 有把正常功能打掛的實質風險。60s 比任何已知查詢高一個數量級，仍舊把「無上限」變成有上限。
- `DB_IDLE_TX_TIMEOUT_MS` 預設 **0（關）**，這是刻意的：`scripts/sync_new_reports.py` 先 `report_exists()` 開了交易，接著才 spawn `claude` CLI 標註（硬逾時 150s）與跑 BGE-M3 嵌入（大檔可達數分鐘），中間完全沒有 commit。設了它＝每 3 小時一次的生產同步會把報告靜默丟進 `data/sync_failures.log`。要開就**只在 web 的 `.env` 開**——批次腳本不讀 repo 根的 `.env`（sync unit 走 `/etc/default/report-mark-sync`），這個切分是天然的。

### 維運長查詢的豁免

匯入流程最後那句 `ANALYZE research.report_chunk` 要在 70 萬列 × `vector(1024)` 上抽樣，可能久於 60s，而它是整條匯入的**最後一步**——被 timeout 砍掉時前面的資料都已 commit，症狀只有 planner 統計靜默過期。三個呼叫點（`scripts/ingest_all.py`、`scripts/run_ingest.py`、`scripts/sync_new_reports.py`）因此都先呼叫 `db.relax_statement_timeout(session)`，值取自 `DB_MAINTENANCE_STATEMENT_TIMEOUT_MS`（預設 0＝不限）。

它用的是 `SET LOCAL` 而非 `SET`：連線是池化的，而 SQLAlchemy 預設的 `reset_on_return="rollback"` **不會**還原 GUC——`SET` 的豁免會跟著連線流到下一個借用者身上，等於把線上查詢的上界一起拆了。`SET LOCAL` 只活到本交易結束。

`tests/test_db_engine.py` 靜態守住「每個 `ANALYZE research.report_chunk` 前面都有 `relax_statement_timeout()`」——漏掉一處的症狀是統計靜默過期，沒有例外也沒有日誌，只有檢索計畫慢慢變差，行為測試照不到。

## 這一輪刻意沒做

- **外部 uptime 監控**：`/healthz` 是給它用的介面，但要接哪一家（UptimeRobot／自架）是部署決策，不該由一次程式碼改動偷渡。
- **把 unit 失敗顯示在監控頁**：`data/unit_failures.log` 已是可讀來源，但要不要進 UI 屬產品決策。
- **告警投遞管道**：webhook 已留 opt-in 掛勾，設不設由部署端決定。
