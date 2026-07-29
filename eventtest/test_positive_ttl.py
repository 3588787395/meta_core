# -*- coding: utf-8 -*-
"""正测试 — TTL 一次性触发卖出事件链（Task 5 SubTask 5.2）。

验证 TTL 到期触发的卖出事件链：
    TTLDue → Signal(SELL, sell_all) → OrderPlaced → OrderFilled → PositionUpdated(持仓=0)

以及 TTL 一次性触发不注册下次（heap 长度不变）。

G2 架构说明：
    EventDriver 的 TTL action 只发布 TTLDue(node_id, code, ts)，TradeModule 订阅
    TTLDue 后自行发布 SELL Signal 并完成卖出事件链。不再经过 DomainEvent(TIMEOUT)
    或 TTLExpired 桥接。

复用 core/ 现有类（PoolEngine / TradeModule / EventBus / EventDriver /
TimedEventSpec / register_ttl_spec），不使用已删除旧接口（at_fn / fire_ttl_due /
TtlTracker / get_node_stocks / SimTickSource / execution_order /
EdgeFired.changed_codes / DomainEvent TIMEOUT / TTLExpired）。
"""
from __future__ import annotations

from typing import Any, List, Optional

import pytest

from core.event_bus import (
    DataChanged,
    EventBus,
    OrderFilled,
    OrderPlaced,
    PositionUpdated,
    Signal,
    TTLDue,
    TransferExecuted,
)
from core.execution_module import (
    EventDriver,
    TimedEventSpec,
    register_ttl_spec,
)


# ═══════════════════════════════════════════════════════════════
# 测试常量（测试参数，非硬编码实例内容）
# ═══════════════════════════════════════════════════════════════

_TS = 34500.0              # 虚拟时钟起点（09:30:00 当日秒数偏移）
_TTL_SEC = 1200            # TTL 间隔秒数（= pool_C hold_seconds=1200）
_TTL_FIRE_TS = _TS + _TTL_SEC  # TTL 到期时刻 = 35700.0
_TICK_PRICE = 10.50        # 模拟 tick 价格（>0，满足 PaperTradeEngine 成交条件）
_POOL_C = "pool_C"         # 目标池 ID
_TTL_EID = "test_ttl_edge"  # 测试用 TTL 边/流程 ID


# ═══════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════


def _get_trade_module(bus: EventBus) -> Optional[Any]:
    """从 EventBus 订阅者中提取 TradeModule 实例。

    与 test_positive_trade_chain.py 同一模式：遍历 ``bus._subscribers``
    的 bound method ``__self__`` 找到 TradeModule。
    """
    from core.trade_module import TradeModule
    for handlers in bus._subscribers.values():
        for h in handlers:
            owner = getattr(h, "__self__", None)
            if isinstance(owner, TradeModule):
                return owner
    return None


def _set_virtual_time(engine: Any, ts: float) -> None:
    """设置 engine.state.time_source 的 current_ts 为指定时刻。

    ``time_at(state=state)`` 读取 ``state.time_source["current_ts"]``，
    ``EventDriver.fire_due(now)`` 与 TTL action 均通过 ``time_at`` 获取当前时刻。
    """
    engine.state.time_source["current_ts"] = float(ts)
    engine.state.time_source.setdefault("start_ts", _TS)
    engine.state.time_source.setdefault("driver_type", "virtual")


def _setup_latest_price(bus: EventBus, code: str, price: float = _TICK_PRICE) -> None:
    """发布 DataChanged(tick) 预填 TradeModule._latest_prices 缓存。

    BUY/SELL 链均需 price > 0（PaperTradeEngine 成交条件）。
    """
    bus.publish(DataChanged(
        ts=_TS,
        bar_hash="",
        codes=[code],
        source="tick",
        data={code: {"close": price, "code": code}},
    ))


def _establish_position(bus: EventBus, code: str) -> None:
    """建立持仓：发布 TransferExecuted 触发买入链，使持仓 = 100。

    前置条件：先发布 DataChanged(tick) 设置最新价。
    后置条件：TradeModule._trackers[(pool_C, code)]["qty"] == 100。
    """
    _setup_latest_price(bus, code)
    bus.publish(TransferExecuted(
        src="cond3", tgt=_POOL_C, codes=[code], mode="copy", ts=_TS,
    ))


