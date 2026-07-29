"""Task：native/builtins 表驱动动作流水线正测试。

验证 ``native/builtins.py`` 中的表驱动 action pipeline：
  - ``condition_dispatcher`` 使用 dict 查找（无 if/elif 链）
  - 7 个 step 函数（``_step_resolve`` ~ ``_step_remove``）存在
  - ``_execute_action`` 按 action 名分派
  - ``_make_action`` 创建可调用 action
  - 16 个 action 库函数存在
  - TDX 函数（``tdx_condition_evaluator`` / ``edge_default_transfer``）存在
  - ``init_market_source`` / ``init_stock_state_pool`` / ``init_tdx_candidate`` 存在
  - ``tdx_convert_from_file`` / ``tdx_convert_from_pool`` 存在
  - 评分函数（``_ra_score_weighted_sort`` 等）存在
  - ``condition_dispatcher`` 是表驱动单一入口（``_HANDLERS`` 注册表）

使用 ``fz_stocks`` fixture 提供 fz 前缀股票代码。
"""
from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any, Callable, List

import pytest


_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_SOURCE_TEXT = (_PROJECT_ROOT / "native" / "builtins.py").read_text(encoding="utf-8")


# ============================================================================
# SubTask：condition_dispatcher 表驱动
# ============================================================================


class TestConditionDispatcherTableDriven:
    """``condition_dispatcher`` 使用 dict 查找（无 if/elif 链）。"""

    def test_condition_dispatcher_exists_and_callable(self):
        """``condition_dispatcher`` 是模块级可调用函数。"""
        from native import builtins

        assert callable(builtins.condition_dispatcher), "condition_dispatcher 必须可调用"

    def test_condition_dispatcher_uses_dict_lookup(self):
        """``condition_dispatcher`` 函数体应包含 ``di.items()`` 遍历（表驱动查找）。"""
        # 从源码中提取 condition_dispatcher 函数体
        src = _SOURCE_TEXT
        start = src.index("def condition_dispatcher(inputs):")
        # 截取到下一个顶层 def（以 ``\ndef `` 或 ``\n# `` 分隔）
        end = src.index("\ndef ", start + 1)
        body = src[start:end]
        assert "di.items()" in body, "condition_dispatcher 应使用 di.items() 进行表驱动查找"
        # 不应包含 if/elif cond == 风格的硬编码分派
        assert "elif cond ==" not in body, "condition_dispatcher 不应使用 elif cond == 硬编码"
        assert "if cond ==" not in body, "condition_dispatcher 不应使用 if cond == 硬编码"

    def test_handlers_registry_contains_dispatcher(self):
        """``_HANDLERS`` 注册表包含 ``condition_dispatcher``。"""
        from native import builtins

        assert "condition_dispatcher" in builtins._HANDLERS, (
            "_HANDLERS 应注册 condition_dispatcher"
        )

    def test_step_funcs_registry_has_seven_steps(self):
        """``_STEP_FUNCS`` 注册表恰好包含 7 个 step（resolve ~ remove）。"""
        from native import builtins

        expected = {"resolve", "pass", "filter", "dzh_filter",
                    "propagate", "transfer", "remove"}
        assert set(builtins._STEP_FUNCS.keys()) == expected, (
            f"_STEP_FUNCS 应包含 7 个 step，实际: {set(builtins._STEP_FUNCS.keys())}"
        )


# ============================================================================
# SubTask：7 个 step 函数存在
# ============================================================================


