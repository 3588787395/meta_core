# -*- coding: utf-8 -*-
"""交易执行正测试（Task 7）。

覆盖 SubTask 7.1 - 7.5：
  - 7.1 C 池入池立即市价买入 100 股
  - 7.2 停留 20 分钟出池卖出
  - 7.3 入池动作分发（声音/弹窗/TDX 板块/历史保存）
  - 7.4 持仓管理（_Position 增减）
  - 7.5 交易记录（_TradeRecord）

测试可能因源码 bug 而失败，这是正常的，不修改源码。
"""
from __future__ import annotations

from typing import Any, Dict, List

import pytest

from core.event_bus import (
    AlertRaised,
    DataChanged,
    EventBus,
    EventLogged,
    OrderFilled,
    OrderPlaced,
    PositionUpdated,
    Signal,
    TransferExecuted,
    TTLDue,
)
from core.trade_module import (
    TradeModule,
    _PaperTrade,
    _PaperTradeEngine,
    _Position,
    _TradeExecutor,
    _TradeRecord,
    _dispatch_pool_enter_actions,
    _play_sound_alert,
    _save_to_tdx_block,
    _show_popup_alert,
)


# ---------------------------------------------------------------------------
# SubTask 7.1: C 池入池立即市价买入 100 股
# ---------------------------------------------------------------------------

class TestPoolEntryImmediateBuy:
    """验证 C 池入池即买入 100 股市价单链路。

    spec: TransferExecuted → Signal(BUY, 100) → OrderPlaced → OrderFilled。
    """

    def test_transfer_to_auto_buy_pool_emits_buy_signal(self):
        """股票转入 auto_buy_pools 配置池时发布 BUY Signal。"""
        bus = EventBus()
        captured: List[Any] = []

        def _handler(event):
            captured.append(event)

        bus.subscribe(Signal, _handler)
        trade = TradeModule(
            bus, config={"auto_buy_pools": ["pool_C"], "trade_interface": "paper_trade"}
        )
        # 模拟 C 池入池事件
        bus.publish(TransferExecuted(
            src="pool_A", tgt="pool_C", codes=["code_a"], mode="move", ts=34500.0,
        ))
        buy_signals = [e for e in captured if e.signal_type == "BUY"]
        assert len(buy_signals) == 1
        assert buy_signals[0].code == "code_a"
        assert buy_signals[0].quantity == 100

    def test_buy_signal_triggers_paper_trade_buy(self):
        """BUY Signal 经 TradeModule 触发 _PaperTradeEngine.buy。"""
        bus = EventBus()
        trade = TradeModule(
            bus,
            config={
                "auto_buy_pools": ["pool_C"],
                "trade_interface": "paper_trade",
                "initial_capital": 1_000_000.0,
                "default_quantity": 100,
            },
        )
        # 预置最新价缓存（DataChanged tick 事件）
        bus.publish(DataChanged(
            ts=34400.0, bar_hash="", codes=["code_a"],
            source="tick", data={"code": "code_a", "close": 10.0},
        ))
        # 触发入池买入
        bus.publish(TransferExecuted(
            src="pool_A", tgt="pool_C", codes=["code_a"], mode="move", ts=34500.0,
        ))
        # 应在 _trading_service.positions 中创建持仓
        assert "code_a" in trade._trading_service.positions
        pos = trade._trading_service.positions["code_a"]
        assert pos.quantity == 100

    def test_buy_signal_default_quantity_100(self):
        """Signal.quantity=0 时 BUY 默认 100 股。"""
        bus = EventBus()
        trade = TradeModule(
            bus, config={"trade_interface": "paper_trade", "default_quantity": 100}
        )
        # 预置价格
        bus.publish(DataChanged(
            ts=34400.0, bar_hash="", codes=["code_a"],
            source="tick", data={"code": "code_a", "close": 10.0},
        ))
        # 直接发 BUY Signal quantity=0
        bus.publish(Signal(
            signal_type="BUY", code="code_a", pool_id="pool_C",
            price=0.0, ts=34500.0, quantity=0,
        ))
        # 应买入 100 股
        assert "code_a" in trade._trading_service.positions
        assert trade._trading_service.positions["code_a"].quantity == 100

    def test_buy_signal_publishes_order_placed(self):
        """BUY Signal 触发 OrderPlaced 事件发布。"""
        bus = EventBus()
        captured: List[Any] = []

        def _handler(event):
            captured.append(event)

        bus.subscribe(OrderPlaced, _handler)
        trade = TradeModule(
            bus, config={"trade_interface": "paper_trade", "default_quantity": 100}
        )
        bus.publish(DataChanged(
            ts=34400.0, bar_hash="", codes=["code_a"],
            source="tick", data={"code": "code_a", "close": 10.0},
        ))
        bus.publish(Signal(
            signal_type="BUY", code="code_a", pool_id="pool_C",
            price=0.0, ts=34500.0, quantity=100,
        ))
        orders = [e for e in captured]
        assert len(orders) >= 1
        assert orders[0].order["code"] == "code_a"
        assert orders[0].order["side"] == "BUY"


