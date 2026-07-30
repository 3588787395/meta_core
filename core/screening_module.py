# -*- coding: utf-8 -*-
"""Screening 模块：股票筛选（强弱对比）+ TDX 条件评估器（单一入口）。"""
from __future__ import annotations

import ast
import asyncio
import concurrent.futures
import json
import logging
import operator
import time
from pathlib import Path
from typing import Any, Callable, ClassVar, Dict, List, Optional, Set, Tuple, Type

try:
    from .schemas import ConfigLoadError
except ImportError:
    from schemas import ConfigLoadError

from .domain import (
    ConditionFormulaEvaluator,
    Evaluator,
    ExpertSystemEvaluator,
    FinancialScalarEvaluator,
    FilterSpec,
    IndicatorEvaluator,
    MarketScalarEvaluator,
    SetOperationEvaluator,
)
from .event_bus import _BaseModule, _event_handler, EventBus, FormulaEvaluated, PoolLoaded, StockFiltered
from .domain import load_config_table

# 表驱动：noperate 操作符规则配置（config/tdx_noperate_rules.json）
# Task 23.3: _noperate_data / _NOPERATE_RULES / _RANK_MODES / _COMBINE_OPS 已迁移至
# core/domain（白名单），此处通过 re-export 保持向后兼容
from .domain import (
    _noperate_data, _NOPERATE_RULES, _RANK_MODES, _COMBINE_OPS,
    _build_op_ctx, _eval_op,
    _tie_exact_rank, _tie_slice, _TIE_HANDLERS, _resolve_rank,
    _DERIVED_BIN_OPS, _DERIVED_CMP_OPS, _DERIVED_BOOL_OPS, _DERIVED_FUNCS,
    _eval_derived_ast, _eval_derived_expr,
    # builtin formulas lookup（从 domain re-export，消除 formula_module → screening_module 耦合）
    _BUILTIN_FORMULAS, _BUILTIN_FORMULA_INDEX, _BUILTIN_FORMULA_INFO,
    _lookup_builtin_script, _lookup_builtin_formula_info,
    # TDX nperiod → period mapping（已迁移至 domain，re-export 保持向后兼容）
    _TDX_NPERIOD_TO_PERIOD, _nperiod_to_period,
    # intersection evaluator（已迁移至 domain，re-export 保持向后兼容）
    evaluate_intersection,
)


logger = logging.getLogger(__name__)


# === 评估器层（自 core/evaluators.py 合并）开始 ===

# 模块级常量（原 evaluators.py 顶层）
_lu = load_config_table("tdx_ntjindexno_lookup")
_financial_fields = {f["value"]: f for f in _lu["nset_3_financial"]["fields"]}
_market_fields = {f["value"]: f for f in _lu["nset_4_market"]["fields"]}
_indicators_data = load_config_table("tdx_indicators")
_SHARE_UNIT_FIELDS = {"zgb", "ltg", "bg", "hg"}

# 表驱动：nset5 集合运算分派表，消除 eval_nset5_set_operation 内联 ops 表
_NSET5_OPS = {
    0: lambda a, b: a | b,   # 并集
    1: lambda a, b: a - b,   # 差集
    2: lambda a, b: a & b,   # 交集
}

# 基本 bar 字段集合，消除硬编码字段元组判断
_BASE_BAR_FIELDS = frozenset({"close", "open", "high", "low", "volume", "amount"})


def _run_async(coro_factory):
    """同步运行异步协程，兼容已有事件循环的场景。"""
    try:
        asyncio.get_running_loop()
        # 已有运行中的事件循环，在新线程中运行以避免嵌套
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(lambda: asyncio.run(coro_factory())).result()
    except RuntimeError:
        # 没有运行中的事件循环
        return asyncio.run(coro_factory())


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


# 表驱动：派生字段公式配置（config/data_source_mappings.json:derived_fields）
# I96：统一 fail-fast 策略，消除 except Exception: return {hardcoded} silent fallback
_data_source_mappings_cache = None

