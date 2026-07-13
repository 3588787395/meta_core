# -*- coding: utf-8 -*-
"""TDX 条件评估器模块：通用 nset 评估器 + 统一入口 eval_tdx_condition。

表驱动重构说明（Task 1+2+3+4）：
    - noperate 比较逻辑由 tdx_noperate_rules.json 的 expr/prev_expr/curr_expr/combine
      字段（表达式字符串）驱动，通用比较器 _eval_op 调 _eval_derived_expr 求值，无 if/elif 分支
    - nset 0/1/2 合并为 eval_formula_nset，nset 3/4 合并为 eval_scalar_nset，
      差异由 dispatch.json:nset_dispatch 的 nset_cfg 字段驱动
    - 排名逻辑由 tdx_noperate_rules.json:rank_modes 表驱动，_resolve_rank 替代 if/elif
    - 派生字段公式由 data_source_mappings.json:derived_fields 表驱动，
      _eval_derived_expr 用 ast 受控求值替代 lambda

解耦说明（Task 7）：
    - nset0/1/2（公式评估）：通过 FormulaRouter.eval_batch() 路由，禁止直接调用 tq_adapter
    - nset3/4（标量评估）：通过 MarketDataPort 接口获取标量数据，禁止直接调用 tq_adapter
    - nset3/4 禁止走 _resolve_fallback / pass_through 降级
    - nset5（集合运算）：纯集合运算，不依赖数据源，保持不变
"""
import ast
import asyncio
import concurrent.futures
import json
import logging
import operator
from pathlib import Path
from typing import Callable

try:
    from .schemas import ConfigLoadError
except ImportError:
    from schemas import ConfigLoadError


def _run_async(coro_factory):
    """同步运行异步协程，兼容已有事件循环的场景。

    FormulaRouter.eval_batch / MarketDataPort.* 均为 async，而评估器为同步入口，
    通过此桥接调用。当主线程已有运行中的事件循环时，在新线程中创建独立 loop 执行。

    Args:
        coro_factory: 返回协程的可调用对象（避免协程在错误线程中创建）。
    """
    try:
        asyncio.get_running_loop()
        # 已有运行中的事件循环，在新线程中运行以避免嵌套
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(lambda: asyncio.run(coro_factory())).result()
    except RuntimeError:
        # 没有运行中的事件循环
        return asyncio.run(coro_factory())


logger = logging.getLogger(__name__)
_lu = json.loads((Path(__file__).parent.parent/"config"/"tdx_ntjindexno_lookup.json").read_text("utf-8"))
_financial_fields = {f["value"]: f for f in _lu["nset_3_financial"]["fields"]}
_market_fields = {f["value"]: f for f in _lu["nset_4_market"]["fields"]}
_indicators_data = json.loads((Path(__file__).parent.parent/"config"/"tdx_indicators.json").read_text("utf-8"))
_SHARE_UNIT_FIELDS = {"zgb", "ltg", "bg", "hg"}

# 表驱动：noperate 操作符规则配置（config/tdx_noperate_rules.json）
# records 的 expr/prev_expr/curr_expr/combine 字段（表达式字符串）驱动通用比较器 _eval_op
# rank_modes 驱动排名处理器 _resolve_rank
_noperate_data = json.loads(
    (Path(__file__).parent.parent/"config"/"tdx_noperate_rules.json").read_text("utf-8")
)
_NOPERATE_RULES = {r["id"]: r for r in _noperate_data.get("records", [])}
_RANK_MODES = _noperate_data.get("rank_modes", {})

# combine 字段消费表：将表中的 "and"/"or" 字符串映射为逻辑运算，消除 if/elif 分支
_COMBINE_OPS = {"and": lambda a, b: a and b, "or": lambda a, b: a or b}

# 表驱动：nset5 集合运算分派表，消除 eval_nset5_set_operation 内联 ops 表
_NSET5_OPS = {
    0: lambda a, b: a | b,   # 并集
    1: lambda a, b: a - b,   # 差集
    2: lambda a, b: a & b,   # 交集
}

# 基本 bar 字段集合，消除硬编码字段元组判断
_BASE_BAR_FIELDS = frozenset({"close", "open", "high", "low", "volume", "amount"})


