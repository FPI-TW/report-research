"""`app.services.accounts` 的記憶體版假物件（不是測試檔：檔名不以 test_ 開頭）。

`tests/conftest.py` 在整個 session 把 `web.deps.accounts` 換成它，預先放一個
tester／testpass 的管理員——既有測試以那組帳密走 `/login`，換成個別帳號後照樣能用。
要驗帳號行為（停用、降級、跨使用者）的測試自己建一份新的、用 `install()` 換上去。

**語意要與真的那份一致**：最後一位管理員保護、不能鎖死自己、停用時撤銷 session、
稽核與變更同時發生。真的 SQL 由 `tests/test_accounts_db.py` 對 PostgreSQL 驗；
兩邊行為分歧時，這份要跟著改（`tests/test_accounts_db.py` 的同一組情境兩邊各跑一次）。
密碼只做明文比對（不跑 Argon2）：這裡驗的是流程，雜湊本身由 `tests/test_passwords.py` 驗。
"""

from __future__ import annotations

import itertools
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from app.services import accounts, passwords
from app.services.accounts import (
    AuditChainStatus,
    AuditEntry,
    InvalidInputError,
    LastAdminError,
    LastSuperError,
    LoginResult,
    PermissionDeniedError,
    SelfLockoutError,
    User,
    UserInfo,
    UsernameTakenError,
    UserNotFoundError,
)


@dataclass
class _Row:
    id: str
    username: str
    password: str
    role: str
    enabled: bool = True
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_login_at: datetime | None = None
    is_super: bool = False
    scopes: set[str] = field(default_factory=set)  # 另外授予的（GRANTABLE_SCOPES）


@dataclass
class _Sess:
    id: str
    user_id: str
    expires_at: datetime
    revoked: bool = False
    ip: str | None = None
    user_agent: str | None = None
    elevated_until: datetime | None = None


