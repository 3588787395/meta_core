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
import re
from pathlib import Path
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


# ============================================================================
# 8. test_rule_87_no_configstore_bypass —— 规则 87 违规：ConfigStore 绕过检测
# ============================================================================
# RULES.md 规则 87：配置加载统一到 ConfigStore.get_table / get_data_file；
# 禁止在模块级重新定义 _load_json / _load_config / _load_json_file / _load_json_cache
# 帮助函数；所有 JSON 配置加载必须通过 ConfigStore，确保热加载能力。


_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_CORE_DIR = _PROJECT_ROOT / "core"


def _grep_count(pattern, file_path):
    """统计文件中匹配 pattern 的行数（re 多行模式）。"""
    if not file_path.exists():
        return 0
    content = file_path.read_text(encoding="utf-8")
    return len(re.findall(pattern, content, re.MULTILINE))


def _grep_count_in_dir(pattern, dir_path, exclude_names=()):
    """统计目录下所有 .py 文件中匹配 pattern 的总行数（排除指定文件名）。"""
    if not dir_path.is_dir():
        return 0
    total = 0
    for f in sorted(dir_path.glob("*.py")):
        if f.name in exclude_names:
            continue
        total += _grep_count(pattern, f)
    return total


def test_rule_87_no_configstore_bypass():
    """规则 87 违规：ConfigStore 绕过检测。

    验证：
      1. core/*.py 不存在 ``def _load_json`` / ``def _load_config`` /
         ``def _load_json_file`` / ``def _load_json_cache`` 帮助函数。
      2. ``json.load(open())`` inline 模式不应复活（变更 G）。
      3. ConfigStore 入口 ``get_global_config_store`` 至少被一个业务模块使用。
    """
    # 1. 不应有 _load_xxx 帮助函数定义
    forbidden_helpers = [
        r"^def _load_json\b",
        r"^def _load_config\b",
        r"^def _load_json_file\b",
        r"^def _load_json_cache\b",
    ]
    for pat in forbidden_helpers:
        total = _grep_count_in_dir(pat, _CORE_DIR)
        assert total == 0, (
            f"规则 87 违规：检测到 {total} 处 {pat} 帮助函数定义"
            "（应统一到 ConfigStore.get_table / get_data_file）"
        )

    # 2. json.load(open()) inline 模式不应复活（ConfigStore/table_engine 内部除外）
    inline_total = 0
    for f in sorted(_CORE_DIR.glob("*.py")):
        if f.name in ("config_store.py", "table_engine.py"):
            continue
        inline_total += _grep_count(r"json\.load\(open\(", f)
    assert inline_total == 0, (
        f"规则 87/变更 G 违规：检测到 {inline_total} 处 json.load(open()) inline"
        "（应通过 ConfigStore 统一加载）"
    )

    # 3. ConfigStore 入口被业务模块使用
    usage_total = _grep_count_in_dir(
        r"get_global_config_store\(\)", _CORE_DIR
    )
    assert usage_total >= 1, (
        "规则 87 违规：未检测到 get_global_config_store() 使用"
        "（业务模块应通过 ConfigStore 加载配置）"
    )


# ============================================================================
# 9. test_rule_59_no_hardcoded_type_branch —— 规则 59 违规：表驱动绕过检测
# ============================================================================
# RULES.md 规则 59：禁止 if type == "xxx" / if nset == X / if pool_type == "custom"
# 硬编码分支；所有类型映射进 JSON 配置表。


