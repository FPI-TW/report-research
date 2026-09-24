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
# 探針單次執行的合理上限，**由 P4 的契約推導而非硬編**：
# 3 次嘗試 × HEALTH_TIMEOUT(5s) ＋ 2 次 × HEALTH_RETRY_WAIT(15s) = 45s（2026-08-20
# 中斷期間實測 46s，吻合）；第 3 次才成功時再加 L3 的 /healthz/storage 與 /healthz/llm
# 各一次 HEALTH_TIMEOUT，最壞 55s。unit 的硬上限是 TimeoutStartSec=90s。
# 超過 90s 代表 systemd 應該已經砍掉它卻沒有——那是探針卡住，不是服務故障。
# 取 120s ＝ 90s ＋ 33% 餘裕。
PROBE_MAX_INFLIGHT="${INCIDENT_PROBE_MAX_INFLIGHT:-120}"
# **上一筆已完成的觀測可以被信任多久。** 這不是「過期」門檻，是「有沒有漏讀」門檻：
# P4 每 ~130s 完成一輪（OnUnitActiveSec=2min ＋ AccuracySec=10s，實測 125–138s），
# 若 P5 每輪都讀得到，快取最多只會有「一個週期 ＋ 當前執行中」這麼舊：
#   140s（一個週期上界）＋ 90s（unit TimeoutStartSec）＝ 230s → 取 240s。
# 超過就代表 **P4 至少完成過一輪而 P5 沒讀到**——那時不能再說「上次是好的」，
# 因為我們不知道那筆漏掉的觀測是什麼。2026-08-20 的中斷正是這個形狀：
# 00:49:49 完成一筆 fail，P5 下一次取樣在 00:51:58（快取已 272s），若無此門檻
# 就會沿用 00:47:26 那筆 OK 而繼續靜默。
# ⚠ 上面那段推導**漏了一項**，2026-08-21 實測補正：快取只在 P5 讀到完成觀測時
# 更新，所以它的年齡上界不是「P4 週期 ＋ 執行時間」，而是「**P5 週期** ＋ P4 週期」
# ——實測 P5 max 153s ＋ P4 max 140s ＝ 293s，已經超過這裡的 240s。也就是說
# **健康系統本來就會週期性越線**（實測 230 輪中 14 輪，6.1%）。
# 這個門檻刻意**不調高**：調高會等比例犧牲真實故障的偵測延遲。改由下面的
# in-flight 邊界重讀把那些「其實剛完成」的取樣讀回來。兩者的分工是：
#   重讀   → 處理「P4 已完成，只是這一讀落在歸零窗內」（假陽性）
#   信任窗 → 處理「P4 真的還在跑，期間確實漏掉一輪」（真故障）
OBS_TRUST_SECONDS="${INCIDENT_OBS_TRUST_SECONDS:-240}"
# 快照不一致時的有界重讀。**這不是重試整輪**，只是再抓一次同一組屬性——
# 實測那個窗口只有毫秒級，一次重讀就足以跨過去。上限刻意很小：handler 的
# TimeoutStartSec 是 60s，而且拖長只會讓 P5 自己變成慢的那一個。
SNAPSHOT_RETRIES="${INCIDENT_SNAPSHOT_RETRIES:-1}"
SNAPSHOT_RETRY_DELAY="${INCIDENT_SNAPSHOT_RETRY_DELAY:-0.3}"
# **in-flight 邊界的有界重讀。** 與上面那組是不同的問題：那組修的是「不可能同時
# 成立」的矛盾快照（#223），這組修的是一個**完全自洽**的狀態——P4 正在 running→
# finished 的轉換點上，ExecMainExitTimestamp* 已歸零而新值尚未寫入。
# 兩支 timer 同為 2min ＋ AccuracySec 10s，systemd 的合併喚醒讓 P5 偏好落在 P4
# 執行的那一瞬間：實測命中率 6.1%，而 P4 的 duty cycle 只有 0.8%（執行 max 3s／
# mean 0.1s），高出約 7 倍——不是隨機取樣的結果。
# P4 只要 ≤3s 就結束，所以再讀一次幾乎必然拿到已完成的時戳。
# 設 0 即完全關閉，退回舊行為（出事時不必改程式）。
INFLIGHT_REREADS="${INCIDENT_INFLIGHT_REREADS:-1}"
INFLIGHT_REREAD_DELAY="${INCIDENT_INFLIGHT_REREAD_DELAY:-1.0}"
WEBHOOK="${REPORT_MARK_ALERT_WEBHOOK:-}"
# 投遞旋鈕。connect 與 overall 分開：連不上的端點應該快速失敗，而不是佔滿整個
# overall 預算——handler 的 TimeoutStartSec 是 60s，一輪最多可能發兩則通知。
NOTIFY_CONNECT_TIMEOUT="${INCIDENT_NOTIFY_CONNECT_TIMEOUT:-5}"
NOTIFY_MAX_TIME="${INCIDENT_NOTIFY_MAX_TIME:-10}"
NOTIFY_MAX_SUMMARY="${INCIDENT_NOTIFY_MAX_SUMMARY:-500}"

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
# status ∈ ok | web_incident | monitor_blind | bootstrap | in_flight | tooling
# current_probe 與 last_completed 是**分開的兩件事**：前者是觀測的生命週期，後者是
# 最近一次完成的結論。把兩者混成一個欄位正是 2026-08-20 那次中斷的成因——只印
# `status=in_flight action=skip`，operator 完全看不出「上一次其實是 fail」。
# current_probe ∈ idle|in_flight ；last_completed ∈ ok|fail|tooling|none
EMIT_CURRENT_PROBE=idle
EMIT_LAST_COMPLETED=none
EMIT_LAST_AGE=-
emit() {
    # status action severity incident reason notified [component]
    printf 'ts=%s component=%s handler=incident status=%s action=%s severity=%s incident=%s reason=%s notified=%s current_probe=%s last_completed=%s last_completed_age=%s snapshot=%s snapshot_retries=%s observation_source=%s\n' \
        "$(date -Iseconds)" "${7:-$COMPONENT}" "$1" "$2" "$3" "$4" "$5" "$6" \
        "$EMIT_CURRENT_PROBE" "$EMIT_LAST_COMPLETED" "$EMIT_LAST_AGE" \
        "${SNAPSHOT_STATE_OUT:-stable}" "${SNAPSHOT_RETRIES_OUT:-0}" "${EMIT_OBS_SOURCE:-live}"
}

