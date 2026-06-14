"""async SQLAlchemy 引擎 / session。連線參數由環境變數覆寫。"""

from __future__ import annotations

import os

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

DATABASE_URL = os.environ.get(
    "REPORT_MARK_DB_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5436/research",
)

engine = create_async_engine(DATABASE_URL, echo=False, pool_pre_ping=True)
SessionFactory: async_sessionmaker[AsyncSession] = async_sessionmaker(
    engine, expire_on_commit=False
)
