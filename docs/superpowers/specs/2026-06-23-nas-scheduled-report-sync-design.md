# 定時偵測 NAS 共享新研報並增量匯入資料庫

- 日期：2026-06-23
- 範圍：新增「排程同步」管線，不更動既有批次管線（`extract_all` / `tag_all_cli` / `ingest_all`）與線上 web/檢索/問答行為。

## 背景與動機

研究團隊把新研報持續放到 NAS 共享 `\\192.168.1.100\投資研究處\02.研究資源\研報自動匯入`。目前要進到可檢索資料庫，得人工跑全量批次管線（`make extract` → Claude 標註 → `make ingest`），且該管線是針對「靜態語料的一次性全量匯入」設計，不適合常態增量。

目標：**每隔幾小時自動偵測 NAS 共享上的新研報，只把新檔增量匯入資料庫，讓它可被檢索/問答**，且對線上服務的干擾降到最低。

## 關鍵現況（探勘結論）

- **本機即正式機**：這台 WSL 跑 systemd，`report-mark-web.service`（uvicorn `web.server:app`，BGE-M3 常駐 :8097）為線上服務；systemd timer 機制可用。服務以 `User=kashionz` 純 Linux-native 啟動，不依賴 Windows interop。
- **共享尚未掛載**：WSL 目前只掛 C 槽（`/mnt/c`）。本地 `研報自動匯入/`（15G、16002 檔）是當初批次匯入的本地副本，非 NAS 即時鏡像。
- **網路可達、只缺認證**：NAS 192.168.1.100 ping 通、SMB 445 通，本機 Windows 192.168.1.128 同網段；`Test-Path` 共享為 False、`net use` 無連線 → 需一次性建立可重用認證。
- **去重很穩**：`file_hash` = 檔案內容 SHA-256（`app/services/extract.py:file_sha256`）。DB 以 `file_hash` 去重 upsert（`app/services/store.py`），同檔重複/改名都會被跳過。
- **匯入成本**：嵌入 BGE-M3 為 CPU-bound（~3 篇/分，10 核全滿），與線上服務搶 CPU 時可能短暫 502。
- **工具盤點**：`rsync` 3.2.7 有；`cifs-utils` 未裝（需一次性 `apt install`）；DB 預設 DSN `postgresql+asyncpg://postgres:postgres@localhost:5436/research`（可由 `DATABASE_URL` 覆寫）。

## 設計決策（已與使用者確認）

1. **存取方式**：先把 NAS 新檔同步到本地 `研報自動匯入/`，再對本地新檔跑管線（不直接讀網路磁碟）。
2. **同步機制**：Linux **cifs 唯讀掛載 + rsync**（不走 Windows robocopy/interop）。
3. **排程頻率**：每隔幾小時（預設 **每 3 小時**，`OnCalendar` 可調）。
4. **架構**：增量腳本 + systemd timer（oneshot），每次跑才載入 BGE-M3、跑完釋放；以 `nice`/`ionice` 降優先序，避免長期佔第二份模型記憶體。

### 否決的替代方案

- **常駐 watcher daemon**：24 小時多吃 ~1.2G RAM；對「每幾小時、量小」無效益。
- **走線上 web 服務的常駐模型（admin endpoint 匯入）**：嵌入 CPU-bound 會卡 serving event loop，跑時線上問答/檢索變慢甚至 502。

## 架構與資料流

```
report-mark-sync.timer  (OnCalendar：每 3 小時)
  └─ report-mark-sync.service  (Type=oneshot；nice -n 19 + ionice -c3)
       scripts/sync_new_reports.sh  ── 編排殼
         1. PID lock（上一輪未完則跳過本輪，不重疊）
         2. 確保 cifs 掛載存在（systemd mount unit 已掛則略過；未掛則嘗試掛）
         3. rsync NAS→本地 研報自動匯入/，--out-format 擷取「本次新傳檔案清單」
            → 寫入 data/sync_delta_<ts>.txt
         4. uv run python scripts/sync_new_reports.py --delta data/sync_delta_<ts>.txt
         5. 收尾：log 摘要、清理本次 delta 檔
       scripts/sync_new_reports.py  ── 增量處理器（重用既有 services）
         逐檔：
           extract_text(path) → file_hash + 文字 + scanned/語言；parse_filename → metadata
           ├ is_admin / scanned / 空文字     → 跳過（計數）
           ├ report_exists(file_hash) 已入庫 → 跳過（安全網）
           ├ claude -p (Haiku) 標註 → 非 is_research / 無 market → 跳過
           └ chunk_text(clean_extracted(text)) → embed_texts → upsert_report
              同步：寫 data/tags/<hash>.json + append data/extracted/all.jsonl（與批次工具一致）
         單檔例外不中斷整批 → data/sync_failures.log；結束印統計
```

### 三層去重

1. **rsync（size+mtime）**：只傳新增/變更檔，純 metadata 比對，不重讀 15G 內容。
2. **delta 清單**：只把本次新傳的檔餵進管線。
3. **DB `file_hash`（內容雜湊）**：最終安全網，改名/重跑/部分失敗重試都不會重複入庫。

