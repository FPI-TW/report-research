"""個別帳號、可撤銷 session、兩步驟驗證、帳號刪除與管理稽核（`research.app_user`／`user_session`／
`account_deletion`／`admin_audit_log`）。

取代原本的單一共用帳密。web 層只經 `web.deps.accounts`（本模組物件）呼叫這裡——
那是測試注入假帳號庫的單一位置（`tests/fake_accounts.py`）；本模組的 SQL 本身由
`tests/test_accounts_db.py` 對真的 PostgreSQL 驗。

四個刻意的設計：

1. **每個請求都查 DB**（`resolve_session`）。停用帳號、強制登出、重設密碼都要「立即」
   生效，任何快取都會在那幾秒到幾分鐘裡留下一個還能用的 session。成本是一次主鍵查詢；
   `last_seen_at` 只在超過 `_TOUCH_INTERVAL` 時才回寫，避免每個請求都是一次寫入。
2. **帳號刪除＝清掉內容與可識別資料，列本身保留**。`admin_audit_log`、`review_state.reviewer_user_id`、
   `user_scope.granted_by` 都指著帳號 UUID；刪掉那一列的話稽核紀錄說不出是誰做的。所以刪除時
   刪掉該使用者的問答（`qa_log`，含對話串與回饋）、指向那些問答的 `review_state`、`user_scope`、
   `user_session`，app_user 只留 UUID 並蓋 `deleted_at`（`_purge_user`）。`qa_log.user_id` 刻意沒有
   FK：正確性靠同一筆交易，以及從舊備份還原後的 `scripts/replay_deletions.py`，不靠 CASCADE。
   刪除分兩段：管理員提出＝立即停用＋撤銷 session＋排程 `DELETION_DELAY_SECONDS` 後執行（期間可取消）；
   執行由 `scripts/execute_deletions.py`（timer）呼叫 `execute_deletion`。
3. **稽核與變更同一筆交易**（`_audit`）。改了卻沒留紀錄、或留了紀錄卻沒改，都不會發生。
   刪除相關的稽核 detail 只記數量，不記帳號名稱或任何被刪內容。
4. **TOTP 的「同一個時間步不能用兩次」在鎖住帳號列的交易裡判斷**（`totp_last_step`）。登入第二步
   的暫時憑證（web 層簽章的 cookie）只帶 `mfa_fingerprint`：它涵蓋密碼雜湊、TOTP secret 與最後
   使用的時間步，所以成功一次（時間步前進）、改密碼、重設 TOTP 都會讓舊的暫時憑證失效。
   secret 以明文存在 app_user（與 Argon2 雜湊同一列、同一份備份）：用 session 簽章金鑰加密的話，
   輪替金鑰（全員登出的正常手段）會讓所有人的 TOTP 一起失效。

管理動作的保護在這一層、不在路由層，讓 CLI 也受同樣的規則約束：
- 最後一位啟用中的管理員不能被停用、降級或刪除（`LastAdminError`）。
- 管理員不能停用、刪除自己、也不能拿掉自己的管理員權限（`SelfLockoutError`）。
- 最後一位啟用中的 super admin 不能失去 super、不能被刪除（`LastSuperError`）。
- 對 super admin 的任何管理動作、以及授予 scope／super，都只有 super admin 能做（`PermissionDeniedError`）。
  `actor_id=None`（CLI，`scripts/create_admin.py`）視為主機上的受信任操作者，不受這條限制。
- 已刪除的帳號不能再被修改（`AccountDeletedError`）；排程刪除中的帳號不能被重新啟用（`DeletionPendingError`）。

權限模型（`effective_scopes`）：一般使用者沒有任何 scope；管理員有 `ADMIN_DEFAULT_SCOPES`，加上另外
授予的 `GRANTABLE_SCOPES`（存在 `research.user_scope`）；super admin 有全部 scope。授權判斷只在
`web/authz.py`（`require_scope`），前端顯示與否只是顯示。
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
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

from app.services import passwords, totp
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
# 提出刪除到真正執行的撤銷窗口。
DELETION_DELAY_SECONDS = 24 * 3600
# 已刪除帳號的 password_hash：不是 PHC 字串，passwords.verify_password 一律回 False。
DELETED_PASSWORD_HASH = "!deleted"
DELETED_USERNAME_PREFIX = "deleted-"


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
    totp_enabled: bool = False

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
class MfaChallenge:
    """密碼正確、但帳號開了 TOTP：登入還差第二步。fingerprint 見 `mfa_fingerprint`。"""

    user_id: str
    fingerprint: str


@dataclass(frozen=True)
class LoginResult:
    """登入結果。reason 只進日誌與錯誤頁的分流，絕不帶密碼相關的任何值。

    `totp_required` 時 user 為 None、challenge 帶第二步要用的資料（web 層簽章後放進短效 cookie）。
    """

    user: User | None
    reason: Literal["ok", "unknown_user", "bad_password", "disabled", "totp_required"]
    challenge: MfaChallenge | None = None


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
    totp_enabled: bool = False
    deleted_at: datetime | None = None
    deletion_execute_after: datetime | None = None  # 有尚未執行的刪除排程時


@dataclass(frozen=True)
class TotpStatus:
    enabled: bool
    pending: bool  # 已產生 secret、還沒輸入第一個正確的碼


@dataclass(frozen=True)
class TotpSetup:
    """開啟 TOTP 的第一步：secret 只在這一次回給使用者（之後不再顯示）。"""

    secret: str
    otpauth_uri: str


@dataclass(frozen=True)
class DeletionInfo:
    """一筆帳號刪除排程。status：pending（等待執行，可取消）／cancelled／executed。"""

    id: int
    user_id: str
    username: str | None
    requested_by: str | None
    requested_by_username: str | None
    requested_at: datetime | None
    execute_after: datetime | None
    cancelled_at: datetime | None
    executed_at: datetime | None

    @property
    def status(self) -> str:
        if self.executed_at is not None:
            return "executed"
        if self.cancelled_at is not None:
            return "cancelled"
        return "pending"


@dataclass(frozen=True)
class DeletionResidue:
    """已刪除帳號在 DB 裡還剩什麼（`deletion_residue`）。全部為零／False＝乾淨。"""

    qa_log: int = 0
    review_state: int = 0
    user_scope: int = 0
    user_session: int = 0
    identifiable: bool = False  # app_user 這一列又帶著可識別或可登入的資料
    pending_deletion: bool = False  # 還有尚未執行的排程（例如還原了執行前的備份）

    @property
    def clean(self) -> bool:
        return not (self.qa_log or self.review_state or self.user_scope or self.user_session
                    or self.identifiable or self.pending_deletion)


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


class AccountDeletedError(AccountError):
    """帳號已刪除（只剩 tombstone），不能再修改。"""


class DeletionPendingError(AccountError):
    """帳號已有尚未執行的刪除排程。"""


class NoPendingDeletionError(AccountError):
    """沒有可以取消的刪除排程。"""


class DeletionWindowClosedError(AccountError):
    """撤銷窗口已過（已到執行時刻），不能再取消。"""


class TotpStateError(AccountError):
    """TOTP 狀態不允許這個動作（例如已啟用卻要重新設定）。"""


class TotpRequiredError(AccountError):
    """密碼正確，但帳號開了 TOTP：還需要驗證碼。"""


def normalize_username(username: str) -> str:
    return (username or "").strip()


def username_problem(username: str) -> str | None:
    if not _USERNAME_RE.fullmatch(username or ""):
        return "帳號需為 2–64 個字元，只能包含文字、數字與 . _ @ -"
    return None


def mfa_fingerprint(user_id: str, password_material: str, last_step: int | None, secret: str | None) -> str:
    """登入第二步暫時憑證要綁的指紋。涵蓋密碼雜湊、TOTP secret 與最後使用的時間步：

    成功登入一次（時間步前進）、改密碼、關閉或重設 TOTP 之後，先前發出的暫時憑證全部對不上——
    所以它不可重放，也不必在伺服器端記住發過哪些。指紋不可逆（sha256），cookie 裡看不出任何祕密。
    """
    material = "\x1f".join([str(user_id), password_material or "", str(last_step), secret or ""])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def deleted_username(user_id: str) -> str:
    return f"{DELETED_USERNAME_PREFIX}{user_id}"


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

# 維運寫入類操作（/api/admin/ops/services/{name}/restart|run）的稽核 action 名稱。
OPS_AUDIT_ACTIONS: frozenset[str] = frozenset({"restart", "run"})


async def record_ops_action(*, actor_id: str | None, action: str, service: str, environment: str, result: str,
                            target: str | None = None, invocation_id: str | None = None) -> None:
    """維運寫入類操作的稽核：成功與被拒都寫一列（自己的交易、立即 commit）。

    操作發生在 systemd 那端，沒辦法和稽核放進同一筆交易；所以每一次嘗試都記下結果碼（`queued`／
    `scheduled`＝已交給 systemd，其餘是拒絕或失敗的原因碼）。detail 只有環境、服務、unit 與結果，
    不含代理的訊息全文（可能帶指令輸出），也沒有任何祕密。
    """
    if action not in OPS_AUDIT_ACTIONS:
        raise ValueError(f"未知的維運操作：{action!r}")
    detail = {"environment": environment, "service": service, "target": target, "result": result}
    if invocation_id:
        detail["previous_invocation_id"] = invocation_id
    async with SessionFactory() as session:
        await _audit(session, actor_id=actor_id, action=f"ops.{action}", target_type="ops_service",
                     target_id=service, detail=detail)
        await session.commit()



# 管理後台 CSV 匯出（/api/admin/export/*.csv）的種類；稽核 action 一律 `data.export`、target_id 是種類。
EXPORT_KINDS: frozenset[str] = frozenset({"audit", "users", "reports", "incidents", "jobs"})
_EXPORT_FILTER_MAX_CHARS = 200


def _export_filter_value(value):
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:_EXPORT_FILTER_MAX_CHARS]


async def record_export(*, actor_id: str | None, kind: str, filters: dict, row_count: int,
                        truncated: bool) -> None:
    """一次 CSV 匯出的稽核（自己的交易、立即 commit）：誰、匯出什麼、篩選條件、筆數、是否達上限。

    **不含任何匯出內容**——detail 只有種類、篩選條件（省略 None；字串截到 200 字）、筆數與 truncated。
    路由層在把資料交出去**之前**呼叫它：寫不進稽核就不匯出。
    """
    if kind not in EXPORT_KINDS:
        raise ValueError(f"未知的匯出種類：{kind!r}")
    detail = {
        "kind": kind, "format": "csv",
        "filters": {str(k): _export_filter_value(v) for k, v in sorted(filters.items()) if v is not None},
        "row_count": int(row_count), "truncated": bool(truncated),
    }
    async with SessionFactory() as session:
        await _audit(session, actor_id=actor_id, action="data.export", target_type="export", target_id=kind,
                     detail=detail)
        await session.commit()


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
                "ARRAY(SELECT scope FROM research.user_scope WHERE user_id = app_user.id), "
                "totp_enabled, totp_secret, totp_last_step "
                "FROM research.app_user WHERE lower(username) = lower(:u)"
            ),
            {"u": name},
        )).first()
        if row is None:
            await asyncio.to_thread(passwords.burn_verify, password)
            return LoginResult(None, "unknown_user")
        uid, uname, role, enabled, pw_hash, is_super, granted, totp_on, secret, last_step = row
        if not await asyncio.to_thread(passwords.verify_password, pw_hash, password):
            return LoginResult(None, "bad_password")
        if not enabled:
            return LoginResult(None, "disabled")
        params: dict = {"id": uid}
        rehash = ""
        if passwords.needs_rehash(pw_hash):
            pw_hash = params["h"] = await asyncio.to_thread(passwords.hash_password, password)
            rehash = ", password_hash = :h"
        if totp_on:
            # 還差第二步：last_login_at 等第二步通過才寫（complete_totp_login）。
            if rehash:
                await session.execute(text("UPDATE research.app_user SET password_hash = :h WHERE id = :id"), params)
                await session.commit()
            return LoginResult(None, "totp_required",
                               MfaChallenge(str(uid), mfa_fingerprint(str(uid), pw_hash, last_step, secret)))
        await session.execute(
            text(f"UPDATE research.app_user SET last_login_at = now(){rehash} WHERE id = :id"), params,
        )
        await session.commit()
    return LoginResult(_make_user(uid, uname, role, is_super, granted), "ok")


async def complete_totp_login(user_id: str, fingerprint: str, code: str) -> User | None:
    """登入第二步：暫時憑證的指紋仍對得上、驗證碼正確且時間步比上次新，才回 User（並記錄時間步）。

    任何不符都回 None（呼叫端計入登入失敗限流）。指紋對不上＝暫時憑證已用過、密碼或 TOTP 已變更。
    """
    if not _valid_uuid(user_id) or not fingerprint:
        return None
    async with SessionFactory() as session:
        row = (await session.execute(
            text(
                "SELECT id, username, role, is_super, "
                "ARRAY(SELECT scope FROM research.user_scope WHERE user_id = app_user.id), "
                "password_hash, enabled, deleted_at, totp_enabled, totp_secret, totp_last_step "
                "FROM research.app_user WHERE id = :id FOR UPDATE"
            ),
            {"id": user_id},
        )).first()
        if row is None:
            return None
        uid, uname, role, is_super, granted, pw_hash, enabled, deleted_at, totp_on, secret, last_step = row
        if not enabled or deleted_at is not None or not totp_on:
            return None
        expected = mfa_fingerprint(str(uid), pw_hash, last_step, secret)
        if not hmac.compare_digest(expected.encode(), str(fingerprint).encode()):
            return None
        step = totp.match_step(secret, code, last_step=last_step)
        if step is None:
            return None
        await session.execute(
            text("UPDATE research.app_user SET totp_last_step = :step, last_login_at = now() WHERE id = :id"),
            {"step": step, "id": uid},
        )
        await session.commit()
    return _make_user(uid, uname, role, is_super, granted, totp_enabled=True)


def _make_user(uid, uname, role, is_super, granted, elevated_until=None, totp_enabled=False) -> User:
    return User(id=str(uid), username=uname, role=role, is_super=bool(is_super),
                scopes=effective_scopes(role, bool(is_super), granted or ()), elevated_until=elevated_until,
                totp_enabled=bool(totp_enabled))


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
                "s.last_seen_at < now() - make_interval(secs => :touch) AS stale, u.totp_enabled "
                "FROM research.user_session s JOIN research.app_user u ON u.id = s.user_id "
                "WHERE s.id = :sid AND s.revoked_at IS NULL AND s.expires_at > now() AND u.enabled "
                "AND u.deleted_at IS NULL"
            ),
            {"sid": session_id, "touch": _TOUCH_INTERVAL_SECONDS},
        )).first()
        if row is None:
            return None
        uid, uname, role, is_super, granted, elevated_until, stale, totp_on = row
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
    return _make_user(uid, uname, role, is_super, granted, elevated_until, totp_on)


async def elevate_session(session_id: str, password: str, totp_code: str | None = None) -> datetime | None:
    """重新驗證這個 session 擁有者的密碼（開了 TOTP 的帳號還要驗證碼）；正確則把權限提升視窗延到
    now()+ELEVATION_SECONDS 並回到期時刻。

    密碼正確但開了 TOTP、卻沒給驗證碼時拋 `TotpRequiredError`（不算失敗、不寫稽核，讓前端補問驗證碼）。
    成功與失敗都寫稽核（不記任何密碼衍生值）。session 無效、帳號停用、密碼或驗證碼錯都回 None。
    驗證碼與登入共用「同一個時間步不能用兩次」。Argon2 丟到 thread，與 authenticate 同理。
    """
    if not _valid_uuid(session_id) or not password:
        return None
    async with SessionFactory() as session:
        row = (await session.execute(
            text(
                "SELECT u.id, u.username, u.password_hash, u.totp_enabled, u.totp_secret, u.totp_last_step "
                "FROM research.user_session s JOIN research.app_user u ON u.id = s.user_id "
                "WHERE s.id = :sid AND s.revoked_at IS NULL AND s.expires_at > now() AND u.enabled "
                "AND u.deleted_at IS NULL FOR UPDATE OF u"
            ),
            {"sid": session_id},
        )).first()
        if row is None:
            return None
        uid, uname, pw_hash, totp_on, secret, last_step = row
        if not await asyncio.to_thread(passwords.verify_password, pw_hash, password):
            await _audit(session, actor_id=str(uid), action="session.elevate_failed", target_type="session",
                         target_id=session_id, detail={"username": uname})
            await session.commit()
            return None
        if totp_on:
            if totp.normalize_code(totp_code) is None:
                raise TotpRequiredError("這個帳號開了兩步驟驗證，請輸入驗證碼")
            step = totp.match_step(secret, totp_code, last_step=last_step)
            if step is None:
                await _audit(session, actor_id=str(uid), action="session.elevate_failed", target_type="session",
                             target_id=session_id, detail={"username": uname, "reason": "totp"})
                await session.commit()
                return None
            await session.execute(text("UPDATE research.app_user SET totp_last_step = :s WHERE id = :id"),
                                  {"s": step, "id": uid})
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
    "u.is_super, ARRAY(SELECT sc.scope FROM research.user_scope sc WHERE sc.user_id = u.id ORDER BY sc.scope), "
    "u.totp_enabled, u.deleted_at, "
    "(SELECT d.execute_after FROM research.account_deletion d WHERE d.user_id = u.id "
    " AND d.cancelled_at IS NULL AND d.executed_at IS NULL) AS deletion_execute_after"
)
# 尚未結束（未取消、未執行）的刪除排程。
_PENDING = "cancelled_at IS NULL AND executed_at IS NULL"
_PENDING_D = "d.cancelled_at IS NULL AND d.executed_at IS NULL"


def _user_info(row) -> UserInfo:
    (uid, uname, role, enabled, created, updated, pw_changed, last_login, last_seen, active, is_super,
     granted, totp_on, deleted_at, deletion_after) = row
    return UserInfo(
        id=str(uid), username=uname, role=role, enabled=bool(enabled), created_at=created,
        updated_at=updated, password_changed_at=pw_changed, last_login_at=last_login,
        last_seen_at=last_seen, active_sessions=int(active or 0), is_super=bool(is_super),
        scopes=tuple(granted or ()), totp_enabled=bool(totp_on), deleted_at=deleted_at,
        deletion_execute_after=deletion_after,
    )


async def _load_user(session, user_id: str) -> UserInfo | None:
    row = (await session.execute(
        text(f"SELECT {_USER_COLS} FROM research.app_user u WHERE u.id = :id"), {"id": user_id},
    )).first()
    return _user_info(row) if row is not None else None


async def list_users() -> list[UserInfo]:
    """所有帳號，不含已刪除的（tombstone 只在刪除排程清單裡看得到）。"""
    async with SessionFactory() as session:
        rows = (await session.execute(
            text(f"SELECT {_USER_COLS} FROM research.app_user u WHERE u.deleted_at IS NULL "
                 "ORDER BY lower(u.username)")
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
    已刪除的帳號一律拒絕（`AccountDeletedError`）。
    """
    if not _valid_uuid(user_id):
        raise UserNotFoundError("帳號不存在")
    await session.execute(
        text("SELECT id FROM research.app_user WHERE role = 'admin' AND enabled ORDER BY id FOR UPDATE")
    )
    row = (await session.execute(
        text("SELECT id, username, role, enabled, is_super, deleted_at FROM research.app_user "
             "WHERE id = :id FOR UPDATE"),
        {"id": user_id},
    )).first()
    if row is None:
        raise UserNotFoundError("帳號不存在")
    if row[5] is not None:
        raise AccountDeletedError("帳號已刪除")
    return tuple(row[:5])


