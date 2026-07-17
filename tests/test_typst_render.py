"""M9a T2：markdown → 型別化中介模型。

兩條紅線：
1. 形狀防禦（CLAUDE.md）——畸形 LLM JSON 不得讓例外逃出，該區塊略過即可。
   WeasyPrint 路徑曾因此炸穿 render_report_pdf（無 PDF、無持久化、重建永久 500）。
2. 注入跳脫——Typst 有 #eval/#read/#import，LLM 原文若被當原始碼拼接等於任意執行。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services.typst_render import (  # noqa: E402
    ChartBlock,
    KpiBlock,
    ProseBlock,
    _split_fences,
    build_document,
)

_GOOD_CHART = '{"type":"bar","x":["Q1","Q2"],"series":[{"name":"營收","values":[1,2]}]}'
_GOOD_KPI = '{"source":"[1]","items":[{"value":"NT$1,280","label":"目標價","change":"+6.2%","dir":"up"}]}'


class SplitFencesTests(unittest.TestCase):
    def test_preserves_relative_order_of_kpi_and_chart(self):
        """分開掃兩種圍欄會丟失相對順序，故用單一 regex。"""
        md = f"前言\n\n```chart\n{_GOOD_CHART}\n```\n\n中段\n\n```kpi\n{_GOOD_KPI}\n```\n\n結尾"
        kinds = [k for k, _ in _split_fences(md)]
        self.assertEqual(kinds, ["prose", "chart", "prose", "kpi", "prose"])

    def test_no_fences_is_single_prose(self):
        self.assertEqual([k for k, _ in _split_fences("純文字")], ["prose"])

    def test_empty_input(self):
        self.assertEqual(_split_fences(""), [])


class KpiShapeDefenceTests(unittest.TestCase):
    """畸形 kpi JSON：一律略過該區塊，例外不得外漏。"""

    def _blocks(self, raw: str):
        return build_document(f"```kpi\n{raw}\n```", title="t").blocks

    def test_malformed_kpi_specs_are_skipped_without_raising(self):
        for raw in (
            "{bad json",                                  # 壞 JSON
            "[]",                                         # 非物件（裸陣列）
            "null",                                       # 非物件
            '"just a string"',                            # 非物件
            "123",                                        # 非物件
            '{"items":"not-a-list"}',                     # items 非陣列
            '{"items":null}',                             # items 為 null
            '{"items":[]}',                               # items 空
            '{"items":[1,2,3]}',                          # item 非 dict（裸值）
            '{"items":[["nested"]]}',                     # item 為陣列
            '{"items":{"not":"a list"}}',                 # items 為物件
            '{"items":[null,null]}',                      # item 全為 null
        ):
            with self.subTest(raw=raw):
                blocks = self._blocks(raw)  # 不得拋例外
                self.assertFalse(
                    [b for b in blocks if isinstance(b, KpiBlock)],
                    f"畸形規格不應產生 KpiBlock: {raw}",
                )

    def test_nested_object_field_is_stringified_not_crashed(self):
        """欄位形狀漂移成物件時 str() 掉，不崩潰（item 本身是 dict 故仍算有效）。"""
        blocks = self._blocks('{"items":[{"value":{"a":1},"label":"x"}]}')
        kpis = [b for b in blocks if isinstance(b, KpiBlock)]
        self.assertEqual(len(kpis), 1)
        self.assertIn("a", kpis[0].items[0].value)

    def test_items_over_limit_truncated_to_five(self):
        items = ",".join(f'{{"value":"{i}","label":"l"}}' for i in range(9))
        kpis = [b for b in self._blocks(f'{{"items":[{items}]}}') if isinstance(b, KpiBlock)]
        self.assertEqual(len(kpis[0].items), 5)

    def test_valid_kpi_parsed_with_item_source_fallback_to_block(self):
        kpis = [b for b in self._blocks(_GOOD_KPI) if isinstance(b, KpiBlock)]
        self.assertEqual(len(kpis), 1)
        it = kpis[0].items[0]
        self.assertEqual(it.label, "目標價")
        self.assertEqual(it.direction, "up")
        self.assertEqual(it.source, "[1]")  # item 無 source → 退回 block source

    def test_invalid_dir_dropped(self):
        kpis = [
            b for b in self._blocks('{"items":[{"value":"1","label":"l","dir":"sideways"}]}')
            if isinstance(b, KpiBlock)
        ]
        self.assertEqual(kpis[0].items[0].direction, "")


class ChartShapeDefenceTests(unittest.TestCase):
    def _blocks(self, raw: str):
        return build_document(f"```chart\n{raw}\n```", title="t").blocks

    def test_malformed_chart_specs_are_skipped_without_raising(self):
        for raw in (
            "{bad json",
            "null",
            "[]",
            '{"type":"pyramid","x":["a"],"series":[{"values":[1]}]}',  # 不支援的型別
            '{"type":"bar","x":"not-a-list","series":[{"values":[1]}]}',
            '{"type":"bar","x":[],"series":[{"values":[1]}]}',         # x 空
            '{"type":"bar","x":["a"],"series":"not-a-list"}',
            '{"type":"bar","x":["a"],"series":[]}',                    # series 空
            '{"type":"bar","x":["a"],"series":[{"values":"nope"}]}',
            '{"type":"bar","x":["a"],"series":[{"values":["非數字"]}]}',
            '{"type":"bar","x":["a"],"series":[{"values":[true]}]}',   # bool 不算數字
        ):
            with self.subTest(raw=raw):
                blocks = self._blocks(raw)
                self.assertFalse(
                    [b for b in blocks if isinstance(b, ChartBlock)],
                    f"畸形規格不應產生 ChartBlock: {raw}",
                )

    def test_valid_chart_produces_svg(self):
        charts = [b for b in self._blocks(_GOOD_CHART) if isinstance(b, ChartBlock)]
        self.assertEqual(len(charts), 1)
        self.assertIn("<svg", charts[0].svg)


class PandocEscapingTests(unittest.TestCase):
    """LLM 原文只能經 pandoc 跳脫後進 Typst——注入向量必須成為字面文字。

    下方測試資料中的 `#eval`/`#read`/`#import` 是 **Typst 語言的語法字串**，不是
    Python 的 eval：它們以字串常值形式餵進轉換器，斷言的正是「轉出後成為字面文字
    `\\#eval`、不會被 Typst 編譯器執行」。這裡沒有任何動態求值。
    """

    def _typst(self, md: str) -> str:
        blocks = build_document(md, title="t").blocks
        return "\n".join(b.typst for b in blocks if isinstance(b, ProseBlock))

    def test_typst_injection_vectors_are_escaped(self):
        out = self._typst('#eval("1+1") 與 #read("/etc/passwd") 與 #import "@preview/x:1.0.0"')
        self.assertIn("\\#eval", out)
        self.assertIn("\\#read", out)
        self.assertIn("\\#import", out)

    def test_dollar_not_treated_as_math(self):
        """D6：gfm-tex_math_dollars 關閉後，財經文本的 $ 不得誤配對成數學模式。"""
        out = self._typst("$100 美元與 EPS $14.2，區間 $880–$1,088")
        self.assertIn("\\$100", out)
        self.assertIn("\\$14.2", out)

    def test_at_reference_escaped(self):
        self.assertIn("\\@", self._typst("聯絡 @someone 取得資料"))

    def test_heading_and_cjk_survive(self):
        out = self._typst("## 執行摘要\n\n台積電先進製程展望良好。")
        self.assertIn("執行摘要", out)
        self.assertIn("台積電先進製程展望良好", out)


class BuildDocumentTests(unittest.TestCase):
    def test_meta_carried(self):
        doc = build_document("內文", title="標題", meta={"date": "2026-07-17", "question": "Q?"})
        self.assertEqual(doc.meta.title, "標題")
        self.assertEqual(doc.meta.date, "2026-07-17")
        self.assertEqual(doc.meta.question, "Q?")

    def test_meta_none_safe(self):
        doc = build_document("內文", title="標題")
        self.assertEqual(doc.meta.date, "")

    def test_bad_block_does_not_drop_surrounding_prose(self):
        """一個壞區塊不得帶走整份內容——寧可少一張卡，不可沒有 PDF。"""
        md = "前言段落\n\n```kpi\n{bad\n```\n\n結尾段落"
        doc = build_document(md, title="t")
        prose = "\n".join(b.typst for b in doc.blocks if isinstance(b, ProseBlock))
        self.assertIn("前言段落", prose)
        self.assertIn("結尾段落", prose)
        self.assertFalse([b for b in doc.blocks if isinstance(b, KpiBlock)])

    def test_empty_markdown_yields_no_blocks(self):
        self.assertEqual(build_document("", title="t").blocks, ())


if __name__ == "__main__":
    unittest.main()
