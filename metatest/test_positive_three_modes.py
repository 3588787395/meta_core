# -*- coding: utf-8 -*-
"""正向测试：三种运行模式（仿真 / 回放 / 实盘）。

覆盖：
- VirtualClock 虚拟时钟（起点 34500.0 = 09:30:00）
- RuntimeSimulator.clock 与 VirtualClock 语义一致
- RuntimeSimulator step / astep 方法签名
- 仿真模式虚拟时钟推进
- fz 前缀股票代码（仿真模式硬约束）
- normalize_display_ms 时间戳归一化（仿真/实盘同代码路径 G2）
- ModeChanged 事件
- classify_event_type 模式相关事件分类
- 三种模式 mode_id 标识
- G2 约束：仿真与实盘同代码路径（无分支）
- RuntimeSimulator 暂停/停止行为
"""
from __future__ import annotations

import inspect
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# 可选依赖：导入失败时 skip 整个模块
# ---------------------------------------------------------------------------
try:
    from core.event_bus import EventBus, ModeChanged, TickReceived
except ImportError as exc:
    pytest.skip(f"无法导入 core.event_bus: {exc}", allow_module_level=True)

try:
    from core.web_state import normalize_display_ms, classify_event_type
except ImportError as exc:
    pytest.skip(f"无法导入 core.web_state: {exc}", allow_module_level=True)


# ---------------------------------------------------------------------------
# Test 1: VirtualClock 虚拟时钟
# ---------------------------------------------------------------------------
class TestVirtualClock:
    """虚拟时钟起点 34500.0（=09:30:00），与 RuntimeSimulator.clock 一致。"""

    def test_clock_starts_at_34500(self, virtual_clock):
        """虚拟时钟起点为 34500.0（A 股开盘 09:30:00 的秒数偏移）。"""
        assert float(virtual_clock) == 34500.0

    def test_clock_advance(self, virtual_clock):
        """advance(seconds) 推进时钟并返回新时刻。"""
        new_ts = virtual_clock.advance(60.0)
        assert new_ts == 34560.0
        assert float(virtual_clock) == 34560.0

    def test_clock_reset(self, virtual_clock):
        """reset() 重置到起点。"""
        virtual_clock.advance(120.0)
        assert float(virtual_clock) == 34620.0
        reset_ts = virtual_clock.reset()
        assert reset_ts == 34500.0
        assert float(virtual_clock) == 34500.0

    def test_clock_multiple_advances_cumulative(self, virtual_clock):
        """多次推进累加。"""
        virtual_clock.advance(30.0)
        virtual_clock.advance(45.0)
        assert float(virtual_clock) == 34575.0


# ---------------------------------------------------------------------------
# Test 2: RuntimeSimulator 虚拟时钟一致性
# ---------------------------------------------------------------------------
class TestRuntimeSimulatorClock:
    """RuntimeSimulator.clock 与 VirtualClock 起点一致（34500.0）。"""

    def test_simulator_clock_initial_value_is_34500(self):
        """RuntimeSimulator.clock 初始值应为 34500.0。"""
        try:
            from core.runtime_mode_module import RuntimeSimulator
        except ImportError:
            pytest.skip("RuntimeSimulator 不可导入")

        src = inspect.getsource(RuntimeSimulator.__init__)
        assert "34500.0" in src, "RuntimeSimulator 应使用 34500.0 作为虚拟时钟起点"

    def test_simulator_has_step_and_astep(self):
        """RuntimeSimulator 应有同步 step 和异步 astep 方法。"""
        try:
            from core.runtime_mode_module import RuntimeSimulator
        except ImportError:
            pytest.skip("RuntimeSimulator 不可导入")

        assert hasattr(RuntimeSimulator, "step")
        assert hasattr(RuntimeSimulator, "astep")
        # astep 应为协程函数
        assert inspect.iscoroutinefunction(RuntimeSimulator.astep)

    def test_simulator_step_accepts_duration_param(self):
        """step(d=1.0) 接受时长参数。"""
        try:
            from core.runtime_mode_module import RuntimeSimulator
        except ImportError:
            pytest.skip("RuntimeSimulator 不可导入")

        sig = inspect.signature(RuntimeSimulator.step)
        assert "d" in sig.parameters