SNAPSHOT_STATE_OUT=stable
SNAPSHOT_RETRIES_OUT=0
EMIT_OBS_SOURCE=live
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
    st_opened_sent=yes
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
                opened_sent)         st_opened_sent="$v" ;;
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
    # 缺值一律視為 yes：升級前寫下的狀態檔沒有這個鍵，若當成 no 會讓既有的
    # 進行中事件在部署當下多送一則 FIRING。
    case "$st_opened_sent" in yes|no) ;; *) st_opened_sent=yes ;; esac
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
        echo "opened_sent=${8:-yes}"
        echo "boot_id=$BOOT_ID"
    } > "$tmp" && mv -f "$tmp" "$STATE_DIR/$1.state" || {
        rm -f "$tmp" 2>/dev/null
        echo "incident_handler: 狀態寫入失敗" >&2
        return 1
    }
}

# JSON 字串跳脫。**payload 含 systemctl 讀來的值**（`Result`、`ActiveState`…），那是
# 外部輸入：未跳脫的 `"` 或 `\` 會產出格式錯誤的 JSON，接收端回 400，於是「通知送不出去」
# 的真正原因會偽裝成「webhook 壞了」。控制字元直接移除而不是跳脫——summary 一律單行，
# 移除比跳脫少一個失效面。**刻意只用 tr/sed**，不引入 python（P5 不依賴 venv）。
_json_escape() {
    printf '%s' "$1" | tr -d '\000-\037' | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g'
}

# ── 上一筆「已完成」的探針觀測 ────────────────────────────────────────────
# **為什麼需要快取，以及為什麼不能放進 web.state。**
#
# systemd 在 oneshot 啟動時把 ExecMainExitTimestamp* 歸零，直到結束才寫入。所以當
# 探針正在執行時，「上一筆已完成的結論」**無法從 systemd 取得**——它不是被藏起來，
# 是真的不存在於可查詢的狀態裡。P5 必須自己記住。
#
# 不放進 web.state 的理由很具體：那個檔在 RESOLVED 時會被 `rm -f`，等於**服務恢復的
# 那一刻把觀測快取一起抹掉**；而且 monitor 元件也要用這筆觀測判新鮮度——兩個消費者
# 共用一個生命週期，遲早互相污染。
#
# 2026-08-20 的真實中斷證明這件事非做不可：P4 在中斷期間每輪執行 46 秒（重試佔絕大
# 部分），約為 P5 週期的 35%，於是 P5 連續兩次取樣都落在執行窗內、兩次都判 in_flight、
# **整場 4 分 54 秒的中斷零告警**。timer 抖動只降低碰撞機率，不是正確性機制。
OBS_CACHE="$STATE_DIR/probe_observation.state"
obs_mono=0; obs_status=-1; obs_result=
load_obs_cache() {
    obs_mono=0; obs_status=-1; obs_result=; _obs_boot=
    [ -f "$OBS_CACHE" ] || return 0
    # 逐鍵解析，**絕不 source／eval**：這是落在磁碟上的外部輸入。
    while IFS='=' read -r k v; do
        case "$k" in
            boot_id)   _obs_boot="$v" ;;
            monotonic) obs_mono="$v" ;;
            status)    obs_status="$v" ;;
            result)    obs_result="$v" ;;
        esac
    done < "$OBS_CACHE"
    case "$obs_mono"   in ''|*[!0-9]*) obs_mono=0 ;; esac
    case "$obs_status" in ''|*[!0-9]*) obs_status=-1 ;; esac
    # boot_id 只接受 UUID 形狀；格式不符即視為無快取（不猜、不沿用）
    case "$_obs_boot" in
        ????????-????-????-????-????????????) ;;
        *) _obs_boot= ;;
    esac
    # **跨開機不得沿用**：單調時鐘的原點是本次開機，舊值無從比較。
    [ "$_obs_boot" != "$BOOT_ID" ] && { obs_mono=0; obs_status=-1; obs_result=; }
}

save_obs_cache() {
    tmp="$OBS_CACHE.tmp.$$"
    {
        echo "boot_id=$BOOT_ID"
        echo "monotonic=$1"
        echo "status=$2"
        echo "result=$3"
    } > "$tmp" && mv -f "$tmp" "$OBS_CACHE" || {
        rm -f "$tmp" 2>/dev/null
        echo "incident_handler: 觀測快取寫入失敗" >&2
        return 1
    }
}

# 結果經全域 NOTIFY_SENT 回傳，**不用 stdout**：這支腳本的 stdout 是給 journal 與
# 消費端看的結構化輸出，若 notify 也往 stdout 回傳值，命令替換會把日誌行一起
# 吃進去，讓 emit 的欄位被汙染（初版就是這樣，被 test_webhook_failure 抓到）。
# **兩個變數不是同一件事，混用會出兩種相反的錯。**
#   NOTIFY_SENT ＝ 這一輪有沒有真的送出去（emit 的 notified 欄位用它）
#   NOTIFY_OK   ＝ 有沒有「該送而沒送到」的通知（狀態機的推進閘用它）
# 未設定 webhook 時 NOTIFY_SENT=no 但 NOTIFY_OK=yes——沒有東西要送，就沒有東西
# 沒送到。若讓狀態機改看 NOTIFY_SENT，未設定 webhook 的部署（＝目前生產）會永遠
# 卡在「RESOLVED 沒送到」而關不掉事件，偵測功能等於被通知功能反噬。
NOTIFY_SENT=no
NOTIFY_OK=yes

