# LineBot 常駐與監控

`C:\LineBot\line_file_bot.py`（辦公室主機的 Windows 側，`127.0.0.1:8000`）把 LINE 群組裡的研報下載到 NAS `\\192.168.1.100\投資研究處\02.研究資源\研報自動匯入`，而那個目錄正是 `scripts/sync_new_reports.sh` 的 rsync 來源。**它斷掉等於這條供稿斷掉**：它運作時約佔每日新進檔案的三成（2026-07-17 至 08-13 實測 32%，單日最高 100%）。

另一條供稿管道照常送檔，所以**不能拿「有沒有新研報」當它的存活指標**，`make freshness` 的語料閘也刻意不把「沒有新研報」算成停更——它要有自己的探針。2026-08-13 起它靜默停擺 18 天沒有任何告警，就是這個盲區。

只有辦公室主機有這套東西；正式環境（EC2）不裝 `report-mark-linebot-*`，它的新研報經 R2 inbox 從辦公室主機推上去。

## 組成

| 元件 | 位置 | 角色 |
|---|---|---|
| bot | `C:\LineBot\line_file_bot.py`（**不在本 repo**） | 收 LINE webhook、下載檔案、發佈到 NAS；`/health` 回報 NAS 是否可寫 |
| 看門狗 | `C:\LineBot\watchdog.ps1` ＋工作排程器任務「LineBot Watchdog」（`LineBotWatchdog.xml`，不在本 repo） | 每 5 分鐘確認 `:8000` 在聽，不在就拉起 |
| 對外通道 | `deploy/nginx.conf` 的 `location = /linebot/callback` | LINE 平台 → Cloudflare → 隧道 → nginx → `host.docker.internal:8000/callback` |
| 探針（P4） | `scripts/check_linebot_health.sh`、`deploy/systemd/report-mark-linebot-health.service`／`.timer` | 每 2 分鐘打 `http://127.0.0.1:8000/health`，只回報事實 |
| 事件（P5） | `deploy/systemd/report-mark-linebot-incident.service`／`.timer` | 與 web 同一支 `scripts/incident_handler.sh`，元件 `linebot`：去重、提醒、恢復判定、webhook |

## bot 的行為

以下是 `C:\LineBot` 那份程式的設計，改它之前要知道的取捨：

- **先落地本機、再發佈到 NAS**：`LINE ──下載──▶ staging/（本機）──發佈──▶ SAVE_DIR（NAS）`。LINE 端的內容有保存期限、過期永久取不回，而 NAS 會斷且頻率高得多；一步到位寫 NAS 的話，NAS 一抖那份研報就跟著消失。`flush_staging()` 在啟動時與每次 webhook 後補送積壓。
- **寫檔是原子的**：中斷不會在 NAS 留下截斷的 PDF（rsync 會把截斷檔當完整研報抓走，抽字損毀不報錯）。
- **下載同步做完才回 200**：改成背景佇列會變成「回了 200 卻還沒落地」，此時崩潰 LINE 認為已送達。nginx 那條路徑的讀取逾時因此放寬到 120 秒。
- **扁平存放、檔名 `{messageId}_{原檔名}`**：下游 rsync 與語料庫的檔名慣例都建立在這個版型上，不要改成依日期分目錄。
- **`failures.jsonl`** 每列一筆失敗（含 `stage` 與 `message_id`），是唯一能回答「這段期間漏了哪幾篇」的東西。
- **`/health` 真的探 `SAVE_DIR` 可不可寫**：回 200 且 `save_writable: true` 才算好；寫不進去回 503（`save_error` 說明原因）。它會回報內部狀態，只給本機探針看、不對公網開。
- 認證由 bot 自己做（LINE 的 `X-Line-Signature` HMAC-SHA256）。

## 看門狗

用「每 5 分鐘問一次埠在不在聽」而不是工作排程器的「失敗時重新啟動」：後者只在工作以非零碼結束時動作，而 bot 最常見的死法（重開機、登出、行程被殺、活著但埠沒在聽）在排程器看來都是「已完成」。腳本是冪等的（已在聽就回 0 什麼都不做），並使用專屬 venv（不共用會被別的專案 pip 汙染的全域環境）。