# ---------------------------------------------------------------------------
# SubTask 7.2: 停留 20 分钟出池卖出
# ---------------------------------------------------------------------------

class TestPoolExitSellAfterTTL:
    """验证停留 20 分钟出池卖出链路。

    spec: TTLDue → Signal(SELL) → OrderPlaced → OrderFilled。
    """

    def test_ttl_due_emits_sell_signal_for_auto_sell_pool(self):
        """auto_sell_pools 配置的池 TTLDue 触发 SELL Signal。"""
        bus = EventBus()
        captured: List[Any] = []

        def _handler(event):
            captured.append(event)

        bus.subscribe(Signal, _handler)
        trade = TradeModule(
            bus,
            config={"auto_sell_pools": ["pool_C"], "trade_interface": "paper_trade"},
        )
        # 预置持仓
        trade._trading_service.positions["code_a"] = _Position(
            code="code_a", entry_price=10.0, entry_time=34500.0,
            quantity=100, current_price=10.5,
        )
        # 预置最新价
        trade._latest_prices["code_a"] = 10.5

        bus.publish(TTLDue(node_id="pool_C", code="code_a", ts=45900.0))
        sell_signals = [e for e in captured if e.signal_type == "SELL"]
        assert len(sell_signals) == 1
        assert sell_signals[0].code == "code_a"

    def test_ttl_due_ignored_for_non_auto_sell_pool(self):
        """非 auto_sell_pools 池的 TTLDue 不触发 SELL。"""
        bus = EventBus()
        captured: List[Any] = []

        def _handler(event):
            captured.append(event)

        bus.subscribe(Signal, _handler)
        trade = TradeModule(
            bus, config={"auto_sell_pools": ["pool_C"], "trade_interface": "paper_trade"}
        )
        bus.publish(TTLDue(node_id="pool_other", code="code_a", ts=45900.0))
        sell_signals = [e for e in captured if e.signal_type == "SELL"]
        assert len(sell_signals) == 0

    def test_sell_signal_clears_position(self):
        """SELL Signal 经 TradeModule 清空持仓。"""
        bus = EventBus()
        trade = TradeModule(
            bus, config={"trade_interface": "paper_trade", "default_quantity": 100}
        )
        # 预置持仓 + 最新价
        trade._trading_service.positions["code_a"] = _Position(
            code="code_a", entry_price=10.0, entry_time=34500.0,
            quantity=100, current_price=10.5,
        )
        trade._latest_prices["code_a"] = 10.5
        # 直接触发 SELL
        bus.publish(Signal(
            signal_type="SELL", code="code_a", pool_id="pool_C",
            price=0.0, ts=45900.0, quantity=0,  # quantity=0 表示卖全部
        ))
        assert "code_a" not in trade._trading_service.positions

    def test_ttl_20min_sells_after_buy(self):
        """端到端：买入后 20 分钟（1200 秒）后 TTLDue 触发卖出。"""
        bus = EventBus()
        trade = TradeModule(
            bus,
            config={
                "auto_buy_pools": ["pool_C"],
                "auto_sell_pools": ["pool_C"],
                "trade_interface": "paper_trade",
                "default_quantity": 100,
            },
        )
        # 入池买入
        bus.publish(DataChanged(
            ts=34400.0, bar_hash="", codes=["code_a"],
            source="tick", data={"code": "code_a", "close": 10.0},
        ))
        bus.publish(TransferExecuted(
            src="pool_A", tgt="pool_C", codes=["code_a"], mode="move", ts=34500.0,
        ))
        assert "code_a" in trade._trading_service.positions

        # 20 分钟后 TTL 触发卖出
        bus.publish(TTLDue(
            node_id="pool_C", code="code_a", ts=34500.0 + 1200.0,
        ))
        assert "code_a" not in trade._trading_service.positions


