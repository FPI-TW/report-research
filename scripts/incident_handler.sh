#!/usr/bin/env bash
# 事件偵測與通知（P5）——消費 P4 探針的結果，決定要不要打擾人。
#
# 分工（見 deploy/systemd/report-mark-health.service 的註解）：
#   P4  每 2 分鐘探測，回報「此刻的事實」，**不通知任何人**
#   P5  記住事實的歷史，做去重／提醒節奏／恢復判定，並在有設定時投遞 webhook
#
# 為什麼需要它：2026-08-18 的中斷持續 4 小時 50 分，期間告警機制**確實運作了**
# （data/unit_failures.log 累積 854 筆完整紀錄），但那個檔案沒有任何消費端。
# 中斷最終是在一次無關的遷移工作中被順帶發現的。854 筆同源紀錄同時說明另一件事：
# **沒有去重就等於沒有告警**。同一次事件在這裡只會產生 1 則 FIRING ＋ 每 30 分鐘
# 一則提醒 ＋ 1 則 RESOLVED（該次中斷＝11 則，而不是 145 則）。
#
# 為什麼是輪詢而不是掛在 P4 的 OnFailure：P4 刻意不宣告 OnFailure（理由見該 unit），
# 而 `OnFailure` 只在失敗時觸發、`ExecStartPost` 只在成功時觸發——兩者都看不到
# 完整的狀態轉換，而 RESOLVED 必須看得到成功。因此這裡讀 systemd 保留的
# 執行結果，用 **ExecMainExitTimestampMonotonic** 判斷「是否有新觀測」
# （單調時鐘，不受 WSL 休眠喚醒或時區調整影響）。
#
# 刻意不用 Python／uv／.venv：與 P4 同一個理由——2026-08-18 的根因是 venv 損毀，
# 相依它的告警器會與被監控的東西一起死。這裡只用 systemctl／flock／coreutils／curl。
set -u

PROBE_UNIT="${INCIDENT_PROBE_UNIT:-report-mark-health.service}"
COMPONENT="${INCIDENT_COMPONENT:-web}"
STATE_DIR="${INCIDENT_STATE_DIR:-}"
REMINDER_SECONDS="${INCIDENT_REMINDER_SECONDS:-1800}"   # 30 分鐘
STALE_SECONDS="${INCIDENT_STALE_SECONDS:-600}"          # P4 超過 10 分鐘沒有新結果＝探針本身停了
WEBHOOK="${REPORT_MARK_ALERT_WEBHOOK:-}"

# repo 根由腳本自身位置推導（與 sync_new_reports.sh 同慣用語）
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
[ -n "$STATE_DIR" ] || STATE_DIR="$ROOT/data/.incidents"

STATE_FILE="$STATE_DIR/$COMPONENT.state"
LOCK_FILE="$STATE_DIR/$COMPONENT.lock"

EXIT_OK=0          # 正常處理完（含 no-op）
EXIT_TOOLING=4     # 自己不能執行

mkdir -p "$STATE_DIR" 2>/dev/null || {
    echo "incident_handler: 無法建立 $STATE_DIR" >&2
    exit "$EXIT_TOOLING"
}

# ── 並發保護 ──────────────────────────────────────────────────────────────
# systemd 理論上不會讓同一個 oneshot 疊跑，但手動執行、timer 抖動與
# `systemctl start` 都可能造成重入。狀態機讀-改-寫不是原子的，兩份同時跑會
# 讓「是否已通知」互相覆蓋——症狀是重複通知或漏送，兩者都很難事後看出來。
exec 9>"$LOCK_FILE" 2>/dev/null || true
if command -v flock >/dev/null 2>&1; then
    flock -n 9 || { echo "incident_handler: 另一份正在執行，本次跳過" >&2; exit "$EXIT_OK"; }
fi

log() { echo "$*"; }

emit() {
    printf 'ts=%s component=%s handler=incident action=%s severity=%s incident=%s reason=%s notified=%s\n' \
        "$(date -Iseconds)" "$COMPONENT" "$1" "$2" "$3" "$4" "$5"
}

