#!/usr/bin/env python3
"""安全資料的保留期清除（每日；report-mark-security-retention.timer）：純 SQL，零 LLM。

用法：
    uv run python scripts/security_retention.py

刪兩樣東西（`security_ops.purge_expired`）：
- `research.auth_event` 超過 `AUTH_EVENT_RETENTION_DAYS` 的列。保留期**至少 365 天**（使用者定案 7）：設定層把更小的
  值拉回 365，清除函式裡再夾一次——一年內的登入事件無論如何都不會被這支刪掉。它也納入每日備份。
- `research.user_session` 結束（撤銷或絕對到期）超過 `SESSION_EXPIRED_RETENTION_DAYS`（90）的列。仍有效的 session
  永遠不刪；user_session 從未清理過，第一次執行可能一次刪掉較多列（分批 commit，不是一筆長交易）。

為什麼是獨立的 oneshot 而不是併進探針：探針（`scripts/check_security_health.sh`）刻意是不碰 Python 的 bash、
每 2 分鐘一次、只讀；刪資料是每天一次、要連 DB 寫入的動作，失敗也該走 OnFailure 告警而不是 P5 的狀態機。

刻意不 import 檢索、嵌入或 LLM 模組（tests/test_security_retention.py 守門）：每天跑一次的小工作不該載 torch。

退出碼：0 正常（含沒有可刪的列）；2 無法執行（DB 不可用等）。
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

# 必須早於 app.services.db：它在 import 期就讀 REPORT_MARK_DB_URL。只補還不存在的鍵。
from web.env_loader import load_env_file  # noqa: E402

load_env_file(REPO_ROOT / ".env")

from app.services import security_ops  # noqa: E402

EXIT_OK, EXIT_ERROR = 0, 2


async def run(purge=security_ops.purge_expired) -> int:
    try:
        res = await purge()
    except Exception as exc:
        print(f"安全資料保留期清除失敗（DB 不可用或尚未套 revision 0011）：{exc!r}", file=sys.stderr)
        return EXIT_ERROR
    print(f"已刪除 auth_event {res.auth_events} 列（超過 {res.auth_event_days} 天）、"
          f"user_session {res.sessions} 列（結束超過 {res.session_days} 天）")
    return EXIT_OK


def main(argv=None) -> int:
    argparse.ArgumentParser(description=__doc__.split("\n")[0]).parse_args(argv)
    return asyncio.run(run())


if __name__ == "__main__":
    sys.exit(main())
