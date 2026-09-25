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
# 本輪時間戳。delta 與（下游中止時）保留的 hashes 共用同一個，讓人一眼對得上是哪一輪。
ROUND_TS=$(date +%Y%m%d_%H%M%S)
DELTA="data/sync_delta_${ROUND_TS}.txt"
# 本輪成功入庫的 file_hash（importer 每輪覆寫），驅動下游摘要、標題、摘錄。
HASHES=data/.sync_last_hashes
# importer 中途整批中止時，「已 commit 的那幾篇」的 hashes（正常結束不產生）。
HASHES_PARTIAL="${HASHES}.partial"
LOCK="data/.sync_new_reports.lock"
UNIT_FAILURES="data/unit_failures.log"
# 本輪狀態。格式刻意是 key=value 而不是 JSON：bash 這側用 sed 逐鍵取（對機器寫出的檔
# 下 source/eval 是安靜的地雷），欄位全是純量，JSON 換不到東西。與 data/.last_successful_sync
# 分工——那個記「最近一次完整成功」，這個記「這一輪的收場」。
ROUND_STATE="data/sync_round_state"

# scripts/_claude_lock.py 的 EXIT_LOCK_BUSY（sysexits.h EX_TEMPFAIL）。三個階段都會
# spawn claude CLI，撞到手動批次時會以這個碼結束——刻意與「這支自己壞了」分開，
# 因為處置完全不同（前者下一輪自然重試，後者要人去看）。
LOCK_BUSY_RC=75

# 帳號／環境型中止（批次以 SystemExit(2) 收場，例如 claude CLI 不在 PATH）。與 rc=1 分開：
# 這類中止是**整批一篇都沒做**，而且不寫 data/sync_failures.log、也不進
# research.llm_task_failure，所以 failures_to_delta.py 撈不到、下一輪也不會自己補。
# 處置見 docs/production_resilience.md「整批中止後的重放」。
# 注意 argparse 參數錯誤同樣是 rc=2（例如殼傳了批次不認得的旗標），提示裡要叫人先看 log。
ENV_ABORT_RC=2

log() { echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }

# 紀錄標頭的唯一產生點。格式是 web/routers/monitor.py `_UNIT_FAIL_RE` 的契約
# （UNIT／STAGE／RC 三段，其中 RC 是 optional），現在有兩個寫入端（下面的
# record_unit_failure 與 report_aborted_round），寫散在兩處遲早會漂掉。
# **rc 允許是空字串＝不明，那時整段 `RC=` 省略**（前端 progressSchema.ts 的 rc 也是
# nullable）——被 SIGKILL 收掉的那一輪本來就沒有退出碼，猜一個數字填進去只會讓讀的
# 人以為那是真的。
failure_header() {
  local stage="$1" rc="${2:-}"
  if [ -n "$rc" ]; then
    echo "=== $(date -Iseconds)  UNIT=report-mark-sync.service  STAGE=${stage}  RC=${rc} ==="
  else
    echo "=== $(date -Iseconds)  UNIT=report-mark-sync.service  STAGE=${stage} ==="
  fi
}

# 把失敗留在 OnFailure 告警既有的落點（deploy/systemd/report-mark-alert.sh 寫的也是
# 這個檔）。理由：下面摘要與摘錄兩段是 best-effort，失敗不會讓 unit 變紅，於是
# `|| log "...已略過"` 等於只寫進當日 sync log，而那個檔沒有任何程式消費端——
# 2026-07-28 連續 10 輪匯入失敗就是這樣 24 小時沒人察覺。單一位置可查才有意義。
# 下游段的異常計數。掛在 record_unit_failure 內是刻意的：每一個下游失敗分支都已經
# 呼叫它，所以這裡一處就涵蓋全部六段**以及未來新增的段**——不必記得在新段裡多寫一行，
# 而「忘記多寫一行」正是這類計數器最典型的失效方式。
# **rc=75（claude CLI 被佔用）不算異常**：它是 EX_TEMPFAIL，本 repo 刻意用它與
# 「批次自己壞了」分流；把它算成異常會讓每次批次撞鎖都抑制心跳。
#
# 保留 hashes 也掛在這裡，理由相同：任一下游段以 rc=2 中止都要保留，新段自動涵蓋。
# 必須在寫紀錄之前呼叫，保留結果與補跑指令才會落在下面那段 log 尾巴裡。
DOWNSTREAM_ABNORMAL=0
record_unit_failure() {
  local stage="$1" rc="$2"
  if [ "$rc" -ne "$LOCK_BUSY_RC" ]; then
    DOWNSTREAM_ABNORMAL=$((DOWNSTREAM_ABNORMAL + 1))
  fi
  if [ "$rc" -eq "$ENV_ABORT_RC" ]; then
    retain_hashes_for_replay "$stage"
  fi
  {
    failure_header "$stage" "$rc"
    if [ "$rc" -eq "$LOCK_BUSY_RC" ]; then
      echo "（rc=${LOCK_BUSY_RC}＝claude CLI 被另一支批次佔用，見 scripts/_claude_lock.py；"
      echo "  不是這支批次壞掉。持有者資訊印在下方 log 尾巴裡。）"
    fi
    echo "--- ${LOG} (last 20) ---"
    tail -n 20 "$LOG" 2>/dev/null || echo "(sync log 讀取失敗)"
    echo
  } >> "$UNIT_FAILURES" 2>/dev/null || true
}

