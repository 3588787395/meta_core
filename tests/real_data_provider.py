"""
Real TQ Data Provider — Downloads and caches real quantitative data.
Tests use this to get real market data instead of mock data.

Data sources (priority order):
  1. Local JSON cache in tests/real_data/
  2. TQ SDK (tqsdk) — primary live data source
  3. AKShare — fallback data source
"""
import os
import json
import time
import logging
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(__file__), 'real_data')
KLINE_DIR = os.path.join(DATA_DIR, 'kline')
SNAPSHOT_DIR = os.path.join(DATA_DIR, 'snapshot')
FINANCIAL_DIR = os.path.join(DATA_DIR, 'financial')
STOCK_LIST_DIR = os.path.join(DATA_DIR, 'stock_list')

# Ensure cache directories exist
for d in [KLINE_DIR, SNAPSHOT_DIR, FINANCIAL_DIR, STOCK_LIST_DIR]:
    os.makedirs(d, exist_ok=True)


def _normalize_symbol(symbol: str) -> str:
    """Normalize stock code to KQ.m format for tqsdk.

    Examples:
        '600000' → 'SH.600000'  (6开头=沪市)
        '000001' → 'SZ.000001'  (0/3开头=深市)
        '830001' → 'BJ.830001'  (8/4开头=北交所)
        '600000.SH' → 'SH.600000'
        'SH600000' → 'SH.600000'
    """
    s = symbol.strip().upper()
    # Already in exchange.code format
    for prefix in ('SH.', 'SZ.', 'BJ.'):
        if s.startswith(prefix):
            return s
    # Suffix format: 600000.SH
    for suffix in ('.SH', '.SZ', '.BJ'):
        if s.endswith(suffix):
            code = s[:-len(suffix)]
            exchange = suffix[1:]
            return f"{exchange}.{code}"
    # Prefix format: SH600000
    for prefix in ('SH', 'SZ', 'BJ'):
        if s.startswith(prefix) and len(s) > len(prefix) and s[len(prefix)].isdigit():
            code = s[len(prefix):]
            return f"{prefix}.{code}"
    # Pure numeric code — infer exchange
    code = s
    if code.startswith('6'):
        return f"SH.{code}"
    elif code.startswith(('0', '3')):
        return f"SZ.{code}"
    elif code.startswith(('8', '4')):
        return f"BJ.{code}"
    else:
        return f"SZ.{code}"  # default


def _cache_path(category: str, key: str, ext: str = 'json') -> str:
    """Build cache file path."""
    base = {'kline': KLINE_DIR, 'snapshot': SNAPSHOT_DIR,
            'financial': FINANCIAL_DIR, 'stock_list': STOCK_LIST_DIR}
    return os.path.join(base.get(category, DATA_DIR), f"{key}.{ext}")


def _read_cache(path: str) -> Optional[Any]:
    """Read JSON cache if exists and not expired."""
    if not os.path.exists(path):
        return None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        # Check TTL
        cached_ts = data.get('_cached_at', 0)
        ttl = data.get('_ttl', 86400)  # default 24h
        if time.time() - cached_ts > ttl:
            return None
        return data.get('payload')
    except (json.JSONDecodeError, KeyError, TypeError):
        return None


def _write_cache(path: str, payload: Any, ttl: int = 86400) -> None:
    """Write data to JSON cache with metadata."""
    data = {
        '_cached_at': time.time(),
        '_ttl': ttl,
        'payload': payload,
    }
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)
    except Exception as e:
        logger.warning("缓存写入失败 %s: %s", path, e)


def get_kline(symbol: str, freq: str = 'day', start_date: Optional[str] = None,
              end_date: Optional[str] = None, use_cache: bool = True) -> List[Dict]:
    """
    Get real K-line data. Tries cache first, then downloads from TQ SDK.

    Args:
        symbol: stock code, e.g. '600000', '600000.SH', 'SH600000'
        freq: 'day', '1h', '30m', '5m', '1m'
        start_date: YYYY-MM-DD (default: 30 trading days ago)
        end_date: YYYY-MM-DD (default: today)
        use_cache: whether to use local cache

    Returns:
        list of dicts with keys: datetime, open, high, low, close, volume, amount
    """
    norm = _normalize_symbol(symbol)
    cache_key = f"{norm.replace('.', '_')}_{freq}"
    cache_file = _cache_path('kline', cache_key)

    if use_cache:
        cached = _read_cache(cache_file)
        if cached is not None:
            return cached

    if start_date is None:
        start_date = (datetime.now() - timedelta(days=60)).strftime('%Y-%m-%d')
    if end_date is None:
        end_date = datetime.now().strftime('%Y-%m-%d')

    # Try TQ SDK
    try:
        data = _fetch_kline_tqsdk(norm, freq, start_date, end_date)
        if data:
            _write_cache(cache_file, data, ttl=86400 if freq == 'day' else 300)
            return data
    except Exception as e:
        logger.info("TQ SDK获取K线失败 %s: %s", norm, e)

    # Try AKShare
    try:
        data = _fetch_kline_akshare(symbol, freq, start_date, end_date)
        if data:
            _write_cache(cache_file, data, ttl=86400 if freq == 'day' else 300)
            return data
    except Exception as e:
        logger.info("AKShare获取K线失败 %s: %s", symbol, e)

    raise RuntimeError(
        f"无法获取K线数据 {symbol}({freq}). "
        f"请安装 tqsdk 或 akshare，或预缓存数据到 {cache_file}"
    )


