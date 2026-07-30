# tests/test_radar_compute.py
"""radar/compute.py 聚合測試（in-memory Signal fixtures，零 DB）。"""
import sys
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.radar import compute as radar_compute  # noqa: E402
from app.services.radar.compute import (  # noqa: E402
    build_broker_history,
    build_instrument_slim,
    build_overview,
)
from app.services.radar.queries import CoverageCounts  # noqa: E402
from app.services.radar.types import DimensionStance, EpsEstimate, Signal  # noqa: E402


def _sig(broker, d, rating="neutral", rating_raw=None, target=None, currency=None,
         eps=(), thesis=None, code="2330", signal_id=None, created_at=None,
         extraction_status="valid"):
    signal_id = signal_id or f"{broker}-{d}"
    signal = Signal(
        id=signal_id, report_id=f"r-{signal_id}", market="TW",
        instrument_code=code, broker=broker, report_date=d, rating_raw=rating_raw,
        rating_normalized=rating, target_price=target, target_currency=currency,
        target_horizon=None, target_price_evidence="TP 證據" if target else None,
        eps=tuple(eps), thesis=thesis or {}, extraction_status=extraction_status,
        file_name=f"{broker}.pdf",
    )
    # RED 階段 Signal 尚無 created_at；object.__setattr__ 讓排序行為先可被測試。
    if created_at is not None:
        object.__setattr__(signal, "created_at", created_at)
    return signal


def _cov(total=1, extracted=1, reports=5, name="台積電", has=True):
    return CoverageCounts(
        market="TW", instrument_code="2330", instrument_name=name,
        brokers_total=total, brokers_extracted=extracted, reports_available=reports,
        has_reports=has,
    )


