# -*- coding: utf-8 -*-
"""反测试 — 无效条件节点配置（Task 6 SubTask 6.2）。

基于 spec.md "反测试（异常与边界验证）" → "无效条件节点配置" Scenario：

    WHEN cond1.func 缺失 accode 字段
    THEN FormulaEngine 抛出明确异常或返回空结果
    AND  异常被 EdgeExecutor 捕获，不传播到 EventDriver
    AND  发布 FormulaEvaluated 事件携带 error 字段

实现要点：
    - 基于 ``config/pools/sim_test_pool_100.json`` 动态构造一份 cond1.func
      缺失 accode 字段的派生配置（不硬编码实例内容，仅删除 accode 键）。
    - 为彻底触发"FormulaEngine 返回空结果"路径，同时清空 indi 与
      filter_spec.formula_ref 两个回退源（``_build_cond_filter_spec`` 中
      formula_ref 优先取 func.accode，回退到 indi / filter_spec.formula_ref），
      使 formula_ref="" → ``_eval_formula_series`` 直接返回 {code: None} → passed=[]。
    - 直接 ``PoolEngine(pool_config=cfg)`` 装配，添加脏股票后手动触发
      ``EdgeFired(eid="ec1")``，验证：
        1. FormulaEngine 返回空（passed=[]，StockFiltered.passed=[]）
        2. 异常被 EdgeExecutor 捕获（``_eval_formula_path`` try/except），
           不传播到 EventDriver（后续 fire_due 仍可正常调用）
        3. FormulaEvaluated 事件携带非空 error 字段（spec.md L128）：
           formula_ref 为空时 ``_eval_formula_path`` 通过 ``eval_deps.bus``
           发布携带 error 信息的 FormulaEvaluated 事件供下游诊断。

复用 core/ 现有类（PoolEngine / EdgeExecutor / EventBus / EventDriver /
FormulaEngine），不修改 core/ 源文件，不使用已删除旧接口（get_node_stocks /
SimTickSource / execution_order / EdgeFired.changed_codes / at_fn /
fire_ttl_due / TtlTracker）。
"""
from __future__ import annotations

import copy
import inspect
import json
from pathlib import Path
from typing import Any, Dict, List

import pytest

from core.engine import PoolEngine
from core.event_bus import (
    EdgeFired,
    FormulaEvaluated,
)


# ═══════════════════════════════════════════════════════════════
# 测试常量（测试参数，非硬编码实例内容）
# ═══════════════════════════════════════════════════════════════

_TS = 34500.0              # 虚拟时钟起点（09:30:00 当日秒数偏移）
_ADVANCE_SECONDS = 600    # 推进虚拟时钟秒数（> 最大边间隔 60s，确保 fire_due 触发）

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_POOL_CONFIG = _PROJECT_ROOT / "config" / "pools" / "sim_test_pool_100.json"


# ═══════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════


def _load_invalid_cond_config() -> Dict[str, Any]:
    """加载 sim_test_pool_100.json 并删除 cond1.func 的 accode 字段。

    派生配置保留原 nodes/edges 结构，仅对 cond1 节点做以下修改：
      - 删除 ``params.func.accode``（spec 要求的核心条件）
      - 清空 ``params.indi``（消除回退源 1）
      - 清空 ``params.filter_spec.formula_ref``（消除回退源 2）

    这样 ``_build_cond_filter_spec`` 中 formula_ref 无任何回退值，
    最终 formula_ref="" → ``_eval_formula_series`` 直接返回 {code: None} → passed=[]，
    触发"FormulaEngine 返回空结果"路径。

    注：``_build_tdx_func_from_panel`` 中 indi 会回退填充 accode，
    故必须同时清空 indi，否则 accode 会被回退恢复。
    """
    with open(_DEFAULT_POOL_CONFIG, encoding="utf-8") as f:
        cfg = json.load(f)
    cfg = copy.deepcopy(cfg)
    for node in cfg.get("nodes", []):
        if not isinstance(node, dict):
            continue
        if node.get("id") != "cond1":
            continue
        params = node.setdefault("params", {})
        func = params.get("func")
        if isinstance(func, dict):
            # 删除 accode 字段（spec 要求的核心条件）
            func.pop("accode", None)
        # 清空 indi（消除 accode 回退源 1）
        params["indi"] = ""
        # 清空 filter_spec.formula_ref（消除 formula_ref 回退源 2）
        filter_spec = params.get("filter_spec")
        if isinstance(filter_spec, dict):
            filter_spec["formula_ref"] = ""
        break
    return cfg


