"""`app/services/ops_rollup.py` 的純函式與 `scripts/rollup_observations.py` 的失敗語意（不連 DB）。

SQL 對真 DB 的驗證在 tests/test_ops_rollup_db.py。這裡釘：桶的對齊與 SQL 的 date_bin 同義（與時區無關）；
三條界線對齊整點；查詢粒度只在資料保證還在時才選細的；DB 連不上 rc=2、參數不合 rc=1。
"""

from __future__ import annotations

import sys
import unittest
from argparse import Namespace
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import ops_rollup as orl  # noqa: E402
from scripts import rollup_observations as ro  # noqa: E402

UTC = timezone.utc
NOW = datetime(2026, 10, 6, 12, 30, 45, tzinfo=UTC)


class BucketTests(unittest.TestCase):
    def test_floor_matches_date_bin_and_ignores_timezone(self):
        taipei = timezone(timedelta(hours=8))
        kolkata = timezone(timedelta(hours=5, minutes=30))
        ts = datetime(2026, 10, 6, 20, 37, 12, tzinfo=taipei)  # 12:37:12 UTC
        self.assertEqual(orl.floor_to(ts, timedelta(minutes=5)), datetime(2026, 10, 6, 12, 35, tzinfo=UTC))
        self.assertEqual(orl.floor_to(ts, timedelta(hours=1)), datetime(2026, 10, 6, 12, 0, tzinfo=UTC))
        # 半小時時區：桶仍對齊 UTC 整點（date_trunc('hour') 在 session 時區下會錯開 30 分）
        self.assertEqual(orl.floor_to(ts.astimezone(kolkata), timedelta(hours=1)),
                         datetime(2026, 10, 6, 12, 0, tzinfo=UTC))

    def test_cutoffs_are_whole_hours(self):
        cut = orl.cutoffs(NOW)
        self.assertEqual(cut.raw_before, datetime(2026, 10, 5, 12, 0, tzinfo=UTC))
        self.assertEqual(cut.fine_before, datetime(2026, 9, 29, 12, 0, tzinfo=UTC))
        self.assertEqual(cut.purge_before, datetime(2026, 7, 8, 12, 0, tzinfo=UTC))
        self.assertLessEqual(cut.raw_before, NOW - orl.RAW_RETENTION, "原始觀測至少保留 24 小時")
        self.assertLessEqual(cut.purge_before, NOW - orl.RETENTION, "至少保留 90 天")


class ResolutionTests(unittest.TestCase):
    def test_finest_resolution_whose_data_is_guaranteed(self):
        cases = [
            (NOW - timedelta(hours=1), "raw"),
            (NOW - timedelta(hours=24), "raw"),
            (NOW - timedelta(hours=24, seconds=1), "5m"),
            (NOW - timedelta(days=7), "5m"),
            (NOW - timedelta(days=7, seconds=1), "1h"),
            (NOW - timedelta(days=90), "1h"),
            (NOW + timedelta(hours=1), "raw"),
        ]
        for since, want in cases:
            with self.subTest(since=since):
                self.assertEqual(orl.pick_resolution(since, NOW), want)

    def test_raw_window_never_reaches_rolled_up_data(self):
        """選 raw 的最早 since（now − 24h）不早於 rollup 搬走的界線；5m 同理不碰到 1 小時桶。"""
        cut = orl.cutoffs(NOW)
        self.assertGreaterEqual(NOW - orl.RAW_RETENTION, cut.raw_before)
        self.assertGreaterEqual(NOW - orl.FINE_RETENTION, cut.fine_before)

    def test_move_sql_is_one_statement_that_deletes_then_inserts(self):
        for source, dest in (("raw", "service_observation_5m"), ("5m", "service_observation_1h")):
            sql = orl._move_sql(source, None)
            self.assertLess(sql.index("DELETE FROM"), sql.index(f"INSERT INTO research.{dest}"))
            self.assertIn("FROM moved", sql, "聚合的來源必須是被刪掉的那些列（同一個 snapshot）")
            self.assertNotIn(":hosts", sql, "正式批次不帶 host 篩選")
        self.assertIn(":hosts", orl._move_sql("raw", ["h"]))


class ScriptTests(unittest.TestCase):
    def test_db_unavailable_returns_2(self):
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        eng = create_async_engine("postgresql+asyncpg://nobody:x@/none?host=/nonexistent/report-mark-socket-dir")
        rc = ro.run(Namespace(dry_run=False, max_slices=1, batch_size=1), eng, async_sessionmaker(eng))
        self.assertEqual(rc, ro.EXIT_DB)

    def test_rejects_non_positive_limits(self):
        self.assertEqual(ro.main(["--max-slices", "0"]), ro.EXIT_FAILED)
        self.assertEqual(ro.main(["--batch-size", "-1"]), ro.EXIT_FAILED)


if __name__ == "__main__":
    unittest.main()
