"""scripts/eval_extraction.py 的純函式測試（不碰 DB、不開 PDF）。

釘住三件會靜默出錯的事：
1. 順序指標對「欄位交錯」敏感（同流配對正確率會掉），對空白差異不敏感。
2. coverage 與 accuracy 分開算——亂猜只會拉高前者。
3. 幻覺率把 prefix 錨定算成錨不回（§4.3 的 blocker）。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "eval_extraction.py"
DATASET = ROOT / "eval" / "extraction_dataset.json"


def _load():
    spec = importlib.util.spec_from_file_location("eval_extraction", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["eval_extraction"] = mod
    spec.loader.exec_module(mod)
    return mod


ev = _load()


def _items(*pairs):
    return [{"stream": s, "text": t} for s, t in pairs]


def test_score_order_perfect_and_whitespace_insensitive():
    text = "增加持股‧維持\n12個月目標價 (NT$) 430.0\n\n1Q26 EPS 優 於 預期。"
    items = _items(("side", "增加持股‧維持"), ("side", "12個月目標價(NT$)430.0"), ("main", "1Q26 EPS優於預期。"))
    r = ev.score_order(text, items)
    assert r.n_hit == 3 and r.misses == []
    assert r.pair_acc == 1.0 and r.pair_acc_within == 1.0 and r.kendall_tau == 1.0


def test_score_order_interleaved_columns_lowers_within_stream():
    # 左欄兩行與主文兩行被交錯輸出：跨欄配對依慣例算錯，但同欄內部順序仍對
    text = "側欄甲 主文一 側欄乙 主文二"
    items = _items(("side", "側欄甲"), ("side", "側欄乙"), ("main", "主文一"), ("main", "主文二"))
    r = ev.score_order(text, items)
    assert r.n_pairs == 6 and r.n_pairs_within == 2
    assert r.pair_acc_within == 1.0
    assert r.pair_acc < 1.0
    # 同欄自己顛倒才會扣同流分
    r2 = ev.score_order("側欄乙 側欄甲 主文一 主文二", items)
    assert r2.pair_acc_within == 0.5


def test_score_order_miss_is_reported_not_scored():
    r = ev.score_order("只有這一句", _items(("main", "只有這一句"), ("main", "不存在的句子")))
    assert r.n_hit == 1 and r.misses == ["不存在的句子"]
    assert r.n_pairs == 0 and r.pair_acc is None


def test_score_fields_coverage_vs_accuracy(monkeypatch):
    golden = [
        {"code": "2382", "rating": "增加持股", "target_price": {"value": 430.0, "currency": "TWD"}},
        {"code": "2313", "rating": "增加持股", "target_price": {"value": 80.0, "currency": "TWD"}},
        {"code": "6271", "rating": "中立", "target_price": None},
    ]
    signals = [
        {"instrument_code": "2382", "rating_raw": "增加持股", "target_price": 430.0, "target_price_evidence": None},
        {"instrument_code": "2313", "rating_raw": "買進", "target_price": 86.0, "target_price_evidence": None},
    ]
    fr = ev.score_fields(golden, signals, "")
    assert (fr.golden_rating, fr.got_rating, fr.ok_rating) == (3, 2, 1)
    assert (fr.golden_tp, fr.got_tp, fr.ok_tp) == (2, 2, 1)


def test_score_fields_tp_tolerance_and_rating_normalisation():
    golden = [{"code": "1", "rating": "Hold", "target_price": {"value": 1000.0}}]
    signals = [{"instrument_code": "1", "rating_raw": "HOLD ", "target_price": 1004.0}]
    fr = ev.score_fields(golden, signals, "")
    assert fr.ok_rating == 1 and fr.ok_tp == 1
    signals[0]["target_price"] = 1010.0
    assert ev.score_fields(golden, signals, "").ok_tp == 0


def test_hallucination_counts_prefix_and_missing(monkeypatch):
    class A:
        def __init__(self, method):
            self.method = method

    def fake_locate(text, quote):
        return {"exact": A("exact"), "prefix": A("prefix"), "missing": None}[quote]

    monkeypatch.setattr(ev, "locate_quote", fake_locate)
    signals = [
        {"instrument_code": "a", "target_price_evidence": "exact"},
        {"instrument_code": "b", "target_price_evidence": "prefix"},
        {"instrument_code": "c", "target_price_evidence": "missing"},
        {"instrument_code": "d", "target_price_evidence": None},
    ]
    fr = ev.score_fields([], signals, "whatever")
    assert fr.n_evidence == 3 and fr.n_unanchored == 2 and fr.n_prefix == 1


def test_aggregate_is_pair_weighted_not_case_mean():
    def case(n_pairs, n_conc, n_pw, n_cw):
        return {
            "order": {
                "n_sentences": 5, "n_hit": 5, "n_pairs": n_pairs, "n_concordant": n_conc,
                "n_pairs_within": n_pw, "n_concordant_within": n_cw, "pair_acc": n_conc / n_pairs,
            },
            "fields": {k: 0 for k in ("golden_rating", "got_rating", "ok_rating", "golden_tp", "got_tp", "ok_tp",
                                      "n_evidence", "n_unanchored", "n_prefix")},
            "chars_nows": 10,
        }

    s = ev.aggregate([case(10, 10, 4, 4), case(90, 0, 40, 0)])
    assert s["order_pair_acc"] == 0.1
    assert s["order_pair_acc_case_mean"] == 0.5
    assert s["order_pair_acc_within"] == 4 / 44
    assert s["tp_coverage"] is None and s["hallucination_rate"] is None


def test_dataset_shape_and_files_exist():
    doc = json.loads(DATASET.read_text(encoding="utf-8"))
    assert doc["version"].startswith("extraction-golden-")
    ids = [c["id"] for c in doc["cases"]]
    assert len(ids) == len(set(ids)), "case id 重複"
    for c in doc["cases"]:
        assert c["annotation_status"] in ev.STATUSES, c["id"]
        assert len(c["file_hash"]) == 64
        assert isinstance(c["two_column"], bool)
        for o in c["order"]:
            assert o["text"].strip(), c["id"]
        for inst in c["fields"]["instruments"]:
            assert inst["code"], c["id"]
            tp = inst.get("target_price")
            assert tp is None or ("value" in tp and "currency" in tp), c["id"]
        if c["annotation_status"] != "prefilled":
            assert len(c["order"]) >= 8, f"{c['id']} 順序層少於 8 條"


def test_score_order_ignores_markdown_table_pipes():
    # 版面抽取器把表格序列化成 markdown；儲存格分隔符不是版面順序資訊，不能扣分
    text = "| 發布日 | 報告 | 評等 |\n| 2/26 | 6690安碁資訊 | 買進 |\n\n台股盤勢分析"
    items = _items(("side", "發布日 報告 評等"), ("side", "2/26 6690安碁資訊 買進"), ("main", "台股盤勢分析"))
    r = ev.score_order(text, items)
    assert r.n_hit == 3 and r.pair_acc == 1.0