# curl 退出碼 → 一句人話。只列真的遇過或最常見的幾個；其餘請 operator 查 curl(1)。
# rc=0 代表 curl 自己成功、是對方回了非 2xx——那時該看的是 HTTP code，不是這裡。
_curl_rc_hint() {
    case "$1" in
        0)  ;;
        3)  printf '＝URL 格式不合法' ;;
        6)  printf '＝主機名解析不到' ;;
        7)  printf '＝連線被拒' ;;
        28) printf '＝逾時' ;;
        35|51|58|59|60|77) printf '＝TLS 或憑證問題' ;;
        *)  printf '＝見 curl(1) EXIT CODES' ;;
    esac
}

notify() {
    local comp="$1" action="$2" severity="$3" summary="$4" reason="${5:-}"
    NOTIFY_SENT=no
    NOTIFY_OK=yes
    # log 一律不含 URL——journal 是多人可讀的，secret 不進去。
    log "[$severity] $action $comp: $summary"
    [ -n "$WEBHOOK" ] || return 0

    # **送之前先看值的形狀。** 2026-09-06 至 09-21 這個值一直是文件裡的佔位字串
    # （照著安裝步驟整行貼上執行的結果），curl 每一輪都以「URL 格式不合法」拒絕，三筆事件
    # 因 RESOLVED 送不出去而卡在 FIRING 十五天。形狀不對的值永遠送不出去，與其讓 curl
    # 每輪回一個看不懂的失敗，不如直接說是設定錯了。**訊息不含值本身**：它可能是貼錯位置
    # 的 secret。語意比照投遞失敗（NOTIFY_OK=no）：該送而沒送到，事件不得無聲結案。
    local shape=bad
    case "$WEBHOOK" in
        *[[:space:]]*) ;;
        http://?*|https://?*) shape=ok ;;
    esac
    if [ "$shape" != ok ]; then
        NOTIFY_OK=no
        log "webhook 設定值不是合法 URL（須以 http:// 或 https:// 開頭且不含空白；檢查 alert.env。事件已記錄，下一輪重試投遞）"
        return 0
    fi

    # 有界的 body：summary 由 systemd 屬性與計時差組成，長度理論上無上限，
    # 而接收端多半有 payload 上限。截斷比被對方靜默丟棄好。
    local trimmed="$summary"
    if [ "${#trimmed}" -gt "$NOTIFY_MAX_SUMMARY" ]; then
        trimmed="${trimmed:0:$NOTIFY_MAX_SUMMARY}…"
    fi
    # 結構化欄位讓接收端能路由（例如只把 CRITICAL 轉呼叫），text 保留給人看。
    # component/action/severity/reason 皆為封閉詞彙，見檔頭。
    local payload
    payload=$(printf '{"component":"%s","action":"%s","severity":"%s","reason":"%s","text":"%s"}' \
        "$(_json_escape "$comp")" "$(_json_escape "$action")" "$(_json_escape "$severity")" \
        "$(_json_escape "$reason")" "$(_json_escape "[report-mark][$severity] $action $comp: $trimmed")")

    # **URL 不進 argv。** `curl ... "$WEBHOOK"` 會讓 URL 出現在行程清單裡，任何本機
    # 使用者 `ps` 就看得到。改用 `-K -` 從 stdin 餵 curl 設定檔，URL 只存在於管線中。
    # 成功條件**明確定義為 2xx**：不用 `-f`（它把 3xx 當成功，而未跟隨的重導向代表
    # POST 根本沒到目的地）。連不上時 curl 的 %{http_code} 是 000。
    # **退出碼要留下來。** 舊版 `|| code=000` 把 curl 的退出碼與 stderr 一起丟掉，於是
    # 「URL 格式不合法」「主機名解析不到」「連線被拒」「逾時」在 log 裡全是同一行
    # HTTP 000，近萬筆失敗沒有一筆看得出原因。退出碼是封閉的小整數、不含 URL，可以進
    # journal；stderr 仍然不收——`Could not resolve host: …` 會把主機名寫進多人可讀的 log。
    local code rc=0
    code="$(printf 'url = "%s"\n' "$WEBHOOK" | curl -sS -o /dev/null -w '%{http_code}' \
        --connect-timeout "$NOTIFY_CONNECT_TIMEOUT" --max-time "$NOTIFY_MAX_TIME" \
        -K - -X POST -H 'Content-Type: application/json' \
        --data-binary "$payload" 2>/dev/null)" || rc=$?
    [ "$rc" -eq 0 ] || code=000
    case "$code" in
        2??) NOTIFY_SENT=yes ;;
        *)
            NOTIFY_OK=no
            # **事件本身照記，但「已通知」不記。** 兩者分開才對：事件是觀測到的事實，
            # 通知是投遞結果。把投遞失敗記成已通知，會讓 30 分鐘的提醒節流從一則
            # 根本沒送達的通知開始計時——事故於是靜默到下一個提醒週期。
            # 重送不會形成風暴：端點掛著時每一輪都失敗、什麼都沒送出，端點恢復後
            # 只會送出一則，之後 last_notified 前進、去重照常生效。
            log "webhook 投遞失敗（HTTP ${code:-000} curl_rc=${rc}$(_curl_rc_hint "$rc")；事件已記錄，下一輪重試投遞）"
            ;;
    esac
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
            notify "$comp" RESOLVED RESOLVED "已恢復，本次事件持續 ${dur}s、共 ${st_count} 次失敗觀測" "$reason"
            if [ "$NOTIFY_OK" = yes ]; then
                rm -f "$STATE_DIR/$comp.state" 2>/dev/null
                emit "$status" resolved RESOLVED CLOSED "$reason" "$NOTIFY_SENT" "$comp"
            else
                # 刪掉狀態檔＝「已恢復」永遠不會送達，而操作者最後看到的是 FIRING。
                # 保留事件，下一輪仍為健康時重試 RESOLVED。
                write_state "$comp" FIRING "$st_severity" "$st_first_seen" "$st_last_notified" "$obs" "$st_count" "$st_opened_sent"
                emit "$status" resolve_retry "${st_severity:-WARNING}" FIRING "$reason" no "$comp"
            fi
        else
            [ "$new_observation" = yes ] && write_state "$comp" CLOSED "" "$st_first_seen" "$st_last_notified" "$obs" 0 yes
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
    # 第二個條件是重試：事件已記錄但開場通知從未送達。**必須以 FIRING 重送而不是
    # 讓它掉進提醒分支**——否則操作者收到的第一則是「仍未恢復」，而他從沒收到過
    # 「開始了」。
    if [ "$st_state" = CLOSED ] || [ "$st_opened_sent" = no ]; then
        notify "$comp" FIRING "$severity" "$detail" "$reason"
        local ffirst="$st_first_seen" fcount=$(( st_count + 1 ))
        if [ "$st_state" = CLOSED ]; then
            # CLOSED 的狀態檔可能帶著上一個事件的 first_seen，不能沿用
            ffirst="$now_epoch"; fcount=1
        fi
        if [ "$NOTIFY_OK" = yes ]; then
            write_state "$comp" FIRING "$severity" "$ffirst" "$now_epoch" "$obs" "$fcount" yes
        else
            write_state "$comp" FIRING "$severity" "$ffirst" 0 "$obs" "$fcount" no
        fi
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
        notify "$comp" ESCALATED CRITICAL "$detail" "$reason"
        if [ "$NOTIFY_OK" = yes ]; then
            write_state "$comp" FIRING CRITICAL "$st_first_seen" "$now_epoch" "$obs" "$count" "$st_opened_sent"
        else
            # 升級沒送到卻把 severity 記成 CRITICAL，下一輪的升級條件就不再成立，
            # 「惡化了」這件事於是永遠不會再嘗試送出。停在 WARNING 才能重試。
            write_state "$comp" FIRING WARNING "$st_first_seen" "$st_last_notified" "$obs" "$count" "$st_opened_sent"
        fi
        emit "$status" escalated CRITICAL FIRING "$reason" "$NOTIFY_SENT" "$comp"
        return 0
    fi

    local since_notify=$(( now_epoch - st_last_notified ))
    # 牆鐘回跳（WSL 休眠喚醒、NTP 校正）或狀態檔損毀成未來時間戳，會讓這個差值變成
    # 負數，而負數永遠小於門檻 ⇒ 提醒永遠不觸發、事件無聲卡住。fail-open：當成到期。
    [ "$since_notify" -lt 0 ] && since_notify="$REMINDER_SECONDS"
    if [ "$since_notify" -ge "$REMINDER_SECONDS" ]; then
        local dur=$(( now_epoch - st_first_seen ))
        notify "$comp" REMINDER "$eff_severity" "仍未恢復，已持續 ${dur}s、共 ${count} 次失敗觀測（${detail}）" "$reason"
        local rnotify="$st_last_notified"
        [ "$NOTIFY_OK" = yes ] && rnotify="$now_epoch"
        write_state "$comp" FIRING "$eff_severity" "$st_first_seen" "$rnotify" "$obs" "$count" "$st_opened_sent"
        emit "$status" reminder "$eff_severity" FIRING "$reason" "$NOTIFY_SENT" "$comp"
    else
        write_state "$comp" FIRING "$eff_severity" "$st_first_seen" "$st_last_notified" "$obs" "$count" "$st_opened_sent"
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

