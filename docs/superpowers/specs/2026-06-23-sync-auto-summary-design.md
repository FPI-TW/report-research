# 定時匯入後自動生成摘要設計

日期：2026-06-23
狀態：設計確認，待實作

## 背景與目標

定時 NAS 增量同步（`report-mark-sync.timer`，每 3h）的匯入主流程
`sync_new_reports.sh → sync_new_reports.py` 在匯入每篇研報時**已自動標籤**
（`extract → tag(Haiku) → chunk/embed → upsert`，標到非研究/無市場者被濾掉）。

但**摘要不會自動產生**：

1. `sync_new_reports.py` 組 `ReportRow` 時沒有 `summary` 欄位 → 新匯入的研報 `summary` 一律 `NULL`。
2. `generate_summaries.py`（`make summaries`，Sonnet）是獨立離線工具，**沒有接進排程**。
3. timer 的 service `ExecStart` 只跑 `sync_new_reports.sh`，裡面不呼叫摘要腳本。

結果：新研報定時匯入後有標籤、但摘要永遠是空的，得人工跑一次 `make summaries` 才補上。

**目標**：把摘要生成接進定時匯入流程，讓新匯入的研報在同一輪 sync 內自動補上摘要。

## 範圍

- 動四個檔：`scripts/sync_new_reports.py`（寫本輪 hash 標記）、
  `scripts/generate_summaries.py`（加 `--hashes-file` 範圍化）、
  `scripts/sync_new_reports.sh`（接摘要步驟）、
  `deploy/systemd/report-mark-sync.env.example`（加可選 env 註解）。
- **不動** 標籤流程、timer/service unit。

## 決策（已與使用者確認）

| 決策 | 選擇 | 理由 |
|------|------|------|
| 失敗耦合 | **Best-effort，不擋 sync** | 摘要失敗只記 log，sync 仍 exit 0；匯入與摘要解耦最穩。 |
| 處理範圍 | **只補本輪新匯入的 file_hash** | 定時流程以「新研報」為主，避免每輪掃歷史 NULL 積壓過度消耗 token；歷史積壓另以手動 `make summaries` 補。 |
| 觸發時機 | **本輪有入庫（hash 清單非空）才跑** | 沒有新研報入庫時完全不動，省資源；不會每 3h 對歷史積壓空轉。 |

組合語意：**import 成功且本輪真的有新研報入庫 → 才跑摘要；只補本輪這批 file_hash（不掃歷史積壓）；摘要失敗只記 log、不擋 sync。**

> **修訂（2026-06-23）**：原設計「跑時補全部 `summary IS NULL`」改為「只補本輪新匯入的 file_hash」，以控制定時流程的 token 消耗。歷史 NULL 積壓改由手動 `make summaries`（不帶 `--hashes-file`，仍補全表 NULL）處理。

## 設計

### 1. `scripts/sync_new_reports.py`（露出本輪入庫的 file_hash 清單）

shell 要 gate 在「本輪有入庫」，且要把「本輪這批」範圍化給摘要，需從 python 露出 file_hash 清單。

- 純函式 `write_ingested_hashes(path: Path, hashes: list[str]) -> None`：把清單原子寫入標記檔（每行一個 file_hash，每輪覆寫）；**空清單寫 0-byte 檔**，殼層 `[ -s file ]` 視為「無新研報」。
- `_run()` 內收集本輪成功 upsert 的 `res.file_hash` 至 `ingested_hashes`；結尾、**非 dry-run** 時寫入 `data/.sync_last_hashes`（常數 `INGESTED_HASHES_FILE`）。
- 純函式便於零工具鏈 unittest，且不引入重型相依。

### 2. `scripts/generate_summaries.py`（加 `--hashes-file` 範圍化）

- 純函式 `read_hashes_file(path) -> list[str]`：讀殼層寫的 file_hash 清單（去空白行）。
- `fetch_candidates(limit, hashes=None)`：`hashes=None`（預設、手動 `make summaries`）＝補全表 `summary IS NULL`；`hashes` 為清單＝額外 `AND file_hash = ANY(:hashes)`，空清單直接回空、不查 DB。
- 新增 CLI `--hashes-file`；不給＝維持原行為（全表 NULL），手動補積壓不受影響。

### 3. `scripts/sync_new_reports.sh`（接摘要步驟）

