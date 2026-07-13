"""meta_core.api — consolidated HTTP endpoint modules.

Merged from 4 files into 2:
- pool_api.py: config_api + table_api + meta_api (pool configuration management)
- system_api.py: run_api + formula_api + import_api (runtime execution + formula + import/export)

向后兼容：通过 sys.modules 注册旧模块名 run_api / formula_api / import_api，
旧 import 路径继续可用。
"""
import sys as _sys

try:
    from .pool_api import (
        router as config_api_router,
        init as config_api_init,
        table_router,
        table_config_router,
        set_engine as set_table_engine,
        create_meta_router,
    )
    from .system_api import (
        create_execution_router,
        create_replay_router,
        create_sim_router,
        _enrich_tdx_node_data,
        _generate_mock_bar_data,
        create_dzh_router,
        create_json_router,
        create_formula_router,
    )
except ImportError:
    from pool_api import (
        router as config_api_router,
        init as config_api_init,
        table_router,
        table_config_router,
        set_engine as set_table_engine,
        create_meta_router,
    )
    from system_api import (
        create_execution_router,
        create_replay_router,
        create_sim_router,
        _enrich_tdx_node_data,
        _generate_mock_bar_data,
        create_dzh_router,
        create_json_router,
        create_formula_router,
    )

# 注册旧模块名到 system_api 模块，保持向后兼容
try:
    from . import system_api as _system_api
    _sys.modules[__name__ + ".run_api"] = _system_api
    _sys.modules[__name__ + ".formula_api"] = _system_api
    _sys.modules[__name__ + ".import_api"] = _system_api
except Exception:
    pass

__all__ = [
    "config_api_router",
    "config_api_init",
    "table_router",
    "table_config_router",
    "set_table_engine",
    "create_meta_router",
    "create_execution_router",
    "create_replay_router",
    "create_sim_router",
    "_enrich_tdx_node_data",
    "_generate_mock_bar_data",
    "create_dzh_router",
    "create_json_router",
    "create_formula_router",
]