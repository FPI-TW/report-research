#!/usr/bin/env bash
# 研報上傳 worker 的一輪（systemd oneshot：report-mark-upload.service，timer 每 5 分鐘、Persistent=false；
# ops 的「立即執行」也是啟動這支 unit）。手動跑：`scripts/process_uploads.sh`（同樣會取整輪鎖）。
#
#   1. scan     ：隔離區 → ClamAV（零 LLM、不取 claude 鎖）
#   2. ingest   ：掃描通過的上傳入庫成草稿（取 claude 鎖；撞鎖 rc=75 時乾淨檔維持 clean，下一輪再處理）
#   3. 下游三段 ：本輪有新草稿才跑摘要、標題、摘錄（--hashes-file；各自取 claude 鎖，不巢狀）
#   4. cleanup  ：過寬限期的退回件、過保留期的感染證據、隔離區孤兒檔
#
# 整輪鎖：這裡以 flock 取 data/.upload_worker.lock（非阻塞，忙就 rc 0 退出），fd 9 經 UPLOAD_WORKER_LOCK_FD
# 交給 Python 子命令；子命令對同一個 fd 再 flock 一次證明自己在鎖底下（app/services/upload_worker.py 的
# round_lock）。所以 Python 那邊看到的 scanning／processing 一定是上一輪被殺的殘留。
# 下游三段也會繼承 fd 9（同一把鎖）：殼被殺而它們還在跑時，鎖會留到它們結束——正是要的。
#
# 退出碼（unit 的 SuccessExitStatus 見 deploy/systemd/report-mark-upload.service；分界是「會不會自己好」）：
#   0  做完了（含另一輪在跑、backfill 在跑只掃描、斷路器延後）
#   1  有一段自己壞了（OnFailure 告警）
#   2  這輪不跑、會自己好：DB 連不上（web 探針告警，這裡不重複）
#   3  LLM 設定或帳號錯誤、不會自己好：缺金鑰、環境檔讀不到、LLM_PROVIDER 拼錯、未知模型、401／402（OnFailure 告警）
#   75 入庫段撞 claude 鎖（其他 LLM 批次在跑），乾淨檔留到下一輪
# 下游三段是 best-effort：失敗不影響草稿可審（預覽會顯示「產生中」），只在 log 留補跑指令。
set -uo pipefail

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
cd "$ROOT" || exit 1

# 與 app/config.py 的 UPLOAD_WORKER_LOCK_FILE 同一個值（空＝預設）；相對路徑以 repo 根為基準（上面已 cd）。
LOCK="${UPLOAD_WORKER_LOCK_FILE:-data/.upload_worker.lock}"
HASHES=data/.upload_last_hashes
ROUND_TS=$(date +%Y%m%d_%H%M%S)
LOCK_BUSY_RC=75
ENV_ABORT_RC=2
LLM_CONFIG_RC=3

log() { echo "[$(date '+%F %T')] $*"; }

UV="${UV:-}"
if [ -z "$UV" ]; then
  UV=$(command -v uv || true)
fi
if [ -z "$UV" ]; then
  log "找不到 uv；請確認 PATH 或以環境變數 UV 指定路徑"
  exit 1
fi

mkdir -p "$(dirname -- "$LOCK")" data || exit 1
# 9>>：附加模式開檔，不截斷（正在持有鎖的那一輪可能在檔裡寫了持有者資訊）。
exec 9>>"$LOCK" || exit 1
if ! flock -n 9; then
  log "另一輪上傳 worker 正在跑，本次不啟動"
  exit 0
fi
export UPLOAD_WORKER_LOCK_FD=9

py() { nice -n 19 ionice -c3 "$UV" run python "$@"; }

# 1) 掃描
SCAN_RC=0
py scripts/process_uploads.py scan || SCAN_RC=$?
if [ "$SCAN_RC" -eq "$ENV_ABORT_RC" ]; then
  log "掃描段 rc=${ENV_ABORT_RC}（DB 不可用）→ 本輪結束"
  exit "$ENV_ABORT_RC"
elif [ "$SCAN_RC" -ne 0 ]; then
  log "掃描段非零退出 rc=${SCAN_RC}（繼續入庫與清除，本輪以 rc=1 收場）"
