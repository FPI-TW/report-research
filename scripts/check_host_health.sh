#!/usr/bin/env bash
# 主機健康探針（P4 式，第五個元件）——每次執行是一次**無狀態**的單點觀測。
#
# 看的是「服務都還活著，但主機快撐不住了」的那一段：磁碟快滿（PostgreSQL 寫不進去之前）、記憶體
# 快用完（OOM killer 挑中 web 之前）、I/O 或記憶體的 PSI 長時間卡在高檔（使用者覺得卡、平均 CPU
# 卻很閒）。數值本身由 scripts/collect_resource_usage.py 持續取樣進 spool／DB；這一支只在越線時
# 回報非零，讓 P5（report-mark-host-incident）開事件。
#
# tier：主機資源不是 critical（真的拖垮服務時 web／container 探針會先以 CRITICAL 開事件），一律回 2，
# P5 那組以 INCIDENT_CONFIRM_EXIT_CODES=2 要求連續多次越線才開 WARNING——單次尖峰（sync 的嵌入段、
# 備份）不吵人。門檻是保守的初值，**不是實測校準過的**：先讓事件投影累積分布，之後依資料調。
#
# 職責邊界：只回報此刻的事實，不保存任何跨執行的狀態。刻意不用 Python／uv／.venv（理由同 P4）；
# 只讀 /proc 與 df。
set -u

HOST_HEALTH_PATHS="${HOST_HEALTH_PATHS:-/ ${REPORT_MARK_ROOT:-/home/kashionz/projects/report-mark}}"
HOST_DISK_MAX_PCT="${HOST_DISK_MAX_PCT:-90}"          # 任一路徑所在檔案系統使用率 ≥ 這個值
HOST_MEM_MIN_AVAIL_PCT="${HOST_MEM_MIN_AVAIL_PCT:-5}" # MemAvailable／MemTotal 低於這個百分比
# PSI 的 avg60（百分比）。`full`＝所有非閒置工作同時卡住的時間比例，比 `some` 更直接代表「整機停頓」。
HOST_PSI_MEMORY_FULL_MAX="${HOST_PSI_MEMORY_FULL_MAX:-10}"
HOST_PSI_IO_FULL_MAX="${HOST_PSI_IO_FULL_MAX:-30}"
HOST_PROC_ROOT="${HOST_PROC_ROOT:-/proc}"   # 測試用：指到假的 /proc 樹

EXIT_OK=0
EXIT_DEGRADED=2   # 有任一項越線（host 那組 P5 連續確認後開 WARNING）
EXIT_TOOLING=4    # 判不出來：讀不到 /proc/meminfo、df 失敗

for v in HOST_DISK_MAX_PCT HOST_MEM_MIN_AVAIL_PCT; do
    case "${!v}" in ''|*[!0-9]*) echo "check_host_health: $v 必須是整數" >&2; exit "$EXIT_TOOLING" ;; esac
done
for v in HOST_PSI_MEMORY_FULL_MAX HOST_PSI_IO_FULL_MAX; do
    case "${!v}" in ''|*[!0-9.]*) echo "check_host_health: $v 必須是數字" >&2; exit "$EXIT_TOOLING" ;; esac
done

emit() {
    printf 'ts=%s component=host probe=proc status=%s disk_max_pct=%s mem_avail_pct=%s psi_memory_full_avg60=%s psi_io_full_avg60=%s reason=%s\n' \
        "$(date -Iseconds)" "$1" "$2" "$3" "$4" "$5" "$6"
}

breaches=()

# ── 磁碟 ──────────────────────────────────────────────────────────────────
disk_max=0
seen_mounts=" "
for p in $HOST_HEALTH_PATHS; do
    [ -e "$p" ] || continue
    read -r pct mnt <<< "$(df -P -- "$p" 2>/dev/null | awk 'NR==2 {gsub("%","",$5); print $5, $6}')"
    case "${pct:-}" in ''|*[!0-9]*) emit tooling - - - - "df_failed"; echo "check_host_health: df 讀不到 $p" >&2; exit "$EXIT_TOOLING" ;; esac
    # 兩個路徑在同一個檔案系統上只算一次（預設的 / 與 repo 根通常就是）
    case "$seen_mounts" in *" ${mnt:-$p} "*) continue ;; esac
    seen_mounts="$seen_mounts${mnt:-$p} "
    [ "$pct" -gt "$disk_max" ] && disk_max="$pct"
    if [ "$pct" -ge "$HOST_DISK_MAX_PCT" ]; then
        breaches+=("disk_${pct}pct")
        echo "check_host_health: $p 所在檔案系統已用 ${pct}%（門檻 ${HOST_DISK_MAX_PCT}%）" >&2
    fi
done

# ── 記憶體 ────────────────────────────────────────────────────────────────
mem_line="$(awk '/^MemTotal:/ {t=$2} /^MemAvailable:/ {a=$2} END { if (t > 0 && a != "") printf "%d", a * 100 / t }' \
    "$HOST_PROC_ROOT/meminfo" 2>/dev/null)"
case "$mem_line" in
    ''|*[!0-9]*) emit tooling "$disk_max" - - - meminfo_unreadable
                 echo "check_host_health: 讀不到 $HOST_PROC_ROOT/meminfo" >&2
                 exit "$EXIT_TOOLING" ;;
esac
mem_avail_pct="$mem_line"
if [ "$mem_avail_pct" -lt "$HOST_MEM_MIN_AVAIL_PCT" ]; then
    breaches+=("mem_avail_${mem_avail_pct}pct")
    echo "check_host_health: 可用記憶體 ${mem_avail_pct}%（門檻 ${HOST_MEM_MIN_AVAIL_PCT}%）" >&2
fi

# ── PSI（核心沒開 PSI 時檔案不存在：判不出來的那一項略過，不算故障）──────────
_psi_full60() {
    awk '$1 == "full" { for (i = 2; i <= NF; i++) if ($i ~ /^avg60=/) { sub("avg60=", "", $i); print $i } }' \
        "$HOST_PROC_ROOT/pressure/$1" 2>/dev/null
}
_gt() { awk -v a="$1" -v b="$2" 'BEGIN { exit !(a + 0 >= b + 0) }'; }
psi_mem="$(_psi_full60 memory)"; psi_io="$(_psi_full60 io)"
if [ -n "$psi_mem" ] && _gt "$psi_mem" "$HOST_PSI_MEMORY_FULL_MAX"; then
    breaches+=("psi_memory_full_${psi_mem}")
    echo "check_host_health: memory PSI full avg60=${psi_mem}%（門檻 ${HOST_PSI_MEMORY_FULL_MAX}%）" >&2
fi
if [ -n "$psi_io" ] && _gt "$psi_io" "$HOST_PSI_IO_FULL_MAX"; then
    breaches+=("psi_io_full_${psi_io}")
    echo "check_host_health: io PSI full avg60=${psi_io}%（門檻 ${HOST_PSI_IO_FULL_MAX}%）" >&2
fi

if [ "${#breaches[@]}" -gt 0 ]; then
    reason="$(IFS=,; printf '%s' "${breaches[*]}")"
    emit degraded "$disk_max" "$mem_avail_pct" "${psi_mem:--}" "${psi_io:--}" "$reason"
    exit "$EXIT_DEGRADED"
fi
emit ok "$disk_max" "$mem_avail_pct" "${psi_mem:--}" "${psi_io:--}" ok
exit "$EXIT_OK"
