#!/usr/bin/env python3
"""既有資料庫導入 Alembic：嚴格 schema drift 驗證 → （受保護的庫）全庫備份 → stamp baseline。

用法：
    uv run python scripts/schema_baseline.py check [--revision REV] [--json PATH]
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

退出碼：0 成功／零 drift；1 有 drift 或 preflight 失敗（**禁止 stamp**）；2 無法比對或拒絕執行。
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

EXIT_OK, EXIT_DRIFT, EXIT_ERROR = 0, 1, 2
DUMP_SPACE_MARGIN = 2 * 1024**3
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


def format_report(report: DriftReport, *, identity: str, revision: str, reference_desc: str) -> str:
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
    lines.append(f"\n結論：{'零 drift' if report.drift_count == 0 else f'{report.drift_count} 項 drift，禁止 stamp'}")
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


def _reference_server(args) -> tuple[str, str]:
    if args.reference_url_env:
        url = os.environ.get(args.reference_url_env)
        if not url:
            raise SystemExit(f"--reference-url-env {args.reference_url_env}：該環境變數未設定")
        return url, f"暫存庫建在 {sm.target_identity(url)} 的伺服器"
    return DATABASE_URL, "暫存庫建在目標的同一台伺服器"


async def run_check(args, *, revision: str | None = None) -> tuple[int, DriftReport | None, str]:
    identity = sm.target_identity(DATABASE_URL)
    try:
        rev = revision or args.revision or await current_revision(DATABASE_URL) or sm.BASELINE_REVISION
        server_url, ref_desc = _reference_server(args)
        reference, ref_order = await build_reference_catalog(server_url, rev)
        target, tgt_order = await fetch_catalog(DATABASE_URL)
    except SystemExit:
        raise
    except ReferenceCleanupError as exc:
        print(f"!! 警報：{exc}", file=sys.stderr)
        return EXIT_ERROR, None, ""
    except Exception as exc:
        print(f"無法比對（{identity}）：{exc!r}", file=sys.stderr)
        return EXIT_ERROR, None, ""
    report = diff_catalogs(reference, target, ref_order, tgt_order)
    print(format_report(report, identity=identity, revision=rev, reference_desc=ref_desc))
    if getattr(args, "json", None):
        payload = report_to_json(report, target=identity, revision=rev, reference=ref_desc,
                                 checked_at=datetime.now().astimezone().isoformat(timespec="seconds"))
        Path(args.json).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"報告已寫入 {args.json}")
    return (EXIT_OK if report.drift_count == 0 else EXIT_DRIFT), report, rev


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
        else:
            p.add_argument("--dump-dir", help="全庫 pg_dump -Fc 的落點目錄")
            p.add_argument("--dump-container", help="以 docker exec 在此容器內執行 pg_dump／pg_restore")
            p.add_argument("--dump-timeout-min", type=float, default=DEFAULT_DUMP_TIMEOUT_MIN)
            p.add_argument("--no-dump", action="store_true", help="略過全庫備份（受保護的庫不接受）")
    args = ap.parse_args(argv)
    if args.cmd == "check":
        args.revision = getattr(args, "revision", None)
        rc, _, _ = asyncio.run(run_check(args))
        return rc
    args.revision = None
    return cmd_stamp(args)


if __name__ == "__main__":
    sys.exit(main())