# ---------------------------------------------------------------------------
# SubTask 7.3: 入池动作分发（声音/弹窗/TDX 板块/历史保存）
# ---------------------------------------------------------------------------

class TestPoolEnterActionsDispatch:
    """验证 _dispatch_pool_enter_actions 分发声音/弹窗/TDX 板块/历史保存四种副作用。"""

    def test_dispatch_calls_configured_actions(self):
        """配置 bsound=1/btip=1/bsavetoblock=1/bsavehis=1 时全部 dispatch。"""
        node = {
            "id": "node_a",
            "params": {
                "tdx_psatt": {
                    "bsound": 1,
                    "nsoundtype": 0,
                    "soundfile": "",
                    "btip": 1,
                    "bsavetoblock": 1,
                    "blockfile": "test_block",
                    "bclearblock": 0,
                    "bsavehis": 1,
                }
            },
        }
        new_stocks = [{"code": "000001"}]
        # 不应抛异常；返回值 >= 0
        result = _dispatch_pool_enter_actions(
            "pool_a", "node_a", node, new_stocks,
        )
        assert isinstance(result, int)
        assert result >= 0

    def test_play_sound_alert_does_not_raise(self):
        """_play_sound_alert 不抛异常。"""
        # nsoundtype=0 表示系统提示音
        _play_sound_alert(0, "", "node_a", [{"code": "000001"}])
        _play_sound_alert(1, "custom.wav", "node_a", [{"code": "000001"}])

    def test_show_popup_alert_does_not_raise(self):
        """_show_popup_alert 不抛异常。"""
        _show_popup_alert("node_a", [{"code": "000001"}])
        _show_popup_alert("node_a", [str])

    def test_save_to_tdx_block_returns_count(self):
        """_save_to_tdx_block 返回保存的代码数。"""
        new_stocks = [{"code": "000001"}, {"code": "000002"}]
        result = _save_to_tdx_block("test_block_file", new_stocks, bclearblock=1)
        assert isinstance(result, int)
        assert result >= 0

    def test_save_to_tdx_block_empty_noop(self):
        """空 new_stocks 返回 0。"""
        assert _save_to_tdx_block("", [{"code": "000001"}], bclearblock=1) == 0
        assert _save_to_tdx_block("test", [], bclearblock=1) == 0

    def test_trade_module_psatt_side_effects_via_order_filled(self):
        """TradeModule._on_order_filled 触发 psatt 副作用事件。"""
        bus = EventBus()
        captured: List[Any] = []

        def _handler(event):
            captured.append(event)

        bus.subscribe(EventLogged, _handler)
        bus.subscribe(AlertRaised, _handler)

        psatt = {
            "bsavehis": 1, "bsound": 1, "btip": 1,
            "bsavetoblock": 1, "blockfile": "blk", "bclearblock": 0,
            "baimpool": 1, "nsoundtype": 0, "soundfile": "",
        }
        trade = TradeModule(
            bus, config={"trade_interface": "paper_trade", "psatt": psatt}
        )
        # 直接发布 OrderFilled 携带 psatt
        bus.publish(OrderFilled(
            fill={
                "code": "code_a", "side": "BUY", "qty": 100,
                "price": 10.0, "order_id": "1", "fill_ts": 34500.0,
                "psatt": psatt,
            },
            ts=34500.0,
        ))
        # 应至少触发 1 个 EventLogged/AlertRaised（psatt 副作用）
        assert len(captured) >= 1


