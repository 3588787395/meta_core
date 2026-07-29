"""合测试 2：10 类事件链顺序严格性验证。

按 ``create-metatest-comprehensive-validation`` spec Task 22.4 实现：
确保 10 类事件按 spec 顺序发布，EdgeFired 先于 FormulaEvaluated。
本测试集独立验证顺序约束，不依赖完整仿真流程。
"""
from __future__ import annotations

from typing import Any, List

import pytest

from core.event_bus import (
    BarComposed,
    DataChanged,
    EdgeFired,
    EventBus,
    FormulaEvaluated,
    OrderFilled,
    OrderPlaced,
    Signal,
    StockFiltered,
    TickReceived,
    TransferExecuted,
)


# spec 定义的事件链顺序
_CHAIN: List[str] = [
    "TickReceived",
    "DataChanged",
    "BarComposed",
    "EdgeFired",
    "FormulaEvaluated",
    "StockFiltered",
    "TransferExecuted",
    "Signal",
    "OrderPlaced",
    "OrderFilled",
]


def _make_events() -> List[Any]:
    """构造按 spec 顺序排列的 10 类事件（ts 单调递增）。"""
    base = 34500.0
    return [
        TickReceived(tick_data={}, code="fz000001", ts=base),
        DataChanged(ts=base + 1, bar_hash="h1", codes=["fz000001"], source="tick"),
        BarComposed(bar={}, period="1min", code="fz000001", ts=base + 2),
        EdgeFired(eid="e1", ts=base + 3),
        FormulaEvaluated(formula_ref="kdj", result=0.5, code="fz000001", bar_hash="h1"),
        StockFiltered(eid="e1", passed=["fz000001"], rejected=[], ts=base + 5),
        TransferExecuted(src="src", tgt="tgt", codes=["fz000001"], mode="copy", ts=base + 6),
        Signal(signal_type="BUY", code="fz000001", pool_id="tgt", price=10.0, ts=base + 7),
        OrderPlaced(order={}, ts=base + 8),
        OrderFilled(fill={}, ts=base + 9),
    ]


# ---------------------------------------------------------------------------
# 严格顺序断言
# ---------------------------------------------------------------------------


def test_full_chain_order(event_collector):
    """完整事件链顺序必须与 spec 定义一致（按发布顺序）。"""
    bus = EventBus()
    collector = event_collector(bus)
    try:
        for ev in _make_events():
            bus.publish(ev)
        # 用原始发布顺序（_events），因部分事件类无 ts 字段
        seq = [type(ev).__name__ for ev in collector._events]
        assert seq == _CHAIN, f"chain order mismatch: {seq}"
    finally:
        collector.disconnect()


def test_edge_fired_precedes_formula_evaluated(event_collector):
    """spec 硬约束：EdgeFired 必须先于 FormulaEvaluated（按发布顺序）。"""
    bus = EventBus()
    collector = event_collector(bus)
    try:
        for ev in _make_events():
            bus.publish(ev)
        events = collector._events
        edge_idx = next(
            (i for i, e in enumerate(events) if type(e).__name__ == "EdgeFired"), -1
        )
        formula_idx = next(
            (i for i, e in enumerate(events) if type(e).__name__ == "FormulaEvaluated"), -1
        )
        assert edge_idx >= 0, "EdgeFired 未发布"
        assert formula_idx >= 0, "FormulaEvaluated 未发布"
        assert edge_idx < formula_idx, (
            f"EdgeFired(idx={edge_idx}) 必须先于 FormulaEvaluated(idx={formula_idx})"
        )
    finally:
        collector.disconnect()


def test_tick_received_is_first(event_collector):
    """TickReceived 必须是事件链第一个事件（按发布顺序）。"""
    bus = EventBus()
    collector = event_collector(bus)
    try:
        for ev in _make_events():
            bus.publish(ev)
        events = collector._events
        assert events, "事件列表为空"
        assert type(events[0]).__name__ == "TickReceived", (
            f"事件链第一个必须是 TickReceived，实际为 {type(events[0]).__name__}"
        )
    finally:
        collector.disconnect()


