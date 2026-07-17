# tests/test_store_sql.py
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services.store import (  # noqa: E402
    _lexical_sql,
    _parse_vec_text,
    fetch_chunk_embeddings,
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
