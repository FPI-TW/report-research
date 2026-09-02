#!/usr/bin/env bash
# LineBot 健康探針（P4，第二個元件）——每次執行是一次**無狀態**的單點觀測。
#
# 監控對象是 C:\LineBot 的 line_file_bot.py（Windows 側 :8000）。它把 LINE 群組
# 裡的研報下載到 NAS，而那個 NAS 目錄正是 scripts/sync_new_reports.sh 的 rsync
# 來源——**它斷掉等於語料供稿斷掉**。
#
# 為什麼需要它：2026-08-13 到 08-31 它靜默停擺 18 天，期間語料庫少掉約三分之一
# 的每日新研報，而現有監控**沒有任何一條會說話**：
#   - check_web_health.sh 只探 report-mark 自己的 /healthz，與它無關
#   - check_batch_freshness.py 的語料閘刻意讓「沒有新研報」不算停更（避開週末假警報）
#   - 另一條供稿管道還在送，所以「有沒有新研報」也不能拿來當它的存活代理指標
# 最後是在一次無關的工作中被順帶發現的——與 2026-08-18 那次 4h50m 中斷同一種
# 發現方式。
#
# 職責邊界（與 check_web_health.sh 相同）：本檔只回報「此刻的事實」。incident
# 狀態、去重、提醒節奏、恢復判定、webhook 投遞全部屬於 P5，**本檔不得實作任何
# 跨執行的狀態**。P5 不必改一個字——incident_handler.sh 的 INCIDENT_PROBE_UNIT／
# INCIDENT_TIMER_UNIT／INCIDENT_COMPONENT／INCIDENT_STATE_DIR 都吃環境變數，
# 直接跑第二個實例即可（見 deploy/systemd/report-mark-linebot-incident.service）。
#
# 刻意不用 Python／uv／.venv：與 P4 同一個理由——2026-08-18 的根因正是 venv 損毀，
# 相依它的探針會與被監控的東西一起死。這裡只用 curl／coreutils。
#
# **沒有 L1（unit 狀態）那一層**：bot 跑在 Windows 側、由工作排程器的看門狗維持，
# WSL 這邊沒有對應的 systemd unit 可查。判定完全靠 HTTP——這反而更接近正解，
# 因為那次教訓就是「process alive != application healthy」。
set -u

# 用 127.0.0.1 而非 localhost：與 check_web_health.sh 同一個理由（localhost 常先
# 解析到 ::1，而服務綁的是 IPv4，curl 會拿到 connection refused——探針自己製造的
# 假故障，症狀與真故障一模一樣）。
# WSL 是 mirrored 網路模式，與 Windows 共用 localhost，所以這裡打得到 Windows 側。
HEALTH_URL="${LINEBOT_HEALTH_URL:-http://127.0.0.1:8000/health}"
HEALTH_TIMEOUT="${LINEBOT_HEALTH_TIMEOUT:-5}"
HEALTH_RETRIES="${LINEBOT_HEALTH_RETRIES:-3}"
HEALTH_RETRY_WAIT="${LINEBOT_HEALTH_RETRY_WAIT:-15}"

# 退出碼契約（P5 依此分級；1/2/4 皆視為故障）
EXIT_OK=0        # 健康：連得上且 200
EXIT_DOWN=1      # 連不上／逾時／非預期狀態碼 ＝ bot 沒在跑
EXIT_DEGRADED=2  # 連得上但回 503 ＝ 行程活著，但存不了檔（NAS 寫不進去或 staging 積壓）
EXIT_TOOLING=4   # 探針自己不能執行

emit() {
    printf 'ts=%s component=linebot probe=local_http status=%s http_code=%s latency_ms=%s attempts=%s reason=%s\n' \
        "$(date -Iseconds)" "$1" "$2" "$3" "$4" "$5"
}

command -v curl >/dev/null 2>&1 || {
    emit tooling 000 0 0 curl_not_found
    echo "check_linebot_health: 找不到 curl，無法探測" >&2
    exit "$EXIT_TOOLING"
}

# ── HTTP 探測（含單次執行內的重試）────────────────────────────────────────
# 重試做在**單次執行內**而非跨執行：跨執行需要狀態檔，而狀態是 P5 的東西。
# 3 次 × 15 秒＝跨 30 秒，足以吸收看門狗重啟 bot 的空窗（watchdog.ps1 啟動後
# 等 6 秒確認，整個週期在 10 秒內）。
attempt=0
code=000
elapsed_ms=0
curl_rc=0
body=""
while [ "$attempt" -lt "$HEALTH_RETRIES" ]; do
    attempt=$((attempt + 1))
    start_ns=$(date +%s%N)
    # `|| curl_rc=$?` 而非裸呼叫 + 獨立 `curl_rc=$?`：全 repo 慣例，且未來有人補上
    # `set -e` 時獨立成行會靜默變成死碼。
    body="$(curl -s -w $'\n%{http_code}' --max-time "$HEALTH_TIMEOUT" "$HEALTH_URL" 2>/dev/null)" \
        || curl_rc=$?
    end_ns=$(date +%s%N)
    elapsed_ms=$(( (end_ns - start_ns) / 1000000 ))
    code="$(printf '%s' "$body" | tail -n1)"
    case "$code" in
        200)
            emit ok "$code" "$elapsed_ms" "$attempt" healthy
            exit "$EXIT_OK"
            ;;
        503)
            # 行程活著但存不了檔。**這一類要與「沒在跑」分開**：處置完全不同
            # （前者去看 NAS 掛載與權限，後者去看看門狗與工作排程器）。
            detail="$(printf '%s' "$body" | head -n-1 | tr -d '\n' | cut -c1-200)"
            emit degraded "$code" "$elapsed_ms" "$attempt" cannot_save
            echo "check_linebot_health: bot 活著但存不了檔 → $detail" >&2
            exit "$EXIT_DEGRADED"
            ;;
    esac
    [ "$attempt" -lt "$HEALTH_RETRIES" ] && sleep "$HEALTH_RETRY_WAIT"
done

if [ "$code" = "000" ]; then
    reason="connect_failed_curl_rc_${curl_rc}"
else
    reason="unexpected_http_${code}"
fi
emit down "$code" "$elapsed_ms" "$attempt" "$reason"
echo "check_linebot_health: $HEALTH_URL 探測失敗（reason=$reason curl_rc=$curl_rc）" >&2
echo "  處置：看 C:\\LineBot\\watchdog.log 與 stdout.log；工作排程器的「LineBot Watchdog」是否還在。" >&2
exit "$EXIT_DOWN"
