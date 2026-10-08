# 部署現況與待確認事項

主機上的部署狀態不在 repo 裡。每條標出最後一次紀錄（日期與 commit）；**「待確認」＝該紀錄之後沒有重新查證，不可當成現況直接操作**，動手前先在該主機核對（schema 版本用 `make schema-version`）。標「repo 可查」的以程式與設定檔為準。狀態一變就改這節；已成為常態的事寫進對應文件後從這裡刪掉。

- **環境角色與站序**：見 `AGENTS.md`「環境角色與資料安全」。
- **schema 版本**（待確認；紀錄 2026-10-08，`c14eee6`）：兩個環境都在 revision 0011（目前的 head）。
- **正式環境（EC2）沒裝的元件**（待確認；紀錄 2026-10-07～08，`9c1d15e`、`c14eee6`）：ClamAV 與上傳 worker、檢索回歸、監控收集與事件投影、刪帳執行與重放（`report-mark-delete-accounts` 沒裝＝排程刪除不會執行）、稽核錨定、應用層 NAS 備份（只有 RDS 自動備份）。repo 可查：`deploy/ops/services.staging.toml` 只列已安裝的 unit；兩個環境的差異表在 `docs/production_resilience.md`「v2 功能的啟用矩陣」。
- **測試環境（辦公室主機）**（待確認；紀錄同上）：Admin v1／v1.5／v2 的 unit 全裝，含 ClamAV＋上傳 worker。已知缺口：刪帳執行、刪帳重放、稽核錨定要寫 NAS（`REPORT_MARK_BACKUP_DIR`），而 NAS 只在每日備份（`scripts/db_backup.sh`）時掛載；WSL 重啟後到下次備份前，這三支會以 rc=2 告警。手動補掛要在 systemd 的命名空間裡做：`sudo systemd-run --wait --pipe /usr/local/sbin/mount-nas-backup`（互動 shell 裡 `sudo mount-nas-backup` 掛的，服務看不到）。病毒碼過期的處置見 `docs/production_resilience.md`「ClamAV（上傳掃描）」。
- **開關**：程式預設（repo 可查）是 `UPLOAD_ENABLED`=0、`QUOTA_ENFORCE`=0（影子模式）、`ADMIN_MFA_REQUIRED` 關（`app/config.py`），功能旗標 `ask.web_search` 關（`app/services/feature_flags.py`）。主機設定（待確認；紀錄 2026-10-08，`c14eee6`）：兩個環境都維持這些值、設 `ASK_ENABLE_WEB=0`、功能旗標沒有任何 DB 覆寫。
- **網搜暫停**（repo 可查）：前端 `useWebSearchPaused()` 讀 `/api/features` 隱藏開關、請求一律送 `web=false`；旗標預設關，所以沒有 DB 覆寫時即使 `ASK_ENABLE_WEB` 未設（＝1）也不開放；`.env.example` 設 `ASK_ENABLE_WEB=0`。DeepSeek 版網搜完成後，上限設 1、再到管理後台「功能旗標」頁覆寫開啟即恢復（前端不必改）。
- **`pg_stat_statements`**（待確認；紀錄 2026-10-08，`43b88c8`）：兩個環境都已啟用；EC2 的 app 帳號沒有 `pg_read_all_stats`，別人的語句文字會隱藏。
- **Claude CLI 退場（PR-M）**（repo 可查；2026-10-08 以 `git fetch` 核對）：分支 `feat/deepseek-remove-cli` 未合併。現在只剩網搜解析到 Claude；`claude_cli`／`claude_only` 仍是合法 provider 值但沒有可用後端；`deploy/systemd/report-mark-web.service.d/path.conf` 還在；探針退出碼 5（claude 依賴）由 `report-mark-health.service` 的空 `HEALTH_DEP_DROPIN=` 停用。合併時一起改：本文件、`AGENTS.md`、`tests/conftest.py` 的 provider 說明、`tests/test_deploy_units.py` 對 `path.conf` 的檢查、改動對照表「model 只用白名單或 `claude-*`」那句。
- **夜間回填** `report-mark-backfill.timer`（待確認是否仍 enabled；紀錄早於 2026-09-30）：跑完（journal 的「估計尚餘」歸零）後由人手動 disable。
- **深度研報已移除**（2026-09-18，`46a8ace`）：既有庫要手動跑 `db/drop_deep_report_tables.sql`；各庫是否已跑：待確認。
- **共用帳密收尾**（待確認；紀錄 2026-10-07）：當時測試環境的環境檔還有 `REPORT_MARK_ACCESS_USERNAME`／`_PASSWORD`（web 啟動記 warning）；正式環境的舊共用帳號 `staging` 已降為一般使用者並撤銷 session；測試環境唯一的 `tester` 隨 revision 0002 成為 super admin。尚未為每位同事建個人帳號並停用共用或測試帳號。
