"""EventPanel：事件浮窗，订阅 EventBus 实时显示事件。"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from .event_bus import (
    EVENT_DATA_CHANGED,
    EVENT_DOMAIN,
    EVENT_EXECUTED,
    EVENT_SIGNAL,
    DataChanged,
    DomainEvent,
    Executed,
    Signal,
    is_event_bus,
)

logger = logging.getLogger(__name__)

_MAX_EVENTS = 500


class EventPanel:
    """事件浮窗：订阅 EventBus，收集事件记录供 UI 渲染。

    属性（≤ 5）:
      - bus: EventBus
      - _events: 事件记录列表（maxlen=500）
      - _pending_specs: 待触发 TimedEventSpec 引用
      - _enabled: 是否已订阅
    """

    def __init__(self, bus: Optional[Any] = None, event_driver: Optional[Any] = None) -> None:
        self.bus = bus
        self._event_driver = event_driver
        self._events: List[Dict[str, Any]] = []
        self._enabled = False

    def subscribe(self) -> None:
        if self._enabled or not is_event_bus(self.bus):
            return
        self.bus.subscribe(EVENT_DATA_CHANGED, self._on_data_changed)
        self.bus.subscribe(EVENT_DOMAIN, self._on_domain_event)
        self.bus.subscribe(EVENT_EXECUTED, self._on_executed)
        self.bus.subscribe(EVENT_SIGNAL, self._on_signal)
        self._enabled = True

    def _append(self, record: Dict[str, Any]) -> None:
        self._events.append(record)
        if len(self._events) > _MAX_EVENTS:
            del self._events[0]

    def _on_data_changed(self, event: DataChanged) -> None:
        self._append({
            "ts": event.ts if hasattr(event, 'ts') else 0,
            "event_type": "DataChanged",
            "details": {
                "source": event.source,
                "codes": list(event.codes) if event.codes else [],
                "period": event.period or "",
            },
        })

    def _on_domain_event(self, event: DomainEvent) -> None:
        self._append({
            "ts": event.ts if hasattr(event, 'ts') else 0,
            "event_type": "DomainEvent",
            "details": {
                "event_type": event.event_type,
                "pool_id": event.pool_id,
                "code": event.code,
            },
        })

    def _on_executed(self, event: Executed) -> None:
        d = event.details if isinstance(event.details, dict) else {}
        self._append({
            "ts": 0,
            "event_type": "Executed",
            "details": {
                "edge_id": event.eid,
                "sid": event.sid,
                "tid": event.tid,
                "entered": list(event.entered) if event.entered else [],
                "exited": list(event.exited) if event.exited else [],
                "actions": d.get("actions", []),
            },
        })

    def _on_signal(self, event: Signal) -> None:
        self._append({
            "ts": event.ts,
            "event_type": "Signal",
            "details": {
                "signal_type": event.signal_type,
                "code": event.code,
                "pool_id": event.pool_id,
                "price": event.price,
                "quantity": event.quantity,
            },
        })

    def get_events(self) -> List[Dict[str, Any]]:
        return list(self._events)

    def get_pending_specs(self) -> List[Dict[str, Any]]:
        if self._event_driver is None:
            return []
        specs = []
        for spec in getattr(self._event_driver, '_specs', []):
            specs.append({
                "edge_id": getattr(spec, 'edge_id', ''),
                "next_fire_time": getattr(spec, 'next_fire_time', 0),
            })
        return specs

    def clear(self) -> None:
        self._events.clear()


__all__ = ["EventPanel"]
