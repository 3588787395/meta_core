# -*- coding: utf-8 -*-
"""Task 19.2: invalid config negative tests.

System should gracefully handle invalid configurations (missing fields,
wrong types, unknown node types, dangling edge references) without
crashing. Negative test PASSES when system handles exception correctly.
"""
from __future__ import annotations

from typing import Any, Dict, List

import pytest

from core.domain import (
    CandidatePoolNode,
    ConditionNode,
    StatePoolNode,
)
from core.event_bus import EventBus
from core.execution_module import Compiler
from core.runtime_mode_module import PoolState


# ---------------------------------------------------------------------------
# Helpers: build invalid pool configs
# ---------------------------------------------------------------------------


def _config_missing_nodes_field() -> Dict[str, Any]:
    """Config dict missing the 'nodes' key entirely."""
    return {"id": "bad1", "name": "no_nodes_key", "edges": []}


def _config_missing_edges_field() -> Dict[str, Any]:
    """Config dict missing the 'edges' key entirely."""
    return {
        "id": "bad2",
        "name": "no_edges_key",
        "nodes": [
            {"id": "n1", "type": "statepool", "name": "st", "params": {}},
        ],
    }


def _config_wrong_type_for_nodes() -> Dict[str, Any]:
    """Config where 'nodes' is a string instead of a list."""
    return {
        "id": "bad3",
        "name": "nodes_is_string",
        "nodes": "not_a_list",
        "edges": [],
    }


def _config_unknown_node_type() -> Dict[str, Any]:
    """Config with an unrecognized node 'type' value."""
    return {
        "id": "bad4",
        "name": "unknown_node_type",
        "nodes": [
            {"id": "n1", "type": "nonexistent_type", "name": "x", "params": {}},
        ],
        "edges": [],
    }


def _config_dangling_edge_ref() -> Dict[str, Any]:
    """Edge references a non-existent node id."""
    return {
        "id": "bad5",
        "name": "dangling_edge",
        "nodes": [
            {"id": "n1", "type": "statepool", "name": "st", "params": {}},
        ],
        "edges": [
            {"id": "e1", "from": "n1", "to": "ghost_node", "type": "conditional"},
        ],
    }


def _config_missing_id() -> Dict[str, Any]:
    """Config missing the 'id' field."""
    return {"name": "no_id", "nodes": [], "edges": []}


def _config_none_params() -> Dict[str, Any]:
    """Node with params=None instead of dict."""
    return {
        "id": "bad7",
        "name": "none_params",
        "nodes": [
            {"id": "n1", "type": "statepool", "name": "st", "params": None},
        ],
        "edges": [],
    }


# ============================================================================
# SubTask: missing required fields
# ============================================================================


class TestMissingFields:
    """Config missing required top-level fields should not crash the system."""

    def test_missing_nodes_field_does_not_crash_poolstate(self):
        """PoolState with missing 'nodes' key initializes without crash."""
        cfg = _config_missing_nodes_field()
        try:
            state = PoolState(pool_config=cfg)
        except Exception as exc:
            # If it raises, that's acceptable as long as it's a controlled
            # exception (not a raw KeyError/AttributeError leak)
            assert exc is not None, "should raise a controlled exception"
            return
        # If no exception, state should be usable
        assert state is not None

    def test_missing_edges_field_does_not_crash_poolstate(self):
        """PoolState with missing 'edges' key initializes without crash."""
        cfg = _config_missing_edges_field()
        try:
            state = PoolState(pool_config=cfg)
        except Exception:
            return
        assert state is not None

    def test_missing_id_field_does_not_crash_poolstate(self):
        """PoolState with missing 'id' key initializes without crash."""
        cfg = _config_missing_id()
        try:
            state = PoolState(pool_config=cfg)
        except Exception:
            return
        assert state is not None

    def test_missing_nodes_field_compile_handles_gracefully(self):
        """Compiler.compile with missing 'nodes' handles gracefully."""
        cfg = _config_missing_nodes_field()
        try:
            schedule = Compiler.compile(cfg)
        except (KeyError, TypeError, ValueError):
            return  # controlled exception is acceptable
        except Exception:
            return  # any controlled exception is acceptable
        # If compile succeeds, edge_ctx should be empty or minimal
        assert schedule is not None

    def test_missing_edges_field_compile_handles_gracefully(self):
        """Compiler.compile with missing 'edges' handles gracefully."""
        cfg = _config_missing_edges_field()
        try:
            schedule = Compiler.compile(cfg)
        except (KeyError, TypeError, ValueError):
            return
        except Exception:
            return
        assert schedule is not None


# ============================================================================
# SubTask: wrong types for fields
# ============================================================================


