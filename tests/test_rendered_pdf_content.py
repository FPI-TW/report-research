# tests/test_rendered_pdf_content.py
"""驗**渲染後 PDF 裡真的有什麼**（F2／F5）。

現有的渲染守門停在兩個代理指標：
- `test_new_templates.py` 驗 `pdf[:4] == b"%PDF"` 與頁數（M9a 爆頁教訓）
- 其他測試驗**原始碼字串**（模板檔裡有沒有 `disclaimer-block`）

兩者都看不到「這段文字有沒有真的印在紙上」。實測過：把三款模板的
`if disclaimer != ""` 整條移掉，全套測試依然全綠——PDF 仍是有效 PDF、頁數依然正常，
只是免責聲明沒了。免責是可轉寄 PDF 上最不該漂移的東西，這裡補上唯一會變紅的守門。

兩個測法上的決定：

1. **期望值取自 `pdf.report_disclaimer(locale)` 而非字面複製。**
   它是 M10c 收斂出的兩軌單一真相源；測試複製一份就變成第二個真相源，
   改文案時反而是測試在扯後腿。代價是「函式回空字串」會讓比對失去意義，
   所以另外守住非空與長度下限。
2. **連 WeasyPrint 回退軌一起驗。** 回退存在正是為了應付沒預料到的狀況，
   那恰恰是最不該少免責的時候（`report.render_report_pdf` 的 docstring 也這麼寫）。

F5：內文 `#kpi-strip(...)` 這個呼叫點**從未被任何測試編譯過**。`_split_hero_kpi`
會把**第一組** KPI 提升為跨欄 hero（走 `report()` 參數），只有第二組以後才會 emit
內文 `#kpi-strip`。既有 fixture 一律只放一組 KPI，於是三款模板的內文 KPI 契約
（含 en 才會附上的 `source-label:` 具名引數）全都沒被驗證過。
"""
import io
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services.pdf import report_disclaimer  # noqa: E402
from app.templates import manifest  # noqa: E402

_LOCALES = ("zh-Hant", "en")

_SIMPLE_MD = """# 測試研報

## 執行摘要

先進製程展望穩健[1]。

## 引用來源

[1] 報告：a.pdf
"""

_KPI_A = '{"source":"[1]","items":[{"value":"NT$1,280","label":"目標價","change":"+6.2%","dir":"up"}]}'
_KPI_B = (
    '{"source":"[2]","items":['
    '{"value":"55%","label":"毛利率","change":"-1.0%","dir":"down"},'
    '{"value":"買進","label":"評等"}]}'
)

# 兩組 KPI：第一組被提為 hero，第二組才會走內文 #kpi-strip。
_TWO_KPI_MD = f"""# 測試研報

## 執行摘要

```kpi
{_KPI_A}
```

摘要內文[1]。

## 重點分析

```kpi
{_KPI_B}
```

分析內文[2]。

## 引用來源

[1] 報告：a.pdf
[2] 報告：b.pdf
"""


def _pdf_text(pdf: bytes) -> str:
    """抽出 PDF 全文，去掉所有空白與不可列印字元。

    兩層正規化都是必要的：

    - **去空白**：PDF 抽字會依版面在字間／行尾插入空白與換行，帶著空白比對必然偽陰性。
    - **去控制字元**：字型的 ToUnicode 對應不全時，pypdf 會把該字位還原成 `\\x00`
      並與可對應的字元交錯（UTF-16BE 的高位位元組直接漏出來）。實測 CI 上
      WeasyPrint 那軌的英文免責就長成 `\\x00T\\x00i\\x00n\\x00g...`——文字其實在裡面，
      只是被 `\\x00` 切碎。`str.split()` 不會移除 `\\x00`（它不是空白），所以必須另外濾。
    """
    from pypdf import PdfReader

    pages = PdfReader(io.BytesIO(pdf)).pages
    return _flat("".join(p.extract_text() or "" for p in pages))


def _flat(s: str) -> str:
    """去空白 + 去不可列印字元，兩邊比對前都要過這一層。"""
    return "".join(c for c in s if not c.isspace() and c.isprintable())


