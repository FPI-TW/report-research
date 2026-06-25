import os
import sys
import tempfile
import unittest
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import sync_new_reports as snr  # noqa: E402


@dataclass
class _Tag:
    market: str | None
    is_research: bool


class SyncNewReportsTests(unittest.TestCase):
    def test_parse_rsync_delta_keeps_only_files_with_known_ext(self):
        lines = [
            "新增/",  # 目錄列 → 略過
            "新增/0701 報告.pdf",
            "note.txt",  # 非目標副檔名 → 略過
            "a/b/Taiwan daily.docx",
            "",  # 空行
        ]
        out = snr.parse_rsync_delta(lines, Path("/local"))
        self.assertEqual(
            out,
            [
                Path("/local/新增/0701 報告.pdf"),
                Path("/local/a/b/Taiwan daily.docx"),
            ],
        )

    def test_parse_rsync_delta_dedupes_preserving_order(self):
        out = snr.parse_rsync_delta(["x.pdf", "x.pdf", "y.PDF"], Path("/d"))
        self.assertEqual(out, [Path("/d/x.pdf"), Path("/d/y.PDF")])

    def test_parse_rsync_delta_dedupes_case_insensitively(self):
        # 來源為大小寫不敏感檔系統；同檔不同大小寫應視為同一檔，保留首次出現的原始路徑
        out = snr.parse_rsync_delta(["x.PDF", "x.pdf"], Path("/d"))
        self.assertEqual(out, [Path("/d/x.PDF")])

    def test_skip_before_tag_priority_order(self):
        self.assertEqual(snr.skip_before_tag(True, False, False), "skip_admin")
        self.assertEqual(snr.skip_before_tag(False, True, False), "skip_scanned")
        self.assertEqual(snr.skip_before_tag(False, False, True), "skip_exists")
        self.assertIsNone(snr.skip_before_tag(False, False, False))

    def test_skip_after_tag(self):
        self.assertEqual(snr.skip_after_tag(None), "skip_untagged")
        self.assertEqual(snr.skip_after_tag(_Tag(None, True)), "skip_non_research")
        self.assertEqual(snr.skip_after_tag(_Tag("TW", False)), "skip_non_research")
        self.assertIsNone(snr.skip_after_tag(_Tag("TW", True)))


class FallbackReportDateTests(unittest.TestCase):
    def _tmp_file_with_mtime(self, day: date) -> Path:
        fd, name = tempfile.mkstemp(suffix=".pdf")
        os.close(fd)
        path = Path(name)
        ts = datetime(day.year, day.month, day.day, 12, tzinfo=timezone.utc).timestamp()
        os.utime(path, (ts, ts))
        return path

    def test_existing_report_date_wins(self):
        path = self._tmp_file_with_mtime(date(2026, 6, 25))
        try:
            explicit = date(2024, 5, 4)
            self.assertEqual(
                snr.fallback_report_date_from_mtime(explicit, path, created_at=None),
                explicit,
            )
        finally:
            path.unlink()

    def test_live_sync_trusts_same_day_mtime_without_created_at(self):
        path = self._tmp_file_with_mtime(date(2026, 6, 25))
        try:
            self.assertEqual(
                snr.fallback_report_date_from_mtime(None, path, created_at=None),
                date(2026, 6, 25),
            )
        finally:
            path.unlink()

    def test_backfill_still_rejects_copy_like_mtime(self):
        path = self._tmp_file_with_mtime(date(2026, 6, 25))
        try:
            self.assertIsNone(
                snr.fallback_report_date_from_mtime(
                    None, path, created_at=date(2026, 6, 25)
                )
            )
        finally:
            path.unlink()


if __name__ == "__main__":
    unittest.main()
