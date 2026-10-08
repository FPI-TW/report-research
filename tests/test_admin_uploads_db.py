"""研報上傳的收檔 SQL 對真的 PostgreSQL 成立（revision 0008 ＋ app/services/upload_intake.py）。

驗：partial unique index `idx_report_upload_active_hash` 擋同一個 file_hash 兩筆進行中、撞 index 轉成
`UploadInProgressError`（API 409）；語料重複（含隱藏、草稿）、進行中重複、曾判感染各自的錯誤；每日
（台北時間）與全站處理中的配額；INSERT 與稽核 `upload.create` 在同一筆交易（稽核失敗時上傳紀錄也不留、
`create_upload` 自己不 commit）；清單、詳情與掃描器摘要。

跑在 CI 的「schema 契約」job；本機沒有 DB（或庫還沒套 revision 0008）就 skip。**一律 rollback、絕不
commit**——本機預設連到的是測試環境的真實資料庫。斷言只針對自己塞進去的列（隨機 file_hash／帳號 id），不假設庫是空的。
"""

from __future__ import annotations

import asyncio
import os
import secrets
import unittest
import uuid
from unittest import mock

from sqlalchemy import text

from app.services import accounts, upload_intake, uploads


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


def _hash() -> str:
    return secrets.token_hex(32)


async def _insert(session, file_hash, state="quarantined", *, uid=None, uploaded_at=None, **extra):
    cols = {"file_hash": file_hash, "original_name": "測試上傳.pdf", "size_bytes": 1234, "state": state,
            "uploaded_by": uid, **extra}
    names = ", ".join(cols)
    values = ", ".join(f"CAST(:{k} AS uuid)" if k == "uploaded_by" else f":{k}" for k in cols)
    if uploaded_at is not None:
        names += ", uploaded_at"
        values += f", now() - interval '{uploaded_at}'"
    return (await session.execute(
        text(f"INSERT INTO research.report_upload ({names}) VALUES ({values}) RETURNING id::text"), cols,
    )).scalar_one()


class _NoCommit:
    def __init__(self, session):
        self.session = session

    def __enter__(self):
        self._orig = self.session.commit

        async def _fail():
            raise AssertionError("create_upload 不可以自己 commit")

        self.session.commit = _fail
        return self

    def __exit__(self, *exc):
        self.session.commit = self._orig
        return False


class UploadIntakeDbTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        async def probe(session):
            return (await session.execute(text("SELECT to_regclass('research.report_upload')"))).scalar()

        try:
            exists = asyncio.run(_with_session(probe))
        except Exception as exc:  # 連不上 DB
            _skip_or_raise(exc, "連不上 DB")
        if not exists:
            _skip_or_raise(RuntimeError("research.report_upload 不存在"), "庫還沒套 revision 0008")

    def run_db(self, fn):
        return asyncio.run(_with_session(fn))

    async def _create(self, session, file_hash, *, uid=None, daily=1000, in_flight=10**6, name="元大_報告.pdf"):
        upload_id = str(uuid.uuid4())
        with _NoCommit(session):
            row = await upload_intake.create_upload(
                session, upload_id=upload_id, file_hash=file_hash, original_name=name, size_bytes=4321,
                client_mtime=None, actor_id=uid, daily_quota=daily, max_in_flight=in_flight,
            )
        return upload_id, row

    def test_partial_unique_index_blocks_two_active_rows(self):
        async def body(session):
            from sqlalchemy.exc import IntegrityError

            h = _hash()
            await _insert(session, h, "quarantined")
            async with session.begin_nested():
                await _insert(session, h, "rejected", decision_reason="重複")  # 終態不受限
                await _insert(session, h, "infected", scan_signature="Eicar-Test-Signature")
            for state in uploads.ACTIVE_STATES:
                with self.subTest(state=state):
                    with self.assertRaises(IntegrityError) as ctx:
                        async with session.begin_nested():
                            await _insert(session, h, state)
                    self.assertIn(upload_intake.ACTIVE_INDEX, str(ctx.exception.orig))

        self.run_db(body)

    def test_create_inserts_quarantined_row_and_audit_in_same_tx(self):
        async def body(session):
            h = _hash()
            upload_id, row = await self._create(session, h)
            self.assertEqual((row.upload_id, row.state, row.file_hash, row.size_bytes),
                             (upload_id, "quarantined", h, 4321))
            self.assertEqual(row.original_name, "元大_報告.pdf")
            audit = (await session.execute(text(
                "SELECT action, target_type, detail FROM research.admin_audit_log WHERE target_id = :id"
            ), {"id": upload_id})).all()
            self.assertEqual(len(audit), 1)
            action, ttype, detail = audit[0]
            self.assertEqual((action, ttype), ("upload.create", "upload"))
            self.assertEqual(detail, {"upload_id": upload_id, "file_name": "元大_報告.pdf", "size_bytes": 4321,
                                      "state": "quarantined"})
            fetched = await upload_intake.get_upload(session, upload_id)
            self.assertEqual(fetched, row)

        self.run_db(body)

    def test_audit_failure_rolls_back_the_insert(self):
        async def body(session):
            h = _hash()

            async def broken_audit(*_a, **_k):
                raise RuntimeError("audit down")

            with mock.patch.object(accounts, "record_audit", broken_audit):
                with self.assertRaises(RuntimeError):
                    async with session.begin_nested():  # 代表呼叫端的交易：失敗就整段 rollback
                        await self._create(session, h)
            left = (await session.execute(
                text("SELECT count(*) FROM research.report_upload WHERE file_hash = :h"), {"h": h},
            )).scalar_one()
            self.assertEqual(left, 0)

        self.run_db(body)

    def test_in_progress_duplicate(self):
        async def body(session):
            h = _hash()
            first_id, _ = await self._create(session, h)
            with self.assertRaises(upload_intake.UploadInProgressError) as ctx:
                await self._create(session, h)
            self.assertEqual((ctx.exception.upload_id, ctx.exception.state), (first_id, "quarantined"))

        self.run_db(body)

    def test_unique_violation_is_mapped_to_in_progress(self):
        """先查後寫的空窗（另一條路徑在檢查之後才把同 hash 轉成進行中）：靠 index 擋，轉成同一個 409。"""
        async def body(session):
            h = _hash()
            await _insert(session, h, "clean")

            async def no_conflict(*_a, **_k):
                return None

            with mock.patch.object(upload_intake, "find_conflict", no_conflict):
                with self.assertRaises(upload_intake.UploadInProgressError) as ctx:
                    async with session.begin_nested():
                        await self._create(session, h)
            self.assertEqual(ctx.exception.file_hash, h)
            self.assertIsNone(ctx.exception.upload_id)

        self.run_db(body)

    def test_corpus_duplicate_reports_status(self):
        async def body(session):
            cases = {"published": None, "hidden": (True, "published"), "draft": (False, "draft")}
            for status, vis in cases.items():
                with self.subTest(status=status):
                    h = _hash()
                    await session.execute(text(
                        "INSERT INTO research.research_report (id, file_hash, file_name, file_path) "
                        "VALUES (gen_random_uuid(), :h, 'dup.pdf', '/nonexistent/dup.pdf')"
                    ), {"h": h})
                    if vis is not None:
                        await session.execute(text(
                            "INSERT INTO research.report_visibility (file_hash, hidden, reason, publication) "
                            "VALUES (:h, :hidden, :reason, :pub)"
                        ), {"h": h, "hidden": vis[0], "reason": "測試" if vis[0] else None, "pub": vis[1]})
                    with self.assertRaises(upload_intake.DuplicateInCorpusError) as ctx:
                        await self._create(session, h)
                    self.assertEqual((ctx.exception.file_hash, ctx.exception.status), (h, status))
                    report = await upload_intake.get_upload_report(session, h)
                    self.assertEqual(report.publication, "draft" if status == "draft" else "published")
                    self.assertEqual(report.hidden, status == "hidden")

        self.run_db(body)

    def test_known_infected(self):
        async def body(session):
            h = _hash()
            await _insert(session, h, "infected", scan_signature="Eicar-Test-Signature")
            with self.assertRaises(upload_intake.KnownInfectedError):
                await self._create(session, h)

        self.run_db(body)

    def test_daily_quota_per_user_taipei_day(self):
        async def body(session):
            uid = str(uuid.uuid4())
            await _insert(session, _hash(), "rejected", uid=uid, decision_reason="x")  # 任何狀態都算
            await _insert(session, _hash(), "published", uid=uid)
            await _insert(session, _hash(), "published", uid=uid, uploaded_at="2 days")  # 不是今天
            await _insert(session, _hash(), "published", uid=str(uuid.uuid4()))  # 別人的
            with self.assertRaises(upload_intake.QuotaExceededError) as ctx:
                await self._create(session, _hash(), uid=uid, daily=2)
            self.assertEqual((ctx.exception.scope, ctx.exception.used, ctx.exception.limit), ("daily", 2, 2))
            _, row = await self._create(session, _hash(), uid=uid, daily=3)
            self.assertEqual(row.state, "quarantined")

        self.run_db(body)

    def test_in_flight_quota_counts_only_processing_states(self):
        async def body(session):
            current = (await session.execute(text(
                "SELECT count(*) FROM research.report_upload WHERE state = ANY(CAST(:s AS text[]))"
            ), {"s": list(uploads.IN_FLIGHT_STATES)})).scalar_one()
            await _insert(session, _hash(), "draft")  # 已處理完，不算
            await _insert(session, _hash(), "scanning")
            with self.assertRaises(upload_intake.QuotaExceededError) as ctx:
                await upload_intake.check_quota(session, actor_id=None, daily_quota=10**6, max_in_flight=current + 1)
            self.assertEqual((ctx.exception.scope, ctx.exception.used), ("in_flight", current + 1))
            await upload_intake.check_quota(session, actor_id=None, daily_quota=10**6, max_in_flight=current + 2)

        self.run_db(body)

    def test_list_detail_and_scanner_summary(self):
        async def body(session):
            uid = (await session.execute(text(
                "INSERT INTO research.app_user (username, password_hash, role) "
                "VALUES (:u, '!test', 'admin') RETURNING id::text"
            ), {"u": f"upl_{secrets.token_hex(4)}"})).scalar_one()
            h = _hash()
            upload_id, _ = await self._create(session, h, uid=uid)
            await session.execute(text(
                "UPDATE research.report_upload SET scan_attempts = 2, scan_last_error = 'signatures_stale', "
                "uploaded_at = now() - interval '100 years', state_changed_at = now() + interval '100 years' "
                "WHERE id = CAST(:id AS uuid)"
            ), {"id": upload_id})
            total, rows = await upload_intake.list_uploads(session, state="quarantined", limit=500, offset=0)
            self.assertGreaterEqual(total, 1)
            mine = [r for r in rows if r.upload_id == upload_id]
            self.assertEqual(len(mine), 1)
            self.assertEqual(mine[0].uploaded_by, (await session.execute(text(
                "SELECT username FROM research.app_user WHERE id = CAST(:id AS uuid)"), {"id": uid})).scalar_one())
            _, rows_other = await upload_intake.list_uploads(session, state="published", limit=500)
            self.assertNotIn(upload_id, [r.upload_id for r in rows_other])
            s = await upload_intake.scanner_summary(session)
            self.assertGreaterEqual(s.pending, 1)
            self.assertGreater(s.oldest_pending_seconds, 99 * 365 * 86400)  # 最舊的就是這列
            self.assertEqual(s.last_error, "signatures_stale")
            self.assertIsNone(await upload_intake.get_upload_report(session, h))
            with self.assertRaises(upload_intake.UploadNotFoundError):
                await upload_intake.get_upload(session, str(uuid.uuid4()))
            with self.assertRaises(upload_intake.UploadNotFoundError):
                await upload_intake.get_upload(session, "not-a-uuid")

        self.run_db(body)


if __name__ == "__main__":
    unittest.main()
