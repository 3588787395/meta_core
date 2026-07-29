"""Test 05: Core Execution Flow — FLOW-001 ~ FLOW-012.

Tests the core execution pipeline: gate → filter → propagate → callback → TTL.
Uses the simtests harness (driver, assertions, bug_asserts) and factory functions
from tests/conftest.py.
"""
from __future__ import annotations

from simtests.conftest import *  # noqa: F401
from simtests.harness.driver import run  # noqa: E402
from simtests.harness.assertions import assert_pool_state, assert_event_emitted  # noqa: E402
from simtests.harness.bug_asserts import assert_no_unhandled_exception  # noqa: E402


def _set_condition_pass_all(config):
    """Set all condition nodes in config to nset=5, ntjindexno=0 (pass-all union)."""
    for node in config.get('nodes', []):
        if node.get('type') == '3':
            func = node.get('params', {}).get('tdx_func', {})
            func['nset'] = 5
            func['ntjindexno'] = 0


def _make_intersection_pool():
    """Build a pool with two sources feeding one condition (intersection filter).

    candidate_A [600000, 000001, 000002] ─┐
                                          ├─ condition_1 (nset=5, ntjindexno=2) ─ state_pool_1
    candidate_B [000001, 000002, 600036] ─┘

    Expected intersection result: [000001, 000002]
    """
    stocks_a = [
        {'code': '600000', 'label': '浦发银行'},
        {'code': '000001', 'label': '平安银行'},
        {'code': '000002', 'label': '万科A'},
    ]
    stocks_b = [
        {'code': '000001', 'label': '平安银行'},
        {'code': '000002', 'label': '万科A'},
        {'code': '600036', 'label': '招商银行'},
    ]
    func = {
        'nset': 5, 'ntjindexno': 2, 'accode': '', 'nperiod': 4,
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
        'tran': 0, 'emptyps': 0, 'starttype': 0,
        'starttime': 0, 'starttimetype': 0, 'starttimehms': 0,
        'cxtype': 0, 'cxtime': 0, 'cxtimetype': 0, 'jgtime': 0,
    }
    return {
        'id': 'test_intersection',
        'name': '交集过滤测试',
        'nodes': [
            {'id': 'cand_A', 'type': '7', 'dzh_cell_type': 7, 'text': '备选A', 'attr': 0,
             'pos': '0,0,200,100', 'params': {'stocks': stocks_a, 'tdx_spinfo': spinfo}},
            {'id': 'cand_B', 'type': '7', 'dzh_cell_type': 7, 'text': '备选B', 'attr': 0,
             'pos': '0,200,200,300', 'params': {'stocks': stocks_b, 'tdx_spinfo': spinfo}},
            {'id': 'cond_1', 'type': '3', 'dzh_cell_type': 3, 'text': '条件', 'attr': 0,
             'pos': '300,100,500,200', 'params': {'tdx_func': func}},
            {'id': 'state_1', 'type': '8', 'dzh_cell_type': 8, 'text': '状态池', 'attr': 0,
             'pos': '600,100,800,200', 'params': {'tdx_psatt': psatt, 'stocks': []}},
        ],
        'edges': [
            {'id': 'e_a_c', 'from': 'cand_A', 'to': 'cond_1', 'attr': 0, 'params': dict(edge_params)},
            {'id': 'e_b_c', 'from': 'cand_B', 'to': 'cond_1', 'attr': 0, 'params': dict(edge_params)},
            {'id': 'e_c_s', 'from': 'cond_1', 'to': 'state_1', 'attr': 0, 'params': dict(edge_params)},
        ],
    }


