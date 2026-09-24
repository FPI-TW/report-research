#!/usr/bin/env bash
# 應用層健康探針（P4）——每次執行是一次**無狀態**的單點觀測。
#
# 為什麼需要它：2026-08-18 的生產中斷持續 4 小時 50 分，期間
# `systemctl is-active report-mark-web.service` 全程顯示 `active`，而實際服務的
# HTTP 請求數是 **0**。原因是 uvicorn 先跑 lifespan 再 bind，bind 失敗後行程仍會
# 存活約 10 秒（背景載入 BGE-M3），配合 `Restart=always`／`RestartSec=3`，任何
# 時間點去查 `is-active` 都有很高機率看到 `active`。
# **process alive != application healthy**——判定應用健康只能打 HTTP。
#
# 刻意不用 Python／uv／.venv：那次中斷的根因正是 `/mnt/c` 上的 venv 損毀
# （uv 的 .tmp→rename 在 9p 上以 os error 2 失敗），若探針相依 Python 環境，
# 它會與被監控的東西一起死。這裡只用 curl／systemctl／coreutils。
#
# 職責邊界：本檔只回報「此刻的事實」。incident 狀態、去重、提醒節奏、恢復通知、
# 升級與 webhook 投遞全部屬於 P5，**本檔不得實作任何跨執行的狀態**。
set -u

HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:8097/healthz}"
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-5}"
HEALTH_RETRIES="${HEALTH_RETRIES:-3}"
HEALTH_RETRY_WAIT="${HEALTH_RETRY_WAIT:-15}"
HEALTH_GRACE="${HEALTH_GRACE:-60}"
HEALTH_UNIT="${HEALTH_UNIT:-report-mark-web.service}"

# 退出碼契約（測試釘死；P5 依此分級）
EXIT_OK=0        # 健康
EXIT_HTTP=1      # L2：HTTP 探測失敗（非 200／連不上／逾時）
EXIT_PROCESS=2   # L1：unit 不在 active
EXIT_GRACE=3     # 剛啟動的寬限期內，不視為故障（unit 需宣告 SuccessExitStatus=3）
EXIT_TOOLING=4   # 探針自己不能執行（缺 curl 等）
EXIT_DEGRADED=5  # L3：HTTP 健康，但問答路徑的必要相依（claude CLI）不在 web unit 的 PATH 上
EXIT_STORAGE=6   # L3：HTTP 健康，但物件儲存（R2）連不上——原檔與 PDF 全壞，其餘功能正常
EXIT_LLM=7       # L3：HTTP 健康，但 DeepSeek 帳號不可用或餘額低於門檻——問答與批次 LLM 段停擺（或即將）

# ── L3：問答相依檢查的設定 ───────────────────────────────────────────────
# 為什麼需要它：2026-09-02 claude CLI 從 npm 全域改裝成原生安裝，舊路徑下的
# 執行檔消失，web unit 的 PATH drop-in 只指向舊路徑，於是 /api/ask 全數以
# FileNotFoundError: 'claude' 失敗、持續三小時——而 /healthz 只探 DB，本探針全程 ok。
# **/healthz 綠不代表問答可用**，這一段補的就是那個盲區。
#
# 做法刻意不查 systemd、不 spawn claude：讀 web unit 已安裝的 PATH drop-in，
# 逐目錄檢查有沒有可執行的 claude。drop-in 不存在（CI、沒部署的機器）就跳過——
# 「判不出來」不是「壞了」。已知盲點：drop-in 改了但還沒 daemon-reload 時，這裡
# 看到的是檔案、不是 unit 實際載入的值。
HEALTH_DEP_BIN="${HEALTH_DEP_BIN:-claude}"
HEALTH_DEP_DROPIN="${HEALTH_DEP_DROPIN:-/etc/systemd/system/report-mark-web.service.d/path.conf}"

