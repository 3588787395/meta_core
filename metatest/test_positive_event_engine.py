# -*- coding: utf-8 -*-
"""正向测试：事件引擎（EventBus / EventDriver / 事件适配器）。

覆盖：
- EventBus publish / subscribe / subscribe_any / get_events / clear
- total_published 绝对计数器与 dropped_count 上限保护
- get_events_since 偏移量查询
- EventDriver heapq 优先队列（add_spec / fire_due）
- TimedEventSpec 周期触发与一次性触发
- tick 优先于 edge/ttl 的 seq 排序
- EVENT_RECORD_ADAPTERS 表驱动事件适配
- classify_event_type 分类映射
- normalize_display_ms 时间戳归一化
- 运行时事件无序（G6：定时器到时即触发，引擎不编排执行顺序）
"""
from __future__ import annotations

import heapq
import inspect
from typing import Any, Dict, List

import pytest

# 覆盖 core.engine 模块（PoolEngine 主引擎）
try:
    from core.engine import PoolEngine  # noqa: F401
except ImportError:
    pass

# 覆盖 core.schemas 模块（数据 schema 定义）
try:
    from core.schemas import TableSchema  # noqa: F401
except ImportError:
    try:
        import core.schemas  # noqa: F401
    except ImportError:
        pass


# ---------------------------------------------------------------------------
# 可选依赖：导入失败时 skip 整个模块
# ---------------------------------------------------------------------------
try:
    from core.event_bus import (
        EventBus,
        DataChanged,
        TickReceived,
        BarComposed,
        EdgeFired,
        FormulaEvaluated,
        StockFiltered,
        TransferExecuted,
        Signal,
        TTLDue,
        TickDue,
        ModeChanged,
        is_event_bus,
    )
except ImportError as exc:
    pytest.skip(f"无法导入 core.event_bus: {exc}", allow_module_level=True)

try:
    from core.domain import TimedEventSpec
except ImportError as exc:
    pytest.skip(f"无法导入 core.domain.TimedEventSpec: {exc}", allow_module_level=True)

try:
    from core.execution_module import EventDriver
except ImportError as exc:
    pytest.skip(f"无法导入 core.execution_module.EventDriver: {exc}", allow_module_level=True)

try:
    from core.monitoring_module import EVENT_RECORD_ADAPTERS, event_to_record
except ImportError as exc:
    pytest.skip(f"无法导入 core.monitoring_module: {exc}", allow_module_level=True)

try:
    from core.web_state import classify_event_type, normalize_display_ms, get_timer_trigger_type
except ImportError as exc:
    pytest.skip(f"无法导入 core.web_state: {exc}", allow_module_level=True)


# ---------------------------------------------------------------------------
# Test 1: EventBus 基础发布/订阅
# ---------------------------------------------------------------------------
class TestEventBusPublishSubscribe:
    """EventBus 同步发布与按类型订阅。"""

    def test_publish_and_subscribe_single_type(self):
        bus = EventBus()
        received: List[Any] = []
        bus.subscribe(TickReceived, lambda ev: received.append(ev))

        ev = TickReceived(tick_data={"code": "fz000001"}, code="fz000001", ts=34500.0)
        bus.publish(ev)

        assert len(received) == 1
        assert received[0] is ev
        assert received[0].code == "fz000001"
        assert received[0].ts == 34500.0

    def test_subscribe_by_type_name_string(self):
        """subscribe 支持事件类型名字符串。"""
        bus = EventBus()
        received: List[Any] = []
        bus.subscribe("TickReceived", lambda ev: received.append(ev))

        bus.publish(TickReceived(tick_data={}, code="fz000002", ts=34600.0))
        assert len(received) == 1
        assert received[0].code == "fz000002"

    def test_get_events_returns_all_when_no_filter(self):
        bus = EventBus()
        bus.publish(TickReceived(tick_data={}, code="c1", ts=1.0))
        bus.publish(BarComposed(bar={}, period="1m", code="c1", ts=2.0))

        all_events = bus.get_events()
        assert len(all_events) == 2
        assert isinstance(all_events[0], TickReceived)
        assert isinstance(all_events[1], BarComposed)

    def test_get_events_filtered_by_type(self):
        bus = EventBus()
        bus.publish(TickReceived(tick_data={}, code="c1", ts=1.0))
        bus.publish(BarComposed(bar={}, period="1m", code="c1", ts=2.0))
        bus.publish(TickReceived(tick_data={}, code="c2", ts=3.0))

        tick_events = bus.get_events(TickReceived)
        assert len(tick_events) == 2
        assert all(isinstance(ev, TickReceived) for ev in tick_events)

    def test_clear_empties_event_log(self):
        bus = EventBus()
        bus.publish(TickReceived(tick_data={}, code="c1", ts=1.0))
        assert len(bus.get_events()) == 1
        bus.clear()
        assert len(bus.get_events()) == 0

    def test_is_event_bus_helper(self):
        bus = EventBus()
        assert is_event_bus(bus) is True
        assert is_event_bus("not a bus") is False


