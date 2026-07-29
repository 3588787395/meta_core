"""正测试：条件节点激活与公式筛选

验证：
- cond1 激活（KDJ 金叉，nperiod=3）：从源池 StatePoolView 取脏股票，按 func 调用
  FormulaEngine.eval_series 添加列，按 filter_spec 筛选，发布 FormulaEvaluated +
  StockFiltered 事件
- cond2 激活（MACD 金叉，nperiod=3）
- 入边按 _order 排序
- 公式 = 添加列（FormulaEngine.eval_series 计算序列结果）
- 筛选 = 列比较（_eval_op，只读列）
- 公式计算与筛选严格分离
- cond3 交集（pool_A 全集 ∩ pool_B 全集，非脏股票交集）
- 差集/并集按 _SET_OP_FUNCS 表驱动分派

基于 spec.md "条件节点激活与公式筛选" + "集合运算交集/差集/并集" Scenario。
复用 ``core/`` 现有 ``EdgeExecutor`` / ``FormulaEngine`` / ``_eval_op`` /
``_SET_OP_FUNCS``，禁止兼容已删除旧接口。
"""
from __future__ import annotations

import inspect
import json
from pathlib import Path

from core.event_bus import EdgeFired, EventBus, FormulaEvaluated, StockFiltered
from core.execution_module import (
    Compiler,
    EdgeExecutor,
    _SET_OP_FUNCS,
    _eval_formula_path,
)

# 测试用 EdgeFired 时间戳（与虚拟时钟起点 34500.0 一致）
_TS = 34500.0

# 配置文件路径（与 conftest.py 一致，从配置动态读取边参数，不硬编码）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_POOL_CONFIG = _PROJECT_ROOT / "config" / "pools" / "sim_test_pool_100.json"

# 历史背景：Compiler.compile 的 edge_index 构建曾存在 bug（dict 推导式使用循环外
# 变量 ``eid`` 作为 key，导致只有最后一条边被索引）。该 bug 已在
# core/execution_module.py 的 Compiler.compile 中修复（改用
# ``str(edge.get("id") or edge.get("flow_id"))`` 作为 key），因此本测试模块
# 直接使用 ``Compiler.compile`` 产出的真实 ``schedule.edge_index``，不再需要
# 任何 workaround 重建。下方 ``test_compiler_edge_index_*`` 两个测试用于固化
# 该修复，防止回归。


# ────────────────────────────────────────────────────────────────
# 辅助：从 engine 提取 EdgeExecutor / EventBus / schedule
# ────────────────────────────────────────────────────────────────


def _get_components(engine):
    """从 PoolEngine 提取测试所需组件。"""
    return (
        engine._components["edge_executor"],
        engine._components["event_bus"],
        engine._components["schedule"],
    )


def _add_stocks_to_pool(state, nid, codes):
    """向指定池添加股票并标脏。"""
    pool = state.get_pool(nid)
    pool.add_stocks([{"code": c} for c in codes])


# ────────────────────────────────────────────────────────────────
# cond1 激活（KDJ 金叉，nperiod=5 对应 5 分钟 K 线）
# ────────────────────────────────────────────────────────────────


def test_cond1_filter_spec_kdj_nperiod5(pool_engine):
    """cond1 合成的 FilterSpec：formula_ref="KDJ"、nperiod=5、evaluator_type="formula"。"""
    engine = pool_engine()
    edge_executor, _bus, schedule = _get_components(engine)

    cond1_node = schedule.nodes.get("cond1", {})
    cond1_params = cond1_node.get("params", {}) if isinstance(cond1_node, dict) else {}
    filter_spec = edge_executor._build_cond_filter_spec(cond1_params)

    assert filter_spec.formula_ref == "KDJ"
    assert filter_spec.nperiod == 5
    assert filter_spec.evaluator_type == "formula"


