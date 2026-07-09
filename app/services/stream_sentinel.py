"""串流中偵測 EXT_SENTINEL 的增量狀態機。

自 answer.py 的逐 chunk buffering 抽出：保留最後 len(sentinel) 個字元以偵測
跨 chunk 邊界的 sentinel；命中後停止輸出後續（外部來源區塊改由 raw 全文的
split_external_sources 處理）。與 split_external_sources 語意一致、並存。
"""


class SentinelStreamParser:
    def __init__(self, sentinel: str = "[EXT_SOURCES]"):
        self._sentinel = sentinel
        self._hold = len(sentinel)
        self._buf = ""
        self._found = False

    @property
    def sentinel_found(self) -> bool:
        return self._found

    def feed(self, chunk: str) -> str:
        """吃一段 chunk，回可安全輸出的片段（保留可能跨界的尾段）。"""
        if self._found:
            return ""
        self._buf += chunk
        idx = self._buf.find(self._sentinel)
        if idx != -1:
            out = self._buf[:idx]
            self._found = True
            self._buf = ""
            return out
        if len(self._buf) > self._hold:
            out = self._buf[: -self._hold]
            self._buf = self._buf[-self._hold :]
            return out
        return ""

    def flush(self) -> str:
        """串流結束：無 sentinel 時吐出殘餘尾段。"""
        if self._found:
            return ""
        out = self._buf
        self._buf = ""
        return out