# ---------------------------------------------------------------------------
# Test 2: subscribe_any 通配订阅
# ---------------------------------------------------------------------------
class TestEventBusSubscribeAny:
    """subscribe_any 订阅所有事件类型，返回取消订阅函数。"""

    def test_subscribe_any_receives_all_event_types(self):
        bus = EventBus()
        received: List[Any] = []
        unsub = bus.subscribe_any(lambda ev: received.append(ev))

        bus.publish(TickReceived(tick_data={}, code="c1", ts=1.0))
        bus.publish(BarComposed(bar={}, period="1m", code="c1", ts=2.0))
        bus.publish(EdgeFired(eid="e1", ts=3.0))

        assert len(received) == 3
        assert isinstance(received[0], TickReceived)
        assert isinstance(received[1], BarComposed)
        assert isinstance(received[2], EdgeFired)

    def test_unsubscribe_stops_receiving(self):
        bus = EventBus()
        received: List[Any] = []
        unsub = bus.subscribe_any(lambda ev: received.append(ev))

        bus.publish(TickReceived(tick_data={}, code="c1", ts=1.0))
        assert len(received) == 1

        unsub()
        bus.publish(TickReceived(tick_data={}, code="c2", ts=2.0))
        assert len(received) == 1  # 取消后不再收到

    def test_subscribe_any_and_specific_both_fire(self):
        """同一事件同时被 specific 和 any 订阅者接收。"""
        bus = EventBus()
        specific_received: List[Any] = []
        any_received: List[Any] = []
        bus.subscribe(TickReceived, lambda ev: specific_received.append(ev))
        bus.subscribe_any(lambda ev: any_received.append(ev))

        bus.publish(TickReceived(tick_data={}, code="c1", ts=1.0))

        assert len(specific_received) == 1
        assert len(any_received) == 1


# ---------------------------------------------------------------------------
# Test 3: total_published / dropped_count / get_events_since
# ---------------------------------------------------------------------------
class TestEventBusCounters:
    """EventBus 绝对计数器与上限保护。"""

    def test_total_published_increments(self):
        bus = EventBus()
        assert bus.total_published == 0
        bus.publish(TickReceived(tick_data={}, code="c1", ts=1.0))
        bus.publish(BarComposed(bar={}, period="1m", code="c1", ts=2.0))
        assert bus.total_published == 2

    def test_dropped_count_when_exceeding_max_events(self):
        """max_events 上限保护：超出时删除最旧事件并累加 dropped_count。"""
        bus = EventBus(max_events=5)
        for i in range(10):
            bus.publish(TickReceived(tick_data={}, code=f"c{i}", ts=float(i)))

        assert bus.total_published == 10
        assert bus.dropped_count == 5
        assert len(bus.get_events()) == 5  # 仅保留最新 5 条

    def test_get_events_since_returns_new_events(self):
        """get_events_since 按绝对偏移返回新增事件。"""
        bus = EventBus()
        for i in range(5):
            bus.publish(TickReceived(tick_data={}, code=f"c{i}", ts=float(i)))

        offset = bus.total_published
        new_events = bus.get_events_since(offset)
        assert len(new_events) == 0

        bus.publish(TickReceived(tick_data={}, code="c5", ts=5.0))
        new_events = bus.get_events_since(offset)
        assert len(new_events) == 1
        assert new_events[0].code == "c5"

    def test_get_events_since_with_dropped_events(self):
        """offset 在已删除范围内时返回当前所有事件。"""
        bus = EventBus(max_events=3)
        for i in range(10):
            bus.publish(TickReceived(tick_data={}, code=f"c{i}", ts=float(i)))

        # offset=0 在已删除范围内（dropped_count=7）
        events = bus.get_events_since(0)
        assert len(events) == 3  # 返回当前所有事件