# ── L3：物件儲存檢查的設定 ───────────────────────────────────────────────
# 為什麼需要它：OBJECT_STORAGE_MODE=r2 時缺 key 即 503 不回退，bucket 或憑證出問題時
# 原檔與 PDF 全壞，而 /healthz 只探 DB 照樣綠；在這之前唯一的偵測是每週一次的對帳。
#
# 探測本身由 web 行程做（它才有 R2 憑證與 boto），結果經 /healthz/storage 取得——那支
# 端點只回答本機直連的請求。這裡只認一種訊號：**HTTP 503＝確定連不上**。404（舊版本
# 沒有這支端點）、連不上、逾時、200 都當作「判不出來或正常」，不開事件——與上面
# drop-in 不存在就跳過是同一個原則。設成空字串可整段停用。
HEALTH_STORAGE_URL="${HEALTH_STORAGE_URL-${HEALTH_URL%/}/storage}"

# ── L3：LLM 帳號檢查的設定 ───────────────────────────────────────────────
# 為什麼需要它：claude CLI 已放棄（2026-09-23 OAuth 過期），問答與 sync 的 LLM 段全靠 DeepSeek、
# 沒有備援；402（餘額不足）、401（金鑰失效）、連不上時問答每題失敗、sync 整批 rc=2，/healthz 照樣綠。
# 餘額查詢由 web 行程做（金鑰在它的 .env），結果經 /healthz/llm 取得——同樣只回答本機直連，
# 同樣只認 **HTTP 503**（其餘一律當作判不出來或正常）。503 的本體是 {"llm":"<state>"}，
# state 併進 reason（llm_exhausted、llm_low…；讀不出來就是 llm_unavailable），**不含任何金額**。
# 設成空字串可整段停用。
HEALTH_LLM_URL="${HEALTH_LLM_URL-${HEALTH_URL%/}/llm}"

# 用 127.0.0.1 而非 localhost：uvicorn 綁的是 0.0.0.0（**只有 IPv4**），而 localhost
# 在多數 glibc 設定下會先解析到 ::1，curl 會拿到 connection refused——那是探針自己
# 製造的假故障，且症狀與真故障一模一樣。
emit() {
    printf 'ts=%s component=web probe=local_http status=%s http_code=%s latency_ms=%s attempts=%s reason=%s\n' \
        "$(date -Iseconds)" "$1" "$2" "$3" "$4" "$5"
}

# 回傳空字串＝相依正常或無法判定；非空＝reason（封閉詞彙：dep_missing_<bin>）。
# 只讀檔、只用 shell 內建與 [ -x ]，不呼叫 systemctl——健康路徑不得相依 systemd
# （tests/test_web_health_probe.py::test_healthy_path_never_consults_systemd）。
check_unit_dependency() {
    [ -r "$HEALTH_DEP_DROPIN" ] || return 0
    local line unit_path="" dir
    while IFS= read -r line; do
        case "$line" in
            Environment=PATH=*) unit_path="${line#Environment=PATH=}" ;;
            'Environment="PATH='*) unit_path="${line#Environment=\"PATH=}"; unit_path="${unit_path%\"}" ;;
        esac
    done < "$HEALTH_DEP_DROPIN"
    [ -n "$unit_path" ] || return 0
    local IFS=:
    for dir in $unit_path; do
        [ -n "$dir" ] && [ -x "$dir/$HEALTH_DEP_BIN" ] && return 0
    done
    printf 'dep_missing_%s' "$HEALTH_DEP_BIN"
}

# 回傳空字串＝儲存正常、未啟用或無法判定；非空＝reason（封閉詞彙：storage_unreachable）。
# 只用 curl，不呼叫 systemctl（理由同上）。web 端有快取，這裡每次問都很便宜。
check_storage() {
    [ -n "$HEALTH_STORAGE_URL" ] || return 0
    local scode
    scode="$(curl -s -o /dev/null -w '%{http_code}' --max-time "$HEALTH_TIMEOUT" "$HEALTH_STORAGE_URL" 2>/dev/null)" \
        || return 0
    [ "$scode" = "503" ] && printf 'storage_unreachable'
    return 0
}

