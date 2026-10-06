"""上傳 worker 的狀態轉移全集、草稿原子性與清除守門（真 PostgreSQL；scripts/process_uploads.py）。

**這支會真的 commit**（worker 的每一步都是自己的交易，條件式 UPDATE 認領、commit 之後才刪檔——rollback 包起來
就測不到這些）。所以只在**拋棄式庫**上跑：連上之後先確認 `research.research_report` 與 `research.report_upload`
都是空的（CI 的 schema 契約 job 是剛 `alembic upgrade head` 的空庫），不是空的就 skip；`REPORT_MARK_REQUIRE_DB=1`
時 skip 改成紅燈。本機預設連到的是生產庫，那裡一定不是空的——這道前提就是不讓它在生產庫上 commit 的守門。
每題結束刪掉自己寫的列（稽核紀錄只能新增、刪不掉，留在拋棄式庫裡無妨）。

不連網、不載模型、不打 LLM、不連 clamd：掃描器、抽字、嵌入、標註、物件儲存、跳過名單的寫入端都是假物件；
只有一題讓入庫前檢查真的起子行程（含 /JavaScript 的 PDF）。

**草稿原子性的證明方式**：worker 用的 session 在每一次 commit 之後，從**另一條連線**查兩件事——
該研報是否已經能被 `visible_report_sql` 看見、`make db-audit` 的 `upload_draft_mismatch` 是否成立。
commit 是別的交易唯一看得到變化的時刻，所以每一個 commit 點都乾淨＝整個流程中沒有任何可見空窗。
發布之後同一個查詢要看得見（證明觀察者不是空轉）。
"""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import hashlib
import json
import os
import stat
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import pdf_samples as ps
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.services import upload_review, uploads
from app.services import upload_worker as uw
from app.services.clamd import EngineVersion, ScanResult
from app.services.extract import ExtractResult
from app.services.object_storage import original_object_key
from app.services.pdf_preflight import PreflightResult
from app.services.tagging import parse_tags
from app.services.upload_review import PurgeCandidate
from app.services.visibility import visible_report_sql
from scripts import db_audit
from scripts import process_uploads as pu
from scripts._claude_cli import LlmEnvironmentError

TAG_OK = {
    "market": "TW", "is_research": True, "confidence": 0.9, "instrument_types": ["equity"],
    "relates_stock": True, "relates_futures": False, "stock_targets": ["2330"], "futures_targets": [],
}
TEXT = "本報告討論半導體產業前景，維持買進評等。\n\n" * 40
ENGINE = EngineVersion(raw="ClamAV 1.4.3/27412/Mon Oct  5 08:30:42 2026", engine="1.4.3", db_version=27412,
                       db_date=datetime(2026, 10, 5, 8, 30, 42, tzinfo=timezone.utc))
OK = ScanResult("ok", engine=ENGINE)
FOUND = ScanResult("found", signature="Eicar-Test-Signature", engine=ENGINE)
TRANSIENT = ScanResult("error", kind="connection_refused", detail="127.0.0.1:3310 refused")
HEURISTIC = ScanResult("found", signature="Heuristics.Encrypted.PDF", engine=ENGINE)
DETERMINISTIC = ScanResult("error", kind="clamd_error", detail="stream: Can't parse ERROR", engine=ENGINE)
NAME = "券商甲_台積電(2330)研究報告.pdf"  # 檔名不帶日期：report_date 只能從 client_mtime 補
MTIME = datetime(2026, 8, 15, 9, 0, tzinfo=timezone.utc)
DRAFT_CHECK = next(c for c in db_audit.CHECKS if c.key == "upload_draft_mismatch").sql


def _skip_or_raise(exc: Exception | None, why: str) -> None:
    if os.getenv("REPORT_MARK_REQUIRE_DB"):
        raise AssertionError(why) from exc
    raise unittest.SkipTest(why)


def _engine():
    from app.services.db import DATABASE_URL

    return create_async_engine(DATABASE_URL, poolclass=NullPool)


class _FakeStorage:
    enabled = True

    def __init__(self):
        self.uploaded: list[str] = []
        self.deleted: list[str] = []

    def upload_file(self, path, key, *, expected_sha256):
        assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == expected_sha256
        self.uploaded.append(key)

    def delete(self, key):
        self.deleted.append(key)