# ---------------------------------------------------------------------------
# SubTask 7.4: 持仓管理（_Position 增减）
# ---------------------------------------------------------------------------

class TestPositionManagement:
    """验证 _Position 增减与盈亏计算。"""

    def test_position_default_quantity_100(self):
        """_Position 默认 quantity=100。"""
        pos = _Position(
            code="code_a", entry_price=10.0, entry_time=34500.0,
        )
        assert pos.quantity == 100

    def test_position_market_value(self):
        """market_value = current_price * quantity。"""
        pos = _Position(
            code="code_a", entry_price=10.0, entry_time=34500.0,
            quantity=200, current_price=12.0,
        )
        assert pos.market_value == 2400.0

    def test_position_profit_positive_when_price_rises(self):
        """价格上涨时 profit 为正。"""
        pos = _Position(
            code="code_a", entry_price=10.0, entry_time=34500.0,
            quantity=100, current_price=12.0,
        )
        assert pos.profit == 200.0

    def test_position_profit_pct_calculation(self):
        """profit_pct = (current - entry) / entry * 100。"""
        pos = _Position(
            code="code_a", entry_price=10.0, entry_time=34500.0,
            quantity=100, current_price=12.0,
        )
        assert pos.profit_pct == pytest.approx(20.0, abs=0.01)

    def test_position_profit_pct_zero_when_entry_zero(self):
        """entry_price=0 时 profit_pct 返回 0.0（避免除零）。"""
        pos = _Position(
            code="code_a", entry_price=0.0, entry_time=34500.0,
            quantity=100, current_price=10.0,
        )
        assert pos.profit_pct == 0.0

    def test_paper_engine_buy_creates_position(self):
        """_PaperTradeEngine.buy 创建 _Position。"""
        engine = _PaperTradeEngine(initial_capital=100_000.0, default_quantity=100)
        record = engine.buy("code_a", price=10.0, timestamp=34500.0)
        assert record is not None
        assert record.direction == "buy"
        assert "code_a" in engine.positions
        assert engine.positions["code_a"].quantity == 100

    def test_paper_engine_sell_reduces_position(self):
        """_PaperTradeEngine.sell 减少持仓。"""
        engine = _PaperTradeEngine(initial_capital=100_000.0, default_quantity=100)
        engine.buy("code_a", price=10.0, timestamp=34500.0)
        # 部分卖出 50 股
        record = engine.sell("code_a", price=11.0, quantity=50, timestamp=34600.0)
        assert record is not None
        assert record.direction == "sell"
        assert engine.positions["code_a"].quantity == 50

    def test_paper_engine_sell_all_removes_position(self):
        """清仓卖出后持仓被删除。"""
        engine = _PaperTradeEngine(initial_capital=100_000.0, default_quantity=100)
        engine.buy("code_a", price=10.0, timestamp=34500.0)
        engine.sell("code_a", price=11.0, quantity=100, timestamp=34600.0)
        assert "code_a" not in engine.positions

    def test_paper_engine_sell_without_position_returns_none(self):
        """无持仓时 sell 返回 None。"""
        engine = _PaperTradeEngine(initial_capital=100_000.0)
        record = engine.sell("code_a", price=10.0, quantity=100, timestamp=34500.0)
        assert record is None


# ---------------------------------------------------------------------------
# SubTask 7.5: 交易记录（_TradeRecord）
# ---------------------------------------------------------------------------

