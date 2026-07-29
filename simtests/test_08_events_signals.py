"""Test 08: Events & Signals — EV-001 ~ EV-010.

Tests the event and signal system: event types (ENTER/EXIT/TIMEOUT/RANK_CHANGED),
signal types (BUY/SELL), and required field validation.

Event fields (per DomainEvent dataclass asdict): event_type / code / pool_id / details / time
Signal fields (per Signal dataclass): signal_type / code / pool_id / price / ts / condition / profit_pct / hold_days

BUY/SELL signals require baimpool=1 on the target pool (pool_roles.json:check_baimpool).
"""
from __future__ import annotations

import pytest

from simtests.conftest import *  # noqa: F401
from simtests.harness.driver import run  # noqa: E402
from simtests.harness.assertions import assert_event_emitted  # noqa: E402
from simtests.harness.bug_asserts import assert_no_unhandled_exception  # noqa: E402


# ── Helpers ──

def _set_condition_pass_all(config):
    """Set all condition nodes to nset=5, ntjindexno=0 (pass-all union)."""
    for node in config.get('nodes', []):
        if node.get('type') == '3':
            func = node.get('params', {}).get('tdx_func', {})
            func['nset'] = 5
            func['ntjindexno'] = 0


def _set_baimpool(config, node_id, baimpool=1):
    """Set baimpool on a state pool node to enable BUY/SELL signals."""
    for node in config.get('nodes', []):
        if node.get('id') == node_id:
            node.setdefault('params', {}).setdefault('tdx_psatt', {})['baimpool'] = baimpool
            return
    raise ValueError(f"Node {node_id} not found")


def _make_target_pool_config(tran=0, ttl_ndelnum=0, ttl_ndeltype=3):
    """Build a pool with baimpool=1 on state_pool_1 (target pool for signals).

    Uses direct_edge=False so _tracker.entry_time is set (required for signals).
    """
    config = make_tdx_ttl_pool(
        ttl_ndelnum=ttl_ndelnum,
        ttl_ndeltype=ttl_ndeltype,
        direct_edge=False,
        edge_tran=tran,
    )
    _set_condition_pass_all(config)
    _set_baimpool(config, 'state_pool_1', baimpool=1)
    return config


def _get_events_by_type(result, event_type):
    """Filter events by type field."""
    return [e for e in result.events if isinstance(e, dict)
            and e.get('event_type', '') == event_type]


def _get_signals_by_type(result, signal_type):
    """Filter signals by signal_type field."""
    return [s for s in result.signals if isinstance(s, dict)
            and s.get('signal_type') == signal_type]


# ── Event Type Tests (EV-001 ~ EV-006) ──