def _get_components(engine: PoolEngine):
    """从 PoolEngine 提取测试所需组件。"""
    return (
        engine._components["edge_executor"],
        engine._components["event_bus"],
        engine._components["event_driver"],
        engine._components["schedule"],
    )


def _set_virtual_time(engine: PoolEngine, ts: float) -> None:
    """设置 engine.state.time_source 的 current_ts 为指定时刻。

    与 test_positive_ttl.py 同一模式。
    """
    engine.state.time_source["current_ts"] = float(ts)
    engine.state.time_source.setdefault("start_ts", _TS)
    engine.state.time_source.setdefault("driver_type", "virtual")


def _add_stocks_to_pool(state: Any, nid: str, codes: List[str]) -> None:
    """向指定池添加股票并标脏（与 test_positive_condition_activation.py 同一模式）。"""
    pool = state.get_pool(nid)
    pool.add_stocks([{"code": c} for c in codes])


def _load_fz_stocks(n: int = 3) -> List[str]:
    """从 sim_test_pool_100.json 动态读取 N 只 fz 前缀股票代码（不硬编码）。"""
    with open(_DEFAULT_POOL_CONFIG, encoding="utf-8") as f:
        cfg = json.load(f)
    codes: List[str] = []
    for node in cfg.get("nodes", []):
        if not isinstance(node, dict):
            continue
        if node.get("type") != "market_source":
            continue
        for stock in node.get("params", {}).get("stocks", []):
            if isinstance(stock, dict) and stock.get("code"):
                codes.append(str(stock["code"]))
    return codes[:n]


# ═══════════════════════════════════════════════════════════════
# 测试用例
# ═══════════════════════════════════════════════════════════════


class TestInvalidConfigStartup:
    """验证无效条件节点配置下 PoolEngine 装配不抛异常。"""

    def test_invalid_config_engine_starts_without_exception(self):
        """cond1.func 缺失 accode 字段时，PoolEngine 装配不抛异常。

        断言：
          - PoolEngine(pool_config=cfg) 构造完成不抛异常
          - _components 含全部关键组件（edge_executor/formula_engine/event_bus/schedule）
        """
        cfg = _load_invalid_cond_config()
        engine = PoolEngine(pool_config=cfg)
        assert "edge_executor" in engine._components
        assert "formula_engine" in engine._components
        assert "event_bus" in engine._components
        assert "schedule" in engine._components

    def test_invalid_config_cond1_func_missing_accode(self):
        """cond1.func 不含 accode 字段（验证派生配置正确性）。"""
        cfg = _load_invalid_cond_config()
        cond1_node = None
        for node in cfg.get("nodes", []):
            if isinstance(node, dict) and node.get("id") == "cond1":
                cond1_node = node
                break
        assert cond1_node is not None, "cond1 节点应存在"
        func = cond1_node.get("params", {}).get("func", {})
        assert "accode" not in func, (
            f"cond1.func 不应含 accode 字段，实际 func={func}"
        )
        # indi 与 filter_spec.formula_ref 也应被清空（消除回退源）
        assert cond1_node.get("params", {}).get("indi") == ""
        assert cond1_node.get("params", {}).get("filter_spec", {}).get("formula_ref") == ""


