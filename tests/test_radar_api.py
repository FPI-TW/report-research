# tests/test_radar_api.py
"""雷達三端點 API 測試（TestClient + monkeypatch fetch，端點串接 + 驗證 + 空狀態）。"""
import os
import sys
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("REPORT_MARK_ACCESS_USERNAME", "tester")
os.environ.setdefault("REPORT_MARK_ACCESS_PASSWORD", "testpass")
os.environ.setdefault("REPORT_MARK_SESSION_SECRET", "fixed-test-secret-0123456789")

from fastapi.testclient import TestClient  # noqa: E402
from pydantic import ValidationError  # noqa: E402

from web import deps, server  # noqa: E402
from app.services.radar.queries import CoverageCounts, RadarInstrumentRow  # noqa: E402
from app.services.radar.types import Signal  # noqa: E402


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, *a, **k):
        raise AssertionError("端點不應直接打 DB（fetch 已被 monkeypatch）")


def _sig(
    broker="daiwa", d=date(2026, 7, 11), rating="buy", *, code="8046",
    market="TW", target=2444.0, target_evidence=None, signal_id=None,
    created_at=None,
):
    signal_id = signal_id or f"{broker}-{d}"
    return Signal(
        id=signal_id, report_id=f"r-{signal_id}", market=market, instrument_code=code,
        broker=broker, report_date=d, rating_raw="Buy", rating_normalized=rating,
        target_price=target, target_currency="TWD", target_horizon=None,
        target_price_evidence=target_evidence, eps=(), thesis={},
        extraction_status="valid", file_name=f"{broker}.pdf", created_at=created_at,
    )


def _cov(has=True, total=12, extracted=1, reports=91, *, code="8046", market="TW"):
    return CoverageCounts(
        market=market, instrument_code=code, instrument_name="南電",
        brokers_total=total, brokers_extracted=extracted, reports_available=reports,
        has_reports=has,
    )


def _broker_cov(instrument_reports=3, broker_reports=1):
    return SimpleNamespace(
        instrument_reports_available=instrument_reports,
        broker_reports_available=broker_reports,
    )


def _event_signals(*, code="8046"):
    return [
        _sig(
            rating="unknown", code=code, target=100.0 + index,
            target_evidence="TP 證據", signal_id=f"event-{index:02d}",
            created_at=datetime(2026, 7, 11, index, tzinfo=timezone.utc),
        )
        for index in range(14)
    ]


def _authed_client():
    client = TestClient(server.app, follow_redirects=False, base_url="http://127.0.0.1")
    r = client.post("/login", data={"username": "tester", "password": "testpass"})
    assert r.status_code == 303, f"login failed: {r.status_code}"
    return client


class RadarApiBase(unittest.TestCase):
    # 這些服務綁定已集中到 web.deps（handler 以 deps.X 呼叫），故 patch 目標是 deps。
    def setUp(self):
        self._missing = object()
        self._orig = {
            k: getattr(deps, k, self._missing)
            for k in (
                "SessionFactory", "fetch_coverage_counts", "fetch_instrument_signals",
                "fetch_broker_signals", "list_radar_instruments",
                "fetch_signals_for_instruments", "fetch_broker_coverage_counts",
            )
        }
        deps.SessionFactory = lambda: _FakeSession()

    def tearDown(self):
        for k, v in self._orig.items():
            if v is self._missing:
                if hasattr(deps, k):
                    delattr(deps, k)
            else:
                setattr(deps, k, v)

    def _set(self, **fns):
        for name, fn in fns.items():
            setattr(deps, name, fn)


