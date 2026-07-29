# -*- coding: utf-8 -*-
"""Task 20.5: 底层逻辑反测试（新类别）。

验证底层逻辑在异常输入下的正确行为：
  - 水位线 hash 无碰撞（sha256 不同数据不同 hash）
  - compile(pool_config) 对无效配置应抛出明确异常
  - 调用深度不超过 3 层（ast 检查 trigger_check/filter_eval/propagate_apply）
  - 未注册的角色应被优雅处理（_ROLE_ACTIONS.get 返回默认动作）
  - 事件-信号-动作解耦失败时（SignalDeriver 异常）不应影响 ActionDispatcher
  - 未知 propagate mode 应回退到默认 copy 行为
  - 畸形 filter_spec 应被拒绝或默认全部通过

硬约束：
  - 仿真模式下股票代码使用 ``fz`` 前缀
  - 复用 conftest.py 中的 fixture（tick_table / compiled_pool / signal_collector）
  - 使用 ``from core.xxx import yyy`` 直接导入
"""
from __future__ import annotations

import ast
import inspect
from typing import Any, Dict, List

import pytest


# ---------------------------------------------------------------------------
# 1. test_waterline_hash_no_collision —— 不同数据产生不同 hash
# ---------------------------------------------------------------------------


def test_waterline_hash_no_collision(tick_table):
    """不同数据产生不同 hash（验证 sha256 不会碰撞）。

    TickTable._compute_hash 使用 sha256 + sort_keys 序列化，
    对不同数据应产生不同 hash；相同数据应产生相同 hash（确定性）。
    """
    data_a = {"fz000001": {"close": 10.0}}
    data_b = {"fz000001": {"close": 11.0}}
    data_c = {"fz000002": {"close": 10.0}}
    h_a = tick_table._compute_hash(data_a)
    h_b = tick_table._compute_hash(data_b)
    h_c = tick_table._compute_hash(data_c)
    # 不同数据应产生不同 hash
    assert h_a != h_b, "不同 close 值产生相同 hash（hash 碰撞）"
    assert h_a != h_c, "不同 code 产生相同 hash（hash 碰撞）"
    assert h_b != h_c, "完全不同数据产生相同 hash（hash 碰撞）"
    # 相同数据应产生相同 hash（确定性）
    assert h_a == tick_table._compute_hash(data_a), "相同数据产生不同 hash（非确定性）"
    # hash 应为大整数
    assert isinstance(h_a, int) and h_a > 0


# ---------------------------------------------------------------------------
# 2. test_compile_failure_raises —— compile 对无效配置应抛出明确异常
# ---------------------------------------------------------------------------


def test_compile_failure_raises():
    """compile(pool_config) 对无效配置应抛出明确异常（如 KeyError/ValueError）。

    _normalize_nodes 对 None / 非 dict 配置访问 .get() 应抛 AttributeError；
    compile({}) 空配置合法（返回空 CompiledPool）。
    """
    from core.execution_module import compile

    invalid_configs = [
        None,               # None 配置 → AttributeError
        "not_a_dict",       # 非 dict → AttributeError
    ]
    raised = 0
    for cfg in invalid_configs:
        with pytest.raises((AttributeError, TypeError, KeyError, ValueError)):
            compile(cfg)
        raised += 1
    # 两个无效配置均应抛异常
    assert raised == len(invalid_configs), \
        f"无效配置应全部抛异常，实际 {raised}/{len(invalid_configs)}"

    # 空字典配置合法（不抛异常）
    result = compile({})
    assert result is not None
    assert result.nodes == {}


# ---------------------------------------------------------------------------
# 3. test_call_depth_within_three —— 调用深度不超过 3 层
# ---------------------------------------------------------------------------


def _call_nesting_depth(node: ast.AST) -> int:
    """递归计算单个 Call 节点的嵌套深度（func / args / keywords 中的 Call）。"""
    if not isinstance(node, ast.Call):
        return 0
    inner = 0
    if isinstance(node.func, ast.Call):
        inner = max(inner, _call_nesting_depth(node.func))
    for arg in node.args:
        if isinstance(arg, ast.Call):
            inner = max(inner, _call_nesting_depth(arg))
    for kw in node.keywords:
        if isinstance(kw.value, ast.Call):
            inner = max(inner, _call_nesting_depth(kw.value))
    return inner + 1


def _func_max_call_depth(func) -> int:
    """解析函数源码，返回最大 Call 嵌套深度。"""
    try:
        src = inspect.getsource(func)
    except (OSError, TypeError):
        return 0
    src = src.lstrip()
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return 0
    max_depth = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            max_depth = max(max_depth, _call_nesting_depth(node))
    return max_depth


def test_call_depth_within_three():
    """调用深度不超过 3 层（通过 ast 检查 trigger_check/filter_eval/propagate_apply）。

    架构约束：单条边执行器（gate→filter→propagate）的函数调用嵌套深度 ≤ 3，
    避免深层调用栈导致的运行时开销与调试困难。
    """
    from core.execution_module import trigger_check, filter_eval, propagate_apply

    for name, func in [
        ("trigger_check", trigger_check),
        ("filter_eval", filter_eval),
        ("propagate_apply", propagate_apply),
    ]:
        depth = _func_max_call_depth(func)
        assert depth <= 3, (
            f"{name} 调用深度 {depth} 超过 3 层（架构约束）"
        )


# ---------------------------------------------------------------------------
# 4. test_unregistered_role_default —— 未注册的角色应被优雅处理
# ---------------------------------------------------------------------------