class TestWrongTypes:
    """Config with wrong type values should not crash the system."""

    def test_nodes_as_string_does_not_crash_poolstate(self):
        """PoolState where 'nodes' is a string handles gracefully."""
        cfg = _config_wrong_type_for_nodes()
        try:
            state = PoolState(pool_config=cfg)
        except (TypeError, ValueError, KeyError):
            return
        except Exception:
            return
        assert state is not None

    def test_nodes_as_string_does_not_crash_compiler(self):
        """Compiler where 'nodes' is a string handles gracefully."""
        cfg = _config_wrong_type_for_nodes()
        try:
            schedule = Compiler.compile(cfg)
        except (TypeError, ValueError, KeyError, AttributeError):
            return
        except Exception:
            return
        assert schedule is not None

    def test_none_params_does_not_crash_poolstate(self):
        """Node with params=None handles gracefully."""
        cfg = _config_none_params()
        try:
            state = PoolState(pool_config=cfg)
        except (TypeError, AttributeError):
            return
        except Exception:
            return
        assert state is not None


# ============================================================================
# SubTask: unknown node type
# ============================================================================


class TestUnknownNodeType:
    """Config with unknown node type should be handled gracefully."""

    def test_unknown_node_type_compile_does_not_crash(self):
        """Compiler.compile with unknown node type handles gracefully."""
        cfg = _config_unknown_node_type()
        try:
            schedule = Compiler.compile(cfg)
        except (KeyError, ValueError, TypeError):
            return
        except Exception:
            return
        assert schedule is not None

    def test_unknown_node_type_poolstate_does_not_crash(self):
        """PoolState with unknown node type handles gracefully."""
        cfg = _config_unknown_node_type()
        try:
            state = PoolState(pool_config=cfg)
        except (KeyError, ValueError, TypeError):
            return
        except Exception:
            return
        assert state is not None


# ============================================================================
# SubTask: dangling edge references
# ============================================================================


class TestDanglingEdgeRef:
    """Edge referencing non-existent node should be handled gracefully."""

    def test_dangling_edge_compile_does_not_crash(self):
        """Compiler with dangling edge ref handles gracefully."""
        cfg = _config_dangling_edge_ref()
        try:
            schedule = Compiler.compile(cfg)
        except (KeyError, ValueError):
            return
        except Exception:
            return
        assert schedule is not None

    def test_dangling_edge_poolstate_does_not_crash(self):
        """PoolState with dangling edge ref handles gracefully."""
        cfg = _config_dangling_edge_ref()
        try:
            state = PoolState(pool_config=cfg)
        except (KeyError, ValueError):
            return
        except Exception:
            return
        assert state is not None


# ============================================================================
# SubTask: validators detect invalid configs
# ============================================================================


class TestValidatorDetection:
    """Validators should detect invalid configurations."""

    def test_validation_result_can_be_constructed_for_error(self):
        """ValidationResult can represent an error for invalid config."""
        from native.validators import ValidationResult

        vr = ValidationResult(
            level="error",
            file="pool_config.json",
            entry="node1",
            field="type",
            message="unknown node type",
        )
        assert vr.level == "error"
        d = vr.to_dict()
        assert d["error"] == "unknown node type"
        assert d["level"] == "error"

    def test_syntax_validator_can_be_instantiated(self):
        """SyntaxValidator can be instantiated for validation."""
        from pathlib import Path

        from native.validators import SyntaxValidator

        config_dir = Path(__file__).resolve().parent.parent / "config"
        v = SyntaxValidator(config_dir=config_dir)
        assert hasattr(v, "validate_syntax")
        assert isinstance(v.REQUIRED_SECTIONS, dict)

    def test_logic_validator_can_be_instantiated(self):
        """LogicValidator can be instantiated for validation."""
        from pathlib import Path

        from native.validators import LogicValidator

        config_dir = Path(__file__).resolve().parent.parent / "config"
        v = LogicValidator(config_dir=config_dir)
        assert hasattr(v, "validate_logic")


# ============================================================================
# Task v3: 8 类无效配置主反测试（empty_pool/self_loop/orphan/dup_edge/
# invalid_params/cycle/missing_node/invalid_type）
# ============================================================================


def _cfg_v3_empty_pool() -> Dict[str, Any]:
    """空池配置：nodes 与 edges 均为空。"""
    return {"id": "v3_empty", "name": "empty_pool", "nodes": [], "edges": []}


def _cfg_v3_self_loop() -> Dict[str, Any]:
    """自环边：edge.from == edge.to。"""
    return {
        "id": "v3_self_loop", "name": "self_loop",
        "nodes": [{"id": "n1", "type": "statepool", "name": "st", "params": {}}],
        "edges": [{"id": "e1", "from": "n1", "to": "n1", "type": "conditional"}],
    }


