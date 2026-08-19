# Incident Postmortem — 2026-08-18 廷豐智能研報生產中斷

- **狀態**：已恢復（2026-08-18 13:40:01 CST）
- **撰寫時間**：2026-08-18
- **系統**：report-mark（廷豐智能研報）web + NAS 同步管線
- **證據來源**：`journalctl`（persistent journal，`/var/log/journal`）、`systemctl show`、
  `data/unit_failures.log`、`data/sync_run_20260818.log`、`netsh interface portproxy`／`netstat`
  的當場觀測、Docker inspect、檔案 mtime

> 本文所有時間為 CST（UTC+8）。因果關係一律標示信心等級；無法由證據證明者標為
> `HYPOTHESIS`，不寫成已證實。

---

## Executive Summary

2026-08-18 早上 08:50 WSL distro 啟動後，report-mark 的 web 服務**從未成功綁定 :8097**，
直到 13:40:01 才恢復，中斷約 **4 小時 50 分**。期間對外與區網使用者完全無法使用，
`journalctl` 顯示當日 13:40 之前**服務的 HTTP 請求數為 0**。

中斷有兩個接續的失效階段。**第一階段（09:00–約 09:43）**：位於 `/mnt/c`（9p）的
Python 虛擬環境處於半安裝狀態，`sqlalchemy` 觸發 circular import，同時打掉 web、
NAS 同步與批次停更偵測三支 unit；`uv` 嘗試自我修復時，安裝所需的 `.tmp*` → 正式路徑
rename 在 9p 上以 `No such file or directory (os error 2)` 失敗。**第二階段（09:43–13:33）**：
在 web 反覆重啟的空檔中，Windows `iphlpsvc` 以一條**遺留的失效 `netsh portproxy` 規則**
（`0.0.0.0:8097 → 172.20.59.191`，指向早已不存在的 NAT IP）搶走了 :8097，此後每次啟動
都是 `EADDRINUSE`，共 661 次、systemd 重啟計數累積到 550。

恢復方式是移除那條 portproxy 規則；同一天進行中的 9p → ext4 遷移（獨立於本事件展開）
在 11:52–13:40 完成 cutover，因此恢復後的生產同時換到了 ext4，**根因所在的檔案系統也一併
消除**。DB 未受損、無資料遺失、無 partial ingestion。

監控之所以沒有即時揭露：`systemctl is-active` 全程顯示 `active` —— uvicorn 在 bind 失敗後
仍會存活約 10 秒載入 BGE-M3，systemd 因此看到「有行程在跑」。**當時沒有任何
application-level 探針**在量 `/healthz`，`OnFailure` 告警雖然有觸發並寫進
`data/unit_failures.log`（854 筆），但那個檔案沒有任何主動通知路徑，也沒有人在看。

---

## Impact

| 項目 | 內容 |
|---|---|
| Outage 起 | **2026-08-18 08:50:02**（WSL distro 啟動；當日**從未**出現成功的 `Uvicorn running on`） |
| Outage 迄 | **2026-08-18 13:40:01**（首次 `Uvicorn running on http://0.0.0.0:8097`） |
| 期間 | **約 4 小時 50 分** |
| HTTP availability | **0%**。`journalctl` 當日 13:40 之前 access log 為 0 筆；13:40 之後累計 95 筆 |
| web | 完全不可用；systemd 重啟計數最高 **550**；`unit_failures.log` 記 **854** 筆 |
| sync（NAS 增量匯入） | 09:00 那輪匯入失敗 `rc=1`；當日在 13:45 手動補跑前**未有成功入庫** |
| freshness | 09:00:13 以 `ImportError` 失敗 `exit 1` |
| audit / backup | 08:50:28 的 catch-up 執行被 `SIGTERM` 中止（啟動期競態），當日未再自然觸發 |
| ingestion impact | 6 份新研報延遲入庫約 4.75 小時，13:45 手動同步後全部補齊（ingested 4、chunks 187、skip_non_research 2、fail 0） |
| DB 是否受損 | **否**。`report-mark-pgdata` volume 未被觸碰；13:40 後 `db_audit.py` 11 項檢查全乾淨（含耐久性 `fsync` 未被關閉） |
| 資料遺失 | **無** |
| partial ingestion | **無**。`full_text IS NOT NULL 但無 chunk` = 0；`embedding IS NULL` = 0 |

---

## Timeline

