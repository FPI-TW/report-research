"""研報 markdown → 型別化中介模型 → Typst → PDF（M9a）。

內容與版型解耦：`markdown` 是真相，```kpi/```chart 是結構化區塊，模板只負責排版。

**安全邊界**：LLM 原文永不直接拼接進 Typst 原始碼。只有兩種東西能通過——
pandoc 跳脫後的片段（`\\#eval`、`\\$`、`\\@` 皆為字面文字），以及 JSON 驗證後的
型別化資料。這不是風格偏好：Typst 是有 `#eval`/`#read`/`#import` 的圖靈完備語言，
把 LLM 輸出當原始碼拼進去等於給它任意執行能力。

**形狀防禦（CLAUDE.md 紅線）**：畸形 LLM JSON 一律略過該區塊並 log，例外絕不逃出。
WeasyPrint 路徑曾因 inject_kpi/inject_charts 未逐層 isinstance-guard 而炸穿
render_report_pdf——結果是無 PDF、無持久化、重建永久 500。這裡承擔同一條紅線。
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Union

import pypandoc

from app.services.chart import _valid as _chart_valid
from app.services.chart import render_chart_svg

# 與 WeasyPrint 路徑共用引用正規化與免責文字——兩軌各留一份必然漂移。
# pdf.py 的 weasyprint 是延遲 import，故此處頂層 import 不會吃到它的載入成本。
from app.services.locale import DEFAULT_LOCALE
from app.services.pdf import _normalize_refs, chart_caption

logger = logging.getLogger(__name__)

# 與 pdf.py 的 _KPI_RE/_CHART_RE 同構，但合併為單一 regex 以保留兩種圍欄的**相對順序**
# （分開掃會丟失 kpi 與 chart 誰先誰後）。
_FENCE_RE = re.compile(r"```(kpi|chart)\s*\n(.*?)\n```", re.DOTALL)

_KPI_MAX_ITEMS = 5  # 與 pdf.py:inject_kpi 一致

# pandoc reader 參數：**不可省**。GFM reader 預設把成對 $...$ 當數學模式，財經文本的
# 美元符號（"$100 美元"、"EPS $14.2"、"$880–$1,088"）會被誤配對成數學式。
# spike 實測關閉後正確跳脫為字面 \$（docs/typst_spike_m9a.md §pandoc 的兩個已知行為）。
_PANDOC_FORMAT = "gfm-tex_math_dollars"


@dataclass(frozen=True)
class KpiItem:
    value: str
    label: str
    change: str
    direction: str  # "up" | "down" | ""
    source: str


@dataclass(frozen=True)
class KpiBlock:
    items: tuple[KpiItem, ...]
    source: str


@dataclass(frozen=True)
class ChartBlock:
    svg: str  # 已由 chart.render_chart_svg 產出；Typst 以 image() 嵌入
    caption: str = ""  # 標題＋來源標記；來源是可追溯性的一部分，不可因換渲染器而消失


@dataclass(frozen=True)
class ProseBlock:
    typst: str  # pandoc 轉出的 Typst 片段（已跳脫）


Block = Union[ProseBlock, KpiBlock, ChartBlock]


@dataclass(frozen=True)
class DocMeta:
    title: str
    date: str
    question: str


@dataclass(frozen=True)
class Section:
    """模板契約的一個區塊。

    key 為骨架節鍵（見 _SECTION_KEYS）；**未知章節 key=None 但內容照樣保留**——
    內容是真相，版型不得吃掉內容。模板對 key=None 以泛用樣式排版。
    """

    key: str | None
    heading: str
    blocks: tuple[Block, ...]


@dataclass(frozen=True)
class DocumentModel:
    sections: tuple[Section, ...]
    meta: DocMeta

    @property
    def blocks(self) -> tuple[Block, ...]:
        """攤平所有區塊（順序保留），供不需章節結構的呼叫端使用。"""
        return tuple(b for s in self.sections for b in s.blocks)

    def section(self, key: str) -> Section | None:
        for s in self.sections:
            if s.key == key:
                return s
        return None


def _split_fences(markdown: str) -> list[tuple[str, str]]:
    """依圍欄**位置**切段 → [(kind, payload)]，kind ∈ {prose, kpi, chart}。

    不使用占位符：占位符會被 pandoc 當普通文字改寫（跳脫、斷行、標點轉換），
    導致還原時對不回去。切段後逐段轉換沒有這個問題（spike 建議 D4）。
    """
    out: list[tuple[str, str]] = []
    src = markdown or ""
    pos = 0
    for m in _FENCE_RE.finditer(src):
        if m.start() > pos:
            out.append(("prose", src[pos:m.start()]))
        out.append((m.group(1), m.group(2)))
        pos = m.end()
    if pos < len(src):
        out.append(("prose", src[pos:]))
    return out


def _parse_kpi(raw: str) -> KpiBlock | None:
    """```kpi JSON → KpiBlock；任何形狀不符→ None（呼叫端略過該區塊）。

    形狀契約與 pdf.py:inject_kpi 一致：{source?, items: [{value,label,change?,dir?,source?}]}。
    每個欄位存取前先驗形狀——items 非 list、item 非 dict、裸值、巢狀陣列都不得拋例外。
    """
    try:
        spec = json.loads(raw)
    except (ValueError, TypeError):
        logger.warning("kpi spec JSON 解析失敗，略過")
        return None
    if not isinstance(spec, dict):
        logger.warning("kpi 規格非物件，略過")
        return None
    items = spec.get("items")
    if not isinstance(items, list):
        logger.warning("kpi 規格無效（缺 items 陣列），略過")
        return None
    valid = [it for it in items if isinstance(it, dict)]  # 形狀漂移 → 跳過該項
    if len(valid) > _KPI_MAX_ITEMS:
        logger.warning("kpi items 超過 %d 筆，僅取前 %d 筆", _KPI_MAX_ITEMS, _KPI_MAX_ITEMS)
        valid = valid[:_KPI_MAX_ITEMS]
    if not valid:
        logger.warning("kpi 無有效 items，略過")
        return None
    block_src = str(spec.get("source", "")).strip()
    parsed: list[KpiItem] = []
    for it in valid:
        d = str(it.get("dir", "")).strip()
        parsed.append(
            KpiItem(
                value=str(it.get("value", "")),
                label=str(it.get("label", "")),
                change=str(it.get("change", "")).strip(),
                direction=d if d in ("up", "down") else "",
                source=str(it.get("source") or block_src).strip(),
            )
        )
    return KpiBlock(items=tuple(parsed), source=block_src)


def _parse_chart(raw: str, locale: str = DEFAULT_LOCALE) -> ChartBlock | None:
    """```chart JSON → ChartBlock（含預先算好的 SVG）；壞規格 → None。

    重用 chart.py 既有的 _valid 與 render_chart_svg（D5）：SVG 走 Typst image()
    原生嵌入，零 @preview 套件依賴 → 無網路編譯開箱即得。caption 來源標籤隨 locale。
    """
    try:
        spec = json.loads(raw)
    except (ValueError, TypeError):
        logger.warning("chart spec JSON 解析失敗，略過")
        return None
    if not _chart_valid(spec):
        logger.warning("chart 規格無效，略過")
        return None
    svg = render_chart_svg(spec)
    if not svg:
        logger.warning("chart SVG 產出為空，略過")
        return None
    return ChartBlock(svg=svg, caption=chart_caption(spec, locale))


class ProseConversionError(RuntimeError):
    """pandoc 無法轉換散文段。由分派層接住 → 回退 WeasyPrint（它不依賴 pandoc）。"""


def _strip_images(node: object) -> object:
    """遞迴移除 pandoc AST 的 Image 節點，只留 alt 文字。

    **這是安全邊界上的函式**：`_prose_to_typst` 的輸出雖然「經過 pandoc 跳脫」，但
    pandoc 不只是跳脫器——它會把 markdown 的 `![x](/a.png)` **翻譯成 Typst 的
    `#box(image("/a.png"))`**，也就是一個貨真價實的檔案讀取指令。跳脫保證的是 LLM 的
    *文字* 不會變成指令，擋不住 LLM 用 markdown 語法要求嵌圖。

    實測：`![x](/frontend/src/assets/help/ask.png)` 讓 PDF 從 33KB 變 195KB——repo 內
    的截圖被完整嵌進一份可下載、可轉發的 PDF。編譯 root=repo 只擋得住 `../` 逃出 repo，
    擋不住讀 repo *內* 的任何 SVG/PNG。研報語料是 NAS 鏡入的券商 PDF（半信任），間接
    注入即可誘導 LLM 輸出路徑。

    合法圖表走 ```chart 圍欄（SVG 由 chart.py 產出、以 bytes 內嵌），完全不需要檔案
    路徑，所以這裡直接拒絕**所有** Image：沒有白名單要維護，也就沒有白名單的洞。
    alt 文字保留為純文字，內容不會憑空消失。
    """
    if isinstance(node, list):
        out: list[object] = []
        for item in node:
            if isinstance(item, dict) and item.get("t") == "Image":
                # Image 的 c = [attr, alt_inlines, [url, title]]；只取 alt inlines
                c = item.get("c")
                alt = c[1] if isinstance(c, list) and len(c) > 1 and isinstance(c[1], list) else []
                got = _strip_images(alt)
                out.extend(got if isinstance(got, list) else [])
            else:
                out.append(_strip_images(item))
        return out
    if isinstance(node, dict):
        return {k: _strip_images(v) for k, v in node.items()}
    return node


def _prose_to_typst(md: str) -> str:
    """散文段 → Typst 片段。轉換失敗 **拋出**，不吞。

    先前這裡是逐段 fail-open（回 ""），但那個 fail-open 在文件層級是錯的：pandoc 若
    整個壞掉（版本不符、binary 缺失），每一段都被略過 → 產出一份「五章標題俱在、免責
    俱在、33KB、`%PDF` 開頭、零例外」卻**完全沒有內文**的空殼研報，然後照樣落地與寫 DB
    當成功。而且因為不拋，分派層的 fail-open 永遠不會觸發、WeasyPrint 也救不了。
    更隱蔽的是部分失敗：單段轉換失敗就從 PDF 靜默消失，無人察覺。

    區塊層的 fail-open（畸形 kpi/chart JSON 略過該區塊）是對的——少一張卡不影響研報
    成立；但少掉內文就不是同一件事了。
    """
    if not md.strip():
        return ""
    try:
        # 走 AST 中繼（md → json → 濾除 Image → typst），而非 md → typst 直轉：
        # 檔案路徑必須在**還是結構化節點**的時候拒絕，事後對 Typst 原始碼做正則
        # 移除等於在自己剛產生的程式碼上重新剖析，是同一類錯誤的溫床。
        ast = json.loads(pypandoc.convert_text(md, "json", format=_PANDOC_FORMAT))
        return pypandoc.convert_text(json.dumps(_strip_images(ast)), "typst", format="json")
    except Exception as exc:
        raise ProseConversionError(f"pandoc 轉換失敗：{exc}") from exc


def _blocks_for(body: str, locale: str = DEFAULT_LOCALE) -> tuple[Block, ...]:
    """一段 markdown（單一章節內文）→ 區塊序列。"""
    out: list[Block] = []
    for kind, payload in _split_fences(body):
        if kind == "prose":
            frag = _prose_to_typst(payload)
            if frag.strip():
                out.append(ProseBlock(typst=frag))
        elif kind == "kpi":
            kb = _parse_kpi(payload)
            if kb is not None:
                out.append(kb)
        elif kind == "chart":
            cb = _parse_chart(payload, locale)
            if cb is not None:
                out.append(cb)
    return tuple(out)


_FENCE_LINE_RE = re.compile(r"^(?P<indent> {0,3})(?P<marker>`{3,}|~{3,})(?P<info>.*)$")