def _load_data_source_mappings():
    global _data_source_mappings_cache
    if _data_source_mappings_cache is not None:
        return _data_source_mappings_cache
    try:
        _data_source_mappings_cache = load_config_table("data_source_mappings")
        if not _data_source_mappings_cache:
            raise ConfigLoadError(
                "无法加载配置表 data_source_mappings（fail-fast：禁止静默回退硬编码值）"
            )
    except ConfigLoadError:
        raise
    except Exception as ex:
        raise ConfigLoadError(
            f"无法加载配置表 data_source_mappings: {ex}（fail-fast：禁止静默回退硬编码值）"
        ) from ex
    return _data_source_mappings_cache

_dsm = _load_data_source_mappings()
_STOCK_INFO_FIELD_MAP = _dsm.get("stock_info_field_map", {})
_DERIVED_FIELDS_CONFIG = _dsm.get("derived_fields", {})
# 派生字段所需的组件字段，从表的 inputs 字段动态构建
_DERIVED_COMPONENT_FIELDS = {name: cfg["inputs"] for name, cfg in _DERIVED_FIELDS_CONFIG.items()}


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


# I96：builtin_formulas.json 模块级缓存 + fail-fast
# 已迁移至 core/domain.py，通过 re-export 提供（_BUILTIN_FORMULAS / _lookup_builtin_script 等）


# _TDX_NPERIOD_TO_PERIOD / _nperiod_to_period 已迁移至 core/domain（白名单），
# 此处通过顶部 from .domain import re-export 保持向后兼容。


def _extract_indicator_scalar(value) -> float | None:
    """从公式求值结果中提取标量值。"""
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

    # 表驱动：若 accode 对应内置信号公式（含 eval_field），提取指定输出变量
    # 用于 KDJ/MACD 金叉等返回多输出变量但仅关注 CROSS_* 信号的场景
    builtin_info = _lookup_builtin_formula_info(accode)
    eval_field = builtin_info.get("eval_field") if builtin_info else None

    try:
        result = _run_async(
            lambda: formula_router.eval_batch(
                formula_text, symbols, period=period, args=formula_args))
    except Exception as e:
        logger.error("nset=%d FormulaRouter.eval_batch(%s) 失败: %s", nset, accode, e)
        return []
    if not result or not isinstance(result, dict):
        return []

    # 按 eval_field 提取信号输出（如 CROSS_J_K / CROSS_DIF_DEA）
    if eval_field:
        extracted: dict = {}
        for symbol, value in result.items():
            if isinstance(value, dict):
                extracted[symbol] = value.get(eval_field)
            else:
                extracted[symbol] = value
        result = extracted

    # SubTask 2.3: 根据 nset 区分处理结果
    if nset == 0:
        # 技术指标：公式求值返回标量值，应用 noperate 操作符与 fsecond 比较
        return _eval_nset0_result(result, noperate, fsecond, eval_field=eval_field)
    # 条件选股/专家系统：公式求值返回 0/1 或 True/False
    return [s for s, v in result.items()
            if isinstance(v, (int, float)) and v > 0]


def _mode_inflection(scalars, noperate, fsecond, prev_lookup, nset_label, eval_field=None):
    """标量拐点：需向量数据，标量模式无法支持。"""
    logger.warning("nset=%s noperate=%d（拐点）需要向量数据，标量模式无法支持", nset_label, noperate)
    return []


def _mode_rank(scalars, noperate, fsecond, prev_lookup, nset_label, eval_field=None):
    """标量排名：收集 (symbol, value) 对用 _resolve_rank 处理。"""
    return _resolve_rank([(s, v) for s, v in scalars.items() if v is not None],
                         fsecond, _RANK_MODES.get(str(noperate), {}))


