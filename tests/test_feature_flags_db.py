"""功能旗標寫入、稽核與匯出匯入的 SQL 對真的 PostgreSQL 成立（Admin v2 Flags lane）。

- `set_override`：寫入與稽核 `flag.update` 同一筆交易（稽核失敗時旗標也不留）；相同內容不寫、不記；作用域的
  text[]／uuid[] 寫得進去也讀得回來；指定不存在或已刪除的使用者 422。
- `clear_override`：刪掉覆寫並記稽核；本來就沒有時不記。
- 稽核 detail：key、前後值、作用域摘要、via；使用者以帳號名稱表示；註記只記 `note_changed`、不含全文。
- `admin_view`：registry 沒登記的列只列在 `ignored_keys`。
- 匯出 → 匯入：使用者以帳號名稱往返；dry-run 不寫；未知 key、找不到的帳號擋下整份；套用時每個變更各一筆稽核。
- HTTP 一條龍：`PUT /api/admin/flags/{key}` 經真的 DB 寫入、`GET /api/admin/flags` 讀回。

跑在 CI 的「schema 契約」job；本機沒有 DB（或庫還沒套 revision 0009）就 skip。**一律 rollback、絕不 commit**——
本機預設連到的是生產庫。服務層函式都吃呼叫端的 session，這裡給綁在外層交易上、commit 只釋放 savepoint 的 session
（同 tests/test_usage_events_db.py）。
"""

from __future__ import annotations

import asyncio
import json
import os
import unittest
import uuid
from unittest import mock

from sqlalchemy import text

from app.services import feature_flags as ff


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


async def _user(factory, *, role: str = "user", deleted: bool = False, name: str | None = None) -> tuple[str, str]:
    uid = str(uuid.uuid4())
    username = name or f"ff_{uid[:12]}"
    async with factory() as s:
        await s.execute(
            text("INSERT INTO research.app_user (id, username, password_hash, role, enabled, deleted_at) "
                 "VALUES (:id, :name, 'x', :role, :enabled, CASE WHEN :deleted THEN now() END)"),
            {"id": uid, "name": username, "role": role, "enabled": not deleted, "deleted": deleted},
        )
        await s.commit()
    return uid, username


async def _audits(factory, key: str) -> list[dict]:
    async with factory() as s:
        rows = (await s.execute(
            text("SELECT actor_user_id::text, action, target_type, detail FROM research.admin_audit_log "
                 "WHERE target_type = 'feature_flag' AND target_id = :key ORDER BY id"),
            {"key": key},
        )).all()
    return [{"actor": a, "action": act, "target_type": t, "detail": d if isinstance(d, dict) else json.loads(d)}
            for a, act, t, d in rows]


async def _row(factory, key: str):
    async with factory() as s:
        return (await s.execute(
            text("SELECT enabled, allow_roles, allow_users::text[], note, updated_by::text "
                 "FROM research.feature_flag WHERE key = :key"), {"key": key},
        )).first()


async def _clear_table(factory) -> None:
    """外層交易裡先清空（本機若連到有覆寫的庫，測試仍從零開始；整段最後 rollback）。"""
    async with factory() as s:
        await s.execute(text("DELETE FROM research.feature_flag"))
        await s.commit()