class OverviewAuthAndValidationTests(RadarApiBase):
    def test_unauthenticated_401(self):
        client = TestClient(server.app, follow_redirects=False, base_url="http://127.0.0.1")
        r = client.get("/api/instrument/8046/radar?market=TW")
        self.assertEqual(r.status_code, 401)

    def test_invalid_window_422(self):
        async def cov(*a, **k):
            return _cov()
        self._set(fetch_coverage_counts=cov)
        r = _authed_client().get("/api/instrument/8046/radar?market=TW&window=15")
        self.assertEqual(r.status_code, 422)

    def test_invalid_market_422(self):
        r = _authed_client().get("/api/instrument/8046/radar?market=ZZ")
        self.assertEqual(r.status_code, 422)

    def test_missing_market_422(self):
        r = _authed_client().get("/api/instrument/8046/radar")
        self.assertEqual(r.status_code, 422)

    def test_unknown_code_404(self):
        async def cov(*a, **k):
            return _cov(has=False, total=0, reports=0)
        self._set(fetch_coverage_counts=cov)
        r = _authed_client().get("/api/instrument/9999/radar?market=TW")
        self.assertEqual(r.status_code, 404)


class OverviewShapeTests(RadarApiBase):
    def test_pending_extraction_200(self):
        async def cov(*a, **k):
            return _cov()

        async def sigs(*a, **k):
            return []

        def boom(*a, **k):
            raise AssertionError("總覽端點不應呼叫 broker-history（延遲載入）")

        self._set(fetch_coverage_counts=cov, fetch_instrument_signals=sigs,
                  fetch_broker_signals=boom)
        r = _authed_client().get("/api/instrument/8046/radar?market=TW")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["coverage"]["state"], "pending_extraction")
        self.assertIsNone(body["rating"])
        self.assertEqual(len(body["thesis"]), 4)

    def test_happy_path_200(self):
        async def cov(*a, **k):
            return _cov()

        async def sigs(*a, **k):
            return [_sig()]

        self._set(fetch_coverage_counts=cov, fetch_instrument_signals=sigs)
        r = _authed_client().get("/api/instrument/8046/radar?market=TW&window=90")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["instrument_code"], "8046")
        self.assertEqual(body["instrument_name"], "南電")
        self.assertEqual(body["coverage"]["state"], "partial")
        self.assertEqual(body["rating"]["bullish"], 1)
        self.assertEqual(len(body["brokers"]), 1)

    def test_code_with_slash_matches_overview_route(self):
        async def cov(session, market, code):
            self.assertEqual(code, "USD/TWD")
            return _cov(code=code)

        async def sigs(session, market, code):
            self.assertEqual(code, "USD/TWD")
            return [_sig(code=code)]

        self._set(fetch_coverage_counts=cov, fetch_instrument_signals=sigs)
        r = _authed_client().get("/api/instrument/USD%2FTWD/radar?market=TW")

        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["instrument_code"], "USD/TWD")


class RadarEventsTests(RadarApiBase):
    def test_unauthenticated_401(self):
        client = TestClient(server.app, follow_redirects=False, base_url="http://127.0.0.1")
        r = client.get("/api/instrument/8046/radar/events?market=TW")
        self.assertEqual(r.status_code, 401)

    def test_stable_paginated_shape(self):
        async def cov(*a, **k):
            return _cov(reports=14)

        async def sigs(*a, **k):
            return _event_signals()

        self._set(fetch_coverage_counts=cov, fetch_instrument_signals=sigs)
        r = _authed_client().get(
            "/api/instrument/8046/radar/events?market=TW&window=all&limit=5&offset=5"
        )

        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["market"], "TW")
        self.assertEqual(body["instrument_code"], "8046")
        self.assertEqual(body["window"], "all")
        self.assertEqual(body["total"], 13)
        self.assertEqual(body["limit"], 5)
        self.assertEqual(body["offset"], 5)
        self.assertTrue(body["has_more"])
        self.assertEqual(body["next_offset"], 10)
        self.assertEqual(len(body["items"]), 5)

    def test_known_instrument_without_signals_returns_empty_page(self):
        async def cov(*a, **k):
            return _cov()

        async def sigs(*a, **k):
            return []

        self._set(fetch_coverage_counts=cov, fetch_instrument_signals=sigs)
        r = _authed_client().get(
            "/api/instrument/8046/radar/events?market=TW&limit=5&offset=0"
        )

        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["total"], 0)
        self.assertEqual(body["items"], [])
        self.assertFalse(body["has_more"])
        self.assertIsNone(body["next_offset"])

    def test_unknown_instrument_404(self):
        async def cov(*a, **k):
            return _cov(has=False, total=0, reports=0)

        self._set(fetch_coverage_counts=cov)
        r = _authed_client().get("/api/instrument/9999/radar/events?market=TW")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json()["detail"], "instrument not found")

    def test_market_window_limit_and_offset_validation(self):
        client = _authed_client()
        paths = (
            "/api/instrument/8046/radar/events?market=ZZ",
            "/api/instrument/8046/radar/events?market=TW&window=15",
            "/api/instrument/8046/radar/events?market=TW&limit=0",
            "/api/instrument/8046/radar/events?market=TW&limit=51",
            "/api/instrument/8046/radar/events?market=TW&offset=-1",
        )
        for path in paths:
            with self.subTest(path=path):
                self.assertEqual(client.get(path).status_code, 422)

    def test_code_with_slash_matches_events_route(self):
        async def cov(session, market, code):
            self.assertEqual(code, "USD/TWD")
            return _cov(code=code)

        async def sigs(session, market, code):
            self.assertEqual(code, "USD/TWD")
            return []

        self._set(fetch_coverage_counts=cov, fetch_instrument_signals=sigs)
        r = _authed_client().get(
            "/api/instrument/USD%2FTWD/radar/events?market=TW&limit=5"
        )

        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["instrument_code"], "USD/TWD")


