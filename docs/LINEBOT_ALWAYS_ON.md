# LineBot 常駐與監控

> **狀態：已安裝並運作中（2026-09-02 覆核）。** bot 在跑且可寫 NAS；四個監控 unit 已 enable，探針回 `status=ok`、P5 判 `CLOSED`；邊緣 nginx 的 running conf 已含 `/linebot/callback` 路由。**尚未由 repo 這側確認的只剩 webhook secret**（`/etc/report-mark/alert.env` 為 root 0600，需 sudo 才看得到；未設定的話 P5 只會寫 log）。安裝步驟仍保留在 §4 供重裝或換機使用。

## 已驗證的事實（2026-09-01）

安裝過程中實際量到的，取代先前的推論：

| 項目 | 結果 |
|---|---|
| bot 監聽 `127.0.0.1:8000` | ✅ 由看門狗拉起，單一實例（venv stub ＋ 真直譯器的父子關係） |
| `/health` | ✅ HTTP 200，`save_writable: true` |
| **NAS 可寫** | ✅ **直接證據**：`save_writable: true`，`save_dir` 就是 `\\192.168.1.100\投資研究處\02.研究資源\研報自動匯入` |
| WSL → Windows `127.0.0.1:8000` | ✅ 通（mirrored 網路確認） |
| nginx 容器 → `host.docker.internal:8000` | ✅ 通（**LINE 實際會走的那條**） |
| nginx 容器 → `192.168.1.128:8000` | ✅ 通（備援路徑也可用） |
| venv 隔離 | ✅ 自帶 flask 3.1.3，`include-system-site-packages = false` |
| 看門狗冪等 | ✅ 第二次執行 0.28 秒回 0，不留新 log、不多啟動行程 |

## 安裝時實際踩到的兩個 bug（已修，記在這裡免得重犯）

**1. `.ps1` 沒有 BOM，Windows PowerShell 5.1 以 ANSI 解讀 → 語法被打爛。**
工作排程器跑的是 `powershell.exe`（5.1），它對**沒有 BOM 的 `.ps1` 以系統
ANSI codepage（本機 cp950）解讀**，UTF-8 的中文註解被誤解碼後把引號與大括號
配對打亂，腳本在寫第一行 log 之前就死了——症狀是「任務顯示成功執行，但
`watchdog.log` 不存在」。
**互動測試看不出來**：使用者的殼是 PowerShell 7，預設 UTF-8。
而我原本的驗證用 `[Parser]::ParseFile`，那個 API 也以 UTF-8 解讀，所以報「語法
OK」——**驗證方法本身錯了**。要驗就要用真正的 `powershell.exe -File`。
處置：檔案存成 **UTF-8 with BOM**。

**2. `Repetition` 掛在 `LogonTrigger` 底下 → 每 5 分鐘根本沒排進去。**
那個重複只在「登入事件實際觸發」之後才生效。任務是在使用者**已經登入**的狀態
下建立的，登入觸發器不會回頭補觸發，於是 `schtasks /query` 顯示
`下次執行時間: 不適用`、`重複: 每隔: 不適用`——**看門狗要等下次登入才會再跑**。
`schtasks /Run` 仍然會回報成功，完全靜默。
處置：改用**獨立的 `TimeTrigger`**（`StartBoundary` 取過去時間 ＋
`StartWhenAvailable`），登入觸發器只負責「登入後盡快拉起」。

**3. `$ErrorActionPreference = 'Stop'` ＋ 原生指令的 stderr。**
PowerShell 5.1 在 `Stop` 之下會把「原生指令往 stderr 印字」當成終止錯誤——
`pip install` 印一行提示就讓看門狗中斷在建 venv 那一步。
處置：改用 `Continue`，靠明確的結果檢查（venv 檔案在不在、埠有沒有在聽）
判定成敗。

## 0. 這件事為什麼重要

`C:\LineBot\line_file_bot.py` 把 LINE 群組裡的研報下載到 NAS
`\\192.168.1.100\投資研究處\02.研究資源\研報自動匯入`，而那個目錄正是
`scripts/sync_new_reports.sh` 的 rsync 來源。**它斷掉等於語料供稿斷掉。**

2026-08-13 之後它沒有再被啟動，直到 08-31 才被發現——**18 天**。實測代價：

| 期間 | LINE 這條佔每日新進檔案 |
|---|---|
| 07-17 ~ 08-13（運作中） | **32%**（214/662），單日最高 100% |
| 08-14 ~ 08-31（停擺） | **0%** |

