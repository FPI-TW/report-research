#!/usr/bin/env bash
# 新檔來源探針——每次執行是一次**無狀態**的單點觀測。
#
# 為什麼需要它：碰不到 NAS 的部署（EC2）的新研報全靠另一台主機（辦公室主機）每輪 rsync NAS 之後推到 R2 的
# inbox/。那台停機、sync 沒在跑、NAS 掛不上、推送失敗時，這台看到的都只是「inbox 沒有新檔」——自己的 sync 每 3 小時
# 照樣 rc=0、心跳照樣更新、/healthz 照樣綠，與「這幾天沒有新稿」無從分辨。
#
# 判斷在 web 行程裡（`/healthz/feed`，`app/services/host_beacon.py`）：它讀來源那台每 10 分鐘發布到 R2 的主機信標
# （`beacons/<HOST_BEACON_WATCH>.json`）。那支端點只回答本機直連、只回一個鍵 {"feed": state}。本檔只把 state 轉成退出碼。
#
# 職責邊界（與 check_web_health.sh 相同）：本檔只回報「此刻的事實」。incident 狀態、去重、提醒節奏、恢復判定、
# webhook 投遞全部屬於 P5（scripts/incident_handler.sh，**不改它**），由 deploy/systemd/report-mark-feed-incident.service
# 以另一組 INCIDENT_* 環境變數跑。
#
# 刻意不用 Python／uv／.venv：與其他探針同一個理由（2026-08-18 的根因是 venv 損毀，相依它的探針會與被監控的
# 東西一起死）。這裡只用 curl／coreutils。
#
# 退出碼與 P5 的對應（incident_handler.sh 既有的分級，不必改它）：
#   1 → CRITICAL「探針回報失敗」：來源主機失聯——信標過期（stale）或根本沒有（missing）。那台停機、排程器死了，
#       或它連不到 R2（連不到 R2 時 inbox 也推不上來，同一件事）
#   2 → CRITICAL「探針回報失敗」：來源主機有在回報，但來源段壞了——sync 的 timer 停了、來源段太久沒有結果、
#       NAS 掛載或 rsync 失敗、推送沒開或失敗、最近一輪被中止，或信標格式看不懂（哪一條在本探針的 reason）
#   3 → 判不出來：web 連不上、端點不存在（舊版本）、回 unknown（R2 讀不到）。feed-incident unit 以
#       INCIDENT_HOLD_EXIT_CODES=3 把它當 hold（不開也不關事件）——web 與 R2 各有自己的事件，這組不該因此把進行中的
#       來源事件誤判成已恢復。
#   4 → WARNING：探針自己不能執行（缺 curl），或 web 沒設定要監看哪一台（HOST_BEACON_WATCH 沒設）——裝了探針卻
#       沒有東西可看，不能靜默當作健康
set -u

HEALTH_URL="${FEED_HEALTH_URL:-http://127.0.0.1:8097/healthz/feed}"
HEALTH_TIMEOUT="${FEED_HEALTH_TIMEOUT:-10}"
HEALTH_RETRIES="${FEED_HEALTH_RETRIES:-2}"
HEALTH_RETRY_WAIT="${FEED_HEALTH_RETRY_WAIT:-10}"

# 退出碼契約（tests/test_feed_health_probe.py 釘死；P5 依此分級）
EXIT_OK=0
EXIT_SILENT=1
EXIT_BROKEN=2
EXIT_UNKNOWN=3
EXIT_TOOLING=4

emit() {
    printf 'ts=%s component=feed probe=local_http status=%s http_code=%s latency_ms=%s attempts=%s reason=%s\n' \
        "$(date -Iseconds)" "$1" "$2" "$3" "$4" "$5"
}

command -v curl >/dev/null 2>&1 || {
    emit tooling 000 0 0 curl_not_found
    echo "check_feed_health: 找不到 curl，無法探測" >&2
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

# 從 {"feed":"<state>"} 取出 state；只收小寫字母與底線（讀不懂就是空字串）。
state="$(printf '%s' "$body" | head -n-1 | tr -d '\n' \
    | sed -n 's/.*"feed"[[:space:]]*:[[:space:]]*"\([a-z_]*\)".*/\1/p' | head -c 64)"

case "$code" in
    200)
        case "$state" in
            ok)
                emit ok "$code" "$elapsed_ms" "$attempt" healthy
                exit "$EXIT_OK"
                ;;
            disabled)
                emit tooling "$code" "$elapsed_ms" "$attempt" feed_disabled
                echo "check_feed_health: web 沒設定要監看的來源主機（HOST_BEACON_WATCH 沒設或不合法）；這台不需要監看就別裝這組 unit" >&2
                exit "$EXIT_TOOLING"
                ;;
        esac
        emit unknown "$code" "$elapsed_ms" "$attempt" "feed_${state:-unreadable}"
        echo "check_feed_health: web 判不出來源主機的狀態（state=${state:-讀不懂}）——R2 讀不到或逾時；見 web 日誌" >&2
        exit "$EXIT_UNKNOWN"
        ;;
    503)
        case "$state" in
            stale|missing)
                emit alert "$code" "$elapsed_ms" "$attempt" "feed_${state}"
                echo "check_feed_health: 來源主機失聯（${state}：信標沒有在更新）。那台停機、排程器沒在跑，或連不到 R2；處置見 docs/production_resilience.md「來源主機信標」" >&2
                exit "$EXIT_SILENT"
                ;;
            invalid|timer_inactive|sync_stalled|source_failed|push_disabled|push_failed|round_aborted)
                emit alert "$code" "$elapsed_ms" "$attempt" "feed_${state}"
                echo "check_feed_health: 來源段異常 ${state}（細節在管理後台「維運 → 來源主機」；處置見 docs/production_resilience.md「來源主機信標」）" >&2
                exit "$EXIT_BROKEN"
                ;;
            *)
                # 503 就是 web 說「有事」；本體讀不懂（或是這支探針還不認得的新狀態）時寧可當告警，也不要靜默。
                emit alert "$code" "$elapsed_ms" "$attempt" feed_unavailable
                echo "check_feed_health: 端點回 503 但本體讀不懂，視為告警" >&2
                exit "$EXIT_BROKEN"
                ;;
        esac
        ;;
    000)
        emit unknown "$code" "$elapsed_ms" "$attempt" "web_unreachable_curl_rc_${curl_rc}"
        echo "check_feed_health: 連不上 $HEALTH_URL（web 本身的事件由 report-mark-incident 負責）" >&2
        exit "$EXIT_UNKNOWN"
        ;;
    404)
        emit unknown "$code" "$elapsed_ms" "$attempt" endpoint_not_found
        echo "check_feed_health: 端點不存在（web 還是舊版本，或請求不是本機直連）" >&2
        exit "$EXIT_UNKNOWN"
        ;;
    *)
        emit unknown "$code" "$elapsed_ms" "$attempt" "unexpected_http_${code}"
        echo "check_feed_health: 非預期的狀態碼 $code" >&2
        exit "$EXIT_UNKNOWN"
        ;;
esac
