"""Task：native/validators 正测试。

验证 ``native/validators.py`` 中的校验器与工具：
  - ``SyntaxValidator`` / ``LogicValidator`` / ``BusinessValidator`` /
    ``SchemaValidator`` / ``ConfigIntegrityValidator`` 五类校验器存在
  - ``ValidationResult`` 单条结果项的字段
  - ``TopologyPatternMatcher.match_pattern`` 拓扑模式识别
  - ``should_fire`` 时控触发判断
  - ``_get_table`` 模块级配置表加载器
  - ``validate_configs`` 快捷函数返回完整校验报告

使用 ``config_store`` fixture 提供 ``ConfigStore`` 上下文。
"""
from __future__ import annotations

from datetime import datetime, time
from pathlib import Path
from typing import Any, List

import pytest


_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_CONFIG_DIR = _PROJECT_ROOT / "config"
_ARCH_DIR = _CONFIG_DIR / "architecture"


# ============================================================================
# SubTask：校验器类存在性与基本协议
# ============================================================================


class TestValidatorClasses:
    """五类校验器存在且可实例化。"""

    def test_validation_result_class_exists(self):
        """``ValidationResult`` 类存在且可构造。"""
        from native.validators import ValidationResult

        vr = ValidationResult("error", "test.json", "node1", "field1", "msg1", "sug1")
        assert vr.level == "error"
        assert vr.file == "test.json"
        assert vr.entry == "node1"
        assert vr.field == "field1"
        assert vr.message == "msg1"
        assert vr.suggestion == "sug1"

    def test_validation_result_to_dict(self):
        """``ValidationResult.to_dict`` 兼容旧 API（提供 error/warning/info 键）。"""
        from native.validators import ValidationResult

        err = ValidationResult("error", "f.json", "e", "fld", "emsg")
        d_err = err.to_dict()
        assert d_err["error"] == "emsg"
        assert d_err["level"] == "error"

        warn = ValidationResult("warning", "f.json", "e", "fld", "wmsg")
        assert warn.to_dict()["warning"] == "wmsg"

        info = ValidationResult("info", "f.json", "e", "fld", "imsg")
        assert info.to_dict()["info"] == "imsg"

    def test_syntax_validator_exists(self):
        """``SyntaxValidator`` 类存在且需要 ``config_dir`` 参数。"""
        from native.validators import SyntaxValidator

        v = SyntaxValidator(config_dir=_CONFIG_DIR)
        assert hasattr(v, "validate_syntax"), "SyntaxValidator 必须有 validate_syntax 方法"
        assert hasattr(v, "REQUIRED_SECTIONS"), "SyntaxValidator 应有 REQUIRED_SECTIONS 字典"

    def test_logic_validator_exists(self):
        """``LogicValidator`` 类存在。"""
        from native.validators import LogicValidator

        v = LogicValidator(config_dir=_CONFIG_DIR)
        assert hasattr(v, "validate_logic"), "LogicValidator 必须有 validate_logic 方法"

    def test_business_validator_exists(self):
        """``BusinessValidator`` 类存在（构造需要 handler_registry）。"""
        from native.validators import BusinessValidator

        v = BusinessValidator(config_dir=_CONFIG_DIR, handler_registry={})
        assert hasattr(v, "validate_business"), "BusinessValidator 必须有 validate_business 方法"


# ============================================================================
# SubTask：SyntaxValidator 实际校验行为
# ============================================================================


