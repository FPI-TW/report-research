#!/usr/bin/env bash
# 由 report-mark-alert@.service 觸發（各 unit 的 OnFailure=）。$1 = 失敗的 unit 名。
#
# 為什麼不是寄信：本機沒有 MTA、也沒有既有的通知管道，硬寫一個「寄信」只會產出
# 靜默失敗的假告警。這支腳本做的是**留下耐久且可查的痕跡**：
#   1. journal 一行 ERROR（給 journalctl -p err 撈）
#   2. data/unit_failures.log 追加一筆（含失敗當下的 journal 尾巴——三週後才發現時，
#      你要的正是當時的上下文，而 journald 可能已經輪替掉了）
#   3. 若設了 REPORT_MARK_ALERT_WEBHOOK 才送 webhook（opt-in，URL 不進 repo）
#
# 鐵律：這支腳本在「東西已經壞掉」時執行，自己絕不能再壞。全程容錯、恆 exit 0——
# 非 0 會讓 systemd 把告警器本身也記成 failed，在日誌裡製造第二筆假故障。
set -u

UNIT="${1:-unknown}"
ROOT="${REPORT_MARK_ROOT:-}"
TS="$(date -Iseconds)"

logger -t report-mark-alert -p user.err "unit failed: ${UNIT}" 2>/dev/null || true

if [ -n "$ROOT" ] && [ -d "$ROOT" ]; then
    LOG_DIR="$ROOT/data"
    mkdir -p "$LOG_DIR" 2>/dev/null || true
    LOG="$LOG_DIR/unit_failures.log"
    {
        echo "=== $TS  UNIT=$UNIT ==="
        systemctl show "$UNIT" -p Result -p ExecMainStatus -p ActiveEnterTimestamp 2>/dev/null || true
        echo "--- journal (last 30) ---"
        journalctl -u "$UNIT" -n 30 --no-pager 2>/dev/null || echo "(journal 讀取失敗)"
        echo
    } >> "$LOG" 2>/dev/null || true
fi

# opt-in webhook：未設就完全不做（不留失敗痕跡、不拖慢告警）
# **URL 不進 argv**：`curl ... "$URL"` 會讓 secret 出現在行程清單裡，任何本機使用者
# `ps` 就看得到。改用 `-K -` 從 stdin 餵 curl 設定檔，URL 只存在於管線中。
# 與 scripts/incident_handler.sh 的 notify() 同一種修法——同一個 secret，同一個洩漏面。
if [ -n "${REPORT_MARK_ALERT_WEBHOOK:-}" ]; then
    printf 'url = "%s"\n' "$REPORT_MARK_ALERT_WEBHOOK" \
      | curl -fsS --connect-timeout 5 --max-time 10 -K - -X POST \
        -H "Content-Type: application/json" \
        -d "{\"text\":\"[report-mark] unit failed: ${UNIT} at ${TS}\"}" \
        >/dev/null 2>&1 || logger -t report-mark-alert -p user.warning \
        "webhook 投遞失敗（告警仍已寫入 unit_failures.log）" 2>/dev/null || true
fi

exit 0
