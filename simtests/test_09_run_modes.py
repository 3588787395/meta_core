"""Test 09: Run Modes — RM-001 ~ RM-007.

Tests the three run modes (live / replay / simulation) and their time source
routing, ensuring each mode binds to the correct driver_type and handler.

Key invariants:
  - live mode → wall_clock driver → system time (NOT virtual_clock)
  - replay mode → sequence driver → bar timeline
  - simulation mode → virtual driver → state.time_source['current_ts']

  - live mode MUST NOT use virtual_clock
  - simulation mode MUST NOT call real tq_adapter
  - All modes MUST set _loop_pool_config (regression test for BUG fix)
"""
from __future__ import annotations

import asyncio
from datetime import datetime as _dt, timedelta
from typing import Any

import pytest

from meta_core.core.engine import PoolEngine
from simtests.conftest import make_tdx_simple_pool
from simtests.harness.bug_asserts import assert_no_unhandled_exception


def _make_pool():
    """Build a simple TDX pool for mode testing."""
    return make_tdx_simple_pool(
        candidate_stocks=[
            {'code': '600000', 'label': '浦发银行'},
            {'code': '000001', 'label': '平安银行'},
        ],
        condition_nset=5,
        condition_params={'ntjindexno': 0},
        edge_tran=0,
    )


