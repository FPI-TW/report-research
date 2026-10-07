"""`app/services/db_insights.py` 的純函式與降級語意（不連 DB）。

系統目錄查詢對真 PostgreSQL 的驗證在 tests/test_db_insights_db.py。這裡釘：
- SQLSTATE 分類（42501→permission_denied、57014／55P03→timeout、其他 error），訊息是固定文字、不回原始例外。
- 即時快照：一段權限不足只讓那一段帶 `error`，其餘照常；全部失敗也不拋例外。
- `pg_stat_statements`：擴充沒建立（套件有／沒有）、沒預載（55000）、SELECT 被拒（42501）各回 `available=false`
  與對應 `reason`；**任何路徑都不送 CREATE EXTENSION、ALTER SYSTEM 或 SET（statement_timeout 以外）**。
  查詢文字壓空白後截斷 200 字、權限不足的別人語句回 `query=None`。
- 快照 stats：失敗的段落記在 errors、對應的 gauge 缺席；每日彙總的 avg／min／max／last 與「counter 取最後值」。
- 趨勢點：gauge 直取、每日取 avg 附 min／max；rate 依實際間隔換算成每小時、統計重設（差為負）回 None；
  cache_hit_ratio 用區間差值；區間前的基準點不輸出。
"""

from __future__ import annotations

import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import db_insights as di  # noqa: E402

UTC = timezone.utc


class _PgError(Exception):
    def __init__(self, sqlstate: str, msg: str = "boom"):
        super().__init__(msg)
        self.sqlstate = sqlstate


class _Wrapped(Exception):
    """模擬 SQLAlchemy 的 DBAPIError：SQLSTATE 在 `.orig` 上。"""

    def __init__(self, orig):
        super().__init__(f"wrapped: {orig}")
        self.orig = orig


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def first(self):
        return self._rows[0] if self._rows else None

    def one(self):
        assert len(self._rows) == 1
        return self._rows[0]

    def all(self):
        return list(self._rows)

    def scalar(self):
        row = self.first()
        if row is None:
            return None
        return next(iter(row.values())) if isinstance(row, dict) else row[0]


class _Nested:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeSession:
    """依 SQL 子字串分派的假 session；handler 回列（list）或拋例外。記下所有送出的 SQL。"""

    def __init__(self, handlers):
        self.handlers = handlers
        self.sql: list[str] = []

    def begin_nested(self):
        return _Nested()

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        self.sql.append(sql)
        if sql.startswith("SET LOCAL"):
            return _Result([])
        for key, handler in self.handlers:
            if key in sql:
                out = handler(params) if callable(handler) else handler
                if isinstance(out, BaseException):
                    raise out
                return _Result(out)
        raise AssertionError(f"沒有對應的假資料：{sql[:120]}")


def _overview_handlers(**overrides):
    base = [
        ("current_database() AS name", [{"name": "research", "size_bytes": 9 * 2**30, "server_version": "16.4"}]),
        ("count(*) AS table_count", [{"table_count": 2, "live": 90, "dead": 10}]),
        ("FROM pg_stat_user_tables s JOIN pg_class", [
            {"schemaname": "research", "relname": "report_chunk", "total_bytes": 8 * 2**30, "reltuples": 700000.0,
             "n_live_tup": 80, "n_dead_tup": 20, "last_autovacuum": datetime(2026, 10, 6, tzinfo=UTC),
             "last_vacuum": None, "last_autoanalyze": None, "last_analyze": None},
            {"schemaname": "research", "relname": "fresh", "total_bytes": 8192, "reltuples": -1.0,
             "n_live_tup": 0, "n_dead_tup": 0, "last_autovacuum": None, "last_vacuum": None,
             "last_autoanalyze": None, "last_analyze": None},
        ]),
        ("COALESCE(sum(pg_relation_size(indexrelid)), 0)", [{"n": 1, "bytes": 16384}]),
        ("FROM pg_stat_user_indexes s JOIN pg_index", [
            {"schemaname": "research", "relname": "t", "indexrelname": "idx_t_x", "size_bytes": 16384,
             "indisunique": False, "indisprimary": False}]),
        ("FROM pg_settings", [{"name": "max_connections", "setting": "100"},
                              {"name": "superuser_reserved_connections", "setting": "3"}]),
        ("FROM pg_stat_activity", [
            {"hidden": False, "state": "active", "n": 2, "here": 2},
            {"hidden": False, "state": "idle", "n": 5, "here": 4},
            {"hidden": False, "state": "idle in transaction", "n": 1, "here": 1},
            {"hidden": True, "state": "", "n": 3, "here": 0}]),
        ("FROM pg_stat_database", [{"blks_hit": 990, "blks_read": 10, "temp_files": 1, "temp_bytes": 4096,
                                    "deadlocks": 0, "xact_commit": 5, "xact_rollback": 1, "stats_reset": None}]),
    ]
    out = []
    for key, rows in base:
        out.append((key, overrides.get(key, rows)))
    return out


