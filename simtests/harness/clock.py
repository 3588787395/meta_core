"""Simulation clock helpers for simtests.

Centralises setting the PoolEngine virtual time source so that
simulation tests do not depend on removed legacy attributes such as
``_virtual_clock`` or ``_current_time_source``.
"""
from __future__ import annotations

from meta_core.core.engine import PoolEngine


_MINIMAL_POOL_CONFIG = {"id": "_simtest_clock_", "nodes": [], "edges": []}


def ensure_pool_engine(engine: PoolEngine) -> "PoolEngine":
    """Return the engine's PoolEngine, creating a minimal one if absent."""
    pe = engine._pool_engine
    if pe is None:
        pe = engine._ensure_pool_engine(_MINIMAL_POOL_CONFIG)
    return pe


def set_engine_clock(engine: PoolEngine, clock: float) -> None:
    """Fix the engine's virtual time source to ``clock`` seconds since 00:00 today.

    ``clock`` is interpreted as an offset from today's midnight (e.g. 34500
    means 09:35:00).  The helper is idempotent: it creates a minimal
    PoolEngine if the engine has not been bound to one yet.
    """
    pe = ensure_pool_engine(engine)
    pe.state.time_source = {
        "driver_type": "virtual",
        "current_ts": clock,
        "kind": "simtest",
    }