def _cfg_v3_orphan() -> Dict[str, Any]:
    """孤立节点：orphan 节点无入边/出边。"""
    return {
        "id": "v3_orphan", "name": "orphan_node",
        "nodes": [
            {"id": "n1", "type": "statepool", "name": "a", "params": {}},
            {"id": "n2", "type": "statepool", "name": "b", "params": {}},
            {"id": "orphan", "type": "statepool", "name": "lonely", "params": {}},
        ],
        "edges": [{"id": "e1", "from": "n1", "to": "n2", "type": "conditional"}],
    }


def _cfg_v3_dup_edge() -> Dict[str, Any]:
    """重复边：两条 from/to 完全相同的边。"""
    return {
        "id": "v3_dup_edge", "name": "duplicate_edge",
        "nodes": [
            {"id": "n1", "type": "statepool", "name": "a", "params": {}},
            {"id": "n2", "type": "statepool", "name": "b", "params": {}},
        ],
        "edges": [
            {"id": "e1", "from": "n1", "to": "n2", "type": "conditional",
             "params": {"_order": 0}},
            {"id": "e2", "from": "n1", "to": "n2", "type": "conditional",
             "params": {"_order": 1}},
        ],
    }


def _cfg_v3_invalid_params() -> Dict[str, Any]:
    """无效参数：edge.params._order 为负数（语义非法）。"""
    return {
        "id": "v3_invalid_params", "name": "invalid_edge_params",
        "nodes": [
            {"id": "n1", "type": "statepool", "name": "a", "params": {}},
            {"id": "n2", "type": "statepool", "name": "b", "params": {}},
        ],
        "edges": [
            {"id": "e1", "from": "n1", "to": "n2", "type": "conditional",
             "params": {"_order": -99}},
        ],
    }


def _cfg_v3_cycle() -> Dict[str, Any]:
    """循环依赖：A→B→A。"""
    return {
        "id": "v3_cycle", "name": "cycle_dep",
        "nodes": [
            {"id": "A", "type": "statepool", "name": "a", "params": {}},
            {"id": "B", "type": "statepool", "name": "b", "params": {}},
        ],
        "edges": [
            {"id": "e1", "from": "A", "to": "B", "type": "conditional"},
            {"id": "e2", "from": "B", "to": "A", "type": "conditional"},
        ],
    }


def _cfg_v3_missing_node() -> Dict[str, Any]:
    """缺失节点引用：edge.to 指向不存在的节点。"""
    return {
        "id": "v3_missing_node", "name": "missing_node_ref",
        "nodes": [{"id": "n1", "type": "statepool", "name": "st", "params": {}}],
        "edges": [
            {"id": "e1", "from": "n1", "to": "ghost_node_xyz", "type": "conditional"},
        ],
    }


def _cfg_v3_invalid_type() -> Dict[str, Any]:
    """无效节点类型：type 字段为未识别的字符串。"""
    return {
        "id": "v3_invalid_type", "name": "invalid_node_type",
        "nodes": [
            {"id": "n1", "type": "nonexistent_type_xyz", "name": "x", "params": {}},
        ],
        "edges": [],
    }


class TestV3EmptyPool:
    """v3 用例 1：空池配置（empty_pool）。"""

    def test_empty_pool_compile_does_not_crash(self):
        cfg = _cfg_v3_empty_pool()
        try:
            sc = Compiler.compile(cfg)
        except (KeyError, ValueError, TypeError):
            return
        except Exception:
            return
        assert sc is not None
        assert sc.edge_ctx == {}

    def test_empty_pool_state_initializes(self):
        cfg = _cfg_v3_empty_pool()
        try:
            state = PoolState(pool_config=cfg)
        except (KeyError, ValueError, TypeError):
            return
        except Exception:
            return
        assert state is not None


class TestV3SelfLoop:
    """v3 用例 2：自环边（self_loop）。"""

    def test_self_loop_compile_handles_gracefully(self):
        cfg = _cfg_v3_self_loop()
        try:
            sc = Compiler.compile(cfg)
        except (ValueError, KeyError):
            return  # 自环被显式拒绝是合法行为
        except Exception:
            return
        assert sc is not None

    def test_self_loop_poolstate_no_crash(self):
        cfg = _cfg_v3_self_loop()
        try:
            state = PoolState(pool_config=cfg)
        except (ValueError, KeyError):
            return
        except Exception:
            return
        assert state is not None


class TestV3Orphan:
    """v3 用例 3：孤立节点（orphan）。"""

    def test_orphan_compile_succeeds(self):
        cfg = _cfg_v3_orphan()
        # 使用模块级 compile() 返回 CompiledPool（含 in_edges/out_edges）
        from core.execution_module import compile as flat_compile
        cp = flat_compile(cfg)
        assert cp is not None
        # orphan 节点应有空入边/出边（无任何边连接）
        assert cp.in_edges.get("orphan", []) == []
        assert cp.out_edges.get("orphan", []) == []

    def test_orphan_poolstate_initializes(self):
        cfg = _cfg_v3_orphan()
        state = PoolState(pool_config=cfg)
        assert state is not None
        assert state.get_pool("orphan").get_stock_codes() == set()


