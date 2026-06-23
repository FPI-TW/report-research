# 定時匯入後自動生成摘要 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 讓定時 NAS 增量同步在「本次有新研報入庫」時，自動 best-effort 補上摘要（`summary IS NULL`），失敗不擋 sync。

**Architecture:** `sync_new_reports.py` 在非 dry-run 結尾把本輪 ingested 篇數寫入標記檔 `data/.sync_last_ingested`；`sync_new_reports.sh` 在匯入成功後讀此檔，`ingested>0` 才呼叫既有 `generate_summaries.py`，用 `|| log` 包成 best-effort。`generate_summaries.py` 與標籤流程、timer/service 皆不動。

**Tech Stack:** Python 3.13（async，asyncio）、bash（`set -euo pipefail`）、systemd oneshot、零工具鏈 `unittest`、`uv`。

## Global Constraints

- 設計文件：`docs/superpowers/specs/2026-06-23-sync-auto-summary-design.md`（所有需求以此為準）。
- 測試＝零工具鏈 `unittest`（專案已移除 pytest）；純函式測試不碰 DB。
- 標記檔常數路徑：`data/.sync_last_ingested`（相對 ROOT；shell cwd 也是 ROOT）。
- shell 受 `set -euo pipefail`：摘要步驟**必須** `|| log ...` 收尾，否則非零退出會終結整個 sync。
- 沿用既有慣例：原子寫檔（tmp + rename，比照 `_persist_tag`）、`nice -n 19 ionice -c3`、`$UV`。
- 提交訊息用 Conventional Commits + 繁中（比照 `fix(sync): ...`、`feat(sync): ...`）。

---

### Task 1: `sync_new_reports.py` 露出本輪 ingested 篇數

**Files:**
- Modify: `scripts/sync_new_reports.py`（加常數 `INGESTED_MARKER`、純函式 `write_ingested_marker`、在 `_run()` 非 dry-run 結尾呼叫）
- Test: `tests/test_sync_summary_marker.py`（新增）

**Interfaces:**
- Produces: `write_ingested_marker(path: Path, n: int) -> None` — 把 `int(n)` 原子寫入 `path`（覆寫、自動建父目錄）。常數 `INGESTED_MARKER: Path = ROOT / "data" / ".sync_last_ingested"`。
- Consumes: 無（純函式，不引入重型相依）。

- [ ] **Step 1: 寫失敗測試**

新增 `tests/test_sync_summary_marker.py`：

```python
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import sync_new_reports as snr  # noqa: E402


class WriteIngestedMarkerTests(unittest.TestCase):
    def test_writes_integer_as_text(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ".sync_last_ingested"
            snr.write_ingested_marker(p, 5)
            self.assertEqual(p.read_text(encoding="utf-8"), "5")
            self.assertEqual(int(p.read_text(encoding="utf-8")), 5)

    def test_overwrites_previous_value(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ".sync_last_ingested"
            snr.write_ingested_marker(p, 3)
            snr.write_ingested_marker(p, 0)
            self.assertEqual(p.read_text(encoding="utf-8"), "0")

    def test_creates_parent_dirs(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a" / "b" / ".sync_last_ingested"
            snr.write_ingested_marker(p, 7)
            self.assertTrue(p.exists())
            self.assertEqual(p.read_text(encoding="utf-8"), "7")

    def test_marker_constant_under_data(self):
        self.assertEqual(snr.INGESTED_MARKER.name, ".sync_last_ingested")
        self.assertEqual(snr.INGESTED_MARKER.parent.name, "data")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run python -m unittest tests.test_sync_summary_marker -v`
Expected: FAIL — `AttributeError: module 'sync_new_reports' has no attribute 'write_ingested_marker'`（及 `INGESTED_MARKER`）。

- [ ] **Step 3: 實作常數 + 純函式**

在 `scripts/sync_new_reports.py` 頂部既有路徑常數區（`FAIL_LOG = ROOT / "data" / "sync_failures.log"` 之後）加：

