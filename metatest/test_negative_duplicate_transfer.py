# -*- coding: utf-8 -*-
"""Task 20.1: duplicate transfer negative tests.

System should gracefully handle duplicate stock transfers (adding same
stock twice, removing non-existent stock, duplicate TransferExecuted
events) without crash.
"""
from __future__ import annotations
from typing import Any, List
import pytest
from core.event_bus import EventBus, TransferExecuted
from core.runtime_mode_module import PoolState

def _basic_cfg():
    return {"id": "dt1", "name": "dup_transfer",
            "nodes": [
                {"id": "src", "type": "statepool", "name": "src", "params": {}},
                {"id": "dst", "type": "statepool", "name": "dst", "params": {}}],
            "edges": []}

class TestDuplicateAdd:
    def test_add_same_code_twice(self):
        s = PoolState(pool_config=_basic_cfg())
        v = s.get_pool("dst")
        v.add_stocks(["fz000001"])
        v.add_stocks(["fz000001"])
        codes = v.get_stock_codes()
        assert "fz000001" in codes
        assert len(codes) == 1

    def test_add_list_with_duplicates(self):
        s = PoolState(pool_config=_basic_cfg())
        v = s.get_pool("dst")
        v.add_stocks(["fz000001", "fz000001", "fz000002"])
        codes = v.get_stock_codes()
        assert len(codes) == 2

    def test_add_empty_list(self):
        s = PoolState(pool_config=_basic_cfg())
        v = s.get_pool("dst")
        v.add_stocks([])
        assert v.get_stock_codes() == set()

class TestRemoveNonExistent:
    def test_remove_nonexistent_no_crash(self):
        s = PoolState(pool_config=_basic_cfg())
        v = s.get_pool("dst")
        try:
            v.remove_stocks(["fz999999"])
        except Exception as exc:
            pytest.fail("remove non-existent raised: " + str(exc))
        assert v.get_stock_codes() == set()

    def test_remove_from_empty_pool(self):
        s = PoolState(pool_config=_basic_cfg())
        v = s.get_pool("dst")
        v.remove_stocks(["fz000001"])
        assert v.get_stock_codes() == set()

    def test_remove_then_add(self):
        s = PoolState(pool_config=_basic_cfg())
        v = s.get_pool("dst")
        v.add_stocks(["fz000001"])
        v.remove_stocks(["fz000001"])
        v.add_stocks(["fz000001"])
        assert "fz000001" in v.get_stock_codes()

class TestDuplicateTransferEvent:
    def test_duplicate_transfer_executed_no_crash(self):
        bus = EventBus()
        collected: List[Any] = []
        bus.subscribe_any(lambda e: collected.append(e))
        ev = TransferExecuted(src="src", tgt="dst", codes=["fz000001"], mode="move", ts=34500.0)
        bus.publish(ev)
        bus.publish(ev)
        assert len(collected) == 2

    def test_transfer_empty_codes(self):
        bus = EventBus()
        ev = TransferExecuted(src="src", tgt="dst", codes=[], mode="move", ts=34500.0)
        try:
            bus.publish(ev)
        except Exception as exc:
            pytest.fail("transfer empty codes raised: " + str(exc))

    def test_transfer_nonexistent_pool(self):
        s = PoolState(pool_config=_basic_cfg())
        v = s.get_pool("nonexistent")
        v.add_stocks(["fz000001"])
        assert "fz000001" in v.get_stock_codes()
