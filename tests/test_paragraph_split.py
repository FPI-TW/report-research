# tests/test_paragraph_split.py
"""巨型段落切分（`pdf.split_long_paragraphs`）——兩軌共用，故**不掛 weasyprint gate**。

這條的行為是純字串邏輯：typst 軌與 weasyprint 軌都吃同一份切好的 markdown，測試沒有
理由因為缺 weasyprint 原生庫而被略過（放在 test_pdf.py 會被該檔的 importorskip 跳掉）。
"""
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

class SplitLongParagraphsTests(unittest.TestCase):
    """巨型段落切分：實測樣張有寬度 2765（約 59 行）的單一段落，雙欄窄欄下就是一整欄
    的墨。門檻用顯示寬度而非句數——同一份樣張有「4 句、59 行」的段落。
    """

    def _w(self, s):
        from app.services.pdf import _display_width

        return _display_width(s)

    def _long_en(self, n=12):
        return " ".join(
            f"Sentence number {i} describes a structural shift in AI server demand "
            f"with meaningful revenue contribution through 2026 and beyond."
            for i in range(n)
        )

    def test_long_paragraph_is_split_at_sentence_boundaries(self):
        from app.services.pdf import split_long_paragraphs

        src = "# T\n\n" + self._long_en()
        out = split_long_paragraphs(src)
        paras = [p for p in out.split("\n\n") if p.strip() and not p.startswith("#")]
        self.assertGreater(len(paras), 1, "過長段落未被切開")
        for p in paras:
            self.assertIn(p.rstrip()[-1], ".!?。！？", f"切在句子中間：{p[-40:]!r}")

    def test_idempotent(self):
        """切完每段都低於門檻，再跑一次不得再變（重出 PDF 不會越切越碎）。"""
        from app.services.pdf import split_long_paragraphs

        once = split_long_paragraphs("# T\n\n" + self._long_en())
        self.assertEqual(split_long_paragraphs(once), once)

    def test_short_paragraph_untouched(self):
        from app.services.pdf import split_long_paragraphs

        src = "# T\n\n短段落，不該被動到。\n"
        self.assertEqual(split_long_paragraphs(src), src)

    def test_fences_lists_quotes_tables_untouched(self):
        """圍欄／清單／引用／表格／`[n]` 引用行一律原樣——寧可漏切，不可切壞結構。

        ```chart 的 JSON 內部可能有空行，圍欄狀態必須跨「以空行分塊」追蹤，否則
        JSON 會被當散文處理。
        """
        from app.services.pdf import split_long_paragraphs

        long_line = self._long_en(8)
        for src in (
            '# T\n\n```chart\n{"type":"bar",\n\n"x":["Q1"]}\n```\n',
            f"# T\n\n- {long_line}\n",
            f"# T\n\n> {long_line}\n",
            f"# T\n\n| a | b |\n| --- | --- |\n| {long_line} | x |\n",
            f"# T\n\n[1] {long_line}\n",
            f"# T\n\n### {long_line}\n",
        ):
            with self.subTest(src=src[:28]):
                self.assertEqual(split_long_paragraphs(src), src)

    def test_abbreviations_and_decimals_are_not_boundaries(self):
        """`vs. 2025`、`J. P. Morgan`、`US$18.4 billion` 不是句界——切在那裡會產生半句。"""
        from app.services.pdf import split_long_paragraphs

        para = (
            "Revenue rose to US$18.4 billion vs. 2025 on stronger demand. "
            "J. P. Morgan is cited as raising its 2026-2028 estimate by 37%-53%. "
        ) * 6
        out = split_long_paragraphs("# T\n\n" + para)
        self.assertIn("vs. 2025", out)
        self.assertIn("J. P. Morgan", out)
        self.assertIn("US$18.4 billion", out)

    def test_single_sentence_monster_left_alone(self):
        """整段只有一個句子時沒有安全切點——原樣回傳，不得硬切。"""
        from app.services.pdf import split_long_paragraphs

        src = "# T\n\n" + "詞" * 900
        self.assertEqual(split_long_paragraphs(src), src)

    def test_cjk_width_counted_double(self):
        """中英混排的門檻必須用顯示寬度：len() 會讓中文段落晚一倍才被切。"""
        from app.services.pdf import split_long_paragraphs

        zh = "台積電 2026 年資本支出上調，CoWoS 產能持續擴充。記憶體價格是主要變數。" * 20
        out = split_long_paragraphs("# T\n\n" + zh)
        paras = [p for p in out.split("\n\n") if p.strip() and not p.startswith("#")]
        self.assertGreater(len(paras), 1, "中文長段落未被切開（門檻可能用了 len()）")
        # 中文句子之間沒有空白：句界判準若統一要求 \\s+，整段永遠只有一句而切不動
        for para in paras:
            self.assertEqual(para.rstrip()[-1], "。", f"切在句中：{para[-30:]!r}")
        self.assertEqual(
            "".join(paras).replace(" ", ""), zh.replace(" ", ""), "切分不得改動內容"
        )


if __name__ == "__main__":
    unittest.main()