```python
INGESTED_MARKER = ROOT / "data" / ".sync_last_ingested"
```

在 `_append_all_jsonl` 之後（module 級函式區）加：

```python
def write_ingested_marker(path: Path, n: int) -> None:
    """把本輪 ingested 篇數原子寫入標記檔，供殼層 gate 摘要步驟（每輪覆寫）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / (path.name + ".tmp")
    tmp.write_text(str(int(n)), encoding="utf-8")
    tmp.rename(path)
```

- [ ] **Step 4: 跑測試確認通過**

Run: `uv run python -m unittest tests.test_sync_summary_marker -v`
Expected: PASS（4 測試）。

- [ ] **Step 5: 在 `_run()` 非 dry-run 結尾寫入標記**

`scripts/sync_new_reports.py` 的 `_run()` 中，把現有的 summary 列印區塊：

```python
    print("\n=== sync summary ===", flush=True)
```

改成在它之前先寫標記：

```python
    if not args.dry_run:
        write_ingested_marker(INGESTED_MARKER, stats["ingested"])

    print("\n=== sync summary ===", flush=True)
```

- [ ] **Step 6: 回歸既有 sync 測試 + 格式檢查**

Run: `uv run python -m unittest tests.test_sync_new_reports tests.test_sync_summary_marker -v`
Expected: 全數 PASS。
Run: `uv run black scripts/sync_new_reports.py tests/test_sync_summary_marker.py && uv run ruff check scripts/sync_new_reports.py tests/test_sync_summary_marker.py`
Expected: 無錯（若 black/ruff 執行檔缺失，記下並略過，不擋提交）。

- [ ] **Step 7: 提交**

```bash
git add scripts/sync_new_reports.py tests/test_sync_summary_marker.py
git commit -m "$(cat <<'EOF'
feat(sync): 匯入結尾寫入 ingested 標記檔供摘要 gate

sync_new_reports.py 非 dry-run 結尾把本輪 ingested 篇數原子寫入
data/.sync_last_ingested，讓殼層能精準判斷「本次有新研報入庫」
而決定是否觸發摘要生成。附純函式 unittest。
EOF
)"
```

---

### Task 2: `sync_new_reports.sh` 接摘要步驟 + env 範例

**Files:**
- Modify: `scripts/sync_new_reports.sh`（匯入成功後插入第 4 步）
- Modify: `deploy/systemd/report-mark-sync.env.example`（加可選 `SYNC_SUMMARY_WORKERS`）

**Interfaces:**
- Consumes: Task 1 的標記檔 `data/.sync_last_ingested`（讀整數）、既有 `scripts/generate_summaries.py`（`--workers` 選項）。
- Produces: 無（終端部署行為）。

- [ ] **Step 1: 插入摘要步驟**

在 `scripts/sync_new_reports.sh` 中，定位這段：

```bash
if [ "$IMPORT_RC" -ne 0 ]; then log "匯入失敗（保留 delta 供排查）→ 結束"; exit 1; fi

rm -f "$DELTA"
log "=== sync done ==="
```

改成在 `rm -f "$DELTA"` 之前插入第 4 步：

```bash
if [ "$IMPORT_RC" -ne 0 ]; then log "匯入失敗（保留 delta 供排查）→ 結束"; exit 1; fi

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

rm -f "$DELTA"
log "=== sync done ==="
```

- [ ] **Step 2: 語法檢查**

Run: `bash -n scripts/sync_new_reports.sh`
Expected: 無輸出（語法正確）。

說明：`set -u` 下 `${SYNC_SUMMARY_WORKERS:+...}` 對未設變數是安全的（`:+` 形式不觸發 unbound）。摘要步驟用 `|| log ...`，在 `set -e` 下不會因非零退出而中止 sync。

- [ ] **Step 3: 加 env 範例**