# ── 整批中止後要重放的東西 ─────────────────────────────────────────────
# **delta 與 hashes 都是「只有這一輪有」的輸入**：rsync --size-only 讓下一輪 delta 不再
# 列出已落地的檔，而 data/.sync_last_hashes 下一輪就被 importer 覆寫。帳號／環境型中止
# （rc=2）不留逐篇失敗紀錄，這兩份檔就是事後重放的唯一依據，所以要保留、要印出來。

# 匯入段是否已完成。之前的 rc=2（匯入本身）不保留 hashes：那時的 .sync_last_hashes 是
# 上一輪或半途的內容，要重放的是 delta，不是它。
IMPORT_DONE=0
HASHES_RETAINED=""

# 把本輪 hashes 複製成 data/sync_hashes_retained_<本輪時間>.txt，並印出補跑指令。
# 一輪只複製一次（第二段 rc=2 時只重提位置）。三段補跑都冪等（只挑 IS NULL），
# 所以即使中止發生在摘錄之後（例如訊號、簡報那幾段），照指令重跑也只是 no-op。
retain_hashes_for_replay() {
  local stage="$1" dst="data/sync_hashes_retained_${ROUND_TS}.txt"
  if [ "$IMPORT_DONE" -ne 1 ]; then
    return 0
  fi
  if [ -n "$HASHES_RETAINED" ]; then
    log "  ${stage} 也以 rc=${ENV_ABORT_RC} 中止；本輪 hashes 已保留於 ${HASHES_RETAINED}"
    return 0
  fi
  if [ ! -s "$HASHES" ]; then
    log "  ${stage} 以 rc=${ENV_ABORT_RC} 中止；本輪無新研報入庫，沒有 hashes 需要保留"
    return 0
  fi
  if ! cp -f "$HASHES" "$dst" 2>/dev/null; then
    log "  ${stage} 以 rc=${ENV_ABORT_RC} 中止，但保留 hashes 失敗（${HASHES} → ${dst}）；"
    log "  下一輪匯入前手動複製 ${HASHES}，否則本輪新研報的下游要靠全表補"
    return 0
  fi
  HASHES_RETAINED="$dst"
  log "  ${stage} 以 rc=${ENV_ABORT_RC} 中止（帳號／環境型）→ 本輪 hashes 已保留：${dst}"
  log "  （rc=${ENV_ABORT_RC} 也可能是參數錯誤，先看本輪 log 確認中止原因）"
  print_downstream_replay "$dst"
}

# 印出「用這份 hashes 補跑摘要、標題、摘錄」的三條指令。
# 以迴圈組指令而不逐行寫死：本檔的靜態測試以「run python scripts/<名>.py」數呼叫點，
# 提示文字若逐字寫出會被數成多一個呼叫。
print_downstream_replay() {
  local dst="$1" s
  log "  → 排除中止原因後依序補跑（見 docs/production_resilience.md「整批中止後的重放」）："
  for s in generate_summaries generate_titles extract_takeaways; do
    log "     $UV run python scripts/${s}.py --hashes-file ${dst}"
  done
}