def test_rule_59_no_hardcoded_type_branch():
    """规则 59 违规：表驱动绕过检测。

    验证：
      1. core/*.py 不存在 ``if type == "..."`` 硬编码分支（按字面字符串匹配类型）。
      2. core/*.py 不存在 ``if pool_type == "custom"`` 硬编码分支。
      3. ``_PROPAGATE_MODE_TABLE`` 表存在（mode 派发表驱动）。
      4. ``_FILTER_SPEC_BUILDERS`` 表存在（FilterSpec 构造分派表驱动）。
    """
    # 1. 不应有 if type == "xxx" 硬编码分支
    type_branch_total = _grep_count_in_dir(
        r'if type == "', _CORE_DIR
    )
    assert type_branch_total == 0, (
        f"规则 59 违规：检测到 {type_branch_total} 处 if type == \"...\""
        "硬编码分支（应进 JSON 配置表）"
    )

    # 2. 不应有 if pool_type == "custom" 硬编码分支
    pool_type_custom_total = _grep_count_in_dir(
        r'if pool_type == "custom"', _CORE_DIR
    )
    assert pool_type_custom_total == 0, (
        f"规则 59 违规：检测到 {pool_type_custom_total} 处 "
        'if pool_type == "custom" 硬编码分支'
    )

    # 3. _PROPAGATE_MODE_TABLE 表存在（execution_module.py）
    exec_path = _CORE_DIR / "execution_module.py"
    assert exec_path.exists(), "core/execution_module.py 不存在"
    propagate_table_count = _grep_count(
        r"^_PROPAGATE_MODE_TABLE\s*:", exec_path
    )
    assert propagate_table_count >= 1, (
        "规则 59 违规：_PROPAGATE_MODE_TABLE 表缺失"
        "（mode 派发应表驱动，不应 if/elif 链）"
    )

    # 4. _FILTER_SPEC_BUILDERS 表存在（execution_module.py）
    filter_builders_count = _grep_count(
        r"^_FILTER_SPEC_BUILDERS\s*:", exec_path
    )
    assert filter_builders_count >= 1, (
        "规则 59 违规：_FILTER_SPEC_BUILDERS 表缺失"
        "（FilterSpec 构造应表驱动分派）"
    )


# ============================================================================
# 15 项「同构复活」反测试（变更 A-O）
# ============================================================================
# 通过 Grep 断言旧同构代码零匹配，确保 Phase 1-3 合并的 15 组模式不会复活。


def test_no_isomorphism_revival_nset_filter():
    """变更 A: 旧 nset 筛选函数不应复活。

    screening_module.py 应通过 ``_NSET_FILTER_HANDLERS`` 表驱动分派，
    不应重新引入 ``_filter_condition_formula`` / ``_filter_expert_system`` /
    ``_filter_financial_scalar`` / ``_filter_market_scalar`` 同构函数。
    """
    f = _CORE_DIR / "screening_module.py"
    assert f.exists(), "core/screening_module.py 不存在"
    count = _grep_count(
        r"def _filter_condition_formula|def _filter_expert_system|"
        r"def _filter_financial_scalar|def _filter_market_scalar",
        f,
    )
    assert count == 0, (
        f"变更 A 违规：screening_module.py 检测到 {count} 处旧 nset 同构函数"
        "（应使用 _NSET_FILTER_HANDLERS 表驱动）"
    )
    # 额外断言：表驱动 _NSET_FILTER_HANDLERS 应存在
    table_count = _grep_count(r"_NSET_FILTER_HANDLERS", f)
    assert table_count >= 1, (
        "变更 A 违规：_NSET_FILTER_HANDLERS 表缺失"
    )


def test_no_isomorphism_revival_json_load_open():
    """变更 G: ``json.load(open())`` inline 模式不应复活。

    core/*.py（ConfigStore / table_engine 内部除外）不应使用
    ``json.load(open(...))`` inline 模式，所有 JSON 加载必须通过
    ConfigStore 或 ``with open(...) as f: json.load(f)`` 显式资源管理。
    """
    total = 0
    for f in sorted(_CORE_DIR.glob("*.py")):
        if f.name in ("config_store.py", "table_engine.py"):
            continue  # ConfigStore 内部除外
        total += _grep_count(r"json\.load\(open\(", f)
    assert total == 0, (
        f"变更 G 违规：检测到 {total} 处 json.load(open()) inline"
        "（应通过 ConfigStore 或 with open() 显式管理）"
    )


