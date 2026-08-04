# tests/test_reading_queries.py
"""reading/queries.py：SQL 編譯層驗證（bind 真的綁上）+ 結構 + 假 session 打包（零 DB）。"""
import re
import sys
import unittest
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.dialects.postgresql import asyncpg as pg_asyncpg  # noqa: E402

from app.services.radar.types import SIGNAL_SELECT_COLUMNS  # noqa: E402
from app.services.reading import queries  # noqa: E402

_DIALECT = pg_asyncpg.dialect()

# 編譯後仍殘留的 `:name` ＝ 沒被綁上的參數（冒號會原樣進 PG）。
# `::text` 這類轉型不會誤判：其冒號前是字元或另一個冒號，被 lookbehind 排除。
_RESIDUAL_BIND_RE = re.compile(r"(?<![:\w]):(\w+)")


def _compiled(stmt):
    return stmt.compile(dialect=_DIALECT)


def _sql(stmt) -> str:
    """編譯後的 SQL 字串（bind 已渲染成 $1/$2…）。"""
    return str(_compiled(stmt))


class BindIntegrityGuardTests(unittest.TestCase):
    """先證明「驗編譯後」這套驗法本身有效，後面的斷言才有意義。

    **不可對 str(text()) 做 assertIn**：那只是把原字串原封不動吐回來，參數有沒有真的
    綁上完全看不出來。生產就是這樣漏掉 bug 的——`:markets::text[]` 的斷言恆綠，實際上
    參數一個都沒綁。
    """

    def test_guard_catches_colon_cast_backtrack(self):
        # PR #89 的真實 bug：compiler 的 BIND_PARAMS 有 `(?![:\w$])` 前瞻，
        # 遇到 `:markets::text[]` 完全比不到 → 不做任何代換 → 冒號原樣進 PG、
        # 參數一個也沒綁上 → 生產 500。
        bad = text("SELECT 1 FROM t WHERE m = ANY(:markets::text[])")
        compiled_bad = _compiled(bad)
        self.assertEqual(set(compiled_bad.params), set())  # 參數完全沒綁上
        self.assertIsNotNone(_RESIDUAL_BIND_RE.search(str(compiled_bad)))
        # 天真的 str(text()) 斷言在同一段壞 SQL 上照樣是綠的 —— 這正是它不可用的原因
        self.assertIn(":markets::text[]", str(bad))

        # 對照組：CAST(:x AS text[]) 才綁得上
        good = text("SELECT 1 FROM t WHERE m = ANY(CAST(:markets AS text[]))")
        compiled_good = _compiled(good)
        self.assertEqual(set(compiled_good.params), {"markets"})
        self.assertIsNone(_RESIDUAL_BIND_RE.search(str(compiled_good)))


class SqlBindTests(unittest.TestCase):
    """每支 SQL：bind 名完整、無殘留冒號參數。"""

    def _assert_binds(self, stmt, expected: set):
        compiled = _compiled(stmt)
        self.assertEqual(set(compiled.params), expected)
        residual = _RESIDUAL_BIND_RE.search(str(compiled))
        self.assertIsNone(
            residual, f"編譯後仍殘留未綁定參數：{residual.group(0) if residual else ''}"
        )

    def test_doc_sql(self):
        self._assert_binds(queries._DOC_SQL, {"file_hash"})

    def test_takeaways_sql(self):
        self._assert_binds(queries._TAKEAWAYS_SQL, {"report_id", "statuses"})

    def test_signals_sql(self):
        self._assert_binds(queries._SIGNALS_SQL, {"report_id", "statuses"})

    def test_instrument_names_sql(self):
        self._assert_binds(queries._INSTRUMENT_NAMES_SQL, {"markets", "codes"})

    def test_similar_sql(self):
        self._assert_binds(
            queries._SIMILAR_SQL,
            {"rid", "probe_n", "per_probe", "max_dist", "min_probes", "limit"},
        )

    def test_chunk_content_sql(self):
        self._assert_binds(queries._CHUNK_CONTENT_SQL, {"rid", "ci"})