class BrokerHistoryTests(RadarApiBase):
    def test_known_broker_without_signals_returns_pending_200(self):
        async def bc(*a, **k):
            return _broker_cov()

        async def bs(*a, **k):
            return []

        self._set(fetch_broker_coverage_counts=bc, fetch_broker_signals=bs)
        r = _authed_client().get("/api/instrument/8046/radar/brokers/daiwa?market=TW")

        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["coverage_state"], "pending_extraction")
        self.assertEqual(body["report_count"], 0)
        self.assertEqual(body["snapshots"], [])
        self.assertEqual(body["diffs"], [])

    def test_unknown_instrument_404(self):
        async def bc(*a, **k):
            return _broker_cov(instrument_reports=0, broker_reports=0)

        async def bs(*a, **k):
            return []

        self._set(fetch_broker_coverage_counts=bc, fetch_broker_signals=bs)
        r = _authed_client().get("/api/instrument/9999/radar/brokers/daiwa?market=TW")

        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json()["detail"], "instrument not found")

    def test_unknown_canonical_broker_404(self):
        async def bc(*a, **k):
            return _broker_cov(instrument_reports=3, broker_reports=0)

        async def bs(*a, **k):
            return []

        self._set(fetch_broker_coverage_counts=bc, fetch_broker_signals=bs)
        r = _authed_client().get("/api/instrument/8046/radar/brokers/unknown?market=TW")

        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json()["detail"], "broker not found")

    def test_undated_known_broker_returns_window_empty_200(self):
        async def bc(*a, **k):
            return _broker_cov()

        async def bs(*a, **k):
            return [_sig(d=None)]

        self._set(fetch_broker_coverage_counts=bc, fetch_broker_signals=bs)
        r = _authed_client().get(
            "/api/instrument/8046/radar/brokers/daiwa?market=TW&window=30"
        )

        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["coverage_state"], "window_empty")
        self.assertFalse(r.json()["snapshots"][0]["in_window"])

    def test_history_200(self):
        async def bc(*a, **k):
            return _broker_cov()

        async def bs(*a, **k):
            return [_sig(d=date(2026, 7, 11)), _sig(d=date(2026, 6, 1), rating="neutral")]

        self._set(fetch_broker_coverage_counts=bc, fetch_broker_signals=bs)
        r = _authed_client().get("/api/instrument/8046/radar/brokers/daiwa?market=TW&window=all")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["report_count"], 2)
        self.assertEqual(body["current_rating"], "buy")
        self.assertFalse(body["diffs"][-1]["has_prior_comparable"])  # 最舊快照無前次

    def test_code_and_broker_with_slash_match_history_route(self):
        async def bc(session, market, code, broker):
            self.assertEqual((code, broker), ("USD/TWD", "A/B"))
            return _broker_cov()

        async def bs(session, market, code, broker):
            self.assertEqual((code, broker), ("USD/TWD", "A/B"))
            return [_sig(broker="A/B", code="USD/TWD")]

        self._set(fetch_broker_coverage_counts=bc, fetch_broker_signals=bs)
        r = _authed_client().get(
            "/api/instrument/USD%2FTWD/radar/brokers/A%2FB?market=TW&window=all"
        )

        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["instrument_code"], "USD/TWD")
        self.assertEqual(body["broker"], "A/B")


