"""pdfplumber／pdfminer.six 版面分析 → `model.Document`。

對應 docs/EXTRACTION_REDESIGN.md 的 `E1`。**本檔目前不接生產路徑**，只給
`scripts/compare_extractors.py` 與 `scripts/profile_corpus.py` 用。

## 這支模組在修什麼

`pypdf.extract_text()` 沒有版面模型，依 content stream 順序吐字，於是雙欄
研報的左右欄交錯。實測 12 份跨券商樣本，pypdf 與 pdfplumber 的去空白後
順序相似度只有 0.275–0.884——**字元幾乎一模一樣，順序不一樣**。所以這裡
要做的不是「抽到更多字」，是「把字排對」。

## 每一個門檻都是可能錯的地方

底下的常數沒有一個是理論值，全部是啟發式。它們錯了**不會拋例外**，只會
讓輸出看起來「比較亂」——這正是 `compare_extractors.py` 要把 Block 分類
結果一起印出來的理由：分不出「抽取壞了」還是「分類壞了」的話，就無從
校準。閾值的正式校準要等 `E0` 的 golden set（§4.1）。
"""

from __future__ import annotations

import logging
import re
import statistics
from collections import Counter
from pathlib import Path
from typing import Any

from app.services.extraction import EXTRACTION_VERSION, EXTRACTOR_NAME
from app.services.extraction.model import Block, BlockType, Document, Page

logger = logging.getLogger(__name__)

# ── 欄偵測 ──────────────────────────────────────────────────────────────────
# 投影用的直方圖格數。200 格對 A4（595pt）約 3pt/格，比欄間距小一個量級。
_COLS_BINS = 200
# 溝槽至少要占內容寬度這麼多才算欄界。4% 對 A4 約 21pt——實測券商雙欄研報
# 的欄間距在 18–36pt，而同一行內兩個詞之間的空白不會超過幾 pt。
_MIN_GUTTER_FRAC = 0.04
# 溝槽必須落在內容寬度的這個區間內。靠邊的空白是頁邊距不是欄界。
_GUTTER_BAND = (0.18, 0.82)
# 每一欄至少要分到這麼多比例的詞，否則那條「溝槽」多半是一張置中的圖。
_MIN_COL_SHARE = 0.15
# 每一欄的墨跡寬度至少要占內容寬度這麼多，否則它不是一欄、是表格裡的一個窄欄位。
# 實測元大投資早報首頁：左側報告清單的「評等」欄只有 20pt 寬（買進／持有），
# 詞數卻夠多（每列一個），通過了 _MIN_COL_SHARE，於是整頁被切成三欄、每列的
# 「買進」被甩到另一條文字流。窄欄不是整頁退回單欄，而是**併回相鄰空隙較小的那一欄**
# ——那一頁真正的兩欄（清單 vs 目次）仍然要分開。
_MIN_COL_WIDTH_FRAC = 0.12
# 在溝槽處要有多寬的間隙才算「這裡真的是欄界」，以溝槽最小寬度為單位。
# 取 0.5 是因為欄界處的實際留白必然接近整條溝槽寬，而跨欄大標在那裡只有
# 一個字距——兩者相差一個量級，門檻落在中間很安全。
_GUTTER_GAP_SLACK = 0.5
# 欄數上限。研報實務上是 1 或 2 欄；判到 4 欄幾乎都是表格被誤認。
_MAX_COLS = 3
# 投影只取版心：頁首、跨欄大標、頁尾都會橫跨溝槽，把它們算進去會讓
# **每一頁的雙欄都偵測不到**。這是這段最容易犯的錯。
_BODY_BAND = (0.12, 0.90)

# ── 行與段落 ────────────────────────────────────────────────────────────────
# 同一行的垂直容差，以該頁字高中位數為單位。
_LINE_TOL_FRAC = 0.60
# 段落內相鄰行的垂直間距上限，以行高中位數為單位。超過就換段。
_PARA_GAP_FRAC = 0.85
# 段落左緣容差（pt）。首行縮排的中文段落左緣會比後續行右移約兩個字。
_PARA_INDENT_TOL = 24.0

