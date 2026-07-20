from __future__ import annotations

"""
simtests package conftest — re-exports factory functions from tests/conftest.py
and provides pytest fixtures for engine and simulator.
"""
import sys
import os

# Add meta_core to path (mirrors tests/conftest.py)
META_CORE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Add parent of meta_core (PYPlugins) so `meta_core` is importable as a package
sys.path.insert(0, os.path.dirname(META_CORE_DIR))
sys.path.insert(0, META_CORE_DIR)

# 兼容测试中以顶层模块名导入 meta_core 内部模块的写法
# 预先把顶层名注册到 sys.modules，确保相对导入链正常工作
import meta_core.converters  # noqa: E402
import meta_core.native  # noqa: E402
import meta_core.core  # noqa: E402
import meta_core.core.screening_module  # noqa: E402
import meta_core.app  # noqa: E402
import meta_core.core.schemas  # noqa: E402
import meta_core.services  # noqa: E402
import meta_core.core.table_engine  # noqa: E402
import meta_core.core.runtime_mode_module  # noqa: E402
sys.modules.setdefault('converters', meta_core.converters)
sys.modules.setdefault('native', meta_core.native)
sys.modules.setdefault('engine', meta_core.core.engine)
sys.modules.setdefault('tdx_evaluators', meta_core.core.screening_module)
sys.modules.setdefault('app', meta_core.app)
sys.modules.setdefault('schemas', meta_core.core.schemas)
sys.modules.setdefault('services', meta_core.services)
sys.modules.setdefault('table_engine', meta_core.core.table_engine)
sys.modules.setdefault('kline_replay_engine', meta_core.core.runtime_mode_module)
sys.modules.setdefault('runtime_simulator', meta_core.core.runtime_mode_module)

import pytest  # noqa: E402
from meta_core.core.engine import PoolEngine  # noqa: E402

# Re-export ALL factory functions from tests/conftest.py
from meta_core.tests.conftest import (  # noqa: E402,F401
    make_tdx_simple_pool,
    make_dzh_simple_pool,
    make_tdx_multi_level_pool,
    make_tdx_unconditional_pool,
    make_tdx_ttl_pool,
    make_dzh_multi_condition_pool,
    make_sample_bar_data,
    extract_node_stocks,
    extract_stock_codes,
    gate_starttype_passes,
    gate_duration_expired,
)


@pytest.fixture
def engine():
    """Create a fresh PoolEngine instance for each test."""
    return PoolEngine()


@pytest.fixture
def simulator():
    """Create a RuntimeSimulator initialized with make_tdx_simple_pool()."""
    from meta_core.core.runtime_mode_module import RuntimeSimulator
    return RuntimeSimulator(make_tdx_simple_pool())