def test_unregistered_role_default():
    """未注册的角色应被优雅处理（_ROLE_ACTIONS.get 返回默认动作）。

    _ROLE_ACTIONS 对未注册角色返回空 dict → on_enter/on_exit 返回空列表 →
    _dispatch_role_action 无副作用（不抛异常、不执行任何动作）。
    """
    from core.engine import _ROLE_ACTIONS, _dispatch_role_action

    unregistered = "completely_unknown_role_xyz"
    assert unregistered not in _ROLE_ACTIONS, "前置：角色未注册"

    # _ROLE_ACTIONS.get 对未注册角色返回空 dict
    actions = _ROLE_ACTIONS.get(unregistered, {})
    assert actions == {}, f"未注册角色应返回空 dict，实际: {actions}"
    assert actions.get("on_enter", []) == [], "未注册角色 on_enter 应为空列表"

    # _dispatch_role_action 对未注册角色应无副作用
    class _DummyEngine:
        pass

    try:
        _dispatch_role_action(
            _DummyEngine(), unregistered, "on_enter",
            "fz_node", "fz000001", 34500.0,
        )
    except Exception as exc:
        pytest.fail(f"未注册角色分发应无副作用，却抛异常: {exc}")


# ---------------------------------------------------------------------------
# 5. test_decouple_failure_recovery —— 解耦失败时不应影响 ActionDispatcher
# ---------------------------------------------------------------------------


def test_decouple_failure_recovery():
    """事件-信号-动作解耦失败时（如 SignalDeriver 异常）不应影响 ActionDispatcher。

    三层正交架构：Event → SignalDeriver → Signal → ActionDispatcher。
    SignalDeriver 与 ActionDispatcher 各自独立订阅不同事件类型，
    SignalDeriver 的配置缺失/异常不影响 ActionDispatcher 对 Signal 的处理。
    """
    from core.event_bus import EventBus, Signal, StockChanged
    from core.trade_module import SignalDeriver, ActionDispatcher

    bus = EventBus()

    # SignalDeriver 使用不匹配的 role 配置（"bad_role" 不匹配任何节点）
    roles_config = {"bad_role": {"on_enter": ["publish_buy_signal"]}}
    SignalDeriver(bus, roles_config)

    # ActionDispatcher 独立订阅 Signal
    action_table = {"BUY": ["play_sound"], "SELL": ["show_popup"]}
    dispatcher = ActionDispatcher(bus, action_table)

    # 发布 StockChanged（SignalDeriver 处理，但因 role 不匹配不发 Signal）
    bus.publish(StockChanged(
        node_id="fz_pool", code="fz000001", action="enter", ts=34500.0,
    ))

    # 直接发布 Signal，验证 ActionDispatcher 独立工作（不受 SignalDeriver 影响）
    sig = Signal(
        signal_type="BUY", code="fz000001", pool_id="fz_pool",
        price=10.0, ts=34500.0,
    )
    try:
        bus.publish(sig)
    except Exception as exc:
        pytest.fail(f"SignalDeriver 解耦失败不应影响 ActionDispatcher: {exc}")

    assert dispatcher is not None


# ---------------------------------------------------------------------------
# 6. test_propagate_unknown_mode_default —— 未知 propagate mode 应回退默认 copy
# ---------------------------------------------------------------------------


def test_propagate_unknown_mode_default():
    """未知 propagate mode 应回退到默认 copy 行为。

    propagate_apply 对未知 mode 使用 fallback lambda：
    ``lambda s, t, p: list(set(t + p))``，与 copy 模式行为一致（集合并集）。
    """
    from core.execution_module import propagate_apply

    src = ["fz000001", "fz000002"]
    tgt = ["fz000003"]
    passed = ["fz000004", "fz000005"]

    # 未知 mode
    result = propagate_apply(src, tgt, passed, {"mode": "totally_unknown_mode"})
    # 应回退到 copy 行为：list(set(tgt + passed))
    expected = list(set(tgt + passed))
    assert set(result) == set(expected), (
        f"未知 mode 应回退到 copy，结果 {set(result)} != 预期 {set(expected)}"
    )

    # 验证与已知 copy 模式产生相同结果
    copy_result = propagate_apply(src, tgt, passed, {"mode": "copy"})
    assert set(result) == set(copy_result), \
        "未知 mode 回退结果应与 copy 模式一致"


# ---------------------------------------------------------------------------
# 7. test_filter_spec_malformed_default —— 畸形 filter_spec 应默认全部通过
# ---------------------------------------------------------------------------


def test_filter_spec_malformed_default(tick_table):
    """畸形 filter_spec 应被拒绝或默认全部通过。

    filter_eval 对 None / 空字典 / enabled=False / 未知 nset / 未知 noperate
    均默认全部通过（rejected 为空），避免畸形配置导致股票被错误过滤。
    """
    from core.execution_module import filter_eval

    codes = ["fz000001", "fz000002", "fz000003"]
    malformed_specs: List[Dict[str, Any]] = [
        None,                                                # None
        {},                                                  # 空字典
        {"enabled": False},                                  # 显式禁用
        {"enabled": True, "nset": "garbage_xxx"},            # 未知 nset
        {"enabled": True, "noperate": "garbage_yyy"},        # 未知 noperate
        {"enabled": True, "nset": "garbage", "noperate": "garbage"},  # 全未知
    ]
    for spec in malformed_specs:
        passed, rejected = filter_eval(codes, spec, tick_table)
        assert set(passed) == set(codes), (
            f"畸形 filter_spec 应默认全部通过，passed={passed}, spec={spec}"
        )
        assert rejected == [], (
            f"畸形 filter_spec 不应有拒绝，rejected={rejected}, spec={spec}"
        )
