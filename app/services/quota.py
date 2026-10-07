"""每人每日配額（Admin v2 Quota lane）：`usage_counter` 原子計數、`user_quota` 個人覆寫、影子模式。

web 層一律經 `web.deps.quota`（本模組物件）呼叫——測試注入假物件的單一位置（`tests/conftest.py` 預設把
`charge` 換成「放行、不計數」）。本模組的 SQL 由 `tests/test_quota_db.py` 對真的 PostgreSQL 驗（含並發）。

**三類配額**（`KINDS`，與 `user_quota.kind` 的 CHECK 同一組）：

- `ask`（預設 `QUOTA_ASK_DAILY`＝100）：`usage_counter`。`web/routers/ask.py` 在 `queue_full()` 之後、開始串流
  之前 `charge`；重新生成、編輯重問都算（同一條 `/api/ask`），`/api/ask/stop` 不算。
- `export`（預設 `QUOTA_EXPORT_DAILY`＝20）：`usage_counter`。`web/routers/admin_exports.py` 每次匯出
  （`_finish`，寫稽核之前）。
- `upload`（預設 `UPLOAD_DAILY_QUOTA`＝30）：**不經這裡計數**，沿用 v1.5 的 `report_upload` 計數與 advisory
  lock（`upload_intake.check_quota`），只在那裡讀個人覆寫（`resolve_limit`）。上傳一直是正式阻擋，不受影子模式影響。

**計數是一句原子 SQL**（`_CHARGE_SQL`）：`INSERT … ON CONFLICT DO UPDATE SET count = count + 1 WHERE count < :limit
RETURNING count`。沒有回傳任何列＝超額；兩個請求同時在第 99 次時只有一個拿得到第 100 次（ON CONFLICT 在列鎖上
對最新版本重判 WHERE），不需要先讀再寫。上限 0 時 INSERT 那條路也不插入（`WHERE :limit > 0`）。日期是台北時間的
日曆日（與上傳配額、`usage_events` 一致）。配額不 COUNT `qa_log`：使用者可以硬刪自己的歷史。

**影子模式（使用者定案 5）**：正式阻擋要兩道開關**同時**開——環境變數 `QUOTA_ENFORCE`（能力上限，預設關）AND
DB 旗標 `quota.enforce`（`feature_flags.is_enabled`，registry 預設關；寫入 UI 屬 Flags lane）。旗標讀取失敗時
`feature_flags` 退回 registry 預設（關），所以 DB 出事只會退回影子模式、不會誤擋人。

- 超額時一律在 `usage_counter` 另記一筆 `<kind>_over`（`ask_over`、`export_over`）：影子模式下是「本來會擋」
  的次數（照常放行），正式阻擋時是「被擋下」的次數。選 `usage_counter` 而不是只寫 log：管理頁要能直接算
  每人每日的真實需求（`<kind>` 封頂在上限、`<kind>_over` 是超出的部分，兩者相加＝實際嘗試次數），兩週後的
  P50／P95 才算得出來；journald 的保留期與查詢都不適合當這個依據。另外印一行結構化 log `quota_over`
  （kind、user_id、上限、是否阻擋；沒有問題內容）方便即時追。
- 正式阻擋時路由回 429 `quota_exceeded`，`Retry-After`＝距台北午夜的秒數（`retry_after_seconds`）。

**DB 寫入失敗＝放行並記 WARNING**（`Decision.error`）。配額是費用控管不是安全邊界；DB 真的掛掉時 session 查驗
早就回 503。沒有登入身分（開發模式免登入的 `DEV_USER`）不計數、不阻擋。

**個人覆寫**（`user_quota`）：沒有列＝程式預設；`daily_limit` 整數＝這個人的上限（0＝完全不能用）；NULL＝不限，
**只有 super admin 能設**。對 super admin 帳號的覆寫也只有 super admin 能改（與 `accounts` 同一條規則）。
規則寫在這一層（`set_override`），CLI 也受約束；`actor_id=None`（主機上的 CLI）視為受信任。寫入與稽核
`quota.update` 同一筆交易；稽核 detail 只有類別、模式、上限與先前值，**不含理由全文與帳號名稱**。

隱私：這裡只有「人×日×類別」的次數，沒有主題（哪一篇、問了什麼）。單一使用者的數字只出現在管理後台的配額頁
（`accounts.manage`）與使用者自己的 `/api/me/quota`。
"""

from __future__ import annotations

import asyncio
import logging
import math
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import text