def _build_op_ctx(line1: list, line2: list, params: dict | None = None) -> dict:
    """构建 _eval_op 的 ctx 字典。

    ctx 字段约定（由 tdx_noperate_rules.json 的 expr/prev_expr/curr_expr 消费）：
        a / b       : 当前值（line1[-1] / line2[-1]）
        line1/line2 : 向量序列（供索引访问 line1[-2]/line1[-3] 等）
        tol_abs/tol_rel : 容差参数
        abs_diff    : abs(a - b)（预计算，便于 expr 直接引用，避免重复求值）
        tol         : max(tol_abs, abs(b) * tol_rel)（预计算，同上）
    """
    params = params or {}
    tol_abs = params.get("tolerance_abs", 1e-8)
    tol_rel = params.get("tolerance_rel", 1e-4)
    a = line1[-1] if line1 else 0.0
    b = line2[-1] if line2 else 0.0
    return {
        "a": a, "b": b, "line1": line1, "line2": line2,
        "tol_abs": tol_abs, "tol_rel": tol_rel,
        "abs_diff": abs(a - b), "tol": max(tol_abs, abs(b) * tol_rel),
    }


def _eval_op(rule: dict, ctx: dict) -> bool | list:
    """通用比较器，按 rule 的 expr/prev_expr/curr_expr/combine 字段执行。

    计算逻辑由表字段（表达式字符串）承载，由 _eval_derived_expr 统一求值，
    无 if/elif 比较分支。rank 类型由 _resolve_rank 统一处理，此处返回占位 []。

    分派依据（表内容驱动，非代码分支）：
        - rule["expr"] 存在 → 单表达式求值（abs_lt/gt/lt）
        - rule["prev_expr"]+["curr_expr"] 存在 → 双表达式按 combine 组合（cross/inflection）
        - rule["compare"] == "rank" → 占位 []（排名由 _resolve_rank 处理）
    """
    if rule.get("compare") == "rank":
        return []
    expr = rule.get("expr")
    if expr is not None:
        return _eval_derived_expr(expr, ctx)
    prev = _eval_derived_expr(rule["prev_expr"], ctx)
    curr = _eval_derived_expr(rule["curr_expr"], ctx)
    return _COMBINE_OPS[rule.get("combine", "and")](prev, curr)


def _apply_noperate(line1: list, line2: list, fsecond: float,
                   noperate: int, nperiodnum: int = 0) -> bool | list[int]:
    rule = _NOPERATE_RULES.get(str(noperate))
    if rule is None: return False
    ctx = _build_op_ctx(line1, line2, rule.get("params", {}))
    try:
        result = _eval_op(rule, ctx)
        return False if result is None else result
    except (IndexError, TypeError): return False


def _build_formula_arg(func: dict) -> str:
    p = _indicators_data.get("formula_arg_priority",["nfirst","nsecond","cfirst","csecond"])
    return ",".join(str(func[k]) for k in p if func.get(k) is not None and str(func[k]).strip())


def _scalar_compare(value: float, fsecond: float, noperate: int, prev_value: float = None) -> bool:
    rule = _NOPERATE_RULES.get(str(noperate))
    if rule is None: return False
    # 标量模式：用 [prev_value, value] 模拟 line1[-2]/line1[-1]，line2 恒为 [fsecond, fsecond]
    line1 = [prev_value, value] if prev_value is not None else [value]
    line2 = [fsecond, fsecond]
    ctx = _build_op_ctx(line1, line2, rule.get("params", {}))
    try:
        result = _eval_op(rule, ctx)
        return False if result is None else result
    except (IndexError, TypeError): return False


def _tie_exact_rank(ranked: list, n: int) -> list[str]:
    """精确排名第N名（处理并列）：同值并列占用相同名次。"""
    result, current_rank, prev_val = [], 0, None
    for idx, (code, val) in enumerate(ranked):
        if val != prev_val:
            current_rank = idx + 1
            prev_val = val
        if current_rank == n:
            result.append(code)
        elif current_rank > n:
            break
    return result


def _tie_slice(ranked: list, n: int) -> list[str]:
    """直接切片取前 N 名（不处理并列）。"""
    return [code for code, _ in ranked[:max(n, 1)]]