# ── 觀測快照：一次查詢，一次解析 ──────────────────────────────────────
# **初版每個屬性各發一次 `systemctl show`，那正是 2026-08-20 三次假 FIRING 的成因。**
# 兩次查詢之間相隔數毫秒，若剛好跨越 P4 的一次 invocation 邊界，就會拼出
# `ActiveState=inactive` ＋ `ExecMainExitTimestampMonotonic=0` 這個**在單一一致快照
# 中不可能出現的組合**（該 unit 有 timer 引用、不會被 GC，idle 時必定有完成時戳）。
# P5 於是把它讀成「沒有完成觀測」→ 退回快取（已落後一個週期）→ age 跳過信任上限
# → observation_missing → FIRING；下一輪讀到正常值 → age=0 → RESOLVED。
#
# 隔離實驗（user-scope oneshot ＋ timer，804 次取樣）：
#   一次 `show -p A -p B`      → 矛盾 0/804  = 0.00%
#   兩次獨立 `show`            → 矛盾 3/804  = 0.37%
# 生產實測 3 次 FIRING ÷ 約 660 個 P5 週期 = 0.45%，同一量級。
#
# **解析刻意不用 `source`／`eval`**：值來自 systemd，`Result` 之類的欄位理論上可含
# 任意字元。逐行切第一個 `=`，只認白名單鍵，其餘一律忽略。
_read_service_snapshot() {
    local out line key val
    svc_load=; probe_result=; probe_status=
    probe_mono=; probe_state=; probe_start_mono=
    out="$(systemctl show "$PROBE_UNIT" \
        -p LoadState -p ActiveState -p Result -p ExecMainStatus \
        -p ExecMainExitTimestampMonotonic -p InactiveExitTimestampMonotonic \
        2>/dev/null)" || return 1
    while IFS= read -r line; do
        key="${line%%=*}"
        val="${line#*=}"
        case "$key" in
            LoadState)                      svc_load="$val" ;;
            ActiveState)                    probe_state="$val" ;;
            Result)                         probe_result="$val" ;;
            ExecMainStatus)                 probe_status="$val" ;;
            ExecMainExitTimestampMonotonic) probe_mono="$val" ;;
            InactiveExitTimestampMonotonic) probe_start_mono="$val" ;;
        esac
    done <<< "$out"
    return 0
}

