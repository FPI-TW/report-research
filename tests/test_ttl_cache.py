"""web/ttl_cache.py：有上限的 TTL 快取。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from web import ttl_cache  # noqa: E402
from web.ttl_cache import TTLCache  # noqa: E402


class TTLCacheTests(unittest.TestCase):
    def test_hit_until_expiry_then_miss(self):
        cache = TTLCache(ttl=10, max_entries=4, name="t")
        with patch("web.ttl_cache.time.monotonic", return_value=100.0):
            cache.put("k", "v")
        with patch("web.ttl_cache.time.monotonic", return_value=109.9):
            self.assertEqual(cache.get("k"), "v")
        with patch("web.ttl_cache.time.monotonic", return_value=110.0):
            self.assertIsNone(cache.get("k"))
        self.assertEqual(len(cache), 0)  # 過期的那一格順手移掉，不佔上限

    def test_oldest_entry_is_evicted_at_capacity(self):
        cache = TTLCache(ttl=60, max_entries=2, name="t")
        cache.put("a", 1)
        cache.put("b", 2)
        cache.put("c", 3)
        self.assertEqual((cache.get("a"), cache.get("b"), cache.get("c")), (None, 2, 3))
        self.assertEqual(len(cache), 2)

    def test_rewriting_a_key_refreshes_its_age(self):
        cache = TTLCache(ttl=60, max_entries=2, name="t")
        cache.put("a", 1)
        cache.put("b", 2)
        cache.put("a", 10)  # a 變成最新，下一個被淘汰的應該是 b
        cache.put("c", 3)
        self.assertEqual((cache.get("a"), cache.get("b"), cache.get("c")), (10, None, 3))

    def test_falsy_values_are_cacheable(self):
        cache = TTLCache(ttl=60, max_entries=2, name="t")
        cache.put("empty", [])
        self.assertEqual(cache.get("empty", "MISS"), [])
        self.assertEqual(cache.get("absent", "MISS"), "MISS")

    def test_zero_ttl_means_disabled(self):
        cache = TTLCache(ttl=0, max_entries=8, name="t")
        cache.put("k", "v")
        self.assertIsNone(cache.get("k"))
        self.assertFalse(cache.enabled)

    def test_reset_all_clears_every_instance(self):
        a = TTLCache(ttl=60, max_entries=2, name="a")
        b = TTLCache(ttl=60, max_entries=2, name="b")
        a.put(1, 1)
        b.put(2, 2)
        ttl_cache.reset_all()
        self.assertEqual((len(a), len(b)), (0, 0))


if __name__ == "__main__":
    unittest.main()