改 `watchdog.ps1` 或任務定義時的三條規則（都是靜默失敗、任務照樣顯示成功）：

1. **`.ps1` 存成 UTF-8 with BOM。** 工作排程器跑的是 Windows PowerShell 5.1（`powershell.exe`），它把沒有 BOM 的檔以系統 ANSI codepage（cp950）解讀，中文註解會把引號與大括號配對打亂，腳本在寫第一行 log 之前就死。互動殼（PowerShell 7）與 `[Parser]::ParseFile` 都以 UTF-8 解讀，驗不出來；要驗就用真正的 `powershell.exe -File`。
2. **重複觸發用獨立的 `TimeTrigger`**（`StartBoundary` 取過去時間＋`StartWhenAvailable`），不要把 `Repetition` 掛在 `LogonTrigger` 底下：那個重複只在登入事件實際觸發後才生效，使用者已登入時建立的任務要等下次登入才會再跑。登入觸發器只負責「登入後盡快拉起」。
3. **`$ErrorActionPreference` 用 `Continue`**，靠明確的結果檢查（venv 檔案在不在、埠有沒有在聽）判定成敗。5.1 在 `Stop` 之下會把原生指令往 stderr 印字當成終止錯誤，`pip install` 印一行提示就中斷。

## 對外通道

LINE 的 webhook 走既有對外網域的 `/linebot/callback`（網域見 `docs/EXTERNAL_ACCESS.md`）。`deploy/nginx.conf` 的兩個刻意設計：

- **精確匹配 `= /linebot/callback`，不是前綴**：bot 的 `/health` 不該對公網開。
- **不另開 hostname／server block**：`server_name _` 是 catch-all，新增具名 server 會讓「儀表板加了 hostname 卻忘了加 server block」變成靜默失敗——LINE 的 POST 被送去 `:8097` 的研報 App、撞上 deny-by-default 被導向 `/login`，LINE 端只看得到非 200。

改了 `deploy/nginx.conf` 要 `make edge-reload`（模板只在容器啟動時渲染）。

## 監控

探針的退出碼（`scripts/check_linebot_health.sh`）：

| 退出碼 | 意思 | 處置方向 |
|---|---|---|
| 0 | 連得上且 200 | — |
| 1 | 連不上／逾時／非預期狀態碼＝bot 沒在跑 | 工作排程器與 `watchdog.log` |
| 2 | 回 503＝行程活著但存不了檔（NAS 寫不進去或 staging 積壓） | NAS 掛載與權限 |
| 4 | 探針自己不能執行（缺 curl） | unit 的 `PATH` |

- 單次執行內重試 3 次、間隔 15 秒（`LINEBOT_HEALTH_RETRIES`、`LINEBOT_HEALTH_RETRY_WAIT`），吸收看門狗重啟 bot 的空窗；網址與逾時是 `LINEBOT_HEALTH_URL`（預設 `http://127.0.0.1:8000/health`）、`LINEBOT_HEALTH_TIMEOUT`（5 秒）。WSL 是 mirrored 網路、與 Windows 共用 localhost，所以從 WSL 打得到。
- 沒有 unit 狀態那一層：bot 在 Windows 側，WSL 沒有對應的 systemd unit，判定完全靠 HTTP。
- P5 的狀態目錄是 `data/.incidents-linebot/`，**必須與 web 那組分開**：`handler.lock` 與 `probe_observation.state` 是固定檔名，共用目錄會互相覆寫觀測快取。
- 探針 `OnBootSec=5min`（等 Windows 側的看門狗把 bot 拉起來），P5 是 6 分鐘、`INCIDENT_BOOTSTRAP_SECONDS=480`。
- 通知與 web 那組共用 `/etc/report-mark/alert.env` 的 webhook；落點、設定與投遞驗證見 `docs/production_resilience.md`「通知投遞與 secret 落點」。
- 兩個 unit 列在辦公室主機的 ops catalog（`deploy/ops/services.prod.toml` 的 `linebot-health`、`linebot-incident` 與外部依賴 `linebot`），管理後台看得到。

## 已知限制

