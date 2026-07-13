"""
数据源提供者抽象基类与管理器。

DataSourceProvider 定义了所有数据源必须实现的接口（带默认空实现），
DataSourceManager 负责根据配置动态加载提供者并维护降级链。
"""

import importlib
import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class DataSourceProvider:
    """数据源提供者抽象基类。

    所有方法均提供默认空实现，子类只需覆写自己支持的方法即可。
    """

    # ------------------------------------------------------------------
    # 基础状态
    # ------------------------------------------------------------------

    def is_ready(self) -> bool:  # noqa: D401
        """返回当前提供者是否就绪。默认返回 False。"""
        return False

    def get_mode_info(self) -> str:
        """返回当前提供者的模式描述字符串。默认返回空字符串。"""
        return ""

    # ------------------------------------------------------------------
    # 行情数据
    # ------------------------------------------------------------------

    def resolve_market(self, markets) -> Dict[str, List[str]]:
        """解析市场列表，返回 {市场名: [股票代码]} 映射。默认返回空字典。"""
        return {}

    def get_kline_data(self, codes, period=None, start_date=None, end_date=None, **kwargs) -> Dict:
        """获取K线数据。默认返回空字典。"""
        return {}

    def get_snapshot(self, codes) -> Dict[str, Dict]:
        """获取实时快照。默认返回空字典。"""
        return {}

    # ------------------------------------------------------------------
    # 板块 / 股票列表
    # ------------------------------------------------------------------

    def get_block_members(self, block_code) -> List[str]:
        """获取板块成员代码列表。默认返回空列表。"""
        return []

    def get_stock_list_by_type(self, list_type, customblockname='', **kwargs) -> List[Dict]:
        """按类型获取股票列表。默认返回空列表。"""
        return []

    def get_sector_list(self, list_type=1) -> List[Dict]:
        """获取板块列表。默认返回空列表。"""
        return []

    def get_sector_stocks(self, sector_code, block_type=0) -> List[str]:
        """获取板块成分股代码列表。默认返回空列表。"""
        return []

    # ------------------------------------------------------------------
    # 公式评估
    # ------------------------------------------------------------------

    def eval_indicator(self, codes, formula_text, period, sorttype=0) -> Dict:
        """评估指标公式。默认返回空结果的标准格式。"""
        return {'result': {}, 'inditype': 0}

    def eval_formula_xg(self, formula_name, formula_arg='', stock_list=None,
                        period='1d', count=0, dividend_type=1,
                        start_time='', end_time='') -> Dict:
        """评估选股公式。默认返回失败的标准格式。"""
        return {"success": False, "result": {}, "selected_codes": []}

    def eval_formula_zb(self, formula_name, formula_arg='', stock_list=None,
                        period='1d', count=5, dividend_type=1,
                        return_count=1, return_date=False,
                        xsflag=6, start_time='', end_time='') -> Dict:
        """评估指标公式。默认返回失败的标准格式。"""
        return {"success": False, "result": {}}

    # ------------------------------------------------------------------
    # 板块操作
    # ------------------------------------------------------------------

    def send_user_block(self, block_code, stocks, show=True) -> Dict:
        """保存股票到自定义板块。默认返回失败格式。"""
        return {"success": False, "message": "not supported"}

    def create_sector(self, block_code, block_name) -> Dict:
        """创建自定义板块。默认返回失败格式。"""
        return {"success": False, "message": "not supported"}

    def clear_sector(self, block_code) -> Dict:
        """清空板块。默认返回失败格式。"""
        return {"success": False, "message": "not supported"}

    # ------------------------------------------------------------------
    # 财务 / 回放 / 重采样
    # ------------------------------------------------------------------

    def get_financial_data(self, codes, fields) -> Dict:
        """获取财务数据。默认返回空字典。"""
        return {}

    def get_replay_data(self, codes, current_time, period='1min') -> Dict[str, List[Dict]]:
        """获取回放数据。默认返回空字典。"""
        return {}

    def resample_kline(self, kline_1min, target_period) -> List[Dict]:
        """从1分钟K线重采样到目标周期。默认返回空列表。"""
        return []


class _StubMockProvider(DataSourceProvider):
    """始终就绪的兜底提供者，作为降级链的最终回退。

    仅在 mock_provider 模块不可用时使用。
    正常情况下 DataSourceManager.__init__ 会用完整 MockProvider 替换此实例。
    """

    def is_ready(self) -> bool:
        return True

    def get_mode_info(self) -> str:
        return "mock"


def _get_full_mock_provider() -> DataSourceProvider:
    """尝试加载完整的 MockProvider，失败则返回 _StubMockProvider。

    注意：此处不调用 grant_consent() —— mock 同意必须由用户显式通过
    DataSourceContract.grant_explicit_consent() 授权。
    MockProvider.is_ready() 在未授权前返回 False。
    """
    try:
        from .mock_provider import MockProvider as _FullMockProvider
        return _FullMockProvider()
    except Exception:
        return _StubMockProvider()


