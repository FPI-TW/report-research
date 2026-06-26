"""把研報 markdown 渲染成帶『廷豐智能研報』品牌的 PDF。markdown→HTML→WeasyPrint。

CJK：WeasyPrint 透過系統 fontconfig 取字型，部署機需裝 Noto Sans CJK，否則中文變空白方塊。
本模組為純函式（輸入 markdown+meta，輸出 PDF bytes），可冒煙測。
"""

from __future__ import annotations

import html as _html

import markdown as _md
from weasyprint import HTML

BRAND_NAME = "廷豐智能研報"
BRAND_GOLD = "#AE7415"

_PAGE_CSS = """
@page {
  size: A4;
  margin: 22mm 18mm 20mm 18mm;
  @bottom-center { content: counter(page) " / " counter(pages); font-size: 9pt; color: #888; }
}
body { font-family: "Noto Sans CJK TC", "Noto Sans CJK SC", "Noto Sans TC", sans-serif;
       font-size: 11pt; line-height: 1.7; color: #222; }
.brand-bar { border-bottom: 2px solid %(gold)s; padding-bottom: 8px; margin-bottom: 18px; }
.brand-name { color: %(gold)s; font-size: 13pt; font-weight: 700; letter-spacing: 1px; }
.brand-meta { color: #888; font-size: 9pt; margin-top: 2px; }
h1 { font-size: 19pt; color: #1a1a1a; margin: 4px 0 14px; }
h2 { font-size: 14pt; color: %(gold)s; border-left: 4px solid %(gold)s; padding-left: 8px; margin: 20px 0 8px; }
h3 { font-size: 12pt; color: #333; margin: 14px 0 6px; }
table { border-collapse: collapse; width: 100%%; margin: 8px 0; }
th, td { border: 1px solid #ddd; padding: 5px 8px; font-size: 10pt; }
th { background: #faf3e6; }
code { background: #f5f5f5; padding: 1px 4px; border-radius: 3px; font-size: 10pt; }
a { color: %(gold)s; text-decoration: none; }
""" % {"gold": BRAND_GOLD}


def _document_html(title: str, body_html: str, meta: dict) -> str:
    date = _html.escape(str(meta.get("date") or ""))
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<style>{_PAGE_CSS}</style></head><body>"
        "<div class='brand-bar'>"
        f"<div class='brand-name'>{_html.escape(BRAND_NAME)}</div>"
        f"<div class='brand-meta'>研究報告　生成日期 {date}</div>"
        "</div>"
        f"{body_html}"
        "</body></html>"
    )


def render_report_pdf(markdown_text: str, *, title: str, meta: dict) -> bytes:
    """markdown → HTML → WeasyPrint PDF。回 PDF bytes（以 b'%PDF' 開頭）。"""
    body_html = _md.markdown(
        markdown_text or "",
        extensions=["tables", "fenced_code", "sane_lists"],
    )
    doc = _document_html(title, body_html, meta or {})
    return HTML(string=doc).write_pdf()
