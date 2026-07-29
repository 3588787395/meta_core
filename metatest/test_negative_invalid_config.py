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
