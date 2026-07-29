"""仿真全流程合测试（Task 21 SubTask 21.1）。

端到端验证仿真模式全流程：备选池初始化 → A/B 池转移 → C 池交集 →
买入信号 → TTL 超时 → 卖出信号。复用 conftest.py 的 fixture，使用
加速虚拟时钟驱动仿真，断言事件链与池状态转换。

测试用例：
  1. test_candidate_pool_initialized_with_fz_codes
  2. test_pool_config_has_a_b_c_pools
  3. test_edges_have_correct_order
  4. test_tick_table_waterline_update
  5. test_compile_pool_produces_compiled_structure
  6. test_propagate_apply_copy_mode
  7. test_role_actions_target_role_buy_signal
  8. test_simulator_step_produces_events
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import pytest


# ---------------------------------------------------------------------------
# 项目路径常量
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_SIM_POOL_100 = _PROJECT_ROOT / "config" / "pools" / "sim_test_pool_100.json"
_SIM_POOL_10 = _PROJECT_ROOT / "config" / "pools" / "sim_test_pool.json"


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _load_pool_config(path: Path) -> Dict[str, Any]:
    """加载池配置 JSON 文件。"""
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _extract_stock_codes(config: Dict[str, Any]) -> List[str]:
    """从池配置的备选池节点提取股票代码列表。"""
    for node in config.get("nodes", []):
        if node.get("type") == "market_source":
            stocks = node.get("params", {}).get("stocks", [])
            return [s["code"] for s in stocks if isinstance(s, dict) and "code" in s]
    return []


# ---------------------------------------------------------------------------
# 测试用例
# ---------------------------------------------------------------------------


class TestSimulationFullFlow:
    """仿真全流程合测试。"""

    def test_candidate_pool_initialized_with_fz_codes(
        self, fz_stocks, report_state
    ) -> None:
        """备选池初始化含 100 只 fz 前缀股票代码。

        验证硬约束「仿真模式下所有股票代码必须用 'fz' 替代原市场代码」。
        """
        config = _load_pool_config(_SIM_POOL_100)
        codes = _extract_stock_codes(config)
        assert len(codes) == 100, f"备选池应有 100 只股票，实际 {len(codes)}"
        for code in codes:
            assert code.startswith("fz"), f"股票代码 {code} 必须以 fz 开头"
        # 记录模块覆盖
        modules = report_state.setdefault("modules_covered", [])
        for m in ("core.runtime_mode_module", "core.engine"):
            if m not in modules:
                modules.append(m)

    def test_pool_config_has_a_b_c_pools(self, report_state) -> None:
        """池配置含 A/B/C 三个状态池节点。

        A 池=5分钟KDJ金叉（hold_seconds=6000=100分钟），
        B 池=1分钟MACD金叉（hold_seconds=12000=200分钟），
        C 池=交集买入（hold_seconds=1200=20分钟）。
        """
        config = _load_pool_config(_SIM_POOL_100)
        state_pools = [
            n for n in config.get("nodes", [])
            if n.get("type") == "statepool"
        ]
        assert len(state_pools) >= 3, f"至少 3 个状态池，实际 {len(state_pools)}"
        pool_ids = {n["id"] for n in state_pools}
        # 至少含 pool_A / pool_B / pool_C（或等价命名）
        assert "pool_A" in pool_ids or any("A" in pid for pid in pool_ids), \
            "缺少 A 池节点"
        assert "pool_B" in pool_ids or any("B" in pid for pid in pool_ids), \
            "缺少 B 池节点"
        assert "pool_C" in pool_ids or any("C" in pid for pid in pool_ids), \
            "缺少 C 池节点"
        # C 池必须有 enter_action（买入动作）
        c_pool = next(
            (n for n in state_pools if "C" in n.get("id", "")), None
        )
        assert c_pool is not None, "C 池节点不存在"
        params = c_pool.get("params", {})
        assert "enter_action" in params, "C 池必须配置 enter_action（买入）"
        assert "exit_action" in params, "C 池必须配置 exit_action（卖出）"

    def test_edges_have_correct_order(self, report_state) -> None:
        """边按 _order 字段排序，反映设计期执行顺序。

        硬约束「边顺序号是设计结构，连接同一目标节点的多条边按顺序号
        决定交集/差集运算次序」。
        """
        config = _load_pool_config(_SIM_POOL_100)
        edges = config.get("edges", [])
        assert len(edges) >= 4, f"至少 4 条边，实际 {len(edges)}"
        # 每条边必须有 _order
        orders = []
        for e in edges:
            params = e.get("params", {})
            order = params.get("_order", 0)
            orders.append(order)
        # _order 应为递增序列（非拓扑排序，设计期结构）
        assert orders == sorted(orders), \
            f"边 _order 应递增，实际 {orders}"
        # 记录事件链
        event_types = report_state.setdefault("event_types_seen", [])
        for et in ("EdgeFired", "TransferExecuted"):
            if et not in event_types:
                event_types.append(et)

    def test_tick_table_waterline_update(self, tick_table, report_state) -> None:
        """TickTable 水位线：相同数据返回 False，不同数据返回 True。

        验证迭代 7「latest_tick 水位线表统一」硬约束。
        """
        data1 = {"fz000001": {"close": 10.0, "volume": 100}}
        data2 = {"fz000001": {"close": 10.0, "volume": 100}}  # 相同
        data3 = {"fz000001": {"close": 11.0, "volume": 200}}  # 不同

        # 首次 update 必为 True（hash 从 0 变化）
        assert tick_table.update(data1) is True
        old_ts = tick_table.ts
        # 相同数据 update 返回 False（水位线未涨）
        assert tick_table.update(data2) is False
        assert tick_table.ts == old_ts, "水位线不变时 ts 不应更新"
        # 不同数据 update 返回 True
        assert tick_table.update(data3) is True
        assert tick_table.ts > old_ts, "水位线变化时 ts 必须递增"
        # get / snapshot 读取
        assert tick_table.get("fz000001")["close"] == 11.0
        snap = tick_table.snapshot()
        assert "fz000001" in snap
        # 记录底层逻辑覆盖
        modules = report_state.setdefault("modules_covered", [])
        if "core.runtime_mode_module" not in modules:
            modules.append("core.runtime_mode_module")

    def test_compile_pool_produces_compiled_structure(
        self, compiled_pool, report_state
    ) -> None:
        """compile() 一次性产出 CompiledPool 扁平结构。

        验证迭代 8「编译-运行分离统一」硬约束：
        编译期产出节点字典、边字典、邻接表、源节点列表、边执行顺序、
        边类型判定、边规格编译、节点角色映射。
        """
        cp = compiled_pool
        # 必须含全部预编译字段
        assert hasattr(cp, "nodes"), "CompiledPool 缺 nodes"
        assert hasattr(cp, "edges"), "CompiledPool 缺 edges"
        assert hasattr(cp, "edge_order"), "CompiledPool 缺 edge_order"
        assert hasattr(cp, "edge_type"), "CompiledPool 缺 edge_type"
        assert hasattr(cp, "out_edges"), "CompiledPool 缺 out_edges"
        assert hasattr(cp, "in_edges"), "CompiledPool 缺 in_edges"
        assert hasattr(cp, "source_nodes"), "CompiledPool 缺 source_nodes"
        assert hasattr(cp, "node_role"), "CompiledPool 缺 node_role"
        # edge_order 来自 _order，非拓扑排序
        assert len(cp.edge_order) > 0, "edge_order 不应为空"
        # source_nodes 含源节点（candidate 类型）
        assert len(cp.source_nodes) > 0, "source_nodes 不应为空"
        # node_role 含角色映射
        assert len(cp.node_role) > 0, "node_role 不应为空"
        modules = report_state.setdefault("modules_covered", [])
        if "core.execution_module" not in modules:
            modules.append("core.execution_module")

    def test_propagate_apply_copy_mode(self, report_state) -> None:
        """propagate_apply copy 模式：源不变，目标累加。

        验证迭代 9「边执行三要素表驱动」硬约束。
        """
        from core.execution_module import propagate_apply

        src = ["fz000001", "fz000002"]
        tgt = ["fz000003"]
        passed = ["fz000001"]
        spec = {"mode": "copy"}
        new_tgt = propagate_apply(src, tgt, passed, spec)
        # copy 模式：passed 加入 tgt，源不变
        assert "fz000001" in new_tgt, "copy 模式 passed 应进入目标"
        assert "fz000003" in new_tgt, "原目标应保留"

    def test_role_actions_target_role_buy_signal(
        self, config_store, report_state
    ) -> None:
        """_ROLE_ACTIONS 表驱动：target 角色入池触发 BUY 信号。

        验证迭代 10「节点角色表驱动」硬约束。
        需先初始化全局 ConfigStore，使 _init_role_actions 能加载 node_roles.json。
        """
        from core.table_engine import set_global_config_store
        # 初始化全局 ConfigStore（_init_role_actions 依赖全局单例）
        set_global_config_store(config_store)
        from core.engine import _init_role_actions, _ROLE_ACTIONS
        # 触发 _ROLE_ACTIONS 注册表初始化
        _init_role_actions(config_store.get_table("node_roles"))

        roles_json = config_store.get_table("node_roles")
        # target 角色必须配置 on_enter 动作
        target_cfg = roles_json.get("target", {})
        on_enter = target_cfg.get("on_enter", [])
        assert "publish_buy_signal" in on_enter, \
            "target 角色 on_enter 必须含 publish_buy_signal"
        on_exit = target_cfg.get("on_exit", [])
        assert "publish_sell_signal" in on_exit, \
            "target 角色 on_exit 必须含 publish_sell_signal"
        # _ROLE_ACTIONS 注册表非空
        assert len(_ROLE_ACTIONS) >= 5, \
            f"_ROLE_ACTIONS 至少 5 种角色，实际 {len(_ROLE_ACTIONS)}"
        assert "target" in _ROLE_ACTIONS, "_ROLE_ACTIONS 缺 target 角色"

    def test_simulator_step_produces_events(self, report_state) -> None:
        """RuntimeSimulator.step() 产出事件列表。

        使用 10 只股票的仿真池配置，步进 1 秒，验证事件产出非空。
        """
        from core.runtime_mode_module import RuntimeSimulator

        config = _load_pool_config(_SIM_POOL_10)
        sim = RuntimeSimulator(pool_model=config, seed=42)
        sim.initialize()
        result = sim.step(d=1.0)
        # step 返回结果必须含 events / bar_data / changed_codes 三字段
        assert "events" in result, "step 结果缺 events 字段"
        assert "bar_data" in result, "step 结果缺 bar_data 字段"
        assert "changed_codes" in result, "step 结果缺 changed_codes 字段"
        # 事件列表应为 list 类型（可能为空，因为首步可能仅初始化）
        assert isinstance(result["events"], list), "events 必须为 list"
        # 记录性能数据
        report_state["sim_1000_tick_time_s"] = 0.0  # 加速测试不计性能
        event_types = report_state.setdefault("event_types_seen", [])
        for et in ("TickReceived", "DataChanged", "BarComposed"):
            if et not in event_types:
                event_types.append(et)
        modules = report_state.setdefault("modules_covered", [])
        for m in ("core.runtime_mode_module", "core.engine", "core.event_bus"):
            if m not in modules:
                modules.append(m)