它沒有以「語料完全停更」的形式暴露出來，因為另一條供稿管道（`daily story_*`、
`Radar Monthly_*` 那種命名）還在送。這也是為什麼**不能拿「有沒有新研報」當它的
存活代理指標**。

## 1. 為什麼會掛，以及為什麼沒人發現

勘查結果（四處全部查無自動啟動）：

| 查了什麼 | 結果 |
|---|---|
| 使用者與全機的「啟動」資料夾 | 無 |
| 工作排程器（386 個工作全量 dump） | 無 |
| 登錄檔 `Run` / `RunOnce` / `WOW6432Node` | 無 |
| Windows 服務（288 個） | 無 |

`bot.log` 只有 **4 次啟動橫幅**，全部是人工啟動。「最後一次連跑 27 天」不是韌性，
只是那 27 天機器沒重開機——`LastBootUpTime` 顯示 08-27 09:16 重開過，bot 沒有回來。

對外通道原本很可能是**手動開一個 ngrok 視窗**：Windows 上有 ngrok authtoken 但
`ngrok.yml` 裡沒有任何 tunnel 定義，而 `bot.log` 的來源全是 127.0.0.1。免費版
網址每次重開都變，Webhook URL 就此失效。

## 2. 改了什麼

### 2.1 程式（`C:\LineBot\line_file_bot.py`）

架構改成 **先落地本機、再發佈到 NAS**：

```
LINE ──下載──▶ staging/（本機、快、幾乎不會失敗）──發佈──▶ SAVE_DIR（NAS）
```

原本是一步到位寫進 NAS UNC，把兩個性質完全不同的失敗點綁在一起：LINE 端的內容
**有保存期限、過期永久取不回**，而 NAS 端**會斷**且頻率高得多。綁在一起的後果是
NAS 一抖那份研報就跟著消失——即使 LINE 那邊當下明明還拿得到。

一併修掉六個會**靜默吃掉研報**的缺陷：

| # | 缺陷 | 後果 |
|---|---|---|
| 1 | `_processed_ids.add(mid)` 在下載**之前** | 失敗的訊息自己毒死自己，永不重試，任何補救機制都會被這行擋掉 |
| 2 | 只接 `RequestException`，`OSError` 沒接住 | NAS 一抖就穿出 handler，**同批次後面的事件一起丟掉** |
| 3 | 寫檔不是原子的 | 中斷會在 NAS 留下截斷 PDF，而 rsync **會把它當完整研報抓走**（抽字損毀不報錯） |
| 4 | HTTP 202 走無上限遞迴 | 約千層後 `RecursionError`，那不是 `RequestException`，直接穿出 handler |
| 5 | `compare_digest` 對非 ASCII header 拋 TypeError | 服務綁 `0.0.0.0`，區網任一台送個帶中文的 header 就能讓該請求 500 |
| 6 | `bot.log` 無 rotation | 約 9.6 KB/日，無上界 |

新增：`staging/`、`failures.jsonl`（**唯一能回答「這段期間漏了哪幾篇」的東西**）、
`flush_staging()`（啟動時與每次 webhook 後自動補送）、`/health` 改成會真的探
`SAVE_DIR` 可不可寫。

**刻意不改**：扁平存放與 `{messageId}_{原檔名}` 的命名——下游 rsync 與語料庫的
檔名慣例都建立在這個版型上（README 寫的「依日期分類」是過時敘述）。
**下載仍然同步做完才回 200**：改成背景佇列會變成「回了 200 卻還沒落地」，一旦
此時崩潰 LINE 認為已送達，而內容有保存期限。在 webhook 重送設定未知的前提下，
同步是比較保守的選擇。

### 2.2 常駐（`C:\LineBot\watchdog.ps1` ＋ `LineBotWatchdog.xml`）

**看門狗 ＋ 定期重跑**，不是「啟動一次 ＋ 失敗時重啟」。工作排程器的「失敗時重新
啟動」只在工作本身以非零碼結束時才動作，而這支 bot 最常見的死法（重開機、登出、
行程被殺、還活著但埠沒在聽）在排程器看來都是「已完成」。改成每 5 分鐘問一次
「埠在不在聽」，判準就從「工作有沒有跑」變成「服務是不是活的」——與
`scripts/check_web_health.sh` 同一個教訓。

腳本是**冪等**的（已在聽就回 0 什麼都不做），所以定期觸發不會疊出第二個行程。
另外它會建立**專屬 venv**，不再共用會被別的專案 pip 汙染的 pyenv 全域環境。

### 2.3 對外通道（`deploy/nginx.conf`）

在既有 server block 加一條**精確路徑** `= /linebot/callback` → `host.docker.internal:8000/callback`。

