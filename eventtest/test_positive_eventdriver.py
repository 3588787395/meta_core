"""正测试：EventDriver 单 heapq 优先队列中断驱动（G1）

验证：
- 内部仅维护 1 个 heapq（``_heap``），无 ``_specs`` 列表
- ``fire_due(now)`` 按最近到期时间弹出堆顶，发布事件后立即 ``heappush`` 下次
  （next = fire_time + interval，非 now + interval）
- TTL spec 一次性触发不注册下次
- 不存在 ``at_fn``/``fire_ttl_due``/``is_edge_due``/``TtlTracker``/
  ``_make_edge_at_fn``/``_make_ttl_interval_at_fn``/``_make_ttl_endtime_at_fn`` 残留

基于 spec.md "EventDriver 单 heapq 优先队列中断驱动（G1）" Scenario。
复用 ``core/`` 现有 ``EventDriver`` / ``TimedEventSpec``，禁止兼容已删除旧接口。
"""
from __future__ import annotations

import inspect

from core.event_bus import EdgeFired, EventBus, TTLDue
from core.execution_module import EventDriver, TimedEventSpec

# 边触发间隔（秒）—— spec 约束 tick/edge 间隔为秒级
_EDGE_INTERVAL = 10.0
# 首次触发时间（任意确定值，与虚拟时钟解耦的行为测试）
_FIRE_TIME = 100.0


# ────────────────────────────────────────────────────────────────
# 结构：单一 heapq，无 _specs 列表
# ────────────────────────────────────────────────────────────────


def test_event_driver_has_single_heap(pool_engine):
    """EventDriver 内部仅维护 1 个 heapq（``_heap``），无 ``_specs`` 列表存储。"""
    engine = pool_engine()
    driver = engine._components["event_driver"]
    # _heap 存在且为 list（heapq 基于列表）
    assert hasattr(driver, "_heap")
    assert isinstance(driver._heap, list)
    # 不存在 _specs 列表型定时器存储（旧线性扫描接口已删除）
    assert not hasattr(driver, "_specs")


# ────────────────────────────────────────────────────────────────
# 行为：fire_due 弹堆顶 → 发布 EdgeFired → 立即 heappush 下次
# ────────────────────────────────────────────────────────────────


def test_edge_fire_due_pops_and_pushes_next(pool_engine, event_collector):
    """fire_due 弹出堆顶发布 EdgeFired，立即 heappush 下次（next = fire_time + interval）。"""
    engine = pool_engine()
    driver = engine._components["event_driver"]
    # 独立 EventBus 收集事件，避免与已装配订阅者（EdgeExecutor 等）交叉
    test_bus = EventBus()
    collector = event_collector(test_bus)

    # 清空 heap 隔离行为测试（engine.py rebuild_schedule 同样清空 _heap）
    driver._heap.clear()
    driver._seq = 0

    def action(params):
        test_bus.publish(EdgeFired(eid="test_edge", ts=_FIRE_TIME))

    spec = TimedEventSpec(action=action, params={}, interval=_EDGE_INTERVAL)
    driver.add_spec(spec, first_fire_time=_FIRE_TIME)
    # 注册后堆中有 1 个待触发项
    assert len(driver._heap) == 1

    # 推进到 fire_time，触发堆顶
    driver.fire_due(_FIRE_TIME)

    # 发布了 1 个 EdgeFired 事件
    assert collector.count_by_type().get("EdgeFired", 0) == 1
    # 立即 heappush 下次：堆长度不变
    assert len(driver._heap) == 1
    # next fire time = fire_time + interval（非 now + interval，保证固定间隔）
    assert driver._heap[0][0] == _FIRE_TIME + _EDGE_INTERVAL

    # 再次推进到 next fire time，验证循环触发
    collector.clear()
    driver.fire_due(_FIRE_TIME + _EDGE_INTERVAL)
    assert collector.count_by_type().get("EdgeFired", 0) == 1
    assert len(driver._heap) == 1
    assert driver._heap[0][0] == _FIRE_TIME + 2 * _EDGE_INTERVAL

    collector.disconnect()


# ────────────────────────────────────────────────────────────────
# 行为：TTL 一次性触发不注册下次
# ────────────────────────────────────────────────────────────────


def test_ttl_one_shot_does_not_reregister(pool_engine, event_collector, fz_stocks):
    """TTL spec（interval=None）一次性触发，发布 TTLDue 后不 heappush 下次。"""
    engine = pool_engine()
    driver = engine._components["event_driver"]
    test_bus = EventBus()
    collector = event_collector(test_bus)

    driver._heap.clear()
    driver._seq = 0

    # 从 fixture 动态取一只 fz 股票代码，避免硬编码
    code = fz_stocks(1)[0]

    def ttl_action(params):
        test_bus.publish(TTLDue(node_id="pool_C", code=code, ts=_FIRE_TIME))

    # interval=None 表示一次性触发，不注册下次
    spec = TimedEventSpec(action=ttl_action, params={}, interval=None)
    driver.add_spec(spec, first_fire_time=_FIRE_TIME)
    assert len(driver._heap) == 1

    driver.fire_due(_FIRE_TIME)

    # 发布了 1 个 TTLDue 事件
    assert collector.count_by_type().get("TTLDue", 0) == 1
    # 一次性触发不注册下次：堆长度减 1（变为 0）
    assert len(driver._heap) == 0

    # 再次推进时间，确认没有再次触发（堆为空，无事件产生）
    driver.fire_due(_FIRE_TIME + _EDGE_INTERVAL)
    assert collector.count_by_type().get("TTLDue", 0) == 1
    assert len(driver._heap) == 0

    collector.disconnect()


# ────────────────────────────────────────────────────────────────
# 源码层面：无禁止残留接口
# ────────────────────────────────────────────────────────────────


def test_no_forbidden_residuals_in_source():
    """源码层面检查 EventDriver 所在模块无禁止残留接口。

    以下旧接口应已彻底删除，源码中不得出现任何引用：
    ``at_fn`` / ``fire_ttl_due`` / ``is_edge_due`` / ``TtlTracker`` /
    ``_make_edge_at_fn`` / ``_make_ttl_interval_at_fn`` / ``_make_ttl_endtime_at_fn``。
    """
    src_path = inspect.getsourcefile(EventDriver)
    assert src_path is not None
    with open(src_path, encoding="utf-8") as f:
        src = f.read()
    forbidden = [
        "at_fn",
        "fire_ttl_due",
        "is_edge_due",
        "TtlTracker",
        "_make_edge_at_fn",
        "_make_ttl_interval_at_fn",
        "_make_ttl_endtime_at_fn",
    ]
    for name in forbidden:
        assert name not in src, f"禁止残留接口 {name!r} 出现在 {src_path}"