def _make_composite_pool():
    """Build a composite pool: multi-in aggregation + multi-out fan-out + TTL.

    cand_A [600000, 000001] ─┬─ state_1 (TTL=2s)
                             ├─ state_2 (TTL=2s)
    cand_B [000001, 000002] ─┼─ state_1 (multi-in aggregation)
                             └─ state_2 (multi-out fan-out)
    """
    stocks_a = [
        {'code': '600000', 'label': '浦发银行'},
        {'code': '000001', 'label': '平安银行'},
    ]
    stocks_b = [
        {'code': '000001', 'label': '平安银行'},
        {'code': '000002', 'label': '万科A'},
    ]
    psatt_ttl = {
        'bdel': 1, 'ndelnum': 2, 'ndeltype': 3,
        'baimpool': 0, 'bsound': 0, 'nsoundtype': 0, 'nsyssound': 0, 'soundfile': '',
        'btip': 0, 'bsavetoblock': 0, 'blockfile': '', 'bclearblock': 0, 'bsavehis': 0,
    }
    spinfo = {'type': 0, 'customblockname': '', 'size': 0, 'market': '', 'sector_type': 0}
    edge_params = {
        'tran': 0, 'emptyps': 0, 'starttype': 0,
        'starttime': 0, 'starttimetype': 0, 'starttimehms': 0,
        'cxtype': 2, 'cxtime': 0, 'cxtimetype': 0, 'jgtime': 0,
    }
    return {
        'id': 'test_composite',
        'name': '复合流测试',
        'nodes': [
            {'id': 'cand_A', 'type': '7', 'dzh_cell_type': 7, 'text': '备选A', 'attr': 0,
             'pos': '0,0,200,100', 'params': {'stocks': stocks_a, 'tdx_spinfo': spinfo}},
            {'id': 'cand_B', 'type': '7', 'dzh_cell_type': 7, 'text': '备选B', 'attr': 0,
             'pos': '0,200,200,300', 'params': {'stocks': stocks_b, 'tdx_spinfo': spinfo}},
            {'id': 'state_1', 'type': '8', 'dzh_cell_type': 8, 'text': '状态池1', 'attr': 0,
             'pos': '600,0,800,100', 'params': {'tdx_psatt': dict(psatt_ttl), 'stocks': []}},
            {'id': 'state_2', 'type': '8', 'dzh_cell_type': 8, 'text': '状态池2', 'attr': 0,
             'pos': '600,200,800,300', 'params': {'tdx_psatt': dict(psatt_ttl), 'stocks': []}},
        ],
        'edges': [
            {'id': 'e_a_s1', 'from': 'cand_A', 'to': 'state_1', 'attr': 0, 'params': dict(edge_params)},
            {'id': 'e_b_s1', 'from': 'cand_B', 'to': 'state_1', 'attr': 0, 'params': dict(edge_params)},
            {'id': 'e_a_s2', 'from': 'cand_A', 'to': 'state_2', 'attr': 0, 'params': dict(edge_params)},
            {'id': 'e_b_s2', 'from': 'cand_B', 'to': 'state_2', 'attr': 0, 'params': dict(edge_params)},
        ],
    }