# ── 型別分類 ────────────────────────────────────────────────────────────────
# 頁首／頁尾候選帶（占頁高比例）。
_HEADER_BAND = 0.10
_FOOTER_BAND = 0.90
# 跨頁重複判定：normalize 後的文字要在這麼多頁出現過才算樣板。
# **這是「跨頁重複 bbox 自動識別頁首頁尾」那條交付的實作**——只看位置會把
# 首頁的標題誤判成頁首，只看重複會把「本報告僅供參考」這種每頁都有的
# 正文免責句誤判成頁尾，兩個條件要同時成立。
_REPEAT_MIN_PAGES = 2
_REPEAT_MIN_FRAC = 0.30
# 標題：字級要比中位數大這麼多倍，且夠短。
_TITLE_SIZE_RATIO = 1.15
_TITLE_MAX_CHARS = 60
# 註腳：字級小於中位數這麼多倍，且在頁面下半。
_FOOTNOTE_SIZE_RATIO = 0.85
# 圖說：保守起見只認明確的前綴。「表」刻意不列入——它會跟真表格打架。
_CAPTION_RE = re.compile(r"^\s*(圖\s*\d|Figure\s*\d|Fig\.\s*\d|Exhibit\s*\d)", re.I)
_CAPTION_MAX_CHARS = 120

# ── 表格 ────────────────────────────────────────────────────────────────────
# pdfplumber 對有框線的版面很容易把「兩條分隔線之間的一段文字」當成表格。
# 要求至少 2×2 且過半儲存格有內容，否則丟回去當一般文字處理。
_TABLE_MIN_ROWS = 2
_TABLE_MIN_COLS = 2
_TABLE_MIN_FILL = 0.50
# 文字對齊策略（補無框線表）。門檻刻意比框線策略嚴——它的誤判是「把並排的
# 兩段文字當成兩欄表格」，那種結果進了 full_text 會比不抽表格更糟。
_TEXT_TABLE_SETTINGS = {
    "vertical_strategy": "text",
    "horizontal_strategy": "text",
    # 相鄰文字要離這麼遠才算不同欄／不同列（pt）。太小會把字距當欄界。
    "text_x_tolerance": 3,
    "text_y_tolerance": 3,
    "intersection_tolerance": 5,
}
_TEXT_TABLE_MIN_ROWS = 3
_TEXT_TABLE_MIN_FILL = 0.60
# 欄數整齊度：多少比例的列必須有相同欄數。
_TABLE_REGULAR_MIN = 0.80
# **整列全空的比例上限**。`horizontal_strategy: "text"` 會在每一條文字線的
# 上下各切一次，於是把「一般段落」切成「資料列與空列交替」——那是這個策略
# 的系統性假影，也是它誤判散文的招牌特徵。
# 實測分界很乾淨：凱基投資早報 6 張真表的空列比例是 0%/4%/0%/4%/3%/3%，
# 而被誤判的散文區塊是 40%。**不要改成限制欄數**——同一份檔的真表格有到
# 17 欄，欄數上限會把真表格一起殺掉。
_TEXT_TABLE_MAX_EMPTY_ROW_FRAC = 0.15

_WS = re.compile(r"\s+")


def _norm(s: str) -> str:
    """跨頁重複比對用的正規化。與 `textnorm.norm_for_match` 精神相同，但這裡
    不需要 NFKC——比的是同一份 PDF 內部的樣板文字，不跨文件。"""
    return _WS.sub("", s).lower()


# ── 欄偵測 ──────────────────────────────────────────────────────────────────


def detect_columns(words: list[dict], width: float, height: float) -> list[float]:
    """回傳欄的切分 x 座標（不含頁面兩端）。空 list ＝ 單欄。

    做法：把版心帶內的詞投影到水平直方圖，找出夠寬、位置合理、且兩側都有
    足量詞的空白帶。
    """
    if not words or width <= 0:
        return []
    body = [
        w
        for w in words
        if _BODY_BAND[0] * height <= (w["top"] + w["bottom"]) / 2 <= _BODY_BAND[1] * height
    ]
    if len(body) < 30:  # 詞太少，統計無意義
        return []

    x0 = min(w["x0"] for w in body)
    x1 = max(w["x1"] for w in body)
    span = x1 - x0
    if span <= 0:
        return []

    covered = [False] * _COLS_BINS
    for w in body:
        a = int((w["x0"] - x0) / span * (_COLS_BINS - 1))
        b = int((w["x1"] - x0) / span * (_COLS_BINS - 1))
        for i in range(max(0, a), min(_COLS_BINS - 1, b) + 1):
            covered[i] = True

    min_bins = max(1, int(_MIN_GUTTER_FRAC * _COLS_BINS))
    lo = int(_GUTTER_BAND[0] * _COLS_BINS)
    hi = int(_GUTTER_BAND[1] * _COLS_BINS)

    gutters: list[float] = []
    run_start: int | None = None
    for i in range(_COLS_BINS):
        if not covered[i]:
            run_start = i if run_start is None else run_start
            continue
        if run_start is not None:
            if i - run_start >= min_bins and lo <= (run_start + i) // 2 <= hi:
                gutters.append(x0 + ((run_start + i) / 2) / (_COLS_BINS - 1) * span)
            run_start = None
    if run_start is not None and _COLS_BINS - run_start >= min_bins:
        mid = (run_start + _COLS_BINS) // 2
        if lo <= mid <= hi:
            gutters.append(x0 + (mid / (_COLS_BINS - 1)) * span)

    if not gutters or len(gutters) >= _MAX_COLS:
        return []

    gutters = _merge_narrow_columns(gutters, body, x0, x1)
    if not gutters:
        return []

    # 每一欄都要有足量的詞，否則那條空白帶多半是置中的圖或短表。
    edges = [x0 - 1.0, *gutters, x1 + 1.0]
    for a, b in zip(edges, edges[1:]):
        share = sum(1 for w in body if a <= (w["x0"] + w["x1"]) / 2 < b) / len(body)
        if share < _MIN_COL_SHARE:
            return []
    return gutters


