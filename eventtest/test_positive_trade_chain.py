# -*- coding: utf-8 -*-
"""正测试 — 交易事件链（Task 5 SubTask 5.1）。

验证 pool_C 入池触发的买入事件链：
    TransferExecuted → Signal(BUY, qty=100) → OrderPlaced → OrderFilled → PositionUpdated

断言要点：
    - PositionUpdated 后持仓 = 100 股
    - TradeModule._trackers[(pool_C, code)]["qty"] == 100
    - 事件链顺序正确（TransferExecuted → Signal → OrderPlaced → OrderFilled → PositionUpdated）

复用 core/ 现有类（PoolEngine / TradeModule / EventBus），不修改 core/ 源文件，
不使用已删除旧接口（get_node_stocks / SimTickSource / execution_order /
EdgeFired.changed_codes / at_fn / fire_ttl_due / TtlTracker）。
"""
from __future__ import annotations

from typing import Any, List, Optional

import pytest

from core.event_bus import (
    EventBus,
    OrderFilled,
    OrderPlaced,
    PositionUpdated,
    Signal,
    TransferExecuted,
    DataChanged,
)


# ═══════════════════════════════════════════════════════════════
# 测试常量（测试参数，非硬编码实例内容）
# ═══════════════════════════════════════════════════════════════

_TS = 34500.0          # 虚拟时钟起点（09:30:00 当日秒数偏移）
_TICK_PRICE = 10.50    # 模拟 tick 价格（>0，满足 PaperTradeEngine 成交条件）
_POOL_C = "pool_C"     # 目标池 ID（config/pools/sim_test_pool_100.json 中 pool_C.psatt.baimpool=1）


# ═══════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════


def _get_trade_module(bus: EventBus) -> Optional[Any]:
    """从 EventBus 订阅者中提取 TradeModule 实例。

    TradeModule 在 PoolEngine._init_pool_runtime 中创建但不存入 _components，
    仅通过 EventBus 订阅者列表可达。遍历 ``bus._subscribers`` 的 bound method
    ``__self__`` 找到 TradeModule 实例。
    """
    from core.trade_module import TradeModule
    for handlers in bus._subscribers.values():
        for h in handlers:
            owner = getattr(h, "__self__", None)
            if isinstance(owner, TradeModule):
                return owner
    return None


def _setup_latest_price(bus: EventBus, code: str, price: float = _TICK_PRICE) -> None:
    """发布 DataChanged(tick) 预填 TradeModule._latest_prices 缓存。

    TradeModule._paper_execute 要求 price > 0 才能成交。TransferExecuted 触发
    的 BUY Signal price=0（市价单），_on_signal 回退到 _latest_prices 取价。
    必须先发布 tick 设置最新价，否则 BUY 链断裂。
    """
    bus.publish(DataChanged(
        ts=_TS,
        bar_hash="",
        codes=[code],
        source="tick",
        data={code: {"close": price, "code": code}},
    ))


def _publish_transfer_to_pool_c(bus: EventBus, code: str) -> None:
    """发布 TransferExecuted 事件（股票入 pool_C）。

    pool_C 在 auto_buy_pools 中（psatt.baimpool=1），TradeModule._on_transfer_executed
    收到后发布 Signal(BUY, qty=100)。
    """
    bus.publish(TransferExecuted(
        src="cond3",
        tgt=_POOL_C,
        codes=[code],
        mode="copy",
        ts=_TS,
    ))


def _extract_event_types(events: List[Any]) -> List[str]:
    """从事件列表提取类型名序列（用于断言事件链顺序）。"""
    return [type(e).__name__ for e in events]


# ═══════════════════════════════════════════════════════════════
# 测试用例
# ═══════════════════════════════════════════════════════════════


