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

from fake_accounts import FakeAccounts

from app.services import accounts


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
]


class FakeParityTests(unittest.TestCase):
    def test_scenarios(self):
        for scenario in [*SCENARIOS, scenario_last_admin, scenario_last_super]:
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
