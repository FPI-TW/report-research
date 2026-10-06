"""`app.services.accounts` 的記憶體版假物件（不是測試檔：檔名不以 test_ 開頭）。

`tests/conftest.py` 在整個 session 把 `web.deps.accounts` 換成它，預先放一個
tester／testpass 的管理員——既有測試以那組帳密走 `/login`，換成個別帳號後照樣能用。
要驗帳號行為（停用、降級、跨使用者）的測試自己建一份新的、用 `install()` 換上去。

**語意要與真的那份一致**：最後一位管理員保護、不能鎖死自己、停用時撤銷 session、
稽核與變更同時發生、TOTP 的時間步不可重用、刪除排程與執行後清掉的東西。
真的 SQL 由 `tests/test_accounts_db.py` 對 PostgreSQL 驗；
兩邊行為分歧時，這份要跟著改（`tests/test_accounts_db.py` 的同一組情境兩邊各跑一次）。
密碼只做明文比對（不跑 Argon2）：這裡驗的是流程，雜湊本身由 `tests/test_passwords.py` 驗。
問答紀錄只模擬刪除需要的部分（`qa_logs`：id → user_id；`review_subjects`：review_state 的 subject_id），
測試用 `seed_qa_log()` 放資料。
"""

from __future__ import annotations

