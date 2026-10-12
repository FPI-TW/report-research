# tests/test_retrieval_entitlement_sql.py
"""entitlement 下推到檢索 SQL：dense 與字面兩路（含 per_report／不限 limit）都帶到，
而 `entitlement=None` 時送出的 SQL 與參數和導入前**逐字相同**。

`_GOLDEN` 是導入 entitlement 之前（main c434330）對同一組呼叫擷取的完整 execute 序列
（SQL 文字＋參數），逐字固定在這裡：未帶 entitlement 的既有呼叫端（`/api/search`、
問答、評測）不得因這次改動多出任何條件或參數。日後**刻意**改了這兩條 SQL（例如
可見性片段）才重新擷取，並在 commit 說明為什麼。
"""
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services import retrieval as ret  # noqa: E402
from app.services import store  # noqa: E402
from app.services.entitlement import Entitlement  # noqa: E402


class _RecordingSession:
    """記下每次 execute 的 (SQL 文字, 參數)；查詢一律回零列。"""

    def __init__(self):
        self.calls: list[tuple[str, dict | None]] = []

    async def execute(self, stmt, params=None):
        self.calls.append((str(stmt), dict(params) if params else None))
        return self

    def all(self):
        return []


_VEC = [0.1, 0.2]
_PATTERNS = ["%ai%", "%伺服器%"]
_FILTERS = dict(market="TW", instrument_type="stock", relates_stock=True, relates_futures=True, report_type="個股")
# 名稱 → (哪條路, 呼叫參數)。per_report／不限 limit 的組合涵蓋 `_lexical_sql` 的兩條分支。
_CASES = {
    "dense_plain": ("meta", dict(scan=120)),
    "dense_filtered": ("meta", dict(scan=600, **_FILTERS)),
    "lex_default": ("lex", dict()),
    "lex_filtered": ("lex", dict(**_FILTERS)),
    "lex_unlimited": ("lex", dict(limit=None, cap=2000)),
    "lex_per_report": ("lex", dict(limit=1000, cap=8000, per_report=True)),
    "lex_per_report_unlimited": ("lex", dict(limit=None, cap=8000, per_report=True, **_FILTERS)),
}

_FULL = Entitlement(
    markets=("TW", "US"), sources=("元大",), report_types=("個股",), instrument_types=("stock", "etf")
)
_FULL_PARAMS = {
    "ent_markets": ["TW", "US"],
    "ent_sources": ["元大"],
    "ent_report_types": ["個股"],
    "ent_instrument_types": ["stock", "etf"],
}
_FULL_FRAGMENTS = (
    "r.market = ANY(CAST(:ent_markets AS text[]))",
    "r.source = ANY(CAST(:ent_sources AS text[]))",
    "r.report_type = ANY(CAST(:ent_report_types AS text[]))",
    "r.instrument_types && CAST(:ent_instrument_types AS text[])",
)


async def _run(name: str, **extra) -> list[tuple[str, dict | None]]:
    kind, kw = _CASES[name]
    session = _RecordingSession()
    if kind == "meta":
        await store.search_chunks_meta(session, _VEC, **kw, **extra)
    else:
        await store.search_chunks_lexical(session, _VEC, _PATTERNS, **kw, **extra)
    return session.calls


class NoEntitlementIsByteIdenticalTests(unittest.IsolatedAsyncioTestCase):
    async def test_omitted_matches_golden(self):
        for name in _CASES:
            with self.subTest(case=name):
                self.assertEqual(await _run(name), _GOLDEN[name])

    async def test_explicit_none_matches_golden(self):
        for name in _CASES:
            with self.subTest(case=name):
                self.assertEqual(await _run(name, entitlement=None), _GOLDEN[name])