class ErrorClassificationTests(unittest.TestCase):
    def test_sqlstate_found_on_orig_or_cause(self):
        self.assertEqual(di.classify_error(_Wrapped(_PgError("42501"))), "permission_denied")
        self.assertEqual(di.classify_error(_Wrapped(_PgError("57014"))), "timeout")
        self.assertEqual(di.classify_error(_PgError("55P03")), "timeout")
        cause = RuntimeError("outer")
        cause.__cause__ = _PgError("42501")
        self.assertEqual(di.classify_error(cause), "permission_denied")
        self.assertEqual(di.classify_error(_Wrapped(_PgError("42P01"))), "error")
        self.assertEqual(di.classify_error(RuntimeError("Permission denied for view x")), "permission_denied")
        self.assertEqual(di.classify_error(RuntimeError("weird")), "error")

    def test_error_payload_never_echoes_exception_text(self):
        payload = di.error_payload("error", RuntimeError("SELECT secret FROM x"), ms=5000)
        self.assertEqual(payload["code"], "error")
        self.assertNotIn("secret", payload["message"])
        self.assertIn("RuntimeError", payload["message"])
        self.assertIn("5000", di.error_payload("timeout", ms=5000)["message"])

    def test_timeout_is_capped_by_engine_default(self):
        self.assertLessEqual(di.effective_timeout_ms(), di.OVERVIEW_STATEMENT_TIMEOUT_MS)
        self.assertGreater(di.effective_timeout_ms(), 0)


class OverviewDegradeTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_sections_ok(self):
        s = _FakeSession(_overview_handlers())
        ov = await di.collect_overview(s, now=datetime(2026, 10, 7, tzinfo=UTC))
        self.assertEqual(s.sql[0], f"SET LOCAL statement_timeout = {di.effective_timeout_ms()}")
        self.assertTrue(s.sql[1].startswith("SET LOCAL lock_timeout"))
        for name in di.SECTIONS:
            self.assertIsNone(ov[name]["error"], name)
        c = ov["connections"]
        self.assertEqual((c["total"], c["hidden"], c["usable_connections"], c["this_database"]), (11, 3, 97, 7))
        self.assertEqual(c["by_state"], {"active": 2, "idle": 5, "idle in transaction": 1})
        t = ov["tables"]
        self.assertAlmostEqual(t["dead_ratio"], 0.1)
        self.assertEqual(t["items"][0]["dead_ratio"], 0.2)
        self.assertIsNone(t["items"][1]["row_estimate"], "reltuples=-1 是沒有估計，不是 -1 列")
        self.assertIsNone(t["items"][1]["dead_ratio"])
        self.assertAlmostEqual(ov["activity"]["cache_hit_ratio"], 0.99)

    async def test_one_section_denied_others_still_answer(self):
        s = _FakeSession(_overview_handlers(**{"FROM pg_stat_activity": _Wrapped(_PgError("42501"))}))
        ov = await di.collect_overview(s)
        self.assertEqual(ov["connections"]["error"]["code"], "permission_denied")
        self.assertNotIn("total", ov["connections"])
        for name in ("database", "tables", "unused_indexes", "activity"):
            self.assertIsNone(ov[name]["error"], name)

    async def test_every_section_failing_does_not_raise(self):
        boom = _Wrapped(_PgError("57014"))
        s = _FakeSession([(k, boom) for k, _ in _overview_handlers()])
        ov = await di.collect_overview(s)
        self.assertEqual({ov[n]["error"]["code"] for n in di.SECTIONS}, {"timeout"})
        stats = di.snapshot_stats(ov)
        self.assertEqual(stats["gauges"], {})
        self.assertEqual(stats["counters"], {})
        self.assertEqual(set(stats["errors"]), set(di.SECTIONS))


_EXT_ROW = [{"schema_q": "public", "extversion": "1.10"}]
_COLS = [("queryid",), ("query",), ("calls",), ("total_exec_time",), ("mean_exec_time",), ("rows",)]