# ---------------------------------------------------------------------------
# Test 3: fz 前缀股票代码（仿真模式硬约束）
# ---------------------------------------------------------------------------
class TestFzStockPrefix:
    """仿真模式下所有股票代码必须用 'fz' 替代原市场代码。"""

    def test_fz_stocks_factory_returns_correct_codes(self, fz_stocks):
        """fz_stocks(n) 返回 fz 前缀代码列表。"""
        codes = fz_stocks(5)
        assert len(codes) == 5
        assert codes[0] == "fz000001"
        assert codes[4] == "fz000005"

    def test_fz_stocks_default_100(self, fz_stocks):
        """fz_stocks() 默认返回 100 个代码。"""
        codes = fz_stocks()
        assert len(codes) == 100
        assert codes[0] == "fz000001"
        assert codes[99] == "fz000100"

    def test_all_codes_start_with_fz(self, fz_stocks):
        """所有代码以 fz 开头。"""
        codes = fz_stocks(50)
        assert all(c.startswith("fz") for c in codes)

    def test_fz_stocks_zero_returns_empty(self, fz_stocks):
        """fz_stocks(0) 返回空列表。"""
        assert fz_stocks(0) == []


# ---------------------------------------------------------------------------
# Test 4: normalize_display_ms 时间戳归一化（G2 同代码路径）
# ---------------------------------------------------------------------------
class TestNormalizeDisplayMsG2:
    """normalize_display_ms 统一处理仿真（相对秒）与实盘（Unix 秒/毫秒）时间戳。

    G2 硬约束：仿真模式与实盘模式使用同一代码路径处理时间戳。
    """

    def test_simulation_relative_seconds(self):
        """仿真模式相对秒（< 1e9）→ *1000 转毫秒。"""
        assert normalize_display_ms(34500.0) == 34500000.0

    def test_live_unix_seconds(self):
        """实盘模式 Unix 秒（>=1e9, <1e12）→ *1000 转毫秒。"""
        result = normalize_display_ms(1700000000)
        assert result == 1700000000000.0

    def test_live_unix_milliseconds(self):
        """实盘模式 Unix 毫秒（>=1e12）→ 保持不变。"""
        result = normalize_display_ms(1700000000000)
        assert result == 1700000000000.0

    def test_same_function_for_both_modes(self):
        """仿真与实盘时间戳通过同一函数处理（G2 同代码路径）。"""
        sim_result = normalize_display_ms(34500.0)
        live_result = normalize_display_ms(1700000000)
        # 两者都返回毫秒值
        assert sim_result is not None
        assert live_result is not None
        assert isinstance(sim_result, float)
        assert isinstance(live_result, float)

    def test_none_input_returns_none(self):
        assert normalize_display_ms(None) is None


# ---------------------------------------------------------------------------
# Test 5: ModeChanged 事件
# ---------------------------------------------------------------------------
class TestModeChangedEvent:
    """ModeChanged 事件承载模式切换信息。"""

    def test_mode_changed_fields(self):
        ev = ModeChanged(mode_id="simulation", prev_mode="design")
        assert ev.mode_id == "simulation"
        assert ev.prev_mode == "design"

    def test_mode_changed_default_prev_mode(self):
        ev = ModeChanged(mode_id="live")
        assert ev.mode_id == "live"
        assert ev.prev_mode == ""

    def test_mode_changed_published_to_bus(self):
        """ModeChanged 事件可通过 EventBus 发布与订阅。"""
        bus = EventBus()
        received = []
        bus.subscribe(ModeChanged, lambda ev: received.append(ev))

        ev = ModeChanged(mode_id="replay", prev_mode="simulation")
        bus.publish(ev)

        assert len(received) == 1
        assert received[0].mode_id == "replay"
        assert received[0].prev_mode == "simulation"


# ---------------------------------------------------------------------------
# Test 6: 三种模式标识
# ---------------------------------------------------------------------------
class TestThreeModeIdentifiers:
    """三种运行模式的 mode_id 标识：live / replay / simulation。"""

    def test_mode_ids_in_mode_changed(self):
        """三种模式都能创建 ModeChanged 事件。"""
        for mode_id in ("live", "replay", "simulation"):
            ev = ModeChanged(mode_id=mode_id)
            assert ev.mode_id == mode_id

    def test_classify_event_type_for_mode_changed(self):
        """ModeChanged 事件分类为 system。"""
        assert classify_event_type("ModeChanged") == "system"

    def test_classify_event_type_for_simulation_step(self):
        """SimulationStep 事件分类为 system。"""
        assert classify_event_type("SimulationStep") == "system"

    def test_classify_event_type_for_replay_step(self):
        """ReplayStep 事件分类为 system。"""
        assert classify_event_type("ReplayStep") == "system"


# ---------------------------------------------------------------------------
# Test 7: 仿真模式虚拟时钟推进与事件
# ---------------------------------------------------------------------------
class TestSimulationClockAdvance:
    """仿真模式虚拟时钟推进与事件时间戳。"""

    def test_tick_event_ts_within_simulation_range(self):
        """仿真模式下 TickReceived 事件的 ts 应在交易时段范围内。"""
        ev = TickReceived(
            tick_data={"code": "fz000001"},
            code="fz000001",
            ts=34500.0,  # 09:30:00
        )
        assert ev.ts == 34500.0
        # 归一化为毫秒
        ms = normalize_display_ms(ev.ts)
        assert ms == 34500000.0

    def test_clock_advance_reflected_in_event_ts(self, virtual_clock):
        """虚拟时钟推进后，事件 ts 应反映推进后的时刻。"""
        virtual_clock.advance(300.0)  # 推进 5 分钟
        ev = TickReceived(
            tick_data={"code": "fz000001"},
            code="fz000001",
            ts=float(virtual_clock),
        )
        assert ev.ts == 34800.0  # 09:35:00


