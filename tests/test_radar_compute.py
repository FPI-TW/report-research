# tests/test_radar_compute.py
"""radar/compute.py 聚合測試（in-memory Signal fixtures，零 DB）。"""
import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.radar.compute import build_broker_history, build_overview  # noqa: E402
from app.services.radar.queries import CoverageCounts  # noqa: E402
from app.services.radar.types import DimensionStance, EpsEstimate, Signal  # noqa: E402


def _sig(broker, d, rating="neutral", rating_raw=None, target=None, currency=None,
         eps=(), thesis=None, code="2330"):
    return Signal(
        id=f"{broker}-{d}", report_id=f"r-{broker}-{d}", market="TW",
        instrument_code=code, broker=broker, report_date=d, rating_raw=rating_raw,
        rating_normalized=rating, target_price=target, target_currency=currency,
        target_horizon=None, target_price_evidence="TP 證據" if target else None,
        eps=tuple(eps), thesis=thesis or {}, extraction_status="valid",
        file_name=f"{broker}.pdf",
    )


def _cov(total=1, extracted=1, reports=5, name="台積電", has=True):
    return CoverageCounts(
        market="TW", instrument_code="2330", instrument_name=name,
        brokers_total=total, brokers_extracted=extracted, reports_available=reports,
        has_reports=has,
    )


def _eps(fy=2026, value=66.4):
    return EpsEstimate(fiscal_year=fy, period="FY", currency="TWD", unit="per_share",
                       value=value, evidence="EPS 證據")


def _st(stance):
    return DimensionStance(stance=stance, summary="摘要", evidence="論點證據")


class ConsensusTests(unittest.TestCase):
    def test_latest_per_broker_only(self):
        signals = [_sig("a", date(2026, 7, 1), "neutral"),
                   _sig("a", date(2026, 7, 10), "buy", rating_raw="買進")]
        ov = build_overview(signals, _cov(), window="90")
        self.assertEqual(ov.rating.bullish, 1)   # 只算最新的 buy
        self.assertEqual(ov.rating.neutral, 0)   # 舊的 neutral 不計入共識
        self.assertEqual(ov.rating.upgrades, 1)  # 最新 vs 前次 = 上調
        self.assertEqual(ov.coverage.brokers_in_consensus, 1)
        self.assertEqual(ov.as_of, "2026-07-10")

    def test_target_currency_grouping(self):
        signals = [
            _sig("a", date(2026, 7, 5), target=1200.0, currency="TWD"),
            _sig("b", date(2026, 7, 6), target=1300.0, currency="TWD"),
            _sig("c", date(2026, 7, 7), target=40.0, currency="USD"),
        ]
        ov = build_overview(signals, _cov(total=3, extracted=3), window="90")
        self.assertEqual(ov.target_price.primary_currency, "TWD")
        twd = [g for g in ov.target_price.groups if g.currency == "TWD"][0]
        self.assertEqual(twd.count, 2)
        self.assertEqual(twd.median, 1250.0)
        self.assertIsNotNone(ov.target_price.note)  # 提示 USD 未併入
        self.assertIn("USD", ov.target_price.note)

    def test_eps_grouped_by_fy(self):
        signals = [
            _sig("a", date(2026, 7, 5), eps=[_eps(2026, 66.0), _eps(2027, 72.0)]),
            _sig("b", date(2026, 7, 6), eps=[_eps(2026, 68.0)]),
        ]
        ov = build_overview(signals, _cov(total=2, extracted=2), window="90")
        fy26 = [g for g in ov.eps.groups if g.fiscal_year == 2026][0]
        self.assertEqual(fy26.count, 2)
        self.assertEqual(fy26.median, 67.0)