# ---------------------------------------------------------------------------
# Test 4: EventDriver heapq 优先队列
# ---------------------------------------------------------------------------
class TestEventDriverHeapQueue:
    """EventDriver 使用 heapq 优先队列，按 fire_time 排序触发。"""

    def test_event_driver_uses_heapq(self):
        """EventDriver._heap 应为 list 且使用 heapq 操作。"""
        src = inspect.getsource(EventDriver)
        assert "heapq.heappush" in src
        assert "heapq.heappop" in src
        assert "_heap" in src

    def test_add_spec_pushes_to_heap(self):
        driver = EventDriver()
        assert len(driver._heap) == 0

        spec = TimedEventSpec(action=lambda params, fire_time=None: None, params={})
        driver.add_spec(spec, first_fire_time=100.0)

        assert len(driver._heap) == 1
        assert driver._heap[0][0] == 100.0  # fire_time

    def test_fire_due_triggers_action(self):
        """fire_due 弹出到期事件并调用 action。"""
        driver = EventDriver()
        fired_times: List[float] = []

        def action(params, fire_time=None):
            fired_times.append(fire_time)

        spec = TimedEventSpec(action=action, params={})
        driver.add_spec(spec, first_fire_time=100.0)

        driver.fire_due(100.0)
        assert len(fired_times) == 1
        assert fired_times[0] == 100.0

    def test_fire_due_skips_not_yet_due(self):
        """未到期的事件不被触发。"""
        driver = EventDriver()
        fired: List[Any] = []

        spec = TimedEventSpec(action=lambda p, ft=None: fired.append(ft), params={})
        driver.add_spec(spec, first_fire_time=200.0)

        driver.fire_due(100.0)
        assert len(fired) == 0

    def test_periodic_spec_registers_next(self):
        """周期性 spec（interval>0）执行后注册下次触发。"""
        driver = EventDriver()
        fire_count = [0]

        def action(params, fire_time=None):
            fire_count[0] += 1

        spec = TimedEventSpec(action=action, params={}, interval=50.0)
        driver.add_spec(spec, first_fire_time=100.0)

        driver.fire_due(100.0)
        assert fire_count[0] == 1
        assert len(driver._heap) == 1  # 注册了下次
        assert driver._heap[0][0] == 150.0  # 100 + 50

    def test_one_shot_spec_no_reregister(self):
        """一次性 spec（interval=None）执行后不注册下次。"""
        driver = EventDriver()
        fire_count = [0]

        def action(params, fire_time=None):
            fire_count[0] += 1

        spec = TimedEventSpec(action=action, params={}, interval=None)
        driver.add_spec(spec, first_fire_time=100.0)

        driver.fire_due(100.0)
        assert fire_count[0] == 1
        assert len(driver._heap) == 0  # 不注册下次


# ---------------------------------------------------------------------------
# Test 5: tick 优先于 edge/ttl 的 seq 排序
# ---------------------------------------------------------------------------
class TestEventDriverTickPriority:
    """同 fire_time 时 kind="tick" 的 spec 使用更小 seq，优先执行。"""

    def test_tick_seq_smaller_than_edge_seq(self):
        """tick 规格的 seq 应小于 edge 规格的 seq。"""
        driver = EventDriver()

        tick_spec = TimedEventSpec(action=lambda p, ft=None: None, params={"kind": "tick"})
        edge_spec = TimedEventSpec(action=lambda p, ft=None: None, params={"kind": "edge"})

        tick_seq = driver._next_seq(tick_spec)
        edge_seq = driver._next_seq(edge_spec)

        assert tick_seq < edge_seq

    def test_tick_fires_before_edge_at_same_time(self):
        """同 fire_time 时 tick 先于 edge 执行。"""
        driver = EventDriver()
        order: List[str] = []

        def tick_action(params, fire_time=None):
            order.append("tick")

        def edge_action(params, fire_time=None):
            order.append("edge")

        tick_spec = TimedEventSpec(action=tick_action, params={"kind": "tick"})
        edge_spec = TimedEventSpec(action=edge_action, params={"kind": "edge"})

        driver.add_spec(edge_spec, first_fire_time=100.0)
        driver.add_spec(tick_spec, first_fire_time=100.0)

        driver.fire_due(100.0)
        assert order == ["tick", "edge"]


