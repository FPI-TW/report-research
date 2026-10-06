"""帳號服務（app/services/accounts.py）對真的 PostgreSQL 成立，且假帳號庫與它行為一致。

同一組情境跑兩次：
- `FakeParityTests`：對 `tests/fake_accounts.py` 的記憶體版（一律跑）。其他 HTTP 層測試都
  靠那份假物件，它的語意若與真的 SQL 分歧，那些測試的綠燈就不可信。
- `AccountsDbTests`：對真的 PostgreSQL（CI 的「schema 契約」job；本機連不上就 skip）。

**一律 rollback、絕不 commit**——本機預設連到的是生產庫。accounts 的函式自己會 commit，
所以這裡把 `accounts.SessionFactory` 換成「綁在一條外層交易上、commit 只釋放 savepoint」
的 session（SQLAlchemy 的 `join_transaction_mode="create_savepoint"`），最後整條交易 rollback。
帳號名稱一律帶隨機尾碼，不假設庫是空的。
"""

from __future__ import annotations

import asyncio
import os
import unittest
import uuid
from contextlib import contextmanager
from unittest import mock

from fake_accounts import FakeAccounts

from app.services import accounts, totp


def _name(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


PW = "first-password-1"
PW2 = "second-password-2"


async def scenario_login(api) -> None:
    name = _name("Login")
    info = await api.create_user(name, PW, "user", actor_id=None)
    ok = await api.authenticate(name.lower(), PW)
    assert ok.reason == "ok" and ok.user is not None and ok.user.id == info.id, ok
    assert ok.user.role == "user"
    assert (await api.authenticate(name, "wrong-password")).reason == "bad_password"
    assert (await api.authenticate(_name("nobody"), PW)).reason == "unknown_user"
    assert (await api.authenticate(name, "")).reason == "unknown_user"


async def scenario_duplicate_username_case_insensitive(api) -> None:
    name = _name("Dup")
    await api.create_user(name, PW, "user", actor_id=None)
    try:
        await api.create_user(name.upper(), PW, "user", actor_id=None)
    except accounts.UsernameTakenError:
        return
    raise AssertionError("大小寫不同的同名帳號應被拒絕")


async def scenario_invalid_input(api) -> None:
    for bad in (("a", PW, "user"), (_name("ok"), "short", "user"), (_name("ok"), PW, "root"),
                ("has space", PW, "user")):
        try:
            await api.create_user(*bad, actor_id=None)
        except accounts.InvalidInputError:
            continue
        raise AssertionError(f"應拒絕 {bad[0]!r}/{bad[2]!r}")


async def scenario_session_lifecycle(api) -> None:
    info = await api.create_user(_name("Sess"), PW, "user", actor_id=None)
    sid = await api.create_session(info.id, max_age_seconds=3600, ip="10.0.0.1", user_agent="ua")
    user = await api.resolve_session(sid)
    assert user is not None and user.id == info.id
    await api.revoke_session(sid)
    assert await api.resolve_session(sid) is None
    assert await api.resolve_session("not-a-uuid") is None
    assert await api.resolve_session(str(uuid.uuid4())) is None


async def scenario_expired_session(api) -> None:
    info = await api.create_user(_name("Exp"), PW, "user", actor_id=None)
    sid = await api.create_session(info.id, max_age_seconds=0)
    assert await api.resolve_session(sid) is None


async def scenario_disable_is_immediate_and_revokes(api) -> None:
    admin = await api.create_user(_name("Adm"), PW, "admin", actor_id=None)
    target = await api.create_user(_name("Tgt"), PW, "user", actor_id=None)
    sid = await api.create_session(target.id, max_age_seconds=3600)
    after = await api.update_user(target.id, enabled=False, actor_id=admin.id)
    assert after.enabled is False and after.active_sessions == 0
    assert await api.resolve_session(sid) is None
    assert (await api.authenticate(target.username, PW)).reason == "disabled"
    # 重新啟用不會讓舊 session 復活
    await api.update_user(target.id, enabled=True, actor_id=admin.id)
    assert await api.resolve_session(sid) is None
    assert (await api.authenticate(target.username, PW)).reason == "ok"


async def scenario_role_change_takes_effect(api) -> None:
    admin = await api.create_user(_name("Adm"), PW, "admin", actor_id=None)
    target = await api.create_user(_name("Tgt"), PW, "user", actor_id=None)
    sid = await api.create_session(target.id, max_age_seconds=3600)
    await api.update_user(target.id, role="admin", actor_id=admin.id)
    assert (await api.resolve_session(sid)).role == "admin"


async def scenario_self_lockout(api) -> None:
    a = await api.create_user(_name("SelfA"), PW, "admin", actor_id=None)
    await api.create_user(_name("SelfB"), PW, "admin", actor_id=None)
    for kwargs in ({"enabled": False}, {"role": "user"}):
        try:
            await api.update_user(a.id, actor_id=a.id, **kwargs)
        except accounts.SelfLockoutError:
            continue
        raise AssertionError(f"管理員不該能對自己 {kwargs}")
    # 對自己做不影響管理權的事（例如再設一次 admin）是允許的
    await api.update_user(a.id, role="admin", actor_id=a.id)


async def scenario_last_admin(api) -> None:
    """只在「庫裡沒有別的啟用中管理員」時有意義（CI 的空庫、假帳號庫）。"""
    a = await api.create_user(_name("Last"), PW, "admin", actor_id=None)
    for kwargs in ({"enabled": False}, {"role": "user"}):
        try:
            await api.update_user(a.id, actor_id=None, **kwargs)
        except accounts.LastAdminError:
            continue
        raise AssertionError(f"最後一位管理員不該能被 {kwargs}")
    b = await api.create_user(_name("Last2"), PW, "admin", actor_id=None)
    await api.update_user(a.id, role="user", actor_id=b.id)  # 有第二位之後就可以


async def scenario_reset_password(api) -> None:
    admin = await api.create_user(_name("Adm"), PW, "admin", actor_id=None)
    target = await api.create_user(_name("Rst"), PW, "user", actor_id=None)
    sid = await api.create_session(target.id, max_age_seconds=3600)
    await api.reset_password(target.id, PW2, actor_id=admin.id)
    assert await api.resolve_session(sid) is None
    assert (await api.authenticate(target.username, PW)).reason == "bad_password"
    assert (await api.authenticate(target.username, PW2)).reason == "ok"
    try:
        await api.reset_password(target.id, "short", actor_id=admin.id)
    except accounts.InvalidInputError:
        pass
    else:
        raise AssertionError("重設密碼也要套密碼政策")


async def scenario_force_logout_and_audit(api) -> None:
    admin = await api.create_user(_name("Adm"), PW, "admin", actor_id=None)
    target = await api.create_user(_name("Out"), PW, "user", actor_id=None)
    s1 = await api.create_session(target.id, max_age_seconds=3600)
    s2 = await api.create_session(target.id, max_age_seconds=3600)
    assert await api.force_logout(target.id, actor_id=admin.id) == 2
    assert await api.resolve_session(s1) is None and await api.resolve_session(s2) is None
    total, entries = await api.list_audit(limit=200)
    mine = [e for e in entries if e.target_id == target.id]
    actions = [e.action for e in mine]
    assert "user.create" in actions and "user.force_logout" in actions, actions
    out = next(e for e in mine if e.action == "user.force_logout")
    assert out.actor_user_id == admin.id and out.actor_username == admin.username
    assert out.detail.get("revoked_sessions") == 2
    assert total >= len(entries)


async def scenario_audit_never_contains_password(api) -> None:
    admin = await api.create_user(_name("Adm"), PW, "admin", actor_id=None)
    target = await api.create_user(_name("Sec"), PW, "user", actor_id=None)
    await api.reset_password(target.id, PW2, actor_id=admin.id)
    _total, entries = await api.list_audit(limit=200)
    blob = repr([e.detail for e in entries if e.target_id in (admin.id, target.id)])
    assert PW not in blob and PW2 not in blob, blob


async def scenario_unknown_user_errors(api) -> None:
    for call in (
        lambda: api.update_user(str(uuid.uuid4()), enabled=False, actor_id=None),
        lambda: api.reset_password(str(uuid.uuid4()), PW2, actor_id=None),
        lambda: api.force_logout(str(uuid.uuid4()), actor_id=None),
    ):
        try:
            await call()
        except accounts.UserNotFoundError:
            continue
        raise AssertionError("不存在的帳號應拋 UserNotFoundError")


async def _expect(exc_type, coro) -> None:
    try:
        await coro
    except exc_type:
        return
    raise AssertionError(f"應拋 {exc_type.__name__}")


async def scenario_scopes_and_super(api) -> None:
    boss = await api.create_user(_name("Boss"), PW, "admin", actor_id=None, is_super=True)
    plain = await api.create_user(_name("Plain"), PW, "admin", actor_id=None)
    member = await api.create_user(_name("Member"), PW, "user", actor_id=None)
    sid = await api.create_session(plain.id, max_age_seconds=3600)
    assert (await api.resolve_session(sid)).scopes == accounts.ADMIN_DEFAULT_SCOPES
    info = await api.set_privileges(plain.id, scopes=["qa_content.read"], actor_id=boss.id)
    assert info.scopes == ("qa_content.read",) and info.is_super is False
    assert "qa_content.read" in (await api.resolve_session(sid)).scopes
    boss_sid = await api.create_session(boss.id, max_age_seconds=3600)
    boss_user = await api.resolve_session(boss_sid)
    assert boss_user.is_super and boss_user.scopes == accounts.ALL_SCOPES
    member_sid = await api.create_session(member.id, max_age_seconds=3600)
    assert (await api.resolve_session(member_sid)).scopes == frozenset()
    await _expect(accounts.InvalidInputError, api.set_privileges(member.id, scopes=["ops.operate"], actor_id=boss.id))
    await _expect(accounts.InvalidInputError, api.set_privileges(plain.id, scopes=["root"], actor_id=boss.id))
    await _expect(accounts.PermissionDeniedError, api.set_privileges(member.id, scopes=[], actor_id=plain.id))
    await _expect(accounts.PermissionDeniedError,
                  api.create_user(_name("Sneaky"), PW, "admin", actor_id=plain.id, is_super=True))
    info = await api.set_privileges(plain.id, scopes=[], actor_id=boss.id)  # 收回
    assert info.scopes == ()
    _total, entries = await api.list_audit(limit=200)
    priv = [e for e in entries if e.target_id == plain.id and e.action == "user.set_privileges"]
    assert len(priv) == 2 and priv[0].detail["scopes_removed"] == ["qa_content.read"], priv


async def scenario_super_protections(api) -> None:
    a = await api.create_user(_name("SupA"), PW, "admin", actor_id=None, is_super=True)
    b = await api.create_user(_name("SupB"), PW, "admin", actor_id=None, is_super=True)
    plain = await api.create_user(_name("Plain"), PW, "admin", actor_id=None)
    for call in (
        lambda: api.update_user(a.id, enabled=False, actor_id=plain.id),
        lambda: api.update_user(a.id, role="user", actor_id=plain.id),
        lambda: api.reset_password(a.id, PW2, actor_id=plain.id),
        lambda: api.force_logout(a.id, actor_id=plain.id),
    ):
        await _expect(accounts.PermissionDeniedError, call())
    await _expect(accounts.SelfLockoutError, api.set_privileges(a.id, is_super=False, actor_id=a.id))
    info = await api.set_privileges(b.id, is_super=False, actor_id=a.id)  # 還有 a，可以
    assert info.is_super is False
    await api.update_user(plain.id, role="user", actor_id=a.id)  # super 管一般管理員照常


async def scenario_last_super(api) -> None:
    """只在「庫裡沒有別的啟用中 super admin」時有意義（CI 的空庫、假帳號庫）。"""
    a = await api.create_user(_name("OnlySup"), PW, "admin", actor_id=None, is_super=True)
    await api.create_user(_name("Helper"), PW, "admin", actor_id=None)
    await _expect(accounts.LastSuperError, api.set_privileges(a.id, is_super=False, actor_id=None))
    await _expect(accounts.LastSuperError, api.update_user(a.id, enabled=False, actor_id=None))
    await _expect(accounts.LastSuperError, api.update_user(a.id, role="user", actor_id=None))


async def scenario_elevation(api) -> None:
    info = await api.create_user(_name("Elev"), PW, "admin", actor_id=None)
    s1 = await api.create_session(info.id, max_age_seconds=3600)
    s2 = await api.create_session(info.id, max_age_seconds=3600)
    assert not (await api.resolve_session(s1)).is_elevated
    assert await api.elevate_session(s1, "wrong-password") is None
    until = await api.elevate_session(s1, PW)
    assert until is not None
    assert (await api.resolve_session(s1)).is_elevated
    assert not (await api.resolve_session(s2)).is_elevated  # 綁在 session 上
    await api.revoke_session(s2)
    assert await api.elevate_session(s2, PW) is None
    assert await api.elevate_session("not-a-uuid", PW) is None
    _total, entries = await api.list_audit(limit=200)
    actions = [e.action for e in entries if e.target_id == s1]
    assert "session.elevate" in actions and "session.elevate_failed" in actions, actions
    assert PW not in repr([e.detail for e in entries if e.target_id == s1])


async def scenario_audit_chain(api) -> None:
    admin = await api.create_user(_name("Chain"), PW, "admin", actor_id=None)
    await api.force_logout(admin.id, actor_id=None)
    status = await api.verify_audit_chain()
    assert status.ok, status
    assert status.head_id is not None and status.total >= 2
    assert await api.audit_row_hashes([status.head_id]) == {status.head_id: status.head_hash}
    assert await api.audit_row_hashes([-1]) == {}


# ───── TOTP ─────

@contextmanager
def _clock(t: float):
    """把 TOTP 的時鐘固定在 t（fake 與真 SQL 都經 totp.match_step，同一個替換點）。"""
    with mock.patch.object(totp, "_now", lambda: t):
        yield


T0 = 1_800_000_000.0  # 固定時刻：時間步 = T0 // 30


def _code(secret: str, t: float) -> str:
    return totp.code_at(secret, totp.current_step(t))


async def _enable_totp(api, user_id: str, t: float = T0) -> str:
    setup = await api.begin_totp_setup(user_id)
    assert setup.otpauth_uri.startswith("otpauth://totp/") and setup.secret in setup.otpauth_uri
    status = await api.totp_status(user_id)
    assert status.pending and not status.enabled
    with _clock(t):
        assert await api.confirm_totp(user_id, "000000" if _code(setup.secret, t) != "000000" else "111111") is False
        assert await api.confirm_totp(user_id, _code(setup.secret, t)) is True
    assert (await api.totp_status(user_id)).enabled
    return setup.secret


async def scenario_totp_login_two_steps(api) -> None:
    info = await api.create_user(_name("Totp"), PW, "user", actor_id=None)
    secret = await _enable_totp(api, info.id)
    res = await api.authenticate(info.username, PW)
    assert res.reason == "totp_required" and res.user is None and res.challenge is not None
    assert res.challenge.user_id == info.id
    fp = res.challenge.fingerprint
    with _clock(T0):
        # 同一個時間步（確認時用掉的那一步）不能再用一次
        assert await api.complete_totp_login(info.id, fp, _code(secret, T0)) is None
    with _clock(T0 + 30):
        assert await api.complete_totp_login(info.id, "bad-fingerprint", _code(secret, T0 + 30)) is None
        assert await api.complete_totp_login(info.id, fp, "123") is None
        user = await api.complete_totp_login(info.id, fp, _code(secret, T0 + 30))
        assert user is not None and user.id == info.id and user.totp_enabled
        # 暫時憑證不可重放：時間步前進後舊指紋對不上
        assert await api.complete_totp_login(info.id, fp, _code(secret, T0 + 60)) is None
    # 密碼錯仍是 bad_password，不洩漏有沒有開 TOTP
    assert (await api.authenticate(info.username, "wrong-password")).reason == "bad_password"


async def scenario_totp_fingerprint_follows_password(api) -> None:
    admin = await api.create_user(_name("Adm"), PW, "admin", actor_id=None)
    info = await api.create_user(_name("TotpPw"), PW, "user", actor_id=None)
    secret = await _enable_totp(api, info.id)
    fp = (await api.authenticate(info.username, PW)).challenge.fingerprint
    await api.reset_password(info.id, PW2, actor_id=admin.id)
    with _clock(T0 + 30):
        assert await api.complete_totp_login(info.id, fp, _code(secret, T0 + 30)) is None


async def scenario_totp_setup_rules_and_disable(api) -> None:
    admin = await api.create_user(_name("Adm"), PW, "admin", actor_id=None)
    info = await api.create_user(_name("TotpRule"), PW, "user", actor_id=None)
    await _expect(accounts.TotpStateError, api.confirm_totp(info.id, "123456"))  # 還沒開始設定
    await _enable_totp(api, info.id)
    await _expect(accounts.TotpStateError, api.begin_totp_setup(info.id))  # 已啟用不能覆蓋
    after = await api.disable_totp(info.id, actor_id=info.id)
    assert after.totp_enabled is False
    status = await api.totp_status(info.id)
    assert not status.enabled and not status.pending
    assert (await api.authenticate(info.username, PW)).reason == "ok"
    await _enable_totp(api, info.id)
    after = await api.disable_totp(info.id, actor_id=admin.id)  # 管理員替遺失裝置的人重設
    assert after.totp_enabled is False
    _total, entries = await api.list_audit(limit=200)
    actions = [e.action for e in entries if e.target_id == info.id]
    assert {"user.totp_enable", "user.totp_disable", "user.totp_reset"} <= set(actions), actions
    blob = repr([e.detail for e in entries if e.target_id == info.id])
    assert "secret" not in blob.lower()


async def scenario_totp_elevation(api) -> None:
    info = await api.create_user(_name("TotpElev"), PW, "admin", actor_id=None)
    sid = await api.create_session(info.id, max_age_seconds=3600)
    secret = await _enable_totp(api, info.id)
    await _expect(accounts.TotpRequiredError, api.elevate_session(sid, PW))
    with _clock(T0 + 30):
        assert await api.elevate_session(sid, "wrong-password", _code(secret, T0 + 30)) is None
        assert await api.elevate_session(sid, PW, "000000" if _code(secret, T0 + 30) != "000000" else "111111") is None
        assert await api.elevate_session(sid, PW, _code(secret, T0 + 30)) is not None
        assert (await api.resolve_session(sid)).is_elevated
        # 同一個碼不能再拿來提升一次
        assert await api.elevate_session(sid, PW, _code(secret, T0 + 30)) is None


async def scenario_totp_admin_reset_super_requires_super(api) -> None:
    boss = await api.create_user(_name("Boss"), PW, "admin", actor_id=None, is_super=True)
    await api.create_user(_name("Boss2"), PW, "admin", actor_id=None, is_super=True)
    plain = await api.create_user(_name("Plain"), PW, "admin", actor_id=None)
    await _enable_totp(api, boss.id)
    await _expect(accounts.PermissionDeniedError, api.disable_totp(boss.id, actor_id=plain.id))


# ───── 帳號刪除 ─────

async def _seed_qa(api, user_id: str, *, with_review: bool = False) -> str:
    """放一筆屬於 user_id 的問答（與指向它的 review_state）。"""
    if isinstance(api, FakeAccounts):
        return api.seed_qa_log(user_id, with_review=with_review)
    from sqlalchemy import text

    qid = str(uuid.uuid4())
    async with accounts.SessionFactory() as session:
        await session.execute(
            text("INSERT INTO research.qa_log (id, question, answer, user_id, feedback) "
                 "VALUES (:id, 'q', 'a', :uid, 'dislike')"),
            {"id": qid, "uid": user_id},
        )
        if with_review:
            await session.execute(
                text("INSERT INTO research.review_state (kind, subject_id, status, note) "
                     "VALUES ('feedback', :id, 'open', 'n')"),
                {"id": qid},
            )
        await session.commit()
    return qid


async def scenario_deletion_request_and_cancel(api) -> None:
    admin = await api.create_user(_name("Adm"), PW, "admin", actor_id=None)
    target = await api.create_user(_name("Del"), PW, "user", actor_id=None)
    sid = await api.create_session(target.id, max_age_seconds=3600)
    d = await api.request_deletion(target.id, actor_id=admin.id)
    assert d.status == "pending" and d.user_id == target.id and d.requested_by == admin.id
    assert d.execute_after is not None and d.requested_by_username == admin.username
    assert await api.resolve_session(sid) is None  # 立即撤銷
    assert (await api.authenticate(target.username, PW)).reason == "disabled"
    info = await api.get_user(target.id)
    assert info.enabled is False and info.deletion_execute_after is not None
    await _expect(accounts.DeletionPendingError, api.request_deletion(target.id, actor_id=admin.id))
    await _expect(accounts.DeletionPendingError, api.update_user(target.id, enabled=True, actor_id=admin.id))
    assert [x.user_id for x in await api.list_deletions()].count(target.id) == 1
    assert target.id not in await api.due_deletions()  # 還沒到期
    assert await api.execute_deletion(target.id) is None
    c = await api.cancel_deletion(target.id, actor_id=admin.id)
    assert c.status == "cancelled"
    info = await api.get_user(target.id)
    assert info.enabled is True and info.deletion_execute_after is None  # 還原提出前的啟用狀態
    assert (await api.authenticate(target.username, PW)).reason == "ok"
    await _expect(accounts.NoPendingDeletionError, api.cancel_deletion(target.id, actor_id=admin.id))
    assert target.id in [x.user_id for x in await api.list_deletions(include_done=True)]
    _total, entries = await api.list_audit(limit=200)
    mine = [e for e in entries if e.target_id == target.id]
    assert {"user.delete_requested", "user.delete_cancelled"} <= {e.action for e in mine}
    for e in mine:
        if e.action.startswith("user.delete"):
            assert target.username not in repr(e.detail), e.detail


async def scenario_deletion_cancel_keeps_disabled(api) -> None:
    admin = await api.create_user(_name("Adm"), PW, "admin", actor_id=None)
    target = await api.create_user(_name("DelOff"), PW, "user", actor_id=None)
    await api.update_user(target.id, enabled=False, actor_id=admin.id)
    await api.request_deletion(target.id, actor_id=admin.id)
    await api.cancel_deletion(target.id, actor_id=admin.id)
    assert (await api.get_user(target.id)).enabled is False


async def scenario_deletion_protections(api) -> None:
    a = await api.create_user(_name("SupA"), PW, "admin", actor_id=None, is_super=True)
    await api.create_user(_name("SupB"), PW, "admin", actor_id=None, is_super=True)
    plain = await api.create_user(_name("Plain"), PW, "admin", actor_id=None)
    await _expect(accounts.SelfLockoutError, api.request_deletion(plain.id, actor_id=plain.id))
    await _expect(accounts.PermissionDeniedError, api.request_deletion(a.id, actor_id=plain.id))
    await _expect(accounts.UserNotFoundError, api.request_deletion(str(uuid.uuid4()), actor_id=None))
    await _expect(accounts.NoPendingDeletionError, api.cancel_deletion(plain.id, actor_id=a.id))
    d = await api.request_deletion(plain.id, actor_id=a.id, delay_seconds=0)
    # 已到執行時刻就不能取消（批次可能已經寫了 tombstone）
    await _expect(accounts.DeletionWindowClosedError, api.cancel_deletion(plain.id, actor_id=a.id))
    assert d.user_id in await api.due_deletions()


async def scenario_deletion_execute_purges(api) -> None:
    admin = await api.create_user(_name("Adm"), PW, "admin", actor_id=None, is_super=True)
    target = await api.create_user(_name("Gone"), PW, "admin", actor_id=None)
    other = await api.create_user(_name("Stay"), PW, "user", actor_id=None)
    await api.set_privileges(target.id, scopes=["qa_content.read"], actor_id=admin.id)
    await _enable_totp(api, target.id)
    await api.create_session(target.id, max_age_seconds=3600)
    q1 = await _seed_qa(api, target.id, with_review=True)
    await _seed_qa(api, target.id)
    kept = await _seed_qa(api, other.id, with_review=True)
    await api.request_deletion(target.id, actor_id=admin.id, delay_seconds=0)
    before = await api.deletion_residue(target.id)
    assert before.qa_log == 2 and before.review_state == 1 and before.pending_deletion and before.identifiable
    executed = await api.execute_deletion(target.id)
    assert executed is not None
    assert await api.execute_deletion(target.id) is None  # 冪等：已執行就不再動
    residue = await api.deletion_residue(target.id)
    assert residue.clean, residue
    info = await api.get_user(target.id)
    assert info.username == accounts.deleted_username(target.id) and info.deleted_at is not None
    assert info.role == "user" and not info.is_super and not info.totp_enabled and info.scopes == ()
    assert target.id not in [u.id for u in await api.list_users()]
    assert (await api.authenticate(target.username, PW)).reason == "unknown_user"
    assert (await api.authenticate(info.username, PW)).reason in ("bad_password", "disabled")
    assert (await api.deletion_residue(other.id)).qa_log == 1  # 別人的不受影響
    for call in (
        lambda: api.update_user(target.id, enabled=True, actor_id=admin.id),
        lambda: api.reset_password(target.id, PW2, actor_id=admin.id),
        lambda: api.force_logout(target.id, actor_id=admin.id),
        lambda: api.set_privileges(target.id, scopes=[], actor_id=admin.id),
        lambda: api.request_deletion(target.id, actor_id=admin.id),
    ):
        await _expect(accounts.AccountDeletedError, call())
    # 原帳號名稱可以再用
    again = await api.create_user(target.username, PW, "user", actor_id=None)
    assert again.id != target.id
    _total, entries = await api.list_audit(limit=200)
    done = next(e for e in entries if e.target_id == target.id and e.action == "user.delete_executed")
    assert done.detail["qa_log"] == 2 and done.detail["review_state"] == 1
    assert target.username not in repr(done.detail)
    assert q1 and kept


async def scenario_deletion_replay(api) -> None:
    """模擬從舊備份還原：已刪除帳號的問答又出現、app_user 又變回可識別 → purge 後乾淨。"""
    admin = await api.create_user(_name("Adm"), PW, "admin", actor_id=None)
    target = await api.create_user(_name("Back"), PW, "user", actor_id=None)
    await api.request_deletion(target.id, actor_id=admin.id, delay_seconds=0)
    await api.execute_deletion(target.id)
    assert (await api.deletion_residue(target.id)).clean
    await _seed_qa(api, target.id, with_review=True)  # 「復活」的問答
    residue = await api.deletion_residue(target.id)
    assert residue.qa_log == 1 and residue.review_state == 1 and not residue.clean
    counts = await api.purge_deleted_user(target.id)
    assert counts["qa_log"] == 1 and counts["review_state"] == 1
    assert (await api.deletion_residue(target.id)).clean
    nobody = str(uuid.uuid4())  # 備份比帳號還舊：app_user 不存在也不算殘留
    assert (await api.deletion_residue(nobody)).clean


async def scenario_last_admin_cannot_be_deleted(api) -> None:
    """只在「庫裡沒有別的啟用中管理員」時有意義（CI 的空庫、假帳號庫）。"""
    a = await api.create_user(_name("OnlyAdm"), PW, "admin", actor_id=None, is_super=True)
    await _expect(accounts.LastAdminError, api.request_deletion(a.id, actor_id=None))
    b = await api.create_user(_name("Helper"), PW, "admin", actor_id=None)
    await _expect(accounts.LastSuperError, api.request_deletion(a.id, actor_id=None))
    await api.request_deletion(b.id, actor_id=None)  # 一般管理員：還有 a，可以


async def scenario_ops_action_audit(api) -> None:
    """維運寫入類操作的稽核：成功與被拒各一列，detail 只有環境、服務、unit 與結果碼；未知操作直接拒絕。"""
    admin = await api.create_user(_name("Ops"), PW, "admin", actor_id=None)
    await api.record_ops_action(actor_id=admin.id, action="restart", service="web", environment="production",
                                result="scheduled", target="report-mark-web.service", invocation_id="abc123")
    await api.record_ops_action(actor_id=admin.id, action="run", service="sync", environment="production",
                                result="already_running")
    await _expect(ValueError, api.record_ops_action(actor_id=admin.id, action="stop", service="web",
                                                    environment="production", result="x"))
    _total, entries = await api.list_audit(limit=200)
    mine = [e for e in entries if e.actor_user_id == admin.id and e.target_type == "ops_service"]
    assert [(e.action, e.target_id, e.detail["result"]) for e in mine] == [
        ("ops.run", "sync", "already_running"), ("ops.restart", "web", "scheduled")], mine
    assert mine[1].detail == {"environment": "production", "service": "web", "target": "report-mark-web.service",
                              "result": "scheduled", "previous_invocation_id": "abc123"}, mine[1].detail
    assert mine[0].detail == {"environment": "production", "service": "sync", "target": None,
                              "result": "already_running"}, mine[0].detail


SCENARIOS = [
    scenario_login,
    scenario_duplicate_username_case_insensitive,
    scenario_invalid_input,
    scenario_session_lifecycle,
    scenario_expired_session,
    scenario_disable_is_immediate_and_revokes,
    scenario_role_change_takes_effect,
    scenario_self_lockout,
    scenario_reset_password,
    scenario_force_logout_and_audit,
    scenario_audit_never_contains_password,
    scenario_unknown_user_errors,
    scenario_scopes_and_super,
    scenario_super_protections,
    scenario_elevation,
    scenario_audit_chain,
    scenario_totp_login_two_steps,
    scenario_totp_fingerprint_follows_password,
    scenario_totp_setup_rules_and_disable,
    scenario_totp_elevation,
    scenario_totp_admin_reset_super_requires_super,
    scenario_deletion_request_and_cancel,
    scenario_deletion_cancel_keeps_disabled,
    scenario_deletion_protections,
    scenario_deletion_execute_purges,
    scenario_deletion_replay,
    scenario_ops_action_audit,
]


class FakeParityTests(unittest.TestCase):
    def test_scenarios(self):
        for scenario in [*SCENARIOS, scenario_last_admin, scenario_last_super, scenario_last_admin_cannot_be_deleted]:
            with self.subTest(scenario.__name__):
                asyncio.run(scenario(FakeAccounts()))


def _skip_or_raise(exc: Exception, why: str) -> None:
    if os.getenv("REPORT_MARK_REQUIRE_DB"):
        raise exc
    raise unittest.SkipTest(f"{why}：{exc}")


async def _in_rolled_back_transaction(fn):
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    from app.services.db import DATABASE_URL

    eng = create_async_engine(DATABASE_URL, echo=False, pool_pre_ping=True)
    try:
        async with eng.connect() as conn:
            outer = await conn.begin()
            orig = accounts.SessionFactory

            def factory():
                return AsyncSession(bind=conn, expire_on_commit=False, join_transaction_mode="create_savepoint")

            accounts.SessionFactory = factory
            try:
                return await fn()
            finally:
                accounts.SessionFactory = orig
                await outer.rollback()
    finally:
        await eng.dispose()


class AccountsDbTests(unittest.TestCase):
    def _run(self, scenario):
        async def go():
            await scenario(accounts)

        try:
            asyncio.run(_in_rolled_back_transaction(go))
        except (unittest.SkipTest, AssertionError, accounts.AccountError):
            raise
        except Exception as exc:
            msg = repr(exc)
            if any(k in msg for k in ("Connect", "connect", "refused", "app_user", "does not exist", "Timeout")):
                _skip_or_raise(exc, "DB 不可用或尚未套 schema")
            raise

    def test_scenarios(self):
        for scenario in SCENARIOS:
            with self.subTest(scenario.__name__):
                self._run(scenario)

    def test_last_admin_guard(self):
        async def go():
            if await accounts.count_enabled_admins() > 0:
                raise unittest.SkipTest("庫裡已有啟用中的管理員，最後一位管理員的情境驗不到（CI 空庫會跑）")
            await scenario_last_admin(accounts)

        try:
            asyncio.run(_in_rolled_back_transaction(go))
        except (unittest.SkipTest, AssertionError, accounts.AccountError):
            raise
        except Exception as exc:
            _skip_or_raise(exc, "DB 不可用或尚未套 schema")

    def test_last_admin_deletion_guard(self):
        async def go():
            if await accounts.count_enabled_admins() > 0:
                raise unittest.SkipTest("庫裡已有啟用中的管理員，刪除最後一位管理員的情境驗不到（CI 空庫會跑）")
            await scenario_last_admin_cannot_be_deleted(accounts)

        try:
            asyncio.run(_in_rolled_back_transaction(go))
        except (unittest.SkipTest, AssertionError, accounts.AccountError):
            raise
        except Exception as exc:
            _skip_or_raise(exc, "DB 不可用或尚未套 schema")

    def test_last_super_guard(self):
        async def go():
            from sqlalchemy import text

            async with accounts.SessionFactory() as session:
                supers = (await session.execute(text(
                    "SELECT count(*) FROM research.app_user WHERE role = 'admin' AND enabled AND is_super"
                ))).scalar_one()
            if supers > 0:
                raise unittest.SkipTest("庫裡已有啟用中的 super admin，最後一位 super 的情境驗不到（CI 空庫會跑）")
            await scenario_last_super(accounts)

        try:
            asyncio.run(_in_rolled_back_transaction(go))
        except (unittest.SkipTest, AssertionError, accounts.AccountError):
            raise
        except Exception as exc:
            _skip_or_raise(exc, "DB 不可用或尚未套 schema")

    def test_audit_log_is_append_only(self):
        """觸發器（revision 0002）擋 UPDATE／DELETE／TRUNCATE；在 savepoint 裡試，失敗就回滾那一段。"""
        async def go():
            from sqlalchemy import text

            await accounts.create_user(_name("Ao"), PW, "user", actor_id=None)
            async with accounts.SessionFactory() as session:
                for sql in ("UPDATE research.admin_audit_log SET action = 'x'",
                            "DELETE FROM research.admin_audit_log",
                            "TRUNCATE research.admin_audit_log"):
                    try:
                        async with session.begin_nested():
                            await session.execute(text(sql))
                    except Exception as exc:
                        assert "只能新增" in str(exc), exc
                        continue
                    raise AssertionError(f"稽核紀錄不該能 {sql}")

        try:
            asyncio.run(_in_rolled_back_transaction(go))
        except (unittest.SkipTest, AssertionError, accounts.AccountError):
            raise
        except Exception as exc:
            _skip_or_raise(exc, "DB 不可用或尚未套 schema")

    def test_nothing_leaks_out_of_the_transaction(self):
        name = _name("Leak")

        async def create():
            await accounts.create_user(name, PW, "user", actor_id=None)
            assert await accounts.find_user_by_username(name) is not None

        async def check():
            return await accounts.find_user_by_username(name)

        try:
            asyncio.run(_in_rolled_back_transaction(create))
            self.assertIsNone(asyncio.run(_in_rolled_back_transaction(check)))
        except (unittest.SkipTest, AssertionError):
            raise
        except Exception as exc:
            _skip_or_raise(exc, "DB 不可用或尚未套 schema")


if __name__ == "__main__":
    unittest.main()
