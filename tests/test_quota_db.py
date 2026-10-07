"""每人配額的 SQL 對真的 PostgreSQL 成立（app/services/quota.py、upload_intake.check_quota 的個人覆寫）。

兩組測試：

- `QuotaDbTests`：**一律 rollback、絕不 commit**——本機預設連到的是生產庫。服務函式都吃 session_factory，這裡給綁在
  外層交易上、commit 只釋放 savepoint 的 session（同 tests/test_usage_events_db.py）。驗原子遞增在上限封頂、超額記
  `<kind>_over`、上限 0 連第一次都不插入、NULL＝不限、調低上限後立即生效；覆寫寫入與同交易的 `quota.update` 稽核
  （detail 不含理由全文與帳號名稱）、沒有變動不寫稽核、不限只有 super admin、super admin 的配額只有 super admin 能改；
  管理總覽、自己的用量、P50／P95 的 SQL；上傳的個人覆寫。
- `QuotaConcurrencyTests`：多個交易**真的並發**對同一人同一天遞增，只有恰好 `limit` 個拿得到名額。另一條連線看得到的
  資料必須先 commit，所以這組**只在拋棄式的庫上跑**：開始時語料表（`research_report`）與相關表（`report_upload`、
  `usage_counter`、`user_quota`）都必須是空的（剛 `alembic upgrade head` 的庫，例如 CI 的 schema job 或本機 devdb
  上的暫存庫）；不是空的就 skip（`REPORT_MARK_REQUIRE_DB=1` 時直接失敗）。自己 commit 的列在 finally 刪掉。

跑在 CI 的「schema 契約」job；本機沒有 DB（或庫還沒套 revision 0009）就 skip。
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import secrets
import time
import unittest
import uuid
from datetime import timedelta
from unittest import mock

from sqlalchemy import text

from app import config
from app.services import feature_flags, quota, upload_intake


def _skip_or_raise(exc: Exception, why: str) -> None:
    if os.getenv("REPORT_MARK_REQUIRE_DB"):
        raise exc
    raise unittest.SkipTest(f"{why}：{exc}")


def _engine(**kw):
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.services.db import DATABASE_URL

    return create_async_engine(DATABASE_URL, echo=False, pool_pre_ping=True, **kw)


async def _in_rolled_back_transaction(fn):
    from sqlalchemy.ext.asyncio import AsyncSession

    eng = _engine()
    try:
        async with eng.connect() as conn:
            outer = await conn.begin()

            def factory():
                return AsyncSession(bind=conn, expire_on_commit=False, join_transaction_mode="create_savepoint")

            try:
                return await fn(factory)
            finally:
                await outer.rollback()
    finally:
        await eng.dispose()


async def _user(factory, *, role="user", is_super=False, deleted=False) -> str:
    uid = str(uuid.uuid4())
    async with factory() as s:
        await s.execute(
            text("INSERT INTO research.app_user (id, username, password_hash, role, enabled, is_super, deleted_at) "
                 "VALUES (:id, :name, 'x', :role, :enabled, :sup, CASE WHEN :deleted THEN now() END)"),
            {"id": uid, "name": f"qt_{uid[:12]}", "role": role, "enabled": not deleted, "sup": is_super,
             "deleted": deleted},
        )
        await s.commit()
    return uid


async def _counts(factory, uid) -> dict[str, int]:
    async with factory() as s:
        rows = (await s.execute(text("SELECT kind, count FROM research.usage_counter WHERE user_id = :u"),
                                {"u": uid})).all()
    return {k: n for k, n in rows}


def _settings(**over):
    return mock.patch.object(config, "_SETTINGS", dataclasses.replace(config.get_settings(), **over))


def _shadow_flags():
    """旗標快取放「沒有覆寫」：超額時的旗標讀取不去另開連線（結論＝影子模式）。"""
    feature_flags._cache = (time.monotonic(), {}, True)


class _User:
    def __init__(self, uid, role="user"):
        self.id, self.role = uid, role
        self.is_admin = role == "admin"


class QuotaDbTests(unittest.TestCase):
    def setUp(self):
        p = _settings(quota_ask_daily=2, quota_export_daily=1, quota_enforce=False)
        p.start()
        self.addCleanup(p.stop)
        _shadow_flags()
        self.addCleanup(feature_flags.invalidate)

    def _run(self, fn):
        try:
            return asyncio.run(_in_rolled_back_transaction(fn))
        except (unittest.SkipTest, AssertionError):
            raise
        except Exception as exc:
            msg = repr(exc)
            if any(k in msg for k in ("Connect", "connect", "refused", "does not exist", "Timeout")):
                _skip_or_raise(exc, "DB 不可用或尚未套 revision 0009")
            raise

    def test_atomic_charge_caps_and_records_over(self):
        async def go(factory):
            uid = await _user(factory)
            ds = [await quota.charge(_User(uid), "ask", session_factory=factory) for _ in range(4)]
            self.assertEqual([d.count for d in ds], [1, 2, None, None])
            self.assertEqual([d.over for d in ds], [False, False, True, True])
            self.assertTrue(all(d.allowed for d in ds), "影子模式不擋人")
            self.assertEqual(await _counts(factory, uid), {"ask": 2, "ask_over": 2})

        self._run(go)

    def test_override_zero_null_and_lowered(self):
        async def go(factory):
            zero, unlimited, lowered = await _user(factory), await _user(factory), await _user(factory)
            async with factory() as s:
                await s.execute(text("INSERT INTO research.user_quota (user_id, kind, daily_limit) VALUES "
                                     "(:a, 'ask', 0), (:b, 'ask', NULL)"), {"a": zero, "b": unlimited})
                await s.commit()
            d = await quota.charge(_User(zero), "ask", session_factory=factory)
            self.assertEqual((d.over, d.limit), (True, 0))
            self.assertEqual(await _counts(factory, zero), {"ask_over": 1}, "上限 0：連第一次都不插入 ask 列")
            for _ in range(5):
                await quota.charge(_User(unlimited), "ask", session_factory=factory)
            self.assertEqual(await _counts(factory, unlimited), {"ask": 5})
            for _ in range(2):
                await quota.charge(_User(lowered), "ask", session_factory=factory)
            async with factory() as s:
                await s.execute(text("INSERT INTO research.user_quota (user_id, kind, daily_limit) "
                                     "VALUES (:a, 'ask', 1)"), {"a": lowered})
                await s.commit()
            self.assertTrue((await quota.charge(_User(lowered), "ask", session_factory=factory)).over)
            self.assertEqual(await _counts(factory, lowered), {"ask": 2, "ask_over": 1})

        self._run(go)

    def test_set_override_audits_in_same_transaction(self):
        async def go(factory):
            actor = await _user(factory, role="admin")
            supr = await _user(factory, role="admin", is_super=True)
            target = await _user(factory)

            async def audits():
                async with factory() as s:
                    return (await s.execute(text(
                        "SELECT action, target_type, detail FROM research.admin_audit_log "
                        "WHERE target_id = :t ORDER BY id"), {"t": target})).all()

            res = await quota.set_override(target, "ask", mode="limit", daily_limit=5, reason="試用期 秘密",
                                           actor_id=actor, session_factory=factory)
            self.assertTrue(res["changed"])
            item = res["item"]
            self.assertEqual((item["limit"], item["mode"], item["reason"]), (5, "limit", "試用期 秘密"))
            rows = await audits()
            self.assertEqual([(r[0], r[1]) for r in rows], [("quota.update", "user")])
            detail = rows[0][2] if isinstance(rows[0][2], dict) else json.loads(rows[0][2])
            self.assertEqual(detail, {"kind": "ask", "mode": "limit", "daily_limit": 5, "previous_mode": "default",
                                      "previous_daily_limit": None, "has_reason": True})
            self.assertNotIn("秘密", json.dumps(detail, ensure_ascii=False))
            self.assertNotIn("qt_", json.dumps(detail, ensure_ascii=False), "detail 不含帳號名稱")

            again = await quota.set_override(target, "ask", mode="limit", daily_limit=5, reason="試用期 秘密",
                                             actor_id=actor, session_factory=factory)
            self.assertFalse(again["changed"])
            self.assertEqual(len(await audits()), 1, "沒有變動不寫稽核")

            with self.assertRaises(quota.QuotaPermissionDenied):
                await quota.set_override(target, "ask", mode="unlimited", actor_id=actor, session_factory=factory)
            res = await quota.set_override(target, "ask", mode="unlimited", actor_id=supr, session_factory=factory)
            self.assertIsNone(res["item"]["limit"])
            async with factory() as s:
                row = (await s.execute(text("SELECT daily_limit, updated_by::text FROM research.user_quota "
                                            "WHERE user_id = :t AND kind = 'ask'"), {"t": target})).one()
            self.assertEqual(row, (None, supr))

            res = await quota.set_override(target, "ask", mode="default", actor_id=actor, session_factory=factory)
            self.assertEqual((res["item"]["mode"], res["item"]["limit"]), ("default", 2))
            async with factory() as s:
                left = (await s.execute(text("SELECT count(*) FROM research.user_quota WHERE user_id = :t"),
                                        {"t": target})).scalar_one()
            self.assertEqual(left, 0)
            self.assertEqual(len(await audits()), 3, "設定、改成不限、清除各一筆；被拒與沒變動的不寫")

            with self.assertRaises(quota.QuotaPermissionDenied):
                await quota.set_override(supr, "ask", mode="limit", daily_limit=1, actor_id=actor,
                                         session_factory=factory)
            gone = await _user(factory, deleted=True)
            with self.assertRaises(quota.QuotaTargetNotFound):
                await quota.set_override(gone, "ask", mode="limit", daily_limit=1, actor_id=supr,
                                         session_factory=factory)
            with self.assertRaises(quota.QuotaTargetNotFound):
                await quota.set_override(str(uuid.uuid4()), "ask", mode="limit", daily_limit=1, actor_id=supr,
                                         session_factory=factory)

        self._run(go)

    def test_overview_me_and_stats(self):
        async def go(factory):
            a, b = await _user(factory, role="admin"), await _user(factory)
            today = quota.taipei_day()
            async with factory() as s:
                await s.execute(text(
                    "INSERT INTO research.usage_counter (user_id, day, kind, count) VALUES "
                    "(:a, :d, 'ask', 2), (:a, :d, 'ask_over', 3), (:a, :d, 'export', 1), (:b, :d, 'ask', 1), "
                    "(:b, :y, 'ask', 2), (:a, :d, 'search', 9)"), {"a": a, "b": b, "d": today,
                                                                    "y": today - timedelta(days=1)})
                await s.execute(text(
                    "INSERT INTO research.report_upload (file_hash, original_name, size_bytes, uploaded_by, state) "
                    "VALUES (:h, 'x.pdf', 1, :a, 'quarantined')"), {"h": secrets.token_hex(32), "a": a})
                await s.execute(text("INSERT INTO research.user_quota (user_id, kind, daily_limit, reason) "
                                     "VALUES (:b, 'ask', 10, '研究需要')"), {"b": b})
                await s.execute(text(
                    "INSERT INTO research.llm_usage_daily (day, user_id, task, model, calls, prompt_hit_tokens, "
                    "prompt_miss_tokens, completion_tokens) "
                    "VALUES (:d, :a, 'ask_answer', 'deepseek-flash', 3, 10, 20, 5)"
                ), {"a": a, "d": today})
                await s.commit()

            overview = await quota.admin_overview(session_factory=factory)
            rows = {r["user_id"]: r for r in overview["users"]}
            items_a = {i["kind"]: i for i in rows[a]["items"]}
            self.assertEqual((items_a["ask"]["used"], items_a["ask"]["over"], items_a["ask"]["limit"]), (2, 3, 2))
            self.assertEqual((items_a["export"]["used"], items_a["upload"]["used"]), (1, 1))
            self.assertEqual(rows[a]["llm"], {"calls": 3, "failures": 0, "prompt_tokens": 30, "completion_tokens": 5})
            items_b = {i["kind"]: i for i in rows[b]["items"]}
            self.assertEqual((items_b["ask"]["mode"], items_b["ask"]["limit"], items_b["ask"]["reason"]),
                             ("limit", 10, "研究需要"))
            self.assertEqual(overview["enforcement"]["mode"], "shadow")
            self.assertGreaterEqual(overview["over_today"]["ask"], 3)

            me = await quota.me_usage(_User(b), session_factory=factory)
            self.assertEqual(me["items"], [{"kind": "ask", "used": 1, "over": 0, "limit": 10, "remaining": 9}])
            me_a = await quota.me_usage(_User(a, "admin"), session_factory=factory)
            self.assertEqual([i["kind"] for i in me_a["items"]], ["ask", "export", "upload"])

            stats = await quota.usage_stats(14, session_factory=factory)
            ask = next(k for k in stats["kinds"] if k["kind"] == "ask")
            self.assertGreaterEqual(ask["user_days"], 3)
            self.assertGreaterEqual(ask["max"], 5, "需求＝ask＋ask_over")
            self.assertGreaterEqual(ask["over_events"], 3)
            upload = next(k for k in stats["kinds"] if k["kind"] == "upload")
            self.assertGreaterEqual(upload["user_days"], 1)
            self.assertEqual(stats["days"], 14)

        self._run(go)

    def test_upload_check_quota_reads_override(self):
        async def go(factory):
            zero, unlimited = await _user(factory, role="admin"), await _user(factory, role="admin")
            async with factory() as s:
                await s.execute(text("INSERT INTO research.user_quota (user_id, kind, daily_limit) VALUES "
                                     "(:a, 'upload', 0), (:b, 'upload', NULL)"), {"a": zero, "b": unlimited})
                await s.commit()
            async with factory() as s:
                with self.assertRaises(upload_intake.QuotaExceededError) as cm:
                    await upload_intake.check_quota(s, actor_id=zero, daily_quota=30, max_in_flight=10**6)
                self.assertEqual((cm.exception.scope, cm.exception.limit), ("daily", 0))
                await upload_intake.check_quota(s, actor_id=unlimited, daily_quota=1, max_in_flight=10**6)
                # 沒有覆寫：沿用程式預設（0 份已上傳 < 1）
                await upload_intake.check_quota(s, actor_id=str(uuid.uuid4()), daily_quota=1, max_in_flight=10**6)

        self._run(go)


class QuotaConcurrencyTests(unittest.TestCase):
    """同一人同一天同時打 N 次、上限 L：恰好 L 次計入、N−L 次記 over。只在拋棄式（相關表皆空）的庫上跑。"""

    N, LIMIT = 24, 5

    @classmethod
    def setUpClass(cls):
        async def counts():
            eng = _engine()
            try:
                async with eng.connect() as conn:
                    return (await conn.execute(text(
                        "SELECT (SELECT count(*) FROM research.research_report), "
                        "(SELECT count(*) FROM research.report_upload), "
                        "(SELECT count(*) FROM research.usage_counter), (SELECT count(*) FROM research.user_quota)"
                    ))).one()
            finally:
                await eng.dispose()

        try:
            got = asyncio.run(counts())
        except Exception as exc:  # noqa: BLE001
            _skip_or_raise(exc, "DB 不可用或尚未套 revision 0009")
        if any(got):
            tables = "research_report／report_upload／usage_counter／user_quota"
            _skip_or_raise(RuntimeError(f"{tables}＝{tuple(got)} 不是空的"),
                           "並發測試要 commit，只在剛 upgrade head 的拋棄式庫上跑")

    def test_concurrent_charges_never_exceed_limit(self):
        from sqlalchemy.ext.asyncio import async_sessionmaker

        async def main():
            eng = _engine(pool_size=self.N, max_overflow=0)
            factory = async_sessionmaker(eng, expire_on_commit=False)
            uid = str(uuid.uuid4())
            try:
                async with factory() as s:
                    await s.execute(text("INSERT INTO research.app_user (id, username, password_hash, role) "
                                         "VALUES (:id, :n, 'x', 'user')"), {"id": uid, "n": f"qc_{uid[:12]}"})
                    await s.commit()
                # 先把連線都開好，讓 N 個 charge 盡量同時抵達 ON CONFLICT
                conns = [await eng.connect() for _ in range(self.N)]
                for c in conns:
                    await c.close()
                user = _User(uid)
                decisions = await asyncio.gather(
                    *[quota.charge(user, "ask", session_factory=factory) for _ in range(self.N)])
                async with factory() as s:
                    rows = dict((await s.execute(text(
                        "SELECT kind, count FROM research.usage_counter WHERE user_id = :u"), {"u": uid})).all())
                return decisions, rows
            finally:
                async with factory() as s:
                    await s.execute(text("DELETE FROM research.usage_counter WHERE user_id = :u"), {"u": uid})
                    await s.execute(text("DELETE FROM research.app_user WHERE id = :u"), {"u": uid})
                    await s.commit()
                await eng.dispose()

        with _settings(quota_ask_daily=self.LIMIT, quota_enforce=False):
            _shadow_flags()
            try:
                decisions, rows = asyncio.run(main())
            finally:
                feature_flags.invalidate()
        self.assertFalse(any(d.error for d in decisions), "不該有寫入失敗")
        counted = sorted(d.count for d in decisions if d.counted)
        self.assertEqual(counted, list(range(1, self.LIMIT + 1)), "每個名額恰好發一次")
        self.assertEqual(sum(d.over for d in decisions), self.N - self.LIMIT)
        self.assertEqual(rows, {"ask": self.LIMIT, "ask_over": self.N - self.LIMIT})