def test_order_filled_is_last(event_collector):
    """OrderFilled 必须是事件链最后一个事件（按发布顺序）。"""
    bus = EventBus()
    collector = event_collector(bus)
    try:
        for ev in _make_events():
            bus.publish(ev)
        events = collector._events
        assert events, "事件列表为空"
        assert type(events[-1]).__name__ == "OrderFilled", (
            f"事件链最后一个必须是 OrderFilled，实际为 {type(events[-1]).__name__}"
        )
    finally:
        collector.disconnect()


# ---------------------------------------------------------------------------
# 时间戳排序与事件顺序一致性
# ---------------------------------------------------------------------------


def test_event_order_matches_timestamp_order(event_collector):
    """事件发布顺序应与时间戳排序一致（仅对含 ts 的事件验证）。"""
    bus = EventBus()
    collector = event_collector(bus)
    try:
        for ev in _make_events():
            bus.publish(ev)
        events = collector._events
        # 仅对有 ts 字段的事件验证单调性
        ts_list = [getattr(e, "ts", None) for e in events]
        ts_values = [t for t in ts_list if isinstance(t, (int, float)) and t > 0]
        sorted_ts = sorted(ts_values)
        assert ts_values == sorted_ts, (
            f"事件 ts 未按序：实际 {ts_values}，排序后 {sorted_ts}"
        )
    finally:
        collector.disconnect()


def test_data_changed_follows_tick_received(event_collector):
    """DataChanged 必须紧跟 TickReceived 之后（按发布顺序）。"""
    bus = EventBus()
    collector = event_collector(bus)
    try:
        for ev in _make_events():
            bus.publish(ev)
        events = collector._events
        tick_idx = next(
            (i for i, e in enumerate(events) if type(e).__name__ == "TickReceived"), -1
        )
        dc_idx = next(
            (i for i, e in enumerate(events) if type(e).__name__ == "DataChanged"), -1
        )
        assert tick_idx >= 0 and dc_idx >= 0
        assert dc_idx == tick_idx + 1, (
            f"DataChanged(idx={dc_idx}) 必须紧跟 TickReceived(idx={tick_idx})"
        )
    finally:
        collector.disconnect()


def test_bar_composed_follows_data_changed(event_collector):
    """BarComposed 必须在 DataChanged 之后（按发布顺序）。"""
    bus = EventBus()
    collector = event_collector(bus)
    try:
        for ev in _make_events():
            bus.publish(ev)
        events = collector._events
        dc_idx = next(
            (i for i, e in enumerate(events) if type(e).__name__ == "DataChanged"), -1
        )
        bar_idx = next(
            (i for i, e in enumerate(events) if type(e).__name__ == "BarComposed"), -1
        )
        assert dc_idx >= 0 and bar_idx >= 0
        assert bar_idx > dc_idx, (
            f"BarComposed(idx={bar_idx}) 必须在 DataChanged(idx={dc_idx}) 之后"
        )
    finally:
        collector.disconnect()


def test_signal_precedes_order_placed(event_collector):
    """Signal 必须先于 OrderPlaced（按发布顺序）。"""
    bus = EventBus()
    collector = event_collector(bus)
    try:
        for ev in _make_events():
            bus.publish(ev)
        events = collector._events
        sig_idx = next(
            (i for i, e in enumerate(events) if type(e).__name__ == "Signal"), -1
        )
        op_idx = next(
            (i for i, e in enumerate(events) if type(e).__name__ == "OrderPlaced"), -1
        )
        assert sig_idx >= 0 and op_idx >= 0
        assert sig_idx < op_idx, (
            f"Signal(idx={sig_idx}) 必须先于 OrderPlaced(idx={op_idx})"
        )
    finally:
        collector.disconnect()


def test_order_placed_precedes_order_filled(event_collector):
    """OrderPlaced 必须先于 OrderFilled（按发布顺序）。"""
    bus = EventBus()
    collector = event_collector(bus)
    try:
        for ev in _make_events():
            bus.publish(ev)
        events = collector._events
        op_idx = next(
            (i for i, e in enumerate(events) if type(e).__name__ == "OrderPlaced"), -1
        )
        of_idx = next(
            (i for i, e in enumerate(events) if type(e).__name__ == "OrderFilled"), -1
        )
        assert op_idx >= 0 and of_idx >= 0
        assert op_idx < of_idx, (
            f"OrderPlaced(idx={op_idx}) 必须先于 OrderFilled(idx={of_idx})"
        )
    finally:
        collector.disconnect()
