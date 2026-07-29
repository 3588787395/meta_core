# -*- coding: utf-8 -*-
"""反测试 — TTL 到期但无持仓（Task 8 SubTask 8.1）。

基于 spec.md "反测试（异常与边界验证）" → "TTL 到期无持仓" Scenario（L140-143）：

    WHEN TTL 到期但该股票无持仓（已被人工卖出）
    THEN Signal(sell_all) 发出但 OrderPlaced 失败或为空
    AND  不抛异常

实现要点：
    - 基于 ``config/pools/sim_test_pool_100.json`` 动态读取 fz 前缀股票代码（不硬编码）。
    - 装配 PoolEngine 实例，复用 conftest.pool_engine 工厂模式。
    - 两种"无持仓"场景：
      A. 股票入 pool_C 后被 ``remove_stocks`` 移出池（人工清空）：
         TTL action 检测 ``code_in_pool=False`` → 直接 return，不发布 TTLDue，
         整条事件链静默跳过（优雅降级，无异常）。
      B. 股票仍在 pool_C 但交易持仓 qty=0（未建立持仓或已平仓）：
         TTL action 发布 TTLDue，TradeModule._on_ttl_due 发布 Signal(SELL, quantity=0)
         （spec L142 "Signal(sell_all) 发出"），_on_signal 检测无持仓 → 发布 rejected
         OrderPlaced（quantity=0, status=rejected，不实际下单）
         （spec L143 "OrderPlaced 失败或为空"），优雅降级无异常，持仓仍为 0。

复用 core/ 现有类（PoolEngine / EventDriver / register_ttl_spec / EventBus），
不修改 core/ 源文件，不使用已删除旧接口（get_node_stocks / SimTickSource /
execution_order / EdgeFired.changed_codes / at_fn / fire_ttl_due / TtlTracker /
DomainEvent TIMEOUT / TTLExpired）。
"""
from __future__ import annotations

from typing import Any, List, Optional

import pytest

from core.event_bus import (
    EventBus,
    OrderPlaced,
    Signal,
    TTLDue,
)
from core.execution_module import (
    EventDriver,
    register_ttl_spec,
)


# ═══════════════════════════════════════════════════════════════
# 测试常量（测试参数，非硬编码实例内容）
# ═══════════════════════════════════════════════════════════════

_TS = 34500.0              # 虚拟时钟起点（09:30:00 当日秒数偏移）
_TTL_SEC = 1200            # TTL 间隔秒数（= pool_C hold_seconds=1200）
_TTL_FIRE_TS = _TS + _TTL_SEC  # TTL 到期时刻 = 35700.0
_POOL_C = "pool_C"         # 目标池 ID
_TTL_EID = "test_ttl_neg_edge"  # 测试用 TTL 边/流程 ID


# ═══════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════


def _get_trade_module(bus: EventBus) -> Optional[Any]:
    """从 EventBus 订阅者中提取 TradeModule 实例。

    与 test_positive_ttl.py / test_positive_trade_chain.py 同一模式：
    遍历 ``bus._subscribers`` 的 bound method ``__self__`` 找到 TradeModule。
    """
    from core.trade_module import TradeModule
    for handlers in bus._subscribers.values():
        for h in handlers:
            owner = getattr(h, "__self__", None)
            if isinstance(owner, TradeModule):
                return owner
    return None


def _set_virtual_time(engine: Any, ts: float) -> None:
    """设置 engine.state.time_source 的 current_ts 为指定时刻。"""
    engine.state.time_source["current_ts"] = float(ts)
    engine.state.time_source.setdefault("start_ts", _TS)
    engine.state.time_source.setdefault("driver_type", "virtual")


def _add_stock_to_pool(engine: Any, code: str) -> None:
    """将股票添加到 pool_C（TTL action 检查股票在池内才触发）。"""
    engine.state.get_pool(_POOL_C).add_stocks([{"code": code}])


def _remove_stock_from_pool(engine: Any, code: str) -> None:
    """从 pool_C 移除股票（模拟人工清空持仓 — 股票出池）。"""
    engine.state.get_pool(_POOL_C).remove_stocks([code])


