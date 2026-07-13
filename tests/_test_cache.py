"""测试专用 LRU + TTL 缓存。

I98：从 engine.py 迁出 LRUCache 类——生产代码零实例化（I29 删除 _data_cache 后），
仅 test_filter.py 和 simtests/test_10_data_integrity.py 直接导入使用。
"""
from __future__ import annotations

from collections import OrderedDict
import time


class LRUCache(dict):
    """OrderedDict-based LRU + TTL 缓存。
    继承 dict 以兼容 isinstance(x, dict) 检查和 dict(x)/.keys() 等用法。"""
    def __init__(self, max_entries=10000, default_ttl=5.0, ttl_map=None):
        super().__init__(); self._store = OrderedDict(); self._max = max_entries; self._default_ttl = default_ttl; self._ttl_map = ttl_map or {}
    def get(self, key, default=None):
        if key not in self._store: return default
        entry = self._store[key]
        if time.time() - entry["ts"] > entry["ttl"]:
            del self._store[key]; return default
        self._store.move_to_end(key); return entry["data"]
    def set(self, key, data, ttl=None):
        if key in self._store: del self._store[key]
        if len(self._store) >= self._max: self._store.popitem(last=False)
        if ttl is None:
            for prefix, t in self._ttl_map.items():
                if key.startswith(prefix): ttl = t; break
        self._store[key] = {"data": data, "ts": time.time(), "ttl": ttl or self._default_ttl}
    def clear(self): self._store.clear(); super().clear()
    def __len__(self): return len(self._store)
    def __iter__(self): return iter(self._store)
    def __contains__(self, key): return key in self._store
    def __getitem__(self, key):
        if key not in self._store: raise KeyError(key)
        return self._store[key]["data"]
    def __setitem__(self, key, value): self.set(key, value)
    def keys(self): return self._store.keys()
    def values(self): return (entry["data"] for entry in self._store.values())
    def items(self): return ((k, entry["data"]) for k, entry in self._store.items())
