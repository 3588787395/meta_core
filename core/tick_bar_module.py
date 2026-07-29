"""TickBar 模块：事件驱动的 tick 接收 + K 线合成。

合并 core/data_updater.py + core/bar_composer.py + services/minute_aggregator.py
四个组件为统一 ``TickBarModule`` 类（core/tick_source.py 已在 SubTask 27.1 删除，
相关 TickSource/MockDataSource 由 core.domain.tick_source 提供）。

仅与 EventBus 交互，内部持有 4 个组件实例（不暴露给外部）。
向后兼容：原 4 个组件类公共方法签名不变，仍可被其他模块直接调用（迁移期内）。
"""
from __future__ import annotations

import hashlib
import json
import logging
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, NamedTuple, Optional, Set

import numpy as np
import pandas as pd

from core.event_bus import (
    _event_handler,
    EVENT_DATA_CHANGED,
    BarComposed,
    DataChanged,
    EventBus,
    ModeChanged,
    PoolLoaded,
    ReplayStep,
    SimulationStep,
    TickDue,
    TickReceived,
    is_event_bus,
)
from core.domain import MockDataSource, TickSource, _hash_tick, time_at
from core.tick_table import TickTable

logger = logging.getLogger(__name__)

# ====================================================================
# 模块常量
# ====================================================================
_DEFAULT_PERIODS = ["1m", "5m", "15m", "30m", "60m"]
DEFAULT_PERIODS = ["1m", "5m", "15m", "30m", "60m", "1d"]
_BARS_HISTORY_MAXLEN = 300


# ====================================================================
# data_updater 辅助函数
# ====================================================================
def _now() -> float:
    """统一委托 ``time_at("wall")``：data_updater 无 state 上下文，恒用系统墙钟。"""
    return time_at("wall")


def _now_from_state(state: Any) -> float:
    """统一委托 ``time_at(state=state)``，不二次包装、不 fallback。

    G2 硬约束：仿真/实盘同代码，仅由 ``time_at`` 单一入口按 state.time_source
    决定时间源。本函数不得对返回值做任何"为0则回退墙钟"的特殊处理——
    那会绕过 time_at 在仿真模式返回 0 的语义、形成"仿真专用分支"分裂。
    0 即 0（仿真冷启动前的合法值），由调用方按业务需要处理。
    """
    return time_at(state=state)


def publish_data_changed(bus, state, source, codes, ts, data=None, period=None, bar_hash=""):
    """统一 DataChanged 事件发布器。

    合并原 _publish_tick_changed 和 _publish_bar_changed，消除同构重复。
    - source="tick": 发布 DataChanged(tick)
    - source="bar": 发布 DataChanged(bar) + 对每个 code 发布 BarComposed

    G2 约束：ts 由调用方传入，不在此处调用 time_at(state)。
    """
    if not codes:
        return
    if not is_event_bus(bus):
        return
    state.add_changed_codes(list(codes))
    event = DataChanged(
        ts=ts,
        bar_hash=bar_hash,
        codes=list(codes),
        source=source,
        data=data,
        period=period or "",
    )
    bus.publish(event)
    if source == "bar":
        for code in codes:
            bar = state.bars.get(period, {}).get(code) if period else None
            if bar:
                bus.publish(BarComposed(
                    bar=dict(bar), period=period, code=code, ts=ts,
                ))


def _publish_tick_batch(
    bus: Any,
    tick_data: Optional[Dict[str, Any]],
    ts: Optional[float],
    *,
    ts_fallback: float = 0.0,
) -> None:
    """统一批量发布 TickReceived 事件。

    消除 ``for code, tick in tick_data.items(): ... TickReceived(tick_data=tick_copy,
    code=str(code), ts=ts)`` 样板，集中维护字段复制与发布逻辑。

    Args:
        bus: EventBus 实例。
        tick_data: ``{code: tick_dict}`` 映射；空或非 dict 直接返回。
        ts: 统一时间戳。若为 ``None``，则对每个 tick 使用
            ``float(tick.get("_ts", ts_fallback))``（保留 _step_once_impl
            "每 tick 自带 _ts 优先"语义）。
        ts_fallback: ``ts`` 为 ``None`` 且 tick 缺失 ``_ts`` 时的回退值。

    单个 tick 发布失败不影响后续 tick（per-tick try/except 错误隔离）。
    """
    if not tick_data or not isinstance(tick_data, dict):
        return
    for code, tick in tick_data.items():
        if not code or not isinstance(tick, dict):
            continue
        try:
            tick_copy = dict(tick)
            tick_copy["code"] = str(code)
            ts_use = ts if ts is not None else float(tick.get("_ts", ts_fallback))
            bus.publish(TickReceived(
                tick_data=tick_copy, code=str(code), ts=ts_use,
            ))
        except Exception as ex:
            logger.warning("TickReceived publish failed for %s: %s", code, ex)


