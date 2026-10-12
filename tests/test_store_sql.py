# tests/test_store_sql.py
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services.rows import ChunkRow  # noqa: E402
from app.services.store import (  # noqa: E402
    _lexical_sql,
    _parse_vec_text,
    fetch_chunk_embeddings,
    search_chunks_lexical,
)


class LexicalSqlTests(unittest.TestCase):
    def test_default_has_no_distinct_on(self):
        sql = _lexical_sql(2, ["r.market = :market"], per_report=False)
        self.assertNotIn("DISTINCT ON", sql)
        self.assertIn("c.content_norm LIKE :t0", sql)
        self.assertIn("c.content_norm LIKE :t1", sql)
        self.assertIn("r.market = :market", sql)
        self.assertLess(sql.index("c.content_norm LIKE :t0"), sql.index("r.market = :market"))
        self.assertIn("LIMIT :cap", sql)
        self.assertIn("LIMIT :limit", sql)

    def test_per_report_uses_distinct_on(self):
        sql = _lexical_sql(1, [], per_report=True)
        self.assertIn("DISTINCT ON (", sql)
        # DISTINCT ON 需以 report_id 起首排序，再依向量距離取最近 chunk
        self.assertIn("ORDER BY c.report_id", sql)

    def test_per_report_reranks_candidates_before_distinct(self):
        sql = _lexical_sql(1, [], per_report=True)
        self.assertIn("WITH lex_base AS MATERIALIZED", sql)
        self.assertIn("SELECT DISTINCT ON (", sql)
        self.assertLess(sql.index("LIMIT :cap"), sql.index("DISTINCT ON ("))

    def test_per_report_can_skip_final_limit(self):
        sql = _lexical_sql(1, [], per_report=True, limit=None)
        self.assertNotIn("LIMIT :limit", sql)

    def test_no_extra_conds_still_has_pattern_cond(self):
        sql = _lexical_sql(1, [], per_report=False)
        self.assertIn("c.content_norm LIKE :t0", sql)


class NullEmbeddingGuardTests(unittest.TestCase):
    """`embedding IS NOT NULL` 是防 500，不是效能過濾。

    `embedding` 允許 NULL，而 `NULL <=> vector` 回 NULL；呼叫端
    `retrieval.hybrid_search` 對每列做 `float(row.distance)` ⇒ `float(None)`
    TypeError ⇒ 整個查詢 500。字面路是唯一能撈出這種列的路徑——dense 路走 HNSW
    索引，而索引本身就不含 NULL。
    """

    def test_both_branches_filter_null_embedding(self):
        for per_report in (False, True):
            with self.subTest(per_report=per_report):
                sql = _lexical_sql(1, [], per_report=per_report)
                self.assertIn("c.embedding IS NOT NULL", sql)

    def test_filter_sits_inside_the_capped_cte(self):
        """必須在 `LIMIT :cap` 之前，否則 NULL 列會佔掉 cap 名額。

        放在最外層一樣不會 500，但會讓「cap 咬到」的計數把不可用的列算進去——
        `lex_hits` 的語意（候選被截斷了嗎）就跟著失真。
        """
        for per_report in (False, True):
            with self.subTest(per_report=per_report):
                sql = _lexical_sql(1, [], per_report=per_report)
                self.assertLess(
                    sql.index("c.embedding IS NOT NULL"), sql.index("LIMIT :cap")
                )

    def test_filter_precedes_caller_supplied_conds(self):
        """呼叫端條件接在後面，不得把守門擠掉或蓋掉。"""
        sql = _lexical_sql(1, ["r.market = :market"], per_report=False)
        self.assertIn("c.embedding IS NOT NULL", sql)
        self.assertIn("r.market = :market", sql)
        self.assertLess(
            sql.index("c.embedding IS NOT NULL"), sql.index("r.market = :market")
        )


class LexHitsSqlShapeTests(unittest.TestCase):
    """lex_hits（cap 截斷可觀測）的 SQL 形狀。

    兩件事錯了都不會拋錯、只會給錯數字：(a) 計數放進 CTE 內（視窗函式在 LIMIT 之前
    算，會強迫掃完全部命中列＝毀掉 LIMIT 的提早結束）；(b) per_report 對 DISTINCT ON
    之後的 lex 計數（那是報告數，答不了「cap 有沒有咬到」）。
    """

    def test_default_counts_capped_cte_as_last_column(self):
        sql = _lexical_sql(1, [], per_report=False)
        self.assertIn("(SELECT count(*) FROM lex) AS lex_hits", sql)
        # 計數必須在 cap 之後（對已 cap 的 CTE 再數），不可用 count(*) OVER ()
        self.assertNotIn("count(*) OVER", sql)
        self.assertLess(sql.index("LIMIT :cap"), sql.index("AS lex_hits"))
        # 末欄：ChunkRow 的欄位在前，distance 其次，lex_hits 最後
        self.assertLess(sql.index("AS distance"), sql.index("AS lex_hits"))

    def test_per_report_counts_lex_base_not_deduped_lex(self):
        sql = _lexical_sql(1, [], per_report=True)
        self.assertIn("(SELECT count(*) FROM lex_base) AS lex_hits", sql)
        self.assertNotIn("count(*) OVER", sql)



