"""事件总线：收敛 `_event_queue` / `_signal_queue` 为统一队列（I55/I61 落地）。

按 ``execute-architecture-migration`` 规格 Task 7 实现。
事件类型收敛为 ``DataChanged`` / ``Executed`` / ``DomainEvent`` / ``Signal`` 四类，
由 ``EventBus`` 统一订阅、发布与查询。

`_alert_queue` 经 I62 评估为单路径无分裂（builtins → queue → app，无 EventBus
双路径、无订阅者缺失），不纳入收敛——强行统一为纯间接层，无实际收益。

扩展：按 ``unify-stockpool-oop-event-driven`` spec 事件契约表扩展事件类型，
原 4 种事件类（``DataChanged`` / ``Executed`` / ``DomainEvent`` / ``Signal``）
字段保持不变；新增覆盖配置/池/行情/公式/过滤/边/转移/订单/持仓/统计/排名/
告警/快照/日志/模式/时间/回放/模拟/导入导出等全链路领域事件。``EventBus``
接口向后兼容：``subscribe`` / ``get_events`` 同时支持事件类与类型名字符串。
"""
from __future__ import annotations

import logging
from dataclasses import field
from typing import Any, Callable, Dict, List, Optional

from pydantic.dataclasses import dataclass


logger = logging.getLogger(__name__)


@dataclass
class DataChanged:
    """行情/K 线数据变更事件。

    兼容 Task 7 的 ``ts / bar_hash / codes`` 字段；Task 8 扩展
    ``source / period / data`` 以区分 tick 与 bar 事件。
    """

    ts: float
    bar_hash: str
    codes: List[str]
    source: Optional[str] = None  # "tick" | "bar"
    period: Optional[str] = None  # 仅 bar 事件携带，如 "1d"
    data: Optional[Any] = None    # 仅 bar 事件携带单只 bar 快照


@dataclass
class Executed:
    """边执行完成事件。

    I23：``details`` 携带原 ``DomainEvent(ENTER)`` 的独有信息（actions/prices/timestamp），
    使一条边执行后只发 1 个 Executed（取代旧的 1 Executed + N DomainEvent(ENTER)）。
    I69：``target_cleared`` 携带被覆盖出目标池的代码（仅 overwrite 模式非空），
    使 SnapshotBuilder view 与 node_stocks 真相源同步——旧实现 view 只 ADD 不
    DISCARD，overwrite 模式下 view 累积陈旧代码（node_stocks 已 REPLACE）。
    """

    eid: str
    sid: str
    tid: str
    entered: List[str]
    exited: List[str]
    target_cleared: List[str] = field(default_factory=list)
    mode: str = "copy"
    details: Optional[Dict[str, Any]] = None


@dataclass
class DomainEvent:
    """领域事件：ENTER / EXIT / TIMEOUT / RANK_CHANGED。

    I61：ALL DomainEvent 统一经 EventBus 发布（与 I55 Signal 同构）。
    ENTER/EXIT 由 ``PoolEngine._push_event`` → ``EventBus.publish`` 发布
    （``event_rules.json`` 表驱动）；RANK_CHANGED 由 ``builtins._ra_score_weighted_sort``
    经 ``push_event`` → ``EventBus.publish`` 发布；TIMEOUT 由 ``edge_executor._run_ttl``
    经 ``_publish`` → ``EventBus.publish`` 发布（I70：TTL 事件唯一由边执行层直发，
    表驱动层 ttl_expire 域仅发 SELL 信号，消除 EXIT+TIMEOUT 双发）。``_on_domain_event``
    订阅者桥接至 ``_event_queue``，``_event_queue`` 纯派生视图，消费者零改动。
    """

    event_type: str  # ENTER / EXIT / TIMEOUT / RANK_CHANGED
    code: str
    pool_id: str
    details: Dict[str, Any]


@dataclass
class Signal:
    """交易信号事件。

    I34：扩展 condition / profit_pct / hold_days 字段（默认值保证向后兼容），
    使 EventBus Signal 与 _push_signal 的 sig dict 字段对齐，为 BUY 信号
    经 EventBus → _signal_queue 订阅收敛提供完整字段载体。
    """

    signal_type: str  # BUY / SELL
    code: str
    pool_id: str
    price: float
    ts: float
    quantity: int = 0
    condition: str = ""
    profit_pct: float = 0.0
    hold_days: int = 0


