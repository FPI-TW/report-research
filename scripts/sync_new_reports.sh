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
UNIT_FAILURES="data/unit_failures.log"

# scripts/_claude_lock.py 的 EXIT_LOCK_BUSY（sysexits.h EX_TEMPFAIL）。三個階段都會
# spawn claude CLI，撞到手動批次時會以這個碼結束——刻意與「這支自己壞了」分開，
# 因為處置完全不同（前者下一輪自然重試，後者要人去看）。
LOCK_BUSY_RC=75

log() { echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }

# 把失敗留在 OnFailure 告警既有的落點（deploy/systemd/report-mark-alert.sh 寫的也是
# 這個檔）。理由：下面摘要與摘錄兩段是 best-effort，失敗不會讓 unit 變紅，於是
# `|| log "...已略過"` 等於只寫進當日 sync log，而那個檔沒有任何程式消費端——
# 2026-07-28 連續 10 輪匯入失敗就是這樣 24 小時沒人察覺。單一位置可查才有意義。
record_unit_failure() {
  local stage="$1" rc="$2"
  {
    echo "=== $(date -Iseconds)  UNIT=report-mark-sync.service  STAGE=${stage}  RC=${rc} ==="
    if [ "$rc" -eq "$LOCK_BUSY_RC" ]; then
      echo "（rc=${LOCK_BUSY_RC}＝claude CLI 被另一支批次佔用，見 scripts/_claude_lock.py；"
      echo "  不是這支批次壞掉。持有者資訊印在下方 log 尾巴裡。）"
    fi
    echo "--- ${LOG} (last 20) ---"
    tail -n 20 "$LOG" 2>/dev/null || echo "(sync log 讀取失敗)"
    echo
  } >> "$UNIT_FAILURES" 2>/dev/null || true
}

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
if [ "$IMPORT_RC" -ne 0 ]; then
  record_unit_failure "sync_new_reports(import)" "$IMPORT_RC"
  if [ "$IMPORT_RC" -eq "$LOCK_BUSY_RC" ]; then
    # 復原提示不可省：rsync 已把新檔落到本地，下一輪 rsync 不會再把它們列進 delta
    # （--size-only 判定為已同步），所以「等下一輪自然補上」是錯的直覺——delta 只有
    # 這一次。等手動批次結束後要用 --all-local 對 DB 補漏。
    log "匯入未執行：claude CLI 被另一支批次佔用（rc=$LOCK_BUSY_RC）"
    log "  → 本輪新檔已在本地但未入庫；等該批次結束後跑："
    log "     $UV run python scripts/sync_new_reports.py --all-local"
  fi
  log "匯入失敗（保留 delta 供排查）→ 結束"
  exit 1
fi

