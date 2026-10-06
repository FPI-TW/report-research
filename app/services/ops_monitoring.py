"""監控投影：spool 紀錄 → DB（scripts/load_observations.py）與管理後台的唯讀查詢（/api/admin/incidents 等）。

資料流（理由見 revision 0005／0006 的註解）：

    collect_resource_usage.py ──┐
                                ├─► data/ops_spool/*.jsonl ──► load_observations.py ──► 本模組 import_records()
    incident_handler.sh（P5）───┘                                                      ──► service_observation
                                                                                           job_execution
                                                                                           incident／incident_event

- **DB 只是 projection**。告警只有 P5（incident_handler.sh）在發，它不碰 DB；這裡的表晚一點、少一點都
  不影響告警。所以匯入一律冪等（自然鍵 ON CONFLICT），重匯同一份 spool 不會重複。
- incident 的 status／severity／last_event_at／event_count 由 incident_event **重算**（`_REFRESH_INCIDENTS`），
  與匯入順序無關：事件可以亂序、重複、分批到達。
- 驗證在 Python 端先做一次（與 CHECK 同規則），不合的紀錄計入 rejected 並略過；萬一 DB 仍拒絕，
  該批退回逐列（savepoint）重試，壞的那一列略過——一筆壞資料不得卡住整個 spool。

本模組不 commit：呼叫端（loader、DB 測試）決定交易邊界。
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from sqlalchemy import text
from sqlalchemy.exc import DataError, IntegrityError

SCOPES = ("host", "container", "service")
JOB_STATES = ("running", "finished")
EXEC_CODES = ("exited", "killed", "dumped")
INCIDENT_KINDS = ("service", "monitor_blind")
INCIDENT_STATUSES = ("firing", "resolved")
INCIDENT_SEVERITIES = ("CRITICAL", "WARNING")
EVENT_ACTIONS = ("FIRING", "REMINDER", "ESCALATED", "RESOLVED")
EVENT_SEVERITIES = ("CRITICAL", "WARNING", "RESOLVED")

JOURNAL_MAX_BYTES = 262144  # 與 incident_event.journal_excerpt 的 CHECK 相同
CHUNK = 1000

_HOST = re.compile(r".{1,255}", re.S)
_METRIC = re.compile(r"[a-z][a-z0-9_]{0,63}")
_UNIT = re.compile(r"[A-Za-z0-9][A-Za-z0-9@._:-]{0,200}\.service")
_INVOCATION = re.compile(r"[0-9a-f]{32}")
_INCIDENT_ID = re.compile(r"[A-Za-z0-9_.:-]{1,300}")
_EVENT_ID = re.compile(r"[A-Za-z0-9_.:-]{1,400}")
_COMPONENT = re.compile(r"[A-Za-z0-9_.-]{1,64}")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


@dataclass
class ImportStats:
    observations: int = 0
    jobs: int = 0
    events: int = 0
    rejected: int = 0
    incident_ids: set[str] = field(default_factory=set)

    def as_dict(self) -> dict:
        return {"observations": self.observations, "jobs": self.jobs, "events": self.events,
                "rejected": self.rejected, "incidents": len(self.incident_ids)}


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


def observation_rows(rec: dict) -> list[dict] | None:
    """一行 observation → 多列（每個指標一列）。整行不合格回 None。"""
    observed = parse_ts(rec.get("observed_at"))
    host = rec.get("host")
    scope = rec.get("scope")
    subject = rec.get("subject")
    if (observed is None or not isinstance(host, str) or not _HOST.fullmatch(host) or scope not in SCOPES
            or not isinstance(subject, str) or not 1 <= len(subject) <= 200):
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
    return rows


def job_row(rec: dict) -> dict | None:
    host, unit, inv, state = rec.get("host"), rec.get("unit"), rec.get("invocation_id"), rec.get("state")
    started = parse_ts(rec.get("started_at"))
    observed = parse_ts(rec.get("observed_at"))
    if (not isinstance(host, str) or not _HOST.fullmatch(host) or not isinstance(unit, str)
            or not _UNIT.fullmatch(unit) or not isinstance(inv, str) or not _INVOCATION.fullmatch(inv)
            or state not in JOB_STATES or started is None or observed is None):
        return None
    finished = parse_ts(rec.get("finished_at"))
    if (state == "finished") != (finished is not None):
        return None
    status = rec.get("exit_status")
    code = rec.get("exec_main_code")
    service = rec.get("service")
    return {
        "host": host, "unit": unit,
        "service": service if isinstance(service, str) and len(service) <= 40 else None,
        "invocation_id": inv, "state": state, "started_at": started, "finished_at": finished,
        "result": _text(rec.get("result"), 64),
        "exit_status": status if isinstance(status, int) and not isinstance(status, bool) else None,
        "exec_main_code": code if code in EXEC_CODES else None,
        "seen_at": observed,
    }


def clean_journal(raw: str | None) -> tuple[str | None, bool]:
    """journal 片段：去掉 NUL 與控制字元（保留換行、tab），超過上限從舊的那端截掉。"""
    if not raw:
        return None, False
    cleaned = _CONTROL.sub("", raw.replace("\r\n", "\n"))
    data = cleaned.encode("utf-8")
    if len(data) <= JOURNAL_MAX_BYTES:
        return cleaned or None, False
    return data[-JOURNAL_MAX_BYTES:].decode("utf-8", "ignore"), True


def incident_event_row(rec: dict, journal: str | None = None) -> dict | None:
    """一行 incident_event → {incident 欄位, event 欄位}。不合格回 None。"""
    event_id, incident_id = rec.get("event_id"), rec.get("incident_id")
    host, component, kind = rec.get("host"), rec.get("component"), rec.get("kind")
    action, severity = rec.get("action"), rec.get("severity")
    occurred, first_seen = parse_ts(rec.get("occurred_at")), parse_ts(rec.get("first_seen_at"))
    if (not isinstance(event_id, str) or not _EVENT_ID.fullmatch(event_id)
            or not isinstance(incident_id, str) or not _INCIDENT_ID.fullmatch(incident_id)
            or not isinstance(host, str) or not _HOST.fullmatch(host)
            or not isinstance(component, str) or not _COMPONENT.fullmatch(component)
            or kind not in INCIDENT_KINDS or action not in EVENT_ACTIONS or severity not in EVENT_SEVERITIES
            or occurred is None or first_seen is None):
        return None
    if (action == "RESOLVED") != (severity == "RESOLVED"):
        return None
    isev = rec.get("incident_severity")
    if isev not in INCIDENT_SEVERITIES:
        isev = severity if severity in INCIDENT_SEVERITIES else "WARNING"
    reason = _text(rec.get("reason"), 200) or "unknown"
    summary = _text(rec.get("summary"), 2000)
    excerpt, cut = clean_journal(journal)
    truncated = bool(rec.get("journal_truncated") is True or cut) and excerpt is not None
    return {
        "incident_id": incident_id, "host": host, "component": component, "kind": kind,
        "incident_severity": isev, "opened_at": first_seen,
        "event_id": event_id, "occurred_at": occurred, "action": action, "severity": severity,
        "reason": reason, "status": _text(rec.get("status"), 32), "summary": summary,
        "notified": rec.get("notified") is True, "journal_excerpt": excerpt, "journal_truncated": truncated,
    }


# ── 匯入 ─────────────────────────────────────────────────────────────────────

_INSERT_OBSERVATION = text("""
INSERT INTO research.service_observation (observed_at, host, scope, subject, metric, value, state, detail)
VALUES (:observed_at, :host, :scope, :subject, :metric, :value, :state, CAST(:detail AS jsonb))
ON CONFLICT (subject, metric, observed_at, scope, host) DO NOTHING
""")

# 執行中先記 running；同一次 invocation 結束後更新成 finished，之後不再變動（WHERE 擋住）。
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
""")