_H1_RE = re.compile(r"^#[ \t]+(.+?)[ \t]*$", re.MULTILINE)
_H2_RE = re.compile(r"^##[ \t]+(.+?)[ \t]*$", re.MULTILINE)


def _fence_spans(src: str) -> list[tuple[int, int]]:
    """**所有** Markdown 圍欄的字元區間（``` 與 ~~~，長度 3+，含未閉合者）。

    章節切分必須看得見所有圍欄，不只 kpi/chart：一般 ```text / ```python / ~~~ 區塊
    裡的 `## ` 是程式碼或範例，不是章節標題。先前只掃 `_FENCE_RE`（僅 kpi/chart），
    於是一個合法的 code block 會被章節 regex 攔腰切成假章節，**原本的 code block 也
    跟著被截斷**（開頭圍欄留在上一節、閉合圍欄漏到下一節）。
    """
    spans: list[tuple[int, int]] = []
    pos, open_at, marker = 0, -1, ""
    for line in src.splitlines(keepends=True):
        m = _FENCE_LINE_RE.match(line.rstrip("\n\r"))
        if open_at < 0:
            # 反引號圍欄的 info string 不得含反引號（CommonMark）——排除行內程式碼
            if m and not (m.group("marker")[0] == "`" and "`" in m.group("info")):
                open_at, marker = pos, m.group("marker")
        elif (
            m
            and m.group("marker")[0] == marker[0]
            and len(m.group("marker")) >= len(marker)
            and not m.group("info").strip()  # 閉合圍欄後不得有 info string
        ):
            spans.append((open_at, pos + len(line)))
            open_at, marker = -1, ""
        pos += len(line)
    if open_at >= 0:
        spans.append((open_at, len(src)))  # 未閉合圍欄延伸到文末（CommonMark）
    return spans


