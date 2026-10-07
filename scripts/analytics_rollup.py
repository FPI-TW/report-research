#!/usr/bin/env python3
"""使用分析的每晚彙總（report-mark-analytics-rollup.timer，每天一次；純 SQL，零 LLM）。

用法：
    uv run python scripts/analytics_rollup.py                     # 每晚：覆寫昨天＋補齊即時窗期內還沒彙總的日子
    uv run python scripts/analytics_rollup.py --backfill 365      # 從 qa_log 補最近 365 天（截至昨天）裡缺的日子
    uv run python scripts/analytics_rollup.py --backfill 30 --force   # 同上但已彙總的也重算覆寫
    uv run python scripts/analytics_rollup.py --day 2026-10-06    # 只重算這一天（覆寫）
    uv run python scripts/analytics_rollup.py --dry-run           # 只計算、印出筆數，不寫

把一個台北日曆日的指標（`app/services/analytics.py` 的 `compute_daily`：問答量、延遲、路由分布、忠實度與回饋、
熱門標的／研報／市場、活躍人數）寫進 `research.analytics_daily`。web 的分析頁最近 90 天即時查 `qa_log`，更早的日子讀
這張表——所以每一天都必須在離開即時窗期（`ANALYTICS_LIVE_WINDOW_DAYS`）前彙總過：預設模式除了昨天，也補齊窗期內
缺標記的日子（機器關機錯過幾晚也能追上）。

**冪等**：一天一個交易，先刪掉那天的全部列再寫入（含標記 `rollup.computed`），重跑同一天＝同樣的結果；同一天的
並行執行以 advisory lock 排隊。**回填預設不覆寫已彙總的日子**：`analytics_daily` 沒有 user_id、刪帳後保留
（使用者定案 4），而 qa_log 會被使用者硬刪——重算會讓已保留的匿名彙總縮水。要重算請明確加 `--force` 或 `--day`。
今天（尚未結束）一律不彙總。

不 import 檢索、嵌入或 LLM 模組（`tests/test_analytics.py` 以子行程確認不會載入 torch），所以不在
`tests/test_llm_env_loading.py` 的掃描範圍內（那份 NON_LLM_ENTRIES 只收被間接層判準掃到的入口）。
批次腳本的 logging 無聲，結果一律 print 到 journal。

退出碼：0 正常（含沒有東西要做）；1 計算或寫入失敗（該天已 rollback，之前已 commit 的日子不受影響）、參數錯誤；
2 DB 不可用（unit 的 `SuccessExitStatus=2`：DB 掛掉本身由 web 探針經 P5 告警，這裡不重複通知）。
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

# 必須早於 app.services.db：它在 import 期就讀 REPORT_MARK_DB_URL。只補還不存在的鍵。
from web.env_loader import load_env_file  # noqa: E402

load_env_file(REPO_ROOT / ".env")

from sqlalchemy.exc import InterfaceError, OperationalError  # noqa: E402

from app.services import analytics  # noqa: E402

EXIT_OK, EXIT_FAILED, EXIT_DB = 0, 1, 2
MAX_BACKFILL_DAYS = 3660


class _Parser(argparse.ArgumentParser):
    """參數錯誤回 1（argparse 預設的 2 在這裡是「DB 不可用」，會被 unit 當成功）。"""

    def error(self, message):
        self.print_usage(sys.stderr)
        print(f"{self.prog}: 參數錯誤：{message}", file=sys.stderr)
        raise SystemExit(EXIT_FAILED)


def build_parser() -> argparse.ArgumentParser:
    p = _Parser(description="使用分析的每晚彙總（純 SQL，零 LLM）")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--backfill", type=int, metavar="N", help="補最近 N 天（截至昨天）裡還沒彙總的日子")
    mode.add_argument("--day", type=date.fromisoformat, metavar="YYYY-MM-DD", help="只重算這一天（覆寫）")
    p.add_argument("--force", action="store_true", help="與 --backfill 併用：已彙總的日子也重算覆寫")
    p.add_argument("--dry-run", action="store_true", help="只計算、印出筆數，不寫入")
    return p


def plan_days(args, today: date, live_days: int) -> tuple[list[date], list[date]]:
    """(要覆寫的日子, 只在缺標記時才補的日子)。純函式。"""
    yesterday = today - timedelta(days=1)
    if args.day is not None:
        return [args.day], []
    if args.backfill is not None:
        days = [yesterday - timedelta(days=i) for i in range(args.backfill)][::-1]
        return (days, []) if args.force else ([], days)
    window_start = today - timedelta(days=live_days - 1)
    fill = [window_start + timedelta(days=i) for i in range((yesterday - window_start).days)]
    return [yesterday], fill


async def _run(args, session_factory, params: analytics.Params, today: date) -> int:
    overwrite, fill = plan_days(args, today, params.live_days)
    if fill:
        async with session_factory() as session:
            fill = await analytics.missing_days(session, fill[0], fill[-1])
            await session.rollback()
    done = written = skipped = 0
    for day, force in [(d, True) for d in overwrite] + [(d, False) for d in fill]:
        try:
            res = await analytics.rollup_day(session_factory, day, params, overwrite=force, dry_run=args.dry_run)
        except (OSError, ConnectionError, OperationalError, InterfaceError):
            raise
        except Exception as exc:  # noqa: BLE001 - 該天的交易已隨 session 關閉 rollback
            print(f"{day} 彙總失敗，已 rollback：{exc!r}", file=sys.stderr)
            print(f"完成 {done} 天（{written} 列）後中止", file=sys.stderr)
            return EXIT_FAILED
        if res.skipped:
            skipped += 1
            continue
        done += 1
        written += res.rows
        print(f"{'[dry-run] ' if args.dry_run else ''}{day} {res.rows} 列")
    print(f"{'[dry-run] ' if args.dry_run else ''}完成：彙總 {done} 天、{written} 列；已有彙總而略過 {skipped} 天")
    return EXIT_OK


def run(args, session_factory=None, engine=None, *, params: analytics.Params | None = None,
        today: date | None = None) -> int:
    if session_factory is None:
        from app.services.db import SessionFactory
        from app.services.db import engine as default_engine

        session_factory, engine = SessionFactory, engine or default_engine
    params = params or analytics.params_from_settings()
    today = today or analytics.today()

    async def main() -> int:
        try:
            return await _run(args, session_factory, params, today)
        finally:
            if engine is not None:
                await engine.dispose()

    try:
        return asyncio.run(main())
    except (OSError, ConnectionError, OperationalError, InterfaceError) as exc:
        print(f"DB 不可用，本輪不跑：{exc!r}", file=sys.stderr)
        return EXIT_DB
    except Exception as exc:  # noqa: BLE001 - 連線層以外的失敗不得當成成功
        print(f"執行失敗：{exc!r}", file=sys.stderr)
        return EXIT_FAILED


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    today = analytics.today()
    if args.force and args.backfill is None:
        print("--force 只能與 --backfill 併用（--day 本來就覆寫）", file=sys.stderr)
        return EXIT_FAILED
    if args.backfill is not None and not 1 <= args.backfill <= MAX_BACKFILL_DAYS:
        print(f"--backfill 必須介於 1 與 {MAX_BACKFILL_DAYS}", file=sys.stderr)
        return EXIT_FAILED
    if args.day is not None and args.day >= today:
        print(f"--day 必須早於今天（{today}，台北時間）：今天還沒結束", file=sys.stderr)
        return EXIT_FAILED
    return run(args, today=today)


if __name__ == "__main__":
    sys.exit(main())
