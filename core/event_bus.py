"""事件总线：收敛 `_event_queue` / `_signal_queue` 为统一队列（I55/I61 落地）。

按 ``execute-architecture-migration`` 规格 Task 7 实现。
事件类型收敛为 ``DataChanged`` / ``Executed`` / ``DomainEvent`` / ``Signal`` 四类，
由 ``EventBus`` 统一订阅、发布与查询。

`_alert_queue` 经 I62 评估为单路径无分裂（builtins → queue → app，无 EventBus
双路径、无订阅者缺失），不纳入收敛——强行统一为纯间接层，无实际收益。
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
    ENTER/EXIT 由 ``MetaEngine._push_event`` → ``EventBus.publish`` 发布
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


EVENT_DATA_CHANGED = "DataChanged"
EVENT_EXECUTED = "Executed"
EVENT_DOMAIN = "DomainEvent"
EVENT_SIGNAL = "Signal"

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

    def __init__(self) -> None:
        self._subscribers: Dict[str, List[Callable[[_Event], None]]] = {}
        self._events: List[_Event] = []

    def subscribe(self, event_type: str, handler: Callable[[_Event], None]) -> None:
        """订阅指定事件类型。"""
        self._subscribers.setdefault(event_type, []).append(handler)

    def publish(self, event: _Event) -> None:
        """同步发布事件：写入日志并通知订阅者。

        I22：订阅者异常从静默 ``except: pass`` 改为 ``logger.warning``，
        使中断驱动维度的失败可被诊断（旧实现完全吞掉异常，bug 难以定位）。
        异常仍不向上抛，保证总线与其他订阅者不受影响。
        """
        self._events.append(event)
        event_type = type(event).__name__
        handlers = list(self._subscribers.get(event_type, []))
        for handler in handlers:
            try:
                handler(event)
            except Exception as ex:
                logger.warning(
                    "EventBus 订阅者异常 (event_type=%s handler=%s): %s",
                    event_type, getattr(handler, "__name__", repr(handler)), ex,
                )

    def get_events(self, event_type: Optional[str] = None) -> List[_Event]:
        """读取已发布事件；``event_type`` 为类型名字符串，None 返回全部。"""
        events = list(self._events)
        if event_type is None:
            return events
        return [ev for ev in events if type(ev).__name__ == event_type]

    def clear(self) -> None:
        """清空事件日志。"""
        self._events.clear()


def is_event_bus(bus: Any) -> bool:
    """判断对象是否为 ``EventBus`` 实例（供 EdgeExecutor 做向后兼容分支）。"""
    return isinstance(bus, EventBus)


__all__ = [
    "DataChanged",
    "DomainEvent",
    "EVENT_DATA_CHANGED",
    "EVENT_DOMAIN",
    "EVENT_EXECUTED",
    "EVENT_SIGNAL",
    "EventBus",
    "Executed",
    "Signal",
    "is_event_bus",
]
