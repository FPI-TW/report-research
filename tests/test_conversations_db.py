"""`answer.list_conversations` 的搜尋與翻頁對真的 PostgreSQL 成立。

`tests/test_answer.py` 用假 session 釘住的是 SQL 字串與參數；`bool_or(...) FILTER`、
`CAST(:pattern AS text) IS NULL OR matched`、ILIKE 的跳脫、以及排序決勝鍵讓翻頁不重疊，
這些只有真的資料庫驗得到。

跑在 CI 的「schema 契約」job；本機沒有 DB 就 skip（`REPORT_MARK_REQUIRE_DB` 設定時改成
紅燈，與 tests/test_extraction_log_db.py 同一套語意）。

**一律 rollback、絕不 commit**：這支測試在本機會連到生產庫。測試資料以一個隨機標記字串
做搜尋條件，所以不受庫裡既有對話影響，也不假設庫是空的。
"""

from __future__ import annotations

import asyncio
import os
import unittest
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest import mock

from sqlalchemy import text

from app.services import answer as ans


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


_INSERT = text(
    "INSERT INTO research.qa_log (id, question, answer, conversation_id, active, created_at) "
    "VALUES (:id, :q, :a, :cid, :active, :ts)"
)


class ListConversationsDbTests(unittest.TestCase):
    def _run(self, fn):
        try:
            return asyncio.run(_with_session(fn))
        except unittest.SkipTest:
            raise
        except AssertionError:
            raise
        except Exception as exc:
            msg = repr(exc)
            if "UndefinedTable" in msg or "UndefinedColumn" in msg or "does not exist" in msg:
                _skip_or_raise(exc, "既有庫尚未套用 schema")
            _skip_or_raise(exc, f"DB 不可用（{type(exc).__name__}）")

    def test_search_paging_and_literal_wildcards(self):
        tag = "zzconv" + uuid.uuid4().hex[:10]
        base = datetime(2031, 1, 1, tzinfo=timezone.utc)  # 遠在未來：排序上一定在既有資料之前
        same_ts = base + timedelta(hours=5)

        async def fn(session):
            async def add(cid, question, *, answer="答", active=True, ts=base):
                await session.execute(_INSERT, {
                    "id": uuid.uuid4(), "q": question, "a": answer, "cid": cid, "active": active, "ts": ts,
                })

            title_hit, followup_hit, inactive_only, offtopic_only, wildcard = (uuid.uuid4() for _ in range(5))
            await add(title_hit, f"{tag} 台積電展望", ts=base + timedelta(hours=1))
            # 標題（第一題）不含標記，後面追問的那一句才含——搜尋要找得到它。
            await add(followup_hit, "完全無關的第一題", ts=base + timedelta(hours=2))
            await add(followup_hit, f"追問 {tag.upper()} 先進封裝", ts=base + timedelta(hours=3))
            # 命中的那一輪已被重生／編輯取代（inactive）→ 不算。
            await add(inactive_only, "有效的第一題", ts=base + timedelta(hours=4))
            await add(inactive_only, f"{tag} 被取代的版本", active=False, ts=base + timedelta(hours=4, minutes=1))
            # 整串只有離題拒答 → 本來就不列。
            await add(offtopic_only, f"{tag} 離題", answer=ans.OFF_TOPIC_MESSAGE, ts=base + timedelta(hours=4))
            await add(wildcard, f"{tag} 成長 50% 以上", ts=base + timedelta(hours=6))
            # 兩串 last_at 完全相同：沒有決勝鍵的話，翻頁之間的相對順序不保證穩定。
            tie_a, tie_b = uuid.uuid4(), uuid.uuid4()
            await add(tie_a, f"{tag} 同秒甲", ts=same_ts)
            await add(tie_b, f"{tag} 同秒乙", ts=same_ts)

            @asynccontextmanager
            async def _same_session():
                yield session

            with mock.patch.object(ans, "SessionFactory", _same_session):
                hits = await ans.list_conversations(limit=50, q=tag)
                ids = [h["conversation_id"] for h in hits]
                pages = [
                    [h["conversation_id"] for h in await ans.list_conversations(limit=2, offset=off, q=tag)]
                    for off in (0, 2, 4)
                ]
                literal = await ans.list_conversations(limit=50, q=f"{tag} 成長 50%")
                as_wildcard = await ans.list_conversations(limit=50, q=f"{tag} 成長 5_%")
            return ids, pages, literal, as_wildcard, {
                "title": str(title_hit), "followup": str(followup_hit), "inactive": str(inactive_only),
                "offtopic": str(offtopic_only), "wildcard": str(wildcard),
                "ties": {str(tie_a), str(tie_b)},
            }

        ids, pages, literal, as_wildcard, k = self._run(fn)

        self.assertEqual(set(ids), {k["title"], k["followup"], k["wildcard"], *k["ties"]})
        self.assertNotIn(k["inactive"], ids)
        self.assertNotIn(k["offtopic"], ids)
        # 由新到舊：wildcard(6h) → 兩個同秒(5h) → followup(3h) → title(1h)
        self.assertEqual(ids[0], k["wildcard"])
        self.assertEqual(set(ids[1:3]), k["ties"])
        self.assertEqual(ids[3:], [k["followup"], k["title"]])
        # 翻頁：串起來與一次取回完全相同——不重疊、不遺漏。
        self.assertEqual([c for page in pages for c in page], ids)
        # % 與 _ 是字面字元：「50%」找得到那一串；把它們當萬用字元用則找不到任何東西。
        self.assertEqual([h["conversation_id"] for h in literal], [k["wildcard"]])
        self.assertEqual(as_wildcard, [])


if __name__ == "__main__":
    unittest.main()
