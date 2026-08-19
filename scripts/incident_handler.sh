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
# ── 監控自己的監控（2026-08-19 生產部署實測後補上）─────────────────────────
# **systemd 不會永遠保存 ExecMain*。** 隔離 unit 的實測（七組情境）證實：
#   - 成功的 oneshot 在**沒有其他 unit 引用**時會被回收，`ExecMainExitTimestampMonotonic`
#     歸 0、`InvocationID` 清空——與「從未執行過」的輸出**逐欄完全相同**
#   - `systemctl disable report-mark-health.timer` 會**當場**抹掉那個觀測值
#   - 但 `stop`（保持 enabled）不會，因為 timer unit 仍載入著、仍持有引用
#   - 失敗的 unit **不會**被回收（停在 failed 並保留完整屬性）
#   - `systemctl show` 對**不存在**的 unit 回 rc=0 加一整組預設值，只有 LoadState 分得出來
#
# 後果：初版在 `probe_mono=0` 且無進行中事件時直接 `emit noop probe_never_ran`。
# 那條路徑同時涵蓋「P4 還沒跑第一輪」與「有人把 P4 的 timer 停了」——後者代表
# **監控已經死了而 P5 每 2 分鐘回報一次沒事**。這正是 P5 存在要防的那種失效，
# 只是換成發生在監控自己身上。
#
# 因此每一輪都先驗證訊號源本身（timer 的 is-enabled／is-active／LoadState 與
# 觀測新鮮度），把結果分成兩類**互相獨立**的事件：
#   WEB_HEALTH    服務真的壞了（探針明確回 exit 1/2/4）
#   MONITOR_BLIND 我看不見了（timer 被停／不存在／觀測消失或過期）
# 兩者各有自己的狀態檔，不會互相覆蓋——「監控瞎了」不得把進行中的 web 事件
# 誤判成已恢復，反之亦然。
#
# 刻意不用 Python／uv／.venv：與 P4 同一個理由——2026-08-18 的根因是 venv 損毀，
# 相依它的告警器會與被監控的東西一起死。這裡只用 systemctl／flock／coreutils／curl。

set -u

PROBE_UNIT="${INCIDENT_PROBE_UNIT:-report-mark-health.service}"
TIMER_UNIT="${INCIDENT_TIMER_UNIT:-report-mark-health.timer}"
COMPONENT="${INCIDENT_COMPONENT:-web}"
MONITOR_COMPONENT="${INCIDENT_MONITOR_COMPONENT:-monitor}"
STATE_DIR="${INCIDENT_STATE_DIR:-}"
REMINDER_SECONDS="${INCIDENT_REMINDER_SECONDS:-1800}"   # 30 分鐘

# ── 門檻：全部由 2026-08-19 的生產實測校準，不是猜的 ──────────────────────
# P4 的穩態間隔**不是 120 秒**。OnUnitActiveSec 從 service 進入 active 起算，
# 加上 AccuracySec=10s 的抖動與探針自身耗時，T0 之後連續 9 個間隔實測為
# 125/132/136/134/135/135/126/126/134（min 125、max 136、mean 131.4）。
# 把「正常」定成 120 會讓每一輪都看起來遲到。
STALE_SECONDS="${INCIDENT_STALE_SECONDS:-420}"          # ≈3 個週期沒有新結果＝不只是抖動
BLIND_CRITICAL_SECONDS="${INCIDENT_BLIND_CRITICAL_SECONDS:-900}"  # ≈7 個週期＝升級 CRITICAL
# 開機／剛 enable 後的合理空窗：OnBootSec(120) ＋ AccuracySec(10) ＋
# 探針最壞耗時 TimeoutStartSec(90) ＝ 220s。取 300s＝36% 餘裕，且遠低於 STALE。
# **刻意不沿用初版的 600s**：那個值是在還沒有任何實測分布時訂的。
BOOTSTRAP_SECONDS="${INCIDENT_BOOTSTRAP_SECONDS:-300}"
WEBHOOK="${REPORT_MARK_ALERT_WEBHOOK:-}"

