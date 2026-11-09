"""scripts/estimate_backfill.py：固定輸入 → 固定輸出的估價、費率檔的嚴格驗證、抽取快取的語料來源。

不連 DB、不呼叫 LLM；快取、標註、側檔、費率檔、用量檔都寫在 tempfile。
"""
from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services import backfill_tiers as bt  # noqa: E402
from scripts import estimate_backfill as eb  # noqa: E402

TODAY = date(2026, 11, 10)  # 第二層＝report_date ≥ 2025-11-10


def _items():
    return [
        eb.Item("A", date(2026, 1, 1), frozenset({"tag", "summary"}), "研" * 100),  # 第二層；內文 80 token
        eb.Item("B", None, frozenset({"title"}), "a" * 35),  # 無日期 → 第三層；內文 10 token
        eb.Item("C", date(2025, 11, 9), frozenset({"summary", "signal"}), ""),  # 窗期前一天 → 第三層
    ]


RATES = eb.Rates(currency="USD", per_tokens=1000, input=1.0, output=2.0)


class EstimateTests(unittest.TestCase):
    def test_fixed_input_fixed_output(self):
        est = eb.estimate(_items(), today=TODAY, months=12, daily_limit=3350, rates=RATES)
        got = eb.to_json(est, source="cache")
        self.assertEqual(got["recent_cutoff"], "2025-11-10")
        self.assertEqual(got["reports"], 3)
        tiers = got["tiers"]
        # 第一層：標註 1 次；輸入 900＋80，輸出 250；金額 (980×1＋250×2)/1000；時間 20 秒 ÷ 1。
        self.assertEqual(tiers["1"]["tasks"]["tag"],
                         {"calls": 1, "input_tokens": 980, "output_tokens": 250, "hours": 0.01, "cost": 1.48})
        # 第二層：只有 A 的摘要。
        self.assertEqual(tiers["2"]["tasks"]["summary"],
                         {"calls": 1, "input_tokens": 240, "output_tokens": 300, "hours": 0.0, "cost": 0.84})
        self.assertEqual(tiers["2"]["tasks"]["title"]["calls"], 0)
        # 第三層：C 的摘要與訊號、B 的標題。
        self.assertEqual(tiers["3"]["tasks"]["summary"],
                         {"calls": 1, "input_tokens": 160, "output_tokens": 300, "hours": 0.0, "cost": 0.76})
        self.assertEqual(tiers["3"]["tasks"]["title"],
                         {"calls": 1, "input_tokens": 470, "output_tokens": 120, "hours": 0.0, "cost": 0.71})
        self.assertEqual(tiers["3"]["tasks"]["signal"],
                         {"calls": 1, "input_tokens": 800, "output_tokens": 1500, "hours": 0.01, "cost": 3.8})
        self.assertEqual(tiers["3"]["total"],
                         {"calls": 3, "input_tokens": 1430, "output_tokens": 1920, "hours": 0.01, "cost": 5.27})
        self.assertEqual(got["background"], {"daily_token_limit": 3350, "days": 1.0})
        self.assertEqual(got["currency"], "USD")

    def test_without_rates_there_is_no_cost(self):
        est = eb.estimate(_items(), today=TODAY, months=12, daily_limit=1000)
        got = eb.to_json(est, source="db")
        self.assertIsNone(got["tiers"]["3"]["total"]["cost"])
        self.assertIn("未提供費率", eb.render(est, source="db"))

    def test_workers_divide_time(self):
        items = [eb.Item(f"h{i}", date(2026, 1, 1), frozenset({"takeaway"}), "") for i in range(8)]
        est = eb.estimate(items, today=TODAY, months=12, daily_limit=1, workers=4)
        self.assertAlmostEqual(est.tiers[2]["takeaway"].seconds, 8 * 45.0 / 4)

    def test_recent_months_moves_the_boundary(self):
        est = eb.estimate(_items(), today=TODAY, months=1, daily_limit=1)
        self.assertEqual(est.tiers[2]["summary"].calls, 0)
        self.assertEqual(est.tiers[3]["summary"].calls, 2)

    def test_usage_log_calibrates(self):
        items = [eb.Item(f"h{i}", date(2026, 1, 1), frozenset({"title"}), "") for i in range(20)]
        rows = [{"task": "title", "file_hash": f"h{i}", "kind": None, "total_ms": 3000,
                 "tokens": {"hit": 0, "miss": 920, "completion": 50}} for i in range(20)]
        est = eb.estimate(items, today=TODAY, months=12, daily_limit=1, usage_rows=rows)
        cell = est.tiers[2]["title"]
        self.assertAlmostEqual(cell.input_tokens, 20 * 920)  # 倍率 920 ÷ 460
        self.assertAlmostEqual(cell.output_tokens, 20 * 50)
        self.assertAlmostEqual(cell.seconds, 20 * 3.0 / 2)
        cal = eb.to_json(est, source="db")["calibration"]["title"]
        self.assertEqual((cal["input_factor"], cal["matched_reports"]), (2.0, 20))
        self.assertIn("輸入×2.00（20 份比對）", eb.render(est, source="db"))

    def test_per_task_rate_override(self):
        rates = eb.Rates(currency="USD", per_tokens=1000, input=1.0, output=2.0, tasks={"signal": (0.0, 0.0)})
        est = eb.estimate(_items(), today=TODAY, months=12, daily_limit=1, rates=rates)
        self.assertEqual(est.tiers[3]["signal"].cost, 0.0)


