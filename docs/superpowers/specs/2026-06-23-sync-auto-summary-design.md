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

- 動三個檔：`scripts/sync_new_reports.py`（小改）、`scripts/sync_new_reports.sh`（接步驟）、
  `deploy/systemd/report-mark-sync.env.example`（加可選 env 註解）。
- **不動** `generate_summaries.py`（已冪等、newest-first、只補 NULL、失敗自記 log）。
- **不動** 標籤流程、timer/service unit。

## 決策（已與使用者確認）

| 決策 | 選擇 | 理由 |
|------|------|------|
| 失敗耦合 | **Best-effort，不擋 sync** | 摘要失敗只記 log，sync 仍 exit 0；殘檔下輪自動重試，匯入與摘要解耦最穩。 |
| 處理範圍 | **補全部 `summary IS NULL`** | 沿用 `generate_summaries.py` 原行為；順手補掉先前 best-effort 失敗的殘留積壓。 |
| 觸發時機 | **只有本次 `ingested>0` 才跑** | 沒有新研報入庫時完全不動，省資源；不會每 3h 對歷史積壓空轉。 |

組合語意：**import 成功且本次真的有新研報入庫 → 才跑摘要；跑時補全部 NULL（含殘留積壓）；摘要失敗只記 log、不擋 sync。**

## 設計

### 1. `scripts/sync_new_reports.py`（露出 ingested 數）

shell 要 gate 在「本次 ingested>0」，需從 python 把這個數字露出來。

- 抽純函式 `write_ingested_marker(path: Path, n: int) -> None`：把整數 `n` 原子寫入標記檔（每輪覆寫）。
- 在 `_run()` 結尾、**非 dry-run** 時呼叫，寫入 `data/.sync_last_ingested`（常數 `INGESTED_MARKER`）。
- 純函式便於零工具鏈 unittest，且不引入重型相依。

### 2. `scripts/sync_new_reports.sh`（接摘要步驟）

在現有「匯入成功」檢查通過、`rm -f "$DELTA"` 之前，插入第 4 步：

```bash
# 4) 本次有新研報入庫才補摘要（best-effort：失敗只記 log，不擋 sync）
INGESTED=$(cat data/.sync_last_ingested 2>/dev/null || echo 0)
if [ "${INGESTED:-0}" -gt 0 ]; then
  log "本次新增 ${INGESTED} 篇 → 生成摘要（Sonnet，補 summary IS NULL）"
  nice -n 19 ionice -c3 "$UV" run python scripts/generate_summaries.py \
    ${SYNC_SUMMARY_WORKERS:+--workers "$SYNC_SUMMARY_WORKERS"} >>"$LOG" 2>&1 \
    || log "摘要生成非零退出（best-effort，已略過）"
else
  log "本次無新研報入庫 → 跳過摘要"
fi
```

要點：
- `set -euo pipefail` 下**必須**用 `|| log ...` 收尾，否則摘要非零退出會直接終結整個 sync，違反 best-effort。
- 沿用 `nice -n 19 ionice -c3` 不搶線上 web 服務；沿用 `$UV`。
- `claude`(Sonnet) 已在 PATH（標籤步驟同進程已用 claude CLI，service 也設了 `SYNC_PATH_EXTRA`）。

### 3. `deploy/systemd/report-mark-sync.env.example`（可選並發 env）

加註解說明可選 `SYNC_SUMMARY_WORKERS`（預設不設＝沿用腳本預設 6 並發），
讓伺服器依 Sonnet 額度/CPU 調整摘要並發數。

## 資料流

```
timer(每3h) → sync_new_reports.sh
  ├ mount 檢查 → rsync(delta)
  ├ 增量匯入 sync_new_reports.py  ── 寫 data/.sync_last_ingested = N
  └ if N>0: generate_summaries.py(補 summary IS NULL, best-effort) ; else 跳過
```

## 錯誤處理

- 摘要步驟非零退出：記 log「best-effort，已略過」，sync 仍視為成功（exit 0）。
- 個別摘要失敗：`generate_summaries.py` 既有機制記 `data/summary_failures.log`，列仍為 NULL，下一輪有新匯入時一併重試。
- 標記檔缺失/讀取失敗：`cat ... || echo 0` 退為 0 → 不跑摘要（安全預設）。

## 測試

- **單元（零工具鏈 unittest）**：新增 `tests/test_sync_summary_marker.py` 驗
  `write_ingested_marker` 正確寫入整數、覆寫舊值、可被 `int()` 讀回。
- **手動**：`make sync-once` 跑一輪，log 出現「生成摘要」段，且 DB `summary IS NULL`
  數量在本輪後下降；無新檔時 log 出現「跳過摘要」。

## 取捨

- gating 用精準的 `ingested>0`（而非 rsync 的 `NEW>0`）：新檔可能整批被 admin/scanned/exists/non-research 濾掉，`NEW>0` 會誤觸發。
- 「跑時補全部 NULL」讓 best-effort 失敗的殘檔在下一次有新匯入的循環被一併重試，達成最終一致。
- 不在 `sync_new_reports.py` 逐檔即時生成摘要：避免每篇多一次 Sonnet 呼叫拖慢匯入，也讓 best-effort 失敗語意單純（一個獨立子程序）。
