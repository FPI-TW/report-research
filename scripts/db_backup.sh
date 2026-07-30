#!/usr/bin/env bash
# 廷豐智能研報：把「重建不回來」的那幾張表 pg_dump 到 NAS。
#
# 為什麼只備七張表而不是整庫：語料層（research_report / report_chunk）雖然大，
# 但確定重跑得回來——研報原檔還在 NAS，extract → tag → ingest 全程 checkpoint 可續。
# 下面這七張不一樣，它們是「人與 LLM 產生、原檔裡沒有」的東西，刪掉就永遠沒有了：
#
#   research.qa_log            每一次提問、當時的來源與證據帳本、使用者的讚／倒讚
#   research.report_doc        深度研報的 markdown（schema 註解明寫「真相來源」，PDF 由它重建）
#   research.report_rendition  換皮重出的不可變渲染史
#   research.report_takeaway   閱讀頁重點摘錄（Sonnet 批次產物，含錨點）
#   research.report_signal     觀點雷達訊號（Sonnet 批次產物）
#   research.report_run        逐節生成狀態機的流程史
#   research.report_section    同上，逐節內容
#
# 體積小、價值最高 ⇒ 先備這七張。要不要連語料層一起備是另一個（成本）決定，
# 連同已知的 report_id 耦合限制寫在 docs/production_resilience.md「備份與還原」。
#
# 用法：bash scripts/db_backup.sh   或   make db-backup
#      平時由 report-mark-backup.timer 每日觸發。
set -euo pipefail

DB_CONTAINER="${DB_CONTAINER:-report-mark-postgres}"
DB_NAME="${DB_NAME:-research}"
# 本 distro 可能沒有 docker CLI（Docker Desktop WSL integration 關閉）→ fallback 到 docker.exe
# 這一行刻意與 scripts/ingest_lowio.sh 逐字相同（tests/test_db_backup.py 會比對）：
# 兩套偵測邏輯並存的話，只會漂到其中一支能跑、另一支莫名其妙失敗。
# 若本機的 `docker` 存在但連不到 daemon，用 DOCKER_BIN 顯式指定（可寫進
# /etc/default/report-mark-sync，備份 unit 也讀那個檔）。
DOCKER_BIN="${DOCKER_BIN:-$(command -v docker || echo '/mnt/c/Program Files/Docker/Docker/resources/bin/docker.exe')}"

# 備份落點。既有的 /mnt/nas-research 是 `-o ro` 掛載（研報來源刻意唯讀），寫不進去；
# 而「同一個 share 再以 rw 掛第二次」2026-07-30 實測也不行——那組帳號對 `投資研究處`
# 只有讀取權，rw 掛載仍得 EACCES。所以 deploy/systemd/mount-nas-backup 掛的是**另一個
# share**，UNC 與落點都由 /etc/default/report-mark-sync 提供（見該檔的備份段）。
BACKUP_MOUNT="${REPORT_MARK_BACKUP_MOUNT:-/mnt/nas-backup}"
BACKUP_DIR="${REPORT_MARK_BACKUP_DIR:-$BACKUP_MOUNT/report-mark-db}"
MOUNT_HELPER="${REPORT_MARK_BACKUP_MOUNT_HELPER:-/usr/local/sbin/mount-nas-backup}"

# 保留策略：7 日 + 4 週。日備擋「昨天手滑」，週備擋「三週前就壞了、今天才發現」——
# 後者是本專案真實發生過的偵測延遲（同步異常結束，三週後才被看到）。
KEEP_DAILY="${BACKUP_KEEP_DAILY:-7}"
KEEP_WEEKLY="${BACKUP_KEEP_WEEKLY:-4}"

BACKUP_TABLES=(
  research.qa_log
  research.report_doc
  research.report_rendition
  research.report_takeaway
  research.report_signal
  research.report_run
  research.report_section
)

STAMP="$(date +%Y%m%d_%H%M%S)"
DAILY_DIR="$BACKUP_DIR/daily"
WEEKLY_DIR="$BACKUP_DIR/weekly"
# 中繼檔刻意不用 .dump 結尾：保留策略與新鮮度檢查都以 *.dump 為準，
# 半成品絕不能被當成一份可用的備份（那正是備份最惡劣的失敗型態）。
TMP=""

