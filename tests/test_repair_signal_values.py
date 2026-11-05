"""scripts/repair_signal_values.py 的修復規劃與安全閘（不連 DB、不呼叫 LLM）。"""
import asyncio
import csv
import importlib.util
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services.signal_extract import (  # noqa: E402
    TARGET_NOTE_IMPLAUSIBLE,
    TARGET_NOTE_INLINE_PRICE,
    TARGET_NOTE_TABLE_COLUMN,
)

_spec = importlib.util.spec_from_file_location(
    "repair_signal_values", REPO_ROOT / "scripts" / "repair_signal_values.py"
)
rs = importlib.util.module_from_spec(_spec)
sys.modules["repair_signal_values"] = rs  # dataclass 解析型別時要查得到模組
_spec.loader.exec_module(rs)


def _rec(id_="s1", report_id=None, code="2308", broker="kgi", day=date(2026, 11, 2), raw="增加持股",
         rating="overweight", price=None, currency="TWD", evidence=None, status="valid", error=None):
    return rs.SignalRecord(
        id=id_, report_id=report_id or f"r-{id_}", instrument_code=code, broker=broker, report_date=day,
        rating_raw=raw, rating_normalized=rating, target_price=price,
        target_currency=currency if price is not None else None, target_horizon="12M" if price is not None else None,
        target_price_evidence=evidence, extraction_status=status, error_detail=error,
    )


class PlanTests(unittest.TestCase):
    def test_clean_row_untouched(self):
        rows = [_rec(price=2505, evidence="下調 12 個月目標價至 2,505 元")]
        self.assertEqual(rs.plan_changes(rows), [])

    def test_rating_only_change(self):
        c = rs.plan_changes([_rec(raw="OW", rating="unknown")])[0]
        self.assertEqual(c.new_rating, "overweight")
        self.assertIsNone(c.target_note)
        self.assertEqual(c.new_status, "valid")

    def test_wrong_direction_fixed(self):
        c = rs.plan_changes([_rec(raw="買進轉中立", rating="buy")])[0]
        self.assertEqual(c.new_rating, "neutral")

    def test_nothing_extracted_row_becomes_valid(self):
        c = rs.plan_changes([_rec(raw="OW", rating="unknown", status="partial", error="無可擷取內容")])[0]
        self.assertEqual((c.new_status, c.new_error), ("valid", None))

    def test_other_partial_reason_stays_partial(self):
        c = rs.plan_changes([_rec(raw="OW", rating="unknown", status="partial", error="eps 丟棄 1 筆（缺欄）")])[0]
        self.assertEqual((c.new_status, c.new_error), ("partial", "eps 丟棄 1 筆（缺欄）"))

    def test_inline_price_cleared_and_note_appended(self):
        rows = [_rec(price=1355, evidence="台達電 (2308 TT，NT$1,355，增加持股)", error="eps 丟棄 1 筆（缺欄）")]
        c = rs.plan_changes(rows)[0]
        self.assertEqual(c.target_note, TARGET_NOTE_INLINE_PRICE)
        self.assertEqual(c.new_status, "partial")
        self.assertEqual(c.new_error, f"eps 丟棄 1 筆（缺欄）；{TARGET_NOTE_INLINE_PRICE}")
        self.assertIs(rs._update_params(c)["clear_target"], True)

    def test_table_column_cleared(self):
        rows = [_rec(price=3770, evidence="世芯 -KY 3661 TT 增加持股 6,180 10,225 3,770.0")]
        self.assertEqual(rs.plan_changes(rows)[0].target_note, TARGET_NOTE_TABLE_COLUMN)

    def test_rating_change_keeps_target_values(self):
        c = rs.plan_changes([_rec(raw="B", rating="unknown", price=850, evidence="金像電(2368 TT，B，850)")])[0]
        self.assertIs(rs._update_params(c)["clear_target"], False)

    def test_update_condition_uses_exact_decimal(self):
        """回歸（devdb 演練抓到）：條件綁 float 時 19.6 對不上 numeric 的 19.6000，那列被靜默略過。"""
        rec = _rec(raw="B", rating="unknown", price=19.6, evidence="友達(2409 TT，B，19.6)")
        rec.target_price_exact = Decimal("19.6000")
        params = rs._update_params(rs.plan_changes([rec])[0])
        self.assertIsInstance(params["old_target_price"], Decimal)
        self.assertEqual(params["old_target_price"], Decimal("19.6000"))

    def test_ten_times_error_cleared_only_on_wrong_row(self):
        rows = [
            _rec("a", day=date(2026, 9, 9), price=720, evidence="720"),
            _rec("b", day=date(2026, 9, 10), price=72, evidence="72"),
            _rec("c", day=date(2026, 9, 11), price=720, evidence="720"),
        ]
        changes = rs.plan_changes(rows)
        self.assertEqual([c.record.id for c in changes], ["b"])
        self.assertEqual(changes[0].target_note, TARGET_NOTE_IMPLAUSIBLE)

    def test_current_price_not_used_as_neighbor(self):
        """現價不能當鄰近比對的基準：這兩筆現價中位數 300，會把真正的目標價 1,000 判成離譜。"""
        rows = [
            _rec("a", day=date(2026, 9, 9), price=300, evidence="X (2308 TT，NT$300，增加持股)"),
            _rec("b", day=date(2026, 9, 10), price=300, evidence="X (2308 TT，NT$300，增加持股)"),
            _rec("c", day=date(2026, 9, 11), price=1000, evidence="目標價 1,000 元"),
        ]
        notes = {c.record.id: c.target_note for c in rs.plan_changes(rows)}
        self.assertEqual(notes, {"a": TARGET_NOTE_INLINE_PRICE, "b": TARGET_NOTE_INLINE_PRICE})

    def test_neighbors_outside_window_ignored(self):
        rows = [
            _rec("a", day=date(2025, 1, 1), price=100, evidence="100"),
            _rec("b", day=date(2025, 1, 2), price=100, evidence="100"),
            _rec("c", day=date(2026, 9, 11), price=1000, evidence="1000"),
        ]
        self.assertEqual(rs.plan_changes(rows), [])

    def test_rejected_rows_skipped(self):
        self.assertEqual(rs.plan_changes([_rec(raw="OW", rating="unknown", status="rejected")]), [])

    def test_idempotent_after_repair(self):
        """修過的列（目標價已清、評等已對）不會再命中。"""
        rows = [_rec(raw="OW", rating="overweight", price=None, evidence=None, status="partial",
                     error=TARGET_NOTE_INLINE_PRICE)]
        self.assertEqual(rs.plan_changes(rows), [])