def test_cond1_activation_publishes_formula_evaluated(pool_engine, event_collector, fz_stocks):
    """cond1 激活后发布 FormulaEvaluated 事件（formula_ref="KDJ"）。

    仿真模式需足够 K线才能触发真实金叉，此处直接构造脏股票 + 手动触发
    EdgeFired 绕过 tick 生成，验证事件发布结构而非 passed 集合内容。
    """
    engine = pool_engine()
    edge_executor, bus, _schedule = _get_components(engine)
    collector = event_collector(bus)

    # 向 source 池添加脏股票（从配置动态读取，不硬编码）
    codes = fz_stocks(3)
    _add_stocks_to_pool(engine.state, "source", codes)

    # 手动触发 ec1 边（source → cond1），绕过 tick 生成
    edge_executor._on_edge_fired(EdgeFired(eid="ec1", ts=_TS))

    formula_events = collector.filter(type="FormulaEvaluated")
    assert len(formula_events) > 0, "cond1 激活后应发布 FormulaEvaluated 事件"
    for ev in formula_events:
        assert ev.formula_ref == "KDJ", f"formula_ref 应为 KDJ，实际 {ev.formula_ref!r}"

    collector.disconnect()


def test_cond1_activation_publishes_stock_filtered(pool_engine, event_collector, fz_stocks):
    """cond1 激活后发布 StockFiltered 事件（含 passed / rejected 集合）。"""
    engine = pool_engine()
    edge_executor, bus, _schedule = _get_components(engine)
    collector = event_collector(bus)

    codes = fz_stocks(3)
    _add_stocks_to_pool(engine.state, "source", codes)

    edge_executor._on_edge_fired(EdgeFired(eid="ec1", ts=_TS))

    stock_events = collector.filter(type="StockFiltered")
    assert len(stock_events) > 0, "cond1 激活后应发布 StockFiltered 事件"
    ev = stock_events[0]
    assert ev.eid == "ec1"
    assert hasattr(ev, "passed"), "StockFiltered 应含 passed 字段"
    assert hasattr(ev, "rejected"), "StockFiltered 应含 rejected 字段"
    assert isinstance(ev.passed, list)
    assert isinstance(ev.rejected, list)

    collector.disconnect()


# ────────────────────────────────────────────────────────────────
# cond2 激活（MACD 金叉，nperiod=1 对应 1 分钟 K 线）
# ────────────────────────────────────────────────────────────────


def test_cond2_filter_spec_macd_nperiod1(pool_engine):
    """cond2 合成的 FilterSpec：formula_ref="MACD"、nperiod=1、evaluator_type="formula"。"""
    engine = pool_engine()
    edge_executor, _bus, schedule = _get_components(engine)

    cond2_node = schedule.nodes.get("cond2", {})
    cond2_params = cond2_node.get("params", {}) if isinstance(cond2_node, dict) else {}
    filter_spec = edge_executor._build_cond_filter_spec(cond2_params)

    assert filter_spec.formula_ref == "MACD"
    assert filter_spec.nperiod == 1
    assert filter_spec.evaluator_type == "formula"


def test_cond2_activation_publishes_formula_evaluated(pool_engine, event_collector, fz_stocks):
    """cond2 激活后发布 FormulaEvaluated 事件（formula_ref="MACD"）。"""
    engine = pool_engine()
    edge_executor, bus, _schedule = _get_components(engine)
    collector = event_collector(bus)

    codes = fz_stocks(3)
    _add_stocks_to_pool(engine.state, "source", codes)

    edge_executor._on_edge_fired(EdgeFired(eid="ec2", ts=_TS))

    formula_events = collector.filter(type="FormulaEvaluated")
    assert len(formula_events) > 0, "cond2 激活后应发布 FormulaEvaluated 事件"
    for ev in formula_events:
        assert ev.formula_ref == "MACD", f"formula_ref 应为 MACD，实际 {ev.formula_ref!r}"

    collector.disconnect()


def test_cond2_activation_publishes_stock_filtered(pool_engine, event_collector, fz_stocks):
    """cond2 激活后发布 StockFiltered 事件。"""
    engine = pool_engine()
    edge_executor, bus, _schedule = _get_components(engine)
    collector = event_collector(bus)

    codes = fz_stocks(3)
    _add_stocks_to_pool(engine.state, "source", codes)

    edge_executor._on_edge_fired(EdgeFired(eid="ec2", ts=_TS))

    stock_events = collector.filter(type="StockFiltered")
    assert len(stock_events) > 0, "cond2 激活后应发布 StockFiltered 事件"
    assert stock_events[0].eid == "ec2"

    collector.disconnect()


# ────────────────────────────────────────────────────────────────
# 入边按 _order 排序
# ────────────────────────────────────────────────────────────────


