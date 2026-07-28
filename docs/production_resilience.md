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

## 這一輪刻意沒做

- **外部 uptime 監控**：`/healthz` 是給它用的介面，但要接哪一家（UptimeRobot／自架）是部署決策，不該由一次程式碼改動偷渡。
- **把 unit 失敗顯示在監控頁**：`data/unit_failures.log` 已是可讀來源，但要不要進 UI 屬產品決策。
- **告警投遞管道**：webhook 已留 opt-in 掛勾，設不設由部署端決定。
