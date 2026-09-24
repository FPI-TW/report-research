# tests/test_signal_extract.py
import json
import sys
import unittest
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services.signal_extract import (  # noqa: E402
    EXTRACTION_VERSION,
    STANCE_CONSTRUCTIVENESS,
    THESIS_DIMENSIONS,
    ReportContext,
    build_rows,
    eps_comparable,
    normalize_currency,
    normalize_rating,
    parse_signal,
)


def _ctx(codes, report_id="r1", market="TW", broker="券商甲"):
    return ReportContext(
        report_id=report_id,
        market=market,
        broker=broker,
        report_date=date(2026, 7, 11),
        requested_codes=list(codes),
    )


def _full_signal(code="2330"):
    return {
        "instrument_code": code,
        "rating": {"raw": "買進", "evidence": "維持買進評等，目標價上調"},
        "target_price": {
            "value": 1220,
            "currency": "NT$",
            "horizon": "12M",
            "evidence": "目標價由 1120 元調升至 1220 元",
        },
        "eps_estimates": [
            {
                "fiscal_year": 2026,
                "period": "FY",
                "currency": "NT$",
                "value": 66.4,
                "unit": "per_share",
                "evidence": "預估 2026 年 EPS 66.4 元",
            }
        ],
        "thesis": {
            "outlook": {"stance": "positive", "summary": "需求改善", "evidence": "能見度改善"},
            "catalyst": {"stance": "positive", "summary": "封裝供給", "evidence": "供給趨緩"},
            "risk": {"stance": "rising", "summary": "匯率風險", "evidence": "匯率壓抑毛利"},
            "valuation": {"stance": "fair", "summary": "估值合理", "evidence": "本益比維持"},
        },
    }


def _raw(*signals):
    return json.dumps({"signals": list(signals)}, ensure_ascii=False)


class RatingMapTests(unittest.TestCase):
    def test_buy_family(self):
        for raw in ("買進", "買入", "加碼", "buy", "Strong Buy", "add"):
            self.assertEqual(normalize_rating(raw), "buy", raw)

    def test_overweight_family(self):
        for raw in ("增持", "優於大盤", "outperform", "Accumulate", "區間偏多",
                    "增加持股", "維持增加持股評等", "逢低承接"):  # 實測 KGI 用語
            self.assertEqual(normalize_rating(raw), "overweight", raw)

    def test_neutral_family(self):
        for raw in ("中立", "持有", "區間操作", "hold", "Neutral", "符合大盤"):
            self.assertEqual(normalize_rating(raw), "neutral", raw)

    def test_underweight_family(self):
        for raw in ("減碼", "減持", "劣於大盤", "reduce", "Underperform"):
            self.assertEqual(normalize_rating(raw), "underweight", raw)

    def test_sell_family(self):
        for raw in ("賣出", "sell", "Strong Sell"):
            self.assertEqual(normalize_rating(raw), "sell", raw)

    def test_full_sentence_keyword(self):
        # 整句：子字串命中「買進」
        self.assertEqual(normalize_rating("本次調升評等至買進"), "buy")
        # 「賣出」須先於「賣」命中
        self.assertEqual(normalize_rating("下調至賣出"), "sell")

    def test_case_and_whitespace(self):
        self.assertEqual(normalize_rating("  BUY  "), "buy")
        self.assertEqual(normalize_rating("OutPerform"), "overweight")

    def test_unknown(self):
        for raw in ("未評等", "無評等", "", None, 123, "隨便亂打的字"):
            self.assertEqual(normalize_rating(raw), "unknown", raw)