class TestSevenStepFunctions:
    """7 个 step 函数（``_step_resolve`` ~ ``_step_remove``）存在且可调用。"""

    def test_step_resolve_callable(self):
        """``_step_resolve`` 存在且可调用。"""
        from native import builtins

        assert callable(builtins._step_resolve), "_step_resolve 必须可调用"

    def test_step_pass_callable(self):
        """``_step_pass`` 存在且可调用。"""
        from native import builtins

        assert callable(builtins._step_pass), "_step_pass 必须可调用"

    def test_step_filter_callable(self):
        """``_step_filter`` 存在且可调用。"""
        from native import builtins

        assert callable(builtins._step_filter), "_step_filter 必须可调用"

    def test_step_dzh_filter_callable(self):
        """``_step_dzh_filter`` 存在且可调用。"""
        from native import builtins

        assert callable(builtins._step_dzh_filter), "_step_dzh_filter 必须可调用"

    def test_step_propagate_callable(self):
        """``_step_propagate`` 存在且可调用。"""
        from native import builtins

        assert callable(builtins._step_propagate), "_step_propagate 必须可调用"

    def test_step_transfer_callable(self):
        """``_step_transfer`` 存在且可调用。"""
        from native import builtins

        assert callable(builtins._step_transfer), "_step_transfer 必须可调用"

    def test_step_remove_callable(self):
        """``_step_remove`` 存在且可调用。"""
        from native import builtins

        assert callable(builtins._step_remove), "_step_remove 必须可调用"


# ============================================================================
# SubTask：_execute_action / _make_action
# ============================================================================


class TestActionExecutor:
    """``_execute_action`` 按 action 名分派，``_make_action`` 创建可调用 action。"""

    def test_make_action_returns_callable(self):
        """``_make_action`` 返回可调用对象。"""
        from native import builtins

        action = builtins._make_action("resolve_and_pass")
        assert callable(action), "_make_action 应返回可调用对象"

    def test_make_action_dispatches_by_name(self):
        """``_make_action`` 不同 action 名返回不同绑定（共享 _execute_action）。"""
        from native import builtins

        a1 = builtins._make_action("resolve_and_pass")
        a2 = builtins._make_action("apply_filter")
        # 都是 callable，且不互为同一对象
        assert callable(a1) and callable(a2)
        assert a1 is not a2, "不同 action 名应返回不同绑定对象"

    def test_execute_action_unknown_name_returns_inputs(self):
        """``_execute_action`` 对未知 action 名直接返回 inputs（steps 为空）。"""
        from native import builtins

        inputs = {"node_stocks": {}, "nodes": {}, "sid": "n1", "tid": "n2"}
        # 未知 action 名 → steps 列表为空 → 直接返回 inputs
        result = builtins._execute_action("nonexistent_action", inputs)
        assert result is inputs, "未知 action 名应原样返回 inputs"


# ============================================================================
# SubTask：16 个 action 库函数
# ============================================================================


class TestActionLibrary:
    """action 库包含 16 个动作函数（render_label ~ discard_stocks）。"""

    EXPECTED_ACTIONS = [
        "render_label", "render_shape", "stock_pool_hold",
        "transfer_condition_check", "resolve_market", "discard_sink_drop",
        "time_trigger_check", "profit_analysis_calc", "formula_eval",
        "sector_filter", "cross_section_eval", "basic_filter", "pass_through",
        "candidate_resolve", "accumulate_state", "discard_stocks",
    ]

    def test_all_16_actions_exist(self):
        """16 个 action 函数均存在且可调用。"""
        from native import builtins

        missing = [n for n in self.EXPECTED_ACTIONS if not hasattr(builtins, n)]
        assert not missing, f"缺少 action 函数: {missing}"

    def test_all_16_actions_callable(self):
        """16 个 action 函数均可调用。"""
        from native import builtins

        non_callable = [n for n in self.EXPECTED_ACTIONS if not callable(getattr(builtins, n))]
        assert not non_callable, f"以下 action 不可调用: {non_callable}"

    def test_all_16_actions_in_handlers_registry(self):
        """16 个 action 函数全部注册在 ``_HANDLERS`` 表中。"""
        from native import builtins

        missing = [n for n in self.EXPECTED_ACTIONS if n not in builtins._HANDLERS]
        assert not missing, f"以下 action 未注册到 _HANDLERS: {missing}"

    def test_action_count_at_least_16(self):
        """``_HANDLERS`` 注册的 action 总数 ≥ 16。"""
        from native import builtins

        action_names = {n for n in self.EXPECTED_ACTIONS if n in builtins._HANDLERS}
        assert len(action_names) >= 16, (
            f"_HANDLERS 中 action 数应 ≥ 16，实际匹配 {len(action_names)}"
        )


