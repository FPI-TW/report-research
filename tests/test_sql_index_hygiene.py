# tests/test_sql_index_hygiene.py
"""P1-11：全表掃描相關的靜態守門（索引宣告、標的過濾寫法、ANALYZE 涵蓋）。

這一整組缺陷共有一個性質：**錯了不會有任何錯誤訊息**。
`:code = ANY(r.stock_targets)` 和 `r.stock_targets @> ARRAY[:code]::text[]` 結果完全
相同，只差在後者才用得到 `idx_rr_stock_targets` GIN；漏一個 `CREATE INDEX` 也只是查詢
變慢；漏 `ANALYZE research.research_report` 也只是 planner 在剛匯入後那個短窗用舊統計。
所以只有靜態斷言擋得住回歸。

嚴重度要如實看待：`research_report` 現況約 1.4 萬列、`full_text` 多半 TOAST 出去，
heap 本身不大，單次 seq scan 的量級估算只有數十毫秒（未實測）。這批東西的價值在
「規模一放大就線性惡化」，**不是現在就會痛**，也**不是已量測到的加速**。
"""
import re
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

SCHEMA_SQL = (REPO_ROOT / "db" / "schema.sql").read_text(encoding="utf-8")

# ANALYZE 這三處是「匯入的最後一步」——被砍掉或漏表都只留下靜默過期的統計。
_INGEST_SCRIPTS = (
    "scripts/ingest_all.py",
    "scripts/run_ingest.py",
    "scripts/sync_new_reports.py",
)


def _code_only(path: Path) -> str:
    """回傳去掉 `#` 註解的原始碼。

    註解裡本來就會逐字寫出被禁的寫法（「不可寫 :code = ANY(...)」正是最該留的說明），
    連註解一起掃會讓這條守門變成「不准解釋為什麼」。以 tokenize 剔除，不用正則猜
    ——`#` 在別處是合法字元（Typst 片段整篇都是）。
    """
    import io
    import tokenize

    src = path.read_text(encoding="utf-8")
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return src  # 掃不動就退回全文：寧可誤報，不要靜默漏掃
    return "\n".join(
        t.string for t in toks if t.type not in (tokenize.COMMENT, tokenize.NL)
    )


def _index_names(sql: str) -> set[str]:
    """schema.sql 裡實際會被建立的索引名（含 UNIQUE INDEX，不含 CONSTRAINT）。"""
    return set(
        re.findall(
            r"CREATE\s+(?:UNIQUE\s+)?INDEX\s+IF\s+NOT\s+EXISTS\s+(\w+)",
            sql,
            re.IGNORECASE,
        )
    )


class ResearchReportIndexTests(unittest.TestCase):
    """`research_report` 的常用過濾／排序欄位原本一個索引都沒有（只有 market 與陣列 GIN）。"""

    def test_scalar_filter_columns_are_indexed(self):
        names = _index_names(SCHEMA_SQL)
        for name in (
            "idx_rr_report_date",
            "idx_rr_source",
            "idx_rr_report_type",
            # stock_code 是純量欄位，與 stock_targets 的 GIN 互不覆蓋：_COVERAGE_SQL 的
            # instrument_name 子查詢與 overview 個股條件的 OR 左支用的都是這一欄。
            "idx_rr_stock_code",
        ):
            with self.subTest(index=name):
                self.assertIn(name, names)

    def test_report_date_index_matches_the_only_sort_direction_used(self):
        # 全 repo 的 report_date 排序一律 DESC NULLS LAST（雷達 instrument_name、
        # 目錄 rep CTE、檢索頁）。索引方向寫反時查詢照樣正確，只是排序又要多一次 sort。
        self.assertRegex(
            SCHEMA_SQL,
            r"idx_rr_report_date\s*\n?\s*ON research\.research_report "
            r"\(report_date DESC NULLS LAST\)",
        )

    def test_company_name_index_is_trgm_gin_not_btree(self):
        """`company_name` 的唯一查法是 ILIKE '%名%'，B-tree 一點用都沒有。"""
        self.assertIn("idx_rr_company_name_trgm", _index_names(SCHEMA_SQL))
        self.assertRegex(
            SCHEMA_SQL,
            r"idx_rr_company_name_trgm\s*\n?\s*ON research\.research_report "
            r"USING gin \(company_name gin_trgm_ops\)",
        )

    def test_trgm_extension_is_declared_before_the_trgm_index(self):
        # gin_trgm_ops 在 CREATE EXTENSION pg_trgm 之前會讓整份 schema.sql 中斷，
        # 而 make schema 是一次性餵進 psql —— 後面所有表與欄位都不會建。
        self.assertLess(
            SCHEMA_SQL.index("CREATE EXTENSION IF NOT EXISTS pg_trgm"),
            SCHEMA_SQL.index("idx_rr_company_name_trgm"),
        )