class CurrencyTests(unittest.TestCase):
    def test_twd_synonyms(self):
        for raw in ("NT$", "台幣", "新台幣", "TWD", "twd"):
            self.assertEqual(normalize_currency(raw), "TWD", raw)

    def test_usd_synonyms(self):
        for raw in ("US$", "美元", "USD"):
            self.assertEqual(normalize_currency(raw), "USD", raw)

    def test_other_currencies(self):
        self.assertEqual(normalize_currency("港元"), "HKD")
        self.assertEqual(normalize_currency("人民幣"), "CNY")
        self.assertEqual(normalize_currency("RMB"), "CNY")

    def test_never_converts(self):
        # 給 USD 絕不會變成 TWD（只正規化、不換算）
        self.assertEqual(normalize_currency("USD"), "USD")
        self.assertNotEqual(normalize_currency("美元"), "TWD")

    def test_unknown_passthrough(self):
        self.assertEqual(normalize_currency("gbp"), "GBP")  # 未知代碼原樣大寫
        self.assertIsNone(normalize_currency(""))
        self.assertIsNone(normalize_currency(None))


class EpsComparabilityTests(unittest.TestCase):
    def _e(self, fy=2026, period="FY", currency="TWD", unit="per_share"):
        return {"fiscal_year": fy, "period": period, "currency": currency, "unit": unit}

    def test_all_same_comparable(self):
        self.assertTrue(eps_comparable(self._e(), self._e()))

    def test_different_fy_not_comparable(self):
        self.assertFalse(eps_comparable(self._e(fy=2025), self._e(fy=2026)))

    def test_different_currency_not_comparable(self):
        self.assertFalse(eps_comparable(self._e(currency="TWD"), self._e(currency="USD")))

    def test_different_period_not_comparable(self):
        self.assertFalse(eps_comparable(self._e(period="FY"), self._e(period="1H")))

    def test_different_unit_not_comparable(self):
        self.assertFalse(eps_comparable(self._e(unit="per_share"), self._e(unit="total")))


class StanceContractTests(unittest.TestCase):
    """thesis stance 詞彙契約：四維齊全、各維度序位 +1/0/−1 完整。"""

    def test_four_dimensions(self):
        self.assertEqual(set(THESIS_DIMENSIONS), set(STANCE_CONSTRUCTIVENESS.keys()))

    def test_each_dimension_has_full_scale(self):
        for dim, m in STANCE_CONSTRUCTIVENESS.items():
            self.assertEqual(sorted(m.values()), [-1, 0, 1], dim)


class BuildRowsValidTests(unittest.TestCase):
    def test_full_signal_is_valid(self):
        parsed = parse_signal(_raw(_full_signal()), ["2330"])
        rows = build_rows(_ctx(["2330"]), parsed)
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r.instrument_code, "2330")
        self.assertEqual(r.extraction_status, "valid")
        self.assertIsNone(r.error_detail)
        self.assertEqual(r.rating_raw, "買進")
        self.assertEqual(r.rating_normalized, "buy")
        self.assertEqual(r.target_price, 1220.0)
        self.assertEqual(r.target_currency, "TWD")  # NT$ → TWD
        self.assertEqual(len(r.eps_estimates), 1)
        self.assertEqual(r.eps_estimates[0]["currency"], "TWD")
        self.assertEqual(set(r.thesis_dimensions.keys()), set(THESIS_DIMENSIONS))
        self.assertEqual(r.extraction_version, EXTRACTION_VERSION)
        self.assertEqual(r.broker, "券商甲")
        self.assertEqual(r.report_date, date(2026, 7, 11))
        # 原始 payload 保留供追溯
        self.assertEqual(r.raw_payload["instrument_code"], "2330")


