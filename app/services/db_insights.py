"""進階 DB 與事件趨勢（Admin v2 DB lane）：系統目錄快照、慢查詢、`db_stat_snapshot` 趨勢、事件與批次趨勢。

呼叫端：`web/routers/admin_db.py`（`/api/admin/db/*`）、`web/routers/admin_monitoring.py`
（`/api/admin/incidents/trends`）、`scripts/db_snapshot.py`（每小時寫一列快照）。本模組不 commit：
呼叫端決定交易邊界（DB 測試整段 rollback）。

## 即時快照（`collect_overview`）

- **只查系統目錄**（`pg_stat_*`、`pg_class`、`pg_settings`、`pg_database_size`），不碰業務表的內容。
- **短上限**：進來先 `SET LOCAL statement_timeout`（`OVERVIEW_STATEMENT_TIMEOUT_MS`，與引擎預設取小）與
  `SET LOCAL lock_timeout`——與 `db.relax_statement_timeout()` 相反，這裡是把上限收緊。`pg_total_relation_size`
  要對每張表取 AccessShareLock，碰上 VACUUM FULL／ALTER 這類排他鎖時不該把管理頁卡到 60 秒。SET LOCAL 只活到
  交易結束（連線池化，SET 會流到下一個借用者，理由見 `db.relax_statement_timeout`）。
- **每段獨立**：五段（database／tables／unused_indexes／connections／activity）各包一個 SAVEPOINT，失敗只
  回滾那一段並在該段回 `error`（`permission_denied`／`timeout`／`error`），其餘照常——staging 的 RDS 帳號
  權限較窄，不能讓一段權限不足就整頁 500。錯誤訊息是固定文字，不回原始例外（可能帶 SQL）。
- **權限較窄時的連線數**：沒有 `pg_read_all_stats` 的帳號看別人的 session 時 `state` 與 `backend_type`
  都是 NULL。這些列（`datname` 非 NULL＝連到某個庫的 client）計入 `hidden`，不是丟掉——總數照樣對得上上限。
- 膨脹只用 dead tuple 比例估計（不裝 pgstattuple，設計 §1.5）。

## 慢查詢（`slow_queries`）

執行期偵測 `pg_stat_statements`：擴充沒建立 → `extension_missing`；建了但沒預載（查 view 時 55000）→
`not_preloaded`；SELECT 被拒 → `permission_denied`。**絕不 CREATE EXTENSION、絕不改設定**（使用者定案 13：
啟用是獨立的維護步驟，見 `docs/production_resilience.md`）。查詢文字壓掉連續空白後截斷到
`QUERY_TEXT_MAX_CHARS`；別人的語句在權限不足時是 `<insufficient privilege>`，回 `query=None`、`query_hidden=True`。

## 趨勢快照（`run_snapshot`；`scripts/db_snapshot.py` 每小時）

1. 寫一列 `granularity='hour'`（`taken_at`＝整點，UTC；同一小時已有就不寫——冪等）。`stats` 只放數字：
   `gauges`（當下值）、`counters`（`pg_stat_database` 的累計值）、`tables`（前 `SNAPSHOT_TOP_TABLES` 大的表）。
2. 每日彙總：台北時間的「已結束、還在逐時保留期內、有逐時列、卻沒有每日列」的每一天各寫一列 `granularity='day'`
   （`taken_at`＝該日台北零時）。所以「每天第一次執行」自然就會補前一天，漏跑幾天也會補齊；重跑是 no-op
   （UNIQUE (granularity, taken_at) ＋ ON CONFLICT DO NOTHING）。gauges 存 avg／min／max／last，counters 存
   當日最後一個累計值（相鄰兩天相減＝那一天的量）。
3. 保留期刪除：逐時超過 `DB_SNAPSHOT_HOURLY_RETENTION_DAYS`（30）、每日超過 `DB_SNAPSHOT_DAILY_RETENTION_DAYS`
   （400）天的列（使用者定案 14：這只是監控統計，不是 DB dump）。先彙總再刪，所以被刪的逐時列都已彙總過。

## 趨勢查詢（`trend_points`）

區間起點還在逐時保留期內就用逐時，否則用每日（`pick_granularity`）。gauge 直接取值（每日取 avg，附 min／max）；
counter 取相鄰兩點的差、換算成「每小時／每日」（中間缺點時依實際間隔換算），差為負（統計被重設）回 None；
`cache_hit_ratio` 是 Δblks_hit ÷ (Δblks_hit＋Δblks_read)。區間前多取一點當第一個差的基準。

## 事件與批次趨勢（`incident_trends`、`job_failure_rates`）

不新表，直接查 `incident`／`job_execution`（revision 0005、0006）。週以台北時間的週一為起點；MTTR 與持續時間
p50／p90 只算 `resolved`（`lost` 不知道何時結束，另計件數、不納入）；失敗＝`finished` 而 systemd Result 不是
`success`（SuccessExitStatus 放行的退出碼本來就是 success），失敗率分母是 finished（`lost`／`running` 另列）。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo

from sqlalchemy import text

from app.config import get_settings

logger = logging.getLogger(__name__)

TZ_NAME = "Asia/Taipei"
TZ = ZoneInfo(TZ_NAME)
UTC = timezone.utc

OVERVIEW_STATEMENT_TIMEOUT_MS = 5000
OVERVIEW_LOCK_TIMEOUT_MS = 1000
TABLE_LIMIT = 50
UNUSED_INDEX_LIMIT = 50
SLOW_QUERY_DEFAULT_LIMIT = 20
SLOW_QUERY_MAX_LIMIT = 50
QUERY_TEXT_MAX_CHARS = 200
SNAPSHOT_TOP_TABLES = 20
SNAPSHOT_VERSION = 1
# 趨勢一次最多回幾點（逐時 30 天＝720、每日 400 天＝400，留餘裕）。
TREND_MAX_POINTS = 2000
MIN_HOURLY_RETENTION_DAYS = 2

SECTIONS = ("database", "tables", "unused_indexes", "connections", "activity")
ERROR_CODES = ("permission_denied", "timeout", "error")
SLOW_QUERY_REASONS = ("extension_missing", "not_preloaded", "permission_denied", "timeout", "error")
SLOW_QUERY_SORTS = {"total": "total_ms", "mean": "mean_ms", "calls": "calls"}

# 趨勢指標 → 種類。gauge＝當下值；rate＝累計計數器的差（每小時／每日）；ratio＝由兩個計數器的差算出的比例。
TREND_METRICS: dict[str, str] = {
    "db_size_bytes": "gauge",
    "connections_total": "gauge",
    "connections_active": "gauge",
    "connections_idle_in_tx": "gauge",
    "dead_tuple_ratio": "gauge",
    "table_bytes": "gauge",
    "cache_hit_ratio": "ratio",
    "temp_bytes": "rate",
    "deadlocks": "rate",
}
GAUGE_KEYS = (
    "db_size_bytes", "connections_total", "connections_active", "connections_idle", "connections_idle_in_tx",
    "connections_hidden", "max_connections", "dead_tuples", "dead_tuple_ratio", "unused_index_count",
    "unused_index_bytes",
)
COUNTER_KEYS = ("blks_hit", "blks_read", "temp_bytes", "temp_files", "deadlocks", "xact_commit", "xact_rollback")

_ERROR_MESSAGES = {
    "permission_denied": "權限不足：DB 帳號缺少讀這段統計的權限（例如 pg_monitor／pg_read_all_stats）",
    "timeout": "查詢逾時或等不到鎖（上限 {ms} ms）",
    "error": "查詢失敗（{kind}）",
}
_SLOW_MESSAGES = {
    "extension_missing": "pg_stat_statements 擴充尚未建立（{detail}）",
    "not_preloaded": "pg_stat_statements 已建立但沒有預載（shared_preload_libraries 未含 pg_stat_statements）",
    "permission_denied": "權限不足：DB 帳號不能讀 pg_stat_statements",
    "timeout": "查詢逾時（上限 {ms} ms）",
    "error": "查詢失敗（{kind}）",
}
_INSUFFICIENT = "<insufficient privilege>"
_WS = re.compile(r"\s+")


# ── 共用：短上限與錯誤分類 ─────────────────────────────────────────────────


def effective_timeout_ms() -> int:
    """快照用的 statement_timeout：模組上限與引擎預設（0＝不限）取小。"""
    default = int(get_settings().db_statement_timeout_ms)
    return OVERVIEW_STATEMENT_TIMEOUT_MS if default <= 0 else min(OVERVIEW_STATEMENT_TIMEOUT_MS, default)


async def apply_short_timeout(session) -> int:
    """把**當前交易**的 statement_timeout／lock_timeout 收緊（SET LOCAL）。回實際的 statement_timeout。"""
    ms = int(effective_timeout_ms())
    lock_ms = int(min(OVERVIEW_LOCK_TIMEOUT_MS, ms))
    # SET 不吃 bind 參數；int() 是唯一的注入防線（值也不是使用者輸入）。
    await session.execute(text(f"SET LOCAL statement_timeout = {ms}"))
    await session.execute(text(f"SET LOCAL lock_timeout = {lock_ms}"))
    return ms


def sqlstate(exc: BaseException) -> str | None:
    """從 SQLAlchemy／asyncpg 的例外取 SQLSTATE（取不到回 None）。"""
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        for attr in ("sqlstate", "pgcode"):
            code = getattr(cur, attr, None)
            if isinstance(code, str) and len(code) == 5:
                return code
        cur = getattr(cur, "orig", None) or cur.__cause__
    return None


def classify_error(exc: BaseException) -> str:
    """42501／權限字樣 → permission_denied；57014（statement_timeout）／55P03（lock_timeout）→ timeout；其他 error。"""
    code = sqlstate(exc)
    if code == "42501":
        return "permission_denied"
    if code in ("57014", "55P03"):
        return "timeout"
    if code is None and "permission denied" in str(exc).lower():
        return "permission_denied"
    return "error"


def error_payload(code: str, exc: BaseException | None = None, *, ms: int | None = None) -> dict:
    msg = _ERROR_MESSAGES[code].format(ms=ms if ms is not None else effective_timeout_ms(),
                                       kind=type(exc).__name__ if exc is not None else "unknown")
    return {"code": code, "message": msg}


async def _guarded(session, fn: Callable[[Any], Awaitable[dict]], ms: int) -> dict:
    """在 SAVEPOINT 裡跑一段；失敗只回滾那一段，回 `{"error": {...}}`。"""
    try:
        async with session.begin_nested():
            data = await fn(session)
        return {**data, "error": None}
    except Exception as exc:  # noqa: BLE001 - 每段獨立降級，不讓單段把整頁變 500
        code = classify_error(exc)
        logger.warning("db_insights 段落失敗（%s）：%r", code, exc)
        return {"error": error_payload(code, exc, ms=ms)}


def _iso(v) -> str | None:
    return v.isoformat() if hasattr(v, "isoformat") else (None if v is None else str(v))


def _ratio(num, den) -> float | None:
    try:
        num, den = float(num), float(den)
    except (TypeError, ValueError):
        return None
    return None if den <= 0 else num / den


def _int(v) -> int | None:
    return None if v is None else int(v)


# ── 即時快照的五段 ────────────────────────────────────────────────────────


async def _database(session) -> dict:
    row = (await session.execute(text(
        "SELECT current_database() AS name, pg_database_size(current_database()) AS size_bytes, "
        "current_setting('server_version') AS server_version"))).mappings().one()
    return {"name": row["name"], "size_bytes": int(row["size_bytes"]), "server_version": row["server_version"]}


async def _tables(session) -> dict:
    totals = (await session.execute(text("""
        SELECT count(*) AS table_count, COALESCE(sum(n_live_tup), 0) AS live, COALESCE(sum(n_dead_tup), 0) AS dead
        FROM pg_stat_user_tables
    """))).mappings().one()
    rows = (await session.execute(text("""
        SELECT s.schemaname, s.relname, pg_total_relation_size(s.relid) AS total_bytes, c.reltuples,
               s.n_live_tup, s.n_dead_tup, s.last_autovacuum, s.last_vacuum, s.last_autoanalyze, s.last_analyze
        FROM pg_stat_user_tables s JOIN pg_class c ON c.oid = s.relid
        ORDER BY total_bytes DESC, s.schemaname, s.relname
        LIMIT :limit
    """), {"limit": TABLE_LIMIT})).mappings().all()
    items = []
    for r in rows:
        live, dead = int(r["n_live_tup"] or 0), int(r["n_dead_tup"] or 0)
        reltuples = r["reltuples"]
        items.append({
            "schema_name": r["schemaname"], "table": r["relname"], "total_bytes": int(r["total_bytes"] or 0),
            # reltuples = -1：從沒 ANALYZE／VACUUM 過（PG14+），估計不存在
            "row_estimate": None if reltuples is None or reltuples < 0 else int(reltuples),
            "live_tuples": live, "dead_tuples": dead, "dead_ratio": _ratio(dead, live + dead),
            "last_autovacuum": _iso(r["last_autovacuum"]), "last_vacuum": _iso(r["last_vacuum"]),
            "last_autoanalyze": _iso(r["last_autoanalyze"]), "last_analyze": _iso(r["last_analyze"]),
        })
    live, dead = int(totals["live"]), int(totals["dead"])
    return {"table_count": int(totals["table_count"]), "live_tuples": live, "dead_tuples": dead,
            "dead_ratio": _ratio(dead, live + dead), "limit": TABLE_LIMIT, "items": items}


async def _unused_indexes(session) -> dict:
    totals = (await session.execute(text("""
        SELECT count(*) AS n, COALESCE(sum(pg_relation_size(indexrelid)), 0) AS bytes
        FROM pg_stat_user_indexes WHERE idx_scan = 0
    """))).mappings().one()
    rows = (await session.execute(text("""
        SELECT s.schemaname, s.relname, s.indexrelname, pg_relation_size(s.indexrelid) AS size_bytes,
               i.indisunique, i.indisprimary
        FROM pg_stat_user_indexes s JOIN pg_index i ON i.indexrelid = s.indexrelid
        WHERE s.idx_scan = 0
        ORDER BY size_bytes DESC, s.schemaname, s.indexrelname
        LIMIT :limit
    """), {"limit": UNUSED_INDEX_LIMIT})).mappings().all()
    return {
        "count": int(totals["n"]), "total_bytes": int(totals["bytes"]), "limit": UNUSED_INDEX_LIMIT,
        "items": [{"schema_name": r["schemaname"], "table": r["relname"], "index": r["indexrelname"],
                   "size_bytes": int(r["size_bytes"] or 0), "is_unique": bool(r["indisunique"]),
                   "is_primary": bool(r["indisprimary"])} for r in rows],
    }


async def _connections(session) -> dict:
    settings = {r["name"]: r["setting"] for r in (await session.execute(text(
        "SELECT name, setting FROM pg_settings WHERE name IN "
        "('max_connections', 'superuser_reserved_connections', 'reserved_connections')"))).mappings().all()}
    # client backend 依 state 分；權限不足時別人的 session state／backend_type 都是 NULL（datname 仍在）→ hidden。
    rows = (await session.execute(text("""
        SELECT backend_type IS NULL AS hidden, COALESCE(state, '') AS state,
               count(*) AS n, count(*) FILTER (WHERE datname = current_database()) AS here
        FROM pg_stat_activity
        WHERE backend_type = 'client backend' OR (backend_type IS NULL AND datname IS NOT NULL)
        GROUP BY 1, 2
    """))).mappings().all()
    by_state: dict[str, int] = {}
    hidden = total = here = 0
    for r in rows:
        n = int(r["n"])
        total += n
        here += int(r["here"])
        if r["hidden"]:
            hidden += n
        else:
            key = r["state"] or "unknown"
            by_state[key] = by_state.get(key, 0) + n
    max_conn = _int(settings.get("max_connections"))
    reserved = sum(int(settings.get(k) or 0) for k in ("superuser_reserved_connections", "reserved_connections"))
    usable = None if max_conn is None else max(0, max_conn - reserved)
    return {"max_connections": max_conn, "reserved_connections": reserved, "usable_connections": usable,
            "total": total, "this_database": here, "hidden": hidden, "by_state": dict(sorted(by_state.items())),
            "usage_ratio": _ratio(total, usable) if usable else None}


async def _activity(session) -> dict:
    r = (await session.execute(text("""
        SELECT blks_hit, blks_read, temp_files, temp_bytes, deadlocks, xact_commit, xact_rollback, stats_reset
        FROM pg_stat_database WHERE datname = current_database()
    """))).mappings().one()
    hit, read = int(r["blks_hit"] or 0), int(r["blks_read"] or 0)
    return {"blks_hit": hit, "blks_read": read, "cache_hit_ratio": _ratio(hit, hit + read),
            "temp_files": int(r["temp_files"] or 0), "temp_bytes": int(r["temp_bytes"] or 0),
            "deadlocks": int(r["deadlocks"] or 0), "xact_commit": int(r["xact_commit"] or 0),
            "xact_rollback": int(r["xact_rollback"] or 0), "stats_reset": _iso(r["stats_reset"])}


_SECTION_FNS = {
    "database": _database, "tables": _tables, "unused_indexes": _unused_indexes,
    "connections": _connections, "activity": _activity,
}


async def collect_overview(session, *, now: datetime | None = None) -> dict:
    """五段系統目錄統計，各段獨立降級。呼叫端在交易結束後 SET LOCAL 自動還原。"""
    ms = await apply_short_timeout(session)
    out: dict[str, Any] = {"generated_at": _iso(now or datetime.now(UTC)), "statement_timeout_ms": ms}
    for name in SECTIONS:
        out[name] = await _guarded(session, _SECTION_FNS[name], ms)
    return out


# ── 慢查詢（pg_stat_statements，執行期偵測） ──────────────────────────────


def _slow_unavailable(reason: str, *, detail: str = "", exc: BaseException | None = None,
                      ms: int | None = None) -> dict:
    msg = _SLOW_MESSAGES[reason].format(detail=detail, ms=ms if ms is not None else effective_timeout_ms(),
                                        kind=type(exc).__name__ if exc is not None else "unknown")
    return {"available": False, "reason": reason, "message": msg, "stats_reset": None, "items": []}


def clean_query_text(raw: str | None) -> tuple[str | None, bool, bool]:
    """→ (顯示文字, 是否截斷, 是否因權限被隱藏)。壓掉連續空白再截斷到 QUERY_TEXT_MAX_CHARS。"""
    if raw is None or raw.strip() == _INSUFFICIENT:
        return None, False, True
    flat = _WS.sub(" ", raw).strip()
    if len(flat) > QUERY_TEXT_MAX_CHARS:
        return flat[:QUERY_TEXT_MAX_CHARS] + "…", True, False
    return flat, False, False


async def slow_queries(session, *, limit: int = SLOW_QUERY_DEFAULT_LIMIT, sort: str = "total") -> dict:
    """pg_stat_statements 的 top N（只限目前這個庫）。不可用時 `available=False` 與原因；絕不建立擴充。"""
    if sort not in SLOW_QUERY_SORTS:
        raise ValueError(f"未知的排序：{sort}")
    limit = max(1, min(int(limit), SLOW_QUERY_MAX_LIMIT))
    ms = await apply_short_timeout(session)
    try:
        async with session.begin_nested():
            ext = (await session.execute(text("""
                SELECT quote_ident(n.nspname) AS schema_q, e.extversion
                FROM pg_extension e JOIN pg_namespace n ON n.oid = e.extnamespace
                WHERE e.extname = 'pg_stat_statements'
            """))).mappings().first()
            available_pkg = None
            if ext is None:
                available_pkg = (await session.execute(text(
                    "SELECT 1 FROM pg_available_extensions WHERE name = 'pg_stat_statements'"))).first() is not None
    except Exception as exc:  # noqa: BLE001
        return _slow_unavailable(classify_error(exc), exc=exc, ms=ms)
    if ext is None:
        detail = "套件已可用，需以具權限的帳號 CREATE EXTENSION" if available_pkg else "這台 PostgreSQL 沒有這個套件"
        return _slow_unavailable("extension_missing", detail=detail)

    view = f"{ext['schema_q']}.pg_stat_statements"
    info_view = f"{ext['schema_q']}.pg_stat_statements_info"
    try:
        async with session.begin_nested():
            cols = {r[0] for r in (await session.execute(text(
                "SELECT attname FROM pg_attribute WHERE attrelid = to_regclass(:v) AND attnum > 0 "
                "AND NOT attisdropped"), {"v": view})).all()}
            total_col = "total_exec_time" if "total_exec_time" in cols else "total_time"
            mean_col = "mean_exec_time" if "mean_exec_time" in cols else "mean_time"
            order = SLOW_QUERY_SORTS[sort]
            # view 名稱來自 quote_ident，欄名來自上面的固定集合：皆非使用者輸入。
            rows = (await session.execute(text(f"""
                SELECT s.queryid::text AS queryid, left(s.query, 2000) AS query, s.calls,
                       s.{total_col} AS total_ms, s.{mean_col} AS mean_ms, s.rows,
                       s.shared_blks_hit, s.shared_blks_read, s.temp_blks_written
                FROM {view} s
                WHERE s.dbid = (SELECT oid FROM pg_database WHERE datname = current_database())
                ORDER BY {order} DESC NULLS LAST, s.queryid
                LIMIT :limit
            """), {"limit": limit})).mappings().all()
            stats_reset = None
            if (await session.execute(text("SELECT to_regclass(:v)"), {"v": info_view})).scalar() is not None:
                stats_reset = (await session.execute(text(f"SELECT stats_reset FROM {info_view}"))).scalar()
    except Exception as exc:  # noqa: BLE001
        code = sqlstate(exc)
        if code == "55000" or "shared_preload_libraries" in str(exc):
            return _slow_unavailable("not_preloaded", exc=exc)
        return _slow_unavailable(classify_error(exc), exc=exc, ms=ms)

    items = []
    hidden = 0
    for r in rows:
        query, truncated, is_hidden = clean_query_text(r["query"])
        hidden += int(is_hidden)
        hit, read = int(r["shared_blks_hit"] or 0), int(r["shared_blks_read"] or 0)
        items.append({
            "queryid": r["queryid"], "query": query, "query_truncated": truncated, "query_hidden": is_hidden,
            "calls": int(r["calls"] or 0), "total_ms": float(r["total_ms"] or 0.0),
            "mean_ms": float(r["mean_ms"] or 0.0), "rows": int(r["rows"] or 0),
            "cache_hit_ratio": _ratio(hit, hit + read), "temp_blks_written": int(r["temp_blks_written"] or 0),
        })
    return {"available": True, "reason": None, "message": None, "stats_reset": _iso(stats_reset),
            "extension_version": ext["extversion"], "sort": sort, "limit": limit, "hidden_count": hidden,
            "items": items}


# ── 趨勢快照（scripts/db_snapshot.py） ─────────────────────────────────────


def snapshot_stats(overview: dict) -> dict:
    """即時快照 → 一列逐時 `stats`（只放數字；失敗的段落記在 errors、對應的 gauge 缺席＝趨勢上是空點）。"""
    gauges: dict[str, float] = {}
    counters: dict[str, int] = {}
    tables: dict[str, int] = {}
    errors: dict[str, str] = {}
    for name in SECTIONS:
        err = (overview.get(name) or {}).get("error")
        if err:
            errors[name] = err["code"]
    def ok(name: str) -> dict | None:  # 缺席或失敗的段落都不留值（不拿 0 冒充）
        sec = overview.get(name)
        return sec if isinstance(sec, dict) and not sec.get("error") else None

    db = ok("database") or {}
    if db.get("size_bytes") is not None:
        gauges["db_size_bytes"] = db["size_bytes"]
    t = ok("tables")
    if t is not None:
        gauges["dead_tuples"] = t.get("dead_tuples", 0)
        if t.get("dead_ratio") is not None:
            gauges["dead_tuple_ratio"] = t["dead_ratio"]
        for it in (t.get("items") or [])[:SNAPSHOT_TOP_TABLES]:
            tables[f"{it['schema_name']}.{it['table']}"] = it["total_bytes"]
    u = ok("unused_indexes")
    if u is not None:
        gauges["unused_index_count"] = u.get("count", 0)
        gauges["unused_index_bytes"] = u.get("total_bytes", 0)
    c = ok("connections")
    if c is not None:
        by_state = c.get("by_state") or {}
        gauges["connections_total"] = c.get("total", 0)
        gauges["connections_active"] = by_state.get("active", 0)
        gauges["connections_idle"] = by_state.get("idle", 0)
        gauges["connections_idle_in_tx"] = (by_state.get("idle in transaction", 0)
                                            + by_state.get("idle in transaction (aborted)", 0))
        gauges["connections_hidden"] = c.get("hidden", 0)
        if c.get("max_connections") is not None:
            gauges["max_connections"] = c["max_connections"]
    a = ok("activity")
    stats_reset = None
    if a is not None:
        for k in COUNTER_KEYS:
            if a.get(k) is not None:
                counters[k] = a[k]
        stats_reset = a.get("stats_reset")
    return {"v": SNAPSHOT_VERSION, "collected_at": overview.get("generated_at"), "gauges": gauges,
            "counters": counters, "tables": tables, "errors": errors, "stats_reset": stats_reset}


def _loads(v) -> dict:
    if isinstance(v, str):  # 依驅動與型別資訊而定，jsonb 可能以字串回來
        try:
            v = json.loads(v)
        except ValueError:
            return {}
    return v if isinstance(v, dict) else {}


def _num(v) -> float | None:
    if isinstance(v, bool) or v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def rollup_day(day: date, hourly: list[dict]) -> dict:
    """一天的逐時 stats（依時間舊→新）→ 每日 stats。純函式。"""
    gauges: dict[str, dict[str, float]] = {}
    series: dict[str, list[float]] = {}
    for s in hourly:
        for k, v in (s.get("gauges") or {}).items():
            n = _num(v)
            if n is not None:
                series.setdefault(k, []).append(n)
    for k, vals in sorted(series.items()):
        gauges[k] = {"avg": sum(vals) / len(vals), "min": min(vals), "max": max(vals), "last": vals[-1]}
    counters: dict[str, float] = {}
    tables: dict[str, float] = {}
    stats_reset = None
    for s in hourly:  # 後面的覆蓋前面的＝取當日最後一個值
        for k, v in (s.get("counters") or {}).items():
            n = _num(v)
            if n is not None:
                counters[k] = n
        if s.get("tables"):
            tables = {k: v for k, v in s["tables"].items() if _num(v) is not None}
        if s.get("stats_reset"):
            stats_reset = s["stats_reset"]
    return {"v": SNAPSHOT_VERSION, "day": day.isoformat(), "samples": len(hourly), "gauges": gauges,
            "counters": counters, "tables": tables, "stats_reset": stats_reset}


def day_start(day: date) -> datetime:
    """台北時間某日零時（timestamptz，UTC 表示）。"""
    return datetime.combine(day, time(0), tzinfo=TZ).astimezone(UTC)


def retention_days() -> tuple[int, int]:
    s = get_settings()
    hourly = max(MIN_HOURLY_RETENTION_DAYS, int(s.db_snapshot_hourly_retention_days))
    daily = max(hourly, int(s.db_snapshot_daily_retention_days))
    return hourly, daily


_INSERT_SNAPSHOT = text("""
    INSERT INTO research.db_stat_snapshot (taken_at, granularity, stats)
    VALUES (:taken_at, :granularity, CAST(:stats AS jsonb))
    ON CONFLICT (granularity, taken_at) DO NOTHING
    RETURNING id