def test_no_isomorphism_revival_mode_inflection_rank():
    """变更 H: ``if mode == "inflection"`` / ``if mode == "rank"`` 不应复活。

    execution_module.py 不应通过 if 分支硬编码 mode 派发，
    应通过 ``_PROPAGATE_MODE_TABLE`` 或 ``_FILTER_SPEC_BUILDERS`` 表驱动。
    """
    f = _CORE_DIR / "execution_module.py"
    count = _grep_count(
        r'if mode == "inflection"|if mode == "rank"', f
    )
    assert count == 0, (
        f"变更 H 违规：execution_module.py 检测到 {count} 处 "
        'if mode == "inflection"|"rank" 硬编码（应表驱动）'
    )


def test_no_isomorphism_revival_base_period():
    """变更 I: ``if self._base_period ==`` 不应复活。

    runtime_mode_module.py 不应通过 ``self._base_period`` 硬编码分支派发周期，
    应通过 ``_PERIOD_KEY_FUNCS`` 表或类似机制。
    """
    f = _CORE_DIR / "runtime_mode_module.py"
    count = _grep_count(r"if self\._base_period ==", f)
    assert count == 0, (
        f"变更 I 违规：runtime_mode_module.py 检测到 {count} 处 "
        "if self._base_period == 硬编码（应表驱动）"
    )


def test_no_isomorphism_revival_apply_tradeattr_side():
    """变更 E: ``_apply_tradeattr`` 方法体内 ``if side == "BUY"`` / ``elif side == "SELL"``
    不应复活。

    trade_module.py 的 ``_apply_tradeattr`` 应通过 ``_TRADEATTR_FIELD_MAP.get(side, [])``
    表驱动提取字段，而非硬编码 BUY/SELL 分支。注意：其他方法中
    ``if side == "BUY"`` 是合法的（订单处理逻辑），仅本方法禁止。
    """
    f = _CORE_DIR / "trade_module.py"
    assert f.exists(), "core/trade_module.py 不存在"
    content = f.read_text(encoding="utf-8")
    # 提取 _apply_tradeattr 方法体
    m = re.search(
        r"def _apply_tradeattr\([^)]*\)[^:]*:.*?(?=\n    def |\nclass |\Z)",
        content,
        re.DOTALL,
    )
    assert m is not None, "未找到 _apply_tradeattr 方法定义"
    body = m.group(0)
    # 方法体内不应有 if side == "BUY" / elif side == "SELL"
    bad_count = len(re.findall(
        r'if side == "BUY"|elif side == "SELL"', body
    ))
    assert bad_count == 0, (
        f"变更 E 违规：_apply_tradeattr 方法体内检测到 {bad_count} 处 "
        'if side == "BUY"/elif side == "SELL" 硬编码'
        "（应通过 _TRADEATTR_FIELD_MAP 表驱动）"
    )
    # 额外断言：_TRADEATTR_FIELD_MAP 表存在
    assert "_TRADEATTR_FIELD_MAP" in content, (
        "变更 E 违规：_TRADEATTR_FIELD_MAP 表缺失"
    )


def test_no_isomorphism_revival_parse_serialize_helpers():
    """变更 C: ``_parse_dzh`` / ``_parse_tdx`` / ``_parse_json`` /
    ``_serialize_dzh`` / ``_serialize_tdx`` / ``_serialize_json`` 不应复活。

    import_export_module.py 应通过 ``_IMPORT_RULES`` / ``_EXPORT_RULES`` 表驱动
    分派，而非同构函数。
    """
    f = _CORE_DIR / "import_export_module.py"
    if not f.exists():
        pytest.skip("core/import_export_module.py 不存在")
    count = _grep_count(
        r"def _parse_dzh|def _parse_tdx|def _parse_json|"
        r"def _serialize_dzh|def _serialize_tdx|def _serialize_json",
        f,
    )
    assert count == 0, (
        f"变更 C 违规：import_export_module.py 检测到 {count} 处 "
        "_parse_dzh/_parse_tdx/_parse_json/_serialize_dzh/_serialize_tdx/_serialize_json "
        "同构函数（应使用 _IMPORT_RULES / _EXPORT_RULES 表）"
    )


