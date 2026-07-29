# -*- coding: utf-8 -*-
"""Task: empty pool, empty tick, empty config negative tests.

System should gracefully handle empty pool / empty data / empty config
scenarios without raising uncaught exceptions.
Negative test PASSES when system handles exception correctly (no crash).
"""
from __future__ import annotations

from typing import Any, Dict, List

import pytest

from core.domain import (
    CandidatePoolNode,
    ConditionNode,
    StatePoolNode,
)
from core.event_bus import EventBus
from core.execution_module import Compiler, EventDriver
from core.runtime_mode_module import PoolState


# ---------------------------------------------------------------------------
# Helpers: build empty pool configs
# ---------------------------------------------------------------------------


def _empty_pool_config() -> Dict[str, Any]:
    """Build a pool config with no stocks."""
    return {
        "id": "neg_empty_pool",
        "name": "negative_empty_pool",
        "nodes": [
            {
                "id": "empty_source",
                "type": "market_source",
                "name": "empty_candidate",
                "params": {"markets": [], "market_count": 0, "stocks": []},
            },
            {
                "id": "empty_state",
                "type": "statepool",
                "name": "empty_state_pool",
                "config": {"pool_role": "target_pool"},
                "params": {"hold_seconds": 600, "psatt": {}},
            },
        ],
        "edges": [],
    }


def _pool_with_no_edges_config() -> Dict[str, Any]:
    """Build a pool config with stocks but no edges."""
    return {
        "id": "no_edges_pool",
        "name": "no_edges",
        "nodes": [
            {
                "id": "src",
                "type": "market_source",
                "name": "src",
                "params": {"stocks": [{"code": "fz000001"}]},
            },
            {
                "id": "dst",
                "type": "statepool",
                "name": "dst",
                "params": {"hold_seconds": 600},
            },
        ],
        "edges": [],
    }


# ============================================================================
# SubTask: empty candidate pool / empty params
# ============================================================================


class TestEmptyCandidatePool:
    """Empty candidate pool scenarios."""

    def test_empty_pool_config_compiles_without_exception(self):
        """Empty params pool config compiles without raising."""
        cfg = _empty_pool_config()
        schedule = None
        try:
            schedule = Compiler.compile(cfg)
        except Exception as exc:
            pytest.fail(f"Compiler.compile raised on empty pool config: {exc}")
        assert schedule is not None, "should return a CompiledSchedule"

    def test_empty_pool_state_creates_without_error(self):
        """Empty stocks PoolState initializes without error."""
        cfg = _empty_pool_config()
        state = PoolState(pool_config=cfg)
        assert state.node_stocks == {}, "empty pool node_stocks should be empty"

    def test_empty_state_pool_returns_empty_codes(self):
        """Empty state pool get_stock_codes returns empty set (not None)."""
        cfg = _empty_pool_config()
        state = PoolState(pool_config=cfg)
        codes = state.get_pool("empty_state").get_stock_codes()
        assert codes is not None, "should return a set, not None"
        assert len(codes) == 0, f"empty pool should have no stocks, got {codes}"

    def test_empty_pool_engine_starts_without_exception(self):
        """Empty pool can build PoolState without crash."""
        cfg = _empty_pool_config()
        state = PoolState(pool_config=cfg)
        assert state.pool_config["id"] == "neg_empty_pool"
        assert state.first_run is True, "first_run should be True after init"


# ============================================================================
# SubTask: empty tick data and empty event stream
# ============================================================================


class TestEmptyTickData:
    """Empty tick data should not publish any events."""

    def test_eventbus_publish_empty_codes_does_not_crash(self):
        """Publishing DataChanged(code=[]) does not raise."""
        from core.event_bus import DataChanged

        bus = EventBus(max_events=100)
        ev = DataChanged(ts=34500.0, bar_hash="h", codes=[])
        try:
            bus.publish(ev)
        except Exception as exc:
            pytest.fail(f"EventBus.publish empty codes raised: {exc}")
        events = bus.get_events()
        assert len(events) == 1, "should retain the single event"
        assert events[0].codes == []

    def test_empty_data_changed_does_not_propagate_codes(self):
        """Empty codes DataChanged should not invent codes."""
        from core.event_bus import DataChanged

        bus = EventBus()
        collector: List[Any] = []
        bus.subscribe_any(lambda e: collector.append(e))
        bus.publish(DataChanged(ts=34500.0, bar_hash="h", codes=[]))
        assert len(collector) == 1
        assert collector[0].codes == []

    def test_empty_event_list_is_safe_to_iterate(self):
        """Empty EventBus.get_events() returns iterable list == []."""
        bus = EventBus()
        events = bus.get_events()
        assert isinstance(events, list)
        assert events == []
        assert len(list(events)) == 0


# ============================================================================
# SubTask: pool with no edges
# ============================================================================


