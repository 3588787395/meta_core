"""
HQChart 数据源提供者  封装 HQChartPy2 C++ 技术指标计算引擎。

提供基于 HQChartPy2 的指标计算、选股评估等功能，
通过 IHQData 接口桥接 C++ 引擎与 Python 数据源。
"""

import asyncio
import json
import logging
import platform
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import DataSourceProvider
from ._common import map_period, to_dzh_code

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# _parse_formula_outvars 结果的模块级缓存（key = formula_text）
# ---------------------------------------------------------------------------
_PARSE_OUTVARS_CACHE: Dict[str, List[str]] = {}

# ---------------------------------------------------------------------------
# 确保 vendor 目录在 sys.path 中，以便导入 HQChartPy2
# ---------------------------------------------------------------------------
_vendor_dir = str(Path(__file__).resolve().parents[2] / 'vendor')
if _vendor_dir not in sys.path:
    sys.path.insert(0, _vendor_dir)

# ---------------------------------------------------------------------------
# 尝试导入 HQChartPy2 C++ 引擎
# ---------------------------------------------------------------------------
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


# ===========================================================================
# HQChart 周期常量
# ===========================================================================

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


# Python 内部 period int  HQChart period ID
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

# Python 内部 period int  TQ 适配器所用的周期字符串
_PERIOD_INT_TO_STR: Dict[int, str] = {
    0: 'tick', 1: '1m', 2: '5m', 3: '15m', 4: '30m',
    5: '60m', 6: '1d', 7: '1w', 8: '1mon',
}


# ===========================================================================
# IHQData 接口实现
# ===========================================================================

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


# ===========================================================================
# FastHQChart  HQChartPy2 C++ 引擎的静态包装器
# ===========================================================================

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


# ===========================================================================
# HQChartProvider  数据源提供者
# ===========================================================================

class HQChartProvider(DataSourceProvider):
    """基于 HQChartPy2 C++ 引擎的指标计算提供者。

    将 HQChartPy2 封装为 DataSourceProvider 接口，
    支持指标公式评估、选股公式评估和指标公式评估。

    K 线数据由调用方在 eval 时通过参数传入，本类不持有任何数据源引用。
    """

    def __init__(self):
        """初始化 HQChart 提供者。"""
        super().__init__()
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
        hqchart_code = to_dzh_code(code)  # 600000.SH  SH600000
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