class TestTradeRecord:
    """验证 _TradeRecord 成交记录。"""

    def test_trade_record_buy_fields(self):
        """买入 _TradeRecord 字段正确。"""
        rec = _TradeRecord(
            code="code_a", direction="buy", price=10.0, quantity=100,
            timestamp=34500.0, commission=3.0, slippage=0.01,
        )
        assert rec.code == "code_a"
        assert rec.direction == "buy"
        assert rec.price == 10.0
        assert rec.quantity == 100
        assert rec.commission == 3.0
        assert rec.profit == 0.0  # buy 时 profit 默认 0

    def test_trade_record_sell_has_profit(self):
        """卖出 _TradeRecord 携带 profit。"""
        rec = _TradeRecord(
            code="code_a", direction="sell", price=11.0, quantity=100,
            timestamp=34600.0, commission=3.3, slippage=0.011,
            profit=100.0,
        )
        assert rec.direction == "sell"
        assert rec.profit == 100.0

    def test_paper_engine_appends_trade_history_on_buy(self):
        """buy 后 trade_history 追加记录。"""
        engine = _PaperTradeEngine(initial_capital=100_000.0)
        engine.buy("code_a", price=10.0, quantity=100, timestamp=34500.0)
        assert len(engine.trade_history) == 1
        assert engine.trade_history[0].direction == "buy"
        assert engine.trade_history[0].code == "code_a"

    def test_paper_engine_appends_trade_history_on_sell(self):
        """sell 后 trade_history 追加记录。"""
        engine = _PaperTradeEngine(initial_capital=100_000.0)
        engine.buy("code_a", price=10.0, quantity=100, timestamp=34500.0)
        engine.sell("code_a", price=11.0, quantity=100, timestamp=34600.0)
        assert len(engine.trade_history) == 2
        assert engine.trade_history[1].direction == "sell"

    def test_paper_engine_buy_rejected_when_insufficient_cash(self):
        """资金不足时 buy 返回 None，且 trade_history 不追加。"""
        engine = _PaperTradeEngine(initial_capital=100.0)  # 极少资金
        record = engine.buy(
            "code_a", price=10.0, quantity=100, timestamp=34500.0
        )
        assert record is None
        assert len(engine.trade_history) == 0
        assert "code_a" not in engine.positions

    def test_trade_executor_records_trades(self):
        """_TradeExecutor 记录 BUY/SELL 交易到 _trades 列表。"""
        executor = _TradeExecutor(bus=None, initial_cash=1_000_000.0)

        # on_signal 内部 isinstance(event, Signal) 校验，必须用真实 Signal 实例
        executor.on_signal(Signal(
            signal_type="BUY", code="code_a", pool_id="pool_C",
            price=10.0, quantity=100, condition="", ts=34500.0,
        ))
        trades = executor.get_trades()
        assert len(trades) == 1
        assert trades[0]["action"] == "BUY"
        assert trades[0]["code"] == "code_a"
        assert trades[0]["quantity"] == 100

    def test_trade_executor_position_after_buy(self):
        """_TradeExecutor BUY 后持仓表更新。"""
        executor = _TradeExecutor(bus=None, initial_cash=1_000_000.0)

        # on_signal 内部 isinstance(event, Signal) 校验，必须用真实 Signal 实例
        executor.on_signal(Signal(
            signal_type="BUY", code="code_a", pool_id="pool_C",
            price=10.0, quantity=100, condition="", ts=34500.0,
        ))
        pos = executor.get_position("code_a")
        assert pos is not None
        assert pos["quantity"] == 100
        assert pos["cost_price"] == 10.0

    def test_paper_engine_from_config(self):
        """_PaperTradeEngine.from_config 从配置创建实例。"""
        config = {
            "methods": {
                "initial_capital": 500_000.0,
                "commission_rate": 0.0005,
                "slippage_pct": 0.002,
                "default_quantity": 200,
            }
        }
        engine = _PaperTradeEngine.from_config(config)
        assert engine.initial_capital == 500_000.0
        assert engine.commission_rate == 0.0005
        assert engine.slippage_pct == 0.002
        assert engine.default_quantity == 200

    def test_paper_engine_freeze_blocks_trades(self):
        """freeze 后 buy/sell 都返回 None。"""
        engine = _PaperTradeEngine(initial_capital=100_000.0)
        engine.freeze()
        assert engine.buy("code_a", price=10.0, timestamp=34500.0) is None
        # 解冻后可正常交易
        engine.unfreeze()
        assert engine.buy("code_a", price=10.0, timestamp=34500.0) is not None