def _extract_doc_title(markdown: str) -> tuple[str, str]:
    """取出文件級 `# 標題` → (title, 移除該行後的 markdown)。

    模板本來就會渲染 metadata title，而正常生成流程固定輸出一個 `# 主標題`，先前它被
    當成前言散文原樣保留 → **每一份研報的 PDF 都同時出現兩個主標題**（呼叫端傳入的
    建議標題，與 LLM 自己寫的標題，兩者不同時尤其刺眼）。

    只處理第一個 `## ` 之前、且不在圍欄內的第一個 H1——那正是「文件標題」的位置。
    章節內文裡的 `# ` 不動（那是內容，不是文件標題）。
    """
    src = markdown or ""
    spans = _fence_spans(src)
    first_h2 = next(
        (m.start() for m in _H2_RE.finditer(src)
         if not any(a <= m.start() < b for a, b in spans)),
        len(src),
    )
    for m in _H1_RE.finditer(src):
        if m.start() >= first_h2:
            break
        if any(a <= m.start() < b for a, b in spans):
            continue
        return m.group(1).strip(), src[: m.start()] + src[m.end():]
    return "", src


def _split_sections(markdown: str) -> list[tuple[str, str]]:
    """依頂層 `## ` 切章節 → [(heading, body_md)]。

    **必須在 pandoc 之前切**：pandoc 會把 `## 執行摘要` 轉成 Typst 的 `== 執行摘要`，
    章節邊界就化進片段裡、模板再也分不出區塊。

    `## ` 之前的前言以 heading="" 的首段承接——不丟棄。
    圍欄內的 `## ` 不算章節標題（見 `_fence_spans`）。
    """
    src = markdown or ""
    spans = _fence_spans(src)

    def _in_fence(i: int) -> bool:
        return any(a <= i < b for a, b in spans)

    out: list[tuple[str, str]] = []
    cuts = [m for m in _H2_RE.finditer(src) if not _in_fence(m.start())]
    if not cuts:
        return [("", src)] if src.strip() else []
    if src[: cuts[0].start()].strip():
        out.append(("", src[: cuts[0].start()]))
    for i, m in enumerate(cuts):
        end = cuts[i + 1].start() if i + 1 < len(cuts) else len(src)
        out.append((m.group(1).strip(), src[m.end() : end]))
    return out