from app.config import get_settings
from app.services import feature_flags
from app.services.db import SessionFactory

logger = logging.getLogger(__name__)

TZ = timezone(timedelta(hours=8))
TZ_NAME = "Asia/Taipei"

KIND_ASK = "ask"
KIND_EXPORT = "export"
KIND_UPLOAD = "upload"
KINDS: tuple[str, ...] = (KIND_ASK, KIND_EXPORT, KIND_UPLOAD)
# 以 usage_counter 計數的類別（upload 沿用 report_upload）。
COUNTED_KINDS: tuple[str, ...] = (KIND_ASK, KIND_EXPORT)
OVER_SUFFIX = "_over"
FLAG_KEY = "quota.enforce"

MODE_DEFAULT = "default"
MODE_LIMIT = "limit"
MODE_UNLIMITED = "unlimited"
MODES: tuple[str, ...] = (MODE_DEFAULT, MODE_LIMIT, MODE_UNLIMITED)
MAX_DAILY_LIMIT = 100_000
REASON_MAX_CHARS = 500
STATS_DEFAULT_DAYS = 14
STATS_MAX_DAYS = 90


def over_kind(kind: str) -> str:
    return f"{kind}{OVER_SUFFIX}"


# ── 錯誤 ────────────────────────────────────────────────────────────────


class QuotaError(Exception):
    """管理配額的可預期錯誤；訊息是給管理員看的中文。"""


class InvalidQuotaInput(QuotaError):
    pass


class QuotaTargetNotFound(QuotaError):
    pass


class QuotaPermissionDenied(QuotaError):
    pass


# ── 時間 ────────────────────────────────────────────────────────────────


def taipei_now(now: datetime | None = None) -> datetime:
    return (now or datetime.now(timezone.utc)).astimezone(TZ)


def taipei_day(now: datetime | None = None) -> date:
    return taipei_now(now).date()


def retry_after_seconds(now: datetime | None = None) -> int:
    """距下一個台北午夜的秒數（無條件進位、至少 1）：429 的 `Retry-After`。"""
    local = taipei_now(now)
    midnight = datetime.combine(local.date() + timedelta(days=1), time(0), tzinfo=TZ)
    return max(1, math.ceil((midnight - local).total_seconds()))


def default_limit(kind: str, settings=None) -> int:
    s = settings or get_settings()
    if kind == KIND_ASK:
        return s.quota_ask_daily
    if kind == KIND_EXPORT:
        return s.quota_export_daily
    if kind == KIND_UPLOAD:
        return s.upload_daily_quota
    raise ValueError(f"未知的配額類別：{kind!r}")


def _valid_uuid(value) -> bool:
    try:
        uuid.UUID(str(value))
        return True
    except (ValueError, TypeError, AttributeError):
        return False


# ── 計數 ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Decision:
    """一次 `charge` 的結論。`allowed=False` 只會在超額且正式阻擋時出現。"""

    kind: str
    allowed: bool
    counted: bool = False  # 這次是否計入 <kind>（超額、沒有身分、DB 失敗都是 False）
    over: bool = False  # 這次超過上限（影子模式＝本來會擋）
    enforced: bool = False  # 超額時：正式阻擋是否生效（兩道開關）
    limit: int | None = None  # 這個人這一類的上限；None＝不限或未知
    count: int | None = None  # 計入後今天的次數
    error: bool = False  # DB 寫入失敗而放行
    retry_after: int | None = None  # 被擋時距台北午夜的秒數


_LIMIT_SQL = "SELECT daily_limit FROM research.user_quota WHERE user_id = CAST(:uid AS uuid) AND kind = :kind"

# 檢查與遞增是同一句（模組 docstring）。limit NULL＝不限。
_CHARGE_SQL = """
INSERT INTO research.usage_counter (user_id, day, kind, count)
SELECT CAST(:uid AS uuid), CAST(:day AS date), :kind, 1
WHERE CAST(:limit AS integer) IS NULL OR CAST(:limit AS integer) > 0
ON CONFLICT (user_id, day, kind) DO UPDATE
SET count = research.usage_counter.count + 1, updated_at = now()
WHERE CAST(:limit AS integer) IS NULL OR research.usage_counter.count < CAST(:limit AS integer)
RETURNING count
"""

_OVER_SQL = """
INSERT INTO research.usage_counter (user_id, day, kind, count)
VALUES (CAST(:uid AS uuid), CAST(:day AS date), :kind, 1)
ON CONFLICT (user_id, day, kind) DO UPDATE
SET count = research.usage_counter.count + 1, updated_at = now()
"""


