"""把研報 markdown 渲染成帶『廷豐智能研報』品牌的 PDF。markdown→HTML→WeasyPrint。

CJK：WeasyPrint 透過系統 fontconfig 取字型，部署機需裝 Noto Sans CJK，否則中文變空白方塊。
本模組為純函式（輸入 markdown+meta，輸出 PDF bytes），可冒煙測。
"""

from __future__ import annotations

import html as _html
import json
import logging
import re

import markdown as _md

from app.services.chart import render_chart_svg

logger = logging.getLogger(__name__)

_CHART_RE = re.compile(r"```chart\s*\n(.*?)\n```", re.DOTALL)
_TITLE_RE = re.compile(r"(?m)^#\s+(.+)$")

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
figure.chart { margin: 14px 0; text-align: center; page-break-inside: avoid; }
figure.chart svg { max-width: 100%%; height: auto; }
figcaption { font-size: 9pt; color: #888; margin-top: 4px; }
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


def split_report(markdown_text: str) -> tuple[str, list[tuple[str, str]]]:
    """丟棄第一個 `# 標題` 之前的所有文字（含流程旁白），再依 `## ` 切章節。

    回 (title, [(section_name, body_md), …])。無 `# ` 標題回 ("", [])，交由
    呼叫端走簡版。標題與第一個 `## ` 間的遊離前言一併丟棄。
    """
    text = markdown_text or ""
    m = _TITLE_RE.search(text)
    if not m:
        return "", []
    title = m.group(1).strip()
    rest = text[m.end():]
    parts = re.split(r"(?m)^##\s+", rest)
    sections: list[tuple[str, str]] = []
    for p in parts[1:]:  # parts[0] = 標題與首個 ## 間（前言）→ 丟
        nl = p.find("\n")
        name = (p[:nl] if nl >= 0 else p).strip()
        body = (p[nl + 1:] if nl >= 0 else "").strip()
        sections.append((name, body))
    return title, sections


def inject_charts(markdown_text: str) -> str:
    """把 markdown 內的 ```chart 區塊換成 <figure><svg>…</figure>；壞規格/數據缺則移除該塊。"""

    def _repl(m: "re.Match[str]") -> str:
        try:
            spec = json.loads(m.group(1))
        except (ValueError, TypeError):
            logger.warning("chart spec JSON 解析失敗，略過")
            return ""
        svg = render_chart_svg(spec)
        if not svg:
            logger.warning("chart 規格無效或數據缺，略過")
            return ""
        title = _html.escape(str(spec.get("title") or ""))
        src = str(spec.get("source") or "").strip()
        cap = f"{title}（來源 {_html.escape(src)}）" if (title and src) else title
        figcap = f"<figcaption>{cap}</figcaption>" if cap else ""
        return f'\n\n<figure class="chart">{svg}{figcap}</figure>\n\n'

    return _CHART_RE.sub(_repl, markdown_text or "")


def render_report_pdf(markdown_text: str, *, title: str, meta: dict) -> bytes:
    """markdown → HTML → WeasyPrint PDF。回 PDF bytes（以 b'%PDF' 開頭）。"""
    prepared = inject_charts(markdown_text or "")
    body_html = _md.markdown(
        prepared,
        extensions=["tables", "fenced_code", "sane_lists"],
    )
    doc = _document_html(title, body_html, meta or {})
    # 延遲 import：weasyprint 載入重（cffi/pango/fontconfig ~3s），不在模組頂層匯入，
    # 以免 `import web.server`（經 report→pdf）開機就吃這秒數（會拖垮匯入逾時測試）。
    from weasyprint import HTML

    return HTML(string=doc).write_pdf()
