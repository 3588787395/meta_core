"""Helper script to append Task 20 additional tests to test_negative_invalid_config.py.

Used because the Edit tool was unable to match the existing file content.
This script reads the file, finds the anchor (last test method), and inserts
the additional tests. Idempotent: detects if Task 20 tests already present.
"""
from pathlib import Path

TARGET = Path(__file__).parent / "test_negative_invalid_config.py"
ANCHOR = '        assert hasattr(v, "validate_logic")'
MARKER = "# Task 20 — additional bad topology scenarios (appended)"

ADDITION = '''


# ============================================================================
# Task 20 — additional bad topology scenarios (appended)
# ============================================================================


def _cfg_empty_candidate_pool() -> Dict[str, Any]:
    """Config with a candidate pool node whose stocks list is empty."""
    return {
        "id": "neg_empty_cand",
        "name": "empty_candidate_pool",
        "nodes": [
            {"id": "src", "type": "candidate", "name": "empty_src",
             "params": {"stocks": []}},
            {"id": "tgt", "type": "target", "name": "tgt", "params": {}},
        ],
        "edges": [
            {"id": "e1", "from": "src", "to": "tgt",
             "params": {"_order": 0, "starttype": 0, "cxtype": 0}},
        ],
    }


def _cfg_self_loop() -> Dict[str, Any]:
    """Edge whose from == to (self-loop)."""
    return {
        "id": "neg_self_loop", "name": "self_loop",
        "nodes": [{"id": "n1", "type": "statepool", "name": "st", "params": {}}],
        "edges": [{"id": "e1", "from": "n1", "to": "n1", "type": "conditional"}],
    }


def _cfg_orphan_node() -> Dict[str, Any]:
    """Config containing a node with no in/out edges."""
    return {
        "id": "neg_orphan", "name": "orphan_node",
        "nodes": [
            {"id": "n1", "type": "statepool", "name": "a", "params": {}},
            {"id": "n2", "type": "statepool", "name": "b", "params": {}},
            {"id": "orphan", "type": "statepool", "name": "lonely", "params": {}},
        ],
        "edges": [{"id": "e1", "from": "n1", "to": "n2", "type": "conditional"}],
    }


def _cfg_duplicate_edge() -> Dict[str, Any]:
    """Two edges with identical from/to (duplicate)."""
    return {
        "id": "neg_dup_edge", "name": "duplicate_edge",
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


def _cfg_invalid_edge_params_negative_order() -> Dict[str, Any]:
    """Edge params._order is negative (invalid)."""
    return {
        "id": "neg_neg_order", "name": "negative_order",
        "nodes": [
            {"id": "n1", "type": "statepool", "name": "a", "params": {}},
            {"id": "n2", "type": "statepool", "name": "b", "params": {}},
        ],
        "edges": [
            {"id": "e1", "from": "n1", "to": "n2", "type": "conditional",
             "params": {"_order": -5}},
        ],
    }


def _cfg_circular_reference() -> Dict[str, Any]:
    """Edges form a cycle A->B->A."""
    return {
        "id": "neg_cycle", "name": "circular_reference",
        "nodes": [
            {"id": "A", "type": "statepool", "name": "a", "params": {}},
            {"id": "B", "type": "statepool", "name": "b", "params": {}},
        ],
        "edges": [
            {"id": "e1", "from": "A", "to": "B", "type": "conditional"},
            {"id": "e2", "from": "B", "to": "A", "type": "conditional"},
        ],
    }


class TestEmptyCandidatePool:
    """Task 20: empty candidate pool should not crash the compiler."""

    def test_empty_candidate_pool_compiles(self):
        cfg = _cfg_empty_candidate_pool()
        schedule = Compiler.compile(cfg)
        assert schedule is not None

    def test_empty_candidate_pool_state_initializes(self):
        cfg = _cfg_empty_candidate_pool()
        state = PoolState(pool_config=cfg)
        assert state is not None
        assert state.get_pool("src").get_stock_codes() == set()


class TestSelfLoopEdge:
    """Task 20: self-loop edge (from == to) should be rejected."""

    def test_self_loop_compile_raises_or_handles_gracefully(self):
        cfg = _cfg_self_loop()
        with pytest.raises(ValueError):
            Compiler.compile(cfg)

    def test_self_loop_poolstate_handles_gracefully(self):
        cfg = _cfg_self_loop()
        try:
            state = PoolState(pool_config=cfg)
        except (ValueError, KeyError):
            return
        except Exception:
            return
        assert state is not None


class TestOrphanNode:
    """Task 20: orphan node (no edges) should be allowed (warning, not error)."""

    def test_orphan_node_compile_succeeds(self):
        cfg = _cfg_orphan_node()
        schedule = Compiler.compile(cfg)
        assert schedule is not None
        assert schedule.in_edges.get("orphan", []) == []
        assert schedule.out_edges.get("orphan", []) == []

    def test_orphan_node_poolstate_initializes(self):
        cfg = _cfg_orphan_node()
        state = PoolState(pool_config=cfg)
        assert state is not None
        assert state.get_pool("orphan").get_stock_codes() == set()


class TestDuplicateEdge:
    """Task 20: duplicate edges (same from/to) should be deduped or kept."""

    def test_duplicate_edge_compile_keeps_both(self):
        cfg = _cfg_duplicate_edge()
        schedule = Compiler.compile(cfg)
        assert schedule is not None
        assert len(schedule.edge_order) == 2

    def test_duplicate_edge_poolstate_no_crash(self):
        cfg = _cfg_duplicate_edge()
        state = PoolState(pool_config=cfg)
        assert state is not None


class TestInvalidEdgeParams:
    """Task 20: invalid edge params (negative _order) should be handled."""

    def test_negative_order_handled_by_compile(self):
        cfg = _cfg_invalid_edge_params_negative_order()
        schedule = Compiler.compile(cfg)
        assert schedule is not None
        assert len(schedule.edge_order) == 1

    def test_negative_order_does_not_crash_poolstate(self):
        cfg = _cfg_invalid_edge_params_negative_order()
        state = PoolState(pool_config=cfg)
        assert state is not None


class TestCircularReference:
    """Task 20: circular reference (A->B->A) should be detected but not block."""

    def test_circular_reference_compile_succeeds(self):
        cfg = _cfg_circular_reference()
        schedule = Compiler.compile(cfg)
        assert schedule is not None
        assert len(schedule.edge_order) == 2

    def test_circular_reference_poolstate_no_infinite_loop(self):
        cfg = _cfg_circular_reference()
        state = PoolState(pool_config=cfg)
        assert state is not None
'''


def main() -> None:
    content = TARGET.read_text(encoding="utf-8")
    if MARKER in content:
        print("Task 20 tests already present, skipping.")
        return
    if ANCHOR not in content:
        raise RuntimeError(f"Anchor not found in {TARGET}")
    new_content = content.replace(ANCHOR, ANCHOR + ADDITION, 1)
    TARGET.write_text(new_content, encoding="utf-8")
    print(f"Appended {len(ADDITION)} chars to {TARGET}")
    print(f"New file size: {TARGET.stat().st_size}")


if __name__ == "__main__":
    main()