def _merge_narrow_columns(gutters: list[float], body: list[dict], x0: float, x1: float) -> list[float]:
    """把墨跡太窄的欄併回相鄰欄：拿掉它與空隙較小那一側之間的溝槽，直到沒有窄欄。"""
    span = x1 - x0
    gutters = list(gutters)
    while gutters:
        edges = [x0 - 1.0, *gutters, x1 + 1.0]
        ink: list[tuple[float, float] | None] = []
        for a, b in zip(edges, edges[1:]):
            ws = [w for w in body if a <= (w["x0"] + w["x1"]) / 2 < b]
            ink.append((min(w["x0"] for w in ws), max(w["x1"] for w in ws)) if ws else None)
        narrow = [i for i, k in enumerate(ink) if k is None or (k[1] - k[0]) < _MIN_COL_WIDTH_FRAC * span]
        if not narrow:
            return gutters
        i = narrow[0]
        candidates: list[tuple[float, int]] = []  # (空隙寬, 要移除的溝槽索引)
        if i > 0:
            gap = (ink[i][0] if ink[i] else gutters[i - 1]) - (ink[i - 1][1] if ink[i - 1] else gutters[i - 1])
            candidates.append((gap, i - 1))
        if i < len(ink) - 1:
            gap = (ink[i + 1][0] if ink[i + 1] else gutters[i]) - (ink[i][1] if ink[i] else gutters[i])
            candidates.append((gap, i))
        gutters.pop(min(candidates)[1])
    return gutters


def _column_of(x0: float, x1: float, gutters: list[float]) -> int:
    """區間 → 欄索引。**橫跨溝槽者一律回 0。**

    跨欄元素（大標、橫幅表格）沒有「屬於哪一欄」這回事。回 0 讓它落在左欄
    的流裡、依 y 排到該去的位置——對「置頂的跨欄大標」是正確的，對「夾在
    中間的橫幅表格」則是已知的近似（真正的閱讀順序需要把頁面切成跨欄帶與
    分欄帶，那超出本輪範圍）。

    **不可以改用中點判定**：橫幅元素的中點必然落在溝槽右側，於是整條大標會
    被排到右欄最後面。
    """
    if any(x0 < g < x1 for g in gutters):
        return 0
    mid = (x0 + x1) / 2
    idx = 0
    for g in gutters:
        if mid >= g:
            idx += 1
    return idx


# ── 行與段落 ────────────────────────────────────────────────────────────────


def _split_at_gutters(line: list[dict], gutters: list[float], min_gap: float) -> list[list[dict]]:
    """一條 y 相同的詞列 → 依溝槽切成數段。

    **雙欄版面裡左右兩欄的行共用同一個 y**，所以頁面層級的分行會把它們併成
    一條；這裡再依溝槽切開。但只有在跨越溝槽處**真的有夠寬的間隙**時才切——
    橫跨兩欄的大標也會跨越溝槽，而它在那裡只有一個普通字距。

    只用「有沒有跨越溝槽」判斷會把大標切成兩半；只用「詞的中點在哪一側」判斷
    會把大標的前後半分進不同欄，變成兩個互不相干的段落。兩者都踩過。
    """
    if not gutters or len(line) < 2:
        return [line]
    ordered = sorted(line, key=lambda w: w["x0"])
    runs: list[list[dict]] = [[ordered[0]]]
    for prev, cur in zip(ordered, ordered[1:]):
        crossed = any(prev["x1"] <= g <= cur["x0"] for g in gutters)
        if crossed and (cur["x0"] - prev["x1"]) >= min_gap:
            runs.append([cur])
        else:
            runs[-1].append(cur)
    return runs


