"""把研報 markdown 渲染成帶『廷豐智能研報』品牌的 PDF。markdown→HTML→WeasyPrint。

CJK：WeasyPrint 透過系統 fontconfig 取字型，部署機需裝 Noto Sans CJK，否則中文變空白方塊。
本模組為純函式（輸入 markdown+meta，輸出 PDF bytes），可冒煙測。
"""

from __future__ import annotations

import html as _html
import json
import logging
import re
import unicodedata
from html.parser import HTMLParser

import markdown as _md

from app.services.chart import render_chart_svg
from app.services.locale import DEFAULT_LOCALE

logger = logging.getLogger(__name__)

_CHART_RE = re.compile(r"```chart\s*\n(.*?)\n```", re.DOTALL)
_KPI_RE = re.compile(r"```kpi\s*\n(.*?)\n```", re.DOTALL)
_CITE_RE = re.compile(r"\[(\d+(?:\s*[,，、]\s*\d+)*)\]")
_REF_NL_RE = re.compile(r"\n+(\[\d+\])")
_TITLE_RE = re.compile(r"(?m)^#\s+(.+)$")

BRAND_NAME = "廷豐智能研報"
BRAND_NAME_EN = "Tingfeng Intelligent Research"


def brand_name(locale: str = DEFAULT_LOCALE) -> str:
    """研報品牌名（輸出隨 locale；非 en 一律中文）。"""
    return BRAND_NAME_EN if locale == "en" else BRAND_NAME

# 研報 PDF 的免責聲明。**兩條渲染路徑（WeasyPrint / Typst）的唯一文字來源**——
# 各自複製一份必然漂移。深度研報是可下載、可轉發的檔案：離開平台後沒有任何上下文，
# 收到的人只看到一份看起來像券商研報、內含目標價與評等彙整的文件。
#
# 免責**不得依賴 LLM 產出**（漏寫或內容截斷都會讓它消失），也不得依賴任何資料是否存在
# ——雷達改版時免責就是這樣無聲消失的（PR #87 cc054c3：plan 以為它「已改置底 notes」，
# 但那區只放資料品質註記且常為空陣列，整區不渲染）。
REPORT_DISCLAIMER = (
    "免責聲明：本報告由「廷豐智能研報」依語料庫中已擷取之券商研報觀點與數值自動彙整生成，"
    "非系統預測，亦不構成投資建議或要約。所引用之評等、目標價與財務預估均為原研報作者之觀點，"
    "其正確性與時效性以原始研報為準。投資人應自行判斷並承擔投資風險。"
)
REPORT_DISCLAIMER_EN = (
    "Disclaimer: This report is automatically compiled by \"Tingfeng Intelligent "
    "Research\" from broker research views and figures already extracted into the "
    "corpus. It is not a system forecast and does not constitute investment advice or "
    "an offer. The ratings, target prices, and financial estimates cited are the views "
    "of the original report authors; their accuracy and timeliness are subject to the "
    "original reports. Investors should exercise their own judgement and bear their own "
    "investment risk."
)


def report_disclaimer(locale: str = DEFAULT_LOCALE) -> str:
    """研報免責聲明（兩軌唯一文字來源，輸出隨 locale；非 en 一律中文）。"""
    return REPORT_DISCLAIMER_EN if locale == "en" else REPORT_DISCLAIMER


def _disclaimer_html(locale: str = DEFAULT_LOCALE) -> str:
    return (
        '<div class="tf-disclaimer">'
        f"{_html.escape(report_disclaimer(locale))}"
        "</div>"
    )


_DISCLAIMER_CSS = (
    ".tf-disclaimer{margin-top:18px;padding-top:10px;border-top:1px solid #e7e4dc;"
    "font-size:8.5pt;line-height:1.6;color:#6e7e89;}"
)
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
    # M10c：英文研報骨架標題（與 report_writer.SKELETON_HEADINGS_EN 對齊）
    "Executive Summary": "exec",
    "Key Findings": "findings",
    "In-Depth Analysis": "analysis",
    "Risks & Outlook": "outlook",
    "References": "refs",
    "External References (Web)": "extrefs",
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