def _add_stock_to_pool(engine: Any, code: str) -> None:
    """将股票添加到 pool_C（TTL action 检查股票在池内才触发）。

    ``_make_ttl_interval_action`` 内部 action 检查 ``state.get_pool(tgt).get_stocks()``
    是否包含该 code，不在池内则跳过。测试需先入池再注册 TTL。
    """
    engine.state.get_pool(_POOL_C).add_stocks([{"code": code}])


def _extract_event_types(events: List[Any]) -> List[str]:
    """从事件列表提取类型名序列（用于断言事件链顺序）。"""
    return [type(e).__name__ for e in events]


# ═══════════════════════════════════════════════════════════════
# 测试用例
# ═══════════════════════════════════════════════════════════════


class TestTTLRegistration:
    """验证 TTL 注册到 EventDriver heapq。"""

    def test_register_ttl_spec_uses_one_shot_interval(self):
        """register_ttl_spec 创建的 TimedEventSpec.interval 为 None（一次性）。

        断言：
          - spec.interval is None
          - spec.params["check_type"] == "interval"
          - spec.params["code"] == 注册的 code
        """
        driver = EventDriver(state=None, bus=None)
        # 构造最小 state mock（register_ttl_spec 只传给 _make_ttl_interval_action）
        mock_state = type("MockState", (), {"get_pool": lambda self, nid: type("MockPool", (), {"get_stocks": lambda self: []})()})()
        register_ttl_spec(
            event_driver=driver,
            state=mock_state,
            tgt=_POOL_C,
            eid=_TTL_EID,
            code="fz600001",
            ttl_sec=_TTL_SEC,
            entry_ts=_TS,
            bus=None,
        )
        assert len(driver._heap) == 1
        _fire_time, _seq, spec = driver._heap[0]
        assert spec.interval is None, f"interval={spec.interval}，期望 None（一次性）"
        assert spec.params["check_type"] == "interval"
        assert spec.params["code"] == "fz600001"
        assert spec.params["tgt"] == _POOL_C

    def test_ttl_registration_adds_spec_to_heap(
        self, pool_engine, fz_stocks,
    ):
        """register_ttl_spec 向 EventDriver heapq 添加 1 条 spec。

        断言：
          - 注册前 heap 为空（使用独立 EventDriver 避免边定时器干扰）
          - 注册后 heap 长度 = 1
          - first_fire_time == entry_ts + ttl_sec
        """
        code = fz_stocks(1)[0]
        engine = pool_engine()
        bus: EventBus = engine._components["event_bus"]
        _set_virtual_time(engine, _TS)

        # 使用独立 EventDriver 隔离边定时器
        driver = EventDriver(state=engine.state, bus=bus)
        assert len(driver._heap) == 0

        register_ttl_spec(
            event_driver=driver,
            state=engine.state,
            tgt=_POOL_C,
            eid=_TTL_EID,
            code=code,
            ttl_sec=_TTL_SEC,
            entry_ts=_TS,
            bus=bus,
        )
        assert len(driver._heap) == 1
        fire_time = driver._heap[0][0]
        assert fire_time == _TS + _TTL_SEC, f"first_fire_time={fire_time}"


class TestTTLFireDue:
    """验证 fire_due 弹出 TTL spec 并发布 TTLDue。"""

    def test_ttl_fire_due_publishes_ttl_due(
        self, pool_engine, event_collector, fz_stocks,
    ):
        """fire_due 弹出 TTL spec，action 发布 TTLDue。

        断言：
          - 恰好发布 1 个 TTLDue 事件
          - TTLDue.node_id == pool_C
          - TTLDue.code == 注册的 code
        """
        code = fz_stocks(1)[0]
        engine = pool_engine()
        bus: EventBus = engine._components["event_bus"]
        collector = event_collector(bus)
        try:
            _set_virtual_time(engine, _TS)
            _add_stock_to_pool(engine, code)

            driver = EventDriver(state=engine.state, bus=bus)
            register_ttl_spec(
                event_driver=driver, state=engine.state, tgt=_POOL_C,
                eid=_TTL_EID, code=code, ttl_sec=_TTL_SEC,
                entry_ts=_TS, bus=bus,
            )

            collector.clear()
            _set_virtual_time(engine, _TTL_FIRE_TS)
            driver.fire_due(_TTL_FIRE_TS)

            ttl_events = collector.filter(type="TTLDue")
            assert len(ttl_events) == 1, (
                f"期望 1 个 TTLDue，实际 {len(ttl_events)}"
            )
            ev = ttl_events[0]
            assert ev.node_id == _POOL_C, f"node_id={ev.node_id}"
            assert ev.code == code, f"code={ev.code}"
        finally:
            collector.disconnect()

    def test_ttl_one_shot_does_not_reregister(
        self, pool_engine, event_collector, fz_stocks,
    ):
        """TTL 一次性触发后不注册下次（heap 长度回到注册前）。

        断言：
          - 注册前 heap 长度 = 0
          - 注册后 heap 长度 = 1
          - fire_due 后 heap 长度 = 0（popped 且不 re-push）
        """
        code = fz_stocks(1)[0]
        engine = pool_engine()
        bus: EventBus = engine._components["event_bus"]
        collector = event_collector(bus)
        try:
            _set_virtual_time(engine, _TS)
            _add_stock_to_pool(engine, code)

            driver = EventDriver(state=engine.state, bus=bus)
            assert len(driver._heap) == 0

            register_ttl_spec(
                event_driver=driver, state=engine.state, tgt=_POOL_C,
                eid=_TTL_EID, code=code, ttl_sec=_TTL_SEC,
                entry_ts=_TS, bus=bus,
            )
            assert len(driver._heap) == 1, "注册后 heap 应有 1 条 spec"

            _set_virtual_time(engine, _TTL_FIRE_TS)
            driver.fire_due(_TTL_FIRE_TS)

            assert len(driver._heap) == 0, (
                f"fire_due 后 heap 长度={len(driver._heap)}，期望 0（一次性不 re-push）"
            )
        finally:
            collector.disconnect()