# 回傳空字串＝LLM 帳號正常、未啟用或無法判定；非空＝reason（封閉詞彙：llm_<state>，state 只收
# 小寫字母與底線、最長 32 字；其餘一律 llm_unavailable）。只用 curl 與 sed（理由同上）。
check_llm() {
    [ -n "$HEALTH_LLM_URL" ] || return 0
    local out code body state
    out="$(curl -s -w ' %{http_code}' --max-time "$HEALTH_TIMEOUT" "$HEALTH_LLM_URL" 2>/dev/null)" \
        || return 0
    code="${out##* }"
    [ "$code" = "503" ] || return 0
    body="${out% *}"
    state="$(printf '%s' "$body" | sed -n 's/^.*"llm"[[:space:]]*:[[:space:]]*"\([a-z_]\{1,32\}\)".*$/\1/p')"
    printf 'llm_%s' "${state:-unavailable}"
    return 0
}

command -v curl >/dev/null 2>&1 || {
    emit tooling 000 0 0 curl_not_found
    echo "check_web_health: 找不到 curl，無法探測" >&2
    exit "$EXIT_TOOLING"
}

# ── L2：HTTP 探測（含重試）────────────────────────────────────────────────
# 重試做在**單次執行內**而非跨執行：跨執行需要狀態檔，而狀態是 P5 的東西。
# 3 次 × 15 秒＝跨 30 秒，足以吸收 `systemctl restart` 的空窗
# （RestartSec=3 ＋ 實測 bind 僅需 1 秒 ≈ 4–5 秒）。
attempt=0
code=000
elapsed_ms=0
curl_rc=0
while [ "$attempt" -lt "$HEALTH_RETRIES" ]; do
    attempt=$((attempt + 1))
    start_ns=$(date +%s%N)
    # `|| curl_rc=$?` 而非裸呼叫 + 獨立 `curl_rc=$?`：本檔目前只有 `set -u`，獨立成行
    # 仍讀得到；但全 repo 慣例是前者，且**未來有人補上 `set -e` 時，獨立成行會靜默
    # 變成死碼**（2026-07-28 的同步故障就是這樣讓日誌永遠停在同一行）。
    # tests/test_db_backup.py::ShellExitCodeCaptureTests 通掃 scripts/*.sh 守這條。
    curl_rc=0
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time "$HEALTH_TIMEOUT" "$HEALTH_URL" 2>/dev/null)" \
        || curl_rc=$?
    elapsed_ms=$(( ($(date +%s%N) - start_ns) / 1000000 ))
    if [ "$curl_rc" -eq 0 ] && [ "$code" = "200" ]; then
        # L3 三項**全部都查**，一行帶出全部 reason（逗號分隔），退出碼取優先序最高的那一個：
        #   5（claude 不在 PATH）→ 6（R2 連不上）→ 7（LLM 帳號）。
        # 7 刻意排最後（審查 M15）：它包含「餘額低於門檻」，那會持續到儲值為止（可能好幾天），
        # 排前面的話這段期間 5、6 永遠開不了事件；5、6 是本機可以立刻處理的基礎設施故障。
        # 反過來 7 被 5／6 蓋住時，reason 仍在這一行裡（journal 看得到），而 5／6 修好後下一輪
        # 退出碼就變成 7。已知限制：web 只有一個 incident 元件，FIRING 期間退出碼從 6 換成 7
        # 不會另開事件，要等下一則提醒（最多 30 分鐘）才看到新的 exit。
        dep_reason="$(check_unit_dependency)"
        storage_reason="$(check_storage)"
        llm_reason="$(check_llm)"
        reasons=""
        for r in "$dep_reason" "$storage_reason" "$llm_reason"; do
            [ -n "$r" ] && reasons="${reasons:+$reasons,}$r"
        done
        if [ -z "$reasons" ]; then
            emit ok "$code" "$elapsed_ms" "$attempt" ok
            exit "$EXIT_OK"
        fi
        emit degraded "$code" "$elapsed_ms" "$attempt" "$reasons"
        [ -n "$dep_reason" ] && echo "check_web_health: /healthz 正常，但 $HEALTH_DEP_BIN 不在 $HEALTH_DEP_DROPIN 宣告的 PATH 上（問答路徑會以 FileNotFoundError 失敗）" >&2
        [ -n "$storage_reason" ] && echo "check_web_health: /healthz 正常，但物件儲存（R2）連不上（原檔下載與 PDF 檢視會失敗；細節見 web 日誌的「healthz 物件儲存探測失敗」）" >&2
        [ -n "$llm_reason" ] && echo "check_web_health: /healthz 正常，但 LLM 帳號不可用或餘額低於門檻（$llm_reason；細節與金額見 web 日誌的「healthz LLM 狀態」，處置見 docs/production_resilience.md）" >&2
        [ -n "$dep_reason" ] && exit "$EXIT_DEGRADED"
        [ -n "$storage_reason" ] && exit "$EXIT_STORAGE"
        exit "$EXIT_LLM"
    fi
    [ "$attempt" -lt "$HEALTH_RETRIES" ] && sleep "$HEALTH_RETRY_WAIT"
