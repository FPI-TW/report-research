#!/usr/bin/env bash
# 一次性 bulk ingest 用：匯入期間關閉 Postgres 的耐久性寫入（fsync / full_page_writes /
# synchronous_commit），大幅降低對 vhdx 的 fsync 磁碟 I/O，匯入結束（含失敗 / Ctrl-C）
# 一律自動還原。
#
# 安全性：關閉 fsync 期間若主機斷電 / DB 崩潰，研究資料庫可能損毀——但本 DB 為衍生、可由
# 原始研報重建，重跑 ingest 即可。跑完務必確認已還原（本腳本用 trap 保證還原）。
# ⚠️ 僅供「離線」大量導入：DB 同時對外服務（web 檢索）時不要用，崩潰損毀會影響線上查詢。
#    對外服務時請改用一般 `uv run python scripts/ingest_all.py`（fsync 保留，靠 Defender 排除降 I/O）。
#
# 用法：bash scripts/ingest_lowio.sh [--limit N] [--batch-size 32]   或   make ingest-lowio
set -euo pipefail

DB_CONTAINER="${DB_CONTAINER:-report-mark-postgres}"
DB_NAME="${DB_NAME:-research}"
# 本 distro 可能沒有 docker CLI（Docker Desktop WSL integration 關閉）→ fallback 到 docker.exe
DOCKER_BIN="${DOCKER_BIN:-$(command -v docker || echo '/mnt/c/Program Files/Docker/Docker/resources/bin/docker.exe')}"

psqlc() {
  "$DOCKER_BIN" exec -i "$DB_CONTAINER" psql -U postgres -d "$DB_NAME" -v ON_ERROR_STOP=1 "$@"
}

restore_durability() {
  echo ">>> 還原 Postgres 耐久性設定（fsync / full_page_writes / synchronous_commit）..."
  if psqlc -c "ALTER SYSTEM RESET fsync;" \
           -c "ALTER SYSTEM RESET full_page_writes;" \
           -c "ALTER SYSTEM RESET synchronous_commit;" \
           -c "SELECT pg_reload_conf();"; then
    echo ">>> 已還原為預設耐久性設定。"
  else
    echo "!! 還原失敗！請手動執行： make restore-durability" >&2
  fi
}
# 不論正常結束、報錯、或被 Ctrl-C，都會還原
trap restore_durability EXIT

echo ">>> 確認容器存活：$DB_CONTAINER"
"$DOCKER_BIN" exec "$DB_CONTAINER" pg_isready -U postgres >/dev/null

echo ">>> 降低 ingest 期間磁碟 I/O：關閉 fsync / full_page_writes / synchronous_commit"
psqlc -c "ALTER SYSTEM SET fsync=off;" \
      -c "ALTER SYSTEM SET full_page_writes=off;" \
      -c "ALTER SYSTEM SET synchronous_commit=off;" \
      -c "SELECT pg_reload_conf();"

echo ">>> 開始全量 ingest（參數：$*）"
uv run python scripts/ingest_all.py "$@"
echo ">>> ingest 完成。"