```
timestamp            | event                                                          | evidence
---------------------+----------------------------------------------------------------+---------------------------------------------
2026-08-18 08:50:02  | WSL distro systemd 啟動                                        | journald "Journal started"；sysinit.target reached
2026-08-18 08:50:03  | sync 的 Persistent catch-up 啟動 (pid=205)                      | data/sync_run_20260818.log
2026-08-18 08:50:07  | audit catch-up 啟動                                            | journalctl -u report-mark-audit
2026-08-18 08:50:28  | sync 與 audit 被 SIGTERM 中止 (status=15/TERM)                   | journalctl；OnFailure "Failed to enqueue ... is destructive"
2026-08-18 08:55:48  | freshness 啟動                                                 | journalctl -u report-mark-freshness
2026-08-18 08:57:19  | backup 執行（sudo mount-nas-backup）                            | journalctl -u report-mark-backup
2026-08-18 09:00:00  | sync 排程啟動 (pid=1237)                                        | sync_run_20260818.log
2026-08-18 09:00:13  | freshness: sqlalchemy circular ImportError → exit 1 → OnFailure | journalctl；unit_failures.log 1 筆
2026-08-18 09:00:17  | web: 同一個 ImportError；首次 "Failed with result 'exit-code'"   | journalctl；unit_failures.log 首筆
2026-08-18 09:01:08  | sync: 匯入結束 rc=1，保留 delta 供排查                            | sync_run_20260818.log；unit_failures STAGE=import RC=1
2026-08-18 09:23:59  | uv: "Failed to spawn: uvicorn" / os error 2                     | journalctl -u report-mark-web
2026-08-18 09:24:10  | uv: Failed to install pytest wheel（.tmp3Fjs28 ENOENT）          | journalctl（路徑在 /mnt/c 的 .venv 下）
2026-08-18 09:24:19  | uv: 同上，.tmpqzODVW ENOENT                                     | journalctl
2026-08-18 09:28:43  | uv: Failed to install torch wheel — **rename .tmpo5z5cW/gather.h → gather.h 失敗 (os error 2)** | journalctl
2026-08-18 09:43:25  | **失效模式改變**：首次 EADDRINUSE on 0.0.0.0:8097               | journalctl -u report-mark-web
2026-08-18 09:43–13:33| 持續 crash loop：661 次 EADDRINUSE，重啟計數累積到 550           | journalctl；unit_failures.log 854 筆
2026-08-18 11:14     | （獨立進行中的）9p → ext4 遷移分析開始                            | 會談紀錄；migration-backup 產物
2026-08-18 11:19:56  | 全庫 pg_dump 完成（2,659,547,325 bytes）                         | ~/migration-backup/premigration-full.dump mtime
2026-08-18 11:14–11:30| rsync 複製 40,430 檔 / 16.4 GB → ext4                           | rsync stats
2026-08-18 11:38:31  | 新 .venv 建立完成（uv sync --frozen，ext4 上）                    | .venv/pyvenv.cfg mtime
2026-08-18 11:40:51  | 前端 dist 建置完成                                             | frontend/dist/index.html mtime
2026-08-18 11:52:20  | web service 停止（cutover 開始）                                 | journalctl
2026-08-18 11:59:36  | systemd unit ＋ /etc/default 更新並 daemon-reload                | /etc/systemd/system/report-mark-web.service mtime
2026-08-18 12:00:46  | edge 容器（nginx/cloudflared）從新 repo 重建                      | docker inspect deploy-nginx-1 .Created (04:00:46Z)
2026-08-18 ~13:35    | 診斷出 stale portproxy 並由管理員刪除該規則                        | netsh portproxy show all 當場觀測；刪除後 bind() 成功
2026-08-18 13:40:00  | web service 啟動（新路徑）                                       | ActiveEnterTimestamp
2026-08-18 13:40:01  | **首次成功 "Uvicorn running on http://0.0.0.0:8097"**            | journalctl
2026-08-18 13:40:13  | **當日首個被服務的 HTTP 請求**（GET /healthz 200）                | journalctl access log
2026-08-18 13:45:20  | 手動完整同步開始                                                | sync_run_20260818.log
2026-08-18 13:58:45  | 手動同步 === sync done ===（rc=0，入庫 4 篇 / 187 chunks）        | sync_run_20260818.log
2026-08-18 15:00:02  | 首次 timer 自然觸發的完整同步                                    | timer LastTriggerUSec == service ExecMainStartTimestamp
2026-08-18 15:03:20  | === sync done ===（rc=0）                                       | sync_run_20260818.log
```

---

## Root Cause

### Primary root cause

**在 9p（`/mnt/c`，drvfs）上維護 Python 虛擬環境不可靠，導致 `uv` 的套件安裝／自我修復
失敗，進而使 `sqlalchemy` 停在半初始化狀態。**