class BuildRowsPartialTests(unittest.TestCase):
    def test_target_missing_currency_downgrades_partial(self):
        sig = _full_signal()
        sig["target_price"] = {"value": 1220, "evidence": "目標價 1220"}  # 無 currency
        rows = build_rows(_ctx(["2330"]), parse_signal(_raw(sig), ["2330"]))
        r = rows[0]
        self.assertEqual(r.extraction_status, "partial")
        self.assertEqual(r.target_price, 1220.0)  # 價仍保留
        self.assertIsNone(r.target_currency)
        self.assertIn("缺幣別", r.error_detail)

    def test_eps_missing_field_dropped(self):
        sig = _full_signal()
        sig["eps_estimates"] = [
            {"fiscal_year": 2026, "period": "FY", "value": 66.4},  # 缺 currency/unit
        ]
        rows = build_rows(_ctx(["2330"]), parse_signal(_raw(sig), ["2330"]))
        r = rows[0]
        self.assertEqual(r.eps_estimates, [])  # 缺欄整筆丟棄
        self.assertEqual(r.extraction_status, "partial")
        self.assertIn("eps", r.error_detail)

    def test_requested_but_not_returned_is_partial_empty(self):
        # requested 兩檔，payload 只回一檔 → 另一檔 partial 空列（非 rejected）
        parsed = parse_signal(_raw(_full_signal("2330")), ["2330", "2317"])
        rows = build_rows(_ctx(["2330", "2317"]), parsed)
        by_code = {r.instrument_code: r for r in rows}
        self.assertEqual(by_code["2330"].extraction_status, "valid")
        self.assertEqual(by_code["2317"].extraction_status, "partial")
        self.assertEqual(by_code["2317"].rating_normalized, "unknown")
        self.assertIn("無可擷取內容", by_code["2317"].error_detail)


class ThesisEvidenceTests(unittest.TestCase):
    def test_dimension_without_evidence_dropped(self):
        sig = _full_signal()
        sig["thesis"]["risk"] = {"stance": "rising", "summary": "匯率"}  # 缺 evidence
        rows = build_rows(_ctx(["2330"]), parse_signal(_raw(sig), ["2330"]))
        r = rows[0]
        self.assertNotIn("risk", r.thesis_dimensions)  # 未取得證據 → 該維留空
        self.assertIn("risk", r.error_detail)  # error_detail 記錄「thesis:risk 缺 evidence」
        self.assertEqual(r.extraction_status, "partial")

    def test_invalid_stance_dropped(self):
        sig = _full_signal()
        sig["thesis"]["valuation"] = {"stance": "positive", "evidence": "x"}  # valuation 詞表外
        rows = build_rows(_ctx(["2330"]), parse_signal(_raw(sig), ["2330"]))
        r = rows[0]
        self.assertNotIn("valuation", r.thesis_dimensions)
        self.assertIn("valuation", r.error_detail)

    def test_valid_stance_kept(self):
        sig = _full_signal()
        rows = build_rows(_ctx(["2330"]), parse_signal(_raw(sig), ["2330"]))
        thesis = rows[0].thesis_dimensions
        self.assertEqual(thesis["risk"]["stance"], "rising")
        self.assertEqual(thesis["valuation"]["stance"], "fair")


class ParseRobustnessTests(unittest.TestCase):
    def test_strips_code_fence(self):
        raw = "```json\n" + _raw(_full_signal()) + "\n```"
        parsed = parse_signal(raw, ["2330"])
        self.assertTrue(parsed.ok)
        self.assertIn("2330", parsed.signals)

    def test_extracts_json_from_noise(self):
        raw = "以下是結果：\n" + _raw(_full_signal()) + "\n（以上）"
        parsed = parse_signal(raw, ["2330"])
        self.assertTrue(parsed.ok)
        self.assertIn("2330", parsed.signals)

    def test_ignores_codes_outside_requested(self):
        parsed = parse_signal(_raw(_full_signal("2330"), _full_signal("9999")), ["2330"])
        self.assertIn("2330", parsed.signals)
        self.assertNotIn("9999", parsed.signals)  # 不在 requested → 忽略

    def test_never_raises_on_garbage(self):
        for raw in ("", "   ", "這不是 JSON", "{壞掉的 json,,,}", "null", "[]"):
            parsed = parse_signal(raw, ["2330"])  # 不可 raise
            self.assertIsInstance(parsed.ok, bool)


