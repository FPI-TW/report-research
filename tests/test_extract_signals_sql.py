# tests/test_extract_signals_sql.py
"""純字串/結構斷言 extract_signals 的 SQL builder 與 row 序列化（不連 DB、不呼叫 LLM）。"""
import asyncio
import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services import llm_http as lh  # noqa: E402
from app.services.signal_extract import SignalRow  # noqa: E402

# 以檔案路徑載入 scripts/extract_signals.py（scripts 非套件）
_spec = importlib.util.spec_from_file_location(
    "extract_signals", REPO_ROOT / "scripts" / "extract_signals.py"
)
es = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(es)


class SubsetSqlTests(unittest.TestCase):
    def test_subset_sql_structure(self):
        sql = es.build_subset_sql()
        self.assertIn("unnest(r.stock_targets)", sql)
        self.assertIn("count(DISTINCT r.source)", sql)
        self.assertIn("HAVING", sql)
        self.assertIn(":min_brokers", sql)
        self.assertIn(":min_reports", sql)
        self.assertIn(":top_n", sql)
        self.assertIn("GROUP BY st, r.market", sql)
        # 依覆蓋度排序（券商數優先）
        self.assertIn("ORDER BY broker_count DESC", sql)

    def test_reports_sql_uses_array_overlap_and_named_params(self):
        sql = es.build_reports_sql()
        self.assertIn("r.stock_targets && CAST(:codes AS text[])", sql)
        self.assertIn(":market", sql)
        self.assertIn("r.full_text IS NOT NULL", sql)
        self.assertIn("r.is_research IS NOT FALSE", sql)
        # 無字串拼接：不得出現 f-string 殘留或 % 格式
        self.assertNotIn("%s", sql)

    def test_existing_signals_sql(self):
        sql = es.build_existing_signals_sql()
        self.assertIn("FROM research.report_signal", sql)
        self.assertIn(":report_ids", sql)
        self.assertIn("extraction_status", sql)
        self.assertIn("extraction_version", sql)


class UpsertSqlTests(unittest.TestCase):
    def test_upsert_on_conflict(self):
        sql = str(es.SIGNAL_UPSERT_SQL)
        self.assertIn("INSERT INTO research.report_signal", sql)
        self.assertIn("ON CONFLICT (report_id, market, instrument_code) DO UPDATE", sql)
        self.assertIn("CAST(:eps_estimates AS jsonb)", sql)
        self.assertIn("CAST(:thesis_dimensions AS jsonb)", sql)
        self.assertIn("CAST(:raw_payload AS jsonb)", sql)

    def test_reextract_preserves_created_at(self):
        sql = str(es.SIGNAL_UPSERT_SQL)
        self.assertNotIn("created_at = now()", sql)


class RowToParamsTests(unittest.TestCase):
    def _row(self):
        return SignalRow(
            report_id="r1", market="TW", instrument_code="2330", broker="券商甲",
            report_date=date(2026, 7, 11), rating_raw="買進", rating_normalized="buy",
            target_price=1220.0, target_currency="TWD", target_horizon="12M",
            target_price_evidence="目標價 1220",
            eps_estimates=[{"fiscal_year": 2026, "period": "FY", "currency": "TWD",
                            "unit": "per_share", "value": 66.4, "evidence": "e"}],
            thesis_dimensions={"outlook": {"stance": "positive", "summary": "s", "evidence": "e"}},
            extraction_version="v1", extraction_status="valid",
            raw_payload={"instrument_code": "2330"}, error_detail=None,
        )

    def test_jsonb_fields_serialized_to_str(self):
        p = es.row_to_params(self._row())
        self.assertIsInstance(p["eps_estimates"], str)
        self.assertIsInstance(p["thesis_dimensions"], str)
        self.assertIsInstance(p["raw_payload"], str)
        # 可反序列化回原結構
        self.assertEqual(json.loads(p["eps_estimates"])[0]["value"], 66.4)
        self.assertEqual(json.loads(p["thesis_dimensions"])["outlook"]["stance"], "positive")
        # ensure_ascii=False → 中文不轉義
        self.assertIn("券商甲", json.dumps(p["broker"], ensure_ascii=False))

    def test_id_generated_and_scalars_passthrough(self):
        p = es.row_to_params(self._row())
        self.assertTrue(p["id"])  # 產生 uuid
        self.assertEqual(p["report_id"], "r1")
        self.assertEqual(p["rating_normalized"], "buy")
        self.assertEqual(p["target_price"], 1220.0)
        self.assertEqual(p["report_date"], date(2026, 7, 11))

    def test_none_raw_payload_stays_none(self):
        row = self._row()
        row.raw_payload = None
        p = es.row_to_params(row)
        self.assertIsNone(p["raw_payload"])


class CheckpointTests(unittest.TestCase):
    def test_done_when_all_valid_current_version(self):
        existing = {
            "2330": ("valid", es.EXTRACTION_VERSION),
            "2317": ("partial", es.EXTRACTION_VERSION),
        }
        self.assertTrue(es._is_done(["2330", "2317"], existing, reextract=False))

    def test_not_done_when_missing_code(self):
        existing = {"2330": ("valid", es.EXTRACTION_VERSION)}
        self.assertFalse(es._is_done(["2330", "2317"], existing, reextract=False))

    def test_not_done_when_rejected(self):
        existing = {"2330": ("rejected", es.EXTRACTION_VERSION)}
        self.assertFalse(es._is_done(["2330"], existing, reextract=False))

    def test_not_done_when_version_mismatch(self):
        existing = {"2330": ("valid", "old-version")}
        self.assertFalse(es._is_done(["2330"], existing, reextract=False))

    def test_reextract_forces_redo(self):
        existing = {"2330": ("valid", es.EXTRACTION_VERSION)}
        self.assertFalse(es._is_done(["2330"], existing, reextract=True))



class RawPayloadModelFlowTests(unittest.IsolatedAsyncioTestCase):
    """extract_one 把產出模型交給 build_rows（遷移 PR-15）：回應的 model 欄優先，缺時退回請求的。"""

    async def _run(self, results):
        captured = {}

        def fake_build_rows(*a, **k):
            captured.update(k)
            return []

        it = iter(results)
        item = es.WorkItem("rep-1", "TW", "券商甲", None, "f.pdf", "內文", ["2330"], file_hash="h1")
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(es, "FAIL_LOG", Path(tmp) / "f.log"), \
             mock.patch.object(es, "call_cli", side_effect=lambda *a, **k: next(it)), \
             mock.patch.object(es, "build_rows", side_effect=fake_build_rows), \
             mock.patch.object(es, "_upsert_rows", new=mock.AsyncMock()):
            await es.extract_one(asyncio.Semaphore(1), item, 16000, "deepseek-flash", 1)
        return captured.get("model")

    async def test_response_model_wins(self):
        ok = es.CliResult('{"signals": []}', None, "deepseek-flash-0925")
        self.assertEqual(await self._run([ok]), "deepseek-flash-0925")

    async def test_falls_back_to_requested_model(self):
        self.assertEqual(await self._run([es.CliResult('{"signals": []}', None)]), "deepseek-flash")

    async def test_no_response_no_model(self):
        err = es.CliResult(None, lh.error_string(lh.TIMEOUT, "超過總期限"))
        self.assertIsNone(await self._run([err, err, err]))


if __name__ == "__main__":
    unittest.main()