async def resolve_limit(session, user_id: str | None, kind: str, default: int) -> int | None:
    """這個人這一類的上限：有覆寫用覆寫（NULL＝不限→回 None），沒有用 `default`。在呼叫端的交易裡查。"""
    if user_id is None or not _valid_uuid(user_id):
        return default
    row = (await session.execute(text(_LIMIT_SQL), {"uid": str(user_id), "kind": kind})).first()
    if row is None:
        return default
    return None if row[0] is None else int(row[0])


async def enforcement_active(user=None) -> bool:
    """兩道開關：`QUOTA_ENFORCE`（上限）AND 旗標 `quota.enforce`。feature_flags 已經把上限 AND 進去。"""
    return await feature_flags.is_enabled(FLAG_KEY, user)


async def charge(user, kind: str, *, session_factory=None, now: datetime | None = None) -> Decision:
    """計一次 `kind`（ask／export）並判斷是否放行。永遠不拋例外（DB 失敗放行）。"""
    if kind not in COUNTED_KINDS:
        raise ValueError(f"charge 只接受 {COUNTED_KINDS}：{kind!r}")
    uid = getattr(user, "id", None)
    if uid is None or not _valid_uuid(uid):
        return Decision(kind=kind, allowed=True)
    day = taipei_day(now)
    factory = session_factory or SessionFactory
    try:
        async with factory() as session:
            limit = await resolve_limit(session, uid, kind, default_limit(kind))
            row = (await session.execute(
                text(_CHARGE_SQL), {"uid": str(uid), "day": day, "kind": kind, "limit": limit},
            )).first()
            if row is None:
                await session.execute(text(_OVER_SQL), {"uid": str(uid), "day": day, "kind": over_kind(kind)})
            await session.commit()
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.warning("配額：計數寫入失敗，這次放行 kind=%s user_id=%s", kind, uid, exc_info=True)
        return Decision(kind=kind, allowed=True, error=True)
    if row is not None:
        return Decision(kind=kind, allowed=True, counted=True, limit=limit, count=int(row[0]))
    enforced = await enforcement_active(user)
    logger.info("quota_over kind=%s user_id=%s limit=%s enforced=%s", kind, uid, limit, enforced)
    return Decision(kind=kind, allowed=not enforced, over=True, enforced=enforced, limit=limit,
                    retry_after=retry_after_seconds(now) if enforced else None)


def exceeded_message(decision: Decision) -> str:
    label = {KIND_ASK: "問答", KIND_EXPORT: "匯出"}.get(decision.kind, decision.kind)
    return f"今天（台北時間）的{label}次數已達上限 {decision.limit} 次，午夜後重置"


# ── 狀態與用量 ──────────────────────────────────────────────────────────


async def enforcement_status() -> dict:
    """全站層級的影子模式狀態（沒有身分＝有作用域的覆寫視為關）。"""
    state = (await feature_flags.snapshot())[FLAG_KEY]
    return {
        "env_ceiling": state.ceiling,
        "flag_enabled": state.override.enabled if state.override is not None else state.default,
        "flag_scoped": state.override is not None and (
            state.override.allow_roles is not None or state.override.allow_users is not None),
        "flag_source": state.source,
        "effective": state.effective,
        "mode": "enforce" if state.effective else "shadow",
    }


def _upload_window_sql(alias: str = "") -> str:
    col = f"{alias}uploaded_at"
    return (f"{col} >= (CAST(:d0 AS date)::timestamp AT TIME ZONE '{TZ_NAME}') "
            f"AND {col} < ((CAST(:d1 AS date) + 1)::timestamp AT TIME ZONE '{TZ_NAME}')")


async def _today_counts(session, day: date, user_id: str | None = None) -> dict[tuple[str, str], int]:
    """{(user_id, kind): count}；kind 含 ask／export／upload 與 *_over。upload 來自 report_upload。"""
    kinds = list(COUNTED_KINDS) + [over_kind(k) for k in COUNTED_KINDS]
    where_user = " AND user_id = CAST(:uid AS uuid)" if user_id else ""
    rows = (await session.execute(text(
        "SELECT user_id::text, kind, count FROM research.usage_counter "
        f"WHERE day = CAST(:day AS date) AND kind = ANY(CAST(:kinds AS text[])){where_user}"
    ), {"day": day, "kinds": kinds, "uid": user_id})).all()
    out = {(r[0], r[1]): int(r[2]) for r in rows}
    up_user = " AND uploaded_by = CAST(:uid AS uuid)" if user_id else ""
    ups = (await session.execute(text(
        "SELECT uploaded_by::text, count(*) FROM research.report_upload "
        f"WHERE uploaded_by IS NOT NULL AND {_upload_window_sql()}{up_user} GROUP BY 1"
    ), {"d0": day, "d1": day, "uid": user_id})).all()
    for uid, n in ups:
        out[(uid, KIND_UPLOAD)] = int(n)
    return out


