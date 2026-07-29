"""
数据源提供者统一模块（services.providers 单文件）。

合并自原 services/providers/ 包下的 7 个文件：
    - __init__.py（DataSourceProvider 基类、DataSourceManager、公共工具层）
    - mock_provider.py（MockProvider）
    - dfcf_provider.py（DfcfProvider）
    - hqchart_provider.py（HQChartProvider）
    - akshare_provider.py（AkShareProvider）
    - local_file_provider.py（LocalFileProvider）
    - tq.py（TqConnector / TqDllProvider / TqSdkBridge / TqSdkProvider / TqProvider）

DataSourceProvider 定义了所有数据源必须实现的接口（带默认空实现），
DataSourceManager 负责根据配置动态加载提供者并维护降级链。

事件驱动（unify-stockpool-oop-event-driven spec Task 4.1）：
    - ``DataSourceProvider.__init__`` 接收可选 ``bus: EventBus``；
      非 None 时子类可通过 ``_emit_tick`` 发布 ``TickReceived`` 事件。
    - ``fetch_tick`` / ``fetch_kline`` 为新增抽象方法（带默认空实现），
      保留现有 ``get_snapshot`` / ``get_kline_data`` 签名不变。
"""

import asyncio
import base64
import ctypes
import hashlib
import importlib
import json
import logging
import os
import platform
import random
import re
import struct
import sys
import time
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

try:
    from ..core.event_bus import EventBus, TickReceived
except ImportError:  # services 作为顶层包导入时回退到绝对导入
    from core.event_bus import EventBus, TickReceived

try:
    from ..core.table_engine import get_global_config_store
except ImportError:  # services 作为顶层包导入时回退到绝对导入
    from core.table_engine import get_global_config_store

logger = logging.getLogger(__name__)


# ===========================================================================
# DataSourceProvider 抽象基类
# ===========================================================================


