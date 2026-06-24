# 定時匯入後自動生成摘要 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 讓定時 NAS 增量同步在「本輪真的有新研報成功入庫」時，自動 best-effort 補上摘要，且摘要只針對這一輪新增的 `file_hash`，失敗不擋 sync。

**Architecture:** `sync_new_reports.py` 在非 dry-run 結尾把本輪成功入庫的 `file_hash` 清單寫到 `data/.sync_last_hashes`；`sync_new_reports.sh` 在匯入成功後以 `[ -s data/.sync_last_hashes ]` gate 摘要步驟；若 gate 成功，呼叫 `generate_summaries.py --hashes-file data/.sync_last_hashes`，並用 `|| log` 維持 best-effort。整體設計以 hash-based scope 限縮 token 消耗，不掃歷史 `summary IS NULL` 積壓。

**Tech Stack:** Python 3.13（async、asyncio）、bash（`set -euo pipefail`）、systemd oneshot、零工具鏈 `unittest`、`uv`。

## Global Constraints

- 設計文件：`docs/superpowers/specs/2026-06-23-sync-auto-summary-design.md`（所有需求以此為準）。
- 測試＝零工具鏈 `unittest`；純函式測試不碰 DB。
- 標記檔常數路徑：`data/.sync_last_hashes`（相對 ROOT；shell cwd 也是 ROOT）。
- `generate_summaries.py` 屬於本次變更範圍，且手動執行時仍要維持「不帶 `--hashes-file` 就補全表 NULL」的既有行為。
- shell 受 `set -euo pipefail`：摘要步驟必須 `|| log ...` 收尾，否則非零退出會終結整個 sync。
- marker 寫入要維持原子替換，且暫存檔名不能假設只有單一執行個體。
- 提交訊息用 Conventional Commits + 繁中。

---

### Task 1: `sync_new_reports.py` 輸出本輪成功入庫的 hash 清單

**Files:**
- Modify: `scripts/sync_new_reports.py`
- Test: `tests/test_sync_ingested_hashes.py`

**Interfaces:**
- Produces: `INGESTED_HASHES_FILE = ROOT / "data" / ".sync_last_hashes"`
- Produces: `write_ingested_hashes(path: Path, hashes: list[str]) -> None`
- Behavior: `_run()` 收集每筆成功 `upsert` 的 `res.file_hash`，非 dry-run 結尾寫入 marker；空清單寫 0-byte 檔。

- [ ] 新增 `INGESTED_HASHES_FILE` 常數。
- [ ] 新增 `write_ingested_hashes()`，用唯一暫存檔原子寫入每行一個 hash。
- [ ] 在 `_run()` 收集 `ingested_hashes`，並於非 dry-run 結尾寫入 marker。
- [ ] 補 `tests/test_sync_ingested_hashes.py`，驗證換行格式、空清單、覆寫、建父目錄與常數路徑。

### Task 2: `generate_summaries.py` 支援 hash-based 範圍化

**Files:**
- Modify: `scripts/generate_summaries.py`
- Test: `tests/test_summary.py`

**Interfaces:**
- Produces: `read_hashes_file(path) -> list[str]`
- Extends: `fetch_candidates(limit, hashes=None)`
- Extends: CLI `--hashes-file`

- [ ] 新增 `read_hashes_file()`，讀取每行一個 `file_hash`，去除空白行。
- [ ] `fetch_candidates()` 在 `hashes is None` 時維持補全表 `summary IS NULL`；有 hash 清單時追加 `AND file_hash = ANY(:hashes)`。
- [ ] `main()` 與 argparse 接入 `--hashes-file`。
- [ ] 補 `tests/test_summary.py` 驗證 hash 檔讀取行為。

### Task 3: `sync_new_reports.sh` 在匯入成功後觸發 best-effort 摘要

**Files:**
- Modify: `scripts/sync_new_reports.sh`
- Modify: `deploy/systemd/report-mark-sync.env.example`

- [ ] 在匯入成功後、刪除 delta 前，加入 `HASHES=data/.sync_last_hashes` gate。
- [ ] `[ -s "$HASHES" ]` 成立時呼叫：
  `nice -n 19 ionice -c3 "$UV" run python scripts/generate_summaries.py --hashes-file "$HASHES" ...`
- [ ] 保留 `${SYNC_SUMMARY_WORKERS:+...}` 的可選並發注入與 `|| log "摘要生成非零退出（best-effort，已略過）"`。
- [ ] env example 補 `SYNC_SUMMARY_WORKERS` 註解。

### Task 4: 驗證

- [ ] `uv run python -m unittest tests.test_summary tests.test_sync_ingested_hashes -v`
- [ ] `bash -n scripts/sync_new_reports.sh`
- [ ] 若環境允許，跑一次 `make sync-once` 或等價 smoke，確認：
  - 本輪有新 hash 時會進摘要步驟
  - 本輪無新 hash 時直接跳過
  - 摘要非零退出不會讓 sync 失敗

## Success Criteria

- 只有本輪成功入庫的 `file_hash` 會在同輪被送進摘要生成。
- `generate_summaries.py` 手動執行時仍可維持補全表 `summary IS NULL` 的既有行為。
- 歷史 NULL 積壓不會被定時流程每 3 小時反覆掃描。
- 摘要流程失敗時，只記 log，不影響 sync 成功與否。
