"""監控投影：spool 紀錄 → DB（`scripts/load_observations.py`）與管理後台的唯讀查詢
（`/api/admin/jobs`、`/api/admin/observations`、`/api/admin/incidents*`）。

資料流（理由見 revision 0005、0006 的註解）：

    collect_resource_usage.py ──► data/ops_spool/{observations,jobs}-*.jsonl ──┐
    incident_handler.sh（P5）──► data/ops_spool/incidents-*.jsonl＋journal/*.log ┴► load_observations.py
                                                                                  └─► import_records()
                                                                                      ├─► service_observation
                                                                                      ├─► job_execution
                                                                                      └─► incident／incident_event

- **DB 只是 projection**：收集器不連 DB，告警也不經 DB（只有 P5 在發），所以這裡的表晚一點、少一點都
  不影響服務與告警。匯入一律冪等（自然鍵 ON CONFLICT），重匯同一份 spool 不會重複。
- 驗證在 Python 端先做一次（與 CHECK 同規則），不合的紀錄計入 rejected 並略過；萬一 DB 仍拒絕，
  該批退回逐列（savepoint）重試，壞的那一列略過——一筆壞資料不得卡住整個 spool。
- 事件：P5 每次已落地的狀態轉換一筆（event_id 去重）。incident 的 status／severity／reason／summary／
  last_event_at／event_count 由 incident_event **重算**（與匯入順序無關）；沒收到 RESOLVED、同主機同元件卻
  已有更晚開的事件時是 lost（P5 同元件同時只有一個事件，它必定已結束、只是時間不明），補到 RESOLVED 仍改
  resolved。journal 片段匯入前去控制字元、每行截斷並遮掉形似祕密的片段（與 `/api/admin/ops/*/logs` 同一套）。
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

from app.services import ops_rollup
from ops_agent.backends import clean_line

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

# 事件投影（revision 0006 的 CHECK 逐字一致）
INCIDENT_KINDS = ("service", "monitor_blind")
INCIDENT_STATUSES = ("firing", "resolved", "lost")
INCIDENT_SEVERITIES = ("CRITICAL", "WARNING")
EVENT_ACTIONS = ("FIRING", "REMINDER", "ESCALATED", "RESOLVED")
EVENT_SEVERITIES = ("CRITICAL", "WARNING", "RESOLVED")
JOURNAL_MAX_BYTES = 262144  # incident_event.journal_excerpt 的 CHECK
_INCIDENT_ID = re.compile(r"[A-Za-z0-9_.:-]{1,300}")
_EVENT_ID = re.compile(r"[A-Za-z0-9_.:-]{1,400}")
_COMPONENT = re.compile(r"[A-Za-z0-9_.-]{1,64}")


@dataclass
class ImportStats:
    observations: int = 0
    jobs: int = 0
    rejected: int = 0
    lost: int = 0
    events: int = 0
    incidents: int = 0

    def as_dict(self) -> dict:
        return {"observations": self.observations, "jobs": self.jobs, "rejected": self.rejected, "lost": self.lost,
                "events": self.events, "incidents": self.incidents}


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


def clean_journal(raw: str | None, *, keep: str = "tail") -> tuple[str | None, bool]:
    """journal 片段 → (可存的文字, 是否截斷)。

    逐行：去 ANSI 與控制字元、每行最多 2000 字、遮掉形似祕密的片段（`ops_agent.backends.clean_line`，
    與 logs API 同一套）。整段超過 JOURNAL_MAX_BYTES 時從 `keep` 的另一端截掉（FIRING 留最新的 tail、
    RESOLVED 留最早的 head，與 P5 擷取時同一個方向），只在行界切。
    """
    if not raw:
        return None, False
    lines = [clean_line(ln) for ln in raw.replace("\r\n", "\n").split("\n")]
    while lines and not lines[-1]:
        lines.pop()
    if not lines:
        return None, False
    text_ = "\n".join(lines) + "\n"
    if len(text_.encode("utf-8")) <= JOURNAL_MAX_BYTES:
        return text_, False
    kept: list[str] = []
    size = 0
    for ln in (reversed(lines) if keep == "tail" else lines):
        n = len(ln.encode("utf-8")) + 1
        if size + n > JOURNAL_MAX_BYTES:
            break
        kept.append(ln)
        size += n
    if keep == "tail":
        kept.reverse()
    return ("\n".join(kept) + "\n") if kept else None, True


def incident_event_row(rec: dict, journal: str | None = None) -> dict | None:
    """一行 incident_event（P5 寫的 spool）→ 匯入用的一列（含 incident 骨架欄位）。不合格回 None。

    `journal` 是 loader 讀到的片段原文（已解碼）；P5 已截斷過時 `journal_truncated` 照樣保留。
    """
    event_id, incident_id = rec.get("event_id"), rec.get("incident_id")
    host, component, kind = rec.get("host"), rec.get("component"), rec.get("kind")
    action, severity = rec.get("action"), rec.get("severity")
    occurred, first_seen = parse_ts(rec.get("occurred_at")), parse_ts(rec.get("first_seen_at"))
    if (not isinstance(event_id, str) or not _EVENT_ID.fullmatch(event_id)
            or not isinstance(incident_id, str) or not _INCIDENT_ID.fullmatch(incident_id)
            or not event_id.startswith(incident_id + ":")
            or _host(host) is None or not isinstance(component, str) or not _COMPONENT.fullmatch(component)
            or kind not in INCIDENT_KINDS or action not in EVENT_ACTIONS or severity not in EVENT_SEVERITIES
            or (action == "RESOLVED") != (severity == "RESOLVED")
            or occurred is None or first_seen is None):
        return None
    isev = rec.get("incident_severity")
    if isev not in INCIDENT_SEVERITIES:
        isev = severity if severity in INCIDENT_SEVERITIES else "WARNING"
    reason = _text(rec.get("reason"), 200) or "unknown"
    excerpt, cut = clean_journal(journal, keep="head" if action == "RESOLVED" else "tail")
    since, until = parse_ts(rec.get("journal_since")), parse_ts(rec.get("journal_until"))
    return {
        "incident_id": incident_id, "host": host, "component": component, "kind": kind,
        "probe_unit": _text(rec.get("probe_unit"), 255),
        "incident_severity": isev, "opened_at": first_seen,
        # 只有 RESOLVED 的事件（開場那一行沒寫進 spool）不拿「healthy」當事件原因
        "incident_reason": reason if action != "RESOLVED" else "unknown",
        "event_id": event_id, "occurred_at": occurred, "action": action, "severity": severity,
        "reason": reason, "status": _text(rec.get("status"), 32), "summary": _text(rec.get("summary"), 2000),
        "notified": rec.get("notified") is True, "journal_excerpt": excerpt,
        "journal_truncated": excerpt is not None and (cut or rec.get("journal_truncated") is True),
        "journal_since": since if excerpt is not None else None,
        "journal_until": until if excerpt is not None else None,
        "journal_units": _text(rec.get("journal_units"), 500) if excerpt is not None else None,
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


# 事件骨架：第一次見到這個 incident_id 時建立；彙總欄位由 _REFRESH_INCIDENTS 重算。
_INSERT_INCIDENT = text("""
INSERT INTO research.incident
    (incident_id, host, component, kind, probe_unit, status, severity, reason, summary, opened_at, last_event_at,
     resolved_at, event_count)