class FeatureFlagsDbTests(unittest.TestCase):
    def _run(self, fn):
        async def wrapped(factory):
            await _clear_table(factory)
            return await fn(factory)

        try:
            asyncio.run(_in_rolled_back_transaction(wrapped))
        except (unittest.SkipTest, AssertionError):
            raise
        except Exception as exc:
            msg = repr(exc)
            if any(k in msg for k in ("Connect", "connect", "refused", "does not exist", "Timeout")):
                _skip_or_raise(exc, "DB 不可用或尚未套 revision 0009")
            raise

    def test_set_override_writes_row_and_audit_in_one_transaction(self):
        async def go(factory):
            actor, _ = await _user(factory, role="admin")
            alice, alice_name = await _user(factory)
            async with factory() as s:
                before, after, changed = await ff.set_override(
                    s, "qa.agentic", enabled=True, allow_roles=["admin"], allow_users=[alice.upper()],
                    note="只給管理員與 alice 試用", actor_id=actor)
                await s.commit()
            self.assertTrue(changed)
            self.assertIsNone(before)
            self.assertEqual((after.allow_roles, after.allow_users), (("admin",), (alice,)))
            enabled, roles, users, note, updated_by = await _row(factory, "qa.agentic")
            self.assertEqual((enabled, roles, users, note, updated_by),
                             (True, ["admin"], [alice], "只給管理員與 alice 試用", actor))
            audits = await _audits(factory, "qa.agentic")
            self.assertEqual(len(audits), 1)
            a = audits[0]
            self.assertEqual((a["actor"], a["action"], a["target_type"]), (actor, "flag.update", "feature_flag"))
            self.assertEqual(a["detail"]["before"], None)
            self.assertEqual(a["detail"]["after"], {"enabled": True, "allow_roles": ["admin"],
                                                    "allow_users": [alice_name]})
            self.assertEqual(a["detail"]["scope"], "限定開啟：角色 admin；指定使用者 1 位")
            self.assertEqual((a["detail"]["via"], a["detail"]["note_changed"]), ("api", True))
            self.assertNotIn("試用", json.dumps(a["detail"], ensure_ascii=False))  # 註記全文不進稽核
            self.assertNotIn(alice, json.dumps(a["detail"]))  # 使用者以帳號名稱表示

            # 讀取路徑（快取）讀得到剛寫的作用域
            ff.invalidate()
            data, ok = await ff.overrides(session_factory=factory)
            ff.invalidate()
            self.assertTrue(ok)
            self.assertEqual(data["qa.agentic"], ff.Override(True, frozenset({"admin"}), frozenset({alice})))

            # 同樣內容：不寫、不記
            async with factory() as s:
                _b, _a, changed = await ff.set_override(
                    s, "qa.agentic", enabled=True, allow_roles=["admin"], allow_users=[alice],
                    note="只給管理員與 alice 試用", actor_id=actor)
                await s.commit()
            self.assertFalse(changed)
            self.assertEqual(len(await _audits(factory, "qa.agentic")), 1)

            # 改成全站關：前後值都記下
            async with factory() as s:
                await ff.set_override(s, "qa.agentic", enabled=False, actor_id=actor)
                await s.commit()
            audits = await _audits(factory, "qa.agentic")
            self.assertEqual(len(audits), 2)
            self.assertEqual(audits[1]["detail"]["before"]["allow_users"], [alice_name])
            self.assertEqual(audits[1]["detail"]["after"], {"enabled": False, "allow_roles": None, "allow_users": None})
            self.assertEqual(audits[1]["detail"]["scope"], "關閉")

        self._run(go)

    def test_audit_failure_leaves_no_flag(self):
        async def go(factory):
            actor, _ = await _user(factory, role="admin")

            async def broken_audit(*a, **k):
                raise RuntimeError("稽核寫不進去")

            async with factory() as s:
                with mock.patch("app.services.accounts.record_audit", broken_audit):
                    with self.assertRaises(RuntimeError):
                        await ff.set_override(s, "ask.rerank", enabled=False, actor_id=actor)
                await s.rollback()
            self.assertIsNone(await _row(factory, "ask.rerank"))
            self.assertEqual(await _audits(factory, "ask.rerank"), [])

        self._run(go)

    def test_unknown_or_deleted_users_are_rejected(self):
        async def go(factory):
            actor, _ = await _user(factory, role="admin")
            gone, _ = await _user(factory, deleted=True)
            for users in ([str(uuid.uuid4())], [gone]):
                async with factory() as s:
                    with self.assertRaises(ff.InvalidFlagInputError):
                        await ff.set_override(s, "qa.agentic", enabled=True, allow_users=users, actor_id=actor)
                    await s.rollback()
            self.assertIsNone(await _row(factory, "qa.agentic"))

        self._run(go)

    def test_clear_override(self):
        async def go(factory):
            actor, _ = await _user(factory, role="admin")
            async with factory() as s:
                before, changed = await ff.clear_override(s, "trusted_data", actor_id=actor)
                await s.commit()
            self.assertEqual((before, changed), (None, False))
            self.assertEqual(await _audits(factory, "trusted_data"), [])
            async with factory() as s:
                await ff.set_override(s, "trusted_data", enabled=False, actor_id=actor)
                await s.commit()
            async with factory() as s:
                before, changed = await ff.clear_override(s, "trusted_data", actor_id=actor)
                await s.commit()
            self.assertTrue(changed)
            self.assertFalse(before.enabled)
            self.assertIsNone(await _row(factory, "trusted_data"))
            audits = await _audits(factory, "trusted_data")
            self.assertEqual([a["detail"]["scope"] for a in audits], ["關閉", "沒有覆寫（registry 預設）"])
            self.assertIsNone(audits[1]["detail"]["after"])
            with self.assertRaises(ff.UnknownFlagError):
                async with factory() as s:
                    await ff.clear_override(s, "no.such", actor_id=actor)

        self._run(go)

    def test_admin_view_lists_ignored_keys_and_usernames(self):
        async def go(factory):
            actor, actor_name = await _user(factory, role="admin")
            async with factory() as s:
                await s.execute(text("INSERT INTO research.feature_flag (key, enabled) VALUES ('legacy.flag', true)"))
                await ff.set_override(s, "ask.rerank", enabled=True, allow_roles=["admin"], actor_id=actor)
                await s.commit()
            async with factory() as s:
                view = await ff.admin_view(s)
            self.assertEqual(view.ignored_keys, ["legacy.flag"])
            self.assertEqual([f.spec.key for f in view.flags], list(ff.REGISTRY))
            rerank = next(f for f in view.flags if f.spec.key == "ask.rerank")
            self.assertEqual(rerank.override.updated_by, actor)
            self.assertEqual(view.usernames[actor], actor_name)
            self.assertEqual(rerank.effective, "scoped" if rerank.ceiling else "off")

        self._run(go)

    def test_export_import_roundtrip_by_username(self):
        async def go(factory):
            actor, _ = await _user(factory, role="admin")
            alice, alice_name = await _user(factory)
            async with factory() as s:
                await ff.set_override(s, "qa.agentic", enabled=True, allow_users=[alice], note="試用", actor_id=actor)
                await ff.set_override(s, "ask.rerank", enabled=False, actor_id=actor)
                await s.commit()
            async with factory() as s:
                doc = await ff.export_document(s)
            self.assertEqual((doc["format"], doc["format_version"], doc["registry_version"]),
                             (ff.EXPORT_FORMAT, 1, ff.REGISTRY_VERSION))
            self.assertEqual([f["key"] for f in doc["flags"]], list(ff.REGISTRY))
            by_key = {f["key"]: f["override"] for f in doc["flags"]}
            self.assertEqual(by_key["qa.agentic"]["allow_users"], [alice_name])
            self.assertIsNone(by_key["uploads.intake"])

            # 目標環境：先清空，模擬另一個庫；dry-run 不寫
            await _clear_table(factory)
            async with factory() as s:
                plan = await ff.plan_import(s, doc)
            self.assertEqual(plan.errors, [])
            self.assertTrue(plan.registry_version_match)
            actions = {c.key: c.action for c in plan.changes}
            self.assertEqual(actions["qa.agentic"], "create")
            self.assertEqual(actions["ask.rerank"], "create")
            self.assertEqual(actions["uploads.intake"], "unchanged")
            self.assertIsNone(await _row(factory, "qa.agentic"))

            # 再放一筆檔案裡是 null 的覆寫：套用時要刪掉
            async with factory() as s:
                await ff.set_override(s, "trusted_data", enabled=False, actor_id=actor)
                await s.commit()
            n_before = len(await _audits(factory, "qa.agentic"))
            async with factory() as s:
                plan = await ff.apply_import(s, doc, actor_id=actor)
                await s.commit()
            self.assertEqual(plan.errors, [])
            self.assertEqual({c.key: c.action for c in plan.changes}["trusted_data"], "delete")
            enabled, _roles, users, note, _by = await _row(factory, "qa.agentic")
            self.assertEqual((enabled, users, note), (True, [alice], "試用"))
            self.assertIsNone(await _row(factory, "trusted_data"))
            qa = await _audits(factory, "qa.agentic")
            self.assertEqual(len(qa) - n_before, 1)
            self.assertEqual(qa[-1]["detail"]["via"], "import")
            self.assertEqual((await _audits(factory, "trusted_data"))[-1]["detail"]["via"], "import")

            # 再匯入一次：全部 unchanged、不記稽核
            async with factory() as s:
                plan = await ff.apply_import(s, doc, actor_id=actor)
                await s.commit()
            self.assertEqual({c.action for c in plan.changes}, {"unchanged"})
            self.assertEqual(len(await _audits(factory, "qa.agentic")), len(qa))

        self._run(go)

    def test_import_errors_block_the_whole_document(self):
        async def go(factory):
            actor, _ = await _user(factory, role="admin")
            doc = {
                "format": ff.EXPORT_FORMAT, "format_version": 1, "registry_version": "deadbeef0000",
                "flags": [
                    {"key": "ask.rerank", "override": {"enabled": False}},
                    {"key": "brand.new", "override": {"enabled": True}},
                    {"key": "qa.agentic", "override": {"enabled": True, "allow_users": ["nobody_here"]}},
                    {"key": "trusted_data", "override": {"enabled": True, "allow_roles": ["root"]}},
                    {"key": "ask.rerank", "override": None},
                ],
            }
            async with factory() as s:
                plan = await ff.apply_import(s, doc, actor_id=actor)
                await s.rollback()
            self.assertFalse(plan.registry_version_match)
            self.assertEqual(sorted((e.key, e.code) for e in plan.errors), [
                ("ask.rerank", "duplicate_key"), ("brand.new", "unknown_key"),
                ("qa.agentic", "unknown_user"), ("trusted_data", "invalid_input"),
            ])
            self.assertIsNone(await _row(factory, "ask.rerank"))  # 有錯誤就一筆都不寫
            self.assertEqual(await _audits(factory, "ask.rerank"), [])

        self._run(go)

    def test_http_put_then_list_through_the_db(self):
        async def go(factory):
            import httpx
            from fake_accounts import FakeAccounts, install

            from web import deps
            from web.server import app

            store = FakeAccounts()
            op_id = store.add_user("flagop", "flag-op-password-1", "admin", scopes={"ops.operate"})
            # 稽核的 actor 是 fake 帳號的 UUID：真的庫裡也要有這一列（admin_audit_log 沒有 FK，只是讓名稱查得到）
            async with factory() as s:
                await s.execute(text("INSERT INTO research.app_user (id, username, password_hash, role) "
                                     "VALUES (:id, 'flagop', 'x', 'admin')"), {"id": op_id})
                await s.commit()

            # 同一個 event loop 裡打 ASGI（TestClient 會自己開 loop，綁在外層連線上的 session 不能跨 loop 用）
            transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 51000))
            with install(store), mock.patch.object(deps, "SessionFactory", factory):
                async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as c:
                    r = await c.post("/login", data={"username": "flagop", "password": "flag-op-password-1"})
                    self.assertEqual(r.status_code, 303)
                    r = await c.post("/api/me/elevate", json={"password": "flag-op-password-1"},
                                     headers={"Origin": "http://127.0.0.1"})
                    self.assertEqual(r.status_code, 200, r.text)
                    put = await c.put("/api/admin/flags/ask.rerank", json={"enabled": False, "note": "CPU 吃緊"},
                                      headers={"Origin": "http://127.0.0.1"})
                    listed = await c.get("/api/admin/flags")
            self.assertEqual(put.status_code, 200, put.text)
            self.assertEqual(put.json()["override"]["note"], "CPU 吃緊")
            self.assertEqual(put.json()["override"]["updated_by"], "flagop")
            item = next(i for i in listed.json()["items"] if i["key"] == "ask.rerank")
            self.assertFalse(item["override"]["enabled"])
            self.assertEqual(item["effective"], "off")
            self.assertEqual(len(await _audits(factory, "ask.rerank")), 1)

        self._run(go)


if __name__ == "__main__":
    unittest.main()
