"""Alembic migration 的共用規則（`db/migrations/env.py`、`scripts/schema_baseline.py`、測試共用）。

**為什麼這些規則不寫在 env.py 裡**：env.py 由 alembic 以檔案路徑載入，測試 import 不到；
而守門邏輯（誰可以對哪個庫做變更）必須能被不連 DB 的測試逐條釘住。

四件事：

1. **目標識別**（`target_identity`）：`host:port/dbname`，不含帳密。本機預設庫**是測試環境的真實資料庫**，
   從 worktree 跑 migration 而沒改 `REPORT_MARK_DB_URL`，動到的就是那份真實資料——所以對「已有
   資料的庫」做變更，必須在 `REPORT_MARK_MIGRATE_CONFIRM` 逐字寫出這個識別。空庫（新機器、
   CI）免確認：那裡沒有東西可以弄壞。
2. **baseline 凍結**：revision 0001 就是 `db/schema.sql` 原文，以 SHA-256 釘住。0001 只接受
   空庫；既有庫走「drift 驗證為零 → stamp」，不准用 upgrade 偷渡（那會跳過驗證）。
3. **revision 一律手寫 SQL**（模組常數 `UPGRADE_SQL`）：repo 沒有 ORM model，autogenerate
   沒有東西可比；而 SQL 常數讓 `schema_source_text()` 能把 baseline＋各 revision 串成一份
   原文，供既有的靜態契約測試（索引、生成欄運算式）掃描。
4. **多語句 SQL 的執行**（`run_sql_script`）：asyncpg 的 prepared statement 不接受多個指令，
   SQLAlchemy 的 `text()` 又會把 `:name` 當 bind 參數。改走 asyncpg 的 simple query protocol，
   而且**必須在 SQLAlchemy 已開啟的交易內**——否則 DDL 會在 autocommit 下逐句生效，
   失敗時留下半套 schema。
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"
BASELINE_PATH = REPO_ROOT / "db" / "schema.sql"
BASELINE_REVISION = "0001"
# 改了 db/schema.sql 就會對不上。正確做法是寫新 revision，不是更新這個值（見 db/schema.sql 檔頭）。
BASELINE_SHA256 = "090a727844b2e9817e633aba5e619ddf6576de63e8c0444c8453445e183510c8"

CONFIRM_ENV = "REPORT_MARK_MIGRATE_CONFIRM"
# 額外受保護的目標（逗號分隔的 host:port/dbname）。staging 在自己的環境檔設 RDS 的識別。
PROTECTED_ENV = "REPORT_MARK_PROTECTED_DB_TARGETS"
# 辦公室主機（測試環境）的真實資料庫（report-mark-postgres 容器，host port 5436）。受保護＝stamp 必須先做全庫備份。
DEFAULT_PROTECTED = frozenset({"localhost:5436/research", "127.0.0.1:5436/research"})

# 會寫入 DB 的 alembic 指令。其餘（current、history）唯讀，不需確認。
MUTATING_COMMANDS = frozenset({"upgrade", "downgrade", "stamp"})

# migration 連線的 server settings。statement_timeout 關掉：web 的上界是給線上查詢的，
# 對大表建索引一定超過。lock_timeout 開著：ALTER 拿不到鎖時寧可失敗重來，也不要排在
# 線上查詢後面、同時把後面所有查詢一起堵住。個別 revision 需要更久時自己 SET LOCAL。
MIGRATION_SERVER_SETTINGS = {
    "statement_timeout": "0",
    "lock_timeout": "10s",
    "application_name": "report-mark-migrate",
}

# 「這個庫已經有東西」：research schema 底下有任何物件，或已經被 alembic 接管。
HAS_STATE_SQL = """
SELECT EXISTS (
    SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'research'
) OR to_regclass('public.alembic_version') IS NOT NULL
"""

RESEARCH_HAS_OBJECTS_SQL = """
SELECT EXISTS (
    SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'research'
)
"""


class MigrationGuardError(RuntimeError):
    """守門拒絕。訊息必須告訴操作者下一步該做什麼，而不只是「不行」。"""


def target_identity(url: str) -> str:
    """`postgresql+asyncpg://u:p@Host:5436/research?ssl=…` → `host:5436/research`。

    host 一律小寫、沒寫 port 補 5432；帳密與查詢參數不進識別（它會被印出來、被比對）。
    """
    from sqlalchemy.engine import make_url

    u = make_url(url)
    host = (u.host or "localhost").lower()
    return f"{host}:{u.port or 5432}/{u.database or ''}"


def protected_targets(env=None) -> frozenset[str]:
    env = os.environ if env is None else env
    extra = {s.strip().lower() for s in (env.get(PROTECTED_ENV) or "").split(",") if s.strip()}
    return DEFAULT_PROTECTED | extra


def guard_problem(*, identity: str, has_state: bool, mutating: bool, confirm: str | None) -> str | None:
    """回 None＝放行；否則回給操作者看的拒絕訊息。"""
    if not mutating or not has_state:
        return None
    if (confirm or "").strip() == identity:
        return None
    return (
        f"目標 DB {identity} 已有資料，對它做 migration 必須明確確認。\n"
        f"確認目標無誤後重跑：{CONFIRM_ENV}={identity}（make 指令用 CONFIRM={identity}）。\n"
        "本機預設庫就是生產庫；在 worktree 開發請把 REPORT_MARK_DB_URL 指向 devdb。"
    )


def baseline_sql() -> str:
    """回 baseline 原文；內容與釘住的雜湊不符就拒絕（不准帶著被改過的 baseline 套庫）。"""
    raw = BASELINE_PATH.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != BASELINE_SHA256:
        raise MigrationGuardError(
            f"db/schema.sql 的 SHA-256 是 {digest}，與凍結的 baseline 不符。"
            "schema 變更請寫新的 revision（db/migrations/versions/），不要改 baseline。"
        )
    return raw.decode("utf-8")


def run_sql_script(bind, sql: str) -> None:
    """在 SQLAlchemy 同步介面的連線（alembic 的 op.get_bind()、或 run_sync 拿到的連線）上
    執行多語句 SQL，且保證落在同一個交易裡。"""
    from sqlalchemy.util import await_only

    # 先經 SQLAlchemy 送一句，讓 asyncpg adapter 真的開啟交易（它是第一次 execute 才 BEGIN 的）。
    bind.exec_driver_sql("SELECT 1")
    raw = bind.connection.driver_connection
    if not raw.is_in_transaction():
        raise MigrationGuardError("內部錯誤：多語句 SQL 必須在交易內執行，但連線不在交易中。")
    await_only(raw.execute(sql))


def assert_research_empty(bind) -> None:
    """baseline 只能建在空庫上。既有庫必須走 drift 驗證＋stamp，不能讓 upgrade 跳過驗證。"""
    if bind.exec_driver_sql(RESEARCH_HAS_OBJECTS_SQL).scalar():
        raise MigrationGuardError(
            "research schema 已有物件：baseline（0001）只套在空庫上。既有庫請改走\n"
            "  make schema-check            # 必須零 drift\n"
            "  make schema-stamp-baseline   # 驗證通過才 stamp（受保護的庫還要全庫備份）"
        )


def _script_directory():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    return ScriptDirectory.from_config(Config(str(ALEMBIC_INI)))


def revision_chain(upto: str | None = None) -> list:
    """從 baseline 到 `upto`（預設 head）依序的 revision 腳本物件。"""
    script = _script_directory()
    target = upto or script.get_current_head()
    revs = list(script.walk_revisions(base="base", head=target))
    revs.reverse()  # walk_revisions 是由新到舊
    return revs


def schema_sql_chain(upto: str | None = None) -> list[tuple[str, str]]:
    """[(revision, SQL)]：baseline 是 db/schema.sql，其餘是各 revision 的 UPGRADE_SQL。"""
    out = []
    for rev in revision_chain(upto):
        if rev.revision == BASELINE_REVISION:
            out.append((rev.revision, baseline_sql()))
        else:
            out.append((rev.revision, rev.module.UPGRADE_SQL))
    return out


def schema_source_text() -> str:
    """baseline＋所有 revision 的 SQL 串成一份，給靜態契約測試掃描。

    限制：這是「寫過什麼」而不是「最後長什麼樣」——後面的 revision DROP 掉的索引，在
    baseline 原文裡仍然看得到。要驗實際生效的定義用 DB 契約測試（例如約束 golden）。
    """
    return "\n\n".join(sql for _, sql in schema_sql_chain())