# 4) 本次有新研報入庫才補摘要（best-effort：失敗只記 log，不擋 sync）
#    僅針對本輪新匯入的 file_hash（--hashes-file），不掃歷史 NULL 積壓；
#    歷史積壓另以 `make summaries`（不帶 --hashes-file，補全表 NULL）手動補。
HASHES=data/.sync_last_hashes
if [ -s "$HASHES" ]; then
  N=$(grep -c . "$HASHES" 2>/dev/null || echo 0)
  log "本次新增 ${N} 篇 → 生成摘要（Sonnet，僅本輪新研報）"
  SUMMARY_RC=0
  nice -n 19 ionice -c3 "$UV" run python scripts/generate_summaries.py \
    --hashes-file "$HASHES" \
    ${SYNC_SUMMARY_WORKERS:+--workers "$SYNC_SUMMARY_WORKERS"} >>"$LOG" 2>&1 \
    || SUMMARY_RC=$?
  if [ "$SUMMARY_RC" -ne 0 ]; then
    log "摘要生成非零退出 rc=${SUMMARY_RC}（best-effort，已略過）"
    record_unit_failure "generate_summaries" "$SUMMARY_RC"
  fi

  # 4b) 顯示標題（best-effort，只針對本輪新研報）
  #     同樣**必須序列跑**：與摘要/摘錄共搶同一支 claude CLI（另有 _claude_lock.py
  #     的 flock 兜底）。沒補到標題不是錯誤——前端會回退檔名，只是讀者看到流水號。
  log "本次新增 ${N} 篇 → 產生顯示標題（僅本輪新研報）"
  TITLE_RC=0
  nice -n 19 ionice -c3 "$UV" run python scripts/generate_titles.py \
    --hashes-file "$HASHES" \
    ${SYNC_TITLE_WORKERS:+--workers "$SYNC_TITLE_WORKERS"} >>"$LOG" 2>&1 \
    || TITLE_RC=$?
  if [ "$TITLE_RC" -ne 0 ]; then
    log "標題生成非零退出 rc=${TITLE_RC}（best-effort，已略過）"
    record_unit_failure "generate_titles" "$TITLE_RC"
  fi

  # 5) 閱讀頁重點摘錄（best-effort，同樣只針對本輪新研報）
  #    **必須序列跑在摘要之後**：兩者都 spawn claude CLI，併發會互搶，擷取會被
  #    大量誤標 rejected（不是資料壞、也不是模型壞，是 CLI 被搶）。這條順序現在
  #    另有 scripts/_claude_lock.py 的跨進程 flock 兜底——但鎖只保證「不會同時
  #    跑」，撞上就是有一邊不跑；要兩段都完成，順序仍然得靠這裡寫對。
  #
  #    **一定要用 --hashes-file，不可用 --since-days 1**：後者濾的是 report_date
  #    而非入庫時間，而 NAS 匯入的研報日期常比入庫日早——實測近 10 天入庫的 90 篇
  #    裡有 79 篇（88%）report_date 超過一天前，用天數會靜默漏掉近九成。
  log "本次新增 ${N} 篇 → 擷取重點摘錄（僅本輪新研報）"
  TAKEAWAY_RC=0
  nice -n 19 ionice -c3 "$UV" run python scripts/extract_takeaways.py \
    --hashes-file "$HASHES" \
    ${SYNC_TAKEAWAY_WORKERS:+--workers "$SYNC_TAKEAWAY_WORKERS"} >>"$LOG" 2>&1 \
    || TAKEAWAY_RC=$?
  if [ "$TAKEAWAY_RC" -ne 0 ]; then
    log "摘錄擷取非零退出 rc=${TAKEAWAY_RC}（best-effort，已略過）"
    record_unit_failure "extract_takeaways" "$TAKEAWAY_RC"
  fi
else
  log "本次無新研報入庫 → 跳過摘要、標題與摘錄"
fi

# 6) 觀點雷達訊號（best-effort）。**刻意在 $HASHES 判斷之外**：它與上面三段不同，
#    不是「補本輪新研報的缺值」，而是在排一份跨全語料的積壓（2026-07-30 實測待擷取
#    5047 份）。綁本輪新檔的話，沒有新研報進來的日子它就完全不動——而雷達正是這樣
#    從 2026-07-16 起靜止了兩週。
#
#    **`--limit` 不可省，這是本段最重要的一行**：訊號擷取每份約 100-135s，5047 份
#    不設上限就是連續佔住 claude CLI 鎖八十小時以上，期間每一輪 sync 的匯入都會撞鎖
#    以 rc=75 收場——而匯入撞鎖的代價不是「下輪再來」：rsync 已把檔案落到本地，
#    `--size-only` 讓下一輪 delta 不再列出它們，那批研報就要靠 `--all-local` 手動補。
#    也就是說，讓這段跑太久會反過來把主資料流弄停。
#
#    擷取順序是 `report_date DESC`（見 extract_signals.py 的 _REPORTS_SQL），所以
#    限量取的一定是最新的那幾份：雷達保持在最新狀態，歷史積壓在背景慢慢排。
SIGNAL_LIMIT=${SYNC_SIGNAL_LIMIT:-15}
log "擷取觀點雷達訊號（每輪最多 ${SIGNAL_LIMIT} 份，新→舊；冪等，無新工作即 no-op）"
SIGNAL_RC=0
nice -n 19 ionice -c3 "$UV" run python scripts/extract_signals.py \
  --limit "$SIGNAL_LIMIT" \
  ${SYNC_SIGNAL_WORKERS:+--workers "$SYNC_SIGNAL_WORKERS"} >>"$LOG" 2>&1 \
  || SIGNAL_RC=$?
if [ "$SIGNAL_RC" -ne 0 ]; then
  log "訊號擷取非零退出 rc=${SIGNAL_RC}（best-effort，已略過）"
  record_unit_failure "extract_signals" "$SIGNAL_RC"
fi

rm -f "$DELTA"
log "=== sync done ==="
