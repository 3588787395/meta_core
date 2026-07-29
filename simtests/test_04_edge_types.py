"""Test 04: Edge Type System — EDGE-001 ~ EDGE-024.

Tests the edge type system: gate (starttype × cxtype) and flow modes
(copy / move / overwrite / force_move / pass_through).

Gate tests use the simulation driver for integration testing (starttype=0/6/7)
and direct engine method calls for cxtype unit testing.

Flow mode tests use the simulation driver with various tran/attr combinations.
"""
from __future__ import annotations

from meta_core.core.engine import PoolEngine

from simtests.conftest import *  # noqa: F401,F403
from simtests.harness.driver import run  # noqa: F401
from simtests.harness.assertions import assert_pool_state  # noqa: F401
from simtests.harness.bug_asserts import assert_no_unhandled_exception  # noqa: F401


# ── Helpers ──

def _set_engine_clock(engine: PoolEngine, clock: float) -> None:
    """将 PoolEngine 的 virtual 时间源固定到指定当日偏移秒数。"""
    pe = engine._pool_engine
    if pe is None:
        pe = engine._ensure_pool_engine({'id': '_simtest_clock_', 'nodes': [], 'edges': []})
    pe.state.time_source = {
        'driver_type': 'virtual',
        'current_ts': clock,
        'kind': 'simtest',
    }


def _set_condition_pass_all(config):
    """Set all condition nodes to nset=5, ntjindexno=0 (pass-all union)."""
    for node in config.get('nodes', []):
        if node.get('type') == '3':
            func = node.get('params', {}).get('tdx_func', {})
            func['nset'] = 5
            func['ntjindexno'] = 0


def _set_edge_param(config, edge_id, key, value):
    """Set a parameter on a specific edge by ID."""
    for edge in config.get('edges', []):
        if edge.get('id') == edge_id:
            edge.setdefault('params', {})[key] = value
            return
    raise ValueError(f"Edge {edge_id} not found")


def _set_all_edges_param(config, key, value):
    """Set a parameter on all edges."""
    for edge in config.get('edges', []):
        edge.setdefault('params', {})[key] = value


def _make_fanout_pool():
    """Build a fan-out pool: 1 candidate → 2 conditions → 2 state pools.

    candidate_1 ─┬─ condition_A → state_A
                  └─ condition_B → state_B

    Directly constructs nodes and edges (no rename) so edge from/to references
    are always valid. Both conditions default to nset=5 ntjindexno=0 (pass-all).
    """
    stocks = [
        {'code': '600000', 'label': '浦发银行'},
        {'code': '000001', 'label': '平安银行'},
        {'code': '000002', 'label': '万科A'},
    ]
    func = {
        'nset': 5, 'ntjindexno': 0, 'accode': '', 'nperiod': 4,
        'nfirst': 0, 'cfirst': '', 'noperate': 0, 'nsecond': -1,
        'csecond': '', 'fsecond': 0.0, 'nbeginday': 0, 'nendday': 0,
        'bnost': 0, 'bnotp': 0, 'bnotq': 0, 'nperiodnum': 0,
    }
    psatt = {
        'bdel': 0, 'ndelnum': 0, 'ndeltype': 0, 'baimpool': 0,
        'bsound': 0, 'nsoundtype': 0, 'nsyssound': 0, 'soundfile': '',
        'btip': 0, 'bsavetoblock': 0, 'blockfile': '', 'bclearblock': 0, 'bsavehis': 0,
    }
    spinfo = {'type': 0, 'customblockname': '', 'size': 0, 'market': '', 'sector_type': 0}
    edge_params = {
        'tran': 0, 'emptyps': 0, 'starttype': 0, 'starttime': 0,
        'starttimetype': 0, 'starttimehms': 0, 'cxtype': 0, 'cxtime': 0,
        'cxtimetype': 0, 'jgtime': 0,
    }
    return {
        'id': 'test_fanout',
        'name': '扇出拓扑测试',
        'nodes': [
            {'id': 'candidate_1', 'type': '7', 'dzh_cell_type': 7, 'text': '备选池', 'attr': 0,
             'pos': '0,0,200,100', 'params': {'stocks': stocks, 'tdx_spinfo': spinfo}},
            {'id': 'condition_A', 'type': '3', 'dzh_cell_type': 3, 'text': '条件A', 'attr': 0,
             'pos': '300,0,500,100', 'params': {'tdx_func': dict(func)}},
            {'id': 'condition_B', 'type': '3', 'dzh_cell_type': 3, 'text': '条件B', 'attr': 0,
             'pos': '300,200,500,300', 'params': {'tdx_func': dict(func)}},
            {'id': 'state_A', 'type': '8', 'dzh_cell_type': 8, 'text': '状态池A', 'attr': 0,
             'pos': '600,0,800,100', 'params': {'tdx_psatt': dict(psatt), 'stocks': []}},
            {'id': 'state_B', 'type': '8', 'dzh_cell_type': 8, 'text': '状态池B', 'attr': 0,
             'pos': '600,200,800,300', 'params': {'tdx_psatt': dict(psatt), 'stocks': []}},
        ],
        'edges': [
            {'id': 'e_c_a', 'from': 'candidate_1', 'to': 'condition_A', 'attr': 0, 'params': dict(edge_params)},
            {'id': 'e_c_b', 'from': 'candidate_1', 'to': 'condition_B', 'attr': 0, 'params': dict(edge_params)},
            {'id': 'e_a_sa', 'from': 'condition_A', 'to': 'state_A', 'attr': 0, 'params': dict(edge_params)},
            {'id': 'e_b_sb', 'from': 'condition_B', 'to': 'state_B', 'attr': 0, 'params': dict(edge_params)},
        ],
    }


