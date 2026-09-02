"""E1b 的 DB 對帳：extraction_log 的 upsert 與 research_report 新欄位真的寫得進去。

跑在 CI 的「schema 契約」job（套完 db/schema.sql 的乾淨庫）；本機沒有 DB、或既有庫
還沒 `make schema` 就 skip（`REPORT_MARK_REQUIRE_DB` 設定時改成紅燈，與
tests/test_schema_constraints.py 同一套語意）。

**一律 rollback、絕不 commit**：這支測試在本機會連到生產庫。`upsert_report` 自己會
commit，所以把 session.commit 換成 no-op 再整個 rollback。
"""

from __future__ import annotations

import asyncio
import os
import unittest
import uuid
from unittest import mock

from app.services import store


def _skip_or_raise(exc: Exception, why: str) -> None:
    if os.getenv("REPORT_MARK_REQUIRE_DB"):
        raise exc
    raise unittest.SkipTest(f"{why}：{exc}")


async def _with_session(fn):
    """自建 engine、跑完 dispose——不重用 app.services.db 的全域池（理由見 test_schema_constraints）。"""
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    from app.services.db import DATABASE_URL

    eng = create_async_engine(DATABASE_URL, echo=False, pool_pre_ping=True)
    try:
        async with AsyncSession(eng, expire_on_commit=False) as session:
            with mock.patch.object(session, "commit", new=mock.AsyncMock()):
                try:
                    return await fn(session)
                finally:
                    await session.rollback()
    finally:
        await eng.dispose()


class ExtractionLogRoundTripTests(unittest.TestCase):
    def _run(self, fn):
        try:
            return asyncio.run(_with_session(fn))
        except unittest.SkipTest:
            raise
        except Exception as exc:  # 連不上、或表還沒建（本機尚未 make schema）
            name = type(exc).__name__
            msg = repr(exc)
            if "UndefinedTable" in msg or "UndefinedColumn" in msg or "does not exist" in msg:
                _skip_or_raise(exc, "既有庫尚未套用 E1b schema")
            _skip_or_raise(exc, f"DB 不可用（{name}）")

    def test_upsert_accumulates_file_names_and_overwrites_stopped_at(self):
        from sqlalchemy import text

        h = "test-e1b-" + uuid.uuid4().hex

        async def fn(session):
            await store.upsert_extraction_log(
                session, store.ExtractionLogRow(h, "a.pdf", "pypdf", "pypdf-x", "scanned", char_count=10)
            )
            await store.upsert_extraction_log(
                session,
                store.ExtractionLogRow(
                    h, "b.pdf", "pdfplumber", "ext-v", "ingested", page_count=3, pages_failed=[2],
                    char_count=999, quality_score=0.42, quality_flags={"garbled_ratio": 0.01},
                ),
            )
            # 同一個檔名再寫一次不得重複累加
            await store.upsert_extraction_log(
                session, store.ExtractionLogRow(h, "a.pdf", "pdfplumber", "ext-v", "ingested")
            )
            row = (
                await session.execute(
                    text(
                        "SELECT file_names, extractor, extraction_version, stopped_at, pages_failed, "
                        "quality_score, quality_flags FROM research.extraction_log WHERE file_hash = :h"
                    ),
                    {"h": h},
                )
            ).one()
            return row

        row = self._run(fn)
        self.assertEqual(list(row[0]), ["a.pdf", "b.pdf"])
        self.assertEqual((row[1], row[2], row[3]), ("pdfplumber", "ext-v", "ingested"))
        self.assertIsNone(row[4], "最後一次寫入沒帶 pages_failed，應覆寫成 NULL 而不是留舊值")
        self.assertIsNone(row[5])
        self.assertEqual(row[6], {})

    def test_check_constraint_rejects_unknown_stopped_at_at_db_level(self):
        from sqlalchemy import text

        async def fn(session):
            try:
                await session.execute(
                    text(
                        "INSERT INTO research.extraction_log (file_hash, file_names, extractor, extraction_version, "
                        "stopped_at) VALUES (:h, ARRAY['x']::text[], 'pypdf', 'v', 'skip_untagged')"
                    ),
                    {"h": "test-e1b-" + uuid.uuid4().hex},
                )
            except Exception as exc:
                if "check" not in repr(exc).lower():
                    raise  # 表不存在之類的環境問題交給 _run 判 skip，不要偽裝成 CHECK 擋下
                return repr(exc)
            return None

        err = self._run(fn)
        self.assertIsNotNone(err, "CHECK 沒擋住未知的 stopped_at")
        self.assertIn("check", err.lower())

    def test_upsert_report_writes_extraction_columns(self):
        from sqlalchemy import text

        h = "test-e1b-" + uuid.uuid4().hex

        async def fn(session):
            report = store.ReportRow(
                file_hash=h, file_name="t.pdf", file_path="/tmp/t.pdf", market="TW", is_research=True,
                confidence=0.9, full_text="x", extractor="pdfplumber", extraction_version="ext-v",
                quality_score=0.5, quality_flags={"fallback_from": None, "garbled_ratio": 0.0},
                page_count=4, pages_failed=[1, 3], needs_review=True,
            )
            await store.upsert_report(session, report, [], [])
            return (
                await session.execute(
                    text(
                        "SELECT extractor, extraction_version, quality_score, quality_flags, page_count, "
                        "pages_failed, needs_review FROM research.research_report WHERE file_hash = :h"
                    ),
                    {"h": h},
                )
            ).one()

        row = self._run(fn)
        self.assertEqual((row[0], row[1]), ("pdfplumber", "ext-v"))
        self.assertAlmostEqual(row[2], 0.5, places=5)
        self.assertEqual(row[3]["garbled_ratio"], 0.0)
        self.assertEqual((row[4], list(row[5]), row[6]), (4, [1, 3], True))


if __name__ == "__main__":
    unittest.main()
