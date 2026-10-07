"""Security Operations 的 SQL 對真的 PostgreSQL 成立（`app/services/security_ops.py` ＋ revision 0009 的 auth_event）。

- 保留期清除：auth_event 只刪超過 365 天的列（364 天的留著，設定改小也一樣）；session 只刪結束超過 90 天的。
- 告警判斷：視窗內全站失敗（含被限流的彙總 count）、同一帳號「上次成功之後」的連續失敗、權限提升失敗。
- 事件清單的篩選與帳號名稱 join（已刪除帳號不顯示名稱）、可疑 IP 彙整、高風險時間線分類、TOTP 採用率。
- 管理員撤銷單一 session：稽核寫不進去時撤銷一起 rollback（同交易）。

跑在 CI 的「schema 契約」job；本機沒有 DB（或庫還沒套 revision 0009）就 skip。**一律 rollback、絕不 commit**——
本機預設連到的是生產庫。security_ops 吃 session_factory 參數，accounts 換掉模組的 SessionFactory，兩者都綁在
外層交易上、commit 只釋放 savepoint（同 tests/test_accounts_db.py）。
"""

from __future__ import annotations

import asyncio
import os
import unittest
import uuid
from types import SimpleNamespace
from unittest import mock

from sqlalchemy import text

from app.services import accounts, security_ops

SETTINGS = SimpleNamespace(
    security_window_minutes=15, security_login_failure_threshold=20, security_account_failure_threshold=5,
    security_elevate_failure_threshold=3, auth_event_retention_days=365, session_expired_retention_days=90,
)


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

            def factory():
                return AsyncSession(bind=conn, expire_on_commit=False, join_transaction_mode="create_savepoint")

            try:
                with mock.patch.object(accounts, "SessionFactory", factory):
                    return await fn(factory)
            finally:
                await outer.rollback()
    finally:
        await eng.dispose()


async def _user(factory, *, role="user", totp=False, deleted=False) -> str:
    uid = str(uuid.uuid4())
    async with factory() as s:
        await s.execute(
            text("INSERT INTO research.app_user (id, username, password_hash, role, enabled, totp_enabled, "
                 "totp_secret, deleted_at) VALUES (:id, :name, 'x', :role, NOT :deleted, :totp, "
                 "CASE WHEN :totp THEN 'JBSWY3DPEHPK3PXP' END, CASE WHEN :deleted THEN now() END)"),
            {"id": uid, "name": f"so_{uid[:12]}", "role": role, "totp": totp, "deleted": deleted},
        )
        await s.commit()
    return uid


async def _event(factory, event, *, age="0 minutes", user_id=None, ip=None, reason=None, count=1) -> None:
    async with factory() as s:
        await s.execute(
            text("INSERT INTO research.auth_event (occurred_at, event, reason, user_id, ip, count) "
                 "VALUES (now() - CAST(CAST(:age AS text) AS interval), :event, :reason, CAST(:uid AS uuid), "
                 ":ip, :count)"),
            {"age": age, "event": event, "reason": reason, "uid": user_id, "ip": ip, "count": count},
        )
        await s.commit()


async def _verify_ok():
    return accounts.AuditChainStatus(ok=True, total=0, head_id=None, head_hash=None)


