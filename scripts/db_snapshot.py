#!/usr/bin/env python3
"""DB 統計快照（report-mark-db-snapshot.timer，每小時；只查系統目錄、純 SQL，零 LLM）。

用法：
    uv run python scripts/db_snapshot.py             # 跑一輪：寫逐時快照、補每日彙總、保留期刪除
    uv run python scripts/db_snapshot.py --dry-run   # 只印這一輪會寫的統計，不改任何資料

每輪三步（SQL 與保證在 `app/services/db_insights.py` 的 `run_snapshot`）：
1. 寫一列 `research.db_stat_snapshot`（granularity=hour、taken_at＝整點）：庫大小、連線數、dead tuple 比例、
   未使用索引、`pg_stat_database` 的累計計數器、前 20 大表。同一小時已有就不寫（冪等）。
2. 每天第一次執行時把前一天（台北時間）彙總成一列 granularity=day；漏跑的日子只要逐時列還在也會補上，重跑是 no-op。
3. 刪除逐時超過 30 天、每日超過 400 天的列（`DB_SNAPSHOT_HOURLY_RETENTION_DAYS`／`_DAILY_`，使用者定案 14：
   這只是監控統計，不是 DB dump，所以不備份）。

為什麼這支要連 DB（監控收集器刻意不連）：量的就是 DB 本身，DB 掛掉時本來就量不到；DB 故障的告警仍由
`/healthz`＋P5 負責。所以 DB 不可用是 rc=2（unit 以 SuccessExitStatus=2 放行，理由同 rollup：
report-mark-alert@ 沒有去重，DB 掛掉時每小時一則重複通知），只有 rc=1（SQL 錯誤、bug）走 OnFailure 告警。
系統目錄某一段權限不足或逾時不算失敗：那一段記在 stats.errors、趨勢上是空點（正式環境 EC2 的 RDS 帳號權限較窄）。

刻意不 import 檢索、嵌入、LLM 模組（主機記憶體緊；`tests/test_db_snapshot.py` 以子行程守門）。同時跑兩份無害：
寫入一律 ON CONFLICT DO NOTHING。批次腳本的 logging 無聲（只有 web 初始化 logging），結果一律 print 到 journal。

退出碼：0 正常；1 其他失敗（該輪已 rollback）；2 DB 不可用。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

# 必須早於 app.services.db：它在 import 期就讀 REPORT_MARK_DB_URL。只補還不存在的鍵。
from web.env_loader import load_env_file  # noqa: E402

load_env_file(REPO_ROOT / ".env")

from sqlalchemy.exc import InterfaceError, OperationalError  # noqa: E402

from app.services import db_insights  # noqa: E402

EXIT_OK, EXIT_FAILED, EXIT_DB = 0, 1, 2


async def _run(args, session_factory) -> int:
    now = datetime.now(timezone.utc)
    async with session_factory() as session:
        if args.dry_run:
            overview = await db_insights.collect_overview(session, now=now)
            await session.rollback()
            print("[dry-run] " + json.dumps(db_insights.snapshot_stats(overview), ensure_ascii=False, sort_keys=True))
            return EXIT_OK
        try:
            result = await db_insights.run_snapshot(session, now=now)
            await session.commit()
        except (OSError, ConnectionError, OperationalError, InterfaceError):
            raise
        except Exception as exc:  # noqa: BLE001 - 整輪 rollback，rc=1 走 OnFailure
            try:
                await session.rollback()
            except Exception:  # noqa: BLE001 - 連線已斷：交易本來就不會 commit
                pass
            print(f"快照失敗，本輪已 rollback：{exc!r}", file=sys.stderr)
            return EXIT_FAILED
    print(f"完成 {result.summary()}")
    return EXIT_OK


def run(args, engine=None, session_factory=None) -> int:
    if engine is None or session_factory is None:
        from app.services.db import SessionFactory
        from app.services.db import engine as default_engine

        engine, session_factory = engine or default_engine, session_factory or SessionFactory

    async def main() -> int:
        try:
            return await _run(args, session_factory)
        finally:
            await engine.dispose()

    try:
        return asyncio.run(main())
    except (OSError, ConnectionError, OperationalError, InterfaceError) as exc:
        print(f"DB 不可用，本輪不跑：{exc!r}", file=sys.stderr)
        return EXIT_DB
    except Exception as exc:  # noqa: BLE001 - 連線層以外的失敗不得當成成功
        print(f"執行失敗：{exc!r}", file=sys.stderr)
        return EXIT_FAILED


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="DB 統計快照（只查系統目錄，零 LLM）")
    p.add_argument("--dry-run", action="store_true", help="只印這一輪會寫的統計，不改資料")
    return p


def main(argv: list[str] | None = None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