# ---------------------------------------------------------------------------
# SubTask 7.6: _INTERFACE_HANDLERS 表驱动分派 + 完整事件链
# ---------------------------------------------------------------------------
class TestInterfaceHandlersTable:
    """验证 TradeModule._INTERFACE_HANDLERS 表驱动分派（无 if/elif 链）。

    spec: 交易接口类型 → handler 方法名通过 _INTERFACE_HANDLERS 字典分派，
    禁止 if/elif 链硬编码接口类型。
    """

    def test_interface_handlers_has_three_entries(self):
        """_INTERFACE_HANDLERS 表有 3 个条目。"""
        assert len(TradeModule._INTERFACE_HANDLERS) == 3

    def test_interface_handlers_contains_live_order_key(self):
        """表包含 live_order 键。"""
        assert "live_order" in TradeModule._INTERFACE_HANDLERS

    def test_interface_handlers_contains_paper_trade_key(self):
        """表包含 paper_trade 键。"""
        assert "paper_trade" in TradeModule._INTERFACE_HANDLERS

    def test_interface_handlers_contains_noop_key(self):
        """表包含 noop 键。"""
        assert "noop" in TradeModule._INTERFACE_HANDLERS

    def test_interface_handlers_values_are_method_names(self):
        """表中值是 TradeModule 实例上的真实方法名。"""
        trade = TradeModule(EventBus(), config={"trade_interface": "noop"})
        for iface_type, method_name in TradeModule._INTERFACE_HANDLERS.items():
            assert hasattr(trade, method_name), (
                f"{iface_type} → {method_name} 不存在于 TradeModule"
            )
            assert callable(getattr(trade, method_name))

    def test_interface_handlers_live_order_maps_to_live_execute(self):
        """live_order 映射到 _live_execute。"""
        assert TradeModule._INTERFACE_HANDLERS["live_order"] == "_live_execute"

    def test_interface_handlers_paper_trade_maps_to_paper_execute(self):
        """paper_trade 映射到 _paper_execute。"""
        assert TradeModule._INTERFACE_HANDLERS["paper_trade"] == "_paper_execute"

    def test_interface_handlers_noop_maps_to_noop_execute(self):
        """noop 映射到 _noop_execute。"""
        assert TradeModule._INTERFACE_HANDLERS["noop"] == "_noop_execute"