# ---------------------------------------------------------------------------
# Test 6: EVENT_RECORD_ADAPTERS 表驱动事件适配
# ---------------------------------------------------------------------------
class TestEventRecordAdapters:
    """EVENT_RECORD_ADAPTERS 表驱动：每个事件类型有对应 adapter。"""

    def test_adapters_cover_key_event_types(self):
        """关键事件类型必须在 EVENT_RECORD_ADAPTERS 中注册。"""
        required = {
            "TickReceived", "DataChanged", "BarComposed",
            "EdgeFired", "FormulaEvaluated", "StockFiltered",
            "TransferExecuted", "Signal", "Executed", "DomainEvent",
            "ModeChanged",
        }
        registered = set(EVENT_RECORD_ADAPTERS.keys())
        missing = required - registered
        assert not missing, f"缺少 adapter: {missing}"

    def test_event_to_record_tick_received(self):
        """event_to_record 将 TickReceived 转为 dict 记录。"""
        ev = TickReceived(tick_data={"close": 10.0}, code="fz000001", ts=34500.0)
        record = event_to_record(ev)
        assert isinstance(record, dict)
        assert record.get("event_type") == "TickReceived"
        assert record.get("code") == "fz000001"

    def test_event_to_record_edge_fired(self):
        ev = EdgeFired(eid="e1", ts=34600.0)
        record = event_to_record(ev)
        assert isinstance(record, dict)
        assert record.get("event_type") == "EdgeFired"

    def test_event_to_record_falls_back_to_default(self):
        """未注册的事件类型走 _default_adapter。"""
        ev = TTLDue(node_id="n1", code="c1", ts=1.0)
        record = event_to_record(ev)
        assert isinstance(record, dict)
        # TTLDue 未在 EVENT_RECORD_ADAPTERS 中，走默认 adapter
        assert "event_type" in record or "ts" in record


# ---------------------------------------------------------------------------
# Test 7: classify_event_type 分类映射
# ---------------------------------------------------------------------------
class TestClassifyEventType:
    """classify_event_type 按事件类型名返回分类 key。"""

    def test_tick_category(self):
        assert classify_event_type("TickReceived") == "tick"
        assert classify_event_type("DataChanged") == "tick"

    def test_bar_category(self):
        assert classify_event_type("BarComposed") == "bar"

    def test_formula_category(self):
        assert classify_event_type("FormulaEvaluated") == "formula"
        assert classify_event_type("StockFiltered") == "formula"

    def test_edge_category(self):
        assert classify_event_type("EdgeFired") == "edge"
        assert classify_event_type("CrossOverDetected") == "edge"

    def test_signal_category(self):
        assert classify_event_type("Signal") == "signal"

    def test_system_category(self):
        assert classify_event_type("ModeChanged") == "system"
        assert classify_event_type("ConfigLoaded") == "system"

    def test_unknown_falls_to_system(self):
        assert classify_event_type("SomeUnknownEvent") == "system"


