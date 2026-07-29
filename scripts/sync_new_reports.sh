#!/usr/bin/env bash
# 排程同步殼：掛載檢查 → rsync NAS→本地（擷取 delta）→ 增量匯入。
# 設計給 systemd oneshot；nice/ionice 降優先序，PID lock 防重疊。
set -euo pipefail

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
cd "$ROOT"

MOUNT=/mnt/nas-research
SRC="$MOUNT/02.研究資源/研報自動匯入/"
DST="研報自動匯入/"
DATE=$(date +%Y%m%d)
LOG="data/sync_run_${DATE}.log"
DELTA="data/sync_delta_$(date +%Y%m%d_%H%M%S).txt"
LOCK="data/.sync_new_reports.lock"

log() { echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }
mkdir -p data

UV="${UV:-}"
if [ -z "$UV" ]; then
  UV=$(command -v uv || true)
fi
if [ -z "$UV" ]; then
  log "找不到 uv；請確認 PATH 或以環境變數 UV 指定路徑"
  exit 1
fi

# 防重入：上一輪仍在跑就跳過
if [ -e "$LOCK" ] && kill -0 "$(cat "$LOCK" 2>/dev/null)" 2>/dev/null; then
  log "已有 sync 在跑（lock=$(cat "$LOCK")），本次跳過"; exit 0
fi
echo "$$" > "$LOCK"
trap 'rm -f "$LOCK"' EXIT

log "=== sync start (pid=$$) ==="

# 1) 確保 NAS 已掛載（未掛則用 root 包裝以快取憑證 drvfs 掛載；需 NOPASSWD sudoers）
if ! mountpoint -q "$MOUNT"; then
  log "嘗試掛載 $MOUNT（drvfs，唯讀，沿用 Windows 快取憑證）"
  sudo -n /usr/local/sbin/mount-nas-research >>"$LOG" 2>&1 || true
fi
if ! mountpoint -q "$MOUNT"; then
  log "掛載失敗或不可用 → 結束（不跑 rsync、不動 DB）"; exit 1
fi

# 2) rsync 只傳新檔，擷取 delta
#    --size-only：本地已有同 NAS 舊副本，避免因 mtime 漂移整批重傳 15G；
#    研報每檔內容唯一，同名同位元組視為相同的風險可忽略。
log "rsync 同步中…（src=$SRC）"
# `|| RC=$?` 不可省：本檔開頭是 set -e，裸呼叫失敗會就地中止，下面那行 `RC=$?`
# 永遠讀到 0 而且根本執行不到——2026-07-28 起連續 10 輪匯入失敗，日誌就只停在
# 「增量匯入 delta…」，事後完全看不出敗在哪一步（見下方同型修正）。
RC=0
rsync -rt --size-only --no-motd --out-format='%n' "$SRC" "$DST" >"$DELTA" 2>>"$LOG" || RC=$?
NEW=$(grep -cvE '/$' "$DELTA" 2>/dev/null || echo 0)
log "rsync rc=$RC，本次新傳檔列≈${NEW}"
if [ "$RC" -ne 0 ]; then log "rsync 失敗 → 結束"; exit 1; fi

# 3) 增量匯入（nice/ionice 降優先序，勿搶線上服務）
log "增量匯入 delta…"
IMPORT_RC=0
nice -n 19 ionice -c3 "$UV" run python scripts/sync_new_reports.py --delta "$DELTA" >>"$LOG" 2>&1 \
  || IMPORT_RC=$?
log "匯入結束 rc=$IMPORT_RC"
if [ "$IMPORT_RC" -ne 0 ]; then log "匯入失敗（保留 delta 供排查）→ 結束"; exit 1; fi

# 4) 本次有新研報入庫才補摘要（best-effort：失敗只記 log，不擋 sync）
#    僅針對本輪新匯入的 file_hash（--hashes-file），不掃歷史 NULL 積壓；
#    歷史積壓另以 `make summaries`（不帶 --hashes-file，補全表 NULL）手動補。
HASHES=data/.sync_last_hashes
if [ -s "$HASHES" ]; then
  N=$(grep -c . "$HASHES" 2>/dev/null || echo 0)
  log "本次新增 ${N} 篇 → 生成摘要（Sonnet，僅本輪新研報）"
  nice -n 19 ionice -c3 "$UV" run python scripts/generate_summaries.py \
    --hashes-file "$HASHES" \
    ${SYNC_SUMMARY_WORKERS:+--workers "$SYNC_SUMMARY_WORKERS"} >>"$LOG" 2>&1 \
    || log "摘要生成非零退出（best-effort，已略過）"

  # 5) 閱讀頁重點摘錄（best-effort，同樣只針對本輪新研報）
  #    **必須序列跑在摘要之後**：兩者都 spawn claude CLI，併發會互搶——
  #    CLAUDE.md 記載 extract_takeaways 與 extract_signals 併發時擷取會被
  #    大量誤標 rejected（不是資料壞、也不是模型壞，是 CLI 被搶）。
  #
  #    **一定要用 --hashes-file，不可用 --since-days 1**：後者濾的是 report_date
  #    而非入庫時間，而 NAS 匯入的研報日期常比入庫日早——實測近 10 天入庫的 90 篇
  #    裡有 79 篇（88%）report_date 超過一天前，用天數會靜默漏掉近九成。
  log "本次新增 ${N} 篇 → 擷取重點摘錄（僅本輪新研報）"
  nice -n 19 ionice -c3 "$UV" run python scripts/extract_takeaways.py \
    --hashes-file "$HASHES" \
    ${SYNC_TAKEAWAY_WORKERS:+--workers "$SYNC_TAKEAWAY_WORKERS"} >>"$LOG" 2>&1 \
    || log "摘錄擷取非零退出（best-effort，已略過）"
else
  log "本次無新研報入庫 → 跳過摘要與摘錄"
fi

rm -f "$DELTA"
log "=== sync done ==="
