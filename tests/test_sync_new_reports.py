import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import sync_new_reports as snr  # noqa: E402


def test_parse_rsync_delta_keeps_only_files_with_known_ext():
    lines = [
        "新增/",                      # 目錄列 → 略過
        "新增/0701 報告.pdf",
        "note.txt",                   # 非目標副檔名 → 略過
        "a/b/Taiwan daily.docx",
        "",                            # 空行
    ]
    out = snr.parse_rsync_delta(lines, Path("/local"))
    assert out == [
        Path("/local/新增/0701 報告.pdf"),
        Path("/local/a/b/Taiwan daily.docx"),
    ]


def test_parse_rsync_delta_dedupes_preserving_order():
    out = snr.parse_rsync_delta(["x.pdf", "x.pdf", "y.PDF"], Path("/d"))
    assert out == [Path("/d/x.pdf"), Path("/d/y.PDF")]


def test_parse_rsync_delta_dedupes_case_insensitively():
    # 來源為大小寫不敏感檔系統；同檔不同大小寫應視為同一檔，保留首次出現的原始路徑
    out = snr.parse_rsync_delta(["x.PDF", "x.pdf"], Path("/d"))
    assert out == [Path("/d/x.PDF")]


def test_skip_before_tag_priority_order():
    assert snr.skip_before_tag(True, False, False) == "skip_admin"
    assert snr.skip_before_tag(False, True, False) == "skip_scanned"
    assert snr.skip_before_tag(False, False, True) == "skip_exists"
    assert snr.skip_before_tag(False, False, False) is None


@dataclass
class _Tag:
    market: str | None
    is_research: bool


def test_skip_after_tag():
    assert snr.skip_after_tag(None) == "skip_untagged"
    assert snr.skip_after_tag(_Tag(None, True)) == "skip_non_research"
    assert snr.skip_after_tag(_Tag("TW", False)) == "skip_non_research"
    assert snr.skip_after_tag(_Tag("TW", True)) is None