fi

# 2) 入庫（--hashes-out 一定會寫：空清單是 0-byte；中途中止也留下已 commit 的那幾篇）
INGEST_RC=0
py scripts/process_uploads.py ingest --hashes-out "$HASHES" || INGEST_RC=$?
case "$INGEST_RC" in
  0) ;;
  "$LOCK_BUSY_RC") log "入庫段未執行：claude 鎖被其他 LLM 批次佔用（rc=${LOCK_BUSY_RC}），乾淨檔留到下一輪" ;;
  "$ENV_ABORT_RC") log "入庫段 rc=${ENV_ABORT_RC}（DB 不可用），乾淨檔留到下一輪" ;;
  "$LLM_CONFIG_RC") log "入庫段 rc=${LLM_CONFIG_RC}：LLM 設定或帳號錯誤（不會自己好，本輪以 rc=${LLM_CONFIG_RC} 收場、告警；原因見上方輸出）" ;;
  *) log "入庫段非零退出 rc=${INGEST_RC}" ;;
esac

# 3) 下游三段（best-effort）：本輪有新草稿才跑，好讓管理員預覽時看得到標題、摘要與摘錄。
#    依序跑、各自取 claude 鎖（這時入庫段已經放掉鎖），不巢狀。
if [ -s "$HASHES" ]; then
  N=$(grep -c . "$HASHES" 2>/dev/null || echo 0)
  DOWNSTREAM_BAD=0
  for stage in generate_summaries generate_titles extract_takeaways; do
    log "本輪新草稿 ${N} 篇 → ${stage}"
    RC=0
    py "scripts/${stage}.py" --hashes-file "$HASHES" || RC=$?
    if [ "$RC" -ne 0 ]; then
      log "${stage} 非零退出 rc=${RC}（best-effort，已略過）"
      DOWNSTREAM_BAD=1
    fi
  done
  if [ "$DOWNSTREAM_BAD" -ne 0 ]; then
    # 下一輪 ingest 會覆寫 $HASHES：沒跑完的那幾篇只剩這份保留檔可以補（三段都冪等，只挑缺的）。
    KEEP="data/upload_hashes_retained_${ROUND_TS}.txt"
    if cp -f "$HASHES" "$KEEP" 2>/dev/null; then
      log "  本輪草稿的 hashes 已保留：${KEEP}；排除原因後依序補跑："
      for stage in generate_summaries generate_titles extract_takeaways; do
        log "     $UV run python scripts/${stage}.py --hashes-file ${KEEP}"
      done
    else
      log "  保留 hashes 失敗（${HASHES} → ${KEEP}）；下一輪入庫前手動複製 ${HASHES}"
    fi
  fi
fi

# 4) 清除
CLEANUP_RC=0
py scripts/process_uploads.py cleanup || CLEANUP_RC=$?
if [ "$CLEANUP_RC" -ne 0 ]; then
  log "清除段非零退出 rc=${CLEANUP_RC}"
fi

# 收場：自己壞了（任一段不是 0／2／3／75）優先，其次 LLM 設定錯誤（告警），再其次 DB 不跑，最後撞鎖。
FINAL=0
for rc in "$SCAN_RC" "$INGEST_RC" "$CLEANUP_RC"; do
  if [ "$rc" -ne 0 ] && [ "$rc" -ne "$ENV_ABORT_RC" ] && [ "$rc" -ne "$LLM_CONFIG_RC" ] \
    && [ "$rc" -ne "$LOCK_BUSY_RC" ]; then
    FINAL=1
  fi
done
if [ "$FINAL" -eq 0 ]; then
  if [ "$INGEST_RC" -eq "$LLM_CONFIG_RC" ]; then
    FINAL=$LLM_CONFIG_RC
  elif [ "$INGEST_RC" -eq "$ENV_ABORT_RC" ] || [ "$CLEANUP_RC" -eq "$ENV_ABORT_RC" ]; then
    FINAL=$ENV_ABORT_RC
  elif [ "$INGEST_RC" -eq "$LOCK_BUSY_RC" ]; then
    FINAL=$LOCK_BUSY_RC
  fi
fi
exit "$FINAL"