import itertools
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from app.services import accounts, passwords, totp
from app.services.accounts import (
    AccountDeletedError,
    AuditChainStatus,
    AuditEntry,
    DeletionInfo,
    DeletionPendingError,
    DeletionResidue,
    DeletionWindowClosedError,
    InvalidInputError,
    LastAdminError,
    LastSuperError,
    LoginResult,
    MfaChallenge,
    NoPendingDeletionError,
    PermissionDeniedError,
    SelfLockoutError,
    TotpRequiredError,
    TotpSetup,
    TotpStateError,
    TotpStatus,
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
    totp_secret: str | None = None
    totp_enabled: bool = False
    totp_last_step: int | None = None
    deleted_at: datetime | None = None


@dataclass
class _Deletion:
    id: int
    user_id: str
    requested_by: str | None
    requested_at: datetime
    execute_after: datetime
    prior_enabled: bool
    cancelled_at: datetime | None = None
    cancelled_by: str | None = None
    executed_at: datetime | None = None

    @property
    def pending(self) -> bool:
        return self.cancelled_at is None and self.executed_at is None


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
    AccountDeletedError = AccountDeletedError
    DeletionPendingError = DeletionPendingError
    NoPendingDeletionError = NoPendingDeletionError
    DeletionWindowClosedError = DeletionWindowClosedError
    TotpStateError = TotpStateError
    TotpRequiredError = TotpRequiredError
    DELETION_DELAY_SECONDS = accounts.DELETION_DELAY_SECONDS
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
        self.deletions: list[_Deletion] = []
        self._deletion_ids = itertools.count(1)
        self.qa_logs: dict[str, str] = {}  # qa_log.id → user_id
        self.review_subjects: set[str] = set()  # review_state.subject_id

    def seed_qa_log(self, user_id: str, *, with_review: bool = False) -> str:
        qid = str(uuid.uuid4())
        self.qa_logs[qid] = str(user_id)
        if with_review:
            self.review_subjects.add(qid)
        return qid

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
                    elevated_until=elevated_until, totp_enabled=row.totp_enabled)

    def _pending_deletion(self, user_id: str) -> _Deletion | None:
        return next((d for d in self.deletions if d.user_id == str(user_id) and d.pending), None)

    def _get(self, user_id) -> _Row:
        row = self.users.get(str(user_id))
        if row is None:
            raise UserNotFoundError("帳號不存在")
        return row

    def _get_live(self, user_id) -> _Row:
        """未刪除的帳號（_lock_target 的語意）。"""
        row = self._get(user_id)
        if row.deleted_at is not None:
            raise AccountDeletedError("帳號已刪除")
        return row

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
            is_super=row.is_super, scopes=tuple(sorted(row.scopes)), totp_enabled=row.totp_enabled,
            deleted_at=row.deleted_at,
            deletion_execute_after=d.execute_after if (d := self._pending_deletion(row.id)) else None,
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
        if row.totp_enabled:
            return LoginResult(None, "totp_required", MfaChallenge(row.id, self._fingerprint(row)))
        row.last_login_at = datetime.now(timezone.utc)
        return LoginResult(self._user(row), "ok")

    def _fingerprint(self, row: _Row) -> str:
        return accounts.mfa_fingerprint(row.id, row.password, row.totp_last_step, row.totp_secret)

    async def complete_totp_login(self, user_id: str, fingerprint: str, code: str) -> User | None:
        self._check()
        row = self.users.get(str(user_id))
        if row is None or not row.enabled or row.deleted_at is not None or not row.totp_enabled:
            return None
        if self._fingerprint(row) != fingerprint:
            return None
        step = totp.match_step(row.totp_secret, code, last_step=row.totp_last_step)
        if step is None:
            return None
        row.totp_last_step = step
        row.last_login_at = datetime.now(timezone.utc)
        return self._user(row)

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
        if row is None or not row.enabled or row.deleted_at is not None:
            return None
        return self._user(row, s.elevated_until)

    async def elevate_session(self, session_id: str, password: str, totp_code: str | None = None):
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
        if row.totp_enabled:
            if totp.normalize_code(totp_code) is None:
                raise TotpRequiredError("這個帳號開了兩步驟驗證，請輸入驗證碼")
            step = totp.match_step(row.totp_secret, totp_code, last_step=row.totp_last_step)
            if step is None:
                self._audit(row.id, "session.elevate_failed", session_id,
                            {"username": row.username, "reason": "totp"}, "session")
                return None
            row.totp_last_step = step
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
        live = [r for r in self.users.values() if r.deleted_at is None]
        return [self._info(r) for r in sorted(live, key=lambda r: r.username.lower())]

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
        row = self._get_live(user_id)
        new_role = row.role if role is None else role
        new_enabled = row.enabled if enabled is None else bool(enabled)
        self._require_can_touch(actor_id, row)
        if new_enabled and not row.enabled and self._pending_deletion(row.id):
            raise DeletionPendingError("這個帳號已排程刪除；要重新啟用請先取消刪除")
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
        row = self._get_live(user_id)
        self._require_can_touch(actor_id, row)
        row.password = password
        revoked = self._revoke_all(row.id)
        self._audit(actor_id, "user.reset_password", row.id,
                    {"username": row.username, "revoked_sessions": revoked, "via": via})
        return self._info(row)

    async def force_logout(self, user_id, *, actor_id, via="web") -> int:
        self._check()
        row = self._get_live(user_id)
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
        row = self._get_live(user_id)
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

    # ── TOTP ─────────────────────────────────────────────────────────
    def _live_enabled(self, user_id) -> _Row:
        row = self._get_live(user_id)
        if not row.enabled:
            raise InvalidInputError("帳號已停用")
        return row

    async def totp_status(self, user_id: str) -> TotpStatus:
        self._check()
        row = self._get(user_id)
        return TotpStatus(enabled=row.totp_enabled, pending=row.totp_secret is not None and not row.totp_enabled)

    async def begin_totp_setup(self, user_id: str) -> TotpSetup:
        self._check()
        row = self._live_enabled(user_id)
        if row.totp_enabled:
            raise TotpStateError("已開啟兩步驟驗證；要換裝置請先關閉再重新開啟")
        row.totp_secret, row.totp_last_step = totp.generate_secret(), None
        return TotpSetup(secret=row.totp_secret, otpauth_uri=totp.otpauth_uri(row.totp_secret, row.username))

    async def confirm_totp(self, user_id: str, code: str) -> bool:
        self._check()
        row = self._live_enabled(user_id)
        if row.totp_enabled:
            raise TotpStateError("已開啟兩步驟驗證")
        if not row.totp_secret:
            raise TotpStateError("請先開始設定兩步驟驗證")
        step = totp.match_step(row.totp_secret, code, last_step=None)
        if step is None:
            return False
        row.totp_enabled, row.totp_last_step = True, step
        self._audit(row.id, "user.totp_enable", row.id, {"username": row.username, "via": "web"})
        return True

    async def disable_totp(self, user_id: str, *, actor_id, via: str = "web") -> UserInfo:
        self._check()
        row = self._get_live(user_id)
        self_service = actor_id is not None and str(actor_id) == row.id
        if not self_service and row.is_super and row.role == "admin" and not self._actor_is_super(actor_id):
            raise PermissionDeniedError("只有 super admin 能管理 super admin 帳號")
        was_on = row.totp_enabled
        row.totp_enabled, row.totp_secret, row.totp_last_step = False, None, None
        if was_on:
            self._audit(actor_id, "user.totp_disable" if self_service else "user.totp_reset", row.id,
                        {"username": row.username, "via": via})
        return self._info(row)

    # ── 帳號刪除 ─────────────────────────────────────────────────────
    def _deletion_info(self, d: _Deletion) -> DeletionInfo:
        target = self.users.get(d.user_id)
        req = self.users.get(d.requested_by) if d.requested_by else None
        return DeletionInfo(id=d.id, user_id=d.user_id, username=target.username if target else None,
                            requested_by=d.requested_by, requested_by_username=req.username if req else None,
                            requested_at=d.requested_at, execute_after=d.execute_after,
                            cancelled_at=d.cancelled_at, executed_at=d.executed_at)

    async def request_deletion(self, user_id: str, *, actor_id, via: str = "web",
                               delay_seconds: int = accounts.DELETION_DELAY_SECONDS) -> DeletionInfo:
        self._check()
        row = self._get_live(user_id)
        if self._pending_deletion(row.id):
            raise DeletionPendingError("這個帳號已排程刪除")
        if row.is_super and row.role == "admin" and not self._actor_is_super(actor_id):
            raise PermissionDeniedError("只有 super admin 能管理 super admin 帳號")
        if actor_id is not None and str(actor_id) == row.id:
            raise SelfLockoutError("不能刪除自己的帳號")
        if row.role == "admin" and row.enabled and not any(
            r.role == "admin" and r.enabled and r.id != row.id for r in self.users.values()
        ):
            raise LastAdminError("至少要保留一位啟用中的管理員")
        if self._effective_super(row):
            self._require_other_super(row)
        now = datetime.now(timezone.utc)
        d = _Deletion(id=next(self._deletion_ids), user_id=row.id,
                      requested_by=str(actor_id) if actor_id else None, requested_at=now,
                      execute_after=now + timedelta(seconds=int(delay_seconds)), prior_enabled=row.enabled)
        self.deletions.append(d)
        row.enabled = False
        revoked = self._revoke_all(row.id)
        self._audit(actor_id, "user.delete_requested", row.id,
                    {"execute_after": d.execute_after.isoformat(), "revoked_sessions": revoked, "via": via})
        return self._deletion_info(d)

    async def cancel_deletion(self, user_id: str, *, actor_id, via: str = "web") -> DeletionInfo:
        self._check()
        d = self._pending_deletion(str(user_id))
        if d is None:
            raise NoPendingDeletionError("這個帳號沒有可以取消的刪除排程")
        if d.execute_after <= datetime.now(timezone.utc):
            raise DeletionWindowClosedError("已到執行時刻，不能再取消")
        row = self.users[d.user_id]
        if row.is_super and row.role == "admin" and not self._actor_is_super(actor_id):
            raise PermissionDeniedError("只有 super admin 能管理 super admin 帳號")
        d.cancelled_at, d.cancelled_by = datetime.now(timezone.utc), str(actor_id) if actor_id else None
        if d.prior_enabled:
            row.enabled = True
        self._audit(actor_id, "user.delete_cancelled", row.id, {"restored_enabled": d.prior_enabled, "via": via})
        return self._deletion_info(d)

    async def list_deletions(self, *, include_done: bool = False, limit: int = 200) -> list[DeletionInfo]:
        self._check()
        items = [d for d in self.deletions if include_done or d.pending]
        items.sort(key=lambda d: (d.requested_at, d.id), reverse=True)
        return [self._deletion_info(d) for d in items[:limit]]

    async def due_deletions(self) -> list[str]:
        self._check()
        now = datetime.now(timezone.utc)
        due = sorted((d for d in self.deletions if d.pending and d.execute_after <= now),
                     key=lambda d: (d.execute_after, d.id))
        return [d.user_id for d in due]

    def _purge(self, user_id: str) -> dict[str, int]:
        qids = {q for q, uid in self.qa_logs.items() if uid == user_id}
        review = len(self.review_subjects & qids)
        self.review_subjects -= qids
        for q in qids:
            del self.qa_logs[q]
        row = self.users.get(user_id)
        scopes = 0
        if row is not None:
            scopes = len(row.scopes)
            row.username, row.password = accounts.deleted_username(user_id), accounts.DELETED_PASSWORD_HASH
            row.role, row.is_super, row.enabled, row.scopes = "user", False, False, set()
            row.totp_enabled, row.totp_secret, row.totp_last_step = False, None, None
            row.last_login_at = None
            row.deleted_at = row.deleted_at or datetime.now(timezone.utc)
        sessions = [sid for sid, s in self.sessions.items() if s.user_id == user_id]
        for sid in sessions:
            del self.sessions[sid]
        return {"qa_log": len(qids), "review_state": review, "user_scope": scopes, "user_session": len(sessions)}

    async def execute_deletion(self, user_id: str, *, via: str = "batch"):
        self._check()
        d = self._pending_deletion(str(user_id))
        now = datetime.now(timezone.utc)
        if d is None or d.execute_after > now:
            return None
        counts = self._purge(d.user_id)
        d.executed_at = now
        self._audit(None, "user.delete_executed", d.user_id, {**counts, "via": via})
        return now

    async def deletion_residue(self, user_id: str) -> DeletionResidue:
        self._check()
        uid = str(user_id)
        qids = {q for q, owner in self.qa_logs.items() if owner == uid}
        row = self.users.get(uid)
        identifiable = row is not None and (
            row.username != accounts.deleted_username(uid) or row.password != accounts.DELETED_PASSWORD_HASH
            or row.enabled or row.role != "user" or row.is_super or row.totp_enabled
            or row.totp_secret is not None or row.last_login_at is not None or row.deleted_at is None
        )
        return DeletionResidue(
            qa_log=len(qids), review_state=len(self.review_subjects & qids),
            user_scope=len(row.scopes) if row else 0,
            user_session=sum(1 for s in self.sessions.values() if s.user_id == uid),
            identifiable=identifiable, pending_deletion=self._pending_deletion(uid) is not None,
        )

    async def purge_deleted_user(self, user_id: str, *, via: str = "replay") -> dict[str, int]:
        self._check()
        counts = self._purge(str(user_id))
        now = datetime.now(timezone.utc)
        for d in self.deletions:
            if d.user_id == str(user_id) and d.pending:
                d.executed_at = now
        self._audit(None, "user.delete_replayed", str(user_id), {**counts, "via": via})
        return counts

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

    async def record_ops_action(self, *, actor_id, action, service, environment, result, target=None,
                                invocation_id=None) -> None:
        self._check()
        if action not in accounts.OPS_AUDIT_ACTIONS:
            raise ValueError(f"未知的維運操作：{action!r}")
        detail = {"environment": environment, "service": service, "target": target, "result": result}
        if invocation_id:
            detail["previous_invocation_id"] = invocation_id
        self._audit(actor_id, f"ops.{action}", service, detail, "ops_service")

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