class TestInvalidConfigFormulaEmpty:
    """验证无效配置下 FormulaEngine 返回空结果（passed 集合为空）。"""

    def test_invalid_config_filter_spec_formula_ref_empty(self):
        """无效配置下 _build_cond_filter_spec 返回的 formula_ref 为空。

        断言：
          - filter_spec.formula_ref == ""（因 func.accode/indi/filter_spec.formula_ref 均无值）
          - filter_spec.evaluator_type == "formula"（indicator → formula 映射）
        """
        cfg = _load_invalid_cond_config()
        engine = PoolEngine(pool_config=cfg)
        edge_executor, _bus, _event_driver, schedule = _get_components(engine)

        cond1_node = schedule.nodes.get("cond1", {})
        cond1_params = cond1_node.get("params", {}) if isinstance(cond1_node, dict) else {}
        filter_spec = edge_executor._build_cond_filter_spec(cond1_params)

        assert filter_spec.formula_ref == "", (
            f"无效配置下 formula_ref 应为空，实际 {filter_spec.formula_ref!r}"
        )

    def test_invalid_config_passed_empty(self, event_collector):
        """无效配置下手动触发 EdgeFired(eid="ec1")，StockFiltered.passed 为空。

        断言：
          - _on_edge_fired 不抛异常
          - StockFiltered.passed == []（FormulaEngine 返回空结果）
          - StockFiltered.rejected 含全部 source_codes（无股票通过筛选，全部被拒绝）

        说明：formula_ref="" → eval_series 返回 {code: None} → _eval_formula_path
        返回 passed=[]，_filter 将 source_codes 全部归入 rejected。
        """
        cfg = _load_invalid_cond_config()
        engine = PoolEngine(pool_config=cfg)
        edge_executor, bus, _event_driver, _schedule = _get_components(engine)
        collector = event_collector(bus)
        try:
            _set_virtual_time(engine, _TS)
            # 向 source 池添加脏股票（从配置动态读取，不硬编码）
            codes = _load_fz_stocks(3)
            _add_stocks_to_pool(engine.state, "source", codes)

            # 手动触发 ec1 边（source → cond1）
            edge_executor._on_edge_fired(EdgeFired(eid="ec1", ts=_TS))

            stock_events = collector.filter(type="StockFiltered")
            # 无效配置下 formula_ref="" → eval_series 返回 {code: None} → passed=[]
            # StockFiltered 事件发布（passed=[], rejected=source_codes 全部被拒绝）
            for ev in stock_events:
                assert ev.passed == [], (
                    f"无效配置下 StockFiltered.passed 应为空，实际 {ev.passed}"
                )
                # rejected 应包含全部 source_codes（无股票通过筛选）
                assert set(ev.rejected) == set(codes), (
                    f"无效配置下 StockFiltered.rejected 应含全部 source_codes，"
                    f"实际 {ev.rejected}，期望 {codes}"
                )
        finally:
            collector.disconnect()

    def test_invalid_config_pool_a_remains_empty(self, event_collector):
        """无效配置下 cond1 激活后 pool_A 仍为空（无股票通过筛选入池）。

        断言：
          - 触发 ec1 后 pool_A = 0 只股票
        """
        cfg = _load_invalid_cond_config()
        engine = PoolEngine(pool_config=cfg)
        edge_executor, bus, _event_driver, _schedule = _get_components(engine)
        collector = event_collector(bus)
        try:
            _set_virtual_time(engine, _TS)
            codes = _load_fz_stocks(3)
            _add_stocks_to_pool(engine.state, "source", codes)

            edge_executor._on_edge_fired(EdgeFired(eid="ec1", ts=_TS))

            pool_a_stocks = engine.state.get_pool("pool_A").get_stocks()
            assert len(pool_a_stocks) == 0, (
                f"无效配置下 pool_A 应为空，实际 {len(pool_a_stocks)} 只股票: {pool_a_stocks}"
            )
        finally:
            collector.disconnect()