def _make_direct_edge_pool(tran=0, attr_in_params=None):
    """Build a direct-edge pool: candidate(7) → state_pool(8) with no condition node.

    Tests the _edge_type_propagate_directly path.
    """
    from meta_core.tests.conftest import make_tdx_simple_pool
    stocks = [
        {'code': '600000', 'label': '浦发银行'},
        {'code': '000001', 'label': '平安银行'},
        {'code': '000002', 'label': '万科A'},
    ]
    pool = make_tdx_simple_pool(
        candidate_stocks=stocks,
        edge_tran=tran,
        direct_edge=True,
    )
    if attr_in_params is not None:
        for edge in pool['edges']:
            edge.setdefault('params', {})['attr'] = attr_in_params
    return pool


# ── Gate Tests: starttype (EDGE-001 ~ EDGE-006) ──

class TestEdgeGateStarttype:
    """EDGE-001 ~ EDGE-006: starttype gate behavior."""

    def test_edge_001_starttype0_immediate_pass(self):
        """EDGE-001: starttype=0 (immediate) → gate passes, stocks transferred."""
        config = make_tdx_simple_pool(
            condition_nset=5,
            condition_params={'ntjindexno': 0},
            edge_starttype=0,
        )
        result = run(config, ticks=1)
        assert_no_unhandled_exception(result)
        assert_pool_state(result, 'state_pool_1', ['600000', '000001', '000002'])

    def test_edge_002_starttype7_specific_time_reject(self):
        """EDGE-002: starttype=7, starttimehms=100000 (> 09:35) → gate rejects."""
        config = make_tdx_simple_pool(
            condition_nset=5,
            condition_params={'ntjindexno': 0},
            edge_starttype=7,
        )
        # virtual_clock starts at 34500.0 = 09:35:00 → hms_now=93500
        # starttimehms=100000 (10:00:00) > 93500 → reject
        _set_all_edges_param(config, 'starttimehms', 100000)
        result = run(config, ticks=1)
        assert_no_unhandled_exception(result)
        assert_pool_state(result, 'state_pool_1', [])

    def test_edge_003_starttype7_specific_time_pass(self):
        """EDGE-003: starttype=7, starttimehms=93000 (<= 09:35) → gate passes."""
        config = make_tdx_simple_pool(
            condition_nset=5,
            condition_params={'ntjindexno': 0},
            edge_starttype=7,
        )
        # starttimehms=93000 (09:30:00) <= 93500 → pass
        _set_all_edges_param(config, 'starttimehms', 93000)
        result = run(config, ticks=1)
        assert_no_unhandled_exception(result)
        assert_pool_state(result, 'state_pool_1', ['600000', '000001', '000002'])

    def test_edge_004_starttype6_trading_time_reject(self):
        """EDGE-004: starttype=6, starttimehms=100000 (> 09:35) → gate rejects."""
        config = make_tdx_simple_pool(
            condition_nset=5,
            condition_params={'ntjindexno': 0},
            edge_starttype=6,
        )
        _set_all_edges_param(config, 'starttimehms', 100000)
        result = run(config, ticks=1)
        assert_no_unhandled_exception(result)
        assert_pool_state(result, 'state_pool_1', [])

    def test_edge_005_starttype6_trading_time_pass(self):
        """EDGE-005: starttype=6, starttimehms=93000 (<= 09:35) → gate passes."""
        config = make_tdx_simple_pool(
            condition_nset=5,
            condition_params={'ntjindexno': 0},
            edge_starttype=6,
        )
        _set_all_edges_param(config, 'starttimehms', 93000)
        result = run(config, ticks=1)
        assert_no_unhandled_exception(result)
        assert_pool_state(result, 'state_pool_1', ['600000', '000001', '000002'])

    def test_edge_006_unknown_starttype_reject(self):
        """EDGE-006: starttype=99 (unknown) → gate rejects with warning."""
        config = make_tdx_simple_pool(
            condition_nset=5,
            condition_params={'ntjindexno': 0},
            edge_starttype=99,
        )
        result = run(config, ticks=1)
        assert_no_unhandled_exception(result)
        # Unknown starttype should reject (no stocks transferred)
        assert_pool_state(result, 'state_pool_1', [])


