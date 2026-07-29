# -*- coding: utf-8 -*-
"""正测试：三模式切换（适配 TickTable / RuntimeModeModule）。

覆盖：
- RuntimeModeModule.switch_mode 在 live / replay / simulation 三模式间切换
- 切换时发布 ModeChanged 事件（prev_mode 正确）
- 仿真模式下股票代码用 'fz' 替代验证（fz_stocks fixture）
- TickTable.update 水位线在模式切换后保持正确语义
- 仿真模式 step_simulation 推进虚拟时钟并发布 SimulationStep 事件
- 回放模式 start_replay 发布 ReplayStarted 事件
- 实盘模式收到 TickReceived 后发布 TimeAdvanced 事件
- 仿真速度调节（0.5x~20x 边界裁剪）
- 未知模式切换被拒绝（不发布 ModeChanged）
"""
from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# 可选依赖：导入失败时 skip 整个模块
# ---------------------------------------------------------------------------
try:
    from core.event_bus import (
        EventBus,
        ModeChanged,
        TickReceived,
        TimeAdvanced,
        ReplayStarted,
        ReplayStep,
        SimulationStep,
    )
except ImportError as exc:
    pytest.skip(f"无法导入 core.event_bus: {exc}", allow_module_level=True)

try:
    from core.runtime_mode_module import RuntimeModeModule, TickTable
except ImportError as exc:
    pytest.skip(f"无法导入 core.runtime_mode_module: {exc}", allow_module_level=True)


# ---------------------------------------------------------------------------
# 辅助：从 config/runtime_modes.json 加载真实 mode id
# ---------------------------------------------------------------------------
def _mode_ids() -> list:
    """从 ConfigStore 读取 runtime_modes 表的 mode id 列表。"""
    try:
        from core.table_engine import get_global_config_store
        cs = get_global_config_store()
        if cs is None:
            return ["live", "replay", "simulation"]
        table = cs.get_table("runtime_modes")
        modes = table.get("modes", {}) if isinstance(table, dict) else {}
        return list(modes.keys()) if modes else ["live", "replay", "simulation"]
    except Exception:
        return ["live", "replay", "simulation"]


# ============================================================================
# Test 1: 三模式切换正确性
# ============================================================================
class TestThreeModeSwitching:
    """RuntimeModeModule 在 live / replay / simulation 三模式间切换。"""

    def test_initial_mode_is_live(self):
        """RuntimeModeModule 初始模式为 live。"""
        bus = EventBus()
        rmm = RuntimeModeModule(bus=bus)
        assert rmm.current_mode == "live"

    def test_switch_to_simulation(self):
        """切换到 simulation 模式后 current_mode 更新。"""
        bus = EventBus()
        rmm = RuntimeModeModule(bus=bus)
        if "simulation" not in _mode_ids():
            pytest.skip("runtime_modes 配置不含 simulation")
        rmm.switch_mode("simulation")
        assert rmm.current_mode == "simulation"

    def test_switch_to_replay(self):
        """切换到 replay 模式后 current_mode 更新。"""
        bus = EventBus()
        rmm = RuntimeModeModule(bus=bus)
        if "replay" not in _mode_ids():
            pytest.skip("runtime_modes 配置不含 replay")
        rmm.switch_mode("replay")
        assert rmm.current_mode == "replay"

    def test_switch_back_to_live(self):
        """simulation → live 切换。"""
        bus = EventBus()
        rmm = RuntimeModeModule(bus=bus)
        if "simulation" not in _mode_ids() or "live" not in _mode_ids():
            pytest.skip("runtime_modes 配置不完整")
        rmm.switch_mode("simulation")
        rmm.switch_mode("live")
        assert rmm.current_mode == "live"


# ============================================================================
# Test 2: 模式切换发布 ModeChanged 事件
# ============================================================================
class TestModeChangedEvent:
    """switch_mode 发布 ModeChanged 事件，承载 prev_mode。"""

    def test_switch_publishes_mode_changed(self):
        """切换模式时发布 ModeChanged 事件。"""
        bus = EventBus()
        received = []
        bus.subscribe(ModeChanged, lambda ev: received.append(ev))
        rmm = RuntimeModeModule(bus=bus)
        if "simulation" not in _mode_ids():
            pytest.skip("runtime_modes 配置不含 simulation")

        rmm.switch_mode("simulation")

        assert len(received) == 1
        assert received[0].mode_id == "simulation"
        assert received[0].prev_mode == "live"

    def test_prev_mode_chain(self):
        """连续切换时 prev_mode 链正确。"""
        bus = EventBus()
        received = []
        bus.subscribe(ModeChanged, lambda ev: received.append(ev))
        rmm = RuntimeModeModule(bus=bus)
        modes = _mode_ids()
        if "replay" not in modes or "simulation" not in modes:
            pytest.skip("runtime_modes 配置不完整")

        rmm.switch_mode("replay")
        rmm.switch_mode("simulation")

        assert len(received) == 2
        assert received[0].prev_mode == "live"
        assert received[1].prev_mode == "replay"

    def test_unknown_mode_no_event(self):
        """未知 mode_id 不发布 ModeChanged 事件。"""
        bus = EventBus()
        received = []
        bus.subscribe(ModeChanged, lambda ev: received.append(ev))
        rmm = RuntimeModeModule(bus=bus)

        rmm.switch_mode("nonexistent_mode_xyz")
        assert len(received) == 0
        assert rmm.current_mode == "live"


