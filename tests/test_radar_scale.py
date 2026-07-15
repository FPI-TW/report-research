# tests/test_radar_scale.py
"""radar/scale.py 決定性基元測試（純函式，零 DB），逐條對應設計規格「服務層驗收」。"""
import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.radar import scale  # noqa: E402
from app.services.radar.scale import (  # noqa: E402
    classify_dimension,
    diff_signals,
    is_material,
    pct_change,
    quantiles,
    rating_bucket,
    rating_direction,
    rating_scale,
    stance_constructiveness,
)
from app.services.radar.types import DimensionStance, EpsEstimate, Signal  # noqa: E402


def _sig(
    broker="a", report_date=date(2026, 7, 1), rating="neutral", rating_raw=None,
    target=None, currency=None, eps=(), thesis=None, code="2330",
):
    return Signal(
        id=f"s-{broker}-{report_date}", report_id=f"r-{broker}-{report_date}",
        market="TW", instrument_code=code, broker=broker, report_date=report_date,
        rating_raw=rating_raw, rating_normalized=rating,
        target_price=target, target_currency=currency, target_horizon=None,
        target_price_evidence=None, eps=tuple(eps), thesis=thesis or {},
        extraction_status="valid",
    )


def _eps(fy=2026, period="FY", currency="TWD", unit="per_share", value=66.4):
    return EpsEstimate(fiscal_year=fy, period=period, currency=currency, unit=unit,
                       value=value, evidence="e")


def _st(stance):
    return DimensionStance(stance=stance, summary="s", evidence="e")


class RatingTests(unittest.TestCase):
    def test_scale(self):
        self.assertEqual(rating_scale("buy"), 2)
        self.assertEqual(rating_scale("sell"), -2)
        self.assertEqual(rating_scale("neutral"), 0)
        self.assertIsNone(rating_scale("unknown"))
        self.assertIsNone(rating_scale(None))

    def test_bucket(self):
        self.assertEqual(rating_bucket("buy"), "bullish")
        self.assertEqual(rating_bucket("underweight"), "bearish")
        self.assertEqual(rating_bucket("neutral"), "neutral")
        self.assertIsNone(rating_bucket("unknown"))

    def test_direction_up_down_flat(self):
        self.assertEqual(rating_direction("neutral", "buy"), "up")
        self.assertEqual(rating_direction("buy", "neutral"), "down")
        self.assertEqual(rating_direction("buy", "buy"), "flat")

    def test_direction_unknown_is_none(self):
        self.assertEqual(rating_direction("unknown", "buy"), "none")
        self.assertEqual(rating_direction("buy", "unknown"), "none")


class QuantilesTests(unittest.TestCase):
    def test_single_value(self):
        q = quantiles([100.0])
        self.assertEqual((q.median, q.q1, q3 := q.q3, q.low, q.high, q.count),
                         (100.0, 100.0, 100.0, 100.0, 100.0, 1))

    def test_multiple(self):
        q = quantiles([1080.0, 1185.0, 1320.0, None])  # None 略過
        self.assertEqual(q.count, 3)
        self.assertEqual(q.low, 1080.0)
        self.assertEqual(q.high, 1320.0)
        self.assertEqual(q.median, 1185.0)

    def test_empty(self):
        self.assertIsNone(quantiles([None]))
        self.assertIsNone(quantiles([]))


class PctChangeTests(unittest.TestCase):
    def test_basic(self):
        self.assertAlmostEqual(pct_change(100.0, 103.2), 3.2)
        self.assertAlmostEqual(pct_change(100.0, 96.0), -4.0)

    def test_guards(self):
        self.assertIsNone(pct_change(0.0, 10.0))
        self.assertIsNone(pct_change(None, 10.0))
        self.assertIsNone(pct_change(10.0, None))


class StanceTests(unittest.TestCase):
    def test_per_dimension(self):
        self.assertEqual(stance_constructiveness("outlook", "positive"), 1)
        self.assertEqual(stance_constructiveness("outlook", "negative"), -1)
        self.assertEqual(stance_constructiveness("valuation", "attractive"), 1)
        self.assertEqual(stance_constructiveness("valuation", "stretched"), -1)
        # 風險升高 = 建設性下降
        self.assertEqual(stance_constructiveness("risk", "rising"), -1)
        self.assertEqual(stance_constructiveness("risk", "easing"), 1)

    def test_unmapped_none(self):
        self.assertIsNone(stance_constructiveness("risk", "positive"))  # 詞表外
        self.assertIsNone(stance_constructiveness("outlook", None))