_INSERT_INCIDENT = text("""
INSERT INTO research.incident
    (incident_id, host, component, kind, status, severity, reason, summary, opened_at, last_event_at,
     resolved_at, event_count)
VALUES (:incident_id, :host, :component, :kind,
        CASE WHEN CAST(:action AS text) = 'RESOLVED' THEN 'resolved' ELSE 'firing' END,
        :incident_severity, :reason,
        CASE WHEN CAST(:action AS text) = 'FIRING' THEN CAST(:summary AS text) END,
        :opened_at, CAST(:occurred_at AS timestamptz),
        CASE WHEN CAST(:action AS text) = 'RESOLVED' THEN CAST(:occurred_at AS timestamptz) END, 0)
ON CONFLICT (incident_id) DO NOTHING
""")

_INSERT_EVENT = text("""
INSERT INTO research.incident_event
    (event_id, incident_id, occurred_at, action, severity, reason, status, summary, notified,
     journal_excerpt, journal_truncated)
VALUES (:event_id, :incident_id, :occurred_at, :action, :severity, :reason, :status, :summary, :notified,
        :journal_excerpt, :journal_truncated)
ON CONFLICT (event_id) DO NOTHING
""")

# 由事件重算 incident 的彙總欄位（與匯入順序無關）。
_REFRESH_INCIDENTS = text("""
UPDATE research.incident i SET
    status        = CASE WHEN agg.resolved_at IS NULL THEN 'firing' ELSE 'resolved' END,
    resolved_at   = agg.resolved_at,
    severity      = COALESCE(agg.severity, i.severity),
    reason        = COALESCE(agg.reason, i.reason),
    summary       = COALESCE(agg.first_summary, i.summary),
    last_event_at = agg.last_event_at,
    event_count   = agg.n
FROM (
    SELECT e.incident_id,
           count(*) AS n,
           max(e.occurred_at) AS last_event_at,
           min(e.occurred_at) FILTER (WHERE e.action = 'RESOLVED') AS resolved_at,
           (array_agg(e.severity ORDER BY e.occurred_at DESC, e.event_id DESC)
                FILTER (WHERE e.action <> 'RESOLVED'))[1] AS severity,
           (array_agg(e.reason ORDER BY e.occurred_at DESC, e.event_id DESC)
                FILTER (WHERE e.action <> 'RESOLVED'))[1] AS reason,
           (array_agg(e.summary ORDER BY e.occurred_at, e.event_id)
                FILTER (WHERE e.action = 'FIRING' AND e.summary IS NOT NULL))[1] AS first_summary
    FROM research.incident_event e
    WHERE e.incident_id = ANY(CAST(:ids AS text[]))
    GROUP BY e.incident_id
) agg
WHERE i.incident_id = agg.incident_id
""")


