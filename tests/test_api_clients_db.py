"""API 用戶端服務（app/services/api_clients.py）對真的 PostgreSQL 成立（DB 契約測試）。

驗：建立後金鑰 resolve 得到、停用立即失效、輪替後舊金鑰失效新金鑰可用、每日額度到上限＋1 被拒且換日重置、
刪除用戶端時授權範圍與用量 cascade、每個寫入動作同交易留稽核且 detail 不含原始金鑰或 key_hash。

**一律 rollback、絕不 commit**——本機預設連到的是生產庫。服務函式自己會 commit，所以比照
`tests/test_accounts_db.py` 把 `api_clients.SessionFactory` 換成綁在一條外層交易上、commit 只釋放
savepoint 的 session，最後整條交易 rollback。名稱一律帶隨機尾碼，不假設庫是空的。
連不上 DB 或尚未套 0010 就 skip；`REPORT_MARK_REQUIRE_DB=1`（CI）時改成失敗。
"""

from __future__ import annotations

import asyncio
import os
import unittest
import uuid
from datetime import date, timedelta

from sqlalchemy import text

from app.services import api_clients


def _name(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


async def _create(**overrides):
    kwargs = dict(actor_id=None, name=_name("client"), scopes=["search"], rate_limit_per_min=60, daily_quota=1000,
                  entitlements={"market": ["TW", "US"], "source": ["凱基"]}, note="測試")
    kwargs.update(overrides)
    return await api_clients.create_client(**kwargs)


async def _audit_rows(client_id: int) -> list[tuple[str, str]]:
    async with api_clients.SessionFactory() as session:
        return [tuple(r) for r in (await session.execute(
            text("SELECT action, detail::text FROM research.admin_audit_log "
                 "WHERE target_type = 'api_client' AND target_id = :id ORDER BY id"),
            {"id": str(client_id)},
        )).all()]


async def scenario_create_and_resolve(_api) -> None:
    client, raw = await _create()
    assert raw.startswith(f"rmk_{client.key_prefix}_"), raw
    got = await api_clients.resolve_key(raw)
    assert got is not None and got.id == client.id, got
    assert got.scopes == frozenset({"search"})
    assert got.entitlements == {"market": ("TW", "US"), "source": ("凱基",)}, got.entitlements
    assert got.note == "測試" and got.enabled and got.key_rotated_at is None
    assert await api_clients.resolve_key(raw[:-1] + ("A" if raw[-1] != "A" else "B")) is None
    assert (await api_clients.get_client(client.id)).name == client.name
    assert client.id in {c.id for c in await api_clients.list_clients()}
    async with api_clients.SessionFactory() as session:
        stored = (await session.execute(
            text("SELECT key_hash FROM research.api_client WHERE id = :id"), {"id": client.id},
        )).scalar_one()
    assert stored == api_clients.hash_key(raw) and stored != raw


async def scenario_duplicate_name(_api) -> None:
    name = _name("dup")
    await _create(name=name)
    try:
        await _create(name=f"  {name} ")
    except api_clients.ApiClientError as exc:
        assert exc.code == "name_taken", exc.code
        return
    raise AssertionError("同名用戶端應被拒絕")


async def scenario_disable_is_immediate(_api) -> None:
    client, raw = await _create()
    after = await api_clients.update_client(actor_id=None, client_id=client.id, enabled=False)
    assert after.enabled is False
    assert await api_clients.resolve_key(raw) is None
    await api_clients.update_client(actor_id=None, client_id=client.id, enabled=True)
    assert (await api_clients.resolve_key(raw)) is not None


async def scenario_update_fields(_api) -> None:
    client, _raw = await _create()
    after = await api_clients.update_client(actor_id=None, client_id=client.id, scopes=["search", "report.file"],
                                            rate_limit_per_min=120, daily_quota=5, note="")
    assert after.scopes == frozenset({"search", "report.file"})
    assert (after.rate_limit_per_min, after.daily_quota, after.note) == (120, 5, None)
    assert after.updated_at >= client.updated_at
    try:
        await api_clients.update_client(actor_id=None, client_id=2**62, enabled=False)
    except api_clients.ApiClientError as exc:
        assert exc.code == "not_found"
    else:
        raise AssertionError("不存在的用戶端應回 not_found")


async def scenario_replace_entitlements(_api) -> None:
    client, raw = await _create()
    after = await api_clients.replace_entitlements(actor_id=None, client_id=client.id, entitlements={
        "market": ["HK"], "source": [], "report_type": ["產業"], "instrument_type": ["股票", "ETF"],
    })
    assert after.entitlements == {"market": ("HK",), "report_type": ("產業",), "instrument_type": ("ETF", "股票")}
    assert (await api_clients.resolve_key(raw)).entitlements == after.entitlements


async def scenario_missing_market_fails_closed(_api) -> None:
    client, raw = await _create()
    async with api_clients.SessionFactory() as session:
        await session.execute(
            text("DELETE FROM research.api_client_entitlement WHERE client_id = :id AND dimension = 'market'"),
            {"id": client.id},
        )
        await session.commit()
    assert await api_clients.resolve_key(raw) is None


async def scenario_rotate(_api) -> None:
    client, old = await _create()
    rotated, new = await api_clients.rotate_key(actor_id=None, client_id=client.id)
    assert new != old and rotated.key_prefix != client.key_prefix
    assert rotated.key_rotated_at is not None
    assert await api_clients.resolve_key(old) is None
    got = await api_clients.resolve_key(new)
    assert got is not None and got.id == client.id


async def scenario_quota(_api) -> None:
    client, _raw = await _create(daily_quota=3)
    day = date(2001, 1, 1)  # 固定的過去日期：不會與下面「台北時間的今天」撞在同一列
    for n in (1, 2, 3):
        assert await api_clients.consume_quota(client.id, today=day) == (True, n)
    assert await api_clients.consume_quota(client.id, today=day) == (False, 4)
    assert await api_clients.consume_quota(client.id, today=day + timedelta(days=1)) == (True, 1)
    assert (await api_clients.get_client(client.id)).last_used_at is not None
    assert await api_clients.consume_quota(2**62, today=day) == (False, 0)
    # 不帶 today：以台北時間的今天計，與指定日期的列互不干擾
    assert await api_clients.consume_quota(client.id) == (True, 1)


async def scenario_cascade(_api) -> None:
    client, _raw = await _create()
    await api_clients.consume_quota(client.id, today=date(2026, 10, 7))
    async with api_clients.SessionFactory() as session:
        await session.execute(text("DELETE FROM research.api_client WHERE id = :id"), {"id": client.id})
        counts = (await session.execute(
            text("SELECT (SELECT count(*) FROM research.api_client_entitlement WHERE client_id = :id), "
                 "(SELECT count(*) FROM research.api_client_usage WHERE client_id = :id)"),
            {"id": client.id},
        )).one()
        await session.commit()
    assert tuple(counts) == (0, 0), counts
    assert await api_clients.get_client(client.id) is None


async def scenario_audit_without_secrets(_api) -> None:
    client, raw = await _create()
    await api_clients.update_client(actor_id=None, client_id=client.id, daily_quota=10)
    await api_clients.replace_entitlements(actor_id=None, client_id=client.id, entitlements={"market": ["CN"]})
    _rotated, raw2 = await api_clients.rotate_key(actor_id=None, client_id=client.id)
    rows = await _audit_rows(client.id)
    assert [a for a, _d in rows] == ["api_client.create", "api_client.update", "api_client.entitlements",
                                     "api_client.rotate"], rows
    secrets = {raw, raw2, api_clients.hash_key(raw), api_clients.hash_key(raw2)}
    for secret in secrets:
        for secret_part in (secret, secret.split("_")[-1]):
            assert all(secret_part not in d for _a, d in rows), "稽核 detail 不得含原始金鑰或 key_hash"
    assert client.key_prefix in rows[0][1] and client.name in rows[0][1]
    # 值沒變的更新不寫、不記稽核
    await api_clients.update_client(actor_id=None, client_id=client.id, daily_quota=10)
    assert len(await _audit_rows(client.id)) == 4


SCENARIOS = [
    scenario_create_and_resolve, scenario_duplicate_name, scenario_disable_is_immediate, scenario_update_fields,
    scenario_replace_entitlements, scenario_missing_market_fails_closed, scenario_rotate, scenario_quota,
    scenario_cascade, scenario_audit_without_secrets,
]


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
            orig = api_clients.SessionFactory

            def factory():
                return AsyncSession(bind=conn, expire_on_commit=False, join_transaction_mode="create_savepoint")

            api_clients.SessionFactory = factory
            try:
                return await fn()
            finally:
                api_clients.SessionFactory = orig
                await outer.rollback()
    finally:
        await eng.dispose()


async def _probe(_api) -> None:
    async with api_clients.SessionFactory() as session:
        await session.execute(text("SELECT 1 FROM research.api_client_usage LIMIT 0"))


class ApiClientsDbTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 先探一次：連不上時每個情境各等一次連線逾時，十個情境就是十倍的等待。
        cls()._run(_probe)

    def _run(self, scenario):
        async def go():
            await scenario(api_clients)

        try:
            asyncio.run(_in_rolled_back_transaction(go))
        except (unittest.SkipTest, AssertionError, api_clients.ApiClientError):
            raise
        except Exception as exc:
            msg = repr(exc)
            if any(k in msg for k in ("Connect", "connect", "refused", "api_client", "does not exist", "Timeout")):
                _skip_or_raise(exc, "DB 不可用或尚未套 schema（revision 0010）")
            raise

    def test_scenarios(self):
        for scenario in SCENARIOS:
            with self.subTest(scenario.__name__):
                self._run(scenario)


if __name__ == "__main__":
    unittest.main()