# ── 狀態檔（key=value，shell 可解析、不需 jq）──────────────────────────────
st_state=CLOSED
st_severity=
st_first_seen=
st_last_notified=0
st_last_obs_monotonic=0
st_count=0

if [ -f "$STATE_FILE" ]; then
    # 逐鍵讀取而非 `source`：狀態檔是本程式自己寫的，但 source 會把它當 shell
    # 執行——一旦檔案損毀（磁碟寫到一半、有人手改），那就是任意程式碼執行。
    while IFS='=' read -r k v; do
        case "$k" in
            state)               st_state="$v" ;;
            severity)            st_severity="$v" ;;
            first_seen)          st_first_seen="$v" ;;
            last_notified)       st_last_notified="$v" ;;
            last_obs_monotonic)  st_last_obs_monotonic="$v" ;;
            count)               st_count="$v" ;;
        esac
    done < "$STATE_FILE"
fi
# 損毀的欄位一律回退到安全預設（fail-open：寧可多送一則，不要靜默漏送）
case "$st_state" in FIRING|CLOSED) ;; *) st_state=CLOSED ;; esac
case "$st_last_notified" in ''|*[!0-9]*) st_last_notified=0 ;; esac
case "$st_last_obs_monotonic" in ''|*[!0-9]*) st_last_obs_monotonic=0 ;; esac
case "$st_count" in ''|*[!0-9]*) st_count=0 ;; esac

write_state() {
    # 原子寫入：先寫暫存再 rename。同一個檔案系統上 rename 是原子的，
    # 中途崩潰只會留下舊檔或新檔，不會有寫到一半的狀態。
    # （這正是 2026-08-18 在 9p 上失效的那個保證——ext4 上成立。）
    tmp="$STATE_FILE.tmp.$$"
    {
        echo "state=$1"
        echo "severity=$2"
        echo "first_seen=$3"
        echo "last_notified=$4"
        echo "last_obs_monotonic=$5"
        echo "count=$6"
    } > "$tmp" && mv -f "$tmp" "$STATE_FILE" || {
        rm -f "$tmp" 2>/dev/null
        echo "incident_handler: 狀態寫入失敗" >&2
        return 1
    }
}

# 結果經全域 NOTIFY_SENT 回傳，**不用 stdout**：這支腳本的 stdout 是給 journal 與
# P5 消費者看的結構化輸出，若 notify 也往 stdout 回傳值，命令替換會把日誌行一起
# 吃進去，讓 emit 的欄位被汙染（初版就是這樣，被 test_webhook_failure 抓到）。
NOTIFY_SENT=no
notify() {
    # action / severity / summary
    local action="$1" severity="$2" summary="$3"
    NOTIFY_SENT=no
    log "[$severity] $action: $summary"
    if [ -n "$WEBHOOK" ]; then
        # 投遞失敗不得影響狀態機的正確性：狀態仍然照常推進，只是這一則沒送到。
        # 反過來（送失敗就不更新狀態）會讓下一輪重送，變成投遞端故障時的通知風暴。
        if curl -fsS --max-time 10 -X POST "$WEBHOOK" \
            -H 'Content-Type: application/json' \
            -d "{\"text\":\"[report-mark][$severity] $action $COMPONENT: $summary\"}" >/dev/null 2>&1; then
            NOTIFY_SENT=yes
        else
            log "webhook 投遞失敗（狀態仍已更新，不重送以免形成風暴）"
        fi
    fi
}

# ── 讀 P4 的最新結果 ──────────────────────────────────────────────────────
probe_result="$(systemctl show "$PROBE_UNIT" -p Result --value 2>/dev/null || true)"
probe_status="$(systemctl show "$PROBE_UNIT" -p ExecMainStatus --value 2>/dev/null || true)"
probe_mono="$(systemctl show "$PROBE_UNIT" -p ExecMainExitTimestampMonotonic --value 2>/dev/null || true)"
case "$probe_status" in ''|*[!0-9]*) probe_status=-1 ;; esac
case "$probe_mono"   in ''|*[!0-9]*) probe_mono=0 ;; esac

now_epoch="$(date +%s)"
now_mono_us=$(( $(awk '{printf "%d", $1 * 1000000}' /proc/uptime 2>/dev/null || echo 0) ))