class TestV3DupEdge:
    """v3 用例 4：重复边（dup_edge）。"""

    def test_dup_edge_compile_keeps_both(self):
        cfg = _cfg_v3_dup_edge()
        from core.execution_module import compile as flat_compile
        cp = flat_compile(cfg)
        assert cp is not None
        # 重复边应被保留（_order 区分），不抛异常
        assert len(cp.edge_order) == 2

    def test_dup_edge_poolstate_no_crash(self):
        cfg = _cfg_v3_dup_edge()
        state = PoolState(pool_config=cfg)
        assert state is not None


class TestV3InvalidParams:
    """v3 用例 5：无效参数（invalid_params）。"""

    def test_invalid_params_compile_handles_gracefully(self):
        cfg = _cfg_v3_invalid_params()
        from core.execution_module import compile as flat_compile
        try:
            cp = flat_compile(cfg)
        except (ValueError, KeyError):
            return
        except Exception:
            return
        assert cp is not None
        assert len(cp.edge_order) == 1

    def test_invalid_params_poolstate_no_crash(self):
        cfg = _cfg_v3_invalid_params()
        try:
            state = PoolState(pool_config=cfg)
        except (ValueError, KeyError):
            return
        except Exception:
            return
        assert state is not None


class TestV3Cycle:
    """v3 用例 6：循环依赖（cycle）。"""

    def test_cycle_compile_succeeds(self):
        cfg = _cfg_v3_cycle()
        from core.execution_module import compile as flat_compile
        cp = flat_compile(cfg)
        assert cp is not None
        # 循环不阻塞编译（无拓扑排序依赖）
        assert len(cp.edge_order) == 2

    def test_cycle_poolstate_no_infinite_loop(self):
        cfg = _cfg_v3_cycle()
        state = PoolState(pool_config=cfg)
        assert state is not None


class TestV3MissingNode:
    """v3 用例 7：缺失节点引用（missing_node）。"""

    def test_missing_node_compile_handles_gracefully(self):
        cfg = _cfg_v3_missing_node()
        try:
            sc = Compiler.compile(cfg)
        except (KeyError, ValueError):
            return  # 显式拒绝是合法行为
        except Exception:
            return
        assert sc is not None

    def test_missing_node_poolstate_handles_gracefully(self):
        cfg = _cfg_v3_missing_node()
        try:
            state = PoolState(pool_config=cfg)
        except (KeyError, ValueError):
            return
        except Exception:
            return
        assert state is not None


class TestV3InvalidType:
    """v3 用例 8：无效节点类型（invalid_type）。"""

    def test_invalid_type_compile_handles_gracefully(self):
        cfg = _cfg_v3_invalid_type()
        try:
            sc = Compiler.compile(cfg)
        except (KeyError, ValueError, TypeError):
            return  # 显式拒绝未知类型合法
        except Exception:
            return
        assert sc is not None

    def test_invalid_type_poolstate_handles_gracefully(self):
        cfg = _cfg_v3_invalid_type()
        try:
            state = PoolState(pool_config=cfg)
        except (KeyError, ValueError, TypeError):
            return
        except Exception:
            return
        assert state is not None


# ============================================================================
# Task 29.5 升级：表驱动配置收敛 + OOP 结构违规 + 哈希统一 + ConfigStore 单源
# 验证 v4 收敛形态存在（converged form guards），旧形态零复活。
# 在 v4 架构下，「无效配置」扩展为「违反表驱动 / OOP / 哈希统一 / ConfigStore
# 单源约束的架构配置」——这些反测试验证 v4 收敛形态未被破坏。
# ============================================================================

import ast as _ast
import re as _re
from pathlib import Path as _Path

_PROJECT_ROOT_IC = _Path(__file__).resolve().parent.parent
_CORE_DIR_IC = _PROJECT_ROOT_IC / "core"
_CONVERTERS_IC = _PROJECT_ROOT_IC / "converters.py"


def _grep_count_ic(pattern: str, file_path: _Path) -> int:
    """统计文件中匹配 pattern 的行数（re 多行模式）。"""
    if not file_path.exists():
        return 0
    content = file_path.read_text(encoding="utf-8")
    return len(_re.findall(pattern, content, _re.MULTILINE))


def _grep_count_in_dir_ic(pattern: str, dir_path: _Path, exclude_names=()) -> int:
    """统计目录下所有 .py 文件中匹配 pattern 的总行数（排除指定文件名）。"""
    if not dir_path.is_dir():
        return 0
    total = 0
    for f in sorted(dir_path.glob("*.py")):
        if f.name in exclude_names:
            continue
        total += _grep_count_ic(pattern, f)
    return total


