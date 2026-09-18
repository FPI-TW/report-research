"""文件模型（Document → Page → Block）與**決定性**序列化。

現況所有下游都吃同一個 `str`，版面資訊在第一步就永久遺失。這個模型是
docs/EXTRACTION.md §1 說的樞紐：切塊、欄位擷取、`full_text`
各自從它取自己要的東西，而 `full_text` 只是**其中一個序列化 view**。

## 為什麼序列化必須是決定性的

`report_takeaway.text_sha256` 是對 `clean_extracted(full_text)` 的驗章。
同一份 PDF、同一 `extraction_version` 若序列化出不同字串，驗章就會變成
隨機紅燈，而摘錄批次會據此無止境地重跑。所以：

- 所有集合都是 `tuple`，不是 `list`／`set`／`dict`（後三者的順序在不同
  Python 版本或不同插入順序下不保證一致）。
- Block 的排序鍵是**全序**的（含 `page_no` 與 `order` 當最後的 tie-break），
  不依賴 `sorted()` 的穩定性去補洞。
- 浮點 bbox **不參與序列化**，只用於排序與品質指標；比較 bbox 時一律先
  量化（`_q()`），否則同一份檔在不同 pdfminer 版本下的第 15 位小數差異
  會讓頁首頁尾偵測的分群結果跳動。

## 為什麼表格寫成 markdown

`textnorm._RE_CJK_GAP` 是 `(?<=[CJK])\\s+(?=[CJK])`，**只吃空白**。表格若用
空白對齊欄位，`clean_extracted()` 會把 CJK 儲存格之間的空白全部刪掉，
「台積電 買進 1200」變成「台積電買進1200」——欄界消失，這正是
docs/EXTRACTION.md §10 診斷 #5。改用可見分隔符 `|` 就穿得過去，
而且**不必動 `clean_extracted`**（動它要重切重嵌 585,942 列 chunk）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Literal

# Block 型別。與 docs/EXTRACTION.md §3 一致。
BlockType = Literal[
    "title",
    "paragraph",
    "table",
    "figure_caption",
    "header",
    "footer",
    "footnote",
]

# 序列化時預設丟掉的型別：頁首頁尾是每頁重複的樣板文字，混進 full_text 會
# 讓檢索把「XX證券投資顧問股份有限公司」當成每篇研報都命中的詞。
DEFAULT_DROP: tuple[BlockType, ...] = ("header", "footer")

# bbox 量化位數。pdfminer 的座標是浮點運算結果，跨版本末位會漂。
_BBOX_Q = 2


def _q(v: float) -> float:
    """量化座標。分群與比較一律用這個，不要直接比原始浮點。"""
    return round(float(v), _BBOX_Q)


@dataclass(frozen=True)
class Block:
    """版面上的一個區塊。

    `text` 與 `table_cells` 是互斥的兩種內容載體：`type == "table"` 時看
    `table_cells`，其餘看 `text`。表格也會有 `text`（markdown 形式），那是
    序列化的產物、不是原始資料，僅供除錯與比對報告顯示。
    """

    type: BlockType
    page_no: int  # 1-based，與 PDF 頁碼一致
    order: int  # 同頁內的閱讀順序，由 layout.py 決定
    bbox: tuple[float, float, float, float] | None = None  # (x0, top, x1, bottom)
    text: str = ""
    table_cells: tuple[tuple[str, ...], ...] = ()
    column: int = 0  # 0-based 欄索引；單欄版面恆為 0
    # 跨欄帶索引：頁面被跨欄元素（橫幅表格、跨欄大標）切成上下幾段，同一段內才分欄。
    # 沒有它，頁尾的橫幅表格會因為「跨欄＝第 0 欄」而排到右欄整段之前（E0 實測凱基）。
    band: int = 0

    @property
    def sort_key(self) -> tuple[int, int, int, float, float, int]:
        """全序排序鍵：頁 → 跨欄帶 → 欄 → y → x → order。**最後兩項是 tie-break，不可省**
        ——沒有它們，兩個 bbox 完全相同的 Block（實務上出現在重疊的浮水印與正文）順序
        會依賴 `sorted()` 的穩定性，而那取決於它們進 list 的順序。"""
        x0 = _q(self.bbox[0]) if self.bbox else 0.0
        top = _q(self.bbox[1]) if self.bbox else 0.0
        return (self.page_no, self.band, self.column, top, x0, self.order)


@dataclass(frozen=True)
class Page:
    """一頁。`failed=True` 代表這一頁抽取拋了例外。

    **這一頁仍然會留在 `Document.pages` 裡**，只是 `blocks` 為空。現況
    `app/services/extract.py` 的 `except Exception: continue` 是直接讓整頁
    消失，於是「抽到 3 頁」與「抽到 30 頁」在下游長得一模一樣
    （docs/EXTRACTION.md §10 診斷 #2）。保留空頁是為了讓
    `pages_failed` 算得出來。
    """

    page_no: int
    width: float
    height: float
    blocks: tuple[Block, ...] = ()
    failed: bool = False
    error: str | None = None


@dataclass(frozen=True)
class Document:
    """一份文件的完整抽取結果。"""

    extractor: str
    extraction_version: str
    pages: tuple[Page, ...] = ()
    error: str | None = None  # 整份檔開不起來時填這裡，pages 為空
    meta: dict = field(default_factory=dict, compare=False)  # 診斷用，不進序列化

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def pages_failed(self) -> tuple[int, ...]:
        return tuple(p.page_no for p in self.pages if p.failed)

    def blocks(self, drop: Iterable[BlockType] = ()) -> tuple[Block, ...]:
        """全文件的 Block，已依全序排序。"""
        dropped = set(drop)
        out = [b for p in self.pages for b in p.blocks if b.type not in dropped]
        out.sort(key=lambda b: b.sort_key)
        return tuple(out)


# ── 表格 → markdown ─────────────────────────────────────────────────────────

# 儲存格內的 `|` 必須跳脫，否則欄數會錯亂；換行折成空白，否則一列表格會被
# `chunk_text` 的段落切分當成多段。
def _cell(v: object) -> str:
    s = "" if v is None else str(v)
    return s.replace("|", "\\|").replace("\r", " ").replace("\n", " ").strip()


def table_to_markdown(rows: tuple[tuple[str, ...], ...]) -> str:
    """表格列 → markdown 表格字串。

    第一列一律當表頭並補分隔列——不去猜「這張表有沒有表頭」，因為猜錯的
    兩種結果（少一列資料 vs 多一條分隔線）不對稱：前者是資料遺失。
    欄數以最寬的一列為準，短列補空白，否則 markdown 表格會渲染錯位。
    """
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    if width == 0:
        return ""
    norm = [tuple(_cell(c) for c in r) + ("",) * (width - len(r)) for r in rows]
    lines = ["| " + " | ".join(norm[0]) + " |", "| " + " | ".join(["---"] * width) + " |"]
    lines.extend("| " + " | ".join(r) + " |" for r in norm[1:])
    return "\n".join(lines)


def block_text(b: Block) -> str:
    """單一 Block 的序列化文字。"""
    if b.type == "table":
        return table_to_markdown(b.table_cells)
    return b.text


# ── 文件 → full_text ────────────────────────────────────────────────────────


def serialize(doc: Document, drop: Iterable[BlockType] = DEFAULT_DROP) -> str:
    """Document → `full_text` 候選字串。**同一份 PDF 兩次執行必須逐字元相同。**

    區塊之間以空行分隔，因為 `chunk_text` 依 `\\n\\s*\\n` 切段
    （`app/services/chunk.py::_split_paragraphs`）——用單一換行會讓整頁併成
    一個超長段落，然後被滑動視窗從中間切開。

    **不做任何清理**：這裡吐的是「未清理的序列化文字」，維持既有契約
    （錨點基準字串／餵 LLM 的 excerpt／API 回傳文字三者同源，清理由
    `textnorm.clean_extracted` 在入庫時統一做）。
    """
    return serialize_with_index(doc, drop)[0]


@dataclass(frozen=True)
class BlockSpan:
    """序列化文字裡一個 Block 的落點。E1c 的 per-hash 快取存的就是這個（不存 bbox）：
    下游只需要「哪一段是表格／標題」，座標要用時重抽比維護一份會漂的大快取便宜。"""

    type: BlockType
    page_no: int
    start: int  # 在序列化文字裡的 [start, end) 字元區間
    end: int


def serialize_with_index(
    doc: Document, drop: Iterable[BlockType] = DEFAULT_DROP
) -> tuple[str, tuple[BlockSpan, ...]]:
    """`serialize` 的帶索引版：回傳 (文字, 每個 Block 的字元區間)。兩者必須同源——
    索引是對**這個**字串算的，任何在外面對字串做的清理都會讓區間失效。"""
    parts: list[str] = []
    spans: list[BlockSpan] = []
    pos = 0
    for b in doc.blocks(drop=drop):
        t = block_text(b)
        if not t.strip():
            continue
        if parts:
            pos += 2  # "\n\n"
        spans.append(BlockSpan(b.type, b.page_no, pos, pos + len(t)))
        parts.append(t)
        pos += len(t)
    return "\n\n".join(parts), tuple(spans)
