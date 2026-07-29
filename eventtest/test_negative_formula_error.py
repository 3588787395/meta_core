# -*- coding: utf-8 -*-
"""反测试 — 公式计算异常返回空（Task 8 SubTask 8.2）。

基于 spec.md "反测试（异常与边界验证）" → "公式计算异常返回空" Scenario（L145-148）：

    WHEN FormulaEngine.eval_series 返回空 dict
    THEN StockFiltered passed 集合为空
    AND  不抛 KeyError

实现要点：
    - 基于 ``config/pools/sim_test_pool_100.json`` 动态读取 fz 前缀股票代码（不硬编码）。
    - 装配 PoolEngine 实例，复用 conftest.pool_engine 工厂模式。
    - 通过替换 ``edge_executor.formula_engine`` 注入 mock 公式引擎，构造两类异常：
      A. ``eval_series`` 返回空 dict ``{}``：所有 code 在 ``series_results.get(code)``
         处得到 None → ``sres is None`` → continue → ``passed=[]``（spec L145-148）。
      B. ``eval_series`` 抛出异常（如 ValueError）：被 ``_eval_formula_path`` 的
         try/except 捕获，发布携带非空 ``error`` 字段的 FormulaEvaluated 事件，
         返回 ``[]``（spec.md L128 + Task 6 修复）。
    - 触发 ``EdgeFired(eid="ec1")`` 激活 cond1 条件节点（条件节点激活模型 SubTask 8.1），
      验证：
        1. ``StockFiltered.passed == []``（FormulaEngine 返回空）
        2. 不抛 KeyError 或其他异常（异常被 ``_eval_formula_path`` try/except 捕获）
        3. 异常路径下 ``FormulaEvaluated.error`` 字段非空（参考 Task 6 修复）
        4. 异常不传播到 EventDriver（后续 ``fire_due`` 仍可正常调用）

复用 core/ 现有类（PoolEngine / EdgeExecutor / EventBus / EventDriver），
不修改 core/ 源文件，不使用已删除旧接口（get_node_stocks / SimTickSource /
execution_order / EdgeFired.changed_codes / at_fn / fire_ttl_due / TtlTracker）。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from core.engine import PoolEngine
from core.event_bus import (
    EdgeFired,
    EventBus,
    FormulaEvaluated,
    StockFiltered,
)


# ═══════════════════════════════════════════════════════════════
# 测试常量（测试参数，非硬编码实例内容）
# ═══════════════════════════════════════════════════════════════

_TS = 34500.0              # 虚拟时钟起点（09:30:00 当日秒数偏移）
_ADVANCE_SECONDS = 600    # 推进虚拟时钟秒数（> 最大边间隔 60s，确保 fire_due 触发）

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_POOL_CONFIG = _PROJECT_ROOT / "config" / "pools" / "sim_test_pool_100.json"


# ═══════════════════════════════════════════════════════════════
# Mock 公式引擎
# ═══════════════════════════════════════════════════════════════


class _EmptyDictFormulaEngine:
    """Mock 公式引擎：eval_series 永远返回空 dict {}。

    模拟 spec L145 "FormulaEngine.eval_series 返回空 dict" 场景。
    所有 code 在 ``series_results.get(code)`` 处得到 None，
    被 ``_eval_formula_path`` 中的 ``if sres is None: continue`` 跳过，
    最终 ``passed=[]``。
    """

    def eval_series(
        self, spec: Any, codes: List[str], ctx: Any, lookback: int = 5,
    ) -> Dict[str, Any]:
        return {}

    def eval_scalar(
        self,
        spec: Any,
        codes: List[str],
        ctx: Any,
        evaluator_fn: Any,
    ) -> Dict[str, Any]:
        return {}


class _RaisingFormulaEngine:
    """Mock 公式引擎：eval_series 永远抛 ValueError。

    模拟公式求值异常路径。异常被 ``_eval_formula_path`` 的 try/except 捕获，
    发布携带非空 ``error`` 字段的 FormulaEvaluated 事件，返回 ``[]``。
    """

    def eval_series(
        self, spec: Any, codes: List[str], ctx: Any, lookback: int = 5,
    ) -> Dict[str, Any]:
        raise ValueError("mock formula engine failure for testing")

    def eval_scalar(
        self,
        spec: Any,
        codes: List[str],
        ctx: Any,
        evaluator_fn: Any,
    ) -> Dict[str, Any]:
        raise ValueError("mock formula engine failure for testing")


class _NoneValueFormulaEngine:
    """Mock 公式引擎：eval_series 返回 {code: None}。

    模拟公式返回 None 值场景，验证 ``_extract_line_from_series`` 的 None 安全处理
    （``if not series_result: return None``），不抛 KeyError。
    """

    def eval_series(
        self, spec: Any, codes: List[str], ctx: Any, lookback: int = 5,
    ) -> Dict[str, Any]:
        return {code: None for code in codes}

    def eval_scalar(
        self,
        spec: Any,
        codes: List[str],
        ctx: Any,
        evaluator_fn: Any,
    ) -> Dict[str, Any]:
        return {code: None for code in codes}


# ═══════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════


def _get_components(engine: PoolEngine):
    """从 PoolEngine 提取测试所需组件。"""
    return (
        engine._components["edge_executor"],
        engine._components["event_bus"],
        engine._components["event_driver"],
        engine._components["schedule"],
    )


def _set_virtual_time(engine: PoolEngine, ts: float) -> None:
    """设置 engine.state.time_source 的 current_ts 为指定时刻。"""
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
# 测试用例 — 场景 A：eval_series 返回空 dict
# ═══════════════════════════════════════════════════════════════


class TestFormulaErrorEmptyDict:
    """验证 FormulaEngine.eval_series 返回空 dict 时 passed 集合为空。

    场景：mock 公式引擎 eval_series 返回 {} → 所有 code 被
    ``_eval_formula_path`` 中的 ``if sres is None: continue`` 跳过 → passed=[]。
    """

    def test_eval_series_empty_dict_passed_empty(self, event_collector):
        """eval_series 返回 {} 时 StockFiltered.passed 为空。

        断言：
          - 触发 ec1 后至少发布一个 StockFiltered 事件
          - 所有 StockFiltered 事件 passed 集合为空
        """
        engine = PoolEngine(pool_config=json.loads(_DEFAULT_POOL_CONFIG.read_text(encoding="utf-8")))
        edge_executor, bus, _event_driver, _schedule = _get_components(engine)
        # 注入 mock 公式引擎（eval_series 永远返回 {}）
        edge_executor.formula_engine = _EmptyDictFormulaEngine()
        collector = event_collector(bus)
        try:
            _set_virtual_time(engine, _TS)
            codes = _load_fz_stocks(3)
            _add_stocks_to_pool(engine.state, "source", codes)

            edge_executor._on_edge_fired(EdgeFired(eid="ec1", ts=_TS))

            stock_events = collector.filter(type="StockFiltered")
            assert len(stock_events) >= 1, (
                "eval_series 返回空 dict 时应至少发布一个 StockFiltered 事件"
            )
            for ev in stock_events:
                assert ev.passed == [], (
                    f"eval_series 返回空 dict 时 StockFiltered.passed 应为空，"
                    f"实际 {ev.passed}"
                )
        finally:
            collector.disconnect()

    def test_eval_series_empty_dict_no_keyerror(self, event_collector):
        """eval_series 返回 {} 时不抛 KeyError 或其他异常。

        断言：
          - _on_edge_fired 调用不抛异常（异常被 _eval_formula_path try/except 捕获，
            或 None 安全处理不触发 KeyError）
        """
        engine = PoolEngine(pool_config=json.loads(_DEFAULT_POOL_CONFIG.read_text(encoding="utf-8")))
        edge_executor, bus, _event_driver, _schedule = _get_components(engine)
        edge_executor.formula_engine = _EmptyDictFormulaEngine()
        collector = event_collector(bus)
        try:
            _set_virtual_time(engine, _TS)
            codes = _load_fz_stocks(3)
            _add_stocks_to_pool(engine.state, "source", codes)

            # 不应抛异常
            edge_executor._on_edge_fired(EdgeFired(eid="ec1", ts=_TS))
        finally:
            collector.disconnect()

    def test_eval_series_empty_dict_pool_a_remains_empty(self, event_collector):
        """eval_series 返回 {} 时 cond1 激活后 pool_A 仍为空。

        断言：
          - 触发 ec1 后 pool_A = 0 只股票（无股票通过筛选入池）
        """
        engine = PoolEngine(pool_config=json.loads(_DEFAULT_POOL_CONFIG.read_text(encoding="utf-8")))
        edge_executor, bus, _event_driver, _schedule = _get_components(engine)
        edge_executor.formula_engine = _EmptyDictFormulaEngine()
        collector = event_collector(bus)
        try:
            _set_virtual_time(engine, _TS)
            codes = _load_fz_stocks(3)
            _add_stocks_to_pool(engine.state, "source", codes)

            edge_executor._on_edge_fired(EdgeFired(eid="ec1", ts=_TS))

            pool_a_stocks = engine.state.get_pool("pool_A").get_stocks()
            assert len(pool_a_stocks) == 0, (
                f"eval_series 返回空 dict 时 pool_A 应为空，"
                f"实际 {len(pool_a_stocks)} 只股票: {pool_a_stocks}"
            )
        finally:
            collector.disconnect()


# ═══════════════════════════════════════════════════════════════
# 测试用例 — 场景 B：eval_series 抛异常
# ═══════════════════════════════════════════════════════════════


class TestFormulaErrorException:
    """验证 FormulaEngine.eval_series 抛异常时的优雅降级。

    场景：mock 公式引擎 eval_series 抛 ValueError →
    ``_eval_formula_path`` 的 try/except 捕获 →
    发布携带非空 error 字段的 FormulaEvaluated 事件 → 返回 []。
    """

    def test_eval_series_exception_no_propagation(self, event_collector):
        """eval_series 抛异常时不传播到上层（异常被 _eval_formula_path try/except 捕获）。

        断言：
          - _on_edge_fired 调用不抛异常
        """
        engine = PoolEngine(pool_config=json.loads(_DEFAULT_POOL_CONFIG.read_text(encoding="utf-8")))
        edge_executor, bus, _event_driver, _schedule = _get_components(engine)
        edge_executor.formula_engine = _RaisingFormulaEngine()
        collector = event_collector(bus)
        try:
            _set_virtual_time(engine, _TS)
            codes = _load_fz_stocks(3)
            _add_stocks_to_pool(engine.state, "source", codes)

            # 不应抛异常（异常被 _eval_formula_path try/except 捕获）
            edge_executor._on_edge_fired(EdgeFired(eid="ec1", ts=_TS))
        finally:
            collector.disconnect()

    def test_eval_series_exception_passed_empty(self, event_collector):
        """eval_series 抛异常时 StockFiltered.passed 为空。

        断言：
          - 触发 ec1 后至少发布一个 StockFiltered 事件
          - 所有 StockFiltered 事件 passed 集合为空
        """
        engine = PoolEngine(pool_config=json.loads(_DEFAULT_POOL_CONFIG.read_text(encoding="utf-8")))
        edge_executor, bus, _event_driver, _schedule = _get_components(engine)
        edge_executor.formula_engine = _RaisingFormulaEngine()
        collector = event_collector(bus)
        try:
            _set_virtual_time(engine, _TS)
            codes = _load_fz_stocks(3)
            _add_stocks_to_pool(engine.state, "source", codes)

            edge_executor._on_edge_fired(EdgeFired(eid="ec1", ts=_TS))

            stock_events = collector.filter(type="StockFiltered")
            assert len(stock_events) >= 1, (
                "eval_series 抛异常时应至少发布一个 StockFiltered 事件"
            )
            for ev in stock_events:
                assert ev.passed == [], (
                    f"eval_series 抛异常时 StockFiltered.passed 应为空，"
                    f"实际 {ev.passed}"
                )
        finally:
            collector.disconnect()

    def test_eval_series_exception_formula_evaluated_error_non_empty(
        self, event_collector,
    ):
        """eval_series 抛异常时发布的 FormulaEvaluated 事件携带非空 error 字段。

        spec.md L128：``_eval_formula_path`` 在公式求值异常时通过 ``eval_deps.bus``
        发布携带非空 error 的 FormulaEvaluated 事件供下游诊断。

        断言：
          - 触发 ec1 后至少发布一个 FormulaEvaluated 事件
          - 该事件 error 字段非空（携带异常信息）
          - error 文本含异常消息关键字（如 "mock"）
          - 该事件 result 为 None（公式未求值）
        """
        engine = PoolEngine(pool_config=json.loads(_DEFAULT_POOL_CONFIG.read_text(encoding="utf-8")))
        edge_executor, bus, _event_driver, _schedule = _get_components(engine)
        edge_executor.formula_engine = _RaisingFormulaEngine()
        collector = event_collector(bus)
        try:
            _set_virtual_time(engine, _TS)
            codes = _load_fz_stocks(3)
            _add_stocks_to_pool(engine.state, "source", codes)

            edge_executor._on_edge_fired(EdgeFired(eid="ec1", ts=_TS))

            formula_events = collector.filter(type="FormulaEvaluated")
            # 找到携带非空 error 的事件（_eval_formula_path 异常路径发布）
            error_events = [e for e in formula_events if e.error]
            assert len(error_events) >= 1, (
                "eval_series 抛异常时应至少发布一个携带非空 error 的 "
                "FormulaEvaluated 事件"
            )
            ev = error_events[0]
            # spec.md L128：error 字段非空
            assert ev.error != "", (
                f"FormulaEvaluated.error 应非空，实际 {ev.error!r}"
            )
            # error 文本应含异常消息关键字
            assert "mock" in ev.error.lower(), (
                f"FormulaEvaluated.error 应含异常消息关键字 'mock'，"
                f"实际 {ev.error!r}"
            )
            # result 为 None（公式未求值）
            assert ev.result is None, (
                f"FormulaEvaluated.result 应为 None，实际 {ev.result!r}"
            )
        finally:
            collector.disconnect()

    def test_eval_series_exception_not_propagated_to_event_driver(
        self, event_collector,
    ):
        """eval_series 抛异常后异常不传播到 EventDriver（后续 fire_due 仍可正常调用）。

        断言：
          - 触发 ec1 后，event_driver.fire_due 仍可正常调用（EventDriver 未崩溃）
          - fire_due 不抛异常
        """
        engine = PoolEngine(pool_config=json.loads(_DEFAULT_POOL_CONFIG.read_text(encoding="utf-8")))
        edge_executor, bus, event_driver, _schedule = _get_components(engine)
        edge_executor.formula_engine = _RaisingFormulaEngine()
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


# ═══════════════════════════════════════════════════════════════
# 测试用例 — 场景 C：eval_series 返回 None 值
# ═══════════════════════════════════════════════════════════════


class TestFormulaErrorNoneValue:
    """验证 FormulaEngine.eval_series 返回 {code: None} 时不抛 KeyError。

    场景：mock 公式引擎 eval_series 返回 {code: None} →
    ``_extract_line_from_series`` 在 ``if not series_result: return None``
    处理 None 值，不抛 KeyError → passed=[]。
    """

    def test_eval_series_none_value_no_keyerror(self, event_collector):
        """eval_series 返回 {code: None} 时不抛 KeyError。

        断言：
          - _on_edge_fired 调用不抛异常（None 值被安全处理）
        """
        engine = PoolEngine(pool_config=json.loads(_DEFAULT_POOL_CONFIG.read_text(encoding="utf-8")))
        edge_executor, bus, _event_driver, _schedule = _get_components(engine)
        edge_executor.formula_engine = _NoneValueFormulaEngine()
        collector = event_collector(bus)
        try:
            _set_virtual_time(engine, _TS)
            codes = _load_fz_stocks(3)
            _add_stocks_to_pool(engine.state, "source", codes)

            # 不应抛 KeyError 或其他异常
            edge_executor._on_edge_fired(EdgeFired(eid="ec1", ts=_TS))
        finally:
            collector.disconnect()

    def test_eval_series_none_value_passed_empty(self, event_collector):
        """eval_series 返回 {code: None} 时 StockFiltered.passed 为空。

        断言：
          - 触发 ec1 后至少发布一个 StockFiltered 事件
          - 所有 StockFiltered 事件 passed 集合为空
        """
        engine = PoolEngine(pool_config=json.loads(_DEFAULT_POOL_CONFIG.read_text(encoding="utf-8")))
        edge_executor, bus, _event_driver, _schedule = _get_components(engine)
        edge_executor.formula_engine = _NoneValueFormulaEngine()
        collector = event_collector(bus)
        try:
            _set_virtual_time(engine, _TS)
            codes = _load_fz_stocks(3)
            _add_stocks_to_pool(engine.state, "source", codes)

            edge_executor._on_edge_fired(EdgeFired(eid="ec1", ts=_TS))

            stock_events = collector.filter(type="StockFiltered")
            assert len(stock_events) >= 1, (
                "eval_series 返回 {code: None} 时应至少发布一个 StockFiltered 事件"
            )
            for ev in stock_events:
                assert ev.passed == [], (
                    f"eval_series 返回 {{code: None}} 时 StockFiltered.passed 应为空，"
                    f"实际 {ev.passed}"
                )
        finally:
            collector.disconnect()

    def test_eval_series_none_value_event_bus_not_corrupted(self, event_collector):
        """eval_series 返回 {code: None} 后 EventBus 仍可正常工作。

        断言：
          - 触发 ec1 后，EventBus 仍可正常 publish 事件
          - EventCollector 仍可正常收集事件
        """
        engine = PoolEngine(pool_config=json.loads(_DEFAULT_POOL_CONFIG.read_text(encoding="utf-8")))
        edge_executor, bus, _event_driver, _schedule = _get_components(engine)
        edge_executor.formula_engine = _NoneValueFormulaEngine()
        collector = event_collector(bus)
        try:
            _set_virtual_time(engine, _TS)
            codes = _load_fz_stocks(3)
            _add_stocks_to_pool(engine.state, "source", codes)

            edge_executor._on_edge_fired(EdgeFired(eid="ec1", ts=_TS))

            # EventBus 仍可正常 publish
            collector.clear()
            bus.publish(EdgeFired(eid="health_check", ts=_TS))
            edge_events = collector.filter(type="EdgeFired")
            assert len(edge_events) == 1, (
                f"EventBus 异常后应仍能发布事件，实际收到 {len(edge_events)} 个 EdgeFired"
            )
            assert edge_events[0].eid == "health_check"
        finally:
            collector.disconnect()