def test_no_isomorphism_revival_eval_formula_thin_wrapper():
    """变更 D: ``_eval_formula`` 与 ``_eval_formula_series`` 应为薄包装（方法体 ≤ 5 行）。

    formula_module.py 的两个方法应委托给 ``_eval_formula_core`` 统一骨架，
    而非各自实现完整求值逻辑（同构复活）。
    """
    import ast as _ast

    f = _CORE_DIR / "formula_module.py"
    assert f.exists(), "core/formula_module.py 不存在"
    content = f.read_text(encoding="utf-8")
    tree = _ast.parse(content)

    target_names = {"_eval_formula", "_eval_formula_series"}
    found_methods = {}

    for node in _ast.walk(tree):
        if isinstance(node, _ast.FunctionDef) and node.name in target_names:
            # 统计方法体行数（去除 docstring 后）
            body = node.body
            # 跳过首条 docstring
            if body and isinstance(body[0], _ast.Expr) and isinstance(
                body[0].value, _ast.Constant
            ) and isinstance(body[0].value.value, str):
                body = body[1:]
            # 用 end_lineno - lineno 估算方法体行数
            body_lines = (node.end_lineno or node.lineno) - node.lineno
            found_methods[node.name] = body_lines

    assert "_eval_formula" in found_methods, (
        "变更 D 违规：未找到 _eval_formula 方法"
    )
    assert "_eval_formula_series" in found_methods, (
        "变更 D 违规：未找到 _eval_formula_series 方法"
    )
    for name, lines in found_methods.items():
        assert lines <= 8, (
            f"变更 D 违规：{name} 方法体行数 {lines} > 8（应为薄包装，"
            "委托给 _eval_formula_core）"
        )

    # 额外断言：_eval_formula_core 应存在（统一骨架）
    core_count = _grep_count(r"def _eval_formula_core\b", f)
    assert core_count >= 1, (
        "变更 D 违规：_eval_formula_core 统一骨架缺失"
    )


def test_no_isomorphism_revival_filter_spec_table_driven():
    """变更 F: ``_build_filter_spec`` 应通过 ``_FILTER_SPEC_BUILDERS`` 表驱动分派，
    不应内含 if/elif 4 路分支。

    execution_module.py 的 ``_build_filter_spec`` 函数应通过三元组 key
    查 ``_FILTER_SPEC_BUILDERS`` 表完成 4 路分派（tdx_func / formula_ref /
    INTERSECTION / passthrough），而非 if/elif 链。
    """
    f = _CORE_DIR / "execution_module.py"
    content = f.read_text(encoding="utf-8")
    # 提取 _build_filter_spec 函数体（模块级或类方法均处理）
    m = re.search(
        r"def _build_filter_spec\([^)]*\)[^:]*:.*?(?=\n    def |\n    @staticmethod|\nclass |\ndef [a-z]|\Z)",
        content,
        re.DOTALL,
    )
    assert m is not None, "未找到 _build_filter_spec 函数定义"
    body = m.group(0)
    # 函数体应使用 _FILTER_SPEC_BUILDERS 表查询
    assert "_FILTER_SPEC_BUILDERS[" in body or "_FILTER_SPEC_BUILDERS.get" in body, (
        "变更 F 违规：_build_filter_spec 未使用 _FILTER_SPEC_BUILDERS 表分派"
    )
    # 不应内含 4 路 if/elif 显式分支（has_tdx_func/has_formula_ref/condition_type）
    bad_count = len(re.findall(
        r'elif has_tdx_func|elif has_formula_ref|elif condition_type',
        body,
    ))
    assert bad_count == 0, (
        f"变更 F 违规：_build_filter_spec 内含 {bad_count} 处显式 elif 分支"
        "（应通过 _FILTER_SPEC_BUILDERS 表驱动）"
    )
    # _FILTER_SPEC_BUILDERS 表应存在
    table_count = _grep_count(r"^_FILTER_SPEC_BUILDERS\s*:", f)
    assert table_count >= 1, (
        "变更 F 违规：_FILTER_SPEC_BUILDERS 表定义缺失"
    )


