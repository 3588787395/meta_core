# -*- coding: utf-8 -*-
"""反测试 — 坏拓扑（Task 6 SubTask 6.1/6.3）。

基于 spec.md "反测试（异常与边界验证）" → "坏边拓扑（自环）" Scenario：

    WHEN 边 source=cond1, target=cond1
    THEN CompiledSchedule 构建抛出明确异常或跳过该边
    AND 不引发无限循环

另验证孤点（无入边无出边的节点）不引发异常。

实现要点：
    - 基于 ``config/pools/sim_test_pool_100.json`` 动态构造自环边配置与孤点配置
      （不硬编码实例内容，仅添加/修改 edges/nodes 字段）。
    - 自环边测试为正向断言：``Compiler.compile`` 对自环边抛 ``ValueError``
      （含"自环"/"loop"/"cycle" 关键词）。生产代码已在 ``_build_edge_ctx``
      中添加自环守卫（``core/execution_module.py``），不再触发旧 depths 计算的
      while changed 无限循环。
    - 孤点测试验证 compile 不抛异常、节点出现在 schedule.nodes、
      TTL spec 正确注册到 node_ttl_spec。
      G6 后 CompiledSchedule 不再保留 topo_order/depths/execution_order 运行时拓扑排序属性。
    - fire_due 测试验证 interval > 0 时不引发无限循环（next_time = fire_time +
      interval > now，单次 fire_due 调用不会重复弹堆）。

复用 core/ 现有类（Compiler / CompiledSchedule / EventDriver / TimedEventSpec），
不使用已删除旧接口（get_node_stocks / SimTickSource /
execution_order / topo_order / depths / EdgeFired.changed_codes / at_fn / fire_ttl_due / TtlTracker）。
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Dict

import pytest

from core.execution_module import (
    Compiler,
    CompiledSchedule,
    EventDriver,
    TimedEventSpec,
)


# ═══════════════════════════════════════════════════════════════
# 测试常量（测试参数，非硬编码实例内容）
# ═══════════════════════════════════════════════════════════════

_TS = 34500.0              # 虚拟时钟起点（09:30:00 当日秒数偏移）

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_POOL_CONFIG = _PROJECT_ROOT / "config" / "pools" / "sim_test_pool_100.json"


# ═══════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════


def _load_config() -> Dict[str, Any]:
    """加载 sim_test_pool_100.json 原始配置。"""
    with open(_DEFAULT_POOL_CONFIG, encoding="utf-8") as f:
        return json.load(f)


def _load_self_loop_config() -> Dict[str, Any]:
    """构造自环边配置：在原始配置基础上添加 source=cond1, target=cond1 的边。

    派生配置保留原 nodes/edges 结构，仅追加一条自环边 ``ec_self``：
      - source = "cond1"（条件节点）
      - target = "cond1"（同一条件节点）
      - type = "conditional"
      - params._order = 99（避免与现有边顺序号冲突）

    用于验证 ``Compiler.compile`` 对自环边的处理：
      - 正确行为（已修复）：``_build_edge_ctx`` 检测 sid==tid 并抛
        ``ValueError``（含"自环"/"loop"/"cycle" 关键词），不再触发
        depths 计算的 while changed 无限循环
    """
    cfg = copy.deepcopy(_load_config())
    cfg["edges"].append({
        "id": "ec_self",
        "source": "cond1",
        "target": "cond1",
        "type": "conditional",
        "params": {"_order": 99},
    })
    return cfg


def _load_orphan_node_config() -> Dict[str, Any]:
    """构造孤点配置：添加无入边无出边的 statepool 节点。

    派生配置保留原 nodes/edges 结构，仅追加一个 ``orphan_pool`` 节点：
      - id = "orphan_pool"
      - type = "statepool"
      - 无任何边引用该节点（既非 source 也非 target）
      - params 含 TTL 配置（bdel=1, ttl_mode=interval）

    用于验证 ``Compiler.compile`` 对孤点的处理：
      - 不抛异常
      - 节点出现在 schedule.nodes
      - TTL spec 正确注册到 node_ttl_spec（因无入边）
      - G6 后不再保留 topo_order/depths/execution_order 运行时拓扑排序属性
    """
    cfg = copy.deepcopy(_load_config())
    cfg["nodes"].append({
        "id": "orphan_pool",
        "type": "statepool",
        "name": "孤点测试池",
        "params": {
            "hold_seconds": 600,
            "ttl_mode": "interval",
            "psatt": {"bdel": 1, "ndelnum": 10, "ndeltype": 2},
        },
    })
    return cfg


# ═══════════════════════════════════════════════════════════════
# 测试用例 — 自环边
# ═══════════════════════════════════════════════════════════════


def test_self_loop_compile_raises_value_error():
    """自环边触发 Compiler.compile 抛 ValueError（spec.md L130-133 正向断言）。

    spec.md L130-133 要求：
        WHEN 边 source=cond1, target=cond1
        THEN CompiledSchedule 构建抛出明确异常或跳过该边
        AND 不引发无限循环

    生产代码已在 ``_build_edge_ctx``（core/execution_module.py）中添加自环守卫：
    当 ``sid == tid`` 时抛 ``ValueError``（消息含"自环"关键词），
    不再进入 depths 计算的 while changed 循环（不会无限循环）。

    本测试为正向断言：compile 抛 ValueError 即视为 spec 被满足（测试通过）。
    """
    cfg = _load_self_loop_config()
    with pytest.raises(ValueError, match=r"自环|loop|cycle"):
        Compiler.compile(cfg)


# ═══════════════════════════════════════════════════════════════
# 测试用例 — 孤点
# ═══════════════════════════════════════════════════════════════


def test_orphan_node_compile_no_crash():
    """孤点（无入边无出边）下 Compiler.compile 不抛异常。

    断言：
      - PoolEngine 装配不抛异常（含孤点节点）
      - CompiledSchedule 含孤点节点
    """
    cfg = _load_orphan_node_config()
    schedule = Compiler.compile(cfg)
    assert isinstance(schedule, CompiledSchedule)
    assert "orphan_pool" in schedule.nodes


def test_orphan_node_ttl_registered():
    """孤点（无入边）的 TTL spec 注册到 node_ttl_spec。

    Compiler.compile 中 targeted_nodes = {ec.tid for ec in edge_ctx.values()}，
    孤点不在 targeted_nodes 中，因此其 TTL spec 被注册到 node_ttl_spec
    （替代旧 engine.py apply_ttl 全扫循环）。

    断言：
      - orphan_pool 在 node_ttl_spec 中
      - TTL spec.bdel == 1
      - TTL spec.check_type == "interval"
      - TTL spec.ttl_sec > 0
    """
    cfg = _load_orphan_node_config()
    schedule = Compiler.compile(cfg)
    assert "orphan_pool" in schedule.node_ttl_spec, (
        "孤点（无入边）应在 node_ttl_spec 中注册 TTL spec"
    )
    ttl = schedule.node_ttl_spec["orphan_pool"]
    assert ttl.bdel == 1
    assert ttl.check_type == "interval"
    assert ttl.ttl_sec > 0, f"ttl_sec 应 > 0，实际 {ttl.ttl_sec}"


# ═══════════════════════════════════════════════════════════════
# 测试用例 — fire_due 不引发无限循环
# ═══════════════════════════════════════════════════════════════


def test_fire_due_no_infinite_loop_with_interval():
    """fire_due 对 interval > 0 的定时器不引发无限循环。

    EventDriver.fire_due(now) 弹出堆顶 fire_time <= now 的 spec，
    执行 action 后立即注册下次：next_time = fire_time + interval。
    当 interval > 0 时，next_time > fire_time <= now，但 next_time 可能 <= now
    （若 fire_time 远小于 now）。然而 next_time = fire_time + interval，
    而 fire_time 是被弹出的原始触发时间，每轮 next = fire_time + interval
    线性增长，最终会超过 now，循环终止。

    本测试验证：
      - 单次 fire_due 调用不会无限循环
      - action 只执行一次（next_time > now，不重复弹出）
      - heap 中重新注册了下次触发
    """
    driver = EventDriver(state=None, bus=None)
    call_count = 0

    def action(params: Any, fire_time=None) -> None:
        nonlocal call_count
        call_count += 1

    interval = 60.0
    spec = TimedEventSpec(
        action=action,
        params={"eid": "test_edge"},
        interval=interval,
    )
    driver.add_spec(spec, first_fire_time=_TS)

    # fire_due at _TS（恰好等于 fire_time）
    driver.fire_due(_TS)

    # action 只执行一次（next_time = _TS + 60 > _TS = now，不重复弹出）
    assert call_count == 1, (
        f"fire_due 应只触发一次 action，实际 {call_count} 次"
    )
    # heap 中重新注册了下次触发
    assert len(driver._heap) == 1, (
        f"heap 应有 1 条下次触发 spec，实际 {len(driver._heap)} 条"
    )
    next_fire_time = driver._heap[0][0]
    assert next_fire_time == _TS + interval, (
        f"next_fire_time 应为 {_TS + interval}，实际 {next_fire_time}"
    )


def test_fire_due_no_infinite_loop_with_catch_up():
    """fire_due 对积压定时器（fire_time << now）不引发无限循环。

    当 heap 中积压多个到时事件（fire_time << now）时，fire_due 逐个弹出执行。
    每个弹出后注册下次（next = fire_time + interval），若 next 仍 <= now
    则继续弹出。但 next 线性增长，最终超过 now，循环终止。

    本测试验证：
      - 初始注册 1 个定时器（fire_time = _TS）
      - fire_due(_TS + 300) 推进 300 秒
      - action 执行次数 = 6（_TS, _TS+60, _TS+120, _TS+180, _TS+240, _TS+300）
      - 不引发无限循环
    """
    driver = EventDriver(state=None, bus=None)
    call_count = 0

    def action(params: Any, fire_time=None) -> None:
        nonlocal call_count
        call_count += 1

    interval = 60.0
    spec = TimedEventSpec(
        action=action,
        params={"eid": "catch_up_edge"},
        interval=interval,
    )
    driver.add_spec(spec, first_fire_time=_TS)

    # 推进 300 秒（5 个 interval + 初始触发 = 6 次）
    now = _TS + 300
    driver.fire_due(now)

    # 应执行 6 次（fire_time = _TS, _TS+60, _TS+120, _TS+180, _TS+240, _TS+300）
    # _TS+300 <= now(=_TS+300) → 第 6 次触发
    # next = _TS+360 > now → 不再触发
    assert call_count == 6, (
        f"fire_due 应触发 6 次（5 个 interval + 初始），实际 {call_count} 次"
    )
    # heap 中下次触发时间 = _TS + 360
    assert len(driver._heap) == 1
    next_fire_time = driver._heap[0][0]
    assert next_fire_time == _TS + 360, (
        f"next_fire_time 应为 {_TS + 360}，实际 {next_fire_time}"
    )
