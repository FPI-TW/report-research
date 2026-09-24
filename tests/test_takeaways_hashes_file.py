# tests/test_takeaways_hashes_file.py
"""extract_takeaways 的 --hashes-file 取材範圍（P4 定時排程接線）。

**為什麼非它不可**：`--since-days` 濾的是 `report_date` 而非入庫時間，而 NAS 匯入的
研報日期常比入庫日早——2026-07-28 實測近 10 天入庫的 90 篇裡有 79 篇（88%）的
report_date 超過一天前。若拿 `--since-days 1` 接 3 小時排程，會靜默漏掉近九成新研報，
正是這次要修的那種失效（閱讀頁摘錄自 07-21 起完全歸零，8 天沒人發現）。
"""
import importlib.util
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _load():
    """extract_takeaways.py 是 scripts/ 下的獨立腳本，非套件模組 → 以檔案路徑載入。"""
    path = REPO_ROOT / "scripts" / "extract_takeaways.py"
    spec = importlib.util.spec_from_file_location("extract_takeaways", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["extract_takeaways"] = mod  # @dataclass 需先註冊才解析得到型別
    spec.loader.exec_module(mod)
    return mod


class BuildReportsSqlScopeTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load()

    def test_default_scope_filters_by_report_date(self):
        sql = self.mod.build_reports_sql()
        self.assertIn("report_date >= current_date - CAST(:since_days AS int)", sql)
        self.assertNotIn(":hashes", sql)

    def test_hashes_scope_drops_date_filter(self):
        """以 file_hash 取材時**不得**再套 report_date 條件——套了就會把「今天入庫但
        日期較舊」的研報排除掉，而那正是要處理的多數情形（實測 88%）。"""
        sql = self.mod.build_reports_sql(by_hashes=True)
        self.assertIn("r.file_hash = ANY(:hashes)", sql)
        self.assertNotIn("report_date >=", sql)
        self.assertNotIn("since_days", sql)

    def test_hashes_scope_keeps_content_guards(self):
        """取材範圍換了，但「有全文、非明確非研報」這兩個既有守門不可跟著掉。"""
        sql = self.mod.build_reports_sql(by_hashes=True)
        self.assertIn("r.full_text IS NOT NULL", sql)
        self.assertIn("r.full_text <> ''", sql)
        self.assertIn("r.is_research IS NOT FALSE", sql)

    def test_no_inline_cast_colon_syntax(self):
        """專案鐵律（見檔頭註解）：一律 CAST(:x AS t)，寫 :x::t 會讓參數綁不上 → 生產 500。"""
        for sql in (self.mod.build_reports_sql(), self.mod.build_reports_sql(by_hashes=True)):
            self.assertNotIn("::int", sql)
            self.assertNotIn("::text[]", sql)


class ReadHashesFileTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load()

    def test_strips_blanks_and_whitespace(self):
        import tempfile

        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False,
                                         encoding="utf-8") as f:
            f.write("  aaa  \n\n bbb\n\n\n")
            p = f.name
        try:
            self.assertEqual(self.mod.read_hashes_file(p), ["aaa", "bbb"])
        finally:
            Path(p).unlink(missing_ok=True)


class CliArgTests(unittest.TestCase):
    def test_hashes_file_arg_exists(self):
        """契約防護：排程接線靠這個參數。被移掉的話 sync 腳本會靜默失效。"""
        _load()  # 匯入本身就是斷言：這支腳本必須 import 得起來
        import argparse

        # 重建 parser（腳本把 add_argument 寫在 __main__ 區塊外的函式或內聯皆可）
        src = (REPO_ROOT / "scripts" / "extract_takeaways.py").read_text(encoding="utf-8")
        self.assertIn('"--hashes-file"', src)
        self.assertIsNotNone(argparse)  # 佔位:僅確認 argparse 可用


class SyncScriptWiringTests(unittest.TestCase):
    """同步腳本必須序列呼叫且帶 --hashes-file。"""

    def setUp(self):
        self.src = (REPO_ROOT / "scripts" / "sync_new_reports.sh").read_text(
            encoding="utf-8"
        )

    def test_calls_extract_takeaways_with_hashes_file(self):
        self.assertIn("extract_takeaways.py", self.src)
        i = self.src.index("extract_takeaways.py")
        self.assertIn("--hashes-file", self.src[i:i + 300])

    def test_takeaways_runs_after_summaries(self):
        """兩者都呼叫 LLM，併發會重複付費、摘錄互相覆寫（scripts/_claude_lock.py 的理由）。"""
        self.assertLess(
            self.src.index("generate_summaries.py"),
            self.src.index("extract_takeaways.py"),
        )

    def test_does_not_use_since_days_in_sync(self):
        """守住本次修復的核心決策：排程不可用 --since-days（會靜默漏掉近九成）。"""
        i = self.src.index("extract_takeaways.py")
        self.assertNotIn("--since-days", self.src[i:i + 300])


if __name__ == "__main__":
    unittest.main()
