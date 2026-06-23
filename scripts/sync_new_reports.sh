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
rsync -rt --size-only --no-motd --out-format='%n' "$SRC" "$DST" >"$DELTA" 2>>"$LOG"
RC=$?
NEW=$(grep -cvE '/$' "$DELTA" 2>/dev/null || echo 0)
log "rsync rc=$RC，本次新傳檔列≈${NEW}"
if [ "$RC" -ne 0 ]; then log "rsync 失敗 → 結束"; exit 1; fi

# 3) 增量匯入（nice/ionice 降優先序，勿搶線上服務）
log "增量匯入 delta…"
nice -n 19 ionice -c3 "$UV" run python scripts/sync_new_reports.py --delta "$DELTA" >>"$LOG" 2>&1
IMPORT_RC=$?
log "匯入結束 rc=$IMPORT_RC"
if [ "$IMPORT_RC" -ne 0 ]; then log "匯入失敗（保留 delta 供排查）→ 結束"; exit 1; fi

rm -f "$DELTA"
log "=== sync done ==="