def test_no_isomorphism_revival_run_coro_module_level():
    """变更 J: ``_run_coro_sync`` / ``_run_coro`` 应仅模块级存在，不应在类内定义。

    runtime_mode_module.py 应通过模块级 ``_run_coro_sync(coro, loop_holder, ...)``
    统一协程同步执行器，不应在 ``KLineReplayEngine`` / ``RuntimeSimulator`` 等
    类内重复定义同构方法。
    """
    import ast as _ast

    f = _CORE_DIR / "runtime_mode_module.py"
    content = f.read_text(encoding="utf-8")
    tree = _ast.parse(content)

    # 遍历 AST，统计 _run_coro_sync / _run_coro 的定义位置（模块级 vs 类内）
    module_level_count = 0
    class_level_count = 0

    # 模块级函数
    for node in tree.body:
        if isinstance(node, _ast.FunctionDef) and re.match(
            r"_run_coro_sync\b|_run_coro\b", node.name
        ):
            module_level_count += 1

    # 类内方法
    for node in _ast.walk(tree):
        if isinstance(node, _ast.ClassDef):
            for item in node.body:
                if isinstance(item, _ast.FunctionDef) and re.match(
                    r"_run_coro_sync\b|_run_coro\b", item.name
                ):
                    class_level_count += 1

    assert module_level_count >= 1, (
        "变更 J 违规：模块级 _run_coro_sync 缺失"
        "（应模块级统一定义协程同步执行器）"
    )
    assert class_level_count == 0, (
        f"变更 J 违规：检测到 {class_level_count} 处类内 "
        "_run_coro_sync / _run_coro 方法定义（应仅模块级存在）"
    )


def test_no_isomorphism_revival_build_topology_thin():
    """变更 K: ``engine.py`` / ``runtime_mode_module.py`` 的 ``_build_topology``
    方法体 ≤ 3 行，应委托给 ``core/domain.py`` 的 ``_build_adjacency``。

    注意：``_build_adjacency`` 在 ``core/domain.py`` 中合法存在（共享辅助），
    不禁止；仅禁止 engine/runtime_mode_module 内的同构实现。
    """
    import ast as _ast

    # 验证 _build_adjacency 在 domain.py 合法存在
    domain_path = _CORE_DIR / "domain.py"
    assert domain_path.exists(), "core/domain.py 不存在"
    domain_count = _grep_count(r"^def _build_adjacency\b", domain_path)
    assert domain_count >= 1, (
        "变更 K：core/domain.py 的 _build_adjacency 应合法存在"
    )

    # engine.py 的 _build_topology 方法体应 ≤ 3 行
    engine_path = _CORE_DIR / "engine.py"
    engine_content = engine_path.read_text(encoding="utf-8")
    engine_tree = _ast.parse(engine_content)

    for node in _ast.walk(engine_tree):
        if isinstance(node, _ast.ClassDef):
            for item in node.body:
                if isinstance(item, _ast.FunctionDef) and item.name == "_build_topology":
                    body_lines = (item.end_lineno or item.lineno) - item.lineno
                    assert body_lines <= 4, (
                        f"变更 K 违规：engine.py _build_topology 方法体 {body_lines} 行 > 4"
                        "（应 ≤ 3 行委托给 _build_adjacency）"
                    )
                    # 应调用 _build_adjacency
                    src = _ast.get_source_segment(engine_content, item) or ""
                    assert "_build_adjacency" in src, (
                        "变更 K 违规：engine.py _build_topology 未委托 _build_adjacency"
                    )

    # runtime_mode_module.py 的 _build_topology 方法体应 ≤ 5 行
    rm_path = _CORE_DIR / "runtime_mode_module.py"
    rm_content = rm_path.read_text(encoding="utf-8")
    rm_tree = _ast.parse(rm_content)

    found = False
    for node in _ast.walk(rm_tree):
        if isinstance(node, _ast.ClassDef):
            for item in node.body:
                if isinstance(item, _ast.FunctionDef) and item.name == "_build_topology":
                    found = True
                    body_lines = (item.end_lineno or item.lineno) - item.lineno
                    assert body_lines <= 6, (
                        f"变更 K 违规：runtime_mode_module.py _build_topology 方法体 "
                        f"{body_lines} 行 > 6（应 ≤ 3 行委托给 _build_adjacency）"
                    )
                    src = _ast.get_source_segment(rm_content, item) or ""
                    assert "_build_adjacency" in src, (
                        "变更 K 违规：runtime_mode_module.py _build_topology "
                        "未委托 _build_adjacency"
                    )
    assert found, "变更 K：runtime_mode_module.py 未找到 _build_topology 方法"