cleanup() {
  # 失敗時把半成品清掉。留著只會讓下次的「最新備份」指向一坨壞檔。
  [ -n "$TMP" ] && [ -e "$TMP" ] && rm -f "$TMP" "$TMP.err"
  return 0
}
trap cleanup EXIT

die() { echo "!! $*" >&2; exit 1; }

# ── 1) 落點必須是真的掛載，而且可寫 ───────────────────────────────────────
# 為什麼掛載不可用要 fail 而不是退回本地：備份的全部價值就在「跟 pgdata 不同一塊磁碟」。
# 靜默寫本地會產出一份看起來成功、實際上跟 pgdata 一起死的備份，而且會餵飽
# scripts/ingest_lowio.sh 的新鮮度閘門——把安全網變成假象比沒有安全網更糟。
if ! mountpoint -q "$BACKUP_MOUNT"; then
  echo ">>> $BACKUP_MOUNT 未掛載，嘗試掛載（drvfs，需 NOPASSWD sudoers）"
  MOUNT_RC=0
  sudo -n "$MOUNT_HELPER" || MOUNT_RC=$?
  [ "$MOUNT_RC" -eq 0 ] || echo ">>> 掛載指令 rc=$MOUNT_RC（下面會再確認一次）"
fi
mountpoint -q "$BACKUP_MOUNT" || die "備份落點 $BACKUP_MOUNT 未掛載 → 中止。\
刻意不退回本地路徑：與 pgdata 同一塊磁碟的「備份」在磁碟壞掉時一起死，\
還會讓 ingest_lowio.sh 的備份新鮮度閘門誤以為有救生索。"

MKDIR_RC=0
mkdir -p "$DAILY_DIR" "$WEEKLY_DIR" || MKDIR_RC=$?
if [ "$MKDIR_RC" -ne 0 ]; then
  # 兩種失敗的處置完全不同，所以訊息要幫人分辨：
  #   Read-only file system（EROFS）＝ Linux 的 mount 旗標在擋 → 改掛載選項有救
  #   Permission denied（EACCES）    ＝ 伺服器端 ACL 在擋   → 換 share／要 NAS 開權限，
  #                                     加 rw 旗標沒有用（2026-07-30 實測過）
  HINT="$(mkdir -p "$BACKUP_DIR" 2>&1 | tail -1)"
  case "$HINT" in
    *"Read-only file system"*|*"唯讀"*)
      WHY="$BACKUP_MOUNT 是唯讀掛載（Linux 旗標）→ 檢查 mount-nas-backup 的選項。" ;;
    *"Permission denied"*|*"拒絕"*)
      WHY="伺服器端不給寫（EACCES）→ 那個 NAS 帳號對這個 share 沒有寫入權。\
換一個寫得進去的 share（改 /etc/default/report-mark-sync 的 NAS_BACKUP_UNC 與 \
REPORT_MARK_BACKUP_DIR），或請 NAS 端開權限。**加 rw 掛載旗標沒有用。**" ;;
    *) WHY="原始錯誤：$HINT" ;;
  esac
  die "無法建立 $BACKUP_DIR（rc=$MKDIR_RC）。$WHY"
fi

# 掛載存在不代表可寫（drvfs 以 ro 掛也會通過 mountpoint 檢查），實際寫一下才算數。
PROBE="$BACKUP_DIR/.write_probe.$$"
PROBE_RC=0
touch "$PROBE" || PROBE_RC=$?
rm -f "$PROBE" 2>/dev/null || true
[ "$PROBE_RC" -eq 0 ] || die "$BACKUP_DIR 不可寫（rc=$PROBE_RC）→ 中止。"

# ── 2) DB 要活著 ──────────────────────────────────────────────────────────
READY_RC=0
"$DOCKER_BIN" exec "$DB_CONTAINER" pg_isready -U postgres >/dev/null 2>&1 || READY_RC=$?
[ "$READY_RC" -eq 0 ] || die "DB 容器 $DB_CONTAINER 不可用（rc=$READY_RC，DOCKER_BIN=$DOCKER_BIN）→ 中止。\
若本機的 docker 連不到 daemon，請設 DOCKER_BIN 指向 docker.exe。"

# ── 3) pg_dump ────────────────────────────────────────────────────────────
TABLE_ARGS=()
for t in "${BACKUP_TABLES[@]}"; do TABLE_ARGS+=(-t "$t"); done

TMP="$DAILY_DIR/.partial-$STAMP"
OUT="$DAILY_DIR/report-mark-critical-$STAMP.dump"