# ============================================================================
# Test 3: 仿真模式下股票代码用 'fz' 替代验证
# ============================================================================
class TestSimulationFzStockCode:
    """仿真模式下所有股票代码必须以 'fz' 前缀替代原市场代码。"""

    def test_fz_stocks_factory(self, fz_stocks):
        """fz_stocks 工厂生成 fz 前缀代码。"""
        codes = fz_stocks(10)
        assert len(codes) == 10
        assert all(c.startswith("fz") for c in codes)
        assert codes[0] == "fz000001"

    def test_fz_codes_used_in_tick_received(self, fz_stocks):
        """仿真模式下 TickReceived 事件的 code 字段使用 fz 前缀。"""
        codes = fz_stocks(3)
        for code in codes:
            ev = TickReceived(tick_data={"code": code}, code=code, ts=34500.0)
            assert ev.code.startswith("fz")

    def test_fz_codes_not_real_market(self, fz_stocks):
        """fz 代码不包含真实市场前缀（SH/SZ）。"""
        codes = fz_stocks(100)
        for c in codes:
            assert not c.startswith("SH")
            assert not c.startswith("SZ")
            assert not c.startswith("sh")
            assert not c.startswith("sz")


# ============================================================================
# Test 4: TickTable 模式切换同步
# ============================================================================
class TestTickTableModeSwitching:
    """TickTable 水位线在模式切换后保持正确语义。"""

    def test_tick_table_update_returns_true_on_new_data(self, tick_table):
        """新数据 update 返回 True，hash 更新。"""
        data = {"fz000001": {"price": 10.0, "_ts": 34500.0}}
        old_hash = tick_table.hash
        result = tick_table.update(data)
        assert result is True
        assert tick_table.hash != old_hash
        assert tick_table.get("fz000001")["price"] == 10.0

    def test_tick_table_update_returns_false_on_same_data(self, tick_table):
        """相同数据 update 返回 False（水位线未涨）。"""
        data = {"fz000001": {"price": 10.0, "_ts": 34500.0}}
        tick_table.update(data)
        old_ts = tick_table.ts
        result = tick_table.update(data)
        assert result is False
        assert tick_table.ts == old_ts

    def test_tick_table_ts_monotonic(self, tick_table):
        """水位线 ts 单调递增。"""
        tick_table.update({"fz000001": {"price": 10.0}})
        ts1 = tick_table.ts
        import time as _time
        _time.sleep(0.001)
        tick_table.update({"fz000001": {"price": 11.0}})
        ts2 = tick_table.ts
        assert ts2 > ts1

    def test_tick_table_snapshot(self, tick_table):
        """snapshot 返回全部数据的浅拷贝。"""
        tick_table.update({"fz000001": {"price": 10.0}, "fz000002": {"price": 20.0}})
        snap = tick_table.snapshot()
        assert "fz000001" in snap
        assert "fz000002" in snap
        # 浅拷贝：修改 snap 不影响原 data
        snap["fz000001"]["price"] = 999.0
        assert tick_table.get("fz000001")["price"] == 999.0  # shallow copy

    def test_tick_table_get_missing_code(self, tick_table):
        """get 未命中 code 返回空 dict。"""
        assert tick_table.get("nonexistent") == {}


# ============================================================================
# Test 5: 仿真模式 step_simulation 推进虚拟时钟
# ============================================================================
class TestSimulationStepAdvance:
    """仿真模式 step_simulation 推进虚拟时钟并发布 SimulationStep。"""

    def test_step_simulation_publishes_event(self):
        """simulation 模式下 step_simulation 发布 SimulationStep 事件。"""
        bus = EventBus()
        received = []
        bus.subscribe(SimulationStep, lambda ev: received.append(ev))
        rmm = RuntimeModeModule(bus=bus)
        if "simulation" not in _mode_ids():
            pytest.skip("runtime_modes 配置不含 simulation")
        rmm.switch_mode("simulation")

        rmm.step_simulation(step_idx=0)

        assert len(received) == 1
        assert received[0].step["step_idx"] == 0
        assert received[0].step["virtual_ts"] > 0

    def test_step_simulation_advances_virtual_clock(self):
        """step_simulation 推进虚拟时钟。"""
        bus = EventBus()
        rmm = RuntimeModeModule(bus=bus)
        if "simulation" not in _mode_ids():
            pytest.skip("runtime_modes 配置不含 simulation")
        rmm.switch_mode("simulation")

        clock_before = rmm.virtual_clock
        rmm.step_simulation(step_idx=0)
        clock_after = rmm.virtual_clock
        assert clock_after > clock_before

    def test_step_simulation_noop_in_live_mode(self):
        """live 模式下 step_simulation 不发布事件。"""
        bus = EventBus()
        received = []
        bus.subscribe(SimulationStep, lambda ev: received.append(ev))
        rmm = RuntimeModeModule(bus=bus)
        # 默认 live 模式
        rmm.step_simulation(step_idx=0)
        assert len(received) == 0


