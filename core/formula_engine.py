"""纯 Python 公式引擎（轻量级、numpy/pandas 向量化）。

作为复杂公式引擎（HQChart 等）不可用时的高性能兜底路径，
仅支持简单 TDX 风格公式：字段、函数、比较/逻辑/算术运算符、
变量赋值 `:=` 与输出 `:`。

所有序列函数（MA、REF、CROSS、HHV、LLV、SUM、COUNT、STD 等）
均使用 pandas 向量化实现，禁止逐根 K 线的 Python 循环。
公式编译结果缓存于 ``self._compiled_cache``（LRU，容量由 ``_CACHE_MAXSIZE`` 控制），
同一条公式二次求值时直接复用编译产物。

本引擎不持有数据源，K 线数据由调用方通过 ``bars`` 参数传入；
批量求值时由 ``data_fetcher`` 或 ``data_query`` 提供每只标的的
``pd.DataFrame``。
"""

from __future__ import annotations

import json
import logging
import re
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 字段名 / 函数名映射
# ---------------------------------------------------------------------------
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
_FUNCS_CFG = json.loads(
    (Path(__file__).parent.parent / "config" / "formula_funcs.json").read_text("utf-8")
)["funcs"]

_TOKEN_RE = re.compile(
    r"(?i)(:=|>=|<=|==|!=|[<>=:\-+*/(),;]|[A-Za-z_][A-Za-z0-9_]*|\d+\.?\d*)"
)


# ---------------------------------------------------------------------------
# 简易 LRU 缓存
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# 向量化 TDX 函数实现
# ---------------------------------------------------------------------------
def _to_series(x: Any) -> pd.Series:
    """将输入统一转为 pd.Series，便于使用 rolling/shift。"""
    if isinstance(x, pd.Series):
        return x
    return pd.Series(np.asarray(x, dtype=float))


# ---------------------------------------------------------------------------
# 通用算子（表驱动）：window_op / shift_op / cross_op
# ---------------------------------------------------------------------------
# 滚动窗口聚合方法名由表字段 agg_method 承载（深表驱动），
# window_op 通过 getattr 动态调用 pandas rolling 方法，无 lambda 分派表。
# COUNT 的 agg_override="bool_sum" 为语义标记：pandas rolling.sum() 已正确处理
# 布尔序列求和，与非布尔 sum 行为一致，无需额外代码分支。


def window_op(series: Any, n: int, agg_method: str,
              agg_override: Optional[str] = None,
              agg_kwargs: Optional[Dict[str, Any]] = None) -> pd.Series:
    """通用滚动窗口算子（深表驱动）。

    agg_method 为 pandas rolling 聚合方法名（mean/max/min/sum/std），
    通过 ``getattr`` 动态调用，避免 if/elif 分支与 lambda 分派表。
    agg_kwargs 透传给聚合方法（如 std 的 ddof）。
    agg_override 为语义标记（如 bool_sum），pandas rolling.sum() 已正确处理
    布尔序列求和，无需额外代码分支。
    """
    s = _to_series(series)
    n = int(n)
    if n <= 0:
        return s
    kwargs = agg_kwargs or {}
    roller = s.rolling(window=n, min_periods=1)
    method = getattr(roller, agg_method, None)
    if method is None:
        return s
    return method(**kwargs)


def shift_op(series: Any, n: int) -> pd.Series:
    """通用偏移算子，替代 REF。"""
    return _to_series(series).shift(int(n))


def cross_op(line1: Any, line2: Any, direction: str = "above") -> pd.Series:
    """通用穿越检测算子，替代 CROSS。

    direction='above'：line1 上穿 line2（前一根 line1<line2 且当前 line1>=line2）。
    direction='below'：line1 下穿 line2（前一根 line1>line2 且当前 line1<=line2）。
    """
    sa, sb = _to_series(line1), _to_series(line2)
    if direction == "above":
        return (sa.shift(1) < sb.shift(1)) & (sa >= sb)
    else:
        return (sa.shift(1) > sb.shift(1)) & (sa <= sb)


