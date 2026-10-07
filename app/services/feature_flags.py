"""功能旗標（Admin v2）：程式內 registry × 環境變數上限 × DB 覆寫（`research.feature_flag`）。

**語意（使用者定案 11）：實際值＝環境變數上限 AND DB 政策。**

- 上限（ceiling）是環境變數（經 `app/config.py`）：代表「能力已安裝／允許」。上限關閉時，DB 怎麼設都是關。
- DB 只存覆寫值；沒有那一列＝registry 預設。registry 沒登記的 key 一律忽略（不會因為 DB 多一列就長出新旗標）。
- 覆寫的作用域只有三種：`allow_roles` 與 `allow_users` 都是 NULL＝全站；任一非 NULL＝只對列出的角色或使用者
  生效（`enabled` 仍須為 true）。沒有身分（背景工作、批次）時，有作用域的覆寫一律視為關。
- **DB 讀取失敗退回 registry 預設**（上限照樣 AND；`ask.web_search` 與 `quota.enforce` 的預設是關）。
  所以這裡**刻意不放任何安全閘門**：DB 遺失時旗標會退回預設，安全邊界不能因此被放寬。管理員 TOTP 強制是
  環境變數 `ADMIN_MFA_REQUIRED`（`web/authz.py`），
  `DEV_NO_AUTH`、`REPORT_MARK_*`、CSRF、模型與儲存設定、併發閘、上傳的安全上限也都不在這裡（設計 §1.4）。
- 批次腳本不讀旗標（設計 §1.4）：旗標只在 web 的請求路徑上判斷，`scripts/judge_agreement.py`、`eval/` 照舊只看
  環境變數，所以線上降級不會靜默改掉評測的設定。

**快取（定案 12）**：DB 覆寫整張讀進來快取 `FEATURE_FLAG_CACHE_SECONDS`（5）秒；同一行程的寫入（`set_override`／
`clear_override`／`apply_import` 的呼叫端 commit 之後）呼叫 `invalidate()` 立即生效（lifespan 保證單一 worker，
所以「同一行程」＝全站）。讀取失敗的結論也快取同樣久，DB 掛掉時不會每個請求都去撞。這不違反「auth 不快取」：
旗標本來就排除所有安全邊界。

**呼叫點只知道帳號 UUID 時**（問答服務層只拿得到 `user_id`、`trusted_market_data` 讀 `request_context`）：
`allow_users` 直接比 UUID；遇到 `allow_roles` 才查一次 `app_user.role`（同樣快取 5 秒、寫入時一起失效）。查不到
角色＝角色作用域不成立（等同沒有身分）。

讀取介面：

    await feature_flags.is_enabled("qa.agentic", user)        # 上限 AND 政策；user 可以是 User、UUID 字串或省略
    await feature_flags.policy("qa.agentic", user_id)         # 只有政策（不含上限）：上限由呼叫端的模組常數提供
    await feature_flags.snapshot(user)                        # {key: FlagState}，給 GET /api/features
    feature_flags.invalidate()                                # 寫入後立即失效
    feature_flags.evaluate(spec, ceiling=..., override=..., user=...)   # 純函式（真值表測試）

`policy()` 存在的理由：`answer.py` 的 `ASK_ENABLE_WEB`、`ASK_FAITHFULNESS_ENABLED`、`ASK_RERANK_TOP_M`，
`trusted_market_data.TRUSTED_DATA_ENABLED` 是 import 期由同一個環境變數算出的模組常數，也是既有測試的替換點。
呼叫點寫成「模組常數 AND `policy()`」＝這裡的「上限 AND 政策」，既有替換點照舊有效（`tests/test_feature_flags.py`
釘住模組常數與 registry 的上限出自同一個 Settings 欄位）。

寫入介面（呼叫端給 session、負責 commit 之後呼叫 `invalidate()`；路由在 `web/routers/admin_flags.py`，
需要 `ops.operate`＋已提升）：`set_override`、`clear_override`、`plan_import`／`apply_import`、`export_document`、
`admin_view`。每個實際變更都在**同一筆交易**寫稽核 `flag.update`（detail：key、前後值、作用域摘要、
經由 api 或 import；使用者以帳號名稱表示；註記只記「有沒有改」不記全文，比照 AGENTS 的管理端點規則）。
registry 的鍵與預設值改了要同步前端（`frontend/src/lib/useFeatures.ts` 讀的鍵）與 `docs/production_resilience.md`
「功能開關與 staging 啟用矩陣」。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text

from app.config import Settings, get_settings
from app.services.db import SessionFactory

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FlagSpec:
    key: str
    description: str
    default: bool  # DB 沒有覆寫（或讀不到 DB）時的政策值；最後仍與上限 AND
    ceiling_env: str  # 上限來自哪個環境變數（顯示與文件用）
    ceiling: Callable[[Settings], bool]


@dataclass(frozen=True)
class Override:
    """`research.feature_flag` 的一列。作用域 None＝不限。"""

    enabled: bool
    allow_roles: frozenset[str] | None = None
    allow_users: frozenset[str] | None = None


@dataclass(frozen=True)
class FlagState:
    key: str
    effective: bool
    ceiling: bool
    default: bool
    override: Override | None
    source: str  # default（沒有覆寫）／db（套用了覆寫）／fallback（DB 讀取失敗，用 registry 預設）


def _spec(key, description, default, env, ceiling) -> tuple[str, FlagSpec]:
    return key, FlagSpec(key, description, default, env, ceiling)


# 旗標清單（唯一定義）。預設值的原則：fail-open 的派生功能預設 true（不設 DB 時行為與 v1 完全相同，
# 由環境變數決定）；會改變使用者可見行為的營運開關預設 false。
# ask.web_search 例外、預設 false：v1 的前端把網搜寫死成暫停（不論 ASK_ENABLE_WEB 都看不到開關、請求一律
# web=false），而網搜後端（claude CLI）已不存在。預設關才是「零行為改變」——即使環境變數未設（＝1），
# 也要上限開 AND DB 明確覆寫開，網搜開關才會出現。
REGISTRY: dict[str, FlagSpec] = dict([
    _spec("ask.web_search", "問答網路搜尋（前後端同一個來源；預設關，網搜後端完成後再以覆寫開啟）", False,
          "ASK_ENABLE_WEB", lambda s: s.ask_enable_web),
    _spec("uploads.intake", "研報上傳收檔（環境變數＝ClamAV 與 worker 已安裝；旗標＝可以暫停收檔）", True,
          "UPLOAD_ENABLED", lambda s: s.upload_enabled),
    _spec("qa.agentic", "問答 agentic 補查（fail-open 的降級開關）", True,
          "QA_AGENTIC_ENABLED", lambda s: s.qa_agentic_enabled),
    _spec("qa.faithfulness", "問答忠實度抽查（fail-open 的降級開關）", True,
          "ASK_FAITHFULNESS_ENABLED", lambda s: s.ask_faithfulness_enabled),
    _spec("ask.rerank", "問答 rerank（fail-open 的降級開關）", True,
          "ASK_RERANK_ENABLED", lambda s: s.ask_rerank_enabled),
    _spec("trusted_data", "可信市場資料（fail-open 的降級開關）", True,
          "TRUSTED_DATA_ENABLED", lambda s: s.trusted_data_enabled),
    _spec("quota.enforce", "每人每日配額正式阻擋（關＝影子模式：只計數、記錄本來會擋）", False,
          "QUOTA_ENFORCE", lambda s: s.quota_enforce),
])

# registry 的指紋：鍵、預設值、上限來源任何一項改了就變。匯出檔帶著它，匯入時不一致只提示（未知 key 另外拒絕）。
REGISTRY_VERSION = hashlib.sha256(
    json.dumps([[k, s.default, s.ceiling_env] for k, s in sorted(REGISTRY.items())]).encode()
).hexdigest()[:12]

ROLES: frozenset[str] = frozenset({"admin", "user"})  # 與 research.feature_flag.allow_roles 的 CHECK 相同
MAX_ALLOW_USERS = 200  # 與 CHECK (cardinality(allow_users) <= 200) 相同
MAX_NOTE_CHARS = 500  # 與 CHECK (char_length(note) <= 500) 相同
EXPORT_FORMAT = "report-mark/feature-flags"
EXPORT_FORMAT_VERSION = 1


def evaluate(spec: FlagSpec, *, ceiling: bool, override: Override | None, user=None) -> bool:
    """實際值＝上限 AND 政策。政策：沒有覆寫＝registry 預設；覆寫關＝關；覆寫開且無作用域＝開；有作用域＝看身分。"""
    if not ceiling:
        return False
    if override is None:
        return spec.default
    if not override.enabled:
        return False
    if override.allow_roles is None and override.allow_users is None:
        return True
    if user is None:
        return False
    if override.allow_roles is not None and getattr(user, "role", None) in override.allow_roles:
        return True
    uid = getattr(user, "id", None)
    return override.allow_users is not None and uid is not None and str(uid).lower() in override.allow_users


# ── DB 覆寫的快取 ─────────────────────────────────────────────────────────────

_cache: tuple[float, dict[str, Override], bool] | None = None  # (讀取時刻 monotonic, 覆寫, 是否讀取成功)
_roles: dict[str, tuple[float, str | None]] = {}  # 只知道 UUID 的呼叫點：uid → (讀取時刻, 角色)
_LOAD_SQL = "SELECT key, enabled, allow_roles, allow_users FROM research.feature_flag"
_ROLE_SQL = "SELECT role FROM research.app_user WHERE id = CAST(:uid AS uuid) AND deleted_at IS NULL"


def invalidate() -> None:
    """寫入之後呼叫：下一次讀取一定重新查 DB。tests/conftest.py 也每題前後呼叫。"""
    global _cache
    _cache = None
    _roles.clear()


def _parse_rows(rows) -> dict[str, Override]:
    out: dict[str, Override] = {}
    for key, enabled, roles, users in rows:
        if key not in REGISTRY:
            continue  # registry 沒登記的 key 一律忽略
        out[key] = Override(
            enabled=bool(enabled),
            allow_roles=frozenset(str(r) for r in roles) if roles is not None else None,
            allow_users=frozenset(str(u).lower() for u in users) if users is not None else None,
        )
    return out


async def _load(session_factory) -> tuple[dict[str, Override], bool]:
    try:
        async with session_factory() as session:
            rows = (await session.execute(text(_LOAD_SQL))).all()
        return _parse_rows(rows), True
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.warning("功能旗標：讀取 research.feature_flag 失敗，暫用 registry 預設", exc_info=True)
        return {}, False


async def overrides(*, session_factory=None) -> tuple[dict[str, Override], bool]:
    """目前的 DB 覆寫（快取 `FEATURE_FLAG_CACHE_SECONDS` 秒）與是否讀取成功。"""
    global _cache
    ttl = get_settings().feature_flag_cache_seconds
    cached = _cache
    if cached is not None and time.monotonic() - cached[0] < ttl:
        return cached[1], cached[2]
    # 刻意不加鎖：快取過期的那一瞬間最多多查幾次一個極小的表；asyncio.Lock 綁 event loop，
    # 模組級的鎖在多個 loop（測試、腳本）之間反而會出錯。
    data, ok = await _load(session_factory or SessionFactory)
    _cache = (time.monotonic(), data, ok)
    return data, ok


@dataclass(frozen=True)
class _Ident:
    """只知道 UUID 的身分（角色視需要才查）。"""

    id: str | None
    role: str | None = None


async def _role_of(uid: str, session_factory) -> str | None:
    ttl = get_settings().feature_flag_cache_seconds
    hit = _roles.get(uid)
    if hit is not None and time.monotonic() - hit[0] < ttl:
        return hit[1]
    role: str | None = None
    try:
        async with session_factory() as session:
            role = (await session.execute(text(_ROLE_SQL), {"uid": uid})).scalar_one_or_none()
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.warning("功能旗標：查詢帳號角色失敗，角色作用域視為不成立", exc_info=True)
    _roles[uid] = (time.monotonic(), role)
    return role


async def _identity(user, override: Override | None, session_factory):
    """把呼叫端給的身分（User、UUID 字串或 None）整理成 evaluate 用的物件；只有需要時才查角色。"""
    if user is None or not isinstance(user, str):
        return user
    uid = user.strip().lower()
    if not _valid_uuid(uid):
        return None
    ident = _Ident(id=uid)
    if (override is not None and override.enabled and override.allow_roles is not None
            and not (override.allow_users is not None and uid in override.allow_users)):
        ident = _Ident(id=uid, role=await _role_of(uid, session_factory or SessionFactory))
    return ident


async def snapshot(user=None, *, session_factory=None) -> dict[str, FlagState]:
    """所有登記旗標對這個身分的有效值與來源。"""
    data, ok = await overrides(session_factory=session_factory)
    settings = get_settings()
    out: dict[str, FlagState] = {}
    for key, spec in REGISTRY.items():
        ceiling = bool(spec.ceiling(settings))
        ov = data.get(key) if ok else None
        ident = await _identity(user, ov, session_factory) if ceiling else user
        out[key] = FlagState(
            key=key, effective=evaluate(spec, ceiling=ceiling, override=ov, user=ident), ceiling=ceiling,
            default=spec.default, override=ov, source="fallback" if not ok else ("db" if ov is not None else "default"),
        )
    return out


async def is_enabled(key: str, user=None, *, session_factory=None) -> bool:
    """單一旗標的有效值（上限 AND 政策）。未登記的 key 拋 KeyError（程式錯誤，不是「關」）。"""
    spec = REGISTRY[key]
    if not spec.ceiling(get_settings()):
        return False
    return await policy(key, user, session_factory=session_factory)


async def policy(key: str, user=None, *, session_factory=None) -> bool:
    """單一旗標的 DB 政策（**不含**環境變數上限）：上限由呼叫端以自己的模組常數 AND 上去（理由見模組 docstring）。

    沒有覆寫或 DB 讀取失敗＝registry 預設。未登記的 key 拋 KeyError。
    """
    spec = REGISTRY[key]
    data, ok = await overrides(session_factory=session_factory)
    ov = data.get(key) if ok else None
    return evaluate(spec, ceiling=True, override=ov, user=await _identity(user, ov, session_factory))


# ── 寫入、稽核、匯出匯入 ──────────────────────────────────────────────────────


class FlagError(Exception):
    code = "invalid_input"


class UnknownFlagError(FlagError):
    code = "flag_not_found"


class InvalidFlagInputError(FlagError):
    code = "invalid_input"


@dataclass(frozen=True)
class StoredOverride:
    """DB 裡的一列（管理頁看的完整形狀）。allow_users 是排序過的小寫 UUID。"""

    key: str
    enabled: bool
    allow_roles: tuple[str, ...] | None
    allow_users: tuple[str, ...] | None
    note: str | None = None
    updated_by: str | None = None
    updated_at: datetime | None = None

    def same_policy(self, other: StoredOverride | None) -> bool:
        return other is not None and (self.enabled, self.allow_roles, self.allow_users, self.note) == (
            other.enabled, other.allow_roles, other.allow_users, other.note)

    def as_override(self) -> Override:
        return Override(
            enabled=self.enabled,
            allow_roles=frozenset(self.allow_roles) if self.allow_roles is not None else None,
            allow_users=frozenset(self.allow_users) if self.allow_users is not None else None,
        )


@dataclass(frozen=True)
class AdminFlag:
    spec: FlagSpec
    ceiling: bool
    override: StoredOverride | None
    effective: str  # on（對所有人開）／off（對所有人關）／scoped（只對作用域內的角色或使用者開）
    effective_for_me: bool


@dataclass(frozen=True)
class AdminView:
    flags: list[AdminFlag]
    usernames: dict[str, str]  # uid → 帳號名稱（allow_users 與 updated_by 顯示用）
    ignored_keys: list[str]  # DB 裡有、registry 沒登記（被忽略）的 key


@dataclass(frozen=True)
class ImportChange:
    key: str
    action: str  # create／update／delete／unchanged
    before: StoredOverride | None
    after: StoredOverride | None


@dataclass(frozen=True)
class ImportProblem:
    key: str | None
    code: str
    detail: str


@dataclass(frozen=True)
class ImportPlan:
    changes: list[ImportChange]
    errors: list[ImportProblem]
    document_registry_version: str | None
    usernames: dict[str, str]

    @property
    def registry_version_match(self) -> bool:
        return self.document_registry_version == REGISTRY_VERSION


_ROWS_SQL = (
    "SELECT key, enabled, allow_roles, allow_users, note, updated_by, updated_at "
    "FROM research.feature_flag ORDER BY key"
)
_UPSERT_SQL = (
    "INSERT INTO research.feature_flag (key, enabled, allow_roles, allow_users, note, updated_by, updated_at) "
    "VALUES (:key, :enabled, CAST(:roles AS text[]), CAST(:users AS uuid[]), :note, CAST(:actor AS uuid), now()) "
    "ON CONFLICT (key) DO UPDATE SET enabled = EXCLUDED.enabled, allow_roles = EXCLUDED.allow_roles, "
    "allow_users = EXCLUDED.allow_users, note = EXCLUDED.note, updated_by = EXCLUDED.updated_by, updated_at = now()"
)
# 所有旗標寫入排成一列：讀「前值」→ 寫 → 稽核之間不會被另一個寫入插隊（稽核的前後值才對得上）。
# 固定常數（"flagwrit" 的 ASCII），全庫唯一即可（同 upload_intake 的 INTAKE_LOCK_KEY 做法）。
WRITE_LOCK_KEY = 0x666C616777726974


def _valid_uuid(value) -> bool:
    try:
        uuid.UUID(str(value))
        return True
    except (ValueError, TypeError, AttributeError):
        return False


def _stored_from_row(row) -> StoredOverride:
    key, enabled, roles, users, note, updated_by, updated_at = row
    return StoredOverride(
        key=key, enabled=bool(enabled),
        allow_roles=tuple(sorted(str(r) for r in roles)) if roles is not None else None,
        allow_users=tuple(sorted(str(u).lower() for u in users)) if users is not None else None,
        note=note, updated_by=str(updated_by).lower() if updated_by is not None else None, updated_at=updated_at,
    )


async def _rows(session) -> dict[str, StoredOverride]:
    return {r[0]: _stored_from_row(r) for r in (await session.execute(text(_ROWS_SQL))).all()}


async def _usernames(session, ids: Iterable[str]) -> dict[str, str]:
    wanted = sorted({i for i in ids if i and _valid_uuid(i)})
    if not wanted:
        return {}
    rows = (await session.execute(
        text("SELECT id::text, username FROM research.app_user WHERE id = ANY(CAST(:ids AS uuid[]))"),
        {"ids": wanted},
    )).all()
    return {str(i).lower(): name for i, name in rows}


async def _live_user_ids(session, ids: list[str]) -> set[str]:
    if not ids:
        return set()
    rows = (await session.execute(
        text("SELECT id::text FROM research.app_user WHERE id = ANY(CAST(:ids AS uuid[])) AND deleted_at IS NULL"),
        {"ids": ids},
    )).all()
    return {str(r[0]).lower() for r in rows}


def _scope_summary(o: StoredOverride | None) -> str:
    if o is None:
        return "沒有覆寫（registry 預設）"
    if not o.enabled:
        return "關閉"
    if o.allow_roles is None and o.allow_users is None:
        return "全站開啟"
    parts = []
    if o.allow_roles is not None:
        parts.append("角色 " + "、".join(o.allow_roles))
    if o.allow_users is not None:
        parts.append(f"指定使用者 {len(o.allow_users)} 位")
    return "限定開啟：" + "；".join(parts)


def _audit_value(o: StoredOverride | None, names: dict[str, str]) -> dict | None:
    if o is None:
        return None
    return {
        "enabled": o.enabled,
        "allow_roles": list(o.allow_roles) if o.allow_roles is not None else None,
        # 使用者以帳號名稱表示（稽核要看得懂）；查不到名稱的（已刪帳）只留 UUID。
        "allow_users": [names.get(u, u) for u in o.allow_users] if o.allow_users is not None else None,
    }


async def _audit(session, *, actor_id: str | None, key: str, before: StoredOverride | None,
                 after: StoredOverride | None, via: str) -> None:
    from app.services.accounts import record_audit

    ids = [*(before.allow_users or () if before else ()), *(after.allow_users or () if after else ())]
    names = await _usernames(session, ids)
    detail = {
        "key": key,
        "via": via,
        "before": _audit_value(before, names),
        "after": _audit_value(after, names),
        "scope": _scope_summary(after),
        # 註記只記「有沒有改」：註記是管理員寫的自由文字，全文不進稽核（AGENTS 管理端點規則）。
        "note_changed": (before.note if before else None) != (after.note if after else None),
    }
    await record_audit(session, actor_id=actor_id, action="flag.update", target_type="feature_flag",
                       target_id=key, detail=detail)


def _normalize(key: str, *, enabled: bool, allow_roles, allow_users, note) -> StoredOverride:
    """驗證並正規化一筆覆寫（去重、排序、小寫 UUID、空清單＝不限該維度）。不查 DB。"""
    if key not in REGISTRY:
        raise UnknownFlagError(f"沒有這個旗標：{key}")
    roles: tuple[str, ...] | None = None
    users: tuple[str, ...] | None = None
    scoped = allow_roles is not None or allow_users is not None
    if allow_roles is not None:
        bad = sorted({str(r) for r in allow_roles} - ROLES)
        if bad:
            raise InvalidFlagInputError(f"不認得的角色：{'、'.join(bad)}（只能是 admin、user）")
        roles = tuple(sorted({str(r) for r in allow_roles})) or None
    if allow_users is not None:
        bad = [str(u) for u in allow_users if not _valid_uuid(u)]
        if bad:
            raise InvalidFlagInputError("使用者必須是帳號 UUID")
        users = tuple(sorted({str(uuid.UUID(str(u))) for u in allow_users})) or None
        if users is not None and len(users) > MAX_ALLOW_USERS:
            raise InvalidFlagInputError(f"指定使用者最多 {MAX_ALLOW_USERS} 位")
    if scoped and roles is None and users is None:
        # 「作用域是空的」若默默當成不限，會把「誰都不開」變成「全站開」：直接拒絕。
        raise InvalidFlagInputError("作用域不可為空：要全站套用請不要指定角色或使用者")
    clean_note = (note or "").strip() or None
    if clean_note is not None and len(clean_note) > MAX_NOTE_CHARS:
        raise InvalidFlagInputError(f"註記最多 {MAX_NOTE_CHARS} 字")
    return StoredOverride(key=key, enabled=bool(enabled), allow_roles=roles, allow_users=users, note=clean_note)


async def _check_users_exist(session, o: StoredOverride) -> None:
    if not o.allow_users:
        return
    missing = sorted(set(o.allow_users) - await _live_user_ids(session, list(o.allow_users)))
    if missing:
        raise InvalidFlagInputError(f"有 {len(missing)} 位指定使用者不存在或已刪除")


async def _write(session, new: StoredOverride, *, actor_id: str | None) -> None:
    await session.execute(text(_UPSERT_SQL), {
        "key": new.key, "enabled": new.enabled,
        "roles": list(new.allow_roles) if new.allow_roles is not None else None,
        "users": list(new.allow_users) if new.allow_users is not None else None,
        "note": new.note, "actor": actor_id,
    })


async def _lock(session) -> None:
    await session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": WRITE_LOCK_KEY})


async def set_override(session, key: str, *, enabled: bool, allow_roles=None, allow_users=None, note=None,
                       actor_id: str | None, via: str = "api") -> tuple[StoredOverride | None, StoredOverride, bool]:
    """建立或更新一筆覆寫；有變更才寫稽核 `flag.update`。回 (前值, 後值, 是否有變更)。呼叫端 commit 後 invalidate()。"""
    new = _normalize(key, enabled=enabled, allow_roles=allow_roles, allow_users=allow_users, note=note)
    await _lock(session)
    await _check_users_exist(session, new)
    before = (await _rows(session)).get(key)
    if new.same_policy(before):
        return before, before, False
    await _write(session, new, actor_id=actor_id)
    after = (await _rows(session))[key]
    await _audit(session, actor_id=actor_id, key=key, before=before, after=after, via=via)
    return before, after, True


async def clear_override(session, key: str, *, actor_id: str | None,
                         via: str = "api") -> tuple[StoredOverride | None, bool]:
    """刪掉一筆覆寫（回到 registry 預設）；本來就沒有就什麼都不做。回 (前值, 是否有變更)。"""
    if key not in REGISTRY:
        raise UnknownFlagError(f"沒有這個旗標：{key}")
    await _lock(session)
    before = (await _rows(session)).get(key)
    if before is None:
        return None, False
    await session.execute(text("DELETE FROM research.feature_flag WHERE key = :key"), {"key": key})
    await _audit(session, actor_id=actor_id, key=key, before=before, after=None, via=via)
    return before, True


def _effective_label(spec: FlagSpec, ceiling: bool, o: StoredOverride | None) -> str:
    if not ceiling:
        return "off"
    if o is None:
        return "on" if spec.default else "off"
    if not o.enabled:
        return "off"
    if o.allow_roles is None and o.allow_users is None:
        return "on"
    return "scoped"


async def admin_view(session, me=None) -> AdminView:
    """管理頁：每個登記旗標的上限、覆寫（完整列）、對所有人的實際狀態與對我（me）的實際值。直接讀 DB，不走快取。"""
    rows = await _rows(session)
    settings = get_settings()
    ids: list[str] = []
    for o in rows.values():
        ids.extend(o.allow_users or ())
        if o.updated_by:
            ids.append(o.updated_by)
    names = await _usernames(session, ids)
    flags = []
    for key, spec in REGISTRY.items():
        ceiling = bool(spec.ceiling(settings))
        o = rows.get(key)
        flags.append(AdminFlag(
            spec=spec, ceiling=ceiling, override=o, effective=_effective_label(spec, ceiling, o),
            effective_for_me=evaluate(spec, ceiling=ceiling, override=o.as_override() if o else None, user=me),
        ))
    return AdminView(flags=flags, usernames=names, ignored_keys=sorted(k for k in rows if k not in REGISTRY))


async def export_document(session) -> dict:
    """匯出（定案 16）：registry 版本與每個登記旗標的覆寫。使用者以帳號名稱表示（UUID 在兩個環境不同）；
    已刪帳的使用者不匯出（目標環境本來就對不上）。"""
    rows = await _rows(session)
    ids = [u for o in rows.values() for u in (o.allow_users or ())]
    names = await _usernames(session, await _live_user_ids(session, ids) if ids else [])
    flags = []
    for key in REGISTRY:
        o = rows.get(key)
        flags.append({"key": key, "override": None if o is None else {
            "enabled": o.enabled,
            "allow_roles": list(o.allow_roles) if o.allow_roles is not None else None,
            "allow_users": sorted(names[u] for u in o.allow_users if u in names) if o.allow_users is not None else None,
            "note": o.note,
        }})
    return {
        "format": EXPORT_FORMAT,
        "format_version": EXPORT_FORMAT_VERSION,
        "registry_version": REGISTRY_VERSION,
        "source_environment": get_settings().ops_agent_environment or None,
        "flags": flags,
    }


def _get(obj, name, default=None):
    return obj.get(name, default) if isinstance(obj, dict) else getattr(obj, name, default)


async def plan_import(session, document) -> ImportPlan:
    """匯入的差異預覽：逐 key 比對目前的覆寫。檔案裡列出的 key＝要讓目標與它一致（override 為 null＝刪掉覆寫）；
    沒列出的 key 不動。未知 key、不認得的角色、目標環境找不到的帳號名稱一律列為錯誤（有任何錯誤就不能套用）。"""
    errors: list[ImportProblem] = []
    entries = list(_get(document, "flags") or [])
    rows = await _rows(session)
    seen: set[str] = set()
    wanted: list[tuple[str, dict | None]] = []
    for entry in entries:
        key = str(_get(entry, "key") or "")
        if key in seen:
            errors.append(ImportProblem(key, "duplicate_key", f"旗標 {key} 在檔案裡出現兩次"))
            continue
        seen.add(key)
        if key not in REGISTRY:
            errors.append(ImportProblem(key, "unknown_key", f"這個環境沒有旗標 {key}（registry 未登記），整份不套用"))
            continue
        ov = _get(entry, "override")
        wanted.append((key, ov if ov is None or isinstance(ov, dict) else ov.model_dump()))
    # 帳號名稱 → 目標環境的 UUID（不分大小寫、排除已刪帳）
    names = sorted({str(n).strip().lower() for _, ov in wanted if ov for n in (ov.get("allow_users") or [])})
    by_name: dict[str, str] = {}
    if names:
        rows_u = (await session.execute(
            text("SELECT lower(username), id::text FROM research.app_user "
                 "WHERE lower(username) = ANY(CAST(:names AS text[])) AND deleted_at IS NULL"),
            {"names": names},
        )).all()
        by_name = {n: str(i).lower() for n, i in rows_u}
    changes: list[ImportChange] = []
    for key, ov in wanted:
        before = rows.get(key)
        if ov is None:
            changes.append(ImportChange(key, "unchanged" if before is None else "delete", before, None))
            continue
        users = ov.get("allow_users")
        missing = sorted({str(n).strip().lower() for n in users or []} - by_name.keys())
        if missing:
            errors.append(ImportProblem(key, "unknown_user", f"這個環境找不到帳號：{'、'.join(missing)}"))
            continue
        try:
            after = _normalize(key, enabled=bool(ov.get("enabled")), allow_roles=ov.get("allow_roles"),
                               allow_users=[by_name[str(n).strip().lower()] for n in users]
                               if users is not None else None, note=ov.get("note"))
        except FlagError as exc:
            errors.append(ImportProblem(key, exc.code, str(exc)))
            continue
        action = "create" if before is None else ("unchanged" if after.same_policy(before) else "update")
        changes.append(ImportChange(key, action, before, after))
    ids = [u for c in changes for o in (c.before, c.after) if o for u in (o.allow_users or ())]
    return ImportPlan(changes=changes, errors=errors,
                      document_registry_version=_get(document, "registry_version"),
                      usernames=await _usernames(session, ids))


async def apply_import(session, document, *, actor_id: str | None) -> ImportPlan:
    """在呼叫端的交易裡套用匯入：先鎖、重算差異；有錯誤就什麼都不寫（回傳的 plan 帶 errors，呼叫端回 422）。
    每個實際變更各自經 set_override／clear_override 寫一筆 `flag.update`（via=import）。"""
    await _lock(session)
    plan = await plan_import(session, document)
    if plan.errors:
        return plan
    for c in plan.changes:
        if c.action == "delete":
            await clear_override(session, c.key, actor_id=actor_id, via="import")
        elif c.action in ("create", "update") and c.after is not None:
            await set_override(session, c.key, enabled=c.after.enabled, allow_roles=c.after.allow_roles,
                               allow_users=c.after.allow_users, note=c.after.note, actor_id=actor_id, via="import")
    return plan
