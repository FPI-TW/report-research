"""問答紀錄的每人隔離對真的 PostgreSQL 成立（服務層 SQL＋HTTP 端點，同一個 event loop）。

`tests/test_qa_isolation.py` 用假物件驗的是路由的接線（帶了誰的 user_id、何時 404）；
`user_id IS NOT DISTINCT FROM`、`ON CONFLICT ... WHERE`、`COALESCE(conversation_id, id)`
分組加上擁有者條件之後還對不對，只有真的資料庫驗得到。

情境：alice、bob 各一串對話，外加一串擁有者為 NULL 的舊共用歷史。驗 alice 看不到、改不到、
刪不到另外兩串，`/api/ask`、`/api/ask/stop` 參照它們時在串流前就 404；免登入開發模式
（user_id=None）看得到的只有 NULL 那串。

跑在 CI 的「schema 契約」job；本機沒有 DB（或生產庫還沒套 user_id 欄位）就 skip。
**一律 rollback、絕不 commit**：服務層自己會 commit，所以把 `answer.SessionFactory` 與
`web.deps.SessionFactory` 換成綁在一條外層交易上、commit 只釋放 savepoint 的 session
（SQLAlchemy `join_transaction_mode="create_savepoint"`），最後整條交易 rollback。HTTP 走
`httpx.ASGITransport`：TestClient 會在另一個 event loop 跑 app，用不了這條連線。
"""

from __future__ import annotations

import asyncio
import os
import unittest
import uuid
from datetime import datetime, timedelta, timezone

import httpx
from fake_accounts import FakeAccounts, install
from sqlalchemy import text

from app.services import answer as ans
from web import auth, deps
from web.server import app


def _skip_or_raise(exc: Exception, why: str) -> None:
    if os.getenv("REPORT_MARK_REQUIRE_DB"):
        raise exc
    raise unittest.SkipTest(f"{why}：{exc}")


_INSERT = text(
    "INSERT INTO research.qa_log (id, question, answer, conversation_id, root_qa_id, active, "
    "feedback, request_id, user_id, created_at) "
    "VALUES (:id, :q, '答', :cid, :root, :active, :fb, :rid, :uid, :ts)"
)


async def _in_rolled_back_transaction(fn):
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    from app.services.db import DATABASE_URL

    eng = create_async_engine(DATABASE_URL, echo=False, pool_pre_ping=True)
    try:
        async with eng.connect() as conn:
            outer = await conn.begin()

            def factory():
                return AsyncSession(bind=conn, expire_on_commit=False, join_transaction_mode="create_savepoint")

            orig = (ans.SessionFactory, deps.SessionFactory)
            ans.SessionFactory = factory
            deps.SessionFactory = factory
            try:
                return await fn(factory)
            finally:
                ans.SessionFactory, deps.SessionFactory = orig
                await outer.rollback()
    finally:
        await eng.dispose()


