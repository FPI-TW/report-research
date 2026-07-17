# tests/test_retrieval_rank.py
import sys
import unittest
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services.retrieval import rank_reports  # noqa: E402
from app.services.rows import ChunkRow  # noqa: E402


def row(report_id, report_date=None):
    """造一列符合 hybrid_search 回傳結構的 ChunkRow。"""
    return ChunkRow(
        chunk_id=None,
        report_id=report_id,
        file_hash=f"h-{report_id}",
        file_name=None,
        market=None,
        source=None,
        summary=None,
        report_date=report_date,
        report_type=None,
        instrument_types=None,
        relates_stock=None,
        relates_futures=None,
        stock_targets=None,
        futures_targets=None,
        chunk_index=0,
        content="",
        distance=0.0,
    )


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


class HybridSearchConfigTests(unittest.IsolatedAsyncioTestCase):
    async def test_lex_unlimited_forwards_none_limit_to_lexical_search(self):
        from app.services import retrieval as ret

        seen = {}

        async def fake_dense(*a, **k):
            return []

        async def fake_lex(*a, **k):
            seen.update(k)
            return []

        orig = (ret.search_chunks_meta, ret.search_chunks_lexical)
        ret.search_chunks_meta = fake_dense
        ret.search_chunks_lexical = fake_lex
        try:
            await ret.hybrid_search(
                object(),
                "台積電",
                [0.0],
                lex_unlimited=True,
            )
        finally:
            ret.search_chunks_meta, ret.search_chunks_lexical = orig

        self.assertIsNone(seen["limit"])


class TierBandContractTests(unittest.TestCase):
    """守約回歸：搜尋頁 band 契約凍結、rank_reports 與問答 band 設定解耦。"""

    def test_band_width_frozen(self):
        from app.services import retrieval as ret

        self.assertEqual(ret.BAND_WIDTH, 0.05)

    def test_tier_constants_frozen(self):
        from app.services import retrieval as ret

        self.assertEqual(ret.TIER_SEMANTIC, 0)
        self.assertEqual(ret.TIER_ALL_TERMS, 1)
        self.assertEqual(ret.TIER_PHRASE, 2)

    def test_rank_reports_decoupled_from_ask_relevance_band(self):
        # 0.80 vs 0.60 在 BAND_WIDTH=0.05 下屬不同 band → 高分在前；
        # 若誤耦合 ask_relevance_band（此處設 0.5 使兩者同 band），
        # 排序會翻成日期新者在前，此測試即抓到。
        import dataclasses
        from unittest import mock

        import app.config as config

        patched = dataclasses.replace(config.get_settings(), ask_relevance_band=0.5)
        with mock.patch.object(config, "_SETTINGS", patched):
            s = scored(
                ("HIGH", 0, 0.80, date(2020, 1, 1)),
                ("LOW", 0, 0.60, date(2024, 1, 1)),
            )
            ranked = rank_reports(s, sort="relevance")
        self.assertEqual([g.report_id for g in ranked], ["HIGH", "LOW"])


class HybridSearchTierTests(unittest.IsolatedAsyncioTestCase):
    """tier 常數值必須與 hybrid_search 融合輸出一致（共用契約的來源端）。"""

    @staticmethod
    def _crow(chunk_id, content):
        return row(f"R{chunk_id}")._replace(
            chunk_id=chunk_id, content=content, distance=0.5
        )

    async def test_fusion_tiers_match_named_constants(self):
        from app.services import retrieval as ret

        rows = [
            self._crow(1, "AI伺服器需求強勁"),  # 片語命中 → TIER_PHRASE
            self._crow(2, "ai 帶動伺服器出貨"),  # 全詞命中（無片語）→ TIER_ALL_TERMS
            self._crow(3, "ai 應用概況"),  # 部分命中 → TIER_SEMANTIC
        ]

        async def fake_dense(*a, **k):
            return rows

        async def fake_lex(*a, **k):
            return []

        orig = (ret.search_chunks_meta, ret.search_chunks_lexical)
        ret.search_chunks_meta = fake_dense
        ret.search_chunks_lexical = fake_lex
        try:
            out = await ret.hybrid_search(object(), "AI伺服器", [0.0])
        finally:
            ret.search_chunks_meta, ret.search_chunks_lexical = orig

        tiers = {r_.chunk_id: tier for tier, _fused, r_ in out}
        self.assertEqual(tiers[1], ret.TIER_PHRASE)
        self.assertEqual(tiers[2], ret.TIER_ALL_TERMS)
        self.assertEqual(tiers[3], ret.TIER_SEMANTIC)


if __name__ == "__main__":
    unittest.main()