class RatesTests(unittest.TestCase):
    def test_parse(self):
        rates = eb.parse_rates({"currency": "TWD", "input": 40, "output": 320.5, "per_tokens": 1000000,
                                "tasks": {"signal": {"output": 1}}})
        self.assertEqual((rates.currency, rates.input, rates.output, rates.tasks), ("TWD", 40.0, 320.5,
                                                                                    {"signal": (40.0, 1.0)}))
        self.assertAlmostEqual(rates.cost("signal", 1_000_000, 1_000_000), 41.0)

    def test_rejects_bad_files(self):
        bad = [
            {"currency": "USD", "input": 1},  # 缺 output
            {"currency": "USD", "input": 1, "output": 1, "inputs": 2},  # 拼錯
            {"currency": "USD", "input": -1, "output": 1},
            {"currency": "USD", "input": True, "output": 1},
            {"currency": "", "input": 1, "output": 1},
            {"currency": "USD", "input": 1, "output": 1, "per_tokens": 0},
            {"currency": "USD", "input": 1, "output": 1, "tasks": {"brief": {"input": 1}}},
            {"currency": "USD", "input": 1, "output": 1, "tasks": {"tag": {"cached": 1}}},
        ]
        for data in bad:
            with self.subTest(data=data), self.assertRaises(eb.EstimateError):
                eb.parse_rates(data)

    def test_load_toml(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rates.toml"
            path.write_text('currency = "USD"\ninput = 1.5\noutput = 6\n[tasks.tag]\ninput = 0.5\n', encoding="utf-8")
            rates = eb.load_rates(path)
            self.assertEqual(rates.tasks, {"tag": (0.5, 6.0)})
            path.write_text("currency = ", encoding="utf-8")
            with self.assertRaises(eb.EstimateError):
                eb.load_rates(path)

    def test_no_vendor_prices_in_the_code(self):
        """費率只能由使用者提供：程式裡沒有任何預設價格。"""
        self.assertEqual((eb.Rates().input, eb.Rates().output), (0.0, 0.0))
        ap = eb.build_parser()
        args = ap.parse_args(["--source", "db"])
        self.assertIsNone(eb.resolve_rates(args))
        args = ap.parse_args(["--source", "db", "--input-rate", "1", "--output-rate", "2"])
        with self.assertRaises(eb.EstimateError):
            eb.resolve_rates(args)  # 參數給費率要一起給幣別


def _write_record(cache_dir: Path, file_hash: str, **kw):
    rec = {"version": 1, "file_hash": file_hash, "file_name": f"{file_hash}.pdf", "text": "研" * 10,
           "report_date": "2026-06-01", "is_admin": False, "scanned": False, "error": None}
    rec.update(kw)
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / f"{file_hash}.json").write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")