# markdown 章節標題 → 骨架節鍵。標題文字來自 M7 的五章骨架（report_writer 的
# outline）；此處是渲染層對它的鏡像，兩邊漂移只會讓章節退化為 key=None 的泛用
# 區塊（內容仍保留），不會丟內容。
_SECTION_KEYS: dict[str, str] = {
    "執行摘要": "exec_summary",
    "關鍵發現": "key_findings",
    "重點分析": "analysis",
    "風險與展望": "risk_outlook",
    "風險展望": "risk_outlook",  # 容忍去「與」的變體
    "引用來源": "references",
    "外部參考（網路）": "external",
    "外部參考": "external",
    # M10c：英文骨架標題（與 report_writer.SKELETON_HEADINGS_EN 對齊）
    "Executive Summary": "exec_summary",
    "Key Findings": "key_findings",
    "In-Depth Analysis": "analysis",
    "Risks & Outlook": "risk_outlook",
    "References": "references",
    "External References (Web)": "external",
}


def _section_key(heading: str) -> str | None:
    return _SECTION_KEYS.get(heading.strip())


def build_document(markdown: str, *, title: str, meta: dict | None = None,
                   locale: str = DEFAULT_LOCALE) -> DocumentModel:
    """markdown + metadata → DocumentModel（模板契約的輸入）。

    無 I/O（pandoc 為同進程呼叫）。任何區塊解析失敗都只是少一個區塊，不影響其餘
    內容——研報寧可少一張 KPI 卡，不可整份沒有 PDF。未知章節保留為 key=None。
    """
    m = meta or {}
    # 文件級 H1 抽成 metadata title（模板負責排它），不再以散文重複渲染一次
    doc_title, markdown = _extract_doc_title(markdown or "")
    sections = []
    for heading, body in _split_sections(markdown):
        key = _section_key(heading)
        if key == "references":
            # 引用來源逐條起新段落：模型常把 [1][2][3] 寫成單換行，pandoc 會併成一段
            # → PDF 上整串擠成一行。與 WeasyPrint 路徑共用同一個正規化（複製一份必漂移）。
            body = _normalize_refs(body)
        sections.append(Section(key=key, heading=heading, blocks=_blocks_for(body, locale)))
    return DocumentModel(
        sections=tuple(s for s in sections if s.blocks or s.heading),
        meta=DocMeta(
            # 呼叫端的建議標題優先；沒有時才用 LLM 寫的 H1（總比無標題好）
            title=str(title or doc_title or ""),
            date=str(m.get("date") or ""),
            question=str(m.get("question") or ""),
        ),
    )


