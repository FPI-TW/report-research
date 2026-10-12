#!/usr/bin/env bash
# 安全事件探針（Admin v2 Security lane）——每次執行是一次**無狀態**的單點觀測。
#
# 判斷在 web 行程裡（`/healthz/security`，`app/services/security_ops.py` 的 evaluate_alerts）：15 分鐘內全站
# 登入失敗、同一帳號連續失敗、權限提升失敗、稽核鏈驗證失敗（門檻 SECURITY_*，在 app/config.py）。那支端點
# 只回答本機直連、只回一個鍵 {"security": state}，不含任何 IP、帳號或計數。本檔只把 state 轉成退出碼。
#
# 職責邊界（與 check_web_health.sh 相同）：本檔只回報「此刻的事實」。incident 狀態、去重、提醒節奏、恢復
# 判定、webhook 投遞全部屬於 P5（scripts/incident_handler.sh，**不改它**），由
# deploy/systemd/report-mark-security-incident.service 以另一組 INCIDENT_* 環境變數跑。Slack 只會收到
# 開場／升級／提醒／恢復這類狀態型通知，不會為每筆登入事件發一則（使用者定案 9）。
#
# 刻意不用 Python／uv／.venv：與其他探針同一個理由（2026-08-18 的根因是 venv 損毀，相依它的探針會與
# 被監控的東西一起死）。這裡只用 curl／coreutils。
#
# 退出碼與 P5 的對應（incident_handler.sh 既有的分級，不必改它）：
#   1 → CRITICAL「探針回報失敗」：稽核雜湊鏈驗證失敗（疑似竄改）
#   2 → CRITICAL「探針回報失敗」：登入失敗／同一帳號連續失敗／權限提升失敗達門檻（哪一條在本探針的 reason）
#   3 → 判不出來：web 連不上、端點不存在（舊版本）、回 unknown。security-incident unit 以
#       INCIDENT_HOLD_EXIT_CODES=3 把它當 hold（不開也不關事件）——web 掛掉是 web 那組事件的事，這組不該因此
#       把進行中的安全事件誤判成已恢復。
#   4 → WARNING：探針自己不能執行（缺 curl）
set -u

HEALTH_URL="${SECURITY_HEALTH_URL:-http://127.0.0.1:8097/healthz/security}"
HEALTH_TIMEOUT="${SECURITY_HEALTH_TIMEOUT:-10}"
HEALTH_RETRIES="${SECURITY_HEALTH_RETRIES:-2}"
HEALTH_RETRY_WAIT="${SECURITY_HEALTH_RETRY_WAIT:-10}"

# 退出碼契約（tests/test_security_health_probe.py 釘死；P5 依此分級）
EXIT_OK=0
EXIT_TAMPER=1
EXIT_ALERT=2
EXIT_UNKNOWN=3
EXIT_TOOLING=4

emit() {
    printf 'ts=%s component=security probe=local_http status=%s http_code=%s latency_ms=%s attempts=%s reason=%s\n' \
        "$(date -Iseconds)" "$1" "$2" "$3" "$4" "$5"
}

command -v curl >/dev/null 2>&1 || {
    emit tooling 000 0 0 curl_not_found
    echo "check_security_health: 找不到 curl，無法探測" >&2
    exit "$EXIT_TOOLING"
}

# 只有「連不上」才在單次執行內重試（web 剛好在重啟）；拿到任何 HTTP 回應就照它判斷。
attempt=0
code=000
body=""
elapsed_ms=0
curl_rc=0
while [ "$attempt" -lt "$HEALTH_RETRIES" ]; do
    attempt=$((attempt + 1))
    curl_rc=0
    start_ns=$(date +%s%N)
    body="$(curl -s -w $'\n%{http_code}' --max-time "$HEALTH_TIMEOUT" "$HEALTH_URL" 2>/dev/null)" || curl_rc=$?
    end_ns=$(date +%s%N)
    elapsed_ms=$(( (end_ns - start_ns) / 1000000 ))
    code="$(printf '%s' "$body" | tail -n1)"
    case "$code" in ''|*[!0-9]*) code=000 ;; esac
    [ "$code" != "000" ] && break
    [ "$attempt" -lt "$HEALTH_RETRIES" ] && sleep "$HEALTH_RETRY_WAIT"
done

# 從 {"security":"<state>"} 取出 state；只收小寫字母與底線（讀不懂就是空字串）。
state="$(printf '%s' "$body" | head -n-1 | tr -d '\n' \
    | sed -n 's/.*"security"[[:space:]]*:[[:space:]]*"\([a-z_]*\)".*/\1/p' | head -c 64)"

case "$code" in
    200)
        if [ "$state" = "ok" ]; then
            emit ok "$code" "$elapsed_ms" "$attempt" healthy
            exit "$EXIT_OK"
        fi
        emit unknown "$code" "$elapsed_ms" "$attempt" "security_${state:-unreadable}"
        echo "check_security_health: web 判不出安全狀態（state=${state:-讀不懂}）——DB 不可用或查詢逾時；見 web 日誌" >&2
        exit "$EXIT_UNKNOWN"
        ;;
    503)
        case "$state" in
            audit_chain_broken)
                emit alert "$code" "$elapsed_ms" "$attempt" security_audit_chain_broken
                echo "check_security_health: 稽核雜湊鏈驗證失敗（疑似竄改）。處置見 docs/production_resilience.md「安全告警」" >&2
                exit "$EXIT_TAMPER"
                ;;
            elevate_failures|account_failures|login_failures)
                emit alert "$code" "$elapsed_ms" "$attempt" "security_${state}"
                echo "check_security_health: 安全告警 ${state}（細節在管理後台「安全」頁；處置見 docs/production_resilience.md「安全告警」）" >&2
                exit "$EXIT_ALERT"
                ;;
            *)
                # 503 就是 web 說「有事」；本體讀不懂時寧可當告警，也不要靜默。
                emit alert "$code" "$elapsed_ms" "$attempt" security_unavailable
                echo "check_security_health: 端點回 503 但本體讀不懂，視為告警" >&2
                exit "$EXIT_ALERT"
                ;;
        esac
        ;;
    000)
        emit unknown "$code" "$elapsed_ms" "$attempt" "web_unreachable_curl_rc_${curl_rc}"
        echo "check_security_health: 連不上 $HEALTH_URL（web 本身的事件由 report-mark-incident 負責）" >&2
        exit "$EXIT_UNKNOWN"
        ;;
    404)
        emit unknown "$code" "$elapsed_ms" "$attempt" endpoint_not_found
        echo "check_security_health: 端點不存在（web 還是舊版本，或請求不是本機直連）" >&2
        exit "$EXIT_UNKNOWN"
        ;;
    *)
        emit unknown "$code" "$elapsed_ms" "$attempt" "unexpected_http_${code}"
        echo "check_security_health: 非預期的狀態碼 $code" >&2
        exit "$EXIT_UNKNOWN"
        ;;
esac