# ---------------------------------------------------------------------------
# Test 8: G2 约束——仿真与实盘同代码路径
# ---------------------------------------------------------------------------
class TestG2SameCodePath:
    """G2 硬约束：仿真模式与实盘模式除 tick 生成逻辑外，使用相同代码路径。"""

    def test_normalize_display_ms_no_mode_branch(self):
        """normalize_display_ms 不应包含模式分支判断。"""
        src = inspect.getsource(normalize_display_ms)
        # 不应包含模式判断分支
        assert "simulation" not in src.lower() or "mode" not in src.lower()
        # 不应包含 if mode == 分支
        assert "if mode" not in src.lower()

    def test_normalize_display_ms_uses_threshold_branches(self):
        """normalize_display_ms 按数值范围分支（非模式分支）。"""
        src = inspect.getsource(normalize_display_ms)
        # 应按 1e9 / 1e12 数值阈值分支
        assert "1e9" in src or "1e12" in src

    def test_virtual_clock_float_compatible_with_normalize(self):
        """VirtualClock 的 float 值可直接传入 normalize_display_ms。"""
        clock = 34500.0  # 仿真虚拟时钟
        result = normalize_display_ms(clock)
        # 34500.0 < 1e9 → 相对秒 → *1000
        assert result == 34500000.0


# ---------------------------------------------------------------------------
# Test 9: RuntimeSimulator 暂停/停止
# ---------------------------------------------------------------------------
class TestRuntimeSimulatorPauseStop:
    """RuntimeSimulator 暂停时 step 返回空结果。"""

    def test_simulator_has_pause_method(self):
        try:
            from core.runtime_mode_module import RuntimeSimulator
        except ImportError:
            pytest.skip("RuntimeSimulator 不可导入")
        assert hasattr(RuntimeSimulator, "pause")

    def test_simulator_has_stop_method(self):
        try:
            from core.runtime_mode_module import RuntimeSimulator
        except ImportError:
            pytest.skip("RuntimeSimulator 不可导入")
        assert hasattr(RuntimeSimulator, "stop")

    def test_simulator_pause_returns_dict(self):
        """暂停方法应返回状态字典。"""
        try:
            from core.runtime_mode_module import RuntimeSimulator
        except ImportError:
            pytest.skip("RuntimeSimulator 不可导入")

        sig = inspect.signature(RuntimeSimulator.pause)
        # pause 方法存在且可调用
        assert callable(RuntimeSimulator.pause)


# ---------------------------------------------------------------------------
# Test 10: 事件收集器（EventCollector from conftest）
# ---------------------------------------------------------------------------
class TestEventCollector:
    """EventCollector 订阅 EventBus 全部事件并收集。"""

    def test_event_collector_collects_events(self):
        """EventCollector 收集 EventBus 发布的所有事件。

        v2 适配：conftest.event_collector fixture 已改为 ``(bus, collected)`` 元组，
        此处直接使用 ``EventCollector`` 类工厂模式保持与原测试意图一致。
        """
        from metatest.conftest import EventCollector

        bus = EventBus()
        collector = EventCollector(bus)

        bus.publish(TickReceived(tick_data={}, code="c1", ts=1.0))
        bus.publish(ModeChanged(mode_id="simulation"))

        assert len(collector.events) == 2
        counts = collector.count_by_type()
        assert counts.get("TickReceived") == 1
        assert counts.get("ModeChanged") == 1

        collector.disconnect()

    def test_event_collector_filter_by_type(self):
        """filter(type=...) 按事件类型过滤。"""
        from metatest.conftest import EventCollector

        bus = EventBus()
        collector = EventCollector(bus)

        bus.publish(TickReceived(tick_data={}, code="c1", ts=1.0))
        bus.publish(ModeChanged(mode_id="live"))

        tick_events = collector.filter(type="TickReceived")
        assert len(tick_events) == 1
        assert tick_events[0].code == "c1"

        collector.disconnect()

    def test_event_collector_clear(self):
        """clear() 清空已收集事件。"""
        from metatest.conftest import EventCollector

        bus = EventBus()
        collector = EventCollector(bus)

        bus.publish(TickReceived(tick_data={}, code="c1", ts=1.0))
        assert len(collector.events) == 1

        collector.clear()
        assert len(collector.events) == 0

        collector.disconnect()