class RejectedTests(unittest.TestCase):
    def test_unparseable_all_rejected(self):
        parsed = parse_signal("這不是 JSON", ["2330", "2317"])
        self.assertFalse(parsed.ok)
        rows = build_rows(_ctx(["2330", "2317"]), parsed)
        self.assertEqual(len(rows), 2)
        for r in rows:
            self.assertEqual(r.extraction_status, "rejected")
            self.assertEqual(r.rating_normalized, "unknown")
            self.assertEqual(r.eps_estimates, [])
            self.assertEqual(r.thesis_dimensions, {})
            self.assertIn("raw_text", r.raw_payload)  # 原文保留供追溯
            self.assertIsNotNone(r.error_detail)

    def test_bad_json_rejected(self):
        parsed = parse_signal("{signals: [oops}", ["2330"])
        self.assertFalse(parsed.ok)
        rows = build_rows(_ctx(["2330"]), parsed)
        self.assertEqual(rows[0].extraction_status, "rejected")



class SimplifiedLookupTests(unittest.TestCase):
    """評等／幣別查表前先轉繁（遷移 PR-15），但存進 DB 的原值與逐字證據一律不轉。"""

    def test_simplified_ratings_classified(self):
        self.assertEqual(normalize_rating("买入"), "buy")
        self.assertEqual(normalize_rating("卖出"), "sell")
        self.assertEqual(normalize_rating("减持"), "underweight")
        self.assertEqual(normalize_rating("调升评等至买入"), "buy")  # 子字串那一層也吃轉過的鍵
        self.assertEqual(normalize_rating("区间操作"), "neutral")

    def test_traditional_ratings_unchanged(self):
        self.assertEqual(normalize_rating("區間操作"), "neutral")
        self.assertEqual(normalize_rating("優於大盤"), "overweight")

    def test_simplified_currency(self):
        self.assertEqual(normalize_currency("人民币"), "CNY")
        self.assertEqual(normalize_currency("港币"), "HKD")

    def test_unknown_currency_returns_original_not_converted(self):
        """查不到時回原值大寫：轉過的鍵只用來查表，不外流。"""
        self.assertEqual(normalize_currency("越南盾币"), "越南盾币")

    def test_row_keeps_raw_values_and_evidence(self):
        sig = _full_signal()
        sig["rating"] = {"raw": "买入", "evidence": "维持买入评级"}
        sig["thesis"]["outlook"] = {"stance": "positive", "summary": "需求改善", "evidence": "订单能见度改善"}
        r = build_rows(_ctx(["2330"]), parse_signal(_raw(sig), ["2330"]))[0]
        self.assertEqual(r.rating_normalized, "buy")
        self.assertEqual(r.rating_raw, "买入")  # 存的是原值
        self.assertEqual(r.thesis_dimensions["outlook"]["evidence"], "订单能见度改善")  # 逐字證據不轉
        self.assertEqual(r.raw_payload["rating"]["raw"], "买入")


class RawPayloadModelTests(unittest.TestCase):
    """raw_payload.model 記產出模型（遷移 PR-15）。"""

    def test_model_recorded_on_every_row(self):
        parsed = parse_signal(_raw(_full_signal("2330")), ["2330", "2317"])
        rows = build_rows(_ctx(["2330", "2317"]), parsed, model="deepseek-flash")
        self.assertEqual([r.raw_payload["model"] for r in rows], ["deepseek-flash", "deepseek-flash"])
        self.assertEqual(rows[0].raw_payload["instrument_code"], "2330")  # 原始物件仍在
        self.assertNotIn("model", parsed.signals["2330"])  # 不改動 parse 結果

    def test_rejected_row_records_model(self):
        parsed = parse_signal("不是 JSON", ["2330"])
        r = build_rows(_ctx(["2330"]), parsed, model="deepseek-flash")[0]
        self.assertEqual(r.extraction_status, "rejected")
        self.assertEqual(r.raw_payload["model"], "deepseek-flash")
        self.assertIn("raw_text", r.raw_payload)

    def test_no_model_no_key(self):
        r = build_rows(_ctx(["2330"]), parse_signal(_raw(_full_signal()), ["2330"]))[0]
        self.assertNotIn("model", r.raw_payload)


if __name__ == "__main__":
    unittest.main()