class LexicalDeterminismSqlTests(unittest.TestCase):
    """字面路必須可重現：同一語料、同一查詢，候選集與最終列每次相同。

    原本 `LIMIT :cap` 之前沒有 ORDER BY，取哪 cap 列由計畫決定；兩字詞走 parallel
    seq scan 時 Gather 的列序每次不同（2026-10-06 devdb 實測：33 個查詢中 14 個連跑 5 次
    得到 5 組候選）。數字與取捨見 `store._lexical_sql` docstring。
    """

    def _head(self, sql: str) -> str:
        return sql[: sql.index("LIMIT :cap")]

    def test_cap_is_preceded_by_stable_unique_order(self):
        for per_report in (False, True):
            with self.subTest(per_report=per_report):
                head = self._head(_lexical_sql(1, ["r.market = :market"], per_report=per_report))
                # 排序鍵必須是唯一鍵（主鍵），且緊接在 LIMIT :cap 之前：非唯一鍵在平手處
                # 一樣會漂移，放在別處則管不到 cap 取哪些列。
                self.assertRegex(head, r"ORDER BY c\.id\s*$")
                # 過濾條件全部在排序之前（WHERE 之內），不得被擠到 cap 之後
                self.assertLess(head.index("r.market = :market"), head.index("ORDER BY c.id"))

    def test_final_order_breaks_distance_ties_by_id(self):
        """內容相同的 chunk 距離完全相同，`LIMIT :limit` 切在平手中間時會漂移。"""
        sql = _lexical_sql(1, [], per_report=False)
        self.assertIn("ORDER BY distance, l.id", sql)
        self.assertLess(sql.index("ORDER BY distance, l.id"), sql.index("LIMIT :limit"))

    def test_per_report_distinct_on_and_final_order_break_ties_by_id(self):
        sql = _lexical_sql(1, [], per_report=True)
        # DISTINCT ON 取每篇「第一列」：同距離的兩個 chunk 誰先由 id 決定
        self.assertIn("ORDER BY c.report_id, c.distance, c.id", sql)
        self.assertIn("ORDER BY l.distance, l.id", sql)


class _RowsSession:
    """回放固定 raw row（tuple）的假 session；不碰 DB。"""

    def __init__(self, rows):
        self._rows = rows

    async def execute(self, stmt, params=None):
        self._stmt, self._params = stmt, params
        return self

    def all(self):
        return list(self._rows)


def _raw_row(chunk_id: str, lex_hits: int) -> tuple:
    """ChunkRow 全欄 + distance + lex_hits 的 raw tuple（欄數由 _fields 推導）。"""
    width = len(ChunkRow._fields)
    row = [None] * width
    row[ChunkRow._fields.index("chunk_id")] = chunk_id
    row[ChunkRow._fields.index("report_id")] = "r-" + chunk_id
    row[ChunkRow._fields.index("content")] = "內容"
    row[ChunkRow._fields.index("distance")] = 0.25
    return tuple(row) + (lex_hits,)


class _RecordingRowsSession(_RowsSession):
    """同 _RowsSession，另記下每一次 execute 的 SQL 文字（依序）。"""

    def __init__(self, rows):
        super().__init__(rows)
        self.statements: list[str] = []

    async def execute(self, stmt, params=None):
        self.statements.append(str(stmt))
        return await super().execute(stmt, params)