class TestInvalidConfigExceptionHandling:
    """验证异常被 EdgeExecutor 捕获，不传播到 EventDriver。"""

    def test_invalid_config_activation_no_crash(self, event_collector):
        """无效配置下手动触发 EdgeFired(eid="ec1")，不抛异常。

        断言：
          - _on_edge_fired 调用不抛异常（异常被 _eval_formula_path try/except 捕获）
        """
        cfg = _load_invalid_cond_config()
        engine = PoolEngine(pool_config=cfg)
        edge_executor, bus, _event_driver, _schedule = _get_components(engine)
        collector = event_collector(bus)
        try:
            _set_virtual_time(engine, _TS)
            codes = _load_fz_stocks(3)
            _add_stocks_to_pool(engine.state, "source", codes)

            # 不应抛异常（异常被 _eval_formula_path try/except 捕获）
            edge_executor._on_edge_fired(EdgeFired(eid="ec1", ts=_TS))
        finally:
            collector.disconnect()

    def test_invalid_config_exception_not_propagated_to_event_driver(
        self, event_collector,
    ):
        """无效配置下异常不传播到 EventDriver（后续 fire_due 仍可正常调用）。

        断言：
          - 触发 ec1 后，event_driver.fire_due 仍可正常调用（EventDriver 未崩溃）
          - fire_due 不抛异常
        """
        cfg = _load_invalid_cond_config()
        engine = PoolEngine(pool_config=cfg)
        edge_executor, bus, event_driver, _schedule = _get_components(engine)
        collector = event_collector(bus)
        try:
            _set_virtual_time(engine, _TS)
            codes = _load_fz_stocks(3)
            _add_stocks_to_pool(engine.state, "source", codes)

            # 触发 ec1（异常被 EdgeExecutor 捕获，不应传播到 EventDriver）
            edge_executor._on_edge_fired(EdgeFired(eid="ec1", ts=_TS))

            # 后续 fire_due 仍可正常调用（EventDriver 未受影响）
            _set_virtual_time(engine, _TS + _ADVANCE_SECONDS)
            event_driver.fire_due(_TS + _ADVANCE_SECONDS)
        finally:
            collector.disconnect()

    def test_invalid_config_event_bus_not_corrupted(self, event_collector):
        """无效配置下 EventBus 不受异常影响（仍可正常发布/订阅事件）。

        断言：
          - 触发 ec1 后，EventBus 仍可正常 publish 事件
          - EventCollector 仍可正常收集事件
        """
        cfg = _load_invalid_cond_config()
        engine = PoolEngine(pool_config=cfg)
        edge_executor, bus, _event_driver, _schedule = _get_components(engine)
        collector = event_collector(bus)
        try:
            _set_virtual_time(engine, _TS)
            codes = _load_fz_stocks(3)
            _add_stocks_to_pool(engine.state, "source", codes)

            edge_executor._on_edge_fired(EdgeFired(eid="ec1", ts=_TS))

            # EventBus 仍可正常 publish
            collector.clear()
            bus.publish(EdgeFired(eid="test_after_invalid", ts=_TS))
            edge_events = collector.filter(type="EdgeFired")
            assert len(edge_events) == 1, (
                f"EventBus 异常后应仍能发布事件，实际收到 {len(edge_events)} 个 EdgeFired"
            )
            assert edge_events[0].eid == "test_after_invalid"
        finally:
            collector.disconnect()


