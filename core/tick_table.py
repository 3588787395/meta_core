"""TickTable —— 最新 tick 数据水位线表（共享值对象）。

从 ``core/runtime_mode_module.py`` 抽离，供 ``runtime_mode_module`` 与
``tick_bar_module`` 共同导入，消除业务模块间的跨引用。
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Dict


class TickTable:
    """最新 tick 数据水位线表。"""

    def __init__(self) -> None:
        self.data: Dict[str, Dict] = {}
        self.ts: float = 0.0
        self.hash: int = 0

    def _compute_hash(self, data: Dict[str, Dict]) -> int:
        """对 tick 数据计算 sha256 指纹（sort_keys 序列化保证内容确定性）。"""
        payload = json.dumps(data, sort_keys=True)
        return int.from_bytes(hashlib.sha256(payload.encode("utf-8")).digest(), "big")

    def update(self, tick_data: Dict[str, Dict]) -> bool:
        """用新 tick 数据刷新水位线。"""
        new_hash = self._compute_hash(tick_data)
        if new_hash == self.hash:
            return False
        self.data = dict(tick_data)
        now = time.time()
        if now <= self.ts:
            now = self.ts + 1e-6
        self.ts = now
        self.hash = new_hash
        return True

    def get(self, code: str) -> Dict:
        """返回指定 code 的 bar dict，未命中返回空 dict。"""
        return self.data.get(code, {})

    def snapshot(self) -> Dict[str, Dict]:
        """返回全部 tick 数据的浅拷贝。"""
        return dict(self.data)