class SqlStructureTests(unittest.TestCase):
    """結構斷言一律對「編譯後」字串做（同一個理由：只有編譯後才是真相）。"""

    def test_doc_sql_filters_non_research_inclusively(self):
        sql = _sql(queries._DOC_SQL)
        self.assertIn("FROM research.research_report", sql)
        self.assertIn("is_research IS NOT FALSE", sql)
        # NULL 也要算研究檔：`= true` 會把未標記的整批弄不見
        self.assertNotIn("is_research = true", sql)

    def test_takeaways_sql_ordered_by_ordinal(self):
        sql = _sql(queries._TAKEAWAYS_SQL)
        self.assertIn("FROM research.report_takeaway", sql)
        self.assertIn("extraction_status = ANY(", sql)
        self.assertIn("ORDER BY ordinal", sql)
        self.assertIn("text_sha256", sql)  # 驗章用，端點需要

    def test_signals_sql_takes_jsonb_as_text(self):
        sql = _sql(queries._SIGNALS_SQL)
        self.assertIn("FROM research.report_signal s", sql)
        self.assertIn("JOIN research.research_report r ON r.id = s.report_id", sql)
        self.assertIn("s.extraction_status = ANY(", sql)
        # jsonb 以 ::text 取出後 json.loads，不依賴 asyncpg codec
        self.assertIn("s.eps_estimates::text", sql)
        self.assertIn("s.thesis_dimensions::text", sql)

    def test_instrument_names_sql_mirrors_radar_name_resolution(self):
        sql = _sql(queries._INSTRUMENT_NAMES_SQL)
        # 名稱只有一個真相來源：research_report.company_name。改成別的來源（例如
        # 拿本篇的 company_name 就好）會讓同一檔標的在閱讀頁與雷達叫不同名字。
        self.assertIn("FROM research.research_report r", sql)
        self.assertIn("r.company_name", sql)
        # 依代號比對的是純量欄 stock_code（雷達 _COVERAGE_SQL／_catalog_cte 同語意），
        # 不是 stock_targets 陣列——後者「這份報告提到這檔」不等於「這份報告在講這檔」，
        # 拿它取名會把整份產業報告的公司名安到隨便一檔成分股上。
        self.assertIn("r.stock_code", sql)
        self.assertNotIn("stock_targets", sql)
        self.assertIn("is_research IS NOT FALSE", sql)
        # 每個 (market, code) 只留最新一列
        self.assertIn("DISTINCT ON (r.market, r.stock_code)", sql)
        self.assertIn("r.report_date DESC NULLS LAST", sql)
        # `:markets::text[]` 會讓參數靜默綁不上（PR #89 的生產 500）
        self.assertIn("CAST(", sql)
        self.assertNotIn("::text[]", sql)

    def test_valid_statuses_excludes_pending_and_rejected(self):
        self.assertEqual(queries.VALID_STATUSES, ["valid", "partial"])

    def test_similar_sql_samples_whole_document(self):
        sql = _sql(queries._SIMILAR_SQL)
        # 全篇均勻取樣（不是取前 N 塊——開頭多為封面/免責樣板）
        self.assertIn("row_number() OVER (ORDER BY c.chunk_index)", sql)
        # 均勻鋪滿：generate_series + floor(i * n / min(probe_n, n))。
        # 舊版的整數除法模數 (rn % (n / probe_n)) 在 n∈[probe_n, 2*probe_n) 會退化成
        # 「取前 probe_n 塊」＝拿封面/目錄/免責樣板當 probe，必須已被移除。
        self.assertIn("generate_series(0,", sql)
        self.assertIn("floor(", sql)
        self.assertNotIn("GREATEST(1, s.tot", sql)
        self.assertNotIn(".rn % ", sql)
        self.assertIn("CROSS JOIN LATERAL", sql)
        self.assertIn("c.embedding <=> p.embedding", sql)

    def test_similar_sql_ranks_by_breadth_weighted_score(self):
        sql = _sql(queries._SIMILAR_SQL)
        # 每個 probe 對每篇只計一次最佳距離，否則同篇多塊會對同一 probe 灌票
        self.assertIn("DISTINCT ON (pe, report_id)", sql)
        self.assertIn("sum(1 - dist) AS score", sql)
        self.assertIn("count(*) AS matched_probes", sql)
        # score 是主排序、best_dist 只是 tiebreaker：純 min(dist) 排序會讓
        # 「兩篇共用同一段免責聲明樣板」看起來極度相似，榜單全雜訊
        self.assertLess(sql.index("a.score DESC"), sql.index("a.best_dist ASC"))
        self.assertIn("a.matched_probes >=", sql)  # 單一樣板塊推不上榜
        self.assertIn("r.is_research IS NOT FALSE", sql)
        self.assertIn("(SELECT count(*) FROM probe) AS total_probes", sql)

    def test_similar_sql_excludes_self(self):
        self.assertIn("c.report_id <>", _sql(queries._SIMILAR_SQL))

    def test_chunk_content_sql_casts_with_cast_not_double_colon(self):
        sql = _sql(queries._CHUNK_CONTENT_SQL)
        self.assertIn("FROM research.report_chunk", sql)
        self.assertIn("chunk_index =", sql)
        # `:rid::uuid` 會讓 compiler 回溯成短名、參數綁不上（PR #89 的生產 500）
        self.assertIn("CAST(", sql)
        self.assertNotIn("::uuid", sql)


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None