> 首跑說明：本地 `研報自動匯入/` 已是同一 NAS 的舊副本，首次 rsync 的 delta 應該很小。即使 rsync 因 mtime 差異把已入庫檔誤標為新檔，處理器在 Claude 標註與嵌入「之前」就先 `report_exists(file_hash)` 短路跳過，誤判只付出 extract（讀檔+雜湊）的便宜成本，不會重跑昂貴的標註/嵌入。

## 元件清單（皆新增，不改既有檔）

| 檔案 | 角色 |
|------|------|
| `scripts/sync_new_reports.py` | 增量處理器：吃 delta 檔清單，逐檔 extract→tag→ingest，重用 `app.services.extract/filename/tagging/chunk/embed/store/textnorm`。CLI：`--delta <file>`、`--limit N`、`--batch-size 32`。 |
| `scripts/sync_new_reports.sh` | 編排殼：PID lock + nice/ionice + 掛載檢查 + rsync 擷取 delta + 呼叫處理器 + log。 |
| `deploy/systemd/report-mark-sync.service` | oneshot；`User=kashionz`、PATH drop-in（含 `claude`/`uv`/node）、WorkingDirectory 專案根、`ExecStart` 跑 `.sh`。 |
| `deploy/systemd/report-mark-sync.timer` | `OnCalendar=*-*-* 00/3:00:00`（每 3 小時）、`Persistent=true`（錯過補跑）。 |
| `deploy/systemd/mnt-nas-research.mount` | cifs 唯讀掛載 NAS 共享 → 本地掛載點；`x-systemd` 自動掛載；憑證走 `credentials=` 檔。 |
| `Makefile` 目標 | `sync-once`（手動跑一次同步，便於測試/補跑）。 |
| `docs/` 部署說明 | cifs-utils 安裝、憑證檔建立、systemd 安裝啟用步驟、維運排錯。 |

### 掛載點與憑證（一次性前置，需使用者提供 NAS 帳密）

- 掛載點：`/mnt/nas-research`（唯讀）。本地同步目的地仍為專案內 `研報自動匯入/`。
- 憑證檔：`/etc/report-mark-nas.cred`，`root:root 0600`，內容：
  ```
  username=<NAS 帳號>
  password=<NAS 密碼>
  domain=<選填工作群組/網域>
  ```
- cifs 掛載選項：`ro,credentials=/etc/report-mark-nas.cred,iocharset=utf8,uid=kashionz,gid=kashionz,vers=3.0`（CJK 路徑需 `iocharset=utf8`；`vers` 視 NAS 實際 SMB 版本微調）。
- 一次性：`sudo apt install cifs-utils`。

## 錯誤處理 / 維運

- **掛載失敗**：`.sh` 檢查掛載點不可用 → 記 log 後直接結束，**不跑 rsync、不污染 DB**。
- **rsync 失敗**：非 0 退出 → 記 log 後結束，不進處理器。
- **單檔失敗**：沿用既有 try/except + rollback 模式，寫 `data/sync_failures.log` 後續跑下一檔。
- **NUL 位元組**：沿用既有處理——`full_text` 走 `raw_text.replace("\x00","")`，tag prompt 也剝 NUL（避免 PDF 抽出文字含 `\x00` 永久失敗）。
- **重疊防護**：PID lock（沿用 `resume_corpus.sh` 的 `kill -0` 檢查模式）。
- **資源干擾**：`nice -n 19` + `ionice -c3`；增量量小 → CPU 飽和時間短；嵌入 `--batch-size` 可調。
- **開機自復原**：cifs mount unit 與 timer 由 systemd 自動拉起，無需手動（不像舊 portproxy）。
- **claude CLI 前置**：service 帶 PATH drop-in 確保 `claude` 可被 systemd 環境找到（沿用過往 systemd 無 PATH 導致問答壞掉的教訓）。

## 測試策略

- **單元測試**（`tests/`，pytest）：
  - delta 清單解析（rsync `--out-format` 輸出 → 檔路徑清單；忽略目錄列、刪除列）。
  - 過濾串判定（admin / scanned / 空 / 非研報 → 正確分類與計數）。
  - dedup 判定（`report_exists` 命中即跳過）以可注入的假 session 驗證，不需真連 DB。
- **端到端（手動）**：放一個測試 PDF 到本地 `研報自動匯入/` → `make sync-once` → 驗證入庫（`make stats` 篇數 +1）+ 檢索得到。
- **乾跑模式**：處理器支援 `--limit`／可加 `--dry-run`（只印將處理清單、不寫 DB）便於驗證選檔正確。

## 不做（YAGNI）

- 不做 inotify 即時監看（SMB 不可靠且使用者要的是定時）。
- 不做刪除同步（NAS 刪檔不連動刪 DB；本期只增不減）。
- 不做第二份常駐模型 / 不改線上服務。
- 不做 NAS→本地的雙向同步（單向唯讀拉取）。

## 部署步驟（落地時）

1. `sudo apt install cifs-utils`
2. 建立 `/etc/report-mark-nas.cred`（600，填 NAS 帳密）。
3. 安裝三個 systemd unit → `systemctl daemon-reload` → `enable --now` mount 與 timer。
4. `make sync-once` 手動驗證一次（先確認掛載 + rsync delta + 入庫）。
5. 觀察首次 timer 觸發 log（`data/sync_run_*.log`）。
