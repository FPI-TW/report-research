import sys
import unittest
from dataclasses import dataclass
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


if __name__ == "__main__":
    unittest.main()