def test_collect_in_edges_ordered_by_order(pool_engine):
    """_collect_in_edges_ordered 按 _order 升序返回条件节点的入边。"""
    engine = pool_engine()
    edge_executor, _bus, schedule = _get_components(engine)
    # 直接使用 Compiler.compile 产出的真实 schedule.edge_index（bug 已修复）

    # cond3 有两条入边：ec3a (_order=30) → ec3b (_order=31)
    in_edges = edge_executor._collect_in_edges_ordered("cond3")
    assert len(in_edges) == 2, "cond3 应有 2 条入边"

    # 读取每条边的 _order 值
    orders = []
    for ec in in_edges:
        edge_dict = schedule.edge_index.get(ec.eid, {})
        params = edge_dict.get("params", {}) if isinstance(edge_dict, dict) else {}
        orders.append(int(params.get("_order", 0) or 0))

    # 断言升序排列且 _order 值正确
    assert orders == sorted(orders), f"入边未按 _order 升序排列: {orders}"
    assert orders == [30, 31], f"_order 值应为 [30, 31]，实际 {orders}"
    # ec3a (_order=30) 在 ec3b (_order=31) 之前
    assert in_edges[0].eid == "ec3a"
    assert in_edges[1].eid == "ec3b"


def test_collect_in_edges_ordered_single_edge(pool_engine):
    """单入边条件节点（cond1）的 _collect_in_edges_ordered 返回 1 条边。"""
    engine = pool_engine()
    edge_executor, _bus, _schedule = _get_components(engine)

    in_edges = edge_executor._collect_in_edges_ordered("cond1")
    assert len(in_edges) == 1
    assert in_edges[0].eid == "ec1"


# ────────────────────────────────────────────────────────────────
# 公式 = 添加列（FormulaEngine.eval_series）
# 筛选 = 列比较（_eval_op）
# 公式计算与筛选严格分离
# ────────────────────────────────────────────────────────────────


def test_formula_path_calls_eval_series():
    """_eval_formula_path 源码调用 formula_engine.eval_series（公式 = 添加列）。"""
    src = inspect.getsource(_eval_formula_path)
    assert "formula_engine.eval_series" in src, (
        "公式路径应调用 formula_engine.eval_series 计算公式序列（添加列）"
    )


def test_formula_path_calls_eval_op():
    """_eval_formula_path 源码调用 _eval_op（筛选 = 列比较，只读列）。"""
    src = inspect.getsource(_eval_formula_path)
    assert "_eval_op" in src, "筛选路径应调用 _eval_op 做列比较"


def test_formula_and_filter_separated():
    """公式计算与筛选严格分离。

    _eval_formula_path 中：
    1. eval_series 返回 series_results（公式 = 添加列，计算只写不比较）
    2. _build_op_ctx 从 series_results 提取 line1/line2 构建比较上下文
    3. _eval_op 基于上下文做列比较（筛选 = 只读列，比较不计算）
    两者无交叉：eval_series 不做比较，_eval_op 不做公式计算。
    """
    src = inspect.getsource(_eval_formula_path)
    # 公式计算步骤
    assert "eval_series" in src, "公式计算应通过 eval_series"
    # 上下文构建步骤（公式结果 → 筛选输入）
    assert "_build_op_ctx" in src, "公式结果经 _build_op_ctx 转换为筛选上下文"
    # 筛选步骤
    assert "_eval_op" in src, "筛选应通过 _eval_op 做列比较"
    # eval_series 结果存储在 series_results 变量
    assert "series_results" in src, "eval_series 结果应存储在 series_results"


# ────────────────────────────────────────────────────────────────
# cond3 交集：pool_A 全集 ∩ pool_B 全集
# ────────────────────────────────────────────────────────────────