# repo 根由腳本自身位置推導（與 sync_new_reports.sh 同慣用語）
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
[ -n "$STATE_DIR" ] || STATE_DIR="$ROOT/data/.incidents"

EXIT_OK=0          # 正常處理完（含 no-op）
EXIT_TOOLING=4     # 自己不能執行

# systemctl 是唯一的訊號來源。它不在就不是「沒事」，是「什麼都不知道」——
# 必須大聲失敗，不能落進任何 noop 分支。
command -v systemctl >/dev/null 2>&1 || {
    printf 'ts=%s component=%s handler=incident status=tooling action=noop severity=WARNING incident=CLOSED reason=systemctl_not_found notified=no\n' \
        "$(date -Iseconds)" "$COMPONENT"
    echo "incident_handler: 找不到 systemctl，無法取得任何觀測" >&2
    exit "$EXIT_TOOLING"
}

mkdir -p "$STATE_DIR" 2>/dev/null || {
    echo "incident_handler: 無法建立 $STATE_DIR" >&2
    exit "$EXIT_TOOLING"
}

# ── 並發保護 ──────────────────────────────────────────────────────────────
# systemd 理論上不會讓同一個 oneshot 疊跑，但手動執行、timer 抖動與
# `systemctl start` 都可能造成重入。狀態機讀-改-寫不是原子的，兩份同時跑會
# 讓「是否已通知」互相覆蓋——症狀是重複通知或漏送，兩者都很難事後看出來。
# **鎖是 handler 層級而非元件層級**：一輪要處理 web 與 monitor 兩個元件，
# 分開上鎖會讓兩份 handler 各自搶到一半、對同一次 systemd 讀取做出兩套決定。
LOCK_FILE="$STATE_DIR/handler.lock"
exec 9>"$LOCK_FILE" 2>/dev/null || true
if command -v flock >/dev/null 2>&1; then
    flock -n 9 || { echo "incident_handler: 另一份正在執行，本次跳過" >&2; exit "$EXIT_OK"; }
fi

log() { echo "$*"; }

# status 與 reason 都是**封閉詞彙**：消費端（人或後續工具）要能窮舉。
# status ∈ ok | web_incident | monitor_blind | bootstrap | tooling
emit() {
    # status action severity incident reason notified [component]
    printf 'ts=%s component=%s handler=incident status=%s action=%s severity=%s incident=%s reason=%s notified=%s\n' \
        "$(date -Iseconds)" "${7:-$COMPONENT}" "$1" "$2" "$3" "$4" "$5" "$6"
}

BOOT_ID="$(cat /proc/sys/kernel/random/boot_id 2>/dev/null || echo unknown)"
now_epoch="$(date +%s)"
now_mono_us=$(( $(awk '{printf "%d", $1 * 1000000}' /proc/uptime 2>/dev/null || echo 0) ))

