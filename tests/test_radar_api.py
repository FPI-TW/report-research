# tests/test_radar_api.py
"""雷達三端點 API 測試（TestClient + monkeypatch fetch，端點串接 + 驗證 + 空狀態）。"""
import os
import sys
import unittest
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("REPORT_MARK_ACCESS_USERNAME", "tester")
os.environ.setdefault("REPORT_MARK_ACCESS_PASSWORD", "testpass")
os.environ.setdefault("REPORT_MARK_SESSION_SECRET", "fixed-test-secret-0123456789")

from fastapi.testclient import TestClient  # noqa: E402

from web import server  # noqa: E402
from app.services.radar.queries import CoverageCounts, RadarInstrumentRow  # noqa: E402
from app.services.radar.types import Signal  # noqa: E402


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, *a, **k):
        raise AssertionError("端點不應直接打 DB（fetch 已被 monkeypatch）")


def _sig(broker="daiwa", d=date(2026, 7, 11), rating="buy"):
    return Signal(
        id=f"{broker}-{d}", report_id=f"r-{broker}", market="TW", instrument_code="8046",
        broker=broker, report_date=d, rating_raw="Buy", rating_normalized=rating,
        target_price=2444.0, target_currency="TWD", target_horizon=None,
        target_price_evidence=None, eps=(), thesis={}, extraction_status="valid",
        file_name=f"{broker}.pdf",
    )


def _cov(has=True, total=12, extracted=1, reports=91):
    return CoverageCounts(
        market="TW", instrument_code="8046", instrument_name="南電",
        brokers_total=total, brokers_extracted=extracted, reports_available=reports,
        has_reports=has,
    )


def _authed_client():
    client = TestClient(server.app, follow_redirects=False, base_url="http://127.0.0.1")
    r = client.post("/login", data={"username": "tester", "password": "testpass"})
    assert r.status_code == 303, f"login failed: {r.status_code}"
    return client


class RadarApiBase(unittest.TestCase):
    def setUp(self):
        self._orig = {
            k: getattr(server, k)
            for k in (
                "SessionFactory", "fetch_coverage_counts", "fetch_instrument_signals",
                "fetch_broker_signals", "list_radar_instruments",
            )
        }
        server.SessionFactory = lambda: _FakeSession()

    def tearDown(self):
        for k, v in self._orig.items():
            setattr(server, k, v)

    def _set(self, **fns):
        for name, fn in fns.items():
            setattr(server, name, fn)


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


class BrokerHistoryTests(RadarApiBase):
    def test_no_signals_404(self):
        async def bs(*a, **k):
            return []
        self._set(fetch_broker_signals=bs)
        r = _authed_client().get("/api/instrument/8046/radar/brokers/daiwa?market=TW")
        self.assertEqual(r.status_code, 404)

    def test_history_200(self):
        async def bs(*a, **k):
            return [_sig(d=date(2026, 7, 11)), _sig(d=date(2026, 6, 1), rating="neutral")]
        self._set(fetch_broker_signals=bs)
        r = _authed_client().get("/api/instrument/8046/radar/brokers/daiwa?market=TW&window=all")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["report_count"], 2)
        self.assertEqual(body["current_rating"], "buy")
        self.assertFalse(body["diffs"][-1]["has_prior_comparable"])  # 最舊快照無前次


class InstrumentCatalogTests(RadarApiBase):
    def test_catalog_200(self):
        async def lst(*a, **k):
            return 2, [
                RadarInstrumentRow("TW", "8046", "南電", 12, 91, date(2026, 7, 11), "partial"),
                RadarInstrumentRow("TW", "9914", "美利達", 11, 179, date(2026, 7, 9), "partial"),
            ]
        self._set(list_radar_instruments=lst)
        r = _authed_client().get("/api/radar/instruments?market=TW")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["total"], 2)
        self.assertEqual(body["items"][0]["instrument_code"], "8046")
        self.assertEqual(body["items"][0]["market_display"], "台股")

    def test_catalog_invalid_market_422(self):
        r = _authed_client().get("/api/radar/instruments?market=ZZ")
        self.assertEqual(r.status_code, 422)


if __name__ == "__main__":
    unittest.main()