# ============================================================================
# SubTask：TDX 函数存在
# ============================================================================


class TestTdxFunctions:
    """``tdx_condition_evaluator`` / ``edge_default_transfer`` 等 TDX 函数存在。"""

    def test_tdx_condition_evaluator_exists(self):
        """``tdx_condition_evaluator`` 存在且可调用。"""
        from native import builtins

        assert callable(builtins.tdx_condition_evaluator), (
            "tdx_condition_evaluator 必须可调用"
        )

    def test_edge_default_transfer_exists(self):
        """``edge_default_transfer`` 存在且可调用。"""
        from native import builtins

        assert callable(builtins.edge_default_transfer), (
            "edge_default_transfer 必须可调用"
        )

    def test_init_market_source_exists(self):
        """``init_market_source`` 存在且可调用。"""
        from native import builtins

        assert callable(builtins.init_market_source), "init_market_source 必须可调用"

    def test_init_stock_state_pool_exists(self):
        """``init_stock_state_pool`` 存在且可调用。"""
        from native import builtins

        assert callable(builtins.init_stock_state_pool), "init_stock_state_pool 必须可调用"

    def test_init_tdx_candidate_exists(self):
        """``init_tdx_candidate`` 存在且可调用。"""
        from native import builtins

        assert callable(builtins.init_tdx_candidate), "init_tdx_candidate 必须可调用"


# ============================================================================
# SubTask：tdx_convert_from_file / tdx_convert_from_pool
# ============================================================================


class TestTdxConvertFunctions:
    """``tdx_convert_from_file`` / ``tdx_convert_from_pool`` 存在。"""

    def test_tdx_convert_from_file_exists(self):
        """``tdx_convert_from_file`` 存在且可调用。"""
        from native import builtins

        assert callable(builtins.tdx_convert_from_file), (
            "tdx_convert_from_file 必须可调用"
        )

    def test_tdx_convert_from_pool_exists(self):
        """``tdx_convert_from_pool`` 存在且可调用。"""
        from native import builtins

        assert callable(builtins.tdx_convert_from_pool), (
            "tdx_convert_from_pool 必须可调用"
        )


# ============================================================================
# SubTask：评分函数存在
# ============================================================================


class TestScoringFunctions:
    """``_ra_score_weighted_sort`` / ``_ra_score_per_group_sort`` / ``_ra_aggregate_panels``。"""

    def test_score_weighted_sort_exists(self):
        """``_ra_score_weighted_sort`` 存在且可调用。"""
        from native import builtins

        assert callable(builtins._ra_score_weighted_sort), (
            "_ra_score_weighted_sort 必须可调用"
        )

    def test_score_per_group_sort_exists(self):
        """``_ra_score_per_group_sort`` 存在且可调用。"""
        from native import builtins

        assert callable(builtins._ra_score_per_group_sort), (
            "_ra_score_per_group_sort 必须可调用"
        )

    def test_aggregate_panels_exists(self):
        """``_ra_aggregate_panels`` 存在且可调用。"""
        from native import builtins

        assert callable(builtins._ra_aggregate_panels), (
            "_ra_aggregate_panels 必须可调用"
        )

    def test_scoring_funcs_registered_in_result_action_funcs(self):
        """3 个评分函数注册在 ``_RESULT_ACTION_FUNCS`` 中。"""
        from native import builtins

        assert "score_weighted_sort" in builtins._RESULT_ACTION_FUNCS
        assert "score_per_group_sort" in builtins._RESULT_ACTION_FUNCS
        assert "aggregate_panels" in builtins._RESULT_ACTION_FUNCS