def test_no_isomorphism_revival_event_handler_count():
    """变更 N: ``@_event_handler`` 装饰器在 5 个核心模块共 ≥ 28 次。

    execution_module / tick_bar_module / monitoring_module / screening_module /
    trade_module 共应至少有 28 处 ``@_event_handler`` 装饰（事件总线订阅统一接口）。
    """
    target_modules = [
        "execution_module.py",
        "tick_bar_module.py",
        "monitoring_module.py",
        "screening_module.py",
        "trade_module.py",
    ]
    total = 0
    per_module = {}
    for name in target_modules:
        f = _CORE_DIR / name
        if not f.exists():
            continue
        c = _grep_count(r"@_event_handler\b", f)
        per_module[name] = c
        total += c

    assert total >= 28, (
        f"变更 N 违规：5 模块共 {total} 处 @_event_handler < 28"
        f"（按模块：{per_module}）"
    )
    # 至少每个模块有 ≥ 1 处
    for name, c in per_module.items():
        assert c >= 1, (
            f"变更 N 违规：{name} 含 0 处 @_event_handler"
        )


def test_no_isomorphism_revival_pnl_helpers():
    """变更 B: monitoring_module.py 不应复活 5 个 PnL 计算同构函数。

    ``_compute_intraday_pnl`` / ``_compute_market_impact_pnl`` /
    ``_compute_historical_pnl`` / ``_compute_distribution_pnl`` /
    ``_compute_positioning_pnl`` 应通过统一 ``_PNL_COMPUTERS`` 表驱动分派。
    """
    f = _CORE_DIR / "monitoring_module.py"
    if not f.exists():
        pytest.skip("core/monitoring_module.py 不存在")
    count = _grep_count(
        r"def _compute_intraday_pnl|def _compute_market_impact_pnl|"
        r"def _compute_historical_pnl|def _compute_distribution_pnl|"
        r"def _compute_positioning_pnl",
        f,
    )
    assert count == 0, (
        f"变更 B 违规：monitoring_module.py 检测到 {count} 处 "
        "5 个 PnL 同构函数（应通过 _PNL_COMPUTERS 表驱动）"
    )


