# tests/test_retrieval_rank.py
import sys
import unittest
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services.retrieval import rank_reports  # noqa: E402


def row(report_id, report_date=None):
    """造一列符合 hybrid_search 回傳結構的 row（rank_reports 只讀 [1] 與 [6]）。"""
    r = [None] * 16
    r[1] = report_id
    r[6] = report_date
    return tuple(r)


def scored(*items):
    """items: (report_id, tier, fused, date) → [(tier, fused, row), ...]。"""
    return [(tier, fused, row(rid, d)) for (rid, tier, fused, d) in items]


class RankReportsTests(unittest.TestCase):
    def ids(self, ranked):
        return [g.report_id for g in ranked]

    def test_group_dedup_keeps_best_first_chunk(self):
        # 同報告多 chunk：tier/best_score 取首見（最佳），match_count 累計
        s = scored(
            ("A", 2, 0.90, date(2024, 1, 1)),
            ("A", 0, 0.40, date(2024, 1, 1)),
        )
        ranked = rank_reports(s, sort="relevance")
        self.assertEqual(len(ranked), 1)
        self.assertEqual(ranked[0].tier, 2)
        self.assertEqual(ranked[0].best_score, 0.90)
        self.assertEqual(ranked[0].match_count, 2)
        self.assertEqual(len(ranked[0].passages), 2)

    def test_higher_tier_wins_even_if_older(self):
        s = scored(
            ("OLD2", 2, 0.50, date(2020, 1, 1)),
            ("NEW0", 0, 0.95, date(2024, 1, 1)),
        )
        self.assertEqual(self.ids(rank_reports(s, sort="relevance")), ["OLD2", "NEW0"])

    def test_higher_band_wins_within_tier_even_if_older(self):
        # 0.80 vs 0.60：band 16 vs 12 → 高 band 在前，不看日期
        s = scored(
            ("HIGH", 0, 0.80, date(2020, 1, 1)),
            ("LOW", 0, 0.60, date(2024, 1, 1)),
        )
        self.assertEqual(self.ids(rank_reports(s, sort="relevance")), ["HIGH", "LOW"])

    def test_within_same_band_newer_wins(self):
        # 0.71 與 0.73 同 band(14, 0.70~0.7499) → 日期新者在前
        s = scored(
            ("OLD", 0, 0.73, date(2021, 1, 1)),
            ("NEW", 0, 0.71, date(2024, 1, 1)),
        )
        self.assertEqual(self.ids(rank_reports(s, sort="relevance")), ["NEW", "OLD"])

    def test_none_date_sorts_last_within_band(self):
        s = scored(
            ("HASDATE", 0, 0.72, date(2021, 1, 1)),
            ("NODATE", 0, 0.72, None),
        )
        self.assertEqual(self.ids(rank_reports(s, sort="relevance")), ["HASDATE", "NODATE"])

    def test_date_desc_ignores_relevance(self):
        s = scored(
            ("OLDREL", 2, 0.99, date(2020, 1, 1)),
            ("NEW", 0, 0.30, date(2024, 1, 1)),
        )
        self.assertEqual(self.ids(rank_reports(s, sort="date_desc")), ["NEW", "OLDREL"])

    def test_date_asc_oldest_first_none_last(self):
        s = scored(
            ("MID", 0, 0.5, date(2022, 1, 1)),
            ("OLD", 0, 0.5, date(2020, 1, 1)),
            ("NODATE", 0, 0.5, None),
        )
        self.assertEqual(self.ids(rank_reports(s, sort="date_asc")), ["OLD", "MID", "NODATE"])

    def test_band_boundary_multiple_of_width(self):
        # 0.10 與 0.05 必須落在不同 band（浮點邊界不可黏在一起）
        s = scored(
            ("B2", 0, 0.10, date(2020, 1, 1)),
            ("B1", 0, 0.05, date(2024, 1, 1)),
        )
        self.assertEqual(self.ids(rank_reports(s, sort="relevance")), ["B2", "B1"])


if __name__ == "__main__":
    unittest.main()
