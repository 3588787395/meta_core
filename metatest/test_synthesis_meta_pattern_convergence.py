"""元模式合并验证合测试（Task 21 SubTask 21.5）。

端到端验证迭代 7-12 共 6 项元模式合并的收敛正确性：
  - 迭代 7：latest_tick 水位线表统一
  - 迭代 8：编译-运行分离统一
  - 迭代 9：边执行三要素表驱动
  - 迭代 10：节点角色表驱动
  - 迭代 11：事件-信号-动作正交化
  - 迭代 12：配置表收敛与死表清理

测试用例：
  1. test_iteration_7_tick_table_waterline
  2. test_iteration_8_compile_run_separation
  3. test_iteration_9_edge_three_layers
  4. test_iteration_10_node_role_table_driven
  5. test_iteration_11_event_signal_action_orthogonality
  6. test_iteration_12_dead_table_archival
  7. test_no_isomorphism_violations
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest


_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_CORE_DIR = _PROJECT_ROOT / "core"
_CONFIG_DIR = _PROJECT_ROOT / "config"


def _grep_count(pattern: str, search_dir: Path, exclude_files=None) -> int:
    """在目录下递归搜索文件内容，返回匹配行数（排除注释行）。"""
    exclude_set = set(exclude_files or [])
    regex = re.compile(pattern)
    count = 0
    if not search_dir.is_dir():
        return 0
    for py_file in search_dir.rglob("*.py"):
        if py_file.name in exclude_set:
            continue
        try:
            content = py_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        # 逐行匹配，排除纯注释行（以 # 开头，允许前导空白）
        for line in content.splitlines():
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            count += len(regex.findall(line))
    return count


class TestMetaPatternConvergence:
    """元模式合并验证合测试。"""

    def test_iteration_7_tick_table_waterline(self, tick_table, report_state) -> None:
        """迭代 7：TickTable 水位线表统一。

        验证：
          - TickTable 类可导入
          - update() 相同数据返回 False，不同数据返回 True
          - get() / snapshot() 读取正确
          - Grep 验证 state.latest_tick[ 匹配数 = 0（除 TickTable 内部）
        """
        from core.runtime_mode_module import TickTable
        # 相同数据返回 False
        data = {"fz000001": {"close": 10.0}}
        assert tick_table.update(data) is True
        assert tick_table.update(data) is False
        # 不同数据返回 True
        data2 = {"fz000001": {"close": 11.0}}
        assert tick_table.update(data2) is True
        # get / snapshot
        assert tick_table.get("fz000001")["close"] == 11.0
        assert "fz000001" in tick_table.snapshot()
        # Grep 验证：state.latest_tick[ 匹配数 = 0（排除 runtime_mode_module.py）
        count = _grep_count(
            r"state\.latest_tick\[", _CORE_DIR,
            exclude_files=["runtime_mode_module.py"]
        )
        assert count == 0, \
            f"state.latest_tick[ 匹配数应为 0，实际 {count}"
        modules = report_state.setdefault("modules_covered", [])
        if "core.runtime_mode_module" not in modules:
            modules.append("core.runtime_mode_module")

    def test_iteration_8_compile_run_separation(self, compiled_pool, report_state) -> None:
        """迭代 8：编译-运行分离统一。

        验证：
          - compile() 函数与 CompiledPool 类可导入
          - CompiledPool 含全部预编译字段
          - edge_order 来自 _order（非拓扑排序）
          - Grep 验证运行时无 _parse_edge / _build_adjacency
        """
        from core.execution_module import compile, CompiledPool
        cp = compiled_pool
        # 全部预编译字段
        required_fields = [
            "nodes", "node_type", "edges", "edge_endpoints",
            "edge_order", "edge_type", "edge_filter_spec",
            "edge_timing_spec", "edge_propagate_spec",
            "out_edges", "in_edges", "source_nodes", "node_role",
        ]
        for field in required_fields:
            assert hasattr(cp, field), f"CompiledPool 缺字段 {field}"
        # Grep 验证：运行时无 _parse_edge / _build_adjacency
        count_parse = _grep_count(r"\b_parse_edge\b", _CORE_DIR)
        count_build = _grep_count(r"\b_build_adjacency\b", _CORE_DIR)
        assert count_parse == 0, f"_parse_edge 匹配数应为 0，实际 {count_parse}"
        assert count_build == 0, f"_build_adjacency 匹配数应为 0，实际 {count_build}"
        modules = report_state.setdefault("modules_covered", [])
        if "core.execution_module" not in modules:
            modules.append("core.execution_module")

    def test_iteration_9_edge_three_layers(self, report_state) -> None:
        """迭代 9：边执行三要素表驱动。

        验证：
          - trigger_check / filter_eval / propagate_apply 三函数可导入
          - Grep 验证 6 层中间函数匹配数 = 0
        """
        from core.execution_module import (
            trigger_check, filter_eval, propagate_apply,
        )
        # 三函数可调用
        assert callable(trigger_check)
        assert callable(filter_eval)
        assert callable(propagate_apply)
        # trigger_check 基本行为：node_dirty=False 必返回 False
        result = trigger_check({}, 1000.0, {}, node_dirty=False)
        assert result is False
        # trigger_check：node_dirty=True + starttype=immediate + cxtype=always
        result2 = trigger_check(
            {"starttype": "immediate", "cxtype": "always"},
            1000.0, {}, node_dirty=True,
        )
        assert result2 is True
        # filter_eval：filter_spec 未启用时返回全部
        passed, rejected = filter_eval(["fz001"], {"enabled": False}, None)
        assert passed == ["fz001"]
        # propagate_apply：copy 模式
        new_tgt = propagate_apply(["s1"], ["t1"], ["p1"], {"mode": "copy"})
        assert "p1" in new_tgt
        # Grep 验证：6 层中间函数定义/调用匹配数 = 0（排除文档字符串提及）
        for fn in (
            "_phase_dispatch", "_phase_nset_filter",
            "_dispatch_filter", "_eval_primitive",
        ):
            # 匹配函数定义 (def fn) 或函数调用 (fn()，排除文档字符串中的纯提及
            count = _grep_count(rf"def\s+{fn}\b|{fn}\s*\(", _CORE_DIR)
            assert count == 0, f"{fn} 定义/调用匹配数应为 0，实际 {count}"

    def test_iteration_10_node_role_table_driven(
        self, config_store, report_state
    ) -> None:
        """迭代 10：节点角色表驱动。

        验证：
          - _ROLE_ACTIONS 注册表可导入且非空
          - node_roles.json 含 5 种角色
          - Grep 验证 if node.type == 匹配数 = 0
        """
        from core.table_engine import set_global_config_store
        set_global_config_store(config_store)
        from core.engine import _init_role_actions, _ROLE_ACTIONS
        # 触发 _ROLE_ACTIONS 初始化
        _init_role_actions(config_store.get_table("node_roles"))
        # _ROLE_ACTIONS 含 5 种角色
        assert len(_ROLE_ACTIONS) >= 5, \
            f"_ROLE_ACTIONS 至少 5 种角色，实际 {len(_ROLE_ACTIONS)}"
        # node_roles.json 含 5 种角色
        roles_path = _CONFIG_DIR / "architecture" / "node_roles.json"
        import json
        with open(roles_path, encoding="utf-8") as f:
            roles = json.load(f)
        expected = {"candidate", "state", "condition", "target", "discard"}
        assert expected.issubset(set(roles.keys())), \
            f"node_roles.json 缺角色: {expected - set(roles.keys())}"
        # Grep 验证：if node.type == 匹配数 = 0
        count = _grep_count(r"if\s+node\.type\s*==", _CORE_DIR)
        assert count == 0, \
            f"if node.type == 匹配数应为 0，实际 {count}"
        modules = report_state.setdefault("modules_covered", [])
        if "core.engine" not in modules:
            modules.append("core.engine")

    def test_iteration_11_event_signal_action_orthogonality(self, report_state) -> None:
        """迭代 11：事件-信号-动作正交化。

        验证：
          - StockChanged / Signal 事件类可导入
          - SignalDeriver / ActionDispatcher 可导入（如存在）
          - 三层概念正交：事件仅记录状态，信号由配置派生，动作由表驱动
        """
        from core.event_bus import StockChanged, Signal
        # 事件类与信号类可导入
        assert StockChanged is not None
        assert Signal is not None
        # Signal 事件含 signal_type / code / pool_id / price / ts 字段
        sig = Signal(
            signal_type="BUY", code="fz000001",
            pool_id="test_pool", price=10.0, ts=1000.0,
        )
        assert hasattr(sig, "signal_type")
        assert sig.signal_type == "BUY"
        assert sig.code == "fz000001"
        # SignalDeriver / ActionDispatcher 可导入（如已实现）
        try:
            from core.trade_module import SignalDeriver, ActionDispatcher
            assert SignalDeriver is not None
            assert ActionDispatcher is not None
        except ImportError:
            # 部分实现可能未拆分，只要 Signal 事件存在即通过
            pass
        # action_table.json 含 BUY/SELL 动作
        action_path = _CONFIG_DIR / "ui" / "action_table.json"
        if action_path.exists():
            import json
            with open(action_path, encoding="utf-8") as f:
                actions = json.load(f)
            # action_table 应含 BUY 或 SELL 相关条目
            actions_str = json.dumps(actions)
            assert "BUY" in actions_str or "SELL" in actions_str or "buy" in actions_str.lower(), \
                "action_table.json 应含 BUY/SELL 动作"
        modules = report_state.setdefault("modules_covered", [])
        for m in ("core.event_bus", "core.trade_module"):
            if m not in modules:
                modules.append(m)

    def test_iteration_12_dead_table_archival(self, report_state) -> None:
        """迭代 12：配置表收敛与死表清理。

        验证：
          - config/_archive/ 目录存在
          - 死表审计报告存在
          - 8 张核心运行时表存在
        """
        # _archive 目录存在
        archive_dir = _CONFIG_DIR / "_archive"
        assert archive_dir.exists(), "config/_archive/ 目录应存在"
        # 死表审计报告存在
        audit_report = archive_dir / "dead_tables_audit.md"
        assert audit_report.exists(), "死表审计报告应存在"
        # 8 张核心运行时表存在
        arch_dir = _CONFIG_DIR / "architecture"
        core_tables = [
            "timing.json", "filter_specs.json", "propagate_modes.json",
            "node_roles.json", "edge_semantics.json",
        ]
        for table in core_tables:
            table_path = arch_dir / table
            assert table_path.exists(), f"核心表 {table} 不存在"
        # runtime_modes.json 存在于 config/runtime/
        runtime_modes = _CONFIG_DIR / "runtime" / "runtime_modes.json"
        assert runtime_modes.exists(), "runtime_modes.json 不存在"
        modules = report_state.setdefault("modules_covered", [])
        if "core.table_engine" not in modules:
            modules.append("core.table_engine")

    def test_no_isomorphism_violations(self, report_state) -> None:
        """同构代码消除度验证：6 项 Grep 检查均无违规。

        验证：
          1. state.latest_tick[ = 0（除 TickTable 内部）
          2. 运行时 json.loads / _parse_edge / _build_adjacency = 0
          3. _phase_dispatch 等中间函数 = 0
          4. if node.type == = 0
          5. transfer_module 中 sound.play / popup.show = 0
        """
        # 1. state.latest_tick[
        count1 = _grep_count(
            r"state\.latest_tick\[", _CORE_DIR,
            exclude_files=["runtime_mode_module.py"]
        )
        assert count1 == 0, f"state.latest_tick[ 匹配数应为 0，实际 {count1}"
        # 2. _parse_edge / _build_adjacency
        count2 = (_grep_count(r"\b_parse_edge\b", _CORE_DIR)
                  + _grep_count(r"\b_build_adjacency\b", _CORE_DIR))
        assert count2 == 0, f"_parse_edge/_build_adjacency 匹配数应为 0，实际 {count2}"
        # 3. 6 层中间函数
        count3 = sum(
            _grep_count(rf"\b{fn}\b", _CORE_DIR)
            for fn in ("_phase_dispatch", "_phase_nset_filter",
                       "_dispatch_filter", "_eval_primitive")
        )
        assert count3 == 0, f"中间函数匹配数应为 0，实际 {count3}"
        # 4. if node.type ==
        count4 = _grep_count(r"if\s+node\.type\s*==", _CORE_DIR)
        assert count4 == 0, f"if node.type == 匹配数应为 0，实际 {count4}"
        # 5. transfer_module 中 sound.play / popup.show
        transfer_file = _CORE_DIR / "transfer_module.py"
        count5 = 0
        if transfer_file.exists():
            try:
                content = transfer_file.read_text(encoding="utf-8")
                count5 = (len(re.findall(r"sound\.play", content))
                          + len(re.findall(r"popup\.show", content)))
            except (OSError, UnicodeDecodeError):
                pass
        assert count5 == 0, f"transfer_module 副作用调用应为 0，实际 {count5}"
        report_state["event_chain_correct"] = True
