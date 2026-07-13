"""
TQ 数据源提供者集合。

合并自 tq_provider.py、tq_dll_provider.py、tq_sdk_provider.py。

包含三个 TQ 相关的 DataSourceProvider 实现：
- TqDllProvider: 基于 TPythClient.dll 的数据源提供者
- TqSdkProvider: 基于 TQ SDK 的数据源提供者
- TqProvider: 委托给 TqDllProvider 的门面提供者
"""

import ctypes
import json
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from . import DataSourceProvider
from ._common import KLineDataCache
from ._common import (
    PERIOD_MAP,
    _norm_period,
    decode_formula,
    map_period,
    normalize_code,
    to_dzh_code,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 市场映射（从 tq_adapter.py 提取）
# ---------------------------------------------------------------------------

DLL_PATH = Path(__file__).resolve().parents[3] / 'TPythClient.dll'

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


def _resolve_market_id(market_key: str) -> int:
    """将市场名称（大智慧格式或短名）解析为 market_id。"""
    if market_key in SHORT_NAME_TO_MARKET_ID:
        return SHORT_NAME_TO_MARKET_ID[market_key]
    if market_key in MARKET_ID_MAP:
        return MARKET_ID_MAP[market_key]
    return 0


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

    def __init__(self):
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
            user_dir = str(Path(__file__).resolve().parents[2] / 'user')
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

    def __init__(self):
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

    def __init__(self):
        self._bridge = None
        self._init_bridge()

    def _init_bridge(self):
        try:
            dll = TqDllProvider()
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
