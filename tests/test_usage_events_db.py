"""Admin v2 接線的 SQL 對真的 PostgreSQL 成立（revision 0011 ＋ usage_events.flush ＋ feature_flags 的讀取）。

- usage_daily：同一格兩次 flush 時 hits 相加、users 取 GREATEST（重啟後的下限語意）。
- usage_counter：已刪除帳號的計數在 upsert 時被濾掉（刪帳後不會復活）。
- llm_usage_daily：user_id NULL 的列靠 COALESCE 唯一索引併成一列（ON CONFLICT 推得到那支運算式索引）。
- feature_flag：text[]／uuid[] 讀得回來、registry 沒有的 key 被忽略。

跑在 CI 的「schema 契約」job；本機沒有 DB（或庫還沒套 revision 0011）就 skip。**一律 rollback、絕不 commit**——
本機預設連到的是測試環境的真實資料庫。
flush 與 feature_flags 都吃 session_factory 參數，這裡給綁在外層交易上、commit 只釋放
savepoint 的 session（同 tests/test_accounts_db.py）。
"""

from __future__ import annotations

import asyncio
import os
import unittest
import uuid
from datetime import date

from sqlalchemy import text

from app.services import feature_flags, usage_events

DAY = date(2026, 10, 7)


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
                return await fn(factory)
            finally:
                await outer.rollback()
    finally:
        await eng.dispose()


async def _user(factory, *, deleted: bool = False) -> str:
    uid = str(uuid.uuid4())
    async with factory() as s:
        await s.execute(
            text("INSERT INTO research.app_user (id, username, password_hash, role, enabled, deleted_at) "
                 "VALUES (:id, :name, 'x', 'user', :enabled, CASE WHEN :deleted THEN now() END)"),
            {"id": uid, "name": f"ue_{uid[:12]}", "enabled": not deleted, "deleted": deleted},
        )
        await s.commit()
    return uid


class UsageEventsDbTests(unittest.TestCase):
    def _run(self, fn):
        try:
            asyncio.run(_in_rolled_back_transaction(fn))
        except (unittest.SkipTest, AssertionError):
            raise
        except Exception as exc:
            msg = repr(exc)
            if any(k in msg for k in ("Connect", "connect", "refused", "does not exist", "Timeout")):
                _skip_or_raise(exc, "DB 不可用或尚未套 revision 0011")
            raise

    def test_flush_upserts_all_three_tables(self):
        async def go(factory):
            alive = await _user(factory)
            other = await _user(factory)
            gone = await _user(factory, deleted=True)
            subject = uuid.uuid4().hex * 2  # 隨機主題與任務名：不假設庫是空的
            task = f"t_{uuid.uuid4().hex[:12]}"
            acc = usage_events.UsageAccumulator()
            acc.record_hit("reading", subject, alive, day=DAY)
            acc.record_hit("reading", subject, other, day=DAY)
            acc.record_hit("reading", subject, gone, day=DAY)
            acc.record_llm({"task": task, "model": "deepseek-flash", "kind": None, "total_ms": 100,
                            "tokens": {"hit": 1, "miss": 2, "completion": 3, "reasoning": 0}}, alive, day=DAY)
            acc.record_llm({"task": task, "model": "deepseek-flash", "kind": "timeout", "total_ms": 50,
                            "tokens": None}, None, day=DAY)
            acc.record_llm({"task": task, "model": "deepseek-flash", "kind": None, "total_ms": 10,
                            "tokens": None}, gone, day=DAY)
            self.assertTrue(await usage_events.flush(acc, session_factory=factory))
            # 第二輪：同一格再一次（同一人）＋ NULL 使用者再一次
            acc.record_hit("reading", subject, alive, day=DAY)
            acc.record_llm({"task": task, "model": "deepseek-flash", "kind": None, "total_ms": 7,
                            "tokens": None}, None, day=DAY)
            self.assertTrue(await usage_events.flush(acc, session_factory=factory))

            async with factory() as s:
                daily = {(r[0], r[1]): (r[2], r[3]) for r in (await s.execute(text(
                    "SELECT kind, subject, hits, users FROM research.usage_daily WHERE day = :d AND kind = 'reading' "
                    "AND subject IN ('', :s)"), {"d": DAY, "s": subject})).all()}
                counters = {str(r[0]): r[1] for r in (await s.execute(text(
                    "SELECT user_id, count FROM research.usage_counter WHERE day = :d AND kind = 'reading' "
                    "AND user_id = ANY(CAST(:ids AS uuid[]))"), {"d": DAY, "ids": [alive, other, gone]})).all()}
                llm = (await s.execute(text(
                    "SELECT user_id, calls, failures, prompt_miss_tokens, calls_without_tokens, total_ms "
                    "FROM research.llm_usage_daily WHERE day = :d AND task = :task"),
                    {"d": DAY, "task": task})).all()
            # 主題格子：4 次、3 人（集合在行程內持續；第二輪 users 仍是 3，GREATEST 不倒退）
            self.assertEqual(daily[("reading", subject)], (4, 3))
            # 個人計數：已刪除帳號被濾掉
            self.assertEqual(counters, {alive: 2, other: 1})
            by_user = {str(r[0]) if r[0] else None: r[1:] for r in llm}
            self.assertEqual(by_user[alive], (1, 0, 2, 0, 100))
            self.assertNotIn(gone, by_user)
            self.assertEqual(by_user[None], (2, 1, 0, 2, 57))  # NULL 使用者兩輪併成同一列
            self.assertEqual(len(llm), 2)

        self._run(go)

    def test_usage_daily_users_is_a_lower_bound_across_restarts(self):
        async def go(factory):
            a, b = await _user(factory), await _user(factory)
            subject = uuid.uuid4().hex * 2
            first = usage_events.UsageAccumulator()
            first.record_hit("reading", subject, a, day=DAY)
            first.record_hit("reading", subject, b, day=DAY)
            await usage_events.flush(first, session_factory=factory)
            restarted = usage_events.UsageAccumulator()  # web 重啟：集合歸零
            restarted.record_hit("reading", subject, a, day=DAY)
            await usage_events.flush(restarted, session_factory=factory)
            async with factory() as s:
                hits, users = (await s.execute(text(
                    "SELECT hits, users FROM research.usage_daily "
                    "WHERE day = :d AND kind = 'reading' AND subject = :s"),
                    {"d": DAY, "s": subject})).one()
            self.assertEqual((hits, users), (3, 2))

        self._run(go)

    def test_feature_flag_rows_are_read_back(self):
        async def go(factory):
            uid = str(uuid.uuid4())
            async with factory() as s:
                await s.execute(text(
                    "INSERT INTO research.feature_flag (key, enabled, allow_roles, allow_users) VALUES "
                    "('ask.rerank', true, ARRAY['admin'], CAST(:users AS uuid[])), "
                    "('not.registered', true, NULL, NULL) "
                    "ON CONFLICT (key) DO UPDATE SET enabled = EXCLUDED.enabled, allow_roles = EXCLUDED.allow_roles, "
                    "allow_users = EXCLUDED.allow_users"), {"users": [uid]})
                await s.commit()
            feature_flags.invalidate()
            try:
                data, ok = await feature_flags.overrides(session_factory=factory)
            finally:
                feature_flags.invalidate()
            self.assertTrue(ok)
            self.assertEqual(data["ask.rerank"], feature_flags.Override(True, frozenset({"admin"}), frozenset({uid})))
            self.assertNotIn("not.registered", data)

        self._run(go)


if __name__ == "__main__":
    unittest.main()
