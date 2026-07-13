"""公式路由分发器。

根据公式复杂度与数据周期，在纯 Python 公式引擎与 HQChart C++ 引擎之间做显式路由，
并集成公式结果缓存。

路由策略（决策预先做出，失败时不切换路径）：
- 简单公式（token 全部属于 ``simple_functions`` 或运算符/比较/逻辑/数字）且周期为
  ``1m``/``tick`` 时，使用 Python 公式引擎。
- 其它情形使用 HQChart 引擎。
- HQChart 不可用时，仅当满足「简单公式 + 1m/tick」才回退到 Python 引擎；
  否则直接抛出 ``RuntimeError``，禁止静默回退到 mock 或任何兜底数据源。

缓存策略：
- 每次求值先查缓存，命中直接返回。
- 计算完成后按周期 TTL 写入缓存（``tick`` 不缓存，分钟级由配置决定，日线级 86400s）。
- 分钟闭合时由调用方触发 ``invalidate_on_minute_close``。

架构契约：
- 公式路由层不通过 PythonFormulaEngine 间接持有数据源。
- K 线数据由注入的 ``data_query`` 提供，显式注入，避免隐式持有。
"""

import asyncio
import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from .formula_engine import PythonFormulaEngine
from ..services.data import DataQuery
from ..services.formula_cache import FormulaCache

logger = logging.getLogger(__name__)

# config/data_pipeline.json 路径（core/ → 上一级 → config/）
_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "data_pipeline.json"

# config/formula_routing.json 路径（引擎路由决策规则表）
_ROUTING_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "formula_routing.json"

# 运算符、分隔符与逻辑关键字集合
_OPERATORS = frozenset({
    ":=", ":", ";", "(", ")", ",",
    "+", "-", "*", "/",
    "<", ">", "<=", ">=", "=", "==", "!=",
})
_LOGIC_WORDS = frozenset({"AND", "OR", "NOT"})

# 分词正则（与 formula_engine 保持一致）
_TOKEN_RE = re.compile(
    r"(?i)(:=|>=|<=|==|!=|[<>=:\-+*/(),;]|[A-Za-z_][A-Za-z0-9_]*|\d+\.?\d*)"
)

# HQChart provider 求值方法优先级表（表驱动：消除方法可用性 4 层 elif）。
# 顺序即优先级：先 outvars（返回全部输出变量），后 indicator（仅首个标量）；
# 先 _async（原生协程），后同步（经 run_in_executor 调度）。
_OUTVARS_METHOD_PRIORITY = [
    "eval_indicator_outvars_async",
    "eval_indicator_outvars",
    "eval_indicator_async",
    "eval_indicator",
]


def _hash_object(obj: Any) -> str:
    """对任意对象做确定性 md5 哈希，生成 32 位十六进制字符串。"""
    if obj is None:
        return "0" * 32
    try:
        serialized = json.dumps(obj, sort_keys=True, default=str, ensure_ascii=False)
    except Exception:
        try:
            serialized = repr(obj)
        except Exception:
            serialized = str(obj)
    return hashlib.md5(serialized.encode("utf-8", errors="replace")).hexdigest()