class CacheSourceTests(unittest.TestCase):
    def test_items_from_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache_dir, tags_dir, docs = root / "extracted", root / "tags", root / "docs"
            docs.mkdir()
            tags_dir.mkdir()
            _write_record(cache_dir, "plain")  # 沒標註、沒側檔：全部都要
            _write_record(cache_dir, "admin", is_admin=True)  # 不入庫
            _write_record(cache_dir, "scan", scanned=True)
            _write_record(cache_dir, "broken", error="壞檔")
            _write_record(cache_dir, "nondate", report_date=None)  # 沒日期：訊號不擷取
            _write_record(cache_dir, "tagged")
            (tags_dir / "tagged.json").write_text('{"market": "TW", "is_research": true, "stock_targets": []}')
            _write_record(cache_dir, "nonres")
            (tags_dir / "nonres.json").write_text('{"market": null, "is_research": false}')
            (docs / "side.pdf").write_bytes(b"%PDF")
            (docs / "side.json").write_text(json.dumps({"market": "TW", "stock_targets": ["2330"], "title": "標題",
                                                        "report_date": "2020-01-02"}), encoding="utf-8")
            _write_record(cache_dir, "side", file_path=str(docs / "side.pdf"))
            (docs / "badside.pdf").write_bytes(b"%PDF")
            (docs / "badside.json").write_text('{"market": "XX"}', encoding="utf-8")
            _write_record(cache_dir, "badside", file_path=str(docs / "badside.pdf"))
            (cache_dir / "garbage.json").write_text("{", encoding="utf-8")
            got = {i.file_hash: i for i in eb.items_from_cache(cache_dir, tags_dir)}
        derived = set(bt.DERIVED_TASKS)
        self.assertEqual(set(got), {"plain", "nondate", "tagged", "side", "badside"})
        self.assertEqual(got["plain"].tasks, frozenset({"tag", *derived}))
        self.assertEqual(got["plain"].report_date, date(2026, 6, 1))
        self.assertEqual(got["nondate"].tasks, frozenset({"tag", "summary", "title", "takeaway"}))
        self.assertIsNone(got["nondate"].report_date)
        # 有標註快取：不再標註；標的是空陣列 → 不擷取訊號。
        self.assertEqual(got["tagged"].tasks, frozenset({"summary", "title", "takeaway"}))
        # 側檔給了 market＋stock_targets＋title：不標註、不產標題；日期以側檔為準。
        self.assertEqual(got["side"].tasks, frozenset({"summary", "takeaway", "signal"}))
        self.assertEqual(got["side"].report_date, date(2020, 1, 2))
        # 側檔不合格：照最壞情況算（全部都要）。
        self.assertEqual(got["badside"].tasks, frozenset({"tag", *derived}))

    def test_main_json_is_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_record(root / "extracted", "h1", text="研" * 100, report_date="2026-01-01")
            _write_record(root / "extracted", "h2", text="研" * 100, report_date="2020-01-01")
            rates = root / "rates.toml"
            rates.write_text('currency = "USD"\ninput = 1\noutput = 2\nper_tokens = 1000\n', encoding="utf-8")
            argv = ["--source", "cache", "--cache-dir", str(root / "extracted"), "--tags-dir", str(root / "tags"),
                    "--rates", str(rates), "--today", "2026-11-10", "--no-calibrate", "--json"]
            outs = []
            for _ in range(2):
                with redirect_stdout(io.StringIO()) as buf:
                    self.assertEqual(eb.main(argv), 0)
                outs.append(buf.getvalue())
        self.assertEqual(outs[0], outs[1])
        got = json.loads(outs[0])
        self.assertEqual(got["tiers"]["1"]["tasks"]["tag"]["calls"], 2)
        self.assertEqual(got["tiers"]["2"]["tasks"]["summary"]["calls"], 1)
        self.assertEqual(got["tiers"]["3"]["tasks"]["summary"]["calls"], 1)
        self.assertEqual(got["background"]["daily_token_limit"], 2_000_000)  # 廷豐 profile 的預設

    def test_main_rejects_bad_rates(self):
        with redirect_stderr(io.StringIO()) as err:
            rc = eb.main(["--source", "cache", "--input-rate", "-1", "--output-rate", "1", "--currency", "USD"])
        self.assertEqual(rc, 1)
        self.assertIn("非負數", err.getvalue())

    def test_human_table(self):
        est = eb.estimate(_items(), today=TODAY, months=12, daily_limit=3350, rates=RATES)
        text = eb.render(est, source="cache")
        self.assertIn("第二層＝report_date ≥ 2025-11-10", text)
        self.assertIn("金額(USD)", text)
        self.assertIn("約需 1.0 天", text)
        self.assertIn("訊號的呼叫數是上限", text)


if __name__ == "__main__":
    unittest.main()
