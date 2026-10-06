"""監控投影：spool 紀錄 → DB（`scripts/load_observations.py`）與管理後台的唯讀查詢
（`/api/admin/jobs`、`/api/admin/observations`）。

資料流（理由見 revision 0005 的註解）：

    collect_resource_usage.py ──► data/ops_spool/{observations,jobs}-*.jsonl ──► load_observations.py
                                                                                  └─► import_records()
                                                                                      ├─► service_observation
                                                                                      └─► job_execution

- **DB 只是 projection**：收集器不連 DB，告警也不經 DB（只有 P5 在發），所以這裡的表晚一點、少一點都
  不影響服務與告警。匯入一律冪等（自然鍵 ON CONFLICT），重匯同一份 spool 不會重複。
- 驗證在 Python 端先做一次（與 CHECK 同規則），不合的紀錄計入 rejected 並略過；萬一 DB 仍拒絕，
  該批退回逐列（savepoint）重試，壞的那一列略過——一筆壞資料不得卡住整個 spool。
- 批次執行：同一次 invocation 先 running、後 finished（之後不再變動）。沒看到結束、同主機同 unit 卻已有
  更晚開始的 invocation 時改成 lost（oneshot 不會重疊執行，它必定已經結束，只是結果不明）；日後若補到
  它的 finished 紀錄（spool 亂序）仍會改成 finished。

本模組不 commit：呼叫端（loader、DB 測試）決定交易邊界。
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from sqlalchemy import text
from sqlalchemy.exc import DataError, IntegrityError

SPOOL_VERSION = 1
SCOPES = ("host", "container", "service")
JOB_STATES = ("running", "finished", "lost")
SPOOL_JOB_STATES = ("running", "finished")  # lost 只由 DB 端判定，spool 裡不該出現
EXEC_CODES = ("exited", "killed", "dumped")

CHUNK = 1000

_HOST = re.compile(r"[^\x00-\x1f\x7f]{1,255}")
_METRIC = re.compile(r"[a-z][a-z0-9_]{0,63}")
_UNIT = re.compile(r"[A-Za-z0-9][A-Za-z0-9@._:-]{0,200}\.service")
_INVOCATION = re.compile(r"[0-9a-f]{32}")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_INT32 = (-(2**31), 2**31 - 1)


@dataclass
class ImportStats:
    observations: int = 0
    jobs: int = 0
    rejected: int = 0
    lost: int = 0

    def as_dict(self) -> dict:
        return {"observations": self.observations, "jobs": self.jobs, "rejected": self.rejected, "lost": self.lost}


# ── spool 紀錄的驗證與正規化（純函式，不碰 DB）──────────────────────────────


def parse_ts(value) -> datetime | None:
    """ISO 8601（必須帶時區）→ aware datetime。沒有時區的一律拒絕：主機時區與 DB 時區可能不同。"""
    if not isinstance(value, str) or not value:
        return None
    try:
        ts = datetime.fromisoformat(value)
    except ValueError:
        return None
    if ts.tzinfo is None:
        return None
    return ts


def _text(value, limit: int) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    return _CONTROL.sub("", value)[:limit] or None


def _host(value) -> str | None:
    return value if isinstance(value, str) and _HOST.fullmatch(value) else None


def observation_rows(rec: dict) -> list[dict] | None:
    """一行 observation → 多列（每個指標一列）。整行不合格回 None；個別指標不合格只略過那一個。"""
    observed = parse_ts(rec.get("observed_at"))
    host = _host(rec.get("host"))
    scope = rec.get("scope")
    subject = rec.get("subject")
    if (observed is None or host is None or scope not in SCOPES or not isinstance(subject, str)
            or not 1 <= len(subject) <= 200 or _CONTROL.search(subject)):
        return None
    detail = rec.get("detail")
    detail_json = json.dumps(detail, ensure_ascii=False) if isinstance(detail, dict) and detail else None
    base = {"observed_at": observed, "host": host, "scope": scope, "subject": subject}
    rows: list[dict] = []
    metrics = rec.get("metrics") if isinstance(rec.get("metrics"), dict) else {}
    for metric, value in metrics.items():
        if not isinstance(metric, str) or not _METRIC.fullmatch(metric):
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            continue
        rows.append({**base, "metric": metric, "value": float(value), "state": None, "detail": None})
    states = rec.get("states") if isinstance(rec.get("states"), dict) else {}
    for metric, state in states.items():
        if not isinstance(metric, str) or not _METRIC.fullmatch(metric):
            continue
        state = _text(state, 64)
        if state is None:
            continue
        # 附帶屬性只掛在狀態列上（同一個快照的數值列不必重複一份）
        rows.append({**base, "metric": metric, "value": None, "state": state, "detail": detail_json})
    # 沒有任何有效指標的觀測回空串列（合格但沒有可存的列：value 與 state 至少要一個）
    return rows


def job_row(rec: dict) -> dict | None:
    host, unit, inv, state = _host(rec.get("host")), rec.get("unit"), rec.get("invocation_id"), rec.get("state")
    started = parse_ts(rec.get("started_at"))
    observed = parse_ts(rec.get("observed_at"))
    if (host is None or not isinstance(unit, str) or not _UNIT.fullmatch(unit)
            or not isinstance(inv, str) or not _INVOCATION.fullmatch(inv)
            or state not in SPOOL_JOB_STATES or started is None or observed is None):
        return None
    finished = parse_ts(rec.get("finished_at"))
    if (state == "finished") != (finished is not None):
        return None
    status = rec.get("exit_status")
    code = rec.get("exec_main_code")
    service = rec.get("service")
    valid_status = isinstance(status, int) and not isinstance(status, bool) and _INT32[0] <= status <= _INT32[1]
    return {
        "host": host, "unit": unit,
        "service": service if isinstance(service, str) and 0 < len(service) <= 40 else None,
        "invocation_id": inv, "state": state, "started_at": started, "finished_at": finished,
        "result": _text(rec.get("result"), 64),
        "exit_status": status if valid_status else None,
        "exec_main_code": code if code in EXEC_CODES else None,
        "seen_at": observed,
    }


# ── 匯入 ─────────────────────────────────────────────────────────────────────

_INSERT_OBSERVATION = text("""
INSERT INTO research.service_observation (observed_at, host, scope, subject, metric, value, state, detail)
VALUES (:observed_at, :host, :scope, :subject, :metric, :value, :state, CAST(:detail AS jsonb))
ON CONFLICT (subject, metric, observed_at, scope, host) DO NOTHING
""")

# running 可以被更新（running 刷新 last_seen、或結束成 finished）；lost 只接受 finished（補到結束紀錄）；
# finished 之後不再變動（WHERE 擋住）。
_UPSERT_JOB = text("""
INSERT INTO research.job_execution
    (host, unit, service, invocation_id, state, started_at, finished_at, result, exit_status, exec_main_code,
     first_seen_at, last_seen_at)