# ── DocumentModel → Typst 原始碼 → PDF ────────────────────────────────────────

_TEMPLATE_PATH = "/app/templates/ib-classic.typ"  # root 相對路徑（compile 的 root=repo）


def _tstr(s: object) -> str:
    """Python 值 → Typst 字串常值。

    **這是安全邊界上的函式**：Typst 有 #eval/#read/#import 且圖靈完備，任何未跳脫的
    值拼進原始碼等於任意執行。反斜線必須先跳脫（否則後續跳脫的 \\" 會被它反過來吃掉）。
    """
    t = str(s)
    t = t.replace("\\", "\\\\").replace('"', '\\"')
    # 控制字元（含 CR/LF）在 Typst 字串常值中須以逸出序列表示
    t = t.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
    return f'"{t}"'


def _emit_kpi(items: tuple[KpiItem, ...]) -> str:
    if not items:
        return "()"
    cells = ", ".join(
        "(value: {v}, label: {l}, change: {c}, direction: {d}, source: {s})".format(
            v=_tstr(it.value), l=_tstr(it.label), c=_tstr(it.change),
            d=_tstr(it.direction), s=_tstr(it.source),
        )
        for it in items
    )
    # 單元素 tuple 在 Typst 需尾逗號，否則會被當成括號運算式
    return f"({cells},)"