class TestTTLDueSellChain:
    """验证 TTLDue 触发卖出事件链。"""

    def test_ttl_due_triggers_sell_signal(
        self, pool_engine, event_collector, fz_stocks,
    ):
        """TTLDue → Signal(SELL, sell_all)。

        前置：建立持仓 = 100。
        断言：
          - 恰好发布 1 个 Signal(SELL)
          - Signal.quantity == 100（sell_all = 当前持仓量）
        """
        code = fz_stocks(1)[0]
        engine = pool_engine()
        bus: EventBus = engine._components["event_bus"]
        collector = event_collector(bus)
        try:
            _set_virtual_time(engine, _TS)
            _establish_position(bus, code)

            collector.clear()
            # 直接发布 TTLDue（模拟 EventDriver TTL action 发布）
            bus.publish(TTLDue(
                node_id=_POOL_C, code=code, ts=_TTL_FIRE_TS,
            ))

            signals = collector.filter(type="Signal")
            sell_signals = [s for s in signals if s.signal_type == "SELL"]
            assert len(sell_signals) == 1, (
                f"期望 1 个 SELL Signal，实际 {len(sell_signals)}"
            )
            assert sell_signals[0].quantity == 100, (
                f"SELL quantity={sell_signals[0].quantity}，期望 100"
            )
            assert sell_signals[0].code == code
        finally:
            collector.disconnect()

    def test_sell_chain_full_event_sequence(
        self, pool_engine, event_collector, fz_stocks,
    ):
        """完整卖出事件链：TTLDue → Signal → OrderPlaced → OrderFilled → PositionUpdated。

        前置：建立持仓 = 100。
        断言事件链顺序正确。

        注意：使用 ``bus.get_events()`` 取事件序列（与 test_positive_trade_chain
        同一原因：EventBus 在 publish 中先 append 再调 handlers，bus._events
        保留正确发布顺序；collector.subscribe_any 在 typed handlers 之后执行，
        嵌套 publish 导致 collector 看到的顺序是反向的）。
        """
        code = fz_stocks(1)[0]
        engine = pool_engine()
        bus: EventBus = engine._components["event_bus"]
        collector = event_collector(bus)
        try:
            _set_virtual_time(engine, _TS)
            _establish_position(bus, code)

            collector.clear()
            # 记录 bus 当前事件数，仅断言此后的新事件顺序
            bus_events_before = len(bus.get_events())
            bus.publish(TTLDue(
                node_id=_POOL_C, code=code, ts=_TTL_FIRE_TS,
            ))

            chain_types = {
                "TTLDue", "Signal", "OrderPlaced",
                "OrderFilled", "PositionUpdated",
            }
            chain_events = [
                e for e in bus.get_events()[bus_events_before:]
                if type(e).__name__ in chain_types
            ]
            actual_seq = _extract_event_types(chain_events)
            expected_seq = [
                "TTLDue", "Signal", "OrderPlaced",
                "OrderFilled", "PositionUpdated",
            ]
            assert actual_seq == expected_seq, (
                f"事件链顺序错误：期望 {expected_seq}，实际 {actual_seq}"
            )
        finally:
            collector.disconnect()

    def test_position_updated_qty_is_0_after_sell(
        self, pool_engine, event_collector, fz_stocks,
    ):
        """卖出后 PositionUpdated.tracker["qty"] == 0。

        前置：建立持仓 = 100。
        断言：
          - PositionUpdated 事件存在
          - tracker["qty"] == 0
        """
        code = fz_stocks(1)[0]
        engine = pool_engine()
        bus: EventBus = engine._components["event_bus"]
        collector = event_collector(bus)
        try:
            _set_virtual_time(engine, _TS)
            _establish_position(bus, code)

            collector.clear()
            bus.publish(TTLDue(
                node_id=_POOL_C, code=code, ts=_TTL_FIRE_TS,
            ))

            positions = collector.filter(type="PositionUpdated")
            assert len(positions) == 1, (
                f"期望 1 个 PositionUpdated，实际 {len(positions)}"
            )
            tracker = positions[0].tracker
            assert tracker["qty"] == 0, f"卖出后持仓={tracker['qty']}，期望 0"
        finally:
            collector.disconnect()

    def test_trade_module_position_is_0_after_sell(
        self, pool_engine, event_collector, fz_stocks,
    ):
        """卖出后 TradeModule._trackers[(pool_C, code)]["qty"] == 0。

        前置：建立持仓 = 100。
        断言 TradeModule 内部持仓跟踪表 qty == 0。
        """
        code = fz_stocks(1)[0]
        engine = pool_engine()
        bus: EventBus = engine._components["event_bus"]
        collector = event_collector(bus)
        try:
            _set_virtual_time(engine, _TS)
            _establish_position(bus, code)

            collector.clear()
            bus.publish(TTLDue(
                node_id=_POOL_C, code=code, ts=_TTL_FIRE_TS,
            ))

            trade_module = _get_trade_module(bus)
            assert trade_module is not None, "未找到 TradeModule 实例"
            key = (_POOL_C, code)
            tracker = trade_module._trackers.get(key)
            assert tracker is not None, f"_trackers[{key}] 不存在"
            assert tracker["qty"] == 0, f"持仓={tracker['qty']}，期望 0"
        finally:
            collector.disconnect()