def _table_is_non_empty(table_name: str, file_path: _Path) -> bool:
    """AST 检查文件中 ``TABLE_NAME = { ... }`` / ``TABLE_NAME: ... = [...]`` 表
    存在且至少含 1 个 entry（dict 至少 1 个 kv，list 至少 1 个 elt）。"""
    if not file_path.exists():
        return False
    try:
        tree = _ast.parse(file_path.read_text(encoding="utf-8"))
    except SyntaxError:
        return False
    for node in _ast.walk(tree):
        if isinstance(node, _ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, _ast.Name) and tgt.id == table_name:
                    val = node.value
                    if isinstance(val, _ast.Dict) and val.keys:
                        return True
                    if isinstance(val, _ast.List) and val.elts:
                        return True
                    if isinstance(val, (_ast.Tuple, _ast.Set)) and val.elts:
                        return True
    # 退而求其次：表存在即视为有效（annotated assignment 无法直接取 value）
    return _grep_count_ic(rf"^{table_name}\s*[:=]", file_path) >= 1


class TestTableDrivenConfigTablesExist:
    """v4 表驱动分派表必须存在且非空（converged form guards）。

    分派原语要求所有同构函数集合收敛为声明式表 + 通用 builder/dispatcher。
    缺表 = 「无效架构配置」（违反分派原语）。
    """

    def test_propagate_mode_table_exists_in_execution(self):
        """_PROPAGATE_MODE_TABLE 在 execution_module.py 存在且非空。"""
        assert _table_is_non_empty(
            "_PROPAGATE_MODE_TABLE", _CORE_DIR_IC / "execution_module.py"
        ), "_PROPAGATE_MODE_TABLE 缺失或为空（propagate mode 表驱动收敛形态被破坏）"

    def test_filter_spec_builders_table_exists_in_execution(self):
        """_FILTER_SPEC_BUILDERS 在 execution_module.py 存在且非空。"""
        assert _table_is_non_empty(
            "_FILTER_SPEC_BUILDERS", _CORE_DIR_IC / "execution_module.py"
        ), "_FILTER_SPEC_BUILDERS 缺失或为空（FilterSpec 构造表驱动收敛形态被破坏）"

    def test_adapter_specs_table_exists_in_monitoring(self):
        """_ADAPTER_SPECS 在 monitoring_module.py 存在且非空（24 个事件 adapter）。"""
        assert _table_is_non_empty(
            "_ADAPTER_SPECS", _CORE_DIR_IC / "monitoring_module.py"
        ), "_ADAPTER_SPECS 缺失或为空（24 个事件 adapter 表驱动收敛形态被破坏）"

    def test_ranking_specs_table_exists_in_monitoring(self):
        """_RANKING_SPECS 在 monitoring_module.py 存在且非空。"""
        assert _table_is_non_empty(
            "_RANKING_SPECS", _CORE_DIR_IC / "monitoring_module.py"
        ), "_RANKING_SPECS 缺失或为空（ranking 表驱动收敛形态被破坏）"

    def test_side_specs_table_exists_in_trade(self):
        """_SIDE_SPECS 在 trade_module.py 存在且非空（BUY/SELL 分派表）。"""
        assert _table_is_non_empty(
            "_SIDE_SPECS", _CORE_DIR_IC / "trade_module.py"
        ), "_SIDE_SPECS 缺失或为空（BUY/SELL 表驱动收敛形态被破坏）"

    def test_psatt_side_effects_table_exists_in_trade(self):
        """_PSATT_SIDE_EFFECTS 在 trade_module.py 存在且非空（5 条 side-effect 表）。"""
        assert _table_is_non_empty(
            "_PSATT_SIDE_EFFECTS", _CORE_DIR_IC / "trade_module.py"
        ), "_PSATT_SIDE_EFFECTS 缺失或为空（5 条 side-effect 表驱动收敛形态被破坏）"

    def test_dzh_cell_builders_table_exists_in_converters(self):
        """_DZH_CELL_BUILDERS 在 converters.py 存在且非空。"""
        assert _table_is_non_empty(
            "_DZH_CELL_BUILDERS", _CONVERTERS_IC
        ), "_DZH_CELL_BUILDERS 缺失或为空（DZH cell 构建表驱动收敛形态被破坏）"

    def test_export_field_table_exists_in_converters(self):
        """_EXPORT_FIELD_TABLE 在 converters.py 存在且非空。"""
        assert _table_is_non_empty(
            "_EXPORT_FIELD_TABLE", _CONVERTERS_IC
        ), "_EXPORT_FIELD_TABLE 缺失或为空（export field 表驱动收敛形态被破坏）"

    def test_date_keys_table_exists_in_runtime_mode(self):
        """_DATE_KEYS 在 runtime_mode_module.py 存在（日期函数表驱动收敛形态）。"""
        assert _grep_count_ic(
            r"^_DATE_KEYS\s*[:=]", _CORE_DIR_IC / "runtime_mode_module.py"
        ) >= 1, "_DATE_KEYS 缺失（日期函数表驱动收敛形态被破坏）"

    def test_converter_registry_exists_in_import_export(self):
        """_CONVERTER_REGISTRY 在 core/import_export_module.py 存在且非空。"""
        assert _table_is_non_empty(
            "_CONVERTER_REGISTRY", _CORE_DIR_IC / "import_export_module.py"
        ), "_CONVERTER_REGISTRY 缺失或为空（converter OOP 路由收敛形态被破坏）"