# 快照是否自洽。**只判「不可能同時成立」的組合，不判健康。**
# 回傳 0＝自洽，1＝矛盾。
_snapshot_consistent() {
    local st="$1" exit_m="$2" start_m="$3" status="$4"
    # 數值欄位必須是數字（空字串／字母／負號都算矛盾）
    case "$exit_m"  in ''|*[!0-9]*) return 1 ;; esac
    case "$start_m" in ''|*[!0-9]*) return 1 ;; esac
    case "$status"  in ''|*[!0-9-]*) return 1 ;; esac
    case "$st" in
        inactive|failed)
            # 已經啟動過（start>0）卻沒有完成時戳，而且不在執行中 ⇒ 讀數跨越邊界。
            # **從未跑過（start=0 且 exit=0）是自洽的**，交給既有的 bootstrap 分類。
            [ "$start_m" -gt 0 ] && [ "$exit_m" -eq 0 ] && return 1
            # 完成早於啟動 ⇒ 兩個欄位來自不同 invocation
            [ "$exit_m" -gt 0 ] && [ "$exit_m" -lt "$start_m" ] && return 1
            ;;
    esac
    return 0
}

snapshot_state=stable
snapshot_retries=0
_read_service_snapshot || query_failed=yes
if [ "$query_failed" = no ] && [ "$svc_load" = loaded ]; then
    while ! _snapshot_consistent "$probe_state" "$probe_mono" "$probe_start_mono" "$probe_status"; do
        if [ "$snapshot_retries" -ge "$SNAPSHOT_RETRIES" ]; then
            snapshot_state=unstable
            break
        fi
        snapshot_retries=$(( snapshot_retries + 1 ))
        sleep "$SNAPSHOT_RETRY_DELAY" 2>/dev/null || true
        _read_service_snapshot || { query_failed=yes; break; }
        snapshot_state=retried
    done
    # 重讀後仍矛盾才算 unstable；重讀成功則保留 retried
    if [ "$snapshot_state" = retried ] \
       && ! _snapshot_consistent "$probe_state" "$probe_mono" "$probe_start_mono" "$probe_status"; then
        snapshot_state=unstable
    fi
fi

# timer 的兩個屬性同樣一次取。與 service 分開是刻意的——不變量講的是「同一筆
# service 觀測要自洽」，timer 是另一個對象。
timer_load=; timer_enter_mono=
_timer_out="$(systemctl show "$TIMER_UNIT" -p LoadState -p ActiveEnterTimestampMonotonic 2>/dev/null)" || query_failed=yes
while IFS= read -r _tl; do
    case "${_tl%%=*}" in
        LoadState)                       timer_load="${_tl#*=}" ;;
        ActiveEnterTimestampMonotonic)   timer_enter_mono="${_tl#*=}" ;;
    esac
done <<< "$_timer_out"
timer_enabled="$(systemctl is-enabled "$TIMER_UNIT" 2>/dev/null || true)"
timer_active="$(systemctl is-active "$TIMER_UNIT" 2>/dev/null || true)"
# **探針此刻是否正在執行。** systemd 在 oneshot 啟動時把 ExecMainExitTimestamp* 歸零，
# 直到執行結束才寫入——所以在執行中讀取必然拿到 0。實測（2026-08-19 生產）：
# ExecStart 期間 ActiveState=activating、SubState=start、
# ExecMainExitTimestampMonotonic=0。少了這個判別，P5 會把「這一輪還沒有結果」
# 誤讀成「沒有任何觀測」。
probe_running=no
case "$probe_state" in activating|active|reloading|deactivating) probe_running=yes ;; esac
# 當前這一次呼叫是何時開始的。用來判斷「執行太久＝探針卡住」，門檻由 P4 契約推導。
case "$probe_start_mono" in ''|*[!0-9]*) probe_start_mono=0 ;; esac