# ============================================================================
# Test 6: 回放模式 start_replay 发布 ReplayStarted
# ============================================================================
class TestReplayStart:
    """回放模式 start_replay 发布 ReplayStarted 事件。"""

    def test_start_replay_publishes_event(self):
        """replay 模式下 start_replay 发布 ReplayStarted。"""
        bus = EventBus()
        received = []
        bus.subscribe(ReplayStarted, lambda ev: received.append(ev))
        rmm = RuntimeModeModule(bus=bus)
        if "replay" not in _mode_ids():
            pytest.skip("runtime_modes 配置不含 replay")
        rmm.switch_mode("replay")

        rmm.start_replay(
            session_id="test-session",
            start_ts=34500.0,
            end_ts=45000.0,
            codes=["fz000001", "fz000002"],
        )

        assert len(received) == 1
        session = received[0].session
        assert session["session_id"] == "test-session"
        assert session["codes"] == ["fz000001", "fz000002"]

    def test_start_replay_noop_in_live_mode(self):
        """live 模式下 start_replay 不发布事件。"""
        bus = EventBus()
        received = []
        bus.subscribe(ReplayStarted, lambda ev: received.append(ev))
        rmm = RuntimeModeModule(bus=bus)

        rmm.start_replay("s1", 0.0, 100.0, ["fz000001"])
        assert len(received) == 0

    def test_step_replay_publishes_replay_step(self):
        """replay 模式下 step_replay 发布 ReplayStep 事件。"""
        bus = EventBus()
        received = []
        bus.subscribe(ReplayStep, lambda ev: received.append(ev))
        rmm = RuntimeModeModule(bus=bus)
        if "replay" not in _mode_ids():
            pytest.skip("runtime_modes 配置不含 replay")
        rmm.switch_mode("replay")
        rmm.start_replay("s1", 0.0, 100.0, ["fz000001"])

        rmm.step_replay(step_idx=0)

        assert len(received) == 1
        assert received[0].step["step_idx"] == 0


# ============================================================================
# Test 7: 实盘模式 TimeAdvanced 事件
# ============================================================================
class TestLiveTimeAdvanced:
    """实盘模式收到 TickReceived 后发布 TimeAdvanced 事件。"""

    def test_tick_received_publishes_time_advanced(self):
        """live 模式下 TickReceived 触发 TimeAdvanced。"""
        bus = EventBus()
        received = []
        bus.subscribe(TimeAdvanced, lambda ev: received.append(ev))
        rmm = RuntimeModeModule(bus=bus)
        # 默认 live 模式

        ev = TickReceived(tick_data={"code": "fz000001"}, code="fz000001", ts=1700000000.0)
        bus.publish(ev)

        assert len(received) == 1
        assert received[0].ts == 1700000000.0
        assert received[0].source == "wall_clock"

    def test_time_advanced_not_in_simulation(self):
        """simulation 模式下 TickReceived 不触发 TimeAdvanced。"""
        bus = EventBus()
        received = []
        bus.subscribe(TimeAdvanced, lambda ev: received.append(ev))
        rmm = RuntimeModeModule(bus=bus)
        if "simulation" not in _mode_ids():
            pytest.skip("runtime_modes 配置不含 simulation")
        rmm.switch_mode("simulation")

        bus.publish(TickReceived(tick_data={}, code="fz000001", ts=1700000000.0))
        assert len(received) == 0


# ============================================================================
# Test 8: 仿真速度调节
# ============================================================================
class TestSimulationSpeed:
    """set_simulation_speed 调节仿真速度（0.5x~20x 边界裁剪）。"""

    def test_set_speed_normal(self):
        """正常设置速度 2.0x。"""
        bus = EventBus()
        rmm = RuntimeModeModule(bus=bus)
        rmm.set_simulation_speed(2.0)
        assert rmm.simulation_speed == 2.0

    def test_set_speed_clamp_min(self):
        """速度低于 0.5x 裁剪到 0.5x。"""
        bus = EventBus()
        rmm = RuntimeModeModule(bus=bus)
        rmm.set_simulation_speed(0.1)
        assert rmm.simulation_speed == 0.5

    def test_set_speed_clamp_max(self):
        """速度高于 20x 裁剪到 20x。"""
        bus = EventBus()
        rmm = RuntimeModeModule(bus=bus)
        rmm.set_simulation_speed(100.0)
        assert rmm.simulation_speed == 20.0

    def test_speed_affects_interval(self):
        """速度影响 step_simulation 的 interval。"""
        bus = EventBus()
        rmm = RuntimeModeModule(bus=bus)
        if "simulation" not in _mode_ids():
            pytest.skip("runtime_modes 配置不含 simulation")
        rmm.switch_mode("simulation")
        rmm.set_simulation_speed(2.0)
        clock_before = rmm.virtual_clock
        rmm.step_simulation(step_idx=0)
        # 2x → interval = 0.5 秒
        assert rmm.virtual_clock - clock_before == pytest.approx(0.5)
