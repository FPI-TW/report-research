# scripts/_docker_bin.sh —— 供 shell 腳本 `source` 的 docker CLI 偵測。
#
# **為什麼不能用 `command -v docker`**：這台機器上 `/usr/bin/docker` **存在但連不到
# daemon**（Docker Desktop 的 WSL integration 沒開給這個 distro），而 `command -v`
# 只看檔案存不存在。2026-07-30 實測：
#
#   /usr/bin/docker                                              → 連不到
#   /mnt/c/Program Files/Docker/Docker/resources/bin/docker.exe   → daemon OK
#
# 於是 `db_backup.sh` 挑到那支不能用的、在 `pg_isready` 就死掉，而錯誤訊息叫人「請設
# DOCKER_BIN 指向 docker.exe」——一個本來可以自己偵測出來的東西，變成每台機器都要
# 人工設定一次的隱性前置條件。`Makefile:15` 的 `DOCKER :=` 從一開始就用實際探測，
# 只有這兩支 shell 用了弱版；本檔把它們收斂到同一套邏輯。
#
# 探測本身要花 1-3 秒（`docker.exe` 經 WSL interop 更慢），所以刻意只在腳本啟動時
# 呼叫一次、把結果存進 DOCKER_BIN，不要放進迴圈。
#
# 呼叫端用法：
#   . "$(dirname "$0")/_docker_bin.sh"
#   DOCKER_BIN="${DOCKER_BIN:-$(detect_docker_bin)}"
# 顯式的 DOCKER_BIN（環境變數或 /etc/default/report-mark-sync）一律優先——探測是
# 便利功能，不是要奪走覆寫權。

# 回傳第一個「daemon 真的連得上」的 docker 執行檔。
# 全部探不到時回 `docker`，讓後續指令自己吐出真正的 daemon 錯誤——這裡不該把
# 「連不到 daemon」偽裝成「找不到指令」，那兩件事的處置不同。
# 候選清單（依序試）。`DOCKER_BIN_CANDIDATES` 可用 `:` 分隔覆寫——存在的理由有兩個：
# 讓測試能餵入受控的假 bin（否則測試結果會取決於這台機器有沒有 docker），以及讓
# 裝在非標準位置的環境不必改程式。**路徑含 `:` 的話這個覆寫用不了**，那種情況請直接
# 設 DOCKER_BIN。
_docker_bin_candidates() {
  if [ -n "${DOCKER_BIN_CANDIDATES:-}" ]; then
    printf '%s' "$DOCKER_BIN_CANDIDATES" | tr ':' '\n'
    return
  fi
  printf '%s\n' \
    docker \
    '/mnt/c/Program Files/Docker/Docker/resources/bin/docker.exe' \
    docker.exe
}

detect_docker_bin() {
  local cand
  while IFS= read -r cand; do
    [ -n "$cand" ] || continue
    # 用 `docker info` 而非 `--version`：後者不碰 daemon，正是誤判的來源。
    if command -v "$cand" >/dev/null 2>&1 && "$cand" info >/dev/null 2>&1; then
      printf '%s' "$cand"
      return 0
    fi
  done <<EOF
$(_docker_bin_candidates)
EOF
  printf 'docker'
}
