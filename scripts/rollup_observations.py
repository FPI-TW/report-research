#!/usr/bin/env python3
"""監控觀測的保留與聚合（report-mark-rollup-observations.timer，每小時；純 SQL，零 LLM）。

用法：
    uv run python scripts/rollup_observations.py                  # 跑一輪
    uv run python scripts/rollup_observations.py --dry-run        # 只印各段待處理的量，不改任何資料
    uv run python scripts/rollup_observations.py --max-slices 50 --batch-size 2000

每輪三步（SQL 與保證在 `app/services/ops_rollup.py`，分段理由在 revision 0007）：
1. 保留期刪除：90 天以前的原始觀測、5 分鐘桶、1 小時桶與 `job_execution`，每批最多 `--batch-size` 列、各自一個交易。
2. 原始觀測 → 5 分鐘桶：早於「整點(現在 − 24h)」的，每片 1 小時、各自一個交易。
3. 5 分鐘桶 → 1 小時桶：早於「整點(現在 − 7d)」的，同上。

每一片的「聚合寫入＋刪除來源」是同一句 SQL（失敗整句回滾、來源原封不動），寫入後再核對筆數，對不上就 rollback
並以 rc=1 結束——不會刪掉沒聚合到的觀測。冪等：搬走的來源就不在了，重跑同一區間是 no-op。
**incident／incident_event 不在這裡處理**（不可重建的事故歷史、列入備份）。

為什麼每小時：原始觀測的保留期是 24 小時，每小時搬一次讓它停在 24–25 小時、每輪只有一兩片（各約 1.2 萬列）
的短交易；每日一次則原始表會累積到 48 小時、而且一次搬一整天。DB 長期停擺後的積壓以 `--max-slices` 分幾輪消化。

同時只跑一份：PostgreSQL advisory lock（session 級，鎖的是被改的那個庫本身，不必在 `data/` 多一個鎖檔；行程被砍、
連線斷掉時自動釋放）。批次腳本的 logging 無聲（只有 web 初始化 logging），結果一律 print 到 journal。

退出碼：0 正常（含沒有東西要處理）；1 核對不符或 SQL 失敗（該步已 rollback，之前已 commit 的步驟不受影響）；
2 DB 不可用；75 另一份正在跑（不跑，不是跑壞）。
**刻意不掛 OnFailure 告警**（與 load_observations 同一個理由）：DB 掛掉時這支每小時失敗一次，而
report-mark-alert@ 沒有去重；DB 掛掉本身已由 web 探針經 P5 帶去重地告警。這支停擺只會讓原始觀測多留幾小時。
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

from sqlalchemy import text  # noqa: E402
from sqlalchemy.exc import InterfaceError, OperationalError  # noqa: E402

from app.services import ops_rollup  # noqa: E402

EXIT_OK, EXIT_FAILED, EXIT_DB, EXIT_LOCKED = 0, 1, 2, 75
# pg_try_advisory_lock 的鍵：固定常數（"rollupob" 的 ASCII），全庫唯一即可。
LOCK_KEY = 0x726F6C6C75706F62


async def _dry_run(session_factory) -> str:
    cut = ops_rollup.cutoffs(datetime.now(timezone.utc))
    lines = [f"界線：raw<{cut.raw_before.isoformat()} 5m<{cut.fine_before.isoformat()} "
             f"purge<{cut.purge_before.isoformat()}"]
    async with session_factory() as session:
        for table, col in ops_rollup.PURGE_TARGETS:
            n = (await session.execute(text(f"SELECT count(*) FROM {table} WHERE {col} < :b"),
                                       {"b": cut.purge_before})).scalar_one()
            lines.append(f"  過期待刪 {table}: {n}")
        for source, before in (("raw", cut.raw_before), ("5m", cut.fine_before)):
            n = await ops_rollup.count_slices(session, source, before)
            lines.append(f"  {source} 待搬 {n} 片")
        await session.rollback()
    return "\n".join(lines)


async def _run(args, engine, session_factory) -> int:
    async with engine.connect() as raw_conn:
        # AUTOCOMMIT：持鎖期間這條連線是 idle、不是 idle in transaction（DB_IDLE_TX_TIMEOUT_MS 打開時不會被砍）
        lock_conn = await raw_conn.execution_options(isolation_level="AUTOCOMMIT")
        got = (await lock_conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": LOCK_KEY})).scalar()
        if not got:
            print("另一份 rollup_observations 正在執行，本次不跑", file=sys.stderr)
            return EXIT_LOCKED
        try:
            if args.dry_run:
                print("[dry-run]\n" + await _dry_run(session_factory))
                return EXIT_OK
            async with session_factory() as session:
                try:
                    stats = await ops_rollup.run_rollup(session, now=datetime.now(timezone.utc),
                                                        max_slices=args.max_slices, batch_size=args.batch_size)
                except Exception as exc:  # noqa: BLE001 - 該步 rollback；之前 commit 的步驟各自完整
                    try:
                        await session.rollback()
                    except Exception:  # noqa: BLE001 - 連線已斷：交易本來就不會 commit
                        pass
                    print(f"聚合或刪除失敗，該步已 rollback：{exc!r}", file=sys.stderr)
                    return EXIT_FAILED
            print(f"完成 {stats.summary()}")
            return EXIT_OK
        finally:
            try:
                await lock_conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": LOCK_KEY})
            except Exception:  # noqa: BLE001 - 連線已斷時鎖隨連線釋放
                pass


def run(args, engine=None, session_factory=None) -> int:
    if engine is None or session_factory is None:
        from app.services.db import SessionFactory
        from app.services.db import engine as default_engine

        engine, session_factory = engine or default_engine, session_factory or SessionFactory

    async def main() -> int:
        try:
            return await _run(args, engine, session_factory)
        finally:
            await engine.dispose()

    try:
        return asyncio.run(main())
    except (OSError, ConnectionError, OperationalError, InterfaceError) as exc:
        print(f"DB 不可用，本輪不跑：{exc!r}", file=sys.stderr)
        return EXIT_DB
    except Exception as exc:  # noqa: BLE001 - 連線層以外的失敗（取鎖、dry-run 查詢）也不得當成成功
        print(f"執行失敗：{exc!r}", file=sys.stderr)
        return EXIT_FAILED


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="監控觀測的保留與聚合（純 SQL，零 LLM）")
    p.add_argument("--dry-run", action="store_true", help="只印各段待處理的量，不改資料")
    p.add_argument("--max-slices", type=int, default=ops_rollup.DEFAULT_MAX_SLICES,
                   help=f"單輪最多搬幾片（每片 1 小時；兩段合計，預設 {ops_rollup.DEFAULT_MAX_SLICES}）")
    p.add_argument("--batch-size", type=int, default=ops_rollup.DEFAULT_BATCH_SIZE,
                   help=f"保留期刪除每批最多幾列（預設 {ops_rollup.DEFAULT_BATCH_SIZE}）")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.max_slices <= 0 or args.batch_size <= 0:
        print("--max-slices 與 --batch-size 必須大於 0", file=sys.stderr)
        return EXIT_FAILED
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
