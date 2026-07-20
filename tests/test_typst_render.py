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

    def test_cjk_prose_survives(self):
        self.assertIn("台積電先進製程展望良好", self._typst("台積電先進製程展望良好。"))

    def test_section_heading_leaves_prose_and_becomes_section_key(self):
        """T3 起 `## ` 標題成為 Section.heading，不再留在內文片段裡。

        章節必須在 pandoc 之前切——否則標題會變成 Typst 的 `==` 而混進片段，
        模板就分不出區塊邊界。
        """
        doc = build_document("## 執行摘要\n\n台積電先進製程展望良好。", title="t")
        self.assertEqual(doc.sections[0].heading, "執行摘要")
        self.assertEqual(doc.sections[0].key, "exec_summary")
        body = "\n".join(b.typst for b in doc.sections[0].blocks if isinstance(b, ProseBlock))
        self.assertIn("台積電先進製程展望良好", body)
        self.assertNotIn("執行摘要", body)

    def test_h3_subsection_stays_in_body(self):
        """動態子節（`###`）不是章節邊界，須留在該章內文中由 pandoc 轉換。"""
        doc = build_document("## 重點分析\n\n### 需求結構\n\n內文", title="t")
        body = "\n".join(b.typst for b in doc.sections[0].blocks if isinstance(b, ProseBlock))
        self.assertIn("需求結構", body)


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


class SectionContractTests(unittest.TestCase):
    """模板契約：章節必須在 pandoc 之前切出來，且任何形態都不得丟內容。

    pandoc 會把 `## 執行摘要` 轉成 Typst `== 執行摘要`——若先轉再切，章節邊界就
    化進片段裡、模板再也分不出區塊。
    """

    def _doc(self, md: str):
        return build_document(md, title="t")

    def _text_of(self, section) -> str:
        return "\n".join(b.typst for b in section.blocks if isinstance(b, ProseBlock))

    def test_five_skeleton_sections_mapped(self):
        md = (
            "## 執行摘要\n\n摘要內文\n\n"
            "## 關鍵發現\n\n發現內文\n\n"
            "## 重點分析\n\n分析內文\n\n"
            "## 風險與展望\n\n風險內文\n\n"
            "## 引用來源\n\n[1] 來源\n"
        )
        doc = self._doc(md)
        self.assertEqual(
            [s.key for s in doc.sections],
            ["exec_summary", "key_findings", "analysis", "risk_outlook", "references"],
        )
        self.assertIn("摘要內文", self._text_of(doc.section("exec_summary")))

    def test_missing_section_is_simply_absent(self):
        """缺章不得炸——section() 回 None，模板自行決定怎麼呈現。"""
        doc = self._doc("## 執行摘要\n\n只有摘要\n")
        self.assertIsNotNone(doc.section("exec_summary"))
        self.assertIsNone(doc.section("key_findings"))

    def test_unknown_section_kept_with_null_key(self):
        """未知章節不得丟棄——內容是真相，版型不得吃掉內容。"""
        doc = self._doc("## 執行摘要\n\n摘要\n\n## 產業補充\n\n這段不能消失\n")
        unknown = [s for s in doc.sections if s.key is None]
        self.assertEqual(len(unknown), 1)
        self.assertEqual(unknown[0].heading, "產業補充")
        self.assertIn("這段不能消失", self._text_of(unknown[0]))

    def test_out_of_order_sections_preserved_in_source_order(self):
        """章節順序錯亂時保留原順序（模板決定排版順序，不在此重排）。"""
        doc = self._doc("## 引用來源\n\n[1] x\n\n## 執行摘要\n\n摘要\n")
        self.assertEqual([s.key for s in doc.sections], ["references", "exec_summary"])

    def test_preamble_before_first_section_kept(self):
        """`## ` 之前的前言（前導 # 標題）以 heading='' 承接，不丟。"""
        doc = self._doc("# 台積電深度研報\n\n前言段落\n\n## 執行摘要\n\n摘要\n")
        self.assertEqual(doc.sections[0].heading, "")
        self.assertIn("前言段落", self._text_of(doc.sections[0]))

    def test_no_sections_at_all_is_single_unkeyed_block(self):
        doc = self._doc("完全沒有章節標題的內文")
        self.assertEqual(len(doc.sections), 1)
        self.assertIsNone(doc.sections[0].key)
        self.assertIn("完全沒有章節標題的內文", self._text_of(doc.sections[0]))

    def test_hash_inside_fence_not_treated_as_section(self):
        """圍欄內出現的 `## ` 不得被誤判為章節邊界。"""
        md = f'## 重點分析\n\n分析\n\n```chart\n{_GOOD_CHART}\n```\n\n後續分析\n'
        doc = self._doc(md)
        self.assertEqual([s.key for s in doc.sections], ["analysis"])
        self.assertTrue([b for b in doc.sections[0].blocks if isinstance(b, ChartBlock)])

    def test_risk_heading_variant_mapped(self):
        self.assertEqual(self._doc("## 風險展望\n\nx\n").sections[0].key, "risk_outlook")

    def test_fences_attach_to_owning_section(self):
        md = (
            f"## 執行摘要\n\n摘要\n\n```kpi\n{_GOOD_KPI}\n```\n\n"
            f"## 重點分析\n\n分析\n\n```chart\n{_GOOD_CHART}\n```\n"
        )
        doc = self._doc(md)
        self.assertTrue([b for b in doc.section("exec_summary").blocks if isinstance(b, KpiBlock)])
        self.assertTrue([b for b in doc.section("analysis").blocks if isinstance(b, ChartBlock)])
        # KPI 不得漏到分析節去
        self.assertFalse([b for b in doc.section("analysis").blocks if isinstance(b, KpiBlock)])

    def test_flattened_blocks_property_preserves_order(self):
        md = f"## 執行摘要\n\n摘要\n\n```kpi\n{_GOOD_KPI}\n```\n\n## 重點分析\n\n分析\n"
        kinds = [type(b).__name__ for b in self._doc(md).blocks]
        self.assertEqual(kinds, ["ProseBlock", "KpiBlock", "ProseBlock"])