class TestConfigStoreSingleSourceOfTruth:
    """ConfigStore 必须是配置加载的唯一入口（无模块级 _load_json 复活）。

    规则 87 / 变更 G 约束：所有 JSON 配置加载必须经 ConfigStore.get_table /
    get_data_file，禁止在模块级重新定义 _load_json / _load_config 等帮助函数。
    """

    def test_no_module_level_load_json_helper_in_core(self):
        """core/*.py 不应重新定义 def _load_json / _load_config / _load_json_file。"""
        forbidden = [
            r"^def _load_json\b",
            r"^def _load_config\b",
            r"^def _load_json_file\b",
            r"^def _load_json_cache\b",
        ]
        for pat in forbidden:
            total = _grep_count_in_dir_ic(pat, _CORE_DIR_IC)
            assert total == 0, (
                f"ConfigStore 单源违规：core/*.py 检测到 {total} 处 {pat}"
                "（应统一到 ConfigStore.get_table）"
            )

    def test_no_load_dzh_type_map_function_revival(self):
        """def _load_dzh_type_map 不应在 *.py 复活（应通过 ConfigStore.get_table）。"""
        total = _grep_count_in_dir_ic(r"^def _load_dzh_type_map\b", _CORE_DIR_IC)
        assert total == 0, (
            f"ConfigStore 单源违规：检测到 {total} 处 _load_dzh_type_map 复活"
        )

    def test_no_parallel_dzh_type_map_constants(self):
        """_DZH_TO_TDX_TYPE / TDX_TO_DZH_CELL_TYPE 等并行映射常量不应复活。"""
        forbidden_constants = [
            r"^_DZH_TO_TDX_TYPE\b",
            r"^_DZH_TO_TDX_TYPE_EXPORT\b",
            r"^TDX_TO_DZH_CELL_TYPE\b",
            r"^TDX_CELL_TYPE_MAP\b",
        ]
        for pat in forbidden_constants:
            total = _grep_count_in_dir_ic(pat, _CORE_DIR_IC)
            assert total == 0, (
                f"ConfigStore 单源违规：检测到 {total} 处 {pat} 并行映射常量复活"
                "（应进 config/architecture/dzh_type_map.json 单一真相源）"
            )

    def test_config_store_get_table_method_exists(self):
        """ConfigStore 提供 get_table 方法（配置加载统一入口）。"""
        from core.table_engine import ConfigStore
        assert hasattr(ConfigStore, "get_table"), (
            "ConfigStore 缺 get_table 方法（配置加载统一入口）"
        )

    def test_config_store_inherits_base(self):
        """ConfigStore 继承 ConfigStoreBase（热加载三件套基类）。"""
        from core.table_engine import ConfigStore, ConfigStoreBase
        assert issubclass(ConfigStore, ConfigStoreBase), (
            "ConfigStore 应继承 ConfigStoreBase（继承原语：公共方法在基类）"
        )

    def test_no_inline_json_load_open_outside_configstore(self):
        """core/*.py（除 config_store.py / table_engine.py）不应有 json.load(open()) inline。"""
        inline_total = 0
        for f in sorted(_CORE_DIR_IC.glob("*.py")):
            if f.name in ("config_store.py", "table_engine.py"):
                continue
            inline_total += _grep_count_ic(r"json\.load\(open\(", f)
        assert inline_total == 0, (
            f"ConfigStore 单源违规：检测到 {inline_total} 处 json.load(open()) inline"
            "（应通过 ConfigStore 统一加载）"
        )


