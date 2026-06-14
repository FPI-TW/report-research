"""文字分塊：盡量在段落邊界切，固定大小 + 重疊。"""

from __future__ import annotations

import re

CHUNK_SIZE = 600
CHUNK_OVERLAP = 80


def _split_paragraphs(text: str) -> list[str]:
    paras = re.split(r"\n\s*\n", text)
    return [p.strip() for p in paras if p.strip()]


def chunk_text(
    text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP
) -> list[str]:
    """先以段落聚合到接近 size，超長段落再以滑動視窗切，並保留 overlap。"""
    text = text.strip()
    if not text:
        return []

    chunks: list[str] = []
    buf = ""
    for para in _split_paragraphs(text):
        if len(para) > size:
            if buf:
                chunks.append(buf)
                buf = ""
            start = 0
            while start < len(para):
                chunks.append(para[start : start + size])
                start += size - overlap
            continue
        if len(buf) + len(para) + 1 <= size:
            buf = f"{buf}\n{para}" if buf else para
        else:
            if buf:
                chunks.append(buf)
            buf = para
    if buf:
        chunks.append(buf)

    # 套用 overlap：相鄰塊接上前一塊尾端，提升語意連續性
    if overlap > 0 and len(chunks) > 1:
        merged: list[str] = [chunks[0]]
        for prev, cur in zip(chunks, chunks[1:]):
            tail = prev[-overlap:]
            merged.append(f"{tail}{cur}")
        chunks = merged

    return [c.strip() for c in chunks if c.strip()]