信心：**HIGH（對「9p 上的安裝失敗」為 PROVEN；對「它是本次中斷的起始環節」為 STRONG EVIDENCE）**

直接證據（全部來自 journal，路徑均在 `/mnt/c/.../.venv` 底下）：

```
09:24:10  Failed to install: pytest-9.1.1-py3-none-any.whl
          Caused by: No such file or directory (os error 2) at path
          ".../.venv/lib/python3.13/site-packages/_pytest/.tmp3Fjs28"

09:28:43  Failed to install: torch-2.12.0-cp313-cp313-manylinux_2_28_x86_64.whl
          Caused by: failed to rename file from
          ".../site-packages/torch/include/ATen/ops/.tmpo5z5cW/gather.h" to
          ".../site-packages/torch/include/ATen/ops/gather.h": No such file or directory (os error 2)
```

`uv` 採「寫入 `.tmpXXXX` → rename 到正式路徑」的原子安裝策略。上面第二條顯示**剛剛才寫出的
來源檔在 rename 當下已不可見**，這是 9p/drvfs 的目錄項快取一致性問題的典型表現，而不是磁碟
空間或權限問題（同一時段 ext4 上有 772 GB 可用，且新環境以同一份 `uv.lock` 一次就裝成功）。

**未證明的部分（HYPOTHESIS）**：最初是什麼動作把 venv 推進半安裝狀態。08:50:02 的 distro
啟動、08:50:28 的 SIGTERM 競態，或先前某次寫入被 9p 快取截斷，都與證據相容，但 journal
沒有留下足以分辨的紀錄。

### Secondary root cause（造成中斷從 40 分鐘延長為 4 小時 50 分）

**一條遺留的失效 Windows `netsh portproxy` 規則在 web 重啟空檔中搶走了 :8097。**

信心：**STRONG EVIDENCE**（當場觀測，但規則已刪除，無法事後重現）

當時的觀測：

- `netsh interface portproxy show all` 顯示 `0.0.0.0 8097 → 172.20.59.191 8097`
- `172.20.59.191` 不在任何現存 WSL 介面上（mirrored 模式下 WSL IP 為 `192.168.1.128`）
- `netstat -ano` 顯示 `0.0.0.0:8097 LISTENING  PID 4744`；PID 4744 = `svchost.exe`，服務為 `iphlpsvc`
- 從 Windows 與 WSL 兩側 TCP 都連得上，但 HTTP 均回 `http_code=000`（接受連線、無後端）
- WSL 內 `ss -ltn` 與 `/proc/net/tcp` **看不到任何 8097 的 listener**，但 `bind()` 回 `errno 98`
- 刪除該規則後，`bind()` 立即成功，服務於 13:40:01 恢復

該規則是 NAT 網路時代的殘留（`.wslconfig` 註記 2026-07-16 改用 `networkingMode=mirrored`）。
平時 web 佔著 8097 使規則搶不到綁定；一旦服務停夠久，`iphlpsvc` 便補上並持有。

---

## Contributing Factors

| 因素 | 說明 | 信心 |
|---|---|---|
| `.venv` 位於 9p | 5.5 GB、數萬個小檔的環境放在每檔 syscall 都要過 9p 的檔案系統上（`msize=65536`） | PROVEN |
| uv 的 temp-file + rename 語意 | 原子安裝依賴 rename 的即時可見性，9p 不保證 | PROVEN（見上方 log） |
| systemd `Restart=always` + `RestartSec=3` | 讓失敗變成每 3 秒一次的重啟風暴，661 次 EADDRINUSE、854 筆告警紀錄，同時把真實訊號淹沒 | PROVEN |
| stale Windows portproxy | 見 Secondary root cause；**同類規則 8045 / 51121 仍存在** | STRONG EVIDENCE |
| mirrored networking 的可見性落差 | WSL 端工具（`ss`、`/proc/net/tcp`）看不到 Windows 端的埠持有者，使診斷指向錯誤方向 | PROVEN |
| `systemctl is-active` 被當成健康證據 | uvicorn bind 失敗後仍存活約 10 秒載入 BGE-M3，systemd 判定 running | PROVEN |
| 缺少 application-level 探針 | 當時沒有任何週期性 `/healthz` 檢查（本機或外部） | PROVEN |
| `unit_failures.log` 無消費端 | 告警確實寫入，但沒有通知路徑；854 筆等於 0 筆被讀到 | PROVEN |
| freshness 的 false-green 風險 | corpus gate 在語料停滯超過門檻時抑制下游判定，可能回 `rc=0`（本次 freshness 是**明確失敗**、有告警，未觸發此風險，但結構仍在） | HYPOTHESIS（結構性，非本次成因） |

