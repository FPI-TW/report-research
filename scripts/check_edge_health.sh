#!/usr/bin/env bash
# 對外邊緣健康探針（P4，第三個元件）——每次執行是一次**無狀態**的單點觀測。
#
# 監控對象是使用者實際走的那條路：Cloudflare → cloudflared 隧道 → nginx 容器 → web。
# 做法是從本機打**對外網址**的 /healthz（`EDGE_HEALTH_URL`），和使用者看到的一樣。
#
# 為什麼需要它：2026-09-24 Docker Desktop 重啟，deploy-nginx-1 重建時單檔 bind mount
# （nginx.conf → default.conf.template）掛載失敗、exit 127 停住，`restart: unless-stopped`
# 也沒把它拉起來。cloudflared 還活著但解析不到 `nginx`，外網全部 502 Bad Gateway，
# 持續約 36 小時，最後是使用者回報才發現。那段期間：
#   - check_web_health.sh 打的是本機 :8097，web 本身健康，全程回 0
#   - nginx 沒有對主機發布任何埠，本機沒有別的路徑探得到它
# **本機 /healthz 綠不代表使用者連得到**，這一支補的就是那個盲區。
#
# 職責邊界（與 check_web_health.sh 相同）：本檔只回報「此刻的事實」。incident
# 狀態、去重、提醒節奏、恢復判定、webhook 投遞全部屬於 P5，**本檔不得實作任何
# 跨執行的狀態**。P5 不改一個字，跑第二個實例即可（見
# deploy/systemd/report-mark-edge-incident.service）。
#
# 刻意不用 Python／uv／.venv：與 P4 同一個理由（2026-08-18 的根因是 venv 損毀）。
# 這裡只用 curl／coreutils。
set -u

# 對外網址**不寫進 repo**：放在 /etc/default/report-mark-sync（unit 以 EnvironmentFile 載入）。
EDGE_HEALTH_URL="${EDGE_HEALTH_URL:-}"
# 本機 origin，只用來做失敗歸因（見下方「origin 先壞」那段）。
EDGE_ORIGIN_URL="${EDGE_ORIGIN_URL:-http://127.0.0.1:8097/healthz}"
# 對外請求要繞 Cloudflare 一圈，逾時比本機探針（5 秒）寬。
EDGE_HEALTH_TIMEOUT="${EDGE_HEALTH_TIMEOUT:-10}"
EDGE_HEALTH_RETRIES="${EDGE_HEALTH_RETRIES:-3}"
EDGE_HEALTH_RETRY_WAIT="${EDGE_HEALTH_RETRY_WAIT:-15}"
EDGE_ORIGIN_TIMEOUT="${EDGE_ORIGIN_TIMEOUT:-5}"

# 退出碼契約（P5 依此分級：1 為 CRITICAL、4 為 WARNING、0/3 視為健康）
EXIT_OK=0        # 對外 /healthz 回 200
EXIT_DOWN=1      # 拿到 HTTP 回應但不是 200，且本機 origin 健康＝邊緣層壞了（nginx／隧道）
EXIT_ORIGIN=3    # 對外失敗，但本機 origin 也不健康＝web 自己壞了，由 web 元件負責（unit 需宣告 SuccessExitStatus=3）
EXIT_TOOLING=4   # 探針判不出來：缺 curl、沒設網址，或對外請求連 HTTP 回應都沒拿到

emit() {
    printf 'ts=%s component=edge probe=external_http status=%s http_code=%s latency_ms=%s attempts=%s reason=%s\n' \
        "$(date -Iseconds)" "$1" "$2" "$3" "$4" "$5"
}

command -v curl >/dev/null 2>&1 || {
    emit tooling 000 0 0 curl_not_found
    echo "check_edge_health: 找不到 curl，無法探測" >&2
    exit "$EXIT_TOOLING"
}

# 沒設網址是「設定缺漏」而不是「沒事」：unit 裝了就代表要監控，靜默回 0 等於監控不存在。
if [ -z "$EDGE_HEALTH_URL" ]; then
    emit tooling 000 0 0 edge_url_unset
    echo "check_edge_health: 未設定 EDGE_HEALTH_URL（應寫在 /etc/default/report-mark-sync）" >&2
    exit "$EXIT_TOOLING"