class FakeAccounts:
    """與 `app.services.accounts` 同名同簽章的 async 函式集合。"""

    # 讓 `deps.accounts.User`、`deps.accounts.DEV_USER` 之類的取用照樣成立。
    User = User
    DEV_USER = accounts.DEV_USER
    ROLES = accounts.ROLES
    AccountError = accounts.AccountError
    InvalidInputError = InvalidInputError
    UsernameTakenError = UsernameTakenError
    UserNotFoundError = UserNotFoundError
    LastAdminError = LastAdminError
    SelfLockoutError = SelfLockoutError
    LastSuperError = LastSuperError
    PermissionDeniedError = PermissionDeniedError
    ADMIN_DEFAULT_SCOPES = accounts.ADMIN_DEFAULT_SCOPES
    GRANTABLE_SCOPES = accounts.GRANTABLE_SCOPES
    ALL_SCOPES = accounts.ALL_SCOPES
    ELEVATION_SECONDS = accounts.ELEVATION_SECONDS

    def __init__(self) -> None:
        self.users: dict[str, _Row] = {}
        self.sessions: dict[str, _Sess] = {}
        self.audit: list[AuditEntry] = []
        self._audit_ids = itertools.count(1)
        # 設成例外實例時，所有呼叫都拋它（模擬 DB 掛掉）。
        self.fail_with: Exception | None = None
        # 稽核雜湊鏈：測試可把某個 id 放進 broken 模擬竄改。
        self.broken_audit_ids: set[int] = set()

    # ── 測試輔助 ─────────────────────────────────────────────────────
    def add_user(self, username: str, password: str, role: str = "user", *, enabled: bool = True,
                 is_super: bool = False, scopes=()) -> str:
        uid = str(uuid.uuid4())
        self.users[uid] = _Row(id=uid, username=username, password=password, role=role, enabled=enabled,
                               is_super=is_super, scopes=set(scopes))
        return uid

    def _user(self, row: _Row, elevated_until=None) -> User:
        return User(id=row.id, username=row.username, role=row.role, is_super=row.is_super,
                    scopes=accounts.effective_scopes(row.role, row.is_super, row.scopes),
                    elevated_until=elevated_until)

    def _actor_is_super(self, actor_id) -> bool:
        if actor_id is None:
            return True
        a = self.users.get(str(actor_id))
        return bool(a and a.enabled and a.role == "admin" and a.is_super)

    def _effective_super(self, row: _Row) -> bool:
        return row.is_super and row.role == "admin" and row.enabled

    def _require_can_touch(self, actor_id, row: _Row) -> None:
        if self._effective_super(row) and not self._actor_is_super(actor_id):
            raise PermissionDeniedError("只有 super admin 能管理 super admin 帳號")

    def _require_other_super(self, row: _Row) -> None:
        if not any(self._effective_super(r) and r.id != row.id for r in self.users.values()):
            raise LastSuperError("至少要保留一位啟用中的 super admin")

    def _check(self) -> None:
        if self.fail_with is not None:
            raise self.fail_with

    def _by_name(self, username: str) -> _Row | None:
        name = accounts.normalize_username(username).lower()
        return next((u for u in self.users.values() if u.username.lower() == name), None)

    def _info(self, row: _Row) -> UserInfo:
        now = datetime.now(timezone.utc)
        live = [s for s in self.sessions.values() if s.user_id == row.id and not s.revoked and s.expires_at > now]
        return UserInfo(
            id=row.id, username=row.username, role=row.role, enabled=row.enabled,
            created_at=row.created_at, updated_at=row.created_at, password_changed_at=row.created_at,
            last_login_at=row.last_login_at, last_seen_at=None, active_sessions=len(live),
            is_super=row.is_super, scopes=tuple(sorted(row.scopes)),
        )

    def _audit(self, actor_id, action, target_id, detail, target_type="user") -> None:
        actor = self.users.get(str(actor_id)) if actor_id else None
        self.audit.insert(0, AuditEntry(
            id=next(self._audit_ids), actor_user_id=str(actor_id) if actor_id else None,
            actor_username=actor.username if actor else None, action=action, target_type=target_type,
            target_id=target_id, detail=detail, created_at=datetime.now(timezone.utc),
        ))

    def _revoke_all(self, user_id: str) -> int:
        n = 0
        for s in self.sessions.values():
            if s.user_id == user_id and not s.revoked:
                s.revoked = True
                n += 1
        return n

    # ── 與 accounts 同介面 ───────────────────────────────────────────
    async def authenticate(self, username: str, password: str) -> LoginResult:
        self._check()
        row = self._by_name(username or "")
        if row is None or not password:
            return LoginResult(None, "unknown_user")
        if row.password != password:
            return LoginResult(None, "bad_password")
        if not row.enabled:
            return LoginResult(None, "disabled")
        row.last_login_at = datetime.now(timezone.utc)
        return LoginResult(self._user(row), "ok")

    async def create_session(self, user_id: str, *, max_age_seconds: int, ip=None, user_agent=None) -> str:
        self._check()
        sid = str(uuid.uuid4())
        self.sessions[sid] = _Sess(
            id=sid, user_id=str(user_id),
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=max_age_seconds),
            ip=ip, user_agent=user_agent,
        )
        return sid

    async def resolve_session(self, session_id: str) -> User | None:
        self._check()
        s = self.sessions.get(session_id)
        if s is None or s.revoked or s.expires_at <= datetime.now(timezone.utc):
            return None
        row = self.users.get(s.user_id)
        if row is None or not row.enabled:
            return None
        return self._user(row, s.elevated_until)

    async def elevate_session(self, session_id: str, password: str):
        self._check()
        s = self.sessions.get(session_id)
        if s is None or s.revoked or s.expires_at <= datetime.now(timezone.utc) or not password:
            return None
        row = self.users.get(s.user_id)
        if row is None or not row.enabled:
            return None
        if row.password != password:
            self._audit(row.id, "session.elevate_failed", session_id, {"username": row.username}, "session")
            return None
        s.elevated_until = datetime.now(timezone.utc) + timedelta(seconds=accounts.ELEVATION_SECONDS)
        self._audit(row.id, "session.elevate", session_id,
                    {"username": row.username, "seconds": accounts.ELEVATION_SECONDS}, "session")
        return s.elevated_until

    async def revoke_session(self, session_id: str) -> None:
        self._check()
        s = self.sessions.get(session_id)
        if s is not None:
            s.revoked = True

    async def list_users(self) -> list[UserInfo]:
        self._check()
        return [self._info(r) for r in sorted(self.users.values(), key=lambda r: r.username.lower())]

    async def get_user(self, user_id: str) -> UserInfo | None:
        self._check()
        row = self.users.get(str(user_id))
        return self._info(row) if row else None

    async def find_user_by_username(self, username: str) -> UserInfo | None:
        self._check()
        row = self._by_name(username)
        return self._info(row) if row else None

    async def count_enabled_admins(self) -> int:
        self._check()
        return sum(1 for r in self.users.values() if r.role == "admin" and r.enabled)

    async def count_enabled_supers(self) -> int:
        self._check()
        return sum(1 for r in self.users.values() if self._effective_super(r))

    async def create_user(self, username, password, role="user", *, actor_id, via="web",
                          is_super=False) -> UserInfo:
        self._check()
        name = accounts.normalize_username(username)
        problem = accounts.username_problem(name)
        if problem:
            raise InvalidInputError(problem)
        if role not in accounts.ROLES:
            raise InvalidInputError("角色只能是 admin 或 user")
        problem = passwords.password_problem(password or "")
        if problem:
            raise InvalidInputError(problem)
        if is_super and role != "admin":
            raise InvalidInputError("只有管理員能是 super admin")
        if is_super and not self._actor_is_super(actor_id):
            raise PermissionDeniedError("只有 super admin 能建立 super admin")
        if self._by_name(name) is not None:
            raise UsernameTakenError(f"帳號「{name}」已存在")
        uid = self.add_user(name, password, role, is_super=bool(is_super))
        detail = {"username": name, "role": role, "via": via}
        if is_super:
            detail["is_super"] = True
        self._audit(actor_id, "user.create", uid, detail)
        return self._info(self.users[uid])

    async def update_user(self, user_id, *, role=None, enabled=None, actor_id, via="web") -> UserInfo:
        self._check()
        if role is not None and role not in accounts.ROLES:
            raise InvalidInputError("角色只能是 admin 或 user")
        row = self.users.get(str(user_id))
        if row is None:
            raise UserNotFoundError("帳號不存在")
        new_role = row.role if role is None else role
        new_enabled = row.enabled if enabled is None else bool(enabled)
        self._require_can_touch(actor_id, row)
        losing = row.role == "admin" and row.enabled and (new_role != "admin" or not new_enabled)
        if losing and actor_id is not None and str(actor_id) == row.id:
            raise SelfLockoutError("不能停用自己，也不能拿掉自己的管理員權限")
        if losing and not any(
            r.role == "admin" and r.enabled and r.id != row.id for r in self.users.values()
        ):
            raise LastAdminError("至少要保留一位啟用中的管理員")
        if losing and self._effective_super(row):
            self._require_other_super(row)
        if new_role != row.role:
            self._audit(actor_id, "user.set_role", row.id,
                        {"username": row.username, "from": row.role, "to": new_role, "via": via})
        if new_enabled != row.enabled:
            revoked = self._revoke_all(row.id) if not new_enabled else 0
            self._audit(actor_id, "user.enable" if new_enabled else "user.disable", row.id,
                        {"username": row.username, "revoked_sessions": revoked, "via": via})
        row.role, row.enabled = new_role, new_enabled
        return self._info(row)

    async def reset_password(self, user_id, password, *, actor_id, via="web") -> UserInfo:
        self._check()
        problem = passwords.password_problem(password or "")
        if problem:
            raise InvalidInputError(problem)
        row = self.users.get(str(user_id))
        if row is None:
            raise UserNotFoundError("帳號不存在")
        self._require_can_touch(actor_id, row)
        row.password = password
        revoked = self._revoke_all(row.id)
        self._audit(actor_id, "user.reset_password", row.id,
                    {"username": row.username, "revoked_sessions": revoked, "via": via})
        return self._info(row)

    async def force_logout(self, user_id, *, actor_id, via="web") -> int:
        self._check()
        row = self.users.get(str(user_id))
        if row is None:
            raise UserNotFoundError("帳號不存在")
        self._require_can_touch(actor_id, row)
        revoked = self._revoke_all(row.id)
        self._audit(actor_id, "user.force_logout", row.id,
                    {"username": row.username, "revoked_sessions": revoked, "via": via})
        return revoked

    async def set_privileges(self, user_id, *, is_super=None, scopes=None, actor_id, via="web") -> UserInfo:
        self._check()
        wanted = None
        if scopes is not None:
            wanted = frozenset(scopes)
            unknown = wanted - accounts.GRANTABLE_SCOPES
            if unknown:
                raise InvalidInputError(f"不能授予的 scope：{'、'.join(sorted(unknown))}")
        if not self._actor_is_super(actor_id):
            raise PermissionDeniedError("只有 super admin 能調整權限")
        row = self.users.get(str(user_id))
        if row is None:
            raise UserNotFoundError("帳號不存在")
        current = frozenset(row.scopes)
        new_super = row.is_super if is_super is None else bool(is_super)
        new_scopes = current if wanted is None else wanted
        granting = (new_super and not row.is_super) or bool(new_scopes - current)
        if granting and not (row.role == "admin" and row.enabled):
            raise InvalidInputError("只有啟用中的管理員能被授予 super 或 scope")
        if row.is_super and not new_super:
            if actor_id is not None and str(actor_id) == row.id:
                raise SelfLockoutError("不能拿掉自己的 super admin 身分")
            if row.role == "admin" and row.enabled:
                self._require_other_super(row)
        added, removed = sorted(new_scopes - current), sorted(current - new_scopes)
        if new_super != row.is_super or added or removed:
            self._audit(actor_id, "user.set_privileges", row.id,
                        {"username": row.username, "is_super": {"from": row.is_super, "to": new_super},
                         "scopes_added": added, "scopes_removed": removed, "via": via})
        row.is_super, row.scopes = new_super, set(new_scopes)
        return self._info(row)

    async def verify_audit_chain(self) -> AuditChainStatus:
        self._check()
        head = self.audit[0] if self.audit else None
        broken = tuple(sorted(self.broken_audit_ids))[:20]
        return AuditChainStatus(ok=not broken, total=len(self.audit), head_id=head.id if head else None,
                                head_hash=self._fake_hash(head.id) if head else None, broken_ids=broken)

    def _fake_hash(self, audit_id: int) -> str:
        return ("tampered" if audit_id in self.broken_audit_ids else "h") + f"{audit_id:062d}"

    async def audit_row_hashes(self, ids) -> dict[int, str]:
        self._check()
        existing = {e.id for e in self.audit}
        return {int(i): self._fake_hash(int(i)) for i in ids if int(i) in existing}

    async def list_audit(self, limit: int = 50, offset: int = 0):
        self._check()
        return len(self.audit), self.audit[offset:offset + limit]


