#!/usr/bin/env bash
# 一次性 bulk ingest 用：匯入期間關閉 Postgres 的耐久性寫入（fsync / full_page_writes /
# synchronous_commit），大幅降低對 vhdx 的 fsync 磁碟 I/O，匯入結束（含失敗 / Ctrl-C）
# 一律自動還原。
#
# 安全性：關閉 fsync 期間若主機斷電 / DB 崩潰，整個 pgdata 可能報廢。
#
# **舊的安全論證「本 DB 為衍生、可由原始研報重建」已經不成立。** 語料層
# （research_report / report_chunk）確實重跑得回來，但同一個叢集裡還住著研報原檔
# 裡沒有的東西：qa_log（問答史、證據帳本、使用者讚／倒讚）、report_takeaway /
# report_signal / report_brief（Sonnet 批次產物）。那些毀了就是毀了，沒有第二份來源。
# 所以本腳本開頭改為硬閘：沒有 24 小時內的備份就不讓跑（見 scripts/db_backup.sh）。
#
# 跑完務必確認已還原（本腳本用 trap 保證還原）。
# ⚠️ 僅供「離線」大量導入：DB 同時對外服務（web 檢索）時不要用，崩潰損毀會影響線上查詢。
#    對外服務時請改用一般 `uv run python scripts/ingest_all.py`（fsync 保留，靠 Defender 排除降 I/O）。
#
# 用法：bash scripts/ingest_lowio.sh [--limit N] [--batch-size 32]   或   make ingest-lowio
set -euo pipefail

DB_CONTAINER="${DB_CONTAINER:-report-mark-postgres}"
DB_NAME="${DB_NAME:-research}"
# docker CLI 偵測收斂在 scripts/_docker_bin.sh（與 db_backup.sh 共用）。它會**實際探
# daemon**——這台機器上 /usr/bin/docker 存在但連不到，而 `command -v` 只看檔案存不存在。
# shellcheck source=scripts/_docker_bin.sh
. "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/_docker_bin.sh"
DOCKER_BIN="${DOCKER_BIN:-$(detect_docker_bin)}"

# ── 硬閘：沒有近期備份就不准關 fsync ───────────────────────────────────────
# 為什麼是硬閘而不是註解裡的提醒：上面那段風險說明從 2026-06 就寫在檔頭，卻沒有
# 任何東西會因此擋下執行。這個閘門要成立必須是可執行的前置條件。
# 刻意檢查「檔案 mtime 在 24h 內」而不是「目錄存在」：備份靜默停掉時目錄照樣在。
BACKUP_MOUNT="${REPORT_MARK_BACKUP_MOUNT:-/mnt/nas-backup}"
BACKUP_DIR="${REPORT_MARK_BACKUP_DIR:-$BACKUP_MOUNT/report-mark-db}"
BACKUP_MAX_AGE_MIN="${BACKUP_MAX_AGE_MIN:-1440}"   # 24 小時

FRESH=""
if [ -d "$BACKUP_DIR" ]; then
  FRESH=$(find "$BACKUP_DIR" -type f -name '*.dump' -mmin "-$BACKUP_MAX_AGE_MIN" -print -quit 2>/dev/null || true)
fi
if [ -z "$FRESH" ]; then
  {
    echo "!! 找不到 ${BACKUP_MAX_AGE_MIN} 分鐘內的 DB 備份（找過 $BACKUP_DIR/**/*.dump）。"
    echo "   本腳本會關掉 fsync / full_page_writes，崩潰即可能整個 pgdata 報廢，"
    echo "   而 qa_log / report_takeaway / report_signal / report_brief 沒有第二份來源。"
    echo "   先跑一次：make db-backup"
    echo "   （確定這座 DB 裡沒有不可重建資料——例如正在從零重建語料——才用"
    echo "     ALLOW_STALE_BACKUP=1 make ingest-lowio 略過本檢查。）"
  } >&2
  if [ "${ALLOW_STALE_BACKUP:-}" = "1" ]; then
    echo "!! ALLOW_STALE_BACKUP=1 → 明知風險仍繼續。" >&2
  else
    exit 1
  fi
else
  echo ">>> 備份新鮮度 OK：$FRESH"
fi

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