def _eps(
    fy=2026, value=66.4, period="FY", currency="TWD", unit="per_share",
    evidence="EPS 證據",
):
    return EpsEstimate(fiscal_year=fy, period=period, currency=currency, unit=unit,
                       value=value, evidence=evidence)


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

    def test_eps_primary_is_order_independent_and_policy_driven(self):
        def primary(eps, *, as_of=date(2026, 7, 10)):
            ov = build_overview([_sig("a", as_of, eps=eps)], _cov(), window="all")
            return ov.eps.primary

        # broker count 是第一順位，即使該 FY 已過期仍勝出。
        count_wins = build_overview(
            [
                _sig("a", date(2026, 7, 10), eps=[_eps(2025), _eps(2026)]),
                _sig("b", date(2026, 7, 9), eps=[_eps(2025)]),
            ],
            _cov(total=2, extracted=2),
            window="all",
        )
        self.assertEqual(count_wins.eps.primary.fiscal_year, 2025)

        # 同 count 時，有 FY 的群組優先；再取最近且未過期的 FY。
        fy_wins = primary([_eps(None, period="LTM"), _eps(2028), _eps(2027)])
        self.assertEqual(fy_wins.fiscal_year, 2027)

        # 若所有 FY 都已過期，取最新 FY。
        past = primary([_eps(2024), _eps(2025)])
        self.assertEqual(past.fiscal_year, 2025)

        # 完整 key 是最後 tie-break，輸入順序不得改變 primary。
        first = primary([
            _eps(2027, currency="USD"),
            _eps(2027, currency="TWD"),
        ])
        second = primary([
            _eps(2027, currency="TWD"),
            _eps(2027, currency="USD"),
        ])
        self.assertEqual(first.model_dump(), second.model_dump())
        self.assertEqual(first.currency, "TWD")

    def test_eps_primary_counts_each_broker_once(self):
        ov = build_overview(
            [
                _sig(
                    "a", date(2026, 7, 10),
                    eps=[_eps(2025), _eps(2025), _eps(2025)],
                ),
                _sig("b", date(2026, 7, 9), eps=[_eps(2026)]),
                _sig("c", date(2026, 7, 8), eps=[_eps(2026)]),
            ],
            _cov(total=3, extracted=3),
            window="all",
        )
        self.assertEqual(ov.eps.primary.fiscal_year, 2026)

    def test_eps_primary_prefers_annual_period(self):
        ov = build_overview(
            [
                _sig(
                    "a", date(2026, 7, 10),
                    eps=[_eps(2027, period="Q1"), _eps(2028, period="FY")],
                )
            ],
            _cov(),
            window="all",
        )
        self.assertEqual(ov.eps.primary.period, "FY")
        self.assertEqual(ov.eps.primary.fiscal_year, 2028)

    def test_rating_movement_uses_window_baseline_not_last_hop(self):
        signals = [
            _sig("a", date(2026, 6, 20), "neutral"),
            _sig("a", date(2026, 7, 1), "buy"),
            _sig("a", date(2026, 7, 10), "neutral"),
        ]
        ov = build_overview(signals, _cov(reports=3), window="30")

        self.assertEqual(ov.rating.upgrades, 0)
        self.assertEqual(ov.rating.downgrades, 0)
        self.assertEqual(ov.rating.unchanged, 1)

    def test_rating_movement_uses_nearest_comparable_pre_window_baseline(self):
        signals = [
            _sig("a", date(2026, 5, 1), "neutral"),
            _sig("a", date(2026, 6, 1), "unknown"),
            _sig("a", date(2026, 7, 10), "buy"),
        ]
        ov = build_overview(signals, _cov(reports=3), window="30")

        self.assertEqual(ov.rating.upgrades, 1)
        self.assertEqual(ov.rating.downgrades, 0)
        self.assertEqual(ov.rating.unchanged, 0)


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

    def test_partial_signal_forces_partial_coverage(self):
        signals = [
            _sig(
                "a", date(2026, 7, 10), "buy",
                extraction_status="partial",
            )
        ]
        ov = build_overview(signals, _cov(total=1, extracted=1), window="90")
        self.assertEqual(ov.coverage.state, "partial")

    def test_null_and_blank_brokers_do_not_form_consensus(self):
        signals = [
            _sig(None, date(2026, 7, 10), "buy"),
            _sig("   ", date(2026, 7, 9), "sell"),
        ]
        ov = build_overview(
            signals,
            _cov(total=0, extracted=0, reports=2),
            window="90",
        )
        self.assertEqual(ov.coverage.brokers_in_consensus, 0)
        self.assertIsNone(ov.rating)
        self.assertEqual(ov.brokers, [])
        self.assertEqual(ov.recent_events_total, 0)

    def test_unattributed_signal_does_not_advance_finite_window_anchor(self):
        attributed = _sig("a", date(2026, 1, 1), "buy")
        unattributed = _sig(None, date(2026, 7, 10), "sell")

        ov = build_overview(
            [attributed, unattributed],
            _cov(total=1, extracted=1, reports=2),
            window="30",
        )
        slim = build_instrument_slim([attributed, unattributed], window="30")

        self.assertEqual(ov.as_of, "2026-01-01")
        self.assertEqual(ov.coverage.brokers_in_consensus, 1)
        self.assertEqual(ov.rating.median_rating, "buy")
        self.assertFalse(ov.brokers[0].stale)
        self.assertIsNotNone(slim)
        self.assertEqual(slim.stance.rating, "buy")

    def test_undated_signal_is_only_in_all_window(self):
        signal = _sig("a", None, "buy")

        finite = build_overview([signal], _cov(), window="30")
        all_time = build_overview([signal], _cov(), window="all")
        finite_history = build_broker_history(
            [signal], market="TW", code="2330", broker="a", window="30"
        )
        all_history = build_broker_history(
            [signal], market="TW", code="2330", broker="a", window="all"
        )

        self.assertEqual(finite.coverage.brokers_in_consensus, 0)
        self.assertIsNone(finite.rating)
        self.assertTrue(finite.brokers[0].stale)
        self.assertFalse(finite_history.snapshots[0].in_window)
        self.assertEqual(all_time.coverage.brokers_in_consensus, 1)
        self.assertEqual(all_time.rating.bullish, 1)
        self.assertFalse(all_time.brokers[0].stale)
        self.assertTrue(all_history.snapshots[0].in_window)

    def test_same_day_latest_uses_created_at_before_uuid(self):
        older = _sig(
            "a", date(2026, 7, 10), "sell", signal_id="z-older",
            created_at=datetime(2026, 7, 10, 8, tzinfo=timezone.utc),
        )
        newer = _sig(
            "a", date(2026, 7, 10), "buy", signal_id="a-newer",
            created_at=datetime(2026, 7, 10, 9, tzinfo=timezone.utc),
        )

        ov = build_overview([older, newer], _cov(reports=2), window="90")
        history = build_broker_history(
            [older, newer], market="TW", code="2330", broker="a", window="90"
        )

        self.assertEqual(ov.rating.median_rating, "buy")
        self.assertEqual(ov.brokers[0].latest_rating, "buy")
        self.assertEqual(history.current_rating, "buy")
        self.assertEqual(history.snapshots[0].report_id, "r-a-newer")


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
    @staticmethod
    def _thirteen_same_day_events():
        return [
            _sig(
                "a", date(2026, 7, 10), "unknown",
                target=100.0 + index, currency="TWD", signal_id=f"event-{index:02d}",
                created_at=datetime(2026, 7, 10, index, tzinfo=timezone.utc),
            )
            for index in range(14)
        ]

    def test_overview_events_are_three_item_preview_with_full_total(self):
        ov = build_overview(
            self._thirteen_same_day_events(), _cov(reports=14), window="all"
        )

        self.assertEqual(ov.recent_events_total, 13)
        self.assertEqual(len(ov.recent_events), 3)
        self.assertTrue(ov.recent_events_has_more)
        self.assertEqual(ov.recent_events_next_offset, 3)

    def test_event_pages_are_stable_complete_and_non_overlapping(self):
        builder = getattr(radar_compute, "build_events_page", None)
        self.assertIsNotNone(builder)
        signals = self._thirteen_same_day_events()

        pages = [
            builder(
                signals, market="TW", code="2330", window="all",
                limit=5, offset=offset,
            )
            for offset in (0, 5, 10)
        ]
        ids = [
            item.report_link.report_id
            for page in pages
            for item in page.items
        ]

        self.assertEqual([page.total for page in pages], [13, 13, 13])
        self.assertEqual([len(page.items) for page in pages], [5, 5, 3])
        self.assertEqual([page.has_more for page in pages], [True, True, False])
        self.assertEqual([page.next_offset for page in pages], [5, 10, None])
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(ids, [f"r-event-{index:02d}" for index in range(1, 14)])

    def test_event_on_material_change(self):
        signals = [
            _sig(
                "a", date(2026, 7, 1), "neutral", rating_raw="中立",
                target=1000.0, currency="TWD",
            ),
            _sig(
                "a", date(2026, 7, 10), "buy", rating_raw="買進",
                target=1200.0, currency="TWD",
            ),
        ]
        ov = build_overview(signals, _cov(), window="90")
        self.assertEqual(ov.recent_events_total, 1)
        self.assertEqual(ov.recent_events[0].broker, "a")
        self.assertIn("目標價上修", ov.recent_events[0].headline)
        self.assertEqual(
            [change.field for change in ov.recent_events[0].changes],
            ["target_price"],
        )
        self.assertEqual(ov.recent_events[0].evidence, ["TP 證據"])

    def test_no_event_without_prior(self):
        signals = [_sig("a", date(2026, 7, 10), "buy")]
        ov = build_overview(signals, _cov(), window="90")
        self.assertEqual(ov.recent_events_total, 0)

    def test_old_report_not_in_recent_when_window_excludes(self):
        # a 於窗期外(很久前)有一份、窗期內最新一份；事件只算窗期內那份
        signals = [
            _sig(
                "a", date(2024, 1, 1), "neutral",
                target=1000.0, currency="TWD",
            ),
            _sig(
                "a", date(2026, 7, 10), "buy", rating_raw="買進",
                target=1200.0, currency="TWD",
            ),
        ]
        ov = build_overview(signals, _cov(), window="30")
        # as_of=2026-07-10，窗期起點 2026-06-10；2024 那份在窗期外
        self.assertEqual(ov.recent_events_total, 1)
        self.assertEqual(ov.recent_events[0].report_date, "2026-07-10")

    def test_material_change_without_evidence_does_not_create_event(self):
        signals = [
            _sig("a", date(2026, 7, 1), "neutral"),
            _sig("a", date(2026, 7, 10), "buy"),
        ]
        ov = build_overview(signals, _cov(reports=2), window="90")
        self.assertEqual(ov.recent_events_total, 0)

    def test_eps_event_uses_matching_group_evidence(self):
        signals = [
            _sig(
                "a", date(2026, 7, 1), "unknown",
                eps=[_eps(2026, 60.0, evidence="舊證據")],
            ),
            _sig(
                "a", date(2026, 7, 10), "unknown",
                eps=[
                    _eps(2027, 70.0, evidence="不相干證據"),
                    _eps(2026, 66.0, evidence="FY2026 對應證據"),
                ],
            ),
        ]
        ov = build_overview(signals, _cov(reports=2), window="90")

        self.assertEqual(ov.recent_events_total, 1)
        self.assertEqual(ov.recent_events[0].evidence, ["FY2026 對應證據"])

    def test_eps_evidence_matches_collision_free_group_identity(self):
        signals = [
            _sig(
                "a", date(2026, 7, 1), "unknown",
                eps=[_eps(2026, 60.0, period=None, evidence="舊證據")],
            ),
            _sig(
                "a", date(2026, 7, 10), "unknown",
                eps=[
                    _eps(
                        2026, 70.0, period="期間未註明",
                        evidence="碰撞但不相干證據",
                    ),
                    _eps(2026, 66.0, period=None, evidence="真正對應證據"),
                ],
            ),
        ]
        ov = build_overview(signals, _cov(reports=2), window="90")

        self.assertEqual(ov.recent_events_total, 1)
        self.assertEqual(ov.recent_events[0].evidence, ["真正對應證據"])

    def test_event_evidence_follows_eps_headline_priority(self):
        signals = [
            _sig(
                "a", date(2026, 7, 1), "unknown",
                target=1000.0, currency="TWD",
                eps=[_eps(2026, 60.0, evidence="舊 EPS 證據")],
            ),
            _sig(
                "a", date(2026, 7, 10), "unknown",
                target=1200.0, currency="TWD",
                eps=[_eps(2026, 66.0, evidence="EPS 標題對應證據")],
            ),
        ]
        ov = build_overview(signals, _cov(reports=2), window="90")

        event = ov.recent_events[0]
        self.assertIn("EPS", event.headline)
        self.assertEqual(
            [change.field for change in event.changes],
            ["target_price", "eps"],
        )
        self.assertEqual(event.evidence, ["EPS 標題對應證據", "TP 證據"])

    def test_event_evidence_follows_target_headline_priority(self):
        signals = [
            _sig(
                "a", date(2026, 7, 1), "unknown",
                target=1000.0, currency="TWD",
                thesis={"outlook": _st("neutral")},
            ),
            _sig(
                "a", date(2026, 7, 10), "unknown",
                target=1200.0, currency="TWD",
                thesis={"outlook": _st("positive")},
            ),
        ]
        ov = build_overview(signals, _cov(reports=2), window="90")

        event = ov.recent_events[0]
        self.assertIn("目標價上修", event.headline)
        self.assertEqual(
            [change.field for change in event.changes],
            ["target_price", "thesis"],
        )
        self.assertEqual(event.evidence, ["TP 證據", "論點證據"])


