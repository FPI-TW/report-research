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
_KPI_RE = re.compile(r"```kpi\s*\n(.*?)\n```", re.DOTALL)
_CITE_RE = re.compile(r"\[(\d+(?:\s*[,，、]\s*\d+)*)\]")
_REF_NL_RE = re.compile(r"\n+(\[\d+\])")
_TITLE_RE = re.compile(r"(?m)^#\s+(.+)$")

BRAND_NAME = "廷豐智能研報"
BRAND_GOLD = "#AE7415"
_GOLD_SOFT = "#faf6ee"
_GOLD_LINE = "#ecdcc0"

# 章節名 → slug（決定樣式）；未知名 → "sec" 預設樣式，安全降級
_SECT_SLUG = {
    "執行摘要": "exec",
    "關鍵發現": "findings",
    "重點分析": "analysis",
    "風險與展望": "outlook",
    "引用來源": "refs",
    "外部參考（網路）": "extrefs",
}
_NO_CITE_SLUGS = {"refs", "extrefs"}
_FANCY_MIN_SECTIONS = 3

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
        '<!doctype html><html><head><meta charset="utf-8">'
        f"<style>{_PAGE_CSS}</style></head><body>"
        '<div class="brand-bar">'
        f'<div class="brand-name">{_html.escape(BRAND_NAME)}</div>'
        f'<div class="brand-meta">研究報告　生成日期 {date}</div>'
        "</div>"
        f"{body_html}"
        "</body></html>"
    )