class EntitlementPushdownTests(unittest.IsolatedAsyncioTestCase):
    async def test_every_dimension_reaches_both_paths(self):
        for name in _CASES:
            with self.subTest(case=name):
                calls = await _run(name, entitlement=_FULL)
                sql, params = calls[-1]
                for fragment in _FULL_FRAGMENTS:
                    self.assertIn(fragment, sql)
                for key, value in _FULL_PARAMS.items():
                    self.assertEqual(params[key], value)

    async def test_only_main_query_changes(self):
        """SET LOCAL（hnsw／force_custom_plan）照舊；只有主查詢多出 entitlement 條件與 ent_ 參數。"""
        for name in _CASES:
            with self.subTest(case=name):
                golden = _GOLDEN[name]
                calls = await _run(name, entitlement=_FULL)
                self.assertEqual(calls[:-1], golden[:-1])
                sql, params = calls[-1]
                golden_sql, golden_params = golden[-1]
                self.assertEqual({k: v for k, v in params.items() if not k.startswith("ent_")}, golden_params)
                clause, _ = _FULL.sql("r")
                # 沒有 metadata 過濾時 entitlement 是 WHERE 的第一個條件（後面接可見性片段）
                joined = " AND " + clause if " AND " + clause in sql else clause + " AND "
                self.assertEqual(sql.count(joined), 1)
                self.assertEqual(sql.replace(joined, "", 1), golden_sql)

    async def test_lexical_filter_sits_inside_the_capped_cte(self):
        """不可見的研報不得佔 `:cap` 名額，也不得改動 cap 前 `ORDER BY c.id` 的可重現排序。"""
        for name in ("lex_default", "lex_unlimited", "lex_per_report", "lex_per_report_unlimited"):
            with self.subTest(case=name):
                sql, _ = (await _run(name, entitlement=_FULL))[-1]
                cap_at = sql.index("LIMIT :cap")
                self.assertLess(sql.index("r.market = ANY(CAST(:ent_markets"), cap_at)
                self.assertLess(sql.index("r.market = ANY(CAST(:ent_markets"), sql.index("ORDER BY c.id"))
                self.assertEqual(sql.count("ent_markets"), 1)
                self.assertLess(sql.index("ORDER BY c.id"), cap_at)

    async def test_dense_filter_is_in_where_before_limit(self):
        for name in ("dense_plain", "dense_filtered"):
            with self.subTest(case=name):
                sql, _ = (await _run(name, entitlement=_FULL))[-1]
                where_at = sql.index("WHERE ")
                self.assertLess(where_at, sql.index("r.market = ANY(CAST(:ent_markets"))
                self.assertLess(sql.index("r.market = ANY(CAST(:ent_markets"), sql.index("LIMIT :scan"))

    async def test_market_only_entitlement_binds_only_markets(self):
        sql, params = (await _run("lex_default", entitlement=Entitlement(markets=("HK",))))[-1]
        self.assertEqual({k for k in params if k.startswith("ent_")}, {"ent_markets"})
        self.assertNotIn("ent_sources", sql)


class HybridSearchForwardsEntitlementTests(unittest.IsolatedAsyncioTestCase):
    """hybrid_search 要把 entitlement 交給兩路——含純中文重探的第二次字面查詢。"""

    async def _search(self, q, *, lex_results, picked=None, **kw):
        seen = {"meta": [], "lex": []}

        async def fake_meta(session, query_embedding, scan=60, market=None, instrument_type=None,
                            relates_stock=None, relates_futures=None, report_type=None, *, entitlement=None):
            seen["meta"].append(entitlement)
            return []

        async def fake_lex(session, query_embedding, term_patterns, limit=200, cap=2000, *, per_report=False,
                           market=None, instrument_type=None, relates_stock=None, relates_futures=None,
                           report_type=None, entitlement=None):
            seen["lex"].append(entitlement)
            return lex_results.pop(0) if lex_results else ([], 0)

        async def fake_pick(session, candidates):
            return picked

        orig = (ret.search_chunks_meta, ret.search_chunks_lexical, ret.pick_title_lead_term)
        ret.search_chunks_meta, ret.search_chunks_lexical, ret.pick_title_lead_term = fake_meta, fake_lex, fake_pick
        try:
            await ret.hybrid_search(object(), q, [0.0], **kw)
        finally:
            ret.search_chunks_meta, ret.search_chunks_lexical, ret.pick_title_lead_term = orig
        return seen

    async def test_forwards_to_dense_and_lexical(self):
        seen = await self._search("台積電", lex_results=[], entitlement=_FULL, lex_per_report=True, lex_unlimited=True)
        self.assertEqual(seen, {"meta": [_FULL], "lex": [_FULL]})

    async def test_forwards_to_cjk_fallback_reprobe(self):
        seen = await self._search("分析兆勁", lex_results=[([], 0), ([], 0)], picked="兆勁", entitlement=_FULL)
        self.assertEqual(seen, {"meta": [_FULL], "lex": [_FULL, _FULL]})

    async def test_default_is_none(self):
        seen = await self._search("台積電", lex_results=[])
        self.assertEqual(seen, {"meta": [None], "lex": [None]})