# === 新增事件类（unify-stockpool-oop-event-driven spec 事件契约表） ===


@dataclass
class ConfigLoaded:
    """配置加载完成事件。"""

    config_tables: Dict[str, Any]


@dataclass
class ConfigChanged:
    """配置变更事件。"""

    changed_tables: List[str]


@dataclass
class PoolLoaded:
    """股票池加载完成事件。"""

    pool_config: Dict[str, Any]
    source_format: str = "json"  # dzh / tdx / json


@dataclass
class TickReceived:
    """Tick 接收事件。"""

    tick_data: Dict[str, Any]
    code: str = ""
    ts: float = 0.0


@dataclass
class BarComposed:
    """Bar 合成完成事件。"""

    bar: Dict[str, Any]
    period: str
    code: str
    ts: float = 0.0


@dataclass
class FormulaEvaluated:
    """公式求值完成事件。"""

    formula_ref: str
    result: Any
    code: str = ""
    bar_hash: str = ""


@dataclass
class CrossOverDetected:
    """交叉穿越检测事件。"""

    code: str
    cross_type: str  # golden / death
    formula_ref: str = ""
    ts: float = 0.0


@dataclass
class StockFiltered:
    """股票过滤事件。"""

    eid: str
    passed: List[str]
    rejected: List[str]
    filter_ref: str = ""


@dataclass
class EdgeFired:
    """边触发事件。

    一条边到期触发时发布一个事件，携带本次有数据变化的股票代码集合。
    changed_codes: 本周期内有Tick/Bar更新的股票（用于增量筛选）。
    筛选器据此仅对变化股票重新评估公式，未变化股票使用上次缓存结果。
    """

    eid: str
    ts: float
    changed_codes: List[str] = field(default_factory=list)


@dataclass
class TransferExecuted:
    """转移执行完成事件。"""

    src: str
    tgt: str
    codes: List[str]
    mode: str = "copy"
    ts: float = 0.0


@dataclass
class TTLExpired:
    """TTL 过期事件。"""

    node_id: str
    codes: List[str]
    ts: float = 0.0


@dataclass
class OrderPlaced:
    """下单事件。"""

    order: Dict[str, Any]  # 含 code/side/qty/price/order_type
    ts: float = 0.0


@dataclass
class OrderFilled:
    """成交事件。"""

    fill: Dict[str, Any]  # 含 code/side/qty/price/order_id/fill_ts
    ts: float = 0.0


@dataclass
class PositionUpdated:
    """持仓更新事件。"""

    tracker: Dict[str, Any]  # 含 node_id/code/entry_price/cur_price/qty/pnl
    ts: float = 0.0


@dataclass
class StatisticsUpdated:
    """统计更新事件。"""

    stats: Dict[str, Any]
    ts: float = 0.0


@dataclass
class RankingChanged:
    """排名变更事件。"""

    rankings: Dict[str, Any]
    dimension: str = ""
    ts: float = 0.0


@dataclass
class AlertRaised:
    """告警触发事件。"""

    alert: Dict[str, Any]  # 含 rule_id/code/severity/message
    ts: float = 0.0


@dataclass
class SnapshotUpdated:
    """快照更新事件。"""

    snapshot: Dict[str, Any]
    ts: float = 0.0


@dataclass
class EventLogged:
    """事件落日志事件。"""

    event: Dict[str, Any]
    event_kind: str = ""
    ts: float = 0.0


@dataclass
class ModeChanged:
    """模式切换事件。"""

    mode_id: str  # live / replay / simulation
    prev_mode: str = ""


@dataclass
class TimeAdvanced:
    """时间推进事件。"""

    ts: float
    source: str = "wall_clock"


@dataclass
class ReplayStarted:
    """回放开始事件。"""

    session: Dict[str, Any]  # 含 session_id/start_ts/end_ts/codes


@dataclass
class ReplayStep:
    """回放单步事件。"""

    step: Dict[str, Any]  # 含 step_idx/ts/bar
    session_id: str = ""


@dataclass
class SimulationStep:
    """模拟单步事件。"""

    step: Dict[str, Any]  # 含 step_idx/virtual_ts/interval
    session_id: str = ""


@dataclass
class ImportStarted:
    """导入开始事件。"""

    format: str  # dzh / tdx / json
    path: str