class DocumentTitleTests(unittest.TestCase):
    """文件級 `# 標題` 由 metadata 承載，不得再以散文重複渲染一次。

    模板本來就會排 metadata title，而正常生成流程固定輸出一個 `# 主標題`——先前它被
    當成前言散文原樣保留，於是**每一份研報的 PDF 都有兩個主標題**（呼叫端傳入的建議
    標題，加上 LLM 自己寫的標題），兩者不同時尤其刺眼。
    """

    def _prose(self, doc) -> str:
        return "\n".join(b.typst for b in doc.blocks if isinstance(b, ProseBlock))

    def test_doc_title_not_duplicated_in_body(self):
        doc = build_document("# LLM 主標題\n\n前言\n\n## 執行摘要\n\n摘要\n", title="建議標題")
        self.assertEqual(doc.meta.title, "建議標題")
        self.assertNotIn("LLM 主標題", self._prose(doc))

    def test_doc_title_promoted_when_caller_title_empty(self):
        """沒有建議標題時用 H1——總比無標題好。"""
        doc = build_document("# LLM 主標題\n\n前言\n\n## 執行摘要\n\n摘要\n", title="")
        self.assertEqual(doc.meta.title, "LLM 主標題")

    def test_preamble_prose_survives_title_removal(self):
        """移除的是標題那一行，不是整段前言。"""
        doc = build_document("# 主標題\n\n前言段落\n\n## 執行摘要\n\n摘要\n", title="T")
        self.assertIn("前言段落", self._prose(doc))

    def test_h1_inside_section_body_is_untouched(self):
        """章節內文裡的 `# ` 是內容，不是文件標題——不得被吃掉。"""
        doc = build_document("## 執行摘要\n\n# 內文中的一級標題\n\n摘要\n", title="T")
        self.assertIn("內文中的一級標題", self._prose(doc))

    def test_h1_inside_fence_is_not_the_doc_title(self):
        doc = build_document("```text\n# 這在圍欄內\n```\n\n## 執行摘要\n\n摘要\n", title="")
        self.assertEqual(doc.meta.title, "")

    def test_no_h1_at_all_is_safe(self):
        doc = build_document("## 執行摘要\n\n摘要\n", title="T")
        self.assertEqual(doc.meta.title, "T")


