"""有上限的單行程 TTL 快取（依 key 分格）。

`web/routers/monitor.py` 的 `_cached()` 是「一個模組級 dict 存一份值」，適合沒有參數的
快照；這一支給**回應會隨查詢參數變**的讀取端點用：每組參數一格，格數有上限。

兩個刻意的簡化，理由與 `monitor._cached` 相同：

- **不加鎖、不做 single-flight**：兩個請求同時撲空時最壞是各算一次。這裡快取的是純讀取
  的聚合查詢，重算只是多花一次查詢時間，不會有副作用。
- **per-process**：lifespan 已經擋多 worker（`web/concurrency.py`），沒有跨行程一致性問題。

滿了淘汰最舊寫入的那一格（dict 保留插入順序）。不做 LRU：TTL 只有幾十秒，
「最近用過」與「最近寫入」幾乎是同一件事，多維護一份存取順序不划算。
"""

from __future__ import annotations

import time
from collections.abc import Hashable
from typing import Any

_MISS = object()
_ALL: list["TTLCache"] = []


class TTLCache:
    def __init__(self, *, ttl: float, max_entries: int, name: str):
        self.ttl = float(ttl)
        self.max_entries = int(max_entries)
        self.name = name
        self._store: dict[Hashable, tuple[float, Any]] = {}
        _ALL.append(self)

    @property
    def enabled(self) -> bool:
        return self.ttl > 0 and self.max_entries > 0

    def get(self, key: Hashable, default: Any = None) -> Any:
        if not self.enabled:
            return default
        hit = self._store.get(key, _MISS)
        if hit is _MISS:
            return default
        expires_at, value = hit
        if time.monotonic() >= expires_at:
            self._store.pop(key, None)
            return default
        return value

    def put(self, key: Hashable, value: Any) -> None:
        if not self.enabled:
            return
        self._store.pop(key, None)  # 重寫同一格時挪到最新，免得剛更新就被當最舊淘汰
        while len(self._store) >= self.max_entries:
            self._store.pop(next(iter(self._store)))
        self._store[key] = (time.monotonic() + self.ttl, value)

    def clear(self) -> None:
        self._store.clear()

    def __len__(self) -> int:
        return len(self._store)


def reset_all() -> None:
    """清空所有已建立的快取。

    **測試用**，由 `tests/conftest.py` 的 autouse fixture 每題前後呼叫——模組級快取會跨測試
    存活，A 測試的假回應會原封不動交給用同一組參數打同一支端點的 B 測試。
    """
    for cache in _ALL:
        cache.clear()
