"""監控觀測的保留與聚合（revision 0007；`scripts/rollup_observations.py` 每小時一輪）與查詢端的粒度選擇。

保留期 90 天，越舊越粗（理由見 revision 0007 的註解）：

    0–24 小時    原始觀測  research.service_observation
    24 小時–7 天 5 分鐘桶  research.service_observation_5m
    7–90 天      1 小時桶  research.service_observation_1h
    90 天以前    刪除（三張觀測表與 job_execution；incident／incident_event 不在這裡處理）

**搬移是原子的**：每一片（1 小時寬的來源區間）只用一句 SQL——data-modifying CTE 先 DELETE 來源、再把
「被刪掉的那些列」聚合後 upsert 進較粗的表，最後回傳刪了多少筆觀測、聚合涵蓋多少筆、寫了幾個桶。同一句＝
同一個 snapshot：loader 在這期間新匯入的列不會被刪而沒聚合到；寫入失敗（CHECK、鎖逾時、被砍）整句回滾、
來源原封不動。回傳的數字由 Python 再核對一次（刪掉的觀測筆數＝聚合涵蓋的筆數、要寫的桶＝寫入的桶），
對不上就拋 `RollupMismatch`，呼叫端 rollback——「確認寫入後才刪」落實在同一個交易裡。

**冪等**：來源搬走之後就不在了，所以同一片重跑時沒有東西可搬（no-op），結果完全相同。遲到的觀測（DB 掛掉
期間 spool 積壓、恢復後才匯入、時間已經超過 24 小時）落在已有的桶時以合併寫入（筆數加總、min／max 取極值、
平均依筆數加權、last 依時間取較新者），不會蓋掉既有的桶。已知的近似：合併時兩段時間交錯的話，state_changes
只是兩邊相加（不知道交錯處有沒有轉換）；loader commit 之後、存進度之前被砍、而 rollup 剛好在那 5 分鐘內把
同一批搬走時，重匯的那幾筆會被重複計入（自然鍵去重只在原始表有效）。兩者都只影響監控曲線。

**刪除分批**：每一片、每一批保留期刪除各自一個交易（呼叫端在每一步之後 commit），每步先
`db.relax_statement_timeout()`（SET LOCAL，只活到該交易結束）。一片＝1 小時的原始觀測（約 200 條序列 × 60
筆＝1.2 萬列）；保留期刪除每批最多 `batch_size` 列（ctid 子查詢，不一次鎖整段）。

查詢端（`/api/admin/observations`）：`pick_resolution()` 依 `since` 距今多久選最細、而且資料保證還在的粒度；
`list_aggregated()` 把「該粒度以下」的每張表（原始＋較細的聚合）用同一套規則重新分桶後聯集，所以剛好跨在
兩段保留期交界、或 rollup 晚跑一輪的資料也不會缺。

本模組不 commit（`run_rollup` 的 commit 由呼叫端傳入）：DB 測試傳 no-op、整段 rollback。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Sequence

from sqlalchemy import text

RAW_RETENTION = timedelta(hours=24)
FINE_RETENTION = timedelta(days=7)
RETENTION = timedelta(days=90)
SLICE = timedelta(hours=1)
ORIGIN = datetime(2000, 1, 1, tzinfo=timezone.utc)

RESOLUTIONS = ("raw", "5m", "1h")
WIDTHS = {"5m": timedelta(minutes=5), "1h": timedelta(hours=1)}
_INTERVAL_SQL = {"5m": "INTERVAL '5 minutes'", "1h": "INTERVAL '1 hour'"}
RAW_TABLE = "research.service_observation"
AGG_TABLES = {"5m": "research.service_observation_5m", "1h": "research.service_observation_1h"}

DEFAULT_MAX_SLICES = 500
DEFAULT_BATCH_SIZE = 5000

# 保留期刪除的對象：表 → 時間欄。job_execution 依 started_at（還停在 running 的也一樣：90 天前開始的 oneshot
# 不可能還在跑，只是收集器沒看到它結束）。
PURGE_TARGETS = (
    (RAW_TABLE, "observed_at"),
    (AGG_TABLES["5m"], "bucket_start"),
    (AGG_TABLES["1h"], "bucket_start"),
    ("research.job_execution", "started_at"),
)

_AGG_COLS = ("bucket_start, host, scope, subject, metric, sample_count, value_count, value_min, value_max, "
             "value_avg, value_last, first_state, last_state, state_changes, last_detail, first_at, last_at")


class RollupMismatch(RuntimeError):
    """聚合核對失敗（刪掉的筆數與聚合涵蓋的筆數不符）：呼叫端必須 rollback，來源不得被刪。"""


@dataclass(frozen=True)
class Cutoffs:
    raw_before: datetime    # 早於此的原始觀測 → 5 分鐘桶
    fine_before: datetime   # 早於此的 5 分鐘桶 → 1 小時桶
    purge_before: datetime  # 早於此的全部刪除


@dataclass
class RollupStats:
    purged: dict[str, int] = field(default_factory=dict)
    raw_slices: int = 0
    raw_rows: int = 0
    fine_slices: int = 0
    fine_rows: int = 0
    buckets_5m: int = 0
    buckets_1h: int = 0
    pending: bool = False  # 還有片沒處理完（超過 max_slices，下一輪繼續）

    def summary(self) -> str:
        purged = " ".join(f"{t.split('.')[-1]}={n}" for t, n in self.purged.items())
        return (f"raw→5m slices={self.raw_slices} rows={self.raw_rows} buckets={self.buckets_5m}; "
                f"5m→1h slices={self.fine_slices} rows={self.fine_rows} buckets={self.buckets_1h}; "
                f"purged {purged or '-'}" + ("; 尚有積壓，下一輪繼續" if self.pending else ""))


def floor_to(ts: datetime, width: timedelta) -> datetime:
    """與 SQL 的 `date_bin(width, ts, '2000-01-01 00:00:00+00')` 相同（ts 必須帶時區）。"""
    return ts - ((ts - ORIGIN) % width)


def cutoffs(now: datetime) -> Cutoffs:
    """三條界線都對齊整點：每一片是完整的 1 小時，原始觀測實際保留 24–25 小時、5 分鐘桶 7 天到 7 天又 1 小時。"""
    return Cutoffs(raw_before=floor_to(now - RAW_RETENTION, SLICE),
                   fine_before=floor_to(now - FINE_RETENTION, SLICE),
                   purge_before=floor_to(now - RETENTION, SLICE))


def pick_resolution(since: datetime, now: datetime) -> str:
    """查詢粒度：`since` 還在原始保留期內 → raw；在 5 分鐘保留期內 → 5m；否則 1h。

    保證：rollup 只搬「早於整點(執行時刻 − 24h)」的原始觀測，所以 since ≥ now − 24h 的區間一定全在原始表；
    同理 since ≥ now − 7d 的區間不會有 1 小時桶。選得比這更細就會缺資料，更粗則白白失去解析度。
    """
    if since >= now - RAW_RETENTION:
        return "raw"
    if since >= now - FINE_RETENTION:
        return "5m"
    return "1h"


# ── SQL 片段 ─────────────────────────────────────────────────────────────────
# 每個來源先正規化成同一個形狀（一列＝一段時間內某條序列的彙總），再依目標寬度重新分桶。原始觀測就是
# 「只有一筆」的彙總；聚合列的 value_sum 由平均 × 筆數還原。

_RAW_NORMALIZED = """host, scope, subject, metric, observed_at AS first_at, observed_at AS last_at,
    1 AS sample_count, CASE WHEN value IS NULL THEN 0 ELSE 1 END AS value_count,
    value AS value_min, value AS value_max, value AS value_sum, value AS value_last,
    state AS first_state, state AS last_state, 0 AS state_changes, detail AS last_detail"""

_AGG_NORMALIZED = """host, scope, subject, metric, first_at, last_at, sample_count, value_count,
    value_min, value_max, value_avg * value_count AS value_sum, value_last,
    first_state, last_state, state_changes, last_detail"""


def _binned_ctes(resolution: str) -> str:
    """接在名為 `src` 的 CTE 之後：重新分桶成 `binned`（欄位同聚合表）。

    state_changes＝各段自己的轉換數加總，再加上同一桶內相鄰兩段「前一段最後的狀態 ≠ 這一段最初的狀態」的次數。
    """
    interval = _INTERVAL_SQL[resolution]
    return f"""