在 `deploy/systemd/report-mark-sync.env.example` 末尾追加：

```bash

# 摘要生成並發數（可選；不設＝沿用 generate_summaries.py 預設 6）。
# Sonnet 額度緊或 CPU 吃緊時可調小，例如：
# SYNC_SUMMARY_WORKERS=4
```

- [ ] **Step 4: 提交**

```bash
git add scripts/sync_new_reports.sh deploy/systemd/report-mark-sync.env.example
git commit -m "$(cat <<'EOF'
feat(sync): 定時匯入後 best-effort 自動生成摘要

sync_new_reports.sh 在匯入成功且本次 ingested>0 時呼叫
generate_summaries.py 補 summary IS NULL；以 || log 包成 best-effort，
摘要失敗只記 log 不擋 sync。新增可選 SYNC_SUMMARY_WORKERS 調並發。
EOF
)"
```

---

## 部署驗證（實作後、合併前，於部署機手動）

不在自動測試範圍，記錄供執行者交付前手動確認：

1. 重新安裝腳本（若有安裝步驟）後，`make sync-once` 跑一輪。
2. 觀察 `data/sync_run_*.log`：有新檔時出現「本次新增 N 篇 → 生成摘要」段；無新檔時出現「本次無新研報入庫 → 跳過摘要」。
3. 確認 DB `SELECT count(*) FROM research.research_report WHERE summary IS NULL` 在本輪後下降。
4. 確認摘要步驟若失敗，sync 仍 `=== sync done ===`（exit 0）、systemd 不標 failed。

---

## Self-Review

**Spec coverage：**
- 露出 ingested 數（spec §設計1）→ Task 1。
- shell 接摘要步驟、best-effort、`|| log`、gating（spec §設計2、§決策）→ Task 2 Step 1。
- env 可選並發（spec §設計3）→ Task 2 Step 3。
- 不動 `generate_summaries.py`/標籤/timer（spec §範圍）→ 計畫未觸碰，符合。
- 單元測試（spec §測試）→ Task 1 Step 1；手動驗證（spec §測試）→ 部署驗證節。

**Placeholder scan：** 無 TBD/TODO；所有 code step 皆給完整程式碼與指令。

**Type consistency：** `write_ingested_marker(path: Path, n: int)` 與 `INGESTED_MARKER` 在 Task 1 定義、Task 2 以檔案路徑（`data/.sync_last_ingested`）間接消費，名稱一致。

---

## 修訂（2026-06-23，PR #25 合併前）

依使用者要求把「處理範圍」由**補全表 `summary IS NULL`**改為**只補本輪新匯入的 file_hash**（控制定時流程 token 消耗；歷史積壓改手動 `make summaries` 補）。落地差異（以最終 spec 為準）：

- `sync_new_reports.py`：`INGESTED_MARKER`(整數計數) → `INGESTED_HASHES_FILE`(`data/.sync_last_hashes`，每行一個 file_hash)；`write_ingested_marker(path,n)` → `write_ingested_hashes(path, hashes)`（空清單寫 0-byte 檔）；`_run()` 收集 `ingested_hashes` 並寫入。
- `generate_summaries.py`：新增純函式 `read_hashes_file(path)` 與 `fetch_candidates(limit, hashes=None)` 的 `AND file_hash = ANY(:hashes)` 範圍化；新增 CLI `--hashes-file`（不給＝原行為，補全表 NULL）。
- `sync_new_reports.sh`：gate 改 `[ -s data/.sync_last_hashes ]`，呼叫帶 `--hashes-file`。
- 測試：`tests/test_sync_summary_marker.py` → `tests/test_sync_ingested_hashes.py`（驗 `write_ingested_hashes`）；`tests/test_summary.py` 加 `read_hashes_file` 測試。
- 整合煙霧：`--hashes-file <bogus>` 對 live DB 走 `ANY(:hashes)` 回 0 候選、不呼叫 Sonnet。
