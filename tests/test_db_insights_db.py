"""DB 快照、慢查詢偵測、趨勢快照與事件趨勢對真的 PostgreSQL 成立（app/services/db_insights.py，revision 0011）。

驗：
- 即時快照五段的系統目錄查詢在真庫上都能跑、型別正確、連線數對得上 max_connections；SET LOCAL 只活到交易結束。
- **權限較窄**：以 `SET LOCAL ROLE pg_signal_backend`（內建、幾乎沒有權限的角色；不建角色、不改任何目錄）模擬
  RDS 一般帳號——快照不拋例外，別人的 session 算進 `hidden`。真的 42501（讀 shared_preload_libraries）與真的
  57014（statement_timeout）在 SAVEPOINT 裡分類正確，之後同一個 session 照常可用。
- `pg_stat_statements`：庫裡沒有這個擴充時回 `extension_missing`（有的話只驗形狀）；絕不建立擴充。
- 快照：寫逐時（同一小時第二次不寫）→ 已結束的日子彙總成每日（重跑不再寫、內容不變）→ 逐時超過 30 天、每日超過
  400 天的列刪除、邊界內的保留。趨勢查詢在逐時與每日上都算得對。
- 事件趨勢：每週×元件×嚴重度件數（台北時間週一起）、MTTR／p50／p90 只算 resolved、lost 只計件數、常見 reason；
  批次失敗率（Result 非 success 才算失敗、分母是 finished）。

跑在 CI 的「schema 契約」job；本機沒有 DB（或庫還沒套 revision 0011）就 skip。**一律 rollback、絕不 commit**——
本機預設連到的是生產庫。時間一律放在 2001 年（`NOW`），保留期刪除與每日彙總的範圍都由 `now` 推得，碰不到庫裡
真的快照；事件與批次以隨機 host／unit 圈住，查詢區間也限在 2001 年。
"""

from __future__ import annotations

import asyncio
import os
import sys
import unittest
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import db_insights as di  # noqa: E402

UTC = timezone.utc
NOW = datetime(2001, 3, 10, 12, 30, tzinfo=UTC)  # 台北 2001-03-10 20:30


def _skip_or_raise(exc: Exception, why: str) -> None:
    if os.getenv("REPORT_MARK_REQUIRE_DB"):
        raise exc
    raise unittest.SkipTest(f"{why}：{exc}")


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
        if "db_stat_snapshot" in str(exc) and "does not exist" in str(exc):
            _skip_or_raise(exc, "庫還沒套 revision 0011")
        raise


async def _insert_hour(session, taken_at: datetime, stats: dict) -> None:
    import json

    await session.execute(text(
        "INSERT INTO research.db_stat_snapshot (taken_at, granularity, stats) VALUES (:t, 'hour', CAST(:s AS jsonb))"),
        {"t": taken_at, "s": json.dumps(stats)})


async def _rows(session, gran: str, lo: datetime, hi: datetime) -> list[tuple]:
    return [tuple(r) for r in (await session.execute(text(
        "SELECT taken_at, stats FROM research.db_stat_snapshot WHERE granularity = :g AND taken_at >= :lo "
        "AND taken_at < :hi ORDER BY taken_at"), {"g": gran, "lo": lo, "hi": hi})).all()]