# 匯入段中途中止時，importer 把「已 commit 的篇」寫成 $HASHES_PARTIAL（正常結束不寫）。
# 那幾篇已在庫，重放 delta 時會變 skip_exists、不會出現在重放的 hashes 裡——這份就是
# 它們跑下游的唯一依據，所以改名保留並印出補跑指令。
# 檔名刻意帶 `_partial`：不可與 list_retained_deltas 建議的 --hashes-out
# （sync_hashes_retained_<ts>.txt）同名，否則照指令重放本輪 delta 就會把它蓋掉。
retain_partial_import_hashes() {
  local dst="data/sync_hashes_retained_${ROUND_TS}_partial.txt"
  if [ ! -s "$HASHES_PARTIAL" ]; then
    return 0
  fi
  if ! mv -f "$HASHES_PARTIAL" "$dst" 2>/dev/null; then
    log "  匯入中止前已有研報入庫，但保留其 hashes 失敗（${HASHES_PARTIAL} → ${dst}）；"
    log "  下一輪匯入前手動改名 ${HASHES_PARTIAL}，否則那幾篇的下游要靠全表補"
    return 0
  fi
  HASHES_RETAINED="$dst"
  local n
  n=$(grep -c . "$dst" 2>/dev/null || echo 0)
  log "  匯入中止前已有 ${n} 篇入庫（重放 delta 時會變 skip_exists）→ 其 hashes 已保留：${dst}"
  print_downstream_replay "$dst"
}

# 殼自己產生的 delta 檔名：sync_delta_<YYYYMMDD>_<HHMMSS>.txt（見檔頭 DELTA）。手動做的
# delta（例如 failures_to_delta.py 的 sync_delta_recover.txt、sync_delta_rehash_<日期>.txt）
# 不是「整批中止保留下來的輪次」，混進重放清單會叫人重放不相干的檔。
round_deltas() {
  ls -1tr data/sync_delta_[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]_[0-9][0-9][0-9][0-9][0-9][0-9].txt \
    2>/dev/null || true
}

