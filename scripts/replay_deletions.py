#!/usr/bin/env python3
"""依 NAS 上的 tombstone 重放帳號刪除：從舊備份還原後，已刪除的使用者資料不會復活。

用法：
    uv run python scripts/replay_deletions.py                  # 每日（report-mark-replay-deletions.timer）
    uv run python scripts/replay_deletions.py --check-only     # 只檢查、不刪（仍以 rc=1 回報殘留）
    uv run python scripts/replay_deletions.py --tombstone-file PATH

**還原備份之後一定要手動跑一次**（`docs/production_resilience.md` 的還原步驟）：備份裡的 `qa_log` 與
`app_user` 是刪除前的樣子，`account_deletion.executed_at` 也回到過去，只有 DB 之外的 tombstone
（`$REPORT_MARK_BACKUP_DIR/account-tombstones.jsonl`，由 `scripts/execute_deletions.py` 寫入）記得。

對 tombstone 裡的每個 user_id 檢查 DB 是否還有：該使用者的 `qa_log`、指向那些問答的 `review_state`、
`user_scope`、`user_session`，或 app_user 又變回可識別／可登入（帳號名稱、密碼雜湊、TOTP、啟用、
`deleted_at`），或還有尚未執行的排程。有任何一項就在同一筆交易重新刪除（`accounts.purge_deleted_user`，
寫稽核 `user.delete_replayed`），並以 rc=1 結束——資料復活本身就是要有人知道的事件（OnFailure 告警）。

純 SQL、零 LLM。退出碼：0 全部乾淨；1 發現殘留（已重新刪除，或 --check-only 只回報）；
2 無法執行（DB 不可用、落點不存在、tombstone 檔壞掉）。
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import asdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

# 必須早於 app.services.db：它在 import 期就讀 REPORT_MARK_DB_URL。只補還不存在的鍵。
from web.env_loader import load_env_file  # noqa: E402

load_env_file(REPO_ROOT / ".env")

from app.services import accounts, tombstones  # noqa: E402

EXIT_OK, EXIT_RESIDUE, EXIT_ERROR = 0, 1, 2


def _describe(residue) -> str:
    parts = [f"{k}={v}" for k, v in asdict(residue).items() if v]
    return "、".join(parts)


async def run(args, api=accounts) -> int:
    try:
        path = tombstones.resolve_tombstone_file(args.tombstone_file)
        user_ids = tombstones.read_tombstones(path)
    except tombstones.TombstoneError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_ERROR
    found = 0
    try:
        for uid in user_ids:
            residue = await api.deletion_residue(uid)
            if residue.clean:
                continue
            found += 1
            print(f"!! 已刪除的 user_id={uid} 在 DB 裡又出現：{_describe(residue)}", file=sys.stderr)
            if not args.check_only:
                counts = await api.purge_deleted_user(uid, via="replay")
                print(f"   已重新刪除：{counts}", file=sys.stderr)
    except Exception as exc:
        print(f"無法檢查或重放（DB 不可用或尚未套 revision 0003）：{exc!r}", file=sys.stderr)
        return EXIT_ERROR
    if found:
        verb = "只回報、未刪除（--check-only）" if args.check_only else "已重新刪除"
        print(f"{len(user_ids)} 個 tombstone 中有 {found} 個在 DB 裡有殘留，{verb}。"
              "通常是從刪除前的備份還原所致；確認還原流程後此告警即可結案。", file=sys.stderr)
        return EXIT_RESIDUE
    print(f"{len(user_ids)} 個 tombstone 全部乾淨（{path}）")
    return EXIT_OK


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check-only", action="store_true", help="只檢查、不重新刪除（仍以 rc=1 回報殘留）")
    ap.add_argument("--tombstone-file",
                    help=f"tombstone 檔（預設 $REPORT_MARK_BACKUP_DIR/{tombstones.TOMBSTONE_NAME}）")
    args = ap.parse_args(argv)

    async def go() -> int:
        try:
            return await run(args)
        finally:
            from app.services import db

            await db.engine.dispose()

    return asyncio.run(go())


if __name__ == "__main__":
    sys.exit(main())
