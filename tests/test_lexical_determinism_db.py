"""字面路對真的 PostgreSQL 可重現（`store._lexical_sql`／`store.search_chunks_lexical`）。

`tests/test_store_sql.py` 只釘 SQL 字串（cap 前有 `ORDER BY c.id`、距離平手以 id 決勝、
查詢前 `SET LOCAL plan_cache_mode = force_custom_plan`）；這裡驗那些字串在真的 planner
底下真的成立：塞兩篇研報、命中數遠超過 cap 的 chunk，

- 連跑多次結果逐列相同，且在 generic plan 與強迫平行掃描的設定下也一樣；
- cap 取到的正是「chunk id 最小的 cap 個命中」，與 heap 寫入順序無關（chunk id 是隨機
  uuid，寫入順序幾乎不可能剛好等於 id 順序——舊的不排序寫法在這裡會紅）；
- 所有 chunk 向量相同（距離全部平手）時，`LIMIT :limit` 與 per_report 的 `DISTINCT ON`
  取到哪幾列由 id 決定；
- 呼叫之後同一交易的 `plan_cache_mode` 是 `force_custom_plan`（SET LOCAL，交易結束還原）。

跑在 CI 的「schema 契約」job；本機沒有 DB 就 skip。**一律 rollback、絕不 commit**——本機
預設連到的是生產庫（`upsert_report` 本身會 commit，測試把那個 session 的 commit 換成
flush）。斷言只針對自己塞進去的列：字面用語料裡不存在的詞，在完整語料上也只命中自己。
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import unittest
import uuid
from datetime import date

from sqlalchemy import text

from app.services import store

DIM = 1024
TERM = "zqlexdetprobe"  # 語料裡不存在的字面詞
PATTERN = f"%{TERM}%"
CHUNKS_PER_REPORT = 80
CAP = 50  # 遠小於命中數（2 × 80）：cap 一定咬到


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


def _vec() -> list[float]:
    """全部 chunk 與查詢共用同一個向量：距離全部平手，排序只剩 id 決勝。"""
    v = [0.0] * DIM
    v[0] = 1.0
    return v


class _NoCommit:
    """把 session.commit 暫時換成 flush：upsert_report 會 commit，測試必須能整個 rollback。"""

    def __init__(self, session):
        self.session = session

    def __enter__(self):
        self._orig = self.session.commit
        self.session.commit = self.session.flush
        return self

    def __exit__(self, *exc):
        self.session.commit = self._orig
        return False


async def _ingest(session, name: str) -> str:
    file_hash = hashlib.sha256(f"lexdet-{name}-{uuid.uuid4()}".encode()).hexdigest()
    row = store.ReportRow(
        file_hash=file_hash, file_name=f"{name}.pdf", file_path=f"/nonexistent/{name}.pdf",
        market="TW", is_research=True, confidence=1.0, source=f"測試券商{name}",
        report_date=date(2099, 1, 2), report_type="產業", full_text=f"{TERM} {name}",
    )
    chunks = [f"{TERM} 字面可重現測試 {name} 第{i}段" for i in range(CHUNKS_PER_REPORT)]
    with _NoCommit(session):
        return await store.upsert_report(session, row, chunks, [_vec() for _ in chunks])


async def _own_chunk_ids(session, report_ids: list[str]) -> list[str]:
    rows = await session.execute(
        text(
            "SELECT id::text FROM research.report_chunk "
            "WHERE report_id = ANY(CAST(:ids AS uuid[])) ORDER BY id"
        ),
        {"ids": report_ids},
    )
    return [r[0] for r in rows]


async def _search(session, **kw):
    return await store.search_chunks_lexical(session, _vec(), [PATTERN], cap=CAP, **kw)


class LexicalDeterminismDbTests(unittest.TestCase):
    def _run(self, fn):
        try:
            return asyncio.run(_with_session(fn))
        except (unittest.SkipTest, AssertionError):
            raise
        except Exception as exc:  # 連不上、或庫還沒套 schema
            _skip_or_raise(exc, "DB 不可用")

    def test_cap_takes_smallest_ids_and_repeats_exactly(self):
        async def body(session):
            rids = [await _ingest(session, "甲"), await _ingest(session, "乙")]
            own = await _own_chunk_ids(session, rids)
            self.assertEqual(len(own), 2 * CHUNKS_PER_REPORT)
            expected = own[:CAP]  # uuid 排序（PG 與這裡的 ORDER BY id 同序）

            first, hits = await _search(session, limit=None)
            self.assertEqual(hits, CAP)
            # 平手（距離全同）時最終順序就是 id 順序
            self.assertEqual([r.chunk_id for r in first], expected)

            for _ in range(4):
                again, again_hits = await _search(session, limit=None)
                self.assertEqual((again, again_hits), (first, hits))

            # 呼叫端事先設了 generic plan 也不影響：查詢前會 SET LOCAL 成 custom plan
            await session.execute(text("SET LOCAL plan_cache_mode = force_generic_plan"))
            generic, _ = await _search(session, limit=None)
            self.assertEqual(generic, first)
            mode = (await session.execute(text("SELECT current_setting('plan_cache_mode')"))).scalar()
            self.assertEqual(mode, "force_custom_plan")

            # 強迫平行掃描（舊寫法不可重現的那條路：Gather 的列序每次不同）
            for stmt in (
                "SET LOCAL parallel_setup_cost = 0",
                "SET LOCAL parallel_tuple_cost = 0",
                "SET LOCAL min_parallel_table_scan_size = 0",
                "SET LOCAL max_parallel_workers_per_gather = 2",
            ):
                await session.execute(text(stmt))
            for _ in range(3):
                parallel, _ = await _search(session, limit=None)
                self.assertEqual(parallel, first)

        self._run(body)

    def test_distance_ties_are_broken_by_id_under_final_limit(self):
        async def body(session):
            rids = [await _ingest(session, "丙"), await _ingest(session, "丁")]
            own = await _own_chunk_ids(session, rids)
            top, _ = await _search(session, limit=10)
            self.assertEqual([r.chunk_id for r in top], own[:CAP][:10])

        self._run(body)

    def test_per_report_picks_smallest_id_per_report_among_ties(self):
        async def body(session):
            rids = [await _ingest(session, "戊"), await _ingest(session, "己")]
            own = await _own_chunk_ids(session, rids)
            capped = set(own[:CAP])
            rows, hits = await _search(session, limit=None, per_report=True)
            self.assertEqual(hits, CAP)
            # 每篇取 cap 內 id 最小的 chunk；兩篇之間距離也平手，再以 id 排
            by_report: dict[str, str] = {}
            id_to_report = dict(
                (r[0], r[1])
                for r in (
                    await session.execute(
                        text(
                            "SELECT id::text, report_id::text FROM research.report_chunk "
                            "WHERE id = ANY(CAST(:ids AS uuid[]))"
                        ),
                        {"ids": sorted(capped)},
                    )
                )
            )
            for cid in sorted(capped):
                by_report.setdefault(id_to_report[cid], cid)
            self.assertEqual([r.chunk_id for r in rows], sorted(by_report.values()))
            for _ in range(3):
                again, _ = await _search(session, limit=None, per_report=True)
                self.assertEqual(again, rows)

        self._run(body)


if __name__ == "__main__":
    unittest.main()