class BrokerSummaryTests(unittest.TestCase):
    def test_broker_summary_eps_uses_own_metadata(self):
        signals = [
            _sig(
                "a", date(2026, 7, 10), "buy", target=1200.0, currency="TWD",
                eps=[
                    _eps(
                        2027, 5.0, period="Q1", currency="USD",
                        unit="per_share",
                    )
                ],
            )
        ]
        broker = build_overview(signals, _cov(), window="90").brokers[0]

        self.assertEqual(broker.latest_target_currency, "TWD")
        self.assertEqual(broker.latest_eps_value, 5.0)
        self.assertEqual(broker.latest_eps_fy, 2027)
        self.assertEqual(broker.latest_eps_period, "Q1")
        self.assertEqual(broker.latest_eps_currency, "USD")
        self.assertEqual(broker.latest_eps_unit, "per_share")

    def test_duplicate_eps_group_is_aggregated_order_independently(self):
        def result(eps):
            signal = _sig("a", date(2026, 7, 10), "buy", eps=eps)
            broker = build_overview([signal], _cov(), window="90").brokers[0]
            snapshot = build_broker_history(
                [signal], market="TW", code="2330", broker="a", window="90"
            ).snapshots[0]
            return broker, snapshot

        forward = result([_eps(2027, 5.0), _eps(2027, 7.0)])
        reverse = result([_eps(2027, 7.0), _eps(2027, 5.0)])

        self.assertEqual(forward[0].latest_eps_value, 6.0)
        self.assertEqual(reverse[0].latest_eps_value, 6.0)
        self.assertEqual(forward[1].primary_eps.median, 6.0)
        self.assertEqual(reverse[1].primary_eps.median, 6.0)
        self.assertEqual(len(forward[1].eps), 1)
        self.assertEqual(len(reverse[1].eps), 1)


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

    def test_history_uses_prior_comparable_report_and_matching_eps_group(self):
        signals = [
            _sig(
                "a", date(2026, 5, 1), "unknown", signal_id="old-comparable",
                eps=[_eps(2026, 60.0, evidence="舊 FY2026")],
            ),
            _sig(
                "a", date(2026, 6, 1), "unknown", signal_id="near-mismatch",
                eps=[_eps(2025, 50.0, evidence="FY2025")],
            ),
            _sig(
                "a", date(2026, 7, 11), "unknown", signal_id="current",
                eps=[
                    _eps(2027, 70.0, evidence="不相干 FY2027"),
                    _eps(2026, 66.0, evidence="對應 FY2026"),
                ],
            ),
        ]
        hist = build_broker_history(
            signals, market="TW", code="2330", broker="a", window="90"
        )

        newest = hist.diffs[0]
        self.assertTrue(newest.has_prior_report)
        self.assertTrue(newest.has_prior_comparable)
        self.assertEqual(newest.from_report_id, "r-old-comparable")
        self.assertEqual(newest.from_report_date, "2026-05-01")
        comparable_eps = [
            change for change in newest.changes
            if change.field == "eps" and change.comparable
        ]
        self.assertEqual(len(comparable_eps), 1)
        self.assertIn("2026", comparable_eps[0].label)

        middle = hist.diffs[1]
        self.assertTrue(middle.has_prior_report)
        self.assertFalse(middle.has_prior_comparable)
        self.assertIsNone(middle.from_report_id)

        newest_snapshot = hist.snapshots[0]
        self.assertIsNotNone(newest_snapshot.primary_eps)
        self.assertEqual(newest_snapshot.primary_eps.fiscal_year, 2026)