def default_accounts() -> FakeAccounts:
    """conftest 預設裝上的那一份：tester／testpass 管理員（既有測試的登入帳密）。"""
    fake = FakeAccounts()
    fake.add_user("tester", "testpass", "admin")
    return fake


@contextmanager
def install(fake: FakeAccounts):
    """在範圍內把 `web.deps.accounts` 換成 `fake`，離開時還原。"""
    from web import deps

    orig = deps.accounts
    deps.accounts = fake
    try:
        yield fake
    finally:
        deps.accounts = orig


def session_cookies(username: str = "tester") -> dict[str, str]:
    """直接拿到一組登入後的 cookie（不走 /login）：在目前裝上的假帳號庫開一個 session 並簽章。

    給「只是需要已登入」的測試用；驗登入流程本身的測試請照常 POST /login。
    """
    import asyncio
    import time

    from web import auth, deps

    store = deps.accounts
    if not isinstance(store, FakeAccounts):
        raise RuntimeError("session_cookies() 只能在假帳號庫裝上時使用（tests/conftest.py 的 _fake_accounts）")
    row = store._by_name(username)
    if row is None:
        raise KeyError(f"假帳號庫裡沒有 {username}")
    sid = asyncio.run(store.create_session(row.id, max_age_seconds=auth.MAX_ABSOLUTE_TTL))
    return {auth.COOKIE_NAME: auth.issue_token(int(time.time()), session_id=sid)}


def _default_user_id(username: str = "tester") -> str:
    """目前裝上的假帳號庫裡某帳號的 id（預設 conftest 那位 tester）。"""
    from web import deps

    row = deps.accounts._by_name(username)
    if row is None:
        raise KeyError(username)
    return row.id
