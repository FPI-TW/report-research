"""監控觀測的保留與聚合對真的 PostgreSQL 成立（revision 0007 ＋ app/services/ops_rollup.py ＋ rollup_observations）。

驗：插入跨越 0–24 小時、2 天、10 天、100 天的觀測 → 跑一輪 → 24 小時內的原始觀測原封不動；2 天前的變成
5 分鐘桶、10 天前的變成 1 小時桶，count／min／max／avg／last 與狀態（first／last／變化次數）正確；100 天前的
（原始、5 分鐘、1 小時、job_execution）被清除。重跑一次三張表完全相同。遲到的觀測落在已有的桶時合併、不覆蓋。
聚合寫入失敗（trigger 拋例外）時原始觀測不被刪除。查詢端 5m／1h 把原始與較細的聚合一起重新分桶。
另一份持有 advisory lock 時批次 rc=75。

跑在 CI 的「schema 契約」job；本機沒有 DB（或庫還沒套 revision 0007）就 skip。**一律 rollback、
絕不 commit**——本機預設連到的是生產庫：`run_rollup` 的 commit 換成 no-op，而且以 `hosts=[隨機 host]`
圈住只處理自己塞的列，不假設庫是空的。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import unittest
import uuid
from argparse import Namespace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import ops_monitoring as om  # noqa: E402
from app.services import ops_rollup as orl  # noqa: E402

UTC = timezone.utc
NOW = datetime(2026, 10, 6, 12, 30, tzinfo=UTC)
CPU, STATE = "cpu_pct", "active_state"


def _skip_or_raise(exc: Exception, why: str) -> None:
    if os.getenv("REPORT_MARK_REQUIRE_DB"):
        raise exc
    raise unittest.SkipTest(f"{why}：{exc}")


async def _noop():
    return None


async def _with_session(fn):
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    from app.services.db import DATABASE_URL

    eng = create_async_engine(DATABASE_URL, echo=False, pool_pre_ping=True)
    try:
        async with AsyncSession(eng, expire_on_commit=False) as session:
            try:
                return await fn(session)
            finally:
                await session.rollback()
    finally:
        await eng.dispose()


def _run(fn):
    try:
        return asyncio.run(_with_session(fn))
    except unittest.SkipTest:
        raise
    except (OSError, ConnectionError) as exc:
        _skip_or_raise(exc, "連不上 PostgreSQL")
    except Exception as exc:  # noqa: BLE001
        if "service_observation_5m" in str(exc) or "service_observation_1h" in str(exc):
            _skip_or_raise(exc, "庫還沒套 revision 0007")
        raise


def t(day: int, hh: int, mm: int, month: int = 10) -> datetime:
    return datetime(2026, month, day, hh, mm, tzinfo=UTC)


class OpsRollupDbTests(unittest.TestCase):
    def setUp(self):
        self.host = f"lane-rollup-{uuid.uuid4().hex[:12]}"
        self.svc = f"{self.host}-web"

    # ── 資料 ──
    def _cpu(self, ts, value) -> dict:
        return {"observed_at": ts, "host": self.host, "scope": "host", "subject": "host", "metric": CPU,
                "value": float(value), "state": None, "detail": None}

    def _state(self, ts, state, detail=None) -> dict:
        return {"observed_at": ts, "host": self.host, "scope": "service", "subject": self.svc, "metric": STATE,
                "value": None, "state": state, "detail": json.dumps(detail) if detail else None}

    def _fixture(self) -> list[dict]:
        return [
            # 0–24 小時（含 24 小時又 10 分鐘：仍在「整點(now − 24h)」之後）→ 原封不動
            self._cpu(t(6, 11, 30), 5), self._cpu(t(5, 13, 30), 7), self._cpu(t(5, 12, 20), 9),
            self._state(t(6, 11, 30), "active", {"n": 0}),
            # 剛好早於界線 → 5 分鐘桶 11:55
            self._cpu(t(5, 11, 59), 4),
            # 2 天前 → 5 分鐘桶
            self._cpu(t(4, 12, 31), 1), self._cpu(t(4, 12, 32), 2), self._cpu(t(4, 12, 33), 6),
            self._cpu(t(4, 12, 36), 10),
            self._state(t(4, 12, 31), "active"), self._state(t(4, 12, 32), "failed"),
            self._state(t(4, 12, 33), "active", {"n": 3}),
            # 10 天前 → 1 小時桶
            self._cpu(t(26, 12, 31, 9), 2), self._cpu(t(26, 12, 32, 9), 4), self._cpu(t(26, 12, 47, 9), 12),
            self._cpu(t(26, 13, 5, 9), 8),
            self._state(t(26, 12, 31, 9), "active"), self._state(t(26, 12, 47, 9), "failed", {"n": 2}),
            # 88 天前（保留期內，遲到的舊資料一輪就一路搬到 1 小時桶）
            self._cpu(t(10, 13, 10, 7), 3),
            # 100 天前 → 清除
            self._cpu(t(28, 12, 0, 6), 3),
        ]

    async def _seed(self, session):
        await om.import_records(session, observations=self._fixture(), jobs=[])
        await session.execute(text(f"""
            INSERT INTO research.service_observation_5m ({orl._AGG_COLS})
            VALUES (:b, :h, 'host', 'host', 'cpu_pct', 1, 1, 1, 1, 1, 1, NULL, NULL, 0, NULL, :b, :b)
        """), {"b": t(28, 12, 0, 6), "h": self.host})
        await session.execute(text(f"""
            INSERT INTO research.service_observation_1h ({orl._AGG_COLS})
            VALUES (:b, :h, 'host', 'host', 'cpu_pct', 1, 1, 1, 1, 1, 1, NULL, NULL, 0, NULL, :b, :b)
        """), {"b": t(27, 12, 0, 6), "h": self.host})
        for inv, started in (("c" * 32, NOW - timedelta(days=100)), ("d" * 32, NOW - timedelta(days=10))):
            await session.execute(text("""
                INSERT INTO research.job_execution (host, unit, invocation_id, state, started_at, finished_at,
                    first_seen_at, last_seen_at)
                VALUES (:h, 'report-mark-sync.service', :inv, 'finished', :s, :s, :s, :s)
            """), {"h": self.host, "inv": inv, "s": started})

    async def _snapshot(self, session) -> dict:
        out = {}
        out["raw"] = [tuple(r) for r in await session.execute(text(
            "SELECT observed_at, scope, subject, metric, value, state, detail::text FROM research.service_observation "
            "WHERE host = :h ORDER BY observed_at, subject, metric"), {"h": self.host})]
        for name, table in orl.AGG_TABLES.items():
            out[name] = [dict(r) for r in (await session.execute(text(
                f"SELECT bucket_start, scope, subject, metric, sample_count, value_count, value_min, value_max, "
                f"value_avg, value_last, first_state, last_state, state_changes, last_detail::text AS last_detail, "
                f"first_at, last_at FROM {table} WHERE host = :h ORDER BY bucket_start, subject, metric"),
                {"h": self.host})).mappings()]
        out["jobs"] = [tuple(r) for r in await session.execute(text(
            "SELECT invocation_id, started_at FROM research.job_execution WHERE host = :h ORDER BY started_at"),
            {"h": self.host})]
        return out

    async def _rollup(self, session, **kw):
        return await orl.run_rollup(session, now=NOW, commit=_noop, hosts=[self.host], **kw)

    @staticmethod
    def _bucket(rows, start, metric):
        hits = [r for r in rows if r["bucket_start"] == start and r["metric"] == metric]
        assert len(hits) == 1, (start, metric, rows)
        return hits[0]

    # ── 測試 ──
    def test_rollup_assigns_granularity_values_and_purges(self):
        async def body(session):
            await self._seed(session)
            before = await self._snapshot(session)
            stats = await self._rollup(session)
            after = await self._snapshot(session)
            again = await self._rollup(session)
            return before, stats, after, again, await self._snapshot(session)

        before, stats, after, again, after2 = _run(body)
        cut = orl.cutoffs(NOW)
        self.assertEqual(cut, orl.Cutoffs(t(5, 12, 0), t(29, 12, 0, 9), t(8, 12, 0, 7)))

        # 24 小時內的原始觀測原封不動，其餘原始觀測都搬走了
        recent = [r for r in before["raw"] if r[0] >= cut.raw_before]
        self.assertEqual(len(recent), 4)
        self.assertEqual(after["raw"], recent)

        # 5 分鐘桶：只有 2 天前那一段與界線前一刻（沒有早於 7 天的）
        fine = after["5m"]
        self.assertEqual(sorted({(r["bucket_start"], r["metric"]) for r in fine}), [
            (t(4, 12, 30), STATE), (t(4, 12, 30), CPU), (t(4, 12, 35), CPU), (t(5, 11, 55), CPU)])
        b = self._bucket(fine, t(4, 12, 30), CPU)
        self.assertEqual((b["sample_count"], b["value_count"], b["value_min"], b["value_max"], b["value_last"]),
                         (3, 3, 1.0, 6.0, 6.0))
        self.assertAlmostEqual(b["value_avg"], 3.0)
        self.assertEqual((b["first_at"], b["last_at"]), (t(4, 12, 31), t(4, 12, 33)))
        self.assertEqual(self._bucket(fine, t(4, 12, 35), CPU)["value_avg"], 10.0)
        self.assertEqual(self._bucket(fine, t(5, 11, 55), CPU)["value_avg"], 4.0)
        s = self._bucket(fine, t(4, 12, 30), STATE)
        self.assertEqual((s["sample_count"], s["value_count"], s["value_avg"], s["first_state"], s["last_state"],
                          s["state_changes"], json.loads(s["last_detail"])),
                         (3, 0, None, "active", "active", 2, {"n": 3}))

        # 1 小時桶：10 天前那一段（由 5 分鐘桶再聚合）與 88 天前的遲到資料；100 天前的已清除
        coarse = after["1h"]
        self.assertEqual(sorted({(r["bucket_start"], r["metric"]) for r in coarse}), [
            (t(10, 13, 0, 7), CPU), (t(26, 12, 0, 9), STATE), (t(26, 12, 0, 9), CPU), (t(26, 13, 0, 9), CPU)])
        h = self._bucket(coarse, t(26, 12, 0, 9), CPU)
        self.assertEqual((h["sample_count"], h["value_min"], h["value_max"], h["value_last"]), (3, 2.0, 12.0, 12.0))
        self.assertAlmostEqual(h["value_avg"], 6.0)
        self.assertEqual(self._bucket(coarse, t(26, 13, 0, 9), CPU)["value_avg"], 8.0)
        hs = self._bucket(coarse, t(26, 12, 0, 9), STATE)
        self.assertEqual((hs["sample_count"], hs["first_state"], hs["last_state"], hs["state_changes"],
                          json.loads(hs["last_detail"])), (2, "active", "failed", 1, {"n": 2}),
                         "跨 5 分鐘桶的轉換在 1 小時桶裡算進去")
        self.assertTrue(all(r["bucket_start"] >= cut.purge_before for r in coarse + fine))

        # job_execution：100 天前的刪掉、10 天前的留著
        self.assertEqual([j[0] for j in after["jobs"]], ["d" * 32])
        self.assertEqual(stats.purged["research.service_observation"], 1)
        self.assertEqual(stats.purged["research.service_observation_5m"], 1)
        self.assertEqual(stats.purged["research.service_observation_1h"], 1)
        self.assertEqual(stats.purged["research.job_execution"], 1)

        # 冪等：重跑沒有東西可搬，三張表完全相同
        self.assertEqual(after2, after)
        self.assertEqual((again.raw_slices, again.fine_slices, sum(again.purged.values())), (0, 0, 0))

    def test_slice_budget_spreads_backlog_over_runs(self):
        async def body(session):
            await self._seed(session)
            runs = []
            while True:
                stats = await self._rollup(session, max_slices=2)
                runs.append(stats)
                if not stats.pending:
                    break
                self.assertLess(len(runs), 20)
            return runs, await self._snapshot(session)

        runs, snap = _run(body)
        self.assertTrue(runs[0].pending)
        self.assertEqual(sum(r.raw_slices + r.fine_slices for r in runs[:-1]), 2 * (len(runs) - 1))
        self.assertEqual(len(snap["raw"]), 4)
        self.assertEqual(len(snap["5m"]), 4)
        self.assertEqual(len(snap["1h"]), 4)

    def test_late_observation_merges_into_existing_bucket(self):
        async def body(session):
            await self._seed(session)
            await self._rollup(session)
            await om.import_records(session, observations=[self._cpu(t(4, 12, 34), 11),
                                                           self._state(t(4, 12, 34), "failed", {"n": 4})], jobs=[])
            await self._rollup(session)
            return await self._snapshot(session)

        snap = _run(body)
        b = self._bucket(snap["5m"], t(4, 12, 30), CPU)
        self.assertEqual((b["sample_count"], b["value_min"], b["value_max"], b["value_last"]), (4, 1.0, 11.0, 11.0))
        self.assertAlmostEqual(b["value_avg"], 5.0)
        self.assertEqual(b["last_at"], t(4, 12, 34))
        s = self._bucket(snap["5m"], t(4, 12, 30), STATE)
        self.assertEqual((s["sample_count"], s["last_state"], s["state_changes"], json.loads(s["last_detail"])),
                         (4, "failed", 3, {"n": 4}))

    def test_failed_aggregate_write_keeps_raw(self):
        async def body(session):
            await om.import_records(session, observations=[self._cpu(t(3, 10, 1), 1), self._cpu(t(3, 10, 2), 2)],
                                    jobs=[])
            await session.execute(text("""
                CREATE FUNCTION research.lane_p8_fail() RETURNS trigger LANGUAGE plpgsql
                AS $$ BEGIN RAISE EXCEPTION 'lane_p8 寫入失敗'; END $$
            """))
            await session.execute(text("""
                CREATE TRIGGER lane_p8_fail BEFORE INSERT ON research.service_observation_5m
                FOR EACH ROW EXECUTE FUNCTION research.lane_p8_fail()
            """))
            failed = None
            try:
                async with session.begin_nested():
                    await orl.move_slice(session, "raw", t(3, 10, 0), hosts=[self.host])
            except Exception as exc:  # noqa: BLE001
                failed = exc
            raw = (await session.execute(text(
                "SELECT count(*) FROM research.service_observation WHERE host = :h"), {"h": self.host})).scalar_one()
            fine = (await session.execute(text(
                "SELECT count(*) FROM research.service_observation_5m WHERE host = :h"),
                {"h": self.host})).scalar_one()
            return failed, raw, fine

        failed, raw, fine = _run(body)
        self.assertIsNotNone(failed)
        self.assertIn("lane_p8", str(failed))
        self.assertEqual((raw, fine), (2, 0), "寫入失敗整句回滾：原始觀測一筆都沒少")

    def test_query_rebins_raw_and_finer_aggregates(self):
        async def body(session):
            await self._seed(session)
            await self._rollup(session)
            five = await om.list_observations(session, since=t(4, 12, 0), until=NOW, subject="host", metric=CPU,
                                              limit=100, resolution="5m")
            hour = await om.list_observations(session, since=t(26, 0, 0, 9), until=NOW, subject="host",
                                              metric=CPU, limit=100, resolution="1h")
            state = await om.list_observations(session, since=t(4, 12, 0), until=NOW, subject=self.svc,
                                               limit=100, resolution="5m")
            return five, hour, state

        (trunc5, five), (trunc1, hour), (_, state) = _run(body)
        mine5 = [r for r in five if r["host"] == self.host]
        self.assertFalse(trunc5)
        self.assertEqual([r["observed_at"] for r in mine5],
                         [t(6, 11, 30), t(5, 13, 30), t(5, 12, 20), t(5, 11, 55), t(4, 12, 35), t(4, 12, 30)],
                         "新→舊；24 小時內的原始觀測也重新分到 5 分鐘桶")
        self.assertEqual((mine5[-1]["value"], mine5[-1]["value_min"], mine5[-1]["value_max"],
                          mine5[-1]["sample_count"]), (3.0, 1.0, 6.0, 3))
        self.assertEqual(mine5[0]["value"], 5.0)

        mine1 = {r["observed_at"]: r for r in hour if r["host"] == self.host}
        self.assertFalse(trunc1)
        self.assertEqual(sorted(mine1), [t(26, 12, 0, 9), t(26, 13, 0, 9), t(4, 12, 0), t(5, 11, 0), t(5, 12, 0),
                                         t(5, 13, 0), t(6, 11, 0)])
        b = mine1[t(4, 12, 0)]  # 由 5 分鐘桶即時再聚合
        self.assertEqual((b["sample_count"], b["value_min"], b["value_max"], b["value_last"]), (4, 1.0, 10.0, 10.0))
        self.assertAlmostEqual(b["value"], 4.75)
        self.assertAlmostEqual(mine1[t(26, 12, 0, 9)]["value"], 6.0)

        st = [r for r in state if r["host"] == self.host]
        self.assertEqual([(r["observed_at"], r["state"], r["first_state"], r["state_changes"]) for r in st],
                         [(t(6, 11, 30), "active", "active", 0), (t(4, 12, 30), "active", "active", 2)])
        self.assertEqual(st[-1]["detail"], {"n": 3})

    def test_second_runner_is_locked_out(self):
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from app.services.db import DATABASE_URL
        from scripts import rollup_observations as ro

        async def hold_lock():
            eng = create_async_engine(DATABASE_URL)
            try:
                async with eng.connect() as conn:
                    conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
                    got = (await conn.execute(text("SELECT pg_try_advisory_lock(:k)"),
                                              {"k": ro.LOCK_KEY})).scalar()
                    self.assertTrue(got)
                    try:
                        other = create_async_engine(DATABASE_URL)
                        # dry-run：萬一沒擋住也不會改資料（本機預設庫就是生產庫）
                        return await asyncio.to_thread(
                            ro.run, Namespace(dry_run=True, max_slices=1, batch_size=1), other,
                            async_sessionmaker(other))
                    finally:
                        await conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": ro.LOCK_KEY})
            finally:
                await eng.dispose()

        try:
            rc = asyncio.run(hold_lock())
        except (OSError, ConnectionError) as exc:
            _skip_or_raise(exc, "連不上 PostgreSQL")
        self.assertEqual(rc, ro.EXIT_LOCKED)


if __name__ == "__main__":
    unittest.main()