在現有「匯入成功」檢查通過、`rm -f "$DELTA"` 之前，插入第 4 步：

```bash
# 4) 本次有新研報入庫才補摘要（best-effort：失敗只記 log，不擋 sync）
#    僅針對本輪新匯入的 file_hash（--hashes-file），不掃歷史 NULL 積壓
HASHES=data/.sync_last_hashes
if [ -s "$HASHES" ]; then
  N=$(grep -c . "$HASHES" 2>/dev/null || echo 0)
  log "本次新增 ${N} 篇 → 生成摘要（Sonnet，僅本輪新研報）"
  nice -n 19 ionice -c3 "$UV" run python scripts/generate_summaries.py \
    --hashes-file "$HASHES" \
    ${SYNC_SUMMARY_WORKERS:+--workers "$SYNC_SUMMARY_WORKERS"} >>"$LOG" 2>&1 \
    || log "摘要生成非零退出（best-effort，已略過）"
else
  log "本次無新研報入庫 → 跳過摘要"
fi
```

要點：
- `set -euo pipefail` 下**必須**用 `|| log ...` 收尾，否則摘要非零退出會直接終結整個 sync，違反 best-effort。
- `[ -s "$HASHES" ]`（檔存在且非空）作 gate；空清單檔（0-byte）視為無新研報、跳過。
- 沿用 `nice -n 19 ionice -c3` 不搶線上 web 服務；沿用 `$UV`。
- `claude`(Sonnet) 已在 PATH（標籤步驟同進程已用 claude CLI，service 也設了 `SYNC_PATH_EXTRA`）。

### 4. `deploy/systemd/report-mark-sync.env.example`（可選並發 env）

加註解說明可選 `SYNC_SUMMARY_WORKERS`（預設不設＝沿用腳本預設 6 並發），
讓伺服器依 Sonnet 額度/CPU 調整摘要並發數。

## 資料流

```
timer(每3h) → sync_new_reports.sh
  ├ mount 檢查 → rsync(delta)
  ├ 增量匯入 sync_new_reports.py  ── 寫 data/.sync_last_hashes（本輪入庫 file_hash，每行一個）
  └ if [ -s hashes ]: generate_summaries.py --hashes-file（只補本輪這批, best-effort）; else 跳過
```

## 錯誤處理

- 摘要步驟非零退出：記 log「best-effort，已略過」，sync 仍視為成功（exit 0）。
- 個別摘要失敗：`generate_summaries.py` 既有機制記 `data/summary_failures.log`，列仍為 NULL；不會在後續定時輪自動補（已不在本輪 hash 清單內），改由手動 `make summaries` 補。
- 標記檔缺失/空：`[ -s "$HASHES" ]` 為偽 → 不跑摘要（安全預設）。

## 測試

- **單元（零工具鏈 unittest）**：
  - `tests/test_sync_ingested_hashes.py` 驗 `write_ingested_hashes` 正確換行寫入、覆寫、空清單寫 0-byte 檔、建父目錄、常數指向 `.sync_last_hashes`。
  - `tests/test_summary.py` 加 `read_hashes_file` 驗讀檔去空白行、空檔回空清單。
- **整合煙霧**：`generate_summaries.py --hashes-file <bogus hash>` 對 live DB 走 `file_hash = ANY(:hashes)` 查詢、回 0 候選不呼叫 Sonnet（驗 SQL 與驅動相容）。
- **手動**：`make sync-once` 跑一輪，log 出現「生成摘要（僅本輪新研報）」段，且本輪新研報 `summary` 被補；無新檔時 log 出現「跳過摘要」。

## 取捨

- gating 與範圍都用精準的「本輪入庫 file_hash」（而非 rsync 的 `NEW>0` 或全表 NULL）：新檔可能整批被 admin/scanned/exists/non-research 濾掉；用 file_hash 清單既正確 gate、又把摘要嚴格限縮在本輪新研報，控制 token 消耗。
- 歷史 NULL 積壓與 best-effort 失敗的殘檔**不**由定時流程自動補（避免每 3h 反覆嘗試壞檔、吃 token）；改由手動 `make summaries`（不帶 `--hashes-file`，補全表 NULL）一次補齊。
- 不在 `sync_new_reports.py` 逐檔即時生成摘要：避免每篇多一次 Sonnet 呼叫拖慢匯入，也讓 best-effort 失敗語意單純（一個獨立子程序）。
