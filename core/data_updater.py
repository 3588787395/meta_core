"""数据更新层：行情 tick → latest_tick → 水位线 → DataChanged。

按 ``execute-architecture-migration`` 规格 Task 8.1 实现。
``DataUpdater`` 是唯一写入 ``PoolState.latest_tick`` 的组件，负责：
  - 从外部推送提取 per-code tick
  - 维护每只股票的水位线 ``_ts`` 与内容摘要 ``_hash``
  - 仅在 tick 推进（``_ts`` 增大）时置 ``dirty.data=True`` 并发布 ``DataChanged(tick)``

I26：``_hash_tick`` 与 ``_hash_aggregate`` 算法收敛到 ``runtime._hash_tick`` /
``PoolState._hash_tick_data``，消除双 hash 算法 bug（详见 I26.md）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .event_bus import EVENT_DATA_CHANGED, DataChanged, EventBus, is_event_bus
from .runtime import _hash_tick
from .time_util import time_at


def _now() -> float:
    """统一委托 ``time_at("wall")``：data_updater 无 state 上下文，恒用系统墙钟。"""
    return time_at("wall")


def _now_from_state(state: Any) -> float:
    """当 state 可用时，优先使用虚拟时钟；否则回退墙钟。"""
    try:
        ts = time_at(state=state)
        if ts > 0:
            return ts
    except Exception:
        pass
    return time_at("wall")


class DataUpdater:
    """行情数据更新器。

    属性（≤ 5）:
      - state: PoolState 运行时表真相源
      - bus: EventBus（可选）
      - data_source: 当前绑定的数据源配置行
      - _fundamentals: 基本面字段缓存（写入 state.fundamentals 前）
      - _watermark: 每只股票最新 _ts 水位线（冗余缓存，避免遍历 state.latest_tick）

    方法（≤ 6）:
      - __init__
      - bind
      - apply_data
      - _apply_code_tick
      - _publish_tick_changed
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

        # I26：顶层 _hash 统一委托 PoolState._hash_tick_data（与 update_latest_tick 同算法），
        # 消除"全量替换 vs 增量更新"双 hash 不一致 bug。
        self.state.latest_tick["_hash"] = self._hash_aggregate()
        self.state.latest_tick["_ts"] = _now_from_state(self.state)

        # 无论冷启动还是推进，都发布 DataChanged(tick) 供 BarComposer 等订阅者同步更新；
        # 只有真正推进时才置 dirty.data。
        self._publish_tick_changed(list(updated.keys()))
        if advanced_codes:
            self.state.mark_data_dirty()
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
            self.state.latest_tick[code] = tick
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
        self.state.latest_tick[code] = tick
        self._watermark[code] = new_ts
        advanced = new_ts > old_ts
        return True, advanced

    def _publish_tick_changed(self, codes: List[str]) -> None:
        if not is_event_bus(self.bus):
            return
        top_hash = self.state.bar_hash()
        event = DataChanged(
            ts=_now_from_state(self.state),
            bar_hash=top_hash,
            codes=list(codes),
            source="tick",
            data=None,
        )
        # I22：删除 try/except + logger.debug——EventBus.publish 内部已隔离订阅者异常，
        # 外层吞掉仅掩盖总线自身 bug（如 _events.append 失败）。
        self.bus.publish(event)

    def _hash_aggregate(self) -> str:
        """对所有 per-code tick（不含顶层 _hash/_ts）做聚合摘要。

        I26：委托 ``PoolState._hash_tick_data``，与 ``update_latest_tick`` 路径
        使用同一算法。原算法（``md5("{code}:{tick._hash}" join \\x00)``）已被
        ``PoolState._hash_tick_data`` 接管，本方法保留为命名访问器（语义清晰）。
        """
        return type(self.state)._hash_tick_data(self.state.latest_tick)


__all__ = ["DataUpdater"]