class ClassifyDimensionTests(unittest.TestCase):
    def test_insufficient(self):
        self.assertEqual(classify_dimension(0, 0, 0, 0), "insufficient")

    def test_stable(self):
        self.assertEqual(classify_dimension(0, 0, 3, 3), "stable")

    def test_strengthen(self):
        self.assertEqual(classify_dimension(4, 0, 1, 5), "strengthen")

    def test_weaken(self):
        self.assertEqual(classify_dimension(0, 4, 1, 5), "weaken")

    def test_diverging(self):
        # up=3 down=2 → 弱側 2 >= 0.4*5=2 → 分歧擴大
        self.assertEqual(classify_dimension(3, 2, 0, 5), "diverging")

    def test_lean_not_diverging(self):
        # up=5 down=1 → 弱側 1 < 0.4*6=2.4 → 轉強（非分歧）
        self.assertEqual(classify_dimension(5, 1, 0, 6), "strengthen")


class DiffSignalsTests(unittest.TestCase):
    def test_no_prev_no_events(self):
        self.assertEqual(diff_signals(None, _sig()), [])

    def test_rating_upgrade(self):
        prev = _sig(rating="neutral", rating_raw="中立")
        curr = _sig(rating="overweight", rating_raw="加碼", report_date=date(2026, 7, 8))
        changes = diff_signals(prev, curr)
        rating = [c for c in changes if c.field == "rating"][0]
        self.assertEqual(rating.direction, "up")
        self.assertTrue(rating.comparable)
        self.assertIn("中立", rating.label)
        self.assertTrue(is_material(rating))

    def test_rating_unknown_not_material(self):
        prev = _sig(rating="unknown")
        curr = _sig(rating="buy")
        rating = [c for c in diff_signals(prev, curr) if c.field == "rating"]
        self.assertEqual(rating, [])  # 含 unknown → 不產生評等變化

    def test_target_same_currency_comparable(self):
        prev = _sig(target=1120.0, currency="TWD")
        curr = _sig(target=1260.0, currency="TWD")
        tp = [c for c in diff_signals(prev, curr) if c.field == "target_price"][0]
        self.assertTrue(tp.comparable)
        self.assertEqual(tp.direction, "up")
        self.assertAlmostEqual(tp.pct_change, 12.5)

    def test_target_diff_currency_incomparable(self):
        prev = _sig(target=40.0, currency="USD")
        curr = _sig(target=1260.0, currency="TWD")
        tp = [c for c in diff_signals(prev, curr) if c.field == "target_price"][0]
        self.assertFalse(tp.comparable)
        self.assertEqual(tp.direction, "incomparable")
        self.assertEqual(tp.incomparable_reason, "幣別不同")
        self.assertFalse(is_material(tp))

    def test_eps_same_key_comparable(self):
        prev = _sig(eps=[_eps(value=64.0)])
        curr = _sig(eps=[_eps(value=66.4)])
        eps = [c for c in diff_signals(prev, curr) if c.field == "eps"][0]
        self.assertTrue(eps.comparable)
        self.assertEqual(eps.direction, "up")

    def test_eps_diff_fy_no_change(self):
        prev = _sig(eps=[_eps(fy=2025, value=60.0)])
        curr = _sig(eps=[_eps(fy=2026, value=66.4)])
        eps = [c for c in diff_signals(prev, curr) if c.field == "eps"]
        self.assertEqual(eps, [])  # 不同 FY 找不到同鍵 → 不比

    def test_thesis_stance_change_event(self):
        prev = _sig(thesis={"outlook": _st("neutral")})
        curr = _sig(thesis={"outlook": _st("positive")})
        th = [c for c in diff_signals(prev, curr) if c.field == "thesis"][0]
        self.assertEqual(th.direction, "up")
        self.assertEqual(th.dimension, "outlook")

    def test_thesis_same_stance_no_event(self):
        prev = _sig(thesis={"outlook": _st("positive")})
        curr = _sig(thesis={"outlook": _st("positive")})
        th = [c for c in diff_signals(prev, curr) if c.field == "thesis"]
        self.assertEqual(th, [])  # 相同 stance → 不產生事件

    def test_thesis_prev_missing_no_fake_event(self):
        prev = _sig(thesis={})
        curr = _sig(thesis={"risk": _st("rising")})
        th = [c for c in diff_signals(prev, curr) if c.field == "thesis"]
        self.assertEqual(th, [])  # 前次缺該維 → 不產生假事件

    def test_thesis_risk_rising_is_down(self):
        prev = _sig(thesis={"risk": _st("easing")})
        curr = _sig(thesis={"risk": _st("rising")})
        th = [c for c in diff_signals(prev, curr) if c.field == "thesis"][0]
        self.assertEqual(th.direction, "down")  # 風險升高 = 建設性下降


if __name__ == "__main__":
    unittest.main()