def _stats(size: int, deadlocks: int, hit: int, read: int) -> dict:
    return {"v": 1, "gauges": {"db_size_bytes": size, "connections_total": 3},
            "counters": {"deadlocks": deadlocks, "blks_hit": hit, "blks_read": read},
            "tables": {"research.report_chunk": size // 2}, "errors": {}, "stats_reset": None}


class OverviewDbTests(unittest.TestCase):
    def test_overview_sections_on_real_catalog(self):
        async def body(session):
            ov = await di.collect_overview(session)
            st = (await session.execute(text(
                "SELECT setting FROM pg_settings WHERE name = 'statement_timeout'"))).scalar()
            return ov, st

        ov, st = _run(body)
        for name in di.SECTIONS:
            self.assertIsNone(ov[name]["error"], f"{name}: {ov[name]['error']}")
        self.assertGreater(ov["database"]["size_bytes"], 0)
        self.assertEqual(st, str(di.effective_timeout_ms()), "SET LOCAL 生效於這個交易（pg_settings 以 ms 回報）")
        tables = ov["tables"]
        self.assertGreater(tables["table_count"], 0)
        names = {(t["schema_name"], t["table"]) for t in tables["items"]}
        self.assertTrue(any(s == "research" for s, _ in names))
        for t in tables["items"]:
            self.assertGreaterEqual(t["total_bytes"], 0)
            if t["dead_ratio"] is not None:
                self.assertTrue(0.0 <= t["dead_ratio"] <= 1.0)
        c = ov["connections"]
        self.assertGreaterEqual(c["total"], 1, "至少有這條連線自己")
        self.assertEqual(c["usable_connections"], c["max_connections"] - c["reserved_connections"])
        self.assertEqual(c["total"], sum(c["by_state"].values()) + c["hidden"])
        self.assertIn("active", c["by_state"])
        a = ov["activity"]
        self.assertGreater(a["blks_hit"] + a["blks_read"], 0)
        u = ov["unused_indexes"]
        self.assertGreaterEqual(u["count"], len(u["items"]))
        self.assertIsNotNone(di.snapshot_stats(ov)["gauges"].get("db_size_bytes"))

    def test_set_local_does_not_leak_past_transaction(self):
        async def body(session):
            before = (await session.execute(text("SELECT current_setting('statement_timeout')"))).scalar()
            await session.rollback()
            await di.collect_overview(session)
            await session.rollback()
            after = (await session.execute(text("SELECT current_setting('statement_timeout')"))).scalar()
            return before, after

        before, after = _run(body)
        self.assertEqual(before, after)

    def test_unprivileged_role_degrades_without_raising(self):
        async def body(session):
            await session.execute(text("SET LOCAL ROLE pg_signal_backend"))
            ov = await di.collect_overview(session)
            slow = await di.slow_queries(session)
            who = (await session.execute(text("SELECT current_user"))).scalar()
            return ov, slow, who

        ov, slow, who = _run(body)
        self.assertEqual(who, "pg_signal_backend")
        c = ov["connections"]
        if c["error"] is None:
            # 這條連線的 session_user 不是 pg_signal_backend：自己這條也被當成別人的、state 看不到
            self.assertGreaterEqual(c["hidden"], 1)
            self.assertEqual(c["total"], sum(c["by_state"].values()) + c["hidden"])
        for name in di.SECTIONS:
            err = ov[name]["error"]
            self.assertTrue(err is None or err["code"] in di.ERROR_CODES, f"{name}: {err}")
        self.assertIn("available", slow)
        if not slow["available"]:
            self.assertIn(slow["reason"], di.SLOW_QUERY_REASONS)

    def test_real_permission_and_timeout_errors_are_classified_and_isolated(self):
        async def denied(session):
            await session.execute(text("SET LOCAL ROLE pg_signal_backend"))
            await session.execute(text("SELECT current_setting('shared_preload_libraries')"))
            return {}

        async def slow(session):
            await session.execute(text("SET LOCAL statement_timeout = 50"))
            await session.execute(text("SELECT pg_sleep(2)"))
            return {}

        async def body(session):
            a = await di._guarded(session, denied, 5000)
            b = await di._guarded(session, slow, 5000)
            still = (await session.execute(text("SELECT current_user, 1"))).one()
            return a, b, tuple(still)

        a, b, still = _run(body)
        self.assertEqual(a["error"]["code"], "permission_denied")
        self.assertEqual(b["error"]["code"], "timeout")
        self.assertEqual(still[1], 1, "SAVEPOINT 回滾後同一個交易照常可用")
        self.assertNotEqual(still[0], "pg_signal_backend", "SAVEPOINT 裡的 SET LOCAL ROLE 也一起回滾")

    def test_slow_queries_never_creates_extension(self):
        async def body(session):
            had = (await session.execute(text(
                "SELECT count(*) FROM pg_extension WHERE extname = 'pg_stat_statements'"))).scalar()
            out = await di.slow_queries(session, limit=5)
            has = (await session.execute(text(
                "SELECT count(*) FROM pg_extension WHERE extname = 'pg_stat_statements'"))).scalar()
            return had, out, has

        had, out, has = _run(body)
        self.assertEqual(had, has, "偵測不得建立擴充")
        if had == 0:
            self.assertEqual((out["available"], out["reason"]), (False, "extension_missing"))
        elif out["available"]:
            self.assertLessEqual(len(out["items"]), 5)
            for it in out["items"]:
                self.assertTrue(it["query"] is None or len(it["query"]) <= di.QUERY_TEXT_MAX_CHARS + 1)
        else:
            self.assertIn(out["reason"], di.SLOW_QUERY_REASONS)


class SnapshotDbTests(unittest.TestCase):
    def test_hourly_daily_rollup_purge_and_idempotence(self):
        hourly_days, daily_days = di.retention_days()
        d1, d2 = date(2001, 3, 8), date(2001, 3, 9)  # 已結束的兩天（台北）
        old_hour = NOW - timedelta(days=hourly_days, hours=1)
        kept_hour = NOW - timedelta(days=hourly_days) + timedelta(hours=1)
        old_day = NOW - timedelta(days=daily_days, hours=1)
        kept_day = NOW - timedelta(days=daily_days) + timedelta(hours=1)
        lo, hi = NOW - timedelta(days=daily_days + 10), NOW + timedelta(days=1)

        async def body(session):
            for i, d in enumerate((d1, d2)):
                base = di.day_start(d)
                for h, size in ((1, 100), (12, 300), (23, 200)):
                    await _insert_hour(session, base + timedelta(hours=h), _stats(size + i, 10 * (i + 1) + h,
                                                                                  1000 * h, 10 * h))
            await _insert_hour(session, old_hour, _stats(1, 0, 0, 0))
            await _insert_hour(session, kept_hour, _stats(2, 0, 0, 0))
            for ts in (old_day, kept_day):
                await session.execute(text(
                    "INSERT INTO research.db_stat_snapshot (taken_at, granularity, stats) "
                    "VALUES (:t, 'day', '{\"v\": 1}'::jsonb)"), {"t": ts})
            overview = {"generated_at": NOW.isoformat(),
                        "database": {"error": None, "size_bytes": 999},
                        "tables": {"error": {"code": "permission_denied", "message": "x"}},
                        "unused_indexes": {"error": None, "count": 0, "total_bytes": 0},
                        "connections": {"error": None, "total": 1, "by_state": {"active": 1}, "hidden": 0,
                                        "max_connections": 100},
                        "activity": {"error": None, "blks_hit": 1, "blks_read": 1, "deadlocks": 0}}
            first = await di.run_snapshot(session, now=NOW, overview=overview)
            hours1, days1 = await _rows(session, "hour", lo, hi), await _rows(session, "day", lo, hi)
            second = await di.run_snapshot(session, now=NOW + timedelta(minutes=20), overview=overview)
            hours2, days2 = await _rows(session, "hour", lo, hi), await _rows(session, "day", lo, hi)
            hourly_pts = await di.trend_points(session, metric="deadlocks",
                                               since=di.day_start(d1) + timedelta(hours=2),
                                               until=di.day_start(d2), granularity="hour")
            daily_pts = await di.trend_points(session, metric="db_size_bytes", since=di.day_start(d1),
                                              until=NOW, granularity="day")
            daily_rate = await di.trend_points(session, metric="deadlocks", since=di.day_start(d1),
                                               until=NOW, granularity="day")
            ratio_pts = await di.trend_points(session, metric="cache_hit_ratio", since=di.day_start(d1),
                                              until=di.day_start(d2), granularity="hour")
            return first, second, hours1, days1, hours2, days2, hourly_pts, daily_pts, daily_rate, ratio_pts

        (first, second, hours1, days1, hours2, days2, hourly_pts, daily_pts, daily_rate,
         ratio_pts) = _run(body)
        # 逐時：本小時寫入一列（整點），第二輪同一小時不寫
        self.assertTrue(first.inserted)
        self.assertFalse(second.inserted)
        self.assertEqual(first.errors, {"tables": "permission_denied"})
        this_hour = NOW.replace(minute=0)
        mine = [s for t, s in hours1 if t == this_hour]
        self.assertEqual(len(mine), 1)
        self.assertEqual(mine[0]["gauges"]["db_size_bytes"], 999)
        self.assertNotIn("dead_tuple_ratio", mine[0]["gauges"], "失敗的段落不留假值")
        # 每日：d1、d2 與 kept_hour 那天各一列（今天還沒結束不彙總）；重跑不再寫、內容不變
        self.assertEqual(first.rolled_up[-2:], [d1, d2])
        self.assertNotIn(NOW.astimezone(di.TZ).date(), first.rolled_up)
        self.assertEqual(second.rolled_up, [])
        self.assertEqual(days1, days2)
        self.assertEqual(hours1, hours2)
        by_day = {t: s for t, s in days1}
        d1_stats = by_day[di.day_start(d1)]
        self.assertEqual(d1_stats["samples"], 3)
        self.assertEqual(d1_stats["gauges"]["db_size_bytes"], {"avg": 200.0, "min": 100.0, "max": 300.0,
                                                               "last": 200.0})
        self.assertEqual(d1_stats["counters"]["deadlocks"], 33.0, "counter 取當日最後一個累計值")
        # 保留期：邊界外的刪除、邊界內的保留
        self.assertEqual((first.purged_hourly, first.purged_daily), (1, 1))
        hour_times = {t for t, _ in hours1}
        day_times = {t for t, _ in days1}
        self.assertNotIn(old_hour, hour_times)
        self.assertIn(kept_hour, hour_times)
        self.assertNotIn(old_day, day_times)
        self.assertIn(kept_day, day_times)
        # 趨勢：d1 的逐時 deadlocks 11→22→33（間隔 11 小時）；區間前的 01:00 那點只當基準、不輸出
        self.assertEqual([p["value"] for p in hourly_pts], [1.0, 1.0])
        self.assertEqual([p["value"] for p in daily_pts][:2], [200.0, 201.0])
        self.assertEqual(daily_pts[0]["min"], 100.0)
        # 每日 rate：d2 最後 43 − d1 最後 33 ＝ 一天 10
        self.assertEqual(daily_rate[1]["value"], 10.0)
        # 命中率：(12000−1000)/((12000−1000)+(120−10))
        self.assertAlmostEqual(ratio_pts[1]["value"], 11000 / (11000 + 110))


class IncidentTrendDbTests(unittest.TestCase):
    def test_incident_and_job_trends(self):
        host = f"lane-v2d-{uuid.uuid4().hex[:10]}"
        comp_a, comp_b = f"a{uuid.uuid4().hex[:8]}", f"b{uuid.uuid4().hex[:8]}"
        unit_ok, unit_bad = f"{host}-ok.service", f"{host}-bad.service"
        mon = datetime(2001, 3, 4, 16, tzinfo=UTC)  # 台北 2001-03-05（週一）00:00
        since, until = datetime(2001, 2, 1, tzinfo=UTC), datetime(2001, 3, 31, tzinfo=UTC)

        async def incident(session, n, comp, status, severity, reason, opened, minutes=None):
            resolved = opened + timedelta(minutes=minutes) if status == "resolved" else None
            await session.execute(text("""
                INSERT INTO research.incident (incident_id, host, component, kind, status, severity, reason,
                                               opened_at, last_event_at, resolved_at, event_count)
                VALUES (:id, :host, :comp, 'service', :status, :sev, :reason, :opened, :last, :resolved, 1)
            """), {"id": f"{host}:{comp}:{n}", "host": host, "comp": comp, "status": status, "sev": severity,
                   "reason": reason, "opened": opened, "last": resolved or opened, "resolved": resolved})

        async def job(session, unit, n, state, result, started):
            await session.execute(text("""
                INSERT INTO research.job_execution (host, unit, invocation_id, state, started_at, finished_at, result,
                                                    first_seen_at, last_seen_at)
                VALUES (:host, :unit, :inv, :state, :started, :finished, :result, :started, :started)
            """), {"host": host, "unit": unit, "inv": f"{n:032x}", "state": state, "started": started,
                   "finished": started + timedelta(minutes=1) if state == "finished" else None, "result": result})

        async def body(session):
            await incident(session, 1, comp_a, "resolved", "CRITICAL", "probe_exit_1", mon + timedelta(hours=1), 10)
            await incident(session, 2, comp_a, "resolved", "CRITICAL", "probe_exit_1", mon + timedelta(hours=2), 30)
            await incident(session, 3, comp_a, "lost", "WARNING", "slow", mon + timedelta(hours=3))
            await incident(session, 4, comp_b, "resolved", "WARNING", "probe_exit_1", mon - timedelta(hours=1), 50)
            await incident(session, 5, comp_b, "firing", "CRITICAL", "disk", mon + timedelta(days=1))
            # 區間外的不算
            await incident(session, 6, comp_b, "resolved", "CRITICAL", "probe_exit_1",
                           datetime(2000, 12, 1, tzinfo=UTC), 5)
            for i in range(4):
                await job(session, unit_ok, i, "finished", "success", mon + timedelta(hours=i))
            await job(session, unit_bad, 10, "finished", "exit-code", mon)
            await job(session, unit_bad, 11, "finished", "success", mon + timedelta(hours=1))
            await job(session, unit_bad, 12, "lost", None, mon + timedelta(hours=2))
            trends = await di.incident_trends(session, since=since, until=until)
            jobs = await di.job_failure_rates(session, since=since, until=until)
            return trends, jobs

        trends, jobs = _run(body)
        mine = {comp_a, comp_b}
        weeks = [w for w in trends["weeks"] if w["component"] in mine]
        self.assertEqual(
            sorted((w["week_start"], w["component"], w["severity"], w["total"], w["resolved"], w["lost"], w["firing"])
                   for w in weeks),
            sorted([("2001-03-05", comp_a, "CRITICAL", 2, 2, 0, 0), ("2001-03-05", comp_a, "WARNING", 1, 0, 1, 0),
                    ("2001-02-26", comp_b, "WARNING", 1, 1, 0, 0), ("2001-03-05", comp_b, "CRITICAL", 1, 0, 0, 1)]),
            "週以台北時間週一零時起算：台北週日 23:00 那筆落在前一週")
        comps = {c["component"]: c for c in trends["by_component"] if c["component"] in mine}
        a = comps[comp_a]
        self.assertEqual((a["total"], a["resolved"], a["lost"], a["firing"], a["critical"], a["warning"]),
                         (3, 2, 1, 0, 2, 1))
        self.assertAlmostEqual(a["mttr_seconds"], 20 * 60, msg="MTTR 只算 resolved（lost 不納入）")
        self.assertAlmostEqual(a["p50_seconds"], 20 * 60)
        self.assertAlmostEqual(a["p90_seconds"], 28 * 60)
        b = comps[comp_b]
        self.assertEqual((b["total"], b["firing"]), (2, 1))
        self.assertAlmostEqual(b["mttr_seconds"], 50 * 60)
        self.assertGreaterEqual(trends["summary"]["total"], 5)
        reasons = {r["reason"]: r for r in trends["top_reasons"]}
        self.assertGreaterEqual(reasons["probe_exit_1"]["total"], 3)
        self.assertTrue(mine <= set(reasons["probe_exit_1"]["components"]))
        by_unit = {j["unit"]: j for j in jobs}
        self.assertEqual((by_unit[unit_ok]["runs"], by_unit[unit_ok]["failed"], by_unit[unit_ok]["failure_rate"]),
                         (4, 0, 0.0))
        bad = by_unit[unit_bad]
        self.assertEqual((bad["runs"], bad["finished"], bad["failed"], bad["lost"]), (3, 2, 1, 1))
        self.assertEqual(bad["failure_rate"], 0.5, "分母是 finished，lost 不算進去")
        self.assertEqual(bad["last_failure_at"], mon.isoformat())
        self.assertLess([j["unit"] for j in jobs].index(unit_bad), [j["unit"] for j in jobs].index(unit_ok))


if __name__ == "__main__":
    unittest.main()
