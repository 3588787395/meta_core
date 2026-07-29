"""
tq_adapter.py —— 数据源适配器门面（Facade）

本模块是历史遗留的统一入口，被 app / api / native / tests 大量 import。
其内部实现已重构为「DataSourceManager + 多个 DataSourceProvider」：
  - 配置：data_providers.json
  - 真实数据源：tq_dll (TPythClient.dll) / tq_sdk / akshare
  - mock 数据源：仅在用户显式调用 set_active_source('mock') 时启用
  - 契约：data_source_contract.json（Task 6 / Task 11）

H2 修复（用户硬性要求）：
  - 不静默回退到 mock —— 无真实数据源时记录 _data_source_state['status']='no_real_source'
  - 状态对外暴露：get_data_source_state() / get_available_sources()
  - 显式切换：set_active_source(name) 可强制使用 mock 或某个真实数据源
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# ── 重新导出 provider 内部工具 ──────────────────────────────────────
from .providers import DataSourceManager, DataSourceProvider
from converters_common import decode_formula
from .providers import (
    map_period,
    normalize_code,
    to_dzh_code,
    _format_timestamp,
    _format_hold_days,
    _norm_period,
)
from .providers import _MOCK_STOCK_NAMES, _MOCK_MARKET_STOCKS
from .data import (
    DataSourceContract,
    DataSourceContractError,
    DataSourceUnavailableErrorContract,
    DataSourceMockExplicitOnlyError,
    get_default_contract,
)
# DZH 列定义已移至 core/domain 作为领域常量，此处重新导出以保持向后兼容
try:
    from ..core.domain import DZH_COL_MAP
except ImportError:  # services 作为顶层包导入时回退到绝对导入
    from core.domain import DZH_COL_MAP


# 暴露为类方法（保持向后兼容）
class _TQFormatterMixin:
    @staticmethod
    def _format_timestamp(ts):
        return _format_timestamp(ts)

    @staticmethod
    def _format_hold_days(hold_sec):
        return _format_hold_days(hold_sec)

    # 静态方法：normalize_code / to_dzh_code（暴露给老代码 TqAdapter.normalize_code(...) 直接调用）
    @staticmethod
    def normalize_code(code):
        return normalize_code(code)

    @staticmethod
    def to_dzh_code(code):
        return to_dzh_code(code)

logger = logging.getLogger(__name__)


def _load_data_providers_config() -> Dict[str, Any]:
    """加载 data_providers.json 配置。"""
    config_path = Path(__file__).resolve().parent.parent / "config" / "data" / "data_providers.json"
    try:
        with open(config_path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning("加载 data_providers.json 失败: %s", e)
        return {}


def _load_data_source_routes() -> Dict[str, Any]:
    """加载 data_source_routes.json 配置。"""
    config_path = Path(__file__).resolve().parent.parent / "config" / "data" / "data_source_routes.json"
    try:
        with open(config_path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning("加载 data_source_routes.json 失败: %s", e)
        return {}


class DataSourceUnavailableError(Exception):
    """无可用数据源异常。当所有真实数据源都不可用且未显式选 mock 时抛出。"""
    pass


class TqAdapter(_TQFormatterMixin):
    """数据源适配器门面（Facade）。

    内部委托 DataSourceManager 沿降级链调用各 DataSourceProvider。
    对外提供 TqAdapter 风格的统一接口，兼容老代码的所有调用方式。
    """

    def __init__(self, mock_mode: bool = False, config: Optional[Dict] = None, sdk_mode: Optional[bool] = None):
        """初始化 TqAdapter。

        Args:
            mock_mode: 兼容老 API。True 时强制使用 mock 模式。
                       推荐使用 set_active_source('mock') 替代。
            config: 自定义 data_providers 配置（默认从 data_providers.json 加载）
            sdk_mode: 兼容老 API。True 时优先天勤 SDK（保留参数，无实际行为差异）。
        """
        self.mock_mode = bool(mock_mode)
        self._mode_source = "mock" if mock_mode else "real"
        self._sdk_mode = sdk_mode  # 仅记录，不再触发行为变化

        # 加载配置
        if config is None:
            config = _load_data_providers_config()
        self._config = config
        self._routes = _load_data_source_routes()

        # 数据源状态（H2 修复：暴露状态给 UI，不静默回退）
        self._data_source_state: Dict[str, Any] = {
            'status': 'unknown',
            'last_check': datetime.now(),
            'available': [],
            'active': None,
            'error': None,
        }

        # 显式切换的覆盖项：set_active_source 后锁定到指定 provider
        self._explicit_source: Optional[str] = None

        # 创建 DataSourceManager
        try:
            self._manager = DataSourceManager(config=config)
        except Exception as e:
            logger.error("创建 DataSourceManager 失败: %s", e)
            self._data_source_state['error'] = str(e)
            self._data_source_state['status'] = 'manager_init_failed'
            self._manager = None  # type: ignore
            return

        # 探测可用数据源（H2 修复：不静默回退）
        self._probe_sources()

    # ------------------------------------------------------------------
    # 数据源状态管理（H2 修复）
    # ------------------------------------------------------------------

    def _probe_sources(self) -> None:
        """探测所有数据源的就绪状态，更新 _data_source_state。"""
        if self._manager is None:
            return
        available: List[str] = []
        for name, provider in self._manager._providers.items():
            try:
                if provider.is_ready():
                    available.append(name)
            except Exception:
                continue

        self._data_source_state['last_check'] = datetime.now()
        self._data_source_state['available'] = available

        # 处理显式切换锁定
        if self._explicit_source:
            if self._explicit_source in self._manager._providers:
                provider = self._manager._providers[self._explicit_source]
                if provider.is_ready():
                    self._data_source_state['active'] = self._explicit_source
                    # HARDCODED: 不可剥离，理由：mock 是特殊 provider，需独立状态标识
                    if self._explicit_source == 'mock':
                        self._data_source_state['status'] = 'user_selected_mock'
                    else:
                        self._data_source_state['status'] = f'{self._explicit_source}_active'
                    self._data_source_state['error'] = None
                    return
            # 显式指定的 provider 不可用
            self._data_source_state['active'] = None
            self._data_source_state['status'] = f'{self._explicit_source}_unavailable'
            self._data_source_state['error'] = f'显式指定的数据源 {self._explicit_source} 不可用'
            return

        # 兼容老 mock_mode 标志
        if self.mock_mode:
            self._data_source_state['active'] = 'mock'
            self._data_source_state['status'] = 'legacy_mock_mode'
            self._data_source_state['error'] = None
            # 兼容老测试：mock_mode 时降级链强制只走 mock
            try:
                self._manager._default_chain = ['mock']
            except Exception:
                pass
            # 授权 mock provider（mock_mode 是显式选择）
            mock_provider = self._manager._providers.get('mock')
            if mock_provider and hasattr(mock_provider, 'grant_consent'):
                mock_provider.grant_consent()
            return

        # 默认行为：选择第一个就绪的真实数据源（H2 修复：不静默回退 mock）
        real_available = [n for n in available if n != 'mock']
        provider_status_map = self._routes.get("provider_status_map", {})
        if real_available:
            # 优先取降级链中第一个就绪的真实数据源
            for name in self._manager.default_chain:
                if name in real_available:
                    self._data_source_state['active'] = name
                    self._data_source_state['status'] = provider_status_map.get(name, 'real_active')
                    self._data_source_state['error'] = None
                    return
        # HARDCODED: 不可剥离，理由：mock 兜底可用性判断是数据源管理的核心语义，需区分无真实源与无源
        elif available == ['mock'] or (len(available) == 0 and 'mock' in self._manager._providers):
            err_msg = "无任何真实数据源可用 (tq_dll / tq_sdk / akshare 均未就绪)。请启动通达信客户端登录 TQ，或显式调用 set_active_source('mock') 使用模拟数据。"
            self._data_source_state['error'] = err_msg
            self._data_source_state['status'] = 'no_real_source'
            self._data_source_state['active'] = None
            logger.warning("数据源初始化: %s", err_msg)
        else:
            self._data_source_state['active'] = None
            self._data_source_state['status'] = 'no_source'
            self._data_source_state['error'] = "数据源管理器的 _providers 为空"

    def get_data_source_state(self) -> Dict[str, Any]:
        """返回当前数据源状态（H2 修复：暴露给 UI）。"""
        return dict(self._data_source_state)

    def _probe(
        self,
        source_name: Optional[str] = None,
        contract: Optional[DataSourceContract] = None,
    ) -> Dict[str, Any]:
        """契约探测（Task 6 / Task 11）。

        调用 data_source_contract.json 中的探测流程，验证数据源可用性。
        **禁止自动回退到 mock**：未就绪时按 on_unavailable 策略 raise / warn。

        Args:
            source_name: 数据源名称，默认探测 default_chain 中第一个。
                          传 'mock' 时仍会校验 explicit_only（防止误用）。
            contract: 可选，自定义契约实例（默认从配置文件加载）

        Returns:
            探测结果 dict: {name, ready, elapsed_ms, method, error}

        Raises:
            DataSourceUnavailableErrorContract: 数据源不可用且 policy=raise
            DataSourceMockExplicitOnlyError: mock 未被显式选择
        """
        c = contract or get_default_contract()
        target = source_name
        if target is None:
            chain = c.default_chain
            target = chain[0] if chain else "tq_dll"
        # 显式 source 已锁定时优先使用
        if self._explicit_source and self._explicit_source != target:
            target = self._explicit_source

        provider_instance = None
        if self._manager is not None and target in self._manager._providers:
            provider_instance = self._manager._providers.get(target)
        result = c.probe_source(target, provider_instance=provider_instance)
        if not result["ready"]:
            c.probe_or_raise(target, provider_instance=provider_instance)
        return result

    def probe_and_assert(self, source_name: Optional[str] = None) -> bool:
        """便捷方法：探测成功返回 True，失败 raise。

        供 api.py（合并自原 execution_api）在 /pools/{id}/run 启动前调用。
        """
        try:
            r = self._probe(source_name=source_name)
            return bool(r.get("ready", False))
        except DataSourceContractError:
            raise


    def get_available_sources(self) -> List[Dict[str, Any]]:
        """返回所有数据源的就绪状态列表。"""
        if self._manager is None:
            return []
        result: List[Dict[str, Any]] = []
        for name, provider in self._manager._providers.items():
            try:
                ready = provider.is_ready()
            except Exception:
                ready = False
            result.append({
                'name': name,
                'ready': ready,
                'mode_info': provider.get_mode_info() if ready else '',
                # HARDCODED: 不可剥离，理由：mock 是特殊 provider，其标识需直接由名称判定；未来可由 data_providers.json 的 explicit_only 派生
                'is_mock': name == 'mock',
            })
        return result

    def set_active_source(self, source_name: str) -> Dict[str, Any]:
        """显式切换数据源（H2 修复）。

        Args:
            source_name: 数据源名称，如 'tq_dll' / 'tq_sdk' / 'akshare' / 'mock'

        Returns:
            dict: {success, active, ready, error}
        """
        if self._manager is None:
            return {'success': False, 'error': 'DataSourceManager 未初始化', 'active': None, 'ready': False}

        provider = self._manager._providers.get(source_name)
        if provider is None:
            return {'success': False, 'error': f'未知数据源: {source_name}', 'active': None, 'ready': False}

        ready = provider.is_ready()
        self._explicit_source = source_name

        # Bug #13 修复：仅在 provider 实际就绪时才设置 active，
        # 否则 is_ready() 会因 active is not None 而错误返回 True
        # HARDCODED: 不可剥离，理由：mock 是特殊 provider，始终视为就绪
        if ready or source_name == 'mock':
            self._data_source_state['active'] = source_name
        else:
            self._data_source_state['active'] = None

        self._data_source_state['last_check'] = datetime.now()

        # HARDCODED: 不可剥离，理由：mock 是特殊 provider，需独立状态与 mock_mode 标记
        if source_name == 'mock':
            self._data_source_state['status'] = 'user_selected_mock'
            self.mock_mode = True
            if hasattr(provider, 'grant_consent') and not provider.is_ready():
                provider.grant_consent()
                ready = provider.is_ready()
        else:
            self._data_source_state['status'] = f'{source_name}_active' if ready else f'{source_name}_unavailable'
            if not self._explicit_only_check(source_name):
                self.mock_mode = False
        self._data_source_state['available'] = [
            n for n, p in self._manager._providers.items() if p.is_ready()
        ]
        self._data_source_state['error'] = None if ready else f'{source_name} 未就绪'

        return {
            'success': True,
            'active': source_name,
            'ready': ready,
            'error': None if ready else f'{source_name} 不可用',
        }

    def _explicit_only_check(self, source_name: str) -> bool:
        """检查数据源是否被配置为 explicit_only（仅在显式选择时使用）。"""
        try:
            providers_cfg = self._config.get('providers', {})
            spec = providers_cfg.get(source_name, {})
            return bool(spec.get('explicit_only', False))
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Tick 推送回调（Task 9: 实时流接线）
    # ------------------------------------------------------------------
    # TQ DLL (TPythClient.dll) / tq_sdk / akshare 均为拉取式（pull）数据源，
    # 不支持服务端 Tick 推送。系统采用轮询模式：由 PoolEngine._tick 周期性
    # 调用 get_snapshot() 拉取最新行情，再驱动 Min1Aggregator.on_tick() 合成分钟线。
    # register_tick_callback 仅记录回调列表，不会触发真实推送；保留接口以便
    # 未来切换到支持推送的数据源（如 tq_sdk 的 subscribe_quote）时无需改动调用方。

    def is_tick_push_supported(self) -> bool:
        """是否支持 Tick 推送回调。

        Returns:
            bool: 当前所有内置 provider（tq_dll / tq_sdk / akshare / mock）均为
                  False —— 系统使用拉取式（polling）获取行情。
        """
        return False

    def register_tick_callback(self, callback) -> bool:
        """注册 Tick 推送回调。

        当前实现为拉取式数据源，**不会真实推送** Tick。回调仅被记录，
        供未来支持推送的数据源使用。调用方应通过轮询 get_snapshot() 获取行情，
        并在收到数据后自行调用 Min1Aggregator.on_tick()。

        Args:
            callback: 回调函数，签名 callback(symbol: str, tick_data: dict) -> None

        Returns:
            bool: True 表示注册成功（已记录），False 表示不支持推送。
        """
        if not hasattr(self, '_tick_callbacks'):
            self._tick_callbacks = []
        self._tick_callbacks.append(callback)
        logger.info(
            "注册 Tick 回调（当前数据源为拉取式，不会真实推送；"
            "请通过 get_snapshot() 轮询行情）"
        )
        return False  # 返回 False 明示当前不支持推送

    # ------------------------------------------------------------------
    # 状态查询（兼容老 API）
    # ------------------------------------------------------------------

    def is_ready(self) -> bool:
        """数据源是否就绪。返回 True 当有真实数据源或显式选了 mock。"""
        active = self._data_source_state.get('active')
        if active is None:
            return False
        return True

    def get_mode_info(self) -> str:
        """返回当前模式描述字符串（分类模式）。
        - 'mock'  / 'no_source' / 'dll' / 'sdk' / 'akshare' / 其他真实 provider 原 key
        """
        active = self._data_source_state.get('active')
        if active is None:
            return 'no_source'
        # HARDCODED: 不可剥离，理由：mock 是特殊模式，需直接返回标识
        if active == 'mock':
            return 'mock'
        # 真实数据源映射：tq_dll → 'dll' / tq_sdk → 'sdk'，其他 provider 直接使用 key
        alias = self._routes.get("mode_aliases", {})
        return alias.get(active, active)

    def get_active_source_key(self) -> Optional[str]:
        """返回当前活跃数据源的原始 key（如 'tq_dll' / 'tq_sdk' / 'mock' / 'akshare'）。"""
        return self._data_source_state.get('active')

    def _active_provider(self) -> Optional[DataSourceProvider]:
        """获取当前活跃 provider。"""
        if self._manager is None:
            return None
        active_name = self._data_source_state.get('active')
        if active_name and active_name in self._manager._providers:
            p = self._manager._providers[active_name]
            if p.is_ready():
                return p
        return self._manager.active_provider

    def _require_active_provider(self) -> DataSourceProvider:
        """获取当前活跃 provider，无可用 provider 时抛 DataSourceUnavailableError。

        单点 active provider 语义（Task 6）：数据获取方法必须通过此方法取得
        provider，禁止走降级链 / 静默回退。
        """
        provider = self._active_provider()
        if provider is None:
            raise DataSourceUnavailableError("no active provider set")
        return provider

    # ------------------------------------------------------------------
    # 行情 / 板块 / 财务（单点 active provider 直调，禁止降级链回退）
    # ------------------------------------------------------------------

    def get_kline_data(self, codes, period=None, start_date=None, end_date=None, **kwargs):
        provider = self._require_active_provider()
        return provider.get_kline_data(codes, period=period, start_date=start_date, end_date=end_date, **kwargs)

    def get_adj_factor(self, symbol: str) -> float:
        """获取前复权因子。

        TqAdapter（基于 TQ DLL / tq_sdk / akshare 的门面）本身不直接支持
        复权因子查询。调用方应配置 AkShareProvider 或其他支持复权因子的数据源。

        Raises:
            NotImplementedError: TqAdapter 不支持复权因子查询。
        """
        raise NotImplementedError(
            "TqAdapter does not support adj factor query; "
            "configure AkShareProvider or another source"
        )

    def get_snapshot(self, codes):
        provider = self._require_active_provider()
        return provider.get_snapshot(codes)

    def resolve_market(self, markets):
        provider = self._require_active_provider()
        return provider.resolve_market(markets)

    def get_block_members(self, block_code):
        provider = self._require_active_provider()
        return provider.get_block_members(block_code)

    def get_stock_list_by_type(self, list_type, customblockname='', **kwargs):
        provider = self._require_active_provider()
        return provider.get_stock_list_by_type(list_type, customblockname=customblockname, **kwargs)

    def get_sector_list(self, list_type=1):
        provider = self._require_active_provider()
        return provider.get_sector_list(list_type)

    def get_sector_stocks(self, sector_code, block_type=0):
        provider = self._require_active_provider()
        return provider.get_sector_stocks(sector_code, block_type=block_type)

    # ------------------------------------------------------------------
    # 公式评估职责已移除（Task 10 / SubTask 10.1）
    # ------------------------------------------------------------------
    # TqAdapter 不再提供 eval_indicator / eval_formula_xg / eval_formula_zb /
    # formula_process_mul_zb / formula_process_mul_xg / eval_formula_zb_batch /
    # formula_exp 等公式转发方法。
    # 公式评估统一通过 FormulaRouter（core/formula_module.py）路由到
    # PythonFormulaEngine / HQChartProvider。
    # ------------------------------------------------------------------

    def get_kline_single(self, code, period='1d', count=100, start_date=None, end_date=None, dividend_type=1):
        """单股 K 线（兼容老 API）。"""
        provider = self._require_active_provider()
        return provider.get_kline_data([code], period=period, count=count, start_date=start_date,
                                       end_date=end_date, dividend_type=dividend_type)

    def get_kline(self, code, period='1d', start=None, end=None, count=100, dividend_type=1):
        """单股 K 线便捷方法（位置参数版本，兼容老 API）。

        Args:
            code: 股票代码
            period: K 线周期
            start: 起始日期/时间
            end: 结束日期/时间
            count: 数量
            dividend_type: 复权类型

        Returns:
            K 线列表，每条为 dict
        """
        provider = self._require_active_provider()
        r = provider.get_kline_data([code], period=period, start_date=start, end_date=end,
                                    count=count, dividend_type=dividend_type)
        if isinstance(r, dict):
            bars = r.get(code) or r.get('data', {}).get(code) if isinstance(r.get('data'), dict) else None
            if isinstance(bars, list):
                return bars
            if isinstance(r.get('data'), list):
                return r['data']
        return []

    def send_user_block(self, block_code, stocks, show=True):
        provider = self._require_active_provider()
        return provider.send_user_block(block_code, stocks, show=show)

    def create_sector(self, block_code, block_name):
        provider = self._require_active_provider()
        return provider.create_sector(block_code, block_name)

    def clear_sector(self, block_code):
        provider = self._require_active_provider()
        return provider.clear_sector(block_code)

    def get_financial_data(self, codes, fields):
        provider = self._require_active_provider()
        return provider.get_financial_data(codes, fields)

    def get_replay_data(self, codes, current_time, period='1min'):
        provider = self._require_active_provider()
        return provider.get_replay_data(codes, current_time, period=period)

    def resample_kline(self, kline_1min, target_period):
        provider = self._require_active_provider()
        return provider.resample_kline(kline_1min, target_period)

    def get_kline_batch(self, codes, base_period, start, end):
        """批量获取 K 线数据（兼容 KLineReplayEngine）。

        Args:
            codes: 股票代码列表
            base_period: 基础周期 ('1min' / '5min' / '1d' 等)
            start: 起始日期 'YYYY-MM-DD' 或 'YYYY-MM-DD HH:MM:SS'
            end: 结束日期

        Returns:
            {code: [bar_dict, ...], ...}

        Raises:
            DataSourceUnavailableError: 无可用 active provider。
        """
        provider = self._require_active_provider()
        method = getattr(provider, '_get_kline_batch', None) or getattr(provider, 'get_kline_batch', None)
        if method is None:
            return {}
        return method(codes, base_period, start, end)

    # ------------------------------------------------------------------
    # 兼容老 TqAdapter 命名（快照 / 财务）
    # ------------------------------------------------------------------
    # 公式评估方法已移除（Task 10 / SubTask 10.1），请使用 FormulaRouter。

    def get_stock_info(self, codes):
        """获取股票基本信息（名称/代码）。"""
        provider = self._require_active_provider()
        return provider.get_snapshot(codes)

    def get_market_snapshot(self, codes):
        """获取行情快照（兼容老 API）。"""
        return self.get_snapshot(codes)

    def get_stock_table_data(self, codes, col_ids, stk_info=None, hold_sec=0):
        """获取股票列表数据（按 DZH_COL_MAP 列裁剪）。
        返回 row 同时支持 col_id（如 '2'/'14'）和 col key（如 'code'/'enter_price'）两种访问方式，
        兼容老代码的 row['code'] 访问模式。
        """
        snap = self.get_snapshot(codes) if codes else {}
        # 把 stk_info 索引到 code → info 字典，便于读取 enter_price 等字段
        info_by_code: Dict[str, Dict[str, Any]] = {}
        if isinstance(stk_info, list):
            for it in stk_info:
                if isinstance(it, dict):
                    c = it.get('label') or it.get('code') or it.get('t')
                    if c:
                        info_by_code[str(c)] = it
        elif isinstance(stk_info, dict):
            for k, v in stk_info.items():
                info_by_code[str(k)] = v if isinstance(v, dict) else {'p': v}
        rows: List[Dict[str, Any]] = []
        for idx, code in enumerate(codes or []):
            s = snap.get(code, {}) if isinstance(snap, dict) else {}
            info = info_by_code.get(code, {}) or info_by_code.get(code.split('.')[0], {})
            row: Dict[str, Any] = {}
            for cid in (col_ids or []):
                col_def = DZH_COL_MAP.get(cid, {})
                key = col_def.get('key', '')
                col_name = col_def.get('name', '')
                # 优先从 stk_info 读 enter_price 等持仓字段，否则从快照读
                # HARDCODED BLOCK: key field dispatch 无法纯表驱动。
                # 理由：DZH 列字段提取涉及多源快照/stk_info 的兼容计算（code/name 透传、seq 索引、
                # hold_days 格式化、enter_time 回退、current_price 多字段回退、enter_price 多源读取、
                # profit_pct 盈亏公式等），需要上下文变量与计算语义。
                # 未来可由 data_source_mappings.json 增加 "source_chain"/"formula" 字段并引入
                # 受控表达式引擎后迁移；当前保持显式分支以兼容老代码的多种访问方式。
                if key == 'code':
                    val = code
                elif key == 'name':
                    val = s.get('name') or info.get('name') or code
                elif key == 'seq':
                    val = idx + 1
                elif key == 'hold_days':
                    val = _format_hold_days(hold_sec) if hold_sec else 0
                elif key == 'enter_time':
                    val = info.get('t') or info.get('enter_time') or s.get('enter_time', '-')
                elif key == 'current_price':
                    cp = s.get('current_price', s.get('latest_price', s.get('price', s.get('close', 0))))
                    val = cp
                elif key == 'enter_price':
                    ep = info.get('p') or info.get('enter_price') or s.get('enter_price')
                    try: val = float(ep) if ep not in (None, '', '-') else '-'
                    except Exception: val = '-'
                elif key == 'profit_pct':
                    cp = s.get('current_price', s.get('latest_price', s.get('price', s.get('close', 0))))
                    ep_raw = info.get('p') or info.get('enter_price')
                    try:
                        ep = float(ep_raw)
                        if ep > 0 and cp:
                            val = round((float(cp) - ep) / ep * 100, 2)
                        else:
                            val = s.get(key, 0)
                    except Exception:
                        val = s.get(key, 0)
                else:
                    val = s.get(key, 0)
                # 同时写入 col_id 和 col key（兼容两种访问方式）
                row[str(cid)] = val
                if key:
                    row[key] = val
                if col_name:
                    row[col_name] = val
            rows.append(row)
        return {"success": True, "data": rows, "columns": [
            {'id': cid, 'name': DZH_COL_MAP[cid]['name'], 'key': DZH_COL_MAP[cid]['key'], 'type': DZH_COL_MAP[cid]['type']}
            for cid in (col_ids or []) if cid in DZH_COL_MAP
        ]}

    def render_shape(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """渲染形状元素（兼容老 API，TQ DLL 才有真实实现）。"""
        return {
            'shape_type': inputs.get('shape_type', ''),
            'rendered': True,
            'mock_mode': self.mock_mode,
        }

    # ------------------------------------------------------------------
    # 调试 / 兼容
    # ------------------------------------------------------------------

    def get_default_chain(self) -> List[str]:
        """返回降级链名称列表。"""
        if self._manager is None:
            return []
        return self._manager.default_chain

    def get_provider(self, name: str) -> Optional[DataSourceProvider]:
        """按名称获取 provider 实例。"""
        if self._manager is None:
            return None
        return self._manager.get_provider(name)


def create_tq_adapter(mock_mode: bool = False) -> TqAdapter:
    """工厂函数（兼容老 API）。"""
    return TqAdapter(mock_mode=mock_mode)