def _register_ttl(
    engine: Any, bus: EventBus, code: str,
) -> EventDriver:
    """注册 per-code TTL 到独立 EventDriver（隔离边定时器干扰）。

    返回注册了 TTL spec 的 EventDriver，测试中调用 ``driver.fire_due`` 触发。
    """
    driver = EventDriver(state=engine.state, bus=bus)
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
    return driver


# ═══════════════════════════════════════════════════════════════
# 测试用例 — 场景 A：股票被移出池（人工清空）
# ═══════════════════════════════════════════════════════════════


class TestTTLNoPositionStockRemoved:
    """验证 TTL 到期时股票已被移出 pool_C 的优雅降级。

    场景：股票入 pool_C 注册 TTL，TTL 到期前 ``remove_stocks`` 将股票移出池。
    TTL action（``_make_ttl_interval_action``）检测 ``code_in_pool=False`` →
    直接 return，不发布 TTLDue，整条事件链静默跳过。
    """

    def test_ttl_stock_removed_no_exception(
        self, pool_engine, event_collector, fz_stocks,
    ):
        """股票移出池后 TTL fire_due 不抛异常。

        断言：
          - fire_due 调用不抛异常（TTL action 检测 code_in_pool=False 后 return）
        """
        code = fz_stocks(1)[0]
        engine = pool_engine()
        bus: EventBus = engine._components["event_bus"]
        collector = event_collector(bus)
        try:
            _set_virtual_time(engine, _TS)
            _add_stock_to_pool(engine, code)
            driver = _register_ttl(engine, bus, code)

            # 人工清空：移出 pool_C
            _remove_stock_from_pool(engine, code)

            # fire_due 不应抛异常
            _set_virtual_time(engine, _TTL_FIRE_TS)
            driver.fire_due(_TTL_FIRE_TS)
        finally:
            collector.disconnect()

    def test_ttl_stock_removed_no_ttl_due(
        self, pool_engine, event_collector, fz_stocks,
    ):
        """股票移出池后 TTL fire_due 不发布 TTLDue。

        断言：
          - TTLDue 事件数 = 0（TTL action 检测 code_in_pool=False 后 return）
        """
        code = fz_stocks(1)[0]
        engine = pool_engine()
        bus: EventBus = engine._components["event_bus"]
        collector = event_collector(bus)
        try:
            _set_virtual_time(engine, _TS)
            _add_stock_to_pool(engine, code)
            driver = _register_ttl(engine, bus, code)

            _remove_stock_from_pool(engine, code)

            collector.clear()
            _set_virtual_time(engine, _TTL_FIRE_TS)
            driver.fire_due(_TTL_FIRE_TS)

            ttl_events = collector.filter(type="TTLDue")
            assert len(ttl_events) == 0, (
                f"股票移出池后不应发布 TTLDue，实际 {len(ttl_events)} 个"
            )
        finally:
            collector.disconnect()

    def test_ttl_stock_removed_no_sell_signal_no_order(
        self, pool_engine, event_collector, fz_stocks,
    ):
        """股票移出池后无 SELL Signal、无 OrderPlaced。

        断言：
          - Signal(SELL) 事件数 = 0
          - OrderPlaced 事件数 = 0
        """
        code = fz_stocks(1)[0]
        engine = pool_engine()
        bus: EventBus = engine._components["event_bus"]
        collector = event_collector(bus)
        try:
            _set_virtual_time(engine, _TS)
            _add_stock_to_pool(engine, code)
            driver = _register_ttl(engine, bus, code)

            _remove_stock_from_pool(engine, code)

            collector.clear()
            _set_virtual_time(engine, _TTL_FIRE_TS)
            driver.fire_due(_TTL_FIRE_TS)

            sell_signals = [
                s for s in collector.filter(type="Signal")
                if s.signal_type == "SELL"
            ]
            assert len(sell_signals) == 0, (
                f"股票移出池后不应发布 SELL Signal，实际 {len(sell_signals)} 个"
            )
            order_events = collector.filter(type="OrderPlaced")
            assert len(order_events) == 0, (
                f"股票移出池后不应发布 OrderPlaced，实际 {len(order_events)} 个"
            )
        finally:
            collector.disconnect()