class _RecordingSession:
    """依序回傳預備結果，並記錄每次 execute 的 (sql, params)。

    **`SET` 敘述不消耗預備結果**。原本是以「第幾次 execute」對位
    （`self._results[len(self.calls) - 1]`），所以待驗函式多下一句 `SET LOCAL`
    就把後面每一個結果整體位移，症狀是 IndexError 而不是「這個測試該驗的東西壞了」。
    `SET` 本來就不回列，把它排除在對位之外才符合現實，也讓「加一句 GUC 設定」不再
    是會弄壞五個測試的改動。

    預備結果用完仍然 IndexError（不補空列）：那代表待驗函式多下了一句**查詢**，
    是測試該知道的事。
    """

    def __init__(self, results):
        self._results = list(results)
        self.calls: list[tuple[str, object]] = []
        self._consumed = 0

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        self.calls.append((sql, params))
        if sql.lstrip().upper().startswith("SET "):
            return _FakeResult([])
        row = self._results[self._consumed]
        self._consumed += 1
        return row


def _doc_row(full_text="內文", file_hash="a" * 64):
    # 順序須與 _DOC_SQL 的 SELECT 一致
    return (
        "rep-1", file_hash, "daiwa-8046.pdf", "基板漲價超預期",
        "/data/daiwa-8046.pdf", "TW", "daiwa",
        date(2026, 7, 11), "個股報告", "摘要", ["equity"], ["8046"], [], full_text,
    )


class FetchDocTests(unittest.IsolatedAsyncioTestCase):
    async def test_packs_row(self):
        session = _RecordingSession([_FakeResult([_doc_row()])])
        doc = await queries.fetch_doc(session, "a" * 64)
        self.assertEqual(doc.report_id, "rep-1")
        self.assertEqual(doc.file_hash, "a" * 64)
        self.assertEqual(doc.file_name, "daiwa-8046.pdf")
        # title 夾在 file_name 與 file_path 之間：位移錯一格不會拋錯，只會讓報頭
        # 印出檔案路徑之類的垃圾，故逐欄釘死
        self.assertEqual(doc.title, "基板漲價超預期")
        self.assertEqual(doc.file_path, "/data/daiwa-8046.pdf")
        self.assertEqual(doc.market, "TW")
        self.assertEqual(doc.source, "daiwa")
        self.assertEqual(doc.report_date, date(2026, 7, 11))
        self.assertEqual(doc.instrument_types, ["equity"])
        self.assertEqual(doc.stock_targets, ["8046"])
        self.assertEqual(doc.futures_targets, [])
        self.assertEqual(doc.full_text, "內文")
        self.assertEqual(session.calls[0][1], {"file_hash": "a" * 64})

    async def test_missing_returns_none(self):
        session = _RecordingSession([_FakeResult([])])
        self.assertIsNone(await queries.fetch_doc(session, "b" * 64))

    async def test_null_arrays_become_empty_lists(self):
        row = list(_doc_row())
        row[9] = row[10] = row[11] = None
        session = _RecordingSession([_FakeResult([tuple(row)])])
        doc = await queries.fetch_doc(session, "a" * 64)
        self.assertEqual(doc.instrument_types, [])
        self.assertEqual(doc.stock_targets, [])
        self.assertEqual(doc.futures_targets, [])

    async def test_null_full_text_survives(self):
        session = _RecordingSession([_FakeResult([_doc_row(full_text=None)])])
        doc = await queries.fetch_doc(session, "a" * 64)
        self.assertIsNone(doc.full_text)  # 端點據此回 text_state="missing"