def _group_lines(words: list[dict]) -> list[list[dict]]:
    """同一欄內的詞 → 行。"""
    if not words:
        return []
    heights = [w["bottom"] - w["top"] for w in words if w["bottom"] > w["top"]]
    tol = _LINE_TOL_FRAC * (statistics.median(heights) if heights else 10.0)
    ordered = sorted(words, key=lambda w: (round(w["top"], 2), round(w["x0"], 2)))
    lines: list[list[dict]] = []
    for w in ordered:
        if lines and abs(w["top"] - lines[-1][0]["top"]) <= tol:
            lines[-1].append(w)
        else:
            lines.append([w])
    for ln in lines:
        ln.sort(key=lambda w: round(w["x0"], 2))
    return lines


def _line_text(line: list[dict]) -> str:
    """一行的文字。

    **一律以空白接**，與現況 pypdf 輸出的形狀一致：CJK 之間多出來的空白由
    `textnorm.clean_extracted` 的 `_RE_CJK_GAP` 在入庫時移除，而
    `quoteNeedle.ts` 的引文階梯也是照著那個形狀調校的（實測 95.3%）。
    在這裡自作聰明地「CJK 不加空白」會讓 full_text 與既有語料形狀不一致，
    而那個不一致沒有任何測試看得見。
    """
    return " ".join(w["text"] for w in line)


def _group_paragraphs(lines: list[list[dict]]) -> list[list[list[dict]]]:
    """行 → 段落。"""
    if not lines:
        return []
    gaps = []
    for a, b in zip(lines, lines[1:]):
        gaps.append(min(w["top"] for w in b) - max(w["bottom"] for w in a))
    heights = [
        max(w["bottom"] for w in ln) - min(w["top"] for w in ln) for ln in lines
    ]
    line_h = statistics.median(heights) if heights else 10.0
    limit = _PARA_GAP_FRAC * line_h

    paras: list[list[list[dict]]] = [[lines[0]]]
    for i, ln in enumerate(lines[1:]):
        prev_x0 = min(w["x0"] for w in paras[-1][0])
        cur_x0 = min(w["x0"] for w in ln)
        same_block = gaps[i] <= limit and abs(cur_x0 - prev_x0) <= _PARA_INDENT_TOL
        if same_block:
            paras[-1].append(ln)
        else:
            paras.append([ln])
    return paras


# ── 表格 ────────────────────────────────────────────────────────────────────


def _accept(
    rows: list[list[Any]], min_rows: int, min_fill: float, need_regular: bool
) -> tuple[tuple[str, ...], ...] | None:
    """表格候選的驗收。回 None ＝ 丟回去當一般文字處理。"""
    if len(rows) < min_rows:
        return None
    width = max((len(r) for r in rows), default=0)
    if width < _TABLE_MIN_COLS:
        return None
    cells = sum(len(r) for r in rows)
    filled = sum(1 for r in rows for c in r if c and str(c).strip())
    if not cells or filled / cells < min_fill:
        return None
    if need_regular:
        # 文字策略最常見的誤判是「把普通段落切成一欄一欄」，那種結果的欄數
        # 很不整齊。要求絕大多數列都有相同欄數，才當它是真表格。
        regular = sum(1 for r in rows if len(r) == width) / len(rows)
        if regular < _TABLE_REGULAR_MIN:
            return None
        blank = sum(1 for r in rows if not any(c and str(c).strip() for c in r))
        if blank / len(rows) > _TEXT_TABLE_MAX_EMPTY_ROW_FRAC:
            return None

    # 全空列不進序列化：真表格也會有幾列（實測 0-4%），留著只是讓 markdown
    # 多出空行。刪除是決定性的，不影響 E1 的逐字元一致驗收。
    out = tuple(tuple("" if c is None else str(c) for c in r) for r in rows)
    out = tuple(r for r in out if any(c.strip() for c in r))
    return out or None


def _bbox4(v) -> tuple[float, float, float, float]:
    x0, top, x1, bottom = (float(n) for n in tuple(v)[:4])
    return (x0, top, x1, bottom)


