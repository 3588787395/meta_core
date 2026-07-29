# -*- coding: utf-8 -*-
"""Task 20.2: TTL with no position negative tests.

System should gracefully handle TTL expiration when there is no position
to sell, selling non-existent stocks, and TTL events for empty pools.
"""
from __future__ import annotations
from typing import Any, List
import pytest
from core.event_bus import (
    EventBus, TTLDue, TransferExecuted, OrderFilled, PositionUpdated,
)
from core.runtime_mode_module import PoolState
from core.trade_module import TradeModule, _Position

def _trade_cfg():
    return {
        "auto_buy_pools": ["pool_C"],
        "trade_interface": "paper_trade",
        "initial_capital": 1000000.0,
        "default_quantity": 100,
    }

class TestTTLNoPosition:
    def test_ttl_due_no_crash(self):
        bus = EventBus()
        trade = TradeModule(bus, config=_trade_cfg())
        ev = TTLDue(node_id="pool_C", code="fz000001", ts=99999.0)
        try:
            bus.publish(ev)
        except Exception as exc:
            pytest.fail("TTLDue with no position raised: " + str(exc))

    def test_ttl_due_empty_codes(self):
        bus = EventBus()
        trade = TradeModule(bus, config=_trade_cfg())
        ev = TTLDue(node_id="pool_C", code="", ts=99999.0)
        try:
            bus.publish(ev)
        except Exception as exc:
            pytest.fail("TTLDue empty codes raised: " + str(exc))

    def test_ttl_due_nonexistent_pool(self):
        bus = EventBus()
        trade = TradeModule(bus, config=_trade_cfg())
        ev = TTLDue(node_id="nonexistent", code="fz000001", ts=99999.0)
        try:
            bus.publish(ev)
        except Exception as exc:
            pytest.fail("TTLDue nonexistent pool raised: " + str(exc))

class TestSellNoPosition:
    def test_sell_without_buying(self):
        bus = EventBus()
        trade = TradeModule(bus, config=_trade_cfg())
        ev = TTLDue(node_id="pool_C", code="fz000001", ts=99999.0)
        bus.publish(ev)
        assert trade is not None

    def test_sell_nonexistent_stock(self):
        bus = EventBus()
        trade = TradeModule(bus, config=_trade_cfg())
        ev = TTLDue(node_id="pool_C", code="fz999999", ts=99999.0)
        try:
            bus.publish(ev)
        except Exception as exc:
            pytest.fail("sell nonexistent stock raised: " + str(exc))

    def test_position_default_empty(self):
        pos = _Position(code="fz000001", entry_price=0.0, entry_time=0.0, quantity=0)
        assert pos.quantity == 0
        assert pos.code == "fz000001"

class TestTTLWithEmptyPool:
    def test_ttl_on_empty_pool_no_crash(self):
        bus = EventBus()
        trade = TradeModule(bus, config=_trade_cfg())
        collected: List[Any] = []
        bus.subscribe_any(lambda e: collected.append(e))
        bus.publish(TTLDue(node_id="pool_C", code="", ts=34500.0))
        ttl_events = [e for e in collected if isinstance(e, TTLDue)]
        assert len(ttl_events) == 1

    def test_multiple_ttl_same_stock(self):
        bus = EventBus()
        trade = TradeModule(bus, config=_trade_cfg())
        ev = TTLDue(node_id="pool_C", code="fz000001", ts=34500.0)
        try:
            bus.publish(ev)
            bus.publish(ev)
        except Exception as exc:
            pytest.fail("double TTL raised: " + str(exc))

    def test_ttl_then_transfer_out(self):
        bus = EventBus()
        trade = TradeModule(bus, config=_trade_cfg())
        bus.publish(TransferExecuted(
            src="pool_C", tgt="pool_D", codes=["fz000001"], mode="move", ts=34600.0))
        bus.publish(TTLDue(node_id="pool_C", code="fz000001", ts=99999.0))
        assert trade is not None
