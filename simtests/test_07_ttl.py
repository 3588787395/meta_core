"""Test 07: TTL Expiry System — TTL-001 ~ TTL-011.

Tests the TTL (Time-To-Live) auto-delete system for state pools.

Covers:
  - ndeltype 0~3 unit conversion (天/小时/分钟/秒)
  - bdel switch (1=enabled / 0=disabled)
  - indate/intime parsing
  - TTL boundary conditions (exactly at, just before, just after)
  - TTL interaction with flow modes (move/copy)
"""
from __future__ import annotations

from datetime import datetime as _dt

from meta_core.core.engine import PoolEngine, _safe_timestamp
from simtests.harness.clock import set_engine_clock

from simtests.conftest import *  # noqa: F401,F403
from simtests.harness.driver import run  # noqa: F401
from simtests.harness.assertions import assert_pool_state  # noqa: F401
from simtests.harness.bug_asserts import assert_no_unhandled_exception  # noqa: F401


# ── Helpers ──

def _set_condition_pass_all(config):
    """Set all condition nodes to nset=5, ntjindexno=0 (pass-all union)."""
    for node in config.get('nodes', []):
        if node.get('type') == '3':
            func = node.get('params', {}).get('tdx_func', {})
            func['nset'] = 5
            func['ntjindexno'] = 0


def _set_psatt(config, node_id, **kwargs):
    """Set tdx_psatt fields on a specific node."""
    for node in config.get('nodes', []):
        if node.get('id') == node_id:
            node.setdefault('params', {}).setdefault('tdx_psatt', {}).update(kwargs)
            return
    raise ValueError(f"Node {node_id} not found")


def _make_engine_with_clock(clock=34500.0):
    """Create a fresh engine with virtual time source anchored to ``clock``."""
    eng = PoolEngine()
    set_engine_clock(eng, clock)
    return eng


def _make_ttl_node(node_id='state_pool_1', bdel=1, ndelnum=2, ndeltype=3):
    """Create a state pool node with TTL config."""
    return {
        'id': node_id,
        'type': '8',
        'dzh_cell_type': 8,
        'text': 'TTL池',
        'attr': 0,
        'pos': '0,0,200,100',
        'params': {
            'tdx_psatt': {
                'bdel': bdel,
                'ndelnum': ndelnum,
                'ndeltype': ndeltype,
                'baimpool': 0,
                'bsound': 0, 'nsoundtype': 0, 'nsyssound': 0, 'soundfile': '',
                'btip': 0, 'bsavetoblock': 0, 'blockfile': '', 'bclearblock': 0, 'bsavehis': 0,
            },
            'stocks': [],
        },
    }


def _make_stock_with_tracker(code, entry_time):
    """Create a stock dict with _tracker.entry_time set.

    entry_time must be a real Unix timestamp (as produced by
    _safe_timestamp(engine._now())), NOT a raw virtual_clock value.
    """
    return {
        'code': code,
        'label': code,
        '_tracker': {'entry_time': float(entry_time)},
    }


def _entry_ts(eng, clock):
    """Compute the real timestamp for a virtual_clock value via the engine's _now()."""
    set_engine_clock(eng, clock)
    return _safe_timestamp(eng._now())


# ── TTL Unit Conversion Tests (TTL-001 ~ TTL-005) ──

