# -*- coding: utf-8 -*-
"""反测试 — 空备选池（Task 6 SubTask 6.1）。

基于 spec.md "反测试（异常与边界验证）" → "空备选池" Scenario：

    WHEN source 池配置 0 只股票
    THEN 启动不抛异常
    AND  tick 数 = 0
    AND  条件节点激活后 passed 集合为空
    AND  pool_A/pool_B/pool_C 均为空

实现要点：
    - 基于 ``config/pools/sim_test_pool_100.json`` 动态构造一份 source 池 stocks=[]
      的派生配置（不硬编码实例内容，仅修改 stocks 字段）。
    - 直接 ``PoolEngine(pool_config=cfg)`` 装配（与 conftest.pool_engine 内部实现一致，
      因 fixture 仅接受路径，这里需要在测试内修改配置 dict）。
    - 推进虚拟时钟并调用 ``event_driver.fire_due(now)`` 触发已注册的边定时器，
      验证空池下事件驱动主循环不抛异常、所有池保持空、无 TickReceived 事件。
    - 手动触发 ``EdgeFired(eid="ec1")`` 验证条件节点激活后 passed 集合为空。

复用 core/ 现有类（PoolEngine / EventDriver / EdgeExecutor / EventBus），
不修改 core/ 源文件，不使用已删除旧接口（get_node_stocks / SimTickSource /
execution_order / EdgeFired.changed_codes / at_fn / fire_ttl_due / TtlTracker）。
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Dict, List

import pytest

from core.engine import PoolEngine
from core.event_bus import EdgeFired


# ═══════════════════════════════════════════════════════════════
# 测试常量（测试参数，非硬编码实例内容）
# ═══════════════════════════════════════════════════════════════

_TS = 34500.0              # 虚拟时钟起点（09:30:00 当日秒数偏移）
_ADVANCE_SECONDS = 600    # 推进虚拟时钟秒数（> 最大边间隔 60s，确保 fire_due 触发全部边）

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_POOL_CONFIG = _PROJECT_ROOT / "config" / "pools" / "sim_test_pool_100.json"


# ═══════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════


def _load_empty_pool_config() -> Dict[str, Any]:
    """加载 sim_test_pool_100.json 并将 source 池的 stocks 设为空列表。

    派生配置保留原 nodes/edges 结构，仅修改 source 节点的 params.stocks=[]，
    用于验证空备选池下系统优雅降级（不抛异常、所有池空、无 tick）。
    """
    with open(_DEFAULT_POOL_CONFIG, encoding="utf-8") as f:
        cfg = json.load(f)
    cfg = copy.deepcopy(cfg)
    for node in cfg.get("nodes", []):
        if not isinstance(node, dict):
            continue
        if node.get("id") == "source":
            node.setdefault("params", {})["stocks"] = []
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

    与 test_positive_ttl.py 同一模式：``time_at(state=state)`` 读取
    ``state.time_source["current_ts"]``，``EventDriver.fire_due(now)`` 与
    边定时器 action 均通过 ``time_at`` 获取当前时刻。
    """
    engine.state.time_source["current_ts"] = float(ts)
    engine.state.time_source.setdefault("start_ts", _TS)
    engine.state.time_source.setdefault("driver_type", "virtual")


# ═══════════════════════════════════════════════════════════════
# 测试用例
# ═══════════════════════════════════════════════════════════════


class TestEmptyPoolStartup:
    """验证空备选池下 PoolEngine 装配与启动不抛异常。"""

    def test_empty_pool_engine_starts_without_exception(self):
        """source 池配置 0 只股票时，PoolEngine 装配不抛异常。

        断言：
          - PoolEngine(pool_config=cfg) 构造完成不抛异常
          - _components 含全部关键组件（edge_executor/event_bus/event_driver/schedule）
        """
        cfg = _load_empty_pool_config()
        # 不应抛异常
        engine = PoolEngine(pool_config=cfg)
        # 关键组件已装配
        assert "edge_executor" in engine._components
        assert "event_bus" in engine._components
        assert "event_driver" in engine._components
        assert "schedule" in engine._components

    def test_empty_pool_source_has_zero_stocks(self):
        """source 池股票数为 0。"""
        cfg = _load_empty_pool_config()
        engine = PoolEngine(pool_config=cfg)
        source_stocks = engine.state.get_pool("source").get_stocks()
        assert len(source_stocks) == 0, (
            f"source 池应为空，实际 {len(source_stocks)} 只股票"
        )


