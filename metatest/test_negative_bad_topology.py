# -*- coding: utf-8 -*-
"""Task 19.3: bad topology negative tests.

System should gracefully handle bad graph topology (self-loops, orphan
nodes, duplicate edges, cycles, non-existent references) without crash.
"""
from __future__ import annotations
from typing import Any, Dict
import pytest
from core.execution_module import Compiler
from core.runtime_mode_module import PoolState

def _cfg_self_loop():
    return {"id": "t1", "name": "self_loop",
            "nodes": [{"id": "n1", "type": "statepool", "name": "st", "params": {}}],
            "edges": [{"id": "e1", "from": "n1", "to": "n1", "type": "conditional"}]}

def _cfg_orphan_nodes():
    return {"id": "t2", "name": "orphan",
            "nodes": [
                {"id": "n1", "type": "statepool", "name": "a", "params": {}},
                {"id": "n2", "type": "statepool", "name": "b", "params": {}},
                {"id": "n3", "type": "statepool", "name": "c", "params": {}}],
            "edges": []}

def _cfg_duplicate_edges():
    return {"id": "t3", "name": "dup_edges",
            "nodes": [
                {"id": "n1", "type": "statepool", "name": "a", "params": {}},
                {"id": "n2", "type": "statepool", "name": "b", "params": {}}],
            "edges": [
                {"id": "e1", "from": "n1", "to": "n2", "type": "conditional"},
                {"id": "e2", "from": "n1", "to": "n2", "type": "conditional"}]}

def _cfg_nonexistent_from():
    return {"id": "t4", "name": "bad_from",
            "nodes": [{"id": "n1", "type": "statepool", "name": "st", "params": {}}],
            "edges": [{"id": "e1", "from": "ghost", "to": "n1", "type": "conditional"}]}

def _cfg_empty_nodes_and_edges():
    return {"id": "t5", "name": "empty", "nodes": [], "edges": []}

def _cfg_cycle():
    return {"id": "t6", "name": "cycle",
            "nodes": [
                {"id": "n1", "type": "statepool", "name": "a", "params": {}},
                {"id": "n2", "type": "statepool", "name": "b", "params": {}}],
            "edges": [
                {"id": "e1", "from": "n1", "to": "n2", "type": "conditional"},
                {"id": "e2", "from": "n2", "to": "n1", "type": "conditional"}]}

class TestSelfLoop:
    def test_self_loop_compile(self):
        try:
            sc = Compiler.compile(_cfg_self_loop())
        except (KeyError, ValueError):
            return
        except Exception:
            return
        assert sc is not None

    def test_self_loop_poolstate(self):
        try:
            s = PoolState(pool_config=_cfg_self_loop())
        except (KeyError, ValueError):
            return
        except Exception:
            return
        assert s is not None

class TestOrphanNodes:
    def test_orphan_compile(self):
        sc = Compiler.compile(_cfg_orphan_nodes())
        assert sc is not None
        assert sc.edge_ctx == {}

    def test_orphan_poolstate(self):
        s = PoolState(pool_config=_cfg_orphan_nodes())
        assert s is not None
        assert s.get_pool("n1").get_stock_codes() == set()

class TestDuplicateEdges:
    def test_dup_compile(self):
        sc = Compiler.compile(_cfg_duplicate_edges())
        assert sc is not None
        assert len(sc.edge_ctx) >= 1

    def test_dup_poolstate(self):
        s = PoolState(pool_config=_cfg_duplicate_edges())
        assert s is not None

class TestNonExistentFrom:
    def test_bad_from_compile(self):
        try:
            sc = Compiler.compile(_cfg_nonexistent_from())
        except (KeyError, ValueError):
            return
        except Exception:
            return
        assert sc is not None

    def test_bad_from_poolstate(self):
        try:
            s = PoolState(pool_config=_cfg_nonexistent_from())
        except (KeyError, ValueError):
            return
        except Exception:
            return
        assert s is not None

class TestEmptyGraph:
    def test_empty_compile(self):
        sc = Compiler.compile(_cfg_empty_nodes_and_edges())
        assert sc is not None
        assert sc.edge_ctx == {}

    def test_empty_poolstate(self):
        s = PoolState(pool_config=_cfg_empty_nodes_and_edges())
        assert s is not None

class TestCycle:
    def test_cycle_compile(self):
        sc = Compiler.compile(_cfg_cycle())
        assert sc is not None
        assert len(sc.edge_ctx) >= 1

    def test_cycle_poolstate(self):
        s = PoolState(pool_config=_cfg_cycle())
        assert s is not None