# ── 狀態檔 ────────────────────────────────────────────────────────────────
# **永遠不 source、不 eval**：狀態檔是被寫進磁碟的外部輸入，source 它等於把
# 檔案內容當成程式碼執行。逐鍵解析，並且**每一個會進入算術展開的欄位都要驗**——
# 漏掉 first_seen 曾讓損毀的狀態檔（first_seen=NOT_A_NUMBER）使整支腳本以
# rc=1、stdout/stderr 全空的方式靜默死亡。
load_state() {
    st_file="$STATE_DIR/$1.state"
    st_state=CLOSED
    st_severity=
    st_first_seen=0
    st_last_notified=0
    st_last_obs_monotonic=0
    st_count=0
    st_boot_id=
    if [ -f "$st_file" ]; then
        while IFS='=' read -r k v; do
            case "$k" in
                state)               st_state="$v" ;;
                severity)            st_severity="$v" ;;
                first_seen)          st_first_seen="$v" ;;
                last_notified)       st_last_notified="$v" ;;
                last_obs_monotonic)  st_last_obs_monotonic="$v" ;;
                count)               st_count="$v" ;;
                boot_id)             st_boot_id="$v" ;;
            esac
        done < "$st_file"
    fi
    case "$st_state" in FIRING|CLOSED) ;; *) st_state=CLOSED ;; esac
    case "$st_severity" in CRITICAL|WARNING|RESOLVED|'') ;; *) st_severity= ;; esac
    case "$st_first_seen" in ''|*[!0-9]*) st_first_seen=0 ;; esac
    case "$st_last_notified" in ''|*[!0-9]*) st_last_notified=0 ;; esac
    case "$st_last_obs_monotonic" in ''|*[!0-9]*) st_last_obs_monotonic=0 ;; esac
    case "$st_count" in ''|*[!0-9]*) st_count=0 ;; esac
    # 單調時鐘的 epoch 是「本次開機」。重開機後狀態檔裡的 last_obs_monotonic 來自
    # 上一次開機，數值可能比現在大得多——直接比對會把舊觀測當成未來的新觀測。
    [ "$st_boot_id" != "$BOOT_ID" ] && st_last_obs_monotonic=0
    [ "$st_first_seen" -eq 0 ] && st_first_seen="$now_epoch"
}

write_state() {
    # 原子寫入：先寫暫存再 rename。同一個檔案系統上 rename 是原子的，
    # 中途崩潰只會留下舊檔或新檔，不會有寫到一半的狀態。
    # （這正是 2026-08-18 在 9p 上失效的那個保證——ext4 上成立。）
    tmp="$STATE_DIR/$1.state.tmp.$$"
    {
        echo "state=$2"
        echo "severity=$3"
        echo "first_seen=$4"
        echo "last_notified=$5"
        echo "last_obs_monotonic=$6"
        echo "count=$7"
        echo "boot_id=$BOOT_ID"
    } > "$tmp" && mv -f "$tmp" "$STATE_DIR/$1.state" || {
        rm -f "$tmp" 2>/dev/null
        echo "incident_handler: 狀態寫入失敗" >&2
        return 1
    }
}

# 結果經全域 NOTIFY_SENT 回傳，**不用 stdout**：這支腳本的 stdout 是給 journal 與
# 消費端看的結構化輸出，若 notify 也往 stdout 回傳值，命令替換會把日誌行一起
# 吃進去，讓 emit 的欄位被汙染（初版就是這樣，被 test_webhook_failure 抓到）。
NOTIFY_SENT=no
notify() {
    local comp="$1" action="$2" severity="$3" summary="$4"
    NOTIFY_SENT=no
    log "[$severity] $action $comp: $summary"
    if [ -n "$WEBHOOK" ]; then
        # 投遞失敗不得影響狀態機的正確性：狀態仍然照常推進，只是這一則沒送到。
        # 反過來（送失敗就不更新狀態）會讓下一輪重送，變成投遞端故障時的通知風暴。
        if curl -fsS --max-time 10 -X POST "$WEBHOOK" \
            -H 'Content-Type: application/json' \
            -d "{\"text\":\"[report-mark][$severity] $action $comp: $summary\"}" >/dev/null 2>&1; then
            NOTIFY_SENT=yes
        else
            log "webhook 投遞失敗（狀態仍已更新，不重送以免形成風暴）"
        fi
    fi
}

