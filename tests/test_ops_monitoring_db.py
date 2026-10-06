"""監控投影對真的 PostgreSQL 成立（revision 0005 ＋ app/services/ops_monitoring.py ＋ load_observations）。

驗：同一份 spool 匯入兩次不重複（自然鍵）；批次 running → finished 只前進不倒退；沒看到結束、同 unit
已有更晚開始的 invocation 時判 lost，之後補到結束紀錄仍改成 finished；DB 拒絕的列（CHECK）只略過那一列，
同一批其他列照常寫入。

跑在 CI 的「schema 契約」job；本機沒有 DB（或庫還沒套 revision 0005）就 skip。**一律 rollback、
絕不 commit**——本機預設連到的是生產庫。斷言只針對自己塞進去的列（以隨機 host 辨識），不假設庫是空的。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import ops_monitoring as om  # noqa: E402
from scripts import load_observations as lo  # noqa: E402

INV_A, INV_B = "a" * 32, "b" * 32
UNIT = "report-mark-sync.service"


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
        if "service_observation" in str(exc) or "job_execution" in str(exc):
            _skip_or_raise(exc, "庫還沒套 revision 0005")
        raise


class OpsMonitoringDbTests(unittest.TestCase):
    def setUp(self):
        self.host = f"lane-test-{uuid.uuid4().hex[:12]}"

    def _obs(self, subject="host", ts="2026-10-06T10:00:00+08:00", **metrics) -> dict:
        return {"v": 1, "type": "observation", "host": self.host, "observed_at": ts, "scope": "host",
                "subject": subject, "metrics": metrics or {"cpu_pct": 1.5, "load1": 0.2},
                "states": {"active_state": "active"}, "detail": {"ncpu": 4}}

    def _job(self, inv, state, started, finished=None, seen=None, result="success", status=0) -> dict:
        done = state == "finished"
        return {"v": 1, "type": "job", "host": self.host, "unit": UNIT, "service": "sync", "invocation_id": inv,
                "state": state, "started_at": started, "finished_at": finished,
                "result": result if done else None, "exit_status": status if done else None,
                "exec_main_code": "exited" if done else None, "observed_at": seen or finished or started}

    async def _count(self, session, table):
        return (await session.execute(text(f"SELECT count(*) FROM research.{table} WHERE host = :h"),
                                      {"h": self.host})).scalar_one()

    async def _jobs(self, session):
        rows = await session.execute(text(
            "SELECT invocation_id, state, finished_at IS NOT NULL AS ended, result FROM research.job_execution "
            "WHERE host = :h ORDER BY started_at"), {"h": self.host})
        return [tuple(r) for r in rows]

    def test_same_spool_imported_twice_does_not_duplicate(self):
        with tempfile.TemporaryDirectory() as tmp:
            spool = Path(tmp)
            (spool / "observations-20261006.jsonl").write_text(
                "".join(json.dumps(r) + "\n" for r in (self._obs(), self._obs(subject="fs:/", used_pct=40.0))),
                encoding="utf-8")
            (spool / "jobs-20261006.jsonl").write_text(
                json.dumps(self._job(INV_A, "finished", "2026-10-06T09:00:00+08:00",
                                     "2026-10-06T09:10:00+08:00")) + "\n", encoding="utf-8")
            batch = lo.collect(spool, {}, lo.DEFAULT_MAX_BYTES)
            again = lo.collect(spool, {}, lo.DEFAULT_MAX_BYTES)  # 進度沒前進＝下一輪重讀同一份

        async def body(session):
            first = await om.import_records(session, observations=batch.observations, jobs=batch.jobs)
            n_obs = await self._count(session, "service_observation")
            n_jobs = await self._count(session, "job_execution")
            await om.import_records(session, observations=again.observations, jobs=again.jobs)
            return first, n_obs, n_jobs, await self._count(session, "service_observation"), \
                await self._count(session, "job_execution")

        first, n_obs, n_jobs, n_obs2, n_jobs2 = _run(body)
        self.assertEqual(first.rejected, 0)
        self.assertEqual(n_obs, 5, "2 個數值＋1 個狀態，加上 fs 的 1 個數值＋1 個狀態")
        self.assertEqual((n_obs2, n_jobs, n_jobs2), (5, 1, 1))

    def test_job_running_finishes_and_never_goes_back(self):
        start, end = "2026-10-06T09:00:00+08:00", "2026-10-06T09:10:00+08:00"
        running = om.job_row(self._job(INV_A, "running", start, seen="2026-10-06T09:01:00+08:00"))
        finished = om.job_row(self._job(INV_A, "finished", start, end))

        async def body(session):
            await om.import_records(session, observations=[], jobs=[running])
            a = await self._jobs(session)
            await om.import_records(session, observations=[], jobs=[finished])
            b = await self._jobs(session)
            await om.import_records(session, observations=[], jobs=[running])  # 遲到的舊 running
            return a, b, await self._jobs(session)

        a, b, c = _run(body)
        self.assertEqual(a, [(INV_A, "running", False, None)])
        self.assertEqual(b, [(INV_A, "finished", True, "success")])
        self.assertEqual(c, b, "finished 之後不再變動")

    def test_unseen_end_becomes_lost_and_late_finish_still_wins(self):
        a_run = om.job_row(self._job(INV_A, "running", "2026-10-06T09:00:00+08:00"))
        b_run = om.job_row(self._job(INV_B, "running", "2026-10-06T12:00:00+08:00"))
        a_done = om.job_row(self._job(INV_A, "finished", "2026-10-06T09:00:00+08:00", "2026-10-06T09:30:00+08:00",
                                      result="exit-code", status=1))

        async def body(session):
            await om.import_records(session, observations=[], jobs=[a_run])
            stats = await om.import_records(session, observations=[], jobs=[b_run])
            lost = await self._jobs(session)
            await om.import_records(session, observations=[], jobs=[a_done])
            return stats, lost, await self._jobs(session)

        stats, lost, after = _run(body)
        self.assertEqual(stats.lost, 1)
        self.assertEqual(lost, [(INV_A, "lost", False, None), (INV_B, "running", False, None)])
        self.assertEqual(after, [(INV_A, "finished", True, "exit-code"), (INV_B, "running", False, None)])

    def test_rejected_row_does_not_block_the_rest_of_the_batch(self):
        rows = om.observation_rows(self._obs())
        bad = dict(rows[0], metric="Bad-Metric")  # 繞過 Python 端驗證，讓 CHECK 擋

        async def body(session):
            stats = await om.import_records(session, observations=[*rows, bad], jobs=[])
            return stats, await self._count(session, "service_observation")

        stats, n = _run(body)
        self.assertEqual((stats.rejected, n), (1, len(rows)))


if __name__ == "__main__":
    unittest.main()
