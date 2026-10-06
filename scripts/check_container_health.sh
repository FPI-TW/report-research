#!/usr/bin/env bash
# 容器健康探針（P4 式）——Service Catalog 裡 kind=container 的 PostgreSQL、nginx、cloudflared 有沒有在跑。
#
# 為什麼 web 探針與邊緣探針之外還要這一支：
#   - web 探針打 /healthz（只探 DB）。DB 容器停掉時它回報 web 故障，訊息指不到「容器沒在跑」
#   - 邊緣探針從外網打，nginx 停掉時只看得到 502；2026-09-24 deploy-nginx-1 停在 Exited 約 36 小時
#     （見 check_edge_health.sh 檔頭）——直接看容器狀態的這一層更早、也更準
#
# 職責邊界（與 check_web_health.sh 相同）：本檔只回報事實，事件狀態、去重、提醒節奏、恢復判定與 webhook
# 投遞全部屬於 P5（report-mark-container-incident 跑同一支 scripts/incident_handler.sh，只換一組 INCIDENT_*）。
# 唯一的跨執行狀態是「每個檢查項目連續失敗幾次」（依 tier 去抖用，理由與格式見 scripts/_health_streak.sh）。
#
# ── 依 tier 去抖 ─────────────────────────────────────────────────────────────
# tier 與 catalog 一致（deploy/ops/services.prod.toml；tests/test_container_host_probes.py 逐字比對）。
#   critical    第一次確認故障就回 1 → P5 開 CRITICAL。「確認」在單次執行內做完：最多 2 次嘗試、間隔 15 秒，
#               蓋過 `restart: unless-stopped` 與 `make edge-reload` 那幾秒到十幾秒的 restarting 空窗。
#               PostgreSQL、nginx、cloudflared 在 catalog 都是 critical：DB 停了問答與檢索全停；nginx 或
#               隧道停了外網整站連不上（邊緣那組也會開事件，這一組指出是哪個容器）。
#   important   連續 2 輪（約 2–4 分鐘）才回 9 → P5 開 WARNING
#   supporting  連續 3 輪（約 4–6 分鐘）才回 9 → WARNING
#   判不出來    docker CLI 連不上 daemon（Docker Desktop 重啟中約 1–2 分鐘）連續 2 輪才回 4 → WARNING
# 還在確認期（有項目失敗但次數未到）回 3：P5 那組設 INCIDENT_HOLD_EXIT_CODES=3，**既不開也不關**——
# 不會因為一次抖動吵人，也不會在確認期把進行中的事件誤判成已恢復。
#
# 退出碼契約：
#   0 全部在跑    1 critical 容器確認故障（CRITICAL）    3 確認期（hold；unit 宣告 SuccessExitStatus=3）
#   4 判不出來（WARNING）    9 只有 important／supporting 容器確認故障（WARNING）
#
# 刻意不用 Python／uv／.venv（理由同 P4：2026-08-18 的根因是 venv 損毀）。只用 docker CLI／coreutils。
set -u

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
# shellcheck source=_health_streak.sh
. "$SCRIPT_DIR/_health_streak.sh"

# 監控名單：`名稱=容器:tier`，空白分隔。預設值與 catalog 逐字一致（bash 不解析 TOML，由測試守住兩邊不漂移）。
CONTAINER_HEALTH_TARGETS="${CONTAINER_HEALTH_TARGETS:-postgres=report-mark-postgres:critical nginx=deploy-nginx-1:critical cloudflared=deploy-cloudflared-1:critical}"
CONTAINER_HEALTH_RETRIES="${CONTAINER_HEALTH_RETRIES:-2}"
CONTAINER_HEALTH_RETRY_WAIT="${CONTAINER_HEALTH_RETRY_WAIT:-15}"
# docker CLI 可能卡住（Docker Desktop 重啟中）：每次呼叫都有上限
CONTAINER_HEALTH_TIMEOUT="${CONTAINER_HEALTH_TIMEOUT:-10}"
STREAK_FILE="${HEALTH_STREAK_DIR:-$ROOT/data/.health-streaks}/container.state"

EXIT_OK=0
EXIT_CRITICAL=1
EXIT_PENDING=3
EXIT_TOOLING=4
EXIT_DEGRADED=9

