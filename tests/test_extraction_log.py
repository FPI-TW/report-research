"""E1b：research_report 七欄、extraction_log 第十一張表、兩條寫入路徑的每一道閘都留痕。

不碰 DB 的靜態守門（DB 對帳由 tests/test_schema_constraints.py 做）。這裡釘的是
「會靜默漂掉的耦合」：schema 的 CHECK 詞彙 vs store.STOPPED_AT、寫入端有沒有漏掉
某一道閘、ReportRow 的新欄位有沒有真的進 INSERT。
"""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

from app.services import store

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = (ROOT / "db" / "schema.sql").read_text(encoding="utf-8")
GOLDEN = (ROOT / "db" / "expected_constraints.txt").read_text(encoding="utf-8")
INGEST = (ROOT / "scripts" / "ingest_all.py").read_text(encoding="utf-8")
SYNC = (ROOT / "scripts" / "sync_new_reports.py").read_text(encoding="utf-8")

NEW_COLUMNS = (
    "extractor", "extraction_version", "quality_score", "quality_flags", "page_count", "pages_failed", "needs_review",
)


class SchemaTests(unittest.TestCase):
    def test_research_report_gets_seven_columns_idempotently(self):
        for col in NEW_COLUMNS:
            with self.subTest(col=col):
                self.assertRegex(
                    SCHEMA,
                    rf"ALTER TABLE research\.research_report ADD COLUMN IF NOT EXISTS {col}\s",
                    f"research_report 缺 {col} 的冪等補欄",
                )

    def test_needs_review_is_not_null_with_default(self):
        self.assertRegex(SCHEMA, r"needs_review\s+boolean NOT NULL DEFAULT false")

    def test_extraction_log_table_and_indexes(self):
        self.assertIn("CREATE TABLE IF NOT EXISTS research.extraction_log", SCHEMA)
        for frag in ("file_hash          text PRIMARY KEY", "file_names         text[] NOT NULL",
                     "stopped_at         text NOT NULL", "idx_extraction_log_stopped_at", "idx_extraction_log_version"):
            with self.subTest(frag=frag):
                self.assertIn(frag, SCHEMA)

    def test_stopped_at_vocabulary_matches_store_constant(self):
        m = re.search(r"CHECK \(stopped_at IN \(([^)]*)\)\)", SCHEMA)
        self.assertIsNotNone(m, "schema.sql 缺 stopped_at 的 CHECK")
        in_sql = tuple(v.strip().strip("'") for v in m.group(1).split(","))
        self.assertEqual(in_sql, store.STOPPED_AT, "schema CHECK 與 store.STOPPED_AT 不一致")
        self.assertIn("skip_admin", store.STOPPED_AT, "§4.2：stopped_at 必須有 skip_admin")

    def test_golden_constraints_lists_the_new_check(self):
        """CHECK 在既有庫是 no-op，golden 清單是唯一守門；新約束必須同時進清單。"""
        self.assertIn("extraction_log.extraction_log_stopped_at_check = CHECK", GOLDEN)
        for v in store.STOPPED_AT:
            self.assertIn(f"'{v}'::text", GOLDEN)


class StoreTests(unittest.TestCase):
    def test_upsert_report_inserts_every_new_column(self):
        src = ast.get_source_segment(
            Path(store.__file__).read_text(encoding="utf-8"),
            next(n for n in ast.parse(Path(store.__file__).read_text(encoding="utf-8")).body
                 if isinstance(n, ast.AsyncFunctionDef) and n.name == "upsert_report"),
        )
        for col in NEW_COLUMNS:
            with self.subTest(col=col):
                self.assertIn(col, src, f"upsert_report 的 INSERT 少了 {col}")

    def test_report_row_defaults_keep_old_callers_working(self):
        row = store.ReportRow(
            file_hash="h", file_name="f", file_path="p", market=None, is_research=None, confidence=None
        )
        self.assertIsNone(row.extractor)
        self.assertFalse(row.needs_review)

    def test_needs_review_rules(self):
        self.assertFalse(store.needs_review(None, None, 0.6), "pypdf 路徑沒分數不算低分")
        self.assertFalse(store.needs_review(0.9, (), 0.6))
        self.assertTrue(store.needs_review(0.3, (), 0.6))
        self.assertTrue(store.needs_review(0.95, (3,), 0.6), "有頁級失敗就要人看，與分數無關")

    def test_extraction_log_rejects_unknown_stopped_at(self):
        import asyncio

        row = store.ExtractionLogRow("h", "f", "pypdf", "v", stopped_at="skip_untagged")
        with self.assertRaises(ValueError):
            asyncio.run(store.upsert_extraction_log(None, row))  # 在打 DB 之前就擋下

    def test_extraction_log_upsert_accumulates_file_names(self):
        src = Path(store.__file__).read_text(encoding="utf-8")
        self.assertIn("ON CONFLICT (file_hash) DO UPDATE", src)
        self.assertIn("unnest(research.extraction_log.file_names || EXCLUDED.file_names)", src,
                      "同 hash 不同檔名必須累加，不能後寫覆蓋前寫（鏡像有 122 組）")


class WritePathTests(unittest.TestCase):
    """兩條入庫路徑的每一道閘都要留痕。漏一道就是 §1.1 那種「落點只在 if 分支裡」。"""

    def _calls(self, src: str) -> list[str]:
        return re.findall(r'upsert_extraction_log\(session, _log(?:_row\(rec)?[^"]*"(\w+)"', src)

    def test_ingest_all_logs_every_gate(self):
        got = set(self._calls(INGEST))
        self.assertEqual(got, {"skip_admin", "scanned", "not_research", "ingested"}, got)

    def test_sync_logs_every_gate_including_extract_error(self):
        got = set(self._calls(SYNC))
        self.assertEqual(got, {"extract_error", "skip_admin", "scanned", "not_research", "ingested"}, got)

    def test_both_paths_pass_extraction_columns_to_report_row(self):
        for name, src in (("ingest_all", INGEST), ("sync_new_reports", SYNC)):
            for col in ("extractor=", "extraction_version=", "needs_review=needs_review("):
                with self.subTest(script=name, col=col):
                    self.assertIn(col, src)

    def test_review_threshold_comes_from_settings(self):
        for src in (INGEST, SYNC):
            self.assertIn("get_settings().extraction_review_min", src)


if __name__ == "__main__":
    unittest.main()