def _overlaps(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> bool:
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


def _find(page: Any, settings: dict | None) -> list[Any]:
    try:
        return list(page.find_tables(table_settings=settings) if settings else page.find_tables())
    except Exception as exc:  # pdfplumber 對畸形框線／密集文字會拋各種例外
        logger.debug("find_tables failed on page %s: %s", getattr(page, "page_number", "?"), exc)
        return []


def _extract_tables(page: Any) -> list[tuple[tuple[float, float, float, float], tuple[tuple[str, ...], ...]]]:
    """兩種策略依序試：框線優先，再用文字對齊補無框線表。

    **券商研報大量使用無框線表格**——實測凱基投資早報 31 頁只有 2 張表有
    框線，而目標價／評等一覽表全都是靠文字對齊排的。只跑預設的框線策略，
    診斷 #5（表格欄界被抹掉）根本沒有機會被觸發，因為表格壓根沒被認出來。

    代價是文字策略會誤判：它會把「兩段左右並排的文字」看成兩欄表格。所以
    它的驗收比框線策略嚴（要求更多列、更高填充率、且欄數整齊），而且**只
    在不與框線表重疊的區域採用**——框線表是比較可信的證據。
    """
    out: list[tuple[tuple[float, float, float, float], tuple[tuple[str, ...], ...]]] = []

    for t in _find(page, None):
        try:
            rows = t.extract()
        except Exception:
            continue
        norm = _accept(rows, _TABLE_MIN_ROWS, _TABLE_MIN_FILL, need_regular=False)
        if norm is not None:
            out.append((_bbox4(t.bbox), norm))

    for t in _find(page, _TEXT_TABLE_SETTINGS):
        bbox = _bbox4(t.bbox)
        if any(_overlaps(bbox, b) for b, _ in out):
            continue
        try:
            rows = t.extract()
        except Exception:
            continue
        norm = _accept(rows, _TEXT_TABLE_MIN_ROWS, _TEXT_TABLE_MIN_FILL, need_regular=True)
        if norm is not None:
            out.append((bbox, norm))

    return out


def _inside(word: dict, bbox: tuple[float, float, float, float]) -> bool:
    cx = (word["x0"] + word["x1"]) / 2
    cy = (word["top"] + word["bottom"]) / 2
    return bbox[0] <= cx <= bbox[2] and bbox[1] <= cy <= bbox[3]


# ── 型別分類 ────────────────────────────────────────────────────────────────


def _classify(
    text: str, bbox: tuple[float, float, float, float], size: float, med_size: float, height: float
) -> BlockType:
    if _CAPTION_RE.match(text) and len(text) <= _CAPTION_MAX_CHARS:
        return "figure_caption"
    if med_size > 0:
        if size >= _TITLE_SIZE_RATIO * med_size and len(text) <= _TITLE_MAX_CHARS:
            return "title"
        if size <= _FOOTNOTE_SIZE_RATIO * med_size and bbox[1] > 0.5 * height:
            return "footnote"
    return "paragraph"


def _mark_repeated_headers_footers(pages: list[Page]) -> list[Page]:
    """跨頁重複 ＋ 位置，兩個條件同時成立才改判頁首／頁尾。

    只看位置會把首頁大標當頁首；只看重複會把每頁都印的正文免責句當頁尾。
    """
    n = len(pages)
    if n < _REPEAT_MIN_PAGES:
        return pages
    threshold = max(_REPEAT_MIN_PAGES, int(_REPEAT_MIN_FRAC * n))

    seen: Counter[str] = Counter()
    for p in pages:
        keys = {
            _norm(b.text)
            for b in p.blocks
            if b.bbox and b.text.strip() and b.type != "table"
            and (b.bbox[3] <= _HEADER_BAND * p.height or b.bbox[1] >= _FOOTER_BAND * p.height)
        }
        seen.update(keys)

    # **頁首帶的重複文字，第一次出現的那一份保留原型別。** 券商研報的文件標題
    # 常常同時是後續每頁的頁眉（凱基「台股一週大勢分析」、元富「公司拜訪快報」）：
    # 只看「重複＋位置」會把首頁那份真標題一起丟掉，E0 golden set 實測漏掉的
    # 7 句裡有 2 句正是這樣消失的。第一次出現保留、之後才判 header，標題留一份、
    # 樣板仍然去掉。頁尾不比照：免責聲明第一次出現也沒有保留價值。
    header_seen: set[str] = set()
    out: list[Page] = []
    for p in pages:
        blocks = []
        for b in p.blocks:
            new_type = b.type
            if b.bbox and b.type != "table" and seen[_norm(b.text)] >= threshold:
                if b.bbox[3] <= _HEADER_BAND * p.height:
                    key = _norm(b.text)
                    if key in header_seen:
                        new_type = "header"
                    header_seen.add(key)
                elif b.bbox[1] >= _FOOTER_BAND * p.height:
                    new_type = "footer"
            blocks.append(b if new_type == b.type else Block(**{**b.__dict__, "type": new_type}))
        out.append(Page(p.page_no, p.width, p.height, tuple(blocks), p.failed, p.error))
    return out


# ── 主入口 ──────────────────────────────────────────────────────────────────


def _page_blocks(page: Any, page_no: int) -> tuple[Block, ...]:
    width = float(page.width)
    height = float(page.height)
    words = page.extract_words(extra_attrs=["size"]) or []

    tables = _extract_tables(page)
    body_words = [w for w in words if not any(_inside(w, bb) for bb, _ in tables)]

    sizes = [float(w.get("size") or 0.0) for w in body_words if w.get("size")]
    med_size = statistics.median(sizes) if sizes else 0.0

    gutters = detect_columns(body_words, width, height)

    blocks: list[Block] = []
    order = 0
    # **先分行、再依溝槽切、最後才分欄**。逐「詞」分欄會把橫跨兩欄的大標
    # 拆進不同欄而變成兩個互不相干的段落；純頁面層級分行則會把左右兩欄
    # 同一個 y 的行併成一條。兩個錯誤都踩過，所以兩段都需要。
    span = (
        max(w["x1"] for w in body_words) - min(w["x0"] for w in body_words)
        if body_words
        else width
    )
    min_gap = _MIN_GUTTER_FRAC * span * _GUTTER_GAP_SLACK
    by_col: dict[int, list[list[dict]]] = {}
    for line in _group_lines(body_words):
        for run in _split_at_gutters(line, gutters, min_gap):
            rx0 = min(w["x0"] for w in run)
            rx1 = max(w["x1"] for w in run)
            by_col.setdefault(_column_of(rx0, rx1, gutters), []).append(run)

    for col in sorted(by_col):
        for para in _group_paragraphs(sorted(by_col[col], key=lambda ln: min(w["top"] for w in ln))):
            flat = [w for ln in para for w in ln]
            text = "\n".join(_line_text(ln) for ln in para)
            if not text.strip():
                continue
            bbox = (
                min(w["x0"] for w in flat),
                min(w["top"] for w in flat),
                max(w["x1"] for w in flat),
                max(w["bottom"] for w in flat),
            )
            psizes = [float(w.get("size") or 0.0) for w in flat if w.get("size")]
            size = statistics.median(psizes) if psizes else med_size
            blocks.append(
                Block(
                    type=_classify(text, bbox, size, med_size, height),
                    page_no=page_no,
                    order=order,
                    bbox=bbox,
                    text=text,
                    column=col,
                )
            )
            order += 1

    for bbox, rows in tables:
        blocks.append(
            Block(
                type="table",
                page_no=page_no,
                order=order,
                bbox=bbox,
                table_cells=rows,
                column=_column_of(bbox[0], bbox[2], gutters),
            )
        )
        order += 1

    blocks.sort(key=lambda b: b.sort_key)
    return tuple(blocks)


def extract_document(path: str | Path) -> Document:
    """PDF → Document。**逐頁失敗不中斷**，失敗的頁以 `failed=True` 留下。"""
    import pdfplumber  # 函式內 import：web 服務不該為它付載入成本

    path = Path(path)
    pages: list[Page] = []
    try:
        with pdfplumber.open(str(path)) as pdf:
            for i, page in enumerate(pdf.pages, start=1):
                try:
                    pages.append(
                        Page(i, float(page.width), float(page.height), _page_blocks(page, i))
                    )
                except Exception as exc:
                    # 現況是 `except Exception: continue`——整頁無聲消失。
                    # 這裡把它留成一筆可查詢的失敗（診斷 #2）。
                    logger.warning("page %d of %s failed: %s", i, path.name, exc)
                    pages.append(
                        Page(i, float(page.width), float(page.height), (), True, str(exc)[:200])
                    )
    except Exception as exc:
        return Document(EXTRACTOR_NAME, EXTRACTION_VERSION, (), error=str(exc)[:300])

    return Document(EXTRACTOR_NAME, EXTRACTION_VERSION, tuple(_mark_repeated_headers_footers(pages)))