---

## Failure Chain

```
9p filesystem (/mnt/c) 上的 .venv
   │  PROVEN — uv 安裝在 .tmp→rename 階段以 os error 2 失敗（journal 有完整路徑）
   ▼
Python environment 半安裝 / 自我修復失敗
   │  PROVEN — sqlalchemy "partially initialized module" circular import
   ▼
imports 失敗
   │  PROVEN — 同一秒內 web(09:00:17) / freshness(09:00:13) / sync(09:01:08) 三支同時失敗
   ▼
web / sync / freshness 全部失敗
   │  PROVEN — systemd Restart=always 產生每 3 秒一次的重啟
   ▼
restart loop（09:00–13:33，重啟計數 550）
   │  STRONG EVIDENCE — 重啟空檔讓 iphlpsvc 得以為失效 portproxy 規則綁定 :8097
   ▼
port 8097 被 Windows 端搶走
   │  PROVEN — 661 次 EADDRINUSE，起於 09:43:25
   ▼
EADDRINUSE
   │  PROVEN — bind 失敗後 uvicorn 仍存活約 10 秒載入模型
   ▼
process 看似 active，但 HTTP 完全不可用（當日 13:40 前服務 0 個請求）
```

**注意**：`uv` 修復最終在 09:28–09:43 之間成功（舊 venv 現況已正常：`sqlalchemy/__init__.py`
與新環境同為 12,659 bytes，`site-packages` 下零殘留 `.tmp*`）。也就是說**第一階段是可自癒的，
真正把中斷拖長 4 小時的是第二階段的 portproxy**。

---

## Detection Gaps

**1. 為什麼 outage 可以持續數小時？**
沒有任何訊號會主動找人。`OnFailure` 有觸發，但它的落點 `data/unit_failures.log` 是一個
append-only 檔案，沒有任何程式或人在消費它。中斷是在一次無關的遷移工作中被順帶發現的。

**2. 為什麼 `systemctl is-active` 沒有阻止 false-green？**
uvicorn 的啟動順序是「lifespan 完成 → bind」。bind 失敗後行程不會立刻退出（暖機任務仍在
背景載入 BGE-M3 約 10 秒），systemd 在這段時間看到的是 `active (running)`。加上
`Restart=always` 每 3 秒重來一次，任何時間點去查 `is-active` 都有很高機率看到 `active`。

**3. 哪些 alert 有觸發？**
- `report-mark-freshness.service` 09:00:13 → `OnFailure` → `unit_failures.log`（1 筆）
- `report-mark-sync.service` 09:01:08 → `OnFailure` → `unit_failures.log`（2 筆，含 STAGE/RC）
- `report-mark-web.service` 09:00:17–13:33:43 → `OnFailure` → `unit_failures.log`（854 筆）

**4. 哪些 alert 沒觸發？**
- 沒有任何 HTTP 可用性告警（不存在這個探針）
- 沒有「重啟風暴」告警（`Restart=always` 沒有 `StartLimitBurst` 上限告警路徑）
- 08:50:28 被 SIGTERM 的 sync/audit 的 `OnFailure` **無法排入**
  （`Failed to enqueue OnFailure= job ... is destructive`），該次失敗連紀錄都沒留下
- 沒有外部（Cloudflare 之外）的可用性監測

**5. 哪個最早的 reliable signal 應該被監控？**
**`/healthz` 連續失敗**。它在 09:00:17 就會開始為紅（web 從未成功綁定），比 EADDRINUSE
早 43 分鐘，也比任何人工發現早 4.5 小時。次佳訊號是**同一分鐘內多支 unit 同時失敗**
（09:00:13 / 09:00:17 / 09:01:08 三支）——那個形狀強烈指向共用環境而非個別批次的問題。

---

## What Went Well

- **DB volume 完全未受影響**：`report-mark-pgdata` 是 Docker volume，不在 9p 上；13:40 後
  `db_audit.py` 11 項檢查全乾淨。
- **回滾能力全程存在**：舊 repo 未被修改；遷移前另做了 2.66 GB 全庫 `pg_dump` 並以
  `pg_restore --list` 驗證（74 TOC 條目、10 張表、`vector` + `pg_trgm` 擴充齊全）。
- **`unit_failures.log` 保住了完整證據**：854 筆紀錄含 journal 尾段，讓事後可以精確重建時間線；
  這個設計即使沒有通知路徑，仍然發揮了取證價值。