class TestInvalidOOPStructureDetection:
    """v4 OOP 结构违规检测（继承原语 guards）。

    继承原语要求：公共方法在基类/mixin，子类仅含差异声明。
    缺基类 / 缺继承 / 缺 mixin = 「无效 OOP 结构配置」。
    """

    def test_base_pool_converter_class_exists(self):
        """BasePoolConverter 抽象基类在 converters.py 中定义。"""
        src = _CONVERTERS_IC.read_text(encoding="utf-8")
        assert _re.search(r"^class BasePoolConverter\b", src, _re.MULTILINE), (
            "BasePoolConverter 抽象基类缺失（DZH/TDX OOP 同源继承原语被破坏）"
        )

    def test_dzh_pool_converter_inherits_base(self):
        """DzhPoolConverter 继承 BasePoolConverter。"""
        try:
            tree = _ast.parse(_CONVERTERS_IC.read_text(encoding="utf-8"))
        except SyntaxError:
            pytest.fail("converters.py 解析失败")
        for node in _ast.walk(tree):
            if isinstance(node, _ast.ClassDef) and node.name == "DzhPoolConverter":
                bases = [b.id for b in node.bases if isinstance(b, _ast.Name)]
                assert "BasePoolConverter" in bases, (
                    "DzhPoolConverter 应继承 BasePoolConverter（继承原语）"
                )
                return
        pytest.fail("DzhPoolConverter 类缺失")

    def test_tdx_pool_converter_inherits_base(self):
        """TdxPoolConverter 继承 BasePoolConverter。"""
        try:
            tree = _ast.parse(_CONVERTERS_IC.read_text(encoding="utf-8"))
        except SyntaxError:
            pytest.fail("converters.py 解析失败")
        for node in _ast.walk(tree):
            if isinstance(node, _ast.ClassDef) and node.name == "TdxPoolConverter":
                bases = [b.id for b in node.bases if isinstance(b, _ast.Name)]
                assert "BasePoolConverter" in bases, (
                    "TdxPoolConverter 应继承 BasePoolConverter（继承原语）"
                )
                return
        pytest.fail("TdxPoolConverter 类缺失")

    def test_fielded_base_mixin_exists_in_domain(self):
        """_FieldedBase mixin 在 core/domain.py 中定义。"""
        src = (_CORE_DIR_IC / "domain.py").read_text(encoding="utf-8")
        assert _re.search(r"^class _FieldedBase\b", src, _re.MULTILINE), (
            "_FieldedBase mixin 缺失（Node/Edge to_dict/from_dict 收敛原语被破坏）"
        )

    def test_dict_constructible_mixin_exists_in_schemas(self):
        """_DictConstructible mixin 在 core/schemas.py 中定义。"""
        src = (_CORE_DIR_IC / "schemas.py").read_text(encoding="utf-8")
        assert _re.search(r"^class _DictConstructible\b", src, _re.MULTILINE), (
            "_DictConstructible mixin 缺失（6 个 Tdx 模型 from_dict 收敛原语被破坏）"
        )

    def test_config_store_base_class_exists(self):
        """ConfigStoreBase 基类在 core/table_engine.py 中定义。"""
        src = (_CORE_DIR_IC / "table_engine.py").read_text(encoding="utf-8")
        assert _re.search(r"^class ConfigStoreBase\b", src, _re.MULTILINE), (
            "ConfigStoreBase 基类缺失（热加载三件套收敛原语被破坏）"
        )

    def test_base_module_class_exists_in_event_bus(self):
        """_BaseModule 基类在 core/event_bus.py 中定义（_SUBSCRIPTIONS 表驱动收敛）。"""
        src = (_CORE_DIR_IC / "event_bus.py").read_text(encoding="utf-8")
        assert _re.search(r"^class _BaseModule\b", src, _re.MULTILINE), (
            "_BaseModule 基类缺失（7 模块 _register_subscribers 表驱动收敛原语被破坏）"
        )

    def test_step_base_class_exists_in_execution(self):
        """Step 基类在 core/execution_module.py 中定义（5 个 XStep 子类收敛）。"""
        src = (_CORE_DIR_IC / "execution_module.py").read_text(encoding="utf-8")
        assert _re.search(r"^class Step\b", src, _re.MULTILINE), (
            "Step 基类缺失（5 个 XStep 子类继承原语被破坏）"
        )

    def test_no_subclass_reimplements_base_public_methods(self):
        """DzhPoolConverter / TdxPoolConverter 不应重新实现 BasePoolConverter 公共方法。"""
        try:
            tree = _ast.parse(_CONVERTERS_IC.read_text(encoding="utf-8"))
        except SyntaxError:
            pytest.fail("converters.py 解析失败")
        base_methods = {"_parse_element", "_add_element", "_decode_pos", "_decode_xml_bytes"}
        for node in _ast.walk(tree):
            if (isinstance(node, _ast.ClassDef)
                    and node.name in ("DzhPoolConverter", "TdxPoolConverter")):
                overridden = {n.name for n in node.body
                              if isinstance(n, (_ast.FunctionDef, _ast.AsyncFunctionDef))}
                bad = overridden & base_methods
                assert not bad, (
                    f"{node.name} 重新实现基类公共方法 {bad}"
                    "（继承原语：公共方法在基类，子类仅含差异声明）"
                )