@dataclass
class ExportCompleted:
    """导出完成事件。"""

    format: str
    path: str
    count: int = 0
    ts: float = 0.0


EVENT_DATA_CHANGED = "DataChanged"
EVENT_EXECUTED = "Executed"
EVENT_DOMAIN = "DomainEvent"
EVENT_SIGNAL = "Signal"
EVENT_CONFIG_LOADED = "ConfigLoaded"
EVENT_CONFIG_CHANGED = "ConfigChanged"
EVENT_POOL_LOADED = "PoolLoaded"
EVENT_TICK_RECEIVED = "TickReceived"
EVENT_BAR_COMPOSED = "BarComposed"
EVENT_FORMULA_EVALUATED = "FormulaEvaluated"
EVENT_CROSS_OVER_DETECTED = "CrossOverDetected"
EVENT_STOCK_FILTERED = "StockFiltered"
EVENT_EDGE_FIRED = "EdgeFired"
EVENT_TRANSFER_EXECUTED = "TransferExecuted"
EVENT_TTL_EXPIRED = "TTLExpired"
EVENT_ORDER_PLACED = "OrderPlaced"
EVENT_ORDER_FILLED = "OrderFilled"
EVENT_POSITION_UPDATED = "PositionUpdated"
EVENT_STATISTICS_UPDATED = "StatisticsUpdated"
EVENT_RANKING_CHANGED = "RankingChanged"
EVENT_ALERT_RAISED = "AlertRaised"
EVENT_SNAPSHOT_UPDATED = "SnapshotUpdated"
EVENT_EVENT_LOGGED = "EventLogged"
EVENT_MODE_CHANGED = "ModeChanged"
EVENT_TIME_ADVANCED = "TimeAdvanced"
EVENT_REPLAY_STARTED = "ReplayStarted"
EVENT_REPLAY_STEP = "ReplayStep"
EVENT_SIMULATION_STEP = "SimulationStep"
EVENT_IMPORT_STARTED = "ImportStarted"
EVENT_EXPORT_COMPLETED = "ExportCompleted"

_Event = Any


class EventBus:
    """事件总线。

    属性（实例级，≤ 5）:
      - _subscribers: 按事件类型名索引的 handler 列表
      - _events: 已发布事件日志

    方法（≤ 5）:
      - __init__
      - subscribe
      - publish
      - get_events
      - clear
    """

    def __init__(self, max_events: int = 5000) -> None:
        self._subscribers: Dict[str, List[Callable[[_Event], None]]] = {}
        self._any_subscribers: List[Callable[[_Event], None]] = []
        self._events: List[_Event] = []
        # I96：事件日志上限保护，避免长时间运行导致 _events 无限增长内存爆炸
        self._max_events = max_events
        # I97：绝对事件计数器，不受 max_events 删除旧事件影响
        # step() 用此计数器计算 offset，避免 _events 切片后 offset 失效
        self._total_published = 0
        self._dropped_count = 0

    @property
    def total_published(self) -> int:
        """累计发布事件总数（绝对值，不受 _events 切片影响）。"""
        return self._total_published

    @property
    def dropped_count(self) -> int:
        """因 max_events 限制被删除的旧事件数。"""
        return self._dropped_count

    def get_events_since(self, absolute_offset: int) -> List[_Event]:
        """获取从绝对偏移量开始的所有事件。

        Args:
            absolute_offset: 绝对事件偏移量（基于 total_published）

        Returns:
            从该偏移量开始的事件列表。如果 offset 在已删除范围内，
            返回当前所有事件；如果 offset 超出总数，返回空列表。
        """
        if absolute_offset >= self._total_published:
            return []
        # 计算相对 _events 列表的实际偏移
        list_offset = absolute_offset - self._dropped_count
        if list_offset < 0:
            # offset 在已删除范围内，返回所有当前事件
            return list(self._events)
        return list(self._events[list_offset:])

    @staticmethod
    def _event_type_name(event_type: Any) -> str:
        """获取事件类型名：兼容事件类与类型名字符串两种入参。"""
        if isinstance(event_type, str):
            return event_type
        return getattr(event_type, "__name__", str(event_type))

    def subscribe(self, event_type: Any, handler: Callable[[_Event], None]) -> None:
        """订阅指定事件类型（支持事件类或类型名字符串）。"""
        name = self._event_type_name(event_type)
        self._subscribers.setdefault(name, []).append(handler)

    def subscribe_any(self, handler: Callable[[_Event], None]) -> Callable[[], None]:
        """订阅所有事件类型，返回取消订阅函数。"""
        self._any_subscribers.append(handler)
        def unsubscribe():
            try:
                self._any_subscribers.remove(handler)
            except ValueError:
                pass
        return unsubscribe

    def publish(self, event: _Event) -> None:
        """同步发布事件：写入日志并通知订阅者。

        I22：订阅者异常从静默 ``except: pass`` 改为 ``logger.warning``，
        使中断驱动维度的失败可被诊断（旧实现完全吞掉异常，bug 难以定位）。
        异常仍不向上抛，保证总线与其他订阅者不受影响。
        """
        self._events.append(event)
        self._total_published += 1
        # I96：上限保护，超出时删除最旧事件
        if len(self._events) > self._max_events:
            drop_n = len(self._events) - self._max_events
            del self._events[:drop_n]
            self._dropped_count += drop_n
        event_type = type(event).__name__
        handlers = list(self._subscribers.get(event_type, []))
        any_handlers = list(self._any_subscribers)
        all_handlers = handlers + any_handlers
        for handler in all_handlers:
            try:
                handler(event)
            except Exception as ex:
                logger.warning(
                    "EventBus 订阅者异常 (event_type=%s handler=%s): %s",
                    event_type, getattr(handler, "__name__", repr(handler)), ex,
                )

    def get_events(self, event_type: Any = None) -> List[_Event]:
        """读取已发布事件；``event_type`` 可为事件类/类型名字符串，None 返回全部。"""
        events = list(self._events)
        if event_type is None:
            return events
        name = self._event_type_name(event_type)
        return [ev for ev in events if type(ev).__name__ == name]

    def clear(self) -> None:
        """清空事件日志。"""
        self._events.clear()