# 依時間序（舊→新，依 mtime）列出所有保留的 delta，每份配一條 --delta 重放指令。
# 每份各自寫一份 --hashes-out，重放多份時才不會互相覆寫 .sync_last_hashes。
list_retained_deltas() {
  local deltas d ts
  deltas=$(round_deltas)
  if [ -z "$deltas" ]; then
    log "  （找不到任何保留的 delta 檔）"
    return 0
  fi
  log "  保留的 delta（舊→新，逐份重放；每份重放完立刻用它的 hashes 補跑摘要、標題、摘錄）："
  while IFS= read -r d; do
    ts=$(basename "$d" .txt)
    ts=${ts#sync_delta_}
    log "     $UV run python scripts/sync_new_reports.py --delta ${d} --hashes-out data/sync_hashes_retained_${ts}.txt"
  done <<< "$deltas"
  log "  每份重放並補跑完下游後刪掉該份 delta，否則下次中止時它會再被列進來。"
  log "  **不要用 --all-local 或 failures_to_delta.py 補救**：整批中止不留逐篇失敗紀錄。"
  log "  完整步驟見 docs/production_resilience.md「整批中止後的重放」。"
}

# ══════════════════════════════════════════════════════════════════════════════
# 本輪狀態：為什麼不能只靠 OnFailure=
# ══════════════════════════════════════════════════════════════════════════════
# `OnFailure=` 在**系統關機時結構性地不會觸發**。2026-08-17 16:34 與 2026-08-18 08:50
# 兩次補跑都在 rsync 階段開始後約 25 秒被 SIGTERM 砍掉，而 journal 明說告警排不進去：
#
#   report-mark-sync.service: Failed to enqueue OnFailure= job, ignoring:
#   Transaction for report-mark-alert@report-mark-sync.service.service/start is
#   destructive (time-set.target has 'stop' job queued, but 'start' is included
#   in transaction)
#
# 也就是「機器關掉時死掉的那一輪」不進 journal ERROR、不進 data/unit_failures.log、
# webhook 也不會響——**完全無聲**，正是本 repo 一路在對付的那種故障。
#
# 對策是不靠 OnFailure：進入點寫一筆 state=running，收尾（含訊號）改寫成終態，
# 下一輪進來時若看到上一輪停在 running／signalled 就補記一筆到 unit_failures.log
# （既有落點，/api/progress 的 unit_failures 已經在讀它，零新管道，偵測延遲 ≤3 小時）。
#
# **它只覆蓋一半——「下一輪有來」的那一半。** 另一半（下一輪再也沒來）本檔看不見：
# 沒被執行時它一個字也寫不出來。那一半由下面的管線心跳與 check_batch_freshness.py
# 的 assess_pipeline（--pipeline-hours）負責，兩者刻意分開、不可互相取代。
write_round_state() {
  local state="$1" rc="${2:-}"
  # `signal=`／`rc=` 即使沒有值也照樣印出來（空值），讓寫出的鍵集合固定——
  # 讀取端就不必分辨「這個鍵不存在」與「這輪還不知道」。
  {
    echo "state=${state}"
    echo "started_at=${ROUND_STARTED_AT:-}"
    echo "started_epoch=${ROUND_STARTED_EPOCH:-}"
    echo "pid=$$"
    echo "log=${LOG}"
    echo "signal=${ROUND_STATE_SIGNAL:-}"
    echo "rc=${rc}"
    echo "updated_at=$(date -Iseconds)"
  } > "${ROUND_STATE}.tmp" 2>/dev/null && mv -f "${ROUND_STATE}.tmp" "$ROUND_STATE" 2>/dev/null || true
}

round_state_key() { sed -n "s/^$1=//p" "$ROUND_STATE" 2>/dev/null | tail -n 1; }

# 補記上一輪的異常收場。**必須在 write_round_state running 之前呼叫**，否則上一輪的
# 狀態就被本輪抹掉了。只認 running／signalled 兩種：
#   running   ＝ 連終態都沒寫成（SIGKILL、斷電、WSL 整個被收掉；trap 擋不住）
#   signalled ＝ 收到 SIGTERM 而 EXIT trap 有跑到；OnFailure 可能有、也可能被關機吃掉
# failed（乾淨的非零退出）刻意不補記——那條路 unit 會進 failed，OnFailure 正常運作。
report_aborted_round() {
  [ -f "$ROUND_STATE" ] || return 0
  local prev_state prev_started prev_pid prev_signal prev_log prev_rc orphan
  prev_state=$(round_state_key state)
  case "$prev_state" in
    running|signalled) ;;
    *) return 0 ;;
  esac
  prev_started=$(round_state_key started_at)
  prev_pid=$(round_state_key pid)
  prev_signal=$(round_state_key signal)
  prev_log=$(round_state_key log)
  prev_rc=$(round_state_key rc)
  orphan=$(round_deltas | tac | head -n 3 || true)
  {
    failure_header "sync_round(aborted)" "$prev_rc"
    echo "（本筆是**下一輪 sync 在進入點補記**的，不是 OnFailure 寫的。系統關機時"
    echo "  systemd 會拒絕把 report-mark-alert@ 排進已含 stop job 的 transaction"
    echo "  （\"Transaction ... is destructive\"），OnFailure= 於是完全無聲——"
    echo "  2026-08-17／08-18 兩次補跑就是這樣消失的。）"
    echo "上一輪：state=${prev_state}  started_at=${prev_started}  pid=${prev_pid}  signal=${prev_signal:-（無；多半是 SIGKILL／斷電／WSL 被收掉）}"
    echo "影響：rsync 可能已把部分新檔落到本地，而 --size-only 讓下一輪 delta 不再列出"
    echo "  它們 ⇒ 那批研報不會自己補回來（同型的坑見本檔 rc=${LOCK_BUSY_RC} 那段註解）。"
    echo "  復原（殘留 delta 由新到舊，最多列三個）："
    if [ -n "$orphan" ]; then
      echo "$orphan" | sed "s|^|    uv run python scripts/sync_new_reports.py --delta |"
    else
      echo "    （無殘留 delta 檔）"
    fi
    echo "    uv run python scripts/sync_new_reports.py --all-local   # delta 不可靠時用這個"
    echo "--- ${prev_log:-（未紀錄 log 路徑）} (last 20) ---"
    if [ -n "$prev_log" ] && [ -f "$prev_log" ]; then
      tail -n 20 "$prev_log" 2>/dev/null || echo "(sync log 讀取失敗)"
    else
      echo "(找不到上一輪的 log)"
    fi
    echo
  } >> "$UNIT_FAILURES" 2>/dev/null || true
  log "上一輪未正常結束（state=${prev_state}，started_at=${prev_started}）→ 已補記入 ${UNIT_FAILURES}"
}

