# tests/test_new_templates.py
"""M9b-3 新模板 broker-modern / privatebank-dark（與 ib-classic 同契約）。

驗每款登錄模板：(a) .typ 匯出契約的 4 個函式；(b) 經 render_report_pdf 真的編出有效
PDF 且頁數合理（M9a 教訓：rect(height:100%) 會讓 1 頁報告爆成 6 頁空白且不報錯——
PDF bytes 不能當驗收，頁數才是訊號）。
"""
import io
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.templates import manifest  # noqa: E402

_CONTRACT = ("report", "section-heading", "kpi-strip", "chart-figure")

_GOOD_CHART = '{"type":"bar","x":["Q1","Q2"],"series":[{"name":"營收","values":[1,2]}]}'
_GOOD_KPI = (
    '{"source":"[1]","items":['
    '{"value":"NT$1,280","label":"目標價","change":"+6.2%","dir":"up"},'
    '{"value":"買進","label":"評等","change":"","dir":"flat"}]}'
)

_MD = f"""# 台積電 深度研報

## 執行摘要

```kpi
{_GOOD_KPI}
```

本報告評估先進製程展望。營收年增 30%[1]，毛利率 55%[1]。

## 重點分析

### 先進製程

2nm 量產進度符合預期，CoWoS 產能持續擴充[1]。這是一段刻意較長的內文，用來測試單欄
與雙欄在各模板下的排版：換行、對齊、行距都應正常，不得爆頁或壓縮。

```chart
{_GOOD_CHART}
```

## 風險與展望

地緣風險與資本支出是主要變數[1]。

## 引用來源

[1] 報告：tsmc.pdf
"""


class TemplateContractTests(unittest.TestCase):
    def test_registered_templates_export_contract_functions(self):
        for spec in manifest.list_templates():
            src = (REPO_ROOT / "app" / "templates" / spec.filename).read_text(
                encoding="utf-8"
            )
            for fn in _CONTRACT:
                self.assertIn(f"#let {fn}", src, f"{spec.filename} 缺 {fn}")

    def test_no_rect_height_100_percent(self):
        # M9a 爆頁地雷：禁 rect(height: 100%)。掃所有登錄模板。
        for spec in manifest.list_templates():
            src = (REPO_ROOT / "app" / "templates" / spec.filename).read_text(
                encoding="utf-8"
            )
            # 允許註解裡提到（說明為何禁）；只抓實際的 rect(.. height: 100% ..)
            code = "\n".join(
                ln for ln in src.splitlines() if not ln.strip().startswith("//")
            )
            self.assertNotIn("height: 100%", code, f"{spec.filename} 用了 height: 100%")


class TemplateRenderTests(unittest.TestCase):
    """真 typst 編譯（慢但必要）。頁數是版面正確與否的訊號。"""

    def _pages(self, pdf: bytes) -> int:
        from pypdf import PdfReader

        return len(PdfReader(io.BytesIO(pdf)).pages)

    def test_each_template_renders_valid_bounded_pdf(self):
        from app.services.typst_render import render_report_pdf

        for spec in manifest.list_templates():
            with self.subTest(template=spec.id):
                pdf = render_report_pdf(
                    _MD, title="台積電 深度研報",
                    meta={"date": "2026-07-23", "question": "台積電"},
                    template_id=spec.id,
                )
                self.assertEqual(pdf[:4], b"%PDF", spec.id)
                pages = self._pages(pdf)
                # 這份短報告應為 1–3 頁；≥6 頁＝踩到 rect(height:100%) 爆頁
                self.assertGreaterEqual(pages, 1, spec.id)
                self.assertLessEqual(pages, 4, f"{spec.id} 爆頁：{pages} 頁")


if __name__ == "__main__":
    unittest.main()
