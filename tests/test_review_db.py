"""待複核佇列的三條查詢對真的 PostgreSQL 成立（`web/routers/review.py` 的 `_fetch`）。

`tests/test_review_api.py` 的假 session 只回放結果；這裡驗的是查詢本身：jsonb 分數的
安全 cast（畸形一列不得讓整支端點炸掉）、門檻與 active／stopped 過濾、`make_interval`
窗期、以及 count 與當頁是同一個條件。

跑在 CI 的「schema 契約」job；本機沒有 DB 就 skip。**一律 rollback、絕不 commit**——本機
連到的是生產庫。斷言只針對自己塞進去的列（以 id 辨識），不假設庫是空的。
"""

from __future__ import annotations

import asyncio
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
        # 單題對話沒有 conversation_id，回退到自己的 id——前端要靠它連回 /ask?c=。
        self.assertEqual(low.conversation_id, low.qa_id)

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
