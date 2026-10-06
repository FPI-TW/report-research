"""個別帳號、可撤銷 session 與管理稽核（`research.app_user`／`user_session`／`admin_audit_log`）。

取代原本的單一共用帳密。web 層只經 `web.deps.accounts`（本模組物件）呼叫這裡——
那是測試注入假帳號庫的單一位置（`tests/fake_accounts.py`）；本模組的 SQL 本身由
`tests/test_accounts_db.py` 對真的 PostgreSQL 驗。

三個刻意的設計：

1. **每個請求都查 DB**（`resolve_session`）。停用帳號、強制登出、重設密碼都要「立即」
   生效，任何快取都會在那幾秒到幾分鐘裡留下一個還能用的 session。成本是一次主鍵查詢；
   `last_seen_at` 只在超過 `_TOUCH_INTERVAL` 時才回寫，避免每個請求都是一次寫入。
2. **帳號只停用、不刪除**。`qa_log.user_id`、`review_state.reviewer_user_id`、
   `admin_audit_log` 都指向它；刪掉的話歷史失去擁有者，稽核紀錄說不出是誰做的。
3. **稽核與變更同一筆交易**（`_audit`）。改了卻沒留紀錄、或留了紀錄卻沒改，都不會發生。

管理動作的保護在這一層、不在路由層，讓 CLI 也受同樣的規則約束：
- 最後一位啟用中的管理員不能被停用或降級（`LastAdminError`）。
- 管理員不能停用自己、也不能拿掉自己的管理員權限（`SelfLockoutError`）。
- 最後一位啟用中的 super admin 不能失去 super（`LastSuperError`）。
- 對 super admin 的任何管理動作、以及授予 scope／super，都只有 super admin 能做（`PermissionDeniedError`）。
  `actor_id=None`（CLI，`scripts/create_admin.py`）視為主機上的受信任操作者，不受這條限制。

權限模型（`effective_scopes`）：一般使用者沒有任何 scope；管理員有 `ADMIN_DEFAULT_SCOPES`，加上另外
授予的 `GRANTABLE_SCOPES`（存在 `research.user_scope`）；super admin 有全部 scope。授權判斷只在
`web/authz.py`（`require_scope`），前端顯示與否只是顯示。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.services import passwords
from app.services.db import SessionFactory

logger = logging.getLogger(__name__)

Role = Literal["admin", "user"]
ROLES: tuple[str, ...] = ("admin", "user")

# ── scope 詞彙（唯一定義處）──────────────────────────────────────────────
# `admin` 是所有管理員都有的基本 scope（只要求「是管理員」的端點用它）。
SCOPE_ADMIN = "admin"
ADMIN_DEFAULT_SCOPES: frozenset[str] = frozenset({
    SCOPE_ADMIN, "accounts.manage", "audit.read", "review.manage", "ops.read", "reports.manage",
})
# 必須另外授予的 scope。改這組要寫 revision 改 research.user_scope 的 CHECK（db/expected_constraints.txt）。
GRANTABLE_SCOPES: frozenset[str] = frozenset({"qa_content.read", "ops.operate"})
ALL_SCOPES: frozenset[str] = ADMIN_DEFAULT_SCOPES | GRANTABLE_SCOPES
# 重新驗證密碼後的權限提升視窗（user_session.elevated_until）。
ELEVATION_SECONDS = 600


def effective_scopes(role: str, is_super: bool, granted: Iterable[str] = ()) -> frozenset[str]:
    """實際生效的 scope。is_super 與授予的 scope 只在 role='admin' 時有效。"""
    if role != "admin":
        return frozenset()
    if is_super:
        return ALL_SCOPES
    return ADMIN_DEFAULT_SCOPES | (frozenset(granted) & GRANTABLE_SCOPES)

# last_seen_at 的回寫間隔：管理頁的「最後活動」不需要秒級精度，每個請求都寫太浪費。
_TOUCH_INTERVAL_SECONDS = 300
# user_agent 只供管理頁辨識裝置，截斷免得被灌入超長 header。
_MAX_USER_AGENT = 300

# 帳號名稱：2–64 個字元，只允許文字、數字與 `._@-`（\w 含中文）。不允許空白與控制字元：
# 登入表單看不出前後空白，日誌與管理頁也無從分辨。
_USERNAME_RE = re.compile(r"^[\w.@-]{2,64}$")


@dataclass(frozen=True)
class User:
    """已通過認證、目前可用的使用者（middleware 放進 `request.state.user`）。

    `id` 為 None 只有一種情況：開發模式免登入（`DEV_USER`）。那時沒有真的帳號，
    寫入的問答紀錄擁有者是 NULL，與個別帳號上線前的共用歷史同一類。
    """

    id: str | None
    username: str
    role: str
    # 以下三欄由 resolve_session 帶入；預設值讓既有的 User(id, username, role) 寫法照樣成立。
    is_super: bool = False
    scopes: frozenset[str] = frozenset()
    elevated_until: datetime | None = None

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    def has_scopes(self, needed: Iterable[str]) -> bool:
        return frozenset(needed) <= self.scopes

    @property
    def is_elevated(self) -> bool:
        return self.elevated_until is not None and self.elevated_until > datetime.now(timezone.utc)


# 開發模式免登入（本機直連、無代理）：沒有真的帳號與 session，視為已提升權限的 super admin。
DEV_USER = User(id=None, username="dev", role="admin", is_super=True, scopes=ALL_SCOPES,
                elevated_until=datetime(9999, 12, 31, tzinfo=timezone.utc))


@dataclass(frozen=True)
class LoginResult:
    """登入結果。reason 只進日誌與錯誤頁的分流，絕不帶密碼相關的任何值。"""

    user: User | None
    reason: Literal["ok", "unknown_user", "bad_password", "disabled"]


@dataclass(frozen=True)
class UserInfo:
    """管理頁看到的一列帳號（沒有 password_hash）。"""

    id: str
    username: str
    role: str
    enabled: bool
    created_at: datetime | None
    updated_at: datetime | None
    password_changed_at: datetime | None
    last_login_at: datetime | None
    last_seen_at: datetime | None
    active_sessions: int
    is_super: bool = False
    scopes: tuple[str, ...] = ()  # 另外授予的（GRANTABLE_SCOPES），不含管理員預設


@dataclass(frozen=True)
class AuditChainStatus:
    """稽核雜湊鏈的驗證結果（`verify_audit_chain`）。broken_ids 最多列 20 筆。"""

    ok: bool
    total: int
    head_id: int | None
    head_hash: str | None
    broken_ids: tuple[int, ...] = ()


@dataclass(frozen=True)
class AuditEntry:
    id: int
    actor_user_id: str | None
    actor_username: str | None
    action: str
    target_type: str
    target_id: str | None
    detail: dict
    created_at: datetime | None


class AccountError(Exception):
    """管理動作被拒絕的基底類別；訊息是給人看的中文，路由層直接轉成 detail。"""


class InvalidInputError(AccountError):
    pass


class UsernameTakenError(AccountError):
    pass


class UserNotFoundError(AccountError):
    pass


class LastAdminError(AccountError):
    pass


class SelfLockoutError(AccountError):
    pass


class LastSuperError(AccountError):
    pass


class PermissionDeniedError(AccountError):
    pass


def normalize_username(username: str) -> str:
    return (username or "").strip()


def username_problem(username: str) -> str | None:
    if not _USERNAME_RE.fullmatch(username or ""):
        return "帳號需為 2–64 個字元，只能包含文字、數字與 . _ @ -"
    return None


def _valid_uuid(value) -> bool:
    try:
        uuid.UUID(str(value))
        return True
    except (ValueError, TypeError, AttributeError):
        return False


async def _audit(session, *, actor_id: str | None, action: str, target_type: str,
                 target_id: str | None, detail: dict | None = None) -> None:
    """在呼叫端的交易裡寫一列稽核（呼叫端負責 commit）。"""
    await session.execute(
        text(
            "INSERT INTO research.admin_audit_log (actor_user_id, action, target_type, target_id, detail) "
            "VALUES (:actor, :action, :ttype, :tid, CAST(:detail AS jsonb))"
        ),
        {
            "actor": actor_id, "action": action, "ttype": target_type, "tid": target_id,
            "detail": json.dumps(detail or {}, ensure_ascii=False),
        },
    )


# 給其他模組（待複核處理）在自己的交易裡寫稽核用；名稱公開，行為與 _audit 相同。
record_audit = _audit


# ───── 登入與 session ─────


async def authenticate(username: str, password: str) -> LoginResult:
    """帳密比對。帳號不分大小寫；停用帳號在密碼正確時才回 `disabled`。

    先比密碼再看停用：否則「停用」這個狀態對不知道密碼的人也看得出來。
    Argon2 是刻意很貴的 CPU 運算（數十毫秒），丟到 thread 免得卡住整個 event loop。
    """
    name = normalize_username(username)
    if not name or not password:
        await asyncio.to_thread(passwords.burn_verify, password or "")
        return LoginResult(None, "unknown_user")
    async with SessionFactory() as session:
        row = (await session.execute(
            text(
                "SELECT id, username, role, enabled, password_hash, is_super, "
                "ARRAY(SELECT scope FROM research.user_scope WHERE user_id = app_user.id) "
                "FROM research.app_user WHERE lower(username) = lower(:u)"
            ),
            {"u": name},
        )).first()
        if row is None:
            await asyncio.to_thread(passwords.burn_verify, password)
            return LoginResult(None, "unknown_user")
        uid, uname, role, enabled, pw_hash, is_super, granted = row
        if not await asyncio.to_thread(passwords.verify_password, pw_hash, password):
            return LoginResult(None, "bad_password")
        if not enabled:
            return LoginResult(None, "disabled")
        params: dict = {"id": uid}
        rehash = ""
        if passwords.needs_rehash(pw_hash):
            params["h"] = await asyncio.to_thread(passwords.hash_password, password)
            rehash = ", password_hash = :h"
        await session.execute(
            text(f"UPDATE research.app_user SET last_login_at = now(){rehash} WHERE id = :id"), params,
        )
        await session.commit()
    return LoginResult(_make_user(uid, uname, role, is_super, granted), "ok")


def _make_user(uid, uname, role, is_super, granted, elevated_until=None) -> User:
    return User(id=str(uid), username=uname, role=role, is_super=bool(is_super),
                scopes=effective_scopes(role, bool(is_super), granted or ()), elevated_until=elevated_until)


async def create_session(user_id: str, *, max_age_seconds: int, ip: str | None = None,
                         user_agent: str | None = None) -> str:
    """開一個新 session，回 session id（放進 cookie，見 web/auth.py）。"""
    async with SessionFactory() as session:
        sid = (await session.execute(
            text(
                "INSERT INTO research.user_session (user_id, expires_at, ip, user_agent) "
                "VALUES (:uid, now() + make_interval(secs => :ttl), :ip, :ua) RETURNING id"
            ),
            {"uid": user_id, "ttl": int(max_age_seconds), "ip": ip,
             "ua": (user_agent or "")[:_MAX_USER_AGENT] or None},
        )).scalar_one()
        await session.commit()
    return str(sid)


async def resolve_session(session_id: str) -> User | None:
    """session 還有效（未撤銷、未過絕對上限）且帳號仍啟用時回 User，否則 None。

    DB 異常**往上拋**：呼叫端（auth middleware）要能分辨「沒登入」與「認證服務掛了」，
    後者回 503 而不是把人導回登入頁——登入頁在 DB 掛掉時一樣登不進去，只會讓人以為密碼錯。
    """
    if not _valid_uuid(session_id):
        return None
    async with SessionFactory() as session:
        row = (await session.execute(
            text(
                "SELECT u.id, u.username, u.role, u.is_super, "
                "ARRAY(SELECT scope FROM research.user_scope WHERE user_id = u.id), s.elevated_until, "
                "s.last_seen_at < now() - make_interval(secs => :touch) AS stale "
                "FROM research.user_session s JOIN research.app_user u ON u.id = s.user_id "
                "WHERE s.id = :sid AND s.revoked_at IS NULL AND s.expires_at > now() AND u.enabled"
            ),
            {"sid": session_id, "touch": _TOUCH_INTERVAL_SECONDS},
        )).first()
        if row is None:
            return None
        uid, uname, role, is_super, granted, elevated_until, stale = row
        if stale:
            try:
                await session.execute(
                    text("UPDATE research.user_session SET last_seen_at = now() WHERE id = :sid"),
                    {"sid": session_id},
                )
                await session.commit()
            except Exception:
                # 最後活動時間只是給管理頁看的，寫不進去不該擋住這個請求。
                logger.warning("user_session last_seen_at 回寫失敗", exc_info=True)
    return _make_user(uid, uname, role, is_super, granted, elevated_until)


async def elevate_session(session_id: str, password: str) -> datetime | None:
    """重新驗證這個 session 擁有者的密碼；正確則把權限提升視窗延到 now()+ELEVATION_SECONDS 並回到期時刻。

    成功與失敗都寫稽核（不記任何密碼衍生值）。session 無效、帳號停用或密碼錯都回 None。
    Argon2 丟到 thread，與 authenticate 同理。
    """
    if not _valid_uuid(session_id) or not password:
        return None
    async with SessionFactory() as session:
        row = (await session.execute(
            text(
                "SELECT u.id, u.username, u.password_hash FROM research.user_session s "
                "JOIN research.app_user u ON u.id = s.user_id "
                "WHERE s.id = :sid AND s.revoked_at IS NULL AND s.expires_at > now() AND u.enabled"
            ),
            {"sid": session_id},
        )).first()
        if row is None:
            return None
        uid, uname, pw_hash = row
        if not await asyncio.to_thread(passwords.verify_password, pw_hash, password):
            await _audit(session, actor_id=str(uid), action="session.elevate_failed", target_type="session",
                         target_id=session_id, detail={"username": uname})
            await session.commit()
            return None
        until = (await session.execute(
            text(
                "UPDATE research.user_session SET elevated_until = now() + make_interval(secs => :ttl) "
                "WHERE id = :sid RETURNING elevated_until"
            ),
            {"sid": session_id, "ttl": ELEVATION_SECONDS},
        )).scalar_one()
        await _audit(session, actor_id=str(uid), action="session.elevate", target_type="session",
                     target_id=session_id, detail={"username": uname, "seconds": ELEVATION_SECONDS})
        await session.commit()
    return until


async def revoke_session(session_id: str) -> None:
    """登出：撤銷這一個 session（其他裝置不受影響）。"""
    if not _valid_uuid(session_id):
        return
    async with SessionFactory() as session:
        await session.execute(
            text("UPDATE research.user_session SET revoked_at = now() WHERE id = :sid AND revoked_at IS NULL"),
            {"sid": session_id},
        )
        await session.commit()


async def _revoke_all(session, user_id: str) -> int:
    result = await session.execute(
        text("UPDATE research.user_session SET revoked_at = now() WHERE user_id = :uid AND revoked_at IS NULL"),
        {"uid": user_id},
    )
    return int(getattr(result, "rowcount", 0) or 0)


# ───── 帳號管理 ─────

_USER_COLS = (
    "u.id, u.username, u.role, u.enabled, u.created_at, u.updated_at, u.password_changed_at, "
    "u.last_login_at, "
    "(SELECT max(s.last_seen_at) FROM research.user_session s WHERE s.user_id = u.id) AS last_seen_at, "
    "(SELECT count(*) FROM research.user_session s WHERE s.user_id = u.id "
    " AND s.revoked_at IS NULL AND s.expires_at > now()) AS active_sessions, "
    "u.is_super, ARRAY(SELECT sc.scope FROM research.user_scope sc WHERE sc.user_id = u.id ORDER BY sc.scope)"
)


def _user_info(row) -> UserInfo:
    (uid, uname, role, enabled, created, updated, pw_changed, last_login, last_seen, active, is_super,
     granted) = row
    return UserInfo(
        id=str(uid), username=uname, role=role, enabled=bool(enabled), created_at=created,
        updated_at=updated, password_changed_at=pw_changed, last_login_at=last_login,
        last_seen_at=last_seen, active_sessions=int(active or 0), is_super=bool(is_super),
        scopes=tuple(granted or ()),
    )


async def _load_user(session, user_id: str) -> UserInfo | None:
    row = (await session.execute(
        text(f"SELECT {_USER_COLS} FROM research.app_user u WHERE u.id = :id"), {"id": user_id},
    )).first()
    return _user_info(row) if row is not None else None


async def list_users() -> list[UserInfo]:
    async with SessionFactory() as session:
        rows = (await session.execute(
            text(f"SELECT {_USER_COLS} FROM research.app_user u ORDER BY lower(u.username)")
        )).all()
    return [_user_info(r) for r in rows]


async def get_user(user_id: str) -> UserInfo | None:
    if not _valid_uuid(user_id):
        return None
    async with SessionFactory() as session:
        return await _load_user(session, user_id)


async def find_user_by_username(username: str) -> UserInfo | None:
    async with SessionFactory() as session:
        row = (await session.execute(
            text(f"SELECT {_USER_COLS} FROM research.app_user u WHERE lower(u.username) = lower(:u)"),
            {"u": normalize_username(username)},
        )).first()
    return _user_info(row) if row is not None else None


async def count_enabled_admins() -> int:
    async with SessionFactory() as session:
        return int((await session.execute(
            text("SELECT count(*) FROM research.app_user WHERE role = 'admin' AND enabled")
        )).scalar_one())


async def count_enabled_supers() -> int:
    async with SessionFactory() as session:
        return int((await session.execute(
            text("SELECT count(*) FROM research.app_user WHERE role = 'admin' AND enabled AND is_super")
        )).scalar_one())


def _check_role(role: str) -> None:
    if role not in ROLES:
        raise InvalidInputError("角色只能是 admin 或 user")


def _check_password(password: str) -> None:
    problem = passwords.password_problem(password or "")
    if problem:
        raise InvalidInputError(problem)


async def _actor_is_super(session, actor_id: str | None) -> bool:
    """actor_id=None（CLI）視為受信任；否則必須是啟用中的 super admin。"""
    if actor_id is None:
        return True
    if not _valid_uuid(actor_id):
        return False
    return bool((await session.execute(
        text("SELECT 1 FROM research.app_user WHERE id = :id AND enabled AND role = 'admin' AND is_super"),
        {"id": actor_id},
    )).first())


async def _require_can_touch(session, actor_id: str | None, target_is_super: bool) -> None:
    if target_is_super and not await _actor_is_super(session, actor_id):
        raise PermissionDeniedError("只有 super admin 能管理 super admin 帳號")


async def create_user(username: str, password: str, role: str = "user", *,
                      actor_id: str | None, via: str = "web", is_super: bool = False) -> UserInfo:
    """is_super=True 只給 CLI 與 super admin（建第一位管理員、救援）。"""
    name = normalize_username(username)
    problem = username_problem(name)
    if problem:
        raise InvalidInputError(problem)
    _check_role(role)
    _check_password(password)
    if is_super and role != "admin":
        raise InvalidInputError("只有管理員能是 super admin")
    pw_hash = await asyncio.to_thread(passwords.hash_password, password)
    try:
        async with SessionFactory() as session:
            if is_super and not await _actor_is_super(session, actor_id):
                raise PermissionDeniedError("只有 super admin 能建立 super admin")
            uid = (await session.execute(
                text(
                    "INSERT INTO research.app_user (username, password_hash, role, is_super) "
                    "VALUES (:u, :h, :r, :s) RETURNING id"
                ),
                {"u": name, "h": pw_hash, "r": role, "s": bool(is_super)},
            )).scalar_one()
            detail = {"username": name, "role": role, "via": via}
            if is_super:
                detail["is_super"] = True
            await _audit(session, actor_id=actor_id, action="user.create", target_type="user",
                         target_id=str(uid), detail=detail)
            info = await _load_user(session, str(uid))
            await session.commit()
    except IntegrityError as exc:
        raise UsernameTakenError(f"帳號「{name}」已存在") from exc
    assert info is not None
    return info


async def _lock_target(session, user_id: str):
    """鎖住所有啟用中的管理員列與目標列，回目標列 (id, username, role, enabled, is_super)。

    先鎖全部管理員是為了「最後一位管理員」的判斷：兩位管理員同時互相降級時，只鎖
    各自的目標列的話，兩筆交易都看得到對方還是管理員，結果兩個都成功、一個管理員都不剩。
    """
    if not _valid_uuid(user_id):
        raise UserNotFoundError("帳號不存在")
    await session.execute(
        text("SELECT id FROM research.app_user WHERE role = 'admin' AND enabled ORDER BY id FOR UPDATE")
    )
    row = (await session.execute(
        text("SELECT id, username, role, enabled, is_super FROM research.app_user WHERE id = :id FOR UPDATE"),
        {"id": user_id},
    )).first()
    if row is None:
        raise UserNotFoundError("帳號不存在")
    return row


async def update_user(user_id: str, *, role: str | None = None, enabled: bool | None = None,
                      actor_id: str | None, via: str = "web") -> UserInfo:
    """改角色／啟用狀態。停用時一併撤銷該帳號所有 session（重新啟用不會讓舊 session 復活）。"""
    if role is not None:
        _check_role(role)
    async with SessionFactory() as session:
        _uid, uname, cur_role, cur_enabled, cur_super = await _lock_target(session, user_id)
        new_role = cur_role if role is None else role
        new_enabled = cur_enabled if enabled is None else bool(enabled)
        effective_super = bool(cur_super) and cur_role == "admin" and cur_enabled
        await _require_can_touch(session, actor_id, effective_super)
        losing_admin = cur_role == "admin" and cur_enabled and (new_role != "admin" or not new_enabled)
        if losing_admin and actor_id is not None and str(actor_id) == str(user_id):
            raise SelfLockoutError("不能停用自己，也不能拿掉自己的管理員權限")
        if losing_admin:
            others = (await session.execute(
                text("SELECT count(*) FROM research.app_user WHERE role = 'admin' AND enabled AND id <> :id"),
                {"id": user_id},
            )).scalar_one()
            if int(others) == 0:
                raise LastAdminError("至少要保留一位啟用中的管理員")
        if losing_admin and effective_super:
            await _require_other_super(session, user_id)
        if (new_role, new_enabled) != (cur_role, cur_enabled):
            await session.execute(
                text("UPDATE research.app_user SET role = :r, enabled = :e, updated_at = now() WHERE id = :id"),
                {"r": new_role, "e": new_enabled, "id": user_id},
            )
            revoked = await _revoke_all(session, user_id) if not new_enabled else 0
            if new_role != cur_role:
                await _audit(session, actor_id=actor_id, action="user.set_role", target_type="user",
                             target_id=str(user_id),
                             detail={"username": uname, "from": cur_role, "to": new_role, "via": via})
            if new_enabled != cur_enabled:
                await _audit(session, actor_id=actor_id,
                             action="user.enable" if new_enabled else "user.disable",
                             target_type="user", target_id=str(user_id),
                             detail={"username": uname, "revoked_sessions": revoked, "via": via})
        info = await _load_user(session, user_id)
        await session.commit()
    assert info is not None
    return info


async def _require_other_super(session, user_id: str) -> None:
    others = (await session.execute(
        text("SELECT count(*) FROM research.app_user WHERE role = 'admin' AND enabled AND is_super AND id <> :id"),
        {"id": user_id},
    )).scalar_one()
    if int(others) == 0:
        raise LastSuperError("至少要保留一位啟用中的 super admin")


async def _target_is_super(session, user_id: str) -> bool:
    return bool((await session.execute(
        text("SELECT 1 FROM research.app_user WHERE id = :id AND role = 'admin' AND enabled AND is_super"),
        {"id": user_id},
    )).first())


async def reset_password(user_id: str, password: str, *, actor_id: str | None,
                         via: str = "web") -> UserInfo:
    """重設密碼並撤銷該帳號所有 session。稽核只記「重設了」，不記任何密碼衍生值。"""
    _check_password(password)
    pw_hash = await asyncio.to_thread(passwords.hash_password, password)
    async with SessionFactory() as session:
        if not _valid_uuid(user_id):
            raise UserNotFoundError("帳號不存在")
        await _require_can_touch(session, actor_id, await _target_is_super(session, user_id))
        row = (await session.execute(
            text(
                "UPDATE research.app_user SET password_hash = :h, password_changed_at = now(), "
                "updated_at = now() WHERE id = :id RETURNING username"
            ),
            {"h": pw_hash, "id": user_id},
        )).first()
        if row is None:
            raise UserNotFoundError("帳號不存在")
        revoked = await _revoke_all(session, user_id)
        await _audit(session, actor_id=actor_id, action="user.reset_password", target_type="user",
                     target_id=str(user_id), detail={"username": row[0], "revoked_sessions": revoked, "via": via})
        info = await _load_user(session, user_id)
        await session.commit()
    assert info is not None
    return info


async def force_logout(user_id: str, *, actor_id: str | None, via: str = "web") -> int:
    """撤銷該帳號所有 session，回撤銷數。"""
    async with SessionFactory() as session:
        if not _valid_uuid(user_id):
            raise UserNotFoundError("帳號不存在")
        row = (await session.execute(
            text("SELECT username FROM research.app_user WHERE id = :id"), {"id": user_id},
        )).first()
        if row is None:
            raise UserNotFoundError("帳號不存在")
        await _require_can_touch(session, actor_id, await _target_is_super(session, user_id))
        revoked = await _revoke_all(session, user_id)
        await _audit(session, actor_id=actor_id, action="user.force_logout", target_type="user",
                     target_id=str(user_id), detail={"username": row[0], "revoked_sessions": revoked, "via": via})
        await session.commit()
    return revoked


async def set_privileges(user_id: str, *, is_super: bool | None = None, scopes: Iterable[str] | None = None,
                         actor_id: str | None, via: str = "web") -> UserInfo:
    """調整 super 身分與另外授予的 scope（只有 super admin／CLI 能做）。scopes 是完整清單（取代）。

    授予（super 或任一 scope）只給啟用中的管理員；收回不限。不能拿掉自己的 super，也不能讓
    啟用中的 super admin 一位都不剩。
    """
    wanted: frozenset[str] | None = None
    if scopes is not None:
        wanted = frozenset(scopes)
        unknown = wanted - GRANTABLE_SCOPES
        if unknown:
            raise InvalidInputError(f"不能授予的 scope：{'、'.join(sorted(unknown))}")
    async with SessionFactory() as session:
        if not await _actor_is_super(session, actor_id):
            raise PermissionDeniedError("只有 super admin 能調整權限")
        # _lock_target 依 id 順序鎖住全部啟用中的管理員（super admin 是其子集）：與 update_user 同一個
        # 取鎖順序，才不會互相死結；「最後一位 super admin」的判斷也靠這把鎖。
        _uid, uname, role, enabled, cur_super = await _lock_target(session, user_id)
        current = frozenset((await session.execute(
            text("SELECT scope FROM research.user_scope WHERE user_id = :id"), {"id": user_id},
        )).scalars().all())
        new_super = bool(cur_super) if is_super is None else bool(is_super)
        new_scopes = current if wanted is None else wanted
        granting = (new_super and not cur_super) or bool(new_scopes - current)
        if granting and not (role == "admin" and enabled):
            raise InvalidInputError("只有啟用中的管理員能被授予 super 或 scope")
        if cur_super and not new_super:
            if actor_id is not None and str(actor_id) == str(user_id):
                raise SelfLockoutError("不能拿掉自己的 super admin 身分")
            if role == "admin" and enabled:
                await _require_other_super(session, user_id)
        added, removed = sorted(new_scopes - current), sorted(current - new_scopes)
        if new_super != bool(cur_super):
            await session.execute(
                text("UPDATE research.app_user SET is_super = :s, updated_at = now() WHERE id = :id"),
                {"s": new_super, "id": user_id},
            )
        if removed:
            await session.execute(
                text("DELETE FROM research.user_scope WHERE user_id = :id AND scope = ANY(CAST(:s AS text[]))"),
                {"id": user_id, "s": removed},
            )
        for scope in added:
            await session.execute(
                text("INSERT INTO research.user_scope (user_id, scope, granted_by) VALUES (:id, :s, :by)"),
                {"id": user_id, "s": scope, "by": actor_id},
            )
        if new_super != bool(cur_super) or added or removed:
            await _audit(session, actor_id=actor_id, action="user.set_privileges", target_type="user",
                         target_id=str(user_id),
                         detail={"username": uname, "is_super": {"from": bool(cur_super), "to": new_super},
                                 "scopes_added": added, "scopes_removed": removed, "via": via})
        info = await _load_user(session, user_id)
        await session.commit()
    assert info is not None
    return info


# 雜湊內容的定義在 DB（research.audit_row_hash，revision 0002），這裡只負責比對：逐列重算並檢查
# prev_hash 是否接到前一列。以 id 排序是正確的——新增時觸發器在鎖內重新取號，id 順序＝鏈的順序。
_AUDIT_VERIFY_SQL = """
SELECT id FROM (
    SELECT id, prev_hash, row_hash, lag(row_hash) OVER (ORDER BY id) AS expected_prev,
           research.audit_row_hash(id, actor_user_id, action, target_type, target_id, detail,
                                   created_at, prev_hash) AS recomputed
    FROM research.admin_audit_log
) t
WHERE prev_hash IS DISTINCT FROM expected_prev OR row_hash IS DISTINCT FROM recomputed
ORDER BY id LIMIT 20
"""


async def verify_audit_chain() -> AuditChainStatus:
    async with SessionFactory() as session:
        broken = tuple(int(i) for i in (await session.execute(text(_AUDIT_VERIFY_SQL))).scalars().all())
        total = int((await session.execute(text("SELECT count(*) FROM research.admin_audit_log"))).scalar_one())
        head = (await session.execute(
            text("SELECT id, row_hash FROM research.admin_audit_log ORDER BY id DESC LIMIT 1")
        )).first()
    return AuditChainStatus(ok=not broken, total=total, head_id=int(head[0]) if head else None,
                            head_hash=head[1] if head else None, broken_ids=broken)


async def audit_row_hashes(ids: Iterable[int]) -> dict[int, str]:
    """指定 id 的 row_hash（給 scripts/audit_anchor.py 比對過去的錨點；不存在的 id 不會出現在結果裡）。"""
    wanted = sorted({int(i) for i in ids})
    if not wanted:
        return {}
    async with SessionFactory() as session:
        rows = (await session.execute(
            text("SELECT id, row_hash FROM research.admin_audit_log WHERE id = ANY(CAST(:ids AS bigint[]))"),
            {"ids": wanted},
        )).all()
    return {int(r[0]): r[1] for r in rows}


async def list_audit(limit: int = 50, offset: int = 0) -> tuple[int, list[AuditEntry]]:
    async with SessionFactory() as session:
        total = (await session.execute(text("SELECT count(*) FROM research.admin_audit_log"))).scalar_one()
        rows = (await session.execute(
            text(
                "SELECT a.id, a.actor_user_id, u.username, a.action, a.target_type, a.target_id, "
                "a.detail, a.created_at FROM research.admin_audit_log a "
                "LEFT JOIN research.app_user u ON u.id = a.actor_user_id "
                "ORDER BY a.created_at DESC, a.id DESC LIMIT :limit OFFSET :offset"
            ),
            {"limit": limit, "offset": max(0, offset)},
        )).all()
    return int(total), [
        AuditEntry(
            id=int(r[0]), actor_user_id=str(r[1]) if r[1] else None, actor_username=r[2],
            action=r[3], target_type=r[4], target_id=r[5],
            detail=r[6] if isinstance(r[6], dict) else {}, created_at=r[7],
        )
        for r in rows
    ]