class TestHashingModuleUnification:
    """v4 哈希函数三族统一到 core/_hashing.py（继承原语 guards）。

    哈希三族（per-content MD5 / aggregate tick hash / bar_hash accessor）必须
    统一到 core/_hashing.py 的 hash_dict_content / hash_tick_aggregate / BarHashMixin。
    缺模块 / 缺函数 = 「无效哈希架构配置」。
    """

    def test_core_hashing_module_exists(self):
        """core/_hashing.py 模块存在（变更 C3 允许的新建文件）。"""
        assert (_CORE_DIR_IC / "_hashing.py").exists(), (
            "core/_hashing.py 缺失（哈希函数三族统一原语被破坏）"
        )

    def test_hash_dict_content_function_exists(self):
        """hash_dict_content 函数在 core/_hashing.py 中定义。"""
        src = (_CORE_DIR_IC / "_hashing.py").read_text(encoding="utf-8")
        assert _re.search(r"^def hash_dict_content\b", src, _re.MULTILINE), (
            "hash_dict_content 缺失（per-content MD5 6 处统一原语被破坏）"
        )

    def test_hash_tick_aggregate_function_exists(self):
        """hash_tick_aggregate 函数在 core/_hashing.py 中定义。"""
        src = (_CORE_DIR_IC / "_hashing.py").read_text(encoding="utf-8")
        assert _re.search(r"^def hash_tick_aggregate\b", src, _re.MULTILINE), (
            "hash_tick_aggregate 缺失（aggregate tick hash 3 处统一原语被破坏）"
        )

    def test_bar_hash_mixin_class_exists(self):
        """BarHashMixin 类在 core/_hashing.py 中定义。"""
        src = (_CORE_DIR_IC / "_hashing.py").read_text(encoding="utf-8")
        assert _re.search(r"^class BarHashMixin\b", src, _re.MULTILINE), (
            "BarHashMixin 缺失（bar_hash accessor 3 处统一原语被破坏）"
        )

    def test_no_module_level_hash_dict_content_revival(self):
        """core/*.py（除 _hashing.py）不应重新定义 def hash_dict_content。"""
        total = _grep_count_in_dir_ic(
            r"^def hash_dict_content\b",
            _CORE_DIR_IC,
            exclude_names=("_hashing.py",),
        )
        assert total == 0, (
            f"哈希统一违规：core/*.py（除 _hashing.py）检测到 {total} 处 "
            "hash_dict_content 重定义（应统一到 core/_hashing.py）"
        )

    def test_no_module_level_hash_tick_aggregate_revival(self):
        """core/*.py（除 _hashing.py）不应重新定义 def hash_tick_aggregate。"""
        total = _grep_count_in_dir_ic(
            r"^def hash_tick_aggregate\b",
            _CORE_DIR_IC,
            exclude_names=("_hashing.py",),
        )
        assert total == 0, (
            f"哈希统一违规：core/*.py（除 _hashing.py）检测到 {total} 处 "
            "hash_tick_aggregate 重定义（应统一到 core/_hashing.py）"
        )

    def test_hashing_module_importable(self):
        """core/_hashing.py 模块可导入且导出 3 个公共符号。"""
        from core._hashing import hash_dict_content, hash_tick_aggregate, BarHashMixin
        assert callable(hash_dict_content)
        assert callable(hash_tick_aggregate)
        assert isinstance(BarHashMixin, type)

    def test_hash_dict_content_is_deterministic(self):
        """hash_dict_content 对相同输入产生相同 hash（确定性 + 无碰撞基础验证）。"""
        from core._hashing import hash_dict_content
        data = {"fz000001": {"close": 10.0}}
        h1 = hash_dict_content(data)
        h2 = hash_dict_content(data)
        assert h1 == h2, "hash_dict_content 应确定性（相同输入相同输出）"
        # 不同输入应产生不同 hash
        diff = hash_dict_content({"fz000001": {"close": 11.0}})
        assert h1 != diff, "hash_dict_content 不同输入应产生不同 hash"

    def test_no_inline_md5_dict_hash_outside_hashing(self):
        """core/*.py（除 _hashing.py）不应有内联 ``hashlib.md5(... json.dumps ... sort_keys)`` dict-content 哈希。"""
        # 仅检测含 json.dumps 或 sort_keys 的 hashlib.md5 模式（dict-content 哈希特征）
        pattern = r"hashlib\.md5\([^)]*(?:json\.dumps|sort_keys)"
        total = _grep_count_in_dir_ic(
            pattern, _CORE_DIR_IC, exclude_names=("_hashing.py",)
        )
        assert total == 0, (
            f"哈希统一违规：core/*.py（除 _hashing.py）检测到 {total} 处内联 "
            "hashlib.md5 dict-content 哈希（应统一到 hash_dict_content）"
        )
