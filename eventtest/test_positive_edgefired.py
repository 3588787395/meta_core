"""正测试：EdgeFired 不携带 changed_codes（G3）

验证：
- EdgeFired 事件只含 eid 和 ts
- EdgeExecutor 从 source_pool.get_dirty_codes() 取脏股票
- 不存在 event.changed_codes 字段引用
- 不存在 _make_edge_action 中 changed_codes 计算逻辑

基于 spec.md "EdgeFired 不携带 changed_codes（G3）" Scenario。
复用 ``core/`` 现有 ``EdgeFired`` / ``EdgeExecutor`` / ``_make_edge_action``，
禁止兼容已删除旧接口。
"""
from __future__ import annotations

import inspect
from dataclasses import asdict, fields

from core.event_bus import EdgeFired, EventBus
from core.execution_module import EdgeExecutor, _make_edge_action

# 测试用 EdgeFired 参数（eid 为通用测试标签，ts 与虚拟时钟起点一致）
_EDGE_EID = "test_edge"
_EDGE_TS = 34500.0


# ────────────────────────────────────────────────────────────────
# 结构：EdgeFired 字段集合 = {eid, ts}
# ────────────────────────────────────────────────────────────────


def test_edgefired_fields_only_eid_ts():
    """EdgeFired dataclass 字段集合 = ``{eid, ts}``，不含 ``changed_codes``。"""
    field_names = {f.name for f in fields(EdgeFired)}
    assert field_names == {"eid", "ts"}
    # 明确断言不含 changed_codes 字段
    assert "changed_codes" not in field_names


def test_edgefired_instance_no_changed_codes():
    """EdgeFired 实例无 ``changed_codes`` 属性，``asdict`` 只含 eid 和 ts。"""
    event = EdgeFired(eid=_EDGE_EID, ts=_EDGE_TS)
    # 实例字典只含 eid 和 ts
    assert set(event.__dict__.keys()) <= {"eid", "ts"}
    # 明确断言无 changed_codes 属性
    assert not hasattr(event, "changed_codes")
    # asdict 只含 eid 和 ts
    assert asdict(event) == {"eid": _EDGE_EID, "ts": _EDGE_TS}


# ────────────────────────────────────────────────────────────────
# 行为：发布 EdgeFired 事件 payload 不含 changed_codes
# ────────────────────────────────────────────────────────────────


def test_edgefired_publish_payload_no_changed_codes(event_collector):
    """EventBus 发布 EdgeFired 后，收集器收到的事件 payload 不含 ``changed_codes``。"""
    bus = EventBus()
    collector = event_collector(bus)

    bus.publish(EdgeFired(eid=_EDGE_EID, ts=_EDGE_TS))

    edge_events = collector.filter(type="EdgeFired")
    assert len(edge_events) == 1
    ev = edge_events[0]
    # 事件字段只有 eid 和 ts
    assert set(ev.__dict__.keys()) <= {"eid", "ts"}
    assert not hasattr(ev, "changed_codes")
    assert ev.eid == _EDGE_EID
    assert ev.ts == _EDGE_TS

    collector.disconnect()


# ────────────────────────────────────────────────────────────────
# 行为：EdgeExecutor._on_edge_fired 从 source_pool.get_dirty_codes() 取脏股票
# ────────────────────────────────────────────────────────────────


def test_edge_executor_uses_get_dirty_codes():
    """``EdgeExecutor._on_edge_fired`` 源码使用 ``source_pool.get_dirty_codes()``
    取脏股票（G3），不引用 ``event.changed_codes``。"""
    src = inspect.getsource(EdgeExecutor._on_edge_fired)
    # 断言源码中调用了 get_dirty_codes()
    assert "get_dirty_codes()" in src
    # 断言源码中不引用 event.changed_codes（G3 已移除）
    assert "event.changed_codes" not in src
    # 断言源码中不存在 .changed_codes 字段访问
    assert ".changed_codes" not in src


# ────────────────────────────────────────────────────────────────
# 源码层面：EdgeExecutor 类无 event.changed_codes / .changed_codes 引用
# ────────────────────────────────────────────────────────────────


def test_no_changed_codes_field_access_in_edge_executor():
    """EdgeExecutor 类源码中不存在 ``event.changed_codes`` 或 ``.changed_codes``
    字段引用（除注释外）。

    ``changed_codes`` 作为方法参数名（如 ``run(changed_codes=...)``）是合法的，
    但 ``.changed_codes``（字段访问，如 ``event.changed_codes`` /
    ``state.dirty.changed_codes``）应不存在——脏股票通过
    ``source_pool.get_dirty_codes()`` 获取。
    """
    src = inspect.getsource(EdgeExecutor)
    # event.changed_codes 是 G3 明确禁止的字段引用
    assert "event.changed_codes" not in src
    # 逐行检查 .changed_codes 字段访问（排除注释行）
    for line in src.split("\n"):
        stripped = line.strip()
        # 跳过纯注释行
        if stripped.startswith("#"):
            continue
        # .changed_codes 字段访问（含 event.changed_codes / self.changed_codes 等）
        # 不含 changed_codes= 参数赋值（无前导点）
        assert ".changed_codes" not in line, (
            f"EdgeExecutor 源码中存在 .changed_codes 字段访问: {line!r}"
        )


# ────────────────────────────────────────────────────────────────
# 源码层面：_make_edge_action 中无 changed_codes 计算逻辑
# ────────────────────────────────────────────────────────────────


def test_no_changed_codes_computation_in_make_edge_action():
    """``_make_edge_action`` 函数体中不存在 ``changed_codes`` 计算逻辑。

    G3：action 只发布 ``EdgeFired(eid, ts)``，不计算/携带 ``changed_codes``。
    脏股票由 ``EdgeExecutor._on_edge_fired`` 从源池取。
    检查时排除 docstring 与注释行。
    """
    src = inspect.getsource(_make_edge_action)
    lines = src.split("\n")
    in_docstring = False
    docstring_seen = False
    for line in lines:
        stripped = line.strip()
        # docstring 检测（首次 """ 开始，再次 """ 结束）
        if not docstring_seen and '"""' in stripped:
            if stripped.count('"""') >= 2:
                # 单行 docstring
                docstring_seen = True
                continue
            in_docstring = True
            docstring_seen = True
            continue
        if in_docstring:
            if '"""' in stripped:
                in_docstring = False
            continue
        # 跳过纯注释行
        if stripped.startswith("#"):
            continue
        # 非注释非 docstring 行中不应出现 changed_codes 计算逻辑
        assert "changed_codes" not in line, (
            f"_make_edge_action 函数体中存在 changed_codes 计算逻辑: {line!r}"
        )