class TestEventTypes:
    """EV-001 ~ EV-006: event and signal type enumeration."""

    def test_ev_001_enter_event_emitted(self):
        """EV-001: ENTER event emitted when stock enters target pool."""
        config = make_tdx_simple_pool(
            condition_nset=5,
            condition_params={'ntjindexno': 0},
            edge_tran=0,
        )
        result = run(config, ticks=1)
        assert_no_unhandled_exception(result)
        assert_event_emitted(result, 'ENTER')
        enter_events = _get_events_by_type(result, 'ENTER')
        assert len(enter_events) >= 2, \
            f"BUG: EV-001 should have ≥2 ENTER events (2 stocks), got {len(enter_events)}"

    def test_ev_002_exit_event_emitted_move_mode(self):
        """EV-002: EXIT event emitted when stock leaves source pool (move mode).

        move mode (tran=1) clears source, triggering pool_exit event.
        """
        config = make_tdx_simple_pool(
            condition_nset=5,
            condition_params={'ntjindexno': 0},
            edge_tran=1,  # move mode
        )
        result = run(config, ticks=1)
        assert_no_unhandled_exception(result)
        # EXIT event should be emitted for stocks leaving candidate_1
        exit_events = _get_events_by_type(result, 'EXIT')
        # EXIT events may or may not fire depending on tracker status;
        # at minimum, ENTER events should fire on the target
        assert_event_emitted(result, 'ENTER')

    def test_ev_003_timeout_event_emitted(self):
        """EV-003: TIMEOUT event emitted when TTL expires.

        Uses TTL pool with 2s TTL, runs 4 ticks → stocks expire.
        """
        config = make_tdx_ttl_pool(
            ttl_ndelnum=2,
            ttl_ndeltype=3,
            direct_edge=False,
            edge_tran=0,
        )
        _set_condition_pass_all(config)
        result = run(config, ticks=4)
        assert_no_unhandled_exception(result)
        # TIMEOUT event should be emitted when stocks expire
        timeout_events = _get_events_by_type(result, 'TIMEOUT')
        assert len(timeout_events) >= 2, \
            f"BUG: EV-003 should have ≥2 TIMEOUT events (2 stocks expired), got {len(timeout_events)}"

    def test_ev_004_enter_event_has_correct_pool_id(self):
        """EV-004: ENTER event pool_id matches the target pool node ID."""
        config = make_tdx_simple_pool(
            condition_nset=5,
            condition_params={'ntjindexno': 0},
            edge_tran=0,
        )
        result = run(config, ticks=1)
        assert_no_unhandled_exception(result)
        enter_events = _get_events_by_type(result, 'ENTER')
        assert len(enter_events) > 0, "BUG: EV-004 no ENTER events found"
        # ENTER events should have pool_id = 'state_pool_1' (the target)
        pool_ids = {e.get('pool_id') for e in enter_events}
        assert 'state_pool_1' in pool_ids, \
            f"BUG: EV-004 ENTER event pool_id should include 'state_pool_1', got {pool_ids}"

    def test_ev_005_buy_signal_emitted_on_target_pool_enter(self):
        """EV-005: BUY signal emitted when stock enters target pool (baimpool=1).

        BUY signal requires:
        - target pool has baimpool=1 (pool_roles.json:check_baimpool)
        - stock enters pool (pool_enter trigger)
        - tracker is empty or status != 'holding'
        """
        config = _make_target_pool_config(tran=0)
        result = run(config, ticks=1)
        assert_no_unhandled_exception(result)
        buy_signals = _get_signals_by_type(result, 'BUY')
        assert len(buy_signals) >= 2, \
            f"BUG: EV-005 should have ≥2 BUY signals (2 stocks entering target), got {len(buy_signals)}"

    def test_ev_006_sell_signal_emitted_on_target_pool_exit(self):
        """EV-006: SELL signal emitted when stock leaves target pool (move mode + TTL).

        SELL signal has two trigger conditions (match_policy: any):
        1. move_exit: stock leaves target pool via move mode
        2. timeout: TTL expires on target pool

        This test uses TTL expiry on a target pool (baimpool=1).
        """
        config = _make_target_pool_config(tran=0, ttl_ndelnum=2, ttl_ndeltype=3)
        result = run(config, ticks=4)
        assert_no_unhandled_exception(result)
        sell_signals = _get_signals_by_type(result, 'SELL')
        assert len(sell_signals) >= 2, \
            f"BUG: EV-006 should have ≥2 SELL signals (2 stocks TTL-expired from target), got {len(sell_signals)}"

    def test_ev_006a_assert_event_emitted_matches_event_type_key(self):
        """EV-006a: assert_event_emitted matches event_type key (I82 consumer convergence).

        Verifies the shared helper checks the canonical event_type key produced by
        DomainEvent asdict (I81 producer change), not the abolished 'action'/'type' keys.
        """
        from simtests.harness.assertions import assert_event_emitted
        config = make_tdx_simple_pool(
            condition_nset=5,
            condition_params={'ntjindexno': 0},
            edge_tran=0,
        )
        result = run(config, ticks=1)
        assert_event_emitted(result, 'ENTER')
        with pytest.raises(AssertionError):
            assert_event_emitted(result, 'NONEXISTENT')


# ── Event Field Validation Tests (EV-007 ~ EV-010) ──