# ── 管線心跳 ──────────────────────────────────────────────────────────────
# 存在理由：這支殼有一條 rc=0 的早退路徑（PID lock 被佔用 → exit 0），而下游六段
# 刻意 best-effort（失敗只 log 不 exit）。兩者合起來的後果是**整條管線可以連續數天
# 完全沒有成功跑完，而 systemd 全程看起來正常**——2026-08-12 的事故正是這個形狀
# （所有 claude 批次連續 4 天 100% 失敗、入庫歸零，而 unit 全綠，症狀長得像「NAS
# 沒有新檔」）。心跳把「管線最近一次完整成功」變成一個可被外部查詢的事實。
#
# **它量的是管線執行新鮮度，不是資料新鮮度。** 即使本輪 0 篇新研報，只要完整跑完
# 就會更新——那是刻意的：週末與連假沒有新稿是常態，用「有沒有新資料」當健康指標
# 會製造日曆型假警報。資料面的停更另有 check_batch_freshness.py 的四個 max(created_at)。
# ── 匯入計數（機器可讀）────────────────────────────────────────────────
# **importer 可以 rc=0 卻沒把該入庫的檔入庫。** 2026-08-20：claude CLI 自我更新到
# 缺 native artifact 的版本，7 篇新研報全部記成 skip_untagged，importer 照常 rc=0，
# 這支殼只印「本次無新研報入庫」——與「NAS 真的沒有新檔」在畫面上完全一樣。
# 那一輪之所以沒有錯誤更新心跳，純粹因為後面的每日簡報剛好 rc=1；若當時不在簡報
# 的執行時段，7 篇會靜默消失（同一形狀見 2026-08-12）。
#
# **刻意不 grep importer 那段給人看的 summary**：那是人類文案，改一個字守門就靜默
# 失效，而症狀是「心跳照常更新」——與沒有守門完全一樣。改讀 importer 原子寫出的
# key=value 檔，逐鍵解析、只收非負整數，**不 source、不 eval**（那個檔的內容源自
# 檔名，而檔名來自 NAS）。
STATS_FILE="data/.sync_last_stats"
read_stat() {
  local key="$1" k val v=""
  [ -f "$STATS_FILE" ] || return 1
  while IFS='=' read -r k val; do
    [ "$k" = "$key" ] && v="$val"
  done < "$STATS_FILE"
  case "$v" in ''|*[!0-9]*) return 1 ;; esac
  printf '%s' "$v"
}

HEARTBEAT="data/.last_successful_sync"
write_heartbeat() {
  # 原子寫入：先寫暫存 → fsync → rename。同檔案系統上 rename 是原子的，
  # 中途崩潰只會留下舊檔或新檔。**不可省 fsync**：freshness 讀到半寫檔會把
  # 「管線正常」誤判成「格式損毀」，而那兩者的處置完全不同。
  local tmp="${HEARTBEAT}.tmp.$$"
  {
    echo "ts=$(date -Iseconds)"
    echo "epoch=$(date +%s)"
    echo "new_reports=${1:-0}"
    echo "pid=$$"
  } > "$tmp" 2>/dev/null || { log "心跳暫存寫入失敗（不擋 sync）"; rm -f "$tmp" 2>/dev/null; return 0; }
  sync -f "$tmp" 2>/dev/null || true
  mv -f "$tmp" "$HEARTBEAT" 2>/dev/null || { log "心跳 rename 失敗（不擋 sync）"; rm -f "$tmp" 2>/dev/null; return 0; }
  log "心跳已更新：$HEARTBEAT"
}

mkdir -p data

# 系統正在關機就別開工。**這不是本輪狀態機制的替代品，是它的補集**：這條只擋得住
# 「觸發點恰好落在關機 transaction 裡」，而 SIGTERM 可以在開工後任何一刻到來——實測那
# 兩次就是開工 25 秒後才被砍的，前置判斷對那種情況無能為力。真正的偵測仍是本輪狀態。
# 認不出 systemd（手動執行、容器、systemd 未啟用）時一律放行：fail-open。
SYSTEM_STATE=$(systemctl is-system-running 2>/dev/null || true)
if [ "$SYSTEM_STATE" = "stopping" ]; then
  log "系統正在關機（systemctl is-system-running=stopping）→ 不開工，乾淨退出"
  exit 0
fi

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

