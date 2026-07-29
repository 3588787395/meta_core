"""Task 6：池转移正测试。

验证 core.runtime_mode_module（PoolState / StatePoolView）与
core.execution_module（EdgeExecutor / EventDriver / EdgeFired）池转移逻辑：
  - PoolEngine 加载 100 股票池配置
  - StatePoolView get_dirty_codes / add_stocks / remove_stocks
  - EdgeExecutor.run() 边执行
  - EdgeFired 事件仅含 eid + ts（无 changed_codes 字段）
  - 边顺序号 _order 决定交集/差集运算次序
  - 多入边到同一目标节点按 _order 排序
  - TTL 一次性事件（interval=None 不重新注册）

测试可能因源码 bug 失败，这是预期行为——测试目的是发现 bug。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import pytest

from metatest.conftest import EventCollector


# 项目根目录（meta_core/），用于定位 config/pools/ 配置文件
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_SIM_DEMO_POOL = _PROJECT_ROOT / "config" / "pools" / "sim_demo_pool.json"
_SIM_TEST_POOL_100 = _PROJECT_ROOT / "config" / "pools" / "sim_test_pool_100.json"


# ============================================================================
# SubTask 6.1: PoolEngine 加载 100 股票池配置
# ============================================================================


class TestPoolEngineLoadConfig:
    """PoolEngine 加载池配置正测试。"""

    def test_pool_engine_loads_100_stock_pool_config(self, pool_engine) -> None:
        """PoolEngine 加载 sim_test_pool_100.json，source 节点含 100 只 fz 股票。"""
        engine = pool_engine(pool_config_path=str(_SIM_TEST_POOL_100))

        assert engine is not None, "PoolEngine 应成功实例化"
        assert hasattr(engine, "pool_config"), "PoolEngine 应有 pool_config 属性"
        cfg = engine.pool_config
        assert cfg["id"] == "sim_test_pool_100", f"池 id 应为 sim_test_pool_100，实际 {cfg['id']}"
        # source 节点的 stocks 参数应有 100 只股票
        source_node = next(n for n in cfg["nodes"] if n["id"] == "source")
        stocks = source_node["params"]["stocks"]
        assert len(stocks) == 100, f"source 节点应有 100 只股票，实际 {len(stocks)}"
        # 全部股票代码应以 fz 前缀开头
        assert all(s["code"].startswith("fz") for s in stocks), \
            "所有股票代码应以 fz 前缀开头"

    def test_pool_engine_state_has_all_nodes(self, pool_engine) -> None:
        """PoolEngine 加载 sim_demo_pool 后 state 包含全部 4 个节点。"""
        engine = pool_engine(pool_config_path=str(_SIM_DEMO_POOL))

        cfg = engine.pool_config
        node_ids = {n["id"] for n in cfg["nodes"]}
        assert node_ids == {"source", "pool_A", "pool_B", "pool_C"}, \
            f"sim_demo_pool 节点集应为 {{source, pool_A, pool_B, pool_C}}，实际 {node_ids}"
        # state 应能获取每个节点的视图
        for nid in node_ids:
            view = engine.state.get_pool(nid)
            assert view is not None, f"state.get_pool({nid}) 不应为 None"


# ============================================================================
# SubTask 6.2 / 6.3 / 6.4: StatePoolView 脏股票管理
# ============================================================================


class TestStatePoolViewDirtyCodes:
    """StatePoolView get_dirty_codes / add_stocks / remove_stocks 正测试。"""

    def _make_state(self):
        """构造带单节点的 PoolState 用于视图测试。"""
        from core.runtime_mode_module import PoolState

        cfg = {
            "id": "test_dirty",
            "nodes": [{"id": "state_1", "type": "statepool"}],
            "edges": [],
        }
        return PoolState(pool_config=cfg)

    def test_get_dirty_codes_returns_changed_intersection(self) -> None:
        """get_dirty_codes() 返回 changed_codes ∩ 池内股票。"""
        state = self._make_state()
        view = state.get_pool("state_1")
        view.add_stocks([{"code": "fz000001"}, {"code": "fz000002"}])
        # add_stocks 已将两只股票标脏，清空脏标记后仅标记 fz000001 为脏
        state.clear_dirty()
        state.add_changed_codes(["fz000001"])

        dirty = view.get_dirty_codes()
        assert isinstance(dirty, set), "get_dirty_codes 应返回 set"
        assert "fz000001" in dirty, "fz000001 应在脏股票集合中"
        assert "fz000002" not in dirty, "fz000002 未变化，不应在脏股票集合中"

    def test_add_stocks_adds_to_pool_and_marks_dirty(self) -> None:
        """add_stocks() 入池 + 标脏。"""
        state = self._make_state()
        view = state.get_pool("state_1")

        assert view.get_stock_codes() == set(), "初始池应为空"
        view.add_stocks([{"code": "fz000001"}, {"code": "fz000002"}])

        codes = view.get_stock_codes()
        assert codes == {"fz000001", "fz000002"}, \
            f"add_stocks 后池应含 2 只股票，实际 {codes}"
        # add_stocks 应将新股票代码加入 changed_codes
        changed = state.get_changed_codes()
        assert "fz000001" in changed, "add_stocks 应将 fz000001 加入 changed_codes"
        assert "fz000002" in changed, "add_stocks 应将 fz000002 加入 changed_codes"

    def test_remove_stocks_removes_and_marks_dirty(self) -> None:
        """remove_stocks() 出池 + 标脏。"""
        state = self._make_state()
        view = state.get_pool("state_1")
        view.add_stocks([{"code": "fz000001"}, {"code": "fz000002"}, {"code": "fz000003"}])
        # 清空脏标记，隔离 remove 的影响
        state.clear_dirty()

        view.remove_stocks(["fz000002"])

        codes = view.get_stock_codes()
        assert "fz000002" not in codes, "fz000002 应已被移除"
        assert codes == {"fz000001", "fz000003"}, \
            f"移除后池应剩 fz000001/fz000003，实际 {codes}"
        # remove_stocks 应将移除的代码加入 changed_codes
        changed = state.get_changed_codes()
        assert "fz000002" in changed, "remove_stocks 应将 fz000002 加入 changed_codes"


# ============================================================================
# SubTask 6.5: EdgeExecutor.run() 边执行
# ============================================================================


class TestEdgeExecutorRun:
    """EdgeExecutor.run() 边执行正测试。"""

    def test_edge_executor_exists_in_engine(self, pool_engine) -> None:
        """PoolEngine 装配后 _components 含 edge_executor 实例。"""
        engine = pool_engine()
        edge_executor = engine._components.get("edge_executor")

        assert edge_executor is not None, "_components 应含 edge_executor"
        assert hasattr(edge_executor, "run"), "EdgeExecutor 应有 run 方法"
        assert callable(edge_executor.run), "EdgeExecutor.run 应为可调用"

    def test_edge_executor_run_returns_bool(self, pool_engine) -> None:
        """EdgeExecutor.run() 对已知 eid 返回 bool（True/False），对未知 eid 返回 False。"""
        engine = pool_engine(pool_config_path=str(_SIM_DEMO_POOL))
        edge_executor = engine._components["edge_executor"]
        schedule = engine._components["schedule"]

        # 未知 eid 应返回 False
        result_unknown = edge_executor.run("nonexistent_edge")
        assert result_unknown is False, "未知 eid run 应返回 False"

        # 已知 eid 应返回 bool（不抛异常即可）
        if schedule.edge_ctx:
            known_eid = next(iter(schedule.edge_ctx.keys()))
            result = edge_executor.run(known_eid, changed_codes=None)
            assert isinstance(result, bool), \
                f"已知 eid run 应返回 bool，实际 {type(result)}"


# ============================================================================
# SubTask 6.6: EdgeFired 事件结构（仅 eid + ts）
# ============================================================================


class TestEdgeFiredEventStructure:
    """EdgeFired 事件结构正测试（G3：仅 eid + ts，无 changed_codes）。"""

    def test_edge_fired_has_only_eid_and_ts_fields(self) -> None:
        """EdgeFired dataclass 仅含 eid 与 ts 两个字段，无 changed_codes。"""
        from core.event_bus import EdgeFired
        import dataclasses

        fields = {f.name for f in dataclasses.fields(EdgeFired)}
        assert fields == {"eid", "ts"}, \
            f"EdgeFired 字段应为 {{eid, ts}}，实际 {fields}"
        assert "changed_codes" not in fields, \
            "EdgeFired 不应包含 changed_codes 字段（G3：脏股票由源池 StatePoolView 取）"

    def test_edge_fired_can_be_constructed_with_eid_and_ts(self) -> None:
        """EdgeFired 可仅用 eid + ts 构造，无需 changed_codes。"""
        from core.event_bus import EdgeFired

        ev = EdgeFired(eid="e1", ts=100.0)
        assert ev.eid == "e1", "EdgeFired.eid 应为 e1"
        assert ev.ts == 100.0, "EdgeFired.ts 应为 100.0"
        assert not hasattr(ev, "changed_codes"), "EdgeFired 不应有 changed_codes 属性"


# ============================================================================
# SubTask 6.7: 池转移（source → target）
# ============================================================================


class TestPoolTransferSourceToTarget:
    """池转移 source → target 正测试。"""

    def test_sim_demo_pool_has_edges_from_source(self, pool_engine) -> None:
        """sim_demo_pool 配置含 source → pool_A / pool_B 的条件转移边。"""
        engine = pool_engine(pool_config_path=str(_SIM_DEMO_POOL))
        schedule = engine._components["schedule"]

        # 至少有边以 source 为源节点
        source_edges = [ec for ec in schedule.edge_ctx.values() if ec.sid == "source"]
        assert len(source_edges) >= 2, \
            f"应以 source 为源节点至少 2 条边，实际 {len(source_edges)}"
        # 验证边目标包含 pool_A 和 pool_B
        targets = {ec.tid for ec in source_edges}
        assert "pool_A" in targets, "source 应有边指向 pool_A"
        assert "pool_B" in targets, "source 应有边指向 pool_B"

    def test_engine_components_contain_event_driver_and_bus(self, pool_engine) -> None:
        """PoolEngine 装配后 _components 含 event_driver 与 event_bus。"""
        engine = pool_engine(pool_config_path=str(_SIM_DEMO_POOL))

        assert "event_driver" in engine._components, "_components 应含 event_driver"
        assert "event_bus" in engine._components, "_components 应含 event_bus"
        event_driver = engine._components["event_driver"]
        assert hasattr(event_driver, "fire_due"), "EventDriver 应有 fire_due 方法"
        assert hasattr(event_driver, "add_spec"), "EventDriver 应有 add_spec 方法"


# ============================================================================
# SubTask 6.8 / 6.9: 边顺序号 _order 与多入边排序
# ============================================================================


class TestEdgeOrderAndMultipleInEdges:
    """边顺序号 _order 决定交集/差集运算次序正测试。"""

    def test_sim_demo_pool_edges_have_order_param(self) -> None:
        """sim_demo_pool 配置中条件边 params 含 _order 顺序号。"""
        with open(_SIM_DEMO_POOL, encoding="utf-8") as f:
            cfg = json.load(f)

        for edge in cfg["edges"]:
            params = edge.get("params", {})
            assert "_order" in params, \
                f"边 {edge['id']} params 应含 _order 顺序号"

    def test_multiple_edges_to_same_target_use_order(self) -> None:
        """sim_demo_pool 中 pool_C 有 2 条入边（e_A_C, e_B_C），按 _order 排序。"""
        with open(_SIM_DEMO_POOL, encoding="utf-8") as f:
            cfg = json.load(f)

        # 收集所有指向 pool_C 的边
        in_edges_to_c = [e for e in cfg["edges"] if e["target"] == "pool_C"]
        assert len(in_edges_to_c) == 2, \
            f"pool_C 应有 2 条入边，实际 {len(in_edges_to_c)}"
        # 按 _order 排序
        in_edges_to_c_sorted = sorted(
            in_edges_to_c, key=lambda e: e["params"]["_order"]
        )
        orders = [e["params"]["_order"] for e in in_edges_to_c_sorted]
        assert orders == sorted(orders), \
            f"入边 _order 应升序排列，实际 {orders}"
        # e_A_C (_order=30) 应先于 e_B_C (_order=31)
        assert in_edges_to_c_sorted[0]["id"] == "e_A_C", \
            f"_order 最小的入边应为 e_A_C，实际 {in_edges_to_c_sorted[0]['id']}"
        assert in_edges_to_c_sorted[1]["id"] == "e_B_C", \
            f"_order 次小的入边应为 e_B_C，实际 {in_edges_to_c_sorted[1]['id']}"

    def test_collect_in_edges_ordered_sorts_by_order(self, pool_engine) -> None:
        """EdgeExecutor._collect_in_edges_ordered 按 _order 升序收集条件节点入边。"""
        engine = pool_engine(pool_config_path=str(_SIM_DEMO_POOL))
        edge_executor = engine._components["edge_executor"]

        # pool_C 是条件节点（INTERSECTION 边的目标），收集其入边
        in_edges = edge_executor._collect_in_edges_ordered("pool_C")
        assert len(in_edges) == 2, f"pool_C 应有 2 条入边，实际 {len(in_edges)}"
        # 验证按 _order 升序：e_A_C (30) 在 e_B_C (31) 之前
        edge_index = engine._components["schedule"].edge_index
        orders = [
            int(edge_index[ec.eid].get("params", {}).get("_order", 0))
            for ec in in_edges
        ]
        assert orders == sorted(orders), \
            f"_collect_in_edges_ordered 应按 _order 升序，实际 {orders}"


# ============================================================================
# SubTask 6.10: TTL 一次性事件（interval=None 不重新注册）
# ============================================================================


class TestTTLOneShotNoReregistration:
    """TTL 一次性事件正测试（interval=None 时不重新注册）。"""

    def test_ttl_spec_interval_none_means_one_shot(self) -> None:
        """TimedEventSpec.interval=None 表示一次性事件。"""
        from core.domain import TimedEventSpec

        fired: List[Dict[str, Any]] = []

        def action(params, fire_time=None):
            fired.append({"params": params, "fire_time": fire_time})

        spec = TimedEventSpec(action=action, params={"kind": "ttl"}, interval=None)
        assert spec.interval is None, "interval=None 表示一次性事件"

    def test_event_driver_one_shot_spec_not_reregistered(self) -> None:
        """EventDriver 对 interval=None 的一次性 spec 不重新注册到 heapq。"""
        from core.domain import TimedEventSpec
        from core.execution_module import EventDriver

        fired: List[float] = []

        def action(params, fire_time=None):
            fired.append(fire_time)

        # 一次性 spec：interval=None
        one_shot = TimedEventSpec(
            action=action, params={"kind": "ttl"}, interval=None
        )
        driver = EventDriver(state=None, bus=None)
        driver.add_spec(one_shot, first_fire_time=100.0)

        assert len(driver._heap) == 1, "注册后 heapq 应有 1 项"
        # 触发到时事件
        driver.fire_due(now=100.0)
        assert len(fired) == 1, f"一次性 spec 应触发 1 次，实际 {len(fired)}"
        # interval=None 不应重新注册
        assert len(driver._heap) == 0, \
            f"一次性 spec 触发后不应重新注册，heapq 应为空，实际 {len(driver._heap)}"

    def test_event_driver_periodic_spec_reregistered(self) -> None:
        """EventDriver 对 interval>0 的周期 spec 重新注册到 heapq（对照测试）。"""
        from core.domain import TimedEventSpec
        from core.execution_module import EventDriver

        fired: List[float] = []

        def action(params, fire_time=None):
            fired.append(fire_time)

        # 周期 spec：interval=10.0
        periodic = TimedEventSpec(
            action=action, params={"kind": "edge"}, interval=10.0
        )
        driver = EventDriver(state=None, bus=None)
        driver.add_spec(periodic, first_fire_time=100.0)

        assert len(driver._heap) == 1, "注册后 heapq 应有 1 项"
        driver.fire_due(now=100.0)
        assert len(fired) == 1, f"周期 spec 应触发 1 次，实际 {len(fired)}"
        # interval>0 应重新注册下次（fire_time + interval = 110.0）
        assert len(driver._heap) == 1, \
            f"周期 spec 触发后应重新注册，heapq 应有 1 项，实际 {len(driver._heap)}"
        next_fire = driver._heap[0][0]
        assert next_fire == 110.0, \
            f"下次触发应为 110.0（fire_time+interval），实际 {next_fire}"