class InstrumentSlimTests(unittest.TestCase):
    def test_slim_happy_path(self):
        signals = [
            _sig("a", date(2026, 7, 1), "neutral", target=1000.0, currency="TWD"),
            _sig("a", date(2026, 7, 10), "buy", rating_raw="買進", target=1200.0, currency="TWD"),
            _sig("b", date(2026, 7, 9), "overweight", target=1100.0, currency="TWD"),
        ]
        c = build_instrument_slim(signals, window="90")
        self.assertIsNotNone(c)
        # 每家最新：a=buy、b=overweight → 皆偏多
        self.assertEqual(c.stance.bullish, 2)
        self.assertEqual(c.stance.total_rated, 2)
        self.assertEqual(c.stance.rating, "buy")  # 中位 [buy,overweight] 向偏多取整
        # a: neutral→buy 上調1；b 無前次；淨 = 1
        self.assertEqual(c.stance.upgrades, 1)
        self.assertEqual(c.stance.net_rating, 1)
        # 目標價 primary TWD 中位 [1200,1100]=1150；a 1000→1200 上修
        self.assertEqual(c.target.currency, "TWD")
        self.assertEqual(c.target.median, 1150.0)
        self.assertEqual(c.target.revision_direction, "up")

    def test_slim_none_when_no_signals(self):
        self.assertIsNone(build_instrument_slim([], window="90"))

    def test_slim_none_when_all_unknown(self):
        signals = [_sig("a", date(2026, 7, 10), "unknown")]
        self.assertIsNone(build_instrument_slim(signals, window="90"))

    def test_slim_target_none_without_prices(self):
        c = build_instrument_slim([_sig("a", date(2026, 7, 10), "buy")], window="90")
        self.assertIsNotNone(c)
        self.assertIsNone(c.target)
        self.assertEqual(c.stance.rating, "buy")
        self.assertEqual(c.stance.net_rating, 0)  # 無前次
        self.assertEqual(len(c.stance.distribution), 5)  # 五級皆在（供迷你條）


if __name__ == "__main__":
    unittest.main()