- **`sync` 的失敗處置是對的**：匯入失敗時保留 delta 檔（`匯入失敗（保留 delta 供排查）`），
  13:45 補跑時 6 個檔全數正確入庫，沒有靜默遺漏。
- **PID lock 與 claude CLI flock 正常運作**：13:50 的 timer 觸發正確 skip、未與手動同步互撞。
- **遷移驗證流程夠嚴謹**：2,217 個後端測試、856 個前端測試、CJK PDF 內容測試、nginx bind mount
  的 SHA256 逐位元比對、`bind()` 實測 —— 這些讓 cutover 本身沒有引入任何新問題。
- **`/healthz` 存在且語意正確**：一旦被查詢就立刻給出正確答案，是恢復判定的可靠依據。

---

## What Went Poorly

- 生產目錄（含 `.venv`、`node_modules`、`.git`）長期放在 9p 上，這是一個**已知會週期性
  出問題**的位置，但沒有被當成風險項處理。
- 唯一的告警落點沒有消費端。告警「有寫」與告警「有人知道」之間隔著一整層，而這層不存在。
- 健康判定依賴 `systemctl is-active`，而該訊號對本系統的主要失效型態（bind 失敗）不敏感。
- 診斷過程中，`ss -ltnp` 在 mirrored 網路下給出誤導性的「無人監聽」，據此得出的
  「port 已釋放」結論是錯的，直接導致服務又空轉了 51 次重啟。**跨 WSL/Windows 邊界的埠
  歸屬必須兩側都驗，或直接以 `bind()` 實測。**
- `Restart=always` + `RestartSec=3` 在無法自癒的失效下，把 4.5 小時放大成 854 筆重複告警，
  訊噪比極差。
- 08:50:28 那次 `OnFailure` 因啟動期 transaction 衝突而無法排入，等於在最需要紀錄的時刻
  失去紀錄。

---

## Corrective Actions

> 只列 action，本文不實作。對應的 implementation plan 見
> `/home/kashionz/report-mark-analysis/plans/`。

| # | Action | 對應 plan |
|---|---|---|
| CA-1 | 建立管線心跳與四態新鮮度判定，讓「上游沒前進」與「上游壞掉」可以分辨 | **P1** |
| CA-2 | 把 torch 收斂為 CPU-only，移除約 3.4 GB 未使用的 CUDA/NVIDIA/triton 負載 | **P2** |
| CA-3 | 清除剩餘的失效 portproxy 規則（8045 / 51121），並記入維運檢查表 | **P3** |
| CA-4 | 建立 application-level 分層健康探針（process / local HTTP / edge / external） | **P4** |
| CA-5 | 建立中斷偵測與告警：去重、升級、恢復通知，讓 854 次重啟只產生 1 個事件 | **P5** |
| CA-6 | （已完成）生產目錄遷至 ext4 —— 消除本次 primary root cause 的所在環境 | 本次遷移 |

---

## Lessons Learned

1. **production repo / `.venv` / `node_modules` 不應該放在 `/mnt/c`。** 9p 不只是慢，它對
   「寫入後立刻 rename」這類原子操作的語意保證不足，而現代套件管理器（uv、npm）正是
   大量依賴這個模式。本次的 `failed to rename .tmpo5z5cW/gather.h` 就是直接證據。

2. **process alive ≠ application healthy。** 對一個「先跑 lifespan、後 bind」的服務，
   `systemctl is-active` 在最常見的失效型態下會給出錯誤答案。健康判定必須走應用層探針。

3. **在 WSL mirrored networking 下，埠的歸屬要跨兩邊驗證。** `ss` 與 `/proc/net/tcp` 只看得到
   Linux 側；Windows 側的持有者（含 `iphlpsvc` 的 portproxy 中繼）對它們完全隱形。
   **唯一可靠的判定是實際 `bind()`。**

4. **systemd 的 `Result=success` / `ExecMainStatus=0` 在 unit 從未執行時也是這個值。**
   要確認一支 unit 真的跑過，看 `ExecMainExitTimestamp` —— 空字串代表從沒執行。

5. **L4（timer 自然觸發）驗證不可被手動執行冒充。** 手動 `make sync-once` 成功，只證明
   腳本可用；它證明不了 `/etc/default` 的路徑、unit 的 `exec` 權限位、PATH drop-in 在
   排程情境下都正確。本次遷移的 exec bit 修正就是靠 13:50 那次自然觸發（而非手動執行）
   才得到驗證。

6. **寫入告警不等於送達告警。** `unit_failures.log` 累積了 854 筆完整證據卻沒有救到任何時間，
   因為沒有任何東西會去讀它。任何新增的告警落點都必須同時定義消費端。