# 表驱动：tie_handling 处理器分派表，消除 _resolve_rank 中的 if/elif
_TIE_HANDLERS = {"exact_rank": _tie_exact_rank, "none": _tie_slice}


def _resolve_rank(ranked: list, fsecond: float, rank_rule: dict) -> list[str]:
    """根据 rank_modes 表的 rank_rule 处理排名结果。

    rank_rule 字段驱动差异：
        - order: 排序方向（desc 降序 / asc 升序）
        - tie_handling: 并列处理（由 _TIE_HANDLERS 表分派，无 if/elif）
        - params.default_n: fsecond<=0 时的默认 N
    """
    if not ranked: return []
    n = int(fsecond) if fsecond > 0 else rank_rule.get("params", {}).get("default_n", 10)
    order = rank_rule.get("order", "desc")
    tie = rank_rule.get("tie_handling", "none")
    ranked.sort(key=lambda x: x[1], reverse=(order == "desc"))
    handler = _TIE_HANDLERS.get(tie, _TIE_HANDLERS["none"])
    return handler(ranked, n)


# 表驱动：派生字段公式配置（config/data_source_mappings.json:derived_fields）
# I96：统一 fail-fast 策略，消除 except Exception: return {hardcoded} silent fallback
_data_source_mappings_cache = None

def _load_data_source_mappings():
    global _data_source_mappings_cache
    if _data_source_mappings_cache is not None:
        return _data_source_mappings_cache
    path = Path(__file__).parent.parent / "config" / "data_source_mappings.json"
    try:
        _data_source_mappings_cache = json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError) as ex:
        raise ConfigLoadError(
            f"无法加载配置表 {path}: {ex}（fail-fast：禁止静默回退硬编码值）"
        ) from ex
    return _data_source_mappings_cache

_dsm = _load_data_source_mappings()
_STOCK_INFO_FIELD_MAP = _dsm.get("stock_info_field_map", {})
_DERIVED_FIELDS_CONFIG = _dsm.get("derived_fields", {})
# 派生字段所需的组件字段，从表的 inputs 字段动态构建
_DERIVED_COMPONENT_FIELDS = {name: cfg["inputs"] for name, cfg in _DERIVED_FIELDS_CONFIG.items()}

# ast 受控求值器支持的二元运算符
_DERIVED_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
}
# ast 受控求值器支持的比较运算符
_DERIVED_CMP_OPS = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
}
# ast 受控求值器支持的逻辑运算符
_DERIVED_BOOL_OPS = {
    ast.And: lambda a, b: a and b,
    ast.Or: lambda a, b: a or b,
}
# ast 受控求值器支持的安全函数表（表驱动：函数名 → 实现）
# 覆盖 tracker formulas 的 max/min 与 _build_op_ctx 历史注释提到的 abs/round
_DERIVED_FUNCS = {
    "max": max,
    "min": min,
    "abs": abs,
    "round": round,
}