def _chunks(rows: list[dict], size: int = CHUNK) -> Iterable[list[dict]]:
    for i in range(0, len(rows), size):
        yield rows[i:i + size]


async def _execute_rows(session, stmt, rows: list[dict]) -> int:
    """executemany；DB 拒絕（CHECK／FK／型別）時該批改逐列重試，拒絕的列略過。回傳被拒絕的列數。"""
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


async def import_records(session, *, observations: list[dict], jobs: list[dict],
                         events: list[dict]) -> ImportStats:
    """把已正規化的列寫進 DB（冪等；不 commit）。參數是 observation_rows／job_row／incident_event_row 的輸出。"""
    stats = ImportStats()
    if observations:
        stats.rejected += await _execute_rows(session, _INSERT_OBSERVATION, observations)
        stats.observations = len(observations)
    if jobs:
        # 同一批裡同一次 invocation 可能有 running 與 finished 兩筆：依觀測時間排序，finished 最後到。
        ordered = sorted(jobs, key=lambda r: (r["seen_at"], r["state"] == "finished"))
        stats.rejected += await _execute_rows(session, _UPSERT_JOB, ordered)
        stats.jobs = len(jobs)
    if events:
        stats.rejected += await _execute_rows(session, _INSERT_INCIDENT, events)
        stats.rejected += await _execute_rows(session, _INSERT_EVENT, events)
        stats.events = len(events)
        stats.incident_ids = {e["incident_id"] for e in events}
        await session.execute(_REFRESH_INCIDENTS, {"ids": sorted(stats.incident_ids)})
    return stats


# ── 管理後台的唯讀查詢 ───────────────────────────────────────────────────────

OBSERVATION_MAX_LIMIT = 5000
DEFAULT_OBSERVATION_WINDOW = timedelta(hours=1)


def _utc(ts: datetime | None) -> datetime | None:
    if ts is None:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


_INCIDENT_COLS = """i.incident_id, i.host, i.component, i.kind, i.status, i.severity, i.reason, i.summary,
    i.opened_at, i.last_event_at, i.resolved_at, i.event_count"""