class _FakeRecorder:
    def __init__(self):
        self.records: list[tuple] = []

    async def record(self, file_hash, reason, *, escalated=False):
        self.records.append((file_hash, reason))


class WorkerDbTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        async def check():
            eng = _engine()
            try:
                async with eng.connect() as conn:
                    n = (await conn.execute(text(
                        "SELECT (SELECT count(*) FROM research.research_report) "
                        "+ (SELECT count(*) FROM research.report_upload)"
                    ))).scalar_one()
            finally:
                await eng.dispose()
            return n

        try:
            n = asyncio.run(check())
        except Exception as exc:  # noqa: BLE001 — 連不上或還沒套 0008
            _skip_or_raise(exc, f"DB 不可用或尚未套 revision 0008（{type(exc).__name__}）")
        if n:
            _skip_or_raise(None, "語料表或上傳表不是空的：這組測試會 commit，只在拋棄式空庫上跑")

    # ── 夾具 ────────────────────────────────────────────────────────────

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.qroot, self.clean, self.tags, self.cache = (root / n for n in ("quarantine", "clean", "tags", "cache"))
        for d in (self.qroot, self.tags, self.cache):
            d.mkdir()
        self.hashes_out = root / "hashes"
        self.claude_lock = root / "claude.lock"
        self.hashes: set[str] = set()
        self.observations: list[tuple[int, int, int]] = []  # (exists, visible, mismatch)
        self.allow_visible = False
        self.notices: list = []
        self.scan_results: list[ScanResult] = []
        self.preflight_result: PreflightResult | None = PreflightResult(True, pages=1, chars=100)
        self.tag_queue: list = []
        self.extract_scanned = False
        self.breaker: str | None = None
        self.backfill: str | None = None
        self.storage = _FakeStorage()
        self.recorder = _FakeRecorder()

        test = self

        class Observed(AsyncSession):
            async def commit(self):
                await super().commit()
                await test._observe()

        self.engine = _engine()
        self.addCleanup(lambda: asyncio.run(self.engine.dispose()))
        factory = async_sessionmaker(self.engine, class_=Observed, expire_on_commit=False)

        async def recorder_factory():
            return self.recorder

        self.ctx = pu.Ctx(
            session_factory=factory, qroot=self.qroot, clean_dir=self.clean, lock_path=root / "round.lock",
            tags_dir=self.tags, cache_dir=self.cache, storage=self.storage, scanner=self._scan,
            notify=self.notices.append, preflight=self._preflight, backfill_probe=lambda: self.backfill,
            claude_lock_path=self.claude_lock, recorder_factory=recorder_factory,
        )
        for target, fn in (
            ("app.services.extract.extract_text", self._extract),
            ("app.services.embed.embed_texts", lambda chunks, batch_size=32: [[0.001] * 1024 for _ in chunks]),
            ("app.services.extraction.cache.write_record", lambda rec: None),
            ("scripts._ingest_core._tag_via_cli", self._tag),
            ("scripts.process_uploads.breaker_active", lambda: self.breaker),
            ("scripts.process_uploads.require_llm_key", lambda models: None),
        ):
            p = mock.patch(target, fn)
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self._cleanup_rows)

    def _scan(self, reader):
        while reader.read(65536):  # 讀完（真的 clamd 也會收完整份串流）
            pass
        return self.scan_results.pop(0) if self.scan_results else OK

    def _preflight(self, path):
        if self.preflight_result is None:
            from app.services.pdf_preflight import run_preflight

            return run_preflight(path)
        return self.preflight_result

    def _extract(self, path, *a, **k):
        sha = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        if self.extract_scanned:
            return ExtractResult(sha, "", 0, True, "zh", page_count=1)
        return ExtractResult(sha, TEXT, len(TEXT), False, "zh", page_count=1)

    def _tag(self, file_name, text_, *a, **k):
        item = self.tag_queue.pop(0) if self.tag_queue else TAG_OK
        if isinstance(item, BaseException):
            raise item
        if isinstance(item, str):  # 錯誤字串
            return None, item
        return parse_tags(json.dumps(item)), None

    async def _observe(self):
        if not self.hashes:
            return
        hs = sorted(self.hashes)
        async with self.engine.connect() as conn:
            exists = (await conn.execute(text(
                "SELECT count(*) FROM research.research_report r WHERE r.file_hash = ANY(CAST(:hs AS text[]))"
            ), {"hs": hs})).scalar_one()
            visible = (await conn.execute(text(
                "SELECT count(*) FROM research.research_report r WHERE r.file_hash = ANY(CAST(:hs AS text[])) "
                f"AND {visible_report_sql('r')}"
            ), {"hs": hs})).scalar_one()
            mismatch = (await conn.execute(text(DRAFT_CHECK))).scalar_one()
        self.observations.append((exists, visible, mismatch))

    def _cleanup_rows(self):
        async def go():
            if not self.hashes:
                return
            async with self.engine.begin() as conn:
                p = {"hs": sorted(self.hashes)}
                for table in ("research_report", "report_visibility", "extraction_log", "report_upload",
                              "llm_task_failure"):
                    await conn.execute(text(
                        f"DELETE FROM research.{table} WHERE file_hash = ANY(CAST(:hs AS text[]))"), p)

        asyncio.run(go())

    def q(self, sql: str, params: dict | None = None):
        async def go():
            async with self.engine.begin() as conn:
                res = await conn.execute(text(sql), params or {})
                return res.all() if res.returns_rows else []

        return asyncio.run(go())

    def add(self, data: bytes | None = None, *, name: str = NAME, state: str = "quarantined",
            place: str = "quarantine", **cols) -> tuple[str, str]:
        data = data if data is not None else ps.plain(marker=uuid.uuid4().hex)
        h = cols.pop("file_hash", None) or hashlib.sha256(data).hexdigest()
        self.hashes.add(h)
        uid = str(uuid.uuid4())
        if place == "quarantine":
            (self.qroot / f"{uid}.bin").write_bytes(data)
        cols.setdefault("client_mtime", MTIME)
        names = ", ".join(["id", "file_hash", "original_name", "size_bytes", "state", *cols])
        binds = ", ".join(["CAST(:id AS uuid)", ":h", ":name", ":size", ":state", *(f":{k}" for k in cols)])
        self.q(f"INSERT INTO research.report_upload ({names}) VALUES ({binds})",
               {"id": uid, "h": h, "name": name, "size": len(data), "state": state, **cols})
        return uid, h

    def row(self, uid: str) -> dict:
        r = self.q("SELECT * FROM research.report_upload WHERE id = CAST(:id AS uuid)", {"id": uid})[0]
        return dict(r._mapping)

    def audits(self, uid: str) -> list[tuple]:
        return [tuple(r) for r in self.q(
            "SELECT action, actor_user_id, detail FROM research.admin_audit_log WHERE target_id = :id ORDER BY id",
            {"id": uid})]

    def scan(self) -> int:
        return pu.cmd_scan(argparse.Namespace(limit=50), self.ctx)

    def ingest(self) -> int:
        return pu.cmd_ingest(argparse.Namespace(limit=20, hashes_out=str(self.hashes_out)), self.ctx)

    def cleanup(self) -> int:
        return pu.cmd_cleanup(argparse.Namespace(), self.ctx)

    def assert_never_visible(self):
        self.assertTrue(self.observations, "觀察者沒有被呼叫：worker 沒經過被觀察的 session commit")
        for exists, visible, mismatch in self.observations:
            self.assertEqual(visible, 0, f"commit 當下研報已可見：{self.observations}")
            self.assertEqual(mismatch, 0, f"commit 當下 upload_draft_mismatch 成立：{self.observations}")

    # ── 正常路徑與草稿原子性 ─────────────────────────────────────────────

    def test_happy_path_draft_is_never_visible_until_published(self):
        uid, h = self.add()
        self.assertEqual(self.scan(), 0)
        r = self.row(uid)
        self.assertEqual(r["state"], "clean")
        self.assertEqual(r["scan_engine"], "ClamAV 1.4.3/27412/2026-10-05T08:30:42Z")
        self.assertIsNotNone(r["scanned_at"])

        self.assertEqual(self.ingest(), 0)
        r = self.row(uid)
        self.assertEqual((r["state"], r["failure_kind"], r["process_attempts"]), ("draft", None, 1))
        self.assertIsNotNone(r["processed_at"])
        self.assert_never_visible()
        self.assertIn((1, 0, 0), self.observations, "要觀察到「研報已在庫、仍不可見」的那個 commit 點")

        dst = self.clean / h / NAME
        self.assertTrue(dst.exists())
        self.assertFalse((self.qroot / f"{uid}.bin").exists())
        self.assertEqual(stat.S_IMODE(dst.stat().st_mode), uw.CLEAN_FILE_MODE)
        rep = self.q("SELECT file_name, file_path, report_date, source_object_key FROM research.research_report "
                     "WHERE file_hash = :h", {"h": h})[0]
        self.assertEqual(rep[0], NAME, "顯示名稱與 parse_filename 用原始檔名，不是 hash")
        self.assertEqual(rep[1], str(dst))
        self.assertEqual(rep[2], MTIME.date(), "檔名沒日期時 report_date 由 client_mtime 補")
        self.assertEqual(rep[3], original_object_key(h, NAME))
        self.assertEqual(self.storage.uploaded, [original_object_key(h, NAME)])
        vis = self.q("SELECT hidden, publication, published_at FROM research.report_visibility WHERE file_hash = :h",
                     {"h": h})[0]
        self.assertEqual(tuple(vis), (False, "draft", None))
        self.assertEqual(self.hashes_out.read_text(), h)

        # 觀察者不是空轉：發布之後同一個查詢看得見。
        async def publish():
            async with self.ctx.session_factory() as s:
                self.allow_visible = True
                await upload_review.publish(s, uid, actor_id=None)
                await s.commit()

        asyncio.run(publish())
        self.assertEqual(self.observations[-1], (1, 1, 0))

    def test_recovery_of_killed_round(self):
        scanning, _ = self.add(state="scanning")
        processing, _ = self.add(state="processing", process_attempts=1)
        stuck, _ = self.add(state="processing", process_attempts=uw.MAX_PROCESS_ATTEMPTS)

        async def go():
            async with self.ctx.session_factory() as s:
                return await uw.recover_stale(s)

        rec = asyncio.run(go())
        self.assertEqual((rec.scanning, rec.processing, rec.gave_up), (1, 1, 1))
        self.assertEqual(self.row(scanning)["state"], "quarantined")
        self.assertEqual(self.row(processing)["state"], "clean")
        r = self.row(stuck)
        self.assertEqual((r["state"], r["failure_kind"]), ("failed", "ingest_error"))
        self.assertTrue(uploads.is_retryable_failure(r["failure_kind"]), "管理員可以重試")

    # ── 掃描 ────────────────────────────────────────────────────────────

    def test_infected_is_isolated_with_evidence_audit_and_notice(self):
        uid, h = self.add()
        self.scan_results = [FOUND]
        self.assertEqual(self.scan(), 0)
        r = self.row(uid)
        self.assertEqual((r["state"], r["scan_signature"]), ("infected", "Eicar-Test-Signature"))
        self.assertAlmostEqual((r["purge_after"] - datetime.now(timezone.utc)).days, 29, delta=1)
        evidence = self.qroot / "infected" / f"{uid}.bin"
        self.assertTrue(evidence.exists())
        self.assertFalse((self.qroot / f"{uid}.bin").exists())
        self.assertEqual(stat.S_IMODE(evidence.stat().st_mode), 0o400)
        self.assertEqual(stat.S_IMODE(evidence.parent.stat().st_mode), 0o700)
        audits = self.audits(uid)
        self.assertEqual([(a[0], a[1]) for a in audits], [("upload.infected", None)])
        self.assertEqual(set(audits[0][2]), {"upload_id", "file_name", "size_bytes", "state"})
        self.assertEqual([(n.upload_id, n.signature) for n in self.notices], [(uid, "Eicar-Test-Signature")])
        self.assertEqual(self.ingest(), 0)
        self.assertEqual(self.row(uid)["state"], "infected", "感染件永遠不入庫")

    def test_heuristic_match_is_blocked_not_infected(self):
        """Heuristics.*（加密、超過掃描上限）是規則攔截不是病毒碼：blocked＋scan_heuristic，簽章照記，不發感染通知，
        同一份檔重傳不會被 422 known_infected 擋；證據保留 30 天後由清除步驟刪檔。"""
        uid, h = self.add()
        self.scan_results = [HEURISTIC]
        self.assertEqual(self.scan(), 0)
        r = self.row(uid)
        self.assertEqual((r["state"], r["failure_kind"], r["scan_signature"]),
                         ("blocked", "scan_heuristic", "Heuristics.Encrypted.PDF"))
        self.assertIsNotNone(r["purge_after"])
        self.assertEqual(self.notices, [], "規則攔截不發感染通知")
        self.assertTrue((self.qroot / f"{uid}.bin").exists())
        self.assertFalse((self.qroot / "infected" / f"{uid}.bin").exists())
        self.assertEqual([a[0] for a in self.audits(uid)], ["upload.blocked"])

        async def conflict():
            from app.services import upload_intake

            async with self.ctx.session_factory() as s:
                try:
                    await upload_intake.find_conflict(s, h)
                finally:
                    await s.rollback()

        asyncio.run(conflict())  # 不拋 KnownInfectedError
        self.q("UPDATE research.report_upload SET purge_after = now() - interval '1 second' "
               "WHERE id = CAST(:id AS uuid)", {"id": uid})
        self.assertEqual(self.cleanup(), 0)
        self.assertFalse((self.qroot / f"{uid}.bin").exists())
        self.assertEqual([a[0] for a in self.audits(uid)], ["upload.blocked", "upload.evidence_purged"])

    def test_hash_mismatch_and_missing_file_are_blocked(self):
        tampered, _ = self.add(file_hash="0" * 64)  # 檔案內容的 SHA 與 DB 不符
        missing, _ = self.add(place="none")
        self.assertEqual(self.scan(), 0)
        for uid in (tampered, missing):
            r = self.row(uid)
            self.assertEqual((r["state"], r["failure_kind"]), ("blocked", "hash_mismatch"))
            self.assertEqual([a[0] for a in self.audits(uid)], ["upload.blocked"])

    def test_scanner_unavailable_keeps_files_quarantined_and_stops(self):
        first, _ = self.add()
        second, _ = self.add()
        self.scan_results = [TRANSIENT]
        self.assertEqual(self.scan(), 0)
        r = self.row(first)
        self.assertEqual((r["state"], r["scan_attempts"]), ("quarantined", 1))
        self.assertIn("connection_refused", r["scan_last_error"])
        r2 = self.row(second)
        self.assertEqual((r2["state"], r2["scan_attempts"]), ("quarantined", 0), "同一輪不再拿其他檔去撞")
        self.assertTrue((self.qroot / f"{first}.bin").exists())
        for _ in range(3):  # 永不放行：再壞幾輪也只是 attempts 往上加
            self.scan_results = [TRANSIENT]
            self.scan()
        self.assertEqual(self.row(first)["state"], "quarantined")

    def test_deterministic_errors_block_on_third_attempt(self):
        uid, _ = self.add()
        for expected in ("quarantined", "quarantined", "blocked"):
            self.scan_results = [DETERMINISTIC]
            self.scan()
            self.assertEqual(self.row(uid)["state"], expected)
        r = self.row(uid)
        self.assertTrue(r["scan_last_error"].startswith("[決定性 3/3] clamd_error"))
        self.assertEqual(r["scan_attempts"], 3)

    # ── 入庫的各種終點 ───────────────────────────────────────────────────

    def test_preflight_rejections(self):
        for kind in ("active_content", "encrypted", "too_many_pages", "extract_timeout"):
            with self.subTest(kind=kind):
                uid, _ = self.add(state="clean")
                self.preflight_result = PreflightResult(False, kind, f"{kind} 細節")
                self.ingest()
                r = self.row(uid)
                self.assertEqual((r["state"], r["failure_kind"]), ("failed", kind))
                self.assertTrue((self.qroot / f"{uid}.bin").exists(), "沒通過檢查的檔留在隔離區，不搬正")
        self.assert_never_visible()

    def test_real_preflight_subprocess_rejects_javascript(self):
        uid, _ = self.add(ps.page_aa_js(marker=uuid.uuid4().hex), state="clean")
        self.preflight_result = None  # 真的起子行程
        self.ingest()
        r = self.row(uid)
        self.assertEqual((r["state"], r["failure_kind"]), ("failed", "active_content"))
        self.assertIn("/JavaScript", r["failure_detail"])

    def test_outcome_failures_and_skip_list(self):
        cases = [
            ("admin", dict(name="2026年連續假期公告.pdf"), None, "admin_file"),
            ("scanned", {}, "scanned", "scanned"),
            ("not_research", {}, {**TAG_OK, "is_research": False, "market": None}, "not_research"),
            ("blocked", {}, "API[content_filter] 400 Content Exists Risk", "tag_blocked"),
            ("truncated", {}, "API[truncated] finish_reason=length", "tag_truncated"),
            ("cli", {}, "CLI 逾時", "tag_failed"),
        ]
        for label, kw, tag, kind in cases:
            with self.subTest(case=label):
                uid, h = self.add(state="clean", **kw)
                self.extract_scanned = tag == "scanned"
                self.tag_queue = [] if tag in (None, "scanned") else [tag]
                self.ingest()
                r = self.row(uid)
                self.assertEqual((r["state"], r["failure_kind"]), ("failed", kind))
                self.assertFalse(self.q("SELECT 1 FROM research.research_report WHERE file_hash = :h", {"h": h}))
                self.assertFalse(self.q("SELECT 1 FROM research.report_visibility WHERE file_hash = :h", {"h": h}))
        self.assertEqual(sorted(r for _, r in self.recorder.records), ["content_filter", "truncated"],
                         "被擋與截斷的標註照 sync 記入跳過名單")
        self.assert_never_visible()

    def test_ingest_error_then_retry_from_clean_dir(self):
        uid, h = self.add(state="clean")
        with mock.patch("app.services.embed.embed_texts", side_effect=RuntimeError("嵌入失敗")):
            self.ingest()
        r = self.row(uid)
        self.assertEqual((r["state"], r["failure_kind"]), ("failed", "ingest_error"))
        self.assertTrue((self.clean / h / NAME).exists(), "通過檢查的檔已搬正，重試不必再過一次子行程")

        async def retry():
            async with self.ctx.session_factory() as s:
                await upload_review.retry(s, uid, actor_id=None, max_in_flight=50)
                await s.commit()

        asyncio.run(retry())
        self.preflight_result = PreflightResult(False, "active_content", "重試時不應再呼叫子行程")
        self.ingest()
        r = self.row(uid)
        self.assertEqual((r["state"], r["process_attempts"]), ("draft", 2))
        self.assert_never_visible()

    def test_duplicate_when_corpus_already_has_hash(self):
        data = ps.plain(marker=uuid.uuid4().hex)
        h = hashlib.sha256(data).hexdigest()
        self.hashes.add(h)
        nas = Path(self._tmp.name) / "研報自動匯入" / NAME
        nas.parent.mkdir()
        nas.write_bytes(data)
        from scripts._ingest_core import ingest_one

        async def nas_ingest():
            async with self.ctx.session_factory() as s:
                return await ingest_one(s, nas, storage=self.storage, tags_dir=self.tags)

        self.allow_visible = True
        self.assertEqual(asyncio.run(nas_ingest()).kind, "ingested")
        uid, _ = self.add(data, state="clean")
        self.ingest()
        self.assertEqual(self.row(uid)["state"], "duplicate")
        self.assertFalse(self.q("SELECT 1 FROM research.report_visibility WHERE file_hash = :h", {"h": h}),
                         "duplicate 不碰 visibility：NAS 進來的研報維持已發布")
        self.assertFalse((self.qroot / f"{uid}.bin").exists())

    # ── LLM 互斥與延後 ───────────────────────────────────────────────────

    def test_breaker_active_defers_without_processing(self):
        a, _ = self.add(state="clean")
        b, _ = self.add(state="clean")
        self.breaker = "ts=2026-10-07T00:00:00+00:00\nreason=逾時"
        self.assertEqual(self.ingest(), 0)
        for uid in (a, b):
            r = self.row(uid)
            self.assertEqual((r["state"], r["failure_kind"], r["process_attempts"]), ("clean", "llm_breaker", 0))

    def test_breaker_tripping_mid_round_defers_the_rest(self):
        a, _ = self.add(state="clean")
        b, _ = self.add(state="clean")

        def trip(*args, **kw):
            self.breaker = "reason=最近 10 次有 5 次逾時"
            raise LlmEnvironmentError("LLM 斷路器跳脫")

        with mock.patch("scripts._ingest_core._tag_via_cli", side_effect=trip):
            self.assertEqual(self.ingest(), 0)
        for uid in (a, b):
            r = self.row(uid)
            self.assertEqual((r["state"], r["failure_kind"]), ("clean", "llm_breaker"))
        self.assert_never_visible()

    def test_llm_environment_error_returns_to_clean_rc2(self):
        uid, _ = self.add(state="clean")
        with mock.patch("scripts._ingest_core._tag_via_cli", side_effect=LlmEnvironmentError("API[auth] 401")):
            self.assertEqual(self.ingest(), 2)
        r = self.row(uid)
        self.assertEqual((r["state"], r["failure_kind"]), ("clean", None))
        self.assertIn("401", r["failure_detail"])

    def test_claude_lock_busy_keeps_clean(self):
        uid, _ = self.add(state="clean")
        fd = os.open(self.claude_lock, os.O_RDWR | os.O_CREAT, 0o644)
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with self.assertRaises(SystemExit) as cm:
            self.ingest()
        self.assertEqual(cm.exception.code, 75)
        r = self.row(uid)
        self.assertEqual((r["state"], r["process_attempts"]), ("clean", 0))

    def test_backfill_running_only_scans(self):
        clean, _ = self.add(state="clean")
        fresh, _ = self.add()
        self.backfill = "pid=1 正在跑 backfill_extraction.py"
        self.scan()
        self.assertEqual(self.ingest(), 0)
        self.assertEqual(self.row(fresh)["state"], "clean", "掃描照做")
        r = self.row(clean)
        self.assertEqual((r["state"], r["process_attempts"]), ("clean", 0), "入庫延到 backfill 結束")

    # ── 清除 ────────────────────────────────────────────────────────────

    def _draft(self) -> tuple[str, str]:
        uid, h = self.add(state="clean")
        self.ingest()
        self.assertEqual(self.row(uid)["state"], "draft")
        return uid, h

    def _reject(self, uid: str, *, expired: bool = True):
        async def go():
            async with self.ctx.session_factory() as s:
                await upload_review.reject(s, uid, reason="內容不適合", actor_id=None, grace_hours=24)
                await s.commit()

        asyncio.run(go())
        if expired:
            self.q("UPDATE research.report_upload SET purge_after = now() - interval '1 second' "
                   "WHERE id = CAST(:id AS uuid)", {"id": uid})

    def test_rejected_draft_is_purged_with_all_traces(self):
        uid, h = self._draft()
        (self.cache / f"{h}.json").write_text("{}")
        self.assertTrue((self.tags / f"{h}.json").exists())
        self._reject(uid, expired=False)
        self.assertEqual(self.cleanup(), 0)
        self.assertIsNone(self.row(uid)["purged_at"], "寬限期內不清")
        self.q("UPDATE research.report_upload SET purge_after = now() - interval '1 second' "
               "WHERE id = CAST(:id AS uuid)", {"id": uid})
        self.assertEqual(self.cleanup(), 0)
        self.assertIsNotNone(self.row(uid)["purged_at"])
        for table in ("research_report", "report_visibility", "extraction_log"):
            self.assertFalse(self.q(f"SELECT 1 FROM research.{table} WHERE file_hash = :h", {"h": h}), table)
        self.assertFalse((self.tags / f"{h}.json").exists())
        self.assertFalse((self.cache / f"{h}.json").exists())
        self.assertFalse((self.clean / h).exists())
        self.assertEqual(self.storage.deleted, [original_object_key(h, NAME)])
        purge = [a for a in self.audits(uid) if a[0] == "upload.purge"]
        self.assertEqual(len(purge), 1)
        self.assertIsNone(purge[0][1])
        self.assertTrue(purge[0][2]["corpus_purged"])
        self.assert_never_visible()

    def test_published_report_is_never_purged(self):
        """守門：語料裡的研報已發布過（NAS 進來的、沒有 visibility 列）。就算清除候選說可以，DELETE 也不動它。"""
        data = ps.plain(marker=uuid.uuid4().hex)
        h = hashlib.sha256(data).hexdigest()
        self.hashes.add(h)
        nas = Path(self._tmp.name) / "nas" / NAME
        nas.parent.mkdir()
        nas.write_bytes(data)
        from scripts._ingest_core import ingest_one

        async def nas_ingest():
            async with self.ctx.session_factory() as s:
                return await ingest_one(s, nas, storage=self.storage, tags_dir=self.tags)

        self.allow_visible = True
        asyncio.run(nas_ingest())
        uid, _ = self.add(data, state="rejected", decision_reason="退回",
                          purge_after=datetime.now(timezone.utc) - timedelta(hours=1))
        (self.cache / f"{h}.json").write_text("{}")
        forged = [PurgeCandidate(upload_id=uid, file_hash=h, purge_after=datetime.now(timezone.utc),
                                 corpus_purgeable=True)]

        async def lying_list(session, *, limit=50):
            return forged

        with mock.patch.object(uw, "list_purgeable", lying_list):
            self.assertEqual(self.cleanup(), 0)
        self.assertTrue(self.q("SELECT 1 FROM research.research_report WHERE file_hash = :h", {"h": h}))
        self.assertTrue(self.q("SELECT 1 FROM research.extraction_log WHERE file_hash = :h", {"h": h}))
        self.assertTrue((self.tags / f"{h}.json").exists())
        self.assertTrue((self.cache / f"{h}.json").exists())
        self.assertEqual(self.storage.deleted, [])
        self.assertFalse((self.qroot / f"{uid}.bin").exists(), "守門不成立時只刪這筆上傳自己的隔離區檔")
        r = self.row(uid)
        self.assertIsNotNone(r["purged_at"])
        purge = [a for a in self.audits(uid) if a[0] == "upload.purge"]
        self.assertFalse(purge[0][2]["corpus_purged"])

    def test_corpus_purge_waits_for_claude_lock(self):
        uid, h = self._draft()
        self._reject(uid)
        fd = os.open(self.claude_lock, os.O_RDWR | os.O_CREAT, 0o644)
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.assertEqual(self.cleanup(), 0)
        self.assertIsNone(self.row(uid)["purged_at"])
        self.assertTrue(self.q("SELECT 1 FROM research.research_report WHERE file_hash = :h", {"h": h}))
        fcntl.flock(fd, fcntl.LOCK_UN)
        self.assertEqual(self.cleanup(), 0)
        self.assertIsNotNone(self.row(uid)["purged_at"])
        self.assertFalse(self.q("SELECT 1 FROM research.research_report WHERE file_hash = :h", {"h": h}))

    def test_infected_evidence_purged_after_retention_metadata_kept(self):
        uid, _ = self.add()
        self.scan_results = [FOUND]
        self.scan()
        evidence = self.qroot / "infected" / f"{uid}.bin"
        self.assertEqual(self.cleanup(), 0)
        self.assertTrue(evidence.exists(), "保留期內不清")
        self.q("UPDATE research.report_upload SET purge_after = now() - interval '1 second' "
               "WHERE id = CAST(:id AS uuid)", {"id": uid})
        self.assertEqual(self.cleanup(), 0)
        self.assertFalse(evidence.exists())
        r = self.row(uid)
        self.assertEqual((r["state"], r["scan_signature"]), ("infected", "Eicar-Test-Signature"))
        self.assertIsNotNone(r["purged_at"])
        self.assertEqual([a[0] for a in self.audits(uid)], ["upload.infected", "upload.evidence_purged"])

    def test_orphans_without_rows_are_removed(self):
        uid, _ = self.add()
        orphan = self.qroot / f"{uuid.uuid4()}.bin"
        orphan.write_bytes(b"x")
        old = datetime.now(timezone.utc).timestamp() - 2 * uw.ORPHAN_MIN_AGE_SECONDS
        for p in (orphan, self.qroot / f"{uid}.bin"):
            os.utime(p, (old, old))
        self.assertEqual(self.cleanup(), 0)
        self.assertFalse(orphan.exists())
        self.assertTrue((self.qroot / f"{uid}.bin").exists(), "有 DB 列的不是孤兒")


if __name__ == "__main__":
    unittest.main()