for v in CONTAINER_HEALTH_RETRIES CONTAINER_HEALTH_RETRY_WAIT CONTAINER_HEALTH_TIMEOUT; do
    case "${!v}" in ''|*[!0-9]*) printf -v "$v" '%s' 1 ;; esac
done
[ "$CONTAINER_HEALTH_RETRIES" -lt 1 ] && CONTAINER_HEALTH_RETRIES=1
[ "$CONTAINER_HEALTH_TIMEOUT" -lt 1 ] && CONTAINER_HEALTH_TIMEOUT=1

emit() {
    # status attempts down pending reason
    printf 'ts=%s component=container probe=docker_inspect status=%s attempts=%s down=%s pending=%s reason=%s\n' \
        "$(date -Iseconds)" "$1" "$2" "${3:--}" "${4:--}" "$5"
}

_bounded() {
    if command -v timeout >/dev/null 2>&1; then
        timeout "$CONTAINER_HEALTH_TIMEOUT" "$@"
    else
        "$@"
    fi
}

# ── 名單解析（寫錯是設定錯誤，立刻判不出來，不進確認期）──────────────────────
names=(); containers=(); tiers=()
for item in $CONTAINER_HEALTH_TARGETS; do
    name="${item%%=*}"; rest="${item#*=}"
    container="${rest%%:*}"; tier="${rest##*:}"
    case "$name" in ''|*[!A-Za-z0-9_.-]*) emit tooling 0 - - bad_target; exit "$EXIT_TOOLING" ;; esac
    case "$container" in ''|-*|*[!A-Za-z0-9_.-]*) emit tooling 0 - - "bad_target_$name"; exit "$EXIT_TOOLING" ;; esac
    case "$tier" in critical|important|supporting) ;; *) emit tooling 0 - - "bad_tier_$name"; exit "$EXIT_TOOLING" ;; esac
    names+=("$name"); containers+=("$container"); tiers+=("$tier")
done
if [ "${#names[@]}" -eq 0 ]; then
    emit tooling 0 - - empty_targets
    exit "$EXIT_TOOLING"
fi

# ── docker CLI（與 db_backup.sh 共用偵測：/usr/bin/docker 存在但連不到 daemon 的那台機器）────────
if [ -z "${DOCKER_BIN:-}" ]; then
    # shellcheck source=_docker_bin.sh
    . "$SCRIPT_DIR/_docker_bin.sh"
    DOCKER_BIN="$(detect_docker_bin)"
fi

# 一輪：一次 docker inspect 查全部容器。找不到的容器 docker 回非零並在 stderr 說 No such…，找得到的
# 照樣印出來——所以退出碼不能直接當結論，要逐一比對。
declare -A SEEN_STATUS=() SEEN_HEALTH=()
PROBE_TOOLING=""
probe_once() {
    local out errtext cname cstatus chealth
    SEEN_STATUS=(); SEEN_HEALTH=(); PROBE_TOOLING=""
    if ! command -v "$DOCKER_BIN" >/dev/null 2>&1; then
        PROBE_TOOLING=docker_not_found
        return 0
    fi
    out="$(_bounded "$DOCKER_BIN" inspect --type container \
        --format '{{.Name}} {{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' \
        -- "${containers[@]}" 2>"$ERR_FILE")" || true
    errtext="$(cat "$ERR_FILE" 2>/dev/null)"
    while IFS=' ' read -r cname cstatus chealth; do
        [ -n "$cname" ] || continue
        cname="${cname#/}"
        SEEN_STATUS["$cname"]="$cstatus"
        SEEN_HEALTH["$cname"]="${chealth:-none}"
    done <<< "$out"
    # 一個都沒拿到、而且不是「全都不存在」：daemon 連不上或逾時，判不出來
    if [ "${#SEEN_STATUS[@]}" -eq 0 ]; then
        case "$errtext" in
            *"No such"*) ;;
            *) PROBE_TOOLING=docker_unavailable ;;
        esac
    fi
}
ERR_FILE="$(mktemp 2>/dev/null || echo "/tmp/check_container_health.$$.err")"
trap 'rm -f "$ERR_FILE"' EXIT