# 本輪狀態的生命週期。**三個順序上的要求，動這段之前先讀懂**：
#   1. 整段必須在**取得 PID lock 之後**：上面那個「已有 sync 在跑就跳過」的分支刻意
#      在安裝 trap 之前 exit，否則被跳過的那次會把仍在跑的那輪的鎖與狀態一起清掉。
#   2. report_aborted_round 必須在 write_round_state running **之前**，否則上一輪的
#      狀態已被本輪覆寫，再也讀不到。
#   3. 終態一律由 EXIT trap 寫，訊號 trap 只記下是哪個訊號然後 exit——兩邊都寫會產生
#      互相矛盾的紀錄。訊號 trap 之所以不能省：沒有它，bash 在被 SIGTERM 收掉時走的
#      是預設處置，`signal=` 就永遠是空的，事後分不出「被砍」與「連 trap 都沒跑到」。
ROUND_STATE_SIGNAL=""
ROUND_STARTED_AT=$(date -Iseconds)
ROUND_STARTED_EPOCH=$(date +%s)

finish_round() {
  local rc=$?                       # 必須是第一行：下面每一個指令都會蓋掉 $?
  rm -f "$LOCK"
  if [ -n "$ROUND_STATE_SIGNAL" ]; then
    write_round_state signalled "$rc"
  elif [ "$rc" -eq 0 ]; then
    write_round_state done "$rc"
  else
    write_round_state failed "$rc"
  fi
}

on_round_signal() {                  # $1=訊號名 $2=對應退出碼（128+n）
  ROUND_STATE_SIGNAL="$1"
  log "收到 SIG$1 → 中止本輪（終態寫入 ${ROUND_STATE}，下一輪會補記告警）"
  exit "$2"
}

report_aborted_round
write_round_state running
trap finish_round EXIT
trap 'on_round_signal TERM 143' TERM
trap 'on_round_signal INT 130' INT
trap 'on_round_signal HUP 129' HUP

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
# 先刪：importer 中途死掉時舊檔會留著，讀到上一輪的 abnormal=0 就等於守門不存在。
rm -f "$STATS_FILE"
# .partial 同理：它只該是「這一輪」中止留下的，殘檔會被誤認成本輪已入庫的篇。
rm -f "$HASHES_PARTIAL"
IMPORT_RC=0
nice -n 19 ionice -c3 "$UV" run python scripts/sync_new_reports.py --delta "$DELTA" >>"$LOG" 2>&1 \
  || IMPORT_RC=$?
log "匯入結束 rc=$IMPORT_RC"
if [ "$IMPORT_RC" -ne 0 ]; then
  # 提示寫在 record_unit_failure 之前：那段紀錄帶的是 log 尾巴，順序反了提示就不在裡面。
  if [ "$IMPORT_RC" -eq "$LOCK_BUSY_RC" ]; then
    # 復原提示不可省：rsync 已把新檔落到本地，下一輪 rsync 不會再把它們列進 delta
    # （--size-only 判定為已同步），所以「等下一輪自然補上」是錯的直覺——delta 只有
    # 這一次。等手動批次結束後要用 --all-local 對 DB 補漏。
    log "匯入未執行：claude CLI 被另一支批次佔用（rc=$LOCK_BUSY_RC）"
    log "  → 本輪新檔已在本地但未入庫；等該批次結束後跑："
    log "     $UV run python scripts/sync_new_reports.py --all-local"
    log "  補完後刪掉本輪 delta（rm -f ${DELTA}），否則之後整批中止時它會被列進待重放清單"
  else
    # 其他非零（rc=2 帳號／環境型、rc=1 未預期例外）：本輪 delta 保留在 data/，而前幾輪
    # 同樣中止的 delta 也還在——只列本輪那份會讓人漏掉前面的。
    log "匯入中止 rc=${IMPORT_RC}：本輪新檔已在本地但未入庫，delta 保留待重放"
    if [ "$IMPORT_RC" -eq "$ENV_ABORT_RC" ]; then
      log "  （rc=${ENV_ABORT_RC} 也可能是參數錯誤，先看本輪 log 確認中止原因）"
    fi
    list_retained_deltas
    retain_partial_import_hashes
  fi
  record_unit_failure "sync_new_reports(import)" "$IMPORT_RC"
  log "匯入失敗（保留 delta 供排查）→ 結束"
  exit 1
fi
IMPORT_DONE=1