async def _overrides(session, user_id: str | None = None) -> dict[tuple[str, str], dict]:
    where = " WHERE user_id = CAST(:uid AS uuid)" if user_id else ""
    rows = (await session.execute(text(
        f"SELECT user_id::text, kind, daily_limit, reason, updated_at FROM research.user_quota{where}"
    ), {"uid": user_id})).all()
    return {(r[0], r[1]): {"daily_limit": r[2], "reason": r[3], "updated_at": r[4]} for r in rows}


def _item(kind: str, uid: str, counts: dict, overrides: dict, settings) -> dict:
    ov = overrides.get((uid, kind))
    default = default_limit(kind, settings)
    if ov is None:
        mode, limit = MODE_DEFAULT, default
    elif ov["daily_limit"] is None:
        mode, limit = MODE_UNLIMITED, None
    else:
        mode, limit = MODE_LIMIT, int(ov["daily_limit"])
    used = counts.get((uid, kind), 0)
    return {
        "kind": kind,
        "used": used,
        "over": counts.get((uid, over_kind(kind)), 0) if kind in COUNTED_KINDS else 0,
        "limit": limit,
        "default_limit": default,
        "mode": mode,
        "remaining": None if limit is None else max(0, limit - used),
        "reason": ov["reason"] if ov else None,
        "updated_at": ov["updated_at"] if ov else None,
    }


async def me_usage(user, *, session_factory=None, now: datetime | None = None) -> dict:
    """目前使用者今天各類的次數與上限。一般使用者只有 ask（匯出與上傳是管理功能）。"""
    day = taipei_day(now)
    uid = getattr(user, "id", None)
    kinds = KINDS if getattr(user, "is_admin", False) else (KIND_ASK,)
    settings = get_settings()
    counts: dict = {}
    overrides: dict = {}
    if uid is not None and _valid_uuid(uid):
        async with (session_factory or SessionFactory)() as session:
            counts = await _today_counts(session, day, str(uid))
            overrides = await _overrides(session, str(uid))
    key = str(uid) if uid is not None else ""
    items = []
    for kind in kinds:
        item = _item(kind, key, counts, overrides, settings)
        items.append({k: item[k] for k in ("kind", "used", "over", "limit", "remaining")})
    return {
        "day": day.isoformat(),
        "timezone": TZ_NAME,
        "resets_in_seconds": retry_after_seconds(now),
        "enforced": await enforcement_active(user),
        "items": items,
    }


async def admin_overview(*, session_factory=None, now: datetime | None = None) -> dict:
    """每位（未刪除）使用者今天的各類次數、上限與覆寫，以及今天的線上 LLM 呼叫與 token。"""
    day = taipei_day(now)
    settings = get_settings()
    async with (session_factory or SessionFactory)() as session:
        users = (await session.execute(text(
            "SELECT id::text, username, role, enabled, is_super FROM research.app_user "
            "WHERE deleted_at IS NULL ORDER BY lower(username)"
        ))).all()
        counts = await _today_counts(session, day)
        overrides = await _overrides(session)
        llm = (await session.execute(text(
            "SELECT user_id::text, sum(calls), sum(failures), "
            "sum(prompt_hit_tokens + prompt_miss_tokens), sum(completion_tokens) "
            "FROM research.llm_usage_daily WHERE day = CAST(:day AS date) AND user_id IS NOT NULL GROUP BY 1"
        ), {"day": day})).all()
    llm_by_user = {r[0]: {"calls": int(r[1] or 0), "failures": int(r[2] or 0), "prompt_tokens": int(r[3] or 0),
                          "completion_tokens": int(r[4] or 0)} for r in llm}
    empty_llm = {"calls": 0, "failures": 0, "prompt_tokens": 0, "completion_tokens": 0}
    rows = []
    over_today = {k: 0 for k in COUNTED_KINDS}
    for uid, username, role, enabled, is_super in users:
        items = [_item(kind, uid, counts, overrides, settings) for kind in KINDS]
        for it in items:
            if it["kind"] in over_today:
                over_today[it["kind"]] += it["over"]
        rows.append({
            "user_id": uid, "username": username, "role": role, "enabled": bool(enabled), "is_super": bool(is_super),
            "items": items, "llm": llm_by_user.get(uid, empty_llm),
        })
    return {
        "day": day.isoformat(),
        "timezone": TZ_NAME,
        "resets_in_seconds": retry_after_seconds(now),
        "defaults": {kind: default_limit(kind, settings) for kind in KINDS},
        "enforcement": await enforcement_status(),
        "over_today": over_today,
        "users": rows,
    }