# ═══════════════════════════════════════════════════════════════
# 测试用例 — 场景 B：股票在池内但持仓 qty=0
# ═══════════════════════════════════════════════════════════════


class TestTTLNoPositionQtyZero:
    """验证 TTL 到期时股票在 pool_C 但交易持仓 qty=0 的优雅降级。

    场景：股票入 pool_C 注册 TTL，但未建立交易持仓（qty=0）。
    TTL action 检测 ``code_in_pool=True`` → 发布 TTLDue →
    TradeModule._on_ttl_due 发布 Signal(SELL, quantity=0)
    （spec L142 "Signal(sell_all) 发出"）→ _on_signal 检测无持仓 →
    发布 rejected OrderPlaced（quantity=0, status=rejected，不实际下单）
    （spec L143 "OrderPlaced 失败或为空"），优雅降级无异常，持仓仍为 0。
    """

    def test_ttl_qty_zero_no_exception(
        self, pool_engine, event_collector, fz_stocks,
    ):
        """股票在池内 qty=0 时 TTL fire_due + _on_ttl_due 不抛异常。

        断言：
          - fire_due 不抛异常
          - TTLDue → TradeModule._on_ttl_due 不抛异常（qty=0 时仍发布 Signal）
          - _on_signal 不抛异常（发布 rejected OrderPlaced）
        """
        code = fz_stocks(1)[0]
        engine = pool_engine()
        bus: EventBus = engine._components["event_bus"]
        collector = event_collector(bus)
        try:
            _set_virtual_time(engine, _TS)
            _add_stock_to_pool(engine, code)
            driver = _register_ttl(engine, bus, code)

            # 不建立持仓（qty=0），直接触发 TTL
            collector.clear()
            _set_virtual_time(engine, _TTL_FIRE_TS)
            driver.fire_due(_TTL_FIRE_TS)
        finally:
            collector.disconnect()

    def test_ttl_qty_zero_publishes_ttl_due(
        self, pool_engine, event_collector, fz_stocks,
    ):
        """股票在池内 qty=0 时 TTL fire_due 仍发布 TTLDue。

        TTL action 仅检测股票是否在池内（不检测交易持仓），
        股票在池内即发布 TTLDue。

        断言：
          - TTLDue 事件数 = 1
          - TTLDue.code == 注册的 code
          - TTLDue.node_id == pool_C
        """
        code = fz_stocks(1)[0]
        engine = pool_engine()
        bus: EventBus = engine._components["event_bus"]
        collector = event_collector(bus)
        try:
            _set_virtual_time(engine, _TS)
            _add_stock_to_pool(engine, code)
            driver = _register_ttl(engine, bus, code)

            collector.clear()
            _set_virtual_time(engine, _TTL_FIRE_TS)
            driver.fire_due(_TTL_FIRE_TS)

            ttl_events = collector.filter(type="TTLDue")
            assert len(ttl_events) == 1, (
                f"股票在池内应发布 1 个 TTLDue，实际 {len(ttl_events)} 个"
            )
            assert ttl_events[0].code == code
            assert ttl_events[0].node_id == _POOL_C
        finally:
            collector.disconnect()

    def test_ttl_qty_zero_publishes_sell_signal(
        self, pool_engine, event_collector, fz_stocks,
    ):
        """股票在池内 qty=0 时 TTLDue 仍发布 SELL Signal（spec L142）。

        spec L142: "Signal(sell_all) 发出" — TradeModule._on_ttl_due
        不再因 qty<=0 跳过，而是发布 Signal(SELL, quantity=0)。

        断言：
          - Signal(SELL) 事件数 >= 1
          - Signal.quantity == 0（无持仓）
        """
        code = fz_stocks(1)[0]
        engine = pool_engine()
        bus: EventBus = engine._components["event_bus"]
        collector = event_collector(bus)
        try:
            _set_virtual_time(engine, _TS)
            _add_stock_to_pool(engine, code)
            driver = _register_ttl(engine, bus, code)

            collector.clear()
            _set_virtual_time(engine, _TTL_FIRE_TS)
            driver.fire_due(_TTL_FIRE_TS)

            sell_signals = [
                s for s in collector.filter(type="Signal")
                if s.signal_type == "SELL"
            ]
            assert len(sell_signals) >= 1, (
                f"qty=0 时应发布 SELL Signal（spec L142），实际 {len(sell_signals)} 个"
            )
            assert sell_signals[0].quantity == 0, (
                f"无持仓时 SELL quantity 应为 0，实际 {sell_signals[0].quantity}"
            )
        finally:
            collector.disconnect()

    def test_ttl_qty_zero_publishes_rejected_order_placed(
        self, pool_engine, event_collector, fz_stocks,
    ):
        """股票在池内 qty=0 时发布 rejected OrderPlaced（spec L143）。

        spec L143: "OrderPlaced 失败或为空" — _on_signal 检测无持仓后
        发布 OrderPlaced(quantity=0, status=rejected)，_on_order_placed
        跳过 rejected 订单不实际成交。

        断言：
          - OrderPlaced 事件数 >= 1
          - OrderPlaced.order["qty"] == 0 或 order["status"] == "rejected"
        """
        code = fz_stocks(1)[0]
        engine = pool_engine()
        bus: EventBus = engine._components["event_bus"]
        collector = event_collector(bus)
        try:
            _set_virtual_time(engine, _TS)
            _add_stock_to_pool(engine, code)
            driver = _register_ttl(engine, bus, code)

            collector.clear()
            _set_virtual_time(engine, _TTL_FIRE_TS)
            driver.fire_due(_TTL_FIRE_TS)

            order_events = collector.filter(type="OrderPlaced")
            assert len(order_events) >= 1, (
                f"qty=0 时应发布 rejected OrderPlaced（spec L143），"
                f"实际 {len(order_events)} 个"
            )
            order = order_events[0].order
            qty = int(order.get("qty", 0) or 0)
            status = order.get("status", "")
            assert qty == 0 or status in ("rejected", "failed"), (
                f"OrderPlaced 应为 qty=0 或 status=rejected/failed，"
                f"实际 qty={qty}, status={status!r}"
            )
        finally:
            collector.disconnect()

    def test_ttl_qty_zero_position_remains_zero(
        self, pool_engine, event_collector, fz_stocks,
    ):
        """qty=0 降级后持仓仍为 0，无 OrderFilled/PositionUpdated 副作用（spec L143）。

        rejected 订单不实际成交，_on_order_placed 跳过，持仓无变化。

        断言：
          - 无 OrderFilled 事件（rejected 订单不成交）
          - TradeModule._get_position_qty(code, pool_C) == 0（持仓无变化）
          - TradeModule._trackers 无 (pool_C, code) 条目（未建立持仓）
        """
        code = fz_stocks(1)[0]
        engine = pool_engine()
        bus: EventBus = engine._components["event_bus"]
        collector = event_collector(bus)
        try:
            _set_virtual_time(engine, _TS)
            _add_stock_to_pool(engine, code)
            driver = _register_ttl(engine, bus, code)

            collector.clear()
            _set_virtual_time(engine, _TTL_FIRE_TS)
            driver.fire_due(_TTL_FIRE_TS)

            # rejected 订单不成交，无 OrderFilled
            fill_events = collector.filter(type="OrderFilled")
            assert len(fill_events) == 0, (
                f"rejected 订单不应发布 OrderFilled，实际 {len(fill_events)} 个"
            )
            # 持仓仍为 0
            trade_module = _get_trade_module(bus)
            assert trade_module is not None, "未找到 TradeModule 实例"
            assert trade_module._get_position_qty(code, _POOL_C) == 0, (
                f"qty=0 降级后持仓应仍为 0"
            )
            assert (_POOL_C, code) not in trade_module._trackers, (
                f"_trackers 不应有 ({_POOL_C}, {code}) 条目（未建立持仓）"
            )
        finally:
            collector.disconnect()


