"""三模式合测试（Task 21 SubTask 21.2）。

端到端验证仿真/回放/实盘三模式：股票代码归一化、数据源使用、
模式切换、同代码路径。复用 conftest.py 的 fixture。

硬约束「仿真模式与实盘模式除 tick 生成逻辑外，其他处理流程必须使用
相同代码，禁止分别处理」。

测试用例：
  1. test_simulation_uses_fz_prefix
  2. test_runtime_modes_config_has_three_modes
  3. test_mode_switch_publishes_mode_changed_event
  4. test_same_compile_path_for_all_modes
  5. test_mode_changed_event_has_correct_fields
  6. test_runtime_mode_module_switch_to_invalid_mode_no_event
  7. test_kline_replay_engine_initial_state
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import pytest


_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_RUNTIME_MODES_PATH = _PROJECT_ROOT / "config" / "runtime" / "runtime_modes.json"


def _load_json(path: Path) -> Dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


class TestThreeModesSynthesis:
    """三模式合测试。"""

    def test_simulation_uses_fz_prefix(self, fz_stocks, report_state) -> None:
        """仿真模式下所有股票代码必须用 'fz' 替代原市场代码。

        验证硬约束「仿真模式下所有股票代码必须用 'fz' 替代原市场代码」。
        """
        codes = fz_stocks(10)
        assert len(codes) == 10
        assert codes[0] == "fz000001"
        assert codes[-1] == "fz000010"
        for code in codes:
            assert code.startswith("fz"), f"仿真模式代码 {code} 必须以 fz 开头"
        # 验证 domain._normalize_to_fz 函数存在
        from core.domain import _normalize_to_fz, is_fz_code
        assert _normalize_to_fz("600000") == "fz600000"
        assert _normalize_to_fz("000001") == "fz000001"
        assert is_fz_code("fz600000") is True
        assert is_fz_code("600000") is False
        assert is_fz_code("fz000001") is True
        modules = report_state.setdefault("modules_covered", [])
        if "core.domain" not in modules:
            modules.append("core.domain")

    def test_runtime_modes_config_has_three_modes(self, report_state) -> None:
        """runtime_modes.json 含 live/replay/simulation 三种模式配置。

        验证三模式表驱动配置完整性。
        """
        cfg = _load_json(_RUNTIME_MODES_PATH)
        modes = cfg.get("modes", {})
        assert "live" in modes, "缺 live 实盘模式"
        assert "replay" in modes, "缺 replay 回放模式"
        assert "simulation" in modes, "缺 simulation 仿真模式"
        assert len(modes) >= 3, f"至少 3 种模式，实际 {len(modes)}"
        # 每种模式必须含关键字段
        for mode_id, mode_cfg in modes.items():
            assert "time_source_id" in mode_cfg, \
                f"{mode_id} 缺 time_source_id"
            assert "data_source_id" in mode_cfg, \
                f"{mode_id} 缺 data_source_id"
            assert "trade_interface_id" in mode_cfg, \
                f"{mode_id} 缺 trade_interface_id"
        # 仿真模式必须用 virtual_clock + mock + paper_trade
        sim_cfg = modes["simulation"]
        assert sim_cfg["time_source_id"] == "virtual_clock"
        assert sim_cfg["data_source_id"] == "mock"
        assert sim_cfg["trade_interface_id"] == "paper_trade"
        # 实盘模式必须用 realtime + live + live_order
        live_cfg = modes["live"]
        assert live_cfg["time_source_id"] == "realtime"
        assert live_cfg["trade_interface_id"] == "live_order"
        # 回放模式必须用 kline_timeline + replay_history + noop
        replay_cfg = modes["replay"]
        assert replay_cfg["time_source_id"] == "kline_timeline"
        assert replay_cfg["trade_interface_id"] == "noop"
        modules = report_state.setdefault("modules_covered", [])
        if "core.table_engine" not in modules:
            modules.append("core.table_engine")

    def test_mode_switch_publishes_mode_changed_event(
        self, event_collector, config_store, report_state
    ) -> None:
        """RuntimeModeModule.switch_mode 发布 ModeChanged 事件。

        验证模式切换事件链正确性。
        需先初始化全局 ConfigStore，使 RuntimeModeModule 能加载 runtime_modes.json。
        """
        from core.table_engine import set_global_config_store
        # 初始化全局 ConfigStore（RuntimeModeModule 依赖全局单例加载 runtime_modes）
        set_global_config_store(config_store)
        from core.runtime_mode_module import RuntimeModeModule
        from core.event_bus import ModeChanged, EventBus

        collector = event_collector(EventBus())
        bus = collector._bus
        collected = collector._events
        # 订阅 ModeChanged 事件
        bus.subscribe(ModeChanged, lambda e: collected.append(e))
        rmm = RuntimeModeModule(bus=bus)
        # 切换到仿真模式
        rmm.switch_mode("simulation")
        assert len(collected) >= 1, "switch_mode 应发布 ModeChanged 事件"
        event = collected[-1]
        assert isinstance(event, ModeChanged)
        assert event.mode_id == "simulation"
        # 切换到回放模式
        rmm.switch_mode("replay")
        assert len(collected) >= 2
        event2 = collected[-1]
        assert event2.mode_id == "replay"
        # 切换到实盘模式
        rmm.switch_mode("live")
        assert len(collected) >= 3
        event3 = collected[-1]
        assert event3.mode_id == "live"
        event_types = report_state.setdefault("event_types_seen", [])
        if "ModeChanged" not in event_types:
            event_types.append("ModeChanged")
        modules = report_state.setdefault("modules_covered", [])
        if "core.runtime_mode_module" not in modules:
            modules.append("core.runtime_mode_module")
        if "core.event_bus" not in modules:
            modules.append("core.event_bus")

    def test_same_compile_path_for_all_modes(
        self, compiled_pool, report_state
    ) -> None:
        """三模式共享同一编译路径 compile() -> CompiledPool。

        验证硬约束「仿真模式与实盘模式除 tick 生成逻辑外，其他处理流程
        必须使用相同代码」。
        """
        from core.execution_module import compile, CompiledPool
        # 同一 compile 函数用于所有模式（无模式分支）
        pool_config = {
            "nodes": {
                "src": {"type": "candidate", "label": "源"},
                "tgt": {"type": "target", "label": "目标"},
            },
            "edges": [
                {"id": "e1", "from": "src", "to": "tgt",
                 "params": {"_order": 0, "starttype": 0, "cxtype": 0}},
            ],
        }
        cp = compile(pool_config)
        assert isinstance(cp, CompiledPool)
        # 编译产物无模式相关字段（模式由 runtime_modes.json 表驱动）
        assert not hasattr(cp, "mode_id"), "CompiledPool 不应含模式字段"
        assert not hasattr(cp, "time_source"), "CompiledPool 不应含时间源字段"
        # 编译产物含节点角色映射（与模式无关）
        assert "src" in cp.node_role
        assert "tgt" in cp.node_role
        assert cp.node_role["src"] == "candidate"
        assert cp.node_role["tgt"] == "target"
        modules = report_state.setdefault("modules_covered", [])
        if "core.execution_module" not in modules:
            modules.append("core.execution_module")

    def test_mode_changed_event_has_correct_fields(self, report_state) -> None:
        """ModeChanged 事件含 mode_id 与 prev_mode 字段。

        验证事件结构完整性。
        """
        from core.event_bus import ModeChanged
        # 构造事件并检查字段
        event = ModeChanged(mode_id="simulation", prev_mode="live")
        assert hasattr(event, "mode_id")
        assert hasattr(event, "prev_mode")
        assert event.mode_id == "simulation"
        assert event.prev_mode == "live"
        # 构造回放模式事件
        event2 = ModeChanged(mode_id="replay", prev_mode="simulation")
        assert event2.mode_id == "replay"
        assert event2.prev_mode == "simulation"
        modules = report_state.setdefault("modules_covered", [])
        if "core.event_bus" not in modules:
            modules.append("core.event_bus")

    def test_runtime_mode_module_switch_to_invalid_mode_no_event(
        self, event_collector, config_store, report_state
    ) -> None:
        """切换到无效模式不发布事件（边界保护）。"""
        from core.table_engine import set_global_config_store
        set_global_config_store(config_store)
        from core.runtime_mode_module import RuntimeModeModule
        from core.event_bus import ModeChanged, EventBus

        collector = event_collector(EventBus())
        bus = collector._bus
        collected = collector._events
        bus.subscribe(ModeChanged, lambda e: collected.append(e))
        rmm = RuntimeModeModule(bus=bus)
        initial_count = len(collected)
        # 切换到不存在的模式
        rmm.switch_mode("nonexistent_mode")
        assert len(collected) == initial_count, \
            "切换到无效模式不应发布 ModeChanged 事件"
        # 切换到空字符串也不应发布
        rmm.switch_mode("")
        assert len(collected) == initial_count, \
            "切换到空模式名不应发布 ModeChanged 事件"

    def test_kline_replay_engine_initial_state(self, report_state) -> None:
        """KLineReplayEngine 初始状态正确（回放模式引擎）。

        验证回放模式引擎可实例化，初始状态为暂停、未播放。
        """
        from core.runtime_mode_module import KLineReplayEngine
        from core.engine import PoolEngine

        engine = PoolEngine()
        bus = None
        try:
            from core.event_bus import EventBus
            bus = EventBus()
        except Exception:
            pass
        kre = KLineReplayEngine(meta_engine=engine, bus=bus)
        # 初始状态：暂停、未播放
        assert kre._paused is True, "回放引擎初始应为暂停态"
        assert kre._playing is False, "回放引擎初始未播放"
        assert kre._current_index == -1, "初始索引应为 -1"
        assert kre._total_bars == 0, "初始总 bar 数应为 0"
        assert kre._base_period == "day", "默认基础周期应为 day"
        assert kre._speed == 1.0, "默认速度应为 1.0"
        # mode id 应为 replay
        assert kre._engine._current_mode_id == "replay", \
            "KLineReplayEngine 应设置 mode_id=replay"
        modules = report_state.setdefault("modules_covered", [])
        if "core.runtime_mode_module" not in modules:
            modules.append("core.runtime_mode_module")