class TestBuySignalEventChain:
    """验证 BUY Signal → OrderPlaced → OrderFilled → PositionUpdated 完整事件链。

    spec: 买入信号触发完整事件链，持仓增加。
    """

    def test_buy_signal_generates_full_event_chain(self):
        """BUY Signal 产生 OrderPlaced → OrderFilled → PositionUpdated 事件链。"""
        bus = EventBus()
        captured: List[Any] = []

        def _handler(event):
            captured.append(event)

        bus.subscribe(OrderPlaced, _handler)
        bus.subscribe(OrderFilled, _handler)
        bus.subscribe(PositionUpdated, _handler)
        trade = TradeModule(
            bus, config={"trade_interface": "paper_trade", "default_quantity": 100}
        )
        # 预置最新价
        bus.publish(DataChanged(
            ts=34400.0, bar_hash="", codes=["code_a"],
            source="tick", data={"code": "code_a", "close": 10.0},
        ))
        # 触发 BUY Signal
        bus.publish(Signal(
            signal_type="BUY", code="code_a", pool_id="pool_C",
            price=0.0, ts=34500.0, quantity=100,
        ))
        # 验证事件链：至少有 OrderPlaced 和 PositionUpdated
        event_types = [type(e).__name__ for e in captured]
        assert "OrderPlaced" in event_types
        assert "PositionUpdated" in event_types
        # OrderPlaced 应在 PositionUpdated 之前
        if "OrderFilled" in event_types:
            op_idx = event_types.index("OrderPlaced")
            of_idx = event_types.index("OrderFilled")
            pu_idx = event_types.index("PositionUpdated")
            assert op_idx < of_idx
            assert of_idx < pu_idx

    def test_buy_signal_creates_position(self):
        """BUY Signal 后持仓表创建 code_a 持仓。"""
        bus = EventBus()
        trade = TradeModule(
            bus, config={"trade_interface": "paper_trade", "default_quantity": 100}
        )
        bus.publish(DataChanged(
            ts=34400.0, bar_hash="", codes=["code_a"],
            source="tick", data={"code": "code_a", "close": 10.0},
        ))
        bus.publish(Signal(
            signal_type="BUY", code="code_a", pool_id="pool_C",
            price=0.0, ts=34500.0, quantity=100,
        ))
        assert "code_a" in trade._trading_service.positions
        assert trade._trading_service.positions["code_a"].quantity == 100

    def test_sell_signal_decreases_position_via_chain(self):
        """SELL Signal 产生事件链且持仓减少。"""
        bus = EventBus()
        captured: List[Any] = []

        def _handler(event):
            captured.append(event)

        bus.subscribe(OrderPlaced, _handler)
        bus.subscribe(PositionUpdated, _handler)
        trade = TradeModule(
            bus, config={"trade_interface": "paper_trade", "default_quantity": 100}
        )
        # 先买入建仓
        bus.publish(DataChanged(
            ts=34400.0, bar_hash="", codes=["code_a"],
            source="tick", data={"code": "code_a", "close": 10.0},
        ))
        bus.publish(Signal(
            signal_type="BUY", code="code_a", pool_id="pool_C",
            price=0.0, ts=34500.0, quantity=100,
        ))
        assert "code_a" in trade._trading_service.positions
        captured.clear()
        # 触发 SELL Signal
        trade._latest_prices["code_a"] = 10.5
        bus.publish(Signal(
            signal_type="SELL", code="code_a", pool_id="pool_C",
            price=0.0, ts=34600.0, quantity=0,
        ))
        # 卖出后持仓应被清除
        assert "code_a" not in trade._trading_service.positions
        # 应有 OrderPlaced 事件
        event_types = [type(e).__name__ for e in captured]
        assert "OrderPlaced" in event_types

    def test_trade_module_subscribes_to_signal(self):
        """TradeModule 构造时订阅 Signal 事件。"""
        bus = EventBus()
        trade = TradeModule(bus, config={"trade_interface": "noop"})
        # 通过 _register_subscribers 注册的订阅应使 Signal 事件可被处理
        # 验证：发布 Signal 不抛异常且 trade 对象正常存在
        assert trade._bus is bus
        bus.publish(Signal(
            signal_type="BUY", code="code_a", pool_id="pool_C",
            price=10.0, ts=34500.0, quantity=100,
        ))


# ============================================================================
# 变更 E：_TRADEATTR_FIELD_MAP 表合并 if side == "BUY" 链回归断言
# ============================================================================


