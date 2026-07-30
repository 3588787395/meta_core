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


# ============================================================================
# 变更 B：_PNL_METRIC_SPECS 表 + _compute_pnl_metric 合并 5 个 _compute_xxx_pnl 同构方法
# ============================================================================


class TestChangeBPnlMetricSpecs:
    """变更 B：_PNL_METRIC_SPECS 表含 5 条 spec + _compute_pnl_metric 统一入口。"""

    def test_pnl_metric_specs_table_exists(self):
        """_PNL_METRIC_SPECS 表存在。"""
        from core.monitoring_module import _PNL_METRIC_SPECS
        assert isinstance(_PNL_METRIC_SPECS, dict), \
            "_PNL_METRIC_SPECS 应为 dict"

    def test_pnl_metric_specs_has_five_entries(self):
        """_PNL_METRIC_SPECS 表含 5 条 spec。"""
        from core.monitoring_module import _PNL_METRIC_SPECS
        expected_keys = {
            "intraday", "market_impact", "historical", "distribution", "positioning",
        }
        assert set(_PNL_METRIC_SPECS.keys()) >= expected_keys, \
            f"_PNL_METRIC_SPECS 应含 5 条 spec，实际 {set(_PNL_METRIC_SPECS.keys())}"
        assert len(_PNL_METRIC_SPECS) >= 5, \
            f"_PNL_METRIC_SPECS 应 ≥5 条，实际 {len(_PNL_METRIC_SPECS)}"

    def test_pnl_metric_specs_each_has_filter_extract_agg_key(self):
        """每条 spec 含 filter/extract/agg/key 四字段。"""
        from core.monitoring_module import _PNL_METRIC_SPECS
        for name, spec in _PNL_METRIC_SPECS.items():
            assert isinstance(spec, dict), f"spec {name} 应为 dict"
            assert "filter" in spec, f"spec {name} 应含 filter"
            assert "extract" in spec, f"spec {name} 应含 extract"
            assert "agg" in spec, f"spec {name} 应含 agg"
            assert "key" in spec, f"spec {name} 应含 key"

    def test_compute_pnl_metric_method_exists(self):
        """_compute_pnl_metric 方法存在（统一入口，分派 _PNL_METRIC_SPECS）。"""
        from core.monitoring_module import StatisticsModule
        assert hasattr(StatisticsModule, "_compute_pnl_metric"), \
            "StatisticsModule 应含 _compute_pnl_metric 方法（变更 B 合并入口）"
        assert callable(getattr(StatisticsModule, "_compute_pnl_metric")), \
            "_compute_pnl_metric 应为可调用方法"

    def test_no_legacy_compute_xxx_pnl_methods(self):
        """Grep 验证：monitoring_module 5 个旧 _compute_xxx_pnl 方法 = 0。"""
        import re
        from pathlib import Path
        mon_path = Path(__file__).resolve().parent.parent / "core" / "monitoring_module.py"
        src = mon_path.read_text(encoding="utf-8")
        legacy_pattern = (
            r"def _compute_intraday_pnl|def _compute_market_impact_pnl|"
            r"def _compute_historical_pnl|def _compute_distribution_pnl|"
            r"def _compute_positioning_pnl"
        )
        matches = re.findall(legacy_pattern, src)
        assert len(matches) == 0, (
            f"monitoring_module 不应含旧 _compute_xxx_pnl 方法（变更 B 已合并），"
            f"实际 {matches}"
        )

    def test_no_generic_compute_word_pnl_pattern(self):
        """Grep 验证：monitoring_module 中 def _compute_\\w+_pnl 通用模式 = 0。"""
        import re
        from pathlib import Path
        mon_path = Path(__file__).resolve().parent.parent / "core" / "monitoring_module.py"
        src = mon_path.read_text(encoding="utf-8")
        matches = re.findall(r"def _compute_\w+_pnl", src)
        assert len(matches) == 0, (
            f"monitoring_module 不应含 _compute_*_pnl 同构方法模式（变更 B），"
            f"实际 {matches}"
        )


# ============================================================================
# 变更 L：_ANGLE_SORT_KEYS lambda dict 合并 3 个 _xxx_key 排序键方法
# ============================================================================


