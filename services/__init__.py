"""meta_core.services — 数据与服务层。

合并后的模块结构：
    - data.py           : 数据服务统一入口（DataQuery / DataSourceContract / DataSyncService / MarketDataPort）
    - storage.py         : SQLite 持久化层
    - tq_adapter.py      : TqAdapter 实时行情适配器
    - minute_aggregator.py : Min1Aggregator 分钟线合成器
    - 其他服务模块

注：``formula_cache.py`` 已于 SubTask 28.4 合并至 ``core/formula_module.py``，
``FormulaCache`` 由该模块内联提供。

向后兼容：通过 sys.modules 注册旧模块名 data_query / data_service / market_data_port，
旧 import 路径（如 ``from meta_core.services.data_query import DataQuery``）继续可用。
"""

import sys as _sys

from . import data as _data

# 注册旧模块名到 data 模块，保持向后兼容
_sys.modules[__name__ + ".data_query"] = _data
_sys.modules[__name__ + ".data_service"] = _data
_sys.modules[__name__ + ".market_data_port"] = _data

# 显式 re-export 关键类/函数，方便 ``from meta_core.services import DataQuery`` 等用法
from .data import (  # noqa: E402
    DataQuery,
    DataQueryService,
    KLineProvider,
    pre_market_calibration,
    DataSourceContractError,
    DataSourceUnavailableErrorContract,
    DataSourceMockExplicitOnlyError,
    DataSourceContract,
    get_default_contract,
    reset_default_contract,
    DataSyncService,
    MarketDataPort,
    TqAdapterMarketDataPort,
)

__all__ = [
    "DataQuery",
    "DataQueryService",
    "KLineProvider",
    "pre_market_calibration",
    "DataSourceContractError",
    "DataSourceUnavailableErrorContract",
    "DataSourceMockExplicitOnlyError",
    "DataSourceContract",
    "get_default_contract",
    "reset_default_contract",
    "DataSyncService",
    "MarketDataPort",
    "TqAdapterMarketDataPort",
]