def _mode_compare(scalars, noperate, fsecond, prev_lookup, nset_label, eval_field=None):
    """标量比较：cross 信号公式（CROSS_*/XG）truthy 即通过，否则逐只 _scalar_compare。"""
    rule = _NOPERATE_RULES.get(str(noperate), {})
    if eval_field and (eval_field.upper().startswith("CROSS_") or eval_field.upper() == "XG"):
        return [s for s, v in scalars.items() if v is not None and v > 0]
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


# 表驱动：标量 mode 分派表，共用 _NOPERATE_RULES 真值源（向量变体已收敛至 execution_module._eval_formula_path）。
_MODE_HANDLERS = {"inflection": _mode_inflection, "rank": _mode_rank, "compare": _mode_compare}


def _apply_noperate_mode(scalars: dict, noperate: int, fsecond: float,
                         prev_lookup: Callable[[str], float | None], nset_label: str,
                         eval_field: str | None = None) -> list[str]:
    """nset 标量评估的 mode 分派内核：经 _MODE_HANDLERS 分派，被 _eval_nset0_result/eval_scalar_nset 共用。"""
    rule = _NOPERATE_RULES.get(str(noperate), {})
    handler = _MODE_HANDLERS.get(rule.get("mode", "compare"), _mode_compare)
    return handler(scalars, noperate, fsecond, prev_lookup, nset_label, eval_field)


def _eval_nset0_result(result: dict, noperate: int, fsecond: float,
                       prev_lookup: Callable[[str], float | None] = None,
                       eval_field: str | None = None) -> list[str]:
    """nset=0 技术指标结果处理：提取标量值并委托 _apply_noperate_mode 分派。

    I47：mode 分派（rank/inflection/compare）上提至 _apply_noperate_mode，与 eval_scalar_nset
    共用内核；本函数仅负责从公式求值结果提取 {symbol: scalar} 字典。
    """
    scalars = {}
    for symbol, value in result.items():
        scalar = _extract_indicator_scalar(value)
        if scalar is not None:
            scalars[symbol] = scalar
    return _apply_noperate_mode(scalars, noperate, fsecond, prev_lookup, "0", eval_field=eval_field)


def eval_scalar_nset(action_inputs: dict, nset_cfg: dict, prev_lookup: Callable[[str], float | None] = None) -> list[str]:
    """nset=3/4 标量评估通用入口：通过 MarketDataPort 接口获取标量。"""
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
_NSET_DISPATCH = load_config_table("dispatch").get("nset_dispatch", {})
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


# evaluate_intersection 已迁移至 core/domain（白名单），
# 此处通过顶部 from .domain import re-export 保持向后兼容。


# === 评估器层（自 core/evaluators.py 合并）结束 ===


# === Screening 模块（事件驱动筛选）开始 ===


# 表驱动：nset → 筛选策略函数（无 if/elif 链）


def _filter_indicator(
    filter_spec: FilterSpec,
    stock_results: Dict[str, Any],
    current_code: str,
    evaluator: Evaluator,
) -> Optional[List[str]]:
    """nset=0：技术指标评估，从公式结果提取标量并用 noperate 比较。"""
    if not stock_results:
        return None
    # 根据 formula_ref 查 builtin_formulas.json 获取 eval_field
    builtin_info = _lookup_builtin_formula_info(filter_spec.formula_ref) or {}
    eval_field = builtin_info.get("eval_field")
    scalars: Dict[str, float] = {}
    for code, result in stock_results.items():
        # 优先提取 eval_field 对应的值（如 CROSS_J_K / CROSS_DIF_DEA）
        if eval_field and isinstance(result, dict) and eval_field in result:
            scalar = _extract_indicator_scalar(result.get(eval_field))
        else:
            scalar = _extract_indicator_scalar(result)
        if scalar is not None:
            scalars[code] = scalar
    return _apply_noperate_mode(
        scalars, filter_spec.noperate, filter_spec.fsecond,
        prev_lookup=None, nset_label="0",
        eval_field=eval_field,
    )


