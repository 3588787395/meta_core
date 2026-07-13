"""
MockProvider —— 始终就绪的纯 Mock 数据源提供者。

所有方法均使用确定性随机（seed 由 code/formula 决定），
确保相同输入始终产出相同结果，便于测试与回放。
"""

import base64
import hashlib
import json
import logging
import random
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import DataSourceProvider
from ._common import KLineDataCache
from ._common import (
    PERIOD_MAP,
    _format_hold_days,
    _format_timestamp,
    _norm_period,
    decode_formula,
    map_period,
    normalize_code,
    to_dzh_code,
)

logger = logging.getLogger(__name__)

# ===========================================================================
# Mock 数据定义（Task 3: 从 _common.py 移入，仅允许 MockProvider 使用）
# ===========================================================================

_CONFIG_PATH = Path(__file__).resolve().parents[2] / 'config' / 'mock_data.json'

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


def _load_config() -> dict:
    try:
        if _CONFIG_PATH.exists():
            with open(_CONFIG_PATH, 'r', encoding='utf-8') as f:
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


_cfg = _load_config()

# 合并：配置构建的名称作为基础，_FALLBACK_STOCK_NAMES 补充缺失的条目
_mock_names_from_cfg = _build_stock_names_from_config(_cfg)
_MOCK_STOCK_NAMES = dict(_FALLBACK_STOCK_NAMES)
_MOCK_STOCK_NAMES.update(_mock_names_from_cfg)  # cfg 覆盖 fallback

_mock_markets_from_cfg = _build_market_stocks_from_config(_cfg)
_MOCK_MARKET_STOCKS = dict(_FALLBACK_MARKET_STOCKS)
_MOCK_MARKET_STOCKS.update(_mock_markets_from_cfg)

# ── 常量 ──────────────────────────────────────────────────────────────

_PERIOD_INT_TO_STR: Dict[int, str] = {
    0: 'tick', 1: '1m', 2: '5m', 3: '15m', 4: '30m',
    5: '60m', 6: '1d', 7: '1w', 8: '1mon',
}