def _cjk_extractable() -> bool:
    """本環境能不能從渲染出來的 PDF 抽回中文字。

    不是所有環境都能：沒安裝 CJK 字型時 Typst 會退到不含中文字符對應的字型，
    PDF 照樣編得出來、頁數照樣正常，但抽字全部變成 `\\x00`（CI 實測即如此）。

    這種情況**不能靜默當成通過**，也不該報成「PDF 不含免責」那種會誤導人的訊息，
    所以做成明確的 skip 並在訊息裡指出缺什麼。英文那組不受影響（ASCII 一律抽得回來），
    所以即使中文這組被跳過，「免責被拿掉」依然會被英文那組抓到。
    """
    global _CJK_OK
    if _CJK_OK is None:
        try:
            from app.services.typst_render import render_report_pdf

            pdf = render_report_pdf(
                "# 探針\n\n## 執行摘要\n\n中文可抽字探針。\n",
                title="探針", meta={"date": "2026-07-28", "question": "q"},
            )
            _CJK_OK = "中文可抽字探針" in _pdf_text(pdf)
        except Exception:
            _CJK_OK = False
    return _CJK_OK


_CJK_OK: bool | None = None
_NO_CJK = "本環境無法從 PDF 抽回中文（缺 CJK 字型，例如 fonts-noto-cjk）"


class DisclaimerSourceSanityTests(unittest.TestCase):
    """比對前先確認期望值本身有意義——否則 `"" in text` 永遠成立。"""

    def test_disclaimer_non_trivial_in_both_locales(self):
        for loc in _LOCALES:
            with self.subTest(locale=loc):
                d = report_disclaimer(loc)
                self.assertGreater(len(d), 60, f"{loc} 免責過短，比對失去意義")

    def test_two_locales_are_actually_different(self):
        """兩語系必須真的不同，跨語言洩漏檢查才有鑑別力。"""
        self.assertNotEqual(report_disclaimer("zh-Hant"), report_disclaimer("en"))


class TypstRenderedDisclaimerTests(unittest.TestCase):
    """三款登錄模板 × 兩語系，逐一編譯後抽字比對。慢但無可替代。"""

    def test_every_template_and_locale_prints_disclaimer(self):
        from app.services.typst_render import render_report_pdf

        for spec in manifest.list_templates():
            for loc in _LOCALES:
                with self.subTest(template=spec.id, locale=loc):
                    if loc != "en" and not _cjk_extractable():
                        self.skipTest(_NO_CJK)
                    pdf = render_report_pdf(
                        _SIMPLE_MD, title="測試研報",
                        meta={"date": "2026-07-28", "question": "台積電"},
                        template_id=spec.id, locale=loc,
                    )
                    text = _pdf_text(pdf)
                    self.assertIn(
                        _flat(report_disclaimer(loc)), text,
                        f"{spec.id}/{loc} 渲染後的 PDF 不含免責聲明",
                    )

    def test_no_cross_locale_disclaimer_leak(self):
        """英文報告不得印出中文免責，反之亦然。

        M9b 換皮那次的生產對照實驗就是這麼漏的：locale 沒被沿用，
        英文研報一重出，品牌與免責整段變回中文。
        """
        from app.services.typst_render import render_report_pdf

        other = {"zh-Hant": "en", "en": "zh-Hant"}
        for spec in manifest.list_templates():
            for loc in _LOCALES:
                with self.subTest(template=spec.id, locale=loc):
                    # 抽不回中文時「中文沒洩漏」是恆真的，斷言失去意義。
                    if not _cjk_extractable():
                        self.skipTest(_NO_CJK)
                    pdf = render_report_pdf(
                        _SIMPLE_MD, title="測試研報",
                        meta={"date": "2026-07-28", "question": "台積電"},
                        template_id=spec.id, locale=loc,
                    )
                    text = _pdf_text(pdf)
                    self.assertNotIn(
                        _flat(report_disclaimer(other[loc])), text,
                        f"{spec.id}/{loc} 印出了另一語系的免責",
                    )