# ---------------------------------------------------------------------------
# Test 8: normalize_display_ms 时间戳归一化
# ---------------------------------------------------------------------------
class TestNormalizeDisplayMs:
    """normalize_display_ms 将任意时间戳归一化为展示毫秒。"""

    def test_relative_seconds_multiplied_by_1000(self):
        """相对秒（< 1e9，仿真 clock）→ *1000。"""
        assert normalize_display_ms(34500.0) == 34500000.0
        assert normalize_display_ms(0.0) == 0.0

    def test_unix_seconds_multiplied_by_1000(self):
        """Unix 秒（>=1e9 且 <1e12）→ *1000。"""
        result = normalize_display_ms(1700000000)
        assert result == 1700000000000.0

    def test_unix_milliseconds_kept(self):
        """Unix 毫秒（>=1e12）→ 保持。"""
        result = normalize_display_ms(1700000000000)
        assert result == 1700000000000.0

    def test_none_returns_none(self):
        assert normalize_display_ms(None) is None

    def test_invalid_returns_none(self):
        assert normalize_display_ms("not a number") is None


# ---------------------------------------------------------------------------
# Test 9: get_timer_trigger_type 定时器触发类型识别
# ---------------------------------------------------------------------------
class TestGetTimerTriggerType:
    """get_timer_trigger_type 按规格识别触发类型标签。"""

    def test_edge_timer_label(self):
        spec = {"event_type": "EdgeFired", "edge_id": "e1"}
        label = get_timer_trigger_type(spec)
        assert label == "边定时器"

    def test_ttl_label(self):
        spec = {"event_type": "TTLDue", "details": {"ttl": 300}}
        label = get_timer_trigger_type(spec)
        assert label == "TTL超时"

    def test_tick_timer_label(self):
        spec = {"event_type": "TickDue", "kind": "tick"}
        label = get_timer_trigger_type(spec)
        assert label == "Tick定时器"


# ---------------------------------------------------------------------------
# Test 10: 运行时事件无序（G6 约束验证）
# ---------------------------------------------------------------------------
class TestRuntimeEventUnordered:
    """G6：运行时事件无序——定时器到时即触发，引擎不编排执行顺序。

    验证 EventDriver.fire_due 按 fire_time 触发，无运行时拓扑排序。
    """

    def test_fire_due_processes_by_fire_time_not_insertion_order(self):
        """fire_due 按 fire_time 排序触发，而非插入顺序。"""
        driver = EventDriver()
        order: List[str] = []

        def make_action(name):
            def action(params, fire_time=None):
                order.append(name)
            return action

        # 先插入 fire_time=200，再插入 fire_time=100
        late_spec = TimedEventSpec(action=make_action("late"), params={"kind": "edge"})
        early_spec = TimedEventSpec(action=make_action("early"), params={"kind": "edge"})

        driver.add_spec(late_spec, first_fire_time=200.0)
        driver.add_spec(early_spec, first_fire_time=100.0)

        driver.fire_due(200.0)
        # early(100) 先触发，late(200) 后触发，按 fire_time 排序
        assert order == ["early", "late"]

    def test_no_execution_order_field_in_event_driver(self):
        """EventDriver 不应包含运行时拓扑排序字段 execution_order。"""
        src = inspect.getsource(EventDriver)
        assert "execution_order" not in src, "EventDriver 不应包含 execution_order（G6 运行时无序）"


# ---------------------------------------------------------------------------
# 变更 N：@_event_handler 装饰器表驱动事件注册回归断言
# ---------------------------------------------------------------------------