def _emit_body(
    doc: DocumentModel,
    *,
    chart_supplement: str | None = None,
    kpi_source_label: str | None = None,
) -> str:
    """章節 → Typst body。ProseBlock 已是 pandoc 跳脫後的片段，可直接插入。

    **heading 必須走 `_tstr` 成為字串常值，不可用 `#section-heading[...]` 的 content
    語法**：章節標題是刻意在 pandoc 之前切出來的（否則 `## X` 會變成 Typst `== X` 而
    丟失章節邊界），所以它是這條管線上**唯一沒有被 pandoc 跳脫過**的 LLM 原文。用
    content 語法等於把它當 Typst 原始碼求值——實測 `## #read("/.env")` 會把 repo root
    的 .env（含共用帳密與 DB 連線字串）整份渲染進一份可下載的 PDF；`## #eval(...)` 會
    真的求值；`]` 還能脫出 content block 接任意指令。

    附帶：content 語法也讓 spec D6 在標題失效——`## 2026 年 EPS 上修 $14.2 至 $16.8`
    的兩個 `$` 會被配對成數學模式而吃掉內容，`## 依 @法說會 資料` 會直接編譯失敗。

    M10c：chart_supplement／kpi_source_label 非 None 時（en）於呼叫附上該具名引數，
    覆寫 .typ 的中文預設（「圖」／「來源」）；None（zh）則不附引數，輸出 byte 相同。
    """
    supp = f", supplement: {_tstr(chart_supplement)}" if chart_supplement is not None else ""
    kpi_lbl = f", source-label: {_tstr(kpi_source_label)}" if kpi_source_label is not None else ""
    out: list[str] = []
    for sec in doc.sections:
        if sec.heading:
            out.append(f"#section-heading({_tstr(sec.heading)})")
        for b in sec.blocks:
            if isinstance(b, ProseBlock):
                out.append(b.typst)
            elif isinstance(b, ChartBlock):
                out.append(f"#chart-figure({_tstr(b.svg)}, {_tstr(b.caption)}{supp})")
            elif isinstance(b, KpiBlock):
                # 章節內的 KPI（非跨欄置頂那組）就地排一列
                out.append(f"#kpi-strip({_emit_kpi(b.items)}{kpi_lbl})")
    return "\n\n".join(out)


def _split_hero_kpi(doc: DocumentModel) -> tuple[tuple[KpiItem, ...], DocumentModel]:
    """抽出第一組 KPI 作為跨欄置頂的 KPI 帶（設計定稿：數字先行）。

    其餘 KPI 留在原章節。跨欄那組必須由 report() 參數帶入——留在 body 會被
    columns(2) 壓進單一欄。
    """
    hero: tuple[KpiItem, ...] = ()
    secs = []
    for sec in doc.sections:
        blocks = []
        for b in sec.blocks:
            if isinstance(b, KpiBlock) and not hero:
                hero = b.items
                continue
            blocks.append(b)
        secs.append(Section(key=sec.key, heading=sec.heading, blocks=tuple(blocks)))
    return hero, DocumentModel(sections=tuple(secs), meta=doc.meta)


