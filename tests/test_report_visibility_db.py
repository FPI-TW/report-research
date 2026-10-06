"""研報隱藏／恢復對真的 PostgreSQL 成立（revision 0004 ＋ app/services/visibility.py）。

`tests/test_visibility_guard.py` 只保證每條使用者讀取 SQL 都呼叫了可見性片段；這裡驗片段本身
真的有效：兩篇研報（含 chunk 與訊號）隱藏其一之後，混合檢索（dense＋字面兩路）、檢索頁瀏覽、
問答選篇、閱讀頁、相似研報、觀點雷達、總覽、每日簡報的來源（含評等變動的「上次」）與原檔
presign 都看不到它；恢復後全部回來；重新入庫（`store.upsert_report` 先刪後插、換新 report_id）
之後隱藏狀態仍在。另驗隱藏與稽核同一筆交易、管理清單看得到隱藏狀態。

跑在 CI 的「schema 契約」job；本機沒有 DB（或庫還沒套 revision 0004）就 skip。**一律 rollback、
絕不 commit**——本機預設連到的是生產庫。`upsert_report` 本身會 commit，測試把那個 session 的
commit 換成 flush。斷言只針對自己塞進去的列（以 file_hash／report_id 辨識），不假設庫是空的：
向量用與查詢完全相同的方向（距離 ≈ 0）、字面用語料裡不存在的詞，在完整語料上也排得到前面。
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import unittest
import uuid
from datetime import date, datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import text

from app.services import answer, brief, overview, store, visibility
from app.services.radar import queries as radar
from app.services.reading import queries as reading
from app.services.retrieval import hybrid_search, rank_reports
from web.routers import report_file

DIM = 1024
TERM = "zqvisprobe"  # 語料裡不存在的字面詞
CODE = "ZQV9"  # 測試專用的標的代號
FUTURE = date(2099, 1, 2)  # 讓瀏覽模式（report_date 新→舊）把自己的列排在最前面


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


def _vec(seed: int) -> list[float]:
    """與 _query_vec() 幾乎同方向的向量（cosine 距離 ≈ 0），每個 chunk 帶一點差異。"""
    v = [0.0] * DIM
    v[0] = 1.0
    v[1 + seed] = 0.001
    return v


def _query_vec() -> list[float]:
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


async def _ingest(session, name: str, file_hash: str, report_date: date) -> str:
    row = store.ReportRow(
        file_hash=file_hash, file_name=f"{name}.pdf", file_path=f"/nonexistent/{name}.pdf",
        market="TW", is_research=True, confidence=1.0, stock_code=CODE, company_name="可見性測試公司",
        source=f"測試券商{name}", report_date=report_date, report_type="個股",
        stock_targets=[CODE], instrument_types=["stock"], relates_stock=True, full_text=f"{TERM} 全文 {name}",
    )
    chunks = [f"{TERM} 可見性測試 {name} 第{i}段" for i in range(3)]
    with _NoCommit(session):
        rid = await store.upsert_report(session, row, chunks, [_vec(i) for i in range(3)])
    # 標題由批次（generate_titles.py）另外補，入庫時沒有；標的名重探靠它。
    await session.execute(
        text("UPDATE research.research_report SET title = :t WHERE id = CAST(:id AS uuid)"),
        {"t": f"{TERM}{name} 報告", "id": rid},
    )
    return rid


_INSERT_SIGNAL = text(
    "INSERT INTO research.report_signal (id, report_id, market, instrument_code, broker, report_date, "
    "rating_normalized, extraction_version, extraction_status) "
    "VALUES (:id, CAST(:rid AS uuid), 'TW', :code, '同一券商', :rdate, :rating, 'test', 'valid')"
)


class ReportVisibilityDbTests(unittest.TestCase):
    def _run(self, fn):
        try:
            return asyncio.run(_with_session(fn))
        except (unittest.SkipTest, AssertionError):
            raise
        except Exception as exc:
            msg = repr(exc)
            if "UndefinedTable" in msg or "UndefinedColumn" in msg or "does not exist" in msg:
                _skip_or_raise(exc, "庫尚未套用 revision 0004")
            _skip_or_raise(exc, f"DB 不可用（{type(exc).__name__}）")

    def test_hidden_report_disappears_from_every_user_path_and_survives_reingest(self):
        tag = uuid.uuid4().hex
        hash_a = hashlib.sha256(f"a-{tag}".encode()).hexdigest()
        hash_b = hashlib.sha256(f"b-{tag}".encode()).hexdigest()

        async def fn(session):
            # 兩篇研報；A 的訊號較舊（買進）、B 較新（中立）＝同一券商的評等變動。
            rid_a = await _ingest(session, "A", hash_a, FUTURE - timedelta(days=1))
            rid_b = await _ingest(session, "B", hash_b, FUTURE)
            for rid, rdate, rating in ((rid_a, FUTURE - timedelta(days=1), "buy"), (rid_b, FUTURE, "neutral")):
                await session.execute(_INSERT_SIGNAL, {
                    "id": uuid.uuid4(), "rid": rid, "code": CODE, "rdate": rdate, "rating": rating,
                })
            now = datetime.now(timezone.utc)
            window = (now - timedelta(hours=1), now + timedelta(hours=1))

            async def snapshot(rid_a_now: str) -> dict:
                """各使用者讀取路徑看到的『自己那兩篇』。"""
                ids = {rid_a_now: "A", rid_b: "B"}
                hashes = {hash_a: "A", hash_b: "B"}
                out: dict = {}
                scored = await hybrid_search(session, TERM, _query_vec(), k=10, dense_scan=200)
                out["hybrid"] = {hashes[r.file_hash] for _, _, r in scored if r.file_hash in hashes}
                dense = await store.search_chunks_meta(session, _query_vec(), scan=200)
                out["dense"] = {hashes[r.file_hash] for r in dense if r.file_hash in hashes}
                lex, _ = await store.search_chunks_lexical(session, _query_vec(), [f"%{TERM}%"], cap=2000)
                out["lexical"] = {hashes[r.file_hash] for r in lex if r.file_hash in hashes}
                out["rank"] = {ids[g.report_id] for g in rank_reports(scored) if g.report_id in ids}
                sources, _ctx = answer.build_context(scored, now=now)
                out["select"] = {ids[s.report_id] for s in sources if s.report_id in ids}
                out["lead_term"] = await store.pick_title_lead_term(session, [f"{TERM}A", f"{TERM}B"])
                _total, rows = await store.list_reports(session, limit=100)
                out["browse"] = {hashes[r[1]] for r in rows if r[1] in hashes}
                out["doc"] = {n for h, n in hashes.items() if await reading.fetch_doc(session, h) is not None}
                out["chunk"] = {n for r, n in ids.items() if await reading.fetch_chunk_content(session, r, 0)}
                out["signals"] = {n for r, n in ids.items() if await reading.fetch_signals(session, r)}
                similar = await reading.fetch_similar(session, rid_b) + await reading.fetch_similar(session, rid_a_now)
                out["similar"] = {hashes[s.file_hash] for s in similar if s.file_hash in hashes}
                radar_sigs = await radar.fetch_instrument_signals(session, "TW", CODE)
                out["radar"] = {ids[s.report_id] for s in radar_sigs if s.report_id in ids}
                out["coverage"] = (await radar.fetch_coverage_counts(session, "TW", CODE)).reports_available
                page = await radar.list_radar_instruments(session, q=CODE, limit=None)
                out["catalog"] = [(r.instrument_code, r.report_count) for r in page.items if r.instrument_code == CODE]
                facets = await overview.aggregate_facets(session, overview.OverviewFilters(stock_code=CODE))
                out["overview"] = facets.total
                win = await brief.fetch_window_reports(session, *window, limit=500)
                out["brief"] = {hashes[r.file_hash] for r in win if r.file_hash in hashes}
                out["brief_count_delta"] = await brief.count_window_reports(session, *window)
                linked = await brief.fetch_reports_by_ids(session, [rid_a_now, rid_b])
                out["brief_links"] = {hashes[r.file_hash] for r in linked}
                changes = await brief.fetch_signal_changes(
                    session, *window, max_report_age_days=365 * 200,
                )
                out["changes"] = [(c.rating_from, c.rating_to) for c in changes if c.instrument_code == CODE]
                presign = set()
                for r, n in ids.items():
                    try:
                        await report_file._fetch_report(session, r)
                        presign.add(n)
                    except HTTPException as exc:
                        assert exc.status_code == 404, exc.status_code
                out["presign"] = presign
                return out

            before = await snapshot(rid_a)
            state = await visibility.set_visibility(
                session, hash_a, hidden=True, reason="測試：版權疑慮", actor_id=None,
            )
            hidden = await snapshot(rid_a)
            audit = (await session.execute(text(
                "SELECT action, detail FROM research.admin_audit_log WHERE target_id = :h ORDER BY id"
            ), {"h": hash_a})).all()
            _t, admin_rows = await visibility.list_reports(session, q=f"{TERM}A", hidden=True)
            await visibility.set_visibility(session, hash_a, hidden=False, reason=None, actor_id=None)
            restored = await snapshot(rid_a)
            # 再隱藏一次，然後重新入庫同一個 file_hash：report_id 換新，旗標必須還在。
            await visibility.set_visibility(session, hash_a, hidden=True, reason="再次隱藏", actor_id=None)
            new_rid_a = await _ingest(session, "A", hash_a, FUTURE - timedelta(days=1))
            reingested = await snapshot(new_rid_a)
            return before, state, hidden, audit, admin_rows, restored, rid_a, new_rid_a, reingested

        before, state, hidden, audit, admin_rows, restored, rid_a, new_rid_a, reingested = self._run(fn)

        set_keys = ("hybrid", "dense", "lexical", "rank", "select", "browse", "doc", "chunk", "signals",
                    "similar", "radar", "brief", "brief_links", "presign")
        for key in set_keys:
            with self.subTest(path=key, phase="before"):
                self.assertEqual(before[key], {"A", "B"})
            with self.subTest(path=key, phase="hidden"):
                self.assertEqual(hidden[key], {"B"})
            with self.subTest(path=key, phase="restored"):
                self.assertEqual(restored[key], {"A", "B"})
        # 數字類：隱藏後各少一篇。
        self.assertEqual(hidden["coverage"], before["coverage"] - 1)
        self.assertEqual(hidden["overview"], before["overview"] - 1)
        self.assertEqual(hidden["brief_count_delta"], before["brief_count_delta"] - 1)
        self.assertEqual(before["catalog"], [(CODE, 2)])
        self.assertEqual(hidden["catalog"], [(CODE, 1)])
        self.assertEqual(restored["coverage"], before["coverage"])
        self.assertEqual(restored["overview"], before["overview"])
        # 標的名重探：A 的標題前綴只在隱藏前後找得到（B 的不受影響）。
        self.assertIn(before["lead_term"], {f"{TERM}A", f"{TERM}B"})
        self.assertEqual(hidden["lead_term"], f"{TERM}B")
        # 評等變動：A 被隱藏時既不是「這次」也不是 B 的「上次」，所以整筆消失。
        self.assertEqual(before["changes"], [("buy", "neutral")])
        self.assertEqual(hidden["changes"], [])
        self.assertEqual(restored["changes"], [("buy", "neutral")])

        self.assertTrue(state.hidden)
        self.assertEqual(state.reason, "測試：版權疑慮")
        # 稽核同交易寫入，detail 不含原因全文。
        self.assertEqual(audit[0][0], "report.hide")
        self.assertEqual(audit[0][1]["has_reason"], True)
        self.assertNotIn("版權疑慮", str(audit[0][1]))
        # 管理清單：關鍵字＋隱藏篩選只找到 A，帶著隱藏原因。
        self.assertEqual([r.file_hash for r in admin_rows], [hash_a])
        self.assertTrue(admin_rows[0].hidden)
        self.assertEqual(admin_rows[0].reason, "測試：版權疑慮")

        # 重新入庫換了 report_id，隱藏狀態仍在。
        self.assertNotEqual(rid_a, new_rid_a)
        for key in set_keys:
            with self.subTest(path=key, phase="reingested"):
                self.assertEqual(reingested[key], {"B"})

    def test_hide_requires_existing_report_and_reason(self):
        async def fn(session):
            missing = hashlib.sha256(uuid.uuid4().bytes).hexdigest()
            with self.assertRaises(visibility.ReportNotFoundError):
                await visibility.set_visibility(session, missing, hidden=True, reason="x", actor_id=None)
            with self.assertRaises(visibility.ReportNotFoundError):
                await visibility.set_visibility(session, "not-a-hash", hidden=True, reason="x", actor_id=None)
            tag = uuid.uuid4().hex
            h = hashlib.sha256(f"c-{tag}".encode()).hexdigest()
            await _ingest(session, "C", h, FUTURE)
            with self.assertRaises(visibility.InvalidReasonError):
                await visibility.set_visibility(session, h, hidden=True, reason="   ", actor_id=None)
            with self.assertRaises(visibility.InvalidReasonError):
                await visibility.set_visibility(session, h, hidden=True, reason="長" * 501, actor_id=None)
            # DB 的 CHECK 是第二道防線：繞過服務層直接寫空原因的隱藏也會被拒。
            try:
                async with session.begin_nested():
                    await session.execute(text(
                        "INSERT INTO research.report_visibility (file_hash, hidden, reason) VALUES (:h, true, '')"
                    ), {"h": h})
            except Exception as exc:  # noqa: BLE001 — 只關心「被拒」
                return repr(exc)
            return None

        err = self._run(fn)
        self.assertIsNotNone(err)
        self.assertIn("report_visibility_hidden_needs_reason", err)


if __name__ == "__main__":
    unittest.main()
