"""Task 18.3: edge execution three layers positive tests.

Verify the three-element functions in core/execution_module:
  - trigger_check(edge_timing_spec, now_ts, flow_state, node_dirty) -> bool
  - filter_eval(codes, filter_spec, tick_table) -> (passed, rejected)
  - propagate_apply(src_stocks, tgt_stocks, passed, propagate_spec) -> list
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List

import pytest


_THIS_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _THIS_DIR.parent
_CORE_DIR = _PROJECT_ROOT / "core"


# 1. trigger_check returns bool
def test_trigger_check_returns_bool():
    """trigger_check(...) should return a bool value."""
    from core.execution_module import trigger_check

    result = trigger_check({"starttype": "immediate", "cxtype": "always"}, 100.0, {}, True)
    assert isinstance(result, bool), f"expected bool, got {type(result)}"
    assert result is True

    result2 = trigger_check({"starttype": "immediate", "cxtype": "always"}, 100.0, {}, False)
    assert isinstance(result2, bool)
    assert result2 is False


# 2. trigger_check node_dirty=False returns False
def test_trigger_check_node_dirty_false_returns_false():
    """node_dirty=False short-circuits trigger_check to False."""
    from core.execution_module import trigger_check

    result = trigger_check(
        edge_timing_spec={"starttype": "immediate", "cxtype": "always"},
        now_ts=100.0,
        flow_state={},
        node_dirty=False,
    )
    assert result is False
    result2 = trigger_check(
        edge_timing_spec={"starttype": "delay", "cxtype": "once", "delay": 10},
        now_ts=1000.0,
        flow_state={"start_ts": 0, "exec_count": 0},
        node_dirty=False,
    )
    assert result2 is False


# 3. trigger_check immediate+always+node_dirty=True returns True
def test_trigger_check_immediate_always():
    """starttype=immediate + cxtype=always + node_dirty=True -> True."""
    from core.execution_module import trigger_check

    result = trigger_check(
        edge_timing_spec={"starttype": "immediate", "cxtype": "always"},
        now_ts=34500.0,
        flow_state={},
        node_dirty=True,
    )
    assert result is True, "immediate+always+node_dirty=True should return True"
    assert trigger_check(
        {"starttype": "immediate", "cxtype": "always"},
        99999.9, {"exec_count": 5}, True
    ) is True


# 4. filter_eval returns (passed, rejected) tuple
def test_filter_eval_returns_tuple(tick_table):
    """filter_eval(...) returns a 2-tuple (passed_codes, rejected_codes)."""
    from core.execution_module import filter_eval

    codes = ["fz000001", "fz000002", "fz000003"]
    result = filter_eval(codes, {}, tick_table)
    assert isinstance(result, tuple), f"expected tuple, got {type(result)}"
    assert len(result) == 2
    passed, rejected = result
    assert isinstance(passed, list)
    assert isinstance(rejected, list)


# 5. filter_eval empty codes returns ([], [])
def test_filter_eval_empty_codes_returns_empty(tick_table):
    """codes=[] returns ([], [])."""
    from core.execution_module import filter_eval

    passed, rejected = filter_eval([], {}, tick_table)
    assert passed == []
    assert rejected == []
    passed2, rejected2 = filter_eval(
        [], {"enabled": True, "nset": "all", "noperate": "gt", "threshold": 0},
        tick_table,
    )
    assert passed2 == []
    assert rejected2 == []


# 6. filter_eval all passes when no filter
def test_filter_eval_all_passes_when_no_filter(tick_table):
    """filter_spec empty or enabled=False means all codes pass."""
    from core.execution_module import filter_eval

    codes = ["fz000001", "fz000002", "fz000003"]
    passed1, rejected1 = filter_eval(codes, {}, tick_table)
    assert passed1 == codes
    assert rejected1 == []
    passed2, rejected2 = filter_eval(
        codes, {"enabled": False, "nset": "all", "noperate": "gt"}, tick_table
    )
    assert passed2 == codes
    assert rejected2 == []


# 7. propagate_apply copy mode returns list(set(tgt + passed))
def test_propagate_apply_copy_mode():
    """copy mode: returns list(set(tgt + passed)), src not merged."""
    from core.execution_module import propagate_apply

    src_stocks = ["fz000001", "fz000002"]
    tgt_stocks = ["fz000003"]
    passed = ["fz000001", "fz000004"]
    result = propagate_apply(src_stocks, tgt_stocks, passed, {"mode": "copy"})
    expected = list(set(tgt_stocks + passed))
    assert sorted(result) == sorted(expected), f"copy: {result}"
    assert "fz000002" not in result


# 8. propagate_apply move mode returns list(set(tgt + passed))
def test_propagate_apply_move_mode():
    """move mode: also returns list(set(tgt + passed))."""
    from core.execution_module import propagate_apply

    src_stocks = ["fz000001", "fz000002"]
    tgt_stocks = ["fz000003"]
    passed = ["fz000001", "fz000004"]
    result = propagate_apply(src_stocks, tgt_stocks, passed, {"mode": "move"})
    expected = list(set(tgt_stocks + passed))
    assert sorted(result) == sorted(expected)


# 9. propagate_apply overwrite mode returns list(passed)
def test_propagate_apply_overwrite_mode():
    """overwrite mode: returns list(passed), replacing target pool content."""
    from core.execution_module import propagate_apply

    src_stocks = ["fz000001", "fz000002"]
    tgt_stocks = ["fz000003", "fz000099"]
    passed = ["fz000001", "fz000004"]
    result = propagate_apply(src_stocks, tgt_stocks, passed, {"mode": "overwrite"})
    assert sorted(result) == sorted(passed)
    assert "fz000003" not in result
    assert "fz000099" not in result


# 10. propagate_apply unknown mode defaults to copy
def test_propagate_apply_unknown_mode_defaults():
    """Unknown mode value defaults to copy behavior."""
    from core.execution_module import propagate_apply

    src_stocks = ["fz000001"]
    tgt_stocks = ["fz000003"]
    passed = ["fz000001", "fz000004"]
    result_unknown = propagate_apply(
        src_stocks, tgt_stocks, passed, {"mode": "nonexistent_mode"}
    )
    result_missing = propagate_apply(
        src_stocks, tgt_stocks, passed, {}
    )
    expected = list(set(tgt_stocks + passed))
    assert sorted(result_unknown) == sorted(expected), f"unknown mode: {result_unknown}"
    assert sorted(result_missing) == sorted(expected), f"missing mode: {result_missing}"


# 11. No six-layer intermediate functions
def test_no_six_layer_intermediate_functions():
    """Grep verify: no six-layer intermediate function definitions in core/.

    Forbidden functions (must all be absent):
      - _phase_dispatch
      - _phase_nset_filter
      - _dispatch_filter
      - _eval_primitive
      - _extract_prim_params_table
      - _extract_single_param
    """
    forbidden_funcs = [
        "_phase_dispatch",
        "_phase_nset_filter",
        "_dispatch_filter",
        "_eval_primitive",
        "_extract_prim_params_table",
        "_extract_single_param",
    ]
    py_files = list(_CORE_DIR.glob("*.py"))
    assert len(py_files) > 0, f"no core/*.py files in {_CORE_DIR}"
    failures = []
    for py_file in py_files:
        try:
            src = py_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for func_name in forbidden_funcs:
            pattern = r"\bdef\s+" + re.escape(func_name) + r"\b"
            matches = re.findall(pattern, src)
            if matches:
                failures.append(
                    f"{py_file.name}: forbidden function {func_name} ({len(matches)} defs)"
                )
    assert not failures, (
        "core/ has forbidden six-layer intermediate function definitions:\n  " +
        "\n  ".join(failures)
    )


# ============================================================================
# 变更 H：mode 分派表驱动合并回归断言（execution_module if mode == 链 → _MODE_HANDLERS 表）
# ============================================================================


class TestChangeHModeHandlerTable:
    """变更 H：_MODE_HANDLERS 表含 inflection/rank/compare，消除 execution_module if mode == 链。"""

    def test_mode_handlers_table_exists_with_three_modes(self):
        """_MODE_HANDLERS 表存在，含 inflection/rank/compare 三键。"""
        from core.screening_module import _MODE_HANDLERS
        assert isinstance(_MODE_HANDLERS, dict), "_MODE_HANDLERS 应为 dict"
        assert "inflection" in _MODE_HANDLERS, "_MODE_HANDLERS 应含 inflection"
        assert "rank" in _MODE_HANDLERS, "_MODE_HANDLERS 应含 rank"
        assert "compare" in _MODE_HANDLERS, "_MODE_HANDLERS 应含 compare"
        # 每个键映射到可调用 handler
        for mode_name in ("inflection", "rank", "compare"):
            assert callable(_MODE_HANDLERS[mode_name]), \
                f"_MODE_HANDLERS[{mode_name}] 应为可调用 handler"

    def test_apply_noperate_mode_series_exists(self):
        """_eval_formula_path 经 _build_op_ctx/_eval_op 表驱动分派（公式与筛选分离）。"""
        import inspect
        from core.execution_module import _eval_formula_path
        src = inspect.getsource(_eval_formula_path)
        assert "_build_op_ctx" in src and "_eval_op" in src and "_SERIES_FILTERS" in src, \
            "_eval_formula_path 应经 _build_op_ctx/_eval_op + _SERIES_FILTERS 表驱动分派"

    def test_apply_noperate_mode_series_importable(self):
        """_build_op_ctx/_eval_op 可从 domain 导入（白名单，公式与筛选分离真值源）。"""
        from core.domain import _build_op_ctx, _eval_op
        assert callable(_build_op_ctx) and callable(_eval_op), \
            "_build_op_ctx/_eval_op 应为可调用函数"

    def test_no_if_mode_inflection_rank_in_execution_module(self):
        """Grep 验证：execution_module.py 中 if mode == "inflection"/"rank" = 0。"""
        import re
        exec_path = _CORE_DIR / "execution_module.py"
        src = exec_path.read_text(encoding="utf-8")
        legacy_pattern = r'if mode == "inflection"|if mode == "rank"'
        matches = re.findall(legacy_pattern, src)
        assert len(matches) == 0, (
            f'execution_module 不应含 if mode == "inflection"/"rank" 硬编码分支'
            f'（变更 H 已表驱动化），实际 {matches}'
        )


# ============================================================================
# 变更 F：edge 三层参数提取与 FilterSpec 构造表回归断言
# ============================================================================


class TestChangeFEdgeSpecBuilders:
    """变更 F：_extract_edge_params + _FILTER_SPEC_BUILDERS + _TIMING_SPEC_FIELDS + _PROPAGATE_SPEC_FIELDS。"""

    def test_extract_edge_params_exists(self):
        """_extract_edge_params 函数存在（edge 参数提取统一入口）。"""
        import re
        exec_path = _CORE_DIR / "execution_module.py"
        src = exec_path.read_text(encoding="utf-8")
        assert re.search(r"def _extract_edge_params\b", src), \
            "execution_module 应定义 _extract_edge_params 函数（变更 F）"

    def test_filter_spec_builders_table_has_four_builders(self):
        """_FILTER_SPEC_BUILDERS 表含 4 个 FilterSpec 构造器。"""
        from core.execution_module import _FILTER_SPEC_BUILDERS
        assert isinstance(_FILTER_SPEC_BUILDERS, dict), \
            "_FILTER_SPEC_BUILDERS 应为 dict"
        # 表含 4 个构造器条目
        assert len(_FILTER_SPEC_BUILDERS) >= 4, \
            f"_FILTER_SPEC_BUILDERS 应含 ≥4 个构造器，实际 {len(_FILTER_SPEC_BUILDERS)}"
        # 每个条目映射到可调用构造器
        for key, builder in _FILTER_SPEC_BUILDERS.items():
            assert callable(builder), \
                f"_FILTER_SPEC_BUILDERS[{key}] 应为可调用构造器"

    def test_timing_spec_fields_table_exists(self):
        """_TIMING_SPEC_FIELDS 表存在。"""
        from core.execution_module import _TIMING_SPEC_FIELDS
        assert isinstance(_TIMING_SPEC_FIELDS, dict), \
            "_TIMING_SPEC_FIELDS 应为 dict"
        assert len(_TIMING_SPEC_FIELDS) > 0, \
            "_TIMING_SPEC_FIELDS 不应为空"

    def test_propagate_spec_fields_table_exists(self):
        """_PROPAGATE_SPEC_FIELDS 表存在。"""
        from core.execution_module import _PROPAGATE_SPEC_FIELDS
        assert isinstance(_PROPAGATE_SPEC_FIELDS, dict), \
            "_PROPAGATE_SPEC_FIELDS 应为 dict"
        assert len(_PROPAGATE_SPEC_FIELDS) > 0, \
            "_PROPAGATE_SPEC_FIELDS 不应为空"

    def test_extract_edge_params_importable_and_callable(self):
        """_extract_edge_params 可导入且可调用。"""
        from core.execution_module import _extract_edge_params
        assert callable(_extract_edge_params), \
            "_extract_edge_params 应为可调用函数"


# ============================================================================
# 变更 M：_with_stock_filters 包装器合并 stock 后过滤回归断言
# ============================================================================


class TestChangeMWithStockFilters:
    """变更 M：_with_stock_filters 包装器统一 stock 后过滤，evaluator 体内无 _apply_stock_filters。"""

    def test_with_stock_filters_wrapper_exists(self):
        """_with_stock_filters 包装器函数存在。"""
        import re
        exec_path = _CORE_DIR / "execution_module.py"
        src = exec_path.read_text(encoding="utf-8")
        assert re.search(r"def _with_stock_filters\b", src), \
            "execution_module 应定义 _with_stock_filters 包装器（变更 M）"

    def test_with_stock_filters_importable(self):
        """_with_stock_filters 可导入且可调用。"""
        from core.execution_module import _with_stock_filters
        assert callable(_with_stock_filters), \
            "_with_stock_filters 应为可调用包装器"

    def test_apply_stock_filters_exists_as_inner_helper(self):
        """_apply_stock_filters 作为内部辅助函数存在（被 _with_stock_filters 调用）。"""
        import re
        exec_path = _CORE_DIR / "execution_module.py"
        src = exec_path.read_text(encoding="utf-8")
        assert re.search(r"def _apply_stock_filters\b", src), \
            "execution_module 应定义 _apply_stock_filters 辅助函数（变更 M）"

    def test_evaluator_path_handlers_wrapped_by_with_stock_filters(self):
        """formula/scalar/set_operation 三类 evaluator 路径均经 _with_stock_filters 包装。"""
        import re
        exec_path = _CORE_DIR / "execution_module.py"
        src = exec_path.read_text(encoding="utf-8")
        # 注册表中应出现 _with_stock_filters 包装调用
        wrapped_count = len(re.findall(r"_with_stock_filters\(", src))
        assert wrapped_count >= 3, (
            f"至少 3 类 evaluator 路径应经 _with_stock_filters 包装（变更 M），"
            f"实际 {wrapped_count} 处"
        )


# === Task 28.6 回归断言：converge-meta-essence-v4 阶段 3 C8 收敛状态 ===


class TestConvergenceRegressionV4:
    """SubTask 28.6：converge-meta-essence-v4 C8 Step 基类 + _compile_spec / _gate_window 收敛回归。"""

    def test_step_base_class_present(self):
        """execution_module 含 Step 基类（C8 合并 5 个 XStep）。"""
        import ast
        from pathlib import Path
        tree = ast.parse((Path(__file__).resolve().parent.parent / "core" / "execution_module.py").read_text(encoding="utf-8"))
        classes = {n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
        assert "Step" in classes, \
            "execution_module.py 应含 Step 基类（C8 合并 5 个 XStep）"

    def test_step_subclasses_present(self):
        """execution_module 含 5 个 Step 子类（GateStep / FilterStep / PropagateStep / TTLStep / CallbackStep）。"""
        import ast
        from pathlib import Path
        tree = ast.parse((Path(__file__).resolve().parent.parent / "core" / "execution_module.py").read_text(encoding="utf-8"))
        classes = {n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
        for cls in ("GateStep", "FilterStep", "PropagateStep", "TTLStep", "CallbackStep"):
            assert cls in classes, \
                f"execution_module.py 应含 {cls}（继承 Step，C8 合并）"

    def test_compile_spec_and_gate_window_helpers(self):
        """execution_module 含 _compile_spec 与 _gate_window helper（C8 合并 _compile_X_spec / _gate_X）。"""
        from pathlib import Path
        src = (Path(__file__).resolve().parent.parent / "core" / "execution_module.py").read_text(encoding="utf-8")
        assert "def _compile_spec" in src, \
            "execution_module 应含 _compile_spec helper（C8 合并 3 个 _compile_X_spec）"
        assert "_gate_window" in src, \
            "execution_module 应含 _gate_window helper（C8 合并 4 个 _gate_X）"