def _filter_truthy(
    filter_spec: FilterSpec,
    stock_results: Dict[str, Any],
    current_code: str,
    evaluator: Evaluator,
) -> Optional[List[str]]:
    """nset=1（条件选股公式）/ nset=2（专家系统公式）：公式求值返回 0/1 或 True/False，检查真值。"""
    if not stock_results:
        return None
    return [c for c, v in stock_results.items()
            if isinstance(v, (int, float)) and v > 0]


def _filter_scalar(
    filter_spec: FilterSpec,
    stock_results: Dict[str, Any],
    current_code: str,
    evaluator: Evaluator,
) -> Optional[List[str]]:
    """nset=3（最新财务标量）/ nset=4（实时行情标量）：用 noperate 比较标量值。"""
    if not stock_results:
        return None
    scalars: Dict[str, float] = {}
    for code, result in stock_results.items():
        scalar = _extract_indicator_scalar(result)
        if scalar is not None:
            scalars[code] = scalar
    return _apply_noperate_mode(
        scalars, filter_spec.noperate, filter_spec.fsecond,
        prev_lookup=None, nset_label=str(filter_spec.nset),
    )


def _filter_set_operation(
    filter_spec: FilterSpec,
    stock_results: Dict[str, Any],
    current_code: str,
    evaluator: Evaluator,
) -> Optional[List[str]]:
    """nset=5：集合运算，纯集合运算（并/差/交），不依赖公式结果。"""
    return list(stock_results.keys()) if stock_results else (
        [current_code] if current_code else []
    )


# 表驱动：nset → 筛选策略函数（无 if/elif 分派）
_NSET_FILTER_HANDLERS: Dict[int, Callable[..., Optional[List[str]]]] = {
    0: _filter_indicator,
    1: _filter_truthy,
    2: _filter_truthy,
    3: _filter_scalar,
    4: _filter_scalar,
    5: _filter_set_operation,
}


