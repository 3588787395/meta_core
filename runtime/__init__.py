"""Compatibility shim — runtime/ modules migrated to core/ (Task 7).

This module exists solely to maintain backward compatibility for code that
imports from meta_core.runtime.*. All actual implementations now live in
meta_core.core.*:
    runtime/kline_replay_engine.py -> core/replay.py
    runtime/runtime_simulator.py   -> core/simulator.py
    runtime/tdx_evaluators.py      -> core/evaluators.py
"""
import sys

from ..core import replay as _replay
from ..core import simulator as _simulator
from ..core import evaluators as _evaluators

# Register as submodules of meta_core.runtime for backward compatibility
# (so `import meta_core.runtime.kline_replay_engine` etc. still work)
sys.modules[__name__ + '.kline_replay_engine'] = _replay
sys.modules[__name__ + '.runtime_simulator'] = _simulator
sys.modules[__name__ + '.tdx_evaluators'] = _evaluators

# Also expose as attributes (so `meta_core.runtime.tdx_evaluators` attribute access works)
kline_replay_engine = _replay
runtime_simulator = _simulator
tdx_evaluators = _evaluators

# Re-export public symbols
from ..core.replay import KLineReplayEngine
from ..core.simulator import RuntimeSimulator

__all__ = [
    'KLineReplayEngine',
    'RuntimeSimulator',
    'tdx_evaluators',
    'kline_replay_engine',
    'runtime_simulator',
]
