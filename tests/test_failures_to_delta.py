# tests/test_failures_to_delta.py
"""失敗記錄 → 精準補救 delta，以及 importer 端的計數／路徑契約。

**這條路徑的價值是量出來的。** 2026-08-20 claude CLI 損毀那次，`--all-local` 要對本地
鏡像的 16,736 個檔逐一抽字再查 DB；而 `data/sync_failures.log` 裡就有那 7 筆的路徑，
轉成 delta 直接補回（7 篇、160 chunks、fail=0）。所以「每一筆異常都要留下可用的
相對路徑」是這個修正的一半——另一半（擋住心跳）在 test_pipeline_heartbeat.py。
"""
import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
HELPER = REPO_ROOT / "scripts" / "failures_to_delta.py"
IMPORTER = REPO_ROOT / "scripts" / "sync_new_reports.py"

sys.path.insert(0, str(REPO_ROOT / "scripts"))
_spec = importlib.util.spec_from_file_location("failures_to_delta", HELPER)
ftd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ftd)


class ParseFailuresTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.mirror = Path(self.tmp.name) / "mirror"
        self.mirror.mkdir()

    def _mk(self, name: str) -> Path:
        p = self.mirror / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"%PDF-1.4\n")
        return p

    def test_maps_tag_failures_to_relative_paths(self):
        a, b = self._mk("A.pdf"), self._mk("sub/B.pdf")
        lines = [f"{a}\ttag\tCLI 呼叫失敗", f"{b}\ttag\tCLI 逾時"]
        ok, bad = ftd.parse_failures(lines, self.mirror)
        self.assertEqual(sorted(ok), ["A.pdf", "sub/B.pdf"])
        self.assertEqual(bad, [])

    def test_all_three_stages_are_recoverable(self):
        """extract／tag／ingest 三個寫入點都必須產出可用路徑。"""
        p = self._mk("C.pdf")
        for stage in ("extract", "tag", "ingest"):
            with self.subTest(stage=stage):
                ok, bad = ftd.parse_failures([f"{p}\t{stage}\tboom"], self.mirror)
                self.assertEqual(ok, ["C.pdf"])
                self.assertEqual(bad, [])

    def test_legacy_hash_first_lines_are_reported_not_swallowed(self):
        """2026-08-20 之前 ingest 那處寫的是 file_hash，不是路徑。

        靜默丟棄會讓「補完了」與「有一半根本沒被看到」長得一樣。
        """
        lines = ["deadbeef" * 8 + "\t國巨(2327).pdf\tDBAPIError(...)"]
        ok, bad = ftd.parse_failures(lines, self.mirror)
        self.assertEqual(ok, [])
        self.assertEqual(len(bad), 1)

    def test_missing_file_is_reported(self):
        lines = [f"{self.mirror / 'gone.pdf'}\ttag\tboom"]
        ok, bad = ftd.parse_failures(lines, self.mirror)
        self.assertEqual(ok, [])
        self.assertEqual(len(bad), 1)

    def test_path_outside_mirror_is_rejected(self):
        """路徑逃逸必須被擋——這個清單的下一步是拿去匯入。"""
        outside = Path(self.tmp.name) / "elsewhere.pdf"
        outside.write_bytes(b"%PDF")
        ok, bad = ftd.parse_failures([f"{outside}\ttag\tboom"], self.mirror)
        self.assertEqual(ok, [])
        self.assertEqual(len(bad), 1)

    def test_duplicates_collapse_and_order_is_preserved(self):
        a, b = self._mk("A.pdf"), self._mk("B.pdf")
        lines = [f"{a}\ttag\tx", f"{b}\ttag\tx", f"{a}\ttag\ty"]
        ok, _ = ftd.parse_failures(lines, self.mirror)
        self.assertEqual(ok, ["A.pdf", "B.pdf"])

    def test_stage_filter(self):
        a, b = self._mk("A.pdf"), self._mk("B.pdf")
        lines = [f"{a}\ttag\tx", f"{b}\textract\tx"]
        ok, _ = ftd.parse_failures(lines, self.mirror, stages={"tag"})
        self.assertEqual(ok, ["A.pdf"])

    def test_content_filter_blocked_is_excluded_by_default(self):
        """tag_blocked（內容審查擋下）重打結果不會變：預設不撈，人工處置時才明確取出。"""
        a, b = self._mk("A.pdf"), self._mk("B.pdf")
        lines = [f"{a}\ttag\tCLI 逾時", f"{b}\ttag_blocked\tAPI[content_filter] 觸發供應商內容審查"]
        ok, bad = ftd.parse_failures(lines, self.mirror)
        self.assertEqual((ok, bad), (["A.pdf"], []))
        ok, _ = ftd.parse_failures(lines, self.mirror, stages={"tag_blocked"})
        self.assertEqual(ok, ["B.pdf"])

    def test_non_report_extension_is_rejected(self):
        p = self.mirror / "note.txt"
        p.write_text("x", encoding="utf-8")
        ok, bad = ftd.parse_failures([f"{p}\ttag\tx"], self.mirror)
        self.assertEqual(ok, [])
        self.assertEqual(len(bad), 1)

    def test_hostile_filenames_are_data_not_code(self):
        """檔名來自 NAS。空白、CJK、引號、分號、$()、反引號都不得被求值。"""
        names = [
            "a b.pdf",
            "研報 台積電(2330).pdf",
            "quote'and\"double.pdf",
            "semi;colon.pdf",
            "dollar$(touch pwned).pdf",
            "back`touch pwned`tick.pdf",
        ]
        made = [self._mk(n) for n in names]
        lines = [f"{p}\ttag\tboom" for p in made]
        ok, bad = ftd.parse_failures(lines, self.mirror)
        self.assertEqual(sorted(ok), sorted(names), bad)
        self.assertFalse((self.mirror / "pwned").exists())
        self.assertFalse(Path("pwned").exists())


class HelperCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.mirror = self.root / "mirror"
        self.mirror.mkdir()

    def _run(self, *args):
        return subprocess.run(
            [sys.executable, str(HELPER), *args],
            capture_output=True, text=True, cwd=self.root,
        )

    def test_produces_delta_and_reports_counts(self):
        p = self.mirror / "A.pdf"
        p.write_bytes(b"%PDF")
        log = self.root / "f.log"
        log.write_text(f"{p}\ttag\tboom\nnot-a-path\ttag\tboom\n", encoding="utf-8")
        out = self.root / "delta.txt"
        r = self._run("--log", str(log), "--out", str(out), "--mirror", str(self.mirror))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(out.read_text(encoding="utf-8").strip(), "A.pdf")
        self.assertIn("可補救：1", r.stdout)
        self.assertIn("對不回檔案：1", r.stdout)

    def test_empty_result_exits_nonzero(self):
        """空 delta 不該被拿去跑匯入——那只會是一次無效的全流程。"""
        log = self.root / "f.log"
        log.write_text("nope\ttag\tboom\n", encoding="utf-8")
        r = self._run("--log", str(log), "--out", str(self.root / "d.txt"),
                      "--mirror", str(self.mirror))
        self.assertEqual(r.returncode, 1)

    def test_missing_log_exits_two(self):
        r = self._run("--log", str(self.root / "nope.log"),
                      "--out", str(self.root / "d.txt"))
        self.assertEqual(r.returncode, 2)

    def test_output_is_valid_delta_input(self):
        """產出必須能被 importer 的 delta 解析器吃下去。"""
        import sync_new_reports as snr

        p = self.mirror / "sub" / "A.pdf"
        p.parent.mkdir()
        p.write_bytes(b"%PDF")
        log = self.root / "f.log"
        log.write_text(f"{p}\ttag\tboom\n", encoding="utf-8")
        out = self.root / "delta.txt"
        self._run("--log", str(log), "--out", str(out), "--mirror", str(self.mirror))
        parsed = snr.parse_rsync_delta(
            out.read_text(encoding="utf-8").splitlines(), self.mirror
        )
        self.assertEqual(parsed, [p])


class ImporterContractTests(unittest.TestCase):
    """importer 端：計數器分類與 FAIL_LOG 欄位格式。"""

    def setUp(self):
        import sync_new_reports as snr

        self.snr = snr

    def test_abnormal_counters_are_exactly_fail_untagged_and_blocked(self):
        # skip_blocked：行內標註被內容審查擋下，本該入庫卻沒進 DB（遷移 PR-12）
        self.assertEqual(set(self.snr.ABNORMAL_COUNTERS), {"fail", "skip_untagged", "skip_blocked"})

    def test_skip_blocked_counts_as_abnormal_in_stats(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / ".sync_last_stats"
            self.snr.write_stats(out, {"ingested": 2, "fail": 0, "skip_untagged": 0, "skip_blocked": 3})
            body = dict(ln.split("=", 1) for ln in out.read_text(encoding="utf-8").splitlines())
            self.assertEqual(body["abnormal"], "3")

    def test_expected_skips_are_not_abnormal(self):
        for k in ("skip_admin", "skip_exists", "skip_non_research", "skip_scanned"):
            self.assertNotIn(k, self.snr.ABNORMAL_COUNTERS)

    def test_stats_file_is_key_value_with_derived_abnormal(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / ".sync_last_stats"
            self.snr.write_stats(out, {"ingested": 2, "fail": 1, "skip_untagged": 3})
            body = dict(
                ln.split("=", 1) for ln in out.read_text(encoding="utf-8").splitlines()
            )
            self.assertEqual(body["abnormal"], "4")
            self.assertEqual(body["ingested"], "2")

    def test_cache_fail_is_reported_but_not_abnormal(self):
        """快取寫失敗的篇已入庫、已記進 hashes：寫進計數檔讓人看得到，但不算「該入庫卻沒進 DB」
        （算了會擋心跳、印出對它無效的重放補救指令）。"""
        self.assertNotIn("cache_fail", self.snr.ABNORMAL_COUNTERS)
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / ".sync_last_stats"
            self.snr.write_stats(out, {"ingested": 2, "fail": 0, "skip_untagged": 0, "cache_fail": 2})
            body = dict(ln.split("=", 1) for ln in out.read_text(encoding="utf-8").splitlines())
            self.assertEqual(body["cache_fail"], "2")
            self.assertEqual(body["abnormal"], "0")

    def test_stats_write_is_atomic_and_leaves_no_temp(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / ".sync_last_stats"
            self.snr.write_stats(out, {"ingested": 0})
            self.assertEqual([p.name for p in Path(d).iterdir()], [".sync_last_stats"])

    def test_all_fail_log_writes_use_path_stage_reason(self):
        """四個寫入點的欄位語意必須一致，否則補救時第 0 欄拿到的不是路徑。

        E1b 起第四個寫入點：extract_text 自己接住的損毀檔（res.error）也留一行，
        階段同樣是 extract。遷移 PR-12 起第五個：行內標註被內容審查擋下（tag_blocked）。
        """
        body = IMPORTER.read_text(encoding="utf-8")
        writes = [
            ln.strip() for ln in body.splitlines() if "fl.write(" in ln
        ]
        self.assertEqual(len(writes), 5, writes)
        for w in writes:
            self.assertIn("{path}", w, f"第 0 欄不是路徑：{w}")


if __name__ == "__main__":
    unittest.main()
