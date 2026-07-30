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


# 兩處檢索路徑用 `SET LOCAL hnsw.iterative_scan`（`store.search_chunks_meta` 與
# `reading.queries.fetch_similar`），該 GUC 自 pgvector 0.8 才存在。0.7 環境不是
# 「功能退化」而是 `unrecognized configuration parameter` ⇒ **每次檢索 500**。
MIN_PGVECTOR_VERSION = (0, 8)


def _parse_extversion(raw: str) -> tuple[int, ...]:
    """'0.8.2' → (0, 8, 2)。非數字段落一律截斷（'0.8.0-rc1' → (0, 8, 0)）。"""
    out: list[int] = []
    for part in str(raw).split("."):
        digits = ""
        for ch in part:
            if not ch.isdigit():
                break
            digits += ch
        if not digits:
            break
        out.append(int(digits))
    return tuple(out)


async def assert_pgvector_version() -> str | None:
    """啟動時檢查 pgvector 版本，太舊即 fail-closed 拋 RuntimeError。回實際版本字串。

    **為什麼是 fail-closed 而不是 fail-open**：太舊的後果不是少個功能，是每次檢索與
    每次開閱讀頁都 500——那種站台「開得起來、登入成功、每個查詢壞掉」的型態，正是
    `/healthz` 存在要對付的那一種。啟動時就死，比讓它上線後逐一 500 好判讀得多。

    **但 DB 連不上時放行**（回 None 並由呼叫端 log）：DB 不可用是 `/healthz` 已經
    處理好的情境（回 503），不該在這裡升級成「App 起不來」。App 起得來、`/healthz`
    誠實回報 503，是比整站 dead 更容易診斷的狀態。

    容器 image 只釘 `pgvector/pgvector:pg16`（沒釘 pgvector 版本），所以「哪個版本」
    取決於 pull 的時間點——這個檢查就是那件事的唯一守門。
    """
    try:
        async with SessionFactory() as session:
            row = (
                await session.execute(
                    text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
                )
            ).first()
    except Exception:
        return None
    if row is None:
        raise RuntimeError(
            "PostgreSQL 沒有安裝 pgvector 擴充——請確認連到的是 pgvector 映像"
            "（`make db`），而不是一般的 postgres。"
        )
    raw = row[0]
    if _parse_extversion(raw) < MIN_PGVECTOR_VERSION:
        want = ".".join(str(n) for n in MIN_PGVECTOR_VERSION)
        raise RuntimeError(
            f"pgvector {raw} 太舊，需要 >= {want}：檢索與閱讀頁會用 "
            "`SET LOCAL hnsw.iterative_scan`，該參數在更舊的版本不存在，"
            "結果是每次查詢都 500。請升級容器映像後重跑 `make schema`。"
        )
    return raw