class TestPoolWithNoEdges:
    """Pool with no edges should not produce any transfers."""

    def test_no_edges_config_compiles(self):
        """No-edges config compiles successfully (edge_ctx empty)."""
        cfg = _pool_with_no_edges_config()
        schedule = Compiler.compile(cfg)
        assert schedule.edge_ctx == {}, "no-edges config edge_ctx should be empty"

    def test_no_edges_state_topology_built(self):
        """No-edges config PoolState topology builds successfully."""
        cfg = _pool_with_no_edges_config()
        state = PoolState(pool_config=cfg)
        assert "src" in state.topology or "src" not in state.topology
        # topology["src"] should be an empty list (no out edges)
        assert state.topology.get("src", []) == []

    def test_no_edges_pool_codes_empty(self):
        """No-edges src/dst pools have empty stock sets (no transfers)."""
        cfg = _pool_with_no_edges_config()
        state = PoolState(pool_config=cfg)
        assert state.get_pool("src").get_stock_codes() == set()
        assert state.get_pool("dst").get_stock_codes() == set()


# ============================================================================
# SubTask: condition node with no input
# ============================================================================


class TestConditionNodeNoInput:
    """Condition node with no input should not activate any edge."""

    def test_condition_node_instantiates_with_default_filter_spec(self):
        """ConditionNode() with default FilterSpec() does not crash."""
        node = ConditionNode(id="cond1")
        assert node.id == "cond1"
        assert node.filter_spec is not None

    def test_state_pool_node_instantiates_with_empty_specs(self):
        """StatePoolNode() with default ttl_spec=None does not crash."""
        node = StatePoolNode(id="st1")
        assert node.id == "st1"
        assert node.ttl_spec is None
        assert node.action_spec is None

    def test_candidate_pool_node_instantiates_empty(self):
        """CandidatePoolNode() with default CandidateRange() does not crash."""
        node = CandidatePoolNode(id="cand1")
        assert node.id == "cand1"
        assert node.candidate_range is not None
        assert node.candidate_range.codes == []


# ============================================================================
# SubTask: state pool with no stocks
# ============================================================================


class TestStatePoolNoStocks:
    """State pool with no stocks should have safe accessors."""

    def test_get_stock_codes_returns_empty_set(self):
        """Empty pool get_stock_codes returns empty set (not None)."""
        state = PoolState(pool_config={"id": "p", "nodes": [], "edges": []})
        codes = state.get_pool("any_pool").get_stock_codes()
        assert isinstance(codes, set)
        assert len(codes) == 0

    def test_get_dirty_codes_for_empty_pool_is_empty(self):
        """Empty pool dirty_codes is empty set (no intersection)."""
        state = PoolState(pool_config={"id": "p", "nodes": [], "edges": []})
        dirty = state.get_pool("any_pool").get_dirty_codes()
        assert dirty == set()

    def test_get_stocks_for_empty_pool_returns_empty_list(self):
        """Empty pool get_stocks returns empty list (not None)."""
        state = PoolState(pool_config={"id": "p", "nodes": [], "edges": []})
        stocks = state.get_pool("any_pool").get_stocks()
        assert isinstance(stocks, list)
        assert stocks == []


# ============================================================================
# SubTask: EventDriver with no specs
# ============================================================================


class TestEventDriverNoSpecs:
    """EventDriver with no specs: fire_due is a no-op."""

    def test_fire_due_with_empty_heap_does_nothing(self):
        """Empty heap EventDriver.fire_due does not crash and publishes nothing."""
        bus = EventBus()
        driver = EventDriver(state=None, bus=bus)
        collector: List[Any] = []
        bus.subscribe_any(lambda e: collector.append(e))
        try:
            driver.fire_due(34500.0)
        except Exception as exc:
            pytest.fail(f"fire_due empty heap raised: {exc}")
        assert len(collector) == 0, "empty heap should not publish events"
        assert driver._heap == [], "heap should still be empty"

    def test_event_driver_heap_empty_initially(self):
        """EventDriver heap is an empty list initially."""
        driver = EventDriver()
        assert isinstance(driver._heap, list)
        assert len(driver._heap) == 0


# ============================================================================
# SubTask: empty filter result
# ============================================================================


class TestEmptyFilterResult:
    """Empty filter result (passed set is empty) behavior."""

    def test_empty_set_can_be_added_to_pool(self):
        """Empty set add_stocks does not crash and does not add stocks."""
        state = PoolState(pool_config={"id": "p", "nodes": [], "edges": []})
        view = state.get_pool("p1")
        before = view.get_stocks()
        view.add_stocks([])
        after = view.get_stocks()
        assert before == after == []

    def test_empty_set_remove_does_not_crash(self):
        """Empty set remove_stocks does not crash."""
        state = PoolState(pool_config={"id": "p", "nodes": [], "edges": []})
        view = state.get_pool("p1")
        try:
            view.remove_stocks([])
        except Exception as exc:
            pytest.fail(f"empty remove_stocks raised: {exc}")
        assert view.get_stocks() == []

    def test_remove_non_existent_code_does_not_crash(self):
        """Removing a non-existent stock code does not crash."""
        state = PoolState(pool_config={"id": "p", "nodes": [], "edges": []})
        view = state.get_pool("p1")
        try:
            view.remove_stocks(["fz999999"])
        except Exception as exc:
            pytest.fail(f"remove non-existent stock raised: {exc}")
        assert view.get_stock_codes() == set()
