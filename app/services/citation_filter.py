"""問答來源編號的決定性輸出檢查。

研報片段可能誘導模型印出不存在的 [n]。只允許本輪已提供的來源編號；
無效編號以明確標記取代，避免把沒有來源的論點呈現成有效引用。
"""

from __future__ import annotations

import re
from collections.abc import Iterable

_CITATION = re.compile(r"\[\d+\]")
INVALID_CITATION = "（無效引用）"


def filter_unknown_citations(text: str, valid_numbers: Iterable[int]) -> str:
    """只保留本輪來源清單中的數字引用。"""
    allowed = {f"[{number}]" for number in valid_numbers}
    return _CITATION.sub(lambda match: match.group() if match.group() in allowed else INVALID_CITATION, text)


def count_unknown_citations(text: str, valid_numbers: Iterable[int]) -> int:
    """回模型原文中不在本輪來源清單的數字引用數，供非敏感遙測。"""
    allowed = {f"[{number}]" for number in valid_numbers}
    return sum(match.group() not in allowed for match in _CITATION.finditer(text))


class CitationStreamFilter:
    """保留跨 chunk 的 `[數字]` 尾段，避免無效引用短暫出現在串流畫面。"""

    def __init__(self, valid_numbers: Iterable[int]) -> None:
        self._allowed = tuple(valid_numbers)
        self._pending = ""

    def feed(self, chunk: str) -> str:
        data = self._pending + chunk
        self._pending = ""
        opening = data.rfind("[")
        if opening >= 0 and re.fullmatch(r"\[\d*", data[opening:]):
            self._pending = data[opening:]
            data = data[:opening]
        return filter_unknown_citations(data, self._allowed)

    def flush(self) -> str:
        tail = self._pending
        self._pending = ""
        return tail