_DZH_TO_SHORT = {
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

_SHORT_TO_DZH = {v: k for k, v in _DZH_TO_SHORT.items()}

_SHORT_NAME_TO_MARKET_ID = {
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

_MARKET_ID_MAP = {
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
    if market_key in _SHORT_NAME_TO_MARKET_ID:
        return _SHORT_NAME_TO_MARKET_ID[market_key]
    if market_key in _MARKET_ID_MAP:
        return _MARKET_ID_MAP[market_key]
    return 0


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

    def __init__(self):
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
            dzh_key = _SHORT_TO_DZH.get(m, m)
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
        norm_period = _norm_period(period)
        return self._get_kline_batch(codes, norm_period, start_date, end_date)

    def get_snapshot(self, codes) -> Dict[str, Dict]:
        if not codes:
            return {}
        result = {}
        for code in codes:
            # Bug #16 修复：使用确定性哈希代替 hash()，确保跨进程可复现
            seed = int(hashlib.md5(code.encode()).hexdigest(), 16) % 10000
            rng = random.Random(seed)
            base_price = round(rng.uniform(2, 200), 2)
            change_pct = round(rng.uniform(-10, 10), 2)
            pre_close = round(base_price / (1 + change_pct / 100), 2)
            change_amt = round(base_price - pre_close, 2)
            open_p = round(base_price * rng.uniform(0.97, 1.03), 2)
            high = round(max(base_price, open_p) * rng.uniform(1.0, 1.05), 2)
            low = round(min(base_price, open_p) * rng.uniform(0.95, 1.0), 2)
            bid = round(base_price * rng.uniform(0.995, 1.0), 2)
            ask = round(base_price * rng.uniform(1.0, 1.005), 2)
            result[code] = {
                'name': _MOCK_STOCK_NAMES.get(code, code),
                'close': base_price,
                'price': base_price,
                'now': base_price,
                'open': open_p,
                'high': high,
                'low': low,
                'pre_close': pre_close,
                'change_pct': change_pct,
                'change_amt': change_amt,
                'rise': change_pct,
                'bid_price': bid,
                'ask_price': ask,
                'volume': rng.randint(100000, 50000000),
                'amount': round(rng.uniform(1000000, 500000000), 2),
                'turnover_rate': round(rng.uniform(0.1, 8.0), 2),
                'volume_ratio': round(rng.uniform(0.5, 3.0), 2),
                'pe_ratio': round(rng.uniform(5.0, 80.0), 2),
                'ddx_red_days': rng.randint(0, 10),
                'bbd': round(rng.uniform(-5000, 5000), 2),
                'ddx': round(rng.uniform(-5, 5), 2),
            }
        return result

    # ------------------------------------------------------------------
    # 板块 / 股票列表
    # ------------------------------------------------------------------

    def get_block_members(self, block_code) -> List[str]:
        all_codes = ['000001.SZ', '000002.SZ', '000858.SZ', '600000.SH',
                     '600036.SH', '600519.SH', '601318.SH', '002415.SZ',
                     '300750.SZ', '688981.SH']
        seed = int(hashlib.md5(block_code.encode()).hexdigest(), 16) % 10000
        rng = random.Random(seed)
        k = rng.randint(3, len(all_codes))
        return rng.sample(all_codes, k=k)

    def get_stock_list_by_type(self, list_type, customblockname='', **kwargs) -> List[Dict]:
        """按类型获取股票列表。

        支持两种调用方式:
        1. spinfo.type 整数值: 0=全市场, 2=全部A股, 4=自定义板块
        2. 传统 list_type 字符串
        """
        spinfo_type = None
        try:
            lt_int = int(list_type)
            if lt_int in (0, 2, 4):
                spinfo_type = lt_int
        except (ValueError, TypeError):
            pass

        cache_key = f'stock_list_by_type_{list_type}_{customblockname}'
        if cache_key in self._method_cache:
            return self._method_cache[cache_key]

        raw_codes: List[str] = []

        if spinfo_type is not None:
            raw_codes = self._get_stocks_by_spinfo_type(spinfo_type, customblockname)
        else:
            raw_codes = list(_MOCK_STOCK_NAMES.keys())

        result = self._codes_to_stock_list(raw_codes)
        self._method_cache[cache_key] = result
        return result

    def get_sector_list(self, list_type=1) -> List[Dict]:
        cache_key = f'sector_list_{list_type}'
        if cache_key in self._method_cache:
            return self._method_cache[cache_key]
        result = [
            {"code": "880201.SH", "name": "种植业"},
            {"code": "880301.SH", "name": "煤炭开采"},
        ]
        self._method_cache[cache_key] = result
        return result

    def get_sector_stocks(self, sector_code, block_type=0) -> List[str]:
        cache_key = f'sector_stocks_{sector_code}_{block_type}'
        if cache_key in self._method_cache:
            return self._method_cache[cache_key]
        result = ["000001.SZ", "600000.SH"]
        self._method_cache[cache_key] = result
        return result

    # ------------------------------------------------------------------
    # 公式评估
    # ------------------------------------------------------------------

    def eval_indicator(self, codes, formula_text, period, sorttype=0) -> Dict:
        if isinstance(formula_text, bytes):
            formula_text = formula_text.decode('utf-8')
        if formula_text and not any(c in formula_text for c in '()><=,;'):
            try:
                decoded = base64.b64decode(formula_text).decode('utf-8')
                if decoded and any(c in decoded for c in '()><=,;ABCDEFGHIJKLMNOPQRSTUVWXYZ'):
                    formula_text = decoded
            except Exception:
                pass
        result = self._eval_mock_indicator(codes, formula_text, period)
        if sorttype > 0 and 'result' in result:
            result_map = result['result']
            sorted_items = sorted(
                result_map.items(),
                key=lambda x: x[1] if isinstance(x[1], (int, float)) else 0,
                reverse=True,
            )
            top_items = sorted_items[:sorttype]
            result['result'] = dict(top_items)
            result['selected_count'] = len(top_items)
        elif sorttype == 0 and 'result' in result:
            result['selected_count'] = len(result['result'])
        return result

    def eval_formula_xg(self, formula_name, formula_arg='', stock_list=None,
                        period='1d', count=0, dividend_type=1,
                        start_time='', end_time='') -> Dict:
        codes = self._resolve_formula_stock_list(stock_list)
        mock_result = {}
        selected = []
        for code in codes:
            seed = int(hashlib.md5(f'{code}:{formula_name}'.encode()).hexdigest(), 16) % 10000
            rng = random.Random(seed)
            passed = rng.random() < 0.3
            mock_result[code] = passed
            if passed:
                selected.append(code)
        return {"success": True, "result": mock_result, "selected_codes": selected}

    # ── 指标公式输出线定义 ─────────────────────────────────────────────

    _FORMULA_LINES: Dict[str, Dict[str, tuple]] = {
        'MACD': {'DIF': (-2.0, 2.0), 'DEA': (-2.0, 2.0), 'MACD': (-2.0, 2.0)},
        'KDJ':  {'K': (0.0, 100.0), 'D': (0.0, 100.0), 'J': (0.0, 100.0)},
        'RSI':  {'RSI1': (0.0, 100.0), 'RSI2': (0.0, 100.0), 'RSI3': (0.0, 100.0)},
        'BOLL': {'MID': (10.0, 200.0), 'UPPER': (10.0, 200.0), 'LOWER': (10.0, 200.0)},
        'MA':   {'MA5': (10.0, 200.0), 'MA10': (10.0, 200.0), 'MA20': (10.0, 200.0), 'MA60': (10.0, 200.0)},
        'VOL':  {'VOL': (1000.0, 100000.0), 'MAVOL5': (1000.0, 100000.0)},
        'UPNDAY': {'UPN': (0.0, 1.0)},
    }

    def eval_formula_zb(self, formula_name, formula_arg='', stock_list=None,
                        period='1d', count=5, dividend_type=1,
                        return_count=1, return_date=False,
                        xsflag=6, start_time='', end_time='') -> Dict:
        """评估指标公式（Mock 版本）。

        返回格式与 TqSdkProvider.eval_formula_zb 一致::

            {
                "success": bool,
                "result": {code: [float]},              # 最后一条输出线的值数组
                "result_detail": {code: {name: [float]}}  # 完整指标结构
            }
        """
        codes = self._resolve_formula_stock_list(stock_list)
        formula_upper = formula_name.upper().strip()

        # 查找匹配的输出线定义
        lines_def = self._FORMULA_LINES.get(formula_upper)
        if lines_def is None:
            # 尝试模糊匹配：MACD(12,26,9) -> MACD, KDJ(9,3,3) -> KDJ 等
            for key in self._FORMULA_LINES:
                if formula_upper.startswith(key):
                    lines_def = self._FORMULA_LINES[key]
                    break

        result_detail: Dict[str, Dict[str, list]] = {}
        result: Dict[str, list] = {}

        for code in codes:
            seed = int(hashlib.md5(f'{code}:{formula_name}:{period}'.encode()).hexdigest(), 16) % 10000
            rng = random.Random(seed)

            if lines_def is not None:
                # 按输出线定义生成各条线的值
                code_detail: Dict[str, list] = {}
                for line_name, (lo, hi) in lines_def.items():
                    if formula_upper == 'UPNDAY' or (formula_upper.startswith('UPNDAY')):
                        vals = [round(rng.uniform(lo, hi)) for _ in range(return_count or 1)]
                    else:
                        vals = [round(rng.uniform(lo, hi), 4) for _ in range(return_count or 1)]
                    code_detail[line_name] = vals
                result_detail[code] = code_detail

                # result: 取最后一条输出线的值数组
                last_key = sorted(code_detail.keys())[-1]
                result[code] = code_detail[last_key]
            else:
                # 其他公式: 1 条输出线，值 -1.0 到 1.0
                vals = [round(rng.uniform(-1.0, 1.0), 4) for _ in range(return_count or 1)]
                result_detail[code] = {'VAL': vals}
                result[code] = vals

        return {
            "success": True,
            "result": result,
            "result_detail": result_detail,
        }

    # ------------------------------------------------------------------
    # 板块操作
    # ------------------------------------------------------------------

    def send_user_block(self, block_code, stocks, show=True) -> Dict:
        return {"success": True, "block_code": block_code, "count": len(stocks)}

    def create_sector(self, block_code, block_name) -> Dict:
        return {"success": True, "block_code": block_code}

    def clear_sector(self, block_code) -> Dict:
        return {"success": True, "block_code": block_code}

    # ------------------------------------------------------------------
    # 财务 / 回放 / 重采样
    # ------------------------------------------------------------------

    def get_financial_data(self, codes, fields) -> Dict:
        return {
            code: {f: round(random.Random(int(hashlib.md5(f'{code}:{f}'.encode()).hexdigest(), 16) % 10000).uniform(0, 100), 2) for f in fields}
            for code in codes
        }

    def get_replay_data(self, codes, current_time, period='1min') -> Dict[str, List[Dict]]:
        ct = current_time.strip()
        if len(ct) == 10:
            end_dt = datetime.strptime(ct, '%Y-%m-%d')
            start_dt = end_dt - timedelta(days=120)
        else:
            end_dt = datetime.strptime(ct, '%Y-%m-%d %H:%M:%S')
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
            if target_period in ('5min', '5m'):
                rule = '5min'
            elif target_period in ('day', '1d'):
                rule = 'D'
            elif target_period in ('15min', '15m'):
                rule = '15min'
            elif target_period in ('30min', '30m'):
                rule = '30min'
            elif target_period in ('60min', '60m'):
                rule = '60min'
            else:
                rule = '5min'
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
    # 表格数据
    # ------------------------------------------------------------------

    def get_stock_table_data(self, codes, col_ids, stk_info=None, hold_sec=0) -> Dict:
        columns = []
        for cid in col_ids:
            col_def = DZH_COL_MAP.get(cid)
            if col_def:
                columns.append({
                    'id': cid,
                    'name': col_def['name'],
                    'key': col_def['key'],
                    'type': col_def['type'],
                })
        if not codes:
            return {'data': [], 'columns': columns}
        stk_map = {}
        if stk_info:
            for s in stk_info:
                label = s.get('label', '')
                stk_map[label] = s
        snapshots = self.get_snapshot(codes)
        rows = []
        for i, code in enumerate(codes):
            snap = snapshots.get(code, {})
            stk = stk_map.get(code, {})
            row = self._build_table_row(i, code, snap, stk, hold_sec, col_ids)
            rows.append(row)
        return {'data': rows, 'columns': columns}

    # ══════════════════════════════════════════════════════════════════
    #  内部辅助方法
    # ══════════════════════════════════════════════════════════════════

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

    # ── K线生成 ────────────────────────────────────────────────────────

    def _generate_mock_kline(self, codes, period, start_date, end_date) -> Dict[str, List[Dict]]:
        norm_period = _norm_period(period)
        is_intraday = norm_period in ('1m', '5m', '15m', '30m', '60m')

        start = datetime.strptime(start_date, '%Y-%m-%d')
        end = datetime.strptime(end_date, '%Y-%m-%d')

        timestamps: List[datetime] = []
        max_bars = 5000

        if is_intraday:
            delta_map = {
                '1m': timedelta(minutes=1),
                '5m': timedelta(minutes=5),
                '15m': timedelta(minutes=15),
                '30m': timedelta(minutes=30),
                '60m': timedelta(minutes=60),
            }
            delta = delta_map.get(norm_period, timedelta(minutes=5))
            current = start.replace(hour=9, minute=30, second=0)
            end_dt = end.replace(hour=15, minute=0, second=0)
            while current <= end_dt and len(timestamps) < max_bars:
                h, m = current.hour, current.minute
                t = h * 60 + m
                in_morning = 570 <= t <= 690
                in_afternoon = 780 <= t <= 900
                if in_morning or in_afternoon:
                    timestamps.append(current)
                current += delta
        else:
            delta = timedelta(days=1)
            current = start
            end_dt = end.replace(hour=23, minute=59, second=59)
            while current <= end_dt and len(timestamps) < max_bars:
                if current.weekday() < 5:
                    timestamps.append(current)
                current += delta

        result = {}
        for code in codes:
            # Bug #16 修复：使用确定性哈希代替 hash()，确保跨进程可复现
            seed = int(hashlib.md5(code.encode()).hexdigest(), 16) % 10000
            rng = random.Random(seed)
            price = round(rng.uniform(5.0, 50.0), 2)
            bars = []
            for ts in timestamps:
                change_pct = rng.uniform(-0.02, 0.02)
                close = round(price * (1 + change_pct), 2)
                close = max(0.01, close)
                open_p = round(price * (1 + rng.uniform(-0.01, 0.01)), 2)
                open_p = max(0.01, open_p)
                high = round(max(open_p, close) * (1 + rng.uniform(0, 0.01)), 2)
                low = round(min(open_p, close) * (1 - rng.uniform(0, 0.01)), 2)
                low = max(0.01, low)
                abs_change = abs(close - price) / price if price > 0 else 0
                vol_mult = 1.0 + abs_change * 20
                base_vol = rng.randint(500000, 10000000)
                vol = int(base_vol * vol_mult)
                amt = round(vol * (open_p + close) / 2, 2)
                bars.append({
                    'open': open_p,
                    'high': high,
                    'low': low,
                    'close': close,
                    'volume': vol,
                    'amount': amt,
                    'time': ts.strftime('%Y-%m-%d %H:%M:%S'),
                })
                price = close
            result[code] = bars
        return result

    def _get_kline_data_legacy(self, codes, period, count) -> Dict:
        """旧签名 K线数据: {code: {'Date': [...], 'Open': [...], ...}}"""
        result = {}
        for code in codes:
            # Bug #16 修复：使用确定性哈希代替 hash()，确保跨进程可复现
            seed = int(hashlib.md5(code.encode()).hexdigest(), 16) % 10000
            rng = random.Random(seed)
            bars = []
            price = round(rng.uniform(5.0, 200.0), 2)
            for i in range(count):
                open_p = round(price * (1 + rng.uniform(-0.02, 0.02)), 2)
                open_p = max(0.01, open_p)
                change_pct = rng.uniform(-0.03, 0.03)
                close = round(open_p * (1 + change_pct), 2)
                close = max(0.01, close)
                high = round(max(open_p, close) * (1 + rng.uniform(0, 0.02)), 2)
                low = round(min(open_p, close) * (1 - rng.uniform(0, 0.02)), 2)
                low = max(0.01, low)
                volume = rng.randint(10000, 10000000)
                bars.append((open_p, high, low, close, volume))
                price = close
            result[code] = {
                'Date': [f'2024{i + 1:02d}01' for i in range(count)],
                'Open': [b[0] for b in bars],
                'High': [b[1] for b in bars],
                'Low': [b[2] for b in bars],
                'Close': [b[3] for b in bars],
                'Volume': [b[4] for b in bars],
            }
        return result

    def _get_kline_batch(self, codes, period, start_date, end_date) -> Dict[str, List[Dict]]:
        result = {}
        uncached = []
        for code in codes:
            if self._kline_cache.has(code, period):
                result[code] = self._kline_cache.get(code, period)
            else:
                uncached.append(code)

        if not uncached:
            return result

        new_data = self._generate_mock_kline(uncached, period, start_date, end_date)

        for code, bars in new_data.items():
            self._kline_cache.put(code, period, bars)
            result[code] = bars

        return result

    # ── 指标计算 ───────────────────────────────────────────────────────

    def _eval_mock_indicator(self, codes, formula_text, period) -> Dict:
        period_str = _PERIOD_INT_TO_STR.get(period, '1d')
        end_dt = datetime.now()
        if period_str in ('1d', '1w', '1mon'):
            start_dt = end_dt - timedelta(days=60)
        else:
            start_dt = end_dt - timedelta(days=5)
        start_date = start_dt.strftime('%Y-%m-%d')
        end_date = end_dt.strftime('%Y-%m-%d')
        klines_map = self.get_kline_data(codes, period_str, start_date, end_date)
        formula_upper = formula_text.upper().strip()
        result_map = {}
        for code in codes:
            klines = klines_map.get(code, [])
            val = self._calc_formula_value(formula_upper, code, klines)
            result_map[code] = val
        return {
            'result': result_map,
            'inditype': 0,
        }

    def _calc_formula_value(self, formula, code, klines) -> Any:
        if not klines:
            seed = int(hashlib.md5(f'{code}:{formula}'.encode()).hexdigest(), 16) % 10000
            rng = random.Random(seed)
            return round(rng.uniform(-10, 10), 2)
        closes = [float(b.get('close', 0)) for b in klines]
        opens = [float(b.get('open', 0)) for b in klines]
        highs = [float(b.get('high', 0)) for b in klines]
        lows = [float(b.get('low', 0)) for b in klines]
        last_close = closes[-1] if closes else 0
        last_open = opens[-1] if opens else 0
        last_high = highs[-1] if highs else 0
        last_low = lows[-1] if lows else 0
        if formula == 'KDJ':
            return self._mock_kdj(highs, lows, closes)
        elif formula == 'MACD':
            return self._mock_macd(closes)
        elif formula == 'RSI':
            return self._mock_rsi(closes)
        elif formula.startswith('MA('):
            return self._mock_ma_formula(formula, closes)
        elif '>' in formula or '<' in formula or '=' in formula:
            return self._eval_comparison(formula, last_open, last_high, last_low, last_close, closes, highs, lows)
        seed = int(hashlib.md5(f'{code}:{formula}'.encode()).hexdigest(), 16) % 10000
        rng = random.Random(seed)
        return round(rng.uniform(-10, 10), 2)

    @staticmethod
    def _mock_kdj(highs, lows, closes, n=9) -> Dict[str, float]:
        if len(closes) < n:
            k = d = j = 50.0
        else:
            c = closes[-1]
            h_n = max(highs[-n:])
            l_n = min(lows[-n:])
            if h_n == l_n:
                rsv = 50.0
            else:
                rsv = (c - l_n) / (h_n - l_n) * 100
            k = round(rsv, 2)
            d = round(rsv, 2)
            j = round(3 * k - 2 * d, 2)
        return {'K': k, 'D': d, 'J': j}

    @staticmethod
    def _mock_macd(closes, fast=12, slow=26, signal=9) -> Dict[str, float]:
        if len(closes) < slow:
            return {'DIF': 0.0, 'DEA': 0.0, 'BAR': 0.0}
        # 计算 EMA_fast 和 EMA_slow 序列
        alpha_fast = 2 / (fast + 1)
        alpha_slow = 2 / (slow + 1)
        ema_fast = sum(closes[:fast]) / fast
        ema_slow = sum(closes[:slow]) / slow
        dif_list = []
        # 先用 fast 期数据初始化 ema_fast
        for i in range(fast, len(closes)):
            ema_fast = ema_fast * (1 - alpha_fast) + closes[i] * alpha_fast
            if i >= slow - 1:
                ema_slow = ema_slow * (1 - alpha_slow) + closes[i] * alpha_slow
                dif_list.append(ema_fast - ema_slow)
        if not dif_list:
            return {'DIF': 0.0, 'DEA': 0.0, 'BAR': 0.0}
        # DEA = EMA of DIF sequence
        alpha_signal = 2 / (signal + 1)
        dea = dif_list[0]
        for d in dif_list[1:]:
            dea = dea * (1 - alpha_signal) + d * alpha_signal
        dif = dif_list[-1]
        bar = 2 * (dif - dea)
        return {'DIF': round(dif, 4), 'DEA': round(dea, 4), 'BAR': round(bar, 4)}

    @staticmethod
    def _mock_rsi(closes, n=14) -> float:
        if len(closes) < n + 1:
            return 50.0
        gains = []
        losses = []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0))
            losses.append(max(-diff, 0))
        avg_gain = sum(gains[-n:]) / n
        avg_loss = sum(losses[-n:]) / n
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return round(100 - (100 / (1 + rs)), 2)

    @staticmethod
    def _mock_ma(values, n) -> float:
        if len(values) < n:
            return round(sum(values) / len(values), 2) if values else 0.0
        return round(sum(values[-n:]) / n, 2)

    @staticmethod
    def _mock_ma_formula(formula, closes) -> float:
        try:
            arg = formula.replace('MA(', '').replace(')', '').replace('CLOSE', '').replace(',', '').strip()
            n = int(arg) if arg else 5
        except Exception:
            n = 5
        return MockProvider._mock_ma(closes, n)

    @staticmethod
    def _eval_comparison(formula, last_open, last_high, last_low, last_close, closes, highs, lows) -> Any:
        formula = formula.replace('CLOSE', str(last_close)).replace('OPEN', str(last_open)).replace('HIGH', str(last_high)).replace('LOW', str(last_low))
        formula = formula.replace('C', str(last_close)).replace('O', str(last_open)).replace('H', str(last_high)).replace('L', str(last_low))
        if 'MA(' in formula:
            try:
                start = formula.index('MA(')
                end = formula.index(')', start) + 1
                ma_expr = formula[start:end]
                inner = ma_expr.replace('MA(', '').replace(')', '').strip()
                parts = [p.strip() for p in inner.split(',')]
                if len(parts) >= 2:
                    src = parts[0].strip()
                    n = int(parts[1])
                    if src in ('CLOSE', 'C'):
                        ma_val = MockProvider._mock_ma(closes, n)
                    elif src in ('HIGH', 'H'):
                        ma_val = MockProvider._mock_ma(highs, n)
                    elif src in ('LOW', 'L'):
                        ma_val = MockProvider._mock_ma(lows, n)
                    else:
                        ma_val = MockProvider._mock_ma(closes, n)
                    formula = formula.replace(ma_expr, str(ma_val))
            except Exception:
                pass
        # ── 安全公式评估 ──────────────────────────────────────────
        _DANGEROUS_PATTERNS = (
            '__import__', 'exec', 'eval', 'open', 'compile',
            'os.', 'sys.', 'subprocess', 'shutil', 'globals',
            'locals', 'vars', 'dir', 'getattr', 'setattr',
            'delattr', '__builtins__', '__class__', '__subclasses__',
            'breakpoint', 'input', 'memoryview',
        )
        formula_lower = formula.lower()
        for pat in _DANGEROUS_PATTERNS:
            if pat.lower() in formula_lower:
                return False

        _safe_namespace = {
            'abs': abs, 'round': round, 'min': min, 'max': max,
            'int': int, 'float': float, 'pow': pow, 'sum': sum,
            'len': len, 'bool': bool, 'True': True, 'False': False,
        }
        try:
            val = eval(formula, {"__builtins__": {}}, _safe_namespace)
            if isinstance(val, bool):
                return val
            return round(float(val), 2)
        except Exception:
            return False

    # ── 股票列表辅助 ──────────────────────────────────────────────────

    def _get_stocks_by_spinfo_type(self, spinfo_type, customblockname) -> List[str]:
        if spinfo_type == 0:
            codes = []
            for market_key in ('SZ#深证A股', 'SH#上证A股', 'BJ#北证A股'):
                codes.extend(_MOCK_MARKET_STOCKS.get(market_key, []))
            return codes
        elif spinfo_type == 2:
            codes = []
            for market_key in ('SZ#深证A股', 'SH#上证A股'):
                codes.extend(_MOCK_MARKET_STOCKS.get(market_key, []))
            return codes
        elif spinfo_type == 4:
            if customblockname:
                all_codes = list(_MOCK_STOCK_NAMES.keys())
                seed = int(hashlib.md5(customblockname.encode()).hexdigest(), 16) % 10000
                rng = random.Random(seed)
                k = min(rng.randint(3, 8), len(all_codes))
                return rng.sample(all_codes, k=k)
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

            setcode = _SHORT_NAME_TO_MARKET_ID.get(
                {'SZ': 'sz_a', 'SH': 'sh_a', 'BJ': 'bj_a'}.get(market, 'sz_a'),
                0,
            )
            name = _MOCK_STOCK_NAMES.get(tq_code, f'股票{code}')
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
            return list(_MOCK_STOCK_NAMES.keys())
        return [
            f"{s['code']}.{('SZ' if s['setcode'] == 0 else 'SH' if s['setcode'] == 1 else 'BJ')}"
            for s in stock_dicts
        ]

    # ── 表格行构建 ─────────────────────────────────────────────────────

    def _build_table_row(self, idx, code, snap, stk, hold_sec, col_ids) -> Dict:
        enter_price = float(stk.get('p', 0)) if stk.get('p') else 0
        enter_time_ts = int(stk.get('t', 0)) if stk.get('t') else 0
        current_price = float(snap.get('close', snap.get('price', 0)))
        pre_close = float(snap.get('pre_close', current_price))
        change_pct = ((current_price - pre_close) / pre_close * 100) if pre_close else 0
        change_amt = current_price - pre_close
        profit_pct = ((current_price - enter_price) / enter_price * 100) if enter_price else 0
        max_profit_pct = profit_pct * 1.5
        volume = int(snap.get('volume', 0))
        turnover_rate = float(snap.get('turnover_rate', 0))

        if enter_time_ts > 0:
            enter_time_str = datetime.fromtimestamp(enter_time_ts).strftime('%Y-%m-%d %H:%M')
        else:
            enter_time_str = ''

        hold_days = 0
        if hold_sec > 0 and enter_time_ts > 0:
            hold_days = max(1, int((datetime.now().timestamp() - enter_time_ts) / 86400))

        dzh_code = to_dzh_code(code)
        name = snap.get('name', '') or _MOCK_STOCK_NAMES.get(code, code.split('.')[0] if '.' in code else code)

        row: Dict[str, Any] = {}
        for cid in col_ids:
            col_def = DZH_COL_MAP.get(cid)
            if not col_def:
                continue
            key = col_def['key']
            if cid == 2:
                row[key] = dzh_code
            elif cid == -1:
                row[key] = name
            elif cid == -2:
                row[key] = round(current_price, 2)
            elif cid == -3:
                row[key] = round(change_pct, 2)
            elif cid == -5:
                row[key] = round(change_amt, 2)
            elif cid == -6:
                row[key] = volume
            elif cid == 1:
                row[key] = idx + 1
            elif cid == 7:
                row[key] = enter_time_str
            elif cid == 8:
                row[key] = round(current_price, 2)
            elif cid == 10:
                row[key] = round(profit_pct, 2)
            elif cid == 14:
                row[key] = round(enter_price, 2) if enter_price else round(current_price, 2)
            elif cid == 17:
                row[key] = round(max_profit_pct, 2)
            elif cid == 24:
                row[key] = round(turnover_rate, 2)
            elif cid == 45:
                row[key] = hold_days
            elif cid == 101:
                row[key] = int(snap.get('ddx_red_days', 0))
            elif cid == 108:
                row[key] = round(float(snap.get('volume_ratio', 0)), 2)
            elif cid == 251:
                row[key] = round(float(snap.get('huge_buy', 0)), 2)
            elif cid == 285:
                row[key] = round(float(snap.get('big_buy', 0)), 2)
            elif cid == 287:
                row[key] = round(float(snap.get('bbd', 0)), 2)
            elif cid == 401:
                row[key] = round(float(snap.get('ddx', 0)), 2)
            else:
                row[key] = '-'

        row['latest_price'] = round(current_price, 2)
        row['enter_time'] = _format_timestamp(enter_time_ts)
        row['enter_price'] = round(enter_price, 2) if enter_price else '-'
        row['profit_pct'] = round(profit_pct, 2) if enter_price else '-'
        row['max_profit'] = round(profit_pct * 1.2, 2) if enter_price else '-'
        row['hold_days'] = _format_hold_days(hold_sec)
        return row