# ── 通用狀態機 ────────────────────────────────────────────────────────────
# 兩個元件（web / monitor）共用同一套 FIRING/CLOSED 轉換與提醒節奏，但**狀態檔
# 各自獨立**。這是刻意的：監控失明不得把進行中的 web 事件覆蓋或誤判成已恢復。
#
# verdict:
#   healthy  一切正常 → 若在 FIRING 則 RESOLVED
#   failing  確定壞了 → 開事件／提醒／升級
#   hold     不知道（bootstrap）→ **既不開也不關**，只讓既有事件照常提醒
run_state_machine() {
    local comp="$1" verdict="$2" severity="$3" reason="$4" obs="$5" status="$6" detail="$7"
    load_state "$comp"

    local new_observation=yes
    [ "$obs" = "$st_last_obs_monotonic" ] && new_observation=no

    if [ "$verdict" = healthy ]; then
        if [ "$st_state" = FIRING ]; then
            local dur=$(( now_epoch - st_first_seen ))
            notify "$comp" RESOLVED RESOLVED "已恢復，本次事件持續 ${dur}s、共 ${st_count} 次失敗觀測"
            rm -f "$STATE_DIR/$comp.state" 2>/dev/null
            emit "$status" resolved RESOLVED CLOSED "$reason" "$NOTIFY_SENT" "$comp"
        else
            [ "$new_observation" = yes ] && write_state "$comp" CLOSED "" "$st_first_seen" "$st_last_notified" "$obs" 0
            emit "$status" noop none CLOSED "$reason" no "$comp"
        fi
        return 0
    fi

    if [ "$verdict" = hold ] && [ "$st_state" = CLOSED ]; then
        # 合理空窗且沒有進行中的事件：不開事件，但**明說是 bootstrap 而不是 ok**。
        emit "$status" noop none CLOSED "$reason" no "$comp"
        return 0
    fi

    # failing，或 hold 但已有進行中的事件（後者不得因為「這輪不知道」就靜音）
    if [ "$st_state" = CLOSED ]; then
        notify "$comp" FIRING "$severity" "$detail"
        write_state "$comp" FIRING "$severity" "$now_epoch" "$now_epoch" "$obs" 1
        emit "$status" firing "$severity" FIRING "$reason" "$NOTIFY_SENT" "$comp"
        return 0
    fi

    local count="$st_count"
    [ "$new_observation" = yes ] && count=$(( st_count + 1 ))
    local eff_severity="$severity"
    [ "$verdict" = hold ] && eff_severity="${st_severity:-WARNING}"

    # 升級（WARNING → CRITICAL）必須立刻通知，不能等提醒週期——否則「暫時看不見」
    # 惡化成「確定被停掉」這件事會被去重機制吞掉最多 30 分鐘。
    if [ "$eff_severity" = CRITICAL ] && [ "${st_severity:-}" = WARNING ]; then
        notify "$comp" ESCALATED CRITICAL "$detail"
        write_state "$comp" FIRING CRITICAL "$st_first_seen" "$now_epoch" "$obs" "$count"
        emit "$status" escalated CRITICAL FIRING "$reason" "$NOTIFY_SENT" "$comp"
        return 0
    fi

    local since_notify=$(( now_epoch - st_last_notified ))
    # 牆鐘回跳（WSL 休眠喚醒、NTP 校正）或狀態檔損毀成未來時間戳，會讓這個差值變成
    # 負數，而負數永遠小於門檻 ⇒ 提醒永遠不觸發、事件無聲卡住。fail-open：當成到期。
    [ "$since_notify" -lt 0 ] && since_notify="$REMINDER_SECONDS"
    if [ "$since_notify" -ge "$REMINDER_SECONDS" ]; then
        local dur=$(( now_epoch - st_first_seen ))
        notify "$comp" REMINDER "$eff_severity" "仍未恢復，已持續 ${dur}s、共 ${count} 次失敗觀測（${detail}）"
        write_state "$comp" FIRING "$eff_severity" "$st_first_seen" "$now_epoch" "$obs" "$count"
        emit "$status" reminder "$eff_severity" FIRING "$reason" "$NOTIFY_SENT" "$comp"
    else
        write_state "$comp" FIRING "$eff_severity" "$st_first_seen" "$st_last_notified" "$obs" "$count"
        emit "$status" suppress "$eff_severity" FIRING "$reason" no "$comp"
    fi
}