async def _has_pending_deletion(session, user_id: str) -> bool:
    return bool((await session.execute(
        text(f"SELECT 1 FROM research.account_deletion WHERE user_id = :id AND {_PENDING}"), {"id": user_id},
    )).first())


async def update_user(user_id: str, *, role: str | None = None, enabled: bool | None = None,
                      actor_id: str | None, via: str = "web") -> UserInfo:
    """改角色／啟用狀態。停用時一併撤銷該帳號所有 session（重新啟用不會讓舊 session 復活）。"""
    if role is not None:
        _check_role(role)
    async with SessionFactory() as session:
        info, _changed = await _update_user_in(session, user_id, role=role, enabled=enabled, actor_id=actor_id,
                                               via=via)
        await session.commit()
    return info


async def _update_user_in(session, user_id: str, *, role: str | None, enabled: bool | None,
                          actor_id: str | None, via: str) -> tuple[UserInfo, bool]:
    """update_user 的本體：在呼叫端的交易裡判規則、改資料、寫稽核（不 commit）。回 (帳號, 是否有變更)。

    拆出來是為了讓多筆操作能在同一筆交易裡逐筆呼叫同一套規則（不另寫一份）。
    """
    _uid, uname, cur_role, cur_enabled, cur_super = await _lock_target(session, user_id)
    new_role = cur_role if role is None else role
    new_enabled = cur_enabled if enabled is None else bool(enabled)
    effective_super = bool(cur_super) and cur_role == "admin" and cur_enabled
    await _require_can_touch(session, actor_id, effective_super)
    if new_enabled and not cur_enabled and await _has_pending_deletion(session, user_id):
        raise DeletionPendingError("這個帳號已排程刪除；要重新啟用請先取消刪除")
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
    assert info is not None
    return info, (new_role, new_enabled) != (cur_role, cur_enabled)