_COUNTED_STATS_SQL = """
WITH d AS (
    SELECT user_id, day,
           COALESCE(sum(count) FILTER (WHERE kind = :kind), 0) AS used,
           COALESCE(sum(count) FILTER (WHERE kind = :over), 0) AS over
    FROM research.usage_counter
    WHERE day BETWEEN CAST(:d0 AS date) AND CAST(:d1 AS date) AND kind IN (:kind, :over)
    GROUP BY user_id, day
)
SELECT count(*), count(DISTINCT user_id),
       percentile_disc(0.5) WITHIN GROUP (ORDER BY used + over),
       percentile_disc(0.95) WITHIN GROUP (ORDER BY used + over),
       max(used + over), count(*) FILTER (WHERE over > 0), COALESCE(sum(over), 0)
FROM d
"""

_UPLOAD_STATS_SQL = f"""
WITH d AS (
    SELECT uploaded_by AS user_id, (uploaded_at AT TIME ZONE '{TZ_NAME}')::date AS day, count(*) AS used
    FROM research.report_upload
    WHERE uploaded_by IS NOT NULL AND {_upload_window_sql()}
    GROUP BY 1, 2
)
SELECT count(*), count(DISTINCT user_id),
       percentile_disc(0.5) WITHIN GROUP (ORDER BY used),
       percentile_disc(0.95) WITHIN GROUP (ORDER BY used),
       max(used), 0, 0
FROM d
"""


async def usage_stats(days: int = STATS_DEFAULT_DAYS, *, session_factory=None, now: datetime | None = None) -> dict:
    """最近 `days` 個台北日（含今天）每人每日需求的 P50／P95（給「兩週後是否正式阻擋」用）。

    需求＝`<kind>`＋`<kind>_over`（影子模式下超出上限的嘗試也算）；只統計**有使用的人日**（當天這一類至少一次），
    沒用的日子不算 0，否則十幾個人的小團隊 P50 永遠是 0。upload 來自 `report_upload`（被拒的上傳不留紀錄，
    所以沒有 over）。
    """
    days = max(1, min(int(days), STATS_MAX_DAYS))
    d1 = taipei_day(now)
    d0 = d1 - timedelta(days=days - 1)
    settings = get_settings()
    kinds = []
    async with (session_factory or SessionFactory)() as session:
        for kind in KINDS:
            if kind == KIND_UPLOAD:
                r = (await session.execute(text(_UPLOAD_STATS_SQL), {"d0": d0, "d1": d1})).one()
            else:
                r = (await session.execute(text(_COUNTED_STATS_SQL),
                                           {"d0": d0, "d1": d1, "kind": kind, "over": over_kind(kind)})).one()
            kinds.append({
                "kind": kind, "default_limit": default_limit(kind, settings),
                "user_days": int(r[0] or 0), "users": int(r[1] or 0),
                "p50": int(r[2]) if r[2] is not None else None, "p95": int(r[3]) if r[3] is not None else None,
                "max": int(r[4]) if r[4] is not None else None,
                "over_user_days": int(r[5] or 0), "over_events": int(r[6] or 0),
            })
    return {"since_day": d0.isoformat(), "until_day": d1.isoformat(), "days": days, "timezone": TZ_NAME,
            "kinds": kinds}


# ── 個人覆寫 ────────────────────────────────────────────────────────────


async def _actor_is_super(session, actor_id: str | None) -> bool:
    """actor_id=None（主機上的 CLI）視為受信任；否則必須是啟用中的 super admin。"""
    if actor_id is None:
        return True
    if not _valid_uuid(actor_id):
        return False
    return bool((await session.execute(
        text("SELECT 1 FROM research.app_user WHERE id = CAST(:id AS uuid) AND enabled AND role = 'admin' "
             "AND is_super AND deleted_at IS NULL"),
        {"id": actor_id},
    )).first())


