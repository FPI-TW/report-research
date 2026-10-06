#!/usr/bin/env bash
# 容器健康探針（P4 式，第四個元件）——每次執行是一次**無狀態**的單點觀測。
#
# 監控對象是 Service Catalog（deploy/ops/services.prod.toml）裡 kind=container 的那幾個：
# PostgreSQL、對外邊緣的 nginx 與 cloudflared。為什麼 web 探針與邊緣探針之外還要這一支：
#   - web 探針打的是 /healthz（只探 DB），DB 容器停掉時它會回報 web 故障，但訊息指不到「容器沒在跑」
#   - 邊緣探針從外網打，nginx 停掉時只看得到 502；2026-09-24 那次是 deploy-nginx-1 停在 Exited
#     36 小時（見 check_edge_health.sh 檔頭）——直接看容器狀態的這一層更早、也更準
#
# 職責邊界（與 check_web_health.sh 相同）：本檔只回報「此刻的事實」。incident 狀態、去重、
# 提醒節奏、恢復判定、webhook 投遞全部屬於 P5（report-mark-container-incident 跑同一支
# scripts/incident_handler.sh），**本檔不得實作任何跨執行的狀態**。
#
# 刻意不用 Python／uv／.venv：與 P4 同一個理由（2026-08-18 的根因是 venv 損毀）。
# 這裡只用 docker CLI／coreutils。
set -u

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

# 監控名單：`名稱=容器:tier`，空白分隔。預設值與 catalog 逐字一致（tests/test_container_host_probes.py
# 對 deploy/ops/services.prod.toml 比對）；bash 不解析 TOML，所以由測試守住兩邊不漂移。
CONTAINER_HEALTH_TARGETS="${CONTAINER_HEALTH_TARGETS:-postgres=report-mark-postgres:critical nginx=deploy-nginx-1:critical cloudflared=deploy-cloudflared-1:critical}"
# 單次執行內的重試：容器 restart（`restart: unless-stopped`、`make edge-reload`）有數秒到十數秒的
# restarting 空窗。critical tier 的 P5 第一次確認故障就開事件，所以「確認」要在這裡做完。
CONTAINER_HEALTH_RETRIES="${CONTAINER_HEALTH_RETRIES:-2}"
CONTAINER_HEALTH_RETRY_WAIT="${CONTAINER_HEALTH_RETRY_WAIT:-15}"
# docker CLI 可能卡住（Docker Desktop 重啟中）：每次呼叫都有上限。
CONTAINER_HEALTH_TIMEOUT="${CONTAINER_HEALTH_TIMEOUT:-10}"

# 退出碼契約（P5 依此分級；container 那組 P5 設 INCIDENT_CONFIRM_EXIT_CODES=2、INCIDENT_WARNING_EXIT_CODES=2）
EXIT_OK=0          # 全部在跑
EXIT_CRITICAL=1    # 至少一個 critical tier 的容器沒在跑（或 unhealthy）→ P5 第一次就開 CRITICAL
EXIT_DEGRADED=2    # 只有 important／supporting tier 的容器沒在跑 → P5 連續確認後開 WARNING
EXIT_TOOLING=4     # 判不出來：找不到 docker、daemon 連不上、名單格式錯

for v in CONTAINER_HEALTH_RETRIES CONTAINER_HEALTH_RETRY_WAIT CONTAINER_HEALTH_TIMEOUT; do
    case "${!v}" in ''|*[!0-9]*) printf -v "$v" '%s' 1 ;; esac
done
[ "$CONTAINER_HEALTH_RETRIES" -lt 1 ] && CONTAINER_HEALTH_RETRIES=1

emit() {
    printf 'ts=%s component=container probe=docker_inspect status=%s attempts=%s down=%s reason=%s\n' \
        "$(date -Iseconds)" "$1" "$2" "$3" "$4"
}

_bounded() {
    if command -v timeout >/dev/null 2>&1; then
        timeout "$CONTAINER_HEALTH_TIMEOUT" "$@"
    else
        "$@"
    fi
}

# ── 名單解析 ──────────────────────────────────────────────────────────────
names=(); containers=(); tiers=()
for item in $CONTAINER_HEALTH_TARGETS; do
    name="${item%%=*}"; rest="${item#*=}"
    container="${rest%%:*}"; tier="${rest##*:}"
    case "$name$container" in *[!A-Za-z0-9_.-]*|'') emit tooling 0 - "bad_target_${item//[^A-Za-z0-9_.-]/_}"; exit "$EXIT_TOOLING" ;; esac
    case "$tier" in critical|important|supporting) ;; *) emit tooling 0 - "bad_tier_$name"; exit "$EXIT_TOOLING" ;; esac
    names+=("$name"); containers+=("$container"); tiers+=("$tier")
