#!/usr/bin/env bash
# 證明一個 oneshot unit「本輪真的執行過」。**唯讀**，不啟動任何東西。
#
# 為什麼需要它：2026-08-19 部署 P1 時，`systemctl start report-mark-freshness.service`
# 的終端輸出看起來完全成功——`Result=success`、`ExecMainStatus=0`，還印出了一份
# freshness 報告。但 journal 裡該 unit 當日只有 9 行、全屬 08:31 那次自然觸發，
# `ExecMainStartTimestamp` 也是 08:31。**unit 根本沒有執行。**
#
# 成因是兩件事疊在一起：
#   1. `Result=success` 與 `ExecMainStatus=0` 是**上一次**執行留下的值，也是從未執行過
#      的 unit 的預設值——兩者無法區分。
#   2. oneshot 在沒有其他 unit 引用時會被 systemd 回收（CollectMode=inactive），
#      回收後 `systemctl show` 讀到的是重新載入的乾淨狀態，屬性一律為空。
#
# 所以判準只能是「相對於一個事前基線的前進」，不能是絕對值。
#
#   用法：
#     BASE=$(bash scripts/verify_oneshot_ran.sh baseline <unit>)
#     sudo systemctl start <unit>
#     bash scripts/verify_oneshot_ran.sh verify <unit> "$BASE"
#
#   rc 0＝EXECUTION_PROVEN／1＝EXECUTION_NOT_PROVEN／2＝用法或工具錯誤
set -u

MODE="${1:-}"
UNIT="${2:-}"
[ -n "$MODE" ] && [ -n "$UNIT" ] || {
    echo "用法: $0 baseline <unit> | $0 verify <unit> <baseline-token>" >&2
    exit 2
}
command -v systemctl >/dev/null 2>&1 || { echo "找不到 systemctl" >&2; exit 2; }

# journal 行數是第二個獨立證據：屬性可能被 GC 清空，但 journal 不會回頭。
_journal_lines() { journalctl -u "$1" --no-pager 2>/dev/null | wc -l; }
_prop() { systemctl show "$1" -p "$2" --value 2>/dev/null; }

case "$MODE" in
  baseline)
    # token 四段：epoch|start_mono|exit_mono|journal 行數
    # **exit 必須與 exit 比**：初版只存 start_mono，卻拿現在的 exit_mono 去比它，
    # 而 exit 本來就晚於 start，於是那個佐證欄位恆為 yes——一個永遠成立的證據
    # 等於沒有證據（判定當時靠必要條件擋住了，但欄位本身在誤導讀者）。
    printf '%s|%s|%s|%s\n' \
        "$(date +%s)" \
        "$(_prop "$UNIT" ExecMainStartTimestampMonotonic)" \
        "$(_prop "$UNIT" ExecMainExitTimestampMonotonic)" \
        "$(_journal_lines "$UNIT")"
    ;;
  verify)
    TOKEN="${3:-}"
    [ -n "$TOKEN" ] || { echo "verify 需要 baseline token" >&2; exit 2; }
    # **格式錯誤的 token 必須是工具錯誤（rc=2），不能被當成判定。**
    # 初版把非數字欄位一律歸 0，於是隨便一個字串當 token 就會讓所有現值看起來
    # 「都前進了」→ 假的 EXECUTION_PROVEN。一個會在輸入壞掉時回報成功的驗證器，
    # 比沒有驗證器更危險。
    _bad_token() {
        echo "baseline token 格式錯誤（需 epoch|start_mono|exit_mono|lines，全為整數）：$1" >&2
        exit 2
    }
    OLD_IFS="$IFS"; IFS='|'
    # shellcheck disable=SC2086
    set -- $TOKEN
    IFS="$OLD_IFS"
    [ "$#" -eq 4 ] || _bad_token "$TOKEN"
    B_EPOCH="$1"; B_MONO="$2"; B_EXIT="$3"; B_LINES="$4"
    for v in "$B_EPOCH" "$B_MONO" "$B_EXIT" "$B_LINES"; do
        case "$v" in ''|*[!0-9]*) _bad_token "$TOKEN" ;; esac
    done

    NOW_MONO="$(_prop "$UNIT" ExecMainStartTimestampMonotonic)"
    NOW_EXIT="$(_prop "$UNIT" ExecMainExitTimestampMonotonic)"
    NOW_LINES="$(_journal_lines "$UNIT")"
    case "$NOW_MONO" in ''|*[!0-9]*) NOW_MONO=0 ;; esac
    case "$NOW_EXIT" in ''|*[!0-9]*) NOW_EXIT=0 ;; esac

    # 必要條件：啟動時間戳必須前進。屬性被 GC 清空（NOW_MONO=0）也算沒有前進——
    # 那正是最容易被誤讀成成功的情況。
    START_ADVANCED=no
    [ "$NOW_MONO" -gt "$B_MONO" ] && START_ADVANCED=yes
    # 至少一項佐證
    LINES_GREW=no;  [ "$NOW_LINES" -gt "$B_LINES" ] && LINES_GREW=yes
    EXIT_ADVANCED=no; [ "$NOW_EXIT" -gt "$B_EXIT" ] && EXIT_ADVANCED=yes

    printf 'unit=%s start_advanced=%s journal_grew=%s exit_advanced=%s baseline_lines=%s now_lines=%s\n' \
        "$UNIT" "$START_ADVANCED" "$LINES_GREW" "$EXIT_ADVANCED" "$B_LINES" "$NOW_LINES"

    if [ "$START_ADVANCED" = yes ] && { [ "$LINES_GREW" = yes ] || [ "$EXIT_ADVANCED" = yes ]; }; then
        echo "EXECUTION_PROVEN"
        exit 0
    fi
    echo "EXECUTION_NOT_PROVEN" >&2
    echo "  Result／ExecMainStatus 不是證據：從未執行過的 unit 也是 success/0。" >&2
    exit 1
    ;;
  *)
    echo "未知模式：$MODE" >&2; exit 2 ;;
esac
