"""M9a T5/T6：渲染器雙軌分派、fail-open 回退、免責兩軌一致。

**為什麼要獨立測分派**：這是本專案的累犯點（patch-where-used）——測試 patch 一個符號，
生產卻走另一條路徑，於是測試全綠而功能是壞的。這裡直接斷言「哪一個渲染器被呼叫」。

**為什麼免責要測回退路徑**：fail-open 存在正是為了應付沒預料到的情況，那恰恰是最不該
少免責的時候。雷達改版時免責就是在無人看著的地方無聲消失的（PR #87 cc054c3）。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import app.services.report as rpt  # noqa: E402
from app.services.pdf import REPORT_DISCLAIMER, render_report_pdf as weasy_render  # noqa: E402

_MD = "## 執行摘要\n\n內文[1]。\n\n## 引用來源\n\n[1] a.pdf（TW·2026-06-01）\n"
_META = {"date": "2026-07-17", "question": "Q"}


class DispatchTests(unittest.TestCase):
    def test_typst_selected_when_renderer_is_typst(self):
        with patch.object(rpt, "REPORT_RENDERER", "typst"), patch(
            "app.services.typst_render.render_report_pdf", return_value=b"%PDF-typst"
        ) as m_typst, patch.object(rpt, "_render_weasyprint") as m_weasy:
            out = rpt.render_report_pdf(_MD, title="T", meta=_META)
        self.assertEqual(out, b"%PDF-typst")
        m_typst.assert_called_once()
        m_weasy.assert_not_called()

    def test_weasyprint_selected_when_renderer_is_weasyprint(self):
        with patch.object(rpt, "REPORT_RENDERER", "weasyprint"), patch(
            "app.services.typst_render.render_report_pdf"
        ) as m_typst, patch.object(
            rpt, "_render_weasyprint", return_value=b"%PDF-weasy"
        ) as m_weasy:
            out = rpt.render_report_pdf(_MD, title="T", meta=_META)
        self.assertEqual(out, b"%PDF-weasy")
        m_weasy.assert_called_once()
        m_typst.assert_not_called()

    def test_typst_failure_falls_back_to_weasyprint(self):
        """Typst 炸掉不得讓研報沒有 PDF——無 PDF＝無持久化＝重建永久 500。"""
        with patch.object(rpt, "REPORT_RENDERER", "typst"), patch(
            "app.services.typst_render.render_report_pdf", side_effect=RuntimeError("typst boom")
        ), patch.object(rpt, "_render_weasyprint", return_value=b"%PDF-weasy") as m_weasy:
            out = rpt.render_report_pdf(_MD, title="T", meta=_META)
        self.assertEqual(out, b"%PDF-weasy")
        m_weasy.assert_called_once()

    def test_fallback_does_not_swallow_weasyprint_failure(self):
        """兩軌都壞就該拋——靜默回空 bytes 會產出壞檔並被當成成功。"""
        with patch.object(rpt, "REPORT_RENDERER", "typst"), patch(
            "app.services.typst_render.render_report_pdf", side_effect=RuntimeError("typst boom")
        ), patch.object(rpt, "_render_weasyprint", side_effect=RuntimeError("weasy boom")):
            with self.assertRaises(RuntimeError):
                rpt.render_report_pdf(_MD, title="T", meta=_META)

    def test_server_imports_dispatcher_not_pdf_directly(self):
        """PDF 重建端點必須走分派層，否則重建永遠是 WeasyPrint 版。"""
        import web.server as srv

        self.assertIs(srv.render_report_pdf, rpt.render_report_pdf)


class DisclaimerTests(unittest.TestCase):
    """免責在兩軌都必須存在，且文字同源。"""

    def test_weasyprint_html_contains_disclaimer(self):
        from app.services.pdf import _build_document

        html = _build_document(_MD, title="T", meta=_META)
        self.assertIn("非系統預測", html)
        self.assertIn("不構成投資建議", html)

    def test_weasyprint_fancy_layout_contains_disclaimer(self):
        """完整研報走 fancy 版型（另一條分支）——同樣要有免責。"""
        from app.services.pdf import _build_document

        md = (
            "# 深度研報\n\n## 執行摘要\n\n摘要[1]。\n\n## 關鍵發現\n\n發現[1]。\n\n"
            "## 重點分析\n\n分析[1]。\n\n## 引用來源\n\n[1] a.pdf\n"
        )
        html = _build_document(md, title="T", meta=_META)
        self.assertIn("非系統預測", html)

    def test_typst_source_contains_disclaimer(self):
        from app.services.typst_render import build_document, emit_typst

        doc = build_document(_MD, title="T", meta=_META)
        src = emit_typst(doc, disclaimer=REPORT_DISCLAIMER)
        self.assertIn("非系統預測", src)
        self.assertIn("disclaimer:", src)

    def test_disclaimer_not_dependent_on_llm_output(self):
        """LLM markdown 完全沒提免責時，兩軌仍須有免責。"""
        from app.services.pdf import _build_document
        from app.services.typst_render import build_document, emit_typst

        bare = "## 執行摘要\n\n先進製程產能滿載，營收預估上修。\n"
        self.assertNotIn("免責", bare)  # 前提：markdown 本身不含任何免責字樣
        self.assertIn("非系統預測", _build_document(bare, title="T", meta=_META))
        self.assertIn(
            "非系統預測",
            emit_typst(build_document(bare, title="T", meta=_META), disclaimer=REPORT_DISCLAIMER),
        )

    def test_single_source_of_truth(self):
        """兩軌共用同一常數——各留一份必然漂移。"""
        import app.services.typst_render as tr

        self.assertIn("非系統預測", REPORT_DISCLAIMER)
        # typst_render 從 pdf 取用，未自行定義
        self.assertFalse(
            hasattr(tr, "REPORT_DISCLAIMER") and tr.__dict__.get("REPORT_DISCLAIMER"),
            "typst_render 不得自行定義免責文字",
        )


class TypstEscapingTests(unittest.TestCase):
    """emit 是安全邊界：任何未跳脫的值拼進 Typst 原始碼＝任意執行。"""

    def _emit(self, **meta_kw):
        from app.services.typst_render import build_document, emit_typst

        doc = build_document(_MD, title=meta_kw.pop("title", "T"), meta=meta_kw or _META)
        return emit_typst(doc, disclaimer=REPORT_DISCLAIMER)

    def test_quotes_in_title_escaped(self):
        src = self._emit(title='他說"買進"', date="2026-07-17", question="Q")
        self.assertIn('\\"買進\\"', src)

    def test_backslash_in_title_escaped(self):
        src = self._emit(title="path\\to\\file", date="d", question="Q")
        self.assertIn("path\\\\to\\\\file", src)

    def test_newline_in_meta_escaped(self):
        """未逸出的換行會截斷 Typst 字串常值 → 編譯錯誤或語法注入。"""
        src = self._emit(title="line1\nline2", date="d", question="Q")
        self.assertIn("line1\\nline2", src)
        self.assertNotIn("line1\nline2", src)

    def test_typst_injection_in_title_is_literal(self):
        # 下方的 `#eval` 是 **Typst 語言的語法字串**（測試資料），不是 Python 的 eval：
        # 這裡沒有任何動態求值，斷言的正是「它被包成字串常值、不會被 Typst 編譯器執行」。
        src = self._emit(title='#eval("1+1")', date="d", question="Q")
        # 標題被包成字串常值，# 不會被當成 Typst 程式碼起始
        self.assertIn('title: "#eval(\\"1+1\\")"', src)



class HostileFixtureTests(unittest.TestCase):
    """完整管線（markdown → emit → compile）對敵意輸入的實證。

    spike 驗的是 pandoc 單步；這裡驗的是整條管線——emit 會把 metadata 拼進原始碼，
    那是 pandoc 管不到的地方（由 `_tstr` 負責）。

    fixture 內的 `#eval`/`#read`/`#import` 是 **Typst 語法字串**（測試資料），
    斷言的正是它們被跳脫成字面文字、不會被 Typst 編譯器執行。
    """

    _FIXTURE = REPO_ROOT / "docs" / "typst_spike" / "fixture_hostile.md"

    def setUp(self):
        if not self._FIXTURE.is_file():
            self.skipTest("敵意 fixture 不存在")
        self.md = self._FIXTURE.read_text(encoding="utf-8")

    def test_hostile_markdown_compiles_to_pdf(self):
        pdf = rpt.render_report_pdf(self.md, title="敵意輸入測試", meta=_META)
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertGreater(len(pdf), 10_000)

    def test_heading_injection_is_not_executed(self):
        """**章節標題是唯一沒被 pandoc 跳脫過的 LLM 原文**——必須由 emitter 以字串
        常值輸出。初版用 `#section-heading[...]` 的 content 語法，實測 `## #eval(...)`
        會真的求值、`## #read("/.env")` 會把 repo root 的 .env（含共用帳密）渲染進
        可下載的 PDF、`]` 還能脫出 content block 接任意指令。
        """
        from app.services.typst_render import build_document, emit_typst

        src = emit_typst(
            build_document(self.md, title="敵意輸入測試", meta=_META),
            disclaimer=REPORT_DISCLAIMER,
        )
        # 標題必須成為字串常值的引數，不得是 content block
        self.assertNotIn("#section-heading[", src, "標題不得用 content 語法（可注入）")
        for probe in ('#section-heading("#eval', '#section-heading("#read'):
            with self.subTest(probe=probe):
                self.assertIn(probe, src, "標題應以字串常值輸出")

    def test_heading_with_dollar_not_mangled(self):
        """`## 2026 年 EPS 上修 $14.2 至 $16.8` 是正常財經標題。

        spec D6 的 gfm-tex_math_dollars 只作用於 pandoc，對標題無效——content 語法下
        兩個 `$` 會被配對成數學模式吃掉內容（編譯成功、零錯誤，但字沒了）。
        """
        from app.services.typst_render import build_document, emit_typst

        doc = build_document("## 2026 年 EPS 上修 $14.2 至 $16.8\n\n內文\n", title="T", meta=_META)
        src = emit_typst(doc, disclaimer=REPORT_DISCLAIMER)
        self.assertIn('#section-heading("2026 年 EPS 上修 $14.2 至 $16.8")', src)

    def test_hostile_pdf_page_count_sane(self):
        """頁數是唯一能自動抓到「版面被撐開」與「#read 把整份檔案灌進 PDF」的訊號。

        PDF bytes 數量不能當驗收——初版曾編譯成功、73KB、零錯誤，但整份是 6 頁空白。
        """
        import typst

        from app.services.pdf import REPORT_DISCLAIMER as _D
        from app.services.typst_render import build_document, emit_typst

        src = emit_typst(
            build_document(self.md, title="敵意輸入測試", meta=_META), disclaimer=_D
        )
        tmp = REPO_ROOT / "_hostile_pagecheck.typ"
        tmp.write_text(src, encoding="utf-8")
        try:
            pages = typst.compile(str(tmp), root=str(REPO_ROOT), format="png", ppi=72)
            pages = pages if isinstance(pages, list) else [pages]
        finally:
            tmp.unlink(missing_ok=True)
        self.assertLessEqual(len(pages), 3, f"{len(pages)} 頁——版面被撐開或有檔案被讀入")

    def test_injection_vectors_escaped_in_emitted_source(self):
        from app.services.typst_render import build_document, emit_typst

        src = emit_typst(
            build_document(self.md, title="敵意輸入測試", meta=_META),
            disclaimer=REPORT_DISCLAIMER,
        )
        for vector in ("\\#eval", "\\#read", "\\#import", "\\#set", "\\$", "\\@"):
            with self.subTest(vector=vector):
                self.assertIn(vector, src, f"{vector} 未被 pandoc 跳脫")

    def test_only_our_own_import_is_unescaped(self):
        """原始碼裡唯一未跳脫的 `#import` 必須是模板自己的那行（第 1 行）。

        raw 區塊（反引號）內的 `#eval` 不求值，故不在此檢查範圍。
        """
        import re

        from app.services.typst_render import build_document, emit_typst

        src = emit_typst(
            build_document(self.md, title="敵意輸入測試", meta=_META),
            disclaimer=REPORT_DISCLAIMER,
        )
        imports = [
            m for m in re.finditer(r"(?<!\\)#import", src)
        ]
        self.assertEqual(len(imports), 1, "除模板 import 外不得有未跳脫的 #import")
        self.assertLess(imports[0].start(), src.index("\n"), "模板 import 應在第 1 行")


class ProseFailureTests(unittest.TestCase):
    """pandoc 壞掉不得靜默產出「只有標題、沒有內文」的空殼研報。

    先前 _prose_to_typst 逐段 fail-open（回 ""），在文件層級累積成內容全失：33KB、
    %PDF 開頭、五章標題俱在、免責俱在、零例外——唯獨沒有任何內文，然後照樣落地寫 DB
    當成功。且因為不拋，分派層的 fail-open 永遠不會觸發、WeasyPrint 也救不了。
    """

    def test_pandoc_failure_raises_not_silently_empty(self):
        from app.services.typst_render import ProseConversionError, build_document

        with patch("pypandoc.convert_text", side_effect=RuntimeError("pandoc dead")):
            with self.assertRaises(ProseConversionError):
                build_document(_MD, title="T", meta=_META)

    def test_pandoc_failure_falls_back_to_weasyprint(self):
        """整條鏈：pandoc 壞 → typst 拋 → 分派層回退 WeasyPrint（它不依賴 pandoc）。"""
        with patch.object(rpt, "REPORT_RENDERER", "typst"), patch(
            "pypandoc.convert_text", side_effect=RuntimeError("pandoc dead")
        ), patch.object(rpt, "_render_weasyprint", return_value=b"%PDF-weasy") as m_weasy:
            out = rpt.render_report_pdf(_MD, title="T", meta=_META)
        self.assertEqual(out, b"%PDF-weasy")
        m_weasy.assert_called_once()

    def test_block_level_failure_still_fails_open(self):
        """區塊層 fail-open 維持不變——少一張畸形 KPI 卡不影響研報成立。"""
        from app.services.typst_render import KpiBlock, build_document

        doc = build_document("## 執行摘要\n\n內文\n\n```kpi\n{bad json\n```\n", title="T", meta=_META)
        self.assertFalse([b for s in doc.sections for b in s.blocks if isinstance(b, KpiBlock)])
        self.assertTrue(doc.sections)


if __name__ == "__main__":
    unittest.main()
