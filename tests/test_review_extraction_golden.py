"""scripts/review_extraction_golden.py 的純函式測試（不開 PDF、不渲染）。

釘住定位邏輯：片段跨行時要回多個框、字序退讓要能接起側欄的兩行、找不到回 None。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "review_extraction_golden.py"


def _load():
    spec = importlib.util.spec_from_file_location("review_extraction_golden", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["review_extraction_golden"] = mod
    spec.loader.exec_module(mod)
    return mod


rv = _load()


def _w(text, x0, top, width=10.0, height=10.0):
    return {"text": text, "x0": x0, "x1": x0 + width, "top": top, "bottom": top + height}


def test_locate_single_line_returns_one_box_covering_matched_words():
    words = [_w("增加持股", 10, 100, 40), _w("維持", 55, 100, 20), _w("其他", 10, 120)]
    found = rv.locate_on_page({"position": words}, "增加持股 維持")
    assert found is not None
    boxes, ordering = found
    assert ordering == "position"
    assert len(boxes) == 1
    assert boxes[0] == (10, 100, 75, 110)


def test_locate_multi_line_returns_one_box_per_line():
    words = [_w("華通", 10, 100), _w("月營收", 25, 100), _w("億元", 10, 115)]
    found = rv.locate_on_page({"position": words}, "華通月營收億元")
    assert found is not None
    assert len(found[0]) == 2


def test_locate_falls_back_to_next_ordering_when_columns_interleave():
    # 位置序把側欄第二行與主文第一行交錯；分欄序才接得起側欄兩行
    side1, main1, side2 = _w("焦點", 10, 100), _w("主文一", 300, 105), _w("內容", 10, 112)
    by_pos = [side1, main1, side2]
    found = rv.locate_on_page({"position": by_pos, "columns": [side1, side2, main1]}, "焦點內容")
    assert found is not None
    assert found[1] == "columns"


def test_locate_is_whitespace_and_case_insensitive():
    words = [_w("Target", 0, 0), _w("Price", 50, 0), _w("(NT$):", 100, 0), _w("800", 150, 0)]
    assert rv.locate_on_page({"position": words}, "target price (nt$): 800") is not None


def test_locate_missing_returns_none():
    assert rv.locate_on_page({"position": [_w("甲", 0, 0)]}, "乙") is None
    assert rv.locate_on_page({"position": [_w("甲", 0, 0)]}, "") is None


def test_orderings_split_columns_by_page_midpoint():
    words = [_w("右", 400, 0), _w("左", 10, 0), _w("左二", 10, 20)]
    o = rv._orderings(words, 600.0)
    assert [w["text"] for w in o["columns"]] == ["左", "左二", "右"]
    assert [w["text"] for w in o["position"]] == ["左", "右", "左二"]
    assert o["text_flow"] is words