class TestSyntaxValidatorBehavior:
    """``SyntaxValidator.validate_syntax`` 对真实配置表的校验行为。"""

    def test_validate_syntax_passes_for_valid_table(self):
        """对 ``modules.json``（结构合法）校验，应至少返回 1 条 info。"""
        from native.validators import SyntaxValidator

        v = SyntaxValidator(config_dir=_CONFIG_DIR)
        import json

        with open(_ARCH_DIR / "modules.json", encoding="utf-8") as f:
            data = json.load(f)
        results = v.validate_syntax("modules", data)
        assert isinstance(results, list), "validate_syntax 应返回列表"
        assert len(results) >= 1, "应至少返回 1 条结果（含 info 通过提示）"
        # 无 error 级结果（modules.json 是结构合法的真实配置）
        errors = [r for r in results if r.level == "error"]
        assert len(errors) == 0, f"modules.json 校验不应有 error，实际: {[r.message for r in errors]}"

    def test_validate_syntax_reports_missing_required_field(self):
        """``behavior_actions`` 缺少必填字段应报 error。"""
        from native.validators import SyntaxValidator, ValidationResult

        v = SyntaxValidator(config_dir=_CONFIG_DIR)
        # 缺少 actions 必填字段
        results = v.validate_syntax("behavior_actions", {})
        assert len(results) >= 1, "空字典应至少返回 1 条结果"
        errors = [r for r in results if r.level == "error"]
        assert len(errors) >= 1, "缺少必填字段应至少报 1 个 error"
        assert all(isinstance(r, ValidationResult) for r in results), "结果项应为 ValidationResult"

    def test_validate_syntax_rejects_non_dict_root(self):
        """非 dict 根元素应报 error。"""
        from native.validators import SyntaxValidator

        v = SyntaxValidator(config_dir=_CONFIG_DIR)
        results = v.validate_syntax("modules", ["not", "a", "dict"])
        assert len(results) == 1, "非 dict 应立即返回单条 error"
        assert results[0].level == "error"
        assert "dict" in results[0].message


# ============================================================================
# SubTask：TopologyPatternMatcher 拓扑模式识别
# ============================================================================


class TestTopologyPatternMatcher:
    """``TopologyPatternMatcher.match_pattern`` 模式识别。"""

    @staticmethod
    def _serial_chain_nodes():
        """构造 3 节点串行链：n1(source) → n2(condition) → n3(sink)。"""
        return {
            "n1": {"type": "market_source"},
            "n2": {"type": "transfer_condition"},
            "n3": {"type": "stock_state_pool"},
        }

    @staticmethod
    def _serial_chain_edges():
        return [
            {"from": "n1", "to": "n2"},
            {"from": "n2", "to": "n3"},
        ]

    def test_match_pattern_returns_dict(self):
        """``match_pattern`` 返回 dict（含 pattern_id 字段）。"""
        from native.validators import TopologyPatternMatcher

        m = TopologyPatternMatcher()
        result = m.match_pattern(self._serial_chain_nodes(), self._serial_chain_edges())
        assert isinstance(result, dict), "match_pattern 应返回 dict"
        assert "pattern_id" in result, "结果应包含 pattern_id"
        assert "execution_strategy" in result, "结果应包含 execution_strategy"
        assert "cache_policy" in result, "结果应包含 cache_policy"

    def test_match_pattern_empty_returns_fallback(self):
        """空节点/边返回 fallback（pattern_id=unknown）。"""
        from native.validators import TopologyPatternMatcher

        m = TopologyPatternMatcher()
        result = m.match_pattern({}, [])
        assert result["pattern_id"] == "unknown", f"空图应回退 unknown，实际 {result['pattern_id']}"

    def test_match_pattern_loop_feedback_detected(self):
        """含环拓扑应匹配 loop_feedback 模式。"""
        from native.validators import TopologyPatternMatcher

        # n1 → n2 → n3 → n1 形成环
        nodes = {
            "n1": {"type": "stock_state_pool"},
            "n2": {"type": "transfer_condition"},
            "n3": {"type": "stock_state_pool"},
        }
        edges = [
            {"from": "n1", "to": "n2"},
            {"from": "n2", "to": "n3"},
            {"from": "n3", "to": "n1"},
        ]
        m = TopologyPatternMatcher()
        result = m.match_pattern(nodes, edges)
        assert result["pattern_id"] == "loop_feedback", (
            f"含环拓扑应匹配 loop_feedback，实际 {result['pattern_id']}"
        )


# ============================================================================
# SubTask：should_fire 时控触发判断
# ============================================================================