class SecurityOpsDbTests(unittest.TestCase):
    def _run(self, fn):
        try:
            asyncio.run(_in_rolled_back_transaction(fn))
        except (unittest.SkipTest, AssertionError):
            raise
        except Exception as exc:
            msg = repr(exc)
            if any(k in msg for k in ("Connect", "connect", "refused", "does not exist", "Timeout")):
                _skip_or_raise(exc, "DB 不可用或尚未套 revision 0009")
            raise

    def test_purge_only_deletes_auth_events_older_than_365_days(self):
        async def go(factory):
            ip = f"10.250.{uuid.uuid4().int % 250}.1"
            for age in ("0 days", "364 days", "365 days - 1 minute", "366 days", "800 days"):
                await _event(factory, "login.failure", age=age, ip=ip, reason="unknown_user")
            # 設定改小（30 天）也不得刪到一年內的列
            res = await security_ops.purge_expired(
                settings=SimpleNamespace(auth_event_retention_days=30, session_expired_retention_days=90),
                session_factory=factory, batch=1,
            )
            self.assertEqual(res.auth_event_days, 365)
            self.assertGreaterEqual(res.auth_events, 2)
            async with factory() as s:
                left = (await s.execute(text(
                    "SELECT count(*), max(now() - occurred_at) FROM research.auth_event WHERE ip = :ip"
                ), {"ip": ip})).one()
            self.assertEqual(left[0], 3, "364 天、剛好不到 365 天、今天的三列都要留著")
            self.assertLess(left[1].days, 365)

        self._run(go)

    def test_purge_sessions_ended_more_than_90_days_ago(self):
        async def go(factory):
            uid = await _user(factory)
            cases = {
                "active": ("now() - interval '200 days'", "now() + interval '1 day'", "NULL"),
                "expired_89": ("now() - interval '120 days'", "now() - interval '89 days'", "NULL"),
                "expired_91": ("now() - interval '121 days'", "now() - interval '91 days'", "NULL"),
                "revoked_91": ("now() - interval '100 days'", "now() + interval '1 day'", "now() - interval '91 days'"),
                "revoked_10": ("now() - interval '20 days'", "now() + interval '1 day'", "now() - interval '10 days'"),
            }
            ids = {}
            async with factory() as s:
                for name, (created, expires, revoked) in cases.items():
                    ids[name] = (await s.execute(text(
                        "INSERT INTO research.user_session (user_id, created_at, last_seen_at, expires_at, revoked_at) "
                        f"VALUES (:uid, {created}, {created}, {expires}, {revoked}) RETURNING id"
                    ), {"uid": uid})).scalar_one()
                await s.commit()
            await security_ops.purge_expired(settings=SETTINGS, session_factory=factory)
            async with factory() as s:
                left = {str(r[0]) for r in (await s.execute(
                    text("SELECT id FROM research.user_session WHERE user_id = :uid"), {"uid": uid})).all()}
            self.assertEqual(left, {str(ids[k]) for k in ("active", "expired_89", "revoked_10")})

        self._run(go)

    def test_alert_counts_window_locked_aggregate_and_consecutive_failures(self):
        async def go(factory):
            base = await security_ops.evaluate_alerts(_verify_ok, session_factory=factory, settings=SETTINGS)
            victim, recovered = await _user(factory), await _user(factory)
            for _ in range(5):
                await _event(factory, "login.failure", age="2 minutes", user_id=victim, reason="bad_password")
            # recovered：四次失敗 → 成功 → 兩次失敗＝連續 2，不算
            for _ in range(4):
                await _event(factory, "login.failure", age="10 minutes", user_id=recovered, reason="bad_password")
            await _event(factory, "login.success", age="9 minutes", user_id=recovered, reason="password")
            for _ in range(2):
                await _event(factory, "login.totp_failure", age="1 minute", user_id=recovered, reason="bad_code")
            await _event(factory, "login.locked", age="1 minute", ip="10.9.9.9", count=40)
            await _event(factory, "login.failure", age="16 minutes", reason="unknown_user")  # 視窗外
            for _ in range(3):
                await _event(factory, "elevate.failure", age="3 minutes", user_id=victim, reason="bad_password")
            ev = await security_ops.evaluate_alerts(_verify_ok, session_factory=factory, settings=SETTINGS)
            self.assertEqual(ev.login_failures - base.login_failures, 5 + 4 + 2 + 40)
            self.assertIn(victim, ev.accounts_over)
            self.assertNotIn(recovered, ev.accounts_over)
            self.assertEqual(ev.elevate_failures - base.elevate_failures, 3)
            self.assertEqual(ev.triggered[:1], ("elevate_failures",))
            names = await security_ops.usernames([victim, "not-a-uuid"], session_factory=factory)
            self.assertEqual(set(names), {victim})

        self._run(go)

    def test_event_list_filters_and_username_join(self):
        async def go(factory):
            live, gone = await _user(factory), await _user(factory, deleted=True)
            ip = f"10.251.{uuid.uuid4().int % 250}.7"
            await _event(factory, "login.success", user_id=live, ip=ip, reason="password")
            await _event(factory, "login.failure", user_id=gone, ip=ip, reason="bad_password")
            await _event(factory, "login.failure", ip=ip, reason="unknown_user")
            rows = await security_ops.list_auth_events(ip=ip, session_factory=factory)
            self.assertEqual(len(rows), 3)
            self.assertEqual([r.id for r in rows], sorted((r.id for r in rows), reverse=True))
            by_user = {r.user_id: r for r in rows}
            self.assertTrue(by_user[live].username.startswith("so_"))
            self.assertIsNone(by_user[gone].username, "已刪除帳號不顯示名稱")
            self.assertIsNone(by_user[None].username)
            only = await security_ops.list_auth_events(ip=ip, event="login.failure", session_factory=factory)
            self.assertEqual(len(only), 2)
            mine = await security_ops.list_auth_events(user_id=live, session_factory=factory)
            self.assertEqual({r.user_id for r in mine}, {live})
            page = await security_ops.list_auth_events(ip=ip, limit=1, session_factory=factory)
            older = await security_ops.list_auth_events(ip=ip, before_id=page[0].id, session_factory=factory)
            self.assertEqual(len(older), 2)
            self.assertEqual(await security_ops.list_auth_events(user_id="x", session_factory=factory), [])

        self._run(go)

    def test_suspicious_ips(self):
        async def go(factory):
            bad, quiet = f"10.252.{uuid.uuid4().int % 250}.1", f"10.252.{uuid.uuid4().int % 250}.2"
            u1, u2 = await _user(factory), await _user(factory)
            await _event(factory, "login.failure", ip=bad, user_id=u1, reason="bad_password")
            await _event(factory, "login.failure", ip=bad, user_id=u2, reason="bad_password")
            await _event(factory, "login.locked", ip=bad, count=30)
            await _event(factory, "login.insecure", ip=bad, count=2)
            await _event(factory, "login.failure", ip=bad, age="30 hours", reason="unknown_user")  # 視窗外
            await _event(factory, "login.failure", ip=quiet, reason="unknown_user")
            rows = {r.ip: r for r in await security_ops.suspicious_ips(hours=24, min_failures=5,
                                                                        session_factory=factory)}
            self.assertIn(bad, rows)
            self.assertNotIn(quiet, rows)
            r = rows[bad]
            self.assertEqual((r.failures, r.locked, r.insecure, r.distinct_users), (2, 30, 2, 2))

        self._run(go)

    def test_high_risk_timeline_classification(self):
        async def go(factory):
            actor = await _user(factory, role="admin")
            tag = uuid.uuid4().hex[:8]
            async with factory() as s:
                for action in ("user.set_privileges", "ops.restart", "review.update", "flag.update", "opsx.none"):
                    await accounts._audit(s, actor_id=actor, action=action, target_type="t", target_id=tag)
                await s.commit()
            rows = [r for r in await security_ops.high_risk_timeline(days=1, session_factory=factory)
                    if r.target_id == tag]
            self.assertEqual({(r.action, r.category) for r in rows},
                             {("user.set_privileges", "privilege"), ("ops.restart", "ops"), ("flag.update", "config")})
            ops_only = [r for r in await security_ops.high_risk_timeline(days=1, category="ops",
                                                                         session_factory=factory)
                        if r.target_id == tag]
            self.assertEqual([r.action for r in ops_only], ["ops.restart"])

        self._run(go)

    def test_totp_adoption(self):
        async def go(factory):
            before = await security_ops.totp_adoption(session_factory=factory)
            a1 = await _user(factory, role="admin", totp=True)
            a2 = await _user(factory, role="admin")
            await _user(factory, totp=True)
            await _user(factory, role="admin", deleted=True)
            after = await security_ops.totp_adoption(session_factory=factory)
            self.assertEqual(after.users_total - before.users_total, 3)
            self.assertEqual(after.users_enabled - before.users_enabled, 2)
            self.assertEqual((after.admins_total - before.admins_total,
                              after.admins_enabled - before.admins_enabled), (2, 1))
            missing = {m[0] for m in after.admins_without_totp}
            self.assertIn(a2, missing)
            self.assertNotIn(a1, missing)

        self._run(go)

    def test_admin_revoke_session_rolls_back_when_audit_fails(self):
        async def go(factory):
            boss = await accounts.create_user(f"so_boss_{uuid.uuid4().hex[:8]}", "first-password-1", "admin",
                                              actor_id=None)
            target = await accounts.create_user(f"so_t_{uuid.uuid4().hex[:8]}", "first-password-1", "user",
                                                actor_id=None)
            sid = await accounts.create_session(target.id, max_age_seconds=3600)

            async def boom(*a, **kw):
                raise RuntimeError("audit down")

            with mock.patch.object(accounts, "_audit", boom):
                with self.assertRaises(RuntimeError):
                    await accounts.admin_revoke_session(sid, actor_id=boss.id)
            self.assertIsNotNone(await accounts.resolve_session(sid), "稽核失敗時撤銷不得生效")
            info = await accounts.admin_revoke_session(sid, actor_id=boss.id)
            self.assertFalse(info.active)
            _t, entries = await accounts.list_audit(limit=50)
            self.assertTrue(any(e.action == "session.admin_revoke" and e.target_id == sid for e in entries))

        self._run(go)


if __name__ == "__main__":
    unittest.main()