""")


async def write_hourly(session, stats: dict, *, now: datetime) -> bool:
    """寫一列逐時快照（taken_at＝整點）。同一小時已有回 False。"""
    taken_at = now.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    row = (await session.execute(_INSERT_SNAPSHOT, {
        "taken_at": taken_at, "granularity": "hour", "stats": json.dumps(stats, ensure_ascii=False)})).first()
    return row is not None


async def rollup_daily(session, *, now: datetime, hourly_days: int) -> list[date]:
    """把已結束、還在逐時保留期內、有逐時列而沒有每日列的每一天彙總成一列。回寫入的日子。"""
    today = now.astimezone(TZ).date()
    first = (now - timedelta(days=hourly_days)).astimezone(TZ).date()
    rows = (await session.execute(text("""
        SELECT taken_at, stats FROM research.db_stat_snapshot
        WHERE granularity = 'hour' AND taken_at >= :lo AND taken_at < :hi
        ORDER BY taken_at
    """), {"lo": day_start(first), "hi": day_start(today)})).mappings().all()
    by_day: dict[date, list[dict]] = {}
    for r in rows:
        by_day.setdefault(r["taken_at"].astimezone(TZ).date(), []).append(_loads(r["stats"]))
    if not by_day:
        return []
    existing = {r[0] for r in (await session.execute(text("""
        SELECT taken_at FROM research.db_stat_snapshot
        WHERE granularity = 'day' AND taken_at = ANY(CAST(:starts AS timestamptz[]))
    """), {"starts": [day_start(d) for d in by_day]})).all()}
    existing_days = {ts.astimezone(TZ).date() for ts in existing}
    written: list[date] = []
    for d in sorted(by_day):
        if d in existing_days:
            continue
        stats = rollup_day(d, by_day[d])
        row = (await session.execute(_INSERT_SNAPSHOT, {
            "taken_at": day_start(d), "granularity": "day",
            "stats": json.dumps(stats, ensure_ascii=False)})).first()
        if row is not None:
            written.append(d)
    return written


async def purge(session, *, now: datetime, hourly_days: int, daily_days: int) -> tuple[int, int]:
    """刪逐時超過 hourly_days、每日超過 daily_days 天的列。回 (逐時刪幾列, 每日刪幾列)。"""
    out = []
    for gran, days in (("hour", hourly_days), ("day", daily_days)):
        res = await session.execute(text(
            "DELETE FROM research.db_stat_snapshot WHERE granularity = :g AND taken_at < :before"),
            {"g": gran, "before": now - timedelta(days=days)})
        out.append(int(res.rowcount or 0))
    return out[0], out[1]


@dataclass
class SnapshotResult:
    inserted: bool = False
    errors: dict[str, str] = field(default_factory=dict)
    rolled_up: list[date] = field(default_factory=list)
    purged_hourly: int = 0
    purged_daily: int = 0

    def summary(self) -> str:
        days = ",".join(d.isoformat() for d in self.rolled_up) or "無"
        errs = ",".join(f"{k}={v}" for k, v in sorted(self.errors.items())) or "無"
        return (f"逐時快照{'已寫入' if self.inserted else '本小時已存在（略過）'}；每日彙總 {days}；"
                f"刪除 逐時 {self.purged_hourly} 列、每日 {self.purged_daily} 列；降級段落 {errs}")


async def run_snapshot(session, *, now: datetime, overview: dict | None = None) -> SnapshotResult:
    """收集 → 寫逐時 → 補每日 → 保留期刪除。不 commit（呼叫端決定）。`overview` 給測試注入。"""
    if overview is None:
        overview = await collect_overview(session, now=now)
    stats = snapshot_stats(overview)
    hourly_days, daily_days = retention_days()
    result = SnapshotResult(errors=dict(stats["errors"]))
    result.inserted = await write_hourly(session, stats, now=now)
    result.rolled_up = await rollup_daily(session, now=now, hourly_days=hourly_days)
    result.purged_hourly, result.purged_daily = await purge(session, now=now, hourly_days=hourly_days,
                                                            daily_days=daily_days)
    return result


# ── 趨勢查詢 ─────────────────────────────────────────────────────────────


def pick_granularity(since: datetime, now: datetime, *, hourly_days: int | None = None) -> str:
    """區間起點還在逐時保留期內（資料保證還在）就用逐時，否則每日。"""
    if hourly_days is None:
        hourly_days = retention_days()[0]
    return "hour" if since >= now - timedelta(days=hourly_days) else "day"


def _gauge_value(stats: dict, metric: str, granularity: str, table: str | None):
    """→ (value, min, max)。"""
    if metric == "table_bytes":
        return _num((stats.get("tables") or {}).get(table or "")), None, None
    g = (stats.get("gauges") or {}).get(metric)
    if granularity == "day":
        if not isinstance(g, dict):
            return None, None, None
        return _num(g.get("avg")), _num(g.get("min")), _num(g.get("max"))
    return _num(g), None, None


def compute_points(rows: list[tuple[datetime, dict]], *, metric: str, granularity: str,
                   since: datetime, table: str | None = None) -> list[dict]:
    """依時間舊→新的 (taken_at, stats) → 趨勢點。`rows` 可含一列 since 之前的基準（只用來算差，不輸出）。"""
    kind = TREND_METRICS[metric]
    bucket = 3600.0 if granularity == "hour" else 86400.0
    points: list[dict] = []
    prev: tuple[datetime, dict] | None = None
    for taken_at, stats in rows:
        value = lo = hi = None
        if kind == "gauge":
            value, lo, hi = _gauge_value(stats, metric, granularity, table)
        elif prev is not None:
            cur_c, prev_c = stats.get("counters") or {}, prev[1].get("counters") or {}
            span = (taken_at - prev[0]).total_seconds()
            if kind == "rate":
                a, b = _num(cur_c.get(metric)), _num(prev_c.get(metric))
                if a is not None and b is not None and a >= b and span > 0:
                    value = (a - b) * bucket / span
            else:  # ratio：cache_hit_ratio
                vals = [_num(x.get(k)) for x in (cur_c, prev_c) for k in ("blks_hit", "blks_read")]
                if None not in vals:
                    d_hit, d_read = vals[0] - vals[2], vals[1] - vals[3]
                    if d_hit >= 0 and d_read >= 0:
                        value = _ratio(d_hit, d_hit + d_read)
        if taken_at >= since:
            points.append({"t": _iso(taken_at), "value": value, "min": lo, "max": hi})
        prev = (taken_at, stats)
    return points


async def trend_points(session, *, metric: str, since: datetime, until: datetime, granularity: str,
                       table: str | None = None) -> list[dict]:
    if metric not in TREND_METRICS:
        raise ValueError(f"未知的指標：{metric}")
    rows = (await session.execute(text("""
        (SELECT taken_at, stats FROM research.db_stat_snapshot
         WHERE granularity = :g AND taken_at < :since ORDER BY taken_at DESC LIMIT 1)
        UNION ALL
        (SELECT taken_at, stats FROM research.db_stat_snapshot
         WHERE granularity = :g AND taken_at >= :since AND taken_at <= :until ORDER BY taken_at LIMIT :limit)
        ORDER BY taken_at
    """), {"g": granularity, "since": since, "until": until, "limit": TREND_MAX_POINTS})).mappings().all()
    return compute_points([(r["taken_at"], _loads(r["stats"])) for r in rows], metric=metric,
                          granularity=granularity, since=since, table=table)


async def latest_tables(session) -> list[str]:
    """最近一列逐時快照記錄的表（趨勢頁 table_bytes 的選項）。"""
    row = (await session.execute(text(
        "SELECT stats FROM research.db_stat_snapshot WHERE granularity = 'hour' "
        "ORDER BY taken_at DESC LIMIT 1"))).first()
    return sorted((_loads(row[0]).get("tables") or {}).keys()) if row else []


# ── 事件與批次趨勢 ─────────────────────────────────────────────────────────

TOP_REASONS = 10
JOB_TREND_WINDOW = timedelta(days=90)

_DURATION = "extract(epoch FROM i.resolved_at - i.opened_at)"
_INCIDENT_STATS = f"""
    count(*) AS total,
    count(*) FILTER (WHERE i.status = 'resolved') AS resolved,
    count(*) FILTER (WHERE i.status = 'lost') AS lost,
    count(*) FILTER (WHERE i.status = 'firing') AS firing,
    count(*) FILTER (WHERE i.severity = 'CRITICAL') AS critical,
    count(*) FILTER (WHERE i.severity = 'WARNING') AS warning,
    avg({_DURATION}) FILTER (WHERE i.status = 'resolved') AS mttr_seconds,
    percentile_cont(0.5) WITHIN GROUP (ORDER BY {_DURATION}) FILTER (WHERE i.status = 'resolved') AS p50_seconds,
    percentile_cont(0.9) WITHIN GROUP (ORDER BY {_DURATION}) FILTER (WHERE i.status = 'resolved') AS p90_seconds