def _document_html(title: str, body_html: str, meta: dict, locale: str = DEFAULT_LOCALE) -> str:
    date = _html.escape(str(meta.get("date") or ""))
    meta_line = f"Research Report · Generated {date}" if locale == "en" else f"研究報告　生成日期 {date}"
    return (
        '<!doctype html><html><head><meta charset="utf-8">'
        f"<style>{_PAGE_CSS}{_DISCLAIMER_CSS}</style></head><body>"
        '<div class="brand-bar">'
        f'<div class="brand-name">{_html.escape(brand_name(locale))}</div>'
        f'<div class="brand-meta">{meta_line}</div>'
        "</div>"
        f"{body_html}"
        f"{_disclaimer_html(locale)}"
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

/* 引用來源：懸掛縮排 */
.s-refs .s-body p { padding-left: 1.9em; text-indent: -1.9em; border-left: none;
  font-size: 9.5pt; color: #555; margin: 5px 0; }

/* 外部參考：清單分層 */
.s-extrefs .s-body ul { list-style: none; padding: 0; }
.s-extrefs .s-body li { padding: 5px 0 5px 14px; border-left: 2px solid $LINE$;
  margin: 6px 0; }
.s-extrefs .s-body a { font-size: 10pt; }
"""
    # $BRAND$ 保留為 placeholder，於 _render_fancy 依 locale 替換（頁尾品牌名）
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


# ── 巨型段落切分（兩軌共用，與 strip_preamble 同層：LLM markdown 的結構正規化）──
#
# 為什麼在這裡而不是在 prompt 裡：四份研報 prompt（單次 zh/en、逐節 zh/en）是手抄的
# 平行副本，加規則要改四處且漏一處就機率性失守（2026-07-28 曾有一節 57.7% 語言跑掉）。
# 段落長度是**決定性的排版問題**，用決定性的程式解，不靠模型配合。
#
# 門檻用「顯示寬度」而非句數：實測樣張有 4 句、2765 寬度單位（約 59 行）的段落——句數
# 少不代表段落短。A4 雙欄單欄約 248pt、正文 10.5pt ⇒ 每行約 47 個寬度單位（西文字元
# 算 1、CJK 算 2）。900 ≈ 19 行（開始難讀），目標 600 ≈ 13 行。
_PARA_SPLIT_WIDTH = 900
_PARA_TARGET_WIDTH = 600
# 句界必須分中西兩支，**因為中文句子之間沒有空白**：
# - CJK：`。！？` 之後（＋可選收尾引號）即為句界，不得要求空白，否則整段永遠只有一句
#   （初版統一要求 `\s+`，中文段落因此完全切不動——測試 test_cjk_width_counted_double
#   就是釘這件事）。
# - 西文：要求 `\s+` 且下一句以大寫/引號開頭，才不會把 "Fig. 1" 切開；小數點
#   （"US$18.4 billion"）後面沒有空白，天然排除。
_RE_SENT_BOUNDARY = re.compile(
    r"(?<=[。！？])(?:[”’」』）】]*)(?![。！？])"
    r"|(?<=[.!?])(?:[\"'”’)\]]*)\s+(?=[A-Z“\"（(\[])"
)
# 這些縮寫後面的句點不是句末（切在這裡會產生半句）
_ABBREV = frozenset(
    "u.s. e.g. i.e. vs. no. inc. ltd. co. corp. fig. approx. etc. dr. mr. ms. "
    "jan. feb. mar. apr. jun. jul. aug. sep. sept. oct. nov. dec.".split()
)
# 非散文區塊的行首標記（清單／標題／引用／表格／圍欄）——整塊跳過，不冒險
_RE_NON_PROSE = re.compile(r"^\s*(#{1,6}\s|[-*+]\s|\d+[.)]\s|>|\||```|~~~|\[\d+\])")


def _display_width(s: str) -> int:
    """粗估排版寬度：CJK/全角算 2，其餘算 1。不用 len()——中英混排差兩倍。"""
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def _sentences(para: str) -> list[str]:
    """段落 → 句子列表（保留原空白，join 後與輸入等價）。"""
    out: list[str] = []
    last = 0
    for m in _RE_SENT_BOUNDARY.finditer(para):
        head = para[last:m.start()]
        tail_word = re.search(r"(\S+)$", head)
        if tail_word:
            w = tail_word.group(1).lower()
            # 縮寫、單字母首字母（"J. P. Morgan"）→ 不是句界
            if w in _ABBREV or re.fullmatch(r"[a-z]\.", w):
                continue
        out.append(para[last:m.end()])
        last = m.end()
    if last < len(para):
        out.append(para[last:])
    return [s for s in out if s]


def split_long_paragraphs(
    markdown_text: str,
    *,
    max_width: int = _PARA_SPLIT_WIDTH,
    target_width: int = _PARA_TARGET_WIDTH,
) -> str:
    """把過長的散文段落在句界切成數段。冪等（切完每段都低於門檻，再跑不變）。

    只動「純散文段落」：圍欄內容（```kpi / ```chart / 任何 code block）、標題、清單、
    引用、表格、`[n]` 引用行一律原樣保留——寧可漏切，不可切壞結構。切不出兩段以上
    （例如整段只有一個句子）時原樣回傳。
    """
    text = markdown_text or ""
    if not text.strip():
        return text
    out: list[str] = []
    in_fence = False
    fence_marker = ""
    # 以空行分塊，但先把圍欄整段保護起來（圍欄內可能有空行）
    for block in re.split(r"(\n[ \t]*\n)", text):
        if not block or block.strip() == "":
            out.append(block)
            continue
        lines = block.split("\n")
        # 圍欄狀態機：跨塊追蹤（```chart 內部可能有空行）
        if in_fence or any(
            ln.lstrip().startswith(("```", "~~~")) for ln in lines
        ):
            for ln in lines:
                stripped = ln.lstrip()
                if not in_fence and stripped.startswith(("```", "~~~")):
                    in_fence, fence_marker = True, stripped[:3]
                elif in_fence and stripped.startswith(fence_marker):
                    in_fence, fence_marker = False, ""
            out.append(block)
            continue
        if any(_RE_NON_PROSE.match(ln) for ln in lines):
            out.append(block)
            continue
        if _display_width(block) <= max_width:
            out.append(block)
            continue
        sents = _sentences(block)
        if len(sents) < 2:
            out.append(block)  # 一個句子的長段落沒有安全切點
            continue
        chunks: list[str] = []
        cur = ""
        for i, s in enumerate(sents):
            cur += s
            cur_w = _display_width(cur)
            nxt_w = _display_width(sents[i + 1]) if i + 1 < len(sents) else 0
            # 前瞻再決定收段：只看「已達 target」會讓長句把段落撐得很不平均
            # （實測 344/619/1429 三句 → 963|1429 兩段）。若加上下一句會大幅超標，
            # 就在這裡收段，即使目前還沒達到 target。
            if cur_w >= target_width or (
                nxt_w and cur_w >= target_width * 0.5
                and cur_w + nxt_w > target_width * 1.35
            ):
                chunks.append(cur.strip())
                cur = ""
        if cur.strip():
            # 尾段太短就併回上一段，避免產生孤零零一句
            if chunks and _display_width(cur) < target_width * 0.35:
                chunks[-1] = chunks[-1] + " " + cur.strip()
            else:
                chunks.append(cur.strip())
        out.append("\n\n".join(chunks) if len(chunks) > 1 else block)
    return "".join(out)


def chart_caption(spec: object, locale: str = DEFAULT_LOCALE) -> str:
    """圖表說明（標題＋來源）純文字——**兩軌共用的唯一來源**。

    來源標記是研報可追溯性的一部分，不該因為換了渲染器就消失（Typst 軌曾固定傳空
    caption，導致 `source: "[7]"` 在 PDF 上只剩「圖 1」）。複製一份必然漂移，故兩軌
    都從這裡取。回傳純文字，跳脫由呼叫端負責（HTML 走 escape、Typst 走 `_tstr`）。
    來源標籤隨 locale（M10c）。
    """
    if not isinstance(spec, dict):  # 形狀防禦：畸形 LLM JSON 不得拋例外
        return ""
    title = str(spec.get("title") or "").strip()
    src = str(spec.get("source") or "").strip()
    if locale == "en":
        if title and src:
            return f"{title} (Source {src})"
        if src:
            return f"(Source {src})"
        return title
    if title and src:
        return f"{title}（來源 {src}）"
    if src:
        return f"（來源 {src}）"
    return title


def inject_charts(markdown_text: str, locale: str = DEFAULT_LOCALE) -> str:
    """把 markdown 內的 ```chart 區塊換成 <figure><svg>…</figure>；壞規格/數據缺則移除該塊。"""

    def _repl(m: "re.Match[str]") -> str:
        try:
            spec = json.loads(m.group(1))
        except (ValueError, TypeError):
            logger.warning("chart spec JSON 解析失敗，略過")
            return ""
        if not isinstance(spec, dict):
            logger.warning("chart spec 非物件，略過")
            return ""
        svg = render_chart_svg(spec)
        if not svg:
            logger.warning("chart 規格無效或數據缺，略過")
            return ""
        cap = _html.escape(chart_caption(spec, locale))
        figcap = f"<figcaption>{cap}</figcaption>" if cap else ""
        return f'\n\n<figure class="chart">{svg}{figcap}</figure>\n\n'

    return _CHART_RE.sub(_repl, markdown_text or "")


def inject_kpi(markdown_text: str, locale: str = DEFAULT_LOCALE) -> str:
    """把 ```kpi 區塊換成一排數據亮點卡片；壞 JSON 或 items 空則移除＋warning。"""
    src_label = "Source" if locale == "en" else "來源"

    def _repl(m: "re.Match[str]") -> str:
        try:
            spec = json.loads(m.group(1))
        except (ValueError, TypeError):
            logger.warning("kpi spec JSON 解析失敗，略過")
            return ""
        items = spec.get("items") if isinstance(spec, dict) else None
        if not isinstance(items, list):
            logger.warning("kpi 規格無效（缺 items 陣列），略過")
            return ""
        valid_items = [
            it
            for it in items
            if isinstance(it, dict)  # 形狀漂移（裸值/陣列）→ 跳過該項，不崩潰
        ]
        if len(valid_items) > 5:
            logger.warning("kpi items 超過 5 筆，僅渲染前 5 筆")
            valid_items = valid_items[:5]
        block_src = str(spec.get("source", "")).strip()
        cells = []
        for it in valid_items:
            val = _html.escape(str(it.get("value", "")))
            lab = _html.escape(str(it.get("label", "")))
            chg = str(it.get("change", "")).strip()
            direction = str(it.get("dir", "")).strip()
            direction = direction if direction in ("up", "down") else ""
            chg_html = (
                f'<div class="kpi-change {direction}">{_html.escape(chg)}</div>'
                if chg
                else ""
            )
            src = str(it.get("source") or block_src).strip()
            src_html = (
                f'<div class="kpi-src">{src_label} {_html.escape(src)}</div>' if src else ""
            )
            cells.append(
                f'<div class="kpi"><div class="kpi-value">{val}</div>'
                f'<div class="kpi-label">{lab}</div>{chg_html}{src_html}</div>'
            )
        if not cells:
            logger.warning("kpi 無有效 items，略過")
            return ""
        return f'\n\n<div class="kpi-strip">{"".join(cells)}</div>\n\n'

    return _KPI_RE.sub(_repl, markdown_text or "")


class _CiteBadgeHTMLParser(HTMLParser):
    """把文字節點中的引用標號換成徽章，保留 code/pre 等 verbatim HTML。"""

    _SKIP_TAGS = {"code", "pre"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ANN001
        self.parts.append(self.get_starttag_text() or f"<{tag}>")
        if tag.lower() in self._SKIP_TAGS:
            self._skip_depth += 1

    def handle_startendtag(self, tag: str, attrs) -> None:  # noqa: ANN001
        self.parts.append(self.get_starttag_text() or f"<{tag} />")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in self._SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        self.parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            self.parts.append(data)
            return
        self.parts.append(
            _CITE_RE.sub(lambda m: f'<sup class="cite">{m.group(1)}</sup>', data)
        )

    def handle_entityref(self, name: str) -> None:
        self.parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        self.parts.append(f"&#{name};")

    def handle_comment(self, data: str) -> None:
        self.parts.append(f"<!--{data}-->")

    def handle_decl(self, decl: str) -> None:
        self.parts.append(f"<!{decl}>")

    def result(self) -> str:
        return "".join(self.parts)


def cite_badges(html: str) -> str:
    """把內文的引用標號 [n]、[n,m] 換成上標金色小徽章（不動其他括號）。"""
    parser = _CiteBadgeHTMLParser()
    parser.feed(html or "")
    parser.close()
    return parser.result()


def _normalize_refs(body: str) -> str:
    """引用來源：確保每個 [n] 起新段落，不靠模型空行也能一條一行。"""
    return _REF_NL_RE.sub(r"\n\n\1", (body or "").strip())


def _render_fancy(title: str, sections: list[tuple[str, str]], meta: dict,
                  locale: str = DEFAULT_LOCALE) -> str:
    en = locale == "en"
    date = _html.escape(str(meta.get("date") or ""))
    brand = _html.escape(brand_name(locale))
    kicker = "In-Depth Research Report" if en else "深度研究報告"
    date_label = f"Generated {date}" if en else f"生成日期 {date}"
    foot_note = "AI-Assisted Research Analysis" if en else "AI 輔助研究分析"
    toc_title = "Contents" if en else "目錄"
    cover = (
        '<section class="cover">'
        f'<div class="cover-brand">{brand}</div>'
        '<div class="cover-rule"></div>'
        '<div class="cover-mid">'
        f'<div class="cover-kicker">{kicker}</div>'
        f'<h1 class="cover-title">{_html.escape(title)}</h1>'
        f'<div class="cover-date">{date_label}</div>'
        "</div>"
        f'<div class="cover-foot">{brand}　·　{foot_note}</div>'
        "</section>"
    )
    toc_items = "".join(
        f'<li><a href="#sec-{i}">{_html.escape(name)}</a></li>'
        for i, (name, _) in enumerate(sections)
    )
    toc = f'<nav class="toc"><div class="toc-h">{toc_title}</div><ol>{toc_items}</ol></nav>'
    body_parts = []
    for i, (name, body) in enumerate(sections):
        slug = _SECT_SLUG.get(name, "sec")
        prepared = inject_kpi(inject_charts(body, locale), locale)
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
    fancy_css = _FANCY_CSS.replace("$BRAND$", brand_name(locale))
    return (
        '<!doctype html><html><head><meta charset="utf-8">'
        f"<style>{fancy_css}{_DISCLAIMER_CSS}</style></head>"
        f"<body>{cover}{toc}{body}{_disclaimer_html(locale)}</body></html>"
    )


def _build_document(markdown_text: str, *, title: str, meta: dict,
                    locale: str = DEFAULT_LOCALE) -> str:
    """依內容選版型：完整研報走 fancy（封面/目錄/章節），退化輸入回退簡版。回完整 HTML。"""
    md = markdown_text or ""
    parsed_title, sections = split_report(md)
    if parsed_title and len(sections) >= _FANCY_MIN_SECTIONS:
        return _render_fancy(parsed_title, sections, meta or {}, locale)
    prepared = inject_charts(md, locale)
    body_html = _md.markdown(prepared, extensions=["tables", "fenced_code", "sane_lists"])
    return _document_html(title, body_html, meta or {}, locale)


def render_report_pdf(markdown_text: str, *, title: str, meta: dict,
                      locale: str = DEFAULT_LOCALE) -> bytes:
    """markdown → HTML → WeasyPrint PDF。回 PDF bytes（以 b'%PDF' 開頭）。輸出 chrome 隨 locale。"""
    doc = _build_document(markdown_text, title=title, meta=meta or {}, locale=locale)
    # 延遲 import：weasyprint 載入重（cffi/pango/fontconfig ~3s），不在模組頂層匯入，
    # 以免 `import web.server`（經 report→pdf）開機就吃這秒數（會拖垮匯入逾時測試）。
    from weasyprint import HTML

    return HTML(string=doc).write_pdf()