class TestTTLActionDirect:
    """直接验证 TTL action 与 _on_ttl_due handler。"""

    def test_ttl_action_publishes_ttl_due(
        self, pool_engine, event_collector, fz_stocks,
    ):
        """TTL action（_make_ttl_interval_action 返回的闭包）发布 TTLDue。

        通过 fire_due 触发 action，验证 TTLDue 发布。
        若股票不在池中则 action 跳过——此处先入池再触发。
        """
        code = fz_stocks(1)[0]
        engine = pool_engine()
        bus: EventBus = engine._components["event_bus"]
        collector = event_collector(bus)
        try:
            _set_virtual_time(engine, _TS)
            _add_stock_to_pool(engine, code)

            driver = EventDriver(state=engine.state, bus=bus)
            register_ttl_spec(
                event_driver=driver, state=engine.state, tgt=_POOL_C,
                eid=_TTL_EID, code=code, ttl_sec=_TTL_SEC,
                entry_ts=_TS, bus=bus,
            )

            collector.clear()
            _set_virtual_time(engine, _TTL_FIRE_TS)
            driver.fire_due(_TTL_FIRE_TS)

            ttl_events = collector.filter(type="TTLDue")
            assert len(ttl_events) == 1
            assert ttl_events[0].code == code
        finally:
            collector.disconnect()

    def test_on_ttl_due_publishes_sell_signal(
        self, pool_engine, event_collector, fz_stocks,
    ):
        """直接验证 TradeModule._on_ttl_due 发布 SELL Signal。

        前置：建立持仓 = 100。
        不依赖 fire_due，直接调用 handler。
        """
        code = fz_stocks(1)[0]
        engine = pool_engine()
        bus: EventBus = engine._components["event_bus"]
        collector = event_collector(bus)
        try:
            _set_virtual_time(engine, _TS)
            _establish_position(bus, code)

            trade_module = _get_trade_module(bus)
            assert trade_module is not None

            collector.clear()
            trade_module._on_ttl_due(TTLDue(
                node_id=_POOL_C, code=code, ts=_TTL_FIRE_TS,
            ))

            signals = collector.filter(type="Signal")
            sell_signals = [s for s in signals if s.signal_type == "SELL"]
            assert len(sell_signals) == 1
            assert sell_signals[0].quantity == 100
        finally:
            collector.disconnect()
