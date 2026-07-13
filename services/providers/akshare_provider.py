"""
AkShare 数据源提供者。

基于 AKShare 开源库获取 A 股行情、板块、财务等数据，
并将结果转换为统一内部格式。
"""

import asyncio
import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from functools import wraps

from . import DataSourceProvider
from ._common import KLineDataCache
from ._common import _norm_period, normalize_code, to_dzh_code

logger = logging.getLogger(__name__)

# ── 请求限频控制 ───────────────────────────────────────────────────────

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


def _rate_limit(func):
    """限频装饰器：自动应用限频和重试逻辑。"""
    @wraps(func)
    async def wrapper(self, *args, **kwargs):
        if not hasattr(self, '_rate_limiter'):
            self._rate_limiter = _RateLimiter(min_interval=1.0, max_retries=3)

        # 对于同步的 AKShare 调用，在线程池中执行
        loop = asyncio.get_event_loop()
        return await self._rate_limiter.execute_with_retry(
            lambda: func(self, *args, **kwargs)
        )
    return wrapper

_CONFIG_CACHE: Dict[str, Any] = {}


def _load_config(name: str) -> Dict[str, Any]:
    """加载 meta_core/config 下的 JSON 配置表（带缓存）。"""
    if name in _CONFIG_CACHE:
        return _CONFIG_CACHE[name]
    cfg_path = Path(__file__).resolve().parent.parent.parent / "config" / name
    try:
        with open(cfg_path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.warning("加载配置 %s 失败: %s", name, e)
        data = {}
    _CONFIG_CACHE[name] = data
    return data


class AkShareProvider(DataSourceProvider):
    """基于 AKShare 的数据源提供者。"""

    def __init__(self):
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
        """契约探测（Task 6）：探测 AkShare 模块是否可用。

        Returns:
            {"ready": bool, "provider": "akshare", "error"?: str}
        """
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
        prefixes = _load_config("market_classifications.json").get("exchange_prefixes", ['SH', 'SZ', 'BJ'])
        if code[:2].upper() in prefixes:
            return code[2:]
        return code

    @staticmethod
    def _ak_code_to_tq(code: str) -> str:
        """将 AKShare 的纯数字代码转换为 TQ 格式 (如 600000 -> 600000.SH)。"""
        code = str(code).strip()
        if not code:
            return code
        for rule in _load_config("market_classifications.json").get("code_prefix_rules", []):
            prefix = rule.get("prefix", "")
            market = rule.get("market", "")
            if prefix and market and code.startswith(prefix):
                return f"{code}.{market}"
        return code

    @staticmethod
    def _normalize_time(raw_time) -> str:
        """将 AKShare 返回的日期/时间标准化为 'YYYY-MM-DD HH:MM:SS' 格式。

        AKShare 的日期列可能是:
        - datetime.date 对象 (如 2024-01-01)
        - 字符串 '2024-01-01' 或 '20240101'
        - datetime 对象
        - pandas Timestamp
        """
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

        mc_cfg = _load_config("market_classifications.json")
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
        routes = _load_config("data_source_routes.json")
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
                    'bid_price': round(now_price * 0.999, 2),
                    'ask_price': round(now_price * 1.001, 2),
                }
                result[tq_code] = snapshot

        except Exception as e:
            logger.warning("获取快照数据失败: %s", e)

        return result

    # ------------------------------------------------------------------
    # 板块 / 股票列表
    # ------------------------------------------------------------------

    def get_block_members(self, block_code) -> List[str]:
        """获取板块成员代码列表。

        尝试通过 get_sector_stocks 获取，失败则返回空列表。
        """
        if not self._ready:
            return []
        try:
            return self.get_sector_stocks(block_code, block_type=0)
        except Exception as e:
            logger.warning("获取板块 %s 成员失败: %s", block_code, e)
            return []

    def get_sector_list(self, list_type=1) -> List[Dict]:
        """获取板块列表。

        list_type: 1=行业板块, 2=概念板块
        """
        if not self._ready:
            return []

        routes = _load_config("data_source_routes.json")
        api_map = routes.get("provider_routes", {}).get("akshare", {}).get("sector_list_apis", {})
        method_name = api_map.get(str(list_type), "stock_board_industry_name_em")
        method = getattr(self._ak, method_name, None)
        if method is None:
            logger.warning("AKShare 未找到板块列表方法: %s", method_name)
            return []

        try:
            df = method()

            if df is None or df.empty:
                return []

            sectors = []
            for _, row in df.iterrows():
                sectors.append({
                    'code': str(row.get('板块代码', '')),
                    'name': str(row.get('板块名称', '')),
                })
            return sectors

        except Exception as e:
            logger.warning("获取板块列表失败 (list_type=%s): %s", list_type, e)
            return []

    def get_sector_stocks(self, sector_code, block_type=0) -> List[str]:
        """获取板块成分股代码列表。

        block_type: 0=行业, 1=概念
        """
        if not self._ready:
            return []

        routes = _load_config("data_source_routes.json")
        api_map = routes.get("provider_routes", {}).get("akshare", {}).get("sector_stocks_apis", {})
        method_name = api_map.get(str(block_type), "stock_board_industry_cons_em")
        method = getattr(self._ak, method_name, None)
        if method is None:
            logger.warning("AKShare 未找到板块成分股方法: %s", method_name)
            return []

        # sector_code 可能是板块名称或板块代码，AKShare 需要 symbol（板块名称）
        sector_name = str(sector_code)

        # 如果传入的是纯数字代码，尝试通过 get_sector_list 查找名称
        if sector_name.isdigit():
            list_type_map = routes.get("provider_routes", {}).get("akshare", {}).get("block_type_to_list_type", {})
            list_type = list_type_map.get(str(block_type), block_type + 1)
            sectors = self.get_sector_list(list_type=list_type)
            for s in sectors:
                if s.get('code') == sector_name:
                    sector_name = s.get('name', sector_name)
                    break

        try:
            df = method(symbol=sector_name)

            if df is None or df.empty:
                return []

            codes = []
            for _, row in df.iterrows():
                raw_code = str(row.get('代码', ''))
                tq_code = self._ak_code_to_tq(raw_code)
                codes.append(tq_code)
            return codes

        except Exception as e:
            logger.warning("获取板块 %s 成分股失败: %s", sector_name, e)
            return []

    def get_stock_list_by_type(self, list_type, customblockname='', **kwargs) -> List[Dict]:
        """按类型获取股票列表。

        支持两种调用方式:
        1. spinfo.type 整数值: 0=全市场, 2=全部A股, 4=自定义板块
        2. 传统 list_type 字符串

        返回格式: [{'setcode': int, 'code': str, 'name': str}, ...]
        setcode: 0=深圳, 1=上海, 2=北交
        code: 纯数字（不含 .SH/.SZ 后缀），与 MockProvider 格式一致
        """
        if not self._ready:
            return []

        # 解析 spinfo_type
        # HARDCODED: 不可剥离，理由：0/2/4 是 DZH spinfo.type 协议常量，属于协议常量
        spinfo_type = None
        try:
            lt_int = int(list_type)
            if lt_int in (0, 2, 4):
                spinfo_type = lt_int
        except (ValueError, TypeError):
            pass

        # HARDCODED: 不可剥离，理由：spinfo_type==4 是 DZH 自定义板块类型码常量，属于协议常量
        if spinfo_type == 4:
            if customblockname:
                # 尝试通过板块名称获取成分股
                try:
                    codes = self.get_sector_stocks(customblockname, block_type=0)
                    if codes:
                        stocks = []
                        for tq_code in codes:
                            parts = tq_code.split('.')
                            raw_code = parts[0]
                            setcode = self._get_setcode_by_code(raw_code)
                            stocks.append({
                                'setcode': setcode,
                                'code': raw_code,
                                'name': '',
                            })
                        return stocks
                except Exception:
                    pass
            return []

        # list_type=0/2 或传统字符串：返回全 A 股
        df = self._get_all_a_spot()
        if df is None or df.empty:
            return []

        stocks = []
        try:
            for _, row in df.iterrows():
                raw_code = str(row.get('代码', ''))
                name = str(row.get('名称', ''))

                setcode = self._get_setcode_by_code(raw_code)
                stocks.append({
                    'setcode': setcode,
                    'code': raw_code,
                    'name': name,
                })
            return stocks

        except Exception as e:
            logger.warning("获取股票列表失败: %s", e)
            return []

    # ------------------------------------------------------------------
    # 公式评估（AKShare 不支持公式引擎，返回标准格式表明不支持）
    # ------------------------------------------------------------------

    def eval_indicator(self, codes, formula_text, period, sorttype=0) -> Dict:
        """AKShare 不支持公式评估，返回空结果的标准格式。"""
        return {'result': {}, 'inditype': 0}

    def eval_formula_xg(self, formula_name, formula_arg='', stock_list=None,
                        period='1d', count=0, dividend_type=1,
                        start_time='', end_time='') -> Dict:
        """AKShare 不支持选股公式，返回标准格式表明失败。"""
        return {"success": False, "result": {}, "selected_codes": []}

    def eval_formula_zb(self, formula_name, formula_arg='', stock_list=None,
                        period='1d', count=5, dividend_type=1,
                        return_count=1, return_date=False,
                        xsflag=6, start_time='', end_time='') -> Dict:
        """AKShare 不支持指标公式，返回标准格式表明失败。"""
        return {"success": False, "result": {}}

    # ------------------------------------------------------------------
    # 板块操作（AKShare 不支持写操作，返回标准格式表明不支持）
    # ------------------------------------------------------------------

    def send_user_block(self, block_code, stocks, show=True) -> Dict:
        return {"success": False, "message": "akshare does not support block write"}

    def create_sector(self, block_code, block_name) -> Dict:
        return {"success": False, "message": "akshare does not support sector creation"}

    def clear_sector(self, block_code) -> Dict:
        return {"success": False, "message": "akshare does not support sector clear"}

    # ------------------------------------------------------------------
    # 财务数据
    # ------------------------------------------------------------------

    def get_financial_data(self, codes, fields) -> Dict:
        """获取财务数据。"""
        if not self._ready:
            return {}

        result = {}
        code_list = codes if isinstance(codes, list) else [codes]
        field_list = fields if isinstance(fields, list) else [fields]

        for code in code_list:
            symbol = self._code_to_ak_symbol(code)
            try:
                df = self._ak.stock_financial_analysis_indicator(symbol=symbol)
                if df is None or df.empty:
                    result[code] = {}
                    continue

                # 取最新一期数据
                latest = df.iloc[0]
                data = {}
                for field in field_list:
                    # 尝试直接匹配列名
                    if field in latest.index:
                        data[field] = latest[field]
                    else:
                        data[field] = None
                result[code] = data

            except Exception as e:
                logger.warning("获取 %s 财务数据失败: %s", code, e)
                result[code] = {}

        return result

    # ------------------------------------------------------------------
    # 回放 / 重采样
    # ------------------------------------------------------------------

    def get_replay_data(self, codes, current_time, period='1min') -> Dict[str, List[Dict]]:
        """获取回放数据，基于 get_kline_data 并按 current_time 过滤。"""
        kline_data = self.get_kline_data(codes, period=period)
        if not kline_data:
            return {}

        result = {}
        for code, bars in kline_data.items():
            if not bars:
                result[code] = []
                continue

            try:
                # current_time 格式可能是 "YYYY-MM-DD HH:MM" 或 "YYYYMMDDHHMM"
                ct = str(current_time)
                # HARDCODED: 不可剥离，理由：12 位纯数字是 YYYYMMDDHHMM 固定格式解析，非业务规则
                if len(ct) == 12 and ct.isdigit():
                    ct = f"{ct[:4]}-{ct[4:6]}-{ct[6:8]} {ct[8:10]}:{ct[10:12]}"

                filtered = [
                    bar for bar in bars
                    if str(bar.get('time', '')) <= ct
                ]
                result[code] = filtered
            except Exception as e:
                logger.warning("回放数据过滤失败 %s: %s", code, e)
                result[code] = bars

        return result

    def resample_kline(self, kline_1min, target_period) -> List[Dict]:
        """从1分钟K线重采样到目标周期。"""
        if not kline_1min:
            return []

        try:
            import pandas as pd

            df = pd.DataFrame(kline_1min)
            if 'time' not in df.columns:
                return []

            df['time'] = pd.to_datetime(df['time'])
            df = df.set_index('time')

            # 目标周期映射到 pandas resample 频率
            # HARDCODED: 不可剥离，理由：目标周期到 pandas resample 频率的映射是库 API 固定映射
            period_map = {
                '5m': '5min', '5min': '5min',
                '15m': '15min', '15min': '15min',
                '30m': '30min', '30min': '30min',
                '60m': '60min', '60min': '60min',
                '1d': '1D', 'day': '1D', '1D': '1D',
            }
            freq = period_map.get(target_period, target_period)

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
                result.append({
                    'open': float(row['open']),
                    'high': float(row['high']),
                    'low': float(row['low']),
                    'close': float(row['close']),
                    'volume': float(row['volume']),
                    'amount': float(row['amount']),
                    'time': idx.strftime('%Y-%m-%d %H:%M:%S'),
                })
            return result

        except Exception as e:
            logger.warning("K线重采样失败: %s", e)
            return []

    # ==================================================================
    # 扩展数据源支持（type 1/2/5/6/7 及多源分类树）
    # ==================================================================

    async def get_hs300_cs500_stocks(self) -> List[Dict]:
        """
        获取沪深300+中证500成分股并集（约800只）

        数据来源：
        - ak.index_stock_cons_hs300() — 沪深300成分股
        - ak.index_stock_cons_cs500() — 中证500成分股

        Returns:
            股票列表 [{setcode, code, name, weight}, ...]（去重后约800只）

        Raises:
            DataSourceError: 当两个数据源都不可用时
        """
        if not self._ready:
            raise DataSourceError("AkShareProvider 未就绪")

        result = {}
        errors = []

        # 获取沪深300成分股
        try:
            df_hs300 = await self._rate_limiter.execute_with_retry(
                lambda: self._ak.index_stock_cons_hs300()
            )
            if df_hs300 is not None and not df_hs300.empty:
                for _, row in df_hs300.iterrows():
                    code = str(row.get('股票代码', ''))
                    if code:
                        result[code] = {
                            'setcode': self._get_setcode_by_code(code),
                            'code': code,
                            'name': str(row.get('股票名称', '')),
                            'weight': float(row.get('权重', 0) or 0),
                        }
        except Exception as e:
            errors.append(f"HS300: {e}")
            logger.warning("获取沪深300成分股失败: %s", e)

        # 获取中证500成分股
        try:
            df_cs500 = await self._rate_limiter.execute_with_retry(
                lambda: self._ak.index_stock_cons_cs500()
            )
            if df_cs500 is not None and not df_cs500.empty:
                for _, row in df_cs500.iterrows():
                    code = str(row.get('股票代码', ''))
                    if code:
                        # 如果已存在（在HS300中），更新权重信息；否则新增
                        weight = float(row.get('权重', 0) or 0)
                        if code in result:
                            result[code]['weight'] = max(result[code].get('weight', 0), weight)
                        else:
                            result[code] = {
                                'setcode': self._get_setcode_by_code(code),
                                'code': code,
                                'name': str(row.get('股票名称', '')),
                                'weight': weight,
                            }
        except Exception as e:
            errors.append(f"CS500: {e}")
            logger.warning("获取中证500成分股失败: %s", e)

        if not result:
            raise DataSourceError(f"所有数据源不可用: {'; '.join(errors)}")

        return list(result.values())

    async def get_all_a_stocks(self) -> List[Dict]:
        """
        获取全部A股列表（约5532只）

        数据来源：
        - ak.stock_zh_a_spot_em() — 东方财富A股实时行情（最全）
        - 备用：ak.stock_info_a_code_name() — A股代码名称表

        Returns:
            股票列表 [{setcode, code, name, market}, ...]（约5532只）
        """
        if not self._ready:
            return []

        stocks = []
        # 主数据源：东方财富全A实时行情
        try:
            df = await self._rate_limiter.execute_with_retry(
                lambda: self._ak.stock_zh_a_spot_em()
            )
            if df is not None and not df.empty:
                for _, row in df.iterrows():
                    raw_code = str(row.get('代码', ''))
                    if not raw_code:
                        continue

                    setcode = self._get_setcode_by_code(raw_code)
                    market = self._get_market_by_code(raw_code)

                    stocks.append({
                        'setcode': setcode,
                        'code': raw_code,
                        'name': str(row.get('名称', '')),
                        'market': market,
                    })
                logger.info("从 stock_zh_a_spot_em 获取到 %d 只A股", len(stocks))
                return stocks
        except Exception as e:
            logger.warning("主数据源失败，尝试备用数据源: %s", e)

        # 备用数据源
        try:
            df_backup = await self._rate_limiter.execute_with_retry(
                lambda: self._ak.stock_info_a_code_name()
            )
            if df_backup is not None and not df_backup.empty:
                for _, row in df_backup.iterrows():
                    raw_code = str(row.get('code', '') or row.get('代码', ''))
                    if not raw_code:
                        continue

                    setcode = self._get_setcode_by_code(raw_code)
                    market = self._get_market_by_code(raw_code)

                    stocks.append({
                        'setcode': setcode,
                        'code': raw_code,
                        'name': str(row.get('name', '') or row.get('名称', '')),
                        'market': market,
                    })
                logger.info("从 stock_info_a_code_name 获取到 %d 只A股", len(stocks))
        except Exception as e:
            logger.warning("备用数据源也失败: %s", e)

        return stocks

    async def get_sector_index_stocks(self) -> List[Dict]:
        """
        获取通达信板块指数成分股（587只）

        数据来源：
        - ak.stock_board_industry_name_em() — 行业板块 + 成分股
        - ak.stock_board_concept_name_em() — 概念板块 + 成分股
        - 或使用 ak.stock_board_industry_cons_em(symbol) 逐个获取

        Returns:
            板块及成分股数据 [{sector_id, sector_name, category, members: [...]}, ...]
        """
        if not self._ready:
            return []

        sectors = []
        routes = _load_config("data_source_routes.json")
        sector_dispatch = routes.get("provider_routes", {}).get("akshare", {}).get("sector_dispatch", {})
        categories = [
            ('industry', '行业板块'),
            ('concept', '概念板块'),
        ]

        for category, category_name in categories:
            try:
                # 获取板块列表
                list_method_name = sector_dispatch.get('em', {}).get(category, {}).get('list')
                list_method = getattr(self._ak, list_method_name, None) if list_method_name else None
                if list_method is None:
                    continue
                df_list = await self._rate_limiter.execute_with_retry(lambda: list_method())

                if df_list is None or df_list.empty:
                    continue

                for _, sector_row in df_list.iterrows():
                    sector_name = str(sector_row.get('板块名称', ''))
                    sector_code = str(sector_row.get('板块代码', ''))

                    if not sector_name:
                        continue

                    # 获取该板块的成分股
                    members = []
                    try:
                        stocks_method_name = sector_dispatch.get('em', {}).get(category, {}).get('stocks')
                        stocks_method = getattr(self._ak, stocks_method_name, None) if stocks_method_name else None
                        if stocks_method is None:
                            continue
                        df_members = await self._rate_limiter.execute_with_retry(
                            lambda: stocks_method(symbol=sector_name)
                        )

                        if df_members is not None and not df_members.empty:
                            for _, member_row in df_members.iterrows():
                                member_code = str(member_row.get('代码', ''))
                                if member_code:
                                    members.append({
                                        'setcode': self._get_setcode_by_code(member_code),
                                        'code': member_code,
                                        'name': str(member_row.get('名称', '')),
                                    })
                    except Exception as e:
                        logger.warning("获取板块 %s 成分股失败: %s", sector_name, e)

                    sectors.append({
                        'sector_id': f"em_{category}_{sector_code}",
                        'sector_name': sector_name,
                        'category': category,
                        'category_name': category_name,
                        'member_count': len(members),
                        'members': members,
                    })

            except Exception as e:
                logger.warning("获取%s列表失败: %s", category_name, e)

        logger.info("共获取 %d 个板块，%d 只成分股",
                     len(sectors), sum(s['member_count'] for s in sectors))
        return sectors

    async def get_all_etf_list(self) -> List[Dict]:
        """
        获取全部ETF基金列表（1610只）

        数据来源：
        - ak.fund_etf_spot_em() — ETF实时行情
        - 或 ak.etf_info_cnspot()

        Returns:
            ETF列表 [{setcode, code, name, fund_type, size}, ...]（约1610只）
        """
        if not self._ready:
            return []

        etfs = []
        try:
            df = await self._rate_limiter.execute_with_retry(
                lambda: self._ak.fund_etf_spot_em()
            )
            if df is not None and not df.empty:
                for _, row in df.iterrows():
                    code = str(row.get('代码', ''))
                    if not code:
                        continue

                    # 判断ETF类型
                    name = str(row.get('名称', ''))
                    fund_type = self._classify_etf_type(name)

                    etfs.append({
                        'setcode': self._get_setcode_by_code(code),
                        'code': code,
                        'name': name,
                        'fund_type': fund_type,
                        'size': float(row.get('规模', 0) or 0),
                        'price': float(row.get('最新价', 0) or 0),
                    })
                logger.info("获取到 %d 只ETF", len(etfs))

        except Exception as e:
            logger.warning("获取ETF列表失败: %s", e)

            # 备用数据源
            try:
                df_backup = await self._rate_limiter.execute_with_retry(
                    lambda: self._ak.etf_info_cnspot()
                )
                if df_backup is not None and not df_backup.empty:
                    for _, row in df_backup.iterrows():
                        code = str(row.get('ETF代码', '') or row.get('代码', ''))
                        if not code:
                            continue
                        name = str(row.get('ETF简称', '') or row.get('名称', ''))
                        etfs.append({
                            'setcode': self._get_setcode_by_code(code),
                            'code': code,
                            'name': name,
                            'fund_type': self._classify_etf_type(name),
                            'size': 0,
                            'price': 0,
                        })
                    logger.info("从备用数据源获取到 %d 只ETF", len(etfs))
            except Exception as e2:
                logger.warning("备用ETF数据源也失败: %s", e2)

        return etfs

    async def get_all_cb_list(self) -> List[Dict]:
        """
        获取全部可转债列表（337只）

        数据来源：
        - ak.bond_cov_spot() — 可转债实时行情
        - 或 ak.bond_cov_deal() — 可转债成交数据

        Returns:
            可转债列表 [{code, name, stock_code, price}, ...]（约337只）
        """
        if not self._ready:
            return []

        cbs = []
        try:
            df = await self._rate_limiter.execute_with_retry(
                lambda: self._ak.bond_cov_spot()
            )
            if df is not None and not df.empty:
                for _, row in df.iterrows():
                    code = str(row.get('代码', ''))
                    if not code:
                        continue

                    cbs.append({
                        'code': code,
                        'name': str(row.get('名称', '')),
                        'stock_code': str(row.get('正股代码', '')),
                        'price': float(row.get('最新价', 0) or 0),
                    })
                logger.info("获取到 %d 只可转债", len(cbs))

        except Exception as e:
            logger.warning("获取可转债列表失败: %s", e)

            # 备用数据源
            try:
                df_backup = await self._rate_limiter.execute_with_retry(
                    lambda: self._ak.bond_cov_deal()
                )
                if df_backup is not None and not df_backup.empty:
                    for _, row in df_backup.iterrows():
                        code = str(row.get('可转债代码', '') or row.get('代码', ''))
                        if not code:
                            continue
                        cbs.append({
                            'code': code,
                            'name': str(row.get('可转债名称', '') or row.get('名称', '')),
                            'stock_code': str(row.get('正股代码', '')),
                            'price': float(row.get('成交价', 0) or 0),
                        })
                    logger.info("从备用数据源获取到 %d 只可转债", len(cbs))
            except Exception as e2:
                logger.warning("备用可转债数据源也失败: %s", e2)

        return cbs

    # ------------------------------------------------------------------
    # 多源分类树接口 (4.6-4.9)
    # ------------------------------------------------------------------

    async def get_ths_concept_list(self) -> List[Dict]:
        """获取同花顺概念板块列表（400+）"""
        if not self._ready:
            return []

        concepts = []
        try:
            df = await self._rate_limiter.execute_with_retry(
                lambda: self._ak.stock_board_concept_name_ths()
            )
            if df is not None and not df.empty:
                for _, row in df.iterrows():
                    concepts.append({
                        'sector_id': f"ths_concept_{row.get('板块代码', '')}",
                        'sector_name': str(row.get('板块名称', '')),
                        'source': 'ths',
                        'category': 'concept',
                        'code': str(row.get('板块代码', '')),
                    })
                logger.info("获取到 %d 个同花顺概念板块", len(concepts))
        except Exception as e:
            logger.warning("获取同花顺概念板块列表失败: %s", e)
        return concepts

    async def get_ths_concept_stocks(self, symbol: str) -> List[Dict]:
        """获取同花顺概念成分股"""
        if not self._ready:
            return []

        stocks = []
        try:
            df = await self._rate_limiter.execute_with_retry(
                lambda: self._ak.stock_board_concept_cons_ths(symbol=symbol)
            )
            if df is not None and not df.empty:
                for _, row in df.iterrows():
                    code = str(row.get('代码', ''))
                    if code:
                        stocks.append({
                            'setcode': self._get_setcode_by_code(code),
                            'code': code,
                            'name': str(row.get('名称', '')),
                        })
        except Exception as e:
            logger.warning("获取同花顺概念 %s 成分股失败: %s", symbol, e)
        return stocks

    async def get_em_industry_list(self) -> List[Dict]:
        """获取东方财富行业板块列表"""
        if not self._ready:
            return []

        industries = []
        try:
            df = await self._rate_limiter.execute_with_retry(
                lambda: self._ak.stock_board_industry_name_em()
            )
            if df is not None and not df.empty:
                for _, row in df.iterrows():
                    industries.append({
                        'sector_id': f"em_industry_{row.get('板块代码', '')}",
                        'sector_name': str(row.get('板块名称', '')),
                        'source': 'em',
                        'category': 'industry',
                        'code': str(row.get('板块代码', '')),
                    })
                logger.info("获取到 %d 个东方财富行业板块", len(industries))
        except Exception as e:
            logger.warning("获取东方财富行业板块列表失败: %s", e)
        return industries

    async def get_em_industry_stocks(self, symbol: str) -> List[Dict]:
        """获取东方财富行业成分股"""
        if not self._ready:
            return []

        stocks = []
        try:
            df = await self._rate_limiter.execute_with_retry(
                lambda: self._ak.stock_board_industry_cons_em(symbol=symbol)
            )
            if df is not None and not df.empty:
                for _, row in df.iterrows():
                    code = str(row.get('代码', ''))
                    if code:
                        stocks.append({
                            'setcode': self._get_setcode_by_code(code),
                            'code': code,
                            'name': str(row.get('名称', '')),
                        })
        except Exception as e:
            logger.warning("获取东方财富行业 %s 成分股失败: %s", symbol, e)
        return stocks

    async def get_em_concept_list(self) -> List[Dict]:
        """获取东方财富概念板块列表（300+）"""
        if not self._ready:
            return []

        concepts = []
        try:
            df = await self._rate_limiter.execute_with_retry(
                lambda: self._ak.stock_board_concept_name_em()
            )
            if df is not None and not df.empty:
                for _, row in df.iterrows():
                    concepts.append({
                        'sector_id': f"em_concept_{row.get('板块代码', '')}",
                        'sector_name': str(row.get('板块名称', '')),
                        'source': 'em',
                        'category': 'concept',
                        'code': str(row.get('板块代码', '')),
                    })
                logger.info("获取到 %d 个东方财富概念板块", len(concepts))
        except Exception as e:
            logger.warning("获取东方财富概念板块列表失败: %s", e)
        return concepts

    async def get_em_concept_stocks(self, symbol: str) -> List[Dict]:
        """获取东方财富概念成分股"""
        if not self._ready:
            return []

        stocks = []
        try:
            df = await self._rate_limiter.execute_with_retry(
                lambda: self._ak.stock_board_concept_cons_em(symbol=symbol)
            )
            if df is not None and not df.empty:
                for _, row in df.iterrows():
                    code = str(row.get('代码', ''))
                    if code:
                        stocks.append({
                            'setcode': self._get_setcode_by_code(code),
                            'code': code,
                            'name': str(row.get('名称', '')),
                        })
        except Exception as e:
            logger.warning("获取东方财富概念 %s 成分股失败: %s", symbol, e)
        return stocks

    async def get_sw_industry_list(self) -> List[Dict]:
        """获取申万一级行业列表（31个）"""
        if not self._ready:
            return []

        industries = []
        try:
            df = await self._rate_limiter.execute_with_retry(
                lambda: self._ak.sw_index_first_info()
            )
            if df is not None and not df.empty:
                for _, row in df.iterrows():
                    industries.append({
                        'sector_id': f"sw_industry_{row.get('指数代码', '')}",
                        'sector_name': str(row.get('指数名称', '')),
                        'source': 'sw',
                        'category': 'industry',
                        'code': str(row.get('指数代码', '')),
                    })
                logger.info("获取到 %d 个申万一级行业", len(industries))
        except Exception as e:
            logger.warning("获取申万一级行业列表失败: %s", e)
        return industries

    async def get_sw_industry_stocks(self, index_code: str) -> List[Dict]:
        """获取申万行业成分股"""
        if not self._ready:
            return []

        stocks = []
        try:
            df = await self._rate_limiter.execute_with_retry(
                lambda: self._ak.sw_index_cons(index_code=index_code)
            )
            if df is not None and not df.empty:
                for _, row in df.iterrows():
                    code = str(row.get('股票代码', '') or row.get('代码', ''))
                    if code:
                        stocks.append({
                            'setcode': self._get_setcode_by_code(code),
                            'code': code,
                            'name': str(row.get('股票名称', '') or row.get('名称', '')),
                        })
        except Exception as e:
            logger.warning("获取申万行业 %s 成分股失败: %s", index_code, e)
        return stocks

    # ------------------------------------------------------------------
    # 通用查询方法 (4.10)
    # ------------------------------------------------------------------

    async def get_all_sectors(self, category: str = None,
                               source: str = None) -> List[Dict]:
        """
        统一查询所有数据源的板块目录

        Args:
            category: 分类过滤（industry/concept/region/index）
            source: 数据源过滤（ths/em/sw/tdx）

        Returns:
            统一格式的板块列表
        """
        all_sectors = []

        routes = _load_config("data_source_routes.json")
        sector_sources = routes.get("provider_routes", {}).get("akshare", {}).get("sector_sources", {})
        sector_dispatch = routes.get("provider_routes", {}).get("akshare", {}).get("sector_dispatch", {})

        # 根据条件选择数据源
        sources_to_query = []
        for src, cfg in sector_sources.items():
            if source is None or source == src:
                sources_to_query.append((src, cfg.get("categories", [])))

        dispatch = routes.get("provider_routes", {}).get("akshare", {}).get("sector_category_dispatch", {})

        for src, categories in sources_to_query:
            if category and category not in categories:
                continue
            for cat in categories:
                if category and cat != category:
                    continue
                method_name = dispatch.get(f"{src}_{cat}")
                method = getattr(self, method_name, None) if method_name else None
                if method:
                    all_sectors.extend(await method())

        logger.info("get_all_sectors(category=%s, source=%s): 返回 %d 个板块",
                     category, source, len(all_sectors))
        return all_sectors

    async def resolve_sector_stocks(self, sector_id: str) -> List[Dict]:
        """
        根据 sector_id 自动选择数据源获取成分股

        sector_id 格式示例：
        - 'ths_concept_人工智能' → 调用 get_ths_concept_stocks()
        - 'em_industry_银行' → 调用 get_em_industry_stocks()
        - 'sw_industry_农林牧渔' → 调用 get_sw_industry_stocks()

        Args:
            sector_id: 板块ID，格式为 "{source}_{category}_{code_or_name}"

        Returns:
            成分股列表 [{setcode, code, name}, ...]
        """
        if not sector_id:
            return []

        parts = sector_id.split('_', 2)
        if len(parts) < 3:
            logger.warning("无效的 sector_id 格式: %s", sector_id)
            return []

        src, cat, identifier = parts

        routes = _load_config("data_source_routes.json")
        dispatch = routes.get("provider_routes", {}).get("akshare", {}).get("sector_id_dispatch", {})
        method_name = dispatch.get(f"{src}_{cat}")
        method = getattr(self, method_name, None) if method_name else None
        if method is None:
            logger.warning("不支持的 sector_id 格式: %s", sector_id)
            return []

        try:
            return await method(identifier)
        except Exception as e:
            logger.warning("解析 sector_id %s 失败: %s", sector_id, e)
            return []

    # ------------------------------------------------------------------
    # 内部辅助方法
    # ------------------------------------------------------------------

    @staticmethod
    def _get_setcode_by_code(code: str) -> int:
        """根据股票代码判断市场编号。

        setcode: 0=深圳, 1=上海, 2=北交
        """
        for rule in _load_config("market_classifications.json").get("code_prefix_rules", []):
            prefix = rule.get("prefix", "")
            if prefix and code.startswith(prefix):
                return rule.get("setcode", 0)
        return 0

    @staticmethod
    def _get_market_by_code(code: str) -> str:
        """根据股票代码判断市场名称。"""
        for rule in _load_config("market_classifications.json").get("code_prefix_rules", []):
            prefix = rule.get("prefix", "")
            if prefix and code.startswith(prefix):
                return rule.get("market", '')
        return ''

    @staticmethod
    def _classify_etf_type(name: str) -> str:
        """根据ETF名称判断类型。"""
        name_lower = name.lower()
        for rule in _load_config("market_classifications.json").get("etf_type_rules", []):
            if "default" in rule:
                continue
            keywords = rule.get("keywords", [])
            if any(kw in name_lower for kw in keywords):
                return rule["type"]
        return 'equity'


class DataSourceError(Exception):
    """数据源异常。"""
    pass