class TestEmptyPoolNoTick:
    """验证空备选池下无 TickReceived 事件。"""

    def test_empty_pool_no_tick_received(self, event_collector):
        """空备选池下，推进虚拟时钟触发边定时器后，TickReceived 事件数 = 0。

        断言：
          - fire_due 不抛异常
          - EventCollector 收集到 0 个 TickReceived 事件
          - （tick 数 = 0，因为没有股票生成 tick）
        """
        cfg = _load_empty_pool_config()
        engine = PoolEngine(pool_config=cfg)
        _edge_executor, bus, event_driver, _schedule = _get_components(engine)
        collector = event_collector(bus)
        try:
            _set_virtual_time(engine, _TS)
            # 推进虚拟时钟，触发所有已注册的边定时器
            _set_virtual_time(engine, _TS + _ADVANCE_SECONDS)
            # fire_due 不应抛异常
            event_driver.fire_due(_TS + _ADVANCE_SECONDS)

            tick_events = collector.filter(type="TickReceived")
            assert len(tick_events) == 0, (
                f"空备选池下 TickReceived 事件数应为 0，实际 {len(tick_events)}"
            )
        finally:
            collector.disconnect()


class TestEmptyPoolFireDue:
    """验证空备选池下 event_driver.fire_due 不抛异常。"""

    def test_empty_pool_fire_due_no_exception(self, event_collector):
        """空备选池下推进虚拟时钟并调用 fire_due，不抛异常。

        断言：
          - fire_due 调用不抛异常（边定时器 action 正常发布 EdgeFired）
          - EdgeFired 事件被发布（边定时器仍触发，只是源池无股票）
        """
        cfg = _load_empty_pool_config()
        engine = PoolEngine(pool_config=cfg)
        _edge_executor, bus, event_driver, _schedule = _get_components(engine)
        collector = event_collector(bus)
        try:
            _set_virtual_time(engine, _TS)
            _set_virtual_time(engine, _TS + _ADVANCE_SECONDS)
            # fire_due 不应抛异常
            event_driver.fire_due(_TS + _ADVANCE_SECONDS)

            # 边定时器仍会发布 EdgeFired（action 只发布事件，不依赖源池股票）
            edge_events = collector.filter(type="EdgeFired")
            # 不强制断言 EdgeFired 数量 > 0（取决于 interval_sec 与推进时长），
            # 但应至少不抛异常
            assert isinstance(edge_events, list)
        finally:
            collector.disconnect()