def _eval_derived_ast(tree, ctx: dict):
    """对已解析的 ast.Expression 受控求值（无 eval）。

    支持 +,-,*,/ 四则运算、比较运算、逻辑运算（and/or/not）、
    索引访问（line1[-1]）、数字字面量、字段名变量、_DERIVED_FUNCS 表内函数调用。
    变量从 ctx 字典查找（数值字段转 float，布尔/列表等非数值类型原样返回
    以支持逻辑运算和索引访问）。None 值通过运算传播（类似 SQL NULL 语义）：
    任意 None 操作数 → None（逻辑运算中 None 视为 False）。
    """
    def _eval(node):
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            if node.id in ctx:
                v = ctx[node.id]
                if v is None:
                    return None
                if isinstance(v, bool):
                    return v
                try:
                    return float(v)
                except (TypeError, ValueError):
                    return v
            return None
        if isinstance(node, ast.BinOp) and type(node.op) in _DERIVED_BIN_OPS:
            left = _eval(node.left)
            right = _eval(node.right)
            if left is None or right is None:
                return None
            try:
                return _DERIVED_BIN_OPS[type(node.op)](left, right)
            except (TypeError, ZeroDivisionError):
                return None
        if isinstance(node, ast.Compare):
            left = _eval(node.left)
            for op, comp in zip(node.ops, node.comparators):
                right = _eval(comp)
                if left is None or right is None:
                    return None
                try:
                    if not _DERIVED_CMP_OPS[type(op)](left, right):
                        return False
                except TypeError:
                    return None
                left = right
            return True
        if isinstance(node, ast.BoolOp) and type(node.op) in _DERIVED_BOOL_OPS:
            is_and = isinstance(node.op, ast.And)
            result = _eval(node.values[0])
            for val_node in node.values[1:]:
                if is_and and not result:
                    return result
                if not is_and and result:
                    return result
                result = _eval(val_node)
            return result
        if isinstance(node, ast.UnaryOp):
            if isinstance(node.op, ast.USub):
                operand = _eval(node.operand)
                return None if operand is None else -operand
            if isinstance(node.op, ast.Not):
                operand = _eval(node.operand)
                return None if operand is None else not operand
        if isinstance(node, ast.Subscript):
            if isinstance(node.value, ast.Name):
                seq = ctx.get(node.value.id)
            else:
                seq = _eval(node.value)
            if seq is None:
                return None
            # Python 3.8: slice 包在 ast.Index 中；3.9+: 直接含 value
            sl = node.slice
            if isinstance(sl, ast.Index):  # Python 3.8 compat
                sl = sl.value
            idx = _eval(sl)
            if idx is None:
                return None
            try:
                return seq[idx]
            except (IndexError, TypeError, KeyError):
                return None
        if isinstance(node, ast.Call):
            # 表驱动函数调用：仅允许 _DERIVED_FUNCS 表中的函数，禁止任意调用
            if not isinstance(node.func, ast.Name) or node.func.id not in _DERIVED_FUNCS:
                raise ValueError(f"不支持的函数调用: {ast.dump(node.func)}")
            if node.keywords or any(isinstance(a, ast.Starred) for a in node.args):
                raise ValueError("不支持关键字参数或星号参数")
            args = [_eval(a) for a in node.args]
            if any(a is None for a in args):
                return None
            try:
                return _DERIVED_FUNCS[node.func.id](*args)
            except (TypeError, ValueError, ZeroDivisionError):
                return None
        raise ValueError(f"不支持的表达式节点: {ast.dump(node)}")
    return _eval(tree)


def _eval_derived_expr(expr: str, ctx: dict, guard: str | None = None) -> float | None:
    """受控表达式求值器，用 ast 模块解析，禁止 eval()。

    支持 +,-,*,/ 四则运算、比较运算、逻辑运算（and/or/not）、
    索引访问（line1[-1]）、数字字面量、字段名变量、_DERIVED_FUNCS 表内函数调用
    （max/min/abs/round）。None 值通过运算传播（类似 SQL NULL 语义）：
    任意 None 操作数 → None（逻辑运算中 None 视为 False）。
    guard 为条件表达式，先求值 guard，False 或 None 则返回 None。
    """
    # 先求值 guard，False 或 None 则返回 None
    if guard:
        try:
            guard_tree = ast.parse(guard, mode="eval")
        except SyntaxError:
            return None
        if not _eval_derived_ast(guard_tree, ctx):
            return None
    # 求值 expr
    try:
        expr_tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        return None
    return _eval_derived_ast(expr_tree, ctx)


def _compute_derived_field(tq_field: str, snap: dict) -> float | None:
    """根据 data_source_mappings.json:derived_fields 配置计算派生字段值。"""
    cfg = _DERIVED_FIELDS_CONFIG.get(tq_field)
    if not cfg:
        return None
    return _eval_derived_expr(cfg["expr"], snap, cfg.get("guard"))


def _extract_code(stock) -> str:
    if isinstance(stock, str): return stock
    if isinstance(stock, dict): return stock.get("code", stock.get("Code", ""))
    return str(stock)
def _get_float(data, *fields) -> float | None:
    if data is None: return None
    for f in fields:
        try: v = data.get(f) if isinstance(data,dict) or hasattr(data,"get") else data[f]
        except (KeyError,IndexError,TypeError): continue
        if v is None: continue
        try: return float(v)
        except (TypeError,ValueError): continue
    return None


# I96：builtin_formulas.json 模块级缓存 + fail-fast，消除 _lookup_builtin_script 每次调用的重复 I/O
_builtin_formulas_cache = None