ordered AS (
    SELECT s.*, date_bin({interval}, s.first_at, TIMESTAMPTZ '2000-01-01 00:00:00+00') AS b
    FROM src s
), lagged AS (
    SELECT o.*, lag(o.last_state) OVER (
        PARTITION BY o.host, o.scope, o.subject, o.metric, o.b ORDER BY o.first_at, o.last_at) AS prev_state
    FROM ordered o
), binned AS (
    SELECT b AS bucket_start, host, scope, subject, metric,
           CAST(sum(sample_count) AS integer) AS sample_count,
           CAST(sum(value_count) AS integer) AS value_count,
           min(value_min) AS value_min,
           max(value_max) AS value_max,
           sum(value_sum) / NULLIF(sum(value_count), 0) AS value_avg,
           (array_agg(value_last ORDER BY last_at DESC) FILTER (WHERE value_last IS NOT NULL))[1] AS value_last,
           (array_agg(first_state ORDER BY first_at) FILTER (WHERE first_state IS NOT NULL))[1] AS first_state,
           (array_agg(last_state ORDER BY last_at DESC) FILTER (WHERE last_state IS NOT NULL))[1] AS last_state,
           CAST(sum(state_changes) + count(*) FILTER (
               WHERE prev_state IS NOT NULL AND first_state IS NOT NULL AND first_state <> prev_state)
               AS integer) AS state_changes,
           (array_agg(last_detail ORDER BY last_at DESC) FILTER (WHERE last_state IS NOT NULL))[1] AS last_detail,
           min(first_at) AS first_at,
           max(last_at) AS last_at
    FROM lagged
    GROUP BY b, host, scope, subject, metric
)"""


def _merge_set(table: str) -> str:
    """ON CONFLICT 的合併（遲到的資料落在已有的桶）。SET 右邊的 t.* 一律是更新前的值。"""
    t = table
    return f"""
    sample_count  = {t}.sample_count + EXCLUDED.sample_count,
    value_count   = {t}.value_count + EXCLUDED.value_count,
    value_min     = LEAST({t}.value_min, EXCLUDED.value_min),
    value_max     = GREATEST({t}.value_max, EXCLUDED.value_max),
    value_avg     = CASE WHEN {t}.value_count + EXCLUDED.value_count = 0 THEN NULL
                         ELSE (COALESCE({t}.value_avg * {t}.value_count, 0)
                               + COALESCE(EXCLUDED.value_avg * EXCLUDED.value_count, 0))
                              / ({t}.value_count + EXCLUDED.value_count) END,
    value_last    = CASE WHEN EXCLUDED.last_at >= {t}.last_at THEN COALESCE(EXCLUDED.value_last, {t}.value_last)
                         ELSE COALESCE({t}.value_last, EXCLUDED.value_last) END,
    first_state   = CASE WHEN EXCLUDED.first_at < {t}.first_at THEN COALESCE(EXCLUDED.first_state, {t}.first_state)
                         ELSE COALESCE({t}.first_state, EXCLUDED.first_state) END,
    last_state    = CASE WHEN EXCLUDED.last_at >= {t}.last_at THEN COALESCE(EXCLUDED.last_state, {t}.last_state)
                         ELSE COALESCE({t}.last_state, EXCLUDED.last_state) END,
    last_detail   = CASE WHEN EXCLUDED.last_state IS NOT NULL
                              AND (EXCLUDED.last_at >= {t}.last_at OR {t}.last_state IS NULL)
                         THEN EXCLUDED.last_detail ELSE {t}.last_detail END,
    state_changes = {t}.state_changes + EXCLUDED.state_changes
                    + CASE WHEN EXCLUDED.first_at > {t}.last_at AND EXCLUDED.first_state <> {t}.last_state THEN 1
                           WHEN EXCLUDED.last_at < {t}.first_at AND EXCLUDED.last_state <> {t}.first_state THEN 1
                           ELSE 0 END,
    first_at      = LEAST({t}.first_at, EXCLUDED.first_at),
    last_at       = GREATEST({t}.last_at, EXCLUDED.last_at)"""


def _host_filter(alias: str, hosts: Sequence[str] | None) -> str:
    # 只給 DB 測試用（以隨機 host 圈住自己塞的列；本機預設庫就是生產庫）。正式批次一律全部主機。
    return f" AND {alias}.host = ANY(CAST(:hosts AS text[]))" if hosts is not None else ""


def _move_sql(source: str, hosts: Sequence[str] | None) -> str:
    """一片的搬移：source＝'raw'（→ 5m）或 '5m'（→ 1h）。"""
    if source == "raw":
        src_table, time_col, normalized, target = RAW_TABLE, "observed_at", _RAW_NORMALIZED, "5m"
    else:
        src_table, time_col, normalized, target = AGG_TABLES["5m"], "bucket_start", _AGG_NORMALIZED, "1h"
    dest = AGG_TABLES[target]
    return f"""
