"""列出 LLM 批次的跳過名單（research.llm_task_failure）。唯讀、零 LLM。

用法：uv run python scripts/llm_blocked.py [--task title] [--all]

預設只列「這一輪會被跳過」的列（依各列自己記錄的 model 判斷，與 llm_failures.should_skip
同規則）；`--all` 連還在累計、尚未達門檻的也列。

刻意不進 `make db-audit`：這張表只要有被審查擋下的研報就永遠非空，放進稽核會讓稽核
永遠紅燈。要解除某篇：對應批次加 `--retry-blocked` 重跑，或直接 DELETE 那一列。

退出碼：0 查詢成功（不論有沒有列）／2 DB 不可用或表不存在（先跑 make schema）。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from app.services import llm_failures  # noqa: E402
from app.services.db import SessionFactory  # noqa: E402


def select_rows(rows, task: str | None, include_pending: bool) -> list[tuple]:
    """過濾查詢結果。rows 的欄位順序同 llm_failures.LIST_SQL。"""
    out = []
    for row in rows:
        row_task, reason, fail_count, model = row[0], row[1], row[2], row[3]
        if task and row_task != task:
            continue
        blocked = llm_failures.should_skip(llm_failures.FailureRecord(reason, model, fail_count), model)
        if blocked or include_pending:
            out.append((blocked, *row))
    return out


def format_row(item: tuple) -> str:
    blocked, task, reason, fail_count, model, _first_at, last_at, file_hash, file_name = item
    state = "跳過" if blocked else "累計中"
    when = last_at.strftime("%Y-%m-%d %H:%M") if last_at else "-"
    return f"{task}\t{state}\t{reason}×{fail_count}\t{model}\t{when}\t{file_hash}\t{file_name or '-'}"


async def main(task: str | None, include_pending: bool) -> int:
    try:
        async with SessionFactory() as session:
            if not await llm_failures.table_ready(session):
                print(f"{llm_failures.TABLE} 不存在，先跑 make schema", file=sys.stderr)
                return 2
            rows = (await session.execute(text(llm_failures.LIST_SQL))).all()
    except Exception as exc:  # noqa: BLE001
        print(f"DB 不可用：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    items = select_rows(rows, task, include_pending)
    if not items:
        print("（無）")
        return 0
    print("task\t狀態\t原因×連續輪數\tmodel\t最後失敗\tfile_hash\t檔名")
    for item in items:
        print(format_row(item))
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", choices=sorted(llm_failures.TASKS), default=None)
    ap.add_argument("--all", action="store_true", help="連尚未達跳過門檻的也列出")
    args = ap.parse_args()
    raise SystemExit(asyncio.run(main(args.task, args.all)))