def _validate(kind: str, mode: str, daily_limit, reason) -> tuple[int | None, str | None]:
    if kind not in KINDS:
        raise InvalidQuotaInput(f"配額類別只能是 {'、'.join(KINDS)}")
    if mode not in MODES:
        raise InvalidQuotaInput("模式只能是 default、limit 或 unlimited")
    limit = None
    if mode == MODE_LIMIT:
        if not isinstance(daily_limit, int) or isinstance(daily_limit, bool) or not 0 <= daily_limit <= MAX_DAILY_LIMIT:
            raise InvalidQuotaInput(f"每日上限必須是 0–{MAX_DAILY_LIMIT} 的整數")
        limit = daily_limit
    clean = (reason or "").strip() or None
    if clean is not None and len(clean) > REASON_MAX_CHARS:
        raise InvalidQuotaInput(f"理由最多 {REASON_MAX_CHARS} 字")
    if mode == MODE_DEFAULT:
        clean = None
    return limit, clean


def _mode_of(row) -> tuple[str, int | None]:
    if row is None:
        return MODE_DEFAULT, None
    return (MODE_UNLIMITED, None) if row[0] is None else (MODE_LIMIT, int(row[0]))


async def set_override(user_id: str, kind: str, *, mode: str, daily_limit: int | None = None,
                       reason: str | None = None, actor_id: str | None, session_factory=None) -> dict:
    """設定或清除（mode=default）某人某一類的覆寫，同一筆交易寫稽核 `quota.update`。回傳 `{changed, item}`。"""
    limit, clean_reason = _validate(kind, mode, daily_limit, reason)
    if not _valid_uuid(user_id):
        raise QuotaTargetNotFound("帳號不存在")
    # 函式內 import：不讓本模組在 import 期拉起帳號模組（與 upload_intake 同理）。
    from app.services.accounts import record_audit

    async with (session_factory or SessionFactory)() as session:
        target = (await session.execute(text(
            "SELECT id::text, role, enabled, is_super, deleted_at FROM research.app_user "
            "WHERE id = CAST(:id AS uuid) FOR UPDATE"
        ), {"id": user_id})).first()
        if target is None or target[4] is not None:
            raise QuotaTargetNotFound("帳號不存在")
        target_super = bool(target[3]) and target[1] == "admin" and bool(target[2])
        if (mode == MODE_UNLIMITED or target_super) and not await _actor_is_super(session, actor_id):
            raise QuotaPermissionDenied("只有 super admin 能設定「不限」" if mode == MODE_UNLIMITED
                                        else "只有 super admin 能調整 super admin 的配額")
        prev = (await session.execute(text(
            "SELECT daily_limit, reason FROM research.user_quota "
            "WHERE user_id = CAST(:uid AS uuid) AND kind = :kind FOR UPDATE"
        ), {"uid": user_id, "kind": kind})).first()
        prev_mode, prev_limit = _mode_of(prev)
        prev_reason = prev[1] if prev is not None else None
        changed = (prev_mode, prev_limit, prev_reason) != (mode, limit, clean_reason)
        if changed:
            if mode == MODE_DEFAULT:
                await session.execute(text(
                    "DELETE FROM research.user_quota WHERE user_id = CAST(:uid AS uuid) AND kind = :kind"
                ), {"uid": user_id, "kind": kind})
            else:
                await session.execute(text(
                    "INSERT INTO research.user_quota (user_id, kind, daily_limit, reason, updated_by) "
                    "VALUES (CAST(:uid AS uuid), :kind, CAST(:lim AS integer), :reason, CAST(:actor AS uuid)) "
                    "ON CONFLICT (user_id, kind) DO UPDATE SET daily_limit = EXCLUDED.daily_limit, "
                    "reason = EXCLUDED.reason, updated_by = EXCLUDED.updated_by, updated_at = now()"
                ), {"uid": user_id, "kind": kind, "lim": limit, "reason": clean_reason, "actor": actor_id})
            await record_audit(
                session, actor_id=actor_id, action="quota.update", target_type="user", target_id=user_id,
                detail={"kind": kind, "mode": mode, "daily_limit": limit, "previous_mode": prev_mode,
                        "previous_daily_limit": prev_limit, "has_reason": clean_reason is not None},
            )
            await session.commit()
        counts = await _today_counts(session, taipei_day(), user_id)
        overrides = await _overrides(session, user_id)
    return {"changed": changed, "item": _item(kind, user_id, counts, overrides, get_settings())}
