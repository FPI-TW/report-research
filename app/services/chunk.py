"""文字分塊：盡量在段落邊界切，固定大小 + 重疊；markdown 表格依列切、每塊帶表頭、不套 overlap。

## 表格為什麼要特別處理（v4，docs/EXTRACTION.md §10 診斷 #6）

版面層把表格序列化成 markdown（`| a | b |`，一列一行），對 `chunk_text` 而言那是一個
「超長段落」，於是走純字元滑動視窗：一列會被從中間切開、後面的塊沒有表頭、前一塊的
尾巴（80 字元）還會黏到表頭前面（實測「…彙總| | 2020 …」）。v3 全庫 20,155 個以 `|` 開頭
的 chunk 裡 18,462 個沒有表頭分隔列——那些數字沒有欄名，檢索到了也不知道是什麼。

所以：段落若整段都是 `|` 開頭的行，就當表格——依列切、每塊重複表頭（首列＋分隔列），
單列不切；表格塊不接前一塊的尾巴、也不把自己的尾巴給下一塊（半列表格黏在散文前面
比沒有 overlap 更糟）。散文之間的 overlap 合併維持原樣（`reading/anchor.py` 的 head-drop
補償依賴它，`tests/test_chunk.py` 釘住）。判定用內容不用版面索引：入庫時切的是
`clean_extracted` 之後的正典文字，區塊索引是對原始序列化字串算的，兩者位移對不上。
"""

from __future__ import annotations

import re

CHUNK_SIZE = 600
CHUNK_OVERLAP = 80

_RE_TABLE_SEP = re.compile(r"^\|(\s*:?-{3,}:?\s*\|)+\s*$")


def _split_paragraphs(text: str) -> list[str]:
    paras = re.split(r"\n\s*\n", text)
    return [p.strip() for p in paras if p.strip()]


def _is_table_para(para: str) -> bool:
    lines = para.split("\n")
    return len(lines) >= 2 and all(ln.lstrip().startswith("|") for ln in lines)


def _chunk_table(para: str, size: int) -> list[str]:
    """markdown 表格 → 數塊，每塊＝表頭（首列＋分隔列）＋若干整列。單列不切，超過 size 也不切。"""
    lines = [ln.strip() for ln in para.split("\n") if ln.strip()]
    header = lines[:2] if len(lines) >= 2 and _RE_TABLE_SEP.match(lines[1]) else lines[:1]
    body = lines[len(header):]
    head = "\n".join(header)
    if not body:
        return [head]
    out: list[str] = []
    cur: list[str] = []
    cur_len = 0
    for row in body:
        if cur and len(head) + 1 + cur_len + len(row) > size:
            out.append("\n".join([head, *cur]))
            cur, cur_len = [], 0
        cur.append(row)
        cur_len += len(row) + 1
    out.append("\n".join([head, *cur]))
    return out


def chunk_text(
    text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP
) -> list[str]:
    """先以段落聚合到接近 size，超長段落再以滑動視窗切，並保留 overlap；表格段落依列切、帶表頭。"""
    text = text.strip()
    if not text:
        return []

    chunks: list[str] = []
    kinds: list[str] = []  # "text" | "table"，決定 overlap 要不要接
    buf = ""

    def _flush() -> None:
        nonlocal buf
        if buf:
            chunks.append(buf)
            kinds.append("text")
            buf = ""

    for para in _split_paragraphs(text):
        if _is_table_para(para):
            _flush()
            for piece in _chunk_table(para, size):
                chunks.append(piece)
                kinds.append("table")
            continue
        if len(para) > size:
            _flush()
            start = 0
            while start < len(para):
                chunks.append(para[start : start + size])
                kinds.append("text")
                start += size - overlap
            continue
        if len(buf) + len(para) + 1 <= size:
            buf = f"{buf}\n{para}" if buf else para
        else:
            _flush()
            buf = para
    _flush()

    # 套用 overlap：相鄰**散文**塊接上前一塊尾端，提升語意連續性；表格塊兩側都不接。
    if overlap > 0 and len(chunks) > 1:
        merged: list[str] = [chunks[0]]
        for i in range(1, len(chunks)):
            if kinds[i] == "text" and kinds[i - 1] == "text":
                merged.append(f"{chunks[i - 1][-overlap:]}{chunks[i]}")
            else:
                merged.append(chunks[i])
        chunks = merged

    return [c.strip() for c in chunks if c.strip()]