WITH moved AS (
    DELETE FROM {src_table} m
    WHERE m.{time_col} >= :lo AND m.{time_col} < :hi{_host_filter("m", hosts)}
    RETURNING m.*
), src AS (
    SELECT {normalized} FROM moved
), {_binned_ctes(target)},
written AS (
    INSERT INTO {dest} ({_AGG_COLS})
    SELECT {_AGG_COLS} FROM binned
    ON CONFLICT (subject, metric, bucket_start, scope, host) DO UPDATE SET {_merge_set(dest)}
    RETURNING 1
)
SELECT (SELECT count(*) FROM moved) AS moved_rows,
       (SELECT COALESCE(sum(sample_count), 0) FROM src) AS moved_samples,
       (SELECT COALESCE(sum(sample_count), 0) FROM binned) AS binned_samples,
       (SELECT count(*) FROM binned) AS buckets,
       (SELECT count(*) FROM written) AS written
"""


# ── 批次（scripts/rollup_observations.py）────────────────────────────────────


def _source(source: str) -> tuple[str, str]:
    return (RAW_TABLE, "observed_at") if source == "raw" else (AGG_TABLES["5m"], "bucket_start")


async def next_slice(session, source: str, before: datetime, after: datetime | None = None,
                     hosts: Sequence[str] | None = None) -> datetime | None:
    """source（'raw' 或 '5m'）裡早於 `before`（且不早於 `after`）最舊那一筆所在的整點片起點；沒有回 None。

    逐片找下一個有資料的片（時間索引一次探測），跳過空的時段：收集器停過幾天也不會把單輪額度耗在空片上。
    """
    table, col = _source(source)
    params: dict[str, Any] = {"before": before}
    cond = f"t.{col} < :before{_host_filter('t', hosts)}"
    if after is not None:
        cond += f" AND t.{col} >= :after"
        params["after"] = after
    if hosts is not None:
        params["hosts"] = list(hosts)
    oldest = (await session.execute(text(f"SELECT min(t.{col}) FROM {table} t WHERE {cond}"), params)).scalar()
    return None if oldest is None else floor_to(oldest, SLICE)


async def count_slices(session, source: str, before: datetime) -> int:
    """早於 `before`、有資料的片數（`--dry-run` 用）。"""
    table, col = _source(source)
    return int((await session.execute(text(
        f"SELECT count(DISTINCT date_bin(INTERVAL '1 hour', t.{col}, TIMESTAMPTZ '2000-01-01 00:00:00+00')) "
        f"FROM {table} t WHERE t.{col} < :before"), {"before": before})).scalar_one())


async def move_slice(session, source: str, lo: datetime, hosts: Sequence[str] | None = None) -> tuple[int, int]:
    """搬一片 [lo, lo+1h)：回傳（搬走的列數, 寫入的桶數）。核對不符拋 RollupMismatch（呼叫端 rollback）。"""
    params: dict[str, Any] = {"lo": lo, "hi": lo + SLICE}
    if hosts is not None:
        params["hosts"] = list(hosts)
    row = (await session.execute(text(_move_sql(source, hosts)), params)).mappings().one()
    if row["moved_samples"] != row["binned_samples"] or row["buckets"] != row["written"]:
        raise RollupMismatch(f"{source} 片 {lo.isoformat()} 核對不符：{dict(row)}")
    return int(row["moved_rows"]), int(row["written"])


async def purge_batch(session, table: str, col: str, before: datetime, limit: int,
                      hosts: Sequence[str] | None = None) -> int:
    """刪掉 `table` 裡 `col` 早於 `before` 的最多 `limit` 列，回傳刪除列數。"""
    params: dict[str, Any] = {"before": before, "limit": limit}
    if hosts is not None:
        params["hosts"] = list(hosts)
    res = await session.execute(text(f"""
        DELETE FROM {table} WHERE ctid = ANY(ARRAY(
            SELECT t.ctid FROM {table} t WHERE t.{col} < :before{_host_filter('t', hosts)} LIMIT :limit))
    """), params)
    return max(res.rowcount or 0, 0)


async def run_rollup(session, *, now: datetime, commit: Callable[[], Awaitable[None]] | None = None,
                     hosts: Sequence[str] | None = None, max_slices: int = DEFAULT_MAX_SLICES,
                     batch_size: int = DEFAULT_BATCH_SIZE) -> RollupStats:
    """一輪：保留期刪除 → 原始搬到 5 分鐘桶 → 5 分鐘桶搬到 1 小時桶。每一步之後 `commit()`（預設 session.commit）。

    先刪過期的再搬：90 天以前的資料不必先聚合再刪。原始 → 5m 在 5m → 1h 之前：遲到的舊觀測同一輪就能一路
    搬到 1 小時桶。`max_slices` 是兩段搬移合計的上限（DB 長期停擺後的積壓分幾輪消化，單輪不會跑太久）。
    """
    from app.services.db import relax_statement_timeout

    commit = commit or session.commit
    cut = cutoffs(now)
    stats = RollupStats()
    for table, col in PURGE_TARGETS:
        total = 0
        while True:
            await relax_statement_timeout(session)
            n = await purge_batch(session, table, col, cut.purge_before, batch_size, hosts)
            await commit()
            total += n
            if n < batch_size:
                break
        stats.purged[table] = total
    budget = max_slices
    for source, before in (("raw", cut.raw_before), ("5m", cut.fine_before)):
        cursor: datetime | None = None
        while True:
            lo = await next_slice(session, source, before, cursor, hosts)
            if lo is None:
                break
            if budget <= 0:
                stats.pending = True
                break
            await relax_statement_timeout(session)
            rows, buckets = await move_slice(session, source, lo, hosts)
            await commit()
            budget -= 1
            cursor = lo + SLICE
            if source == "raw":
                stats.raw_slices, stats.raw_rows, stats.buckets_5m = (
                    stats.raw_slices + 1, stats.raw_rows + rows, stats.buckets_5m + buckets)
            else:
                stats.fine_slices, stats.fine_rows, stats.buckets_1h = (
                    stats.fine_slices + 1, stats.fine_rows + rows, stats.buckets_1h + buckets)
    return stats


# ── 查詢端（/api/admin/observations 的 5m／1h）──────────────────────────────────


async def list_aggregated(session, *, resolution: str, since: datetime, until: datetime, scope: str | None = None,
                          subject: str | None = None, metric: str | None = None,
                          limit: int = 500) -> tuple[bool, list[dict]]:
    """把原始觀測與較細的聚合一起重新分桶成 `resolution`（新→舊、最多 `limit` 桶，多取一筆判斷 truncated）。

    第一個桶對齊到 `since` 所在的桶起點（桶是完整的）；`value` 是桶內平均、`state` 是桶內最後的狀態、`detail`
    是最後一個狀態列的屬性，另附 sample_count／value_min／value_max／value_last／first_state／state_changes。
    """
    if resolution not in WIDTHS:
        raise ValueError(f"不支援的粒度：{resolution}")
    lo = floor_to(since, WIDTHS[resolution])
    params: dict[str, Any] = {"lo": lo, "until": until, "limit": limit + 1}
    filters = ""
    for col, val in (("scope", scope), ("subject", subject), ("metric", metric)):
        if val:
            filters += f" AND x.{col} = :{col}"
            params[col] = val
    parts = [f"SELECT {_RAW_NORMALIZED} FROM {RAW_TABLE} x "
             f"WHERE x.observed_at >= :lo AND x.observed_at <= :until{filters}"]
    for name in RESOLUTIONS[1:RESOLUTIONS.index(resolution) + 1]:
        parts.append(f"SELECT {_AGG_NORMALIZED} FROM {AGG_TABLES[name]} x "
                     f"WHERE x.bucket_start >= :lo AND x.bucket_start <= :until{filters}")
    rows = (await session.execute(text(f"""
        WITH src AS ({' UNION ALL '.join(parts)}), {_binned_ctes(resolution)}
        SELECT bucket_start, host, scope, subject, metric, sample_count, value_count, value_min, value_max,
               value_avg, value_last, first_state, last_state, state_changes, last_detail
        FROM binned
        ORDER BY bucket_start DESC, subject, metric, scope, host
        LIMIT :limit
    """), params)).mappings().all()
    truncated = len(rows) > limit
    out = []
    for r in rows[:limit]:
        detail = r["last_detail"]
        if isinstance(detail, str):
            try:
                detail = json.loads(detail)
            except ValueError:
                detail = None
        out.append({
            "observed_at": r["bucket_start"], "host": r["host"], "scope": r["scope"], "subject": r["subject"],
            "metric": r["metric"], "value": r["value_avg"], "state": r["last_state"],
            "detail": detail if isinstance(detail, dict) else None,
            "sample_count": r["sample_count"], "value_min": r["value_min"], "value_max": r["value_max"],
            "value_last": r["value_last"], "first_state": r["first_state"], "state_changes": r["state_changes"],
        })
    return truncated, out