def _load_builtin_formulas():
    global _builtin_formulas_cache
    if _builtin_formulas_cache is not None:
        return _builtin_formulas_cache
    path = Path(__file__).parent.parent / "config" / "builtin_formulas.json"
    try:
        _builtin_formulas_cache = json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError) as ex:
        raise ConfigLoadError(
            f"无法加载配置表 {path}: {ex}（fail-fast：禁止静默回退空字符串）"
        ) from ex
    return _builtin_formulas_cache

_BUILTIN_FORMULAS = _load_builtin_formulas()
_BUILTIN_FORMULA_INDEX = {f.get("name"): f.get("script", "") for f in _BUILTIN_FORMULAS.get("formulas", [])}


def _lookup_builtin_script(name: str) -> str:
    """从 config/builtin_formulas.json 按名称查找公式脚本。

    Args:
        name: 公式名称（如 "MA"、"MACD"）。

    Returns:
        公式脚本文本；未找到时返回空字符串。
    """
    if not name:
        return ""
    return _BUILTIN_FORMULA_INDEX.get(name, "")


# TDX nperiod 整数码 → 标准周期字符串映射（参考 docs/公式引擎使用指南.md 5.7 节）
_TDX_NPERIOD_TO_PERIOD = {
    0: '1d', 1: '1m', 2: '5m', 3: '15m', 4: '30m', 5: '60m',
    6: '1d', 7: '1wk', 8: '1mon',
}


def _nperiod_to_period(nperiod) -> str:
    """TDX nperiod 整数码映射为标准周期字符串。

    0/6 → '1d'（日线），1 → '1m'，2 → '5m'，3 → '15m'，4 → '30m'，
    5 → '60m'，7 → '1wk'，8 → '1mon'。缺失或无效返回 '1d'。
    """
    try:
        return _TDX_NPERIOD_TO_PERIOD.get(int(nperiod), '1d')
    except (TypeError, ValueError):
        return '1d'