async def list_incidents(session, *, status: str | None = None, component: str | None = None,
                         since: datetime | None = None, until: datetime | None = None,
                         limit: int = 50, offset: int = 0) -> tuple[int, list[dict]]:
    """事件清單，新→舊。時間篩選取「與 [since, until] 重疊」：開在 until 之前、而且還沒結束或結束在 since 之後。"""
    where = ["TRUE"]
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if status:
        where.append("i.status = :status")
        params["status"] = status
    if component:
        where.append("i.component = :component")
        params["component"] = component
    if since:
        where.append("(i.resolved_at IS NULL OR i.resolved_at >= :since)")
        params["since"] = _utc(since)
    if until:
        where.append("i.opened_at <= :until")
        params["until"] = _utc(until)
    cond = " AND ".join(where)
    total = (await session.execute(
        text(f"SELECT count(*) FROM research.incident i WHERE {cond}"), params)).scalar_one()
    rows = (await session.execute(text(
        f"SELECT {_INCIDENT_COLS} FROM research.incident i WHERE {cond} "
        "ORDER BY i.opened_at DESC, i.incident_id DESC LIMIT :limit OFFSET :offset"), params)).mappings().all()
    return int(total), [dict(r) for r in rows]


async def get_incident(session, incident_id: str) -> tuple[dict | None, list[dict]]:
    row = (await session.execute(text(
        f"SELECT {_INCIDENT_COLS} FROM research.incident i WHERE i.incident_id = :id"),
        {"id": incident_id})).mappings().first()
    if row is None:
        return None, []
    events = (await session.execute(text("""
        SELECT event_id, occurred_at, action, severity, reason, status, summary, notified,
               journal_excerpt, journal_truncated
        FROM research.incident_event WHERE incident_id = :id ORDER BY occurred_at, event_id
    """), {"id": incident_id})).mappings().all()
    return dict(row), [dict(e) for e in events]


async def list_jobs(session, *, unit: str | None = None, service: str | None = None,
                    state: str | None = None, result: str | None = None,
                    since: datetime | None = None, until: datetime | None = None,
                    limit: int = 50, offset: int = 0) -> tuple[int, list[dict]]:
    """批次執行紀錄，依開始時間新→舊；時間篩選看 started_at。"""
    where = ["TRUE"]
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    for col, val in (("unit", unit), ("service", service), ("state", state), ("result", result)):
        if val:
            where.append(f"j.{col} = :{col}")
            params[col] = val
    if since:
        where.append("j.started_at >= :since")
        params["since"] = _utc(since)
    if until:
        where.append("j.started_at <= :until")
        params["until"] = _utc(until)
    cond = " AND ".join(where)
    total = (await session.execute(
        text(f"SELECT count(*) FROM research.job_execution j WHERE {cond}"), params)).scalar_one()
    rows = (await session.execute(text(f"""
        SELECT j.host, j.unit, j.service, j.invocation_id, j.state, j.started_at, j.finished_at, j.result,
               j.exit_status, j.exec_main_code
        FROM research.job_execution j WHERE {cond}
        ORDER BY j.started_at DESC, j.id DESC LIMIT :limit OFFSET :offset
    """), params)).mappings().all()
    return int(total), [dict(r) for r in rows]


async def list_observations(session, *, scope: str | None = None, subject: str | None = None,
                            metric: str | None = None, since: datetime | None = None,
                            until: datetime | None = None,
                            limit: int = 500) -> tuple[datetime, datetime, bool, list[dict]]:
    """觀測值，新→舊、最多 `limit` 筆（多一筆用來判斷 truncated）。沒給時間範圍時取最近一小時。"""
    until = _utc(until) or datetime.now(timezone.utc)
    since = _utc(since) or (until - DEFAULT_OBSERVATION_WINDOW)
    limit = max(1, min(limit, OBSERVATION_MAX_LIMIT))
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
        if isinstance(row.get("detail"), str):  # text() 查詢不帶型別資訊，jsonb 以字串回來
            try:
                row["detail"] = json.loads(row["detail"])
            except ValueError:
                row["detail"] = None
        if not isinstance(row.get("detail"), dict):
            row["detail"] = None
        out.append(row)
    return since, until, truncated, out