class ConfigInconsistencyError(Exception):
    """配置不一致异常。

    当 data_providers.json 等配置文件包含与 data_source_contract.json
    唯一真相源冲突的字段（如 default_chain）时抛出。
    """

    pass


class DataSourceUnavailableError(Exception):
    """数据源不可用异常。

    当活跃数据源不存在、未就绪或调用失败时抛出。
    禁止自动降级到其他数据源 —— 调用方必须显式处理（切换数据源或报错）。
    """

    pass


class DataSourceManager:
    """数据源管理器：根据配置动态加载提供者并维护降级链。

    default_chain 的唯一真相源是 data_source_contract.json（通过 DataSourceContract 读取）。
    data_providers.json 仅用于声明 providers 列表，不再承载 default_chain。

    配置示例 (data_providers.json)::

        {
            "providers": {
                "tq_dll": {
                    "module": "meta_core.services.providers.tq",
                    "class": "TqDllProvider"
                }
            }
        }
    """

    def __init__(self, config: Optional[Dict] = None):
        self._providers: Dict[str, DataSourceProvider] = {}
        self._default_chain: List[str] = []
        self._config = config or {}

        # 校验配置一致性：data_providers.json 不应包含 default_chain 字段
        # data_source_contract.json 是 default_chain 的唯一真相源
        self._validate_config_consistency()

        # 确保内置 MockProvider 始终可用（优先使用完整版）
        self._providers["mock"] = _get_full_mock_provider()

        # 从配置加载提供者
        self._load_providers()

        # 从 data_source_contract.json 读取 default_chain（唯一真相源，经 DataSourceContract）
        chain = self._read_default_chain_from_contract()
        if chain:
            self._default_chain = list(chain)
        else:
            self._default_chain = list(self._providers.keys())

        # 单源模式：活跃数据源默认取 default_chain 首项，不自动降级。
        # 用户可通过 set_active_source() 显式切换（mock 需先 grant_explicit_consent）。
        self._active_source: Optional[str] = (
            self._default_chain[0] if self._default_chain else None
        )

    # ------------------------------------------------------------------
    # 配置一致性校验
    # ------------------------------------------------------------------

    def _validate_config_consistency(self) -> None:
        """校验配置一致性。

        data_providers.json 不应再包含 default_chain 字段。
        data_source_contract.json 是 default_chain 的唯一真相源。
        若发现冲突字段，抛出 ConfigInconsistencyError —— 不静默回退。
        """
        if "default_chain" in self._config:
            raise ConfigInconsistencyError(
                "data_providers.json 不应包含 'default_chain' 字段。"
                "default_chain 的唯一真相源是 data_source_contract.json。"
                "请从 data_providers.json 中移除 default_chain 字段。"
            )

    def _read_default_chain_from_contract(self) -> List[str]:
        """从 DataSourceContract 读取 default_chain（唯一真相源）。

        通过 services.data_service.DataSourceContract 读取
        config/data_source_contract.json 的 default_chain 字段。
        """
        from ..data import get_default_contract
        contract = get_default_contract()
        return contract.default_chain

    # ------------------------------------------------------------------
    # 提供者加载
    # ------------------------------------------------------------------

    def _load_providers(self):
        """根据配置动态导入并实例化提供者。"""
        providers_config = self._config.get("providers", {})
        if not providers_config:
            return

        for name, spec in providers_config.items():
            if name == "mock":
                # mock 已内置，跳过
                continue
            module_path = spec.get("module", "")
            class_name = spec.get("class", "")
            if not module_path or not class_name:
                logger.warning("提供者配置缺少 module 或 class: %s", name)
                continue
            try:
                module = importlib.import_module(module_path)
                cls = getattr(module, class_name)
                instance = cls()
                if not isinstance(instance, DataSourceProvider):
                    logger.warning("提供者 %s 未继承 DataSourceProvider，跳过", name)
                    continue
                self._providers[name] = instance
                logger.info("已加载数据源提供者: %s (%s.%s)", name, module_path, class_name)
            except Exception as e:
                logger.warning("加载数据源提供者 %s 失败: %s", name, e)

    # ------------------------------------------------------------------
    # 公共接口
    # ------------------------------------------------------------------

    @property
    def active_provider(self) -> Optional[DataSourceProvider]:
        """返回当前活跃数据源提供者（单源，不遍历降级链）。"""
        if self._active_source is None:
            return None
        return self._providers.get(self._active_source)

    @property
    def default_chain(self) -> List[str]:
        """返回当前降级链的名称列表。"""
        return list(self._default_chain)

    def get_provider(self, name: str) -> Optional[DataSourceProvider]:
        """按名称获取特定提供者实例。"""
        return self._providers.get(name)

    def set_active_source(self, name: str) -> None:
        """显式切换活跃数据源（禁止自动降级）。

        mock 仅在用户显式调用 set_active_source('mock') 后可用，
        且需先通过 DataSourceContract.grant_explicit_consent() 授权。
        """
        if name not in self._providers:
            raise DataSourceUnavailableError(
                f"未知数据源: {name!r}，已注册: {list(self._providers.keys())}"
            )
        self._active_source = name

    def _call_active(self, method_name: str, *args, **kwargs):
        """调用当前活跃数据源的指定方法（单源，不降级，不回退）。

        - 仅调用 _active_source 对应的 provider
        - provider 抛异常时直接 re-raise（不尝试下一个 provider）
        - provider 返回空/None 时直接返回（不尝试下一个 provider）
        - 无活跃 provider 或方法不存在时抛 DataSourceUnavailableError
        """
        provider = (
            self._providers.get(self._active_source)
            if self._active_source is not None
            else None
        )
        if provider is None:
            raise DataSourceUnavailableError(
                f"无活跃数据源 (active_source={self._active_source!r})"
            )
        method = getattr(provider, method_name, None)
        if method is None:
            raise DataSourceUnavailableError(
                f"活跃数据源 {self._active_source!r} 不支持方法 {method_name!r}"
            )
        return method(*args, **kwargs)

    # ------------------------------------------------------------------
    # 便捷代理方法 —— 直接转发到 _call_active
    # ------------------------------------------------------------------

    def is_ready(self) -> bool:
        provider = self.active_provider
        return provider.is_ready() if provider else False

    def get_mode_info(self) -> str:
        return self._call_active("get_mode_info") or ""

    def resolve_market(self, markets) -> Dict[str, List[str]]:
        return self._call_active("resolve_market", markets) or {}

    def get_kline_data(self, codes, period=None, start_date=None, end_date=None, **kwargs) -> Dict:
        return self._call_active("get_kline_data", codes, period=period,
                                   start_date=start_date, end_date=end_date, **kwargs) or {}

    def get_snapshot(self, codes) -> Dict[str, Dict]:
        return self._call_active("get_snapshot", codes) or {}

    def get_block_members(self, block_code) -> List[str]:
        return self._call_active("get_block_members", block_code) or []

    def get_stock_list_by_type(self, list_type, customblockname='', **kwargs) -> List[Dict]:
        return self._call_active("get_stock_list_by_type", list_type,
                                   customblockname=customblockname, **kwargs) or []

    def get_sector_list(self, list_type=1) -> List[Dict]:
        return self._call_active("get_sector_list", list_type) or []

    def get_sector_stocks(self, sector_code, block_type=0) -> List[str]:
        return self._call_active("get_sector_stocks", sector_code, block_type=block_type) or []

    def eval_indicator(self, codes, formula_text, period, sorttype=0) -> Dict:
        return self._call_active("eval_indicator", codes, formula_text, period, sorttype=sorttype) or {}

    def eval_formula_xg(self, formula_name, formula_arg='', stock_list=None,
                        period='1d', count=0, dividend_type=1,
                        start_time='', end_time='') -> Dict:
        return self._call_active("eval_formula_xg", formula_name,
                                   formula_arg=formula_arg, stock_list=stock_list,
                                   period=period, count=count, dividend_type=dividend_type,
                                   start_time=start_time, end_time=end_time) or {}

    def eval_formula_zb(self, formula_name, formula_arg='', stock_list=None,
                        period='1d', count=5, dividend_type=1,
                        return_count=1, return_date=False,
                        xsflag=6, start_time='', end_time='') -> Dict:
        return self._call_active("eval_formula_zb", formula_name,
                                   formula_arg=formula_arg, stock_list=stock_list,
                                   period=period, count=count, dividend_type=dividend_type,
                                   return_count=return_count, return_date=return_date,
                                   xsflag=xsflag,
                                   start_time=start_time, end_time=end_time) or {}

    def send_user_block(self, block_code, stocks, show=True) -> Dict:
        return self._call_active("send_user_block", block_code, stocks, show=show) or {}

    def create_sector(self, block_code, block_name) -> Dict:
        return self._call_active("create_sector", block_code, block_name) or {}

    def clear_sector(self, block_code) -> Dict:
        return self._call_active("clear_sector", block_code) or {}

    def get_financial_data(self, codes, fields) -> Dict:
        return self._call_active("get_financial_data", codes, fields) or {}

    def get_replay_data(self, codes, current_time, period='1min') -> Dict[str, List[Dict]]:
        return self._call_active("get_replay_data", codes, current_time, period=period) or {}

    def resample_kline(self, kline_1min, target_period) -> List[Dict]:
        return self._call_active("resample_kline", kline_1min, target_period) or []