class FormulaRouter:
    """公式路由器：按复杂度与周期选择执行引擎，并管理结果缓存。"""

    def __init__(
        self,
        data_query: Optional[DataQuery] = None,
        hqchart_provider: Optional[Any] = None,
        python_engine: Optional[PythonFormulaEngine] = None,
        cache: Optional[FormulaCache] = None,
    ):
        """初始化 FormulaRouter。

        Args:
            data_query: K 线数据查询实例，需提供 ``get_kline_series(symbol, period)`` 方法。
            hqchart_provider: HQChart 引擎封装实例；为 None 时懒加载。
            python_engine: Python 公式引擎实例；为 None 时懒加载。
            cache: 公式结果缓存实例；为 None 时懒加载。
        """
        self._data_query = data_query
        self._python_engine = python_engine or PythonFormulaEngine()
        self._cache = cache or FormulaCache()

        self._simple_functions = self._load_simple_functions()

        # 加载公式引擎路由规则表（表驱动路由决策）
        self._routing_rules = self._load_routing_rules()
        # 加载引擎方法映射表（表驱动引擎分派，消除 if engine 分支）
        self._engine_methods = self._load_engine_methods()

        # 懒加载 HQChartProvider；导入失败时标记为不可用
        self._hqchart_provider: Optional[Any] = hqchart_provider
        self._hqchart_available = False
        if self._hqchart_provider is None:
            try:
                from ..services.providers.hqchart_provider import HQChartProvider
                self._hqchart_provider = HQChartProvider()
                self._hqchart_available = bool(self._hqchart_provider.is_ready())
            except ImportError as e:
                logger.warning("HQChartProvider 导入失败，HQChart 引擎不可用: %s", e)
                self._hqchart_provider = None
                self._hqchart_available = False
        else:
            try:
                self._hqchart_available = bool(self._hqchart_provider.is_ready())
            except Exception:
                self._hqchart_available = False

    @staticmethod
    def _load_simple_functions() -> frozenset:
        """从 config/data_pipeline.json 加载 simple_functions 列表。"""
        try:
            if _CONFIG_PATH.exists():
                with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                funcs = cfg.get("formula", {}).get("simple_functions", [])
                return frozenset(str(fn).upper() for fn in funcs)
        except Exception as e:
            logger.warning("读取 simple_functions 配置失败: %s", e)
        return frozenset()

    @staticmethod
    def _load_routing_config(key: str, default: Any) -> Any:
        """从 config/formula_routing.json 加载指定键（统一加载函数）。

        engine_routing / engine_methods 等同构配置项共用此加载逻辑，
        仅 key 与默认值不同，消除重复的 try/open/get 样板。
        """
        try:
            if _ROUTING_CONFIG_PATH.exists():
                with open(_ROUTING_CONFIG_PATH, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                return cfg.get(key, default)
        except Exception as e:
            logger.warning("读取 formula_routing.json %s 失败: %s", key, e)
        return default

    @staticmethod
    def _load_routing_rules() -> list:
        """从 config/formula_routing.json 加载引擎路由规则表。"""
        return FormulaRouter._load_routing_config("engine_routing", [])

    @staticmethod
    def _load_engine_methods() -> dict:
        """从 config/formula_routing.json 加载引擎方法映射表。"""
        return FormulaRouter._load_routing_config("engine_methods", {})

    def _resolve_engine(self, ctx: dict) -> str:
        """按 formula_routing.json 规则表匹配引擎。

        ctx 含 complexity/period/hqchart_available，按规则表顺序匹配，
        首个命中的规则返回其 engine；均不命中时返回 "error"。

        Args:
            ctx: 路由上下文，包含 complexity、period、hqchart_available 等字段。

        Returns:
            引擎名称："python" / "hqchart" / "error"。
        """
        for rule in self._routing_rules:
            cond = rule["condition"]
            if cond == "default":
                return rule["engine"]
            if self._match_condition(cond, ctx):
                return rule["engine"]
        return "error"

    @staticmethod
    def _match_condition(cond: dict, ctx: dict) -> bool:
        """检查 ctx 是否匹配 condition 中的全部字段约束。

        支持的约束字段：complexity（等值）、period_in（成员归属）、
        hqchart_available（等值）。所有出现的约束均需满足才返回 True。
        """
        if "complexity" in cond and ctx.get("complexity") != cond["complexity"]:
            return False
        if "period_in" in cond and ctx.get("period") not in cond["period_in"]:
            return False
        if "hqchart_available" in cond and ctx.get("hqchart_available") != cond["hqchart_available"]:
            return False
        return True

    async def _dispatch_engine_call(
        self, engine: str, method_key: str, *args: Any, **kwargs: Any
    ) -> Any:
        """通用引擎方法分派器：查 engine_methods 表反射调用。

        按 formula_routing.json 的 engine_methods[engine][method_key] 取方法名，
        getattr(self, method_name) 反射调用，无 if engine 分支。

        Args:
            engine: 引擎名称（"python" / "hqchart"）。
            method_key: 方法键（"eval" / "eval_outvars" / "eval_batch"）。
            *args: 透传给目标方法的 positional 参数。
            **kwargs: 透传给目标方法的关键字参数。

        Returns:
            目标方法的返回值。

        Raises:
            RuntimeError: engine 或 method_key 未在 engine_methods 表中声明。
        """
        engine_map = self._engine_methods.get(engine)
        if not engine_map:
            raise RuntimeError(
                "HQChart engine unavailable and formula cannot be evaluated by Python engine"
            )
        method_name = engine_map.get(method_key)
        if not method_name:
            raise RuntimeError(
                "HQChart engine unavailable and formula cannot be evaluated by Python engine"
            )
        method = getattr(self, method_name)
        return await method(*args, **kwargs)

    def _analyze_complexity(self, formula: str) -> str:
        """分析公式复杂度：``simple`` / ``complex``。

        对公式分词后，仅检查函数调用名（标识符后紧跟 ``(`` ）是否在
        config/data_pipeline.json ``formula.simple_functions`` 声明中。

        非函数标识符（输出变量名、参数名、OHLC 字段别名如 CLOSE/OPEN、
        显示指令如 COLORSTICK）不参与复杂度判定。

        若所有函数调用均属于 simple_functions，则判定为 simple；
        否则判定为 complex（如含 BOLL、KDJ 等未声明函数）。
        """
        tokens = self._tokenize(formula)
        for i, tok in enumerate(tokens):
            upper = tok.upper()
            if self._is_number(tok) or tok in _OPERATORS or upper in _LOGIC_WORDS:
                continue
            # 仅检查函数调用名：标识符后紧跟 "("
            is_function_call = (i + 1 < len(tokens) and tokens[i + 1] == "(")
            if not is_function_call:
                continue
            if upper in self._simple_functions:
                continue
            return "complex"
        return "simple"

    @staticmethod
    def _tokenize(formula: str) -> List[str]:
        """简单分词器：移除注释后识别标识符、数字、运算符和分隔符。"""
        formula = re.sub(r"\{[^}]*\}", " ", formula)
        formula = re.sub(r"//[^\n]*", " ", formula)
        return _TOKEN_RE.findall(formula)

    @staticmethod
    def _is_number(token: str) -> bool:
        """判断 token 是否为数字（支持整数和小数）。"""
        return bool(re.match(r"^\d+\.?\d*$", token))

    def _make_key(
        self,
        formula: str,
        symbol: str,
        period: str,
        args: Optional[dict],
    ) -> str:
        """生成缓存键。"""
        args = args or {}
        return self._cache.make_key(
            symbol,
            period,
            _hash_object(formula),
            _hash_object(args),
        )

    @staticmethod
    def _inject_args_into_script(formula: str, args: Optional[dict]) -> str:
        """将公式参数注入到脚本前部（HQChart 引擎不支持 SetArgs 时的兜底实现）。

        TDX/DZH 公式参数（如 MACD 的 SHORT/LONG/MID）在脚本中以裸标识符引用，
        在脚本前部追加 ``NAME:=value;`` 赋值即可让 HQChart 引擎使用前端配置值。
        若脚本自身已对同名变量赋值，脚本内赋值生效，保持向后兼容。
        """
        if not args:
            return formula
        parts = []
        for k, v in args.items():
            if isinstance(v, bool):
                continue
            if isinstance(v, (int, float)):
                parts.append(f"{k}:={v};")
        if not parts:
            return formula
        return "".join(parts) + formula

    async def eval(
        self,
        formula: str,
        symbol: str,
        period: str = "1d",
        args: Optional[dict] = None,
        context: Optional[dict] = None,
    ) -> Any:
        """单股公式求值（带缓存+路由）。

        Args:
            formula: 公式字符串。
            symbol: 标的代码。
            period: 周期，默认 ``'1d'``。
            args: 公式参数，参与缓存键计算。
            context: 可选上下文（暂不参与缓存键，仅保留扩展性）。

        Returns:
            公式求值结果；HQChart 不可用时若无法回退到 Python 引擎则抛出异常。
        """
        key = self._make_key(formula, symbol, period, args)
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        complexity = self._analyze_complexity(formula)
        engine = self._resolve_engine({
            "complexity": complexity,
            "period": period,
            "hqchart_available": self._hqchart_available,
        })

        result = await self._dispatch_engine_call(
            engine, "eval", formula, symbol, period, args
        )

        self._cache.set(key, result)
        return result

    async def eval_outvars(
        self,
        formula: str,
        symbol: str,
        period: str = "1d",
        args: Optional[dict] = None,
    ) -> Optional[Dict[str, Any]]:
        """单股公式求值，返回全部输出变量的末值。

        与 ``eval`` 不同，本方法始终返回 ``{outvar_name: last_value}`` 字典，
        适用于需要多输出变量结果的场景（如公式测试端点）。

        Returns:
            ``{outvar_name: last_value}`` 字典；求值失败时返回 None。
        """
        complexity = self._analyze_complexity(formula)
        engine = self._resolve_engine({
            "complexity": complexity,
            "period": period,
            "hqchart_available": self._hqchart_available,
        })

        return await self._dispatch_engine_call(
            engine, "eval_outvars", formula, symbol, period, args
        )

    async def _eval_hqchart_outvars(
        self, formula: str, symbol: str, period: str, args: Optional[dict] = None,
    ) -> Optional[Dict[str, Any]]:
        """使用 HQChart 引擎对单股求值，返回全部输出变量。"""
        if self._data_query is None:
            raise RuntimeError("data_query is required for HQChart engine evaluation")
        if self._hqchart_provider is None:
            raise RuntimeError("HQChart provider is not available")

        # 注入公式参数（HQChart 不支持 SetArgs，与 _eval_hqchart_batch 一致通过脚本前置赋值）
        script = self._inject_args_into_script(formula, args)

        loop = asyncio.get_event_loop()
        df = await loop.run_in_executor(
            None, self._data_query.get_kline_series, symbol, period
        )
        bars = df.to_dict("records") if df is not None else []
        kline_data = {symbol: bars}

        for method_name in _OUTVARS_METHOD_PRIORITY:
            if not hasattr(self._hqchart_provider, method_name):
                continue
            method = getattr(self._hqchart_provider, method_name)
            is_async = method_name.endswith("_async")
            is_outvars = "outvars" in method_name
            if is_async:
                response = await method(
                    [symbol], script, period, kline_data=kline_data
                )
            else:
                response = await loop.run_in_executor(
                    None, method, [symbol], script, period, 0, kline_data
                )
            if is_outvars:
                return response.get("result", {}).get(symbol) if response else None
            scalar = response.get("result", {}).get(symbol) if response else None
            return {"value": scalar} if scalar is not None else None
        raise RuntimeError("HQChart provider 缺少可用的求值方法")

    async def _eval_python_outvars(
        self, formula: str, symbol: str, period: str, args: Optional[dict] = None,
    ) -> Optional[Dict[str, Any]]:
        """使用 Python 公式引擎对单股求值，返回全部输出变量末值字典。

        与 ``_eval_python`` 不同，本方法始终返回 ``{outvar_name: last_value}`` 字典
        （由 ``PythonFormulaEngine.eval_outvars`` 保证形状契约），适用于公式测试端点。
        """
        if self._data_query is None:
            raise RuntimeError("data_query is required for Python engine evaluation")

        loop = asyncio.get_event_loop()
        df = await loop.run_in_executor(
            None, self._data_query.get_kline_series, symbol, period
        )
        if df is None or df.empty:
            return None
        return await loop.run_in_executor(
            None, self._python_engine.eval_outvars, formula, df, args
        )

    async def _eval_python(self, formula: str, symbol: str, period: str, args: Optional[dict] = None) -> Any:
        """使用 Python 公式引擎对单股求值。"""
        if self._data_query is None:
            raise RuntimeError("data_query is required for Python engine evaluation")

        loop = asyncio.get_event_loop()
        df = await loop.run_in_executor(
            None, self._data_query.get_kline_series, symbol, period
        )
        if df is None or df.empty:
            return None
        return await loop.run_in_executor(
            None, self._python_engine.eval, formula, df, args
        )

    async def _eval_hqchart(self, formula: str, symbol: str, period: str, args: Optional[dict] = None) -> Any:
        """使用 HQChart 引擎对单股求值。"""
        if self._data_query is None:
            raise RuntimeError("data_query is required for HQChart engine evaluation")
        if self._hqchart_provider is None:
            raise RuntimeError("HQChart provider is not available")

        # 注入公式参数（HQChart 不支持 SetArgs，与 _eval_hqchart_outvars/_eval_hqchart_batch 一致）
        script = self._inject_args_into_script(formula, args)

        loop = asyncio.get_event_loop()
        df = await loop.run_in_executor(
            None, self._data_query.get_kline_series, symbol, period
        )
        bars = df.to_dict("records") if df is not None else []
        kline_data = {symbol: bars}

        if hasattr(self._hqchart_provider, "eval_indicator_async"):
            response = await self._hqchart_provider.eval_indicator_async(
                [symbol], script, period, kline_data=kline_data
            )
        else:
            response = await loop.run_in_executor(
                None,
                self._hqchart_provider.eval_indicator,
                [symbol], script, period, 0, kline_data,
            )
        return response.get("result", {}).get(symbol) if response else None

    async def eval_batch(
        self,
        formula: str,
        symbols: List[str],
        period: str = "1d",
        args: Optional[dict] = None,
        context: Optional[dict] = None,
    ) -> Dict[str, Any]:
        """批量公式求值（带缓存+路由）。

        Args:
            formula: 公式字符串。
            symbols: 标的代码列表。
            period: 周期，默认 ``'1d'``。
            args: 公式参数，参与缓存键计算。
            context: 可选上下文（暂不参与缓存键，仅保留扩展性）。

        Returns:
            ``{symbol: result}`` 映射；HQChart 不可用时若无法回退到 Python 引擎则抛出异常。
        """
        results: Dict[str, Any] = {}
        misses: List[str] = []

        for symbol in symbols:
            key = self._make_key(formula, symbol, period, args)
            cached = self._cache.get(key)
            if cached is not None:
                results[symbol] = cached
            else:
                misses.append(symbol)

        if not misses:
            return results

        complexity = self._analyze_complexity(formula)
        engine = self._resolve_engine({
            "complexity": complexity,
            "period": period,
            "hqchart_available": self._hqchart_available,
        })

        batch = await self._dispatch_engine_call(
            engine, "eval_batch", formula, misses, period, args, context
        )

        for symbol, value in batch.items():
            key = self._make_key(formula, symbol, period, args)
            self._cache.set(key, value)
            results[symbol] = value

        return results

    async def _eval_python_batch(
        self, formula: str, symbols: List[str], period: str, args: Optional[dict] = None,
        context: Optional[dict] = None,
    ) -> Dict[str, Any]:
        """使用 Python 公式引擎批量求值。"""
        if self._data_query is None:
            raise RuntimeError("data_query is required for Python engine batch evaluation")
        loop = asyncio.get_event_loop()

        def fetcher(s: str, p: str) -> Any:
            return self._data_query.get_kline_series(s, p)

        return await loop.run_in_executor(
            None, self._python_engine.eval_batch, formula, symbols, period, fetcher, args
        )

    async def _eval_hqchart_batch(
        self, formula: str, symbols: List[str], period: str,
        args: Optional[dict] = None, context: Optional[dict] = None,
    ) -> Dict[str, Any]:
        """使用 HQChart 引擎批量求值。"""
        if self._data_query is None:
            raise RuntimeError("data_query is required for HQChart engine batch evaluation")
        if self._hqchart_provider is None:
            raise RuntimeError("HQChart provider is not available")

        # 注入公式参数（HQChart 不支持 SetArgs，通过脚本前置赋值实现）
        script = self._inject_args_into_script(formula, args)

        loop = asyncio.get_event_loop()
        kline_data: Dict[str, List[Dict]] = {}
        for symbol in symbols:
            df = await loop.run_in_executor(
                None, self._data_query.get_kline_series, symbol, period
            )
            kline_data[symbol] = df.to_dict("records") if df is not None else []

        # 优先使用批量接口；若不存在则逐只调用
        if hasattr(self._hqchart_provider, "eval_indicator_async"):
            response = await self._hqchart_provider.eval_indicator_async(
                symbols, script, period, kline_data=kline_data
            )
            batch = response.get("result", {}) if response else {}
        else:
            logger.warning("HQChartProvider 缺少批量方法，使用单只循环")
            batch = {}
            for symbol in symbols:
                single_response = await loop.run_in_executor(
                    None,
                    self._hqchart_provider.eval_indicator,
                    [symbol], script, period, 0,
                    {symbol: kline_data.get(symbol, [])},
                )
                batch[symbol] = single_response.get("result", {}).get(symbol) if single_response else None

        # 保证返回集合包含所有请求标的
        for symbol in symbols:
            if symbol not in batch:
                batch[symbol] = None
        return batch

    def invalidate_on_minute_close(self, symbol: str, minute: int) -> int:
        """分钟闭合时，使该标的所有分钟级缓存失效。"""
        return self._cache.invalidate_on_minute_close(symbol, minute)

    def clear_all_cache(self) -> None:
        """清空所有公式缓存（日终清理）。"""
        self._cache.clear_all()