done

# ── 失敗歸因 ──────────────────────────────────────────────────────────────
# curl 退出碼 → reason。**兩種「連不上」都必須算失敗**：
# 2026-08-18 的失效型態是 uvicorn 根本沒綁上（連不上），而不是回 503——
# 而既有文件只描述了後者。實測本機在 mirrored networking 下，連一個沒有
# listener 的埠拿到的是 rc=28（逾時，約 4.8 秒）而不是 rc=7（拒絕），
# 因為 Windows 側是丟棄而非拒絕。兩者都在這裡歸為失敗，不依賴哪一種出現。
case "$curl_rc" in
    0)  reason="http_$code" ;;       # 連上了但不是 200（例如 /healthz 回 503＝DB 不可用）
    7)  reason=connection_refused ;; # 直接拒絕（純 Linux 網路堆疊的典型）
    28) reason=timeout ;;            # 逾時；mirrored networking 下「埠沒開」也走這條
    *)  reason="curl_rc_$curl_rc" ;;
esac

unit_state="$(systemctl is-active "$HEALTH_UNIT" 2>/dev/null || true)"

# ── 啟動寬限 ──────────────────────────────────────────────────────────────
# **三個條件必須同時成立**，缺一不可：
#   (1) unit 目前是 active
#   (2) 進入 active 的時間距今 < HEALTH_GRACE
#   (3) NRestarts == 0
#
# (3) 是這段邏輯的關鍵，少了它寬限會在 crash loop 下永遠成立而抑制告警：
# `Restart=always` ＋ `RestartSec=3` 讓 ActiveEnterTimestamp 每 3 秒更新一次，
# 「距今 < 60 秒」於是恆為真。2026-08-18 的 550 次重啟正是這個形狀——
# 只看時間戳的寬限會完整重現那次「壞了但沒有人知道」。
# NRestarts 在人為 start/restart 後歸零、在自動重啟時累加，正好分辨兩者。
in_grace=no
if [ "$unit_state" = "active" ]; then
    restarts="$(systemctl show "$HEALTH_UNIT" -p NRestarts --value 2>/dev/null || echo 0)"
    entered="$(systemctl show "$HEALTH_UNIT" -p ActiveEnterTimestamp --value 2>/dev/null || true)"
    if [ "${restarts:-0}" = "0" ] && [ -n "$entered" ]; then
        entered_epoch="$(date -d "$entered" +%s 2>/dev/null || echo 0)"
        if [ "$entered_epoch" -gt 0 ]; then
            age=$(( $(date +%s) - entered_epoch ))
            [ "$age" -ge 0 ] && [ "$age" -lt "$HEALTH_GRACE" ] && in_grace=yes
        fi
    fi
fi

if [ "$in_grace" = yes ]; then
    emit grace "$code" "$elapsed_ms" "$attempt" "warmup_${HEALTH_GRACE}s"
    exit "$EXIT_GRACE"
fi

if [ "$unit_state" != "active" ]; then
    emit fail "$code" "$elapsed_ms" "$attempt" "unit_${unit_state:-unknown}"
    echo "check_web_health: $HEALTH_UNIT 不在 active（state=${unit_state:-unknown}）" >&2
    exit "$EXIT_PROCESS"
fi

emit fail "$code" "$elapsed_ms" "$attempt" "$reason"
echo "check_web_health: $HEALTH_URL 探測失敗（reason=$reason http_code=$code curl_rc=$curl_rc）" >&2
exit "$EXIT_HTTP"