async def _require_other_super(session, user_id: str) -> None:
    others = (await session.execute(
        text("SELECT count(*) FROM research.app_user WHERE role = 'admin' AND enabled AND is_super AND id <> :id"),
        {"id": user_id},
    )).scalar_one()
    if int(others) == 0:
        raise LastSuperError("至少要保留一位啟用中的 super admin")


async def _require_not_deleted(session, user_id: str) -> None:
    """帳號不存在拋 UserNotFoundError、已刪除拋 AccountDeletedError（並鎖住那一列）。"""
    row = (await session.execute(
        text("SELECT deleted_at FROM research.app_user WHERE id = :id FOR UPDATE"), {"id": user_id},
    )).first()
    if row is None:
        raise UserNotFoundError("帳號不存在")
    if row[0] is not None:
        raise AccountDeletedError("帳號已刪除")


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
        await _require_not_deleted(session, user_id)
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
        revoked = await _force_logout_in(session, user_id, actor_id=actor_id, via=via)
        await session.commit()
    return revoked


async def _force_logout_in(session, user_id: str, *, actor_id: str | None, via: str) -> int:
    """force_logout 的本體：在呼叫端的交易裡判規則、撤銷 session、寫稽核（不 commit）。"""
    if not _valid_uuid(user_id):
        raise UserNotFoundError("帳號不存在")
    row = (await session.execute(
        text("SELECT username, deleted_at FROM research.app_user WHERE id = :id"), {"id": user_id},
    )).first()
    if row is None:
        raise UserNotFoundError("帳號不存在")
    if row[1] is not None:
        raise AccountDeletedError("帳號已刪除")
    await _require_can_touch(session, actor_id, await _target_is_super(session, user_id))
    revoked = await _revoke_all(session, user_id)
    await _audit(session, actor_id=actor_id, action="user.force_logout", target_type="user",
                 target_id=str(user_id), detail={"username": row[0], "revoked_sessions": revoked, "via": via})
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


