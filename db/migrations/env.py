"""Alembic 執行環境：連 REPORT_MARK_DB_URL，對「已有資料的庫」做變更前要求逐字確認目標。

規則本體在 app/services/schema_migrations.py（這裡只組裝），理由見該模組 docstring。
刻意不支援 offline（--sql）模式：revision 走 asyncpg 的 simple query protocol 執行多語句
SQL，沒有連線就沒有東西可以產生。
"""
import asyncio
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

# 必須早於 app.services.db：它在 import 期就讀 REPORT_MARK_DB_URL。只補還不存在的鍵（同 web）。
from web.env_loader import load_env_file  # noqa: E402

load_env_file(REPO_ROOT / ".env")

from alembic import context  # noqa: E402
from sqlalchemy import pool, text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from app.services import schema_migrations as sm  # noqa: E402
from app.services.db import DATABASE_URL  # noqa: E402

config = context.config
if config.config_file_name and not config.attributes.get("skip_logging_config"):
    from logging.config import fileConfig

    fileConfig(config.config_file_name, disable_existing_loggers=False)


def _command_name() -> str | None:
    """CLI 下取 alembic 子指令名；程式呼叫（scripts/schema_baseline.py）由呼叫端放進 attributes。"""
    cmd = getattr(getattr(config, "cmd_opts", None), "cmd", None)
    if cmd:
        return cmd[0].__name__
    return config.attributes.get("command")


def _do_run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=None, transaction_per_migration=False)
    with context.begin_transaction():
        context.run_migrations()


async def _run_online() -> None:
    identity = sm.target_identity(DATABASE_URL)
    command = _command_name()
    # 指令名拿不到時當成會寫入：寧可多要一次確認，也不要放過一次沒確認的變更。
    mutating = command is None or command in sm.MUTATING_COMMANDS
    engine = create_async_engine(
        DATABASE_URL, poolclass=pool.NullPool,
        connect_args={"server_settings": dict(sm.MIGRATION_SERVER_SETTINGS)},
    )
    try:
        async with engine.connect() as conn:
            has_state = bool((await conn.execute(text(sm.HAS_STATE_SQL))).scalar())
        print(f"目標 DB：{identity}（{'已有資料' if has_state else '空庫'}；指令：{command or '程式呼叫'}）",
              file=sys.stderr)
        problem = sm.guard_problem(
            identity=identity, has_state=has_state, mutating=mutating, confirm=os.environ.get(sm.CONFIRM_ENV),
        )
        if problem:
            raise SystemExit(problem)
        async with engine.connect() as conn:
            await conn.run_sync(_do_run_migrations)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    raise SystemExit("不支援 offline（--sql）模式：revision 需要真的連線才能執行多語句 SQL。")
asyncio.run(_run_online())
