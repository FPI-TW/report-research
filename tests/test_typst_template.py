"""M9a T4：ib-classic.typ 模板的編譯與版面回歸。

**為什麼要斷言頁數**：Typst 的 `height: 100%` 不像 CSS 那樣相對於父容器，而是相對於
整頁。實作初版把鎏金豎線寫成 `rect(height: 100%)`、KPI 卡寫成 `block(height: 100%)`，
結果每條豎線／每張卡都撐開一整頁——**一份單頁報告變成 6 頁、內容被推光，而編譯完全
不報錯、PDF 也有 73KB**。單看「compile 沒拋例外」或「PDF 非空」是驗不出來的，只有
頁數與目視能抓到。這組測試把它釘住。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import typst  # noqa: E402

_SMOKE = REPO_ROOT / "tests" / "fixtures" / "ib_classic_smoke.typ"
_TEMPLATE = REPO_ROOT / "app" / "templates" / "ib-classic.typ"


def _code_only(src: str) -> str:
    """剔除 Typst 註解後的程式碼。

    模板的註解刻意寫著「不要用 height: 100%」「不得使用 style: "italic"」這類警告，
    直接 grep 原始碼會把警告本身當成違規（第一版測試就是這樣自己打自己）。
    """
    out = []
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith("//"):
            continue
        idx = line.find("//")
        out.append(line if idx == -1 else line[:idx])
    return "\n".join(out)


class TemplateCompileTests(unittest.TestCase):
    def test_template_file_exists(self):
        self.assertTrue(_TEMPLATE.is_file(), "ib-classic.typ 不存在")

    def test_smoke_compiles_to_pdf(self):
        pdf = typst.compile(str(_SMOKE), root=str(REPO_ROOT))
        self.assertGreater(len(pdf), 10_000, "PDF 過小，可能沒渲染出內容")
        self.assertTrue(pdf.startswith(b"%PDF"), "輸出不是 PDF")

    def test_smoke_is_single_page(self):
        """版面回歸：內容這麼少就該是 1 頁。

        頁數暴增＝有元素被撐開（典型成因：把 CSS 的 height: 100% 帶進 Typst）。
        """
        pages = typst.compile(str(_SMOKE), root=str(REPO_ROOT), format="png", ppi=72)
        pages = pages if isinstance(pages, list) else [pages]
        self.assertEqual(len(pages), 1, f"預期 1 頁，實得 {len(pages)} 頁——版面被撐開了")

    def test_no_italic_in_template(self):
        """繁中無斜體字重（Noto CJK），Typst 不合成 → 模板不得使用斜體。"""
        src = _code_only(_TEMPLATE.read_text(encoding="utf-8"))
        for bad in ('style: "italic"', "emph("):
            self.assertNotIn(bad, src, f"模板不得使用斜體：{bad}")

    def test_no_preview_package_dependency(self):
        """零 @preview 依賴才能無網路編譯開箱即得（spike D5）。"""
        src = _code_only(_TEMPLATE.read_text(encoding="utf-8"))
        self.assertNotIn("@preview", src, "模板不得依賴 @preview 套件")

    def test_no_percentage_height(self):
        """height: 100% 在 Typst 相對於整頁，不是父容器——會撐爆版面。"""
        src = _code_only(_TEMPLATE.read_text(encoding="utf-8"))
        self.assertNotIn("height: 100%", src, "不得使用 height: 100%（見本檔 docstring）")


if __name__ == "__main__":
    unittest.main()
