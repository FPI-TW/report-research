#!/usr/bin/env python3
"""執行已到期的帳號刪除排程（report-mark-delete-accounts.timer，每小時）：先寫 tombstone，再刪 DB。

用法：
    uv run python scripts/execute_deletions.py                     # 執行所有到期的排程
    uv run python scripts/execute_deletions.py --dry-run           # 只列出會執行哪些
    uv run python scripts/execute_deletions.py --tombstone-file PATH

管理員在管理頁提出刪除＝立即停用、撤銷 session、排程 24 小時後執行（`accounts.request_deletion`）。
這支在排程到期後，對每一筆：
1. 把 `{user_id, executed_at}` 追加到 `$REPORT_MARK_BACKUP_DIR/account-tombstones.jsonl`（NAS，fsync）；
2. `accounts.execute_deletion`：同一筆交易刪 qa_log、指向它們的 review_state、user_scope、user_session，
   清掉 app_user 的可識別資料，蓋 executed_at，寫稽核（只記數量）。
先寫 tombstone 的理由見 `app/services/tombstones.py`。落點不存在就整批不做（rc=2），不退回本機。

純 SQL、零 LLM。退出碼：0 正常（含沒有到期的）；1 有排程執行失敗（OnFailure 告警）；
2 無法執行（DB 不可用、tombstone 落點不存在）。
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

# 必須早於 app.services.db：它在 import 期就讀 REPORT_MARK_DB_URL。只補還不存在的鍵。
from web.env_loader import load_env_file  # noqa: E402

load_env_file(REPO_ROOT / ".env")

from app.services import accounts, tombstones  # noqa: E402

EXIT_OK, EXIT_FAILED, EXIT_ERROR = 0, 1, 2


async def run(args, api=accounts, now=lambda: datetime.now(timezone.utc)) -> int:
    try:
        path = tombstones.resolve_tombstone_file(args.tombstone_file)
    except tombstones.TombstoneError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_ERROR
    try:
        due = await api.due_deletions()
    except Exception as exc:
        print(f"無法讀取刪除排程（DB 不可用或尚未套 revision 0003）：{exc!r}", file=sys.stderr)
        return EXIT_ERROR
    if not due:
        print("沒有到期的帳號刪除排程")
        return EXIT_OK
    if args.dry_run:
        for uid in due:
            print(f"[dry-run] 會刪除 user_id={uid}")
        return EXIT_OK
    failed = 0
    for uid in due:
        try:
            tombstones.append_tombstone(path, uid, now())
        except OSError as exc:
            print(f"!! 寫不進 tombstone（{path}）：{exc!r}——不刪除 user_id={uid}", file=sys.stderr)
            return EXIT_ERROR
        try:
            executed = await api.execute_deletion(uid, via="batch")
        except Exception as exc:
            failed += 1
            print(f"!! 刪除 user_id={uid} 失敗：{exc!r}（tombstone 已寫，replay_deletions 會再處理）", file=sys.stderr)
            continue
        if executed is None:
            # 兩次查詢之間被別的行程執行了（timer 與手動重疊）；tombstone 重複一行無害。
            print(f"user_id={uid} 已不在到期清單（可能已被另一個行程執行）")
        else:
            print(f"已刪除 user_id={uid}（{executed.isoformat() if hasattr(executed, 'isoformat') else executed}）")
    return EXIT_FAILED if failed else EXIT_OK


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="只列出到期的排程，不寫 tombstone、不刪除")
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
