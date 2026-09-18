"""pdfplumber／pdfminer.six 版面分析 → `model.Document`。

對應 docs/EXTRACTION.md §4。生產路徑由 `extract.py` 的 pdfplumber 分支進來；另給
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
# 下限 0.15 不是 0.18：元富英文個股報告的側欄只占內容寬度 33%，溝槽中點落在
# 35.5%，0.18 會把它擋掉、整頁退回單欄（E0 實測）。內容寬度已經是墨跡的兩端，
# 15% 仍遠大於任何頁邊距。
_GUTTER_BAND = (0.15, 0.85)
# 每一欄至少要分到這麼多比例的詞，否則那條「溝槽」多半是一張置中的圖。
_MIN_COL_SHARE = 0.15
# 每一欄的墨跡寬度至少要占內容寬度這麼多，否則它不是一欄、是表格裡的一個窄欄位。
# 實測元大投資早報首頁：左側報告清單的「評等」欄只有 20pt 寬（買進／持有），
# 詞數卻夠多（每列一個），通過了 _MIN_COL_SHARE，於是整頁被切成三欄、每列的
# 「買進」被甩到另一條文字流。窄欄不是整頁退回單欄，而是**併回相鄰空隙較小的那一欄**
# ——那一頁真正的兩欄（清單 vs 目次）仍然要分開。
_MIN_COL_WIDTH_FRAC = 0.12
# 一欄裡「數字型 token」占比達此值就不是一欄，是側欄裡標籤右側對齊的數值欄
# （元富個股報告：目標價／52 週高低／成交量／PER 一整排靠右的數字）。它夠寬、
# 詞數也夠，寬度與詞數兩條都擋不住；只有內容擋得住——真的文字欄不會七成是數字。
_NUMERIC_COL_MAX_FRAC = 0.70
_NUMERIC_TOKEN = re.compile(r"^[\d.,%/()+\-–~:xX]+$")
# 帶單位後綴的數值（6M、12M、3.5x、20bp）也算數值 token。單一英文字母不算進分母——
# 那是圖表座標軸的字距散字（「J a n」「M a r」），不是文字欄的證據，卻會把數值占比
# 壓到門檻之下：v4 實測凱基美股個股頁的側欄數值欄占比 0.69 對門檻 0.70，差這一點
# 就被判成獨立一欄，「12 個月目標價」與「47.3」從此分家（全庫 1,177 篇 max_columns=3）。
_NUMERIC_UNIT_TOKEN = re.compile(r"^[(\[]?[+\-–]?\d[\d.,]*[)\]]?[A-Za-z%]{1,2}$")
_SINGLE_LETTER = re.compile(r"^[A-Za-z]$")
# 窄數值側欄：寬度低於內容寬度這個比例、且數值占比達此值，就是側欄裡靠右對齊的數值欄，
# 一律併回左邊的標籤欄。比 _NUMERIC_COL_MAX_FRAC 寬鬆是因為多了「窄」這個條件。
_NUMERIC_SIDEBAR_MAX_WIDTH_FRAC = 0.20
_NUMERIC_SIDEBAR_MIN_FRAC = 0.50
# 溝槽驗證：兩側都有字的行裡，若超過這個比例是「連續穿過溝槽」（穿越處沒有 min_gap
# 寬的間隙），那條溝槽是段落內部的稀疏帶不是欄界。投影容許 4% 跨欄詞之後，只有幾行
# 的段落（大摩首頁右上角的問卷提示）會在自己內部投影出一條假溝槽，段落於是被腰斬成
# 兩欄。真雙欄頁的穿越行只有跨欄大標，占比遠低於此。
_GUTTER_CROSSING_MAX_FRAC = 0.5
# 相鄰詞合併：同一行、水平間隙小於此值（含重疊）的兩個詞是同一個詞。券商 PDF 用
# 空白字元做右對齊填充，填充的空白會覆蓋在數字上，pdfplumber 把「50,009.35」斷成
# 「5」與「0,009.35」——v3 全庫抽樣 30 份 277 頁，數值 token 有 16–64% 被切開。
# 真正的詞距即使在窄體字也有 1pt 以上；0.5pt 以內只可能是同一個詞。
_TOUCHING_GAP = 0.5
# 重疊超過這麼多的不是同一個詞被切開，是兩個疊在一起的物件（浮水印壓在正文上）。
# 填充空白造成的切開只重疊 0.1pt 左右，字距微調（kerning）也在 1pt 內。
_TOUCHING_MAX_OVERLAP = 1.5
_TOUCHING_LINE_TOL = 1.0
# 圖區：曲線、影像、細長矩形（長條圖的 bar）聚成的區域。區域內的短數值 token 是座標軸
# 刻度、圖例與資料標籤，不是正文：它們讓 chunk 充滿「50 40 30 20 10 0」，也是元大圖表頁
# 被判成三欄的原因。表格的框線是 line、底色是寬矩形，都不在這裡的取材範圍內；細矩形
# 還要求高度不一（bar），排除表格窄欄的逐列底色。
_FIG_MERGE_PAD = 6.0
_FIG_MIN_CURVES = 8  # 折線／圓餅至少由這麼多段小基元組成；側欄的幾個圓角色塊湊不到
_FIG_MIN_BARS = 5
_FIG_MIN_W, _FIG_MIN_H = 50.0, 30.0
_FIG_MAX_PAGE_FRAC = 0.6  # 超過頁面六成的區域是全頁背景圖，不是圖表
_FIG_PRIM_MAX_PAGE_FRAC = 0.03  # 單一基元超過頁面 3% 是色塊或裁切路徑，不是圖的一部分
_FIG_PRIM_MAX_W_FRAC = 0.30  # 小基元：寬度不超過頁寬三成（側欄標題色塊有四成寬）
_FIG_BAR_MIN_W, _FIG_BAR_MAX_W = 3.0, 25.0  # 1pt 寬的是分隔線，不是 bar
_FIG_BAR_HEIGHT_SPREAD = 1.5
# 區域內長度不超過此值的 token 視為刻度、圖例、座標軸標籤（「Nov-24」「FY22」「女裝」），
# 只留長字串（圖說、資料來源）。散文極少壓在繪圖基元上，代價可接受。
_FIG_NOISE_MAX_CHARS = 6
# 在溝槽處要有多寬的間隙才算「這裡真的是欄界」，以溝槽最小寬度為單位。
# 跨欄大標在溝槽處只有一個字距（9–12pt 字約 2.5–4pt），而欄界處的留白通常接近整條溝槽寬。
# v3 取 0.5（A4 約 10pt）；v4 降到 0.3（約 6pt）：分行改用中點分群後，左欄長行與右欄表格列
# 更常落在同一行，左欄文字侵入溝槽時局部留白只剩 7–8pt，0.5 會讓整行跨欄、整段被排到
# 跨欄帶之後（元富 3008 快報實測）。6pt 仍是字距的 1.5–2 倍。
_GUTTER_GAP_SLACK = 0.3
# 欄數上限。研報實務上是 1 或 2 欄；判到 4 欄幾乎都是表格被誤認。
_MAX_COLS = 3
# 投影只取版心：頁首、跨欄大標、頁尾都會橫跨溝槽，把它們算進去會讓
# **每一頁的雙欄都偵測不到**。這是這段最容易犯的錯。
_BODY_BAND = (0.12, 0.90)
# 版心帶**內**也會有少數跨欄元素（元富個股報告的結論列、速報中段的橫幅表格）。
# 投影若是布林「有沒有墨」，4 個跨欄詞就把整條溝槽填滿、整頁退回單欄——E0 實測
# 元富三份首頁全部如此（跨欄詞占全頁 0.8–2.9%），側欄與主文於是依 y 交錯。
# 改為計數：一格被不超過這個比例的詞蓋到，仍算空白。散文單欄頁的每一格都被
# 幾乎每一行蓋到，不會因此冒出假溝槽；跨欄詞本身之後由 _column_of 判成跨欄。
_GUTTER_MAX_CROSS_FRAC = 0.04
# 頁首脫離：頁首帶內的右欄區塊，要與同欄下一個區塊相距超過頁高這個比例才歸第 0 欄。
# 實測元富「公司拜訪快報」到右欄首段相距 111pt（13%）、「個股報告／HOLD」99pt；
# 而版心從頁頂開始的頁面，右欄首段到次段只有一行高（<2%）。
_HEADER_DETACH_GAP_FRAC = 0.05

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
# 框線表的兩種已知失敗（v4 實測凱基投資早報）：
# (a) **殘缺**——框線只圈到表格的一部分，框外同列還有數值（YTD 整欄掉到框外變成獨立段落，
#     從此與它的列失聯）。判準：同列 y 範圍內、框外 3–60pt 處的數值詞達列數一半。
# (b) **欄位不足**——框線少於實際欄數，pdfplumber 把四個數值塞進一格
#     （「| 3.4 6.6 2.6 1.3 |」）。判準：含 3 個以上數值 token 的儲存格占比。
# 兩種都先用文字對齊策略在（放寬後的）同一區域重抽一次，欄數變多才採用；否則整張
# 退回文字流——每一列仍是一行、數值仍在自己的列上，比一張少一欄的表格誠實。
_TABLE_BESIDE_MIN_GAP, _TABLE_BESIDE_MAX_GAP = 3.0, 60.0
_TABLE_BESIDE_MIN_WORDS = 3
_TABLE_UNDERSEG_MAX_CELL_FRAC = 0.25
_CELL_NUMBER = re.compile(r"(?<!\S)[(\-+]?\d[\d,.]*%?\)?(?!\S)")
# 詞重建表格（_words_table）的欄界：正文列上沒有任何詞跨過、至少這麼寬的垂直空隙。
# 相鄰欄的數值即使靠右對齊也隔 6pt 以上；同一儲存格內兩個詞之間是一個字距（約 2pt）。
_TABLE_COL_MIN_GAP = 4.0

_WS = re.compile(r"\s+")


def _is_numeric_token(text: str) -> bool:
    return bool(_NUMERIC_TOKEN.match(text) or _NUMERIC_UNIT_TOKEN.match(text))


def _merge_touching_words(words: list[dict]) -> tuple[list[dict], int]:
    """把同一行、水平間隙落在 (−`_TOUCHING_MAX_OVERLAP`, `_TOUCHING_GAP`) 的相鄰詞合併。
    回 (詞, 合併次數)。重疊太多的是兩個疊在一起的物件（浮水印壓在正文上），不併。"""
    ordered = sorted(words, key=lambda w: (round(w["top"], 1), w["x0"]))
    out: list[dict] = []
    merged = 0
    for w in ordered:
        if out:
            p = out[-1]
            gap = w["x0"] - p["x1"]
            if abs(w["top"] - p["top"]) <= _TOUCHING_LINE_TOL and -_TOUCHING_MAX_OVERLAP < gap < _TOUCHING_GAP:
                out[-1] = {
                    **p,
                    "text": p["text"] + w["text"],
                    "x1": max(p["x1"], w["x1"]),
                    "top": min(p["top"], w["top"]),
                    "bottom": max(p["bottom"], w["bottom"]),
                }
                merged += 1
                continue
        out.append(dict(w))
    return out, merged


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

    counts = [0] * _COLS_BINS
    for w in body:
        a = int((w["x0"] - x0) / span * (_COLS_BINS - 1))
        b = int((w["x1"] - x0) / span * (_COLS_BINS - 1))
        for i in range(max(0, a), min(_COLS_BINS - 1, b) + 1):
            counts[i] += 1
    max_cross = int(_GUTTER_MAX_CROSS_FRAC * len(body))
    covered = [c > max_cross for c in counts]

    min_bins = max(1, int(_MIN_GUTTER_FRAC * _COLS_BINS))
    lo = int(_GUTTER_BAND[0] * _COLS_BINS)
    hi = int(_GUTTER_BAND[1] * _COLS_BINS)

    def _gutter_x(a: int, b: int) -> float:
        # 溝槽位置取 run 內**完全無墨**最長一段的中點，而不是整個 run 的中點：
        # 容許跨欄詞之後，run 的兩端會含有被項目符號或縮排蓋到的格子，取整段中點
        # 會把溝槽推進主文的縮排裡，主文首行於是被 _column_of 判成跨欄（E0 實測
        # 元富穩懋那份就是這樣）。
        best_a, best_len, cur_a = a, 0, None
        for i in range(a, b + 1):
            if i < b and counts[i] == 0:
                cur_a = i if cur_a is None else cur_a
                continue
            if cur_a is not None and i - cur_a > best_len:
                best_a, best_len = cur_a, i - cur_a
            cur_a = None
        mid = (best_a + best_a + best_len) / 2 if best_len else (a + b) / 2
        return x0 + mid / (_COLS_BINS - 1) * span

    gutters: list[float] = []
    run_start: int | None = None
    for i in range(_COLS_BINS):
        if not covered[i]:
            run_start = i if run_start is None else run_start
            continue
        if run_start is not None:
            if i - run_start >= min_bins and lo <= (run_start + i) // 2 <= hi:
                gutters.append(_gutter_x(run_start, i))
            run_start = None
    if run_start is not None and _COLS_BINS - run_start >= min_bins:
        mid = (run_start + _COLS_BINS) // 2
        if lo <= mid <= hi:
            gutters.append(_gutter_x(run_start, _COLS_BINS))

    if not gutters or len(gutters) >= _MAX_COLS:
        return []

    # 太窄或詞數太少的欄不是一欄——併回鄰欄，**不是整頁退回單欄**：元富英文個股報告
    # 的側欄裡「標籤…數值」之間有一條稀疏帶，會被投影成第二條溝槽；若因此整頁放棄，
    # 真正的側欄／主文分界就跟著丟了（E0 實測）。
    gutters = _merge_weak_columns(gutters, body, x0, x1)
    if not gutters:
        return []
    # 最後用行級證據驗證：與 `_page_blocks` 同一個 min_gap 口徑。
    min_gap = _MIN_GUTTER_FRAC * span * _GUTTER_GAP_SLACK
    return _reject_crossed_gutters(gutters, _group_lines(body), min_gap)


def _reject_crossed_gutters(gutters: list[float], lines: list[list[dict]], min_gap: float) -> list[float]:
    """丟掉被文字連續穿過的溝槽。

    對每條溝槽只看「兩側都有字」的行：若其中超過 `_GUTTER_CROSSING_MAX_FRAC` 的行在
    穿越處沒有 `min_gap` 寬的間隙（或有詞直接橫跨），那是段落自己的內部空隙被投影
    成了溝槽。真雙欄頁上兩側都有字的行幾乎是全部的行，而連續穿過的只有跨欄大標。"""
    kept: list[float] = []
    for g in gutters:
        both = crossing = 0
        for ln in lines:
            left = [w["x1"] for w in ln if w["x1"] <= g]
            right = [w["x0"] for w in ln if w["x0"] >= g]
            spanning = any(w["x0"] < g < w["x1"] for w in ln)
            if not (spanning or (left and right)):
                continue
            both += 1
            if spanning or (min(right) - max(left)) < min_gap:
                crossing += 1
        if both and crossing / both > _GUTTER_CROSSING_MAX_FRAC:
            continue
        kept.append(g)
    return kept


def _merge_weak_columns(gutters: list[float], body: list[dict], x0: float, x1: float) -> list[float]:
    """把「弱欄」併回鄰欄，直到每一欄都夠寬（_MIN_COL_WIDTH_FRAC）、夠多詞（_MIN_COL_SHARE）
    且不是純數值欄（_NUMERIC_COL_MAX_FRAC）。

    併的方向是**空隙較小的那一側**：窄欄通常是表格裡被切出來的一個欄位，它離自己
    的表比離隔壁欄近。全部併光就回空 list（單欄）。"""
    span = x1 - x0
    gutters = list(gutters)
    while gutters:
        edges = [x0 - 1.0, *gutters, x1 + 1.0]
        ink: list[tuple[float, float] | None] = []
        shares: list[float] = []
        for a, b in zip(edges, edges[1:]):
            ws = [w for w in body if a <= (w["x0"] + w["x1"]) / 2 < b]
            ink.append((min(w["x0"] for w in ws), max(w["x1"] for w in ws)) if ws else None)
            shares.append(len(ws) / len(body))
        numeric: list[float] = []
        for a, b in zip(edges, edges[1:]):
            # 分母排除單一英文字母（圖表座標軸的字距散字），見 _SINGLE_LETTER 的說明。
            ws = [
                w for w in body
                if a <= (w["x0"] + w["x1"]) / 2 < b and not _SINGLE_LETTER.match(w.get("text", ""))
            ]
            numeric.append(sum(1 for w in ws if _is_numeric_token(w.get("text", ""))) / len(ws) if ws else 0.0)
        narrow_numeric = [
            i > 0
            and k is not None
            and (k[1] - k[0]) < _NUMERIC_SIDEBAR_MAX_WIDTH_FRAC * span
            and numeric[i] >= _NUMERIC_SIDEBAR_MIN_FRAC
            for i, k in enumerate(ink)
        ]
        weak = [
            i
            for i, k in enumerate(ink)
            if k is None
            or (k[1] - k[0]) < _MIN_COL_WIDTH_FRAC * span
            or shares[i] < _MIN_COL_SHARE
            or numeric[i] >= _NUMERIC_COL_MAX_FRAC
            or narrow_numeric[i]
        ]
        if not weak:
            return gutters
        i = weak[0]
        # 數值欄一律併回**左邊**：它是標籤右側對齊的數值，屬於左邊的標籤，不看空隙
        # （空隙常常反而是往主文那側較小，實測元富三份都會併錯邊）。窄數值側欄同理。
        if i > 0 and (numeric[i] >= _NUMERIC_COL_MAX_FRAC or narrow_numeric[i]):
            gutters.pop(i - 1)
            continue
        candidates: list[tuple[float, int]] = []  # (空隙寬, 要移除的溝槽索引)
        if i > 0:
            gap = (ink[i][0] if ink[i] else gutters[i - 1]) - (ink[i - 1][1] if ink[i - 1] else gutters[i - 1])
            candidates.append((gap, i - 1))
        if i < len(ink) - 1:
            gap = (ink[i + 1][0] if ink[i + 1] else gutters[i]) - (ink[i][1] if ink[i] else gutters[i])
            candidates.append((gap, i))
        gutters.pop(min(candidates)[1])
    return gutters


def _spans(x0: float, x1: float, gutters: list[float], slack: float) -> bool:
    """區間是否「真的」橫跨溝槽：兩側都要伸出溝槽至少 `slack`。左欄的長行常侵入溝槽幾 pt，
    沒有這個容差會被當成跨欄元素、整段被排進跨欄帶（元富 3008 快報實測，v4）。"""
    return any(x0 < g - slack and x1 > g + slack for g in gutters)


def _column_of(x0: float, x1: float, gutters: list[float], slack: float = 0.0) -> int:
    """區間 → 欄索引。**橫跨溝槽者一律回 0。**

    跨欄元素（大標、橫幅表格）沒有「屬於哪一欄」這回事。回 0 讓它落在左欄
    的流裡、依 y 排到該去的位置——對「置頂的跨欄大標」是正確的，對「夾在
    中間的橫幅表格」則是已知的近似（真正的閱讀順序需要把頁面切成跨欄帶與
    分欄帶，那超出本輪範圍）。

    **不可以改用中點判定**：橫幅元素的中點必然落在溝槽右側，於是整條大標會
    被排到右欄最後面。`slack` 見 `_spans`。
    """
    if _spans(x0, x1, gutters, slack):
        return 0
    mid = (x0 + x1) / 2
    idx = 0
    for g in gutters:
        if mid >= g:
            idx += 1
    return idx


def _run_column(run: list[dict], gutters: list[float]) -> int:
    """一段同行的詞 → 欄索引：詞的中點分布在溝槽兩側才算跨欄（回 0），否則依中點的中位數歸欄。
    用詞中點而不是區間端點，長行侵入溝槽幾 pt 不會被誤判成跨欄。"""
    centers = sorted((w["x0"] + w["x1"]) / 2 for w in run)
    if any(centers[0] < g < centers[-1] for g in gutters):
        return 0
    mid = centers[len(centers) // 2]
    return sum(1 for g in gutters if mid >= g)


def _page_lines(words: list[dict], gutters: list[float], min_gap: float) -> list[list[dict]]:
    """整頁的詞 → 行，先分欄再分行、再把跨欄大標接回來。

    v3 是整頁一起分行、再依溝槽切：不同欄字級不同時（高盛首頁主文 10pt、側欄 Key Data
    6.7pt 且列距 8pt），主文一行的垂直範圍蓋到側欄兩列，「Market cap」與「Enterprise value」
    被絞成一行、詞依 x 交錯（v4 實測）。改成：每個詞依中點分到暫定欄，各欄自己分行（行距
    容差只看該欄自己的字級），最後把相鄰欄「垂直中點一致、且溝槽處留白小於 `min_gap`」的
    兩行接回一行——那是橫跨兩欄的大標，接回後由 `_split_at_gutters`／`_run_column` 判成跨欄。
    真雙欄的兩行在溝槽處留白遠大於 `min_gap`，不會被接。"""
    if not gutters:
        return _group_lines(words)
    cols: dict[int, list[dict]] = {}
    for w in words:
        cols.setdefault(sum(1 for g in gutters if (w["x0"] + w["x1"]) / 2 >= g), []).append(w)
    per = {c: _group_lines(ws) for c, ws in cols.items()}

    def _center(ln: list[dict]) -> float:
        return statistics.median((w["top"] + w["bottom"]) / 2 for w in ln)

    out: list[list[dict]] = []
    carry = per.get(0, [])
    for c in range(1, len(gutters) + 1):
        right = per.get(c, [])
        taken: set[int] = set()
        rejoined: list[list[dict]] = []
        for left in carry:
            lc = _center(left)
            tol = _LINE_TOL_FRAC * statistics.median(w["bottom"] - w["top"] for w in left)
            lx1 = max(w["x1"] for w in left)
            hit = None
            for j, rl in enumerate(right):
                if j in taken:
                    continue
                if abs(_center(rl) - lc) <= tol and min(w["x0"] for w in rl) - lx1 < min_gap:
                    hit = j
                    break
            if hit is None:
                out.append(left)
            else:
                taken.add(hit)
                rejoined.append(left + right[hit])
        carry = [rl for j, rl in enumerate(right) if j not in taken] + rejoined
    out.extend(carry)
    return out


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
        # 以兩個詞的中點判「分處溝槽兩側」，不要求前一個詞完全在溝槽左邊：左欄的長行
        # 常侵入溝槽幾 pt，用 x1 判會漏掉，整行於是跨欄（v4 實測）。
        pm, cm = (prev["x0"] + prev["x1"]) / 2, (cur["x0"] + cur["x1"]) / 2
        crossed = any(pm < g < cm for g in gutters)
        if crossed and (cur["x0"] - prev["x1"]) >= min_gap:
            runs.append([cur])
        else:
            runs[-1].append(cur)
    return runs


def _group_lines(words: list[dict]) -> list[list[dict]]:
    """同一欄內的詞 → 行。

    以**垂直中點**對行內中點的**中位數**分群，不比 `top`、也不只比行首詞：同一列裡中文
    標籤與拉丁數字的字框高度不同（7.6pt 對 9.5pt 的符號字），比 top 會差到門檻邊緣；只比
    行首詞則一個高字框的雜訊字元（凱基側欄每列前的隱形符號）就把整列拆成兩行，標籤與數值
    再度分家。用中位數不用平均：平均會被同列的一個高字框（高盛 Key Data 框的邊線字元）
    拉向下一列，把「Market cap」與「Enterprise value」兩列絞成一行（v4 實測兩者都踩過）。"""
    if not words:
        return []
    heights = [w["bottom"] - w["top"] for w in words if w["bottom"] > w["top"]]
    tol = _LINE_TOL_FRAC * (statistics.median(heights) if heights else 10.0)
    ordered = sorted(words, key=lambda w: (round((w["top"] + w["bottom"]) / 2, 2), round(w["x0"], 2)))
    lines: list[list[dict]] = []
    centers: list[list[float]] = []
    for w in ordered:
        c = (w["top"] + w["bottom"]) / 2
        if lines and abs(c - statistics.median(centers[-1])) <= tol:
            lines[-1].append(w)
            centers[-1].append(c)
        else:
            lines.append([w])
            centers.append([c])
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


def _numeric_words_beside(bbox: tuple[float, float, float, float], words: list[dict]) -> list[dict]:
    """同列 y 範圍內、落在框外 3–60pt 處的數值詞——框線表**殘缺**的證據（見 _TABLE_BESIDE_*）。"""
    out: list[dict] = []
    for w in words:
        cy = (w["top"] + w["bottom"]) / 2
        if not (bbox[1] <= cy <= bbox[3]):
            continue
        gap_left = bbox[0] - w["x1"]
        gap_right = w["x0"] - bbox[2]
        beside = (
            _TABLE_BESIDE_MIN_GAP <= gap_left <= _TABLE_BESIDE_MAX_GAP
            or _TABLE_BESIDE_MIN_GAP <= gap_right <= _TABLE_BESIDE_MAX_GAP
        )
        if beside and _is_numeric_token(w.get("text", "")):
            out.append(w)
    return out


def _underseg_cell_frac(rows: tuple[tuple[str, ...], ...]) -> float:
    """含 3 個以上數值 token 的儲存格占非空儲存格的比例——**欄位不足**的證據。"""
    cells = [c for r in rows for c in r if c and c.strip()]
    if not cells:
        return 0.0
    return sum(1 for c in cells if len(_CELL_NUMBER.findall(c)) >= 3) / len(cells)


def _words_table(
    words: list[dict], bbox: tuple[float, float, float, float]
) -> tuple[tuple[str, ...], ...] | None:
    """用（已合併的）詞在 bbox 區域重建表格：列＝行，欄＝所有正文列都沒有詞跨過的垂直空隙。

    不用 pdfplumber 的文字策略重抽：它以字元定欄界，右對齊填充的空白字元會讓欄界
    落在數字中間（「道瓊指數 5 | 0,009.35」，v4 實測）。詞是 `_merge_touching_words`
    之後的單位，欄界只可能落在詞與詞之間。欄界用第二列起的正文算——表頭常橫跨數欄，
    算進去會把欄併掉；表頭詞之後依中點歸欄。回 None ＝ 湊不成可接受的表。"""
    inside = [w for w in words if _inside(w, bbox)]
    lines = _group_lines(inside)
    if len(lines) < _TEXT_TABLE_MIN_ROWS:
        return None
    body = lines[1:] if len(lines) > 2 else lines
    x0 = min(w["x0"] for ln in lines for w in ln)
    x1 = max(w["x1"] for ln in lines for w in ln)
    res = 0.5
    n = int((x1 - x0) / res) + 1
    occ = [False] * n
    for ln in body:
        for w in ln:
            a, b = int((w["x0"] - x0) / res), int((w["x1"] - x0) / res)
            for i in range(max(0, a), min(n - 1, b) + 1):
                occ[i] = True
    bounds: list[float] = []
    run: int | None = None
    for i in range(n):
        if not occ[i]:
            run = i if run is None else run
            continue
        if run is not None and run > 0 and (i - run) * res >= _TABLE_COL_MIN_GAP:
            bounds.append(x0 + (run + i) / 2 * res)
        run = None
    ncols = len(bounds) + 1
    rows: list[tuple[str, ...]] = []
    for ln in lines:
        cells: list[list[str]] = [[] for _ in range(ncols)]
        for w in sorted(ln, key=lambda w: w["x0"]):
            c = (w["x0"] + w["x1"]) / 2
            cells[sum(1 for b in bounds if c >= b)].append(w["text"])
        rows.append(tuple(" ".join(c) for c in cells))
    return _accept(rows, _TEXT_TABLE_MIN_ROWS, _TEXT_TABLE_MIN_FILL, need_regular=False)


def _extract_tables(
    page: Any, words: list[dict] | None = None, stats: dict[str, int] | None = None
) -> list[tuple[tuple[float, float, float, float], tuple[tuple[str, ...], ...]]]:
    """兩種策略依序試：框線優先，再用文字對齊補無框線表。

    **券商研報大量使用無框線表格**——實測凱基投資早報 31 頁只有 2 張表有
    框線，而目標價／評等一覽表全都是靠文字對齊排的。只跑預設的框線策略，
    診斷 #5（表格欄界被抹掉）根本沒有機會被觸發，因為表格壓根沒被認出來。

    代價是文字策略會誤判：它會把「兩段左右並排的文字」看成兩欄表格。所以
    它的驗收比框線策略嚴（要求更多列、更高填充率、且欄數整齊），而且**只
    在不與框線表重疊的區域採用**——框線表是比較可信的證據。

    框線表通過驗收後還要過兩道體檢（殘缺、欄位不足，見 _TABLE_BESIDE_* 的說明）：
    可疑的先在放寬後的同一區域用文字策略重抽，欄數變多才採用，否則整張退回
    文字流。`stats` 記 `tables_suspect`／`tables_retried`／`tables_rejected`。
    """
    out: list[tuple[tuple[float, float, float, float], tuple[tuple[str, ...], ...]]] = []
    if words is None:
        words = page.extract_words() or []
    if stats is None:
        stats = {}

    for t in _find(page, None):
        try:
            rows = t.extract()
        except Exception:
            continue
        norm = _accept(rows, _TABLE_MIN_ROWS, _TABLE_MIN_FILL, need_regular=False)
        if norm is None:
            continue
        bbox = _bbox4(t.bbox)
        beside = _numeric_words_beside(bbox, words)
        truncated = len(beside) >= max(_TABLE_BESIDE_MIN_WORDS, len(norm) // 2)
        underseg = _underseg_cell_frac(norm) >= _TABLE_UNDERSEG_MAX_CELL_FRAC
        if not (truncated or underseg):
            out.append((bbox, norm))
            continue
        stats["tables_suspect"] = stats.get("tables_suspect", 0) + 1
        wide = (
            min([bbox[0], *(w["x0"] for w in beside)]),
            bbox[1],
            max([bbox[2], *(w["x1"] for w in beside)]),
            bbox[3],
        )
        retry = _words_table(words, wide)
        if retry is not None and max(len(r) for r in retry) > max(len(r) for r in norm):
            out.append((wide, retry))
            stats["tables_retried"] = stats.get("tables_retried", 0) + 1
        else:
            stats["tables_rejected"] = stats.get("tables_rejected", 0) + 1

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


# ── 圖區 ────────────────────────────────────────────────────────────────────


_BBox = tuple[float, float, float, float]


def _figure_regions(page: Any, exclude: list[_BBox]) -> list[_BBox]:
    """把頁面上的繪圖基元聚成圖區（見 _FIG_* 的說明）。

    取材：`curves`、斜線（折線圖的資料線在 pdfminer 是一段段 LTLine，寬高皆大於 1pt；
    表格框線與格線是水平或垂直的，寬或高為 0）、細長矩形（長條圖的 bar）。
    **刻意不取 `images`**：券商版型把側欄底色做成一張點陣圖墊在文字下面（凱基美股個股頁
    的整個側欄），把它當圖區會把目標價、市值整排短詞當刻度丟掉（v4 實測）；點陣圖表的
    刻度文字本來就在圖外，取它也沒有增益。
    聚合用 12pt 格子的 union-find，等價於「外擴 pad 後重疊就併」，但對上千段折線
    仍是線性時間。`exclude`（已抽到的表格 bbox）內的基元不取。
    """
    pw, ph = float(page.width), float(page.height)
    page_area = pw * ph
    if page_area <= 0:
        return []
    px0, ptop, px1, pbottom = (float(v) for v in page.bbox)
    prims: list[tuple[str, tuple[float, float, float, float]]] = []

    def _add(kind: str, objs) -> None:
        for o in objs or []:
            try:
                b = (float(o["x0"]), float(o["top"]), float(o["x1"]), float(o["bottom"]))
            except (KeyError, TypeError, ValueError):
                continue
            # 超出頁面的是裁切路徑或背景，不是圖的一部分
            if b[0] < px0 - 1 or b[1] < ptop - 1 or b[2] > px1 + 1 or b[3] > pbottom + 1:
                continue
            if (b[2] - b[0]) * (b[3] - b[1]) > _FIG_PRIM_MAX_PAGE_FRAC * page_area:
                continue
            if kind == "curve" and (b[2] - b[0]) > _FIG_PRIM_MAX_W_FRAC * pw:
                continue
            if any(_overlaps(b, t) for t in exclude):
                continue
            prims.append((kind, b))

    _add("curve", getattr(page, "curves", None))
    _add(
        "curve",
        [
            ln for ln in (getattr(page, "lines", None) or [])
            if float(ln["x1"]) - float(ln["x0"]) > 1.0 and float(ln["bottom"]) - float(ln["top"]) > 1.0
        ],
    )
    _add(
        "bar",
        [
            r for r in (getattr(page, "rects", None) or [])
            if _FIG_BAR_MIN_W <= float(r["x1"]) - float(r["x0"]) <= _FIG_BAR_MAX_W
            and float(r["bottom"]) - float(r["top"]) > 1.0
        ],
    )
    if not prims:
        return []

    cell = 12.0
    pad = _FIG_MERGE_PAD
    parent = list(range(len(prims)))

    def _find_root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    grid: dict[tuple[int, int], int] = {}
    for idx, (_kind, b) in enumerate(prims):
        for gx in range(int((b[0] - pad) // cell), int((b[2] + pad) // cell) + 1):
            for gy in range(int((b[1] - pad) // cell), int((b[3] + pad) // cell) + 1):
                other = grid.setdefault((gx, gy), idx)
                if other != idx:
                    ra, rb = _find_root(idx), _find_root(other)
                    if ra != rb:
                        parent[rb] = ra

    groups: dict[int, list[int]] = {}
    for idx in range(len(prims)):
        groups.setdefault(_find_root(idx), []).append(idx)

    out: list[tuple[float, float, float, float]] = []
    for members in groups.values():
        boxes = [prims[i][1] for i in members]
        b = (min(x[0] for x in boxes), min(x[1] for x in boxes), max(x[2] for x in boxes), max(x[3] for x in boxes))
        if (b[2] - b[0]) < _FIG_MIN_W or (b[3] - b[1]) < _FIG_MIN_H:
            continue
        if (b[2] - b[0]) * (b[3] - b[1]) > _FIG_MAX_PAGE_FRAC * page_area:
            continue
        kinds = Counter(prims[i][0] for i in members)
        heights = [prims[i][1][3] - prims[i][1][1] for i in members if prims[i][0] == "bar"]
        bars_vary = (
            len(heights) >= _FIG_MIN_BARS
            and max(heights) / max(min(heights), 0.01) >= _FIG_BAR_HEIGHT_SPREAD
        )
        if kinds["curve"] >= _FIG_MIN_CURVES or bars_vary:
            out.append(b)
    return out


def _is_chart_noise(word: dict) -> bool:
    """圖區內視為座標軸刻度／圖例散字的詞：數值 token，或長度不超過 _FIG_NOISE_MAX_CHARS。"""
    t = word.get("text", "")
    return _is_numeric_token(t) or len(t) <= _FIG_NOISE_MAX_CHARS


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


def _page_blocks(page: Any, page_no: int) -> tuple[tuple[Block, ...], dict[str, int]]:
    """一頁 → (Block, 統計)。統計鍵：words_raw、words_merged、chart_words_dropped、
    tables_suspect、tables_retried、tables_rejected；由 `extract_document` 加總進
    `Document.meta["layout"]`，`quality.measure` 再寫進 quality_flags。"""
    width = float(page.width)
    height = float(page.height)
    stats: dict[str, int] = {}
    raw_words = page.extract_words(extra_attrs=["size"]) or []
    words, merged = _merge_touching_words(raw_words)
    stats["words_raw"] = len(raw_words)
    stats["words_merged"] = merged

    tables = _extract_tables(page, words, stats)
    table_boxes = [bb for bb, _ in tables]
    figures = _figure_regions(page, table_boxes)
    body_words: list[dict] = []
    dropped = 0
    for w in words:
        if any(_inside(w, bb) for bb in table_boxes):
            continue
        if figures and _is_chart_noise(w) and any(_inside(w, fb) for fb in figures):
            dropped += 1
            continue
        body_words.append(w)
    stats["chart_words_dropped"] = dropped

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
    for line in _page_lines(body_words, gutters, min_gap):
        for run in _split_at_gutters(line, gutters, min_gap):
            by_col.setdefault(_run_column(run, gutters), []).append(run)

    # 候選：(col, bbox, text, size, table_rows)。先全部收齊再決定欄與帶，因為
    # 「頁首脫離」與「跨欄帶」都需要看到整頁其他區塊的位置。
    cands: list[tuple[int, tuple[float, float, float, float], str, float, tuple | None]] = []
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
            cands.append((col, bbox, text, size, None))
    for bbox, rows in tables:
        cands.append((_column_of(bbox[0], bbox[2], gutters, min_gap), bbox, "", med_size, rows))

    # 頁首脫離：頁首帶（版心帶之上）落在右欄、且與同欄下一個區塊有明顯間距的區塊，
    # 歸第 0 欄——報告類型、券商名這類東西在閱讀上先於任何一欄。「有明顯間距」
    # 這個條件不可省：版心從頁頂開始的頁面，右欄首段也在頁首帶內，沒有間距條件
    # 會把它搬到左欄前面。
    head_limit = _BODY_BAND[0] * height
    detach_gap = _HEADER_DETACH_GAP_FRAC * height
    cols_final: list[int] = []
    for idx, (col, bbox, _text, _size, _rows) in enumerate(cands):
        if col > 0 and bbox[3] <= head_limit:
            below = [c[1][1] for j, c in enumerate(cands) if j != idx and c[0] == col and c[1][1] >= bbox[3]]
            if not below or min(below) - bbox[3] > detach_gap:
                col = 0
        cols_final.append(col)

    # 跨欄帶：跨過溝槽的區塊（橫幅表格、跨欄大標）把頁面切成上下幾段，段內才分欄。
    # 沒有它，「跨欄＝第 0 欄」會讓頁尾的橫幅表格排到右欄整段之前。
    spanning = sorted(
        (bbox[1], bbox[3])
        for (col, bbox, _t, _s, _r) in cands
        if gutters and _spans(bbox[0], bbox[2], gutters, min_gap)
    )

    def _band(bbox: tuple[float, float, float, float]) -> int:
        mid = (bbox[1] + bbox[3]) / 2
        is_span = any(abs(bbox[1] - t) < 1e-6 and abs(bbox[3] - b) < 1e-6 for t, b in spanning)
        if is_span:
            return 2 * next(i for i, (t, b) in enumerate(spanning) if abs(bbox[1] - t) < 1e-6) + 1
        return 2 * sum(1 for _t, b in spanning if b <= mid)

    for (col, bbox, text, size, rows), col_f in zip(cands, cols_final):
        if rows is None:
            blocks.append(
                Block(
                    type=_classify(text, bbox, size, med_size, height),
                    page_no=page_no,
                    order=order,
                    bbox=bbox,
                    text=text,
                    column=col_f,
                    band=_band(bbox),
                )
            )
        else:
            blocks.append(
                Block(
                    type="table",
                    page_no=page_no,
                    order=order,
                    bbox=bbox,
                    table_cells=rows,
                    column=col_f,
                    band=_band(bbox),
                )
            )
        order += 1

    blocks.sort(key=lambda b: b.sort_key)
    return tuple(blocks), stats


def extract_document(path: str | Path) -> Document:
    """PDF → Document。**逐頁失敗不中斷**，失敗的頁以 `failed=True` 留下。

    `Document.meta["layout"]` 是全文件加總的版面統計（鍵見 `_page_blocks`）；它不進序列化，
    只供 `quality.measure` 寫進 quality_flags。"""
    import pdfplumber  # 函式內 import：web 服務不該為它付載入成本

    path = Path(path)
    pages: list[Page] = []
    layout_stats: Counter[str] = Counter()
    try:
        with pdfplumber.open(str(path)) as pdf:
            for i, page in enumerate(pdf.pages, start=1):
                try:
                    blocks, stats = _page_blocks(page, i)
                    layout_stats.update(stats)
                    pages.append(Page(i, float(page.width), float(page.height), blocks))
                except Exception as exc:
                    # 現況是 `except Exception: continue`——整頁無聲消失。
                    # 這裡把它留成一筆可查詢的失敗（診斷 #2）。
                    logger.warning("page %d of %s failed: %s", i, path.name, exc)
                    pages.append(
                        Page(i, float(page.width), float(page.height), (), True, str(exc)[:200])
                    )
    except Exception as exc:
        return Document(EXTRACTOR_NAME, EXTRACTION_VERSION, (), error=str(exc)[:300])

    return Document(
        EXTRACTOR_NAME,
        EXTRACTION_VERSION,
        tuple(_mark_repeated_headers_footers(pages)),
        meta={"layout": dict(layout_stats)},
    )