class DataSourceProvider:
    """数据源提供者抽象基类。

    所有方法均提供默认空实现，子类只需覆写自己支持的方法即可。

    事件驱动：
        构造函数接收可选 ``bus``；非 None 时子类内部调用 ``_emit_tick``
        会发布 ``TickReceived`` 事件，供 TickBar 模块订阅。``bus=None``
        时 ``_emit_tick`` 为空操作，保持向后兼容。
    """

    def __init__(
        self,
        bus: Optional[EventBus] = None,
        config: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._bus: Optional[EventBus] = bus
        self._config: Dict[str, Any] = config or {}

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
    # 事件驱动钩子（Task 4.1）
    # ------------------------------------------------------------------

    def _emit_tick(self, tick_data: Dict, code: str, ts: float) -> None:
        """发布 ``TickReceived`` 事件（受保护方法，供子类调用）。

        ``bus=None`` 时为空操作，保持向后兼容。子类可在 ``get_snapshot`` /
        ``fetch_tick`` 等方法内部调用本钩子，将 tick 数据通过事件总线
        分发给 TickBar 模块订阅者。异常隔离由 ``EventBus.publish`` 保证。
        """
        bus = self._bus
        if bus is None:
            return
        try:
            bus.publish(TickReceived(tick_data=tick_data, code=code, ts=ts))
        except Exception as ex:  # pragma: no cover — 防御性兜底
            logger.warning(
                "DataSourceProvider._emit_tick 发布失败 (code=%s): %s",
                code, ex,
            )

    # ------------------------------------------------------------------
    # 事件化抽象方法（Task 4.1：新增 fetch_tick / fetch_kline，默认空实现）
    # ------------------------------------------------------------------

    def fetch_tick(self, code: str) -> Dict:
        """获取单只股票 tick（事件化抽象方法，默认返回空字典）。

        子类可覆写本方法，在返回前调用 ``self._emit_tick(tick_data, code, ts)``
        发布 ``TickReceived`` 事件。保留现有 ``get_snapshot`` 签名不变。
        """
        return {}

    def fetch_kline(self, code: str, period: str, count: int) -> List[Dict]:
        """获取 K 线（事件化抽象方法，默认返回空列表）。

        子类可覆写本方法。保留现有 ``get_kline_data`` 签名不变。
        """
        return []

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

    仅在 MockProvider 不可用时使用。
    正常情况下 DataSourceManager.__init__ 会用完整 MockProvider 替换此实例。
    """

    def is_ready(self) -> bool:
        return True

    def get_mode_info(self) -> str:
        return "mock"


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
                    "module": "meta_core.services.providers",
                    "class": "TqDllProvider"
                }
            }
        }
    """

    def __init__(
        self,
        config: Optional[Dict] = None,
        bus: Optional[EventBus] = None,
    ):
        self._providers: Dict[str, DataSourceProvider] = {}
        self._default_chain: List[str] = []
        self._config = config or {}
        self._bus: Optional[EventBus] = bus

        # 校验配置一致性：data_providers.json 不应包含 default_chain 字段
        # data_source_contract.json 是 default_chain 的唯一真相源
        self._validate_config_consistency()

        # 确保内置 MockProvider 始终可用（优先使用完整版）
        self._providers["mock"] = self._get_full_mock_provider(self._bus)

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
    # 内部辅助
    # ------------------------------------------------------------------

    @staticmethod
    def _get_full_mock_provider(bus: Optional[EventBus] = None) -> DataSourceProvider:
        """返回完整 MockProvider 实例（合并后同文件内直接引用）。

        注意：此处不调用 grant_consent() —— mock 同意必须由用户显式通过
        DataSourceContract.grant_explicit_consent() 授权。
        MockProvider.is_ready() 在未授权前返回 False。
        """
        try:
            return MockProvider(bus=bus)
        except Exception:
            return _StubMockProvider(bus=bus)

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

        通过 services.data.get_default_contract 读取
        config/data_source_contract.json 的 default_chain 字段。
        """
        from .data import get_default_contract
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
                # 优先用 bus 关键字参数注入事件总线（Task 4.1），
                # 兼容尚未改造签名的旧 provider：回退到无参构造。
                try:
                    instance = cls(bus=self._bus)
                except TypeError:
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


# ===========================================================================
# 公共工具层（自原 services/providers/_common.py 合并）
# ===========================================================================
#
# 数据源提供者公共工具：
#   - 二进制公式解码（decode_formula / _extract_formula_from_binary /
#     _is_valid_formula / _extract_text_segments）
#   - 周期/代码映射（PERIOD_MAP / SORTTYPE_MAP / map_period /
#     decode_sorttype / normalize_code / to_dzh_code）
#   - 格式化辅助（_format_timestamp / _format_hold_days / _norm_period）
#   - K 线缓存（KLineDataCache）
#   - 配置加载（_load_config / _CONFIG_CACHE，供 AkShareProvider/DfcfProvider 共用）
# ===========================================================================

PERIOD_MAP = {
    '分笔': 0,
    '1分': 1,
    '5分': 2,
    '15分': 3,
    '30分': 4,
    '60分': 5,
    '日': 6,
    '周': 7,
    '月': 8,
    '1m': 1,
    '5m': 2,
    '15m': 3,
    '30m': 4,
    '60m': 5,
    '1d': 6,
    '1w': 7,
    '1mon': 8,
    'tick': 0,
}

SORTTYPE_MAP: Dict[str, int] = {}


def _extract_text_segments(raw_bytes):
    """提取二进制中所有 ASCII 文本段。"""
    segments = []
    i = 0
    while i < len(raw_bytes):
        if 0x20 <= raw_bytes[i] <= 0x7E:
            start = i
            while i < len(raw_bytes) and 0x20 <= raw_bytes[i] <= 0x7E:
                i += 1
            segments.append({
                'start': start, 'end': i, 'len': i - start,
                'text': raw_bytes[start:i].decode('ascii', errors='replace'),
            })
        else:
            i += 1
    return segments


def _is_valid_formula(text):
    """验证文本是否像有效的 DZH 公式。"""
    if not text or len(text) < 3:
        return False
    illegal_chars = set(r'\`~@#$%^&?{|}=')
    for c in text:
        if c in illegal_chars:
            return False
    alpha_count = sum(1 for c in text if c.isalpha())
    digit_count = sum(1 for c in text if c.isdigit())
    total_len = len(text)
    if alpha_count < 2 and alpha_count + digit_count < 3:
        return False
    alpha_digit_ratio = (alpha_count + digit_count) / total_len if total_len > 0 else 0
    if alpha_digit_ratio < 0.4:
        return False
    valid_starters = [
        r'^[A-Z][A-Za-z0-9]*\(',
        r'^[a-z]{2,3}[\(\)\[\]\s\d]',
        r'^and\s',
        r'^or\s',
        r'^not\(',
        r'^[A-Z][\s]*[><=]',
        r'^[A-Z][\s]*\(',
        r'^[a-z][\s]*[><=]',
        r'^[a-z][\s]*\(',
        r'^[A-Za-z]{2,3}\s+[A-Za-z]',
    ]
    if not any(re.match(p, text) for p in valid_starters):
        return False
    has_paren = '(' in text or ')' in text
    has_comparison = any(op in text for op in ('>', '<', '='))
    has_comma = ',' in text
    if len(text) > 5:
        if not (has_paren or has_comparison or has_comma):
            return False
    if len(text) <= 5:
        if not (has_paren or has_comparison):
            return False
    if re.match(r'^[a-z]{3,5}$', text):
        return False
    return True


def _extract_formula_from_binary(raw_bytes):
    """从 DZH 二进制 indi 数据中提取公式文本。

    策略：从末尾查找 ;\\0 模式定位公式起始点，扩展提取完整文本段。
    降级：传统 ASCII 文本段提取。
    """
    if not raw_bytes:
        return ""
    formula_text = ""
    tail = raw_bytes[-64:] if len(raw_bytes) > 64 else raw_bytes
    for end_pos in range(len(tail) - 1, -1, -1):
        if tail[end_pos] == 0x3B:  # ';'
            null_terminated = (end_pos + 1 < len(tail) and tail[end_pos + 1] == 0x00)
            crlf_terminated = (end_pos + 2 < len(tail) and tail[end_pos:end_pos+3] == b';\r\n')
            if null_terminated or crlf_terminated:
                seg_end = end_pos
                seg_start = seg_end
                while seg_start > 0 and (0x20 <= tail[seg_start - 1] <= 0x7E or 0x81 <= tail[seg_start - 1] <= 0xFE):
                    seg_start -= 1
                candidate = tail[seg_start:seg_end + 1].decode('gbk', errors='replace')
                candidate = re.sub(r'^[^a-zA-Z0-9_\(]+', '', candidate)
                if not _is_valid_formula(candidate):
                    for strip_n in range(1, min(4, len(candidate))):
                        test = candidate[strip_n:]
                        if test and _is_valid_formula(test):
                            removed = candidate[:strip_n]
                            if len(removed) <= 2 and (not removed.isalpha() or len(removed) == 1):
                                candidate = test
                                break
                if candidate and _is_valid_formula(candidate):
                    formula_text = candidate
                    break
    if not formula_text:
        segments = _extract_text_segments(raw_bytes)
        for seg in reversed(segments):
            if seg['len'] >= 4 and seg['text'].rstrip().endswith(';'):
                candidate = seg['text'].rstrip('; \r\n\t\0')
                if candidate and _is_valid_formula(candidate):
                    formula_text = candidate
                    break
    if not formula_text:
        segments = _extract_text_segments(raw_bytes)
        for seg in reversed(segments):
            if seg['len'] >= 5 and seg['end'] >= len(raw_bytes) * 0.7:
                candidate = seg['text'].rstrip('; \r\n\t\0')
                if candidate and _is_valid_formula(candidate):
                    formula_text = candidate
                    break
        if not formula_text and segments:
            longest = max(segments, key=lambda s: s['len'])
            if longest['len'] >= 3:
                candidate = longest['text'].rstrip('; \r\n\t\0')
                if candidate and _is_valid_formula(candidate):
                    formula_text = candidate
    return formula_text


def decode_formula(indi_b64: str, ency: int = 0) -> str:
    """解码 DZH base64 编码的公式文本。

    支持 ency XOR 解密与 GBK 编码：先 XOR 解密（若 ency != 0），
    再从二进制中提取公式文本，最后 GBK 解码。
    降级：_extract_formula_from_binary / UTF-8 文本解码。

    TODO(Task 23.1): 此函数已复制到 converters/_common.py（消除 converters/dzh.py
    跨层违规 import）。本处保留以避免破坏其他历史调用方；新代码应从
    ``converters._common`` 导入。后续可在确认无外部调用后删除此副本。
    """
    if not indi_b64 or indi_b64 == "0;":
        return ''
    try:
        raw = base64.b64decode(indi_b64)
    except Exception:
        return ''
    if not raw:
        return ''

    # XOR 解密
    if ency != 0:
        ency_bytes = struct.pack('<q', ency)
        raw = bytes(raw[i] ^ ency_bytes[i % 8] for i in range(len(raw)))

    # 查找终止符
    term_pos = -1
    target = b';\x00'
    pos = raw.rfind(target)
    if pos >= 0:
        term_pos = pos
    else:
        for i in range(len(raw) - 1, -1, -1):
            if raw[i] == 0x3B:
                if i + 2 < len(raw) and raw[i + 1:i + 3] == b'\r\n':
                    term_pos = i
                    break
                if i + 1 < len(raw) and raw[i + 1] == 0x0A:
                    term_pos = i
                    break
        if term_pos < 0:
            formula = _extract_formula_from_binary(raw)
            if formula:
                return formula
            try:
                return raw.decode('gbk', errors='replace')
            except Exception:
                try:
                    return raw.decode('utf-8', errors='replace')
                except Exception:
                    return ''

    # 从终止符向前搜索连续文本字节
    text_start = term_pos
    while text_start > 0:
        b = raw[text_start - 1]
        if 0x20 <= b <= 0x7E:
            text_start -= 1
        elif 0x81 <= b <= 0xFE:
            text_start -= 1
        else:
            break

    if term_pos - text_start < 2:
        formula = _extract_formula_from_binary(raw)
        if formula:
            return formula
        return ''

    formula_bytes = raw[text_start:term_pos + 1]
    try:
        formula = formula_bytes.decode('gbk')
    except Exception:
        formula = formula_bytes.decode('gbk', errors='replace')

    clean = re.sub(r'^[^a-zA-Z0-9_\(\u4e00-\u9fff]+', '', formula)
    return clean


def map_period(cycle: str) -> int:
    return PERIOD_MAP.get(cycle, 6)


def decode_sorttype(sorttype: str) -> int:
    return SORTTYPE_MAP.get(sorttype, 0)


def normalize_code(code: str) -> str:
    if not code:
        return code
    code = code.strip()
    if '.' in code:
        return code.upper()
    if code[:2].upper() in ('SH', 'SZ', 'BJ'):
        return code[2:] + '.' + code[:2].upper()
    return code


def to_dzh_code(code: str) -> str:
    if not code:
        return code
    code = code.strip()
    if '.' in code:
        parts = code.split('.')
        return parts[1].upper() + parts[0]
    return code.upper()


def _format_timestamp(ts):
    if not ts or ts <= 0:
        return '-'
    try:
        from datetime import datetime
        return datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M')
    except Exception:
        return '-'


def _format_hold_days(hold_sec):
    if not hold_sec or hold_sec <= 0:
        return '-'
    days = hold_sec / 86400
    if days >= 1:
        return f'{int(days)}天'
    hours = hold_sec / 3600
    if hours >= 1:
        return f'{int(hours)}时'
    return f'{int(hold_sec / 60)}分'


def _norm_period(period: str) -> str:
    _map = {'1min': '1m', '5min': '5m', '15min': '15m', '30min': '30m', '60min': '60m', 'day': '1d'}
    return _map.get(period, period)


class KLineDataCache:
    def __init__(self, max_size: int = 500):
        self._cache: Dict[str, List[Dict]] = {}
        self._max_size = max_size

    def _make_key(self, code: str, period: str) -> str:
        return f"{code}|{period}"

    def get(self, code: str, period: str) -> Optional[List[Dict]]:
        key = self._make_key(code, period)
        return self._cache.get(key)

    def put(self, code: str, period: str, bars: List[Dict]):
        if len(self._cache) >= self._max_size:
            self._cache.pop(next(iter(self._cache)))
        key = self._make_key(code, period)
        self._cache[key] = bars

    def has(self, code: str, period: str) -> bool:
        return self._make_key(code, period) in self._cache

    def clear(self):
        self._cache.clear()


# ===========================================================================
# 配置加载工具：已统一到 ConfigStore.get_table(name)（Task 9.1）
# 模块级 _load_config 帮助函数已删除，调用方通过 get_global_config_store().get_table(name) 访问
# ===========================================================================


# ===========================================================================
# 共享常量（去重：原 tq.py 和 mock_provider.py 中重复定义，现合并为一份）
# ===========================================================================

MARKET_ID_MAP = {
    'SH#上证A股': 1,
    'SH#上证B股': 1,
    'SZ#深证A股': 0,
    'SZ#深证B股': 0,
    'BJ#北证A股': 2,
    'SH#上证指数': 1,
    'SZ#深证指数': 0,
    'SZ#中小企业': 0,
    'SZ#创业板': 0,
    'B$#板块指数': 1,
    'B$#热门概念': 1,
}

SHORT_NAME_TO_MARKET_ID = {
    'sh_a': 1,
    'sz_a': 0,
    'bj_a': 2,
    'sh_b': 1,
    'sz_b': 0,
    'sme': 0,
    'gem': 0,
    'sector_index': 1,
    'hot_concept': 1,
}

DZH_TO_SHORT = {
    'SH#上证A股': 'sh_a',
    'SH#上证B股': 'sh_b',
    'SZ#深证A股': 'sz_a',
    'SZ#深证B股': 'sz_b',
    'BJ#北证A股': 'bj_a',
    'SZ#中小企业': 'sme',
    'SZ#创业板': 'gem',
    'B$#板块指数': 'sector_index',
    'B$#热门概念': 'hot_concept',
}

SHORT_TO_DZH = {v: k for k, v in DZH_TO_SHORT.items()}

_PERIOD_INT_TO_STR: Dict[int, str] = {
    0: 'tick', 1: '1m', 2: '5m', 3: '15m', 4: '30m',
    5: '60m', 6: '1d', 7: '1w', 8: '1mon',
}

_PERIOD_STR_TO_INT: Dict[str, int] = {v: k for k, v in _PERIOD_INT_TO_STR.items()}

DZH_COL_MAP = {
    2: {'name': '代码', 'key': 'code', 'type': 'string'},
    -1: {'name': '名称', 'key': 'name', 'type': 'string'},
    -2: {'name': '最新价', 'key': 'latest_price', 'type': 'number'},
    -3: {'name': '涨跌幅', 'key': 'change_pct', 'type': 'number'},
    -5: {'name': '涨跌额', 'key': 'change_amt', 'type': 'number'},
    -6: {'name': '成交量', 'key': 'volume', 'type': 'number'},
    1: {'name': '序号', 'key': 'seq', 'type': 'number'},
    7: {'name': '入池时间', 'key': 'enter_time', 'type': 'string'},
    8: {'name': '现价', 'key': 'current_price', 'type': 'number'},
    10: {'name': '收益率', 'key': 'profit_pct', 'type': 'number'},
    14: {'name': '入池价', 'key': 'enter_price', 'type': 'number'},
    17: {'name': '最大收益', 'key': 'max_profit', 'type': 'number'},
    24: {'name': '换手率', 'key': 'turnover_rate', 'type': 'number'},
    45: {'name': '保留天数', 'key': 'hold_days', 'type': 'number'},
    101: {'name': 'DDX连续飘红天数', 'key': 'ddx_red_days', 'type': 'number'},
    108: {'name': '量比', 'key': 'volume_ratio', 'type': 'number'},
    251: {'name': '特大单买入', 'key': 'huge_buy', 'type': 'number'},
    285: {'name': '大单买入', 'key': 'big_buy', 'type': 'number'},
    287: {'name': 'BBD', 'key': 'bbd', 'type': 'number'},
    401: {'name': 'DDX', 'key': 'ddx', 'type': 'number'},
}


def _resolve_market_id(market_key: str) -> int:
    """将市场名称（大智慧格式或短名）解析为 market_id。"""
    if market_key in SHORT_NAME_TO_MARKET_ID:
        return SHORT_NAME_TO_MARKET_ID[market_key]
    if market_key in MARKET_ID_MAP:
        return MARKET_ID_MAP[market_key]
    return 0


# ===========================================================================
# MockProvider —— 始终就绪的纯 Mock 数据源提供者
# ===========================================================================
#
# 所有方法均使用确定性随机（seed 由 code/formula 决定），
# 确保相同输入始终产出相同结果，便于测试与回放。
# ===========================================================================

# Mock 数据配置路径（已调整：原 parents[2] → parents[1]）
_MOCK_CONFIG_PATH = Path(__file__).resolve().parents[1] / 'config' / 'data' / 'mock_data.json'

_FALLBACK_STOCK_NAMES = {
    '600000.SH': '浦发银行', '600004.SH': '白云机场', '600005.SH': 'ST东电',
    '600006.SH': '东风汽车', '600007.SH': '中国国贸', '600008.SH': '首创股份',
    '600009.SH': '上海机场', '600010.SH': '包钢股份', '600011.SH': '华能国际',
    '600012.SH': '皖通高速', '600015.SH': '华夏银行', '600016.SH': '民生银行',
    '600018.SH': '上港集团', '600019.SH': '宝钢股份', '600020.SH': '中原高速',
    '600021.SH': '上海电力', '600022.SH': '山东钢铁', '600023.SH': '浙能电力',
    '600025.SH': '华能水电', '600026.SH': '中远海能', '600027.SH': '华电国际',
    '600028.SH': '中国石化', '600029.SH': '南方航空', '600030.SH': '中信证券',
    '600031.SH': '三一重工', '600036.SH': '招商银行', '600048.SH': '保利发展',
    '600050.SH': '中国联通', '600104.SH': '上汽集团', '600115.SH': '东方航空',
    '600150.SH': '中国船舶', '600276.SH': '恒瑞医药', '600309.SH': '万华化学',
    '600346.SH': '恒力石化', '600406.SH': '国电南瑞', '600436.SH': '片仔癀',
    '600519.SH': '贵州茅台', '600585.SH': '海螺水泥', '600588.SH': '用友网络',
    '600690.SH': '海尔智家', '600745.SH': '闻泰科技', '600809.SH': '山西汾酒',
    '600837.SH': '海通证券', '600893.SH': '航发动力', '600900.SH': '长江电力',
    '601012.SH': '隆基绿能', '601088.SH': '中国神华', '601166.SH': '兴业银行',
    '601318.SH': '中国平安', '601398.SH': '工商银行', '601628.SH': '中国人寿',
    '601668.SH': '中国建筑', '601688.SH': '华泰证券', '601728.SH': '中国电信',
    '601857.SH': '中国石油', '601888.SH': '中国中免', '601899.SH': '紫金矿业',
    '601919.SH': '中远海控', '601985.SH': '中国核电', '603259.SH': '药明康德',
    '000001.SZ': '平安银行', '000002.SZ': '万科A', '000063.SZ': '中兴通讯',
    '000100.SZ': 'TCL科技', '000333.SZ': '美的集团', '000338.SZ': '潍柴动力',
    '000425.SZ': '徐工机械', '000538.SZ': '云南白药', '000568.SZ': '泸州老窖',
    '000596.SZ': '古井贡酒', '000625.SZ': '长安汽车', '000651.SZ': '格力电器',
    '000661.SZ': '长春高新', '000725.SZ': '京东方A', '000768.SZ': '中航西飞',
    '000776.SZ': '广发证券', '000783.SZ': '长江证券', '000786.SZ': '北新建材',
    '000800.SZ': '一汽解放', '000858.SZ': '五粮液', '000876.SZ': '新 希 望',
    '000895.SZ': '双汇发展', '000898.SZ': '鞍钢股份', '000938.SZ': '紫光股份',
    '000963.SZ': '华东医药', '000977.SZ': '浪潮信息', '001979.SZ': '招商蛇口',
    '002001.SZ': '新和成', '002007.SZ': '华兰生物', '002024.SZ': '苏宁易购',
    '002027.SZ': '分众传媒', '002049.SZ': '紫光国微', '002120.SZ': '韵达股份',
    '002142.SZ': '宁波银行', '002179.SZ': '中航光电', '002230.SZ': '科大讯飞',
    '002241.SZ': '歌尔股份', '002304.SZ': '洋河股份', '002352.SZ': '顺丰控股',
    '002415.SZ': '海康威视', '002460.SZ': '赣锋锂业', '002475.SZ': '立讯精密',
    '002493.SZ': '荣盛石化', '002555.SZ': '三七互娱', '002594.SZ': '比亚迪',
    '002601.SZ': '龙蟒佰利', '002607.SZ': '中公教育', '002709.SZ': '天赐材料',
    '002714.SZ': '牧原股份', '002736.SZ': '国信证券', '002812.SZ': '恩捷股份',
    '002841.SZ': '视源股份', '003816.SZ': '中国广核',
}

_FALLBACK_MARKET_STOCKS = {
    'SH#上证A股': [f'60{i:04d}.SH' for i in range(0, 200, 3)],
    'SH#上证B股': [f'900{i:03d}.SH' for i in range(1, 55, 3)],
    'SZ#深证A股': [f'000{i:03d}.SZ' for i in range(1, 100, 3)] + [f'001{i:03d}.SZ' for i in range(1, 50, 3)] + [f'002{i:03d}.SZ' for i in range(1, 100, 3)] + [f'003{i:03d}.SZ' for i in range(1, 50, 3)] + [f'300{i:03d}.SZ' for i in range(1, 100, 3)],
    'SZ#深证B股': [f'200{i:03d}.SZ' for i in range(1, 55, 3)],
    'BJ#北证A股': [f'8{i:05d}.BJ' for i in range(1, 50, 2)] + [f'4{i:05d}.BJ' for i in range(1, 50, 2)],
    'SH#上证指数': ['000001.SH', '000002.SH', '000003.SH', '000004.SH', '000005.SH'],
    'SZ#深证指数': ['399001.SZ', '399002.SZ', '399003.SZ', '399004.SZ', '399005.SZ'],
    'SZ#中小企业': [f'002{i:03d}.SZ' for i in range(1, 100, 5)],
    'SZ#创业板': [f'300{i:03d}.SZ' for i in range(1, 100, 5)],
    'B$#板块指数': [f'88{i:04d}.SH' for i in range(1, 30)],
    'B$#热门概念': [f'88{i:04d}.SH' for i in range(100, 130)],
}


def _load_mock_config() -> dict:
    """加载 mock_data.json（mock 专用，不接受参数，与通用 _load_config 区分）。"""
    try:
        if _MOCK_CONFIG_PATH.exists():
            with open(_MOCK_CONFIG_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception as e:
        logger.debug("Failed to load mock_data.json: %s", e)
    return {}


def _build_stock_names_from_config(cfg: dict) -> dict:
    """Try to build _MOCK_STOCK_NAMES from config; return empty dict if not possible.

    从 config 的 sector_stocks 中提取代码列表，然后用 _FALLBACK_STOCK_NAMES
    中的真实名称填充，找不到名称的代码使用 code.split('.')[0] 作为默认值。
    """
    result = {}
    sector_stocks = cfg.get('sector_stocks', {})
    for stocks in sector_stocks.values():
        for code in stocks:
            if '.' in code and code not in result:
                # 优先使用 _FALLBACK_STOCK_NAMES 中的真实名称
                result[code] = _FALLBACK_STOCK_NAMES.get(code, code.split('.')[0])
    return result


def _build_market_stocks_from_config(cfg: dict) -> dict:
    """Try to build _MOCK_MARKET_STOCKS from config; return empty dict if not possible.

    Config format: {prefix: "60", suffix: "SH", range_start: 0, range_end: 1999}
    Generates codes like: 600000.SH, 600003.SH, ..., 601998.SH
    width = 6 - len(prefix) ensures total code length is always 6 digits.
    """
    result = {}
    market_scopes = cfg.get('market_scopes', {})
    scope_to_dzh = {
        'sh_a': 'SH#上证A股',
        'sz_a': 'SZ#深证A股',
        'gem': 'SZ#创业板',
        'star': 'SH#科创板',
        'all_a': 'SH#全部A股',
    }
    for scope_key, dzh_key in scope_to_dzh.items():
        entries = market_scopes.get(scope_key, [])
        codes = []
        for entry in entries:
            prefix = entry.get('prefix', '')
            suffix = entry.get('suffix', '')
            range_start = entry.get('range_start', 0)
            range_end = entry.get('range_end', 0)
            if prefix and suffix and range_end > range_start:
                prefix_len = len(prefix)
                width = 6 - prefix_len  # ensure 6-digit TDX code
                for i in range(range_start, range_end + 1, 3):
                    codes.append(f'{prefix}{i:0{width}d}.{suffix}')
        if codes:
            result[dzh_key] = codes
    return result


_mock_cfg = _load_mock_config()

# 合并：配置构建的名称作为基础，_FALLBACK_STOCK_NAMES 补充缺失的条目
_mock_names_from_cfg = _build_stock_names_from_config(_mock_cfg)
_MOCK_STOCK_NAMES = dict(_FALLBACK_STOCK_NAMES)
_MOCK_STOCK_NAMES.update(_mock_names_from_cfg)  # cfg 覆盖 fallback

_mock_markets_from_cfg = _build_market_stocks_from_config(_mock_cfg)
_MOCK_MARKET_STOCKS = dict(_FALLBACK_MARKET_STOCKS)
_MOCK_MARKET_STOCKS.update(_mock_markets_from_cfg)


# ══════════════════════════════════════════════════════════════════════
#  MockProvider
# ══════════════════════════════════════════════════════════════════════


class MockProvider(DataSourceProvider):
    """模拟数据源提供者 — 必须 explicit_only。

    关键契约：
    - is_ready() 在未授权时返回 False（禁止自动就绪）
    - grant_consent() 后返回 True
    - _probe() 仍返回 ready=True 以兼容契约探测（探测归探测，授权归授权）
    """

    def __init__(self, bus: Optional[EventBus] = None, config: Optional[Dict[str, Any]] = None):
        super().__init__(bus=bus, config=config)
        self._kline_cache = KLineDataCache()
        self._method_cache: Dict[str, Any] = {}
        # explicit_only 授权状态：未授权时 is_ready() 返回 False
        self._state = "not_consented"
        self._explicit_consent = False

    # ------------------------------------------------------------------
    # 基础状态
    # ------------------------------------------------------------------

    def is_ready(self) -> bool:
        # 关键契约：未授权时永远不 ready（防止 mock 被自动使用）
        if not self._explicit_consent:
            return False
        return self._state == "ready"

    def grant_consent(self):
        """由 DataSourceContract.grant_explicit_consent() 同步调用。"""
        self._explicit_consent = True
        self._state = "ready"

    def revoke_consent(self):
        """撤销显式授权。"""
        self._explicit_consent = False
        self._state = "not_consented"

    def get_mode_info(self) -> str:
        return "mock"

    def create_tick_source(self, codes, clock_start: float = 0.0, **kwargs) -> Any:
        """创建仿真 TickSource（MockDataSource）。

        Task 1：将 MockProvider 生成的行情统一收敛到 ``MockDataSource``，
        由 ``TickSource.next_ticks`` 驱动核心循环，所有输出代码统一为 ``fz`` 前缀。
        G5：tick 定时器注册到 EventDriver 统一优先队列。
        """
        try:
            from ..core.domain import MockDataSource
        except ImportError:
            try:
                from core.domain import MockDataSource
            except ImportError:
                from services.core.domain import MockDataSource
        return MockDataSource(codes=codes, clock_start=clock_start, **kwargs)

    def _probe(self) -> Dict[str, Any]:
        """契约探测（Task 6）：mock provider 始终就绪（_probe 不受授权门控）。

        授权校验在 probe_or_raise / probe 层进行；本方法仅作基础探测。

        Returns:
            {"ready": True, "provider": "mock"}
        """
        return {"ready": True, "provider": "mock", "explicit_only": True}

    # ------------------------------------------------------------------
    # 行情数据
    # ------------------------------------------------------------------

    def resolve_market(self, markets) -> Dict[str, List[str]]:
        if markets is None:
            return {}
        if isinstance(markets, str):
            markets = self._parse_attrtext(markets)
        if not isinstance(markets, list):
            return {}
        result = {}
        for m in markets:
            dzh_key = SHORT_TO_DZH.get(m, m)
            stocks = _MOCK_MARKET_STOCKS.get(dzh_key)
            if stocks:
                result[m] = list(stocks)
            else:
                all_stocks = sorted(_MOCK_STOCK_NAMES.keys())
                k = min(random.randint(1, 4), len(all_stocks))
                result[m] = sorted(random.sample(all_stocks, k=k))
        return result

    def get_kline_data(self, codes, period=None, start_date=None, end_date=None, **kwargs) -> Dict:
        """获取K线数据，兼容旧签名 (period: int) 和新签名 (period: str)。"""
        count = kwargs.get('count')
        if isinstance(period, int) or isinstance(count, int):
            actual_count = count if count is not None else start_date
            if not isinstance(actual_count, int):
                actual_count = 3
            return self._get_kline_data_legacy(codes, period, actual_count)
        if not codes:
            return {}
        if isinstance(codes, str):
            codes = [codes]
        result = {}
        norm_period = _norm_period(period or '1d')
        for code in codes:
            cached = self._kline_cache.get(code, norm_period)
            if cached is not None:
                result[code] = cached
                continue
            bars = self._generate_kline(code, norm_period, start_date, end_date)
            self._kline_cache.put(code, norm_period, bars)
            result[code] = bars
        return result

    def _generate_kline(self, code, period, start_date=None, end_date=None) -> List[Dict]:
        """生成确定性随机 K 线数据。"""
        seed_str = f"{code}_{period}_{start_date}_{end_date}"
        seed = int(hashlib.md5(seed_str.encode('utf-8')).hexdigest()[:8], 16)
        rng = random.Random(seed)

        # 根据周期生成合适数量的K线
        period_count_map = {'1m': 240, '5m': 48, '15m': 16, '30m': 8, '60m': 4, '1d': 1}
        base_count = period_count_map.get(period, 1)
        # 默认生成 30 天的数据
        days = 30
        total_count = base_count * days

        bars = []
        base_price = 10.0 + (seed % 90)
        base_time = datetime(2024, 1, 1, 9, 30)

        period_minutes_map = {'1m': 1, '5m': 5, '15m': 15, '30m': 30, '60m': 60, '1d': 1440}
        minutes_step = period_minutes_map.get(period, 1440)

        current_time = base_time
        current_price = base_price

        for i in range(total_count):
            # 简单的随机游走
            change_pct = rng.gauss(0, 0.02)
            open_price = current_price
            close_price = open_price * (1 + change_pct)
            high_price = max(open_price, close_price) * (1 + abs(rng.gauss(0, 0.005)))
            low_price = min(open_price, close_price) * (1 - abs(rng.gauss(0, 0.005)))
            volume = int(rng.uniform(100000, 1000000))
            amount = volume * (open_price + close_price) / 2

            bars.append({
                'open': round(open_price, 2),
                'high': round(high_price, 2),
                'low': round(low_price, 2),
                'close': round(close_price, 2),
                'volume': volume,
                'amount': round(amount, 2),
                'time': current_time.strftime('%Y-%m-%d %H:%M:%S'),
            })

            current_price = close_price
            current_time = current_time + timedelta(minutes=minutes_step)

        return bars

    def _get_kline_data_legacy(self, codes, period: int, count: int) -> Dict:
        """旧签名兼容：period 为 int。"""
        if isinstance(codes, str):
            codes = [codes]
        period_str = _PERIOD_INT_TO_STR.get(period, '1d')
        return self.get_kline_data(codes, period=period_str, start_date=None, end_date=None, count=count)

    def get_snapshot(self, codes) -> Dict[str, Dict]:
        if isinstance(codes, str):
            codes = [codes]
        result = {}
        for code in codes:
            seed = int(hashlib.md5(code.encode('utf-8')).hexdigest()[:8], 16)
            rng = random.Random(seed)
            base_price = 10.0 + (seed % 90)
            current_price = base_price * (1 + rng.gauss(0, 0.05))
            pre_close = base_price
            change_pct = round((current_price - pre_close) / pre_close * 100, 2) if pre_close else 0
            change_amt = round(current_price - pre_close, 2)
            result[code] = {
                'name': _MOCK_STOCK_NAMES.get(code, code.split('.')[0] if '.' in code else code),
                'close': round(current_price, 2),
                'price': round(current_price, 2),
                'now': round(current_price, 2),
                'open': round(base_price, 2),
                'high': round(max(current_price, base_price) * 1.01, 2),
                'low': round(min(current_price, base_price) * 0.99, 2),
                'pre_close': round(pre_close, 2),
                'change_pct': change_pct,
                'change_amt': change_amt,
                'rise': change_pct,
                'volume': int(rng.uniform(100000, 1000000)),
                'amount': round(current_price * rng.uniform(100000, 1000000), 2),
                'turnover_rate': round(rng.uniform(0.5, 5.0), 2),
                'volume_ratio': round(rng.uniform(0.5, 2.0), 2),
            }
        return result

    # ------------------------------------------------------------------
    # 板块 / 股票列表
    # ------------------------------------------------------------------

    def get_block_members(self, block_code) -> List[str]:
        # 返回与该板块相关的 mock 股票
        all_stocks = sorted(_MOCK_STOCK_NAMES.keys())
        seed = int(hashlib.md5(str(block_code).encode('utf-8')).hexdigest()[:8], 16)
        rng = random.Random(seed)
        k = min(rng.randint(5, 20), len(all_stocks))
        return sorted(rng.sample(all_stocks, k=k))

    def get_stock_list_by_type(self, list_type, customblockname='', **kwargs) -> List[Dict]:
        all_stocks = sorted(_MOCK_STOCK_NAMES.keys())
        result = []
        for code in all_stocks:
            parts = code.split('.')
            result.append({
                'code': parts[0],
                'name': _MOCK_STOCK_NAMES.get(code, parts[0]),
                'market': parts[1] if len(parts) > 1 else '',
            })
        return result

    def get_sector_list(self, list_type=1) -> List[Dict]:
        return [
            {'sector_code': '880001', 'sector_name': '银行', 'category': 'industry', 'member_count': 10},
            {'sector_code': '880002', 'sector_name': '房地产', 'category': 'industry', 'member_count': 8},
            {'sector_code': '880003', 'sector_name': '医药', 'category': 'industry', 'member_count': 15},
        ]

    def get_sector_stocks(self, sector_code, block_type=0) -> List[str]:
        return self.get_block_members(sector_code)

    # ------------------------------------------------------------------
    # 公式评估
    # ------------------------------------------------------------------

    def eval_indicator(self, codes, formula_text, period, sorttype=0) -> Dict:
        if isinstance(codes, str):
            codes = [codes]
        result = {}
        for code in codes:
            seed = int(hashlib.md5(f"{code}_{formula_text}".encode('utf-8')).hexdigest()[:8], 16)
            rng = random.Random(seed)
            result[code] = rng.uniform(-10, 10)
        if sorttype > 0:
            sorted_items = sorted(result.items(), key=lambda x: x[1], reverse=True)
            result = dict(sorted_items[:sorttype])
        return {'result': result, 'selected_count': len(result)}

    def eval_formula_xg(self, formula_name, formula_arg='', stock_list=None,
                        period='1d', count=0, dividend_type=1,
                        start_time='', end_time='') -> Dict:
        if not stock_list:
            stock_list = list(_MOCK_STOCK_NAMES.keys())[:50]
        result = {}
        for code in stock_list:
            seed = int(hashlib.md5(f"{code}_{formula_name}".encode('utf-8')).hexdigest()[:8], 16)
            rng = random.Random(seed)
            result[code] = rng.random() > 0.7  # 30% 概率选中
        selected = [c for c, v in result.items() if v]
        return {"success": True, "result": result, "selected_codes": selected}

    def eval_formula_zb(self, formula_name, formula_arg='', stock_list=None,
                        period='1d', count=5, dividend_type=1,
                        return_count=1, return_date=False,
                        xsflag=6, start_time='', end_time='') -> Dict:
        if not stock_list:
            stock_list = list(_MOCK_STOCK_NAMES.keys())[:50]
        result = {}
        result_detail = {}
        for code in stock_list:
            seed = int(hashlib.md5(f"{code}_{formula_name}".encode('utf-8')).hexdigest()[:8], 16)
            rng = random.Random(seed)
            values = [rng.uniform(-10, 10) for _ in range(count)]
            result[code] = values
            result_detail[code] = {'MA1': values, 'MA2': [v * 0.9 for v in values]}
        return {"success": True, "result": result, "result_detail": result_detail}

    # ------------------------------------------------------------------
    # 财务 / 回放 / 重采样
    # ------------------------------------------------------------------

    def get_financial_data(self, codes, fields) -> Dict:
        if isinstance(codes, str):
            codes = [codes]
        result = {}
        for code in codes:
            seed = int(hashlib.md5(code.encode('utf-8')).hexdigest()[:8], 16)
            rng = random.Random(seed)
            data = {f: rng.uniform(1e6, 1e9) for f in fields}
            result[code] = data
        return result

    def get_replay_data(self, codes, current_time, period='1min') -> Dict[str, List[Dict]]:
        return self.get_kline_data(codes, period=period)

    def resample_kline(self, kline_1min, target_period) -> List[Dict]:
        if not kline_1min:
            return []
        try:
            import pandas as pd
            df = pd.DataFrame(kline_1min)
            df['time'] = pd.to_datetime(df['time'])
            df.set_index('time', inplace=True)
            rule_map = {
                '5min': '5min', '5m': '5min',
                '15min': '15min', '15m': '15min',
                '30min': '30min', '30m': '30min',
                '60min': '60min', '60m': '60min',
                'day': 'D', '1d': 'D',
            }
            rule = rule_map.get(target_period, '5min')
            resampled = df.resample(rule).agg({
                'open': 'first',
                'high': 'max',
                'low': 'min',
                'close': 'last',
                'volume': 'sum',
                'amount': 'sum',
            }).dropna()
            result = []
            for idx, row in resampled.iterrows():
                result.append({
                    'open': round(float(row['open']), 2),
                    'high': round(float(row['high']), 2),
                    'low': round(float(row['low']), 2),
                    'close': round(float(row['close']), 2),
                    'volume': int(row['volume']),
                    'amount': round(float(row['amount']), 2),
                    'time': idx.strftime('%Y-%m-%d %H:%M:%S'),
                })
            return result
        except Exception:
            return []

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_attrtext(attrtext: str) -> List[str]:
        if not attrtext:
            return []
        markets = []
        for item in attrtext.split():
            item = item.strip()
            if item and '#' in item:
                markets.append(item)
        return markets


# ===========================================================================
# DfcfProvider —— 东方财富数据源提供者
# ===========================================================================


class DfcfProvider(DataSourceProvider):
    """基于东方财富的数据源提供者。"""

    def __init__(self, bus: Optional[EventBus] = None, config: Optional[Dict[str, Any]] = None):
        super().__init__(bus=bus, config=config)
        self._ready = False
        self._kline_cache = KLineDataCache()
        try:
            import requests
            self._requests = requests
            self._ready = True
            logger.info("DfcfProvider 初始化成功")
        except ImportError:
            logger.warning("requests 未安装，DfcfProvider 不可用")
        except Exception as e:
            logger.warning("DfcfProvider 初始化失败: %s", e)

    # ------------------------------------------------------------------
    # 基础状态
    # ------------------------------------------------------------------

    def is_ready(self) -> bool:
        return self._ready

    def get_mode_info(self) -> str:
        return "dfcf"

    def _probe(self) -> Dict[str, Any]:
        """契约探测（Task 6）：探测 DfcfProvider 是否就绪。"""
        if not self._ready:
            return {
                "ready": False,
                "provider": "dfcf",
                "error": "requests 模块未安装或初始化失败",
            }
        return {"ready": True, "provider": "dfcf"}

    # ------------------------------------------------------------------
    # 行情数据
    # ------------------------------------------------------------------

    def resolve_market(self, markets) -> Dict[str, List[str]]:
        if not self._ready or not markets:
            return {}
        result = {}
        mc_cfg = get_global_config_store().get_table("market_classifications") if get_global_config_store() else {}
        for market in markets:
            dzh_key = mc_cfg.get("short_to_dzh", {}).get(market, market)
            flt = mc_cfg.get("market_filters", {}).get(dzh_key)
            if not flt:
                continue
            prefixes = flt.get("prefixes") or ([flt["prefix"]] if flt.get("prefix") else [])
            if not prefixes:
                continue
            # 东方财富通过 API 获取股票列表，此处简化为根据前缀生成代码
            codes = []
            for prefix in prefixes:
                for i in range(0, 100):
                    code = f"{prefix}{i:04d}"
                    if prefix.startswith('6'):
                        codes.append(f"{code}.SH")
                    elif prefix.startswith(('0', '3')):
                        codes.append(f"{code}.SZ")
                    elif prefix.startswith(('4', '8')):
                        codes.append(f"{code}.BJ")
            result[market] = codes
        return result

    def get_kline_data(self, codes, period=None, start_date=None, end_date=None, **kwargs) -> Dict:
        if not self._ready:
            return {}
        if isinstance(codes, str):
            codes = [codes]
        period = _norm_period(period or '1d')
        result = {}
        for code in codes:
            cached = self._kline_cache.get(code, period)
            if cached is not None:
                result[code] = cached
                continue
            # 东方财富 K 线 API（简化实现，实际应调用 push2 API）
            bars = self._fetch_dfcf_kline(code, period, start_date, end_date)
            if bars:
                self._kline_cache.put(code, period, bars)
            result[code] = bars
        return result

    def _fetch_dfcf_kline(self, code, period, start_date, end_date) -> List[Dict]:
        """通过东方财富 API 获取 K 线数据（简化实现）。"""
        try:
            # 将统一代码转换为东方财富格式
            if '.' in code:
                pure_code, market = code.split('.')
                # 东方财富 market: 0=深, 1=沪
                mkt = 0 if market in ('SZ', 'BJ') else 1
            else:
                pure_code = code
                mkt = 1 if code.startswith('6') else 0

            period_map = {'1m': 1, '5m': 5, '15m': 15, '30m': 30, '60m': 60, '1d': 101, '1w': 102, '1mon': 103}
            klt = period_map.get(period, 101)

            url = "http://push2his.eastmoney.com/api/qt/stock/kline/get"
            params = {
                'secid': f"{mkt}.{pure_code}",
                'fields1': 'f1,f2,f3,f4,f5,f6',
                'fields2': 'f51,f52,f53,f54,f55,f56,f57,f58',
                'klt': klt,
                'fqt': 1,  # 前复权
                'beg': start_date.replace('-', '') if start_date else '0',
                'end': end_date.replace('-', '') if end_date else '20500101',
            }
            resp = self._requests.get(url, params=params, timeout=10)
            data = resp.json()
            klines = data.get('data', {}).get('klines', [])
            bars = []
            for line in klines:
                parts = line.split(',')
                if len(parts) >= 7:
                    bars.append({
                        'open': float(parts[1]),
                        'close': float(parts[2]),
                        'high': float(parts[3]),
                        'low': float(parts[4]),
                        'volume': int(float(parts[5])),
                        'amount': float(parts[6]),
                        'time': parts[0],
                    })
            return bars
        except Exception as e:
            logger.warning("获取 %s K线数据失败: %s", code, e)
            return []

    def get_snapshot(self, codes) -> Dict[str, Dict]:
        if not self._ready:
            return {}
        if isinstance(codes, str):
            codes = [codes]
        result = {}
        for code in codes:
            try:
                if '.' in code:
                    pure_code, market = code.split('.')
                    mkt = 0 if market in ('SZ', 'BJ') else 1
                else:
                    pure_code = code
                    mkt = 1 if code.startswith('6') else 0

                url = "http://push2.eastmoney.com/api/qt/stock/get"
                params = {
                    'secid': f"{mkt}.{pure_code}",
                    'fields': 'f43,f44,f45,f46,f47,f48,f50,f51,f52,f57,f58,f60,f116,f117,f170',
                }
                resp = self._requests.get(url, params=params, timeout=5)
                data = resp.json().get('data', {})
                if not data:
                    continue

                close = data.get('f43', 0) / 100 if data.get('f43') else 0
                pre_close = data.get('f60', 0) / 100 if data.get('f60') else 0
                change_pct = data.get('f170', 0) / 100 if data.get('f170') else 0

                result[code] = {
                    'name': data.get('f58', ''),
                    'close': close,
                    'price': close,
                    'now': close,
                    'open': data.get('f46', 0) / 100 if data.get('f46') else 0,
                    'high': data.get('f44', 0) / 100 if data.get('f44') else 0,
                    'low': data.get('f45', 0) / 100 if data.get('f45') else 0,
                    'pre_close': pre_close,
                    'change_pct': change_pct,
                    'change_amt': round(close - pre_close, 2) if close and pre_close else 0,
                    'rise': change_pct,
                    'volume': data.get('f47', 0),
                    'amount': data.get('f48', 0),
                    'turnover_rate': data.get('f170', 0) / 100 if data.get('f170') else 0,
                }
            except Exception as e:
                logger.warning("获取 %s 快照失败: %s", code, e)
        return result

    # ------------------------------------------------------------------
    # 板块 / 股票列表
    # ------------------------------------------------------------------

    def get_block_members(self, block_code) -> List[str]:
        # 东方财富板块 API（简化实现）
        return []

    def get_stock_list_by_type(self, list_type, customblockname='', **kwargs) -> List[Dict]:
        return []

    def get_sector_list(self, list_type=1) -> List[Dict]:
        return []

    def get_sector_stocks(self, sector_code, block_type=0) -> List[str]:
        return []

    # ------------------------------------------------------------------
    # 公式评估
    # ------------------------------------------------------------------

    def eval_indicator(self, codes, formula_text, period, sorttype=0) -> Dict:
        return {'result': {}, 'inditype': 0}

    def eval_formula_xg(self, formula_name, formula_arg='', stock_list=None,
                        period='1d', count=0, dividend_type=1,
                        start_time='', end_time='') -> Dict:
        return {"success": False, "result": {}, "selected_codes": []}

    def eval_formula_zb(self, formula_name, formula_arg='', stock_list=None,
                        period='1d', count=5, dividend_type=1,
                        return_count=1, return_date=False,
                        xsflag=6, start_time='', end_time='') -> Dict:
        return {"success": False, "result": {}}

    # ------------------------------------------------------------------
    # 财务 / 回放 / 重采样
    # ------------------------------------------------------------------

    def get_financial_data(self, codes, fields) -> Dict:
        return {}

    def get_replay_data(self, codes, current_time, period='1min') -> Dict[str, List[Dict]]:
        return {}

    def resample_kline(self, kline_1min, target_period) -> List[Dict]:
        return []


# ===========================================================================
# HQChartProvider —— HQChartPy2 C++ 引擎封装
# ===========================================================================
#
# 提供基于 HQChartPy2 的指标计算、选股评估等功能，
# 通过 IHQData 接口桥接 C++ 引擎与 Python 数据源。
# ===========================================================================

# _parse_formula_outvars 结果的模块级缓存（key = formula_text）
_PARSE_OUTVARS_CACHE: Dict[str, List[str]] = {}

# HQChartPy2 C++ 引擎：位于项目根目录 ``HQChartPy2/``（绝对可见，无需 sys.path 修改）。
# 项目根目录在 Python 启动时位于 ``sys.path[0]``，``from HQChartPy2 import ...`` 直接生效。
_HQCHART_AVAILABLE = False
try:
    from HQChartPy2 import (  # noqa: E402
        GetAuthorizeInfo,
        GetVersion as _GetVersion,
        LoadAuthorizeInfo,
        Run as _Run,
        SetLog as _SetLog,
    )
    _HQCHART_AVAILABLE = True
except ImportError as e:
    logger.warning("HQChartPy2 导入失败，HQChart 功能不可用: %s", e)


class PERIOD_ID:
    """HQChart 引擎使用的周期 ID 常量。"""

    DAY_ID = 0
    WEEK_ID = 1
    MONTH_ID = 2
    YEAR_ID = 3
    QUARTER_ID = 9
    TWO_WEEK_ID = 21
    MIN1_ID = 4
    MIN5_ID = 5
    MIN15_ID = 6
    MIN30_ID = 7
    MIN60_ID = 8
    TICK_ID = 10  # 分笔


# Python 内部 period int → HQChart period ID
_PYTHON_TO_HQCHART_PERIOD: Dict[int, int] = {
    6: PERIOD_ID.DAY_ID,    # day
    7: PERIOD_ID.WEEK_ID,   # week
    8: PERIOD_ID.MONTH_ID,  # month
    1: PERIOD_ID.MIN1_ID,   # 1min
    2: PERIOD_ID.MIN5_ID,   # 5min
    3: PERIOD_ID.MIN15_ID,  # 15min
    4: PERIOD_ID.MIN30_ID,  # 30min
    5: PERIOD_ID.MIN60_ID,  # 60min
    0: PERIOD_ID.TICK_ID,   # tick
}


class IHQDataImpl:
    """实现 IHQData 接口，连接 C++ 引擎与调用方传入的 K 线数据。

    将 HQChart C++ 引擎的数据请求转发给调用方预先获取的 K 线数据，
    并转换数据格式为引擎所需的数组结构。
    """

    def __init__(self, kline_data=None):
        """初始化 IHQData 实现。

        Args:
            kline_data: K 线数据字典，由调用方通过数据源获取并传入。
                        格式为 {symbol: [bars]}，其中 symbol 为 TQ 格式（如 600000.SH），
                        bars 为每根 K 线的 dict 列表。
        """
        self._kline_data = kline_data or {}

    # ------------------------------------------------------------------
    # 代码标准化
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_to_hqchart(symbol: str) -> str:
        """将代码标准化为 HQChart 格式 (SH600000)。"""
        if not symbol:
            return symbol
        symbol = symbol.strip()
        if '.' in symbol:
            parts = symbol.split('.')
            return parts[1].upper() + parts[0]
        return symbol.upper()

    @staticmethod
    def _normalize_for_tq(symbol: str) -> str:
        """将代码标准化为 TQ 格式 (600000.SH)。"""
        if not symbol:
            return symbol
        symbol = symbol.strip()
        if '.' in symbol:
            return symbol.upper()
        if symbol[:2].upper() in ('SH', 'SZ', 'BJ'):
            return symbol[2:] + '.' + symbol[:2].upper()
        return symbol

    # ------------------------------------------------------------------
    # K 线数据
    # ------------------------------------------------------------------

    def GetKLineData(self, symbol, period, right, jobID):
        """返回 K 线数据，格式为 HQChart 引擎需要的数组字典。

        HQChart 期望格式:
        {
            "open": [...], "high": [...], "low": [...],
            "close": [...], "volume": [...], "amount": [...],
            "date": [...], "time": [...]
        }
        """
        if not self._kline_data:
            return None

        try:
            normalized = self._normalize_for_tq(symbol)
            bars = self._kline_data.get(normalized, [])
            if not bars:
                # 尝试原始 symbol（调用方可能使用了不同的代码格式）
                bars = self._kline_data.get(symbol, [])
            if not bars:
                return None

            open_vals = []
            high_vals = []
            low_vals = []
            close_vals = []
            yclose_vals = []
            volume_vals = []
            amount_vals = []
            date_vals = []

            for bar in bars:
                open_vals.append(bar.get('open', 0))
                high_vals.append(bar.get('high', 0))
                low_vals.append(bar.get('low', 0))
                close_vals.append(bar.get('close', 0))
                yclose_vals.append(bar.get('yclose', bar.get('pre_close', bar.get('open', 0))))
                volume_vals.append(bar.get('volume', 0))
                amount_vals.append(bar.get('amount', 0))
                date_vals.append(bar.get('date', 0))

            return {
                'count': len(bars),
                'date': date_vals,
                'yclose': yclose_vals,
                'open': open_vals,
                'high': high_vals,
                'low': low_vals,
                'close': close_vals,
                'vol': volume_vals,
                'amount': amount_vals,
            }
        except Exception as e:
            logger.debug("GetKLineData error for %s: %s", symbol, e)
            return None

    def GetKLineData2(self, symbol, period, right, callInfo, kdataInfo, jobID):
        pass

    # ------------------------------------------------------------------
    # 财务 / 动态数据 (暂不支持)
    # ------------------------------------------------------------------

    def GetFinance(self, symbol, id, period, right, kcount, jobID):
        return False

    def GetDynainfo(self, symbol, id, period, right, kcount, jobID):
        return False

    def GetCapital(self, symbol, period, right, kcount, jobID):
        return False

    def GetTotalCapital(self, symbol, period, right, kcount, jobID):
        return False

    def GetHisCapital(self, symbol, period, right, kcount, jobID):
        return False

    # ------------------------------------------------------------------
    # 数据分发方法
    # ------------------------------------------------------------------

    def GetDataByNumber(self, symbol, funcName, id, period, right, kcount, jobID):
        if funcName == 'FINANCE':
            return self.GetFinance(symbol, id, period, right, kcount, jobID)
        elif funcName == 'DYNAINFO':
            return self.GetDynainfo(symbol, id, period, right, kcount, jobID)
        return False

    def GetDataByNumbers(self, symbol, funcName, args, period, right, kcount, jobID):
        return False

    def GetDataByName(self, symbol, funcName, period, right, kcount, jobID):
        if funcName == 'CAPITAL':
            return self.GetCapital(symbol, period, right, kcount, jobID)
        elif funcName == 'GetHisCapital':
            return self.GetHisCapital(symbol, period, right, kcount, jobID)
        elif funcName == 'TOTALCAPITAL':
            return self.GetTotalCapital(symbol, period, right, kcount, jobID)
        return False

    def GetDataByString(self, symbol, funcName, period, right, kcount, jobID):
        return False

    def GetGPJYValue(self, symbol, args, period, right, kcount, jobID):
        return False

    # ------------------------------------------------------------------
    # 系统指标脚本
    # ------------------------------------------------------------------

    def GetIndexScript(self, name, callInfo, jobID):
        """返回内置系统指标的脚本定义 (JSON 字符串)。"""
        if name == 'MA':
            index_script = {
                'Name': name,
                'Script': '''
                T1:MA(C,M1);
                T2:MA(C,M2);
                T3:MA(C,M3);
                ''',
                'Args': [
                    {'Name': 'M1', 'Value': 15},
                    {'Name': 'M2', 'Value': 20},
                    {'Name': 'M3', 'Value': 30},
                ],
            }
            return json.dumps(index_script)
        elif name == 'KDJ':
            index_script = {
                'Name': name,
                'Script': '''
                RSV:=(CLOSE-LLV(LOW,N))/(HHV(HIGH,N)-LLV(LOW,N))*100;
                K:SMA(RSV,M1,1);
                D:SMA(K,M2,1);
                J:3*K-2*D;
                ''',
                'Args': [
                    {'Name': 'N', 'Value': 9},
                    {'Name': 'M1', 'Value': 3},
                    {'Name': 'M2', 'Value': 3},
                ],
            }
            return json.dumps(index_script)
        elif name == 'MACD':
            index_script = {
                'Name': name,
                'Script': '''
                DIF:EMA(CLOSE,SHORT)-EMA(CLOSE,LONG);
                DEA:EMA(DIF,MID);
                MACD:(DIF-DEA)*2,COLORSTICK;
                ''',
                'Args': [
                    {'Name': 'SHORT', 'Value': 12},
                    {'Name': 'LONG', 'Value': 26},
                    {'Name': 'MID', 'Value': 9},
                ],
            }
            return json.dumps(index_script)
        return None


class FastHQChart:
    """HQChartPy2 C++ 引擎的静态包装器。

    提供版本查询、初始化、日志设置和公式运行等核心功能。
    """

    _initialized = False

    @staticmethod
    def GetVersion():
        """返回 HQChartPy2 引擎版本字符串。"0.0" 表示不可用。"""
        if not _HQCHART_AVAILABLE:
            return "0.0"
        try:
            version = _GetVersion()
            return "{0}.{1}".format(int(version / 100000), (version % 100000))
        except Exception:
            return "0.0"

    @staticmethod
    def IsAvailable():
        """返回引擎是否可用。"""
        return _HQCHART_AVAILABLE

    @staticmethod
    def SetLog(value):
        """设置引擎日志级别。"""
        if not _HQCHART_AVAILABLE:
            return False
        try:
            return _SetLog(value)
        except Exception:
            return False

    @staticmethod
    def Initialization(Key=None):
        """初始化 HQChartPy2 C++ 引擎。

        Args:
            Key: 可选的授权码，用于激活正式版功能。

        Returns:
            bool: 初始化是否成功。
        """
        if FastHQChart._initialized:
            return True
        if not _HQCHART_AVAILABLE:
            logger.warning("HQChartPy2 不可用，跳过初始化")
            return False

        try:
            str_os = platform.system()
            dll_version = _GetVersion()
            if Key:
                LoadAuthorizeInfo(Key)
            authorize = GetAuthorizeInfo()

            border = "*" * 80
            print(border)
            print("*  欢迎使用HQChartPy2 C++ 技术指标计算引擎")
            print("*  版本号:{0}.{1}".format(
                int(dll_version / 100000), (dll_version % 100000),
            ))
            print("*  授权信息:{0}".format(authorize))
            print("*  运行系统:{0}".format(str_os))
            print(border)

            FastHQChart._initialized = True
            return True
        except Exception as e:
            logger.warning("HQChartPy2 Initialization 失败: %s", e)
            return False

    @staticmethod
    def Run(jsonConfig, hqData, proSuccess=None, procFailed=None):
        """运行 HQChartPy2 公式计算。

        Args:
            jsonConfig: JSON 格式的配置字符串。
            hqData: IHQData 实现实例，提供数据回调。
            proSuccess: 可选的成功回调函数。
            procFailed: 可选的失败回调函数。

        Returns:
            bool: 计算是否成功提交。
        """
        if not _HQCHART_AVAILABLE:
            return False

        try:
            callbackConfig = {}
            callbackConfig['GetKLineData'] = hqData.GetKLineData
            callbackConfig['GetKLineData2'] = hqData.GetKLineData2
            callbackConfig['GetDataByNumber'] = hqData.GetDataByNumber
            callbackConfig['GetDataByNumbers'] = hqData.GetDataByNumbers
            callbackConfig['GetDataByName'] = hqData.GetDataByName
            callbackConfig['GetDataByString'] = hqData.GetDataByString
            callbackConfig['GetIndexScript'] = hqData.GetIndexScript

            if proSuccess:
                callbackConfig['Success'] = proSuccess
            if procFailed:
                callbackConfig['Failed'] = procFailed

            return _Run(jsonConfig, callbackConfig)
        except Exception as e:
            logger.warning("HQChartPy2 Run 失败: %s", e)
            return False


class HQChartProvider(DataSourceProvider):
    """基于 HQChartPy2 C++ 引擎的指标计算提供者。

    将 HQChartPy2 封装为 DataSourceProvider 接口，
    支持指标公式评估、选股公式评估和指标公式评估。

    K 线数据由调用方在 eval 时通过参数传入，本类不持有任何数据源引用。
    """

    def __init__(self, bus: Optional[EventBus] = None, config: Optional[Dict[str, Any]] = None):
        """初始化 HQChart 提供者。"""
        super().__init__(bus=bus, config=config)
        self._ready = False

        version = FastHQChart.GetVersion()
        available = FastHQChart.IsAvailable()
        logger.info(
            "HQChartProvider 初始化: 引擎版本=%s, 可用=%s",
            version, available,
        )

        try:
            if FastHQChart.Initialization():
                self._ready = True
                logger.info("HQChartProvider 初始化成功")
        except Exception as e:
            logger.warning("HQChartProvider 初始化失败: %s", e)

    # ------------------------------------------------------------------
    # 基础状态
    # ------------------------------------------------------------------

    def is_ready(self) -> bool:
        return self._ready

    def get_mode_info(self) -> str:
        return "hqchart"

    def check_health(self) -> Dict:
        """检查提供者健康状态。

        Returns:
            Dict: 包含 status、version 和可选的 error 字段。
        """
        version = FastHQChart.GetVersion()
        if FastHQChart.IsAvailable() and self._ready:
            return {"status": "ready", "version": version}
        else:
            if not FastHQChart.IsAvailable():
                error = "HQChartPy2 引擎不可用，请检查导入"
            else:
                error = "HQChartPy2 引擎初始化失败"
            return {"status": "unavailable", "version": version, "error": error}

    def _probe(self) -> Dict[str, Any]:
        """契约探测（Task 6）：探测 HQChart 引擎是否就绪。"""
        if not self._ready:
            return {
                "ready": False,
                "provider": "hqchart",
                "error": "HQChartPy2 引擎未安装或初始化失败",
            }
        return {"ready": True, "provider": "hqchart"}

    # ------------------------------------------------------------------
    # 公式解析工具
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_formula_outvars(formula_text: str) -> List[str]:
        """解析公式文本，提取输出变量名（以 : 开头但不以 := 开头）。

        在 TDX 公式语法中：
        - `NAME:EXPRESSION;` 是输出变量
        - `NAME:=EXPRESSION;` 是中间变量（不输出）

        结果通过模块级缓存 `_PARSE_OUTVARS_CACHE`（key = formula_text）缓存，
        避免对相同公式文本重复执行正则扫描。
        """
        cached = _PARSE_OUTVARS_CACHE.get(formula_text)
        if cached is not None:
            return cached

        outvars = []
        seen = set()
        # 匹配 变量名后跟 : 但不跟 = 的模式
        pattern = re.compile(r'([A-Za-z_]\w*)\s*:(?!\s*=)')
        for match in pattern.finditer(formula_text):
            name = match.group(1)
            if name not in seen:
                seen.add(name)
                outvars.append(name)
        result = outvars if outvars else ['T1']
        _PARSE_OUTVARS_CACHE[formula_text] = result
        return result

    # ------------------------------------------------------------------
    # 配置构建
    # ------------------------------------------------------------------

    def _build_config(self, code: str, formula_text: str, period: int,
                      max_count: int = 500) -> Dict[str, Any]:
        """构建 HQChart 单股计算配置。"""
        hqchart_code = to_dzh_code(code)  # 600000.SH → SH600000
        hqchart_period = _PYTHON_TO_HQCHART_PERIOD.get(
            period, PERIOD_ID.DAY_ID,
        )
        outvars = self._parse_formula_outvars(formula_text)

        return {
            'Symbol': hqchart_code,
            'Right': 0,
            'Period': hqchart_period,
            'Script': formula_text,
            'OutCount': 5,
            'JobID': 'meta_core_001',
        }

    # ------------------------------------------------------------------
    # 结果解析
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_result_value(result_data) -> float:
        """从 HQChart 返回结果中提取最后一个输出变量的最后一个值。"""
        if isinstance(result_data, str):
            try:
                result_data = json.loads(result_data)
            except (json.JSONDecodeError, TypeError):
                return 0

        if not isinstance(result_data, dict):
            return 0

        outvars = result_data.get('OutVar', [])
        if outvars and len(outvars) > 0:
            first_var = outvars[0]
            if isinstance(first_var, dict):
                values = first_var.get('Data', [])
                if values and len(values) > 0:
                    return values[-1]
        return 0

    @staticmethod
    def _extract_all_outvars(result_data) -> Dict[str, Any]:
        """从 HQChart 返回结果中提取全部输出变量的末值。

        Returns:
            ``{outvar_name: last_value}`` 字典；无输出变量时返回空字典。
        """
        if isinstance(result_data, str):
            try:
                result_data = json.loads(result_data)
            except (json.JSONDecodeError, TypeError):
                return {}

        if not isinstance(result_data, dict):
            return {}

        result = {}
        for var in result_data.get('OutVar', []):
            if isinstance(var, dict):
                name = var.get('Name', '')
                values = var.get('Data', [])
                if name and values and len(values) > 0:
                    result[name] = values[-1]
                else:
                    result[name] = 0
        return result

    # ------------------------------------------------------------------
    # 指标评估
    # ------------------------------------------------------------------

    def eval_indicator(self, codes, formula_text, period, sorttype=0,
                       kline_data=None) -> Dict:
        """评估指标公式，返回每个代码的指标值。

        Args:
            codes: 股票代码或代码列表。
            formula_text: TDX 格式的指标公式文本。
            period: 周期（Python 内部 int 或周期字符串）。
            sorttype: 排序类型（保留参数）。
            kline_data: K 线数据字典，由调用方通过数据源获取并传入。
                        格式为 {symbol: [bars]}。若为 None 则抛出 ValueError。

        Returns:
            Dict: `{'result': {code: value}, 'inditype': 0}`
        """
        if kline_data is None:
            raise ValueError("kline_data must be provided by caller")

        if not self._ready:
            return {
                'result': {},
                'inditype': 0,
                'error': 'HQChart 引擎未就绪，请检查引擎初始化状态',
            }

        if not codes:
            return {'result': {}, 'inditype': 0}

        if isinstance(codes, str):
            codes = [codes]

        period_int = period if isinstance(period, int) else map_period(period)
        results = {}
        errors = {}

        hq_data = IHQDataImpl(kline_data=kline_data)

        # 闭包外提：在循环外定义一次，避免每次迭代重建函数对象
        result_holder = {}

        def on_success(symbol, jsData, jobID):
            result_holder['data'] = jsData

        def on_failed(code, symbol, error, jobID):
            logger.debug("HQChart Run failed for %s: %s", code, error)

        for code in codes:
            try:
                config = self._build_config(code, formula_text, period_int)
                config_json = json.dumps(config)

                # 每次迭代清空 holder，复用已定义的闭包
                result_holder.clear()

                run_ok = FastHQChart.Run(config_json, hq_data, on_success, on_failed)
                if not run_ok:
                    errors[code] = "HQChart 引擎执行失败"
                    results[code] = 0
                    continue

                data = result_holder.get('data')
                if data is not None:
                    results[code] = self._extract_result_value(data)
                else:
                    results[code] = 0
            except Exception as e:
                error_msg = "公式计算异常: {}".format(str(e))
                logger.debug("eval_indicator error for %s: %s", code, e)
                results[code] = 0
                errors[code] = error_msg

        result_dict = {'result': results, 'inditype': 0}
        if errors:
            result_dict['errors'] = errors
        return result_dict

    async def eval_indicator_async(self, codes, formula_text, period, sorttype=0,
                                   kline_data=None) -> Dict:
        """异步包装的指标公式评估。

        将同步的 ``eval_indicator`` 通过 ``run_in_executor`` 包装，
        避免阻塞事件循环。签名与 ``eval_indicator`` 一致。

        Args:
            codes: 股票代码列表。
            formula_text: 公式脚本文本。
            period: 分析周期（字符串或整数）。
            sorttype: 排序类型。
            kline_data: K 线数据字典，由调用方通过数据源获取并传入。

        Returns:
            Dict: 与 ``eval_indicator`` 相同的结果结构。
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            self.eval_indicator,
            codes,
            formula_text,
            period,
            sorttype,
            kline_data,
        )

    def eval_indicator_outvars(self, codes, formula_text, period, sorttype=0,
                               kline_data=None) -> Dict:
        """评估指标公式，返回每个代码的全部输出变量末值。

        与 ``eval_indicator`` 签名一致，但结果中每个代码的值是
        ``{outvar_name: last_value}`` 字典而非标量。

        Returns:
            Dict: ``{'result': {code: {outvar: val, ...}}, 'inditype': 0}``
        """
        if kline_data is None:
            raise ValueError("kline_data must be provided by caller")

        if not self._ready:
            return {
                'result': {},
                'inditype': 0,
                'error': 'HQChart 引擎未就绪，请检查引擎初始化状态',
            }

        if not codes:
            return {'result': {}, 'inditype': 0}

        if isinstance(codes, str):
            codes = [codes]

        period_int = period if isinstance(period, int) else map_period(period)
        results = {}
        errors = {}

        hq_data = IHQDataImpl(kline_data=kline_data)

        result_holder = {}

        def on_success(symbol, jsData, jobID):
            result_holder['data'] = jsData

        def on_failed(code, symbol, error, jobID):
            logger.debug("HQChart Run failed for %s: %s", code, error)

        for code in codes:
            try:
                config = self._build_config(code, formula_text, period_int)
                config_json = json.dumps(config)

                result_holder.clear()

                run_ok = FastHQChart.Run(config_json, hq_data, on_success, on_failed)
                if not run_ok:
                    errors[code] = "HQChart 引擎执行失败"
                    results[code] = {}
                    continue

                data = result_holder.get('data')
                if data is not None:
                    results[code] = self._extract_all_outvars(data)
                else:
                    results[code] = {}
            except Exception as e:
                error_msg = "公式计算异常: {}".format(str(e))
                logger.debug("eval_indicator_outvars error for %s: %s", code, e)
                results[code] = {}
                errors[code] = error_msg

        result_dict = {'result': results, 'inditype': 0}
        if errors:
            result_dict['errors'] = errors
        return result_dict

    async def eval_indicator_outvars_async(self, codes, formula_text, period,
                                           sorttype=0, kline_data=None) -> Dict:
        """异步包装的指标公式评估（返回全部输出变量）。

        将同步的 ``eval_indicator_outvars`` 通过 ``run_in_executor`` 包装，
        避免阻塞事件循环。签名与 ``eval_indicator_outvars`` 一致。
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            self.eval_indicator_outvars,
            codes,
            formula_text,
            period,
            sorttype,
            kline_data,
        )

    # ------------------------------------------------------------------
    # 选股公式评估
    # ------------------------------------------------------------------

    def eval_formula_xg(self, formula_name, formula_arg='', stock_list=None,
                        period='1d', count=0, dividend_type=1,
                        start_time='', end_time='', kline_data=None) -> Dict:
        """评估选股公式。

        选股公式返回符合条件的股票代码列表。

        Args:
            formula_name: 公式名称。
            formula_arg: 公式参数。
            stock_list: 候选股票列表。
            period: 周期。
            count: 数量限制。
            dividend_type: 复权类型。
            start_time: 开始时间。
            end_time: 结束时间。
            kline_data: K 线数据字典，由调用方通过数据源获取并传入。

        Returns:
            Dict: `{"success": bool, "result": {}, "selected_codes": []}`
        """
        if not self._ready:
            return {"success": False, "result": {}, "selected_codes": []}

        if stock_list is None:
            stock_list = []

        if not stock_list:
            return {"success": False, "result": {}, "selected_codes": []}

        period_int = period if isinstance(period, int) else map_period(period)

        try:
            result = self.eval_indicator(
                stock_list, formula_name, period_int, kline_data=kline_data,
            )
            indicator_results = result.get('result', {})

            selected_codes = [
                code for code, value in indicator_results.items()
                if value and value != 0
            ]

            return {
                "success": True,
                "result": indicator_results,
                "selected_codes": selected_codes,
            }
        except Exception as e:
            logger.debug("eval_formula_xg error: %s", e)
            return {"success": False, "result": {}, "selected_codes": []}

    # ------------------------------------------------------------------
    # 指标公式评估
    # ------------------------------------------------------------------

    def eval_formula_zb(self, formula_name, formula_arg='', stock_list=None,
                        period='1d', count=5, dividend_type=1,
                        return_count=1, return_date=False,
                        xsflag=6, start_time='', end_time='',
                        kline_data=None) -> Dict:
        """评估指标公式。

        Args:
            formula_name: 公式名称或公式文本。
            formula_arg: 公式参数。
            stock_list: 候选股票列表。
            period: 周期。
            count: 数据条数。
            dividend_type: 复权类型。
            return_count: 返回数据条数。
            return_date: 是否返回日期。
            xsflag: 显示标志。
            start_time: 开始时间。
            end_time: 结束时间。
            kline_data: K 线数据字典，由调用方通过数据源获取并传入。

        Returns:
            Dict: `{"success": bool, "result": {}}`
        """
        if not self._ready:
            return {"success": False, "result": {}}

        if stock_list is None:
            stock_list = []

        if not stock_list:
            return {"success": False, "result": {}}

        period_int = period if isinstance(period, int) else map_period(period)

        try:
            result = self.eval_indicator(
                stock_list, formula_name, period_int, kline_data=kline_data,
            )
            return {
                "success": True,
                "result": result.get('result', {}),
            }
        except Exception as e:
            logger.debug("eval_formula_zb error: %s", e)
            return {"success": False, "result": {}}


# ===========================================================================
# AkShareProvider —— 基于 AKShare 的数据源提供者
# ===========================================================================


class _RateLimiter:
    """简单的请求限频器，支持最小间隔和指数退避重试。"""

    def __init__(self, min_interval: float = 1.0, max_retries: int = 3):
        self._min_interval = min_interval
        self._max_retries = max_retries
        self._last_request_time = 0.0

    async def wait_if_needed(self):
        """确保两次请求之间有最小间隔。"""
        now = time.time()
        elapsed = now - self._last_request_time
        if elapsed < self._min_interval:
            await asyncio.sleep(self._min_interval - elapsed)
        self._last_request_time = time.time()

    async def execute_with_retry(self, func, *args, **kwargs):
        """执行函数，失败时自动重试（指数退避）。"""
        last_error = None
        for attempt in range(self._max_retries):
            try:
                await self.wait_if_needed()
                result = func(*args, **kwargs)
                # 如果是协程，需要 await
                if asyncio.iscoroutine(result):
                    result = await result
                return result
            except Exception as e:
                last_error = e
                if attempt < self._max_retries - 1:
                    delay = (2 ** attempt) * 0.5  # 0.5s, 1s, 2s
                    logger.warning("请求失败（第%d次重试）: %s，%ds后重试", attempt + 1, e, delay)
                    await asyncio.sleep(delay)
        raise last_error


class DataSourceError(Exception):
    """AkShare 数据源异常。"""

    pass


class AkShareProvider(DataSourceProvider):
    """基于 AKShare 的数据源提供者。

    基于 AKShare 开源库获取 A 股行情、板块、财务等数据，
    并将结果转换为统一内部格式。
    """

    def __init__(self, bus: Optional[EventBus] = None, config: Optional[Dict[str, Any]] = None):
        super().__init__(bus=bus, config=config)
        self._ready = False
        self._ak = None
        self._kline_cache = KLineDataCache()
        self._rate_limiter = _RateLimiter(min_interval=1.0, max_retries=3)
        try:
            import akshare as ak
            self._ak = ak
            self._ready = True
            logger.info("AkShareProvider 初始化成功")
        except ImportError:
            logger.warning("akshare 未安装，AkShareProvider 不可用")
        except Exception as e:
            logger.warning("AkShareProvider 初始化失败: %s", e)

    # ------------------------------------------------------------------
    # 基础状态
    # ------------------------------------------------------------------

    def is_ready(self) -> bool:
        return self._ready

    def get_mode_info(self) -> str:
        return "akshare"

    def _probe(self) -> Dict[str, Any]:
        """契约探测（Task 6）：探测 AkShare 模块是否可用。"""
        if not self._ready:
            return {
                "ready": False,
                "provider": "akshare",
                "error": "akshare 模块未安装或初始化失败",
            }
        return {"ready": True, "provider": "akshare"}

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------

    @staticmethod
    def _code_to_ak_symbol(code: str) -> str:
        """将统一代码 (如 600000.SH 或 SH600000) 转换为 AKShare 的纯数字 symbol (如 600000)。"""
        if '.' in code:
            return code.split('.')[0]
        # 处理 TDX 格式: SH600000 → 600000, SZ000001 → 000001
        prefixes = (get_global_config_store().get_table("market_classifications") if get_global_config_store() else {}).get("exchange_prefixes", ['SH', 'SZ', 'BJ'])
        if code[:2].upper() in prefixes:
            return code[2:]
        return code

    @staticmethod
    def _ak_code_to_tq(code: str) -> str:
        """将 AKShare 的纯数字代码转换为 TQ 格式 (如 600000 -> 600000.SH)。"""
        code = str(code).strip()
        if not code:
            return code
        for rule in (get_global_config_store().get_table("market_classifications") if get_global_config_store() else {}).get("code_prefix_rules", []):
            prefix = rule.get("prefix", "")
            market = rule.get("market", "")
            if prefix and market and code.startswith(prefix):
                return f"{code}.{market}"
        return code

    @staticmethod
    def _normalize_time(raw_time) -> str:
        """将 AKShare 返回的日期/时间标准化为 'YYYY-MM-DD HH:MM:SS' 格式。"""
        if raw_time is None:
            return ''
        # pandas Timestamp / datetime
        if hasattr(raw_time, 'strftime'):
            return raw_time.strftime('%Y-%m-%d %H:%M:%S')
        s = str(raw_time).strip()
        if not s:
            return ''
        # 纯数字格式 YYYYMMDD
        if len(s) == 8 and s.isdigit():
            return f'{s[:4]}-{s[4:6]}-{s[6:8]} 00:00:00'
        # HARDCODED: 不可剥离，理由：按字符位置识别 YYYY-MM-DD 是固定日期格式解析，非业务规则
        if len(s) == 10 and s[4] == '-' and s[7] == '-':
            return f'{s} 00:00:00'
        # HARDCODED: 不可剥离，理由：按字符位置识别完整时间字符串是固定日期格式解析，非业务规则
        if len(s) >= 19 and s[4] == '-' and s[10] == ' ':
            return s[:19]
        return s

    def _get_all_a_spot(self) -> Optional[object]:
        """获取全 A 股实时行情 DataFrame，失败返回 None。"""
        if not self._ready:
            return None
        try:
            return self._ak.stock_zh_a_spot_em()
        except Exception as e:
            logger.warning("获取全 A 股实时行情失败: %s", e)
            return None

    # ------------------------------------------------------------------
    # 行情数据
    # ------------------------------------------------------------------

    def resolve_market(self, markets) -> Dict[str, List[str]]:
        """解析市场列表，返回 {市场名: [股票代码]} 映射。

        支持两种格式:
        1. DZH 格式: 'SH#上证A股', 'SZ#深证A股' 等
        2. 短名称格式: 'sh_a', 'sz_a', 'gem', 'sme' 等
        """
        if not self._ready:
            return {}
        result = {}
        df = self._get_all_a_spot()
        if df is None or df.empty:
            return {}

        mc_cfg = get_global_config_store().get_table("market_classifications") if get_global_config_store() else {}
        short_to_dzh = mc_cfg.get("short_to_dzh", {})
        market_filters = mc_cfg.get("market_filters", {})
        for market in markets:
            try:
                # 统一转换为 DZH 格式
                dzh_key = short_to_dzh.get(market, market)
                codes = []
                flt = market_filters.get(dzh_key)
                if not flt:
                    continue
                prefixes = flt.get("prefixes") or ([flt["prefix"]] if flt.get("prefix") else [])
                if not prefixes:
                    continue
                codes = [
                    self._ak_code_to_tq(str(c))
                    for c in df['代码']
                    if any(str(c).startswith(p) for p in prefixes)
                ]
                # 返回时保留原始 market key
                result[market] = codes
            except Exception as e:
                logger.warning("解析市场 %s 失败: %s", market, e)
        return result

    def get_kline_data(self, codes, period=None, start_date=None, end_date=None, **kwargs) -> Dict:
        """获取K线数据。"""
        if not self._ready:
            return {}

        period = _norm_period(period or '1d')

        # AKShare period 映射（由 data_source_routes.json 驱动）
        routes = get_global_config_store().get_table("data_source_routes") if get_global_config_store() else {}
        ak_period_map = routes.get("provider_routes", {}).get("akshare", {}).get("period_map", {})
        ak_period = ak_period_map.get(period, 'daily')

        # 日期格式化为 AKShare 要求的 YYYYMMDD
        fmt_date = None
        if start_date:
            try:
                # HARDCODED: 不可剥离，理由：'-' 是日期格式 YYYY-MM-DD 与 YYYYMMDD 的固定分隔符识别
                if '-' in str(start_date):
                    fmt_date = str(start_date).replace('-', '')
                else:
                    fmt_date = str(start_date)
            except Exception:
                fmt_date = None

        fmt_end = None
        if end_date:
            try:
                # HARDCODED: 不可剥离，理由：'-' 是日期格式 YYYY-MM-DD 与 YYYYMMDD 的固定分隔符识别
                if '-' in str(end_date):
                    fmt_end = str(end_date).replace('-', '')
                else:
                    fmt_end = str(end_date)
            except Exception:
                fmt_end = None

        result = {}
        code_list = codes if isinstance(codes, list) else [codes]

        for code in code_list:
            symbol = self._code_to_ak_symbol(code)

            # 检查缓存
            cached = self._kline_cache.get(code, period)
            if cached is not None:
                result[code] = cached
                continue

            try:
                df = self._ak.stock_zh_a_hist(
                    symbol=symbol,
                    period=ak_period,
                    start_date=fmt_date or '19900101',
                    end_date=fmt_end or '20991231',
                    adjust="qfq",
                )
                if df is None or df.empty:
                    result[code] = []
                    continue

                bars = []
                for _, row in df.iterrows():
                    # AKShare 成交量单位为"手"，需乘以100转为"股"
                    vol_lots = float(row.get('成交量', 0) or 0)
                    bar = {
                        'open': float(row.get('开盘', 0) or 0),
                        'high': float(row.get('最高', 0) or 0),
                        'low': float(row.get('最低', 0) or 0),
                        'close': float(row.get('收盘', 0) or 0),
                        'volume': vol_lots * 100,
                        'amount': float(row.get('成交额', 0) or 0),
                        'time': self._normalize_time(row.get('日期', '')),
                    }
                    bars.append(bar)

                self._kline_cache.put(code, period, bars)
                result[code] = bars

            except Exception as e:
                logger.warning("获取 %s K线数据失败: %s", code, e)
                result[code] = []

        return result

    def get_snapshot(self, codes) -> Dict[str, Dict]:
        """获取实时快照。"""
        if not self._ready:
            return {}

        df = self._get_all_a_spot()
        if df is None or df.empty:
            return {}

        code_list = codes if isinstance(codes, list) else [codes]
        # 构建纯数字代码集合用于过滤
        target_symbols = set()
        for c in code_list:
            target_symbols.add(self._code_to_ak_symbol(c))

        result = {}
        try:
            for _, row in df.iterrows():
                sym = str(row.get('代码', ''))
                if sym not in target_symbols:
                    continue

                tq_code = self._ak_code_to_tq(sym)
                pre_close = float(row.get('昨收', 0) or 0)
                now_price = float(row.get('最新价', 0) or 0)
                change_amt = now_price - pre_close if pre_close else 0
                change_pct = (change_amt / pre_close * 100) if pre_close else 0

                # AKShare 成交量单位为"手"，需乘以100转为"股"
                vol_lots = float(row.get('成交量', 0) or 0)
                snapshot = {
                    'name': str(row.get('名称', '')),
                    'close': now_price,
                    'price': now_price,
                    'now': now_price,
                    'open': float(row.get('今开', 0) or 0),
                    'high': float(row.get('最高', 0) or 0),
                    'low': float(row.get('最低', 0) or 0),
                    'pre_close': pre_close,
                    'change_pct': round(change_pct, 2),
                    'change_amt': round(change_amt, 2),
                    'rise': round(change_pct, 2),
                    'volume': vol_lots * 100,
                    'amount': float(row.get('成交额', 0) or 0),
                    'turnover_rate': float(row.get('换手率', 0) or 0),
                    'volume_ratio': float(row.get('量比', 0) or 0),
                    'pe_ratio': float(row.get('市盈率-动态', 0) or 0),
                    'total_market_cap': float(row.get('总市值', 0) or 0),
                    'circulating_market_cap': float(row.get('流通市值', 0) or 0),
                }
                result[tq_code] = snapshot
        except Exception as e:
            logger.warning("获取快照失败: %s", e)

        return result

    # ------------------------------------------------------------------
    # 板块 / 股票列表
    # ------------------------------------------------------------------

    def get_block_members(self, block_code) -> List[str]:
        """获取板块成员（通过 AKShare 板块接口）。"""
        if not self._ready:
            return []
        try:
            # 尝试通过板块代码获取成分股
            df = self._ak.stock_board_consr_ts_stocks(symbol=block_code)
            if df is not None and not df.empty:
                return [self._ak_code_to_tq(str(c)) for c in df['代码'].tolist()]
        except Exception as e:
            logger.debug("获取板块 %s 成分股失败: %s", block_code, e)
        return []

    def get_stock_list_by_type(self, list_type, customblockname='', **kwargs) -> List[Dict]:
        """按类型获取股票列表。"""
        if not self._ready:
            return []
        try:
            df = self._get_all_a_spot()
            if df is None or df.empty:
                return []
            result = []
            for _, row in df.iterrows():
                code = str(row.get('代码', ''))
                tq_code = self._ak_code_to_tq(code)
                if '.' in tq_code:
                    pure_code, market = tq_code.split('.')
                else:
                    pure_code = tq_code
                    market = ''
                result.append({
                    'code': pure_code,
                    'name': str(row.get('名称', '')),
                    'market': market,
                })
            return result
        except Exception as e:
            logger.warning("获取股票列表失败: %s", e)
            return []

    def get_sector_list(self, list_type=1) -> List[Dict]:
        """获取板块列表。"""
        if not self._ready:
            return []
        try:
            df = self._ak.stock_board_consr_name_em()
            if df is None or df.empty:
                return []
            result = []
            for _, row in df.iterrows():
                result.append({
                    'sector_code': str(row.get('板块名称', '')),
                    'sector_name': str(row.get('板块名称', '')),
                    'category': 'concept',
                    'member_count': int(row.get('总数量', 0) or 0),
                })
            return result
        except Exception as e:
            logger.warning("获取板块列表失败: %s", e)
            return []

    def get_sector_stocks(self, sector_code, block_type=0) -> List[str]:
        """获取板块成分股。"""
        return self.get_block_members(sector_code)

    # ------------------------------------------------------------------
    # 公式评估（AkShare 不支持公式评估，返回默认空结果）
    # ------------------------------------------------------------------

    def eval_indicator(self, codes, formula_text, period, sorttype=0) -> Dict:
        return {'result': {}, 'inditype': 0}

    def eval_formula_xg(self, formula_name, formula_arg='', stock_list=None,
                        period='1d', count=0, dividend_type=1,
                        start_time='', end_time='') -> Dict:
        return {"success": False, "result": {}, "selected_codes": []}

    def eval_formula_zb(self, formula_name, formula_arg='', stock_list=None,
                        period='1d', count=5, dividend_type=1,
                        return_count=1, return_date=False,
                        xsflag=6, start_time='', end_time='') -> Dict:
        return {"success": False, "result": {}}

    # ------------------------------------------------------------------
    # 财务 / 回放 / 重采样
    # ------------------------------------------------------------------

    def get_financial_data(self, codes, fields) -> Dict:
        """获取财务数据（通过 AKShare 财务接口）。"""
        if not self._ready:
            return {}
        if isinstance(codes, str):
            codes = [codes]
        result = {}
        for code in codes:
            try:
                symbol = self._code_to_ak_symbol(code)
                # 简化实现：使用 stock_financial_abstract 接口
                df = self._ak.stock_financial_abstract(symbol=symbol)
                if df is None or df.empty:
                    continue
                data = {}
                for field in fields:
                    # 尝试从财务摘要中匹配字段
                    if field in df.columns:
                        data[field] = df[field].iloc[0] if not df.empty else None
                result[code] = data
            except Exception as e:
                logger.debug("获取 %s 财务数据失败: %s", code, e)
        return result

    def get_replay_data(self, codes, current_time, period='1min') -> Dict[str, List[Dict]]:
        """获取回放数据。"""
        return self.get_kline_data(codes, period=period)

    def resample_kline(self, kline_1min, target_period) -> List[Dict]:
        """从1分钟K线重采样到目标周期。"""
        if not kline_1min:
            return []
        try:
            import pandas as pd
            df = pd.DataFrame(kline_1min)
            df['time'] = pd.to_datetime(df['time'])
            df.set_index('time', inplace=True)
            rule_map = {
                '5min': '5min', '5m': '5min',
                '15min': '15min', '15m': '15min',
                '30min': '30min', '30m': '30min',
                '60min': '60min', '60m': '60min',
                'day': 'D', '1d': 'D',
            }
            rule = rule_map.get(target_period, '5min')
            resampled = df.resample(rule).agg({
                'open': 'first',
                'high': 'max',
                'low': 'min',
                'close': 'last',
                'volume': 'sum',
                'amount': 'sum',
            }).dropna()
            result = []
            for idx, row in resampled.iterrows():
                result.append({
                    'open': round(float(row['open']), 2),
                    'high': round(float(row['high']), 2),
                    'low': round(float(row['low']), 2),
                    'close': round(float(row['close']), 2),
                    'volume': int(row['volume']),
                    'amount': round(float(row['amount']), 2),
                    'time': idx.strftime('%Y-%m-%d %H:%M:%S'),
                })
            return result
        except Exception:
            return []

# ===========================================================================
# LocalFileProvider（合并自 local_file_provider.py）
# 本地文件数据源提供者：解析 TDX/DZH/THS 本地配置文件
# ===========================================================================

# 本地文件路径规则配置表（相对 meta_core 目录解析）
CONFIG_PATH = Path(__file__).resolve().parents[1] / 'config' / 'data' / 'local_file_paths.json'

# TDX 市场标识位默认映射（配置缺失时兜底）
DEFAULT_TDX_MARKET_DIGIT = {
    '0': {'setcode': 0, 'market': 'SZ'},  # 深圳
    '1': {'setcode': 1, 'market': 'SH'},  # 上海
    '2': {'setcode': 2, 'market': 'BJ'},  # 北京
}

# 代码前缀推断市场默认映射（用于 THS，配置缺失时兜底）
DEFAULT_CODE_PREFIX_MAP = {
    '6': {'setcode': 1, 'market': 'SH'},
    '0': {'setcode': 0, 'market': 'SZ'},
    '3': {'setcode': 0, 'market': 'SZ'},
    '4': {'setcode': 2, 'market': 'BJ'},
    '8': {'setcode': 2, 'market': 'BJ'},
}

# 客户端探测顺序
_CLIENT_ORDER = ('tdx', 'dzh', 'ths')

# TDX 自选股 / 板块成分股行正则：1 位市场标识（0=深,1=沪,2=京） + 6 位代码
_TDX_LINE_RE = re.compile(r'^([012])(\d{6})$')
# DZH 自选股行正则：代码 Tab 市场 [Tab 名称]
_DZH_LINE_RE = re.compile(r'^(\S+)\t(\S+)(?:\t(\S+))?$')
# THS 自选股行正则：6 位数字代码
_THS_LINE_RE = re.compile(r'^(\d{6})$')
# THS 自选股行正则（ZXG.cfg）：可选市场前缀(SH/SZ/BJ) + 6 位数字代码
_THS_FAVORITES_RE = re.compile(r'^(SH|SZ|BJ)?(\d{6})$')

# 通达信系统板块类型映射（tdxbk.cfg 第一字段）
# 依据实际 tdxbk.cfg 文件内容确定：1=概念, 2=风格, 3=指数
# 注：通达信 tdxbk.cfg 未发现行业（industry）类型，仅 1/2/3 三种。
_TDX_SYSTEM_BLOCK_TYPES = {
    '1': {'name': 'concept', 'label': '概念'},
    '2': {'name': 'style', 'label': '风格'},
    '3': {'name': 'index', 'label': '指数'},
}

# 大智慧（DZH）行业板块名映射（cfg/block.ini 的 [BlockInfo]SysBlock 中含"行业"的板块名）
# 依据实际 DZH block.ini 文件内容确定。DZH 不使用数字类型字段，而是空格分隔的板块名。
# 当前 _parse_dzh_block_ini 返回所有 SysBlock 名称列表，未按分类分组；
# 如需按行业分类，可据此集合识别行业类板块。
_DZH_INDUSTRY_BLOCK_NAMES = {
    '所属行业',
    '证监会行业',
    '大智慧行业(经典)',
    '申万行业',
}

# 同花顺（THS）行业板块文件标识
# THS 在安装根目录下有 industry.ini 文件，格式为 INI：
#   [industry]
#   881101=603336,601996,...   (键=行业代码，值=逗号分隔的股票代码)
# 该文件包含完整的行业分类与成分股，但当前 _parse_ths_blocks 仅解析 Block.cfg，
# 未解析 industry.ini。如需支持 THS 行业板块，需新增解析逻辑。
_THS_INDUSTRY_FILE_KEY = 'industry_ini'


class LocalFileProvider(DataSourceProvider):
    """本地文件数据源提供者。

    解析通达信/大智慧/同花顺本地配置文件，提供自选股与自定义板块数据。
    文件不存在时返回空列表，不抛出异常；解析结果按文件路径缓存，
    文件修改时间变化时自动刷新。
    """

    def __init__(self, bus: Optional[EventBus] = None, config: Optional[Dict[str, Any]] = None):
        super().__init__(bus=bus, config=config)
        # 实例级缓存：key=文件路径(str)，value=(解析结果, mtime)
        self._cache: Dict[str, Tuple[Any, float]] = {}
        # 路径规则配置表
        self._paths_config: Dict[str, Any] = self._load_paths_config()
        # 市场代码映射规则（从配置读取，缺失用默认值）
        market_rules = self._paths_config.get('market_code_rules', {})
        self._tdx_market_digit: Dict[str, Dict] = market_rules.get(
            'tdx_market_digit', DEFAULT_TDX_MARKET_DIGIT)
        self._code_prefix_map: Dict[str, Dict] = market_rules.get(
            'code_prefix_to_market', DEFAULT_CODE_PREFIX_MAP)
        # 探测各客户端安装根目录
        self._homes: Dict[str, str] = self._detect_homes()

    # ------------------------------------------------------------------
    # 配置加载
    # ------------------------------------------------------------------

    def _load_paths_config(self) -> Dict[str, Any]:
        """加载 local_file_paths.json 路径规则配置表。"""
        try:
            with open(CONFIG_PATH, encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.warning("加载本地路径配置 %s 失败: %s", CONFIG_PATH, e)
            return {}

    # ------------------------------------------------------------------
    # 路径探测
    # ------------------------------------------------------------------

    def _detect_homes(self) -> Dict[str, str]:
        """探测各客户端安装根目录。

        优先级：配置文件显式指定 > 环境变量 > Windows 注册表。
        """
        homes: Dict[str, str] = {}
        clients_cfg = self._paths_config.get('clients', {})
        for client in _CLIENT_ORDER:
            cfg = clients_cfg.get(client, {})
            home = None

            # 1. 配置文件显式指定（config_key 指向 default_homes 下的字段，如 tdx_home）
            config_key = cfg.get('config_key')
            if config_key:
                home = self._paths_config.get('default_homes', {}).get(config_key)

            # 2. 环境变量
            if not home:
                env_var = cfg.get('env_var')
                if env_var:
                    home = os.environ.get(env_var)

            # 3. Windows 注册表
            if not home:
                home = self._detect_from_registry(cfg.get('registry_keys', []))

            # 4. 默认安装路径（配置/环境变量/注册表均未命中时依次尝试）
            if not home:
                for default_path in cfg.get('default_paths', []):
                    if default_path and Path(default_path).exists():
                        home = default_path
                        break

            if home:
                home_path = Path(home)
                if home_path.exists():
                    homes[client] = str(home_path)
                    logger.debug("探测到 %s 安装目录: %s", client, home)
                else:
                    logger.debug("探测到 %s 路径不存在: %s", client, home)
        return homes

    def _detect_from_registry(self, registry_keys: List[Dict]) -> Optional[str]:
        """从 Windows 注册表探测安装路径（非 Windows 平台跳过）。"""
        if not sys.platform.startswith('win'):
            return None
        if not registry_keys:
            return None
        try:
            import winreg
        except ImportError:
            return None

        hive_map = {
            'HKLM': winreg.HKEY_LOCAL_MACHINE,
            'HKCU': winreg.HKEY_CURRENT_USER,
            'HKEY_LOCAL_MACHINE': winreg.HKEY_LOCAL_MACHINE,
            'HKEY_CURRENT_USER': winreg.HKEY_CURRENT_USER,
        }

        for key_spec in registry_keys:
            hive = key_spec.get('hive', 'HKLM')
            subpath = key_spec.get('path', '')
            value_name = key_spec.get('value', 'InstallPath')
            hkey = hive_map.get(hive)
            if hkey is None:
                continue
            try:
                with winreg.OpenKey(hkey, subpath) as k:
                    val, _ = winreg.QueryValueEx(k, value_name)
                    if val:
                        return str(val)
            except OSError:
                # 键不存在，尝试下一个
                continue
            except Exception as e:
                logger.debug("注册表查询失败 %s\\%s: %s", hive, subpath, e)
                continue
        return None

    # ------------------------------------------------------------------
    # 基础状态
    # ------------------------------------------------------------------

    def is_ready(self) -> bool:
        """检测本地配置文件是否存在（通达信/大智慧/同花顺任一可用即就绪）。"""
        return bool(self._homes)

    def _probe(self) -> Dict[str, Any]:
        """契约探测方法，供 data_source_contract.json 调用。

        Returns:
            {"ready": bool, "provider": "local_file", "error"?: str}
        """
        try:
            ready = self.is_ready()
        except Exception as e:
            return {"ready": False, "provider": "local_file", "error": str(e)}
        if not ready:
            return {
                "ready": False,
                "provider": "local_file",
                "error": "未找到通达信/大智慧/同花顺本地配置文件",
            }
        return {"ready": True, "provider": "local_file"}

    def get_mode_info(self) -> str:
        """返回当前提供者的模式描述字符串。"""
        return "local_file"

    # ------------------------------------------------------------------
    # 文件读取与缓存
    # ------------------------------------------------------------------

    def get_file_mtime(self, path) -> Optional[float]:
        """获取文件修改时间，用于缓存失效判断。文件不存在返回 None。"""
        try:
            return os.path.getmtime(str(path))
        except OSError:
            return None

    def _read_text(self, path: str, encoding: str) -> Optional[str]:
        """读取文件文本，解码失败时依次降级尝试 gbk / utf-8。"""
        try:
            with open(path, 'rb') as f:
                raw = f.read()
        except OSError as e:
            logger.warning("读取文件失败 %s: %s", path, e)
            return None

        # 依次尝试：指定编码 → gb18030 → gbk → utf-8
        tried = []
        for enc in (encoding, 'gb18030', 'gbk', 'utf-8'):
            if not enc or enc in tried:
                continue
            tried.append(enc)
            try:
                return raw.decode(enc)
            except (UnicodeDecodeError, LookupError):
                continue
        # 全部严格解码失败，用 gb18030 + replace 兜底（保留中文，仅替换少数无效字节）
        logger.warning("文件 %s 严格解码失败，使用 gb18030 replace 模式", path)
        return raw.decode('gb18030', errors='replace')

    def _read_file_cached(self, path: str, encoding: str,
                          parser: Callable[[str], Any]) -> Any:
        """读取文件并缓存解析结果，文件修改时间变化时刷新缓存。

        Args:
            path: 文件绝对路径
            encoding: 首选编码
            parser: 解析函数，接收文本返回解析结果

        Returns:
            解析结果；文件不存在时返回 None。
        """
        mtime = self.get_file_mtime(path)
        if mtime is None:
            logger.warning("文件未找到: %s", path)
            return None

        cached = self._cache.get(path)
        if cached is not None and cached[1] == mtime:
            logger.debug("命中缓存: %s", path)
            return cached[0]

        text = self._read_text(path, encoding)
        if text is None:
            return None
        result = parser(text)
        self._cache[path] = (result, mtime)
        logger.debug("已解析文件: %s", path)
        return result

    def _read_binary_cached(self, path: str,
                            parser: Callable[[bytes], Any]) -> Any:
        """读取二进制文件并缓存解析结果，文件修改时间变化时刷新缓存。

        Args:
            path: 文件绝对路径
            parser: 解析函数，接收 bytes 返回解析结果

        Returns:
            解析结果；文件不存在时返回 None。
        """
        mtime = self.get_file_mtime(path)
        if mtime is None:
            logger.warning("文件未找到: %s", path)
            return None

        cached = self._cache.get(path)
        if cached is not None and cached[1] == mtime:
            logger.debug("命中缓存: %s", path)
            return cached[0]

        try:
            with open(path, 'rb') as f:
                raw = f.read()
        except OSError as e:
            logger.warning("读取二进制文件失败 %s: %s", path, e)
            return None

        try:
            result = parser(raw)
        except Exception as e:
            logger.warning("解析二进制文件失败 %s: %s", path, e)
            return None

        self._cache[path] = (result, mtime)
        logger.debug("已解析二进制文件: %s", path)
        return result

    # ------------------------------------------------------------------
    # 路径解析辅助
    # ------------------------------------------------------------------

    def _get_file_path(self, client: str, file_key: str) -> Optional[str]:
        """根据配置获取客户端某文件的完整路径。"""
        home = self._homes.get(client)
        if not home:
            return None
        client_cfg = self._paths_config.get('clients', {}).get(client, {})
        file_cfg = client_cfg.get('files', {}).get(file_key, {})
        rel_path = file_cfg.get('path')
        if not rel_path:
            return None
        return str(Path(home) / rel_path)

    def _get_encoding(self, client: str, file_key: str) -> str:
        """从配置获取客户端某文件的首选编码。"""
        client_cfg = self._paths_config.get('clients', {}).get(client, {})
        file_cfg = client_cfg.get('files', {}).get(file_key, {})
        return file_cfg.get('encoding', 'gb2312')

    def _get_block_file_path(self, client: str, block_filename: str) -> Optional[str]:
        """根据配置获取板块成分股文件完整路径（替换占位符）。"""
        home = self._homes.get(client)
        if not home:
            return None
        client_cfg = self._paths_config.get('clients', {}).get(client, {})
        file_cfg = client_cfg.get('files', {}).get('block_members_pattern', {})
        rel_pattern = file_cfg.get('path', '')
        if not rel_pattern:
            return None
        # 替换占位符 {block_filename} / {block_name}
        rel_path = rel_pattern.replace('{block_filename}', block_filename) \
                              .replace('{block_name}', block_filename)
        return str(Path(home) / rel_path)

    # ------------------------------------------------------------------
    # 市场映射辅助
    # ------------------------------------------------------------------

    def _market_to_setcode(self, market_field: str, code: str = '') -> Tuple[int, str]:
        """解析市场字段，返回 (setcode, market)。

        支持数字标识（0/1/2）、字母标识（SH/SZ/BJ），无法识别时按代码前缀推断。
        """
        market_field = str(market_field).strip().upper()
        # 数字标识（TDX 风格）
        if market_field in self._tdx_market_digit:
            info = self._tdx_market_digit[market_field]
            return info['setcode'], info['market']
        # 字母标识
        letter_map = {'SH': (1, 'SH'), 'SZ': (0, 'SZ'), 'BJ': (2, 'BJ')}
        if market_field in letter_map:
            return letter_map[market_field]
        # 按代码前缀推断
        if code and code[0] in self._code_prefix_map:
            info = self._code_prefix_map[code[0]]
            return info['setcode'], info['market']
        return 0, 'SZ'

    @staticmethod
    def _to_tq_code(code: str, setcode: int) -> str:
        """将纯数字代码 + setcode 转换为 XXXXXX.SH 格式。"""
        market_map = {0: 'SZ', 1: 'SH', 2: 'BJ'}
        market = market_map.get(setcode, 'SZ')
        return f"{code}.{market}"

    # ------------------------------------------------------------------
    # 文件解析器
    # ------------------------------------------------------------------

    def _parse_tdx_zxg(self, text: str) -> List[Dict]:
        """解析通达信自选股文件。每行：市场标识+代码（如 1600141 = 600141.SH）。"""
        favorites: List[Dict] = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            m = _TDX_LINE_RE.match(line)
            if not m:
                logger.debug("TDX zxg 跳过无法匹配的行: %s", line)
                continue
            market_digit, code = m.group(1), m.group(2)
            info = self._tdx_market_digit.get(market_digit)
            if not info:
                continue
            favorites.append({
                'setcode': info['setcode'],
                'code': code,
                'name': '',  # zxg.blk 不含名称
            })
        return favorites

    def _parse_tdx_blocknew(self, text: str) -> List[Dict]:
        """解析通达信自定义板块索引。每行用 | 分隔：板块名|板块文件名|类型|..."""
        blocks: List[Dict] = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split('|')
            if len(parts) < 2:
                continue
            block_name = parts[0].strip()
            block_filename = parts[1].strip()
            if not block_filename:
                continue
            blocks.append({
                'block_code': block_filename,
                'block_name': block_name,
                'block_filename': block_filename,
            })
        return blocks

    def _parse_tdx_blk(self, text: str) -> List[Dict]:
        """解析通达信板块成分股文件。每行：市场标识+代码（如 1600141 = 600141.SH）。"""
        members: List[Dict] = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            m = _TDX_LINE_RE.match(line)
            if not m:
                continue
            market_digit, code = m.group(1), m.group(2)
            info = self._tdx_market_digit.get(market_digit)
            if not info:
                continue
            members.append({
                'setcode': info['setcode'],
                'code': code,
                'name': '',
            })
        return members

    def _parse_dzh_zxg(self, text: str) -> List[Dict]:
        """解析大智慧自选股文件。Tab 分隔：代码\\t市场[\\t名称]。"""
        favorites: List[Dict] = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            m = _DZH_LINE_RE.match(line)
            if not m:
                logger.debug("DZH zxg 跳过无法匹配的行: %s", line)
                continue
            code = m.group(1).strip()
            market_field = m.group(2).strip()
            name = m.group(3).strip() if m.group(3) else ''
            setcode, _ = self._market_to_setcode(market_field, code)
            favorites.append({
                'setcode': setcode,
                'code': code,
                'name': name,
            })
        return favorites

    def _parse_dzh_blk_binary(self, raw_bytes: bytes) -> List[Dict]:
        """解析大智慧二进制 BLK 板块文件。

        BLK 文件格式（二进制，GBK编码）：
        - 前2字节可能是记录数（小端 uint16），但不同版本格式有差异
        - 股票代码以 SZ/SH + 6位数字 的形式存储
        - 采用扫描方式匹配所有代码，兼容不同记录长度的格式

        返回：[{'setcode': int, 'code': str, 'name': str}, ...]
        """
        members: List[Dict] = []
        if len(raw_bytes) < 8:
            return members

        # 扫描整个文件，匹配 SZ/SH + 6位数字 的模式
        i = 0
        size = len(raw_bytes)
        seen = set()
        while i < size - 8:
            # 尝试匹配 SZ 或 SH 开头
            prefix = raw_bytes[i:i+2]
            if prefix in (b'SZ', b'SH', b'BJ'):
                code_bytes = raw_bytes[i+2:i+8]
                try:
                    code = code_bytes.decode('ascii')
                except UnicodeDecodeError:
                    i += 1
                    continue
                if len(code) == 6 and code.isdigit():
                    market = prefix.decode('ascii')
                    key = (market, code)
                    if key not in seen:
                        seen.add(key)
                        setcode, _ = self._market_to_setcode(market, code)
                        members.append({
                            'setcode': setcode,
                            'code': code,
                            'name': '',
                        })
                    i += 8
                    continue
            i += 1

        return members

    def _parse_ths_zxg(self, text: str) -> List[Dict]:
        """解析同花顺自选股文件。INI 风格，含 [ZXG] 段，每行 6 位代码。"""
        favorites: List[Dict] = []
        in_zxg = False
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            # 段头判断
            if line.startswith('[') and line.endswith(']'):
                in_zxg = (line.upper() == '[ZXG]')
                continue
            if not in_zxg:
                continue
            m = _THS_LINE_RE.match(line)
            if not m:
                continue
            code = m.group(1)
            info = self._code_prefix_map.get(code[0])
            if not info:
                continue
            favorites.append({
                'setcode': info['setcode'],
                'code': code,
                'name': '',
            })
        return favorites

    def _parse_ths_favorites(self, text: str) -> List[Dict]:
        """解析同花顺自选股文件（hexin/ZXG.cfg）。

        GBK 编码，每行一个股票代码，格式可能是纯代码 600000 或带市场前缀 SH600000。
        根据代码前缀判断市场：6开头=SH, 0/3开头=SZ, 8/4开头=BJ。
        若存在 INI 段头（如 [ZXG]）则跳过，兼容纯文本与 INI 风格。
        """
        favorites: List[Dict] = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            # 跳过 INI 段头（如 [ZXG]）
            if line.startswith('[') and line.endswith(']'):
                continue
            m = _THS_FAVORITES_RE.match(line)
            if not m:
                logger.debug("THS ZXG.cfg 跳过无法匹配的行: %s", line)
                continue
            market_prefix, code = m.group(1), m.group(2)
            if market_prefix:
                setcode, _ = self._market_to_setcode(market_prefix, code)
            else:
                info = self._code_prefix_map.get(code[0])
                if not info:
                    logger.debug("THS ZXG.cfg 无法识别市场，跳过代码: %s", code)
                    continue
                setcode = info['setcode']
            favorites.append({
                'setcode': setcode,
                'code': code,
                'name': '',
            })
        return favorites

    def _parse_ths_blocks(self, text: str) -> List[Dict]:
        """解析同花顺板块配置文件（hexin/Block.cfg）。

        格式可能是 INI 或自定义格式。支持 INI 风格的板块定义：
        - [板块名] 段头定义一个板块
        - 段内每行为成员代码（纯代码 600000 或带前缀 SH600000）
        无法识别的行跳过，无法确定格式时返回空列表。
        """
        blocks: List[Dict] = []
        current_block: Optional[str] = None
        current_members: List[Dict] = []

        def _flush_block():
            """保存当前板块到 blocks 列表。"""
            if current_block is not None:
                blocks.append({
                    'block_code': current_block,
                    'block_name': current_block,
                    'members': current_members[:],
                })

        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            # INI 段头判断：[板块名]
            if line.startswith('[') and line.endswith(']'):
                _flush_block()
                current_block = line[1:-1].strip()
                current_members = []
                continue
            # 未进入任何板块段，跳过
            if current_block is None:
                continue
            # 尝试解析成员代码
            m = _THS_FAVORITES_RE.match(line)
            if not m:
                continue
            market_prefix, code = m.group(1), m.group(2)
            if market_prefix:
                setcode, _ = self._market_to_setcode(market_prefix, code)
            else:
                info = self._code_prefix_map.get(code[0])
                if not info:
                    continue
                setcode = info['setcode']
            current_members.append({
                'setcode': setcode,
                'code': code,
                'name': '',
            })
        # 保存最后一个板块
        _flush_block()
        return blocks

    # ------------------------------------------------------------------
    # 同花顺 BlockUpdate INI 解析（板块名称 + 成分股）
    # ------------------------------------------------------------------

    # THS BlockUpdate market 代码到市场标识的映射
    # A股：17=SH, 33=SZ, -105=BJ
    # 港股：-79=港股主板/国企H股, -78=港股, -76=香港ETF
    # 美股：-87=美股(纽交所/纳斯达克), -71=美股, -70=美股, -85=美股, -86=美股
    # 英股：-95=英股
    _THS_MARKET_MAP = {
        '17': 'SH',
        '33': 'SZ',
        '-105': 'BJ',
        # 港股
        '-79': 'HK',
        '-78': 'HK',
        '-76': 'HK',
        # 美股
        '-87': 'US',
        '-71': 'US',
        '-70': 'US',
        '-85': 'US',
        '-86': 'US',
        # 英股
        '-95': 'UK',
    }

    # THS BlockUpdate 文件名到分类的映射
    # 依据每个文件 [BLOCK_NAME_MAP_TABLE] 第一条（根节点名）确定分类：
    #   block_DFF8.ini 首条 DFF8=行业 → A股行业（真正的行业分类）
    #   block_2B.ini   首条 2B=概念 → A股概念
    #   block_CC2B.ini 首条 CC2B=同花顺英股行业 → 英股行业（独立分类，不混入行业）
    #   block_D8FA.ini 首条 D8FA=同花顺美股行业 → 美股行业（归入 us）
    #   block_DACC.ini 首条 DACC=新三板行业 → 新三板行业（归入 neeq）
    # ConfigName 全为 "stockblock_同花顺方案"，无法用于分类识别。
    _THS_BLOCKUPDATE_FILE_MAP = {
        # A股概念板块
        'block_2B.ini': 'concept',      # 概念（A股概念，含地域类子板块）
        'block_C4BC.ini': 'concept',    # 概念索引（一级/二级概念）
        # A股行业板块（仅 block_DFF8 归为 industry，其他市场行业独立分类）
        'block_DFF8.ini': 'industry',   # 行业（A股一/二/三级行业）
        # 港股板块（含港股概念、港股行业、港股特色指数）
        'block_7.ini': 'hk',            # 港股（所有港股/AH股/港股通等）
        'block_CD3D.ini': 'hk',         # 同花顺港股概念
        'block_BA07.ini': 'hk',         # 港股特色指数
        # 美股板块（含美股行业、美股概念、美股ETF）
        'block_D8FA.ini': 'us',         # 同花顺美股行业
        'block_CD3C.ini': 'us',         # 同花顺美股概念
        'block_D2DB.ini': 'us',         # 美股ETF分类
        # 英股板块
        'block_CC2B.ini': 'uk',         # 同花顺英股行业
        # 新三板板块（含新三板行业）
        'block_D8CF.ini': 'neeq',       # 股转(新三板)
        'block_DACC.ini': 'neeq',       # 新三板行业
        # 地区板块
        'block_47.ini': 'region',       # 地域（安徽、北京等）
        # 指数板块（含行业索引、特色指数、统计指数等）
        'block_C4B7.ini': 'index',      # 行业索引（一级/二级行业索引）
        'block_C4BB.ini': 'index',      # 同花顺行业索引
        'block_2.ini': 'index',         # 基金指数（ETF/LOF等）
        'block_C6.ini': 'index',        # 指标股/指数（中证500等）
        'block_D3.ini': 'index',        # 指数类（沪深300等）
        'block_D18F.ini': 'index',      # 上海指数（上证50/180/380等）
        'block_BC0F.ini': 'index',      # 综合指数/风格指数/策略指数
        'block_BF4D.ini': 'index',      # 统计指数
        'block_C0C5.ini': 'index',      # 特色指数（成交前十/资金前十等）
        'block_BA04.ini': 'index',      # 可转债特色指数
        'block_CE32.ini': 'index',      # 大盘风向/统计指数
        'block_C7B2.ini': 'index',      # REITs
        'block_CBBE.ini': 'index',      # 科创板
        'block_CFE3.ini': 'index',      # 深创业板
        'block_DA61.ini': 'index',      # 沪港通/上证380/上证180
        'block_EFFE.ini': 'index',      # 深中小板
        'block_F049.ini': 'index',      # 跳转板块集合（Level2等）
        'block_C2D5.ini': 'index',      # 其他树根节点（A股指数/退市可转债等）
        # 期货板块
        'block_EFA2.ini': 'futures',    # 期货（豆粕/玉米等）
        'block_D9FE.ini': 'futures',    # 期股联动（商品猪肉/煤炭等）
        # 市场分类
        'block_D.ini': 'market',        # 沪深京A股/B股/债券/基金
        # 自定义板块
        'block_22.ini': 'custom',       # 自定义板块（板块1-8）
        # 其他
        'block_DB56.ini': 'other',      # 分时突破预警
    }

    # 空文件列表（80字节，仅含 ConfigInfo 头，无板块数据，跳过）
    _THS_BLOCKUPDATE_EMPTY_FILES = {
        'block_B995.ini', 'block_B996.ini', 'block_B997.ini', 'block_B998.ini',
        'block_B999.ini', 'block_B99A.ini', 'block_B99B.ini', 'block_B99C.ini',
        'block_B99D.ini', 'block_B99E.ini', 'block_B99F.ini', 'block_B9A0.ini',
        'block_B9A9.ini', 'block_B9AB.ini', 'block_B9AC.ini',
        'block_B9C1.ini', 'block_B9C2.ini', 'block_B9C3.ini', 'block_B9C4.ini',
        'block_B9C5.ini', 'block_B9A8.ini',  # hk_industry 索引，无实际成分股
    }

    def _parse_ths_blockupdate_ini(self, text: str) -> Dict:
        """解析同花顺 BlockUpdate INI 文件，返回板块名称和成分股。

        INI 格式：
            [ConfigInfo]
            ConfigName=stockblock_概念板块
            [BLOCK_NAME_MAP_TABLE]
            2B=概念
            DBD0=新能源
            [BLOCK_STOCK_CONTEXT]
            DA4F=17:605567,33:300666,...
            [SUBDIVISION_BLOCK_STOCK_CONTEXT]
            B225=33:001316,...

        返回：{'config_name': str, 'blocks': {block_id: {'name': str, 'members': [str]}},
               'subdivisions': {block_id: {'members': [str]}}}
        """
        result: Dict = {
            'config_name': '',
            'blocks': {},
            'subdivisions': {},
        }
        current_section: Optional[str] = None

        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            # section header
            if line.startswith('[') and line.endswith(']'):
                current_section = line[1:-1].strip()
                continue
            if '=' not in line:
                continue
            key, _, value = line.partition('=')
            key = key.strip()
            value = value.strip()

            if current_section == 'ConfigInfo':
                if key == 'ConfigName':
                    result['config_name'] = value
            elif current_section == 'BLOCK_NAME_MAP_TABLE':
                # block_id = block_name
                result['blocks'].setdefault(key, {
                    'name': value, 'members': []})
            elif current_section == 'BLOCK_STOCK_CONTEXT':
                # block_id = market:code,market:code,...
                members = self._parse_ths_stock_context(value)
                if key in result['blocks']:
                    result['blocks'][key]['members'] = members
                else:
                    result['blocks'][key] = {'name': key, 'members': members}
            elif current_section == 'SUBDIVISION_BLOCK_STOCK_CONTEXT':
                # 细分板块成分股
                members = self._parse_ths_stock_context(value)
                result['subdivisions'][key] = {'members': members}

        return result

    def _parse_ths_stock_context(self, value: str) -> List[str]:
        """解析 THS BLOCK_STOCK_CONTEXT 值，返回成分股代码列表。

        格式：market:code,market:code,...
        如：17:605567,33:300666,-105:920351,-79:HK9995,-87:FCOM
        market 17=SH, 33=SZ, -105=BJ, -79=HK, -87=US, -95=UK

        返回代码格式：
            A股：SH600000 / SZ000001 / BJ920706（市场前缀+6位代码）
            港股：HK9995（保留原始 HK 前缀代码）
            美股：US.FCOM（加 US. 前缀区分）
            英股：UK.SRT
        """
        members: List[str] = []
        for item in value.split(','):
            item = item.strip()
            if not item or ':' not in item:
                continue
            market_code, _, code = item.partition(':')
            market_code = market_code.strip()
            code = code.strip()
            if not code:
                continue
            market = self._THS_MARKET_MAP.get(market_code)
            if market:
                if market in ('SH', 'SZ', 'BJ'):
                    # A股：市场前缀 + 6位代码
                    members.append(f"{market}{code}")
                elif market == 'HK':
                    # 港股：代码已含 HK 前缀（如 HK9995），直接使用
                    members.append(code)
                else:
                    # 美股/英股：加市场前缀区分（如 US.FCOM, UK.SRT）
                    members.append(f"{market}.{code}")
            else:
                # 未知市场代码，仅对纯数字代码按前缀推断（A股兜底）
                if code.isdigit():
                    info = self._code_prefix_map.get(code[0]) if code else None
                    if info:
                        members.append(f"{info['market']}{code}")
                    else:
                        logger.debug("THS 未知 market 代码: %s (code=%s)", market_code, code)
        return members

    # THS block_tree.ini 缓存的层级映射（block_id → {level, parent_id}）
    _ths_block_tree_cache: Dict[str, Dict] = None

    def _parse_ths_block_tree(self, text: str) -> Dict[str, Dict]:
        """解析同花顺 block_tree.ini，构建 block_id → {level, parent_id} 层级映射。

        block_tree.ini 结构：
            [BLOCK_TREE_ROOT]
            1=@10001              (虚拟根 → @10001)

            [@10001]              (顶层板块列表)
            DFF8=@67336           (DFF8=行业，子节点列表 @67336)
            2B=@10043             (2B=概念，子节点列表 @10043)

            [@67336]              (DFF8 的子节点 = 一级行业)
            DFCE=@67294           (DFCE=种植业与林业，有子节点 @67294)
            DFCC=@67292           (DFCC=养殖业，有子节点 @67292)

            [@67294]              (DFCE 的子节点 = 二级行业)
            D118=536871427        (D118=种子生产，叶子节点，无子节点)
            D117=536871427        (D117=粮食种植，叶子节点)

        level 约定（相对于顶层分类板块，如 DFF8=行业）：
            - DFF8（根分类）本身不计入结果
            - DFF8 的直接子节点（DFCE 等）level=1（一级行业）
            - DFCE 的子节点（D118 等）level=2（二级行业）
            - 更深层级 level=3（三级行业）

        Returns:
            {block_id: {'level': int, 'parent_id': str}, ...}
        """
        # 第一遍：解析所有 section 为 {section_name: {key: value}}
        sections: Dict[str, Dict[str, str]] = {}
        current_section: Optional[str] = None
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith('[') and line.endswith(']'):
                current_section = line[1:-1].strip()
                sections.setdefault(current_section, {})
                continue
            if current_section and '=' in line:
                key, _, value = line.partition('=')
                sections[current_section][key.strip()] = value.strip()

        # 第二遍：建立 @xxx → parent_block_id 反向映射
        # 在某个 [@yyy] section 中，若 key=block_id value=@xxx，
        # 则 @xxx 的父节点是 block_id
        ref_to_parent: Dict[str, str] = {}
        for section_name, items in sections.items():
            if section_name in ('ConfigInfo', 'SYSTEM', 'BLOCK_TREE_ROOT'):
                continue
            for key, value in items.items():
                if value.startswith('@'):
                    ref_to_parent[value] = key

        # 第三遍：构建 block_id → children 列表 的正向映射
        block_children: Dict[str, List[str]] = {}
        for section_name, items in sections.items():
            if section_name in ('ConfigInfo', 'SYSTEM', 'BLOCK_TREE_ROOT'):
                continue
            parent_block_id = ref_to_parent.get(section_name)
            if parent_block_id:
                block_children.setdefault(parent_block_id, [])
                block_children[parent_block_id].extend(items.keys())

        # 第四遍：从顶层板块开始 DFS 分配 level
        # 顶层板块（DFF8, 2B, CC2B 等）在 [BLOCK_TREE_ROOT]→[@10001] 中
        # 顶层板块本身 level=0（不加入结果），其子节点 level=1
        result: Dict[str, Dict] = {}

        def _dfs(block_id: str, level: int, parent_id: str):
            # 避免循环引用
            if block_id in result:
                return
            result[block_id] = {'level': level, 'parent_id': parent_id}
            for child_id in block_children.get(block_id, []):
                _dfs(child_id, level + 1, block_id)

        root_items = sections.get('BLOCK_TREE_ROOT', {})
        for root_value in root_items.values():
            if not root_value.startswith('@'):
                continue
            section_data = sections.get(root_value, {})
            for top_block_id in section_data.keys():
                # top_block_id 是顶层板块（如 DFF8），level=0，不加入结果
                # 从其子节点开始 level=1
                for child_id in block_children.get(top_block_id, []):
                    _dfs(child_id, 1, top_block_id)

        return result

    def _get_ths_block_tree_map(self) -> Dict[str, Dict]:
        """获取（带缓存的）THS block_tree.ini 层级映射。

        从 BlockUpdate 目录读取 block_tree.ini 并解析为
        {block_id: {'level': int, 'parent_id': str}}。
        文件不存在或解析失败时返回空字典。
        """
        if self._ths_block_tree_cache is not None:
            return self._ths_block_tree_cache

        blockupdate_dir = self._get_file_path('ths', 'blockupdate_dir')
        if not blockupdate_dir:
            self._ths_block_tree_cache = {}
            return self._ths_block_tree_cache

        tree_file = Path(blockupdate_dir) / 'block_tree.ini'
        if not tree_file.is_file():
            self._ths_block_tree_cache = {}
            return self._ths_block_tree_cache

        text = self._read_text(str(tree_file), self._get_encoding('ths', 'blockupdate_dir'))
        if not text:
            self._ths_block_tree_cache = {}
            return self._ths_block_tree_cache

        try:
            self._ths_block_tree_cache = self._parse_ths_block_tree(text)
        except Exception as e:
            logger.warning("解析 THS block_tree.ini 失败: %s", e)
            self._ths_block_tree_cache = {}
        return self._ths_block_tree_cache

    def _scan_ths_blockupdate_dir(self) -> List[Dict]:
        """扫描同花顺 BlockUpdate 目录，返回文件列表与分类。

        返回：[{'filename': 'block_2B.ini', 'filepath': '/path/to/file', 'category': 'concept'}, ...]
        """
        blockupdate_dir = self._get_file_path('ths', 'blockupdate_dir')
        if not blockupdate_dir:
            return []
        dir_path = Path(blockupdate_dir)
        if not dir_path.is_dir():
            logger.debug("THS BlockUpdate 目录不存在: %s", blockupdate_dir)
            return []

        files: List[Dict] = []
        for entry in dir_path.iterdir():
            if not entry.is_file():
                continue
            fname = entry.name
            if not fname.lower().startswith('block_') or not fname.lower().endswith('.ini'):
                continue
            # 跳过 block_tree.ini（索引文件，非板块数据）
            if fname.lower() == 'block_tree.ini':
                continue
            # 跳过空文件（80字节，仅含 ConfigInfo 头，无板块数据）
            if fname in self._THS_BLOCKUPDATE_EMPTY_FILES:
                continue
            # 跳过未在映射表中的未知文件（避免 other 分类混乱）
            category = self._THS_BLOCKUPDATE_FILE_MAP.get(fname)
            if not category:
                logger.debug("THS 跳过未映射的文件: %s", fname)
                continue
            files.append({
                'filename': fname,
                'filepath': str(entry),
                'category': category,
            })
        return files

    def _get_ths_blockupdate_sectors(self, category: str = None) -> List[Dict]:
        """获取同花顺 BlockUpdate 板块列表（含名称、成分股与层级信息）。

        Args:
            category: 板块分类（concept/industry/index/region/hk/us/uk/futures/
                      neeq/market/custom），None 表示获取所有分类

        返回：[{'block_id': '2B', 'block_name': '概念', 'members': ['SH605567', ...],
                'category': 'concept', 'source_file': 'block_2B.ini',
                'level': 1, 'parent_id': '', 'parent_name': ''}, ...]

        分类通过 _THS_BLOCKUPDATE_FILE_MAP 文件名映射确定（ConfigName 全为
        "stockblock_同花顺方案"，无法用于分类识别）。

        层级信息来自 block_tree.ini：
            - level=1 一级分类（如种植业与林业、养殖业，90 个一级行业）
            - level=2 二级分类（如种子生产、粮食种植，257 个二级行业叶子节点）
            - level=0 未在树中找到（兜底）

        保留策略（参考同花顺官方一级/二级/三级行业分类展示）：
            - level>=1 的分类节点（一级行业等）即使无成分股也保留，is_category=True，
              前端可据此构建"一级→二级"树形展示；
            - 根节点（DFF8/2B 等，level=0 且无成分股）与无层级且无成分股的节点跳过。
        """
        files = self._scan_ths_blockupdate_dir()
        if not files:
            return []

        # 获取层级映射（block_id → {level, parent_id}）
        level_map = self._get_ths_block_tree_map()

        # 第一遍：读取所有文件，构建 block_id → name 全局映射（用于查找 parent_name）
        file_results: List[Tuple[Dict, Dict]] = []
        block_name_map: Dict[str, str] = {}
        for file_info in files:
            fname = file_info['filename']
            filepath = file_info['filepath']
            file_category = file_info['category']

            # 分类过滤
            if category and file_category != category:
                continue

            result = self._read_file_cached(
                filepath, self._get_encoding('ths', 'blockupdate_dir'),
                self._parse_ths_blockupdate_ini)
            if not result:
                continue

            file_results.append((file_info, result))

            # 收集 block_id → name（用于后续查找 parent_name）
            for bid, binfo in result.get('blocks', {}).items():
                bname = binfo.get('name', bid)
                if bname:
                    block_name_map[bid] = bname

        # 第二遍：为每个板块添加层级信息
        sectors: List[Dict] = []
        for file_info, result in file_results:
            fname = file_info['filename']
            detected_category = file_info['category']

            # 提取板块（保留 level>=1 的分类节点，跳过根节点与无层级且无成分股的节点）
            blocks = result.get('blocks', {})
            for block_id, block_info in blocks.items():
                members = block_info.get('members', [])
                level_info = level_map.get(block_id, {})
                level = level_info.get('level', 0)
                parent_id = level_info.get('parent_id', '')
                parent_name = block_name_map.get(parent_id, '')

                # 跳过根节点（DFF8/2B 等，level=0 且无成分股）与无层级且无成分股的节点
                if not members and level < 1:
                    continue

                # level>=1 且无成分股的节点 = 一级行业分类节点（is_category）
                is_category = (not members) and (level >= 1)

                sectors.append({
                    'code': block_id,
                    'name': block_info.get('name', block_id),
                    'block_id': block_id,
                    'block_name': block_info.get('name', block_id),
                    'members': members,
                    'member_count': len(members),
                    'category': detected_category,
                    'source_file': fname,
                    'level': level,
                    'parent_id': parent_id,
                    'parent_name': parent_name,
                    'is_category': is_category,
                })

            # 提取细分板块
            subdivisions = result.get('subdivisions', {})
            for sub_id, sub_info in subdivisions.items():
                members = sub_info.get('members', [])

                level_info = level_map.get(sub_id, {})
                parent_id = level_info.get('parent_id', '')
                parent_name = block_name_map.get(parent_id, '')

                sectors.append({
                    'code': sub_id,
                    'name': sub_id,  # 细分板块无独立名称
                    'block_id': sub_id,
                    'block_name': sub_id,
                    'members': members,
                    'member_count': len(members),
                    'category': detected_category,
                    'source_file': fname,
                    'is_subdivision': True,
                    'level': level_info.get('level', 0),
                    'parent_id': parent_id,
                    'parent_name': parent_name,
                })

        return sectors

    def _parse_tdx_blocknew_cfg(self, data: bytes) -> Dict[str, str]:
        """解析通达信 blocknew.cfg，返回 {板块代码: 板块名称} 映射。

        blocknew.cfg 为固定 120 字节/条的二进制记录，
        每条记录前半部分为板块名称（GBK编码），约50字节处为板块代码（ASCII）。
        """
        result: Dict[str, str] = {}
        record_size = 120
        num_records = len(data) // record_size
        for i in range(num_records):
            offset = i * record_size
            record = data[offset:offset + record_size]
            name_end = 0
            while name_end < min(50, len(record)) and record[name_end] != 0:
                name_end += 1
            if name_end == 0:
                continue
            try:
                name = record[:name_end].decode('gbk')
            except (UnicodeDecodeError, LookupError):
                try:
                    name = record[:name_end].decode('gb2312', errors='ignore')
                except Exception:
                    continue
            code_start = 50
            code_end = code_start
            while code_end < min(100, len(record)) and record[code_end] != 0:
                code_end += 1
            if code_end <= code_start:
                continue
            try:
                code = record[code_start:code_end].decode('ascii', errors='ignore').strip()
            except Exception:
                continue
            if code and name:
                result[code] = name
        return result

    def _scan_tdx_blocknew_dir(self) -> List[Dict]:
        """扫描通达信 blocknew 目录下的 .blk 文件，返回自定义板块列表。

        优先从 blocknew.cfg 读取板块名称（120字节/条二进制记录），
        若 cfg 文件不可用则用文件名作为板块名。
        排除系统文件（zxg.blk / tjg.blk 等）。
        """
        blocknew_dir = self._get_file_path('tdx', 'custom_blocks_index')
        if not blocknew_dir:
            return []
        dir_path = Path(blocknew_dir)
        if not dir_path.is_dir():
            logger.debug("TDX blocknew 目录不存在: %s", blocknew_dir)
            return []

        name_map: Dict[str, str] = {}
        cfg_path = dir_path / 'blocknew.cfg'
        if cfg_path.is_file():
            try:
                name_map = self._read_binary_cached(
                    str(cfg_path), self._parse_tdx_blocknew_cfg)
            except Exception as e:
                logger.debug("解析 TDX blocknew.cfg 失败: %s", e)

        client_cfg = self._paths_config.get('clients', {}).get('tdx', {})
        file_cfg = client_cfg.get('files', {}).get('custom_blocks_index', {})
        exclude_files = set(file_cfg.get(
            'exclude_files', ['zxg.blk', 'tjg.blk', 'error.cfg', 'time.cfg', 'blocknew.cfg']))
        exclude_lower = {f.lower() for f in exclude_files}

        blocks: List[Dict] = []
        try:
            for entry in sorted(dir_path.iterdir()):
                if not entry.is_file():
                    continue
                if entry.suffix.lower() != '.blk':
                    continue
                if entry.name.lower() in exclude_lower:
                    continue
                block_code = entry.stem
                block_name = name_map.get(block_code, block_code)
                blocks.append({
                    'block_code': block_code,
                    'block_name': block_name,
                    'block_filename': block_code,
                })
        except OSError as e:
            logger.warning("扫描 TDX blocknew 目录失败 %s: %s", blocknew_dir, e)
            return []
        return blocks

    def _scan_dzh_userdata_block_dir(self) -> List[Dict]:
        """扫描大智慧 USERDATA/block 目录下的 .BLK 文件，返回自定义板块列表。

        大智慧 USERDATA/block 目录下每个 .BLK 文件是一个板块，
        文件名（不含 .BLK）即为板块名，排除文件名包含"自选股"的文件。
        BLK 文件为二进制格式，用 _parse_dzh_blk_binary 解析。
        """
        block_dir = self._get_file_path('dzh', 'userdata_block_dir')
        if not block_dir:
            return []
        dir_path = Path(block_dir)
        if not dir_path.is_dir():
            logger.debug("DZH USERDATA/block 目录不存在: %s", block_dir)
            return []

        # 从配置读取排除关键词
        client_cfg = self._paths_config.get('clients', {}).get('dzh', {})
        file_cfg = client_cfg.get('files', {}).get('userdata_block_dir', {})
        exclude_keywords = [kw.lower() for kw in file_cfg.get('exclude_keywords', ['自选股'])]

        blocks: List[Dict] = []
        try:
            for entry in sorted(dir_path.iterdir()):
                if not entry.is_file():
                    continue
                if entry.suffix.lower() != '.blk':
                    continue
                block_name = entry.stem  # 文件名（不含 .BLK）
                # 排除包含指定关键词的文件（如自选股）
                name_lower = block_name.lower()
                if any(kw in name_lower for kw in exclude_keywords):
                    continue
                blocks.append({
                    'block_code': block_name,
                    'block_name': block_name,
                    'block_filename': entry.name,
                })
        except OSError as e:
            logger.warning("扫描 DZH USERDATA/block 目录失败 %s: %s", block_dir, e)
            return []
        return blocks

    def _parse_tdx_system_blocks(self, text: str) -> List[Dict]:
        """解析通达信系统板块文件（tdxbk.cfg）。

        管道分隔格式：类型|板块名|板块描述|标志（GB2312编码）。
        示例：1|概念板块|概念板块描述|0

        返回的每个板块字典含 type（类型编号）、type_name（映射后的分类名）、
        type_label（分类中文标签）、block_name、block_desc、flag 字段。
        """
        blocks: List[Dict] = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split('|')
            if len(parts) < 4:
                continue
            block_type = parts[0].strip()
            block_name = parts[1].strip()
            block_desc = parts[2].strip()
            block_flag = parts[3].strip()
            if not block_name:
                continue
            type_info = _TDX_SYSTEM_BLOCK_TYPES.get(block_type, {})
            blocks.append({
                'type': block_type,
                'type_name': type_info.get('name', 'other'),
                'type_label': type_info.get('label', '其他'),
                'block_name': block_name,
                'block_desc': block_desc,
                'flag': block_flag,
            })
        return blocks

    def _get_tdx_system_blocks(self) -> List[Dict]:
        """读取并解析通达信系统板块文件（tdxbk.cfg）。"""
        path = self._get_file_path('tdx', 'system_blocks')
        if not path:
            return []
        result = self._read_file_cached(
            path, self._get_encoding('tdx', 'system_blocks'),
            self._parse_tdx_system_blocks)
        return result or []

    # ------------------------------------------------------------------
    # 通达信 tdxhy.cfg + tdxzs.cfg 解析（行业板块成分股）
    # ------------------------------------------------------------------

    # tdxhy.cfg 行正则：market|code|tdx_industry|||sw_industry
    _TDX_HY_RE = re.compile(r'^([012])\|(\d{6})\|([^|]+)\|\|\|([^|]+)$')
    # tdxzs.cfg 行正则：name|code|type|category|?|desc
    _TDX_ZS_RE = re.compile(r'^([^|]+)\|(\d+)\|(\d+)\|(\d+)\|(\d+)\|([^|]*)$')
    # tdxzs.cfg type 字段到子分类的映射（用于 sector_index 分类下的 sub_type 标识）
    # type=2 行业(145)/type=3 地区(32)/type=4 概念(270)/type=5 风格(158)
    _TDX_ZS_TYPE_MAP = {
        '2': 'industry',
        '3': 'region',
        '4': 'concept',
        '5': 'style',
    }

    def _parse_tdx_industry_mapping(self, text: str) -> Dict[str, List[str]]:
        """解析通达信 tdxhy.cfg，返回按行业代码分组的成分股字典。

        格式：market|code|tdx_industry|||sw_industry
        返回：{'T1001': ['SZ000001', 'SZ000002', ...], ...}
        """
        mapping: Dict[str, List[str]] = {}
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            m = self._TDX_HY_RE.match(line)
            if not m:
                continue
            market_digit, code, tdx_industry, _sw_industry = (
                m.group(1), m.group(2), m.group(3), m.group(4))
            info = self._tdx_market_digit.get(market_digit)
            if not info:
                continue
            stock_code = f"{info['market']}{code}"
            mapping.setdefault(tdx_industry, []).append(stock_code)
        return mapping

    def _get_tdx_industry_members(self) -> Dict[str, List[str]]:
        """读取 tdxhy.cfg 并返回按行业代码分组的成分股字典。

        返回：{'T1001': ['SZ000001', ...], 'T110201': [...], ...}
        文件不存在时返回空字典。
        """
        path = self._get_file_path('tdx', 'tdxhy_cfg')
        if not path:
            return {}
        result = self._read_file_cached(
            path, self._get_encoding('tdx', 'tdxhy_cfg'),
            self._parse_tdx_industry_mapping)
        return result or {}

    def _parse_tdx_sector_indices(self, text: str) -> List[Dict]:
        """解析通达信 tdxzs.cfg，返回板块指数列表。

        格式：name|code|type|category|?|desc
        返回：[{'code': '880201', 'name': '农业种植', 'type': '3', 'category': '1', 'desc': '1'}, ...]
        """
        indices: List[Dict] = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            m = self._TDX_ZS_RE.match(line)
            if not m:
                continue
            indices.append({
                'code': m.group(2),
                'name': m.group(1),
                'type': m.group(3),
                'category': m.group(4),
                'desc': m.group(6),
            })
        return indices

    def _get_tdx_sector_indices(self) -> List[Dict]:
        """读取 tdxzs.cfg 并返回板块指数列表。

        返回：[{'code': '880201', 'name': '农业种植', 'type': '3', ...}, ...]
        文件不存在时返回空列表。
        """
        path = self._get_file_path('tdx', 'tdxzs_cfg')
        if not path:
            return []
        result = self._read_file_cached(
            path, self._get_encoding('tdx', 'tdxzs_cfg'),
            self._parse_tdx_sector_indices)
        return result or []

    # ------------------------------------------------------------------
    # 通达信 infoharbor_block.dat 实时板块成分股解析
    # 格式：头行 #XX_板块名,数量,指数代码,创建日期,更新日期,,
    #       数据行 市场位#6位代码（0=深,1=沪,2=京），逗号分隔
    # 前缀：#GN_=概念, #FG_=风格, #ZS_=指数
    # ------------------------------------------------------------------

    # infoharbor 头行正则：#XX_名称,数量,指数代码,创建日期,更新日期,,
    _TDX_INFOHARBOR_HEAD_RE = re.compile(
        r'^#(GN|FG|ZS)_([^,]+),(\d+),([^,]*),([^,]*),([^,]*),,')

    # 前缀到分类的映射
    _TDX_INFOHARBOR_PREFIX_MAP = {
        'GN': 'concept',
        'FG': 'style',
        'ZS': 'index',
    }

    def _parse_tdx_infoharbor_block(self, text: str) -> Dict[str, List[Dict]]:
        """解析通达信 infoharbor_block.dat 实时板块成分股文件。

        返回按分类分组的板块列表：
            {'concept': [{'code': '880534', 'name': '锂电池', 'members': ['SZ000040', ...]}, ...],
             'style': [...], 'index': [...]}
        市场位映射：0=SZ, 1=SH, 2=BJ（独立解析，不跨数据源匹配）
        """
        grouped: Dict[str, List[Dict]] = {}
        current_block: Optional[Dict] = None
        current_cat = ''
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            m = self._TDX_INFOHARBOR_HEAD_RE.match(line)
            if m:
                prefix = m.group(1)
                block_name = m.group(2)
                count = int(m.group(3) or 0)
                idx_code = m.group(4)
                # 更新日期 = m.group(6)
                current_cat = self._TDX_INFOHARBOR_PREFIX_MAP.get(prefix, 'other')
                current_block = {
                    'code': idx_code,
                    'name': block_name,
                    'members': [],
                    'count_hint': count,
                }
                grouped.setdefault(current_cat, []).append(current_block)
                continue
            # 数据行：0#000040,0#000049,...,1#600072,...,2#920019,
            if current_block is None:
                continue
            for tok in line.split(','):
                tok = tok.strip()
                if '#' not in tok:
                    continue
                market_digit, _, code = tok.partition('#')
                if len(code) != 6:
                    continue
                info = self._tdx_market_digit.get(market_digit)
                if not info:
                    continue
                full_code = f"{info['market']}{code}"
                current_block['members'].append(full_code)
        return grouped

    def _get_tdx_infoharbor_blocks(self) -> Dict[str, List[Dict]]:
        """读取并缓存 infoharbor_block.dat，返回按分类分组的板块列表。"""
        path = self._get_file_path('tdx', 'infoharbor_block')
        if not path:
            return {}
        result = self._read_file_cached(
            path, self._get_encoding('tdx', 'infoharbor_block'),
            self._parse_tdx_infoharbor_block)
        return result or {}

    # ------------------------------------------------------------------
    # 通达信 base.dbf 地区板块成分股解析（DBF 格式）
    # ------------------------------------------------------------------

    def _parse_tdx_base_dbf_region(self, raw: bytes) -> Dict[str, List[str]]:
        """解析 base.dbf 的 DY 字段，返回地区代码→成分股列表映射。

        DBF/dBASE III 格式：
        - 头部 32 字节，记录数在偏移 4-7（小端），记录长度在偏移 10-11
        - 字段定义每字段 32 字节，以 0x0D 结束
        - 记录首字节为删除标志（0x20=正常, 0x2A=删除）
        - SC 字段（市场标志）在记录内偏移 1，长度 1（'0'=深圳, '1'=上海, '2'=北京）
        - GPDM 字段（股票代码）在记录内偏移 2，长度 6
        - DY 字段（地区代码）在记录内偏移 450，长度 3

        DY 代码 1-32 对应 tdxzs.cfg type=3 的 32 个地区（序列号=DY代码）
        DY=0 表示非 A 股股票（指数/债券/基金等），跳过

        返回：{'1': ['SH600001', ...], '7': ['SZ000008', ...], ...}
        """
        if len(raw) < 32:
            return {}
        # 解析 DBF 头部
        record_count = int.from_bytes(raw[4:8], 'little')
        header_size = int.from_bytes(raw[8:10], 'little')
        record_size = int.from_bytes(raw[10:12], 'little')
        if record_size == 0 or record_count == 0:
            return {}

        # 字段偏移（依据 base.dbf 实际字段定义）：
        # SC(市场,offset=1,len=1) + GPDM(代码,offset=2,len=6) + ... + DY(地区,offset=450,len=3)
        sc_offset = 1
        code_offset = 2
        code_len = 6
        dy_offset = 450
        dy_len = 3

        region_map: Dict[str, List[str]] = {}
        data_start = header_size
        for i in range(record_count):
            rec_start = data_start + i * record_size
            if rec_start + record_size > len(raw):
                break
            # 删除标志
            del_flag = raw[rec_start]
            if del_flag == 0x2A:  # 已删除
                continue
            # 市场标志 SC（'0'=深圳, '1'=上海, '2'=北京）
            sc_byte = raw[rec_start + sc_offset: rec_start + sc_offset + 1]
            sc = sc_byte.decode('ascii', errors='replace').strip()
            # 股票代码 GPDM
            code_bytes = raw[rec_start + code_offset: rec_start + code_offset + code_len]
            code = code_bytes.decode('ascii', errors='replace').strip()
            if not code or not code.isdigit() or len(code) != 6:
                continue
            # 地区代码 DY
            dy_bytes = raw[rec_start + dy_offset: rec_start + dy_offset + dy_len]
            dy = dy_bytes.decode('ascii', errors='replace').strip()
            if not dy or not dy.isdigit() or dy == '0':
                continue
            # 市场判断：优先用 SC 字段，兜底用代码前缀
            market = None
            if sc == '0':
                market = 'SZ'
            elif sc == '1':
                market = 'SH'
            elif sc == '2':
                market = 'BJ'
            if not market:
                first = code[0]
                info = self._code_prefix_map.get(first)
                if info:
                    market = info['market']
                elif first == '9':
                    market = 'SH'
                else:
                    market = 'SZ'
            full_code = f"{market}{code}"
            region_map.setdefault(dy, []).append(full_code)
        return region_map

    def _get_tdx_base_dbf_region_members(self) -> Dict[str, List[str]]:
        """读取 base.dbf 并返回地区代码→成分股列表映射。

        返回：{'1': ['SH600001', ...], '7': ['SZ000008', ...], ...}
        """
        path = self._get_file_path('tdx', 'base_dbf')
        if not path:
            return {}
        try:
            with open(path, 'rb') as f:
                raw = f.read()
        except OSError as e:
            logger.warning("读取 base.dbf 失败: %s", e)
            return {}
        return self._parse_tdx_base_dbf_region(raw)

    # ------------------------------------------------------------------
    # 股票名称映射（infoharbor_ex.code）
    # ------------------------------------------------------------------

    def _parse_tdx_stock_names(self, text: str) -> Dict[str, str]:
        """解析 infoharbor_ex.code 文件，返回 {full_code: name} 映射。

        格式：股票代码|股票名称|关键词列表（如 000001|平安银行|平安保险,谢永林）
        股票代码 6 位数字按前缀推断市场（6→SH, 0/3→SZ, 4/8→BJ）
        """
        name_map: Dict[str, str] = {}
        for line in text.splitlines():
            line = line.strip()
            if not line or '|' not in line:
                continue
            parts = line.split('|', 2)
            if len(parts) < 2:
                continue
            code = parts[0].strip()
            name = parts[1].strip()
            if len(code) != 6 or not code.isdigit():
                continue
            # 按代码前缀推断市场
            first = code[0]
            info = self._code_prefix_map.get(first)
            if not info:
                if first == '9':
                    market = 'SH'
                elif first == '2':
                    market = 'SZ'
                else:
                    market = 'SZ'
            else:
                market = info['market']
            full_code = f"{market}{code}"
            if name:
                name_map[full_code] = name
        return name_map

    def _parse_tdx_bj_stock_names(self, text: str) -> Dict[str, str]:
        """解析 addedcode_bj.cfg 文件，返回 {full_code: name} 映射。

        格式：市场标志|旧代码|新代码|股票名称(含状态)|日期
        如：44|832000|920000|安徽凤凰(已切换)|20251009

        同时注册旧代码和新代码（均以 BJ 为市场前缀），名称去除状态后缀。
        """
        name_map: Dict[str, str] = {}
        for line in text.splitlines():
            line = line.strip()
            if not line or '|' not in line:
                continue
            parts = line.split('|')
            if len(parts) < 4:
                continue
            old_code = parts[1].strip()
            new_code = parts[2].strip()
            raw_name = parts[3].strip()
            # 去除状态后缀（如 "(已切换)"、"(已转板)"）
            name = re.sub(r'\([^)]*\)$', '', raw_name).strip()
            if not name:
                continue
            # 旧代码（430xxx, 830xxx, 870xxx, 874xxx 等）
            if len(old_code) == 6 and old_code.isdigit():
                name_map[f"BJ{old_code}"] = name
            # 新代码（920xxx）
            if len(new_code) == 6 and new_code.isdigit():
                name_map[f"BJ{new_code}"] = name
        return name_map

    def _parse_ths_stockspirit_json(self, text: str) -> Dict[str, str]:
        """解析同花顺 stockspirit.json 文件，返回 {full_code: name} 映射。

        格式：JSON 字典 {"股票名": "代码.市场", ...}
        如：{"达瑞电子": "301696.SZ", "浦发银行": "600000.SH", ...}

        反转为 {MARKET+code: name} 格式，与 TDX 代码格式一致。
        """
        import json as _json
        name_map: Dict[str, str] = {}
        try:
            data = _json.loads(text)
        except Exception as e:
            logger.warning("解析 THS stockspirit.json 失败: %s", e)
            return name_map
        if not isinstance(data, dict):
            return name_map
        # 反转 name→code 为 code→name
        for name, code_val in data.items():
            name = str(name).strip()
            code_val = str(code_val).strip()
            if not name or not code_val or '.' not in code_val:
                continue
            # 格式: "301696.SZ" → "SZ301696"
            parts = code_val.split('.')
            if len(parts) != 2:
                continue
            num_code, market = parts[0].strip(), parts[1].strip().upper()
            if len(num_code) == 6 and num_code.isdigit() and market in ('SH', 'SZ', 'BJ'):
                full_code = f"{market}{num_code}"
                name_map[full_code] = name
        return name_map

    _stock_name_cache: Optional[Dict[str, str]] = None

    # profile.dat 固定记录长度（字节）
    _TDX_PROFILE_RECORD_SIZE = 64

    def _parse_tdx_profile_dat(self, raw: bytes) -> Dict[str, str]:
        """解析 profile.dat 二进制文件，返回 {SZ+code: name} 映射。

        格式：固定 64 字节记录，每条 \\x00 + 6位代码 + \\x00 + 名称(GBK, 填充\\x00) + 其他数据。
        含历史名称（ST/*ST/S 前缀），取最后一条记录作为当前名称。
        profile.dat 仅含深圳市场股票（SZ0/3 A 股 + SZ2 B 股）。
        """
        name_map: Dict[str, str] = {}
        size = len(raw)
        record_size = self._TDX_PROFILE_RECORD_SIZE
        # 按固定记录长度遍历
        for offset in range(0, size - 8, record_size):
            # 记录首字节应为 \x00，之后是 6 位数字代码
            if raw[offset] != 0:
                continue
            code_bytes = raw[offset + 1: offset + 7]
            try:
                code_str = code_bytes.decode('ascii')
            except UnicodeDecodeError:
                continue
            if not (len(code_str) == 6 and code_str.isdigit()):
                continue
            # 代码后应为 \x00，之后是名称（到下一个 \x00）
            if raw[offset + 7] != 0:
                continue
            name_start = offset + 8
            name_end = raw.find(b'\x00', name_start)
            if name_end < 0 or name_end >= offset + record_size:
                name_end = offset + record_size
            name_bytes = raw[name_start:name_end].rstrip(b'\x00')
            if not name_bytes:
                continue
            try:
                name = name_bytes.decode('gbk')
            except UnicodeDecodeError:
                try:
                    name = name_bytes.decode('gb18030', errors='replace')
                except Exception:
                    continue
            # profile.dat 只含深圳股票：0/3 开头 A 股，20 开头 B 股
            # 取最后一条记录（最新名称），覆盖之前的历史名称
            name_map[f"SZ{code_str}"] = name
        return name_map

    def _get_tdx_profile_names(self) -> Dict[str, str]:
        """读取 profile.dat 并返回 {SZ+code: name} 映射。

        profile.dat 含深圳股票历史名称，可补全 infoharbor_ex.code 缺失的 SZ2 B 股名。
        """
        path = self._get_file_path('tdx', 'profile_dat')
        if not path:
            return {}
        try:
            with open(path, 'rb') as f:
                raw = f.read()
        except OSError as e:
            logger.warning("读取 profile.dat 失败: %s", e)
            return {}
        try:
            return self._parse_tdx_profile_dat(raw)
        except Exception as e:
            logger.warning("解析 profile.dat 失败: %s", e)
            return {}

    def get_stock_name_map(self) -> Dict[str, str]:
        """获取股票代码→名称映射（供 API 层补全个股名）。

        数据来源（合并多个文件以覆盖所有股票）：
        1. TDX infoharbor_ex.code（5500+ 只 A 股名称，不含北交所）
        2. TDX addedcode_bj.cfg（300+ 只北交所股票名称，补全 BJ 股票）
        3. THS stockspirit.json（6287 条股票名，补充 TDX 缺失的名称）
        4. TDX profile.dat（929 只深圳股票历史名称，补全 SZ2 B 股等缺失名称）

        返回：{'SH600000': '浦发银行', 'SZ000001': '平安银行', 'BJ920017': '星昊医药', ...}
        """
        if self._stock_name_cache is not None:
            return self._stock_name_cache

        name_map: Dict[str, str] = {}

        # 1. 主数据源：TDX infoharbor_ex.code（A股名称，不含BJ）
        path = self._get_file_path('tdx', 'infoharbor_ex_code')
        if path:
            result = self._read_file_cached(
                path, self._get_encoding('tdx', 'infoharbor_ex_code'),
                self._parse_tdx_stock_names)
            if result:
                name_map.update(result)

        # 2. 补充数据源：TDX addedcode_bj.cfg（北交所股票名称）
        bj_path = self._get_file_path('tdx', 'addedcode_bj')
        if bj_path:
            bj_result = self._read_file_cached(
                bj_path, self._get_encoding('tdx', 'addedcode_bj'),
                self._parse_tdx_bj_stock_names)
            if bj_result:
                name_map.update(bj_result)

        # 3. 补充数据源：THS stockspirit.json（补充 TDX 缺失的名称）
        ths_path = self._get_file_path('ths', 'stockspirit_json')
        if ths_path:
            ths_result = self._read_file_cached(
                ths_path, self._get_encoding('ths', 'stockspirit_json'),
                self._parse_ths_stockspirit_json)
            if ths_result:
                # 仅补充缺失的名称，不覆盖已有名称
                for code, name in ths_result.items():
                    if code not in name_map and name:
                        name_map[code] = name

        # 4. 补充数据源：TDX profile.dat（深圳股票历史名称，补全 SZ2 B 股等）
        profile_result = self._get_tdx_profile_names()
        if profile_result:
            for code, name in profile_result.items():
                if code not in name_map and name:
                    name_map[code] = name

        self._stock_name_cache = name_map
        return self._stock_name_cache

    def _parse_dzh_block_ini(self, text: str) -> List[str]:
        """解析大智慧板块配置文件（block.ini）。

        INI 格式，[BlockInfo] 段的 SysBlock 键含空格分隔的板块名列表。
        """
        block_names: List[str] = []
        in_block_info = False
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            # 段头判断
            if line.startswith('[') and line.endswith(']'):
                in_block_info = (line.upper() == '[BLOCKINFO]')
                continue
            if not in_block_info:
                continue
            # 键值对
            if '=' not in line:
                continue
            key, _, value = line.partition('=')
            if key.strip().upper() == 'SYSBLOCK':
                names = value.strip().split()
                block_names.extend(names)
        return block_names

    # ------------------------------------------------------------------
    # 大智慧 classtree XML 解析（分类树结构）
    # ------------------------------------------------------------------

    # classtree XML 文件名前缀到分类的映射
    _DZH_CLASSTREE_PREFIX_MAP = {
        'bkgn': 'concept',
        'bkhy': 'industry',
        'bkzs': 'index',
        'gphq_dzhhy': 'industry',
        'gphq': 'stock_class',
        'indextree': 'index',
        'hszs': 'index',
        'jjbk': 'fund',
        'etf': 'fund',
    }

    # classtree XML 文件到分类的精确映射（优先于前缀匹配）
    _DZH_CLASSTREE_FILE_MAP = {
        'bkgnzf.xml': 'concept',
        'bkhyzf.xml': 'industry',
        'bkzs.xml': 'index',
        'gphq_dzhhy.xml': 'industry',
    }

    def _parse_dzh_classtree_xml(self, text: str, filename: str = '') -> List[Dict]:
        """解析大智慧 classtree XML 文件，返回分类树结构。

        XML 格式：<classes><class name="..."><subclass name="..." node="..."/></class></classes>
        返回：[{'class_name': '...', 'subclasses': [{'name': '...', 'node': '...'}, ...]}]
        """
        import xml.etree.ElementTree as ET
        try:
            root = ET.fromstring(text)
        except ET.ParseError as e:
            logger.warning("DZH classtree XML 解析失败 (%s): %s", filename, e)
            return []

        classes: List[Dict] = []
        for cls in root.findall('class'):
            class_name = cls.get('name', '')
            subclasses: List[Dict] = []
            for sub in cls.findall('subclass'):
                subclasses.append({
                    'name': sub.get('name', ''),
                    'node': sub.get('node', ''),
                    'scriptfile': sub.get('scriptfile', ''),
                })
            classes.append({
                'class_name': class_name,
                'subclasses': subclasses,
            })
        return classes

    def _get_dzh_classtree(self, category: str) -> List[Dict]:
        """获取指定分类的大智慧分类树。

        根据 category 选择对应 XML 文件：
        - concept → bkgnzf.xml
        - industry → gphq_dzhhy.xml
        - index → bkzs.xml

        返回分类树结构列表，文件不存在时返回空列表。
        """
        file_map = {
            'concept': 'bkgnzf.xml',
            'industry': 'gphq_dzhhy.xml',
            'index': 'bkzs.xml',
        }
        filename = file_map.get(category)
        if not filename:
            return []

        classtree_dir = self._get_file_path('dzh', 'classtree_dir')
        if not classtree_dir:
            return []
        xml_path = os.path.join(classtree_dir, filename)
        if not os.path.isfile(xml_path):
            logger.debug("DZH classtree XML 不存在: %s", xml_path)
            return []

        result = self._read_file_cached(
            xml_path, self._get_encoding('dzh', 'classtree_dir'),
            lambda text: self._parse_dzh_classtree_xml(text, filename))
        return result or []

    def _scan_dzh_classtree_dir(self) -> Dict[str, List[str]]:
        """扫描大智慧 classtree 目录，按分类分组返回文件名列表。

        返回：{'concept': ['bkgnzf.xml', ...], 'industry': ['gphq_dzhhy.xml', ...], ...}
        """
        classtree_dir = self._get_file_path('dzh', 'classtree_dir')
        if not classtree_dir:
            return {}
        dir_path = Path(classtree_dir)
        if not dir_path.is_dir():
            return {}

        result: Dict[str, List[str]] = {}
        for entry in dir_path.iterdir():
            if not entry.is_file() or entry.suffix.lower() != '.xml':
                continue
            fname = entry.name.lower()
            # 精确映射优先
            category = self._DZH_CLASSTREE_FILE_MAP.get(fname)
            if not category:
                # 前缀匹配
                for prefix, cat in self._DZH_CLASSTREE_PREFIX_MAP.items():
                    if fname.startswith(prefix):
                        category = cat
                        break
            if category:
                result.setdefault(category, []).append(entry.name)
        return result

    # ------------------------------------------------------------------
    # 大智慧 full.ABK 解析（板块成分股）
    # ------------------------------------------------------------------

    # full.ABK 条目正则：<板块名><7位数字板块ID>= <空格分隔的代码>
    _DZH_ABK_ENTRY_RE = re.compile(r'^(.+?)(\d{7})?$')
    # full.ABK section 名称到分类的映射
    _DZH_ABK_SECTION_MAP = {
        '大智慧概念': 'concept',
        '大智慧行业(经典)': 'industry',
        '申万行业': 'industry',
        '证监会行业': 'industry',
        '恒生行业分类': 'industry',
        '申万指数': 'index',
        '中证指数': 'index',
        '沪市指数': 'index',
        '深市指数': 'index',
        '国证指数': 'index',
        '恒生指数': 'index',
        '恒生主题指数': 'index',
        '富时指数': 'index',
        '地区板块': 'region',
        '市场分类': 'market',
        '自定义板块': 'custom',
    }

    def _parse_dzh_abk(self, text: str) -> Dict[str, Dict[str, Dict]]:
        """解析大智慧 full.ABK 文本内容，返回按 section 和板块ID组织的字典。

        格式：
            [section_name]
            板块名板块ID= 股票代码1 股票代码2 ...

        返回：{section_name: {block_id: {'name': str, 'codes': [str], 'id': str}}}
        无数字ID的条目以板块名为 key。
        """
        sections: Dict[str, Dict[str, Dict]] = {}
        current_section: Optional[str] = None

        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            # section header
            if line.startswith('[') and line.endswith(']'):
                current_section = line[1:-1].strip()
                sections.setdefault(current_section, {})
                continue
            if current_section is None or '=' not in line:
                continue
            left, _, right = line.partition('=')
            left = left.strip()
            codes = right.strip().split()
            # 分离板块名和数字ID
            m = self._DZH_ABK_ENTRY_RE.match(left)
            if not m:
                continue
            name = m.group(1)
            block_id = m.group(2)
            key = block_id if block_id else name
            sections[current_section][key] = {
                'name': name,
                'id': block_id,
                'codes': codes,
            }
        return sections

    def _get_dzh_abk_data(self) -> Dict[str, Dict[str, Dict]]:
        """读取 full.ABK 并返回解析后的字典。

        先加载 full.ABK，再用 inc.ABK 的条目覆盖（增量更新）。
        文件不存在时返回空字典。
        """
        path = self._get_file_path('dzh', 'full_abk')
        if not path:
            return {}
        result = self._read_file_cached(
            path, self._get_encoding('dzh', 'full_abk'),
            self._parse_dzh_abk)
        if not result:
            return {}

        # 增量更新：用 inc.ABK 覆盖同 section 同 block_id 的条目
        client_cfg = self._paths_config.get('clients', {}).get('dzh', {})
        file_cfg = client_cfg.get('files', {}).get('full_abk', {})
        inc_path_rel = file_cfg.get('inc_abk_path')
        if inc_path_rel:
            dzh_home = self._homes.get('dzh', '')
            if dzh_home:
                inc_path = os.path.join(dzh_home, inc_path_rel)
                if os.path.isfile(inc_path):
                    inc_data = self._read_file_cached(
                        inc_path, 'gbk', self._parse_dzh_abk)
                    if inc_data:
                        for section, blocks in inc_data.items():
                            if section not in result:
                                result[section] = {}
                            result[section].update(blocks)
                        logger.info("DZH inc.ABK 增量更新已合并 (%d sections)",
                                    len(inc_data))
        return result

    def _get_dzh_abk_members(self, node_id: str) -> List[str]:
        """根据 classtree XML 的 node ID 获取板块成分股。

        node ID 转换规则：补 "0" 前缀变成 7 位 ABK block ID
        （如 "600019" → "0600019"）。

        返回成分股代码列表（如 ['SH600000', 'SZ000001', ...]）。
        找不到时返回空列表。
        """
        abk_data = self._get_dzh_abk_data()
        if not abk_data:
            return []
        abk_id = '0' + node_id if len(node_id) == 6 else node_id
        for section_name, blocks in abk_data.items():
            block = blocks.get(abk_id)
            if block:
                return block.get('codes', [])
        logger.debug("DZH ABK 未找到 node=%s (abk_id=%s)", node_id, abk_id)
        return []

    def _get_dzh_abk_sectors_by_category(self, category: str) -> List[Dict]:
        """按分类获取大智慧 ABK 板块列表（含名称和成分股）。

        根据 section 名称映射到分类，返回该分类下所有板块。
        每个板块含 block_id、name、members（成分股列表）。
        """
        abk_data = self._get_dzh_abk_data()
        if not abk_data:
            return []

        # 找到属于该分类的所有 section
        target_sections = [
            sec for sec, cat in self._DZH_ABK_SECTION_MAP.items()
            if cat == category
        ]

        sectors: List[Dict] = []
        for section_name in target_sections:
            blocks = abk_data.get(section_name, {})
            for block_id, block_info in blocks.items():
                codes = block_info.get('codes', [])
                sectors.append({
                    'code': block_id,
                    'name': block_info.get('name', ''),
                    'block_id': block_id,
                    'block_name': block_info.get('name', ''),
                    'members': codes,
                    'member_count': len(codes),
                    'section': section_name,
                })
        return sectors

    # ------------------------------------------------------------------
    # 自选股 / 自定义板块接口
    # ------------------------------------------------------------------

    def get_user_sector(self) -> Dict[str, List[Dict]]:
        """解析自选股文件，返回 {'favorites': [...], 'custom_blocks': [...]}。

        依次尝试 TDX → DZH → THS，任一可用即返回结果。
        - favorites 元素：{'setcode': int, 'code': str, 'name': str}
        - custom_blocks 元素：{'block_code': str, 'block_name': str, 'members': [...]}
        """
        for client in _CLIENT_ORDER:
            if client not in self._homes:
                continue
            result = self._get_user_sector_for_client(client)
            if result['favorites'] or result['custom_blocks']:
                logger.debug("get_user_sector 使用 %s，自选股 %d 个，自定义板块 %d 个",
                             client, len(result['favorites']), len(result['custom_blocks']))
                return result
        return {'favorites': [], 'custom_blocks': []}

    def _get_user_sector_for_client(self, client: str) -> Dict[str, List[Dict]]:
        """获取指定客户端的自选股与自定义板块。"""
        if client == 'tdx':
            return self._get_user_sector_tdx()
        if client == 'dzh':
            return self._get_user_sector_dzh()
        if client == 'ths':
            return self._get_user_sector_ths()
        return {'favorites': [], 'custom_blocks': []}

    def _get_user_sector_tdx(self) -> Dict[str, List[Dict]]:
        """获取通达信自选股与自定义板块。"""
        favorites: List[Dict] = []
        custom_blocks: List[Dict] = []

        # 自选股（zxg.blk，与 .blk 格式相同：市场位+6位代码）
        zxg_path = self._get_file_path('tdx', 'favorites')
        if zxg_path:
            parsed = self._read_file_cached(
                zxg_path, self._get_encoding('tdx', 'favorites'), self._parse_tdx_zxg)
            if parsed:
                favorites = parsed

        # 自定义板块：扫描 blocknew 目录下的 .blk 文件
        blocks = self._scan_tdx_blocknew_dir()
        for blk in blocks:
            block_filename = blk.get('block_filename', '')
            if not block_filename:
                continue
            blk_path = self._get_block_file_path('tdx', block_filename)
            if not blk_path:
                continue
            members = self._read_file_cached(
                blk_path, self._get_encoding('tdx', 'block_members_pattern'),
                self._parse_tdx_blk)
            custom_blocks.append({
                'block_code': blk.get('block_code', block_filename),
                'block_name': blk.get('block_name', ''),
                'members': members or [],
            })
        return {'favorites': favorites, 'custom_blocks': custom_blocks}

    def _get_user_sector_dzh(self) -> Dict[str, List[Dict]]:
        """获取大智慧自选股与自定义板块。

        自选股优先解析 USERDATA/block/自选股.BLK（二进制格式），降级到 cfg/zxg.cfg；
        自定义板块优先从 ABK 文件的"自定义板块"section 获取（权威来源），
        降级到 USERDATA/block/ 目录扫描（需排除系统板块）。
        """
        favorites: List[Dict] = []

        # 自选股：优先 USERDATA/block/自选股.BLK（二进制）
        userdata_block_dir = self._get_file_path('dzh', 'userdata_block_dir')
        if userdata_block_dir:
            client_cfg = self._paths_config.get('clients', {}).get('dzh', {})
            file_cfg = client_cfg.get('files', {}).get('userdata_block_dir', {})
            fav_filename = file_cfg.get('favorites_filename', '自选股.BLK')
            fav_path = Path(userdata_block_dir) / fav_filename
            if fav_path.is_file():
                parsed = self._read_binary_cached(
                    str(fav_path), self._parse_dzh_blk_binary)
                if parsed:
                    favorites = parsed

        # 降级：cfg/zxg.cfg（文本格式）
        if not favorites:
            zxg_path = self._get_file_path('dzh', 'favorites')
            if zxg_path:
                parsed = self._read_file_cached(
                    zxg_path, self._get_encoding('dzh', 'favorites'),
                    self._parse_dzh_zxg)
                if parsed:
                    favorites = parsed

        # 自定义板块：优先从 ABK 的"自定义板块"section 获取（权威来源）
        custom_blocks: List[Dict] = []
        abk_data = self._get_dzh_abk_data()
        if abk_data:
            for sec_name, blks in abk_data.items():
                if '自定义' not in sec_name:
                    continue
                for blk_id, blk_info in blks.items():
                    blk_name = blk_info.get('name', '')
                    if not blk_name:
                        continue
                    codes = blk_info.get('codes', [])
                    members = []
                    for c in codes:
                        if isinstance(c, str) and len(c) >= 6:
                            code = c[-6:] if len(c) > 6 else c
                            info = self._code_prefix_map.get(code[0])
                            setcode = info['setcode'] if info else 0
                            members.append({
                                'setcode': setcode,
                                'code': code,
                                'name': '',
                            })
                    custom_blocks.append({
                        'block_code': blk_id,
                        'block_name': blk_name,
                        'members': members,
                    })

        # 降级：USERDATA/block 目录扫描（排除明显的系统板块）
        if not custom_blocks and userdata_block_dir:
            blocks = self._scan_dzh_userdata_block_dir()
            if blocks:
                for blk in blocks:
                    block_filename = blk.get('block_filename', '')
                    if not block_filename:
                        continue
                    blk_path = Path(userdata_block_dir) / block_filename
                    if not blk_path.is_file():
                        continue
                    members = self._read_binary_cached(
                        str(blk_path), self._parse_dzh_blk_binary)
                    custom_blocks.append({
                        'block_code': blk.get('block_code', ''),
                        'block_name': blk.get('block_name', ''),
                        'members': members or [],
                    })

        return {'favorites': favorites, 'custom_blocks': custom_blocks}

    def _find_ths_self_stock_json(self) -> Optional[str]:
        """查找同花顺 SelfStockInfo.json 文件（游客或登录用户）。"""
        ths_home = self._homes.get('ths')
        if not ths_home:
            return None
        home_path = Path(ths_home)
        candidates = []
        try:
            for entry in home_path.iterdir():
                if not entry.is_dir():
                    continue
                if (entry.name.startswith('thsguest_')
                        or entry.name.startswith('userdata')
                        or entry.name == '股神之师'):
                    candidates.append(entry / 'SelfStockInfo.json')
        except OSError:
            pass
        best_path = None
        best_size = 0
        for cand in candidates:
            if cand.is_file():
                try:
                    size = cand.stat().st_size
                    if size > best_size:
                        best_size = size
                        best_path = str(cand)
                except OSError:
                    pass
        if best_path and best_size > 10:
            return best_path
        return None

    def _parse_ths_self_stock_json(self, content: str) -> List[Dict]:
        """解析同花顺 SelfStockInfo.json，返回自选股列表。"""
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            return []
        if not data or not isinstance(data, list):
            return []
        result: List[Dict] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            code = str(item.get('C', '') or item.get('code', '')).strip()
            market = item.get('M', '') or item.get('market', '')
            name = str(item.get('N', '') or item.get('name', '')).strip()
            if not code:
                continue
            setcode = 0
            if market == 17 or market == 'SH':
                setcode = 1
            elif market == 33 or market == 'SZ':
                setcode = 0
            elif market == -105 or market == 'BJ':
                setcode = 2
            else:
                info = self._code_prefix_map.get(code[0])
                if info:
                    setcode = info['setcode']
            result.append({
                'setcode': setcode,
                'code': code,
                'name': name,
            })
        return result

    def _get_user_sector_ths(self) -> Dict[str, List[Dict]]:
        """获取同花顺自选股与自定义板块。

        自选股：优先从 SelfStockInfo.json 获取（游客/登录用户目录），
               再尝试 BlockUpdate 自定义板块中的"自选股"相关板块，
               降级到 hexin/ZXG.cfg；
        自定义板块：优先从 BlockUpdate 的 custom 分类获取（block_22.ini），
                   降级到 hexin/Block.cfg 或 custom_block/ 目录。
        """
        favorites: List[Dict] = []
        custom_blocks: List[Dict] = []

        # 0. 自选股：优先从 SelfStockInfo.json 获取
        self_stock_path = self._find_ths_self_stock_json()
        if self_stock_path:
            parsed = self._read_file_cached(
                self_stock_path, 'utf-8', self._parse_ths_self_stock_json)
            if parsed:
                favorites = parsed

        # 1. 自定义板块：优先从 BlockUpdate 的 custom 分类获取
        custom_sectors = self._get_ths_blockupdate_sectors('custom')
        if custom_sectors:
            for sec in custom_sectors:
                members_raw = []
                for m in sec.get('members', []):
                    # 从 SH600000 / SZ000001 格式解析
                    if len(m) >= 8 and m[:2] in ('SH', 'SZ', 'BJ'):
                        code = m[2:8]
                        market = m[:2]
                        setcode, _ = self._market_to_setcode(market, code)
                        members_raw.append({
                            'setcode': setcode,
                            'code': code,
                            'name': '',
                        })
                    elif len(m) == 6 and m.isdigit():
                        info = self._code_prefix_map.get(m[0])
                        if info:
                            members_raw.append({
                                'setcode': info['setcode'],
                                'code': m,
                                'name': '',
                            })
                custom_blocks.append({
                    'block_code': sec.get('code', ''),
                    'block_name': sec.get('name', ''),
                    'members': members_raw,
                })

        # 2. 自选股：尝试从 BlockUpdate 自定义板块中找"自选股"相关板块
        if not favorites and custom_sectors:
            for sec in custom_sectors:
                sec_name = sec.get('name', '')
                if '自选股' in sec_name or 'zxg' in sec_name.lower():
                    for m in sec.get('members', []):
                        if len(m) >= 8 and m[:2] in ('SH', 'SZ', 'BJ'):
                            code = m[2:8]
                            market = m[:2]
                            setcode, _ = self._market_to_setcode(market, code)
                            favorites.append({
                                'setcode': setcode,
                                'code': code,
                                'name': '',
                            })
                        elif len(m) == 6 and m.isdigit():
                            info = self._code_prefix_map.get(m[0])
                            if info:
                                favorites.append({
                                    'setcode': info['setcode'],
                                    'code': m,
                                    'name': '',
                                })
                    if favorites:
                        break

        # 3. 自选股降级：hexin/ZXG.cfg（文本格式）
        if not favorites:
            zxg_path = self._get_file_path('ths', 'favorites_zxg')
            if zxg_path:
                parsed = self._read_file_cached(
                    zxg_path, self._get_encoding('ths', 'favorites_zxg'),
                    self._parse_ths_favorites)
                if parsed:
                    favorites = parsed

        # 4. 自定义板块降级：Block.cfg 或 custom_block/ 目录
        if not custom_blocks:
            # 4a. 优先解析 hexin/Block.cfg
            block_cfg_path = self._get_file_path('ths', 'block_cfg')
            if block_cfg_path:
                parsed = self._read_file_cached(
                    block_cfg_path, self._get_encoding('ths', 'block_cfg'),
                    self._parse_ths_blocks)
                if parsed:
                    custom_blocks = parsed

            # 4b. 降级：扫描 custom_block/ 目录
            if not custom_blocks:
                block_dir = self._get_file_path('ths', 'custom_blocks_index')
                if block_dir:
                    dir_path = Path(block_dir)
                    if dir_path.is_dir():
                        try:
                            for entry in sorted(dir_path.iterdir()):
                                if not entry.is_file():
                                    continue
                                block_code = entry.name
                                custom_blocks.append({
                                    'block_code': block_code,
                                    'block_name': block_code,
                                    'members': [],
                                })
                        except OSError as e:
                            logger.warning("扫描 THS custom_block 目录失败 %s: %s",
                                           block_dir, e)

        return {'favorites': favorites, 'custom_blocks': custom_blocks}

    # ------------------------------------------------------------------
    # 板块成分股接口
    # ------------------------------------------------------------------

    def get_block_members(self, block_code) -> List[str]:
        """解析板块成分股，返回股票代码列表（如 ['600000.SH', '000001.SZ']）。

        依次尝试 TDX → DZH → THS 的自定义板块文件。
        若自定义板块文件未匹配，降级从系统板块（get_system_sectors）中按名称查找。
        block_code 可为板块文件名或板块名。
        """
        if not block_code:
            return []
        block_code = str(block_code)
        # 1. 依次尝试各客户端的自定义板块文件
        for client in _CLIENT_ORDER:
            if client not in self._homes:
                continue
            codes = self._get_block_members_for_client(client, block_code)
            if codes:
                return codes
        # 2. 降级：从系统板块中按名称查找（覆盖行业/概念/指数等系统板块）
        try:
            all_sectors = self.get_system_sectors()
            for source_key, categories in all_sectors.items():
                for cat, sec_list in categories.items():
                    for sec in sec_list:
                        sec_name = sec.get('name', '') or sec.get('code', '')
                        if sec_name == block_code:
                            members = sec.get('members', [])
                            if members:
                                return members
        except Exception:
            pass
        return []

    def _get_block_members_for_client(self, client: str, block_code: str) -> List[str]:
        """获取指定客户端的板块成分股（返回 XXXXXX.SH 格式列表）。"""
        members = self._get_block_members_raw(client, block_code)
        return [self._to_tq_code(m['code'], m['setcode']) for m in members]

    def _get_block_members_raw(self, client: str, block_code: str) -> List[Dict]:
        """获取指定客户端的板块成分股（返回原始成员字典列表）。"""
        if client == 'tdx':
            return self._get_block_members_tdx(block_code)
        if client == 'ths':
            return self._get_block_members_ths(block_code)
        # DZH 板块成分股文件格式未标准化，暂不支持按 block_code 解析
        return []

    def _get_block_members_ths(self, block_code: str) -> List[Dict]:
        """获取同花顺板块成分股。

        从 hexin/Block.cfg 中查找匹配 block_code 的板块成员。
        若 Block.cfg 不存在则返回空列表。
        """
        block_cfg_path = self._get_file_path('ths', 'block_cfg')
        if not block_cfg_path:
            return []
        blocks = self._read_file_cached(
            block_cfg_path, self._get_encoding('ths', 'block_cfg'),
            self._parse_ths_blocks)
        if not blocks:
            return []
        for blk in blocks:
            if block_code in (blk.get('block_code', ''), blk.get('block_name', '')):
                return blk.get('members', [])
        return []

    def _get_block_members_tdx(self, block_code: str) -> List[Dict]:
        """获取通达信板块成分股。

        先尝试直接以 block_code 作为文件名读取 .blk；
        若失败则解析 blocknew.cfg 查找匹配的板块（按文件名或板块名）。
        """
        # 1. 直接以 block_code 作为文件名
        blk_path = self._get_block_file_path('tdx', block_code)
        if blk_path:
            members = self._read_file_cached(
                blk_path, self._get_encoding('tdx', 'block_members_pattern'),
                self._parse_tdx_blk)
            if members:
                return members

        # 2. 解析 blocknew.cfg 查找匹配板块
        blocknew_path = self._get_file_path('tdx', 'custom_blocks_index')
        if blocknew_path:
            blocks = self._read_file_cached(
                blocknew_path, self._get_encoding('tdx', 'custom_blocks_index'),
                self._parse_tdx_blocknew)
            if blocks:
                for blk in blocks:
                    filename = blk.get('block_filename', '')
                    name = blk.get('block_name', '')
                    if block_code == filename or block_code == name:
                        target = filename or block_code
                        target_path = self._get_block_file_path('tdx', target)
                        if target_path:
                            members = self._read_file_cached(
                                target_path,
                                self._get_encoding('tdx', 'block_members_pattern'),
                                self._parse_tdx_blk)
                            if members:
                                return members
        return []

    # ------------------------------------------------------------------
    # 板块列表接口
    # ------------------------------------------------------------------

    def get_sector_list(self, list_type=1) -> List[Dict]:
        """返回板块列表。

        Args:
            list_type: 列表类型
                - 0：系统板块（来自 tdxbk.cfg，按类型分组返回全部系统板块）
                - 1（默认）：自定义板块（向后兼容，保持原有行为）

        Returns:
            [{'code': str, 'name': str, 'block_code': str, 'block_name': str,
              'member_count': int, 'type': str}, ...]
            其中 type 字段为系统板块类型编号（list_type=0 时有值）。
        """
        # list_type=0：返回系统板块（来自 tdxbk.cfg）
        try:
            lt_int = int(list_type)
        except (ValueError, TypeError):
            lt_int = 1
        if lt_int == 0:
            return self._get_system_sector_list()

        # list_type=1（默认）：返回自定义板块（向后兼容）
        for client in _CLIENT_ORDER:
            if client not in self._homes:
                continue
            sectors = self._get_sector_list_for_client(client)
            if sectors:
                return sectors
        return []

    def _get_system_sector_list(self) -> List[Dict]:
        """获取系统板块列表，统一为 get_sector_list 输出格式。

        系统板块无独立文件代码，以板块名作为 code/block_code。
        成员数暂不统计（系统板块成分股文件命名规则未标准化）。
        """
        blocks = self._get_tdx_system_blocks()
        if not blocks:
            return []
        result: List[Dict] = []
        for blk in blocks:
            block_name = blk.get('block_name', '')
            block_type = blk.get('type', '')
            result.append({
                'code': block_name,
                'name': block_name,
                'block_code': block_name,
                'block_name': block_name,
                'member_count': 0,
                'type': block_type,
            })
        return result

    def get_system_sectors(self) -> Dict[str, Dict[str, List[Dict]]]:
        """返回按数据源和分类分组的系统板块字典。

        仅返回**系统板块**（concept/industry/index/style/region），
        不包含板块指数、自定义板块、自选股。
        - 板块指数请用 `get_sector_index_list()`
        - 自定义板块请用 `get_custom_blocks_grouped()`
        - 自选股请用 `get_favorites_grouped()`

        Returns:
            {
                'tdx': {'concept': [...], 'industry': [...], 'index': [...],
                        'style': [...], 'region': [...]},
                'dzh': {'concept': [...], 'industry': [...], 'index': [...]},
                'ths': {'concept': [...], 'industry': [...], 'index': [...]},
            }
            每个板块元素含：
                - code: 板块代码/标识
                - name: 板块名称
                - source: 数据源（tdx_local/dzh_local/ths_local）
                - members: 成分股代码列表（如 ['SH600000', 'SZ000001', ...]）
                - member_count: 成分股数量
        """
        result: Dict[str, Dict[str, List[Dict]]] = {
            'tdx': {}, 'dzh': {}, 'ths': {},
        }

        # --- 通达信 ---
        result['tdx'] = self._get_tdx_system_sectors_grouped()

        # --- 大智慧 ---
        result['dzh'] = self._get_dzh_system_sectors_grouped()

        # --- 同花顺 ---
        result['ths'] = self._get_ths_system_sectors_grouped()

        return result

    # ------------------------------------------------------------------
    # 板块指数 / 自定义板块 / 自选股 独立查询接口
    # 三类数据各自独立方法 + 独立 API 端点，不混入 get_system_sectors
    # ------------------------------------------------------------------

    def get_sector_index_list(self, source: Optional[str] = None) -> Dict[str, List[Dict]]:
        """返回各软件的板块指数列表（按数据源分组）。

        板块指数代码（880xxx）是衡量板块走势的指数代码，与板块本身一一对应。
        本方法仅返回板块指数**定义**（code/name/type/sub_type），不含成分股。
        - TDX: tdxzs.cfg 板块指数定义
        - DZH: 从 ABK 的指数分类 section（申万指数、中证指数、沪市指数、深市指数等）提取
        - THS: 从 BlockUpdate 的 index 分类板块提取

        Args:
            source: 限定数据源（'tdx'/'dzh'/'ths'，None=返回全部数据源）

        Returns:
            {'tdx': [{'code': '880201', 'name': '农业种植',
                      'type': '4', 'sub_type': 'concept', ...}, ...],
             'dzh': [...], 'ths': [...]}
        """
        if source is not None and source not in ('tdx', 'dzh', 'ths'):
            return {}
        sources = [source] if source else ['tdx', 'dzh', 'ths']
        result: Dict[str, List[Dict]] = {s: [] for s in sources}
        src_name_map = {'tdx': 'tdx_local', 'dzh': 'dzh_local', 'ths': 'ths_local'}

        # TDX: tdxzs.cfg
        if 'tdx' in sources:
            tdx_indices = self._get_tdx_sector_indices()
            tdx_list: List[Dict] = []
            for idx in tdx_indices:
                idx_code = idx.get('code', '')
                idx_type = idx.get('type', '')
                tdx_list.append({
                    'code': idx_code,
                    'name': idx.get('name', ''),
                    'sector_index_code': idx_code,
                    'source': 'tdx_local',
                    'type': idx_type,
                    'sub_type': self._TDX_ZS_TYPE_MAP.get(idx_type, 'other'),
                    'description': f'TDX板块指数 {idx_code} {idx.get("name", "")}',
                    'member_count': 0,
                })
            result['tdx'] = tdx_list

        # DZH: 从 ABK 的指数分类 section 提取
        if 'dzh' in sources:
            dzh_list: List[Dict] = []
            abk_data = self._get_dzh_abk_data()
            if abk_data:
                index_sections = [
                    sec for sec, cat in self._DZH_ABK_SECTION_MAP.items()
                    if cat == 'index'
                ]
                seen_codes = set()
                for sec_name in index_sections:
                    blocks = abk_data.get(sec_name, {})
                    for block_id, block_info in blocks.items():
                        code = block_id
                        if code in seen_codes:
                            continue
                        seen_codes.add(code)
                        name = block_info.get('name', '')
                        member_count = len(block_info.get('codes', []))
                        dzh_list.append({
                            'code': code,
                            'name': name,
                            'sector_index_code': code,
                            'source': 'dzh_local',
                            'type': 'index',
                            'sub_type': sec_name,
                            'description': f'DZH板块指数 {sec_name} - {name}',
                            'member_count': member_count,
                        })
            result['dzh'] = dzh_list

        # THS: 从 BlockUpdate 的 index 分类提取
        if 'ths' in sources:
            ths_list: List[Dict] = []
            ths_index_sectors = self._get_ths_blockupdate_sectors('index')
            if ths_index_sectors:
                for sec in ths_index_sectors:
                    code = sec.get('code', '')
                    name = sec.get('name', '')
                    member_count = sec.get('member_count', 0)
                    source_file = sec.get('source_file', '')
                    ths_list.append({
                        'code': code,
                        'name': name,
                        'sector_index_code': code,
                        'source': 'ths_local',
                        'type': 'index',
                        'sub_type': source_file,
                        'description': f'THS板块指数 {name}',
                        'member_count': member_count,
                    })
            result['ths'] = ths_list

        return result

    def _fill_member_names(self, members: List[Dict]) -> List[Dict]:
        """为成分股列表填充名称（使用 get_stock_name_map）。

        Args:
            members: [{code, setcode, name?}, ...] 格式的成分股列表

        Returns:
            同样格式但 name 字段已填充的列表
        """
        if not members:
            return members
        try:
            name_map = self.get_stock_name_map()
        except Exception:
            return members
        if not name_map:
            return members
        result = []
        for m in members:
            if not isinstance(m, dict):
                result.append(m)
                continue
            code = m.get('code', '')
            setcode = m.get('setcode', 0)
            name = m.get('name', '')
            if not name and code:
                market_map = {0: 'SZ', 1: 'SH', 2: 'BJ'}
                market = market_map.get(setcode, 'SZ')
                full_code = f"{market}{code}"
                name = name_map.get(full_code, '')
                if not name:
                    alt_codes = [f"SH{code}", f"SZ{code}", f"BJ{code}"]
                    for ac in alt_codes:
                        if ac in name_map:
                            name = name_map[ac]
                            break
            new_m = dict(m)
            new_m['name'] = name
            result.append(new_m)
        return result

    def get_custom_blocks_grouped(self, source: Optional[str] = None) -> Dict[str, List[Dict]]:
        """返回各软件的自定义板块（按数据源分组，含成分股）。

        各数据源独立解析，互不混淆：
        - TDX: blocknew/*.blk（含成分股）
        - DZH: cfg/block.ini SysBlock + ABK"自定义板块"section 成分股
        - THS: hexin/Block.cfg 或 custom_block/ 目录

        Args:
            source: 限定数据源（'tdx'/'dzh'/'ths'，None=返回全部数据源）

        Returns:
            {'tdx': [{'code': 'blk文件名', 'name': '板块名',
                      'members': [{'stock_code': 'SH600000', 'name': '浦发银行'}, ...],
                      'member_count': N}, ...],
             'dzh': [...], 'ths': [...]}
        """
        if source is not None and source not in ('tdx', 'dzh', 'ths'):
            return {}
        sources = [source] if source else ['tdx', 'dzh', 'ths']
        result: Dict[str, List[Dict]] = {s: [] for s in sources}
        src_name_map = {'tdx': 'tdx_local', 'dzh': 'dzh_local', 'ths': 'ths_local'}

        name_map = {}
        try:
            name_map = self.get_stock_name_map()
        except Exception:
            pass

        def _lookup_name(code: str, setcode: int) -> str:
            if not name_map or not code:
                return ''
            market_map = {0: 'SZ', 1: 'SH', 2: 'BJ'}
            market = market_map.get(setcode, 'SZ')
            full_code = f"{market}{code}"
            if full_code in name_map:
                return name_map[full_code]
            for alt in (f"SH{code}", f"SZ{code}", f"BJ{code}"):
                if alt in name_map:
                    return name_map[alt]
            return ''

        for client in sources:
            if client not in self._homes:
                continue
            source_name = src_name_map[client]
            user_sector = self._get_user_sector_for_client(client)
            custom_blocks = user_sector.get('custom_blocks', []) or []
            if not custom_blocks:
                continue
            custom_list: List[Dict] = []
            for blk in custom_blocks:
                blk_code = blk.get('block_code', '') or blk.get('block_name', '')
                blk_name = blk.get('block_name', '') or blk_code
                raw_members = blk.get('members', []) or []
                member_list: List[Dict] = []
                for m in raw_members:
                    if isinstance(m, dict):
                        code = m.get('code', '')
                        if not code:
                            continue
                        setcode = m.get('setcode', 0)
                        mname = m.get('name', '') or _lookup_name(code, setcode)
                        tq_code = self._to_tq_code(code, setcode)
                        member_list.append({
                            'stock_code': tq_code,
                            'code': tq_code,
                            'name': mname,
                        })
                    elif isinstance(m, str) and len(m) >= 6:
                        info = self._code_prefix_map.get(m[0])
                        setcode = info['setcode'] if info else 0
                        mname = _lookup_name(m, setcode)
                        tq_code = self._to_tq_code(m, setcode)
                        member_list.append({
                            'stock_code': tq_code,
                            'code': tq_code,
                            'name': mname,
                        })
                custom_list.append({
                    'code': blk_code,
                    'name': blk_name,
                    'source': source_name,
                    'members': member_list,
                    'member_count': len(member_list),
                })
            result[client] = custom_list
        return result

    def get_favorites_grouped(self, source: Optional[str] = None) -> Dict[str, List[Dict]]:
        """返回各软件的自选股（按数据源分组，含完整成员列表）。

        各数据源独立解析：
        - TDX: zxg.blk
        - DZH: cfg/zxg.cfg
        - THS: hexin/ZXG.cfg

        Args:
            source: 限定数据源（'tdx'/'dzh'/'ths'，None=返回全部数据源）

        Returns:
            {'tdx': [{'code': 'tdx_favorites', 'name': 'TDX自选股',
                      'members': [{'stock_code': 'SH600000', 'name': '浦发银行'}, ...],
                      'member_count': N}],
             'dzh': [...], 'ths': [...]}
            每个数据源仅返回一条记录（该客户端的全部自选股）。
        """
        if source is not None and source not in ('tdx', 'dzh', 'ths'):
            return {}
        sources = [source] if source else ['tdx', 'dzh', 'ths']
        result: Dict[str, List[Dict]] = {s: [] for s in sources}
        src_name_map = {'tdx': 'tdx_local', 'dzh': 'dzh_local', 'ths': 'ths_local'}

        name_map = {}
        try:
            name_map = self.get_stock_name_map()
        except Exception:
            pass

        def _lookup_name(code: str, setcode: int) -> str:
            if not name_map or not code:
                return ''
            market_map = {0: 'SZ', 1: 'SH', 2: 'BJ'}
            market = market_map.get(setcode, 'SZ')
            full_code = f"{market}{code}"
            if full_code in name_map:
                return name_map[full_code]
            for alt in (f"SH{code}", f"SZ{code}", f"BJ{code}"):
                if alt in name_map:
                    return name_map[alt]
            return ''

        for client in sources:
            if client not in self._homes:
                continue
            source_name = src_name_map[client]
            user_sector = self._get_user_sector_for_client(client)
            favorites = user_sector.get('favorites', []) or []
            if not favorites:
                continue
            member_list: List[Dict] = []
            for fav in favorites:
                if isinstance(fav, dict):
                    code = fav.get('code', '')
                    if not code:
                        continue
                    setcode = fav.get('setcode', 0)
                    mname = fav.get('name', '') or _lookup_name(code, setcode)
                    tq_code = self._to_tq_code(code, setcode)
                    member_list.append({
                        'stock_code': tq_code,
                        'code': tq_code,
                        'name': mname,
                    })
                elif isinstance(fav, str) and len(fav) >= 6:
                    info = self._code_prefix_map.get(fav[0])
                    setcode = info['setcode'] if info else 0
                    mname = _lookup_name(fav, setcode)
                    tq_code = self._to_tq_code(fav, setcode)
                    member_list.append({
                        'stock_code': tq_code,
                        'code': tq_code,
                        'name': mname,
                    })
            result[client] = [{
                'code': f'{client}_favorites',
                'name': f'{client.upper()}自选股',
                'source': source_name,
                'members': member_list,
                'member_count': len(member_list),
            }]
        return result

    def get_system_sectors_flat(self) -> Dict[str, List[Dict]]:
        """向后兼容：返回扁平分类的系统板块字典（合并所有数据源）。

        返回格式与旧版 get_system_sectors() 一致：
            {'concept': [...], 'industry': [...], 'index': [...], ...}
        """
        grouped_data = self.get_system_sectors()
        flat: Dict[str, List[Dict]] = {}
        for source_key, categories in grouped_data.items():
            source_name = {
                'tdx': 'tdx_local', 'dzh': 'dzh_local', 'ths': 'ths_local'
            }.get(source_key, source_key)
            for cat, sectors in categories.items():
                flat.setdefault(cat, []).extend(sectors)
        return flat

    # ------------------------------------------------------------------
    # 板块 ↔ 板块指数代码 ↔ 个股 双向映射求解
    # ------------------------------------------------------------------

    def get_members_by_sector_index_code(self, index_code: str,
                                         source: Optional[str] = None) -> Optional[Dict]:
        """由板块指数代码输出板块成分股。

        板块指数代码（如 880201）是衡量板块走势的指数代码，与板块名一一对应。
        本方法建立"板块指数代码 → 成分股"的正确求解关系：
        - 概念/风格/指数: 直接从 infoharbor_block.dat 头行第4字段匹配
        - 行业: 通过 tdxzs.cfg type=2 的 desc(Txxxxxx) → tdxhy.cfg 成分股
        - 地区: 通过 tdxzs.cfg type=3 的 desc(序列号) → base.dbf DY字段成分股

        Args:
            index_code: 板块指数代码（如 '880201', '880302', '880515'）
            source: 限定数据源（'tdx'/'dzh'/'ths'，None=自动搜索所有数据源）

        Returns:
            {'sector_index_code': str, 'sector_name': str, 'category': str,
             'source': str, 'members': List[str], 'member_count': int}
            未找到返回 None。
        """
        if not index_code:
            return None
        index_code = str(index_code).strip()
        # 严格数据源过滤：None=搜索所有，无效值=返回 None
        if source is not None:
            if source not in ('tdx', 'dzh', 'ths'):
                return None
            sources = [source]
        else:
            sources = ['tdx', 'dzh', 'ths']
        grouped_all = self.get_system_sectors()
        for src in sources:
            categories = grouped_all.get(src, {})
            src_name = {'tdx': 'tdx_local', 'dzh': 'dzh_local', 'ths': 'ths_local'}.get(src, src)
            for cat, sec_list in categories.items():
                # 跳过 sector_index 分类（仅含板块指数定义，无真实成分股）
                # 该分类用于前端展示完整板块指数列表，不能用于成分股求解
                if cat == 'sector_index':
                    continue
                for sec in sec_list:
                    # 优先匹配 sector_index_code，兼容匹配 code/sector_code
                    if (sec.get('sector_index_code', '') == index_code
                            or sec.get('code', '') == index_code
                            or sec.get('sector_code', '') == index_code):
                        members = sec.get('members', [])
                        return {
                            'sector_index_code': sec.get('sector_index_code', index_code),
                            'sector_code': sec.get('sector_code', index_code),
                            'sector_name': sec.get('name', ''),
                            'category': cat,
                            'source': src_name,
                            'members': list(members),
                            'member_count': len(members),
                        }
        return None

    def get_sectors_by_stock(self, stock_code: str,
                             source: Optional[str] = None) -> List[Dict]:
        """由个股代码反查所属所有板块（板块↔个股反向映射）。

        遍历所有板块的成分股，返回包含该个股的板块列表。
        支持多数据源独立查询，不跨数据源匹配。

        Args:
            stock_code: 股票代码（如 'SH600000', 'SZ000001'），自动归一化
            source: 限定数据源（'tdx'/'dzh'/'ths'，None=搜索所有数据源）

        Returns:
            [{'sector_index_code': str, 'sector_name': str, 'category': str,
              'source': str, 'member_count': int}, ...]
            按数据源分组，每个数据源独立返回。
        """
        if not stock_code:
            return []
        target = self._to_market_prefixed_code(stock_code)
        if not target:
            return []
        # 严格数据源过滤：None=搜索所有，无效值=返回空列表
        if source is not None:
            if source not in ('tdx', 'dzh', 'ths'):
                return []
            sources = [source]
        else:
            sources = ['tdx', 'dzh', 'ths']
        grouped_all = self.get_system_sectors()
        results: List[Dict] = []
        for src in sources:
            categories = grouped_all.get(src, {})
            src_name = {'tdx': 'tdx_local', 'dzh': 'dzh_local', 'ths': 'ths_local'}.get(src, src)
            for cat, sec_list in categories.items():
                # 跳过 sector_index 分类（无真实成分股，无法反查个股）
                if cat == 'sector_index':
                    continue
                for sec in sec_list:
                    members = sec.get('members', [])
                    if not members:
                        continue
                    # 成分股匹配（归一化后比较，兼容 SH/SZ 前缀大小写）
                    if any(self._to_market_prefixed_code(m) == target for m in members):
                        results.append({
                            'sector_index_code': sec.get('sector_index_code', ''),
                            'sector_code': sec.get('sector_code', sec.get('code', '')),
                            'sector_name': sec.get('name', ''),
                            'category': cat,
                            'source': src_name,
                            'member_count': len(members),
                        })
        return results

    @staticmethod
    def _to_market_prefixed_code(code: str) -> str:
        """转成「市场前缀+数字」归一形式用于成分股匹配。

        I38 改名：与 core/domain/tick_source._normalize_stock_code（STRIP 前后缀→纯数字）语义相反，
        此处是 ADD 前缀→「SH600000」匹配键，故名 _to_market_prefixed_code。
        'sh600000' / 'SH600000' / '600000.SH' → 'SH600000'
        'sz000001' / 'SZ000001' / '000001.SZ' → 'SZ000001'
        """
        if not code:
            return ''
        s = str(code).strip().upper()
        # 处理 XXXXXX.XX 格式
        if '.' in s:
            parts = s.split('.')
            if len(parts) == 2:
                code_part, market_part = parts
                if market_part in ('SH', 'SZ', 'BJ'):
                    return f"{market_part}{code_part}"
                if code_part in ('SH', 'SZ', 'BJ'):
                    return f"{code_part}{market_part}"
        # 纯数字 → 按前缀推断市场
        if s.isdigit() and len(s) == 6:
            first = s[0]
            if first == '6':
                return f"SH{s}"
            if first in ('0', '3'):
                return f"SZ{s}"
            if first in ('4', '8'):
                return f"BJ{s}"
        # 已带市场前缀
        if s.startswith(('SH', 'SZ', 'BJ')) and len(s) == 8:
            return s
        return s

    def _get_tdx_system_sectors_grouped(self) -> Dict[str, List[Dict]]:
        """获取通达信系统板块（按分类分组，含完整成分股）。

        各分类成分股数据源（各自独立解析，不跨数据源匹配，使用程序实时数据）：
        - 概念板块 (concept): infoharbor_block.dat 的 #GN_ 前缀板块（含成分股）
        - 风格板块 (style): infoharbor_block.dat 的 #FG_ 前缀板块（含成分股）
        - 指数板块 (index): infoharbor_block.dat 的 #ZS_ 前缀板块（含成分股）
        - 行业板块 (industry): tdxhy.cfg 的 Txxxxxx 行业代码聚合（含成分股）
        - 地区板块 (region): base.dbf 的 DY 字段 + tdxzs.cfg type=3 地区名称映射

        板块 vs 板块指数代码（关键区分）：
        - 板块 = 一组股票的集合（如"农业种植"概念板块）
        - 板块指数代码 = 衡量板块走势的指数代码（如 880201），来自 tdxzs.cfg
        - 概念/风格/指数板块: sector_index_code = infoharbor 头行第4字段（880xxx 或空）
        - 行业板块: sector_index_code = tdxzs.cfg type=2 的 code（880xxx），industry_code = Txxxxxx
        - 地区板块: sector_index_code = tdxzs.cfg type=3 的 code（880xxx），region_seq = 序列号(1-32)

        降级策略（实时文件缺失时）：
        - tdxbk.cfg: 仅板块名，无成分股
        - tdxzs.cfg: 板块指数定义，无成分股
        """
        grouped: Dict[str, List[Dict]] = {}

        # 1. infoharbor_block.dat：概念/风格/指数板块（含完整成分股，实时数据）
        # 头行第4字段 = 板块指数代码（880xxx），与 tdxzs.cfg type=4/5 的 code 对应
        harbor_blocks = self._get_tdx_infoharbor_blocks()
        for cat, sec_list in harbor_blocks.items():
            for sec in sec_list:
                members = sec.get('members', [])
                idx_code = sec.get('code', '')
                grouped.setdefault(cat, []).append({
                    'code': idx_code,
                    'name': sec.get('name', ''),
                    'sector_index_code': idx_code,  # 板块指数代码（880xxx，可能为空）
                    'sector_code': idx_code,         # 板块代码（=板块指数代码）
                    'source': 'tdx_local',
                    'type': cat,
                    'category': cat,
                    'description': f'TDX{cat}板块 {idx_code}',
                    'members': members,
                    'member_count': len(members),
                })

        # 2. 行业板块：tdxhy.cfg 的 Txxxxxx 行业代码聚合（含成分股）
        # tdxzs.cfg type=2: code=板块指数代码(880xxx), name=中文名, desc=Txxxxxx行业代码
        # 建立 Txxxxxx → (板块指数代码, 中文名) 映射（desc 字段作 key）
        industry_idx_map = {
            idx.get('desc', '').strip(): {
                'index_code': idx.get('code', ''),
                'name': idx.get('name', ''),
            }
            for idx in self._get_tdx_sector_indices()
            if idx.get('type', '') == '2' and idx.get('desc', '').strip()
        }
        industry_members = self._get_tdx_industry_members()
        for ind_code, members in industry_members.items():
            idx_info = industry_idx_map.get(ind_code, {})
            idx_code = idx_info.get('index_code', '')
            ind_name = idx_info.get('name', ind_code)
            grouped.setdefault('industry', []).append({
                'code': ind_code,                # 向后兼容: 保持 Txxxxxx
                'name': ind_name,
                'sector_index_code': idx_code,   # 板块指数代码（880xxx）
                'sector_code': ind_code,         # 行业代码（Txxxxxx）
                'industry_code': ind_code,       # 行业代码（Txxxxxx，明确字段）
                'source': 'tdx_local',
                'type': 'industry',
                'category': 'industry',
                'description': f'TDX行业板块 {ind_code} (指数={idx_code})',
                'members': members,
                'member_count': len(members),
            })

        # 3. 地区板块：base.dbf 的 DY 字段 + tdxzs.cfg type=3 地区名称映射
        # tdxzs.cfg type=3: code=板块指数代码(880xxx), name=地区名, desc=地区序列号(1-32)
        # DY 代码 = tdxzs.cfg type=3 的序列号（desc 字段）
        region_indices = [
            idx for idx in self._get_tdx_sector_indices()
            if idx.get('type', '') == '3' and idx.get('category', '') == '1'
        ]
        region_members_map = self._get_tdx_base_dbf_region_members()
        if region_indices and region_members_map:
            # 构建序列号 → 地区信息映射
            for idx in region_indices:
                seq = idx.get('desc', '').strip()
                if not seq:
                    continue
                members = region_members_map.get(seq, [])
                idx_code = idx.get('code', '')
                grouped.setdefault('region', []).append({
                    'code': idx_code,                # 板块指数代码（880xxx）
                    'name': idx.get('name', ''),
                    'sector_index_code': idx_code,   # 板块指数代码（880xxx）
                    'sector_code': idx_code,         # 板块代码
                    'region_seq': seq,               # 地区序列号（1-32，用于关联 base.dbf）
                    'source': 'tdx_local',
                    'type': 'region',
                    'category': 'region',
                    'description': f'TDX地区板块 {idx_code} (DY={seq})',
                    'members': members,
                    'member_count': len(members),
                })

        # 4. 降级路径：当所有实时文件都缺失时，使用 tdxbk.cfg + tdxzs.cfg（无成分股）
        if not grouped:
            logger.warning("TDX 实时数据文件缺失，降级到 tdxbk.cfg + tdxzs.cfg（无成分股）")
            blocks = self._get_tdx_system_blocks()
            for blk in blocks:
                type_name = blk.get('type_name', 'other')
                block_name = blk.get('block_name', '')
                grouped.setdefault(type_name, []).append({
                    'code': block_name,
                    'name': block_name,
                    'source': 'tdx_local',
                    'type': blk.get('type', ''),
                    'description': blk.get('block_desc', ''),
                    'members': [],
                    'member_count': 0,
                })
            for idx in self._get_tdx_sector_indices():
                idx_type = idx.get('type', '')
                idx_category = idx.get('category', '')
                if idx_type == '3' and idx_category == '1':
                    cat_name = 'region'
                elif idx_type == '5' and idx_category == '2':
                    cat_name = 'style'
                else:
                    cat_name = 'index'
                grouped.setdefault(cat_name, []).append({
                    'code': idx.get('code', ''),
                    'name': idx.get('name', ''),
                    'source': 'tdx_local',
                    'type': idx_type,
                    'description': idx.get('desc', ''),
                    'members': [],
                    'member_count': 0,
                })

        return grouped

    def _get_dzh_system_sectors_grouped(self) -> Dict[str, List[Dict]]:
        """获取大智慧系统板块（按分类分组，含成分股）。

        数据来源：
        - full.ABK: 完整板块数据（含名称和成分股），优先
        - classtree XML: 分类树结构（降级，仅板块名无成分股）
        """
        grouped: Dict[str, List[Dict]] = {}

        # 1. 优先从 full.ABK 获取（含成分股）
        abk_categories = ['concept', 'industry', 'index', 'region']
        for cat in abk_categories:
            sectors = self._get_dzh_abk_sectors_by_category(cat)
            if sectors:
                for sec in sectors:
                    sec['source'] = 'dzh_local'
                    grouped.setdefault(cat, []).append(sec)

        # 2. 降级：从 classtree XML 获取（仅板块名，无成分股）
        # 仅当 ABK 无数据时使用
        if not grouped:
            xml_categories = ['concept', 'industry', 'index']
            for cat in xml_categories:
                classtree = self._get_dzh_classtree(cat)
                if not classtree:
                    continue
                for cls in classtree:
                    class_name = cls.get('class_name', '')
                    for sub in cls.get('subclasses', []):
                        node_id = sub.get('node', '')
                        sub_name = sub.get('name', '')
                        # 尝试从 ABK 获取成分股
                        members = self._get_dzh_abk_members(node_id) if node_id else []
                        grouped.setdefault(cat, []).append({
                            'code': node_id,
                            'name': sub_name,
                            'source': 'dzh_local',
                            'members': members,
                            'member_count': len(members),
                            'class_name': class_name,
                        })

        return grouped

    def _get_ths_system_sectors_grouped(self) -> Dict[str, List[Dict]]:
        """获取同花顺系统板块（按分类分组，含成分股）。

        数据来源：BlockUpdate/block_*.ini（同时含板块名称和成分股）
        """
        grouped: Dict[str, List[Dict]] = {}

        # 获取所有分类的板块
        sectors = self._get_ths_blockupdate_sectors()
        for sec in sectors:
            cat = sec.get('category', 'other')
            sec['source'] = 'ths_local'
            grouped.setdefault(cat, []).append(sec)

        return grouped

    def _get_sector_list_for_client(self, client: str) -> List[Dict]:
        """获取指定客户端的自定义板块列表。"""
        if client == 'tdx':
            return self._get_sector_list_tdx()
        if client == 'dzh':
            return self._get_sector_list_dzh()
        if client == 'ths':
            return self._get_sector_list_ths()
        return []

    def _get_sector_list_tdx(self) -> List[Dict]:
        """获取通达信自定义板块列表。"""
        blocks = self._scan_tdx_blocknew_dir()
        if not blocks:
            return []
        result: List[Dict] = []
        for blk in blocks:
            block_code = blk.get('block_code', '')
            block_name = blk.get('block_name', '')
            # 统计成员数（读取 .blk 文件）
            member_count = 0
            if block_code:
                blk_path = self._get_block_file_path('tdx', block_code)
                if blk_path:
                    members = self._read_file_cached(
                        blk_path, self._get_encoding('tdx', 'block_members_pattern'),
                        self._parse_tdx_blk)
                    member_count = len(members) if members else 0
            result.append({
                'code': block_code,
                'name': block_name,
                'block_code': block_code,
                'block_name': block_name,
                'member_count': member_count,
            })
        return result

    def _get_sector_list_dzh(self) -> List[Dict]:
        """获取大智慧自定义板块列表（含成分股数量）。

        优先扫描 USERDATA/block/ 目录，降级到 block.ini + ABK。
        """
        # 优先：USERDATA/block/ 目录
        blocks = self._scan_dzh_userdata_block_dir()
        if blocks:
            userdata_block_dir = self._get_file_path('dzh', 'userdata_block_dir')
            result: List[Dict] = []
            for blk in blocks:
                block_name = blk.get('block_name', '')
                block_filename = blk.get('block_filename', '')
                member_count = 0
                if userdata_block_dir and block_filename:
                    blk_path = Path(userdata_block_dir) / block_filename
                    if blk_path.is_file():
                        members = self._read_binary_cached(
                            str(blk_path), self._parse_dzh_blk_binary)
                        member_count = len(members) if members else 0
                result.append({
                    'code': block_name,
                    'name': block_name,
                    'block_code': block_name,
                    'block_name': block_name,
                    'member_count': member_count,
                })
            return result

        # 降级：cfg/block.ini + ABK
        block_ini_path = self._get_file_path('dzh', 'custom_blocks_index')
        if not block_ini_path:
            return []
        block_names = self._read_file_cached(
            block_ini_path, self._get_encoding('dzh', 'custom_blocks_index'),
            self._parse_dzh_block_ini)
        if not block_names:
            return []
        abk_custom_map: Dict[str, List[str]] = {}
        try:
            abk_data = self._get_dzh_abk_data()
            if abk_data:
                for sec_name, blks in abk_data.items():
                    if '自定义' not in sec_name:
                        continue
                    for _blk_id, blk_info in blks.items():
                        name_abk = blk_info.get('name', '')
                        if name_abk:
                            abk_custom_map[name_abk] = blk_info.get('codes', [])
        except Exception as e:
            logger.debug("DZH _get_sector_list_dzh ABK 映射构建失败: %s", e)
        result: List[Dict] = []
        for name in block_names:
            members = abk_custom_map.get(name, [])
            result.append({
                'code': name,
                'name': name,
                'block_code': name,
                'block_name': name,
                'member_count': len(members),
            })
        return result

    def _get_sector_list_ths(self) -> List[Dict]:
        """获取同花顺自定义板块列表。

        优先从 BlockUpdate 的 custom 分类获取（block_22.ini），
        降级到 hexin/Block.cfg 或 custom_block/ 目录。
        """
        # 1. 优先从 BlockUpdate 的 custom 分类获取
        custom_sectors = self._get_ths_blockupdate_sectors('custom')
        if custom_sectors:
            return [{
                'code': sec.get('code', ''),
                'name': sec.get('name', ''),
                'block_code': sec.get('code', ''),
                'block_name': sec.get('name', ''),
                'member_count': sec.get('member_count', 0),
            } for sec in custom_sectors]

        # 2. 降级：hexin/Block.cfg
        block_cfg_path = self._get_file_path('ths', 'block_cfg')
        if block_cfg_path:
            blocks = self._read_file_cached(
                block_cfg_path, self._get_encoding('ths', 'block_cfg'),
                self._parse_ths_blocks)
            if blocks:
                return [{
                    'code': blk.get('block_code', ''),
                    'name': blk.get('block_name', ''),
                    'block_code': blk.get('block_code', ''),
                    'block_name': blk.get('block_name', ''),
                    'member_count': len(blk.get('members', [])),
                } for blk in blocks]

        # 3. 降级：扫描 custom_block/ 目录
        result: List[Dict] = []
        block_dir = self._get_file_path('ths', 'custom_blocks_index')
        if block_dir:
            dir_path = Path(block_dir)
            if dir_path.is_dir():
                try:
                    for entry in sorted(dir_path.iterdir()):
                        if not entry.is_file():
                            continue
                        block_code = entry.name
                        result.append({
                            'code': block_code,
                            'name': block_code,
                            'block_code': block_code,
                            'block_name': block_code,
                            'member_count': 0,
                        })
                except OSError as e:
                    logger.warning("扫描 THS custom_block 目录失败 %s: %s", block_dir, e)
        return result

    # ------------------------------------------------------------------
    # 按类型获取股票列表
    # ------------------------------------------------------------------

    def get_stock_list_by_type(self, list_type, customblockname='', **kwargs) -> List[Dict]:
        """按类型获取股票列表。

        本地文件仅包含自选股与自定义板块，支持：
        - list_type=4（自定义板块）：返回 customblockname 对应板块成员
        - list_type=0/2 或其他：返回自选股（favorites）

        Returns:
            [{'setcode': int, 'code': str, 'name': str}, ...]
        """
        # 判断是否为 spinfo.type 整数值
        spinfo_type = None
        try:
            lt_int = int(list_type)
            if lt_int in (0, 2, 4):
                spinfo_type = lt_int
        except (ValueError, TypeError):
            pass

        # 自定义板块
        if spinfo_type == 4:
            if customblockname:
                for client in _CLIENT_ORDER:
                    if client not in self._homes:
                        continue
                    members = self._get_block_members_raw(client, str(customblockname))
                    if members:
                        return [{'setcode': m['setcode'], 'code': m['code'],
                                 'name': m.get('name', '')} for m in members]
            return []

        # 其他类型：返回自选股
        user_sector = self.get_user_sector()
        return list(user_sector.get('favorites', []))


# ===========================================================================
# TQ 数据源提供者集合（合并自 tq.py）
# 包含 TqConnector / TqDllProvider / TqSdkBridge / TqSdkProvider / TqProvider
# 注意：MARKET_ID_MAP / SHORT_NAME_TO_MARKET_ID / DZH_TO_SHORT / SHORT_TO_DZH
#       _PERIOD_INT_TO_STR / _PERIOD_STR_TO_INT / DZH_COL_MAP / _resolve_market_id
#       已在上方共享常量区定义，此处不再重复。
# ===========================================================================


# TQ DLL 路径（调整后：原 parents[3] -> parents[2]，因合并后少一层目录）
DLL_PATH = Path(__file__).resolve().parents[2] / 'TPythClient.dll'

# ---------------------------------------------------------------------------
# list_type 参数完整映射（参考 tdxdata_test.py 第209-217行）
# 对应 tq.get_stock_list() 的 list_type 参数，共53种
# ---------------------------------------------------------------------------

LIST_TYPE_MAP: Dict[int, Dict[str, Any]] = {
    0:  {'name': '自选股',           'desc': '用户自选股列表'},
    1:  {'name': '持仓股',           'desc': '用户持仓股列表'},
    5:  {'name': '所有A股',          'desc': '全部A股（含沪深京）'},
    6:  {'name': '上证指数成份股',   'desc': '上证指数成份股'},
    7:  {'name': '上证主板',         'desc': '上海证券交易所主板'},
    8:  {'name': '深证主板',         'desc': '深圳证券交易所主板'},
    9:  {'name': '重点指数',         'desc': '重点指数成份股'},
    10: {'name': '所有板块指数',     'desc': '全部板块指数'},
    11: {'name': '缺省行业板块',     'desc': '通达信默认行业板块'},
    12: {'name': '概念板块',         'desc': '概念板块'},
    13: {'name': '风格板块',         'desc': '风格板块'},
    14: {'name': '地区板块',         'desc': '地域板块'},
    15: {'name': '行业+概念板块',   'desc': '缺省行业分类+概念板块'},
    16: {'name': '研究行业一级',     'desc': '研究行业一级分类'},
    17: {'name': '研究行业二级',     'desc': '研究行业二级分类'},
    18: {'name': '研究行业三级',     'desc': '研究行业三级分类'},
    21: {'name': '含H股',            'desc': '含H股的A股'},
    22: {'name': '含可转债',         'desc': '含可转债标的'},
    23: {'name': '沪深300',          'desc': '沪深300指数成份股'},
    24: {'name': '中证500',          'desc': '中证500指数成份股'},
    25: {'name': '中证1000',         'desc': '中证1000指数成份股'},
    26: {'name': '国证2000',         'desc': '国证2000指数成份股'},
    27: {'name': '中证2000',         'desc': '中证2000指数成份股'},
    28: {'name': '中证A500',         'desc': '中证A500指数成份股'},
    30: {'name': 'REITs',            'desc': '不动产投资信托基金'},
    31: {'name': 'ETF基金',          'desc': '交易型开放式指数基金'},
    32: {'name': '可转债',           'desc': '可转换公司债券'},
    33: {'name': 'LOF基金',          'desc': '上市型开放式基金'},
    34: {'name': '所有可交易基金',   'desc': '全部可交易基金'},
    35: {'name': '所有沪深基金',     'desc': '沪深交易所基金'},
    36: {'name': 'T+0基金',          'desc': 'T+0交易基金'},
    49: {'name': '金融类企业',       'desc': '金融行业企业'},
    50: {'name': '沪深A股',          'desc': '沪深交易所A股'},
    51: {'name': '创业板',           'desc': '创业板股票'},
    52: {'name': '科创板',           'desc': '科创板股票'},
    53: {'name': '北交所',           'desc': '北京证券交易所'},
    91: {'name': 'ETF追踪的指数',    'desc': 'ETF所跟踪的指数'},
    92: {'name': '国内期货主力合约', 'desc': '国内期货主力合约'},
    101: {'name': '国内期货',         'desc': '国内期货品种'},
    102: {'name': '港股',             'desc': '港股通标的'},
    103: {'name': '美股',             'desc': '美股标的'},
}

# 常用市场代码 → market_id 映射（用于 get_stock_list 按类型查询）
_LIST_TYPE_TO_MARKET_IDS: Dict[int, List[int]] = {
    5:  [0, 1, 2],              # 所有A股：SZ + SH + BJ
    6:  [1],                    # 上证指数
    7:  [1],                    # 上证主板
    8:  [0],                    # 深证主板
    50: [0, 1],                 # 沪深A股：SZ + SH
    51: [0],                    # 创业板（SZ）
    52: [1],                    # 科创板（SH）
    53: [2],                    # 北交所（BJ）
}

# list_type → 板块分类映射
_LIST_TYPE_CATEGORY_MAP: Dict[int, str] = {
    11: 'industry',
    12: 'concept',
    13: 'style',
    14: 'region',
}

# 板块代码前缀 → 分类推断规则
_SECTOR_CODE_PREFIX_CATEGORY: Dict[str, str] = {
    '88': 'industry',     # 880xxx 行业板块
    '99': 'index',        # 99xxxx 指数类
}

_SECTOR_NAME_KEYWORD_CATEGORY: Dict[str, str] = {
    '概念': 'concept',
    '地域': 'region',
    '地区': 'region',
    '风格': 'style',
}



def _process_formula_arg(formula_arg):
    """将 formula_arg 字符串如 '12,26,9' 转换为数值列表 [12.0, 26.0, 9.0]"""
    if isinstance(formula_arg, list):
        return formula_arg
    if isinstance(formula_arg, str) and formula_arg:
        try:
            return [float(x.strip()) for x in formula_arg.split(',') if x.strip()]
        except (ValueError, TypeError):
            return []
    return []


# ===========================================================================
# TqConnector —— DLL 桥接层（从 tq_adapter.py 提取，完整保留）
# ===========================================================================

class TqConnector:
    """通过 TPythClient.dll 与通达信内核通信的桥接层。"""

    def __init__(self):
        self._dll = None
        self._run_id = -1
        self._ready = False
        self._cache: Dict[str, Any] = {}
        self._init_dll()

    def _init_dll(self):
        try:
            dll_path_str = str(DLL_PATH)
            self._dll = ctypes.CDLL(dll_path_str)
            self._setup_restypes()
            self._init_connect()
        except Exception:
            self._dll = None

    def _setup_restypes(self):
        dll = self._dll
        dll.InitConnect.restype = ctypes.c_char_p
        dll.GetStockListInStr.restype = ctypes.c_char_p
        dll.GetHISDATsInStr.restype = ctypes.c_char_p
        dll.GetCWDATAInStr.restype = ctypes.c_char_p
        dll.GetBlockListInStr.restype = ctypes.c_char_p
        dll.GetBlockStocksInStr.restype = ctypes.c_char_p
        dll.TdxFuncMain.restype = ctypes.c_char_p
        dll.GetGPBlockInStr.restype = ctypes.c_char_p
        dll.GetSTOCKInStr.restype = ctypes.c_char_p

    def _init_connect(self):
        try:
            major = str(sys.version_info.major)
            minor = str(sys.version_info.minor)
            version = int(major + minor)
            # 第一个参数是通达信客户端连接路径（与 tqcenter.py 一致）
            connection_path = str(DLL_PATH.parent).encode('utf-8')
            dll_path_bytes = str(DLL_PATH).encode('utf-8')
            ptr = self._dll.InitConnect(connection_path, dll_path_bytes, 0, version, False)
            if ptr and len(ptr) > 0:
                data = json.loads(ptr.decode('utf-8'))
                eid = data.get('ErrorId', '-1')
                if eid in ('0', '12'):
                    self._run_id = int(data.get('run_id', '-1'))
                    if self._run_id >= 0:
                        self._ready = True
                else:
                    logger.warning("TqConnector InitConnect 失败: %s", data.get('Error', ''))
        except Exception as e:
            logger.warning("TqConnector InitConnect 异常: %s", e)
            self._ready = False

    def _call(self, func, *args, timeout_ms: int = 10000) -> Optional[Dict]:
        if not self._ready or self._dll is None:
            return None
        try:
            ptr = func(self._run_id, *args, timeout_ms)
            if not ptr or len(ptr) == 0:
                return None
            result_str = ptr.decode('utf-8')
            data = json.loads(result_str)
            eid = data.get('ErrorId', '-1')
            if eid not in ('0', '19'):
                return None
            return data
        except Exception:
            return None

    def get_stock_list(self, market_id: int) -> List[str]:
        cache_key = f'stock_list_{market_id}'
        if cache_key in self._cache:
            return self._cache[cache_key]
        result = self._call(self._dll.GetStockListInStr, str(market_id).encode('utf-8'), 0)
        if result is None or result.get('Value') is None:
            return []
        self._cache[cache_key] = result['Value']
        return result['Value']

    def get_kline_data(self, codes: List[str], period: int, count: int) -> Dict:
        if not codes:
            return {}
        all_data = {}
        for code in codes:
            cache_key = f'kline_{code}_{period}_{count}'
            if cache_key in self._cache:
                all_data[code] = self._cache[cache_key]
                continue
            try:
                code_bytes = code.encode('utf-8')
                period_str = _PERIOD_INT_TO_STR.get(period, '1d')
                ptr = self._dll.GetHISDATsInStr(
                    self._run_id, code_bytes, b'', b'',
                    period_str.encode('utf-8'), 0, count, 10000
                )
                if ptr and len(ptr) > 0:
                    data = json.loads(ptr.decode('utf-8'))
                    if data.get('ErrorId') == '0':
                        self._cache[cache_key] = data
                        all_data[code] = data
            except Exception:
                pass
        return all_data

    def get_snapshot(self, codes: List[str]) -> Dict:
        if not codes:
            return {}
        all_data = {}
        for code in codes:
            cache_key = f'snapshot_{code}'
            if cache_key in self._cache:
                all_data[code] = self._cache[cache_key]
                continue
            result = self._call(self._dll.GetCWDATAInStr, code.encode('utf-8'), b'', b'')
            if result is not None:
                self._cache[cache_key] = result
                all_data[code] = result
        return all_data

    def get_block_list(self) -> List[str]:
        cache_key = 'block_list'
        if cache_key in self._cache:
            return self._cache[cache_key]
        result = self._call(self._dll.GetBlockListInStr, 0)
        if result is None or result.get('Value') is None:
            return []
        self._cache[cache_key] = result['Value']
        return result['Value']

    def get_block_members(self, block_code: str) -> List[str]:
        cache_key = f'block_members_{block_code}'
        if cache_key in self._cache:
            return self._cache[cache_key]
        result = self._call(self._dll.GetBlockStocksInStr, block_code.encode('utf-8'), 0)
        if result is None or result.get('Value') is None:
            return []
        self._cache[cache_key] = result['Value']
        return result['Value']

    def eval_formula(self, formula_text: str, codes: List[str], period: int) -> Dict:
        if not codes:
            return {}
        sp = _PERIOD_INT_TO_STR.get(period, '1d')
        try:
            code_json = {
                'id': self._run_id,
                'type': 4,
                'formula_name': formula_text,
                'formula_arg': [],
                'formula_type': 0,
                'xsflag': -1,
                'return_count': 1,
                'return_date': False,
                'stock_list': codes,
                'stock_period': sp,
                'start_time': '',
                'end_time': '',
                'count': -2,
                'dividend_type': 0,
            }
            json_str = json.dumps(code_json, ensure_ascii=False).encode('utf-8')
            ptr = self._dll.TdxFuncMain(self._run_id, json_str, 60000)
            if ptr and len(ptr) > 0:
                data = json.loads(ptr.decode('utf-8'))
                if data.get('ErrorId') in ('0', '19'):
                    return data
        except Exception:
            pass
        return {}

    def get_stock_blocks(self, code: str) -> List[str]:
        cache_key = f'stock_blocks_{code}'
        if cache_key in self._cache:
            return self._cache[cache_key]
        result = self._call(self._dll.GetGPBlockInStr, code.encode('utf-8'))
        if result is None or result.get('Value') is None:
            return []
        self._cache[cache_key] = result['Value']
        return result['Value']

    def get_stock_info(self, codes: List[str]) -> Dict:
        if not codes:
            return {}
        all_data = {}
        for code in codes:
            cache_key = f'stock_info_{code}'
            if cache_key in self._cache:
                all_data[code] = self._cache[cache_key]
                continue
            result = self._call(self._dll.GetSTOCKInStr, code.encode('utf-8'))
            if result is not None:
                self._cache[cache_key] = result
                all_data[code] = result
        return all_data

    def get_report_data(self, codes: List[str]) -> Dict:
        if not codes:
            return {}
        all_data = {}
        for code in codes:
            cache_key = f'report_{code}'
            if cache_key in self._cache:
                all_data[code] = self._cache[cache_key]
                continue
            result = self._call(self._dll.GetCWDATAInStr, code.encode('utf-8'), b'', b'')
            if result is not None:
                self._cache[cache_key] = result
                all_data[code] = result
        return all_data

    def user_block_control(self, block_code, stocks, action):
        """通过DLL调用用户板块操作。

        action: "create"=创建板块, "add"=添加股票到板块, "clear"=清空板块
        """
        if not self._ready or self._dll is None:
            return None
        try:
            if action == "create":
                logger.debug("TqConnector.user_block_control: create not supported via DLL")
                return None
            elif action == "add":
                logger.debug("TqConnector.user_block_control: add not supported via DLL")
                return None
            elif action == "clear":
                logger.debug("TqConnector.user_block_control: clear not supported via DLL")
                return None
            else:
                logger.debug("TqConnector.user_block_control: unknown action '%s'", action)
                return None
        except Exception as e:
            logger.debug("TqConnector.user_block_control failed: %s", e)
            return None

    # 内部周期格式 → DLL期望的周期格式映射
    _PERIOD_TO_DLL = {
        '1m': '1m', '5m': '5m', '15m': '15m', '30m': '30m',
        '60m': '1h', '60min': '1h', '1h': '1h',
        '1d': '1d', '1w': '1w', '1mon': '1mon',
        'day': '1d', 'week': '1w', 'month': '1mon',
    }

    def tdx_func_main(self, func_type, formula_name, formula_arg, stock_list=None,
                       xsflag=6, return_count=1, return_date=False,
                       stock_period='1d', count=100, dividend_type=1,
                       start_time='', end_time=''):
        """通过DLL调用公式评估。

        func_type: "formula_zb"=指标公式(type=0), "formula_xg"=选股公式(type=1)
        stock_list: 待评估的股票代码列表，为空则DLL自行加载
        """
        if not self._ready or self._dll is None:
            return None
        try:
            formula_type = 0 if func_type == "formula_zb" else 1
            # 将内部周期格式转换为DLL期望的格式
            dll_period = self._PERIOD_TO_DLL.get(stock_period, stock_period)
            code_json = {
                'id': self._run_id,
                'type': 4,
                'formula_name': formula_name,
                'formula_arg': _process_formula_arg(formula_arg),
                'formula_type': formula_type,
                'xsflag': xsflag,
                'return_count': return_count,
                'return_date': return_date,
                'stock_list': stock_list if stock_list else [],
                'stock_period': dll_period,
                'start_time': start_time,
                'end_time': end_time,
                'count': count,
                'dividend_type': dividend_type,
            }
            json_str = json.dumps(code_json, ensure_ascii=False).encode('utf-8')
            ptr = self._dll.TdxFuncMain(self._run_id, json_str, 60000)
            if ptr and len(ptr) > 0:
                data = json.loads(ptr.decode('utf-8'))
                if data.get('ErrorId') in ('0', '19'):
                    return data
            return None
        except Exception as e:
            logger.debug("TqConnector.tdx_func_main failed: %s", e)
            return None

    def is_ready(self) -> bool:
        return self._ready


# ===========================================================================
# TqDllProvider —— DataSourceProvider 的 DLL 实现
# ===========================================================================

class TqDllProvider(DataSourceProvider):
    """基于 TPythClient.dll 的数据源提供者。"""

    def __init__(self, bus: Optional[EventBus] = None, config: Optional[Dict[str, Any]] = None):
        super().__init__(bus=bus, config=config)
        self._connector = TqConnector()
        self._kline_cache = KLineDataCache()
        self._method_cache: Dict[str, Any] = {}

    # ------------------------------------------------------------------
    # 基础状态
    # ------------------------------------------------------------------

    def is_ready(self) -> bool:
        return self._connector.is_ready()

    def get_mode_info(self) -> str:
        return "dll"

    def _probe(self) -> Dict[str, Any]:
        """契约探测（Task 6）：探测 TQ DLL 是否就绪。

        Returns:
            {"ready": bool, "provider": "tq_dll", "error"?: str}
        """
        try:
            ready = self._connector.is_ready()
        except Exception as e:
            return {"ready": False, "provider": "tq_dll", "error": str(e)}
        if not ready:
            return {
                "ready": False,
                "provider": "tq_dll",
                "error": "通达信客户端未启动或 TPythClient.dll 不可用",
            }
        return {"ready": True, "provider": "tq_dll"}

    # ------------------------------------------------------------------
    # 行情数据
    # ------------------------------------------------------------------

    def resolve_market(self, markets) -> Dict[str, List[str]]:
        if markets is None:
            return {}
        if isinstance(markets, str):
            markets = self._parse_attrtext(markets)
        if not isinstance(markets, list):
            return {}
        result = {}
        for m in markets:
            mid = _resolve_market_id(m)
            stocks = self._connector.get_stock_list(mid)
            if stocks:
                result[m] = stocks
        return result

    def get_kline_data(self, codes, period=None, start_date=None, end_date=None, **kwargs) -> Dict:
        """获取K线数据，同时支持旧签名和新签名。"""
        count = kwargs.get('count')
        # 旧签名: period 为 int 或 count 为 int
        if isinstance(period, int) or isinstance(count, int):
            actual_count = count if count is not None else start_date
            if not isinstance(actual_count, int):
                actual_count = 3
            return self._get_kline_data_legacy(codes, period, actual_count)
        # 新签名: period 为 str
        if not codes:
            return {}
        norm_period = _norm_period(period or '1d')
        return self._get_kline_batch(codes, norm_period, start_date, end_date)

    def get_snapshot(self, codes) -> Dict[str, Dict]:
        if isinstance(codes, str):
            codes = [codes]
        raw = self._connector.get_snapshot(codes)
        if not raw:
            return {}
        result = {}
        for code, data in raw.items():
            if isinstance(data, dict):
                result[code] = self._normalize_snapshot(data)
            else:
                result[code] = data
        return result

    # ------------------------------------------------------------------
    # 板块 / 股票列表
    # ------------------------------------------------------------------

    def get_block_members(self, block_code) -> List[str]:
        return self._connector.get_block_members(block_code)

    def get_stock_list_by_type(self, list_type, customblockname='', **kwargs) -> List[Dict]:
        cache_key = f'stock_list_by_type_{list_type}_{customblockname}'
        if cache_key in self._method_cache:
            return self._method_cache[cache_key]

        # 判断是否为 spinfo.type 整数值
        spinfo_type = None
        try:
            lt_int = int(list_type)
            if lt_int in (0, 2, 4):
                spinfo_type = lt_int
        except (ValueError, TypeError):
            pass

        raw_codes = []
        if spinfo_type is not None:
            raw_codes = self._get_stocks_by_spinfo_type(spinfo_type, customblockname)
        else:
            raw_codes = self._connector.get_stock_list(list_type) or []

        result = self._codes_to_stock_list(raw_codes)
        self._method_cache[cache_key] = result
        return result

    def get_sector_list(self, list_type=1) -> List[Dict]:
        cache_key = f'sector_list_{list_type}'
        if cache_key in self._method_cache:
            return self._method_cache[cache_key]
        result = self._connector.get_block_list()
        self._method_cache[cache_key] = result
        return result

    def get_sector_stocks(self, sector_code, block_type=0) -> List[str]:
        cache_key = f'sector_stocks_{sector_code}_{block_type}'
        if cache_key in self._method_cache:
            return self._method_cache[cache_key]
        result = self._connector.get_block_members(sector_code)
        self._method_cache[cache_key] = result
        return result

    # ------------------------------------------------------------------
    # 板块扩展接口（异步，支持全部53种list_type）
    # ------------------------------------------------------------------

    async def get_stock_list(self, list_type: int = 0, **kwargs) -> List[Dict]:
        """获取股票列表（支持全部53种list_type参数）。

        Args:
            list_type: 列表类型，常用值：
                0 - 自选股          1 - 持仓股           5 - 所有A股
                6 - 上证指数成份股   7 - 上证主板         8 - 深证主板
                9 - 重点指数        10 - 所有板块指数    11 - 缺省行业板块
                12 - 概念板块       13 - 风格板块       14 - 地区板块
                23 - 沪深300        24 - 中证500         25 - 中证1000
                50 - 沪深A股        51 - 创业板          52 - 科创板
                53 - 北交所         ... （完整列表见 LIST_TYPE_MAP）
            **kwargs: 扩展参数（保留）

        Returns:
            股票代码列表 [{'code': '600000', 'name': '浦发银行', 'market': 'SH', 'setcode': 1}, ...]

        Raises:
            RuntimeError: TQ DLL 不可用时抛出
        """
        cache_key = f'async_stock_list_{list_type}'
        if cache_key in self._method_cache:
            return self._method_cache[cache_key]

        if not self._connector.is_ready():
            logger.warning("get_stock_list(list_type=%d): TQ DLL 未就绪", list_type)
            raise RuntimeError("TQ DLL 未就绪，无法获取股票列表")

        type_info = LIST_TYPE_MAP.get(list_type)
        if type_info:
            logger.info("get_stock_list: 获取 %s (list_type=%d)", type_info['name'], list_type)
        else:
            logger.info("get_stock_list: 获取未知类型 list_type=%d", list_type)

        raw_codes: List[str] = []

        # 根据 list_type 映射到对应的 market_id 列表
        market_ids = _LIST_TYPE_TO_MARKET_IDS.get(list_type)
        if market_ids is not None:
            for mid in market_ids:
                codes = self._connector.get_stock_list(mid) or []
                raw_codes.extend(codes)
        elif list_type == 0:
            # 自选股：通过用户自定义板块获取
            try:
                user_data = await self.get_user_sector()
                raw_codes = [s.get('code', '') for s in user_data.get('favorites', []) if s.get('code')]
            except Exception as e:
                logger.debug("get_stock_list(list_type=0) 获取自选股失败: %s", e)
        elif list_type in (10,):
            # 板块指数：从板块列表中提取指数类
            try:
                sectors = await self.get_sector_list()
                for sec in sectors:
                    code = sec.get('sector_code', '')
                    if code.startswith('8') or code.startswith('99'):
                        raw_codes.append(code)
            except Exception as e:
                logger.debug("get_stock_list(list_type=10) 获取板块索引失败: %s", e)
        elif list_type in (11, 12, 13, 14, 15):
            # 行业/概念/风格/地区板块：返回板块自身信息而非成分股
            try:
                sectors = await self.get_sector_list(category=_LIST_TYPE_CATEGORY_MAP.get(list_type))
                for sec in sectors:
                    raw_codes.append(sec.get('sector_code', ''))
            except Exception as e:
                logger.debug("get_stock_list(list_type=%d) 获取板块列表失败: %s", list_type, e)
        else:
            # 回退：尝试用 spinfo_type 或直接按 market_id 查询
            try:
                lt_int = int(list_type)
                if lt_int in (0, 2, 4):
                    raw_codes = self._get_stocks_by_spinfo_type(lt_int, kwargs.get('customblockname', ''))
                else:
                    # 尝试直接作为 market_id 使用
                    codes = self._connector.get_stock_list(lt_int) or []
                    raw_codes.extend(codes)
            except (ValueError, TypeError):
                pass

        result = self._codes_to_stock_list(raw_codes)
        self._method_cache[cache_key] = result
        logger.debug("get_stock_list(list_type=%d): 返回 %d 条记录", list_type, len(result))
        return result

    async def get_sector_list(self, category: str = None) -> List[Dict]:
        """获取通达信板块列表。

        Args:
            category: 板块分类（可选）
                - 'industry': 行业板块
                - 'concept': 概念板块
                - 'region': 地域板块
                - 'style': 风格板块
                - None: 全部板块

        Returns:
            板块列表 [{'sector_code': '880001', 'sector_name': '银行',
                       'category': 'industry', 'member_count': 45}, ...]
        """
        cache_key = f'sector_list_async_{category or "all"}'
        if cache_key in self._method_cache:
            return self._method_cache[cache_key]

        if not self._connector.is_ready():
            logger.warning("get_sector_list(category=%s): TQ DLL 未就绪", category)
            raise RuntimeError("TQ DLL 未就绪，无法获取板块列表")

        raw_blocks = self._connector.get_block_list() or []
        logger.info("get_sector_list: 从DLL获取到 %d 个原始板块", len(raw_blocks))

        result: List[Dict] = []
        for block in raw_blocks:
            if not isinstance(block, dict):
                continue
            sector_code = block.get('code', block.get('sector_code', ''))
            sector_name = block.get('name', block.get('sector_name', ''))

            # 推断板块分类
            blk_category = self._infer_sector_category(sector_code, sector_name)

            # 如果指定了 category 则过滤
            if category and blk_category != category:
                continue

            # 获取成员数量（不立即加载全部成员，仅计数）
            member_count = 0
            try:
                members = self._connector.get_block_members(str(sector_code))
                member_count = len(members) if members else 0
            except Exception:
                pass

            result.append({
                'sector_code': str(sector_code),
                'sector_name': str(sector_name),
                'category': blk_category,
                'member_count': member_count,
            })

        self._method_cache[cache_key] = result
        logger.debug("get_sector_list(category=%s): 返回 %d 个板块", category, len(result))
        return result

    async def get_stock_list_in_sector(self, sector: Union[str, int],
                                        by_code: bool = True,
                                        by_name: bool = False) -> List[Dict]:
        """获取板块成分股。

        Args:
            sector: 板块代码（如 '880001' 或 '880001.SH'）或名称（如 '银行'）
            by_code: 是否按代码精确匹配（默认 True）
            by_name: 是否按名称模糊搜索（默认 False）

        Returns:
            成分股列表 [{'code': '600000', 'name': '浦发银行', 'market': 'SH', 'setcode': 1}, ...]

        Raises:
            RuntimeError: TQ DLL 不可用时抛出
            ValueError: 未找到匹配板块时抛出
        """
        cache_key = f'sector_members_{sector}_bycode_{by_code}_byname_{by_name}'
        if cache_key in self._method_cache:
            return self._method_cache[cache_key]

        if not self._connector.is_ready():
            logger.warning("get_stock_list_in_sector(sector=%s): TQ DLL 未就绪", sector)
            raise RuntimeError("TQ DLL 未就绪，无法获取板块成分股")

        sector_str = str(sector).strip()
        logger.info("get_stock_list_in_sector: 查询板块 '%s' (by_code=%s, by_name=%s)",
                     sector_str, by_code, by_name)

        raw_codes: List[str] = []

        if by_code and sector_str:
            # 按代码精确查询
            # 尝试直接获取板块成员
            codes = self._connector.get_block_members(sector_str) or []
            if codes:
                raw_codes.extend(codes)
            elif '.' not in sector_str and sector_str.isdigit():
                # 尝试添加市场后缀
                for suffix in ['.SH', '.SZ']:
                    codes = self._connector.get_block_members(sector_str + suffix) or []
                    if codes:
                        raw_codes.extend(codes)
                        break

        if not raw_codes and by_name and sector_str:
            # 按名称模糊搜索：先获取全部板块列表，再匹配名称
            try:
                all_sectors = await self.get_sector_list()
                matched = [
                    s['sector_code'] for s in all_sectors
                    if sector_str in s.get('sector_name', '')
                ]
                logger.debug("get_stock_list_in_sector: 名称模糊匹配到 %d 个板块", len(matched))
                for sc in matched:
                    codes = self._connector.get_block_members(sc) or []
                    raw_codes.extend(codes)
            except Exception as e:
                logger.debug("get_stock_list_in_sector: 名称搜索失败: %s", e)

        if not raw_codes:
            logger.warning("get_stock_list_in_sector: 未找到板块 '%s' 的成分股", sector_str)
            raise ValueError(f"未找到板块 '{sector_str}' 的成分股，请检查板块代码或名称")

        result = self._codes_to_stock_list(raw_codes)
        self._method_cache[cache_key] = result
        logger.debug("get_stock_list_in_sector(sector='%s'): 返回 %d 只成分股", sector_str, len(result))
        return result

    async def get_user_sector(self) -> Dict[str, List[Dict]]:
        """获取用户在通达信客户端中创建的自定义板块。

        Returns:
            {
                'favorites': [...],     # 自选股列表
                'custom_blocks': [      # 自定义板块列表
                    {
                        'block_code': 'TEST',
                        'block_name': '测试板块',
                        'members': [
                            {'code': '600000', 'name': '浦发银行'},
                            ...
                        ]
                    },
                    ...
                ]
            }

        Raises:
            RuntimeError: TQ DLL 不可用时抛出
        """
        cache_key = 'user_sector_async'
        if cache_key in self._method_cache:
            return self._method_cache[cache_key]

        if not self._connector.is_ready():
            logger.warning("get_user_sector: TQ DLL 未就绪")
            raise RuntimeError("TQ DLL 未就绪，无法获取用户自定义板块")

        logger.info("get_user_sector: 开始获取用户自定义板块")

        # 通过 DLL 获取自选股（ZXG）
        favorites: List[Dict] = []
        try:
            zxg_codes = self._connector.get_block_members('ZXG') or []
            favorites = self._codes_to_stock_list(zxg_codes)
            logger.debug("get_user_sector: 获取到 %d 只自选股", len(favorites))
        except Exception as e:
            logger.debug("get_user_sector: 获取自选股(ZXG)失败: %s", e)

        # 获取自定义板块列表（CSBK 开头的板块）
        custom_blocks: List[Dict] = []
        try:
            all_blocks = self._connector.get_block_list() or []
            csbk_pattern = ('CSBK', 'ZXG')
            for block in all_blocks:
                if not isinstance(block, dict):
                    continue
                bcode = str(block.get('code', block.get('block_code', '')))
                bname = str(block.get('name', block.get('block_name', '')))

                # 筛选自定义板块（以 CSBK 开头或已知自定义前缀）
                is_custom = any(bcode.upper().startswith(p) for p in csbk_pattern)
                if not is_custom and bcode.upper() != 'ZXG':
                    continue

                # 获取板块成员
                members_raw = self._connector.get_block_members(bcode) or []
                members = self._codes_to_stock_list(members_raw)

                custom_blocks.append({
                    'block_code': bcode,
                    'block_name': bname,
                    'members': members,
                })
            logger.debug("get_user_sector: 获取到 %d 个自定义板块", len(custom_blocks))
        except Exception as e:
            logger.debug("get_user_sector: 获取自定义板块列表失败: %s", e)

        result = {
            'favorites': favorites,
            'custom_blocks': custom_blocks,
        }
        self._method_cache[cache_key] = result
        logger.info("get_user_sector: 完成，共 %d 只自选股 + %d 个自定义板块",
                     len(favorites), len(custom_blocks))
        return result

    async def send_user_block(self, block_code: str, stocks: List[Dict]) -> bool:
        """发送自定义板块到通达信客户端。

        Args:
            block_code: 板块代码（如 'CSBK_AI'、'ZXG' 表示自选股）
            stocks: 股票列表 [{'code': '600000', 'setcode': 1}, ...]

        Returns:
            bool: 是否发送成功

        Note:
            block_code 为客户端已有的自定义板块简称，如果不存在则无效果。
            空字符串则表示添加到临时条件股。
            自选股的 block_code 为 'ZXG'。
        """
        if not stocks:
            logger.info("send_user_block(block_code='%s'): 股票列表为空，跳过", block_code)
            return True

        if not self._connector.is_ready():
            logger.warning("send_user_block(block_code='%s'): TQ DLL 未就绪", block_code)
            return False

        logger.info("send_user_block: 发送 %d 只股票到板块 '%s'", len(stocks), block_code)

        try:
            # 将标准格式转换为 DLL 期望的格式
            stock_codes = []
            for s in stocks:
                code = s.get('code', '')
                setcode = s.get('setcode', '')
                if code:
                    if setcode == '':
                        stock_codes.append(code)
                    elif isinstance(setcode, int):
                        market = {0: 'SZ', 1: 'SH', 2: 'BJ'}.get(setcode, 'SZ')
                        stock_codes.append(f"{code}.{market}")
                    else:
                        stock_codes.append(code)

            result = self._connector.user_block_control(block_code, stock_codes, "add")
            if result is not None:
                logger.info("send_user_block(block_code='%s'): 发送成功", block_code)
                return True
            else:
                logger.warning("send_user_block(block_code='%s'): DLL 返回空结果", block_code)
                return False
        except Exception as e:
            logger.error("send_user_block(block_code='%s'): 发送异常: %s", block_code, e)
            return False

    # ------------------------------------------------------------------
    # 公式评估
    # ------------------------------------------------------------------

    def eval_indicator(self, codes, formula_text, period, sorttype=0) -> Dict:
        if isinstance(formula_text, bytes):
            formula_text = formula_text.decode('utf-8')
        if formula_text and not any(c in formula_text for c in '()><=,;'):
            try:
                decoded = decode_formula(formula_text)
                if decoded and any(c in decoded for c in '()><=,;ABCDEFGHIJKLMNOPQRSTUVWXYZ'):
                    formula_text = decoded
            except Exception:
                pass
        result = self._connector.eval_formula(formula_text, codes, period)
        if sorttype > 0 and 'result' in result:
            sorted_items = sorted(
                result['result'].items(),
                key=lambda x: x[1] if isinstance(x[1], (int, float)) else 0,
                reverse=True,
            )
            result['result'] = dict(sorted_items[:sorttype])
            result['selected_count'] = min(sorttype, len(sorted_items))
        elif 'result' in result:
            result['selected_count'] = len(result['result'])
        return result

    def eval_formula_xg(self, formula_name, formula_arg='', stock_list=None,
                        period='1d', count=0, dividend_type=1,
                        start_time='', end_time='') -> Dict:
        codes = self._resolve_formula_stock_list(stock_list)
        raw = self._connector.tdx_func_main(
            "formula_xg", formula_name, formula_arg, codes,
            xsflag=6, return_count=1, return_date=False,
            stock_period=period, count=count, dividend_type=dividend_type,
            start_time=start_time or '', end_time=end_time or ''
        )
        if raw is None:
            return {"success": False, "result": {}, "selected_codes": []}
        result = dict(raw)
        if 'result' not in result or not result['result']:
            return {"success": False, "result": {}, "selected_codes": []}
        if 'success' not in result:
            result['success'] = True
        selected = [c for c, v in result['result'].items()
                    if v is True or (v is not None and v != 0 and v is not False)]
        result['selected_codes'] = selected
        return result

    def eval_formula_zb(self, formula_name, formula_arg='', stock_list=None,
                        period='1d', count=5, dividend_type=1,
                        return_count=1, return_date=False,
                        xsflag=6, start_time='', end_time='') -> Dict:
        codes = self._resolve_formula_stock_list(stock_list)
        raw = self._connector.tdx_func_main(
            "formula_zb", formula_name, formula_arg, codes,
            xsflag=xsflag, return_count=return_count, return_date=return_date,
            stock_period=period, count=count, dividend_type=dividend_type,
            start_time=start_time or '', end_time=end_time or ''
        )
        if raw is None:
            return {"success": False, "result": {}, "result_detail": {}}

        # 兼容 DLL 已返回 result 键的情况
        if 'result' in raw:
            return {
                "success": raw.get('success', True),
                "result": raw['result'],
                "result_detail": raw.get('result_detail', {}),
            }

        # 解析 DLL 原始返回格式：{code: {indicator: [values]}, ErrorId: ...}
        skip_keys = {'ErrorId', 'ErrMsg', 'error', 'err'}
        result_detail = {}
        result = {}
        for key, value in raw.items():
            if key in skip_keys:
                continue
            if isinstance(value, dict):
                result_detail[key] = value
                lines = list(value.values())
                if lines:
                    result[key] = lines[-1]
            else:
                result[key] = value

        return {
            "success": True,
            "result": result,
            "result_detail": result_detail,
        }

    # ------------------------------------------------------------------
    # 板块操作
    # ------------------------------------------------------------------

    def send_user_block(self, block_code, stocks, show=True) -> Dict:
        self._connector.user_block_control(block_code, stocks, "add")
        return {"success": True, "block_code": block_code, "count": len(stocks)}

    def create_sector(self, block_code, block_name) -> Dict:
        self._connector.user_block_control(block_code, [], "create")
        return {"success": True, "block_code": block_code}

    def clear_sector(self, block_code) -> Dict:
        self._connector.user_block_control(block_code, [], "clear")
        return {"success": True, "block_code": block_code}

    # ------------------------------------------------------------------
    # 财务 / 回放 / 重采样
    # ------------------------------------------------------------------

    def get_financial_data(self, codes, fields) -> Dict:
        raw = self._connector.get_stock_info(codes)
        if not fields:
            return raw
        result = {}
        for code, data in raw.items():
            result[code] = {f: data.get(f) for f in fields if f in data}
        return result

    def get_replay_data(self, codes, current_time, period='1min') -> Dict[str, List[Dict]]:
        if len(current_time.strip()) == 10:
            end_dt = datetime.strptime(current_time, '%Y-%m-%d')
            start_dt = end_dt - timedelta(days=120)
        else:
            end_dt = datetime.strptime(current_time, '%Y-%m-%d %H:%M:%S')
            start_dt = end_dt - timedelta(days=30)
        start_date = start_dt.strftime('%Y-%m-%d')
        end_date = end_dt.strftime('%Y-%m-%d')
        all_klines = self.get_kline_data(codes, period, start_date, end_date)
        result = {}
        for code, bars in all_klines.items():
            filtered = [b for b in bars if b['time'] <= current_time]
            result[code] = filtered
        return result

    def resample_kline(self, kline_1min, target_period) -> List[Dict]:
        if not kline_1min:
            return []
        try:
            import pandas as pd
            df = pd.DataFrame(kline_1min)
            df['time'] = pd.to_datetime(df['time'])
            df.set_index('time', inplace=True)
            rule_map = {
                '5min': '5min', '5m': '5min',
                '15min': '15min', '15m': '15min',
                '30min': '30min', '30m': '30min',
                '60min': '60min', '60m': '60min',
                'day': 'D', '1d': 'D',
            }
            rule = rule_map.get(target_period, '5min')
            resampled = df.resample(rule).agg({
                'open': 'first',
                'high': 'max',
                'low': 'min',
                'close': 'last',
                'volume': 'sum',
                'amount': 'sum',
            }).dropna()
            result = []
            for idx, row in resampled.iterrows():
                result.append({
                    'open': round(float(row['open']), 2),
                    'high': round(float(row['high']), 2),
                    'low': round(float(row['low']), 2),
                    'close': round(float(row['close']), 2),
                    'volume': int(row['volume']),
                    'amount': round(float(row['amount']), 2),
                    'time': idx.strftime('%Y-%m-%d %H:%M:%S'),
                })
            return result
        except Exception:
            return []

    # ------------------------------------------------------------------
    # 内部辅助方法
    # ------------------------------------------------------------------

    @staticmethod
    def _infer_sector_category(sector_code: str, sector_name: str) -> str:
        """根据板块代码和名称推断板块分类。

        Args:
            sector_code: 板块代码（如 '880001'）
            sector_name: 板块名称（如 '银行'）

        Returns:
            分类字符串：'industry' / 'concept' / 'region' / 'style' / 'index' / 'unknown'
        """
        code_str = str(sector_code).strip()
        name_str = str(sector_name).strip()

        # 1. 按代码前缀判断
        for prefix, cat in _SECTOR_CODE_PREFIX_CATEGORY.items():
            if code_str.startswith(prefix):
                return cat

        # 2. 按名称关键词判断
        for keyword, cat in _SECTOR_NAME_KEYWORD_CATEGORY.items():
            if keyword in name_str:
                return cat

        # 3. 默认归为行业板块（通达信默认分类）
        return 'industry'

    @staticmethod
    def _normalize_snapshot(raw: dict) -> dict:
        """将原始快照数据标准化为统一格式。"""
        if not isinstance(raw, dict):
            return raw

        def _get(*keys, default=None):
            for k in keys:
                v = raw.get(k)
                if v is not None:
                    try:
                        return float(v) if default is not None and isinstance(default, (int, float)) else v
                    except (ValueError, TypeError):
                        continue
            return default

        name = _get('name', 'Name', default='')
        close = _get('close', 'Close', 'price', 'Price', 'now', 'Now', default=0.0)
        open_p = _get('open', 'Open', default=0.0)
        high = _get('high', 'High', default=0.0)
        low = _get('low', 'Low', default=0.0)
        pre_close = _get('pre_close', 'PreClose', 'yesterday_close', 'YesterdayClose', default=0.0)
        volume = _get('volume', 'Volume', default=0)
        amount = _get('amount', 'Amount', default=0.0)
        turnover_rate = _get('turnover_rate', 'TurnoverRate', default=0.0)
        volume_ratio = _get('volume_ratio', 'VolumeRatio', default=0.0)
        pe_ratio = _get('pe_ratio', 'PERatio', default=0.0)
        bid_price = _get('bid_price', 'BidPrice', default=0.0)
        ask_price = _get('ask_price', 'AskPrice', default=0.0)

        # Calculate change_pct if not directly available
        change_pct = _get('change_pct', 'ChangePercent', 'rise', 'Rise', default=None)
        if change_pct is None and pre_close and close:
            change_pct = round((close - pre_close) / pre_close * 100, 2) if pre_close else 0.0

        # Calculate change_amt
        change_amt = _get('change_amt', 'ChangeAmount', default=None)
        if change_amt is None and close and pre_close:
            change_amt = round(close - pre_close, 2)

        # Ensure volume is int
        try:
            volume = int(volume)
        except (ValueError, TypeError):
            volume = 0

        return {
            'name': name,
            'close': close,
            'price': close,
            'now': close,
            'open': open_p,
            'high': high,
            'low': low,
            'pre_close': pre_close,
            'change_pct': change_pct or 0.0,
            'change_amt': change_amt or 0.0,
            'rise': change_pct or 0.0,
            'bid_price': bid_price,
            'ask_price': ask_price,
            'volume': volume,
            'amount': amount,
            'turnover_rate': turnover_rate,
            'volume_ratio': volume_ratio,
            'pe_ratio': pe_ratio,
        }

    @staticmethod
    def _parse_attrtext(attrtext: str) -> List[str]:
        if not attrtext:
            return []
        markets = []
        for item in attrtext.split():
            item = item.strip()
            if item and '#' in item:
                markets.append(item)
        return markets

    def _get_kline_data_legacy(self, codes: List[str], period: int, count: int) -> Dict:
        return self._connector.get_kline_data(codes, period, count)

    def _get_kline_batch(self, codes: List[str], period: str, start_date: str, end_date: str) -> Dict[str, List[Dict]]:
        # 将 SH600000 格式转换为 DLL 期望的 600000.SH 格式
        def _to_dll_code(c: str) -> str:
            if len(c) >= 8 and c[:2] in ('SH', 'SZ') and c[2:].isdigit():
                return c[2:] + '.' + c[:2]
            return c

        # 解析起止时间，支持 "YYYY-MM-DD" 和 "YYYY-MM-DDTHH:MM" 格式
        has_time = 'T' in start_date or len(start_date) > 10
        if has_time:
            start_dt = datetime.strptime(start_date[:16], '%Y-%m-%dT%H:%M')
            end_dt = datetime.strptime(end_date[:16], '%Y-%m-%dT%H:%M')
        else:
            start_dt = datetime.strptime(start_date, '%Y-%m-%d')
            end_dt = datetime.strptime(end_date, '%Y-%m-%d')
        # 用于DLL请求的日期范围（取整天）
        start_day = start_dt.strftime('%Y-%m-%d')
        end_day = end_dt.strftime('%Y-%m-%d')

        dll_codes = [_to_dll_code(c) for c in codes]
        code_map = {dll: orig for dll, orig in zip(dll_codes, codes)}

        result = {}
        uncached = []
        uncached_dll = []
        for orig_code, dll_code in zip(codes, dll_codes):
            if self._kline_cache.has(orig_code, period):
                result[orig_code] = self._kline_cache.get(orig_code, period)
            else:
                uncached.append(orig_code)
                uncached_dll.append(dll_code)

        if not uncached:
            # 即使全部缓存，也需要按时间范围过滤
            if has_time:
                for code in result:
                    result[code] = self._filter_bars_by_time(result[code], start_dt, end_dt)
            return result

        period_int = PERIOD_MAP.get(period, 6)
        days = (end_dt - start_dt).days + 1
        count = min(days * 5, 5000) if period_int >= 6 else min(days * 240, 5000)

        raw = self._connector.get_kline_data(uncached_dll, period_int, count)
        new_data = self._convert_kline_raw(raw)

        for dll_code, bars in new_data.items():
            orig_code = code_map.get(dll_code, dll_code)
            self._kline_cache.put(orig_code, period, bars)
            result[orig_code] = bars

        # 按精确时间范围过滤
        if has_time:
            for code in result:
                result[code] = self._filter_bars_by_time(result[code], start_dt, end_dt)
        return result

    @staticmethod
    def _filter_bars_by_time(bars: List[Dict], start_dt: datetime, end_dt: datetime) -> List[Dict]:
        """按精确时间范围过滤K线数据"""
        filtered = []
        for bar in bars:
            t = bar.get('time', '')
            if not t:
                continue
            try:
                # K线时间格式: "2024-01-15 09:31" 或 "2024-01-15"
                if ' ' in t and ':' in t:
                    bar_dt = datetime.strptime(t[:16], '%Y-%m-%d %H:%M')
                elif 'T' in t:
                    bar_dt = datetime.strptime(t[:16], '%Y-%m-%dT%H:%M')
                else:
                    bar_dt = datetime.strptime(t[:10], '%Y-%m-%d')
                if start_dt <= bar_dt <= end_dt:
                    filtered.append(bar)
            except (ValueError, TypeError):
                filtered.append(bar)  # 解析失败的保留
        return filtered

    @staticmethod
    def _convert_kline_raw(raw: Dict) -> Dict[str, List[Dict]]:
        result = {}
        for code, data in raw.items():
            if not isinstance(data, dict):
                continue
            dates = data.get('Date') or data.get('date') or []
            times = data.get('Time') or data.get('time') or []
            opens = data.get('Open') or data.get('open') or []
            highs = data.get('High') or data.get('high') or []
            lows = data.get('Low') or data.get('low') or []
            closes = data.get('Close') or data.get('close') or []
            volumes = data.get('Volume') or data.get('volume') or []
            n = min(len(dates), len(opens), len(highs), len(lows), len(closes))
            bars = []
            for i in range(n):
                o = float(opens[i]) if opens[i] is not None else 0
                h = float(highs[i]) if highs[i] is not None else 0
                lo = float(lows[i]) if lows[i] is not None else 0
                c = float(closes[i]) if closes[i] is not None else 0
                v = int(float(volumes[i])) if i < len(volumes) and volumes[i] is not None else 0
                d = str(dates[i])
                t = str(times[i]) if i < len(times) else ''
                if len(d) == 8:
                    date_part = f'{d[:4]}-{d[4:6]}-{d[6:8]}'
                    if len(t) == 6:
                        time_str = f'{date_part} {t[:2]}:{t[2:4]}:{t[4:6]}'
                    else:
                        time_str = f'{date_part} 00:00:00'
                else:
                    time_str = d
                bars.append({
                    'open': round(o, 2),
                    'high': round(h, 2),
                    'low': round(lo, 2),
                    'close': round(c, 2),
                    'volume': v,
                    'amount': round(v * (o + c) / 2, 2),
                    'time': time_str,
                })
            if bars:
                result[code] = bars
        return result

    def _get_stocks_by_spinfo_type(self, spinfo_type: int, customblockname: str) -> List[str]:
        if spinfo_type == 0:
            codes = []
            for market_key in ('SZ#深证A股', 'SH#上证A股', 'BJ#北证A股'):
                mid = _resolve_market_id(market_key)
                codes.extend(self._connector.get_stock_list(mid) or [])
            return codes
        elif spinfo_type == 2:
            codes = []
            for market_key in ('SZ#深证A股', 'SH#上证A股'):
                mid = _resolve_market_id(market_key)
                codes.extend(self._connector.get_stock_list(mid) or [])
            return codes
        elif spinfo_type == 4:
            if customblockname:
                return self._connector.get_block_members(customblockname) or []
            return []
        return []

    @staticmethod
    def _codes_to_stock_list(codes: List[str]) -> List[Dict]:
        result = []
        for tq_code in (codes or []):
            if not tq_code:
                continue
            if '.' in tq_code:
                parts = tq_code.split('.')
                code = parts[0]
                market = parts[1].upper() if len(parts) > 1 else ''
            else:
                code = tq_code
                if code.startswith('6'):
                    market = 'SH'
                elif code.startswith(('0', '3')):
                    market = 'SZ'
                elif code.startswith(('4', '8')):
                    market = 'BJ'
                else:
                    market = 'SZ'
            setcode = SHORT_NAME_TO_MARKET_ID.get(
                {'SZ': 'sz_a', 'SH': 'sh_a', 'BJ': 'bj_a'}.get(market, 'sz_a'),
                0,
            )
            name = code
            result.append({
                'setcode': setcode,
                'code': code,
                'name': name,
            })
        return result

    def _resolve_formula_stock_list(self, stock_list=None) -> List[str]:
        if stock_list:
            return list(stock_list)
        stock_dicts = self.get_stock_list_by_type(2)
        if not stock_dicts:
            return []
        return [
            f"{s['code']}.{('SZ' if s['setcode'] == 0 else 'SH' if s['setcode'] == 1 else 'BJ')}"
            for s in stock_dicts
        ]


# ===========================================================================
# TqSdkBridge —— 从 tq_adapter.py 提取的 SDK 桥接层
# ===========================================================================

class TqSdkBridge:
    """封装 TQ SDK (tqcenter.tq) 的底层调用，提供缓存与异常兜底。"""

    def __init__(self):
        self._tq = None
        self._ready = False
        self._cache: Dict[str, Any] = {}
        self._init_sdk()

    def _init_sdk(self):
        try:
            import sys as _sys
            user_dir = str(Path(__file__).resolve().parents[1] / 'user')
            if user_dir not in _sys.path:
                _sys.path.insert(0, user_dir)
            from tqcenter import tq as _tq_cls
            self._tq = _tq_cls
            if not _tq_cls._initialized:
                init_path = str(Path(__file__).resolve().parents[2])
                _tq_cls.initialize(init_path)
            self._ready = _tq_cls._initialized
        except Exception:
            self._tq = None
            self._ready = False

    def is_ready(self) -> bool:
        if self._tq is None:
            return False
        return self._tq._initialized

    def get_stock_list(self, market_id, **kwargs) -> List[str]:
        if kwargs.get('list_type') is not None:
            # 新签名: get_stock_list(list_type, list_type=1)
            cache_key = f'stock_list_by_type_{market_id}'
            if cache_key in self._cache:
                return self._cache[cache_key]
            if not self._tq:
                return []
            try:
                result = self._tq.get_stock_list(market_id, **kwargs)
                self._cache[cache_key] = result
                return result
            except Exception as e:
                logger.debug("TqSdkBridge.get_stock_list(by_type) failed: %s", e)
                return []
        # 旧签名: get_stock_list(market_id: int)
        cache_key = f'stock_list_{market_id}'
        if cache_key in self._cache:
            return self._cache[cache_key]
        try:
            result = self._tq.get_stock_list(market=str(market_id))
            self._cache[cache_key] = result
            return result
        except Exception:
            return []

    def get_kline_data(self, codes: List[str], period: int, count: int) -> Dict:
        if not codes:
            return {}
        period_str = _PERIOD_INT_TO_STR.get(period, '1d')
        try:
            raw = self._tq.get_market_data(stock_list=codes, period=period_str, count=count)
            result = {}
            for code in codes:
                code_data = {}
                for field, df in raw.items():
                    if hasattr(df, 'columns') and code in df.columns:
                        code_data[field] = df[code].tolist()
                if code_data:
                    first_df = next((v for v in raw.values() if hasattr(v, 'index')), None)
                    if first_df is not None:
                        code_data['Date'] = [d.strftime('%Y%m%d') for d in first_df.index]
                    result[code] = code_data
            return result
        except Exception:
            return {}

    def get_snapshot(self, codes: List[str]) -> Dict:
        if not codes:
            return {}
        all_data = {}
        for code in codes:
            cache_key = f'snapshot_{code}'
            if cache_key in self._cache:
                all_data[code] = self._cache[cache_key]
                continue
            try:
                data = self._tq.get_market_snapshot(stock_code=code)
                self._cache[cache_key] = data
                all_data[code] = data
            except Exception:
                pass
        return all_data

    def get_block_list(self) -> List[str]:
        cache_key = 'block_list'
        if cache_key in self._cache:
            return self._cache[cache_key]
        try:
            result = self._tq.get_sector_list()
            self._cache[cache_key] = result
            return result
        except Exception:
            return []

    def get_block_members(self, block_code: str) -> List[str]:
        cache_key = f'block_members_{block_code}'
        if cache_key in self._cache:
            return self._cache[cache_key]
        try:
            result = self._tq.get_stock_list_in_sector(block_code=block_code)
            self._cache[cache_key] = result
            return result
        except Exception:
            return []

    def eval_formula(self, formula_text: str, codes: List[str], period: int) -> Dict:
        if not codes:
            return {}
        try:
            result = self._tq.formula_zb(formula_name=formula_text)
            return result
        except Exception:
            return {}

    def get_stock_info(self, codes: List[str]) -> Dict:
        if not codes:
            return {}
        all_data = {}
        for code in codes:
            cache_key = f'stock_info_{code}'
            if cache_key in self._cache:
                all_data[code] = self._cache[cache_key]
                continue
            try:
                data = self._tq.get_stock_info(stock_code=code)
                self._cache[cache_key] = data
                all_data[code] = data
            except Exception:
                pass
        return all_data

    def get_report_data(self, codes: List[str]) -> Dict:
        if not codes:
            return {}
        all_data = {}
        for code in codes:
            cache_key = f'report_{code}'
            if cache_key in self._cache:
                all_data[code] = self._cache[cache_key]
                continue
            try:
                data = self._tq.get_market_snapshot(stock_code=code)
                if data:
                    self._cache[cache_key] = data
                    all_data[code] = data
            except Exception:
                pass
        return all_data

    def get_sector_list(self, list_type=1) -> List[Dict]:
        cache_key = f'sector_list_{list_type}'
        if cache_key in self._cache:
            return self._cache[cache_key]
        if not self._tq:
            return []
        try:
            result = self._tq.get_sector_list(list_type=list_type)
            self._cache[cache_key] = result
            return result
        except Exception as e:
            logger.debug("TqSdkBridge.get_sector_list failed: %s", e)
            return []

    def get_stock_list_in_sector(self, sector_code, block_type=0, **kwargs) -> List[str]:
        cache_key = f'stock_list_in_sector_{sector_code}_{block_type}'
        if cache_key in self._cache:
            return self._cache[cache_key]
        if not self._tq:
            return []
        try:
            result = self._tq.get_stock_list_in_sector(sector_code, block_type=block_type, **kwargs)
            self._cache[cache_key] = result
            return result
        except Exception as e:
            logger.debug("TqSdkBridge.get_stock_list_in_sector failed: %s", e)
            return []

    def send_user_block(self, block_code, stocks, show=True):
        if not self._tq:
            return None
        try:
            result = self._tq.send_user_block(block_code=block_code, stocks=stocks, show=show)
            return result
        except Exception as e:
            logger.debug("TqSdkBridge.send_user_block failed: %s", e)
            return None

    def create_sector(self, block_code, block_name):
        if not self._tq:
            return None
        try:
            result = self._tq.create_sector(block_code=block_code, block_name=block_name)
            return result
        except Exception as e:
            logger.debug("TqSdkBridge.create_sector failed: %s", e)
            return None

    def clear_sector(self, block_code):
        if not self._tq:
            return None
        try:
            result = self._tq.clear_sector(block_code=block_code)
            return result
        except Exception as e:
            logger.debug("TqSdkBridge.clear_sector failed: %s", e)
            return None

    def formula_process_mul_xg(self, **kwargs):
        if not self._tq:
            return None
        try:
            result = self._tq.formula_process_mul_xg(**kwargs)
            return result
        except Exception as e:
            logger.debug("TqSdkBridge.formula_process_mul_xg failed: %s", e)
            return None

    def formula_process_mul_zb(self, **kwargs):
        if not self._tq:
            return None
        try:
            result = self._tq.formula_process_mul_zb(**kwargs)
            return result
        except Exception as e:
            logger.debug("TqSdkBridge.formula_process_mul_zb failed: %s", e)
            return None


# ===========================================================================
# TqSdkProvider —— DataSourceProvider 接口实现
# ===========================================================================

class TqSdkProvider(DataSourceProvider):
    """基于 TQ SDK 的数据源提供者。"""

    def __init__(self, bus: Optional[EventBus] = None, config: Optional[Dict[str, Any]] = None):
        super().__init__(bus=bus, config=config)
        self._bridge = TqSdkBridge()
        self._kline_cache = KLineDataCache()

    # ------------------------------------------------------------------
    # 基础状态
    # ------------------------------------------------------------------

    def is_ready(self) -> bool:
        return self._bridge.is_ready()

    def get_mode_info(self) -> str:
        return "sdk"

    def _probe(self) -> Dict[str, Any]:
        """契约探测（Task 6）：探测天勤 TQ SDK bridge 是否就绪。

        Returns:
            {"ready": bool, "provider": "tq_sdk", "error"?: str}
        """
        try:
            ready = self._bridge.is_ready()
        except Exception as e:
            return {"ready": False, "provider": "tq_sdk", "error": str(e)}
        if not ready:
            return {
                "ready": False,
                "provider": "tq_sdk",
                "error": "天勤 TQ SDK 账户未登录或网络不可用",
            }
        return {"ready": True, "provider": "tq_sdk"}

    # ------------------------------------------------------------------
    # 行情数据
    # ------------------------------------------------------------------

    def resolve_market(self, markets) -> Dict[str, List[str]]:
        """解析市场列表，返回 {市场名: [股票代码]} 映射。"""
        result: Dict[str, List[str]] = {}
        if not markets:
            return result
        if isinstance(markets, str):
            markets = [markets]
        for market_key in markets:
            market_id = _resolve_market_id(market_key)
            codes = self._bridge.get_stock_list(market_id)
            if codes:
                result[market_key] = codes
        return result

    def get_kline_data(self, codes, period=None, start_date=None, end_date=None, **kwargs) -> Dict:
        """获取K线数据，支持新旧两种调用签名。

        新签名: get_kline_data(codes, period='1d', start_date=None, end_date=None)
                返回标准列表格式: {code: [{'time': '...', 'open': ..., ...}, ...]}
        旧签名: get_kline_data(codes, period=6, start_date=None, end_date=None)
                其中 period 为整数，返回原始 dict 格式: {code: {'Date': [...], 'Open': [...], ...}}
        """
        if not codes:
            return {}

        # 判断是否为旧签名（period 为整数）
        legacy_mode = isinstance(period, int)

        # 解析 period 为整数
        if period is None:
            period_int = 6  # 默认日线
        elif isinstance(period, int):
            period_int = period
        else:
            period_int = map_period(str(period))

        # 计算 count
        count = kwargs.get('count', 500)

        # 尝试缓存
        if isinstance(codes, str):
            codes = [codes]

        raw = self._bridge.get_kline_data(codes, period_int, count)
        if not raw:
            return {}

        # 旧签名：返回原始 dict 格式
        if legacy_mode:
            return raw

        # 新签名：将原始格式转换为标准列表格式
        result = self._convert_raw_kline(raw, period_int)

        # 如果指定了日期范围，进行过滤
        if start_date or end_date:
            result = self._filter_kline_by_date(result, start_date, end_date)

        return result

    @staticmethod
    def _normalize_snapshot(raw: dict) -> dict:
        """将原始快照数据标准化为统一格式。"""
        if not isinstance(raw, dict):
            return raw

        def _get(*keys, default=None):
            for k in keys:
                v = raw.get(k)
                if v is not None:
                    try:
                        return float(v) if default is not None and isinstance(default, (int, float)) else v
                    except (ValueError, TypeError):
                        continue
            return default

        name = _get('name', 'Name', default='')
        close = _get('close', 'Close', 'price', 'Price', 'now', 'Now', default=0.0)
        open_p = _get('open', 'Open', default=0.0)
        high = _get('high', 'High', default=0.0)
        low = _get('low', 'Low', default=0.0)
        pre_close = _get('pre_close', 'PreClose', 'yesterday_close', 'YesterdayClose', default=0.0)
        volume = _get('volume', 'Volume', default=0)
        amount = _get('amount', 'Amount', default=0.0)
        turnover_rate = _get('turnover_rate', 'TurnoverRate', default=0.0)
        volume_ratio = _get('volume_ratio', 'VolumeRatio', default=0.0)
        pe_ratio = _get('pe_ratio', 'PERatio', default=0.0)
        bid_price = _get('bid_price', 'BidPrice', default=0.0)
        ask_price = _get('ask_price', 'AskPrice', default=0.0)

        # Calculate change_pct if not directly available
        change_pct = _get('change_pct', 'ChangePercent', 'rise', 'Rise', default=None)
        if change_pct is None and pre_close and close:
            change_pct = round((close - pre_close) / pre_close * 100, 2) if pre_close else 0.0

        # Calculate change_amt
        change_amt = _get('change_amt', 'ChangeAmount', default=None)
        if change_amt is None and close and pre_close:
            change_amt = round(close - pre_close, 2)

        # Ensure volume is int
        try:
            volume = int(volume)
        except (ValueError, TypeError):
            volume = 0

        return {
            'name': name,
            'close': close,
            'price': close,
            'now': close,
            'open': open_p,
            'high': high,
            'low': low,
            'pre_close': pre_close,
            'change_pct': change_pct or 0.0,
            'change_amt': change_amt or 0.0,
            'rise': change_pct or 0.0,
            'bid_price': bid_price,
            'ask_price': ask_price,
            'volume': volume,
            'amount': amount,
            'turnover_rate': turnover_rate,
            'volume_ratio': volume_ratio,
            'pe_ratio': pe_ratio,
        }

    def _convert_raw_kline(self, raw: Dict, period_int: int) -> Dict:
        """将 TqSdkBridge 返回的原始 dict 格式转换为标准列表格式。

        原始格式: {code: {'Open': [...], 'Close': [...], 'Date': [...]}}
        标准格式: {code: [{'time': '...', 'open': ..., 'close': ..., ...}, ...]}
        """
        result = {}
        for code, code_data in raw.items():
            if not code_data:
                continue
            dates = code_data.get('Date', [])
            opens = code_data.get('Open', code_data.get('open', []))
            highs = code_data.get('High', code_data.get('high', []))
            lows = code_data.get('Low', code_data.get('low', []))
            closes = code_data.get('Close', code_data.get('close', []))
            volumes = code_data.get('Volume', code_data.get('volume', []))
            amounts = code_data.get('Amount', code_data.get('amount', []))

            bars = []
            n = len(dates)
            for i in range(n):
                d = str(dates[i]) if i < len(dates) else ''
                if len(d) == 8:
                    time_str = f'{d[:4]}-{d[4:6]}-{d[6:8]} 00:00:00'
                else:
                    time_str = d
                bar = {
                    'time': time_str,
                    'open': float(opens[i]) if i < len(opens) else 0.0,
                    'high': float(highs[i]) if i < len(highs) else 0.0,
                    'low': float(lows[i]) if i < len(lows) else 0.0,
                    'close': float(closes[i]) if i < len(closes) else 0.0,
                    'volume': float(volumes[i]) if i < len(volumes) else 0.0,
                    'amount': float(amounts[i]) if i < len(amounts) else 0.0,
                }
                bars.append(bar)

            if bars:
                period_str = _PERIOD_INT_TO_STR.get(period_int, '1d')
                self._kline_cache.put(code, period_str, bars)

            result[code] = bars
        return result

    def _filter_kline_by_date(self, data: Dict, start_date=None, end_date=None) -> Dict:
        """按日期范围过滤K线数据。"""
        result = {}
        for code, bars in data.items():
            filtered = bars
            if start_date:
                start_str = str(start_date).replace('-', '')
                filtered = [b for b in filtered if b.get('time', '').replace('-', '').replace(':', '').replace(' ', '')[:8] >= start_str]
            if end_date:
                end_str = str(end_date).replace('-', '')
                filtered = [b for b in filtered if b.get('time', '').replace('-', '').replace(':', '').replace(' ', '')[:8] <= end_str]
            result[code] = filtered
        return result

    def get_snapshot(self, codes) -> Dict[str, Dict]:
        """获取实时快照。"""
        if isinstance(codes, str):
            codes = [codes]
        raw = self._bridge.get_snapshot(codes)
        if not raw:
            return {}
        result = {}
        for code, data in raw.items():
            if isinstance(data, dict):
                result[code] = self._normalize_snapshot(data)
            else:
                result[code] = data
        return result

    # ------------------------------------------------------------------
    # 板块 / 股票列表
    # ------------------------------------------------------------------

    def get_block_members(self, block_code) -> List[str]:
        return self._bridge.get_block_members(block_code)

    def get_stock_list_by_type(self, list_type, customblockname='', **kwargs) -> List[Dict]:
        """按类型获取股票列表。

        支持两种调用方式:
        1. spinfo.type 整数值: 0=全市场, 2=全部A股, 4=自定义板块
        2. 传统 list_type 字符串
        """
        # 判断是否为 spinfo.type 整数值
        spinfo_type = None
        try:
            lt_int = int(list_type)
            if lt_int in (0, 2, 4):
                spinfo_type = lt_int
        except (ValueError, TypeError):
            pass

        if spinfo_type is not None:
            raw_codes = self._get_stocks_by_spinfo_type(spinfo_type, customblockname)
        elif customblockname:
            raw_codes = self._bridge.get_stock_list_in_sector(customblockname, **kwargs)
        else:
            raw_codes = self._bridge.get_stock_list(list_type, list_type=list_type, **kwargs)

        return self._codes_to_stock_list(raw_codes)

    def _get_stocks_by_spinfo_type(self, spinfo_type: int, customblockname: str) -> List[str]:
        if spinfo_type == 0:
            codes = []
            for market_key in ('SZ#深证A股', 'SH#上证A股', 'BJ#北证A股'):
                mid = _resolve_market_id(market_key)
                codes.extend(self._bridge.get_stock_list(mid) or [])
            return codes
        elif spinfo_type == 2:
            codes = []
            for market_key in ('SZ#深证A股', 'SH#上证A股'):
                mid = _resolve_market_id(market_key)
                codes.extend(self._bridge.get_stock_list(mid) or [])
            return codes
        elif spinfo_type == 4:
            if customblockname:
                return self._bridge.get_stock_list_in_sector(customblockname) or []
            return []
        return []

    @staticmethod
    def _codes_to_stock_list(codes) -> List[Dict]:
        result = []
        for tq_code in (codes or []):
            if not tq_code:
                continue
            if '.' in tq_code:
                parts = tq_code.split('.')
                code = parts[0]
                market = parts[1].upper() if len(parts) > 1 else ''
            else:
                code = tq_code
                if code.startswith('6'):
                    market = 'SH'
                elif code.startswith(('0', '3')):
                    market = 'SZ'
                elif code.startswith(('4', '8')):
                    market = 'BJ'
                else:
                    market = 'SZ'
            setcode = SHORT_NAME_TO_MARKET_ID.get(
                {'SZ': 'sz_a', 'SH': 'sh_a', 'BJ': 'bj_a'}.get(market, 'sz_a'),
                0,
            )
            name = code
            result.append({
                'setcode': setcode,
                'code': code,
                'name': name,
            })
        return result

    def get_sector_list(self, list_type=1) -> List[Dict]:
        return self._bridge.get_sector_list(list_type)

    def get_sector_stocks(self, sector_code, block_type=0) -> List[str]:
        return self._bridge.get_stock_list_in_sector(sector_code, block_type=block_type)

    # ------------------------------------------------------------------
    # 公式评估
    # ------------------------------------------------------------------

    def eval_indicator(self, codes, formula_text, period, sorttype=0) -> Dict:
        """评估指标公式。"""
        if isinstance(period, str):
            period_int = map_period(period)
        else:
            period_int = int(period)
        return self._bridge.eval_formula(formula_text, codes, period_int)

    def eval_formula_xg(self, formula_name, formula_arg='', stock_list=None,
                        period='1d', count=0, dividend_type=1,
                        start_time='', end_time='') -> Dict:
        """评估选股公式。返回 {"success": bool, "result": {code: bool}, "selected_codes": [str]}"""
        result = self._bridge.formula_process_mul_xg(
            formula_name=formula_name,
            formula_arg=formula_arg,
            stock_list=stock_list or [],
            period=period,
            count=count,
            dividend_type=dividend_type,
            start_time=start_time,
            end_time=end_time,
        )
        if not result or not isinstance(result, dict):
            return {"success": False, "result": {}, "selected_codes": []}
        if 'success' not in result:
            result['success'] = True
        if 'result' not in result:
            result['result'] = {}
        if 'selected_codes' not in result:
            selected = [c for c, v in result.get('result', {}).items()
                        if v is True or (v is not None and v != 0 and v is not False)]
            result['selected_codes'] = selected
        return result

    def eval_formula_zb(self, formula_name, formula_arg='', stock_list=None,
                        period='1d', count=5, dividend_type=1,
                        return_count=1, return_date=False,
                        xsflag=6, start_time='', end_time='') -> Dict:
        """评估指标公式。

        返回格式::
            {
                "success": bool,
                "result": {code: [float]},           # 最后一条输出线的值数组
                "result_detail": {code: {name: [float]}}  # 完整指标结构
            }
        """
        raw = self._bridge.formula_process_mul_zb(
            formula_name=formula_name,
            formula_arg=formula_arg,
            stock_list=stock_list or [],
            period=period,
            count=count,
            dividend_type=dividend_type,
            return_count=return_count,
            return_date=return_date,
            xsflag=xsflag,
            start_time=start_time,
            end_time=end_time,
        )
        if not raw or not isinstance(raw, dict):
            return {"success": False, "result": {}, "result_detail": {}}

        # 排除非股票代码的条目（如 'ErrorId'）
        _META_KEYS = {'ErrorId', 'ErrMsg', 'error', 'error_msg'}
        result_detail: Dict[str, Dict[str, list]] = {}
        for key, value in raw.items():
            if key in _META_KEYS:
                continue
            if not isinstance(value, dict):
                continue
            result_detail[key] = value

        # 构建 result: 提取最后一条输出线的值数组
        result: Dict[str, list] = {}
        for code, indicators in result_detail.items():
            if not indicators:
                continue
            # 取最后一条输出线（排序后取最后一个 key 对应的值）
            last_key = sorted(indicators.keys())[-1]
            values = indicators[last_key]
            if isinstance(values, list):
                result[code] = values
            else:
                result[code] = [values]

        return {
            "success": True,
            "result": result,
            "result_detail": result_detail,
        }

    # ------------------------------------------------------------------
    # 板块操作
    # ------------------------------------------------------------------

    def send_user_block(self, block_code, stocks, show=True) -> Dict:
        result = self._bridge.send_user_block(block_code, stocks, show=show)
        return result if result else {}

    def create_sector(self, block_code, block_name) -> Dict:
        result = self._bridge.create_sector(block_code, block_name)
        return result if result else {}

    def clear_sector(self, block_code) -> Dict:
        result = self._bridge.clear_sector(block_code)
        return result if result else {}

    # ------------------------------------------------------------------
    # 财务 / 回放 / 重采样
    # ------------------------------------------------------------------

    def get_financial_data(self, codes, fields) -> Dict:
        """获取财务数据，委托给 TqSdkBridge.get_stock_info。"""
        if isinstance(codes, str):
            codes = [codes]
        return self._bridge.get_stock_info(codes)

    def get_replay_data(self, codes, current_time, period='1min') -> Dict[str, List[Dict]]:
        """获取回放数据：取K线后按 current_time 过滤。"""
        if isinstance(codes, str):
            codes = [codes]

        # 将 period 映射为内部格式
        period_int = map_period(period)

        # 获取足够多的K线数据
        raw = self._bridge.get_kline_data(codes, period_int, count=2000)
        if not raw:
            return {}

        converted = self._convert_raw_kline(raw, period_int)

        # 按 current_time 过滤
        result: Dict[str, List[Dict]] = {}
        ct_str = str(current_time).replace('-', '').replace(':', '').replace(' ', '')

        for code, bars in converted.items():
            filtered = []
            for bar in bars:
                bar_time = bar.get('time', '').replace('-', '').replace(':', '').replace(' ', '')
                if bar_time <= ct_str:
                    filtered.append(bar)
            if filtered:
                result[code] = filtered
        return result

    def resample_kline(self, kline_1min, target_period) -> List[Dict]:
        """从1分钟K线重采样到目标周期，使用 pandas resample。"""
        if not kline_1min:
            return []
        try:
            import pandas as pd
        except ImportError:
            logger.debug("pandas not available, cannot resample kline")
            return kline_1min

        df = pd.DataFrame(kline_1min)
        if 'time' not in df.columns:
            return kline_1min

        df['time'] = pd.to_datetime(df['time'], errors='coerce')
        df = df.dropna(subset=['time'])
        if df.empty:
            return kline_1min

        df = df.set_index('time')

        # 确定 resample 频率
        period_map = {
            '1m': '1min', '5m': '5min', '15m': '15min', '30m': '30min',
            '60m': '60min', '1d': '1D', '1w': '1W', '1mon': '1ME',
            '1min': '1min', '5min': '5min', '15min': '15min', '30min': '30min',
            '60min': '60min', 'day': '1D', 'week': '1W', 'month': '1ME',
        }
        freq = period_map.get(target_period, '1D')

        resampled = df.resample(freq).agg({
            'open': 'first',
            'high': 'max',
            'low': 'min',
            'close': 'last',
            'volume': 'sum',
            'amount': 'sum',
        }).dropna(subset=['open'])

        result = []
        for idx, row in resampled.iterrows():
            bar = {
                'time': idx.strftime('%Y-%m-%d %H:%M:%S'),
                'open': float(row['open']),
                'high': float(row['high']),
                'low': float(row['low']),
                'close': float(row['close']),
                'volume': float(row['volume']),
                'amount': float(row['amount']),
            }
            result.append(bar)
        return result


# ===========================================================================
# TqProvider —— TQ 量化数据源提供者（门面）
# ===========================================================================

class TqProvider(DataSourceProvider):
    """TQ 量化数据源提供者，基于 TPythClient.dll。

    直接通过 TPythClient.dll 与通达信内核通信，
    将所有 DataSourceProvider 方法委托给 TqDllProvider 实例。
    """

    def __init__(self, bus: Optional[EventBus] = None, config: Optional[Dict[str, Any]] = None):
        super().__init__(bus=bus, config=config)
        self._bridge = None
        self._init_bridge()

    def _init_bridge(self):
        try:
            dll = TqDllProvider(bus=self._bus)
            if dll.is_ready():
                self._bridge = dll
                logger.info("TqProvider: DLL bridge 就绪 (TPythClient.dll)")
                return
        except Exception as e:
            logger.debug("TqProvider: DLL bridge 初始化失败: %s", e)

        self._bridge = None
        logger.warning("TqProvider: DLL 不可用")

    # ------------------------------------------------------------------
    # 基础状态
    # ------------------------------------------------------------------

    def is_ready(self) -> bool:
        return self._bridge.is_ready() if self._bridge else False

    def get_mode_info(self) -> str:
        return "tq"

    # ------------------------------------------------------------------
    # 行情数据
    # ------------------------------------------------------------------

    def resolve_market(self, markets) -> Dict[str, List[str]]:
        if self._bridge is None:
            return {}
        return self._bridge.resolve_market(markets)

    def get_kline_data(self, codes, period=None, start_date=None, end_date=None, **kwargs) -> Dict:
        if self._bridge is None:
            return {}
        return self._bridge.get_kline_data(codes, period=period,
                                            start_date=start_date, end_date=end_date, **kwargs)

    def get_snapshot(self, codes) -> Dict[str, Dict]:
        if self._bridge is None:
            return {}
        return self._bridge.get_snapshot(codes)

    def get_market_snapshot(self, codes) -> Dict[str, Dict]:
        if self._bridge is None:
            return {}
        return self._bridge.get_market_snapshot(codes)

    # ------------------------------------------------------------------
    # 板块 / 股票列表
    # ------------------------------------------------------------------

    def get_block_members(self, block_code, **kwargs) -> List[str]:
        if self._bridge is None:
            return []
        return self._bridge.get_block_members(block_code, **kwargs)

    def get_stock_list_by_type(self, list_type, **kwargs) -> List[str]:
        if self._bridge is None:
            return []
        return self._bridge.get_stock_list_by_type(list_type, **kwargs)

    def get_stock_list(self, market_id, **kwargs) -> List[str]:
        if self._bridge is None:
            return []
        return self._bridge.get_stock_list(market_id, **kwargs)

    def get_stock_info(self, codes) -> Dict:
        if self._bridge is None:
            return {}
        return self._bridge.get_stock_info(codes)

    def get_sector_list(self, list_type=0, **kwargs) -> List:
        if self._bridge is None:
            return []
        return self._bridge.get_sector_list(list_type=list_type, **kwargs)

    def get_sector_stocks(self, sector_code, **kwargs) -> List:
        if self._bridge is None:
            return []
        return self._bridge.get_sector_stocks(sector_code, **kwargs)

    def get_stock_list_in_sector(self, sector_code, **kwargs) -> List:
        if self._bridge is None:
            return []
        return self._bridge.get_stock_list_in_sector(sector_code, **kwargs)

    # ------------------------------------------------------------------
    # 公式评估
    # ------------------------------------------------------------------

    def eval_indicator(self, formula_text, stock_list=None, **kwargs) -> Dict:
        if self._bridge is None:
            return {}
        return self._bridge.eval_indicator(formula_text, stock_list=stock_list, **kwargs)

    def eval_formula_zb(self, formula_name, formula_arg='', stock_list=None,
                        period='1d', count=5, dividend_type=1,
                        return_count=1, return_date=False,
                        xsflag=6, start_time='', end_time='') -> Dict:
        if self._bridge is None:
            return {"success": False, "result": {}, "result_detail": {}}
        return self._bridge.eval_formula_zb(formula_name, formula_arg=formula_arg,
                                             stock_list=stock_list, period=period,
                                             count=count, dividend_type=dividend_type,
                                             return_count=return_count, return_date=return_date,
                                             xsflag=xsflag, start_time=start_time, end_time=end_time)

    def eval_formula_xg(self, formula_name, formula_arg='', stock_list=None,
                        period='1d', count=0, dividend_type=1,
                        start_time='', end_time='') -> Dict:
        if self._bridge is None:
            return {"success": False, "result": {}, "selected_codes": []}
        return self._bridge.eval_formula_xg(formula_name, formula_arg=formula_arg,
                                             stock_list=stock_list, period=period,
                                             count=count, dividend_type=dividend_type,
                                             start_time=start_time, end_time=end_time)

    # ------------------------------------------------------------------
    # 板块操作
    # ------------------------------------------------------------------

    def send_user_block(self, block_code, stocks, show=True) -> Dict:
        if self._bridge is None:
            return {"success": False}
        return self._bridge.send_user_block(block_code, stocks, show=show)

    def create_sector(self, block_code, block_name) -> Dict:
        if self._bridge is None:
            return {"success": False}
        return self._bridge.create_sector(block_code, block_name)

    def clear_sector(self, block_code) -> Dict:
        if self._bridge is None:
            return {"success": False}
        return self._bridge.clear_sector(block_code)

    # ------------------------------------------------------------------
    # 财务 / 回放 / 重采样
    # ------------------------------------------------------------------

    def get_financial_data(self, codes, fields) -> Dict:
        if self._bridge is None:
            return {}
        return self._bridge.get_financial_data(codes, fields)

    def get_replay_data(self, codes, current_time, period='1min') -> Dict[str, List[Dict]]:
        if self._bridge is None:
            return {}
        return self._bridge.get_replay_data(codes, current_time, period=period)

    def resample_kline(self, kline_data, target_period) -> List[Dict]:
        if self._bridge is None:
            return []
        return self._bridge.resample_kline(kline_data, target_period)


# ===========================================================================
# 模块级 _get_full_mock_provider（兼容历史调用方，原 __init__.py 模块级函数）
# ===========================================================================


def _get_full_mock_provider(bus: Optional[EventBus] = None) -> DataSourceProvider:
    """尝试加载完整的 MockProvider，失败则返回 _StubMockProvider。

    注意：此处不调用 grant_consent() —— mock 同意必须由用户显式通过
    DataSourceContract.grant_explicit_consent() 授权。
    MockProvider.is_ready() 在未授权前返回 False。
    """
    try:
        return MockProvider(bus=bus)
    except Exception:
        return _StubMockProvider(bus=bus)


# ===========================================================================
# 模块公开符号表
# ===========================================================================

__all__ = [
    # 基类与管理器
    'DataSourceProvider',
    'DataSourceManager',
    'ConfigInconsistencyError',
    'DataSourceUnavailableError',
    '_StubMockProvider',
    '_get_full_mock_provider',

    # 公共工具函数
    'decode_formula',
    'decode_sorttype',
    'map_period',
    'normalize_code',
    'to_dzh_code',

    # 公共常量与缓存
    'PERIOD_MAP',
    'SORTTYPE_MAP',
    'KLineDataCache',

    # Provider 实现类
    'MockProvider',
    'DfcfProvider',
    'HQChartProvider',
    'AkShareProvider',
    'LocalFileProvider',
    'TqDllProvider',
    'TqSdkProvider',
    'TqProvider',
    'TqConnector',
    'TqSdkBridge',

    # HQChart 相关
    'PERIOD_ID',
    'IHQDataImpl',
    'FastHQChart',

    # AkShare 异常
    'DataSourceError',

    # TQ 常量
    'DLL_PATH',
    'LIST_TYPE_MAP',
    'MARKET_ID_MAP',
    'SHORT_NAME_TO_MARKET_ID',
    'DZH_TO_SHORT',
    'SHORT_TO_DZH',
    '_PERIOD_INT_TO_STR',
    '_PERIOD_STR_TO_INT',
    'DZH_COL_MAP',
    '_LIST_TYPE_TO_MARKET_IDS',
    '_LIST_TYPE_CATEGORY_MAP',
    '_SECTOR_CODE_PREFIX_CATEGORY',
    '_SECTOR_NAME_KEYWORD_CATEGORY',

    # Mock 惰性 re-export（兼容历史调用方）
    '_MOCK_MARKET_STOCKS',
    '_MOCK_STOCK_NAMES',
]