class RedundantIndexTests(unittest.TestCase):
    """完全被 UNIQUE 約束（或其最左前綴）覆蓋的索引不該再宣告。

    多餘索引不會讓查詢出錯，只是每次 INSERT/UPDATE 多維護一份、多佔一份空間。
    這三個是逐欄（含欄序）比對確認的。
    """

    def test_indexes_fully_covered_by_unique_constraints_are_gone(self):
        names = _index_names(SCHEMA_SQL)
        for dropped, covering in (
            # 與 uq_report_section_run_pos UNIQUE (run_id, position) 逐欄相同
            ("idx_report_section_run", "uq_report_section_run_pos"),
            # 與 uq_report_takeaway_ordinal UNIQUE (report_id, ordinal) 逐欄相同
            ("idx_report_takeaway_report", "uq_report_takeaway_ordinal"),
            # (report_id) 是 uq_report_signal_report_instr 的最左前綴
            ("idx_report_signal_report", "uq_report_signal_report_instr"),
        ):
            with self.subTest(index=dropped):
                self.assertNotIn(dropped, names)
                self.assertIn(covering, SCHEMA_SQL)

    def test_broker_composite_index_is_deliberately_kept(self):
        """`idx_report_signal_instr_broker_date` 刻意不刪。

        券商過濾一律走 radar/types.py 的 EFFECTIVE_BROKER_SQL（跨表 COALESCE），
        所以第三欄 broker 永遠不會被當過濾鍵——但「從未被使用」是**執行期**斷言，
        planner 仍可能為只用 (market, instrument_code) 的查詢挑中它。
        要刪之前得先查生產的 pg_stat_user_indexes.idx_scan。
        """
        self.assertIn("idx_report_signal_instr_broker_date", _index_names(SCHEMA_SQL))


class StockTargetContainmentTests(unittest.TestCase):
    """`= ANY(<array column>)` 一律用不到 array_ops GIN，全 repo 不該再出現。"""

    def test_no_scalar_any_over_stock_targets_anywhere_in_app(self):
        # array_ops GIN 只支援 && @> <@ =，text = text[] 不在其中。兩種寫法語意等價，
        # 所以退回舊寫法完全不會有錯誤 —— 只有這個掃描擋得住。
        offenders = []
        for path in sorted((REPO_ROOT / "app").rglob("*.py")):
            for m in re.finditer(
                r"=\s*ANY\(\s*\w*\.?stock_targets\s*\)", _code_only(path)
            ):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{m.group(0)}")
        self.assertEqual(offenders, [])

    def test_overview_stock_code_filter_uses_containment(self):
        from app.services.overview import OverviewFilters, _build_where

        where, params = _build_where(OverviewFilters(stock_code="2330"))

        self.assertIn("r.stock_code = :sc", where)
        self.assertIn("r.stock_targets @> ARRAY[:sc]::text[]", where)
        self.assertEqual(params["sc"], "2330")

    def test_overview_containment_bind_survives_compilation(self):
        """`ARRAY[:sc]::text[]` 的 bind 必須完整綁上。

        `::` 緊接參數名（:sc::text[]）會讓 SQLAlchemy 的 text() 回溯成短名，冒號原樣
        送進 PG 炸 syntax error，而字串比對測試會是綠的（它比對的正是壞掉的字串）。
        """
        from sqlalchemy import text
        from sqlalchemy.dialects.postgresql import asyncpg as pg_asyncpg

        from app.services.overview import OverviewFilters, _build_where

        where, _ = _build_where(OverviewFilters(stock_code="2330"))
        compiled = text(
            f"SELECT 1 FROM research.research_report r WHERE {where}"
        ).compile(dialect=pg_asyncpg.dialect())

        self.assertEqual(set(compiled.params), {"sc"})
        self.assertNotRegex(str(compiled), r"(?<!:):\w+")


class AnalyzeCoverageTests(unittest.TestCase):
    """匯入收尾的 ANALYZE 必須同時涵蓋 chunk 與 report 兩張表。

    autoanalyze 是開著的（DB 跑官方映像、無 conf 覆寫），所以缺 research_report 不會
    讓統計長期失真；會失真的是「剛大批 ingest 完就立刻查詢」那個短窗——排程每 3 小時
    匯入一次，正好落在窗裡。
    """

    def _src(self, rel: str) -> str:
        return (REPO_ROOT / rel).read_text(encoding="utf-8")

    def test_both_tables_are_analyzed(self):
        for rel in _INGEST_SCRIPTS:
            src = self._src(rel)
            for table in ("research.report_chunk", "research.research_report"):
                with self.subTest(script=rel, table=table):
                    # 不用 assertIn：haystack 是整份原始碼，失敗訊息會吐出整個檔案
                    self.assertTrue(
                        f"ANALYZE {table}" in src, f"{rel} 缺 ANALYZE {table}"
                    )

    def test_analyze_stays_inside_the_statement_timeout_exemption(self):
        """兩句 ANALYZE 都要在同一個 relax_statement_timeout 豁免之內。

        `relax_statement_timeout` 用的是 SET LOCAL（見 app/services/db.py），豁免只到
        本交易 commit 為止。ANALYZE 掉到 relax 之前或 commit 之後，就會被引擎層的
        statement_timeout 砍掉，而它是匯入的最後一步——資料早就 commit 了，症狀只有
        planner 統計靜默過期，排程沒人在看。
        """
        for rel in _INGEST_SCRIPTS:
            src = self._src(rel)
            with self.subTest(script=rel):
                relax = src.index("await relax_statement_timeout(session)")
                chunk = src.index('ANALYZE research.report_chunk')
                report = src.index('ANALYZE research.research_report')
                commit = src.index("await session.commit()", relax)
                self.assertLess(relax, chunk)
                self.assertLess(chunk, report)
                self.assertLess(report, commit)


if __name__ == "__main__":
    unittest.main()