def is_event_bus(bus: Any) -> bool:
    """判断对象是否为 ``EventBus`` 实例（供 EdgeExecutor 做向后兼容分支）。"""
    return isinstance(bus, EventBus)


__all__ = [
    "AlertRaised",
    "BarComposed",
    "ConfigChanged",
    "ConfigLoaded",
    "CrossOverDetected",
    "DataChanged",
    "DomainEvent",
    "EVENT_ALERT_RAISED",
    "EVENT_BAR_COMPOSED",
    "EVENT_CONFIG_CHANGED",
    "EVENT_CONFIG_LOADED",
    "EVENT_CROSS_OVER_DETECTED",
    "EVENT_DATA_CHANGED",
    "EVENT_DOMAIN",
    "EVENT_EDGE_FIRED",
    "EVENT_EVENT_LOGGED",
    "EVENT_EXECUTED",
    "EVENT_EXPORT_COMPLETED",
    "EVENT_FORMULA_EVALUATED",
    "EVENT_IMPORT_STARTED",
    "EVENT_MODE_CHANGED",
    "EVENT_ORDER_FILLED",
    "EVENT_ORDER_PLACED",
    "EVENT_POOL_LOADED",
    "EVENT_POSITION_UPDATED",
    "EVENT_RANKING_CHANGED",
    "EVENT_REPLAY_STARTED",
    "EVENT_REPLAY_STEP",
    "EVENT_SIGNAL",
    "EVENT_SIMULATION_STEP",
    "EVENT_SNAPSHOT_UPDATED",
    "EVENT_STATISTICS_UPDATED",
    "EVENT_STOCK_FILTERED",
    "EVENT_TICK_RECEIVED",
    "EVENT_TIME_ADVANCED",
    "EVENT_TRANSFER_EXECUTED",
    "EVENT_TTL_EXPIRED",
    "EdgeFired",
    "EventBus",
    "EventLogged",
    "Executed",
    "ExportCompleted",
    "FormulaEvaluated",
    "ImportStarted",
    "ModeChanged",
    "OrderFilled",
    "OrderPlaced",
    "PoolLoaded",
    "PositionUpdated",
    "RankingChanged",
    "ReplayStarted",
    "ReplayStep",
    "Signal",
    "SimulationStep",
    "SnapshotUpdated",
    "StatisticsUpdated",
    "StockFiltered",
    "TTLExpired",
    "TickReceived",
    "TimeAdvanced",
    "TransferExecuted",
    "is_event_bus",
]