VALUES (:host, :unit, :service, :invocation_id, :state, :started_at, :finished_at, :result, :exit_status,
        :exec_main_code, :seen_at, :seen_at)
ON CONFLICT (host, unit, invocation_id) DO UPDATE SET
    state          = EXCLUDED.state,
    finished_at    = EXCLUDED.finished_at,
    result         = EXCLUDED.result,
    exit_status    = EXCLUDED.exit_status,
    exec_main_code = EXCLUDED.exec_main_code,
    service        = COALESCE(EXCLUDED.service, research.job_execution.service),
    last_seen_at   = GREATEST(research.job_execution.last_seen_at, EXCLUDED.last_seen_at)
WHERE research.job_execution.state = 'running'
   OR (research.job_execution.state = 'lost' AND EXCLUDED.state = 'finished')
""")

# 同主機同 unit 已有更晚開始的 invocation，而這一列還停在 running：它必定已經結束（oneshot 不重疊）。
_MARK_LOST = text("""
UPDATE research.job_execution j SET state = 'lost'
WHERE j.state = 'running'
  AND j.unit = ANY(CAST(:units AS text[]))
  AND EXISTS (
      SELECT 1 FROM research.job_execution n
      WHERE n.host = j.host AND n.unit = j.unit AND n.started_at > j.started_at
  )
