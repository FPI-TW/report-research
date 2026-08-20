#!/usr/bin/env bash
# 續傳全量語料：並行（補完標註 tag_all_cli + 導入既有 backlog ingest_all）
# → 兩者都結束後，收尾再導入一次，抓標註期間新產生的 tag。
# 設計為 setsid/nohup 背景長跑，脫離互動 session 也能跑完。
set -uo pipefail
# repo 根由腳本自身位置推導，與 sync_new_reports.sh 同一個慣用語。原本這裡寫死
# 絕對路徑，2026-08-18 從 /mnt/c 遷到 ext4 時就是壞的——而它「設計為 setsid/nohup
# 背景長跑」，失敗時沒有人在看終端機。
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
cd "$ROOT"

DATE=20260612
TAG_LOG="data/tag_run_${DATE}.log"
ING_LOG="data/ingest_run_${DATE}.log"
ORCH_LOG="data/resume_orchestrator_${DATE}.log"
LOCK="data/.resume_corpus.lock"

log() { echo "[$(date '+%F %T')] $*" >> "$ORCH_LOG"; }

# 防重入
if [ -e "$LOCK" ] && kill -0 "$(cat "$LOCK" 2>/dev/null)" 2>/dev/null; then
  log "已有 resume_corpus 在跑（lock=$(cat "$LOCK")），本次不重複啟動"
  exit 0
fi
echo $$ > "$LOCK"
trap 'rm -f "$LOCK"' EXIT

log "=== resume start (pid=$$) ==="

# 1) 並行啟動：標註補完 + 導入 backlog
uv run python scripts/tag_all_cli.py --workers 8 >> "$TAG_LOG" 2>&1 &
TAG_PID=$!
log "標註啟動 tag_all_cli --workers 8 (pid=$TAG_PID) → $TAG_LOG"

uv run python scripts/ingest_all.py >> "$ING_LOG" 2>&1 &
ING_PID=$!
log "導入啟動 ingest_all backlog (pid=$ING_PID) → $ING_LOG"

# 2) 等第一輪導入完（啃完目前已標未導的 ~12k）
wait "$ING_PID"; log "第一輪導入結束 (rc=$?)"

# 3) 等標註全部完
wait "$TAG_PID"; log "標註結束 (rc=$?)"

# 4) 收尾：標註期間新產生的 tag 再導入一次
log "收尾導入（catch newly-tagged）"
uv run python scripts/ingest_all.py >> "$ING_LOG" 2>&1
log "收尾導入結束 (rc=$?)"

log "=== resume done ==="
