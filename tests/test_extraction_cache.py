"""E1c：per-hash 抽取快取、轉檔腳本、摘錄批次的表格過濾。全部在 tmp 目錄，不碰 data/。"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from app.services.extraction import cache

ROOT = Path(__file__).resolve().parents[1]


def _res(**kw):
    base = dict(
        file_hash="a" * 64, text="hello\n\n| a | b |\n\nworld", char_count=22, scanned=False, language="en",
        error=None, extractor="pdfplumber", extraction_version="ext-v", page_count=1, pages_failed=(),
        quality={"quality_score": 0.9}, blocks=(("paragraph", 1, 0, 5), ("table", 1, 7, 16), ("paragraph", 1, 18, 23)),
    )
    base.update(kw)
    return SimpleNamespace(**base)


class RecordTests(unittest.TestCase):
    def test_round_trip_and_atomic_write(self):
        with tempfile.TemporaryDirectory() as d:
            cd = Path(d)
            meta = SimpleNamespace(is_admin=False, stock_code="2330", company_name="台積電", report_date=None,
                                   report_type=None)
            rec = cache.record_from_result(_res(), Path("/x/y.pdf"), meta, "kgi")
            p = cache.write_record(rec, cd)
            self.assertEqual(p.name, "a" * 64 + ".json")
            self.assertFalse(list(cd.glob("*.tmp")), "暫存檔必須被 rename 掉")
            back = cache.read_record("a" * 64, cd)
            self.assertEqual(back["extractor"], "pdfplumber")
            self.assertEqual(back["blocks"][1], ["table", 1, 7, 16])
            self.assertEqual(back["source"], "kgi")
            self.assertEqual(back["version"], cache.RECORD_VERSION)

    def test_legacy_record_gets_honest_defaults(self):
        rec = cache.record_from_legacy({"file_hash": "h", "file_name": "f", "text": "t", "char_count": 1,
                                        "scanned": False, "language": "zh", "is_admin": False})
        self.assertEqual(rec["extractor"], "pypdf")
        self.assertEqual(rec["extraction_version"], cache.LEGACY_VERSION)
        self.assertEqual(rec["blocks"], [])

    def test_iter_is_sorted_and_flags_unreadable(self):
        with tempfile.TemporaryDirectory() as d:
            cd = Path(d)
            for h in ("c" * 64, "a" * 64, "b" * 64):
                cache.write_record({"file_hash": h, "file_name": h[:3]}, cd)
            (cd / ("d" * 64 + ".json")).write_text("{not json", encoding="utf-8")
            (cd / (".hidden" + ".json")).write_text("{}", encoding="utf-8")
            recs = list(cache.iter_records(cd))
            self.assertEqual([r.get("file_hash", "?")[:1] for r in recs], ["a", "b", "c", "?"])
            self.assertTrue(recs[-1]["_unreadable"])
            self.assertEqual(cache.count_records(cd), 5)

    def test_cache_path_rejects_traversal(self):
        for bad in ("", "../x", ".x", "a/b"):
            with self.assertRaises(ValueError):
                cache.cache_path(bad)


class StripTablesTests(unittest.TestCase):
    TEXT = "hello\n\n| a | b |\n\nworld"
    BLOCKS = [["paragraph", 1, 0, 5], ["table", 1, 7, 16], ["paragraph", 1, 18, 23]]

    def test_removes_table_spans_only(self):
        self.assertEqual(cache.strip_tables(self.TEXT, self.BLOCKS), "hello\n\n\n\nworld")

    def test_length_mismatch_returns_text_untouched(self):
        self.assertEqual(cache.strip_tables(self.TEXT, self.BLOCKS, expected_len=len(self.TEXT) - 1), self.TEXT)

    def test_no_blocks_or_no_tables_is_identity(self):
        self.assertEqual(cache.strip_tables(self.TEXT, []), self.TEXT)
        self.assertEqual(cache.strip_tables(self.TEXT, [["paragraph", 1, 0, 5]]), self.TEXT)

    def test_inconsistent_index_returns_text_untouched(self):
        self.assertEqual(cache.strip_tables(self.TEXT, [["table", 1, 10, 999]]), self.TEXT)
        self.assertEqual(cache.strip_tables(self.TEXT, [["table", 1, 7, 16], ["table", 1, 3, 9]]), self.TEXT)


class MigrationTests(unittest.TestCase):
    def _load(self):
        spec = importlib.util.spec_from_file_location("migrate_cache", ROOT / "scripts" / "migrate_extraction_cache.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules["migrate_cache"] = mod
        spec.loader.exec_module(mod)
        return mod

    def _src(self, d: Path) -> Path:
        rows = [
            {"file_hash": "h1", "file_name": "old.pdf", "text": "v1", "char_count": 2},
            {"file_path": "/broken.pdf", "error": "boom", "_failed": True},
            {"file_hash": "h2", "file_name": "two.pdf", "text": "t2", "char_count": 2},
            {"file_hash": "h1", "file_name": "new.pdf", "text": "v2", "char_count": 2},
        ]
        src = d / "all.jsonl"
        src.write_text("\n".join(json.dumps(r) for r in rows) + "\nnot-json\n", encoding="utf-8")
        return src

    def test_dry_run_writes_nothing(self):
        mod = self._load()
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            src = self._src(dd)
            stats = mod.migrate(src, dd / "cache", apply=False, force=False)
            self.assertEqual((stats["unique"], stats["dupes"], stats["failed_rows"], stats["bad_json"]), (2, 1, 1, 1))
            self.assertFalse((dd / "cache").exists())
            self.assertTrue(src.exists())

    def test_apply_takes_last_row_and_keeps_existing(self):
        mod = self._load()
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            src = self._src(dd)
            cd = dd / "cache"
            mod.migrate(src, cd, apply=True, force=False)
            self.assertEqual(cache.read_record("h1", cd)["file_name"], "new.pdf", "同 hash 取最後一行")
            self.assertEqual(cache.read_record("h1", cd)["extraction_version"], cache.LEGACY_VERSION)
            # 第二次：已存在者不覆寫
            cache.write_record({"file_hash": "h1", "file_name": "fresh.pdf", "text": "v3"}, cd)
            stats = mod.migrate(src, cd, apply=True, force=False)
            self.assertEqual(stats["kept_existing"], 2)
            self.assertEqual(cache.read_record("h1", cd)["file_name"], "fresh.pdf")


class EndpointContractTests(unittest.TestCase):
    """四個端點都改讀寫新格式；只有轉檔腳本還認得 all.jsonl。"""

    def test_no_script_reads_or_writes_all_jsonl(self):
        for name in ("extract_all", "sync_new_reports", "ingest_all", "tag_all_cli"):
            src = (ROOT / "scripts" / f"{name}.py").read_text(encoding="utf-8")
            body = "\n".join(ln for ln in src.splitlines() if not ln.strip().startswith("#") and '"""' not in ln)
            with self.subTest(script=name):
                self.assertNotRegex(body, r"all\.jsonl", f"{name} 仍引用 all.jsonl——漏改的端點會靜默寫給沒人讀的檔")
                self.assertIn("extraction", src)

    def test_takeaways_excerpt_uses_stripped_source_but_sha_uses_canonical(self):
        src = (ROOT / "scripts" / "extract_takeaways.py").read_text(encoding="utf-8")
        self.assertIn("item.excerpt_source[:excerpt]", src)
        self.assertNotIn("item.canonical[:excerpt]", src)
        self.assertIn("sha = sha256_of(canonical)", src)
        self.assertRegex(src, r"r\.full_text, r\.file_hash")


if __name__ == "__main__":
    unittest.main()