# P4 從未執行過：沒有可消費的訊號，不是故障也不是健康。
if [ "$probe_mono" -eq 0 ]; then
    emit noop none "$st_state" probe_never_ran no
    exit "$EXIT_OK"
fi

# P4 自己停了：距上次結果超過 STALE_SECONDS。這是**監控失明**，不是服務故障。
obs_age=$(( (now_mono_us - probe_mono) / 1000000 ))
[ "$obs_age" -lt 0 ] && obs_age=0
if [ "$obs_age" -gt "$STALE_SECONDS" ]; then
    if [ "$st_state" = CLOSED ]; then
        notify FIRING WARNING "健康探針已 ${obs_age}s 沒有新結果（門檻 ${STALE_SECONDS}s）——監控本身可能停了"
        sent="$NOTIFY_SENT"
        write_state FIRING WARNING "$now_epoch" "$now_epoch" "$st_last_obs_monotonic" 1
        emit firing WARNING FIRING probe_stale "$sent"
    else
        emit suppress "${st_severity:-WARNING}" FIRING probe_stale no
    fi
    exit "$EXIT_OK"
fi

# 沒有新觀測（P4 還沒跑下一輪）：不重複計數，但進行中的事件仍需判斷提醒是否到期。
new_observation=yes
[ "$probe_mono" = "$st_last_obs_monotonic" ] && new_observation=no

# P4 的退出碼契約：0 健康／3 寬限（視為健康）／1,2 服務故障／4 探針自身錯誤
case "$probe_status" in
    0|3) healthy=yes; severity=  ;;
    1|2) healthy=no;  severity=CRITICAL ;;   # 使用者當下無法使用
    4)   healthy=no;  severity=WARNING  ;;   # 探針壞了＝「我不知道」，不是「壞了」
    *)   healthy=no;  severity=WARNING  ;;
esac

# ── 狀態機 ────────────────────────────────────────────────────────────────
if [ "$healthy" = yes ]; then
    if [ "$st_state" = FIRING ]; then
        dur=$(( now_epoch - ${st_first_seen:-$now_epoch} ))
        notify RESOLVED RESOLVED "已恢復，本次事件持續 ${dur}s、共 ${st_count} 次失敗觀測"
        sent="$NOTIFY_SENT"
        rm -f "$STATE_FILE" 2>/dev/null
        emit resolved RESOLVED CLOSED recovered "$sent"
    else
        # 健康且無進行中事件：只更新觀測游標，不寫通知
        [ "$new_observation" = yes ] && write_state CLOSED "" "" "$st_last_notified" "$probe_mono" 0
        emit noop none CLOSED healthy no
    fi
    exit "$EXIT_OK"
fi

# 失敗路徑
if [ "$st_state" = CLOSED ]; then
    notify FIRING "$severity" "探針回報失敗（exit=$probe_status result=$probe_result）"
    sent="$NOTIFY_SENT"
    write_state FIRING "$severity" "$now_epoch" "$now_epoch" "$probe_mono" 1
    emit firing "$severity" FIRING "probe_exit_$probe_status" "$sent"
    exit "$EXIT_OK"
fi

# 已在 FIRING：只有提醒到期才再送一次
count="$st_count"
[ "$new_observation" = yes ] && count=$(( st_count + 1 ))
since_notify=$(( now_epoch - st_last_notified ))
if [ "$since_notify" -ge "$REMINDER_SECONDS" ]; then
    dur=$(( now_epoch - ${st_first_seen:-$now_epoch} ))
    notify REMINDER "$severity" "仍未恢復，已持續 ${dur}s、共 ${count} 次失敗觀測"
    sent="$NOTIFY_SENT"
    write_state FIRING "$severity" "$st_first_seen" "$now_epoch" "$probe_mono" "$count"
    emit reminder "$severity" FIRING still_failing "$sent"
else
    write_state FIRING "$severity" "$st_first_seen" "$st_last_notified" "$probe_mono" "$count"
    emit suppress "$severity" FIRING "dedup_${since_notify}s_of_${REMINDER_SECONDS}s" no
fi
exit "$EXIT_OK"
