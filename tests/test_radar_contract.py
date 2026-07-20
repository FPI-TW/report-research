"""Radar OpenAPI 的 additive response 與分頁參數契約。"""
import json
import os
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("REPORT_MARK_ACCESS_USERNAME", "tester")
os.environ.setdefault("REPORT_MARK_ACCESS_PASSWORD", "testpass")
os.environ.setdefault("REPORT_MARK_SESSION_SECRET", "fixed-test-secret-0123456789")

from web import server  # noqa: E402


EXPECTED_MARKETS = {"TW", "US", "HK", "CN", "FX", "WTX", "MACRO", "GLOBAL", "CRYPTO"}


def _enum_values(value):
    if isinstance(value, dict):
        found = set(value.get("enum", []))
        for child in value.values():
            found.update(_enum_values(child))
        return found
    if isinstance(value, list):
        found = set()
        for child in value:
            found.update(_enum_values(child))
        return found
    return set()


class RadarOpenApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        server.app.openapi_schema = None
        cls.schema = server.app.openapi()

    def test_events_endpoint_declares_pagination_and_errors(self):
        path = "/api/instrument/{code}/radar/events"
        self.assertIn(path, self.schema["paths"])
        operation = self.schema["paths"][path]["get"]
        parameters = {item["name"]: item for item in operation["parameters"]}

        self.assertTrue(parameters["market"]["required"])
        self.assertEqual(parameters["window"]["schema"]["default"], "90")
        self.assertIn("90", json.dumps(parameters["window"]["schema"]))
        self.assertEqual(parameters["limit"]["schema"]["minimum"], 1)
        self.assertEqual(parameters["limit"]["schema"]["maximum"], 50)
        self.assertEqual(parameters["offset"]["schema"]["minimum"], 0)
        self.assertIn("404", operation["responses"])
        self.assertIn("422", operation["responses"])

    def test_events_response_metadata_is_required(self):
        schema = self.schema["components"]["schemas"]["RadarEventsResponse"]
        expected = {
            "market", "instrument_code", "window", "as_of", "total", "limit",
            "offset", "has_more", "next_offset", "items",
        }
        self.assertTrue(expected.issubset(set(schema["required"])))

    def test_all_radar_market_parameters_publish_the_same_enum(self):
        paths = (
            "/api/radar/instruments",
            "/api/instrument/{code}/radar/events",
            "/api/instrument/{code}/radar",
            "/api/instrument/{code}/radar/brokers/{broker}",
        )
        for path in paths:
            with self.subTest(path=path):
                operation = self.schema["paths"][path]["get"]
                parameters = {item["name"]: item for item in operation["parameters"]}
                self.assertEqual(
                    _enum_values(parameters["market"]["schema"]),
                    EXPECTED_MARKETS,
                )

    def test_all_radar_response_markets_publish_the_same_enum(self):
        response_schemas = (
            "RadarOverviewResponse",
            "RadarEventsResponse",
            "BrokerHistoryResponse",
            "RadarInstrumentItem",
        )
        schemas = self.schema["components"]["schemas"]

        for schema_name in response_schemas:
            with self.subTest(schema=schema_name):
                self.assertEqual(
                    _enum_values(schemas[schema_name]["properties"]["market"]),
                    EXPECTED_MARKETS,
                )

    def test_overview_and_catalog_pagination_fields_are_additive(self):
        schemas = self.schema["components"]["schemas"]
        overview = schemas["RadarOverviewResponse"]
        catalog = schemas["RadarInstrumentsResponse"]

        self.assertIn("recent_events_has_more", overview["properties"])
        self.assertIn("recent_events_next_offset", overview["properties"])
        self.assertNotIn("recent_events_has_more", overview.get("required", []))
        self.assertNotIn("recent_events_next_offset", overview.get("required", []))
        for field in ("limit", "has_more", "next_offset"):
            self.assertIn(field, catalog["properties"])


if __name__ == "__main__":
    unittest.main()