failing=()   # 「名稱:狀態」
failing_idx=()
attempt=0
while :; do
    attempt=$((attempt + 1))
    probe_once
    failing=(); failing_idx=()
    if [ -z "$PROBE_TOOLING" ]; then
        for i in "${!containers[@]}"; do
            c="${containers[$i]}"
            st="${SEEN_STATUS[$c]:-missing}"; hl="${SEEN_HEALTH[$c]:-none}"
            why=""
            if [ "$st" != running ]; then why="${names[$i]}:${st}"
            elif [ "$hl" = unhealthy ]; then why="${names[$i]}:unhealthy"; fi
            [ -n "$why" ] && { failing+=("$why"); failing_idx+=("$i"); }
        done
        [ "${#failing[@]}" -eq 0 ] && break
    fi
    [ "$attempt" -ge "$CONTAINER_HEALTH_RETRIES" ] && break
    sleep "$CONTAINER_HEALTH_RETRY_WAIT"
done

# ── 依 tier 去抖 ─────────────────────────────────────────────────────────────
now="$(date +%s)"
streak_load "$STREAK_FILE"
if [ -n "$PROBE_TOOLING" ]; then
    # 判不出來的這一輪不碰各容器的計數（不知道就不知道，不歸零也不累加）
    streak_bump tooling "$now"
else
    streak_clear tooling
    declare -A _is_failing=()
    for i in "${failing_idx[@]+"${failing_idx[@]}"}"; do _is_failing["${names[$i]}"]=1; done
    for i in "${!names[@]}"; do
        if [ -n "${_is_failing[${names[$i]}]:-}" ]; then streak_bump "${names[$i]}" "$now"; else streak_clear "${names[$i]}"; fi
    done
fi
streak_save "$STREAK_FILE"
note=""
[ "$STREAK_SAVE_OK" = yes ] || note="+streak_state_unwritable"

_confirmed() {  # key tier
    [ "$STREAK_SAVE_OK" = yes ] || return 0    # 記不住次數就每筆都算確認（寧可多吵也不靜默）
    [ "${STREAK_N[$1]:-0}" -ge "$(streak_confirm_count "$2")" ]
}

if [ -n "$PROBE_TOOLING" ]; then
    if _confirmed tooling tooling; then
        emit tooling "$attempt" - - "${PROBE_TOOLING}${note}"
        echo "check_container_health: docker inspect 沒有回應任何容器（$PROBE_TOOLING，連續 ${STREAK_N[tooling]:-1} 輪）；Docker Desktop 是否在跑？" >&2
        exit "$EXIT_TOOLING"
    fi
    emit pending "$attempt" - "tooling:${STREAK_N[tooling]:-1}" "${PROBE_TOOLING}_unconfirmed${note}"
    exit "$EXIT_PENDING"
fi

crit=(); other=(); pend=()
for j in "${!failing_idx[@]}"; do
    i="${failing_idx[$j]}"
    if _confirmed "${names[$i]}" "${tiers[$i]}"; then
        if [ "${tiers[$i]}" = critical ]; then crit+=("${failing[$j]}"); else other+=("${failing[$j]}"); fi
    else
        pend+=("${failing[$j]}:${STREAK_N[${names[$i]}]:-1}/$(streak_confirm_count "${tiers[$i]}")")
    fi
done
_join() { local IFS=,; printf '%s' "$*"; }
down="$(_join "${crit[@]+"${crit[@]}"}" "${other[@]+"${other[@]}"}")"
down="${down#,}"; down="${down%,}"
pending="$(_join "${pend[@]+"${pend[@]}"}")"

if [ "${#crit[@]}" -gt 0 ]; then
    emit down "$attempt" "$down" "$pending" "critical_container_down${note}"
    echo "check_container_health: critical 容器沒在跑：$down（docker ps -a；PostgreSQL 見 make setup，邊緣見 make edge-reload）" >&2
    exit "$EXIT_CRITICAL"
fi
if [ "${#other[@]}" -gt 0 ]; then
    emit degraded "$attempt" "$down" "$pending" "container_down${note}"
    echo "check_container_health: 容器沒在跑：$down" >&2
    exit "$EXIT_DEGRADED"
fi
if [ "${#pend[@]}" -gt 0 ]; then
    emit pending "$attempt" - "$pending" "unconfirmed${note}"
    exit "$EXIT_PENDING"
fi
emit ok "$attempt" - - ok
exit "$EXIT_OK"