class SafetyTests(unittest.TestCase):
    def _run(self, apply=False, confirm=None, out=None):
        args = SimpleNamespace(apply=apply, confirm=confirm, out=out)
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            rc = asyncio.run(rs.main(args))
        return rc, stdout.getvalue(), stderr.getvalue()

    def test_apply_requires_exact_confirm(self):
        with mock.patch.object(rs, "load_records", new=mock.AsyncMock(side_effect=AssertionError("不該讀 DB"))):
            rc, _, err = self._run(apply=True, confirm="wrong:1/x", out="x.csv")
        self.assertEqual(rc, 2)
        self.assertIn(rs.db_target(), err)

    def test_apply_requires_out(self):
        with mock.patch.object(rs, "load_records", new=mock.AsyncMock(side_effect=AssertionError("不該讀 DB"))):
            rc, _, err = self._run(apply=True, confirm=rs.db_target())
        self.assertEqual(rc, 2)
        self.assertIn("--out", err)

    def test_dry_run_never_writes(self):
        rows = [_rec(price=1355, evidence="台達電 (2308 TT，NT$1,355，增加持股)")]
        with (
            mock.patch.object(rs, "load_records", new=mock.AsyncMock(return_value=rows)),
            mock.patch.object(rs, "apply_changes", new=mock.AsyncMock(side_effect=AssertionError("試跑不該寫"))),
        ):
            rc, out, _ = self._run()
        self.assertEqual(rc, 0)
        self.assertIn("要改 1 列", out)

    def test_csv_written_before_update_keeps_old_values(self):
        rows = [_rec(price=1355, evidence="台達電 (2308 TT，NT$1,355，增加持股)")]
        seen = {}

        async def fake_apply(changes):
            seen["exists"] = path.exists()  # 寫 DB 之前備份要已經落地
            return len(changes), 0

        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "before.csv"
            with (
                mock.patch.object(rs, "load_records", new=mock.AsyncMock(return_value=rows)),
                mock.patch.object(rs, "apply_changes", new=fake_apply),
            ):
                rc, out, _ = self._run(apply=True, confirm=rs.db_target(), out=str(path))
            self.assertEqual(rc, 0)
            self.assertTrue(seen["exists"])
            self.assertIn("已更新 1 列", out)
            self.assertIn("略過 0 列", out)
            row = next(csv.DictReader(open(path, encoding="utf-8")))
        self.assertEqual(row["old_target_price"], "1355")
        self.assertEqual(row["old_target_currency"], "TWD")
        self.assertEqual(row["target_note"], TARGET_NOTE_INLINE_PRICE)

    def test_db_target_hides_credentials(self):
        with mock.patch.object(rs, "DATABASE_URL", "postgresql+asyncpg://u:secret@db.example:5433/research"):
            self.assertEqual(rs.db_target(), "db.example:5433/research")


if __name__ == "__main__":
    unittest.main()
