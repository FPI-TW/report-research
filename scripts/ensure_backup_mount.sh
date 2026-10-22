#!/usr/bin/env bash
# 寫 NAS 備份落點的 unit（刪帳執行、刪帳重放、稽核錨定）的 ExecStartPre：落點沒掛就先掛上。
#
# 為什麼需要它：NAS 備份落點原本只由每日 03:30 的備份（scripts/db_backup.sh）掛載。WSL 重開後到下次
# 備份之前，這三支每次都以 rc=2「落點不存在」告警——2026-10-22 重開後，刪帳排程就每小時失敗一次。
# 掛載必須在 systemd 的命名空間裡做（互動 shell 裡 `sudo mount-nas-backup` 掛的，服務看不到），
# 由 unit 自己掛正好滿足這一點。
#
# 授權沿用備份已有的 deploy/systemd/report-mark-backup.sudoers（`sudo -n` 掛載輔助程式），不另開權限。
#
# 刻意一律 exit 0：
# - 主機沒裝掛載輔助程式（EC2：沒有 NAS，tombstone 寫本機磁碟）＝不需要掛，直接略過。
# - 掛載失敗只印警告。落點不存在時主程式會以 rc=2 失敗並觸發 OnFailure 告警，訊息也比這裡清楚；
#   在這裡失敗會讓主程式根本不跑，告警只剩一句「ExecStartPre 失敗」。
set -u

HELPER="${REPORT_MARK_BACKUP_MOUNT_HELPER:-/usr/local/sbin/mount-nas-backup}"
MOUNT_POINT="${REPORT_MARK_BACKUP_MOUNT:-/mnt/nas-backup}"

if [ ! -x "$HELPER" ]; then
    echo "ensure_backup_mount: 沒有掛載輔助程式 $HELPER，這台不掛 NAS（略過）"
    exit 0
fi
if mountpoint -q "$MOUNT_POINT"; then
    exit 0
fi

echo "ensure_backup_mount: $MOUNT_POINT 未掛載，嘗試掛載"
rc=0
sudo -n "$HELPER" || rc=$?
if [ "$rc" -ne 0 ] || ! mountpoint -q "$MOUNT_POINT"; then
    echo "ensure_backup_mount: 掛載 $MOUNT_POINT 失敗（rc=$rc）；交給主程式判斷落點（不存在時 rc=2 告警）" >&2
fi
exit 0