class LexicalCustomPlanTests(unittest.IsolatedAsyncioTestCase):
    """字面查詢之前必須在同一交易 `SET LOCAL plan_cache_mode = force_custom_plan`。

    asyncpg 快取 prepared statement，PG 跑過 5 次後可能改用 generic plan；generic plan
    不知道 pattern 是兩字（抽不出 trigram），devdb 實測兩字詞 3.3–4.2 秒、加上 cap 前的
    ORDER BY 後「散熱」29.6 秒（custom plan 0.5 秒內）。見 `search_chunks_lexical` 註解。
    """

    async def test_custom_plan_is_forced_before_the_lexical_query(self):
        session = _RecordingRowsSession([_raw_row("c1", 1)])
        await search_chunks_lexical(session, [0.1], ["%散熱%"])
        self.assertEqual(len(session.statements), 2)
        self.assertEqual(
            session.statements[0].strip(), "SET LOCAL plan_cache_mode = force_custom_plan"
        )
        self.assertIn("c.content_norm LIKE :t0", session.statements[1])

    async def test_set_is_transaction_local_not_session_wide(self):
        """連線是池化的：session 級 SET 會流到下一個借用者（同 db.relax_statement_timeout）。"""
        session = _RecordingRowsSession([])
        await search_chunks_lexical(session, [0.1], ["%ai%"])
        self.assertTrue(session.statements[0].lstrip().startswith("SET LOCAL "))


class SearchChunksLexicalReturnTests(unittest.IsolatedAsyncioTestCase):
    async def test_returns_rows_and_lex_hits_without_polluting_chunkrow(self):
        session = _RowsSession([_raw_row("c1", 2000), _raw_row("c2", 2000)])
        rows, lex_hits = await search_chunks_lexical(session, [0.1], ["%ai%"], cap=2000)
        self.assertEqual(lex_hits, 2000)
        self.assertEqual([r.chunk_id for r in rows], ["c1", "c2"])
        # lex_hits 不得洩進 ChunkRow：欄數必須維持不變（位置式契約）
        self.assertEqual(len(rows[0]), len(ChunkRow._fields))
        self.assertEqual(rows[0].distance, 0.25)

    async def test_empty_result_reports_zero_hits(self):
        rows, lex_hits = await search_chunks_lexical(_RowsSession([]), [0.1], ["%ai%"])
        self.assertEqual((rows, lex_hits), ([], 0))

    async def test_no_terms_short_circuits_before_db(self):
        # session=None：無 pattern 必須在碰 session 前回 ([], 0)
        self.assertEqual(await search_chunks_lexical(None, [0.1], []), ([], 0))


class ParseVecTextTests(unittest.TestCase):
    def test_normal_vector(self):
        self.assertEqual(_parse_vec_text("[0.5,-1.25,3]"), [0.5, -1.25, 3.0])

    def test_empty_vector(self):
        self.assertEqual(_parse_vec_text("[]"), [])

    def test_scientific_notation(self):
        self.assertEqual(_parse_vec_text("[1e-05,-2.5E3]"), [1e-05, -2500.0])

    def test_whitespace_tolerant(self):
        self.assertEqual(_parse_vec_text(" [0.1, 0.2] "), [0.1, 0.2])


class _FakeSession:
    """記錄 execute 呼叫並回放固定列的假 session（不碰 DB）。"""

    def __init__(self, rows):
        self._rows = rows
        self.calls = []

    async def execute(self, stmt, params=None):
        self.calls.append((stmt, params))
        return list(self._rows)


class FetchChunkEmbeddingsTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_input_short_circuits_without_db(self):
        # session=None：空輸入必須在碰 session 前回 {}
        self.assertEqual(await fetch_chunk_embeddings(None, []), {})

    async def test_sql_shape_expanding_in_and_text_cast(self):
        session = _FakeSession(rows=[])
        await fetch_chunk_embeddings(session, ["c1", "c2"])
        self.assertEqual(len(session.calls), 1)
        stmt, params = session.calls[0]
        sql = stmt.text
        self.assertIn("id IN :ids", sql)
        self.assertIn("id::text", sql)
        self.assertIn("embedding::text", sql)
        self.assertIn("research.report_chunk", sql)
        self.assertTrue(stmt._bindparams["ids"].expanding)
        self.assertEqual(params, {"ids": ["c1", "c2"]})

    async def test_rows_parsed_and_missing_ids_absent(self):
        session = _FakeSession(rows=[("c1", "[0.1,0.2]"), ("c2", "[1,2]")])
        result = await fetch_chunk_embeddings(session, ["c1", "c2", "c3"])
        self.assertEqual(result, {"c1": [0.1, 0.2], "c2": [1.0, 2.0]})
        self.assertNotIn("c3", result)

    async def test_null_embedding_row_skipped(self):
        session = _FakeSession(rows=[("c1", None), ("c2", "[1]")])
        result = await fetch_chunk_embeddings(session, ["c1", "c2"])
        self.assertEqual(result, {"c2": [1.0]})


if __name__ == "__main__":
    unittest.main()
