# 部署現況與待確認事項

主機上的部署狀態不在 repo 裡。每條標出最後一次紀錄（日期與 commit）；**「待確認」＝該紀錄之後沒有重新查證，不可當成現況直接操作**，動手前先在該主機核對（schema 版本用 `make schema-version`）。標「repo 可查」的以程式與設定檔為準。狀態一變就改這節；已成為常態的事寫進對應文件後從這裡刪掉。

- **環境角色與站序**：見 `AGENTS.md`「環境角色與資料安全」。
- **schema 版本**（待確認；紀錄 2026-10-08，`c14eee6`）：兩個環境都在 revision 0011（目前的 head）。
- **正式環境（EC2）沒裝的元件**（待確認；紀錄 2026-10-07～08，`9c1d15e`、`c14eee6`）：ClamAV 與上傳 worker、檢索回歸、監控收集與事件投影、刪帳執行與重放（`report-mark-delete-accounts` 沒裝＝排程刪除不會執行）、稽核錨定、應用層 NAS 備份（只有 RDS 自動備份）。repo 可查：`deploy/ops/services.staging.toml` 只列已安裝的 unit；兩個環境的差異表在 `docs/production_resilience.md`「v2 功能的啟用矩陣」。
- **測試環境（辦公室主機）**（待確認；紀錄同上）：Admin v1／v1.5／v2 的 unit 全裝，含 ClamAV＋上傳 worker。已知缺口：刪帳執行、刪帳重放、稽核錨定要寫 NAS（`REPORT_MARK_BACKUP_DIR`），而 NAS 只在每日備份（`scripts/db_backup.sh`）時掛載；WSL 重啟後到下次備份前，這三支會以 rc=2 告警。手動補掛要在 systemd 的命名空間裡做：`sudo systemd-run --wait --pipe /usr/local/sbin/mount-nas-backup`（互動 shell 裡 `sudo mount-nas-backup` 掛的，服務看不到）。病毒碼過期的處置見 `docs/production_resilience.md`「ClamAV（上傳掃描）」。
- **開關**：程式預設（repo 可查）是 `UPLOAD_ENABLED`=0、`QUOTA_ENFORCE`=0（影子模式）、`ADMIN_MFA_REQUIRED` 關（`app/config.py`），功能旗標 `ask.web_search` 關（`app/services/feature_flags.py`）。主機設定（待確認；紀錄 2026-10-08，`c14eee6`）：兩個環境都維持這些值、設 `ASK_ENABLE_WEB=0`、功能旗標沒有任何 DB 覆寫。
- **物件儲存與金鑰共用**（待確認）：測試環境用 `OBJECT_STORAGE_MODE=r2`（紀錄早於 2026-09-30，當時寫作「生產」）；正式環境與它共用同一個 R2 bucket 與 DeepSeek 金鑰（repo 記載：`README.md`「非辦公室主機」一節、`deploy/ops/services.staging.toml` 檔頭）。在測試環境刪改 R2 物件或手動多跑 LLM 批次，會影響正式環境的資料或費用。
- **R2 對帳**（紀錄 2026-10-12）：兩個環境缺 `source_object_key` 的研報都已歸零（2026-09-25 手動補入庫的 18 篇：17 篇補傳原檔並寫入鍵，1 篇原檔遺失且已被重發版取代、兩邊都刪除）。測試環境 2026-10-12 的對帳退出碼 0；剩下的 `orphans=2` 是只有正式環境入庫的研報（共用 bucket），不是垃圾，判讀見 `docs/WORKFLOW.md`。正式環境要等更新到含 `409114f` 的版本後再看對帳結果（待確認）。
- **網搜暫停**（repo 可查）：前端 `useWebSearchPaused()` 讀 `/api/features` 隱藏開關、請求一律送 `web=false`；網搜沒有後端（claude CLI 的 WebSearch 已移除、DeepSeek 版尚未實作），上限 `ASK_ENABLE_WEB` 的程式預設是 0、旗標預設也關，後端對網搜請求一律以設定錯誤拒絕。DeepSeek 版網搜完成後，上限設 1、再到管理後台「功能旗標」頁覆寫開啟即恢復（前端不必改）。
- **`pg_stat_statements`**（待確認；紀錄 2026-10-08，`43b88c8`）：兩個環境都已啟用；EC2 的 app 帳號沒有 `pg_read_all_stats`，別人的語句文字會隱藏。
- **Claude CLI 已移除（PR-M）**（repo 可查）：LLM 只剩 DeepSeek；`claude_cli`／`claude_only` 是退役值（web 記 ERROR 並當成 `deepseek`，批次預檢 rc=2 拒跑）；`report-mark-web.service.d/` 的 PATH drop-in 已從 repo 刪除，探針退出碼 5 退役。主機端的收尾（步驟見 `docs/production_resilience.md`「PR-M 部署步驟」）：**測試環境已完成**（2026-10-12，`36ded3f`：drop-in 已刪、health 等 7 支 unit 重裝、web 重啟後 `/healthz/llm` 回 `ok`、啟動自檢沒有 ERROR）；**正式環境還沒做**（紀錄 2026-10-12：EC2 的 checkout 在 `1117291`、drop-in 還在；環境檔是 `LLM_PROVIDER=deepseek`、沒有 `*_MODEL=claude-…`）。正式環境沒做收尾前功能不受影響，drop-in 只是多一段用不到的 PATH。
- **夜間回填** `report-mark-backfill.timer`（待確認是否仍 enabled；紀錄早於 2026-09-30）：跑完（journal 的「估計尚餘」歸零）後由人手動 disable。
- **深度研報已移除**（2026-09-18，`46a8ace`）：既有庫要手動跑 `db/drop_deep_report_tables.sql`；各庫是否已跑：待確認。
- **共用帳密收尾**（待確認；紀錄 2026-10-07）：當時測試環境的環境檔還有 `REPORT_MARK_ACCESS_USERNAME`／`_PASSWORD`（web 啟動記 warning）；正式環境的舊共用帳號 `staging` 已降為一般使用者並撤銷 session；測試環境唯一的 `tester` 隨 revision 0002 成為 super admin。尚未為每位同事建個人帳號並停用共用或測試帳號。