# ───── TOTP（自助開關；管理員可替遺失裝置的人重設）─────


async def _lock_live_user(session, user_id: str):
    """鎖住啟用中、未刪除的帳號列，回 (id, username, role, is_super, totp_enabled, totp_secret)。"""
    if not _valid_uuid(user_id):
        raise UserNotFoundError("帳號不存在")
    row = (await session.execute(
        text("SELECT id, username, role, is_super, totp_enabled, totp_secret, enabled, deleted_at "
             "FROM research.app_user WHERE id = :id FOR UPDATE"),
        {"id": user_id},
    )).first()
    if row is None:
        raise UserNotFoundError("帳號不存在")
    if row[7] is not None:
        raise AccountDeletedError("帳號已刪除")
    if not row[6]:
        raise InvalidInputError("帳號已停用")
    return tuple(row[:6])


async def totp_status(user_id: str) -> TotpStatus:
    if not _valid_uuid(user_id):
        raise UserNotFoundError("帳號不存在")
    async with SessionFactory() as session:
        row = (await session.execute(
            text("SELECT totp_enabled, totp_secret IS NOT NULL FROM research.app_user WHERE id = :id"),
            {"id": user_id},
        )).first()
    if row is None:
        raise UserNotFoundError("帳號不存在")
    return TotpStatus(enabled=bool(row[0]), pending=bool(row[1]) and not row[0])


