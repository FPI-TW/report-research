"""async SQLAlchemy 引擎 / session。

池大小與逾時全部走 `app/config.py` 的 `DB_*` 旋鈕，這裡只負責組裝——選值理由寫在
config 的註解裡（與 PG `max_connections` 的關係、為何 idle_in_transaction 預設關）。

**唯一留在本檔的 os.getenv 是 `REPORT_MARK_DB_URL`**：它早於設定集中化就存在，
改名或搬家會讓現有部署靜默改連預設庫。`DATABASE_URL` 只是模組常數名，不是環境變數鍵。
"""

from __future__ import annotations

import os

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import get_settings

DATABASE_URL = os.environ.get(
    "REPORT_MARK_DB_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5436/research",
)

_S = get_settings()

# asyncpg 把 connect_args["server_settings"] 當作連線 startup 參數送出，值必須是字串。
# 兩個鍵**無條件送出**（含 0＝關）：讓實際行為只取決於本 repo 的設定，而不是 DB 端
# GUC 預設或某人手動改過的 postgresql.conf——否則同一份程式碼在兩台機器上的逾時
# 行為會不一樣，而且沒有任何地方看得出來。
_SERVER_SETTINGS = {
    "statement_timeout": str(_S.db_statement_timeout_ms),
    "idle_in_transaction_session_timeout": str(_S.db_idle_tx_timeout_ms),
}

engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
    pool_size=_S.db_pool_size,
    max_overflow=_S.db_max_overflow,
    pool_timeout=_S.db_pool_timeout,
    pool_recycle=_S.db_pool_recycle,
    connect_args={"server_settings": _SERVER_SETTINGS},
)
SessionFactory: async_sessionmaker[AsyncSession] = async_sessionmaker(
    engine, expire_on_commit=False
)


async def relax_statement_timeout(session: AsyncSession) -> None:
    """把**當前交易**的 statement_timeout 放寬到維運值（0＝不限，見
    `DB_MAINTENANCE_STATEMENT_TIMEOUT_MS`）。呼叫後直到 commit／rollback 為止有效。

    為什麼需要豁免而不是一刀切：引擎層的 statement_timeout 是給線上查詢用的上界，
    但匯入流程最後那句 `ANALYZE research.report_chunk` 是在 70 萬列 × `vector(1024)`
    上抽樣，本來就可能比任何線上查詢久。它又剛好是整條匯入的最後一步——被 timeout
    砍掉時前面的資料都已 commit，症狀只是 planner 統計靜默過期，不會有人發現。

    為什麼用 `SET LOCAL` 而不是 `SET`：連線是池化的。`SET` 是 session 級，而
    SQLAlchemy 預設的 `reset_on_return="rollback"` **不會**還原 GUC，改動會跟著連線
    流到下一個借用者身上（＝把豁免無聲地送給線上查詢）。`SET LOCAL` 只活到本交易
    結束，離開就自動還原。
    """
    ms = int(get_settings().db_maintenance_statement_timeout_ms)
    # SET 不吃 bind 參數，只能字串內插；上一行的 int() 就是這裡唯一的注入防線。
    await session.execute(text(f"SET LOCAL statement_timeout = {ms}"))