class GenericFenceTests(unittest.TestCase):
    """一般 ```/~~~ 圍欄內的 `## ` 是程式碼或範例，不是章節標題。

    先前只把 kpi/chart 圍欄視為圍欄，於是一個合法的 ```text / ```python 區塊會被章節
    regex 攔腰切成假章節，**原本的 code block 也跟著被截斷**（開頭圍欄留在上一節、
    閉合圍欄漏到下一節）。
    """

    def _headings(self, md: str):
        return [s.heading for s in build_document(md, title="T").sections]

    def test_hash_inside_generic_fence_is_not_a_section(self):
        for fence in ("```text", "```python", "~~~", "````"):
            with self.subTest(fence=fence):
                close = "````" if fence == "````" else fence[:3]
                md = f"## 執行摘要\n\n範例：\n\n{fence}\n## 這在圍欄內\n續行\n{close}\n\n後續\n"
                self.assertEqual(self._headings(md), ["執行摘要"])

    def test_code_block_content_not_truncated(self):
        md = "## 執行摘要\n\n```text\n## 圍欄內標題\n圍欄內續行\n```\n\n後續內文\n"
        doc = build_document(md, title="T")
        prose = "\n".join(b.typst for b in doc.blocks if isinstance(b, ProseBlock))
        self.assertIn("圍欄內續行", prose)
        self.assertIn("後續內文", prose)

    def test_real_sections_after_fence_still_split(self):
        md = "## 執行摘要\n\n```text\n## 假章節\n```\n\n## 關鍵發現\n\n要點\n"
        self.assertEqual(self._headings(md), ["執行摘要", "關鍵發現"])

    def test_unclosed_fence_swallows_rest(self):
        """未閉合圍欄延伸到文末（CommonMark）——其後的 `## ` 不是章節。"""
        self.assertEqual(self._headings("## 執行摘要\n\n```text\n## 未閉合\n"), ["執行摘要"])

    def test_inline_code_is_not_a_fence(self):
        """反引號圍欄的 info string 不得含反引號——行內碼不算圍欄。"""
        md = "## 執行摘要\n\n行內 ```code``` 文字\n\n## 關鍵發現\n\n要點\n"
        self.assertEqual(self._headings(md), ["執行摘要", "關鍵發現"])


class ChartCaptionTests(unittest.TestCase):
    """圖表的來源標記是研報可追溯性的一部分，不得因為換渲染器而消失。

    Typst 軌先前只保存 SVG、固定傳空 caption，於是 `source: "[7]"` 在 PDF 上只剩
    「圖 1」——WeasyPrint 軌一直都顯示來源，等於新的預設渲染器弄丟了追溯資訊。
    """

    def _chart(self, spec: str) -> ChartBlock:
        blocks = [b for b in build_document(f"```chart\n{spec}\n```", title="T").blocks
                  if isinstance(b, ChartBlock)]
        self.assertEqual(len(blocks), 1)
        return blocks[0]

    def test_source_and_title_preserved(self):
        spec = ('{"type":"bar","title":"營收趨勢","source":"[7]",'
                '"x":["Q1"],"series":[{"name":"營收","values":[1]}]}')
        self.assertEqual(self._chart(spec).caption, "營收趨勢（來源 [7]）")

    def test_source_without_title_still_shown(self):
        spec = ('{"type":"bar","source":"[7]","x":["Q1"],'
                '"series":[{"name":"營收","values":[1]}]}')
        self.assertIn("[7]", self._chart(spec).caption)

    def test_no_caption_when_neither_present(self):
        self.assertEqual(self._chart(_GOOD_CHART).caption, "")

    def test_caption_reaches_emitted_typst(self):
        """中介模型存了不算數——必須真的傳進 chart-figure 的 caption 參數。"""
        from app.services.typst_render import emit_typst

        spec = ('{"type":"bar","title":"營收趨勢","source":"[7]",'
                '"x":["Q1"],"series":[{"name":"營收","values":[1]}]}')
        src = emit_typst(build_document(f"```chart\n{spec}\n```", title="T"), disclaimer="D")
        call = [ln for ln in src.splitlines() if ln.startswith("#chart-figure(")]
        self.assertEqual(len(call), 1)
        self.assertIn("營收趨勢（來源 [7]）", call[0])

    def test_both_renderers_share_one_caption_source(self):
        """複製一份必漂移——兩軌都必須走 pdf.chart_caption。"""
        import json

        from app.services.pdf import chart_caption

        spec = ('{"type":"bar","title":"營收","source":"[3]",'
                '"x":["Q1"],"series":[{"name":"s","values":[1]}]}')
        self.assertEqual(self._chart(spec).caption, chart_caption(json.loads(spec)))

    def test_malformed_spec_shape_does_not_raise(self):
        """形狀防禦（CLAUDE.md 紅線）：caption 取值不得成為新的炸點。"""
        from app.services.pdf import chart_caption

        for bad in (None, [], "str", 3, {"title": None}, {"source": []}):
            with self.subTest(bad=bad):
                self.assertIsInstance(chart_caption(bad), str)


if __name__ == "__main__":
    unittest.main()