class QaIsolationDbTests(unittest.TestCase):
    def setUp(self):
        auth._FAILS.clear()
        self.store = FakeAccounts()
        self.alice = self.store.add_user("alice", "alice-password")
        self.bob = self.store.add_user("bob", "bob-password")
        self._ctx = install(self.store)
        self._ctx.__enter__()
        # conftest 預設把擁有權檢查 stub 成「不是別人的」；這裡要驗真的那一份。
        self._orig_checks = (deps.conversation_is_foreign, deps.qa_is_foreign, deps.answer_question)
        deps.conversation_is_foreign = ans.conversation_is_foreign
        deps.qa_is_foreign = ans.qa_is_foreign

        async def _must_not_stream(*_a, **_k):
            raise AssertionError("參照別人的資料時不該開始作答")
            yield  # pragma: no cover

        deps.answer_question = _must_not_stream

    def tearDown(self):
        deps.conversation_is_foreign, deps.qa_is_foreign, deps.answer_question = self._orig_checks
        self._ctx.__exit__(None, None, None)
        auth._FAILS.clear()

    def _run(self, fn):
        try:
            return asyncio.run(_in_rolled_back_transaction(fn))
        except (unittest.SkipTest, AssertionError):
            raise
        except Exception as exc:
            msg = repr(exc)
            if "UndefinedTable" in msg or "UndefinedColumn" in msg or "does not exist" in msg:
                _skip_or_raise(exc, "既有庫尚未套用 schema（qa_log.user_id）")
            _skip_or_raise(exc, f"DB 不可用（{type(exc).__name__}）")

    def test_users_cannot_see_or_touch_each_others_history(self):
        alice, bob = self.alice, self.bob
        tag = "zziso" + uuid.uuid4().hex[:10]
        base = datetime(2031, 6, 1, tzinfo=timezone.utc)
        ca, cb, cn = (str(uuid.uuid4()) for _ in range(3))
        a1, a1v2, a2, b1, n1 = (str(uuid.uuid4()) for _ in range(5))
        bob_rid = str(uuid.uuid4())

        async def fn(factory):
            async with factory() as s:
                rows = [
                    # alice：第一輪有兩個版本（舊版 inactive），再一輪追問
                    dict(id=a1, q=f"{tag} 甲一", cid=ca, root=None, active=False, fb=None, rid=None, uid=alice,
                         ts=base),
                    dict(id=a1v2, q=f"{tag} 甲一", cid=ca, root=a1, active=True, fb=None, rid=None, uid=alice,
                         ts=base + timedelta(minutes=1)),
                    dict(id=a2, q=f"{tag} 甲二", cid=ca, root=None, active=True, fb=None, rid=None, uid=alice,
                         ts=base + timedelta(minutes=2)),
                    dict(id=b1, q=f"{tag} 乙一", cid=cb, root=None, active=True, fb="like", rid=bob_rid, uid=bob,
                         ts=base + timedelta(minutes=3)),
                    # 個別帳號上線前的共用歷史：擁有者 NULL
                    dict(id=n1, q=f"{tag} 舊共用", cid=cn, root=None, active=True, fb=None, rid=None, uid=None,
                         ts=base + timedelta(minutes=4)),
                ]
                for r in rows:
                    await s.execute(_INSERT, r)
                await s.commit()

            async def row(qa_id):
                async with factory() as s:
                    return (await s.execute(
                        text("SELECT active, feedback, user_id FROM research.qa_log WHERE id = :id"), {"id": qa_id},
                    )).first()

            out = {}
            # ── 服務層 ──
            convs = await ans.list_conversations(limit=50, q=tag, user_id=alice)
            out["alice_convs"] = [c["conversation_id"] for c in convs]
            dev = await ans.list_conversations(limit=50, q=tag, user_id=None)
            out["dev_convs"] = [c["conversation_id"] for c in dev]
            out["get_own"] = await ans.get_conversation(ca, user_id=alice)
            out["get_bob"] = await ans.get_conversation(cb, user_id=alice)
            out["get_null"] = await ans.get_conversation(cn, user_id=alice)
            out["get_null_dev"] = await ans.get_conversation(cn, user_id=None)
            out["versions_own"] = await ans.list_qa_versions(a1, user_id=alice)
            out["versions_bob"] = await ans.list_qa_versions(b1, user_id=alice)
            out["turns_own"] = await ans.load_recent_turns(ca, user_id=alice)
            out["turns_bob"] = await ans.load_recent_turns(cb, user_id=alice)
            out["fb_bob"] = await ans.record_feedback(b1, "dislike", user_id=alice)
            out["fb_own"] = await ans.record_feedback(a2, "dislike", user_id=alice)
            out["del_conv_bob"] = await ans.delete_conversation(cb, user_id=alice)
            out["del_qa_bob"] = await ans.delete_qa(b1, user_id=alice)
            out["del_qa_null"] = await ans.delete_qa(n1, user_id=alice)
            out["foreign"] = {
                "qa_bob": await ans.qa_is_foreign(b1, user_id=alice),
                "qa_null": await ans.qa_is_foreign(n1, user_id=alice),
                "qa_own": await ans.qa_is_foreign(a1, user_id=alice),
                "qa_missing": await ans.qa_is_foreign(str(uuid.uuid4()), user_id=alice),
                "qa_null_dev": await ans.qa_is_foreign(n1, user_id=None),
                "conv_bob": await ans.conversation_is_foreign(cb, user_id=alice),
                "conv_null": await ans.conversation_is_foreign(cn, user_id=alice),
                "conv_own": await ans.conversation_is_foreign(ca, user_id=alice),
                "conv_missing": await ans.conversation_is_foreign(str(uuid.uuid4()), user_id=alice),
            }
            # 撞上 bob 的 request_id：不收斂到 bob 那一列、也不把它的 id 回給 alice
            out["rid_clash"] = await ans._log_qa(
                "q", "a", [], {}, 1, [], conversation_id=ca, request_id=bob_rid, user_id=alice,
            )
            # 第二道防線：就算停用／截斷指到 bob 的列，也動不到它
            out["mutate_bob"] = await ans._log_qa(
                "q", "a", [], {}, 1, [], conversation_id=ca, deactivate_qa_id=b1,
                truncate_from=(cb, base), user_id=alice,
            )
            out["stop_regen_bob"] = await ans.log_stopped_qa("q", "部分", regenerate_of=b1, user_id=alice)
            out["b1_after"] = await row(b1)
            out["n1_after"] = await row(n1)
            out["a2_after"] = await row(a2)

            # ── HTTP（同一個 event loop）──
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as c:
                r = await c.post("/login", data={"username": "alice", "password": "alice-password"})
                assert r.status_code == 303, r.status_code
                history = (await c.get("/api/history", params={"limit": 200})).json()
                out["http_history"] = [h["id"] for h in history]
                out["http_conv_bob"] = (await c.get(f"/api/conversations/{cb}")).status_code
                out["http_conv_null"] = (await c.get(f"/api/conversations/{cn}")).status_code
                out["http_conv_own"] = (await c.get(f"/api/conversations/{ca}")).status_code
                out["http_versions_bob"] = (await c.get(f"/api/qa/{b1}/versions")).status_code
                out["http_fb_bob"] = (await c.post("/api/feedback", json={"qa_id": b1, "value": "dislike"})).json()
                out["http_del_bob"] = (await c.delete(f"/api/conversations/{cb}")).json()
                out["http_del_hist_bob"] = (await c.delete(f"/api/history/{b1}")).json()
                out["http_ask_conv_bob"] = (await c.post(
                    "/api/ask", json={"question": "續問", "conversation_id": cb})).status_code
                out["http_ask_regen_bob"] = (await c.post(
                    "/api/ask", json={"question": "重生", "regenerate_of": b1})).status_code
                out["http_stop_edit_null"] = (await c.post(
                    "/api/ask/stop", json={"question": "編輯", "edit_of": n1})).status_code
                stop = await c.post("/api/ask/stop", json={"question": "停", "conversation_id": ca})
                out["http_stop_own"] = stop.status_code
                out["stop_row"] = await row(stop.json()["qa_id"]) if stop.status_code == 200 else None
            out["b1_final"] = await row(b1)
            return out

        out = self._run(fn)
        alice = self.alice
        self.assertEqual(out["alice_convs"], [ca])
        self.assertEqual(out["dev_convs"], [cn], "免登入開發模式（user_id=None）只看得到 NULL 共用歷史")
        self.assertEqual([t["id"] for t in out["get_own"]], [a1v2, a2])
        self.assertEqual(out["get_own"][0]["version_count"], 2)
        self.assertEqual((out["get_bob"], out["get_null"]), ([], []))
        self.assertEqual(len(out["get_null_dev"]), 1)
        self.assertEqual(len(out["versions_own"]), 2)
        self.assertEqual(out["versions_bob"], [])
        self.assertEqual(len(out["turns_own"]), 2)
        self.assertEqual(out["turns_bob"], [])
        self.assertEqual((out["fb_bob"], out["fb_own"]), (False, True))
        self.assertEqual((out["del_conv_bob"], out["del_qa_bob"], out["del_qa_null"]), (False, False, False))
        self.assertEqual(out["foreign"], {
            "qa_bob": True, "qa_null": True, "qa_own": False, "qa_missing": False, "qa_null_dev": False,
            "conv_bob": True, "conv_null": True, "conv_own": False, "conv_missing": False,
        })
        self.assertIsNone(out["rid_clash"])
        self.assertIsNotNone(out["mutate_bob"])
        self.assertIsNotNone(out["stop_regen_bob"])
        # bob 的列：仍 active、讚仍在、仍是 bob 的；NULL 列仍在
        self.assertEqual(tuple(out["b1_after"])[:2], (True, "like"))
        self.assertEqual(str(out["b1_after"][2]), self.bob)
        self.assertIsNotNone(out["n1_after"])
        self.assertEqual(out["a2_after"][1], "dislike")
        # HTTP
        self.assertNotIn(b1, out["http_history"])
        self.assertNotIn(n1, out["http_history"])
        self.assertIn(a2, out["http_history"])
        self.assertEqual((out["http_conv_bob"], out["http_conv_null"], out["http_conv_own"]), (404, 404, 200))
        self.assertEqual(out["http_versions_bob"], 404)
        self.assertEqual(out["http_fb_bob"], {"ok": False})
        self.assertEqual((out["http_del_bob"], out["http_del_hist_bob"]), ({"ok": False}, {"ok": False}))
        self.assertEqual((out["http_ask_conv_bob"], out["http_ask_regen_bob"]), (404, 404))
        self.assertEqual(out["http_stop_edit_null"], 404)
        self.assertEqual(out["http_stop_own"], 200)
        self.assertEqual(str(out["stop_row"][2]), alice, "停止列的擁有者是目前登入者")
        self.assertEqual(tuple(out["b1_final"])[:2], (True, "like"))


if __name__ == "__main__":
    unittest.main()