class InstrumentCatalogTests(RadarApiBase):
    def test_catalog_200(self):
        async def lst(*a, **k):
            return 2, [
                RadarInstrumentRow("TW", "8046", "南電", 12, 91, date(2026, 7, 11), "partial"),
                RadarInstrumentRow("TW", "9914", "美利達", 11, 179, date(2026, 7, 9), "partial"),
            ]

        async def batch(*a, **k):
            return {}  # 無共識資料 → consensus 皆 None

        self._set(list_radar_instruments=lst, fetch_signals_for_instruments=batch)
        r = _authed_client().get(
            "/api/radar/instruments?market=TW&q=南電&limit=2&offset=0"
        )
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["total"], 2)
        self.assertEqual(body["limit"], 2)
        self.assertEqual(body["offset"], 0)
        self.assertFalse(body["has_more"])
        self.assertIsNone(body["next_offset"])
        self.assertEqual(body["items"][0]["instrument_code"], "8046")
        self.assertEqual(body["items"][0]["market_display"], "台股")
        self.assertIsNone(body["items"][0]["consensus"])

    def test_catalog_filtered_total_and_next_offset(self):
        calls = []

        async def lst(*a, **k):
            calls.append(k)
            return 7, [
                RadarInstrumentRow(
                    "TW", "8046", "南電", 12, 91,
                    date(2026, 7, 11), "partial",
                ),
                RadarInstrumentRow(
                    "TW", "9914", "美利達", 11, 179,
                    date(2026, 7, 9), "partial",
                ),
            ]

        async def batch(*a, **k):
            return {}

        self._set(list_radar_instruments=lst, fetch_signals_for_instruments=batch)
        r = _authed_client().get(
            "/api/radar/instruments?market=TW&q=南&limit=2&offset=2"
        )

        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["total"], 7)
        self.assertEqual(body["limit"], 2)
        self.assertEqual(body["offset"], 2)
        self.assertTrue(body["has_more"])
        self.assertEqual(body["next_offset"], 4)
        self.assertEqual(calls[0]["q"], "南")

    def test_catalog_with_consensus(self):
        async def lst(*a, **k):
            return 1, [
                RadarInstrumentRow("TW", "8046", "南電", 12, 91, date(2026, 7, 11), "partial"),
            ]

        async def batch(session, keys, **k):
            return {("TW", "8046"): [_sig()]}

        self._set(list_radar_instruments=lst, fetch_signals_for_instruments=batch)
        r = _authed_client().get("/api/radar/instruments?market=TW")
        self.assertEqual(r.status_code, 200)
        item = r.json()["items"][0]
        self.assertIsNotNone(item["consensus"])
        self.assertEqual(item["consensus"]["stance"]["rating"], "buy")
        self.assertEqual(item["consensus"]["stance"]["total_rated"], 1)
        self.assertEqual(item["consensus"]["target"]["currency"], "TWD")

    def test_catalog_invalid_market_422(self):
        r = _authed_client().get("/api/radar/instruments?market=ZZ")
        self.assertEqual(r.status_code, 422)

    def test_catalog_rejects_unsupported_response_market(self):
        async def lst(*a, **k):
            return 1, [
                RadarInstrumentRow(
                    "UNKNOWN", "8046", "南電", 1, 1,
                    date(2026, 7, 11), "ok",
                )
            ]

        async def batch(*a, **k):
            return {}

        self._set(list_radar_instruments=lst, fetch_signals_for_instruments=batch)

        with self.assertRaises(ValidationError):
            _authed_client().get("/api/radar/instruments?with_consensus=false")


if __name__ == "__main__":
    unittest.main()
