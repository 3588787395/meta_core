"""Formula 模块：公式计算 + 金叉检测。仅与 EventBus 交互。"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, ClassVar, Dict, List, Literal, Optional, Protocol, Tuple, TYPE_CHECKING, runtime_checkable

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    from .domain import FilterSpec
    from .runtime_mode_module import PoolState
from .domain import _lookup_builtin_script, _lookup_builtin_formula_info
from .event_bus import (
    _BaseModule,
    BarComposed,
    CrossOverDetected,
    DataChanged,
    EventBus,
    FormulaEvaluated,
    PoolLoaded,
)
from .domain import load_config_table
from ._hashing import hash_dict_content, hash_object
from converters_common import safe_float

logger = logging.getLogger(__name__)


# 来自 core/formula_engine.py — 纯 Python 公式引擎

# 字段名 / 函数名映射
_FIELD_MAP = {
    "CLOSE": "close",
    "C": "close",
    "OPEN": "open",
    "O": "open",
    "HIGH": "high",
    "H": "high",
    "LOW": "low",
    "L": "low",
    "VOL": "vol",
    "V": "vol",
    "VOLUME": "vol",
}

# 表驱动：从 formula_funcs.json 加载算子配置，驱动通用 window_op/shift_op/cross_op
_FUNCS_CFG = load_config_table("formula_funcs").get("funcs", [])

_TOKEN_RE = re.compile(
    r"(?i)(:=|>=|<=|==|!=|[<>=:\-+*/(),;]|[A-Za-z_][A-Za-z0-9_]*|\d+\.?\d*)"
)


# 简易 LRU 缓存
_CACHE_MAXSIZE = 1000


class _LRUCache:
    """基于 OrderedDict 的 LRU 缓存。"""

    def __init__(self, maxsize: int = _CACHE_MAXSIZE):
        self._data: OrderedDict = OrderedDict()
        self._maxsize = maxsize

    def get(self, key: str) -> Optional[Any]:
        if key in self._data:
            self._data.move_to_end(key)
            return self._data[key]
        return None

    def set(self, key: str, value: Any) -> None:
        if key in self._data:
            self._data.move_to_end(key)
        self._data[key] = value
        if len(self._data) > self._maxsize:
            self._data.popitem(last=False)


# 向量化 TDX 函数实现
def _to_series(x: Any) -> pd.Series:
    """将输入统一转为 pd.Series，便于使用 rolling/shift。"""
    if isinstance(x, pd.Series):
        return x
    return pd.Series(np.asarray(x, dtype=float))


# 通用算子（表驱动）：window_op / shift_op / cross_op


def window_op(series: Any, n: int, agg_method: str,
              agg_override: Optional[str] = None,
              agg_kwargs: Optional[Dict[str, Any]] = None) -> pd.Series:
    """通用滚动窗口算子（深表驱动）。"""
    s = _to_series(series)
    n = int(n)
    kwargs = agg_kwargs or {}
    if n == 0:
        roller = s.expanding(min_periods=1)
    elif n < 0:
        return s
    else:
        roller = s.rolling(window=n, min_periods=1)
    method = getattr(roller, agg_method, None)
    if method is None:
        return s
    return method(**kwargs)


def shift_op(series: Any, n: int) -> pd.Series:
    """通用偏移算子，替代 REF。"""
    return _to_series(series).shift(int(n))


def cross_op(line1: Any, line2: Any, direction: str = "above") -> pd.Series:
    """通用穿越检测算子，替代 CROSS。"""
    sa, sb = _to_series(line1), _to_series(line2)
    if direction == "above":
        return (sa.shift(1) <= sb.shift(1)) & (sa > sb)
    else:
        return (sa.shift(1) >= sb.shift(1)) & (sa < sb)


# 递推与逐元素算子：EMA / SMA / ABS / MAX / MIN / IF
def _ewm_core(series: Any, n: int, alpha_fn: Callable[[int], float]) -> pd.Series:
    """通用指数加权核心：按 ``alpha_fn(n)`` 计算 alpha，调用 pandas ``ewm``。"""
    s = _to_series(series)
    n = int(n)
    if n <= 0 or len(s) == 0:
        return s
    alpha = alpha_fn(n)
    return s.ewm(alpha=alpha, adjust=False).mean()


def ema_op(series: Any, n: int) -> pd.Series:
    """指数移动平均 EMA(X, N)。"""
    return _ewm_core(series, n, lambda n: 2.0 / (n + 1))


def sma_op(series: Any, n: int, m: int) -> pd.Series:
    """加权移动平均 SMA(X, N, M)。"""
    m = int(m)
    return _ewm_core(series, n, lambda n: m / n)


def abs_op(series: Any) -> Any:
    """绝对值 ABS(X)。"""
    return np.abs(series)


_ELEMENTWISE_BIN = {"MAX": np.maximum, "MIN": np.minimum}


def max_op(a: Any, b: Any) -> Any:
    """二元取大 MAX(A, B)（两个值取大，非 HHV 滚动窗口最大）。"""
    return _ELEMENTWISE_BIN["MAX"](a, b)


def min_op(a: Any, b: Any) -> Any:
    """二元取小 MIN(A, B)（两个值取小，非 LLV 滚动窗口最小）。"""
    return _ELEMENTWISE_BIN["MIN"](a, b)


def if_op(cond: Any, a: Any, b: Any) -> Any:
    """条件判断 IF(cond, A, B)：cond 为真取 A，否则取 B。"""
    result = np.where(cond, a, b)
    if isinstance(a, pd.Series):
        return pd.Series(result, index=a.index)
    if isinstance(b, pd.Series):
        return pd.Series(result, index=b.index)
    return result


def sar_op(high: Any, low: Any, n: int, step: int, maxp: int) -> pd.Series:
    """抛物线转向 SAR(N, STEP, MAXP)。"""
    h = _to_series(high).astype(float).values
    l = _to_series(low).astype(float).values
    length = len(h)
    if length == 0:
        return pd.Series(dtype=float)

    af_step = max(float(step) / 100.0, 1e-6)
    af_max = max(float(maxp) / 100.0, af_step)
    # n 用于首根极值窗口（TDX 惯例），至少 1
    lookback = max(int(n), 1)

    sar = np.empty(length, dtype=float)
    # 初始趋势由前 lookback 根高低点决定
    if length >= lookback:
        init_high = h[:lookback].max()
        init_low = l[:lookback].min()
    else:
        init_high = h.max()
        init_low = l.min()
    rising = h[-1] >= init_high if length > 0 else True

    sar[0] = l[0] if rising else h[0]
    ep = h[0] if rising else l[0]
    af = af_step

    for i in range(1, length):
        sar[i] = sar[i - 1] + af * (ep - sar[i - 1])
        if rising:
            # 限制 SAR 不超过前两根最低点
            limit = min(l[i - 1], l[i]) if i >= 1 else l[i]
            if sar[i] > limit:
                sar[i] = limit
            # 趋势反转
            if l[i] < sar[i]:
                rising = False
                sar[i] = ep
                ep = l[i]
                af = af_step
            else:
                if h[i] > ep:
                    ep = h[i]
                    af = min(af + af_step, af_max)
        else:
            limit = max(h[i - 1], h[i]) if i >= 1 else h[i]
            if sar[i] < limit:
                sar[i] = limit
            if h[i] > sar[i]:
                rising = True
                sar[i] = ep
                ep = h[i]
                af = af_step
            else:
                if l[i] < ep:
                    ep = l[i]
                    af = min(af + af_step, af_max)

    return pd.Series(sar, index=_to_series(high).index)


_CASTERS = {"int": int, "series": _to_series}


def _dispatch_func(name: str, args: List[Any], ctx: Optional[Dict[str, Any]] = None) -> Any:
    """按表配置分派到通用算子（深表驱动：handler 反射调用 + arg_spec 参数提取入表）。"""
    cfg = _FUNCS_CFG.get(name)
    if cfg is None:
        return None
    # 按 arg_spec 从 args 提取并转换参数（idx/cast/default 全部入表）
    extracted_args: List[Any] = []
    for spec in cfg["arg_spec"]:
        idx = spec["idx"]
        val = args[idx] if idx < len(args) else spec.get("default")
        cast = spec.get("cast")
        val = _CASTERS.get(cast, lambda x: x)(val)
        extracted_args.append(val)
    # 从 cfg 提取额外 kwargs（cfg_kwargs 列表声明需透传的 cfg 字段名）
    extra_kwargs: Dict[str, Any] = {}
    for key in cfg.get("cfg_kwargs", []):
        if key in cfg:
            extra_kwargs[key] = cfg[key]
    # 处理 direction_field：cross 算子方向参数（字段值指向承载 direction 的 cfg key）
    direction_field = cfg.get("direction_field")
    if direction_field:
        extra_kwargs["direction"] = cfg.get(direction_field, "above")
    # 注入需要从命名空间提取的上下文字段（如 SAR 的 high/low）
    ctx_fields = cfg.get("context_fields")
    if ctx_fields and isinstance(ctx, dict):
        extracted_args = [ctx.get(f) for f in ctx_fields] + extracted_args
    # 反射调用 handler
    handler_func = globals().get(cfg["handler"])
    if handler_func is None:
        return None
    return handler_func(*extracted_args, **extra_kwargs)


# 公式分词与表达式解析
def _tokenize(formula: str) -> List[str]:
    """简单分词器：识别标识符、数字、运算符和分隔符。"""
    # 移除注释：{} 块注释 与 // 行注释
    formula = re.sub(r"\{[^}]*\}", " ", formula)
    formula = re.sub(r"//[^\n]*", " ", formula)
    return _TOKEN_RE.findall(formula)


def _split_statements(tokens: List[str]) -> List[List[str]]:
    """按 ';' 将 token 序列拆分为语句。"""
    statements: List[List[str]] = []
    current: List[str] = []
    for tok in tokens:
        if tok == ";":
            if current:
                statements.append(current)
                current = []
        else:
            current.append(tok)
    if current:
        statements.append(current)
    return statements


class _ExprParser:
    """递归下降表达式解析器，将 TDX 表达式转为带括号的 Python 表达式字符串。"""

    def __init__(self, tokens: List[str]):
        self.tokens = tokens
        self.pos = 0

    def _peek(self) -> Optional[str]:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def _peek_upper(self) -> Optional[str]:
        t = self._peek()
        return t.upper() if t is not None else None

    def _consume(self) -> str:
        t = self.tokens[self.pos]
        self.pos += 1
        return t

    def parse(self) -> str:
        return self._parse_or()

    # OR
    def _parse_or(self) -> str:
        left = self._parse_and()
        while self._peek_upper() == "OR":
            self._consume()
            right = self._parse_and()
            left = f"({left}) | ({right})"
        return left

    # AND
    def _parse_and(self) -> str:
        left = self._parse_not()
        while self._peek_upper() == "AND":
            self._consume()
            right = self._parse_not()
            left = f"({left}) & ({right})"
        return left

    # NOT
    def _parse_not(self) -> str:
        if self._peek_upper() == "NOT":
            self._consume()
            operand = self._parse_not()
            return f"~({operand})"
        return self._parse_comparison()

    # 比较
    def _parse_comparison(self) -> str:
        left = self._parse_additive()
        t = self._peek()
        if t in (">", ">=", "<", "<=", "==", "="):
            op = "==" if t == "=" else t
            self._consume()
            right = self._parse_additive()
            return f"({left} {op} {right})"
        return left

    # 加减
    def _parse_additive(self) -> str:
        left = self._parse_multiplicative()
        while True:  # noqa: event-driver  # 递归下降解析器消费 +/- token（受 token 流约束，非轮询）
            t = self._peek()
            if t in ("+", "-"):
                self._consume()
                right = self._parse_multiplicative()
                left = f"({left} {t} {right})"
            else:
                break
        return left

    # 乘除
    def _parse_multiplicative(self) -> str:
        left = self._parse_unary()
        while True:  # noqa: event-driver  # 递归下降解析器消费 */ token（受 token 流约束，非轮询）
            t = self._peek()
            if t in ("*", "/"):
                self._consume()
                right = self._parse_unary()
                left = f"({left} {t} {right})"
            else:
                break
        return left

    # 一元正负号
    def _parse_unary(self) -> str:
        t = self._peek()
        if t in ("+", "-"):
            self._consume()
            operand = self._parse_unary()
            return f"({t}{operand})"
        return self._parse_primary()

    # 原子：括号、函数、字段、变量、数字
    def _parse_primary(self) -> str:
        t = self._peek()
        if t is None:
            raise ValueError("表达式意外结束")
        if t == "(":
            self._consume()
            expr = self.parse()
            if self._peek() != ")":
                raise ValueError("缺少右括号 ')'")
            self._consume()
            return f"({expr})"

        self._consume()
        upper = t.upper()

        # 数字
        if re.match(r"^\d", t):
            return t

        # 函数调用（表驱动：查 _FUNCS_CFG 分派到 _dispatch_func）
        if upper in _FUNCS_CFG and self._peek() == "(":
            cfg = _FUNCS_CFG[upper]
            self._consume()  # '('
            args: List[str] = []
            if self._peek() != ")":
                args.append(self.parse())
                while self._peek() == ",":
                    self._consume()
                    args.append(self.parse())
            if self._peek() != ")":
                raise ValueError("函数调用缺少右括号 ')'")
            self._consume()
            ctx_fields = cfg.get("context_fields")
            if ctx_fields:
                ctx_literal = "{" + ", ".join(f'"{f}": {f}' for f in ctx_fields) + "}"
                return f'_dispatch_func("{upper}", [{", ".join(args)}], {ctx_literal})'
            return f'_dispatch_func("{upper}", [{", ".join(args)}])'

        # 字段名
        if upper in _FIELD_MAP:
            return _FIELD_MAP[upper]

        # 变量或标识符
        return t


def _parse_statement(stmt: List[str]) -> Tuple[str, Optional[str], Any]:
    """解析单条语句，返回 (kind, name, compiled_code)。"""
    depth = 0
    assign_idx = -1
    assign_tok = ""
    for i, tok in enumerate(stmt):
        if tok == "(":
            depth += 1
        elif tok == ")":
            depth -= 1
        elif depth == 0 and tok in (":=", ":"):
            assign_idx = i
            assign_tok = tok
            break

    if assign_idx >= 0:
        if assign_idx == 0:
            raise ValueError(f"赋值语句缺少左侧变量名: {' '.join(stmt)}")
        name = stmt[assign_idx - 1]
        rhs_tokens = stmt[assign_idx + 1 :]
        if not rhs_tokens:
            raise ValueError(f"赋值语句右侧为空: {' '.join(stmt)}")
        # 剥离 TDX 绘图修饰符：顶层逗号后跟随 COLORSTICK/VOLSTICK/NODRAW/LINETHICK*/COLOR* 等，
        # 这些是显示指令而非表达式的一部分。在第一个顶层逗号（depth==0）处截断。
        expr_tokens = []
        depth = 0
        for tok in rhs_tokens:
            if tok == "(":
                depth += 1
                expr_tokens.append(tok)
            elif tok == ")":
                depth -= 1
                expr_tokens.append(tok)
            elif tok == "," and depth == 0:
                break
            else:
                expr_tokens.append(tok)
        if not expr_tokens:
            raise ValueError(f"赋值语句表达式为空（剥离绘图修饰符后）: {' '.join(stmt)}")
        kind = "assign" if assign_tok == ":=" else "output"
        expr = _ExprParser(expr_tokens).parse()
        return kind, name, compile(expr, "<formula>", "eval")

    # 无赋值符，整体作为表达式（同样剥离顶层绘图修饰符）
    expr_tokens_clean = []
    depth = 0
    for tok in stmt:
        if tok == "(":
            depth += 1
            expr_tokens_clean.append(tok)
        elif tok == ")":
            depth -= 1
            expr_tokens_clean.append(tok)
        elif tok == "," and depth == 0:
            break
        else:
            expr_tokens_clean.append(tok)
    expr = _ExprParser(expr_tokens_clean).parse()
    return "output", None, compile(expr, "<formula>", "eval")


# 公式引擎协议（Task 4 / RULES.md 第 85 条）
@runtime_checkable
class IFormulaEngine(Protocol):
    """公式引擎协议：统一 ``CompiledFormula`` / ``PythonFormulaEngine`` /
    ``FormulaEngine`` / ``FormulaRouter`` 的 ``eval`` / ``eval_outvars`` /
    ``eval_series`` / ``eval_batch`` 方法契约。

    Protocol 是结构化的，sync 与 async 变体均兼容（duck typing）：
    ``FormulaRouter`` 的 async 方法满足本协议（方法名存在即结构匹配），
    其余 3 个类为 sync 实现。具体类按各自上下文实现方法签名，
    但方法名与返回值语义须一致。

    新增公式引擎（如 JS 引擎）SHALL 实现本协议，并在
    ``FormulaRouter._ENGINE_DISPATCH`` 中登记分派条目，零行
    ``FormulaRouter`` 改动（详见 spec 迭代 2 Scenario: 新增公式引擎）。
    """

    def eval(self, *args: Any, **kwargs: Any) -> Any:
        """公式求值：单股条件/指标求值。"""
        ...

    def eval_outvars(self, *args: Any, **kwargs: Any) -> Optional[Dict[str, Any]]:
        """公式求值：返回全部输出变量末值字典。"""
        ...

    def eval_series(self, *args: Any, **kwargs: Any) -> Optional[Dict[str, Any]]:
        """公式序列求值：返回输出变量的最近 N 个值序列。"""
        ...

    def eval_batch(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        """批量求值：返回 ``{symbol: eval_result}`` 映射。"""
        ...


# 编译产物
@dataclass
class CompiledFormula:
    """已编译的公式，保存按顺序执行的语句（赋值/输出）及其 code 对象。

    impl IFormulaEngine（Task 4.2 / RULES.md 第 85 条）：结构化实现
    ``eval`` / ``eval_outvars`` / ``eval_series``，作为 ``PythonFormulaEngine``
    的编译产物求值核心。``eval_batch`` 由 ``PythonFormulaEngine`` 在引擎层封装。
    """

    formula: str
    statements: List[Tuple[str, Optional[str], Any]] = field(default_factory=list)

    def __init__(self, formula: str):
        self.formula = formula
        self.statements = []
        tokens = _tokenize(formula)
        for stmt in _split_statements(tokens):
            kind, name, code = _parse_statement(stmt)
            self.statements.append((kind, name, code))

    @staticmethod
    def _last_value(value: Any) -> Any:
        """取序列最后一个标量值；NaN 转为 None。"""
        if isinstance(value, pd.Series):
            if len(value) == 0:
                return None
            v = value.iloc[-1]
        elif isinstance(value, np.ndarray):
            if len(value) == 0:
                return None
            v = value[-1]
        else:
            v = value

        if isinstance(v, (float, np.floating)) and np.isnan(v):
            return None
        if isinstance(v, np.bool_):
            return bool(v)
        return v

    def _eval_core(self, bars: pd.DataFrame, args: Optional[dict] = None) -> Optional[OrderedDict]:
        """核心求值：构建命名空间并执行全部语句，返回输出变量 OrderedDict。"""
        namespace = _build_namespace(bars)
        if namespace is None:
            return None
        namespace.update(_FUNC_NAMESPACE)
        # 注入公式参数（如 SHORT/LONG/MID），值为数字
        if args:
            for k, v in args.items():
                namespace[k] = v
                namespace[k.upper()] = v

        outputs: OrderedDict = OrderedDict()
        for kind, name, code in self.statements:
            try:
                val = eval(code, {"__builtins__": {}}, namespace)
            except Exception as e:
                logger.debug("公式求值异常 %s: %s", self.formula, e)
                return None

            if kind == "assign":
                namespace[name] = val
            else:
                outputs[name] = val
                # TDX 语义：输出变量(:)同样可在后续语句中引用
                if name is not None:
                    namespace[name] = val

        return outputs if outputs else None

    def eval(self, bars: pd.DataFrame, args: Optional[dict] = None) -> Any:
        """对单只股票的 K 线数据进行求值（异构返回）。"""
        outputs = self._eval_core(bars, args)
        if outputs is None:
            return None

        names = list(outputs.keys())
        if len(names) == 1:
            name, val = names[0], outputs[names[0]]
            if name is None or name.upper() == "XG":
                v = self._last_value(val)
                return bool(v) if v is not None else False
            return self._last_value(val)

        return {name: self._last_value(val) for name, val in outputs.items()}

    def eval_outvars(self, bars: pd.DataFrame, args: Optional[dict] = None) -> Optional[Dict[str, Any]]:
        """对单只股票的 K 线数据进行求值，返回全部输出变量末值字典。"""
        outputs = self._eval_core(bars, args)
        if outputs is None:
            return None
        result: OrderedDict = OrderedDict()
        for name, val in outputs.items():
            key = "XG" if name is None else name
            result[key] = self._last_value(val)
        return result

    def eval_series(self, bars: pd.DataFrame, args: Optional[dict] = None, lookback: int = 5) -> Optional[Dict[str, Any]]:
        """对单只股票的 K 线数据进行求值，返回全部输出变量的最近 lookback 个值序列。"""
        outputs = self._eval_core(bars, args)
        if outputs is None:
            return None
        result: OrderedDict = OrderedDict()
        for name, val in outputs.items():
            key = "XG" if name is None else name
            # pd.Series/np.ndarray/list/tuple 共享末 N 值提取骨架（3 分支合并）。
            seq = val.values if isinstance(val, pd.Series) else val
            if isinstance(seq, (np.ndarray, list, tuple)):
                n = min(lookback, len(seq))
                result[key] = ([float(x) if x is not None and not (isinstance(x, float) and np.isnan(x)) else None
                                for x in seq[-n:]] if n > 0 else [])
            else:
                v = self._last_value(val)
                result[key] = [v] if v is not None else []
        return result


# 数据归一化
_FUNC_NAMESPACE = {
    "np": np,
    "_dispatch_func": _dispatch_func,
}

_REQUIRED_COLS = ("close", "open", "high", "low")

_COL_CANDIDATES = {
    "close": ["close", "CLOSE", "C"],
    "open": ["open", "OPEN", "O"],
    "high": ["high", "HIGH", "H"],
    "low": ["low", "LOW", "L"],
    "vol": ["vol", "volume", "VOL", "V", "VOLUME"],
}


def _build_namespace(bars: pd.DataFrame) -> Optional[Dict[str, Any]]:
    """将 bars 归一化为字段 Series，构建求值命名空间。"""
    if bars is None or len(bars) == 0:
        return None

    namespace: Dict[str, Any] = {}
    for key, candidates in _COL_CANDIDATES.items():
        for col in candidates:
            if col in bars.columns:
                namespace[key] = pd.Series(
                    np.asarray(bars[col].values, dtype=float), index=bars.index
                )
                break

    for req in _REQUIRED_COLS:
        if req not in namespace:
            return None

    if "vol" not in namespace:
        namespace["vol"] = pd.Series(
            np.zeros(len(namespace["close"])), index=bars.index
        )

    return namespace


# 公式引擎主类
class PythonFormulaEngine:
    """纯 Python 公式引擎（轻量级、numpy/pandas 向量化）。

    impl IFormulaEngine（Task 4.3 / RULES.md 第 85 条）：结构化实现
    ``eval`` / ``eval_outvars`` / ``eval_batch``，并通过 ``eval_series_batch``
    提供序列批量求值。作为 ``FormulaRouter`` 的 Python 引擎实现，
    以及 ``FormulaEngine`` 的底层求值核心。
    """

    def __init__(self, data_query: Any = None):
        """初始化引擎。"""
        self.data_query = data_query
        self._compiled_cache = _LRUCache(maxsize=_CACHE_MAXSIZE)

    def _compile(self, formula: str) -> CompiledFormula:
        """将公式字符串编译为 CompiledFormula，结果缓存。"""
        cached = self._compiled_cache.get(formula)
        if cached is not None:
            return cached

        compiled = CompiledFormula(formula)
        self._compiled_cache.set(formula, compiled)
        return compiled

    def _eval_single(self, formula: str, bars: pd.DataFrame, args: Optional[dict], method_name: str) -> Any:
        """编译 + 委托 CompiledFormula.{method_name} 求值（eval/eval_outvars 同构骨架合并）。"""
        try:
            return getattr(self._compile(formula), method_name)(bars, args)
        except Exception as e:
            logger.warning("公式编译失败 %s: %s", formula, e)
            return None

    def eval(self, formula: str, bars: pd.DataFrame, args: Optional[dict] = None) -> Any:
        """对单只股票的 bars 求值（条件→bool / 单输出→标量 / 多输出→dict；失败返回 None）。"""
        return self._eval_single(formula, bars, args, "eval")

    def eval_outvars(self, formula: str, bars: pd.DataFrame, args: Optional[dict] = None) -> Optional[Dict[str, Any]]:
        """对单只股票的 bars 求值，返回全部输出变量末值字典（失败返回 None）。"""
        return self._eval_single(formula, bars, args, "eval_outvars")

    def _fetch_bars(self, symbol: str, period: str, data_fetcher) -> Optional[pd.DataFrame]:
        df: Optional[pd.DataFrame] = None
        if data_fetcher is not None:
            try:
                df = data_fetcher(symbol, period)
            except Exception as e:
                logger.debug("data_fetcher 异常 %s: %s", symbol, e)
        elif self.data_query is not None:
            try:
                df = self.data_query.fetch(symbol, period)
            except Exception:
                try:
                    df = self.data_query.get_bars(symbol, period)
                except Exception as e2:
                    logger.debug("data_query 取数异常 %s: %s", symbol, e2)
        return df

    def _eval_batch_impl(self, formula, symbols, period, data_fetcher, args, eval_call, miss_default) -> Dict[str, Any]:
        try:
            compiled = self._compile(formula)
        except Exception as e:
            logger.warning("批量公式编译失败 %s: %s", formula, e)
            return {symbol: miss_default for symbol in symbols}
        results: Dict[str, Any] = {}
        for symbol in symbols:
            df = self._fetch_bars(symbol, period, data_fetcher)
            if df is None or not isinstance(df, pd.DataFrame) or len(df) == 0:
                results[symbol] = miss_default
                continue
            try:
                results[symbol] = eval_call(compiled, df, args)
            except Exception as e:
                logger.debug("批量求值异常 %s: %s", symbol, e)
                results[symbol] = miss_default
        return results

    def eval_batch(self, formula, symbols, period="1d", data_fetcher=None, args=None) -> Dict[str, Any]:
        """批量求值：为每只标的取数据并分别求值。失败标的结果为 ``False``。"""
        return self._eval_batch_impl(formula, symbols, period, data_fetcher, args,
                                     lambda c, df, a: c.eval(df, a), False)

    def eval_series_batch(self, formula, symbols, period="1d", data_fetcher=None, args=None, lookback=5) -> Dict[str, Any]:
        """批量序列求值：返回各标的输出变量最近 lookback 个值序列。失败标的结果为 ``None``。"""
        return self._eval_batch_impl(formula, symbols, period, data_fetcher, args,
                                     lambda c, df, a: c.eval_series(df, a, lookback=lookback), None)


# 来自 core/formula.py — 公式定义（EvalContext + 有状态 FormulaEngine）


def _hash_code_bars(bars_data: Any) -> str:
    """计算单只股票的K线数据哈希，用于per-code缓存粒度。"""
    def _serialize(b):
        try:
            if isinstance(b, pd.DataFrame):
                return b.to_json(orient="records", date_format="iso")
            if isinstance(b, list):
                return json.dumps(b, sort_keys=True, ensure_ascii=False, default=str)
            return repr(b)
        except Exception:
            try:
                return repr(b)
            except Exception:
                return str(b)
    return hash_object(bars_data, serializer=_serialize)


def _get_period_bars(state: Any, period: str = "1d") -> Dict[str, Any]:
    """从 `PoolState` 提取指定周期的 code->bars 列表。"""
    if state is None:
        return {}
    state_hist = getattr(state, "bars_history", None)
    state_cur = getattr(state, "bars", None)
    state_tick = getattr(state, "latest_tick", None)
    if not isinstance(state_hist, dict):
        return {}
    # 规范化 period：5min->5m, 1min->1m, 15min->15m, 30min->30m, 60min->60m
    period_key = period
    if period.endswith("min"):
        period_key = period[:-3] + "m"
    period_hist = state_hist.get(period_key, {})
    period_cur = state_cur.get(period_key, {}) if isinstance(state_cur, dict) else {}
    if not isinstance(period_hist, dict):
        return {}
    
    result: Dict[str, Any] = {}
    all_codes = set()
    if isinstance(period_hist, dict):
        all_codes.update(period_hist.keys())
    if isinstance(period_cur, dict):
        all_codes.update(period_cur.keys())
    
    for code in all_codes:
        bars_list: List[Dict[str, Any]] = []
        hist_bars = period_hist.get(code)
        if isinstance(hist_bars, list) and hist_bars:
            bars_list.extend([dict(b) for b in hist_bars])
        cur_bar = period_cur.get(code)
        if isinstance(cur_bar, dict):
            current = {k: v for k, v in cur_bar.items() if k != "_hash"}
            if isinstance(state_tick, dict):
                tick = state_tick.get(code)
                if isinstance(tick, dict):
                    tick_close = tick.get("close")
                    current["close"] = safe_float(tick_close, current.get("close"))
            bars_list.append(current)
        if bars_list:
            result[code] = bars_list
    return result


@dataclass
class EvalContext:
    """公式求值上下文。"""

    mode: Literal["live", "replay", "simulation"]
    bar_hash: str
    bars: Any
    latest_tick: Dict[str, Any]
    period: str = "1d"
    extra: Dict[str, Any] = field(default_factory=dict)


def live_context(state: PoolState, period: str = "1d") -> EvalContext:
    """构造实盘模式求值上下文。"""
    return EvalContext(
        mode="live",
        bar_hash=state.bar_hash(),
        bars=_get_period_bars(state, period),
        latest_tick=state.latest_tick,
        period=period,
    )


def _mock_context(
    mode: str, state: PoolState, bars: Dict[str, Dict[str, Any]], bar_hash: str = ""
) -> EvalContext:
    """构造回放/仿真模式求值上下文（两模式仅 mode 字符串不同，bar_hash 缺省自动生成）。"""
    return EvalContext(
        mode=mode,
        bar_hash=bar_hash or hash_dict_content(bars),
        bars=bars,
        latest_tick=state.latest_tick,
    )


def replay_context(
    state: PoolState, bars: Dict[str, Dict[str, Any]], bar_hash: str = ""
) -> EvalContext:
    """构造回放模式求值上下文。若未提供 `bar_hash`，则根据 `bars` 内容自动生成。"""
    return _mock_context("replay", state, bars, bar_hash)


def simulation_context(
    state: PoolState, mock_bars: Dict[str, Dict[str, Any]], bar_hash: str = ""
) -> EvalContext:
    """构造仿真模式求值上下文。`mock_bars` 由 mock 数据生成器当前 tick 提供。"""
    return _mock_context("simulation", state, mock_bars, bar_hash)


class FormulaEngine:
    """统一公式引擎（有状态，依赖 ``PoolState``）。

    impl IFormulaEngine（Task 4.4 / RULES.md 第 85 条）：结构化实现
    ``eval`` / ``eval_series``（经 ``_cached_eval`` 统一缓存），并经
    ``_python_engine.eval_batch`` 提供 batch 能力。``eval_outvars`` 由
    ``PythonFormulaEngine`` 直接提供，本层在 ``_eval_formula`` 中转调。

    属性 ≤ 5、方法 ≤ 6、事件 ≤ 3：
      - 属性：state, _python_engine, _logger
      - 方法：__init__, eval, eval_scalar, _cached_eval, _eval_formula, _cache_key
      - 事件：本实现保持无事件发布（0 个），满足 ≤ 3 约束

    I54：缓存逻辑（键构造/读/写）收敛到 _cached_eval，formula 与 scalar 路径共享，
    消除 edge_executor._eval_scalar_path 中重复的 cache_key 构造与 formula_results 读写。
    """

    def __init__(self, state: PoolState, data_query: Any = None):
        self.state = state
        self._data_query = data_query
        self._python_engine = PythonFormulaEngine()
        self._logger = logging.getLogger(__name__)

    def eval(self, spec: FilterSpec, codes: List[str], ctx: EvalContext) -> Dict[str, Any]:
        """公式求值路径：委托 _eval_formula，缓存经 _cached_eval 统一管理。

        I53：filter_type 降级为元数据，evaluator_type 为唯一运行期分派键。
        I54：缓存逻辑收敛到 _cached_eval，与 scalar 路径共享。
        """
        return self._cached_eval(
            spec, codes, ctx,
            lambda c, x: self._eval_formula(spec, c, x),
            writeback=True,
        )

    def eval_scalar(
        self,
        spec: FilterSpec,
        codes: List[str],
        ctx: EvalContext,
        evaluator_fn: Callable[[List[str], EvalContext], Dict[str, Any]],
    ) -> Dict[str, Any]:
        """标量求值路径：委托外部 evaluator_fn，缓存经 _cached_eval 统一管理。

        I54：scalar 路径缓存收敛到 FormulaEngine，消除 edge_executor 中
        重复的 cache_key 构造与 formula_results 读写。evaluator_fn 接收
        (codes, ctx)，返回 {code: bool}。writeback=False（标量结果不写回 tick 列）。
        """
        return self._cached_eval(spec, codes, ctx, evaluator_fn, writeback=False)

    def _cached_eval(
        self,
        spec: FilterSpec,
        codes: List[str],
        ctx: EvalContext,
        evaluator_fn: Callable[[List[str], EvalContext], Dict[str, Any]],
        writeback: bool,
    ) -> Dict[str, Any]:
        """统一缓存求值：per-code 粒度缓存。"""
        formula_ref = spec.formula_ref
        period = getattr(ctx, 'period', '1d') or '1d'

        if not formula_ref:
            return evaluator_fn(codes, ctx)

        result: Dict[str, Any] = {}
        codes_to_eval: List[str] = []

        def _code_bar_hash(code: str) -> str:
            code_bars = ctx.bars.get(code) if hasattr(ctx, 'bars') and isinstance(ctx.bars, dict) else None
            if code_bars is None:
                tick = ctx.latest_tick.get(code) if hasattr(ctx, 'latest_tick') and isinstance(ctx.latest_tick, dict) else None
                code_bars = tick
            return _hash_code_bars(code_bars)

        for code in codes:
            code_bar_hash = _code_bar_hash(code)
            cache_key = (formula_ref, code, period, code_bar_hash)
            cached_value = self.state.formula_results.get(cache_key)
            if cached_value is not None:
                result[code] = cached_value
                continue
            codes_to_eval.append(code)

        if codes_to_eval:
            new_results = evaluator_fn(codes_to_eval, ctx)
            for code in codes_to_eval:
                value = new_results.get(code)
                result[code] = value
                code_bar_hash = _code_bar_hash(code)
                cache_key = (formula_ref, code, period, code_bar_hash)
                self.state.formula_results[cache_key] = value

                if writeback:
                    tick = self.state.latest_tick.get(code)
                    if isinstance(tick, dict):
                        tick[formula_ref] = value

        return result

    def _eval_formula(
        self, spec: FilterSpec, codes: List[str], ctx: EvalContext
    ) -> Dict[str, Any]:
        """调用底层 Python 公式引擎逐只求值（薄包装，委托 _eval_formula_core）。"""
        formula_ref = spec.formula_ref if spec else ""
        return self._eval_formula_core(formula_ref, codes, ctx, spec, series=False)

    def _eval_formula_core(self, formula_ref: str, codes: List[str], ctx: EvalContext,
                          spec: Optional[FilterSpec] = None, lookback: Optional[int] = None,
                          series: bool = False) -> Dict[str, Any]:
        """统一公式求值核心：7 步同构骨架（标量/序列共用），仅步骤 6（eval_batch vs
        eval_series_batch）与步骤 7（{code: scalar} vs batch）随 series 分派。"""
        # 步骤 1：提取 formula_ref
        formula = formula_ref or ""
        if not formula:
            return {code: None for code in codes}

        # 步骤 2：查 builtin（取 script / eval_field）
        builtin_info = _lookup_builtin_formula_info(formula)
        builtin_script = builtin_info.get("script", "") if builtin_info else ""
        eval_field = builtin_info.get("eval_field", "") if builtin_info else ""
        if builtin_script:
            formula = builtin_script

        # 步骤 3：解析 period（spec.formula_period 优先，其次 builtin 默认）
        period = getattr(ctx, 'period', '1d') or '1d'
        if spec is not None and spec.formula_period:
            period = spec.formula_period
        elif builtin_info and builtin_info.get("period"):
            period = builtin_info["period"]

        # 步骤 4：合并公式参数（spec.formula_args 覆盖 builtin 默认值）
        formula_args: Dict[str, Any] = {}
        if builtin_info and builtin_info.get("args"):
            for arg in builtin_info["args"]:
                name = arg.get("name")
                if name:
                    formula_args[name] = arg.get("value")
        if spec is not None and getattr(spec, "formula_args", None):
            formula_args.update(spec.formula_args)

        # 步骤 5：定义 fetcher（_data_query 优先，否则从 ctx.bars/latest_tick 取）
        if self._data_query is not None:
            def fetcher(symbol: str, p: str) -> pd.DataFrame | None:
                return self._data_query.get_kline_series(symbol, p or period)
        else:
            def fetcher(symbol: str, p: str) -> pd.DataFrame | None:
                bar = ctx.bars.get(symbol) if hasattr(ctx, 'bars') and isinstance(ctx.bars, dict) else None
                if bar is None:
                    tick = ctx.latest_tick.get(symbol) if hasattr(ctx, 'latest_tick') and isinstance(ctx.latest_tick, dict) else None
                    if isinstance(tick, dict):
                        bar = tick
                if isinstance(bar, pd.DataFrame):
                    return bar
                if isinstance(bar, dict):
                    return pd.DataFrame([bar])
                if isinstance(bar, list):
                    return pd.DataFrame(bar)
                return None

        # 步骤 6：调用 engine（series → eval_series_batch，标量 → eval_batch）
        try:
            if series:
                return self._python_engine.eval_series_batch(
                    formula, codes, period=period, data_fetcher=fetcher,
                    args=formula_args or None, lookback=lookback,
                )
            batch = self._python_engine.eval_batch(
                formula, codes, period=period, data_fetcher=fetcher, args=formula_args or None,
            )
        except Exception as exc:
            self._logger.debug("公式求值异常: %s", exc)
            return {code: None for code in codes}

        # 步骤 7：转换结果（标量提取 eval_field 返回 {code: scalar}；序列已在步骤 6 返回 batch）
        result = {}
        for code in codes:
            val = batch.get(code)
            if isinstance(val, dict) and eval_field:
                val = val.get(eval_field)
            result[code] = val
        return result

    def eval_series(self, spec: FilterSpec, codes: List[str], ctx: EvalContext, lookback: int = 5) -> Dict[str, Any]:
        """公式序列求值路径：返回输出变量的最近lookback个值序列，用于cross/inflection检测。"""
        return self._cached_eval(
            spec, codes, ctx,
            lambda c, x: self._eval_formula_series(spec.formula_ref, c, x, spec, lookback=lookback),
            writeback=False,
        )

    def _eval_formula_series(
        self, formula_ref: str, codes: List[str], ctx: EvalContext, spec: Optional[FilterSpec] = None, lookback: int = 5
    ) -> Dict[str, Any]:
        """调用底层 Python 公式引擎逐只求值，返回序列数据（薄包装，委托 _eval_formula_core）。"""
        return self._eval_formula_core(formula_ref, codes, ctx, spec, lookback=lookback, series=True)


# 来自 core/formula_router.py — 公式路由器


# Protocol 接口定义（替代跨层 services import，构造函数注入）
class IDataQuery(Protocol):
    """数据查询接口（替代 services.data.DataQuery）。"""

    def get_kline_series(self, symbol: str, period: str, *args: Any, **kwargs: Any) -> Any: ...


class IFormulaCache(Protocol):
    """公式缓存接口（替代 services.formula_cache.FormulaCache）。"""

    def make_key(
        self,
        symbol: str,
        period: str,
        formula_hash: str,
        args_hash: Optional[str] = None,
        minute: Optional[int] = None,
    ) -> str: ...

    def get(self, key: Any) -> Any: ...

    def set(self, key: Any, value: Any, ttl: Optional[int] = None) -> None: ...

    def clear_all(self) -> None: ...

    def invalidate_on_minute_close(self, symbol: str, minute: int) -> int: ...


class IHQChartProvider(Protocol):
    """HQChart 数据源接口（替代 services.providers.hqchart_provider.HQChartProvider）。"""

    def is_ready(self) -> bool: ...

    def eval_indicator(
        self,
        codes: List[str],
        formula_text: str,
        period: str,
        sorttype: int = 0,
        kline_data: Optional[Dict[str, Any]] = None,
    ) -> Any: ...


class _InMemoryCache:
    """默认内存缓存（无 TTL），当未注入 IFormulaCache 时作为兜底。"""

    def __init__(self) -> None:
        self._data: Dict[Any, Any] = {}

    def make_key(
        self,
        symbol: str,
        period: str,
        formula_hash: str,
        args_hash: Optional[str] = None,
        minute: Optional[int] = None,
    ) -> str:
        fh = formula_hash if formula_hash is not None else ""
        ah = args_hash if args_hash is not None else ""
        if minute is not None:
            return f"{symbol}:{minute}:{period}:{fh}:{ah}"
        return f"{symbol}:{period}:{fh}:{ah}"

    def get(self, key: Any) -> Any:
        return self._data.get(key)

    def set(self, key: Any, value: Any, ttl: Optional[int] = None) -> None:
        self._data[key] = value

    def clear_all(self) -> None:
        self._data.clear()

    def invalidate_on_minute_close(self, symbol: str, minute: int) -> int:
        prefix = f"{symbol}:{minute}:"
        tag = f":{minute}:"
        removed = 0
        for key in list(self._data.keys()):
            if isinstance(key, str) and (key.startswith(prefix) or tag in key):
                self._data.pop(key, None)
                removed += 1
        return removed

# config/data_pipeline.json 路径（core/ → 上一级 → config/）
_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "data" / "data_pipeline.json"

# config/formula_routing.json 路径（引擎路由决策规则表）
_ROUTING_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "data" / "formula_routing.json"

# 运算符、分隔符与逻辑关键字集合
_OPERATORS = frozenset({
    ":=", ":", ";", "(", ")", ",",
    "+", "-", "*", "/",
    "<", ">", "<=", ">=", "=", "==", "!=",
})
_LOGIC_WORDS = frozenset({"AND", "OR", "NOT"})

# HQChart provider 求值方法优先级表（表驱动：消除方法可用性 4 层 elif）。
# 顺序即优先级：先 outvars（返回全部输出变量），后 indicator（仅首个标量）；
# 先 _async（原生协程），后同步（经 run_in_executor 调度）。
_OUTVARS_METHOD_PRIORITY = [
    "eval_indicator_outvars_async",
    "eval_indicator_outvars",
    "eval_indicator_async",
    "eval_indicator",
]


class FormulaRouter:
    """公式路由器：按复杂度与周期选择执行引擎，并管理结果缓存。

    impl IFormulaEngine（Task 4.5 / RULES.md 第 85 条）：结构化实现
    ``eval`` / ``eval_outvars`` / ``eval_batch``（均为 async）。引擎分派
    通过 ``_ENGINE_DISPATCH`` 表驱动查表，禁止 if/elif engine_type 链
    （Task 5 / spec 迭代 2 Scenario: 公式路由分派）。
    """

    # Task 5.1 — 表驱动引擎分派表（spec 迭代 2 / RULES.md 第 16 条）。
    _ENGINE_DISPATCH: Dict[str, Dict[str, str]] = {
        "python": {
            "eval": "_eval_python",
            "eval_outvars": "_eval_python_outvars",
            "eval_batch": "_eval_python_batch",
        },
        "hqchart": {
            "eval": "_eval_hqchart",
            "eval_outvars": "_eval_hqchart_outvars",
            "eval_batch": "_eval_hqchart_batch",
        },
    }

    def __init__(
        self,
        data_query: Optional[IDataQuery] = None,
        hqchart_provider: Optional[IHQChartProvider] = None,
        python_engine: Optional[PythonFormulaEngine] = None,
        cache: Optional[IFormulaCache] = None,
        formula_cache: Optional[IFormulaCache] = None,
        bus: Optional[Any] = None,
    ):
        """初始化 FormulaRouter。

        Args:
            data_query: K 线数据查询实例（实现 ``IDataQuery``），需提供
                ``get_kline_series(symbol, period)`` 方法；为 None 时 HQChart/Python
                引擎求值会抛 ``RuntimeError``。
            hqchart_provider: HQChart 引擎封装实例（实现 ``IHQChartProvider``）；
                为 None 时 HQChart 引擎不可用（不再跨层 import services 懒加载）。
            python_engine: Python 公式引擎实例；为 None 时默认实例化 ``PythonFormulaEngine``。
            cache: 公式结果缓存实例（实现 ``IFormulaCache``）；为 None 时使用内置
                ``_InMemoryCache`` 兜底。向后兼容旧参数名。
            formula_cache: 同 ``cache`` 的新命名别名，优先于 ``cache`` 生效。
            bus: SubTask 22.2 — EventBus 实例（可选）。注入后 FormulaRouter
                可订阅 ``FormulaEvaluated`` 事件并在路由完成后再次发布
                ``FormulaEvaluated`` 事件（路由内部逻辑），实现模块间事件驱动通信。
                为 None 时退化为纯方法调用模式（向后兼容）。
        """
        self._data_query = data_query
        self._python_engine = python_engine or PythonFormulaEngine()
        # formula_cache（新命名）优先于 cache（旧命名），两者均 None 时使用内置兜底缓存
        effective_cache: Optional[IFormulaCache] = (
            formula_cache if formula_cache is not None else cache
        )
        self._cache: IFormulaCache = (
            effective_cache if effective_cache is not None else _InMemoryCache()
        )

        self._simple_functions = self._load_simple_functions()

        # 加载公式引擎路由规则表（表驱动路由决策）
        self._routing_rules = self._load_routing_rules()
        # 加载引擎方法映射表（表驱动引擎分派，消除 if engine 分支）
        self._engine_methods = self._load_engine_methods()

        # HQChart provider 仅接受注入，不再跨层 import services.providers
        self._hqchart_provider: Optional[Any] = hqchart_provider
        self._hqchart_available = False
        if self._hqchart_provider is not None:
            try:
                self._hqchart_available = bool(self._hqchart_provider.is_ready())
            except Exception:
                self._hqchart_available = False

        # 金叉/死叉检测所需的上一帧结果（按 (formula_ref, code) 索引）
        self._prev_results: Dict[tuple, tuple] = {}

        # SubTask 22.2: EventBus 实例（可选）——订阅 FormulaEvaluated 事件用于路由后
        self._bus: Optional[Any] = bus

    @staticmethod
    def _load_simple_functions() -> frozenset:
        """从 config/data_pipeline.json 加载 simple_functions 列表。"""
        cfg = load_config_table("data_pipeline")
        funcs = cfg.get("formula", {}).get("simple_functions", [])
        return frozenset(str(fn).upper() for fn in funcs)

    @staticmethod
    def _load_routing_config(key: str, default: Any) -> Any:
        """从 config/formula_routing.json 加载指定键（统一加载函数）。"""
        cfg = load_config_table("formula_routing")
        return cfg.get(key, default)

    @staticmethod
    def _load_routing_rules() -> list:
        """从 config/formula_routing.json 加载引擎路由规则表。"""
        return FormulaRouter._load_routing_config("engine_routing", [])

    @staticmethod
    def _load_engine_methods() -> dict:
        """加载引擎方法映射表：优先 ``formula_routing.json`` 的 ``engine_methods``，
        缺失或为空时回退到类级 ``_ENGINE_DISPATCH`` 默认表（Task 5.1）。

        这样 ``_ENGINE_DISPATCH`` 既是 spec 要求的表驱动分派声明，
        也是 JSON 配置缺失时的兜底，保持配置热加载能力不退化。
        """
        json_methods = FormulaRouter._load_routing_config("engine_methods", {})
        if json_methods:
            return json_methods
        # JSON 缺失/为空：回退到类级 _ENGINE_DISPATCH 默认表
        return FormulaRouter._ENGINE_DISPATCH

    def _resolve_engine(self, ctx: dict) -> str:
        """按 formula_routing.json 规则表匹配引擎。"""
        for rule in self._routing_rules:
            cond = rule["condition"]
            if cond == "default":
                return rule["engine"]
            if self._match_condition(cond, ctx):
                return rule["engine"]
        return "error"

    @staticmethod
    def _match_condition(cond: dict, ctx: dict) -> bool:
        """检查 ctx 是否匹配 condition 中的全部字段约束。"""
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
        """通用引擎方法分派器：查 ``_ENGINE_DISPATCH`` 表反射调用（Task 5.2-5.4）。

        按 ``_engine_methods``（来自 ``formula_routing.json`` 或类级
        ``_ENGINE_DISPATCH`` 兜底）的 ``[engine][method_key]`` 取方法名，
        ``getattr(self, method_name)`` 反射调用，无 if engine 分支。

        Args:
            engine: 引擎名称（"python" / "hqchart"）。
            method_key: 方法键（"eval" / "eval_outvars" / "eval_batch"）。
            *args: 透传给目标方法的 positional 参数。
            **kwargs: 透传给目标方法的关键字参数。

        Returns:
            目标方法的返回值。

        Raises:
            RuntimeError: engine 或 method_key 未在 ``_ENGINE_DISPATCH`` 表中声明。
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
        """分析公式复杂度：``simple`` / ``complex``。"""
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
            hash_object(formula),
            hash_object(args),
        )

    @staticmethod
    def _inject_args_into_script(formula: str, args: Optional[dict]) -> str:
        """将公式参数注入到脚本前部（HQChart 引擎不支持 SetArgs 时的兜底实现）。"""
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

    def _resolve_engine_for(self, formula: str, period: str) -> str:
        """统一复杂度分析 + 引擎路由（合并 eval/eval_outvars/eval_batch 同构块）。"""
        complexity = self._analyze_complexity(formula)
        return self._resolve_engine({
            "complexity": complexity,
            "period": period,
            "hqchart_available": self._hqchart_available,
        })

    async def eval(
        self,
        formula: str,
        symbol: str,
        period: str = "1d",
        args: Optional[dict] = None,
        context: Optional[dict] = None,
    ) -> Any:
        """单股公式求值（带缓存+路由）。"""
        key = self._make_key(formula, symbol, period, args)
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        engine = self._resolve_engine_for(formula, period)
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
        """单股公式求值，返回全部输出变量的末值。"""
        engine = self._resolve_engine_for(formula, period)
        return await self._dispatch_engine_call(
            engine, "eval_outvars", formula, symbol, period, args
        )

    # ── 单股求值共享原语（合并 6 处 ``_data_query is None`` 守卫 + 4 处 K 线获取骨架）──

    def _require_data_query(self, engine_name: str) -> None:
        """统一 ``_data_query is None`` 守卫 + RuntimeError 抛出。"""
        if self._data_query is None:
            raise RuntimeError(
                f"data_query is required for {engine_name} engine evaluation"
            )

    def _require_hqchart(self, formula: str, args: Optional[dict]) -> str:
        """HQChart 引擎统一前置守卫 + 脚本注入（合并 3 个 _eval_hqchart* 同构块）。"""
        self._require_data_query("HQChart")
        if self._hqchart_provider is None:
            raise RuntimeError("HQChart provider is not available")
        return self._inject_args_into_script(formula, args)

    async def _fetch_kline_df(self, symbol: str, period: str) -> "Optional[pd.DataFrame]":
        """合并 4 处单股 K 线获取骨架：``loop.run_in_executor(get_kline_series)``。"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, self._data_query.get_kline_series, symbol, period
        )

    async def _eval_hqchart_outvars(
        self, formula: str, symbol: str, period: str, args: Optional[dict] = None,
    ) -> Optional[Dict[str, Any]]:
        """使用 HQChart 引擎对单股求值，返回全部输出变量。"""
        script = self._require_hqchart(formula, args)

        df = await self._fetch_kline_df(symbol, period)
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

    async def _eval_python_core(
        self, formula: str, symbol: str, period: str, args: Optional[dict], method_name: str,
    ) -> Any:
        """Python 引擎单股求值共享骨架（合并 _eval_python / _eval_python_outvars）。"""
        self._require_data_query("Python")
        df = await self._fetch_kline_df(symbol, period)
        if df is None or df.empty:
            return None
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, getattr(self._python_engine, method_name), formula, df, args
        )

    async def _eval_python_outvars(
        self, formula: str, symbol: str, period: str, args: Optional[dict] = None,
    ) -> Optional[Dict[str, Any]]:
        """使用 Python 公式引擎对单股求值，返回全部输出变量末值字典。"""
        return await self._eval_python_core(formula, symbol, period, args, "eval_outvars")

    async def _eval_python(self, formula: str, symbol: str, period: str, args: Optional[dict] = None) -> Any:
        """使用 Python 公式引擎对单股求值。"""
        return await self._eval_python_core(formula, symbol, period, args, "eval")

    async def _eval_hqchart(self, formula: str, symbol: str, period: str, args: Optional[dict] = None) -> Any:
        """使用 HQChart 引擎对单股求值。"""
        script = self._require_hqchart(formula, args)

        df = await self._fetch_kline_df(symbol, period)
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
        """批量公式求值（带缓存+路由）。"""
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

        engine = self._resolve_engine_for(formula, period)
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
        self._require_data_query("Python")
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
        script = self._require_hqchart(formula, args)

        kline_data: Dict[str, List[Dict]] = {}
        for symbol in symbols:
            df = await self._fetch_kline_df(symbol, period)
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

    # ------------------------------------------------------------------
    # 事件驱动入口（同步便捷方法 + 金叉/死叉检测）
    # ------------------------------------------------------------------
    def evaluate(
        self,
        formula: str,
        symbol: str,
        period: str = "1d",
        args: Optional[dict] = None,
    ) -> Any:
        """同步公式求值（事件驱动场景的便捷入口）。"""
        coro = self.eval(formula, symbol, period, args)
        try:
            asyncio.get_running_loop()
            in_async = True
        except RuntimeError:
            in_async = False
        if not in_async:
            result = asyncio.run(coro)
        else:
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                result = pool.submit(asyncio.run, coro).result()
        # SubTask 22.2: 路由完成后发布 FormulaEvaluated 事件（路由内部逻辑），
        # 供 ScreeningModule / StatisticsModule 等下游模块订阅消费。
        # bus 未注入时跳过（向后兼容纯方法调用模式）。
        if self._bus is not None and FormulaEvaluated is not None:
            try:
                self._bus.publish(FormulaEvaluated(
                    formula_ref=formula,
                    result=result,
                    code=symbol,
                    bar_hash="",
                ))
            except Exception as ex:
                logger.warning("FormulaEvaluated 事件发布失败: %s", ex)
        return result

    def detect_crossover(
        self, formula_ref: str, code: str, result: Any
    ) -> Optional[str]:
        """检测金叉/死叉。"""
        if not isinstance(result, dict):
            return None

        main_key, trig_key = self._pick_crossover_pair(result)
        if main_key is None:
            return None

        cur_main = result.get(main_key)
        cur_trig = result.get(trig_key)
        if cur_main is None or cur_trig is None:
            return None
        try:
            cur_main_f = float(cur_main)
            cur_trig_f = float(cur_trig)
        except (TypeError, ValueError):
            return None

        key = (formula_ref, code)
        prev = self._prev_results.get(key)
        self._prev_results[key] = (cur_main_f, cur_trig_f)
        if prev is None:
            return None

        prev_main, prev_trig = prev
        # 使用 cross_op 统一穿越检测（标量对转为2元素Series，取第2个位置的检测结果）
        main_s = pd.Series([prev_main, cur_main_f], dtype=float)
        trig_s = pd.Series([prev_trig, cur_trig_f], dtype=float)
        golden_cross = cross_op(main_s, trig_s, direction="above")
        death_cross = cross_op(main_s, trig_s, direction="below")
        if bool(golden_cross.iloc[-1]):
            return "golden"
        if bool(death_cross.iloc[-1]):
            return "death"
        return None

    @staticmethod
    def _pick_crossover_pair(result: Dict[str, Any]) -> tuple:
        """从结果字典中识别主信号线与触发线。"""
        upper_keys = {k.upper(): k for k in result.keys()}
        if "DIF" in upper_keys and "DEA" in upper_keys:
            return upper_keys["DIF"], upper_keys["DEA"]
        if "K" in upper_keys and "D" in upper_keys:
            return upper_keys["K"], upper_keys["D"]
        if "MACD" in upper_keys and "SIGNAL" in upper_keys:
            return upper_keys["MACD"], upper_keys["SIGNAL"]
        return None, None


# ValueExtractor（从 core/value_extractor.py 迁移，SubTask 27.1）
class ValueExtractor:
    """表驱动值提取器：按 value_extractors 表分派提取原语。

    从 ``core/value_extractor.py`` 迁移至 ``formula_module.py``（SubTask 27.1）。

    依赖注入：
    - tables: PoolEngine.tables 配置表容器
    - engine: PoolEngine 引用（用于 self.x 路径导航和方法反射调用）
    """

    def __init__(self, tables: Dict[str, Any], engine: Any):
        self._tables = tables
        self._engine = engine

    def extract_value(self, spec: Dict[str, Any], ctx: Dict[str, Any], *,
                      edge: Optional[Dict] = None, args=None, kwargs=None, source=None) -> Any:
        """按 extractors[spec.type] 表行的 path/call/value/wrap 字段执行提取。"""
        extractor_type = spec.get('type') or spec.get('source') or 'literal_value'
        extractors = self._tables.get('value_extractors', {}).get('extractors', {})
        cfg = extractors.get(extractor_type)
        if not cfg:
            return None

        tmpl_vars = {k: v for k, v in spec.items() if isinstance(v, (str, int, float))}

        if 'eid_from' in cfg:
            eid = ''
            roots = {'ctx': ctx, 'edge': edge if isinstance(edge, dict) else {}, 'self': self._engine, 'source': source}
            for eid_path in str(cfg['eid_from']).split('|'):
                parts = eid_path.split('.')
                obj = roots.get(parts[0])
                for p in parts[1:]:
                    obj = obj.get(p, '') if isinstance(obj, dict) else getattr(obj, p, '')
                if obj:
                    eid = obj
                    break
            tmpl_vars['eid'] = eid

        if 'value' in cfg:
            return spec.get('value')

        result = None

        if 'path' in cfg:
            path_tmpl = cfg['path']
            try:
                path_str = path_tmpl.format(**tmpl_vars) if isinstance(path_tmpl, str) else path_tmpl
            except (KeyError, IndexError):
                return spec.get('fallback')
            result = self._navigate_path(path_str, spec, ctx, edge, args, kwargs, source)
            if result is None:
                default = cfg.get('default')
                if default is not None:
                    return default
                return spec.get('fallback')

        elif 'call' in cfg:
            call_tmpl = cfg['call']
            try:
                call_str = call_tmpl.format(**tmpl_vars) if isinstance(call_tmpl, str) and '{' in call_tmpl else call_tmpl
            except (KeyError, IndexError):
                call_str = call_tmpl
            # 内联 _exec_call：支持 self.method(args_from_ctx) / fn()
            if call_str.startswith('self.'):
                method_name = call_str[5:]
                handler = getattr(self._engine, method_name, None)
                if handler:
                    call_args = [ctx.get(k) for k in spec.get('args_from_ctx', [])]
                    result = handler(*call_args)
            elif call_str == '{fn}' or 'fn' in spec:
                fn = spec.get('fn') or source
                result = fn() if callable(fn) else None

        wrap_name = spec.get('wrap')
        if wrap_name and result is not None:
            wrap_fn = globals().get(wrap_name) if isinstance(wrap_name, str) else wrap_name
            if wrap_fn:
                result = wrap_fn(result)

        return result

    def resolve_chain(self, candidates: List[Dict[str, Any]], *, default=None) -> Any:
        """通用多源解析链：按优先级依次尝试候选者，返回第一个非 None 结果。"""
        for c in candidates:
            try:
                val = self.extract_value(c, {}, source=c.get("source"))
                if val is not None:
                    return val
            except (KeyError, AttributeError, TypeError):
                continue
        return default

    def resolve_field(self, spec: Dict[str, Any], ctx: Dict[str, Any]) -> Any:
        """深度表驱动字段解析：遍历 chain 返回首个非 None/非 0 值，否则 default。"""
        for path in spec.get('chain', []):
            # 1. 纯嵌套取值
            val = self._get_nested(ctx, path)
            # 2. 直接 key 取值（key 含点号）
            if val is None:
                val = ctx.get(path)
            # 3. 前缀 key + 子路径
            if val is None:
                keys = path.split('.')
                for i in range(len(keys) - 1, 0, -1):
                    prefix = '.'.join(keys[:i])
                    if prefix in ctx:
                        cur = ctx[prefix]
                        for k in keys[i:]:
                            if isinstance(cur, dict):
                                cur = cur.get(k)
                            else:
                                cur = None
                                break
                        if cur is not None:
                            val = cur
                            break
            if val is not None and val != 0:
                return val
        return spec.get('default', 0)

    def _navigate_path(self, path_str: str, spec, ctx, edge, args, kwargs, source) -> Any:
        """按 path 字符串导航取值。支持 ctx.x / self.x / edge.x / source.x / args[i]"""
        if not path_str:
            return None

        if path_str.startswith('args['):
            idx_str = path_str[5:path_str.index(']')]
            try:
                idx = int(idx_str)
                if args and len(args) > idx:
                    return args[idx]
                if kwargs:
                    key = spec.get('path', '')
                    return kwargs.get(key, spec.get('fallback', 0)) if key else spec.get('fallback', 0)
                return spec.get('fallback', 0)
            except (ValueError, IndexError):
                return None

        parts = path_str.split('.', 1)
        root_name = parts[0]
        rest = parts[1] if len(parts) > 1 else ''

        roots = {'ctx': ctx, 'self': self._engine, 'edge': edge if isinstance(edge, dict) else {}, 'source': source}
        if root_name in roots:
            val = roots[root_name]
        elif isinstance(ctx, dict) and root_name in ctx:
            val = ctx[root_name]
        else:
            return None

        if rest:
            if '[' in rest:
                attr_part, key_part = rest.split('[', 1)
                key = key_part.rstrip(']')
                if attr_part:
                    val = getattr(val, attr_part, None) if root_name == 'self' else (val.get(attr_part) if isinstance(val, dict) else None)
                if val is None:
                    return None
                if isinstance(val, dict):
                    return val.get(key)
                return None
            else:
                for p in rest.split('.'):
                    if val is None:
                        return None
                    if isinstance(val, dict):
                        val = val.get(p)
                    else:
                        val = getattr(val, p, None)
        return val

    @staticmethod
    def _get_nested(obj: Dict[str, Any], path: str) -> Any:
        """点号路径取值：'tracker.snapshot.exit_price' → obj['tracker']['snapshot']['exit_price']"""
        keys = path.split('.')
        cur = obj
        for k in keys:
            if isinstance(cur, dict):
                cur = cur.get(k)
            else:
                return None
            if cur is None:
                return None
        return cur


# FormulaModule — 对外统一入口
class FormulaModule(_BaseModule):
    """Formula 模块：公式计算 + 金叉检测。仅与 EventBus 交互。"""

    def __init__(
        self,
        bus: EventBus,
        config: Optional[Dict[str, Any]] = None,
        data_query: Optional[IDataQuery] = None,
        formula_cache: Optional[IFormulaCache] = None,
        hqchart_provider: Optional[IHQChartProvider] = None,
    ):
        """初始化 FormulaModule。"""
        self._bus = bus
        self._config: Dict[str, Any] = dict(config or {})

        # 依赖 tick / bar 的公式引用列表（由 config 驱动）
        self._tick_formulas: List[str] = list(self._config.get("tick_formulas", []))
        self._bar_formulas: Dict[str, List[str]] = {
            str(k): list(v)
            for k, v in self._config.get("bar_formulas", {}).items()
        }

        # 持有原组件实例（不暴露给外部）
        # 1) PythonFormulaEngine（core.formula_engine 组件，亦代表 core.formula 的求值能力）
        self._formula_engine = PythonFormulaEngine(data_query=data_query)
        # 2) FormulaCache（本模块内联，自 services/formula_cache.py 合并）
        self._formula_cache: IFormulaCache = (
            formula_cache if formula_cache is not None else FormulaCache()
        )
        # 3) FormulaRouter（core.formula_router 组件，组合 engine + cache + provider）
        self._formula_router = FormulaRouter(
            data_query=data_query,
            formula_cache=self._formula_cache,
            hqchart_provider=hqchart_provider,
            python_engine=self._formula_engine,
        )

        self.register_subscribers()

    # ------------------------------------------------------------------
    # 订阅注册
    # ------------------------------------------------------------------
    _SUBSCRIPTIONS: ClassVar[List[Tuple[type, str]]] = [
        (DataChanged, "_on_data_changed"),
        (BarComposed, "_on_bar_composed"),
        (PoolLoaded, "_on_pool_loaded"),
    ]

    def _on_pool_loaded(self, event: PoolLoaded) -> None:
        """收到 PoolLoaded 时，从 pool_config.edges 提取 formula_ref 并按周期归类。"""
        try:
            pool_config = event.pool_config or {}
            edges = pool_config.get("edges", []) if isinstance(pool_config, dict) else []
            formula_refs: List[str] = []
            for edge in edges:
                if not isinstance(edge, dict):
                    continue
                params = edge.get("params") or {}
                ref = params.get("formula_ref") if isinstance(params, dict) else None
                if isinstance(ref, str) and ref and ref not in formula_refs:
                    formula_refs.append(ref)
            # 按 builtin_formulas.json 的 period 字段归类
            period_map = {
                "1min": "1m", "5min": "5m", "15min": "15m",
                "30min": "30m", "60min": "60m", "1d": "1d",
                "1wk": "1wk", "1mon": "1mon",
                "1m": "1m", "5m": "5m", "15m": "15m",
            }
            for ref in formula_refs:
                info = _lookup_builtin_formula_info(ref) or {}
                period_str = info.get("period") or "1d"
                period_key = period_map.get(period_str, period_str)
                self._bar_formulas.setdefault(period_key, [])
                if ref not in self._bar_formulas[period_key]:
                    self._bar_formulas[period_key].append(ref)
            logger.info(
                "FormulaModule PoolLoaded 归类公式 formula_refs=%s bar_formulas=%s",
                formula_refs, self._bar_formulas,
            )
        except Exception as ex:
            logger.warning("FormulaModule._on_pool_loaded 异常: %s", ex, exc_info=True)

    def _get_dependent_formulas(self, period: str) -> List[str]:
        """获取依赖指定周期的公式引用列表。"""
        if period == "tick":
            return list(self._tick_formulas)
        return list(self._bar_formulas.get(period, []))

    # ------------------------------------------------------------------
    # 事件 handler（异常隔离：try/except + logger.warning）
    # ------------------------------------------------------------------
    def _on_data_changed(self, event: DataChanged) -> None:
        """tick 数据变更，触发依赖 tick 的公式重算。"""
        try:
            codes = list(getattr(event, "codes", []) or [])
            bar_hash = getattr(event, "bar_hash", "") or ""
            tick_formulas = self._get_dependent_formulas("tick")
            if not tick_formulas:
                return
            for code in codes:
                for formula_ref in tick_formulas:
                    result = self._formula_router.evaluate(formula_ref, code, "tick")
                    self._bus.publish(FormulaEvaluated(
                        formula_ref=formula_ref,
                        result=result,
                        code=code,
                        bar_hash=bar_hash,
                    ))
        except Exception as ex:
            logger.warning("FormulaModule._on_data_changed 异常: %s", ex)

    def _on_bar_composed(self, event: BarComposed) -> None:
        """bar 合成完成，触发依赖 bar 的公式重算 + 金叉/死叉检测。"""
        try:
            code = getattr(event, "code", "")
            period = getattr(event, "period", "")
            ts = float(getattr(event, "ts", 0.0) or 0.0)
            bar_formulas = self._get_dependent_formulas(period)
            if not bar_formulas:
                return
            for formula_ref in bar_formulas:
                result = self._formula_router.evaluate(formula_ref, code, period)
                self._bus.publish(FormulaEvaluated(
                    formula_ref=formula_ref,
                    result=result,
                    code=code,
                    bar_hash="",
                ))
                self._detect_crossover(formula_ref, code, result, ts)
        except Exception as ex:
            logger.warning("FormulaModule._on_bar_composed 异常: %s", ex)

    def _detect_crossover(
        self, formula_ref: str, code: str, result: Any, ts: float
    ) -> None:
        """金叉/死叉检测，命中时发布 ``CrossOverDetected`` 事件。"""
        try:
            cross = self._formula_router.detect_crossover(formula_ref, code, result)
            if cross in ("golden", "death"):
                self._bus.publish(CrossOverDetected(
                    code=code,
                    cross_type=cross,
                    formula_ref=formula_ref,
                    ts=ts,
                ))
        except Exception as ex:
            logger.warning("FormulaModule._detect_crossover 异常: %s", ex)

    async def eval_outvars(
        self,
        formula: str,
        symbol: str,
        period: str = "1d",
        args: Optional[dict] = None,
    ) -> Optional[Dict[str, Any]]:
        """对外暴露 FormulaRouter.eval_outvars，供 /api/indicator/values 调用。"""
        return await self._formula_router.eval_outvars(formula, symbol, period, args)

    async def eval_indicator_series(
        self,
        formula: str,
        bars: pd.DataFrame,
        args: Optional[dict] = None,
    ) -> Optional[Dict[str, List[Any]]]:
        """对提供的 bars 求值，返回全部输出变量的完整序列（指标面板折线图）。"""
        try:
            compiled = self._formula_router._python_engine._compile(formula)
            outputs = compiled._eval_core(bars, args)
            if outputs is None:
                return None
            result: Dict[str, List[Any]] = {}
            for name, val in outputs.items():
                key = "XG" if name is None else name
                if isinstance(val, pd.Series):
                    result[key] = [
                        float(x) if not (isinstance(x, float) and np.isnan(x)) else None
                        for x in val.values
                    ]
                elif isinstance(val, np.ndarray):
                    result[key] = [
                        float(x) if not (isinstance(x, float) and np.isnan(x)) else None
                        for x in val
                    ]
                elif isinstance(val, (list, tuple)):
                    result[key] = [
                        float(x) if x is not None and not (isinstance(x, float) and np.isnan(x)) else None
                        for x in val
                    ]
                else:
                    v = compiled._last_value(val)
                    result[key] = [v] * len(bars) if v is not None else [None] * len(bars)
            return result
        except Exception as e:
            logger.warning("指标序列求值失败 %s: %s", formula, e)
            return None


# === 公式缓存层（自 services/formula_cache.py 合并）===
_DEFAULT_TTL_MINUTE = 60
_DEFAULT_TTL_DAY = 86400

# 分钟级周期集合（用于 TTL 与分钟闭合失效判断）
_MINUTE_PERIODS = frozenset({'1m', '5m', '15m', '30m', '60m'})
_ALL_PERIODS = _MINUTE_PERIODS | {'tick', '1d', '1w', '1mon'}


def _load_ttl_config() -> Dict[str, int]:
    """从 config/data_pipeline.json 加载公式缓存 TTL 配置。"""
    try:
        if _CONFIG_PATH.exists():
            with open(_CONFIG_PATH, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
            formula_cfg = cfg.get('formula', {}) if isinstance(cfg, dict) else {}
            return {
                'cache_ttl_minute': int(formula_cfg.get('cache_ttl_minute', _DEFAULT_TTL_MINUTE)),
                'cache_ttl_day': int(formula_cfg.get('cache_ttl_day', _DEFAULT_TTL_DAY)),
            }
    except Exception as e:
        logger.debug("加载公式缓存 TTL 配置失败，使用默认值: %s", e)
    return {
        'cache_ttl_minute': _DEFAULT_TTL_MINUTE,
        'cache_ttl_day': _DEFAULT_TTL_DAY,
    }


_TTL_CFG = _load_ttl_config()
_TTL_MINUTE: int = _TTL_CFG['cache_ttl_minute']
_TTL_DAY: int = _TTL_CFG['cache_ttl_day']


class FormulaCache:
    """公式结果 L1 进程内缓存。"""

    def __init__(self):
        self._cache: Dict[Any, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _period_from_key(key: str) -> Optional[str]:
        """从字符串缓存键中解析 period 字段。"""
        if not isinstance(key, str):
            return None
        parts = key.split(':')
        if len(parts) < 3:
            return None
        # 第二段为数字说明存在 minute 标签，period 位于第三段
        if parts[1].isdigit():
            return parts[2] if parts[2] in _ALL_PERIODS else None
        return parts[1] if parts[1] in _ALL_PERIODS else None

    def make_key(
        self,
        symbol: str,
        period: str,
        formula_hash: str,
        args_hash: Optional[str] = None,
        minute: Optional[int] = None,
    ) -> str:
        """生成确定性缓存键。"""
        fh = formula_hash if formula_hash is not None else ''
        ah = args_hash if args_hash is not None else ''
        if minute is not None:
            return f"{symbol}:{minute}:{period}:{fh}:{ah}"
        return f"{symbol}:{period}:{fh}:{ah}"

    def get(self, key: Any) -> Any:
        """读取缓存值；不存在或已过期返回 ``None``，并自动驱逐过期条目。"""
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            if time.time() > entry['ts'] + entry['ttl']:
                del self._cache[key]
                return None
            return entry['value']

    def set(self, key: Any, value: Any, ttl: Optional[int] = None) -> None:
        """写入缓存。"""
        if ttl is None:
            period = self._period_from_key(key)
            if period == 'tick':
                return
            ttl = _TTL_MINUTE if period in _MINUTE_PERIODS else _TTL_DAY
        if ttl <= 0:
            return
        with self._lock:
            self._cache[key] = {'value': value, 'ts': time.time(), 'ttl': ttl}

    def clear_all(self) -> None:
        """清空全部缓存。"""
        with self._lock:
            self._cache.clear()

    def invalidate_daily(self) -> None:
        """日终失效：清空全部缓存，禁止跨交易日复用。"""
        self.clear_all()

    def invalidate_on_minute_close(self, symbol: str, minute: int) -> int:
        """分钟闭合时，失效指定标的在该分钟下的分钟级缓存。"""
        prefix = f"{symbol}:{minute}:"
        tag = f":{minute}:"
        removed = 0
        with self._lock:
            to_remove = []
            for key in list(self._cache.keys()):
                if isinstance(key, str):
                    if key.startswith(prefix) or tag in key:
                        to_remove.append(key)
                        continue
                    parts = key.split(':')
                    if parts and parts[0] == symbol:
                        period = parts[1] if len(parts) > 1 else None
                        if period in _MINUTE_PERIODS:
                            to_remove.append(key)
                elif isinstance(key, tuple) and len(key) >= 3:
                    if key[1] == symbol and key[2] in _MINUTE_PERIODS:
                        to_remove.append(key)
            for key in to_remove:
                if self._cache.pop(key, None) is not None:
                    removed += 1
        return removed

    def __len__(self) -> int:
        with self._lock:
            return len(self._cache)

    def __contains__(self, key: Any) -> bool:
        return self.get(key) is not None


__all__ = [
    # 入口
    "FormulaModule",
    # 公式引擎
    "PythonFormulaEngine",
    "CompiledFormula",
    "FormulaEngine",
    "EvalContext",
    "live_context",
    "replay_context",
    "simulation_context",
    # 算子
    "window_op",
    "shift_op",
    "cross_op",
    "ema_op",
    "sma_op",
    "abs_op",
    "max_op",
    "min_op",
    "if_op",
    "sar_op",
    # 路由
    "FormulaRouter",
    "IDataQuery",
    "IFormulaCache",
    "IHQChartProvider",
    # 公式缓存（自 services/formula_cache.py 合并）
    "FormulaCache",
    # 值提取器
    "ValueExtractor",
]