VALUES (:incident_id, :host, :component, :kind, :probe_unit,
        CASE WHEN CAST(:action AS text) = 'RESOLVED' THEN 'resolved' ELSE 'firing' END,
        :incident_severity, :incident_reason, NULL, :opened_at, :occurred_at,
        CASE WHEN CAST(:action AS text) = 'RESOLVED' THEN CAST(:occurred_at AS timestamptz) END, 0)
ON CONFLICT (incident_id) DO NOTHING
""")

_INSERT_EVENT = text("""
INSERT INTO research.incident_event
    (event_id, incident_id, occurred_at, action, severity, reason, status, summary, notified,
     journal_excerpt, journal_truncated, journal_since, journal_until, journal_units)
VALUES (:event_id, :incident_id, :occurred_at, :action, :severity, :reason, :status, :summary, :notified,
        :journal_excerpt, :journal_truncated, :journal_since, :journal_until, :journal_units)
ON CONFLICT (event_id) DO NOTHING
""")

# 由事件重算彙總欄位（與匯入順序無關）。status 先只分 resolved／firing，lost 由下一句判。
_REFRESH_INCIDENTS = text("""
UPDATE research.incident i SET
    status        = CASE WHEN agg.resolved_at IS NULL THEN 'firing' ELSE 'resolved' END,
    resolved_at   = agg.resolved_at,
    severity      = COALESCE(agg.severity, i.severity),
    reason        = COALESCE(agg.reason, i.reason),
    summary       = agg.first_summary,
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

