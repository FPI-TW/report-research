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

    def test_no_order_by_before_cap(self):
        """刻意不加穩定排序鍵：ORDER BY 會逼掃完全部命中列。

        cap 截斷本來就不穩定（heap 物理順序 + synchronize_seqscans），但加排序鍵是
        「慢且仍不完整」的淨損失。先用 lex_hits 量發生率，見 _lexical_sql docstring。
        """
        for per_report in (False, True):
            sql = _lexical_sql(1, [], per_report=per_report)
            head = sql[: sql.index("LIMIT :cap")]
            self.assertNotIn("ORDER BY", head, f"per_report={per_report}")


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
