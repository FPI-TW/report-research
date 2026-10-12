"""帳號刪除的 tombstone：DB 之外（NAS）的「這個 user_id 已被刪除」紀錄，一行一筆 JSON。

`scripts/execute_deletions.py` 執行刪除**之前**先寫一行 `{"user_id": ..., "executed_at": ...}`
（不含帳號名稱或任何內容），`scripts/replay_deletions.py` 讀它：從比刪除還舊的備份還原之後，
那位使用者的問答會跟著備份回來，而 DB 裡的 `account_deletion.executed_at` 也一起回到過去——
只有 DB 之外的紀錄記得「這個人已經被刪掉了」。

為什麼先寫 tombstone 再刪：反過來的話，DB 已刪、tombstone 卻寫失敗（NAS 剛好斷線）的那一刻
從備份還原，資料就復活了而且沒有任何東西記得。先寫的代價只是「tombstone 有、DB 還沒刪」，
下一次重放會把它刪掉（並以 rc=1 告警，有人會看到）。撤銷窗口在執行時刻就關閉
（`accounts.cancel_deletion` 拒絕已到期的排程），所以不會有「寫了 tombstone 又被取消」的帳號。

落點預設 `$REPORT_MARK_BACKUP_DIR/account-tombstones.jsonl`（與每日備份、稽核錨點同一個 NAS 落點）。
落點目錄不存在時**失敗而不是改寫本機**：與 pgdata 同一塊磁碟的 tombstone 會跟 DB 一起遺失或一起
被還原，就失去存在的意義（同 `scripts/audit_anchor.py`、`scripts/db_backup.sh` 的理由）。
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path

TOMBSTONE_NAME = "account-tombstones.jsonl"


class TombstoneError(Exception):
    """落點不存在或檔案內容壞掉。訊息給人看。"""


def default_tombstone_file(env: Mapping[str, str] | None = None) -> Path | None:
    env = os.environ if env is None else env
    base = (env.get("REPORT_MARK_BACKUP_DIR") or "").strip()
    return Path(base) / TOMBSTONE_NAME if base else None


def resolve_tombstone_file(explicit: str | None, env: Mapping[str, str] | None = None) -> Path:
    path = Path(explicit) if explicit else default_tombstone_file(env)
    if path is None:
        raise TombstoneError("沒有 tombstone 落點：請設 REPORT_MARK_BACKUP_DIR（/etc/default/report-mark-sync）"
                             "或 --tombstone-file")
    if not path.parent.is_dir():
        raise TombstoneError(f"tombstone 落點 {path.parent} 不存在（NAS 未掛載？）——刻意不改寫到本機")
    return path


def append_tombstone(path: Path, user_id: str, executed_at: datetime) -> None:
    """追加一行並 fsync（寫完才回，呼叫端才去刪 DB）。"""
    rec = {"user_id": str(uuid.UUID(str(user_id))), "executed_at": executed_at.isoformat(timespec="seconds")}
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def read_tombstones(path: Path) -> list[str]:
    """檔案裡的 user_id（去重、保留第一次出現的順序）。檔案不存在＝從未執行過刪除，回空清單。"""
    if not path.exists():
        return []
    seen: dict[str, None] = {}
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            uid = str(uuid.UUID(str(json.loads(line)["user_id"])))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise TombstoneError(f"{path} 第 {n} 行不是有效的 tombstone：{exc}") from exc
        seen.setdefault(uid, None)
    return list(seen)
