#!/usr/bin/env python3
"""既有資料庫導入 Alembic：嚴格 schema drift 驗證 → （受保護的庫）全庫備份 → stamp baseline。
另有兩個唯讀的日常用途：版本 drift 比對（`check --expect-head`）與每日定期檢查（`scheduled`）。

用法：
    uv run python scripts/schema_baseline.py check [--revision REV] [--json PATH]
    uv run python scripts/schema_baseline.py check --expect-head      # 只比版本，不建暫存庫
    uv run python scripts/schema_baseline.py scheduled [--mode full|version] [--status-file PATH]
    REPORT_MARK_MIGRATE_CONFIRM=<host:port/db> \\
        uv run python scripts/schema_baseline.py stamp --dump-dir DIR [--dump-container NAME]
    REPORT_MARK_MIGRATE_CONFIRM=<host:port/db> \\
        uv run python scripts/schema_baseline.py stamp --no-dump        # 只限非受保護的庫

**連的是 `REPORT_MARK_DB_URL`**（repo 根 `.env`，與 web 同一個鍵）。本機預設就是生產庫。

── 為什麼不能直接 `alembic stamp` ──────────────────────────────────────────────
stamp 只是在 `alembic_version` 寫一個版本號，**不檢查庫長什麼樣**。被 stamp 成 0001、實際卻少
一個欄位或多一條手動索引的庫，之後每個 revision 都建立在錯誤的前提上，而 alembic 永遠
不會察覺。所以 stamp 前先證明零 drift，有任何差異就拒絕。

── 基準怎麼來 ──────────────────────────────────────────────────────────────
在**目標庫所在的同一台伺服器**建一個暫存資料庫，套 baseline（＋到指定 revision 為止的
SQL），再逐項比對兩邊的系統目錄，比完就刪掉暫存庫。用同一台伺服器是刻意的：PostgreSQL
與 pgvector 版本完全相同，`pg_get_constraintdef`／`pg_get_indexdef` 的輸出才能逐字比，不會
因為換了版本而出現假的 drift。代價是需要 CREATEDB 權限、會在目標伺服器短暫多一個空庫
（對目標庫本身唯讀）。暫存庫名唯一、只刪自己建成功的那一個，刪完再確認；清理失敗時整次
檢查作廢（退出碼 2）並印出手動清理指令。要用別台伺服器當基準，設 `--reference-url-env 環境變數名`。

比對範圍：所有非系統 schema 的 extension（含版本）、schema、表／視圖／序列、欄位（型別、
NOT NULL、預設值、生成欄運算式、identity、collation）、約束、索引、trigger、function、自訂
型別、RLS policy。排除 `public.alembic_version` 與 extension 自己的物件。**不比**：擁有者與
權限（備份一律 --no-owner）、註解、統計資訊。欄位的實體順序不同只列為資訊、不算 drift：
既有庫歷年 `ADD COLUMN` 的順序必然與空庫不同，而查詢一律指名欄位，順序沒有語意。

── 受保護的庫要先備份 ─────────────────────────────────────────────────────
受保護＝`app/services/schema_migrations.py` 的 DEFAULT_PROTECTED 加上
`REPORT_MARK_PROTECTED_DB_TARGETS`。對它們 stamp 前必須做一次全庫 `pg_dump -Fc`，而且是
硬性 preflight：可用空間 ≥ 庫大小＋2 GiB、dump 在時限內成功、檔頭是 PGDMP、
`pg_restore -l` 讀得出 TABLE DATA，全部通過才 stamp。日常備份（scripts/db_backup.sh）
只涵蓋七張不可重建的表、不含向量語料，這份是 stamp 專用的額外保險。

── 版本 drift（`check --expect-head`）────────────────────────────────────
完整比對以「DB 自己宣稱的 revision」為基準：被 stamp／upgrade 到 0006 而結構確實是 0006 的庫是零
drift——即使部署的程式期待 0007。這正是最常見的部署失誤（換了程式、忘了 `make schema`；新程式在
舊 schema 上會壞），所以另外比對「程式的 alembic head」與 DB 的 `public.alembic_version`。只讀那一張
表、不建暫存庫、不需要 CREATEDB。判定：

  一致（ok）              DB 的 revision 就是程式的 head。
  落後（behind）          DB 的 revision 在程式的 revision 鏈上、但不是 head：要 `make schema`。
  超前（ahead）           DB 的 revision 不在程式的鏈上：DB 套過比這份程式新的 migration（程式回退、
                          或另一份較新的 checkout 對它 upgrade 過），或套過別的分支的 revision。
                          不必（也無從）分辨，兩者都要人看。
  未接管（unversioned）   沒有 alembic_version 表或表是空的。
  不明確（ambiguous）     alembic_version 有多列（分支），或程式的 revision 鏈有多個 head。

── 每日定期檢查（`scheduled`，report-mark-schema-check.timer）──────────────────
先做版本比對；版本可對應時（一致或落後）再以 DB 的 revision 做完整比對（`--mode version` 只做前者，
給沒有 CREATEDB 的部署，例如 staging）。結果另寫一份 JSON 狀態檔（`--status-file`；預設
`SCHEMA_CHECK_STATUS_FILE`，再預設 `data/schema_check.json`），寫入失敗只警告、不改退出碼（狀態檔
是給管理頁讀的投影，告警走退出碼）。格式見 `build_status_payload`。

退出碼（三個子指令共用）：
  0 成功／零 drift／版本一致
  1 有 drift、版本落後、或 preflight 失敗（**禁止 stamp**）
  2 無法比對、拒絕執行、版本超前／未接管／不明確、**暫存庫清理失敗**
  3 目標 DB 無法連線（只在第一次接觸目標時判定：連線被拒、逾時、DNS、socket 不存在、伺服器
    正在啟動／關閉。帳密錯、庫名錯是設定問題，算 2）
`scheduled` 合併兩段結果時取 2 → 1 → 3 → 0 最前面的；3 只會在連不上目標、整次沒比到任何東西時出現。
unit 以 `SuccessExitStatus=3` 讓「DB 掛了」不告警（理由見 deploy/systemd/report-mark-schema-check.service）。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

# 必須早於 app.services.db：它在 import 期就讀 REPORT_MARK_DB_URL。只補還不存在的鍵。
from web.env_loader import load_env_file  # noqa: E402

load_env_file(REPO_ROOT / ".env")

from app.services import schema_migrations as sm  # noqa: E402
from app.services.db import DATABASE_URL  # noqa: E402

EXIT_OK, EXIT_DRIFT, EXIT_ERROR, EXIT_DB_UNAVAILABLE = 0, 1, 2, 3
DUMP_SPACE_MARGIN = 2 * 1024**3

# 每日檢查（scheduled）的旋鈕：只有這個子指令讀，unit 經 /etc/default/report-mark-sync 提供。
STATUS_FILE_ENV = "SCHEMA_CHECK_STATUS_FILE"
DEFAULT_STATUS_FILE = REPO_ROOT / "data" / "schema_check.json"
MODE_ENV = "SCHEMA_CHECK_MODE"
REFERENCE_URL_ENV_ENV = "SCHEMA_CHECK_REFERENCE_URL_ENV"
MODES = ("full", "version")
STATUS_FORMAT = 1
DEFAULT_DUMP_TIMEOUT_MIN = 90

# ───────────────────────── 系統目錄 ─────────────────────────

_USER_NS = "n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'"


def _not_ext_member(catalog: str, oid_expr: str) -> str:
    return (f"NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.classid = '{catalog}'::regclass "
            f"AND d.objid = {oid_expr} AND d.deptype = 'e')")


CATALOG_QUERIES: dict[str, str] = {
    "extension": "SELECT e.extname, e.extversion || ' @' || n.nspname "
                 "FROM pg_extension e JOIN pg_namespace n ON n.oid = e.extnamespace",
    "schema": f"SELECT n.nspname, '' FROM pg_namespace n WHERE {_USER_NS}",
    "relation": (
        "SELECT n.nspname || '.' || c.relname, "
        "c.relkind::text || ' persistence=' || c.relpersistence::text || ' rls=' || c.relrowsecurity::text "
        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        f"WHERE {_USER_NS} AND c.relkind IN ('r','p','v','m','S','f') AND {_not_ext_member('pg_class', 'c.oid')}"
    ),
    "column": (
        "SELECT n.nspname || '.' || c.relname || '.' || a.attname, "
        "format_type(a.atttypid, a.atttypmod)"
        " || CASE WHEN a.attnotnull THEN ' NOT NULL' ELSE '' END"
        " || COALESCE(' default=' || pg_get_expr(ad.adbin, ad.adrelid), '')"
        " || CASE WHEN a.attgenerated <> '' THEN ' generated=' || a.attgenerated::text ELSE '' END"
        " || CASE WHEN a.attidentity <> '' THEN ' identity=' || a.attidentity::text ELSE '' END"
        " || CASE WHEN a.attcollation <> t.typcollation THEN ' collate=' || co.collname ELSE '' END "
        "FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid "
        "JOIN pg_namespace n ON n.oid = c.relnamespace JOIN pg_type t ON t.oid = a.atttypid "
        "LEFT JOIN pg_attrdef ad ON ad.adrelid = a.attrelid AND ad.adnum = a.attnum "
        "LEFT JOIN pg_collation co ON co.oid = a.attcollation "
        f"WHERE {_USER_NS} AND a.attnum > 0 AND NOT a.attisdropped "
        f"AND c.relkind IN ('r','p','v','m','f') AND {_not_ext_member('pg_class', 'c.oid')}"
    ),
    "constraint": (
        "SELECT n.nspname || '.' || t.relname || '.' || c.conname, "
        "c.contype::text || ' ' || pg_get_constraintdef(c.oid) "
        "FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid JOIN pg_namespace n ON n.oid = t.relnamespace "
        f"WHERE {_USER_NS}"
    ),
    "index": "SELECT n.nspname || '.' || i.relname, pg_get_indexdef(i.oid) "
             "FROM pg_index x JOIN pg_class i ON i.oid = x.indexrelid JOIN pg_namespace n ON n.oid = i.relnamespace "
             f"WHERE {_USER_NS}",
    "sequence": "SELECT schemaname || '.' || sequencename, "
                "data_type::text || ' start=' || start_value || ' inc=' || increment_by || ' min=' || min_value "
                "|| ' max=' || max_value || ' cycle=' || cycle "
                "FROM pg_sequences WHERE schemaname !~ '^pg_' AND schemaname <> 'information_schema'",
    "trigger": "SELECT n.nspname || '.' || c.relname || '.' || t.tgname, pg_get_triggerdef(t.oid) "
               "FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid JOIN pg_namespace n ON n.oid = c.relnamespace "
               f"WHERE {_USER_NS} AND NOT t.tgisinternal",
    "function": (
        "SELECT n.nspname || '.' || p.proname || '(' || pg_get_function_identity_arguments(p.oid) || ')', "
        "p.prokind::text || CASE WHEN p.prokind IN ('f','p','w') THEN ' md5=' || md5(pg_get_functiondef(p.oid)) "
        "ELSE '' END "
        "FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
        f"WHERE {_USER_NS} AND {_not_ext_member('pg_proc', 'p.oid')}"
    ),
    "view": "SELECT schemaname || '.' || viewname, definition FROM pg_views "
            "WHERE schemaname !~ '^pg_' AND schemaname <> 'information_schema' "
            "UNION ALL SELECT schemaname || '.' || matviewname, definition FROM pg_matviews "
            "WHERE schemaname !~ '^pg_' AND schemaname <> 'information_schema'",
    "type": (
        "SELECT n.nspname || '.' || t.typname, t.typtype::text || ' ' || CASE t.typtype "
        "WHEN 'e' THEN (SELECT string_agg(enumlabel, ',' ORDER BY enumsortorder) FROM pg_enum WHERE enumtypid = t.oid) "
        "WHEN 'd' THEN format_type(t.typbasetype, t.typtypmod) || COALESCE(' ' || (SELECT string_agg("
        "pg_get_constraintdef(oid), '; ' ORDER BY conname) FROM pg_constraint WHERE contypid = t.oid), '') "
        "ELSE '' END "
        "FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace "
        f"WHERE {_USER_NS} AND t.typtype IN ('e','d','r','c') "
        "AND (t.typrelid = 0 OR (SELECT relkind FROM pg_class WHERE oid = t.typrelid) = 'c') "
        f"AND {_not_ext_member('pg_type', 't.oid')}"
    ),
    "policy": "SELECT schemaname || '.' || tablename || '.' || policyname, "
              "permissive::text || ' ' || roles::text || ' ' || cmd || ' ' || COALESCE(qual, '') || ' ' "
              "|| COALESCE(with_check, '') FROM pg_policies "
              "WHERE schemaname !~ '^pg_' AND schemaname <> 'information_schema'",
}

# 欄位實體順序：只列為資訊（理由見模組 docstring）。
COLUMN_ORDER_SQL = (
    "SELECT n.nspname || '.' || c.relname, string_agg(a.attname, ',' ORDER BY a.attnum) "
    "FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid JOIN pg_namespace n ON n.oid = c.relnamespace "
    f"WHERE {_USER_NS} AND a.attnum > 0 AND NOT a.attisdropped AND c.relkind IN ('r','p') "
    "GROUP BY 1"
)

# alembic 自己的版本表（與它的主鍵索引 alembic_version_pkc）：一邊有、一邊沒有是預期的，不是 drift。
_EXCLUDED_PREFIX = "public.alembic_version"


def _excluded(key: str) -> bool:
    return key == _EXCLUDED_PREFIX or key.startswith((_EXCLUDED_PREFIX + ".", _EXCLUDED_PREFIX + "_"))


Catalog = dict[str, dict[str, str]]


@dataclass
class CategoryDiff:
    missing: list[str] = field(default_factory=list)  # 基準有、目標沒有
    extra: list[str] = field(default_factory=list)    # 目標有、基準沒有
    changed: list[tuple[str, str, str]] = field(default_factory=list)  # (key, 基準, 目標)

    def empty(self) -> bool:
        return not (self.missing or self.extra or self.changed)


@dataclass
class DriftReport:
    categories: dict[str, CategoryDiff]
    column_order: list[str]

    @property
    def drift_count(self) -> int:
        return sum(len(d.missing) + len(d.extra) + len(d.changed) for d in self.categories.values())


def diff_catalogs(reference: Catalog, target: Catalog,
                  ref_order: dict[str, str] | None = None, tgt_order: dict[str, str] | None = None) -> DriftReport:
    cats: dict[str, CategoryDiff] = {}
    for cat in sorted(set(reference) | set(target)):
        ref = {k: v for k, v in reference.get(cat, {}).items() if not _excluded(k)}
        tgt = {k: v for k, v in target.get(cat, {}).items() if not _excluded(k)}
        d = CategoryDiff(
            missing=sorted(set(ref) - set(tgt)),
            extra=sorted(set(tgt) - set(ref)),
            changed=sorted((k, ref[k], tgt[k]) for k in set(ref) & set(tgt) if ref[k] != tgt[k]),
        )
        cats[cat] = d
    order_notes = []
    for table in sorted(set(ref_order or {}) & set(tgt_order or {})):
        if (ref_order or {})[table] != (tgt_order or {})[table]:
            order_notes.append(table)
    return DriftReport(categories=cats, column_order=order_notes)


def format_report(report: DriftReport, *, identity: str, revision: str, reference_desc: str,
                  drift_verdict: str = "禁止 stamp") -> str:
    lines = [f"schema drift：目標 {identity} 對基準 revision {revision}（{reference_desc}）"]
    for cat, d in report.categories.items():
        if d.empty():
            continue
        lines.append(f"\n[{cat}] 缺 {len(d.missing)}／多 {len(d.extra)}／不同 {len(d.changed)}")
        lines += [f"  - 缺（基準有、目標沒有）{k}" for k in d.missing]
        lines += [f"  + 多（目標有、基準沒有）{k}" for k in d.extra]
        for k, ref, tgt in d.changed:
            lines += [f"  ~ 不同 {k}", f"      基準：{ref}", f"      目標：{tgt}"]
    if report.column_order:
        lines.append(f"\n資訊（不算 drift）：{len(report.column_order)} 張表的欄位實體順序與空庫不同："
                     + "、".join(report.column_order))
    verdict = "零 drift" if report.drift_count == 0 else f"{report.drift_count} 項 drift，{drift_verdict}"
    lines.append(f"\n結論：{verdict}")
    return "\n".join(lines)


def report_to_json(report: DriftReport, **meta) -> dict:
    return {
        **meta,
        "drift_count": report.drift_count,
        "categories": {
            cat: {"missing": d.missing, "extra": d.extra,
                  "changed": [{"key": k, "reference": r, "target": t} for k, r, t in d.changed]}
            for cat, d in report.categories.items() if not d.empty()
        },
        "column_order_differs": report.column_order,
    }


# ───────────────────────── DB 存取 ─────────────────────────

def _engine(url: str, **kw):
    from sqlalchemy import pool
    from sqlalchemy.ext.asyncio import create_async_engine

    return create_async_engine(url, poolclass=pool.NullPool,
                               connect_args={"server_settings": dict(sm.MIGRATION_SERVER_SETTINGS)}, **kw)


async def fetch_catalog(url: str) -> tuple[Catalog, dict[str, str]]:
    from sqlalchemy import text

    eng = _engine(url)
    try:
        async with eng.connect() as conn:
            catalog: Catalog = {}
            for cat, sql in CATALOG_QUERIES.items():
                rows = (await conn.execute(text(sql))).all()
                catalog[cat] = {str(k): str(v) for k, v in rows}
            order = {str(k): str(v) for k, v in (await conn.execute(text(COLUMN_ORDER_SQL))).all()}
            return catalog, order
    finally:
        await eng.dispose()


async def current_revision(url: str) -> str | None:
    from sqlalchemy import text

    eng = _engine(url)
    try:
        async with eng.connect() as conn:
            if not (await conn.execute(text("SELECT to_regclass('public.alembic_version') IS NOT NULL"))).scalar():
                return None
            return (await conn.execute(text("SELECT version_num FROM public.alembic_version"))).scalar()
    finally:
        await eng.dispose()


async def fetch_db_revisions(url: str) -> list[str] | None:
    """`public.alembic_version` 的所有列（正常只有一列）；表不存在回 None。唯讀。"""
    from sqlalchemy import text

    eng = _engine(url)
    try:
        async with eng.connect() as conn:
            if not (await conn.execute(text("SELECT to_regclass('public.alembic_version') IS NOT NULL"))).scalar():
                return None
            rows = (await conn.execute(text("SELECT version_num FROM public.alembic_version ORDER BY 1"))).all()
            return [str(r[0]) for r in rows]
    finally:
        await eng.dispose()


def _asyncpg_connection_errors() -> tuple[type, ...]:
    try:
        from asyncpg import exceptions as ape
    except ImportError:  # pragma: no cover - asyncpg 是必要相依
        return ()
    # 伺服器正在啟動／關閉／復原（57P03）、連線在半途斷掉。
    return (ape.CannotConnectNowError, ape.ConnectionDoesNotExistError)


def is_connection_failure(exc: BaseException) -> bool:
    """「連不上目標」才回 True：連線被拒、逾時、DNS、socket 不存在、伺服器正在啟動或關閉。

    帳密錯（InvalidPasswordError）、庫名錯（InvalidCatalogNameError）**不算**——那是設定錯誤，
    每天重試也不會自己好，必須告警。只在第一次接觸目標時拿來判斷（之後的 OSError 可能是
    別的東西，例如讀不到 db/schema.sql）。

    只追「明確包裝」的鏈（SQLAlchemy 的 `.orig`、`raise … from` 的 `__cause__`），刻意不追
    `__context__`：asyncpg 逐一嘗試多個位址時，前一個位址的 OSError 會掛在帳密錯誤的 context 上，
    追下去就會把「密碼錯」誤判成「DB 掛了」而不告警。
    """
    connection_errors = (OSError, *_asyncpg_connection_errors())
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if isinstance(cur, connection_errors):
            return True
        cur = getattr(cur, "orig", None) or cur.__cause__
    return False


async def database_size(url: str) -> int:
    from sqlalchemy import text

    eng = _engine(url)
    try:
        async with eng.connect() as conn:
            return int((await conn.execute(text("SELECT pg_database_size(current_database())"))).scalar())
    finally:
        await eng.dispose()


class ReferenceCleanupError(RuntimeError):
    """暫存基準庫沒清掉。檢查結果一律作廢（不論比對本身成功與否），並告訴操作者怎麼手動清。"""

    def __init__(self, name: str, server: str, cause: BaseException, original: BaseException | None = None):
        self.name = name
        msg = (f"暫存基準庫 {name}（伺服器 {server}）清理失敗：{cause!r}。本次檢查結果作廢。\n"
               f"請確認後手動清理：DROP DATABASE IF EXISTS \"{name}\" WITH (FORCE);")
        if original is not None:
            msg += f"\n（清理前已先發生錯誤：{original!r}）"
        super().__init__(msg)


def reference_db_name(now: Callable = datetime.now) -> str:
    """唯一暫存庫名：時間＋pid＋亂數。撞名時 CREATE 直接失敗，而且**不會**去刪那個既有的庫。"""
    return f"schema_ref_{now():%Y%m%d%H%M%S}_{os.getpid()}_{secrets.token_hex(4)}"


class _ReferenceOps:
    """建／套／讀／刪暫存基準庫的實際 DB 操作（測試以假物件替換）。"""

    def __init__(self, server_url: str):
        self.server_url = server_url
        self.admin = _engine(server_url, isolation_level="AUTOCOMMIT")

    def _url(self, name: str) -> str:
        from sqlalchemy.engine import make_url

        return make_url(self.server_url).set(database=name).render_as_string(hide_password=False)

    async def create(self, name: str) -> None:
        from sqlalchemy import text

        async with self.admin.connect() as conn:
            await conn.execute(text(f'CREATE DATABASE "{name}"'))

    async def apply(self, name: str, chain: list[tuple[str, str]]) -> None:
        ref = _engine(self._url(name))
        try:
            async with ref.begin() as conn:
                def _apply(bind):
                    for _rev, sql in chain:
                        sm.run_sql_script(bind, sql)
                await conn.run_sync(_apply)
        finally:
            await ref.dispose()

    async def fetch(self, name: str) -> tuple[Catalog, dict[str, str]]:
        return await fetch_catalog(self._url(name))

    async def drop(self, name: str) -> None:
        from sqlalchemy import text

        async with self.admin.connect() as conn:
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))

    async def exists(self, name: str) -> bool:
        from sqlalchemy import text

        async with self.admin.connect() as conn:
            return bool((await conn.execute(
                text("SELECT EXISTS (SELECT 1 FROM pg_database WHERE datname = :n)"), {"n": name})).scalar())

    async def close(self) -> None:
        await self.admin.dispose()


async def build_reference_catalog(server_url: str, revision: str, *, ops=None,
                                  name: str | None = None) -> tuple[Catalog, dict[str, str]]:
    """在 server_url 那台伺服器建唯一暫存庫、套 baseline 到 revision 的 SQL、取目錄，最後一定清理。

    只刪**自己建成功**的那一個；刪完再查一次確認真的不在。清理失敗（含刪了還在）一律拋
    ReferenceCleanupError——即使比對已經完成，結果也作廢：留在生產伺服器上的殘骸要有人處理。
    """
    ops = ops or _ReferenceOps(server_url)
    name = name or reference_db_name()
    chain = sm.schema_sql_chain(revision)
    created = False
    primary: BaseException | None = None
    try:
        await ops.create(name)
        created = True
        await ops.apply(name, chain)
        return await ops.fetch(name)
    except BaseException as exc:
        primary = exc
        raise
    finally:
        cleanup_error: BaseException | None = None
        if created:
            try:
                await ops.drop(name)
                if await ops.exists(name):
                    cleanup_error = RuntimeError("DROP 之後 pg_database 仍查得到它")
            except Exception as exc:
                cleanup_error = exc
        try:
            await ops.close()
        except Exception:
            pass
        if cleanup_error is not None:
            raise ReferenceCleanupError(name, sm.target_identity(server_url), cleanup_error, primary) \
                from cleanup_error


def _reference_server_for(env_name: str | None) -> tuple[str, str]:
    """基準暫存庫建在哪台伺服器。指定的環境變數沒設時拋 ValueError（不默默退回目標伺服器）。"""
    if env_name:
        url = os.environ.get(env_name)
        if not url:
            raise ValueError(f"--reference-url-env {env_name}：該環境變數未設定")
        return url, f"暫存庫建在 {sm.target_identity(url)} 的伺服器"
    return DATABASE_URL, "暫存庫建在目標的同一台伺服器"


def _reference_server(args) -> tuple[str, str]:
    try:
        return _reference_server_for(args.reference_url_env)
    except ValueError as exc:
        raise SystemExit(str(exc)) from None


@dataclass
class DriftOutcome:
    """一次完整比對的結果。status：ok／drift／error／cleanup_failed／skipped。"""

    status: str
    revision: str | None = None
    reference: str = ""
    report: DriftReport | None = None
    message: str = ""

    @property
    def exit_code(self) -> int:
        return {"ok": EXIT_OK, "skipped": EXIT_OK, "drift": EXIT_DRIFT}.get(self.status, EXIT_ERROR)


async def drift_check(revision: str, reference_url_env: str | None) -> DriftOutcome:
    """建基準暫存庫（一定清理）→ 讀目標目錄 → 比對。不拋例外：失敗也是一種結果。"""
    identity = sm.target_identity(DATABASE_URL)
    try:
        server_url, ref_desc = _reference_server_for(reference_url_env)
        reference, ref_order = await build_reference_catalog(server_url, revision)
        target, tgt_order = await fetch_catalog(DATABASE_URL)
    except ReferenceCleanupError as exc:
        return DriftOutcome("cleanup_failed", revision, message=str(exc))
    except Exception as exc:
        return DriftOutcome("error", revision, message=f"無法比對（{identity}）：{exc!r}")
    report = diff_catalogs(reference, target, ref_order, tgt_order)
    return DriftOutcome("ok" if report.drift_count == 0 else "drift", revision, ref_desc, report)


async def run_check(args, *, revision: str | None = None) -> tuple[int, DriftReport | None, str]:
    identity = sm.target_identity(DATABASE_URL)
    _reference_server(args)  # 參數錯誤（環境變數未設）在接觸任何 DB 之前就拒絕
    # 第一次接觸目標：連不上（3）與其他失敗（2）在這裡分流；之後的失敗一律 2。
    try:
        current = await current_revision(DATABASE_URL)
    except Exception as exc:
        if is_connection_failure(exc):
            print(f"目標 DB 無法連線（{identity}）：{exc!r}", file=sys.stderr)
            return EXIT_DB_UNAVAILABLE, None, ""
        print(f"無法比對（{identity}）：{exc!r}", file=sys.stderr)
        return EXIT_ERROR, None, ""
    rev = revision or args.revision or current or sm.BASELINE_REVISION
    outcome = await drift_check(rev, args.reference_url_env)
    if outcome.status == "cleanup_failed":
        print(f"!! 警報：{outcome.message}", file=sys.stderr)
        return EXIT_ERROR, None, ""
    if outcome.report is None:
        print(outcome.message, file=sys.stderr)
        return EXIT_ERROR, None, ""
    report, ref_desc = outcome.report, outcome.reference
    print(format_report(report, identity=identity, revision=rev, reference_desc=ref_desc))
    if getattr(args, "json", None):
        payload = report_to_json(report, target=identity, revision=rev, reference=ref_desc,
                                 checked_at=datetime.now().astimezone().isoformat(timespec="seconds"))
        Path(args.json).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"報告已寫入 {args.json}")
    return (EXIT_OK if report.drift_count == 0 else EXIT_DRIFT), report, rev


# ───────────────────────── 版本 drift ─────────────────────────

VERSION_STATUSES = ("ok", "behind", "ahead", "unversioned", "ambiguous", "db_unavailable", "error")


@dataclass
class VersionResult:
    status: str  # VERSION_STATUSES 之一
    expected_head: str | None
    db_revision: str | None
    pending: list[str] = field(default_factory=list)  # 落後時：還沒套的 revision（由舊到新）
    message: str = ""

    @property
    def exit_code(self) -> int:
        return {"ok": EXIT_OK, "behind": EXIT_DRIFT, "db_unavailable": EXIT_DB_UNAVAILABLE}.get(self.status, EXIT_ERROR)

    def to_json(self) -> dict:
        return {"status": self.status, "expected_head": self.expected_head, "db_revision": self.db_revision,
                "pending": list(self.pending)}


def compare_versions(db_revisions: list[str] | None, *, chain: list[str], heads: list[str]) -> VersionResult:
    """純函式：DB 的 alembic_version 各列 vs 程式的 revision 鏈（由 baseline 到 head）與 head 清單。"""
    if len(heads) != 1:
        return VersionResult("ambiguous", None, None,
                             message=f"程式的 revision 鏈有 {len(heads)} 個 head（{', '.join(heads)}）："
                                     "先把鏈整合成線性")
    head = heads[0]
    if db_revisions is None:
        return VersionResult("unversioned", head, None,
                             message="DB 沒有 public.alembic_version：未被 alembic 接管"
                                     "（既有庫先走 schema-check＋stamp）")
    if not db_revisions:
        return VersionResult("unversioned", head, None, message="DB 的 public.alembic_version 是空的")
    if len(db_revisions) > 1:
        return VersionResult("ambiguous", head, ",".join(db_revisions),
                             message=f"DB 的 alembic_version 有 {len(db_revisions)} 列（{', '.join(db_revisions)}）")
    rev = db_revisions[0]
    if rev == head:
        return VersionResult("ok", head, rev, message=f"版本一致：DB 與程式都在 revision {head}")
    if rev in chain:
        pending = chain[chain.index(rev) + 1:]
        return VersionResult("behind", head, rev, pending,
                             message=f"DB 落後：DB 在 {rev}、程式期待 {head}，尚未套用 {', '.join(pending)}"
                                     "（make schema CONFIRM=…；新程式在舊 schema 上會壞）")
    return VersionResult("ahead", head, rev,
                         message=f"DB 的 revision {rev} 不在這份程式的 revision 鏈上（程式 head 是 {head}）："
                                 "DB 比部署的程式新（程式回退、或較新的 checkout 對它 upgrade 過），"
                                 "或套過別的分支的 revision")


def code_revisions() -> tuple[list[str], list[str]]:
    """（由 baseline 到 head 的 revision 鏈, head 清單）。只讀 db/migrations/，不連 DB。"""
    script = sm._script_directory()
    heads = list(script.get_heads())
    chain = [r.revision for r in sm.revision_chain(heads[0])] if len(heads) == 1 else []
    return chain, heads


async def check_version(url: str, *, fetch=None, revisions=None) -> VersionResult:
    """讀 DB 的 alembic_version 並與程式的 head 比對。不拋例外。"""
    fetch = fetch or fetch_db_revisions
    try:
        chain, heads = (revisions or code_revisions)()
    except Exception as exc:
        return VersionResult("error", None, None, message=f"讀不到程式的 revision 鏈：{exc!r}")
    head = heads[0] if len(heads) == 1 else None
    try:
        db_revs = await fetch(url)
    except Exception as exc:
        identity = sm.target_identity(url)
        if is_connection_failure(exc):
            return VersionResult("db_unavailable", head, None, message=f"目標 DB 無法連線（{identity}）：{exc!r}")
        return VersionResult("error", head, None, message=f"讀不到 DB 的 alembic_version（{identity}）：{exc!r}")
    return compare_versions(db_revs, chain=chain, heads=heads)


def run_expect_head() -> int:
    identity = sm.target_identity(DATABASE_URL)
    result = asyncio.run(check_version(DATABASE_URL))
    print(f"版本比對：目標 {identity}；{result.message}", file=sys.stdout if result.exit_code == 0 else sys.stderr)
    return result.exit_code


# ───────────────────────── 每日定期檢查 ─────────────────────────

# 狀態檔 problems 的詞彙（管理頁依它顯示；改了要同步改讀取端）。
PROBLEM_CODES = (
    "db_unavailable", "version_behind", "version_ahead", "version_unversioned", "version_ambiguous",
    "schema_drift", "reference_cleanup_failed", "check_error",
)
_VERSION_PROBLEM = {"db_unavailable": "db_unavailable", "behind": "version_behind", "ahead": "version_ahead",
                    "unversioned": "version_unversioned", "ambiguous": "version_ambiguous", "error": "check_error"}
_DRIFT_PROBLEM = {"drift": "schema_drift", "cleanup_failed": "reference_cleanup_failed", "error": "check_error"}


def combine_exit_codes(*codes: int) -> int:
    """2（無法比對／清理失敗）→ 1（drift／落後）→ 3（連不上）→ 0。"""
    for rc in (EXIT_ERROR, EXIT_DRIFT, EXIT_DB_UNAVAILABLE):
        if rc in codes:
            return rc
    return EXIT_OK


def summarize(version: VersionResult, drift: DriftOutcome) -> tuple[int, list[str]]:
    problems: list[str] = []
    for code in (_VERSION_PROBLEM.get(version.status), _DRIFT_PROBLEM.get(drift.status)):
        if code and code not in problems:
            problems.append(code)
    return combine_exit_codes(version.exit_code, drift.exit_code), problems


def build_status_payload(*, mode: str, target: str, version: VersionResult, drift: DriftOutcome,
                         checked_at: datetime, duration_s: float) -> dict:
    """狀態檔內容（format 1）。只有識別（host:port/db）與物件名，不含帳密。

    {
      "format": 1, "checked_at": ISO-8601（含時區）, "duration_s": 秒, "mode": "full"|"version",
      "target": "host:port/db", "exit_code": 0|1|2|3, "alert": exit_code 是否會告警（1、2）,
      "problems": PROBLEM_CODES 的子集（依發現順序）, "message": 給人看的一行,
      "version": {"status", "expected_head", "db_revision", "pending": [...]},
      "drift": {"status": ok|drift|error|cleanup_failed|skipped, "revision", "reference",
                "drift_count", "categories": {類別: {missing, extra, changed}}, "column_order_differs": [...],
                "message"}
    }
    """
    rc, problems = summarize(version, drift)
    drift_json: dict = {"status": drift.status, "revision": drift.revision, "reference": drift.reference,
                        "message": drift.message}
    if drift.report is not None:
        full = report_to_json(drift.report)
        drift_json.update(drift_count=full["drift_count"], categories=full["categories"],
                          column_order_differs=full["column_order_differs"])
    else:
        drift_json.update(drift_count=None, categories={}, column_order_differs=[])
    if not problems:
        message = f"正常：revision {version.db_revision}" + ("、零 drift" if drift.status == "ok" else "（只比版本）")
    else:
        parts = [version.message] if version.status != "ok" else []
        if drift.status == "drift" and drift.report is not None:
            parts.append(f"{drift.report.drift_count} 項 schema drift（基準 revision {drift.revision}）")
        elif drift.status in ("error", "cleanup_failed"):
            parts.append(drift.message)
        message = "；".join(parts)
    return {
        "format": STATUS_FORMAT,
        "checked_at": checked_at.isoformat(timespec="seconds"),
        "duration_s": round(duration_s, 1),
        "mode": mode,
        "target": target,
        "exit_code": rc,
        "alert": rc in (EXIT_DRIFT, EXIT_ERROR),
        "problems": problems,
        "message": message,
        "version": version.to_json(),
        "drift": drift_json,
    }


def status_file_path(arg: str | None) -> Path:
    return Path(arg or os.environ.get(STATUS_FILE_ENV) or DEFAULT_STATUS_FILE)


def write_status_file(path: Path, payload: dict) -> bool:
    """原子寫入（同目錄暫存檔＋rename）。失敗只警告：告警走退出碼，狀態檔只是給管理頁讀的投影。
    刻意不建立上層目錄：落點不存在多半是設定錯了，而不是該替它建一個。"""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)
        return True
    except OSError as exc:
        print(f"警告：狀態檔寫不進 {path}（{exc}）；檢查結果與退出碼不受影響。", file=sys.stderr)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return False


def cmd_scheduled(args, *, version_check=None, drift=None, now: Callable = datetime.now,
                  clock: Callable = time.monotonic) -> int:
    """每日定期檢查：版本比對 →（full 模式、版本可對應時）完整比對 → 狀態檔 → 退出碼。"""
    mode = (args.mode or os.environ.get(MODE_ENV) or "full").strip()
    if mode not in MODES:
        print(f"拒絕：--mode／{MODE_ENV} 只接受 {'、'.join(MODES)}，收到 {mode!r}", file=sys.stderr)
        return EXIT_ERROR
    reference_url_env = args.reference_url_env or os.environ.get(REFERENCE_URL_ENV_ENV) or None
    version_check = version_check or (lambda: asyncio.run(check_version(DATABASE_URL)))
    drift = drift or (lambda rev: asyncio.run(drift_check(rev, reference_url_env)))
    identity = sm.target_identity(DATABASE_URL)
    started, t0 = now().astimezone(), clock()

    version = version_check()
    print(f"版本比對：目標 {identity}；{version.message}")
    if mode == "version":
        outcome = DriftOutcome("skipped", message="版本模式（--mode version）：不做完整比對")
    elif version.status == "db_unavailable":
        outcome = DriftOutcome("skipped", message="目標 DB 無法連線，未做完整比對")
    elif version.status in ("ok", "behind"):
        outcome = drift(version.db_revision)
        if outcome.report is not None:
            print(format_report(outcome.report, identity=identity, revision=outcome.revision or "?",
                                reference_desc=outcome.reference, drift_verdict="需要人處理"))
        elif outcome.status == "cleanup_failed":
            print(f"!! 警報：{outcome.message}", file=sys.stderr)
        else:
            print(outcome.message, file=sys.stderr)
    else:
        outcome = DriftOutcome("skipped", message="DB 的版本對應不到程式的 revision 鏈，無從建立比對基準")

    payload = build_status_payload(mode=mode, target=identity, version=version, drift=outcome,
                                   checked_at=started, duration_s=clock() - t0)
    path = status_file_path(args.status_file)
    if write_status_file(path, payload):
        print(f"狀態檔已寫入 {path}")
    rc = payload["exit_code"]
    print(f"結論：exit={rc} problems={','.join(payload['problems']) or '-'}",
          file=sys.stdout if rc == EXIT_OK else sys.stderr)
    return rc


# ───────────────────────── 全庫備份 preflight ─────────────────────────

@dataclass
class DumpResult:
    ok: bool
    message: str
    path: Path | None = None
    size: int = 0
    seconds: float = 0.0


def _dump_commands(url: str, container: str | None, docker_bin: str) -> tuple[list[str], list[str], dict]:
    """回 (pg_dump 指令, pg_restore -l 指令前綴, 環境)。祕密走環境變數，不進 argv。"""
    from sqlalchemy.engine import make_url

    u = make_url(url)
    common = ["-Fc", "--no-owner", "--no-privileges"]
    if container:
        dump = [docker_bin, "exec", "-i", container, "pg_dump", "-U", u.username or "postgres",
                "-d", u.database or "", *common]
        restore = [docker_bin, "exec", "-i", container, "pg_restore", "-l"]
        return dump, restore, dict(os.environ)
    env = dict(os.environ)
    env.update({"PGHOST": u.host or "localhost", "PGPORT": str(u.port or 5432),
                "PGUSER": u.username or "", "PGDATABASE": u.database or ""})
    if u.password:
        env["PGPASSWORD"] = u.password
    ssl = u.query.get("ssl")
    if isinstance(ssl, str) and ssl:
        env["PGSSLMODE"] = ssl
    return ["pg_dump", *common], ["pg_restore", "-l"], env


def full_dump_preflight(url: str, dump_dir: Path, *, db_size: int, container: str | None,
                        docker_bin: str = "docker", timeout_s: float = DEFAULT_DUMP_TIMEOUT_MIN * 60,
                        runner: Callable = subprocess.run, disk_usage: Callable = shutil.disk_usage,
                        now: Callable = datetime.now) -> DumpResult:
    """全部通過才回 ok。任何一步失敗都清掉半成品並說明原因。"""
    if not dump_dir.is_dir():
        return DumpResult(False, f"備份目錄不存在：{dump_dir}")
    free = disk_usage(dump_dir).free
    need = db_size + DUMP_SPACE_MARGIN
    if free < need:
        return DumpResult(False, f"空間不足：{dump_dir} 可用 {free / 1024**3:.1f} GiB，"
                                 f"需要 ≥ 庫大小 {db_size / 1024**3:.1f} GiB＋2 GiB 餘裕")
    identity = sm.target_identity(url).replace(":", "-").replace("/", "-")
    stamp = now().strftime("%Y%m%dT%H%M%S")
    tmp = dump_dir / f".partial-full-{identity}-{stamp}.dump"
    final = dump_dir / f"report-mark-full-{identity}-{stamp}.dump"
    dump_cmd, restore_cmd, env = _dump_commands(url, container, docker_bin)

    def fail(msg: str) -> DumpResult:
        tmp.unlink(missing_ok=True)
        return DumpResult(False, msg)

    t0 = time.monotonic()
    try:
        with open(tmp, "wb") as out:
            res = runner(dump_cmd, stdout=out, stderr=subprocess.PIPE, env=env, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return fail(f"pg_dump 超過時限 {timeout_s / 60:.0f} 分鐘，已中止")
    except OSError as exc:
        return fail(f"無法執行 pg_dump：{exc}")
    seconds = time.monotonic() - t0
    if res.returncode != 0:
        err = (res.stderr or b"").decode("utf-8", "replace").strip()[-2000:]
        return fail(f"pg_dump 失敗（rc={res.returncode}）：{err}")
    with open(tmp, "rb") as fh:
        magic = fh.read(5)
    size = tmp.stat().st_size
    if magic != b"PGDMP":
        return fail(f"產出的檔案不是 pg_dump custom 格式（檔頭={magic!r}）")
    if size <= 1024:
        return fail(f"產出的檔案只有 {size} bytes")
    try:
        with open(tmp, "rb") as fh:
            if container:
                lst = runner(restore_cmd, stdin=fh, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
                             timeout=600)
            else:
                lst = runner([*restore_cmd, str(tmp)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
                             timeout=600)
    except (subprocess.TimeoutExpired, OSError) as exc:
        return fail(f"pg_restore -l 無法完成：{exc}")
    listing = (lst.stdout or b"").decode("utf-8", "replace")
    if lst.returncode != 0 or "TABLE DATA" not in listing:
        err = (lst.stderr or b"").decode("utf-8", "replace").strip()[-2000:]
        return fail(f"pg_restore -l 讀不出內容（rc={lst.returncode}）：{err}")
    tmp.rename(final)
    return DumpResult(True, f"全庫備份完成：{final}（{size / 1024**3:.2f} GiB，{seconds:.0f} 秒）",
                      path=final, size=size, seconds=seconds)


# ───────────────────────── stamp ─────────────────────────

def alembic_stamp(revision: str) -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(sm.ALEMBIC_INI))
    cfg.attributes["command"] = "stamp"
    command.stamp(cfg, revision)


def cmd_stamp(args, *, check=None, stamp=None, dump=None, size_of=None, revision_of=None) -> int:
    """依序：確認目標 → 狀態檢查 → 零 drift → （受保護）全庫備份 → stamp → 驗證。"""
    check = check or (lambda: asyncio.run(run_check(args, revision=sm.BASELINE_REVISION)))
    stamp = stamp or alembic_stamp
    dump = dump or full_dump_preflight
    size_of = size_of or (lambda: asyncio.run(database_size(DATABASE_URL)))
    revision_of = revision_of or (lambda: asyncio.run(current_revision(DATABASE_URL)))

    identity = sm.target_identity(DATABASE_URL)
    protected = identity in sm.protected_targets()
    if (os.environ.get(sm.CONFIRM_ENV) or "").strip() != identity:
        print(f"拒絕：stamp 必須以 {sm.CONFIRM_ENV}={identity} 確認目標。", file=sys.stderr)
        return EXIT_ERROR
    if args.no_dump and protected:
        print(f"拒絕：{identity} 是受保護的庫，stamp 前必須全庫備份（--dump-dir），不接受 --no-dump。",
              file=sys.stderr)
        return EXIT_ERROR
    if not args.no_dump and not args.dump_dir:
        print("拒絕：請指定 --dump-dir（全庫備份落點），非受保護的庫才可用 --no-dump 明確略過。", file=sys.stderr)
        return EXIT_ERROR

    current = revision_of()
    if current == sm.BASELINE_REVISION:
        print(f"{identity} 已經是 revision {current}，不需要 stamp。")
        return EXIT_OK
    if current is not None:
        print(f"拒絕：{identity} 已被 alembic 接管（revision {current}），baseline stamp 只用於未接管的既有庫。",
              file=sys.stderr)
        return EXIT_ERROR

    rc, _report, _rev = check()
    if rc != EXIT_OK:
        print("禁止 stamp：drift 不為零或無法比對。先修正差異（寫給該庫的 SQL），再重跑。", file=sys.stderr)
        return rc

    if not args.no_dump:
        result = dump(DATABASE_URL, Path(args.dump_dir), db_size=size_of(), container=args.dump_container,
                      docker_bin=os.environ.get("DOCKER_BIN") or "docker", timeout_s=args.dump_timeout_min * 60)
        print(result.message, file=sys.stdout if result.ok else sys.stderr)
        if not result.ok:
            print("禁止 stamp：全庫備份 preflight 未通過。", file=sys.stderr)
            return EXIT_DRIFT

    sys.stdout.flush()  # alembic 的 log 走 stderr；先送出前面的報告，輸出順序才符合因果
    stamp(sm.BASELINE_REVISION)
    after = revision_of()
    if after != sm.BASELINE_REVISION:
        print(f"stamp 後讀回的 revision 是 {after!r}，不是 {sm.BASELINE_REVISION}。", file=sys.stderr)
        return EXIT_ERROR
    print(f"完成：{identity} 已 stamp 為 revision {after}。")
    return EXIT_OK


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("check", "stamp"):
        p = sub.add_parser(name)
        p.add_argument("--reference-url-env", help="以此環境變數的連線字串所在伺服器建基準暫存庫（預設：目標同一台）")
        p.add_argument("--json", help="另把完整報告寫成 JSON（P0-ops 留存放行證據用）")
        if name == "check":
            p.add_argument("--revision", help="基準 revision（預設：目標已 stamp 的版本，未接管則為 baseline）")
            p.add_argument("--expect-head", action="store_true",
                           help="只比對 DB 的 alembic_version 與程式的 head（唯讀、不建暫存庫）："
                                "0 一致／1 DB 落後／2 超前或無法判斷／3 DB 無法連線")
        else:
            p.add_argument("--dump-dir", help="全庫 pg_dump -Fc 的落點目錄")
            p.add_argument("--dump-container", help="以 docker exec 在此容器內執行 pg_dump／pg_restore")
            p.add_argument("--dump-timeout-min", type=float, default=DEFAULT_DUMP_TIMEOUT_MIN)
            p.add_argument("--no-dump", action="store_true", help="略過全庫備份（受保護的庫不接受）")
    p = sub.add_parser("scheduled", help="每日定期檢查（report-mark-schema-check.timer）：版本＋完整比對＋狀態檔")
    p.add_argument("--mode", help=f"full（預設）或 version（只比版本，給沒有 CREATEDB 的部署）；預設讀 {MODE_ENV}")
    p.add_argument("--reference-url-env",
                   help=f"同 check；預設讀 {REFERENCE_URL_ENV_ENV}（staging 以 master 帳號建基準暫存庫時用）")
    p.add_argument("--status-file", help=f"狀態檔路徑；預設 {STATUS_FILE_ENV}，再預設 data/schema_check.json")
    args = ap.parse_args(argv)
    if args.cmd == "scheduled":
        return cmd_scheduled(args)
    if args.cmd == "check":
        if args.expect_head:
            if args.revision or args.json or args.reference_url_env:
                print("拒絕：--expect-head 只比版本，不與 --revision／--json／--reference-url-env 並用。",
                      file=sys.stderr)
                return EXIT_ERROR
            return run_expect_head()
        args.revision = getattr(args, "revision", None)
        rc, _, _ = asyncio.run(run_check(args))
        return rc
    args.revision = None
    return cmd_stamp(args)


if __name__ == "__main__":
    sys.exit(main())