def _run_coro(coro):
    """Run a coroutine in a fresh event loop (for synchronous test context)."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TestRunModes:
    """RM-001 ~ RM-007: Run mode time source routing and isolation."""

    def test_rm_001_simulation_mode_sets_virtual_clock_time_source(self):
        """RM-001: simulation 模式 _setup_mode 将 virtual_clock 写入 state.time_source。

        正向：simulation 模式必须绑定 virtual_clock 时间源。
        """
        eng = PoolEngine()
        pool = _make_pool()
        eng._setup_mode("simulation", pool)

        ts = eng._pool_engine.state.time_source
        assert ts is not None, "BUG: RM-001 simulation 模式 time_source 不应为 None"
        assert ts.get('driver_type') == 'virtual', \
            f"BUG: RM-001 simulation 模式 driver_type 应为 'virtual', 实际 '{ts.get('driver_type')}'"
        assert ts.get('time_source_id') == 'virtual_clock', \
            f"BUG: RM-001 simulation 模式 time_source_id 应为 'virtual_clock', 实际 '{ts.get('time_source_id')}'"

    def test_rm_002_live_mode_sets_wall_clock_time_source(self):
        """RM-002: live 模式 _setup_mode 将 realtime 写入 state.time_source (wall_clock)。

        正向：live 模式必须绑定 wall_clock 时间源。
        """
        eng = PoolEngine()
        pool = _make_pool()
        eng._setup_mode("live", pool)

        ts = eng._pool_engine.state.time_source
        assert ts is not None, "BUG: RM-002 live 模式 time_source 不应为 None"
        assert ts.get('driver_type') == 'wall_clock', \
            f"BUG: RM-002 live 模式 driver_type 应为 'wall_clock', 实际 '{ts.get('driver_type')}'"
        assert ts.get('time_source_id') == 'realtime', \
            f"BUG: RM-002 live 模式 time_source_id 应为 'realtime', 实际 '{ts.get('time_source_id')}'"

    def test_rm_003_replay_mode_sets_sequence_time_source(self):
        """RM-003: replay 模式 _setup_mode 将 kline_timeline 写入 state.time_source (sequence)。

        正向：replay 模式必须绑定 sequence 时间源。
        """
        eng = PoolEngine()
        pool = _make_pool()
        eng._setup_mode("replay", pool)

        ts = eng._pool_engine.state.time_source
        assert ts is not None, "BUG: RM-003 replay 模式 time_source 不应为 None"
        assert ts.get('driver_type') == 'sequence', \
            f"BUG: RM-003 replay 模式 driver_type 应为 'sequence', 实际 '{ts.get('driver_type')}'"
        assert ts.get('time_source_id') == 'kline_timeline', \
            f"BUG: RM-003 replay 模式 time_source_id 应为 'kline_timeline', 实际 '{ts.get('time_source_id')}'"

    def test_rm_004_live_mode_now_does_not_use_virtual_clock(self):
        """RM-004: live 模式 _now() 禁止使用 virtual_clock handler。

        反向：live 模式 _now() 必须返回系统时间，不能返回 virtual current_ts 值。
        设置 state.time_source['current_ts'] 为一个特殊值，验证 _now() 不返回该值对应的时间。
        """
        eng = PoolEngine()
        pool = _make_pool()
        eng._setup_mode("live", pool)

        # Set current_ts to a distinctive value (01:00:00 = 3600s)
        eng._pool_engine.state.time_source['current_ts'] = 3600.0

        now = eng._now()
        # live mode should use wall_clock → system time, NOT virtual current_ts
        # System time should be close to _dt.now(), not 01:00:00
        system_now = _dt.now()
        delta = abs((now - system_now).total_seconds())
        assert delta < 5.0, \
            f"BUG: RM-004 live 模式 _now() 应返回系统时间，实际返回 {now} (与系统时间差 {delta}s，可能误用 virtual current_ts)"

        # Verify the hour is NOT 1 (which would indicate current_ts=3600 was used)
        # Allow some tolerance: system hour vs virtual hour=1
        assert now.hour != 1 or system_now.hour == 1, \
            f"BUG: RM-004 live 模式 _now() 返回 hour={now.hour}，疑似误用 virtual current_ts=3600 (01:00:00)"

    def test_rm_005_simulation_mode_now_uses_virtual_clock(self):
        """RM-005: simulation 模式 _now() 使用 state.time_source['current_ts']。

        正向：simulation 模式 _now() 必须返回 virtual current_ts 对应的时间。
        """
        eng = PoolEngine()
        pool = _make_pool()
        eng._setup_mode("simulation", pool)

        # Set current_ts to 10:00:00 = 36000s
        eng._pool_engine.state.time_source['current_ts'] = 36000.0

        now = eng._now()
        expected = _dt.now().replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(seconds=36000)
        delta = abs((now - expected).total_seconds())
        assert delta < 1.0, \
            f"BUG: RM-005 simulation 模式 _now() 应返回 current_ts=36000 对应时间 {expected}, 实际 {now} (差 {delta}s)"

        # Verify hour is 10 (36000s = 10:00:00)
        assert now.hour == 10, \
            f"BUG: RM-005 simulation 模式 _now() hour 应为 10 (current_ts=36000), 实际 {now.hour}"

    def test_rm_006_simulation_mode_no_real_tq_adapter(self):
        """RM-006: simulation 模式禁止调用真实 tq_adapter。

        反向：simulation 模式下 engine.tq_adapter 必须为 None（不连接真实行情）。
        """
        eng = PoolEngine()
        pool = _make_pool()
        eng._setup_mode("simulation", pool)

        # simulation mode must not have a real tq_adapter
        assert eng.tq_adapter is None, \
            f"BUG: RM-006 simulation 模式 tq_adapter 应为 None, 实际 {eng.tq_adapter}"

        # Run a tick to verify no real adapter is invoked
        eng._pool_engine.state.time_source['current_ts'] = 34500.0
        mode_state = {'node_stocks': eng._init_node_stocks({n['id']: n for n in pool.get('nodes', [])}),
                      'inject': True}
        bar_data = {'600000': {'close': 10.0, 'open': 10.0, 'high': 10.5, 'low': 9.5, 'volume': 1000},
                    '000001': {'close': 15.0, 'open': 15.0, 'high': 15.5, 'low': 14.5, 'volume': 2000}}
        _run_coro(eng._tick(pool, mode_state['node_stocks'], bar_data, mode_state))

        # After tick, tq_adapter should still be None
        assert eng.tq_adapter is None, \
            "BUG: RM-006 simulation 模式 tick 后 tq_adapter 不应被设置为真实适配器"

    def test_rm_007_all_modes_set_loop_pool_config(self):
        """RM-007: 三模式 _loop_pool_config 都正确设置（回归测试）。

        综合：验证 _setup_mode 在所有模式下都设置 _loop_pool_config，
        确保后续 _emit_transfer_events / _pre_tick 等能读取池配置。
        这是之前 simulation/replay 模式下 BUY/SELL 信号不发射的 BUG 回归测试。
        """
        for mode_id in ("live", "replay", "simulation"):
            eng = PoolEngine()
            pool = _make_pool()
            eng._setup_mode(mode_id, pool)

            assert eng._loop_pool_config is not None, \
                f"BUG: RM-007 {mode_id} 模式 _loop_pool_config 不应为 None"
            assert eng._loop_pool_config is pool, \
                f"BUG: RM-007 {mode_id} 模式 _loop_pool_config 应为传入的 pool_config 引用"
            # Verify nodes are accessible
            nodes = eng._loop_pool_config.get('nodes', [])
            assert len(nodes) >= 3, \
                f"BUG: RM-007 {mode_id} 模式 _loop_pool_config nodes 应有 ≥3 个节点, 实际 {len(nodes)}"