# ── Gate Tests: cxtype (EDGE-007 ~ EDGE-012) — Direct engine unit tests ──

class TestEdgeGateCxtype:
    """EDGE-007 ~ EDGE-012: cxtype duration gate behavior.

    Tests gate_duration_expired (production _CXTYPE_POST_GATES) directly
    since the driver doesn't expose per-tick gate decisions.
    """

    def _make_engine(self):
        """Create a fresh engine with virtual time source anchored to 09:35:00."""
        eng = PoolEngine()
        _set_engine_clock(eng, 34500.0)  # 09:35:00
        return eng

    def test_edge_007_cxtype0_forever_never_expires(self):
        """EDGE-007: cxtype=0 (forever) → never expires."""
        eng = self._make_engine()
        edge = {'id': 'test_edge', 'params': {'cxtype': 0}}
        # Should not expire regardless of execution count or time
        assert gate_duration_expired(eng, edge, exec_count=100) is False

    def test_edge_008_cxtype2_once_not_expired_before_execution(self):
        """EDGE-008: cxtype=2 (once), exec_count=0 → not expired."""
        eng = self._make_engine()
        edge = {'id': 'test_edge_once', 'params': {'cxtype': 2}}
        # exec_count=0 → not expired
        assert gate_duration_expired(eng, edge, exec_count=0) is False

    def test_edge_009_cxtype2_once_expired_after_execution(self):
        """EDGE-009: cxtype=2 (once), exec_count=1 → expired."""
        eng = self._make_engine()
        edge = {'id': 'test_edge_once', 'params': {'cxtype': 2}}
        # exec_count>=1 → expired
        assert gate_duration_expired(eng, edge, exec_count=1) is True

    def test_edge_010_cxtype1_duration_within_window(self):
        """EDGE-010: cxtype=1 (duration), within window → not expired."""
        eng = self._make_engine()
        edge = {'id': 'test_edge_dur', 'params': {'cxtype': 1, 'cxtime': 10, 'cxtimetype': 0}}
        # first_fire=None → not expired
        assert gate_duration_expired(eng, edge) is False
        # Immediately after (still no first_fire) → not expired
        assert gate_duration_expired(eng, edge) is False

    def test_edge_011_cxtype1_duration_expired_after_window(self):
        """EDGE-011: cxtype=1 (duration), after window → expired."""
        eng = self._make_engine()
        edge = {'id': 'test_edge_dur2', 'params': {'cxtype': 1, 'cxtime': 2, 'cxtimetype': 0}}
        # Record first_fire at virtual 34500 (09:35:00)
        from meta_core.core.execution_module import _now_ts
        first_fire_ts = _now_ts(eng._pool_engine.state)
        # Advance virtual clock by 3 seconds (> 2 second window)
        _set_engine_clock(eng, 34503.0)
        assert gate_duration_expired(eng, edge, first_fire=first_fire_ts) is True

    def test_edge_012_unknown_cxtype_default_not_expired(self):
        """EDGE-012: unknown cxtype=99 → default not expired (with warning)."""
        eng = self._make_engine()
        edge = {'id': 'test_edge_unknown', 'params': {'cxtype': 99}}
        # Unknown cxtype → default not expired
        assert gate_duration_expired(eng, edge) is False


