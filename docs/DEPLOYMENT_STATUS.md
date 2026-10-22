# 部署現況與待確認事項

主機上的部署狀態不在 repo 裡，這份文件記的是最後一次到主機查到的結果。每條括號裡是查證日期；標「待確認」的＝那之後沒有重新查證，不可當成現況直接操作，動手前先在該主機核對（schema 版本用 `make schema-version`）。標「repo 可查」的以程式與設定檔為準。狀態一變就改這份；已成為常態的事寫進對應文件後從這裡刪掉。

最後一次整份查證：2026-10-13，兩個環境的程式都在 main `58e2e4f`。

- **環境角色與站序**：見 `AGENTS.md`「環境角色與資料安全」。
- **schema 版本**（2026-10-13）：兩個環境都在 revision 0011（目前的 head）。
- **正式環境（EC2）**（2026-10-13）：
  - 已裝並啟用：`report-mark-web`、`report-mark-ops-agent-staging`，以及 sync、freshness、audit、r2-reconcile、schema-check、db-snapshot、analytics-rollup、security-retention 的 timer，和 health／incident、edge-health／edge-incident、security-health／security-incident、feed-health／feed-incident 四組探針與事件。
  - 沒裝：監控收集與匯入（metrics、load-observations、rollup-observations）、容器與主機探針（container-*、host-*）、linebot 探針、應用層備份（backup）、稽核錨定（audit-anchor）、檢索回歸（retrieval-regression）、上傳 worker（upload）與 ClamAV、回填（backfill）、信標發布（beacon）。本專案在 EC2 不用容器。
  - 2026-10-22 補裝並啟用刪帳執行與重放（delete-accounts、replay-deletions）：tombstone 寫本機磁碟 `/var/lib/report-mark/tombstones`（`REPORT_MARK_BACKUP_DIR`，`/etc/default/report-mark-sync`）；首跑執行了 1 筆積壓的到期刪除，重放全部乾淨。
  - 連帶的現況：備份只有 RDS 自動備份；管理後台不列主機、排程工作、事件三頁（服務清單裡沒有 load-observations 時前端自動隱藏）。
  - repo 可查：`deploy/ops/services.staging.toml` 只列已安裝的 unit；兩個環境的差異表在 `docs/production_resilience.md`「v2 功能的啟用矩陣」。
- **測試環境（辦公室主機）**（2026-10-13）：`deploy/systemd/` 裡屬於這台的 unit 全裝且 timer 都啟用（沒裝的只有 EC2 專用的 feed-health／feed-incident、`report-mark-ops-agent-staging`，與開發用的 dev-smoke、ops-agent-dev），含 ClamAV 容器與上傳 worker。刪帳執行、刪帳重放、稽核錨定寫 NAS（`REPORT_MARK_BACKUP_DIR`）：2026-10-22 起這三支執行前自己掛 NAS（`scripts/ensure_backup_mount.sh`），WSL 重開後不必等每日備份；要立刻手動掛時要在 systemd 的命名空間裡做：`sudo systemd-run --wait --pipe /usr/local/sbin/mount-nas-backup`（互動 shell 裡 `sudo mount-nas-backup` 掛的，服務看不到）。
- **來源主機信標**（2026-10-13）：兩台都已啟用——辦公室主機設 `HOST_BEACON_NAME=office` 並跑 `report-mark-beacon.timer`，EC2 設 `HOST_BEACON_WATCH=office` 並跑 feed-health／feed-incident。**告警還沒演練過**（沒有實際驗證過失聯或推送失敗時通知會送達）。處置與停用見 `docs/production_resilience.md`「來源主機信標」。
- **開關**（2026-10-13）：兩個環境的環境檔都沒有設 `UPLOAD_ENABLED`、`QUOTA_ENFORCE`、`ADMIN_MFA_REQUIRED`，走程式預設（關、影子模式、關；`app/config.py`）；都設 `ASK_ENABLE_WEB=0`；功能旗標沒有任何 DB 覆寫（`ask.web_search` 的預設是關，`app/services/feature_flags.py`）。
- **網搜**（repo 可查）：沒有後端（DeepSeek 版尚未實作），後端對網搜請求一律以設定錯誤拒絕，前端 `frontend/src/lib/useWebSearch.ts` 依功能旗標隱藏開關。後端完成後把 `ASK_ENABLE_WEB` 設 1、再到管理後台「功能旗標」覆寫開啟即可，前端不必改。
- **物件儲存與金鑰共用**（2026-10-13）：兩個環境都是 `OBJECT_STORAGE_MODE=r2`，共用同一個 R2 bucket 與 DeepSeek 金鑰。在測試環境刪改 R2 物件或手動多跑 LLM 批次，會影響正式環境的資料或費用。
- **`pg_stat_statements`**（2026-10-13）：兩個環境都已啟用；EC2 的 app 帳號沒有 `pg_read_all_stats`，別人的語句文字會隱藏。
- **夜間回填** `report-mark-backfill.timer`（2026-10-13）：測試環境仍啟用、還沒跑完（10/13 清晨那一輪結束時估計尚餘 869 篇）。journal 的「估計尚餘」歸零後由人手動 disable。
- **帳號**（2026-10-13）：測試環境只有一位 `tester`（super admin），還沒建個人帳號。正式環境有 5 個帳號：4 位管理員（其中 1 位已停用）與舊的共用帳號 `staging`（已降為一般使用者，仍啟用）；共用帳號尚未停用。
- **環境檔的舊鍵**（2026-10-13）：兩個環境的 repo 根 `.env` 都還留著 `REPORT_MARK_ACCESS_USERNAME`／`REPORT_MARK_ACCESS_PASSWORD`（共用帳密時代的鍵，只剩 `scripts/create_admin.py --from-env` 會讀）。待刪。