echo ">>> pg_dump ${#BACKUP_TABLES[@]} 張表 → $OUT"
DUMP_RC=0
# -Fc：custom 格式，可選擇性還原、內建壓縮。--no-owner/--no-privileges 讓 dump 能
# 還原進任何角色設定的叢集（還原時常常是臨時 DB，不是原本那個）。
# **絕對不要加 -t（TTY）**：docker exec 配 TTY 會對 stdout 做行尾轉換，把二進位
# dump 悄悄弄壞——檔案照樣產出、大小也合理，直到還原那天才發現。
"$DOCKER_BIN" exec -i "$DB_CONTAINER" \
  pg_dump -U postgres -d "$DB_NAME" -Fc --no-owner --no-privileges "${TABLE_ARGS[@]}" \
  >"$TMP" 2>"$TMP.err" || DUMP_RC=$?
if [ "$DUMP_RC" -ne 0 ]; then
  echo "--- pg_dump stderr ---" >&2
  cat "$TMP.err" >&2 || true
  die "pg_dump 失敗（rc=$DUMP_RC）→ 中止，半成品已清除。"
fi

# ── 4) 驗這份 dump 不是空殼 ───────────────────────────────────────────────
# 備份最惡劣的失敗型態是「檔案在、內容不能用」。兩道最便宜的檢查：
#   a) 檔頭魔數：pg_dump -Fc 的前五個位元組固定是 PGDMP，被截斷或被 stdout 攪過就對不上
#   b) 大小下限：七張表的 schema 本身就不只 1KB，比這小一定是空輸出
MAGIC="$(head -c 5 "$TMP" 2>/dev/null || true)"
[ "$MAGIC" = "PGDMP" ] || die "產出的檔案不是 pg_dump custom 格式（檔頭='$MAGIC'）→ 視為失敗。"
SIZE=0
SIZE="$(stat -c %s "$TMP" 2>/dev/null || echo 0)"
[ "$SIZE" -gt 1024 ] || die "產出的檔案只有 ${SIZE} bytes，視為失敗。"

# 原子上線：驗過才改名成 *.dump。在此之前任何觀察者都看不到這個檔名。
mv "$TMP" "$OUT"
TMP=""
rm -f "$DAILY_DIR/.partial-$STAMP.err" 2>/dev/null || true
echo ">>> 日備完成：$OUT（${SIZE} bytes）"

# ── 5) 週備：本 ISO 週還沒有就留一份 ──────────────────────────────────────
# 用 cp 而非 hardlink：drvfs 的 hardlink 支援不可靠，而且日備輪替砍檔時，
# 「以為連結還在」是完全靜默的資料消失。多佔一份空間換確定性。
WEEK="$(date +%G-W%V)"
WEEKLY_FILE="$WEEKLY_DIR/report-mark-critical-$WEEK.dump"
if [ ! -e "$WEEKLY_FILE" ]; then
  CP_RC=0
  cp "$OUT" "$WEEKLY_FILE" || CP_RC=$?
  if [ "$CP_RC" -eq 0 ]; then
    echo ">>> 週備完成：$WEEKLY_FILE"
  else
    # 週備失敗不該讓已經成功的日備變成整體失敗，但必須留下痕跡。
    echo "!! 週備複製失敗（rc=$CP_RC），日備仍有效" >&2
  fi
fi

# ── 6) 輪替 ───────────────────────────────────────────────────────────────
prune_dir() {
  local dir="$1" keep="$2" victim
  # 檔名內嵌時間戳，字典序即時間序 → 排序後砍掉第 keep+1 名以後。
  find "$dir" -maxdepth 1 -type f -name '*.dump' | sort -r | tail -n +"$((keep + 1))" \
    | while IFS= read -r victim; do
        echo ">>> 清理逾期備份：$(basename "$victim")"
        rm -f "$victim"
      done
}
prune_dir "$DAILY_DIR" "$KEEP_DAILY"
prune_dir "$WEEKLY_DIR" "$KEEP_WEEKLY"

echo ">>> 現況：日備 $(find "$DAILY_DIR" -maxdepth 1 -type f -name '*.dump' | wc -l) 份，\
週備 $(find "$WEEKLY_DIR" -maxdepth 1 -type f -name '*.dump' | wc -l) 份"
echo ">>> 還原步驟見 docs/production_resilience.md「備份與還原」——沒演練過的備份不算備份。"