def _extract_indicator_scalar(value) -> float | None:
    """从公式求值结果中提取标量值。

    nset=0 技术指标求值可能返回：
        - 标量值（单输出，如 15.5）
        - dict（多输出变量，如 {"DIF": 1.2, "DEA": 0.8}，取第一个变量的值）
        - list/tuple（时间序列，取末值）
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    if isinstance(value, dict):
        for v in value.values():
            return _extract_indicator_scalar(v)
        return None
    if isinstance(value, (list, tuple)):
        if not value:
            return None
        return _extract_indicator_scalar(value[-1])
    return None


def eval_formula_nset(action_inputs: dict, nset_cfg: dict) -> list[str]:
    """nset=0/1/2 公式评估通用入口：通过 FormulaRouter.eval_batch() 路由。

    差异由 nset_cfg 字段驱动：
        - nset_cfg.rank_mode_unsupported：nset=1/2 排名模式是否返回空
        - nset_cfg.nset：日志前缀，同时决定结果处理分支

    nset 区分处理（SubTask 2.3）：
        - nset=0（技术指标）：公式求值返回标量值，用 noperate 操作符与 fsecond 比较
        - nset=1/2（条件选股/专家系统）：公式求值返回 0/1 或 True/False，检查真值
    """
    nset = nset_cfg.get("nset", 0)
    func = action_inputs.get("src_params", {}).get("tdx_func", {})
    accode = str(func.get("accode", "")).strip()
    noperate = int(func.get("noperate", 0))
    try:
        fsecond = float(func.get("fsecond", 0))
    except (TypeError, ValueError):
        fsecond = 0.0
    formula_router = action_inputs.get("formula_router")
    stock_list = action_inputs.get("stock_list", [])

    # SubTask 2.4: 优先使用 formula_script（完整脚本），回退到 accode 查 builtin_formulas.json
    formula_text = func.get("formula_script")
    if not formula_text:
        formula_text = _lookup_builtin_script(accode)
    if not formula_text:
        logger.error("nset=%d 未找到公式脚本 (accode=%s)", nset, accode)
        return []

    if not stock_list:
        return []
    if not formula_router:
        logger.error("nset=%d formula_router 不可用，无法评估公式（不降级）", nset)
        return []
    if not hasattr(formula_router, 'eval_batch'):
        logger.error("nset=%d formula_router.eval_batch 不可用，无法评估公式（不降级）", nset)
        return []
    # nset=1/2 排名模式需要线数据，eval_batch 仅返回 bool/标量，无法支持排名
    # nset=0 基于标量值排名，可支持
    # 表驱动：通过 _NOPERATE_RULES 的 mode 字段判定排名模式，消除内联元组 (5,6,7)
    _nset12_rule = _NOPERATE_RULES.get(str(noperate), {})
    if nset != 0 and nset_cfg.get("rank_mode_unsupported") and _nset12_rule.get("mode", "compare") == "rank":
        logger.warning(
            "nset=%d 排名模式(noperate=%d)需要线数据，"
            "FormulaRouter.eval_batch 无法支持", nset, noperate)
        return []
    symbols = [_extract_code(s) for s in stock_list if _extract_code(s)]
    if not symbols:
        return []
    # 透传前端配置的公式参数（如 MACD 的 SHORT/LONG/MID）与动态分析周期
    formula_args = func.get("formula_args")
    period = _nperiod_to_period(func.get("nperiod"))
    try:
        result = _run_async(
            lambda: formula_router.eval_batch(
                formula_text, symbols, period=period, args=formula_args))
    except Exception as e:
        logger.error("nset=%d FormulaRouter.eval_batch(%s) 失败: %s", nset, accode, e)
        return []
    if not result or not isinstance(result, dict):
        return []
    # SubTask 2.3: 根据 nset 区分处理结果
    if nset == 0:
        # 技术指标：公式求值返回标量值，应用 noperate 操作符与 fsecond 比较
        return _eval_nset0_result(result, noperate, fsecond)
    # 条件选股/专家系统：公式求值返回 0/1 或 True/False
    # I87：eval_batch 契约收敛——真实 FormulaRouter.eval_batch 返回 {symbol: value}，
    # 不含 success 元数据键（I86 统一失败路径到 raise RuntimeError）。测试 mock 同步收敛。
    # bool 是 int 子类，v>0 自然区分 True/False。
    return [s for s, v in result.items()
            if isinstance(v, (int, float)) and v > 0]


def _apply_noperate_mode(scalars: dict, noperate: int, fsecond: float,
                         prev_lookup: Callable[[str], float | None], nset_label: str) -> list[str]:
    """nset 标量评估的 mode 分派内核：rank/inflection/compare 三模式统一处理。

    被 _eval_nset0_result（nset=0，从公式结果提取标量）与 eval_scalar_nset（nset=3/4，
    从 MarketDataPort 获取标量）共用。I47：消除两处重复的 mode 分派（提取优先于表驱动——
    分支非虚假特化但跨函数重复，提取共用内核而非各自表驱动）。
        - rank（5-7 排名为/排名前N/排名后N）：收集 (symbol, value) 对用 _resolve_rank 处理
        - inflection（8-9 上拐/下拐）：需要向量数据，标量模式无法支持
        - compare（0-4 等于/大于/小于/上穿/下破）：逐只用 _scalar_compare 比较
    """
    rule = _NOPERATE_RULES.get(str(noperate), {})
    mode = rule.get("mode", "compare")
    if mode == "inflection":
        logger.warning("nset=%s noperate=%d（拐点）需要向量数据，标量模式无法支持", nset_label, noperate)
        return []
    if mode == "rank":
        ranked = [(s, v) for s, v in scalars.items() if v is not None]
        rank_rule = _RANK_MODES.get(str(noperate), {})
        return _resolve_rank(ranked, fsecond, rank_rule)
    # 比较模式：用 _scalar_compare 逐只比较
    passed = []
    for symbol, value in scalars.items():
        if value is None:
            continue
        prev_value = prev_lookup(symbol) if prev_lookup and rule.get("compare") == "cross" else None
        if rule.get("compare") == "cross" and prev_value is None:
            continue
        if _scalar_compare(value, fsecond, noperate, prev_value):
            passed.append(symbol)
    return passed


def _eval_nset0_result(result: dict, noperate: int, fsecond: float, prev_lookup: Callable[[str], float | None] = None) -> list[str]:
    """nset=0 技术指标结果处理：提取标量值并委托 _apply_noperate_mode 分派。

    I47：mode 分派（rank/inflection/compare）上提至 _apply_noperate_mode，与 eval_scalar_nset
    共用内核；本函数仅负责从公式求值结果提取 {symbol: scalar} 字典。
    """
    scalars = {}
    for symbol, value in result.items():
        scalar = _extract_indicator_scalar(value)
        if scalar is not None:
            scalars[symbol] = scalar
    return _apply_noperate_mode(scalars, noperate, fsecond, prev_lookup, "0")


def eval_scalar_nset(action_inputs: dict, nset_cfg: dict, prev_lookup: Callable[[str], float | None] = None) -> list[str]:
    """nset=3/4 标量评估通用入口：通过 MarketDataPort 接口获取标量。

    差异由 nset_cfg 字段驱动：
        - nset_cfg.field_table：选择字段表（nset_3_financial / nset_4_market）
        - nset_cfg.data_method：选择数据获取方法
        - nset_cfg.supports_derived：是否处理派生字段
        - nset_cfg.supports_bar_fallback：是否回退 current_bar_data
        - nset_cfg.apply_field_map：是否应用 stock_info_field_map 映射
        - nset_cfg.nset：日志前缀
    """
    nset = nset_cfg.get("nset", 0)
    func = action_inputs.get("src_params", {}).get("tdx_func", {})
    ntjindexno = int(func.get("ntjindexno", 0))
    noperate = int(func.get("noperate", 0))
    fsecond = float(func.get("fsecond", 0))
    # 选择字段表
    field_table_name = nset_cfg.get("field_table")
    field_table = _financial_fields if field_table_name == "nset_3_financial" else _market_fields
    field_def = field_table.get(ntjindexno)
    if not field_def:
        logger.warning("nset=%d 未知 ntjindexno=%d", nset, ntjindexno)
        return []
    tq_field = field_def["tq_field"]
    # 应用字段映射（nset3 财务字段需要映射到 tq_adapter 实际字段名）
    if nset_cfg.get("apply_field_map"):
        actual_tq_field = _STOCK_INFO_FIELD_MAP.get(tq_field, tq_field)
    else:
        actual_tq_field = tq_field
    market_data_port = action_inputs.get("market_data_port")
    stock_list = action_inputs.get("stock_list", [])
    symbols = [_extract_code(s) for s in stock_list if _extract_code(s)]
    if not symbols:
        return []
    data_method_name = nset_cfg.get("data_method")
    supports_derived = nset_cfg.get("supports_derived", False)
    supports_bar_fallback = nset_cfg.get("supports_bar_fallback", False)
    # 获取标量值
    values: dict = {}
    if market_data_port and hasattr(market_data_port, data_method_name):
        data_method = getattr(market_data_port, data_method_name)
        # 派生字段需要多个组件字段，批量获取后组装计算
        if supports_derived and tq_field in _DERIVED_COMPONENT_FIELDS:
            component_fields = _DERIVED_COMPONENT_FIELDS[tq_field]
            # 批量获取所有组件字段
            component_values: dict = {}
            for cf in component_fields:
                try:
                    cf_values = _run_async(
                        lambda cf=cf: data_method(symbols, cf))
                except Exception as e:
                    logger.error("nset=%d %s(%s) 失败: %s", nset, data_method_name, cf, e)
                    return []
                if not cf_values or not isinstance(cf_values, dict):
                    return []
                component_values[cf] = cf_values
            # 组装 snap dict 并计算派生字段值
            for symbol in symbols:
                snap = {cf: component_values[cf].get(symbol) for cf in component_fields}
                val = _compute_derived_field(tq_field, snap)
                if val is not None:
                    values[symbol] = val
        else:
            try:
                values = _run_async(
                    lambda: data_method(symbols, actual_tq_field))
            except Exception as e:
                logger.error("nset=%d %s(%s) 失败: %s", nset, data_method_name, actual_tq_field, e)
                return []
    elif supports_bar_fallback:
        # 仿真模式回退：从 current_bar_data 读取行情数据
        current_bar_data = action_inputs.get("current_bar_data", {})
        if not current_bar_data:
            logger.warning("nset=%d market_data_port 不可用且无 current_bar_data 可回退", nset)
            return []
        # 基本字段直接从 bar_data 读取
        if tq_field in _BASE_BAR_FIELDS:
            for symbol in symbols:
                bar = current_bar_data.get(symbol)
                if isinstance(bar, dict) and tq_field in bar:
                    try:
                        values[symbol] = float(bar[tq_field])
                    except (ValueError, TypeError):
                        pass
        # 派生字段从 bar_data 组装后计算
        elif supports_derived and tq_field in _DERIVED_COMPONENT_FIELDS:
            component_fields = _DERIVED_COMPONENT_FIELDS[tq_field]
            for symbol in symbols:
                bar = current_bar_data.get(symbol)
                if isinstance(bar, dict):
                    snap = {cf: bar.get(cf) for cf in component_fields}
                    val = _compute_derived_field(tq_field, snap)
                    if val is not None:
                        values[symbol] = val
        if not values:
            return []
    else:
        if not market_data_port:
            logger.error("nset=%d market_data_port 不可用，无法获取数据（不降级）", nset)
        else:
            logger.error("nset=%d market_data_port.%s 不可用（不降级）", nset, data_method_name)
        return []
    # I47：mode 分派委托 _apply_noperate_mode（与 _eval_nset0_result 共用内核）
    return _apply_noperate_mode(values, noperate, fsecond, prev_lookup, str(nset))


def eval_nset5_set_operation(action_inputs: dict) -> list[str]:
    func = action_inputs.get("src_params", {}).get("tdx_func", {})
    ntjindexno = int(func.get("ntjindexno", 0))
    node_stocks = action_inputs.get("node_stocks", {})
    sid, tid = action_inputs.get("sid", ""), action_inputs.get("tid", "")
    edges = action_inputs.get("edges", [])
    source_stocks = {_extract_code(s) for s in action_inputs.get("stock_list", []) if _extract_code(s)}
    target_in_edges = [e for e in edges if (e.get("to", "") or e.get("target", {}).get("node_id", "")) == tid]
    if len(target_in_edges) <= 1:
        if ntjindexno == 2:  # intersection with nothing = empty
            return []
        return list(source_stocks)  # union/difference with nothing = source
    other_stocks = set()
    for edge in target_in_edges:
        edge_from = edge.get("from", "") or edge.get("source", {}).get("node_id", "")
        if edge_from and edge_from != sid:
            other_raw = node_stocks.get(edge_from, [])
            other_stocks |= {_extract_code(s) for s in other_raw if _extract_code(s)}
    op = _NSET5_OPS.get(ntjindexno)
    return list(op(source_stocks, other_stocks) if op else source_stocks)


# 表驱动：nset 分发配置（config/dispatch.json:nset_dispatch）
_NSET_DISPATCH = json.loads(
    (Path(__file__).parent.parent/"config"/"dispatch.json").read_text("utf-8")
).get("nset_dispatch", {})
# dispatch_key → nset_cfg 查找表
_DISPATCH_TO_NSET_CFG = {
    cfg.get("dispatch_key"): cfg for cfg in _NSET_DISPATCH.values()
    if cfg.get("dispatch_key")
}


def eval_tdx_condition(dispatch_key: str, action_inputs: dict) -> list[str]:
    nset_cfg = _DISPATCH_TO_NSET_CFG.get(dispatch_key)
    if not nset_cfg:
        logger.warning("eval_tdx_condition 未知 dispatch_key='%s'", dispatch_key)
        return []
    evaluator_name = nset_cfg.get("evaluator")
    if not evaluator_name:
        logger.warning("eval_tdx_condition dispatch_key='%s' 缺少 evaluator", dispatch_key)
        return []
    fn = globals().get(evaluator_name)
    if not fn or not callable(fn):
        logger.error("eval_tdx_condition evaluator '%s' 未找到", evaluator_name)
        return []
    try:
        # nset5 保持原签名 (action_inputs)，其它通用评估器接收 (action_inputs, nset_cfg)
        if "direct_handler" in nset_cfg:
            result = fn(action_inputs)
        else:
            result = fn(action_inputs, nset_cfg)
        return result if isinstance(result, list) else []
    except Exception as e:
        logger.error("eval_tdx_condition(%s) 异常: %s", dispatch_key, e)
        return []