def _slow_session(*, ext=_EXT_ROW, pkg=True, view=None):
    rows = view if view is not None else [
        {"queryid": "1", "query": "SELECT   *\n FROM research.report_chunk WHERE id = $1", "calls": 10,
         "total_ms": 1234.5, "mean_ms": 123.45, "rows": 10, "shared_blks_hit": 9, "shared_blks_read": 1,
         "temp_blks_written": 0},
        {"queryid": "2", "query": "<insufficient privilege>", "calls": 3, "total_ms": 10.0, "mean_ms": 3.3,
         "rows": 0, "shared_blks_hit": 0, "shared_blks_read": 0, "temp_blks_written": 0},
        {"queryid": "3", "query": "SELECT " + "x, " * 200 + "1", "calls": 1, "total_ms": 1.0, "mean_ms": 1.0,
         "rows": 1, "shared_blks_hit": 0, "shared_blks_read": 0, "temp_blks_written": 0},
    ]
    return _FakeSession([
        ("FROM pg_extension e", ext),
        ("FROM pg_available_extensions", [(1,)] if pkg else []),
        ("FROM pg_attribute", _COLS),
        ("SELECT to_regclass", [(None,)]),
        ("FROM public.pg_stat_statements s", rows),
    ])


class SlowQueryTests(unittest.IsolatedAsyncioTestCase):
    def _assert_no_side_effects(self, s: _FakeSession):
        for sql in s.sql:
            upper = sql.upper()
            self.assertNotIn("CREATE EXTENSION", upper)
            self.assertNotIn("ALTER SYSTEM", upper)
            if upper.startswith("SET"):
                self.assertTrue(upper.startswith("SET LOCAL STATEMENT_TIMEOUT") or upper.startswith(
                    "SET LOCAL LOCK_TIMEOUT"), sql)

    async def test_extension_missing_with_and_without_package(self):
        for pkg, needle in ((True, "CREATE EXTENSION"), (False, "沒有這個套件")):
            with self.subTest(pkg=pkg):
                s = _slow_session(ext=[], pkg=pkg)
                out = await di.slow_queries(s)
                self.assertEqual((out["available"], out["reason"], out["items"]), (False, "extension_missing", []))
                self.assertIn(needle, out["message"])
                self._assert_no_side_effects(s)

    async def test_not_preloaded(self):
        err = _Wrapped(_PgError("55000", "pg_stat_statements must be loaded via shared_preload_libraries"))
        s = _slow_session(view=err)
        out = await di.slow_queries(s)
        self.assertEqual((out["available"], out["reason"]), (False, "not_preloaded"))
        self._assert_no_side_effects(s)

    async def test_permission_denied_on_view_or_catalog(self):
        s = _slow_session(view=_Wrapped(_PgError("42501")))
        self.assertEqual((await di.slow_queries(s))["reason"], "permission_denied")
        s = _slow_session(ext=_Wrapped(_PgError("42501")))
        self.assertEqual((await di.slow_queries(s))["reason"], "permission_denied")

    async def test_available_truncates_and_hides(self):
        s = _slow_session()
        out = await di.slow_queries(s, limit=999, sort="mean")
        self.assertTrue(out["available"])
        self.assertEqual((out["limit"], out["sort"], out["hidden_count"]), (di.SLOW_QUERY_MAX_LIMIT, "mean", 1))
        first, hidden, long_ = out["items"]
        self.assertEqual(first["query"], "SELECT * FROM research.report_chunk WHERE id = $1")
        self.assertEqual(first["cache_hit_ratio"], 0.9)
        self.assertEqual((hidden["query"], hidden["query_hidden"]), (None, True))
        self.assertTrue(long_["query_truncated"])
        self.assertEqual(len(long_["query"]), di.QUERY_TEXT_MAX_CHARS + 1)  # 截斷＋「…」
        view_sql = next(q for q in s.sql if "pg_stat_statements s" in q)
        self.assertIn("ORDER BY mean_ms DESC", view_sql)
        self.assertIn("current_database()", view_sql, "只看目前這個庫")
        self._assert_no_side_effects(s)

    async def test_unknown_sort_rejected(self):
        with self.assertRaises(ValueError):
            await di.slow_queries(_slow_session(), sort="query; DROP")