class TestChangeETradeattrFieldMap:
    """变更 E：_TRADEATTR_FIELD_MAP + _TRADEATTR_TARGET_KEYS 表，消除 if side == "BUY" 分支。"""

    def test_tradeattr_field_map_exists(self):
        """_TRADEATTR_FIELD_MAP 表存在。"""
        from core.trade_module import _TRADEATTR_FIELD_MAP
        assert isinstance(_TRADEATTR_FIELD_MAP, dict), \
            "_TRADEATTR_FIELD_MAP 应为 dict"

    def test_tradeattr_field_map_has_buy_and_sell(self):
        """_TRADEATTR_FIELD_MAP 含 BUY 与 SELL 两键。"""
        from core.trade_module import _TRADEATTR_FIELD_MAP
        assert "BUY" in _TRADEATTR_FIELD_MAP, "_TRADEATTR_FIELD_MAP 应含 BUY"
        assert "SELL" in _TRADEATTR_FIELD_MAP, "_TRADEATTR_FIELD_MAP 应含 SELL"

    def test_tradeattr_field_map_buy_has_nine_fields(self):
        """BUY 字段列表含 9 个 enter* 字段。"""
        from core.trade_module import _TRADEATTR_FIELD_MAP
        buy_fields = _TRADEATTR_FIELD_MAP["BUY"]
        assert isinstance(buy_fields, list), "BUY 字段应为 list"
        assert len(buy_fields) == 9, \
            f"BUY 应含 9 个字段，实际 {len(buy_fields)}"
        # 全部字段应以 enter 前缀开头
        assert all(f.startswith("enter") for f in buy_fields), \
            f"BUY 字段应以 enter 前缀开头，实际 {buy_fields}"

    def test_tradeattr_field_map_sell_has_nine_fields(self):
        """SELL 字段列表含 9 个 exit* 字段。"""
        from core.trade_module import _TRADEATTR_FIELD_MAP
        sell_fields = _TRADEATTR_FIELD_MAP["SELL"]
        assert isinstance(sell_fields, list), "SELL 字段应为 list"
        assert len(sell_fields) == 9, \
            f"SELL 应含 9 个字段，实际 {len(sell_fields)}"
        # 全部字段应以 exit 前缀开头
        assert all(f.startswith("exit") for f in sell_fields), \
            f"SELL 字段应以 exit 前缀开头，实际 {sell_fields}"

    def test_tradeattr_target_keys_has_nine_keys(self):
        """_TRADEATTR_TARGET_KEYS 列表含 9 个 key。"""
        from core.trade_module import _TRADEATTR_TARGET_KEYS
        assert isinstance(_TRADEATTR_TARGET_KEYS, list), \
            "_TRADEATTR_TARGET_KEYS 应为 list"
        assert len(_TRADEATTR_TARGET_KEYS) == 9, \
            f"_TRADEATTR_TARGET_KEYS 应含 9 个 key，实际 {len(_TRADEATTR_TARGET_KEYS)}"

    def test_apply_tradeattr_no_if_side_buy_branch(self):
        """_apply_tradeattr 方法体内无 if side == "BUY"/elif side == "SELL" 分支。"""
        import ast
        import re
        from pathlib import Path
        trade_path = Path(__file__).resolve().parent.parent / "core" / "trade_module.py"
        src = trade_path.read_text(encoding="utf-8")
        tree = ast.parse(src)
        method_text = None
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "_apply_tradeattr":
                end = getattr(node, "end_lineno", node.lineno) or node.lineno
                method_text = "\n".join(src.splitlines()[node.lineno - 1: end])
                break
        assert method_text is not None, "trade_module 应含 _apply_tradeattr 方法"
        matches = re.findall(r'if side == "BUY"|elif side == "SELL"', method_text)
        assert len(matches) == 0, (
            f"_apply_tradeattr 不应含 if side == \"BUY\"/elif side == \"SELL\" 分支"
            f"（变更 E 已表驱动化），实际 {matches}"
        )

    def test_apply_tradeattr_uses_field_map_and_target_keys(self):
        """_apply_tradeattr 方法引用 _TRADEATTR_FIELD_MAP 与 _TRADEATTR_TARGET_KEYS。"""
        import ast
        from pathlib import Path
        trade_path = Path(__file__).resolve().parent.parent / "core" / "trade_module.py"
        src = trade_path.read_text(encoding="utf-8")
        tree = ast.parse(src)
        method_text = ""
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "_apply_tradeattr":
                end = getattr(node, "end_lineno", node.lineno) or node.lineno
                method_text = "\n".join(src.splitlines()[node.lineno - 1: end])
                break
        assert "_TRADEATTR_FIELD_MAP" in method_text, \
            "_apply_tradeattr 应查 _TRADEATTR_FIELD_MAP 表（变更 E 表驱动）"
        assert "_TRADEATTR_TARGET_KEYS" in method_text, \
            "_apply_tradeattr 应引用 _TRADEATTR_TARGET_KEYS（变更 E 表驱动）"