class TestChangeNEventHandlerDecorator:
    """变更 N：@_event_handler 装饰器在 event_bus.py 定义，5 模块共 ≥28 次应用。"""

    def test_event_handler_decorator_defined_in_event_bus(self):
        """_event_handler 装饰器函数在 core/event_bus.py 定义。"""
        import re
        from pathlib import Path
        eb_path = Path(__file__).resolve().parent.parent / "core" / "event_bus.py"
        src = eb_path.read_text(encoding="utf-8")
        assert re.search(r"^def _event_handler\b", src, re.MULTILINE), \
            "event_bus.py 应定义模块级 _event_handler 装饰器（变更 N 表驱动注册）"

    def test_event_handler_importable_and_callable(self):
        """_event_handler 可从 core.event_bus 导入且可调用。"""
        from core.event_bus import _event_handler
        assert callable(_event_handler), \
            "_event_handler 应为可调用装饰器工厂"

    def test_event_handler_decorator_applied_at_least_28_times(self):
        """Grep 验证：5 模块共 ≥28 次 @_event_handler 装饰器应用。"""
        import re
        from pathlib import Path
        core_dir = Path(__file__).resolve().parent.parent / "core"
        # 采样 5 模块（与 runner.py RULE 99 一致）
        handler_modules = [
            "trade_module.py",
            "execution_module.py",
            "monitoring_module.py",
            "tick_bar_module.py",
            "screening_module.py",
        ]
        total = 0
        per_module = {}
        for mod_name in handler_modules:
            mod_path = core_dir / mod_name
            try:
                src = mod_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            count = len(re.findall(r"@_event_handler\b", src))
            per_module[mod_name] = count
            total += count
        assert total >= 28, (
            f"5 模块 @_event_handler 应用总数应 ≥ 28（变更 N），"
            f"实际 {total}（{per_module}）"
        )

    def test_each_sampled_module_uses_event_handler(self):
        """5 模块每个都至少应用 1 次 @_event_handler。"""
        import re
        from pathlib import Path
        core_dir = Path(__file__).resolve().parent.parent / "core"
        handler_modules = [
            "trade_module.py",
            "execution_module.py",
            "monitoring_module.py",
            "tick_bar_module.py",
            "screening_module.py",
        ]
        for mod_name in handler_modules:
            mod_path = core_dir / mod_name
            src = mod_path.read_text(encoding="utf-8")
            count = len(re.findall(r"@_event_handler\b", src))
            assert count >= 1, \
                f"{mod_name} 应至少应用 1 次 @_event_handler（变更 N），实际 {count}"

    def test_event_handler_returns_callable(self):
        """_event_handler(name) 返回可调用装饰器。"""
        from core.event_bus import _event_handler
        decorator = _event_handler("TestEvent")
        assert callable(decorator), \
            "_event_handler(name) 应返回可调用装饰器"


# === Task 28.6 回归断言：converge-meta-essence-v4 阶段 2 E1/E2 + 阶段 3 C11 收敛状态 ===


class TestConvergenceRegressionV4:
    """SubTask 28.6：converge-meta-essence-v4 E1/E2 EventDriver heapq + C11 _BaseModule 收敛回归。"""

    def test_event_driver_heapq_scheduling(self):
        """execution_module EventDriver 使用 heapq 调度（E1/E2 时间原语）。"""
        from pathlib import Path
        src = (Path(__file__).resolve().parent.parent / "core" / "execution_module.py").read_text(encoding="utf-8")
        assert "class EventDriver" in src, "execution_module 应含 EventDriver 类（时间原语）"
        assert "import heapq" in src, "execution_module 应 import heapq（EventDriver 优先队列）"
        assert "self._heap" in src, "EventDriver 应维护 self._heap 优先队列属性"

    def test_no_sync_play_sim_loop_residue(self):
        """runtime_mode_module 不含 _sync_play_loop / _sync_sim_loop / auto_step_loop（E1/E2 已改 heapq）。"""
        import re
        from pathlib import Path
        src = (Path(__file__).resolve().parent.parent / "core" / "runtime_mode_module.py").read_text(encoding="utf-8")
        count = len(re.findall(r"def _sync_play_loop\b|def _sync_sim_loop\b|async def auto_step_loop\b", src))
        assert count == 0, \
            f"runtime_mode_module 不应含 _sync_play_loop/_sync_sim_loop/auto_step_loop（E1/E2 heapq），实际 {count} 处"

    def test_base_module_subscriptions_table(self):
        """event_bus.py 含 _BaseModule + _SUBSCRIPTIONS 表（C11 表驱动订阅）。"""
        import ast
        from pathlib import Path
        tree = ast.parse((Path(__file__).resolve().parent.parent / "core" / "event_bus.py").read_text(encoding="utf-8"))
        classes = {n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
        assert "_BaseModule" in classes, \
            "event_bus.py 应含 _BaseModule 基类（C11 表驱动订阅）"
        src = (Path(__file__).resolve().parent.parent / "core" / "event_bus.py").read_text(encoding="utf-8")
        assert "_SUBSCRIPTIONS" in src, \
            "event_bus.py 应含 _SUBSCRIPTIONS 类属性表（C11 表驱动）"