# ====================================================================
# DataUpdater：行情数据更新器（来自 core/data_updater.py）
# ====================================================================
class DataUpdater:
    """行情数据更新器。

    属性（≤ 5）:
      - state: PoolState 运行时表真相源
      - bus: EventBus（可选）
      - data_source: 当前绑定的数据源配置行
      - _fundamentals: 基本面字段缓存（写入 state.fundamentals 前）
      - _watermark: 每只股票最新 _ts 水位线（冗余缓存，避免遍历 state.latest_tick）

    方法（≤ 5）:
      - __init__
      - bind
      - apply_data
      - _apply_code_tick
      - _hash_aggregate
    """

    def __init__(self, state: Any, bus: Optional[Any] = None) -> None:
        self.state = state
        self.bus = bus
        self.data_source: Dict[str, Any] = {}
        self._fundamentals: Dict[str, Any] = {}
        self._watermark: Dict[str, float] = {}

    def bind(self, data_source: Dict[str, Any]) -> None:
        """绑定 ``data_sources.json`` 配置行，运行期只读。"""
        self.data_source = dict(data_source)

    def apply_data(self, tick_data: Optional[Dict[str, Any]]) -> bool:
        """应用外部行情推送，返回是否有股票的行情发生推进。

        ``tick_data`` 格式为 ``{code: {open, high, low, close, volume, amount, ...}}``。
        首次出现的股票写入 ``latest_tick`` 并发布 ``DataChanged(tick)``（供 ``BarComposer``
        同步合成 bars），但不置 ``dirty.data``（冷启动无意义推进）；后续仅当 ``_ts``
        严格增大时才覆盖、置脏并再次发布事件。
        """
        if not tick_data:
            return False

        advanced_codes: List[str] = []
        updated: Dict[str, Any] = {}

        for code, raw in tick_data.items():
            if not isinstance(raw, dict):
                continue
            tick = dict(raw)
            tick["code"] = str(code)
            applied, advanced = self._apply_code_tick(str(code), tick)
            if applied:
                updated[str(code)] = self.state.latest_tick.get(str(code), tick)
            if advanced:
                advanced_codes.append(str(code))

        if not updated:
            return False

        # Task 2: 顶层 _hash/_ts 元数据由 TickTable 内部管理，不再直写 latest_tick

        # 记录本 tick 有变化的股票代码（updated 包含首次写入和推进的股票）
        updated_codes = list(updated.keys())
        self.state.add_changed_codes(updated_codes)

        data_payload = updated if updated else None
        publish_data_changed(self.bus, self.state, "tick", updated_codes, _now_from_state(self.state), data=data_payload)
        if updated_codes:
            self.state.mark_data_dirty()
        if advanced_codes:
            return True
        return False

    def _apply_code_tick(self, code: str, tick: Dict[str, Any]) -> tuple[bool, bool]:
        """单只股票 tick 应用。返回 (是否写入, 是否推进)。

        I13：覆盖前将旧 tick 快照到 ``state.prev_tick[code]``，激活 TickTable 双周期视图
        （cross 模式 prev_column 不再恒 None）。
        """
        existing = self.state.latest_tick.get(code)
        new_ts = float(tick.get("_ts", _now_from_state(self.state)))
        new_hash = _hash_tick(tick)

        if existing is None:
            tick["_ts"] = new_ts
            tick["_hash"] = new_hash
            # Task 2: 通过 tick_table 统一入口（snapshot + 增量合并 + update）
            tick_table = getattr(self.state, "tick_table", None)
            if tick_table is not None:
                _snap = tick_table.snapshot()
                _snap[code] = tick
                tick_table.update(_snap)
                self.state.latest_tick = _snap
            else:
                self.state.latest_tick = {**self.state.latest_tick, code: tick}
            self._watermark[code] = new_ts
            return True, False

        if not isinstance(existing, dict):
            return False, False

        old_ts = float(existing.get("_ts", 0.0))

        if new_ts < old_ts:
            # 乱序 tick，直接丢弃
            return False, False

        if new_ts == old_ts and existing.get("_hash") == new_hash:
            # 幂等忽略
            return False, False

        # 覆盖写入（同 _ts 但 hash 不同也覆盖，但不置脏）
        # I13：推进前快照 prev_tick（剔除 _hash/_ts 元数据，仅保留行情字段）
        self.state.prev_tick[code] = {k: v for k, v in existing.items() if not k.startswith("_")}
        tick["_ts"] = new_ts
        tick["_hash"] = new_hash
        # Task 2: 通过 tick_table 统一入口（snapshot + 增量合并 + update）
        tick_table = getattr(self.state, "tick_table", None)
        if tick_table is not None:
            _snap = tick_table.snapshot()
            _snap[code] = tick
            tick_table.update(_snap)
            self.state.latest_tick = _snap
        else:
            self.state.latest_tick = {**self.state.latest_tick, code: tick}
        self._watermark[code] = new_ts
        advanced = new_ts > old_ts
        return True, advanced

    def _hash_aggregate(self) -> str:
        """对所有 per-code tick（不含顶层 _hash/_ts）做聚合摘要。

        I26：委托 ``PoolState._hash_tick_data``，与 ``update_latest_tick`` 路径
        使用同一算法。原算法（``md5("{code}:{tick._hash}" join \\x00)``）已被
        ``PoolState._hash_tick_data`` 接管，本方法保留为命名访问器（语义清晰）。
        """
        return type(self.state)._hash_tick_data(self.state.latest_tick)