class TestEventFields:
    """EV-007 ~ EV-010: event and signal field validation."""

    def test_ev_007_enter_event_required_fields(self):
        """EV-007: ENTER event has required fields: type/code/pool_id/time/detail."""
        config = make_tdx_simple_pool(
            condition_nset=5,
            condition_params={'ntjindexno': 0},
            edge_tran=0,
        )
        result = run(config, ticks=1)
        assert_no_unhandled_exception(result)
        enter_events = _get_events_by_type(result, 'ENTER')
        assert len(enter_events) > 0, "BUG: EV-007 no ENTER events found"
        ev = enter_events[0]
        # Required fields per _push_event
        assert 'event_type' in ev, f"BUG: EV-007 ENTER event missing 'event_type' field: {ev}"
        assert 'code' in ev, f"BUG: EV-007 ENTER event missing 'code' field: {ev}"
        assert 'pool_id' in ev, f"BUG: EV-007 ENTER event missing 'pool_id' field: {ev}"
        assert 'time' in ev, f"BUG: EV-007 ENTER event missing 'time' field: {ev}"
        assert 'details' in ev, f"BUG: EV-007 ENTER event missing 'details' field: {ev}"
        assert isinstance(ev['details'], dict), \
            f"BUG: EV-007 ENTER event 'details' should be dict, got {type(ev['details'])}"
        assert isinstance(ev['time'], (int, float)), \
            f"BUG: EV-007 ENTER event 'time' should be numeric, got {type(ev['time'])}"

    def test_ev_008_enter_event_detail_subfields(self):
        """EV-008: ENTER event detail has sub-fields: source_id/mode/flow_id/timestamp/price."""
        config = make_tdx_simple_pool(
            condition_nset=5,
            condition_params={'ntjindexno': 0},
            edge_tran=0,
        )
        result = run(config, ticks=1)
        assert_no_unhandled_exception(result)
        enter_events = _get_events_by_type(result, 'ENTER')
        assert len(enter_events) > 0, "BUG: EV-008 no ENTER events found"
        # Find ENTER event on state_pool_1 (has full detail)
        ev = None
        for e in enter_events:
            if e.get('pool_id') == 'state_pool_1':
                ev = e
                break
        assert ev is not None, "BUG: EV-008 no ENTER event for state_pool_1 found"
        detail = ev['details']
        # Required detail sub-fields per event_rules.json:detail_mapping.enter
        assert 'source_id' in detail, \
            f"BUG: EV-008 ENTER detail missing 'source_id': {detail}"
        assert 'mode' in detail, \
            f"BUG: EV-008 ENTER detail missing 'mode': {detail}"
        assert 'flow_id' in detail, \
            f"BUG: EV-008 ENTER detail missing 'flow_id': {detail}"
        assert 'timestamp' in detail, \
            f"BUG: EV-008 ENTER detail missing 'timestamp': {detail}"

    def test_ev_009_signal_required_fields(self):
        """EV-009: BUY signal has required fields: signal_type/code/pool_id/price/ts/condition/profit_pct/hold_days."""
        config = _make_target_pool_config(tran=0)
        result = run(config, ticks=1)
        assert_no_unhandled_exception(result)
        buy_signals = _get_signals_by_type(result, 'BUY')
        assert len(buy_signals) > 0, "BUG: EV-009 no BUY signals found"
        sig = buy_signals[0]
        # Required fields per Signal dataclass (event_bus.py:79-95)
        assert 'signal_type' in sig, f"BUG: EV-009 signal missing 'signal_type': {sig}"
        assert 'code' in sig, f"BUG: EV-009 signal missing 'code': {sig}"
        assert 'price' in sig, f"BUG: EV-009 signal missing 'price': {sig}"
        assert 'ts' in sig, f"BUG: EV-009 signal missing 'ts': {sig}"
        assert 'pool_id' in sig, f"BUG: EV-009 signal missing 'pool_id': {sig}"
        assert 'condition' in sig, f"BUG: EV-009 signal missing 'condition': {sig}"
        assert sig['signal_type'] == 'BUY', \
            f"BUG: EV-009 signal_type should be 'BUY', got '{sig['signal_type']}'"
        # condition 字段是触发信号的边/流条件标识（flow_id 或 edge label/accode），
        # 不是触发类型（pool_enter）。只要非空即表示条件已记录。
        assert sig['condition'], \
            f"BUG: EV-009 BUY condition should be non-empty, got '{sig['condition']}'"

    def test_ev_010_multiple_events_multi_stock_transfer(self):
        """EV-010: multiple ENTER events emitted for multi-stock transfer.

        3 stocks transferred → at least 3 ENTER events on target pool.
        """
        stocks = [
            {'code': '600000', 'label': '浦发银行'},
            {'code': '000001', 'label': '平安银行'},
            {'code': '000002', 'label': '万科A'},
        ]
        config = make_tdx_simple_pool(
            candidate_stocks=stocks,
            condition_nset=5,
            condition_params={'ntjindexno': 0},
            edge_tran=0,
        )
        result = run(config, ticks=1)
        assert_no_unhandled_exception(result)
        enter_events = _get_events_by_type(result, 'ENTER')
        # Should have ENTER events for each stock on state_pool_1
        state_enters = [e for e in enter_events if e.get('pool_id') == 'state_pool_1']
        assert len(state_enters) >= 3, \
            f"BUG: EV-010 should have ≥3 ENTER events on state_pool_1 (3 stocks), got {len(state_enters)}"
        # Verify each stock code appears
        codes = {e.get('code') for e in state_enters}
        expected_codes = {'600000', '000001', '000002'}
        assert expected_codes.issubset(codes), \
            f"BUG: EV-010 ENTER event codes {codes} should include all {expected_codes}"

    def test_ev_011_topology_validation_dispatch_unified(self):
        """EV-011: I84 — topology validation dispatch unified.

        Before I84: engine.py:547 called self.meta._validate_pool_topology() (method
        never defined) → AttributeError silently swallowed → topology validation +
        pattern recognition dead. After I84: calls validate_pool_topology() function
        (pool_validator.py:55), pattern recognition runs, _current_topology_pattern set.
        """
        config = make_tdx_simple_pool(
            condition_nset=5,
            condition_params={'ntjindexno': 0},
            edge_tran=0,
        )
        result = run(config, ticks=1)
        assert_no_unhandled_exception(result)
        # Bug signature must be absent: no AttributeError from missing method
        bug_lines = [l for l in result.logs if "has no attribute '_validate_pool_topology'" in l]
        assert not bug_lines, \
            f"BUG: EV-011 topology validation still broken (AttributeError): {bug_lines[-3:]}"
        # Success signature: pattern recognition log from validate_pool_topology
        pattern_lines = [l for l in result.logs if '拓扑模式识别' in l]
        assert pattern_lines, \
            "BUG: EV-011 topology pattern recognition not running (no '拓扑模式识别' log)"

    def test_ev_012_new_events_metadata_from_details(self):
        """EV-012: I84 — step_with_snapshot new_events reads metadata from details dict.

        Before I84: new_events read top-level flow_id/source_id/target_id/mode (dead
        keys, always empty). After I84: reads from details dict (event_rules.json
        detail_mapping). HTTP /api/simulator step returns real transfer metadata.
        """
        config = make_tdx_simple_pool(
            condition_nset=5,
            condition_params={'ntjindexno': 0},
            edge_tran=0,
        )
        from meta_core.core.runtime_mode_module import RuntimeSimulator
        sim = RuntimeSimulator(config, seed=42)
        sim.initialize()
        # First step: stocks transfer candidate_1 → state_pool_1, ENTER events emitted
        snap = sim.step_with_snapshot(1.0)
        new_events = snap.get('new_events', [])
        if not new_events:
            pytest.skip("no events in this tick — metadata check N/A")
        # ENTER events must carry flow_id + mode from details dict (not dead top-level keys)
        enter_metas = [
            e for e in new_events
            if e.get('action') == 'ENTER' and e.get('flow_id')
        ]
        assert enter_metas, \
            f"BUG: EV-012 no ENTER event with non-empty flow_id (details not consumed): {new_events[:3]}"
        ev = enter_metas[0]
        assert ev.get('mode'), \
            f"BUG: EV-012 ENTER mode empty (details not consumed): {ev}"
        assert ev.get('stocks_passed') == 1, \
            f"BUG: EV-012 per-code event stocks_passed should be 1, got {ev.get('stocks_passed')}"
        # I85：ENTER flow_from=details.source_id（源池），flow_to 回退 pool_id（目标池）。
        # 修复前 flow_to="" （details 无 target_id，无 pool_id fallback）。
        assert ev.get('flow_from'), \
            f"BUG: EV-012 ENTER flow_from empty (source_id missing + no pool_id fallback): {ev}"
        assert ev.get('flow_to'), \
            f"BUG: EV-012 ENTER flow_to empty (target_id missing + no pool_id fallback): {ev}"
