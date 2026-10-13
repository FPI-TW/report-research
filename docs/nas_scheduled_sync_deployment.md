# NAS 定時增量同步匯入 — 部署指南

辦公室主機（測試環境）專用：`report-mark-sync.timer` 每 3 小時整點 → oneshot service → `scripts/sync_new_reports.sh`。來源段以 drvfs 唯讀掛載 NAS（沿用 Windows Credential Manager 既有「Jacky Yeh」快取憑證，**免明文密碼**）→ rsync 擷取 delta；之後的匯入與 LLM 各段見 `docs/WORKFLOW.md`「生產同步鏈」。

正式環境（EC2）碰不到 NAS，不做本文的掛載與 sudoers：它以 `SYNC_SOURCE=r2-inbox` 從 R2 拉辦公室主機推上去的檔，unit 用 `deploy/install_units.sh` 安裝，見 `deploy/aws/README.md`「應用層」。

## 前置：掛載點 + root 包裝 + sudoers（一次性）

```bash
sudo mkdir -p /mnt/nas-research
sudo install -m 0755 deploy/systemd/mount-nas-research /usr/local/sbin/mount-nas-research
sudo install -m 0440 deploy/systemd/report-mark-sync.sudoers /etc/sudoers.d/report-mark-sync
sudo visudo -c        # 驗證 sudoers 語法
```

## 前置：部署環境檔（把主機專屬路徑留在本機）

```bash
# 只在還沒有這個檔時建立：既有的檔是這台主機的實際設定（備份落點、R2、信標等），不可覆蓋
[ -e /etc/default/report-mark-sync ] || sudo install -m 0644 deploy/systemd/report-mark-sync.env.example /etc/default/report-mark-sync
sudoedit /etc/default/report-mark-sync
```

systemd 的 `EnvironmentFile` 不做 shell 解析，而有的值含括號：不要在 shell 裡 `source` 這個檔。至少確認：

- `REPORT_MARK_ROOT`：本機 repo 根目錄。
- `SYNC_PATH_EXTRA`：讓 service 找得到 `uv` 的額外 bin 目錄。
- `EXTRACTOR=pdfplumber`：批次不讀 repo 根 `.env`，沒設會靜默走程式預設的 pypdf。
- `OBJECT_STORAGE_MODE` 與 `R2_*`：與 repo 根 `.env` 的同名設定一致。
- `SYNC_INBOX_PUSH=1`：每輪 rsync 完把新檔推到 R2 `inbox/` 給 EC2 拉；沒設的話 EC2 收不到新研報。搭配 `HOST_BEACON_NAME` 與 `report-mark-beacon.timer` 讓 EC2 監看這一段（`docs/production_resilience.md`「來源主機信標」）。

LLM 批次走 DeepSeek HTTP，金鑰另放 `/etc/default/report-mark-llm`（0640 root:kashionz，只有 sync 與上傳 worker 的 unit 載入），安裝與輪替見 `docs/production_resilience.md`「DeepSeek 金鑰落點與輪替」。

## 實測 drvfs 掛載（關鍵：確認免密碼讀得到）

```bash
sudo /usr/local/sbin/mount-nas-research
ls "/mnt/nas-research/02.研究資源/研報自動匯入" | head
```

預期：列得出 PDF 檔名。若失敗，多半是 Windows 端「Jacky Yeh」快取憑證失效——在 Windows 檔案總管手動連一次 `\\192.168.1.100\投資研究處`（勾「記住認證」）即可重存。

## 安裝排程單元

從部署目錄的 repo 根執行：

```bash
sudo install -m 0644 deploy/systemd/report-mark-sync.service \
  deploy/systemd/report-mark-sync.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemd-analyze verify /etc/systemd/system/report-mark-sync.service \
  /etc/systemd/system/report-mark-sync.timer
sudo systemctl enable --now report-mark-sync.timer
systemctl list-timers report-mark-sync.timer
```

## 手動驗證一次端到端

經由 unit 跑一輪（會跑完整一輪，含 LLM 各段，會計費）：

```bash
BASE=$(bash scripts/verify_oneshot_ran.sh baseline report-mark-sync.service)
sudo systemctl start --no-block report-mark-sync.service
tail -f data/sync_run_$(date +%Y%m%d).log      # 等到「=== sync done ===」
bash scripts/verify_oneshot_ran.sh verify report-mark-sync.service "$BASE"
make stats            # 確認 reports 篇數有隨新檔增加
```

不要拿 `make sync-once` 驗證部署：它直接執行 `scripts/sync_new_reports.sh`，不經過 unit，所以不會載入 `/etc/default/report-mark-sync`——`EXTRACTOR`、`SYNC_INBOX_PUSH`、`OBJECT_STORAGE_MODE` 與 `R2_*` 都退回程式預設（pypdf、不推 inbox、`local`）。要手動跑就自己把這些值帶在命令列。

## 維運排錯

- 線上變慢：匯入與各 LLM 段跑在 `nice -n 19` ＋ `ionice -c3`，不該搶線上服務。觸發時刻不要隨手改：EC2 的 sync 刻意比這台晚 20 分鐘（`deploy/aws/report-mark-sync.timer.d/offset.conf`），這台改了那邊要跟著改。
- 掛載失敗：service 記 log 並以 rc=1 結束、不跑 rsync、不動 DB；下次 timer 自動再試。先看 NAS 是否在線、Windows 的快取憑證，再跑 `sudo -n /usr/local/sbin/mount-nas-research`。EC2 那端會以來源主機信標的 `source_failed` 告警。
- 單檔失敗：見 `data/sync_failures.log`；修因後用 `scripts/failures_to_delta.py` 轉 delta 重放，不要 `--all-local`（只有匯入撞鎖或上一輪被砍且 delta 不可靠時才用；整批中止的重放見 `docs/production_resilience.md`「整批中止後的重放」）。
- LLM 段整批 rc=2：多半是 DeepSeek 金鑰或餘額（401／402），處置見 `docs/production_resilience.md`「DeepSeek 批次的失敗處置」；402 是儲值，沒有備援後端。
- service 啟不來：先看 `/etc/default/report-mark-sync` 的 `REPORT_MARK_ROOT` 與 `SYNC_PATH_EXTRA` 是否指到實機正確路徑。