class TestTradeChainBuy:
    """验证 pool_C 入池 → 买入事件链。"""

    def test_transfer_executed_triggers_buy_signal(
        self, pool_engine, event_collector, fz_stocks,
    ):
        """TransferExecuted(tgt=pool_C) → Signal(BUY, qty=100)。

        断言：
          - 恰好发布 1 个 Signal 事件
          - Signal.signal_type == "BUY"
          - Signal.quantity == 100
          - Signal.code == 入池股票代码
        """
        code = fz_stocks(1)[0]
        engine = pool_engine()
        bus: EventBus = engine._components["event_bus"]
        collector = event_collector(bus)
        try:
            _setup_latest_price(bus, code)
            _publish_transfer_to_pool_c(bus, code)

            signals = collector.filter(type="Signal")
            assert len(signals) == 1, f"期望 1 个 Signal，实际 {len(signals)}"
            sig = signals[0]
            assert sig.signal_type == "BUY", f"signal_type={sig.signal_type}"
            assert sig.quantity == 100, f"quantity={sig.quantity}"
            assert sig.code == code, f"code={sig.code}"
            assert sig.pool_id == _POOL_C, f"pool_id={sig.pool_id}"
        finally:
            collector.disconnect()

    def test_buy_signal_triggers_order_placed(
        self, pool_engine, event_collector, fz_stocks,
    ):
        """Signal(BUY) → OrderPlaced。

        断言：
          - 恰好发布 1 个 OrderPlaced 事件
          - OrderPlaced.order["side"] == "BUY"
          - OrderPlaced.order["qty"] == 100
        """
        code = fz_stocks(1)[0]
        engine = pool_engine()
        bus: EventBus = engine._components["event_bus"]
        collector = event_collector(bus)
        try:
            _setup_latest_price(bus, code)
            _publish_transfer_to_pool_c(bus, code)

            orders = collector.filter(type="OrderPlaced")
            assert len(orders) == 1, f"期望 1 个 OrderPlaced，实际 {len(orders)}"
            order = orders[0].order
            assert order["side"] == "BUY", f"side={order['side']}"
            assert order["qty"] == 100, f"qty={order['qty']}"
            assert order["code"] == code, f"code={order['code']}"
        finally:
            collector.disconnect()

    def test_order_placed_triggers_fill_and_position(
        self, pool_engine, event_collector, fz_stocks,
    ):
        """OrderPlaced → OrderFilled + PositionUpdated。

        断言：
          - 恰好发布 1 个 OrderFilled 和 1 个 PositionUpdated
          - OrderFilled.fill["side"] == "BUY"
          - PositionUpdated.tracker["qty"] == 100
        """
        code = fz_stocks(1)[0]
        engine = pool_engine()
        bus: EventBus = engine._components["event_bus"]
        collector = event_collector(bus)
        try:
            _setup_latest_price(bus, code)
            _publish_transfer_to_pool_c(bus, code)

            fills = collector.filter(type="OrderFilled")
            positions = collector.filter(type="PositionUpdated")
            assert len(fills) == 1, f"期望 1 个 OrderFilled，实际 {len(fills)}"
            assert len(positions) == 1, f"期望 1 个 PositionUpdated，实际 {len(positions)}"
            assert fills[0].fill["side"] == "BUY"
            assert positions[0].tracker["qty"] == 100
        finally:
            collector.disconnect()

    def test_buy_chain_full_event_sequence(
        self, pool_engine, event_collector, fz_stocks,
    ):
        """完整买入事件链：TransferExecuted → Signal → OrderPlaced → OrderFilled → PositionUpdated。

        断言事件链顺序正确。

        注意：使用 ``bus.get_events()`` 而非 ``collector.events`` 取事件序列——
        EventBus 在 ``publish`` 中先 ``_events.append(event)`` 再调用 handlers，
        所以 ``bus._events`` 保留正确的发布顺序；而 EventCollector 的
        ``subscribe_any`` handler 在 typed handlers 之后执行，嵌套 publish 导致
        collector 看到的事件顺序是反向的（最深层事件先收集）。
        """
        code = fz_stocks(1)[0]
        engine = pool_engine()
        bus: EventBus = engine._components["event_bus"]
        collector = event_collector(bus)
        try:
            _setup_latest_price(bus, code)
            _publish_transfer_to_pool_c(bus, code)

            # 过滤出买入链相关事件类型（用 bus.get_events 取正确发布顺序）
            chain_types = {
                "TransferExecuted", "Signal", "OrderPlaced",
                "OrderFilled", "PositionUpdated",
            }
            chain_events = [
                e for e in bus.get_events()
                if type(e).__name__ in chain_types
            ]
            actual_seq = _extract_event_types(chain_events)
            expected_seq = [
                "TransferExecuted", "Signal", "OrderPlaced",
                "OrderFilled", "PositionUpdated",
            ]
            assert actual_seq == expected_seq, (
                f"事件链顺序错误：期望 {expected_seq}，实际 {actual_seq}"
            )
        finally:
            collector.disconnect()

    def test_position_updated_qty_is_100(
        self, pool_engine, event_collector, fz_stocks,
    ):
        """PositionUpdated 后持仓 = 100 股。

        断言 PositionUpdated.tracker["qty"] == 100。
        """
        code = fz_stocks(1)[0]
        engine = pool_engine()
        bus: EventBus = engine._components["event_bus"]
        collector = event_collector(bus)
        try:
            _setup_latest_price(bus, code)
            _publish_transfer_to_pool_c(bus, code)

            positions = collector.filter(type="PositionUpdated")
            assert len(positions) == 1
            tracker = positions[0].tracker
            assert tracker["qty"] == 100, f"持仓数量={tracker['qty']}，期望 100"
            assert tracker["code"] == code
            assert tracker["node_id"] == _POOL_C
        finally:
            collector.disconnect()

    def test_trade_module_position_is_100(
        self, pool_engine, event_collector, fz_stocks,
    ):
        """TradeModule._trackers[(pool_C, code)]["qty"] == 100。

        直接检查 TradeModule 内部持仓跟踪表，验证持仓状态持久化。
        """
        code = fz_stocks(1)[0]
        engine = pool_engine()
        bus: EventBus = engine._components["event_bus"]
        collector = event_collector(bus)
        try:
            _setup_latest_price(bus, code)
            _publish_transfer_to_pool_c(bus, code)

            trade_module = _get_trade_module(bus)
            assert trade_module is not None, "未找到 TradeModule 实例"
            key = (_POOL_C, code)
            tracker = trade_module._trackers.get(key)
            assert tracker is not None, f"_trackers[{key}] 不存在"
            assert tracker["qty"] == 100, f"持仓={tracker['qty']}，期望 100"
        finally:
            collector.disconnect()

    def test_on_transfer_executed_publishes_buy_signal(
        self, pool_engine, event_collector, fz_stocks,
    ):
        """直接验证 TradeModule._on_transfer_executed 发布 BUY Signal。

        不依赖事件链间接触发，直接调用 handler 方法并断言 Signal 发布。
        """
        code = fz_stocks(1)[0]
        engine = pool_engine()
        bus: EventBus = engine._components["event_bus"]
        collector = event_collector(bus)
        try:
            _setup_latest_price(bus, code)
            trade_module = _get_trade_module(bus)
            assert trade_module is not None

            collector.clear()
            trade_module._on_transfer_executed(TransferExecuted(
                src="cond3", tgt=_POOL_C, codes=[code], mode="copy", ts=_TS,
            ))

            signals = collector.filter(type="Signal")
            assert len(signals) == 1
            assert signals[0].signal_type == "BUY"
            assert signals[0].quantity == 100
        finally:
            collector.disconnect()

    def test_on_signal_dispatches_to_paper_execute(
        self, pool_engine, event_collector, fz_stocks,
    ):
        """直接验证 TradeModule._on_signal 分派到 _paper_execute 并发布 OrderPlaced。

        不依赖 TransferExecuted 间接触发，直接调用 _on_signal 并断言 OrderPlaced 发布。
        """
        code = fz_stocks(1)[0]
        engine = pool_engine()
        bus: EventBus = engine._components["event_bus"]
        collector = event_collector(bus)
        try:
            _setup_latest_price(bus, code)
            trade_module = _get_trade_module(bus)
            assert trade_module is not None

            collector.clear()
            trade_module._on_signal(Signal(
                signal_type="BUY", code=code, pool_id=_POOL_C,
                price=0.0, ts=_TS, quantity=100,
            ))

            orders = collector.filter(type="OrderPlaced")
            assert len(orders) == 1
            assert orders[0].order["side"] == "BUY"
            assert orders[0].order["qty"] == 100
        finally:
            collector.disconnect()