# 3b) 匯入 rc=0 不等於完整成功：檢查「本該入庫卻沒進 DB」的計數
if ABNORMAL=$(read_stat abnormal); then
  if [ "$ABNORMAL" -gt 0 ]; then
    log "匯入異常：${ABNORMAL} 篇本該入庫卻沒進 DB（rc 仍為 0）→ 本輪不算完整成功"
    log "  → 逐筆路徑在 data/sync_failures.log；精準補救（不必掃全庫）："
    log "     $UV run python scripts/failures_to_delta.py --out data/sync_delta_recover.txt"
    log "     $UV run python scripts/sync_new_reports.py --delta data/sync_delta_recover.txt"
    record_unit_failure "sync_new_reports(ingest_abnormal)" 1
  fi
else
  # 缺檔或格式壞掉一律保守視為異常：這個判斷的唯一用途就是擋心跳，
  # 讀不到就當成沒問題，等於在最需要它的時候關掉它。
  log "匯入計數檔不可讀或格式異常（$STATS_FILE）→ 保守視為匯入異常"
  record_unit_failure "sync_new_reports(stats_unreadable)" 1
fi

# 4) 本次有新研報入庫才補摘要（best-effort：失敗只記 log，不擋 sync）
#    僅針對本輪新匯入的 file_hash（--hashes-file），不掃歷史 NULL 積壓；
#    歷史積壓另以 `make summaries`（不帶 --hashes-file，補全表 NULL）手動補。
#    $HASHES 定義在檔頭：下游 rc=2 時 retain_hashes_for_replay 要讀它。
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
#    **`--limit` 不可省，這是本段最重要的一行**：訊號擷取每份每 worker 約 100-135s，
#    5047 份不設上限就是連續佔住 claude CLI 鎖八十小時以上，期間每一輪 sync 的匯入都會
#    撞鎖以 rc=75 收場——而匯入撞鎖的代價不是「下輪再來」：rsync 已把檔案落到本地，
#    `--size-only` 讓下一輪 delta 不再列出它們，那批研報就要靠 `--all-local` 手動補。
#    也就是說，讓這段跑太久會反過來把主資料流弄停。
#
#    **上限值是從吞吐與視窗回推的，不是隨手填的**：2026-08-06 實測 6 份 / 167s
#    （預設 2 workers）＝ 每份約 28s 牆鐘，最壞情況以上面的 135s/份/worker 估則約
#    68s 牆鐘。100 份 ⇒ 常態約 47 分、最壞約 113 分，加上前面 rsync＋匯入的數分鐘，
#    都還在 3 小時視窗內。**要再往上調就得重量一次吞吐**——超出視窗不會壞資料
#    （systemd 不會讓同一個 service 併跑，下一輪只會被延後），但匯入會跟著延。
#
#    先前預設 15 是 2026-07-30 積壓 5047 份時的保守值；那個速率要 42 天才清得完一輪
#    積壓，而 2026-08-05 一次大批量手動擷取留下 5932 列 rejected（fail log 全是
#    「CLI 無回應或逾時」），待擷取因此回升到 2828 份。100 份/輪 ⇒ 約 3.5 天清完。
#
#    擷取順序是 `report_date DESC`（見 extract_signals.py 的 _REPORTS_SQL），所以
#    限量取的一定是最新的那幾份：雷達保持在最新狀態，歷史積壓在背景慢慢排。
SIGNAL_LIMIT=${SYNC_SIGNAL_LIMIT:-100}
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

# 7) 每日簡報（best-effort）。**沒有自己的 timer 是刻意的**：它一天只需要跑一次，
#    而本殼每 3 小時就會來一趟，由腳本自己判斷「今天要不要跑」（當日已有列、或本地
#    時間未到 --after-hour → 直接 no-op 退出 0）。多一個 systemd unit 就多一份要
#    安裝、要跟 deploy/ 對帳的東西，而它換到的只是精確的觸發時刻。
#
#    它**不在 $HASHES 判斷之內**：簡報的窗期是「上一份的結尾到現在」，與本輪有沒有
#    新研報無關——沒有新研報的那一輪照樣可能有前一輪進來的東西還沒被寫進簡報。
#
#    排在訊號之後、標題積壓之前：簡報會讀當輪剛擷取出來的評等變動（故要在訊號之後），
#    而它是這條鏈上唯一有「當日」期限的一段——標題積壓是沒有期限的長工，排在它前面
#    的話，一輪被中斷或超時就是簡報整天不出。
log "每日簡報（一天一次；當日已有或未到時間即 no-op）"
BRIEF_RC=0
nice -n 19 ionice -c3 "$UV" run python scripts/generate_brief.py >>"$LOG" 2>&1 \
  || BRIEF_RC=$?