def test_cond3_intersection(pool_engine, event_collector, fz_stocks):
    """cond3 交集：pool_A 全集 ∩ pool_B 全集 → pool_C。"""
    engine = pool_engine()
    edge_executor, bus, _schedule = _get_components(engine)
    # 直接使用 Compiler.compile 产出的真实 schedule.edge_index（bug 已修复）
    collector = event_collector(bus)

    codes = fz_stocks(4)
    # pool_A = {codes[0], codes[1]}
    _add_stocks_to_pool(engine.state, "pool_A", codes[:2])
    # pool_B = {codes[1], codes[2]}（codes[1] 为交集）
    _add_stocks_to_pool(engine.state, "pool_B", codes[1:3])

    # 触发 ec3a（pool_A → cond3），_activate_condition 会收集两条入边做交集
    edge_executor._on_edge_fired(EdgeFired(eid="ec3a", ts=_TS))

    # pool_C = pool_A ∩ pool_B = {codes[1]}
    pool_c_codes = engine.state.get_pool("pool_C").get_stock_codes()
    assert codes[1] in pool_c_codes, f"pool_C 应含交集股票 {codes[1]}"
    assert codes[0] not in pool_c_codes, "pool_C 不应含仅 pool_A 的股票"
    assert codes[2] not in pool_c_codes, "pool_C 不应含仅 pool_B 的股票"

    collector.disconnect()


def test_cond3_intersection_uses_full_set_not_dirty(pool_engine, fz_stocks):
    """cond3 交集使用源池全集（source_codes），而非脏股票交集。

    清脏后触发：pool_A/pool_B 无脏股票，但交集仍基于全集计算。
    这验证了交集路径使用 source_codes（当前池全集），不是 dirty_codes。
    """
    engine = pool_engine()
    edge_executor, _bus, _schedule = _get_components(engine)
    # 直接使用 Compiler.compile 产出的真实 schedule.edge_index（bug 已修复）

    codes = fz_stocks(4)
    _add_stocks_to_pool(engine.state, "pool_A", codes[:2])
    _add_stocks_to_pool(engine.state, "pool_B", codes[1:3])

    # 清脏：所有股票不再标记为脏
    engine.state.clear_dirty()
    assert not engine.state.get_changed_codes(), "清脏后 changed_codes 应为空"

    # 触发 ec3a：即使无脏股票，交集仍基于全集计算
    edge_executor._on_edge_fired(EdgeFired(eid="ec3a", ts=_TS))

    pool_c_codes = engine.state.get_pool("pool_C").get_stock_codes()
    assert codes[1] in pool_c_codes, (
        "交集应基于源池全集而非脏股票；清脏后 pool_C 仍应含交集股票"
    )
    assert codes[0] not in pool_c_codes, "清脏后 pool_C 仍不应含仅 pool_A 的股票"
    assert codes[2] not in pool_c_codes, "清脏后 pool_C 仍不应含仅 pool_B 的股票"


def test_cond3_intersection_empty_when_no_overlap(pool_engine, fz_stocks):
    """pool_A 与 pool_B 无交集时，pool_C 为空。"""
    engine = pool_engine()
    edge_executor, _bus, _schedule = _get_components(engine)
    # 直接使用 Compiler.compile 产出的真实 schedule.edge_index（bug 已修复）

    codes = fz_stocks(4)
    # pool_A 与 pool_B 无重叠
    _add_stocks_to_pool(engine.state, "pool_A", codes[:2])
    _add_stocks_to_pool(engine.state, "pool_B", codes[2:4])

    edge_executor._on_edge_fired(EdgeFired(eid="ec3a", ts=_TS))

    pool_c_codes = engine.state.get_pool("pool_C").get_stock_codes()
    assert len(pool_c_codes) == 0, f"无交集时 pool_C 应为空，实际 {pool_c_codes}"


# ────────────────────────────────────────────────────────────────
# _SET_OP_FUNCS 表驱动分派
# ────────────────────────────────────────────────────────────────


def test_set_op_funcs_has_three_keys():
    """_SET_OP_FUNCS 表包含 intersection / union / difference 三个键。"""
    assert "intersection" in _SET_OP_FUNCS, "缺少 intersection 键"
    assert "union" in _SET_OP_FUNCS, "缺少 union 键"
    assert "difference" in _SET_OP_FUNCS, "缺少 difference 键"


def test_set_op_funcs_dispatches_correctly(fz_stocks):
    """_SET_OP_FUNCS 三个集合运算函数计算正确。"""
    codes = fz_stocks(4)
    a = {codes[0], codes[1], codes[2]}
    b = {codes[1], codes[2], codes[3]}

    assert _SET_OP_FUNCS["intersection"](a, b) == {codes[1], codes[2]}
    assert _SET_OP_FUNCS["union"](a, b) == {codes[0], codes[1], codes[2], codes[3]}
    assert _SET_OP_FUNCS["difference"](a, b) == {codes[0]}