"""
_STAT_KEYS = ("total", "resolved", "lost", "firing", "critical", "warning")


def _stats_row(r) -> dict:
    out = {k: int(r[k] or 0) for k in _STAT_KEYS}
    for k in ("mttr_seconds", "p50_seconds", "p90_seconds"):
        out[k] = None if r[k] is None else float(r[k])
    return out


async def incident_trends(session, *, since: datetime, until: datetime) -> dict:
    """開場時間落在 [since, until] 的事件：每週×元件×嚴重度件數、整體與各元件的 MTTR／p50／p90、常見 reason。"""
    params = {"since": since, "until": until, "tz": TZ_NAME}
    where = "i.opened_at >= :since AND i.opened_at <= :until"
    weeks = (await session.execute(text(f"""
        SELECT to_char(date_trunc('week', i.opened_at AT TIME ZONE :tz), 'YYYY-MM-DD') AS week_start,
               i.component, i.severity, count(*) AS total,
               count(*) FILTER (WHERE i.status = 'resolved') AS resolved,
               count(*) FILTER (WHERE i.status = 'lost') AS lost,
               count(*) FILTER (WHERE i.status = 'firing') AS firing
        FROM research.incident i WHERE {where}
        GROUP BY 1, 2, 3 ORDER BY 1, 2, 3
    """), params)).mappings().all()
    grouped = (await session.execute(text(f"""
        SELECT i.component, GROUPING(i.component) AS is_total, {_INCIDENT_STATS}
        FROM research.incident i WHERE {where}
        GROUP BY GROUPING SETS ((i.component), ())
        ORDER BY is_total DESC, total DESC, i.component
    """), params)).mappings().all()
    reasons = (await session.execute(text(f"""
        SELECT i.reason, count(*) AS total, array_agg(DISTINCT i.component ORDER BY i.component) AS components
        FROM research.incident i WHERE {where}
        GROUP BY i.reason ORDER BY total DESC, i.reason LIMIT :top
    """), {**params, "top": TOP_REASONS})).mappings().all()
    summary = {**{k: 0 for k in _STAT_KEYS}, "mttr_seconds": None, "p50_seconds": None, "p90_seconds": None}
    by_component = []
    for r in grouped:
        if r["is_total"]:
            summary = _stats_row(r)
        else:
            by_component.append({"component": r["component"], **_stats_row(r)})
    return {
        "weeks": [{"week_start": r["week_start"], "component": r["component"], "severity": r["severity"],
                   "total": int(r["total"]), "resolved": int(r["resolved"]), "lost": int(r["lost"]),
                   "firing": int(r["firing"])} for r in weeks],
        "summary": summary,
        "by_component": by_component,
        "top_reasons": [{"reason": r["reason"], "total": int(r["total"]), "components": list(r["components"] or [])}
                        for r in reasons],
    }


async def job_failure_rates(session, *, since: datetime, until: datetime) -> list[dict]:
    """各 unit 在 [since, until]（依 started_at）的執行次數與失敗率（失敗率高的在前）。"""
    rows = (await session.execute(text("""
        SELECT j.unit, count(*) AS runs,
               count(*) FILTER (WHERE j.state = 'finished') AS finished,
               count(*) FILTER (WHERE j.state = 'finished' AND j.result IS DISTINCT FROM 'success') AS failed,
               count(*) FILTER (WHERE j.state = 'lost') AS lost,
               count(*) FILTER (WHERE j.state = 'running') AS running,
               max(j.started_at) FILTER (WHERE j.state = 'finished' AND j.result IS DISTINCT FROM 'success')
                   AS last_failure_at,
               max(j.started_at) AS last_started_at
        FROM research.job_execution j
        WHERE j.started_at >= :since AND j.started_at <= :until
        GROUP BY j.unit
    """), {"since": since, "until": until})).mappings().all()
    items = []
    for r in rows:
        finished, failed = int(r["finished"]), int(r["failed"])
        items.append({"unit": r["unit"], "runs": int(r["runs"]), "finished": finished, "failed": failed,
                      "lost": int(r["lost"]), "running": int(r["running"]),
                      "failure_rate": _ratio(failed, finished),
                      "last_failure_at": _iso(r["last_failure_at"]), "last_started_at": _iso(r["last_started_at"])})
    items.sort(key=lambda x: (-(x["failure_rate"] or 0.0), -x["failed"], x["unit"]))
    return items