1. **登入才會啟動。** 排程任務用 `LogonType=InteractiveToken`，這是刻意的：NAS 存取靠使用者 session 的快取憑證，改成「不論使用者是否登入都執行」需要儲存密碼，否則憑證庫不會解鎖、寫 NAS 會失敗。代價是機器重開後若沒有人登入，bot 不會起來（探針會以 exit 1 告警）。
2. **`failures.jsonl` 沒有消費端。** 它記下「本來應該有這一篇」，但沒有任何探針或批次會讀它，要人去看。

## 安裝（重裝或換機）

### 1) 註冊工作排程器任務

```powershell
# 在 Windows 以一般使用者身分（不需要系統管理員）
schtasks /Create /XML "C:\LineBot\LineBotWatchdog.xml" /TN "LineBot Watchdog"
schtasks /Run /TN "LineBot Watchdog"

# 一定要確認重複觸發器真的排進去了（看門狗規則 2）：
schtasks /query /tn "LineBot Watchdog" /fo LIST /v | Select-String "下次執行時間|重複: 每隔"
# 「下次執行時間: 不適用」或「重複: 每隔: 不適用」＝ 沒排進去，看門狗不會定期跑。
```

### 2) 確認 bot 起來了

```powershell
Get-Content C:\LineBot\watchdog.log -Tail 5
Invoke-WebRequest http://127.0.0.1:8000/health | Select-Object -ExpandProperty Content
```

`save_writable: true` 才算真的好了。若是 `false`，看 `save_error`——那代表 NAS 寫不進去，而不是 bot 有問題。

### 3) 設定 LINE Developers Console 的 Webhook URL

設成 `https://<對外網域>/linebot/callback`，然後按 **Verify**。

### 4) 確認邊緣路徑

在部署目錄（repo 根）執行：

```bash
make edge-reload
# 從 WSL 打 Windows 側的 bot
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/health
# 從 nginx 容器打（這條才是 LINE 實際會走的路）
docker exec deploy-nginx-1 wget -qO- --timeout=5 http://host.docker.internal:8000/health || echo "打不到"
```

兩條都要通。第二條不通時，把 `deploy/nginx.conf` 那條 `proxy_pass` 的主機改成辦公室主機的區網 IP（2026-09-01 實測 `192.168.1.128` 可通）再 `make edge-reload`。

### 5) 安裝監控 unit

```bash
sudo cp deploy/systemd/report-mark-linebot-health.service \
        deploy/systemd/report-mark-linebot-health.timer \
        deploy/systemd/report-mark-linebot-incident.service \
        deploy/systemd/report-mark-linebot-incident.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now report-mark-linebot-health.timer
sudo systemctl enable --now report-mark-linebot-incident.timer
```

驗證（不要用 `is-active` 判定）：

```bash
sudo systemctl start report-mark-linebot-health.service
journalctl -u report-mark-linebot-health.service -n 5 --no-pager
# 應該看到 status=ok 那一行
```

webhook 還沒設定的主機，照 `docs/production_resilience.md`「通知投遞與 secret 落點」設定並驗證投遞；沒有它，P5 只會寫 log。

## 出事時看哪裡

| 症狀 | 先看 |
|---|---|
| P5 報 `linebot` FIRING、探針 exit 1 | `C:\LineBot\watchdog.log`、`stdout.log`；工作排程器裡「LineBot Watchdog」還在不在、上次執行結果；機器重開後有沒有人登入 |
| P5 報 `linebot` FIRING、探針 exit 2 | bot 的 `/health` 的 `save_error`；NAS 掛載與 `\\192.168.1.100\投資研究處` 的權限；`C:\LineBot\staging\` 有沒有積壓 |
| P5 報 `linebot_monitor` | 探針 timer 被停／不存在／觀測過期——**監控自己瞎了**，不是 bot 的問題 |
| LINE Console 顯示 Webhook 失敗 | 安裝第 4 步的兩條檢查；`docker logs deploy-nginx-1` |
| 研報有進 NAS 但沒進語料庫 | 那是 sync 這側的事，看 `data/sync_run_*.log` 與 `make freshness` |
| 想知道漏了哪幾篇 | `C:\LineBot\failures.jsonl`（每列一筆，含 `stage` 與 `message_id`） |
