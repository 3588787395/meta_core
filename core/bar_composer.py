"""K 线组合层：订阅 ``DataChanged(tick)`` 并独立维护多周期 ``bars``。

按 ``execute-architecture-migration`` 规格 Task 8.2 实现。
``BarComposer`` 是唯一写入 ``PoolState.bars`` 的组件，职责：
  - 订阅 ``EventBus`` 的 ``DataChanged`` 事件
  - 根据 ``latest_tick`` 合成/更新多周期 K 线
  - 仅当某周期 bar 推进时发布 ``DataChanged(bar, period)``
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .event_bus import EVENT_DATA_CHANGED, DataChanged, is_event_bus
from .time_util import time_at

DEFAULT_PERIODS = ["1m", "5m", "15m", "30m", "60m", "1d"]


def _bar_bucket_ts(ts: float, period: str) -> int:
    """根据时间戳计算该周期 bar 的桶起始时间戳（秒）。"""
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    # I46：midnight 原 1d 分支与通用分支各算一次（数据流冗余），上提为公共前缀。
    midnight = int(datetime(dt.year, dt.month, dt.day, tzinfo=timezone.utc).timestamp())
    if period == "1d":
        return midnight
    minutes = int(period[:-1]) if period[:-1].isdigit() else 1
    minutes_since_midnight = int((ts - midnight) // 60)
    bucket = (minutes_since_midnight // minutes) * minutes
    return midnight + bucket * 60


def _hash_bar(bar: Dict[str, Any]) -> str:
    try:
        payload = json.dumps(bar, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        payload = str(sorted(bar.items()))
    return hashlib.md5(payload.encode("utf-8")).hexdigest()


def _new_bar_from_tick(tick: Dict[str, Any], bucket_ts: int) -> Dict[str, Any]:
    close = float(tick.get("close", 0.0) or 0.0)
    open_p = float(tick.get("open", close) or close)
    high = float(tick.get("high", open_p) or open_p)
    low = float(tick.get("low", open_p) or open_p)
    return {
        "code": tick.get("code", ""),
        "open": open_p,
        "high": high,
        "low": low,
        "close": close,
        "volume": int(tick.get("volume", 0) or 0),
        "amount": float(tick.get("amount", 0.0) or 0.0),
        "bucket_ts": bucket_ts,
        "_hash": "",
    }


def _merge_tick(bar: Dict[str, Any], tick: Dict[str, Any]) -> Dict[str, Any]:
    new_bar = dict(bar)
    close = float(tick.get("close", 0.0) or 0.0)
    tick_high = float(tick.get("high", close) or close)
    tick_low = float(tick.get("low", close) or close)
    new_bar["close"] = close
    new_bar["high"] = max(new_bar.get("high", tick_high), tick_high)
    low = new_bar.get("low", tick_low)
    if low == 0.0:
        low = tick_low
    new_bar["low"] = min(low, tick_low)
    new_bar["volume"] = int(new_bar.get("volume", 0)) + int(tick.get("volume", 0) or 0)
    new_bar["amount"] = float(new_bar.get("amount", 0.0)) + float(tick.get("amount", 0.0) or 0.0)
    return new_bar


def _hash_period_bars(period_bars: Dict[str, Any]) -> str:
    parts: List[str] = []
    for code in sorted(period_bars.keys()):
        bar = period_bars[code]
        if isinstance(bar, dict):
            parts.append(f"{code}:{bar.get('_hash', '')}")
    payload = "\x00".join(parts)
    return hashlib.md5(payload.encode("utf-8")).hexdigest()


_BARS_HISTORY_MAXLEN = 300


def _append_closed_bar(state: Any, period: str, code: str, bar: Dict[str, Any]) -> None:
    """将闭合 bar 追加到 ``state.bars_history[period][code]``，限制 maxlen。"""
    hist = state.bars_history.setdefault(period, {}).setdefault(code, [])
    clean = {k: v for k, v in bar.items() if k != "_hash"}
    hist.append(clean)
    if len(hist) > _BARS_HISTORY_MAXLEN:
        del hist[0]


def _publish_bar_changed(composer: "BarComposer", period: str, codes: List[str]) -> None:
    if not is_event_bus(composer.bus):
        return
    ts = time_at(state=composer.state)
    period_hash = composer._bar_hashes.get(period, "")
    for code in codes:
        bar = composer.get_bar(period, code)
        event = DataChanged(
            ts=ts,
            bar_hash=period_hash,
            codes=[code],
            source="bar",
            period=period,
            data=bar,
        )
        # I22：删除 try/except + logger.debug——EventBus.publish 内部已隔离订阅者异常。
        composer.bus.publish(event)


class BarComposer:
    """多周期 K 线组合器。

    属性（≤ 5）:
      - state: PoolState 运行时表真相源
      - bus: EventBus（可选）
      - periods: 维护的周期列表
      - _bar_hashes: 各周期 bars 内容摘要缓存
      - _enabled: 是否已订阅事件

    方法（≤ 6）:
      - __init__
      - subscribe
      - on_data_changed
      - on_tick
      - get_bar
      - bar_hash
    """

    def __init__(
        self,
        state: Any,
        bus: Optional[Any] = None,
        periods: Optional[List[str]] = None,
    ) -> None:
        self.state = state
        self.bus = bus
        self.periods = list(periods or DEFAULT_PERIODS)
        self._bar_hashes: Dict[str, str] = {}
        self._enabled = False

    def subscribe(self) -> None:
        """订阅 ``DataChanged(tick)`` 事件；幂等，重复调用无额外副作用。

        I44：与 SnapshotBuilder/engine 订阅模式对齐 —— 直接方法引用 + ``EVENT_*`` 常量，
        替代原 lambda 包装模块函数 + 硬编码字符串 ``"DataChanged"``。EventBus 按
        ``type(event).__name__`` 分派，handler 仅收 DataChanged 实例，无需 isinstance 复检。
        """
        if self._enabled or not is_event_bus(self.bus):
            return
        self.bus.subscribe(EVENT_DATA_CHANGED, self.on_data_changed)
        self._enabled = True

    def on_data_changed(self, event: DataChanged) -> None:
        """``DataChanged`` 事件处理器：仅处理 tick 源，bar 源跳过（避免无限循环）。

        I44：原模块函数 ``_on_data_changed`` 收敛为方法。EventBus 已按 ``type(event).__name__``
        分派，handler 仅收 DataChanged 实例，故删除冗余 isinstance 复检与 getattr 防御。
        """
        if event.source != "tick":
            return
        self.on_tick(event.codes or [])

    def on_tick(self, codes: List[str]) -> None:
        """根据 ``latest_tick`` 更新 ``codes`` 对应的多周期 bars。

        仅当某个周期 bar 发生推进（新 bucket 或当前 bar 内容变化）时，
        发布 ``DataChanged(bar, period)``。

        当 bar 闭合（bucket_ts 推进）时，将闭合 bar 追加到
        ``state.bars_history[period][code]``。
        """
        if not codes:
            return

        now = time_at(state=self.state)
        for period in self.periods:
            period_bars = self.state.bars.setdefault(period, {})
            period_advanced: List[str] = []

            for code in codes:
                tick = self.state.latest_tick.get(code)
                if not isinstance(tick, dict):
                    continue
                ts = float(tick.get("_ts", now))
                bucket_ts = _bar_bucket_ts(ts, period)

                existing = period_bars.get(code)
                if isinstance(existing, dict) and existing.get("bucket_ts") == bucket_ts:
                    new_bar = _merge_tick(existing, tick)
                else:
                    if isinstance(existing, dict):
                        _append_closed_bar(self.state, period, code, existing)
                    new_bar = _new_bar_from_tick(tick, bucket_ts)

                new_bar["_hash"] = _hash_bar(new_bar)
                if existing is None or existing.get("_hash") != new_bar["_hash"]:
                    period_bars[code] = new_bar
                    period_advanced.append(code)

            # I21：合并 if/else 公共前缀——两分支均更新 _bar_hashes，
            # 仅 if 分支额外发布 DataChanged(bar)；消除 2 行重复赋值。
            self._bar_hashes[period] = _hash_period_bars(period_bars)
            if period_advanced:
                _publish_bar_changed(self, period, period_advanced)

    def get_bar(self, period: str, code: str) -> Optional[Dict[str, Any]]:
        """读取指定周期某只股票的当前 bar。"""
        return self.state.bars.get(period, {}).get(code)

    def bar_hash(self, field_refs: Optional[List[str]] = None) -> str:
        """按字段依赖聚合当前 bar_hash。

        ``field_refs`` 中每项形如 ``bars:<period>:<field>``；未提供时返回全周期摘要。
        """
        periods: List[str] = []
        if field_refs:
            for ref in field_refs:
                parts = ref.split(":")
                if len(parts) >= 2 and parts[0] == "bars" and parts[1]:
                    periods.append(parts[1])
        if not periods:
            periods = list(self.periods)

        parts: List[str] = []
        for period in sorted(set(periods)):
            h = self._bar_hashes.get(period, "")
            parts.append(f"{period}:{h}")
        payload = "\x00".join(parts)
        return hashlib.md5(payload.encode("utf-8")).hexdigest()


def make_bars_history_getter(state: Any, periods: Optional[List[str]] = None) -> Callable:
    """构造 bars_history_getter 闭包，供 DataQuery 注入。

    从 ``state.bars_history[period][code]`` 读取闭合 bar 历史序列，
    转换为标准 K 线 DataFrame；末尾追加 ``state.bars[period][code]`` 当前未闭合 bar。

    Args:
        state: PoolState 实例。
        periods: 支持的周期列表，默认 DEFAULT_PERIODS。

    Returns:
        Callable[[symbol, period], pd.DataFrame]: 注入 DataQuery 的 getter。
    """
    import pandas as pd

    _periods = list(periods or DEFAULT_PERIODS)

    def _bucket_ts_to_hhmm(bucket_ts: int) -> int:
        dt = datetime.fromtimestamp(bucket_ts, tz=timezone.utc)
        return dt.hour * 100 + dt.minute

    def getter(symbol: str, period: str) -> pd.DataFrame:
        rows: List[Dict[str, Any]] = []
        hist = state.bars_history.get(period, {}).get(symbol, [])
        if len(hist) > 0:
            import logging
            logging.getLogger("bar_debug").warning(
                "BARS_GETTER symbol=%s period=%s hist_len=%d",
                symbol, period, len(hist)
            )
        for bar in hist:
            row: Dict[str, Any] = {}
            bucket_ts = bar.get("bucket_ts", 0)
            row["time"] = _bucket_ts_to_hhmm(int(bucket_ts))
            for col in ("open", "high", "low", "close", "volume"):
                row[col] = bar.get(col, 0)
            rows.append(row)

        current = state.bars.get(period, {}).get(symbol)
        if isinstance(current, dict):
            row = {}
            bucket_ts = current.get("bucket_ts", 0)
            row["time"] = _bucket_ts_to_hhmm(int(bucket_ts))
            for col in ("open", "high", "low", "close", "volume"):
                row[col] = current.get(col, 0)
            rows.append(row)

        if not rows:
            return pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])
        return pd.DataFrame(rows)

    return getter


__all__ = ["BarComposer", "DEFAULT_PERIODS", "make_bars_history_getter"]