# ═══════════════════════════════════════════════════════════════
# 测试用例 — 优雅降级后系统不崩溃
# ═══════════════════════════════════════════════════════════════


class TestTTLNoPositionGracefulDegradation:
    """验证 TTL 无持仓优雅降级后系统组件未受影响。"""

    def test_ttl_no_position_event_bus_still_works(
        self, pool_engine, event_collector, fz_stocks,
    ):
        """TTL 无持仓降级后 EventBus 仍可正常发布/订阅事件。

        断言：
          - TTL fire_due 后 EventBus 仍能 publish EdgeFired 事件
          - EventCollector 仍能收集事件
        """
        from core.event_bus import EdgeFired
        code = fz_stocks(1)[0]
        engine = pool_engine()
        bus: EventBus = engine._components["event_bus"]
        collector = event_collector(bus)
        try:
            _set_virtual_time(engine, _TS)
            _add_stock_to_pool(engine, code)
            driver = _register_ttl(engine, bus, code)

            _set_virtual_time(engine, _TTL_FIRE_TS)
            driver.fire_due(_TTL_FIRE_TS)

            # EventBus 仍可正常工作
            collector.clear()
            bus.publish(EdgeFired(eid="health_check", ts=_TTL_FIRE_TS))
            edge_events = collector.filter(type="EdgeFired")
            assert len(edge_events) == 1, (
                f"EventBus 降级后应仍能发布事件，实际 {len(edge_events)} 个 EdgeFired"
            )
            assert edge_events[0].eid == "health_check"
        finally:
            collector.disconnect()

    def test_ttl_no_position_trade_module_qty_zero_confirmed(
        self, pool_engine, event_collector, fz_stocks,
    ):
        """确认 qty=0 场景下 TradeModule._get_position_qty 返回 0。

        断言：
          - TradeModule._get_position_qty(code, pool_C) == 0
          - TradeModule._trackers 无 (pool_C, code) 条目（未建立持仓）
        """
        code = fz_stocks(1)[0]
        engine = pool_engine()
        bus: EventBus = engine._components["event_bus"]
        collector = event_collector(bus)
        try:
            _set_virtual_time(engine, _TS)
            _add_stock_to_pool(engine, code)

            trade_module = _get_trade_module(bus)
            assert trade_module is not None, "未找到 TradeModule 实例"

            qty = trade_module._get_position_qty(code, _POOL_C)
            assert qty == 0, (
                f"未建立持仓时 qty 应为 0，实际 {qty}"
            )
            # _trackers 中不应有 (pool_C, code) 条目
            assert (_POOL_C, code) not in trade_module._trackers, (
                f"_trackers 不应有 ({_POOL_C}, {code}) 条目"
            )
        finally:
            collector.disconnect()

    def test_ttl_no_position_fire_due_after_degradation(
        self, pool_engine, event_collector, fz_stocks,
    ):
        """TTL 无持仓降级后 EventDriver.fire_due 仍可正常调用。

        断言：
          - 降级后再次调用 fire_due 不抛异常
        """
        code = fz_stocks(1)[0]
        engine = pool_engine()
        bus: EventBus = engine._components["event_bus"]
        collector = event_collector(bus)
        try:
            _set_virtual_time(engine, _TS)
            _add_stock_to_pool(engine, code)
            driver = _register_ttl(engine, bus, code)

            # 第一次 fire_due（TTL 触发，qty=0 降级）
            _set_virtual_time(engine, _TTL_FIRE_TS)
            driver.fire_due(_TTL_FIRE_TS)

            # 第二次 fire_due（heap 已空，不应抛异常）
            _set_virtual_time(engine, _TTL_FIRE_TS + 60)
            driver.fire_due(_TTL_FIRE_TS + 60)
        finally:
            collector.disconnect()