class WeasyprintFallbackDisclaimerTests(unittest.TestCase):
    """回退軌同樣必須有免責——那是最不該少的時候。"""

    def test_fallback_track_prints_disclaimer(self):
        try:
            from app.services.pdf import render_report_pdf as weasy_render
        except Exception as exc:  # pragma: no cover - 環境相依
            self.skipTest(f"WeasyPrint 不可用：{exc}")

        for loc in _LOCALES:
            with self.subTest(locale=loc):
                if loc != "en" and not _cjk_extractable():
                    self.skipTest(_NO_CJK)
                pdf = weasy_render(
                    _SIMPLE_MD, title="測試研報",
                    meta={"date": "2026-07-28", "question": "台積電"}, locale=loc,
                )
                self.assertEqual(pdf[:4], b"%PDF")
                self.assertIn(_flat(report_disclaimer(loc)), _pdf_text(pdf))


class InlineKpiStripTests(unittest.TestCase):
    """F5：第二組以後的 KPI 走內文 `#kpi-strip`，此路徑先前零編譯。"""

    def test_second_kpi_block_emits_inline_call(self):
        """先確認 fixture 真的踩到那條路徑，再談編譯成不成功。

        若哪天 `_split_hero_kpi` 改成全部 KPI 都提升為 hero，這條會先變紅，
        提醒下面的編譯測試已經名存實亡。
        """
        from app.services.typst_render import build_document, emit_typst

        doc = build_document(
            _TWO_KPI_MD, title="測試研報",
            meta={"date": "2026-07-28", "question": "台積電"},
        )
        src = emit_typst(doc, disclaimer="D")
        self.assertEqual(
            src.count("#kpi-strip("), 1,
            "fixture 未產生內文 #kpi-strip：第一組應被提為 hero、第二組留在內文",
        )

    def test_en_inline_kpi_passes_source_label(self):
        """en 才會附上 `source-label:` 具名引數——模板必須接得住。"""
        from app.services.typst_render import build_document, emit_typst

        doc = build_document(
            _TWO_KPI_MD, title="測試研報",
            meta={"date": "2026-07-28", "question": "台積電"},
        )
        src = emit_typst(doc, disclaimer="D", locale="en")
        self.assertIn("source-label:", src)

    def test_every_template_compiles_multi_kpi_document(self):
        from app.services.typst_render import render_report_pdf

        for spec in manifest.list_templates():
            for loc in _LOCALES:
                with self.subTest(template=spec.id, locale=loc):
                    pdf = render_report_pdf(
                        _TWO_KPI_MD, title="測試研報",
                        meta={"date": "2026-07-28", "question": "台積電"},
                        template_id=spec.id, locale=loc,
                    )
                    self.assertEqual(pdf[:4], b"%PDF", spec.id)
                    text = _pdf_text(pdf)
                    # hero（第一組）與內文（第二組）都要印出來。
                    # **主斷言用 ASCII 的數值而非中文標籤**：數值在任何字型環境下都
                    # 抽得回來，中文標籤在缺 CJK 字型的環境會整批變 \x00（CI 實測）。
                    # 「內文那組有沒有被渲染出來」才是這個測試要問的問題，用值問就夠。
                    self.assertIn("NT$1,280", text, f"{spec.id}/{loc} 缺 hero KPI")
                    self.assertIn("55%", text, f"{spec.id}/{loc} 缺內文 KPI")
                    if _cjk_extractable():
                        self.assertIn("目標價", text, f"{spec.id}/{loc} 缺 hero KPI 標籤")
                        self.assertIn("毛利率", text, f"{spec.id}/{loc} 缺內文 KPI 標籤")
                    # 版面守門沿用 M9a 訊號：短報告不該爆頁
                    from pypdf import PdfReader

                    pages = len(PdfReader(io.BytesIO(pdf)).pages)
                    self.assertLessEqual(pages, 4, f"{spec.id}/{loc} 爆頁：{pages} 頁")


if __name__ == "__main__":
    unittest.main()