class TestShouldFire:
    """``should_fire`` 判断 Flow/Edge 是否在当前时间触发。"""

    def test_should_fire_no_time_constraint_returns_true(self):
        """无 begin/end/interval 约束时应触发。"""
        from native.validators import should_fire

        now = datetime(2024, 1, 1, 10, 0, 0)
        edge = {"params": {}}
        assert should_fire(edge, now) is True, "无时控参数应触发"

    def test_should_fire_within_begin_end_window(self):
        """在 begin..end HHMMSS 窗口内应触发（begin_mode=7 / end_mode=3）。"""
        from native.validators import should_fire

        now = datetime(2024, 1, 1, 10, 0, 0)
        # begin_mode=7：HHMMSS 解析，now_sec >= 093000 才触发
        # end_mode=3：HHMMSS 解析，now_sec > 150000 不触发
        edge = {
            "params": {
                "begin": 7,        # begin_mode=7 → HHMMSS 解析
                "begint": "093000",  # 09:30:00
                "end": 3,            # end_mode=3 → HHMMSS 解析
                "endt": "150000",    # 15:00:00
            }
        }
        assert should_fire(edge, now) is True, "10:00 在 09:30~15:00 窗口内应触发"

    def test_should_fire_outside_window_returns_false(self):
        """在 begin..end HHMMSS 窗口外不应触发（begin_mode=7 解析 HHMMSS）。"""
        from native.validators import should_fire

        now = datetime(2024, 1, 1, 8, 0, 0)  # 08:00 早于 09:30
        # begin_mode=7：将 begint 作为 HHMMSS 时间点解析，now_sec >= target_sec 才触发
        # end_mode=3：将 endt 作为 HHMMSS 时间点解析，now_sec > target_sec 不触发
        edge = {
            "params": {
                "begin": 7,        # begin_mode=7 → HHMMSS 解析
                "begint": "093000",  # 09:30:00
                "end": 3,            # end_mode=3 → HHMMSS 解析
                "endt": "150000",    # 15:00:00
            }
        }
        assert should_fire(edge, now) is False, "08:00 在 09:30~15:00 窗口外不应触发"


# ============================================================================
# SubTask：_get_table 模块级配置表加载器
# ============================================================================


class TestGetTableHelper:
    """``_get_table(filename)`` 通过 ConfigStore 加载配置表。"""

    def test_get_table_returns_dict_or_none(self, config_store):
        """``_get_table`` 加载已存在的表返回 dict（或 None 表示空表）。"""
        from native.validators import _get_table

        # modules.json 是真实存在的配置表
        result = _get_table("modules.json")
        assert result is None or isinstance(result, dict), (
            f"_get_table 应返回 dict 或 None，实际 {type(result)}"
        )

    def test_get_table_strips_json_suffix(self, config_store):
        """``_get_table`` 自动剥离 ``.json`` 后缀。"""
        from native.validators import _get_table

        a = _get_table("modules")
        b = _get_table("modules.json")
        # 两种调用应返回等价结果（自动剥离后缀）
        assert a == b, "_get_table 应自动剥离 .json 后缀"


# ============================================================================
# SubTask：validate_configs 快捷函数
# ============================================================================


class TestValidateConfigs:
    """``validate_configs`` 返回完整校验报告 dict。"""

    def test_validate_configs_returns_dict(self):
        """``validate_configs`` 返回 dict，含 valid/errors/warnings 等字段。"""
        from native.validators import validate_configs

        report = validate_configs(str(_CONFIG_DIR))
        assert isinstance(report, dict), "validate_configs 应返回 dict"
        assert "valid" in report, "报告应含 valid 字段"
        assert "errors" in report, "报告应含 errors 字段"
        assert "warnings" in report, "报告应含 warnings 字段"
        assert "error_count" in report, "报告应含 error_count 字段"
        assert "warning_count" in report, "报告应含 warning_count 字段"
        assert "stats" in report, "报告应含 stats 字段"

    def test_validate_configs_valid_is_bool(self):
        """``report['valid']`` 是 bool。"""
        from native.validators import validate_configs

        report = validate_configs(str(_CONFIG_DIR))
        assert isinstance(report["valid"], bool), "valid 应为 bool"
        assert isinstance(report["errors"], list), "errors 应为 list"
        assert isinstance(report["warnings"], list), "warnings 应为 list"

    def test_config_integrity_validator_exists(self):
        """``ConfigIntegrityValidator`` 类存在并可实例化。"""
        from native.validators import ConfigIntegrityValidator

        v = ConfigIntegrityValidator(config_dir=str(_CONFIG_DIR))
        assert hasattr(v, "validate_all"), "ConfigIntegrityValidator 必须有 validate_all 方法"