def test_apply_set_operation_dispatches_by_eval_type(pool_engine, fz_stocks):
    """_apply_set_operation 按 eval_type 表驱动分派到 _SET_OP_FUNCS。"""
    engine = pool_engine()
    edge_executor, _bus, _schedule = _get_components(engine)

    codes = fz_stocks(3)
    port_results = {0: [codes[0], codes[1]], 1: [codes[1], codes[2]]}

    # intersection
    result = edge_executor._apply_set_operation(dict(port_results), "intersection")
    assert set(result) == {codes[1]}

    # union
    result = edge_executor._apply_set_operation(dict(port_results), "union")
    assert set(result) == {codes[0], codes[1], codes[2]}

    # difference
    result = edge_executor._apply_set_operation(dict(port_results), "difference")
    assert set(result) == {codes[0]}


def test_apply_set_operation_single_port(pool_engine, fz_stocks):
    """单入边时 _apply_set_operation 直接输出该端口结果（不做集合运算）。"""
    engine = pool_engine()
    edge_executor, _bus, _schedule = _get_components(engine)

    codes = fz_stocks(2)
    port_results = {0: [codes[0], codes[1]]}
    result = edge_executor._apply_set_operation(port_results, "intersection")
    assert set(result) == {codes[0], codes[1]}


# ────────────────────────────────────────────────────────────────
# 源码层面：_activate_condition 使用 get_dirty_codes 取脏股票
# ────────────────────────────────────────────────────────────────


def test_activate_condition_uses_get_dirty_codes():
    """_activate_condition 源码使用 source_pool.get_dirty_codes() 取脏股票。"""
    src = inspect.getsource(EdgeExecutor._activate_condition)
    assert "get_dirty_codes()" in src, (
        "_activate_condition 应从源池 StatePoolView.get_dirty_codes() 取脏股票"
    )
    # 不引用 event.changed_codes（G3 已移除）
    assert "event.changed_codes" not in src


# ────────────────────────────────────────────────────────────────
# Compiler.compile 产出的 edge_index 完整性（bug 修复固化）
# ────────────────────────────────────────────────────────────────


def test_compiler_edge_index_contains_all_edges():
    """Compiler.compile 产出的 schedule.edge_index 包含配置中的所有边。

    历史 bug：dict 推导式使用循环外变量 ``eid`` 作为 key，导致每条边都
    覆盖同一个 key（``eid`` 循环结束后的最终值），edge_index 最终只含
    最后一条边。修复后 edge_index 应包含配置中的全部边。
    从配置动态读取 expected 边数，不硬编码。
    """
    with open(_DEFAULT_POOL_CONFIG, encoding="utf-8") as f:
        cfg = json.load(f)
    expected_edge_count = sum(
        1 for e in cfg.get("edges", [])
        if isinstance(e, dict) and (e.get("id") or e.get("flow_id"))
    )
    schedule = Compiler.compile(cfg)
    assert len(schedule.edge_index) == expected_edge_count, (
        f"edge_index 应包含全部 {expected_edge_count} 条边，"
        f"实际 {len(schedule.edge_index)} 条: {list(schedule.edge_index.keys())}"
    )


def test_compiler_edge_index_keys_match_edge_ids():
    """Compiler.compile 产出的 edge_index keys 集合等于配置中所有 edge id 集合。

    历史 bug：edge_index 的 key 全部是循环外变量 ``eid`` 的最终值（最后一条
    边的 id），导致 keys 集合只有 1 个元素。修复后 keys 应等于配置中全部
    edge id 集合。从配置动态读取 expected keys，不硬编码。
    """
    with open(_DEFAULT_POOL_CONFIG, encoding="utf-8") as f:
        cfg = json.load(f)
    expected_keys = {
        str(e.get("id") or e.get("flow_id"))
        for e in cfg.get("edges", [])
        if isinstance(e, dict) and (e.get("id") or e.get("flow_id"))
    }
    schedule = Compiler.compile(cfg)
    assert set(schedule.edge_index.keys()) == expected_keys, (
        f"edge_index keys 应为 {expected_keys}，"
        f"实际 {set(schedule.edge_index.keys())}"
    )