_FANCY_CSS = (
    """
@page { size: A4; margin: 20mm 18mm 18mm 18mm;
  @bottom-center { content: "$BRAND$　·　" counter(page) " / " counter(pages);
    font-size: 8.5pt; color: #aaa; } }
@page:first { margin: 0; @bottom-center { content: none; } }
body { font-family: "Noto Sans CJK TC","Noto Sans CJK SC","Noto Sans TC",sans-serif;
  color: #222; font-size: 10.5pt; line-height: 1.75; }

/* 封面 */
.cover { page-break-after: always; height: 100vh; padding: 40mm 24mm;
  box-sizing: border-box; position: relative;
  background: linear-gradient(180deg,#fffdf9 0%,#fbf4e8 100%); }
.cover-brand { color: $GOLD$; font-size: 15pt; font-weight: 700; letter-spacing: 2px; }
.cover-rule { height: 3px; width: 56px; background: $GOLD$; margin: 10px 0 0; }
.cover-mid { position: absolute; top: 42%; left: 24mm; right: 24mm; }
.cover-kicker { color: $GOLD$; font-size: 11pt; letter-spacing: 4px; margin-bottom: 10px; }
.cover-title { font-size: 30pt; line-height: 1.3; color: #1a1a1a; margin: 0; font-weight: 700; }
.cover-date { color: #888; font-size: 11pt; margin-top: 18px; }
.cover-foot { position: absolute; bottom: 26mm; left: 24mm; color: #b9a06f;
  font-size: 9pt; letter-spacing: 1px; }

/* 目錄 */
.toc { page-break-after: always; padding-top: 6mm; }
.toc-h { color: $GOLD$; font-size: 16pt; font-weight: 700; border-bottom: 2px solid $GOLD$;
  padding-bottom: 6px; margin-bottom: 14px; }
.toc ol { list-style: none; counter-reset: toc; padding: 0; }
.toc li { counter-increment: toc; margin: 9px 0; font-size: 11.5pt; }
.toc a { color: #333; text-decoration: none; }
.toc a::before { content: counter(toc,decimal-leading-zero) "　"; color: $GOLD$; font-weight: 700; }
.toc a::after { content: leader('·') target-counter(attr(href), page); color: #aaa; }

/* 章節 */
section[id] { margin-top: 16px; }
h2 { font-size: 14.5pt; color: $GOLD$; margin: 18px 0 10px; padding-bottom: 4px;
  border-bottom: 1.5px solid $LINE$; }
.s-body p { margin: 7px 0; text-align: justify; }
h3 { font-size: 11.5pt; color: #333; margin: 12px 0 5px; }
a { color: $GOLD$; text-decoration: none; }
table { border-collapse: collapse; width: 100%; margin: 8px 0; }
th,td { border: 1px solid #ddd; padding: 5px 8px; font-size: 9.5pt; }
th { background: $SOFT$; }

/* 執行摘要：淡金底色框 */
.s-exec .s-body { background: $SOFT$; border: 1px solid $LINE$;
  border-left: 4px solid $GOLD$; border-radius: 8px; padding: 12px 16px; }

/* 關鍵發現：卡片＋編號徽章 */
.s-findings ol { list-style: none; counter-reset: f; padding: 0; }
.s-findings li { counter-increment: f; position: relative; background: #fff;
  border: 1px solid $LINE$; border-radius: 8px; padding: 11px 14px 11px 46px;
  margin: 9px 0; box-shadow: 0 1px 0 rgba(0,0,0,0.03); page-break-inside: avoid; }
.s-findings li::before { content: counter(f); position: absolute; left: 12px; top: 11px;
  width: 24px; height: 24px; background: $GOLD$; color: #fff; border-radius: 50%;
  font-size: 11pt; font-weight: 700; text-align: center; line-height: 24px; }

/* 圖表 */
figure.chart { margin: 14px 0; text-align: center; page-break-inside: avoid; }
figure.chart svg { max-width: 100%; height: auto; }
figcaption { font-size: 9pt; color: #888; margin-top: 4px; }

/* 引用來源 */
.s-refs .s-body p { font-size: 9.5pt; color: #555; margin: 4px 0;
  padding-left: 10px; border-left: 2px solid $LINE$; }

/* 子標題層次（覆蓋既有 h3） */
h3 { font-size: 12pt; color: #9c6a16; font-weight: 700; margin: 15px 0 6px; }
h4 { font-size: 10.5pt; color: #555; font-weight: 700; margin: 11px 0 4px; }

/* 引用標號：上標金色小徽章 */
sup.cite { color: $GOLD$; font-size: 0.68em; font-weight: 700;
  vertical-align: super; padding: 0 0.5px; letter-spacing: 0.5px; }

/* 重點引言 callout */
.s-body blockquote { margin: 11px 0; padding: 10px 15px; background: $SOFT$;
  border-left: 4px solid $GOLD$; border-radius: 0 7px 7px 0; color: #4a4138; }
.s-body blockquote p { margin: 3px 0; font-size: 10.5pt; }

/* 數據亮點卡片 */
.kpi-strip { display: table; width: 100%; border-spacing: 8px 0; margin: 14px 0;
  table-layout: fixed; }
.kpi { display: table-cell; background: #fffdf9; border: 1px solid $LINE$;
  border-top: 3px solid $GOLD$; border-radius: 9px; padding: 11px 8px;
  text-align: center; page-break-inside: avoid; vertical-align: top; }
.kpi-value { font-size: 18pt; font-weight: 700; color: #1a1a1a; line-height: 1.15; }
.kpi-label { font-size: 8.5pt; color: #888; margin-top: 4px; line-height: 1.3; }
.kpi-change { font-size: 8.5pt; margin-top: 3px; font-weight: 700; }
.kpi-change.up { color: #4f9268; }
.kpi-change.down { color: #b5573f; }
.kpi-src { font-size: 8pt; color: #aaa; text-align: right; margin: 2px 4px 0; }

/* 表格美化（覆蓋既有 table 規則） */
table { border-collapse: collapse; width: 100%; margin: 11px 0; }
thead th { background: $GOLD$; color: #fff; font-weight: 700; font-size: 9.5pt; }
tbody tr:nth-child(even) { background: $SOFT$; }
td, th { border: 1px solid $LINE$; padding: 6px 10px; font-size: 9.5pt; }
tbody td:first-child { font-weight: 700; color: #333; }

/* 引用來源：懸掛縮排（覆蓋既有 .s-refs .s-body p） */
.s-refs .s-body p { padding-left: 1.9em; text-indent: -1.9em; border-left: none;
  font-size: 9.5pt; color: #555; margin: 5px 0; }

/* 外部參考：清單分層 */
.s-extrefs .s-body ul { list-style: none; padding: 0; }
.s-extrefs .s-body li { padding: 5px 0 5px 14px; border-left: 2px solid $LINE$;
  margin: 6px 0; }
.s-extrefs .s-body a { font-size: 10pt; }
"""
    .replace("$BRAND$", BRAND_NAME)
    .replace("$GOLD$", BRAND_GOLD)
    .replace("$SOFT$", _GOLD_SOFT)
    .replace("$LINE$", _GOLD_LINE)
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


def strip_preamble(markdown_text: str) -> str:
    """丟棄第一個 `# 標題` 之前的所有文字（流程旁白）；無 `# ` 標題則原樣回傳。

    用於串流組裝後的根因去旁白：持久化與全文檢視都拿到乾淨 markdown，不僅 PDF。
    無標題時（如「找不到相關資料」）無可靠錨點，原樣回傳由 prompt 規則把關。
    """
    text = markdown_text or ""
    m = _TITLE_RE.search(text)
    return text[m.start():] if m else text


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


def inject_kpi(markdown_text: str) -> str:
    """把 ```kpi 區塊換成一排數據亮點卡片；壞 JSON 或 items 空則移除＋warning。"""

    def _repl(m: "re.Match[str]") -> str:
        try:
            spec = json.loads(m.group(1))
            items = spec.get("items") or []
        except (ValueError, TypeError):
            logger.warning("kpi spec JSON 解析失敗，略過")
            return ""
        if not items:
            logger.warning("kpi 無 items，略過")
            return ""
        cells = []
        for it in items:
            val = _html.escape(str(it.get("value", "")))
            lab = _html.escape(str(it.get("label", "")))
            chg = str(it.get("change", "")).strip()
            direction = str(it.get("dir", "")).strip()
            chg_html = (
                f'<div class="kpi-change {_html.escape(direction)}">{_html.escape(chg)}</div>'
                if chg
                else ""
            )
            cells.append(
                f'<div class="kpi"><div class="kpi-value">{val}</div>'
                f'<div class="kpi-label">{lab}</div>{chg_html}</div>'
            )
        src = str(spec.get("source", "")).strip()
        src_html = (
            f'<div class="kpi-src">來源 {_html.escape(src)}</div>' if src else ""
        )
        return f'\n\n<div class="kpi-strip">{"".join(cells)}</div>{src_html}\n\n'

    return _KPI_RE.sub(_repl, markdown_text or "")


def cite_badges(html: str) -> str:
    """把內文的引用標號 [n]、[n,m] 換成上標金色小徽章（不動其他括號）。"""
    return _CITE_RE.sub(lambda m: f'<sup class="cite">{m.group(1)}</sup>', html or "")


def _normalize_refs(body: str) -> str:
    """引用來源：確保每個 [n] 起新段落，不靠模型空行也能一條一行。"""
    return _REF_NL_RE.sub(r"\n\n\1", (body or "").strip())


def _render_fancy(title: str, sections: list[tuple[str, str]], meta: dict) -> str:
    date = _html.escape(str(meta.get("date") or ""))
    cover = (
        '<section class="cover">'
        f'<div class="cover-brand">{_html.escape(BRAND_NAME)}</div>'
        '<div class="cover-rule"></div>'
        '<div class="cover-mid">'
        '<div class="cover-kicker">深度研究報告</div>'
        f'<h1 class="cover-title">{_html.escape(title)}</h1>'
        f'<div class="cover-date">生成日期 {date}</div>'
        "</div>"
        f'<div class="cover-foot">{_html.escape(BRAND_NAME)}　·　AI 輔助研究分析</div>'
        "</section>"
    )
    toc_items = "".join(
        f'<li><a href="#sec-{i}">{_html.escape(name)}</a></li>'
        for i, (name, _) in enumerate(sections)
    )
    toc = f'<nav class="toc"><div class="toc-h">目錄</div><ol>{toc_items}</ol></nav>'
    body_parts = []
    for i, (name, body) in enumerate(sections):
        slug = _SECT_SLUG.get(name, "sec")
        prepared = inject_kpi(inject_charts(body))
        if slug == "refs":
            prepared = _normalize_refs(prepared)
        body_html = _md.markdown(
            prepared, extensions=["tables", "fenced_code", "sane_lists"]
        )
        if slug not in _NO_CITE_SLUGS:
            body_html = cite_badges(body_html)
        body_parts.append(
            f'<section class="s-{slug}" id="sec-{i}">'
            f"<h2>{_html.escape(name)}</h2>"
            f'<div class="s-body">{body_html}</div>'
            "</section>"
        )
    body = "".join(body_parts)
    return (
        '<!doctype html><html><head><meta charset="utf-8">'
        f"<style>{_FANCY_CSS}</style></head><body>{cover}{toc}{body}</body></html>"
    )


def _build_document(markdown_text: str, *, title: str, meta: dict) -> str:
    """依內容選版型：完整研報走 fancy（封面/目錄/章節），退化輸入回退簡版。回完整 HTML。"""
    md = markdown_text or ""
    parsed_title, sections = split_report(md)
    if parsed_title and len(sections) >= _FANCY_MIN_SECTIONS:
        return _render_fancy(parsed_title, sections, meta or {})
    prepared = inject_charts(md)
    body_html = _md.markdown(prepared, extensions=["tables", "fenced_code", "sane_lists"])
    return _document_html(title, body_html, meta or {})


def render_report_pdf(markdown_text: str, *, title: str, meta: dict) -> bytes:
    """markdown → HTML → WeasyPrint PDF。回 PDF bytes（以 b'%PDF' 開頭）。"""
    doc = _build_document(markdown_text, title=title, meta=meta or {})
    # 延遲 import：weasyprint 載入重（cffi/pango/fontconfig ~3s），不在模組頂層匯入，
    # 以免 `import web.server`（經 report→pdf）開機就吃這秒數（會拖垮匯入逾時測試）。
    from weasyprint import HTML

    return HTML(string=doc).write_pdf()