class TestChangeLAngleSortKeys:
    """变更 L：_ANGLE_SORT_KEYS lambda dict 含 momentum/trend/value，消除 3 个 _xxx_key 方法。"""

    def test_angle_sort_keys_table_exists(self):
        """_ANGLE_SORT_KEYS 表存在。"""
        from core.monitoring_module import _ANGLE_SORT_KEYS
        assert isinstance(_ANGLE_SORT_KEYS, dict), \
            "_ANGLE_SORT_KEYS 应为 dict"

    def test_angle_sort_keys_has_three_angles(self):
        """_ANGLE_SORT_KEYS 表含 momentum/trend/value 三键。"""
        from core.monitoring_module import _ANGLE_SORT_KEYS
        assert "momentum" in _ANGLE_SORT_KEYS, "_ANGLE_SORT_KEYS 应含 momentum"
        assert "trend" in _ANGLE_SORT_KEYS, "_ANGLE_SORT_KEYS 应含 trend"
        assert "value" in _ANGLE_SORT_KEYS, "_ANGLE_SORT_KEYS 应含 value"

    def test_angle_sort_keys_values_are_callables(self):
        """_ANGLE_SORT_KEYS 每个键映射到可调用 lambda 排序键。"""
        from core.monitoring_module import _ANGLE_SORT_KEYS
        for angle_name in ("momentum", "trend", "value"):
            assert callable(_ANGLE_SORT_KEYS[angle_name]), \
                f"_ANGLE_SORT_KEYS[{angle_name}] 应为可调用 lambda 排序键"

    def test_no_legacy_xxx_key_methods(self):
        """Grep 验证：monitoring_module 3 个旧 _xxx_key 排序键方法 = 0。"""
        import re
        from pathlib import Path
        mon_path = Path(__file__).resolve().parent.parent / "core" / "monitoring_module.py"
        src = mon_path.read_text(encoding="utf-8")
        legacy_pattern = r"def _momentum_key|def _trend_key|def _value_key"
        matches = re.findall(legacy_pattern, src)
        assert len(matches) == 0, (
            f"monitoring_module 不应含旧 _xxx_key 排序键方法（变更 L 已合并），"
            f"实际 {matches}"
        )

    def test_angle_sort_keys_used_in_module(self):
        """monitoring_module 引用 _ANGLE_SORT_KEYS 作为排序键源。"""
        import re
        from pathlib import Path
        mon_path = Path(__file__).resolve().parent.parent / "core" / "monitoring_module.py"
        src = mon_path.read_text(encoding="utf-8")
        assert "_ANGLE_SORT_KEYS" in src, \
            "monitoring_module 应引用 _ANGLE_SORT_KEYS 表（变更 L 表驱动）"


# === Task 28.6 回归断言：converge-meta-essence-v4 阶段 1 P4 + 阶段 3 C4 收敛状态 ===


class TestConvergenceRegressionV4:
    """SubTask 28.6：converge-meta-essence-v4 P4/C4 safe_cast 公共工具下沉收敛回归。"""

    def test_converters_common_module_present(self):
        """converters_common.py 模块存在（P4/C4 公共工具下沉模块）。"""
        from pathlib import Path
        common_path = Path(__file__).resolve().parent.parent / "converters_common.py"
        assert common_path.is_file(), \
            "converters_common.py 应存在（P4/C4 公共工具下沉模块）"

    def test_safe_cast_int_float_defined(self):
        """converters_common.py 定义 safe_cast / safe_int / safe_float（C4 跨模块统一）。"""
        from pathlib import Path
        src = (Path(__file__).resolve().parent.parent / "converters_common.py").read_text(encoding="utf-8")
        assert "def safe_cast" in src, "converters_common.py 应定义 safe_cast（C4 跨模块统一）"
        assert "def safe_int" in src, "converters_common.py 应定义 safe_int"
        assert "def safe_float" in src, "converters_common.py 应定义 safe_float"

    def test_no_safe_int_float_in_core(self):
        """core/*.py 不含 def _safe_int / def _safe_float（C4 已下沉到 converters_common）。"""
        import re
        from pathlib import Path
        core_dir = Path(__file__).resolve().parent.parent / "core"
        total = 0
        for py in core_dir.glob("*.py"):
            try:
                src = py.read_text(encoding="utf-8")
            except OSError:
                continue
            total += len(re.findall(r"def _safe_int\b|def _safe_float\b", src))
        assert total == 0, \
            f"core/*.py 不应含 def _safe_int/_safe_float（C4 已下沉），实际 {total} 处"