# ── in-flight 邊界的有界重讀（2026-08-21）────────────────────────────────────
# 這裡處理的狀態**完全自洽**，所以上面 #223 的矛盾偵測看不到它：P4 正好在
# running→finished 的轉換點上，ExecMain* 已歸零而新值還沒寫入。生產實測 14 次
# 假 FIRING 中有 13 次，「最近一次 P4 完成」的實際年齡是 0s——P4 剛在同一秒
# 成功完成，卻因為快取年齡越過信任窗而被判成「期間至少漏讀一輪」。
#
# **只在拿得到自洽的完成觀測時才改變結論**；否則原封不動還原，走原本的信任窗
# 路徑。真實故障因此零影響：2026-08-20 中斷期間 P4 每輪執行 46s，重讀仍是
# in-flight，照舊 FIRING。
#
# 重讀沿用 `_read_service_snapshot`（單次取六屬性），不新增任何 `systemctl show`
# 呼叫點——`test_handler_makes_one_snapshot_query_per_unit` 靜態釘住那件事。
_inflight_rereads=0
if [ "$probe_mono" -eq 0 ] && [ "$probe_running" = yes ] \
   && [ "$snapshot_state" = stable ] && [ "$query_failed" = no ] \
   && [ "$svc_load" = loaded ] && [ "$INFLIGHT_REREADS" -gt 0 ]; then
    # 還原點：重讀沒收穫時必須讓後續判斷看到與現在**完全相同**的快照。
    _snap_state="$probe_state"; _snap_mono="$probe_mono"
    _snap_start="$probe_start_mono"; _snap_status="$probe_status"
    while [ "$_inflight_rereads" -lt "$INFLIGHT_REREADS" ]; do
        _inflight_rereads=$(( _inflight_rereads + 1 ))
        sleep "$INFLIGHT_REREAD_DELAY" 2>/dev/null || true
        _read_service_snapshot || break
        case "$probe_mono"       in ''|*[!0-9]*) probe_mono=0 ;; esac
        case "$probe_start_mono" in ''|*[!0-9]*) probe_start_mono=0 ;; esac
        case "$probe_status"     in ''|*[!0-9]*) probe_status=-1 ;; esac
        [ "$probe_mono" -gt 0 ] \
            && _snapshot_consistent "$probe_state" "$probe_mono" \
                                    "$probe_start_mono" "$probe_status" \
            && break
    done
    if [ "$probe_mono" -eq 0 ] \
       || ! _snapshot_consistent "$probe_state" "$probe_mono" \
                                 "$probe_start_mono" "$probe_status"; then
        probe_state="$_snap_state"; probe_mono="$_snap_mono"
        probe_start_mono="$_snap_start"; probe_status="$_snap_status"
    fi
    # 快照可能已經換了一筆，`probe_running` 必須跟著重算（判別式與上面同一份）
    probe_running=no
    case "$probe_state" in activating|active|reloading|deactivating) probe_running=yes ;; esac
    [ "$probe_running" = yes ] && EMIT_CURRENT_PROBE=in_flight || EMIT_CURRENT_PROBE=idle
fi

# 上一筆已完成的觀測（P5 自己的記憶；systemd 在探針執行中不提供它）
load_obs_cache
_last_label() {
    case "$1" in 0|3) echo ok ;; 1|2) echo fail ;; 4) echo tooling ;; *) echo none ;; esac
}
EMIT_LAST_COMPLETED="$(_last_label "$obs_status")"
if [ "$obs_mono" -gt 0 ]; then
    EMIT_LAST_AGE=$(( (now_mono_us - obs_mono) / 1000000 ))
    [ "$EMIT_LAST_AGE" -lt 0 ] && EMIT_LAST_AGE=0
fi
[ "$probe_running" = yes ] && EMIT_CURRENT_PROBE=in_flight

# 全部數值欄位驗證（外部輸入，可能是空字串、字母或負數）
case "$probe_status"    in ''|*[!0-9]*) probe_status=-1 ;; esac
case "$probe_mono"      in ''|*[!0-9]*) probe_mono=0 ;; esac
case "$timer_enter_mono" in ''|*[!0-9]*) timer_enter_mono=0 ;; esac

# ── 分類訊號源 ────────────────────────────────────────────────────────────
# 這一段的唯一職責是回答「我現在看得見嗎」，**不看服務健康與否**。
signal=ok
sig_status=ok        # 對外封閉詞彙：ok|bootstrap|in_flight|monitor_blind（內部 signal 另有 cached）
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
elif [ "$probe_mono" -eq 0 ] && [ "$probe_running" = yes ] \
     && [ "$probe_start_mono" -gt 0 ] \
     && [ $(( (now_mono_us - probe_start_mono) / 1000000 )) -gt "$PROBE_MAX_INFLIGHT" ]; then
    # 探針執行超過契約上限（3×5s ＋ 2×15s ＋ L3 2×5s = 55s，unit 硬上限 90s）。
    # systemd 應該已經砍掉它卻沒有 ⇒ 探針本身卡住，是監控故障不是服務故障。
    inflight_age=$(( (now_mono_us - probe_start_mono) / 1000000 ))
    signal=blind; sig_status=monitor_blind; sig_severity=CRITICAL; sig_reason=probe_stuck
    sig_detail="探針已執行 ${inflight_age}s，超過上限 ${PROBE_MAX_INFLIGHT}s（契約最壞 55s、unit 上限 90s）"
elif { { [ "$probe_mono" -eq 0 ] && [ "$probe_running" = yes ]; } \
        || [ "$snapshot_state" = unstable ]; } && [ "$obs_mono" -gt 0 ]; then
    # 兩種「本輪沒有可消費的新觀測」共用同一套信任窗判斷：
    #   (a) 探針正在執行（#216 修的那條）
    #   (b) 快照跨越 invocation 邊界、重讀後仍矛盾（2026-08-20 的假 FIRING）
    # **刻意不另開一條平行路徑**——信任窗的語意完全相同，複製只會讓兩邊漂移。
    # ── 這是 2026-08-20 中斷修掉的那一條 ────────────────────────────────
    # 探針正在執行，但**我們記得上一次的結論**。
    #
    # `in_flight` 是觀測的**生命週期**，不是健康結論。舊版把它當結論用
    # （web action=skip），於是「越是中斷、探針越慢、P5 越容易只看到 in_flight」——
    # 中斷期間 P4 每輪執行 46 秒（佔 P5 週期 35%），連續兩次撞上就整場靜默。
    #
    # 這裡改為沿用上一筆已完成的觀測來判 web，但**仍不解除 monitor 事件**
    # （「正在跑」不等於「有一筆新的完成觀測」）。
    obs_age=$(( (now_mono_us - obs_mono) / 1000000 ))
    [ "$obs_age" -lt 0 ] && obs_age=0
    if [ "$obs_age" -gt "$BLIND_CRITICAL_SECONDS" ]; then
        signal=blind; sig_status=monitor_blind; sig_severity=CRITICAL; sig_reason=observation_stale
        sig_detail="探針執行中，且上一筆完成的觀測已 ${obs_age}s（升級門檻 ${BLIND_CRITICAL_SECONDS}s）"
    elif [ "$obs_age" -gt "$OBS_TRUST_SECONDS" ]; then
        # **漏讀，不是過期。** 快取比「一個 P4 週期＋一次執行」還舊 ⇒ P4 至少完成過
        # 一輪而我們沒讀到，那筆結果是什麼並不知道。此時沿用舊結論等於用一筆可能
        # 已被推翻的觀測代替現實——2026-08-20 的靜默中斷就是這樣發生的。
        signal=blind; sig_status=monitor_blind; sig_severity=WARNING; sig_reason=observation_missed
        sig_detail="探針執行中，且上一筆完成的觀測已 ${obs_age}s > 信任上限 ${OBS_TRUST_SECONDS}s ⇒ 期間至少漏讀一輪"
    else
        # **舊的結論仍然有效**：不新鮮到過期，就不該被「有新的一輪正在跑」抹掉。
        signal=cached
        if [ "$snapshot_state" = unstable ]; then
            sig_status=in_flight; sig_reason=snapshot_unstable
            sig_detail="systemd 快照跨越 invocation 邊界（重讀 ${snapshot_retries} 次後仍矛盾）；沿用 ${obs_age}s 前已完成的觀測（status=$(_last_label "$obs_status")）"
        else
            sig_status=in_flight; sig_reason=probe_in_flight
            sig_detail="探針執行中；沿用 ${obs_age}s 前已完成的觀測（status=$(_last_label "$obs_status")）"
        fi
    fi
