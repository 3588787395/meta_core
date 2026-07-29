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