async def begin_totp_setup(user_id: str) -> TotpSetup:
    """產生新的 secret（尚未啟用）。已啟用時拒絕：要換裝置請先關閉再重新開啟。重複呼叫會換掉未確認的 secret。"""
    async with SessionFactory() as session:
        _uid, uname, _role, _sup, totp_on, _secret = await _lock_live_user(session, user_id)
        if totp_on:
            raise TotpStateError("已開啟兩步驟驗證；要換裝置請先關閉再重新開啟")
        secret = totp.generate_secret()
        await session.execute(
            text("UPDATE research.app_user SET totp_secret = :s, totp_last_step = NULL, updated_at = now() "
                 "WHERE id = :id"),
            {"s": secret, "id": user_id},
        )
        await session.commit()
    return TotpSetup(secret=secret, otpauth_uri=totp.otpauth_uri(secret, uname))


async def confirm_totp(user_id: str, code: str) -> bool:
    """輸入第一個正確的驗證碼才啟用（證明驗證器 App 真的設好了）。碼不對回 False。"""
    async with SessionFactory() as session:
        _uid, uname, _role, _sup, totp_on, secret = await _lock_live_user(session, user_id)
        if totp_on:
            raise TotpStateError("已開啟兩步驟驗證")
        if not secret:
            raise TotpStateError("請先開始設定兩步驟驗證")
        step = totp.match_step(secret, code, last_step=None)
        if step is None:
            return False
        await session.execute(
            text("UPDATE research.app_user SET totp_enabled = true, totp_last_step = :step, updated_at = now() "
                 "WHERE id = :id"),
            {"step": step, "id": user_id},
        )
        await _audit(session, actor_id=str(user_id), action="user.totp_enable", target_type="user",
                     target_id=str(user_id), detail={"username": uname, "via": "web"})
        await session.commit()
    return True