# ====================================================================
# bar_composer 辅助函数
# ====================================================================
def _to_local_datetime(ts: float) -> datetime:
    """将时间戳转换为本地datetime。
    支持两种格式：
    - Unix时间戳（>= 1e9，如1700000000）：直接转换
    - 日内秒数（< 86400，如34500=09:30:00）：使用今天的日期作为基准
    """
    if ts is None:
        return datetime.now()
    ts = float(ts)
    if ts < 86400:
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        return today + timedelta(seconds=ts)
    try:
        return datetime.fromtimestamp(ts)
    except (OSError, ValueError, OverflowError):
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        return today + timedelta(seconds=max(0, ts % 86400))


def _bar_bucket_ts(ts: float, period: str) -> int:
    """根据时间戳计算该周期 bar 的桶起始时间戳（秒）。

    使用本地时区计算K线桶，确保1分钟/5分钟K线在本地时间整点对齐
    （如A股09:30/09:35等，而非UTC时间01:30/01:35）。
    支持日内秒数（仿真模式）和Unix时间戳（实盘模式）。
    返回值类型与输入ts一致：日内秒数输入返回日内秒数，Unix时间戳返回Unix时间戳。
    """
    is_virtual = float(ts) < 86400
    dt = _to_local_datetime(ts)
    midnight = datetime(dt.year, dt.month, dt.day)
    seconds_since_midnight = (dt - midnight).total_seconds()
    if period == "1d":
        return 0 if is_virtual else int(midnight.timestamp())
    minutes = int(period[:-1]) if period[:-1].isdigit() else 1
    minutes_since_midnight = int(seconds_since_midnight // 60)
    bucket = (minutes_since_midnight // minutes) * minutes
    if is_virtual:
        return bucket * 60
    return int(midnight.timestamp() + bucket * 60)


def _hash_bar(bar: Dict[str, Any]) -> str:
    content = {k: v for k, v in bar.items() if k != "_hash"}
    try:
        payload = json.dumps(content, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        payload = str(sorted(content.items()))
    return hashlib.md5(payload.encode("utf-8")).hexdigest()


def _new_bar_from_tick(tick: Dict[str, Any], bucket_ts: int) -> Dict[str, Any]:
    close = float(tick.get("close", 0.0) or 0.0)
    open_p = float(tick.get("open", close) or close)
    high = float(tick.get("high", close) or close)
    low = float(tick.get("low", close) or close)
    high = max(high, close, open_p)
    low = min(low, close, open_p)
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
    new_bar["high"] = max(new_bar.get("high", close), tick_high, close)
    new_bar["low"] = min(new_bar.get("low", close), tick_low, close)
    new_bar["volume"] = int(new_bar.get("volume", 0)) + int(tick.get("volume", 0) or 0)
    new_bar["amount"] = float(new_bar.get("amount", 0.0)) + float(tick.get("amount", 0.0) or 0.0)
    return new_bar


def _compose_5m_from_1m(state: Any, code: str, bucket_ts: int, tick: Dict[str, Any]) -> Dict[str, Any]:
    """由已闭合 1 分钟 K 线与当前未闭合 1 分钟 K 线合成 5 分钟 K 线。

    取 ``bucket_ts`` 所在 5 分钟窗口内的所有 1 分钟 bar：
    ``window_start <= bucket_ts_1m < window_start + 300``，
    按时间顺序聚合 open/high/low/close/volume/amount。
    当窗口内尚无 1 分钟数据时（首次 tick），退化为以当前 tick 生成新 bar。
    """
    window_start = bucket_ts
    window_end = bucket_ts + 300

    hist_1m = state.bars_history.get("1m", {}).get(code, [])
    cur_1m = state.bars.get("1m", {}).get(code)

    bars_1m: List[Dict[str, Any]] = [
        dict(b) for b in hist_1m
        if isinstance(b, dict) and window_start <= b.get("bucket_ts", 0) < window_end
    ]
    if isinstance(cur_1m, dict) and window_start <= cur_1m.get("bucket_ts", 0) < window_end:
        bars_1m.append({k: v for k, v in cur_1m.items() if k != "_hash"})

    if not bars_1m:
        return _new_bar_from_tick(tick, bucket_ts)

    bars_1m.sort(key=lambda b: b.get("bucket_ts", 0))
    open_p = float(bars_1m[0].get("open", 0.0) or 0.0)
    high = max(float(b.get("high", 0.0) or 0.0) for b in bars_1m)
    low = min(float(b.get("low", 0.0) or 0.0) for b in bars_1m)
    close = float(bars_1m[-1].get("close", 0.0) or 0.0)
    volume = sum(int(b.get("volume", 0) or 0) for b in bars_1m)
    amount = sum(float(b.get("amount", 0.0) or 0.0) for b in bars_1m)

    return {
        "code": code,
        "open": open_p,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "amount": amount,
        "bucket_ts": bucket_ts,
        "_hash": "",
    }


def _hash_period_bars(period_bars: Dict[str, Any]) -> str:
    parts: List[str] = []
    for code in sorted(period_bars.keys()):
        bar = period_bars[code]
        if isinstance(bar, dict):
            parts.append(f"{code}:{bar.get('_hash', '')}")
    payload = "\x00".join(parts)
    return hashlib.md5(payload.encode("utf-8")).hexdigest()


def _append_closed_bar(state: Any, period: str, code: str, bar: Dict[str, Any]) -> None:
    """将闭合 bar 追加到 ``state.bars_history[period][code]``，限制 maxlen。"""
    hist = state.bars_history.setdefault(period, {}).setdefault(code, [])
    clean = {k: v for k, v in bar.items() if k != "_hash"}
    hist.append(clean)
    if len(hist) > _BARS_HISTORY_MAXLEN:
        del hist[0]


# ====================================================================
# BarComposer：多周期 K 线组合器（来自 core/bar_composer.py）
# ====================================================================
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
        self.on_tick(event.codes or [], event.ts)

    def on_tick(self, codes: List[str], event_ts: Optional[float] = None) -> None:
        """根据 ``latest_tick`` 更新 ``codes`` 对应的多周期 bars。

        1 分钟 K 线由 tick 直接聚合；5 分钟 K 线由已闭合 1 分钟 K 线与当前
        未闭合 1 分钟 K 线合成。仅当某个周期 bar 发生推进（新 bucket 或当前
        bar 内容变化）时，发布 ``DataChanged(bar, period)`` 与 ``BarComposed``。

        当 bar 闭合（bucket_ts 推进）时，将闭合 bar 追加到
        ``state.bars_history[period][code]``。

        G2 硬约束：``event_ts`` 来自上游 DataChanged(tick) 事件，由事件流传递，
        不在此处重复调用 ``time_at(state)``。实盘模式下 ``event.ts`` 同样来自
        ``time_at(state)``（PoolState），仿真/实盘同代码路径。``event_ts`` 为
        ``None`` 时（异常路径）退化到 ``time_at(state)`` 以保证健壮性。
        """
        if not codes:
            return

        now = event_ts if event_ts is not None else time_at(state=self.state)
        # 确保 1m 先于 5m 处理，使 5m 能读取到最新闭合的 1m K 线
        ordered_periods = ["1m"] + [p for p in self.periods if p != "1m"]

        for period in ordered_periods:
            period_bars = self.state.bars.setdefault(period, {})
            period_advanced: List[str] = []

            for code in codes:
                tick = self.state.latest_tick.get(code)
                if not isinstance(tick, dict):
                    continue
                ts = float(tick.get("_ts", now))
                bucket_ts = _bar_bucket_ts(ts, period)

                existing = period_bars.get(code)
                if isinstance(existing, dict) and existing.get("bucket_ts") != bucket_ts:
                    _append_closed_bar(self.state, period, code, existing)
                    existing = None

                if period == "5m":
                    new_bar = _compose_5m_from_1m(self.state, code, bucket_ts, tick)
                elif existing is not None:
                    new_bar = _merge_tick(existing, tick)
                else:
                    new_bar = _new_bar_from_tick(tick, bucket_ts)

                new_bar["_hash"] = _hash_bar(new_bar)
                if existing is None or existing.get("_hash") != new_bar["_hash"]:
                    period_bars[code] = new_bar
                    period_advanced.append(code)

            self._bar_hashes[period] = _hash_period_bars(period_bars)
            if period_advanced:
                publish_data_changed(self.bus, self.state, "bar", period_advanced, now, period=period, bar_hash=self._bar_hashes.get(period, ""))

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
        dt = _to_local_datetime(float(bucket_ts))
        return dt.hour * 100 + dt.minute

    def getter(symbol: str, period: str) -> pd.DataFrame:
        rows: List[Dict[str, Any]] = []
        hist = state.bars_history.get(period, {}).get(symbol, [])
        if len(hist) > 0:
            import logging
            logging.getLogger("bar_debug").debug(
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


# ====================================================================
# Tick / Min1Aggregator：分钟线合成器（来自 services/minute_aggregator.py）
# ====================================================================
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
        self.vol = np.zeros(self.n, dtype=np.int64)

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

    def get_5min_bar(self, symbol: str) -> Optional[dict]:
        """从已闭合的 1min bar 聚合最新一根 5min OHLCV bar。

        取最近 5 根已闭合 1min bar，合成一根 5min bar。
        不足 5 根时返回 None。

        用于 KDJ_5MIN_CROSS 等基于 5 分钟周期的公式求值。
        """
        bars = self.closed_bars.get(symbol)
        if not bars or len(bars) < 5:
            return None
        last5 = list(bars)[-5:]
        df = pd.DataFrame(last5)
        return {
            'time': int(df['time'].iloc[0]),
            'open': float(df['open'].iloc[0]),
            'high': float(df['high'].max()),
            'low': float(df['low'].min()),
            'close': float(df['close'].iloc[-1]),
            'volume': int(df['volume'].sum()),
        }

    def get_5min_bars(self, symbol: str) -> Optional[pd.DataFrame]:
        """从已闭合的 1min bar 聚合所有 5min OHLCV bar 序列。

        按每 5 根 1min bar 合成一根 5min bar，从最早一根开始。
        不足 5 根时返回 None。
        """
        bars = list(self.closed_bars.get(symbol, ()))
        if not bars or len(bars) < 5:
            return None
        df = pd.DataFrame(bars)
        result = []
        n = len(df)
        for i in range(0, n - n % 5, 5):
            chunk = df.iloc[i:i+5]
            result.append({
                'time': int(chunk['time'].iloc[0]),
                'open': float(chunk['open'].iloc[0]),
                'high': float(chunk['high'].max()),
                'low': float(chunk['low'].min()),
                'close': float(chunk['close'].iloc[-1]),
                'volume': int(chunk['volume'].sum()),
            })
        if not result:
            return None
        return pd.DataFrame(result)

    def get_all_5min_bars(self) -> Dict[str, dict]:
        """返回所有标的的最新 5min bar。"""
        result = {}
        for symbol in self.symbols:
            bar = self.get_5min_bar(symbol)
            if bar is not None:
                result[symbol] = bar
        return result

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


# ====================================================================
# TickBarModule 内部状态
# ====================================================================
class _InternalState:
    """TickBarModule 内部轻量状态，提供 DataUpdater/BarComposer 所需最小接口。

    不依赖 PoolState；``time_source`` 由 SimulationStep/ReplayStep 事件更新，
    使 ``time_at(state=...)`` 在仿真/回放模式下返回虚拟时钟。
    """

    def __init__(self) -> None:
        self.latest_tick: Dict[str, Any] = {}
        self.prev_tick: Dict[str, Any] = {}
        self.bars: Dict[str, Any] = {}
        self.bars_history: Dict[str, Any] = {}
        self.time_source: Dict[str, Any] = {}
        self.changed_codes: Set[str] = set()
        # Task 2: TickTable 作为统一入口，latest_tick 由 tick_table.data 同步
        self.tick_table = TickTable()

    def mark_data_dirty(self) -> None:
        """接口契约：TickBarModule 内部不消费脏标记。"""

    def add_changed_codes(self, codes) -> None:
        """记录变化的股票代码。"""
        if not codes:
            return
        for code in codes:
            if code:
                self.changed_codes.add(str(code))

    def take_changed_codes(self) -> List[str]:
        """获取并清空changed_codes。"""
        result = sorted(self.changed_codes)
        self.changed_codes.clear()
        return result

    def bar_hash(self) -> str:
        return self.latest_tick.get("_hash", "")

    @staticmethod
    def _hash_tick_data(tick_data: Dict[str, Any]) -> str:
        """聚合 hash，与 PoolStateMixin._hash_tick_data 算法一致。"""
        parts: List[str] = []
        for code in sorted(tick_data.keys()):
            if isinstance(code, str) and code.startswith("_"):
                continue
            tick = tick_data[code]
            if not isinstance(tick, dict):
                continue
            per_hash = tick.get("_hash")
            if not per_hash:
                content = {k: v for k, v in tick.items()
                           if k not in ("_ts", "_hash")}
                try:
                    payload = json.dumps(content, sort_keys=True,
                                         ensure_ascii=False, default=str)
                except (TypeError, ValueError):
                    payload = str(sorted(content.items()))
                per_hash = hashlib.md5(payload.encode("utf-8")).hexdigest()
            parts.append(f"{code}:{per_hash}")
        return hashlib.md5("\x00".join(parts).encode("utf-8")).hexdigest()


# ====================================================================
# TickBarModule：统一对外入口
# ====================================================================
class TickBarModule:
    """TickBar 模块：最新 tick + K 线合成。仅与 EventBus 交互。

    内部组合 4 个组件（TickSource / DataUpdater / BarComposer / Min1Aggregator），
    不暴露给外部。外部仅通过事件与模块交互：
      - 订阅：TickReceived / DataChanged / SimulationStep / ReplayStep
      - 发布：DataChanged(source=tick) / BarComposed
    """

    def __init__(self, bus: EventBus, config: Optional[Dict[str, Any]] = None) -> None:
        self._bus = bus
        self._config = dict(config or {})
        self._state = _InternalState()
        # 当前运行模式（live/replay/simulation）；由 ModeChanged 事件更新
        self._mode_id: str = "live"

        codes: List[str] = list(self._config.get("codes", []))
        periods: List[str] = list(self._config.get("periods", _DEFAULT_PERIODS))

        # --- TickSource（仿真默认 MockDataSource；可通过 config.tick_source 注入）---
        custom_ts = self._config.get("tick_source")
        if isinstance(custom_ts, TickSource):
            self._tick_source: TickSource = custom_ts
        else:
            self._tick_source = MockDataSource(
                codes=codes,
                clock_start=float(self._config.get("clock_start", 0.0)),
                price_range=self._config.get("price_range", (5.0, 200.0)),
                change_pct_std=float(self._config.get("change_pct_std", 2.0)),
                volume_lognorm_mu=float(self._config.get("volume_lognorm_mu", 14.0)),
                volume_lognorm_sigma=float(self._config.get("volume_lognorm_sigma", 2.0)),
            )

        # --- DataUpdater（bus=None：由模块统一编排 tick 事件发布，避免重复）---
        self._data_updater = DataUpdater(self._state, bus=None)

        # --- BarComposer（传入 bus 以发布 DataChanged(bar)，不调用 subscribe 避免重复订阅）---
        self._bar_composer = BarComposer(self._state, bus=self._bus, periods=periods)

        # --- Min1Aggregator（1min K 线合成）---
        self._minute_aggregator = Min1Aggregator(symbols=codes)

        self._register_subscribers()

    # ------------------------------------------------------------------
    # 事件订阅注册
    # ------------------------------------------------------------------
    def _register_subscribers(self) -> None:
        # G2：TickDue 由 MockDataSource 定时器发布，TickBarModule 订阅后生成 tick 数据
        self._bus.subscribe(TickDue, self._on_tick_due)
        self._bus.subscribe(TickReceived, self._on_tick_received)
        self._bus.subscribe(DataChanged, self._on_data_changed)
        self._bus.subscribe(SimulationStep, self._on_simulation_step)
        self._bus.subscribe(ReplayStep, self._on_replay_step)
        self._bus.subscribe(ModeChanged, self._on_mode_changed)
        # 订阅 PoolLoaded：从 pool_config 提取股票 codes 重建 MockDataSource，
        # 解决 lifespan 创建时 config 无 codes 导致 _on_simulation_step 空转的问题
        self._bus.subscribe(PoolLoaded, self._on_pool_loaded)

    # ------------------------------------------------------------------
    # PoolLoaded → 提取 codes → 重建 MockDataSource
    # ------------------------------------------------------------------
    @_event_handler("_on_pool_loaded")
    def _on_pool_loaded(self, event: PoolLoaded) -> None:
        """收到 PoolLoaded 事件时，从 pool_config.nodes 提取股票 codes 并重建 MockDataSource。

        lifespan 创建 TickBarModule 时 config 来自 defaults.json，无 codes 字段，
        导致 _tick_source.codes 为空，_on_simulation_step 永远 ticks_count=0。
        本 handler 从 pool_config.nodes[*].params.stocks[*].code 提取所有股票代码，
        用 codes 重建 MockDataSource，使后续 SimulationStep 事件能正确生成 tick。
        """
        pool_config = event.pool_config or {}
        nodes = pool_config.get("nodes", []) if isinstance(pool_config, dict) else []
        codes: List[str] = []
        for node in nodes:
            if not isinstance(node, dict):
                continue
            params = node.get("params") or {}
            stocks = params.get("stocks") if isinstance(params, dict) else None
            if not isinstance(stocks, list):
                continue
            for s in stocks:
                if isinstance(s, dict):
                    c = s.get("code", "")
                    if c:
                        codes.append(str(c))
                elif isinstance(s, str) and s:
                    codes.append(s)
        if codes:
            # 保留原 clock_start/price_range 等配置，仅更新 codes
            self._tick_source = MockDataSource(
                codes=codes,
                clock_start=float(self._config.get("clock_start", 0.0)),
                price_range=self._config.get("price_range", (5.0, 200.0)),
                change_pct_std=float(self._config.get("change_pct_std", 2.0)),
                volume_lognorm_mu=float(self._config.get("volume_lognorm_mu", 14.0)),
                volume_lognorm_sigma=float(self._config.get("volume_lognorm_sigma", 2.0)),
            )
            logger.info("TickBarModule PoolLoaded 重建 MockDataSource codes_count=%d sample=%s",
                        len(codes), codes[:3])

    # ------------------------------------------------------------------
    # SubTask 20.1：ModeChanged → 切换内部数据源状态
    # ------------------------------------------------------------------
    @_event_handler("_on_mode_changed")
    def _on_mode_changed(self, event: ModeChanged) -> None:
        """模式切换时调整内部 tick 处理逻辑。

        TickBarModule 本身不直接持有 DataSource 实例（通过事件接收
        ``TickReceived``），模式切换主要是切换内部 tick 处理逻辑：
          - ``live``:        使用实时 tick 源（默认，等待外部 publish TickReceived）
          - ``replay``:      使用历史 K 线 replay（由 ``ReplayStep`` 事件驱动）
          - ``simulation``:  使用 mock tick 生成器（由 ``SimulationStep`` 事件驱动
                              MockDataSource.next_ticks）

        实现：仅记录 ``self._mode_id``，由 ``_on_simulation_step`` /
        ``_on_replay_step`` / ``_on_tick_received`` 根据 mode_id 选择处理路径。
        """
        new_mode = event.mode_id or "live"
        prev = self._mode_id
        self._mode_id = new_mode
        logger.info(
            "TickBarModule 模式切换: %s -> %s（数据源由事件驱动切换）",
            prev, new_mode,
        )

    # ------------------------------------------------------------------
    # 公共 API：直接应用 tick 数据（不通过事件总线）
    # ------------------------------------------------------------------
    def apply_data(self, tick_data: Optional[Dict[str, Any]]) -> List[str]:
        """直接应用 tick 数据并触发 K 线合成。

        Args:
            tick_data: {code: {open, high, low, close, volume, amount, ...}} 格式的行情数据

        Returns:
            有变化的股票代码列表
        """
        if not tick_data:
            return []

        # 先清空 changed_codes
        self._state.changed_codes.clear()

        # 1. DataUpdater 更新 latest_tick（bus=None，不会发布事件）
        advanced = self._data_updater.apply_data(tick_data)

        # 2. 获取更新的 codes
        updated_codes = [str(code) for code in tick_data.keys() if isinstance(tick_data.get(code), dict)]
        if not updated_codes:
            return []

        # 3. 同步虚拟时钟：G2 硬约束——仿真/实盘同代码，统一通过 time_at(state) 决定 ts。
        # time_at 已按 state.time_source.driver_type 在内部决定返回虚拟时钟/墙钟/0。
        # 调用方不得再做 driver 分支或 fallback，否则破坏统一入口语义。
        ts = time_at(state=self._state)
        if ts <= 0:
            # 仿真冷启动前虚拟时钟可能为 0：从 tick._ts 推断首个时钟（如 K 线回放 bar_ts）
            for code in updated_codes:
                tick = self._state.latest_tick.get(code)
                if isinstance(tick, dict) and tick.get("_ts", 0) > 0:
                    inferred = float(tick["_ts"])
                    cur_driver = (self._state.time_source or {}).get("driver_type", "virtual")
                    self._state.time_source = {"current_ts": inferred, "driver_type": cur_driver}
                    ts = inferred
                    break

        # 4. 发布 DataChanged(source=tick) 事件（同步触发 _on_data_changed 合成 K 线）
        self._bus.publish(DataChanged(
            ts=ts,
            bar_hash=self._state.bar_hash(),
            codes=updated_codes,
            source="tick",
            data=tick_data,
        ))

        # 5. 返回所有变化的 codes
        return self._state.take_changed_codes()

    # ------------------------------------------------------------------
    # G2：TickDue → 生成 tick 数据 → 发布 TickReceived
    # ------------------------------------------------------------------
    @_event_handler("_on_tick_due")
    def _on_tick_due(self, event: TickDue) -> None:
        """MockDataSource 定时器到时信号：生成实际 tick 数据并发布 TickReceived。

        G2 要求引擎只发事件不执行计算，tick 数据生成由 TickBarModule 在订阅
        TickDue 后调用 ``self._tick_source.get_tick`` 完成，再经 TickReceived
        事件进入 _on_tick_received 写 latest_tick / 发布 DataChanged(tick)。
        """
        code = event.code
        # G2 硬约束：仿真/实盘同代码，统一通过 time_at(state) 决定 ts。
        # event.ts 优先（虚拟时钟秒），为 0 则用 state 虚拟时钟，
        # 仍为 0 则保持 0（仿真冷启动前）。
        ts = event.ts or time_at(state=self._state) or 0.0
        if not code:
            return
        tick_source = self._tick_source
        if tick_source is None:
            return
        tick = tick_source.get_tick(code, ts)
        if not isinstance(tick, dict):
            return
        # 单 tick 包装成单元素 dict，复用 _publish_tick_batch 统一发布路径。
        _publish_tick_batch(self._bus, {code: tick}, ts)

    # ------------------------------------------------------------------
    # SubTask 5.2：TickReceived → 写 latest_tick → 发 DataChanged
    # ------------------------------------------------------------------
    @_event_handler("_on_tick_received")
    def _on_tick_received(self, event: TickReceived) -> None:
        tick_data = event.tick_data
        if not isinstance(tick_data, dict):
            return
        code = event.code or tick_data.get("code", "") or tick_data.get("symbol", "") or tick_data.get("stock_code", "")
        if not code:
            if isinstance(tick_data, dict) and len(tick_data) > 0:
                for k in ("code", "symbol", "stock_code", "secucode"):
                    if k in tick_data and tick_data[k]:
                        code = str(tick_data[k])
                        break
        if not code:
            return
        code = str(code)
        tick_copy = dict(tick_data)
        tick_copy["code"] = code
        self._data_updater.apply_data({code: tick_copy})
        # G2 硬约束：仿真/实盘同代码，统一通过 time_at(state) 决定 ts。
        # event.ts 优先（来自 TickDue 时的虚拟时钟）；为 0 则用 state 虚拟时钟；
        # state.time_source.current_ts 也为 0（仿真冷启动前）则保持 0。
        # 禁止 fallback 到 tick._ts（可能含真实 Unix 秒污染时间坐标系）。
        ts = event.ts or time_at(state=self._state) or 0.0
        self._bus.publish(DataChanged(
            ts=ts,
            bar_hash=self._state.bar_hash(),
            codes=[code],
            source="tick",
            data=tick_copy,
        ))

    # ------------------------------------------------------------------
    # SubTask 5.3：DataChanged(source=tick) → 合成 K 线 → 发 BarComposed
    # ------------------------------------------------------------------
    @_event_handler("_on_data_changed")
    def _on_data_changed(self, event: DataChanged) -> None:
        if event.source != "tick":
            return
        codes = event.codes or []
        if not codes:
            return

        # 1. Min1Aggregator 合成 1min K 线
        for code in codes:
            tick = self._state.latest_tick.get(code)
            if isinstance(tick, dict):
                self._feed_minute_aggregator(code, tick, event.ts)

        # 2. BarComposer 合成多周期 K 线（读 state.latest_tick，写 state.bars）
        # G2：传入 event.ts，使 publish_data_changed 发布的 DataChanged(bar)/BarComposed
        # 与上游 DataChanged(tick) 同源，避免 time_at(state) 在 _InternalState 未初始化时
        # 回落 time.time() 污染仿真坐标系。
        self._bar_composer.on_tick(codes, event.ts)

        # 3. 发布 BarComposed 事件
        for code in codes:
            for period in self._bar_composer.periods:
                bar = self._bar_composer.get_bar(period, code)
                if bar:
                    self._bus.publish(BarComposed(
                        bar=dict(bar), period=period, code=code, ts=event.ts,
                    ))

    def _feed_minute_aggregator(self, code: str, tick: Dict[str, Any], ts: float) -> None:
        """将 tick 转换为 Tick NamedTuple 并喂入 Min1Aggregator。"""
        try:
            price = float(tick.get("close", 0.0) or 0.0)
            volume = int(tick.get("volume", 0) or 0)
            hhmmss = self._ts_to_hhmmss(ts if ts else tick.get("_ts", 0.0))
            self._minute_aggregator.on_tick(code, Tick(
                symbol=code, time=hhmmss, price=price, volume=volume,
            ))
        except Exception as ex:
            logger.warning("MinuteAggregator feed 异常 (code=%s): %s", code, ex)

    @staticmethod
    def _ts_to_hhmmss(ts: float) -> int:
        """时间戳 → HHMMSS 整数（供 Min1Aggregator.Tick.time）。
        支持Unix时间戳和日内秒数（仿真模式）。"""
        try:
            dt = _to_local_datetime(float(ts))
            return dt.hour * 10000 + dt.minute * 100 + dt.second
        except (TypeError, ValueError, OSError):
            try:
                sec = int(float(ts))
                if 0 <= sec < 86400:
                    return (sec // 3600) * 10000 + ((sec % 3600) // 60) * 100 + (sec % 60)
            except (TypeError, ValueError):
                pass
            return 0

    # ------------------------------------------------------------------
    # SubTask 5.4：SimulationStep / ReplayStep → 生成 tick → 发 TickReceived
    # 7 步同构骨架合并至 _on_step_event，两个入口仅构造 provider_fn 并委托。
    # ------------------------------------------------------------------
    @_event_handler("_on_step_event")
    def _on_step_event(
        self,
        event,
        *,
        driver_type: str,
        provider_fn: Callable[[Dict[str, Any]], Optional[Dict[str, Any]]],
    ) -> None:
        """SimulationStep / ReplayStep 统一处理骨架。

        7 步：从 event.step 提取 ts → 设置 state.time_source →
        调用 provider_fn(step) 取 ticks → 校验 dict → 批量发布 TickReceived。
        异常由 ``_event_handler`` 装饰器统一捕获并记录日志。

        Args:
            event: SimulationStep 或 ReplayStep 事件。
            driver_type: "virtual"（仿真，ts 字段 virtual_ts）或
                "sequence"（回放，ts 字段 ts）。
            provider_fn: 接受 step dict，返回 ``{code: tick}`` 映射；
                调用方负责 _codes 非空 / replay_provider callable 等前置校验，
                校验失败返回 ``{}`` 即可（_publish_tick_batch 会安全跳过）。
        """
        step = event.step or {}
        ts_key = "virtual_ts" if driver_type == "virtual" else "ts"
        ts = float(step.get(ts_key, 0.0))
        self._state.time_source = {"current_ts": ts, "driver_type": driver_type}
        ticks = provider_fn(step)
        _publish_tick_batch(self._bus, ticks, ts)

    def _on_simulation_step(self, event: SimulationStep) -> None:
        """SimulationStep → 生成 mock tick → 发 TickReceived（委托 _on_step_event）。"""
        def _provider(step: Dict[str, Any]) -> Dict[str, Any]:
            codes = getattr(self._tick_source, '_codes', [])
            if not codes:
                return {}
            virtual_ts = float((step or {}).get("virtual_ts", 0.0))
            return self._tick_source.next_ticks(virtual_ts)
        return self._on_step_event(
            event, driver_type="virtual", provider_fn=_provider,
        )

    def _on_replay_step(self, event: ReplayStep) -> None:
        """ReplayStep → 从历史 K 线生成 tick → 发 TickReceived（委托 _on_step_event）。"""
        def _provider(step: Dict[str, Any]) -> Dict[str, Any]:
            provider = self._config.get("replay_provider")
            if not callable(provider):
                return {}
            return provider(step)
        return self._on_step_event(
            event, driver_type="sequence", provider_fn=_provider,
        )


__all__ = [
    "TickBarModule",
    "DataUpdater",
    "BarComposer",
    "Min1Aggregator",
    "Tick",
    "DEFAULT_PERIODS",
    "make_bars_history_getter",
]
