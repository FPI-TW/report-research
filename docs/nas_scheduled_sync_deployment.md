# NAS 定時增量同步匯入 — 部署指南

排程：systemd timer 每 3 小時 → oneshot service → `scripts/sync_new_reports.sh`。
同步：drvfs 唯讀掛載 NAS（沿用 Windows Credential Manager 既有「Jacky Yeh」快取
憑證，**免明文密碼**）→ rsync 擷取 delta → 只對新檔 extract→tag→ingest。

## 前置：掛載點 + root 包裝 + sudoers（一次性）

```bash
sudo mkdir -p /mnt/nas-research
sudo install -m 0755 deploy/systemd/mount-nas-research /usr/local/sbin/mount-nas-research
sudo install -m 0440 deploy/systemd/report-mark-sync.sudoers /etc/sudoers.d/report-mark-sync
sudo visudo -c        # 驗證 sudoers 語法
```

## 實測 drvfs 掛載（關鍵：確認免密碼讀得到）

```bash
sudo /usr/local/sbin/mount-nas-research
ls "/mnt/nas-research/02.研究資源/研報自動匯入" | head
```

預期：列得出 PDF 檔名。若失敗，多半是 Windows 端「Jacky Yeh」快取憑證失效——
在 Windows 檔案總管手動連一次 \\192.168.1.100\投資研究處（勾「記住認證」）即可重存。

## 安裝排程單元

```bash
sudo cp deploy/systemd/report-mark-sync.service deploy/systemd/report-mark-sync.timer \
  /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now report-mark-sync.timer
systemctl list-timers report-mark-sync.timer
```

## 手動驗證一次端到端

```bash
make sync-once
tail -n 40 data/sync_run_$(date +%Y%m%d).log
make stats            # 確認 reports 篇數有隨新檔增加
```

## 維運排錯

- 502 / 線上變慢：匯入跑在 nice -n 19 + ionice -c3；量大時段可把 timer 改較少頻率
  （改 OnCalendar，如每日凌晨 `*-*-* 03:00:00`）。
- 掛載偶發失敗：service 會記 log 並早退、不動 DB；下次 timer 自動再試。
- 單檔失敗：見 data/sync_failures.log；修因後可 `make sync-once` 或
  `uv run python scripts/sync_new_reports.py --all-local` 全本地對 DB 補漏。
- claude CLI 找不到：確認 service 的 PATH drop-in 含 node bin 目錄。