if [ "$BRIEF_RC" -ne 0 ]; then
  log "每日簡報非零退出 rc=${BRIEF_RC}（best-effort，已略過）"
  record_unit_failure "generate_brief" "$BRIEF_RC"
fi

# 8) 顯示標題的歷史積壓（best-effort）。**與 4b 那段刻意分開，不是重複**：4b 補的是
#    「本輪新研報」的缺值（--hashes-file），這段排的是跨全語料的積壓——而兩者的差別
#    正是 4b 永遠碰不到後者：2026-08-06 實測 14,800 篇有 11,879 篇沒有 title（80%），
#    但近一年只差 1 篇。也就是說缺的全是一年以上的舊檔，它們永遠不會出現在任何一輪的
#    --hashes-file 裡，不另外排就是永遠不補，讀者在舊研報上看到的永遠是券商流水號檔名。
#
#    **同樣以 --limit 限量，理由與 6) 一字不差**：不設上限＝連續佔住 claude 鎖，把主
#    資料流弄停。generate_titles.py 的順序是 `report_date DESC NULLS LAST, file_name`，
#    故限量取的一定是最新的那批缺值。標題只餵 3000 字（訊號餵 16000），每份遠比訊號快，
#    60 份/輪 ≈ 每日 480 份；訊號積壓清完後這段就是視窗裡唯一的長工，屆時可再往上調。
#
#    排在最後：訊號的時效性較高、簡報有「當日」期限，而這段是沒有期限的長工。
#    三者搶同一支 claude CLI，先跑的那個吃掉的是後面那個的預算。
#
#    **排除本輪 4b 已打過的新研報**（--exclude-hashes-file）：積壓段依 report_date DESC 取，
#    本輪新研報恰好排最前面；4b 失敗的那篇會被這段再打一次，失敗在跳過名單
#    （research.llm_task_failure）裡一輪記兩次，「連續 3 輪才跳過」實際約 2 輪就跳。
#    它下一輪就不在 --hashes-file 裡了，自然回到積壓段，不會漏。
TITLE_BACKLOG_LIMIT=${SYNC_TITLE_BACKLOG_LIMIT:-60}
TITLE_BACKLOG_EXCLUDE=""
if [ -s "$HASHES" ]; then TITLE_BACKLOG_EXCLUDE="$HASHES"; fi
log "補顯示標題的歷史積壓（每輪最多 ${TITLE_BACKLOG_LIMIT} 份，新→舊；冪等，無缺值即 no-op）"
TITLE_BACKLOG_RC=0
nice -n 19 ionice -c3 "$UV" run python scripts/generate_titles.py \
  --limit "$TITLE_BACKLOG_LIMIT" \
  ${TITLE_BACKLOG_EXCLUDE:+--exclude-hashes-file "$TITLE_BACKLOG_EXCLUDE"} \
  ${SYNC_TITLE_WORKERS:+--workers "$SYNC_TITLE_WORKERS"} >>"$LOG" 2>&1 \
  || TITLE_BACKLOG_RC=$?
if [ "$TITLE_BACKLOG_RC" -ne 0 ]; then
  log "標題積壓補值非零退出 rc=${TITLE_BACKLOG_RC}（best-effort，已略過）"
  record_unit_failure "generate_titles_backlog" "$TITLE_BACKLOG_RC"
fi

rm -f "$DELTA"

# 心跳只在**完整成功**時更新。走到這裡代表掛載、rsync、匯入都成功（前三者失敗都
# exit 1，根本到不了這行），所以剩下要判的只有下游是否有異常失敗。
# 注意：這裡刻意**不改變** best-effort 的語意——下游失敗仍然不擋 sync、unit 仍然不變紅，
# 只是不更新心跳。持續的下游異常於是變成「管線執行新鮮度」上的可見事實，
# 而不是只躺在 unit_failures.log 裡等人去看。
NEW_INGESTED=0
if [ -s "${HASHES:-}" ]; then NEW_INGESTED=$(grep -c . "$HASHES" 2>/dev/null || echo 0); fi
if [ "$DOWNSTREAM_ABNORMAL" -eq 0 ]; then
  write_heartbeat "$NEW_INGESTED"
else
  log "本輪有 ${DOWNSTREAM_ABNORMAL} 段異常（匯入或下游）→ **不更新心跳**（rc=${LOCK_BUSY_RC} 不計）"
fi
log "=== sync done ==="