async def disable_totp(user_id: str, *, actor_id: str | None, via: str = "web") -> UserInfo:
    """關閉（本人）或重設（管理員替遺失裝置的人）TOTP：清掉 secret 與時間步。

    本人關閉要已提升權限（路由層 require_elevated）；替別人重設要 accounts.manage＋已提升，
    對象是 super admin 時只有 super admin 能做。停用中的帳號也能重設（救援流程常是先重設再啟用）。
    """
    if not _valid_uuid(user_id):
        raise UserNotFoundError("帳號不存在")
    async with SessionFactory() as session:
        row = (await session.execute(
            text("SELECT username, role, is_super, totp_enabled, totp_secret, deleted_at "
                 "FROM research.app_user WHERE id = :id FOR UPDATE"),
            {"id": user_id},
        )).first()
        if row is None:
            raise UserNotFoundError("帳號不存在")
        uname, role, is_super, totp_on, secret, deleted_at = row
        if deleted_at is not None:
            raise AccountDeletedError("帳號已刪除")
        self_service = actor_id is not None and str(actor_id) == str(user_id)
        if not self_service:
            await _require_can_touch(session, actor_id, bool(is_super) and role == "admin")
        if totp_on or secret:
            await session.execute(
                text("UPDATE research.app_user SET totp_enabled = false, totp_secret = NULL, "
                     "totp_last_step = NULL, updated_at = now() WHERE id = :id"),
                {"id": user_id},
            )
            if totp_on:
                await _audit(session, actor_id=actor_id,
                             action="user.totp_disable" if self_service else "user.totp_reset",
                             target_type="user", target_id=str(user_id), detail={"username": uname, "via": via})
        info = await _load_user(session, user_id)
        await session.commit()
    assert info is not None
    return info


# ───── 帳號刪除（提出 → 撤銷窗口 → 執行；tombstone 與還原後重放見 scripts/）─────

_DELETION_COLS = (
    "d.id, d.user_id, u.username, d.requested_by, r.username, d.requested_at, d.execute_after, "
    "d.cancelled_at, d.executed_at"
)
_DELETION_FROM = (
    "research.account_deletion d JOIN research.app_user u ON u.id = d.user_id "
    "LEFT JOIN research.app_user r ON r.id = d.requested_by"
)


def _deletion_info(row) -> DeletionInfo:
    return DeletionInfo(
        id=int(row[0]), user_id=str(row[1]), username=row[2], requested_by=str(row[3]) if row[3] else None,
        requested_by_username=row[4], requested_at=row[5], execute_after=row[6], cancelled_at=row[7],
        executed_at=row[8],
    )


async def _load_deletion(session, deletion_id: int) -> DeletionInfo:
    row = (await session.execute(
        text(f"SELECT {_DELETION_COLS} FROM {_DELETION_FROM} WHERE d.id = :id"), {"id": deletion_id},
    )).first()
    return _deletion_info(row)