- **不另開 hostname／server block**：本檔的 `server_name _` 是 catch-all，新增
  具名 server 會讓「儀表板加了 hostname 卻忘了加 server block」變成靜默失敗——
  LINE 的 POST 會被送去 `:8097` 的研報 App，撞上 deny-by-default 而被導向
  `/login`，LINE 端只看得到非 200，完全不會提示路由錯了。
- **精確匹配而非前綴**：bot 的 `/health` 會回報 NAS 是否可寫等內部狀態，那是給
  區網探針看的，不該對公網開。
- 零 Cloudflare 儀表板變更。

### 2.4 監控（P4/P5 第二個實例）

| 檔案 | 角色 |
|---|---|
| `scripts/check_linebot_health.sh` | P4 探針：只回報此刻的事實，不通知任何人 |
| `deploy/systemd/report-mark-linebot-health.{service,timer}` | 每 2 分鐘探測 |
| `deploy/systemd/report-mark-linebot-incident.{service,timer}` | P5：去重／提醒／恢復判定／webhook |

**`scripts/incident_handler.sh` 一個字都沒改。** 它從一開始就把
`INCIDENT_PROBE_UNIT`／`INCIDENT_TIMER_UNIT`／`INCIDENT_COMPONENT`／
`INCIDENT_STATE_DIR` 全部參數化了，所以多監控一個元件只需要跑第二個實例。

退出碼刻意分流：**1＝沒在跑**（查工作排程器與 `watchdog.log`）、**2＝活著但存不了
檔**（查 NAS 掛載與權限）。兩者處置完全不同。

狀態目錄用 `data/.incidents-linebot/`：狀態檔以 component 命名不會撞，但
`handler.lock` 與 `probe_observation.state` 是固定檔名，共用目錄會讓兩個實例
互相覆寫觀測快取。

## 3. 已知限制（安裝前要知道）

1. **登入才會啟動。** 排程任務用 `LogonType=InteractiveToken`，這是刻意的：NAS
   存取靠的是使用者 session 的快取憑證，改成「不論使用者是否登入都執行」需要
   儲存密碼，否則憑證庫不會解鎖、**寫 NAS 會失敗**。代價是機器重開後若沒有人
   登入，bot 不會起來。若要真正免登入，需另外處理身分（自動登入，或在服務帳戶
   下重新建立憑證）。
2. ~~`host.docker.internal:8000` 尚未實測~~ → **2026-09-01 已驗證通過**，兩條
   路徑（`host.docker.internal` 與 `192.168.1.128`）都通。
3. **`REPORT_MARK_ALERT_WEBHOOK` 目前沒有設定。** 2026-08-28 真的 FIRING 過一次
   而它沒有送出任何東西。**不設它的話，新增的偵測也只是多寫一筆 log。**
4. **`deploy/systemd/` 與主機 `/etc/systemd/system/` 會分岔**——已經有一支
   （`report-mark-metrics.service`）只存在於 repo、主機上是 not-found。第 6 步
   的 `sudo cp` 不做，就會出現「檔案在 repo 裡、監控其實沒跑」的假安全感。
5. **`failures.jsonl` 目前沒有消費端。** 它記下「本來應該有這一篇」，但沒有任何
   東西會讀它。下一輪應該讓探針或 freshness 把它納入。

## 4. 安裝步驟（人工執行）

### 1) 註冊工作排程器任務

```powershell
# 在 Windows 以一般使用者身分（不需要系統管理員）
schtasks /Create /XML "C:\LineBot\LineBotWatchdog.xml" /TN "LineBot Watchdog"
schtasks /Run /TN "LineBot Watchdog"

# **一定要確認重複觸發器真的排進去了**（見上面的 bug 2）：
schtasks /query /tn "LineBot Watchdog" /fo LIST /v | Select-String "下次執行時間|重複: 每隔"
# 「下次執行時間: 不適用」或「重複: 每隔: 不適用」＝ 沒排進去，看門狗不會定期跑。
```

### 2) 確認 bot 起來了

```powershell
Get-Content C:\LineBot\watchdog.log -Tail 5
Invoke-WebRequest http://127.0.0.1:8000/health | Select-Object -ExpandProperty Content
```

`save_writable: true` 才算真的好了。若是 `false`，看 `save_error`——那代表 NAS
寫不進去，而不是 bot 有問題。

### 3) 更新 LINE Developers Console 的 Webhook URL

改成 `https://<既有的對外網域>/linebot/callback`，然後按 **Verify**。
（網域就是 report-mark 現在對外用的那一個，見 `docs/EXTERNAL_ACCESS.md`。）