class StateTests(unittest.TestCase):
    def test_pending_extraction(self):
        ov = build_overview([], _cov(has=True), window="90")
        self.assertEqual(ov.coverage.state, "pending_extraction")
        self.assertIsNone(ov.rating)
        self.assertEqual(len(ov.thesis), 4)  # 四格仍在（insufficient）
        for t in ov.thesis:
            self.assertEqual(t.label, "insufficient")

    def test_partial_when_not_all_extracted(self):
        signals = [_sig("a", date(2026, 7, 10), "buy")]
        ov = build_overview(signals, _cov(total=8, extracted=1), window="90")
        self.assertEqual(ov.coverage.state, "partial")
        self.assertIn("1/8", ov.coverage.note)

    def test_ok_when_full(self):
        signals = [_sig("a", date(2026, 7, 10), "buy")]
        ov = build_overview(signals, _cov(total=1, extracted=1), window="90")
        self.assertEqual(ov.coverage.state, "ok")


class ThesisAggTests(unittest.TestCase):
    def test_four_dimensions_present(self):
        signals = [_sig("a", date(2026, 7, 10), thesis={"outlook": _st("positive")})]
        ov = build_overview(signals, _cov(), window="90")
        dims = {t.dimension for t in ov.thesis}
        self.assertEqual(dims, {"outlook", "catalyst", "risk", "valuation"})

    def test_strengthen_when_broker_upgrades_stance(self):
        signals = [
            _sig("a", date(2026, 7, 1), thesis={"outlook": _st("neutral")}),
            _sig("a", date(2026, 7, 10), thesis={"outlook": _st("positive")}),
        ]
        ov = build_overview(signals, _cov(), window="90")
        outlook = [t for t in ov.thesis if t.dimension == "outlook"][0]
        self.assertEqual(outlook.label, "strengthen")
        self.assertEqual(outlook.brokers_strengthen, 1)


class EventTests(unittest.TestCase):
    def test_event_on_material_change(self):
        signals = [
            _sig("a", date(2026, 7, 1), "neutral", rating_raw="中立"),
            _sig("a", date(2026, 7, 10), "buy", rating_raw="買進"),
        ]
        ov = build_overview(signals, _cov(), window="90")
        self.assertEqual(ov.recent_events_total, 1)
        self.assertEqual(ov.recent_events[0].broker, "a")
        self.assertIn("上調", ov.recent_events[0].headline)

    def test_no_event_without_prior(self):
        signals = [_sig("a", date(2026, 7, 10), "buy")]
        ov = build_overview(signals, _cov(), window="90")
        self.assertEqual(ov.recent_events_total, 0)

    def test_old_report_not_in_recent_when_window_excludes(self):
        # a 於窗期外(很久前)有一份、窗期內最新一份；事件只算窗期內那份
        signals = [
            _sig("a", date(2024, 1, 1), "neutral"),
            _sig("a", date(2026, 7, 10), "buy", rating_raw="買進"),
        ]
        ov = build_overview(signals, _cov(), window="30")
        # as_of=2026-07-10，窗期起點 2026-06-10；2024 那份在窗期外
        self.assertEqual(ov.recent_events_total, 1)
        self.assertEqual(ov.recent_events[0].report_date, "2026-07-10")


class BrokerHistoryTests(unittest.TestCase):
    def test_history_snapshots_and_diffs(self):
        signals = [
            _sig("a", date(2026, 6, 4), "neutral", rating_raw="中立", target=1120.0, currency="TWD"),
            _sig("a", date(2026, 7, 11), "buy", rating_raw="買進", target=1260.0, currency="TWD"),
        ]
        hist = build_broker_history(signals, market="TW", code="2330", broker="a", window="90")
        self.assertEqual(hist.report_count, 2)
        self.assertEqual(hist.current_rating, "buy")
        # 快照由新到舊
        self.assertEqual(hist.snapshots[0].report_date, "2026-07-11")
        # 最舊快照無前次可比較
        oldest = hist.diffs[-1]
        self.assertFalse(oldest.has_prior_comparable)
        self.assertIsNotNone(oldest.note)
        # 最新快照 vs 前次：目標價上修
        newest = hist.diffs[0]
        tp = [c for c in newest.changes if c.field == "target_price"][0]
        self.assertEqual(tp.direction, "up")
        self.assertTrue(tp.comparable)


if __name__ == "__main__":
    unittest.main()