""")


def _chunks(rows: list[dict], size: int = CHUNK) -> Iterable[list[dict]]:
    for i in range(0, len(rows), size):
        yield rows[i:i + size]


async def _execute_rows(session, stmt, rows: list[dict]) -> int:
    """executemany；DB 拒絕（CHECK／型別）時該批改逐列重試，拒絕的列略過。回傳被拒絕的列數。"""
    rejected = 0
    for chunk in _chunks(rows):
        try:
            async with session.begin_nested():
                await session.execute(stmt, chunk)
            continue
        except (IntegrityError, DataError):
            pass
        for row in chunk:
            try:
                async with session.begin_nested():
                    await session.execute(stmt, row)
            except (IntegrityError, DataError):
                rejected += 1
    return rejected


async def import_records(session, *, observations: list[dict], jobs: list[dict]) -> ImportStats:
    """把已正規化的列寫進 DB（冪等；不 commit）。參數是 observation_rows／job_row 的輸出。"""
    stats = ImportStats()
    if observations:
        stats.rejected += await _execute_rows(session, _INSERT_OBSERVATION, observations)
        stats.observations = len(observations)
    if jobs:
        # 同一批裡同一次 invocation 可能有 running 與 finished 兩筆：依觀測時間排序，同時刻 finished 最後到。
        ordered = sorted(jobs, key=lambda r: (r["seen_at"], r["state"] == "finished"))
        stats.rejected += await _execute_rows(session, _UPSERT_JOB, ordered)
        stats.jobs = len(jobs)
        res = await session.execute(_MARK_LOST, {"units": sorted({r["unit"] for r in jobs})})
        stats.lost = max(res.rowcount or 0, 0)
    return stats


# ── 管理後台的唯讀查詢（/api/admin/jobs、/api/admin/observations）──────────────
#
# 時間範圍與筆數的上限由路由層驗證（超過回 400／422）；這裡只負責 SQL。沒有時區的時間一律當 UTC。

OBSERVATION_DEFAULT_WINDOW = timedelta(hours=1)
OBSERVATION_MAX_WINDOW = timedelta(days=7)
OBSERVATION_MAX_LIMIT = 5000
JOB_DEFAULT_WINDOW = timedelta(days=7)
JOB_MAX_WINDOW = timedelta(days=90)
JOB_MAX_LIMIT = 200


def _utc(ts: datetime | None) -> datetime | None:
    if ts is None:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def resolve_window(since: datetime | None, until: datetime | None, default: timedelta,
                   *, now: datetime | None = None) -> tuple[datetime, datetime]:
    """沒給 until＝現在；沒給 since＝until 往前 `default`。回傳的兩端都帶時區。"""
    until = _utc(until) or now or datetime.now(timezone.utc)
    since = _utc(since) or (until - default)
    return since, until


async def list_jobs(session, *, since: datetime, until: datetime, service: str | None = None,
                    unit: str | None = None, state: str | None = None, result: str | None = None,
                    limit: int = 50, offset: int = 0) -> tuple[int, list[dict]]:
    """批次執行紀錄，依開始時間新→舊；時間範圍看 started_at（[since, until]）。"""
    where = ["j.started_at >= :since", "j.started_at <= :until"]
    params: dict[str, Any] = {"since": since, "until": until, "limit": limit, "offset": offset}
    for col, val in (("service", service), ("unit", unit), ("state", state), ("result", result)):
        if val:
            where.append(f"j.{col} = :{col}")
            params[col] = val
    cond = " AND ".join(where)
    total = (await session.execute(
        text(f"SELECT count(*) FROM research.job_execution j WHERE {cond}"), params)).scalar_one()
    rows = (await session.execute(text(f"""
        SELECT j.host, j.unit, j.service, j.invocation_id, j.state, j.started_at, j.finished_at, j.result,
               j.exit_status, j.exec_main_code, j.last_seen_at
        FROM research.job_execution j WHERE {cond}
        ORDER BY j.started_at DESC, j.id DESC LIMIT :limit OFFSET :offset
    """), params)).mappings().all()
    return int(total), [dict(r) for r in rows]


async def list_observations(session, *, since: datetime, until: datetime, scope: str | None = None,
                            subject: str | None = None, metric: str | None = None,
                            limit: int = 500) -> tuple[bool, list[dict]]:
    """觀測值，新→舊、最多 `limit` 筆（多取一筆判斷 truncated）。時間範圍看 observed_at（[since, until]）。"""
    where = ["o.observed_at >= :since", "o.observed_at <= :until"]
    params: dict[str, Any] = {"since": since, "until": until, "limit": limit + 1}
    for col, val in (("scope", scope), ("subject", subject), ("metric", metric)):
        if val:
            where.append(f"o.{col} = :{col}")
            params[col] = val
    rows = (await session.execute(text(f"""
        SELECT o.observed_at, o.host, o.scope, o.subject, o.metric, o.value, o.state, o.detail
        FROM research.service_observation o WHERE {' AND '.join(where)}
        ORDER BY o.observed_at DESC, o.id DESC LIMIT :limit
    """), params)).mappings().all()
    truncated = len(rows) > limit
    out = []
    for r in rows[:limit]:
        row = dict(r)
        detail = row.get("detail")
        if isinstance(detail, str):  # 依驅動與型別資訊而定，jsonb 可能以字串回來
            try:
                detail = json.loads(detail)
            except ValueError:
                detail = None
        row["detail"] = detail if isinstance(detail, dict) else None
        out.append(row)
    return truncated, out