async def request_deletion(user_id: str, *, actor_id: str | None, via: str = "web",
                           delay_seconds: int = DELETION_DELAY_SECONDS) -> DeletionInfo:
    """提出刪除：立即停用、撤銷所有 session，並排程 delay_seconds 後執行（期間可 `cancel_deletion`）。

    與停用同一套保護：不能刪自己、不能刪最後一位啟用中的管理員／super admin，對 super admin 只有
    super admin 能動手。稽核 detail 不記帳號名稱（刪除之後那就是被刪的內容）。
    """
    async with SessionFactory() as session:
        _uid, _uname, role, enabled, is_super = await _lock_target(session, user_id)
        if await _has_pending_deletion(session, user_id):
            raise DeletionPendingError("這個帳號已排程刪除")
        effective_super = bool(is_super) and role == "admin" and enabled
        await _require_can_touch(session, actor_id, bool(is_super) and role == "admin")
        if actor_id is not None and str(actor_id) == str(user_id):
            raise SelfLockoutError("不能刪除自己的帳號")
        if role == "admin" and enabled:
            others = (await session.execute(
                text("SELECT count(*) FROM research.app_user WHERE role = 'admin' AND enabled AND id <> :id"),
                {"id": user_id},
            )).scalar_one()
            if int(others) == 0:
                raise LastAdminError("至少要保留一位啟用中的管理員")
        if effective_super:
            await _require_other_super(session, user_id)
        if enabled:
            await session.execute(
                text("UPDATE research.app_user SET enabled = false, updated_at = now() WHERE id = :id"),
                {"id": user_id},
            )
        revoked = await _revoke_all(session, user_id)
        deletion_id, execute_after = (await session.execute(
            text(
                "INSERT INTO research.account_deletion (user_id, requested_by, execute_after, prior_enabled) "
                "VALUES (:id, :by, now() + make_interval(secs => :delay), :prior) RETURNING id, execute_after"
            ),
            {"id": user_id, "by": actor_id, "delay": int(delay_seconds), "prior": bool(enabled)},
        )).one()
        await _audit(session, actor_id=actor_id, action="user.delete_requested", target_type="user",
                     target_id=str(user_id),
                     detail={"execute_after": execute_after.isoformat(), "revoked_sessions": revoked, "via": via})
        info = await _load_deletion(session, int(deletion_id))
        await session.commit()
    return info


async def cancel_deletion(user_id: str, *, actor_id: str | None, via: str = "web") -> DeletionInfo:
    """撤銷窗口內取消刪除：還原提出當下的啟用狀態（被撤銷的 session 不會復活，要重新登入）。"""
    if not _valid_uuid(user_id):
        raise UserNotFoundError("帳號不存在")
    async with SessionFactory() as session:
        row = (await session.execute(
            text(f"SELECT d.id, d.prior_enabled, d.execute_after <= now(), u.role, u.is_super "
                 f"FROM research.account_deletion d JOIN research.app_user u ON u.id = d.user_id "
                 f"WHERE d.user_id = :id AND {_PENDING_D} FOR UPDATE"),
            {"id": user_id},
        )).first()
        if row is None:
            raise NoPendingDeletionError("這個帳號沒有可以取消的刪除排程")
        deletion_id, prior_enabled, due, role, is_super = row
        if due:
            raise DeletionWindowClosedError("已到執行時刻，不能再取消")
        await _require_can_touch(session, actor_id, bool(is_super) and role == "admin")
        await session.execute(
            text("UPDATE research.account_deletion SET cancelled_at = now(), cancelled_by = :by WHERE id = :id"),
            {"by": actor_id, "id": deletion_id},
        )
        if prior_enabled:
            await session.execute(
                text("UPDATE research.app_user SET enabled = true, updated_at = now() WHERE id = :id"),
                {"id": user_id},
            )
        await _audit(session, actor_id=actor_id, action="user.delete_cancelled", target_type="user",
                     target_id=str(user_id), detail={"restored_enabled": bool(prior_enabled), "via": via})
        info = await _load_deletion(session, int(deletion_id))
        await session.commit()
    return info


async def list_deletions(*, include_done: bool = False, limit: int = 200) -> list[DeletionInfo]:
    """刪除排程，新的在前。預設只列尚未結束的；include_done 連已取消、已執行的一起列。"""
    where = "" if include_done else f"WHERE {_PENDING_D}"
    async with SessionFactory() as session:
        rows = (await session.execute(
            text(f"SELECT {_DELETION_COLS} FROM {_DELETION_FROM} {where} "
                 "ORDER BY d.requested_at DESC, d.id DESC LIMIT :limit"),
            {"limit": int(limit)},
        )).all()
    return [_deletion_info(r) for r in rows]


async def due_deletions() -> list[str]:
    """已到執行時刻、尚未取消或執行的排程的 user_id（舊的在前）。"""
    async with SessionFactory() as session:
        rows = (await session.execute(
            text(f"SELECT user_id FROM research.account_deletion WHERE {_PENDING} AND execute_after <= now() "
                 "ORDER BY execute_after, id")
        )).scalars().all()
    return [str(r) for r in rows]


