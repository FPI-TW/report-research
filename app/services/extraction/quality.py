"""抽取品質指標。docs/EXTRACTION.md §5 的**唯一計算來源**。

兩份實作必然漂移，所以 `scripts/profile_corpus.py`、`scripts/compare_extractors.py`
以及未來 `E1` 的寫入端全部從這裡取值，不要各自再算一次。

**全部純程式，零 LLM。** 這是刻意的：每檔一次 LLM 呼叫的成本與延遲都不成
比例，而且評分本身會變得不可重現——不可重現的品質分數沒辦法拿來做
「這一版比上一版好」的判斷，而那正是這些指標存在的唯一理由。

## `layout_coverage` 的口徑與它的已知混淆

它問的是：**頁面上有墨的地方，有多少被抽出的文字 bbox 蓋到？** 沒被蓋到
的區域就是「抽漏的整塊」——這是偵測漏抽最靈敏的單一指標，因為字元數
之類的量測對「少抽了一整欄」是不敏感的（少 40% 的字看起來只是「這頁字
比較少」）。

已知混淆有三個，讀這個數字時要一起讀：

1. **圖表與圖片有墨但本來就沒有文字**，會壓低分數。研報的圖多，所以絕對
   值偏低是常態；**有意義的是同一份檔在不同抽取器之間的差值**。
2. 掃描檔整頁都是墨、零文字 bbox，分數趨近 0——那是對的訊號。
3. 需要 render 每一頁（pypdfium2），成本比其他指標高一個量級，所以預設
   關閉，只在並排比對時開。

`pypdf` 沒有 bbox，所以這個指標**對現況抽取器算不出來**，§7 基準線那一欄
只能從 `E1` 起算。
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass
from typing import Any

from app.services.extraction.model import Document, block_text

logger = logging.getLogger(__name__)

# 私用區與替換字元。CID 缺 ToUnicode map 時 pdfminer／pypdf 會吐這些。
# **不把「非常用 CJK」算進去**：那條在 v1 的定義裡，但實測會把正常的罕用字
# （券商名、人名）算成亂碼，而真正的亂碼幾乎都落在私用區。
_GARBLED = re.compile(r"[-�]")

# 每頁字元數的健康下界。低於此值多半是掃描檔或抽取失敗。
# 與 `extract.MIN_TEXT_CHARS`（整份 100 字）不同：那是「整份是不是掃描檔」，
# 這是「平均每頁抽到多少」，後者對「30 頁只抽到 3 頁」才敏感。
_CHARS_PER_PAGE_FLOOR = 200.0

# quality_score 的權重。**可調，但一調就要 bump `EXTRACTION_VERSION`**，
# 否則 DB 裡會出現同一版本號下兩種尺度的分數。
_W_GARBLED = 0.35
_W_DENSITY = 0.25
_W_FAILED = 0.25
_W_COVERAGE = 0.15

# render 成本控制：把每頁縮到約這麼寬的像素再比對墨跡。
# 80px 對 A4 約 7pt/px——比一行字高（約 10pt）粗，所以它量的是「整塊」不是
# 「單字」，正好是這個指標要的粒度。
_INK_GRID_W = 80
# 灰階閾值。降取樣會把細字平均成淺灰，所以門檻取得寬。
_INK_THRESHOLD = 245


@dataclass(frozen=True)
class PageQuality:
    page_no: int
    chars: int
    garbled: int
    failed: bool
    columns: int
    ink_cells: int = 0
    ink_cells_covered: int = 0


@dataclass(frozen=True)
class Quality:
    """一份文件的品質指標。欄位名對齊 `research_report.quality_flags` 的鍵。"""

    page_count: int
    char_count: int
    chars_per_page: float
    garbled_ratio: float
    pages_failed: tuple[int, ...]
    pages_failed_ratio: float
    max_columns: int
    multi_column_pages: int
    layout_coverage: float | None  # None ＝ 沒開 render
    quality_score: float
    # ── v4 起，來自 layout.py 的統計（Document.meta["layout"]），不進分數、供稽核 ──
    words_merged_ratio: float = 0.0  # 被 _merge_touching_words 併掉的詞 ÷ 原始詞數
    chart_words_dropped: int = 0  # 圖區內丟掉的刻度／圖例詞
    tables_retried: int = 0  # 框線表體檢可疑、以詞重建成功的張數
    tables_rejected: int = 0  # 體檢可疑且重建失敗、退回文字流的張數

    def as_flags(self) -> dict[str, Any]:
        d = asdict(self)
        d["pages_failed"] = list(self.pages_failed)
        return d


def garbled_ratio(text: str) -> float:
    if not text:
        return 0.0
    return len(_GARBLED.findall(text)) / len(text)


def _ink_stats(
    pdfium_page: Any,
    blocks_bbox: list[tuple[float, float, float, float]],
    width: float,
    height: float,
) -> tuple[int, int]:
    """回 (有墨的格數, 其中被文字 bbox 蓋到的格數)。

    `pdfium_page` 必須是 **pypdfium2** 的 `PdfPage`——render 是它的能力，
    pdfplumber 的 Page 沒有 `.render()`。這兩個型別長得很像而且都叫 page，
    傳錯只會拋 AttributeError 然後被上層吞掉、`layout_coverage` 悄悄變成
    None（開發時實際踩過一次）。
    """
    if width <= 0 or height <= 0:
        return (0, 0)
    scale = _INK_GRID_W / width
    img = pdfium_page.render(scale=scale).to_pil().convert("L")
    gw, gh = img.size
    if gw == 0 or gh == 0:
        return (0, 0)
    px = img.load()
    if px is None:
        return (0, 0)

    # 文字 bbox → 格座標的覆蓋遮罩
    covered = [[False] * gw for _ in range(gh)]
    for x0, top, x1, bottom in blocks_bbox:
        a = max(0, int(x0 * scale))
        b = min(gw - 1, int(x1 * scale))
        c = max(0, int(top * scale))
        d = min(gh - 1, int(bottom * scale))
        for y in range(c, d + 1):
            row = covered[y]
            for x in range(a, b + 1):
                row[x] = True

    ink = 0
    hit = 0
    for y in range(gh):
        row = covered[y]
        for x in range(gw):
            if px[x, y] < _INK_THRESHOLD:
                ink += 1
                if row[x]:
                    hit += 1
    return (ink, hit)


def measure(doc: Document, renderer: Any | None = None) -> Quality:
    """計算一份文件的品質指標。

    `renderer` 傳入已開啟的 **pypdfium2 `PdfDocument`** 才會算
    `layout_coverage`；不傳就跳過（省掉每頁一次 render）。**不在這裡自己
    開檔**——呼叫端多半已經開著，重開一次是純浪費。
    """
    text = "\n\n".join(block_text(b) for b in doc.blocks())
    char_count = len(text)
    n = doc.page_count

    per_page: list[PageQuality] = []
    ink_total = 0
    ink_hit = 0
    for i, p in enumerate(doc.pages):
        ptext = "\n".join(block_text(b) for b in p.blocks)
        cols = (max((b.column for b in p.blocks), default=0)) + 1
        ic = ih = 0
        if renderer is not None and not p.failed:
            try:
                boxes = [b.bbox for b in p.blocks if b.bbox]
                ic, ih = _ink_stats(renderer[i], boxes, p.width, p.height)
            except Exception as exc:
                # **不可以靜默吞**：吞掉的結果是 layout_coverage 悄悄變 None，
                # 而 None 看起來只是「沒開 render」，與「開了但壞了」無法區分。
                logger.warning("ink stats failed on page %d: %s", p.page_no, exc)
        ink_total += ic
        ink_hit += ih
        per_page.append(
            PageQuality(p.page_no, len(ptext), len(_GARBLED.findall(ptext)), p.failed, cols, ic, ih)
        )

    failed = doc.pages_failed
    failed_ratio = len(failed) / n if n else 1.0
    chars_pp = char_count / n if n else 0.0
    g = garbled_ratio(text)
    coverage = (ink_hit / ink_total) if (renderer is not None and ink_total) else None

    density = min(1.0, chars_pp / _CHARS_PER_PAGE_FLOOR)
    # 沒量到 coverage 就把它的權重拿掉重新正規化，而不是白送滿分：v3 生產路徑從未傳
    # renderer，0.15 的權重每篇都送，9,263 篇全部 ≥ 0.9、5,804 篇恰好 1.0，分數毫無鑑別力。
    terms = [
        (_W_GARBLED, 1.0 - min(1.0, g * 5)),  # 亂碼率 20% 就扣滿
        (_W_DENSITY, density),
        (_W_FAILED, 1.0 - failed_ratio),
    ]
    if coverage is not None:
        terms.append((_W_COVERAGE, coverage))
    score = sum(w * v for w, v in terms) / sum(w for w, _ in terms)

    layout = doc.meta.get("layout") if isinstance(doc.meta, dict) else None
    layout = layout if isinstance(layout, dict) else {}
    words_raw = int(layout.get("words_raw") or 0)

    return Quality(
        page_count=n,
        char_count=char_count,
        chars_per_page=round(chars_pp, 1),
        garbled_ratio=round(g, 6),
        pages_failed=failed,
        pages_failed_ratio=round(failed_ratio, 4),
        max_columns=max((q.columns for q in per_page), default=0),
        multi_column_pages=sum(1 for q in per_page if q.columns > 1),
        layout_coverage=round(coverage, 4) if coverage is not None else None,
        quality_score=round(max(0.0, min(1.0, score)), 4),
        words_merged_ratio=round(int(layout.get("words_merged") or 0) / words_raw, 4) if words_raw else 0.0,
        chart_words_dropped=int(layout.get("chart_words_dropped") or 0),
        tables_retried=int(layout.get("tables_retried") or 0),
        tables_rejected=int(layout.get("tables_rejected") or 0),
    )