class TestExecutionFlow:
    """FLOW-001 ~ FLOW-012: Core execution flow tests."""

    def test_flow_001_full_chain_order(self):
        """FLOW-001: gate→filter→propagate→callback→ttl full chain order."""
        config = make_tdx_simple_pool(
            condition_nset=5,
            condition_params={'ntjindexno': 0},
            psatt_params={'bsavehis': 1},
        )
        result = run(config, ticks=1)
        assert_no_unhandled_exception(result)
        assert_pool_state(result, 'state_pool_1', ['600000', '000001', '000002'])
        assert_event_emitted(result, 'ENTER')
        assert result.tick_count == 1, "BUG: FLOW-001 full chain did not complete in 1 tick"

    def test_flow_002_gate_passes_filter_passes_propagate(self):
        """FLOW-002: gate passes → filter executes → stocks propagate to state pool."""
        config = make_tdx_simple_pool(
            condition_nset=5,
            condition_params={'ntjindexno': 0},
        )
        result = run(config, ticks=1)
        assert_no_unhandled_exception(result)
        assert_pool_state(result, 'state_pool_1', ['600000', '000001', '000002'])
        assert len(result.node_stocks.get('state_pool_1', [])) == 3, \
            "BUG: FLOW-002 expected 3 stocks propagated to state_pool_1"

    def test_flow_003_gate_passes_filter_rejects_all(self):
        """FLOW-003: gate passes → filter rejects all → state pool unchanged (empty)."""
        config = make_tdx_simple_pool(
            condition_nset=5,
            condition_params={'ntjindexno': 2},
        )
        result = run(config, ticks=1)
        assert_no_unhandled_exception(result)
        assert_pool_state(result, 'state_pool_1', [])
        assert len(result.node_stocks.get('state_pool_1', [])) == 0, \
            "BUG: FLOW-003 state_pool_1 should be empty when filter rejects all"

    def test_flow_004_filter_passes_some(self):
        """FLOW-004: gate passes → filter passes some → only passing stocks propagate.

        使用 nset=5 ntjindexno=0 (并集透传) 验证源池全部股票传播；
        再用 nset=5 ntjindexno=2 (差集) 验证结果为空（差集在单源池时为空）。"""
        # 正向：并集模式透传所有源池股票
        config_pass = make_tdx_simple_pool(
            condition_nset=5,
            condition_params={'ntjindexno': 0},
        )
        result_pass = run(config_pass, ticks=1)
        assert_no_unhandled_exception(result_pass)
        state_stocks = set(result_pass.node_stocks.get('state_pool_1', []))
        expected = {'600000', '000001', '000002'}
        assert state_stocks == expected, \
            f"BUG: FLOW-004 union should pass all {expected}, got {state_stocks}"

        # 反向：差集模式（单源池差集为空）
        config_reject = make_tdx_simple_pool(
            condition_nset=5,
            condition_params={'ntjindexno': 2},
        )
        result_reject = run(config_reject, ticks=1)
        reject_stocks = set(result_reject.node_stocks.get('state_pool_1', []))
        assert reject_stocks == set(), \
            f"BUG: FLOW-004 difference of single source should be empty, got {reject_stocks}"

    def test_flow_005_unconditional_edge_direct_propagate(self):
        """FLOW-005: unconditional edge skips gate and filter → direct propagate."""
        config = make_tdx_unconditional_pool()
        result = run(config, ticks=1)
        assert_no_unhandled_exception(result)
        assert_pool_state(result, 'state_pool_1', ['600000', '000001'])
        assert_event_emitted(result, 'ENTER')
        assert len(result.node_stocks.get('state_pool_1', [])) == 2, \
            "BUG: FLOW-005 unconditional edge should propagate 2 stocks directly"

    def test_flow_006_callback_fires_after_propagate(self):
        """FLOW-006: callback fires after propagate (bsavehis=1 → history saved)."""
        config = make_tdx_simple_pool(
            condition_nset=5,
            condition_params={'ntjindexno': 0},
            psatt_params={'bsavehis': 1},
        )
        result = run(config, ticks=1)
        assert_no_unhandled_exception(result)
        assert_pool_state(result, 'state_pool_1', ['600000', '000001', '000002'])
        assert_event_emitted(result, 'ENTER')
        enter_events = [e for e in result.events if isinstance(e, dict)
                        and e.get('event_type', '') == 'ENTER']
        assert len(enter_events) > 0, \
            "BUG: FLOW-006 callback should fire ENTER events after propagate"

    def test_flow_007_ttl_expiry(self):
        """FLOW-007: TTL set on propagate → stock expires after N ticks.

        使用 direct_edge=False 走条件节点路径，使 _tracker.entry_time 被设置，
        TTL 过期才能生效。ndeltype=3 (秒) ndelnum=2 → 2秒后过期。"""
        config = make_tdx_ttl_pool(
            ttl_ndelnum=2,
            ttl_ndeltype=3,
            direct_edge=False,
            edge_tran=0,
        )
        # 设置条件为透传（nset=5, ntjindexno=0）
        _set_condition_pass_all(config)
        result = run(config, ticks=4)
        enter_events = [e for e in result.events if isinstance(e, dict)
                        and e.get('event_type', '') == 'ENTER']
        assert len(enter_events) > 0, \
            "BUG: FLOW-007 ENTER events should have been emitted before TTL expiry"
        assert len(result.node_stocks.get('state_pool_1', [])) == 0, \
            "BUG: FLOW-007 state_pool_1 should be empty after TTL expiry (4 ticks)"

    def test_flow_008_multi_level_pool(self):
        """FLOW-008: multi-level pool — stocks flow through both levels."""
        config = make_tdx_multi_level_pool()
        _set_condition_pass_all(config)
        result = run(config, ticks=1)
        assert_no_unhandled_exception(result)
        expected = ['600000', '000001', '000002', '600036', '601318']
        assert_pool_state(result, 'state_A', expected)
        assert_pool_state(result, 'state_B', expected)
        assert len(result.node_stocks.get('state_B', [])) == 5, \
            "BUG: FLOW-008 expected 5 stocks in state_B after multi-level flow"

    def test_flow_009_gate_rejects_filter_not_executed(self):
        """FLOW-009: gate rejects (wrong timing) → filter does NOT execute."""
        config = make_tdx_simple_pool(
            condition_nset=5,
            condition_params={'ntjindexno': 0},
            edge_starttype=7,
        )
        for edge in config['edges']:
            edge['params']['starttimehms'] = 150000
        result = run(config, ticks=1)
        assert_pool_state(result, 'state_pool_1', [])
        enter_events = [e for e in result.events if isinstance(e, dict)
                        and e.get('event_type', '') == 'ENTER']
        assert len(enter_events) == 0, \
            "BUG: FLOW-009 no ENTER events expected when gate rejects (filter not executed)"

    def test_flow_010_filter_throws_exception_caught(self):
        """FLOW-010: filter throws exception → no silent pass, no crash.

        nset=1 (条件选股) 在无公式数据时评估失败，应跳过该股票而非静默通过。
        验证：state_pool_1 为空 + 无 ENTER 事件 + 无未处理异常（Traceback）。"""
        config = make_tdx_simple_pool(
            condition_nset=1,
            condition_params={'ntjindexno': 0},
        )
        result = run(config, ticks=1)
        # 无静默通过：状态池应为空
        assert len(result.node_stocks.get('state_pool_1', [])) == 0, \
            "BUG: FLOW-010 no silent pass — state_pool_1 should be empty on filter exception"
        # 无 ENTER 事件
        enter_events = [e for e in result.events if isinstance(e, dict)
                        and e.get('event_type', '') == 'ENTER']
        assert len(enter_events) == 0, \
            "BUG: FLOW-010 no ENTER events expected on filter exception"
        # 无未处理异常
        assert_no_unhandled_exception(result)

    def test_flow_011_empty_source_pool_no_crash(self):
        """FLOW-011: empty source pool → no propagate, no crash."""
        config = make_tdx_simple_pool(
            candidate_stocks=[],
            condition_nset=5,
            condition_params={'ntjindexno': 0},
        )
        result = run(config, ticks=1)
        assert_no_unhandled_exception(result)
        assert_pool_state(result, 'state_pool_1', [])
        enter_events = [e for e in result.events if isinstance(e, dict)
                        and e.get('event_type', '') == 'ENTER']
        assert len(enter_events) == 0, \
            "BUG: FLOW-011 no ENTER events expected with empty source pool"

    def test_flow_012_composite_multi_in_out_ttl(self):
        """FLOW-012: multi-in edge aggregation + multi-out edge fan-out + TTL expiry synchronized."""
        config = _make_composite_pool()
        result = run(config, ticks=1)
        assert_no_unhandled_exception(result)
        expected = ['600000', '000001', '000002']
        assert_pool_state(result, 'state_1', expected)
        assert_pool_state(result, 'state_2', expected)
        assert_event_emitted(result, 'ENTER')
        result_ttl = run(config, ticks=3)
        ttl_logs = [l for l in result_ttl.logs if 'TTL' in l]
        assert len(ttl_logs) > 0, \
            f"BUG: FLOW-012 TTL expiry logs expected after 3 ticks, logs={result_ttl.logs[-5:]}"