class SnapshotStatsTests(unittest.TestCase):
    def test_rollup_day_aggregates(self):
        hourly = [
            {"gauges": {"db_size_bytes": 100, "connections_total": 4}, "counters": {"temp_bytes": 10},
             "tables": {"research.a": 1}, "stats_reset": None},
            {"gauges": {"db_size_bytes": 300}, "counters": {"temp_bytes": 30}, "tables": {"research.a": 3}},
            {"gauges": {"db_size_bytes": 200, "connections_total": 8}, "counters": {"temp_bytes": 50},
             "tables": {"research.a": 2}},
        ]
        day = di.rollup_day(date(2026, 10, 6), hourly)
        self.assertEqual(day["samples"], 3)
        self.assertEqual(day["gauges"]["db_size_bytes"], {"avg": 200.0, "min": 100.0, "max": 300.0, "last": 200.0})
        self.assertEqual(day["gauges"]["connections_total"], {"avg": 6.0, "min": 4.0, "max": 8.0, "last": 8.0})
        self.assertEqual(day["counters"], {"temp_bytes": 50.0})
        self.assertEqual(day["tables"], {"research.a": 2})

    def test_day_start_is_taipei_midnight(self):
        self.assertEqual(di.day_start(date(2026, 10, 7)), datetime(2026, 10, 6, 16, tzinfo=UTC))

    def test_pick_granularity(self):
        now = datetime(2026, 10, 7, tzinfo=UTC)
        self.assertEqual(di.pick_granularity(now - timedelta(days=7), now, hourly_days=30), "hour")
        self.assertEqual(di.pick_granularity(now - timedelta(days=30), now, hourly_days=30), "hour")
        self.assertEqual(di.pick_granularity(now - timedelta(days=31), now, hourly_days=30), "day")

    def test_retention_days_defaults(self):
        self.assertEqual(di.retention_days(), (30, 400))


T0 = datetime(2026, 10, 6, 0, tzinfo=UTC)


def _h(i, **kw):
    return (T0 + timedelta(hours=i), kw)


class TrendPointTests(unittest.TestCase):
    def test_gauge_hourly_and_baseline_not_emitted(self):
        rows = [_h(0, gauges={"db_size_bytes": 1}), _h(1, gauges={"db_size_bytes": 2}), _h(2, gauges={})]
        pts = di.compute_points(rows, metric="db_size_bytes", granularity="hour", since=T0 + timedelta(hours=1))
        self.assertEqual([p["value"] for p in pts], [2.0, None])
        self.assertEqual(pts[0]["t"], (T0 + timedelta(hours=1)).isoformat())

    def test_gauge_daily_uses_avg_with_min_max(self):
        rows = [(T0, {"gauges": {"connections_total": {"avg": 5, "min": 1, "max": 9, "last": 4}}})]
        (p,) = di.compute_points(rows, metric="connections_total", granularity="day", since=T0)
        self.assertEqual((p["value"], p["min"], p["max"]), (5.0, 1.0, 9.0))

    def test_rate_normalised_per_hour_and_reset_is_none(self):
        rows = [
            _h(0, counters={"deadlocks": 10}),
            _h(1, counters={"deadlocks": 12}),
            _h(3, counters={"deadlocks": 16}),   # 缺一點：兩小時增加 4 → 每小時 2
            _h(4, counters={"deadlocks": 1}),    # 統計被重設
        ]
        pts = di.compute_points(rows, metric="deadlocks", granularity="hour", since=T0)
        self.assertEqual([p["value"] for p in pts], [None, 2.0, 2.0, None])

    def test_cache_hit_ratio_uses_deltas(self):
        rows = [
            _h(0, counters={"blks_hit": 1000, "blks_read": 1000}),
            _h(1, counters={"blks_hit": 1090, "blks_read": 1010}),
            _h(2, counters={"blks_hit": 1090, "blks_read": 1010}),  # 沒有讀取：比例無定義
        ]
        pts = di.compute_points(rows, metric="cache_hit_ratio", granularity="hour", since=T0 + timedelta(hours=1))
        self.assertAlmostEqual(pts[0]["value"], 0.9)
        self.assertIsNone(pts[1]["value"])

    def test_table_bytes_reads_tables_map(self):
        rows = [_h(0, tables={"research.a": 5})]
        (p,) = di.compute_points(rows, metric="table_bytes", granularity="hour", since=T0, table="research.a")
        self.assertEqual(p["value"], 5.0)
        (p,) = di.compute_points(rows, metric="table_bytes", granularity="hour", since=T0, table="research.b")
        self.assertIsNone(p["value"])


if __name__ == "__main__":
    unittest.main()