class TestTTLUnitConversion:
    """TTL-001 ~ TTL-005: ndeltype unit conversion (天/小时/分钟/秒)."""

    def test_ttl_001_ndeltype3_seconds_expiry(self):
        """TTL-001: ndeltype=3 (秒), ndelnum=2 → expires after 2 seconds.

        Integration test: stocks enter at tick 0 (clock=34501),
        TTL=2s, should expire by tick 2 (clock=34503, elapsed=2s >= 2s).
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
        assert len(result.node_stocks.get('state_pool_1', [])) == 0, \
            "BUG: TTL-001 state_pool_1 should be empty after 4 ticks (2s TTL)"

    def test_ttl_002_ndeltype3_seconds_no_expiry_within_window(self):
        """TTL-002: ndeltype=3 (秒), ndelnum=5 → does NOT expire within 3 ticks.

        Integration test: stocks enter at tick 0 (clock=34501),
        TTL=5s, after 3 ticks (clock=34504, elapsed=3s < 5s) → not expired.
        """
        config = make_tdx_ttl_pool(
            ttl_ndelnum=5,
            ttl_ndeltype=3,
            direct_edge=False,
            edge_tran=0,
        )
        _set_condition_pass_all(config)
        result = run(config, ticks=3)
        assert_no_unhandled_exception(result)
        assert len(result.node_stocks.get('state_pool_1', [])) == 2, \
            "BUG: TTL-002 state_pool_1 should still have 2 stocks (5s TTL, only 3s elapsed)"

    def test_ttl_003_ndeltype2_minutes_unit_conversion(self):
        """TTL-003: ndeltype=2 (分钟), ndelnum=1 → TTL=60 seconds.

        Unit test: verify ttl_units["2"]=60, so ndelnum=1 → TTL=60s.
        Uses direct engine call to avoid running 60+ ticks.
        """
        eng = _make_engine_with_clock(clock=34500.0)
        node = _make_ttl_node(ndelnum=1, ndeltype=2)
        entry_ts = _entry_ts(eng, 34500.0)
        node_stocks = {
            'state_pool_1': [_make_stock_with_tracker('600000', entry_ts)]
        }
        # At clock=34530 (30s elapsed), TTL=60s → not expired
        set_engine_clock(eng, 34530.0)
        eng._ttl.apply_ttl('state_pool_1', node, node_stocks)
        assert len(node_stocks['state_pool_1']) == 1, \
            "BUG: TTL-003 should NOT expire at 30s (TTL=60s)"
        # At clock=34561 (61s elapsed), TTL=60s → expired
        set_engine_clock(eng, 34561.0)
        eng._ttl.apply_ttl('state_pool_1', node, node_stocks)
        assert len(node_stocks['state_pool_1']) == 0, \
            "BUG: TTL-003 should expire at 61s (TTL=60s)"

    def test_ttl_004_ndeltype1_hours_unit_conversion(self):
        """TTL-004: ndeltype=1 (小时), ndelnum=1 → TTL=3600 seconds.

        Unit test: verify ttl_units["1"]=3600, so ndelnum=1 → TTL=3600s.
        """
        eng = _make_engine_with_clock(clock=34500.0)
        node = _make_ttl_node(ndelnum=1, ndeltype=1)
        entry_ts = _entry_ts(eng, 34500.0)
        node_stocks = {
            'state_pool_1': [_make_stock_with_tracker('600000', entry_ts)]
        }
        # At clock=34500+3599 (3599s elapsed), TTL=3600s → not expired
        set_engine_clock(eng, 34500.0 + 3599)
        eng._ttl.apply_ttl('state_pool_1', node, node_stocks)
        assert len(node_stocks['state_pool_1']) == 1, \
            "BUG: TTL-004 should NOT expire at 3599s (TTL=3600s)"
        # At clock=34500+3600 (3600s elapsed), TTL=3600s → expired
        set_engine_clock(eng, 34500.0 + 3600)
        eng._ttl.apply_ttl('state_pool_1', node, node_stocks)
        assert len(node_stocks['state_pool_1']) == 0, \
            "BUG: TTL-004 should expire at 3600s (TTL=3600s)"

    def test_ttl_005_ndeltype0_days_unit_conversion(self):
        """TTL-005: ndeltype=0 (天), ndelnum=1 → TTL=86400 seconds.

        Unit test: verify ttl_units["0"]=86400, so ndelnum=1 → TTL=86400s.
        """
        eng = _make_engine_with_clock(clock=34500.0)
        node = _make_ttl_node(ndelnum=1, ndeltype=0)
        entry_ts = _entry_ts(eng, 34500.0)
        node_stocks = {
            'state_pool_1': [_make_stock_with_tracker('600000', entry_ts)]
        }
        # At clock=34500+86399 (86399s elapsed), TTL=86400s → not expired
        set_engine_clock(eng, 34500.0 + 86399)
        eng._ttl.apply_ttl('state_pool_1', node, node_stocks)
        assert len(node_stocks['state_pool_1']) == 1, \
            "BUG: TTL-005 should NOT expire at 86399s (TTL=86400s)"
        # At clock=34500+86400 (86400s elapsed), TTL=86400s → expired
        set_engine_clock(eng, 34500.0 + 86400)
        eng._ttl.apply_ttl('state_pool_1', node, node_stocks)
        assert len(node_stocks['state_pool_1']) == 0, \
            "BUG: TTL-005 should expire at 86400s (TTL=86400s)"


# ── bdel Switch Tests (TTL-006 ~ TTL-008) ──

class TestTTLBdelSwitch:
    """TTL-006 ~ TTL-008: bdel switch behavior."""

    def test_ttl_006_bdel1_enabled_ttl_fires(self):
        """TTL-006: bdel=1 → TTL enabled, stocks expire after TTL window."""
        config = make_tdx_ttl_pool(
            ttl_ndelnum=2,
            ttl_ndeltype=3,
            direct_edge=False,
            edge_tran=0,
        )
        _set_condition_pass_all(config)
        # Ensure bdel=1 (should be default from make_tdx_ttl_pool)
        _set_psatt(config, 'state_pool_1', bdel=1)
        result = run(config, ticks=4)
        assert_no_unhandled_exception(result)
        assert len(result.node_stocks.get('state_pool_1', [])) == 0, \
            "BUG: TTL-006 bdel=1 should enable TTL expiry"

    def test_ttl_007_bdel0_disabled_ttl_skipped(self):
        """TTL-007: bdel=0 → TTL disabled, stocks do NOT expire even after TTL window."""
        config = make_tdx_ttl_pool(
            ttl_ndelnum=2,
            ttl_ndeltype=3,
            direct_edge=False,
            edge_tran=0,
        )
        _set_condition_pass_all(config)
        # Disable TTL
        _set_psatt(config, 'state_pool_1', bdel=0)
        result = run(config, ticks=4)
        assert_no_unhandled_exception(result)
        assert len(result.node_stocks.get('state_pool_1', [])) == 2, \
            "BUG: TTL-007 bdel=0 should disable TTL, stocks should remain"

    def test_ttl_008_ndelnum0_no_ttl_applied(self):
        """TTL-008: ndelnum=0 → TTL not applied (no deletion).

        Even with bdel=1, ndelnum=0 means TTL=0 which should be skipped
        (the TTLHelper.apply_ttl method returns early when ndn <= 0).
        """
        config = make_tdx_ttl_pool(
            ttl_ndelnum=0,
            ttl_ndeltype=3,
            direct_edge=False,
            edge_tran=0,
        )
        _set_condition_pass_all(config)
        result = run(config, ticks=4)
        assert_no_unhandled_exception(result)
        assert len(result.node_stocks.get('state_pool_1', [])) == 2, \
            "BUG: TTL-008 ndelnum=0 should skip TTL, stocks should remain"


# ── indate/intime Parsing Tests (TTL-009 ~ TTL-010) ──

class TestTTLIndateIntime:
    """TTL-009 ~ TTL-010: indate/intime parsing and TTL boundary."""

    def test_ttl_009_indate_intime_parsing(self):
        """TTL-009: stocks with explicit indate/intime are TTL-checked.

        Uses direct engine call: stock has indate/intime set,
        eng._ttl.apply_ttl should parse and check TTL.

        virtual_clock=34500 = 09:35:00, so intime must be '93500' to match.
        """
        eng = _make_engine_with_clock(clock=34500.0)
        node = _make_ttl_node(ndelnum=2, ndeltype=3)
        # Stock with explicit indate/intime matching today's date at 09:35:00
        today_str = _dt.now().strftime('%Y%m%d')
        stock = {
            'code': '600000',
            'label': '浦发银行',
            'indate': today_str,
            'intime': '93500',  # 09:35:00 = 34500 seconds
        }
        node_stocks = {'state_pool_1': [stock]}
        # At clock=34501 (1s elapsed), TTL=2s → not expired
        set_engine_clock(eng, 34501.0)
        eng._ttl.apply_ttl('state_pool_1', node, node_stocks)
        assert len(node_stocks['state_pool_1']) == 1, \
            "BUG: TTL-009 should NOT expire at 1s (TTL=2s)"
        # At clock=34503 (3s elapsed), TTL=2s → expired
        set_engine_clock(eng, 34503.0)
        eng._ttl.apply_ttl('state_pool_1', node, node_stocks)
        assert len(node_stocks['state_pool_1']) == 0, \
            "BUG: TTL-009 should expire at 3s (TTL=2s, indate/intime parsed)"

    def test_ttl_010_boundary_exactly_at_ttl(self):
        """TTL-010: TTL boundary — exactly at TTL threshold → expired (>=).

        The TTL check uses (nts - ets) >= ttl, so at exactly TTL seconds
        the stock should be expired.
        """
        eng = _make_engine_with_clock(clock=34500.0)
        node = _make_ttl_node(ndelnum=10, ndeltype=3)  # TTL=10s
        entry_ts = _entry_ts(eng, 34500.0)
        node_stocks = {
            'state_pool_1': [_make_stock_with_tracker('600000', entry_ts)]
        }
        # At clock=34509 (9s elapsed), TTL=10s → not expired
        set_engine_clock(eng, 34509.0)
        eng._ttl.apply_ttl('state_pool_1', node, node_stocks)
        assert len(node_stocks['state_pool_1']) == 1, \
            "BUG: TTL-010 should NOT expire at 9s (TTL=10s, < threshold)"
        # At clock=34510 (10s elapsed), TTL=10s → expired (>=)
        set_engine_clock(eng, 34510.0)
        eng._ttl.apply_ttl('state_pool_1', node, node_stocks)
        assert len(node_stocks['state_pool_1']) == 0, \
            "BUG: TTL-010 should expire at exactly 10s (TTL=10s, >= threshold)"


# ── TTL + Flow Mode Interaction (TTL-011) ──

class TestTTLFlowModeInteraction:
    """TTL-011: TTL interaction with flow modes."""

    def test_ttl_011_ttl_with_move_mode(self):
        """TTL-011: TTL with move mode → stocks still expire after transfer.

        Move mode clears source, but stocks in target should still be
        subject to TTL expiry.
        """
        config = make_tdx_ttl_pool(
            ttl_ndelnum=2,
            ttl_ndeltype=3,
            direct_edge=False,
            edge_tran=1,  # move mode
        )
        _set_condition_pass_all(config)
        result = run(config, ticks=4)
        assert_no_unhandled_exception(result)
        # Source should be cleared (move mode)
        assert len(result.node_stocks.get('candidate_1', [])) == 0, \
            "BUG: TTL-011 move mode should clear source pool"
        # Target should be empty (TTL expired)
        assert len(result.node_stocks.get('state_pool_1', [])) == 0, \
            "BUG: TTL-011 TTL should expire stocks in target after move"