elif [ "$probe_mono" -eq 0 ] && [ "$probe_running" = yes ]; then
    # 探針正在執行：ExecMain* 描述的是一次**進行中**的呼叫，不是可消費的觀測。
    #
    # 2026-08-19 部署當天實測到的真實故障：P4 與 P5 的 timer 週期都是 2 分鐘，
    # enable 的時機讓它們落在**同一秒**觸發（15:34:22、15:36:28 皆同秒），於是
    # 勝負由次秒級順序決定——P5 讀在 P4 完成之前就拿到 0、判成 observation_missing
    # 並開 CRITICAL 事件；下一輪讀在完成之後就 RESOLVED。結果是 FIRING↔RESOLVED
    # **震盪**，而那正好重現 P5 存在要消除的「一次事件產生大量訊息」。
    #
    # 判為 in_flight 而非 blind，且**既不開事件也不解除**：這一輪我們對 web
    # 一無所知（所以 web 跳過），但監控本身顯然活著（它正在跑）——只是「正在跑」
    # 不等於「有一筆完成的觀測」，所以也不足以解除進行中的失明事件。
    # 兩個 timer 的相位會互相漂移（P4 實測間隔 125–138s、P5 為 120s＋抖動），
    # 所以連續多輪都撞上的機率極低，真正的觀測會在一兩輪內到來。
    signal=in_flight; sig_status=in_flight; sig_reason=probe_in_flight
    sig_detail="探針正在執行中（ActiveState=$probe_state），本輪尚無完成的觀測"
elif [ "$snapshot_state" = unstable ]; then
    # 快照持續矛盾**且沒有可沿用的完成觀測**。這確實看不見，但原因與「真的過期」
    # 不同——要讓 operator 分得出來，否則排查方向會完全走錯。
    signal=blind; sig_status=monitor_blind; sig_severity=WARNING; sig_reason=snapshot_unstable
    sig_detail="systemd 快照自相矛盾（state=${probe_state}, exit=${probe_mono}, start=${probe_start_mono}），重讀 ${snapshot_retries} 次後仍不一致，且無可沿用的完成觀測"
elif [ "$probe_mono" -eq 0 ]; then
    # 沒有任何觀測，而且探針不在執行中。timer 本身健康，所以只可能是
    # 「還沒跑第一輪」或「觀測消失了」。
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

# ── 輸出欄位：描述本輪**實際使用**的那一筆觀測 ────────────────────────────
# **位置很重要，必須在 monitor 分派之前。** 第一版把它放在 web 分派裡重算，於是
# monitor 那一行仍印載入快取當下的舊值——同一個缺陷只修好一半。2026-08-20 部署後
# 實測到 `component=web last_completed_age=58` 而 `component=monitor` 是 179，
# 兩行描述同一輪卻互相矛盾。
#
# 而最初的缺陷是：EMIT_LAST_* 在載入快取當下就算好，反映「上一輪消費的那筆」，
# 年齡累積成「上一輪間隔 ＋ 那筆當時的年齡」（實測 239s／255s，而快取其實完全同步、
# 真實年齡只有 83.6s）。判斷邏輯不受影響，但那個數字逼近 OBS_TRUST=240 的門檻值，
# 會讓人以為觀測已四分鐘沒更新——**會讓人誤判的欄位本身就是缺陷**。
if [ "$signal" = ok ]; then
    _used_status="$probe_status"; _used_obs="$probe_mono"
else
    # cached／bootstrap／blind 一律以記憶中的那筆為準（沒有就是 none）
    _used_status="$obs_status"; _used_obs="$obs_mono"
fi
SNAPSHOT_STATE_OUT="$snapshot_state"
SNAPSHOT_RETRIES_OUT="$snapshot_retries"
# 這一輪的結論是從 systemd 現讀來的，還是沿用記憶中的那一筆
if [ "$signal" = ok ]; then EMIT_OBS_SOURCE=live; else EMIT_OBS_SOURCE=cache; fi
EMIT_LAST_COMPLETED="$(_last_label "$_used_status")"
if [ "$_used_obs" -gt 0 ]; then
    EMIT_LAST_AGE=$(( (now_mono_us - _used_obs) / 1000000 ))
    [ "$EMIT_LAST_AGE" -lt 0 ] && EMIT_LAST_AGE=0
else
    EMIT_LAST_AGE=-
fi