def _fetch_kline_tqsdk(symbol: str, freq: str, start_date: str, end_date: str) -> Optional[List[Dict]]:
    """Fetch K-line data using TQ SDK."""
    from tqsdk import TqApi, TqAuth

    freq_map = {'1m': 60, '5m': 300, '15m': 900, '30m': 1800,
                '1h': 3600, '2h': 7200, 'day': 86400, 'week': 604800}
    duration = freq_map.get(freq, 86400)

    # Calculate data length (approximate trading days)
    days = (datetime.strptime(end_date, '%Y-%m-%d') -
            datetime.strptime(start_date, '%Y-%m-%d')).days
    data_length = max(days * 2, 100) if freq == 'day' else max(days * 4 * 4, 200)

    api = TqApi(auth=TqAuth("test", "test"))
    try:
        kline = api.get_kline_serial(symbol, duration, data_length)
        while True:
            api.wait_update()
            if api.is_changing(kline.iloc[-1], "datetime"):
                break
        result = []
        for _, row in kline.iterrows():
            if row.get('datetime') and not (row.get('close') != row.get('close')):  # NaN check
                result.append({
                    'datetime': str(row['datetime']),
                    'open': float(row['open']),
                    'high': float(row['high']),
                    'low': float(row['low']),
                    'close': float(row['close']),
                    'volume': float(row['volume']),
                    'amount': float(row.get('amount', 0)),
                })
        return result
    finally:
        api.close()


def _fetch_kline_akshare(symbol: str, freq: str, start_date: str, end_date: str) -> Optional[List[Dict]]:
    """Fetch K-line data using AKShare."""
    import akshare as ak

    # Determine symbol format for akshare
    code = symbol.replace('.SH', '').replace('.SZ', '').replace('.BJ', '')
    code = code.replace('SH', '').replace('SZ', '').replace('BJ', '')

    freq_map = {'day': 'daily', '1h': '60', '30m': '30', '5m': '5', '1m': '1'}
    ak_freq = freq_map.get(freq, 'daily')

    if freq == 'day':
        # Determine market
        if symbol.startswith('6') or 'SH' in symbol.upper():
            df = ak.stock_zh_a_hist(symbol=code, period='daily',
                                     start_date=start_date.replace('-', ''),
                                     end_date=end_date.replace('-', ''), adjust='qfq')
        else:
            df = ak.stock_zh_a_hist(symbol=code, period='daily',
                                     start_date=start_date.replace('-', ''),
                                     end_date=end_date.replace('-', ''), adjust='qfq')
    else:
        df = ak.stock_zh_a_hist_min_em(symbol=code, period=ak_freq, adjust='qfq')

    result = []
    for _, row in df.iterrows():
        result.append({
            'datetime': str(row.get('日期', row.get('时间', ''))),
            'open': float(row.get('开盘', row.get('open', 0))),
            'high': float(row.get('最高', row.get('high', 0))),
            'low': float(row.get('最低', row.get('low', 0))),
            'close': float(row.get('收盘', row.get('close', 0))),
            'volume': float(row.get('成交量', row.get('volume', 0))),
            'amount': float(row.get('成交额', row.get('amount', 0))),
        })
    return result


def get_stock_list(market: str = 'SH') -> List[Dict]:
    """
    Get real stock list for a market.

    Args:
        market: 'SH' (上海), 'SZ' (深圳), 'BJ' (北交所)

    Returns:
        list of dicts with 'code' and 'label' fields
    """
    cache_file = _cache_path('stock_list', f"{market}_stocks")
    cached = _read_cache(cache_file)
    if cached is not None:
        return cached

    # Try AKShare first (no auth needed)
    try:
        import akshare as ak
        if market == 'SH':
            df = ak.stock_info_sh_name_code(symbol='主板A股')
        elif market == 'SZ':
            df = ak.stock_info_sz_name_code(indicator='A股列表')
        else:
            df = ak.stock_info_sh_name_code(symbol='主板A股')

        result = []
        for _, row in df.iterrows():
            code = str(row.get('证券代码', row.get('A股代码', '')))
            name = str(row.get('证券简称', row.get('公司简称', '')))
            if code and name:
                result.append({'code': code, 'label': name})
        if result:
            _write_cache(cache_file, result, ttl=86400)
            return result
    except Exception as e:
        logger.info("AKShare获取股票列表失败 %s: %s", market, e)

    raise RuntimeError(
        f"无法获取 {market} 股票列表. "
        f"请安装 akshare，或预缓存数据到 {cache_file}"
    )


