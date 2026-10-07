"""Security Operations（Admin v2 Security lane）：登入事件的記錄與彙整、狀態型安全告警、保留期清除。

## 分工

- `web/routers/auth_pages.py`、`web/routers/account_security.py`：登入、登出、權限提升的結果經
  `record_event`（fail-open）寫 `research.auth_event`。
- `web/routers/admin_security.py`：`/api/admin/security/*`——事件清單、可疑 IP、session 總覽與撤銷、
  高風險時間線、稽核鏈、TOTP 採用率。
- `web/routers/health.py`：`/healthz/security` 把 `evaluate_alerts` 的結論縮成一個字串（只回答本機直連）。
- `scripts/check_security_health.sh` → `report-mark-security-incident`：把上面那個字串轉成退出碼，交給既有的
  P5（`scripts/incident_handler.sh`，不改它）。
- `scripts/security_retention.py`（每日 oneshot）：`purge_expired`——auth_event 超過保留期（至少 365 天）、
  結束超過 90 天的 session。
- `scripts/audit_anchor.py`：每日錨定後以 `write_anchor_status` 寫 `data/health/audit_anchor.json`，
  安全頁讀 `read_anchor_status`。

## 刻意的設計

- **記錄失敗絕不影響登入**：`record_event` 吞掉所有例外（含逾時）、只記 WARNING。`accounts.record_auth_event`
  本身會把 DB 錯誤往上拋，fail-open 的責任在這裡。
- **不存帳號名稱**：`accounts.record_auth_event` 的介面上就沒有帳號名稱；帳號不存在時 user_id 也是 NULL
  （使用者常把密碼誤打進帳號欄）。清單端點顯示的帳號名稱是讀取時以 user_id 對 `app_user` 現值 join 出來的，
  已刪除帳號一律不顯示。
- **被限流、非 HTTPS 遭拒、第二步沒有有效暫時憑證只在記憶體計數**（`ThrottledTally`）：攻擊時這三類請求
  不受（或已被）每 IP 限流擋下、可以無限多，每一筆都寫 DB
  就是把攻擊放大成 DB 寫入。每個 IP 累計、至少間隔 `TALLY_FLUSH_SECONDS` 才寫一列彙總（`count`）。
  寫入時機是下一次登入請求或下一次 `/healthz/security`（探針每 2 分鐘打一次），所以最晚約 3 分鐘落庫；
  web 重啟時尚未落庫的計數會遺失（只是彙總，不影響限流本身）。追蹤的 IP 數有上限，超過的併進 ip=NULL 那一列。
- **每 IP 失敗限流（`web/auth.py` 的 `_FAILS`）原封不動**（使用者定案 8）；這裡只觀察、不鎖帳號、不封 IP。
- **告警只有狀態型**（定案 9）：`evaluate_alerts` 看「此刻的視窗」，條件消失就恢復；一次性事件（例如有人被授予
  super）只在高風險時間線上，不送 Slack。
- **稽核鏈驗證是 O(n)**：結果快取 `AUDIT_VERIFY_CACHE_SECONDS`（3600）秒，安全頁與 `/healthz/security` 共用；
  驗證本身失敗（DB 不可用）的結論只快取 `_AUDIT_ERROR_TTL` 秒。
- 本模組的模組級狀態（兩個 tally、稽核鏈快取）由 `reset()` 清空，`tests/conftest.py` 每題前後呼叫。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import tempfile
import time
import uuid
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

from app.config import get_settings
from app.services.data_health import results_dir
from app.services.db import SessionFactory

logger = logging.getLogger(__name__)

# ───── 事件記錄（fail-open）─────

RECORD_TIMEOUT_SECONDS = 3.0
# 登入失敗類事件：全站登入失敗數與可疑 IP 都用這組。login.locked 是被限流擋下的嘗試（彙總列，count＞1）。
FAILURE_EVENTS = ("login.failure", "login.totp_failure")
LOCKED_EVENT = "login.locked"
INSECURE_EVENT = "login.insecure"


async def record_event(api: Any, event: str, **fields: Any) -> bool:
    """經 `api.record_auth_event`（`web.deps.accounts`）寫一列事件；任何失敗都只記 WARNING、回 False。

    呼叫端是登入流程：記不了事件不能讓登入失敗、也不能讓它卡住（`RECORD_TIMEOUT_SECONDS`）。
    """
    try:
        async with asyncio.timeout(RECORD_TIMEOUT_SECONDS):
            await api.record_auth_event(event, **fields)
        return True
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.warning("登入事件寫入失敗（不影響登入）event=%s", event, exc_info=True)
        return False


TALLY_FLUSH_SECONDS = 60.0
TALLY_MAX_KEYS = 256


class ThrottledTally:
    """每 IP 的記憶體計數；距第一筆未落庫的計數至少 `flush_seconds` 秒才可 `drain`。

    超過 `max_keys` 個相異 IP 時，新的 IP 併進 None（落庫時 ip=NULL），計數不丟。
    """

    def __init__(self, event: str, *, reason: str | None = None, flush_seconds: float = TALLY_FLUSH_SECONDS,
                 max_keys: int = TALLY_MAX_KEYS, clock: Callable[[], float] = time.monotonic) -> None:
        self.event = event
        self.reason = reason
        self.flush_seconds = float(flush_seconds)
        self.max_keys = max(1, int(max_keys))
        self._clock = clock
        self._counts: dict[str | None, int] = {}
        self._since: float | None = None

    def note(self, ip: str | None) -> None:
        key = (ip or None)
        if key not in self._counts and len(self._counts) >= self.max_keys:
            key = None
        if self._since is None:
            self._since = self._clock()
        self._counts[key] = self._counts.get(key, 0) + 1

    @property
    def pending(self) -> int:
        return sum(self._counts.values())

    def due(self, *, force: bool = False) -> bool:
        if not self._counts:
            return False
        return force or (self._since is not None and self._clock() - self._since >= self.flush_seconds)

    def drain(self) -> list[tuple[str | None, int]]:
        rows = sorted(self._counts.items(), key=lambda kv: (kv[0] is None, kv[0] or ""))
        self._counts = {}
        self._since = None
        return rows

    def reset(self) -> None:
        self._counts = {}
        self._since = None


LOCKED_TALLY = ThrottledTally(LOCKED_EVENT)
INSECURE_TALLY = ThrottledTally(INSECURE_EVENT)
# 第二步沒有（或帶著失效的）暫時憑證：不計入每 IP 限流（那不是猜錯驗證碼），所以同樣只能在記憶體計數，
# 否則任何人都能以不帶 cookie 的 step=totp 請求無限灌 DB。
EXPIRED_CHALLENGE_TALLY = ThrottledTally("login.totp_failure", reason="expired_challenge")
_TALLIES = (LOCKED_TALLY, INSECURE_TALLY, EXPIRED_CHALLENGE_TALLY)


async def flush_tallies(api: Any, *, force: bool = False) -> int:
    """把到期的彙總寫成 auth_event 列（每 IP 一列、`count`＝期間內的請求數）。回寫入的列數。永不拋例外。"""
    written = 0
    for tally in _TALLIES:
        if not tally.due(force=force):
            continue
        for ip, count in tally.drain():
            fields: dict[str, Any] = {"ip": ip, "count": count}
            if tally.reason is not None:
                fields["reason"] = tally.reason
            if await record_event(api, tally.event, **fields):
                written += 1
    return written


async def note_throttled(api: Any, tally: ThrottledTally, ip: str | None) -> None:
    """記一筆被限流（或非 HTTPS 遭拒）的請求：只加記憶體計數，到期才落庫。"""
    tally.note(ip)
    await flush_tallies(api)


# ───── 稽核鏈驗證（快取）─────

_AUDIT_ERROR_TTL = 60.0


@dataclass(frozen=True)
class AuditChainSnapshot:
    """`verify_audit_chain` 的快取結論。state：ok／broken／error。"""

    state: str
    total: int | None = None
    head_id: int | None = None
    broken_count: int = 0
    checked_at: datetime | None = None
    error: str | None = None


_audit_cache: tuple[float, AuditChainSnapshot] | None = None


async def audit_chain_snapshot(verify: Callable[[], Awaitable[Any]], *, max_age: float | None = None,
                               clock: Callable[[], float] = time.monotonic) -> AuditChainSnapshot:
    """稽核鏈的驗證結論；`max_age`（預設 `AUDIT_VERIFY_CACHE_SECONDS`）內重用上一次的結果。永不拋例外。"""
    global _audit_cache
    ttl = float(get_settings().audit_verify_cache_seconds if max_age is None else max_age)
    now = clock()
    if _audit_cache is not None:
        at, snap = _audit_cache
        limit = _AUDIT_ERROR_TTL if snap.state == "error" else ttl
        if now - at < min(limit, ttl):
            return snap
    try:
        status = await verify()
        snap = AuditChainSnapshot(
            state="ok" if status.ok else "broken", total=int(status.total),
            head_id=status.head_id, broken_count=len(tuple(status.broken_ids or ())),
            checked_at=datetime.now(timezone.utc),
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("稽核鏈驗證失敗（視為判不出來）", exc_info=True)
        snap = AuditChainSnapshot(state="error", checked_at=datetime.now(timezone.utc), error=type(exc).__name__)
    _audit_cache = (now, snap)
    return snap


# ───── 錨定狀態檔（data/health/audit_anchor.json）─────

ANCHOR_STATUS_NAME = "audit_anchor"
ANCHOR_STATUS_MAX_BYTES = 16 * 1024
# 錨定 timer 每日一次（04:15）：超過 48 小時沒有新結果＝過期（timer 停了或一直起不來）。
ANCHOR_STALE_HOURS = 48.0
ANCHOR_RESULTS = ("ok", "tamper", "error")


def anchor_status_path():
    return results_dir() / f"{ANCHOR_STATUS_NAME}.json"


def write_anchor_status(payload: Mapping[str, Any], *, now: datetime | None = None) -> bool:
    """原子寫入錨定結果（同目錄 tempfile → fsync → os.replace）。失敗只印到 stderr、回 False，不拋。

    呼叫端是 `scripts/audit_anchor.py`：狀態檔只是給安全頁看的，絕不改變錨定的退出碼。
    內容只有結果、計數、鏈頭 id 與雜湊前綴，沒有稽核內容。
    """
    try:
        path = anchor_status_path()
        body = {**payload, "name": ANCHOR_STATUS_NAME, "written_at": (now or datetime.now(timezone.utc)).isoformat()}
        data = json.dumps(body, ensure_ascii=False, default=str, sort_keys=True)
        if len(data.encode("utf-8")) > ANCHOR_STATUS_MAX_BYTES:
            raise ValueError(f"錨定狀態超過 {ANCHOR_STATUS_MAX_BYTES} bytes")
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=f".{ANCHOR_STATUS_NAME}.", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return True
    except Exception as exc:  # noqa: BLE001 — 狀態檔只是輔助顯示
        print(f"錨定狀態檔寫入失敗（不影響錨定結果）：{type(exc).__name__}: {exc}", file=sys.stderr)
        return False


@dataclass(frozen=True)
class AnchorStatus:
    """最後一次錨定。state：ok／tamper／error（腳本的結果）、stale（ok 但過期）、missing（從未寫過）、corrupt。"""

    state: str
    result: str | None = None
    at: str | None = None
    written_at: str | None = None
    age_hours: float | None = None
    head_id: int | None = None
    total: int | None = None
    anchors_checked: int | None = None
    message: str | None = None


def _parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        ts = datetime.fromisoformat(value)
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def _opt_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def read_anchor_status(*, now: datetime | None = None) -> AnchorStatus:
    path = anchor_status_path()
    try:
        if path.stat().st_size > ANCHOR_STATUS_MAX_BYTES:
            return AnchorStatus(state="corrupt", message="too_large")
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return AnchorStatus(state="missing")
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        return AnchorStatus(state="corrupt", message=type(exc).__name__)
    if not isinstance(data, dict) or data.get("name") != ANCHOR_STATUS_NAME or data.get("result") not in ANCHOR_RESULTS:
        return AnchorStatus(state="corrupt", message="shape")
    written = _parse_ts(data.get("written_at"))
    age = None
    if written is not None:
        age = round(max(0.0, ((now or datetime.now(timezone.utc)) - written).total_seconds() / 3600), 1)
    state = data["result"]
    if state == "ok" and (age is None or age > ANCHOR_STALE_HOURS):
        state = "stale"
    message = data.get("message")
    return AnchorStatus(
        state=state, result=data["result"], at=data.get("at") if isinstance(data.get("at"), str) else None,
        written_at=data.get("written_at") if isinstance(data.get("written_at"), str) else None, age_hours=age,
        head_id=_opt_int(data.get("head_id")), total=_opt_int(data.get("total")),
        anchors_checked=_opt_int(data.get("anchors_checked")),
        message=message[:500] if isinstance(message, str) else None,
    )


# ───── 狀態型告警（/healthz/security）─────

STATE_OK = "ok"
STATE_UNKNOWN = "unknown"
# 告警狀態依嚴重度排序：多項同時成立時回最前面那一個（/healthz/security 只回一個鍵）。
ALERT_STATES = ("audit_chain_broken", "elevate_failures", "account_failures", "login_failures")
HEALTH_STATES = (STATE_OK, STATE_UNKNOWN, *ALERT_STATES)


@dataclass(frozen=True)
class AlertEvaluation:
    window_minutes: int
    login_failures: int = 0
    login_failure_threshold: int = 0
    # 視窗內「上一次登入成功之後」連續失敗達門檻的帳號（user_id）。
    accounts_over: tuple[str, ...] = ()
    max_account_failures: int = 0
    account_failure_threshold: int = 0
    elevate_failures: int = 0
    elevate_failure_threshold: int = 0
    audit_chain: AuditChainSnapshot = field(default_factory=lambda: AuditChainSnapshot(state="error"))
    events_error: str | None = None  # 事件查詢失敗（DB 不可用、逾時）

    @property
    def triggered(self) -> tuple[str, ...]:
        out = []
        if self.audit_chain.state == "broken":
            out.append("audit_chain_broken")
        if self.events_error is None:
            if self.elevate_failures >= self.elevate_failure_threshold:
                out.append("elevate_failures")
            if self.accounts_over:
                out.append("account_failures")
            if self.login_failures >= self.login_failure_threshold:
                out.append("login_failures")
        return tuple(out)

    @property
    def state(self) -> str:
        triggered = self.triggered
        if triggered:
            return triggered[0]
        if self.events_error is not None or self.audit_chain.state == "error":
            return STATE_UNKNOWN
        return STATE_OK


_EVENTS_SQL = text(
    "SELECT "
    "  COALESCE(sum(count) FILTER (WHERE event IN ('login.failure', 'login.totp_failure', 'login.locked')), 0), "
    "  COALESCE(sum(count) FILTER (WHERE event = 'elevate.failure'), 0) "
    "FROM research.auth_event WHERE occurred_at > now() - make_interval(mins => :mins)"
)
# 同一帳號連續失敗：視窗內、而且晚於該帳號最後一次登入成功的失敗（密碼錯、停用、TOTP 錯）。
_ACCOUNT_SQL = text(
    "SELECT e.user_id, sum(e.count) AS n FROM research.auth_event e "
    "WHERE e.occurred_at > now() - make_interval(mins => :mins) AND e.user_id IS NOT NULL "
    "  AND e.event IN ('login.failure', 'login.totp_failure') "
    "  AND e.occurred_at > COALESCE((SELECT max(s.occurred_at) FROM research.auth_event s "
    "                                WHERE s.user_id = e.user_id AND s.event = 'login.success'), '-infinity') "
    "GROUP BY e.user_id ORDER BY n DESC, e.user_id"
)


async def evaluate_alerts(verify: Callable[[], Awaitable[Any]], *, session_factory=SessionFactory,
                          settings=None) -> AlertEvaluation:
    """依 `SECURITY_*` 門檻判斷此刻的安全狀態。永不拋例外：事件查詢失敗記在 `events_error`。"""
    settings = settings or get_settings()
    mins = max(1, int(settings.security_window_minutes))
    base = dict(
        window_minutes=mins,
        login_failure_threshold=max(1, int(settings.security_login_failure_threshold)),
        account_failure_threshold=max(1, int(settings.security_account_failure_threshold)),
        elevate_failure_threshold=max(1, int(settings.security_elevate_failure_threshold)),
    )
    audit = await audit_chain_snapshot(verify)
    try:
        async with session_factory() as session:
            login_n, elevate_n = (await session.execute(_EVENTS_SQL, {"mins": mins})).one()
            rows = (await session.execute(_ACCOUNT_SQL, {"mins": mins})).all()
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("安全告警的事件查詢失敗（視為判不出來）", exc_info=True)
        return AlertEvaluation(**base, audit_chain=audit, events_error=type(exc).__name__)
    over = tuple(str(r[0]) for r in rows if int(r[1]) >= base["account_failure_threshold"])
    return AlertEvaluation(
        **base, login_failures=int(login_n), elevate_failures=int(elevate_n), accounts_over=over,
        max_account_failures=max((int(r[1]) for r in rows), default=0), audit_chain=audit,
    )


# ───── 清單與彙整（/api/admin/security/*）─────

EVENT_LIST_MAX = 200
SUSPICIOUS_IP_MAX = 100


def valid_uuid(value: Any) -> bool:
    try:
        uuid.UUID(str(value))
        return True
    except (ValueError, TypeError, AttributeError):
        return False


@dataclass(frozen=True)
class AuthEventRow:
    id: int
    occurred_at: datetime | None
    event: str
    reason: str | None
    user_id: str | None
    username: str | None  # 讀取時 join 的現值；帳號已刪除為 None
    session_id: str | None
    ip: str | None
    user_agent: str | None
    count: int


async def list_auth_events(*, event: str | None = None, user_id: str | None = None, ip: str | None = None,
                           since: datetime | None = None, until: datetime | None = None,
                           before_id: int | None = None, limit: int = 100,
                           session_factory=SessionFactory) -> list[AuthEventRow]:
    """事件清單，新的在前（keyset：`before_id`）。user_id 不是合法 UUID 時回空清單。"""
    where, params = [], {"limit": max(1, min(int(limit), EVENT_LIST_MAX))}
    if event is not None:
        where.append("e.event = :event")
        params["event"] = event
    if user_id is not None:
        if not valid_uuid(user_id):
            return []
        where.append("e.user_id = CAST(:uid AS uuid)")
        params["uid"] = str(user_id)
    if ip is not None:
        where.append("e.ip = :ip")
        params["ip"] = ip
    if since is not None:
        where.append("e.occurred_at >= :since")
        params["since"] = since
    if until is not None:
        where.append("e.occurred_at < :until")
        params["until"] = until
    if before_id is not None:
        where.append("e.id < :before")
        params["before"] = int(before_id)
    sql = (
        "SELECT e.id, e.occurred_at, e.event, e.reason, e.user_id, "
        "CASE WHEN u.deleted_at IS NULL THEN u.username END, e.session_id, e.ip, e.user_agent, e.count "
        "FROM research.auth_event e LEFT JOIN research.app_user u ON u.id = e.user_id "
        + ("WHERE " + " AND ".join(where) + " " if where else "")
        + "ORDER BY e.id DESC LIMIT :limit"
    )
    async with session_factory() as session:
        rows = (await session.execute(text(sql), params)).all()
    return [
        AuthEventRow(id=int(r[0]), occurred_at=r[1], event=r[2], reason=r[3],
                     user_id=str(r[4]) if r[4] else None, username=r[5], session_id=str(r[6]) if r[6] else None,
                     ip=r[7], user_agent=r[8], count=int(r[9]))
        for r in rows
    ]


@dataclass(frozen=True)
class SuspiciousIp:
    ip: str
    failures: int  # 密碼錯、帳號不存在、停用、TOTP 錯
    locked: int  # 被每 IP 限流擋下的請求（彙總）
    insecure: int  # 非 HTTPS 遭拒（彙總）
    successes: int
    distinct_users: int  # 失敗事件裡出現過幾個不同的帳號（帳號不存在的不算）
    first_seen: datetime | None
    last_seen: datetime | None


async def suspicious_ips(*, hours: int = 24, min_failures: int = 5, limit: int = SUSPICIOUS_IP_MAX,
                         session_factory=SessionFactory) -> list[SuspiciousIp]:
    """視窗內失敗（含被限流）次數達 `min_failures` 的 IP，多的在前。只彙整、不封鎖（定案 8：交給 Cloudflare WAF）。"""
    params = {"hours": max(1, int(hours)), "min": max(1, int(min_failures)),
              "limit": max(1, min(int(limit), SUSPICIOUS_IP_MAX))}
    sql = text(
        "SELECT * FROM (SELECT ip, "
        "  COALESCE(sum(count) FILTER (WHERE event IN ('login.failure', 'login.totp_failure')), 0) AS failures, "
        "  COALESCE(sum(count) FILTER (WHERE event = 'login.locked'), 0) AS locked, "
        "  COALESCE(sum(count) FILTER (WHERE event = 'login.insecure'), 0) AS insecure, "
        "  COALESCE(sum(count) FILTER (WHERE event = 'login.success'), 0) AS successes, "
        "  count(DISTINCT user_id) FILTER (WHERE event IN ('login.failure', 'login.totp_failure')) AS users, "
        "  min(occurred_at) AS first_seen, max(occurred_at) AS last_seen "
        "FROM research.auth_event "
        "WHERE ip IS NOT NULL AND occurred_at > now() - make_interval(hours => :hours) "
        "GROUP BY ip) agg "
        "WHERE failures + locked >= :min ORDER BY failures + locked DESC, ip LIMIT :limit"
    )
    async with session_factory() as session:
        rows = (await session.execute(sql, params)).all()
    return [SuspiciousIp(ip=r[0], failures=int(r[1]), locked=int(r[2]), insecure=int(r[3]), successes=int(r[4]),
                         distinct_users=int(r[5]), first_seen=r[6], last_seen=r[7]) for r in rows]


# 高風險操作的分類（設計 §1.2）。稽核 action 名稱是穩定介面；日後的審批流程（approval workflow，預留未做）
# 也以這份清單為掛點。結尾 `.*` 是前綴比對（ops.restart、ops.run…）。一次性事件只放時間線、不送告警。
HIGH_RISK_ACTIONS: dict[str, tuple[str, ...]] = {
    "privilege": ("user.set_privileges", "user.set_role"),
    "elevation": ("session.elevate", "session.elevate_failed"),
    "session": ("session.admin_revoke", "user.force_logout", "user.disable", "user.bulk_action"),
    "credential": ("user.reset_password", "user.totp_reset", "user.totp_disable"),
    "deletion": ("user.delete_requested", "user.delete_cancelled", "user.delete_executed", "user.delete_replayed"),
    "ops": ("ops.*",),
    "data_access": ("qa_content.read", "data.export"),
    "config": ("flag.update", "quota.update"),
}
HIGH_RISK_CATEGORIES = tuple(HIGH_RISK_ACTIONS)
TIMELINE_MAX = 200


def classify_action(action: str) -> str | None:
    for category, patterns in HIGH_RISK_ACTIONS.items():
        for p in patterns:
            if (p.endswith(".*") and action.startswith(p[:-1])) or action == p:
                return category
    return None


def _action_filter(categories: Iterable[str]) -> tuple[list[str], list[str]]:
    exact, likes = [], []
    for c in categories:
        for p in HIGH_RISK_ACTIONS[c]:
            if p.endswith(".*"):
                likes.append(re.sub(r"([%_\\])", r"\\\1", p[:-1]) + "%")
            else:
                exact.append(p)
    return exact, likes


@dataclass(frozen=True)
class TimelineEntry:
    id: int
    category: str
    action: str
    actor_user_id: str | None
    actor_username: str | None
    target_type: str
    target_id: str | None
    detail: dict
    created_at: datetime | None


async def high_risk_timeline(*, days: int = 30, category: str | None = None, before_id: int | None = None,
                             limit: int = 100, session_factory=SessionFactory) -> list[TimelineEntry]:
    """稽核紀錄中屬於高風險分類的列，新的在前（keyset：`before_id`）。未知分類拋 ValueError。"""
    if category is not None and category not in HIGH_RISK_ACTIONS:
        raise ValueError(f"未知的高風險分類：{category!r}")
    exact, likes = _action_filter([category] if category else HIGH_RISK_CATEGORIES)
    params: dict = {"days": max(1, int(days)), "exact": exact, "likes": likes,
                    "limit": max(1, min(int(limit), TIMELINE_MAX))}
    where = ("a.created_at > now() - make_interval(days => :days) "
             "AND (a.action = ANY(CAST(:exact AS text[])) OR a.action LIKE ANY(CAST(:likes AS text[])))")
    if before_id is not None:
        where += " AND a.id < :before"
        params["before"] = int(before_id)
    sql = text(
        "SELECT a.id, a.action, a.actor_user_id, u.username, a.target_type, a.target_id, a.detail, a.created_at "
        "FROM research.admin_audit_log a LEFT JOIN research.app_user u ON u.id = a.actor_user_id "
        f"WHERE {where} ORDER BY a.id DESC LIMIT :limit"
    )
    async with session_factory() as session:
        rows = (await session.execute(sql, params)).all()
    out = []
    for r in rows:
        cat = classify_action(r[1])
        if cat is None:  # LIKE 與 Python 的比對理論上一致；不一致時寧可不列也不錯分
            continue
        out.append(TimelineEntry(id=int(r[0]), category=cat, action=r[1], actor_user_id=str(r[2]) if r[2] else None,
                                 actor_username=r[3], target_type=r[4], target_id=r[5],
                                 detail=r[6] if isinstance(r[6], dict) else {}, created_at=r[7]))
    return out


@dataclass(frozen=True)
class TotpAdoption:
    users_total: int
    users_enabled: int
    admins_total: int
    admins_enabled: int
    admins_without_totp: tuple[tuple[str, str, bool], ...]  # (id, username, is_super)


async def totp_adoption(*, session_factory=SessionFactory) -> TotpAdoption:
    """啟用中、未刪除帳號的 TOTP 採用狀況。只顯示、不強制（定案 10 更新：依個人設定）。"""
    async with session_factory() as session:
        counts = (await session.execute(text(
            "SELECT count(*), count(*) FILTER (WHERE totp_enabled), "
            "count(*) FILTER (WHERE role = 'admin'), count(*) FILTER (WHERE role = 'admin' AND totp_enabled) "
            "FROM research.app_user WHERE enabled AND deleted_at IS NULL"
        ))).one()
        missing = (await session.execute(text(
            "SELECT id, username, is_super FROM research.app_user "
            "WHERE enabled AND deleted_at IS NULL AND role = 'admin' AND NOT totp_enabled "
            "ORDER BY lower(username)"
        ))).all()
    return TotpAdoption(users_total=int(counts[0]), users_enabled=int(counts[1]), admins_total=int(counts[2]),
                        admins_enabled=int(counts[3]),
                        admins_without_totp=tuple((str(r[0]), r[1], bool(r[2])) for r in missing))


async def usernames(user_ids: Iterable[str], *, session_factory=SessionFactory) -> dict[str, str]:
    """user_id → 帳號名稱（未刪除的）。給告警明細把「哪個帳號連續失敗」顯示成名稱。"""
    ids = sorted({str(u) for u in user_ids if valid_uuid(u)})
    if not ids:
        return {}
    async with session_factory() as session:
        rows = (await session.execute(
            text("SELECT id, username FROM research.app_user WHERE id = ANY(CAST(:ids AS uuid[])) "
                 "AND deleted_at IS NULL"),
            {"ids": ids},
        )).all()
    return {str(r[0]): r[1] for r in rows}


# ───── 保留期清除（scripts/security_retention.py，每日）─────

PURGE_BATCH = 5000


@dataclass(frozen=True)
class PurgeResult:
    auth_event_days: int
    session_days: int
    auth_events: int
    sessions: int


async def purge_expired(*, settings=None, session_factory=SessionFactory, batch: int = PURGE_BATCH) -> PurgeResult:
    """刪除超過保留期的 auth_event（至少 365 天，定案 7）與結束超過 `SESSION_EXPIRED_RETENTION_DAYS` 的 session。

    auth_event 的保留天數在這裡**再夾一次下限 365**（設定層已夾，縱深防禦：日後有人改設定解析也刪不到一年內的列）。
    「結束」＝撤銷時刻或絕對到期時刻；仍有效的 session 永遠不刪。分批刪（每批 `batch` 列、各自 commit），
    被攻擊後累積的大量事件不會變成一筆長交易。
    """
    settings = settings or get_settings()
    auth_days = max(365, int(settings.auth_event_retention_days))
    session_days = max(1, int(settings.session_expired_retention_days))
    batch = max(1, int(batch))
    auth_n = await _purge_loop(
        session_factory,
        "DELETE FROM research.auth_event WHERE id IN (SELECT id FROM research.auth_event "
        "WHERE occurred_at < now() - make_interval(days => :days) ORDER BY id LIMIT :batch)",
        {"days": auth_days, "batch": batch},
    )
    sess_n = await _purge_loop(
        session_factory,
        "DELETE FROM research.user_session WHERE id IN (SELECT id FROM research.user_session "
        "WHERE expires_at < now() - make_interval(days => :days) "
        "OR (revoked_at IS NOT NULL AND revoked_at < now() - make_interval(days => :days)) LIMIT :batch)",
        {"days": session_days, "batch": batch},
    )
    return PurgeResult(auth_event_days=auth_days, session_days=session_days, auth_events=auth_n, sessions=sess_n)


async def _purge_loop(session_factory, sql: str, params: dict) -> int:
    total = 0
    while True:
        async with session_factory() as session:
            n = int(getattr(await session.execute(text(sql), params), "rowcount", 0) or 0)
            await session.commit()
        total += n
        if n < params["batch"]:
            return total


def reset() -> None:
    """清空模組級狀態（兩個 tally、稽核鏈快取）。tests/conftest.py 每題前後呼叫。"""
    global _audit_cache
    for tally in _TALLIES:
        tally.reset()
    _audit_cache = None