# ── Flow Mode Tests (EDGE-013 ~ EDGE-024) ──

class TestEdgeFlowModes:
    """EDGE-013 ~ EDGE-024: flow mode (copy/move/overwrite/force_move/pass_through)."""

    def test_edge_013_copy_mode_source_retained(self):
        """EDGE-013: copy mode (tran=0) → source retained, target gets stocks."""
        config = make_tdx_simple_pool(
            condition_nset=5,
            condition_params={'ntjindexno': 0},
            edge_tran=0,
        )
        result = run(config, ticks=1)
        assert_no_unhandled_exception(result)
        assert_pool_state(result, 'state_pool_1', ['600000', '000001', '000002'])
        # Source (candidate_1) should retain stocks (copy mode)
        assert len(result.node_stocks.get('candidate_1', [])) == 3, \
            "BUG: EDGE-013 copy mode should retain source stocks"

    def test_edge_014_move_mode_source_cleared(self):
        """EDGE-014: move mode (tran=1) → source cleared, target gets stocks."""
        config = make_tdx_simple_pool(
            condition_nset=5,
            condition_params={'ntjindexno': 0},
            edge_tran=1,
        )
        result = run(config, ticks=1)
        assert_no_unhandled_exception(result)
        assert_pool_state(result, 'state_pool_1', ['600000', '000001', '000002'])
        # Source (candidate_1) should be cleared (move mode)
        # Note: move happens on candidate→condition edge, so candidate is cleared
        assert len(result.node_stocks.get('candidate_1', [])) == 0, \
            "BUG: EDGE-014 move mode should clear source pool"

    def test_edge_015_overwrite_mode_target_cleared_first(self):
        """EDGE-015: overwrite mode (clear_dest_first) → target cleared then filled.

        Uses direct edge with attr=8192 (bit 13 = clear_dest_first, per
        field_definitions.json bit_fields.flow.clear_dest_first mask 0x2000).
        Pre-fills target with an old stock to verify it gets cleared.
        """
        config = _make_direct_edge_pool(tran=0, attr_in_params=8192)
        # Pre-fill state_pool_1 with a different stock
        for node in config['nodes']:
            if node['id'] == 'state_pool_1':
                node['params']['stocks'] = [
                    {'code': '999999', 'label': 'OLD_STOCK'}
                ]
        result = run(config, ticks=1)
        assert_no_unhandled_exception(result)
        # Overwrite: old stock should be gone, new stocks present
        state_stocks = result.node_stocks.get('state_pool_1', [])
        assert '999999' not in state_stocks, \
            "BUG: EDGE-015 overwrite mode should clear old target stocks first"
        assert '600000' in state_stocks, \
            "BUG: EDGE-015 overwrite mode should fill with new stocks"

    def test_edge_016_force_move_mode_both_cleared(self):
        """EDGE-016: force_move mode (delete_source + force_move) → source cleared, target overwritten.

        Uses direct edge with attr=3 (bit0=delete_source 0x1 + bit1=force_move 0x2,
        per field_definitions.json bit_fields.flow). Pre-fills target with an old
        stock to verify it gets overwritten.
        """
        config = _make_direct_edge_pool(tran=1, attr_in_params=3)
        # Pre-fill state_pool_1 with a different stock
        for node in config['nodes']:
            if node['id'] == 'state_pool_1':
                node['params']['stocks'] = [
                    {'code': '999999', 'label': 'OLD_STOCK'}
                ]
        result = run(config, ticks=1)
        assert_no_unhandled_exception(result)
        # Force_move: source cleared
        assert len(result.node_stocks.get('candidate_1', [])) == 0, \
            "BUG: EDGE-016 force_move should clear source pool"
        # Force_move: target overwritten (old stock gone, new stocks present)
        state_stocks = result.node_stocks.get('state_pool_1', [])
        assert '999999' not in state_stocks, \
            "BUG: EDGE-016 force_move should overwrite target pool"
        assert '600000' in state_stocks, \
            "BUG: EDGE-016 force_move should fill target with new stocks"

    def test_edge_017_pass_through_default_transfer(self):
        """EDGE-017: pass_through (default, attr=0, tran=0) → basic transfer."""
        config = _make_direct_edge_pool(tran=0, attr_in_params=0)
        result = run(config, ticks=1)
        assert_no_unhandled_exception(result)
        assert_pool_state(result, 'state_pool_1', ['600000', '000001', '000002'])
        # Default pass_through: source retained
        assert len(result.node_stocks.get('candidate_1', [])) == 3, \
            "BUG: EDGE-017 pass_through (default) should retain source"

    def test_edge_018_emptyps0_empty_source_skip(self):
        """EDGE-018: emptyps=0, empty source → skip, no crash."""
        config = make_tdx_simple_pool(
            candidate_stocks=[],  # Empty candidate pool
            condition_nset=5,
            condition_params={'ntjindexno': 0},
            edge_tran=0,
        )
        _set_all_edges_param(config, 'emptyps', 0)
        result = run(config, ticks=1)
        assert_no_unhandled_exception(result)
        # Empty source + emptyps=0 → no transfer
        assert_pool_state(result, 'state_pool_1', [])

    def test_edge_019_emptyps1_empty_source_still_execute(self):
        """EDGE-019: emptyps=1, empty source → still executes, no crash.

        emptyps=1 means the flow triggers even when source is empty.
        The condition evaluator receives an empty stock_list and returns [].
        """
        config = make_tdx_simple_pool(
            candidate_stocks=[],  # Empty candidate pool
            condition_nset=5,
            condition_params={'ntjindexno': 0},
            edge_tran=0,
        )
        _set_all_edges_param(config, 'emptyps', 1)
        result = run(config, ticks=1)
        assert_no_unhandled_exception(result)
        # emptyps=1 → flow executes even with empty source
        # But condition evaluator returns [] for empty input, so state_pool is empty
        assert_pool_state(result, 'state_pool_1', [])

    def test_edge_020_copy_mode_source_count_unchanged(self):
        """EDGE-020: copy mode → source count unchanged after transfer."""
        config = make_tdx_simple_pool(
            condition_nset=5,
            condition_params={'ntjindexno': 0},
            edge_tran=0,
        )
        result = run(config, ticks=1)
        assert_no_unhandled_exception(result)
        src_count = len(result.node_stocks.get('candidate_1', []))
        tgt_count = len(result.node_stocks.get('state_pool_1', []))
        assert src_count == 3, \
            f"BUG: EDGE-020 copy mode source should have 3 stocks, got {src_count}"
        assert tgt_count == 3, \
            f"BUG: EDGE-020 copy mode target should have 3 stocks, got {tgt_count}"

    def test_edge_021_move_mode_source_count_zero(self):
        """EDGE-021: move mode → source count zero after transfer."""
        config = make_tdx_simple_pool(
            condition_nset=5,
            condition_params={'ntjindexno': 0},
            edge_tran=1,
        )
        result = run(config, ticks=1)
        assert_no_unhandled_exception(result)
        src_count = len(result.node_stocks.get('candidate_1', []))
        tgt_count = len(result.node_stocks.get('state_pool_1', []))
        assert src_count == 0, \
            f"BUG: EDGE-021 move mode source should have 0 stocks, got {src_count}"
        assert tgt_count == 3, \
            f"BUG: EDGE-021 move mode target should have 3 stocks, got {tgt_count}"

    def test_edge_022_overwrite_replaces_existing_target(self):
        """EDGE-022: overwrite mode → existing target stocks replaced.

        Pre-fills target with 2 stocks, then overwrites with 3 new stocks.
        Verifies old stocks are gone and new stocks are present.

        Uses attr=8192 (bit 13 = clear_dest_first, per field_definitions.json
        bit_fields.flow.clear_dest_first mask 0x2000).
        """
        config = _make_direct_edge_pool(tran=0, attr_in_params=8192)
        # Pre-fill state_pool_1 with 2 different stocks
        for node in config['nodes']:
            if node['id'] == 'state_pool_1':
                node['params']['stocks'] = [
                    {'code': '999999', 'label': 'OLD_1'},
                    {'code': '888888', 'label': 'OLD_2'},
                ]
        result = run(config, ticks=1)
        assert_no_unhandled_exception(result)
        state_stocks = result.node_stocks.get('state_pool_1', [])
        # Old stocks should be gone
        assert '999999' not in state_stocks, \
            "BUG: EDGE-022 overwrite should remove old stock 999999"
        assert '888888' not in state_stocks, \
            "BUG: EDGE-022 overwrite should remove old stock 888888"
        # New stocks should be present
        assert '600000' in state_stocks, \
            "BUG: EDGE-022 overwrite should add new stock 600000"
        assert '000001' in state_stocks, \
            "BUG: EDGE-022 overwrite should add new stock 000001"
        assert '000002' in state_stocks, \
            "BUG: EDGE-022 overwrite should add new stock 000002"

    def test_edge_023_multi_edge_fanout_copy(self):
        """EDGE-023: multi-edge fan-out, copy mode → both targets get stocks.

        candidate_1 ─┬─ condition_A → state_A
                      └─ condition_B → state_B
        """
        config = _make_fanout_pool()
        _set_condition_pass_all(config)
        result = run(config, ticks=1)
        assert_no_unhandled_exception(result)
        expected = ['600000', '000001', '000002']
        assert_pool_state(result, 'state_A', expected)
        assert_pool_state(result, 'state_B', expected)
        # Source retained (copy mode)
        assert len(result.node_stocks.get('candidate_1', [])) == 3, \
            "BUG: EDGE-023 fan-out copy should retain source stocks"

    def test_edge_024_direct_edge_propagate_directly(self):
        """EDGE-024: direct edge (candidate→state_pool) → propagate_directly path.

        Tests the unconditional edge handler (_edge_type_propagate_directly)
        which skips gate and filter, directly propagating stocks.
        """
        config = _make_direct_edge_pool(tran=0)
        result = run(config, ticks=1)
        assert_no_unhandled_exception(result)
        assert_pool_state(result, 'state_pool_1', ['600000', '000001', '000002'])
        # Direct edge with tran=0 → source retained
        assert len(result.node_stocks.get('candidate_1', [])) == 3, \
            "BUG: EDGE-024 direct edge copy should retain source"

    def test_edge_025_overwrite_multi_tick_no_rebuy(self):
        """EDGE-025: overwrite_copy + multi-tick + stable source → no re-BUY / no tracker reset.

        I66：验证 _tgt_overwrite entered 语义统一（与 _tgt_merge 同构，返回 NEW codes）。
        旧实现返回 ALL transferred codes，导致 3 个可观察 bug：
          1. BUY 信号 spam：已持仓代码每 tick 重复发 BUY（_run_callback 遍历 entered）
          2. tracker 重置：_init_entry_trackers 对已持仓代码每 tick 重置
             entry_price/entry_time → profit_pct/hold_days 恒 0、TTL 永不触发
          3. ENTER 事件 spam：_emit_transfer_events 对 ALL transferred_codes 发 ENTER

        场景：overwrite_copy（attr=8192, source retained）+ baimpool=1 + ticks=3
        + source 稳定含 600000。修复后 BUY 仅 1 次（tick 1 新入池），tracker
        entry_time 跨 tick 不变。
        """
        from tests.conftest import make_tdx_simple_pool
        stocks = [{'code': '600000', 'label': '浦发银行'}]
        config = make_tdx_simple_pool(
            candidate_stocks=stocks, edge_tran=0, direct_edge=True,
            psatt_params={'baimpool': 1},
        )
        for edge in config['edges']:
            edge.setdefault('params', {})['attr'] = 8192  # clear_dest_first → overwrite_copy

        result = run(config, ticks=3)
        assert_no_unhandled_exception(result)

        buy_signals = [s for s in result.signals
                       if s.get('signal_type') == 'BUY' and s.get('code') == '600000']
        assert len(buy_signals) == 1, \
            f"BUG: EDGE-025 overwrite_copy multi-tick should emit BUY once, got {len(buy_signals)}"

        # I67: ENTER spam 修复——_sync_events_to_meta 仅同步本 tick 新增 Executed，
        # 历史 Executed 不再重复发 ENTER（旧实现 ENTER=3，应 1）
        enter_events = [e for e in result.events if isinstance(e, dict)
                        and e.get('event_type', '') == 'ENTER'
                        and e.get('code') == '600000']
        assert len(enter_events) == 1, \
            f"BUG: EDGE-025 overwrite_copy multi-tick should emit ENTER once, got {len(enter_events)}"

        # tracker 验证：直接构造 RuntimeSimulator 访问 raw stock._tracker
        from meta_core.core.runtime_mode_module import RuntimeSimulator
        sim = RuntimeSimulator(config, seed=42)
        sim.initialize()
        entry_times = []
        for _ in range(3):
            sim.step()
            pe = sim._engine._pool_engine
            if pe is not None:
                for s in pe.state.get_pool('state_pool_1').get_stocks():
                    if isinstance(s, dict) and s.get('code') == '600000':
                        tracker = s.get('_tracker')
                        entry_times.append(tracker.get('entry_time') if tracker else None)
        # tick 1: tracker 首次创建；tick 2-3: entered=[] → _init_entry_trackers 不调用 → entry_time 不变
        assert len(entry_times) == 3, \
            f"BUG: EDGE-025 should collect 3 ticks of tracker data, got {len(entry_times)}"
        assert entry_times[0] is not None, \
            "BUG: EDGE-025 tick 1 tracker.entry_time should be set"
        assert entry_times[1] == entry_times[0], \
            f"BUG: EDGE-025 tracker.entry_time reset at tick 2: {entry_times[1]} != {entry_times[0]}"
        assert entry_times[2] == entry_times[0], \
            f"BUG: EDGE-025 tracker.entry_time reset at tick 3: {entry_times[2]} != {entry_times[0]}"

    def test_edge_026_copy_multi_tick_enter_once(self):
        """EDGE-026: copy + multi-tick + stable source → ENTER emitted once.

        I67：验证 _sync_events_to_meta 历史重复处理 bug 修复。旧实现每 tick
        读取 ALL 历史 Executed 填入 transfer_events，导致 _emit_transfer_events
        对历史 transferred_codes 重复发 ENTER（copy 模式 1 stock × 3 ticks →
        ENTER=3，应 1）。

        场景：copy（direct_edge, tran=0）+ baimpool=1 + ticks=3 + source 稳定
        含 600000。修复后 ENTER 仅 1 次（tick 1 新入池），tick 2-3 entered=[]
        → 不发 ENTER。
        """
        from tests.conftest import make_tdx_simple_pool
        stocks = [{'code': '600000', 'label': '浦发银行'}]
        config = make_tdx_simple_pool(
            candidate_stocks=stocks, edge_tran=0, direct_edge=True,
            psatt_params={'baimpool': 1},
        )
        result = run(config, ticks=3)
        assert_no_unhandled_exception(result)

        enter_events = [e for e in result.events if isinstance(e, dict)
                        and e.get('event_type', '') == 'ENTER'
                        and e.get('code') == '600000']
        assert len(enter_events) == 1, \
            f"BUG: EDGE-026 copy multi-tick should emit ENTER once, got {len(enter_events)}"