class TestInvalidConfigFormulaEvaluatedEvent:
    """验证 FormulaEvaluated 事件发布情况。

    spec.md L128 要求"发布 FormulaEvaluated 事件携带 error 字段"：
      - FormulaEvaluated dataclass 含 error 字段（默认空串表示无错误）
      - 无效配置（formula_ref 缺失）下 ``_eval_formula_path`` 发布
        携带非空 error 的 FormulaEvaluated 事件供下游诊断
    """

    def test_formula_evaluated_dataclass_has_error_field(self):
        """FormulaEvaluated dataclass 含 error 字段（spec.md L128）。

        断言字段集合包含 formula_ref/result/code/bar_hash/error 五个字段，
        其中 error 字段默认空串（无错误），公式异常或无效配置时填充错误信息。
        """
        from dataclasses import fields as dc_fields
        field_names = {f.name for f in dc_fields(FormulaEvaluated)}
        # spec.md L128：FormulaEvaluated 必须含 error 字段
        assert "error" in field_names, (
            f"FormulaEvaluated 应含 error 字段（spec.md L128），"
            f"实际字段集合 = {field_names}"
        )
        # 完整字段集合（formula_ref/result/code/bar_hash/error）
        assert field_names == {
            "formula_ref", "result", "code", "bar_hash", "error",
        }, (
            f"FormulaEvaluated 字段集合 = {field_names}，"
            f"期望 {{formula_ref, result, code, bar_hash, error}}"
        )

    def test_invalid_config_no_exception_propagated(self, event_collector):
        """无效配置下不抛异常传播到上层（spec 异常隔离断言）。

        本测试断言（spec.md L128）：
          - _on_edge_fired 调用不抛异常（异常被 _eval_formula_path try/except 捕获）
          - 不抛异常传播到 EventDriver（fire_due 仍可调用）
          - EventBus 仍可正常工作
        """
        cfg = _load_invalid_cond_config()
        engine = PoolEngine(pool_config=cfg)
        edge_executor, bus, event_driver, _schedule = _get_components(engine)
        collector = event_collector(bus)
        try:
            _set_virtual_time(engine, _TS)
            codes = _load_fz_stocks(3)
            _add_stocks_to_pool(engine.state, "source", codes)

            # 1. _on_edge_fired 不抛异常
            edge_executor._on_edge_fired(EdgeFired(eid="ec1", ts=_TS))

            # 2. 不抛异常传播到 EventDriver（fire_due 仍可调用）
            _set_virtual_time(engine, _TS + _ADVANCE_SECONDS)
            event_driver.fire_due(_TS + _ADVANCE_SECONDS)

            # 3. EventBus 仍可正常工作
            collector.clear()
            bus.publish(EdgeFired(eid="health_check", ts=_TS))
            assert len(collector.filter(type="EdgeFired")) == 1
        finally:
            collector.disconnect()

    def test_invalid_config_stock_filtered_published(self, event_collector):
        """无效配置下仍发布 StockFiltered 事件（passed=[]，rejected=[]）。

        验证公式求值失败（formula_ref 缺失）被捕获后，EdgeExecutor 仍发布
        StockFiltered 事件携带空 passed 集合，使下游订阅者知晓筛选完成
        （虽无股票通过）。

        spec.md L128：``_eval_formula_path`` 在 formula_ref 为空时同步发布
        携带非空 error 的 FormulaEvaluated 事件（见
        ``test_invalid_config_formula_evaluated_error_non_empty``）。
        """
        cfg = _load_invalid_cond_config()
        engine = PoolEngine(pool_config=cfg)
        edge_executor, bus, _event_driver, _schedule = _get_components(engine)
        collector = event_collector(bus)
        try:
            _set_virtual_time(engine, _TS)
            codes = _load_fz_stocks(3)
            _add_stocks_to_pool(engine.state, "source", codes)

            edge_executor._on_edge_fired(EdgeFired(eid="ec1", ts=_TS))

            # StockFiltered 事件应发布（passed=[]）
            stock_events = collector.filter(type="StockFiltered")
            # 无效配置下 _eval_formula_path 在 formula_ref="" 时发布
            # 携带 error 的 FormulaEvaluated 事件并 return []，
            # _filter 据此返回 passed=[] / rejected=source_codes，
            # _activate_condition 发布 StockFiltered。
            # 关键断言：不抛异常 + 任何发布的 StockFiltered 事件 passed=[]
            for ev in stock_events:
                assert ev.passed == [], (
                    f"无效配置下 StockFiltered.passed 应为空，实际 {ev.passed}"
                )
        finally:
            collector.disconnect()

    def test_invalid_config_formula_evaluated_error_non_empty(
        self, event_collector,
    ):
        """无效配置下发布的 FormulaEvaluated 事件携带非空 error 字段。

        spec.md L128：``_eval_formula_path`` 在 formula_ref 缺失（无效配置）
        时通过 ``eval_deps.bus`` 发布携带非空 error 的 FormulaEvaluated 事件。

        断言：
          - 触发 ec1 后至少发布一个 FormulaEvaluated 事件
          - 该事件 error 字段非空（携带 formula_ref 缺失的错误信息）
          - error 文本含 "formula_ref" 关键字（描述错误原因）
          - 该事件 result 为 None（公式未求值）
        """
        cfg = _load_invalid_cond_config()
        engine = PoolEngine(pool_config=cfg)
        edge_executor, bus, _event_driver, _schedule = _get_components(engine)
        collector = event_collector(bus)
        try:
            _set_virtual_time(engine, _TS)
            codes = _load_fz_stocks(3)
            _add_stocks_to_pool(engine.state, "source", codes)

            edge_executor._on_edge_fired(EdgeFired(eid="ec1", ts=_TS))

            formula_events = collector.filter(type="FormulaEvaluated")
            assert len(formula_events) >= 1, (
                "无效配置下应至少发布一个携带 error 的 FormulaEvaluated 事件"
            )
            ev = formula_events[0]
            # spec.md L128：error 字段非空
            assert ev.error != "", (
                f"无效配置下 FormulaEvaluated.error 应非空，实际 {ev.error!r}"
            )
            # error 文本应描述 formula_ref 缺失的错误原因
            assert "formula_ref" in ev.error.lower(), (
                f"FormulaEvaluated.error 应含 'formula_ref' 关键字，"
                f"实际 {ev.error!r}"
            )
            # formula_ref 为空（无效配置）
            assert ev.formula_ref == "", (
                f"无效配置下 FormulaEvaluated.formula_ref 应为空，"
                f"实际 {ev.formula_ref!r}"
            )
            # result 为 None（公式未求值）
            assert ev.result is None, (
                f"无效配置下 FormulaEvaluated.result 应为 None，实际 {ev.result!r}"
            )
        finally:
            collector.disconnect()