# ---------------------------------------------------------------------------
# 递推与逐元素算子：EMA / SMA / ABS / MAX / MIN / IF
# ---------------------------------------------------------------------------
def _ewm_core(series: Any, n: int, alpha_fn: Callable[[int], float]) -> pd.Series:
    """通用指数加权核心：按 ``alpha_fn(n)`` 计算 alpha，调用 pandas ``ewm``。

    EMA/SMA 共享同一递推骨架（首值即 X[0]，adjust=False），
    仅 alpha 计算方式不同，差异通过 alpha_fn 闭包注入，消除重复分支。
    """
    s = _to_series(series)
    n = int(n)
    if n <= 0 or len(s) == 0:
        return s
    alpha = alpha_fn(n)
    return s.ewm(alpha=alpha, adjust=False).mean()


def ema_op(series: Any, n: int) -> pd.Series:
    """指数移动平均 EMA(X, N)。

    递推：ema[i] = X[i]*2/(N+1) + ema[i-1]*(1-2/(N+1))，首值 ema[0] = X[0]。
    等价于 pandas ``ewm(alpha=2/(N+1), adjust=False).mean()``。
    """
    return _ewm_core(series, n, lambda n: 2.0 / (n + 1))


def sma_op(series: Any, n: int, m: int) -> pd.Series:
    """加权移动平均 SMA(X, N, M)。

    递推：sma[i] = (X[i]*M + sma[i-1]*(N-M)) / N，首值 sma[0] = X[0]。
    等价于 pandas ``ewm(alpha=M/N, adjust=False).mean()``。
    """
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
    """抛物线转向 SAR(N, STEP, MAXP)。

    TDX 参数以百分比×100 传入（STEP=2 表示 0.02，MAXP=20 表示 0.2）。
    递推规则：
      - 首根 K 线假定上升趋势，SAR[0]=low[0]，EP=high[0]，AF=step/100。
      - 每根更新 SAR = SAR_prev + AF*(EP - SAR_prev)。
      - 上升趋势限制 SAR <= min(low[i-1], low[i])；跌破 SAR 则反转。
      - 下降趋势限制 SAR >= max(high[i-1], high[i])；升破 SAR 则反转。
    """
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
    """按表配置分派到通用算子（深表驱动：handler 反射调用 + arg_spec 参数提取入表）。

    参数提取规则由 cfg["arg_spec"] 声明（idx/cast/default），
    算子函数由 cfg["handler"] 声明并通过 globals() 反射获取，
    额外 kwargs 由 cfg["cfg_kwargs"] 列表声明（从 cfg 提取指定字段透传），
    cross 方向由 cfg["direction_field"] 声明（指向承载 direction 值的字段名）。
    若 cfg 声明 ``context_fields``，则从求值命名空间自动注入对应字段（如 high/low），
    支持 SAR 等需要多字段的系统函数。
    无 if/elif op 分支，差异完全入表。
    """
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


# ---------------------------------------------------------------------------
# 公式分词与表达式解析
# ---------------------------------------------------------------------------
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
        while True:
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
        while True:
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
    """解析单条语句，返回 (kind, name, compiled_code)。

    kind 为 'assign'（:= 中间变量）或 'output'（: 输出）。
    无赋值符时作为匿名 output，name 为 None。
    """
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
        kind = "assign" if assign_tok == ":=" else "output"
        expr = _ExprParser(rhs_tokens).parse()
        return kind, name, compile(expr, "<formula>", "eval")

    # 无赋值符，整体作为表达式
    expr = _ExprParser(stmt).parse()
    return "output", None, compile(expr, "<formula>", "eval")


# ---------------------------------------------------------------------------
# 编译产物
# ---------------------------------------------------------------------------
@dataclass
class CompiledFormula:
    """已编译的公式，保存按顺序执行的语句（赋值/输出）及其 code 对象。"""

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
        """核心求值：构建命名空间并执行全部语句，返回输出变量 OrderedDict。

        与 ``eval`` / ``eval_outvars`` 共享同一求值核心，避免形状分裂。

        Returns:
            ``{name: raw_value}`` 有序字典；命名空间构建失败、求值异常或无输出时返回 None。
        """
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
        """对单只股票的 K 线数据进行求值（异构返回）。

        - 匿名表达式或 XG 输出返回最后一根 K 线的 bool。
        - 单输出指标返回最后一根 K 线的标量值。
        - 多输出指标返回 ``{output_name: last_value}``。

        适用于条件筛选（``eval``）；需要统一字典契约时使用 ``eval_outvars``。
        """
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
        """对单只股票的 K 线数据进行求值，返回全部输出变量末值字典。

        与 ``eval`` 共享 ``_eval_core`` 求值核心，但始终返回 ``{outvar_name: last_value}``：
        匿名/XG 输出归一为键 ``"XG"``，单输出指标以输出名作键，多输出原样返回。

        Returns:
            ``{outvar_name: last_value}`` 字典；求值失败或无输出时返回 None。
        """
        outputs = self._eval_core(bars, args)
        if outputs is None:
            return None
        result: OrderedDict = OrderedDict()
        for name, val in outputs.items():
            key = "XG" if name is None else name
            result[key] = self._last_value(val)
        return result


