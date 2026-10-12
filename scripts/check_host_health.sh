#!/usr/bin/env bash
# 主機健康探針（P4 式）——「服務都還活著，但主機快撐不住了」的那一段：磁碟快滿（PostgreSQL 寫不進去
# 之前）、可用記憶體快用完（OOM killer 挑中 web 之前）、記憶體或 I/O 的 PSI 長時間卡在高檔（使用者覺得
# 卡、平均 CPU 卻很閒）。連續數值由 scripts/collect_resource_usage.py 每 60 秒寫進監控 spool（管理頁的
# 主機頁）；這一支只在越線時回報，讓 P5（report-mark-host-incident）開事件。
#
# 職責邊界與 check_container_health.sh 相同：只回報事實，事件狀態與通知屬於 P5；唯一的跨執行狀態是
# 每個檢查項目的連續越線次數（scripts/_health_streak.sh）。刻意不用 Python／uv／.venv，只讀 /proc 與 df。
#
# ── 門檻與 tier（都可由 /etc/default/report-mark-sync 覆寫）────────────────────
# **這些是保守的初值，還沒有越線事件的實測分布**（2026-10-06 辦公室主機：根檔案系統 26%、MemAvailable
# 約 45%、memory／io PSI full avg60 0.00／0.07）。事件投影與 L3 的主機觀測累積一段時間後依資料調。
#   磁碟  important   任一路徑所在檔案系統使用率 ≥ HOST_DISK_MAX_PCT（90）。ext4 預設保留 5% 給 root，
#                     一般使用者（web、PostgreSQL 容器的 bind mount、sync）在約 95% 就拿到 ENOSPC；90 留
#                     5 個百分點、在 1 TB 的根檔案系統上約 50 GB，夠撐過一兩天的回填與備份再處理。
#                     磁碟不會自己好、也不會抖動，連續 2 輪（約 2–4 分鐘）就開。
#   記憶體 supporting MemAvailable／MemTotal < HOST_MEM_MIN_AVAIL_PCT（5）。web 常駐的 BGE-M3 約 2–3 GB，
#                     20 GB 的主機剩 5%（約 1 GB）已經在 OOM 邊緣；sync 嵌入段的尖峰會短暫掉下去，所以
#                     要連續 3 輪（約 4–6 分鐘）。
#   PSI   supporting  memory full avg60 ≥ HOST_PSI_MEMORY_FULL_MAX（10）：過去一分鐘有 6 秒以上「所有非閒置
#                     工作同時卡在記憶體」＝已經在換頁顛簸。io full avg60 ≥ HOST_PSI_IO_FULL_MAX（30）：每分鐘
#                     18 秒以上整機卡在 I/O（`make ingest-lowio` 存在就是因為大量入庫曾把 I/O 拖垮）。
#                     avg60 本身已平滑一分鐘，再要連續 3 輪，備份與 sync 的短尖峰不會開事件。
#                     核心沒開 PSI（/proc/pressure 不存在）時這兩項略過，不算故障。
# 主機資源不設 critical：真的拖垮服務時，web／container 那兩組會先以 CRITICAL 開事件；這一組是預警。
#
# 退出碼契約（與容器探針相同）：
#   0 都在門檻內    3 確認期（hold）    4 判不出來（df／meminfo 讀不到，連續 2 輪才回）
#   9 有項目確認越線（WARNING）
set -u

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
# shellcheck source=_health_streak.sh
. "$SCRIPT_DIR/_health_streak.sh"

HOST_HEALTH_PATHS="${HOST_HEALTH_PATHS:-/ ${REPORT_MARK_ROOT:-$ROOT}}"
HOST_DISK_MAX_PCT="${HOST_DISK_MAX_PCT:-90}"
HOST_MEM_MIN_AVAIL_PCT="${HOST_MEM_MIN_AVAIL_PCT:-5}"
HOST_PSI_MEMORY_FULL_MAX="${HOST_PSI_MEMORY_FULL_MAX:-10}"
HOST_PSI_IO_FULL_MAX="${HOST_PSI_IO_FULL_MAX:-30}"
HOST_PROC_ROOT="${HOST_PROC_ROOT:-/proc}"   # 測試用：指到假的 /proc 樹
HOST_DF_TIMEOUT="${HOST_DF_TIMEOUT:-10}"    # df 卡在壞掉的掛載上時的上限
STREAK_FILE="${HEALTH_STREAK_DIR:-$ROOT/data/.health-streaks}/host.state"