def emit_typst(
    doc: DocumentModel,
    *,
    disclaimer: str,
    methods: str = "",
    template_import_path: str = _TEMPLATE_PATH,
    locale: str = DEFAULT_LOCALE,
) -> str:
    """DocumentModel → 完整 .typ 原始碼（呼叫模板契約的 4 個函式）。

    template_import_path 指向 compile root 內的模板檔（預設 ib-classic）；M9b 依
    template_id 換不同模板，import 契約不變（report/section-heading/kpi-strip/chart-figure）。

    M10c：locale=en 時額外 emit 品牌／頁尾／語系／KPI 來源標籤等 chrome 參數（模板函式
    皆有中文預設，未 emit 時走預設）。zh-Hant（預設）不 emit 任何額外參數 → 輸出與
    改動前 byte 相同（零回歸）。全部經 `_tstr` 跳脫，維持安全邊界。
    """
    en = locale == "en"
    # en-only chrome：從 pdf.py 取品牌（兩軌單一真相源），其餘為本地標籤常量
    extra = ""
    chart_supp: str | None = None
    kpi_src_label: str | None = None
    if en:
        from app.services.pdf import brand_name

        extra = (
            f"  brand: {_tstr(brand_name('en'))},\n"
            f"  footer-note: {_tstr('Auto-generated')},\n"
            '  lang: "en",\n'
            '  region: "US",\n'
            f"  kpi-source-label: {_tstr('Source')},\n"
        )
        chart_supp = "Fig."
        kpi_src_label = "Source"
    hero, rest = _split_hero_kpi(doc)
    head = (
        f'#import "{template_import_path}": report, section-heading, kpi-strip, chart-figure\n\n'
        "#show: report.with(\n"
        f"  title: {_tstr(doc.meta.title)},\n"
        f"  date: {_tstr(doc.meta.date)},\n"
        f"  subject: {_tstr(doc.meta.question)},\n"
        f"  kpi: {_emit_kpi(hero)},\n"
        f"  methods: {_tstr(methods)},\n"
        f"  disclaimer: {_tstr(disclaimer)},\n"
        f"{extra}"
        ")\n\n"
    )
    return head + _emit_body(rest, chart_supplement=chart_supp, kpi_source_label=kpi_src_label) + "\n"


def render_report_pdf(
    markdown_text: str, *, title: str, meta: dict, template_id: str | None = None,
    locale: str = DEFAULT_LOCALE,
) -> bytes:
    """markdown → Typst → PDF bytes。簽章與 pdf.render_report_pdf 一致（雙軌可互換）。

    template_id 依 manifest 選模板（None／未知 → 預設 ib-classic，fail-safe）；只換渲染
    層、不動內容。

    典型 0.2s（spike 實測，WeasyPrint 為秒級）。模板零 @preview 依賴故無網路需求。
    失敗直接拋——由 report.py 的分派層 fail-open 回退 WeasyPrint（回退路徑同樣有
    免責，見 pdf.REPORT_DISCLAIMER）。

    **編譯 root 是隔離的暫存目錄，只放模板與生成檔**，不是 repo root。root=repo 擋得住
    `../` 逃出 repo，卻讓 repo 內的每個檔案都可被讀取——`.env`、券商 PDF、任何截圖都在
    射程內。這裡是縱深防禦的第二層：`_strip_images` 已在 AST 階段拒絕所有檔案路徑，就算
    它有漏，root 內也沒有東西可洩漏。
    """
    # 延遲 import：與 pdf.py 的 weasyprint 同理，不讓模組匯入期吃載入成本
    import typst

    from app.services.pdf import report_disclaimer
    from app.templates import manifest

    spec = manifest.resolve(template_id)  # 未知/None → 預設（fail-safe）
    template_rel = f"/app/templates/{spec.filename}"  # compile root 內相對路徑
    template_src = Path(__file__).resolve().parents[1] / "templates" / spec.filename

    doc = build_document(markdown_text, title=title, meta=meta or {}, locale=locale)
    src = emit_typst(
        doc, disclaimer=report_disclaimer(locale),
        template_import_path=template_rel, locale=locale,
    )
    with tempfile.TemporaryDirectory(prefix="tf-typst-") as tmpdir:
        root = Path(tmpdir)
        # 選定模板放進 root 內對應相對路徑，`template_rel` 的 import 才解析得到
        dst = root / template_rel.lstrip("/")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(template_src, dst)
        entry = root / "report.typ"
        entry.write_text(src, encoding="utf-8")
        return typst.compile(str(entry), root=str(root))