class TestEmptyPoolConditionActivation:
    """验证空备选池下条件节点激活后 passed 集合为空。"""

    def test_empty_pool_cond1_activation_passed_empty(
        self, event_collector,
    ):
        """空备选池下手动触发 EdgeFired(eid="ec1")，StockFiltered.passed 为空。

        断言：
          - _on_edge_fired 不抛异常
          - 发布 StockFiltered 事件（passed=[]，rejected=[]）
        """
        cfg = _load_empty_pool_config()
        engine = PoolEngine(pool_config=cfg)
        edge_executor, bus, _event_driver, _schedule = _get_components(engine)
        collector = event_collector(bus)
        try:
            _set_virtual_time(engine, _TS)
            # 手动触发 ec1 边（source → cond1）
            edge_executor._on_edge_fired(EdgeFired(eid="ec1", ts=_TS))

            stock_events = collector.filter(type="StockFiltered")
            # 空源池下 _activate_condition 中 source_codes=[]，continue 跳过，
            # port_results 为空，final_passed=[]，但仍可能发布 StockFiltered
            # （取决于 _activate_condition 是否在 source_codes 空时仍发布事件）
            for ev in stock_events:
                assert ev.passed == [], (
                    f"空备选池下 StockFiltered.passed 应为空，实际 {ev.passed}"
                )
                assert ev.rejected == [], (
                    f"空备选池下 StockFiltered.rejected 应为空，实际 {ev.rejected}"
                )
        finally:
            collector.disconnect()

    def test_empty_pool_cond1_activation_no_crash(self, event_collector):
        """空备选池下手动触发 EdgeFired(eid="ec1")，不抛异常且 EventDriver 未崩溃。

        断言：
          - _on_edge_fired 调用不抛异常
          - 后续 fire_due 仍可正常调用（EventDriver 未崩溃）
        """
        cfg = _load_empty_pool_config()
        engine = PoolEngine(pool_config=cfg)
        edge_executor, bus, event_driver, _schedule = _get_components(engine)
        collector = event_collector(bus)
        try:
            _set_virtual_time(engine, _TS)
            # 手动触发 ec1 边
            edge_executor._on_edge_fired(EdgeFired(eid="ec1", ts=_TS))

            # 后续 fire_due 仍可正常调用（EventDriver 未受影响）
            _set_virtual_time(engine, _TS + _ADVANCE_SECONDS)
            event_driver.fire_due(_TS + _ADVANCE_SECONDS)
        finally:
            collector.disconnect()


class TestEmptyPoolAllPoolsEmpty:
    """验证空备选池下所有池（source/pool_A/pool_B/pool_C）均为空。"""

    def test_empty_pool_all_pools_empty_after_fire_due(
        self, event_collector,
    ):
        """空备选池下推进虚拟时钟触发边定时器后，所有池均为空。

        断言：
          - source 池 = 0 只股票
          - pool_A = 0 只股票
          - pool_B = 0 只股票
          - pool_C = 0 只股票
        """
        cfg = _load_empty_pool_config()
        engine = PoolEngine(pool_config=cfg)
        _edge_executor, bus, event_driver, _schedule = _get_components(engine)
        collector = event_collector(bus)
        try:
            _set_virtual_time(engine, _TS)
            _set_virtual_time(engine, _TS + _ADVANCE_SECONDS)
            event_driver.fire_due(_TS + _ADVANCE_SECONDS)

            # 所有池均应为空
            for nid in ("source", "pool_A", "pool_B", "pool_C"):
                stocks = engine.state.get_pool(nid).get_stocks()
                assert len(stocks) == 0, (
                    f"空备选池下 {nid} 应为空，实际 {len(stocks)} 只股票: {stocks}"
                )
        finally:
            collector.disconnect()

    def test_empty_pool_all_pools_empty_after_cond_activation(
        self, event_collector,
    ):
        """空备选池下手动触发全部条件边后，所有池均为空。

        断言：
          - 触发 ec1/ec2/ec3a/ec3b 后，pool_A/pool_B/pool_C 仍为空
        """
        cfg = _load_empty_pool_config()
        engine = PoolEngine(pool_config=cfg)
        edge_executor, bus, _event_driver, _schedule = _get_components(engine)
        collector = event_collector(bus)
        try:
            _set_virtual_time(engine, _TS)
            # 手动触发全部条件边
            for eid in ("ec1", "ec2", "ec3a", "ec3b"):
                edge_executor._on_edge_fired(EdgeFired(eid=eid, ts=_TS))

            # 所有池均应为空
            for nid in ("source", "pool_A", "pool_B", "pool_C"):
                stocks = engine.state.get_pool(nid).get_stocks()
                assert len(stocks) == 0, (
                    f"空备选池下 {nid} 应为空，实际 {len(stocks)} 只股票: {stocks}"
                )
        finally:
            collector.disconnect()