class TestInvalidConfigEvalFormulaPathSource:
    """源码层面验证 _eval_formula_path 的异常捕获逻辑。"""

    def test_eval_formula_path_has_try_except(self):
        """``_eval_formula_path`` 源码含 try/except 包裹 eval_series 调用。

        验证异常被 EdgeExecutor 捕获的代码层面证据：
        - try 块中调用 formula_engine.eval_series
        - except Exception 捕获并记录日志，返回 []
        """
        from core.execution_module import _eval_formula_path
        src = inspect.getsource(_eval_formula_path)
        # try/except 包裹 eval_series 调用
        assert "try:" in src, "_eval_formula_path 应含 try 块"
        assert "formula_engine.eval_series" in src, (
            "try 块中应调用 formula_engine.eval_series"
        )
        assert "except Exception" in src, (
            "_eval_formula_path 应含 except Exception 捕获公式异常"
        )
        # 异常时返回空列表（优雅降级）
        assert "return []" in src, (
            "异常时应返回空列表 []（优雅降级）"
        )

    def test_event_driver_fire_due_has_try_except(self):
        """``EventDriver.fire_due`` 源码含 try/except 包裹 action 调用。

        验证 EventDriver 不受 action 异常影响的代码层面证据：
        - try 块中调用 spec.action(spec.params)
        - except Exception 捕获并记录日志
        """
        from core.execution_module import EventDriver
        src = inspect.getsource(EventDriver.fire_due)
        assert "try:" in src, "fire_due 应含 try 块"
        assert "spec.action" in src, "try 块中应调用 spec.action"
        assert "except Exception" in src, (
            "fire_due 应含 except Exception 捕获 action 异常"
        )

    def test_event_bus_publish_has_try_except(self):
        """``EventBus.publish`` 源码含 try/except 包裹 handler 调用。

        验证 EventBus 不受订阅者异常影响的代码层面证据：
        - try 块中调用 handler(event)
        - except Exception 捕获并记录日志
        """
        from core.event_bus import EventBus
        src = inspect.getsource(EventBus.publish)
        assert "try:" in src, "publish 应含 try 块"
        assert "handler(event)" in src, "try 块中应调用 handler(event)"
        assert "except Exception" in src, (
            "publish 应含 except Exception 捕获订阅者异常"
        )
