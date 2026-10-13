"""待複核佇列的三條查詢對真的 PostgreSQL 成立（`web/routers/review.py` 的 `_fetch`）。

`tests/test_review_api.py` 的假 session 只回放結果；這裡驗的是查詢本身：jsonb 分數的
安全 cast（畸形一列不得讓整支端點炸掉）、門檻與 active／stopped 過濾、`make_interval`
窗期、以及 count 與當頁是同一個條件。

跑在 CI 的「schema 契約」job；本機沒有 DB 就 skip。**一律 rollback、絕不 commit**——本機
連到的是測試環境的真實資料庫。斷言只針對自己塞進去的列（以 id 辨識），不假設庫是空的。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import unittest
import uuid

from sqlalchemy import text

from web.routers import review


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


_INSERT_QA = text(
    "INSERT INTO research.qa_log (id, question, answer, active, stopped, feedback, evaluation, created_at) "
    "VALUES (:id, :q, '答', :active, :stopped, :feedback, CAST(:evaluation AS jsonb), "
    "now() - make_interval(days => :age))"
)


class ReviewQueueDbTests(unittest.TestCase):
    def _run(self, fn):
        try:
            return asyncio.run(_with_session(fn))
        except (unittest.SkipTest, AssertionError):
            raise
        except Exception as exc:
            msg = repr(exc)
            if "UndefinedTable" in msg or "UndefinedColumn" in msg or "does not exist" in msg:
                _skip_or_raise(exc, "既有庫尚未套用 schema")
            _skip_or_raise(exc, f"DB 不可用（{type(exc).__name__}）")

    def test_qa_kinds_filter_correctly_and_survive_malformed_evaluation(self):
        ids = {name: uuid.uuid4() for name in (
            "low", "ok", "malformed", "no_eval", "inactive", "stopped", "old", "disliked", "liked",
        )}

        from app.config import get_settings

        judge = get_settings().faithfulness_model  # 待複核只計現行 judge（缺 judge_model 的舊列視為 haiku）

        def ev(score):
            return {"faithfulness_score": score, "judge_model": judge}

        async def fn(session):
            async def add(name, *, evaluation=None, feedback=None, active=True, stopped=False, age=1):
                await session.execute(_INSERT_QA, {
                    "id": ids[name], "q": name, "active": active, "stopped": stopped, "feedback": feedback,
                    "evaluation": json.dumps(evaluation) if evaluation is not None else None, "age": age,
                })

            await add("low", evaluation=ev(0.05))
            await add("ok", evaluation=ev(0.99))
            # 分數不是數字：不該被當成低分，更不該讓查詢拋 cast 例外。
            await add("malformed", evaluation=ev("n/a"))
            await add("no_eval")
            await add("inactive", evaluation=ev(0.01), active=False)
            await add("stopped", evaluation=ev(0.01), stopped=True)
            await add("old", evaluation=ev(0.01), age=90)
            await add("disliked", feedback="dislike")
            await add("liked", feedback="like")

            out = {}
            for kind in ("faithfulness", "feedback"):
                total, items = await review._fetch(session, kind, limit=100, offset=0, days=30)
                out[kind] = (total, items)
            total_wide, items_wide = await review._fetch(session, "faithfulness", limit=100, offset=0, days=365)
            out["wide"] = (total_wide, items_wide)
            return out

        out = self._run(fn)
        mine = {str(v): k for k, v in ids.items()}

        def names(items):
            return {mine[i.qa_id] for i in items if i.qa_id in mine}

        self.assertEqual(names(out["faithfulness"][1]), {"low"})
        self.assertEqual(names(out["feedback"][1]), {"disliked"})
        # 窗期放寬到一年，90 天前那筆才出現；inactive／stopped 仍然不算。
        self.assertEqual(names(out["wide"][1]), {"low", "old"})
        # 最低分排最前：自己塞的 0.01（old）在 0.05（low）之前。
        order = [mine[i.qa_id] for i in out["wide"][1] if i.qa_id in mine]
        self.assertEqual(order, ["old", "low"])
        # count 與當頁同一個條件：limit 夠大時 total 不得小於實際拿到的筆數。
        for key in ("faithfulness", "feedback", "wide"):
            self.assertGreaterEqual(out[key][0], len(out[key][1]))
        low = next(i for i in out["faithfulness"][1] if i.qa_id == str(ids["low"]))
        self.assertAlmostEqual(low.faithfulness_score, 0.05)

    def test_reviewer_resolves_to_username_and_asker_to_opaque_code(self):
        """處理人以純量子查詢補帳號名；提問者只回不可逆代號、不回帳號名或提問原文。

        舊資料（NULL）是 None，不是整列消失。
        """
        asker, reviewer = uuid.uuid4(), uuid.uuid4()
        owned, legacy = uuid.uuid4(), uuid.uuid4()
        suffix = uuid.uuid4().hex[:8]

        async def fn(session):
            for uid, name in ((asker, f"asker_{suffix}"), (reviewer, f"rev_{suffix}")):
                await session.execute(
                    text("INSERT INTO research.app_user (id, username, password_hash) VALUES (:id, :u, 'x')"),
                    {"id": uid, "u": name},
                )
            for qid in (owned, legacy):
                await session.execute(_INSERT_QA, {
                    "id": qid, "q": "誰問的", "active": True, "stopped": False, "feedback": "dislike",
                    "evaluation": None, "age": 1,
                })
            await session.execute(
                text("UPDATE research.qa_log SET user_id = :u WHERE id = :id"), {"u": asker, "id": owned},
            )
            await session.execute(
                text(
                    "INSERT INTO research.review_state (kind, subject_id, status, reviewer_user_id) "
                    "VALUES ('feedback', :id, 'resolved', :r)"
                ),
                {"id": owned, "r": reviewer},
            )
            _total, items = await review._fetch(session, "feedback", limit=200, offset=0, days=30, status="all")
            return {i.qa_id: i for i in items if i.qa_id in (str(owned), str(legacy))}

        got = self._run(fn)
        self.assertEqual(got[str(owned)].asker_code, review._asker_code(asker))
        self.assertEqual(got[str(owned)].reviewer, f"rev_{suffix}")
        dumped = got[str(owned)].model_dump_json()
        self.assertNotIn(f"asker_{suffix}", dumped)
        self.assertNotIn("誰問的", dumped)
        self.assertNotIn(str(asker), dumped)
        self.assertIsNone(got[str(legacy)].asker_code)
        self.assertIsNone(got[str(legacy)].reviewer)

    def test_qa_content_access_only_reaches_items_in_the_queue(self):
        """逐筆讀取的條件＝佇列的條件：佇列裡看得到的才讀得到，其餘（含不存在）都是 None。"""
        ids = {name: uuid.uuid4() for name in (
            "low", "ok", "malformed", "old", "inactive", "stopped", "disliked", "liked", "both", "old_judge",
        )}
        missing = uuid.uuid4()

        async def fn(session):
            async def add(name, *, evaluation=None, feedback=None, active=True, stopped=False, age=1):
                await session.execute(_INSERT_QA, {
                    "id": ids[name], "q": f"q-{name}", "active": active, "stopped": stopped, "feedback": feedback,
                    "evaluation": json.dumps(evaluation) if evaluation is not None else None, "age": age,
                })

            cur = review._JUDGE_MODEL
            await add("low", evaluation={"faithfulness_score": 0.05, "judge_model": cur})
            await add("ok", evaluation={"faithfulness_score": 0.99, "judge_model": cur})
            await add("malformed", evaluation={"faithfulness_score": "n/a", "judge_model": cur})
            await add("old", evaluation={"faithfulness_score": 0.01, "judge_model": cur}, age=90)
            await add("inactive", evaluation={"faithfulness_score": 0.01, "judge_model": cur}, active=False)
            await add("stopped", feedback="dislike", stopped=True)
            await add("disliked", feedback="dislike")
            await add("liked", feedback="like")
            await add("both", evaluation={"faithfulness_score": 0.02, "judge_model": cur}, feedback="dislike")
            await add("old_judge", evaluation={"faithfulness_score": 0.01, "judge_model": "some-retired-judge"})

            got = {name: await review._qa_content_in_queue(session, qid) for name, qid in ids.items()}
            got["missing"] = await review._qa_content_in_queue(session, missing)
            # 佇列（預設窗期）看得到的 id 集合，與讀得到的必須一致。
            queued = set()
            for kind in ("faithfulness", "feedback"):
                _t, items = await review._fetch(
                    session, kind, limit=1000, offset=0, days=review._DEFAULT_DAYS, status="all",
                )
                queued |= {i.qa_id for i in items}
            return got, queued

        got, queued = self._run(fn)
        readable = {name for name, v in got.items() if v is not None}
        self.assertEqual(readable, {"low", "disliked", "both"})
        mine = {str(v): k for k, v in ids.items()}
        self.assertEqual({mine[q] for q in queued if q in mine}, readable)
        question, answer, _created, kinds = got["low"]
        self.assertEqual((question, answer, kinds), ("q-low", "答", ["faithfulness"]))
        self.assertEqual(got["disliked"][3], ["feedback"])
        self.assertEqual(got["both"][3], ["faithfulness", "feedback"])

    def test_single_item_reaches_exactly_what_the_queue_lists(self):
        """取單筆（`_fetch_one`）的條件＝佇列的條件（status=all、預設窗期）：佇列列得出來的才取得到，
        而且那一筆與佇列的同一列逐欄相同；不在佇列、屬於另一個 kind、不存在都是 None。"""
        ids = {name: uuid.uuid4() for name in (
            "low", "ok", "malformed", "old", "inactive", "stopped", "disliked", "liked", "both", "old_judge",
        )}
        missing = uuid.uuid4()
        reviewer = uuid.uuid4()
        suffix = uuid.uuid4().hex[:8]

        async def fn(session):
            async def add(name, *, evaluation=None, feedback=None, active=True, stopped=False, age=1):
                await session.execute(_INSERT_QA, {
                    "id": ids[name], "q": f"q-{name}", "active": active, "stopped": stopped, "feedback": feedback,
                    "evaluation": json.dumps(evaluation) if evaluation is not None else None, "age": age,
                })

            cur = review._JUDGE_MODEL
            await add("low", evaluation={"faithfulness_score": 0.05, "judge_model": cur})
            await add("ok", evaluation={"faithfulness_score": 0.99, "judge_model": cur})
            await add("malformed", evaluation={"faithfulness_score": "n/a", "judge_model": cur})
            await add("old", evaluation={"faithfulness_score": 0.01, "judge_model": cur}, age=90)
            await add("inactive", evaluation={"faithfulness_score": 0.01, "judge_model": cur}, active=False)
            await add("stopped", feedback="dislike", stopped=True)
            await add("disliked", feedback="dislike")
            await add("liked", feedback="like")
            await add("both", evaluation={"faithfulness_score": 0.02, "judge_model": cur}, feedback="dislike")
            await add("old_judge", evaluation={"faithfulness_score": 0.01, "judge_model": "some-retired-judge"})
            # 已處理的照樣取得到（不看處理狀態），處理人一樣補成帳號名。
            await session.execute(
                text("INSERT INTO research.app_user (id, username, password_hash) VALUES (:id, :u, 'x')"),
                {"id": reviewer, "u": f"rev_{suffix}"},
            )
            await session.execute(
                text(
                    "INSERT INTO research.review_state (kind, subject_id, status, note, reviewer_user_id) "
                    "VALUES ('faithfulness', :id, 'resolved', '已看過', :r)"
                ),
                {"id": ids["low"], "r": reviewer},
            )
            out = {}
            for kind in ("faithfulness", "feedback"):
                _t, items = await review._fetch(
                    session, kind, limit=1000, offset=0, days=review._DEFAULT_DAYS, status="all",
                )
                listed = {i.qa_id: i for i in items}
                single = {name: await review._fetch_one(session, kind, qid) for name, qid in ids.items()}
                single["missing"] = await review._fetch_one(session, kind, missing)
                out[kind] = (listed, single)
            # 問答的 id 拿去當研報查，一樣查不到。
            out["cross"] = await review._fetch_one(session, "extraction", ids["low"])
            return out

        out = self._run(fn)
        expected = {"faithfulness": {"low", "both"}, "feedback": {"disliked", "both"}}
        for kind, names in expected.items():
            listed, single = out[kind]
            with self.subTest(kind=kind):
                self.assertEqual({name for name, item in single.items() if item is not None}, names)
                for name in names:
                    self.assertEqual(single[name], listed[str(ids[name])])
        low = out["faithfulness"][1]["low"]
        self.assertEqual((low.review_status, low.review_note, low.reviewer), ("resolved", "已看過", f"rev_{suffix}"))
        self.assertAlmostEqual(low.faithfulness_score, 0.05)
        # 單筆與佇列一樣不帶原文。
        self.assertNotIn("q-low", low.model_dump_json())
        self.assertIsNone(out["cross"])

    def test_single_extraction_item_follows_needs_review(self):
        """研報的單筆：目前標著 needs_review 才取得到，欄位與佇列的同一列相同；解除標記就是 None。"""
        flagged, clean = uuid.uuid4(), uuid.uuid4()
        tag = uuid.uuid4().hex

        async def fn(session):
            for rid, name, needs in ((flagged, "flagged", True), (clean, "clean", False)):
                await session.execute(
                    text(
                        "INSERT INTO research.research_report (id, file_hash, file_name, file_path, needs_review, "
                        "quality_score, quality_flags, pages_failed) "
                        "VALUES (:id, :h, :n, :p, :needs, -1, CAST(:flags AS jsonb), CAST(:failed AS int[]))"
                    ),
                    {"id": rid, "h": hashlib.sha256(f"{name}-{tag}".encode()).hexdigest(), "n": f"{name}.pdf",
                     "p": f"/nonexistent/{name}.pdf", "needs": needs,
                     "flags": json.dumps({"garbled_ratio": 0.2}), "failed": [3]},
                )
            await session.execute(
                text("INSERT INTO research.review_state (kind, subject_id, status) "
                     "VALUES ('extraction', :id, 'dismissed')"),
                {"id": flagged},
            )
            _t, items = await review._fetch(session, "extraction", limit=100, offset=0, days=30, status="all")
            listed = {i.report_id: i for i in items}
            got = {name: await review._fetch_one(session, "extraction", rid)
                   for name, rid in (("flagged", flagged), ("clean", clean), ("missing", uuid.uuid4()))}
            # 研報的 id 拿去當問答查，一樣查不到。
            got["cross"] = await review._fetch_one(session, "feedback", flagged)
            return listed, got

        listed, got = self._run(fn)
        self.assertIsNotNone(got["flagged"])
        self.assertEqual((got["flagged"].report_id, got["flagged"].review_status, got["flagged"].pages_failed),
                         (str(flagged), "dismissed", [3]))
        # 分數 -1 一定排在佇列最前面（分數低的在前），所以第一頁裡有它；兩邊必須逐欄相同。
        self.assertEqual(got["flagged"], listed[str(flagged)])
        self.assertEqual((got["clean"], got["missing"], got["cross"]), (None, None, None))

    def test_qa_content_audit_row_lands_in_same_transaction_without_content(self):
        """稽核與讀取同一筆交易；detail 只有 qa_id 與 kinds（rollback，不留痕）。"""
        from app.services.accounts import record_audit

        qid, actor = uuid.uuid4(), uuid.uuid4()

        async def fn(session):
            await session.execute(
                text("INSERT INTO research.app_user (id, username, password_hash, role) "
                     "VALUES (:id, :u, 'x', 'admin')"),
                {"id": actor, "u": f"qa_{uuid.uuid4().hex[:8]}"},
            )
            await session.execute(_INSERT_QA, {
                "id": qid, "q": "機密提問內容", "active": True, "stopped": False, "feedback": "dislike",
                "evaluation": None, "age": 1,
            })
            found = await review._qa_content_in_queue(session, qid)
            await record_audit(
                session, actor_id=str(actor), action="qa_content.read", target_type="qa",
                target_id=str(qid), detail={"qa_id": str(qid), "kinds": found[3]},
            )
            rows = (await session.execute(
                text("SELECT action, target_type, detail FROM research.admin_audit_log "
                     "WHERE actor_user_id = :a AND target_id = :t"),
                {"a": actor, "t": str(qid)},
            )).all()
            return [tuple(r) for r in rows]

        rows = self._run(fn)
        self.assertEqual(len(rows), 1)
        action, ttype, detail = rows[0]
        self.assertEqual((action, ttype), ("qa_content.read", "qa"))
        self.assertEqual(detail, {"qa_id": str(qid), "kinds": ["feedback"]})
        self.assertNotIn("機密", json.dumps(detail, ensure_ascii=False))

    def test_extraction_query_runs_and_only_returns_flagged_reports(self):
        async def fn(session):
            total, items = await review._fetch(session, "extraction", limit=5, offset=0, days=30)
            flagged = (await session.execute(
                text("SELECT count(*) FROM research.research_report WHERE needs_review")
            )).scalar_one()
            not_flagged = (await session.execute(
                text(
                    "SELECT count(*) FROM research.research_report "
                    "WHERE NOT needs_review AND id = ANY(CAST(:ids AS uuid[]))"
                ),
                {"ids": [i.report_id for i in items]},
            )).scalar_one()
            return total, items, int(flagged), int(not_flagged)

        total, items, flagged, not_flagged = self._run(fn)
        self.assertEqual(total, flagged)
        self.assertEqual(not_flagged, 0)
        self.assertLessEqual(len(items), 5)
        scores = [i.quality_score for i in items if i.quality_score is not None]
        self.assertEqual(scores, sorted(scores))


if __name__ == "__main__":
    unittest.main()