# ── 讀訊號源：systemctl 的輸出一律當成不可信字串 ──────────────────────────
# `systemctl show` 對**不存在**的 unit 也回 rc=0 加一整組預設值（實測），
# 所以「查詢成功」什麼都不保證，只有 LoadState 分得出 unit 到底在不在。
# **退出碼必須在父 shell 承接。** 初版把它包成 show_prop() 並在 `$(...)` 裡設
# query_failed=yes——命令替換是子 shell，那個賦值當場就丟了，於是 systemctl 整個
# 壞掉時仍會一路走到「沒有觀測」而歸因錯誤。`var="$(cmd)"` 的退出碼就是 cmd 的
# 退出碼，直接接 `||` 才會作用在父 shell。
query_failed=no
timer_load="$(systemctl show "$TIMER_UNIT" -p LoadState --value 2>/dev/null)" || query_failed=yes
timer_enter_mono="$(systemctl show "$TIMER_UNIT" -p ActiveEnterTimestampMonotonic --value 2>/dev/null)" || query_failed=yes
svc_load="$(systemctl show "$PROBE_UNIT" -p LoadState --value 2>/dev/null)" || query_failed=yes
probe_result="$(systemctl show "$PROBE_UNIT" -p Result --value 2>/dev/null)" || query_failed=yes
probe_status="$(systemctl show "$PROBE_UNIT" -p ExecMainStatus --value 2>/dev/null)" || query_failed=yes
probe_mono="$(systemctl show "$PROBE_UNIT" -p ExecMainExitTimestampMonotonic --value 2>/dev/null)" || query_failed=yes
timer_enabled="$(systemctl is-enabled "$TIMER_UNIT" 2>/dev/null || true)"
timer_active="$(systemctl is-active "$TIMER_UNIT" 2>/dev/null || true)"

# 全部數值欄位驗證（外部輸入，可能是空字串、字母或負數）
case "$probe_status"    in ''|*[!0-9]*) probe_status=-1 ;; esac
case "$probe_mono"      in ''|*[!0-9]*) probe_mono=0 ;; esac
case "$timer_enter_mono" in ''|*[!0-9]*) timer_enter_mono=0 ;; esac

# ── 分類訊號源 ────────────────────────────────────────────────────────────
# 這一段的唯一職責是回答「我現在看得見嗎」，**不看服務健康與否**。
signal=ok
sig_status=ok        # 對外的封閉詞彙，與內部的 signal 分開：ok|bootstrap|monitor_blind
sig_reason=
sig_severity=
sig_detail=
if [ "$query_failed" = yes ]; then
    signal=blind; sig_status=monitor_blind; sig_severity=WARNING; sig_reason=query_failed
    sig_detail="systemctl 查詢失敗，無法取得 $PROBE_UNIT 的觀測"
elif [ "$timer_load" = not-found ] || [ "$timer_enabled" = not-found ]; then
    signal=blind; sig_status=monitor_blind; sig_severity=CRITICAL; sig_reason=timer_not_found
    sig_detail="$TIMER_UNIT 不存在——健康探針的排程來源消失了"
elif [ "$svc_load" = not-found ]; then
    signal=blind; sig_status=monitor_blind; sig_severity=CRITICAL; sig_reason=service_not_found
    sig_detail="$PROBE_UNIT 不存在——健康探針本身消失了"
elif [ "$timer_enabled" = disabled ]; then
    signal=blind; sig_status=monitor_blind; sig_severity=CRITICAL; sig_reason=timer_disabled
    sig_detail="$TIMER_UNIT 已被停用——健康探針不會再執行"
elif [ "$timer_active" != active ]; then
    signal=blind; sig_status=monitor_blind; sig_severity=CRITICAL; sig_reason=timer_inactive
    sig_detail="$TIMER_UNIT 不在 active（state=${timer_active:-unknown}）——不會再觸發探測"