# ── 元件一：監控本身 ──────────────────────────────────────────────────────
case "$signal" in
    ok)        run_state_machine "$MONITOR_COMPONENT" healthy ""             monitor_ok           "$probe_mono" ok            "" ;;
    bootstrap) run_state_machine "$MONITOR_COMPONENT" hold    ""             "$sig_reason"        "$probe_mono" bootstrap     "$sig_detail" ;;
    in_flight) run_state_machine "$MONITOR_COMPONENT" hold    ""             "$sig_reason"        "$probe_mono" in_flight     "$sig_detail" ;;
    cached)    run_state_machine "$MONITOR_COMPONENT" hold    ""             "$sig_reason"        "$probe_mono" in_flight     "$sig_detail" ;;
    blind)     run_state_machine "$MONITOR_COMPONENT" failing "$sig_severity" "$sig_reason"       "$probe_mono" monitor_blind "$sig_detail" ;;
esac

# ── 元件二：web 服務 ──────────────────────────────────────────────────────
# **訊號不可信時完全不碰 web 的狀態檔。** 讓進行中的 web 事件維持 FIRING：
# 「我看不見了」不是「已經好了」，把它當成恢復會在真正的中斷中途送出 RESOLVED。
if [ "$signal" != ok ] && [ "$signal" != cached ]; then
    load_state "$COMPONENT"
    emit "$sig_status" skip none "$st_state" "signal_$sig_reason" no "$COMPONENT"
    exit "$EXIT_OK"
fi

# 判 web 用的是哪一筆觀測：signal=ok 用剛讀到的，signal=cached 用記憶中的上一筆。
# **兩者都帶各自的 monotonic 當游標**，所以同一筆觀測不會在多輪 in-flight 中被重複計數。
if [ "$signal" = cached ]; then
    web_status="$obs_status"; web_obs="$obs_mono"; web_result="$obs_result"
    web_detail_suffix="（沿用執行中前一筆完成的觀測）"
else
    web_status="$probe_status"; web_obs="$probe_mono"; web_result="$probe_result"
    web_detail_suffix=""
    # 只有真正讀到一筆完成的觀測時才更新記憶
    save_obs_cache "$probe_mono" "$probe_status" "$probe_result"
fi


# P4 的退出碼契約：0 健康／3 寬限（視為健康）／1,2 服務故障／4 探針自身錯誤／
# （5 已退役：PR-M 前是「問答相依的 claude CLI 不在 web unit 的 PATH 上」；收到它落下面的未知退出碼）
# 6 服務降級（/healthz 正常但物件儲存 R2 連不上）
# 7 服務提醒（/healthz 正常但 DeepSeek 餘額低於門檻、尚未停擺：/healthz/llm 回 503 llm_low）
# 8 服務降級（/healthz 正常但 DeepSeek 帳號不可用或判斷不出來：/healthz/llm 回 low 以外的 503）
# 多項同時成立時探針回 8→6→7 中最前面那一個（理由見 check_web_health.sh 的 L3 段）。
case "$web_status" in
    0|3) run_state_machine "$COMPONENT" healthy "" healthy "$web_obs" ok "" ;;
    1|2) run_state_machine "$COMPONENT" failing CRITICAL "probe_exit_$web_status" "$web_obs" web_incident \
             "探針回報失敗（exit=$web_status result=$web_result）${web_detail_suffix}" ;;
    4)   run_state_machine "$COMPONENT" failing WARNING "probe_exit_4" "$web_obs" web_incident \
             "探針自己不能執行（exit=4 result=$web_result）——是「我不知道」不是「壞了」${web_detail_suffix}" ;;
    # WARNING 而非 CRITICAL：壞的只有原檔下載與 PDF 檢視（r2 模式缺 key 即 503 不回退），
    # 檢索、問答、雷達、簡報都不受影響；也同樣不會自己好。
    6)   run_state_machine "$COMPONENT" failing WARNING "probe_exit_6" "$web_obs" web_incident \
             "服務降級：健康端點正常，但物件儲存（R2）連不上，原檔下載與 PDF 檢視會失敗（exit=6 result=$web_result）${web_detail_suffix}" ;;
    # WARNING：餘額低於門檻、問答與批次都還能跑，但不會自己好（要儲值），而且可能持續好幾天。
    7)   run_state_machine "$COMPONENT" failing WARNING "probe_exit_7" "$web_obs" web_incident \
             "DeepSeek 餘額低於門檻（尚未停擺），請於 3 個工作天內儲值（exit=7 result=$web_result；處置見 docs/production_resilience.md）${web_detail_suffix}" ;;
    # CRITICAL：問答每題失敗、sync 的 LLM 段整批中止，沒有備援（claude CLI 已於 PR-M 移除）。檢索、閱讀、雷達與
    # 既有簡報的讀取不需要 LLM，但問答是主要功能。與 7 分開、而且嚴重度不同，是為了讓「餘額低」FIRING
    # 好幾天的期間轉成真停擺時看得出來：同一個 web 事件 WARNING → CRITICAL 會立刻送 ESCALATED
    # （run_state_machine 的升級分支），不必等 30 分鐘的提醒。判斷不出來（indeterminate、本體讀不懂）也算這裡。
    8)   run_state_machine "$COMPONENT" failing CRITICAL "probe_exit_8" "$web_obs" web_incident \
             "LLM 帳號不可用（餘額用罄／認證失敗／連不上），問答與批次 LLM 段停擺；檢索、閱讀、雷達正常（exit=8 result=$web_result；判斷不出來也歸這裡，處置見 docs/production_resilience.md）${web_detail_suffix}" ;;
    *)   run_state_machine "$COMPONENT" failing WARNING "probe_exit_unknown" "$web_obs" web_incident \
             "探針回報未知退出碼（exit=$web_status result=$web_result）${web_detail_suffix}" ;;
esac
exit "$EXIT_OK"
