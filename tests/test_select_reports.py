import sys
import unittest
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services.answer import build_context  # noqa: E402
from app.services.rows import ChunkRow  # noqa: E402


def _row(chunk_id, rid, fn, market, rdate, content, distance):
    return ChunkRow._make((
        chunk_id, rid, fn, market, "src", "sum", rdate, None,
        None, None, None, None, None, 0, content, distance,
    ))


# 固定「今天」讓新近度可重現
_NOW = datetime(2026, 7, 8, tzinfo=timezone.utc)


def _fixture():
    # 跨 tier、跨新近度、含長片段觸字數上限
    return [
        (2, 0.90, _row("c1", "rA", "A.pdf", "TW", "2026-07-01", "A 段一", 0.10)),
        (1, 0.70, _row("c2", "rB", "B.pdf", "US", "2026-01-01", "B 段一", 0.30)),
        (0, 0.65, _row("c3", "rC", "C.pdf", "TW", "2025-01-01", "C 段一（很舊）", 0.35)),
        (2, 0.88, _row("c1b", "rA", "A.pdf", "TW", "2026-07-01", "A 段二", 0.12)),
    ]


class BuildContextGoldenTests(unittest.TestCase):
    def test_golden_output_unchanged(self):
        sources, context = build_context(_fixture(), now=_NOW)
        got = ([asdict(s) for s in sources], context)
        # 執行者 Step 2：把實際 got 貼成 EXPECTED 後改為 assertEqual(got, EXPECTED)
        self.assertEqual(got, EXPECTED)


# 執行者於 Step 2 填入實際輸出（golden master）
EXPECTED = (
    [
        {
            "n": 1,
            "report_id": "rA",
            "file_name": "A.pdf",
            "market": "TW",
            "report_date": "2026-07-01",
            "is_latest": True,
        },
        {
            "n": 2,
            "report_id": "rB",
            "file_name": "B.pdf",
            "market": "US",
            "report_date": "2026-01-01",
            "is_latest": False,
        },
        {
            "n": 3,
            "report_id": "rC",
            "file_name": "C.pdf",
            "market": "TW",
            "report_date": "2025-01-01",
            "is_latest": False,
        },
    ],
    "[1] 報告：A.pdf（市場 TW，日期 2026-07-01）\n"
    "A 段一\n"
    "A 段二\n\n"
    "[2] 報告：B.pdf（市場 US，日期 2026-01-01）\n"
    "B 段一\n\n"
    "[3] 報告：C.pdf（市場 TW，日期 2025-01-01）\n"
    "C 段一（很舊）",
)


class SelectReportsTests(unittest.TestCase):
    def test_returns_ordered_selected_reports(self):
        from app.services.answer import (
            select_reports, MAX_REPORTS, MAX_PASSAGES_PER_REPORT,
            MAX_CONTEXT_CHARS, RECENCY_HALF_LIFE_DAYS, ASK_MIN_REPORTS,
            ASK_RELEVANCE_FLOOR, ASK_STALE_AGE_DAYS, ASK_MAX_STALE_REPORTS,
        )
        sel = select_reports(
            _fixture(),
            max_reports=MAX_REPORTS, max_passages=MAX_PASSAGES_PER_REPORT,
            max_chars=MAX_CONTEXT_CHARS, now=_NOW.date(),
            half_life_days=RECENCY_HALF_LIFE_DAYS, min_reports=ASK_MIN_REPORTS,
            relevance_floor=ASK_RELEVANCE_FLOOR, stale_age_days=ASK_STALE_AGE_DAYS,
            max_stale=ASK_MAX_STALE_REPORTS,
        )
        rids = [s.report_id for s in sel]
        self.assertEqual(rids[0], "rA")                 # 最高 tier/分數在前
        self.assertTrue(all(s.passages for s in sel))   # 皆有 kept passage
        a = next(s for s in sel if s.report_id == "rA")
        self.assertEqual(a.passages, ["A 段一", "A 段二"])  # 同報告多段累積


if __name__ == "__main__":
    unittest.main()