def get_financial_data(symbol: str) -> Dict:
    """
    Get real financial data for a stock.

    Args:
        symbol: stock code

    Returns:
        dict with financial metrics (roe, eps, etc.)
    """
    norm = _normalize_symbol(symbol)
    cache_file = _cache_path('financial', norm.replace('.', '_'))

    cached = _read_cache(cache_file)
    if cached is not None:
        return cached

    try:
        import akshare as ak
        code = symbol.replace('.SH', '').replace('.SZ', '').replace('.BJ', '')
        code = code.replace('SH', '').replace('SZ', '').replace('BJ', '')
        df = ak.stock_financial_analysis_indicator(symbol=code)
        if df is not None and not df.empty:
            latest = df.iloc[0].to_dict() if len(df) > 0 else {}
            result = {str(k): v for k, v in latest.items()}
            _write_cache(cache_file, result, ttl=86400)
            return result
    except Exception as e:
        logger.info("AKShare获取财务数据失败 %s: %s", symbol, e)

    raise RuntimeError(
        f"无法获取财务数据 {symbol}. "
        f"请安装 akshare，或预缓存数据到 {cache_file}"
    )


def get_realtime_snapshot(symbols: List[str]) -> Dict[str, Dict]:
    """
    Get real snapshot data (for nset=4 tests).

    Args:
        symbols: list of stock codes

    Returns:
        dict of {code: {open, high, low, close, volume, amount, ...}}
    """
    cache_key = "_".join(sorted(symbols)[:10])  # Limit key length
    cache_file = _cache_path('snapshot', f"snap_{cache_key}")

    # Snapshot data has very short TTL (5 seconds in production)
    cached = _read_cache(cache_file)
    if cached is not None:
        return cached

    try:
        import akshare as ak
        # Build code list for akshare
        codes = []
        for s in symbols:
            code = s.replace('.SH', '').replace('.SZ', '').replace('.BJ', '')
            code = code.replace('SH', '').replace('SZ', '').replace('BJ', '')
            codes.append(code)

        df = ak.stock_zh_a_spot_em()
        result = {}
        for _, row in df.iterrows():
            code = str(row.get('代码', ''))
            if code in codes:
                result[code] = {
                    'open': float(row.get('今开', 0)),
                    'high': float(row.get('最高', 0)),
                    'low': float(row.get('最低', 0)),
                    'close': float(row.get('最新价', 0)),
                    'volume': float(row.get('成交量', 0)),
                    'amount': float(row.get('成交额', 0)),
                    'turnover': float(row.get('换手率', 0)),
                    'pe': float(row.get('市盈率-动态', 0)),
                }
        if result:
            _write_cache(cache_file, result, ttl=300)  # 5 min cache for testing
            return result
    except Exception as e:
        logger.info("AKShare获取实时行情失败: %s", e)

    raise RuntimeError(
        f"无法获取实时行情. "
        f"请安装 akshare，或预缓存数据到 {cache_file}"
    )


def get_kline_as_bar_data(symbols: List[str], freq: str = 'day') -> Dict[str, Dict]:
    """
    Get K-line data formatted as current_bar_data for engine.run_pool().

    This is the format expected by PoolEngine.run_pool()'s current_bar_data parameter:
    {code: {open, high, low, close, volume, amount, ...}}

    Args:
        symbols: list of stock codes
        freq: K-line frequency

    Returns:
        dict of {code: bar_data_dict}
    """
    result = {}
    for symbol in symbols:
        try:
            klines = get_kline(symbol, freq=freq)
            if klines:
                latest = klines[-1]  # Most recent bar
                # Use pure numeric code as key (engine normalizes internally)
                code = symbol.replace('.SH', '').replace('.SZ', '').replace('.BJ', '')
                code = code.replace('SH', '').replace('SZ', '').replace('BJ', '')
                result[code] = {
                    'open': latest.get('open', 0),
                    'high': latest.get('high', 0),
                    'low': latest.get('low', 0),
                    'close': latest.get('close', 0),
                    'volume': latest.get('volume', 0),
                    'amount': latest.get('amount', 0),
                }
        except Exception as e:
            logger.warning("获取 %s K线数据失败: %s", symbol, e)
    return result


def pre_cache_test_data(symbols: Optional[List[str]] = None, freq: str = 'day'):
    """
    Pre-cache all test data for offline testing.

    Call this once to download and cache data, then tests can run offline.

    Args:
        symbols: list of stock codes to cache (default: common test stocks)
        freq: K-line frequency to cache
    """
    if symbols is None:
        symbols = ['600000', '000001', '000002', '600036', '601318',
                   '000651', '600519', '601398', '000858', '002415']

    logger.info("开始预缓存测试数据: %d 只股票", len(symbols))

    for symbol in symbols:
        try:
            get_kline(symbol, freq=freq)
            logger.info("  ✓ K线 %s", symbol)
        except Exception as e:
            logger.warning("  ✗ K线 %s: %s", symbol, e)

    for market in ['SH', 'SZ']:
        try:
            get_stock_list(market)
            logger.info("  ✓ 股票列表 %s", market)
        except Exception as e:
            logger.warning("  ✗ 股票列表 %s: %s", market, e)

    logger.info("预缓存完成")