EXIT_OK=0
EXIT_PENDING=3
EXIT_TOOLING=4
EXIT_DEGRADED=9

emit() {
    # status disk mem psi_mem psi_io breached pending reason
    printf 'ts=%s component=host probe=proc status=%s disk_max_pct=%s mem_avail_pct=%s psi_memory_full_avg60=%s psi_io_full_avg60=%s breached=%s pending=%s reason=%s\n' \
        "$(date -Iseconds)" "$1" "$2" "$3" "$4" "$5" "${6:--}" "${7:--}" "$8"
}

# 門檻寫錯是設定錯誤：判不出來（不進確認期，立刻讓人看到）
for v in HOST_DISK_MAX_PCT HOST_MEM_MIN_AVAIL_PCT HOST_DF_TIMEOUT; do
    case "${!v}" in ''|*[!0-9]*) emit tooling - - - - - - "bad_knob_$v"; exit "$EXIT_TOOLING" ;; esac
done
for v in HOST_PSI_MEMORY_FULL_MAX HOST_PSI_IO_FULL_MAX; do
    case "${!v}" in ''|.|*[!0-9.]*|*.*.*) emit tooling - - - - - - "bad_knob_$v"; exit "$EXIT_TOOLING" ;; esac
done

_df() {
    if command -v timeout >/dev/null 2>&1; then timeout "$HOST_DF_TIMEOUT" df -P -- "$1"; else df -P -- "$1"; fi
}

tooling=""
declare -A breach=()   # 檢查項目 → 說明（這一輪越線的）
checked=" "            # 這一輪真的判得出來的檢查項目

# ── 磁碟 ──────────────────────────────────────────────────────────────────
disk_max=0
seen_mounts=" "
for p in $HOST_HEALTH_PATHS; do
    [ -e "$p" ] || continue
    read -r pct mnt <<< "$(_df "$p" 2>/dev/null | awk 'NR==2 {gsub("%","",$5); print $5, $6}')"
    case "${pct:-}" in ''|*[!0-9]*) tooling="df_failed"; echo "check_host_health: df 讀不到 $p" >&2; break ;; esac
    case "$seen_mounts" in *" ${mnt:-$p} "*) continue ;; esac   # 同一個檔案系統只算一次
    seen_mounts="$seen_mounts${mnt:-$p} "
    [ "$pct" -gt "$disk_max" ] && disk_max="$pct"
    if [ "$pct" -ge "$HOST_DISK_MAX_PCT" ]; then
        breach[disk]="${breach[disk]:+${breach[disk]};}disk:${mnt:-$p}:${pct}%"
        echo "check_host_health: $p 所在檔案系統已用 ${pct}%（門檻 ${HOST_DISK_MAX_PCT}%）" >&2
    fi
done
[ -z "$tooling" ] && checked="${checked}disk "

# ── 記憶體 ────────────────────────────────────────────────────────────────
mem_avail_pct="$(awk '/^MemTotal:/ {t=$2} /^MemAvailable:/ {a=$2} END { if (t > 0 && a != "") printf "%d", a * 100 / t }' \
    "$HOST_PROC_ROOT/meminfo" 2>/dev/null)"
case "$mem_avail_pct" in
    ''|*[!0-9]*) mem_avail_pct=-; tooling="${tooling:+$tooling,}meminfo_unreadable"
                 echo "check_host_health: 讀不到 $HOST_PROC_ROOT/meminfo" >&2 ;;
    *) checked="${checked}mem "
       if [ "$mem_avail_pct" -lt "$HOST_MEM_MIN_AVAIL_PCT" ]; then
           breach[mem]="mem_avail:${mem_avail_pct}%"
           echo "check_host_health: 可用記憶體 ${mem_avail_pct}%（門檻 ${HOST_MEM_MIN_AVAIL_PCT}%）" >&2
       fi ;;
esac