async def _purge_user(session, user_id: str) -> dict[str, int]:
    """在呼叫端的交易裡刪掉該使用者的內容並清掉 app_user 的可識別資料；回各表刪除數。

    順序：review_state 先（它以 subject_id 指向 qa_log，qa_log 刪了就找不到是哪些），再 qa_log。
    `qa_log.user_id` 沒有 FK，不會有 CASCADE 幫忙——漏一張表就是漏，所以集中在這一個函式。
    """
    params = {"id": user_id}
    review = (await session.execute(
        text("DELETE FROM research.review_state WHERE subject_id IN "
             "(SELECT id FROM research.qa_log WHERE user_id = :id)"), params,
    )).rowcount or 0
    qa = (await session.execute(text("DELETE FROM research.qa_log WHERE user_id = :id"), params)).rowcount or 0
    scopes = (await session.execute(text("DELETE FROM research.user_scope WHERE user_id = :id"), params)).rowcount or 0
    sessions = (await session.execute(
        text("DELETE FROM research.user_session WHERE user_id = :id"), params,
    )).rowcount or 0
    await session.execute(
        text(
            "UPDATE research.app_user SET username = :name, password_hash = :pw, role = 'user', is_super = false, "
            "enabled = false, totp_enabled = false, totp_secret = NULL, totp_last_step = NULL, "
            "last_login_at = NULL, deleted_at = coalesce(deleted_at, now()), updated_at = now() WHERE id = :id"
        ),
        {"id": user_id, "name": deleted_username(user_id), "pw": DELETED_PASSWORD_HASH},
    )
    return {"qa_log": int(qa), "review_state": int(review), "user_scope": int(scopes),
            "user_session": int(sessions)}


async def execute_deletion(user_id: str, *, via: str = "batch") -> datetime | None:
    """執行已到期的刪除排程（同一筆交易：刪內容、清 app_user、蓋 executed_at、寫稽核）。

    沒有到期的排程（已取消、已執行、還沒到時間）回 None、什麼都不做。回執行時刻。
    """
    if not _valid_uuid(user_id):
        return None
    async with SessionFactory() as session:
        deletion_id = (await session.execute(
            text(f"SELECT id FROM research.account_deletion WHERE user_id = :id AND {_PENDING} "
                 "AND execute_after <= now() FOR UPDATE"),
            {"id": user_id},
        )).scalar()
        if deletion_id is None:
            return None
        await session.execute(text("SELECT 1 FROM research.app_user WHERE id = :id FOR UPDATE"), {"id": user_id})
        counts = await _purge_user(session, user_id)
        executed_at = (await session.execute(
            text("UPDATE research.account_deletion SET executed_at = now() WHERE id = :id RETURNING executed_at"),
            {"id": deletion_id},
        )).scalar_one()
        await _audit(session, actor_id=None, action="user.delete_executed", target_type="user",
                     target_id=str(user_id), detail={**counts, "via": via})
        await session.commit()
    return executed_at


async def deletion_residue(user_id: str) -> DeletionResidue:
    """已刪除（有 tombstone）的帳號在 DB 裡還剩什麼。app_user 列不存在＝那份備份比帳號還舊，只看內容表。"""
    if not _valid_uuid(user_id):
        return DeletionResidue()
    async with SessionFactory() as session:
        row = (await session.execute(
            text(
                "SELECT "
                "(SELECT count(*) FROM research.qa_log WHERE user_id = :id), "
                "(SELECT count(*) FROM research.review_state WHERE subject_id IN "
                " (SELECT id FROM research.qa_log WHERE user_id = :id)), "
                "(SELECT count(*) FROM research.user_scope WHERE user_id = :id), "
                "(SELECT count(*) FROM research.user_session WHERE user_id = :id), "
                "(SELECT username <> :name OR password_hash <> :pw OR enabled OR role <> 'user' OR is_super "
                " OR totp_enabled OR totp_secret IS NOT NULL OR last_login_at IS NOT NULL OR deleted_at IS NULL "
                " FROM research.app_user WHERE id = :id), "
                f"EXISTS (SELECT 1 FROM research.account_deletion WHERE user_id = :id AND {_PENDING})"
            ),
            {"id": user_id, "name": deleted_username(user_id), "pw": DELETED_PASSWORD_HASH},
        )).one()
    return DeletionResidue(qa_log=int(row[0]), review_state=int(row[1]), user_scope=int(row[2]),
                           user_session=int(row[3]), identifiable=bool(row[4]), pending_deletion=bool(row[5]))


async def purge_deleted_user(user_id: str, *, via: str = "replay") -> dict[str, int]:
    """重新執行刪除（`scripts/replay_deletions.py`：從舊備份還原後，讓已刪除的資料不會復活）。

    不看排程是否到期——有 tombstone 就代表刪除早已執行過。尚未結束的排程一併蓋上 executed_at。
    """
    if not _valid_uuid(user_id):
        raise UserNotFoundError("帳號不存在")
    async with SessionFactory() as session:
        await session.execute(text("SELECT 1 FROM research.app_user WHERE id = :id FOR UPDATE"), {"id": user_id})
        counts = await _purge_user(session, user_id)
        await session.execute(
            text(f"UPDATE research.account_deletion SET executed_at = now() WHERE user_id = :id AND {_PENDING}"),
            {"id": user_id},
        )
        await _audit(session, actor_id=None, action="user.delete_replayed", target_type="user",
                     target_id=str(user_id), detail={**counts, "via": via})
        await session.commit()
    return counts


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