class FetchTakeawaysTests(unittest.IsolatedAsyncioTestCase):
    async def test_packs_rows_and_filters_by_status(self):
        rows = [
            (1, "論點一", "引文一", 10, 20, "exact", "s" * 64),
            (2, "論點二", None, None, None, None, "s" * 64),
        ]
        session = _RecordingSession([_FakeResult(rows)])
        out = await queries.fetch_takeaways(session, "rep-1")
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0].ordinal, 1)
        self.assertEqual(out[0].claim, "論點一")
        self.assertEqual(out[0].quote_start, 10)
        self.assertEqual(out[0].anchor_method, "exact")
        self.assertEqual(out[0].text_sha256, "s" * 64)
        self.assertIsNone(out[1].quote_start)  # 錨不到的條目照樣回
        self.assertEqual(
            session.calls[0][1], {"report_id": "rep-1", "statuses": ["valid", "partial"]}
        )


def _sig_row(status="valid"):
    # 順序須與 radar.types.SIGNAL_SELECT_COLUMNS 一致
    return (
        "sig-1", "rep-1", "TW", "8046", "daiwa", date(2026, 7, 11), "Buy (1)", "buy",
        Decimal("2444.0000"), "TWD", "12M", "TP 證據", "[]", "{}", status,
        "daiwa-8046.pdf", datetime(2026, 7, 11, 9, tzinfo=timezone.utc),
        "基板漲價超預期",
    )


class SigRowFixtureTests(unittest.TestCase):
    def test_width_matches_select_columns(self):
        # fixture 比 SELECT 窄時，既有的 test_reuses_radar_parse 就會 IndexError；
        # 這條真正補上的是另一半——fixture 比 SELECT 寬時 parse_signal_row 只讀
        # row[0..16]、不會報錯，fixture 卻已不代表真實列，兩條都會綠。
        # 順帶讓增欄時的訊息是「16 != 17」而不是 tuple index out of range。
        self.assertEqual(len(_sig_row()), len(SIGNAL_SELECT_COLUMNS))


class FetchSignalsTests(unittest.IsolatedAsyncioTestCase):
    async def test_reuses_radar_parse(self):
        session = _RecordingSession([_FakeResult([_sig_row()])])
        out = await queries.fetch_signals(session, "rep-1")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].instrument_code, "8046")
        self.assertEqual(out[0].target_price, 2444.0)  # Decimal → float
        self.assertEqual(out[0].rating_normalized, "buy")

    async def test_empty_is_normal(self):
        # 全語料僅 0.68% 有訊號：空是常態，不是錯誤
        session = _RecordingSession([_FakeResult([])])
        self.assertEqual(await queries.fetch_signals(session, "rep-1"), [])


class FetchInstrumentNamesTests(unittest.IsolatedAsyncioTestCase):
    async def test_keys_to_names(self):
        session = _RecordingSession([_FakeResult([("TW", "8046", "南亞電路板")])])
        out = await queries.fetch_instrument_names(session, [("TW", "8046")])
        self.assertEqual(out, {("TW", "8046"): "南亞電路板"})
        # market/code 兩個陣列逐位對應（unnest 的兩欄），順序不可錯開
        self.assertEqual(session.calls[0][1], {"markets": ["TW"], "codes": ["8046"]})

    async def test_no_keys_never_touches_db(self):
        # 99.3% 的研報沒有訊號：那條路徑一次 roundtrip 都不該付。
        # _RecordingSession 沒有預備結果，真打了 DB 會 IndexError 而不是靜默通過。
        session = _RecordingSession([])
        self.assertEqual(await queries.fetch_instrument_names(session, []), {})
        self.assertEqual(session.calls, [])

    async def test_missing_name_is_absent_not_empty_string(self):
        # 查無名稱是常態（檔名解析不出公司名）：鍵不進 dict，呼叫端據此回退代號
        session = _RecordingSession([_FakeResult([])])
        self.assertEqual(await queries.fetch_instrument_names(session, [("TW", "8046")]), {})

    async def test_blank_name_dropped(self):
        # 全空白的名稱會在呈現層渲染出看不見卻佔位的抬頭，比沒有名稱更糟
        session = _RecordingSession([_FakeResult([("TW", "8046", "   "), ("TW", "2330", None)])])
        out = await queries.fetch_instrument_names(session, [("TW", "8046"), ("TW", "2330")])
        self.assertEqual(out, {})


class FetchChunkContentTests(unittest.IsolatedAsyncioTestCase):
    async def test_returns_content_for_chunk_index(self):
        session = _RecordingSession([_FakeResult([("命中的那一段內文。",)])])
        out = await queries.fetch_chunk_content(session, "rep-1", 4)
        self.assertEqual(out, "命中的那一段內文。")
        self.assertEqual(session.calls[0][1], {"rid": "rep-1", "ci": 4})

    async def test_missing_chunk_returns_none(self):
        # chunk 不存在不是錯誤（連結可能來自重新 ingest 前的檢索結果）：
        # 端點據此不回 offset，前端不高亮但頁面照常
        session = _RecordingSession([_FakeResult([])])
        self.assertIsNone(await queries.fetch_chunk_content(session, "rep-1", 999))