# ── PSI（核心沒開 PSI 時檔案不存在：那一項略過，不算故障也不算判不出來）──────
_psi_full60() {
    awk '$1 == "full" { for (i = 2; i <= NF; i++) if ($i ~ /^avg60=/) { sub("avg60=", "", $i); print $i } }' \
        "$HOST_PROC_ROOT/pressure/$1" 2>/dev/null
}
_ge() { awk -v a="$1" -v b="$2" 'BEGIN { exit !(a + 0 >= b + 0) }'; }
psi_mem="$(_psi_full60 memory)"; psi_io="$(_psi_full60 io)"
case "$psi_mem" in ''|*[!0-9.]*) psi_mem= ;; esac
case "$psi_io"  in ''|*[!0-9.]*) psi_io= ;; esac
if [ -n "$psi_mem" ]; then
    checked="${checked}psi_memory "
    if _ge "$psi_mem" "$HOST_PSI_MEMORY_FULL_MAX"; then
        breach[psi_memory]="psi_memory_full_avg60:${psi_mem}"
        echo "check_host_health: memory PSI full avg60=${psi_mem}%（門檻 ${HOST_PSI_MEMORY_FULL_MAX}%）" >&2
    fi
fi
if [ -n "$psi_io" ]; then
    checked="${checked}psi_io "
    if _ge "$psi_io" "$HOST_PSI_IO_FULL_MAX"; then
        breach[psi_io]="psi_io_full_avg60:${psi_io}"
        echo "check_host_health: io PSI full avg60=${psi_io}%（門檻 ${HOST_PSI_IO_FULL_MAX}%）" >&2
    fi
fi

# ── 依 tier 去抖 ─────────────────────────────────────────────────────────────
declare -A TIER=([disk]=important [mem]=supporting [psi_memory]=supporting [psi_io]=supporting)
now="$(date +%s)"
streak_load "$STREAK_FILE"
for k in disk mem psi_memory psi_io; do
    case "$checked" in
        *" $k "*) if [ -n "${breach[$k]:-}" ]; then streak_bump "$k" "$now"; else streak_clear "$k"; fi ;;
        # 判不出來的項目不碰它的計數（不知道就不知道）
    esac
done
if [ -n "$tooling" ]; then streak_bump tooling "$now"; else streak_clear tooling; fi
streak_save "$STREAK_FILE"
note=""
[ "$STREAK_SAVE_OK" = yes ] || note="+streak_state_unwritable"

_confirmed() {  # key tier
    [ "$STREAK_SAVE_OK" = yes ] || return 0    # 記不住次數就每筆都算確認（寧可多吵也不靜默）
    [ "${STREAK_N[$1]:-0}" -ge "$(streak_confirm_count "$2")" ]
}

confirmed=(); pend=()
for k in disk mem psi_memory psi_io; do
    [ -n "${breach[$k]:-}" ] || continue
    if _confirmed "$k" "${TIER[$k]}"; then
        confirmed+=("${breach[$k]}")
    else
        pend+=("$k:${STREAK_N[$k]:-1}/$(streak_confirm_count "${TIER[$k]}")")
    fi
done
_join() { local IFS=,; printf '%s' "$*"; }
breached="$(_join "${confirmed[@]+"${confirmed[@]}"}")"
pending="$(_join "${pend[@]+"${pend[@]}"}")"
fields=("$disk_max" "$mem_avail_pct" "${psi_mem:--}" "${psi_io:--}")

# 判得出來的項目已經確認越線，就照實回報（即使另一項判不出來）
if [ "${#confirmed[@]}" -gt 0 ]; then
    emit degraded "${fields[@]}" "$breached" "$pending" "host_threshold_breached${note}"
    exit "$EXIT_DEGRADED"
fi
if [ -n "$tooling" ]; then
    if _confirmed tooling tooling; then
        emit tooling "${fields[@]}" - "$pending" "${tooling}${note}"
        exit "$EXIT_TOOLING"
    fi
    pending="${pending:+$pending,}tooling:${STREAK_N[tooling]:-1}/$(streak_confirm_count tooling)"
    emit pending "${fields[@]}" - "$pending" "${tooling}_unconfirmed${note}"
    exit "$EXIT_PENDING"
fi
if [ "${#pend[@]}" -gt 0 ]; then
    emit pending "${fields[@]}" - "$pending" "unconfirmed${note}"
    exit "$EXIT_PENDING"
fi
emit ok "${fields[@]}" - - ok
exit "$EXIT_OK"