# 還在 firing、同主機同元件卻已有更晚開的事件：P5 同元件同時只有一個事件，它必定已經結束（結束時間不明）。
_MARK_INCIDENTS_LOST = text("""
UPDATE research.incident i SET status = 'lost'
WHERE i.status = 'firing'
  AND i.component = ANY(CAST(:components AS text[]))
  AND EXISTS (
      SELECT 1 FROM research.incident n
      WHERE n.host = i.host AND n.component = i.component AND n.opened_at > i.opened_at
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


async def import_records(session, *, observations: list[dict], jobs: list[dict],
                         events: list[dict] | None = None) -> ImportStats:
    """把已正規化的列寫進 DB（冪等；不 commit）。參數是 observation_rows／job_row／incident_event_row 的輸出。"""
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
    if events:
        # 同一個事件的骨架只需要一列；取最早的那一則（通常是 FIRING）當骨架
        skeletons: dict[str, dict] = {}
        for e in sorted(events, key=lambda r: (r["occurred_at"], r["action"] == "RESOLVED")):
            skeletons.setdefault(e["incident_id"], e)
        stats.rejected += await _execute_rows(session, _INSERT_INCIDENT, list(skeletons.values()))
        stats.rejected += await _execute_rows(session, _INSERT_EVENT, events)
        stats.events = len(events)
        stats.incidents = len(skeletons)
        await session.execute(_REFRESH_INCIDENTS, {"ids": sorted(skeletons)})
        await session.execute(_MARK_INCIDENTS_LOST, {"components": sorted({e["component"] for e in events})})
    return stats


# ── 管理後台的唯讀查詢（/api/admin/jobs、/api/admin/observations）──────────────
#
# 時間範圍與筆數的上限由路由層驗證（超過回 400／422）；這裡只負責 SQL。沒有時區的時間一律當 UTC。
# 觀測的時間範圍上限＝保留期（90 天，revision 0007）；超過 24 小時的查詢由 `ops_rollup` 回聚合後的桶。

OBSERVATION_DEFAULT_WINDOW = timedelta(hours=1)
OBSERVATION_MAX_WINDOW = timedelta(days=90)
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
                            limit: int = 500, resolution: str = "raw") -> tuple[bool, list[dict]]:
    """觀測值，新→舊、最多 `limit` 筆（多取一筆判斷 truncated）。時間範圍看 observed_at（[since, until]）。

    `resolution` 是 `5m`／`1h` 時改回聚合後的桶（`ops_rollup.list_aggregated`，粒度由路由層以
    `ops_rollup.pick_resolution` 依查詢區間決定）。
    """
    if resolution != "raw":
        return await ops_rollup.list_aggregated(session, resolution=resolution, since=since, until=until,
                                                scope=scope, subject=subject, metric=metric, limit=limit)
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


# ── 事件投影的唯讀查詢（/api/admin/incidents、/api/admin/incidents/{incident_id}）──────────

INCIDENT_DEFAULT_WINDOW = timedelta(days=30)
INCIDENT_MAX_WINDOW = timedelta(days=366)
INCIDENT_MAX_LIMIT = 200
INCIDENT_MAX_EVENTS = 1000

_INCIDENT_COLS = """i.incident_id, i.host, i.component, i.kind, i.probe_unit, i.status, i.severity, i.reason,
    i.summary, i.opened_at, i.last_event_at, i.resolved_at, i.event_count"""


async def list_incidents(session, *, since: datetime, until: datetime, status: str | None = None,
                         component: str | None = None, limit: int = 50,
                         offset: int = 0) -> tuple[int, list[dict]]:
    """事件清單，依開場時間新→舊。時間篩選取「與 [since, until] 重疊」：開在 until 之前，而且還在 firing、
    或結束（resolved 用 resolved_at；lost 不知道何時結束，用最後一則事件的時間）在 since 之後。"""
    where = ["i.opened_at <= :until", "(i.status = 'firing' OR COALESCE(i.resolved_at, i.last_event_at) >= :since)"]
    params: dict[str, Any] = {"since": since, "until": until, "limit": limit, "offset": offset}
    for col, val in (("status", status), ("component", component)):
        if val:
            where.append(f"i.{col} = :{col}")
            params[col] = val
    cond = " AND ".join(where)
    total = (await session.execute(
        text(f"SELECT count(*) FROM research.incident i WHERE {cond}"), params)).scalar_one()
    rows = (await session.execute(text(
        f"SELECT {_INCIDENT_COLS} FROM research.incident i WHERE {cond} "
        "ORDER BY i.opened_at DESC, i.incident_id DESC LIMIT :limit OFFSET :offset"), params)).mappings().all()
    return int(total), [dict(r) for r in rows]


async def get_incident(session, incident_id: str) -> tuple[dict | None, list[dict], bool]:
    """單一事件＋它的轉換（舊→新，最多 INCIDENT_MAX_EVENTS 則，多出的從新的那端略過）＋是否截斷。"""
    row = (await session.execute(text(
        f"SELECT {_INCIDENT_COLS} FROM research.incident i WHERE i.incident_id = :id"),
        {"id": incident_id})).mappings().first()
    if row is None:
        return None, [], False
    events = (await session.execute(text("""
        SELECT event_id, occurred_at, action, severity, reason, status, summary, notified,
               journal_excerpt, journal_truncated, journal_since, journal_until, journal_units
        FROM research.incident_event WHERE incident_id = :id
        ORDER BY occurred_at, event_id LIMIT :limit
    """), {"id": incident_id, "limit": INCIDENT_MAX_EVENTS + 1})).mappings().all()
    truncated = len(events) > INCIDENT_MAX_EVENTS
    return dict(row), [dict(e) for e in events[:INCIDENT_MAX_EVENTS]], truncated