class ScreeningModule(_BaseModule):
    """Screening 模块：股票筛选（强弱对比）。仅与 EventBus 交互。"""

    # 表驱动：nset → Evaluator 子类（无 if/elif 链）
    # 使用 core.domain.evaluators 的 6 种 Evaluator 子类作为评估器层次
    _NSET_TO_EVALUATOR: Dict[int, Type[Evaluator]] = {
        0: IndicatorEvaluator,           # nset=0, DZH 技术指标
        1: ConditionFormulaEvaluator,    # nset=1, DZH 条件选股
        2: ExpertSystemEvaluator,        # nset=2, DZH 交易系统
        3: FinancialScalarEvaluator,     # nset=3, DZH 基本面条件
        4: MarketScalarEvaluator,        # nset=4, DZH 动态行情
        5: SetOperationEvaluator,        # nset=5, DZH 板块成员
    }

    def __init__(self, bus: EventBus, config: Optional[Dict[str, Any]] = None) -> None:
        self._bus = bus
        self._config = config or {}
        # 加载 dispatch.json:nset_dispatch（nset×noperate 矩阵配置）
        self._dispatch_cfg = self._load_dispatch_config()
        # 公式结果缓存：(formula_ref, code) → result（per-code 粒度）
        self._formula_results: Dict[Tuple[str, str], Any] = {}
        # 边的 filter_spec 配置：eid → FilterSpec
        self._edge_filter_specs: Dict[str, FilterSpec] = {}
        # 边的 passed 缓存：eid → set(code)，用于增量筛选
        self._edge_passed_cache: Dict[str, Set[str]] = {}
        self.register_subscribers()

    _SUBSCRIPTIONS: ClassVar[List[Tuple[type, str]]] = [
        (FormulaEvaluated, "_on_formula_evaluated"),
        (PoolLoaded, "_on_pool_loaded"),
    ]

    @_event_handler("_on_pool_loaded")
    def _on_pool_loaded(self, event: PoolLoaded) -> None:
        """收到 PoolLoaded 时，从 pool_config.edges 提取 formula_ref 编译 filter_spec。"""
        pool_config = event.pool_config or {}
        edges = pool_config.get("edges", []) if isinstance(pool_config, dict) else []
        # 清空旧 spec，避免重复池配置残留
        self._edge_filter_specs.clear()
        for edge in edges:
            if not isinstance(edge, dict):
                continue
            eid = str(edge.get("id") or edge.get("flow_id") or "")
            if not eid:
                continue
            params = edge.get("params") or {}
            if not isinstance(params, dict):
                params = {}
            formula_ref = str(params.get("formula_ref") or "")
            condition_type = str(params.get("condition_type") or "")
            if formula_ref:
                spec = FilterSpec(
                    evaluator_type="formula",
                    nset=0,
                    noperate=int(params.get("noperate", 0) or 0),
                    formula_ref=formula_ref,
                    fsecond=params.get("fsecond", 0),
                )
            elif condition_type == "INTERSECTION":
                spec = FilterSpec(
                    evaluator_type="intersection",
                    nset=5,
                    noperate=2,  # 交集
                    formula_ref="",
                    fsecond=0,
                )
            else:
                spec = FilterSpec(
                    evaluator_type="pass_through",
                    nset=5,
                    noperate=0,
                    formula_ref="",
                    fsecond=0,
                )
            self._edge_filter_specs[eid] = spec
        logger.info(
            "ScreeningModule PoolLoaded 编译 filter_spec 边数=%d spec=%s",
            len(self._edge_filter_specs),
            {eid: s.formula_ref for eid, s in self._edge_filter_specs.items()},
        )

    def _load_dispatch_config(self) -> Dict[str, Any]:
        """加载 config/dispatch.json 的 nset_dispatch 配置。"""
        try:
            data = load_config_table("dispatch")
            return data.get("nset_dispatch", {})
        except Exception as ex:
            logger.warning("ScreeningModule 加载 dispatch.json 失败: %s", ex)
            return {}

    def register_edge_filter(self, eid: str, filter_spec: FilterSpec) -> None:
        """注册边的 filter_spec（供 Execution 模块调用）。"""
        self._edge_filter_specs[eid] = filter_spec

    def unregister_edge_filter(self, eid: str) -> None:
        """注销边的 filter_spec。"""
        self._edge_filter_specs.pop(eid, None)

    def _resolve_evaluator(self, nset: int) -> Evaluator:
        """根据 nset 返回对应 Evaluator 子类实例（表驱动，无 if/elif）。"""
        evaluator_cls = self._NSET_TO_EVALUATOR.get(nset, IndicatorEvaluator)
        return evaluator_cls()

    @_event_handler("_on_formula_evaluated")
    def _on_formula_evaluated(self, event: FormulaEvaluated) -> None:
        """处理 FormulaEvaluated 事件：per-code 缓存并触发依赖边的增量筛选。"""
        # 确定本次变化的 code 集合
        changed_codes: List[str] = []
        if event.code:
            changed_codes = [event.code]
            self._formula_results[(event.formula_ref, event.code)] = event.result
        elif isinstance(event.result, dict) and all(
            isinstance(k, str) for k in event.result.keys()
        ):
            changed_codes = list(event.result.keys())
            for code, value in event.result.items():
                self._formula_results[(event.formula_ref, code)] = value

        # 触发依赖此公式的边的增量筛选
        for eid, filter_spec in list(self._edge_filter_specs.items()):
            if filter_spec.formula_ref != event.formula_ref:
                continue
            passed, rejected = self._evaluate_filter(
                eid, filter_spec, changed_codes=changed_codes,
            )
            if passed is not None:
                self._bus.publish(StockFiltered(
                    eid=eid, passed=passed, rejected=rejected,
                    filter_ref=filter_spec.formula_ref,
                    ts=time.time(),
                ))

    def _evaluate_filter(
        self,
        eid: str,
        filter_spec: FilterSpec,
        changed_codes: Optional[List[str]] = None,
    ) -> Tuple[Optional[List[str]], List[str]]:
        """执行筛选：按 nset 表驱动分派，支持增量筛选。"""
        try:
            formula_ref = filter_spec.formula_ref
            # 收集当前公式已缓存的所有 code（作为候选全集）
            all_codes = [
                code for (fr, code) in self._formula_results.keys()
                if fr == formula_ref
            ]
            if not all_codes:
                return None, []
            codes_set = set(all_codes)
            cached_passed = self._edge_passed_cache.get(eid)

            if changed_codes is None:
                eval_codes = list(all_codes)
                prev_passed: Set[str] = set()
            elif not changed_codes:
                if cached_passed is not None:
                    passed_set = cached_passed & codes_set
                    return list(passed_set), [c for c in all_codes if c not in passed_set]
                eval_codes = list(all_codes)
                prev_passed = set()
            else:
                changed_set = set(changed_codes) & codes_set
                if cached_passed is not None:
                    prev_passed = cached_passed - changed_set
                    eval_codes = list(changed_set)
                else:
                    eval_codes = list(all_codes)
                    prev_passed = set()

            if not eval_codes:
                passed_set = prev_passed & codes_set
                self._edge_passed_cache[eid] = passed_set
                return list(passed_set), [c for c in all_codes if c not in passed_set]

            # 为待评估 code 构造 {code: result} 子集
            stock_results = {
                code: self._formula_results.get((formula_ref, code))
                for code in eval_codes
            }

            evaluator = self._resolve_evaluator(filter_spec.nset)
            handler = _NSET_FILTER_HANDLERS.get(filter_spec.nset, _filter_set_operation)
            newly_passed = handler(filter_spec, stock_results, "", evaluator)
            if newly_passed is None:
                newly_passed = []
            passed_set = (prev_passed | set(newly_passed)) & codes_set
            self._edge_passed_cache[eid] = passed_set

            rejected = [c for c in all_codes if c not in passed_set]
            return list(passed_set), rejected
        except Exception as ex:
            logger.warning(
                "ScreeningModule._evaluate_filter 异常 (eid=%s nset=%d): %s",
                eid, filter_spec.nset, ex,
            )
            return None, []