fi

# ── 對外探測（含單次執行內的重試）────────────────────────────────────────
# 3 次 × 15 秒＝跨 30 秒，吸收 `make edge-reload` 重建容器與隧道重連的空窗。
attempt=0
code=000
elapsed_ms=0
curl_rc=0
while [ "$attempt" -lt "$EDGE_HEALTH_RETRIES" ]; do
    attempt=$((attempt + 1))
    start_ns=$(date +%s%N)
    # `|| curl_rc=$?`：全 repo 慣例（tests/test_db_backup.py::ShellExitCodeCaptureTests 通掃）。
    curl_rc=0
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time "$EDGE_HEALTH_TIMEOUT" "$EDGE_HEALTH_URL" 2>/dev/null)" \
        || curl_rc=$?
    elapsed_ms=$(( ($(date +%s%N) - start_ns) / 1000000 ))
    if [ "$curl_rc" -eq 0 ] && [ "$code" = "200" ]; then
        emit ok "$code" "$elapsed_ms" "$attempt" ok
        exit "$EXIT_OK"
    fi
    [ "$attempt" -lt "$EDGE_HEALTH_RETRIES" ] && sleep "$EDGE_HEALTH_RETRY_WAIT"
done

# ── 失敗歸因 ──────────────────────────────────────────────────────────────
# origin 先壞：web 自己不健康時，對外一定也失敗（nginx 回 502／503）。那是 web 元件的事件，
# 這裡再開一個只會重複通知同一件事。回 3（視為健康）並把原因寫進 reason。
# 本機 origin 連不上、逾時、非 200 都算「origin 不健康」——與 check_web_health.sh 的判準一致。
origin_code="$(curl -s -o /dev/null -w '%{http_code}' --max-time "$EDGE_ORIGIN_TIMEOUT" "$EDGE_ORIGIN_URL" 2>/dev/null)" \
    || origin_code=000
if [ "$origin_code" != "200" ]; then
    emit origin_down "$code" "$elapsed_ms" "$attempt" "origin_http_${origin_code}"
    echo "check_edge_health: 對外探測失敗，但本機 origin 也不健康（$EDGE_ORIGIN_URL → $origin_code），由 web 元件的事件負責" >&2
    exit "$EXIT_ORIGIN"
fi

# 連 HTTP 回應都沒拿到（DNS 解析失敗、連線被拒、逾時）：問題可能在本機這一側的網路，
# 不一定是站台壞了——WSL 的 DNS 偶發逾時是實際見過的（cloudflared 日誌）。歸 4＝「判不出來」，
# 仍會開 WARNING 事件，不會靜默。
if [ "$curl_rc" -ne 0 ] || [ "$code" = "000" ]; then
    emit tooling "$code" "$elapsed_ms" "$attempt" "edge_curl_rc_${curl_rc}"
    echo "check_edge_health: $EDGE_HEALTH_URL 沒有 HTTP 回應（curl_rc=$curl_rc），本機 origin 正常；先確認本機對外網路與 DNS" >&2
    exit "$EXIT_TOOLING"
fi

# 拿到 HTTP 回應、不是 200，而 origin 健康：壞在邊緣層。
emit down "$code" "$elapsed_ms" "$attempt" "edge_http_${code}"
echo "check_edge_health: $EDGE_HEALTH_URL 回 $code，本機 origin 正常——壞在 nginx 或隧道" >&2
case "$code" in
    502|504) echo "  處置：多半是 nginx 容器沒在跑。docker ps -a | grep deploy-nginx；make edge-reload（見 docs/EXTERNAL_ACCESS.md 疑難排解）" >&2 ;;
    530)     echo "  處置：隧道斷了（Cloudflare 1033）。make edge-logs 看 cloudflared；make up-edge" >&2 ;;
    *)       echo "  處置：make edge-logs；若啟用了 Cloudflare Access，/healthz 要有 Bypass（docs/EXTERNAL_ACCESS.md 3d）" >&2 ;;
esac
exit "$EXIT_DOWN"