def _similar_row(file_hash="c" * 64, matched=9, score=5.4, total=12, title="相似報告標題"):
    # 順序須與 _SIMILAR_SQL 的最終 SELECT 一致（title 在尾端，見 fetch_similar 註解）
    return (
        file_hash, "other.pdf", "TW", "kgi", date(2026, 7, 1), "摘要",
        matched, score, total, title,
    )


class FetchSimilarTests(unittest.IsolatedAsyncioTestCase):
    """`SET` 敘述**不算進**預備結果的對位（見 `_RecordingSession` docstring）。

    所以這些測試只 script 真正的查詢，並用 `_query_call()` 按語意取那一次 execute，
    不寫 `calls[1]`——不然每加一句 GUC 設定就要重數一輪索引。
    """

    @staticmethod
    def _query_call(session):
        """非 SET 的那一次 execute 的 (sql, params)。恰好一次，否則斷言失敗。"""
        hits = [c for c in session.calls if not c[0].lstrip().upper().startswith("SET ")]
        assert len(hits) == 1, f"預期恰好一次查詢，實際 {len(hits)} 次"
        return hits[0]

    async def test_sets_hnsw_guc_before_query(self):
        session = _RecordingSession([_FakeResult([_similar_row()])])
        await queries.fetch_similar(session, "rep-1")
        sets = [c[0] for c in session.calls if c[0].lstrip().upper().startswith("SET ")]
        # 本篇 chunk 會佔滿 HNSW top-k（`report_id <> :rid` 是掃描後才 recheck），
        # 不拉高 ef_search 外篇根本擠不進候選
        self.assertTrue(any("hnsw.ef_search = 100" in s for s in sets), sets)
        # 但拉高 ef_search 只是把候選集變大——過濾掉之後不夠了仍會停在那裡，
        # 靜默少回幾篇。iterative_scan 才是讓它繼續往下掃的那個開關。
        self.assertTrue(any("hnsw.iterative_scan = relaxed" in s for s in sets), sets)
        # 兩個 SET 都必須在查詢之前（SET LOCAL 只影響同交易內後續敘述）
        first_query = next(
            i for i, c in enumerate(session.calls)
            if not c[0].lstrip().upper().startswith("SET ")
        )
        self.assertEqual(first_query, len(sets), "SET 必須全部排在查詢之前")

    async def test_packs_rows_in_select_order(self):
        session = _RecordingSession([_FakeResult([_similar_row()])])
        out = await queries.fetch_similar(session, "rep-1")
        self.assertEqual(len(out), 1)
        item = out[0]
        self.assertEqual(item.file_hash, "c" * 64)
        self.assertEqual(item.file_name, "other.pdf")
        self.assertEqual(item.market, "TW")
        self.assertEqual(item.source, "kgi")
        self.assertEqual(item.report_date, date(2026, 7, 1))
        self.assertEqual(item.summary, "摘要")
        self.assertEqual(item.title, "相似報告標題")
        # 「9/12 段相符」的兩個數字不可對調（score 夾在中間，位移最易錯的地方）
        self.assertEqual(item.matched_probes, 9)
        self.assertEqual(item.total_probes, 12)
        self.assertEqual(item.score, 5.4)

    async def test_defaults_passed_as_params(self):
        session = _RecordingSession([_FakeResult([])])
        await queries.fetch_similar(session, "rep-1")
        self.assertEqual(
            self._query_call(session)[1],
            {"rid": "rep-1", "probe_n": 12, "per_probe": 20, "max_dist": 0.45,
             "min_probes": 2, "limit": 6},
        )

    async def test_overrides_passed_through(self):
        session = _RecordingSession([_FakeResult([])])
        await queries.fetch_similar(session, "rep-1", probe_n=4, limit=2)
        params = self._query_call(session)[1]
        self.assertEqual(params["probe_n"], 4)
        self.assertEqual(params["limit"], 2)

    async def test_no_hits_returns_empty(self):
        session = _RecordingSession([_FakeResult([]), _FakeResult([])])
        self.assertEqual(await queries.fetch_similar(session, "rep-1"), [])


if __name__ == "__main__":
    unittest.main()
