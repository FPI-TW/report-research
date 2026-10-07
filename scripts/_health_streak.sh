# scripts/_health_streak.sh —— 容器／主機探針共用的「連續失敗次數」記錄（供 `source`）。
#
# **為什麼探針要記這一點點跨執行的狀態**：依 tier 去抖（important／supporting 要連續多次失敗才算數）
# 需要記得上一輪。這件事刻意放在探針、不放在 scripts/incident_handler.sh（P5）：P5 是主要的 incident
# state machine／狀態型告警路徑（unit failure 的 report-mark-alert 與上傳感染通知另有路徑），它的狀態機
# 一行都不動；探針只是把「尚在確認期」用一個 hold 退出碼（3）
# 表達出來，那正是 P5 既有的 INCIDENT_HOLD_EXIT_CODES 機制（邊緣那一組已經在用）。
# 這裡記的只是每個檢查項目的連續失敗次數，**不是事件狀態**：不去重、不通知、不決定 RESOLVED。
#
# 檔案格式：每行 `key=次數:最後一次失敗的 epoch`。逐行解析、**絕不 source**（落在磁碟上的外部輸入）。
# 「連續」的意思是兩次失敗之間不超過 HEALTH_STREAK_MAX_GAP 秒（預設 600 ≈ 4 個 2 分鐘週期）：探針停了
# 一陣子（timer 被停、主機休眠）之後的第一筆失敗重新起算，不沿用很久以前的計數。
#
# 狀態檔寫不進去（目錄不存在、唯讀、磁碟滿）時 STREAK_SAVE_OK=no：呼叫端改成**每筆失敗都算確認**——
# 寧可多吵，也不要因為記不住次數而永遠停在確認期（那等於靜默）。

declare -gA STREAK_N=() STREAK_T=()
STREAK_SAVE_OK=yes

streak_load() {
    local file="$1" k v n t
    STREAK_N=(); STREAK_T=()
    [ -f "$file" ] || return 0
    while IFS='=' read -r k v; do
        case "$k" in ''|*[!A-Za-z0-9_.:-]*) continue ;; esac
        n="${v%%:*}"; t="${v#*:}"
        case "$n" in ''|*[!0-9]*) continue ;; esac
        case "$t" in ''|*[!0-9]*) continue ;; esac
        STREAK_N["$k"]=$(( 10#$n )); STREAK_T["$k"]=$(( 10#$t ))
    done < "$file" 2>/dev/null
    return 0
}

# streak_bump KEY NOW：記下這一輪的失敗，之後的連續次數在 STREAK_N[KEY]（不可在 $(...) 裡呼叫：子 shell 會丟掉更新）
streak_bump() {
    local k="$1" now="$2" gap="${HEALTH_STREAK_MAX_GAP:-600}" n=0 t=0
    case "$gap" in ''|*[!0-9]*) gap=600 ;; esac
    n="${STREAK_N[$k]:-0}"; t="${STREAK_T[$k]:-0}"
    if [ "$n" -gt 0 ] && [ $(( now - t )) -ge 0 ] && [ $(( now - t )) -le "$gap" ]; then
        n=$(( n + 1 ))
    else
        n=1
    fi
    STREAK_N["$k"]="$n"; STREAK_T["$k"]="$now"
}

streak_clear() {
    unset 'STREAK_N[$1]' 'STREAK_T[$1]'
}

streak_save() {
    local file="$1" dir tmp k
    dir="$(dirname -- "$file")"
    STREAK_SAVE_OK=no
    mkdir -p -- "$dir" 2>/dev/null || return 0
    tmp="$file.tmp.$$"
    {
        for k in "${!STREAK_N[@]}"; do
            printf '%s=%s:%s\n' "$k" "${STREAK_N[$k]}" "${STREAK_T[$k]}"
        done
    } > "$tmp" 2>/dev/null && mv -f "$tmp" "$file" 2>/dev/null && STREAK_SAVE_OK=yes
    [ "$STREAK_SAVE_OK" = yes ] || rm -f "$tmp" 2>/dev/null
    return 0
}

# tier → 要連續幾次失敗才算確認（HEALTH_CONFIRM_<TIER> 可覆寫；非數字或 < 1 一律退回預設）
streak_confirm_count() {
    local tier="$1" v def
    case "$tier" in
        critical)   v="${HEALTH_CONFIRM_CRITICAL:-}";   def=1 ;;
        important)  v="${HEALTH_CONFIRM_IMPORTANT:-}";  def=2 ;;
        supporting) v="${HEALTH_CONFIRM_SUPPORTING:-}"; def=3 ;;
        tooling)    v="${HEALTH_CONFIRM_TOOLING:-}";    def=2 ;;
        *)          v="";                               def=1 ;;
    esac
    case "$v" in ''|*[!0-9]*) v="$def" ;; esac
    v=$(( 10#$v ))
    [ "$v" -lt 1 ] && v=1
    printf '%s' "$v"
}
