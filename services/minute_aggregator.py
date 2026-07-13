"""全市场分钟线合成器。

设计约束：
    - 热路径（``on_tick``）中禁止使用 SQLite / 磁盘 I/O，全部走内存预分配数组。
    - 当前分钟 OHLCV 使用 numpy 数组按标的索引存储，禁止为每个标的建立 Python dict。
    - 已闭合分钟线按标的分桶保存为 ``deque(maxlen=240)``，保留当日约 240 根 1 分钟线。
    - ``Tick`` 使用轻量 NamedTuple，避免 Python 对象开销。
"""

from collections import defaultdict, deque
from typing import Dict, List, NamedTuple, Set

import numpy as np
import pandas as pd


class Tick(NamedTuple):
    """轻量 Tick 数据结构。

    Attributes:
        symbol: 标的代码
        time: 成交时间，格式 HHMMSS；分钟部分通过 ``time // 100`` 取 HHMM
        price: 最新价
        volume: 本次 Tick 成交量（增量）
    """

    symbol: str
    time: int
    price: float
    volume: int


class Min1Aggregator:
    """全市场分钟线合成器（无锁、预分配、批量处理）。

    设计要点：
        - OHLCV 使用预分配 numpy 数组，避免逐标的 Python dict 开销
        - 已闭合分钟线按标的分桶存入 ``deque(maxlen=240)``，保留一个交易日
        - 非监控标的（不在 ``sym2idx``）直接丢弃
        - ``on_tick`` 假设单线程热路径调用，如需并发由调用方加锁
        - 支持冷热分级配置 ``tier_config``，用于区分实时 / 批量 / 惰性合成标的
    """

    def __init__(self, symbols: List[str], tier_config: dict = None):
        self.symbols = list(symbols)
        self.n = len(self.symbols)
        self.sym2idx = {s: i for i, s in enumerate(self.symbols)}

        # 预分配 numpy 数组（避免 Python 对象开销）
        self.cur_min = np.zeros(self.n, dtype=np.int32)      # 当前分钟 HHMM
        self.open = np.zeros(self.n, dtype=np.float32)
        self.high = np.zeros(self.n, dtype=np.float32)
        self.low = np.zeros(self.n, dtype=np.float32)
        self.close = np.zeros(self.n, dtype=np.float32)
        self.vol = np.zeros(self.n, dtype=np.int32)

        # 已闭合分钟线：按标的分桶，避免全局锁
        self.closed_bars = defaultdict(lambda: deque(maxlen=240))  # 保留当日已闭合

        # 冷热分级配置
        self._tier_config = tier_config or {}

    def on_tick(self, symbol: str, tick: Tick):
        """单 Tick 处理（热路径）。"""
        idx = self.sym2idx.get(symbol)
        if idx is None:
            return  # 非监控标的，直接丢弃

        min_id = tick.time // 100  # HHMM

        if min_id != self.cur_min[idx]:
            self._close_bar(symbol, idx)
            self.cur_min[idx] = min_id
            self.open[idx] = tick.price
            self.high[idx] = tick.price
            self.low[idx] = tick.price
            self.vol[idx] = 0

        # 更新当前分钟（分支预测友好）
        if tick.price > self.high[idx]:
            self.high[idx] = tick.price
        elif tick.price < self.low[idx]:
            self.low[idx] = tick.price
        self.close[idx] = tick.price
        self.vol[idx] += tick.volume

    def on_tick_batch(self, ticks: List[Tick]):
        """批量处理（Python GIL 优化）。"""
        for tick in ticks:
            self.on_tick(tick.symbol, tick)

    def _close_bar(self, symbol: str, idx: int):
        """闭合上一分钟，归档到内存队列。"""
        if self.cur_min[idx] == 0:
            return  # 尚未开始任何分钟，跳过
        self.closed_bars[symbol].append({
            'time': int(self.cur_min[idx]),
            'open': float(self.open[idx]),
            'high': float(self.high[idx]),
            'low': float(self.low[idx]),
            'close': float(self.close[idx]),
            'volume': int(self.vol[idx]),
        })

    def get_today_series(self, symbol: str) -> pd.DataFrame:
        """获取某标的今日已闭合分钟线 + 当前未闭合分钟。

        Returns:
            DataFrame，列: ``time, open, high, low, close, volume, confirmed``。
            已闭合 bar 不含 ``confirmed`` 字段（或视为 True），当前未闭合 bar ``confirmed=False``。
        """
        cols = ['time', 'open', 'high', 'low', 'close', 'volume', 'confirmed']
        # 已闭合 bar 统一标记 confirmed=True；复制避免修改原始归档数据
        rows = [{**row, 'confirmed': True} for row in self.closed_bars.get(symbol, ())]

        idx = self.sym2idx.get(symbol)
        if idx is not None and self.cur_min[idx] > 0:
            rows.append({
                'time': int(self.cur_min[idx]),
                'open': float(self.open[idx]),
                'high': float(self.high[idx]),
                'low': float(self.low[idx]),
                'close': float(self.close[idx]),
                'volume': int(self.vol[idx]),
                'confirmed': False,
            })

        if not rows:
            return pd.DataFrame(columns=cols)
        return pd.DataFrame(rows, columns=cols)

    def tier_symbols(self) -> Dict[str, List[str]]:
        """返回冷热分级标的映射。

        支持 ``tier_config`` 中的值为：
            - ``'all'``：表示全部未分配标的
            - ``list`` / ``set`` / ``tuple``：显式指定的标的列表
            - 其他值：空列表

        分级顺序为 ``tier1_realtime`` → ``tier2_batch`` → ``tier3_lazy``，
        已分配的标的不参与后续分级。未提供 ``tier_config`` 时，全部标的归入 ``tier3_lazy``。

        Returns:
            Dict[str, List[str]]: {tier_name: [symbols]}
        """
        result: Dict[str, List[str]] = {}
        assigned: Set[str] = set()
        order = ('tier1_realtime', 'tier2_batch', 'tier3_lazy')

        for tier_name in order:
            value = self._tier_config.get(tier_name)
            if value == 'all':
                syms = [s for s in self.symbols if s not in assigned]
            elif isinstance(value, (list, tuple, set, frozenset)):
                syms = [s for s in value if s in self.sym2idx and s not in assigned]
            else:
                syms = []
            result[tier_name] = syms
            assigned.update(syms)

        if not self._tier_config:
            result['tier3_lazy'] = list(self.symbols)

        return result
