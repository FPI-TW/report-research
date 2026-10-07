"""研報上傳的審核 SQL 對真的 PostgreSQL 成立（revision 0008 ＋ app/services/upload_review.py）。

驗：發布前草稿在混合檢索（dense＋字面）與可見性片段都查不到、發布後查得到；每個寫入端點的狀態閘門；
狀態與稽核同一筆交易（稽核失敗時狀態與 visibility 都不變）；退回後草稿仍不可見、visibility 列不動；
撤銷退回依事實推導回正確狀態（對每個可退回狀態做「退回 → 撤銷」來回）、過期與已清除拒絕、撞 partial
unique index 轉 409；重試只限可重試類；預覽與原檔指標；清除守門（已發布過的研報永遠不會被判可清除）。

兩組測試：

- `UploadReviewDbTests`：**一律 rollback、絕不 commit**——本機預設連到的是生產庫。`store.upsert_report`
  本身會 commit，測試把那個 session 的 commit 換成 flush。斷言只針對自己塞進去的列（隨機 file_hash）。
- `UploadReviewConcurrencyTests`：兩個交易真的並發（第二個在 report_upload 的列鎖上等，第一個 commit 後
  條件式 UPDATE 影響 0 列）。另一個連線看得到的資料必須先 commit，所以這組**只在拋棄式的庫上跑**：
  開始時 `research_report` 與 `report_upload` 都必須是空的（剛 `alembic upgrade head` 的庫，例如 CI 的
  schema job 或本機 devdb 上的暫存庫）；不是空的就 skip（`REPORT_MARK_REQUIRE_DB=1` 時直接失敗）。
  自己 commit 的列在 finally 刪掉；稽核列只能新增（觸發器擋 DELETE），留在拋棄式的庫裡。

跑在 CI 的「schema 契約」job；本機沒有 DB（或庫還沒套 revision 0008）就 skip。不載 BGE-M3：向量用與查詢
完全相同的方向、字面用語料裡不存在的詞。
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import secrets
import unittest
import uuid
from datetime import date, datetime, timedelta, timezone
from unittest import mock

from sqlalchemy import text

from app.services import accounts, store, upload_review, uploads
from app.services.retrieval import hybrid_search
from app.services.textnorm import clean_extracted
from app.services.upload_intake import UploadNotFoundError
from app.services.visibility import visible_report_sql

DIM = 1024
TERM = "zqreviewprobe"  # 語料裡不存在的字面詞
CJK_TEXT = f"{TERM} 台 積 電 第 三 季\n\n營 收 成 長"


def _skip_or_raise(exc: Exception, why: str) -> None:
    if os.getenv("REPORT_MARK_REQUIRE_DB"):
        raise exc
    raise unittest.SkipTest(f"{why}：{exc}")


def _engine(**kw):
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.services.db import DATABASE_URL

    return create_async_engine(DATABASE_URL, echo=False, pool_pre_ping=True, **kw)


async def _with_session(fn):
    from sqlalchemy.ext.asyncio import AsyncSession

    eng = _engine()
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


def _vec(seed: int = 0) -> list[float]:
    v = [0.0] * DIM
    v[0] = 1.0
    if seed:
        v[seed] = 0.001
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


async def _insert_upload(session, file_hash, state, **extra) -> str:
    cols = {"file_hash": file_hash, "original_name": "審核測試.pdf", "size_bytes": 1234, "state": state, **extra}
    if state == uploads.STATE_REJECTED:
        cols.setdefault("decision_reason", "測試")
    if state == uploads.STATE_INFECTED:
        cols.setdefault("scan_signature", "Eicar-Test-Signature")
    uuid_cols = {"uploaded_by", "decided_by"}
    names = ", ".join(cols)
    values = ", ".join(f"CAST(:{k} AS uuid)" if k in uuid_cols else f":{k}" for k in cols)
    return (await session.execute(
        text(f"INSERT INTO research.report_upload ({names}) VALUES ({values}) RETURNING id::text"), cols,
    )).scalar_one()


async def _ingest_draft(session, file_hash, *, name="審核測試", object_key=None) -> str:
    """worker 的做法：同一個 session 先寫草稿標記，再 upsert（commit 換成 flush）。回 report_id。"""
    await session.execute(
        text("INSERT INTO research.report_visibility (file_hash, hidden, publication) VALUES (:h, false, 'draft')"),
        {"h": file_hash},
    )
    row = store.ReportRow(
        file_hash=file_hash, file_name=f"{name}.pdf", file_path=f"/nonexistent/{name}.pdf", market="TW",
        is_research=True, confidence=0.9, stock_code="ZQR1", company_name="審核測試公司", source="測試券商",
        report_date=date(2099, 1, 2), report_type="個股", language="zh", stock_targets=["ZQR1"],
        instrument_types=["stock"], relates_stock=True, full_text=CJK_TEXT, source_object_key=object_key,
    )
    chunks = [f"{TERM} 審核測試 {name} 第{i}段" for i in range(2)]
    with _NoCommit(session):
        return await store.upsert_report(session, row, chunks, [_vec(i + 1) for i in range(2)])


async def _insert_report(session, file_hash, *, name="nas.pdf") -> None:
    await session.execute(
        text("INSERT INTO research.research_report (id, file_hash, file_name, file_path) "
             "VALUES (gen_random_uuid(), :h, :n, '/nonexistent/x.pdf')"),
        {"h": file_hash, "n": name},
    )


async def _searchable(session, file_hash) -> dict:
    scored = await hybrid_search(session, TERM, _vec(), k=50, dense_scan=400)
    visible = (await session.execute(
        text(f"SELECT count(*) FROM research.research_report r WHERE r.file_hash = :h AND {visible_report_sql('r')}"),
        {"h": file_hash},
    )).scalar_one()
    return {"hybrid": file_hash in {r.file_hash for _, _, r in scored}, "fragment": visible == 1}


async def _state(session, upload_id) -> tuple:
    return (await session.execute(
        text("SELECT state, decision_reason, purge_after, decided_by::text, failure_kind, failure_detail, "
             "process_attempts FROM research.report_upload WHERE id = CAST(:id AS uuid)"),
        {"id": upload_id},
    )).one()


async def _visibility(session, file_hash):
    return (await session.execute(
        text("SELECT publication, published_at, published_by::text, hidden FROM research.report_visibility "
             "WHERE file_hash = :h"),
        {"h": file_hash},
    )).first()


async def _audits(session, upload_id) -> list:
    return (await session.execute(
        text("SELECT action, actor_user_id::text, detail FROM research.admin_audit_log WHERE target_id = :id "
             "ORDER BY id"),
        {"id": upload_id},
    )).all()


async def _broken_audit(*_a, **_k):
    raise RuntimeError("audit down")


def _probe_schema():
    async def probe(session):
        return (await session.execute(text("SELECT to_regclass('research.report_upload')"))).scalar()

    try:
        exists = asyncio.run(_with_session(probe))
    except Exception as exc:  # 連不上 DB
        _skip_or_raise(exc, "連不上 DB")
    if not exists:
        _skip_or_raise(RuntimeError("research.report_upload 不存在"), "庫還沒套 revision 0008")


class UploadReviewDbTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _probe_schema()

    def run_db(self, fn):
        return asyncio.run(_with_session(fn))

    # ── 發布 ────────────────────────────────────────────────────────────

    def test_publish_makes_draft_searchable(self):
        async def body(session):
            h = _hash()
            actor = str(uuid.uuid4())
            uploader = str(uuid.uuid4())
            await session.execute(
                text("INSERT INTO research.app_user (id, username, password_hash, role) "
                     "VALUES (CAST(:id AS uuid), :u, 'x', 'admin')"),
                {"id": actor, "u": f"pub_{actor[:8]}"},
            )
            await _ingest_draft(session, h)
            upload_id = await _insert_upload(session, h, "draft", uploaded_by=uploader)
            self.assertEqual(await _searchable(session, h), {"hybrid": False, "fragment": False})

            row = await upload_review.publish(session, upload_id, actor_id=actor)

            self.assertEqual((row.state, row.decided_by), ("published", f"pub_{actor[:8]}"))
            self.assertIsNotNone(row.decided_at)
            self.assertEqual(await _searchable(session, h), {"hybrid": True, "fragment": True})
            pub, published_at, published_by, hidden = await _visibility(session, h)
            self.assertEqual((pub, published_by, hidden), ("published", actor, False))
            self.assertIsNotNone(published_at)
            audits = await _audits(session, upload_id)
            self.assertEqual([(a[0], a[1]) for a in audits], [("upload.publish", actor)])
            self.assertEqual(audits[0][2], {
                "upload_id": upload_id, "file_hash": h, "file_name": "審核測試.pdf", "uploaded_by": uploader,
                "published_by": actor, "self_published": False, "previous_state": "draft", "state": "published",
                "hidden": False,
            })
            # 第二次發布：已發布 → 409，不再寫稽核
            with self.assertRaises(upload_review.UploadStateConflictError) as ctx:
                await upload_review.publish(session, upload_id, actor_id=actor)
            self.assertEqual(ctx.exception.state, "published")
            self.assertEqual(len(await _audits(session, upload_id)), 1)

        self.run_db(body)

    def test_self_publish_is_recorded(self):
        async def body(session):
            h = _hash()
            me = str(uuid.uuid4())
            await _ingest_draft(session, h)
            upload_id = await _insert_upload(session, h, "draft", uploaded_by=me)
            await upload_review.publish(session, upload_id, actor_id=me)
            self.assertTrue((await _audits(session, upload_id))[0][2]["self_published"])

        self.run_db(body)

    def test_publish_gate_only_draft(self):
        async def body(session):
            for state in uploads.STATES:
                if state == uploads.STATE_DRAFT:
                    continue
                with self.subTest(state=state):
                    h = _hash()
                    upload_id = await _insert_upload(session, h, state)
                    with self.assertRaises(upload_review.UploadStateConflictError) as ctx:
                        await upload_review.publish(session, upload_id, actor_id=None)
                    self.assertEqual(ctx.exception.state, state)
                    self.assertEqual((await _state(session, upload_id))[0], state)
            with self.assertRaises(UploadNotFoundError):
                await upload_review.publish(session, str(uuid.uuid4()), actor_id=None)
            with self.assertRaises(UploadNotFoundError):
                await upload_review.publish(session, "not-a-uuid", actor_id=None)

        self.run_db(body)

    def test_publish_without_draft_marker_rolls_back(self):
        """草稿標記不一致（沒有 visibility 列、或語料沒有研報）：拋錯，呼叫端 rollback 後 upload 仍是 draft。"""
        async def body(session):
            no_marker, no_report = _hash(), _hash()
            await _insert_report(session, no_marker)  # 語料有、但沒有草稿標記（等同已發布）
            await session.execute(
                text("INSERT INTO research.report_visibility (file_hash, hidden, publication) "
                     "VALUES (:h, false, 'draft')"), {"h": no_report},
            )
            for h in (no_marker, no_report):
                with self.subTest(h=h[:8]):
                    upload_id = await _insert_upload(session, h, "draft")
                    with self.assertRaises(upload_review.UploadStateConflictError):
                        async with session.begin_nested():
                            await upload_review.publish(session, upload_id, actor_id=None)
                    self.assertEqual((await _state(session, upload_id))[0], "draft")
                    self.assertEqual(await _audits(session, upload_id), [])
            self.assertEqual((await _visibility(session, no_report))[0], "draft")

        self.run_db(body)

    # ── 稽核與狀態同一筆交易 ─────────────────────────────────────────────

    def test_audit_failure_leaves_state_untouched(self):
        async def body(session):
            h_pub, h_rej, h_unrej, h_retry = (_hash() for _ in range(4))
            await _ingest_draft(session, h_pub, name="pub")
            pub_id = await _insert_upload(session, h_pub, "draft")
            rej_id = await _insert_upload(session, h_rej, "quarantined")
            unrej_id = await _insert_upload(session, h_unrej, "rejected", purge_after=datetime.now(timezone.utc)
                                            + timedelta(hours=1))
            retry_id = await _insert_upload(session, h_retry, "failed", failure_kind="tag_failed")
            calls = {
                pub_id: lambda: upload_review.publish(session, pub_id, actor_id=None),
                rej_id: lambda: upload_review.reject(session, rej_id, reason="x", actor_id=None, grace_hours=24),
                unrej_id: lambda: upload_review.unreject(session, unrej_id, actor_id=None),
                retry_id: lambda: upload_review.retry(session, retry_id, actor_id=None),
            }
            before = {uid: await _state(session, uid) for uid in calls}
            with mock.patch.object(accounts, "record_audit", _broken_audit):
                for uid, call in calls.items():
                    with self.subTest(upload=before[uid][0]):
                        with self.assertRaises(RuntimeError):
                            async with session.begin_nested():  # 代表呼叫端的交易：失敗就整段 rollback
                                await call()
                        self.assertEqual(await _state(session, uid), before[uid])
            self.assertEqual((await _visibility(session, h_pub))[:3], ("draft", None, None))
            self.assertEqual(await _searchable(session, h_pub), {"hybrid": False, "fragment": False})

        self.run_db(body)

    # ── 退回 ────────────────────────────────────────────────────────────

    def test_reject_draft_keeps_it_invisible(self):
        async def body(session):
            h = _hash()
            actor = str(uuid.uuid4())
            await _ingest_draft(session, h)
            upload_id = await _insert_upload(session, h, "draft")
            reason = "  內容與檔名不符，請重新上傳  "

            row = await upload_review.reject(session, upload_id, reason=reason, actor_id=actor, grace_hours=24)

            self.assertEqual((row.state, row.decision_reason), ("rejected", reason.strip()))
            state, _, purge_after, decided_by, *_ = await _state(session, upload_id)
            self.assertEqual(decided_by, actor)
            now = (await session.execute(text("SELECT now()"))).scalar_one()
            self.assertEqual(purge_after - now, timedelta(hours=24))
            # 語料、草稿標記都不動：仍不可見，visibility 仍是從未發布的草稿
            self.assertEqual((await _visibility(session, h))[:3], ("draft", None, None))
            self.assertEqual(await _searchable(session, h), {"hybrid": False, "fragment": False})
            audits = await _audits(session, upload_id)
            self.assertEqual([a[0] for a in audits], ["upload.reject"])
            detail = audits[0][2]
            self.assertEqual(detail, {
                "upload_id": upload_id, "file_hash": h, "file_name": "審核測試.pdf", "previous_state": "draft",
                "state": "rejected", "has_reason": True, "reason_chars": len(reason.strip()),
                "purge_after": purge_after.isoformat(),
            })
            self.assertNotIn("內容與檔名不符", str(detail))  # 原因全文不進稽核

        self.run_db(body)

    def test_reject_grace_hours_is_honoured(self):
        async def body(session):
            upload_id = await _insert_upload(session, _hash(), "clean", scanned_at=datetime.now(timezone.utc))
            await upload_review.reject(session, upload_id, reason="x", actor_id=None, grace_hours=3)
            purge_after = (await _state(session, upload_id))[2]
            now = (await session.execute(text("SELECT now()"))).scalar_one()
            self.assertEqual(purge_after - now, timedelta(hours=3))

        self.run_db(body)

    def test_reject_gate_table(self):
        async def body(session):
            expected = {
                **{s: None for s in uploads.REJECTABLE_STATES},
                "scanning": upload_review.UploadBusyError,
                "processing": upload_review.UploadBusyError,
                "published": upload_review.UploadPublishedError,
                "infected": upload_review.UploadStateConflictError,
                "blocked": upload_review.UploadStateConflictError,
                "duplicate": upload_review.UploadStateConflictError,
                "rejected": upload_review.UploadStateConflictError,
            }
            self.assertEqual(set(expected), set(uploads.STATES))
            for state, err in expected.items():
                with self.subTest(state=state):
                    h = _hash()
                    if state == uploads.STATE_DRAFT:
                        await _ingest_draft(session, h, name=f"gate-{h[:6]}")
                    upload_id = await _insert_upload(session, h, state)
                    if err is None:
                        row = await upload_review.reject(session, upload_id, reason="r", actor_id=None,
                                                         grace_hours=24)
                        self.assertEqual(row.state, "rejected")
                    else:
                        with self.assertRaises(err) as ctx:
                            await upload_review.reject(session, upload_id, reason="r", actor_id=None,
                                                       grace_hours=24)
                        self.assertEqual(ctx.exception.state, state)
                        self.assertEqual((await _state(session, upload_id))[0], state)
                        self.assertEqual(await _audits(session, upload_id), [])

        self.run_db(body)

    def test_reject_draft_refused_when_corpus_was_published(self):
        """draft 的上傳，語料卻是已發布（NAS 送來或 visibility 已發布）：退回會讓清除刪到已發布研報 → 拒絕。"""
        async def body(session):
            nas, vis_pub, ever = _hash(), _hash(), _hash()
            await _insert_report(session, nas)  # 沒有 visibility 列＝已發布
            await _insert_report(session, vis_pub)
            await session.execute(text("INSERT INTO research.report_visibility (file_hash, hidden, publication) "
                                       "VALUES (:h, false, 'published')"), {"h": vis_pub})
            await _insert_report(session, ever)  # 草稿但 published_at 有值（曾被發布過）
            await session.execute(text("INSERT INTO research.report_visibility (file_hash, hidden, publication, "
                                       "published_at) VALUES (:h, false, 'draft', now())"), {"h": ever})
            for h in (nas, vis_pub, ever):
                with self.subTest(h=h[:8]):
                    upload_id = await _insert_upload(session, h, "draft")
                    with self.assertRaises(upload_review.UploadStateConflictError):
                        await upload_review.reject(session, upload_id, reason="r", actor_id=None, grace_hours=24)
                    self.assertEqual((await _state(session, upload_id))[0], "draft")

        self.run_db(body)

    def test_reject_reason_validation(self):
        async def body(session):
            upload_id = await _insert_upload(session, _hash(), "quarantined")
            for bad in ("", "   ", "字" * 501, None):
                with self.subTest(bad=bad if bad is None else len(bad)):
                    with self.assertRaises(upload_review.InvalidReasonError):
                        await upload_review.reject(session, upload_id, reason=bad, actor_id=None, grace_hours=24)
            row = await upload_review.reject(session, upload_id, reason="字" * 500, actor_id=None, grace_hours=24)
            self.assertEqual(len(row.decision_reason), 500)

        self.run_db(body)

    # ── 撤銷退回 ────────────────────────────────────────────────────────

    def test_unreject_round_trip_restores_each_rejectable_state(self):
        async def body(session):
            now = datetime.now(timezone.utc)
            scenarios = {
                "draft": dict(scanned_at=now, processed_at=now),
                "failed": dict(scanned_at=now, failure_kind="not_research", failure_detail="非研究"),
                "clean": dict(scanned_at=now),
                "clean-deferred": dict(scanned_at=now, failure_kind="llm_breaker"),
                "quarantined": dict(scan_last_error="signatures_stale"),
            }
            for label, extra in scenarios.items():
                state = label.split("-")[0]
                with self.subTest(state=label):
                    h = _hash()
                    if state == "draft":
                        await _ingest_draft(session, h, name=f"rt-{h[:6]}")
                    upload_id = await _insert_upload(session, h, state, **extra)
                    await upload_review.reject(session, upload_id, reason="誤退", actor_id=None, grace_hours=24)
                    row = await upload_review.unreject(session, upload_id, actor_id=None)
                    self.assertEqual(row.state, state)
                    st, reason, purge_after, decided_by, kind, *_ = await _state(session, upload_id)
                    self.assertEqual((st, reason, purge_after, decided_by), (state, None, None, None))
                    self.assertEqual(kind, extra.get("failure_kind"))  # failure_kind 原樣保留
                    audits = await _audits(session, upload_id)
                    self.assertEqual([a[0] for a in audits], ["upload.reject", "upload.unreject"])
                    self.assertEqual(audits[1][2]["state"], state)
                    self.assertEqual(audits[1][2]["previous_state"], "rejected")
            # draft 回來之後語料仍是草稿、仍不可見
        self.run_db(body)

    def test_unreject_rejects_expired_purged_and_wrong_state(self):
        async def body(session):
            past = datetime.now(timezone.utc) - timedelta(seconds=1)
            future = datetime.now(timezone.utc) + timedelta(hours=1)
            expired = await _insert_upload(session, _hash(), "rejected", purge_after=past)
            purged = await _insert_upload(session, _hash(), "rejected", purge_after=future, purged_at=past)
            no_deadline = await _insert_upload(session, _hash(), "rejected")
            for uid in (expired, purged, no_deadline):
                with self.subTest(uid=uid):
                    with self.assertRaises(upload_review.RejectExpiredError):
                        await upload_review.unreject(session, uid, actor_id=None)
                    self.assertEqual((await _state(session, uid))[0], "rejected")
            for state in ("draft", "failed", "published", "quarantined"):
                with self.subTest(state=state):
                    uid = await _insert_upload(session, _hash(), state)
                    with self.assertRaises(upload_review.UploadStateConflictError):
                        await upload_review.unreject(session, uid, actor_id=None)
            with self.assertRaises(UploadNotFoundError):
                await upload_review.unreject(session, str(uuid.uuid4()), actor_id=None)

        self.run_db(body)

    def test_unreject_conflicts_with_newer_active_upload(self):
        async def body(session):
            h = _hash()
            old = await _insert_upload(session, h, "quarantined")
            await upload_review.reject(session, old, reason="重傳", actor_id=None, grace_hours=24)
            await _insert_upload(session, h, "quarantined")  # 寬限期內有人重新上傳了同一份
            with self.assertRaises(upload_review.ActiveUploadConflictError):
                await upload_review.unreject(session, old, actor_id=None)
            # savepoint 吸收了 unique violation：交易仍可用、舊的那筆仍是 rejected、沒有多寫稽核
            self.assertEqual((await _state(session, old))[0], "rejected")
            self.assertEqual([a[0] for a in await _audits(session, old)], ["upload.reject"])

        self.run_db(body)

    # ── 重試 ────────────────────────────────────────────────────────────

    def test_retry(self):
        async def body(session):
            for kind in uploads.RETRYABLE_FAILURE_KINDS:
                with self.subTest(kind=kind):
                    uid = await _insert_upload(session, _hash(), "failed", failure_kind=kind,
                                               failure_detail="細節", process_attempts=2)
                    row = await upload_review.retry(session, uid, actor_id=None)
                    self.assertEqual(row.state, "clean")
                    st, *_, kind_after, detail_after, attempts = await _state(session, uid)
                    self.assertEqual((st, kind_after, detail_after, attempts), ("clean", None, None, 2))
                    audit = (await _audits(session, uid))[0]
                    self.assertEqual(audit[0], "upload.retry")
                    self.assertEqual(audit[2]["failure_kind"], kind)
                    self.assertEqual(audit[2]["process_attempts"], 2)
            for kind in set(uploads.FAILURE_KINDS) - set(uploads.RETRYABLE_FAILURE_KINDS) | {None}:
                with self.subTest(kind=kind):
                    uid = await _insert_upload(session, _hash(), "failed", failure_kind=kind)
                    with self.assertRaises(upload_review.NotRetryableError) as ctx:
                        await upload_review.retry(session, uid, actor_id=None)
                    self.assertEqual(ctx.exception.failure_kind, kind)
                    self.assertEqual((await _state(session, uid))[0], "failed")
            for state in set(uploads.STATES) - {"failed"}:
                with self.subTest(state=state):
                    uid = await _insert_upload(session, _hash(), state, failure_kind="tag_failed")
                    with self.assertRaises(upload_review.UploadStateConflictError):
                        await upload_review.retry(session, uid, actor_id=None)

        self.run_db(body)

    def test_retry_conflicts_with_newer_active_upload(self):
        async def body(session):
            h = _hash()
            uid = await _insert_upload(session, h, "failed", failure_kind="ingest_error")
            await _insert_upload(session, h, "quarantined")
            with self.assertRaises(upload_review.ActiveUploadConflictError):
                await upload_review.retry(session, uid, actor_id=None)
            self.assertEqual((await _state(session, uid))[0], "failed")
            self.assertEqual(await _audits(session, uid), [])

        self.run_db(body)

    # ── 預覽與原檔 ──────────────────────────────────────────────────────

    def test_preview_and_original(self):
        async def body(session):
            h = _hash()
            key = f"originals/{h[:2]}/{h}.pdf"
            rid = await _ingest_draft(session, h, object_key=key)
            upload_id = await _insert_upload(session, h, "draft")

            p = await upload_review.get_preview(session, upload_id)
            canonical = clean_extracted(CJK_TEXT)
            self.assertEqual((p.report_id, p.publication, p.hidden), (rid, "draft", False))
            self.assertEqual((p.title, p.summary, p.takeaways_state, p.takeaways), (None, None, "pending", []))
            self.assertEqual(p.text, canonical)
            self.assertNotEqual(p.text, CJK_TEXT)  # 正典文字，不是原始抽取
            self.assertEqual(p.text_sha256, hashlib.sha256(canonical.encode()).hexdigest())
            self.assertEqual((p.market, p.stock_targets, p.instrument_types), ("TW", ["ZQR1"], ["stock"]))

            await session.execute(text("UPDATE research.research_report SET title = '台積電', summary = '摘要' "
                                       "WHERE id = CAST(:id AS uuid)"), {"id": rid})
            await session.execute(
                text("INSERT INTO research.report_takeaway (id, report_id, ordinal, claim, quote, quote_start, "
                     "quote_end, anchor_method, text_sha256, extraction_version, extraction_status) "
                     "VALUES (gen_random_uuid(), CAST(:rid AS uuid), 1, '論點', '引文', 0, 3, 'exact', :sha, 't', "
                     "'valid')"),
                {"rid": rid, "sha": p.text_sha256},
            )
            p2 = await upload_review.get_preview(session, upload_id)
            self.assertEqual((p2.title, p2.summary, p2.takeaways_state), ("台積電", "摘要", "ready"))
            self.assertEqual((p2.takeaways[0].quote_start, p2.takeaways[0].quote_end), (0, 3))

            ref = await upload_review.get_original(session, upload_id)
            self.assertEqual((ref.file_hash, ref.object_key, ref.file_name), (h, key, "審核測試.pdf"))

            for state in set(uploads.STATES) - set(upload_review.PREVIEWABLE_STATES):
                with self.subTest(state=state):
                    uid = await _insert_upload(session, _hash(), state)
                    with self.assertRaises(upload_review.UploadStateConflictError):
                        await upload_review.get_preview(session, uid)
                    with self.assertRaises(upload_review.UploadStateConflictError):
                        await upload_review.get_original(session, uid)
            missing = await _insert_upload(session, _hash(), "published")
            with self.assertRaises(upload_review.ReportMissingError):
                await upload_review.get_preview(session, missing)

        self.run_db(body)

    # ── 清除守門 ────────────────────────────────────────────────────────

    def test_purge_guard_never_selects_published_corpus(self):
        async def body(session):
            past = datetime.now(timezone.utc) - timedelta(minutes=1)
            future = datetime.now(timezone.utc) + timedelta(hours=1)
            cases: dict[str, tuple[str, bool | None]] = {}

            h = _hash()
            await _ingest_draft(session, h, name="purge-draft")
            cases["draft_corpus"] = (await _insert_upload(session, h, "rejected", purge_after=past), True)

            h = _hash()
            cases["no_corpus"] = (await _insert_upload(session, h, "rejected", purge_after=past), True)

            h = _hash()
            await _insert_report(session, h)  # NAS：沒有 visibility 列＝已發布
            cases["nas_published"] = (await _insert_upload(session, h, "rejected", purge_after=past), False)

            h = _hash()
            await _insert_report(session, h)
            await session.execute(text("INSERT INTO research.report_visibility (file_hash, hidden, publication, "
                                       "reason, published_at) VALUES (:h, true, 'published', '隱藏', now())"), {"h": h})
            cases["published_hidden"] = (await _insert_upload(session, h, "rejected", purge_after=past), False)

            h = _hash()
            await session.execute(text("INSERT INTO research.report_visibility (file_hash, hidden, publication, "
                                       "published_at) VALUES (:h, false, 'draft', now())"), {"h": h})
            cases["ever_published"] = (await _insert_upload(session, h, "rejected", purge_after=past), False)

            h = _hash()
            await _ingest_draft(session, h, name="purge-newer")
            old = await _insert_upload(session, h, "rejected", purge_after=past)
            await _insert_upload(session, h, "draft")  # 語料屬於較新的那筆
            cases["newer_active"] = (old, False)

            h = _hash()
            cases["in_grace"] = (await _insert_upload(session, h, "rejected", purge_after=future), None)
            h = _hash()
            cases["already_purged"] = (await _insert_upload(session, h, "rejected", purge_after=past,
                                                            purged_at=past), None)

            got = {c.upload_id: c.corpus_purgeable for c in await upload_review.list_purgeable(session, limit=100000)}
            for label, (uid, expected) in cases.items():
                with self.subTest(case=label):
                    if expected is None:
                        self.assertNotIn(uid, got)
                    else:
                        self.assertEqual(got.get(uid), expected)

        self.run_db(body)


class UploadReviewConcurrencyTests(unittest.TestCase):
    """兩個交易同時對同一筆上傳做條件式 UPDATE：只有一個成功。只在拋棄式（語料與上傳表皆空）的庫上跑。"""

    @classmethod
    def setUpClass(cls):
        _probe_schema()

        async def counts(session):
            return (await session.execute(text(
                "SELECT (SELECT count(*) FROM research.research_report), (SELECT count(*) FROM research.report_upload)"
            ))).one()

        reports, uploads_ = asyncio.run(_with_session(counts))
        if reports or uploads_:
            _skip_or_raise(
                RuntimeError(f"research_report={reports}、report_upload={uploads_} 不是空的"),
                "並發測試要 commit，只在剛 upgrade head 的拋棄式庫上跑",
            )

    def _race(self, setup, first, second):
        """setup(session) 建資料並 commit；first 先拿到列鎖（不 commit），second 在鎖上等；first commit 後收 second。"""
        from sqlalchemy.ext.asyncio import AsyncSession

        async def main():
            eng = _engine(pool_size=4)
            hashes: list[str] = []
            try:
                async with AsyncSession(eng, expire_on_commit=False) as s0:
                    upload_id = await setup(s0, hashes)
                    await s0.commit()
                async with AsyncSession(eng) as sa, AsyncSession(eng) as sb, AsyncSession(eng) as watch:
                    res_a = await first(sa, upload_id)  # 持有 report_upload 的列鎖
                    pid_b = (await sb.execute(text("SELECT pg_backend_pid()"))).scalar_one()

                    async def run_b():
                        try:
                            return await second(sb, upload_id)
                        except Exception as exc:  # noqa: BLE001 — 結果交給斷言
                            return exc

                    task_b = asyncio.create_task(run_b())
                    for _ in range(200):
                        waiting = (await watch.execute(
                            text("SELECT wait_event_type FROM pg_stat_activity WHERE pid = :p"), {"p": pid_b},
                        )).scalar()
                        await watch.rollback()
                        if waiting == "Lock":
                            break
                        await asyncio.sleep(0.05)
                    else:
                        task_b.cancel()
                        raise AssertionError("第二個交易沒有在列鎖上等待")
                    await sa.commit()
                    res_b = await asyncio.wait_for(task_b, timeout=30)
                    if isinstance(res_b, Exception):
                        await sb.rollback()
                    else:
                        await sb.commit()
                    final = (await watch.execute(
                        text("SELECT state FROM research.report_upload WHERE id = CAST(:id AS uuid)"),
                        {"id": upload_id},
                    )).scalar_one()
                    audits = (await watch.execute(
                        text("SELECT action FROM research.admin_audit_log WHERE target_id = :id ORDER BY id"),
                        {"id": upload_id},
                    )).scalars().all()
                    vis = (await watch.execute(
                        text("SELECT publication FROM research.report_visibility WHERE file_hash = :h"),
                        {"h": hashes[0]},
                    )).scalar()
                    return res_a, res_b, final, audits, vis
            finally:
                async with AsyncSession(eng) as cleanup:
                    for h in hashes:
                        await cleanup.execute(text("DELETE FROM research.report_upload WHERE file_hash = :h"), {"h": h})
                        await cleanup.execute(text("DELETE FROM research.research_report WHERE file_hash = :h"),
                                              {"h": h})
                        await cleanup.execute(text("DELETE FROM research.report_visibility WHERE file_hash = :h"),
                                              {"h": h})
                    await cleanup.commit()
                await eng.dispose()

        return asyncio.run(main())

    @staticmethod
    async def _draft(session, hashes):
        h = _hash()
        hashes.append(h)
        await _insert_report(session, h, name="race.pdf")
        await session.execute(
            text("INSERT INTO research.report_visibility (file_hash, hidden, publication) VALUES (:h, false, 'draft')"),
            {"h": h},
        )
        return await _insert_upload(session, h, "draft")

    @staticmethod
    def _publish(session, upload_id):
        return upload_review.publish(session, upload_id, actor_id=str(uuid.uuid4()))

    @staticmethod
    def _reject(session, upload_id):
        return upload_review.reject(session, upload_id, reason="並發", actor_id=str(uuid.uuid4()), grace_hours=24)

    def test_publish_publish(self):
        res_a, res_b, final, audits, vis = self._race(self._draft, self._publish, self._publish)
        self.assertEqual(res_a.state, "published")
        self.assertIsInstance(res_b, upload_review.UploadStateConflictError)
        self.assertEqual((final, audits, vis), ("published", ["upload.publish"], "published"))

    def test_publish_then_reject(self):
        res_a, res_b, final, audits, vis = self._race(self._draft, self._publish, self._reject)
        self.assertEqual(res_a.state, "published")
        self.assertIsInstance(res_b, upload_review.UploadPublishedError)
        self.assertEqual((final, audits, vis), ("published", ["upload.publish"], "published"))

    def test_reject_then_publish(self):
        res_a, res_b, final, audits, vis = self._race(self._draft, self._reject, self._publish)
        self.assertEqual(res_a.state, "rejected")
        self.assertIsInstance(res_b, upload_review.UploadStateConflictError)
        self.assertEqual((final, audits, vis), ("rejected", ["upload.reject"], "draft"))

    def test_reject_reject(self):
        res_a, res_b, final, audits, vis = self._race(self._draft, self._reject, self._reject)
        self.assertEqual(res_a.state, "rejected")
        self.assertIsInstance(res_b, upload_review.UploadStateConflictError)
        self.assertEqual((final, audits, vis), ("rejected", ["upload.reject"], "draft"))


if __name__ == "__main__":
    unittest.main()