done
if [ "${#names[@]}" -eq 0 ]; then
    emit tooling 0 - empty_targets
    exit "$EXIT_TOOLING"
fi

# ── docker CLI ────────────────────────────────────────────────────────────
# 與 db_backup.sh 共用偵測（/usr/bin/docker 存在但連不到 daemon 的那台機器，見 _docker_bin.sh）。
if [ -z "${DOCKER_BIN:-}" ]; then
    # shellcheck source=_docker_bin.sh
    . "$SCRIPT_DIR/_docker_bin.sh"
    DOCKER_BIN="$(detect_docker_bin)"
fi
if ! command -v "$DOCKER_BIN" >/dev/null 2>&1; then
    emit tooling 0 - docker_not_found
    echo "check_container_health: 找不到 docker CLI（$DOCKER_BIN）" >&2
    exit "$EXIT_TOOLING"
fi

# 一輪：一次 docker inspect 查全部容器。找不到的容器 docker 會回非零並在 stderr 說 No such…，
# 找得到的照樣印出來——所以退出碼不能直接當結論，要逐一比對。
probe_once() {
    local out err rc=0 line cname cstatus chealth i
    err="$(mktemp 2>/dev/null || echo /tmp/check_container_health.$$)"
    out="$(_bounded "$DOCKER_BIN" inspect --type container \
        --format '{{.Name}} {{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' \
        -- "${containers[@]}" 2>"$err")" || rc=$?
    local errtext
    errtext="$(cat "$err" 2>/dev/null)"; rm -f "$err"
    declare -gA SEEN_STATUS=() SEEN_HEALTH=()
    while IFS=' ' read -r cname cstatus chealth; do
        [ -n "$cname" ] || continue
        cname="${cname#/}"
        SEEN_STATUS["$cname"]="$cstatus"
        SEEN_HEALTH["$cname"]="$chealth"
    done <<< "$out"
    # 一個都沒拿到、而且不是「全都不存在」：daemon 連不上或逾時，判不出來。
    if [ "${#SEEN_STATUS[@]}" -eq 0 ] && [ "$rc" -ne 0 ]; then
        case "$errtext" in
            *"No such"*) ;;
            *) PROBE_TOOLING="docker_rc_${rc}"; return 0 ;;
        esac
    fi
    PROBE_TOOLING=""
    CRIT_DOWN=(); OTHER_DOWN=()
    for i in "${!containers[@]}"; do
        local c="${containers[$i]}" st hl why=""
        st="${SEEN_STATUS[$c]:-missing}"; hl="${SEEN_HEALTH[$c]:-none}"
        if [ "$st" != running ]; then
            why="${names[$i]}:${st}"
        elif [ "$hl" = unhealthy ]; then
            why="${names[$i]}:unhealthy"
        fi
        [ -n "$why" ] || continue
        if [ "${tiers[$i]}" = critical ]; then CRIT_DOWN+=("$why"); else OTHER_DOWN+=("$why"); fi
    done
}

attempt=0
while :; do
    attempt=$((attempt + 1))
    probe_once
    if [ -z "$PROBE_TOOLING" ] && [ "${#CRIT_DOWN[@]}" -eq 0 ] && [ "${#OTHER_DOWN[@]}" -eq 0 ]; then
        emit ok "$attempt" - ok
        exit "$EXIT_OK"
    fi
    [ "$attempt" -ge "$CONTAINER_HEALTH_RETRIES" ] && break
    sleep "$CONTAINER_HEALTH_RETRY_WAIT"
done

if [ -n "$PROBE_TOOLING" ]; then
    emit tooling "$attempt" - "$PROBE_TOOLING"
    echo "check_container_health: docker inspect 沒有回應任何容器（$PROBE_TOOLING）；Docker Desktop 是否在跑？" >&2
    exit "$EXIT_TOOLING"
fi

all_down=("${CRIT_DOWN[@]+"${CRIT_DOWN[@]}"}" "${OTHER_DOWN[@]+"${OTHER_DOWN[@]}"}")
down_list="$(IFS=,; printf '%s' "${all_down[*]}")"
if [ "${#CRIT_DOWN[@]}" -gt 0 ]; then
    emit down "$attempt" "$down_list" critical_container_down
    echo "check_container_health: critical 容器沒在跑：$down_list（docker ps -a；PostgreSQL 見 make setup，邊緣見 make edge-reload）" >&2
    exit "$EXIT_CRITICAL"
fi
emit degraded "$attempt" "$down_list" container_down
echo "check_container_health: 容器沒在跑：$down_list" >&2
exit "$EXIT_DEGRADED"