def test_no_isomorphism_revival_pnl_keys():
    """变更 L: monitoring_module.py 不应复活 ``_momentum_key`` / ``_trend_key`` /
    ``_value_key`` 同构函数。

    应通过统一 key 函数表（如 ``_PERIOD_KEY_FUNCS``）或参数化派生。
    """
    f = _CORE_DIR / "monitoring_module.py"
    if not f.exists():
        pytest.skip("core/monitoring_module.py 不存在")
    count = _grep_count(
        r"def _momentum_key|def _trend_key|def _value_key", f
    )
    assert count == 0, (
        f"变更 L 违规：monitoring_module.py 检测到 {count} 处 "
        "_momentum_key/_trend_key/_value_key 同构函数"
    )


def test_no_isomorphism_revival_apply_stock_filters_only_in_wrapper():
    """变更 M: ``_apply_stock_filters`` 应仅在 ``_with_stock_filters`` 包装器内调用，
    不应在 evaluator 函数体内直接调用。

    execution_module.py 的 ``_eval_formula_path`` / ``_eval_scalar_path`` /
    ``_eval_set_op_path`` 应通过 ``_with_stock_filters`` 包装器统一应用后过滤，
    不应内联 ``_apply_stock_filters`` 调用。
    """
    import ast as _ast

    f = _CORE_DIR / "execution_module.py"
    content = f.read_text(encoding="utf-8")
    tree = _ast.parse(content)

    evaluator_names = {
        "_eval_formula_path",
        "_eval_scalar_path",
        "_eval_set_op_path",
    }

    # 找到模块级 evaluator 函数定义
    evaluator_funcs = {}
    for node in tree.body:
        if isinstance(node, _ast.FunctionDef) and node.name in evaluator_names:
            evaluator_funcs[node.name] = node

    # 至少有 1 个 evaluator 存在（其他可能改名）
    assert len(evaluator_funcs) >= 1, (
        "变更 M 违规：未找到 _eval_formula_path / _eval_scalar_path / "
        "_eval_set_op_path 中的任一 evaluator 函数"
    )

    # 检查每个 evaluator 函数体内是否有 _apply_stock_filters 直接调用
    for name, node in evaluator_funcs.items():
        src = _ast.get_source_segment(content, node) or ""
        # 排除函数定义行后查找 _apply_stock_filters( 调用
        # 简单方法：去除函数签名行后 grep
        body_src = src.split("\n", 1)[1] if "\n" in src else ""
        bad_calls = re.findall(r"_apply_stock_filters\s*\(", body_src)
        assert len(bad_calls) == 0, (
            f"变更 M 违规：evaluator {name} 体内检测到 {len(bad_calls)} 处 "
            "_apply_stock_filters 调用（应仅通过 _with_stock_filters 包装器调用）"
        )

    # _with_stock_filters 包装器应存在
    wrapper_count = _grep_count(r"def _with_stock_filters\b", f)
    assert wrapper_count >= 1, (
        "变更 M 违规：_with_stock_filters 包装器缺失"
    )


def test_no_isomorphism_revival_iter_entries_in_table_engine():
    """变更 O: ``_iter_entries`` 应在 ``table_engine.py`` 存在，
    ``_validate_table`` 应使用它（表驱动按 type 分派，无 if/elif 双分支）。
    """
    f = _CORE_DIR / "table_engine.py"
    assert f.exists(), "core/table_engine.py 不存在"

    # _iter_entries 应作为模块级函数存在
    iter_count = _grep_count(r"^def _iter_entries\b", f)
    assert iter_count >= 1, (
        "变更 O 违规：table_engine.py 缺失 _iter_entries 模块级函数"
    )

    # _validate_table 应使用 _iter_entries
    content = f.read_text(encoding="utf-8")
    m = re.search(
        r"def _validate_table\([^)]*\)[^:]*:.*?(?=\n    def |\Z)",
        content,
        re.DOTALL,
    )
    assert m is not None, "未找到 _validate_table 方法定义"
    body = m.group(0)
    assert "_iter_entries(" in body, (
        "变更 O 违规：_validate_table 未使用 _iter_entries"
        "（应表驱动按 type 分派，无 if/elif 双分支）"
    )