# 導入 entitlement 之前擷取（見模組 docstring）。
_GOLDEN = {'dense_filtered': [('SET LOCAL hnsw.ef_search = 600', None),
                    ('SET LOCAL hnsw.iterative_scan = relaxed_order', None),
                    ('\n'
                     '        SELECT c.id::text, r.id::text, r.file_hash, r.file_name, r.title,\n'
                     '               r.market, r.source,\n'
                     '               r.summary, r.report_date, r.report_type, r.instrument_types,\n'
                     '               r.relates_stock, r.relates_futures, r.stock_targets, '
                     'r.futures_targets,\n'
                     '               c.chunk_index, c.content,\n'
                     '               c.embedding <=> CAST(:q AS vector) AS distance\n'
                     '        FROM research.report_chunk c\n'
                     '        JOIN research.research_report r ON r.id = c.report_id\n'
                     '        WHERE r.market = :market AND r.instrument_types @> ARRAY[:it]::text[] AND '
                     'r.relates_stock = true AND r.relates_futures = true AND r.report_type = :report_type '
                     'AND NOT EXISTS (SELECT 1 FROM research.report_visibility rvis WHERE rvis.file_hash = '
                     "r.file_hash AND (rvis.hidden OR rvis.publication <> 'published'))\n"
                     '        ORDER BY c.embedding <=> CAST(:q AS vector)\n'
                     '        LIMIT :scan\n'
                     '    ',
                     {'it': 'stock',
                      'market': 'TW',
                      'q': '[0.1000000,0.2000000]',
                      'report_type': '個股',
                      'scan': 600})],
 'dense_plain': [('SET LOCAL hnsw.ef_search = 120', None),
                 ('SET LOCAL hnsw.iterative_scan = relaxed_order', None),
                 ('\n'
                  '        SELECT c.id::text, r.id::text, r.file_hash, r.file_name, r.title,\n'
                  '               r.market, r.source,\n'
                  '               r.summary, r.report_date, r.report_type, r.instrument_types,\n'
                  '               r.relates_stock, r.relates_futures, r.stock_targets, r.futures_targets,\n'
                  '               c.chunk_index, c.content,\n'
                  '               c.embedding <=> CAST(:q AS vector) AS distance\n'
                  '        FROM research.report_chunk c\n'
                  '        JOIN research.research_report r ON r.id = c.report_id\n'
                  '        WHERE NOT EXISTS (SELECT 1 FROM research.report_visibility rvis WHERE '
                  "rvis.file_hash = r.file_hash AND (rvis.hidden OR rvis.publication <> 'published'))\n"
                  '        ORDER BY c.embedding <=> CAST(:q AS vector)\n'
                  '        LIMIT :scan\n'
                  '    ',
                  {'q': '[0.1000000,0.2000000]', 'scan': 120})],
 'lex_default': [('SET LOCAL plan_cache_mode = force_custom_plan', None),
                 ('\n'
                  '        WITH lex AS MATERIALIZED (\n'
                  '            SELECT c.id, c.report_id, c.chunk_index, c.content, c.embedding\n'
                  '            FROM research.report_chunk c\n'
                  '            JOIN research.research_report r ON r.id = c.report_id\n'
                  '            WHERE c.content_norm LIKE :t0 AND c.content_norm LIKE :t1 AND c.embedding IS '
                  'NOT NULL AND NOT EXISTS (SELECT 1 FROM research.report_visibility rvis WHERE '
                  "rvis.file_hash = r.file_hash AND (rvis.hidden OR rvis.publication <> 'published'))\n"
                  '            ORDER BY c.id\n'
                  '            LIMIT :cap\n'
                  '        )\n'
                  '        SELECT l.id::text, r.id::text, r.file_hash, r.file_name, r.title,\n'
                  '               r.market, r.source,\n'
                  '               r.summary, r.report_date, r.report_type, r.instrument_types,\n'
                  '               r.relates_stock, r.relates_futures, r.stock_targets, r.futures_targets,\n'
                  '               l.chunk_index, l.content,\n'
                  '               l.embedding <=> CAST(:q AS vector) AS distance,\n'
                  '               (SELECT count(*) FROM lex) AS lex_hits\n'
                  '        FROM lex l\n'
                  '        JOIN research.research_report r ON r.id = l.report_id\n'
                  '        ORDER BY distance, l.id\n'
                  '        LIMIT :limit\n'
                  '    ',
                  {'cap': 2000, 'limit': 200, 'q': '[0.1000000,0.2000000]', 't0': '%ai%', 't1': '%伺服器%'})],
 'lex_filtered': [('SET LOCAL plan_cache_mode = force_custom_plan', None),
                  ('\n'
                   '        WITH lex AS MATERIALIZED (\n'
                   '            SELECT c.id, c.report_id, c.chunk_index, c.content, c.embedding\n'
                   '            FROM research.report_chunk c\n'
                   '            JOIN research.research_report r ON r.id = c.report_id\n'
                   '            WHERE c.content_norm LIKE :t0 AND c.content_norm LIKE :t1 AND c.embedding IS '
                   'NOT NULL AND r.market = :market AND r.instrument_types @> ARRAY[:it]::text[] AND '
                   'r.relates_stock = true AND r.relates_futures = true AND r.report_type = :report_type AND '
                   'NOT EXISTS (SELECT 1 FROM research.report_visibility rvis WHERE rvis.file_hash = '
                   "r.file_hash AND (rvis.hidden OR rvis.publication <> 'published'))\n"
                   '            ORDER BY c.id\n'
                   '            LIMIT :cap\n'
                   '        )\n'
                   '        SELECT l.id::text, r.id::text, r.file_hash, r.file_name, r.title,\n'
                   '               r.market, r.source,\n'
                   '               r.summary, r.report_date, r.report_type, r.instrument_types,\n'
                   '               r.relates_stock, r.relates_futures, r.stock_targets, r.futures_targets,\n'
                   '               l.chunk_index, l.content,\n'
                   '               l.embedding <=> CAST(:q AS vector) AS distance,\n'
                   '               (SELECT count(*) FROM lex) AS lex_hits\n'
                   '        FROM lex l\n'
                   '        JOIN research.research_report r ON r.id = l.report_id\n'
                   '        ORDER BY distance, l.id\n'
                   '        LIMIT :limit\n'
                   '    ',
                   {'cap': 2000,
                    'it': 'stock',
                    'limit': 200,
                    'market': 'TW',
                    'q': '[0.1000000,0.2000000]',
                    'report_type': '個股',
                    't0': '%ai%',
                    't1': '%伺服器%'})],
 'lex_per_report': [('SET LOCAL plan_cache_mode = force_custom_plan', None),
                    ('\n'
                     '        WITH lex_base AS MATERIALIZED (\n'
                     '            SELECT c.id, c.report_id, c.chunk_index, c.content, c.embedding\n'
                     '            FROM research.report_chunk c\n'
                     '            JOIN research.research_report r ON r.id = c.report_id\n'
                     '            WHERE c.content_norm LIKE :t0 AND c.content_norm LIKE :t1 AND c.embedding '
                     'IS NOT NULL AND NOT EXISTS (SELECT 1 FROM research.report_visibility rvis WHERE '
                     "rvis.file_hash = r.file_hash AND (rvis.hidden OR rvis.publication <> 'published'))\n"
                     '            ORDER BY c.id\n'
                     '            LIMIT :cap\n'
                     '        ),\n'
                     '        lex_ranked AS MATERIALIZED (\n'
                     '            SELECT c.id, c.report_id, c.chunk_index, c.content, c.embedding,\n'
                     '                   c.embedding <=> CAST(:q AS vector) AS distance\n'
                     '            FROM lex_base c\n'
                     '        ),\n'
                     '        lex AS MATERIALIZED (\n'
                     '            SELECT DISTINCT ON (c.report_id)\n'
                     '                   c.id, c.report_id, c.chunk_index, c.content, c.embedding, '
                     'c.distance\n'
                     '            FROM lex_ranked c\n'
                     '            ORDER BY c.report_id, c.distance, c.id\n'
                     '        )\n'
                     '        SELECT l.id::text, r.id::text, r.file_hash, r.file_name, r.title,\n'
                     '               r.market, r.source,\n'
                     '               r.summary, r.report_date, r.report_type, r.instrument_types,\n'
                     '               r.relates_stock, r.relates_futures, r.stock_targets, '
                     'r.futures_targets,\n'
                     '               l.chunk_index, l.content,\n'
                     '               l.distance,\n'
                     '               (SELECT count(*) FROM lex_base) AS lex_hits\n'
                     '        FROM lex l\n'
                     '        JOIN research.research_report r ON r.id = l.report_id\n'
                     '        ORDER BY l.distance, l.id\n'
                     '        LIMIT :limit\n'
                     '    ',
                     {'cap': 8000,
                      'limit': 1000,
                      'q': '[0.1000000,0.2000000]',
                      't0': '%ai%',
                      't1': '%伺服器%'})],
 'lex_per_report_unlimited': [('SET LOCAL plan_cache_mode = force_custom_plan', None),
                              ('\n'
                               '        WITH lex_base AS MATERIALIZED (\n'
                               '            SELECT c.id, c.report_id, c.chunk_index, c.content, c.embedding\n'
                               '            FROM research.report_chunk c\n'
                               '            JOIN research.research_report r ON r.id = c.report_id\n'
                               '            WHERE c.content_norm LIKE :t0 AND c.content_norm LIKE :t1 AND '
                               'c.embedding IS NOT NULL AND r.market = :market AND r.instrument_types @> '
                               'ARRAY[:it]::text[] AND r.relates_stock = true AND r.relates_futures = true '
                               'AND r.report_type = :report_type AND NOT EXISTS (SELECT 1 FROM '
                               'research.report_visibility rvis WHERE rvis.file_hash = r.file_hash AND '
                               "(rvis.hidden OR rvis.publication <> 'published'))\n"
                               '            ORDER BY c.id\n'
                               '            LIMIT :cap\n'
                               '        ),\n'
                               '        lex_ranked AS MATERIALIZED (\n'
                               '            SELECT c.id, c.report_id, c.chunk_index, c.content, '
                               'c.embedding,\n'
                               '                   c.embedding <=> CAST(:q AS vector) AS distance\n'
                               '            FROM lex_base c\n'
                               '        ),\n'
                               '        lex AS MATERIALIZED (\n'
                               '            SELECT DISTINCT ON (c.report_id)\n'
                               '                   c.id, c.report_id, c.chunk_index, c.content, c.embedding, '
                               'c.distance\n'
                               '            FROM lex_ranked c\n'
                               '            ORDER BY c.report_id, c.distance, c.id\n'
                               '        )\n'
                               '        SELECT l.id::text, r.id::text, r.file_hash, r.file_name, r.title,\n'
                               '               r.market, r.source,\n'
                               '               r.summary, r.report_date, r.report_type, r.instrument_types,\n'
                               '               r.relates_stock, r.relates_futures, r.stock_targets, '
                               'r.futures_targets,\n'
                               '               l.chunk_index, l.content,\n'
                               '               l.distance,\n'
                               '               (SELECT count(*) FROM lex_base) AS lex_hits\n'
                               '        FROM lex l\n'
                               '        JOIN research.research_report r ON r.id = l.report_id\n'
                               '        ORDER BY l.distance, l.id\n'
                               '    ',
                               {'cap': 8000,
                                'it': 'stock',
                                'market': 'TW',
                                'q': '[0.1000000,0.2000000]',
                                'report_type': '個股',
                                't0': '%ai%',
                                't1': '%伺服器%'})],
 'lex_unlimited': [('SET LOCAL plan_cache_mode = force_custom_plan', None),
                   ('\n'
                    '        WITH lex AS MATERIALIZED (\n'
                    '            SELECT c.id, c.report_id, c.chunk_index, c.content, c.embedding\n'
                    '            FROM research.report_chunk c\n'
                    '            JOIN research.research_report r ON r.id = c.report_id\n'
                    '            WHERE c.content_norm LIKE :t0 AND c.content_norm LIKE :t1 AND c.embedding '
                    'IS NOT NULL AND NOT EXISTS (SELECT 1 FROM research.report_visibility rvis WHERE '
                    "rvis.file_hash = r.file_hash AND (rvis.hidden OR rvis.publication <> 'published'))\n"
                    '            ORDER BY c.id\n'
                    '            LIMIT :cap\n'
                    '        )\n'
                    '        SELECT l.id::text, r.id::text, r.file_hash, r.file_name, r.title,\n'
                    '               r.market, r.source,\n'
                    '               r.summary, r.report_date, r.report_type, r.instrument_types,\n'
                    '               r.relates_stock, r.relates_futures, r.stock_targets, r.futures_targets,\n'
                    '               l.chunk_index, l.content,\n'
                    '               l.embedding <=> CAST(:q AS vector) AS distance,\n'
                    '               (SELECT count(*) FROM lex) AS lex_hits\n'
                    '        FROM lex l\n'
                    '        JOIN research.research_report r ON r.id = l.report_id\n'
                    '        ORDER BY distance, l.id\n'
                    '    ',
                    {'cap': 2000, 'q': '[0.1000000,0.2000000]', 't0': '%ai%', 't1': '%伺服器%'})]}


if __name__ == "__main__":
    unittest.main()