# === Screening 模块（事件驱动筛选）结束 ===


__all__ = [
    # Screening 模块
    "ScreeningModule",
    # 评估器层（原 core.evaluators 公开 API）
    "eval_formula_nset",
    "eval_scalar_nset",
    "eval_nset5_set_operation",
    "eval_tdx_condition",
    "evaluate_intersection",
    # 评估器层（向后兼容 re-export，原 core.evaluators 私有但被外部 import）
    "_apply_noperate_mode",
    # mode 分派真值源（标量），execution_module 经此共用 _NOPERATE_RULES
    "_MODE_HANDLERS",
    "_extract_indicator_scalar",
    "_lookup_builtin_script",
    "_lookup_builtin_formula_info",
    "_nperiod_to_period",
    "_run_async",
    "_scalar_compare",
    "_apply_noperate",
    "_build_formula_arg",
    "_compute_derived_field",
    "_extract_code",
    "_get_float",
    "_eval_derived_expr",
    "_NOPERATE_RULES",
    "_RANK_MODES",
    "_NSET_DISPATCH",
    "_DISPATCH_TO_NSET_CFG",
    "_NSET5_OPS",
    "_TDX_NPERIOD_TO_PERIOD",
    "_BASE_BAR_FIELDS",
    "_BUILTIN_FORMULAS",
    "_BUILTIN_FORMULA_INDEX",
    "_BUILTIN_FORMULA_INFO",
    "_financial_fields",
    "_market_fields",
    "_indicators_data",
    "_SHARE_UNIT_FIELDS",
    "_STOCK_INFO_FIELD_MAP",
    "_DERIVED_FIELDS_CONFIG",
    "_DERIVED_COMPONENT_FIELDS",
]