# ---------------------------------------------------------------------------
# 数据归一化
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# 公式引擎主类
# ---------------------------------------------------------------------------
class PythonFormulaEngine:
    """纯 Python 公式引擎（轻量级、numpy/pandas 向量化）。"""

    def __init__(self, data_query: Any = None):
        """初始化引擎。

        Args:
            data_query: 可选的数据查询对象，当 ``eval_batch`` 未提供
                ``data_fetcher`` 时作为兜底数据源。
        """
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

    def eval(self, formula: str, bars: pd.DataFrame, args: Optional[dict] = None) -> Any:
        """对单只股票的 bars 求值。

        - 条件公式返回最后一根 K 线是否成立（bool）。
        - 单输出指标返回最后一根 K 线的标量值。
        - 多输出指标返回 ``{output_name: last_value}``。

        数据不足或编译/求值失败时保守返回 ``False`` / ``None``。
        """
        try:
            compiled = self._compile(formula)
            return compiled.eval(bars, args)
        except Exception as e:
            logger.warning("公式编译失败 %s: %s", formula, e)
            return None

    def eval_outvars(self, formula: str, bars: pd.DataFrame, args: Optional[dict] = None) -> Optional[Dict[str, Any]]:
        """对单只股票的 bars 求值，返回全部输出变量末值字典。

        始终返回 ``{outvar_name: last_value}`` 字典（匿名/XG 输出归一为 ``"XG"``），
        编译/求值失败或数据不足时返回 None。
        """
        try:
            compiled = self._compile(formula)
            return compiled.eval_outvars(bars, args)
        except Exception as e:
            logger.warning("公式编译失败 %s: %s", formula, e)
            return None

    def eval_batch(
        self,
        formula: str,
        symbols: List[str],
        period: str = "1d",
        data_fetcher: Optional[Callable[[str, str], pd.DataFrame]] = None,
        args: Optional[dict] = None,
    ) -> Dict[str, Any]:
        """批量求值：为每只标的取数据并分别求值。

        Args:
            formula: 公式字符串。
            symbols: 标的代码列表。
            period: 周期，默认 ``'1d'``。
            data_fetcher: 可选数据获取函数 ``(symbol, period) -> pd.DataFrame``。
            args: 公式参数（如 SHORT/LONG/MID），注入到求值命名空间。

        Returns:
            ``{symbol: eval_result}``，任一标的数据不足或求值失败时该标的结果为 ``False``。
        """
        results: Dict[str, Any] = {}
        try:
            compiled = self._compile(formula)
        except Exception as e:
            logger.warning("批量公式编译失败 %s: %s", formula, e)
            return {symbol: False for symbol in symbols}

        for symbol in symbols:
            df: Optional[pd.DataFrame] = None
            if data_fetcher is not None:
                try:
                    df = data_fetcher(symbol, period)
                except Exception as e:
                    logger.debug("data_fetcher 异常 %s: %s", symbol, e)
            elif self.data_query is not None:
                try:
                    # 优先尝试 (symbol, period) 签名
                    df = self.data_query.fetch(symbol, period)
                except Exception:
                    try:
                        df = self.data_query.get_bars(symbol, period)
                    except Exception as e2:
                        logger.debug("data_query 取数异常 %s: %s", symbol, e2)

            if df is None or not isinstance(df, pd.DataFrame) or len(df) == 0:
                results[symbol] = False
                continue

            try:
                results[symbol] = compiled.eval(df, args)
            except Exception as e:
                logger.debug("批量求值异常 %s: %s", symbol, e)
                results[symbol] = False

        return results


# ---------------------------------------------------------------------------
# 便捷别名，保持与旧代码的兼容
# ---------------------------------------------------------------------------
FormulaEngine = PythonFormulaEngine
