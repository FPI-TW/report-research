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
from dataclasses import dataclass
from typing import Union

import pypandoc

from app.services.chart import _valid as _chart_valid
from app.services.chart import render_chart_svg

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
class DocumentModel:
    blocks: tuple[Block, ...]
    meta: DocMeta


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


def _parse_chart(raw: str) -> ChartBlock | None:
    """```chart JSON → ChartBlock（含預先算好的 SVG）；壞規格 → None。

    重用 chart.py 既有的 _valid 與 render_chart_svg（D5）：SVG 走 Typst image()
    原生嵌入，零 @preview 套件依賴 → 無網路編譯開箱即得。
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
    return ChartBlock(svg=svg)


def _prose_to_typst(md: str) -> str:
    """散文段 → Typst 片段。轉換失敗 → ""（該段捨棄，不讓整份炸掉）。"""
    if not md.strip():
        return ""
    try:
        return pypandoc.convert_text(md, "typst", format=_PANDOC_FORMAT)
    except Exception:
        logger.warning("pandoc 轉換失敗，略過該段", exc_info=True)
        return ""


def build_document(markdown: str, *, title: str, meta: dict | None = None) -> DocumentModel:
    """markdown + metadata → DocumentModel（模板契約的輸入）。

    純函式、無 I/O（pandoc 為同進程呼叫）。任何區塊解析失敗都只是少一個區塊，
    不影響其餘內容——研報寧可少一張 KPI 卡，不可整份沒有 PDF。
    """
    m = meta or {}
    blocks: list[Block] = []
    for kind, payload in _split_fences(markdown):
        if kind == "prose":
            frag = _prose_to_typst(payload)
            if frag.strip():
                blocks.append(ProseBlock(typst=frag))
        elif kind == "kpi":
            kb = _parse_kpi(payload)
            if kb is not None:
                blocks.append(kb)
        elif kind == "chart":
            cb = _parse_chart(payload)
            if cb is not None:
                blocks.append(cb)
    return DocumentModel(
        blocks=tuple(blocks),
        meta=DocMeta(
            title=str(title or ""),
            date=str(m.get("date") or ""),
            question=str(m.get("question") or ""),
        ),
    )