elif [ "$probe_mono" -eq 0 ]; then
    # 沒有任何觀測。timer 本身健康，所以只可能是「還沒跑第一輪」或「觀測消失了」。
    # 兩者用 timer 進入 active 的時間分辨：剛 enable／剛開機屬前者。
    bootstrap_age=$(( (now_mono_us - timer_enter_mono) / 1000000 ))
    [ "$bootstrap_age" -lt 0 ] && bootstrap_age=0
    if [ "$timer_enter_mono" -gt 0 ] && [ "$bootstrap_age" -le "$BOOTSTRAP_SECONDS" ]; then
        signal=bootstrap; sig_status=bootstrap; sig_reason=awaiting_first_probe
        sig_detail="timer 啟動 ${bootstrap_age}s，尚未產出第一筆觀測（空窗上限 ${BOOTSTRAP_SECONDS}s）"
    else
        signal=blind; sig_status=monitor_blind; sig_severity=CRITICAL; sig_reason=observation_missing
        sig_detail="timer 看似正常但沒有任何觀測（已 ${bootstrap_age}s，超過空窗上限 ${BOOTSTRAP_SECONDS}s）"
    fi
else
    obs_age=$(( (now_mono_us - probe_mono) / 1000000 ))
    [ "$obs_age" -lt 0 ] && obs_age=0
    if [ "$obs_age" -gt "$BLIND_CRITICAL_SECONDS" ]; then
        signal=blind; sig_status=monitor_blind; sig_severity=CRITICAL; sig_reason=observation_stale
        sig_detail="健康探針已 ${obs_age}s 沒有新結果（門檻 ${STALE_SECONDS}s，升級門檻 ${BLIND_CRITICAL_SECONDS}s）"
    elif [ "$obs_age" -gt "$STALE_SECONDS" ]; then
        signal=blind; sig_status=monitor_blind; sig_severity=WARNING; sig_reason=observation_stale
        sig_detail="健康探針已 ${obs_age}s 沒有新結果（門檻 ${STALE_SECONDS}s）"
    fi
fi

# ── 元件一：監控本身 ──────────────────────────────────────────────────────
case "$signal" in
    ok)        run_state_machine "$MONITOR_COMPONENT" healthy ""             monitor_ok           "$probe_mono" ok            "" ;;
    bootstrap) run_state_machine "$MONITOR_COMPONENT" hold    ""             "$sig_reason"        "$probe_mono" bootstrap     "$sig_detail" ;;
    blind)     run_state_machine "$MONITOR_COMPONENT" failing "$sig_severity" "$sig_reason"       "$probe_mono" monitor_blind "$sig_detail" ;;
esac

# ── 元件二：web 服務 ──────────────────────────────────────────────────────
# **訊號不可信時完全不碰 web 的狀態檔。** 讓進行中的 web 事件維持 FIRING：
# 「我看不見了」不是「已經好了」，把它當成恢復會在真正的中斷中途送出 RESOLVED。
if [ "$signal" != ok ]; then
    load_state "$COMPONENT"
    emit "$sig_status" skip none "$st_state" "signal_$sig_reason" no "$COMPONENT"
    exit "$EXIT_OK"
fi

# P4 的退出碼契約：0 健康／3 寬限（視為健康）／1,2 服務故障／4 探針自身錯誤
case "$probe_status" in
    0|3) run_state_machine "$COMPONENT" healthy "" healthy "$probe_mono" ok "" ;;
    1|2) run_state_machine "$COMPONENT" failing CRITICAL "probe_exit_$probe_status" "$probe_mono" web_incident \
             "探針回報失敗（exit=$probe_status result=$probe_result）" ;;
    4)   run_state_machine "$COMPONENT" failing WARNING "probe_exit_4" "$probe_mono" web_incident \
             "探針自己不能執行（exit=4 result=$probe_result）——是「我不知道」不是「壞了」" ;;
    *)   run_state_machine "$COMPONENT" failing WARNING "probe_exit_unknown" "$probe_mono" web_incident \
             "探針回報未知退出碼（exit=$probe_status result=$probe_result）" ;;
esac
exit "$EXIT_OK"