### 4) 重載邊緣 nginx

```bash
cd ~/projects/report-mark && make edge-reload
```

**是 `--force-recreate` 不是 `restart`**：`nginx.conf` 是 envsubst 模板，只在容器
啟動時渲染一次。

### 5) 驗證外部路徑真的通（限制 2 的驗證點）

```bash
# 從 WSL 打 Windows 側的 bot
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/health
# 從 nginx 容器打（這條才是 LINE 實際會走的路）
docker exec deploy-nginx-1 wget -qO- --timeout=5 http://host.docker.internal:8000/health || echo "打不到"
```

兩條都要通。第二條不通就把 `deploy/nginx.conf` 的 `proxy_pass` 改成
`http://192.168.1.128:8000/callback` 再 `make edge-reload`。

### 6) 安裝監控 unit

```bash
cd ~/projects/report-mark
sudo cp deploy/systemd/report-mark-linebot-health.service \
        deploy/systemd/report-mark-linebot-health.timer \
        deploy/systemd/report-mark-linebot-incident.service \
        deploy/systemd/report-mark-linebot-incident.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now report-mark-linebot-health.timer
sudo systemctl enable --now report-mark-linebot-incident.timer
```

驗證（**不要用 `is-active` 判定，那是 2026-08-18 的教訓**）：

```bash
sudo systemctl start report-mark-linebot-health.service
journalctl -u report-mark-linebot-health.service -n 5 --no-pager
# 應該看到 status=ok 那一行
```

### 7) 設定告警 webhook（否則前六步只是多寫 log）

```bash
sudo install -d -m 0700 /etc/report-mark
read -rsp '貼上 webhook URL 後按 Enter：' WEBHOOK_URL; echo
case "$WEBHOOK_URL" in
  *[[:space:]]*|'') echo '值是空的或含空白，未寫入' >&2 ;;
  https://?*) printf 'REPORT_MARK_ALERT_WEBHOOK=%s\n' "$WEBHOOK_URL" | sudo tee /etc/report-mark/alert.env >/dev/null \
                && sudo chmod 0600 /etc/report-mark/alert.env && echo '已寫入' ;;
  *) echo '不是 https:// 開頭，未寫入' >&2 ;;
esac
unset WEBHOOK_URL
sudo systemctl start report-mark-linebot-incident.service
```

URL 用 `read -s` 讀進來而不是寫在指令裡：指令列上沒有可以原樣貼上執行的佔位字串，URL 也不會
留在 shell history。值不是 `https://` 開頭就不寫檔。

**設定完一定要驗證投遞。** 值寫錯時 unit 照樣 active、`/healthz` 照樣綠，只有 journal 看得出來：

```bash
journalctl -u report-mark-incident.service -u report-mark-linebot-incident.service --since "-10min" --no-pager \
  | grep -E "notified=yes|webhook 投遞失敗|不是合法 URL"
```

`notified=yes` 才算通（當下沒有進行中的事件就不會有東西要送，這時沒有任何一行也是正常的）。
`不是合法 URL` 是值的形狀錯了；`webhook 投遞失敗` 那一行會帶 `curl_rc=`，對照
`docs/production_resilience.md` 的「投遞失敗語意」。

這個檔刻意與 `/etc/default/report-mark-sync` 分開（後者必須使用者可讀，見
`report-mark-incident.service` 的註解）。設定後 **web 那組的告警也會一起開始
投遞**——那本來就是它該有的行為。

## 5. 出事時看哪裡

| 症狀 | 先看 |
|---|---|
| P5 報 `linebot` FIRING、探針 exit 1 | `C:\LineBot\watchdog.log`、`stdout.log`；工作排程器裡「LineBot Watchdog」還在不在、上次執行結果 |
| P5 報 `linebot` FIRING、探針 exit 2 | bot 的 `/health` 的 `save_error`；NAS 掛載與 `\\192.168.1.100\投資研究處` 的權限；`C:\LineBot\staging\` 有沒有積壓 |
| P5 報 `linebot_monitor` | 探針 timer 被停／不存在／觀測過期——**監控自己瞎了**，不是 bot 的問題 |
| LINE Console 顯示 Webhook 失敗 | 第 5 步的兩條 curl；`docker logs deploy-nginx-1` |
| 研報有進 NAS 但沒進語料庫 | 那是 report-mark 這側的事，看 `data/sync_run_*.log` 與 `make freshness` |
| 想知道漏了哪幾篇 | `C:\LineBot\failures.jsonl`（每列一筆，含 `stage` 與 `message_id`） |
