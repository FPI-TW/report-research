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

    def test_reports_sql_does_not_select_full_text(self):
        # 2026-10-12：這句每輪對整個子集跑，帶內文時 TW 一個 market 回傳約 193 MB，主機一忙就撞
        # statement_timeout、訊號擷取 rc=1。內文改由 load_full_text 只撈要送 LLM 的那幾份。
        select = es.build_reports_sql().split("FROM")[0]
        self.assertNotIn("full_text", select)
        self.assertEqual([c.strip() for c in select.replace("SELECT", "").split(",")],
                         ["r.id::text", "r.source", "r.report_date", "r.market",
                          "r.file_name", "r.stock_targets", "r.file_hash"])

    def test_full_text_sql_binds_ids_with_cast(self):
        sql = es.build_full_text_sql()
        self.assertIn("r.id = ANY(CAST(:ids AS uuid[]))", sql)
        self.assertTrue(sql.startswith("SELECT r.id::text, r.full_text "))

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



class _TextSession:
    """只認 `build_full_text_sql()`：回傳 `texts` 裡有的 id，並記下每一句綁的 ids。"""

    def __init__(self, texts, calls):
        self.texts, self.calls = texts, calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, stmt, params):
        assert str(stmt) == es.build_full_text_sql(), str(stmt)
        self.calls.append(list(params["ids"]))
        rows = [(rid, self.texts[rid]) for rid in params["ids"] if rid in self.texts]
        return mock.Mock(all=mock.Mock(return_value=rows))


def _item(rid, full_text=None):
    return es.WorkItem(rid, "TW", "券商甲", None, f"{rid}.pdf", full_text, ["2330"], file_hash=f"h-{rid}")


class LoadFullTextTests(unittest.IsolatedAsyncioTestCase):
    async def _load(self, items, texts):
        calls = []
        with mock.patch.object(es, "SessionFactory", lambda: _TextSession(texts, calls)):
            out = await es.load_full_text(items)
        return out, calls

    async def test_fills_text_in_bounded_batches_and_keeps_order(self):
        n = es.FULL_TEXT_BATCH * 2 + 3
        items = [_item(f"r{i}") for i in range(n)]
        out, calls = await self._load(items, {f"r{i}": f"內文{i}" for i in range(n)})
        self.assertEqual([len(c) for c in calls], [es.FULL_TEXT_BATCH, es.FULL_TEXT_BATCH, 3])
        self.assertEqual([it.report_id for it in out], [f"r{i}" for i in range(n)])
        self.assertEqual([it.full_text for it in out], [f"內文{i}" for i in range(n)])

    async def test_report_that_vanished_or_has_no_text_is_dropped(self):
        # 建清單之後被重新入庫（換 report_id）或刪除：送空內文給 LLM 只會多一筆 rejected。
        items = [_item("r1"), _item("gone"), _item("empty"), _item("r2")]
        out, _ = await self._load(items, {"r1": "甲", "empty": "", "r2": "乙"})
        self.assertEqual([it.report_id for it in out], ["r1", "r2"])

    async def test_items_that_already_have_text_do_not_touch_the_db(self):
        def boom():
            raise AssertionError("不該開 session")

        items = [_item("r1", "甲"), _item("r2", "乙")]
        with mock.patch.object(es, "SessionFactory", boom):
            out = await es.load_full_text(items)
        self.assertEqual(out, items)


class WorklistTextLoadingTests(unittest.IsolatedAsyncioTestCase):
    """main 的順序：先套 --limit，再只替留下來的那幾份撈內文。"""

    def _args(self, **kw):
        base = dict(min_brokers=3, min_reports=5, top_n=50, workers=1, limit=None, excerpt=16000,
                    model="deepseek-flash", dry_run=False, reextract=False, retry_blocked=False)
        base.update(kw)
        return mock.Mock(**base)

    async def _main(self, args, items, texts):
        calls, extracted = [], []

        async def fake_extract_one(sem, item, excerpt, model, total, **kw):
            extracted.append((item.report_id, item.full_text, total))

        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(es, "FAIL_LOG", Path(tmp) / "f.log"), \
             mock.patch.object(es.llm_failures, "open_recorder", new=mock.AsyncMock(return_value=None)), \
             mock.patch.object(es, "build_worklist", new=mock.AsyncMock(return_value=([], items))), \
             mock.patch.object(es, "SessionFactory", lambda: _TextSession(texts, calls)), \
             mock.patch.object(es, "extract_one", new=fake_extract_one):
            await es.main(args)
        return calls, extracted

    async def test_only_the_limited_slice_gets_its_text_loaded(self):
        items = [_item(f"r{i}") for i in range(10)]
        calls, extracted = await self._main(self._args(limit=3), items, {f"r{i}": f"內文{i}" for i in range(10)})
        self.assertEqual(calls, [["r0", "r1", "r2"]])
        self.assertEqual(extracted, [("r0", "內文0", 3), ("r1", "內文1", 3), ("r2", "內文2", 3)])

    async def test_dry_run_never_loads_text(self):
        calls, extracted = await self._main(self._args(dry_run=True), [_item("r1")], {"r1": "甲"})
        self.assertEqual((calls, extracted), ([], []))

    async def test_total_counts_only_items_that_still_have_text(self):
        calls, extracted = await self._main(self._args(), [_item("r1"), _item("gone")], {"r1": "甲"})
        self.assertEqual(extracted, [("r1", "甲", 1)])

    async def test_build_worklist_leaves_text_unloaded(self):
        reports = [("r1", "券商", None, "TW", "a.pdf", ["2330"], "hA")]

        class _Ctx:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

        with mock.patch.object(es, "SessionFactory", _Ctx), \
             mock.patch.object(es, "_fetch_subset", new=mock.AsyncMock(return_value=[("2330", "TW", 5, 9)])), \
             mock.patch.object(es, "_fetch_reports", new=mock.AsyncMock(return_value=reports)), \
             mock.patch.object(es, "_fetch_done_map", new=mock.AsyncMock(return_value={})):
            _, worklist = await es.build_worklist(3, 5, 50, False)
        self.assertEqual([(w.report_id, w.full_text, w.requested_codes, w.file_hash) for w in worklist],
                         [("r1", None, ["2330"], "hA")])


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
